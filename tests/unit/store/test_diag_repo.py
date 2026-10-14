"""`kbj.store.repos.diag`(공개)·`diag_prv`(로그인) — 진단 저장소(docs/p7_design.md §1.1·§3.3).

메모리판 쓰기·읽기 규칙, jsonb 의 Decimal 왕복, 공개 모듈이 로그인 표를 모르는지.
"""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from kbj.core.p7_rows import (
    DiagIndicatorRow,
    EventMarker,
    FearGreedRow,
    GaugeRow,
    VerdictRow,
    VolIndexRow,
)
from kbj.core.quality import Quality
from kbj.store.repos import memory_repos
from kbj.store.repos.diag import DiagPublicRepo, MemoryDiagPublicRepo
from kbj.store.repos.diag_prv import FEAR_GREED, DiagRepo, MemoryDiagRepo

ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 10, 13, 7, 0, tzinfo=UTC)
D1, D2 = date(2026, 10, 12), date(2026, 10, 13)
JOB = "diag.public"


def ind(day: date, key: str = "credit_spread", value: str | None = "0.6") -> DiagIndicatorRow:
    return DiagIndicatorRow(
        trade_date=day, key=key, value=None if value is None else Decimal(value), unit="%p",
        state=None if value is None else "normal", pct_rank=None, inputs={"x": Decimal("1.5")},
        source="KBJ:diag.public", as_of=NOW,
        quality=Quality.OK if value is not None else Quality.ESTIMATED,
    )  # fmt: skip


def test_public_repo_indicators_gauge_events() -> None:
    repo: DiagPublicRepo = MemoryDiagPublicRepo()
    repo.upsert_indicators([ind(D1), ind(D2), ind(D2, "deposits")], computed_at=NOW, loaded_by=JOB)
    repo.upsert_indicators([ind(D2, value="0.7")], computed_at=NOW, loaded_by=JOB)  # 덮는다
    assert [r.key for r in repo.indicators_on(D2)] == ["credit_spread", "deposits"]
    hist = repo.indicator_history("credit_spread", D1, D2)
    assert [r.value for r in hist] == [Decimal("0.6"), Decimal("0.7")]
    assert repo.last_indicator_date() == D2 and repo.last_indicator_date(D1) == D1
    g = GaugeRow(trade_date=D2, label="neutral", score=0, parts={"credit_spread": 0},
                 source="KBJ:diag.public", as_of=NOW, quality=Quality.OK)  # fmt: skip
    repo.upsert_gauge(g, computed_at=NOW, loaded_by=JOB)
    assert repo.gauge_on(D2) == g and repo.gauge_on(D1) is None
    assert repo.gauge_history(D1, D2) == [g]
    ev = EventMarker(date=D2, kind="mpc", title="금통위", source="KBJ:calendar_events",
                     as_of=NOW, quality=Quality.OK)  # fmt: skip
    old = EventMarker(date=D1, kind="expiry", title="월물 만기", source="KBJ:calendar",
                      as_of=NOW, quality=Quality.OK)  # fmt: skip
    repo.replace_events(D1, D2, [ev, old], computed_at=NOW, loaded_by=JOB)
    repo.replace_events(D2, D2, [], computed_at=NOW, loaded_by=JOB)  # D2 만 갈아 끼운다
    assert repo.events_between(D1, D2) == [old]
    with pytest.raises(ValueError, match="기간 밖"):
        repo.replace_events(D2, D2, [old], computed_at=NOW, loaded_by=JOB)
    with pytest.raises(ValueError, match="loaded_by"):
        repo.upsert_gauge(g, computed_at=NOW, loaded_by=" ")
    with pytest.raises(ValueError, match="naive"):
        repo.upsert_gauge(g, computed_at=datetime(2026, 10, 13), loaded_by=JOB)  # noqa: DTZ001


def test_private_repo_fear_greed_verdict_vol_index() -> None:
    repo: DiagRepo = MemoryDiagRepo()
    fg = FearGreedRow(
        trade_date=D2, score=Decimal("52.5"), band="neutral",
        components={"vol": Decimal("40.0"), "breadth": None}, source="KBJ:diag.daily",
        as_of=NOW, quality=Quality.ESTIMATED,
    )  # fmt: skip
    repo.upsert_fear_greed(fg, computed_at=NOW, loaded_by="diag.daily")
    assert repo.fear_greed_history(D1, D2) == [fg]
    v = VerdictRow(trade_date=D2, label="hold", score=None, parts={}, event_flags=(),
                   source="KBJ:diag.daily", as_of=NOW, quality=Quality.INVALID,
                   reasons=("성분 3개",))  # fmt: skip
    repo.upsert_verdict(v, computed_at=NOW, loaded_by="diag.daily")
    assert repo.verdict_on(D2) == v and repo.last_verdict_date() == D2
    vi = VolIndexRow(trade_date=D2, index_id="vkospi", close=Decimal("18.2"),
                     source="KRX:idx/drvprod_dd_trd", as_of=NOW, quality=Quality.OK)  # fmt: skip
    repo.upsert_vol_index([vi], received_at=NOW, loaded_by="krx.vkospi")
    assert repo.vol_index_history("vkospi", D1, D2) == [vi]


def test_jsonb_decimal_round_trip() -> None:
    fg = FearGreedRow(
        trade_date=D2, score=Decimal("52.5"), band="neutral",
        components={"vol": Decimal("40.1"), "breadth": None}, source="KBJ:diag.daily",
        as_of=NOW, quality=Quality.ESTIMATED,
    )  # fmt: skip
    values = FEAR_GREED.values(fg, {"computed_at": NOW, "loaded_by": "t"})
    comp = values[FEAR_GREED.fields.index("components")]
    assert comp.obj == {"vol": "40.1", "breadth": None}  # pyright: ignore[reportAttributeAccessIssue]
    rec = [comp.obj if f == "components" else getattr(fg, f) for f in FEAR_GREED.fields]  # pyright: ignore[reportAttributeAccessIssue]
    rec = [v.value if isinstance(v, Quality) else v for v in rec]
    back = FEAR_GREED.from_db(rec)
    assert back.components == {"vol": Decimal("40.1"), "breadth": None}


def test_bundles_share_public_diag_and_public_module_is_isolated() -> None:
    m = memory_repos()
    assert m.public().diag is m.diag_pub
    tree = ast.parse((ROOT / "kbj" / "store" / "repos" / "diag.py").read_text(encoding="utf-8"))
    imported = {
        n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module is not None
    }
    assert "kbj.store.repos.diag_prv" not in imported
    assert "prv_diag" not in (ROOT / "kbj" / "store" / "repos" / "diag.py").read_text(
        encoding="utf-8"
    ).replace("kbj.store.repos.diag_prv", "")
