"""매크로 파생 지표(metrics §16 — 순수). 공개 입력만이면 공개, 로그인 입력이 하나라도 있으면 로그인.

- 장단기 금리차·신용스프레드(`spread`): 같은 기간(날짜)끼리만 a − b(%p). 한쪽이 없거나 값이 None
  이면
  그 기간은 None + 사유(0 으로 채우지 않는다). 날짜가 다르면 계산하지 않는다(맞춤 없음).
- 전년동월비·전월비·3개월 연율(`yoy`·`mom`·`ann3m`): 월(YYYY-MM)·분기(YYYY-Qn) 시리즈만. 비교 기간이
  없거나 값이 None·0 이면 None. 일별 시리즈는 ValueError(설정 오류).
- 실질 정책금리(`real_rate`): 달마다 정책금리(그달 마지막 값 — 일별이면 그달 마지막 날) − 같은 달
  CPI
  YoY. 어느 한쪽이 없으면 None.
- 최신값 변화(`latest_change`): 값이 있는 마지막 두 기간의 차·변화율(직전 공표 대비).
- 품질: 결과 점의 quality = 입력 점 quality 중 가장 나쁜 것(None 값이면 invalid).
- 반올림하지 않는다(metrics §0 — 화면에서만). 차(spread)는 Decimal 로 정확히 뺀 뒤 float.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Literal

from kbj.config.macro import DerivedCfg
from kbj.core.p5_rows import MacroSeriesRow
from kbj.core.quality import Quality

__all__ = [
    "DerivedPoint",
    "DerivedSeries",
    "Latest",
    "TierMixError",
    "ann3m",
    "derive",
    "latest_change",
    "mom",
    "real_rate",
    "spread",
    "yoy",
]

Tier = Literal["public", "private"]
_ORDER: Final = (Quality.OK, Quality.ESTIMATED, Quality.STALE, Quality.INVALID)
NA_MISSING: Final = "입력 없음"
NA_BASE: Final = "비교 기간 없음"


class TierMixError(ValueError):
    """공개 파생에 로그인 입력이 섞였다(metrics §16 — 등급 섞임 금지)."""


@dataclass(frozen=True)
class DerivedPoint:
    period: str
    value: float | None
    quality: Quality
    reason: str | None = None


@dataclass(frozen=True)
class DerivedSeries:
    series_id: str
    tier: Tier
    unit: str
    sources: tuple[str, ...]
    points: tuple[DerivedPoint, ...]
    na_reason: str | None = None  # 점이 하나도 없을 때의 사유


@dataclass(frozen=True)
class Latest:
    period: str
    value: float
    change: float | None
    change_pct: float | None
    prev_period: str | None


def _worst(qs: Sequence[Quality]) -> Quality:
    return max(qs, key=_ORDER.index) if qs else Quality.INVALID


def _by_period(rows: Sequence[MacroSeriesRow]) -> dict[str, MacroSeriesRow]:
    return {r.period: r for r in rows}


def spread(a: Sequence[MacroSeriesRow], b: Sequence[MacroSeriesRow]) -> list[DerivedPoint]:
    """a − b. 기간은 a 의 기간 순서(오름차순). b 에 없는 기간은 None + 사유."""
    bb = _by_period(b)
    out: list[DerivedPoint] = []
    for ra in sorted(a, key=lambda r: r.period):
        rb = bb.get(ra.period)
        if rb is None:
            out.append(DerivedPoint(ra.period, None, Quality.INVALID, f"{NA_MISSING}(뺄 값)"))
            continue
        if ra.value is None or rb.value is None:
            out.append(DerivedPoint(ra.period, None, Quality.INVALID, NA_MISSING))
            continue
        out.append(
            DerivedPoint(ra.period, float(ra.value - rb.value), _worst([ra.quality, rb.quality]))
        )
    return out


def _shift(period: str, months: int) -> str:
    """기간 문자열을 months 만큼 앞으로(월·분기만). 분기는 months 가 3 의 배수."""
    if "-Q" in period:
        y, q = int(period[:4]), int(period[-1])
        idx = y * 4 + (q - 1) - months // 3
        return f"{idx // 4:04d}-Q{idx % 4 + 1}"
    if len(period) == 7:
        y, m = int(period[:4]), int(period[5:7])
        idx = y * 12 + (m - 1) - months
        return f"{idx // 12:04d}-{idx % 12 + 1:02d}"
    raise ValueError(f"월·분기 시리즈만 비율을 계산한다: {period!r}")


def _ratio(rows: Sequence[MacroSeriesRow], months: int, power: int = 1) -> list[DerivedPoint]:
    by = _by_period(rows)
    out: list[DerivedPoint] = []
    for r in sorted(rows, key=lambda x: x.period):
        if "-Q" in r.period and months % 3:
            raise ValueError(f"분기 시리즈에 {months}개월 비교는 없다")
        base = by.get(_shift(r.period, months))
        if base is None:
            out.append(DerivedPoint(r.period, None, Quality.INVALID, NA_BASE))
            continue
        if r.value is None or base.value is None or base.value == 0:
            out.append(DerivedPoint(r.period, None, Quality.INVALID, NA_MISSING))
            continue
        g = float(r.value / base.value)
        v = (g**power - 1.0) * 100.0
        out.append(DerivedPoint(r.period, v, _worst([r.quality, base.quality])))
    return out


def yoy(rows: Sequence[MacroSeriesRow]) -> list[DerivedPoint]:
    """전년동월(분기)비 % — (x_t / x_{t−12} − 1) × 100."""
    return _ratio(rows, 12)


def mom(rows: Sequence[MacroSeriesRow]) -> list[DerivedPoint]:
    """전월비 % — (x_t / x_{t−1} − 1) × 100. 분기 시리즈는 전기비(3개월)."""
    if rows and "-Q" in rows[0].period:
        return _ratio(rows, 3)
    return _ratio(rows, 1)


def ann3m(rows: Sequence[MacroSeriesRow]) -> list[DerivedPoint]:
    """3개월 연율 % — ((x_t / x_{t−3})^4 − 1) × 100(계절조정 시리즈만 — 설정이 고른다)."""
    return _ratio(rows, 3, power=4)


def _month_last(rows: Sequence[MacroSeriesRow]) -> dict[str, MacroSeriesRow]:
    """일별·월별 → 달마다 값이 있는 마지막 행."""
    out: dict[str, MacroSeriesRow] = {}
    for r in sorted(rows, key=lambda x: x.period):
        if len(r.period) < 7 or "-Q" in r.period:
            raise ValueError(f"정책금리는 일별·월별 시리즈만: {r.period!r}")
        if r.value is not None:
            out[r.period[:7]] = r
    return out


def real_rate(rate: Sequence[MacroSeriesRow], cpi: Sequence[MacroSeriesRow]) -> list[DerivedPoint]:
    """달마다 정책금리 − CPI YoY(%p). CPI YoY 가 있는 달만 점을 만든다."""
    by_month = _month_last(rate)
    out: list[DerivedPoint] = []
    for p in yoy(cpi):
        r = by_month.get(p.period)
        if p.value is None or r is None or r.value is None:
            out.append(DerivedPoint(p.period, None, Quality.INVALID, p.reason or NA_MISSING))
            continue
        out.append(DerivedPoint(p.period, float(r.value) - p.value, _worst([p.quality, r.quality])))
    return out


def derive(
    cfg: DerivedCfg,
    inputs: Mapping[str, Sequence[MacroSeriesRow]],
    tiers: Mapping[str, Tier],
) -> DerivedSeries:
    """설정 한 줄(`config/macro.yaml derived`) → 파생 시리즈. 공개 파생에 로그인 입력이면
    TierMixError."""
    in_tiers = [tiers.get(i) for i in cfg.inputs]
    if any(t is None for t in in_tiers):
        raise ValueError(f"{cfg.id}: 입력 등급을 모른다 {list(cfg.inputs)}")
    want: Tier = "public" if all(t == "public" for t in in_tiers) else "private"
    if cfg.tier == "public" and want != "public":
        raise TierMixError(f"{cfg.id}: 공개 파생에 로그인 입력이 섞였다(metrics §16)")
    rows = [list(inputs.get(i, ())) for i in cfg.inputs]
    sources = tuple(dict.fromkeys(r.source for rs in rows for r in rs))
    if not all(rows):
        miss = [i for i, rs in zip(cfg.inputs, rows, strict=True) if not rs]
        return DerivedSeries(
            cfg.id, cfg.tier, cfg.unit, sources, (), f"{NA_MISSING}: {', '.join(miss)}"
        )
    if cfg.kind == "spread":
        if len(rows) != 2:
            raise ValueError(f"{cfg.id}: spread 는 입력 둘")
        pts = spread(rows[0], rows[1])
    elif cfg.kind == "real_rate":
        if len(rows) != 2:
            raise ValueError(f"{cfg.id}: real_rate 는 입력 둘(정책금리, CPI)")
        pts = real_rate(rows[0], rows[1])
    else:
        if len(rows) != 1:
            raise ValueError(f"{cfg.id}: {cfg.kind} 는 입력 하나")
        pts = {"yoy": yoy, "mom": mom, "ann3m": ann3m}[cfg.kind](rows[0])
    return DerivedSeries(cfg.id, cfg.tier, cfg.unit, sources, tuple(pts))


def latest_change(points: Sequence[tuple[str, float | Decimal | None]]) -> Latest | None:
    """(기간, 값) 오름차순 → 값이 있는 마지막 기간과 그 직전 기간 대비 차·변화율. 없으면 None."""
    have = [(p, float(v)) for p, v in sorted(points, key=lambda x: x[0]) if v is not None]
    if not have:
        return None
    p, v = have[-1]
    if len(have) == 1:
        return Latest(p, v, None, None, None)
    pp, pv = have[-2]
    pct = (v / pv - 1.0) * 100.0 if pv != 0 else None
    return Latest(p, v, v - pv, pct, pp)
