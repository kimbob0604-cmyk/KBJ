"""신고가 보드 = 기존 board 산출(골든) — PLAN §8 P3 완료 기준, docs/p3_design.md §0.3·§4.1·§8.1.

legacy board 엔진(shim 전 — META.json `legacy_commit`)으로 캡처한 합성 30거래일 골든과
`kbj.engines.board.build.compute_day` 의 결과를 필드 단위로 비교한다. 실패 메시지는 (날짜, 파일,
JSON 경로, legacy 값, kbj 값).
"""

from __future__ import annotations

import pytest

from kbj.core.quality import Quality
from kbj.engines.board.build import compute_day
from tests.golden.board_golden import (
    FILES,
    GoldenDay,
    as_json,
    diff,
    golden_config,
    iter_days,
    load_common,
)

COMMON = load_common()
CFG = golden_config(COMMON)
DAYS = list(iter_days(COMMON))


def test_골든은_30일이다() -> None:
    assert len(DAYS) == 30


@pytest.mark.parametrize("day", DAYS, ids=[d.asof for d in DAYS])
def test_compute_day_는_legacy_산출과_같다(day: GoldenDay) -> None:
    got = compute_day(day.inputs, CFG)
    payloads = as_json(got.payloads())
    problems: list[str] = []
    for name in FILES:
        diff(f"{day.asof} {name}.json $", day.expected[name], payloads[name], problems)
    assert not problems, "\n".join(problems[:30]) + (
        f"\n… 외 {len(problems) - 30}건" if len(problems) > 30 else ""
    )


def test_품질은_종가_출처를_따른다() -> None:
    """KRX 확정이면 ok, KIS 마감(잠정)이면 estimated — 골든 안에 둘 다 있다."""
    seen = set()
    for day in DAYS:
        got = compute_day(day.inputs, CFG)
        want = Quality.OK if day.expected["universe"]["close_confirmed"] else Quality.ESTIMATED
        assert got.quality is want, day.asof
        seen.add(got.quality)
    assert seen == {Quality.OK, Quality.ESTIMATED}


def test_골든이_사례를_고루_덮는다() -> None:
    """심은 사례가 실제로 산출에 나타나는지(골든이 빈 표끼리 비교하지 않게)."""
    types: set[str] = set()
    notes: list[str] = []
    diag: list[str] = []
    counts = {"hist": 0, "w52": 0, "d60": 0}
    near = suspects = stale = no_series = 0
    for day in DAYS:
        e = day.expected
        types |= {x["type"] for x in e["events"]["events"]}
        notes += e["universe"]["notes"]
        diag += e["universe"]["diagnostics"]
        for k in counts:
            counts[k] += e["newhigh"]["counts"].get(k, 0)
        near += len(e["newhigh"]["proximity"])
        suspects += len(e["universe"]["adjusted_price_suspects"])
        stale += bool(e["universe"]["snapshot_stale"])
        no_series += len(e["universe"]["no_price_series"])
    assert types == {
        "material_giveback",
        "volume_anomaly",
        "multi_label_high",
        "proximity_cluster",
        "breakout_fail",
    }
    assert all(v > 0 for v in counts.values()), counts
    assert near and suspects and stale and no_series
    assert any("잠정치" in n for n in notes)  # 정규화 문장이 실제로 비교된다
    assert any("분류 체계가 섞여" in n for n in notes)
    assert any("단위 의심" in d for d in diag)
