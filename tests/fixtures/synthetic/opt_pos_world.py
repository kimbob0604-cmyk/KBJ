"""합성 옵션 포지션 세계 — 행사가별 투자자 추정 포지션(metrics §34, ADR 0044) 시험 재료.

**전부 합성**이다(KIS·KRX 원자료 아님 — 공개 레포 규칙). 가격·체결은 손으로 정해 배분 결과를
손계산할 수 있게 했다(`HAND_*`). 여러 날 세계(`world_rows`)는 고정 시드로 만든다 — API·웹 시험용.

손계산 세계(2026-11-03 화, 월물 '202611' — 최종거래일 11-12, 콜, 외국인):

| 시각(슬롯) | 시세(직전 1분) | 누적 매수 수량·대금(백만원) | 누적 매도 | 분 평균단가 → 배분 |
|---|---|---|---|---|
| 09:00:30 | 850=12·860=7·870=4·880=2·890=1 | | | |
| 09:01 | | 100·150 | 30·30 | 매수 p=6 → 860 66.667·870 33.333 / 매도 p=4 → 870 20·880 10 |
| 09:02:10 | 870=5 | | | (09:02 슬롯 빠짐) |
| 09:03 | | 160·225 | 70·45 | 매수 p=5 → 870 40·860 20 / 매도 p=1.5 → 880·890 같은 거리 1:1 20·20 |
| 09:04 | | **150**·225(역행) | 70·45 | 매수 버림(기준 160 유지) · 매도 0 |
| 09:05 | | 200·345 | 70·45 | 매수 p=12 → 850 26.667 · 860 13.333 |

순 = 850 +26.667 · 860 +100 · 870 +53.333 · 880 −30 · 890 −20. 빠진 분 = 09:00·09:02 → 2.
"""

from __future__ import annotations

import random
from datetime import date, datetime

from kbj.core.calendar import TradingCalendar
from kbj.core.time import KST
from kbj.engines.gex.option_positions import Cp, DayStat, FlowPoint, Quote, StrikeDay

__all__ = [
    "HAND_DAY",
    "HAND_NET",
    "HAND_POINTS",
    "HAND_QUOTES",
    "WORLD_DAYS",
    "WORLD_SEED",
    "at",
    "world_rows",
]

HAND_DAY = date(2026, 11, 3)
MONTHLY = ("", "202611")


def at(h: int, m: int, s: int = 0, d: date = HAND_DAY) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=KST)


def _q(t: datetime, k: float, last: float, cp: Cp = "C") -> Quote:
    return Quote(t, MONTHLY[0], MONTHLY[1], k, cp, last)


HAND_QUOTES: tuple[Quote, ...] = (
    _q(at(9, 0, 30), 850, 12.0),
    _q(at(9, 0, 30), 860, 7.0),
    _q(at(9, 0, 30), 870, 4.0),
    _q(at(9, 0, 30), 880, 2.0),
    _q(at(9, 0, 30), 890, 1.0),
    _q(at(9, 2, 10), 870, 5.0),
    _q(at(9, 0, 30), 870, 9.9, cp="P"),  # 풋 시세는 콜 배분에 섞이지 않는다
)
HAND_POINTS: tuple[FlowPoint, ...] = (
    FlowPoint(at(9, 1), 100, 150, 30, 30),
    FlowPoint(at(9, 3), 160, 225, 70, 45),
    FlowPoint(at(9, 4), 150, 225, 70, 45),
    FlowPoint(at(9, 5), 200, 345, 70, 45),
)
HAND_NET: dict[float, float] = {850: 80 / 3, 860: 100.0, 870: 160 / 3, 880: -30.0, 890: -20.0}


def _close(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 15, 55, tzinfo=KST)


# ── 여러 날 세계(저장 행 꼴 — API·웹 시험) ──────────────────────────────────────────────

WORLD_SEED = 20261103
WORLD_DAYS = (date(2026, 10, 28), date(2026, 10, 29), date(2026, 10, 30), date(2026, 11, 2),
              date(2026, 11, 3))  # fmt: skip
STRIKES = [800.0 + 12.5 * i for i in range(17)]  # 800 ~ 1000


def world_rows(
    cal: TradingCalendar,
    investor: str = "frgn",
) -> tuple[list[StrikeDay], list[DayStat], dict[tuple[str, str, float, str], int]]:
    """고정 시드 5거래일 — 월물 202611·위클리(목) 하나. 콜 950·975 순매도(−1,000/일)가 4일 쌓였다가
    마지막 날 +4,000 으로 청산(cleared), 900 콜 매도는 남고(−1,500 ×3·−200 ×2 → 콜 벽), 풋 850
    순매도(−800/일 → 풋 벽), 1000 콜은 +60/일인데 KRX OI 100 → invalid. 선물 −3,000 ×4·+670.
    반환 = (행사가 하루 행, 묶음 하루 요약, 마지막 날 KRX OI)."""
    rnd = random.Random(f"{WORLD_SEED}:{investor}")  # noqa: S311 — 합성 데이터(보안 무관)
    rows: list[StrikeDay] = []
    stats: list[DayStat] = []
    weekly = ("WKI", "261101")  # 11월 첫째 목요일(YYMM + n번째) 11-05
    for i, d in enumerate(WORLD_DAYS):
        last = i == len(WORLD_DAYS) - 1
        for k in STRIKES:
            delta_c = max(0.02, min(0.98, 0.5 - (k - 900) / 250))
            c = rnd.randint(-40, 40)
            if k in (950.0, 975.0):
                c = 4000 if last else -1000
            if k == 900.0:
                c = -1500 if i < 3 else -200
            if k == 1000.0:
                c = 60
            p = rnd.randint(-40, 40)
            if k == 850.0:
                p = -800
            legs: tuple[tuple[Cp, int, float], ...] = (("C", c, delta_c), ("P", p, delta_c - 1))
            for cp, net, delta in legs:
                buy = float(max(net, 0) + 50)
                rows.append(
                    StrikeDay(d, "", "202611", k, cp, buy, buy - net,
                              close=round(5 + abs(900 - k) / 20, 2), board_oi=50_000,
                              delta=round(delta, 4))
                )  # fmt: skip
            if d >= date(2026, 11, 2):  # 위클리는 11-02 부터 상장분만
                rows.append(StrikeDay(d, weekly[0], weekly[1], k, "C", 30.0, 10.0,
                                      board_oi=8_000, delta=round(delta_c, 4)))  # fmt: skip
        for mkt, sec in (("K2I", "OC01"), ("K2I", "OP01"), ("WKI", "OC04"), ("WKI", "OP04")):
            stats.append(DayStat(d, mkt, sec, minutes=416, gap_minutes=0, unallocated=0.0,
                                 last_ts=_close(d)))  # fmt: skip
        stats.append(
            DayStat(
                d,
                "K2I",
                "F001",
                minutes=416,
                gap_minutes=0,
                unallocated=0.0,
                futures_net=(-3000 if i < 4 else 670),
                fut_price=880.0 + i,
                last_ts=_close(d),
            )
        )
    oi: dict[tuple[str, str, float, str], int] = {
        ("", "202611", k, cp): 20_000 for k in STRIKES for cp in ("C", "P")
    }
    oi[("", "202611", 1000.0, "C")] = 100  # 1000 콜 누적 +300 > OI → invalid
    assert all(cal.is_trading_day(d) for d in WORLD_DAYS)
    return rows, stats, oi
