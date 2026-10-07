"""scripts.validate_greeks — §6.2 KIS 관례 재현 검사·자체 vs KIS 기록·KIS 관례 역산 도우미.

fixture 는 합성 스냅샷(KBJ P1 — `scripts/make_synthetic_fixtures.py`, 시드 고정)을 `chain-subset`
규칙으로 자른 것이다. 원래의 2026-09-28 14:27 실측 발췌와 같은 자리·같은 성질의 행이다: 월물 202610
전광판 4행 + 단건 보강 ATM±5 22행, 월물 202611 전광판 6행(1347.5~1352.5 콜·풋 — 전광판이 최고
행사가부터라 ATM 이 없다), 0DTE WKM 260904 12행(1090~1100 콜·풋 + 1087.5·1102.5 풋 — 1102.5 풋은
KIS IV 폴백 행), WKM 261001 4행(당일 거래 없는 행 3개). KIS 칸은 docs/validation_greeks.md 의 KIS
관례로 만든 값이다(필드명은 원본 그대로 — 전광판 `invl_val` 포함). 합성 교체로 바뀐 기대값은
`--explain` 독립 계산 값이다(legacy/gexlab/MIGRATION.md).
"""

import copy
import json
import math
import re
import statistics
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from core import black76
from core.calendar import KST, TradingCalendar, expiry_at
from core.forward import kis_time_to_expiry, time_to_expiry
from core.gex import net_gex, option_gex, select_scope
from core.greeks import greeks
from core.iv import rescale_sigma
from core.levels import atm_iv, gamma_flip
from scripts.validate_greeks import (
    RECORD_HEADER,
    REPRO_HEADER,
    SUMMARY_HEADER,
    Board,
    BoardRow,
    CompareRow,
    ErrMetric,
    FallbackCounts,
    FitInput,
    KisRow,
    ParityFit,
    SeriesSummary,
    Snapshot,
    analyze,
    board_spot,
    bootstrap_delta_days,
    compare,
    error_stats,
    evaluate,
    fallback_counts,
    fallback_gamma,
    fallback_gamma_rel,
    fallback_gex_vs_kis,
    fit_delta_surface,
    fit_gamma,
    fit_inputs,
    fit_iv,
    fit_parity,
    implied_t_days,
    implied_underlying,
    kis_rel_err,
    kis_sigma,
    kis_underlying,
    load_snapshot,
    main,
    parse_expiry_arg,
    percentile,
    rel_err,
    report,
    reproduce,
    reproduce_row,
    resolve_expiry,
    scope_evals,
    series_rows,
    snapshot_hist,
    solve_delta_gamma,
    split_by_s,
    summarize,
    summary_report,
    t_bases_within,
    t_candidates,
    t_years,
    unmatched_fills,
    window_strikes,
)

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "validation"
    / "chain_snapshot_synthetic_20260928_1427_small.json"
)
NOW = datetime(2026, 9, 28, 14, 27, 18, tzinfo=KST)
S_REF = 1095.1
D = Decimal


@pytest.fixture(scope="module")
def cal() -> TradingCalendar:
    return TradingCalendar.default()


@pytest.fixture(scope="module")
def snap() -> Snapshot:
    return load_snapshot(FIXTURE)


def _raw() -> dict[str, Any]:
    """fixture 원본(dict) 사본 — 변형 스냅샷을 만들 때."""
    return copy.deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8")))


def _fill(series: str, acpr: str, last_tr_date: str) -> dict[str, Any]:
    row = {"acpr": acpr, "hts_otst_stpl_qty": "3", "hts_ints_vltl": "40.0", "gama": "0.003"}
    return {
        "ts_kst": "2026-09-28T14:27:30+09:00",
        "series": series,
        "rt_cd": "0",
        "row": row | {"futs_prpr": "12.00", "futs_last_tr_date": last_tr_date},
    }


# --- fixture 자체 ---


def test_fixture_has_no_secrets() -> None:
    text = FIXTURE.read_text(encoding="utf-8")
    for word in ("token", "appkey", "appsecret", "secret", "authorization", "bearer", "eyJ"):
        assert word.lower() not in text.lower(), word
    assert not re.search(r"[A-Za-z0-9+/=_-]{32,}", text)  # 키·토큰 같은 긴 문자열 없음


# --- 적재 ---


def test_series_rows_merges_board_and_fills(snap: Snapshot, cal: TradingCalendar) -> None:
    s = series_rows(snap, "MONTH:202610", cal)
    assert (s.expiry, s.expiry_date, s.expiry_source) == ("202610", date(2026, 10, 8), "kis")
    assert s.now == datetime.fromisoformat("2026-09-28T14:27:18.149318+09:00")
    board = [q for q in s.quotes if q.source == "board"]
    fill = [q for q in s.quotes if q.source == "fill"]
    assert (len(board), len(fill)) == (4, 22)
    assert all(q.bid is None and q.ask is None and q.last for q in fill)
    k = s.kis[(D("1100.00"), "C")]
    assert (k.iv_pct, k.gamma, k.delta, k.hist_pct, k.rmnn, k.volume) == (
        36.9547,
        0.0027,
        0.5136,
        81.0495,
        11,
        282,
    )


def test_series_rows_prefers_board_on_overlap(cal: TradingCalendar) -> None:
    row = {"acpr": "1100.00", "hts_otst_stpl_qty": "5", "hts_ints_vltl": "40.0", "gama": "0.003"}
    raw: dict[str, Any] = {
        "started_kst": "2026-09-28T14:27:12+09:00",
        "atm_ref": {"code": "A01612", "price": 1100.0},
        "boards": {
            "MONTH:202610": {
                "ts_kst": "2026-09-28T14:27:18+09:00",
                "output1": [row | {"optn_bidp": "10.00", "optn_askp": "10.05"}],
                "output2": [],
            }
        },
        "fills": [
            {
                "ts_kst": "2026-09-28T14:27:20+09:00",
                "series": "C 202610",
                "rt_cd": "0",
                "row": row | {"futs_prpr": "11.00", "futs_last_tr_date": "20261008"},
            }
        ],
    }
    s = series_rows(Snapshot.model_validate(raw), "MONTH:202610", cal)
    assert [(q.strike, q.source, q.bid) for q in s.quotes] == [(D("1100.00"), "board", D("10.00"))]


def test_kis_zero_means_missing() -> None:
    raw: dict[str, Any] = {
        "acpr": "1100",
        "hts_otst_stpl_qty": "0",
        "acml_vol": "0",
        "hts_ints_vltl": "0.0000",
        "gama": "0.0000",
        "delta_val": "0.0000",
        "hist_vltl": "0.0000",
        "hts_thpr": "0.00",
    }

    k = KisRow.of(BoardRow.model_validate(raw))
    assert (k.iv_pct, k.gamma, k.hist_pct, k.thpr) == (None, None, None, None)
    assert (k.delta, k.volume) == (0.0, 0)  # 델타·거래량 0 은 값이다


def test_resolve_expiry(cal: TradingCalendar) -> None:
    assert resolve_expiry("MONTH:202610", ["20261008", ""], cal) == (date(2026, 10, 8), "kis")
    assert resolve_expiry("WKM:261001", [], cal) == (date(2026, 10, 6), "kis")
    assert resolve_expiry("MONTH:202611", [], cal) == (date(2026, 11, 12), "computed")
    with pytest.raises(ValueError, match="여러 개"):
        resolve_expiry("MONTH:202610", ["20261008", "20261009"], cal)
    with pytest.raises(ValueError, match="모른다"):
        resolve_expiry("WKI:261008", [], cal)
    with pytest.raises(ValueError, match="라벨"):
        resolve_expiry("202610", [], cal)
    # fills 와 아는 만기일(KIS_EXPIRY)이 다르면 조용히 한쪽을 고르지 않는다
    with pytest.raises(ValueError, match="다르다"):
        resolve_expiry("MONTH:202610", ["20261009"], cal)


def test_resolve_expiry_given_overrides_table(cal: TradingCalendar) -> None:
    given = {"WKI:261008": date(2026, 10, 8), "WKM:261001": date(2026, 10, 5)}
    assert resolve_expiry("WKI:261008", [], cal, given) == (date(2026, 10, 8), "arg")
    assert resolve_expiry("WKM:261001", [], cal, given) == (date(2026, 10, 5), "arg")
    with pytest.raises(ValueError, match="다르다"):
        resolve_expiry("WKI:261008", ["20261009"], cal, given)


def test_parse_expiry_arg() -> None:
    assert parse_expiry_arg("WKI:261008=20261008") == ("WKI:261008", date(2026, 10, 8))
    for bad in ("WKI:261008", "WKI:261008=2026-10-08", "261008=20261008", "WKI:261008=20261332"):
        with pytest.raises(ValueError):
            parse_expiry_arg(bad)


# --- 보강 행 가르기 ---


def test_shared_code_fills_split_by_last_trade_date(cal: TradingCalendar) -> None:
    """WKI·WKM 261001 은 코드가 같다 — 보강 행은 futs_last_tr_date 로 제 시리즈에만 붙는다."""
    raw = _raw()
    raw["boards"]["WKI:261001"] = copy.deepcopy(raw["boards"]["WKM:261001"])
    raw["fills"].append(_fill("C 261001", "1200.00", "20261006"))  # WKM 261001(10-06) 것
    s = Snapshot.model_validate(raw)
    wki = series_rows(s, "WKI:261001", cal)
    wkm = series_rows(s, "WKM:261001", cal)
    assert wki.expiry_date == date(2026, 10, 1)  # 보강 행 날짜(10-06)에 끌려가지 않는다
    assert not [q for q in wki.quotes if q.source == "fill"]
    assert wkm.expiry_date == date(2026, 10, 6)
    assert [(q.strike, q.cp) for q in wkm.quotes if q.source == "fill"] == [(D("1200.00"), "C")]


def test_shared_code_fills_without_known_expiry_raise(cal: TradingCalendar) -> None:
    raw = _raw()
    raw["boards"]["WKI:261008"] = copy.deepcopy(raw["boards"]["WKM:261001"])
    raw["boards"]["WKM:261008"] = copy.deepcopy(raw["boards"]["WKM:261001"])
    raw["fills"].append(_fill("P 261008", "1200.00", "20261008"))
    s = Snapshot.model_validate(raw)
    with pytest.raises(ValueError, match="어디에 붙일지"):
        series_rows(s, "WKI:261008", cal)
    given = {"WKI:261008": date(2026, 10, 8), "WKM:261008": date(2026, 10, 12)}
    wki = series_rows(s, "WKI:261008", cal, given)
    wkm = series_rows(s, "WKM:261008", cal, given)
    assert (wki.expiry_date, wki.expiry_source) == (date(2026, 10, 8), "kis")
    assert [q.strike for q in wki.quotes if q.source == "fill"] == [D("1200.00")]
    assert (wkm.expiry_date, wkm.expiry_source) == (date(2026, 10, 12), "arg")
    assert not [q for q in wkm.quotes if q.source == "fill"]


def test_master_named_weekly_fill_is_loaded_and_reported(
    tmp_path: Path, cal: TradingCalendar
) -> None:
    """위클리 보강 행 series 는 마스터 이름('C 2610W1')일 수 있다 — 적재되고 따로 보고된다."""
    raw = _raw()
    raw["fills"].append(_fill("C 2610W1", "1100.00", "20261006"))
    s = Snapshot.model_validate(raw)
    assert unmatched_fills(s) == {"C 2610W1": 1}
    assert not [q for q in series_rows(s, "WKM:261001", cal).quotes if q.source == "fill"]
    p = tmp_path / "chain_snapshot.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert "안 붙어 뺀 보강 행: C 2610W1 1건" in report(p, cal)


# --- 오차 통계 ---


def test_quotes_carry_volume_and_skip_prev_session_in_forward(
    snap: Snapshot, cal: TradingCalendar
) -> None:
    """acml_vol 이 OptionQuote.volume 으로 가고, 당일 거래 없는 last 행사가는 합성 F 에서 빠진다
    (metrics §1.1·§1.3) — 14:27 월물은 보강 행 1097.5 가 그렇다."""
    series = series_rows(snap, "MONTH:202610", cal)
    assert all(q.volume == series.kis[(q.strike, q.cp)].volume for q in series.quotes)
    ev = evaluate(series, S_REF)
    assert ev.forward.prev_session_skipped == (D("1097.50"),)
    assert ev.forward.strikes == (D("1090.00"), D("1092.50"), D("1095.00"), D("1100.00"))
    assert ev.F == pytest.approx(1089.55)  # 합성: 규칙 전에도 1089.55(나머지 행사가 패리티가 같다)
    stale = [o for o in ev.options if o.choice.prev_session]
    assert stale and all(o.quality_reasons == ("prev_session_last",) for o in stale)


def test_percentile_linear() -> None:
    assert percentile([4.0, 1.0, 3.0, 2.0], 90) == pytest.approx(3.7)
    assert percentile([5.0], 90) == 5.0
    assert percentile([1.0, 2.0], 0) == 1.0
    with pytest.raises(ValueError):
        percentile([], 50)
    with pytest.raises(ValueError):
        percentile([1.0], 101)


def test_error_stats() -> None:
    s = error_stats([-0.10, 0.02, 0.04, 0.05], tol=0.05)
    assert s is not None
    assert (s.n, s.within, s.exceed) == (4, 3, 1)
    assert s.bias == pytest.approx(0.03)  # 부호 있는 중앙값
    assert s.median == pytest.approx(0.045)
    assert s.max == pytest.approx(0.10)
    assert s.pass_rate == 0.75
    assert error_stats([], 0.05) is None
    with pytest.raises(ValueError, match="nan"):
        error_stats([math.nan], 0.05)


# --- §6.2 비교 (fixture 행) ---


def test_compare_monthly_atm_window(snap: Snapshot, cal: TradingCalendar) -> None:
    s = series_rows(snap, "MONTH:202610", cal)
    ev = evaluate(s, S_REF)
    assert window_strikes(ev, S_REF) == [D("1082.5") + D("2.5") * i for i in range(11)]
    rows = compare(ev, s.kis, S_REF)
    assert rows is not None and len(rows) == 22
    assert all(r.iv_quality == "ok" and r.iv_diff is not None for r in rows)
    # 자체 감마는 KIS 보다 크게 나온다 — KIS 는 σ 로 역사적 변동성(≈75%)을 쓴다(아래 관례 테스트)
    assert all(r.gamma_rel is not None and r.gamma_rel > 0.5 for r in rows)
    stale = [r for r in rows if r.stale_last]
    assert {(r.strike, r.cp) for r in stale} == {
        (D("1097.50"), "P"),
        (D("1102.50"), "P"),
        (D("1107.50"), "P"),
    }


def test_compare_returns_none_when_window_not_covered(snap: Snapshot, cal: TradingCalendar) -> None:
    s = series_rows(snap, "WKM:261001", cal)  # 2개 행사가뿐
    assert compare(evaluate(s, S_REF), s.kis, S_REF) is None


def test_compare_counts_only_model_iv(snap: Snapshot, cal: TradingCalendar) -> None:
    s = series_rows(snap, "WKM:260904", cal)
    rows = compare(evaluate(s, S_REF), s.kis, S_REF, n=2)
    assert rows is not None
    for r in rows:
        assert (r.iv_ours_pct is None) == (r.iv_quality != "ok")


def test_0dte_kis_iv_fallback_gamma_is_rescaled(snap: Snapshot, cal: TradingCalendar) -> None:
    """0DTE KIS IV 폴백(§1.5): 파이프라인이 KIS σ(0.5일 기준)를 자체 T(53분)로 옮긴다(검증 수정 3).

    합성 1102.5 풋(실측과 같은 성질) — last 6.53 이 내재가치(F 1094.46 기준 8.04) 아래라 역산 실패
    → KIS IV 11.92%. 옮기기 전에는 KIS σ 를 자체 T 에 그대로 써 감마가 사실상 0(−100%)이었다.
    총분산 보존 σ_KIS·√(T_KIS/T) 로 옮기면 감마 크기가 돌아온다. 남는 −64% 는 KIS 가 이 행 그릭스에
    자기 IV 보다 작은 σ(Δ·Γ 가 함의하는 σ√T 로 ≈ 9.53% × √(0.5일))와 다른 기초자산을 쓴 탓이다.
    """
    s = series_rows(snap, "WKM:260904", cal)
    ev = evaluate(s, S_REF)
    assert ev.F is not None
    t_kis = t_years("days_min_half", s.now, s.expiry_date, cal)
    assert t_kis is not None and t_kis == 0.5 / 365 == ev.T_kis
    rows = compare(ev, s.kis, S_REF, n=3)
    assert rows is not None
    fb = {(r.strike, r.cp): r for r in rows}[(D("1102.50"), "P")]
    assert (fb.price_kind, fb.iv_quality, fb.iv_ours_pct) == ("last", "estimated", None)
    assert fb.iv_kis_pct == 11.9151 and fb.gamma_kis == 0.0575
    o = next(o for o in ev.options if (o.quote.strike, o.quote.cp) == (D("1102.50"), "P"))
    assert o.iv is not None and (o.iv.source, o.iv.rescaled, o.iv.t_kis) == ("kis", True, t_kis)

    assert greeks("p", ev.F, 1102.5, ev.T, 0.119151).gamma < 1e-4  # 옮기기 전: 사실상 0
    sigma = rescale_sigma(0.119151, t_kis, ev.T)
    assert o.iv.sigma == pytest.approx(sigma)
    g = greeks("p", ev.F, 1102.5, ev.T, sigma).gamma
    assert g == pytest.approx(greeks("p", ev.F, 1102.5, t_kis, 0.119151).gamma)  # 총분산 보존
    assert fb.gamma_ours == pytest.approx(g)
    assert g / 0.0575 == pytest.approx(0.364, abs=0.01)
    dg = solve_delta_gamma(1102.5, "P", -0.8599, 0.0575)
    assert dg is not None and dg[1] / math.sqrt(t_kis) == pytest.approx(0.0953, abs=0.002)

    # 전후 비교(검증 수정 4): 그대로 = KIS σ 를 자체 T 에(옮기기 전 파이프라인), T 맞춤 = 지금
    got = fallback_gamma(ev, rows)
    assert got is not None
    assert (got.n, got.dropped) == (1, 0)
    assert got.as_is == pytest.approx(greeks("p", ev.F, 1102.5, ev.T, 0.119151).gamma / 0.0575 - 1)
    assert got.as_is is not None and got.as_is < -0.99  # 옮기기 전: −100%
    assert got.rescaled == pytest.approx(g / 0.0575 - 1) == pytest.approx(fb.gamma_rel)
    assert fallback_gamma(ev, [r for r in rows if r is not fb]) is None


def test_fallback_gamma_counts_rows_dropped_by_rescale(
    snap: Snapshot, cal: TradingCalendar
) -> None:
    """폴백 묶음은 파이프라인 품질(estimated)이 아니라 '자체 역산 실패 + KIS IV·감마 있음' 으로
    고른다 — T 맞춤으로 σ > 300% 가 되어 invalid 로 빠진 행도 '그대로' 쪽에 남고 dropped 로 센다.
    §1.2 제외(below_min_premium)·역산 이상치(model_out_of_range)·자체 역산(ok)은 묶음이 아니다."""
    s = series_rows(snap, "WKM:260904", cal)
    ev = evaluate(s, S_REF)
    assert ev.F is not None and ev.T_kis is not None
    rows = compare(ev, s.kis, S_REF, n=3)
    assert rows is not None
    fb = {(r.strike, r.cp): r for r in rows}[(D("1102.50"), "P")]

    def row(reason: str | None, quality: str, iv_kis: float) -> CompareRow:
        return CompareRow(
            D("1060.00"), "P", "board", "last", quality, None, iv_kis, None, 0.001, 0, reason
        )

    dropped = row("below_intrinsic/kis_out_of_range_rescaled", "invalid", 90.0)  # × 3.69 > 300%
    others = [
        row("below_min_premium", "estimated", 20.0),
        row("model_out_of_range", "invalid", 20.0),
        row(None, "ok", 20.0),
        row("below_intrinsic/kis_missing", "invalid", 0.0),
    ]
    only = fallback_gamma(ev, [fb])
    got = fallback_gamma(ev, [fb, dropped, *others])
    assert only is not None and got is not None
    assert (got.n, got.dropped) == (2, 1)
    assert got.rescaled == only.rescaled  # 빠진 행은 T 맞춤 쪽 중앙값에 없다
    assert got.as_is is not None and only.as_is is not None and got.as_is != only.as_is
    assert rescale_sigma(0.9, ev.T_kis, ev.T) > 3.0  # KIS 90% 는 범위 안, 옮기면 밖


# --- T 후보 ---


def test_t_years_bases(cal: TradingCalendar) -> None:
    exp = date(2026, 10, 8)
    t = t_candidates(NOW, exp, cal)
    assert t["minutes"] == time_to_expiry(NOW, expiry_at(exp))
    assert t["days"] == 10 / 365
    assert t["days_incl"] == 11 / 365  # KIS hts_rmnn_dynu 11 과 같은 날수
    assert t["days_min_half"] == 10 / 365
    # 09-28·29·30, 10-01·02·06·07·08 (10-05 대체공휴일)
    assert t["trading_incl"] == 8 / 252


def test_t_years_expiry_day(cal: TradingCalendar) -> None:
    today = date(2026, 9, 28)
    assert t_years("days", NOW, today, cal) is None
    assert t_years("days_incl", NOW, today, cal) == 1 / 365
    assert t_years("days_min_half", NOW, today, cal) == 0.5 / 365
    assert t_years("minutes", NOW, today, cal) == pytest.approx((53 - 18 / 60) / 525600)
    assert t_years("days", NOW, date(2026, 9, 25), cal) is None  # 만기 지남
    with pytest.raises(ValueError):
        t_years("hours", NOW, today, cal)  # type: ignore[arg-type]


# --- KIS 관례 역산 ---


def test_kis_monthly_gamma_is_black_with_hist_vol(snap: Snapshot, cal: TradingCalendar) -> None:
    """22행 모두 σ=hist_vltl·S=선물가·T=달력일/365 Black 감마가 KIS gama 와 4자리까지 같다(합성 —
    KIS 관례로 만든 값, 원래 실측에서 확인한 관례)."""
    s = series_rows(snap, "MONTH:202610", cal)
    ev = evaluate(s, S_REF)
    xs = fit_inputs(ev, s.kis, window_strikes(ev, S_REF) or [])
    fits = {f.name: f for f in fit_gamma(xs, {"fut": S_REF}, {"days": 10 / 365})}
    hist = fits["σ=hist · S=fut · T=days"]
    assert (hist.n, hist.round_match) == (22, 1.0)
    assert hist.main < 0.01 and hist.delta_abs is not None and hist.delta_abs < 0.001
    assert fits["σ=ours · S=fut · T=days"].main > 0.5
    assert fits["σ=kis_iv · S=fut · T=days"].main > 0.5


def test_kis_monthly_iv_is_last_on_futures_with_calendar_days(
    snap: Snapshot, cal: TradingCalendar
) -> None:
    s = series_rows(snap, "MONTH:202610", cal)
    ev = evaluate(s, S_REF)
    assert ev.F is not None
    xs = fit_inputs(ev, s.kis, window_strikes(ev, S_REF) or [])
    t = t_candidates(s.now, s.expiry_date, cal)
    fits = {f.name: f for f in fit_iv(xs, {"fut": S_REF, "F": ev.F}, t, prices=("last",))}
    assert fits["가격=last · S=fut · T=days"].main < 0.2
    # 자체 파이프라인(합성 F·달력 분)과의 차는 거의 다 기초자산 차이다
    assert fits["가격=last · S=F · T=minutes"].main > 2.0
    assert fits["가격=last · S=fut · T=days_incl"].main > 1.0


def test_implied_t_days_synthetic_and_0dte(snap: Snapshot, cal: TradingCalendar) -> None:

    t = 0.5 / 365
    price = black76.price("c", 1095.0, 1100.0, t, 0.14)
    x = FitInput(1100.0, "C", KisRow(14.0, None, None, None, None), None, price, None)
    assert implied_t_days(x, 1095.0) == pytest.approx(0.5, rel=1e-6)
    assert implied_t_days(x, 1095.0, "mid") is None

    s = series_rows(snap, "WKM:260904", cal)
    ev = evaluate(s, S_REF)
    xs = fit_inputs(ev, s.kis, [o.quote.strike for o in ev.options])
    days = [d for x in xs if (d := implied_t_days(x, S_REF)) is not None]
    assert len(days) >= 8
    med = sorted(days)[len(days) // 2]
    assert 0.35 < med < 0.75  # KIS 0DTE IV 는 약 0.5일로 매긴 값


def test_fit_delta_surface_recovers_synthetic() -> None:
    S, sigma, T = 1100.0, 0.5, 10 / 365
    rows = [
        (float(k), cp, greeks("c" if cp == "C" else "p", S, float(k), T, sigma).delta)
        for k in range(1000, 1201, 10)
        for cp in ("C", "P")
    ]
    fit = fit_delta_surface(rows)  # type: ignore[arg-type]
    assert fit is not None
    assert fit.S == pytest.approx(S, abs=1e-6)
    assert fit.x == pytest.approx(sigma * math.sqrt(T), rel=1e-9)
    assert fit.resid_pt < 1e-6
    assert fit.implied_days(sigma) == pytest.approx(10)
    assert fit_delta_surface([(1100.0, "C", 0.5)]) is None
    assert fit_delta_surface([(1100.0, "C", 0.999), (1200.0, "C", 0.001)]) is None  # 범위 밖


def test_fit_delta_surface_on_kis_monthly(snap: Snapshot, cal: TradingCalendar) -> None:
    s = series_rows(snap, "MONTH:202610", cal)
    fit = fit_delta_surface(
        (float(k), cp, r.delta) for (k, cp), r in s.kis.items() if r.delta is not None
    )
    assert fit is not None and fit.resid_pt < 0.5
    assert fit.S == pytest.approx(S_REF, abs=0.5)
    assert fit.implied_days(0.8105) == pytest.approx(10.0, abs=0.05)  # 합성 hist_vltl 81.05%


def test_bootstrap_delta_days_synthetic() -> None:
    """4자리 반올림 Black 델타에서 T 구간이 참값을 담고, 시드가 같으면 같은 구간이다."""
    S, sigma, T = 1095.0, 0.754, 10 / 365
    rows = [
        (k, cp, round(greeks("c" if cp == "C" else "p", S, k, T, sigma).delta, 4))
        for k in [1045.0 + 2.5 * i for i in range(43)]
        for cp in ("C", "P")
    ]
    ci = bootstrap_delta_days(rows, sigma)  # type: ignore[arg-type]
    assert ci is not None
    lo, hi = ci
    assert lo < 10.0 < hi and hi - lo < 0.05
    assert bootstrap_delta_days(rows, sigma) == ci  # type: ignore[arg-type]
    assert bootstrap_delta_days(rows, sigma, seed=1) != ci  # type: ignore[arg-type]
    assert bootstrap_delta_days(rows[:4], sigma) is None  # type: ignore[arg-type]  # 행사가 2개
    assert bootstrap_delta_days(rows[:6], sigma) is not None  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        bootstrap_delta_days(rows, sigma, level=1.0)  # type: ignore[arg-type]


def test_t_bases_within(cal: TradingCalendar) -> None:
    t = t_candidates(NOW, date(2026, 10, 8), cal)  # minutes 10.037일, days 10일
    assert t_bases_within(9.99, 10.01, t) == ("days", "days_min_half")
    assert t_bases_within(9.99, 10.05, t) == ("minutes", "days", "days_min_half")
    assert t_bases_within(10.5, 10.9, t) == ()


def test_monthly_delta_surface_t_interval(snap: Snapshot, cal: TradingCalendar) -> None:
    """월물 델타 곡면 T 는 달력일 10일 — fixture 는 26행이라 구간이 넓어 달력 분도 못 뺀다."""
    r = analyze(series_rows(snap, "MONTH:202610", cal), S_REF, cal)
    assert r.diag is not None and r.diag.delta_days is not None
    est, lo, hi = r.diag.delta_days
    assert est == pytest.approx(10.0, abs=0.02)
    assert "days" in t_bases_within(lo, hi, r.t_values)


def test_solve_delta_gamma_recovers_synthetic() -> None:
    for S, K, sigma, T, cp in [
        (1126.0, 1095.0, 0.754, 8 / 365, "C"),
        (1094.8, 1100.0, 0.14, 0.5 / 365, "P"),
        (1095.0, 1120.0, 0.754, 10 / 365, "C"),
    ]:
        g = greeks("c" if cp == "C" else "p", S, K, T, sigma)
        got = solve_delta_gamma(K, cp, g.delta, g.gamma)  # type: ignore[arg-type]
        assert got is not None
        assert got[0] == pytest.approx(S, rel=1e-6)
        assert got[1] == pytest.approx(sigma * math.sqrt(T), rel=1e-6)
    assert solve_delta_gamma(1100.0, "C", 0.999, 0.001) is None  # 범위 밖 델타
    assert solve_delta_gamma(1100.0, "C", 0.5, 0.0) is None


def test_solve_delta_gamma_shows_stale_weekly_underlying(
    snap: Snapshot, cal: TradingCalendar
) -> None:
    """당일 거래 없는 위클리 행의 KIS 그릭스는 시장(≈1095)이 아닌 전 세션 수준(합성 ≈1124)
    기초자산으로 매겨져 있다."""
    s = series_rows(snap, "WKM:261001", cal)
    stale = s.kis[(D("1095.00"), "P")]
    assert stale.volume == 0 and stale.delta is not None and stale.gamma is not None
    got = solve_delta_gamma(1095.0, "P", stale.delta, stale.gamma)
    assert got is not None and got[0] == pytest.approx(1124.2, abs=3)
    near, far = split_by_s([got, (1094.0, 0.05)], S_REF)
    assert (near.n, far.n) == (1, 1)
    assert near.S == 1094.0 and far.S == got[0]


def test_implied_underlying_synthetic() -> None:
    T = 8 / 365
    for cp, flag in (("C", "c"), ("P", "p")):
        p = black76.price(flag, 1126.3, 1095.0, T, 0.40)  # type: ignore[arg-type]
        got = implied_underlying(p, 1095.0, T, 0.40, cp)  # type: ignore[arg-type]
        assert got == pytest.approx(1126.3, abs=1e-6)
    assert implied_underlying(0.0, 1095.0, T, 0.4, "C") is None
    assert implied_underlying(5000.0, 1095.0, T, 0.4, "C") is None  # [K/4, 4K] 밖


def test_kis_weekly_iv_uses_stale_underlying(snap: Snapshot, cal: TradingCalendar) -> None:
    """무거래 위클리 행의 KIS IV 는 last(전 세션 값)·오늘 T(8일)·S ≈ 1124.2(합성 전 세션 수준)로
    매겨져 있다."""
    s = series_rows(snap, "WKM:261001", cal)
    T = t_years("days_min_half", s.now, s.expiry_date, cal)
    assert T is not None and T == 8 / 365
    got: list[float] = []
    for q in s.quotes:
        k = s.kis[(q.strike, q.cp)]
        if q.strike == D("1095.00") and k.volume == 0:
            assert q.last is not None and k.iv_pct is not None
            u = implied_underlying(float(q.last), float(q.strike), T, k.iv_pct / 100, q.cp)
            assert u is not None
            got.append(u)
    assert len(got) == 2  # 콜·풋
    assert got == [pytest.approx(1124.2, abs=0.1)] * 2


def test_fit_parity() -> None:
    A, Dd = 1094.5, 0.9994
    kis = {
        (D(k), cp): KisRow(None, None, None, None, v)
        for k in ("1080", "1100", "1120")
        for cp, v in (("C", 100.0 + (A - Dd * float(k))), ("P", 100.0))
    }
    fit = fit_parity(kis)  # type: ignore[arg-type]
    assert fit is not None
    assert (fit.A, fit.D, fit.n) == (pytest.approx(A), pytest.approx(Dd), 3)
    assert fit.forward == pytest.approx(A / Dd)
    assert fit.rate(10 / 365) == pytest.approx(-math.log(Dd) * 36.5)
    assert fit_parity({(D("1100"), "C"): KisRow(None, None, None, None, 1.0)}) is None


# --- 보고서 ---


def test_analyze_and_report_smoke(
    snap: Snapshot, cal: TradingCalendar, capsys: pytest.CaptureFixture[str]
) -> None:
    r = analyze(series_rows(snap, "MONTH:202610", cal), S_REF, cal)
    assert r.gamma is not None and r.gamma.within == 0
    assert r.diag is not None and r.diag.stale_last == 3
    assert r.gamma_fits[0].round_match == 1.0  # 1위 후보는 σ=hist
    assert r.gamma_fits[0].name.startswith("σ=hist")
    assert main([str(FIXTURE), "--detail"]) == 0
    out = capsys.readouterr().out
    assert f"### {REPRO_HEADER}" in out and f"### {RECORD_HEADER}" in out
    assert "MONTH:202610" in out and "ATM±5 없음" in out
    repro = out.split(f"### {REPRO_HEADER}")[1].split("####")[0]
    row = next(ln for ln in repro.splitlines() if ln.startswith("| MONTH:202610 |"))
    # 판정(평이 1%): ATM±5 Δ·Γ 22/22, 전체 Δ 26/26·Γ 22/26. 반올림 보정([확인 필요]) 칸은 따로
    assert "| 22/22 (" in row and "| 26/26 (" in row and "| 22/26 (" in row
    assert "| Δ 22/22 · Γ 22/22 | Δ 26/26 · Γ 26/26 |" in row
    assert row.endswith("| 4·0·0·0·0 |")  # 원인 반올림·S·σ√T·S+σ√T·풀이 없음
    assert "#### MONTH:202610 — 재현 ATM±5" in out  # --detail
    # 후보 표 ATM±5(평이) — T = 달력일+1 이면 감마 0/22
    atm = out.split("#### 후보 — ATM±5")[1].split("####")[0]
    arow = next(ln for ln in atm.splitlines() if ln.startswith("| MONTH:202610 |"))
    assert arow.startswith("| MONTH:202610 | Δ 22/22 · Γ 22/22 |")
    assert arow.endswith("| Δ 22/22 · Γ 0/22 |")  # 마지막 칸 T=달력일+1
    # T 훑기 — 만기일 시리즈 한 줄
    sweep = out.split("#### T 훑기")[1].split("####")[0]
    line = next(ln for ln in sweep.splitlines() if ln.startswith("- WKM:260904"))
    assert "전체 12행" in line and "최소 0.50일" in line and "0.10일" in line and "1.00일" in line
    assert "MONTH:202610" not in sweep


def test_report_isolates_series_failures(tmp_path: Path, cal: TradingCalendar) -> None:
    """만기일 모르는 위클리·빈 전광판이 있어도 나머지는 보고되고, 실패는 사유와 함께 남는다."""
    raw = _raw()
    raw["boards"]["WKI:261008"] = copy.deepcopy(raw["boards"]["WKM:260904"])
    raw["boards"]["WKM:261001"] = {"ts_kst": "2026-09-28T14:27:18+09:00"}  # 빈 전광판
    p = tmp_path / "chain_snapshot.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    text = report(p, cal)
    summary = text.split(f"### {RECORD_HEADER}")[1].split("### 진단")[0]
    lines = {ln.split(" | ")[0]: ln for ln in summary.splitlines() if ln.startswith("| ")}
    assert "건너뜀: ValueError: WKI:261008: 만기일을 모른다" in lines["| WKI:261008"]
    assert "건너뜀: ValueError:" in lines["| WKM:261001"]
    # 월물은 그대로 분석된다(+100.7 — F 가 전 세션 가격 1097.5 를 뺀 1089.55, metrics §1.3)
    assert "| 22 | +100.7 |" in lines["| MONTH:202610"]
    assert all(ln.count("|") == 17 for ln in lines.values() if "건너뜀" in ln)  # 16칸
    # 재현 검사 표도 같은 시리즈를 건너뛰고(10칸) 나머지는 본다
    repro = text.split(f"### {REPRO_HEADER}")[1].split("####")[0]
    rlines = {ln.split(" | ")[0]: ln for ln in repro.splitlines() if ln.startswith("| ")}
    assert "건너뜀: ValueError: WKI:261008" in rlines["| WKI:261008"]
    assert all(ln.count("|") == 13 for ln in rlines.values())  # 12칸 — 건너뜀 줄도
    assert "| 22/22 (" in rlines["| MONTH:202610"]
    # --expiry 로 만기일을 주면 그 시리즈도 분석된다
    out = report(p, cal, given={"WKI:261008": date(2026, 10, 8)})
    assert "| WKI:261008 | 2026-10-08 (인자) |" in out


def test_main_isolates_snapshot_failures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "없음.json"
    assert main([str(missing), str(FIXTURE), "--expiry", "WKI:261008=20261008"]) == 1
    out = capsys.readouterr().out
    assert f"## {missing} — 읽지 못함: FileNotFoundError" in out
    assert "| MONTH:202610 |" in out  # 뒤 스냅샷은 계속 본다


# --- §6.2 KIS 관례 재현 검사 (metrics §1.7) ---


def test_board_spot_from_intrinsic_value(snap: Snapshot, cal: TradingCalendar) -> None:
    """전광판 내재가치가 함의하는 S 는 전광판마다 하나다(조회 시점 현물 — 선물 1095.1 과 다르다)."""
    spots = {label: series_rows(snap, label, cal).spot for label in snap.boards}
    assert spots == {
        "MONTH:202610": pytest.approx(1094.55),
        "MONTH:202611": pytest.approx(1094.52),
        "WKM:260904": pytest.approx(1094.87),
        "WKM:261001": pytest.approx(1094.47),
    }
    assert board_spot(Board(ts_kst=NOW)) is None


def test_kis_t_matches_kis_days(snap: Snapshot, cal: TradingCalendar) -> None:
    """재현 검사 T 는 core `kis_time_to_expiry`(metrics §1.7) — max(달력일, 0.5)/365. 시각은 안
    보고, 날수 = 실측 hts_rmnn_dynu − 1, 주말·휴일도 센다. 스크립트에 따로 두지 않는다."""
    exp = date(2026, 10, 8)
    assert kis_time_to_expiry(NOW, exp) == 10 / 365 == t_years("days_min_half", NOW, exp, cal)
    rmnn = series_rows(snap, "MONTH:202610", cal).kis[(D("1100.00"), "C")].rmnn
    assert rmnn is not None and kis_time_to_expiry(NOW, exp) == (rmnn - 1) / 365
    assert kis_time_to_expiry(NOW, date(2026, 10, 6)) == 8 / 365  # 10-03~05 주말·대체공휴일
    for hh, mm in ((8, 45), (14, 27), (15, 19), (15, 30)):  # 만기일은 시각과 무관하게 0.5일
        now = datetime(2026, 9, 28, hh, mm, tzinfo=KST)
        assert kis_time_to_expiry(now, date(2026, 9, 28)) == 0.5 / 365
    # UTC 로 준 시각도 KST 날짜로 센다(09-27 16:00 UTC = 09-28 01:00 KST)
    assert kis_time_to_expiry(datetime(2026, 9, 27, 16, 0, tzinfo=UTC), exp) == 10 / 365
    rep = reproduce(series_rows(snap, "MONTH:202610", cal), S_REF, None)
    assert rep is not None and rep.T == kis_time_to_expiry(NOW, exp)


def test_rel_err_is_plain_relative_error() -> None:
    """PLAN §6.2 결정 오차: 크기는 |자체/KIS − 1| 그대로(반올림 보정 없음), 부호는 자체 − KIS."""
    assert rel_err(0.00292, 0.0029) == pytest.approx(0.02 / 2.9)  # 반올림 구간 안이어도 0 아님
    assert rel_err(0.0029, 0.0029) == 0.0
    assert rel_err(0.000209, 0.0002) == pytest.approx(0.045)
    # 풋(KIS 음수): 자체 −0.5 는 KIS −0.4894 보다 작다 → 음수. 크기는 |자체/KIS − 1|
    assert rel_err(-0.5, -0.4894) == pytest.approx(-(0.5 / 0.4894 - 1))
    assert abs(rel_err(-0.5, -0.4894)) == pytest.approx(abs(-0.5 / -0.4894 - 1))
    with pytest.raises(ValueError):
        rel_err(0.001, 0.0)


@pytest.mark.parametrize(
    ("ours", "kis"),
    [(0.0030, 0.0029), (0.0028, 0.0029), (-0.5, -0.4894), (-0.47, -0.4894), (0.93, 0.9)],
)
def test_rel_err_sign_matches_rounding_err(ours: float, kis: float) -> None:
    """--detail 에 두 오차를 나란히 쓰므로 부호 관례가 같아야 한다(둘 다 자체 − KIS)."""
    plain, rounded = rel_err(ours, kis), kis_rel_err(ours, kis)
    assert rounded != 0.0
    assert (
        math.copysign(1.0, plain) == math.copysign(1.0, rounded) == math.copysign(1.0, ours - kis)
    )


def test_kis_rel_err_rounding_interval() -> None:
    """반올림 보정 오차([확인 필요] 제안): KIS 4자리 반올림 구간 안이면 0, 밖이면 벗어난 만큼
    / |KIS| (부호는 자체 − KIS)."""
    assert kis_rel_err(0.00292, 0.0029) == 0.0  # 0.00285~0.00295 안
    assert kis_rel_err(0.00285, 0.0029) == 0.0
    assert kis_rel_err(0.0030, 0.0029) == pytest.approx(0.00005 / 0.0029)
    assert kis_rel_err(0.0028, 0.0029) == pytest.approx(-0.00005 / 0.0029)
    assert kis_rel_err(-0.5, -0.4894) == pytest.approx(-(0.0106 - 0.00005) / 0.4894)
    assert kis_rel_err(0.3, 0.2, half_ulp=0.0) == pytest.approx(0.5)
    with pytest.raises(ValueError):
        kis_rel_err(0.001, 0.0)


def test_kis_sigma_convention() -> None:
    def row(hist: float | None, iv: float | None, vol: int | None) -> KisRow:
        return KisRow(iv, 0.003, 0.5, hist, None, vol)

    assert kis_sigma(row(75.38, 40.0, 0), 75.4) == (pytest.approx(0.7538), "hist")  # 월물
    assert kis_sigma(row(None, 40.0, 12), 75.4) == (pytest.approx(0.40), "kis_iv")  # 당일 거래
    assert kis_sigma(row(None, 40.0, None), 75.4) == (pytest.approx(0.40), "kis_iv")  # 모름
    assert kis_sigma(row(None, 40.0, 0), 75.4) == (pytest.approx(0.754), "hist_ref")  # 무거래
    assert kis_sigma(row(None, 40.0, 0), None) is None
    assert kis_sigma(row(None, None, 5), 75.4) is None


def test_kis_underlying_convention() -> None:
    good = ParityFit(A=1094.5, D=0.9994, n=3, resid=0.4)
    assert kis_underlying(good, 1095.1) == (pytest.approx(1094.5 / 0.9994), "parity")
    assert kis_underlying(ParityFit(1094.5, 0.9994, 2, 0.0), 1095.1) == (1095.1, "fut")
    assert kis_underlying(ParityFit(1094.5, 0.9994, 36, 39.96), 1095.1) == (1095.1, "fut")
    assert kis_underlying(None, 1095.1) == (1095.1, "fut")


def test_reproduce_row_attributes_cause_synthetic() -> None:
    """Black 값을 KIS 처럼 4자리로 반올림하면 통과하고, 어긋난 입력이 원인으로 잡힌다."""
    S, K, T, sigma = 1095.0, 1100.0, 3 / 365, 0.40

    def kis_of(s: float, sg: float) -> KisRow:
        g = greeks("c", s, K, T, sg)
        return KisRow(None, round(g.gamma, 4), round(g.delta, 4), None, None, 10)

    ok = reproduce_row(D("1100"), "C", kis_of(S, sigma), (sigma, "kis_iv"), S, T)
    assert ok.cause is None
    assert ok.delta_err is not None and ok.gamma_err is not None
    assert abs(ok.delta_err) <= 0.01 and abs(ok.gamma_err) <= 0.01  # 판정: 평이 상대오차
    assert ok.err("delta", "rounding") == 0.0 and ok.err("gamma", "rounding") == 0.0
    stale = reproduce_row(D("1100"), "C", kis_of(1126.0, sigma), (sigma, "kis_iv"), S, T)
    assert stale.cause == "S" and stale.implied is not None
    assert stale.implied[0] == pytest.approx(1126.0, abs=0.5)
    other_sigma = reproduce_row(D("1100"), "C", kis_of(S, 0.55), (sigma, "kis_iv"), S, T)
    assert other_sigma.cause == "σ√T"
    assert other_sigma.implied_sigma(T) == pytest.approx(0.55, rel=0.01)
    both = reproduce_row(D("1100"), "C", kis_of(1126.0, 0.55), (sigma, "kis_iv"), S, T)
    assert both.cause == "S+σ√T"
    no_gamma = KisRow(None, None, 0.9, None, None, 10)  # 감마 0(없음)이면 한 쌍을 못 푼다
    assert reproduce_row(D("1100"), "C", no_gamma, (sigma, "kis_iv"), S, T).cause == "풀이 없음"


def test_reproduce_row_plain_metric_and_rounding_cause_synthetic() -> None:
    """판정은 평이 상대오차 1%(사용자 결정). 입력이 같아도 KIS 4자리 반올림이 1% 를 넘기는 작은
    값(날개 감마 0.0002 는 반올림 구간 ±25%)은 실패로 세고, 원인을 `반올림` 으로 적는다 — 반올림
    보정 오차([확인 필요] 제안)로는 통과. ATM 감마 0.0029 는 반올림 구간이 ±1.7% 여도 평이 1% 로
    통과한다."""
    S, T, sigma = 1095.0, 10 / 365, 0.754

    def kis_of(K: float) -> KisRow:
        g = greeks("c", S, K, T, sigma)
        return KisRow(None, round(g.gamma, 4), round(g.delta, 4), 75.4, None, 115)

    wing = reproduce_row(D("1470"), "C", kis_of(1470.0), (sigma, "hist"), S, T)
    assert wing.kis_gamma == 0.0002
    assert wing.gamma_err == pytest.approx(0.0427, abs=0.001)  # 평이 상대오차 — 실패
    assert wing.err("gamma", "plain") == wing.gamma_err
    assert wing.err("gamma", "rounding") == 0.0  # 반올림 구간 안
    assert wing.cause == "반올림"
    atm = reproduce_row(D("1100"), "C", kis_of(1100.0), (sigma, "hist"), S, T)
    assert atm.kis_gamma == 0.0029 and atm.gamma_err == pytest.approx(0.0063, abs=0.0002)
    assert atm.cause is None
    # 입력이 틀리면(S) 반올림이 아니라 그 입력이 원인이다
    stale = reproduce_row(D("1100"), "C", kis_of(1100.0), (sigma, "hist"), S - 30.0, T)
    assert stale.cause == "S"


def test_snapshot_hist(snap: Snapshot) -> None:
    assert snapshot_hist(snap) == pytest.approx(
        81.050, abs=0.002
    )  # 월물 행 중앙값, 위클리 0 은 뺀다


def test_monthly_reproduction_passes(snap: Snapshot, cal: TradingCalendar) -> None:
    """월물 KIS 그릭스 = Black(σ = hist_vltl, S = 이론가 패리티 선도, T = 달력일/365) — 다 통과."""
    s = series_rows(snap, "MONTH:202610", cal)
    ev = evaluate(s, S_REF)
    rp = reproduce(s, S_REF, snapshot_hist(snap), ev.F, cal)
    assert rp is not None
    assert (rp.T, rp.s_src) == (10 / 365, "parity")
    assert rp.S == pytest.approx(1095.16, abs=0.01)
    assert rp.window is not None and len(rp.window) == 11
    # 판정(평이 상대오차 1%): ATM±5 는 전 행. 전체 행은 날개 감마(합성: 1595 Γ 0.0001, 1470 Γ 0.0003
    # 콜·풋)가 KIS 4자리 반올림만으로 1% 를 넘는다 — 반올림 보정([확인 필요] 제안)으로는 전 행
    cases: tuple[tuple[bool, ErrMetric, tuple[int, int, int, int]], ...] = (
        (True, "plain", (22, 22, 22, 22)),
        (False, "plain", (26, 26, 22, 26)),
        (True, "rounding", (22, 22, 22, 22)),
        (False, "rounding", (26, 26, 26, 26)),
    )
    for atm_only, metric, want in cases:
        d, g = rp.stats("delta", atm_only, metric), rp.stats("gamma", atm_only, metric)
        assert d is not None and g is not None
        assert (d.within, d.n, g.within, g.n) == want, (atm_only, metric)
    atm_gamma = rp.stats("gamma", True)
    assert atm_gamma is not None and atm_gamma.max == pytest.approx(0.0061, abs=0.0001)
    assert rp.causes() == {"반올림": 4} and rp.causes(atm_only=True) == {}
    assert rp.sigma_sources() == {"hist": 26}
    assert rp.t_implied_days == pytest.approx(10.0, abs=0.2)
    assert rp.delta_days is not None  # 델타 곡면 T — 26행이라 구간이 넓다(전체 스냅샷은 ±0.01일)
    est, lo, hi = rp.delta_days
    assert est == pytest.approx(10.0, abs=0.05) and lo < 10.0 < hi
    # 후보: T = 달력일+1(hts_rmnn_dynu)이면 감마가 전부, S = 합성 F 면 델타가 거의 다 빠진다
    t_incl = rp.candidate_counts("T=달력일+1")
    assert t_incl is not None and t_incl.gamma == 0
    # ATM±5 로 좁혀도 센다(metrics §1.7 근거 "T = 11일이면 ATM±5 감마 0/22")
    t_incl_atm = rp.candidate_counts("T=달력일+1", atm_only=True)
    assert t_incl_atm is not None and (t_incl_atm.gamma, t_incl_atm.gamma_n) == (0, 22)
    assert (t_incl_atm.delta, t_incl_atm.delta_n) == (22, 22)
    assert rp.t_sweep == () and rp.t_sweep_best is None  # T 훑기는 만기일(D = 0) 시리즈만
    synth = rp.candidate_counts("S=합성 F")
    assert synth is not None and synth.delta == 2
    assert rp.candidate_counts("없는 후보") is None


def test_monthly_202611_needs_kis_forward_not_futures(snap: Snapshot, cal: TradingCalendar) -> None:
    """202611(45일)은 KIS S 가 선물가보다 3pt 높은 이론 선도라 S=선물이면 델타 절반이 빠진다.

    만기일은 계산값(11-12)이고 T = 45/365 로 통과한다 — 달력일+1(46일)이면 델타가 빠진다.
    """
    s = series_rows(snap, "MONTH:202611", cal)
    rp = reproduce(s, S_REF, snapshot_hist(snap), None, cal)
    assert rp is not None
    assert (s.expiry_date, s.expiry_source, rp.T) == (date(2026, 11, 12), "computed", 45 / 365)
    assert rp.s_src == "parity" and rp.S - S_REF == pytest.approx(3.4, abs=0.3)
    assert rp.window is None  # 전광판 잘림 — ATM±5 없음
    # 감마 0.0011 은 반올림 구간 ±4.5% — 평이 1% 로는 0/6(합성), 반올림 보정으로는 6/6
    assert _counts(rp) == (6, 6, 0, 6)
    assert _counts(rp, "rounding") == (6, 6, 6, 6)
    assert rp.causes() == {"반올림": 6}
    fut, t_incl = rp.candidate_counts("S=선물"), rp.candidate_counts("T=달력일+1")
    assert fut is not None and (fut.delta, fut.delta_n) == (3, 6)
    assert t_incl is not None and (t_incl.delta, t_incl.delta_n) == (3, 6)
    assert rp.candidate_counts("S=선물", atm_only=True) is None  # ATM±5 없음


def _counts(rp: Any, metric: ErrMetric = "plain") -> tuple[int, int, int, int]:
    d, g = rp.stats("delta", False, metric), rp.stats("gamma", False, metric)
    return (d.within, d.n, g.within, g.n)


def test_0dte_reproduction_uses_half_day(snap: Snapshot, cal: TradingCalendar) -> None:
    """0DTE: T = 0.5일(15:20 까지 53분이 아니다), σ = 행 KIS IV. 행마다 계산 시점 S 가 달라
    못 맞춘다."""
    s = series_rows(snap, "WKM:260904", cal)
    ev = evaluate(s, S_REF)
    rp = reproduce(s, S_REF, snapshot_hist(snap), ev.F, cal)
    assert rp is not None
    assert (rp.T, rp.S, rp.s_src) == (0.5 / 365, S_REF, "fut")
    assert rp.sigma_sources() == {"kis_iv": 12}
    assert rp.t_implied_days == pytest.approx(0.5, abs=0.05)
    minutes = rp.candidate_counts("T=달력 분")
    assert minutes is not None and (minutes.gamma, minutes.gamma_n) == (0, 12)
    # T 훑기: 관례 S·σ 그대로 T 만 0.10~1.00일 — 감마 |평이 오차| 중앙이 0.5일에서 가장 작다
    # (fixture 는 ATM±5 를 못 덮어 전체 12행)
    assert not rp.t_sweep_atm
    assert [p.days for p in rp.t_sweep] == [round(0.05 * i, 2) for i in range(2, 21)]
    assert all(p.n == 12 for p in rp.t_sweep)
    assert rp.t_sweep_best == 0.5
    by_days = {p.days: p for p in rp.t_sweep}
    assert by_days[0.5].median == pytest.approx(0.0424, abs=0.0005)
    assert by_days[0.1].median > 0.5 and by_days[1.0].median > 0.2
    causes = rp.causes()
    assert set(causes) <= {"S", "S+σ√T"} and sum(causes.values()) == 12
    implied_s = [r.implied[0] for r in rp.rows if r.implied is not None]
    assert statistics.median(implied_s) == pytest.approx(1094.58, abs=0.1)


def test_weekly_untraded_rows_use_hv_and_previous_level(
    snap: Snapshot, cal: TradingCalendar
) -> None:
    """위클리 무거래 행: σ = 기초 HV(월물 hist_vltl)·T = 8일은 맞고 S 만 전 세션 수준
    (합성 ≈ 1124)."""
    s = series_rows(snap, "WKM:261001", cal)
    rp = reproduce(s, S_REF, snapshot_hist(snap), None, cal)
    assert rp is not None
    untraded = [r for r in rp.rows if r.volume == 0]
    assert len(untraded) == 3
    for r in untraded:
        assert (r.sigma_src, r.cause) == ("hist_ref", "S")
        assert r.implied is not None and r.implied[0] == pytest.approx(1124.2, abs=0.5)
        assert r.implied_sigma(rp.T) == pytest.approx(0.8105, abs=0.02)


# --- 파이프라인 요약 (--summary — 검증 수정 4 전후 비교) ---


def _later(price: float, code: str = "A01612") -> Snapshot:
    """fixture 를 25분 뒤·다른 근월물 선물가로 찍은 것처럼 — 행은 그대로(합성 F 가 같게)."""
    raw = _raw()
    raw["started_kst"] = "2026-09-28T14:52:36+09:00"
    raw["atm_ref"] = {"code": code, "price": price}
    return Snapshot.model_validate(raw)


def _by_label(sm: Any) -> dict[str, SeriesSummary]:
    return {s.series.label: s for s in sm.series if isinstance(s, SeriesSummary)}


def test_evaluate_passes_forward_basis(snap: Snapshot, cal: TradingCalendar) -> None:
    s = series_rows(snap, "MONTH:202610", cal)
    assert evaluate(s, S_REF).forward.notes == ("no_futures_ref",)
    fwd = evaluate(s, S_REF, D("-5.55")).forward
    assert fwd.reference is not None and fwd.reference.kind == "near_basis"
    assert fwd.reference.price == D("1089.55")  # 근월물 1095.1 + 베이시스 −5.55
    assert fwd.F == 1089.55 and fwd.futures_gap == pytest.approx(0.0)


def test_summarize_carries_confirmed_basis(snap: Snapshot, cal: TradingCalendar) -> None:
    """첫 스냅샷은 기준가가 없고(no_futures_ref), 그 ok F 로 확정한 베이시스(F − 근월물 선물가)가
    다음 스냅샷의 §1.3 기준가(근월물 + 베이시스)가 된다. 입력 순서와 무관하게 시각 순으로 돈다."""
    out = summarize([("later", _later(1094.1)), ("first", snap)], cal)
    assert [o.name for o in out] == ["first", "later"]
    first, later = (_by_label(o) for o in out)
    m1, m2 = first["MONTH:202610"], later["MONTH:202610"]
    assert (m1.basis, m1.ev.forward.reference, m1.ev.forward.quality) == (None, None, "ok")
    assert "no_futures_ref" in m1.ev.forward.notes
    assert m1.ev.F == 1089.55
    assert m2.basis == D("1089.55") - D("1095.1")  # −5.55
    ref = m2.ev.forward.reference
    assert ref is not None and ref.price == D("1094.1") + D("-5.55")
    assert m2.ev.forward.futures_gap == pytest.approx(1.0)  # 같은 행이라 F 그대로
    assert m2.ev.forward.quality == "ok"  # |gap| ≤ 2pt
    # F 가 없는(ok F 가 한 번도 없는) 만기는 베이시스도 없다 — 기준가 없음, 선물 대체도 없음
    w2 = later["WKM:261001"]
    assert (w2.basis, w2.ev.F, w2.ev.forward.reference) == (None, None, None)

    # 근월물 선물가가 2.1pt 넘게 내려가면(F 는 그대로) 기준가 차 > 2pt → estimated
    far = _by_label(summarize([("first", snap), ("later", _later(1092.0))], cal)[1])
    fwd = far["MONTH:202610"].ev.forward
    assert fwd.futures_gap is not None and abs(fwd.futures_gap) > 2.0
    assert (fwd.quality, fwd.reasons) == ("estimated", ("futures_ref_gap",))

    # 근월물 코드가 바뀌면 옛 근월물 기준 베이시스는 버린다
    rolled = _by_label(summarize([("first", snap), ("later", _later(1094.1, "A01703"))], cal)[1])
    assert rolled["MONTH:202610"].basis is None
    assert rolled["MONTH:202610"].ev.forward.reference is None


def test_summary_uses_core_exposure_and_levels(snap: Snapshot, cal: TradingCalendar) -> None:
    """순GEX·Flip·ATM IV·§3.8 은 core 함수 값 그대로 — 시리즈별과 범위 all·0dte."""
    (sm,) = summarize([("first", snap)], cal)
    got = _by_label(sm)
    for label, s in got.items():
        series = series_rows(snap, label, cal)
        ev = evaluate(series, S_REF)
        assert s.gex.value == net_gex([ev]).value
        assert s.fb_gex == fallback_gex_vs_kis(ev, series.kis)
        assert s.flip.level == gamma_flip([ev]).level
        assert s.atm.value == atm_iv(ev).value
    evs = [replace(evaluate(series_rows(snap, label, cal), S_REF), expiry=label) for label in got]
    assert sm.gex_all == net_gex(evs)
    assert sm.flip_all.level == gamma_flip(evs).level
    assert sm.trade_date == date(2026, 9, 28)
    assert sm.gex_0dte is not None and sm.gex_0dte.expiries == ("WKM:260904",)
    assert sm.gex_0dte.value == got["WKM:260904"].gex.value
    m = got["MONTH:202610"]
    cal_m, trade_m = m.moves
    assert (cal_m.basis, trade_m.basis) == ("calendar", "trading")
    assert cal_m.sigma is not None and trade_m.sigma is not None
    assert trade_m.sigma / cal_m.sigma == pytest.approx(math.sqrt(365 * 24 * 60 / (252 * 420)))
    assert m.repro is not None and m.repro.counts(True) is not None
    # F 없는 만기: 순GEX·Flip·±1σ 없음(invalid)
    w = got["WKM:261001"]
    assert (w.gex.value, w.gex.quality, w.flip.f_ref, w.moves[0].sigma) == (
        None,
        "invalid",
        None,
        None,
    )


def test_scope_evals_relabels_shared_expiry_codes(snap: Snapshot, cal: TradingCalendar) -> None:
    """WKI·WKM 261001 처럼 만기 코드가 같은 두 시리즈 — core 범위 합산은 코드로 가려 ValueError 라
    요약은 라벨로 바꿔 넣는다(값은 그대로)."""
    raw = _raw()
    raw["boards"]["WKI:261001"] = copy.deepcopy(raw["boards"]["WKM:261001"])
    two = Snapshot.model_validate(raw)
    (sm,) = summarize([("x", two)], cal)
    got = _by_label(sm)
    assert got["WKI:261001"].ev.expiry == got["WKM:261001"].ev.expiry == "261001"
    with pytest.raises(ValueError, match="같은 만기가 두 번"):
        select_scope([got["WKI:261001"].ev, got["WKM:261001"].ev], "all")
    evs = scope_evals(sm.series)
    assert [e.expiry for e in evs] == list(got)
    assert set(sm.gex_all.expiries) == set(got)
    assert sm.gex_all.value == pytest.approx(
        math.fsum(s.gex.value for s in got.values() if s.gex.value is not None)
    )


def test_fallback_counts_rescaled_and_dropped(cal: TradingCalendar) -> None:
    """§1.5 폴백 행 수: fixture 0DTE 1102.5 풋은 쓴·GEX·T 환산 1행. KIS IV 90% 로 바꾸면 옮긴 σ
    (× √(0.5일/53분) ≈ 3.69 → 332%)가 §6.1 밖이라 GEX 에서 빠지고(OI 641), §1.2 제외 행(가격
    0.01)이 같은 이유로 invalid 가 되면 표시용으로 센다."""
    raw = _raw()
    s = series_rows(Snapshot.model_validate(raw), "WKM:260904", cal)
    base = fallback_counts(evaluate(s, S_REF))
    assert (base.used, base.in_gex, base.rescaled, base.dropped_gex, base.dropped_display) == (
        1,
        1,
        1,
        0,
        0,
    )
    puts = raw["boards"]["WKM:260904"]["output2"]
    p1102 = next(r for r in puts if r["acpr"] == "1102.50")
    p1087 = next(r for r in puts if r["acpr"] == "1087.50")
    p1102["hts_ints_vltl"] = "90.0"
    p1087.update(optn_bidp="0.01", optn_askp="0.01", optn_prpr="0.01", hts_ints_vltl="90.0")
    s2 = series_rows(Snapshot.model_validate(raw), "WKM:260904", cal)
    ev = evaluate(s2, S_REF)
    got = fallback_counts(ev)
    assert got == FallbackCounts(0, 0, 0, 1, 1, 641)
    o = next(o for o in ev.options if (o.quote.strike, o.quote.cp) == (D("1102.50"), "P"))
    assert o.iv is not None and o.iv.reason == "below_intrinsic/kis_out_of_range_rescaled"
    assert o.excluded and o.iv.rescaled


def test_fallback_gamma_rel_uses_included_fallback_rows() -> None:
    def row(quality: str, ours: float | None, kis: float | None) -> CompareRow:
        return CompareRow(D("1100"), "P", "board", "last", quality, None, 20.0, ours, kis)

    rows = [
        row("estimated", 0.03, 0.04),  # 폴백·GEX 포함 → −25%
        row("estimated", 0.02, 0.04),  # → −50%
        row("estimated", None, 0.04),  # §1.2 제외(표시용 σ) — 감마 없음
        row("estimated", 0.02, None),  # KIS 감마 없음
        row("ok", 0.01, 0.04),  # 자체 역산
    ]
    assert fallback_gamma_rel(rows) == (2, pytest.approx(-0.375))
    assert fallback_gamma_rel([]) == (0, None)


def test_fallback_gex_vs_kis_compares_like_rows(snap: Snapshot, cal: TradingCalendar) -> None:
    """GEX 에 든 KIS IV 폴백 행의 자체 GEX 를 같은 행 KIS `gama` 로 매긴 값과 견주는 진단은 KIS
    감마가 있는 행끼리만 비를 낸다. KIS 가 gama 0(없음 — Δ −1.0 인 깊은 ITM, 실측 0DTE 1105 풋)으로
    준 행은 분모에 없으니 분자에서도 빼 따로 적는다(재검토: 모두 넣으면 14:27 0.92 배, 같은 행끼리
    0.67 배). 자체 역산·§1.2 제외 행은 폴백 묶음이 아니다."""
    s = series_rows(snap, "WKM:260904", cal)
    ev = evaluate(s, S_REF)
    assert ev.F is not None
    p1102 = next(o for o in ev.options if (o.quote.strike, o.quote.cp) == (D("1102.50"), "P"))
    assert p1102.greeks is not None and p1102.iv is not None and p1102.iv.source == "kis"
    ours = option_gex("P", p1102.greeks.gamma, 641, ev.F)
    base = fallback_gex_vs_kis(ev, s.kis)
    assert (base.n, base.no_kis) == (1, ())
    assert base.ours == pytest.approx(ours) and base.total == pytest.approx(ours)
    assert base.kis == pytest.approx(option_gex("P", 0.0575, 641, ev.F))
    assert base.ratio == pytest.approx(p1102.greeks.gamma / 0.0575)
    assert base.rel_median == pytest.approx(p1102.greeks.gamma / 0.0575 - 1)

    # KIS 가 gama 0·Δ −1.0 으로 준 폴백 행을 더해도 같은 행끼리의 비는 그대로다
    k1105 = D("1105.00")
    extra = replace(p1102, quote=p1102.quote.model_copy(update={"strike": k1105, "oi": 251}))
    ev2 = replace(ev, options=(*ev.options, extra))
    no_gama = KisRow(iv_pct=22.7151, gamma=None, delta=-1.0, hist_pct=None, thpr=8.97)
    got = fallback_gex_vs_kis(ev2, {**s.kis, (k1105, "P"): no_gama})
    assert (got.n, got.ours, got.kis, got.ratio) == (1, base.ours, base.kis, base.ratio)
    assert got.rel_median == base.rel_median
    ((strike, cp, oi, gex, delta),) = got.no_kis
    assert (strike, cp, oi, delta) == (k1105, "P", 251, -1.0)
    assert gex == pytest.approx(option_gex("P", p1102.greeks.gamma, 251, ev.F))
    assert got.total == pytest.approx(base.ours + gex)
    # KIS 행이 아예 없어도 같은 쪽(비교 밖)으로 — 델타 없음
    miss = fallback_gex_vs_kis(ev2, s.kis)
    assert miss.n == 1 and [(r[0], r[4]) for r in miss.no_kis] == [(k1105, None)]

    # 폴백 행이 없으면 비는 없다
    m = series_rows(snap, "MONTH:202610", cal)
    none = fallback_gex_vs_kis(evaluate(m, S_REF), m.kis)
    assert (none.n, none.ours, none.kis, none.ratio, none.rel_median, none.no_kis) == (
        0,
        0.0,
        0.0,
        None,
        None,
        (),
    )


def test_main_summary_smoke(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    raw = _raw()
    raw["boards"]["WKI:261008"] = copy.deepcopy(raw["boards"]["WKM:260904"])  # 만기일 모름
    p = tmp_path / "run" / "chain_snapshot.json"
    p.parent.mkdir()
    p.write_text(json.dumps(raw), encoding="utf-8")
    missing = tmp_path / "없음.json"
    assert main([str(missing), str(p), "--summary"]) == 1
    out = capsys.readouterr().out
    assert f"## {missing} — 읽지 못함: FileNotFoundError" in out
    assert f"### {SUMMARY_HEADER}" in out and f"### {REPRO_HEADER}" not in out
    assert out.count("| WKI:261008 | 건너뜀: ValueError: WKI:261008: 만기일을 모른다") == 3
    tables = [ln for ln in out.splitlines() if ln.startswith("| MONTH:202610 |")]
    assert len(tables) == 5  # F 표·폴백 표·폴백 vs KIS 감마 표·재현 표·§3.8 표
    assert tables[0].startswith("| MONTH:202610 | 14453 | 1089.55 (ok) | 4·1 | -5.55 | 없음 |")
    assert tables[2] == "| MONTH:202610 | — (0) | — | — | — | 없음 |"
    assert "| 22/22 | 22/22 | 26/26 | 22/26 |" in tables[3]
    assert tables[4].endswith("| 78 | 5.50 | 12.25 | 2.23 |")
    (fb_kis,) = [ln for ln in out.splitlines() if ln.startswith("| WKM:260904 | -")]
    assert re.fullmatch(
        r"\| WKM:260904 \| -[\d.,]+ \(1\) \| -[\d.,]+ · -[\d.,]+ \(1\) "
        r"\| 0\.\d\d \| -\d+% \| 없음 \|",
        fb_kis,
    ), fb_kis
    assert "- 범위 all 순GEX" in out and "- 범위 0dte(2026-09-28: WKM:260904) 순GEX" in out
    assert main([str(FIXTURE), "--summary"]) == 0
    assert summary_report([]) == ""
