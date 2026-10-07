"""코어 파이프라인 골든 (PLAN §6.3 골든, scripts/make_golden.py `core`).

- 작은 체인 fixture 두 개(2026-09-28 14:27·14:52 — 합성 전체 스냅샷에서 자른 것, KBJ P1)와
  야간 파생 입력 하나
  (14:52 의 시각만 2026-10-07 수 21:52 로 옮긴 것 — `NIGHT_DERIVED`, 실측 야간 체인이 아니다)를 시각
  순으로 파이프라인에 넣은 결과가 tests/golden/core/chain_synthetic_20260928_small.json 과 허용오차
  (`TOLERANCES`) 안에서 같다. 다르면 차이(JSON 경로)와 다시 만드는 명령을 찍는다 — 수식·기본값·
  fixture 를 **일부러** 바꿨을 때만 다시 만들고 diff 를 검토한다
- 골든이 덮는 갈래를 따로 고정한다(아래 '덮는 갈래') — 다시 만들다 갈래를 잃으면 여기서 걸린다
- 비교기: 허용 경계, 단위를 모르는 float, 형·길이·키 차이, CLI(--write 없으면 쓰지 않음)
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import scripts.make_golden as mg
from core.calendar import expiry_at, session_bounds
from core.levels import CALENDAR_MINUTES_PER_YEAR, TRADING_MINUTES_PER_YEAR
from scripts.make_golden import (
    CORE_FIXTURES,
    CORE_GOLDEN,
    CORE_INPUTS,
    NIGHT_DERIVED,
    REGEN_CORE,
    ROOT,
    Derived,
    GoldenSchemaError,
    InputError,
    compare_golden,
    core_golden,
    failure_report,
    main,
    tolerance,
)

Golden = dict[str, Any]


@pytest.fixture(scope="module")
def golden() -> Golden:
    return json.loads(CORE_GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def actual() -> Golden:
    return json.loads(json.dumps(core_golden()))  # 파일과 같게 JSON 을 한 번 지난 값


def test_core_pipeline_matches_golden(golden: Golden, actual: Golden) -> None:
    diffs = compare_golden(golden, actual)
    assert not diffs, failure_report(diffs, REGEN_CORE)


def test_golden_pins_its_inputs_and_command(golden: Golden) -> None:
    assert golden["_regenerate"] == REGEN_CORE
    paths = [*CORE_FIXTURES, NIGHT_DERIVED.source]
    assert CORE_INPUTS == (*CORE_FIXTURES, NIGHT_DERIVED)
    assert [i["path"] for i in golden["inputs"]] == [p.relative_to(ROOT).as_posix() for p in paths]
    assert [i.get("derived") for i in golden["inputs"]] == [
        None,
        None,
        {"shift_minutes": 9 * 1440 + 7 * 60, "note": NIGHT_DERIVED.note},
    ]
    for i in golden["inputs"]:
        sha = hashlib.sha256((ROOT / i["path"]).read_bytes()).hexdigest()
        assert sha == i["sha256"], f"fixture {i['path']} 가 바뀌었다 — 의도했으면 {REGEN_CORE}"


def test_every_golden_float_has_a_tolerance(golden: Golden) -> None:
    assert compare_golden(golden, golden) == []  # 단위를 모르는 float 키면 GoldenSchemaError


# ── 덮는 갈래 (골든 내용) ─────────────────────────────────────────────────────


def _snap(golden: Golden, i: int) -> Golden:
    return golden["snapshots"][i]


def _expiry(snap: Golden, label: str) -> Golden:
    return next(e for e in snap["expiries"] if e["label"] == label)


def _options(golden: Golden) -> list[Golden]:
    return [o for s in golden["snapshots"] for e in s["expiries"] for o in e["options"]]


def test_snapshots_in_time_order_two_day_then_derived_night(golden: Golden) -> None:
    snaps = golden["snapshots"]
    assert [s["started_kst"][:16] for s in snaps] == [
        "2026-09-28T14:27",
        "2026-09-28T14:52",
        "2026-10-07T21:52",
    ]
    assert [(s["state"], s["trade_date"], s["session"]) for s in snaps] == [
        ("DAY", "2026-09-28", "day"),
        ("DAY", "2026-09-28", "day"),
        ("NIGHT", "2026-10-08", "night"),  # 10-07 밤은 다음 거래일 귀속
    ]
    assert all(s["skipped"] == [] for s in snaps)
    assert [s["derived"] is None for s in snaps] == [True, True, False]
    assert [s["expired"] for s in snaps[:2]] == [[], []]


def test_derived_night_pins_t_plus_1_scopes_and_expired_series(golden: Golden) -> None:
    """야간 파생 입력(14:52 의 시각만 옮김 — 가격·OI 는 그대로라 값의 뜻은 없다): 귀속 T+1,
    0dte 는 달력일이 아닌 귀속 거래일 만기, 15:20 이 지난 만기는 뺀다(§1.4·§2.2), ±1σ Δt 는
    야간 끝 06:00 까지(§3.8)."""
    day, night = _snap(golden, 1), _snap(golden, 2)
    assert night["input"] == day["input"]
    assert night["derived"] == {"shift_minutes": 9 * 1440 + 7 * 60, "note": NIGHT_DERIVED.note}
    assert NIGHT_DERIVED.shift == timedelta(days=9, hours=7)
    assert night["expired"] == [
        {"label": "WKM:260904", "expiry_date": "2026-09-28"},
        {"label": "WKM:261001", "expiry_date": "2026-10-06"},
    ]
    assert [e["label"] for e in night["expiries"]] == ["MONTH:202610", "MONTH:202611"]
    scopes = night["scopes"]
    assert scopes["all"]["expiries"] == ["MONTH:202610", "MONTH:202611"]
    # 10-07(달력일)엔 만기가 없다 — 귀속 거래일 10-08 이 월물 202610 만기일
    assert scopes["0dte"]["expiries"] == scopes["nearest"]["expiries"] == ["MONTH:202610"]
    assert night["top_levels"]["from"] == "MONTH:202610"
    m = _expiry(night, "MONTH:202610")
    now = datetime.fromisoformat(m["now_kst"])
    left = (expiry_at(date(2026, 10, 8)) - now).total_seconds() / 60
    assert m["T_years"] == pytest.approx(left / CALENDAR_MINUTES_PER_YEAR, rel=1e-12)
    assert m["T_kis_years"] == pytest.approx(1 / 365)  # 야간도 now 의 달력 날짜로 센다(§1.7)
    night_end = session_bounds(date(2026, 10, 7), "night")[1]  # 10-08 06:00 KST
    minutes = (night_end - now).total_seconds() / 60
    for move in m["expected_move"].values():
        assert move["minutes"] == pytest.approx(minutes, rel=1e-12)
    # 가격이 14:52 그대로라 합성 F 도 같다 — 확정 베이시스는 14:52 ok F 에서 넘어온다
    assert m["forward"]["F_pt"] == _expiry(day, "MONTH:202610")["forward"]["F_pt"]
    assert m["forward"]["reference"]["kind"] == "near_basis"


def test_forward_quality_reasons_and_futures_cross_check(golden: Golden) -> None:
    first, second = _snap(golden, 0), _snap(golden, 1)
    m1, m2 = _expiry(first, "MONTH:202610"), _expiry(second, "MONTH:202610")
    # 첫 스냅샷은 앞선 ok F 가 없어 교차 확인을 건너뛴다(§1.3 기준가 ③)
    assert (m1["forward"]["quality"], m1["forward"]["notes"]) == ("ok", ["no_futures_ref"])
    assert m1["forward"]["prev_session_skipped"] == ["1097.50"]  # 전 세션 가격 행사가는 F 에서 뺀다
    # 14:27 의 ok F 로 확정한 베이시스(F − S_ref)가 14:52 기준가 = 근월물 + 베이시스
    assert m2["basis_in_pt"] == pytest.approx(m1["forward"]["F_pt"] - first["s_ref"]["price_pt"])
    ref = m2["forward"]["reference"]
    assert ref["kind"] == "near_basis"
    assert ref["price_pt"] == pytest.approx(second["s_ref"]["price_pt"] + ref["basis_pt"])
    assert abs(m2["forward"]["futures_gap_pt"]) <= 2.0 and m2["forward"]["quality"] == "ok"
    for label in ("MONTH:202611", "WKM:261001"):  # F 없음 — 베이시스도 없다
        for s in golden["snapshots"]:
            if s["state"] == "NIGHT" and label == "WKM:261001":
                continue  # 야간 파생에선 만기 지남(빠짐)
            e = _expiry(s, label)
            assert (e["forward"]["F_pt"], e["forward"]["reasons"]) == (None, ["no_parity_strikes"])
            assert e["quality"] == "invalid" and e["basis_in_pt"] is None


def test_option_branches_are_covered(golden: Golden) -> None:
    opts = _options(golden)
    assert {o["source"] for o in opts} == {"board", "fill"}
    assert {o["price_kind"] for o in opts} >= {"mid", "last"}
    assert any(o["prev_session"] and o["quality"] == "estimated" for o in opts)
    assert {o["excluded"] for o in opts} >= {None, "no_forward", "iv_invalid"}
    fallback = [o for o in opts if o["iv"] and o["iv"]["source"] == "kis"]
    assert fallback and all(o["iv"]["quality"] == "estimated" for o in fallback)
    assert any(o["iv"]["rescaled"] and o["gex_won"] is not None for o in fallback)  # §1.5 T 환산
    assert any(o["iv"] and o["iv"]["reason"] == "below_intrinsic/kis_missing" for o in opts)


def test_gex_signs_follow_the_dealer_convention(golden: Golden) -> None:
    for o in _options(golden):  # 딜러 콜 롱 +, 풋 숏 − (tests/test_sign_conventions.py)
        if o["gex_won"] is not None:
            assert (o["gex_won"] >= 0) if o["cp"] == "C" else (o["gex_won"] <= 0)
        assert (o["gamma"] is None) == (o["excluded"] is not None)


def test_scopes_quality_and_flip_crossings(golden: Golden) -> None:
    first, second = _snap(golden, 0), _snap(golden, 1)
    for s in (first, second):
        scopes = s["scopes"]
        assert scopes["all"]["net_gex"]["quality"] == "invalid"  # F 없는 만기가 범위에 있다
        assert scopes["nearest"]["expiries"] == scopes["0dte"]["expiries"] == ["WKM:260904"]
        assert scopes["all"]["flip"]["multi_cross"] is True
    for flip in (first["scopes"]["all"]["flip"], first["scopes"]["0dte"]["flip"]):
        assert len(flip["crossings_pt"]) == 2 and flip["multi_cross"] is True
        nearest = min(flip["crossings_pt"], key=lambda c: (abs(c - flip["f_ref_pt"]), c))
        assert flip["level_pt"] == nearest  # 여럿이면 기준 F 에 가장 가까운 것
    none = second["scopes"]["0dte"]["flip"]  # ±5% 안에 교차 없음
    assert (none["level_pt"], none["crossings_pt"], none["distance_pct"]) == (None, [], None)
    assert none["grid_points"] > 0 and none["profile_sample"][-1]["x_pt"] == pytest.approx(
        none["f_ref_pt"] * 1.05
    )


def test_expected_move_both_bases(golden: Golden) -> None:
    ratio = math.sqrt(CALENDAR_MINUTES_PER_YEAR / TRADING_MINUTES_PER_YEAR)
    seen = 0
    for s in golden["snapshots"]:
        for e in s["expiries"]:
            cal, trade = e["expected_move"]["calendar"], e["expected_move"]["trading"]
            assert cal["minutes"] == trade["minutes"]
            if cal["sigma_pt"] is None:
                assert trade["sigma_pt"] is None and e["atm_iv"]["quality"] == "invalid"
                continue
            seen += 1
            assert trade["sigma_pt"] / cal["sigma_pt"] == pytest.approx(ratio, rel=1e-12)
            assert cal["lower_pt"] == pytest.approx(e["forward"]["F_pt"] - cal["sigma_pt"])
    assert seen == 5  # 주간 둘 × (202610·0DTE 260904) + 야간 파생 202610


# ── 비교기 ───────────────────────────────────────────────────────────────────


def test_compare_uses_unit_tolerances() -> None:
    base = {"a": {"F_pt": 1000.0, "gex_won": 1e11, "T_years": 1e-3, "ok": True, "k": "1095.00"}}
    near = {
        "a": {
            "F_pt": 1000.0 + 1e-7,
            "gex_won": 1e11 + 10.0,
            "T_years": 1e-3,
            "ok": True,
            "k": "1095.00",
        }
    }
    assert compare_golden(base, near) == []  # 상대 1e-10 — 플랫폼 끝자리 차이
    far = json.loads(json.dumps(base))
    far["a"]["F_pt"] = 1000.0 + 1e-5
    diffs = compare_golden(base, far)
    assert len(diffs) == 1 and diffs[0].startswith("$.a.F_pt: 1000.0 → ")
    assert compare_golden({"gex_won": 0.0}, {"gex_won": 5e-4}) == []  # 0 근처는 절대 허용
    assert compare_golden({"gex_won": 0.0}, {"gex_won": 5e-3}) != []


def test_compare_reports_structure_and_type_changes() -> None:
    diffs = compare_golden(
        {"a_pt": 1, "b_pt": [1.0, 2.0], "c": "x", "d_pt": None, "e": True},
        {"a_pt": 1.0, "b_pt": [1.0], "c": "y", "d_pt": 1.0, "f": 1},
    )
    joined = "\n".join(diffs)
    for want in ("$.a_pt: 1 → 1.0", "$.b_pt: 길이 2 → 1", "$.c: 'x' → 'y'", "$.d_pt: None → 1.0"):
        assert want in joined
    assert "$.e: 골든에만 있다" in joined and "$.f: 새 값에만 있다" in joined
    assert compare_golden({"n": [1, "a"]}, {"n": [1, "a"]}) == []  # float 없으면 단위 불필요


def test_float_keys_need_a_unit() -> None:
    assert tolerance("crossings_pt") == tolerance("pt")
    assert tolerance("excluded_oi_ratio") == tolerance("ratio")
    for key in ("value", "x", None):
        with pytest.raises(GoldenSchemaError):
            tolerance(key)
    with pytest.raises(GoldenSchemaError):
        compare_golden({"value": 1.0}, {"value": 1.0})


def test_failure_report_names_the_regeneration_command() -> None:
    text = failure_report([f"$.x[{i}]: 1 → 2" for i in range(40)], REGEN_CORE, limit=3)
    assert "40곳" in text and "$.x[2]" in text and "$.x[3]" not in text and "37곳 더" in text
    assert text.rstrip().endswith(REGEN_CORE)


# ── CLI·입력 ─────────────────────────────────────────────────────────────────


def test_cli_core_checks_with_tolerance_and_writes_only_with_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "core.json"
    assert main(["core", "--out", str(out)]) == 1 and not out.exists()
    assert main(["core", "--out", str(out), "--write"]) == 0
    g = json.loads(out.read_text(encoding="utf-8"))
    # 글자 그대로가 아니라 허용오차로 — 플랫폼(macOS arm64 ↔ Linux x86_64) float 끝자리 차이
    assert compare_golden(json.loads(CORE_GOLDEN.read_text(encoding="utf-8")), g) == []
    level = g["snapshots"][0]["scopes"]["all"]["flip"]["level_pt"]
    g["snapshots"][0]["scopes"]["all"]["flip"]["level_pt"] = level * (1 + 1e-12)
    out.write_text(json.dumps(g), encoding="utf-8")
    assert main(["core", "--out", str(out)]) == 0  # 허용 안 — 파일은 그대로
    g["snapshots"][0]["scopes"]["all"]["flip"]["level_pt"] = level + 0.01
    out.write_text(json.dumps(g), encoding="utf-8")
    capsys.readouterr()
    assert main(["core", "--out", str(out)]) == 1
    printed = capsys.readouterr().out
    assert "$.snapshots[0].scopes.all.flip.level_pt" in printed and REGEN_CORE in printed


def test_cli_unknown_float_unit_is_exit_2_and_nothing_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """단위를 모르는 float 는 추적이 아니라 종료 코드 2 — 비교할 수 없는 골든은 쓰지도 않는다."""
    monkeypatch.setattr(mg, "core_golden", lambda: {"value": 1.0})
    out = tmp_path / "core.json"
    out.write_text(json.dumps({"value": 1.0}), encoding="utf-8")
    assert main(["core", "--out", str(out)]) == 2
    err = capsys.readouterr().err
    assert "골든 스키마 오류" in err and "'value'" in err and "TOLERANCES" in err
    fresh = tmp_path / "fresh.json"
    assert main(["core", "--out", str(fresh), "--write"]) == 2
    assert not fresh.exists()


def test_series_failure_is_isolated(tmp_path: Path) -> None:
    raw = json.loads(CORE_FIXTURES[0].read_text(encoding="utf-8"))
    raw["boards"]["WKI:261008"] = raw["boards"]["WKM:261001"]  # 만기일을 모르는 위클리
    p = tmp_path / "snap.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    g = core_golden([p])
    snap = g["snapshots"][0]
    assert [s["label"] for s in snap["skipped"]] == ["WKI:261008"]
    assert "만기일을 모른다" in snap["skipped"][0]["reason"]
    assert [e["label"] for e in snap["expiries"]] == [
        "MONTH:202610",
        "MONTH:202611",
        "WKM:260904",
        "WKM:261001",
    ]


def test_expiry_output_failure_is_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    real = mg._expiry_golden

    def flaky(series: Any, ev: Any, basis: Any, cal: Any) -> dict[str, Any]:
        if series.label == "WKM:260904":
            raise ZeroDivisionError("boom")
        return real(series, ev, basis, cal)

    monkeypatch.setattr(mg, "_expiry_golden", flaky)
    snap = core_golden(CORE_INPUTS[:1])["snapshots"][0]
    assert snap["skipped"] == [{"label": "WKM:260904", "reason": "ZeroDivisionError: boom"}]
    assert "WKM:260904" not in snap["scopes"]["all"]["expiries"]  # 범위에서도 빠진다
    assert len(snap["expiries"]) == 3


@pytest.mark.parametrize(("minutes", "expired"), [(27, False), (28, True)])
def test_series_past_1520_is_left_out(minutes: int, expired: bool) -> None:
    """0DTE WKM 260904 전광판 14:52:44 → +27분 15:19:44 는 평가, +28분 15:20:44 는 만기 지남."""
    shifted = Derived(CORE_FIXTURES[1], timedelta(minutes=minutes), "시험")
    snap = core_golden([shifted])["snapshots"][0]
    labels = [e["label"] for e in snap["expiries"]]
    assert ("WKM:260904" not in labels) is expired
    gone = [{"label": "WKM:260904", "expiry_date": "2026-09-28"}]
    assert snap["expired"] == (gone if expired else [])
    assert ("WKM:260904" in snap["scopes"]["0dte"]["expiries"]) is not expired
    assert snap["trade_date"] == "2026-09-28" and snap["skipped"] == []


def test_derived_shift_is_whole_minutes() -> None:
    with pytest.raises(ValueError, match="분 단위"):
        Derived(ROOT, timedelta(seconds=90), "x")


def test_unreadable_input_is_an_input_error(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text('{"started_kst": "2026-09-28T14:27:00"}', encoding="utf-8")  # naive·필드 없음
    with pytest.raises(InputError, match=r"bad\.json"):
        core_golden([p])
