"""장중 투자자 1분 수집의 실패 변형(F2 §5, docs/p5_design.md §1.8-2 — 웨이브 3 WRAP).

`test_one_day_p5` 는 정상 하루에 정규장 391분 × 켠 조합(K2I 3) 빈 분 0 을 본다. 여기서는 짧은 창
(2026-11-17 08:50 ~ 10:20)에서 09:30·09:31·09:32 세 분 동안 가짜 KIS 가 1분 투자자 TR
(`FHPTJ04030000`)에 500 을 준다(재시도 15초 뒤도 같은 창 안 — 등록부 retry max 1). 단언:
- 그 세 분의 `flows.market_minute` 실행만 실패하고(사유가 남는다) 나머지는 ok.
- 빈 분 = 실패한 분: 조합마다 09:00~10:19 사이 비어 있는 분이 정확히 그 세 분이다(0 으로 채우거나
  앞 값을 끌어오지 않는다 — 화면은 끊긴 선, metrics §21).
- KIS 어떤 1초에도 ≤ 4, 발급은 auth 만.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

import pytest

from kbj.data.private.kis.investors_minute import TR_MARKET_MINUTE
from tests.sim.conftest import KST, at
from tests.sim.harness import SimDay, SimOptions

pytestmark = pytest.mark.sim

DAY = date(2026, 11, 17)
PAIRS = (("K2I", "F001"), ("K2I", "OC01"), ("K2I", "OP01"))
BROKEN = ("09:30", "09:31", "09:32")


def _break(sim: SimDay) -> None:
    sim.kis.inject(
        "HTTP500",
        10_000,
        start=at(11, 17, 9, 30),
        until=at(11, 17, 9, 33),
        tr_id=TR_MARKET_MINUTE.tr_id,
    )


@pytest.fixture(scope="module")
def broken() -> Iterator[SimDay]:
    sim = SimDay(
        SimOptions(
            start=at(11, 17, 8, 50), hours=1.5, p5=True, ws=False, legacy_calls=False,
            prepare=(_break,),
        )
    )  # fmt: skip
    try:
        yield sim.run()
    finally:
        sim.close()


def test_only_the_broken_minutes_fail(broken: SimDay) -> None:
    runs = {a[-5:]: r for (j, a), r in broken.final_runs().items() if j == "flows.market_minute"}
    failed = sorted(t for t, r in runs.items() if r.status != "ok")
    assert failed == list(BROKEN)
    for t in BROKEN:
        assert runs[t].detail.get("reason"), t  # 사유가 남는다(삼키지 않는다)
    assert len(runs) == 80  # 09:00~10:19 매분(창 끝 10:20 은 빼고)


def test_empty_minutes_equal_failed_minutes(broken: SimDay) -> None:
    rows = broken.repos.flows.investor_minutes(DAY, list(PAIRS))
    want = []
    t = at(11, 17, 9)
    while t < at(11, 17, 10, 20):
        want.append(t.astimezone(KST).strftime("%H:%M"))
        t += timedelta(minutes=1)
    for pair in PAIRS:
        have = {
            m.ts.astimezone(KST).strftime("%H:%M")
            for m in rows
            if (m.market_code, m.sector_code) == pair
        }
        assert sorted(set(want) - have) == list(BROKEN), pair


def test_kis_limits_hold(broken: SimDay) -> None:
    assert broken.kis.max_in_window(1.0) <= 4
    assert {c.consumer for c in broken.kis.posts()} == {"auth"}
