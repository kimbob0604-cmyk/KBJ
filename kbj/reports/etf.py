"""작업 `etf.report`(08:20 거래일 — docs/p5_design.md §1.4·§4.4, D-P5-13 ④, notify `etf.report`).

`prv_etf.change_log`·`holding`·`fund`(운용사 PDF — `etf.collect` 08:00)와
`prv_etf.etf_daily`·`meta`·
`split_event`(KRX — `krx.daily` 08:05) → `kbj.engines.etf.report.assemble`·`build`(ET 섹션 그대로) →
`notify(kind='etf.report', subject=<기준일>)` 한 통.

- 기준일 = 실행일의 **직전 완료 영업일** 이하 마지막 ETF 일별 날짜(ET `market.py` 머리말 — 장중 값을
  그날 수치로 쓰지 않는다). 전 영업일 자료가 아직 없으면(krx.daily 늦음) 있는 마지막 날로 쓰고
  머리에
  그 사실을 적는다.
- 그날 구성종목 변동(change_log run_date = 실행일)이 0 이어도 보낸다 — 머리 줄 '감지된 변동
  없음'(조용히
  끝내지 않는다 — monitor/flow '대상 없는 날' 원칙).
- 본문은 ET `_pack` 이 3,900자 섹션 경계로 묶은 메시지들을 이어 붙인 한 통이다(텔레그램 4,096자
  나눔은
  notifier 가 HTML 태그를 지키며 한다 — 대기열 한 건 = '한 통', dedup daily + subject = 기준일).
- 알림을 넣지 못하면(대기열 거절) 실패로 끝낸다(에러를 삼키지 않는다).
- 그날 `etf.collect` 가 일부 운용사를 통째로 못 받고 `ok` 로 끝났으면(실행 기록 detail
  `failed_issuers`) 머리 줄에 빠진 운용사를 적는다 — 받은 운용사로 보내되 '전체'인 척하지 않는다
  (ADR 0010 갱신 2026-10-27). 실행 기록은 자원 `runs`(스케줄러가 넣는다 — 없으면 적지 않는다).

자원: `repos`(없으면 `connect` 로 `pg_repos`), `notify`(필수), `kr`(없으면
`TradingCalendar.default()`), `runs`(선택 — 실행 기록).
시각은 `ctx.now`·`ctx.as_of` 만 쓴다(시계 주입).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from datetime import date
from typing import Any, Final, Protocol, cast

from kbj.core.calendar import TradingCalendar
from kbj.core.time import to_kst
from kbj.engines.etf.holdings import fund_pairs
from kbj.engines.etf.report import ReportInput, assemble, build
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos import pg_repos
from kbj.store.repos.etf import EtfRepo

__all__ = ["COLLECT_JOB", "JOB", "KIND", "WINDOW", "failed_issuers", "make_input", "report", "run"]

JOB: Final = "etf.report"
KIND: Final = "etf.report"
COLLECT_JOB: Final = "etf.collect"
# 1년 기간 수익률(ET PERIODS '1년' 365일)과 20일 거래량 창을 덮는 거래일 수.
WINDOW: Final = 260


class EtfReportRepos(Protocol):
    @property
    def etf(self) -> EtfRepo: ...


class _RunRec(Protocol):
    @property
    def status(self) -> str: ...

    @property
    def detail(self) -> Mapping[str, Any]: ...


class _Runs(Protocol):
    def latest(self, job: str, as_of: str) -> _RunRec | None: ...


def _repos(ctx: JobContext) -> EtfReportRepos:
    repos = ctx.resources.get("repos")
    if repos is not None:
        return cast(EtfReportRepos, repos)
    return pg_repos(ctx.resource("connect"))


def _run_day(ctx: JobContext) -> date:
    try:
        return date.fromisoformat(ctx.as_of[:10])
    except ValueError:
        return to_kst(ctx.now).date()


def _calendar(ctx: JobContext) -> TradingCalendar:
    kr = ctx.resources.get("kr")
    return kr if isinstance(kr, TradingCalendar) else TradingCalendar.default()


def failed_issuers(runs: _Runs | None, run_day: date) -> tuple[str, ...]:
    """그날 `etf.collect` 가 `ok` 로 끝났지만 통째로 못 받은 운용사 이름(실행 기록 detail
    `failed_issuers` — 키 → 이름). 기록·자원이 없으면 빈 튜플."""
    if runs is None:
        return ()
    rec = runs.latest(COLLECT_JOB, run_day.isoformat())
    if rec is None or rec.status != "ok":
        return ()
    got = rec.detail.get("failed_issuers")
    if not isinstance(got, Mapping):
        return ()
    return tuple(str(v or k) for k, v in sorted(cast(Mapping[str, Any], got).items()))


def make_input(
    repo: EtfRepo, run_day: date, cal: TradingCalendar, *, failed: Sequence[str] = ()
) -> ReportInput | None:
    """저장소 → 리포트 입력. ETF 일별이 하나도 없으면 None(보낼 것이 없다 — 실패로 남긴다).
    `failed` = 그날 구성종목을 못 받은 운용사 이름(머리 줄에 적는다)."""
    want = cal.prev_trading_day(run_day)
    days = repo.etf_days(want, WINDOW)
    dates = sorted({r.date for rows in days.values() for r in rows})
    if not dates:
        return None
    base = dates[-1]
    notes: list[str] = []
    if base != want:
        notes.append(
            f"KRX ETF 일별 {base.isoformat()} 기준(전 영업일 {want.isoformat()} 아직 없음)"
        )
    if failed:
        notes.append(
            f"⚠ 구성종목 수집 실패 {len(failed)}곳: {'·'.join(failed)} — 그 운용사 변동은 빠졌다"
        )
    changes = repo.changes(run_day)
    snaps: dict[tuple[str, date], Collection[str]] = {}
    for c in changes:
        for key in ((c.fund_id, c.prev_asof), (c.fund_id, c.asof)):
            if key not in snaps:
                snaps[key] = frozenset(repo.holdings(*key))
    return ReportInput(
        base=base,
        days=days,
        meta=repo.meta(),
        run_date=run_day,
        changes=changes,
        funds=repo.funds(),
        snaps=snaps,
        pair_count=len(fund_pairs(repo.holding_dates())),
        splits=repo.split_events(),
        notes=tuple(notes),
    )


def run(
    repo: EtfRepo,
    run_day: date,
    *,
    cal: TradingCalendar,
    notify: Any,
    source: str = JOB,
    failed: Sequence[str] = (),
) -> dict[str, Any]:
    """리포트를 만들어 알림 대기열에 넣는다. 반환 = 작업 기록 detail(값·키 없이 수만)."""
    if notify is None:
        raise RuntimeError("자원 notify 가 주입되지 않았다 — ETF 리포트를 넣지 못했다")
    inp = make_input(repo, run_day, cal, failed=failed)
    if inp is None:
        raise RuntimeError("ETF 일별이 없다 — krx.daily(08:05)·etf.collect(08:00) 확인")
    msgs = build(assemble(inp))
    text = "\n\n".join(msgs)
    ticket = notify(text, kind=KIND, source=source, as_of=run_day, subject=inp.base.isoformat())
    if not getattr(ticket, "ok", False):
        raise RuntimeError(f"알림을 넣지 못했다: {getattr(ticket, 'reason', '사유 없음')}")
    return {
        "base": inp.base.isoformat(),
        "changes": len(inp.changes),
        "messages": len(msgs),
        "chars": len(text),
        "duplicate": bool(getattr(ticket, "duplicate", False)),
        "failed_issuers": len(failed),
    }


def report(ctx: JobContext) -> JobResult:
    """`etf.report`(08:20 거래일) 처리기."""
    day = _run_day(ctx)
    detail = run(
        _repos(ctx).etf,
        day,
        cal=_calendar(ctx),
        notify=ctx.resources.get("notify"),
        source=ctx.job or JOB,
        failed=failed_issuers(ctx.resources.get("runs"), day),
    )
    return JobResult("ok", collected=tuple(ctx.keys), rows=detail["messages"], detail=detail)
