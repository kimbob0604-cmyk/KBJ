"""섀도 운영 점검 (scripts/shadow_report.py — docs/phase3_design.md §4, metrics §8) — DB 없이.

- 명세 null: 카탈로그 산출마다 규칙이 있다, 규칙 갈래(빈 범위·분모 0·보간 구간 밖·20일 안 됨·교차
  없음·첫 스냅샷…), invalid 인 null 은 명세대로, 규칙 밖은 명세 밖. engine 이 실제로 쓰는 null 행
  (2026-09-28 합성 작은 스냅샷 — 오늘·0DTE 가 빈 다음 날)은 모두 명세 null 이다
- 예외 health → 플래그: engine 문구 머리(지표 `이름/범위/키`·레벨 `범위/이름`·플로우 곁일·일별)
- 집계·판정: 플래그별 무오류·오류·계산 없음·꺼짐(지금 off — 행이 없는 게 정상), 고른 플래그(행
  플래그·지금 플래그 파일), 행 플래그가 바뀐 기간, 지표에 묶이지 않는 예외(사이클·시리즈·행
  만들기)는 완료 기준을 막는다
- 실행: 종료 코드 0 충족·1 미충족·2 설정·DB 오류, 기간(최근 거래일)·태그 없는 health 창
실제 DB 는 tests/integration/test_engine_store.py(읽기)·tests/integration/test_shadow_report.py.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from core.calendar import TradingCalendar
from core.features import CATALOG, Features
from scripts import shadow_report as sr
from scripts.make_golden import CORE_FIXTURES
from services.bus import BasisBook
from services.engine.daily import DAILY_METRICS
from services.engine.evaluate import evaluate_cycle
from services.engine.service import ENGINE_REGISTRY
from tests.fakes.engine_inputs import snapshot_cycle

KST = ZoneInfo("Asia/Seoul")
CAL = TradingCalendar.default()
D = date(2026, 9, 28)


def group(
    name: str,
    payload: dict[str, Any] | None = None,
    *,
    table: str = "metrics",
    quality: str = "ok",
    flag: str = "shadow",
    count: int = 1,
) -> sr.NullGroup:
    return sr.NullGroup(table, name, flag, "all", quality, sr.project(payload or {}), count)


# ── 명세 null ──


def test_every_catalog_output_has_a_null_rule() -> None:
    """새 산출을 카탈로그에 더하면 그 null 이 명세대로인지도 정한다(규칙이 없으면 invalid 만)."""
    outputs = {(s.table, o) for s in CATALOG for o in s.outputs}
    assert set(sr.NULL_RULES) == outputs


@pytest.mark.parametrize(
    ("table", "name", "payload", "why"),
    [
        ("metrics", "net_gex", {"expiries": []}, "empty_scope"),
        ("metrics", "vex", {"expiries": []}, "empty_scope"),
        ("metrics", "gex_pc", {"expiries": ["M:202610"], "call_gex": 0.0}, "call_gex_zero"),
        ("metrics", "iv_term", {"empty": True, "reasons": []}, "empty_slot"),
        ("metrics", "skew_25d", {"reasons": ["call_out_of_range"]}, "call_out_of_range"),
        ("metrics", "iv_rank", {"reasons": ["short_window", "flat_window"]}, "flat_window"),
        ("metrics", "iv_percentile", {"reasons": ["too_few_days"]}, "too_few_days"),
        ("metrics", "iv_hv", {"reasons": ["missing_returns"]}, "missing_returns"),
        ("metrics", "pcr_oi", {"put": 5, "call": 0}, "denominator_zero"),
        ("metrics", "max_pain", {"tied": 0}, "no_oi"),
        ("metrics", "futures_basis", {"reasons": ["field_missing"]}, "field_missing"),
        ("levels", "call_wall", {"gex_won": None}, "no_positive_gex"),
        ("levels", "flip", {"crossings": [], "multi_cross": False}, "no_crossing"),
        ("levels", "flip_distance", {"flip": None}, "no_flip"),
        ("levels", "expected_move_trading", {"empty": True}, "empty_scope"),
        ("levels", "top_levels", {"empty": True, "levels": []}, "empty_scope"),
        ("oi_changes", "oi_changes", {"first_snapshot": True}, "first_snapshot"),
    ],
)
def test_spec_nulls_have_their_reason(
    table: str, name: str, payload: dict[str, Any], why: str
) -> None:
    g = sr.NullGroup(table, name, "shadow", "all", "ok", sr.project(payload), 1)
    if table == "oi_changes":  # 저장소가 칸을 바로 만든다
        g = sr.NullGroup(table, name, "shadow", "", "ok", payload, 1)
    assert sr.null_reason(g) == why


@pytest.mark.parametrize(
    ("name", "payload", "table"),
    [
        ("net_gex", {"expiries": ["M:202610"]}, "metrics"),  # 범위가 있는데 값이 없다
        ("gex_pc", {"expiries": ["M:202610"], "call_gex": 1.0e9}, "metrics"),
        ("gex_pc", {"expiries": ["M:202610"], "call_gex": False}, "metrics"),  # bool 은 0 이 아니다
        ("hiro", {}, "metrics"),  # §6.1 늘 값이 있다
        ("atm_iv", {"reasons": ["one_side"]}, "metrics"),  # §3.7 null 은 invalid 뿐
        ("pcr_volume", {"put": 3, "call": 7}, "metrics"),
        ("flip", {"crossings": [1095.0]}, "levels"),
        ("mystery", {}, "metrics"),  # 카탈로그 밖 이름
    ],
)
def test_other_nulls_are_off_spec_unless_invalid(
    name: str, payload: dict[str, Any], table: str
) -> None:
    assert sr.null_reason(group(name, payload, table=table)) is None
    assert sr.null_reason(group(name, payload, table=table, quality="invalid")) == "invalid"


def test_first_snapshot_false_is_off_spec() -> None:
    g = sr.NullGroup("oi_changes", "oi_changes", "shadow", "", "ok", {"first_snapshot": False}, 1)
    assert sr.null_reason(g) is None


def test_project_keeps_only_the_null_keys_like_the_store() -> None:
    p = sr.project({"expiries": [], "put_iv": 0.3, "call_gex": 0.0})
    assert set(p) == set(sr.NULL_KEYS) and p["expiries"] == [] and p["call_gex"] == 0.0
    assert p["crossings"] is None and "put_iv" not in p


@pytest.mark.parametrize("shift", [None, timedelta(days=1)], ids=["measured", "next_day"])
def test_every_null_row_the_engine_writes_for_the_measured_snapshot_is_a_spec_null(
    shift: timedelta | None,
) -> None:
    """2026-09-28 14:27 합성 작은 스냅샷(그대로 — F 없는 시리즈, 25Δ 풋 구간 밖)과 하루 뒤로 옮긴
    것(09-29 화 — WKM 260904 만기가 지나 0DTE 범위가 비고 nearest 는 F 없는 시리즈)을 engine 이
    평가한 값 없는 행이 모두 명세 null 이다 — 저장소가 돌려주는 모양(`project`)으로 판정."""
    c = snapshot_cycle(CORE_FIXTURES[0], shift=shift)
    r = evaluate_cycle(c.inp, BasisBook(), cal=CAL, registry=ENGINE_REGISTRY)
    groups = [
        sr.NullGroup("levels", x.name, x.flag, x.scope, x.quality, sr.project(x.detail), 1)
        for x in r.levels
        if x.value is None
    ] + [
        sr.NullGroup("metrics", m.metric, m.flag, m.scope, m.quality, sr.project(m.payload), 1)
        for m in r.metrics
        if m.value is None
    ]
    assert groups  # 갈래가 실제로 있다
    reasons = {(g.table, g.name, g.scope): sr.null_reason(g) for g in groups}
    assert None not in reasons.values(), reasons
    if shift is not None:
        assert reasons[("metrics", "net_gex", "0dte")] == "empty_scope"
        assert reasons[("levels", "top_levels", "0dte")] == "empty_scope"
        assert reasons[("metrics", "iv_term", "all")] in ("empty_slot", "invalid")
    assert reasons[("metrics", "skew_25d", "series")] in ("put_out_of_range", "invalid")


# ── 예외 health → 플래그 ──


@pytest.mark.parametrize(
    ("kind", "message", "flags"),
    [
        ("engine_metric_failed", "vex/all/: ValueError: x", ("vex",)),
        ("engine_metric_failed", "pcr_oi/series/WKM:261001: KeyError: 'a'", ("pcr",)),
        ("engine_metric_failed", "max_pain: ZeroDivisionError: x", ("max_pain",)),  # 플러그인 통째
        ("engine_metric_failed", "iv_rank/2026-09-28: ValueError: x", ("iv_rank",)),  # 일별 한 지표
        ("engine_metric_failed", "oi_changes: ValueError: x", ("oi_changes",)),
        ("engine_metric_failed", "expiry_gamma: ValueError: x", ("expiry_gamma",)),
        ("engine_level_failed", "all/expected_move_calendar: ValueError: x", ("expected_move",)),
        ("engine_level_failed", "0dte/flip: RuntimeError: x", ("flip",)),
        ("engine_flow_failed", "block_trade: KeyError: x", ("block_trades",)),
        ("engine_flow_failed", "block_trades: StoreError: x", ("block_trades",)),
        ("engine_flow_failed", "ws_events: StoreError: x", ("hiro",)),
        ("engine_flow_failed", "hiro: ValueError: x", ("hiro",)),
        ("engine_flow_failed", "futures: StoreError: x", ("futures",)),
        ("engine_flow_failed", "investor_flow: StoreError: x", ("investor_flow",)),
        ("engine_daily_failed", "딜러 가정 점검 실패(2026-09-28): E: x", ("dealer_check",)),
        ("engine_daily_failed", "일별 지표 실패(2026-09-28): E: x", DAILY_METRICS),
        ("engine_metric_failed", "zzz/all/: ValueError: x", ("?zzz",)),
    ],
)
def test_failure_health_names_its_flag(kind: str, message: str, flags: tuple[str, ...]) -> None:
    assert sr.failure_flags(kind, message) == flags


# ── 집계·판정 ──

Count = tuple[str, str, str, int, int, int, int, int]


def _count(name: str, flag: str = "shadow", *, table: str = "metrics", **kw: int) -> Count:
    v = {"rows": 10, "cycles": 5, "invalid": 0, "errors": 0, "nulls": 0} | kw
    return (table, name, flag, v["rows"], v["cycles"], v["invalid"], v["errors"], v["nulls"])


def _summary(
    counts: Sequence[Count],
    nulls: Sequence[sr.NullGroup] = (),
    failures: Sequence[tuple[str, str, int]] = (),
    selection: sr.Selection = "shadow",
    features: Features | None = None,
) -> sr.ShadowReport:
    return sr.summarize(
        counts,
        nulls,
        failures,
        first=D,
        last=D,
        days=[D],
        selection=selection,
        features=features or Features(),
    )


def _shadow_outputs() -> list[Count]:
    """지금 기본값에서 shadow 인 카탈로그 플래그의 산출 전부 — 한 거래일 무오류."""
    return [_count(o, table=s.table) for s in CATALOG if s.default == "shadow" for o in s.outputs]


def by_flag(r: sr.ShadowReport) -> dict[str, sr.FlagResult]:
    return {f.name: f for f in r.flags}


def test_all_shadow_flags_clean_satisfy_the_criterion() -> None:
    r = _summary(_shadow_outputs())
    assert r.satisfied and {f.verdict for f in r.flags} == {"clean"}
    assert set(by_flag(r)) == {s.name for s in CATALOG if s.default == "shadow"}
    pcr = by_flag(r)["pcr"]
    assert [o.name for o in pcr.outputs] == ["pcr_oi", "pcr_volume"] and pcr.rows == 20


def test_error_rows_off_spec_nulls_and_failure_health_each_fail_a_flag() -> None:
    counts = [c for c in _shadow_outputs() if c[1] not in ("vex", "cex", "gex_pc")]
    counts += [
        _count("vex", errors=1, invalid=1),
        _count("cex", nulls=2),
        _count("gex_pc", nulls=3),
    ]
    nulls = [
        group("cex", {"expiries": ["M:202610"]}, count=2),  # 명세 밖
        group("gex_pc", {"expiries": []}, count=3),  # 빈 범위 — 명세
    ]
    failures = [("engine_metric_failed", "skew_25d: ValueError: boom", 4)]
    r = _summary(counts, nulls, failures)
    f = by_flag(r)
    assert (f["vex"].verdict, f["vex"].errors) == ("errors", 1)
    assert (f["cex"].verdict, f["cex"].off_spec) == ("errors", 2)
    assert (f["gex_pc"].verdict, f["gex_pc"].outputs[0].spec_reasons) == (
        "clean",
        {"empty_scope": 3},
    )
    assert (f["skew_25d"].verdict, f["skew_25d"].health) == ("errors", 4)
    assert f["skew_25d"].health_samples == ["skew_25d: ValueError: boom"]
    assert not r.satisfied


def test_a_shadow_flag_without_rows_is_not_computed_and_blocks_the_criterion() -> None:
    r = _summary([c for c in _shadow_outputs() if c[1] != "hiro"])
    assert by_flag(r)["hiro"].verdict == "no_rows" and not r.satisfied


@pytest.mark.parametrize(
    "kind", ["engine_cycle_failed", "engine_series_failed", "engine_rows_failed"]
)
def test_failures_outside_any_metric_block_the_criterion(kind: str) -> None:
    r = _summary(_shadow_outputs(), failures=[(kind, "사이클 실패(…): StoreError: x", 1)])
    assert all(f.verdict == "clean" for f in r.flags)
    assert r.cycle_failures[kind] == 1 and not r.satisfied


def test_failures_without_a_catalog_flag_block_the_criterion() -> None:
    """예외 health 의 대상을 카탈로그 플래그로 못 가리면(engine 새 문구·이름 바뀜) 버리지 않고
    '대상 모름'으로 세어 완료 기준을 막는다(검토 F5: 어느 플래그에도 사이클 예외에도 들지 않아
    충족이 났다). 카탈로그 플래그인데 고르지 않은 것(visible 레벨 등)은 여전히 이 점검 밖."""
    failures = [
        ("engine_metric_failed", "new_thing: KeyError: x", 3),
        ("engine_level_failed", "all/flip: ValueError: y", 2),  # visible — shadow 점검 밖
    ]
    r = _summary(_shadow_outputs(), failures=failures)
    assert all(f.verdict == "clean" for f in r.flags) and not any(r.cycle_failures.values())
    assert r.unattributed == {"?new_thing": 3} and not r.satisfied
    text = sr.render(r)
    head = "대상 플래그를 못 가린 예외 health(10분 묶음)"
    assert f"{head}: ?new_thing 3 — new_thing: KeyError: x" in text
    assert text.endswith(": 미충족")
    clean = _summary(_shadow_outputs(), failures=failures[1:])
    assert clean.unattributed == {} and clean.satisfied
    assert f"{head}: 0" in sr.render(clean)
    # 카탈로그 밖 산출 행이 있으면(행 플래그 그대로 고른다) 그 `?이름` 플래그의 예외로 든다
    odd = _summary([*_shadow_outputs(), _count("new_thing")], failures=failures[:1])
    assert by_flag(odd)["?new_thing"].health == 3 and odd.unattributed == {}


def test_selection_follows_the_row_flag_and_the_flag_file() -> None:
    """vex 를 기간 중에 shadow → visible 로 올렸다: shadow 선택엔 shadow 행만, visible 선택엔
    visible 행과 지금 visible 인 플래그(Phase 2 핵심 + vex)."""
    features = Features(metrics={"vex": "visible"})
    counts = [
        _count("vex", "shadow", rows=4),
        _count("vex", "visible", rows=6, nulls=1),
        _count("net_gex", "visible"),
    ]
    nulls = [group("vex", {"expiries": ["M:202610"]}, flag="visible")]
    shadow = by_flag(_summary(counts, nulls, features=features))
    assert shadow["vex"].rows == 4 and shadow["vex"].verdict == "clean"
    assert "net_gex" not in shadow
    visible = by_flag(_summary(counts, nulls, selection="visible", features=features))
    assert visible["vex"].rows == 6 and visible["vex"].off_spec == 1
    assert visible["net_gex"].verdict == "clean" and visible["flip"].verdict == "no_rows"
    everything = by_flag(_summary(counts, nulls, selection="all", features=features))
    assert [o.row_flag for o in everything["vex"].outputs] == ["shadow", "visible"]
    assert set(everything) == {s.name for s in CATALOG}


def _core_outputs() -> list[Count]:
    return [_count(o, "visible", table=s.table) for s in CATALOG if s.core for o in s.outputs]


def test_off_flags_are_expected_to_have_no_rows() -> None:
    """off 는 계산하지 않는다 — 지금 off 인 플래그에 행이 없으면 '꺼짐'(충족을 막지 않는다). off
    로 계산된 행(있어선 안 된다)·예외 health 는 오류(검토 F4: `--flag off` 는 늘 미충족이었다)."""
    features = Features(metrics={"vex": "off", "hiro": "off"})
    r = _summary([], selection="off", features=features)
    assert {f.name: f.verdict for f in r.flags} == {"vex": "off", "hiro": "off"} and r.satisfied
    assert "| hiro | 꺼짐 | 0 |" in sr.render(r) and sr.render(r).endswith(": 충족")
    r = _summary([_count("vex", "off", rows=2)], selection="off", features=features)
    assert by_flag(r)["vex"].verdict == "errors" and not r.satisfied
    failures = [("engine_flow_failed", "hiro: ValueError: x", 1)]
    r = _summary([], failures=failures, selection="off", features=features)
    assert by_flag(r)["hiro"].verdict == "errors" and not r.satisfied
    # 전부 볼 때도 — hiro 는 기간 내내 off(행 없음), vex 는 기간 중에 껐다(끄기 전 shadow 행으로)
    counts = [c for c in _shadow_outputs() if c[1] != "hiro"] + _core_outputs()
    r = _summary(counts, selection="all", features=features)
    f = by_flag(r)
    assert (f["hiro"].verdict, f["vex"].verdict, f["net_gex"].verdict) == ("off", "clean", "clean")
    assert r.satisfied
    # off 가 아닌데 행이 없으면 여전히 계산 없음
    r = _summary([c for c in counts if c[1] != "cex"], selection="all", features=features)
    assert by_flag(r)["cex"].verdict == "no_rows" and not r.satisfied


def test_main_with_flag_off_exits_zero_when_off_flags_stayed_off(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    flags = _flags_file(tmp_path, "metrics:\n  vex: off\n")
    argv = ["--end", "2026-09-28", "--days", "1", "--features", flags, "--flag", "off"]
    assert sr.main(argv, store=FakeStore(), cal=CAL) == 0
    out = capsys.readouterr().out
    assert "| vex | 꺼짐 |" in out and out.rstrip().endswith(": 충족")


def test_null_groups_without_counted_rows_still_count() -> None:
    """두 읽기 사이에 새로 쓴 행(null 묶음에만 있다)도 명세 밖이면 센다."""
    r = _summary(_shadow_outputs(), [group("hiro", {}, flag="shadow")])
    assert by_flag(r)["hiro"].off_spec == 1 and not r.satisfied


def test_render_shows_rates_reasons_and_the_verdict() -> None:
    counts = [c for c in _shadow_outputs() if c[1] != "gex_pc"]
    counts.append(_count("gex_pc", rows=8, invalid=2, nulls=4))
    nulls = [group("gex_pc", {"expiries": []}, count=3), group("gex_pc", {"expiries": ["x"]})]
    text = sr.render(_summary(counts, nulls))
    assert text.startswith("# 섀도 운영 점검 — 귀속 거래일 2026-09-28 ~ 2026-09-28 (1거래일")
    assert "| gex_pc | gex_pc | metrics | shadow | 8 | 5 | 25.0% | 0 | 50.0% | 12.5% (1) |" in text
    assert "empty_scope 3" in text and "| gex_pc | 오류 |" in text
    assert "engine_cycle_failed 0 · engine_series_failed 0 · engine_rows_failed 0" in text
    assert text.endswith("'1주 섀도 운영 무오류(예외 0, 명세 밖 null 0)': 미충족")
    assert sr.render(_summary(_shadow_outputs())).endswith(": 충족")


# ── 실행 ──


@dataclass
class FakeStore:
    counts: list[Count] = field(default_factory=_shadow_outputs)
    nulls: list[tuple[str, str, str, str, str, dict[str, Any], int]] = field(
        default_factory=list[tuple[str, str, str, str, str, dict[str, Any], int]]
    )
    failures: list[tuple[str, str, int]] = field(default_factory=list[tuple[str, str, int]])
    fail: bool = False
    calls: list[tuple[Any, ...]] = field(default_factory=list[tuple[Any, ...]])

    def engine_output_counts(self, first: date, last: date) -> list[Count]:
        if self.fail:
            raise RuntimeError("connection refused\npassword=secret")
        self.calls.append(("counts", first, last))
        return self.counts

    def engine_null_groups(
        self, first: date, last: date, keys: Sequence[str]
    ) -> list[tuple[str, str, str, str, str, dict[str, Any], int]]:
        self.calls.append(("nulls", first, last, tuple(keys)))
        return self.nulls

    def engine_failures(
        self, first: date, last: date, start: datetime, end: datetime, kinds: Sequence[str]
    ) -> list[tuple[str, str, int]]:
        self.calls.append(("failures", first, last, start, end, tuple(kinds)))
        return self.failures


def _flags_file(tmp_path: Path, text: str = "metrics: {}\n") -> str:
    p = tmp_path / "features.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_main_reads_the_last_trading_days_and_exits_zero_when_clean(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = FakeStore()
    now = datetime(2026, 10, 4, 12, 0, tzinfo=KST)  # 일요일 — 끝은 직전 거래일
    rc = sr.main(["--features", _flags_file(tmp_path)], store=store, cal=CAL, now=now)
    assert rc == 0
    days = sr.trading_days(date(2026, 10, 4), 5, CAL)
    assert len(days) == 5 and days[-1] == date(2026, 10, 2) and all(map(CAL.is_trading_day, days))
    first, last = days[0], days[-1]
    start, end = sr.untagged_window(first, last, CAL)
    assert store.calls == [
        ("counts", first, last),
        ("nulls", first, last, sr.NULL_KEYS),
        ("failures", first, last, start, end, sr.FAILURE_KINDS),
    ]
    assert start == datetime.combine(CAL.prev_trading_day(first), sr.PRE_NIGHT_START, tzinfo=KST)
    assert end == datetime(2026, 10, 2, 17, 50, tzinfo=KST)
    out = capsys.readouterr().out
    assert f"귀속 거래일 {first} ~ {last} (5거래일, 플래그 shadow)" in out and ": 충족" in out


def test_main_exits_one_when_a_flag_has_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = FakeStore(failures=[("engine_flow_failed", "hiro: ValueError: x", 1)])
    argv = ["--end", "2026-09-28", "--days", "1", "--features", _flags_file(tmp_path)]
    assert sr.main(argv, store=store, cal=CAL) == 1
    assert store.calls[0] == ("counts", D, D)
    assert "| hiro | 오류 |" in capsys.readouterr().out


def test_main_exits_two_on_a_bad_flag_file_or_a_store_error_without_leaking_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = _flags_file(tmp_path, "metrics:\n  vex: loud\n")
    assert sr.main(["--features", bad], store=FakeStore(), cal=CAL) == 2
    assert "FeatureError" in capsys.readouterr().err
    good = _flags_file(tmp_path)
    assert sr.main(["--features", good], store=FakeStore(fail=True), cal=CAL) == 2
    err = capsys.readouterr().err
    assert "RuntimeError: connection refused" in err and "secret" not in err


def test_days_must_be_positive(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        sr.parse_args(["--days", "0"])
    assert "--days" in capsys.readouterr().err
