"""텔레그램 Bot API — `api.telegram.org` 를 부르는 유일한 파일(설계 §1.6, §5.1, U1).

승격 원본:
- ET `board/report/telegram.py`(`API`:50, `_safe_err`:53, `_why`:770, `send`:779,
  `send_document`:822, `check`:857) — 실패는 예외가 아니라 사유 문자열로(`SendResult.reason`),
  텔레그램이 준 거절 사유를 요약하지 않고 그대로 옮긴다, 없는 파일은 조용히 성공으로 넘기지
  않는다, 점검은 getMe·getChat 만 — 메시지를 보내지 않는다.
- ET `monitor/flow/telegram.py:send_photos`(:36) — sendMediaGroup 10장 묶음, 캡션은 첫 장에만.
- bok `send-telegram.js:34` — 429 는 `retry_after` + 1초 쉬고 다시(최대 4회).

규칙:
- 봇 토큰은 URL 경로(`/bot<토큰>/<메서드>`)에 실린다. httpx 예외 문구에는 URL 이 통째로 들어 있어,
  사유를 만들 때 토큰 원문을 지우고(`redact`) 비밀 형태를 가린다(`mask_text` — 절대 규칙 5).
- chat id 는 사유·로그에 싣지 않는다(비밀 취급 — secrets.md).
- 이 클래스는 **한 번 부르면 한 번 보낸다**. 하루 1회·쿨다운·발송 꺼짐·재시도는 notifier 서비스가
  한다(service.py). 이 파일을 notifier 밖에서 import 하지 않는다(설계 §11.4 E 검사).
- 레이트리미터(`rl:telegram:<토큰 해시>` 전체 25/s, 대화당 1/s — config/limits.yaml)는 주입한다.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import httpx
from pydantic import SecretStr

from kbj.core.masking import mask_text, redact
from kbj.data.ratelimit import Priority, RateLimiter
from kbj.services.notifier.format import MEDIA_GROUP_MAX, TG_LIMIT, cap_caption

API_HOST: Final = "https://api.telegram.org"
TIMEOUT: Final = 20.0  # 호출 1건(ET TIMEOUT)
UPLOAD_TIMEOUT: Final = 80.0  # 파일 업로드는 본문보다 오래 걸린다(ET TIMEOUT * 4)
RETRY_AFTER_MAX: Final = 4  # 429 재시도 횟수(bok)
REASON_MAX: Final = 300

TOKEN_MISSING: Final = "KBJ_TELEGRAM_BOT_TOKEN 이 없다. notifier 환경변수로 주입하라"  # noqa: S105 — 안내 문구
CHAT_MISSING: Final = "KBJ_TELEGRAM_CHAT_ID 가 없다. 보낼 대화방을 모른다"
BODY_EMPTY: Final = "본문이 비어 있다. 보낼 것이 없다"


@dataclass(frozen=True)
class Attachment:
    """보낼 파일 하나 — 이름과 바이트. 대기열(outbox)에 바이트로 실려 notifier 로 온다."""

    name: str
    data: bytes

    @classmethod
    def from_path(cls, path: str | Path) -> Attachment:
        p = Path(path)
        return cls(p.name, p.read_bytes())


@dataclass(frozen=True)
class SendResult:
    """발송 한 번의 결과. 실패해도 예외 대신 사유(ET `(ok, 사유)` 계약)."""

    ok: bool
    reason: str
    message_ids: tuple[int, ...] = ()
    status: int | None = None  # 마지막 HTTP 상태(받았을 때)
    transient: bool = False  # 다시 해 볼 만한 실패(네트워크·5xx·429 초과)
    parts_sent: int = 0  # 나눠 보낼 때 성공한 조각 수(앞에서부터)


class TelegramApiError(RuntimeError):
    """조회·설정 호출(getMe·getChat·getWebhookInfo·setWebhook) 실패. 문구에 토큰이 없다."""

    def __init__(self, reason: str, *, status: int | None = None, transient: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.transient = transient


@dataclass(frozen=True)
class _Reply:
    status: int | None
    body: dict[str, Any] | None
    reason: str  # 실패 사유(성공이면 "")
    transient: bool = False

    @property
    def ok(self) -> bool:
        return self.status == 200 and bool(self.body and self.body.get("ok"))

    @property
    def result(self) -> Any:
        return (self.body or {}).get("result")


def _why(status: int | None, body: dict[str, Any] | None, text: str) -> str:
    """텔레그램이 준 거절 사유를 그대로(ET `_why`). JSON 이 아니면 본문 앞 200자."""
    if body is not None:
        desc = body.get("description")
        return str(desc) if desc else json.dumps(body, ensure_ascii=False)[:200]
    return (text or "")[:200]


def _retry_after(body: dict[str, Any] | None) -> float | None:
    params = (body or {}).get("parameters")
    if isinstance(params, dict):
        ra: object = params.get("retry_after")  # pyright: ignore[reportUnknownMemberType]
        if isinstance(ra, int | float) and not isinstance(ra, bool) and ra >= 0:
            return float(ra)
    return None


def _flag(v: bool) -> str:
    return "true" if v else "false"


def _message_id(result: Any) -> int | None:
    if isinstance(result, dict):
        mid = result.get("message_id")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if isinstance(mid, int):
            return mid
    return None


class TelegramApi:
    """봇 토큰 하나의 Bot API 클라이언트."""

    def __init__(
        self,
        token: SecretStr | None,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = TIMEOUT,
        upload_timeout: float = UPLOAD_TIMEOUT,
        sleep: Callable[[float], None] = time.sleep,
        limiter: RateLimiter | None = None,
        chat_limiter: Callable[[str], RateLimiter | None] | None = None,
        retry_after_max: int = RETRY_AFTER_MAX,
        base_url: str = API_HOST,
    ) -> None:
        if retry_after_max < 0:
            raise ValueError("retry_after_max 는 0 이상")
        self._token = token
        self._timeout = timeout
        self._upload_timeout = upload_timeout
        self._sleep = sleep
        self._limiter = limiter
        self._chat_limiter = chat_limiter
        self._retry_after_max = retry_after_max
        self._http = httpx.Client(base_url=base_url, transport=transport, timeout=timeout)

    # ── 공통 ─────────────────────────────────────────────────────────────────────────
    @property
    def has_token(self) -> bool:
        return self._token is not None and bool(self._token.get_secret_value())

    def close(self) -> None:
        self._http.close()

    def _clean(self, text: str, chat_id: str | None = None) -> str:
        """사유 문구 — 토큰·chat id 원문을 지우고 비밀 형태를 가린 뒤 자른다.

        원문 지우기는 토큰 8자·chat id 5자 이상일 때만 — 시험용 한두 글자 값이 문구의 같은 글자를
        모두 지우지 않게(실제 봇 토큰은 40자, 그룹 chat id 는 14자 안팎이다. 형태 가리기는 길이와
        무관하게 한다). chat id 는 조회(getChat)의 GET 주소에 실려 예외 문구로 샐 수 있다.
        """
        known: list[SecretStr | str] = []
        if self._token and len(self._token.get_secret_value()) >= 8:
            known.append(self._token)
        if chat_id and len(chat_id) >= 5:
            known.append(chat_id)
        out = mask_text(redact(text, known))
        return out if len(out) <= REASON_MAX else out[: REASON_MAX - 1] + "…"

    def _acquire(self, method: str, chat_id: str | None) -> None:
        if self._limiter is not None:
            self._limiter.acquire(Priority.P2, method)
        if chat_id is not None and self._chat_limiter is not None:
            per_chat = self._chat_limiter(chat_id)
            if per_chat is not None:
                per_chat.acquire(Priority.P2, method)

    def _post(
        self,
        method: str,
        *,
        data: Mapping[str, str] | None = None,
        files: Mapping[str, tuple[str, bytes]] | None = None,
        chat_id: str | None = None,
        timeout: float | None = None,
        http_get: bool = False,
    ) -> _Reply:
        """한 메서드 호출. 429 는 retry_after 만큼 쉬고 다시(최대 `retry_after_max` 회)."""
        if self._token is None:
            return _Reply(None, None, TOKEN_MISSING)
        url = f"/bot{self._token.get_secret_value()}/{method}"
        hide = chat_id or (data or {}).get("chat_id")
        attempts = 0
        while True:
            try:
                self._acquire(method, chat_id)
            except (
                Exception
            ) as e:  # 리미터 대기 초과·Redis 오류 — 보내지 않고 다시 해 볼 만한 실패로
                why = self._clean(f"{method} 레이트리미터 실패: {type(e).__name__}")
                return _Reply(None, None, why, transient=True)
            try:
                if http_get:
                    resp = self._http.get(url, params=data, timeout=timeout or self._timeout)
                else:
                    resp = self._http.post(
                        url, data=data, files=files, timeout=timeout or self._timeout
                    )
            except httpx.HTTPError as e:
                why = self._clean(f"{method} 실패: {type(e).__name__}: {e}", hide)
                return _Reply(None, None, why, transient=True)
            try:
                raw: Any = resp.json()
                body: dict[str, Any] | None = raw if isinstance(raw, dict) else None
            except ValueError:
                body = None
            if resp.status_code == 200 and body is not None and body.get("ok"):
                return _Reply(200, body, "")
            code = body.get("error_code") if body is not None else None
            if resp.status_code == 429 or code == 429:
                wait = _retry_after(body)
                if self._limiter is not None:
                    try:
                        self._limiter.on_rate_limited()
                    except Exception as e:  # 리미터(Redis) 오류 — 예외 대신 다시 해 볼 실패로
                        why = self._clean(
                            f"{method} 한도 초과(429) · 레이트리미터 실패: {type(e).__name__}"
                        )
                        return _Reply(resp.status_code, body, why, transient=True)
                if attempts < self._retry_after_max:
                    attempts += 1
                    self._sleep((wait if wait is not None else 1.0) + 1.0)
                    continue
                why = self._clean(
                    f"{method} 한도 초과(429) {attempts}회 재시도 뒤에도 거부 · "
                    f"{_why(resp.status_code, body, resp.text)}",
                    hide,
                )
                return _Reply(resp.status_code, body, why, transient=True)
            told = _why(resp.status_code, body, resp.text)
            why = self._clean(f"{method} 거부: HTTP {resp.status_code} · {told}", hide)
            return _Reply(resp.status_code, body, why, transient=resp.status_code >= 500)

    def _query(self, method: str, data: Mapping[str, str] | None = None) -> Any:
        """조회·설정 호출 — 실패는 `TelegramApiError`."""
        if not self.has_token:
            raise TelegramApiError(TOKEN_MISSING)
        r = self._post(method, data=data, http_get=data is None or method.startswith("get"))
        if not r.ok:
            raise TelegramApiError(r.reason, status=r.status, transient=r.transient)
        return r.result

    @staticmethod
    def _common(
        chat_id: str, thread_id: int | None, silent: bool, extra: Mapping[str, str]
    ) -> dict[str, str]:
        data = {"chat_id": chat_id, "disable_notification": _flag(silent), **extra}
        if thread_id is not None:
            data["message_thread_id"] = str(thread_id)
        return data

    def _preflight(self, chat_id: str | None) -> tuple[str | None, str]:
        """(보낼 chat, 못 보낼 사유). chat 이 None 이면 사유를 돌려준다."""
        if not self.has_token:
            return None, TOKEN_MISSING
        if not chat_id:
            return None, CHAT_MISSING
        return chat_id, ""

    # ── 발송 ─────────────────────────────────────────────────────────────────────────
    def send_message(
        self,
        chat_id: str | None,
        text: str,
        *,
        thread_id: int | None = None,
        parse_mode: str | None = "HTML",
        silent: bool = False,
        disable_preview: bool = True,
    ) -> SendResult:
        """메시지 한 건(4,096자 이하). parse_mode=None 이면 서식 없는 평문 — `parse_mode` 를 아예
        싣지 않는다(ET TestPlainTextMode). 긴 본문은 `send_text` 로 나눠 보낸다."""
        chat, bad = self._preflight(chat_id)
        if chat is None:
            return SendResult(False, bad)
        if not (text or "").strip():
            return SendResult(False, BODY_EMPTY)
        if len(text) > TG_LIMIT:
            return SendResult(False, f"본문이 {len(text)}자 — {TG_LIMIT}자 상한(나눠 보내라)")
        extra = {"text": text, "disable_web_page_preview": _flag(disable_preview)}
        if parse_mode:
            extra["parse_mode"] = parse_mode
        r = self._post(
            "sendMessage", data=self._common(chat, thread_id, silent, extra), chat_id=chat
        )
        if not r.ok:
            return SendResult(False, r.reason, status=r.status, transient=r.transient)
        mid = _message_id(r.result)
        return SendResult(True, "1건 발송", (mid,) if mid is not None else (), 200, parts_sent=1)

    def send_text(
        self,
        chat_id: str | None,
        chunks: Sequence[str],
        *,
        thread_id: int | None = None,
        parse_mode: str | None = "HTML",
        silent: bool = False,
        start: int = 0,
    ) -> SendResult:
        """이미 나눈 조각들을 차례로. 실패하면 거기서 멈추고 `parts_sent` 로 어디까지 갔는지 알린다
        (재시도는 `start=parts_sent` 로 이어서 — 이미 간 조각을 다시 보내지 않는다)."""
        n = len(chunks)
        if not 0 <= start <= n:
            raise ValueError("start 범위 밖")
        ids: list[int] = []
        for i in range(start, n):
            r = self.send_message(
                chat_id, chunks[i], thread_id=thread_id, parse_mode=parse_mode, silent=silent
            )
            if not r.ok:
                why = f"{i + 1}/{n}번째 조각 전송 실패: {r.reason}" if n > 1 else r.reason
                return SendResult(
                    False, why, tuple(ids), r.status, transient=r.transient, parts_sent=i
                )
            ids.extend(r.message_ids)
        return SendResult(True, f"{n}건 발송", tuple(ids), 200, parts_sent=n)

    def send_document(
        self,
        chat_id: str | None,
        document: str | Path | Attachment,
        caption: str = "",
        *,
        thread_id: int | None = None,
        silent: bool = False,
    ) -> SendResult:
        """파일 한 개를 첨부로(ET `send_document`). 없는 파일은 실패 — 보냈다고 적지 않는다."""
        chat, bad = self._preflight(chat_id)
        if chat is None:
            return SendResult(False, bad)
        if isinstance(document, Attachment):
            att = document
        else:
            p = Path(document)
            if not p.is_file():
                return SendResult(False, f"보낼 파일이 없다: {p.name}")
            att = Attachment.from_path(p)
        data = self._common(chat, thread_id, silent, {"caption": cap_caption(caption)})
        r = self._post(
            "sendDocument",
            data=data,
            files={"document": (att.name, att.data)},
            chat_id=chat,
            timeout=self._upload_timeout,
        )
        if not r.ok:
            return SendResult(False, r.reason, status=r.status, transient=r.transient)
        mid = _message_id(r.result)
        return SendResult(True, att.name, (mid,) if mid is not None else (), 200, parts_sent=1)

    def send_media_group(
        self,
        chat_id: str | None,
        photos: Sequence[str | Path | Attachment],
        caption: str = "",
        *,
        thread_id: int | None = None,
        silent: bool = False,
        start: int = 0,
    ) -> SendResult:
        """사진 여러 장을 10장씩 묶어(ET flow `send_photos`). 캡션은 첫 묶음 첫 장에만.
        `parts_sent`·`start` 는 묶음 단위."""
        chat, bad = self._preflight(chat_id)
        if chat is None:
            return SendResult(False, bad)
        atts: list[Attachment] = []
        missing: list[str] = []
        for ph in photos:
            if isinstance(ph, Attachment):
                atts.append(ph)
                continue
            p = Path(ph)
            if p.is_file():
                atts.append(Attachment.from_path(p))
            else:
                missing.append(p.name)
        if missing:
            return SendResult(False, f"보낼 그림이 없다: {', '.join(missing)}")
        if not atts:
            return SendResult(False, "보낼 그림이 없다")
        groups = [atts[i : i + MEDIA_GROUP_MAX] for i in range(0, len(atts), MEDIA_GROUP_MAX)]
        if not 0 <= start <= len(groups):
            raise ValueError("start 범위 밖")
        ids: list[int] = []
        for gi in range(start, len(groups)):
            media: list[dict[str, str]] = []
            files: dict[str, tuple[str, bytes]] = {}
            for i, att in enumerate(groups[gi]):
                key = f"photo{i}"
                item = {"type": "photo", "media": f"attach://{key}"}
                if gi == 0 and i == 0 and caption:
                    item["caption"] = cap_caption(caption)
                media.append(item)
                files[key] = (att.name, att.data)
            data = self._common(
                chat, thread_id, silent, {"media": json.dumps(media, ensure_ascii=False)}
            )
            r = self._post(
                "sendMediaGroup",
                data=data,
                files=files,
                chat_id=chat,
                timeout=self._upload_timeout,
            )
            if not r.ok:
                why = f"그림 {gi + 1}/{len(groups)}번째 묶음 거부: {r.reason}"
                return SendResult(
                    False, why, tuple(ids), r.status, transient=r.transient, parts_sent=gi
                )
            res = r.result
            if isinstance(res, list):
                for m in res:  # pyright: ignore[reportUnknownVariableType]
                    mid = _message_id(m)
                    if mid is not None:
                        ids.append(mid)
        return SendResult(True, f"그림 {len(atts)}장", tuple(ids), 200, parts_sent=len(groups))

    # ── 조회·설정 ────────────────────────────────────────────────────────────────────
    def get_me(self) -> dict[str, Any]:
        res = self._query("getMe")
        return res if isinstance(res, dict) else {}

    def get_chat(self, chat_id: str) -> dict[str, Any]:
        res = self._query("getChat", {"chat_id": chat_id})
        return res if isinstance(res, dict) else {}

    def get_webhook_info(self) -> dict[str, Any]:
        res = self._query("getWebhookInfo")
        return res if isinstance(res, dict) else {}

    def set_webhook(
        self,
        url: str,
        secret: SecretStr,
        allowed_updates: Sequence[str],
        *,
        drop_pending: bool = False,
    ) -> bool:
        """웹훅 등록(수동 — `python -m kbj.services.notifier setup-webhook`). secret 은
        `X-Telegram-Bot-Api-Secret-Token` 헤더로 되돌아온다."""
        if not secret.get_secret_value():
            raise TelegramApiError("KBJ_TELEGRAM_WEBHOOK_SECRET 이 없다")
        data = {
            "url": url,
            "secret_token": secret.get_secret_value(),
            "allowed_updates": json.dumps(list(allowed_updates)),
            "drop_pending_updates": _flag(drop_pending),
        }
        try:
            res = self._query("setWebhook", data)
        except TelegramApiError as e:
            raise TelegramApiError(
                mask_text(redact(e.reason, [secret])), status=e.status, transient=e.transient
            ) from None
        return bool(res)

    def check(self, chat_id: str | None) -> tuple[bool, str]:
        """봇 연결 확인(ET `check`) — getMe 다음 getChat. **메시지를 보내지 않는다.**

        봇이 살아 있는 것과 그 방에 보낼 수 있는 것은 다르다(쫓겨났거나 chat id 가 틀리면 getMe 만
        통과한다). 절반의 성공을 참으로 보고하지 않는다.
        """
        if not self.has_token:
            return False, TOKEN_MISSING
        me = self._post("getMe", http_get=True)
        if not me.ok:
            return False, f"getMe 실패 · {me.reason}"
        res = me.result
        uname = res.get("username") if isinstance(res, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        name = "@" + (str(uname) if uname else "이름없음")  # pyright: ignore[reportUnknownArgumentType]
        if not chat_id:
            return False, f"{name} 은 응답하지만 KBJ_TELEGRAM_CHAT_ID 가 없다"
        c = self._post("getChat", data={"chat_id": chat_id}, http_get=True)
        if not c.ok:
            return False, f"{name} · 대화방 확인 실패 · {c.reason}"
        room = c.result if isinstance(c.result, dict) else {}
        where = (
            room.get("title") or room.get("username") or room.get("type") or "대화방"  # pyright: ignore[reportUnknownMemberType]
        )
        return True, f"{name} → {where}"
