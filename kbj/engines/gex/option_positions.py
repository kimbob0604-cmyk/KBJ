"""옵션 행사가별 투자자 추정 포지션·콜/풋 벽·합성 델타 — 순수 계산(metrics §34, ADR 0044).

근거: 키움 영웅문 도움말 0797(https://download.kiwoom.com/hero4_help_new/0797.htm) — 행사가별 투자자
포지션은 거래소 값이 아니라 투자자별 **합계** 매매에서 추정한다: 평균단가 = 금액 ÷ 수량 ÷ 승수 →
그 시각 현재가가 평균단가에 가장 가까운 두 행사가 → 가까운 쪽 2 : 먼 쪽 1 로 배분 → 누적.
여기서는 1분(`prv_gex.investor_flow`) 해상도로 같은 일을 한다. 계약 ⑨ — I/O 없음, 입력은
작업·리더가 저장소 행을 옮긴 값. 모든 결과는 추정치(quality 최고 estimated)이고 문장은 만들지
않는다(라벨 코드만).

두 단계:
1. `allocate_day` — 그날 분 누적 행 + 체인 시세 → 행사가별 하루 매수·매도 배분(작업이 저장).
2. `build_positions` — 저장된 하루 변화 → 시리즈별 누적 보유·OI 검산·벽·라벨·합성 델타·레벨 표
   (리더).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Final, Literal

from kbj.core.calendar import TradingCalendar, expiry_at
from kbj.core.quality import Quality
from kbj.engines.gex.series import last_trade_date_of

__all__ = [
    "ADD_PCT",
    "CLEAR_PCT",
    "FUTURES_MULTIPLIER",
    "FUT_MARKET",
    "FUT_SECTOR",
    "IMPRECISE_ABS",
    "IMPRECISE_REL",
    "INVESTORS",
    "LABELS",
    "MILLION",
    "MIN_JUDGE",
    "NEAR_SHARE",
    "OPTION_MULTIPLIER",
    "OPTION_SECTORS",
    "PRICE_STALE",
    "REDUCE_PCT",
    "TOP_N",
    "Cp",
    "DayAlloc",
    "DayStat",
    "FlowPoint",
    "FutDay",
    "Label",
    "Level",
    "LevelKind",
    "PositionBoard",
    "Quote",
    "SeriesBoard",
    "SeriesKey",
    "StrikeAlloc",
    "StrikeDay",
    "StrikePosition",
    "Synthetic",
    "TopChange",
    "Wall",
    "allocate_day",
    "build_positions",
    "ewy_level",
    "judge",
    "pick_two",
    "series_end",
    "split",
]

Cp = Literal["C", "P"]
SeriesKey = tuple[str, str]  # (mrkt_cls '' 월물·WKM·WKI, 만기 6자리)
Label = Literal["small", "new", "flipped", "cleared", "reduced", "added", "hold"]
LABELS: Final[tuple[Label, ...]] = (
    "small", "new", "flipped", "cleared", "reduced", "added", "hold",
)  # fmt: skip

MILLION: Final = 1_000_000  # investor_flow 대금 백만원 → 원
# 원/pt — 코스피200 옵션·위클리(KRX 상품 명세) [확인 필요 — 위클리]
OPTION_MULTIPLIER: Final = 250_000
FUTURES_MULTIPLIER: Final = 250_000  # 원/pt — 코스피200 선물. 옵션과 같아 H × δ = 선물 환산 계약
NEAR_SHARE: Final = 2.0 / 3.0  # 키움: 가까운 쪽 2 : 먼 쪽 1
PRICE_STALE: Final = timedelta(minutes=5)  # 이보다 묵은 행사가 시세는 후보에서 뺀다 [확인 필요]
IMPRECISE_ABS: Final = 0.05  # pt — 평균단가 반올림 오차 반폭이 이것과
IMPRECISE_REL: Final = 0.10  # 평균단가 × 이것 중 큰 값보다 크면 imprecise [확인 필요]
TIE_EPS: Final = 1e-9  # 거리 같음 판정(pt)
MIN_JUDGE: Final = 300  # 계약 — 이보다 작은 보유는 판정 보류 small [확인 필요]
CLEAR_PCT: Final = 0.80  # 줄어든 비율 ≥ 80% → cleared(청산) [확인 필요]
REDUCE_PCT: Final = 0.30  # ≥ 30% → reduced(축소) [확인 필요]
ADD_PCT: Final = 0.30  # 늘어난 비율 ≥ 30% → added(확대) [확인 필요]
TOP_N: Final = 10  # 오늘 바뀐 행사가 상위 수
ROUND: Final = 4  # 계약 수 표시 반올림 자리(소수 배분 — 플랫폼 간 float 끝자리)

INVESTORS: Final[tuple[str, ...]] = ("frgn", "prsn", "orgn")
FUT_MARKET: Final = "K2I"
FUT_SECTOR: Final = "F001"
# (시장, 업종) → (mrkt_cls, 콜/풋) — config/markets.yaml flows.minute.pairs 의 옵션 조합
OPTION_SECTORS: Final[Mapping[tuple[str, str], tuple[str, Cp]]] = {
    ("K2I", "OC01"): ("", "C"),
    ("K2I", "OP01"): ("", "P"),
    ("WKM", "OC05"): ("WKM", "C"),
    ("WKM", "OP05"): ("WKM", "P"),
    ("WKI", "OC04"): ("WKI", "C"),
    ("WKI", "OP04"): ("WKI", "P"),
}


# ── 1. 하루 배분 ────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FlowPoint:
    """투자자 하나의 그날 누적 한 분(`investor_flow` 행). 대금 백만원."""

    ts: datetime  # 슬롯 분(aware)
    buy_qty: int | None
    buy_value: int | None
    sell_qty: int | None
    sell_value: int | None
    quality: Quality = Quality.OK


@dataclass(frozen=True)
class Quote:
    """행사가 시세 한 줄(`chain_snapshots`)."""

    ts: datetime
    mrkt_cls: str
    expiry: str
    strike: float
    cp: Cp
    last: float | None


@dataclass(frozen=True)
class StrikeAlloc:
    mrkt_cls: str
    expiry: str
    strike: float
    cp: Cp
    buy: float
    sell: float

    @property
    def net(self) -> float:
        return self.buy - self.sell


@dataclass
class _Side:
    qty: int = 0  # 기준(앞 행) 누적 수량
    value: int = 0  # 기준 누적 대금(백만원)
    allocated: float = 0.0
    unallocated: float = 0.0
    imprecise: float = 0.0
    single: float = 0.0
    reverted: int = 0
    missing: int = 0


@dataclass(frozen=True)
class DayAlloc:
    """묶음(시장·업종) 하나·투자자 하나의 하루 배분 결과."""

    investor: str
    mrkt_cls: str
    cp: Cp
    minutes: int  # 쓴 분 행 수
    gap_minutes: int  # 개장 ~ 마지막 행 사이 빠진 슬롯 분
    first_ts: datetime | None
    last_ts: datetime | None
    total_buy: int  # 마지막으로 쓴 누적 매수 수량 = 배분 + 미배분
    total_sell: int
    unallocated_buy: float
    unallocated_sell: float
    imprecise_qty: float
    single_qty: float
    reverted: int  # 역행(원천 정정)으로 버린 행·면 수
    skipped: int  # invalid·빈 칸으로 쓰지 않은 행·면 수
    strikes: tuple[StrikeAlloc, ...]
    reasons: tuple[str, ...]

    @property
    def allocated_buy(self) -> float:
        return sum(s.buy for s in self.strikes)

    @property
    def allocated_sell(self) -> float:
        return sum(s.sell for s in self.strikes)


def series_end(key: SeriesKey, cal: TradingCalendar) -> datetime | None:
    """시리즈 만기 시각(최종거래일 15:20 — `expiry_at`). 형식을 모르면 None."""
    ltd = last_trade_date_of(key[0], key[1], cal)
    return None if ltd is None else expiry_at(ltd)


def pick_two(cands: Sequence[tuple[float, float]], p: float, cp: Cp) -> list[tuple[float, float]]:
    """(행사가, 가격) 후보 중 |가격 − p| 가 가장 작은 둘. 거리가 같으면 ATM 쪽(콜 낮은·풋 높은
    행사가)."""

    def key(c: tuple[float, float]) -> tuple[float, float]:
        return (round(abs(c[1] - p), 9), c[0] if cp == "C" else -c[0])

    return sorted(cands, key=key)[:2]


def split(qty: float, two: Sequence[tuple[float, float]], p: float) -> list[tuple[float, float]]:
    """수량을 두 후보에 (행사가, 배분 수량)으로 — 2 : 1, 거리가 같으면 1 : 1, 하나면 전부."""
    if not two:
        return []
    if len(two) == 1:
        return [(two[0][0], qty)]
    d0, d1 = abs(two[0][1] - p), abs(two[1][1] - p)
    if abs(d0 - d1) <= TIE_EPS:
        return [(two[0][0], qty / 2.0), (two[1][0], qty / 2.0)]
    near = qty * NEAR_SHARE
    return [(two[0][0], near), (two[1][0], qty - near)]


class _Ladder:
    """시각 순 시세 → 슬롯 t 에 (시리즈, 콜풋) 별 [t − PRICE_STALE, t) 안 가장 늦은 가격."""

    def __init__(self, quotes: Iterable[Quote], cp: Cp) -> None:
        self._q = sorted((q for q in quotes if q.cp == cp), key=lambda q: q.ts)
        self._i = 0
        self._latest: dict[tuple[SeriesKey, float], tuple[datetime, float | None]] = {}

    def at(self, t: datetime) -> dict[SeriesKey, list[tuple[float, float]]]:
        while self._i < len(self._q) and self._q[self._i].ts < t:
            q = self._q[self._i]
            self._latest[((q.mrkt_cls, q.expiry), q.strike)] = (q.ts, q.last)
            self._i += 1
        out: dict[SeriesKey, list[tuple[float, float]]] = defaultdict(list)
        floor = t - PRICE_STALE
        for (s, k), (ts, last) in self._latest.items():
            if ts >= floor and last is not None and last > 0:
                out[s].append((k, last))
        return out


def _series_for(
    ladder: Mapping[SeriesKey, list[tuple[float, float]]],
    mrkt_cls: str,
    t: datetime,
    cal: TradingCalendar,
) -> SeriesKey | None:
    """분 t 에 배분할 시리즈 — 시세가 있고 만기 시각이 t 보다 뒤인 가장 가까운 만기."""
    best: tuple[datetime, SeriesKey] | None = None
    for s, rows in ladder.items():
        if s[0] != mrkt_cls or not rows:
            continue
        end = series_end(s, cal)
        if end is None or end <= t:
            continue
        if best is None or (end, s) < best:
            best = (end, s)
    return None if best is None else best[1]


def _delta(side: _Side, qty: int | None, value: int | None) -> tuple[int, int] | None:
    """앞 기준 대비 (ΔQ, ΔV). 빈 칸·역행이면 None(기준을 옮기지 않는다)."""
    if qty is None or value is None:
        side.missing += 1
        return None
    dq, dv = qty - side.qty, value - side.value
    if dq < 0 or dv < 0:
        side.reverted += 1
        return None
    side.qty, side.value = qty, value
    return dq, dv


def _allocate_side(
    side: _Side,
    acc: dict[tuple[SeriesKey, float], float],
    dq: int,
    dv: int,
    series: SeriesKey | None,
    ladder: Mapping[SeriesKey, list[tuple[float, float]]],
    cp: Cp,
) -> None:
    if dq == 0:
        return
    if series is None:
        side.unallocated += dq
        return
    p = dv * MILLION / dq / OPTION_MULTIPLIER
    two = pick_two(ladder[series], p, cp)
    if not two:
        side.unallocated += dq
        return
    u = 2 * 0.5 * MILLION / (dq * OPTION_MULTIPLIER)  # 대금 반올림 두 번의 평균단가 오차 반폭(pt)
    if u > max(IMPRECISE_ABS, IMPRECISE_REL * p):
        side.imprecise += dq
    if len(two) == 1:
        side.single += dq
    for k, q in split(float(dq), two, p):
        acc[(series, k)] += q
        side.allocated += q


def allocate_day(
    points: Mapping[str, Sequence[FlowPoint]],
    quotes: Sequence[Quote],
    *,
    mrkt_cls: str,
    cp: Cp,
    open_at: datetime,
    cal: TradingCalendar,
) -> dict[str, DayAlloc]:
    """묶음 하나(월물 콜 등)의 그날 — 투자자 → 하루 배분(metrics §34.1).

    `points` = 투자자 → 그날 분 누적 행(아무 순서). `quotes` = 그날 체인 시세(그 mrkt_cls 의
    시리즈들).
    """
    if open_at.tzinfo is None:
        raise ValueError("open_at 은 시간대가 있어야 한다")
    ordered = {inv: sorted(rows, key=lambda r: r.ts) for inv, rows in points.items()}
    slots = sorted({r.ts for rows in ordered.values() for r in rows})
    ladder = _Ladder(quotes, cp)
    snaps: dict[datetime, tuple[SeriesKey | None, dict[SeriesKey, list[tuple[float, float]]]]] = {}
    for t in slots:
        lad = ladder.at(t)
        snaps[t] = (_series_for(lad, mrkt_cls, t, cal), lad)
    out: dict[str, DayAlloc] = {}
    for inv, rows in ordered.items():
        buy, sell = _Side(), _Side()
        acc_b: dict[tuple[SeriesKey, float], float] = defaultdict(float)
        acc_s: dict[tuple[SeriesKey, float], float] = defaultdict(float)
        used: list[datetime] = []
        skipped = 0
        no_series = 0
        for r in rows:
            if r.quality is Quality.INVALID:
                skipped += 1
                continue
            series, lad = snaps[r.ts]
            db = _delta(buy, r.buy_qty, r.buy_value)
            ds = _delta(sell, r.sell_qty, r.sell_value)
            if db is None and ds is None:
                continue
            used.append(r.ts)
            if series is None and ((db and db[0]) or (ds and ds[0])):
                no_series += 1
            if db is not None:
                _allocate_side(buy, acc_b, db[0], db[1], series, lad, cp)
            if ds is not None:
                _allocate_side(sell, acc_s, ds[0], ds[1], series, lad, cp)
        keys = sorted(set(acc_b) | set(acc_s), key=lambda k: (k[0], k[1]))
        strikes = tuple(
            StrikeAlloc(s[0], s[1], k, cp, acc_b.get((s, k), 0.0), acc_s.get((s, k), 0.0))
            for s, k in keys
        )
        gap = 0
        if used:
            gap = int((used[0] - open_at).total_seconds() // 60)
            gap += sum(int((b - a).total_seconds() // 60) - 1 for a, b in pairwise(used))
            gap = max(gap, 0)
        reasons: list[str] = []
        if gap:
            reasons.append(f"빠진 분 {gap} — 그 구간 체결은 다음 분 시세로 배분")
        if buy.reverted or sell.reverted:
            reasons.append(f"역행(원천 정정) {buy.reverted + sell.reverted}면 — 기준 유지")
        if skipped or buy.missing or sell.missing:
            reasons.append(f"쓰지 않은 행 {skipped + buy.missing + sell.missing}(invalid·빈 칸)")
        if buy.unallocated or sell.unallocated:
            reasons.append(
                f"미배분 매수 {round(buy.unallocated)}·매도 {round(sell.unallocated)}계약"
                f"(시세 없음·만기 뒤 {no_series}분)"
            )
        total = buy.allocated + buy.unallocated + sell.allocated + sell.unallocated
        imp = buy.imprecise + sell.imprecise
        if total and imp / total >= 0.5:
            reasons.append(f"평균단가 거침(대금 백만원 반올림) {round(100 * imp / total)}%")
        out[inv] = DayAlloc(
            investor=inv, mrkt_cls=mrkt_cls, cp=cp, minutes=len(used), gap_minutes=gap,
            first_ts=used[0] if used else None, last_ts=used[-1] if used else None,
            total_buy=buy.qty, total_sell=sell.qty, unallocated_buy=buy.unallocated,
            unallocated_sell=sell.unallocated, imprecise_qty=imp,
            single_qty=buy.single + sell.single, reverted=buy.reverted + sell.reverted,
            skipped=skipped + buy.missing + sell.missing, strikes=strikes, reasons=tuple(reasons),
        )  # fmt: skip
    return out


# ── 2. 누적·검산·벽·합성 ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StrikeDay:
    """저장된 행사가 하루(`prv_gex.opt_pos_strike`)."""

    trade_date: date
    mrkt_cls: str
    expiry: str
    strike: float
    cp: Cp
    buy: float
    sell: float
    close: float | None = None
    board_oi: int | None = None
    delta: float | None = None

    @property
    def net(self) -> float:
        return self.buy - self.sell


@dataclass(frozen=True)
class DayStat:
    """저장된 묶음 하루 요약(`prv_gex.opt_pos_day`) 중 리더가 쓰는 칸."""

    trade_date: date
    market_code: str
    sector_code: str
    minutes: int
    gap_minutes: int
    unallocated: float
    reasons: tuple[str, ...] = ()
    futures_net: int | None = None  # F001 만 — 그날 마지막 행 순매수 수량
    fut_price: float | None = None  # F001 만 — 근월물 마지막 가격
    last_ts: datetime | None = None


@dataclass(frozen=True)
class FutDay:
    trade_date: date
    net_qty: int | None
    last_ts: datetime | None


@dataclass(frozen=True)
class StrikePosition:
    strike: float
    cp: Cp
    holding: float
    prev_holding: float
    change: float
    buy: float
    sell: float
    oi: int | None
    oi_source: Literal["krx", "board"] | None
    oi_ratio: float | None
    delta: float | None
    label: Label
    quality: Quality
    reason: str | None


@dataclass(frozen=True)
class Wall:
    strike: float
    holding: float
    label: Label | None = None


@dataclass(frozen=True)
class SeriesBoard:
    mrkt_cls: str
    expiry: str
    last_trade_date: date | None
    accum_from: date
    days_used: int
    days_missing: int
    strikes: tuple[StrikePosition, ...]
    call_wall: Wall | None
    put_wall: Wall | None
    prev_call_wall: Wall | None
    prev_put_wall: Wall | None
    delta_contracts: float
    missing_delta: int
    invalid: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Synthetic:
    futures: float | None
    futures_from: date | None
    options: float
    total: float | None
    by_series: tuple[tuple[str, str, float], ...]
    missing_delta: int


@dataclass(frozen=True)
class TopChange:
    mrkt_cls: str
    expiry: str
    strike: float
    cp: Cp
    change: float
    holding: float
    prev_holding: float
    label: Label


LevelKind = Literal["call_wall", "put_wall", "prev_call_wall", "prev_put_wall", "futures"]


@dataclass(frozen=True)
class Level:
    kind: LevelKind
    level: float
    distance_pct: float | None
    ewy: float | None = None


@dataclass(frozen=True)
class PositionBoard:
    trade_date: date
    investor: str
    series: tuple[SeriesBoard, ...]
    synthetic: Synthetic
    top_changes: tuple[TopChange, ...]
    levels: tuple[Level, ...]
    futures_price: float | None
    futures_today: int | None
    reasons: tuple[str, ...] = field(default=())


def judge(prev: float, now: float) -> Label:
    """전일 보유·오늘 보유 → 판정 라벨(metrics §34.4)."""
    if max(abs(prev), abs(now)) < MIN_JUDGE:
        return "small"
    if abs(prev) < MIN_JUDGE:
        return "new"
    sign = 1.0 if prev > 0 else -1.0
    if sign * now < 0 and abs(now) >= MIN_JUDGE:
        return "flipped"
    rem = max(sign * now, 0.0) / abs(prev)
    if 1 - rem >= CLEAR_PCT - TIE_EPS:
        return "cleared"
    if 1 - rem >= REDUCE_PCT - TIE_EPS:
        return "reduced"
    if rem - 1 >= ADD_PCT - TIE_EPS:
        return "added"
    return "hold"


def _wall(items: Iterable[tuple[float, float]], cp: Cp) -> tuple[float, float] | None:
    """(행사가, 보유) 중 가장 작은 음수 — 같으면 ATM 쪽(콜 낮은·풋 높은 행사가)."""
    neg = [(k, h) for k, h in items if h < 0]
    if not neg:
        return None
    return min(neg, key=lambda x: (round(x[1], ROUND), x[0] if cp == "C" else -x[0]))


def _r(v: float) -> float:
    return round(v, ROUND)


def ewy_level(level: float, ewy_close: float | None, fut_price: float | None) -> float | None:
    """`L × EWY ÷ F`(metrics §34.6 — 같은 시각 원/달러는 지워진다). 원천이 없으면 None."""
    if ewy_close is None or fut_price is None or fut_price <= 0:
        return None
    return level * ewy_close / fut_price


def _series_board(
    key: SeriesKey,
    rows: Sequence[StrikeDay],
    *,
    day: date,
    prev_day: date | None,
    krx_oi: Mapping[tuple[str, str, float, str], int],
    days_done: set[date],
    cal: TradingCalendar,
) -> SeriesBoard:
    accum_from = min(r.trade_date for r in rows)
    hold: dict[tuple[float, Cp], float] = defaultdict(float)
    prev: dict[tuple[float, Cp], float] = defaultdict(float)
    today: dict[tuple[float, Cp], StrikeDay] = {}
    for r in rows:
        k = (r.strike, r.cp)
        hold[k] += r.net
        if prev_day is not None and r.trade_date <= prev_day:
            prev[k] += r.net
        if r.trade_date == day:
            today[k] = r
    # 델타·전광판 OI 는 그날 행, 없으면 가장 최근 행
    latest: dict[tuple[float, Cp], StrikeDay] = {}
    for r in sorted(rows, key=lambda r: r.trade_date):
        latest[(r.strike, r.cp)] = r
    span = _trading_days(accum_from, day, cal)
    missing = sum(1 for d in span if d not in days_done)
    strikes: list[StrikePosition] = []
    invalid = 0
    delta_sum = 0.0
    missing_delta = 0
    for k in sorted(hold, key=lambda x: (x[1], x[0])):
        h, hp = hold[k], prev[k]
        t = today.get(k)
        oi_k = krx_oi.get((key[0], key[1], k[0], k[1]))
        src: Literal["krx", "board"] | None = "krx" if oi_k is not None else None
        if oi_k is None and t is not None and t.board_oi is not None:
            oi_k, src = t.board_oi, "board"
        quality, reason = Quality.ESTIMATED, None
        ratio: float | None = None
        if oi_k is None:
            reason = "OI 없음 — 검산 불가"
        elif oi_k > 0:
            ratio = _r(abs(h) / oi_k)
            if abs(h) > oi_k:
                quality, reason = Quality.INVALID, "OI 초과 — 추정 오차·누적 시작 뒤 일부만"
        elif abs(h) >= 1:
            quality, reason = Quality.INVALID, "OI 0 인데 보유 추정 — 추정 오차"
        delta = latest[k].delta
        if quality is not Quality.INVALID and h:
            if delta is None:
                missing_delta += 1
            else:
                delta_sum += h * delta
        if quality is Quality.INVALID:
            invalid += 1
        strikes.append(
            StrikePosition(
                strike=k[0], cp=k[1], holding=_r(h), prev_holding=_r(hp),
                change=_r(t.net if t else 0.0), buy=_r(t.buy if t else 0.0),
                sell=_r(t.sell if t else 0.0), oi=oi_k, oi_source=src, oi_ratio=ratio,
                delta=delta, label=judge(hp, h), quality=quality, reason=reason,
            )
        )  # fmt: skip
    ok = [s for s in strikes if s.quality is not Quality.INVALID]

    def wall(cp: Cp, prev_side: bool) -> Wall | None:
        w = _wall(((s.strike, s.prev_holding if prev_side else s.holding) for s in ok
                   if s.cp == cp), cp)  # fmt: skip
        if w is None:
            return None
        lab: Label = next(s.label for s in ok if s.cp == cp and s.strike == w[0])
        return Wall(w[0], w[1], lab)

    reasons: list[str] = [f"누적 시작 {accum_from.isoformat()} 이후 변화분(실제 잔고 아님)"]
    if missing:
        reasons.append(f"빠진 날 {missing}거래일 — 그날 변화 없음으로 누적")
    if invalid:
        reasons.append(f"OI 검산 실패 {invalid}행(invalid — 벽·합성에서 뺌)")
    if missing_delta:
        reasons.append(f"델타 없음 {missing_delta}행 — 합성에서 뺌")
    return SeriesBoard(
        mrkt_cls=key[0], expiry=key[1], last_trade_date=last_trade_date_of(key[0], key[1], cal),
        accum_from=accum_from, days_used=len(span) - missing, days_missing=missing,
        strikes=tuple(strikes), call_wall=wall("C", False), put_wall=wall("P", False),
        prev_call_wall=wall("C", True) if prev_day else None,
        prev_put_wall=wall("P", True) if prev_day else None,
        delta_contracts=_r(delta_sum), missing_delta=missing_delta, invalid=invalid,
        reasons=tuple(reasons),
    )  # fmt: skip


def _trading_days(a: date, b: date, cal: TradingCalendar) -> list[date]:
    out: list[date] = []
    d = a
    while d <= b:
        if cal.is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def build_positions(
    rows: Sequence[StrikeDay],
    stats: Sequence[DayStat],
    *,
    day: date,
    investor: str,
    krx_oi: Mapping[tuple[str, str, float, str], int],
    cal: TradingCalendar,
    ewy_close: float | None = None,
) -> PositionBoard:
    """저장된 하루 변화(그 투자자, day 이하) → 살아 있는 시리즈별 누적·검산·벽·합성
    (metrics §34.2~§34.6)."""
    prev_day = cal.prev_trading_day(day)
    by_series: dict[SeriesKey, list[StrikeDay]] = defaultdict(list)
    for r in rows:
        if r.trade_date <= day:
            by_series[(r.mrkt_cls, r.expiry)].append(r)
    done: dict[str, set[date]] = defaultdict(set)
    for s in stats:
        cls = OPTION_SECTORS.get((s.market_code, s.sector_code))
        if cls is not None and s.minutes > 0:
            done[cls[0]].add(s.trade_date)
    boards: list[SeriesBoard] = []
    for key in sorted(by_series, key=lambda k: (k[0] != "", k[0], k[1])):
        ltd = last_trade_date_of(key[0], key[1], cal)
        if ltd is None or ltd <= day:  # 만기 소멸(그날 만기면 마감 뒤 소멸)
            continue
        boards.append(
            _series_board(
                key,
                by_series[key],
                day=day,
                prev_day=prev_day,
                krx_oi=krx_oi,
                days_done=done[key[0]],
                cal=cal,
            )
        )
    monthly = sorted((b for b in boards if b.mrkt_cls == ""), key=lambda b: b.expiry)
    head = monthly[0] if monthly else None
    fut = sorted(
        (s for s in stats if (s.market_code, s.sector_code) == (FUT_MARKET, FUT_SECTOR)),
        key=lambda s: s.trade_date,
    )
    f_from = head.accum_from if head else (min((b.accum_from for b in boards), default=None))
    f_cum: float | None = None
    fut_today: int | None = None
    fut_price: float | None = None
    reasons: list[str] = []
    if f_from is not None:
        span = [s for s in fut if f_from <= s.trade_date <= day]
        if span:
            f_cum = float(sum(s.futures_net or 0 for s in span))
            if any(s.futures_net is None for s in span):
                reasons.append("선물 순매수 빈 날 — 0 으로 더함")
    for s in fut:
        if s.trade_date == day:
            fut_today, fut_price = s.futures_net, s.fut_price
    options = sum(b.delta_contracts for b in boards)
    syn = Synthetic(
        futures=f_cum, futures_from=f_from, options=_r(options),
        total=None if f_cum is None else _r(f_cum + options),
        by_series=tuple((b.mrkt_cls, b.expiry, b.delta_contracts) for b in boards),
        missing_delta=sum(b.missing_delta for b in boards),
    )  # fmt: skip
    tops: list[TopChange] = [
        TopChange(
            b.mrkt_cls, b.expiry, s.strike, s.cp, s.change, s.holding, s.prev_holding, s.label
        )
        for b in boards
        for s in b.strikes
        if s.change
    ]
    tops.sort(key=lambda t: (-abs(t.change), t.mrkt_cls, t.expiry, t.cp, t.strike))
    levels: list[Level] = []
    if head is not None:
        walls: tuple[tuple[LevelKind, Wall | None], ...] = (
            ("call_wall", head.call_wall), ("put_wall", head.put_wall),
            ("prev_call_wall", head.prev_call_wall), ("prev_put_wall", head.prev_put_wall),
        )  # fmt: skip
        for kind, w in walls:
            if w is not None:
                levels.append(_level(kind, w.strike, fut_price, ewy_close))
    if fut_price is not None:
        levels.append(_level("futures", fut_price, fut_price, ewy_close))
    levels.sort(key=lambda lv: (-lv.level, lv.kind))
    return PositionBoard(
        trade_date=day, investor=investor, series=tuple(boards), synthetic=syn,
        top_changes=tuple(tops[:TOP_N]), levels=tuple(levels), futures_price=fut_price,
        futures_today=fut_today, reasons=tuple(reasons),
    )  # fmt: skip


def _level(
    kind: LevelKind,
    lv: float,
    fut: float | None,
    ewy_close: float | None,
) -> Level:
    dist = None if fut is None or fut <= 0 else round((lv - fut) / fut * 100, ROUND)
    return Level(kind, lv, dist, ewy_level(lv, ewy_close, fut))
