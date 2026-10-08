"""엔진 = 단순 루프 — 신고가 3축 독립 오라클 속성 시험(docs/p3_design.md §4.1 6단계·§8.1, ADR 0017).

hypothesis 로 만든 **시장 거래일 달력**(평일 + 임의 휴장일)과 그 위의 종목 일봉(임의 상장일·거래정지
구간·같은 값이 자주 나오는 가격·가끔 계단)에서, `kbj.engines.board.newhigh.evaluate` 의 창별 기준
최고가·갱신 여부가 `tests/oracles/newhigh_loop`(엔진을 쓰지 않는 날짜 루프)와 **같은지** 본다.
(처음 판은 '엔진 라벨 ⊆ 직전 n봉 루프 라벨' 이었다 — 지금은 같음을 본다. 더 세다.)

신고가 정의를 사용자 요청(2026-10-08)으로 바꾸면서 골든(tests/golden/board)은 새 엔진으로 다시
캡처했다 — 골든은 이제 '바뀌지 않았다'만 지키고, 정의대로 계산하는지는 이 시험이 지킨다.

- 120일(d120): 판정일 직전 120 시장 거래일 — 거래정지로 창이 늘어나지 않는다.
- 52주(w52): 판정일 − 364일 ≤ 봉 날짜 < 판정일.
- 창을 못 채우면 None. 수정주가 이상 지점(계단)이 창 안이면 엔진만 None 이다(엔진이 더하는 조건).
- 역사적(hist): 상장일부터의 일봉으로 스칼라를 쌓아(`roll_alltime` → `hist_ref_for`) 판정한 값이
  '판정일 전 전체 최고가 초과' 루프와 같다(이력 60거래일 이상·계단 없음일 때).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from kbj.engines.board import newhigh as nh
from kbj.engines.board.config import BoardConfig
from tests.oracles.newhigh_loop import hist_by_loop, labels_by_loop, window_by_loop

CFG = BoardConfig.load()
WINDOWS: dict[str, tuple[str, int]] = nh.lookbacks(CFG)
D0 = date(2024, 1, 1)

World = tuple[list[str], list[dict[str, Any]]]


@st.composite
def world(draw: st.DrawFn) -> World:
    """(시장 거래일, 종목 일봉) — 마지막 시장 거래일이 판정일이고 종목은 그날 봉이 있다."""
    # 달력일 수 — 절반쯤은 52주 창을 채울 만큼 길게
    n_cal = draw(st.one_of(st.integers(min_value=3, max_value=560), st.integers(370, 560)))
    weekdays = [D0 + timedelta(days=i) for i in range(n_cal)]
    weekdays = [d for d in weekdays if d.weekday() < 5]
    hol = set(draw(st.lists(st.integers(0, len(weekdays) - 2), max_size=12)))
    market = [d.isoformat() for i, d in enumerate(weekdays) if i not in hol]
    if len(market) < 2:
        market = [weekdays[0].isoformat(), weekdays[-1].isoformat()]
    n = len(market)
    listed = draw(st.integers(0, n - 2))  # 상장 첫 거래일(인덱스)
    gaps: set[int] = set()
    for _ in range(draw(st.integers(0, 2))):  # 거래정지 구간(첫 봉·판정일은 남긴다)
        a = draw(st.integers(listed + 1, n - 1))
        ln = draw(st.integers(1, 40))
        gaps |= set(range(a, min(a + ln, n - 1)))
    idx = [i for i in range(listed, n) if i not in gaps]
    jumps = set(draw(st.lists(st.sampled_from(idx), max_size=1)))
    # 창 경계 바로 안·밖에 하루짜리 고점을 심는다(120·121번째 이전 거래일, 364·365일 전 근처) —
    # 경계를 하루 틀리게 잡은 구현이 걸리게
    asof_d = date.fromisoformat(market[-1])
    edge = {n - 1 - k for k in (119, 120, 121) if n - 1 - k >= 0}
    edge |= {i for i, d in enumerate(market) if 362 <= (asof_d - date.fromisoformat(d)).days <= 366}
    spikes = set(draw(st.lists(st.sampled_from(sorted(edge)), max_size=2))) if edge else set()
    px = draw(st.sampled_from([1000.0, 5000.0, 20000.0]))
    rows: list[dict[str, Any]] = []
    for i in idx:
        px = max(px * (1 + draw(st.sampled_from([-0.02, -0.01, 0.0, 0.0, 0.01, 0.02]))), 100.0)
        if i in jumps:
            px *= draw(st.sampled_from([0.2, 2.0]))  # 계단(분할 미반영)
        close = round(
            px * (1.25 if i in spikes else 1.0), -1
        )  # 10원 단위 — 같은 값(> 경계)이 자주 나온다
        high = close + draw(st.sampled_from([0.0, 0.0, 10.0, 20.0]))
        rows.append(
            dict(asof=market[i], open=close, high=high, low=close, close=close, volume=1000.0)
        )
    return market, rows


@settings(max_examples=250, deadline=None)
@given(w=world(), cleared=st.booleans())
def test_엔진의_창별_기준과_갱신은_루프와_같다(w: World, cleared: bool) -> None:
    market, rows = w
    asof = rows[-1]["asof"]
    ev = nh.evaluate(rows, asof, CFG, split_cleared=cleared, calendar=market)
    assert ev is not None
    loop = labels_by_loop(rows, asof, WINDOWS, market)
    floor: int = ev["split_floor"]
    for basis in nh.BASES:
        b = ev["basis"][basis]
        for kind, (unit, n) in WINDOWS.items():
            want = loop[basis][kind]
            ref, hit = b["refs"][kind], b["hit"][kind]
            if ref is None and want["ref"] is not None:
                # 엔진만 못 판정한 것은 창이 수정주가 이상 지점 전 구간을 써야 채워질 때뿐이다
                bound = window_by_loop(market, asof, unit, n)
                assert floor > 0 and bound is not None, (basis, kind, asof)
                assert rows[floor]["asof"] > bound[1], (basis, kind, asof)
                assert hit is False
                continue
            assert ref == want["ref"], (basis, kind, asof, ref, want)
            assert hit is bool(want["hit"]), (basis, kind, asof)
        # 라벨은 갱신한 것 중 최상위(우선순위 — 상위가 하위를 포함)
        on = [k for k in CFG["newhigh"]["priority"] if b["hit"][k]]
        assert b["label"] == (on[0] if on else None)


@settings(max_examples=150, deadline=None)
@given(w=world())
def test_역사적_신고가는_상장_이후_전체_루프와_같다(w: World) -> None:
    market, rows = w
    asof = rows[-1]["asof"]
    at = nh.roll_alltime(None, rows, CFG)
    ref, why = nh.hist_ref_for(at, asof)
    ev = nh.evaluate(rows, asof, CFG, hist_ref=ref, hist_days=at["n_days"], calendar=market)
    assert ev is not None
    want = hist_by_loop(rows, asof)
    min_h = CFG["integrity"]["min_history_days"]["hist"]
    for basis in nh.BASES:
        got = ev["basis"][basis]["hit"]["hist"]
        if ev["suspect"] or at["n_days"] < min_h or ref is None:
            assert got is False  # 판정하지 않음(계단·이력 부족·직전 최고가 미확보)
            assert ref is not None or why
            continue
        assert got is bool(want[basis]), (basis, asof)


def test_오라클은_정의대로_센다() -> None:
    # 평일 달력(휴장 없음) — 120거래일 창은 판정일 전 120번째 거래일부터, 52주는 364일 전부터
    days = [D0 + timedelta(days=i) for i in range(600)]
    market = [d.isoformat() for d in days if d.weekday() < 5]
    asof = market[-1]
    rows: list[dict[str, Any]] = [dict(asof=d, high=100.0, close=100.0) for d in market[:-1]]
    rows.append(dict(asof=asof, high=101.0, close=101.0))
    got = labels_by_loop(rows, asof, WINDOWS, market)
    assert got["close"]["d120"] == {"ref": 100.0, "hit": True}
    assert got["close"]["w52"] == {"ref": 100.0, "hit": True}
    # 같은 값은 신고가가 아니다(엄격 > — ADR 0017, ET 원본 그대로)
    rows[-1] = dict(asof=asof, high=100.0, close=100.0)
    assert labels_by_loop(rows, asof, WINDOWS, market)["close"]["d120"]["hit"] is False
    # 120번째 이전 거래일은 창 안, 121번째는 밖
    assert window_by_loop(market, asof, "trading", 120) == (market[-121], market[-121])
    # 52주 창의 시작은 364일 전(그날이 주말이면 창 안 첫 거래일이 덮기 기준)
    start = (date.fromisoformat(asof) - timedelta(days=364)).isoformat()
    w = window_by_loop(market, asof, "calendar", 364)
    assert w is not None and w[0] == start and w[1] == min(d for d in market if d >= start)
    # 상장 119거래일(+판정일)이면 120일 창을 못 채우고, 120거래일이면 채운다
    assert labels_by_loop(rows[-120:], asof, WINDOWS, market)["close"]["d120"]["ref"] is None
    assert labels_by_loop(rows[-121:], asof, WINDOWS, market)["close"]["d120"]["ref"] == 100.0
