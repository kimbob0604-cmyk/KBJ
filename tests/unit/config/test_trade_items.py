"""수출입 분류표 본문(묶음 EXP — docs/p6_design.md §1.4·§3.5, D-P6-9):

- `knowledge/trade_items.yaml`: 104 묶음, id g001~g104, 접두는 짝수 자리·장이 hs_chapters 에
  있음·겹침 0, 값(무역액) 칸 없음(분류표만).
- `knowledge/hs_chapters.yaml`: 01~97(77 결번) 자체 이름.
- `knowledge/trade_regions.yaml`: id 형식·시도 코드가 어댑터 목록에 있음·기간 개편(전남광주
  12)·같은 id 의 기간이 겹치지 않음.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from pathlib import Path

import pytest
import yaml

from kbj.config.files import ConfigFileError
from kbj.config.trade import (
    load_hs_chapters,
    load_trade_items,
    load_trade_regions,
    parse_trade_items,
)
from kbj.data.public.customs.models import SIDO_CODES

ROOT = Path(__file__).resolve().parents[3]


def test_104품목_형식() -> None:
    items = load_trade_items()
    assert len(items.groups) == 104
    assert [g.id for g in items.groups] == [f"g{i:03d}" for i in range(1, 105)]
    chapters = load_hs_chapters().chapters
    prefixes = [p for g in items.groups for p in g.hs]
    assert len(prefixes) == len(set(prefixes))
    for g in items.groups:
        assert g.name.strip() and g.hs
        for p in g.hs:
            assert len(p) % 2 == 0 and 2 <= len(p) <= 10
            assert p[:2] in chapters, (g.id, p)


def test_104품목_파일에_값_칸이_없다() -> None:
    raw = yaml.safe_load((ROOT / "config/knowledge/trade_items.yaml").read_text(encoding="utf-8"))
    for g in raw["groups"]:
        assert set(g) == {"id", "name", "hs"}


def test_겹치는_묶음은_설정_오류() -> None:
    with pytest.raises(ConfigFileError, match="겹친다"):
        parse_trade_items({"version": 1, "groups": [
            {"id": "a", "name": "가", "hs": ["85"]}, {"id": "b", "name": "나", "hs": ["8542"]},
        ]})  # fmt: skip


def test_HS_장_01_97_77_결번() -> None:
    ch = load_hs_chapters().chapters
    assert set(ch) == {f"{i:02d}" for i in range(1, 98)} - {"77"}
    assert all(v.strip() for v in ch.values())


def test_지역_통합_ID() -> None:
    regions = load_trade_regions().regions
    assert regions
    spans: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for r in regions:
        assert r.id.startswith("KR-") and r.sido_cd in SIDO_CODES, r
        spans[r.id].append((r.valid_from or "0000-00", r.valid_to or "9999-99"))
    for rid, ss in spans.items():
        ss.sort()
        for (_, a_to), (b_from, _) in itertools.pairwise(ss):
            assert a_to < b_from, rid  # 같은 id 의 기간이 겹치지 않는다
    yeosu = sorted((r.valid_from or "", r.sido_cd) for r in regions if r.id == "KR-46-yeosu")
    assert yeosu == [("", "46"), ("2026-07", "12")]
