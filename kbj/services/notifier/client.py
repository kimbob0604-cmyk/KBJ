"""알림 넣기 — kbj 작업용 `notify*` 와 legacy 발송 7벌을 대신하는 공용 shim(설계 §1.6·§5.9).

어디서 부르든 하는 일은 하나다: 정책으로 중복 키를 만들어 대기열(`notify:outbox`)에 넣는다.
텔레그램은 부르지 않는다(보내는 것은 notifier 서비스 하나 — U1). 그래서 봇 토큰이 필요 없다.

- kbj 작업: `notify(text, kind=…, source=…)` → `NotifyTicket(ok, id, reason, duplicate)`.
  발송 꺼짐(`KBJ_NOTIFY_ENABLED=false`, 기본)이어도 넣는다 — notifier 가 정책·중복 판정까지 하고
  `suppressed` 로 기록한다(§5.5). 이때 ticket 은 `ok=True, reason="suppressed"`.
- legacy shim: `legacy_send(text, source=…, parse_mode=…, kind=None, numbered=False)` →
  `(ok, 사유)`(ET 식). 꺼짐이면 `(False, "발송 꺼짐(KBJ_NOTIFY_ENABLED=false) — 기록만")` — SD
  `send_telegram` 의 `TELEGRAM_ENABLED` 꺼짐 반환(False)과 같은 뜻. `parse_mode=None` 은 평문이다
  (기본값으로 바꾸지 않는다 — ET 미국장 브리프).
- kind 를 안 넘긴 legacy 호출은 호출 스택을 거슬러 올라가며 `config/notify.yaml legacy_kinds` 에
  있는 함수 이름을 찾는다(전환 기간 한정 [제안]). 없으면 `legacy_default_kind`.
- 실패는 삼키지 않는다: Redis 가 없거나 설정이 틀리면 ticket `ok=False`·shim `(False, 사유)` 로
  돌려주고 경고 로그를 남긴다. 사유에 키·토큰이 실리지 않게 가린다.
- 기본 클라이언트는 환경(`KBJ_REDIS_URL`·`KBJ_NOTIFY_ENABLED`)으로 한 번 만든다. 시험·시뮬레이션은
  `set_default_client()` 로 바꿔 끼운다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import FrameType
from typing import Any, Final

from redis import Redis
from redis.exceptions import RedisError

from kbj.config.settings import Settings
from kbj.core.masking import mask_text
from kbj.core.time import utcnow
from kbj.services.notifier.outbox import NotifyTicket, OutboundMessage, Outbox
from kbj.services.notifier.policy import (
    KindPolicy,
    NotifyConfig,
    UnknownKind,
    dedup_key,
    default_as_of,
    load_notify_config,
    sha16,
)
from kbj.services.notifier.telegram_api import Attachment
from kbj.services.runtime.heartbeat import Heartbeat, connect_redis
from kbj.store.redis_keys import NOTIFY_WEBHOOK_INFO, heartbeat_key

log = logging.getLogger(__name__)

POLICY: Final = "policy"  # parse_mode 를 정책(notify.yaml)에서 가져오라는 표시
DISABLED_REASON: Final = "발송 꺼짐(KBJ_NOTIFY_ENABLED=false) — 기록만"
SUPPRESSED: Final = "suppressed"
NOTIFIER_SERVICE: Final = "notifier"
_FRAME_DEPTH: Final = 12  # legacy 함수 이름을 찾을 때 거슬러 올라갈 프레임 수


def _digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest_files(caption: str, atts: Sequence[Attachment]) -> str:
    h = hashlib.sha256(caption.encode("utf-8"))
    for a in atts:
        h.update(b"\0" + a.name.encode("utf-8") + b"\0" + hashlib.sha256(a.data).digest())
    return h.hexdigest()


def _clean_reason(e: BaseException) -> str:
    return mask_text(f"{type(e).__name__}: {e}")[:300]


def _legacy_origin(source: str, fn: str | None, part: str, content: str) -> str:
    """legacy 발송의 '부른 곳' — 하루 1회(`daily`) 규칙에서 무엇을 한 번으로 셀지(정책 `origin`).

    - `legacy_kinds` 로 찾은 함수(fn): 그 함수는 한 번 돌 때 한 통을 보낸다(SD 아침·마감 등 — 표에
      올린 14개를 확인했다) → 함수마다 하루 1회.
    - kind 를 넘긴 호출(fn 없음): ETF 리포트 여러 통(ET `tracker.send_report`)·수급 종목별
      차트(ET flow `run` → `send_report`)처럼 한 번 돌 때 여러 통을 보낸다 → 내용 해시를 붙여 다른
      내용은 모두 가고 같은 내용의 되풀이(재실행)만 막는다. 붙이지 않으면 둘째 통부터 duplicate 로
      빠지는데 legacy 는 `(True, '중복 …')` 을 받아 보낸 줄 안다(조용한 유실).
    """
    if fn is not None:
        return f"{source}.{fn}.{part}"
    return f"{source}.-.{part}-{sha16(content)[:8]}"


@dataclass
class NotifyClient:
    """대기열에 넣는 쪽. `outbox_factory` 는 처음 넣을 때 한 번 부른다(Redis 접속을 늦춘다)."""

    config: NotifyConfig
    enabled: bool
    outbox: Outbox | None = None
    outbox_factory: Callable[[], Outbox] | None = None
    now: Callable[[], datetime] = utcnow

    def _box(self) -> Outbox:
        if self.outbox is None:
            if self.outbox_factory is None:
                raise RuntimeError("KBJ_REDIS_URL 이 없다 — 알림 대기열을 모른다")
            self.outbox = self.outbox_factory()
        return self.outbox

    # ── 공통 ─────────────────────────────────────────────────────────────────────────
    def _enqueue(
        self,
        policy: KindPolicy,
        *,
        source: str,
        text: str | None,
        caption: str,
        document: Attachment | None,
        media: tuple[Attachment, ...],
        as_of: date | None,
        subject: str | None,
        parse_mode: str | None,
        silent: bool,
        chat: str | None,
        thread_id: int | None,
        numbered: bool,
        origin: str | None,
    ) -> NotifyTicket:
        if not source or not source.strip():
            return NotifyTicket(False, None, "source(부른 곳)가 비었다")
        now = self.now()
        day = as_of if as_of is not None else default_as_of(now)
        atts = (document,) if document is not None else media
        size = sum(len(a.data) for a in atts)
        cap = self.config.delivery.max_attachment_bytes
        if size > cap:
            return NotifyTicket(False, None, f"첨부가 {size:,}바이트 — 상한 {cap:,}바이트 초과")
        body_sha = _digest_files(caption, atts) if atts else _digest_text(text or "")
        gate, log_key = dedup_key(
            policy, as_of=day, subject=subject, body_sha256=body_sha, now=now, origin=origin
        )
        msg = OutboundMessage(
            kind=policy.kind,
            source=source.strip(),
            requested_at=now,
            as_of=day,
            dedup_key=gate,
            log_key=log_key,
            gate_ttl_s=policy.gate_ttl_s(),
            body_sha256=body_sha,
            text=text,
            caption=caption,
            document=document,
            media=media,
            subject=subject,
            chat=chat,
            thread_id=thread_id,
            parse_mode=parse_mode,
            silent=silent,
            numbered=numbered,
        )
        reject = "cooldown" if policy.dedup == "cooldown" else "duplicate"
        try:
            ticket = self._box().put(msg, reject_status=reject)
        except (RedisError, RuntimeError, OSError) as e:
            why = f"알림 대기열(Redis)에 넣지 못했다 — 보내지 않았다: {_clean_reason(e)}"
            log.warning(
                json.dumps(
                    {
                        "service": "notifier.client",
                        "event": "enqueue_failed",
                        "kind": policy.kind,
                        "source": source,
                        "error": type(e).__name__,
                    },
                    ensure_ascii=False,
                )
            )
            return NotifyTicket(False, None, why)
        if not self.enabled and not ticket.duplicate:
            return NotifyTicket(True, ticket.id, SUPPRESSED, dedup_key=ticket.dedup_key)
        return ticket

    def _policy(self, kind: str) -> KindPolicy:
        return self.config.policy(kind)

    # ── kbj 작업용 ───────────────────────────────────────────────────────────────────
    def notify(
        self,
        text: str,
        *,
        kind: str,
        source: str,
        as_of: date | None = None,
        subject: str | None = None,
        parse_mode: str | None = POLICY,
        silent: bool | None = None,
        chat: str | None = None,
        thread_id: int | None = None,
        numbered: bool = False,
    ) -> NotifyTicket:
        """본문 알림 하나. parse_mode·silent 를 안 주면 종류의 규칙(notify.yaml)을 따른다."""
        try:
            pol = self._policy(kind)
        except UnknownKind as e:
            return NotifyTicket(False, None, str(e))
        if not (text or "").strip():
            return NotifyTicket(False, None, "본문이 비어 있다. 보낼 것이 없다")
        return self._enqueue(
            pol,
            source=source,
            text=text,
            caption="",
            document=None,
            media=(),
            as_of=as_of,
            subject=subject,
            parse_mode=pol.parse_mode if parse_mode == POLICY else parse_mode,
            silent=pol.silent if silent is None else silent,
            chat=chat,
            thread_id=thread_id,
            numbered=numbered,
            origin=None,
        )

    def notify_document(
        self,
        path: str | Path,
        caption: str = "",
        *,
        kind: str,
        source: str,
        as_of: date | None = None,
        subject: str | None = None,
        silent: bool | None = None,
        chat: str | None = None,
        thread_id: int | None = None,
        origin: str | None = None,
    ) -> NotifyTicket:
        """파일 한 개 첨부. 없는 파일은 실패(ET — 없는 것을 보냈다고 적지 않는다)."""
        try:
            pol = self._policy(kind)
        except UnknownKind as e:
            return NotifyTicket(False, None, str(e))
        p = Path(path)
        if not p.is_file():
            return NotifyTicket(False, None, f"보낼 파일이 없다: {p.name}")
        try:
            att = Attachment.from_path(p)
        except OSError as e:
            return NotifyTicket(False, None, f"파일을 읽지 못했다: {p.name} · {type(e).__name__}")
        return self._enqueue(
            pol,
            source=source,
            text=None,
            caption=caption or "",
            document=att,
            media=(),
            as_of=as_of,
            subject=subject,
            parse_mode=None,
            silent=pol.silent if silent is None else silent,
            chat=chat,
            thread_id=thread_id,
            numbered=False,
            origin=origin,
        )

    def notify_media(
        self,
        paths: Sequence[str | Path],
        caption: str = "",
        *,
        kind: str,
        source: str,
        as_of: date | None = None,
        subject: str | None = None,
        silent: bool | None = None,
        chat: str | None = None,
        thread_id: int | None = None,
        origin: str | None = None,
    ) -> NotifyTicket:
        """사진 여러 장(10장씩 묶어 보낸다). 없는 그림이 하나라도 있으면 실패(ET flow)."""
        try:
            pol = self._policy(kind)
        except UnknownKind as e:
            return NotifyTicket(False, None, str(e))
        ps = [Path(p) for p in paths]
        missing = [p.name for p in ps if not p.is_file()]
        if missing:
            return NotifyTicket(False, None, f"보낼 그림이 없다: {', '.join(missing)}")
        if not ps:
            return NotifyTicket(False, None, "보낼 그림이 없다")
        try:
            atts = tuple(Attachment.from_path(p) for p in ps)
        except OSError as e:
            return NotifyTicket(False, None, f"그림을 읽지 못했다: {type(e).__name__}")
        return self._enqueue(
            pol,
            source=source,
            text=None,
            caption=caption or "",
            document=None,
            media=atts,
            as_of=as_of,
            subject=subject,
            parse_mode=None,
            silent=pol.silent if silent is None else silent,
            chat=chat,
            thread_id=thread_id,
            numbered=False,
            origin=origin,
        )

    # ── legacy shim ──────────────────────────────────────────────────────────────────
    def legacy_kind(self, kind: str | None, frame: FrameType | None) -> tuple[str, str | None]:
        """(종류, 찾은 legacy 함수 이름). kind 를 넘겼으면 그대로."""
        if kind is not None:
            return kind, None
        f = frame
        for _ in range(_FRAME_DEPTH):
            if f is None:
                break
            name = f.f_code.co_name
            if f.f_globals.get("__name__") != __name__ and name in self.config.legacy_kinds:
                return self.config.legacy_kinds[name], name
            f = f.f_back
        return self.config.legacy_default_kind, None

    def _legacy_result(self, ticket: NotifyTicket) -> tuple[bool, str]:
        if not ticket.ok:
            return False, ticket.reason
        if not self.enabled:
            return False, DISABLED_REASON
        if ticket.duplicate:
            return True, f"{'쿨다운' if ticket.reason == 'cooldown' else '중복'} — 이미 넣은 알림"
        return True, f"대기열에 넣음({ticket.id})"

    def legacy_send(
        self,
        text: str,
        *,
        source: str,
        parse_mode: str | None = "HTML",
        kind: str | None = None,
        numbered: bool = False,
        silent: bool = False,
        frame: FrameType | None = None,
    ) -> tuple[bool, str]:
        """legacy `send_telegram`·`send`·`send_telegram_message` 대체. 반환 `(ok, 사유)`."""
        try:
            k, fn = self.legacy_kind(kind, frame if frame is not None else sys._getframe(1))
            pol = self._policy(k)
            if not (text or "").strip():
                return False, "본문이 비어 있다. 보낼 것이 없다"
            ticket = self._enqueue(
                pol,
                source=source,
                text=text,
                caption="",
                document=None,
                media=(),
                as_of=None,
                subject=None,
                parse_mode=parse_mode,
                silent=silent,
                chat=None,
                thread_id=None,
                numbered=numbered,
                origin=_legacy_origin(source, fn, "text", text),
            )
        except Exception as e:  # legacy 서버를 멈추지 않는다 — 사유는 돌려주고 로그로 남긴다
            log.warning("legacy_send 실패: %s", type(e).__name__)
            return False, _clean_reason(e)
        return self._legacy_result(ticket)

    def legacy_send_document(
        self,
        path: str | Path,
        caption: str = "",
        *,
        source: str,
        kind: str | None = None,
        silent: bool = False,
        frame: FrameType | None = None,
    ) -> tuple[bool, str]:
        """legacy `send_document` 대체. 반환 `(ok, 사유)`."""
        try:
            k, fn = self.legacy_kind(kind, frame if frame is not None else sys._getframe(1))
            name = Path(path).name
            ticket = self.notify_document(
                path,
                caption,
                kind=k,
                source=source,
                silent=silent,
                origin=f"{source}.{fn or '-'}.doc-{sha16(name)[:8]}",
            )
        except Exception as e:
            log.warning("legacy_send_document 실패: %s", type(e).__name__)
            return False, _clean_reason(e)
        return self._legacy_result(ticket)

    def legacy_send_media(
        self,
        paths: Sequence[str | Path],
        caption: str = "",
        *,
        source: str,
        kind: str | None = None,
        silent: bool = False,
        frame: FrameType | None = None,
    ) -> tuple[bool, str]:
        """legacy `send_photos`(sendMediaGroup) 대체. 반환 `(ok, 사유)`."""
        try:
            k, fn = self.legacy_kind(kind, frame if frame is not None else sys._getframe(1))
            names = "\0".join(Path(p).name for p in paths)
            ticket = self.notify_media(
                paths,
                caption,
                kind=k,
                source=source,
                silent=silent,
                origin=_legacy_origin(source, fn, "media", f"{caption}\0{names}"),
            )
        except Exception as e:
            log.warning("legacy_send_media 실패: %s", type(e).__name__)
            return False, _clean_reason(e)
        return self._legacy_result(ticket)


# ── 웹훅·notifier 상태(읽기만 — getWebhookInfo 는 notifier 가 10분마다 부른다) ──────────────────


def webhook_status_from(redis: Redis, now: datetime) -> dict[str, Any]:
    """`notify:webhook_info`(notifier 가 쓴 요약) + notifier 하트비트 나이.

    ET `check`·`tracker.doctor`·`triggers.py:812` 의 getMe·getWebhookInfo 직접 호출 대체
    (§5.9·#36·#46).
    """
    out: dict[str, Any] = {"ok": False, "reason": "", "notifier_alive": False}
    raw_hb: Any = redis.get(heartbeat_key(NOTIFIER_SERVICE))
    if raw_hb is not None:
        hb = Heartbeat.model_validate_json(raw_hb)
        age = (now - hb.at).total_seconds()
        out["notifier_age_s"] = age
        out["notifier_alive"] = age <= 120
        out["notify_enabled"] = hb.status.get("enabled")
    raw: Any = redis.get(NOTIFY_WEBHOOK_INFO)
    if raw is None:
        out["reason"] = "웹훅 점검 기록이 없다(notifier 가 아직 점검하지 않았다)"
        return out
    info: dict[str, Any] = json.loads(raw)
    out.update(info)
    if not out["notifier_alive"]:
        out["reason"] = "notifier 하트비트가 없다"
    elif info.get("checked") is False:
        # 발송 꺼짐·토큰 없음으로 getWebhookInfo 를 안 불렀다 — '안 걸려 있다' 로 단정하지 않는다
        out["reason"] = f"웹훅 점검 안 함: {info.get('error') or info.get('note') or '사유 없음'}"
    elif not info.get("url_set"):
        out["reason"] = "웹훅이 걸려 있지 않다(setup-webhook 필요)"
    elif info.get("error"):
        out["reason"] = f"웹훅 점검 실패: {info['error']}"
    else:
        out["ok"] = True
        out["reason"] = f"웹훅 정상 · 대기 {info.get('pending_update_count', '?')}건"
    return out


# ── 기본 클라이언트(모듈 함수) ──────────────────────────────────────────────────────────────

_default: NotifyClient | None = None
_default_lock = threading.Lock()


def _build_default() -> NotifyClient:
    settings = Settings()
    config = load_notify_config(settings=settings)
    url = settings.redis_url

    def factory() -> Outbox:
        if url is None:
            raise RuntimeError("KBJ_REDIS_URL 이 없다 — 알림 대기열을 모른다")
        return Outbox(connect_redis(url), now=utcnow)

    return NotifyClient(config, settings.notify_enabled, outbox_factory=factory)


def default_client() -> NotifyClient:
    global _default
    with _default_lock:
        if _default is None:
            _default = _build_default()
        return _default


def set_default_client(client: NotifyClient | None) -> None:
    """시험·시뮬레이션이 기본 클라이언트를 바꿔 끼운다(None 이면 다음에 환경으로 다시 만든다)."""
    global _default
    with _default_lock:
        _default = client


def _client_or_reason() -> tuple[NotifyClient | None, str]:
    try:
        return default_client(), ""
    except Exception as e:  # 설정 파일·환경 오류 — 사유를 돌려준다
        return None, f"알림 설정을 읽지 못했다: {_clean_reason(e)}"


def notify(
    text: str,
    *,
    kind: str,
    source: str,
    as_of: date | None = None,
    subject: str | None = None,
    parse_mode: str | None = POLICY,
    silent: bool | None = None,
    chat: str | None = None,
    thread_id: int | None = None,
    numbered: bool = False,
) -> NotifyTicket:
    c, why = _client_or_reason()
    if c is None:
        return NotifyTicket(False, None, why)
    return c.notify(
        text,
        kind=kind,
        source=source,
        as_of=as_of,
        subject=subject,
        parse_mode=parse_mode,
        silent=silent,
        chat=chat,
        thread_id=thread_id,
        numbered=numbered,
    )


def notify_document(
    path: str | Path,
    caption: str = "",
    *,
    kind: str,
    source: str,
    as_of: date | None = None,
    subject: str | None = None,
    silent: bool | None = None,
) -> NotifyTicket:
    c, why = _client_or_reason()
    if c is None:
        return NotifyTicket(False, None, why)
    return c.notify_document(
        path, caption, kind=kind, source=source, as_of=as_of, subject=subject, silent=silent
    )


def notify_media(
    paths: Sequence[str | Path],
    caption: str = "",
    *,
    kind: str,
    source: str,
    as_of: date | None = None,
    subject: str | None = None,
    silent: bool | None = None,
) -> NotifyTicket:
    c, why = _client_or_reason()
    if c is None:
        return NotifyTicket(False, None, why)
    return c.notify_media(
        paths, caption, kind=kind, source=source, as_of=as_of, subject=subject, silent=silent
    )


def legacy_send(
    text: str,
    *,
    source: str,
    parse_mode: str | None = "HTML",
    kind: str | None = None,
    numbered: bool = False,
    silent: bool = False,
) -> tuple[bool, str]:
    """legacy 발송 shim(§5.9). 반환 `(ok, 사유)` — SD 는 `[0]` 만 쓴다."""
    c, why = _client_or_reason()
    if c is None:
        return False, why
    return c.legacy_send(
        text,
        source=source,
        parse_mode=parse_mode,
        kind=kind,
        numbered=numbered,
        silent=silent,
        frame=sys._getframe(1),
    )


def legacy_send_document(
    path: str | Path,
    caption: str = "",
    *,
    source: str,
    kind: str | None = None,
    silent: bool = False,
) -> tuple[bool, str]:
    c, why = _client_or_reason()
    if c is None:
        return False, why
    return c.legacy_send_document(
        path, caption, source=source, kind=kind, silent=silent, frame=sys._getframe(1)
    )


def legacy_send_media(
    paths: Sequence[str | Path],
    caption: str = "",
    *,
    source: str,
    kind: str | None = None,
    silent: bool = False,
) -> tuple[bool, str]:
    c, why = _client_or_reason()
    if c is None:
        return False, why
    return c.legacy_send_media(
        paths, caption, source=source, kind=kind, silent=silent, frame=sys._getframe(1)
    )


def webhook_status() -> dict[str, Any]:
    """웹훅·notifier 상태 dict(`ok`·`reason` 포함). Redis 를 못 읽으면 `ok=False` + 사유."""
    try:
        settings = Settings()
        if settings.redis_url is None:
            return {"ok": False, "reason": "KBJ_REDIS_URL 이 없다"}
        return webhook_status_from(connect_redis(settings.redis_url), utcnow())
    except Exception as e:
        return {"ok": False, "reason": f"상태를 읽지 못했다: {_clean_reason(e)}"}


__all__ = [
    "DISABLED_REASON",
    "POLICY",
    "NotifyClient",
    "NotifyTicket",
    "default_client",
    "legacy_send",
    "legacy_send_document",
    "legacy_send_media",
    "notify",
    "notify_document",
    "notify_media",
    "set_default_client",
    "webhook_status",
    "webhook_status_from",
]
