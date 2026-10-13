"""회계 기간 — 보고서 코드·결산월 → 분기 기간, 정기보고서 제목 → (사업연도, 보고서), PIT TTM 창.

docs/p4_design.md §1.5·§4.5, docs/metrics.md §9 '회계 기간'·'TTM'.

- `bsns_year` 는 **회계연도가 시작하는 해**로 본다(비12월 결산 — 3월 결산 2026 사업연도 = 2026-04 ~
  2027-03) [확인 필요 #34]. 합성 세계(`p4_world.fiscal_quarter`)도 같은 가정이다. 실측으로 다르면 이
  파일 한 곳만 고친다.
- 결산월을 모르면 12월로 보되 **추정**이다 — 호출자가 quality=estimated + 사유를
  단다(`acc_mt_known`).
- PIT(D-P4-11): `ttm_window(quarters, t)` 는 t 에 공시돼 있던(정기보고서 접수일 ≤ t) 분기만으로
  마지막 연속 4분기를 고른다 — 미래 분기를 지우거나 바꿔도 t 이전의 답이 바뀌지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Final

from kbj.core.fin_rows import FiscalPeriod, QuarterRow, quarter_of_reprt, reprt_of_quarter

__all__ = [
    "REPORT_KIND_OF_Q",
    "fiscal_period",
    "fiscal_quarter",
    "report_of_title",
    "ttm_window",
]

# 분기 → 정기보고서 이름(분기·반기·사업)
REPORT_KIND_OF_Q: Final = {1: "분기보고서", 2: "반기보고서", 3: "분기보고서", 4: "사업보고서"}
_TITLE_PERIOD = re.compile(r"\((\d{4})\.(\d{2})\)")


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def fiscal_quarter(year: int, q: int, acc_mt: int | None) -> FiscalPeriod:
    """회계연도 `year`(시작하는 해) 의 q 분기 기간. 결산월 None 은 12월로 본다(추정 — 호출자)."""
    if not 1 <= q <= 4:
        raise ValueError(f"분기는 1~4: {q!r}")
    mt = 12 if acc_mt is None else acc_mt
    if not 1 <= mt <= 12:
        raise ValueError(f"결산월은 1~12: {acc_mt!r}")
    fy_start = date(year, mt % 12 + 1, 1)
    start = _add_months(fy_start, 3 * (q - 1))
    end = _add_months(start, 3) - timedelta(days=1)
    return FiscalPeriod(year, q, start, end)


def fiscal_period(bsns_year: int, reprt_code: str, acc_mt: int | None) -> FiscalPeriod:
    """DART (사업연도, 보고서 코드, 결산월) → 그 보고서가 끝나는 분기의 기간(3개월)."""
    return fiscal_quarter(bsns_year, quarter_of_reprt(reprt_code), acc_mt)


def report_of_title(title: str, acc_mt: int | None) -> tuple[int, str] | None:
    """정기보고서 제목('분기보고서 (2026.09)'·'[기재정정]사업보고서 (2027.03)') → (사업연도, 보고서
    코드).

    제목의 (연.월)은 보고 기간의 끝 달이다. 보고서 이름(분기·반기·사업)과 결산월로 계산한 분기가
    어긋나면 None(모르는 것을 짐작하지 않는다 — 호출자가 사유를 남긴다).
    """
    m = _TITLE_PERIOD.search(title)
    if m is None:
        return None
    end_year, end_month = int(m.group(1)), int(m.group(2))
    if not 1 <= end_month <= 12:
        return None
    mt = 12 if acc_mt is None else acc_mt
    start_month = mt % 12 + 1
    q = ((end_month - start_month) % 12) // 3 + 1
    if (end_month - start_month) % 3 != 2:
        return None  # 분기 끝 달이 아니다
    kind = REPORT_KIND_OF_Q[q]
    if kind not in title:
        return None
    year = end_year if end_month >= start_month else end_year - 1
    return year, reprt_of_quarter(q)


def _seq(year: int, q: int) -> int:
    return year * 4 + (q - 1)


def ttm_window(quarters: Sequence[QuarterRow], t: date) -> list[QuarterRow] | None:
    """t 에 공시돼 있던 분기 중 마지막 연속 4분기(같은 fs_div) — 오래된 것부터. 없으면 None.

    fs_div 는 t 까지 공시된 마지막 분기의 것(같은 분기에 둘이면 CFS)을 따르고, 넷 다 그 기준이어야
    한다(§4.5 'TTM — 같은 기준 4개 연속'). 값 칸(None)은 보지 않는다 — 합계는 호출자가(하나라도
    None 이면 None).
    """
    known = [r for r in quarters if r.known_by(t)]
    if not known:
        return None
    last = max(known, key=lambda r: (_seq(r.fiscal_year, r.fiscal_q), r.fs_div == "CFS"))
    by_key = {_seq(r.fiscal_year, r.fiscal_q): r for r in known if r.fs_div == last.fs_div}
    end = _seq(last.fiscal_year, last.fiscal_q)
    window = [by_key.get(s) for s in range(end - 3, end + 1)]
    if any(r is None for r in window):
        return None
    return [r for r in window if r is not None]
