"""GEX 브리핑(kbj.engines.gex.briefing) — 레벨표·머리 문장·옵션 포지셔닝 손익(합성 값)."""

from __future__ import annotations

import pytest

from kbj.engines.gex.briefing import (
    ChainQuote,
    Leg,
    StrikeBar,
    briefing,
    money,
    one_day_move,
    sigma_to_expiry,
    strategies,
    strike_table,
    table_walls,
)

E = 1e8  # 억

# 사용자 예시(10/20 아침)와 같은 모양 — 콜월 892.5·풋월 880·플립 890·현재 884.8
BARS = [
    StrikeBar(900.0, 2.0e12, -5.8e12),
    StrikeBar(897.5, 1.2e12, -0.6e12),
    StrikeBar(895.0, 4.1e12, -0.8e12),
    StrikeBar(892.5, 6.5e12, -0.9e12),
    StrikeBar(890.0, 4.6e12, -0.6e12),
    StrikeBar(885.0, 5.6e12, -0.5e12),
    StrikeBar(880.0, 0.3e12, -0.9e12),
    StrikeBar(870.0, 0.1e12, -0.75e12),
    StrikeBar(892.5, 0.2e12, -0.1e12),  # 다른 만기 같은 행사가 — 합산된다
]


def test_strike_table_sums_expiries_and_sorts_high_to_low() -> None:
    rows = strike_table(BARS)
    assert [r.strike for r in rows] == [900.0, 897.5, 895.0, 892.5, 890.0, 885.0, 880.0, 870.0]
    r = next(r for r in rows if r.strike == 892.5)
    assert (r.call, r.put) == (6.7e12, -1.0e12) and r.net == pytest.approx(5.7e12)


def test_briefing_levels_and_sentences() -> None:
    rows = strike_table(BARS)
    b = briefing(rows, price=884.8, flip=890.0, call_wall=892.5, put_wall=880.0,
                 expected_move=27.0, recent_net=[10e12, 30e12])  # fmt: skip
    assert b.regime == "short"  # 현재가 884.8 이 플립 890 아래(순GEX 합은 양수여도)
    assert b.net_vs_avg == pytest.approx(sum(r.net for r in rows) / 20e12)
    kinds = [lv.kind for lv in b.levels]
    # 높은 값부터: 예상 상단 911.8 · 2차 콜월 895 · 콜월 892.5 · 플립 890 · 현재 · 풋월 880
    # · 2차 풋월 870 · 하단
    assert kinds == ["em_up", "call_wall2", "call_wall", "flip", "spot", "put_wall", "put_wall2",
                     "em_down"]  # fmt: skip
    by = {lv.kind: lv for lv in b.levels}
    assert by["call_wall2"].value == 895.0  # 콜월 위 콜 GEX 최대(900 은 2.0조 < 895 4.1조)
    assert by["put_wall2"].value == 870.0
    assert by["flip"].lines[0].startswith("이미 이 선 아래에 있습니다")  # 884.8 < 890
    assert by["call_wall"].lines == ("막히면 → 880까지 되돌림.",
                                     "892.5 위에서 마감하면 → 다음 저항은 895입니다.")  # fmt: skip
    assert by["put_wall"].lines == ("무너지면 → 870 2차 풋월까지 열립니다.",)
    assert by["call_wall"].strength == "약"  # 6.7조 < 콜 GEX 중앙값 4.1조 × 2
    assert by["em_up"].dist_pt == 27.0 and by["em_up"].dist_pct == pytest.approx(3.05, abs=0.01)
    assert (b.first_up, b.first_down) == (890.0, 880.0)
    assert b.headline == "숏감마 구간에서 출발합니다. 오늘 첫 시험대는 위로 890, 아래로 880입니다."


def test_short_gamma_headline_and_missing_levels() -> None:
    rows = strike_table([StrikeBar(880.0, 1e11, -9e11), StrikeBar(890.0, 1e11, -2e11)])
    b = briefing(rows, price=885.0, flip=None, call_wall=None, put_wall=None, expected_move=None)
    assert b.regime == "short" and b.regime_label.startswith("Short γ")
    assert [lv.kind for lv in b.levels] == ["spot"] and b.net_vs_avg is None
    assert b.headline == "숏감마 구간에서 출발합니다."


def test_money_formats_jo_and_eok() -> None:
    assert (money(5.6e12), money(-5857 * E), money(0.0)) == ("+5.6조", "-5,857억", "0억")


# ── 옵션 포지셔닝 ──

# ATM 220(현재 217) — 원문 삼성전자 예시 모양(가격은 pt 로 합성)
QUOTES = [
    ChainQuote(220.0, "C", 10.0), ChainQuote(220.0, "P", 13.0),
    ChainQuote(230.0, "C", 6.0), ChainQuote(210.0, "P", 8.0),
    ChainQuote(240.0, "C", 3.0), ChainQuote(200.0, "P", 4.0),
    ChainQuote(250.0, "C", 1.0), ChainQuote(190.0, "P", 2.0),
]  # fmt: skip


def test_four_structures_with_breakevens_and_bounds() -> None:
    got = {s.key: s for s in strategies(QUOTES, price=217.0, sigma_to_expiry=10.0)}
    assert list(got) == ["long_straddle", "straddle_wings", "short_strangle", "synthetic_long"]
    ls = got["long_straddle"]
    assert ls.net_premium == -23.0 and ls.breakevens == (197.0, 243.0)
    assert ls.max_profit is None and ls.max_loss == 23.0  # 위로 무제한, 손실은 프리미엄
    w = got["straddle_wings"]  # +C220 +P220 −P210 −C230
    assert [(g.side, g.cp, g.strike) for g in w.legs] == [
        ("buy", "C", 220.0),
        ("buy", "P", 220.0),
        ("sell", "P", 210.0),
        ("sell", "C", 230.0),
    ]
    assert w.net_premium == -9.0 and w.breakevens == (211.0, 229.0)
    assert (w.max_profit, w.max_loss) == (1.0, 9.0)
    ss = got["short_strangle"]  # ±1.5σ = 235·205 → 가장 가까운 240·200
    assert [g.strike for g in ss.legs] == [240.0, 200.0]
    assert ss.net_premium == 7.0 and ss.breakevens == (193.0, 247.0)
    assert ss.max_profit == 7.0 and ss.max_loss is None and "제한 없음" in ss.note
    syn = got["synthetic_long"]  # +C220 −P210
    assert syn.net_premium == -2.0 and syn.breakevens == (222.0,)
    assert syn.max_profit is None and syn.max_loss is None
    # 시나리오: 0% 에서 양매수는 프리미엄 − 내재가치
    assert dict(ls.scenario)[0.0] == pytest.approx(-23.0 + 3.0)


def test_no_quotes_means_no_strategies_and_sigma() -> None:
    assert strategies([], price=217.0, sigma_to_expiry=None) == []
    assert sigma_to_expiry(400.0, 0.2, 0.25) == pytest.approx(40.0)
    assert sigma_to_expiry(400.0, None, 0.25) is None


def test_payoff_respects_leg_sides() -> None:
    from kbj.engines.gex.briefing import _payoff  # pyright: ignore[reportPrivateUsage]

    legs = [Leg("sell", "P", 210.0, 8.0)]
    assert _payoff(legs, 220.0) == 8.0 and _payoff(legs, 200.0) == -2.0


def test_table_walls_pick_call_above_and_put_below_the_price() -> None:
    rows = strike_table(BARS)
    # 풋 절댓값 최대는 900(5.8조)이지만 현재가 위 — 풋월은 아래에서 가장 큰 880
    assert table_walls(rows, 884.8) == (892.5, 880.0)
    # 현재가 위에 콜이 없으면 전체에서
    assert table_walls(rows, 950.0)[0] == 892.5
    assert table_walls([], 884.8) == (None, None)


def test_pullback_target_is_the_nearest_support_below() -> None:
    rows = strike_table(BARS)
    b = briefing(rows, price=884.8, flip=870.0, call_wall=892.5, put_wall=860.0,
                 expected_move=None)  # fmt: skip
    cw = next(x for x in b.levels if x.kind == "call_wall")
    assert cw.lines[0] == "막히면 → 870까지 되돌림."  # 플립 870 > 풋월 860
    b = briefing(rows, price=884.8, flip=None, call_wall=892.5, put_wall=None,
                 expected_move=None)  # fmt: skip
    cw = next(x for x in b.levels if x.kind == "call_wall")
    assert cw.lines[0].startswith("막히면 → 아래 받쳐 줄 레벨이 멀어")


def test_one_day_move_uses_252_sessions() -> None:
    assert one_day_move(888.75, 0.2) == pytest.approx(888.75 * 0.2 / 252**0.5)
    assert one_day_move(888.75, None) is None and one_day_move(888.75, 0.0) is None


def test_parity_outlier_strike_is_dropped() -> None:
    # 선도 ≈ 217 인 행사가들 + 220 콜만 튀는 호가(마감 뒤 한쪽만 남은 값처럼)
    quotes = [
        ChainQuote(210.0, "C", 12.0), ChainQuote(210.0, "P", 5.0),
        ChainQuote(215.0, "C", 8.5), ChainQuote(215.0, "P", 6.5),
        ChainQuote(220.0, "C", 25.0), ChainQuote(220.0, "P", 8.0),
        ChainQuote(225.0, "C", 3.5), ChainQuote(225.0, "P", 11.5),
    ]  # fmt: skip
    ls = next(s for s in strategies(quotes, price=218.0, sigma_to_expiry=5.0)
              if s.key == "long_straddle")  # fmt: skip
    assert {g.strike for g in ls.legs} == {215.0}  # 220 이 더 가깝지만 패리티가 깨져 빠짐


def test_structure_without_profit_zone_is_flagged() -> None:
    quotes = [
        ChainQuote(220.0, "C", 30.0), ChainQuote(220.0, "P", 30.0),
        ChainQuote(230.0, "C", 1.0), ChainQuote(210.0, "P", 1.0),
    ]  # fmt: skip
    w = next(s for s in strategies(quotes, price=220.0, sigma_to_expiry=10.0)
             if s.key == "straddle_wings")  # fmt: skip
    assert w.breakevens == () and w.max_profit is not None and w.max_profit <= 0
    assert w.note.startswith("이 시세로는 만기 이익 구간이 없다")
