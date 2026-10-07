"""일 예산 속성(kbj.data.budget): 임의 순서·여러 프로세스·KST 자정을 넘는 호출에서도 하루 상한을
넘지 않는다(설계 §4.4 "일 예산 초과 0").

- 여러 `DailyBudget` 인스턴스(서로 다른 프로세스)가 Redis 하나를 나눠 쓴다. 하루(KST)마다 받은 몫은
  상한 이하, 닫히기(`exhaust`) 전에는 정확히 min(시도, 상한)이고 닫힌 뒤에는 0이다.
- config/limits.yaml 의 실제 상한(KRX·DART·공공데이터포털 데이터셋별)마다 상한 바로 앞에서 정확히
  멈춘다 — 카운터를 상한 근처로 미리 올려 두고 본다(상한만큼 실제로 부르지 않는다).
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import fakeredis
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from kbj.data.budget import BudgetExhausted, DailyBudget, krx_budget
from kbj.data.limits import parse_limits

ROOT = Path(__file__).resolve().parents[2]
KST = ZoneInfo("Asia/Seoul")
T0 = datetime(2026, 10, 5, 22, 0, tzinfo=KST)  # 10-05 대체공휴일 밤 — 바로 자정을 넘는다
# 레포의 config/limits.yaml — 바깥 KBJ_* 환경(운영 VM 의 KBJ_CONFIG_DIR 등)·.env 와 무관하게
LIMITS = parse_limits(yaml.safe_load((ROOT / "config" / "limits.yaml").read_text(encoding="utf-8")))

# (앞 연산과의 간격(분), 어느 프로세스, 연산) — take 를 exhaust 보다 자주
Op = tuple[int, int, str]
ops = st.lists(
    st.tuples(
        st.integers(min_value=0, max_value=6 * 60),
        st.integers(min_value=0, max_value=2),
        st.sampled_from(["take", "take", "take", "take", "exhaust"]),
    ),
    max_size=60,
)

# limits.yaml 에서 일 상한이 있는 (출처, 상한) — 데이터셋별 상한(datago)도 하나의 예산으로 본다
CAPPED: list[tuple[str, int]] = sorted(
    (name, cap)
    for name, src in LIMITS.sources.items()
    for cap in (src.daily_cap, src.daily_cap_per_dataset)
    if cap is not None
)


def budget_for(r: fakeredis.FakeRedis, name: str, cap: int) -> DailyBudget:
    if name == "krx":
        return krx_budget(r, cap)  # GX 와 같은 키 krx:calls:<날짜>
    scope = "15100475" if name == "datago" else None  # 공공데이터포털은 데이터셋마다 따로 센다
    return DailyBudget(r, name, cap, scope=scope)


@settings(max_examples=60, deadline=None)
@given(st.integers(min_value=1, max_value=6), ops)
def test_no_kst_day_ever_exceeds_its_cap(cap: int, seq: list[Op]) -> None:
    server = fakeredis.FakeServer()
    budgets = [DailyBudget(fakeredis.FakeRedis(server=server), "dart", cap) for _ in range(3)]
    at = T0
    taken: Counter[date] = Counter()
    tried: Counter[date] = Counter()
    closed: set[date] = set()
    for gap_min, who, op in seq:
        at += timedelta(minutes=gap_min)
        day = at.astimezone(KST).date()
        b = budgets[who]
        if op == "exhaust":
            b.exhaust(at, "020 요청 제한 초과")
            closed.add(day)
            continue
        tried[day] += 1
        try:
            n = b.take(at)
        except BudgetExhausted:
            assert day in closed or taken[day] == cap
            continue
        assert day not in closed  # 닫힌 날에는 어느 프로세스도 받지 못한다
        taken[day] += 1
        assert n == taken[day]  # 돌려준 수 = 그날 모든 프로세스가 받은 수
    for day in tried:
        assert taken[day] <= cap
        if day not in closed:
            assert taken[day] == min(tried[day], cap)
        noon = datetime.combine(day, time(12), KST)
        assert all(b.used(noon) == taken[day] for b in budgets)


@settings(max_examples=60, deadline=None)
@given(
    st.sampled_from(CAPPED),
    st.integers(min_value=0, max_value=5),
    st.integers(min_value=0, max_value=8),
)
def test_configured_caps_stop_exactly_at_the_cap(
    source_cap: tuple[str, int], left: int, tries: int
) -> None:
    name, cap = source_cap
    r = fakeredis.FakeRedis()
    b = budget_for(r, name, cap)
    at = datetime(2026, 10, 6, 9, 0, tzinfo=KST)
    r.set(b.key(b.day_of(at)), cap - left)  # 그날 이미 cap - left 번 불렀다
    got = 0
    for _ in range(tries):
        try:
            b.take(at)
        except BudgetExhausted:
            continue
        got += 1
    assert got == min(left, tries)
    assert b.used(at) == cap - left + got <= cap


def test_every_source_with_a_daily_cap_is_covered() -> None:
    # 설계 §4.2 표: KRX 8,000 [확인 필요]·DART 18,000·공공데이터포털 데이터셋별 9,500
    assert dict(CAPPED) == {"datago": 9500, "dart": 18000, "krx": 8000}
