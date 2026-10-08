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
    assert days["000020"].label == "d120"
    labels = board.labels(D, "close")
    assert {c: lb.kind for c, lb in labels.items()}["000020"] == "d120"
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
    rep = _run(w, now=datetime(2026, 10, 7, 7, 0, tzinfo=NOW_DAILY.tzinfo), asof=nxt)
    assert rep.status == "ok"
    s = w.repos.board.stock_days(nxt)["000020"]
    assert s.label == "d120" and s.status == "이어감"
    assert s.extra["status_unknown"] is False


def test_정의_전환일에는_전일과_비교하지_않고_재실행해도_같다() -> None:
    """전일 보드가 예전 신고가 정의(d60·252봉)로 계산됐으면 신규/이어감을 판정하지 않는다(ADR 0017).

    ETF-Traker 검토에서 나온 버그(전환일 같은 날 재실행 시 '비교 불가' 표시가 풀려 '신규' 가
    거짓으로 찍힘)가 생기지 않는지 — 판단을 저장된 전일 산출에서 매번 다시 하므로 재실행해도 같아야
    한다."""
    import dataclasses

    from tests.unit.engines.board.board_world import add_krx_confirm, bar, snap

    w = build_world()
    _run(w)
    add_krx_confirm(w, close_000010=10500.0)
    old = w.repos.board.artifact_rows[(D, "newhigh")]
    payload = dict(old.payload)
    payload.update(
        priority=["hist", "w52", "d60"],
        thresholds={"proximity": {}, "lookback": {"d60": 60, "w52": 252}},
    )
    w.repos.board.artifact_rows[(D, "newhigh")] = dataclasses.replace(old, payload=payload)
    nxt = date(2026, 10, 7)
    w.repos.market.upsert_daily_bars([bar("000020", nxt, 10300.0, "kis")], loaded_by="t")
    w.repos.market.upsert_snapshots([snap("000020", nxt, 10300.0, "kis")], loaded_by="t")
    now = datetime(2026, 10, 7, 7, 0, tzinfo=NOW_DAILY.tzinfo)
    for _ in range(2):  # 같은 날 재실행 — 결과가 같아야 한다
        rep = _run(w, now=now, asof=nxt)
        assert rep.status == "ok" and rep.day is not None
        s = w.repos.board.stock_days(nxt)["000020"]
        assert s.label == "d120" and s.status is None
        assert s.extra["status_unknown"] is True
        assert any("예전 신고가 정의" in n for n in rep.day.notes)
    assert svc.newhigh_definition(payload) != svc.newhigh_definition(CFG)
    assert svc.newhigh_definition(w.repos.board.artifact(nxt, "newhigh").payload) == (  # type: ignore[union-attr]
        svc.newhigh_definition(CFG)
    )


def test_백필_뒤에는_스칼라를_처음부터_다시_쌓는다() -> None:
    from tests.unit.engines.board.board_world import bar

    w = build_world()
    _run(w)
    old = date(2025, 1, 2)  # 이력 시작보다 앞(백필이 채운 과거)
    w.repos.market.upsert_daily_bars([bar("000020", old, 20000.0, "krx")], loaded_by="backfill")
    n = svc.rebuild_alltime(w.repos, D, now=NOW_DAILY, loaded_by="market.backfill", cfg=CFG)
    assert n == 6
    a = w.repos.board.alltime()["000020"]
    assert a.hi == 20000.0 and a.hi_date == old  # 받은 일봉 전체로 다시 쌓는다
    assert a.last_date == D and a.n_days == 301
    # 한 종목만 들어온 옛 날짜는 '시장 일봉을 받은 날'이 아니다 — history_from 은 시장 기준(ADR
    # 0017)
    assert a.history_from == FIRST


def test_시장_전체를_백필하면_history_from_이_그만큼_과거로_간다() -> None:
    """KRX 백필은 날짜마다 전 종목을 받는다 — 빠짐없이 받은 첫날이 모든 종목의 history_from 이다.

    그 뒤에 상장한 종목은 첫 봉이 늦어도(상장 직후 거래정지) 상장 이후 전체를 가진 것이다."""
    from kbj.core.calendar import TradingCalendar
    from tests.unit.engines.board.board_world import CODES, bar

    w = build_world()
    _run(w)
    kr = TradingCalendar.default()
    old: list[date] = []
    d = FIRST
    for _ in range(5):
        d = kr.prev_trading_day(d)
        old.append(d)
    w.repos.market.upsert_daily_bars(
        [bar(c, x, 10000.0, "krx") for c in CODES for x in old], loaded_by="backfill"
    )
    svc.rebuild_alltime(w.repos, D, now=NOW_DAILY, loaded_by="market.backfill", cfg=CFG)
    at = w.repos.board.alltime()
    assert {a.history_from for a in at.values()} == {min(old)}
    # 하루라도 빠진 거래일(구멍)이 있으면 그 뒤부터다 — 구멍 너머를 '받았다'고 하지 않는다
    w2 = build_world()
    w2.repos.market.upsert_daily_bars(
        [bar(c, x, 10000.0, "krx") for c in CODES for x in old if x != old[2]], loaded_by="b"
    )
    svc.rebuild_alltime(w2.repos, D, now=NOW_DAILY, loaded_by="market.backfill", cfg=CFG)
    assert {a.history_from for a in w2.repos.board.alltime().values()} == {old[1]}


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
