"""카탈로그 P4 변경(docs/p4_design.md §3.3) — 새·바뀐 DART 데이터셋의 저장 표가 0010·0011 에 있고,
선점 대상은
등록부의 한 작업만 받는다. KIS 컨센서스 데이터셋(로그인)의 저장 표도 0012 에 있다."""

from __future__ import annotations

from kbj.data import catalog
from kbj.data.spec import Tier
from kbj.services.scheduler.registry import migration_tables

TABLES = migration_tables()
P4_DART = (
    "DART:list", "DART:document", "DART:cvbdIsDecsn", "DART:bdwtIsDecsn", "DART:exbdIsDecsn",
    "DART:piicDecsn", "DART:fricDecsn", "DART:tsstkAqDecsn", "DART:tsstkDpDecsn",
    "DART:elestock", "DART:majorstock", "DART:stockTotqySttus", "DART:company",
    "DART:fnlttSinglAcntAll", "DART:fnlttMultiAcnt", "DART:report_inputs",
)  # fmt: skip


def test_p4_stores_are_created_by_migrations() -> None:
    for ds in P4_DART:
        spec = catalog.get(ds)
        assert spec.tier is Tier.PUBLIC, ds
        assert spec.store in TABLES, (ds, spec.store)
        assert spec.store.startswith(("pub_filings.", "pub_fin.")), ds
    # 로그인 — 컨센서스 스냅(묶음 K 가 웨이브 2 에 invest_opinion 행을 더한다)
    kis = catalog.get("KIS:consensus_estimate")
    assert kis.tier is Tier.PRIVATE and kis.store == "prv_fin.consensus_snapshot" in TABLES


def test_p4_event_datasets_are_claimable_but_not_preclaimed() -> None:
    """저장하는 event 데이터셋은 선점 대상(claimable)이다 — 단 실행기는 정시 발화에서 미리 잡지 않고
    처리기가 claims 자원으로 잡는다(D-P4-25 ②, tests/unit/scheduler/test_registry_validation.py)."""
    events = [catalog.get(ds) for ds in P4_DART if catalog.get(ds).as_of_kind == "event"]
    assert len(events) == 14
    assert all(catalog.claimable(s) for s in events)
