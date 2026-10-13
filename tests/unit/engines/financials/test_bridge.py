"""이익 브릿지(dart-report `bridge` 승격본) — 잔차 플러그·부호·한 파일 고르기·워터폴.

docs/p4_design.md §4.6·§8.3. DR CLAUDE.md '깨뜨리면 안 되는 것' 3: 잔차 플러그는 언제나 세전이익에
맞춘다. legacy 버그(부호 키워드 '손')는 고정한다(D-P4-8).
"""

from __future__ import annotations

from typing import Any

import pytest

from kbj.data.public.dart.client import DartClient
from kbj.engines.financials import bridge as B
from kbj.engines.financials.costs import load_mapping
from kbj.engines.financials.statements import EOK, Period
from tests.fixtures.synthetic.p4_world import build_world
from tests.golden.dart_report.golden import WorldDart, world_now


@pytest.fixture(scope="module")
def mapping() -> dict[str, Any]:
    return load_mapping()


def _period(op: float, pretax: float, tax: float | None, net: float) -> Period:
    cum = {"operating_income": op * EOK, "pretax_income": pretax * EOK, "net_income": net * EOK}
    if tax is not None:
        cum["tax"] = tax * EOK
    return Period(2025, "4Q", "CFS", cum)


def test_잔차_플러그는_세전이익에_맞춘다() -> None:
    steps = B.build_bridge(
        _period(100, 80, 18, 62), {"순이자손익": -5 * EOK, "순외환손익": 0.004 * EOK}
    )
    names = [s["name"] for s in steps]
    assert names == [
        "영업이익",
        "순이자손익",
        "기타 영업외손익",
        "법인세차감전순이익",
        "법인세비용",
        "당기순이익",
    ]  # fmt: skip — 0.01억 미만 순외환은 생략
    resid = steps[2]
    assert resid["note"] == "주석 미분해 잔차" and resid["value"] == pytest.approx(-15.004)
    lay = B.waterfall_layout(steps)
    assert lay[-1]["total"] == 62 and lay[1]["down"] == 5 and lay[1]["base"] == 95


def test_세부가_없으면_영업외손익_한_줄_세전_없으면_빈_목록() -> None:
    steps = B.build_bridge(_period(100, 90, None, 70), None)
    assert [s["name"] for s in steps] == [
        "영업이익",
        "영업외손익",
        "법인세차감전순이익",
        "당기순이익",
    ]
    assert steps[1]["note"] == "주석 분해 미적용"
    p = Period(2025, "4Q", "CFS", {"operating_income": 1.0})
    assert B.build_bridge(p, {}) == []


def test_법인세_수익도_비용_단계로_간다_legacy() -> None:
    """legacy 동작(고정): 법인세가 음수(수익)여도 `-abs` — 워터폴 끝이 순이익과 어긋난다."""
    steps = B.build_bridge(_period(-10, -12, -3, -9), {})
    assert steps[-2] == {"name": "법인세비용", "value": -3.0, "kind": "delta"}


def test_부호_키워드_손이_평가이익에도_걸린다_legacy(mapping: dict[str, Any]) -> None:
    rows = [("이자수익", 10.0), ("이자비용", 4.0), ("당기손익-공정가치측정금융자산평가이익", 3.0),
            ("외환차손", 2.0), ("기부금", 1.0), ("합계", 99.0), ("기타의수수료", 5.0)]  # fmt: skip
    buckets, un = B._bucketize_bridge(rows, mapping["bridge"])
    assert buckets["순이자손익"] == 6.0
    assert buckets["FVPL 평가이익"] == -3.0  # '손'(negative_keywords) — legacy 버그 그대로
    assert buckets["순외환손익"] == -2.0 and buckets["배당·기타"] == -1.0
    assert un == [("기타의수수료", 5.0)]


def test_원문_셋_중_기대값에_가까운_한_파일만(mapping: dict[str, Any]) -> None:
    w = build_world()
    client = WorldDart(w, world_now()).client(DartClient)
    t = next(x for x in w.quarter_truth("99400010", "CFS") if x.key == (2025, 1))
    exp = float(t.pretax - t.op)
    lines: list[str] = []
    got = B.fetch_bridge_details(client, "99400010", [2025], mapping, verbose=True,
                                 expected={(2025, "1Q"): exp}, log=lines.append)  # fmt: skip
    q1 = got[(2025, "1Q")]
    # 연결 표 하나만 — 본문+연결+별도를 다 더한 값(약 2.6배)이 아니다
    assert abs(sum(q1.values())) < abs(exp) * 3
    assert set(q1) == set(B.BRIDGE_ORDER)
