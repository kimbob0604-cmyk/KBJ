"""scheduler KIS 선물 분봉 일일 적재 (docs/phase1_design.md §8·§9, PLAN §3 #17·§4.4).

세션 하나(주간 D 또는 D 에 시작한 밤)의 근월물 분봉을 KIS 에서 받아 `minute_bars` 에 쓴다. 무결측
판정(설계 §9)이 선물 체결 공백을 분봉과 대조하는 입력이다.

- 창(`Window`) — 같은 날짜 입력으로 과거로 이어 가는 조회 묶음 (설계 §8, probe_results #17b 실측):
  - 주간: 시장 `F`, (D, 160000) 부터. 08:45 에 닿으면 끝
  - 야간: 시장 `CM`, **(야간 시작일 D, 300000) 부터 한 줄로** — 24~30시 확장 표기를 입력으로도 받고,
    확장 시각으로 이어 가면 자정을 넘어 18~24시로 이어진다(2026-09-29 실측: (09-28, 300000) →
    30:00~27:58, (09-28, 240400) → 24:04~22:23). 18:00 에 닿으면 끝. 금요일 밤도 날짜는 금요일
  - (다음 날, 05:59:59)·(다음 날, 달력 시각) 입력은 쓰지 않는다 — (09-29, 055959)·(09-29, 040300)
    이 09-28 밤이 아니라 09-22 밤을 줬다(#17b)
- 다음 조회는 직전까지 받은 이 세션의 가장 이른 봉 − 1분부터(`continuation_hour`) — 날짜는 창의
  anchor 그대로, 시각은 anchor 자정부터 잰 HHMMSS(야간은 18~29시 확장 표기). 실측(#17b): 주간
  (D, 135300)·야간 (D, 221800)·(D, 280300) 모두 겹침·빈틈 없이 이어짐
- 창을 끝내는 것: 세션 시작에 닿음(`complete`), 더 이른 이 세션 봉이 없음 — 새 봉이 없거나 앞
  세션 봉으로 넘어감(`exhausted`), 창 호출 상한(`capped` — 주간 5·야간 8, **[확인 필요]**).
  상한은 성공한 조회 수다. 실패(HTTP·KIS 오류·저장 실패·레이트리미터 P4 건너뜀)는 그 시도를
  멈추고, 다음 시도가 멈춘 곳에서 이어 간다(재시도 간격·횟수는 부르는 쪽 몫)
- 이 세션 봉만 쓴다: 봉의 날짜 필드가 세션 날짜(주간 D, 야간은 야간 시작일 D — 24~30시 표기)인
  것. 시각 변환(UTC·trade_date·session)은 `MinuteBarRecord.from_kis` → core.calendar. 쓰기는
  `(code, market, ts)` DO UPDATE 라 다시 받아도 행이 늘지 않는다(멱등)
- 근월물 코드: 마스터(Redis `kis:master` — scheduler 가 둔 것)의 코스피200 선물 중 결제월 순으로
  최종거래일이 지나지 않은 첫 종목. 최종거래일은 poller 체인 문맥(KIS 값, `poller:chain_context`)이
  있으면 그것, 없으면 캘린더 계산(ws-gateway 대체와 같은 규칙). 주간은 최종거래일 ≥ D, 밤은 > D
  (만기일 15:20 뒤 밤은 차월물). 분기 만기일 주간은 만기 종목과 차월물 둘 다 — 15:20 뒤 근월물이
  차월물이다(ws-gateway 구독 전환과 같게) **[확인 필요]**
- 모든 조회는 공용 KisClient(레이트리미터 P4, 토큰은 auth 캐시 읽기만)로. 받은 본문은 원문 그대로
  `raw_messages`(kis_rest)에, 검증 실패 행은 `quarantine` 에 — 세션 태그(귀속 거래일, 세션)로
- 부르기 전에 입력 (날짜, 시각)이 지금보다 뒤면 부르지 않는다(미래 날짜 금지 — 실패로 본다)
- 하루 구동(`MinuteDaily`, scheduler 가 1초마다 step): 거래일 T 의 16:00~17:50(POST_DAY)에 T 주간,
  06:10~08:00(IDLE)에 T 로 귀속되는 밤(열린 밤만 — 휴장 전날 밤은 없다). 한 시도는 작업 스레드에서,
  실패하면 5분 뒤 멈춘 곳부터 다시 (시각·횟수는 `MinuteConfig` — 설계 §12 [확인 필요])
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator
from redis import Redis

from config.settings import Settings
from core.calendar import (
    DAY_START,
    KST,
    NIGHT_END,
    NIGHT_START,
    TradingCalendar,
    night_session_opens,
)
from data.kis.master import MasterRow, parse_master
from data.kis.models import MinuteBar, output_rows, parse_rows
from data.kis.ratelimit import Clock, Priority, RateLimitTimeout, RedisRateLimiter
from data.kis.rest import MINUTE_PATH, MINUTE_TR, KisClient, KisResponse, minute_chart_params
from data.store import MinuteBarRecord
from services.auth.health import HealthEvent, HealthSink, Severity
from services.auth.service import reader
from services.bus import ChainContextSnapshot, MasterSnapshot
from services.chain_feed import apply_context
from services.poller.context import ChainContext, kospi200_futures
from services.poller.records import QuarantineRecord
from services.recorder.envelope import RawEnvelope, wrap
from services.runtime import log_event, run_in_thread, utcnow

SERVICE = "scheduler"
DETAIL_MAX = 160

Market = Literal["F", "CM"]
Session = Literal["day", "night"]
WindowName = Literal["day", "night"]
Outcome = Literal["pending", "complete", "exhausted", "capped"]

# 첫 조회 시각 (설계 §8·probe_results #17·#17b — 실측한 입력)
DAY_FIRST_HOUR = "160000"  # probe DAY_END F — 15:45 마감 봉까지 온다
NIGHT_FIRST_HOUR = "300000"  # (D, 30:00:00) → D 밤 30:00 봉부터(확장 표기 입력, #17b)
WINDOW_KO: dict[WindowName, str] = {"day": "주간", "night": "야간"}

log = logging.getLogger("services.scheduler.minute")


class MinuteConfig(BaseModel):
    """분봉 적재 설정. 시각은 KST. 값은 설계 §8 과 §12 [확인 필요] 기본값."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 주간: POST_DAY(15:45~17:50) 안. 첫 입력 (D, 160000) 이 미래가 아니게 16:00 부터
    day_start: time = time(16, 0)
    day_end: time = time(17, 50)
    # 야간: 귀속 거래일 T 의 IDLE 06:10 (설계 §8) ~ PRE_DAY 08:00 (주간 수집 전)
    night_start: time = time(6, 10)
    night_end: time = time(8, 0)
    day_max_calls: int = Field(default=5, ge=1)  # 주간 약 411봉 / 102 → 5회
    night_max_calls: int = Field(default=8, ge=1)  # 밤 12시간 721봉 / 102 → 8회
    retry_s: float = Field(default=300.0, gt=0)  # 실패한 시도 뒤 다시 부를 간격
    # 세션 하나를 시도하는 횟수 상한 — 5분 × 12 = 한 시간 안에 KIS·DB 가 돌아오면 창 안에서
    # 따라잡는다. 실패한 시도는 첫 실패 조회에서 멈추므로 헛 호출은 시도마다 1건 이하
    max_attempts: int = Field(default=12, ge=1)
    p4_timeout_s: float = Field(default=1.0, ge=0)  # 레이트리미터 허가를 기다리는 한도(P4)
    # 더 이른 봉이 없어 끝난 창의 첫 봉이 세션 시작보다 이만큼 넘게 늦으면 health(warning)
    late_start_warn_s: float = Field(default=600.0, ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> MinuteConfig:
        if not (time(16, 0) <= self.day_start < self.day_end):
            raise ValueError("16:00 <= day_start < day_end — 첫 입력 160000 이 미래가 아니게")
        if not (NIGHT_END < self.night_start < self.night_end):
            raise ValueError("06:00 < night_start < night_end — 밤이 끝난 뒤에")
        return self


# ── 창과 이어 가기 ───────────────────────────────────────────────────────────


def _kst(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=KST)


@dataclass(frozen=True)
class Window:
    """같은 날짜 입력으로 과거로 이어 가는 조회 묶음 (모듈 설명)."""

    code: str
    market: Market
    name: WindowName
    anchor: date  # FID_INPUT_DATE_1 — 이 창의 모든 조회에 같은 날짜
    first_hour: str  # 첫 조회 FID_INPUT_HOUR_1
    target: date  # 이 세션 봉의 날짜 필드 (주간 D, 야간 = 야간 시작일 D)
    start: datetime  # KST — 이 시각 이하의 봉을 받으면 창 끝
    max_calls: int

    @property
    def label(self) -> str:
        return f"{self.code} {self.market} {WINDOW_KO[self.name]}"


def day_windows(code: str, d: date, cfg: MinuteConfig) -> list[Window]:
    """주간 D — (D, 160000) 부터 08:45 까지."""
    return [Window(code, "F", "day", d, DAY_FIRST_HOUR, d, _kst(d, DAY_START), cfg.day_max_calls)]


def night_windows(code: str, d: date, cfg: MinuteConfig) -> list[Window]:
    """D 에 시작한 밤 — (D, 300000) 부터 확장 시각으로 18:00 까지 한 줄(#17b). 금요일 밤도 D."""
    return [
        Window(
            code, "CM", "night", d, NIGHT_FIRST_HOUR, d, _kst(d, NIGHT_START), cfg.night_max_calls
        )
    ]


# 창마다 입력 시각(anchor 자정부터 잰 시간)이 들 수 있는 범위 — 주간 08:45~15:45, 야간 18~30시
_HOURS: dict[WindowName, tuple[int, int]] = {"day": (0, 24), "night": (18, 30)}


def continuation_hour(window: Window, earliest: datetime) -> str | None:
    """다음 조회의 FID_INPUT_HOUR_1 — 가장 이른 봉 − 1분. 창 시작보다 앞이면 None(창 끝).

    날짜 입력은 늘 창의 anchor 그대로, 시각은 anchor 자정부터 잰 HHMMSS — 주간 13:53 → '135300',
    야간 22:18 → '221800', 야간 28:06(= D+1 04:06) → **확장 표기 '280600'**, 24:00 봉 다음은
    '235900' (2026-09-29 실측 #17b — (D, 280300)·(D, 221800) 이 겹침·빈틈 없이 이어짐). 시각이
    창의 범위(주간 0~24시·야간 18~30시) 밖이면 ValueError(창 밖).
    """
    nxt = earliest.astimezone(KST) - timedelta(minutes=1)
    if nxt < window.start:
        return None
    offset = nxt - _kst(window.anchor, time(0))
    lo, hi = _HOURS[window.name]
    secs = int(offset.total_seconds())
    if offset.total_seconds() != secs or not (lo * 3600 <= secs < hi * 3600):
        raise ValueError(f"{window.label}: 이어 갈 시각 {nxt:%Y-%m-%d %H:%M} 이 창 밖이다")
    h, rem = divmod(secs, 3600)
    return f"{h:02d}{rem // 60:02d}{rem % 60:02d}"


def input_at(anchor: date, hour: str) -> datetime:
    """조회 입력 (날짜, HHMMSS) → 달력 시각(KST) — 미래 날짜 검사용. 24~30시 확장 표기는 다음 날."""
    h, m, s = int(hour[:2]), int(hour[2:4]), int(hour[4:])
    if not (0 <= h <= 30 and 0 <= m < 60 and 0 <= s < 60):
        raise ValueError(f"입력 시각 형식 밖: {hour!r}")
    return _kst(anchor, time(0)) + timedelta(hours=h, minutes=m, seconds=s)


def bar_start(bar: MinuteBar) -> datetime:
    """봉의 (날짜, HHMMSS) 를 그대로 더한 시각(KST) — 야간 24~30시 표기는 다음 날로 넘어간다."""
    s, h = bar.stck_bsop_date, bar.stck_cntg_hour
    d = date(int(s[:4]), int(s[4:6]), int(s[6:]))
    return _kst(d, time(0)) + timedelta(hours=int(h[:2]), minutes=int(h[2:4]), seconds=int(h[4:]))


# ── 근월물 ───────────────────────────────────────────────────────────────────


def futures_last_dates(
    rows: Sequence[MasterRow], cal: TradingCalendar, context: ChainContextSnapshot | None
) -> list[tuple[str, date | None]]:
    """마스터의 코스피200 선물(결제월 순) → (코드, 최종거래일). 최종거래일은 poller 문맥(KIS)이
    있으면 그것, 없으면 캘린더 계산 — `ChainContext.futures_last_trade_date` 와 같은 규칙."""
    ctx = ChainContext(rows)
    if context is not None:
        apply_context(ctx, context)
    return [(r.code, ctx.futures_last_trade_date(r.code, cal)) for r in kospi200_futures(rows)]


def near_month_codes(
    futures: Sequence[tuple[str, date | None]], session: Session, d: date
) -> tuple[str, ...]:
    """세션의 근월물 코드. futures 는 결제월 순 (코드, 최종거래일 — None 이면 살아 있다고 본다).

    주간 D: 최종거래일 ≥ D 인 첫 종목 — 그날이 최종거래일이면(분기 만기일) 차월물도.
    D 에 시작한 밤: 최종거래일 > D 인 첫 종목(만기일 15:20 뒤 밤은 차월물).
    """
    if session == "night":
        return tuple(c for c, last in futures if last is None or last > d)[:1]
    alive = [(c, last) for c, last in futures if last is None or last >= d]
    if not alive:
        return ()
    if alive[0][1] == d and len(alive) > 1:
        return (alive[0][0], alive[1][0])
    return (alive[0][0],)


# ── 진행 ─────────────────────────────────────────────────────────────────────


@dataclass
class WindowRun:
    """창 하나의 진행 — 시도가 실패해도 남아 다음 시도가 이어 간다."""

    window: Window
    calls: int = 0  # 성공한 조회 수 (상한 대상)
    earliest: datetime | None = None  # 받은 이 세션 봉 중 가장 이른 시각(UTC)
    bars: set[datetime] = field(default_factory=set[datetime])  # 받은 봉 시각(UTC, 겹침 없이)
    invalid: int = 0  # 검증·변환 실패로 quarantine 에 둔 행
    outcome: Outcome = "pending"
    next_hour: str | None = None  # 이어 갈 입력 시각 (None 이면 첫 조회)

    @property
    def done(self) -> bool:
        return self.outcome != "pending"

    @property
    def hour(self) -> str:
        return self.next_hour or self.window.first_hour

    def late_by(self) -> timedelta | None:
        """가장 이른 봉이 창 시작보다 늦은 만큼 (봉이 없으면 None)."""
        return None if self.earliest is None else self.earliest - self.window.start

    def describe(self) -> str:
        w = self.window
        first = "-" if self.earliest is None else f"{self.earliest.astimezone(KST):%m-%d %H:%M}"
        what = {
            "pending": "진행 중",
            "complete": "시작까지",
            "exhausted": "더 이른 봉 없음",
            "capped": "호출 상한",
        }[self.outcome]
        bad = f"·검증 실패 {self.invalid}" if self.invalid else ""
        return f"{w.label} {len(self.bars)}봉{bad} {self.calls}회 첫 봉 {first}({what})"


@dataclass
class SessionJob:
    """세션 하나(주간 D 또는 D 에 시작한 밤)의 적재 — 시도·재시도는 부르는 쪽(MinuteDaily)이."""

    session: Session
    start_date: date  # D — 세션이 시작한 달력일
    trade_date: date  # 귀속 거래일 (주간 D, 야간 next_trading_day(D))
    deadline: datetime  # 이때가 지나면 다시 시도하지 않는다
    next_try: datetime
    attempts: int = 0
    runs: list[WindowRun] | None = None  # 근월물 코드를 정한 뒤 창마다
    finished: bool = False
    # 끝난 결과 — loaded · partial · missing (무결측 판정이 선물 체결 대조 가능 여부로 본다)
    status: Literal["loaded", "partial", "missing"] | None = None

    @property
    def key(self) -> tuple[Session, date]:
        return self.session, self.start_date

    @property
    def tag(self) -> tuple[date, Session]:
        return self.trade_date, self.session

    @property
    def done(self) -> bool:
        return self.runs is not None and all(r.done for r in self.runs)

    def describe(self) -> str:
        name = "주간" if self.session == "day" else "야간"
        head = f"{self.start_date} {name}(귀속 {self.trade_date})"
        if not self.runs:
            return head
        return f"{head} " + "; ".join(r.describe() for r in self.runs)


@dataclass(frozen=True)
class Attempt:
    """한 번의 시도 결과. ok 면 모든 창이 끝났다."""

    ok: bool
    calls: int  # 이번 시도에 보낸 요청 (실패 포함)
    detail: str = ""  # 실패 사유 (가린 짧은 문구)


class _PageFailed(Exception):
    def __init__(self, detail: str, *, sent: bool) -> None:
        super().__init__(detail)
        self.detail = detail
        self.sent = sent


# ── KIS 클라이언트 ───────────────────────────────────────────────────────────


def reader_kis_client(
    settings: Settings,
    redis: Redis,
    *,
    clock: Clock | None = None,
    now: Callable[[], datetime] = utcnow,
    transport: httpx.BaseTransport | None = None,
) -> KisClient:
    """scheduler 의 KIS REST 클라이언트 — 앱키당 하나인 Redis 레이트리미터(poller·ws-gateway 와
    같은 버킷, 설계 §3)와 auth 가 Redis 에 둔 토큰 읽기만(발급하지 않는다 — PLAN §2.5). 토큰이
    없으면 그 조회가 실패하고 다음 시도에 다시 읽는다. KIS_APP_KEY 가 없으면 ValueError.

    clock 은 레이트리미터 시계(기본 호스트 시계 — 모든 프로세스가 같은 호스트 시계), now 는 토큰
    만료 판정 시계, transport 는 시험용 가짜 KIS.
    """
    key = settings.kis_app_key
    if key is None:
        raise ValueError("KIS_APP_KEY 가 없다")
    limiter = RedisRateLimiter(redis, key.get_secret_value(), clock=clock)
    return KisClient(
        settings,
        token_provider=reader(redis, settings, now=now),
        rate_limiter=limiter,
        transport=transport,
    )


# ── 한 번의 시도 ─────────────────────────────────────────────────────────────


class MinuteStore(Protocol):
    def write_minute_bars(self, rows: Sequence[MinuteBarRecord]) -> None: ...

    def write_raw(self, envelopes: Sequence[RawEnvelope]) -> None: ...

    def write_quarantine(self, rows: Sequence[QuarantineRecord]) -> None: ...


ContextSource = Callable[[], ChainContextSnapshot | None]


def _no_context() -> ChainContextSnapshot | None:
    return None


def _short(text: str) -> str:
    lines = text.strip().splitlines()
    msg = lines[0] if lines else ""
    return msg if len(msg) <= DETAIL_MAX else msg[: DETAIL_MAX - 1] + "…"


class MinuteLoader:
    """세션 하나를 한 번 시도한다 — 작업 스레드에서 돈다. 결과만 돌려주고 health 는 부르는 쪽이."""

    def __init__(
        self,
        kis: KisClient,
        store: MinuteStore,
        calendar: TradingCalendar,
        *,
        config: MinuteConfig | None = None,
        now: Callable[[], datetime] = utcnow,
        context: ContextSource = _no_context,
    ) -> None:
        self._kis = kis
        self._store = store
        self._cal = calendar
        self.cfg = config or MinuteConfig()
        self._now = now
        self._context = context
        self._lock = threading.Lock()  # 한 번에 한 시도

    def run(self, job: SessionJob, master: MasterSnapshot | None) -> Attempt:
        """끝나지 않은 창을 차례로 이어 간다. 실패하면 거기서 멈추고 진행은 job 에 남긴다."""
        with self._lock:
            if job.runs is None:
                try:
                    job.runs = self.plan(job, master)
                except LookupError as e:
                    return Attempt(False, 0, _short(str(e)))
            sent = 0
            for run in job.runs:
                while not run.done:
                    try:
                        self._page(job, run)
                    except _PageFailed as e:
                        self._log_failure(job, run, e.detail)
                        return Attempt(False, sent + int(e.sent), e.detail)
                    sent += 1
            return Attempt(True, sent)

    def plan(self, job: SessionJob, master: MasterSnapshot | None) -> list[WindowRun]:
        """근월물 코드를 정하고 창을 만든다. 정하지 못하면 LookupError."""
        if master is None:
            raise LookupError("마스터가 없다 — 근월물 코드를 모른다")
        try:
            rows = parse_master(master.text)
        except ValueError as e:
            raise LookupError(f"마스터 파싱 실패: {_short(str(e))}") from None
        futures = futures_last_dates(rows, self._cal, self._read_context())
        codes = near_month_codes(futures, job.session, job.start_date)
        if not codes:
            raise LookupError(f"마스터에 살아 있는 코스피200 선물이 없다({len(futures)}종목)")
        windows: list[Window] = []
        for code in codes:
            if job.session == "day":
                windows += day_windows(code, job.start_date, self.cfg)
            else:
                windows += night_windows(code, job.start_date, self.cfg)
        return [WindowRun(w) for w in windows]

    def _read_context(self) -> ChainContextSnapshot | None:
        try:
            return self._context()
        except Exception as e:  # 문맥이 없으면 캘린더 계산으로 — 적재를 막지 않는다
            err = type(e).__name__
            log_event(log, logging.WARNING, SERVICE, "minute_context_failed", error=err)
            return None

    # ── 조회 한 번 ──

    def _page(self, job: SessionJob, run: WindowRun) -> None:
        w = run.window
        hour = run.hour
        if input_at(w.anchor, hour) > self._now():
            raise _PageFailed(f"{w.label}: 미래 입력 {w.anchor} {hour} — 부르지 않았다", sent=False)
        params = minute_chart_params(w.market, w.code, w.anchor, hour)
        try:
            resp = self._kis.get(
                MINUTE_PATH,
                MINUTE_TR,
                params,
                priority=Priority.P4,
                timeout=self.cfg.p4_timeout_s,
            )
        except RateLimitTimeout as e:
            raise _PageFailed(f"{w.label}: P4 건너뜀({e.reason.name})", sent=False) from None
        except Exception as e:  # 전송·토큰 오류 — 문구는 가린다
            detail = _short(self._redact(f"{w.label}: {type(e).__name__}: {e}"))
            raise _PageFailed(detail, sent=True) from None
        received = self._now()
        key = "minute_bars|" + "&".join(f"{k}={v}" for k, v in params.items())
        self._raw(job, resp, received, key)
        if not resp.ok:
            msg = self._redact(f"HTTP {resp.status} {resp.msg_cd} {resp.body.get('msg1')}")
            raise _PageFailed(_short(f"{w.label}: {msg}"), sent=True)
        target, others, bad = self._parse(job, w, resp.body, received, key)
        try:
            self._store.write_minute_bars(target)
        except Exception as e:  # 진행을 넘기지 않는다 — 다음 시도가 같은 조회부터
            raise _PageFailed(f"{w.label}: 저장 실패 — {type(e).__name__}", sent=True) from None
        self._quarantine(bad)
        run.invalid += len(bad)
        run.calls += 1
        new = self._advance(run, target, others)
        self._log_page(job, run, hour, len(target), new, len(others), len(bad))

    def _parse(
        self, job: SessionJob, w: Window, body: dict[str, Any], received: datetime, key: str
    ) -> tuple[list[MinuteBarRecord], list[datetime], list[QuarantineRecord]]:
        """→ (이 세션 봉 레코드, 다른 세션 봉 시각(KST), 격리할 행)."""
        raws = output_rows(body, "output2")
        bars, errors = parse_rows(MinuteBar, raws)
        bad = [self._bad(job, received, key, raws[e.index], e.error) for e in errors]
        want = f"{w.target:%Y%m%d}"
        target: list[MinuteBarRecord] = []
        others: list[datetime] = []
        for bar in bars:
            try:
                at = bar_start(bar)
                if bar.stck_bsop_date != want:
                    others.append(at)
                    continue
                target.append(
                    MinuteBarRecord.from_kis(
                        bar,
                        code=w.code,
                        market=w.market,
                        received_at=received,
                        calendar=self._cal,
                    )
                )
            except ValueError as e:  # 없는 날짜·세션 밖 시각 등 — 그 행만 격리
                bad.append(
                    self._bad(job, received, key, bar.model_dump(mode="json"), _short(str(e)))
                )
        return target, others, bad

    def _advance(
        self, run: WindowRun, target: list[MinuteBarRecord], others: list[datetime]
    ) -> int:
        """받은 봉으로 창 진행을 고치고 새로 받은(더 이른) 봉 수를 돌려준다."""
        w = run.window
        times = [r.ts for r in target]
        prev = run.earliest
        new = sum(1 for t in times if prev is None or t < prev)
        run.bars.update(times)
        if times and (prev is None or min(times) < prev):
            run.earliest = min(times)
        earliest = run.earliest
        if new == 0 or earliest is None:
            run.outcome = "exhausted"  # 더 이른 이 세션 봉이 오지 않는다
        elif earliest <= w.start:
            run.outcome = "complete"
        elif any(o < earliest for o in others):
            run.outcome = "exhausted"  # 앞 세션 봉으로 넘어갔다 — 이 세션엔 더 이른 봉이 없다
        elif run.calls >= w.max_calls:
            run.outcome = "capped"
        else:
            try:
                nxt = continuation_hour(w, earliest)
            except ValueError as e:  # 봉이 창 범위 밖 — 이어 갈 입력을 모른다. 창은 여기까지
                log_event(log, logging.WARNING, SERVICE, "minute_bars_off_window", error=str(e))
                run.outcome = "exhausted"
                return new
            if nxt is None:
                run.outcome = "complete"
            else:
                run.next_hour = nxt
        return new

    # ── 기록 ──

    def _redact(self, text: str) -> str:
        for s in self._kis.secrets():
            if s:
                text = text.replace(s, "***")
        return text

    def _bad(
        self, job: SessionJob, received: datetime, key: str, payload: Any, error: str
    ) -> QuarantineRecord:
        return QuarantineRecord(
            ts=received,
            trade_date=job.trade_date,
            session=job.session,
            tr_id=MINUTE_TR,
            key=key,
            payload=dict(payload),
            error=error,
        )

    def _raw(self, job: SessionJob, resp: KisResponse, received: datetime, key: str) -> None:
        tag = job.tag

        def tagger(_: datetime) -> tuple[date, Session]:
            return tag

        env = wrap(
            resp.body,
            source="kis_rest",
            tr_id=MINUTE_TR,
            received_at=received,
            tagger=tagger,
            key=key,
        )
        try:
            self._store.write_raw([env])
        except Exception as e:  # 원문 녹화 실패가 적재를 막지 않는다
            log_event(
                log, logging.WARNING, SERVICE, "minute_raw_failed", job.tag, error=type(e).__name__
            )

    def _quarantine(self, rows: list[QuarantineRecord]) -> None:
        if not rows:
            return
        try:
            self._store.write_quarantine(rows)
        except Exception as e:
            log_event(
                log,
                logging.WARNING,
                SERVICE,
                "minute_quarantine_failed",
                (rows[0].trade_date, rows[0].session),
                rows=len(rows),
                error=type(e).__name__,
            )

    def _log_page(
        self, job: SessionJob, run: WindowRun, hour: str, bars: int, new: int, other: int, bad: int
    ) -> None:
        w = run.window
        earliest = run.earliest.astimezone(KST).isoformat() if run.earliest else None
        log_event(
            log,
            logging.INFO,
            SERVICE,
            "minute_bars_page",
            job.tag,
            code=w.code,
            market=w.market,
            window=w.name,
            anchor=w.anchor.isoformat(),
            hour=hour,
            bars=bars,
            new=new,
            other=other,
            invalid=bad,
            calls=run.calls,
            earliest=earliest,
            outcome=run.outcome,
        )

    def _log_failure(self, job: SessionJob, run: WindowRun, detail: str) -> None:
        w = run.window
        log_event(
            log,
            logging.WARNING,
            SERVICE,
            "minute_bars_page_failed",
            job.tag,
            code=w.code,
            market=w.market,
            window=w.name,
            anchor=w.anchor.isoformat(),
            hour=run.hour,
            detail=detail,
        )


# ── 하루 구동 (Scheduler.step 이 1초마다) ─────────────────────────────────────


class MinuteRuns(Protocol):
    def run(self, job: SessionJob, master: MasterSnapshot | None) -> Attempt: ...


class Submit(Protocol):
    """일을 작업 스레드로 넘기고 Future 를 돌려준다(시험은 그 자리에서 부르는 것을 넣는다)."""

    def __call__[T](self, fn: Callable[[], T], /) -> Future[T]: ...


def minute_thread_submit[T](fn: Callable[[], T], /) -> Future[T]:
    return run_in_thread(fn, name="minute-bars")


NightOpens = Callable[[date, TradingCalendar], bool]


class MinuteDaily:
    """거래일 T 의 창에서 세션 하나를 적재한다 — 상태가 아니라 KST 날짜·시각으로 정한다.

    - 주간: T 의 `day_start`~`day_end`(16:00~17:50, POST_DAY 안)에 T 주간
    - 야간: T 의 `night_start`~`night_end`(06:10~08:00, IDLE)에 T 로 귀속되는 밤 — D =
      prev_trading_day(T) 의 밤이 열렸을 때만(`night_session_opens`). 금요일 밤은 월요일 06:10,
      월요일이 휴장이면 화요일 06:10(지금 규칙으로는 그 밤이 열리지 않아 적재도 없다 — 휴장 전날
      밤과 같다). 토요일 06:10 엔 부르지 않는다(비거래일 — (월, 05:59:59) 는 미래다)
    - 한 시도 = `MinuteLoader.run`(작업 스레드). step 은 끝났는지만 보고 기다리지 않는다
    - 다 받으면 info `minute_bars_loaded`, 창이 상한에 닿았거나 봉이 없거나 첫 봉이 세션 시작보다
      `late_start_warn_s` 넘게 늦으면 warning `minute_bars_partial`
    - 실패면 warning `minute_bars_failed` + `retry_s` 뒤 멈춘 곳부터 다시. `max_attempts` 번을 다
      썼거나 다음 시도가 창 끝을 넘으면 그 세션은 그만 + warning `minute_bars_missing`
    - 재기동하면 창 안에서 그 세션을 처음부터 다시 받는다(멱등 — 행은 늘지 않는다). 창을 통째로
      놓친 세션(서비스가 멈춰 있던 날)은 되받지 않는다 — 필요하면 수동 백필
    """

    def __init__(
        self,
        loader: MinuteRuns,
        calendar: TradingCalendar,
        health: HealthSink,
        *,
        config: MinuteConfig | None = None,
        submit: Submit = minute_thread_submit,
        night_opens: NightOpens = night_session_opens,
    ) -> None:
        self._loader = loader
        self._cal = calendar
        self._health = health
        self.cfg = config or MinuteConfig()
        self._submit = submit
        self._night_opens = night_opens
        self.job: SessionJob | None = None  # 지금 창의 세션
        self.jobs: list[SessionJob] = []  # 만든 세션 (시험·진단용 — 최근 것만)
        self._inflight: tuple[SessionJob, Future[Attempt]] | None = None
        self._closed_noted: date | None = None

    @property
    def busy(self) -> bool:
        return self._inflight is not None

    def session_job(self, session: Session, start_date: date) -> SessionJob | None:
        """세션 하나(주간 D 또는 D 에 시작한 밤)의 적재 — 만들지 않았으면 None (최근 8개만 둔다)."""
        return next((j for j in reversed(self.jobs) if j.key == (session, start_date)), None)

    def step(self, now: datetime, master: MasterSnapshot | None = None) -> None:
        """끝난 시도를 적용하고, 때가 되면 다음 시도를 작업 스레드에 넘긴다. master 는 지금 쓰는
        마스터(scheduler 가 Redis 에 둔 것) — 근월물 코드를 정하는 데 쓴다."""
        self._collect(now)
        job = self._current(now)
        if job is None or job.finished or self._inflight is not None or now < job.next_try:
            return
        self._start(now, job, master)

    def _current(self, now: datetime) -> SessionJob | None:
        k = now.astimezone(KST)
        d, t = k.date(), k.time()
        if not self._cal.is_trading_day(d):
            return None
        cfg = self.cfg
        if cfg.day_start <= t < cfg.day_end:
            return self._job(now, "day", d, d, _kst(d, cfg.day_end))
        if cfg.night_start <= t < cfg.night_end:
            start = self._cal.prev_trading_day(d)
            if not self._night_opens(start, self._cal):
                self._note_closed(d, start)
                return None
            return self._job(now, "night", start, d, _kst(d, cfg.night_end))
        return None

    def _job(
        self, now: datetime, session: Session, start: date, trade_date: date, deadline: datetime
    ) -> SessionJob:
        if self.job is None or self.job.key != (session, start):
            self.job = SessionJob(session, start, trade_date, deadline=deadline, next_try=now)
            self.jobs = [*self.jobs[-7:], self.job]
        return self.job

    def _note_closed(self, d: date, start: date) -> None:
        if self._closed_noted == d:
            return
        self._closed_noted = d
        log_event(
            log,
            logging.INFO,
            SERVICE,
            "minute_night_closed",
            night_start=start.isoformat(),
            trade_date_of_night=d.isoformat(),
        )

    def _start(self, now: datetime, job: SessionJob, master: MasterSnapshot | None) -> None:
        job.attempts += 1
        loader = self._loader
        try:
            fut = self._submit(lambda: loader.run(job, master))
        except Exception as e:  # 스레드를 못 띄웠다 — 실패처럼 다시
            self._failed(now, job, f"시작 실패: {type(e).__name__}")
            return
        self._inflight = (job, fut)
        self._collect(now)  # 벌써 끝났으면(그 자리에서 부르는 submit) 이 step 에 적용

    def _collect(self, now: datetime) -> None:
        if self._inflight is None:
            return
        job, fut = self._inflight
        if not fut.done():
            return
        self._inflight = None
        try:
            res = fut.result()
        except Exception as e:  # run 은 실패를 결과로 돌려주지만, 혹시
            self._failed(now, job, f"적재 오류: {type(e).__name__}")
            return
        if res.ok:
            self._finished(now, job)
        else:
            self._failed(now, job, res.detail)

    def _finished(self, now: datetime, job: SessionJob) -> None:
        job.finished = True
        runs = job.runs or []
        late = timedelta(seconds=self.cfg.late_start_warn_s)
        problems: list[str] = []
        for r in runs:
            gap = r.late_by()
            if r.outcome == "capped":
                problems.append(f"{r.window.label} 호출 상한 {r.calls}회")
            elif gap is None:
                problems.append(f"{r.window.label} 봉 없음")
            elif r.outcome == "exhausted" and gap > late:
                problems.append(f"{r.window.label} 첫 봉이 {gap.total_seconds() / 60:g}분 늦다")
        job.status = "partial" if problems else "loaded"
        self._log_session(job, job.status)
        if problems:
            detail = f"분봉 {'·'.join(problems)} — {job.describe()}"
            self._emit(now, "minute_bars_partial", detail, "warning")
        else:
            self._emit(now, "minute_bars_loaded", f"분봉 {job.describe()}", "info")

    def _failed(self, now: datetime, job: SessionJob, detail: str) -> None:
        nxt = now + timedelta(seconds=self.cfg.retry_s)
        if job.attempts >= self.cfg.max_attempts or nxt >= job.deadline:
            job.finished = True
            job.status = "missing"
            self._log_session(job, "missing", detail=detail)
            msg = f"분봉 적재를 그만둔다(시도 {job.attempts}회) — {detail}; {job.describe()}"
            self._emit(now, "minute_bars_missing", msg, "warning")
            return
        job.next_try = nxt
        again = f"{self.cfg.retry_s / 60:g}분 뒤 멈춘 곳부터 다시"
        self._emit(now, "minute_bars_failed", f"분봉 {detail} — {again}", "warning")

    def _log_session(self, job: SessionJob, status: str, **extra: object) -> None:
        runs = job.runs or []
        log_event(
            log,
            logging.INFO if status == "loaded" else logging.WARNING,
            SERVICE,
            "minute_bars_session",
            job.tag,
            status=status,
            night_start=job.start_date.isoformat() if job.session == "night" else None,
            attempts=job.attempts,
            bars=sum(len(r.bars) for r in runs),
            calls=sum(r.calls for r in runs),
            windows={f"{r.window.code}:{r.window.name}": r.outcome for r in runs},
            **extra,
        )

    def _emit(self, now: datetime, kind: str, detail: str, severity: Severity) -> None:
        try:
            self._health.emit(HealthEvent(kind, detail, now, severity, service=SERVICE))
        except Exception as e:
            log_event(log, logging.ERROR, SERVICE, "health_failed", error=type(e).__name__)
