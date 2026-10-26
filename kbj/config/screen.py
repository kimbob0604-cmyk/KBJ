"""`config/screen.yaml` 해석기 — 종목 스크리너(페이지 11 '스크리너' 패널)의 문턱.

숫자는 이 파일 한 곳 — 엔진(`kbj.engines.screen`)은 이 값을 받는다. 모르는 키는 오류.
"""

from __future__ import annotations

from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from kbj.config.files import ConfigFileError, load_yaml_mapping
from kbj.config.settings import Settings

__all__ = [
    "SCREEN_FILE",
    "CisBacktestCfg",
    "CisCfg",
    "CisPlan",
    "CostsCfg",
    "ScreenConfig",
    "load_screen",
    "parse_screen",
]

SCREEN_FILE: Final = "screen.yaml"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CisPlan(_Model):
    """다음 날 계획 — 표에 함께 보이는 값(백테스트 기본값과 같다)."""

    stop_pct: float = Field(gt=0, lt=50)
    trail_pct: float = Field(gt=0, lt=50)
    gap_skip_pct: float = Field(gt=0, lt=50)
    add_at_pct: float = Field(gt=0, lt=100)
    risk_pct: float = Field(gt=0, le=10)
    max_positions: int = Field(ge=1, le=50)


class CisCfg(_Model):
    lookback_days: int = Field(ge=5, le=250)
    vol_mult: float = Field(gt=1)
    vol_max: float | None = Field(gt=1)  # 넘으면 '거래량 과열'(통과 빼기), None = 상한 없음
    min_turnover_won: int = Field(ge=0)
    min_avg_turnover_won: int = Field(ge=0)
    min_price: float = Field(ge=0)
    close_pos_min: float = Field(ge=0, le=1)
    limit_pct: float = Field(gt=0, le=30)
    regime_ma: int = Field(ge=2, le=250)
    index: dict[str, tuple[str, ...]]
    max_rows: int = Field(ge=1, le=500)
    plan: CisPlan


class CostsCfg(_Model):
    fee_bps: float = Field(ge=0)
    sell_tax_pct: float = Field(ge=0)
    slippage_bps: float = Field(ge=0)


class CisBacktestCfg(_Model):
    years: int = Field(ge=1, le=20)
    capital_won: int = Field(gt=0)
    max_weight_pct: float = Field(gt=0, le=100)
    add_frac: float = Field(ge=0, le=2)
    breakeven_at_pct: float = Field(gt=0)
    ma_exit_days: int = Field(ge=2, le=250)
    ma_exit_breaks: int = Field(ge=0, le=10)  # 0 = 이평 청산 끔
    time_stop_days: int = Field(ge=0, le=250)  # 0 = 시간 손절 끔
    time_stop_gain_pct: float = Field(ge=0)
    flat_band_pct: float = Field(ge=0, le=10)
    stop_mode: Literal["pct", "signal_low", "atr"]
    atr_days: int = Field(ge=2, le=100)
    atr_mult: float = Field(gt=0, le=10)
    entry_mode: Literal["open", "confirm", "close"]
    costs: CostsCfg


class ScreenConfig(_Model):
    cis: CisCfg
    cis_backtest: CisBacktestCfg


def parse_screen(data: dict[str, Any]) -> ScreenConfig:
    try:
        return ScreenConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigFileError(f"{SCREEN_FILE}: {e}") from None


def load_screen(*, settings: Settings | None = None) -> ScreenConfig:
    return parse_screen(load_yaml_mapping(SCREEN_FILE, settings=settings))
