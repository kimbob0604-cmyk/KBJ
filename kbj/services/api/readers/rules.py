"""규칙 리더 — `/api/rules`·`/api/rules/{id}`·`/api/rules/signals`·`/api/rules/examples`·검증 시험
평가(docs/p8_design.md §5, 로그인 — DB 만 읽는다, 계약 ⑩). 묶음 EVL(웨이브 2).

- 규칙·판: `ctx.repos.rules`(규칙 머리·판 — 쓰기는 `kbj.services.personal.rules`). 목록의 모드·
  쿨다운·우니버스는 현재 판 DSL 에서, 30일 신호 수·마지막 신호는 신호 뷰(`prv_alerts.signal`)에서
  센다(보냄·발송 꺼짐·문지기 막음 — 쿨다운·건너뜀은 세지 않는다).
- 신호 이력: 기간 기본 최근 7일(최대 `MAX_SIGNAL_DAYS`), 최신 순 `MAX_SIGNALS` 줄. 발송 결과
  (`ops.notify_log`)는 API 가 읽을 저장소가 없어 비워 둔다(notes — [확인 필요: notify_log 읽기]).
- 시험 평가(`rule_trial`): 오늘(마지막 거래일) 원장으로 규칙을 평가 — 프레임 입력은 실시간·
  백테스트와 같은 함수(`kbj.services.engine.rules_input.load_frames`), 저장하지 않는다. 장중 규칙도
  마감 원장으로 평가한다(부분 봉 없음 — notes).
- 예시: `config/rule_examples.yaml`(`load_example_rules` — 검사·정규화된 10개).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from typing import Any, Final, Literal, cast

from kbj.config.rules_cfg import RulesConfig, load_rules_cfg
from kbj.core.quality import Quality
from kbj.core.rules_rows import ALLOWED_SIGNAL_STATUSES, RuleRow, RuleVersion, SignalRow
from kbj.core.time import to_kst
from kbj.engines.board.config import BoardConfig
from kbj.engines.flows.ledger import close_as_of
from kbj.engines.rules.dsl import (
    Rule,
    UMarket,
    UWatchlist,
    conditions,
    dsl_hash,
    load_example_rules,
)
from kbj.engines.rules.evaluate import evaluate
from kbj.engines.rules.live import holdings, universe_codes
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.rules import (
    CondOut,
    RuleDetail,
    RuleExample,
    RuleExamples,
    RuleList,
    RuleSummary,
    RuleTrial,
    RuleVersionOut,
    SignalList,
    SignalModeT,
    SignalOut,
)
from kbj.services.api.readers._common import NoData, ReadContext, envelope, label, worst
from kbj.services.engine.rules_input import load_frames, market_codes, stock_names

__all__ = [
    "MAX_SIGNALS",
    "MAX_SIGNAL_DAYS",
    "MAX_TRIAL",
    "NO_RULES",
    "NO_SIGNALS",
    "SOURCE",
    "rule_detail",
    "rule_examples",
    "rule_signals",
    "rule_trial",
    "rules_list",
    "summary_of",
]

NO_RULES: Final = "아직 없음 — 규칙 없음(페이지 11 편집기에서 만든다)"
NO_SIGNALS: Final = "아직 없음 — rules.intraday 장중 10분 · rules.close 16:00"
SOURCE: Final = "KBJ:rules"
RECENT_DAYS: Final = 30
DEFAULT_SIGNAL_DAYS: Final = 7
MAX_SIGNAL_DAYS: Final = 92
MAX_SIGNALS: Final = 500
MAX_TRIAL: Final = 30
NOTIFY_NOTE: Final = "발송 결과(ops.notify_log) 결합 없음 — 상태(보냄·꺼짐·막힘)는 신호 표 값"
_COUNTED: Final = frozenset(ALLOWED_SIGNAL_STATUSES)

ModeT = Literal["close", "intraday", "both"]
SourceT = Literal["api", "cli", "import:sd", "example"]


def _today(ctx: ReadContext) -> date:
    return to_kst(ctx.now()).date()


def _cfg() -> RulesConfig:
    return load_rules_cfg()


def _version_of(versions: Sequence[RuleVersion], v: int) -> RuleVersion | None:
    return next((x for x in versions if x.version == v), None)


def summary_of(
    head: RuleRow, ver: RuleVersion | None, sigs: Iterable[SignalRow], *, cooldown_default: int
) -> RuleSummary:
    dsl: dict[str, Any] = dict(ver.dsl) if ver is not None else {}
    mine = [s for s in sigs if s.rule_id == head.rule_id and s.status in _COUNTED]
    alert = dsl.get("alert")
    cd = cast(dict[str, Any], alert).get("cooldown_min") if isinstance(alert, dict) else None
    uni = dsl.get("universe")
    return RuleSummary(
        rule_id=head.rule_id,
        name=head.name,
        mode=cast(ModeT, dsl.get("mode", "close")),
        enabled=head.enabled,
        archived=head.archived_at is not None,
        current_version=head.current_version,
        cooldown_min=int(cd) if isinstance(cd, int | float) else cooldown_default,
        universe=dict(cast(dict[str, Any], uni)) if isinstance(uni, dict) else {},
        signals_30d=len(mine),
        last_signal_at=max((s.eval_ts for s in mine), default=None),
        source=cast(SourceT, head.source),
        updated_at=head.updated_at,
    )


def rules_list(ctx: ReadContext) -> Envelope[RuleList]:
    heads = ctx.repos.rules.rules(include_archived=True)
    if not heads:
        raise NoData(NO_RULES)
    today = _today(ctx)
    sigs = ctx.repos.rules.signals(today - timedelta(days=RECENT_DAYS), today)
    cd = _cfg().cooldown_min.default
    rows = [
        summary_of(
            h,
            _version_of(ctx.repos.rules.versions(h.rule_id), h.current_version),
            sigs,
            cooldown_default=cd,
        )
        for h in heads
    ]
    return envelope(
        RuleList(rules=rows),
        source=SOURCE,
        as_of=max(h.updated_at for h in heads),
        quality=Quality.OK,
        notes=[],
        generated_at=ctx.now(),
    )


def rule_detail(ctx: ReadContext, rule_id: str) -> Envelope[RuleDetail]:
    head = ctx.repos.rules.rule(rule_id)
    if head is None:
        raise NoData("없는 규칙")
    vers = ctx.repos.rules.versions(rule_id)
    today = _today(ctx)
    sigs = ctx.repos.rules.signals(today - timedelta(days=RECENT_DAYS), today, rule_id=rule_id)
    summary = summary_of(
        head,
        _version_of(vers, head.current_version),
        sigs,
        cooldown_default=_cfg().cooldown_min.default,
    )
    out = [
        RuleVersionOut(
            version=v.version, dsl=dict(v.dsl), dsl_hash=v.dsl_hash, created_at=v.created_at
        )
        for v in vers
    ]
    return envelope(
        RuleDetail(rule=summary, versions=out),
        source=SOURCE,
        as_of=head.updated_at,
        quality=Quality.OK,
        notes=[],
        generated_at=ctx.now(),
    )


def rule_signals(
    ctx: ReadContext,
    *,
    start: date | None,
    end: date | None,
    rule_id: str | None,
    code: str | None,
    mode: SignalModeT | None,
) -> Envelope[SignalList]:
    notes: list[str] = [NOTIFY_NOTE]
    to = end or _today(ctx)
    frm = start or (to - timedelta(days=DEFAULT_SIGNAL_DAYS - 1))
    if frm > to:
        frm, to = to, frm
    if (to - frm).days + 1 > MAX_SIGNAL_DAYS:
        frm = to - timedelta(days=MAX_SIGNAL_DAYS - 1)
        notes.append(f"기간은 최대 {MAX_SIGNAL_DAYS}일 — 앞을 잘랐다")
    sigs = ctx.repos.rules.signals(frm, to, rule_id=rule_id, code=code, mode=mode)
    if not sigs:
        raise NoData(NO_SIGNALS)
    sigs = sorted(sigs, key=lambda s: (s.eval_ts, s.rule_id, s.code), reverse=True)
    if len(sigs) > MAX_SIGNALS:
        notes.append(f"최신 {MAX_SIGNALS}줄만(전체 {len(sigs)}줄)")
        sigs = sigs[:MAX_SIGNALS]
    names = {h.rule_id: h.name for h in ctx.repos.rules.rules(include_archived=True)}
    stock = stock_names(ctx.repos.market, to)
    rows = [
        SignalOut(
            signal_id=s.signal_id,
            mode=s.mode,
            rule_id=s.rule_id,
            rule_name=names.get(s.rule_id),
            version=s.version,
            code=s.code,
            name=stock.get(s.code),
            eval_ts=s.eval_ts,
            trade_date=s.trade_date,
            entry_price=s.entry_price,
            status=s.status,
            skip_reason=s.skip_reason,
            evidence=dict(s.evidence),
            quality=s.quality,
            notify_status=None,
            notified_at=None,
        )
        for s in sigs
    ]
    return envelope(
        SignalList(signals=rows),
        source=label(s.source for s in sigs),
        as_of=max(s.eval_ts for s in sigs),
        quality=worst(s.quality for s in sigs),
        notes=notes,
        generated_at=ctx.now(),
    )


def rule_examples(ctx: ReadContext) -> Envelope[RuleExamples]:
    rows = [
        RuleExample(
            id=k,
            name=r.name,
            mode=r.mode,
            dsl=r.model_dump(mode="json", exclude_none=True),
            dsl_hash=dsl_hash(r),
        )
        for k, r in load_example_rules().items()
    ]
    return envelope(
        RuleExamples(examples=rows),
        source="KBJ:config/rule_examples.yaml",
        as_of=ctx.now(),
        quality=Quality.OK,
        notes=[],
        generated_at=ctx.now(),
    )


# ── 시험 평가(POST /api/rules/validate) ───────────────────────────────────────────────────────


def _watchlists(ctx: ReadContext, rule: Rule) -> dict[str, list[str]]:
    u = rule.universe
    if not isinstance(u, UWatchlist):
        return {}
    items = sorted(ctx.repos.journal.watchlist(u.watchlist), key=lambda w: (w.sort, w.code))
    return {u.watchlist: [w.code for w in items]}


def rule_trial(ctx: ReadContext, rule: Rule) -> tuple[list[RuleTrial], list[str], datetime | None]:
    """오늘(마지막 거래일) 원장으로 규칙을 평가 — (종목별 결과, notes, 기준 시각).
    저장하지 않는다."""
    today = _today(ctx)
    days = ctx.repos.market.trading_days(today, 1)
    if not days:
        return [], ["원장 일봉이 아직 없다 — 시험 평가 없음"], None
    day = days[-1]
    notes: list[str] = []
    if rule.mode != "close":
        notes.append("장중 규칙도 마감 원장으로 평가했다(부분 봉·장중 가집계 없음)")
    hold = holdings(ctx.repos.journal.trades(), day)
    mk = market_codes(ctx.repos.market, day) if isinstance(rule.universe, UMarket) else None
    codes = universe_codes(rule, watchlists=_watchlists(ctx, rule), holdings=list(hold), market=mk)
    if len(codes) > MAX_TRIAL:
        notes.append(f"앞 {MAX_TRIAL}종목만 평가(우니버스 {len(codes)}종목)")
        codes = codes[:MAX_TRIAL]
    nh = [day] if any(c.ind == "new_high" for _, c in conditions(rule.when)) else []
    frames = load_frames(
        ctx.repos,
        codes,
        upto=day,
        cfg=_cfg(),
        cal=ctx.cal,
        split_ratio=float(BoardConfig.load()["integrity"]["split_guard_ratio"]),
        hold_since=hold,
        new_high_days=nh,
    )
    names = stock_names(ctx.repos.market, day)
    out: list[RuleTrial] = []
    missing = 0
    for code in codes:
        f = frames.get(code)
        if f is None or f.dates[f.last] != day:
            missing += 1
            continue
        v = evaluate(rule, f, f.last)
        conds = [
            CondOut(
                path=r.path,
                ind=r.cond.ind,
                field=r.cond.field,
                op=r.cond.op,
                ok=r.ok,
                value=r.value,
                ref_value=r.ref_value,
                prev_value=r.prev_value,
                prev_ref=r.prev_ref,
                reason=r.reason,
            )
            for r in v.conds
        ]
        out.append(
            RuleTrial(
                code=code,
                name=names.get(code),
                trade_date=day,
                state=v.state,
                skipped_reason=v.skipped_reason,
                conds=conds,
                quality=v.quality,
            )
        )
    if missing:
        notes.append(f"그날 일봉이 없는 종목 {missing}개는 뺐다")
    return out, notes, close_as_of(day)
