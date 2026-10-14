"""체인 문맥과 Redis 로 나눠 쓰는 체인 재료.

GX `services/chain_feed.py` + `services/poller/context.py` 의 문맥 모델을 옮겼다(P7 묶음 FND-B,
docs/p7_design.md §1.1·D-P7-3 ⑥).

GX 문서: docs/phase1_design.md §2·§4·§5. ws-gateway(KBJ)·engine(KBJ)·legacy poller·scheduler
분봉이 같은 문맥을 쓴다. poller 만 쓰는 수집 계획(보강 대상·전광판 커버리지·근월물 고르기·
후보 시리즈)은 legacy `services/poller/context.py` 에 남고, 이 모듈의 이름을 다시 내보낸다.

- 마스터: scheduler 가 `kis:master`(+`kis:master:sha`)에 둔다 → poller·ws-gateway 는 `MasterWatcher`
  로 sha 만 가끔 보고 바뀌었을 때만 원문을 받아 파싱한다. 파싱 실패·Redis 오류면 직전 것을 둔다
- 체인 문맥: poller 가 월물리스트·KIS 최종거래일·선물 전광판 순서를 알아내면 `poller:chain_context`
  에 둔다(`ContextPublisher`) → ws-gateway 가 같은 최근접 만기·근월물을 고른다(`apply_context`).
  poller 문맥이 없으면 ws-gateway 는 마스터 + 캘린더 계산 최종거래일로 대신한다(`calendar_context`)
- 시리즈 = (시장분류, 6자리 만기). 최종거래일 원천은 KIS 단건 `futs_last_tr_date`, 캘린더 계산은
  교차검증용(KIS 값을 못 얻었을 때만 대신 쓴다). 대상 만기: 살아 있는(만기일 15:20 KST 전) 시리즈를
  최종거래일 순으로 세워 최근접·차기, 첫 월물(`select_targets`)
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, NamedTuple, cast

from pydantic import ValidationError
from redis import Redis
from redis.exceptions import RedisError

from kbj.core.calendar import (
    TradingCalendar,
    expiry_at,
)
from kbj.core.gex.records import CallPut, SessionName
from kbj.data.private.kis.master import MRKT_CLS_FAMILY, Family, MasterRow, parse_master, series_for
from kbj.engines.gex.series import CLS_ORDER, MONDAY, THURSDAY, last_trade_date_of
from kbj.services.runtime.bus import (
    CHAIN_CONTEXT_KEY,
    MASTER_KEY,
    MASTER_SHA_KEY,
    ChainContextSnapshot,
    ExpiryEntry,
    MasterSnapshot,
)
from kbj.services.runtime.log import log_event

Tag = tuple[date, SessionName]
CPS: tuple[CallPut, CallPut] = ("C", "P")
# 시장분류 순서·요일·캘린더 최종거래일 계산은 엔진 순수 모듈이 정본(P7 ENG — 계약 ⑨).
# legacy poller 가 옛 비공개 이름으로 읽어 별칭을 둔다.
_CLS_ORDER = CLS_ORDER
_MONDAY, _THURSDAY = MONDAY, THURSDAY


# ── 문맥 모델(GX services/poller/context.py) ─────────────────────────────────────────────────


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


def estimate_last_trade_date(series: Series, cal: TradingCalendar) -> date | None:
    """캘린더로 계산한 최종거래일 (교차검증용, PLAN §6.2). 형식이 맞지 않으면 None.

    위클리 YYMMWW 의 WW 는 그 달 n번째 월요일(WKM)·목요일(WKI)로 읽는다 — 실측 두 건
    (2609W4 = 09-28, 2610W1 = 10-01·WKM 10-05 휴장 → 10-06)과 맞지만 규칙 자체는 확인 필요.
    """
    return last_trade_date_of(series.cls, series.mtrt, cal)


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


# ── Redis 로 나눠 쓰기(GX services/chain_feed.py) ─────────────────────────────────────────────

MASTER_CHECK_S = 30.0
CONTEXT_EVERY_S = 5.0
CONTEXT_REFRESH_S = 60.0

log = logging.getLogger("services.chain_feed")  # GX 로그 이름 그대로(운영 grep)


class MasterWatcher:
    """`poll()` — 새 마스터면 행 목록, 그대로거나 못 읽으면 None."""

    def __init__(
        self,
        redis: Redis,
        service: str,
        *,
        every_s: float = MASTER_CHECK_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._r = redis
        self._service = service
        self._every = every_s
        self._clock = clock
        self._next = 0.0
        self.sha: str | None = None
        self.snapshot: MasterSnapshot | None = None

    def poll(self, *, force: bool = False) -> list[MasterRow] | None:
        now = self._clock()
        if not force and now < self._next:
            return None
        self._next = now + self._every
        try:
            sha = self._r.get(MASTER_SHA_KEY)
            if sha is None or sha.decode() == self.sha:  # pyright: ignore[reportAttributeAccessIssue]
                return None
            raw = self._r.get(MASTER_KEY)
            if raw is None:
                return None
            snap = MasterSnapshot.model_validate_json(raw)  # pyright: ignore[reportArgumentType]
            if MasterSnapshot.digest(snap.text) != snap.sha256:
                raise ValueError("마스터 sha 가 원문과 다르다")
            rows = parse_master(snap.text)
        except (RedisError, ValidationError, ValueError) as e:
            err = type(e).__name__
            log_event(log, logging.WARNING, self._service, "master_read_failed", error=err)
            return None
        self.sha = snap.sha256
        self.snapshot = snap
        log_event(
            log,
            logging.INFO,
            self._service,
            "master_loaded",
            (snap.trade_date, snap.session),
            rows=len(rows),
            sha=snap.sha256[:12],
        )
        return rows


# ── 체인 문맥 ────────────────────────────────────────────────────────────────


def context_snapshot(ctx: ChainContext, at: datetime) -> ChainContextSnapshot:
    return ChainContextSnapshot(
        at=at,
        version=ctx.version,
        listed={cls: list(v) for cls, v in ctx.listed.items()},
        expiries=[
            ExpiryEntry(cls=s.cls, mtrt=s.mtrt, last_trade_date=e.last_trade_date, source=e.source)
            for s, e in sorted(ctx.expiries.items())
        ],
        futures_codes=list(ctx.futures_codes),
    )


def apply_context(ctx: ChainContext, snap: ChainContextSnapshot) -> None:
    """poller 문맥을 이 서비스의 ChainContext 에 (마스터는 따로)."""
    for cls, mtrts in snap.listed.items():
        ctx.set_listed(cls, mtrts)
    for e in snap.expiries:
        ctx.set_expiry(Series(e.cls, e.mtrt), ExpiryInfo(e.last_trade_date, e.source))
    ctx.set_futures_codes(snap.futures_codes)


def calendar_context(ctx: ChainContext, cal: TradingCalendar) -> int:
    """poller 문맥이 없을 때: 마스터의 코스피200 시리즈를 상장 목록으로, 최종거래일은 캘린더 계산.
    채운 시리즈 수를 돌려준다(PLAN §2.3 — KIS 값이 원천이고 이것은 대신 쓰는 값)."""
    n = 0
    for cls, family in MRKT_CLS_FAMILY.items():
        mtrts = sorted(
            {r.expiry for r in ctx.master if r.is_option and r.family == family and r.expiry}
        )
        ctx.set_listed(cls, mtrts)
        for m in mtrts:
            s = Series(cls, m)
            if s in ctx.expiries:
                continue
            d = estimate_last_trade_date(s, cal)
            if d is not None:
                ctx.set_expiry(s, ExpiryInfo(d, "calendar"))
                n += 1
    return n


def load_context(redis: Redis) -> ChainContextSnapshot | None:
    """없거나 형식이 틀리면 None. Redis 오류는 그대로 올린다."""
    raw = redis.get(CHAIN_CONTEXT_KEY)
    if raw is None:
        return None
    try:
        return ChainContextSnapshot.model_validate_json(raw)  # pyright: ignore[reportArgumentType]
    except ValidationError:
        return None


class ContextPublisher:
    """poller 문맥이 바뀌면(또는 60초마다) `poller:chain_context` 에 둔다. 최소 간격 5초."""

    def __init__(
        self,
        redis: Redis,
        service: str = "poller",
        *,
        every_s: float = CONTEXT_EVERY_S,
        refresh_s: float = CONTEXT_REFRESH_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._r = redis
        self._service = service
        self._every = every_s
        self._refresh = refresh_s
        self._clock = clock
        self._version: int | None = None
        self._at: float | None = None
        self._failing = False

    def publish(self, ctx: ChainContext, now: datetime) -> bool:
        t = self._clock()
        if self._at is not None:
            age = t - self._at
            if age < self._every or (ctx.version == self._version and age < self._refresh):
                return False
        if not ctx.listed:
            return False  # 아직 월물리스트를 못 받았다 — 빈 문맥으로 덮지 않는다
        try:
            self._r.set(CHAIN_CONTEXT_KEY, context_snapshot(ctx, now).model_dump_json())
        except RedisError as e:
            if not self._failing:
                err = type(e).__name__
                log_event(log, logging.WARNING, self._service, "context_publish_failed", error=err)
            self._failing = True
            self._at = t
            return False
        self._failing = False
        self._version, self._at = ctx.version, t
        return True


__all__ = [
    "CONTEXT_EVERY_S", "CONTEXT_REFRESH_S", "CPS", "MASTER_CHECK_S", "BoardSeen", "ChainContext",
    "ContextPublisher", "ExpiryInfo", "FuturesQuote", "MasterWatcher", "Series", "SeriesChain",
    "Tag", "Targets", "apply_context", "build_chain", "calendar_context", "context_snapshot",
    "estimate_last_trade_date", "kospi200_futures", "load_context", "select_targets",
]  # fmt: skip
