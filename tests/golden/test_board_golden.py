"""신고가 보드 골든 — PLAN §8 P3 완료 기준, docs/p3_design.md §0.3·§4.1·§8.1, ADR 0017.

합성 30거래일 골든과 `kbj.engines.board.build.compute_day` 의 결과를 필드 단위로 비교한다. 실패
메시지는 (날짜, 파일, JSON 경로, 골든 값, kbj 값).

**재캡처(사용자 요청 2026-10-08)**: 처음 골든은 legacy board 엔진(shim 전 — META `legacy_commit`)의
산출이었다(옛 정의 d60·w52=252봉·hist). 신고가 정의를 3축(d120 = 120 시장 거래일 · w52 = 달력 364일
· hist = 상장 이후 전체, 엄격 >)으로 바꾼 것은 사용자 결정이라, 같은 합성 입력을 새 엔진으로 다시
계산해 기대 산출을 바꿨다(tests/golden/board/recapture.py — META `recaptured`). 그래서 이 골든은
이제 '산출이 조용히 바뀌지 않았다' 를 지키고, **정의대로 계산하는지는 독립 오라클이 지킨다** — 아래
`test_골든은_독립_오라클과_같다`(골든 30일 전 종목의 창별 기준 최고가·갱신을 엔진을 쓰지 않는 날짜
루프로 다시 센다)와 tests/property/test_newhigh_oracle.py.
"""

from __future__ import annotations

import pytest

from kbj.core.quality import Quality
from kbj.engines.board import newhigh as nh
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
from tests.oracles.newhigh_loop import labels_by_loop

COMMON = load_common()
CFG = golden_config(COMMON)
DAYS = list(iter_days(COMMON))


def test_골든은_30일이다() -> None:
    assert len(DAYS) == 30


@pytest.mark.parametrize("day", DAYS, ids=[d.asof for d in DAYS])
def test_compute_day_는_골든과_같다(day: GoldenDay) -> None:
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
    counts = {"hist": 0, "w52": 0, "d120": 0}
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


def test_골든은_독립_오라클과_같다() -> None:
    """골든 기대 산출의 창별 기준 최고가(refs)·갱신(hits)이 날짜 루프 오라클과 같다(ADR 0017).

    시장 달력은 골든 입력 전 종목 일봉 날짜의 합집합(`compute_day` 의 기본과 같은 뜻 — 그날 한
    종목이라도 거래했으면 시장 거래일). 수정주가 의심 종목은 엔진이 창을 자르므로 엔진 쪽 None 만
    허용한다. 역사적(hist)은 스칼라 입력이라 '기준 ≥ 모든 창' 관계와 갱신 = (당일 > 기준)만 본다.
    """
    windows = nh.lookbacks(CFG)
    checked = hits = 0
    for day in DAYS:
        # 골든 입력의 일봉은 dict 행이다(`as_rows` 로 모양을 맞춘다)
        series = {c: nh.as_rows(rows) for c, rows in day.inputs.series.items()}
        market = sorted({str(r["asof"]) for rows in series.values() for r in rows})
        for r in day.expected["universe"]["stocks"]:
            rows = series[r["code"]]
            loop = labels_by_loop(rows, day.asof, windows, market)
            for basis, key in (("close", "close_basis"), ("high", "high_basis")):
                b = r[key]
                for kind in windows:
                    want = loop[basis][kind]
                    gap = b["gap"][kind]
                    if gap is None:  # 엔진이 판정하지 않음(창 못 채움·당일 값 없음·계단)
                        assert want["hit"] is None or r["suspect"], (day.asof, r["code"], kind)
                        assert b["hits"][kind] is False
                        continue
                    assert b["hits"][kind] is bool(want["hit"]), (day.asof, r["code"], basis, kind)
                    checked += 1
                    hits += bool(want["hit"])
            # 기본 기준(종가)의 기준 최고가는 값으로 대조한다
            for kind in windows:
                ref, want = r["refs"][kind], loop["close"][kind]["ref"]
                assert ref is None or ref == want, (day.asof, r["code"], kind, ref, want)
            h = r["refs"].get("hist")
            if h is not None:
                assert all(
                    v is None or v <= h * (1 + 1e-9) for k, v in r["refs"].items() if k != "hist"
                )
                assert r["hits"]["hist"] is (r["close"] > h)
    assert checked > 10_000 and hits > 100, (checked, hits)
