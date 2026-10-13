"""실적 일정 — 결산실적공시예고 예정일(ok)·법정 제출기한(estimated).

docs/p4_design.md §4.2, docs/metrics.md §14.

SD `server.py:_fetch_kr_earnings`:11047(정기보고서 목록에서 날짜를 짐작)을 대신한다.

정의
- `pre_announce`: 결산실적공시예고 본문의 공시 예정일(quality ok). 대상 기간 = 예정일 **전에 끝난
  마지막 회계 분기**(결산월 기준 — 1월 말 예고는 12월 결산의 4분기·연간).
- `statutory`: 법정 제출기한 — 분기·반기 = 분기말 + 45일, 사업 = 결산일 + 90일 [확인 필요: 법정
  기한], 휴장일이면 다음 거래일 [확인 필요: 휴일 처리 — 달력을 넘길 때만]. 언제나 estimated.
  대상 = 오늘 앞뒤 회계 분기(끝난 지 `lookback_days` 안 · `horizon_days` 안에 끝날 것).
- 이미 접수된 정기보고서가 있는 기간의 행은 만들지 않는다(두 종류 모두 — 지운다).
- 반기·사업보고서만 내는 회사(분기보고서 이력이 없고 반기보고서가 있다, 또는 코넥스 [확인 필요])는
  1·3분기 법정 기한을 만들지 않는다.
- 결산월을 모르면 12월로 보고 estimated + 사유.

회계 분기 계산은 재무 엔진의 정본 `kbj.engines.financials.period.fiscal_quarter` 하나를 쓴다(두 벌
금지 — `bsns_year` 는 회계연도가 시작하는 해 [확인 필요 #34]).
"""

from __future__ import annotations

import calendar as _cal
import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

from kbj.core.calendar import TradingCalendar
from kbj.core.fin_rows import FiscalPeriod, ScheduleRow
from kbj.core.quality import Quality
from kbj.engines.financials.period import fiscal_quarter

__all__ = [
    "DEADLINE_DAYS",
    "PreAnnounceItem",
    "filed_period_ends",
    "from_pre_announce",
    "is_semiannual",
    "schedule_rows",
    "statutory_deadlines",
]

# 법정 제출기한(분기말·결산일 + 일수) [확인 필요: 자본시장법 시행령 — 사업 90일·반기/분기 45일]
DEADLINE_DAYS: Final = {1: 45, 2: 45, 3: 45, 4: 90}
LOOKBACK_DAYS: Final = 120  # 끝난 지 이만큼 안의 분기까지 본다(사업보고서 90일 + 여유)
HORIZON_DAYS: Final = 100  # 이만큼 안에 끝날 분기까지 본다(다음 분기 하나)
_TITLE_PERIOD = re.compile(r"\((\d{4})\.(\d{1,2})\)")


@dataclass(frozen=True)
class PreAnnounceItem:
    """해석된 결산실적공시예고 한 건(공시 행의 `parse_note` 를 서비스가 풀어 넘긴다)."""

    rcept_no: str
    rcept_dt: date
    planned_date: date
    target: str | None = None


def _month_end(y: int, m: int) -> date:
    return date(y, m, _cal.monthrange(y, m)[1])


def filed_period_ends(titles: Iterable[str]) -> set[date]:
    """정기보고서 제목('분기보고서 (2026.09)' — 정정 머리 포함)의 보고 기간 끝 달 → 그 달
    말일 집합."""
    out: set[date] = set()
    for t in titles:
        m = _TITLE_PERIOD.search(t or "")
        if m is None:
            continue
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            out.add(_month_end(y, mo))
    return out


def is_semiannual(periodic_titles: Iterable[str], corp_cls: str | None) -> bool:
    """1·3분기 보고서를 내지 않는 회사인가 — 코넥스(N) [확인 필요] 또는 이력에 분기보고서 없이
    반기보고서만 있다."""
    if corp_cls == "N":
        return True
    titles = [t.replace(" ", "") for t in periodic_titles]
    return (
        bool(titles)
        and not any("분기보고서" in t for t in titles)
        and any("반기보고서" in t for t in titles)
    )


def _quarters_around(acc_mt: int, lo: date, hi: date) -> list[FiscalPeriod]:
    out: list[FiscalPeriod] = []
    for y in range(lo.year - 2, hi.year + 2):
        for q in range(1, 5):
            p = fiscal_quarter(y, q, acc_mt)
            if lo <= p.end <= hi:
                out.append(p)
    return sorted(out, key=lambda p: p.end)


def _roll(d: date, cal: TradingCalendar | None) -> date:
    if cal is None or cal.is_trading_day(d):
        return d
    return cal.next_trading_day(d)


def statutory_deadlines(
    corp_code: str,
    acc_mt: int | None,
    today: date,
    *,
    filed: Collection[date] = (),
    semiannual: bool = False,
    calendar: TradingCalendar | None = None,
    lookback_days: int = LOOKBACK_DAYS,
    horizon_days: int = HORIZON_DAYS,
) -> list[ScheduleRow]:
    """오늘 앞뒤 회계 분기의 법정 제출기한(estimated) — 이미 낸 기간(`filed` — 분기말)은 뺀다."""
    mt = 12 if acc_mt is None else acc_mt
    lo = today - timedelta(days=lookback_days)
    hi = today + timedelta(days=horizon_days)
    filed_set = set(filed)
    rows: list[ScheduleRow] = []
    for p in _quarters_around(mt, lo, hi):
        if semiannual and p.q in (1, 3):
            continue
        if p.end in filed_set:
            continue
        days = DEADLINE_DAYS[p.q]
        deadline = _roll(p.end + timedelta(days=days), calendar)
        what = "결산일" if p.q == 4 else "분기말"
        notes = [f"법정 제출기한({what} + {days}일) — 추정"]
        if acc_mt is None:
            notes.append("결산월 모름 — 12월로 가정")
        if deadline < today:
            notes.append("기한 지남 — 정기보고서 미제출 [추정]")
        rows.append(
            ScheduleRow(
                corp_code=corp_code,
                period_end=p.end,
                kind="statutory",
                planned_date=deadline,
                fiscal_year=p.year,
                fiscal_q=p.q,
                note=" · ".join(notes),
                quality=Quality.ESTIMATED,
            )
        )
    return rows


def from_pre_announce(
    corp_code: str,
    acc_mt: int | None,
    item: PreAnnounceItem,
    *,
    filed: Collection[date] = (),
) -> ScheduleRow | None:
    """결산실적공시예고 → 예정일 행(대상 기간 = 예정일 전에 끝난 마지막 회계 분기). 그 기간의
    정기보고서를 이미 냈으면 None."""
    mt = 12 if acc_mt is None else acc_mt
    lo = item.planned_date - timedelta(days=200)
    cands = [p for p in _quarters_around(mt, lo, item.planned_date) if p.end < item.planned_date]
    if not cands:
        return None
    p = cands[-1]
    if p.end in set(filed):
        return None
    note = item.target
    if acc_mt is None:
        note = ((note + " · ") if note else "") + "결산월 모름 — 12월로 가정(대상 기간 추정)"
    return ScheduleRow(
        corp_code=corp_code,
        period_end=p.end,
        kind="pre_announce",
        planned_date=item.planned_date,
        fiscal_year=p.year,
        fiscal_q=p.q,
        rcept_no=item.rcept_no,
        note=note,
        quality=Quality.ESTIMATED if acc_mt is None else Quality.OK,
    )


def schedule_rows(
    corp_code: str,
    acc_mt: int | None,
    today: date,
    *,
    periodic_titles: Sequence[str] = (),
    pre_announces: Sequence[PreAnnounceItem] = (),
    corp_cls: str | None = None,
    calendar: TradingCalendar | None = None,
) -> list[ScheduleRow]:
    """회사 하나의 일정 행 전부(법정 기한 + 예고) — 키 (period_end, kind) 마다 하나, 날짜 순.

    같은 기간의 예고가 여럿(정정·날짜 변경)이면 늦은 접수번호.
    """
    filed = filed_period_ends(periodic_titles)
    rows = statutory_deadlines(
        corp_code,
        acc_mt,
        today,
        filed=filed,
        semiannual=is_semiannual(periodic_titles, corp_cls),
        calendar=calendar,
    )
    pre: dict[date, ScheduleRow] = {}
    for item in sorted(pre_announces, key=lambda i: i.rcept_no):
        row = from_pre_announce(corp_code, acc_mt, item, filed=filed)
        if row is not None:
            pre[row.period_end] = row
    return sorted([*rows, *pre.values()], key=lambda r: (r.planned_date, r.period_end, r.kind))
