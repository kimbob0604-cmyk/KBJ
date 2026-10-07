"""텔레그램 웹훅 수신 — 순수 처리 함수(요청 dict → 응답 dict)와 받은 업데이트의 갈래(설계 §5.6).

FastAPI 는 P3 로 미룬다(메인 결정 D4, R8). 여기에는 HTTP 서버가 없다 — P3 의 API 가
`POST /telegram/webhook` 요청을 `WebhookHandler.handle({"method","path","headers","body"})` 에
넘기고 돌려받은 결과의 `as_response()`(`{"status","headers","body"}`)를 그대로 응답한다.

- 인증: 헤더 `X-Telegram-Bot-Api-Secret-Token` 을 `KBJ_TELEGRAM_WEBHOOK_SECRET` 과
  `hmac.compare_digest` 로(SD `_telegram_secret` 의 봇 토큰 파생·`!=` 비교 대체). 다르면 403,
  비밀이 설정되지 않았으면 모두 503(인증 없는 수신은 없다).
- 중복: `update_id` 를 `SET NX tg:update:<id>`(48시간) — 이미 있으면 200(다시 처리하지 않는다).
- 응답: 검증 뒤 **바로 200** — 처리는 스트림 `notify:inbound` 로 넘긴다(중복 키와 Lua 한 번에).
- Redis 장애: 503 — 텔레그램이 다시 보낸다(받았다고 하고 잃지 않는다).

갈래(`InboundRouter.route` — notifier 작업 스레드가 스트림을 읽어 부른다):
① 명령 — 텍스트가 `/` 로 시작 + 명령 허용 대화(`KBJ_TELEGRAM_CHAT_ID` + 인박스 대화) → 명령 분배 →
  `notify(kind="cmd.reply")`(받은 대화·스레드로, subject=`u<update_id>`).
  `message`·`edited_message`(SD 와 같다).
② 인박스 — 명령이 아니고 인박스 허용 대화(숫자 id·`@username`) → `InboxProcessor`.
그 밖 — 버리고 건수만 센다(chat id 는 남기지 않는다).
"""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Literal

from pydantic import SecretStr
from redis import Redis
from redis.exceptions import RedisError, ResponseError

from kbj.services.notifier.client import NotifyClient
from kbj.services.notifier.commands import CommandRegistry
from kbj.services.notifier.inbox import AllowList, InboxProcessor, InboxReport
from kbj.services.notifier.outbox import NotifyTicket
from kbj.store.redis_keys import NOTIFY_INBOUND, TG_UPDATE_PREFIX, tg_update_key

log = logging.getLogger(__name__)

WEBHOOK_PATH: Final = "/telegram/webhook"
SECRET_HEADER: Final = "X-Telegram-Bot-Api-Secret-Token"  # noqa: S105 — 헤더 이름
ALLOWED_UPDATES: Final = ("message", "edited_message", "channel_post")  # setWebhook 본문
TG_UPDATE_TTL_S: Final = 48 * 3600
GROUP: Final = "notifier"
COMMAND_SOURCE: Final = "notifier.commands"

Outcome = Literal[
    "queued", "duplicate", "forbidden", "bad_request", "not_found", "method", "unavailable"
]

# KEYS[1]=tg:update:<id> KEYS[2]=notify:inbound
# ARGV[1]=수명(초) ARGV[2]=업데이트 JSON ARGV[3]=받은 시각
_ACCEPT_LUA: Final = """
if redis.call('SET', KEYS[1], '1', 'NX', 'EX', ARGV[1]) then
  return redis.call('XADD', KEYS[2], '*', 'v', '1', 'update', ARGV[2], 'received_at', ARGV[3])
end
return false
"""


@dataclass(frozen=True)
class WebhookResult:
    status: int
    outcome: Outcome
    update_id: int | None = None
    detail: str = ""

    def as_response(self) -> dict[str, Any]:
        """P3 API 가 그대로 돌려줄 응답 dict. 본문에는 사유를 싣지 않는다(밖에서 읽힌다)."""
        body = {"ok": self.status == 200}
        return {
            "status": self.status,
            "headers": {"content-type": "application/json"},
            "body": json.dumps(body),
        }


def _header(headers: Mapping[str, Any] | None, name: str) -> str | None:
    if not headers:
        return None
    want = name.lower()
    for k, v in headers.items():
        if str(k).lower() == want:
            return v.decode("latin-1") if isinstance(v, bytes) else str(v)
    return None


def secret_ok(secret: SecretStr | None, header: str | None) -> bool:
    """상수 시간 비교. 비밀이 없으면(설정 전) 늘 거짓 — 인증 없는 수신은 없다."""
    if secret is None or not secret.get_secret_value() or header is None:
        return False
    return hmac.compare_digest(header.encode("utf-8"), secret.get_secret_value().encode("utf-8"))


class WebhookHandler:
    """`POST /telegram/webhook` 의 순수 처리기(서버 없이 시험한다)."""

    def __init__(
        self,
        secret: SecretStr | None,
        redis: Redis,
        *,
        now: Callable[[], datetime],
        path: str = WEBHOOK_PATH,
        stream: str = NOTIFY_INBOUND,
    ) -> None:
        self._secret = secret
        self._r = redis
        self._now = now
        self._path = path
        self._stream = stream
        self._accept = redis.register_script(_ACCEPT_LUA)

    def handle(self, request: Mapping[str, Any]) -> WebhookResult:
        """요청 dict(`method`·`path`·`headers`·`body` — bytes·str·dict) → 결과."""
        if str(request.get("path") or "") != self._path:
            return WebhookResult(404, "not_found")
        if str(request.get("method") or "").upper() != "POST":
            return WebhookResult(405, "method")
        raw = request.get("body")
        update: Any
        if isinstance(raw, Mapping):
            update = raw
        else:
            try:
                update = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else str(raw))
            except (ValueError, UnicodeDecodeError):
                update = None
        return self.handle_update(update, _header(request.get("headers"), SECRET_HEADER))

    def handle_update(self, update: Any, secret_header: str | None) -> WebhookResult:
        """업데이트 dict + 시크릿 헤더 → 결과(설계 §1.6 의 이름)."""
        if self._secret is None or not self._secret.get_secret_value():
            return WebhookResult(503, "unavailable", detail="KBJ_TELEGRAM_WEBHOOK_SECRET 이 없다")
        if not secret_ok(self._secret, secret_header):
            return WebhookResult(403, "forbidden")
        if not isinstance(update, Mapping):
            return WebhookResult(400, "bad_request", detail="본문이 JSON 객체가 아니다")
        uid: Any = update.get("update_id")  # pyright: ignore[reportUnknownMemberType]
        if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
            return WebhookResult(400, "bad_request", detail="update_id 가 없다")
        payload = json.dumps(update, ensure_ascii=False)
        try:
            res: Any = self._accept(
                keys=[tg_update_key(uid), self._stream],
                args=[TG_UPDATE_TTL_S, payload, self._now().astimezone(UTC).isoformat()],
            )
        except RedisError as e:
            log.warning("웹훅 수신 Redis 실패: %s", type(e).__name__)
            return WebhookResult(503, "unavailable", uid, detail=type(e).__name__)
        if not res:
            return WebhookResult(200, "duplicate", uid)
        return WebhookResult(200, "queued", uid)


def handle_update(handler: WebhookHandler, update: Any, secret_header: str | None) -> WebhookResult:
    """모듈 수준 편의 — `handler.handle_update`."""
    return handler.handle_update(update, secret_header)


# ── 받은 업데이트 스트림 ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InboundEntry:
    id: str
    update: dict[str, Any] | None
    error: str | None = None


@dataclass
class InboundQueue:
    redis: Redis
    stream: str = NOTIFY_INBOUND
    group: str = GROUP
    _ready: bool = field(default=False, init=False)

    def ensure_group(self) -> None:
        if self._ready:
            return
        try:
            self.redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
        self._ready = True

    def read(self, consumer: str, n: int = 20) -> list[InboundEntry]:
        self.ensure_group()
        out: list[InboundEntry] = []
        for start in ("0", ">"):
            res: Any = self.redis.xreadgroup(self.group, consumer, {self.stream: start}, count=n)
            for _s, entries in res or []:
                for eid, raw in entries:
                    if not raw:
                        continue
                    sid = eid.decode() if isinstance(eid, bytes) else str(eid)
                    fields = {
                        (k.decode() if isinstance(k, bytes) else str(k)): v for k, v in raw.items()
                    }
                    try:
                        body = fields["update"]
                        up = json.loads(body.decode("utf-8") if isinstance(body, bytes) else body)
                        if not isinstance(up, dict):
                            raise ValueError("객체가 아니다")
                        out.append(InboundEntry(sid, up))  # pyright: ignore[reportUnknownArgumentType]
                    except (KeyError, ValueError, UnicodeDecodeError) as e:
                        out.append(InboundEntry(sid, None, f"형식 오류: {type(e).__name__}"))
        return out

    def ack(self, entry_id: str) -> None:
        self.redis.xack(self.stream, self.group, entry_id)
        self.redis.xdel(self.stream, entry_id)

    def backlog(self) -> int:
        n: Any = self.redis.xlen(self.stream)
        return int(n)


# ── 갈래 ─────────────────────────────────────────────────────────────────────────────────────

Route = Literal["command", "inbox", "ignored", "not_message"]


@dataclass(frozen=True)
class RouteResult:
    route: Route
    ticket: NotifyTicket | None = None  # 명령 응답을 넣은 결과
    inbox: InboxReport | None = None


@dataclass
class RouteCounts:
    command: int = 0
    inbox: int = 0
    ignored: int = 0
    not_message: int = 0


class InboundRouter:
    """업데이트 하나 → 명령 / 인박스 / 버림."""

    def __init__(
        self,
        *,
        commands: CommandRegistry,
        client: NotifyClient,
        command_chats: AllowList,
        inbox: InboxProcessor | None,
    ) -> None:
        self.commands = commands
        self.client = client
        self.command_chats = command_chats
        self.inbox = inbox
        self.counts = RouteCounts()

    def route(self, update: Mapping[str, Any]) -> RouteResult:
        uid = update.get("update_id")
        cmd_msg = update.get("message") or update.get("edited_message")
        if isinstance(cmd_msg, Mapping):
            msg: Mapping[str, Any] = cmd_msg  # pyright: ignore[reportUnknownVariableType]
            text = str(msg.get("text") or "")
            chat: Mapping[str, Any] = msg.get("chat") or {}
            if text.startswith("/") and self.command_chats.allows(chat):
                thread = msg.get("message_thread_id")
                thread_id = thread if isinstance(thread, int) else None
                reply = self.commands.dispatch(
                    text, chat.get("id"), thread_id, update_id=uid if isinstance(uid, int) else None
                )
                if reply is not None:
                    self.counts.command += 1
                    ticket = self.client.notify(
                        reply.text,
                        kind="cmd.reply",
                        source=COMMAND_SOURCE,
                        subject=f"u{uid}",
                        parse_mode=reply.parse_mode,
                        chat=str(chat.get("id")),
                        thread_id=thread_id,
                    )
                    return RouteResult("command", ticket=ticket)
        if self.inbox is not None and self.inbox.allows(update):
            rep = self.inbox.process([update])
            self.counts.inbox += 1
            return RouteResult("inbox", inbox=rep)
        if not any(isinstance(update.get(k), Mapping) for k in ALLOWED_UPDATES):
            self.counts.not_message += 1
            return RouteResult("not_message")
        self.counts.ignored += 1  # 허용 밖 대화 — 건수만(chat id 는 남기지 않는다)
        return RouteResult("ignored")


__all__ = [
    "ALLOWED_UPDATES",
    "SECRET_HEADER",
    "TG_UPDATE_PREFIX",
    "TG_UPDATE_TTL_S",
    "WEBHOOK_PATH",
    "InboundEntry",
    "InboundQueue",
    "InboundRouter",
    "RouteResult",
    "WebhookHandler",
    "WebhookResult",
    "handle_update",
    "secret_ok",
]
