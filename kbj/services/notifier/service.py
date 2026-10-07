"""notifier 서비스 — 대기열 소비·발송·발송 기록·받은 업데이트 처리·웹훅 점검(설계 §5.1·§5.5).

한 번(`step`)에 하는 일:
1. `notify:outbox` 를 읽어 메시지마다 — 2차 중복(발송 기록 UNIQUE) → 캐치업 시한 → **발송
   꺼짐이면 보내지 않고 `suppressed`**(§5.5, `KBJ_NOTIFY_ENABLED=false` 기본) → 켜짐이면
   텔레그램(토픽 thread, 4,096자 분할, 첨부) → `ops.notify_log`(메타)·`prv_alerts.notify_message`
   (본문).
2. `notify:inbound`(웹훅이 받은 업데이트)를 읽어 명령·인박스로 나눈다(webhook.InboundRouter).
3. 켜짐이면 10분마다 getWebhookInfo → Redis `notify:webhook_info` + health.

실패 규칙(절대 규칙 4 — 삼키지 않는다, 격리):
- 네트워크·5xx·429 초과 → `delivery.max_attempts` 까지 두 배 간격으로 다시(나눠 보낸 조각은 이어서 —
  이미 간 조각을 다시 보내지 않는다). 그 밖 4xx·조각 상한 초과·캐치업 시한 → 바로 `failed`.
- 끝내 `failed` 면 문지기 키를 풀어(부른 쪽이 다시 넣을 수 있게) health 경고를 낸다.
- 발송 기록(DB)을 못 쓰면 **보내지 않고** 그 항목을 대기열에 남겨 다음에 다시 — 기록 없는 발송·
  2차 중복 검사 없는 발송을 하지 않는다. health 경고.
- 발송 꺼짐 모드에서는 외부 HTTP 가 0 이다: 텔레그램(발송·getWebhookInfo)도, 인박스 oEmbed 도
  부르지 않는다(시험이 고정한다).
- 로그·health 에 본문·chat id·토큰을 싣지 않는다.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit

import httpx
from redis import Redis
from redis.exceptions import RedisError

from kbj.config.settings import Settings
from kbj.core.masking import mask_text
from kbj.data.limits import load_limits
from kbj.data.ratelimit import Clock, RateLimiter, RedisRateLimiter
from kbj.services.notifier.client import NotifyClient
from kbj.services.notifier.commands import default_registry
from kbj.services.notifier.format import split_numbered, split_text
from kbj.services.notifier.inbox import AllowList, InboxProcessor, norm_ids
from kbj.services.notifier.outbox import OutboundMessage, Outbox, OutboxEntry, RejectedRecord
from kbj.services.notifier.policy import (
    KindPolicy,
    NotifyConfig,
    Topic,
    UnknownKind,
    catch_up_deadline,
)
from kbj.services.notifier.store import (
    InboxStore,
    NotifyLogEntry,
    NotifyLogStore,
    NotifyStatus,
    StoredBody,
    rejected_key,
)
from kbj.services.notifier.telegram_api import SendResult, TelegramApi, TelegramApiError
from kbj.services.notifier.webhook import InboundQueue, InboundRouter
from kbj.services.runtime.health import HealthEvent, HealthSink, Severity
from kbj.store.redis_keys import NOTIFY_WEBHOOK_INFO

log = logging.getLogger(__name__)

SERVICE: Final = "notifier"
DISABLED_REASON: Final = "KBJ_NOTIFY_ENABLED=false"
_PARSE_ERROR = "can't parse entities"


@dataclass
class DrainStats:
    sent: int = 0
    suppressed: int = 0
    failed: int = 0
    retried: int = 0
    rejected: int = 0
    deferred: int = 0  # 기록 저장소 장애로 미룬 것


@dataclass
class _Retry:
    attempts: int = 0
    next_at: datetime | None = None
    # 이미 보낸 조각(묶음) 수 — 앞에서부터 센 절대 위치(`SendResult.parts_sent` 와 같은 뜻).
    # 더하지 않고 그 값으로 바꾼다: 이어 보낸 호출의 parts_sent 도 절대 위치라 더하면 건너뛴다.
    progress: int = 0
    message_ids: list[int] = field(default_factory=list[int])
    plain: bool = False  # 서식 오류로 평문 재발송 중
    began: bool = False  # 발송 기록 begin(2차 중복 검사)을 마쳤다
    # 끝냄(sent·failed) 기록 — 정해진 뒤 대기열 확인(ack)이 실패해도 다시 보내지 않고 이것만 쓴다
    final: tuple[NotifyLogEntry, StoredBody | None] | None = None


class NotifierService:
    """notifier 한 프로세스. 시계는 주입한다(시험·시뮬레이션)."""

    def __init__(
        self,
        outbox: Outbox,
        api: TelegramApi,
        config: NotifyConfig,
        store: NotifyLogStore,
        settings: Settings,
        *,
        now: Callable[[], datetime],
        health: HealthSink | None = None,
        redis: Redis | None = None,
        inbound: InboundQueue | None = None,
        router: InboundRouter | None = None,
        consumer: str = SERVICE,
    ) -> None:
        self.outbox = outbox
        self.api = api
        self.config = config
        self.store = store
        self.settings = settings
        self._now = now
        self._health = health
        self._redis = redis
        self.inbound = inbound
        self.router = router
        self.consumer = consumer
        self.stats = DrainStats()
        self._retry: dict[str, _Retry] = {}
        self._warned: set[str] = set()
        self._next_webhook_check: datetime | None = None
        self._last_webhook: dict[str, Any] | None = None
        # 텔레그램에 이미 나간 뒤 기록을 못 쓴 것 — 다시 보내지 않고 기록만 나중에(flush_records)
        self._unrecorded: list[tuple[NotifyLogEntry, StoredBody | None]] = []

    @property
    def enabled(self) -> bool:
        return self.settings.notify_enabled

    # ── health ──────────────────────────────────────────────────────────────────────
    def _emit(self, kind: str, detail: str, severity: Severity = "warning") -> None:
        if self._health is None:
            log.warning("%s: %s", kind, mask_text(detail))
            return
        try:
            self._health.emit(HealthEvent(kind, detail, self._now(), severity, SERVICE))
        except Exception as e:  # health 싱크 실패가 발송을 멈추지 않는다
            log.error("health 기록 실패: %s", type(e).__name__)

    def _emit_once(self, key: str, kind: str, detail: str, severity: Severity = "warning") -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        self._emit(kind, detail, severity)

    # ── 발송 기록 ────────────────────────────────────────────────────────────────────
    @staticmethod
    def _entry(
        m: OutboundMessage,
        policy: KindPolicy | None,
        status: NotifyStatus,
        reason: str,
        *,
        parts: int = 0,
        message_ids: tuple[int, ...] = (),
        sent_at: datetime | None = None,
    ) -> NotifyLogEntry:
        return NotifyLogEntry(
            kind=m.kind,
            topic=policy.topic.value if policy is not None else None,
            as_of=m.as_of,
            subject=m.subject,
            dedup_key=m.log_key,
            body_sha256=m.body_sha256,
            body_chars=m.body_chars,
            parts=parts,
            status=status,
            reason=mask_text(reason)[:500],
            source=m.source,
            requested_at=m.requested_at,
            message_ids=message_ids,
            sent_at=sent_at,
        )

    def _body(self, m: OutboundMessage) -> StoredBody:
        return StoredBody(
            dedup_key=m.log_key,
            body=m.text if m.text is not None else m.caption,
            parse_mode=m.parse_mode,
            created_at=self._now(),
        )

    def _record_rejected(self, r: RejectedRecord, reason: str, entry_id: str) -> None:
        try:
            topic = self.config.policy(r.kind).topic.value
        except UnknownKind:
            topic = None
        self.store.record_rejected(
            NotifyLogEntry(
                kind=r.kind,
                topic=topic,
                as_of=r.as_of,
                subject=r.subject,
                dedup_key=rejected_key(r.dedup_key, r.status, entry_id),
                body_sha256=r.body_sha256,
                body_chars=r.body_chars,
                parts=0,
                status=r.status,
                reason=reason,
                source=r.source,
                requested_at=r.requested_at,
            )
        )

    # ── 대기열 소비 ──────────────────────────────────────────────────────────────────
    def drain_once(self, n: int = 20) -> int:
        """대기열에서 최대 n 개를 처리한다. 처리(끝냄·재시도 예약)한 수."""
        now = self._now()
        self.flush_records()
        done = 0
        for e in self.outbox.read(self.consumer, n):
            st = self._retry.get(e.id)
            if st is not None and st.next_at is not None and now < st.next_at:
                continue  # 아직 재시도 때가 아니다
            try:
                self._handle(e, now)
            except Exception as exc:  # 기록 저장소 장애 등 — 보내지 않고 다음에 다시
                self.stats.deferred += 1
                st = self._retry.setdefault(e.id, _Retry())
                st.next_at = now + timedelta(seconds=self.config.delivery.retry_initial_s)
                self._emit(
                    "notify_store_failed",
                    f"발송 기록을 못 써 보내지 않고 미룸: {type(exc).__name__}",
                    "critical",
                )
                continue
            done += 1
        return done

    def _ack(self, entry_id: str) -> None:
        self.outbox.ack(entry_id)
        self._retry.pop(entry_id, None)

    def _finish_after_send(self, entry: NotifyLogEntry, body: StoredBody | None) -> None:
        """텔레그램을 부른 뒤의 기록 — 실패해도 다시 보내지 않게, 기록만 미뤄 둔다."""
        try:
            self.store.finish(entry, body)
        except Exception as e:
            self._unrecorded.append((entry, body))
            self._emit(
                "notify_store_failed",
                f"보낸 뒤 발송 기록을 못 씀(나중에 다시 기록): {type(e).__name__}",
                "critical",
            )

    def flush_records(self) -> int:
        """미뤄 둔 발송 기록을 쓴다. 쓴 수(실패한 것은 남긴다)."""
        left: list[tuple[NotifyLogEntry, StoredBody | None]] = []
        done = 0
        for entry, body in self._unrecorded:
            try:
                self.store.finish(entry, body)
                done += 1
            except Exception:
                left.append((entry, body))
        self._unrecorded = left
        return done

    def _handle(self, e: OutboxEntry, now: datetime) -> None:
        if e.error is not None:
            self.stats.failed += 1
            self._emit("notify_bad_entry", f"대기열 항목을 읽지 못해 버림: {e.error}")
            self._ack(e.id)
            return
        if e.rejected is not None:
            r = e.rejected
            self._record_rejected(
                r, f"{r.status}: 같은 키({r.dedup_key})가 이미 대기열·기록에 있다", e.id
            )
            self.stats.rejected += 1
            self._ack(e.id)
            return
        m = e.message
        if m is None:  # 형식상 메시지·거절·오류 중 하나 — 여기 올 일은 없다
            self._ack(e.id)
            return
        try:
            policy = self.config.policy(m.kind)
        except UnknownKind as exc:
            self.store.finish(self._entry(m, None, "failed", str(exc)))
            self.stats.failed += 1
            self._ack(e.id)
            return
        st = self._retry.setdefault(e.id, _Retry())
        if st.final is not None:
            # 이미 끝낸(보냈거나 실패로 정한) 항목 — 대기열 확인만 못 했다. 다시 보내지 않는다
            entry, body = st.final
            self._ack(e.id)
            self._finish_after_send(entry, body)
            return
        if not st.began and not self.store.begin(self._entry(m, policy, "queued", "")):
            # 2차 — Redis 문지기 키가 만료된 뒤 다시 들어온 같은 메시지
            self._record_rejected(
                RejectedRecord(
                    "duplicate",
                    m.kind,
                    m.source,
                    m.requested_at,
                    m.as_of,
                    m.dedup_key,
                    m.body_sha256,
                    m.body_chars,
                    m.subject,
                ),
                f"duplicate: 발송 기록에 같은 키({m.log_key})가 이미 있다(2차)",
                e.id,
            )
            self.stats.rejected += 1
            self._ack(e.id)
            return
        st.began = True
        deadline = catch_up_deadline(policy, m.as_of)
        if deadline is not None and now >= deadline:
            why = f"캐치업 시한({deadline:%Y-%m-%d %H:%M} KST)이 지나 보내지 않음"
            self.store.finish(self._entry(m, policy, "failed", why), self._body(m))
            self.stats.failed += 1
            self._ack(e.id)
            return
        try:
            chunks = self._chunks(m)
        except ValueError as exc:
            self._fail(e.id, m, policy, f"본문 분할 실패: {exc}", 0)
            return
        if len(chunks) > policy.max_parts:
            why = f"본문이 {len(chunks)}조각 — 상한 {policy.max_parts}(max_parts) 초과, 보내지 않음"
            self._fail(e.id, m, policy, why, len(chunks))
            return
        parts = len(chunks) if m.text is not None else max(1, len(m.attachments))
        if not self.enabled:
            # §5.5 — 정책·중복 판정까지 다 하고 텔레그램은 부르지 않는다
            self.store.finish(
                self._entry(m, policy, "suppressed", DISABLED_REASON, parts=parts), self._body(m)
            )
            self.stats.suppressed += 1
            self._ack(e.id)
            return
        self._send(e.id, m, policy, chunks, now)

    def _chunks(self, m: OutboundMessage) -> list[str]:
        if m.text is None:
            return []
        if m.numbered:
            return split_numbered(m.text, parse_mode=m.parse_mode)
        return split_text(m.text, parse_mode=m.parse_mode)

    def _destination(self, m: OutboundMessage, policy: KindPolicy) -> tuple[str | None, int | None]:
        if m.chat is not None:  # 받은 대화로 답한다(명령 응답)
            return m.chat, m.thread_id
        chat = self.settings.telegram_chat_id
        thread = m.thread_id if m.thread_id is not None else self.config.thread_for(policy.topic)
        if thread is None and policy.topic is not Topic.REPLY:
            self._emit_once(
                f"topic:{policy.topic.value}",
                "notify_topic_missing",
                f"토픽 {policy.topic.value} 의 thread id 가 notify.yaml 에 없다 — 일반 대화로 보냄",
            )
        return (chat.get_secret_value() if chat is not None else None), thread

    def _send(
        self,
        entry_id: str,
        m: OutboundMessage,
        policy: KindPolicy,
        chunks: list[str],
        now: datetime,
    ) -> None:
        if not self.api.has_token:
            self._emit_once(
                "token", "notify_token_missing", "KBJ_TELEGRAM_BOT_TOKEN 이 없다", "critical"
            )
        chat, thread = self._destination(m, policy)
        st = self._retry.setdefault(entry_id, _Retry())
        res = self._call_api(m, chunks, chat, thread, st)
        if (
            not res.ok
            and res.status == 400
            and _PARSE_ERROR in res.reason.lower()
            and m.text is not None
            and m.parse_mode
            and not st.plain
        ):
            # 서식 오류 — 같은 본문을 평문으로 한 번 더(SD earnings_telegram_sender 의 처리)
            st.plain = True
            st.progress = max(st.progress, res.parts_sent)
            st.message_ids.extend(res.message_ids)
            res = self._call_api(m, chunks, chat, thread, st)
        st.progress = max(st.progress, res.parts_sent)  # 절대 위치 — 더하지 않는다
        st.message_ids.extend(res.message_ids)
        parts = len(chunks) if m.text is not None else max(1, len(m.attachments))
        if res.ok:
            note = "평문으로 재발송(서식 오류)" if st.plain else res.reason
            self.stats.sent += 1
            st.final = (
                self._entry(
                    m,
                    policy,
                    "sent",
                    note,
                    parts=parts,
                    message_ids=tuple(st.message_ids),
                    sent_at=self._now(),
                ),
                self._body(m),
            )
            self._ack(entry_id)
            self._finish_after_send(*st.final)
            return
        st.attempts += 1
        if res.transient and st.attempts < self.config.delivery.max_attempts:
            delay = self.config.delivery.retry_delay_s(st.attempts)
            st.next_at = now + timedelta(seconds=delay)
            why = f"재시도 {st.attempts}/{self.config.delivery.max_attempts}: {res.reason}"
            self.stats.retried += 1
            self._finish_after_send(
                self._entry(
                    m, policy, "queued", why, parts=parts, message_ids=tuple(st.message_ids)
                ),
                None,
            )
            return
        self._fail(entry_id, m, policy, res.reason, parts)

    def _call_api(
        self,
        m: OutboundMessage,
        chunks: list[str],
        chat: str | None,
        thread: int | None,
        st: _Retry,
    ) -> SendResult:
        if m.text is not None:
            return self.api.send_text(
                chat,
                chunks,
                thread_id=thread,
                parse_mode=None if st.plain else m.parse_mode,
                silent=m.silent,
                start=st.progress,
            )
        if m.document is not None:
            return self.api.send_document(
                chat, m.document, m.caption, thread_id=thread, silent=m.silent
            )
        return self.api.send_media_group(
            chat, m.media, m.caption, thread_id=thread, silent=m.silent, start=st.progress
        )

    def _fail(
        self, entry_id: str, m: OutboundMessage, policy: KindPolicy, reason: str, parts: int
    ) -> None:
        st = self._retry.setdefault(entry_id, _Retry())
        ids = tuple(st.message_ids)
        try:
            self.outbox.release(m.dedup_key)  # 부른 쪽이 다시 넣을 수 있게
        except RedisError as e:
            log.warning("문지기 키 풀기 실패: %s", type(e).__name__)
        self.stats.failed += 1
        self._emit("notify_failed", f"{m.kind} 발송 실패({m.source}): {reason}")
        st.final = (
            self._entry(m, policy, "failed", reason, parts=parts, message_ids=ids),
            self._body(m),
        )
        self._ack(entry_id)
        self._finish_after_send(*st.final)

    # ── 받은 업데이트 ────────────────────────────────────────────────────────────────
    def process_inbound_once(self, n: int = 20) -> int:
        """웹훅이 넣은 업데이트를 갈래로. 처리한 수. 업데이트 하나의 실패는 그것만 남긴다."""
        if self.inbound is None or self.router is None:
            return 0
        done = 0
        for e in self.inbound.read(self.consumer, n):
            if e.update is None:
                self._emit("inbound_bad_entry", f"받은 업데이트를 읽지 못해 버림: {e.error}")
                self.inbound.ack(e.id)
                continue
            try:
                self.router.route(e.update)
            except Exception as exc:  # 저장소 장애 — 다음에 다시(텔레그램은 이미 200 을 받았다)
                self._emit("inbound_failed", f"받은 업데이트 처리 실패: {type(exc).__name__}")
                continue
            self.inbound.ack(e.id)
            done += 1
        return done

    # ── 웹훅 점검 ────────────────────────────────────────────────────────────────────
    def check_webhook(self) -> dict[str, Any]:
        """getWebhookInfo → `notify:webhook_info`. 꺼짐이면 부르지 않는다(외부 HTTP 0)."""
        now = self._now()
        info: dict[str, Any] = {"checked_at": now.astimezone(UTC).isoformat()}
        if not self.enabled:
            info.update(checked=False, url_set=None, error=None, note="발송 꺼짐 — 점검 생략")
        elif not self.api.has_token:
            info.update(checked=False, url_set=None, error="KBJ_TELEGRAM_BOT_TOKEN 이 없다")
        else:
            try:
                raw = self.api.get_webhook_info()
            except TelegramApiError as e:
                info.update(checked=True, url_set=None, error=e.reason)
                self._emit("webhook_check_failed", f"getWebhookInfo 실패: {e.reason}")
            else:
                url = str(raw.get("url") or "")
                pending = int(raw.get("pending_update_count") or 0)
                info.update(
                    checked=True,
                    url_set=bool(url),
                    host=urlsplit(url).hostname if url else None,
                    pending_update_count=pending,
                    last_error_date=raw.get("last_error_date"),
                    last_error_message=mask_text(str(raw.get("last_error_message") or ""))[:200]
                    or None,
                    allowed_updates=raw.get("allowed_updates"),
                    error=None,
                )
                if not url:
                    self._emit("webhook_missing", "웹훅이 걸려 있지 않다(setup-webhook 필요)")
                elif pending >= self.config.delivery.webhook_pending_warn:
                    self._emit("webhook_pending", f"대기 업데이트 {pending}건 — 수신이 밀린다")
        self._last_webhook = info
        if self._redis is not None:
            try:
                self._redis.set(NOTIFY_WEBHOOK_INFO, json.dumps(info, ensure_ascii=False))
            except RedisError as e:
                log.warning("웹훅 점검 기록 실패: %s", type(e).__name__)
        return info

    # ── 한 바퀴 ──────────────────────────────────────────────────────────────────────
    def step(self) -> dict[str, Any]:
        """대기열 → 받은 업데이트 → (때가 되면) 웹훅 점검. 한쪽 실패가 다른 쪽을 막지 않는다."""
        out: dict[str, Any] = {}
        for name, fn in (("outbox", self.drain_once), ("inbound", self.process_inbound_once)):
            try:
                out[name] = fn()
            except RedisError as e:
                out[name] = None
                self._emit(f"{name}_redis_failed", f"Redis 오류: {type(e).__name__}", "critical")
        now = self._now()
        if self._next_webhook_check is None or now >= self._next_webhook_check:
            self.check_webhook()
            self._next_webhook_check = now + timedelta(seconds=self.config.delivery.webhook_check_s)
        try:
            backlog = self.outbox.backlog()
        except RedisError:
            backlog = None
        if backlog is not None and backlog >= self.config.delivery.backlog_warn:
            self._emit("notify_backlog", f"발송 대기열 {backlog}건 — 밀린다")
        out["backlog"] = backlog
        return out

    def health(self) -> dict[str, Any]:
        """하트비트 status(값·본문 없음)."""
        return {
            "enabled": self.enabled,
            "sent": self.stats.sent,
            "suppressed": self.stats.suppressed,
            "failed": self.stats.failed,
            "retried": self.stats.retried,
            "rejected": self.stats.rejected,
            "deferred": self.stats.deferred,
            "webhook_url_set": (self._last_webhook or {}).get("url_set"),
        }


def run(
    service: NotifierService,
    stop: threading.Event,
    *,
    step_s: float = 1.0,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> int:
    """stop 이 설 때까지 step. 돈 바퀴 수. step 예외는 종류만 남기고 계속(죽지 않는다)."""
    if step_s < 0:
        raise ValueError("step_s 는 0 이상")
    n = 0
    while not stop.is_set():
        n += 1
        try:
            st = service.step()
            if on_step is not None:
                on_step(st)
        except Exception as e:
            log.error("notifier step 실패: %s", type(e).__name__)
        if stop.wait(step_s):
            break
    return n


# ── 조립 ────────────────────────────────────────────────────────────────────────────────────


def _inbox_ids(settings: Settings) -> list[int | str]:
    """인박스 대화 — `KBJ_TELEGRAM_INBOX_CHAT_IDS`, 없으면 `KBJ_TELEGRAM_CHAT_ID`(ET 와 같다)."""
    raw = settings.telegram_inbox_chat_ids or settings.telegram_chat_id
    return norm_ids(raw.get_secret_value()) if raw is not None else []


def _command_ids(settings: Settings) -> list[int | str]:
    """명령을 받을 대화 — `KBJ_TELEGRAM_CHAT_ID` + 인박스 대화(§5.6)."""
    ids: list[int | str] = []
    for raw in (settings.telegram_chat_id, settings.telegram_inbox_chat_ids):
        if raw is not None:
            ids += norm_ids(raw.get_secret_value())
    return ids


def build_service(
    settings: Settings,
    *,
    config: NotifyConfig,
    redis: Redis,
    log_store: NotifyLogStore,
    inbox_store: InboxStore,
    now: Callable[[], datetime],
    health: HealthSink | None = None,
    telegram_transport: httpx.BaseTransport | None = None,
    oembed_transport: httpx.BaseTransport | None = None,
    limiter_clock: Clock | None = None,
    sleep: Callable[[float], None] | None = None,
) -> NotifierService:
    """설정으로 notifier 를 조립한다(진입점·시험·시뮬레이션이 같은 길을 쓴다).

    발송 꺼짐(`KBJ_NOTIFY_ENABLED=false`)이면 oEmbed 클라이언트를 만들지 않는다 — 외부 HTTP 0.
    텔레그램 리미터는 config/limits.yaml `telegram`(전체 `rl:telegram:<토큰 해시>`, 대화당
    `per_chat_rate`). transport 는 시험이 가짜 서버를 끼우는 자리다.
    """
    outbox = Outbox(redis, now=now)
    client = NotifyClient(config, settings.notify_enabled, outbox=outbox, now=now)
    token = settings.telegram_bot_token
    tg = load_limits(settings=settings).source("telegram")
    limiter: RateLimiter | None = None
    chat_limiter: Callable[[str], RateLimiter | None] | None = None
    if token is not None and token.get_secret_value():
        base = tg.rate_config()
        limiter = RedisRateLimiter.scoped(
            redis, "telegram", token.get_secret_value(), base, limiter_clock
        )
        per_chat_rate = tg.per_chat_rate
        if per_chat_rate is not None:
            per_cfg = dataclasses.replace(
                base,
                rate=per_chat_rate,
                floor_rate=min(base.floor_rate, per_chat_rate),
                step_rate=min(base.step_rate, per_chat_rate),
            )
            cache: dict[str, RateLimiter] = {}

            def _per_chat(chat: str) -> RateLimiter:
                if chat not in cache:
                    cache[chat] = RedisRateLimiter.scoped(
                        redis, "telegram", f"chat:{chat}", per_cfg, limiter_clock
                    )
                return cache[chat]

            chat_limiter = _per_chat
    api = TelegramApi(
        token,
        transport=telegram_transport,
        limiter=limiter,
        chat_limiter=chat_limiter,
        sleep=sleep if sleep is not None else time.sleep,
        retry_after_max=tg.retry_after_max if tg.retry_after_max is not None else 4,
    )
    oembed = (
        httpx.Client(transport=oembed_transport, timeout=config.inbox.oembed_timeout_s)
        if settings.notify_enabled
        else None
    )
    inbox_ids = _inbox_ids(settings)
    inbox = (
        InboxProcessor(
            inbox_store,
            inbox_ids,
            now=now,
            oembed_client=oembed,
            keep_days=config.inbox.keep_days,
            oembed_timeout=config.inbox.oembed_timeout_s,
        )
        if inbox_ids
        else None
    )
    router = InboundRouter(
        commands=default_registry(),
        client=client,
        command_chats=AllowList.of(_command_ids(settings)),
        inbox=inbox,
    )
    return NotifierService(
        outbox,
        api,
        config,
        log_store,
        settings,
        now=now,
        health=health,
        redis=redis,
        inbound=InboundQueue(redis),
        router=router,
    )
