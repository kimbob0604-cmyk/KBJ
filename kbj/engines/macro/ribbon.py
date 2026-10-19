"""상단 띠 P5 칩 셋(D-P5-20 — 순수): 신용스프레드 AA-·신용잔고(공개), 한국 대 글로벌(로그인).

P3 `kbj.engines.market.ribbon.PENDING` 의 같은 키 칩을 값으로 바꾼다(API 리더 `readers/market.py` 가
갈아 끼운다). 입력이 없으면 None — 부르는 쪽이 '준비 중(P5 — 수집 꺼짐·실측 전)' 칩을 그대로 둔다
(0 으로 그리지 않는다).

- 신용스프레드 AA-: 회사채 3년 AA- − 국고 3년(ECOS 817Y002, %p — metrics §16). 두 시리즈 모두 값이
  있는 마지막 날. 어느 한쪽이 그보다 뒤 날짜 값을 이미 가졌으면(공표 지연) `stale`. detail 에 전
  공통일
  대비 변화(bp).
- 신용잔고: 금투협 신용거래융자 전체(`loan_total`) — 없으면
  유가+코스닥(`loan_kospi`+`loan_kosdaq`)이
  같은 날 둘 다 있을 때만 합. 기대 날짜(전 거래일)보다 이르면 `stale`. detail 에 전일 대비 변화.
- 한국 대 글로벌(로그인 — [제안] 정의, metrics §6.5 보강은 WRAP 요청): 코스피 최근 거래일 등락률 −
  간밤 미국 지수(`US_INDEX` — S&P 500·나스닥) 등락률 평균(%p). 한쪽이라도 없으면 None.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Final

from kbj.core.p5_rows import MacroSeriesRow, MarketStatRow, UsDailyRow
from kbj.core.quality import Quality, Sourced
from kbj.core.rows import IndexBar
from kbj.engines.flows.ledger import close_as_of
from kbj.engines.market.ribbon import Chip

__all__ = [
    "BALANCE_PARTS",
    "BALANCE_TOTAL",
    "CREDIT_AA",
    "CREDIT_KTB",
    "US_INDEX",
    "balance_on",
    "credit_balance_chip",
    "credit_spread_chip",
    "kr_vs_global_chip",
]

CREDIT_AA: Final = "ECOS:817Y002:corp_aa3y"
CREDIT_KTB: Final = "ECOS:817Y002:ktb3y"
CREDIT_SOURCE: Final = "ECOS:817Y002"
BALANCE_TOTAL: Final = "loan_total"
BALANCE_PARTS: Final = ("loan_kospi", "loan_kosdaq")
US_INDEX: Final[tuple[str, ...]] = ("^GSPC", "^IXIC")  # [제안] — US 묶음 config/us.yaml 심볼과 같게
_ORDER: Final = (Quality.OK, Quality.ESTIMATED, Quality.STALE, Quality.INVALID)


def _worst(*qs: Quality) -> Quality:
    return max(qs, key=_ORDER.index)


def credit_spread_chip(aa: Sequence[MacroSeriesRow], ktb: Sequence[MacroSeriesRow]) -> Chip | None:
    a = {r.period: r for r in aa if r.value is not None}
    k = {r.period: r for r in ktb if r.value is not None}
    common = sorted(set(a) & set(k))
    if not common:
        return None
    p = common[-1]
    ra, rk = a[p], k[p]
    v = float(ra.value - rk.value)  # pyright: ignore[reportOperatorIssue]
    q = _worst(ra.quality, rk.quality)
    lag = max([*a, *k]) > p
    if lag:
        q = _worst(q, Quality.STALE)
    stamps = [t for t in (ra.released_at, rk.released_at) if t is not None] or [ra.as_of, rk.as_of]
    detail: dict[str, object] = {"period": p, "aa": float(ra.value), "ktb3y": float(rk.value)}  # pyright: ignore[reportArgumentType]
    if len(common) > 1:
        pp = common[-2]
        prev = float(a[pp].value - k[pp].value)  # pyright: ignore[reportOperatorIssue]
        detail["change_bp"] = (v - prev) * 100.0
        detail["prev_period"] = pp
    note = "공표 지연 — 한쪽 시리즈가 늦다(전 공통일 값)" if lag else None
    return Chip(
        "credit_spread", "신용스프레드 AA-",
        Sourced[float](value=v, source=CREDIT_SOURCE, as_of=max(stamps), quality=q),
        "public", tags=("%p",), note=note, detail=detail,
    )  # fmt: skip


def balance_on(rows: Sequence[MarketStatRow], d: date) -> tuple[float, Quality, datetime] | None:
    """그날 신용거래융자 잔고(전체, 없으면 유가+코스닥 둘 다 있을 때 합) — 띠 칩·페이지 1 공용."""
    by = {r.item: r for r in rows if r.date == d and r.value is not None}
    if BALANCE_TOTAL in by:
        r = by[BALANCE_TOTAL]
        return float(r.value), r.quality, r.as_of  # pyright: ignore[reportArgumentType]
    if all(i in by for i in BALANCE_PARTS):
        parts = [by[i] for i in BALANCE_PARTS]
        return (
            sum(float(r.value) for r in parts if r.value is not None),
            _worst(*(r.quality for r in parts)),
            max(r.as_of for r in parts),
        )
    return None


def credit_balance_chip(rows: Sequence[MarketStatRow], *, expected: date | None) -> Chip | None:
    """신용공여 잔고(credit_balance 표 행). expected = 이 날 값이 있어야 최신(전 거래일)."""
    days = sorted({r.date for r in rows if r.stat == "credit_balance"}, reverse=True)
    got: list[tuple[date, tuple[float, Quality, datetime]]] = []
    for d in days:
        b = balance_on([r for r in rows if r.stat == "credit_balance"], d)
        if b is not None:
            got.append((d, b))
        if len(got) == 2:
            break
    if not got:
        return None
    (d, (v, q, at)) = got[0]
    unit = next((r.unit for r in rows if r.date == d), "")
    note = None
    if expected is not None and d < expected:
        q = _worst(q, Quality.STALE)
        note = f"공표 지연 — {d.isoformat()} 값(D+1 13시 개방)"
    detail: dict[str, object] = {"date": d.isoformat(), "unit": unit}
    if len(got) == 2:
        detail["change"] = v - got[1][1][0]
        detail["prev_date"] = got[1][0].isoformat()
    return Chip(
        "credit_balance", "신용잔고",
        Sourced[float](value=v, source="DATAGO:15094809/credit", as_of=at, quality=q),
        "public", tags=(unit,) if unit else (), note=note, detail=detail,
    )  # fmt: skip


def _pct(
    closes: Sequence[tuple[date, float | None, Quality]],
) -> tuple[date, float, Quality] | None:
    have = [(d, c, q) for d, c, q in closes if c is not None and c != 0]
    if len(have) < 2:
        return None
    (_, c0, q0), (d1, c1, q1) = have[-2], have[-1]
    return d1, (c1 / c0 - 1.0) * 100.0, _worst(q0, q1)  # pyright: ignore[reportOptionalOperand]


def kr_vs_global_chip(
    kospi: Sequence[IndexBar], us: Mapping[str, Sequence[UsDailyRow]]
) -> Chip | None:
    kr = _pct([(b.date, b.close, b.quality) for b in sorted(kospi, key=lambda b: b.date)])
    if kr is None:
        return None
    us_pct: dict[str, float] = {}
    qs: list[Quality] = [kr[2]]
    us_day: date | None = None
    srcs: set[str] = set()
    for sym in US_INDEX:
        rows = sorted(us.get(sym, ()), key=lambda r: r.date)
        got = _pct([(r.date, r.close, r.quality) for r in rows])
        if got is None:
            return None
        us_pct[sym] = got[1]
        qs.append(got[2])
        us_day = got[0] if us_day is None else max(us_day, got[0])
        srcs |= {r.source for r in rows}
    avg = sum(us_pct.values()) / len(us_pct)
    kr_src = {b.source for b in kospi}
    return Chip(
        "kr_vs_global", "한국 대 글로벌",
        Sourced[float](
            value=kr[1] - avg, source="+".join(sorted(kr_src | srcs)) or "KBJ",
            as_of=close_as_of(kr[0]), quality=_worst(*qs),
        ),
        "login", tags=("%p",),
        detail={"kospi_pct": kr[1], "kr_day": kr[0].isoformat(), "us_pct": us_pct,
                "us_day": us_day.isoformat() if us_day else None},
    )  # fmt: skip
