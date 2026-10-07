"""발송 대기열 — Redis 스트림 `notify:outbox` + 중복 문지기 `notify:dedup:<키>`(설계 §5.1·§5.3).

흐름: 발송자(kbj 작업·legacy shim) → `Outbox.put` → notifier `Outbox.read`/`ack`.

- `put` 은 Lua 한 번으로 **원자적**: 문지기 키가 비었거나 지났으면 '언제까지'를 적고 메시지를
  XADD, 아직 살아 있으면 본문 없는 '거절 기록'(duplicate·cooldown)을 XADD 한다 — 부른 쪽은 그
  자리에서 `duplicate` 를 알고, notifier 는 거절도 발송 기록(`ops.notify_log`)에 남긴다. 부른
  프로세스는 DB 를 몰라도 된다.
- 첨부는 경로가 아니라 **바이트**로 싣는다(`file:<i>` 필드) — notifier 컨테이너가 부른 쪽의 파일을
  못 볼 수 있고, 넣은 순간의 내용을 보낸다. 상한은 notify.yaml `delivery.max_attachment_bytes`.
- 스트림에 MAXLEN 을 두지 않는다 — 소비하면(`ack`) XDEL 로 지우므로 쌓인 것은 아직 안 보낸 것뿐이고,
  잘라 버리면 조용히 잃는다. 쌓임은 notifier health(`notify_backlog`)가 알린다.
- 소비 그룹 `notifier`. 같은 소비자 이름으로 다시 뜨면 못 끝낸(ack 안 한) 것부터 다시 읽는다.
- Redis 오류는 그대로 올라간다(부른 쪽 shim 이 `(False, 사유)` 로 바꾼다 — 조용히 버리지 않는다).
- 본문(시세·포트폴리오가 섞일 수 있다)은 Redis 안에만 있고 로그에는 싣지 않는다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final, Literal

from redis import Redis
from redis.exceptions import ResponseError

from kbj.services.notifier.telegram_api import Attachment
from kbj.store.redis_keys import NOTIFY_OUTBOX, notify_dedup_key

GROUP: Final = "notifier"
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
FORMAT_VERSION: Final = "1"

RejectStatus = Literal["duplicate", "cooldown"]

# KEYS[1]=문지기 키 KEYS[2]=스트림 ARGV[1]=지금(µs, 주입한 시계) ARGV[2]=수명(ms)
# ARGV[3]=받을 때 필드 수 n, ARGV[4..3+n]=받을 때 필드·값, 나머지=거절 기록 필드·값.
# 문지기 값은 '언제까지'(µs) — 만료 판정은 주입한 시계로 한다(시험·시뮬레이션이 가짜 시계로 돈다).
# PX 수명은 같은 길이로 걸어 둔다(청소용 — 실제 운영에서는 둘이 같은 순간에 끝난다).
_PUT_LUA: Final = """
local now = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local held = redis.call('GET', KEYS[1])
local n = tonumber(ARGV[3])
local fields = {}
if (not held) or tonumber(held) <= now then
  redis.call('SET', KEYS[1], string.format('%d', now + ttl * 1000), 'PX', ttl)
  for i = 4, 3 + n do fields[#fields + 1] = ARGV[i] end
  return {1, redis.call('XADD', KEYS[2], '*', unpack(fields))}
end
for i = 4 + n, #ARGV do fields[#fields + 1] = ARGV[i] end
return {0, redis.call('XADD', KEYS[2], '*', unpack(fields))}
"""


@dataclass(frozen=True)
class OutboundMessage:
    """보낼 메시지 하나. 본문(text) 또는 첨부(document·media) 중 하나 이상.

    `dedup_key`(문지기)·`log_key`(발송 기록 유일 키)·`gate_ttl_s` 는 부른 쪽(client)이 정책으로
    채운다. `chat`·`thread_id` 는 명령 응답처럼 받은 대화로 답할 때만 — 없으면 기본 대화방 + 토픽.
    """

    kind: str
    source: str  # 부른 곳(legacy 함수·kbj 작업 이름) — 발송 기록에 남는다
    requested_at: datetime
    as_of: date
    dedup_key: str
    log_key: str
    gate_ttl_s: int
    body_sha256: str
    text: str | None = None
    caption: str = ""
    document: Attachment | None = None
    media: tuple[Attachment, ...] = ()
    subject: str | None = None
    chat: str | None = None
    thread_id: int | None = None
    parse_mode: str | None = "HTML"
    silent: bool = False
    numbered: bool = False

    def __post_init__(self) -> None:
        if self.requested_at.tzinfo is None or self.requested_at.utcoffset() is None:
            raise ValueError("naive datetime 금지")
        if self.text is None and self.document is None and not self.media:
            raise ValueError("본문도 첨부도 없다")
        if self.document is not None and self.media:
            raise ValueError("document 와 media 를 한 메시지에 같이 싣지 않는다")
        if self.gate_ttl_s <= 0:
            raise ValueError("gate_ttl_s 는 양수")

    @property
    def body_chars(self) -> int:
        return len(self.text) if self.text is not None else len(self.caption)

    @property
    def attachments(self) -> tuple[Attachment, ...]:
        return (self.document,) if self.document is not None else self.media

    def attachment_bytes(self) -> int:
        return sum(len(a.data) for a in self.attachments)


@dataclass(frozen=True)
class RejectedRecord:
    """문지기에 걸린 요청 — 본문 없이 메타만(발송 기록용)."""

    status: RejectStatus
    kind: str
    source: str
    requested_at: datetime
    as_of: date
    dedup_key: str
    body_sha256: str
    body_chars: int
    subject: str | None = None


@dataclass(frozen=True)
class NotifyTicket:
    """넣은 결과(설계 §1.6). ok=False 면 넣지 못했다(사유) — duplicate 는 ok=True(이미 있다)."""

    ok: bool
    id: str | None
    reason: str
    duplicate: bool = False
    dedup_key: str | None = None


@dataclass(frozen=True)
class OutboxEntry:
    """스트림 항목 하나 — 메시지 또는 거절 기록."""

    id: str
    message: OutboundMessage | None = None
    rejected: RejectedRecord | None = None
    error: str | None = None  # 형식이 깨진 항목(읽을 수 없음) — notifier 가 기록하고 지운다
    raw_kind: str | None = None


def _s(v: Any) -> str:
    return v.decode("utf-8") if isinstance(v, bytes) else str(v)


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat()


def _meta_of(m: OutboundMessage) -> dict[str, Any]:
    return {
        "kind": m.kind,
        "source": m.source,
        "requested_at": _iso(m.requested_at),
        "as_of": m.as_of.isoformat(),
        "dedup_key": m.dedup_key,
        "log_key": m.log_key,
        "gate_ttl_s": m.gate_ttl_s,
        "body_sha256": m.body_sha256,
        "text": m.text,
        "caption": m.caption,
        "subject": m.subject,
        "chat": m.chat,
        "thread_id": m.thread_id,
        "parse_mode": m.parse_mode,
        "silent": m.silent,
        "numbered": m.numbered,
        "document": m.document.name if m.document is not None else None,
        "media": [a.name for a in m.media],
    }


def _message_fields(m: OutboundMessage) -> list[str | bytes]:
    fields: list[str | bytes] = [
        "v",
        FORMAT_VERSION,
        "type",
        "message",
        "meta",
        json.dumps(_meta_of(m), ensure_ascii=False),
    ]
    for i, att in enumerate(m.attachments):
        fields += [f"file:{i}", att.data]
    return fields


def _rejected_fields(r: RejectedRecord) -> list[str | bytes]:
    meta = {
        "status": r.status,
        "kind": r.kind,
        "source": r.source,
        "requested_at": _iso(r.requested_at),
        "as_of": r.as_of.isoformat(),
        "dedup_key": r.dedup_key,
        "body_sha256": r.body_sha256,
        "body_chars": r.body_chars,
        "subject": r.subject,
    }
    return ["v", FORMAT_VERSION, "type", "rejected", "meta", json.dumps(meta, ensure_ascii=False)]


def _decode(entry_id: str, raw: Mapping[Any, Any]) -> OutboxEntry:
    fields = {_s(k): v for k, v in raw.items()}
    try:
        meta: dict[str, Any] = json.loads(_s(fields["meta"]))
        kind_s = _s(fields["type"])
        if kind_s == "rejected":
            return OutboxEntry(
                entry_id,
                rejected=RejectedRecord(
                    status=meta["status"],
                    kind=meta["kind"],
                    source=meta["source"],
                    requested_at=datetime.fromisoformat(meta["requested_at"]),
                    as_of=date.fromisoformat(meta["as_of"]),
                    dedup_key=meta["dedup_key"],
                    body_sha256=meta["body_sha256"],
                    body_chars=int(meta["body_chars"]),
                    subject=meta.get("subject"),
                ),
            )
        if kind_s != "message":
            raise ValueError(f"모르는 항목 종류 {kind_s!r}")
        names: list[str] = [meta["document"]] if meta["document"] else list(meta["media"])
        atts = [Attachment(name, bytes(fields[f"file:{i}"])) for i, name in enumerate(names)]
        msg = OutboundMessage(
            kind=meta["kind"],
            source=meta["source"],
            requested_at=datetime.fromisoformat(meta["requested_at"]),
            as_of=date.fromisoformat(meta["as_of"]),
            dedup_key=meta["dedup_key"],
            log_key=meta["log_key"],
            gate_ttl_s=int(meta["gate_ttl_s"]),
            body_sha256=meta["body_sha256"],
            text=meta["text"],
            caption=meta["caption"],
            document=atts[0] if meta["document"] else None,
            media=tuple(atts) if not meta["document"] else (),
            subject=meta["subject"],
            chat=meta["chat"],
            thread_id=meta["thread_id"],
            parse_mode=meta["parse_mode"],
            silent=bool(meta["silent"]),
            numbered=bool(meta["numbered"]),
        )
        return OutboxEntry(entry_id, message=msg)
    except (KeyError, ValueError, TypeError) as e:
        kind = None
        try:
            kind = str(json.loads(_s(fields.get("meta", b"{}"))).get("kind"))
        except (ValueError, AttributeError):
            kind = None
        return OutboxEntry(entry_id, error=f"항목 형식 오류: {type(e).__name__}", raw_kind=kind)


@dataclass
class Outbox:
    """`notify:outbox` 스트림과 중복 문지기."""

    redis: Redis
    now: Callable[[], datetime]
    stream: str = NOTIFY_OUTBOX
    group: str = GROUP
    _group_ready: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self._put = self.redis.register_script(_PUT_LUA)

    # ── 넣기 ─────────────────────────────────────────────────────────────────────────
    def put(
        self, msg: OutboundMessage, *, reject_status: RejectStatus = "duplicate"
    ) -> NotifyTicket:
        """문지기 키가 서면 메시지를, 아니면 거절 기록을 넣는다(원자적). Redis 오류는 올린다."""
        now = self.now()
        now_us = (now - _EPOCH) // timedelta(microseconds=1)
        rejected = RejectedRecord(
            status=reject_status,
            kind=msg.kind,
            source=msg.source,
            requested_at=msg.requested_at,
            as_of=msg.as_of,
            dedup_key=msg.dedup_key,
            body_sha256=msg.body_sha256,
            body_chars=msg.body_chars,
            subject=msg.subject,
        )
        accept = _message_fields(msg)
        args: list[str | bytes | int] = [
            now_us,
            msg.gate_ttl_s * 1000,
            len(accept),
            *accept,
            *_rejected_fields(rejected),
        ]
        res: Any = self._put(keys=[notify_dedup_key(msg.dedup_key), self.stream], args=args)
        accepted, entry_id = int(res[0]), _s(res[1])
        if accepted:
            return NotifyTicket(True, entry_id, "queued", dedup_key=msg.dedup_key)
        why = "cooldown" if reject_status == "cooldown" else "duplicate"
        return NotifyTicket(True, entry_id, why, duplicate=True, dedup_key=msg.dedup_key)

    def release(self, dedup_key: str) -> None:
        """문지기 키를 푼다 — 끝내 못 보낸(failed) 메시지를 부른 쪽이 다시 넣을 수 있게."""
        self.redis.delete(notify_dedup_key(dedup_key))

    # ── 읽기 ─────────────────────────────────────────────────────────────────────────
    def ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            # id 0 — 그룹이 생기기 전에 쌓인 것도 읽는다
            self.redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
        self._group_ready = True

    def read(self, consumer: str, n: int = 10) -> list[OutboxEntry]:
        """못 끝낸 내 항목(재시도 대기 포함)부터, 그다음 새 항목. 최대 n 개씩."""
        if n < 1:
            raise ValueError("n 은 1 이상")
        self.ensure_group()
        out: list[OutboxEntry] = []
        for start in ("0", ">"):
            res: Any = self.redis.xreadgroup(self.group, consumer, {self.stream: start}, count=n)
            for _stream, entries in res or []:
                for eid, raw in entries:
                    if raw:  # 지워진 항목은 (id, None/{}) 로 남는다
                        out.append(_decode(_s(eid), raw))
        return out

    def ack(self, entry_id: str) -> None:
        """끝낸 항목 — 그룹에서 확인하고 스트림에서 지운다(본문을 오래 두지 않는다)."""
        self.redis.xack(self.stream, self.group, entry_id)
        self.redis.xdel(self.stream, entry_id)

    def backlog(self) -> int:
        """스트림에 남은 항목 수(안 보낸 것 + 재시도 대기)."""
        n: Any = self.redis.xlen(self.stream)
        return int(n)
