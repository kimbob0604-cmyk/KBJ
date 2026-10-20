"""`config/gex.yaml`·`config/diag.yaml` 해석기(P7 — docs/p7_design.md §3.6, metrics §D0~§D9·§G8).

모양(모르는 키 오류·값 범위)과 다른 설정 파일과 같아야 하는 값(알림 쿨다운·등록부 시각·GX
플래그)을 본다.
값 자체의 근거 시험은 ENG(gex)·DIAG(diag)가 더한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from kbj.config.diag import DiagConfig, load_diag, parse_diag
from kbj.config.files import ConfigFileError
from kbj.config.gex import load_gex, parse_gex
from kbj.core.p7_rows import fear_greed_band

ROOT = Path(__file__).resolve().parents[3]


def _yaml(name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))


def test_gex_config_loads_and_keeps_gx_flags() -> None:
    g = load_gex()
    # Phase 2 핵심은 visible. 확장 지표도 2026-10-20 사용자 요청으로 visible(섀도 운영 끝 — §10 Q3)
    core = ("net_gex", "dex", "atm_iv", "call_wall", "put_wall", "flip", "flip_distance",
            "expected_move", "top_levels", "expiry_gamma", "abs_gamma")  # fmt: skip
    assert all(g.features.flag(n) == "visible" for n in core)
    for n in ("vex", "cex", "skew_25d", "iv_term", "iv_rank", "hiro", "oi_changes", "futures"):
        assert g.features.flag(n) == "visible", n
    assert g.features.flag("nope") is None
    # GX 엔진 상수 그대로
    assert (g.engine.catchup_s, g.engine.grace_s, g.engine.daily_retry_s) == (10, 35, 60)
    # 알림: 정규장만·쿨다운은 notify.yaml 과 같다(§10 Q5)
    assert (g.alert.window.start, g.alert.window.end) == ("09:00", "15:30")
    notify = _yaml("notify.yaml")
    policies = notify["kinds"]
    assert g.alert.cooldown_s == policies["alert.gex_level"]["cooldown_s"] == 600
    assert set(g.alert.conditions) == {"flip_cross", "wall_move", "outside_1sigma"}


def test_gex_off_is_read_as_text_and_unknown_keys_fail() -> None:
    raw = (ROOT / "config" / "gex.yaml").read_text(encoding="utf-8")
    data = yaml.load(raw.replace("vex: visible", "vex: off"), Loader=yaml.BaseLoader)  # noqa: S506
    data["version"] = int(data["version"])
    assert parse_gex(data).features.flag("vex") == "off"  # YAML 1.1 false 로 읽지 않는다
    with pytest.raises(ConfigFileError, match=r"gex\.yaml"):
        parse_gex({**data, "extra": "1"})
    bad = yaml.load(raw.replace("flip: visible", "flip: shown"), Loader=yaml.BaseLoader)  # noqa: S506
    bad["version"] = int(bad["version"])
    with pytest.raises(ConfigFileError):
        parse_gex(bad)


def test_diag_config_loads_and_follows_metrics_appendix() -> None:
    d = load_diag()
    assert (d.window, d.min_history, d.bands) == (250, 60, (20, 40, 60, 80))
    assert d.fear_greed.components == ("vol", "breadth", "credit_balance", "foreign",
                                       "credit_spread")  # fmt: skip
    assert d.fear_greed.min_components == 4 and d.fear_greed.weights == "equal"
    # 공포탐욕 구간 경계 = 행 자료형의 구간 함수(정본 하나)
    lo = (0, *d.fear_greed.bands)
    names = [fear_greed_band(__import__("decimal").Decimal(x)) for x in lo]
    assert names == ["extreme_fear", "fear", "neutral", "greed", "extreme_greed"]
    assert (d.gauge.risk_on_at, d.gauge.risk_off_at, d.gauge.min_components) == (2, -2, 3)
    assert (d.verdict.risk_on_at, d.verdict.risk_off_at, d.verdict.min_components) == (2, -2, 4)
    assert set(d.keys("public")) == {
        "credit_spread", "credit_balance", "deposits", "export_momentum", "rate_direction",
    }  # fmt: skip
    assert d.events.earnings_cluster_min == 10


def test_diag_config_rejects_duplicates_and_semis_lists() -> None:
    base = _yaml("diag.yaml")
    with pytest.raises(ConfigFileError):
        parse_diag({**base, "semis": ["005930"]})  # 묶음은 markets.yaml ribbon(두 벌 금지)
    with pytest.raises(ConfigFileError):
        parse_diag({**base, "bands": [40, 20, 60, 80]})
    with pytest.raises(ConfigFileError):
        parse_diag({**base, "min_history": 300})
    fg = {**base["fear_greed"], "min_components": 6}
    with pytest.raises(ConfigFileError):
        parse_diag({**base, "fear_greed": fg})
    assert isinstance(parse_diag(base), DiagConfig)
    # 반도체 묶음은 markets.yaml 에만
    assert "semis" in _yaml("markets.yaml")["ribbon"]
