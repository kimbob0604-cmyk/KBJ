"""잠정실적 알림 작업 — `earnings.alerts`(5분 폴링 — 사건 시점)·`earnings.backfill`(06:30 성과).

docs/p4_design.md §1.4·§3.2·§4.2, D-P4-4·10·14, ADR 0005(outbox·dedup·쿨다운)·0018(16:00 은 시각을
정한 마감 뒤 발송만 — 이 알림은 사건 시점 그대로).

승격 원본: SD `earnings_telegram_sender.send_pending_alerts`:161(발송)·`backfill_alert_performance`
:217(성과), `earnings_alert_writer.build_fallback`:299(본문 — **LLM 은 쓰지 않는다**, Ollama 폐기),
`earnings_signal_classifier.process_earnings_announcement`(분류 흐름). 순수 계산은
`kbj.engines.filings.earnings`, 이 모듈은 저장소와 알림 shim 을 잇는다.

`earnings.alerts`(로그인 — 관심종목·서프라이즈가 들어간다)
1. 최근 `PRELIM_LOOKBACK_DAYS` 일 잠정실적(`pub_filings.earnings_prelim`) 중 분류 기록
   (`prv_fin.earnings_surprise`)이 없는 행 = 미처리(DB 대기 상태 폴링 — D-P4-4). 접수번호 순.
2. 분류: 기본은 전년동기 대비(YoY — 공개 규칙). 분기 컨센서스가 있을 때만(D-P4-10 — KIS 는 연간
   추정뿐일 가능성이 높다) 범위 가드 → 신뢰도 3관문 → 서프라이즈 분류(통과 못 하면 YoY 로 되돌린다).
   분기 컨센서스 = KIS 스냅 중 `extra["period"] == "quarter"` 이고 결산년월이 그 분기말인 행
   [확인 필요 #29 — 묶음 K 와 약속. 그런 행이 없으면 이 길은 쓰이지 않는다].
3. 알림: 관심종목(우선주를 넣었으면 보통주 회사도) && 범위가 자회사 아님 && 우선순위 ≤
   `earnings.alert_max_priority` && 접수일 ≥ 실행일의 `ALERT_FRESH_TRADING_DAYS` 거래일 앞(금요일
   19:59 뒤 접수분을 월요일 07:00 다시 훑기로 처음 보면 아직 알린다. 그보다 늦게 본 옛 공시를
   '사건 시점' 알림으로 보내지 않는다 — 기록만) → `notify(kind='alert.earnings',
   subject=<corp>:<기간>:<범위>)`. 같은 대상(subject)으로 이미 알림을 넣은 기록이 있으면
   (정정공시) 다시 넣지 않고 notes 에만 적는다(notifier 도 subject 30일 dedup 으로 한 번 더
   막는다).
4. 알림을 넣지 못하면(대기열 오류) 그 행은 **기록하지 않는다** — 다음 실행이 다시 분류·알림한다
   (subject dedup 이 두 번 가는 것을 막는다). 실행은 failed(사유). 알림을 먼저 넣고 기록하므로
   기록 전에 죽어도 다음 실행이 duplicate 를 받아 기록만 한다.

`earnings.backfill`(로그인 — 원장 종가)
- 알림을 넣었고 성과를 마치지 않은 행마다: 기준 = 알림 시각 직전 마감 거래일의 종가
  (`price_at_alert`), 3·5·7 **거래일** 뒤 종가로 수익률(%) — 원장 우선순위(krx > kis)는 저장소 몫.
- 그날 원장 행이 있는데 그 종목 봉이 없거나 거래량 0(거래정지)이면 그 칸은 None(지어내지 않는다).
  원장 자체가 아직 없으면 기다린다(`PERF_GIVE_UP_DAYS` 거래일이 지나면 None 으로 마친다).
- 모든 칸이 정해지면 `perf_done`.

자원: `repos`(없으면 `connect` 로 `pg_repos`), `notify`(알림을 넣을 때만 필수), `fin_config`(없으면
`load_fin`), `kr`(없으면 `TradingCalendar.default()`). 시각은 `ctx.now` 만 쓴다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any, Final, Protocol, cast

from kbj.config.fin import FinConfig, load_fin
from kbj.core.calendar import TradingCalendar
from kbj.core.fin_rows import ConsensusRow, PrelimRow, PrelimScope, QuarterRow, SurpriseRow
from kbj.core.masking import mask_text
from kbj.core.quality import Quality
from kbj.core.rows import Bar
from kbj.core.time import to_kst
from kbj.engines.filings import earnings as eng
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos import (
    ConsensusRepo,
    FilingsRepo,
    FinRepo,
    JournalRepo,
    MarketRepo,
    pg_repos,
)
from kbj.store.repos.consensus import with_perf

__all__ = [
    "ALERT_FRESH_TRADING_DAYS",
    "JOB_ALERTS",
    "JOB_BACKFILL",
    "PRELIM_LOOKBACK_DAYS",
    "AlertReport",
    "BackfillReport",
    "EarningsRepos",
    "alerts",
    "backfill",
    "run_alerts",
    "run_backfill",
]

JOB_ALERTS: Final = "earnings.alerts"
JOB_BACKFILL: Final = "earnings.backfill"
ALERT_KIND: Final = "alert.earnings"
PRELIM_LOOKBACK_DAYS: Final = 30  # 미처리 잠정실적을 찾는 기간 [제안]
# 접수일이 실행일의 이 거래일 수 앞보다 오래면 알림 없이 기록만(그 사이 장이 이미 반영) [제안]
ALERT_FRESH_TRADING_DAYS: Final = 1
CONSENSUS_LOOKBACK_DAYS: Final = 90  # 분기 컨센서스 스냅을 찾는 기간(발표 전날까지)
HISTORY_QUARTERS: Final = 20  # 신뢰도 관문 B 의 이력(5년)
PERF_GIVE_UP_DAYS: Final = 10  # 목표 거래일 뒤 이만큼 원장이 없으면 None 으로 마친다
PERF_READY_AFTER: Final = timedelta(minutes=30)  # 정규장 마감 뒤 이만큼 지나면 그날 종가가 있다
REASON_MAX: Final = 200


class EarningsRepos(Protocol):
    """이 작업이 쓰는 저장소 — `kbj.store.repos.Repos`(Pg)·`MemoryRepos`(시험) 둘 다 맞는다."""

    @property
    def filings(self) -> FilingsRepo: ...

    @property
    def consensus(self) -> ConsensusRepo: ...

    @property
    def journal(self) -> JournalRepo: ...

    @property
    def market(self) -> MarketRepo: ...

    @property
    def fin(self) -> FinRepo: ...


class _Ticket(Protocol):
    ok: bool
    id: str | None
    reason: str
    duplicate: bool


Notify = Callable[..., Any]


@dataclass
class AlertReport:
    day: date
    processed: int = 0
    alerts: int = 0
    duplicates: int = 0
    already: int = 0
    errors: dict[str, str] = field(default_factory=dict[str, str])

    def detail(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "day": self.day.isoformat(),
            "processed": self.processed,
            "alerts": self.alerts,
            "duplicates": self.duplicates,
            "already_alerted": self.already,
        }
        if self.errors:
            d["n_errors"] = len(self.errors)
            d["errors"] = dict(sorted(self.errors.items())[:5])
        return d


@dataclass
class BackfillReport:
    day: date
    through: date | None = None
    pending: int = 0
    updated: int = 0
    completed: int = 0
    errors: dict[str, str] = field(default_factory=dict[str, str])

    def detail(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "day": self.day.isoformat(),
            "through": None if self.through is None else self.through.isoformat(),
            "pending": self.pending,
            "updated": self.updated,
            "completed": self.completed,
        }
        if self.errors:
            d["n_errors"] = len(self.errors)
            d["errors"] = dict(sorted(self.errors.items())[:5])
        return d


def _reason(e: BaseException | str) -> str:
    raw = e if isinstance(e, str) else f"{type(e).__name__}: {' '.join(str(e).split())}"
    text = mask_text(raw)
    return text if len(text) <= REASON_MAX else text[: REASON_MAX - 1] + "…"


# ── 분류 ───────────────────────────────────────────────────────────────────────────────


def _quarter_consensus(
    repos: EarningsRepos, code: str | None, row: PrelimRow
) -> ConsensusRow | None:
    """발표 전날까지의 분기 컨센서스 스냅(가장 늦은 것). 없으면 None — P4 기본 경로는 YoY."""
    if code is None or row.is_annual:
        return None
    snaps = repos.consensus.snapshots(
        code,
        row.rcept_dt - timedelta(days=CONSENSUS_LOOKBACK_DAYS),
        row.rcept_dt - timedelta(days=1),
    )
    want = f"{row.period_end:%Y%m}"
    cands = [
        s
        for s in snaps
        if s.fiscal_ym == want and s.is_estimate and s.extra.get("period") == "quarter"
    ]
    return max(cands, key=lambda s: s.snap_date) if cands else None


def _history(repos: EarningsRepos, row: PrelimRow) -> list[eng.Figures]:
    """신뢰도 관문의 분기 이력(발표 전날까지 공시된 것 — PIT, 최근 것부터)."""
    fs = "OFS" if row.scope is PrelimScope.STANDALONE else "CFS"
    qs: list[QuarterRow] = repos.fin.quarters(
        row.corp_code, fs_div=fs, upto_rcept=row.rcept_dt - timedelta(days=1)
    )
    qs = sorted(qs, key=lambda q: (q.fiscal_year, q.fiscal_q), reverse=True)[:HISTORY_QUARTERS]
    return [eng.Figures(q.revenue, q.op) for q in qs]


def _mktcap(repos: EarningsRepos, code: str, before: date) -> int | None:
    snaps, _day = repos.market.snapshot(before)
    s = snaps.get(code)
    return None if s is None else s.mktcap


def classify(
    repos: EarningsRepos, row: PrelimRow, code: str | None, cfg: FinConfig
) -> tuple[eng.EarningsSignal, eng.EarningsSignal, str | None]:
    """(쓸 분류, YoY 분류, 컨센 참조). 분기 컨센서스가 없거나 가드·관문에 걸리면 쓸 분류 = YoY."""
    th = cfg.earnings.thresholds
    yoy = eng.classify_prelim(row, th, tol_pp=cfg.earnings.yoy_reported_tol_pp)
    snap = _quarter_consensus(repos, code, row)
    if snap is None or code is None:
        return yoy, yoy, None
    ref = f"KIS:{snap.snap_date.isoformat()}:{snap.fiscal_ym}"
    cons = eng.Figures(snap.revenue, snap.op)
    guard = eng.scope_guard(
        row.scope, row.revenue_cur, snap.revenue, row.yoy_reported.get("revenue")
    )
    if guard is not None:
        return _with_note(yoy, f"{guard} → 전년동기 대비로 분류"), yoy, ref
    s = eng.sanity(
        cons, _history(repos, row), _mktcap(repos, code, row.rcept_dt - timedelta(days=1))
    )
    if s.recommendation == "skip":
        why = "; ".join(s.reasons) or "관문 실패"
        return _with_note(yoy, f"컨센 신뢰도 낮음({why}) → 전년동기 대비로 분류"), yoy, ref
    actual = eng.Figures(row.revenue_cur, row.op_cur)
    return eng.apply_sanity(eng.classify_surprise(actual, cons, th), s), yoy, ref


def _with_note(sig: eng.EarningsSignal, note: str) -> eng.EarningsSignal:
    return replace(sig, notes=(*sig.notes, note))


# ── earnings.alerts ────────────────────────────────────────────────────────────────────


def _watched_companies(repos: EarningsRepos, list_name: str) -> set[str]:
    """관심종목 코드 + 우선주를 넣었으면 그 보통주 코드(회사 단위 — 잠정실적은 회사 공시다)."""
    codes = repos.journal.watch_codes(list_name)
    classes = repos.consensus.share_classes(codes) if codes else {}
    return set(codes) | {c.common_code for c in classes.values()}


def _alerted(repos: EarningsRepos, corp: str) -> dict[str, SurpriseRow]:
    """이 회사의 이미 알림을 넣은 기록 {subject: 행}(같은 subject 면 먼저 넣은 행)."""
    out: dict[str, SurpriseRow] = {}
    for r in sorted(repos.consensus.surprises(corp_code=corp), key=lambda x: x.rcept_no):
        if r.alerted_at is not None:
            out.setdefault(f"{r.corp_code}:{r.period}:{r.scope.value}", r)
    return out


def run_alerts(
    repos: EarningsRepos,
    day: date,
    *,
    now: datetime,
    cfg: FinConfig,
    notify: Notify | None,
    loaded_by: str = JOB_ALERTS,
    list_name: str = "main",
    calendar: TradingCalendar | None = None,
) -> AlertReport:
    """`earnings.alerts` 한 번(시험·시뮬레이션은 이것을 직접 부른다)."""
    rep = AlertReport(day=day)
    kr = calendar if calendar is not None else TradingCalendar.default()
    fresh_from = day
    for _ in range(ALERT_FRESH_TRADING_DAYS):
        fresh_from = kr.prev_trading_day(fresh_from)
    prelims = repos.filings.prelims(start=day - timedelta(days=PRELIM_LOOKBACK_DAYS), end=day)
    known = repos.consensus.surprises_known(r.rcept_no for r in prelims)
    todo = sorted((r for r in prelims if r.rcept_no not in known), key=lambda r: r.rcept_no)
    if not todo:
        return rep
    watched = _watched_companies(repos, list_name)
    sent: dict[str, dict[str, SurpriseRow]] = {}
    for row in todo:
        try:
            rec = _one(repos, row, fresh_from, now=now, cfg=cfg, notify=notify,
                       loaded_by=loaded_by, watched=watched, sent=sent, rep=rep)  # fmt: skip
        except Exception as e:  # 한 건 — 기록하지 않고 다음 실행이 다시(실행은 failed)
            rep.errors[row.rcept_no] = _reason(e)
            continue
        repos.consensus.put_surprises([rec], loaded_by=loaded_by, now=now)
        rep.processed += 1
    return rep


def _one(
    repos: EarningsRepos,
    row: PrelimRow,
    fresh_from: date,
    *,
    now: datetime,
    cfg: FinConfig,
    notify: Notify | None,
    loaded_by: str,
    watched: set[str],
    sent: dict[str, dict[str, SurpriseRow]],
    rep: AlertReport,
) -> SurpriseRow:
    d = repos.filings.disclosure(row.rcept_no)
    code = d.stock_code if d is not None else None
    sig, yoy, ref = classify(repos, row, code, cfg)
    notes = list(sig.notes)
    subject = eng.alert_subject(row)
    alerted_at: datetime | None = None
    ticket_id: str | None = None
    want = (
        code is not None
        and code in watched
        and row.scope is not PrelimScope.SUBSIDIARY
        and sig.priority <= cfg.earnings.alert_max_priority
    )
    if want and row.quality is Quality.INVALID:
        # 계산에 쓰면 안 되는 행(절대 규칙 2) — 분류는 기록하되 알림의 숫자로 내보내지 않는다
        want = False
        notes.append("잠정실적 행 품질 invalid — 알림 없이 기록만")
    if want and row.rcept_dt < fresh_from:
        want = False
        notes.append(f"접수일 {row.rcept_dt.isoformat()} — 늦게 처리해 알림 없이 기록만")
    if want:
        if row.corp_code not in sent:
            sent[row.corp_code] = _alerted(repos, row.corp_code)
        prior = sent[row.corp_code].get(subject)
        if prior is not None:
            want = False
            rep.already += 1
            notes.append(f"같은 대상 알림을 이미 넣음(접수 {prior.rcept_no}) — 다시 보내지 않는다")
            if prior.signal != sig.signal:
                notes.append(f"분류 변경 {prior.signal} → {sig.signal}")
    if want:
        if notify is None:
            raise RuntimeError("자원 notify 가 주입되지 않았다 — 알림을 넣지 못했다")
        text = eng.alert_text(
            row,
            sig,
            name=d.corp_name if d is not None else row.corp_code,
            code=code,
            link=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={row.rcept_no}",
            correction=d is not None and d.is_correction,
        )
        ticket = cast(
            _Ticket,
            notify(text, kind=ALERT_KIND, source=loaded_by, as_of=row.rcept_dt, subject=subject),
        )
        if not getattr(ticket, "ok", False):
            raise RuntimeError(f"알림을 넣지 못했다: {getattr(ticket, 'reason', '사유 없음')}")
        alerted_at = now
        ticket_id = getattr(ticket, "id", None)
        if getattr(ticket, "duplicate", False):
            rep.duplicates += 1
            notes.append("알림 대기열 중복 키 — 이미 넣은 알림")
        else:
            rep.alerts += 1
    # invalid 는 invalid 그대로(estimated 로 올리지 않는다), 기간 추정·그 밖 품질은 estimated
    if row.quality is Quality.INVALID:
        quality = Quality.INVALID
    elif row.period_inferred or row.quality is not Quality.OK:
        quality = Quality.ESTIMATED
    else:
        quality = Quality.OK
    rec = SurpriseRow(
        rcept_no=row.rcept_no,
        code=code,
        corp_code=row.corp_code,
        period=row.period,
        scope=row.scope,
        signal=sig.signal,
        priority=sig.priority,
        rev_yoy=yoy.rev_pct,
        op_yoy=yoy.op_pct,
        rev_surprise=sig.rev_pct if sig.method == "surprise" else None,
        op_surprise=sig.op_pct if sig.method == "surprise" else None,
        consensus_ref=ref,
        alerted_at=alerted_at,
        notify_ticket=ticket_id,
        notes=tuple(dict.fromkeys(n for n in notes if n.strip())),
        source="DART" if ref is None else "DART+KIS",
        quality=quality,
    )
    if alerted_at is not None:
        sent.setdefault(row.corp_code, {})[subject] = rec
    return rec


# ── earnings.backfill ──────────────────────────────────────────────────────────────────


def _close(bar: Bar | None) -> float | None:
    """거래가 있었던 날의 종가. 봉이 없거나 거래량 0(KRX 정지일 행)·종가 없음이면 None."""
    if bar is None or bar.close is None or bar.close <= 0 or bar.volume == 0:
        return None
    return bar.close


def _through(day: date, now: datetime, kr: TradingCalendar) -> date:
    """종가가 있을 수 있는 마지막 거래일(그날 마감 + 여유가 지났으면 그날, 아니면 앞 거래일)."""
    if kr.is_trading_day(day):
        _open, close = kr.equity_bounds(day)
        if to_kst(now) >= close + PERF_READY_AFTER:
            return day
    return kr.prev_trading_day(day)


def run_backfill(
    repos: EarningsRepos,
    day: date,
    *,
    now: datetime,
    cfg: FinConfig,
    calendar: TradingCalendar,
    loaded_by: str = JOB_BACKFILL,
) -> BackfillReport:
    """`earnings.backfill` 한 번 — 알림 후 3·5·7 거래일 성과."""
    rep = BackfillReport(day=day)
    pend = repos.consensus.perf_pending()
    rep.pending = len(pend)
    if not pend:
        return rep
    kr = calendar
    through = _through(day, now, kr)
    rep.through = through
    data_days = set(repos.market.trading_days(through, 400))
    ks = tuple(cfg.earnings.perf_days)
    out: list[SurpriseRow] = []
    for row in pend:
        try:
            new = _perf_one(repos, row, through, kr, ks, data_days)
        except Exception as e:  # 한 건 — 다음 실행이 다시(실행은 failed)
            rep.errors[row.rcept_no] = _reason(e)
            continue
        if new is not None:
            out.append(new)
            rep.updated += 1
            rep.completed += int(new.perf_done)
    if out:
        repos.consensus.put_surprises(out, loaded_by=loaded_by, now=now)
    return rep


def _perf_one(
    repos: EarningsRepos,
    row: SurpriseRow,
    through: date,
    kr: TradingCalendar,
    ks: tuple[int, ...],
    data_days: set[date],
) -> SurpriseRow | None:
    if row.alerted_at is None:
        return None
    if row.code is None:  # 종목코드 없는 회사 — 원장이 없다
        return with_perf(row, price_at_alert=None, perf=(None, None, None), done=True)
    base = eng.alert_base_day(row.alerted_at, kr)
    if through < base:
        return None
    targets = eng.perf_days(base, ks, kr)
    # 원장 창은 표에 행이 있는 날 기준이다 — 달력과 어긋나도 기준일이 빠지지 않게 여유를 둔다
    n = kr.trading_days_between(base, through) + 1 + 10
    bars = {b.date: b for b in repos.market.series([row.code], through, n).get(row.code, [])}
    price = row.price_at_alert if row.price_at_alert is not None else _close(bars.get(base))

    def settled(d: date, value: float | None) -> bool:
        """값이 있거나, 그날 원장이 있는데 이 종목 거래가 없었거나(정지), 너무 오래 기다렸다."""
        return (
            value is not None
            or d in data_days
            or kr.trading_days_between(d, through) >= PERF_GIVE_UP_DAYS
        )

    base_settled = settled(base, price)
    current = {3: row.perf_3d, 5: row.perf_5d, 7: row.perf_7d}
    vals: dict[int, float | None] = dict(current)
    resolved: dict[int, bool] = {}
    for k in (3, 5, 7):
        t = targets.get(k)
        if t is None:  # 설정에 없는 칸 — 기록하지 않는다
            vals[k] = None
            resolved[k] = True
            continue
        if current[k] is not None:
            resolved[k] = True
            continue
        if t > through:
            resolved[k] = False
            continue
        close = _close(bars.get(t))
        if not (base_settled and settled(t, close)):
            resolved[k] = False  # 원장이 아직 없다 — 기다린다
            continue
        # 정지로 종가(또는 기준 종가)가 없으면 None 으로 마친다 — 지어내지 않는다
        vals[k] = eng.perf_pct(price, close)
        resolved[k] = True
    done = all(resolved.values())
    perf = (vals[3], vals[5], vals[7])
    if (price, perf, done) == (
        row.price_at_alert,
        (row.perf_3d, row.perf_5d, row.perf_7d),
        row.perf_done,
    ):
        return None
    return with_perf(row, price_at_alert=price, perf=perf, done=done)


# ── 작업 처리기(config/jobs.yaml owner) ───────────────────────────────────────────────────


def _repos(ctx: JobContext) -> EarningsRepos:
    """주입된 저장소(`repos` — 시험·시뮬레이션), 없으면 연결 공장(`connect`)으로 Pg 저장소."""
    repos = ctx.resources.get("repos")
    if repos is not None:
        return cast(EarningsRepos, repos)
    return pg_repos(ctx.resource("connect"))


def _run_day(ctx: JobContext) -> date:
    try:
        return date.fromisoformat(ctx.as_of[:10])
    except ValueError:
        return to_kst(ctx.now).date()


def _calendar(ctx: JobContext) -> TradingCalendar:
    kr = ctx.resources.get("kr")
    return kr if isinstance(kr, TradingCalendar) else TradingCalendar.default()


def _cfg(ctx: JobContext) -> FinConfig:
    got = ctx.resources.get("fin_config")
    return got if isinstance(got, FinConfig) else load_fin(settings=ctx.settings)


def _result(
    errors: dict[str, str], rows: int, detail: dict[str, Any], keys: Iterable[Any]
) -> JobResult:
    if errors:
        return JobResult("failed", rows=rows, detail=detail)
    return JobResult("ok", collected=tuple(keys), rows=rows, detail=detail)


def alerts(ctx: JobContext) -> JobResult:
    """`earnings.alerts`(5분 — 사건 시점) 처리기."""
    rep = run_alerts(
        _repos(ctx),
        _run_day(ctx),
        now=ctx.now,
        cfg=_cfg(ctx),
        notify=ctx.resources.get("notify"),
        loaded_by=ctx.job or JOB_ALERTS,
        calendar=_calendar(ctx),
    )
    return _result(rep.errors, rep.processed, rep.detail(), ctx.keys)


def backfill(ctx: JobContext) -> JobResult:
    """`earnings.backfill`(06:30 거래일) 처리기."""
    rep = run_backfill(
        _repos(ctx),
        _run_day(ctx),
        now=ctx.now,
        cfg=_cfg(ctx),
        calendar=_calendar(ctx),
        loaded_by=ctx.job or JOB_BACKFILL,
    )
    return _result(rep.errors, rep.updated, rep.detail(), ctx.keys)
