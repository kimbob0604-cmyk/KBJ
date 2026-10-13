"""P6 설정 — config/trade.yaml + config/knowledge/{trade_map,trade_items,trade_regions,
hs_chapters}.yaml(docs/p6_design.md §3.5·§4.5). 모르는 키는 오류, 형식·범위·한 파일 안의 맞음."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from kbj.config.files import REPO_ROOT, ConfigFileError
from kbj.config.trade import (
    load_hs_chapters,
    load_local_map,
    load_trade,
    load_trade_items,
    load_trade_map,
    load_trade_regions,
    parse_hs_chapters,
    parse_trade,
    parse_trade_items,
    parse_trade_map,
    parse_trade_regions,
)

CFG = REPO_ROOT / "config"


def _yaml(name: str) -> dict[str, Any]:
    return yaml.safe_load((CFG / name).read_text(encoding="utf-8"))


def test_real_files_load() -> None:
    t = load_trade()
    assert t.backfill.years == 5 and t.overlay.min_quarters == 8 and t.overlay.lags == (0, 1, 2)
    assert t.public_export.months == 24 and t.public_export.dir_cap_mb == 20
    assert t.items104.mode == "fixed" and t.items104.n == 104  # 사용자 결정 2026-10-13
    assert t.surge.min_usd == 10_000_000 and t.surge.surge_yoy == 0.5
    assert t.map.local_file is None  # VM 에서 사용자가 준다
    load_trade_map()
    load_trade_items()
    load_trade_regions()
    assert set(load_hs_chapters().chapters) >= {"85"}


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("surge", "unknown_key"), 1),
        (("tenday", "poll", "start"), "9:00"),
        (("tenday", "poll", "month_days"), [1, 1]),
        (("overlay", "lags"), [0, 9]),
        (("items104", "mode"), "auto"),
        (("surge", "plunge_yoy"), 0.3),
        (("price_volume", "weight_unfit"), ["8"]),
    ],
)
def test_trade_yaml_rejects_bad_values(path: tuple[str, ...], value: object) -> None:
    data = copy.deepcopy(_yaml("trade.yaml"))
    node = data
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value
    with pytest.raises(ConfigFileError):
        parse_trade(data)


STOCK: dict[str, Any] = {
    "code": "000000",
    "name": "예시",
    "reviewed": False,
    "items": [{"hs": "854232", "flow": "exp", "weight": 0.5, "countries": ["CN", "US"]}],
    "regions": [{"region_id": "KR-41-a01", "hs6": "330499", "weight": 0.3}],
    "evidence": [{"kind": "dart_business_report", "rcept_no": "20260314000123", "note": "요약"}],
}


def test_map_schema_accepts_the_design_example() -> None:
    m = parse_trade_map({"version": 1, "stocks": [STOCK]})
    assert m.stocks[0].items[0].countries == ("CN", "US")


@pytest.mark.parametrize(
    "change",
    [
        {"code": "12345"},
        {"items": [{"hs": "854", "flow": "exp", "weight": 0.5}]},  # 홀수 자리
        {"items": [{"hs": "8542", "flow": "exp", "weight": 0.8}]},  # 합 0.8 + 0.3 > 1
        {"items": [{"hs": "8542", "flow": "exp", "weight": 1.5}]},
        {"evidence": []},  # 근거 필수
        {"items": [], "regions": []},
        {"regions": [{"region_id": "41-a01", "hs6": "330499", "weight": 0.1}]},
        {"items": [{"hs": "8542", "flow": "exp", "weight": 0.1}] * 2},  # 중복
        {"watch": True},  # 모르는 키
    ],
)
def test_map_schema_rejects(change: dict[str, Any]) -> None:
    with pytest.raises(ConfigFileError):
        parse_trade_map({"version": 1, "stocks": [{**STOCK, **change}]})


def test_map_rejects_duplicate_stock_and_local_file_loads(tmp_path: Path) -> None:
    with pytest.raises(ConfigFileError, match="두 번"):
        parse_trade_map({"version": 1, "stocks": [STOCK, STOCK]})
    p = tmp_path / "local_map.yaml"
    p.write_text(yaml.safe_dump({"version": 1, "stocks": [STOCK]}, allow_unicode=True), "utf-8")
    assert load_local_map(p).stocks[0].code == "000000"
    with pytest.raises(FileNotFoundError):
        load_local_map(tmp_path / "missing.yaml")


def test_items_prefix_overlap_is_a_config_error() -> None:
    ok = {"version": 1, "groups": [{"id": "g1", "name": "a", "hs": ["8542"]},
                                   {"id": "g2", "name": "b", "hs": ["8471"]}]}  # fmt: skip
    assert len(parse_trade_items(ok).groups) == 2
    bad = copy.deepcopy(ok)
    bad["groups"][1]["hs"] = ["854232"]
    with pytest.raises(ConfigFileError, match="겹친다"):
        parse_trade_items(bad)


def test_regions_and_chapters_shape() -> None:
    r = parse_trade_regions(
        {
            "version": 1,
            "regions": [
                {"id": "KR-46-a01", "sido_cd": "46", "sgg_nm": "가상군", "valid_to": "2026-06"},
                {"id": "KR-46-a01", "sido_cd": "12", "sgg_nm": "가상군", "valid_from": "2026-07"},
            ],
        }
    )
    assert r.ids() == frozenset({"KR-46-a01"})
    with pytest.raises(ConfigFileError):
        parse_trade_regions(
            {"version": 1, "regions": [{"id": "KR-46-a", "sido_cd": "46", "sgg_nm": "x",
                                        "valid_from": "2026-07", "valid_to": "2026-06"}]}
        )  # fmt: skip
    with pytest.raises(ConfigFileError):
        parse_hs_chapters({"version": 1, "chapters": {"8": "x"}})


def test_knowledge_templates_hold_no_watchlist_or_vendor_data() -> None:
    """공개 매핑표에 관심종목 흔적이 없다(R9) — FND 틀 단계에서는 본문이 비었다."""
    for name in ("trade_map.yaml", "trade_items.yaml", "trade_regions.yaml", "hs_chapters.yaml"):
        text = (CFG / "knowledge" / name).read_text(encoding="utf-8")
        assert "watchlist:" not in text and "is_watch" not in text, name
