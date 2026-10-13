"""부문 매출 — 사업보고서 원문 표 best-effort(`pub_fin.segment`, quality=estimated).

docs/p4_design.md §1.5·D-P4-22. 승격 원본: SD `server.py:_try_dart_segment_revenue`:10454(표 고르기
— 표 바로 앞 제목에 '매출실적'·'부문별' 등이 있는 첫 표, 행마다 [이름, 숫자들]).

SD 에서 바꾼 것
- 원문 받기·zip 풀기는 호출자(작업)가 한다 — 이 함수는 HTML 하나만 받는다(엔진 순수성 ⑨).
- 단위: 제목·표 안의 '(단위: 백만원)' 류를 dart-report 와 같은 규칙(`costs._unit_scale`)으로 원으로
  바꾼다. 단위 표기가 없으면 원으로 본다(행은 언제나 quality=estimated).
- 열 이름: 첫 행(숫자 없는 머리행)의 칸 이름을 금액 키로(`{'당기': 원, '전기': 원}`). 머리행이
  없으면 `c1`·`c2`…
- 합계 행('합계'·'계'·'총계')은 부문이 아니라 뺀다. 숫자가 하나도 없는 행도 뺀다.
- 실패(표 없음)는 빈 목록 — 화면은 '표 없음'(SD 처럼 예외를 삼켜 None 으로 돌리지 않는다: 파싱
  예외는 그대로 올라간다).
"""

from __future__ import annotations

import re
from typing import Any, Final

from bs4 import BeautifulSoup

from kbj.core.fin_rows import SegmentRow
from kbj.core.quality import Quality
from kbj.engines.financials.costs import _norm, _num, _unit_scale

__all__ = ["SEGMENT_KEYWORDS", "TOTAL_LABELS", "parse_segments"]

# SD _try_dart_segment_revenue 의 KW 그대로
SEGMENT_KEYWORDS: Final = (
    "매출실적",
    "매출현황",
    "부문별",
    "사업부문별",
    "제품별 매출",
    "제품별매출",
)
TOTAL_LABELS: Final = frozenset({"합계", "계", "총계", "소계"})
_HEAD_TAGS: Final = ["p", "div", "h3", "h4", "span", "title"]


def _cells(tr: Any) -> list[str]:
    return [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]


def parse_segments(
    html: str, *, corp_code: str, bsns_year: int, rcept_no: str | None
) -> list[SegmentRow]:
    """원문 HTML → 부문 행(첫 매칭 표만 — SD 와 같다). 없으면 빈 목록."""
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        prev = table.find_previous(_HEAD_TAGS)
        head_txt = prev.get_text(strip=True) if prev else ""
        if not any(k in head_txt for k in SEGMENT_KEYWORDS):
            continue
        inner = _norm(table.get_text(" ", strip=True))
        ctx = _norm(head_txt)
        scale = _unit_scale(inner) if "단위:" in inner else _unit_scale(ctx)
        trs = table.find_all("tr")
        header: list[str] | None = None
        out: list[SegmentRow] = []
        seen: set[str] = set()
        for tr in trs:
            cells = _cells(tr)
            if len(cells) < 2:
                continue
            nums = [_num(c) for c in cells[1:]]
            if header is None and all(n is None for n in nums):
                header = [re.sub(r"\s+", "", c) or f"c{i}" for i, c in enumerate(cells[1:], 1)]
                continue
            name = cells[0].strip()
            if not name or _norm(name) in TOTAL_LABELS or all(n is None for n in nums):
                continue
            if name in seen:
                continue  # 같은 이름이 두 번이면 첫 행(SegmentRow 키 — 회사·연도·부문)
            seen.add(name)
            keys = (
                header
                if header is not None and len(header) == len(nums)
                else [f"c{i}" for i in range(1, len(nums) + 1)]
            )
            amounts = {
                k: (None if n is None else round(n * scale))
                for k, n in zip(keys, nums, strict=True)
            }
            out.append(
                SegmentRow(
                    corp_code=corp_code,
                    bsns_year=bsns_year,
                    segment=name,
                    amounts=amounts,
                    rcept_no=rcept_no,
                    quality=Quality.ESTIMATED,
                )
            )
        if out:
            return out
    return []
