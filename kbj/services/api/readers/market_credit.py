"""페이지 1 '신용잔고 · 예탁금 · 펀드 자금' reader — `/api/market/credit`(로그인).

- 원천: 금투협 종합통계(`pub_market_stats.*` — 수집 `market_stats.kofia` 13:30, D+1 개방). 공개 등급
  원천이지만 공개 내보내기 `credit.json` 은 마지막 값·전 값 변화만 싣는다(시계열 없음) — 20일 추이가
  필요한 이 패널은 로그인 API 로 읽는다.
- 신용잔고 = 신용거래융자 전체(`loan_total`), 없으면 유가+코스닥 합(엔진 `balance_on` — 띠 칩과 같은
  정의). 예탁금 = `investor_deposit`. 금투협 일별 예탁금이 하나도 없으면 ECOS 901Y056(투자자예탁금,
  월별 — `prv_macro`)로 대신 채우고 freq 'M' 으로 알린다. 펀드 자금 = 펀드 순자산(`fund_nav`
  `net_assets`) — 수집기가 필수 입력 실측 전(#48)이라 비어 있으면 사유만.
- 단위: 출처 단위(원·백만원·십억원 …)를 원으로 바꿔 정수로 싣는다(화면에서만 조·억). 모르는 단위는
  값을 지어내지 않고 그 줄을 비운다(사유 note).
- 값이 없는 날은 None(0 금지). 마지막 값이 기대 날짜(전 거래일, 13:30 전이면 전전 거래일)보다
  이르면 stale + 공표 지연 사유.
- 세 줄 모두 값이 없으면 404 no_data(수집 전).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Final, Literal

from kbj.core.p5_rows import MacroSeriesRow, MarketStatRow
from kbj.core.quality import Quality
from kbj.engines.macro.ribbon import BALANCE_PARTS, BALANCE_TOTAL, balance_on
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.market import CreditLine, CreditPanel, CreditPoint
from kbj.services.api.readers._common import NoData, ReadContext, envelope, label, won, worst

__all__ = ["ECOS_DEPOSIT", "LOOKBACK_DAYS", "SPARK_N", "UNIT_WON", "market_credit"]

SPARK_N: Final = 20
LOOKBACK_DAYS: Final = 45  # 달력일 — 20영업일 + 공표 지연·연휴
ECOS_LOOKBACK_MONTHS: Final = 24
KOFIA_OPEN: Final = (13, 30)  # 금투협 통계 D+1 13시 개방 + 수집 13:30(띠 칩과 같다)
ECOS_DEPOSIT: Final = "ECOS:901Y056:deposit"
SRC_CREDIT: Final = "DATAGO:15094809/credit"
SRC_CAPITAL: Final = "DATAGO:15094809/capital"
SRC_FUND: Final = "DATAGO:15094809/fund"
FUND_ITEM: Final = "net_assets"
DEPOSIT_ITEM: Final = "investor_deposit"
# 출처 단위 → 원 배수(모르는 단위는 값을 비운다)
UNIT_WON: Final[dict[str, Decimal]] = {
    "원": Decimal(1),
    "천원": Decimal(10) ** 3,
    "백만원": Decimal(10) ** 6,
    "억원": Decimal(10) ** 8,
    "십억원": Decimal(10) ** 9,
    "조원": Decimal(10) ** 12,
}

Key = Literal["credit_balance", "investor_deposit", "fund_nav"]


@dataclass(frozen=True)
class _Point:
    day: date
    value: int | None
    quality: Quality
    as_of: datetime


def _to_won(v: Decimal | float | None, unit: str) -> int | None:
    k = UNIT_WON.get(unit.strip())
    if v is None or k is None:
        return None
    return won(float(Decimal(str(v)) * k))


def _unknown_units(units: Sequence[str]) -> list[str]:
    return sorted({u for u in units if u.strip() not in UNIT_WON})


def _balance_points(rows: Sequence[MarketStatRow]) -> tuple[list[_Point], list[str]]:
    days = sorted({r.date for r in rows})
    items = {BALANCE_TOTAL, *BALANCE_PARTS}
    out: list[_Point] = []
    units: list[str] = []
    for d in days:
        b = balance_on(rows, d)
        if b is None:
            continue
        unit = next((r.unit for r in rows if r.date == d and r.item in items), "")
        units.append(unit)
        v, q, at = b
        out.append(_Point(d, _to_won(v, unit), q, at))
    return out, units


def _item_points(rows: Sequence[MarketStatRow], item: str) -> tuple[list[_Point], list[str]]:
    got = sorted((r for r in rows if r.item == item), key=lambda r: r.date)
    return (
        [_Point(r.date, _to_won(r.value, r.unit), r.quality, r.as_of) for r in got],
        [r.unit for r in got],
    )


def _ecos_points(rows: Sequence[MacroSeriesRow]) -> tuple[list[_Point], list[str]]:
    out: list[_Point] = []
    for r in rows:
        if len(r.period) != 7:  # 월별(YYYY-MM)만
            continue
        d = date(int(r.period[:4]), int(r.period[5:7]), 1)
        out.append(_Point(d, _to_won(r.value, r.unit), r.quality, r.released_at or r.as_of))
    return out, [r.unit for r in rows]


def _diff(a: int | None, b: int | None) -> int | None:
    return None if a is None or b is None else a - b


def _line(
    key: Key,
    title: str,
    freq: Literal["D", "M"],
    pts: Sequence[_Point],
    units: Sequence[str],
    *,
    source: str,
    expected: date | None,
    empty_note: str,
) -> CreditLine:
    bad = _unknown_units(units)
    have = [p for p in pts if p.value is not None]
    if not have:
        note = f"단위 확인 필요({', '.join(bad)}) — 값을 비운다" if bad else empty_note
        return CreditLine(
            key=key, label=title, freq=freq, value=None, date=None, change=None,
            prev_date=None, spark=[], source=source, as_of=None, quality=None, note=note,
        )  # fmt: skip
    last = have[-1]
    prev = have[-2] if len(have) > 1 else None
    q = last.quality
    notes: list[str] = []
    if expected is not None and last.day < expected:
        q = worst([q, Quality.STALE]) or Quality.STALE
        notes.append(f"공표 지연 — {last.day.isoformat()} 값(D+1 13시 개방)")
    if freq == "M":
        notes.append("월별(ECOS 901Y056) — 금투협 일별 값 없음")
    if bad:
        notes.append(f"단위 확인 필요({', '.join(bad)})인 날은 비움")
    upto = [p for p in pts if p.day <= last.day][-SPARK_N:]
    return CreditLine(
        key=key,
        label=title,
        freq=freq,
        value=last.value,
        date=last.day,
        change=_diff(last.value, None if prev is None else prev.value),
        prev_date=None if prev is None else prev.day,
        spark=[CreditPoint(date=p.day, value=p.value) for p in upto],
        source=source,
        as_of=last.as_of,
        quality=q,
        note=" · ".join(notes) or None,
    )


def market_credit(ctx: ReadContext) -> Envelope[CreditPanel]:
    now = ctx.now_kst()
    today = now.date()
    start = today - timedelta(days=LOOKBACK_DAYS)
    prev = ctx.cal.prev_trading_day(today)
    expected = prev if (now.hour, now.minute) >= KOFIA_OPEN else ctx.cal.prev_trading_day(prev)
    stats = ctx.repos.market_stats

    bal_pts, bal_units = _balance_points(stats.series("credit_balance", start=start, end=today))
    credit = _line(
        "credit_balance", "신용잔고", "D", bal_pts, bal_units,
        source=SRC_CREDIT, expected=expected,
        empty_note="아직 없음 — market_stats.kofia 13:30(D+1)",
    )  # fmt: skip

    dep_pts, dep_units = _item_points(
        stats.series("market_capital", [DEPOSIT_ITEM], start=start, end=today), DEPOSIT_ITEM
    )
    deposit = _line(
        "investor_deposit", "투자자예탁금", "D", dep_pts, dep_units,
        source=SRC_CAPITAL, expected=expected,
        empty_note="아직 없음 — market_stats.kofia 13:30(D+1)",
    )  # fmt: skip
    if deposit.value is None:
        since = today.replace(day=1)
        for _ in range(ECOS_LOOKBACK_MONTHS):
            since = (since - timedelta(days=1)).replace(day=1)
        ecos = ctx.repos.macro_prv.series([ECOS_DEPOSIT], f"{since:%Y-%m}").get(ECOS_DEPOSIT, [])
        e_pts, e_units = _ecos_points(ecos)
        if any(p.value is not None for p in e_pts):
            deposit = _line(
                "investor_deposit", "투자자예탁금", "M", e_pts, e_units,
                source="ECOS:901Y056", expected=None, empty_note=deposit.note or "",
            )  # fmt: skip

    fund_pts, fund_units = _item_points(
        stats.series("fund_nav", [FUND_ITEM], start=start, end=today), FUND_ITEM
    )
    fund = _line(
        "fund_nav", "펀드 순자산", "D", fund_pts, fund_units,
        source=SRC_FUND, expected=expected,
        empty_note="아직 없음 — 펀드 순자산 수집 전(필수 입력 ctg·tstMthdCtg 실측 #48)",
    )  # fmt: skip

    lines = [credit, deposit, fund]
    got = [ln for ln in lines if ln.value is not None and ln.as_of is not None]
    if not got:
        raise NoData("아직 없음 — market_stats.kofia 13:30(D+1 개방)")
    return envelope(
        CreditPanel(lines=lines),
        source=label(ln.source.split(":", 1)[0] for ln in got),
        as_of=max(ln.as_of for ln in got if ln.as_of is not None),
        quality=worst(ln.quality for ln in got),
        notes=[],
        generated_at=ctx.now(),
    )
