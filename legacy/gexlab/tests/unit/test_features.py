"""기능 플래그 로더 (core/features.py, config/features.yaml — metrics §8, 설계 §4).

- 저장소의 플래그 파일: Phase 2 핵심 visible, Phase 3 확장 지표 shadow, 카탈로그 이름을 다 적었다
- 모르는 구역·이름·값, Phase 2 핵심 off, 중복 키(이름·구역), 깨진 YAML, 읽기 실패 → FeatureError
  (기동 실패)
- 따옴표 없는 `off` 는 문자열 off(YAML 1.1 false 가 아니다), 적지 않은 이름은 카탈로그 기본값
- 감시자: 30초마다 파일 서명(mtime·크기)을 보고 바뀌면 다시 읽는다 — 실패하면 직전 값 + 오류(같은
  서명은 한 번), 고치면 다시 쓴다
- 카탈로그 = engine 이 쓰는 플래그 이름(등록부·플로우·선물·일별)과 산출 이름
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.features import (
    CATALOG,
    CORE_NAMES,
    FEATURES_PATH,
    FeatureError,
    Features,
    FeatureWatcher,
    changed_flags,
    default_flag,
    flag_for_output,
    load_features,
    parse_text,
)
from services.engine.daily import DAILY_METRICS
from services.engine.flow import (
    BLOCK_STATUS,
    BLOCK_TRADE,
    DEALER_CHECK,
    HIRO,
    INVESTOR_FLOW,
    OI_FLAG,
)
from services.engine.futures import FUTURES_FLAG, FUTURES_METRICS
from services.engine.service import ENGINE_REGISTRY

PHASE2 = {
    "net_gex",
    "dex",
    "atm_iv",
    "expiry_gamma",
    "call_wall",
    "put_wall",
    "abs_gamma",
    "flip",
    "flip_distance",
    "expected_move",
    "top_levels",
}


# ── 저장소의 플래그 파일 ──


def test_the_repository_file_keeps_phase2_visible_and_phase3_shadow() -> None:
    f = load_features(FEATURES_PATH)
    got = f.resolved()
    assert set(got) == {s.name for s in CATALOG}
    assert {n for n, v in got.items() if v == "visible"} == PHASE2 == CORE_NAMES
    assert {n for n, v in got.items() if v != "visible"} == {s.name for s in CATALOG if not s.core}
    assert set(got.values()) == {"visible", "shadow"}
    # 기본값에 기대지 않고 이름을 다 적어 둔다 — 파일만 봐도 무엇이 켜졌는지 안다
    assert set(f.metrics) == {s.name for s in CATALOG}
    assert (f.widgets, f.alerts, f.strategies) == ({}, {}, {})


def test_catalog_defaults_are_visible_for_the_core_and_shadow_for_new_metrics() -> None:
    assert parse_text("").resolved() == {s.name: s.default for s in CATALOG}
    assert all(s.default == "visible" for s in CATALOG if s.core)
    assert all(s.default == "shadow" for s in CATALOG if not s.core)
    assert default_flag("net_gex") == "visible" and default_flag("vex") == "shadow"
    assert default_flag("not_in_catalog") == "shadow"  # 카탈로그 밖(시험 플러그인 등)은 새 지표


# ── 검증 ──


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("metrics:\n  vexx: shadow\n", "metrics.vexx: 모르는 이름"),
        ("metrics:\n  vex: hidden\n", "metrics.vex"),
        ("metric:\n  vex: shadow\n", "metric"),  # 모르는 구역
        ("metrics:\n  flip: off\n", "Phase 2 핵심은 off 불가"),
        ("widgets:\n  regime_card: visible\n", "widgets.regime_card: 모르는 이름"),
        ("metrics:\n  vex: [shadow]\n", "metrics.vex"),
        ("- vex\n", "(파일)"),
    ],
)
def test_unknown_names_values_and_core_off_fail(text: str, needle: str) -> None:
    with pytest.raises(FeatureError) as e:
        parse_text(text)
    assert needle in str(e.value)


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("metrics:\n  vex: visible\n  vex: off\n", "'vex' (3행)"),  # 같은 이름 두 번
        ("metrics:\n  vex: shadow\nmetrics:\n  net_gex: shadow\n", "'metrics' (3행)"),  # 구역
        ("metrics: {vex: shadow, vex: visible}\n", "'vex'"),  # 흐름 표기
        ("metrics:\n  vex: shadow\n  'vex': visible\n", "'vex' (3행)"),  # 따옴표만 다른 같은 키
    ],
)
def test_duplicate_keys_fail(text: str, needle: str) -> None:
    """뒤 값이 앞 값을 조용히 덮지 않는다 — 같은 이름·구역이 두 번이면 모호한 파일이라 기동
    실패(검토 F3: 앞 구역이 통째로 빠져 기본값으로 돌아갔다)."""
    with pytest.raises(FeatureError, match="같은 키") as e:
        parse_text(text)
    assert needle in str(e.value)
    # 다른 구역·다른 매핑에 같은 이름은 중복이 아니다
    assert parse_text("metrics:\n  vex: off\nwidgets:\n").flag("vex") == "off"


def test_broken_yaml_and_unreadable_files_fail(tmp_path: Path) -> None:
    with pytest.raises(FeatureError, match="YAML"):
        parse_text("metrics: {vex: shadow\n")
    with pytest.raises(FeatureError, match="읽지 못했다"):
        load_features(tmp_path / "missing.yaml")


def test_unquoted_off_is_the_string_off_and_blank_sections_are_empty() -> None:
    f = parse_text("metrics:\n  vex: off\n  hiro: visible\n  flip: shadow\nwidgets:\n")
    assert (f.flag("vex"), f.flag("hiro"), f.flag("flip")) == ("off", "visible", "shadow")
    assert f.flag("cex") == "shadow" and f.flag("net_gex") == "visible"  # 적지 않은 이름
    assert f.widgets == {}


def test_changed_flags_compare_with_defaults_filled() -> None:
    a = parse_text("metrics:\n  vex: shadow\n")
    b = parse_text("metrics:\n  vex: visible\n  flip: shadow\n  cex: shadow\n")
    assert changed_flags(a, b) == {"vex": ("shadow", "visible"), "flip": ("visible", "shadow")}
    assert changed_flags(a, a) == {}


def test_features_are_frozen() -> None:
    f = Features()
    with pytest.raises(ValueError):
        f.metrics = {}  # type: ignore[misc]


# ── 감시자 ──


class Mono:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def _write(path: Path, text: str, mtime_ns: int) -> None:
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(mtime_ns, mtime_ns))


def test_the_watcher_rereads_on_a_new_signature_every_thirty_seconds(tmp_path: Path) -> None:
    p = tmp_path / "features.yaml"
    _write(p, "metrics:\n  vex: shadow\n", 1_000_000_000)
    mono = Mono()
    w = FeatureWatcher(p, mono=mono)
    assert w.features.flag("vex") == "shadow"
    _write(p, "metrics:\n  vex: visible\n", 2_000_000_000)
    mono.t += 29.0
    assert w.poll() is None  # 30초 전에는 보지 않는다
    mono.t += 1.0
    r = w.poll()
    assert r is not None and r.error is None and dict(r.changes) == {"vex": ("shadow", "visible")}
    assert w.features.flag("vex") == "visible"
    mono.t += 30.0
    assert w.poll() is None  # 서명이 그대로
    # 같은 mtime 이어도 크기가 바뀌면 다시 읽는다
    _write(p, "metrics:\n  vex: visible\n  cex: off\n", 2_000_000_000)
    mono.t += 30.0
    r = w.poll()
    assert r is not None and dict(r.changes) == {"cex": ("shadow", "off")}


def test_a_bad_reload_keeps_the_previous_flags_and_reports_once(tmp_path: Path) -> None:
    p = tmp_path / "features.yaml"
    _write(p, "metrics:\n  vex: visible\n", 1_000_000_000)
    mono = Mono()
    w = FeatureWatcher(p, mono=mono, every_s=5.0)
    _write(p, "metrics:\n  vexx: visible\n", 2_000_000_000)
    mono.t += 5.0
    r = w.poll()
    assert r is not None and r.error is not None and "vexx" in r.error
    assert r.features.flag("vex") == "visible" and w.features.flag("vex") == "visible"
    mono.t += 5.0
    assert w.poll() is None  # 같은 틀린 파일은 한 번만 알린다
    p.unlink()
    mono.t += 5.0
    r = w.poll()
    assert r is not None and r.error is not None and "읽지 못했다" in r.error
    mono.t += 5.0
    assert w.poll() is None
    _write(p, "metrics:\n  vex: shadow\n", 3_000_000_000)  # 고치면 다시 쓴다
    mono.t += 5.0
    r = w.poll()
    assert r is not None and r.error is None and w.features.flag("vex") == "shadow"


def test_the_watcher_fails_at_startup_on_a_bad_or_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FeatureError):
        FeatureWatcher(tmp_path / "missing.yaml", mono=Mono())
    p = tmp_path / "features.yaml"
    _write(p, "metrics:\n  net_gex: off\n", 1)
    with pytest.raises(FeatureError, match="net_gex"):
        FeatureWatcher(p, mono=Mono())
    with pytest.raises(ValueError):
        FeatureWatcher(FEATURES_PATH, mono=Mono(), every_s=0)


# ── 카탈로그 = engine 이 쓰는 이름 ──


def test_the_catalog_names_every_engine_flag_and_output() -> None:
    names = {s.name for s in CATALOG}
    for p in ENGINE_REGISTRY:  # 사이클 등록부 — 산출 이름 → 플래그
        assert p.flag in names, p
        assert flag_for_output(p.name) == p.flag, p
    for flag, outputs in (
        (HIRO, (HIRO,)),
        (BLOCK_STATUS, (BLOCK_STATUS, BLOCK_TRADE)),
        (INVESTOR_FLOW, (INVESTOR_FLOW,)),
        (DEALER_CHECK, (DEALER_CHECK,)),
        (FUTURES_FLAG, FUTURES_METRICS),
        *((m, (m,)) for m in DAILY_METRICS),
    ):
        assert all(flag_for_output(o) == flag for o in outputs), flag
    assert flag_for_output(OI_FLAG, "oi_changes") == OI_FLAG
    for name in ("net_gex", "dex", "atm_iv", "expiry_gamma"):
        assert flag_for_output(name) == name
    levels = ("call_wall", "put_wall", "abs_gamma", "flip", "flip_distance", "top_levels")
    assert all(flag_for_output(n, "levels") == n for n in levels)
    for n in ("expected_move_calendar", "expected_move_trading"):
        assert flag_for_output(n, "levels") == "expected_move"
    assert flag_for_output("nope") is None
