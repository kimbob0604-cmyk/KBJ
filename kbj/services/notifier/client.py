"""알림 넣기 — kbj 작업용 `notify*`(설계 §1.6).

어디서 부르든 하는 일은 하나다: 정책으로 중복 키를 만들어 대기열(`notify:outbox`)에 넣는다.
텔레그램은 부르지 않는다(보내는 것은 notifier 서비스 하나 — U1). 그래서 봇 토큰이 필요 없다.

- kbj 작업: `notify(text, kind=…, source=…)` → `NotifyTicket(ok, id, reason, duplicate)`.
  발송 꺼짐(`KBJ_NOTIFY_ENABLED=false`, 기본)이어도 넣는다 — notifier 가 정책·중복 판정까지 하고
  `suppressed` 로 기록한다(§5.5). 이때 ticket 은 `ok=True, reason="suppressed"`.
- P9(LEGACY): legacy 발송 7벌을 대신하던 shim(`legacy_send*`·`legacy_kinds` 프레임 찾기)은 ET·SD 를
  지우며 같이 지웠다(docs/p9_design.md 'LEGACY 구현 기록').
- 실패는 삼키지 않는다: Redis 가 없거나 설정이 틀리면 ticket `ok=False` + 사유로
  돌려주고 경고 로그를 남긴다. 사유에 키·토큰이 실리지 않게 가린다.
- 기본 클라이언트는 환경(`KBJ_REDIS_URL`·`KBJ_NOTIFY_ENABLED`)으로 한 번 만든다. 시험·시뮬레이션은
  `set_default_client()` 로 바꿔 끼운다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
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
)
from kbj.services.notifier.telegram_api import Attachment
from kbj.services.runtime.heartbeat import Heartbeat, connect_redis
from kbj.store.redis_keys import NOTIFY_WEBHOOK_INFO, heartbeat_key

log = logging.getLogger(__name__)

POLICY: Final = "policy"  # parse_mode 를 정책(notify.yaml)에서 가져오라는 표시
SUPPRESSED: Final = "suppressed"
NOTIFIER_SERVICE: Final = "notifier"


def _digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest_files(caption: str, atts: Sequence[Attachment]) -> str:
    h = hashlib.sha256(caption.encode("utf-8"))
    for a in atts:
        h.update(b"\0" + a.name.encode("utf-8") + b"\0" + hashlib.sha256(a.data).digest())
    return h.hexdigest()


def _clean_reason(e: BaseException) -> str:
    return mask_text(f"{type(e).__name__}: {e}")[:300]


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
        gate, log_key = dedup_key(policy, as_of=day, subject=subject, body_sha256=body_sha, now=now)
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
        )


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
    "POLICY",
    "NotifyClient",
    "NotifyTicket",
    "default_client",
    "notify",
    "notify_document",
    "notify_media",
    "set_default_client",
    "webhook_status",
    "webhook_status_from",
]
