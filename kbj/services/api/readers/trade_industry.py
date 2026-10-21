"""국가 품목 추이·산업 reader — `/api/trade/countries/{cc}/trend`·`/api/trade/sectors`
(P10 단계 4 — docs/p10_trade_stocks_plan.md, 로그인 전용).

DB 만 읽는다(계약 ⑩). 금액은 미화 달러(원화 환산 없음).

- 국가 품목 추이: 그 나라 수출 품목(HS4 — 없으면 HS2)마다 최근 `SPARK_MONTHS` 개월 금액·전월 대비·
  전년 같은 달 대비, 그 품목을 매핑한 종목(매핑 줄의 상대국이 이 나라이거나 비어 있는 전국 품목 줄).
- 산업: 매핑표 종목의 산업(`flow_meta.sector` — 없으면 '미분류')마다 종목 수출 지수(`stock_export`
  월 행, 가중 합) 합·YoY·3개월 YoY·스파크라인, 종목·추적 품목. YoY 는 두 기간 모두 값이 있는
  종목만으로 셈(종목 지수가 새로 생긴 달이 성장으로 보이지 않게).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Final

from kbj.core.quality import Quality
from kbj.core.trade_rows import StockMapRow, prev_ym
from kbj.engines.trade.explore import item_index, months_between, yoy
from kbj.engines.trade.mapping import resolve
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.trade import (
    TradeCountryTrend,
    TradeCountryTrendRow,
    TradeRelatedStock,
    TradeSector,
    TradeSectorProduct,
    TradeSectors,
    TradeSectorStock,
)
from kbj.services.api.readers._common import NoData, ReadContext, envelope, worst
from kbj.services.api.readers.trade_map import NO_MAP, NO_STOCK_INDEX, NO_TRADE

__all__ = ["SECTOR_NONE", "SOURCE", "SPARK_MONTHS", "trade_country_trend", "trade_sectors"]

SOURCE: Final = "관세청(공공데이터포털)"
SPARK_MONTHS: Final = 24
SECTOR_NONE: Final = "미분류"
NOTE_USD: Final = "금액은 미화 달러(원화 환산 없음 — 환율 미조정)"
NOTE_SECTOR: Final = (
    "산업 금액 = 종목 수출 지수(매핑 가중) 합 — 통관 합이 아니다. YoY 는 두 기간 모두 값이 있는"
    " 종목만"
)


def _int(v: Decimal | None) -> int | None:
    return None if v is None else int(v)


def _item_lines(smap: Iterable[StockMapRow]) -> list[StockMapRow]:
    res = resolve(smap)
    return [ln for code in res.codes() for ln in res.lines[code] if not ln.region_id]


def trade_country_trend(ctx: ReadContext, *, country: str) -> Envelope[TradeCountryTrend]:
    repo = ctx.repos.trade
    at = repo.last_ym("pub_trade.item_country_monthly")
    if at is None:
        raise NoData(NO_TRADE)
    months = months_between(prev_ym(at, SPARK_MONTHS - 1), at)
    rows = repo.item_country_monthly(country=country, ym_from=months[0], ym_to=at)
    idx = item_index(rows, "exp")
    level = 4 if idx.codes_at(4) else 2
    codes = idx.codes_at(level)
    if not codes:
        raise NoData(f"{country}·{at} 품목 값 없음 — {NO_TRADE}")
    lines = [
        ln
        for ln in _item_lines(repo.stock_map())
        if ln.flow == "exp" and (not ln.countries or country in ln.countries)
    ]
    out: list[TradeCountryTrendRow] = []
    for hs in codes:
        cur = idx.usd(hs, at)
        stocks = sorted(
            {(ln.code, ln.name) for ln in lines if ln.hs[:level] == hs or hs.startswith(ln.hs)}
        )
        out.append(
            TradeCountryTrendRow(
                hs=hs, usd=_int(cur), mom=yoy(cur, idx.usd(hs, prev_ym(at, 1))),
                yoy=yoy(cur, idx.usd(hs, prev_ym(at, 12))),
                spark=[_int(idx.usd(hs, m)) for m in months],
                stocks=[TradeRelatedStock(code=c, name=n) for c, n in stocks],
            )
        )  # fmt: skip
    out.sort(key=lambda r: (r.usd is None, -(r.usd or 0), r.hs))
    used = [r for r in rows if r.ym == at]
    return envelope(
        TradeCountryTrend(country=country, ym=at, months=months, rows=out), source=SOURCE,
        as_of=max((r.as_of for r in used), default=ctx.now()),
        quality=worst(r.quality for r in used) or Quality.OK,
        notes=[NOTE_USD, "관련 종목 = 그 품목을 매핑한 종목(상대국이 이 나라이거나 전세계)"],
        generated_at=ctx.now(),
    )  # fmt: skip


def _pair_yoy(
    monthly: Mapping[str, Mapping[str, Decimal | None]],
    codes: Sequence[str],
    now: Sequence[str],
    before: Sequence[str],
) -> float | None:
    """두 기간 모두 모든 달 값이 있는 종목만 합해 now ÷ before − 1."""
    a = b = Decimal(0)
    hit = False
    for c in codes:
        m = monthly.get(c, {})
        xs = [m.get(ym) for ym in now]
        ys = [m.get(ym) for ym in before]
        if all(x is not None for x in xs) and all(y is not None for y in ys):
            a += sum((x for x in xs if x is not None), Decimal(0))
            b += sum((y for y in ys if y is not None), Decimal(0))
            hit = True
    return yoy(a, b) if hit else None


def _sum(
    monthly: Mapping[str, Mapping[str, Decimal | None]], codes: Sequence[str], ym: str
) -> int | None:
    vals = [v for c in codes if (v := monthly.get(c, {}).get(ym)) is not None]
    return int(sum(vals, Decimal(0))) if vals else None


def trade_sectors(ctx: ReadContext) -> Envelope[TradeSectors]:
    repo = ctx.repos.trade
    smap = repo.stock_map()
    if not smap:
        raise NoData(NO_MAP)
    res = resolve(smap)
    exports = [r for r in repo.stock_export() if len(r.period) == 7]
    if not exports:
        raise NoData(NO_STOCK_INDEX)
    monthly: dict[str, dict[str, Decimal | None]] = defaultdict(dict)
    for r in exports:
        monthly[r.code][r.period] = r.export_usd
    last = max(r.period for r in exports)
    months = months_between(prev_ym(last, SPARK_MONTHS - 1), last)
    now3 = [prev_ym(last, k) for k in (2, 1, 0)]
    before3 = [prev_ym(m, 12) for m in now3]
    groups: dict[str, list[str]] = defaultdict(list)
    products: dict[str, dict[str, str | None]] = defaultdict(dict)
    names: dict[str, str] = {}
    for code in res.codes():
        lines = res.lines[code]
        sector = str(lines[0].flow_meta.get("sector") or "") or SECTOR_NONE
        groups[sector].append(code)
        names[code] = lines[0].name
        for ln in lines:
            meta = ln.flow_meta.get("products")
            got = {
                str(p.get("hs")): (None if p.get("name") is None else str(p.get("name")))
                for p in (meta if isinstance(meta, list) else [])
                if isinstance(p, dict)
            }
            products[sector].setdefault(ln.hs, got.get(ln.hs))
    rows: list[TradeSector] = []
    for sector, codes in groups.items():
        stocks = [
            TradeSectorStock(
                code=c, name=names[c], usd=_sum(monthly, [c], last),
                yoy_3m=_pair_yoy(monthly, [c], now3, before3),
            )
            for c in codes
        ]  # fmt: skip
        stocks.sort(key=lambda s: (s.usd is None, -(s.usd or 0), s.code))
        rows.append(
            TradeSector(
                sector=sector, n_stocks=len(codes), usd=_sum(monthly, codes, last),
                yoy=_pair_yoy(monthly, codes, [last], [prev_ym(last, 12)]),
                yoy_3m=_pair_yoy(monthly, codes, now3, before3),
                spark=[_sum(monthly, codes, m) for m in months], stocks=stocks,
                products=[
                    TradeSectorProduct(hs=h, name=n) for h, n in sorted(products[sector].items())
                ],
            )
        )  # fmt: skip
    rows.sort(key=lambda r: (r.usd is None, -(r.usd or 0), r.sector))
    used = [r for r in exports if r.period == last]
    return envelope(
        TradeSectors(last_ym=last, months=months, rows=rows), source="관세청(공공데이터포털)+DART",
        as_of=max(r.as_of for r in used), quality=worst(r.quality for r in used) or Quality.OK,
        notes=[NOTE_USD, NOTE_SECTOR], generated_at=ctx.now(),
    )  # fmt: skip
