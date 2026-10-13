"""ETF 맵 — 셀(ETF 하나)·묶음·기간 수익률(docs/p5_design.md §4.4, metrics §19 — 로그인).

ET `monitor/kr/etf.py` 의 묶음·기간 계산만 승격했다(D-P5-13 ①):

- `build_items`:128 → `build_items` — 원장 행(KRX 일별 `prv_etf.etf_daily`) + 메타 → 항목. ET 는
  네이버
  목록·일봉을 받았지만 kbj 는 저장소 행(EtfDay·EtfMeta)을 받는다(네이버 스크래핑 금지). 기간
  수익률은 ET
  `engine.period_return` 규칙 그대로(기준일 = `dates[-1 - tdays]`, 그날 관측이 없으면 이전 가장
  가까운
  관측일, 기준일~오늘 사이 결측이 `GAP_LIMIT` 을 넘으면 None). 종가는 분할·병합 보정(metrics §8.2 —
  `prv_etf.split_event` 의 비율로 이전 종가를 나눈다).
- `filter_universe`:168 → `filter_universe` — 순자산 50억↑·20일 거래대금(중앙값) 1억↑, 무엇이 왜
  빠졌는지 센다(noData·small·illiquid).
- `build_groups`:183 → `build_groups` — 묶음 = 유형(metrics §8.1 7종) 또는 테마(`classify`). 테마
  묶음에는 국내주식(국내 대표지수·국내 테마)만 넣는다(ET '국내테마 묶음에 해외 ETF 가 안 들어온다').
  묶음 순서 = 구성 수 내림차순(같으면 이름), 셀 순서 = 순자산 내림차순.

분류는 정본 하나만 쓴다 — `kbj.engines.etf.types.classify`·`etf_type`(ET
`monitor/kr/etf.py:classify`
는 폐지 목록 — 두 번째 사본을 만들지 않는다). 메타에 저장된 유형·테마(수집 때 `typed_meta` 가
채움)가
있으면 그것을, 없으면 같은 정본 함수로 이름에서 읽는다.

크기 = 순자산(원, 마지막 확정일 — 보고 순자산이 없으면 상장좌수 × NAV), 색 = 기간 수익률(%).
레버리지·
인버스는 기본 숨김(`lev=False`), 신규 상장(이력이 기간보다 짧음)은 수익률 None + `new_listing`(회색
+
꼬리표 — 상장 뒤 수익률을 기간 수익률로 쓰지 않는다).
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal

from kbj.core.p5_rows import ETF_MAP_PERIODS, EtfMapCell
from kbj.core.quality import Quality
from kbj.core.rows import EtfDay, EtfMeta, EtfType, SplitEvent
from kbj.engines.etf.types import classify, etf_type
from kbj.engines.flows.ledger import worst_quality

__all__ = [
    "DOMESTIC_TYPES",
    "GAP_LIMIT",
    "MIN_NET_ASSET",
    "MIN_TURNOVER",
    "PERIOD_TDAYS",
    "TURNOVER_WINDOW",
    "WINDOW",
    "Excluded",
    "Group",
    "Item",
    "adjusted_closes",
    "build_groups",
    "build_items",
    "cells",
    "filter_universe",
    "period_return",
]

Period = Literal["1D", "1W", "1M", "3M"]
GroupBy = Literal["type", "theme"]

# ET engine.PERIODS 의 영업일 수(1D·1W·1M·3M 만 — 맵 기간).
PERIOD_TDAYS: Final[Mapping[str, int]] = {"1D": 1, "1W": 5, "1M": 21, "3M": 63}
# ET engine.GAP_LIMIT — 기준일~오늘 사이 결측 거래일이 이보다 많으면 수익률을 내지 않는다.
GAP_LIMIT: Final = 20
# ET monitor/kr/etf.py — 화면 문구 '순자산 50억↑ · 20일 거래대금 1억↑'.
MIN_NET_ASSET: Final = 5_000_000_000
MIN_TURNOVER: Final = 100_000_000
TURNOVER_WINDOW: Final = 20
# 저장소에서 읽을 창(거래일) — 3M(63) + 기준일 1 이 들어가고 20일 거래대금 창도 덮는다.
WINDOW: Final = max(PERIOD_TDAYS.values()) + 1
# 테마 묶음에 넣는 유형(국내주식만 — ET '국내테마' 묶음 규칙).
DOMESTIC_TYPES: Final[frozenset[EtfType]] = frozenset({EtfType.KR_INDEX, EtfType.KR_THEME})
NO_THEME: Final = "기타"

if set(PERIOD_TDAYS) != set(
    ETF_MAP_PERIODS
):  # 행 자료형(p5_rows)과 기간 목록이 어긋나면 import 에서 멈춘다
    raise RuntimeError("PERIOD_TDAYS 와 ETF_MAP_PERIODS 가 다르다")


@dataclass(frozen=True)
class Item:
    """ETF 하나(ET `build_items` 의 항목 — 맵에 필요한 칸만)."""

    code: str
    name: str | None
    etf_type: EtfType | None
    theme: str | None
    net_asset: int | None  # 원(마지막 확정일)
    turnover: float | None  # 원 — 최근 20거래일 거래대금 중앙값
    close: float | None  # 마지막 확정일 종가
    ret_pct: float | None
    leveraged: bool
    new_listing: bool
    asof: date
    source: str
    quality: Quality


@dataclass(frozen=True)
class Excluded:
    """유니버스에서 뺀 수(ET `filter_universe` 의 ex)."""

    no_data: int = 0
    small: int = 0
    illiquid: int = 0

    @property
    def total(self) -> int:
        return self.no_data + self.small + self.illiquid


@dataclass(frozen=True)
class Group:
    key: str
    label: str
    cells: tuple[EtfMapCell, ...]


def adjusted_closes(rows: Sequence[EtfDay], events: Iterable[SplitEvent]) -> dict[date, float]:
    """날짜 → 분할·병합 보정 종가(마지막 날 단위). 이벤트 날짜(효력일) **앞** 종가를 비율로 나눈다
    (`splits.adjust` 와 같은 방향 — ratio = 새 좌수 ÷ 옛 좌수). 종가가 없거나 0 이하인 날은 뺀다."""
    evs = sorted((e for e in events), key=lambda e: e.effective_date)
    out: dict[date, float] = {}
    for r in rows:
        if r.close is None or r.close <= 0 or r.quality is Quality.INVALID:
            continue
        f = 1.0
        for e in evs:
            if r.date < e.effective_date:
                f *= e.ratio
        out[r.date] = r.close / f
    return out


def period_return(closes: Mapping[date, float], dates: Sequence[date], tdays: int) -> float | None:
    """ET `engine.period_return` — `tdays` 영업일 전 대비 수익률(%). 못 내면 None.

    기준일은 `dates[-1 - tdays]`. 그날 관측이 없으면 이전으로 물러나 가장 가까운 관측일을 잡는다.
    기준일과 오늘 사이 결측 거래일이 `GAP_LIMIT` 을 넘으면 None(오래 멈춰 있던 값은 그 구간 성과가
    아니다)."""
    if tdays < 1:
        raise ValueError("tdays 는 1 이상")
    if len(dates) <= tdays:
        return None
    last = dates[-1]
    if last not in closes:
        return None
    i = len(dates) - 1 - tdays
    base_j = next((j for j in range(i, -1, -1) if dates[j] in closes), None)
    if base_j is None:
        return None
    span = dates[base_j:]
    if sum(1 for d in span if d not in closes) > GAP_LIMIT:
        return None
    base = closes[dates[base_j]]
    return (closes[last] / base - 1.0) * 100.0


def _type_of(m: EtfMeta | None, name: str | None, base_index: str | None) -> EtfType | None:
    if m is not None and m.etf_type is not None:
        return EtfType(m.etf_type)
    if name and name.strip():
        return etf_type(name, base_index)
    return None


def _theme_of(m: EtfMeta | None, name: str | None) -> str | None:
    if m is not None and m.theme is not None:
        return m.theme
    if name and name.strip():
        return classify(name)
    return None


def _leveraged(t: EtfType | None, m: EtfMeta | None) -> bool:
    if t is EtfType.LEVERAGED_INVERSE:
        return True
    lev = None if m is None else m.leverage
    return lev is not None and lev != 1.0


def _net_asset(r: EtfDay) -> int | None:
    if r.net_asset is not None:
        return r.net_asset
    if r.list_shrs is not None and r.nav is not None:
        return round(r.list_shrs * r.nav)
    return None


def build_items(
    meta: Mapping[str, EtfMeta],
    days: Mapping[str, Sequence[EtfDay]],
    dates: Sequence[date],
    period: Period,
    *,
    splits: Mapping[str, Sequence[SplitEvent]] | None = None,
) -> dict[str, Item]:
    """ET `build_items` — 원장 + 메타 → 항목(마지막 거래일에 행이 있는 ETF 만).

    `dates` = 창의 거래일(오름차순, 마지막 = 기준일). 수익률은 분할 보정 종가로 낸다."""
    if period not in PERIOD_TDAYS:
        raise ValueError(f"모르는 기간: {period!r}")
    if not dates:
        return {}
    dates = sorted(dates)
    tdays = PERIOD_TDAYS[period]
    asof = dates[-1]
    base_day = dates[-1 - tdays] if len(dates) > tdays else None
    out: dict[str, Item] = {}
    for code in sorted(days):
        rows = sorted((r for r in days[code] if r.date <= asof), key=lambda r: r.date)
        if not rows or rows[-1].date != asof:
            continue  # 기준일 행이 없다 — 상장폐지·정지(맵에 올리지 않는다)
        last = rows[-1]
        m = meta.get(code)
        name = (m.name if m is not None and m.name else None) or last.name
        t = _type_of(m, name, (m.base_index if m is not None else None) or last.base_index)
        closes = adjusted_closes(rows, (splits or {}).get(code, ()))
        first = rows[0].date if m is None or m.listed_on is None else m.listed_on
        new_listing = base_day is None or first > base_day
        ret = None if new_listing else period_return(closes, dates, tdays)
        turns = [float(r.turnover) for r in rows[-TURNOVER_WINDOW:] if r.turnover is not None]
        out[code] = Item(
            code=code,
            name=name,
            etf_type=t,
            theme=_theme_of(m, name),
            net_asset=_net_asset(last),
            turnover=statistics.median(turns) if turns else None,
            close=last.close,
            ret_pct=ret,
            leveraged=_leveraged(t, m),
            new_listing=new_listing,
            asof=asof,
            source=last.source,
            quality=worst_quality(r.quality for r in rows[-(tdays + 1) :]) or Quality.OK,
        )
    return out


def filter_universe(
    items: Mapping[str, Item],
    *,
    min_net_asset: int = MIN_NET_ASSET,
    min_turnover: int = MIN_TURNOVER,
) -> tuple[dict[str, Item], Excluded]:
    """ET `filter_universe` — 순자산 50억↑ · 20일 거래대금 1억↑. 무엇이 왜 빠졌는지 센다.

    ET 는 수익률이 없으면 noData 로 뺐지만, 맵은 신규 상장을 회색으로 보여야 하므로 수익률이 아니라
    종가·순자산이 없는 것만 noData 다."""
    no_data = small = illiquid = 0
    keep: dict[str, Item] = {}
    for c, x in items.items():
        if x.close is None or x.net_asset is None or x.quality is Quality.INVALID:
            no_data += 1
            continue
        if x.net_asset < min_net_asset:
            small += 1
            continue
        if (x.turnover or 0) < min_turnover:
            illiquid += 1
            continue
        keep[c] = x
    return keep, Excluded(no_data, small, illiquid)


def _cell(x: Item, period: Period) -> EtfMapCell:
    return EtfMapCell(
        code=x.code,
        name=x.name,
        etf_type=None if x.etf_type is None else x.etf_type.value,
        theme=x.theme,
        net_asset=x.net_asset,
        ret_pct=None if x.ret_pct is None else round(x.ret_pct, 4),
        period=period,
        leveraged=x.leveraged,
        new_listing=x.new_listing,
        asof=x.asof,
        source=x.source,
        quality=x.quality,
    )


def cells(
    meta: Mapping[str, EtfMeta],
    days: Mapping[str, Sequence[EtfDay]],
    dates: Sequence[date],
    period: Period,
    *,
    splits: Mapping[str, Sequence[SplitEvent]] | None = None,
    lev: bool = False,
) -> tuple[list[EtfMapCell], Excluded]:
    """맵 셀(유니버스 안 — 코드 순)과 뺀 수. `lev=False` 면 레버리지·인버스를 숨긴다(숨긴 것은 뺀
    수에
    세지 않는다 — 토글)."""
    items = build_items(meta, days, dates, period, splits=splits)
    keep, ex = filter_universe(items)
    out = [_cell(x, period) for c, x in sorted(keep.items()) if lev or not x.leveraged]
    return out, ex


def _type_label(v: str | None) -> tuple[str, str]:
    if v is None:
        return EtfType.OTHER.value, EtfType.OTHER.label
    t = EtfType(v)
    return t.value, t.label


def build_groups(cells_: Iterable[EtfMapCell], group: GroupBy) -> list[Group]:
    """ET `build_groups` — 유형 또는 테마 묶음. 구성이 없는 칸은 만들지 않는다.

    테마 묶음은 국내주식(국내 대표지수·국내 테마)만. 묶음 순서 = 구성 수 내림차순(같으면 키),
    셀 순서 = 순자산 내림차순(같으면 코드)."""
    if group not in ("type", "theme"):
        raise ValueError(f"모르는 묶음: {group!r}")
    buckets: dict[str, tuple[str, list[EtfMapCell]]] = {}
    for c in cells_:
        if group == "type":
            key, label = _type_label(c.etf_type)
        else:
            if c.etf_type is None or EtfType(c.etf_type) not in DOMESTIC_TYPES:
                continue
            key = label = c.theme or NO_THEME
        buckets.setdefault(key, (label, []))[1].append(c)
    out: list[Group] = []
    for key, (label, mem) in sorted(buckets.items(), key=lambda kv: (-len(kv[1][1]), kv[0])):
        mem.sort(key=lambda c: (-(c.net_asset or 0), c.code))
        out.append(Group(key, label, tuple(mem)))
    return out
