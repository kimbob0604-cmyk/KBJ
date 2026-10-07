"""config/limits.yaml 해석(kbj.data.limits) — 설계 §4.2·§4.3 값, 형식 검사, 리미터 설정 변환.

레포의 config/limits.yaml 을 본다 — 바깥 `KBJ_*`(운영 VM 의 `KBJ_CONFIG_DIR` 등)와 작업 디렉터리의
`.env` 가 닿지 않게 지운다(tests/test_settings.py 와 같은 방식).
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from kbj.config.settings import Settings
from kbj.data.limits import (
    REQUIRED_SOURCES,
    LimitsError,
    SourceLimits,
    load_limits,
    parse_limits,
)
from kbj.data.ratelimit import TR_DISPLAY_BOARD_CALLPUT, Priority, RateLimitConfig

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in list(os.environ):
        if name.upper().startswith("KBJ_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


def settings(config_dir: Path | None = None) -> Settings:
    kw: dict[str, Any] = {} if config_dir is None else {"config_dir": config_dir}
    return Settings(_env_file=None, **kw)  # pyright: ignore[reportCallIssue]


def repo_yaml() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "config" / "limits.yaml").read_text(encoding="utf-8"))


def test_repo_file_has_the_design_values() -> None:
    lim = load_limits(settings=settings())
    assert set(REQUIRED_SOURCES) <= set(lim.sources)
    kis = lim.source("kis")
    assert kis.rate_config() == RateLimitConfig()  # GX 기본값과 같다(4/s·버킷 1·하한 1·60초)
    assert kis.tr_min_interval_s == {TR_DISPLAY_BOARD_CALLPUT: 1.0}
    assert kis.daily_cap is None  # KIS 일 한도 미공표
    krx = lim.source("krx")
    assert (krx.rate, krx.daily_cap, krx.backfill_cap) == (2.0, 8000, 5000)  # D5 [확인 필요]
    assert (lim.source("dart").rate, lim.source("dart").daily_cap) == (8.0, 18000)
    datago = lim.source("datago")
    assert (datago.rate, datago.daily_cap_per_dataset, datago.daily_cap) == (25.0, 9500, None)
    assert lim.source("kosis").rate == 3.0 and lim.source("kosis").daily_cap is None
    ecos = lim.source("ecos")
    assert (ecos.rate, ecos.hold_s, ecos.close_after_throttles) == (2.0, 600, 3)
    tg = lim.source("telegram")
    assert (tg.rate, tg.per_chat_rate, tg.retry_after_max) == (25.0, 1.0, 4)


def test_kis_priorities_follow_the_design_table() -> None:
    kis = load_limits(settings=settings()).source("kis")
    assert kis.priority("auth_issue") is Priority.P0
    assert kis.priority("gex_board") is Priority.P1
    assert kis.priority("command_reply") is Priority.P2
    assert kis.priority("close_collect") is Priority.P3
    assert kis.priority("legacy_bridge") is Priority.P3
    assert kis.priority("backfill") is Priority.P4
    assert kis.priority("모르는 역할") is Priority.P2  # default_priority


def test_every_source_converts_to_a_valid_rate_config() -> None:
    for name, src in load_limits(settings=settings()).sources.items():
        cfg = src.rate_config()
        assert cfg.capacity == 1, name  # 버킷 1 → 1초 창 ceil(rate) 건 이하
        assert 0 < cfg.floor_rate <= cfg.rate, name
        assert cfg.state_ttl_s > cfg.recovery_s(), name  # 회복 중에 상태가 사라지지 않는다
        assert cfg.recovery_s() <= 15 * 60, name  # 하한에서 기본 속도까지 15분 안


def test_state_ttl_follows_recovery_when_not_given() -> None:
    slow = SourceLimits(rate=40.0, floor_rate=1.0, step_rate=0.5)  # 회복 78단계 — 900초를 넘는다
    cfg = slow.rate_config()
    assert cfg.recovery_s() > 900 and cfg.state_ttl_s == pytest.approx(cfg.recovery_s() + 60)
    fixed = SourceLimits(rate=4.0, state_ttl_s=1200.0)
    assert fixed.rate_config().state_ttl_s == 1200.0
    assert SourceLimits(rate=0.5).rate_config().floor_rate == 0.5  # 하한 기본값 min(1.0, rate)


@pytest.mark.parametrize(
    ("patch", "match"),
    [
        ({"kis": {"rate": 4.0, "rat": 5.0}}, "kis"),  # 모르는 키(철자)
        ({"krx": {"rate": 0}}, "krx"),
        ({"krx": {"rate": 2.0, "daily_cap": 100, "backfill_cap": 200}}, "backfill_cap"),
        ({"dart": {"rate": 2.0, "floor_rate": 3.0}}, "dart"),  # 하한 > 속도
        ({"ecos": {"rate": 2.0, "state_ttl_s": 10}}, "ecos"),  # 회복보다 짧은 상태 수명
        ({"kis": {"rate": 4.0, "priorities": {"x": "P9"}}}, "kis"),
        ({"Bad": {"rate": 1.0}}, "소문자"),
        ({"version": 2}, "version"),
    ],
)
def test_bad_values_fail_loudly(patch: dict[str, Any], match: str) -> None:
    data = {**repo_yaml(), **patch}
    with pytest.raises(LimitsError, match=match):
        parse_limits(data)


def test_missing_source_and_unknown_lookup() -> None:
    data = repo_yaml()
    del data["kosis"]
    with pytest.raises(LimitsError, match="kosis"):
        parse_limits(data)
    with pytest.raises(LimitsError, match="naver"):
        parse_limits(repo_yaml()).source("naver")  # 네이버는 쓰지 않는다(U4)


def test_reads_from_the_configured_folder(tmp_path: Path) -> None:
    data = repo_yaml()
    data["krx"]["daily_cap"] = 1234
    data["krx"]["backfill_cap"] = 1000
    (tmp_path / "limits.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    assert load_limits(settings=settings(tmp_path)).source("krx").daily_cap == 1234
    with pytest.raises(FileNotFoundError):
        load_limits(settings=settings(tmp_path / "없는 폴더"))


def test_rates_keep_documented_headroom() -> None:
    """공표·실측 한도보다 여유가 있다(초당: KIS 5/s·data.go.kr 30 tps·KOSIS 200/분, 일: KRX 10,000·
    DART 20,000·data.go.kr 10,000)."""
    lim = load_limits(settings=settings())
    assert lim.source("kis").rate < 5.0
    assert lim.source("datago").rate < 30.0
    assert lim.source("kosis").rate * 60 < 200
    assert math.isclose(lim.source("krx").daily_cap or 0, 10_000 * 0.8)
    assert (lim.source("dart").daily_cap or 0) < 20_000
    assert (lim.source("datago").daily_cap_per_dataset or 0) < 10_000
