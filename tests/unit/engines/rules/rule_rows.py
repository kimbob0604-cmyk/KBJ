"""규칙 엔진 시험의 합성 입력(고정 — 실데이터 아님)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta

from kbj.core.quality import Quality
from kbj.core.rows import Bar
from kbj.core.rules_rows import QuoteSlot
from kbj.core.time import KST
from kbj.engines.flows.ledger import LedgerRow
from kbj.engines.rules.dsl import Rule, validate_rule
from kbj.engines.rules.frame import Frame, build_frame

CODE = "005930"
ANCHOR = date(2020, 1, 2)


def days(n: int, start: date = date(2026, 1, 5)) -> list[date]:
    """평일 n 개(합성 거래일)."""
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def bar(
    d: date, c: float | None, *, code: str = CODE, vol: int | None = 1000, q: Quality = Quality.OK
) -> Bar:
    if c is None:
        return Bar(code, d, None, None, None, None, None, None, "krx", "KRX", Quality.STALE)
    return Bar(code, d, c * 0.99, c * 1.01, c * 0.98, c, vol, None, "krx", "KRX", q)


def bars(
    closes: Sequence[float | None],
    ds: Sequence[date] | None = None,
    vols: Sequence[int | None] | None = None,
) -> list[Bar]:
    ds = ds or days(len(closes))
    vs = vols or [1000] * len(closes)
    return [bar(d, c, vol=v) for d, c, v in zip(ds, closes, vs, strict=True)]


def flow(
    d: date, foreign: int | None, inst: int | None = 0, *, code: str = CODE, q: Quality = Quality.OK
) -> LedgerRow:
    return LedgerRow(
        code=code,
        date=d,
        name=code,
        market="KOSPI",
        kind="common",
        close=100.0,
        chg_pct=0.0,
        volume=None,
        turnover=1_000_000,
        mktcap=None,
        foreign=foreign,
        inst=inst,
        other_corp=0,
        indiv=0,
        inst7=None,
        traded=True,
        flags=(),
        quality=q,
        sources={},
    )


def slot(
    d: date,
    price: float | None,
    *,
    hhmm: tuple[int, int] = (10, 0),
    vol: int | None = 500,
    flags: tuple[str, ...] = (),
    code: str = CODE,
) -> QuoteSlot:
    ts = datetime(d.year, d.month, d.day, hhmm[0], hhmm[1], tzinfo=KST)
    q = Quality.ESTIMATED
    if price is None:
        return QuoteSlot(ts, code, None, None, None, None, vol, None, None, flags, q, "KIS")
    return QuoteSlot(
        ts, code, price, price * 1.01, price * 0.99, price, vol, None, None, flags, q, "KIS"
    )


def frame(closes: Sequence[float | None], **kw: object) -> Frame:
    bs = bars(closes)
    upto = bs[-1].date
    return build_frame(CODE, bs, upto=upto, anchor=ANCHOR, split_ratio=0.31, **kw)  # type: ignore[arg-type]


def rule(when: dict[str, object], mode: str = "close", **extra: object) -> Rule:
    data: dict[str, object] = {
        "v": 1,
        "name": "t",
        "universe": {"codes": [CODE]},
        "mode": mode,
        "when": when,
        **extra,
    }
    return validate_rule(data)
