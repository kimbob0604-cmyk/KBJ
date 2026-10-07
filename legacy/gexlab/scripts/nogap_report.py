"""무결측 리포트 — '3거래일 연속 무결측' 판정 (docs/phase1_design.md §0 완료 기준·§9).

실행(DATABASE_URL 은 .env·환경변수 — 출력에 싣지 않는다):

    uv run python -m scripts.nogap_report [--end 2026-10-02] [--days 20] [--need 3] [--recompute]

- scheduler(services/scheduler/gaps.py `GapDaily`)가 세션마다 쓴 collection_reports 를 읽는다.
  `--recompute` 면 저장된 데이터로 그 자리에서 다시 판정한다(services/gaps.py — 쓰지 않는다. 창을
  놓쳐 리포트가 없는 세션용, 분봉 적재 결과를 몰라 DB 에 있는 분봉으로 대조한다)
- 거래일 T = T 주간 + T 로 귀속되는 밤(prev_trading_day(T) 에 시작해 열린 밤만 — 금요일 밤은 보통
  월요일, 휴장 전날 밤은 없다). 휴장일은 거래일이 아니라 건너뛴다(세지도 끊지도 않음)
- 세션 판정: 리포트가 없으면 `missing`(판정 전 — 무결측이 아니다), 필수 스트림에 공백이 있으면
  `gaps`, 공백은 없고 판정 불가가 있으면 `unverified`, 모두 ok 면 `clean`. 필수가 아닌 스트림(야간
  투자자별 등)은 판정에 들지 않는다
- T 가 무결측 = T 의 세션이 모두 clean. [end 까지 최근 days 거래일] 안에 연속 need 거래일 무결측
  구간이 있으면 종료 코드 0, 없으면 1. 설정·DB 오류는 2
- 표(마크다운): 거래일 | 세션 | 판정 | 필수 ok/전체 | 공백 | 최대 공백(초) | 비고, 끝에 가장 긴
  연속 구간과 판정
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal, Protocol

from core.calendar import KST, TradingCalendar, night_session_opens
from data.store import CollectionReportRecord
from services.gaps import (
    GapConfig,
    GapReader,
    Session,
    evaluate_session,
    load_inputs,
    session_span,
)
from services.runtime import utcnow

Verdict = Literal["clean", "gaps", "unverified", "missing"]
SESSIONS_KO: dict[str, str] = {"day": "주간", "night": "야간"}
VERDICT_KO: dict[Verdict, str] = {
    "clean": "무결측",
    "gaps": "공백",
    "unverified": "판정 불가",
    "missing": "리포트 없음",
}


class ReportSource(Protocol):
    def gap_reports(self, first: date, last: date) -> list[CollectionReportRecord]: ...


class ReportStore(ReportSource, GapReader, Protocol):
    """리포트 읽기 + 다시 판정할 입력 읽기 (data/store.py `PostgresSink`)."""


def sessions_of(t: date, cal: TradingCalendar) -> list[Session]:
    """거래일 t 에 드는 세션: 주간 + t 로 귀속되는 밤(열렸을 때만)."""
    out: list[Session] = ["day"]
    if night_session_opens(cal.prev_trading_day(t), cal):
        out.append("night")
    return out


def trading_days(end: date, count: int, cal: TradingCalendar) -> list[date]:
    """end 이하 최근 count 거래일(오름차순)."""
    d = end if cal.is_trading_day(end) else cal.prev_trading_day(end)
    out = [d]
    while len(out) < count:
        out.append(cal.prev_trading_day(out[-1]))
    return sorted(out)


@dataclass(frozen=True)
class SessionVerdict:
    trade_date: date
    session: Session
    verdict: Verdict
    required_ok: int = 0
    required: int = 0
    gaps: int = 0
    max_gap_s: float | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DayVerdict:
    trade_date: date
    sessions: tuple[SessionVerdict, ...]

    @property
    def clean(self) -> bool:
        return all(s.verdict == "clean" for s in self.sessions)


@dataclass(frozen=True)
class Judgement:
    days: tuple[DayVerdict, ...]
    need: int
    best: tuple[date, ...] = field(default_factory=tuple[date, ...])  # 가장 긴 연속 무결측 구간

    @property
    def satisfied(self) -> bool:
        return len(self.best) >= self.need


def judge_session(
    trade_date: date, session: Session, reports: Sequence[CollectionReportRecord]
) -> SessionVerdict:
    if not reports:
        return SessionVerdict(trade_date, session, "missing", notes=("리포트 없음 — 판정 전",))
    req = [r for r in reports if r.required]
    gapped = [r for r in req if r.status == "gaps"]
    unknown = [r for r in req if r.status == "unverified"]
    ok = sum(1 for r in req if r.status == "ok")
    verdict: Verdict = "gaps" if gapped else "unverified" if unknown else "clean"
    notes = [f"{r.stream} 공백 {r.gaps}" for r in gapped]
    notes += [f"{r.stream}: {r.detail.get('reason', '판정 불가')}" for r in unknown]
    worst = max((r.max_gap_s for r in gapped if r.max_gap_s is not None), default=None)
    return SessionVerdict(
        trade_date,
        session,
        verdict,
        required_ok=ok,
        required=len(req),
        gaps=sum(r.gaps for r in gapped),
        max_gap_s=worst,
        notes=tuple(notes),
    )


def judge(
    days: Sequence[date],
    reports: Sequence[CollectionReportRecord],
    cal: TradingCalendar,
    need: int = 3,
) -> Judgement:
    """거래일마다 세션 판정 → 가장 긴 연속 무결측 구간. days 는 거래일만(오름차순)."""
    by: dict[tuple[date, str], list[CollectionReportRecord]] = {}
    for r in reports:
        by.setdefault((r.trade_date, r.session), []).append(r)
    verdicts = tuple(
        DayVerdict(t, tuple(judge_session(t, s, by.get((t, s), [])) for s in sessions_of(t, cal)))
        for t in days
    )
    best: list[date] = []
    run: list[date] = []
    for v in verdicts:
        run = [*run, v.trade_date] if v.clean else []
        if len(run) > len(best):
            best = run
    return Judgement(verdicts, need, tuple(best))


def render(j: Judgement) -> str:
    lines = [
        "| 거래일 | 세션 | 판정 | 필수 ok/전체 | 공백 | 최대 공백(초) | 비고 |",
        "|---|---|---|---|---|---|---|",
    ]
    for day in j.days:
        for s in day.sessions:
            worst = "-" if s.max_gap_s is None else f"{s.max_gap_s:g}"
            ratio = f"{s.required_ok}/{s.required}" if s.verdict != "missing" else "-"
            note = "; ".join(s.notes[:4]) + (" …" if len(s.notes) > 4 else "")
            lines.append(
                f"| {s.trade_date} | {SESSIONS_KO[s.session]} | {VERDICT_KO[s.verdict]} | "
                f"{ratio} | {s.gaps} | {worst} | {note} |"
            )
    lines.append("")
    if j.best:
        span = f"{j.best[0]} ~ {j.best[-1]}"
        lines.append(f"가장 긴 연속 무결측: {len(j.best)}거래일 ({span}, 휴장일은 건너뜀)")
    else:
        lines.append("연속 무결측 거래일 없음")
    verdict = "충족" if j.satisfied else "미충족"
    lines.append(f"완료 기준 '{j.need}거래일 연속 무결측(주간 + 귀속 야간)': {verdict}")
    return "\n".join(lines)


class RecomputeSource:
    """저장된 데이터로 그 자리에서 다시 판정한다(쓰지 않는다) — 리포트가 없는 세션용."""

    def __init__(
        self, reader: GapReader, cal: TradingCalendar, config: GapConfig | None = None
    ) -> None:
        self._reader = reader
        self._cal = cal
        self._cfg = config
        self.now = utcnow()

    def gap_reports(self, first: date, last: date) -> list[CollectionReportRecord]:
        out: list[CollectionReportRecord] = []
        d = first
        while d <= last:
            for s in ("day", "night"):
                span = session_span(d, s, self._cal)
                if span is None:
                    continue
                inputs = load_inputs(self._reader, d, s, span)
                rep = evaluate_session(inputs, self._cal, d, s, self._cfg)
                out += rep.report_records(self.now)
            d += timedelta(days=1)
        return out


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="scripts.nogap_report", description="3거래일 연속 무결측 판정 (설계 §9)"
    )
    p.add_argument("--end", type=date.fromisoformat, help="마지막 거래일(기본: 오늘 KST)")
    p.add_argument("--days", type=int, default=20, help="살펴볼 최근 거래일 수(기본 20)")
    p.add_argument("--need", type=int, default=3, help="필요한 연속 무결측 거래일(기본 3)")
    p.add_argument("--recompute", action="store_true", help="저장된 데이터로 다시 판정(쓰지 않음)")
    args = p.parse_args(argv)
    if args.days < 1 or args.need < 1:
        p.error("--days·--need 는 1 이상")
    return args


def main(
    argv: Sequence[str] | None = None,
    *,
    store: ReportStore | None = None,
    cal: TradingCalendar | None = None,
    now: datetime | None = None,
) -> int:
    """종료 코드: 0 충족, 1 미충족, 2 설정·DB 오류. store 는 시험이 넣는다(기본 DATABASE_URL).
    캘린더(config/holidays_override.yaml)를 못 만들거나 거래일을 못 찾는 것도 설정 오류(2) —
    미충족(1)과 섞이지 않게 try 안에서 만든다."""
    args = parse_args(argv)
    owned = None
    try:
        cal = cal or TradingCalendar.default()
        end = args.end or (now or utcnow()).astimezone(KST).date()
        days = trading_days(end, args.days, cal)
        if store is None:
            from data.store import PostgresSink

            owned = store = PostgresSink.from_settings(service="nogap_report")
        source: ReportSource = RecomputeSource(store, cal) if args.recompute else store
        reports = source.gap_reports(days[0], days[-1])
    except Exception as e:  # 설정(캘린더)·DB 오류 — 문구는 저장소가 가린 첫 줄만
        text = str(e).splitlines()[0][:200] if str(e) else ""
        print(f"무결측 판정을 못 했다(설정·DB 오류): {type(e).__name__}: {text}", file=sys.stderr)
        return 2
    finally:
        if owned is not None:
            owned.close()
    j = judge(days, reports, cal, args.need)
    print(render(j))
    return 0 if j.satisfied else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
