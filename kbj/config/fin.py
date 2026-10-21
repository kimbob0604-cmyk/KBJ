"""`config/fin.yaml`·`config/reports.yaml` 해석기 — 공시·재무·컨센서스·밸류 튜닝값은 이 한 곳.

근거: docs/p4_design.md §3.7. `kbj/config/markets.py` 와 같은 방식이다.

- 모르는 키는 오류(`extra="forbid"` — 철자 틀린 기준값이 조용히 기본값으로 돌지 않게).
- 금액은 원 단위 정수(시총 경계 — `strict`), 비율·배수는 실수. 값의 근거·[확인 필요] 는 yaml 주석에
  있고
  여기는 형식·범위만 본다.
- 수집(F·R·K)·엔진(E·R·K·V)·API(A)·공개 내보내기(X)가 `load_fin()`·`load_reports()` 로 읽기만 한다.
  캐시하지 않는다 — 자주 읽는 쪽이 결과를 들고 있는다.
- 엔진의 계산 함수는 이 객체(또는 그 일부)를 **인자로 받는다**(엔진 순수성 — 계약 ⑨ 의 설명과 같다).
"""

from __future__ import annotations

import re
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from kbj.config.files import ConfigFileError, load_yaml_mapping
from kbj.config.settings import Settings

__all__ = [
    "FIN_FILE",
    "PERF_DAYS_STORED",
    "REPORTS_FILE",
    "REVISION_WINDOWS_STORED",
    "FinConfig",
    "ReportsConfig",
    "load_fin",
    "load_reports",
    "parse_fin",
    "parse_reports",
]

FIN_FILE: Final = "fin.yaml"
REPORTS_FILE: Final = "reports.yaml"
_CODE = re.compile(r"[0-9A-Z]{6}")
_HHMM = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")
# DB 가 받는 값(0012) — 설정은 이 안에서만 고를 수 있다(넓히려면 새 마이그레이션과 함께)
PERF_DAYS_STORED: Final = (3, 5, 7)  # prv_fin.earnings_surprise.perf_3d·perf_5d·perf_7d
REVISION_WINDOWS_STORED: Final = (7, 30)  # prv_fin.revision.window_days CHECK·RevisionRow


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ImportanceLevels(_Model):
    critical: int = Field(gt=0)
    high: int = Field(gt=0)
    medium: int = Field(gt=0)

    @model_validator(mode="after")
    def _order(self) -> ImportanceLevels:
        if not self.critical > self.high > self.medium:
            raise ValueError("critical > high > medium")
        return self

    def level(self, total: int) -> Literal["critical", "high", "medium", "low"]:
        """총점 → 등급(SD score_disclosure:9993 — 경계 포함)."""
        if total >= self.critical:
            return "critical"
        if total >= self.high:
            return "high"
        if total >= self.medium:
            return "medium"
        return "low"


class LoginBonus(_Model):
    watchlist: int = Field(ge=0)
    # [(시총 경계(원 — 초과), 가산)] — 큰 경계부터
    mktcap_krw: tuple[tuple[int, int], ...] = Field(min_length=1)

    @field_validator("mktcap_krw")
    @classmethod
    def _desc(cls, v: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
        bounds = [b for b, _ in v]
        if bounds != sorted(bounds, reverse=True) or len(set(bounds)) != len(bounds):
            raise ValueError("mktcap_krw 경계는 큰 것부터, 겹치지 않게")
        if any(isinstance(b, bool) or b <= 0 or s < 0 for b, s in v):
            raise ValueError("경계는 양의 원 단위 정수, 가산은 0 이상")
        return v

    def cap_bonus(self, mktcap: int | None) -> int:
        """전 거래일 회사 시총(원) → 가산(SD _cap_bonus_for_code:9946 — '초과'). 모르면 0."""
        if mktcap is None:
            return 0
        for bound, bonus in self.mktcap_krw:
            if mktcap > bound:
                return bonus
        return 0


class FeedCfg(_Model):
    max_pages: int = Field(ge=1, le=100)
    retry_parse_max: int = Field(ge=1, le=10)
    # n 번 실패한 건의 다음 시도 = 처음 본 시각 + retry_wait_min[n-1] 분(길이 = 상한 - 1)
    retry_wait_min: tuple[int, ...] = ()
    rescan_at: str
    # 공용 DART 일 예산을 이 몫 이상 쓰면 전날까지의 밀린 파싱은 다음 날로
    # (그날 목록·그날 공시 몫을 남긴다)
    backlog_budget_share: float = Field(default=1.0, gt=0, le=1)

    @model_validator(mode="after")
    def _waits(self) -> FeedCfg:
        w = self.retry_wait_min
        if len(w) != self.retry_parse_max - 1:
            raise ValueError(
                f"retry_wait_min 은 retry_parse_max - 1 개: {len(w)} != {self.retry_parse_max - 1}"
            )
        if any(x < 0 for x in w) or list(w) != sorted(w):
            raise ValueError(f"retry_wait_min 은 0 이상·커지는 순서: {w!r}")
        return self

    @field_validator("rescan_at")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not _HHMM.fullmatch(v):
            raise ValueError(f"rescan_at 은 'HH:MM': {v!r}")
        return v


class FilingsCfg(_Model):
    keywords: dict[str, int] = Field(min_length=1)
    importance_levels: ImportanceLevels
    login_bonus: LoginBonus
    feed: FeedCfg
    correction_prefixes: tuple[str, ...] = Field(min_length=1)

    @field_validator("keywords")
    @classmethod
    def _scores(cls, v: dict[str, int]) -> dict[str, int]:
        for kw, score in v.items():
            if not kw.strip() or isinstance(score, bool) or not 1 <= score <= 10:
                raise ValueError(f"키워드 점수는 1~10: {kw!r}={score!r}")
        return v


class EarningsThresholds(_Model):
    big_pct: float = Field(gt=0)
    standard_pct: float = Field(gt=0)
    inline_pct: float = Field(gt=0)
    turnaround_partial_ratio: float = Field(gt=0, lt=1)

    @model_validator(mode="after")
    def _order(self) -> EarningsThresholds:
        if not self.big_pct > self.standard_pct > self.inline_pct:
            raise ValueError("big > standard > inline")
        return self


class EarningsCfg(_Model):
    thresholds: EarningsThresholds
    alert_max_priority: int = Field(ge=1, le=9)
    perf_days: tuple[int, ...] = Field(min_length=1)
    yoy_reported_tol_pp: float = Field(ge=0)

    @field_validator("perf_days")
    @classmethod
    def _days(cls, v: tuple[int, ...]) -> tuple[int, ...]:
        if list(v) != sorted(set(v)) or v[0] < 1:
            raise ValueError("perf_days 는 1 이상, 오름차순, 겹치지 않게(거래일)")
        # 저장 칸이 perf_3d·perf_5d·perf_7d 셋뿐이다(0012) — 다른 날을 적으면 기록할 칸이 없다
        if not set(v) <= set(PERF_DAYS_STORED):
            raise ValueError(f"perf_days 는 {PERF_DAYS_STORED} 안에서만(저장 칸 — 0012)")
        return v


class SignalPct(_Model):
    strong_up: float
    up: float
    down: float
    strong_down: float

    @model_validator(mode="after")
    def _order(self) -> SignalPct:
        if not self.strong_up > self.up > 0 > self.down > self.strong_down:
            raise ValueError("strong_up > up > 0 > down > strong_down")
        return self

    def classify(self, pct: float) -> tuple[str, int]:
        """리비전 % → (신호, 우선순위) — SD classify_signal:76(경계는 위쪽 포함)."""
        if pct >= self.strong_up:
            return ("STRONG_UP", 1)
        if pct >= self.up:
            return ("UP", 2)
        if pct > self.down:
            return ("NEUTRAL", 3)
        if pct > self.strong_down:
            return ("DOWN", 2)
        return ("STRONG_DOWN", 1)


class ConsensusCfg(_Model):
    top_n: int = Field(ge=0)
    opinion_window_days: int = Field(ge=1)
    min_brokers_ok: int = Field(ge=1)
    revision_windows: tuple[int, ...] = Field(min_length=1)
    signal_pct: SignalPct
    alert_min_abs_pct: float = Field(ge=0)

    @field_validator("revision_windows")
    @classmethod
    def _windows(cls, v: tuple[int, ...]) -> tuple[int, ...]:
        # prv_fin.revision.window_days CHECK (7, 30)(0012)·RevisionRow 와 같은 범위 — 다른 창은
        # 쓰기에서 실패한다
        if list(v) != sorted(set(v)) or not set(v) <= set(REVISION_WINDOWS_STORED):
            raise ValueError(
                f"revision_windows 는 {REVISION_WINDOWS_STORED} 안에서 오름차순(0012 CHECK)"
            )
        return v


class ValuationCfg(_Model):
    band_days: int = Field(ge=20)
    min_days: int = Field(ge=1)
    caps: dict[Literal["per", "pbr", "psr", "ev_ebitda"], tuple[float, float]]
    financial_ksic_prefixes: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> ValuationCfg:
        if self.min_days > self.band_days:
            raise ValueError("min_days ≤ band_days")
        if set(self.caps) != {"per", "pbr", "psr", "ev_ebitda"}:
            raise ValueError("caps 는 per·pbr·psr·ev_ebitda 넷 다")
        for k, (lo, hi) in self.caps.items():
            if not lo < hi:
                raise ValueError(f"caps.{k}: 하한 < 상한")
        for p in self.financial_ksic_prefixes:
            if not re.fullmatch(r"[0-9]{2}", p):
                raise ValueError(f"KSIC 앞 두 자리: {p!r}")
        return self

    def in_cap(self, metric: Literal["per", "pbr", "psr", "ev_ebitda"], x: float) -> bool:
        """거름값 안(열린 구간 — SD 0 < x < 상한)."""
        lo, hi = self.caps[metric]
        return lo < x < hi


class ScenarioCfg(_Model):
    bear_eps_mult: float = Field(gt=0, lt=1)
    bull_eps_mult: float = Field(gt=1)


class OverhangCfg(_Model):
    lockup_months_third_party: int = Field(ge=1, le=60)


class DetailUniverseCfg(_Model):
    watchlist: bool
    consensus_top_n: bool
    public_demo: bool


class PublicExportCfg(_Model):
    fin_max_total_mb: float = Field(gt=0)


class BackfillCfg(_Model):
    from_year: int = Field(ge=2015, le=2100)  # DART 재무 API 는 2015 년부터 [확인 필요]
    tier: Literal["main", "full"]


class FinConfig(_Model):
    version: Literal[1]
    filings: FilingsCfg
    earnings: EarningsCfg
    consensus: ConsensusCfg
    valuation: ValuationCfg
    scenario: ScenarioCfg
    overhang: OverhangCfg
    detail_universe: DetailUniverseCfg
    public_export: PublicExportCfg
    backfill: BackfillCfg


class ReportsConfig(_Model):
    version: Literal[1]
    years_back: int = Field(ge=0, le=10)
    fs_div: Literal["CFS", "OFS"]
    public_demo: tuple[str, ...] = ()
    mapping: str = Field(min_length=1)
    keep_per_stock: int = Field(ge=1)
    xlsx_max_bytes: int = Field(gt=0, le=2 * 1024 * 1024)

    @field_validator("public_demo")
    @classmethod
    def _codes(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        for c in v:
            if not _CODE.fullmatch(c):
                raise ValueError(f"종목코드(^[0-9A-Z]{{6}}$): {c!r}")
        if len(set(v)) != len(v):
            raise ValueError("public_demo 가 겹친다")
        return v

    @field_validator("mapping")
    @classmethod
    def _rel(cls, v: str) -> str:
        if v.startswith("/") or ".." in v.split("/"):
            raise ValueError("mapping 은 config/ 안의 상대 경로")
        return v


def parse_fin(data: dict[str, Any]) -> FinConfig:
    try:
        return FinConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigFileError(f"{FIN_FILE}: {e}") from None


def parse_reports(data: dict[str, Any]) -> ReportsConfig:
    try:
        return ReportsConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigFileError(f"{REPORTS_FILE}: {e}") from None


def load_fin(*, settings: Settings | None = None) -> FinConfig:
    return parse_fin(load_yaml_mapping(FIN_FILE, settings=settings))


def load_reports(*, settings: Settings | None = None) -> ReportsConfig:
    return parse_reports(load_yaml_mapping(REPORTS_FILE, settings=settings))
