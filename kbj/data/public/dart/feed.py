"""DART 공시 증분 피드 — `list.json` 을 1쪽부터 읽다 이미 본 접수번호가 나온 쪽에서 멈춘다.

승격 원본: SD `server.py:poll_dart_disclosures`:10166(매분 1쪽부터 최대 10쪽 — 매번 처음부터 다시),
ET `board/ingest/dart.py:disclosures`:123(한 쪽만). docs/p4_design.md §1.3·§4.1, ADR 0013 §2.1.

- 정렬: `list.json` 의 기본 정렬을 **접수 내림차순**으로 가정한다 [확인 필요 #28]. 그래서 새 공시는
  앞 쪽에 오고, 한 쪽에 이미 본 접수번호가 하나라도 있으면 그 뒤 쪽은 다 본 것이다. 실측에서 정렬이
  다르면 `full=True`(모든 쪽 + `seen` 거름)로 바꾸고 호출량(§3.5)을 다시 잰다.
- 쪽을 넘기는 사이 새 공시가 들어오면 앞 쪽의 행이 뒤 쪽으로 밀린다 — 같은 행이 두 번 보일 수 있어서
  한 번의 훑기 안에서 접수번호로 겹침을 지운다(빠지는 행은 없다 — 밀릴 뿐).
- `max_pages` 를 넘으면 `complete=False`. 다음 실행의 증분은 1쪽에서 이미 본 접수번호를 만나
  멈추므로 끊긴 뒤쪽을 이어 읽지 못한다 — 처리기(`collectors.dart_feed`)가 같은 실행에서 `full=True`
  로 그날 목록 전체를 이어 읽고 경고로 남긴다(조용히 자르지 않는다).
- 정정 공시: 제목 머리 `[기재정정]`·`[첨부정정]`·`[첨부추가]`·`[변경등록]`(config/fin.yaml
  `filings.correction_prefixes` [확인 필요 #28]). `base_title` 은 머리의 대괄호 묶음을 모두 지운
  제목 (원 공시와 짝짓기).

접수 **시각**은 응답에 없다(`rcept_dt` 날짜뿐) — 처리기가 `first_seen_at`(처음 본 시각)을 붙인다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final

from kbj.data.public.dart.disclosures import DartJson, Disclosure, list_disclosures

__all__ = [
    "CORRECTION_PREFIXES",
    "PAGE_COUNT",
    "FeedScan",
    "base_title",
    "is_correction",
    "scan_new",
]

PAGE_COUNT: Final = 100  # list.json page_count 최댓값(쪽당 100건)
# 정정 머리 기본값 — 정본은 config/fin.yaml filings.correction_prefixes(처리기가 넘긴다)
CORRECTION_PREFIXES: Final[tuple[str, ...]] = (
    "[기재정정]",
    "[첨부정정]",
    "[첨부추가]",
    "[변경등록]",
)
_HEAD_BRACKETS = re.compile(r"^\s*(?:\[[^\]]*\]\s*)+")

Seen = Callable[[Iterable[str]], set[str]]


def is_correction(title: str | None, prefixes: Sequence[str] = CORRECTION_PREFIXES) -> bool:
    """제목 머리가 정정 표기인가(머리의 대괄호 묶음 중 하나라도 `prefixes` 에 있으면)."""
    head = _HEAD_BRACKETS.match(title or "")
    if head is None:
        return False
    groups = re.findall(r"\[[^\]]*\]", head.group(0))
    want = {p.replace(" ", "") for p in prefixes}
    return any(g.replace(" ", "") in want for g in groups)


def base_title(title: str | None) -> str:
    """머리의 대괄호 묶음(`[기재정정]` 등)을 모두 지운 제목. 남는 것이 없으면 원래 제목."""
    t = (title or "").strip()
    stripped = _HEAD_BRACKETS.sub("", t).strip()
    return stripped or t


@dataclass(frozen=True)
class FeedScan:
    """한 번의 훑기 결과.

    `items` = 이번에 처음 본 공시(받은 순서 — 접수 내림차순 가정), `pages` = 부른 쪽 수,
    `stopped_at` = 이미 본 접수번호가 나와 멈춘 쪽(없으면 None), `complete` = 끝까지(또는 멈춤
    쪽까지) 읽었는가 — `max_pages` 에 걸려 끊겼으면 False. `total_count` = 마지막으로 받은 쪽이 알려
    준 전체 건수.
    """

    items: tuple[Disclosure, ...]
    pages: int
    stopped_at: int | None
    complete: bool
    total_count: int
    seen_count: int = 0
    duplicates: int = 0
    day: date | None = None
    end: date | None = None

    @property
    def new(self) -> int:
        return len(self.items)


def scan_new(
    client: DartJson,
    day: date,
    seen: Seen,
    *,
    max_pages: int = 30,
    end: date | None = None,
    full: bool = False,
    corp_cls: str | None = None,
) -> FeedScan:
    """`day`(~`end`) 공시 목록에서 처음 보는 공시만.

    `seen(접수번호들) -> 이미 저장된 접수번호` 는 저장소(`FilingsRepo.seen`)다. `full=True` 는
    멈추지 않고 모든 쪽을 읽는다(07:00 전 영업일 다시 훑기 — 19:59 뒤 접수분과 중간에 놓친 행을
    채운다). 목록 조회 실패(`DartError`·예산)는 그대로 올린다 — 처리기가 그 실행을 실패로 기록한다.
    """
    if max_pages < 1:
        raise ValueError("max_pages 는 1 이상")
    if end is not None and end < day:
        raise ValueError(f"end({end}) 가 day({day}) 보다 앞이다")
    bgn = day.isoformat()
    fin = (end or day).isoformat()
    got: dict[str, Disclosure] = {}
    pages = 0
    stopped_at: int | None = None
    complete = False
    total_count = 0
    seen_count = 0
    duplicates = 0
    for page_no in range(1, max_pages + 1):
        page = list_disclosures(client, bgn, fin, page_no, PAGE_COUNT, corp_cls=corp_cls)
        pages += 1
        total_count = page.total_count
        ids = [d.rcept_no for d in page.items if d.rcept_no]
        old = seen(ids) if ids else set()
        seen_count += len(old)
        for d in page.items:
            if not d.rcept_no or d.rcept_no in old:
                continue
            if d.rcept_no in got:
                duplicates += 1  # 쪽을 넘기는 사이 밀려 내려온 행
                continue
            got[d.rcept_no] = d
        if old and not full:
            stopped_at = page_no
            complete = True
            break
        if not page.items or page_no >= page.total_page:
            complete = True
            break
    items = tuple(got.values())
    return FeedScan(
        items=items,
        pages=pages,
        stopped_at=stopped_at,
        complete=complete,
        total_count=total_count,
        seen_count=seen_count,
        duplicates=duplicates,
        day=day,
        end=end or day,
    )
