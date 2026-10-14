"""백테스트·성과 응답·요청 모델(docs/p8_design.md §5 — `/api/backtest*`·`/api/rules/stats`, 로그인).
모양은 웨이브 1(FND)이 고정한다. 바꿔야 하면 FND 담당에게 요청 — BKT 는 리더·계산 본문만.

- 통계 칸은 `kbj.engines.backtest.stats`(거래 통계·기술 통계)와 같은 이름. n < 20 이면 `small`
  ('표본 적음' 흐림). 수익을 약속하는 문구 없음 — 봉투 notes 에 "과거 신호의 사후 수익 — 비용·체결
  미반영"(성과) / "표본 = 원장 보유 구간"(백테스트).
- 값이 없으면 None(0 으로 채우지 않는다).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from kbj.services.api.models.common import ApiModel

__all__ = [
    "BacktestIn",
    "BacktestMatch",
    "BacktestResult",
    "BacktestRunOut",
    "BacktestRuns",
    "BacktestSignalOut",
    "BacktestTrade",
    "CurvePoint",
    "DistBin",
    "MatchDiff",
    "MatchRowOut",
    "RuleStats",
    "RuleStatsRow",
    "StatsCell",
    "TradeStatsOut",
]

BacktestModeT = Literal["close", "intraday_replay"]


class StatsCell(ApiModel):
    """규칙 × 기간 한 칸(metrics §30.4). excluded = 정지·상폐·분할 의심으로 뺀 수."""

    horizon: int = Field(ge=1)
    n: int = Field(ge=0)
    excluded: int = Field(ge=0)
    win_rate: float | None
    mean: float | None
    median: float | None
    q25: float | None
    q75: float | None
    excess_mean: float | None
    mfe_median: float | None
    mae_median: float | None
    small: bool


class DistBin(ApiModel):
    lo: float
    hi: float
    n: int = Field(ge=0)


class RuleStatsRow(ApiModel):
    rule_id: str
    version: int | None  # None = 판 합침
    name: str | None
    cells: list[StatsCell]
    dist: list[DistBin]  # 요청한 기간(horizon)의 수익 분포


class RuleStats(ApiModel):
    horizons: list[int]
    rows: list[RuleStatsRow]


class BacktestIn(BaseModel):
    """즉석 백테스트 요청(D-P8-17 — 저장 없음, 상한 config/rules.yaml adhoc). 규칙은 `rule_id`(+ 판)
    또는
    `dsl` 하나."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str | None = Field(default=None, pattern=r"^r_[0-9a-z]{12}$")
    version: int | None = Field(default=None, ge=1)
    dsl: dict[str, Any] | None = None
    start: date
    end: date
    mode: BacktestModeT = "close"
    hold: int = Field(default=5, ge=1, le=120)
    stop_pct: float | None = Field(default=None, lt=0, ge=-50)
    target_pct: float | None = Field(default=None, gt=0, le=200)
    codes: list[str] | None = Field(default=None, max_length=100)


class TradeStatsOut(ApiModel):
    n: int = Field(ge=0)
    win_rate: float | None
    mean: float | None
    median: float | None
    p10: float | None
    p90: float | None
    profit_factor: float | None
    avg_bars: float | None
    mdd: float | None
    total: float | None
    annualized: float | None
    reasons: dict[str, int]


class CurvePoint(ApiModel):
    date: date
    equity: float


class BacktestTrade(ApiModel):
    code: str = Field(pattern=r"^[0-9A-Z]{6}$")
    signal_date: date
    entry_date: date
    entry_price: float
    exit_date: date
    exit_price: float
    reason: Literal["stop", "target", "hold", "open_end"]
    ret_pct: float
    bars: int = Field(ge=1)


class BacktestSignalOut(ApiModel):
    code: str = Field(pattern=r"^[0-9A-Z]{6}$")
    trade_date: date
    eval_ts: AwareDatetime
    mode: BacktestModeT
    entry_price: float | None
    rets: dict[str, float | None]
    status: Literal["sent", "cooldown", "suppressed", "dry", "skipped"]


class BacktestResult(ApiModel):
    rule_id: str | None
    version: int | None
    dsl_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    mode: BacktestModeT
    start: date
    end: date
    params: dict[str, Any]
    summary: TradeStatsOut
    outcome: list[StatsCell]  # 신호 성과(§4.5 와 같은 함수) — '백테스트 기대' 칸
    curve: list[CurvePoint]
    trades: list[BacktestTrade]
    signals: list[BacktestSignalOut]
    cached: bool


class BacktestRunOut(ApiModel):
    run_id: str = Field(pattern=r"^bt_[0-9A-HJKMNP-TV-Z]{26}$")
    rule_id: str
    version: int = Field(ge=1)
    mode: BacktestModeT
    start: date
    end: date
    summary: dict[str, Any]
    created_at: AwareDatetime


class BacktestRuns(ApiModel):
    runs: list[BacktestRunOut]


class MatchDiff(ApiModel):
    code: str = Field(pattern=r"^[0-9A-Z]{6}$")
    eval_ts: AwareDatetime
    side: Literal["live_only", "backtest_only"]
    reason: Literal["data_revised", "logic"]


class MatchRowOut(ApiModel):
    trade_date: date
    rule_id: str
    version: int = Field(ge=1)
    live_n: int = Field(ge=0)
    backtest_n: int = Field(ge=0)
    logic_n: int = Field(ge=0)
    diffs: list[MatchDiff]


class BacktestMatch(ApiModel):
    rows: list[MatchRowOut]
