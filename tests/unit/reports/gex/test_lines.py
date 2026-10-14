"""GEX·진단 문장(kbj.reports.gex.lines — D-P7-16, §10 Q3). 합성 입력."""

from __future__ import annotations

from datetime import date, timedelta

from kbj.core.quality import Quality
from kbj.reports.gex.lines import (
    ALERT_EXCLUDE,
    command_reader,
    command_text,
    diag_lines,
    gex_lines,
    snapshot,
)
from kbj.store.repos.gex import MemoryGexRepo
from tests.unit.reports.gex.gex_world import at, fear_greed, levels, verdict

DAY = date(2026, 12, 10)
T = at(DAY, 14, 0)


def test_스냅샷은_가장_늦은_사이클_visible_값만() -> None:
    rows = levels(T - timedelta(minutes=1), f=1.0, flip=2.0) + levels(T, f=351.2, flip=350.0)
    s = snapshot(rows)
    assert s is not None and s.ts == T
    assert (s.price, s.flip, s.call_wall, s.put_wall, s.sigma) == (351.2, 350.0, 360.0, 340.0, 4.0)
    assert (s.lower, s.upper) == (347.2, 355.2)
    assert s.side == 1 and s.quality is Quality.OK and s.shadow == ()
    assert snapshot([]) is None


def test_섀도는_값을_싣지_않고_자리만() -> None:
    s = snapshot(levels(T, flags={"call_wall": "shadow", "put_wall": "shadow"}))
    assert s is not None and s.call_wall is None and s.put_wall is None
    assert s.shadow == ("call_wall", "put_wall")
    assert not any("콜월" in x for x in gex_lines(s))
    allshadow = {n: "shadow" for n in ("flip", "flip_distance", "call_wall", "put_wall",
                                       "expected_move")}  # fmt: skip
    s2 = snapshot(levels(T, flags=allshadow))
    assert s2 is not None and gex_lines(s2) == ["GEX 레벨 섀도 검증 중 — 싣지 않는다"]


def test_invalid_는_쓰지_않고_알림은_stale_도_뺀다() -> None:
    s = snapshot(levels(T, quality="invalid"))
    assert s is not None and not s.has_values
    stale = levels(T, quality="stale")
    brief = snapshot(stale)
    assert brief is not None and brief.flip == 350.0 and brief.quality is Quality.STALE
    alert = snapshot(stale, exclude=ALERT_EXCLUDE)
    assert alert is not None and alert.flip is None and not alert.has_values


def test_현재가가_없으면_Flip_거리에서_되돌린다_estimated() -> None:
    rows = levels(T, f=400.0, flip=396.0)
    for r in rows:  # f_ref·F 가 빠진 사이클(엔진 detail 누락)
        r.detail.pop("f_ref", None)
        r.detail.pop("F", None)
    s = snapshot(rows)
    assert s is not None and s.price is not None and abs(s.price - 400.0) < 1e-9
    assert s.quality is Quality.ESTIMATED and s.reasons


def test_GEX_줄_모양() -> None:
    s = snapshot(levels(T, f=351.2, flip=350.0))
    assert s is not None
    lines = gex_lines(s)
    assert lines[0] == "F 351.20 · Flip 350.00(Flip 위 +0.34%)"
    assert lines[1] == "콜월 360.00 · 풋월 340.00"
    assert lines[2] == "±1σ 347.20~355.20(±4.00pt, 달력 기준)"
    none = snapshot(levels(T, flip=None))
    assert none is not None and gex_lines(none)[0] == "F 351.20 · Flip 없음(범위 안 부호 같음)"
    est = snapshot(levels(T, quality="estimated"))
    assert est is not None and gex_lines(est)[-1] == "<i>품질 estimated</i>"


def test_진단_줄_판정_근거_이벤트_공포탐욕_변화() -> None:
    v = verdict(DAY, "risk_on", 3, events=("expiry", "mpc"))
    lines = diag_lines(v, fear_greed(DAY, "62.5"), fear_greed(date(2026, 12, 9), "50.0"))
    assert (
        lines[0]
        == "종합 판정: 위험 선호(점수 +3) — 근거 +공포탐욕 · +시장폭 · +GEX 체제 <i>(잠정)</i>"
    )
    assert lines[1] == "이벤트 주의: 만기·금통위"
    assert lines[2] == "공포탐욕 62.5 탐욕(전일 50.0, +12.5, 중립에서)"
    hold = verdict(DAY, "hold", None, parts={}, q=Quality.ESTIMATED)
    assert diag_lines(hold, fear_greed(DAY, None), None) == [
        "종합 판정: 보류(성분 부족) <i>(잠정)</i>",
        "공포탐욕: 값 없음(성분 부족)",
    ]
    assert diag_lines(None, None) == []


def test_명령_요약_한_덩어리와_없음() -> None:
    s = snapshot(levels(T))
    txt = command_text(s, T + timedelta(seconds=30))
    assert txt.startswith("📐 <b>GEX(all)</b> · 12/10 14:00 KST 주간 사이클\nF 351.20")
    assert "분 전" not in txt
    assert "마지막 사이클 10분 전" in command_text(s, T + timedelta(minutes=10))
    assert "산출 없음" in command_text(None, T)


def test_명령_읽기_함수는_부를_때마다_저장소를_읽는다() -> None:
    repo = MemoryGexRepo()
    clock = [T]
    read = command_reader(repo, lambda: clock[0])
    assert "산출 없음" in read()
    repo.write_levels(levels(T))
    repo.write_levels(levels(T + timedelta(minutes=5), f=1.0))  # 아직 오지 않은 시각 — 안 씀
    assert "F 351.20" in read()
    clock[0] = T + timedelta(minutes=5)
    assert "F 1.00" in read()
