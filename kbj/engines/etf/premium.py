"""ETF 괴리율·경고(docs/metrics.md §4·§8.3, docs/p3_design.md §4.3).

- 괴리율(%) = (가격 ÷ NAV − 1) × 100.
- 마감: 종가·NAV(KRX 일별 — 원장 품질 그대로, 보통 ok). 장중: 현재가·iNAV(KIS — estimated).
- 경고: |괴리율| ≥ 기준(`config/markets.yaml` `etf.premium_warn_pct` — default·overseas·bond, 모두
  0.5% [확인 필요]). 해외주식 유형은 overseas, 채권·현금은 bond, 나머지는 default. **경계값(0.5%
  정확히)은 경고**다 — 나눗셈의 부동소수 끝자리 때문에 0.49999… 로 떨어지지 않게 괴리율을 소수
  9자리로 반올림한 뒤 비교한다.
- NAV 가 없거나 0 이하면 괴리율을 내지 않는다(None — 지어내지 않는다).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Literal, Protocol

from kbj.core.quality import Quality
from kbj.core.rows import EtfDay, EtfMeta, EtfQuote, EtfType

__all__ = [
    "PremiumRow",
    "Thresholds",
    "alerts",
    "premium_pct",
    "premium_rows",
    "threshold_for",
]

_ROUND: Final = 9


class Thresholds(Protocol):
    """`kbj.config.markets.PremiumCfg` 모양(엔진은 설정 모듈을 모른다)."""

    @property
    def default(self) -> float: ...

    @property
    def overseas(self) -> float: ...

    @property
    def bond(self) -> float: ...


@dataclass(frozen=True)
class PremiumRow:
    code: str
    as_of: date | datetime  # 마감은 거래일, 장중은 시각(시간대 있음)
    basis: Literal["close", "intraday"]
    price: float
    nav: float
    premium_pct: float
    threshold: float
    warn: bool
    source: str
    quality: Quality


def premium_pct(price: float, nav: float) -> float:
    """(가격 ÷ NAV − 1) × 100. NAV ≤ 0 이거나 가격 < 0 이면 ValueError."""
    if nav <= 0:
        raise ValueError(f"NAV 는 0 보다 커야 한다: {nav!r}")
    if price < 0:
        raise ValueError(f"가격은 0 이상: {price!r}")
    return round((price / nav - 1.0) * 100.0, _ROUND)


def threshold_for(kind: EtfType | None, thresholds: Thresholds) -> float:
    if kind is EtfType.OVERSEAS:
        return thresholds.overseas
    if kind is EtfType.BOND_CASH:
        return thresholds.bond
    return thresholds.default


def _row(
    code: str,
    as_of: date | datetime,
    basis: Literal["close", "intraday"],
    price: float | None,
    nav: float | None,
    source: str,
    quality: Quality,
    thr: float,
) -> PremiumRow | None:
    if price is None or nav is None or nav <= 0 or price < 0 or not quality.usable:
        return None
    p = premium_pct(price, nav)
    return PremiumRow(code, as_of, basis, price, nav, p, thr, abs(p) >= thr, source, quality)


def premium_rows(
    rows: Iterable[EtfDay | EtfQuote],
    thresholds: Thresholds,
    meta: Mapping[str, EtfMeta] | None = None,
) -> list[PremiumRow]:
    """행마다 괴리율(낼 수 없는 행은 뺀다). 일별은 종가·NAV, 장중은 현재가·iNAV.

    장중 행은 원천 품질과 무관하게 `estimated` 로 낮춘다(iNAV 는 추정치 — metrics §4 오류 4번)."""
    out: list[PremiumRow] = []
    for r in rows:
        m = (meta or {}).get(r.code)
        thr = threshold_for(None if m is None else m.etf_type, thresholds)
        if isinstance(r, EtfDay):
            row = _row(r.code, r.date, "close", r.close, r.nav, r.source, r.quality, thr)
        else:
            q = Quality.INVALID if r.quality is Quality.INVALID else Quality.ESTIMATED
            row = _row(r.code, r.ts, "intraday", r.price, r.inav, r.source, q, thr)
        if row is not None:
            out.append(row)
    return out


def alerts(
    rows: Iterable[EtfDay | EtfQuote],
    thresholds: Thresholds,
    meta: Mapping[str, EtfMeta] | None = None,
) -> list[PremiumRow]:
    """경고 행만 — |괴리율| 큰 순(같으면 코드 순)."""
    hits = [r for r in premium_rows(rows, thresholds, meta) if r.warn]
    return sorted(hits, key=lambda r: (-abs(r.premium_pct), r.code))
