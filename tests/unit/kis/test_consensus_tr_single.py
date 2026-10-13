"""KIS 컨센서스 TR id 는 한 곳(`kbj/data/private/kis/datasets.py:CONSENSUS_TRS`)에만 — 묶음 K.

docs/p4_design.md D-P4-9·§9.3(메인 결정 2026-10-08), ADR 0015 2-1. 레포 안의 옛 값이 서로 어긋났다
(카탈로그 노트·conflict_map·가짜 서버). 실측(#29) 뒤 상수 하나만 고치면 되도록, 코드(`kbj/`·
`tests/`·`scripts/` 의 `.py`)에 컨센서스 TR id 꼴 문자열이 그 파일 밖에 없어야 한다(문서는 기록이라
제외). 가짜 서버·시뮬레이션 KIS_SPECS 는 상수와 같은 tr_id·경로를 쓴다.
"""

from __future__ import annotations

import re
from pathlib import Path

from kbj.data.private.kis.consensus import TR_ESTIMATE, TR_OPINION
from kbj.data.private.kis.datasets import CONSENSUS_TRS, DATASETS
from tests.fakes.kis_server import TRS
from tests.sim.harness import KIS_SPECS

ROOT = Path(__file__).resolve().parents[3]
HOME = Path("kbj/data/private/kis/datasets.py")
# 컨센서스 TR id 꼴(시세분석 66xx 대역) — 이 정규식 문자열 자체는 걸리지 않는다
TR_LIKE = re.compile(r"[FH]HKST66\d{4}C0")
SCAN = ("kbj", "tests", "scripts")


def _hits() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for top in SCAN:
        for p in sorted((ROOT / top).rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            found = TR_LIKE.findall(p.read_text(encoding="utf-8"))
            if found:
                out[str(p.relative_to(ROOT))] = found
    return out


def test_consensus_tr_ids_live_only_in_datasets() -> None:
    hits = _hits()
    assert set(hits) == {str(HOME)}, {k: v for k, v in hits.items() if k != str(HOME)}
    assert set(hits[str(HOME)]) == {tr for tr, _p in CONSENSUS_TRS.values()}


def test_constant_shape() -> None:
    want = {"consensus_estimate", "invest_opinion", "invest_opinion_by_broker"}
    assert set(CONSENSUS_TRS) == want
    for name, (tr, path) in CONSENSUS_TRS.items():
        assert TR_LIKE.fullmatch(tr), name
        assert path.startswith("/uapi/domestic-stock/v1/quotations/"), name
    assert len({tr for tr, _p in CONSENSUS_TRS.values()}) == len(CONSENSUS_TRS)


def test_fake_server_and_simulation_follow_the_constant() -> None:
    for tr, path in CONSENSUS_TRS.values():
        assert TRS[tr] == path
    assert KIS_SPECS["consensus_estimate"].tr_id == CONSENSUS_TRS["consensus_estimate"][0]
    assert KIS_SPECS["invest_opinion"].tr_id == CONSENSUS_TRS["invest_opinion"][0]
    assert (TR_ESTIMATE.tr_id, TR_ESTIMATE.path) == CONSENSUS_TRS["consensus_estimate"]
    assert (TR_OPINION.tr_id, TR_OPINION.path) == CONSENSUS_TRS["invest_opinion"]


def test_catalog_notes_are_built_from_the_constant() -> None:
    by_id = {d.id: d for d in DATASETS}
    for name in ("consensus_estimate", "invest_opinion"):
        tr, path = CONSENSUS_TRS[name]
        note = by_id[f"KIS:{name}"].notes
        assert tr in note and path in note and "[확인 필요 #29" in note
        # 다른 컨센서스 TR(옛 노트의 엇갈린 문구)은 그 데이터셋 노트에 없다 — 대안 표기만 허용
        others = {t for n, (t, _p) in CONSENSUS_TRS.items() if n != name}
        if name == "consensus_estimate":
            assert not others & set(TR_LIKE.findall(note))
