"""`board.confirm` — 같은 날 KIS(잠정) → KRX(확정) 다시 계산(docs/p3_design.md §8.1·§3.5, D-P3-8).

바뀐 라벨 목록이 기대와 같고, 확정 보드는 quality ok, 스칼라는 확정 일봉으로 다시 쓴다.
"""

from __future__ import annotations

from kbj.core.quality import Quality
from kbj.engines.board import newhigh as nh
from kbj.services.engine import board as svc
from tests.unit.engines.board.board_world import (
    CFG,
    KNOW,
    NOW_CONFIRM,
    NOW_DAILY,
    D,
    World,
    add_krx_confirm,
    build_world,
)


def _daily(w: World) -> svc.BoardRunReport:
    return svc.run_board(
        w.repos, D, now=NOW_DAILY, loaded_by="board.daily", cfg=CFG, knowledge=KNOW
    )


def _confirm(w: World) -> svc.BoardRunReport:
    return svc.run_board(
        w.repos, D, now=NOW_CONFIRM, loaded_by="board.confirm", mode="confirm", cfg=CFG,
        knowledge=KNOW,
    )  # fmt: skip


def test_확정하면_바뀐_라벨_목록이_남는다() -> None:
    w = build_world()
    _daily(w)
    before = w.repos.board.labels(D, "close")["000010"].kind
    add_krx_confirm(w, close_000010=9900.0)  # 확정 종가는 신고가 아래
    rep = _confirm(w)
    assert rep.status == "ok"
    assert rep.quality is Quality.OK
    ch = rep.changes
    assert ch["removed"] == [["close", "000010", before]]
    assert ch["added"] == [] and ch["changed"] == []
    assert ch["n_changed"] == 1
    assert ch["previous_quality"] == "estimated"
    nhj = w.repos.board.artifact(D, "newhigh")
    assert nhj is not None and nhj.quality is Quality.OK
    assert nhj.payload["confirm"]["n_changed"] == 1
    assert "000010" not in w.repos.board.labels(D, "close")
    assert rep.detail()["n_changed"] == 1
    s = w.repos.board.stock_days(D)["000010"]
    assert s.label is None and s.quality is Quality.OK and s.source == "krx"


def test_확정하면_스칼라의_그날_몫을_확정값으로_다시_쓴다() -> None:
    w = build_world()
    _daily(w)
    assert w.repos.board.alltime()["000010"].hi == 10500.0  # KIS 잠정 고가
    add_krx_confirm(w, close_000010=9900.0)
    _confirm(w)
    a = w.repos.board.alltime()["000010"]
    assert a.hi == 10060.0  # 직전 최고로 돌아간다(확정 고가 9,900 < 10,060)
    assert a.hi_date is None  # 그 최고의 날짜는 스칼라가 모른다 — 지어내지 않는다
    assert a.prev_hi == 10060.0 and a.last_date == D
    assert a.source == "krx" and a.quality is Quality.OK


def test_값이_같으면_바뀐_라벨이_없다() -> None:
    w = build_world()
    _daily(w)
    add_krx_confirm(w, close_000010=10500.0)
    rep = _confirm(w)
    assert rep.changes["n_changed"] == 0
    assert rep.quality is Quality.OK


def test_KRX_확정이_없으면_쓰지_않고_not_ready() -> None:
    w = build_world()
    _daily(w)
    before = {k: v.payload for k, v in w.repos.board.artifact_rows.items()}
    rep = _confirm(w)
    assert rep.status == "not_ready"
    assert {k: v.payload for k, v in w.repos.board.artifact_rows.items()} == before


def test_확정_재실행은_멱등이다() -> None:
    w = build_world()
    _daily(w)
    add_krx_confirm(w)
    _confirm(w)
    snap1 = (
        dict(w.repos.board.label_rows),
        {c: s.row for c, s in w.repos.board.alltime_rows.items()},
    )
    rep = _confirm(w)
    snap2 = (
        dict(w.repos.board.label_rows),
        {c: s.row for c, s in w.repos.board.alltime_rows.items()},
    )
    assert snap1 == snap2
    assert rep.changes["n_changed"] == 0  # 이미 확정된 라벨과 같다
    assert rep.changes["previous_quality"] == "ok"


def test_restate_last_day_는_그날_몫만_바꾼다() -> None:
    at = dict(hi=120.0, hi_date="2026-10-06", cl=118.0, cl_date="2026-10-06",
              prev_hi=110.0, prev_cl=109.0, last_date="2026-10-06", n_days=10)  # fmt: skip
    up = nh.restate_last_day(at, dict(asof="2026-10-06", high=125.0, close=121.0))
    assert (up["hi"], up["hi_date"], up["cl"], up["cl_date"]) == (
        125.0,
        "2026-10-06",
        121.0,
        "2026-10-06",
    )
    down = nh.restate_last_day(at, dict(asof="2026-10-06", high=100.0, close=100.0))
    assert (down["hi"], down["hi_date"], down["cl"], down["cl_date"]) == (110.0, None, 109.0, None)
    assert down["prev_hi"] == 110.0 and down["n_days"] == 10
    other = nh.restate_last_day(at, dict(asof="2026-10-05", high=999.0, close=999.0))
    assert other == at  # 다른 날이면 그대로
    older = dict(at, hi=130.0, hi_date="2026-01-02", cl=129.0, cl_date="2026-01-02",
                 prev_hi=130.0, prev_cl=129.0)  # fmt: skip
    same = nh.restate_last_day(older, dict(asof="2026-10-06", high=100.0, close=100.0))
    assert (same["hi"], same["hi_date"]) == (130.0, "2026-01-02")  # 과거 최고의 날짜는 그대로


def test_다음_날_보드_뒤의_늦은_확정은_보드를_덮지_않고_실패한다() -> None:
    """검증에서 찾은 경우: 다음 거래일 board.daily 가 스칼라를 굴린 뒤 D 를 확정하면 D 의
    '직전일까지 최고가' 를 복원할 수 없다 — 역사적 신고가가 전부 '복원 불가' 로 빠진 보드로 멀쩡한
    보드를 덮으면 안 된다(지어내지도, 조용히 빼지도 않는다 — 실패로 남긴다)."""
    from datetime import UTC, date, datetime

    from tests.unit.engines.board.board_world import CODES, bar, snap

    w = build_world()
    _daily(w)
    nxt = date(2026, 10, 7)
    w.repos.market.upsert_daily_bars([bar(c, nxt, 10000.0, "kis") for c in CODES], loaded_by="t")
    w.repos.market.upsert_snapshots(
        [snap(c, nxt, 10000.0, "kis") for c in CODES if c not in ("000050", "000060")],
        loaded_by="t",
    )
    nxt_rep = svc.run_board(
        w.repos, nxt, now=datetime(2026, 10, 7, 7, 20, tzinfo=UTC), loaded_by="board.daily",
        cfg=CFG, knowledge=KNOW,
    )  # fmt: skip
    assert nxt_rep.status == "ok"
    labels_before = dict(w.repos.board.label_rows)
    at_before = {c: s.row for c, s in w.repos.board.alltime_rows.items()}
    add_krx_confirm(w, close_000010=10500.0)
    rep = _confirm(w)
    assert rep.status == "failed"
    assert rep.reason and "복원할 수 없어" in rep.reason
    assert dict(w.repos.board.label_rows) == labels_before
    assert {c: s.row for c, s in w.repos.board.alltime_rows.items()} == at_before
    nhj = w.repos.board.artifact(D, "newhigh")
    assert nhj is not None and nhj.quality is Quality.ESTIMATED  # 잠정 보드가 그대로 남는다


def test_같은_날_재실행은_바뀐_일봉으로_그날_몫을_다시_쓴다() -> None:
    """같은 as_of 를 다시 돌릴 때 그날 일봉이 바뀌었으면(재수집) 스칼라의 그날 몫도 따라간다."""
    from tests.unit.engines.board.board_world import bar

    w = build_world()
    _daily(w)
    assert w.repos.board.alltime()["000010"].hi == 10500.0
    w.repos.market.upsert_daily_bars([bar("000010", D, 10700.0, "kis")], loaded_by="t")
    rep = _daily(w)
    assert rep.status == "ok"
    a = w.repos.board.alltime()["000010"]
    assert (a.hi, a.hi_date, a.prev_hi, a.last_date) == (10700.0, D, 10060.0, D)
