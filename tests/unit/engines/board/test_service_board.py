"""`board.daily` 서비스(kbj.services.engine.board) — 메모리 저장소로 하루를 돌린다.

docs/p3_design.md §1.3('새로: 메모리 저장소로 하루 → 다음 날 확정, 같은 as_of 재실행 멱등')·§4.1,
메인 결정 R8(역사적 신고가는 KRX 백필이 닿는 범위에서만 — 범위 시작일을 산출에 싣는다).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import BOARD_ARTIFACTS
from kbj.engines.board import newhigh as nh
from kbj.services.engine import board as svc
from kbj.services.scheduler.handlers import JobContext
from tests.unit.engines.board.board_world import (
    CFG,
    FIRST,
    KNOW,
    NOW_DAILY,
    D,
    World,
    build_world,
)


def _run(
    w: World,
    now: datetime = NOW_DAILY,
    asof: date = D,
    mode: Literal["daily", "confirm"] = "daily",
) -> svc.BoardRunReport:
    return svc.run_board(
        w.repos, asof, now=now, loaded_by="board.daily", mode=mode, cfg=CFG, knowledge=KNOW
    )


def test_하루_보드를_잠정으로_쓴다() -> None:
    w = build_world()
    rep = _run(w)
    assert rep.status == "ok"
    assert rep.quality is Quality.ESTIMATED  # KIS 마감값 — KRX 확정 전
    board = w.repos.board
    assert {a for (d, a) in board.artifact_rows if d == D} == set(BOARD_ARTIFACTS)
    days = board.stock_days(D)
    # ETF·invalid 스냅은 보드에 없다
    assert set(days) == {"000010", "000020", "000030", "000040"}
    s10 = days["000010"]
    assert s10.label in ("hist", "w52")
    assert s10.quality is Quality.ESTIMATED and s10.source == "kis"
    # 금액은 원(엔진 안의 억원 × 1e8) — 정수
    assert s10.turnover == 10500 * 100_000 and isinstance(s10.turnover, int)
    assert s10.mktcap == 500_000_000_000
    assert days["000020"].label == "d60"
    labels = board.labels(D, "close")
    assert {c: lb.kind for c, lb in labels.items()}["000020"] == "d60"
    assert all(lb.quality is Quality.ESTIMATED for lb in labels.values())


def test_산출에는_안내와_범위가_실린다() -> None:
    w = build_world()
    _run(w)
    meta = w.repos.board.artifact(D, "universe_meta")
    assert meta is not None and "stocks" not in meta.payload
    notes = meta.payload["notes"]
    assert any("잠정치" in n and "KIS 마감값(잠정)" in n for n in notes)
    assert any("KRX 대조에서 어긋난 1종목" in n for n in notes)
    assert meta.payload["funds_excluded"] == "1종목 제외 / 종류 판정 6종목"
    nhj = w.repos.board.artifact(D, "newhigh")
    assert nhj is not None
    scope = nhj.payload["hist_scope"]
    assert scope["history_from"] == FIRST.isoformat()  # 받은 이력의 첫날(R8)
    assert scope["n_before_listing"] == 1  # 000030
    assert scope["n_listing_unknown"] == 1  # 000040
    hist_ne = meta.payload["hist_not_evaluated"]
    assert hist_ne[nh.HIST_BEFORE_LISTING] == 1
    assert hist_ne[nh.HIST_LISTING_UNKNOWN] == 1
    assert nhj.as_of.isoformat() == "2026-10-06T15:30:00+09:00"
    assert nhj.engine_version and len(nhj.input_digest) == 32


def test_이력이_상장일_전에서_끊기면_역사적_신고가를_내지_않는다() -> None:
    w = build_world()
    _run(w)
    days = w.repos.board.stock_days(D)
    for code in ("000030", "000040"):
        s = days[code]
        assert s.extra["refs"]["hist"] is None
        assert s.label == "w52"  # 52주는 창 안이라 계산한다
    # 000010 은 상장일 = 이력 시작 → 상장 이후 전체 → hist 를 계산한다
    assert days["000010"].extra["refs"]["hist"] is not None


def test_스칼라를_굴려_저장한다() -> None:
    w = build_world()
    rep = _run(w)
    at = w.repos.board.alltime()
    assert rep.n_alltime == len(at) == 6  # 일봉이 있는 종목 전부(보드 제외 종목도 스칼라는 굴린다)
    a = at["000010"]
    assert a.last_date == D and a.hi == 10500.0 and a.prev_hi == 10060.0
    assert a.history_from == FIRST
    assert a.quality is Quality.ESTIMATED and a.source == "kis"


def test_같은_as_of_재실행은_멱등이다() -> None:
    w = build_world()
    _run(w)
    first = (
        dict(w.repos.board.label_rows),
        dict(w.repos.board.stock_day_rows),
        {k: v.payload for k, v in w.repos.board.artifact_rows.items()},
        {c: s.row for c, s in w.repos.board.alltime_rows.items()},
    )
    rep = _run(w)
    second = (
        dict(w.repos.board.label_rows),
        dict(w.repos.board.stock_day_rows),
        {k: v.payload for k, v in w.repos.board.artifact_rows.items()},
        {c: s.row for c, s in w.repos.board.alltime_rows.items()},
    )
    assert first == second
    assert rep.n_alltime == 0  # 바뀐 스칼라가 없다


def test_그날_일봉이_없으면_not_ready() -> None:
    w = build_world()
    rep = _run(w, asof=date(2026, 10, 8))
    assert rep.status == "not_ready"
    assert w.repos.board.artifact_rows == {}


def test_처리기는_저장소_자원을_쓰고_detail_에_값을_싣지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = build_world()
    monkeypatch.setattr(svc, "BoardKnowledge", type("K", (), {"load": staticmethod(lambda: KNOW)}))
    ctx = JobContext(
        job="board.daily",
        as_of=D.isoformat(),
        run_id="r1",
        attempt=1,
        now=NOW_DAILY,
        resources={"repos": w.repos},
    )
    res = svc.daily(ctx)
    assert res.status == "ok"
    assert res.rows and res.rows > 0
    assert res.detail["quality"] == "estimated"
    assert res.detail["n_stocks"] == 4
    flat = repr(res.detail)
    assert "000010" not in flat  # 종목코드·값은 detail 에 싣지 않는다


def test_다음_날_보드는_전일_라벨로_신규_이어감을_적는다() -> None:
    from tests.unit.engines.board.board_world import add_krx_confirm, bar, snap

    w = build_world()
    _run(w)
    add_krx_confirm(w, close_000010=10500.0)
    nxt = date(2026, 10, 7)
    w.repos.market.upsert_daily_bars([bar("000020", nxt, 10300.0, "kis")], loaded_by="t")
    w.repos.market.upsert_snapshots([snap("000020", nxt, 10300.0, "kis")], loaded_by="t")
    rep = _run(w, now=datetime(2026, 10, 7, 7, 20, tzinfo=NOW_DAILY.tzinfo), asof=nxt)
    assert rep.status == "ok"
    s = w.repos.board.stock_days(nxt)["000020"]
    assert s.label == "d60" and s.status == "이어감"
    assert s.extra["status_unknown"] is False


def test_백필_뒤에는_스칼라를_처음부터_다시_쌓는다() -> None:
    from tests.unit.engines.board.board_world import bar

    w = build_world()
    _run(w)
    old = date(2025, 1, 2)  # 이력 시작보다 앞(백필이 채운 과거)
    w.repos.market.upsert_daily_bars([bar("000020", old, 20000.0, "krx")], loaded_by="backfill")
    n = svc.rebuild_alltime(w.repos, D, now=NOW_DAILY, loaded_by="market.backfill", cfg=CFG)
    assert n == 6
    a = w.repos.board.alltime()["000020"]
    assert a.history_from == old and a.hi == 20000.0 and a.hi_date == old
    assert a.last_date == D and a.n_days == 301


def test_스냅에_종류가_없으면_유니버스_종류로_펀드를_뺀다() -> None:
    """검증에서 보탠 시험: 스냅 kind 가 비어도 유니버스 kind 로 ETF 를 거른다. 둘 다 없으면
    짐작하지 않고 '종류 모름' 수를 안내에 남긴다."""
    import dataclasses

    from tests.unit.engines.board.board_world import snap

    w = build_world()
    w.repos.market.upsert_snapshots(
        [
            dataclasses.replace(snap("000050", D, 10000.0, "kis"), kind=None),  # 유니버스는 etf
            dataclasses.replace(snap("000070", D, 10000.0, "kis"), kind=None),  # 어디에도 없음
        ],
        loaded_by="t",
    )
    _run(w)
    days = w.repos.board.stock_days(D)
    assert "000050" not in days
    meta = w.repos.board.artifact(D, "universe_meta")
    assert meta is not None
    assert meta.payload["funds_excluded"] == (
        "1종목 제외 / 종류 판정 6종목 · 종류 모름 1종목(펀드인지 확인 못 함)"
    )
