"""`rules.intraday`·`rules.close` 처리기 — 규칙 실시간 평가·저장·발송(P8 묶음 EVL,
docs/p8_design.md §1.2·§3.1·§3.2·§4.2~4.4, D-P8-4·6·8·9·10·11·20, 사용자 결정 2026-10-13).

같은 신호의 정의(D-P8-6): 실시간과 백테스트는 같은 프레임(`build_frame`)·평가기(`evaluate`)·
쿨다운 상태기(`allow`)를 부른다. 저장소에서 프레임 입력을 고르는 일도 한 곳
(`kbj.services.engine.rules_input.load_frames` — 백테스트·야간 감시·규칙 검증 API 가 같이 쓴다).

`intraday(ctx)` — 정규장 10분(as_of = 슬롯 시작 `YYYY-MM-DDTHH:MM` KST):
1. 켜진 장중 규칙(`intraday`·`both`)이 없으면 KIS 를 부르지 않고 끝.
2. 대상 = 장중 규칙 우니버스의 합집합(관심 목록·보유·코드), 상한 `intraday_max_codes`
   (D-P8-20) — 넘으면 잘린 종목을 `skipped/truncated` 로 기록하고 detail 에 수.
3. KIS FHKST01010100(리미터 P2 — 앱키당 하나) → 부분 봉(`live.slot_from_quote`) →
   `prv_alerts.watch_quote_slot`(장중 재생 입력). 한 종목 실패는 그 종목만(`skipped/error`).
4. 장중 수급 = 가집계(`prv_flows.investor_intraday` — 쓰기 주인 `flows.intraday`, 등록부가 무른
   의존 5분으로 기다린다)의 **확실히 들어온 가장 최근 입력 회차** 값만(`rules_input.
   live_flow_rounds` — 공식 예제상 하루 4회 입력·순위 목록, `kbj.engines.flows.prelim`). 근거
   문구에 회차 시각('장중 잠정 13:20 회차'). 회차 전·순매수/순매도 두 목록 어디에도 없는
   종목·구분은 수급 조건이 판단 불가 → 그 규칙은 그 종목을 보내지 않는다(`skipped/no_live_flow`,
   지어내지 않음). 가격 조건은 FHKST01010100 슬롯 시세.
5. 평가 → 쿨다운(같은 규칙·종목의 since 이후 신호 — 장중·마감 함께) → 허락이면 `alert.rule`
   (subject `<rule_id>:<code>` — notifier 60분은 안전망) 사건 시점 발송 →
   `prv_alerts.signal_intraday`.

`close(ctx)` — 16:00(굳은 의존 `market.close_collect`, 캐치업 20:30): 원장만(KIS 호출 없음,
상한 없음) → 평가 → 쿨다운 → 허락된 신호를 **한 통 묶음**(`alert.rule_close` — ADR 0018,
0건이면 보내지 않는다) → `prv_alerts.signal_close`. 오늘 행은 KIS 마감 값(estimated 가능),
수급은 원장 행 그대로(장중 잠정과 다르면 일치 감시가 data_revised).

신호 상태(D-P8-10): 쿨다운에 막힘 `cooldown`, 보냄 `sent`, notifier 문지기(중복·60분)가 막음
`suppressed`, 발송 꺼짐(`KBJ_NOTIFY_ENABLED=false` 또는 notify 자원 없음) `dry`. 같은 키
(규칙·판·종목·평가 시각)를 다시 돌리면(재기동·캐치업) 이미 판정된 행은 그대로 두고 다시 보내지
않는다(멱등). 발송 넣기에 실패하면 그 신호는 `skipped/error` 로 남기고(다음 실행이 다시 판정)
실행은 failed.

`command_reader(repos, now)` — 텔레그램 `/시그널` 읽기 함수(저장소만 — 오늘 신호 최신 10).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Final, cast

from kbj.config.rules_cfg import RulesConfig, load_rules_cfg
from kbj.core.calendar import TradingCalendar
from kbj.core.quality import Quality
from kbj.core.rules_rows import QuoteSlot, SignalMode, SignalRow, SignalStatus
from kbj.core.time import to_kst
from kbj.data.private.kis.quotes import bar_from_quote, params_quote, parse_quote
from kbj.data.ratelimit import Priority
from kbj.engines.board.config import BoardConfig
from kbj.engines.rules.cooldown import allow
from kbj.engines.rules.evaluate import Verdict, evaluate
from kbj.engines.rules.evidence import (
    DigestItem,
    SignalLine,
    close_digest,
    evidence_payload,
    signal_text,
    signals_reply,
)
from kbj.engines.rules.frame import Frame
from kbj.engines.rules.live import (
    close_eval_ts,
    holdings,
    select_targets,
    signal_id,
    slot_from_quote,
    universe_codes,
)
from kbj.services.collectors._p3 import KisGetter, kis_client, kis_get, reason, slot_ts
from kbj.services.engine.rules_input import (
    ActiveRule,
    RulesRepos,
    active_rules,
    live_flow_rounds,
    live_flows,
    load_frames,
    market_codes,
    needs_market,
    stock_names,
    uses_new_high,
    watchlists,
)
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos import pg_repos
from kbj.store.repos.rules import RulesRepo

__all__ = [
    "JOB_CLOSE",
    "JOB_INTRADAY",
    "KIND_CLOSE",
    "KIND_RULE",
    "close",
    "command_reader",
    "intraday",
    "live_flow_rounds",
    "live_flows",
    "status_of",
]

log = logging.getLogger(__name__)

JOB_INTRADAY: Final = "rules.intraday"
JOB_CLOSE: Final = "rules.close"
KIND_RULE: Final = "alert.rule"
KIND_CLOSE: Final = "alert.rule_close"
PRIORITY: Final = Priority.P2
TIMEOUT_S: Final = 30.0
SLOT_MIN: Final = 10
NO_FP: Final = hashlib.sha256(b"").hexdigest()[:16]  # 프레임이 없는 기록(잘림·시세 실패)의 지문
# ── 자원 ──────────────────────────────────────────────────────────────────────────────────────


def _repos(ctx: JobContext) -> RulesRepos:
    got = ctx.resources.get("repos")
    if got is not None:
        return cast(RulesRepos, got)
    return pg_repos(ctx.resource("connect"))


def _cfg(ctx: JobContext) -> RulesConfig:
    got = ctx.resources.get("rules_config")
    return got if isinstance(got, RulesConfig) else load_rules_cfg(settings=ctx.settings)


def _cal(ctx: JobContext) -> TradingCalendar:
    got = ctx.resources.get("kr")
    return got if isinstance(got, TradingCalendar) else TradingCalendar.default()


def _split_ratio(ctx: JobContext) -> float:
    got = ctx.resources.get("split_ratio")
    if isinstance(got, int | float):
        return float(got)
    return float(BoardConfig.load()["integrity"]["split_guard_ratio"])


def _base_url(ctx: JobContext) -> str | None:
    got = ctx.resources.get("base_url")
    if isinstance(got, str):
        return got
    return ctx.settings.public_base_url if ctx.settings is not None else None


# ── 규칙 ──────────────────────────────────────────────────────────────────────────────────────


# ── 프레임 입력(실시간·백테스트 공용) ─────────────────────────────────────────────────────────


# ── 상태 ──────────────────────────────────────────────────────────────────────────────────────


def status_of(ticket: object | None) -> SignalStatus | None:
    """notify 결과 → 신호 상태. None = 넣기 실패(사유는 ticket.reason)."""
    if ticket is None:
        return "dry"
    if not getattr(ticket, "ok", False):
        return None
    if getattr(ticket, "duplicate", False):
        return "suppressed"
    if getattr(ticket, "reason", "") == "suppressed":  # 발송 꺼짐 — notifier 가 기록만
        return "dry"
    return "sent"


@dataclass
class _Run:
    mode: SignalMode
    job: str
    ts: datetime
    day: date
    rows: list[SignalRow] = field(default_factory=list[SignalRow])
    counts: dict[str, int] = field(default_factory=dict[str, int])
    errors: dict[str, str] = field(default_factory=dict[str, str])

    def count(self, k: str) -> None:
        self.counts[k] = self.counts.get(k, 0) + 1

    def row(
        self,
        a: ActiveRule,
        code: str,
        *,
        status: SignalStatus,
        evidence: Mapping[str, Any],
        fp: str = NO_FP,
        entry: float | None = None,
        skip: str | None = None,
        ticket: str | None = None,
        quality: Quality = Quality.ESTIMATED,
    ) -> SignalRow:
        r = SignalRow(
            signal_id=signal_id(self.mode, self.ts, a.rule_id, a.version, code),
            mode=self.mode,
            rule_id=a.rule_id,
            version=a.version,
            dsl_hash=a.dsl_hash,
            code=code,
            eval_ts=self.ts,
            trade_date=self.day,
            entry_price=entry,
            evidence=dict(evidence),
            input_fp=fp,
            status=status,
            skip_reason=skip,
            ticket=ticket,
            quality=quality,
            source=f"KBJ:{self.job}",
        )
        self.count(status if skip is None else f"skipped:{skip}")
        return r


@dataclass(frozen=True)
class _Hit:
    """참이고 쿨다운이 허락한 평가 — 발송을 기다린다."""

    a: ActiveRule
    code: str
    verdict: Verdict
    evidence: dict[str, Any]


def _existing(
    prior: Sequence[SignalRow], a: ActiveRule, code: str, ts: datetime
) -> SignalRow | None:
    for s in prior:
        if (s.rule_id, s.version, s.code, s.eval_ts) == (a.rule_id, a.version, code, ts) and (
            s.status in ("sent", "suppressed", "dry", "cooldown")
        ):
            return s
    return None


def _judge(
    run: _Run,
    repo: RulesRepo,
    a: ActiveRule,
    code: str,
    frame: Frame,
    *,
    extra: Mapping[str, Any] | None = None,
) -> _Hit | None:
    """한 (규칙, 종목) 평가 → 기록(건너뜀·쿨다운) 또는 발송 대기."""
    i = frame.last
    v = evaluate(a.rule, frame, i)
    if v.state == "false":
        run.count("false")
        return None
    ev = evidence_payload(a.rule, v, frame, extra=extra)
    if v.state in ("skipped", "unknown"):
        why = v.skipped_reason or "no_live_flow"
        run.rows.append(
            run.row(
                a, code, status="skipped", evidence=ev, fp=v.input_fp, skip=why, quality=v.quality
            )
        )
        return None
    cd = a.rule.alert.cooldown_min or 1440
    prior = repo.signals_since(a.rule_id, code, run.ts - timedelta(minutes=cd))
    if _existing(prior, a, code, run.ts) is not None:
        run.count("kept")  # 같은 키가 이미 판정됐다 — 다시 보내지 않는다(멱등)
        return None
    d = allow(prior, rule_id=a.rule_id, code=code, ts=run.ts, cooldown_min=cd)
    if not d.allowed:
        ev["cooldown_until"] = None if d.until is None else d.until.isoformat()
        run.rows.append(
            run.row(
                a,
                code,
                status="cooldown",
                evidence=ev,
                fp=v.input_fp,
                entry=frame.close[i],
                quality=v.quality,
            )
        )
        return None
    return _Hit(a, code, v, ev)


# ── rules.intraday ────────────────────────────────────────────────────────────────────────────


def _fetch_slots(
    kis: KisGetter, codes: Sequence[str], ts: datetime, now: datetime, errors: dict[str, str]
) -> list[QuoteSlot]:
    out: list[QuoteSlot] = []
    day = to_kst(ts).date()
    for code in codes:
        try:
            body = kis_get(
                kis,
                "stock_quote",
                params_quote(code),
                what=f"FHKST01010100 {code}",
                priority=PRIORITY,
                timeout=TIMEOUT_S,
            )
            snap = parse_quote(body, code, day=day, source="kis", quality=Quality.ESTIMATED)
            bar = bar_from_quote(body, snap)
            out.append(slot_from_quote(snap, bar, slot_ts=ts, received_at=now))
        except Exception as e:  # 한 종목의 실패는 그 종목만(격리) — 사유를 남긴다
            errors[code] = reason(e)
    return out


def intraday(ctx: JobContext) -> JobResult:
    ts = slot_ts(ctx.as_of)
    day = ts.date()
    cal = _cal(ctx)
    if not cal.is_trading_day(day):
        return JobResult("skipped", detail={"reason": "휴장일"})
    repos = _repos(ctx)
    cfg = _cfg(ctx)
    rules, bad = active_rules(repos.rules, cfg, modes=("intraday", "both"))
    if not rules:
        # 'ok' 로 끝내면 선점한 시세 키(watch_quotes_intraday)를 안 받은 것이 실패로 세진다 —
        # 규칙이 없는 날 10분마다 실패가 쌓였다(2026-10-20). 할 일이 없다고 한다.
        return JobResult(
            "skipped", rows=0, detail={"reason": "켜진 장중 규칙 없음", "dsl_invalid": len(bad)}
        )
    run = _Run("intraday", ctx.job or JOB_INTRADAY, ts, day)
    upto = cal.prev_trading_day(day)
    hold = holdings(repos.journal.trades(), upto)
    wl = watchlists(repos.journal, rules)
    universes = {
        a.rule_id: universe_codes(a.rule, watchlists=wl, holdings=list(hold)) for a in rules
    }
    tg = select_targets(
        [(a.rule.alert.priority, universes[a.rule_id]) for a in rules], cfg.intraday_max_codes
    )
    cut = set(tg.truncated)
    kis_errors: dict[str, str] = {}
    with kis_client(ctx) as kis:
        slots = _fetch_slots(kis, tg.codes, ts, ctx.now, kis_errors)
    repos.rules.put_quote_slots_intraday(slots)
    flows_now, flow_rounds = live_flow_rounds(repos.flows, ts)
    frames = load_frames(
        repos,
        [s.code for s in slots],
        upto=upto,
        cfg=cfg,
        cal=cal,
        split_ratio=_split_ratio(ctx),
        partial={s.code: s for s in slots},
        partial_flows=flows_now,
        hold_since=hold,
    )
    names = stock_names(repos.market, upto)
    base = _base_url(ctx)
    notify: Callable[..., Any] | None = ctx.resources.get("notify")
    send_errors = 0
    for a in rules:
        for code in universes[a.rule_id]:
            if code in cut:
                run.rows.append(
                    run.row(a, code, status="skipped", evidence={"v": 1}, skip="truncated")
                )
                continue
            frame = frames.get(code)
            if frame is None:
                why = "error" if code in kis_errors else "no_data"
                run.rows.append(run.row(a, code, status="skipped", evidence={"v": 1}, skip=why))
                continue
            try:
                hit = _judge(run, repos.rules, a, code, frame, extra={"flow_rounds": flow_rounds})
            except Exception as e:  # 한 칸의 실패는 그 칸만
                run.errors[f"{a.rule_id}:{code}"] = reason(e)
                run.rows.append(run.row(a, code, status="skipped", evidence={"v": 1}, skip="error"))
                continue
            if hit is None:
                continue
            text = signal_text(
                hit.evidence,
                rule_name=a.name,
                version=a.version,
                code=code,
                name=names.get(code),
                eval_ts=ts,
                base_url=base,
            )
            ticket: object | None = None
            if notify is not None:
                try:
                    ticket = notify(
                        text,
                        kind=KIND_RULE,
                        source=run.job,
                        as_of=day,
                        subject=f"{a.rule_id}:{code}",
                        parse_mode=None,
                        silent=a.rule.alert.silent or None,
                    )
                except Exception as e:  # 넣기 실패 — 삼키지 않는다(기록 + 실행 failed)
                    ticket = _Failed(reason(e))
            st = status_of(ticket)
            if st is None:
                send_errors += 1
                run.errors[f"{a.rule_id}:{code}"] = str(getattr(ticket, "reason", ""))[:200]
                run.rows.append(
                    run.row(
                        a,
                        code,
                        status="skipped",
                        evidence=hit.evidence,
                        fp=hit.verdict.input_fp,
                        skip="error",
                        quality=hit.verdict.quality,
                    )
                )
                continue
            run.rows.append(
                run.row(
                    a,
                    code,
                    status=st,
                    evidence=hit.evidence,
                    fp=hit.verdict.input_fp,
                    entry=frame.close[frame.last],
                    ticket=getattr(ticket, "id", None),
                    quality=hit.verdict.quality,
                )
            )
    repos.rules.put_signals_intraday(run.rows)
    detail: dict[str, Any] = {
        "rules": len(rules),
        "targets": len(tg.codes),
        "truncated": len(tg.truncated),
        "slots": len(slots),
        "quote_errors": len(kis_errors),
        "live_flow_codes": len(flows_now),
        "flow_rounds": flow_rounds,
        "signals": run.counts,
    }
    if bad:
        detail["dsl_invalid"] = sorted(bad)
    if run.errors:
        detail["errors"] = dict(sorted(run.errors.items())[:20])
    if kis_errors:
        detail["quote_error_codes"] = sorted(kis_errors)[:20]
    failed = send_errors > 0 or (bool(tg.codes) and not slots)
    return JobResult(
        "failed" if failed else "ok",
        collected=tuple(ctx.keys) if slots else (),
        rows=len(slots) + len(run.rows),
        detail=detail,
    )


@dataclass(frozen=True)
class _Failed:
    reason: str
    ok: bool = False


# ── rules.close ───────────────────────────────────────────────────────────────────────────────


def close(ctx: JobContext) -> JobResult:
    day = date.fromisoformat(ctx.as_of[:10]) if ctx.as_of else to_kst(ctx.now).date()
    cal = _cal(ctx)
    if not cal.is_trading_day(day):
        return JobResult("skipped", detail={"reason": "휴장일"})
    repos = _repos(ctx)
    cfg = _cfg(ctx)
    rules, bad = active_rules(repos.rules, cfg, modes=("close", "both"))
    if not rules:
        return JobResult("ok", rows=0, detail={"rules": 0, "dsl_invalid": len(bad)})
    ts = close_eval_ts(day)
    run = _Run("close", ctx.job or JOB_CLOSE, ts, day)
    hold = holdings(repos.journal.trades(), day)
    wl = watchlists(repos.journal, rules)
    mk = market_codes(repos.market, day) if needs_market(rules) else None
    universes = {
        a.rule_id: universe_codes(a.rule, watchlists=wl, holdings=list(hold), market=mk)
        for a in rules
    }
    codes = sorted({c for cs in universes.values() for c in cs})
    frames = load_frames(
        repos,
        codes,
        upto=day,
        cfg=cfg,
        cal=cal,
        split_ratio=_split_ratio(ctx),
        hold_since=hold,
        new_high_days=[day] if uses_new_high(rules) else (),
    )
    names = stock_names(repos.market, day)
    hits: list[_Hit] = []
    for a in rules:
        for code in universes[a.rule_id]:
            frame = frames.get(code)
            if frame is None or frame.dates[frame.last] != day:
                run.rows.append(
                    run.row(
                        a,
                        code,
                        status="skipped",
                        evidence={"v": 1},
                        skip="no_data",
                        quality=Quality.INVALID,
                    )
                )
                continue
            try:
                hit = _judge(run, repos.rules, a, code, frame)
            except Exception as e:
                run.errors[f"{a.rule_id}:{code}"] = reason(e)
                run.rows.append(
                    run.row(
                        a,
                        code,
                        status="skipped",
                        evidence={"v": 1},
                        skip="error",
                        quality=Quality.INVALID,
                    )
                )
                continue
            if hit is not None:
                hits.append(hit)
    text = close_digest(
        [DigestItem(h.a.rule_id, h.a.name, h.code, names.get(h.code), h.evidence) for h in hits],
        day=day,
        base_url=_base_url(ctx),
    )
    ticket: object | None = None
    notify: Callable[..., Any] | None = ctx.resources.get("notify")
    if text is not None and notify is not None:
        silent = all(h.a.rule.alert.silent for h in hits) or None
        try:
            ticket = notify(
                text, kind=KIND_CLOSE, source=run.job, as_of=day, parse_mode=None, silent=silent
            )
        except Exception as e:
            ticket = _Failed(reason(e))
    st = status_of(ticket) if hits else None
    for h in hits:
        f = frames[h.code]
        if st is None:
            run.errors[f"{h.a.rule_id}:{h.code}"] = str(getattr(ticket, "reason", ""))[:200]
            run.rows.append(
                run.row(
                    h.a,
                    h.code,
                    status="skipped",
                    evidence=h.evidence,
                    fp=h.verdict.input_fp,
                    skip="error",
                    quality=h.verdict.quality,
                )
            )
            continue
        run.rows.append(
            run.row(
                h.a,
                h.code,
                status=st,
                evidence=h.evidence,
                fp=h.verdict.input_fp,
                entry=f.close[f.last],
                ticket=getattr(ticket, "id", None),
                quality=h.verdict.quality,
            )
        )
    repos.rules.put_signals_close(run.rows)
    detail: dict[str, Any] = {
        "rules": len(rules),
        "codes": len(codes),
        "signals": run.counts,
        "digest": "none" if text is None else ("dry" if notify is None else str(st)),
    }
    if bad:
        detail["dsl_invalid"] = sorted(bad)
    if run.errors:
        detail["errors"] = dict(sorted(run.errors.items())[:20])
    failed = bool(hits) and st is None
    return JobResult("failed" if failed else "ok", rows=len(run.rows), detail=detail)


# ── /시그널 ───────────────────────────────────────────────────────────────────────────────────


def command_reader(repos: RulesRepos, now: Callable[[], datetime]) -> Callable[[], str]:
    """`/시그널` 읽기 함수 — 오늘(KST) 신호 최신 10(저장소만 읽는다)."""

    def read() -> str:
        day = to_kst(now()).date()
        sigs = repos.rules.signals(day, day)
        names = {r.rule_id: r.name for r in repos.rules.rules(include_archived=True)}
        stock = stock_names(repos.market, day)
        lines = [
            SignalLine(
                s.eval_ts,
                s.mode,
                names.get(s.rule_id, s.rule_id),
                s.code,
                stock.get(s.code),
                s.status,
            )
            for s in sigs
        ]
        return signals_reply(lines, day=day)

    return read
