"""`krx.vkospi`(P7 DIAG — D-P7-14, 꺼진 작업 #P7-1) — 가짜 KRX + 메모리 저장소.

VKOSPI 행 고르기(행 이름 후보·공백 무시)·미공표 not_ready·행 없음 사유·종가 없음 invalid·실패는
올린다.
가짜 KRX 행은 합성(칸 이름은 실측 전 가정).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from kbj.core.quality import Quality
from kbj.data.private.krx.client import KrxError
from kbj.data.private.krx.vol_index import SOURCE, pick_vkospi
from kbj.data.spec import DataKey
from kbj.services.collectors import krx_vkospi
from kbj.store.repos.diag_prv import MemoryDiagRepo
from tests.fakes.krx_server import FakeKrx
from tests.unit.collectors.p3_fakes import Clock, ctx, krx_client, kst, new_repos

D = date(2026, 12, 9)
NOW = kst(2026, 12, 10, 8, 10)
KEYS = [DataKey("KRX", "idx/drvprod_dd_trd", D.isoformat())]


def _run(fake: FakeKrx, repo: MemoryDiagRepo, **res: Any) -> Any:
    return krx_vkospi.run(
        ctx("krx.vkospi", D.isoformat(), KEYS, now=NOW, repos=new_repos(), krx=krx_client(fake),
            diag_repo=repo, **res)
    )  # fmt: skip


def test_writes_vkospi_close() -> None:
    fake = FakeKrx(Clock(NOW))
    fake.publish(D)
    repo = MemoryDiagRepo()
    res = _run(fake, repo)
    assert res.status == "ok" and res.rows == 1
    (row,) = repo.vol_index_history("vkospi", D, D)
    assert row.close == Decimal("18.52") and row.quality is Quality.OK and row.source == SOURCE
    assert fake.requested() == [("/idx/drvprod_dd_trd", "20261209")]


def test_not_published_is_not_ready_and_writes_nothing() -> None:
    fake = FakeKrx(Clock(NOW))
    repo = MemoryDiagRepo()
    res = _run(fake, repo)
    assert res.status == "not_ready" and "휴장" in res.detail["reason"]
    assert repo.vol_index_history("vkospi", D, D) == []


def test_http_failure_is_raised() -> None:
    fake = FakeKrx(Clock(NOW))
    fake.publish(D)
    fake.fail["/idx/drvprod_dd_trd"] = 500
    with pytest.raises(KrxError):
        _run(fake, MemoryDiagRepo())


def _row(name: str, close: str | None, day: str = "20261209") -> dict[str, Any]:
    r: dict[str, Any] = {"BAS_DD": day, "IDX_NM": name}
    if close is not None:
        r["CLSPRC_IDX"] = close
    return r


def test_pick_name_variants_and_missing() -> None:
    at = NOW
    assert pick_vkospi([_row("VKOSPI", "20.1")], D, received_at=at).row is not None
    p = pick_vkospi([_row("코스피200 선물지수", "1")], D, received_at=at)
    assert p.row is None and "#P7-1" in (p.reason or "")
    two = pick_vkospi([_row("VKOSPI", "1"), _row("코스피 200 변동성지수", "2")], D, received_at=at)
    assert two.row is None and "2개" in (two.reason or "")
    blank = pick_vkospi([_row("VKOSPI", None)], D, received_at=at)
    assert blank.row is not None and blank.row.close is None
    assert blank.row.quality is Quality.INVALID and blank.reason == "종가 없음"
    other_day = pick_vkospi([_row("VKOSPI", "1", "20261208")], D, received_at=at)
    assert other_day.row is None


def test_zero_close_is_stored_invalid() -> None:
    repo = MemoryDiagRepo()
    p = pick_vkospi([_row("VKOSPI", "0")], D, received_at=NOW)
    assert p.row is not None and p.row.close is None and p.reason == "종가 없음"
    repo.upsert_vol_index((p.row,), received_at=NOW, loaded_by="krx.vkospi")
    assert repo.vol_index_history("vkospi", D, D)[0].quality is Quality.INVALID
