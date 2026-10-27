"""합성 선물 OI 세계 `oi_world` — 선물 미결제약정 패널(metrics §33, ADR 0043) 시험·웹 픽스처용.

KRX·KIS 원자료와 그것으로 만든 픽스처는 공개 레포에 넣지 않는다 — **전부 합성**(고정 시드
`random.Random(f"{seed}:{이름}")`). 종목코드는 가상(`A0` 접두 합성 코드).

- 가짜 시계 `CLOCK` = 2026-11-04(수) 18:00 KST — 18시 정리 시각. KRX 확정은 11-03 까지(11-04 분은
  다음 날 08:05), 11-04 는 선물 전광판 잠정(15:44 스냅)·투자자 15:40 슬롯(estimated).
- KRX 정규 행: 2026-08-03 ~ 11-03 거래일(캘린더 정본), 분기 월물 넷(9월물 만기 2026-09-10 — 만기일
  다음 날 2027-09 물 신규 상장). 만기 전 5거래일 동안 근월물 OI 가 다음 월물로 옮겨 간다(롤오버).
- 투자자(investor_flow 정규장 마지막 행): 날마다 15:40 슬롯 하나 — K2I F001(선물)·OC01/OP01,
  WKM OC05/OP05, WKI OC04/OP04 × 외국인·기관·개인. 외국인 선물은 마지막 3거래일 연속 매수(10-26
  사용자 예시와 같은 꼴 — 값은 합성).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from functools import cache
from typing import Any, Final

from kbj.core.calendar import TradingCalendar, monthly_expiry
from kbj.core.gex.records import FuturesRecord, InvestorRecord
from kbj.core.time import KST

__all__ = ["CLOCK", "FIRST", "LAST_CONFIRMED", "SEED", "TODAY", "OiWorld", "build_oi_world"]

SEED: Final = 20261104
CLOCK: Final = datetime(2026, 11, 4, 18, 0, tzinfo=KST)
TODAY: Final = CLOCK.date()
LAST_CONFIRMED: Final = date(2026, 11, 3)
FIRST: Final = date(2026, 8, 3)
ROLL_DAYS: Final = 5
FLOW_SLOT: Final = time(15, 40)  # flows.market_minute 마지막 슬롯(현물 마감 + 10분)
BOARD_AT: Final = time(15, 44)
COMBOS: Final = (
    ("K2I", "F001"), ("K2I", "OC01"), ("K2I", "OP01"),
    ("WKM", "OC05"), ("WKM", "OP05"), ("WKI", "OC04"), ("WKI", "OP04"),
)  # fmt: skip
# 마지막 3거래일 외국인 선물 순매수(계약 — 합성)
FOREIGN_TAIL: Final = {date(2026, 10, 30): -1_200, date(2026, 11, 2): 2_100,
                       date(2026, 11, 3): 3_451, date(2026, 11, 4): 1_800}  # fmt: skip


def _rng(seed: int, *parts: object) -> random.Random:
    return random.Random(":".join(str(p) for p in (seed, *parts)))  # noqa: S311 — 합성 데이터


def _utc(local: datetime) -> datetime:
    return local.astimezone(UTC)


def _q2(v: float) -> Decimal:
    return Decimal(str(round(v, 2)))


def _quarter_expiries(start: date, n: int) -> list[str]:
    """start 이후(그 달 포함) 분기 월물 n 개 'YYYYMM'."""
    out: list[str] = []
    y, m = start.year, start.month
    while len(out) < n:
        if m in (3, 6, 9, 12):
            out.append(f"{y}{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


@dataclass(frozen=True)
class OiWorld:
    seed: int
    clock: datetime
    krx: tuple[dict[str, Any], ...]  # MemoryGexRepo.krx_futures 행
    investor: tuple[InvestorRecord, ...]
    board: tuple[FuturesRecord, ...]

    def fill(self, repo: Any) -> None:
        """`MemoryGexRepo` 에 KRX 선물 일별·투자자·전광판을 싣는다."""
        repo.krx_futures.extend(dict(r) for r in self.krx)
        repo.seed_investor(self.investor)
        repo.seed_futures(self.board)


def _days(cal: TradingCalendar, first: date, last: date) -> list[date]:
    out: list[date] = []
    d = first
    while d <= last:
        if cal.is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def _ltd(expiry: str, cal: TradingCalendar) -> date:
    return monthly_expiry(int(expiry[:4]), int(expiry[4:]), cal)


KrxOut = tuple[list[dict[str, Any]], dict[str, float], dict[str, int]]


def _make_krx(seed: int, cal: TradingCalendar, days: list[date]) -> KrxOut:
    """종목별 OI 를 이어 간다: 주력 종목에 날마다 잡음, 만기 전 `ROLL_DAYS` 거래일은 근월물 잔고의
    30%를 차월물로 옮긴다(롤오버), 만기일 남은 잔고는 결제되어 다음 날 행이 없다."""
    rows: list[dict[str, Any]] = []
    price = 560.0
    oi: dict[str, float] = {}
    prev_close: dict[str, Decimal] = {}
    last_close: dict[str, float] = {}
    last_oi: dict[str, int] = {}
    for d in days:
        r = _rng(seed, "krx", d.isoformat())
        price *= 1 + r.gauss(0, 0.009)
        live = [e for e in _quarter_expiries(d.replace(day=1), 6) if _ltd(e, cal) >= d][:4]
        if not oi:
            oi.update(zip(live, (284_000.0, 36_000.0, 6_600.0, 3_300.0), strict=True))
        for e in live:
            oi.setdefault(e, 100.0)  # 신규 상장 원월물
        for e in [k for k in oi if k not in live]:
            del oi[e]  # 만기 결제로 소멸
        near, nxt = live[0], live[1]
        to_exp = sum(1 for x in days if d < x <= _ltd(near, cal))  # 만기까지 남은 거래일
        rolling = to_exp < ROLL_DAYS
        if rolling:
            move = oi[near] * 0.3
            oi[near] -= move
            oi[nxt] += move
        main = nxt if rolling else near
        oi[main] = max(1_000.0, oi[main] + r.gauss(0, 3_500))
        received = _utc(datetime.combine(cal.next_trading_day(d), time(8, 5), tzinfo=KST))
        for i, e in enumerate(live):
            close = _q2(price + 0.9 * i + r.uniform(-0.2, 0.2))
            cmp = None if e not in prev_close else close - prev_close[e]
            n = int(oi[e])
            rows.append({
                "trade_date": d, "session": "day", "family": "kospi200", "expiry": e,
                "isu_cd": f"A0{e[2:]}", "close": close, "cmp_prev": cmp, "setl_prc": close,
                "acc_opnint_qty": n, "ts": received,
            })  # fmt: skip
            prev_close[e] = close
            last_close[e] = float(close)
            last_oi[e] = n
    return rows, last_close, last_oi


def _make_investor(seed: int, days: list[date]) -> list[InvestorRecord]:
    out: list[InvestorRecord] = []
    inv = ("frgn", "orgn", "prsn")
    for d in days:
        at = _utc(datetime.combine(d, FLOW_SLOT, tzinfo=KST))
        for market, sector in COMBOS:
            r = _rng(seed, "flow", d.isoformat(), market, sector)
            f = FOREIGN_TAIL.get(d) if (market, sector) == ("K2I", "F001") else None
            frgn = f if f is not None else r.randint(-4_000, 4_000)
            orgn = r.randint(-2_000, 2_000)
            qty = {"frgn": frgn, "orgn": orgn, "prsn": -(frgn + orgn)}
            unit = 140 if sector == "F001" else 3  # 계약당 대금(백만원 — 합성)
            for name in inv:
                out.append(InvestorRecord(
                    ts=at, trade_date=d, session="day", market_code=market, sector_code=sector,
                    investor=name,  # type: ignore[arg-type]
                    net_qty=qty[name], net_value=qty[name] * unit, quality="ok",
                ))  # fmt: skip
    return out


def _make_board(seed: int, cal: TradingCalendar, last_close: dict[str, float],
                last_oi: dict[str, int]) -> list[FuturesRecord]:  # fmt: skip
    at = _utc(datetime.combine(TODAY, BOARD_AT, tzinfo=KST))
    r = _rng(seed, "board")
    live = sorted(e for e in last_oi if _ltd(e, cal) >= TODAY)
    out: list[FuturesRecord] = []
    for i, e in enumerate(live):
        oi = last_oi[e] + (2_600 if i == 0 else 150)
        out.append(FuturesRecord(
            ts=at, trade_date=TODAY, session="day", code=f"A0{e[2:]}", market="F", source="board",
            price=_q2(last_close[e] + 3.1 + r.uniform(-0.05, 0.05)), volume=1_000, oi=oi,
            remaining_days=(_ltd(e, cal) - TODAY).days, quality="ok",
        ))  # fmt: skip
    return out


@cache
def build_oi_world(seed: int = SEED) -> OiWorld:
    cal = TradingCalendar.default()
    days = _days(cal, FIRST, LAST_CONFIRMED)
    krx, last_close, last_oi = _make_krx(seed, cal, days)
    investor = _make_investor(seed, [*days, TODAY])
    board = _make_board(seed, cal, last_close, last_oi)
    return OiWorld(seed, CLOCK, tuple(krx), tuple(investor), tuple(board))
