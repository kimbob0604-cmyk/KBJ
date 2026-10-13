"""수출입 탐색 엔진(순수 — 계약 ⑨, docs/p6_design.md §4.2, metrics §26).

관세청 월간 품목 응답은 **자릿수가 섞여** 온다(같은 달에 HS2·4·6·10 행이 함께 — 상위 행은 하위의
합). 이 모듈은 저장된 행만으로 접두(HS 2~10 짝수 자리) 값을 낸다.

- **접두 값(이중 합산 금지)**: 접두 P 의 그 달 값 = P 행이 있으면 그 행, 없으면 P 바로 아래 접두
  (P + 2자리)들의 값의 합(재귀 — 그 아래도 같은 규칙). 같은 달에 상위·하위 행이 함께 오면 **둘을
  더하지 않는다**. 상위 행이 있는데 하위 행이 빠진 달(응답 누락 — p6_world 390120 2026-06)은 상위
  행이 그 접두의 값이다(하위만 더하면 빠진 만큼 작아진다). 상위 행이 없을 때만 하위를 모은다 —
  `docs/metrics.md` §26 '가장 깊은 행만'과 일관된 값을 내면서(일관된 응답이면 둘이 같다 — 속성 시험
  '접두 합 = 상위 합') 누락에 견딘다.
- 하위를 모을 때 어떤 칸이 None 이면 아는 값만 더하고 quality 를 `estimated` 로 낮춘다(모두 None
  이면 None). 0 으로 채우지 않는다.
- YoY = 이번 / 전년 같은 달 − 1(전년 None·0, 이번 None → None). 3개월 YoY = 최근 3개월 합 / 전년
  같은 3개월 합 − 1(6칸 중 하나라도 None → None). 3개월 평균 = 직전 3개월 합 ÷ 3(하나라도 None →
  None). 비중 = 부분 / 전체(전체 None·0 → None).
- 기간 값(트리맵 입력): 기간 안 달들의 합. 기간 YoY 는 같은 길이의 전년 기간과 비교 — 두 기간 모두
  모든 달 값이 있을 때만.

금액은 미화 달러(Decimal), 중량 kg(Decimal) — 원화 환산 없음(D-P6-11).
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from kbj.core.quality import Quality
from kbj.core.trade_rows import (
    CountryMonthRow,
    Flow,
    ItemCountryMonthRow,
    ItemMonthRow,
    SigunguMonthRow,
    prev_ym,
)
from kbj.engines.trade.price_volume import unit_price

__all__ = [
    "Amount",
    "CountryLine",
    "ItemIndex",
    "Month",
    "Node",
    "RegionLine",
    "avg_3m",
    "country_lines",
    "item_country_lines",
    "item_index",
    "months_between",
    "region_lines",
    "share",
    "tree_nodes",
    "worst",
    "yoy",
    "yoy_3m",
]

_RANK: Final[dict[Quality, int]] = {
    Quality.OK: 0,
    Quality.STALE: 1,
    Quality.ESTIMATED: 2,
    Quality.INVALID: 3,
}


def worst(qs: Iterable[Quality]) -> Quality | None:
    """가장 나쁜 품질(비면 None)."""
    out: Quality | None = None
    for q in qs:
        if out is None or _RANK[q] > _RANK[out]:
            out = q
    return out


@dataclass(frozen=True)
class Amount:
    """한 접두·한 달의 값(한 흐름 — 수출 또는 수입)."""

    usd: Decimal | None
    kg: Decimal | None
    quality: Quality


def _total(vals: Sequence[Decimal | None]) -> tuple[Decimal | None, bool]:
    """(아는 값의 합, 빠진 칸 있음). 모두 None → (None, True)."""
    got = [v for v in vals if v is not None]
    if not got:
        return None, True
    return sum(got, Decimal(0)), len(got) < len(vals)


class Month:
    """한 달의 접두 → 행 값(정렬된 코드 + 이분 탐색). 접두 값은 메모."""

    __slots__ = ("_codes", "_memo", "_rows")

    def __init__(self, rows: Mapping[str, Amount]) -> None:
        self._rows = dict(rows)
        self._codes = sorted(self._rows)
        self._memo: dict[str, Amount | None] = {}

    def codes(self) -> Sequence[str]:
        return self._codes

    def _under(self, prefix: str) -> list[str]:
        """prefix 로 시작하는 코드(prefix 자신 제외)."""
        i = bisect.bisect_left(self._codes, prefix)
        out: list[str] = []
        while i < len(self._codes) and self._codes[i].startswith(prefix):
            if self._codes[i] != prefix:
                out.append(self._codes[i])
            i += 1
        return out

    def has_below(self, prefix: str) -> bool:
        return bool(self._under(prefix))

    def value(self, prefix: str) -> Amount | None:
        """접두 값 — 그 행이 있으면 그 행, 없으면 바로 아래 접두들의 합(이중 합산 금지)."""
        if prefix in self._memo:
            return self._memo[prefix]
        out = self._rows.get(prefix)
        if out is None:
            kids = sorted({c[: len(prefix) + 2] for c in self._under(prefix)})
            parts = [v for v in (self.value(k) for k in kids) if v is not None]
            if parts:
                usd, g1 = _total([p.usd for p in parts])
                kg, g2 = _total([p.kg for p in parts])
                gap = g1 or g2
                q = worst(p.quality for p in parts) or Quality.OK
                if gap and q is Quality.OK:
                    q = Quality.ESTIMATED
                if usd is None and q is Quality.OK:
                    q = Quality.ESTIMATED
                out = Amount(usd=usd, kg=kg, quality=q)
        self._memo[prefix] = out
        return out


@dataclass(frozen=True)
class ItemIndex:
    """기준 월 → `Month`(한 흐름)."""

    flow: Flow
    months: Mapping[str, Month]

    def value(self, prefix: str, ym: str) -> Amount | None:
        m = self.months.get(ym)
        return None if m is None else m.value(prefix)

    def usd(self, prefix: str, ym: str) -> Decimal | None:
        a = self.value(prefix, ym)
        return None if a is None else a.usd

    def codes_at(self, level: int, parent: str | None = None) -> list[str]:
        """어느 달에든 있는 그 자릿수 접두(부모 아래만) — 정렬."""
        p = parent or ""
        out: set[str] = set()
        for m in self.months.values():
            for c in m.codes():
                if len(c) >= level and c.startswith(p):
                    out.add(c[:level])
        return sorted(out)

    def has_below(self, prefix: str) -> bool:
        return any(m.has_below(prefix) for m in self.months.values())

    def last_ym(self) -> str | None:
        return max(self.months) if self.months else None


def _flow_cells(r: ItemMonthRow | ItemCountryMonthRow, flow: Flow) -> Amount:
    if flow == "exp":
        return Amount(usd=r.exp_usd, kg=r.exp_kg, quality=r.quality)
    return Amount(usd=r.imp_usd, kg=r.imp_kg, quality=r.quality)


def item_index(rows: Iterable[ItemMonthRow | ItemCountryMonthRow], flow: Flow) -> ItemIndex:
    """행 → 달별 접두 색인(invalid 행은 값 없음으로 — 쓰지 않는다)."""
    by: dict[str, dict[str, Amount]] = {}
    for r in rows:
        if r.quality is Quality.INVALID:
            continue
        cell = by.setdefault(r.ym, {})
        if r.hs in cell:
            raise ValueError(f"같은 달 같은 HS 행이 두 번 있다: {r.ym} {r.hs}")
        cell[r.hs] = _flow_cells(r, flow)
    return ItemIndex(flow=flow, months={ym: Month(c) for ym, c in sorted(by.items())})


# ── 비율 ────────────────────────────────────────────────────────────────────────────────────


def yoy(cur: Decimal | None, prev: Decimal | None) -> float | None:
    if cur is None or prev is None or prev == 0:
        return None
    return float(cur / prev) - 1.0


def _sum(vals: Iterable[Decimal | None]) -> Decimal | None:
    total = Decimal(0)
    for v in vals:
        if v is None:
            return None
        total += v
    return total


def _window(get: Callable[[str], Decimal | None], ym: str, n: int) -> Decimal | None:
    return _sum(get(prev_ym(ym, k)) for k in range(n))


def yoy_3m(get: Callable[[str], Decimal | None], ym: str) -> float | None:
    """최근 3개월 합 / 전년 같은 3개월 합 − 1(6칸 중 하나라도 None → None)."""
    return yoy(_window(get, ym, 3), _window(get, prev_ym(ym, 12), 3))


def avg_3m(get: Callable[[str], Decimal | None], ym: str) -> Decimal | None:
    """직전 3개월(ym 포함) 합 ÷ 3."""
    s = _window(get, ym, 3)
    return None if s is None else s / 3


def share(part: Decimal | None, total: Decimal | None) -> float | None:
    if part is None or total is None or total == 0:
        return None
    return float(part / total)


def months_between(ym_from: str, ym_to: str) -> list[str]:
    """ym_from ~ ym_to(포함) 달 목록. from > to 면 빈 목록."""
    out: list[str] = []
    ym = ym_to
    while ym >= ym_from:
        out.append(ym)
        ym = prev_ym(ym)
    return out[::-1]


# ── 접두 트리(트리맵 입력 — 크기 = 금액, 색 = YoY) ───────────────────────────────────────────


@dataclass(frozen=True)
class Node:
    hs: str
    usd: Decimal | None
    kg: Decimal | None
    yoy: float | None
    share: float | None
    unit_price: float | None
    has_children: bool
    quality: Quality


def _period(idx: ItemIndex, hs: str, months: Sequence[str]) -> tuple[Amount | None, bool]:
    """(기간 합, 모든 달 금액이 있음)."""
    vals = [idx.value(hs, m) for m in months]
    got = [v for v in vals if v is not None]
    if not got:
        return None, False
    usd, gap_u = _total([v.usd for v in got])
    kg, _ = _total([v.kg for v in got])
    full = len(got) == len(vals) and not gap_u
    q = worst(v.quality for v in got) or Quality.OK
    if not full and q is Quality.OK:
        q = Quality.ESTIMATED
    return Amount(usd=usd, kg=kg, quality=q), full


def tree_nodes(
    idx: ItemIndex, *, level: int, parent: str | None, ym_from: str, ym_to: str
) -> list[Node]:
    """한 층(level 자리)의 접두 — 기간 합·기간 YoY·형제 중 비중·단가. 금액 내림차순(동률 코드 순).
    부모가 있으면 그 아래만(부모 길이 < level)."""
    if parent is not None and len(parent) >= level:
        raise ValueError(f"부모 {parent!r} 는 level {level} 보다 짧아야 한다")
    months = months_between(ym_from, ym_to)
    prev = [prev_ym(m, 12) for m in months]
    raw: list[tuple[str, Amount, bool, float | None]] = []
    for hs in idx.codes_at(level, parent):
        cur, full = _period(idx, hs, months)
        if cur is None:
            continue
        before, full_prev = _period(idx, hs, prev)
        y = yoy(cur.usd, before.usd if before else None) if full and full_prev else None
        raw.append((hs, cur, full, y))
    total = _sum(a.usd for _, a, _, _ in raw if a.usd is not None) if raw else None
    out = [
        Node(
            hs=hs,
            usd=a.usd,
            kg=a.kg,
            yoy=y,
            share=share(a.usd, total),
            unit_price=unit_price(a.usd, a.kg),
            has_children=idx.has_below(hs),
            quality=a.quality,
        )
        for hs, a, _, y in raw
    ]
    out.sort(key=lambda n: (-(n.usd if n.usd is not None else Decimal(-1)), n.hs))
    return out


# ── 국가·품목×국가·지역 ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CountryLine:
    country: str
    country_name: str
    exp_usd: Decimal | None
    imp_usd: Decimal | None
    bal_usd: Decimal | None
    exp_yoy: float | None
    imp_yoy: float | None
    exp_share: float | None
    quality: Quality


def country_lines(rows: Iterable[CountryMonthRow], ym: str) -> list[CountryLine]:
    """그 달 국가 총괄 — 수출 내림차순(동률 국가 코드 순). 비중 = 국가 수출 / 그 달 국가 합."""
    by = {(r.ym, r.country): r for r in rows if r.quality is not Quality.INVALID}
    cur = [r for (m, _), r in by.items() if m == ym]
    total = _sum(r.exp_usd for r in cur if r.exp_usd is not None) if cur else None
    py = prev_ym(ym, 12)
    out: list[CountryLine] = []
    for r in cur:
        p = by.get((py, r.country))
        out.append(
            CountryLine(
                country=r.country,
                country_name=r.country_name,
                exp_usd=r.exp_usd,
                imp_usd=r.imp_usd,
                bal_usd=r.bal_usd,
                exp_yoy=yoy(r.exp_usd, p.exp_usd if p else None),
                imp_yoy=yoy(r.imp_usd, p.imp_usd if p else None),
                exp_share=share(r.exp_usd, total),
                quality=r.quality,
            )
        )
    out.sort(key=lambda c: (-(c.exp_usd if c.exp_usd is not None else Decimal(-1)), c.country))
    return out


def item_country_lines(
    rows: Iterable[ItemCountryMonthRow], *, prefix: str, ym: str, flow: Flow
) -> list[tuple[str, Amount, float | None, float | None]]:
    """품목(접두)×국가 — [(국가, 그 달 값, YoY, 비중)] 금액 내림차순(동률 국가 순). 접두 값 규칙은
    `Month.value` 와 같다(국가마다)."""
    by: dict[str, list[ItemCountryMonthRow]] = {}
    for r in rows:
        if r.hs.startswith(prefix):
            by.setdefault(r.country, []).append(r)
    py = prev_ym(ym, 12)
    raw: list[tuple[str, Amount, float | None]] = []
    for cc, rs in by.items():
        idx = item_index(rs, flow)
        a = idx.value(prefix, ym)
        if a is None:
            continue
        raw.append((cc, a, yoy(a.usd, idx.usd(prefix, py))))
    total = _sum(a.usd for _, a, _ in raw if a.usd is not None) if raw else None
    out = [(cc, a, y, share(a.usd, total)) for cc, a, y in raw]
    out.sort(key=lambda t: (-(t[1].usd if t[1].usd is not None else Decimal(-1)), t[0]))
    return out


@dataclass(frozen=True)
class RegionLine:
    region_id: str | None
    sido_cd: str
    sgg_nm: str
    hs6: str
    exp_usd: Decimal | None
    exp_yoy: float | None
    share: float | None
    quality: Quality


def _region_key(r: SigunguMonthRow) -> tuple[str, str]:
    """전년 비교 열쇠 — 통합 지역 ID(개편 전후 같은 곳), 없으면 (시도, 시군구 이름)."""
    return (r.region_id, r.hs6) if r.region_id else (f"{r.sido_cd}:{r.sgg_nm}", r.hs6)


def region_lines(rows: Iterable[SigunguMonthRow], ym: str) -> list[RegionLine]:
    """그 달 시군구 표 — 비중 = 같은 HS6 안 시군구 수출 비중. 수출 내림차순."""
    valid = [r for r in rows if r.quality is not Quality.INVALID]
    prev = {_region_key(r): r for r in valid if r.ym == prev_ym(ym, 12)}
    cur = [r for r in valid if r.ym == ym]
    totals: dict[str, Decimal] = {}
    for r in cur:
        if r.exp_usd is not None:
            totals[r.hs6] = totals.get(r.hs6, Decimal(0)) + r.exp_usd
    out: list[RegionLine] = []
    for r in cur:
        p = prev.get(_region_key(r))
        out.append(
            RegionLine(
                region_id=r.region_id,
                sido_cd=r.sido_cd,
                sgg_nm=r.sgg_nm,
                hs6=r.hs6,
                exp_usd=r.exp_usd,
                exp_yoy=yoy(r.exp_usd, p.exp_usd if p else None),
                share=share(r.exp_usd, totals.get(r.hs6)),
                quality=r.quality,
            )
        )
    out.sort(
        key=lambda x: (
            -(x.exp_usd if x.exp_usd is not None else Decimal(-1)),
            x.hs6,
            x.sido_cd,
            x.sgg_nm,
        )
    )
    return out
