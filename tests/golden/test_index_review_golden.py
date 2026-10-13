"""골든(합성) — 합성 240종목·합성 구성종목으로 세 리뷰(MSCI 2026-11·KOSPI200 2026-12·KOSDAQ150
2026-12)
예측 → 고정 기대값(tests/golden/index_review/expect.json, 시드 20261113 — p5_world 와 같다).

다시 만들기(엔진 규칙을 일부러 바꿨을 때만 — 바뀐 줄을 검토한다):
`uv run python -m tests.fixtures.synthetic.index_world --write-golden`
비교는 허용오차로(바이트 비교 금지 — 플랫폼 간 float 끝자리, CLAUDE.md §4).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.fixtures.synthetic.index_world import GOLDEN_PATH, SEED, golden_doc

EXPECT: dict[str, Any] = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
FLOATS = ("fif", "gap_mcap", "gap_ff", "required_rise")


def test_golden_is_synthetic_and_covers_three_reviews() -> None:
    assert EXPECT["seed"] == SEED
    assert set(EXPECT["reviews"]) == {"msci_korea:2026-11", "kospi200:2026-12", "kosdaq150:2026-12"}
    for key, rv in EXPECT["reviews"].items():
        assert rv["status"] == "ok", key
        outcomes = {r["outcome"] for r in rv["rows"]}
        assert {"add", "delete"} <= outcomes, key  # 세 리뷰 모두 편입·편출 후보가 있다
        assert all(r["code"].startswith("993") for r in rv["rows"])  # 합성 대역만


@pytest.mark.parametrize("key", sorted(EXPECT["reviews"]))
def test_engine_matches_golden(key: str) -> None:
    got = golden_doc()["reviews"][key]
    want = EXPECT["reviews"][key]
    assert got["status"] == want["status"] and got["n_members"] == want["n_members"]
    assert got["cutoff"] == want["cutoff"]
    assert [(r["code"], r["outcome"], r["member"]) for r in got["rows"]] == [
        (r["code"], r["outcome"], r["member"]) for r in want["rows"]
    ]
    for g, w in zip(got["rows"], want["rows"], strict=True):
        for f in FLOATS:
            if w[f] is None:
                assert g[f] is None, (g["code"], f)
            else:
                assert g[f] == pytest.approx(w[f], rel=1e-9, abs=1e-12), (g["code"], f)
        if w["passive_won"] is None:
            assert g["passive_won"] is None
        else:
            assert g["passive_won"] == pytest.approx(w["passive_won"], abs=2)
