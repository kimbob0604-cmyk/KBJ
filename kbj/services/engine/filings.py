"""`filings.derive` — 공시 파생값(오버행 상태·요약, 내부자 창, 실적 일정, 공시 키워드 점수)을
`pub_filings.*` 에 미리 쓴다(공개 작업 — docs/p4_design.md §1.4·§3.2·§4.1~§4.4, D-P4-4·17).

순수 계산은 `kbj.engines.filings`(I/O 없음), 읽기·쓰기는 공개 저장소(`kbj.store.repos.public`), 이
모듈은 둘을 잇는 실행만 한다. 공개 내보내기는 엔진을 부르지 못하므로(계약 ⑧) 공개 파생값은 이 작업이
미리 쓴다(D-P4-17).

한 실행(`10,40 7-21 * * *`, as_of = 분 — 날짜 부분이 파생 행의 as_of)
1. **키워드 점수**: 최근 `SCORE_LOOKBACK_DAYS` 일 공시 중 점수가 없는 행(`kw_score` None)에
   `importance.keyword_score` 를 채운다(07:00 다시 훑기가 넣은 전 영업일 행 포함).
2. **대상 회사**
   - 증분: 최근 `CHANGE_WINDOW` 안에 처음 본 공시(또는 재무 적재 — 주식총수가 바뀐다)가 있는 회사 중
     종류가 맞는 회사(오버행 ← 결정·청구·조정·정기보고서, 내부자 ← 소유보고·정기보고서, 일정 ←
     정기보고서·결산실적공시예고).
   - **훑기(sweep)**: 그날 파생 행이 아직 없을 때(그날 첫 실행)와 밤 마무리(21시대 두 번)에는
     날짜가 지나면서 바뀌는 값(창에서 빠지는 보고·청구기간 시작·상장일·법정 기한)을 위해 관련 회사
     전체를 다시 계산한다 — 오버행: 상태가 아직 바뀔 수 있는 결정(만기·상장일·추정 보호예수 + 30일
     안)이 있는 회사, 내부자: 최근 190일 소유보고 공시가 있는 회사(창에서 마지막 보고가 빠지는 날 0
     행을 쓴다), 일정: 상장사 전체(한 번에 통째로 바꾼다).
3. 회사마다 엔진을 불러 (회사, as_of) 단위로 통째로 바꾼다(같은 as_of 재실행 멱등 — 저장소 규칙).

PIT: 결정·조정·보고는 접수일 ≤ as_of 만, 분모(주식총수)는 접수일 ≤ as_of 인 가장 최근 정기보고서.

실패 격리(절대 규칙 4): 회사 하나의 실패는 그 회사만 — 사유를 모아 실행을 `failed` 로 돌려준다(다른
회사는 계속 쓴다). 저장소 예외를 삼키지 않는다.

계약 ⑬(공개 작업): 로그인 모듈(`kbj.data.private`·`kbj.engines.{consensus,valuation}`·
`kbj.store.repos.{consensus,journal}`)과 `kbj.store.repos` 패키지 이름을 import 하지 않는다 —
저장소는 `kbj.store.repos.public`·`.filings` 에서만 가져온다. 알림은 하지 않는다(일반 공시 push
없음 — D-P4-14).

자원(`JobContext.resources`): `public_repos`(없으면 `repos.public()`, 그것도 없으면 `connect` 로
`pg_public_repos`), `fin_config`(없으면 `load_fin`), `kr`(없으면 `TradingCalendar.default()`).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any, Final, Protocol

from kbj.config.fin import FinConfig, load_fin
from kbj.core.calendar import TradingCalendar
from kbj.core.fin_rows import (
    INSIDER_WINDOWS,
    DisclosureRow,
    Instrument,
    OverhangEvent,
    ScheduleRow,
)
from kbj.core.masking import mask_text
from kbj.core.quality import Quality
from kbj.core.time import to_kst
from kbj.data.public.dart.overhang_doc import adjust_instrument
from kbj.data.public.dart.prelim_doc import decode_pre_announce
from kbj.engines.filings import importance, insider, overhang, schedule
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos.filings import FilingsRepo
from kbj.store.repos.public import pg_public_repos

__all__ = [
    "CHANGE_WINDOW",
    "JOB",
    "DeriveReport",
    "DeriveRepos",
    "derive",
    "derive_insider",
    "derive_overhang",
    "derive_schedule",
    "run_derive",
    "score_disclosures",
    "should_sweep",
]

JOB: Final = "filings.derive"
SCORE_LOOKBACK_DAYS: Final = 7  # 점수 없는 공시를 찾는 기간(연휴 뒤 07:00 다시 훑기 포함) [제안]
# 증분 대상 = 이 시간 안에 처음 본 공시(30분 주기 + 실패·재시도 여유 — 놓친 것은 훑기가 채운다)
CHANGE_WINDOW: Final = timedelta(minutes=90)
NIGHT_SWEEP_HOUR: Final = 21  # 21:10·21:40 — 밤 마무리 훑기(KST)
OVERHANG_LOOKBACK_YEARS: Final = 10  # 결정·조정 공시를 보는 기간(사채 만기 보통 3~5년, 넉넉히)
OVERHANG_GRACE_DAYS: Final = 30  # 상태가 끝난 뒤에도 이만큼은 다시 계산(마지막 비활성 행을 남긴다)
INSIDER_FETCH_DAYS: Final = 240  # 창(180일) + 정정 짝을 찾을 여유
INSIDER_SWEEP_DAYS: Final = 190  # 훑기 대상 = 이 안에 소유보고 공시가 있는 회사
PERIODIC_LOOKBACK_DAYS: Final = 400  # 정기보고서 제목(제출한 기간·반기 보고 회사 판정)
PRE_ANNOUNCE_LOOKBACK_DAYS: Final = 200
UNPARSED_STATES: Final = frozenset({"pending", "failed", "na"})
OVERHANG_KINDS: Final = frozenset({"overhang", "overhang_exercise", "price_adjust"})
ADJUST_KINDS: Final = frozenset({"overhang_exercise", "price_adjust"})
REASON_MAX: Final = 200


class DeriveRepos(Protocol):
    """이 작업이 쓰는 공개 저장소 — `PublicRepos`(Pg)·`MemoryPublicRepos`(시험) 둘 다 맞는다."""

    @property
    def filings(self) -> FilingsRepo: ...


@dataclass
class DeriveReport:
    """한 실행 결과(작업 detail — 값은 싣지 않는다)."""

    day: date
    sweep: bool = False
    n_scored: int = 0
    n_overhang: int = 0
    n_insider: int = 0
    n_schedule_corps: int = 0
    rows: int = 0
    errors: dict[str, str] = field(default_factory=dict[str, str])

    @property
    def status(self) -> str:
        return "failed" if self.errors else "ok"

    def detail(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "as_of": self.day.isoformat(),
            "sweep": self.sweep,
            "scored": self.n_scored,
            "overhang_corps": self.n_overhang,
            "insider_corps": self.n_insider,
            "schedule_corps": self.n_schedule_corps,
        }
        if self.errors:
            d["n_errors"] = len(self.errors)
            d["errors"] = dict(sorted(self.errors.items())[:5])
        return d


def _reason(e: BaseException) -> str:
    text = mask_text(f"{type(e).__name__}: {' '.join(str(e).split())}")
    return text if len(text) <= REASON_MAX else text[: REASON_MAX - 1] + "…"


def _years_back(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # 2월 29일
        return d.replace(year=d.year - years, day=28)


# ── 1. 키워드 점수 ──────────────────────────────────────────────────────────────────────


def score_disclosures(f: FilingsRepo, rows: Iterable[DisclosureRow], cfg: FinConfig) -> int:
    """점수가 없는 공시 행에 키워드 점수·맞은 키워드를 채운다(공개 — 관심·시총 가산은 읽을 때)."""
    scores: dict[str, tuple[int, Sequence[str]]] = {}
    for d in rows:
        if d.kw_score is None:
            ks = importance.keyword_score(d.title, cfg.filings.keywords)
            scores[d.rcept_no] = (ks.score, ks.matched)
    return f.set_scores(scores) if scores else 0


# ── 2. 훑기 판정·대상 ───────────────────────────────────────────────────────────────────


def should_sweep(f: FilingsRepo, day: date, now: datetime) -> bool:
    """밤 마무리(21시대)이거나 그날 파생 행(오버행 요약·내부자 30일 창)이 아직 없으면 훑는다."""
    if to_kst(now).hour >= NIGHT_SWEEP_HOUR:
        return True
    return not f.overhang_summaries(day) and not f.insider_windows_on(day, 30)


def _live_overhang_corps(
    events: Iterable[OverhangEvent], day: date, lockup_months: int
) -> set[str]:
    """상태가 아직 바뀔 수 있는 결정(끝날 날 + 여유 ≥ 오늘, 끝을 모르면 계속)이 있는 회사."""
    out: set[str] = set()
    for ev in events:
        if ev.instrument is Instrument.BONUS:
            continue
        end = overhang.event_horizon(ev, lockup_months)
        if end is None or end + timedelta(days=OVERHANG_GRACE_DAYS) >= day:
            out.add(ev.corp_code)
    return out


# ── 3. 회사별 파생 ──────────────────────────────────────────────────────────────────────


def derive_overhang(
    f: FilingsRepo,
    corp: str,
    day: date,
    *,
    cfg: FinConfig,
    now: datetime,
    loaded_by: str,
    events: Sequence[OverhangEvent] | None = None,
) -> int:
    """회사 하나의 오버행 상태·요약(as_of = day)을 통째로 바꾼다. 결정 공시가 없으면 쓰지 않고
    0."""
    evs = (
        list(events)
        if events is not None
        else f.overhang_events(corp, start=_years_back(day, OVERHANG_LOOKBACK_YEARS), end=day)
    )
    evs = [e for e in evs if e.rcept_dt <= day]
    if not evs:
        return 0
    adjusts = f.overhang_adjusts(corp)
    discl = f.disclosures(
        start=_years_back(day, OVERHANG_LOOKBACK_YEARS),
        end=day,
        corp_code=corp,
        kinds=OVERHANG_KINDS,
    )
    corrections = {d.rcept_no for d in discl if d.is_correction}
    unparsed: dict[str, Instrument | None] = {
        d.rcept_no: adjust_instrument(d.title)
        for d in discl
        if d.kind in ADJUST_KINDS and d.parse_state in UNPARSED_STATES
    }
    months = cfg.overhang.lockup_months_third_party
    states = overhang.state_at(
        evs, adjusts, day, lockup_months=months, corrections=corrections, unparsed=unparsed
    )
    shares = f.latest_share_count(corp, upto=day)
    summ = overhang.summary(states, shares, day, corp_code=corp, events=evs)
    return f.put_overhang_state(corp, day, states, summ, loaded_by=loaded_by, now=now)


def derive_insider(f: FilingsRepo, corp: str, day: date, *, now: datetime, loaded_by: str) -> int:
    """회사 하나의 30·90·180일 창(as_of = day)을 통째로 바꾼다. 소유보고 공시도 보고 행도 없으면
    쓰지 않고 0."""
    start = day - timedelta(days=INSIDER_FETCH_DAYS)
    reports = f.insider_reports(corp, start=start, end=day)
    discl = f.disclosures(start=start, end=day, corp_code=corp, kinds={"insider"})
    if not reports and not discl:
        return 0
    corrections = {d.rcept_no for d in discl if d.is_correction}
    recent = day - timedelta(days=max(INSIDER_WINDOWS))
    unparsed = any(d.parse_state in UNPARSED_STATES and d.rcept_dt >= recent for d in discl)
    shares = f.latest_share_count(corp, upto=day)
    rows = insider.windows(
        reports,
        day,
        corp_code=corp,
        # invalid 분모는 쓰지 않는다(오버행 요약과 같은 규칙)
        denominator=(
            None if shares is None or shares.quality is Quality.INVALID else shares.common_issued
        ),
        corrections=corrections,
    )
    if unparsed:  # 파싱하지 못한 소유보고가 창 안에 있다 — 합이 빠졌을 수 있다
        rows = [replace(r, quality=Quality.ESTIMATED) for r in rows]
    return f.put_insider_windows(corp, day, rows, loaded_by=loaded_by, now=now)


def derive_schedule(
    f: FilingsRepo,
    day: date,
    *,
    corps: Collection[str] | None,
    now: datetime,
    loaded_by: str,
    calendar: TradingCalendar | None,
    errors: dict[str, str],
) -> tuple[int, int]:
    """실적 일정 — `corps` None 이면 상장사 전체를 한 번에 통째로 바꾼다. (회사 수, 쓴 행 수)."""
    profiles = f.profiles(None if corps is None else corps)
    per_start = day - timedelta(days=PERIODIC_LOOKBACK_DAYS)
    pre_start = day - timedelta(days=PRE_ANNOUNCE_LOOKBACK_DAYS)
    periodic: list[DisclosureRow] = []
    pre: list[DisclosureRow] = []
    if corps is None:
        periodic = f.disclosures(start=per_start, end=day, kinds={"periodic"})
        pre = f.disclosures(start=pre_start, end=day, kinds={"pre_announce"})
    else:
        for c in sorted(corps):
            periodic += f.disclosures(start=per_start, end=day, corp_code=c, kinds={"periodic"})
            pre += f.disclosures(start=pre_start, end=day, corp_code=c, kinds={"pre_announce"})
    titles: dict[str, list[str]] = defaultdict(list)
    listed: dict[str, tuple[int | None, str | None]] = {}  # corp → (결산월, corp_cls)
    for d in periodic:
        if d.rcept_dt <= day:
            titles[d.corp_code].append(d.title)
    items: dict[str, list[schedule.PreAnnounceItem]] = defaultdict(list)
    for d in pre:
        if d.rcept_dt > day or d.parse_state != "done":
            continue
        got = decode_pre_announce(d.parse_note)
        if got is not None:
            items[d.corp_code].append(
                schedule.PreAnnounceItem(d.rcept_no, d.rcept_dt, got.planned_date, got.target)
            )
    for p in profiles.values():
        if p.stock_code is not None:
            listed[p.corp_code] = (p.acc_mt, p.corp_cls)
    # 기업개황이 아직 없는 상장사(주 1회 수집 전 신규상장 등) — 결산월 모름(12월 가정, 추정)
    for d in [*periodic, *pre]:
        if d.stock_code is not None and d.corp_code not in listed and d.corp_code not in profiles:
            listed[d.corp_code] = (None, d.corp_cls)
    target = set(listed) if corps is None else set(corps)
    rows: list[ScheduleRow] = []
    done: set[str] = set()
    for corp in sorted(target):
        if corp not in listed:
            done.add(corp)  # 상장사가 아니다 — 옛 일정 행만 지운다
            continue
        acc_mt, corp_cls = listed[corp]
        try:
            rows += schedule.schedule_rows(
                corp,
                acc_mt,
                day,
                periodic_titles=titles.get(corp, []),
                pre_announces=items.get(corp, []),
                corp_cls=corp_cls,
                calendar=calendar,
            )
        except Exception as e:  # 회사 하나 — 사유를 모으고 계속(실행은 failed)
            errors[f"schedule:{corp}"] = _reason(e)
            continue
        done.add(corp)
    if corps is None:
        done |= set(profiles)  # 상장폐지 등 — 옛 행을 지운다
    if not done:
        return 0, 0
    return len(done), f.replace_schedule(done, rows, loaded_by=loaded_by, now=now)


# ── 실행 ──────────────────────────────────────────────────────────────────────────────


def _each(
    corps: Iterable[str],
    part: str,
    fn: Callable[[str], int],
    errors: dict[str, str],
) -> tuple[int, int]:
    n_corps = rows = 0
    for corp in sorted(corps):
        try:
            got = fn(corp)
        except Exception as e:  # 회사 하나 — 사유를 모으고 계속(실행은 failed)
            errors[f"{part}:{corp}"] = _reason(e)
            continue
        if got:
            n_corps += 1
            rows += got
    return n_corps, rows


def run_derive(
    repos: DeriveRepos,
    day: date,
    *,
    now: datetime,
    cfg: FinConfig,
    calendar: TradingCalendar | None = None,
    loaded_by: str = JOB,
    sweep: bool | None = None,
) -> DeriveReport:
    """`filings.derive` 한 번(시험·시뮬레이션은 이것을 직접 부른다)."""
    f = repos.filings
    rep = DeriveReport(day=day)
    recent = f.disclosures(start=day - timedelta(days=SCORE_LOOKBACK_DAYS), end=day)
    rep.n_scored = score_disclosures(f, recent, cfg)
    rep.rows += rep.n_scored

    rep.sweep = should_sweep(f, day, now) if sweep is None else sweep
    since = now - CHANGE_WINDOW
    changed = [
        d
        for d in recent
        if d.first_seen_at >= since or (d.fin_loaded_at is not None and d.fin_loaded_at >= since)
    ]
    oh_corps = {d.corp_code for d in changed if d.kind in OVERHANG_KINDS or d.kind == "periodic"}
    ins_corps = {d.corp_code for d in changed if d.kind in ("insider", "periodic")}
    sch_corps: set[str] | None = {
        d.corp_code for d in changed if d.kind in ("periodic", "pre_announce")
    }
    months = cfg.overhang.lockup_months_third_party
    by_corp: dict[str, list[OverhangEvent]] = defaultdict(list)
    if rep.sweep:
        events = f.overhang_events(start=_years_back(day, OVERHANG_LOOKBACK_YEARS), end=day)
        for ev in events:
            by_corp[ev.corp_code].append(ev)
        oh_corps |= _live_overhang_corps(events, day, months)
        ins_corps |= {
            d.corp_code
            for d in f.disclosures(
                start=day - timedelta(days=INSIDER_SWEEP_DAYS), end=day, kinds={"insider"}
            )
        }
        sch_corps = None

    def one_overhang(corp: str) -> int:
        evs = by_corp.get(corp) if rep.sweep else None
        return derive_overhang(f, corp, day, cfg=cfg, now=now, loaded_by=loaded_by, events=evs)

    rep.n_overhang, n = _each(oh_corps, "overhang", one_overhang, rep.errors)
    rep.rows += n
    rep.n_insider, n = _each(
        ins_corps,
        "insider",
        lambda corp: derive_insider(f, corp, day, now=now, loaded_by=loaded_by),
        rep.errors,
    )
    rep.rows += n
    if sch_corps is None or sch_corps:
        try:
            rep.n_schedule_corps, n = derive_schedule(
                f,
                day,
                corps=sch_corps,
                now=now,
                loaded_by=loaded_by,
                calendar=calendar,
                errors=rep.errors,
            )
            rep.rows += n
        except Exception as e:  # 일정 쓰기 전체 실패 — 다른 파생은 이미 썼다
            rep.errors["schedule"] = _reason(e)
    return rep


def _run_day(ctx: JobContext) -> date:
    """실행 as_of(`YYYY-MM-DDTHH:MM` KST)의 날짜. 형식이 아니면 지금의 KST 날짜."""
    try:
        return date.fromisoformat(ctx.as_of[:10])
    except ValueError:
        return to_kst(ctx.now).date()


def _public_repos(ctx: JobContext) -> DeriveRepos:
    got = ctx.resources.get("public_repos")
    if got is not None:
        return got
    repos = ctx.resources.get("repos")
    if repos is not None and callable(getattr(repos, "public", None)):
        return repos.public()
    return pg_public_repos(ctx.resource("connect"))


def derive(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.engine.filings:derive`)."""
    cfg: FinConfig = ctx.resources.get("fin_config") or load_fin(settings=ctx.settings)
    kr = ctx.resources.get("kr")
    cal = kr if isinstance(kr, TradingCalendar) else TradingCalendar.default()
    rep = run_derive(
        _public_repos(ctx),
        _run_day(ctx),
        now=ctx.now,
        cfg=cfg,
        calendar=cal,
        loaded_by=ctx.job or JOB,
    )
    # 수집 데이터 키가 없는 작업이다 — run-once(수동)가 실행 키를 잡았으면 그대로 돌려준다
    if rep.errors:
        return JobResult("failed", rows=rep.rows, detail=rep.detail())
    return JobResult("ok", collected=ctx.keys, rows=rep.rows, detail=rep.detail())
