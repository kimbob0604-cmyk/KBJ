"""DART 응답·원문의 표기 해석 공용 — 숫자·날짜·HTML 표(묶음 F, docs/p4_design.md §1.3).

구조화 응답(주요사항보고서·지분공시·주식총수)과 공시 원문(`document.xml`)은 값을 **문자열**로 준다.
표기는 실측 전 추정이다 [확인 필요 #30~#33] — 금액은 쉼표(`10,000,000,000`), 날짜는 `2026년 03월
15일`·`2026-03-15`·`2026.03.15`·`20260315`, 빈 값은 `-`. 표기가 바뀌면 이 모듈 한 곳을 고친다.

규칙
- 빈 값(`''`·`-`·`—`·`해당사항없음`)은 None — 0 으로 채우지 않는다(절대 규칙 2).
- 음수: `-1,234`·`(1,234)`·`△1,234`·`▲`(위쪽 삼각형은 양수 표기로 보지 않는다 — 음수 표기만).
- 해석할 수 없는 글자는 `strict=True` 면 `ValueError`, 아니면 None(호출자가 필수 칸인지 판단).
- HTML 표는 표준 `html.parser`(bs4 — lxml 불필요, D-P4-20)로 읽는다. 셀 글자는 공백을 하나로 접는다.
"""

from __future__ import annotations

import html as _html
import math
import re
from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Final

from bs4 import BeautifulSoup
from bs4.element import Tag

__all__ = [
    "BLANKS",
    "INT_MAX",
    "ParseError",
    "cell_after",
    "html_tables",
    "largest",
    "norm_label",
    "parse_date",
    "parse_float",
    "parse_int",
    "strip_html",
]

BLANKS: Final = frozenset({"", "-", "—", "–", "N/A", "n/a", "해당사항없음", "해당없음", "없음"})
# 금액·주식 수는 DB bigint(0010) — 그 밖의 정수는 해석 실패로 본다(저장에서 터지지 않게)
INT_MAX: Final = 2**63 - 1
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_NEG_MARKS: Final = ("△", "▽", "−")  # 회계 표기의 음수(△) — '−'(U+2212)도 음수 부호로
_DATE_PATTERNS: Final = (
    re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일?"),
    re.compile(r"(\d{4})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})"),
    re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)"),
)
# 표 머리의 번호 매김('1.'·'가.'·'(1)'·'①')과 끝 쌍점
_LABEL_HEAD = re.compile(r"^(?:\(?\d+\)|\d+\.|[가-하]\.|[①-⑳])\s*")


class ParseError(ValueError):
    """원문·구조화 응답에서 필요한 값을 읽지 못했다(그 공시 한 건의 실패 — 다른 건을 멈추지
    않는다)."""


def strip_html(text: str | None) -> str:
    """태그를 지우고 엔티티를 풀고 공백을 하나로(SD earnings_parser._strip_html + 엔티티)."""
    if not text:
        return ""
    return _WS.sub(" ", _html.unescape(_TAG.sub(" ", text))).strip()


def _clean(s: object) -> str:
    return _WS.sub(" ", str(s if s is not None else "")).strip()


def _number_text(s: object) -> tuple[str, bool] | None:
    """숫자 글자와 음수 여부. 빈 값이면 None."""
    t = _clean(s)
    if t in BLANKS:
        return None
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1].strip()
    for mark in _NEG_MARKS:
        if t.startswith(mark):
            neg, t = True, t[len(mark) :].strip()
    if t.startswith("-"):
        neg, t = not neg, t[1:].strip()
    elif t.startswith("+"):
        t = t[1:].strip()
    t = t.replace(",", "").replace(" ", "")
    for unit in ("원", "주", "%"):
        t = t.removesuffix(unit)
    if t in BLANKS:
        return None
    return t, neg


def parse_int(s: object, *, strict: bool = False) -> int | None:
    """'1,234'·'-1,234'·'(1,234)'·'△1,234'·'1,234주' → 정수. 빈 값 → None.

    소수(정수가 아닌 값)·NaN·무한·bigint 범위 밖은 해석 실패로 본다(금액·주식 수는 정수 —
    `decimal` 의 `InvalidOperation`(ValueError 가 아니다)이 호출자 밖으로 새지 않게 여기서 거른다).
    """
    got = _number_text(s)
    if got is None:
        return None
    t, neg = got
    try:
        d = Decimal(t)
    except InvalidOperation:
        if strict:
            raise ValueError(f"정수로 읽을 수 없다: {_clean(s)[:40]!r}") from None
        return None
    # is_finite 를 먼저 — sNaN 은 비교·to_integral_value 에서 InvalidOperation 을 낸다
    if not d.is_finite() or d != d.to_integral_value() or abs(d) > INT_MAX:
        if strict:
            raise ValueError(f"정수가 아니다(또는 범위 밖): {_clean(s)[:40]!r}")
        return None
    v = int(d)
    return -v if neg else v


def parse_float(s: object, *, strict: bool = False) -> float | None:
    """'12.34'·'-0.5'·'1,234.5'·'(3.2)' → 실수. 빈 값 → None."""
    got = _number_text(s)
    if got is None:
        return None
    t, neg = got
    try:
        d = Decimal(t)
    except InvalidOperation:
        if strict:
            raise ValueError(f"수로 읽을 수 없다: {_clean(s)[:40]!r}") from None
        return None
    v = float(d) if d.is_finite() else math.inf
    if not math.isfinite(v):  # Decimal 로는 유한해도 float 로 넘치는 값('1e400')
        if strict:
            raise ValueError(f"유한한 수가 아니다: {_clean(s)[:40]!r}")
        return None
    return -v if neg else v


def parse_date(s: object, *, strict: bool = False) -> date | None:
    """'2026년 03월 15일'·'2026-03-15'·'2026.03.15'·'20260315' → date. 빈 값 → None."""
    t = _clean(s)
    if t in BLANKS:
        return None
    for pat in _DATE_PATTERNS:
        m = pat.search(t)
        if m is None:
            continue
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            break
    if strict:
        raise ValueError(f"날짜로 읽을 수 없다: {t[:40]!r}")
    return None


def norm_label(s: str) -> str:
    """표 머리 비교용 — 번호 매김·공백·끝 쌍점을 지운다('1. 공시예정일 :' → '공시예정일')."""
    t = _LABEL_HEAD.sub("", _clean(s))
    return t.replace(" ", "").rstrip(":：")


def html_tables(text: str | None) -> list[list[list[str]]]:
    """HTML 의 표마다 [행][셀] 글자(공백 접음). 표가 없으면 빈 목록."""
    if not text:
        return []
    soup = BeautifulSoup(text, "html.parser")
    out: list[list[list[str]]] = []
    for table in soup.find_all("table"):
        if not isinstance(table, Tag):
            continue
        rows: list[list[str]] = []
        for tr in table.find_all("tr"):
            if not isinstance(tr, Tag):
                continue
            cells = [
                _clean(td.get_text(" ")) for td in tr.find_all(["td", "th"]) if isinstance(td, Tag)
            ]
            if cells:
                rows.append(cells)
        if rows:
            out.append(rows)
    return out


def cell_after(tables: Sequence[Sequence[Sequence[str]]], labels: Iterable[str]) -> str | None:
    """표에서 머리 칸이 `labels` 중 하나인 셀의 바로 다음(빈 칸 아닌) 셀 글자. 없으면 None.

    머리는 `norm_label` 로 비교한다(번호·공백 무시). 같은 머리가 여러 번이면 처음 것.
    """
    wanted = {norm_label(x) for x in labels}
    for table in tables:
        for row in table:
            for i, cell in enumerate(row):
                if norm_label(cell) not in wanted:
                    continue
                for nxt in row[i + 1 :]:
                    if _clean(nxt) not in BLANKS:
                        return _clean(nxt)
                return None
    return None


def largest(files: Sequence[tuple[str, str]]) -> str | None:
    """원문 zip 의 파일들 중 본문(가장 큰 파일 — SD fetch_dart_document 와 같은 고르기)."""
    if not files:
        return None
    return max(files, key=lambda f: len(f[1]))[1]
