"""수집 일정 — 지금 시각·세션 상태·문맥·실행 기록으로 할 일을 고른다 (부작용 없음).

설계 docs/phase1_design.md §3(우선순위)·§5(주기)·§7(야간 분기), PLAN §4.4 표.

| 호출 | 등급 | 주기 | 상태 |
|---|---|---|---|
| 월물리스트 3종('' WKM WKI) | P0 | 상태 진입 때 + 1시간 | PRE_DAY·DAY·PRE_NIGHT·NIGHT |
| 최종거래일(시리즈당 단건 1회) | P0 | 세션마다 한 번, 실패는 60초 뒤 다시 | 같음 (야간 C 제외) |
| 전광판 콜/풋 3만기 | P1 | 30초, 만기 사이 1초 | DAY·NIGHT(A) |
| 선물 전광판·기초자산 | P1 | 30초 | DAY·NIGHT(A) |
| 선물 단건 CM(근월·차월) | P1 | 30초 | NIGHT(B) |
| 투자자별 7조합 | P2 | 60초 | DAY·NIGHT(설정) |
| 보강 1 월물 ATM±20 × 콜풋 | P3 | 60초에 고르게 | DAY·NIGHT(A); B 는 최근접 60초·월물 120초(EU) |
| 보강 2 나머지 행사가 | P4 | 1초에 1건 | DAY·NIGHT(A·B) |

- IDLE·POST_DAY 는 할 일이 없다. PRE_DAY·PRE_NIGHT 는 월물리스트·최종거래일만(설계 §4.6)
- 선물: 최종거래일 15:20 이 지난 종목(분기 만기일의 근월물)은 기초자산·야간 선물 단건에 쓰지 않고,
  그 종목 시세로 보강 ATM 을 잡지 않는다(`ChainContext.live_futures_codes`·`reference`). 15:20 뒤
  차월물 시세가 들어올 때까지 보강은 쉰다
- 야간 C(설계 §7: 야간 옵션 REST 없음)는 PRE_NIGHT 부터 옵션 단건을 부르지 않는다 — 최종거래일은
  주간에 받은 값을 그대로 쓰고, 모르는 시리즈만 부르지 않는 일(request None)로 캘린더 값을 채운다
- 주기는 에포크 기준 격자다: 주기 번호 c = ⌊t / 주기⌋, 그 주기의 예정 시각 = c·주기 + 오프셋.
  주기마다 한 번 — 밀려서 다음 주기로 넘어가면 그 회차는 건너뛴다(다음 주기로, 설계 §3)
- 시각은 모두 에포크 마이크로초 정수(가짜 시계와 어긋나지 않게)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from core.calendar import SessionInfo, State, TradingCalendar
from data.kis.ratelimit import US, Priority
from services.poller import endpoints as ep
from services.poller.config import PollerConfig
from services.poller.context import (
    ChainContext,
    FillTarget,
    FuturesQuote,
    Series,
    SeriesChain,
    Tag,
    Targets,
    fill1_targets,
    fill2_targets,
    resolve_candidates,
)

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

JobKind = Literal[
    "option_list",
    "expiry",
    "board",
    "futures_board",
    "underlying",
    "futures_single",
    "investor",
    "fill1",
    "fill2",
]
Mode = Literal["A", "B", "C"]


def to_us(ts: datetime) -> int:
    return (ts - EPOCH) // timedelta(microseconds=1)


def from_us(t_us: int) -> datetime:
    return EPOCH + timedelta(microseconds=t_us)


def _us(seconds: float) -> int:
    return round(seconds * US)


@dataclass(frozen=True)
class Job:
    kind: JobKind
    key: str  # 흐름 이름 — 실행 기록·실패 횟수의 단위
    priority: Priority
    due_us: int
    cycle: int  # 이 흐름의 주기 번호 (option_list·expiry 는 0)
    request: ep.Request | None  # None 이면 부르지 않고 처리 (최종거래일 코드가 없을 때)
    tag: Tag  # 계획 시점의 (귀속 거래일, 세션)
    series: Series | None = None
    target: FillTarget | None = None
    market: str = ""
    stale_after_s: float = 0.0  # 체인 행 품질 판정 기준
    state_key: str = ""  # option_list: 어느 상태에서 부른 것인가
    new_round: bool = False  # fill2: 한 바퀴를 다 돌아 새로 시작


@dataclass(frozen=True)
class Plan:
    jobs: tuple[Job, ...]  # 지금 할 일, 우선순위·예정 시각 순
    next_due_us: int | None  # 다음 예정 시각 (지금 할 일이 없을 때 깰 시각)


@dataclass
class OptionListRun:
    state_key: str
    at_us: int
    ok: bool


@dataclass
class RunState:
    """collector 가 갱신하는 실행 기록."""

    done: dict[str, int] = field(default_factory=dict[str, int])  # 흐름 → 마친 주기 번호
    option_list: dict[str, OptionListRun] = field(default_factory=dict[str, OptionListRun])
    # 시리즈 → KIS 최종거래일을 받은 세션. 받지 못한 시리즈는 expiry_retry 시각 뒤에 다시 부른다
    expiry_resolved: dict[Series, Tag] = field(default_factory=dict[Series, Tag])
    expiry_retry: dict[Series, int] = field(default_factory=dict[Series, int])
    # 이번 바퀴에 호출을 마친 보강 2 대상(실패 포함, 허가 대기 초과·한도초과로 건너뛴 것 제외)
    fill2_done: set[str] = field(default_factory=set[str])


@dataclass(frozen=True)
class FillGroup:
    targets: tuple[FillTarget, ...]
    period_s: float
    market: str


@dataclass(frozen=True)
class FillPlan:
    fill1: tuple[FillGroup, ...]
    fill2: tuple[FillTarget, ...]
    fill2_market: str


def night_mode(info: SessionInfo, cfg: PollerConfig) -> Mode | None:
    """DAY 는 'A'(전 구성), NIGHT 는 설정값, 그 밖엔 None."""
    if info.state is State.DAY:
        return "A"
    if info.state is State.NIGHT:
        return cfg.night_mode
    return None


def fill_plan(
    ctx: ChainContext, targets: Targets, mode: Mode, cfg: PollerConfig
) -> FillPlan | None:
    """보강 1·2 대상 (선물가·마스터가 있어야 한다). 문맥 버전마다 한 번만 계산한다.

    기준가는 `ctx.futures` 다 — `plan` 은 그 종목이 살아 있을 때(`ctx.reference`)만 부른다."""
    fut = ctx.futures
    if fut is None or mode == "C":
        return None
    ref = fut.price
    return ctx.memo(("fill", targets, mode, cfg), lambda: _fill_plan(ctx, targets, mode, ref, cfg))


def _fill_plan(
    ctx: ChainContext, targets: Targets, mode: Mode, ref: Decimal, cfg: PollerConfig
) -> FillPlan:
    n = cfg.fill1_atm_range
    groups: list[FillGroup] = []
    if mode == "A":
        m = targets.monthly
        ch = ctx.chain(m) if m is not None else None
        if ch is not None:
            f1 = fill1_targets(ch, ref, n)
            groups.append(FillGroup(f1, cfg.fill1_period_s, cfg.option_market))
        market = cfg.option_market
    else:  # B: 전광판 없이 최근접 ATM±n(60초) + 월물 ATM±n(120초), 시장 EU (설계 §7)
        market = cfg.night_option_market
        near = ctx.chain(targets.nearest)
        if near is not None:
            groups.append(FillGroup(fill1_targets(near, ref, n), cfg.fill1_period_s, market))
        m = targets.monthly
        mch = ctx.chain(m) if m is not None and m != targets.nearest else None
        if mch is not None:
            f1 = fill1_targets(mch, ref, n)
            groups.append(FillGroup(f1, cfg.night_b_monthly_period_s, market))
    in_fill1: dict[Series, set[tuple[Decimal, str]]] = {}
    for g in groups:
        for t in g.targets:
            in_fill1.setdefault(t.series, set()).add((t.strike, t.cp))
    chains: list[SeriesChain] = []
    exclude: dict[Series, frozenset[tuple[Decimal, str]]] = {}
    for s in targets.tracked:
        ch = ctx.chain(s)
        if ch is None:
            continue
        board = ctx.boards.get(s)
        if mode == "A" and board is None:
            continue  # 전광판을 아직 못 받았으면 무엇이 빠졌는지 모른다
        chains.append(ch)
        exclude[s] = frozenset(in_fill1.get(s, set())) | (board.keys if board else frozenset())
    return FillPlan(tuple(groups), fill2_targets(chains, ref, exclude), market)


class _Builder:
    def __init__(self, now_us: int, runs: RunState, tag: Tag) -> None:
        self.now = now_us
        self.runs = runs
        self.tag = tag
        self.jobs: list[Job] = []
        self.next: int | None = None

    def wake(self, t_us: int) -> None:
        self.next = t_us if self.next is None else min(self.next, t_us)

    def slot(self, key: str, period_s: float, offset_s: float) -> tuple[int, int] | None:
        """주기 흐름 하나: 이번 주기 예정 시각이 지났고 아직 안 했으면 (예정 시각, 주기 번호)."""
        period = max(_us(period_s), 1)
        offset = _us(offset_s) % period
        c = self.now // period
        start = c * period + offset
        if self.now >= start and self.runs.done.get(key, -1) < c:
            return start, c
        self.wake(start if self.now < start else start + period)
        return None

    def plan(self) -> Plan:
        jobs = sorted(self.jobs, key=lambda j: (j.priority, j.due_us, j.key))
        return Plan(tuple(jobs), self.next)


def plan(
    now_us: int,
    info: SessionInfo,
    tag: Tag | None,
    ctx: ChainContext,
    runs: RunState,
    cfg: PollerConfig,
    *,
    calendar: TradingCalendar,
) -> Plan:
    """지금 할 일. 같은 입력이면 같은 결과다(문맥·기록을 바꾸지 않는다).

    `calendar` 는 선물 최종거래일을 월물리스트·KIS 값으로 모를 때의 계산용(분기 만기 판정)."""
    if tag is None or info.state in (State.IDLE, State.POST_DAY):
        return Plan((), None)
    b = _Builder(now_us, runs, tag)
    _option_lists(b, info, cfg)
    _expiries(b, ctx, cfg)
    mode = night_mode(info, cfg)
    if mode is None:  # PRE_DAY·PRE_NIGHT
        return b.plan()
    now = from_us(now_us)
    targets = ctx.targets(now)
    live = ctx.live_futures_codes(now, calendar)
    ref = ctx.reference(now, calendar)
    if mode == "A":
        _boards_and_futures(b, targets, cfg, live, ref)
    elif mode == "B":
        _night_futures(b, cfg, live)
    if info.state is State.DAY or cfg.night_investor:
        _investors(b, cfg)
    if targets is not None and ref is not None:  # 만기 지난 근월물 시세로 ATM 을 잡지 않는다
        fp = fill_plan(ctx, targets, mode, cfg)
        if fp is not None:
            _fills(b, fp, cfg)
    return b.plan()


def state_key(info: SessionInfo, tag: Tag) -> str:
    return f"{info.state.value}:{tag[0].isoformat()}:{tag[1]}"


def _option_lists(b: _Builder, info: SessionInfo, cfg: PollerConfig) -> None:
    sk = state_key(info, b.tag)
    for cls in ep.OPTION_LIST_CLASSES:
        key = f"option_list:{cls or 'M'}"
        run = b.runs.option_list.get(key)
        due = b.now
        if run is not None and run.state_key == sk:
            wait = cfg.option_list_refresh_s if run.ok else cfg.option_list_retry_s
            due = run.at_us + _us(wait)
            if b.now < due:
                b.wake(due)
                continue
        req = ep.option_list(cls)
        job = Job("option_list", key, Priority.P0, due, 0, req, b.tag, state_key=sk)
        b.jobs.append(replace(job, series=Series(cls, "")))


def option_rest(tag: Tag, cfg: PollerConfig) -> bool:
    """이 세션 몫으로 옵션 REST 를 부르는가 — 야간 C 는 아니다(설계 §7, PRE_NIGHT 포함)."""
    return tag[1] == "day" or cfg.night_mode != "C"


def _expiries(b: _Builder, ctx: ChainContext, cfg: PollerConfig) -> None:
    rest = option_rest(b.tag, cfg)
    for s in resolve_candidates(ctx.listed, cfg.monthly_resolve):
        if b.runs.expiry_resolved.get(s) == b.tag:
            continue
        if not rest and s in ctx.expiries:
            continue  # 야간 C: 아는 값(주간 KIS 또는 캘린더)을 그대로 쓴다
        retry = b.runs.expiry_retry.get(s)
        if retry is not None and b.now < retry:
            b.wake(retry)
            continue
        ch = ctx.chain(s) if rest else None
        code = ch.code(ch.strikes[len(ch.strikes) // 2], "C") if ch is not None else None
        req = ep.single_price(cfg.option_market, code) if code is not None else None
        b.jobs.append(
            Job("expiry", f"expiry:{s.label}", Priority.P0, b.now, 0, req, b.tag, series=s)
        )


def _boards_and_futures(
    b: _Builder,
    targets: Targets | None,
    cfg: PollerConfig,
    live: Sequence[str],
    ref: FuturesQuote | None,
) -> None:
    mkt = cfg.futures_board_market
    if (sl := b.slot("futures_board", cfg.futures_period_s, 0.0)) is not None:
        req = ep.futures_board(mkt)
        b.jobs.append(
            Job("futures_board", "futures_board", Priority.P1, *sl, req, b.tag, market=mkt)
        )
    code = ref.code if ref is not None else (live[0] if live else None)
    if code is not None and (sl := b.slot("underlying", cfg.futures_period_s, 0.5)) is not None:
        req = ep.underlying_top(code, mkt)
        b.jobs.append(Job("underlying", "underlying", Priority.P1, *sl, req, b.tag, market=mkt))
    if targets is None:
        return
    for i, s in enumerate(targets.tracked):
        key = f"board:{s.label}"
        if (sl := b.slot(key, cfg.board_period_s, 0.25 + i * cfg.board_gap_s)) is not None:
            req = ep.callput_board(s.cls, s.mtrt, cfg.board_market)
            job = Job("board", key, Priority.P1, *sl, req, b.tag, series=s)
            b.jobs.append(replace(job, market=cfg.board_market, stale_after_s=cfg.rest_stale_s))


def _night_futures(b: _Builder, cfg: PollerConfig, live: Sequence[str]) -> None:
    """야간 B: 선물 전광판이 CM 을 받지 않아(#19) 선물 단건 CM 으로 대신한다 — 살아 있는 종목만
    (분기 만기일 밤엔 만기 지난 근월물을 빼고 차월물부터)."""
    mkt = cfg.night_futures_market
    for i, code in enumerate(live[: cfg.night_b_futures]):
        key = f"futures_single:{code}"
        if (sl := b.slot(key, cfg.futures_period_s, 0.5 * i)) is not None:
            req = ep.single_price(mkt, code)
            b.jobs.append(Job("futures_single", key, Priority.P1, *sl, req, b.tag, market=mkt))


def _investors(b: _Builder, cfg: PollerConfig) -> None:
    for j, (mkt, sector) in enumerate(ep.INVESTOR_PAIRS):
        key = f"investor:{mkt}/{sector}"
        if (sl := b.slot(key, cfg.investor_period_s, cfg.investor_offset_s + j)) is not None:
            req = ep.investor(mkt, sector)
            b.jobs.append(Job("investor", key, Priority.P2, *sl, req, b.tag))


def _fills(b: _Builder, fp: FillPlan, cfg: PollerConfig) -> None:
    for g in fp.fill1:
        n = max(len(g.targets), 1)
        stale = max(cfg.rest_stale_s, g.period_s + cfg.fill1_slack_s)
        for i, t in enumerate(g.targets):
            key = f"fill1:{t.key}"
            if (sl := b.slot(key, g.period_s, g.period_s * i / n)) is not None:
                req = ep.single_price(g.market, t.code)
                job = Job("fill1", key, Priority.P3, *sl, req, b.tag, series=t.series, target=t)
                b.jobs.append(replace(job, market=g.market, stale_after_s=stale))
    order = fp.fill2
    if not order or (sl := b.slot("fill2", 1.0 / cfg.fill2_rate, 0.0)) is None:
        return
    # 높은 등급에 밀려 놓친 슬롯은 fill2_backlog 개까지 따라잡는다 — 평균은 fill2_rate 이하
    due, c = sl
    consumed = max(b.runs.done.get("fill2", c - 1) + 1, c - cfg.fill2_backlog)
    pending = [t for t in order if t.key not in b.runs.fill2_done]
    t = (pending or list(order))[0]
    req = ep.single_price(fp.fill2_market, t.code)
    job = Job("fill2", "fill2", Priority.P4, due, consumed, req, b.tag, series=t.series, target=t)
    cycle_s = len(order) / cfg.fill2_rate + cfg.fill2_slack_s
    b.jobs.append(
        replace(job, market=fp.fill2_market, stale_after_s=cycle_s, new_round=not pending)
    )
