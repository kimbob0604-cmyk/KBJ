"""수급·시장 엔진 시험용 손 행 도우미(합성 — 실데이터 아님)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from kbj.core.quality import Quality
from kbj.core.rows import INST7, Bar, Investor, InvestorDay, Snap
from kbj.engines.flows.ledger import Ledger, LedgerRow

D0 = date(2026, 9, 1)
OK = Quality.OK


def days(n: int, start: date = D0) -> list[date]:
    """start 부터 평일 n 개(가짜 캘린더)."""
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def snap(
    code: str,
    d: date,
    *,
    close: float | None = 1000.0,
    chg: float | None = 0.0,
    turnover: int | None = 1_000_000_000,
    mktcap: int | None = 100_000_000_000,
    market: str | None = "KOSPI",
    kind: str | None = "common",
    flags: tuple[str, ...] | None = (),
    source: str = "krx",
    quality: Quality = OK,
    venue: str = "KRX",
    name: str | None = None,
) -> Snap:
    vol = None if turnover is None or close is None else turnover // int(close)
    return Snap(
        code, d, name or f"이름{code}", market, kind, close, chg, vol, turnover, False, mktcap,
        None, flags, source, venue, quality,
    )  # fmt: skip


def bar(
    code: str,
    d: date,
    *,
    close: float | None = 1000.0,
    turnover: int | None = 1_000_000_000,
    source: str = "krx",
    quality: Quality = OK,
) -> Bar:
    return Bar(code, d, close, close, close, close, None, turnover, source, "KRX", quality)


def inv(
    code: str,
    d: date,
    *,
    foreign: int | None = 0,
    inst: int | None = 0,
    other_corp: int | None = 0,
    indiv: int | None = None,
    inst7: list[int] | None = None,
    source: str = "kis",
    quality: Quality = OK,
) -> list[InvestorDay]:
    """4구분(개인은 주지 않으면 합 0 이 되게) + 선택 7구분."""
    if indiv is None and None not in (foreign, inst, other_corp):
        indiv = -((foreign or 0) + (inst or 0) + (other_corp or 0))
    out: list[InvestorDay] = []
    for who, v in (
        (Investor.FOREIGN, foreign),
        (Investor.INSTITUTION, inst),
        (Investor.OTHER_CORP, other_corp),
        (Investor.INDIVIDUAL, indiv),
    ):
        if v is not None:
            out.append(InvestorDay(code, d, who, v, None, source, "KRX", quality))
    if inst7 is not None:
        for who, v in zip(INST7, inst7, strict=False):
            out.append(InvestorDay(code, d, who, v, None, source, "KRX", quality))
    return out


def lrow(
    code: str,
    d: date,
    *,
    foreign: int | None = 0,
    inst: int | None = 0,
    other_corp: int | None = 0,
    indiv: int | Literal["auto"] | None = "auto",
    turnover: int | None = 1_000_000_000,
    traded: bool = True,
    quality: Quality = OK,
    flags: tuple[str, ...] | None = (),
    close: float | None = 1000.0,
    chg_pct: float | None = 0.0,
    mktcap: int | None = 100_000_000_000,
    market: str | None = "KOSPI",
    kind: str | None = "common",
) -> LedgerRow:
    if indiv == "auto":  # 검산 ① 이 맞게(개인 = −나머지 합)
        indiv = (
            None
            if None in (foreign, inst, other_corp)
            else -((foreign or 0) + (inst or 0) + (other_corp or 0))
        )
    return LedgerRow(
        code=code, date=d, name=code, market=market, kind=kind, close=close, chg_pct=chg_pct,
        volume=None, turnover=turnover, mktcap=mktcap, foreign=foreign, inst=inst,
        other_corp=other_corp, indiv=indiv, inst7=None, traded=traded, flags=flags,
        quality=quality, sources={"turnover": "krx", "flows": "kis"},
    )  # fmt: skip


def ledger_of(rows: list[LedgerRow], trading_days: list[date] | None = None) -> Ledger:
    td = trading_days or sorted({r.date for r in rows})
    return Ledger(rows={(r.code, r.date): r for r in rows}, trading_days=tuple(td))
