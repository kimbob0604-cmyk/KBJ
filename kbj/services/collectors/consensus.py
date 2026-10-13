"""`consensus.snapshot` — KIS 추정실적·투자의견 수집 → 목표가 컨센서스·리비전 → 리비전 알림.

근거: docs/p4_design.md §1.6·§3.2·§3.6·§4.7, D-P4-9·10·14·24, ADR 0015·0018, docs/metrics.md
§10. 승격 원본(대체): SD `consensus_snapshot_collector.get_extended_universe`:45(시총 상위 +
밸류체인 350 — 밸류체인은 P5 분류 사전이라 빼고 관심종목으로), `run_daily_snapshot`:233,
`server.py:alert_revision_signals`:5857(inventory #10 — STRONG 만 보내던 것을 |pct| ≥ 5 묶음
1통으로). 네이버 스크래핑(SD `consensus_collector`)은 쓰지 않는다(U4).

**등록부에서 꺼져 있다**(D-P4-10 — 실데이터 확인 #29 전까지, 웨이브 3 에도 켜지 않는다).
처리기·파서·엔진은 합성 fixture·가짜 KIS 로 완성해 둔다. 켜는 것은 사용자 확인 뒤.

한 실행(as_of = 그 거래일, 16:00 — ADR 0018, `market.close_collect` 굳은 의존은 KIS 버킷 차례)
1. 유니버스(`consensus_universe`) = 관심종목(`prv_journal.watchlist` main) ∪ 전 거래일 시총
   상위 `consensus.top_n` 보통주(코스피·코스닥 — KRX 기본정보 `kind=common`). 관심종목이라도
   스팩·리츠·ETF·코넥스는 빼고(사유를 detail 에), 우선주는 보통주(`prv_market.share_class`)로
   바꾼다 — 우선주 컨센서스는 보통주 것. 유니버스(KRX 기본정보)가 없으면 실패(지어내지 않는다).
2. 데이터 키마다(한 키의 실패가 다른 키를 멈추지 않는다 — `_p3.Outcome`):
   - `KIS:consensus_estimate@<날>` → 종목추정실적 TR(`CONSENSUS_TRS`) 종목마다 1회 →
     `prv_fin.consensus_snapshot`(snap_date = 그날). 그날 스냅이 이미 있는 종목은 건너뛴다
     (재시도는 남은 종목만).
   - `KIS:invest_opinion@<날>` → 종목투자의견 TR 종목마다 1회(기간 = 그날 −
     `opinion_window_days` ~ 그날) → `prv_fin.invest_opinion`(증권사·날짜 키 — 다시 받아도 같은
     행).
   - 빈 응답(`KisEmpty`)은 '커버 없음' — 실패가 아니고 행을 만들지 않는다(0 을 사실로 쓰지
     않는다). 일시 실패(5xx·한도·토큰·리미터 대기)는 그 키를 미완으로 두고 재시도가 남은 것을
     받는다. 종목 고유 실패(형식·거절)는 사유를 세고 넘어가되 `PERMANENT_MAX_RATIO` 를 넘으면
     키 실패.
3. 파생(유니버스 종목마다 — 순수 엔진 `kbj.engines.consensus`): 목표가 컨센서스(그날 — 증권사
   0 이면 행 없음)·리비전(FY1·FY2 × 매출·영업익·순익·EPS + 목표가 평균 × 7·30일 — 목표가
   기준선은 의견 이력으로 그 날짜의 컨센서스를 다시 계산). `prv_fin.target_consensus`·
   `revision`(종목·날짜 단위로 통째 교체 — 같은 날 재실행 멱등).
4. 알림: 모든 키를 받았고 파생이 끝났을 때만 — **그날 스냅이 있는** 관심종목의 `alertable`
   리비전(|pct| ≥ `consensus.alert_min_abs_pct` 또는 부호 전환 — 그날 커버가 없어 지난 스냅으로
   계산한 stale 리비전은 저장만 하고 알리지 않는다)을 하루 1통(`alert.revision` — dedup daily,
   0건이면 보내지 않는다). 본문은 템플릿(LLM 없음 — 절대 규칙 3), 이름은 "KIS 추정"([확인 필요
   #29] — 시장 컨센서스인지 모른다, §4.7). 알림을 넣지 못하면 실행 실패 + 키를 미완으로
   (재시도가 다시 보낸다 — daily dedup 이 두 번 가는 것을 막는다). 파생 실패도 키를 미완으로
   둔다.

자원: `repos`(없으면 `connect` 로 `pg_repos`), `kis`(없으면 `KisRestClient.for_service` — auth
토큰 읽기만·앱키 리미터, `_p3.kis_client`), `fin_config`(없으면 `load_fin`), `kr`(없으면 기본
캘린더), `notify`(없으면 보내지 않고 detail 에 적는다), `kis_priority`(시험용). KIS 우선순위는
`config/limits.yaml kis.priorities.consensus`(없으면 P3 — 설계 §1.6).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Final, Protocol, cast

import httpx

from kbj.config.fin import ConsensusCfg, FinConfig, load_fin
from kbj.core.fin_rows import ConsensusRow, OpinionRow, RevisionRow, TargetConsensus
from kbj.core.rows import UniverseRow
from kbj.data.limits import load_limits
from kbj.data.private.kis.consensus import (
    TR_ESTIMATE,
    TR_OPINION,
    params_estimate,
    params_opinion,
    parse_estimate,
    parse_opinion,
)
from kbj.data.private.kis.parse import KisEmpty, KisRejected, require_ok
from kbj.data.private.kis.token import TokenError
from kbj.data.ratelimit import Priority, RateLimitTimeout
from kbj.data.spec import DataKey
from kbj.engines.consensus import LABEL
from kbj.engines.consensus.revision import SignalCuts, alertable, revisions
from kbj.engines.consensus.target import target_consensus
from kbj.engines.consensus.universe import consensus_na
from kbj.services.collectors._p3 import (
    KisGetter,
    NotReady,
    Outcome,
    PartialFailure,
    calendar,
    kis_client,
    reason,
)
from kbj.services.runtime.log import log_event
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos import ConsensusRepo, JournalRepo, MarketRepo, pg_repos

__all__ = [
    "ALERT_KIND",
    "DATASETS",
    "LABEL",
    "PERMANENT_MAX_RATIO",
    "ConsensusRepos",
    "Universe",
    "collect",
    "consensus_universe",
    "derive",
    "revision_text",
    "run",
]

JOB: Final = "consensus.snapshot"
ALERT_KIND: Final = "alert.revision"
WATCH_LIST: Final = "main"
DATASETS: Final[tuple[str, ...]] = ("consensus_estimate", "invest_opinion")  # 처리 순서
PERMANENT_MAX_RATIO: Final = 0.05  # 종목 고유 실패 허용 비율 [확인 필요] — 마감 수집과 같다
PERMANENT_MIN: Final = 3
SAMPLE_MAX: Final = 5
DEFAULT_PRIORITY: Final = Priority.P3  # 설계 §1.6 — limits.yaml 에 consensus 역할이 없을 때
ALERT_MAX_CODES: Final = 20  # 알림 본문에 싣는 종목 수 상한(나머지는 '외 n종목') [제안]
ALERT_MAX_LINES_PER_CODE: Final = 3
_METRIC_KR: Final = {"revenue": "매출", "op": "영업이익", "ni": "순이익", "eps": "EPS"}
_SIGNAL_KR: Final = {
    "STRONG_UP": "강한상향",
    "UP": "상향",
    "NEUTRAL": "중립",
    "DOWN": "하향",
    "STRONG_DOWN": "강한하향",
    "TURN": "부호전환",
}
log = logging.getLogger("kbj.services.collectors")


class ConsensusRepos(Protocol):
    """이 처리기가 쓰는 저장소 — `kbj.store.repos.Repos`·`MemoryRepos` 둘 다 맞는 모양."""

    @property
    def market(self) -> MarketRepo: ...

    @property
    def consensus(self) -> ConsensusRepo: ...

    @property
    def journal(self) -> JournalRepo: ...


# ── 유니버스 ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Universe:
    """컨센서스 수집 대상. `codes` 는 관심종목 먼저(목록 순) → 시총 상위(큰 순), 겹치지 않게."""

    codes: tuple[str, ...]
    names: dict[str, str]
    watch: frozenset[str]  # 관심종목 중 수집 대상이 된 보통주 코드(우선주는 보통주로 바뀐 것)
    excluded: dict[str, str]  # 관심종목 중 뺀 코드 → 사유
    universe_day: date | None


def _eligible(u: UniverseRow) -> str | None:
    """보통주·코스피/코스닥이면 None, 아니면 뺀 사유(엔진 `consensus_na` — API 와 같은 규칙)."""
    na = consensus_na(u)
    if na is None:
        return None
    return f"{na.value} — 시장 {u.market or '모름'}·종류 {u.kind or '모름'}"


def consensus_universe(r: ConsensusRepos, prev: date, *, top_n: int) -> Universe:
    """관심종목 ∪ 전 거래일(prev) 시총 상위 top_n 보통주. 유니버스(KRX 기본정보)가 없으면 실패."""
    rows = r.market.universe(prev)
    if not rows:
        raise RuntimeError(f"{prev} 이하 유니버스(KRX 기본정보)가 없다 — krx.daily 선행 필요")
    uni = {u.code: u for u in rows}
    names = {u.code: u.name for u in rows if u.name}
    codes: list[str] = []
    watch: set[str] = set()
    excluded: dict[str, str] = {}
    items = r.journal.watchlist(WATCH_LIST)
    pref = [i.code for i in items if (u := uni.get(i.code)) is not None and u.kind == "pref"]
    classes = r.consensus.share_classes(pref) if pref else {}
    for item in items:
        code = item.code
        u = uni.get(code)
        if u is None:
            excluded[code] = "유니버스(KRX 기본정보)에 없다"
            continue
        if u.kind == "pref":
            sc = classes.get(code)
            if sc is None or sc.common_code not in uni:
                excluded[code] = "우선주 — 보통주 매핑(share_class) 없음"
                continue
            code = sc.common_code
            u = uni[code]
        why = _eligible(u)
        if why is not None:
            excluded[item.code] = why
            continue
        if code not in watch:
            watch.add(code)
            codes.append(code)
        if item.name_at_add and code not in names:
            names[code] = item.name_at_add
    if top_n > 0:
        snaps, _d = r.market.snapshot(prev)
        ranked = sorted(
            (
                (s.mktcap, code)
                for code, s in snaps.items()
                if s.mktcap is not None and code in uni and _eligible(uni[code]) is None
            ),
            key=lambda t: (-t[0], t[1]),
        )
        for _cap, code in ranked[:top_n]:
            if code not in watch and code not in codes:
                codes.append(code)
    newest = max(u.as_of for u in rows)
    return Universe(tuple(codes), names, frozenset(watch), excluded, newest)


# ── 수집 ────────────────────────────────────────────────────────────────────────────────


def _transient(e: BaseException) -> bool:
    if isinstance(e, KisRejected):
        return e.retryable
    return isinstance(e, TokenError | RateLimitTimeout | OSError | httpx.TransportError | NotReady)


@dataclass
class _Tally:
    """종목 단위 결과 — 커버 없음(빈 응답)·일시 실패·고유 실패를 나눠 센다."""

    total: int
    empty: list[str] = field(default_factory=list[str])
    transient: dict[str, str] = field(default_factory=dict[str, str])
    permanent: dict[str, str] = field(default_factory=dict[str, str])

    def fail(self, code: str, e: BaseException) -> None:
        if isinstance(e, KisEmpty):
            self.empty.append(code)
        elif _transient(e):
            self.transient[code] = reason(e)
        else:
            self.permanent[code] = reason(e)

    def detail(self) -> dict[str, Any]:
        d: dict[str, Any] = {"targets": self.total}
        if self.empty:
            d["empty_codes"] = len(self.empty)
            d["empty_sample"] = sorted(self.empty)[:SAMPLE_MAX]
        if self.permanent:
            d["skipped_codes"] = len(self.permanent)
            d["skipped_sample"] = dict(sorted(self.permanent.items())[:SAMPLE_MAX])
        if self.transient:
            d["retry_codes"] = len(self.transient)
        return d

    def settle(self, what: str) -> None:
        cap = max(PERMANENT_MIN, int(self.total * PERMANENT_MAX_RATIO))
        if len(self.permanent) > cap:
            first = next(iter(sorted(self.permanent.items())))
            raise PartialFailure(
                f"{what}: 종목 고유 실패 {len(self.permanent)}/{self.total} — 허용 {cap} 초과"
                f"(첫 사유 {first[0]}: {first[1]})"
            )
        if self.transient:
            first = next(iter(sorted(self.transient.items())))
            raise PartialFailure(
                f"{what}: {len(self.transient)}/{self.total} 종목 미완 — 재시도가 남은 것만 받는다"
                f"(첫 사유 {first[0]}: {first[1]})"
            )


@dataclass
class _Ctx:
    repos: ConsensusRepos
    kis: KisGetter
    day: date
    now: datetime
    job: str
    cfg: ConsensusCfg
    priority: Priority
    universe: Universe
    detail: dict[str, Any]


def _get(c: _Ctx, tr: Any, params: dict[str, str], what: str) -> dict[str, Any]:
    resp = c.kis.get(tr.path, tr.tr_id, params, priority=c.priority, timeout=None)
    require_ok(what, resp.status, resp.body)
    return resp.body


def _estimates(c: _Ctx) -> int:
    targets = list(c.universe.codes)
    have = {code for code in targets if c.repos.consensus.snapshots(code, c.day, c.day)}
    tally = _Tally(len(targets))
    rows: list[ConsensusRow] = []
    for code in targets:
        if code in have:
            continue
        try:
            body = _get(c, TR_ESTIMATE, params_estimate(code), f"{TR_ESTIMATE.tr_id} {code}")
            rows += parse_estimate(body, code, snap_date=c.day)
        except Exception as e:  # 종목 단위 격리 — 사유를 세고 남긴다
            tally.fail(code, e)
    n = c.repos.consensus.put_snapshot(rows, loaded_by=c.job, now=c.now)
    c.detail["consensus_estimate"] = {
        **tally.detail(),
        "had": len(have),
        "codes": len({r.code for r in rows}),
        "rows": n,
    }
    tally.settle(TR_ESTIMATE.tr_id)
    return n


def _opinions(c: _Ctx) -> int:
    targets = list(c.universe.codes)
    start = c.day - timedelta(days=c.cfg.opinion_window_days)
    tally = _Tally(len(targets))
    rows: list[OpinionRow] = []
    for code in targets:
        try:
            body = _get(c, TR_OPINION, params_opinion(code, start, c.day),
                        f"{TR_OPINION.tr_id} {code}")  # fmt: skip
            rows += [o for o in parse_opinion(body, code) if o.opinion_date <= c.day]
        except Exception as e:  # 종목 단위 격리
            tally.fail(code, e)
    n = c.repos.consensus.put_opinions(rows, loaded_by=c.job, now=c.now)
    c.detail["invest_opinion"] = {
        **tally.detail(),
        "codes": len({r.code for r in rows}),
        "rows": n,
    }
    tally.settle(TR_OPINION.tr_id)
    return n


_HANDLERS: Final[dict[str, Callable[[_Ctx], int]]] = {
    "consensus_estimate": _estimates,
    "invest_opinion": _opinions,
}


def collect(keys: Sequence[DataKey], c: _Ctx) -> Outcome:
    """데이터 키마다 받는다(`DATASETS` 순서). 모르는 데이터셋은 그 키의 실패."""
    out = Outcome(c.job, tuple(keys))
    c.detail = out.detail
    order = {name: i for i, name in enumerate(DATASETS)}
    for key in sorted(keys, key=lambda k: (order.get(k.dataset, len(order)), k.label())):
        fn = _HANDLERS.get(key.dataset)
        if fn is None:
            out.errors[key.label()] = f"모르는 데이터셋: {key.dataset}"
            continue
        if key.as_of[:10] != c.day.isoformat():
            out.errors[key.label()] = f"실행 날짜({c.day})와 키 날짜가 다르다"
            continue
        out.run(key, lambda fn=fn: fn(c))
    return out


# ── 파생 ────────────────────────────────────────────────────────────────────────────────


def _targets_at(
    opinions: Sequence[OpinionRow], days: Iterable[date], cfg: ConsensusCfg
) -> list[TargetConsensus]:
    out: list[TargetConsensus] = []
    for d in sorted(set(days)):
        t = target_consensus(
            opinions, d, window_days=cfg.opinion_window_days, min_brokers_ok=cfg.min_brokers_ok
        )
        if t is not None:
            out.append(t)
    return out


def derive(
    r: ConsensusRepos,
    codes: Sequence[str],
    day: date,
    *,
    cfg: ConsensusCfg,
    now: datetime,
    loaded_by: str,
) -> dict[str, list[RevisionRow]]:
    """종목마다 목표가 컨센서스(그날)와 리비전을 다시 계산해 쓴다. {종목: 리비전 행}."""
    cuts: SignalCuts = cfg.signal_pct
    windows = tuple(cfg.revision_windows)
    span = max(windows)
    out: dict[str, list[RevisionRow]] = {}
    targets_today: list[TargetConsensus] = []
    for code in codes:
        ops = r.consensus.opinions(code, day - timedelta(days=cfg.opinion_window_days + span), day)
        hist = _targets_at(ops, [day, *(day - timedelta(days=w) for w in windows)], cfg)
        today = [t for t in hist if t.as_of == day]
        targets_today += today
        snaps = r.consensus.snapshots(code, day - timedelta(days=span + 31), day)
        if not snaps and not today:
            continue
        revs = revisions(snaps, day, cuts=cuts, windows=windows, targets=hist)
        r.consensus.put_revisions(code, day, revs, loaded_by=loaded_by, now=now)
        out[code] = revs
    r.consensus.put_targets(targets_today, loaded_by=loaded_by, now=now)
    return out


# ── 알림 ────────────────────────────────────────────────────────────────────────────────


def _fy_label(ym: str) -> str:
    return f"FY{ym[:4]}" if ym.endswith("12") else f"FY{ym[:4]}.{ym[4:]}"


def _line(r: RevisionRow) -> str:
    what = f"{_fy_label(r.fiscal_ym)} {_METRIC_KR.get(r.metric, r.metric)}"
    if r.signal == "TURN":
        turn = "적자→흑자" if r.current is not None and r.current > 0 else "흑자→적자"
        return f"{what} {turn}({r.window_days}일)"
    sig = _SIGNAL_KR.get(r.signal or "", "")
    pct = "—" if r.pct is None else f"{r.pct:+.1f}%"
    return f"{what} {pct}({r.window_days}일) {sig}".rstrip()


def _rank(r: RevisionRow) -> tuple[int, float]:
    return (0 if r.signal == "TURN" else 1, -abs(r.pct or 0.0))


def revision_text(
    by_code: dict[str, list[RevisionRow]],
    names: dict[str, str],
    day: date,
    *,
    min_abs_pct: float,
) -> str | None:
    """관심종목 리비전 묶음 본문(템플릿 — 숫자는 행에서만). 대상이 없으면 None(보내지 않는다)."""
    picked = {
        code: sorted(alertable(rows, min_abs_pct=min_abs_pct), key=_rank)
        for code, rows in sorted(by_code.items())
    }
    picked = {code: rows for code, rows in picked.items() if rows}
    if not picked:
        return None
    # 이름은 "KIS 추정 변화"(§4.7 — 시장 컨센서스인지 모른다, [확인 필요 #29]), 창은 행에서
    windows = "·".join(str(w) for w in sorted({x.window_days for v in picked.values() for x in v}))
    lines = [
        f"{LABEL} 변화(리비전) {day.isoformat()} — 관심종목 {len(picked)}개",
        f"기준: {windows}일 전 스냅 대비 |변화| ≥ {min_abs_pct:g}% 또는 부호 전환 · 출처 KIS",
    ]
    order = sorted(picked, key=lambda code: (_rank(picked[code][0]), code))
    for code in order[:ALERT_MAX_CODES]:
        rows = picked[code]
        shown = "; ".join(_line(x) for x in rows[:ALERT_MAX_LINES_PER_CODE])
        rest = len(rows) - ALERT_MAX_LINES_PER_CODE
        more = f" 외 {rest}건" if rest > 0 else ""
        lines.append(f"• {names.get(code, code)}({code}) {shown}{more}")
    if len(order) > ALERT_MAX_CODES:
        lines.append(f"… 외 {len(order) - ALERT_MAX_CODES}종목")
    return "\n".join(lines)


def _fresh_watch(
    r: ConsensusRepos, by_code: dict[str, list[RevisionRow]], watch: frozenset[str], day: date
) -> tuple[dict[str, list[RevisionRow]], list[str]]:
    """알림 대상 = 관심종목 중 **그날 스냅이 있는** 종목의 리비전.

    그날 KIS 가 빈 응답(커버 없음)이었거나 종목 고유 실패였던 종목은 지난 스냅으로 계산한 stale
    리비전만 남는다 — 저장은 하되(API 에는 stale 로 보인다) 오늘의 리비전처럼 알리지 않는다(같은
    지난 값이 커버가 돌아올 때까지 매일 다시 울리는 것도 막는다). 둘째 값 = 그래서 뺀 종목."""
    fresh: dict[str, list[RevisionRow]] = {}
    stale: list[str] = []
    for code, rows in sorted(by_code.items()):
        if code not in watch:
            continue
        if r.consensus.snapshots(code, day, day):
            fresh[code] = rows
        elif alertable(rows, min_abs_pct=0.0):
            stale.append(code)
    return fresh, stale


def _notify(ctx: JobContext, day: date, text: str | None, detail: dict[str, Any]) -> str | None:
    """알림을 넣는다. 실패면 사유(실행 실패로), 아니면 None."""
    if text is None:
        detail["alert"] = "대상 없음 — 보내지 않았다"
        return None
    fn: Callable[..., Any] | None = ctx.resources.get("notify")
    if fn is None:
        detail["alert"] = "notify 자원 없음 — 보내지 않았다"
        return None
    try:
        ticket = fn(text, kind=ALERT_KIND, source=ctx.job or JOB, as_of=day, parse_mode=None)
    except Exception as e:  # 알림 실패는 삼키지 않는다 — 실행 실패로(재시도가 다시 보낸다)
        return f"알림을 넣지 못했다: {reason(e)}"
    if getattr(ticket, "ok", True) is False:
        return f"알림을 넣지 못했다: {str(getattr(ticket, 'reason', ''))[:200]}"
    detail["alert"] = "duplicate" if getattr(ticket, "duplicate", False) else "queued"
    return None


# ── 처리기 ───────────────────────────────────────────────────────────────────────────────


def _repos(ctx: JobContext) -> ConsensusRepos:
    got = ctx.resources.get("repos")
    if got is not None:
        return cast(ConsensusRepos, got)
    return pg_repos(ctx.resource("connect"))


def _cfg(ctx: JobContext) -> FinConfig:
    got = ctx.resources.get("fin_config")
    return got if isinstance(got, FinConfig) else load_fin(settings=ctx.settings)


def _priority(ctx: JobContext) -> Priority:
    got = ctx.resources.get("kis_priority")
    if got is not None:
        return Priority(got)
    src = load_limits(settings=ctx.settings).source("kis")
    role = src.priorities.get("consensus")
    return Priority[role] if role is not None else DEFAULT_PRIORITY


def run(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.collectors.consensus:run` — `consensus.snapshot`)."""
    if not ctx.keys:
        return JobResult("skipped", detail={"reason": "선점한 키가 없다"})
    day = date.fromisoformat(ctx.as_of[:10])
    cfg = _cfg(ctx).consensus
    r = _repos(ctx)
    prev = calendar(ctx).prev_trading_day(day)
    try:
        uni = consensus_universe(r, prev, top_n=cfg.top_n)
    except Exception as e:  # 유니버스가 없으면 모든 키가 미완(지어내지 않는다)
        detail = {"reason": f"유니버스: {reason(e)}"}
        return JobResult("failed", detail=detail)
    with kis_client(ctx) as kis:
        c = _Ctx(
            repos=r,
            kis=kis,
            day=day,
            now=ctx.now,
            job=ctx.job or JOB,
            cfg=cfg,
            priority=_priority(ctx),
            universe=uni,
            detail={},
        )
        out = collect(ctx.keys, c)
    out.detail["universe"] = {
        "codes": len(uni.codes),
        "watch": len(uni.watch),
        "top_n": cfg.top_n,
        "universe_day": None if uni.universe_day is None else uni.universe_day.isoformat(),
    }
    if uni.excluded:
        out.detail["watch_excluded"] = dict(sorted(uni.excluded.items())[:SAMPLE_MAX])
    if out.errors or out.not_ready or len(out.collected) != len(out.keys):
        return out.result()  # 미완 키가 있으면 파생·알림은 재시도 때
    try:
        by_code = derive(r, uni.codes, day, cfg=cfg, now=ctx.now, loaded_by=c.job)
    except Exception as e:  # 파생 실패 — 키를 미완으로(재시도가 다시 받고 다시 계산한다)
        why = reason(e)
        log_event(log, logging.WARNING, "scheduler", "consensus_derive_failed",
                  job=c.job, error=why)  # fmt: skip
        out.errors["derive"] = why
        out.collected.clear()
        return out.result()
    out.detail["revisions"] = {"codes": len(by_code), "rows": sum(len(v) for v in by_code.values())}
    watch_rows, stale = _fresh_watch(r, by_code, uni.watch, day)
    if stale:  # 그날 스냅이 없는 관심종목 — 지난 값으로 알리지 않는다
        out.detail["alert_skipped_stale"] = stale[:SAMPLE_MAX]
    text = revision_text(watch_rows, uni.names, day, min_abs_pct=cfg.alert_min_abs_pct)
    failed = _notify(ctx, day, text, out.detail)
    if failed is not None:
        out.errors["alert"] = failed
        out.collected.clear()
    return out.result()
