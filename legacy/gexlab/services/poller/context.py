"""수집 대상 문맥 (docs/phase1_design.md §5·§7, PLAN §2.3·§4.4).

- 시리즈 = (시장분류, 6자리 만기). 시장분류는 월물리스트·전광판 `FID_COND_MRKT_CLS_CODE`
  ('' 월물, WKM 위클리(월), WKI 위클리(목)) — 같은 `261001` 이 WKM·WKI 둘 다 있다
- 최종거래일 원천은 KIS 단건 `futs_last_tr_date`(만기 목록은 월물리스트, PLAN §2.3). 캘린더 계산은
  교차검증용이고, KIS 값을 못 얻었을 때만 대신 쓴다
- 대상 만기: 살아 있는(만기일 15:20 KST 전) 시리즈를 최종거래일 순으로 세워 최근접·차기, 첫 월물.
  만기일 15:20 이 지나면 그 시리즈는 빠지고 다음이 최근접이 된다
- 선물도 같다: 최종거래일 15:20 이 지난 종목(분기 만기일의 근월물)은 건너뛴다
  (`ChainContext.live_futures_codes`). 선물 최종거래일은 마스터 결제월(`F YYYYMM`)과 같은 달 월물
  옵션의 최종거래일(월물리스트·KIS 값), 없으면 캘린더 계산(둘째 목요일). 마스터에 코스피200 선물이
  있는데 없는 종목(만기 지나 새 마스터에서 빠진 근월물)도 건너뛴다 — 선물 전광판 순서엔 남아 있다
- ATM 은 선물 근월물 가격으로 `core.chain` 이 고른다 — 전광판 `atm_cls_name` 은 쓰지 않는다(#11a).
  기준가(`ChainContext.reference`)는 살아 있는 종목의 시세만이다
- 보강 1 = 월물 ATM±n(마스터 행사가), 보강 2 = 추적 만기의 나머지 행사가(전광판·보강 1 제외),
  ATM 가까운 순(`core.chain.by_distance`)
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal, NamedTuple, cast

from kbj.core.calendar import session_tag  # noqa: F401 — 다시 내보내기(KBJ P2)

from core.calendar import (
    TradingCalendar,
    expiry_at,
    monthly_expiry,
    weekly_monday_expiry,
    weekly_thursday_expiry,
)
from core.chain import atm_window, by_distance, covers
from data.kis.master import MRKT_CLS_FAMILY, Family, MasterRow, series_for
from data.kis.models import FuturesBoardRow
from services.poller.records import CallPut, SessionName

Tag = tuple[date, SessionName]
CPS: tuple[CallPut, CallPut] = ("C", "P")
_CLS_ORDER = {"": 0, "WKM": 1, "WKI": 2}  # 같은 날 만기면 월물 먼저(가장 큰 OI)
_MONDAY, _THURSDAY = 0, 3


# KBJ P2(설계 §1.3): session_tag 는 kbj.core.calendar 로 승격했다 — 위 import 로 다시 내보낸다.


class Series(NamedTuple):
    cls: str  # '' 월물 · WKM · WKI
    mtrt: str  # 6자리 mtrt_yymm (월물 YYYYMM, 위클리 YYMMWW)

    @property
    def weekly(self) -> bool:
        return self.cls != ""

    @property
    def family(self) -> Family:
        return MRKT_CLS_FAMILY[self.cls]

    @property
    def label(self) -> str:
        return f"{self.cls or 'M'}:{self.mtrt}"


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    d = first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    if d.month != month:
        raise ValueError(f"{year}-{month} 에 {n}번째 요일이 없다")
    return d


def estimate_last_trade_date(series: Series, cal: TradingCalendar) -> date | None:
    """캘린더로 계산한 최종거래일 (교차검증용, PLAN §6.2). 형식이 맞지 않으면 None.

    위클리 YYMMWW 의 WW 는 그 달 n번째 월요일(WKM)·목요일(WKI)로 읽는다 — 실측 두 건
    (2609W4 = 09-28, 2610W1 = 10-01·WKM 10-05 휴장 → 10-06)과 맞지만 규칙 자체는 확인 필요.
    """
    m = series.mtrt
    if len(m) != 6 or not m.isdigit() or series.cls not in _CLS_ORDER:
        return None
    try:
        if series.cls == "":
            return monthly_expiry(int(m[:4]), int(m[4:]), cal)
        year, month, n = 2000 + int(m[:2]), int(m[2:4]), int(m[4:])
        if series.cls == "WKM":
            return weekly_monday_expiry(_nth_weekday(year, month, _MONDAY, n), cal)
        return weekly_thursday_expiry(_nth_weekday(year, month, _THURSDAY, n), cal)
    except ValueError:
        return None


def resolve_candidates(listed: Mapping[str, Sequence[str]], monthly: int) -> list[Series]:
    """최종거래일을 조회할 시리즈: 위클리 전부 + 월물 목록 앞 `monthly` 개(목록은 결제월 순)."""
    out = [Series("", m) for m in list(listed.get("", ()))[:monthly]]
    out += [Series(cls, m) for cls in ("WKM", "WKI") for m in listed.get(cls, ())]
    return out


@dataclass(frozen=True)
class Targets:
    nearest: Series
    next: Series | None
    monthly: Series | None

    @property
    def tracked(self) -> tuple[Series, ...]:
        """추적 만기(최근접·차기·월물), 겹치면 한 번만."""
        out: list[Series] = []
        for s in (self.nearest, self.next, self.monthly):
            if s is not None and s not in out:
                out.append(s)
        return tuple(out)


def select_targets(dates: Mapping[Series, date], now: datetime) -> Targets | None:
    """만기일 15:20 KST 전인 시리즈를 최종거래일 순으로 — 최근접·차기·첫 월물."""
    alive = sorted((d, _CLS_ORDER.get(s.cls, 9), s) for s, d in dates.items() if now < expiry_at(d))
    if not alive:
        return None
    order = [s for _, _, s in alive]
    monthly = next((s for s in order if not s.weekly), None)
    return Targets(order[0], order[1] if len(order) > 1 else None, monthly)


def kospi200_futures(master: Iterable[MasterRow]) -> list[MasterRow]:
    """마스터의 코스피200 선물 행(`F YYYYMM`), 결제월 순."""
    rows = [
        r
        for r in master
        if r.strike is None
        and r.family == "kospi200"
        and r.expiry is not None
        and r.name.startswith("F ")
    ]
    return sorted(rows, key=lambda r: r.expiry or "")


def near_month(rows: Sequence[FuturesBoardRow]) -> FuturesBoardRow | None:
    """선물 전광판에서 근월물 — 가격이 있는 행 중 잔존일수가 가장 짧은 것(같으면 앞 행)."""
    priced = [r for r in rows if r.futs_prpr is not None and r.futs_prpr > 0]
    if not priced:
        return None
    return min(priced, key=lambda r: 10**6 if r.hts_rmnn_dynu is None else r.hts_rmnn_dynu)


@dataclass(frozen=True)
class SeriesChain:
    """마스터에서 얻은 한 시리즈의 전 행사가와 (행사가, 콜풋) → 종목코드."""

    series: Series
    strikes: tuple[Decimal, ...]  # 오름차순
    codes: Mapping[tuple[Decimal, str], str]

    def code(self, strike: Decimal, cp: str) -> str | None:
        return self.codes.get((strike, cp))


def build_chain(master: Iterable[MasterRow], series: Series) -> SeriesChain | None:
    rows = list(master)
    codes: dict[tuple[Decimal, str], str] = {}
    for cp in CPS:
        for r in series_for(rows, series.family, series.mtrt, cp):
            if r.strike is not None:
                codes[(r.strike, cp)] = r.code
    if not codes:
        return None
    return SeriesChain(series, tuple(sorted({k for k, _ in codes})), codes)


@dataclass(frozen=True)
class FillTarget:
    series: Series
    strike: Decimal
    cp: CallPut
    code: str

    @property
    def key(self) -> str:
        return f"{self.series.label}:{self.strike:.2f}:{self.cp}"


def fill1_targets(chain: SeriesChain, ref: Decimal, n: int) -> tuple[FillTarget, ...]:
    """ATM±n 행사가 × 콜·풋, ATM 가까운 순 (보강 1)."""
    window = atm_window(chain.strikes, ref, n)
    out: list[FillTarget] = []
    for k in by_distance(window, ref):
        for cp in CPS:
            code = chain.code(k, cp)
            if code is not None:
                out.append(FillTarget(chain.series, k, cp, code))
    return tuple(out)


def fill2_targets(
    chains: Sequence[SeriesChain],
    ref: Decimal,
    exclude: Mapping[Series, frozenset[tuple[Decimal, str]]],
) -> tuple[FillTarget, ...]:
    """추적 만기 전 행사가 − 제외(전광판이 준 것·보강 1), ATM 가까운 순 → 만기 순 → 콜·풋."""
    strikes = {k for ch in chains for k in ch.strikes}
    if not strikes:
        return ()
    out: list[FillTarget] = []
    for k in by_distance(strikes, ref):
        for ch in chains:
            ex = exclude.get(ch.series, frozenset())
            for cp in CPS:
                code = ch.code(k, cp)
                if code is not None and (k, cp) not in ex:
                    out.append(FillTarget(ch.series, k, cp, code))
    return tuple(out)


def board_covers(
    series: Series, board: Iterable[Decimal], ref: Decimal, fill1: Iterable[Decimal] = ()
) -> bool:
    """전광판 커버리지 (설계 §5): 위클리는 전광판이 ATM 을 담는가, 월물은 전광판 또는 보강 1 범위가.

    `covers` 는 이어진 구간을 가정하므로 두 구간을 따로 본다(합치면 가운데 빈 곳을 못 본다).
    """
    ks = list(board)
    if ks and covers(ks, ref):
        return True
    if series.weekly:
        return False
    f1 = list(fill1)
    return bool(f1) and covers(f1, ref)


# ── 가변 문맥 (collector 가 응답으로 갱신, planner 는 읽기만) ─────────────────


@dataclass(frozen=True)
class ExpiryInfo:
    last_trade_date: date
    source: Literal["kis", "calendar"]


@dataclass(frozen=True)
class FuturesQuote:
    code: str
    price: Decimal
    at_us: int
    source: Literal["board", "single"]


@dataclass(frozen=True)
class BoardSeen:
    keys: frozenset[tuple[Decimal, str]]  # 전광판이 준 (행사가, 콜풋)
    at_us: int

    @property
    def strikes(self) -> frozenset[Decimal]:
        return frozenset(k for k, _ in self.keys)


class ChainContext:
    """poller 의 현재 문맥. 바뀔 때마다 `version` 이 올라 파생값 캐시를 비운다."""

    def __init__(self, master: Iterable[MasterRow] = ()) -> None:
        self.master: tuple[MasterRow, ...] = tuple(master)
        self.listed: dict[str, tuple[str, ...]] = {}
        self.expiries: dict[Series, ExpiryInfo] = {}
        self.futures: FuturesQuote | None = None
        self.futures_codes: tuple[str, ...] = ()  # 선물 전광판 근월물부터
        self.boards: dict[Series, BoardSeen] = {}
        self.version = 0
        self._chains: dict[Series, SeriesChain | None] = {}
        self._memo: dict[object, object] = {}

    def _bump(self) -> None:
        self.version += 1
        self._memo.clear()

    def set_master(self, rows: Iterable[MasterRow]) -> None:
        self.master = tuple(rows)
        self._chains.clear()
        self._bump()

    def set_listed(self, cls: str, mtrts: Sequence[str]) -> None:
        new = tuple(mtrts)
        if self.listed.get(cls) != new:
            self.listed[cls] = new
            self._bump()

    def set_expiry(self, series: Series, info: ExpiryInfo) -> None:
        if self.expiries.get(series) != info:
            self.expiries[series] = info
            self._bump()

    def set_futures(self, quote: FuturesQuote) -> None:
        old = self.futures
        self.futures = quote
        if old is None or (old.code, old.price) != (quote.code, quote.price):
            self._bump()

    def set_futures_codes(self, codes: Sequence[str]) -> None:
        self.futures_codes = tuple(codes)

    def set_board(self, series: Series, seen: BoardSeen) -> None:
        old = self.boards.get(series)
        self.boards[series] = seen
        if old is None or old.keys != seen.keys:
            self._bump()

    def memo[T](self, key: object, fn: Callable[[], T]) -> T:
        """이 버전에서 한 번만 계산 (planner 파생값)."""
        if key not in self._memo:
            self._memo[key] = fn()
        return cast(T, self._memo[key])

    def chain(self, series: Series) -> SeriesChain | None:
        if series not in self._chains:
            self._chains[series] = build_chain(self.master, series)
        return self._chains[series]

    def listed_series(self) -> list[Series]:
        return [Series(cls, m) for cls in ("", "WKM", "WKI") for m in self.listed.get(cls, ())]

    def dates(self) -> dict[Series, date]:
        listed = set(self.listed_series())
        return {s: e.last_trade_date for s, e in self.expiries.items() if s in listed}

    def targets(self, now: datetime) -> Targets | None:
        return select_targets(self.dates(), now)

    def master_futures_codes(self) -> tuple[str, ...]:
        """마스터의 코스피200 선물(월물 순) — 선물 전광판을 아직 못 받았을 때 쓴다."""
        return tuple(r.code for r in kospi200_futures(self.master))

    def all_futures_codes(self) -> tuple[str, ...]:
        return self.futures_codes or self.master_futures_codes()

    def futures_last_trade_date(self, code: str, cal: TradingCalendar) -> date | None:
        """선물 최종거래일 — 마스터 결제월(`F YYYYMM`)과 같은 달 월물 옵션의 최종거래일(월물리스트·
        KIS `futs_last_tr_date` 로 받은 값), 없으면 캘린더 계산(둘째 목요일).

        결제월을 모르면(마스터에 없는 종목) None."""
        return self._futures_last_map(cal).get(code)

    def _futures_last_map(self, cal: TradingCalendar) -> dict[str, date | None]:
        """마스터의 코스피200 선물 전부 → 최종거래일(결제월 형식이 달라 못 구하면 None)."""
        return self.memo(("futures_last_trade", cal), lambda: self._futures_last(cal))

    def _futures_last(self, cal: TradingCalendar) -> dict[str, date | None]:
        out: dict[str, date | None] = {}
        for r in kospi200_futures(self.master):
            s = Series("", r.expiry or "")
            info = self.expiries.get(s)
            out[r.code] = (
                info.last_trade_date if info is not None else estimate_last_trade_date(s, cal)
            )
        return out

    def futures_alive(self, code: str, now: datetime, cal: TradingCalendar) -> bool:
        """최종거래일 15:20 KST 전인가.

        마스터(상장 종목 목록)에 코스피200 선물이 있는데 이 종목이 없으면 살아 있지 않다 — 만기 지난
        근월물이 새 마스터에서 빠져도 선물 전광판 순서(`futures_codes`)엔 남는다(야간 B 는 전광판을
        부르지 않아 갱신되지 않는다). 마스터에 코스피200 선물이 하나도 없을 때만(아직 모른다) 모르는
        종목을 살아 있다고 본다. 마스터에 있는데 최종거래일을 못 구한 종목도 살아 있다고 본다."""
        known = self._futures_last_map(cal)
        if code not in known:
            return not known
        last = known[code]
        return last is None or now < expiry_at(last)

    def live_futures_codes(self, now: datetime, cal: TradingCalendar) -> tuple[str, ...]:
        """`all_futures_codes` 순서에서 최종거래일 15:20 이 지나지 않은 종목 — 분기 만기일 15:20 이
        지나면 만기 지난 근월물이 빠지고 차월물이 맨 앞에 온다."""
        return tuple(c for c in self.all_futures_codes() if self.futures_alive(c, now, cal))

    def reference(self, now: datetime, cal: TradingCalendar) -> FuturesQuote | None:
        """ATM 기준 선물 시세 — 살아 있는 종목의 것만. 분기 만기일 15:20 이 지나면 만기 지난 근월물
        시세는 쓰지 않는다(차월물 시세가 들어올 때까지 None)."""
        q = self.futures
        if q is None or not self.futures_alive(q.code, now, cal):
            return None
        return q
