"""공개 매핑표 `config/knowledge/trade_map.yaml` 검증(CI — docs/p6_design.md §4.5, metrics §28,
묶음 MAP).

형식·짝수 자리·가중치 0~1·종목별 합 ≤ 1·중복 0·근거 1개 이상은 해석기(`kbj.config.trade`)가,
HS2 장이 `hs_chapters.yaml` 에 있음·지역 ID 가 `trade_regions.yaml` 에 있음은
`mapping.cross_check` 가 본다.
DB 대조(상장사·`pub_trade.hs_dim`)는 VM 의 `verify_trade`(실측 #56).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from kbj.config.files import ConfigFileError
from kbj.config.trade import (
    load_hs_chapters,
    load_trade_map,
    load_trade_regions,
    parse_trade_map,
)
from kbj.core.time import KST
from kbj.engines.trade.mapping import cross_check, map_rows, map_version, resolve

ROOT = Path(__file__).resolve().parents[3]
FILE = ROOT / "config" / "knowledge" / "trade_map.yaml"


def test_판_2는_300종목_산업·흐름이_있다() -> None:
    tm = load_trade_map()
    # 판 2(사용자 결정 2026-10-21 — 300종목). 검토 전 출시 — 검토하면 reviewed: true
    assert tm.version == 2 and len(tm.stocks) >= 300
    assert all(isinstance(s.reviewed, bool) for s in tm.stocks)
    assert all(s.sector and s.flows for s in tm.stocks)


def test_파일_사이_대조_통과() -> None:
    tm = load_trade_map()
    probs = cross_check(tm, chapters=load_hs_chapters(), regions=load_trade_regions())
    assert [p.line() for p in probs] == []


def test_합성_대역·관심종목_흔적_없음() -> None:
    """공개 매핑표는 관심종목과 무관하게 고른다(D-P6-14). 합성 대역(99xxxx)·origin 칸 없음."""
    raw = yaml.safe_load(FILE.read_text(encoding="utf-8"))
    for s in raw["stocks"]:
        assert isinstance(s["code"], str), f"코드는 따옴표 문자열: {s['code']!r}"
        assert not s["code"].startswith("99")
        assert "origin" not in s and "watch" not in str(s).lower()


def test_근거는_자체_문장_접수번호는_14자리만() -> None:
    tm = load_trade_map()
    for s in tm.stocks:
        assert s.evidence
        for e in s.evidence:
            assert e.note.strip()
            assert e.rcept_no is None or re.fullmatch(r"[0-9]{14}", e.rcept_no)


def test_HS_짝수_자리·가중치_합() -> None:
    tm = load_trade_map()
    for s in tm.stocks:
        flows = s.flow_list()
        total = sum(p.weight for f in flows for p in f.products)
        assert 0 < total <= 1 + 1e-9, s.code
        for f in flows:
            for p in f.products:
                assert len(p.hs) in (2, 4, 6, 8, 10) and p.hs.isdigit()
                assert f.kind == "national" or len(p.hs) == 6


def test_행으로_펴면_종목마다_줄이_있다() -> None:
    from datetime import datetime

    tm = load_trade_map()
    v = map_version(tm)
    rows = map_rows(tm, origin="public", version=v, as_of=datetime(2026, 12, 11, tzinfo=KST))
    res = resolve(rows)
    assert len(res.codes()) == len(tm.stocks) and res.warnings == ()


@pytest.mark.parametrize(
    ("bad", "msg"),
    [
        ({"hs": "854", "flow": "exp", "weight": 0.1}, "짝수"),
        ({"hs": "85", "flow": "exp", "weight": 1.2}, "less than or equal"),
    ],
)
def test_해석기가_막는_형식(bad: dict[str, object], msg: str) -> None:
    data = {
        "version": 1,
        "stocks": [
            {
                "code": "000001",
                "name": "가상",
                "reviewed": False,
                "items": [bad],
                "evidence": [{"kind": "synthetic", "note": "x"}],
            }
        ],
    }
    with pytest.raises(ConfigFileError, match=msg):
        parse_trade_map(data)


def test_가중치_합_1_초과·중복·근거_없음() -> None:
    base = {"code": "000001", "name": "가상", "reviewed": False}
    ev = [{"kind": "synthetic", "note": "x"}]
    over = [{"hs": "85", "flow": "exp", "weight": 0.6}, {"hs": "84", "flow": "exp", "weight": 0.6}]
    with pytest.raises(ConfigFileError, match="가중치 합"):
        parse_trade_map({"version": 1, "stocks": [{**base, "items": over, "evidence": ev}]})
    dup = [{"hs": "85", "flow": "exp", "weight": 0.1}] * 2
    with pytest.raises(ConfigFileError, match="두 번"):
        parse_trade_map({"version": 1, "stocks": [{**base, "items": dup, "evidence": ev}]})
    one = [{"hs": "85", "flow": "exp", "weight": 0.1}]
    with pytest.raises(ConfigFileError):
        parse_trade_map({"version": 1, "stocks": [{**base, "items": one, "evidence": []}]})
