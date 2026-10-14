"""규칙 평가 입력 고르기 — 저장소 → 켜진 규칙·우니버스·프레임(P8 묶음 EVL, docs/p8_design.md §4.2,
D-P8-6·7·8).

실시간 작업(`kbj.services.engine.rules` — `rules.intraday`·`rules.close`), 백테스트·야간 감시
(`kbj.services.engine.rules_perf` — BKT), 규칙 검증 API(`POST
/api/rules/validate`)가 **같은 함수**로
프레임 입력을 고른다(같은 신호의 정의 — 입력이 같아야 지문·신호가 같다). 로그인 원천 어댑터·수집기를
import 하지 않는다(API 계약 ⑩ — 저장소·엔진·설정만).

- 일봉: `MarketRepo.series`(원장 우선순위 한 행), 칸 = 거래일 캘린더(`max(anchor, 첫
  봉)`~upto — 일봉
  없는 거래일은 정지 칸).
- 수급: 확정 원장 `FlowsRepo.days` → `build_ledger`. 언제나 싣는다 — 입력 지문(`input_fp`)이 수급
  값을 포함하므로 규칙이 수급을 쓰든 말든 같은 지문.
- 분할 의심: board `split_guard` 문턱(`config/board.yaml integrity.split_guard_ratio` — 부르는 쪽이
  넘긴다). `prv_board.split_check` 는 의심 날짜가 없어(종목·메모만) 넘기지 않는다 [확인 필요].
- 신고가: board 라벨(종가 기준, `new_high_days` 로 준 날짜만 — 평가 칸).
- 보유 시작일(`hold_since`)은 `kbj.engines.rules.live.holdings`.
- 장중 수급(`live_flow_rounds`): 가집계 이력에서 구분마다 확실히 들어온 가장 최근 입력 회차의 목록만
  (`kbj.engines.flows.prelim` — 공식 예제상 하루 4회 입력·순위 목록). 실시간·장중 재생이 같이 쓴다.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final, Protocol

from kbj.config.rules_cfg import RulesConfig
from kbj.core.calendar import TradingCalendar
from kbj.core.rows import Bar
from kbj.core.rules_rows import QuoteSlot
from kbj.core.time import KST, to_kst
from kbj.engines.flows.ledger import LedgerRow, build_ledger
from kbj.engines.flows.prelim import live_rows
from kbj.engines.rules.backtest import FrameInputs
from kbj.engines.rules.dsl import DslError, Rule, UMarket, UWatchlist, limits_of, validate_rule
from kbj.engines.rules.frame import Frame, build_frame
from kbj.store.repos.board import BoardRepo
from kbj.store.repos.flows import FlowsRepo
from kbj.store.repos.journal import JournalRepo
from kbj.store.repos.market import MarketRepo
from kbj.store.repos.rules import RulesRepo

__all__ = [
    "NEW_HIGH_BASIS",
    "ActiveRule",
    "FrameRepos",
    "RulesRepos",
    "active_rules",
    "calendar_days",
    "frame_inputs",
    "live_flow_rounds",
    "live_flows",
    "load_frames",
    "market_codes",
    "needs_market",
    "new_high_labels",
    "stock_names",
    "uses_new_high",
    "watchlists",
]


NEW_HIGH_BASIS: Final = "close"  # 신고가 지표는 종가 기준 라벨 [제안 — ADR 0017 3축]
_LABEL_AXES: Final[Mapping[str, frozenset[str]]] = {
    "hist": frozenset({"ath", "52w", "d120"}),
    "w52": frozenset({"52w", "d120"}),
    "d120": frozenset({"d120"}),
}


SLOT_MIN: Final = 10


def live_flow_rounds(flows: FlowsRepo, ts: datetime) -> tuple[dict[str, LedgerRow], dict[str, str]]:
    """슬롯 ts 의 장중 수급 — 그날 가집계 이력(ts 슬롯까지)에서 구분마다 확실히 들어온 가장 최근
    회차의 목록만 → 종목별 오늘 원장 행(kis.prelim, estimated)과 구분별 회차 시각
    (`kbj.engines.flows.prelim.live_rows`). 회차 전·목록 밖 구분은 그 칸이 비어 '판단 불가'."""
    day = to_kst(ts).date()
    start = datetime.combine(day, datetime.min.time(), tzinfo=KST)
    return live_rows(flows.intraday(start, ts + timedelta(minutes=SLOT_MIN)), ts)


def live_flows(flows: FlowsRepo, ts: datetime) -> dict[str, LedgerRow]:
    """`live_flow_rounds` 의 원장 행만(백테스트 장중 재생 입력)."""
    return live_flow_rounds(flows, ts)[0]


class FrameRepos(Protocol):
    """프레임 입력 고르기가 읽는 저장소(시세·수급 원장·신고가 라벨).

    `RulesRepos`·`PerfRepos`·API `ApiRepos` 모두 맞는다."""

    @property
    def market(self) -> MarketRepo: ...

    @property
    def flows(self) -> FlowsRepo: ...

    @property
    def board(self) -> BoardRepo: ...


class RulesRepos(Protocol):
    """이 처리기가 읽고 쓰는 저장소 — `Repos`(Pg)·`MemoryRepos`(시험) 둘 다 맞는다."""

    @property
    def market(self) -> MarketRepo: ...

    @property
    def flows(self) -> FlowsRepo: ...

    @property
    def board(self) -> BoardRepo: ...

    @property
    def journal(self) -> JournalRepo: ...

    @property
    def rules(self) -> RulesRepo: ...


@dataclass(frozen=True)
class ActiveRule:
    rule_id: str
    name: str
    version: int
    dsl_hash: str
    rule: Rule  # 검사·정규화(기본값 채움)


def active_rules(
    repo: RulesRepo, cfg: RulesConfig, *, modes: Collection[str]
) -> tuple[list[ActiveRule], dict[str, str]]:
    """켜진 규칙 중 모드가 맞는 것(규칙 id 순). 판 DSL 이 지금 검사를 못 지나면 그 규칙만 빼고
    사유를 돌려준다(격리 — 저장된 판은 바꾸지 않는다)."""
    lim = limits_of(cfg)
    out: list[ActiveRule] = []
    bad: dict[str, str] = {}
    for head, ver in repo.enabled_rules():
        try:
            rule = validate_rule(dict(ver.dsl), lim)
        except DslError as e:
            bad[head.rule_id] = "dsl_invalid: " + ",".join(i.reason for i in e.issues)[:200]
            continue
        if rule.mode in modes:
            out.append(ActiveRule(head.rule_id, head.name, ver.version, ver.dsl_hash, rule))
    return sorted(out, key=lambda a: a.rule_id), bad


def watchlists(repo: JournalRepo, rules: Iterable[ActiveRule]) -> dict[str, list[str]]:
    names = {r.rule.universe.watchlist for r in rules if isinstance(r.rule.universe, UWatchlist)}
    out: dict[str, list[str]] = {}
    for n in sorted(names):
        items = sorted(repo.watchlist(n), key=lambda w: (w.sort, w.code))
        out[n] = [w.code for w in items]
    return out


def calendar_days(cal: TradingCalendar, start: date, end: date) -> list[date]:
    """[start, end] 의 거래일(오름차순)."""
    if start > end:
        return []
    d = start if cal.is_trading_day(start) else cal.next_trading_day(start)
    out: list[date] = []
    while d <= end:
        out.append(d)
        d = cal.next_trading_day(d)
    return out


def new_high_labels(
    board: BoardRepo, days: Sequence[date]
) -> dict[str, dict[date, frozenset[str]]]:
    out: dict[str, dict[date, frozenset[str]]] = {}
    for d in days:
        for code, lab in board.labels(d, NEW_HIGH_BASIS).items():
            axes = _LABEL_AXES.get(lab.kind)
            if axes:
                out.setdefault(code, {})[d] = axes
    return out


def frame_inputs(
    repos: FrameRepos,
    codes: Sequence[str],
    *,
    upto: date,
    cfg: RulesConfig,
    cal: TradingCalendar,
    split_ratio: float,
    hold_since: Mapping[str, date] | None = None,
    new_high_days: Sequence[date] = (),
) -> dict[str, FrameInputs]:
    """종목별 프레임 입력(anchor ~ upto 확정 일봉·수급) — 실시간 평가(`load_frames`)·백테스트·야간
    감시·즉석 백테스트 API 가 모두 이 한 함수로 고른다(P8 WRAP — 두 벌을 하나로). 일봉이 하나도 없는
    종목은 빠진다(부르는 쪽이 `no_data` 로 적는다). `new_high_days` 는 신고가 라벨을 읽을
    날짜(평가 칸)."""
    if not codes:
        return {}
    days = calendar_days(cal, cfg.anchor_date, upto)
    if not days:
        return {}
    series = repos.market.series(list(codes), upto, len(days))
    inv = repos.flows.days(list(codes), days[0], upto)
    ledger = build_ledger([], [], [r for rows in inv.values() for r in rows], [], days)
    flows_by: dict[str, dict[date, LedgerRow]] = {}
    for (code, d), r in ledger.rows.items():
        flows_by.setdefault(code, {})[d] = r
    nh = new_high_labels(repos.board, new_high_days) if new_high_days else {}
    out: dict[str, FrameInputs] = {}
    for code in codes:
        bars: list[Bar] = [b for b in series.get(code, []) if b.date <= upto]
        if not bars:
            continue
        out[code] = FrameInputs(
            code=code,
            bars=tuple(bars),
            anchor=cfg.anchor_date,
            split_ratio=split_ratio,
            flows=flows_by.get(code, {}),
            calendar=tuple(days),
            new_high=nh.get(code, {}) if new_high_days else None,
            hold_since=None if hold_since is None else hold_since.get(code),
        )
    return out


def load_frames(
    repos: FrameRepos,
    codes: Sequence[str],
    *,
    upto: date,
    cfg: RulesConfig,
    cal: TradingCalendar,
    split_ratio: float,
    partial: Mapping[str, QuoteSlot] | None = None,
    partial_flows: Mapping[str, LedgerRow] | None = None,
    hold_since: Mapping[str, date] | None = None,
    new_high_days: Sequence[date] = (),
) -> dict[str, Frame]:
    """종목별 프레임(`frame_inputs` 그대로 + 장중이면 그 슬롯 부분 봉·가집계 잠정 행)."""
    fis = frame_inputs(repos, codes, upto=upto, cfg=cfg, cal=cal, split_ratio=split_ratio,
                       hold_since=hold_since,
                       new_high_days=new_high_days)  # fmt: skip
    out: dict[str, Frame] = {}
    for code, fi in fis.items():
        out[code] = build_frame(
            code,
            fi.bars,
            upto=upto,
            anchor=fi.anchor,
            split_ratio=fi.split_ratio,
            flows=fi.flows,
            partial=None if partial is None else partial.get(code),
            partial_flow=None if partial_flows is None else partial_flows.get(code),
            calendar=fi.calendar,
            split_dates=fi.split_dates,
            new_high=fi.new_high,
            hold_since=fi.hold_since,
            split_window=fi.split_window,
            fp_cells=fi.fp_cells,
        )
    return out


def stock_names(market: MarketRepo, day: date) -> dict[str, str]:
    return {u.code: u.name for u in market.universe(day) if u.name}


def market_codes(market: MarketRepo, day: date) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"KOSPI": [], "KOSDAQ": []}
    for u in sorted(market.universe(day), key=lambda u: u.code):
        if u.market in out:
            out[u.market].append(u.code)
    return out


def uses_new_high(rules: Iterable[ActiveRule]) -> bool:
    return any('"new_high"' in a.rule.model_dump_json() for a in rules)


def needs_market(rules: Iterable[ActiveRule]) -> bool:
    return any(isinstance(a.rule.universe, UMarket) for a in rules)
