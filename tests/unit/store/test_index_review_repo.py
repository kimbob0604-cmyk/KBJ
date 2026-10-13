"""0018 `prv_index.*` — 마이그레이션 정적 구조 + 메모리 저장소(키·덮어쓰기·기간·최신 실행).

실제 Postgres 적용은 tests/integration/test_migrations_pg.py(메인이 Docker 로 한 번에 돌린다).
"""

from __future__ import annotations

import re
from datetime import date, datetime

import pytest

from kbj.core.index_rows import (
    EVIDENCE_KEYS,
    ConstituentRow,
    PassiveAumRow,
    PredictionRow,
    PredictionRun,
    ReviewEvent,
)
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.store.migrate import MIGRATIONS_DIR
from kbj.store.repos.index_review import MemoryIndexReviewRepo

SQL = (MIGRATIONS_DIR / "0018_index.sql").read_text(encoding="utf-8")
BODY = "\n".join(line.split("--", 1)[0] for line in SQL.splitlines())
NOW = datetime(2026, 10, 30, 17, 30, tzinfo=KST)


def test_migration_is_login_tier_and_idempotent() -> None:
    assert "CREATE SCHEMA IF NOT EXISTS prv_index;" in BODY
    assert "REVOKE ALL ON SCHEMA prv_index FROM PUBLIC;" in BODY
    assert "kbj_public_export" not in BODY and not re.search(r"\bpub_", BODY)  # 공개 역할 권한 없음
    assert not re.search(r"\bpublic\.", BODY)
    assert not re.search(r"CREATE (SCHEMA|TABLE|EXTENSION)(?! IF NOT EXISTS)", BODY)
    assert not re.search(r"CREATE (UNIQUE )?INDEX(?! IF NOT EXISTS)", BODY)
    starts = {s.strip().split(maxsplit=1)[0].upper() for s in BODY.split(";") if s.strip()}
    assert starts <= {"CREATE", "REVOKE"}
    tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS (prv_index\.\w+)", BODY))
    assert tables == {
        "prv_index.constituents", "prv_index.review_calendar", "prv_index.prediction_run",
        "prv_index.predictions", "prv_index.passive_aum",
    }  # fmt: skip
    # 값 표는 source·quality, 목록 출처는 user·press·synthetic 만
    for t in tables:
        block = re.search(rf"{re.escape(t)} \((.*?)\n\);", BODY, re.S)
        assert block is not None and "source" in block.group(1) and "quality" in block.group(1)
    assert "CHECK (source IN ('user', 'press', 'synthetic'))" in BODY
    # ok 가 아닌 실행은 후보 0·quality ≠ ok(지어내지 않음), 확인 전 일정은 ok 아님
    assert "CHECK (status = 'ok' OR (n_add = 0 AND n_delete = 0" in BODY
    assert "CHECK (confirmed OR quality <> 'ok')" in BODY


def test_migration_follows_0017() -> None:
    names = sorted(p.name for p in MIGRATIONS_DIR.glob("*.sql"))
    assert names.index("0018_index.sql") == names.index("0017_program.sql") + 1


def _ev(review: str = "2026-11", confirmed: bool = False) -> ReviewEvent:
    return ReviewEvent(
        index_id="msci_korea", review_id=review, index_title="MSCI Korea", display_order=1,
        base_date=date(2026, 10, 30), announce_date=date(2026, 11, 11),
        trade_date=date(2026, 11, 30), effective_date=date(2026, 12, 1), confirmed=confirmed,
        source="config", quality=Quality.OK if confirmed else Quality.ESTIMATED,
    )  # fmt: skip


def _run(base: date, kind: str = "model", status: str = "ok", n_add: int = 1) -> PredictionRun:
    return PredictionRun(
        index_id="msci_korea", review_id="2026-11", base_date=base, cutoff_kind=kind,  # type: ignore[arg-type]
        status=status, cutoff_won=10**12, cutoff_low_won=None, cutoff_high_won=None,  # type: ignore[arg-type]
        cutoff_method="coverage", n_members=40, n_add=n_add, n_delete=0, n_borderline=0,
        aum_won=None, data_as_of=NOW, source="KBJ", quality=Quality.ESTIMATED,
    )  # fmt: skip


def _pred(base: date, code: str, verdict: str = "add") -> PredictionRow:
    return PredictionRow(
        index_id="msci_korea", review_id="2026-11", base_date=base, cutoff_kind="model",
        code=code, name=None, verdict=verdict, member=False,  # type: ignore[arg-type]
        evidence={"required_rise": -0.1, "reasons": []}, cutoff_won=10**12, data_as_of=NOW,
        source="KBJ", quality=Quality.ESTIMATED,
    )  # fmt: skip


def test_rows_validate() -> None:
    with pytest.raises(ValueError):
        _ev(confirmed=False).__class__(**{**_ev().__dict__, "quality": Quality.OK})
    with pytest.raises(ValueError):  # ok 가 아닌 실행은 후보를 낼 수 없다
        _run(date(2026, 10, 30), status="no_constituents", n_add=1)
    with pytest.raises(ValueError):
        PredictionRow(**{**_pred(date(2026, 10, 30), "993001").__dict__, "evidence": {"x": 1}})
    with pytest.raises(ValueError):
        ConstituentRow("msci_korea", "993001", date(2026, 1, 1), None, None, 0.5, "krx")
    assert "required_rise" in EVIDENCE_KEYS


def test_constituents_by_period() -> None:
    r = MemoryIndexReviewRepo()
    r.put_constituents([
        ConstituentRow("msci_korea", "993001", date(2025, 12, 1), date(2026, 5, 31), None, None,
                       "synthetic"),
        ConstituentRow("msci_korea", "993002", date(2026, 6, 1), None, None, 0.4, "user"),
    ], loaded_by="t", received_at=NOW)  # fmt: skip
    assert [c.code for c in r.constituents("msci_korea", date(2026, 5, 31))] == ["993001"]
    assert [c.code for c in r.constituents("msci_korea", date(2026, 6, 1))] == ["993002"]
    assert r.constituents("kospi200", date(2026, 6, 1)) == []
    assert len(r.constituent_history("msci_korea")) == 2


def test_calendar_keeps_user_cutoff_on_rewrite() -> None:
    r = MemoryIndexReviewRepo()
    r.put_calendar([_ev()], loaded_by="t", now=NOW)
    assert r.set_user_cutoff("msci_korea", "2026-11", 3 * 10**12, loaded_by="t", now=NOW)
    r.put_calendar([_ev()], loaded_by="t", now=NOW)  # index.calendar 가 다시 써도
    assert r.calendar("msci_korea")[0].user_cutoff_won == 3 * 10**12
    assert not r.set_user_cutoff("msci_korea", "2027-02", 1, loaded_by="t", now=NOW)
    with pytest.raises(ValueError):
        r.set_user_cutoff("msci_korea", "2026-11", 0, loaded_by="t", now=NOW)


def test_put_run_replaces_rows_and_latest_wins() -> None:
    r = MemoryIndexReviewRepo()
    d1, d2 = date(2026, 10, 29), date(2026, 10, 30)
    r.put_run(_run(d1), [_pred(d1, "993001"), _pred(d1, "993002")], loaded_by="t", now=NOW)
    r.put_run(_run(d1), [_pred(d1, "993003")], loaded_by="t", now=NOW)  # 같은 키 — 통째로 바꿈
    assert [p.code for p in r.predictions("msci_korea", "2026-11", d1, "model")] == ["993003"]
    r.put_run(_run(d2), [_pred(d2, "993003", "borderline")], loaded_by="t", now=NOW)
    assert [x.base_date for x in r.latest_runs("msci_korea", "2026-11")] == [d2]
    latest = r.predictions_for_code("993003")
    assert len(latest) == 1 and latest[0].verdict == "borderline"
    with pytest.raises(ValueError):  # 실행 키와 다른 행
        r.put_run(_run(d2), [_pred(d1, "993001")], loaded_by="t", now=NOW)
    with pytest.raises(ValueError):  # 목록 없음 실행은 행을 쓰지 않는다
        r.put_run(_run(d2, status="no_constituents", n_add=0), [_pred(d2, "993001")],
                  loaded_by="t", now=NOW)  # fmt: skip


def test_passive_aum_latest_per_kind() -> None:
    r = MemoryIndexReviewRepo()
    rows = [
        PassiveAumRow("kospi200", date(2026, 10, 29), "etf", 100, 3, "KRX", Quality.ESTIMATED),
        PassiveAumRow("kospi200", date(2026, 10, 30), "etf", 110, 3, "KRX", Quality.ESTIMATED),
        PassiveAumRow("kospi200", date(2026, 10, 30), "non_etf", 50, None, "cfg",
                      Quality.ESTIMATED),
        PassiveAumRow("kospi200", date(2026, 11, 2), "etf", 999, 3, "KRX", Quality.ESTIMATED),
    ]  # fmt: skip
    r.put_passive_aum(rows, loaded_by="t", now=NOW)
    got = {x.kind: x.aum_won for x in r.passive_aum("kospi200", date(2026, 10, 30))}
    assert got == {"etf": 110, "non_etf": 50}
