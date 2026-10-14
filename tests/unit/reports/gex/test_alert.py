"""장중 GEX 레벨 알림 조건(kbj.reports.gex.alert — D-P7-17 [제안]). 합성 입력."""

from __future__ import annotations

from datetime import date, timedelta

from kbj.reports.gex.alert import AlertState, blocked_reason, evaluate
from kbj.reports.gex.lines import ALERT_EXCLUDE, LevelSnapshot, snapshot
from tests.unit.reports.gex.gex_world import at, levels

DAY = date(2026, 12, 10)
T0 = at(DAY, 9, 1)
ALL = ("flip_cross", "wall_move", "outside_1sigma")


def snap(minute: int, **kw: object) -> LevelSnapshot:
    s = snapshot(levels(T0 + timedelta(minutes=minute), **kw), exclude=ALERT_EXCLUDE)  # type: ignore[arg-type]
    assert s is not None
    return s


def test_세션_첫_사이클은_기준만_잡고_보내지_않는다() -> None:
    alerts, st = evaluate(None, snap(0, f=351.2, flip=350.0), ALL)
    assert alerts == []
    assert st.side == 1 and (st.call_wall, st.put_wall) == (360.0, 340.0)
    assert (st.band_lower, st.band_upper) == (347.2, 355.2) and st.outside == 0
    # 다른 세션(야간)으로 넘어가면 다시 기준 — 직전 주간 값과 비교하지 않는다
    night = snapshot(levels(at(DAY, 18, 1), session="night", trade_date=date(2026, 12, 11),
                            f=340.0, flip=350.0), exclude=ALERT_EXCLUDE)  # fmt: skip
    assert night is not None
    alerts, st2 = evaluate(st, night, ALL)
    assert alerts == [] and st2.session == "night" and st2.side == -1


def test_Flip_건넘_한_번_되돌아오면_또_한_번() -> None:
    _, st = evaluate(None, snap(0, f=351.2, flip=350.0), ALL)
    a1, st = evaluate(st, snap(1, f=351.0, flip=350.0), ALL)
    assert a1 == []
    a2, st = evaluate(st, snap(2, f=349.5, flip=350.0), ALL)
    assert [a.subject for a in a2] == ["flip_cross"]
    assert "Flip 350.00 아래로(직전 Flip 위)" in a2[0].text and "12/10 09:03 KST 주간" in a2[0].text
    a3, st = evaluate(st, snap(3, f=349.0, flip=350.0), ALL)
    assert a3 == []
    a4, _ = evaluate(st, snap(4, f=350.5, flip=350.0), ALL)
    assert [a.subject for a in a4] == ["flip_cross"] and "위로" in a4[0].text


def test_Flip_없음_같음은_건넘으로_세지_않는다() -> None:
    _, st = evaluate(None, snap(0, f=351.2, flip=350.0), ALL)
    a, st = evaluate(st, snap(1, f=351.2, flip=None), ALL)
    assert a == [] and st.side == 1  # 직전 쪽 그대로
    a, st = evaluate(st, snap(2, f=350.0, flip=350.0), ALL)
    assert a == [] and st.side == 1
    a, _ = evaluate(st, snap(3, f=349.0, flip=350.0), ALL)
    assert [x.subject for x in a] == ["flip_cross"]


def test_콜월_풋월_이동은_각각() -> None:
    _, st = evaluate(None, snap(0), ALL)
    a, st = evaluate(st, snap(1, call_wall=362.5), ALL)
    assert [x.subject for x in a] == ["wall_move:call"] and "360.00 → 362.50" in a[0].text
    a, st = evaluate(st, snap(2, call_wall=362.5, put_wall=337.5), ALL)
    assert [x.subject for x in a] == ["wall_move:put"]
    a, st = evaluate(st, snap(3, call_wall=None, put_wall=337.5), ALL)  # 값 없음 — 이동 아님
    assert a == [] and st.call_wall == 362.5


def test_세션_기준_1시그마_밖은_나갈_때_한_번() -> None:
    _, st = evaluate(None, snap(0, f=351.2, flip=340.0, sigma=4.0), ALL)  # 기준 347.2~355.2
    # 사이클마다 F 를 가운데 두고 다시 잰 ±1σ 가 아니라 세션 첫 범위와 비교한다
    a, st = evaluate(st, snap(1, f=355.5, flip=340.0, sigma=4.0), ALL)
    assert [x.subject for x in a] == ["outside_1sigma:up"]
    assert "상단 355.20" in a[0].text and "세션 기준 347.20~355.20" in a[0].text
    a, st = evaluate(st, snap(2, f=356.0, flip=340.0), ALL)
    assert a == []  # 계속 밖 — 다시 보내지 않는다
    a, st = evaluate(st, snap(3, f=351.0, flip=340.0), ALL)
    assert a == [] and st.outside == 0
    a, _ = evaluate(st, snap(4, f=347.0, flip=340.0), ALL)
    assert [x.subject for x in a] == ["outside_1sigma:down"]


def test_첫_사이클에_1시그마가_없으면_처음_본_범위가_기준() -> None:
    _, st = evaluate(None, snap(0, sigma=None), ALL)
    assert st.band_lower is None
    a, st = evaluate(st, snap(1, f=351.2, sigma=4.0), ALL)
    assert a == [] and (st.band_lower, st.band_upper) == (347.2, 355.2)


def test_설정에_없는_조건은_보내지_않고_같은_사이클은_다시_세지_않는다() -> None:
    _, st = evaluate(None, snap(0, f=351.2, flip=350.0), ALL)
    a, st2 = evaluate(st, snap(1, f=349.0, flip=350.0, call_wall=365.0), ("wall_move",))
    assert [x.subject for x in a] == ["wall_move:call"] and st2.side == -1
    again, st3 = evaluate(st2, snap(1, f=349.0, flip=350.0, call_wall=370.0), ALL)
    assert again == [] and st3 == st2


def test_막힘_사유() -> None:
    now = T0 + timedelta(seconds=30)
    stale = timedelta(minutes=3)
    assert blocked_reason(None, now, stale) == "레벨 산출 없음"
    assert blocked_reason(snap(0), now, stale) is None
    assert "지연" in (blocked_reason(snap(0), T0 + timedelta(minutes=4), stale) or "")
    est = snapshot(levels(T0, quality="estimated"), exclude=ALERT_EXCLUDE)
    assert blocked_reason(est, now, stale) is None  # estimated 는 보낸다(invalid·stale 만 막음)
    bad = snapshot(levels(T0, quality="invalid"), exclude=ALERT_EXCLUDE)
    assert blocked_reason(bad, now, stale) == "visible 레벨 없음(섀도·꺼짐)"
    sh = snapshot(levels(T0, flags={n: "shadow" for n in ("flip", "flip_distance", "call_wall",
                                                          "put_wall", "expected_move")}),
                  exclude=ALERT_EXCLUDE)  # fmt: skip
    assert blocked_reason(sh, now, stale) == "visible 레벨 없음(섀도·꺼짐)"


def test_상태_JSON_왕복과_모양이_다르면_새로() -> None:
    _, st = evaluate(None, snap(0), ALL)
    assert AlertState.loads(st.dumps()) == st
    assert AlertState.loads(st.dumps().encode()) == st
    for bad in (None, "", "[]", '{"v": 0}', '{"v": 1}', "{깨짐"):
        assert AlertState.loads(bad) is None
