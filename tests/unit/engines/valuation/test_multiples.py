"""배수 계산 — 회사 시총(보통 + 우선)·trailing 손계산·적용 불가 사유 표·forward(§4.8, metrics
§11)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from kbj.core.fin_rows import NaReason
from kbj.core.quality import Quality
from kbj.engines.consensus.forward import Ntm, ntm
from kbj.engines.valuation.multiples import (
    NA_PRIORITY,
    Balance,
    Cell,
    Facts,
    Ttm,
    blocked_reason,
    cells_of,
    company_mktcap,
    first_reason,
    forward,
    trailing,
)
from tests.unit.engines.valuation.val_rows import CODE, PREF, estimate, snap, vday

D = date(2026, 11, 13)


TTM0 = Ttm(
    ref="2026Q3:CFS",
    revenue=4_000,
    op=400,
    ni=320,
    ni_owner=300,
    ebitda=480,
    eps=30.0,
    tier="full",
    currency="KRW",
    quality=Quality.OK,
)
BAL0 = Balance(
    ref="2026Q3:CFS",
    period_end=date(2026, 9, 30),
    equity_owner=2_000,
    net_debt=300,
    minority=50,
    tier="full",
    currency="KRW",
    quality=Quality.OK,
)


def ttm(**kw: Any) -> Ttm:
    return replace(TTM0, **kw)


def bal(**kw: Any) -> Balance:
    return replace(BAL0, **kw)


# ── 회사 시총 ──


def test_회사_시총은_보통주와_우선주의_합() -> None:
    snaps = {CODE: snap(CODE, D, 70_000.0, 400), PREF: snap(PREF, D, 60_000.0, 60)}
    cap = company_mktcap(snaps, (CODE, PREF), D)
    assert cap is not None
    assert cap.value == 70_000 * 400 + 60_000 * 60
    assert (cap.codes, cap.missing, cap.quality, cap.source) == (
        (CODE, PREF),
        (),
        Quality.OK,
        "krx",
    )


def test_우선주가_빠지면_빼고_estimated() -> None:
    snaps = {CODE: snap(CODE, D, 70_000.0, 400)}
    cap = company_mktcap(snaps, (CODE, PREF), D)
    assert cap is not None and cap.value == 28_000_000
    assert cap.missing == (PREF,) and cap.quality is Quality.ESTIMATED


def test_보통주가_빠지면_우선주만으로_회사_시총을_세지_않는다() -> None:
    # 우선주만의 합은 회사 시총의 작은 조각 — 배수가 몇 배 틀린다
    # (§4.8 이 빼도 된다고 한 것은 우선주뿐)
    pref = snap(PREF, D, 60_000.0, 60)
    assert company_mktcap({PREF: pref}, (CODE, PREF), D) is None
    stale = snap(CODE, date(2026, 11, 12), 70_000.0, 400)
    assert company_mktcap({CODE: stale, PREF: pref}, (CODE, PREF), D) is None
    bad = snap(CODE, D, 70_000.0, 400, quality=Quality.INVALID)
    assert company_mktcap({CODE: bad, PREF: pref}, (CODE, PREF), D) is None


def test_시총_칸이_없으면_종가_곱하기_주식수_invalid_는_뺀다() -> None:
    s = snap(CODE, D, 100.0, 50)
    s = s.__class__(**{**s.__dict__, "mktcap": None})
    bad = snap(PREF, D, 1.0, 1, quality=Quality.INVALID)
    cap = company_mktcap({CODE: s, PREF: bad}, (CODE, PREF), D)
    assert cap is not None and cap.value == 5_000 and cap.missing == (PREF,)
    assert company_mktcap({}, (CODE,), D) is None
    assert company_mktcap({CODE: snap(CODE, date(2026, 11, 12), 1.0, 1)}, (CODE,), D) is None


# ── trailing 손계산 ──


def test_trailing_손계산() -> None:
    tr = trailing(10_000, ttm(), bal(), Facts(kind="common"))
    assert tr.per.value == pytest.approx(10_000 / 300)
    assert tr.pbr.value == pytest.approx(5.0)
    assert tr.psr.value == pytest.approx(2.5)
    assert tr.ev == 10_000 + 300 + 50  # 오버행 가치를 더하지 않는다
    assert tr.ev_ebitda.value == pytest.approx(10_350 / 480)
    assert tr.blocked is None and tr.quality is Quality.OK


def test_비지배지분_줄이_없으면_0_으로_보고_estimated() -> None:
    tr = trailing(10_000, ttm(), bal(minority=None), Facts())
    assert tr.ev == 10_300 and tr.quality is Quality.ESTIMATED
    assert any("비지배지분" in n for n in tr.notes)


CASES: list[
    tuple[str, dict[str, object], dict[str, object], Facts, NaReason, dict[str, NaReason]]
] = [
    (
        "재무 없음",
        {"none": True},
        {"none": True},
        Facts(),
        NaReason.MISSING_INPUT,
        {m: NaReason.NO_FILINGS for m in ("per", "pbr", "psr", "ev_ebitda")},
    ),
    (
        "통화 불일치 — 스팩보다 먼저",
        {"currency": "USD"},
        {"currency": "USD"},
        Facts(kind="spac"),
        NaReason.MISSING_INPUT,
        {m: NaReason.CURRENCY_MISMATCH for m in ("per", "pbr", "psr", "ev_ebitda")},
    ),
    (
        "스팩 — 금융업보다 먼저, PBR 은 계산",
        {},
        {},
        Facts(kind="spac", is_financial=True),
        NaReason.MISSING_INPUT,
        {"per": NaReason.SPAC, "psr": NaReason.SPAC, "ev_ebitda": NaReason.SPAC},
    ),
    (
        "금융업 — EV/EBITDA 만",
        {"ebitda": None},
        {},
        Facts(is_financial=True),
        NaReason.MISSING_INPUT,
        {"ev_ebitda": NaReason.FINANCIAL_SECTOR},
    ),
    (
        "주요계정 층",
        {"tier": "main", "ebitda": None},
        {"tier": "main"},
        Facts(),
        NaReason.MISSING_INPUT,
        {"ev_ebitda": NaReason.NOT_DETAIL_TIER},
    ),
    (
        "신규상장 TTM 없음",
        {"none": True},
        {},
        Facts(),
        NaReason.SHORT_HISTORY,
        {
            "per": NaReason.SHORT_HISTORY,
            "psr": NaReason.SHORT_HISTORY,
            "ev_ebitda": NaReason.SHORT_HISTORY,
        },
    ),
    (
        "반기 보고만 TTM 없음",
        {"none": True},
        {},
        Facts(),
        NaReason.MISSING_INPUT,
        {
            "per": NaReason.MISSING_INPUT,
            "psr": NaReason.MISSING_INPUT,
            "ev_ebitda": NaReason.MISSING_INPUT,
        },
    ),
    (
        "적자",
        {"ni_owner": -5, "ebitda": -1},
        {},
        Facts(),
        NaReason.MISSING_INPUT,
        {"per": NaReason.LOSS, "ev_ebitda": NaReason.LOSS},
    ),
    (
        "자본잠식",
        {},
        {"equity_owner": -10},
        Facts(),
        NaReason.MISSING_INPUT,
        {"pbr": NaReason.CAPITAL_IMPAIRMENT},
    ),
    ("매출 0", {"revenue": 0}, {}, Facts(), NaReason.MISSING_INPUT, {"psr": NaReason.NO_REVENUE}),
    (
        "감가상각 없음",
        {"ebitda": None},
        {},
        Facts(),
        NaReason.MISSING_INPUT,
        {"ev_ebitda": NaReason.MISSING_INPUT},
    ),
    (
        "순차입금 없음 → EV 없음",
        {},
        {"net_debt": None},
        Facts(),
        NaReason.MISSING_INPUT,
        {"ev_ebitda": NaReason.MISSING_INPUT},
    ),
]


@pytest.mark.parametrize(
    ("name", "t_kw", "b_kw", "facts", "gap", "want"), CASES, ids=[c[0] for c in CASES]
)
def test_적용_불가_사유_표(
    name: str,
    t_kw: dict[str, Any],
    b_kw: dict[str, Any],
    facts: Facts,
    gap: NaReason,
    want: dict[str, NaReason],
) -> None:
    t = None if t_kw.pop("none", False) else ttm(**t_kw)
    b = None if b_kw.pop("none", False) else bal(**b_kw)
    tr = trailing(10_000, t, b, facts, ttm_gap=gap)
    for m in ("per", "pbr", "psr", "ev_ebitda"):
        cell: Cell = getattr(tr, m)
        if m in want:
            assert (cell.value, cell.na_reason) == (None, want[m]), (name, m)
        else:
            assert cell.value is not None and cell.na_reason is None, (name, m)


def test_사유_순위() -> None:
    assert (
        NA_PRIORITY[0] is NaReason.CURRENCY_MISMATCH and NA_PRIORITY[-1] is NaReason.MISSING_INPUT
    )
    assert first_reason([NaReason.MISSING_INPUT, NaReason.LOSS, None]) is NaReason.LOSS
    assert first_reason([NaReason.SHORT_HISTORY, NaReason.SPAC]) is NaReason.SPAC
    assert first_reason([NaReason.KONEX_NO_CONSENSUS, NaReason.MISSING_INPUT]) is (
        NaReason.MISSING_INPUT
    )
    assert first_reason([None]) is None
    assert blocked_reason(None, None) is NaReason.NO_FILINGS
    assert blocked_reason(ttm(), None) is None


def test_값과_사유를_같이_둘_수_없다() -> None:
    with pytest.raises(ValueError):
        Cell(1.0, NaReason.LOSS)


# ── forward ──


def _ntm(eps: float | None, ni_owner: int | None, *, ebitda: int | None = 500) -> Ntm | None:
    rows = [
        estimate(CODE, D, "202612", eps=eps, ni_owner=ni_owner, revenue=4_000, ebitda=ebitda),
        estimate(CODE, D, "202712", eps=eps, ni_owner=ni_owner, revenue=4_800, ebitda=ebitda),
    ]
    return ntm(rows, D)


def test_forward_손계산() -> None:
    est = _ntm(30.0, 300)
    fw = forward(10_000, 10_350, est, Facts(), shares=10)
    w = (date(2026, 12, 31) - D).days / 365
    assert fw.per_fwd.value == pytest.approx(10_000 / 300)
    assert fw.psr_fwd.value == pytest.approx(10_000 / (w * 4_000 + (1 - w) * 4_800))
    assert fw.ev_ebitda_fwd.value == pytest.approx(10_350 / 500)
    assert fw.used and fw.quality is Quality.ESTIMATED


def test_forward_지배순익이_없으면_EPS_곱하기_주식수() -> None:
    fw = forward(10_000, None, _ntm(25.0, None), Facts(), shares=20)
    assert fw.per_fwd.value == pytest.approx(10_000 / 500)
    assert fw.ev_ebitda_fwd.na_reason is NaReason.MISSING_INPUT  # EV 없음


def test_forward_사유() -> None:
    konex = forward(1, 1, None, Facts(consensus_na=NaReason.KONEX_NO_CONSENSUS), shares=1)
    assert konex.per_fwd.na_reason is NaReason.KONEX_NO_CONSENSUS and not konex.used
    empty = forward(1, 1, None, Facts(), shares=1)
    assert empty.per_fwd == Cell(None) and empty.quality is None  # 자료 없음 — 사유 없음
    loss = forward(10, 10, _ntm(-3.0, -30, ebitda=-1), Facts(), shares=10)
    assert loss.per_fwd.na_reason is NaReason.LOSS and loss.ev_ebitda_fwd.na_reason is NaReason.LOSS
    fin = forward(10, 10, _ntm(3.0, 30), Facts(is_financial=True), shares=10)
    assert fin.ev_ebitda_fwd.na_reason is NaReason.FINANCIAL_SECTOR
    usd = forward(
        10,
        None,
        _ntm(3.0, 30),
        Facts(),
        shares=10,
        ev_blocked=NaReason.CURRENCY_MISMATCH,
    )
    assert usd.ev_ebitda_fwd.na_reason is NaReason.CURRENCY_MISMATCH and usd.per_fwd.value
    # 순위는 trailing 과 같다 — EV 를 막은 사유(통화·재무 없음)가 금융업보다 앞
    for blocked in (NaReason.CURRENCY_MISMATCH, NaReason.NO_FILINGS):
        both = forward(
            10, None, _ntm(3.0, 30), Facts(is_financial=True), shares=10, ev_blocked=blocked
        )
        assert both.ev_ebitda_fwd.na_reason is blocked
    tr = trailing(10, None, None, Facts(is_financial=True))
    assert tr.ev_ebitda.na_reason is NaReason.NO_FILINGS


def test_저장_행에서_칸() -> None:
    row = vday(D, per=None, pbr=1.5, na={"per": NaReason.LOSS})
    cells = cells_of(row)
    assert cells["per"] == Cell(None, NaReason.LOSS)
    assert cells["pbr"] == Cell(1.5)
    assert cells["per_fwd"] == Cell(None)
