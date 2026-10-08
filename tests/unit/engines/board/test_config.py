"""보드 설정(kbj.engines.board.config) — 새 시험: 모르는 키 거부·숫자는 한 곳·사전 읽기."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from kbj.config.files import ConfigFileError
from kbj.engines.board.config import (
    SECTIONS,
    BoardConfig,
    load_sector_map,
    load_taxonomy,
    load_themes,
)

REPO = Path(__file__).resolve().parents[4]


def _raw() -> dict:
    data = yaml.safe_load((REPO / "config" / "board.yaml").read_text(encoding="utf-8"))
    data.pop("meta")
    return data


def test_board_yaml_을_읽는다() -> None:
    cfg = BoardConfig.load()
    assert cfg["newhigh"]["default_basis"] == "close"
    assert cfg["newhigh"]["priority"] == ["hist", "w52", "d120"]
    # 신고가 3축(ADR 0017): 120일 = 시장 거래일 120, 52주 = 달력 364일
    assert cfg["newhigh"]["lookback_trading_days"] == {"d120": 120}
    assert cfg["newhigh"]["lookback_calendar_days"] == {"w52": 364}
    assert cfg["newhigh"]["min_display_kind"] == "d120"
    assert cfg.series_days == 420
    assert set(cfg) <= set(SECTIONS)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.__setitem__("newhighs", {}),  # 절 철자
        lambda d: d["newhigh"].__setitem__("lookbak", {}),  # 키 철자
        lambda d: d["proximity"].pop("max_gap_pct"),  # 빠진 키
        lambda d: d.pop("display"),  # 빠진 절
        lambda d: d["newhigh"].__setitem__("default_basis", "open"),
        lambda d: d["newhigh"].__setitem__("priority", ["w52", "d120"]),
        # 신고가 창(ADR 0017)
        lambda d: d["newhigh"].__setitem__("lookback", {"d60": 60}),  # 옛 키는 KR 설정에 없다
        lambda d: d["newhigh"]["lookback_calendar_days"].__setitem__(
            "d120", 168
        ),  # 두 창에 한 라벨
        lambda d: d["newhigh"]["lookback_trading_days"].__setitem__(
            "hist", 9999
        ),  # hist 는 창이 아니다
        lambda d: d["newhigh"]["lookback_trading_days"].pop("d120"),  # 창이 없는 라벨
        lambda d: d["newhigh"]["lookback_trading_days"].__setitem__("d120", 0),
        lambda d: d["newhigh"]["lookback_calendar_days"].__setitem__("w52", "364"),
        lambda d: d["newhigh"].__setitem__("min_display_kind", "d60"),
    ],
)
def test_모르는_키와_빠진_키는_거부한다(mutate) -> None:
    d = _raw()
    mutate(d)
    with pytest.raises(ValueError):
        BoardConfig.from_mapping(d)


def test_파일에서_읽을_때는_파일_이름을_싣는다(tmp_path: Path) -> None:
    d = _raw()
    d["detect"]["oops"] = 1
    p = tmp_path / "board.yaml"
    p.write_text(yaml.safe_dump(d, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ConfigFileError, match=r"board\.yaml"):
        BoardConfig.load(p)


def test_원본을_바꿔도_설정은_그대로다() -> None:
    d = _raw()
    cfg = BoardConfig.from_mapping(d)
    d["newhigh"]["lookback_trading_days"]["d120"] = 1
    assert cfg["newhigh"]["lookback_trading_days"]["d120"] == 120
    assert cfg.to_dict() == BoardConfig.from_mapping(copy.deepcopy(_raw())).to_dict()


def test_숫자는_한_곳이다_legacy_settings_에는_엔진_절이_없다() -> None:
    legacy = yaml.safe_load(
        (REPO / "legacy" / "etf_traker" / "board" / "config" / "settings.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert not (set(legacy) & (set(SECTIONS) - {"service"}))


def test_자체_사전을_읽는다() -> None:
    th = load_themes()
    assert th["themes"] and "unlisted" in th
    tax = load_taxonomy()
    assert tax["meta"]["taxonomy"] == "board48"
    smap, name = load_sector_map()
    assert name == "board48"
    assert len(smap) > 2000
    assert all(isinstance(c, str) and len(c) == 6 for c in smap)
    assert all(isinstance(v, str) and v for v in smap.values())
