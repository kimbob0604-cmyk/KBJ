"""GEX·DEX 부호·단위 고정 (CLAUDE.md, PLAN §2.4, docs/metrics.md §2).

naive 딜러: 고객 콜 매도·풋 매수 → 딜러 콜 롱·풋 숏.
- `GEX_call(K) = +Γ·OI·250,000·F²·0.01`, `GEX_put(K) = −Γ·OI·250,000·F²·0.01` (원/1%, 표시 억원)
- `DEX = Σ OI_c·Δ_c·m·F − Σ OI_p·Δ_p·m·F` — 풋 Δ 가 음수라 딜러 풋 숏의 델타는 양수

부호를 바꾸는 변경은 여기서 실패해야 한다.
"""

from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal

import pytest

from core import black76
from core.calendar import KST, expiry_at
from core.forward import ForwardResult, time_to_expiry
from core.gex import (
    OPTION_MULTIPLIER,
    CallPut,
    ExpiryEval,
    OptionEval,
    OptionQuote,
    dex,
    evaluate_expiry,
    net_gex,
    option_dex,
    option_gex,
    strike_gex,
    to_eok,
)
from core.greeks import Greeks
from core.iv import IvResult

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=KST)
EXP = date(2026, 10, 8)
F = 1100.0
STRIKES = (1080, 1090, 1100, 1110, 1120)


def _made(rows: Iterable[tuple[int, CallPut, float, float, int]], f: float = F) -> ExpiryEval:
    """(행사가, 콜/풋, 감마, 델타, OI) 로 그릭스를 직접 넣은 만기 — 역산 오차 없이 부호만 본다."""
    iv = IvResult(0.2, "ok", "model", None)
    opts: list[OptionEval] = []
    for k, cp, gamma, delta, oi in rows:
        q = OptionQuote(
            expiry="202610", expiry_date=EXP, strike=Decimal(k), cp=cp, last=Decimal(1), oi=oi
        )
        opts.append(OptionEval(q, q.price(), iv, Greeks(delta, gamma, 0.0, 0.0), False, None))
    fwd = ForwardResult(f, "ok", Decimal(1100), (Decimal(1100),), None, ())
    return ExpiryEval("202610", EXP, fwd, 7 / 365, tuple(opts))


def _chain(oi_c: int, oi_p: int) -> ExpiryEval:
    """F = 1100, σ = 20% 로 값을 매긴 체인을 실제 흐름(가격 → F → IV → 그릭스)으로 평가."""
    t = time_to_expiry(NOW, expiry_at(EXP))
    qs = [
        OptionQuote(
            expiry="202610",
            expiry_date=EXP,
            strike=Decimal(k),
            cp=cp,
            last=Decimal(repr(black76.price("c" if cp == "C" else "p", F, k, t, 0.2))),
            oi=oi_c if cp == "C" else oi_p,
        )
        for k in STRIKES
        for cp in ("C", "P")
    ]
    return evaluate_expiry(qs, NOW, F)


# --- 단위 계산 예시 ---


def test_worked_example_value() -> None:
    # Γ = 0.001, OI = 1,000, F = 1,100, m = 250,000
    # 0.001 × 1000 × 250000 × 1100² × 0.01 = 3,025,000,000원 = 30.25억원 / 1%
    assert OPTION_MULTIPLIER == 250_000
    call = option_gex("C", 0.001, 1000, 1100.0)
    put = option_gex("P", 0.001, 1000, 1100.0)
    assert call == pytest.approx(3.025e9, rel=1e-12)
    assert put == pytest.approx(-3.025e9, rel=1e-12)
    assert to_eok(call) == pytest.approx(30.25, rel=1e-12)
    assert to_eok(put) == pytest.approx(-30.25, rel=1e-12)


def test_worked_example_through_strike_rows() -> None:
    ev = _made([(1100, "C", 0.001, 0.5, 1000), (1100, "P", 0.001, -0.5, 1000)])
    (row,) = strike_gex(ev).rows
    assert row.gex_call == pytest.approx(3.025e9, rel=1e-12)
    assert row.gex_put == pytest.approx(-3.025e9, rel=1e-12)
    assert row.gex == 0.0


def test_dex_worked_example() -> None:
    # 1000 × 0.5 × 250000 × 1100 = 1,375억원. 풋 Δ −0.5 도 딜러 쪽은 +1,375억원
    assert option_dex("C", 0.5, 1000, 1100.0) == pytest.approx(1.375e11, rel=1e-12)
    assert option_dex("P", -0.5, 1000, 1100.0) == pytest.approx(1.375e11, rel=1e-12)


# --- GEX 부호 ---


def test_call_only_oi_is_positive() -> None:
    ev = _chain(oi_c=1000, oi_p=0)
    assert all(r.gex_call > 0 and r.gex_put == 0 and r.gex > 0 for r in strike_gex(ev).rows)
    net = net_gex([ev]).value
    assert net is not None and net > 0


def test_put_only_oi_is_negative() -> None:
    ev = _chain(oi_c=0, oi_p=1000)
    assert all(r.gex_put < 0 and r.gex_call == 0 and r.gex < 0 for r in strike_gex(ev).rows)
    net = net_gex([ev]).value
    assert net is not None and net < 0


def test_equal_gamma_and_oi_cancel() -> None:
    ev = _made([(1100, "C", 0.0123, 0.5, 700), (1100, "P", 0.0123, -0.5, 700)])
    (row,) = strike_gex(ev).rows
    assert row.gex_call > 0 and row.gex_put == -row.gex_call
    assert row.gex == 0.0
    assert net_gex([ev]).value == 0.0


def test_equal_oi_cancels_through_pipeline() -> None:
    # 같은 K·T·σ 면 콜·풋 감마가 같다 — 역산 오차만큼만 남는다
    ev = _chain(oi_c=1000, oi_p=1000)
    for r in strike_gex(ev).rows:
        assert r.gex == pytest.approx(0.0, abs=1e-9 * r.gex_call)


# --- DEX 부호 ---


def test_dex_call_only_is_positive() -> None:
    res = dex([_chain(oi_c=1000, oi_p=0)])
    assert res.value is not None and res.value > 0


def test_dex_put_only_is_positive() -> None:
    # 딜러 풋 숏: −OI·Δ_p·m·F 이고 Δ_p < 0 이라 양수
    ev = _chain(oi_c=0, oi_p=1000)
    assert all(o.greeks is not None and o.greeks.delta < 0 for o in ev.options if o.quote.cp == "P")
    res = dex([ev])
    assert res.value is not None and res.value > 0
