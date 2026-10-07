"""scheduler KRX 전일 적재 (PLAN §4.6 `PRE_DAY`, docs/phase1_design.md §2·§8).

- 한 번의 적재 시도(`KrxLoader.load`)는 거래일(BAS_DD) 하나의 KRX 코스피200 옵션·선물 일별을
  종류마다 한 번 불러 `krx_opt_daily`·`krx_fut_daily` 에 쓴다. 유니크 키 DO UPDATE 라 다시 써도
  행이 늘지 않는다(멱등). 이미 DB 에 있는 종류는 부르지 않는다(이어 받기 — 재기동해도 그날 호출을
  되풀이하지 않는다). DB 를 읽지 못하면 쓰기도 못 하니 부르지 않고 `failed`(호출을 아낀다).
  종류끼리 격리 — 한쪽 실패가 다른 쪽을 막지 않는다
- 저장소는 스풀 없는 싱크를 따로 준다: 스풀에 들어간 행은 `krx_loaded` 에 보이지 않아 다시 부르게
  되고, 상태 루프의 싱크 잠금을 KRX 쓰기(옵션 하루 약 16,700행)가 오래 쥐지 않게 한다. DB 가 안
  되면 `failed` 로 돌아오고 부르는 쪽이 나중에 다시 부른다
- 파싱은 data/krx/models.py(세션 = `ISU_NM` 끝·`MKT_NM`, 야간 `IMP_VOLT` '0.00' → NULL), 적재는
  코스피200 계열만(data/store.py `KRX_FAMILIES`). 행 검증 실패는 그 행만 빼고 건수·첫 오류를 결과에
- 아직 갱신 전: 빈 응답(휴장일과 같은 `{"OutBlock_1": []}`) 또는 요청한 BAS_DD 가 아닌 행뿐이면
  `not_refreshed` — 다시 부를지는 부르는 쪽이 정한다
- 호출 수(`KrxCallBudget`): KST 날짜별로 Redis `krx:calls:<YYYYMMDD>`(services/bus.py)에 센다 —
  재기동해도 이어진다. Redis 가 안 되면 이 프로세스 셈만. 상한(기본 200 **[확인 필요]** — KRX
  10,000회/일(#16)이고 키를 다른 작업과 나눠 쓴다)에 닿으면 부르지 않고 `capped`
- 하루 구동(`KrxDaily`, scheduler 가 1초마다 step): 거래일 D 의 08:05(PRE_DAY)부터 전 거래일을
  받는다. 아직 갱신 전·실패면 10분 뒤 다시, 10:00 **[확인 필요]** 까지 못 받으면 health
- 마스터 ⊇ KRX 대조(설계 §5, services/scheduler/master_check.py): 그날 받은 마스터(PRE_DAY 이후)와
  전 거래일 KRX 옵션이 둘 다 있으면 마스터마다 한 번(PRE_DAY·PRE_NIGHT) 대조해 빠진 행사가가
  있으면 health. 신규 행사가·KRX 행 없는 만기는 health 가 아니다(로그만)
- 적재·대조는 작업 스레드에서 돈다(상태 루프를 막지 않게) — 인증키는 KrxClient 안에만 있고 결과·
  로그에는 가린 문구만 담는다
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from redis import Redis
from redis.exceptions import RedisError

from core.calendar import KST, PRE_DAY_START, TradingCalendar
from data.kis.master import parse_master
from data.krx.eod import FUT_DAILY, OPT_DAILY, KrxError
from data.krx.models import (
    KrxFuturesDaily,
    KrxOptionDaily,
    parse_futures_rows,
    parse_option_rows,
)
from data.store import KrxKind
from services.auth.health import HealthEvent, HealthSink, Severity
from services.bus import MasterSnapshot, krx_calls_key
from services.runtime import Tagger, log_event, run_in_thread, utcnow
from services.scheduler.master_check import MasterKrxReport, master_covers_krx

SERVICE = "scheduler"
KINDS: tuple[KrxKind, ...] = ("options", "futures")
ENDPOINTS: dict[KrxKind, str] = {"options": OPT_DAILY, "futures": FUT_DAILY}
KIND_KO: dict[KrxKind, str] = {"options": "옵션", "futures": "선물"}
CALLS_TTL_S = 3 * 86400
DETAIL_MAX = 120

Status = Literal["loaded", "present", "not_refreshed", "failed", "capped"]

log = logging.getLogger("services.scheduler.krx")


class KrxDailyConfig(BaseModel):
    """KRX 전일 적재 구동 설정. 시각은 KST. 하루 호출 상한은 `KrxCallBudget`(설정
    `KRX_DAILY_CALL_CAP`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: time = time(8, 5)  # 설계 §2: PRE_DAY 08:05~ (KRX 는 다음 영업일 08:00 갱신, #16)
    retry_s: float = Field(default=600.0, gt=0)  # 확인 필요: 아직 갱신 전·실패면 10분 뒤 다시
    deadline: time = time(10, 0)  # 확인 필요: 다시 부르는 것은 이때까지, 못 받으면 health


# ── 호출 수 ─────────────────────────────────────────────────────────────────


class KrxCapReached(RuntimeError):
    """그날 호출 상한 — 부르지 않았다."""


class KrxCallBudget:
    """KST 날짜별 KRX 호출 수. 부르기 전에 `take` 로 한 번 몫을 가져간다(실패한 호출도 센다).

    Redis(`krx:calls:<YYYYMMDD>`, TTL 3일)에 두어 재기동해도 상한이 이어진다. Redis 가 안 되면 이
    프로세스 셈만 쓴다 — 쓴 수는 둘 중 큰 값.
    """

    def __init__(self, redis: Redis | None, cap: int, *, ttl_s: int = CALLS_TTL_S) -> None:
        if cap <= 0:
            raise ValueError("cap > 0")
        self.cap = cap
        self._r = redis
        self._ttl = ttl_s
        self._local: dict[date, int] = {}
        self._lock = threading.Lock()
        self._redis_failing = False

    @staticmethod
    def day_of(at: datetime) -> date:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("naive datetime 금지")
        return at.astimezone(KST).date()

    def used(self, at: datetime) -> int:
        """그날(KST) 쓴 호출 수."""
        day = self.day_of(at)
        with self._lock:
            return max(self._local.get(day, 0), self._redis_get(day))

    def take(self, at: datetime) -> int:
        """한 번 몫을 가져가고 그날 쓴 수(이번 포함)를 돌려준다. 상한이면 KrxCapReached(안 센다)."""
        day = self.day_of(at)
        with self._lock:
            used = max(self._local.get(day, 0), self._redis_get(day))
            if used >= self.cap:
                raise KrxCapReached(f"KRX 호출 {day} {used}/{self.cap} — 그날 상한")
            n = used + 1
            self._local[day] = n
            return max(n, self._redis_incr(day))

    def _redis_get(self, day: date) -> int:
        if self._r is None:
            return 0
        try:
            raw = self._r.get(krx_calls_key(day))
        except RedisError as e:
            self._redis_failed(e)
            return 0
        self._redis_failing = False
        return int(raw) if raw is not None else 0  # pyright: ignore[reportArgumentType]

    def _redis_incr(self, day: date) -> int:
        if self._r is None:
            return 0
        try:
            pipe = self._r.pipeline()
            pipe.incr(krx_calls_key(day))
            pipe.expire(krx_calls_key(day), self._ttl)
            n, _ = pipe.execute()
        except RedisError as e:
            self._redis_failed(e)
            return 0
        self._redis_failing = False
        return int(n)

    def _redis_failed(self, e: RedisError) -> None:
        if not self._redis_failing:
            err = type(e).__name__
            log_event(log, logging.WARNING, SERVICE, "krx_calls_redis_failed", error=err)
        self._redis_failing = True


# ── 한 번의 적재 시도 ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class KindResult:
    kind: KrxKind
    status: Status
    rows: int = 0  # 쓴 행 (코스피200 계열)
    received: int = 0  # 받은 행 (요청한 BAS_DD)
    invalid: int = 0  # 행 검증 실패 (빼고 썼다)
    detail: str = ""  # 가린 짧은 사유

    @property
    def done(self) -> bool:
        return self.status in ("loaded", "present")

    def describe(self) -> str:
        name = KIND_KO[self.kind]
        if self.status == "loaded":
            bad = f"·검증 실패 {self.invalid}" if self.invalid else ""
            return f"{name} {self.rows:,}행(받은 {self.received:,}{bad})"
        what = {
            "present": "이미 적재됨",
            "not_refreshed": "아직 갱신 전",
            "failed": "실패",
            "capped": "호출 상한",
        }[self.status]
        return f"{name} {what}" + (f": {self.detail}" if self.detail else "")


@dataclass(frozen=True)
class KrxLoadResult:
    trade_date: date  # 적재한 거래일(BAS_DD)
    results: tuple[KindResult, ...]
    calls_today: int = 0  # 끝난 뒤 그날(KST) 쓴 호출 수

    def of(self, kind: KrxKind) -> KindResult:
        return next(r for r in self.results if r.kind == kind)

    def has(self, status: Status) -> bool:
        return any(r.status == status for r in self.results)

    @property
    def done(self) -> bool:
        return all(r.done for r in self.results)

    def describe(self) -> str:
        return "·".join(r.describe() for r in self.results)


class KrxSource(Protocol):
    def daily(self, endpoint: str, bas_dd: date) -> list[dict[str, Any]]: ...


class KrxStore(Protocol):
    def krx_loaded(self, kind: KrxKind, trade_date: date) -> bool: ...

    def write_krx_options(self, rows: Sequence[KrxOptionDaily], *, ts: datetime) -> int: ...

    def write_krx_futures(self, rows: Sequence[KrxFuturesDaily], *, ts: datetime) -> int: ...

    def krx_option_listing(self, trade_date: date) -> list[tuple[str, str, str, Decimal]]: ...


def _short(text: str) -> str:
    lines = text.strip().splitlines()
    msg = lines[0] if lines else ""
    return msg if len(msg) <= DETAIL_MAX else msg[: DETAIL_MAX - 1] + "…"


class KrxLoader:
    """작업 스레드에서 도는 한 번의 적재 시도 (모듈 설명). 결과만 돌려주고 health 는 부르는 쪽이."""

    def __init__(
        self,
        client: KrxSource,
        store: KrxStore,
        budget: KrxCallBudget,
        *,
        now: Callable[[], datetime] = utcnow,
        tagger: Tagger | None = None,
    ) -> None:
        self._client = client
        self._store = store
        self._budget = budget
        self._now = now
        self._tagger = tagger
        self._loaded: set[tuple[date, KrxKind]] = set()  # 이 프로세스가 쓴 것 (DB 확인 실패 대비)
        self._lock = threading.Lock()  # 한 번에 한 시도 (스레드가 겹쳐 떠도)

    def load(self, trade_date: date) -> KrxLoadResult:
        with self._lock:
            results = tuple(self._one(kind, trade_date) for kind in KINDS)
            return KrxLoadResult(trade_date, results, self._budget.used(self._now()))

    def listing(self, trade_date: date) -> list[tuple[str, str, str, Decimal]]:
        """DB 의 그 거래일 KRX 옵션 상장 목록 (마스터 대조 입력). 실패는 StoreError."""
        return self._store.krx_option_listing(trade_date)

    def _one(self, kind: KrxKind, trade_date: date) -> KindResult:
        try:
            res = self._fetch(kind, trade_date)
        except Exception as e:  # 격리 — 한 종류의 뜻밖의 오류가 다른 종류를 막지 않는다
            res = KindResult(kind, "failed", detail=f"{type(e).__name__}")
        self._log(kind, trade_date, res)
        return res

    def _fetch(self, kind: KrxKind, trade_date: date) -> KindResult:
        if (trade_date, kind) in self._loaded:
            return KindResult(kind, "present")
        try:
            if self._store.krx_loaded(kind, trade_date):
                self._loaded.add((trade_date, kind))
                return KindResult(kind, "present")
        except Exception as e:  # DB 를 못 읽으면 쓰기도 못 한다 — 부르지 않고(호출 아낌) 다음에
            self._log_error("krx_check_failed", kind, trade_date, type(e).__name__)
            return KindResult(kind, "failed", detail=f"DB 확인 실패 — {type(e).__name__}")
        try:
            self._budget.take(self._now())
        except KrxCapReached as e:
            return KindResult(kind, "capped", detail=str(e))
        try:
            raw = self._client.daily(ENDPOINTS[kind], trade_date)
        except KrxError as e:
            return KindResult(kind, "failed", detail=_short(str(e)))
        received_at = self._now()
        want = f"{trade_date:%Y%m%d}"
        same = [r for r in raw if str(r.get("BAS_DD", "")).strip() == want]
        if not same:
            if not raw:
                return KindResult(kind, "not_refreshed", detail="빈 응답")
            got = sorted({str(r.get("BAS_DD", "")).strip() for r in raw})
            detail = f"BAS_DD {','.join(got[:3])} {len(raw)}행"
            return KindResult(kind, "not_refreshed", received=len(raw), detail=detail)
        return self._write(kind, trade_date, same, len(raw) - len(same), received_at)

    def _write(
        self,
        kind: KrxKind,
        trade_date: date,
        rows: list[dict[str, Any]],
        other_days: int,
        received_at: datetime,
    ) -> KindResult:
        notes: list[str] = []
        opts: list[KrxOptionDaily] = []
        futs: list[KrxFuturesDaily] = []
        if kind == "options":
            opts, bad = parse_option_rows(rows)
        else:
            futs, bad = parse_futures_rows(rows)
        try:
            if kind == "options":
                n = self._store.write_krx_options(opts, ts=received_at)
            else:
                n = self._store.write_krx_futures(futs, ts=received_at)
        except Exception as e:  # StoreError 문구는 접속 정보를 가렸다
            detail = _short(f"저장 실패 — {type(e).__name__}: {e}")
            return KindResult(kind, "failed", received=len(rows), detail=detail)
        if bad:
            notes.append(f"검증 실패 {len(bad)}행(첫 {bad[0].index}번: {_short(bad[0].error)})")
        if other_days:
            notes.append(f"다른 BAS_DD {other_days}행 버림")
        if n == 0:
            notes.insert(0, "코스피200 계열 행이 없다")
            return KindResult(kind, "failed", 0, len(rows), len(bad), "; ".join(notes))
        self._loaded.add((trade_date, kind))
        return KindResult(kind, "loaded", n, len(rows), len(bad), "; ".join(notes))

    def _tag(self) -> tuple[date | None, str | None] | None:
        if self._tagger is None:
            return None
        try:
            return self._tagger(self._now())
        except Exception:  # 태깅 실패로 로그가 사라지지 않게
            return None

    def _log(self, kind: KrxKind, trade_date: date, res: KindResult) -> None:
        level = logging.INFO if res.done or res.status == "not_refreshed" else logging.WARNING
        log_event(
            log,
            level,
            SERVICE,
            "krx_daily",
            self._tag(),
            krx_date=trade_date.isoformat(),
            kind=kind,
            status=res.status,
            rows=res.rows,
            received=res.received,
            invalid=res.invalid,
            detail=res.detail,
        )

    def _log_error(self, event: str, kind: KrxKind, trade_date: date, error: str) -> None:
        log_event(
            log,
            logging.WARNING,
            SERVICE,
            event,
            self._tag(),
            krx_date=trade_date.isoformat(),
            kind=kind,
            error=error,
        )


# ── 하루 구동 (Scheduler.step 이 1초마다) ─────────────────────────────────────


class KrxLoads(Protocol):
    def load(self, trade_date: date) -> KrxLoadResult: ...

    def listing(self, trade_date: date) -> list[tuple[str, str, str, Decimal]]: ...


class Submit(Protocol):
    """일을 작업 스레드로 넘기고 Future 를 돌려준다(시험은 그 자리에서 부르는 것을 넣는다)."""

    def __call__[T](self, fn: Callable[[], T], /) -> Future[T]: ...


def krx_thread_submit[T](fn: Callable[[], T], /) -> Future[T]:
    return run_in_thread(fn, name="krx-daily")


@dataclass
class _Day:
    """KST 날짜(거래일) 하루의 적재 진행."""

    day: date  # KST 날짜
    target: date  # 받을 거래일(BAS_DD) = prev_trading_day(day)
    next_try: datetime
    attempts: int = 0
    finished: bool = False  # 다 받았거나 그날은 그만(상한·마감)
    options_done: bool = False  # 옵션 행이 DB 에 있다 (마스터 대조 입력)
    last: KrxLoadResult | None = None
    checked: set[str] = field(default_factory=set[str])  # 대조를 마친 마스터 sha
    check_next: datetime | None = None  # 대조 실패 뒤 다시 할 시각
    reports: list[MasterKrxReport] = field(default_factory=list[MasterKrxReport])


class KrxDaily:
    """거래일 D 의 `start`(08:05, PRE_DAY) 부터 전 거래일 KRX 일별을 받는다 — 상태와 상관없이 KST
    날짜로 정한다(PRE_DAY 에서 DAY 로 넘어가도 이어진다).

    - 한 시도 = `KrxLoader.load(전 거래일)`(작업 스레드). step 은 끝났는지만 보고 기다리지 않는다
    - 다 받으면 그날은 끝(info health `krx_daily_loaded` — 이미 DB 에 있던 날은 로그만)
    - 아직 갱신 전·실패면 `retry_s`(10분) 뒤 다시, 다음 시도가 `deadline`(10:00)을 넘으면 그날은
      그만 + warning `krx_daily_missing`. 실패(HTTP·저장 오류 등)는 그때마다 warning
      `krx_daily_failed`, 갱신 전은 마감 때만 알린다
    - 호출 상한이면 그날은 그만 + warning `krx_daily_capped`
    - 기동이 마감 뒤여도 그날 한 번은 부른다(재기동 따라잡기) — 못 받으면 곧바로
      `krx_daily_missing`. 지난 날(전 거래일보다 앞)은 되받지 않는다
    - 마스터 ⊇ KRX 대조: 전 거래일 옵션이 DB 에 있고, 넘겨받은 마스터가 그날 PRE_DAY(08:00) 뒤에
      받은 것이면 마스터(sha)마다 한 번 — 작업 스레드에서 마스터 파싱 + DB 상장 목록 + 대조.
      빠진 행사가 → warning `master_krx_missing`, 모두 있으면 로그만(`master_krx_checked`). 대조를
      못 하면(DB·파싱 오류, 상장 목록이 빔) warning `master_krx_check_failed`, `retry_s` 뒤 다시
    """

    def __init__(
        self,
        loader: KrxLoads,
        calendar: TradingCalendar,
        health: HealthSink,
        *,
        config: KrxDailyConfig | None = None,
        submit: Submit = krx_thread_submit,
        tagger: Tagger | None = None,
    ) -> None:
        self._loader = loader
        self._cal = calendar
        self._health = health
        self.cfg = config or KrxDailyConfig()
        self._submit = submit
        self._tagger = tagger
        self.day: _Day | None = None
        self._inflight: tuple[_Day, Future[KrxLoadResult]] | None = None
        self._checking: tuple[_Day, str, Future[MasterKrxReport]] | None = None

    @property
    def busy(self) -> bool:
        return self._inflight is not None or self._checking is not None

    def step(self, now: datetime, master: MasterSnapshot | None = None) -> None:
        """끝난 일을 적용하고, 때가 되면 다음 적재·대조를 작업 스레드에 넘긴다. master 는 지금
        쓰는 마스터(scheduler 가 Redis 에 둔 것)."""
        self._collect(now)
        self._collect_check(now)
        day = self._today(now)
        if day is None:
            return
        if self._inflight is None and not day.finished and now >= day.next_try:
            self._start_load(now, day)
        if self._checking is None and master is not None:
            self._start_check(now, day, master)

    def _start_load(self, now: datetime, day: _Day) -> None:
        day.attempts += 1
        target = day.target
        try:
            self._inflight = (day, self._submit(lambda: self._loader.load(target)))
        except Exception as e:  # 스레드를 못 띄웠다 — 실패처럼 다시
            self._retry_or_give_up(now, day, f"시작 실패: {type(e).__name__}", failed=True)
            return
        self._collect(now)  # 벌써 끝났으면(그 자리에서 부르는 submit) 이 step 에 적용

    def _today(self, now: datetime) -> _Day | None:
        k = now.astimezone(KST)
        d = k.date()
        if not self._cal.is_trading_day(d) or k.time() < self.cfg.start:
            return None
        if self.day is None or self.day.day != d:
            self.day = _Day(day=d, target=self._cal.prev_trading_day(d), next_try=now)
        return self.day

    def _collect(self, now: datetime) -> None:
        if self._inflight is None:
            return
        day, fut = self._inflight
        if not fut.done():
            return
        self._inflight = None
        try:
            res = fut.result()
        except Exception as e:  # load 는 종류마다 격리해 예외를 내지 않지만, 혹시
            self._retry_or_give_up(now, day, f"적재 오류: {type(e).__name__}", failed=True)
            return
        day.last = res
        if res.of("options").done:
            day.options_done = True
        what = f"KRX {day.target} 일별"
        if res.done:
            day.finished = True
            if res.has("loaded"):
                detail = f"{what} {res.describe()} — 오늘 호출 {res.calls_today}회"
                self._emit(now, "krx_daily_loaded", detail, "info")
            return
        if res.has("capped"):
            day.finished = True
            detail = f"{what} {res.describe()} — 그날은 더 부르지 않는다"
            self._emit(now, "krx_daily_capped", detail, "warning")
            return
        self._retry_or_give_up(now, day, res.describe(), failed=res.has("failed"))

    def _retry_or_give_up(self, now: datetime, day: _Day, detail: str, *, failed: bool) -> None:
        what = f"KRX {day.target} 일별"
        nxt = now + timedelta(seconds=self.cfg.retry_s)
        deadline = datetime.combine(day.day, self.cfg.deadline, tzinfo=KST)
        if nxt > deadline:
            day.finished = True
            msg = (
                f"{what}을 {self.cfg.deadline:%H:%M}까지 못 받았다(시도 {day.attempts}회) — "
                f"{detail}"
            )
            self._emit(now, "krx_daily_missing", msg, "warning")
            return
        day.next_try = nxt
        if failed:
            again = f"{self.cfg.retry_s / 60:g}분 뒤 다시"
            self._emit(now, "krx_daily_failed", f"{what} {detail} — {again}", "warning")

    # ── 마스터 ⊇ KRX 대조 ──

    def _start_check(self, now: datetime, day: _Day, master: MasterSnapshot) -> None:
        if not day.options_done or master.sha256 in day.checked:
            return
        if master.asof < datetime.combine(day.day, PRE_DAY_START, tzinfo=KST):
            return  # 그날 PRE_DAY 전에 받은 마스터 — PRE_DAY 것을 기다린다
        if day.check_next is not None and now < day.check_next:
            return
        text, target, sha = master.text, day.target, master.sha256
        try:
            fut = self._submit(lambda: self._check(text, target))
        except Exception as e:  # 스레드를 못 띄웠다
            self._check_failed(now, day, sha, f"시작 실패: {type(e).__name__}")
            return
        self._checking = (day, sha, fut)
        self._collect_check(now)

    def _check(self, text: str, target: date) -> MasterKrxReport:
        """작업 스레드: 마스터 파싱 + DB 상장 목록 + 대조."""
        rows = parse_master(text)
        listing = self._loader.listing(target)
        if not listing:
            raise LookupError(f"KRX {target} 옵션 상장 목록이 비었다")
        return master_covers_krx(rows, listing, target)

    def _collect_check(self, now: datetime) -> None:
        if self._checking is None:
            return
        day, sha, fut = self._checking
        if not fut.done():
            return
        self._checking = None
        try:
            report = fut.result()
        except Exception as e:
            self._check_failed(now, day, sha, f"{type(e).__name__}: {_short(str(e))}")
            return
        day.checked.add(sha)
        day.check_next = None
        day.reports.append(report)
        log_event(
            log,
            logging.INFO if report.ok else logging.WARNING,
            SERVICE,
            "master_krx_checked",
            self._tag(now),
            krx_date=day.target.isoformat(),
            master_sha=sha[:12],
            ok=report.ok,
            compared=report.compared,
            krx_strikes=report.krx_strikes,
            missing=report.missing,
            new_strikes=report.new_strikes,
            krx_only=len(report.krx_only),
            master_only=len(report.master_only),
        )
        if not report.ok:
            detail = f"{report.describe()} (마스터 sha {sha[:12]})"
            self._emit(now, "master_krx_missing", detail, "warning")

    def _tag(self, now: datetime) -> tuple[date | None, str | None] | None:
        if self._tagger is None:
            return None
        try:
            return self._tagger(now)
        except Exception:  # 태깅 실패로 로그가 사라지지 않게
            return None

    def _check_failed(self, now: datetime, day: _Day, sha: str, detail: str) -> None:
        day.check_next = now + timedelta(seconds=self.cfg.retry_s)
        again = f"{self.cfg.retry_s / 60:g}분 뒤 다시"
        msg = f"마스터(sha {sha[:12]}) ⊇ KRX {day.target} 대조 실패 — {detail} — {again}"
        self._emit(now, "master_krx_check_failed", msg, "warning")

    def _emit(self, now: datetime, kind: str, detail: str, severity: Severity) -> None:
        try:
            self._health.emit(HealthEvent(kind, detail, now, severity, service=SERVICE))
        except Exception as e:
            log_event(log, logging.ERROR, SERVICE, "health_failed", error=type(e).__name__)
