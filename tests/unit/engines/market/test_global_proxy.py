"""글로벌 프록시 엔진 — 환산·괴리율·펀딩률(합성 값)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import GlobalQuote
from kbj.engines.market.global_proxy import build, yahoo_symbols

TS = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)


def q(
    sym: str, price: float, prev: float | None = None, funding: float | None = None
) -> GlobalQuote:
    return GlobalQuote(
        symbol=sym, ts=TS, price=price, prev=prev, funding_1h=funding, vol_24h=None,
        currency="USD", source="yahoo", quality=Quality.ESTIMATED,
    )  # fmt: skip


def quotes() -> dict[str, GlobalQuote]:
    rows = [
        q("KRW=X", 1000.0),
        q("EURUSD=X", 1.2),
        q("xyz:SMSN", 110.0, 100.0, 0.0001),
        q("xyz:KR200", 505.0, 500.0),
        q("xyz:SKHY", 21.0, 20.0),
        q("SKHY", 20.0, 19.0),
        q("SMSN.IL", 2750.0, 2700.0),
        q("HY9H.F", 180.0),
        q("NQ=F", 101.0, 100.0),
    ]
    return {r.symbol: r for r in rows}


def test_perps_vs_domestic_close() -> None:
    v = build(quotes(), {"005930": 100_000.0, "000660": 200_000.0}, {"KOSPI200": 500.0})
    smsn = next(r for r in v.perps if r.symbol == "xyz:SMSN")
    assert smsn.vs_close_pct == pytest.approx(10.0)  # 110$ vs 100,000원 / 1000 = 100$
    assert smsn.change_pct == pytest.approx(10.0)
    assert smsn.funding_8h_pct == pytest.approx(0.08)
    assert smsn.vs_label == "국내 종가(달러 환산)"
    k200 = next(r for r in v.perps if r.symbol == "xyz:KR200")
    assert k200.vs_close_pct == pytest.approx(1.0) and k200.unit == "pt"
    skhy = next(r for r in v.perps if r.symbol == "xyz:SKHY")
    assert skhy.vs_close_pct == pytest.approx(5.0)  # 나스닥 ADR 종가 대비
    hd = next(r for r in v.perps if r.symbol == "xyz:HYUNDAI")
    assert hd.price is None and hd.error == "받은 값 없음"


def test_listing_premium_usd_and_eur() -> None:
    v = build(quotes(), {"005930": 100_000.0, "000660": 200_000.0}, {})
    adr = next(r for r in v.listings if r.symbol == "SKHY")
    assert adr.fair == pytest.approx(20.0)  # 200,000 × 0.1 / 1000
    assert adr.premium_pct == pytest.approx(0.0)
    gdr = next(r for r in v.listings if r.symbol == "SMSN.IL")
    assert gdr.fair == pytest.approx(2500.0) and gdr.premium_pct == pytest.approx(10.0)
    fra = next(r for r in v.listings if r.symbol == "HY9H.F")
    assert fra.unit == "€" and fra.fair == pytest.approx(200_000 / 1200, rel=1e-3)
    assert v.usdkrw == 1000.0


def test_missing_domestic_leaves_none() -> None:
    v = build(quotes(), {}, {})
    smsn = next(r for r in v.perps if r.symbol == "xyz:SMSN")
    assert smsn.vs_close_pct is None and smsn.vs_label is None
    adr = next(r for r in v.listings if r.symbol == "SKHY")
    assert adr.premium_pct is None and adr.fair is None
    assert next(r for r in v.futures if r.symbol == "NQ=F").change_pct == pytest.approx(1.0)
    assert next(r for r in v.futures if r.symbol == "ES=F").error == "받은 값 없음"


def test_yahoo_symbols_cover_everything() -> None:
    syms = yahoo_symbols()
    assert {"SKHY", "SMSN.IL", "HY9H.F", "KRW=X", "EURUSD=X", "NQ=F", "EWY"} <= set(syms)
    assert list(syms) == sorted(syms) and not any(s.startswith("xyz:") for s in syms)
