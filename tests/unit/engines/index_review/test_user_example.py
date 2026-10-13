"""사용자 예시 표 재현(합성 숫자) — 열 정의가 metrics §24.3 의 식과 같다.

예시의 열: 종목 · 시가총액 · 유동시총 · 유동비율 · 시총 컷오프 괴리율 · 유동시총 컷오프 괴리율 ·
조건 충족(O/X) · 필요 주가 상승률(시총 기준·유동 기준 중 큰 쪽) · 예상 패시브 매수(억) · 일평균
거래대금 대비 배수.
값은 합성이다(실제 종목·리포트 숫자 아님). 배수는 설정(1.8× · 0.9× — [확인 필요]).
"""

from __future__ import annotations

from datetime import date

import pytest

from kbj.config.index_reviews import load_index_reviews
from kbj.engines.index_review.msci import predict_msci
from kbj.engines.index_review.types import StockIn

CFG = load_index_reviews()
RULES = CFG.msci_rules("msci_korea")
JO = 1_000_000_000_000
CUTOFF = 3 * JO  # 합성 컷오프 3조
AUM = 40 * JO  # 합성 추종 자금 40조

# (코드, 시총(조), 추정 유동비율, 일평균 거래대금(억)) — 합성
ROWS = [
    ("990901", 6.20, 0.52, 450.0),
    ("990902", 5.10, 0.31, 300.0),
    ("990903", 4.70, 0.66, 200.0),
]


def _stocks() -> list[StockIn]:
    out = [
        StockIn(code=c, name=None, market="KOSPI", kind="common", sector=None, listed_on=None,
                mktcap=int(cap * JO), adv_won=adv * 1e8, fif_raw=f, fif_prev=None,
                fif_source="DART", flags=(), member=False)
        for c, cap, f, adv in ROWS
    ]  # fmt: skip
    out.append(StockIn(code="990999", name=None, market="KOSPI", kind="common", sector=None,
                       listed_on=None, mktcap=200 * JO, adv_won=1e12, fif_raw=0.5, fif_prev=0.5,
                       fif_source="DART", flags=(), member=True))  # fmt: skip
    return out


def test_columns_follow_the_formulas() -> None:
    rule = CFG.fif_rule_for(date(2026, 11, 11))
    res = predict_msci(_stocks(), RULES, rule, CFG.fol, cutoff_override=CUTOFF, aum_won=AUM)
    got = {o.code: o for o in res.stocks}
    m_mult, f_mult = RULES.val("add_mcap_mult"), RULES.val("add_ff_mult")
    for code, cap, raw, adv in ROWS:
        o = got[code]
        fif = o.fif
        assert fif is not None
        ff = cap * JO * fif
        # 유동비율 = 새 규칙 반올림(> 25% 는 2.5% 단위)
        assert fif == pytest.approx(round(raw / 0.025) * 0.025)
        assert o.ff_mcap == pytest.approx(ff, abs=1)
        # 괴리율 = 값 ÷ (배수 × 컷오프) − 1
        assert o.gap_mcap == pytest.approx(cap * JO / (m_mult * CUTOFF) - 1)
        assert o.gap_ff == pytest.approx(ff / (f_mult * CUTOFF) - 1)
        # 조건 충족 O/X = 두 괴리율 모두 ≥ 0
        assert o.gap_mcap is not None and o.gap_ff is not None
        assert o.condition_met is (o.gap_mcap >= 0 and o.gap_ff >= 0)
        # 필요 상승률 = max(시총 기준, 유동 기준) — 1bp 올림
        need = max(m_mult * CUTOFF / (cap * JO), f_mult * CUTOFF / ff) - 1
        assert o.required_rise is not None
        assert need <= o.required_rise < need + 1e-4 + 1e-12
        assert o.required_basis == ("mcap" if m_mult * CUTOFF / (cap * JO) >= f_mult * CUTOFF / ff
                                    else "ff")  # fmt: skip
        # 예상 패시브 매수 = 추종 자금 × 유동시총 ÷ (편입 뒤) 지수 유동시총 — 편입·경계일 때
        if o.outcome in ("add", "borderline") and o.passive_won is not None:
            assert o.adv_multiple == pytest.approx(abs(o.passive_won) / (adv * 1e8))
    # 이 합성 예: 990901 은 충족(편입), 990902 는 유동 조건 미달(필요 상승률은 유동 기준)
    assert got["990901"].outcome == "add" and got["990901"].condition_met
    assert got["990902"].required_basis == "ff" and not got["990902"].condition_met
