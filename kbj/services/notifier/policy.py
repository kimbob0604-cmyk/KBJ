"""발송 규칙 — 토픽·메시지 종류·중복 방지 키·쿨다운(설계 §5.2·§5.3, `config/notify.yaml`).

- `config/notify.yaml` 의 유일한 해석기다. 모르는 키는 오류(`extra="forbid"` — 철자 틀린 규칙이
  조용히 무시되지 않게). 다른 묶음은 `load_notify_config()` 로 읽기만 한다.
- 토픽 4개(inventory d-2): 시장·신고가·알림·운영 + 명령 응답용 `reply`(받은 대화·스레드로 답한다).
  토픽 thread id 는 슈퍼그룹을 만든 뒤 yaml 에 적는다 **[확인 필요]** — 비어 있으면 일반 대화로
  보내고 경고(R16). chat id 는 비밀 취급이라 yaml 에 두지 않는다(`KBJ_TELEGRAM_CHAT_ID`).

중복 방지 키(§5.3). `dedup_key()` 가 만든 키는 Redis 문지기(`notify:dedup:<키>`, SET NX — 넣는 순간
호출자가 `duplicate` 를 안다)와 발송 기록 `ops.notify_log.dedup_key`(UNIQUE — 2차) 에 같이 쓴다.

| 규칙(`dedup`) | 문지기 키 | 수명 |
|---|---|---|
| `daily` 하루 1회 | `{kind}:{as_of:%Y%m%d}`(+`:{subject}`)(+`:{origin}`) | 36시간 |
| `cooldown` | `{kind}:{subject}` — **미끄러지는 창**(마지막 발송부터 `cooldown_s`) | `cooldown_s` |
| `subject` 대상별 1회 | `{kind}:{subject}` | 30일 |
| `body` 그 밖 | `{kind}:{as_of:%Y%m%d}:{sha256(본문)[:16]}` | 24시간 |

- 쿨다운: 설계 표의 `{kind}:{subject}:{floor(now / cooldown_s)}` 를 문지기로 쓰면 창 경계
  (10:59·11:01)에서 2분 만에 두 번 나간다. 그래서 문지기는 `{kind}:{subject}`(수명 `cooldown_s`)로
  두고, `floor` 붙은 키는 발송 기록의 유일 키(`log_key`)로만 쓴다 — 두 번 받아들인 시각은 늘
  `cooldown_s` 이상 떨어져 있으므로 기록 키가 겹치지 않는다.
- subject 가 없으면(`cooldown`·`subject` 규칙) 본문 해시를 대상으로 본다 — legacy 처럼 대상을 모르는
  호출이 서로 다른 알림을 한 쿨다운에 묶어 버리지 않게(SD `_alert_cooldown_ok` 와 같은 뜻).
- `origin`(legacy shim 이 넣는 '부른 곳') — 전환 기간에 legacy 의 여러 함수가 같은 종류로 매겨져도
  (§5.9 `legacy_kinds`) 함수마다 하루 1회로 센다. kind 를 넘긴 legacy 호출(한 번에 여러 통 — ETF
  리포트·수급 종목별 차트)은 origin 에 내용 해시가 붙어 같은 내용의 되풀이만 막는다(client
  `_legacy_origin`). kbj 작업은 origin 없이 부른다 → 종류당 하루 1회(U2).
- 키 조각에는 공백이 없다(Redis 키 규칙 — `kbj.store.redis_keys`). chat id 는 키에 넣지 않는다.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from datetime import date, datetime, time
from enum import StrEnum
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from kbj.config.files import ConfigFileError, load_yaml_mapping
from kbj.config.settings import Settings
from kbj.core.time import KST

NOTIFY_FILE: Final = "notify.yaml"

DAILY_TTL_S: Final = 36 * 3600
SUBJECT_TTL_S: Final = 30 * 86400
BODY_TTL_S: Final = 24 * 3600
SUBJECT_MAX: Final = 64  # 이보다 긴 대상은 sha256 앞 16자로

DedupMode = Literal["daily", "cooldown", "subject", "body"]
ParseMode = Literal["HTML", "Markdown", "MarkdownV2"]
PARSE_MODES: Final[tuple[str, ...]] = ("HTML", "Markdown", "MarkdownV2")

_KIND = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+")
_FUNC = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SPACE = re.compile(r"\s+")


class Topic(StrEnum):
    """슈퍼그룹 토픽(inventory d-2) + 명령 응답."""

    MARKET = "market"  # 시장 — 아침 브리핑·마감 요약·수급 리포트·ETF 리포트
    NEWHIGH = "newhigh"  # 신고가 — 보드 엑셀·노트
    ALERT = "alert"  # 알림 — 규칙·관심종목·트레일링·어닝·리비전·GEX 레벨
    OPS = "ops"  # 운영 — 워치독·작업 실패·헬스·유니버스 변동
    REPLY = "reply"  # 명령 응답 — 받은 대화·스레드(토픽 id 없음)


TOPIC_LABELS: Final[Mapping[Topic, str]] = {
    Topic.MARKET: "시장",
    Topic.NEWHIGH: "신고가",
    Topic.ALERT: "알림",
    Topic.OPS: "운영",
    Topic.REPLY: "응답",
}


class NotifyConfigError(ConfigFileError):
    """notify.yaml 내용 오류."""


class UnknownKind(KeyError):
    """notify.yaml `kinds` 에 없는 메시지 종류."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind

    def __str__(self) -> str:
        return f"모르는 알림 종류 {self.kind!r} — config/notify.yaml kinds 에 없다"


class KindPolicy(BaseModel):
    """메시지 종류 하나의 규칙."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    topic: Topic
    dedup: DedupMode
    cooldown_s: int | None = Field(default=None, gt=0)
    ttl_s: int | None = Field(default=None, gt=0)  # 문지기 수명을 바꿀 때만(기본은 위 표)
    parse_mode: ParseMode | None = "HTML"
    silent: bool = False
    max_parts: int = Field(default=10, ge=1, le=50)
    # 이 시각(KST, as_of 날짜)이 지나면 보내지 않는다 — 늦은 마감 요약이 다음 날 아침에 가지 않게
    catch_up_until: time | None = None
    note: str = ""

    @field_validator("kind")
    @classmethod
    def _kind_name(cls, v: str) -> str:
        if not _KIND.fullmatch(v):
            raise ValueError(f"종류 이름은 '영역.이름'(소문자) 이어야 한다: {v!r}")
        return v

    @field_validator("catch_up_until", mode="before")
    @classmethod
    def _hhmm(cls, v: object) -> object:
        if isinstance(v, int) and not isinstance(v, bool):  # yaml 이 20:30 을 분(1230)으로 읽는다
            return time(v // 60, v % 60)
        return v

    @model_validator(mode="after")
    def _cooldown_needs_seconds(self) -> KindPolicy:
        if self.dedup == "cooldown" and self.cooldown_s is None:
            raise ValueError(f"{self.kind}: cooldown 규칙에는 cooldown_s 가 필요하다")
        if self.dedup != "cooldown" and self.cooldown_s is not None:
            raise ValueError(f"{self.kind}: cooldown_s 는 cooldown 규칙에서만 쓴다")
        return self

    def _cooldown(self) -> int:
        if self.cooldown_s is None:  # 검증자가 막지만, 타입상 한 번 더
            raise ValueError(f"{self.kind}: cooldown_s 가 없다")
        return self.cooldown_s

    @property
    def once_per_day(self) -> bool:
        return self.dedup == "daily"

    def gate_ttl_s(self) -> int:
        """Redis 문지기 키 수명(초)."""
        if self.ttl_s is not None:
            return self.ttl_s
        if self.dedup == "daily":
            return DAILY_TTL_S
        if self.dedup == "cooldown":
            return self._cooldown()
        if self.dedup == "subject":
            return SUBJECT_TTL_S
        return BODY_TTL_S

    def expired(self, as_of: date, now: datetime) -> bool:
        """`catch_up_until` 이 지났는가(as_of 날짜의 그 시각, KST)."""
        if self.catch_up_until is None:
            return False
        deadline = datetime.combine(as_of, self.catch_up_until, tzinfo=KST)
        return now >= deadline


class Delivery(BaseModel):
    """발송 재시도·첨부 상한·점검 주기."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_initial_s: float = Field(default=10.0, gt=0)
    retry_max_s: float = Field(default=300.0, gt=0)
    max_attachment_bytes: int = Field(default=20 * 1024 * 1024, gt=0)
    backlog_warn: int = Field(default=500, gt=0)
    webhook_check_s: float = Field(default=600.0, gt=0)
    webhook_pending_warn: int = Field(default=100, gt=0)

    @model_validator(mode="after")
    def _order(self) -> Delivery:
        if self.retry_max_s < self.retry_initial_s:
            raise ValueError("retry_max_s 는 retry_initial_s 이상")
        return self

    def retry_delay_s(self, attempt: int) -> float:
        """attempt 번째 실패 뒤 기다릴 초(두 배씩, 상한)."""
        return min(self.retry_initial_s * (2 ** max(attempt - 1, 0)), self.retry_max_s)


class InboxConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    keep_days: int = Field(default=14, ge=1)  # ET KEEP_DAYS
    oembed_timeout_s: float = Field(default=20.0, gt=0)


class NotifyConfig(BaseModel):
    """notify.yaml 전체."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1]
    topics: dict[Topic, int | None]
    kinds: dict[str, KindPolicy]
    legacy_kinds: dict[str, str] = Field(default_factory=dict[str, str])
    legacy_default_kind: str
    delivery: Delivery = Delivery()
    inbox: InboxConfig = InboxConfig()

    @model_validator(mode="after")
    def _consistent(self) -> NotifyConfig:
        if Topic.REPLY in self.topics:
            raise ValueError("topics 에 reply 를 두지 않는다(받은 대화로 답한다)")
        missing = [t.value for t in Topic if t is not Topic.REPLY and t not in self.topics]
        if missing:
            raise ValueError(f"topics 가 빠졌다: {', '.join(missing)}")
        for name, pol in self.kinds.items():
            if name != pol.kind:
                raise ValueError(f"kinds.{name}: kind 값이 키와 다르다({pol.kind})")
        for fn, kind in self.legacy_kinds.items():
            if not _FUNC.fullmatch(fn):
                raise ValueError(f"legacy_kinds: 함수 이름이 아니다: {fn!r}")
            if kind not in self.kinds:
                raise ValueError(f"legacy_kinds.{fn}: 모르는 종류 {kind!r}")
        if self.legacy_default_kind not in self.kinds:
            raise ValueError(f"legacy_default_kind: 모르는 종류 {self.legacy_default_kind!r}")
        return self

    def policy(self, kind: str) -> KindPolicy:
        try:
            return self.kinds[kind]
        except KeyError:
            raise UnknownKind(kind) from None

    def thread_for(self, topic: Topic) -> int | None:
        """토픽의 message_thread_id. 응답·미설정은 None(일반 대화)."""
        if topic is Topic.REPLY:
            return None
        return self.topics.get(topic)


def _kinds_with_names(raw: object) -> object:
    """yaml 은 `kinds: {brief.morning: {...}}` 로 쓴다 — 키를 kind 필드로 넣어 준다."""
    if not isinstance(raw, dict):
        return raw
    out: dict[Any, Any] = {}
    for k, v in raw.items():  # pyright: ignore[reportUnknownVariableType]
        out[k] = {"kind": k, **v} if isinstance(v, dict) and "kind" not in v else v
    return out


def parse_notify_config(data: Mapping[str, Any]) -> NotifyConfig:
    """매핑(yaml 을 읽은 것) → `NotifyConfig`. 형식 오류는 `NotifyConfigError`."""
    body = dict(data)
    body["kinds"] = _kinds_with_names(body.get("kinds"))
    if body.get("topics") is None:
        body["topics"] = {}
    try:
        return NotifyConfig.model_validate(body)
    except (ValidationError, ValueError) as e:
        raise NotifyConfigError(f"{NOTIFY_FILE}: {e}") from None


def load_notify_config(*, settings: Settings | None = None) -> NotifyConfig:
    """`config/notify.yaml` 을 읽는다(캐시하지 않는다 — 서비스는 시작할 때 한 번 읽어 든다)."""
    return parse_notify_config(load_yaml_mapping(NOTIFY_FILE, settings=settings))


def load_policies(*, settings: Settings | None = None) -> dict[str, KindPolicy]:
    """종류 → 규칙(설계 §1.6 의 이름)."""
    return dict(load_notify_config(settings=settings).kinds)


# ── 중복 방지 키 ─────────────────────────────────────────────────────────────────────────────


def sha16(data: str | bytes) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()[:16]


def key_part(value: str) -> str:
    """키 조각 — 공백은 `_`, 너무 길면 해시. 빈 값은 `-`."""
    s = _SPACE.sub("_", value.strip())
    if not s:
        return "-"
    return s if len(s) <= SUBJECT_MAX else "h" + sha16(s)


def dedup_key(
    policy: KindPolicy,
    *,
    as_of: date,
    subject: str | None,
    body_sha256: str,
    now: datetime,
    origin: str | None = None,
) -> tuple[str, str]:
    """(문지기 키, 발송 기록 키). 둘은 `cooldown` 에서만 다르다(머리말 표).

    body_sha256 = 본문(첨부는 캡션 + 파일 해시)의 sha256 16진수.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("naive datetime 금지")
    if not body_sha256:
        raise ValueError("body_sha256 이 비었다")
    kind = policy.kind
    day = f"{as_of:%Y%m%d}"
    subj = key_part(subject) if subject is not None else None
    if policy.dedup == "daily":
        parts = [kind, day]
        if subj is not None:
            parts.append(subj)
        if origin:
            parts.append(key_part(origin))
        key = ":".join(parts)
        return key, key
    if policy.dedup == "body":
        key = f"{kind}:{day}:{body_sha256[:16]}"
        return key, key
    target = subj if subj is not None else "b" + body_sha256[:16]
    gate = f"{kind}:{target}"
    if policy.dedup == "subject":
        return gate, gate
    bucket = math.floor(now.timestamp() / policy._cooldown())  # pyright: ignore[reportPrivateUsage]
    return gate, f"{gate}:{bucket}"


def default_as_of(now: datetime) -> date:
    """as_of 를 주지 않은 호출의 기준일 — 그 순간의 KST 날짜."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("naive datetime 금지")
    return now.astimezone(KST).date()


def catch_up_deadline(policy: KindPolicy, as_of: date) -> datetime | None:
    if policy.catch_up_until is None:
        return None
    return datetime.combine(as_of, policy.catch_up_until, tzinfo=KST)


__all__ = [
    "BODY_TTL_S",
    "DAILY_TTL_S",
    "NOTIFY_FILE",
    "PARSE_MODES",
    "SUBJECT_TTL_S",
    "TOPIC_LABELS",
    "DedupMode",
    "Delivery",
    "InboxConfig",
    "KindPolicy",
    "NotifyConfig",
    "NotifyConfigError",
    "ParseMode",
    "Topic",
    "UnknownKind",
    "catch_up_deadline",
    "dedup_key",
    "default_as_of",
    "key_part",
    "load_notify_config",
    "load_policies",
    "parse_notify_config",
    "sha16",
]
