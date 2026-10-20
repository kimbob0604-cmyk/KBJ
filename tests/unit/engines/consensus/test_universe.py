"""컨센서스 수집 대상 규칙(§4.7) — p4_expect.yaml 의 consensus 칸(na 사유)과 같은 규칙."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from kbj.core.fin_rows import NaReason
from kbj.core.quality import Quality
from kbj.core.rows import UniverseRow
from kbj.engines.consensus.universe import consensus_na, market_of
from tests.fixtures.synthetic.p4_world import build_world

D = date(2026, 11, 13)


def u(market: str | None, kind: str | None) -> UniverseRow:
    return UniverseRow("994010", D, "모의", market, kind, None, "krx", Quality.OK)


@pytest.mark.parametrize(
    ("market", "kind", "want"),
    [
        ("KOSPI", "common", None),
        ("KOSDAQ GLOBAL", "common", None),
        ("kosdaq", "common", None),
        ("KONEX", "common", NaReason.KONEX_NO_CONSENSUS),
        ("KOSDAQ", "spac", NaReason.NOT_IN_CONSENSUS_UNIVERSE),
        ("KOSPI", "reit", NaReason.NOT_IN_CONSENSUS_UNIVERSE),
        ("KOSPI", "pref", NaReason.NOT_IN_CONSENSUS_UNIVERSE),  # 부르는 쪽이 보통주로 바꾼다
        ("KOSPI", "etf", NaReason.NOT_IN_CONSENSUS_UNIVERSE),
        # KRX 기본정보는 종류를 비워 둔다 — 보드 정본 분류(이름·코드)로 본다(2026-10-20: 0종목이던 것)
        ("KOSPI", None, None),
        (None, "common", NaReason.NOT_IN_CONSENSUS_UNIVERSE),
    ],
)
def test_consensus_na(market: str | None, kind: str | None, want: NaReason | None) -> None:
    assert consensus_na(u(market, kind)) == want


def test_kind_from_board_classifier_when_source_has_none() -> None:
    spac = UniverseRow("994010", D, "합성스팩7호", "KOSDAQ", None, None, "krx", Quality.OK)
    pref = UniverseRow("994015", D, "모의우", "KOSPI", None, None, "krx", Quality.OK)
    assert consensus_na(spac) is NaReason.NOT_IN_CONSENSUS_UNIVERSE
    assert consensus_na(pref) is NaReason.NOT_IN_CONSENSUS_UNIVERSE


def test_market_of() -> None:
    assert market_of(u("KOSDAQ GLOBAL", "common")) == "KOSDAQ"
    assert market_of(u(None, "common")) is None


def test_matches_the_expect_matrix_for_world_stocks() -> None:
    """p4_expect.yaml 의 consensus 가 na:<사유> 인 보통주·종류 사유가 이 규칙과 같다."""
    root = Path(__file__).resolve().parents[4]
    doc = yaml.safe_load((root / "tests/fixtures/synthetic/p4_expect.yaml").read_text("utf-8"))
    w = build_world()
    for row in w.universe():
        want = doc["stocks"][row.code]["consensus"]
        got = consensus_na(row)
        if row.kind == "pref" or not w.stock(row.code).watch:
            continue  # 우선주는 보통주 것, 관심 밖은 top_n 에 달렸다
        if got is None:
            assert not str(want).startswith("na:"), row.code
        else:
            assert want == f"na:{got.value}", row.code
