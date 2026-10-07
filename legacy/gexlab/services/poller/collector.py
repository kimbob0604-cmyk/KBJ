"""REST 수집 실행기 (docs/phase1_design.md §2·§3·§5, PLAN §4.4·§6.1·§6.5).

- `planner.plan` 이 고른 일을 하나씩 `KisClient`(레이트리미터 경유)로 부르고, 응답을
  `data/kis/models` 로 검증해 레코드로 바꿔 `Sink` 에 쓴다. 받은 원본은 모두 `RawEnvelope` 로 녹화
- 검증 실패 행은 quarantine 에 쓰고, 어느 행인지 알 수 있으면 quality=invalid 레코드를 남긴다.
  행·호출 하나의 실패가 주기를 멈추지 않는다(호출 단위 격리, 설계 §2)
- PLAN §6.1 이상치: 행 단위(가격·OI 음수, IV ≤ 0 또는 > 300%)는 invalid + quarantine, 전광판
  행사가 개수 급감(> 20%)은 그 스냅샷 전체 invalid + health
- 한도초과 `EGW00201`: `KisClient` 가 `on_rate_limited()` + 다음 토큰에서 1회 재시도한다. 여기서는
  리미터를 감싸 감속 신호마다 health 를 남기고, 재시도를 실제로 보냈는데 그것도 한도초과면 한 번
  더 알린다(재시도 허가를 못 받았으면 알리지 않는다 — 한도초과 응답은 하나뿐이다)
- 우선순위(설계 §3): P0·P1 기다림, P2·P3 짧게 기다렸다 다음 주기로, P4 는 토큰이 없으면 건너뜀.
  우리 호출 사이는 설계 속도(1/4초)만큼 띄운다 — 그래서 P4 는 자기 차례 토큰에서 시도하고,
  감속·다른 프로세스 경합으로 토큰이 모자랄 때 가장 먼저 빠진다
- 레코드 태그: 수신 시각의 `core.calendar.state_at`(장 전 준비 상태는 곧 열릴 세션). 세션이 끝난
  뒤 도착한 응답은 계획 시점 세션으로 남긴다
- 선물 기준가(ATM): 최종거래일 15:20 이 지난 종목(분기 만기일의 근월물)은 건너뛴다 — 15:20 뒤 선물
  전광판·단건 응답에서 차월물을 근월물로 잡는다. 만기 지난 종목의 행은 그대로 적재한다(원문 그대로)
- 한계: `KisClient` 는 마지막 응답만 돌려주므로 재시도 전 한도초과 응답 원문은 녹화되지 않는다
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from core.calendar import TradingCalendar, state_at
from core.chain import atm_strike, atm_window
from data.kis.master import MasterRow
from data.kis.models import (
    CallPutRow,
    FuturesBoardRow,
    InvestorRow,
    OptionListRow,
    PriceOutput,
    RowError,
    output_rows,
    parse_rows,
)
from data.kis.ratelimit import US, Clock, Priority, RateLimiter, RateLimitTimeout
from data.kis.rest import KisClient, KisResponse, redact
from services.poller.config import PollerConfig
from services.poller.context import (
    BoardSeen,
    ChainContext,
    ExpiryInfo,
    FuturesQuote,
    Series,
    Tag,
    board_covers,
    estimate_last_trade_date,
    near_month,
    session_tag,
)
from services.poller.planner import (
    Job,
    OptionListRun,
    Plan,
    RunState,
    from_us,
    option_rest,
    plan,
    to_us,
)
from services.poller.quality import ChainBook
from services.poller.records import (
    CallPut,
    ChainRecord,
    ExpiryRecord,
    FuturesRecord,
    HealthEvent,
    HealthLevel,
    InvestorRecord,
    QuarantineRecord,
)
from services.poller.sink import Sink
from services.recorder.envelope import SessionName, wrap

log = logging.getLogger("services.poller")

IV_MAX = Decimal(300)  # PLAN §6.1: IV ≤ 0 또는 > 300% 는 이상치 (0 은 KIS '값 없음')


class ObservedLimiter:
    """레이트리미터를 감싸 한도초과 신호(`on_rate_limited`)를 collector 에 알리고, 받은 허가를 센다.

    `KisClient.get` 은 한도초과면 신호를 보내고 다음 허가에서 1회 재시도하는데, 재시도 허가를 못
    받으면(`RateLimitTimeout`) 처음 응답을 그대로 돌려준다. 신호 뒤에 허가가 나갔는가
    (`sent_since_signal`)로 재시도를 실제로 보냈는지 가린다.
    """

    def __init__(self, inner: RateLimiter) -> None:
        self.inner = inner
        self.listeners: list[Callable[[float], None]] = []
        self.grants = 0  # 받은 허가 수 (= 보낸 요청 수)
        self._grants_at_signal = 0

    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        self.inner.acquire(priority, tr_id, timeout)
        self.grants += 1

    def on_rate_limited(self) -> float:
        self._grants_at_signal = self.grants
        rate = self.inner.on_rate_limited()
        for fn in self.listeners:
            fn(rate)
        return rate

    @property
    def sent_since_signal(self) -> bool:
        """마지막 한도초과 신호 뒤에 요청을 보냈는가."""
        return self.grants > self._grants_at_signal


@dataclass
class CollectorStats:
    executed: Counter[str] = field(default_factory=Counter[str])  # 정상 응답을 처리한 일(종류별)
    failed: Counter[str] = field(default_factory=Counter[str])  # 오류 응답·예외(종류별)
    skipped: Counter[Priority] = field(default_factory=Counter[Priority])  # 허가 대기 초과
    slowdowns: int = 0  # 한도초과 신호 수
    sink_errors: int = 0


class Collector:
    def __init__(
        self,
        kis: KisClient,
        sink: Sink,
        *,
        calendar: TradingCalendar,
        clock: Clock,
        config: PollerConfig | None = None,
        master: Iterable[MasterRow] = (),
    ) -> None:
        if kis.rate_limiter is None:
            # 설계 §2: 레이트리미터를 못 쓰면 KIS 호출을 멈춘다 — 한도 초과보다 결측이 낫다
            raise ValueError("poller 는 레이트리미터 없는 KisClient 를 쓰지 않는다")
        self._slowdowns: list[float] = []
        limiter = kis.rate_limiter
        if not isinstance(limiter, ObservedLimiter):
            limiter = kis.rate_limiter = ObservedLimiter(limiter)
        limiter.listeners.append(self._slowdowns.append)
        self._limiter = limiter
        self.kis = kis
        self.sink = sink
        self.cal = calendar
        self.clock = clock
        self.cfg = config or PollerConfig()
        self.ctx = ChainContext(master)
        self.runs = RunState()
        self.book = ChainBook()
        self.stats = CollectorStats()
        self.next_due_us: int | None = None
        self._pace_us = round(US / self.cfg.pace_rate)
        self._last_call_us: int | None = None
        self._fails: dict[str, int] = {}
        self._fallback_noted: dict[Series, Tag] = {}  # 캘린더 대체를 health 로 알린 세션
        self._held: list[QuarantineRecord] = []  # 이 일에서 격리한 행 — 끝에 한 번에 쓴다

    # ── 시각·계획 ──

    def now_us(self) -> int:
        return self.clock.now_us()

    def now(self) -> datetime:
        return from_us(self.now_us())

    def plan(self, now_us: int | None = None) -> Plan:
        t = self.now_us() if now_us is None else now_us
        ts = from_us(t)
        return plan(
            t,
            state_at(ts, self.cal),
            session_tag(ts, self.cal),
            self.ctx,
            self.runs,
            self.cfg,
            calendar=self.cal,
        )

    def set_master(self, rows: Iterable[MasterRow]) -> None:
        """scheduler 가 PRE_DAY·PRE_NIGHT 에 받은 마스터로 바꾼다.

        KIS 최종거래일을 아직 못 받은 시리즈는 재시도 시각을 기다리지 않고 새 코드로 곧 다시 부른다.
        """
        self.ctx.set_master(rows)
        self.runs.expiry_retry.clear()

    def chain_view(self, now: datetime | None = None) -> list[ChainRecord]:
        """최신 체인 행(품질은 지금 기준 — 설계 §5)."""
        ts = now or self.now()
        tag = session_tag(ts, self.cal)
        return self.book.view(ts, None if tag is None else tag[1])

    # ── 루프 ──

    def step(self) -> bool:
        """할 일 하나를 한다(또는 호출 간격을 기다린다). 할 일이 없으면 False."""
        now = self.now_us()
        if self._last_call_us is not None and now < self._last_call_us + self._pace_us:
            self.clock.sleep((self._last_call_us + self._pace_us - now) / US)
            return True
        p = self.plan(now)
        self.next_due_us = p.next_due_us
        if not p.jobs:
            return False
        self.execute(p.jobs[0])
        return True

    def run_until(self, end: datetime) -> None:
        end_us = to_us(end)
        idle_us = round(self.cfg.max_idle_s * US)
        while (now := self.now_us()) < end_us:
            if self.step():
                continue
            wake = min(end_us, now + idle_us, self.next_due_us or end_us)
            self.clock.sleep(max(wake - now, 1) / US)

    # ── 실행 ──

    def execute(self, job: Job) -> None:
        try:
            self._execute(job)
        finally:
            self._flush_quarantine(job)

    def _execute(self, job: Job) -> None:
        if job.request is None:
            if option_rest(job.tag, self.cfg):
                self._expiry_fallback(job, self.now(), job.tag, "마스터에 이 시리즈 코드가 없다")
            else:  # 설정대로 부르지 않았다 — 경고가 아니다
                reason = "야간 C: 옵션 단건을 부르지 않는다"
                self._expiry_fallback(job, self.now(), job.tag, reason, level="info")
            self._mark(job, ok=False)
            return
        req = job.request
        before = len(self._slowdowns)
        try:
            resp = self.kis.get(
                req.path,
                req.tr_id,
                req.as_dict(),
                priority=job.priority,
                timeout=self.cfg.timeout(job.priority),
            )
        except RateLimitTimeout:
            self.stats.skipped[job.priority] += 1  # P2·P3 다음 주기로, P4 건너뜀 (설계 §3)
            self._mark(job, ok=False, called=False)
            return
        except Exception as e:  # 호출 단위 격리 — 전송·토큰 오류가 루프를 멈추지 않는다
            self._last_call_us = self.now_us()
            ts = self.now()
            self._failed(job, ts, self._tag(ts, job), redact(f"{type(e).__name__}: {e}", self.kis))
            self._mark(job, ok=False)
            return
        self._last_call_us = self.now_us()
        ts = self.now()
        tag = self._tag(ts, job)
        self._record_raw(job, resp, ts, tag)
        if resp.rate_limited and len(self._slowdowns) > before and self._limiter.sent_since_signal:
            # KisClient 가 알린 뒤 재시도를 보냈고 그것도 한도초과 — 한 번 더 알린다. 재시도 허가를
            # 못 받았으면(P2·P3 대기 초과, P4 즉시 포기) 돌아온 건 처음 응답이라 다시 알리지 않는다
            self._limiter.on_rate_limited()
        for rate in self._slowdowns[before:]:
            self.stats.slowdowns += 1
            self._health(
                ts,
                tag,
                "rate_limited",
                "warning",
                "KIS 한도초과(EGW00201) — 감속",
                job=job.key,
                rate=rate,
            )
        if not resp.ok:
            self._failed(job, ts, tag, f"HTTP {resp.status} {resp.msg_cd} {resp.body.get('msg1')}")
            # 한도초과는 대상 탓이 아니다 — 받지 못한 것으로 보고 보강 2 순서를 넘기지 않는다
            self._mark(job, ok=False, called=not resp.rate_limited)
            return
        try:
            self._handle(job, resp.body, ts, tag)
        except Exception as e:  # 파서·핸들러 결함도 이 일에서 멈춘다
            self._health(
                ts,
                tag,
                "handler_error",
                "error",
                f"{job.kind} 처리 실패",
                job=job.key,
                error=f"{type(e).__name__}: {e}",
            )
        self._fails.pop(job.key, None)
        self.stats.executed[job.kind] += 1
        self._mark(job, ok=True)

    def _mark(self, job: Job, *, ok: bool, called: bool = True) -> None:
        """실행 기록. `called` = 호출이 대상에 대해 끝났는가(허가 대기 초과·한도초과는 아니다).

        보강 2 는 실패한 호출도 이번 바퀴에 끝난 것으로 친다 — 늘 실패하는 코드 하나가 순환을
        멈추지 않게(설계 §2: 실패 호출은 다음 주기에 다시). 건너뛴 슬롯만 같은 대상을 다시 잡는다
        """
        r = self.runs
        if job.kind == "option_list":
            r.option_list[job.key] = OptionListRun(job.state_key, self.now_us(), ok)
        elif job.kind == "expiry":
            # KIS 값을 받아야 이 세션 완료(_on_expiry). 못 받았으면 캘린더 값을 쓰며 다시 부른다
            if job.series is not None and r.expiry_resolved.get(job.series) != job.tag:
                r.expiry_retry[job.series] = self.now_us() + round(self.cfg.expiry_retry_s * US)
        elif job.kind == "fill2":
            r.done[job.key] = job.cycle
            if job.new_round:
                r.fill2_done.clear()
            if called and job.target is not None:
                r.fill2_done.add(job.target.key)
        else:
            r.done[job.key] = job.cycle

    def _tag(self, ts: datetime, job: Job) -> Tag:
        return session_tag(ts, self.cal) or job.tag

    def _failed(self, job: Job, ts: datetime, tag: Tag, reason: str) -> None:
        n = self._fails[job.key] = self._fails.get(job.key, 0) + 1
        self.stats.failed[job.kind] += 1
        _log(logging.WARNING, "job_failed", tag, job=job.key, consecutive=n, reason=reason)
        if n >= self.cfg.fail_health_after:
            self._health(
                ts,
                tag,
                "job_failed",
                "warning",
                f"{job.kind} 연속 실패 {n}회",
                job=job.key,
                consecutive=n,
                reason=reason,
            )
        if job.kind == "expiry":
            self._expiry_fallback(job, ts, tag, reason)

    # ── 처리 ──

    def _handle(self, job: Job, body: dict[str, Any], ts: datetime, tag: Tag) -> None:
        handlers: dict[str, Callable[[Job, dict[str, Any], datetime, Tag], None]] = {
            "option_list": self._on_option_list,
            "expiry": self._on_expiry,
            "board": self._on_board,
            "futures_board": self._on_futures_board,
            "underlying": self._on_underlying,
            "futures_single": self._on_futures_single,
            "investor": self._on_investor,
            "fill1": self._on_fill,
            "fill2": self._on_fill,
        }
        handlers[job.kind](job, body, ts, tag)

    def _on_option_list(self, job: Job, body: dict[str, Any], ts: datetime, tag: Tag) -> None:
        raws = output_rows(body, "output")
        rows, bad = parse_rows(OptionListRow, raws)
        self._quarantine(job, ts, tag, raws, bad)
        if job.series is not None:
            self.ctx.set_listed(job.series.cls, [r.mtrt_yymm for r in rows])

    def _on_expiry(self, job: Job, body: dict[str, Any], ts: datetime, tag: Tag) -> None:
        s = job.series
        if s is None:
            return
        raws = output_rows(body, "output1")
        rows, bad = parse_rows(PriceOutput, raws)
        self._quarantine(job, ts, tag, raws, bad)
        kis_date = rows[0].futs_last_tr_date if rows else None
        if kis_date is None:
            self._expiry_fallback(job, ts, tag, "단건 응답에 최종거래일이 없다")
            return
        calc = estimate_last_trade_date(s, self.cal)
        matches = None if calc is None else calc == kis_date
        self._emit(
            self.sink.write_expiries,
            [
                ExpiryRecord(
                    ts=ts,
                    trade_date=tag[0],
                    session=tag[1],
                    mrkt_cls=s.cls,
                    expiry=s.mtrt,
                    last_trade_date=kis_date,
                    source="kis",
                    calendar_date=calc,
                    matches=matches,
                    code=_code(job),
                )
            ],
        )
        if matches is False:
            # PLAN §6.2: 자체 계산 vs KIS — 불일치 시 KIS 값 사용 + 경고
            self._health(
                ts,
                tag,
                "expiry_mismatch",
                "warning",
                "최종거래일 계산값이 KIS 와 다르다",
                series=s.label,
                kis=kis_date,
                calendar=calc,
            )
        self.ctx.set_expiry(s, ExpiryInfo(kis_date, "kis"))
        self.runs.expiry_resolved[s] = job.tag
        self.runs.expiry_retry.pop(s, None)

    def _expiry_fallback(
        self, job: Job, ts: datetime, tag: Tag, reason: str, level: HealthLevel | None = None
    ) -> None:
        """KIS 최종거래일을 못 얻었다 — 캘린더 계산값을 쓴다(이미 KIS 값이 있으면 그대로 둔다).

        재시도(60초마다)가 이어져도 health·대체 레코드는 시리즈·세션마다 한 번만 남긴다.
        """
        s = job.series
        if s is None:
            return
        calc = estimate_last_trade_date(s, self.cal)
        known = self.ctx.expiries.get(s)
        kept = known is not None and known.source == "kis"
        if self._fallback_noted.get(s) == tag:
            _log(logging.INFO, "expiry_unresolved", tag, series=s.label, reason=reason)
            return
        self._fallback_noted[s] = tag
        self._health(
            ts,
            tag,
            "expiry_unresolved",
            "error" if calc is None and not kept else level or "warning",
            "KIS 최종거래일을 다시 받지 못해 직전 KIS 값을 쓴다"
            if kept
            else "KIS 최종거래일을 못 얻어 캘린더 계산값을 쓴다",
            series=s.label,
            calendar=calc,
            reason=reason,
        )
        if calc is None or kept:
            return
        self.ctx.set_expiry(s, ExpiryInfo(calc, "calendar"))
        self._emit(
            self.sink.write_expiries,
            [
                ExpiryRecord(
                    ts=ts,
                    trade_date=tag[0],
                    session=tag[1],
                    mrkt_cls=s.cls,
                    expiry=s.mtrt,
                    last_trade_date=calc,
                    source="calendar",
                    calendar_date=calc,
                    code=_code(job),
                    quality="estimated",
                )
            ],
        )

    def _on_board(self, job: Job, body: dict[str, Any], ts: datetime, tag: Tag) -> None:
        s = job.series
        if s is None:
            return
        recs: list[ChainRecord] = []
        keys: set[tuple[Decimal, str]] = set()
        side: tuple[tuple[str, CallPut], ...] = (("output1", "C"), ("output2", "P"))
        for out, cp in side:
            raws = output_rows(body, out)
            rows, bad = parse_rows(CallPutRow, raws)
            bad_idx = {e.index for e in bad}
            for raw, r in zip(
                (x for i, x in enumerate(raws) if i not in bad_idx), rows, strict=True
            ):
                rec = ChainRecord(
                    ts=ts,
                    trade_date=tag[0],
                    session=tag[1],
                    mrkt_cls=s.cls,
                    expiry=s.mtrt,
                    strike=r.strike,
                    cp=cp,
                    source="board",
                    code=r.code,
                    last=r.optn_prpr,
                    bid=r.optn_bidp,
                    ask=r.optn_askp,
                    oi=r.oi,
                    oi_chg=r.otst_stpl_qty_icdc,
                    volume=r.acml_vol,
                    iv_kis=r.iv_kis,
                    delta=r.delta_val,
                    gamma=r.gama,
                    theta=r.theta,
                    vega=r.vega,
                    rho=r.rho,
                )
                recs.append(self._sane(job, rec, raw, ts, tag, r.hts_ints_vltl))
                keys.add((r.strike, cp))
            self._quarantine(job, ts, tag, raws, bad)
            for e in bad:
                strike = _raw_strike(raws[e.index])
                if strike is not None:
                    recs.append(
                        self._invalid(
                            s,
                            strike,
                            cp,
                            "board",
                            ts,
                            tag,
                            _raw_str(raws[e.index], "optn_shrn_iscd"),
                        )
                    )
        strikes = {k for k, _ in keys}
        if self._strike_drop(s, strikes, ts, tag):
            recs = [r.model_copy(update={"quality": "invalid"}) for r in recs]
        self._write_chain(recs, job.stale_after_s)
        self.ctx.set_board(s, BoardSeen(frozenset(keys), to_us(ts)))
        self._check_coverage(s, strikes, ts, tag)

    def _strike_drop(self, s: Series, strikes: set[Decimal], ts: datetime, tag: Tag) -> bool:
        """PLAN §6.1 스냅샷 간 행사가 개수 급감(> 20%) — 같은 시리즈 직전 전광판과 비교.

        이 스냅샷 행은 invalid(원문은 raw 로 남는다). 다음 스냅샷은 이것과 비교한다 — 줄어든 채
        이어지면 한 번만 잡는다. 보강 2 는 전광판이 준 행사가만 빼므로 빠진 행사가를 단건으로 채운다
        """
        prev = self.ctx.boards.get(s)
        if prev is None or not prev.strikes:
            return False
        n, before = len(strikes), len(prev.strikes)
        if n >= before * (1 - self.cfg.board_drop_ratio):
            return False
        self._health(
            ts,
            tag,
            "board_strike_drop",
            "warning",
            "전광판 행사가 개수가 직전보다 급감했다 — 이 스냅샷은 invalid",
            series=s.label,
            previous=before,
            strikes=n,
        )
        return True

    def _check_coverage(self, s: Series, strikes: set[Decimal], ts: datetime, tag: Tag) -> None:
        """설계 §5: 위클리 전광판이 ATM 을 담는가, 월물은 전광판 ∪ 보강 1 범위가."""
        fut = self.ctx.reference(ts, self.cal)
        if fut is None:
            return
        chain = self.ctx.chain(s)
        fill1: list[Decimal] = []
        if not s.weekly and chain is not None:
            fill1 = atm_window(chain.strikes, fut.price, self.cfg.fill1_atm_range)
        if board_covers(s, strikes, fut.price, fill1):
            return
        universe = chain.strikes if chain is not None else sorted(strikes)
        atm = atm_strike(universe, fut.price) if universe else None
        self._health(
            ts,
            tag,
            "board_coverage",
            "warning",
            "전광판이 ATM 을 담지 못한다",
            series=s.label,
            futures=fut.price,
            atm=atm,
            board_min=min(strikes, default=None),
            board_max=max(strikes, default=None),
        )

    def _on_futures_board(self, job: Job, body: dict[str, Any], ts: datetime, tag: Tag) -> None:
        raws = output_rows(body, "output")
        rows, bad = parse_rows(FuturesBoardRow, raws)
        self._quarantine(job, ts, tag, raws, bad)
        recs = [
            FuturesRecord(
                ts=ts,
                trade_date=tag[0],
                session=tag[1],
                code=r.futs_shrn_iscd,
                name=r.hts_kor_isnm,
                market=job.market,
                source="board",
                price=r.futs_prpr,
                bid=r.futs_bidp,
                ask=r.futs_askp,
                volume=r.acml_vol,
                oi=r.hts_otst_stpl_qty,
                remaining_days=r.hts_rmnn_dynu,
            )
            for r in rows
        ]
        recs += [
            FuturesRecord(
                ts=ts,
                trade_date=tag[0],
                session=tag[1],
                market=job.market,
                source="board",
                code=code,
                quality="invalid",
            )
            for e in bad
            if (code := _raw_str(raws[e.index], "futs_shrn_iscd")) is not None
        ]
        self._emit(self.sink.write_futures, recs)
        dated = sorted(
            (r for r in rows if r.hts_rmnn_dynu is not None), key=lambda r: r.hts_rmnn_dynu or 0
        )
        if dated:
            self.ctx.set_futures_codes([r.futs_shrn_iscd for r in dated])
        # 분기 만기일 15:20 뒤엔 만기 지난 근월물(잔존일수가 가장 짧다)을 건너뛰고 차월물을 잡는다
        near = near_month(
            [r for r in rows if self.ctx.futures_alive(r.futs_shrn_iscd, ts, self.cal)]
        )
        if near is None or near.futs_prpr is None:
            self._health(ts, tag, "no_futures_price", "warning", "선물 근월물 가격이 없다")
            return
        self.ctx.set_futures(FuturesQuote(near.futs_shrn_iscd, near.futs_prpr, to_us(ts), "board"))

    def _on_underlying(self, job: Job, body: dict[str, Any], ts: datetime, tag: Tag) -> None:
        """기초자산 시세(display-board-top): 응답 필드 미실측 — raw 녹화만 한다(확인 필요). 무결측
        판정(services/gaps.py `underlying`)이 이 원문의 일 키 `underlying|…`·`rt_cd` 로 센다."""

    def _on_futures_single(self, job: Job, body: dict[str, Any], ts: datetime, tag: Tag) -> None:
        raws = output_rows(body, "output1")
        rows, bad = parse_rows(PriceOutput, raws)
        self._quarantine(job, ts, tag, raws, bad)
        code = _code(job) or ""
        if not rows:
            self._emit(
                self.sink.write_futures,
                [
                    FuturesRecord(
                        ts=ts,
                        trade_date=tag[0],
                        session=tag[1],
                        code=code,
                        market=job.market,
                        source="single",
                        quality="invalid",
                    )
                ],
            )
            return
        p = rows[0]
        self._emit(
            self.sink.write_futures,
            [
                FuturesRecord(
                    ts=ts,
                    trade_date=tag[0],
                    session=tag[1],
                    code=code,
                    name=p.hts_kor_isnm,
                    market=job.market,
                    source="single",
                    price=p.price,
                    volume=p.acml_vol,
                    oi=p.oi,
                    remaining_days=p.hts_rmnn_dynu,
                )
            ],
        )
        live = self.ctx.live_futures_codes(ts, self.cal)  # 만기 지난 근월물은 빠져 있다
        if live and code == live[0] and p.price is not None and p.price > 0:
            self.ctx.set_futures(FuturesQuote(code, p.price, to_us(ts), "single"))

    def _on_investor(self, job: Job, body: dict[str, Any], ts: datetime, tag: Tag) -> None:
        params = job.request.as_dict() if job.request is not None else {}
        mkt, sector = params.get("FID_INPUT_ISCD", ""), params.get("FID_INPUT_ISCD_2", "")
        raws = output_rows(body, "output")
        rows, bad = parse_rows(InvestorRow, raws)
        self._quarantine(job, ts, tag, raws, bad)
        recs = [
            InvestorRecord(
                ts=ts,
                trade_date=tag[0],
                session=tag[1],
                market_code=mkt,
                sector_code=sector,
                **f.model_dump(),
            )
            for row in rows
            for f in row.to_long()
        ]
        self._emit(self.sink.write_investor, recs)

    def _on_fill(self, job: Job, body: dict[str, Any], ts: datetime, tag: Tag) -> None:
        t = job.target
        if t is None:
            return
        raws = output_rows(body, "output1")
        rows, bad = parse_rows(PriceOutput, raws)
        self._quarantine(job, ts, tag, raws, bad)
        if not rows:
            if not bad:
                self._quarantine_one(job, ts, tag, body, "단건 응답에 output1 이 없다")
            self._write_chain(
                [self._invalid(t.series, t.strike, t.cp, "fill", ts, tag, t.code)],
                job.stale_after_s,
            )
            return
        p = rows[0]
        if p.strike != t.strike:
            self._quarantine_one(
                job, ts, tag, raws[0], f"행사가 불일치: 요청 {t.strike} 응답 {p.strike}"
            )
            self._write_chain(
                [self._invalid(t.series, t.strike, t.cp, "fill", ts, tag, t.code)],
                job.stale_after_s,
            )
            return
        rec = ChainRecord(
            ts=ts,
            trade_date=tag[0],
            session=tag[1],
            mrkt_cls=t.series.cls,
            expiry=t.series.mtrt,
            strike=t.strike,
            cp=t.cp,
            source="fill",
            code=t.code,
            last=p.price,
            oi=p.oi,
            oi_chg=p.otst_stpl_qty_icdc,
            volume=p.acml_vol,
            iv_kis=p.iv_kis,
            delta=p.delta_val,
            gamma=p.gama,
            theta=p.theta,
            vega=p.vega,
            rho=p.rho,
        )
        rec = self._sane(job, rec, raws[0], ts, tag, p.hts_ints_vltl)
        self._write_chain([rec], job.stale_after_s)

    # ── 레코드 도우미 ──

    def _sane(
        self,
        job: Job,
        rec: ChainRecord,
        raw: Mapping[str, Any],
        ts: datetime,
        tag: Tag,
        raw_iv: Decimal | None,
    ) -> ChainRecord:
        """PLAN §6.1 이상치(가격·OI 음수, IV ≤ 0 또는 > 300%)면 invalid + quarantine.

        KIS IV 0 은 값 없음(models `_positive` → None)이라 이상치로 보지 않는다 — 음수만 잡는다
        (`raw_iv` = 받은 `hts_ints_vltl` 그대로, `rec.iv_kis` 는 음수도 None 으로 바뀌어 있다).
        """
        bad = [
            name
            for name, v in (("last", rec.last), ("bid", rec.bid), ("ask", rec.ask))
            if v is not None and v < 0
        ]
        bad += [
            name for name, v in (("oi", rec.oi), ("volume", rec.volume)) if v is not None and v < 0
        ]
        if (rec.iv_kis is not None and rec.iv_kis > IV_MAX) or (raw_iv is not None and raw_iv < 0):
            bad.append("iv_kis")
        if not bad:
            return rec
        self._quarantine_one(job, ts, tag, raw, f"이상치: {', '.join(bad)}")
        return rec.model_copy(update={"quality": "invalid"})

    def _invalid(
        self,
        s: Series,
        strike: Decimal,
        cp: CallPut,
        source: str,
        ts: datetime,
        tag: Tag,
        code: str | None,
    ) -> ChainRecord:
        return ChainRecord.model_validate(
            {
                "ts": ts,
                "trade_date": tag[0],
                "session": tag[1],
                "mrkt_cls": s.cls,
                "expiry": s.mtrt,
                "strike": strike,
                "cp": cp,
                "source": source,
                "code": code,
                "quality": "invalid",
            }
        )

    def _write_chain(self, recs: Sequence[ChainRecord], stale_after_s: float) -> None:
        for r in recs:
            self.book.update(r, stale_after_s)
        self._emit(self.sink.write_chain, recs)

    def _quarantine(
        self,
        job: Job,
        ts: datetime,
        tag: Tag,
        raws: Sequence[Mapping[str, Any]],
        bad: Sequence[RowError],
    ) -> None:
        for e in bad:
            self._quarantine_one(job, ts, tag, raws[e.index], e.error)

    def _quarantine_one(
        self, job: Job, ts: datetime, tag: Tag, payload: Mapping[str, Any], error: str
    ) -> None:
        tr_id = job.request.tr_id if job.request is not None else ""
        self._held.append(
            QuarantineRecord(
                ts=ts,
                trade_date=tag[0],
                session=tag[1],
                tr_id=tr_id,
                key=job.key,
                payload=dict(payload),
                error=error,
            )
        )

    def _flush_quarantine(self, job: Job) -> None:
        """격리 행을 한 번에 쓰고 health 는 일 하나당 한 건(행 수와 첫 오류)."""
        held, self._held = self._held, []
        if not held:
            return
        self._emit(self.sink.write_quarantine, held)
        first = held[0]
        self._health(
            first.ts,
            (first.trade_date, first.session),
            "quarantine",
            "warning",
            "검증 실패 레코드를 격리했다",
            job=job.key,
            rows=len(held),
            error=first.error[:300],
        )

    def _record_raw(self, job: Job, resp: KisResponse, ts: datetime, tag: Tag) -> None:
        req = job.request
        if req is None:
            return
        key = job.key + "|" + "&".join(f"{k}={v}" for k, v in req.params)
        env = wrap(
            resp.body,
            source="kis_rest",
            tr_id=req.tr_id,
            received_at=ts,
            tagger=_fixed_tagger(tag),
            key=key,
        )
        self._emit(self.sink.write_raw, [env])

    def _health(
        self,
        ts: datetime,
        tag: Tag | None,
        kind: str,
        level: HealthLevel,
        message: str,
        **detail: object,
    ) -> None:
        d = {k: _jsonable(v) for k, v in detail.items()}
        ev = HealthEvent(
            ts=ts,
            trade_date=tag[0] if tag else None,
            session=tag[1] if tag else None,
            kind=kind,
            level=level,
            message=message,
            detail=d,
        )
        _log(logging.WARNING if level != "info" else logging.INFO, kind, tag, **d)
        self._emit(self.sink.write_health, [ev])

    def _emit[R](self, write: Callable[[Sequence[R]], None], rows: Sequence[R]) -> None:
        """싱크 쓰기 격리 — 저장 실패가 수집을 멈추지 않는다(설계 §2: DB 장애는 로컬 큐 몫)."""
        if not rows:
            return
        try:
            write(rows)
        except Exception as e:
            self.stats.sink_errors += 1
            _log(logging.ERROR, "sink_error", None, error=f"{type(e).__name__}: {e}")


def _fixed_tagger(tag: Tag) -> Callable[[datetime], tuple[date | None, SessionName | None]]:
    def tagger(_: datetime) -> tuple[date | None, SessionName | None]:
        return tag[0], tag[1]

    return tagger


def _code(job: Job) -> str | None:
    if job.request is None:
        return None
    return job.request.as_dict().get("FID_INPUT_ISCD")


def _raw_str(raw: Mapping[str, Any], key: str) -> str | None:
    v = raw.get(key)
    s = str(v).strip() if v is not None else ""
    return s or None


def _raw_strike(raw: Mapping[str, Any]) -> Decimal | None:
    s = _raw_str(raw, "acpr")
    if s is None:
        return None
    try:
        k = Decimal(s)
    except InvalidOperation:
        return None
    return k if k.is_finite() and k > 0 else None


def _jsonable(v: object) -> object:
    if v is None or isinstance(v, str | int | float | bool):
        return v
    return str(v)


def _log(level: int, event: str, tag: Tag | None, **fields: object) -> None:
    """구조화 JSON 로그 (CLAUDE.md: 거래일·세션·서비스명). 토큰·키는 넣지 않는다."""
    rec = {
        "service": "poller",
        "event": event,
        "trade_date": tag[0].isoformat() if tag else None,
        "session": tag[1] if tag else None,
        **fields,
    }
    log.log(level, json.dumps(rec, ensure_ascii=False, default=str))
