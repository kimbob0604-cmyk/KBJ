"""본문 분할·조각 머리·캡션(kbj.services.notifier.format).

승격: ET `board/tests/test_telegram.py:TestSplit` 2개 — `T._split(text, limit)` → `split_text(text,
limit)`(단언 그대로).
새로: HTML 태그가 조각을 넘지 않음, 태그·엔티티 가운데서 자르지 않음, `(i/n)` 머리를 붙여도 상한 안,
캡션 상한.
"""

from __future__ import annotations

import re

import pytest

from kbj.services.notifier.format import (
    TG_CAPTION_LIMIT,
    TG_LIMIT,
    cap_caption,
    part_header,
    split_numbered,
    split_text,
    with_part_headers,
)

# ── 승격: ET TestSplit ───────────────────────────────────────────────────────────────────


def test_chunks_cut_at_line_boundaries() -> None:
    lines = [f"{i}번째 줄 " + "가" * 40 for i in range(200)]
    chunks = split_text("\n".join(lines), 500)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= 500
    assert "\n".join(chunks).split("\n") == lines  # 문장 중간에서 안 자른다


def test_single_overlong_line_is_not_lost() -> None:
    chunks = split_text("가" * 130, 50)
    assert "".join(chunks) == "가" * 130


# ── 새로 ─────────────────────────────────────────────────────────────────────────────────

_TAG = re.compile(r"<(/?)([a-z]+)[^>]*>")


def _balanced(chunk: str) -> bool:
    stack: list[str] = []
    for m in _TAG.finditer(chunk):
        if m.group(1):
            if not stack or stack[-1] != m.group(2):
                return False
            stack.pop()
        else:
            stack.append(m.group(2))
    return not stack


def test_html_tags_never_cross_a_chunk() -> None:
    body = "<b>제목</b>\n<i>" + "\n".join(f"{i}번 종목 +1.23%" for i in range(600)) + "</i>"
    chunks = split_text(body, TG_LIMIT, "HTML")
    assert len(chunks) > 1
    assert all(len(c) <= TG_LIMIT for c in chunks)
    assert all(_balanced(c) for c in chunks)
    assert chunks[1].startswith("<i>")  # 다음 조각에서 다시 연다


def test_html_overlong_line_is_not_cut_inside_a_tag_or_entity() -> None:
    line = ('가나다 <a href="https://example.com/x">링크</a> &amp; ' * 300).strip()
    chunks = split_text(line, 1000, "HTML")
    for c in chunks:
        assert len(c) <= 1000
        assert c.count("<") == c.count(">")
        assert not re.search(r"&[a-z]*$", c)
        assert _balanced(c)


def test_plain_mode_does_not_touch_tags() -> None:
    assert split_text("<b>열림", 100) == ["<b>열림"]


def test_numbered_headers_fit_the_limit() -> None:
    body = "\n".join("줄" * 50 for _ in range(300))
    chunks = split_numbered(body, TG_LIMIT, "HTML")
    n = len(chunks)
    assert n > 1
    assert all(len(c) <= TG_LIMIT for c in chunks)
    assert chunks[0].startswith(f"<i>(1/{n})</i>\n") and chunks[-1].startswith(f"<i>({n}/{n})")


def test_single_chunk_gets_no_header() -> None:
    assert split_numbered("짧다", parse_mode="HTML") == ["짧다"]
    assert with_part_headers(["하나"], "HTML") == ["하나"]


@pytest.mark.parametrize(
    ("mode", "head"),
    [("HTML", "<i>(1/2)</i>\n"), ("Markdown", "_(1/2)_\n"), (None, "(1/2)\n")],
)
def test_part_header_by_parse_mode(mode: str | None, head: str) -> None:
    assert part_header(1, 2, mode) == head


def test_caption_is_capped_visibly() -> None:
    assert cap_caption("짧은 캡션") == "짧은 캡션"
    long = cap_caption("가" * 2000)
    assert len(long) == TG_CAPTION_LIMIT and long.endswith("…")
    assert cap_caption(None) == ""


def test_empty_body_is_one_empty_chunk() -> None:
    assert split_text("") == [""]
    with pytest.raises(ValueError):
        split_text("x", 0)
