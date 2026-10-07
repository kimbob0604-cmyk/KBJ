"""core.gex — 만기 평가(§1.1~§1.6 연결), 행사가별 GEX(§2.1), 순GEX 범위·품질(§2.2), DEX(§2.3).

부호 규칙 자체는 tests/test_sign_conventions.py 가 고정한다.
"""

import ast
import math
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from core import black76
from core.calendar import KST, expiry_at
from core.forward import (
    MINUTES_PER_YEAR,
    T_FLOOR_MINUTES,
    ForwardResult,
    FuturesRef,
    confirm_basis,
    kis_time_to_expiry,
    time_to_expiry,
)
from core.gex import (
    EOK,
    OPTION_MULTIPLIER,
    CallPut,
    ExpiryEval,
    OptionEval,
    OptionQuote,
    StrikeGex,
    StrikeGexTable,
    dex,
    evaluate_expiry,
    net_gex,
    option_dex,
    option_gex,
    select_scope,
    strike_gex,
    to_eok,
)
from core.greeks import Greeks, greeks
from core.iv import IvResult
from core.preprocess import PriceChoice

D = Decimal
NOW = datetime(2026, 10, 1, 9, 0, tzinfo=KST)
EXP = date(2026, 10, 8)  # 월물 202610
T = time_to_expiry(NOW, expiry_at(EXP))
F0 = 1100.0
STRIKES = (1090, 1095, 1100, 1105, 1110)


def quote(strike: float | str, cp: CallPut, **kw: Any) -> OptionQuote:
    base: dict[str, Any] = {
        "expiry": "202610",
        "expiry_date": EXP,
        "strike": strike,
        "cp": cp,
        "oi": 1000,
    }
    return OptionQuote(**(base | kw))


def priced(
    strike: float, cp: CallPut, sigma: float = 0.2, f: float = F0, ed: date = EXP, **kw: Any
) -> OptionQuote:
    """Black-76 로 값을 매긴 last 가격 행 — 역산하면 sigma 가 돌아온다."""
    t = time_to_expiry(NOW, expiry_at(ed))
    p = black76.price("c" if cp == "C" else "p", f, strike, t, sigma)
    return quote(strike, cp, last=D(repr(p)), expiry_date=ed, **kw)


def chain(oi_c: int = 1000, oi_p: int = 1000, **kw: Any) -> list[OptionQuote]:
    return [priced(k, "C", oi=oi_c, **kw) for k in STRIKES] + [
        priced(k, "P", oi=oi_p, **kw) for k in STRIKES
    ]


# --- 입력 모델 ---


def test_quote_normalizes_inputs() -> None:
    q = quote("1100", "C", bid="", ask=1.2, last=D("1.30"), kis_iv_pct=D("20.5"))
    assert (q.strike, q.bid, q.ask, q.last) == (D(1100), None, D("1.2"), D("1.30"))
    assert q.kis_iv_pct == 20.5
    assert q.source == "board"
    assert hash(q) == hash(quote("1100", "C", ask=D("1.2"), last=D("1.30"), kis_iv_pct=20.5))


@pytest.mark.parametrize(
    "bad",
    [
        {"expiry": "20261"},
        {"expiry": "2026-10"},
        {"strike": 0},
        {"strike": ""},
        {"cp": "c"},
        {"bid": -1},
        {"bid": True},
        {"last": "abc"},
        {"last": float("nan")},
        {"oi": -1},
        {"oi": True},
        {"oi": 10.0},
        {"oi": "-1"},
        {"oi": "1.0"},
        {"oi": "1e3"},
        {"oi": "1,000"},
        {"oi": " 10"},
        {"oi": ""},
        {"oi": "１０"},  # 전각 숫자 — str.isdigit 는 참이지만 KIS 형식이 아니다
        {"expiry_date": "2026-10-08T00:00:00"},  # ISO 는 날짜만 — 시각이 붙으면 거부
        {"expiry_date": "2026-10-08T00:00:00+09:00"},
        {"expiry_date": "2026-1-8"},
        {"expiry_date": "2026-02-30"},  # 없는 날짜
        {"expiry_date": "2026108"},
        {"expiry_date": "20261332"},  # 없는 날짜
        {"expiry_date": ""},
        {"expiry_date": 20261008},
        {"expiry_date": 1_791_417_600},  # pydantic 기본은 Unix 시각으로 읽는다
        {"expiry_date": datetime(2026, 10, 8, tzinfo=KST)},
        {"kis_iv_pct": True},
        {"volume": -1},
        {"volume": True},
        {"volume": 1.5},
        {"volume": "1.5"},
        {"volume": "-1"},
        {"source": "rest"},
        {"extra": 1},
    ],
)
def test_quote_rejects_bad_inputs(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        quote(**({"strike": 1100, "cp": "C"} | bad))


def test_quote_accepts_kis_strings() -> None:
    # KIS 는 모든 값을 문자열로 준다 — 수량은 숫자만, 날짜는 YYYYMMDD
    q = quote("1100", "C", oi="0012", expiry_date="20261008")
    assert (q.oi, q.expiry_date) == (12, EXP)
    assert q == quote(1100, "C", oi=12)
    assert quote(1100, "C", oi="0").oi == 0
    # 거래량: "0" 은 '거래 없음' 이라는 값, 빈 값은 모름
    assert [quote(1100, "C", volume=v).volume for v in ("0", "0034", "", 7, None)] == [
        0,
        34,
        None,
        7,
        None,
    ]


@pytest.mark.parametrize(
    "q",
    [
        quote("1097.5", "P", bid="1.00", ask="1.05", last=D("1.02"), kis_iv_pct=20.5, volume=0),
        quote(1100, "C", last=D("12.345678901234567"), expiry_date="20261008", volume="0034"),
        quote(1100, "C", source="fill", last=D("3.10"), volume=None, oi="0"),
    ],
)
def test_quote_json_round_trip(q: OptionQuote) -> None:
    # model_dump_json 은 날짜를 ISO(2026-10-08)로 쓴다 — 그대로 되읽혀야 한다(검토 GEXFIX-R1)
    text = q.model_dump_json()
    assert '"expiry_date":"2026-10-08"' in text
    assert OptionQuote.model_validate_json(text) == q
    assert OptionQuote.model_validate(q.model_dump(mode="json")) == q
    assert quote(1100, "C", expiry_date="2026-10-08").expiry_date == EXP


@pytest.mark.parametrize(
    ("raw", "ok"),
    [
        ('"20261008"', True),  # KIS 문자열
        ('"2026-10-08"', True),  # ISO 날짜
        ("20261008", False),  # JSON 정수
        ("1791417600", False),  # Unix 시각
        ('"2026-10-08T00:00:00"', False),
        ('"2026-10-08T00:00:00Z"', False),
    ],
)
def test_quote_json_expiry_date_forms(raw: str, ok: bool) -> None:
    text = f'{{"expiry":"202610","expiry_date":{raw},"strike":"1100","cp":"C","oi":1}}'
    if ok:
        assert OptionQuote.model_validate_json(text).expiry_date == EXP
    else:
        with pytest.raises(ValidationError):
            OptionQuote.model_validate_json(text)


def test_quote_is_frozen() -> None:
    q = quote(1100, "C")
    with pytest.raises(ValidationError):
        q.oi = 5  # pyright: ignore[reportAttributeAccessIssue]


def test_fill_row_uses_last_only() -> None:
    kw: dict[str, Any] = {"bid": D("1.00"), "ask": D("1.01"), "last": D("1.50")}
    assert quote(1100, "C", **kw).price() == PriceChoice(D("1.005"), "mid")
    assert quote(1100, "C", source="fill", **kw).price() == PriceChoice(D("1.50"), "last")


def test_price_marks_prev_session_last() -> None:
    # 당일 거래량 0 인 last 는 전 세션 가격(§1.1) — 호가 mid 와 거래량 모름은 아니다
    kw: dict[str, Any] = {"bid": D("1.00"), "ask": D("1.01"), "last": D("1.50")}
    prev = PriceChoice(D("1.50"), "last", prev_session=True)
    assert quote(1100, "C", source="fill", volume=0, **kw).price() == prev
    assert quote(1100, "C", last=D("1.50"), volume="0").price() == prev
    assert not quote(1100, "C", volume=0, **kw).price().prev_session  # mid
    assert not quote(1100, "C", last=D("1.50"), volume=3).price().prev_session
    assert not quote(1100, "C", last=D("1.50")).price().prev_session


# --- 만기 평가 ---


def test_evaluate_expiry_round_trip() -> None:
    ev = evaluate_expiry(reversed(chain()), NOW, 1100.0)
    assert (ev.expiry, ev.expiry_date, ev.T) == ("202610", EXP, T)
    assert ev.F == pytest.approx(F0, abs=1e-9)
    assert ev.forward.quality == "ok"
    assert ev.quality == "ok"
    # 행사가 오름차순, 같은 행사가는 콜 먼저
    assert [(o.quote.strike, o.quote.cp) for o in ev.options] == [
        (D(k), cp) for k in STRIKES for cp in ("C", "P")
    ]
    for o in ev.options:
        assert not o.excluded and o.reason is None
        assert o.iv is not None and o.iv.sigma == pytest.approx(0.2, rel=1e-9)
        assert (o.iv.source, o.iv.rescaled, o.iv.t_kis) == ("model", False, None)
        assert o.greeks is not None and o.greeks.gamma > 0


def test_mid_price_feeds_iv() -> None:
    qs = chain()
    qs.append(quote(1150, "C", bid=D("0.50"), ask=D("0.52"), last=D("0.40")))
    ev = evaluate_expiry(qs, NOW, 1100.0)
    o = next(o for o in ev.options if o.quote.strike == D(1150))
    assert o.choice == PriceChoice(D("0.51"), "mid")
    assert not o.excluded


def test_kis_fallback_is_included_as_estimated() -> None:
    # 내재가치(100) 아래 가격 → KIS 20% 폴백 → T_KIS(7일)에서 자체 T(7일 6시간 20분)로 옮긴
    # sigma 로 그릭스, GEX 에 포함, 품질 estimated
    ev = evaluate_expiry([*chain(), _below_intrinsic_call()], NOW, 1100.0)
    assert ev.T_kis == 7 / 365 == kis_time_to_expiry(NOW, EXP)
    o = next(o for o in ev.options if o.quote.strike == D(1000))
    assert o.iv is not None
    assert (o.iv.quality, o.iv.reason, o.iv.source) == ("estimated", "below_intrinsic", "kis")
    assert (o.iv.rescaled, o.iv.t_kis) == (True, ev.T_kis)
    assert o.iv.sigma == pytest.approx(0.2 * math.sqrt(7 / 365 / T))
    assert not o.excluded and o.greeks is not None
    assert ev.quality == "estimated"
    net = net_gex([ev])
    assert (net.quality, net.excluded_oi_ratio) == ("estimated", 0.0)
    base = net_gex([evaluate_expiry(chain(), NOW, 1100.0)])
    assert net.value is not None and base.value is not None
    assert ev.F is not None
    assert net.value - base.value == pytest.approx(option_gex("C", o.greeks.gamma, 1000, ev.F))


# --- KIS IV 폴백의 T 환산 (§1.5·§1.7 — 2026-09-28 사용자 결정, 검증 수정 3) ---

EXP_0DTE = date(2026, 9, 28)  # WKM 260904 — validation_greeks §5.3 의 0DTE
NOW_0DTE = datetime(2026, 9, 28, 14, 27, tzinfo=KST)  # 15:20 까지 53분


def _quote_0dte(strike: float, cp: CallPut, **kw: Any) -> OptionQuote:
    return quote(strike, cp, expiry="260904", expiry_date=EXP_0DTE, **kw)


def _chain_0dte(sigma: float = 0.5, now: datetime = NOW_0DTE) -> list[OptionQuote]:
    t = time_to_expiry(now, expiry_at(EXP_0DTE))
    return [
        _quote_0dte(k, cp, last=D(repr(black76.price("c" if cp == "C" else "p", F0, k, t, sigma))))
        for k in STRIKES
        for cp in ("C", "P")
    ]


def _below_intrinsic_call() -> OptionQuote:
    """내재가치(100) 아래 가격 → 역산 실패 → KIS 20% 폴백(§1.5)."""
    return quote(1000, "C", last=D("99.00"), kis_iv_pct=20.0)


def test_0dte_kis_fallback_sigma_moves_to_own_t() -> None:
    """0DTE: KIS σ 는 T_KIS 0.5일 기준이라 자체 T(53분)에 그대로 쓰면 감마가 사실상 0 이다
    (validation_greeks §5.3). σ_KIS·√(T_KIS/T) 로 옮긴 σ 의 델타·감마는 (σ_KIS, T_KIS) 값과 같다."""
    fb = _quote_0dte(1112.5, "P", last=D("12.00"), kis_iv_pct=16.0)  # 내재가치 12.5 아래
    ev = evaluate_expiry([*_chain_0dte(), fb], NOW_0DTE, 1100.0)
    F = ev.F
    assert F is not None and F == pytest.approx(F0, abs=1e-9)
    assert (ev.T, ev.T_kis) == (53 / MINUTES_PER_YEAR, 0.5 / 365)
    o = next(o for o in ev.options if o.quote == fb)
    assert o.iv is not None and o.greeks is not None and not o.excluded
    assert (o.iv.quality, o.iv.reason, o.iv.source) == ("estimated", "below_intrinsic", "kis")
    assert (o.iv.rescaled, o.iv.t_kis) == (True, 0.5 / 365)
    assert o.iv.sigma == pytest.approx(0.16 * math.sqrt(12 * 60 / 53))
    at_kis_t = greeks("p", F, 1112.5, 0.5 / 365, 0.16)
    assert o.greeks.gamma == pytest.approx(at_kis_t.gamma)
    assert o.greeks.delta == pytest.approx(at_kis_t.delta)
    assert greeks("p", F, 1112.5, ev.T, 0.16).gamma < 1e-6 * o.greeks.gamma  # 옮기기 전
    # 자체 역산 종목은 옮기지 않는다
    for x in ev.options:
        if x is not o:
            assert x.iv is not None
            assert (x.iv.source, x.iv.rescaled, x.iv.t_kis) == ("model", False, None)


def test_kis_fallback_identity_when_t_kis_equals_own_t() -> None:
    """만기 D 일 전 15:20 KST 정각이면 자체 T(D × 1440분)와 T_KIS(D/365)가 같다 → σ_KIS 그대로."""
    now = datetime(2026, 10, 7, 15, 20, tzinfo=KST)
    ev = evaluate_expiry([*chain(), _below_intrinsic_call()], now, 1100.0)
    assert ev.T == ev.T_kis == 1 / 365
    o = next(o for o in ev.options if o.quote.strike == D(1000))
    assert o.iv is not None and o.greeks is not None
    assert (o.iv.source, o.iv.sigma, o.iv.rescaled, o.iv.t_kis) == ("kis", 0.2, False, 1 / 365)


def test_expired_expiry_keeps_kis_sigma() -> None:
    """만기일이 지난 만기(§1.4 — 상위에서 빠질 만기)는 KIS 관례 T 가 없다 → T_KIS None, KIS σ 그대로
    [확인 필요]. 평가는 에러 없이 끝난다(자체 T 는 하한 5분)."""
    now = datetime(2026, 10, 9, 9, 0, tzinfo=KST)
    ev = evaluate_expiry([*chain(), _below_intrinsic_call()], now, 1100.0)
    assert (ev.T, ev.T_kis) == (T_FLOOR_MINUTES / MINUTES_PER_YEAR, None)
    o = next(o for o in ev.options if o.quote.strike == D(1000))
    assert o.iv is not None
    assert (o.iv.source, o.iv.sigma, o.iv.rescaled, o.iv.t_kis) == ("kis", 0.2, False, None)


def test_fallback_dropped_by_rescale_is_excluded_with_record() -> None:
    """0DTE 15:18 KST — 자체 T 는 하한 5분, T_KIS 0.5일이라 배율 √(720/5) = 12. KIS 26% 는 옮기면
    312% 라 §6.1 이상치로 GEX 에서 빠진다(OI 는 제외 비율 분자). 빠진 행도 폴백 σ 를 옮겼다는 기록
    (t_kis·rescaled)과 옮긴 탓이라는 사유(`kis_out_of_range_rescaled`)를 남긴다(§1.5 기록)."""
    now = datetime(2026, 9, 28, 15, 18, tzinfo=KST)
    fb = _quote_0dte(1112.5, "P", last=D("12.00"), kis_iv_pct=26.0, oi=500)  # 내재가치 12.5 아래
    ev = evaluate_expiry([*_chain_0dte(now=now), fb], now, 1100.0)
    assert (ev.T, ev.T_kis) == (T_FLOOR_MINUTES / MINUTES_PER_YEAR, 0.5 / 365)
    o = next(o for o in ev.options if o.quote == fb)
    assert (o.excluded, o.reason, o.greeks) == (True, "iv_invalid", None)
    assert o.iv is not None
    assert (o.iv.quality, o.iv.source) == ("invalid", None)
    assert o.iv.reason == "below_intrinsic/kis_out_of_range_rescaled"
    assert (o.iv.rescaled, o.iv.t_kis) == (True, 0.5 / 365)
    base = net_gex(
        [evaluate_expiry(_chain_0dte(now=now), now, 1100.0)]
    )  # 5분이면 깊은 ITM 도 빠진다
    net = net_gex([ev])
    assert net.value == base.value
    assert net.excluded_oi_ratio == pytest.approx((base.excluded_oi_ratio * 10_000 + 500) / 10_500)


def test_invalid_iv_is_excluded_but_counted() -> None:
    qs = [*chain(), quote(1000, "C", last=D("99.00"), oi=500)]  # KIS 없음 → invalid
    ev = evaluate_expiry(qs, NOW, 1100.0)
    o = next(o for o in ev.options if o.quote.strike == D(1000))
    assert (o.excluded, o.reason, o.greeks) == (True, "iv_invalid", None)
    assert o.iv is not None and o.iv.quality == "invalid"
    assert ev.quality == "ok"  # 제외 종목 IV 품질은 비율로만 반영
    net = net_gex([ev])
    assert net.excluded_oi_ratio == 500 / 10_500
    assert net.quality == "ok"  # 4.8% ≤ 10%
    table = strike_gex(ev)
    assert (table.quality, table.excluded_oi_ratio) == ("ok", 500 / 10_500)
    row = next(r for r in table.rows if r.strike == D(1000))
    assert row == StrikeGex(D(1000), 0.0, 0.0)


@pytest.mark.parametrize(
    ("last", "excluded"),
    [("0.019", True), ("0.02", False)],
)
def test_min_premium_boundary(last: str, excluded: bool) -> None:
    qs = [*chain(), quote(1300, "C", last=D(last), kis_iv_pct=30.0, oi=200)]
    ev = evaluate_expiry(qs, NOW, 1100.0)
    o = next(o for o in ev.options if o.quote.strike == D(1300))
    assert o.excluded is excluded
    assert o.iv is not None and o.iv.below_min_premium is excluded
    if excluded:
        # KIS 로 sigma 는 채워지지만(표시용) 그릭스는 없고 GEX 에서 빠진다
        assert (o.reason, o.iv.quality, o.greeks) == ("below_min_premium", "estimated", None)
        assert net_gex([ev]).excluded_oi_ratio == 200 / 10_200
    else:
        assert o.iv.quality == "ok" and o.greeks is not None
        assert net_gex([ev]).excluded_oi_ratio == 0.0


def test_min_premium_is_a_parameter() -> None:
    qs = [*chain(), quote(1300, "C", last=D("0.03"))]
    ev = evaluate_expiry(qs, NOW, 1100.0, min_premium=0.05)
    assert next(o for o in ev.options if o.quote.strike == D(1300)).reason == "below_min_premium"


def test_no_price_is_excluded() -> None:
    qs = [*chain(), quote(1200, "P", oi=300), quote(1200, "C", bid=D("0.10"), oi=100)]
    ev = evaluate_expiry(qs, NOW, 1100.0)
    for o in ev.options:
        if o.quote.strike == D(1200):
            assert (o.excluded, o.reason, o.iv) == (True, "no_price", None)
            assert o.choice == PriceChoice(None, None)
    assert net_gex([ev]).excluded_oi_ratio == 400 / 10_400


def test_missing_forward_makes_expiry_invalid() -> None:
    # 콜만 있어 C − P + K 를 못 만든다
    ev = evaluate_expiry([priced(k, "C") for k in STRIKES], NOW, 1100.0)
    assert (ev.F, ev.forward.quality, ev.quality) == (None, "invalid", "invalid")
    assert {o.reason for o in ev.options} == {"no_forward"}
    assert strike_gex(ev) == StrikeGexTable((), "invalid", 1.0)
    net = net_gex([ev])
    assert (net.value, net.quality, net.excluded_oi_ratio) == (None, "invalid", 1.0)
    assert dex([ev]).value is None


def test_missing_forward_in_scope_makes_total_invalid() -> None:
    good = evaluate_expiry(chain(), NOW, 1100.0)
    bad = evaluate_expiry(
        [priced(k, "C", ed=date(2026, 10, 6), expiry="261002") for k in STRIKES], NOW, 1100.0
    )
    net, alone = net_gex([good, bad]), net_gex([good])
    assert net.quality == "invalid"
    assert net.value == alone.value  # 값은 F 있는 만기만의 합


def test_all_excluded_with_forward_is_null_invalid() -> None:
    # F 는 있는데 모든 종목이 §1.2 로 빠진다 — 순GEX 는 0 이 아니라 모른다
    ev = evaluate_expiry(chain(), NOW, 1100.0, min_premium=1e6)
    assert ev.F is not None and ev.forward.quality == "ok"
    assert {o.reason for o in ev.options} == {"below_min_premium"}
    for fn in (net_gex, dex):
        res = fn([ev])
        assert (res.value, res.quality, res.excluded_oi_ratio) == (None, "invalid", 1.0)


def test_zero_included_oi_is_null_invalid() -> None:
    # 포함 종목은 있지만 OI 가 모두 0 이고, OI 는 가격 없는 종목에만 있다
    ev = evaluate_expiry([*chain(oi_c=0, oi_p=0), quote(1200, "C", oi=100)], NOW, 1100.0)
    assert any(not o.excluded for o in ev.options)
    res = net_gex([ev])
    assert (res.value, res.quality, res.excluded_oi_ratio) == (None, "invalid", 1.0)


def test_no_oi_at_all_is_zero_not_null() -> None:
    # OI 가 전혀 없으면 노출이 정말 0 이다 — 제외 여부와 무관
    for ev in (
        evaluate_expiry(chain(oi_c=0, oi_p=0), NOW, 1100.0),
        evaluate_expiry(chain(oi_c=0, oi_p=0), NOW, 1100.0, min_premium=1e6),
    ):
        for fn in (net_gex, dex):
            res = fn([ev])
            assert (res.value, res.quality, res.excluded_oi_ratio) == (0.0, "ok", 0.0)


def test_one_expiry_all_excluded_in_wider_scope() -> None:
    # 범위 전체로는 포함 OI 가 있어 값이 난다 — 빠진 만기 몫은 제외 비율(> 10% → estimated)로
    good = evaluate_expiry(chain(), NOW, 1100.0)
    gone = evaluate_expiry(
        chain(ed=date(2026, 10, 6), expiry="261002"), NOW, 1100.0, min_premium=1e6
    )
    both, alone = net_gex([good, gone]), net_gex([good])
    assert (both.value, both.quality, both.excluded_oi_ratio) == (alone.value, "estimated", 0.5)
    assert net_gex([good, gone], max_excluded_ratio=0.5).quality == "ok"
    assert net_gex([good, gone], "nearest").value is None  # 261002(10-06)가 최근접


def test_forward_quality_propagates() -> None:
    few = [priced(k, cp) for k in (1100, 1105) for cp in ("C", "P")]
    assert evaluate_expiry(few, NOW, 1100.0).quality == "estimated"  # 2 개 < 3 [확인 필요]
    assert evaluate_expiry(few, NOW, 1100.0, min_forward_strikes=2).quality == "ok"


def test_quarterly_futures_gap() -> None:
    dec = date(2026, 12, 10)
    qs = chain(ed=dec, expiry="202612")
    far = evaluate_expiry(qs, NOW, 1100.0, D("1101.00"))
    assert (far.forward.quality, far.forward.reasons, far.quality) == (
        "estimated",
        ("futures_gap",),
        "estimated",
    )
    assert far.forward.reference == FuturesRef(D("1101.00"), "same_month")
    near = evaluate_expiry(qs, NOW, 1100.0, 1100.3)
    assert near.quality == "ok"
    assert near.forward.futures_gap == pytest.approx(-0.3, abs=1e-9)
    # 2pt 를 넘으면 §6.1 규칙 사유도 함께 선다
    very_far = evaluate_expiry(qs, NOW, 1100.0, D("1103.00"))
    assert very_far.forward.reasons == ("futures_gap", "futures_ref_gap")


# --- 선물 교차 확인: 근월물 + 확정 베이시스·대체 (§1.3, 검증 수정 2) ---


def test_basis_reference_for_non_quarterly_expiry() -> None:
    # 10월물은 같은 결제월 선물이 없다 — 기준가 = 근월물(s_ref) + 확정 베이시스
    first = evaluate_expiry(chain(), NOW, 1105.0)
    assert (first.forward.quality, first.forward.notes) == ("ok", ("no_futures_ref",))
    basis = confirm_basis(first.forward, 1105.0)  # 첫 ok F 로 확정
    assert basis is not None and float(basis) == pytest.approx(-5.0, abs=1e-9)
    # 근월물이 1pt 내리고 옵션은 그대로 — F − 기준가 = +1.0, 2pt 안
    ok = evaluate_expiry(chain(), NOW, 1104.0, forward_basis=basis)
    assert ok.forward.reference is not None and ok.forward.reference.kind == "near_basis"
    assert (ok.quality, ok.forward.notes) == ("ok", ())
    assert ok.forward.futures_gap == pytest.approx(1.0, abs=1e-9)
    # 3pt 내리면 +3.0 → estimated, 베이시스는 그대로 둔다(ok F 로만 갱신)
    far = evaluate_expiry(chain(), NOW, 1102.0, forward_basis=basis)
    assert (far.forward.reasons, far.quality) == (("futures_ref_gap",), "estimated")
    assert net_gex([far]).quality == "estimated"
    assert confirm_basis(far.forward, 1102.0, basis) == basis
    loose = evaluate_expiry(chain(), NOW, 1102.0, forward_basis=basis, futures_ref_tolerance=3.5)
    assert loose.quality == "ok"


def test_fallback_forward_feeds_iv_and_gex() -> None:
    # ATM±2 에서 콜·풋이 다 있는 행사가가 1100 하나 — F = 기준가(1100 + 1.0), 그 F 로 IV·그릭스
    one = [priced(1100, "C"), priced(1100, "P"), priced(1095, "C"), priced(1105, "P")]
    ev = evaluate_expiry(one, NOW, 1100.0, forward_basis=1.0)
    assert (ev.F, ev.forward.reasons, ev.forward.strikes) == (1101.0, ("futures_fallback",), ())
    assert ev.quality == "estimated"
    included = [o for o in ev.options if not o.excluded]
    assert included
    for o in included:
        assert o.iv is not None and o.iv.sigma is not None
        flag = "c" if o.quote.cp == "C" else "p"
        assert o.greeks == greeks(flag, 1101.0, float(o.quote.strike), ev.T, o.iv.sigma)
    net = net_gex([ev])
    assert net.value is not None and net.quality == "estimated"
    # 기준가가 없으면 전과 같다 — 1100 하나로 few_strikes
    old = evaluate_expiry(one, NOW, 1100.0)
    assert old.forward.reasons == ("few_strikes",)
    assert old.F == pytest.approx(F0, abs=1e-9)


def test_prev_session_rows_fall_back_to_reference() -> None:
    # ATM±2 다섯 중 넷이 전 세션 가격(§1.1 규칙 그대로) — 남은 1110 하나라 기준가로 대체
    qs = [
        priced(k, cp, volume=7) if k == 1110 else priced(k, cp, f=YESTERDAY_F, volume=0)
        for k in STRIKES
        for cp in ("C", "P")
    ]
    ev = evaluate_expiry(qs, NOW, 1100.0, forward_basis=D("0.50"))
    assert (ev.F, ev.forward.reasons) == (1100.5, ("futures_fallback",))
    assert ev.forward.prev_session_skipped == tuple(D(k) for k in STRIKES[:-1])
    alone = evaluate_expiry(qs, NOW, 1100.0)
    assert (alone.forward.reasons, alone.forward.strikes) == (("few_strikes",), (D(1110),))
    assert alone.F == pytest.approx(F0, abs=1e-9)


def test_basis_carry_age_reaches_the_forward_rule() -> None:
    # §1.3 이월 상한(2026-09-29 사용자 결정): 2거래일까지 대체 + basis_carried, 넘으면 invalid
    stale = [priced(k, cp, f=YESTERDAY_F, volume=0) for k in STRIKES for cp in ("C", "P")]
    two = evaluate_expiry(stale, NOW, 1100.0, forward_basis=D("0.50"), forward_basis_age=2)
    assert (two.F, two.forward.reasons) == (1100.5, ("futures_fallback", "basis_carried"))
    three = evaluate_expiry(stale, NOW, 1100.0, forward_basis=D("0.50"), forward_basis_age=3)
    assert (three.F, three.quality, three.forward.reasons) == (
        None,
        "invalid",
        ("basis_carry_expired",),
    )
    assert net_gex([three]).value is None
    looser = evaluate_expiry(
        stale, NOW, 1100.0, forward_basis=D("0.50"), forward_basis_age=3, max_basis_carry=3
    )
    assert looser.F == 1100.5


def test_all_prev_session_expiry_gets_forward_only_with_reference() -> None:
    # validation_greeks §5.6 WKM 261001 꼴 — ATM±2 가 전부 전 세션 가격
    stale = [priced(k, cp, f=YESTERDAY_F, volume=0) for k in STRIKES for cp in ("C", "P")]
    gone = evaluate_expiry(stale, NOW, 1100.0)
    assert (gone.F, gone.quality, net_gex([gone]).value) == (None, "invalid", None)
    ev = evaluate_expiry(stale, NOW, 1100.0, forward_basis=D("0.50"))
    assert (ev.F, ev.forward.quality, ev.quality) == (1100.5, "estimated", "estimated")
    assert len(ev.forward.prev_session_skipped) == len(STRIKES)
    net = net_gex([ev])
    assert net.value is not None and net.quality == "estimated"


def test_master_strikes_choose_atm() -> None:
    ev = evaluate_expiry(chain(), NOW, 1112.0, strikes=[D(k) for k in (*STRIKES, 1115, 1120)])
    assert ev.forward.atm == D(1110)
    assert ev.forward.strikes == (D(1100), D(1105), D(1110))


@pytest.mark.parametrize(
    ("quotes", "kw"),
    [
        ([], {}),
        ([quote(1100, "C"), quote(1100, "C", oi=5)], {}),  # 중복
        ([quote(1100, "C"), quote(1100, "P", expiry="261001")], {}),  # 만기 섞임
        ([quote(1100, "C"), quote(1100, "P", expiry_date=date(2026, 10, 9))], {}),
        (chain(), {"quarterly_futures_price": 1100.0}),  # 10월물은 분기 월물 아님
        # 분기월(12월)의 위클리도 같은 결제월 선물가를 받지 않는다 [확인 필요]
        (chain(ed=date(2026, 12, 3), expiry="261201"), {"quarterly_futures_price": 1100.0}),
        (chain(), {"forward_basis": -1100.0}),  # 기준가 0 이하
        (chain(), {"min_parity_strikes": 0}),
    ],
)
def test_evaluate_expiry_rejects(quotes: list[OptionQuote], kw: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        evaluate_expiry(quotes, NOW, 1100.0, **kw)


def test_evaluate_expiry_rejects_naive_now() -> None:
    with pytest.raises(ValueError, match="naive"):
        evaluate_expiry(chain(), datetime(2026, 10, 1, 9, 0), 1100.0)  # noqa: DTZ001


def test_option_eval_invariants() -> None:
    q = quote(1100, "C", last=D("10"))
    iv = IvResult(0.2, "ok", "model", None)
    g = Greeks(0.5, 0.01, 1.0, -1.0)
    with pytest.raises(ValueError):
        OptionEval(q, q.price(), iv, g, True, "iv_invalid")
    with pytest.raises(ValueError):
        OptionEval(q, q.price(), iv, None, False, None)
    with pytest.raises(ValueError):
        OptionEval(q, q.price(), None, None, True, "iv_invalid")
    with pytest.raises(ValueError):
        OptionEval(q, q.price(), iv, None, True, "no_price")


# --- 전 세션 가격 (§1.1·§1.3) [확인 필요] ---

YESTERDAY_F = 1130.0  # 전 세션 기초자산 수준 — validation_greeks §5.4 의 위클리처럼 +30pt


def stale_chain(volume: int | None = 0, mid: bool = False) -> list[OptionQuote]:
    """F0 로 값을 매긴 체인에서 ATM±2 가운데 셋(1095·1100·1105)만 전 세션 가격(YESTERDAY_F 로 매긴
    last)이다. 나머지는 당일 거래가 있다. mid=True 면 그 셋에 좁은 호가(3틱 안)가 있다."""
    out: list[OptionQuote] = []
    for k in STRIKES:
        for cp in ("C", "P"):
            if k in (1095, 1100, 1105):
                q = priced(k, cp, f=YESTERDAY_F, volume=volume)
                if mid:
                    fresh = priced(k, cp).last
                    assert fresh is not None
                    tick = D("0.01")
                    q = q.model_copy(update={"bid": fresh - tick, "ask": fresh + tick})
            else:
                q = priced(k, cp, volume=12)
            out.append(q)
    return out


def test_prev_session_prices_do_not_shift_forward() -> None:
    old = evaluate_expiry(stale_chain(), NOW, 1100.0, prev_session_in_forward=True)
    assert old.F == pytest.approx(YESTERDAY_F, abs=1e-6)  # 세 행사가가 중앙값을 끌어간다
    ev = evaluate_expiry(stale_chain(), NOW, 1100.0)
    assert ev.F == pytest.approx(F0, abs=1e-9)  # 당일 가격 행사가(1090·1110)만
    assert ev.forward.strikes == (D(1090), D(1110))
    assert ev.forward.prev_session_skipped == (D(1095), D(1100), D(1105))
    assert (ev.forward.quality, ev.forward.reasons) == ("estimated", ("few_strikes",))


def test_prev_session_options_stay_in_gex_as_estimated() -> None:
    ev = evaluate_expiry(stale_chain(), NOW, 1100.0)
    # 1105 풋만 빠진다 — 전 세션 가격(F 1130 기준)이 오늘 F 의 내재가치(5pt) 아래라 IV invalid
    gone = [(o.quote.strike, o.quote.cp, o.reason) for o in ev.options if o.excluded]
    assert gone == [(D(1105), "P", "iv_invalid")]
    for o in ev.options:
        stale = o.quote.strike in (D(1095), D(1100), D(1105))
        assert o.choice.prev_session is stale
        assert o.quality_reasons == (("prev_session_last",) if stale else ())
        if o.excluded:
            continue
        assert o.greeks is not None  # IV·그릭스·GEX 에는 쓴다
        assert o.iv is not None and o.iv.quality == "ok"  # 역산 자체는 됐다
        assert o.quality == ("estimated" if stale else "ok")
    assert ev.quality == "estimated"
    net = net_gex([ev])
    assert (net.quality, net.excluded_oi_ratio) == ("estimated", 1000 / 10_000)
    assert strike_gex(ev).quality == "estimated"


def test_prev_session_with_enough_fresh_strikes_keeps_forward_ok() -> None:
    # 전 세션 행사가가 하나뿐이면 F 는 남은 4 개로 ok — 그래도 그 종목 품질 때문에 만기는 estimated
    qs = [priced(k, cp, volume=0 if k == 1100 else 5) for k in STRIKES for cp in ("C", "P")]
    ev = evaluate_expiry(qs, NOW, 1100.0)
    assert (ev.forward.quality, ev.forward.prev_session_skipped) == ("ok", (D(1100),))
    assert ev.F == pytest.approx(F0, abs=1e-9)
    assert ev.quality == "estimated"


def test_unknown_volume_keeps_old_behaviour() -> None:
    ev = evaluate_expiry(stale_chain(volume=None), NOW, 1100.0)
    assert ev.F == pytest.approx(YESTERDAY_F, abs=1e-6)
    assert ev.forward.prev_session_skipped == ()
    assert all(o.quality_reasons == () for o in ev.options)
    assert evaluate_expiry(stale_chain(volume=5), NOW, 1100.0).F == ev.F  # 당일 거래 있음


def test_live_quotes_override_prev_session_last() -> None:
    # 좁은 호가가 있으면 mid 를 쓴다 — 거래량 0 이어도 전 세션 가격이 아니다
    ev = evaluate_expiry(stale_chain(mid=True), NOW, 1100.0)
    assert ev.F == pytest.approx(F0, abs=1e-6)
    assert ev.forward.prev_session_skipped == ()
    assert all(o.choice.kind == "mid" for o in ev.options if o.quote.strike == D(1100))
    assert ev.quality == "ok"


def test_excluded_prev_session_option_quality() -> None:
    # 전 세션 가격이 §1.2 하한 아래라 빠져도 사유는 남는다 — 품질은 IV 품질과 합성
    qs = [*chain(), quote(1300, "C", last=D("0.01"), volume=0, oi=10)]
    o = next(o for o in evaluate_expiry(qs, NOW, 1100.0).options if o.quote.strike == D(1300))
    assert (o.excluded, o.reason, o.quality_reasons) == (
        True,
        "below_min_premium",
        ("prev_session_last",),
    )
    assert o.quality == "invalid"  # KIS 폴백도 없음


# --- 행사가별 GEX ---


def test_strike_gex_rows() -> None:
    ev = evaluate_expiry(chain(oi_c=1000, oi_p=3000), NOW, 1100.0)
    table = strike_gex(ev)
    assert (table.quality, table.excluded_oi_ratio) == ("ok", 0.0)
    rows = table.rows
    assert [r.strike for r in rows] == [D(k) for k in STRIKES]
    assert ev.F is not None
    for r in rows:
        c, p = (o for o in ev.options if o.quote.strike == r.strike)
        assert c.greeks is not None and p.greeks is not None
        assert r.gex_call == option_gex("C", c.greeks.gamma, 1000, ev.F) > 0
        assert r.gex_put == option_gex("P", p.greeks.gamma, 3000, ev.F) < 0
        assert r.gex == r.gex_call + r.gex_put < 0
        # 같은 σ 라 감마가 같다 → 풋 OI 3 배면 GEX = −2 × 콜 GEX
        assert r.gex == pytest.approx(-2 * r.gex_call, rel=1e-9)


@pytest.mark.parametrize(("excluded_oi", "quality"), [(1000, "ok"), (1001, "estimated")])
def test_strike_gex_quality_follows_expiry_aggregate(excluded_oi: int, quality: str) -> None:
    # 행사가별 표도 순GEX 와 같은 규칙 — 제외 비율 10% 경계
    ev = evaluate_expiry(
        [*chain(oi_c=900, oi_p=900), quote(1200, "C", oi=excluded_oi)], NOW, 1100.0
    )
    table = strike_gex(ev)
    assert (table.quality, table.excluded_oi_ratio) == (quality, excluded_oi / (9000 + excluded_oi))
    assert strike_gex(ev, max_excluded_ratio=0.2).quality == "ok"
    net = net_gex([ev])
    assert (table.quality, table.excluded_oi_ratio) == (net.quality, net.excluded_oi_ratio)


def test_strike_gex_quality_composes_inputs() -> None:
    # F estimated(행사가 2 개) → estimated, KIS 폴백 포함 종목 → estimated
    few = [priced(k, cp) for k in (1100, 1105) for cp in ("C", "P")]
    assert strike_gex(evaluate_expiry(few, NOW, 1100.0)).quality == "estimated"
    fb = [*chain(), quote(1000, "C", last=D("99.00"), kis_iv_pct=20.0)]
    assert strike_gex(evaluate_expiry(fb, NOW, 1100.0)).quality == "estimated"


def test_strike_gex_all_excluded_is_invalid() -> None:
    # F 는 있어 행(0)은 남지만 덮은 OI 가 없다 — 순GEX 와 같게 invalid
    table = strike_gex(evaluate_expiry(chain(), NOW, 1100.0, min_premium=1e6))
    assert (table.quality, table.excluded_oi_ratio) == ("invalid", 1.0)
    assert [r.strike for r in table.rows] == [D(k) for k in STRIKES]
    assert all(r.gex_call == r.gex_put == 0.0 for r in table.rows)


# --- 범위 ---


def _three_expiries() -> list[ExpiryEval]:
    return [
        evaluate_expiry(chain(oi_p=0), NOW, 1100.0),  # 10-08 월물, 콜만
        evaluate_expiry(chain(oi_c=0, ed=date(2026, 10, 1), expiry="261001"), NOW, 1100.0),
        evaluate_expiry(chain(ed=date(2026, 10, 6), expiry="261002"), NOW, 1100.0),
    ]


def test_scopes() -> None:
    evs = _three_expiries()
    parts = [net_gex([e]).value for e in evs]
    assert all(v is not None for v in parts)
    total = net_gex(evs)
    assert total.expiries == ("202610", "261001", "261002")
    assert total.value == pytest.approx(math.fsum(v for v in parts if v is not None))

    nearest = net_gex(evs, "nearest")
    assert (nearest.expiries, nearest.value) == (("261001",), parts[1])
    assert nearest.value is not None and nearest.value < 0  # 풋만

    zero = net_gex(evs, "0dte", date(2026, 10, 1))
    assert (zero.expiries, zero.value) == (("261001",), parts[1])


def test_empty_scope_is_null() -> None:
    res = net_gex(_three_expiries(), "0dte", date(2026, 10, 2))
    assert (res.value, res.quality, res.excluded_oi_ratio, res.expiries) == (None, "ok", 0.0, ())
    assert net_gex([]).value is None
    assert net_gex([], "nearest").value is None


def test_scope_errors() -> None:
    evs = _three_expiries()
    with pytest.raises(ValueError):
        net_gex(evs, "0dte")  # trade_date 필요
    with pytest.raises(ValueError):
        net_gex(evs, "0dte", datetime(2026, 10, 1, tzinfo=KST))  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValueError):
        net_gex(evs, "week")  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValueError):
        select_scope([evs[0], evs[0]], "all")


@pytest.mark.parametrize(("excluded_oi", "quality"), [(1000, "ok"), (1001, "estimated")])
def test_excluded_ratio_ten_percent_boundary(excluded_oi: int, quality: str) -> None:
    # 포함 OI 9,000 + 가격 없는 종목 → 1000/10000 = 10% 정확히는 ok, 넘으면 estimated
    qs = [*chain(oi_c=900, oi_p=900), quote(1200, "C", oi=excluded_oi)]
    ev = evaluate_expiry(qs, NOW, 1100.0)
    for fn in (net_gex, dex):
        res = fn([ev])
        assert res.excluded_oi_ratio == excluded_oi / (9000 + excluded_oi)
        assert res.quality == quality
    assert net_gex([ev], max_excluded_ratio=0.2).quality == "ok"


def test_excluded_ratio_is_per_scope() -> None:
    # 최근접 만기에만 가격 없는 OI 2,000 — 그 만기로는 2000/12000 > 10%, 전체로는 2000/27000
    evs = _three_expiries()
    nd = date(2026, 10, 1)
    orphan = quote(1200, "C", expiry="261001", expiry_date=nd, oi=2000)
    evs[1] = evaluate_expiry([*chain(ed=nd, expiry="261001"), orphan], NOW, 1100.0)
    nearest, total = net_gex(evs, "nearest"), net_gex(evs)
    assert (nearest.excluded_oi_ratio, nearest.quality) == (2000 / 12_000, "estimated")
    assert (total.excluded_oi_ratio, total.quality) == (2000 / 27_000, "ok")


# --- DEX ---


def test_dex_value() -> None:
    ev = evaluate_expiry(chain(oi_c=1000, oi_p=2000), NOW, 1100.0)
    assert ev.F is not None
    expected = 0.0
    for o in ev.options:
        assert o.greeks is not None
        sign = 1 if o.quote.cp == "C" else -1
        expected += sign * o.quote.oi * o.greeks.delta * OPTION_MULTIPLIER * ev.F
    res = dex([ev])
    assert res.value is not None and res.value > 0
    assert res.value == pytest.approx(expected, rel=1e-12)


def test_units() -> None:
    assert OPTION_MULTIPLIER == 250_000
    assert to_eok(3.025e9) == pytest.approx(30.25)
    assert EOK == 1e8
    with pytest.raises(ValueError):
        option_gex("X", 0.001, 1, 1100.0)  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValueError):
        option_dex("X", 0.5, 1, 1100.0)  # pyright: ignore[reportArgumentType]


def test_forward_result_carried() -> None:
    ev = evaluate_expiry(chain(), NOW, 1100.0)
    assert isinstance(ev.forward, ForwardResult)
    assert ev.forward.strikes == tuple(D(k) for k in STRIKES)


# --- KIS 그릭스는 GEX 입력이 아니다 (2026-09-28 사용자 결정, PLAN §6.2·metrics §1.7) ---

CORE = Path(__file__).resolve().parents[2] / "core"
# KIS 응답의 그릭스·이론가 필드 — `gama`·`delta_val`·`hist_vltl`·`hts_thpr` 는 KIS 만 쓰는 이름이다.
# theta·vega·rho 는 자체 Greeks 와 이름이 같아 코드 검색 대신 입력 모델(extra 거부)로 막는다
KIS_ONLY_NAMES = ("gama", "delta_val", "hist_vltl", "hts_thpr")
KIS_GREEK_FIELDS = (*KIS_ONLY_NAMES, "delta", "gamma", "theta", "vega", "rho")


def _core_identifiers() -> set[str]:
    """core 패키지 소스의 식별자·속성 이름·키워드 인자·문자열 상수(정확히 같은 값만)."""
    names: set[str] = set()
    for path in sorted(CORE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.keyword) and node.arg is not None:
                names.add(node.arg)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                names.add(node.value.strip())
    return names


def test_gex_never_reads_kis_greeks() -> None:
    """KIS 그릭스(`gama`·`delta_val` 등)는 저장·표시만 하고 GEX 에는 어떤 경우에도 쓰지 않는다.

    GEX 입력(`OptionQuote`)엔 그 자리가 없고(extra 거부), core 코드 어디에도 KIS 그릭스 필드
    이름이 없다. core 가 받는 KIS 값은 §1.5 IV 폴백(`hts_ints_vltl` → `kis_iv_pct`) 하나뿐이다.
    """
    assert not set(OptionQuote.model_fields) & set(KIS_GREEK_FIELDS)
    for field in KIS_GREEK_FIELDS:
        with pytest.raises(ValidationError):
            quote(1100, "C", last=D("10.00"), **{field: "0.0029"})
    found = _core_identifiers()
    assert "kis_iv_pct" in found  # 검색이 실제로 core 를 읽었다
    assert not found & set(KIS_ONLY_NAMES)


def test_gex_greeks_come_from_own_forward_and_t() -> None:
    """GEX·DEX 에 든 그릭스는 자체 합성 F·자체 T(§1.3·§1.4)와 그 종목 σ 로 낸 Black-76 값이다.

    KIS IV 폴백 종목도 T 는 자체 T 다 — KIS T 는 폴백 σ 를 옮기는 데만 쓴다(§1.5).
    """
    ev = evaluate_expiry([*chain(), _below_intrinsic_call()], NOW, 1100.0)
    assert ev.F is not None and ev.T_kis is not None and ev.T_kis != ev.T
    assert {o.iv.source for o in ev.options if o.iv is not None} == {"model", "kis"}
    for o in ev.options:
        assert o.greeks is not None and o.iv is not None and o.iv.sigma is not None
        flag = "c" if o.quote.cp == "C" else "p"
        assert o.greeks == greeks(flag, ev.F, float(o.quote.strike), ev.T, o.iv.sigma)
