"""규칙 응답·요청 모델(docs/p8_design.md §5 — `/api/rules/*`, 로그인). 모양은 웨이브 1(FND)이
고정한다. 바꿔야 하면 FND 담당에게 요청 — EVL 은 리더·쓰기 본문만.

- 규칙 본문은 DSL v1(`kbj.engines.rules.dsl` — JSON 스키마 `web/src/api/rule.schema.json`). 요청의
  `dsl` 은
  여기서 dict 로 받고 서버가 `validate_rule` 로 검사한다(422 는 위치·사유만 — 입력값 없음, D-P8-16).
- 값이 없으면 None(0 으로 채우지 않는다). 신호마다 quality, 봉투가 source·as_of.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from kbj.core.quality import Quality
from kbj.services.api.models.common import ApiModel

__all__ = [
    "CondOut",
    "RuleDetail",
    "RuleExample",
    "RuleExamples",
    "RuleIn",
    "RuleList",
    "RuleSummary",
    "RuleTrial",
    "RuleVersionOut",
    "RuleWriteResult",
    "SignalList",
    "SignalModeT",
    "SignalOut",
    "SignalStatusT",
    "ValidateIssue",
    "ValidateResult",
]

RuleModeT = Literal["close", "intraday", "both"]
SignalModeT = Literal["intraday", "close"]
SignalStatusT = Literal["sent", "cooldown", "suppressed", "dry", "skipped"]
StateT = Literal["true", "false", "unknown", "skipped"]


class RuleSummary(ApiModel):
    """규칙 목록 한 줄(페이지 11 ① — 이름·모드·켜짐·쿨다운·최근 신호·30일 신호 수)."""

    rule_id: str = Field(pattern=r"^r_[0-9a-z]{12}$")
    name: str
    mode: RuleModeT
    enabled: bool
    archived: bool
    current_version: int = Field(ge=1)
    cooldown_min: int = Field(ge=1)
    universe: dict[str, Any]
    signals_30d: int = Field(ge=0)
    last_signal_at: AwareDatetime | None
    source: Literal["api", "cli", "import:sd", "example"]
    updated_at: AwareDatetime


class RuleList(ApiModel):
    rules: list[RuleSummary]


class RuleVersionOut(ApiModel):
    version: int = Field(ge=1)
    dsl: dict[str, Any]
    dsl_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: AwareDatetime


class RuleDetail(ApiModel):
    rule: RuleSummary
    versions: list[RuleVersionOut]  # 판 오름차순(바꾸지 않는다 — 수정은 새 판)


class RuleIn(BaseModel):
    """규칙 만들기·새 판 요청 본문(64 KiB 상한 — `kbj.services.api.writes`)."""

    model_config = ConfigDict(extra="forbid")

    dsl: dict[str, Any]
    enabled: bool = False


class RuleWriteResult(ApiModel):
    rule_id: str = Field(pattern=r"^r_[0-9a-z]{12}$")
    version: int = Field(ge=1)
    dsl_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    enabled: bool
    archived: bool


class ValidateIssue(ApiModel):
    pointer: str  # JSON 포인터
    reason: str  # 사유 종류(입력값 없음)


class CondOut(ApiModel):
    """조건 하나의 평가(근거 — metrics §30.3). ok None = 판단 불가(장중 수급 없음)."""

    path: str
    ind: str
    field: str | None
    op: str
    ok: bool | None
    value: float | None
    ref_value: float | None
    prev_value: float | None
    prev_ref: float | None
    reason: str | None


class RuleTrial(ApiModel):
    """'오늘 데이터로 시험' 한 종목(저장 없음)."""

    code: str = Field(pattern=r"^[0-9A-Z]{6}$")
    name: str | None
    trade_date: date
    state: StateT
    skipped_reason: str | None
    conds: list[CondOut]
    quality: Quality


class ValidateResult(ApiModel):
    ok: bool
    issues: list[ValidateIssue]
    dsl_hash: str | None
    normalized: dict[str, Any] | None  # 기본값을 채운 DSL(ok 일 때)
    trial: list[RuleTrial]  # 오늘 원장으로 평가(최대 30종목 — 넘으면 notes)


class RuleExample(ApiModel):
    id: str
    name: str
    mode: RuleModeT
    dsl: dict[str, Any]
    dsl_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class RuleExamples(ApiModel):
    examples: list[RuleExample]


class SignalOut(ApiModel):
    """신호 이력 한 줄(페이지 11 ③). notify_* 는 `ops.notify_log` 결합(보낸 것만)."""

    signal_id: str
    mode: SignalModeT
    rule_id: str
    rule_name: str | None
    version: int = Field(ge=1)
    code: str = Field(pattern=r"^[0-9A-Z]{6}$")
    name: str | None
    eval_ts: AwareDatetime
    trade_date: date
    entry_price: float | None
    status: SignalStatusT
    skip_reason: str | None
    evidence: dict[str, Any]
    quality: Quality
    notify_status: str | None
    notified_at: AwareDatetime | None


class SignalList(ApiModel):
    signals: list[SignalOut]
