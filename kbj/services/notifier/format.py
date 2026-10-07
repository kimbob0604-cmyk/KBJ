"""발송 본문 다듬기 — 4,096자 분할·조각 머리·캡션 상한(설계 §1.6 `format.py`).

승격 원본:
- ET `board/report/telegram.py:_split`(:237) — **줄 경계에서만** 자른다. 한 줄이 통째로 한도를 넘는
  병적인 경우에만 공백에서, 그마저 없으면 글자 수로 자른다(문장 중간에서 안 자른다).
  `TG_LIMIT` 4096, `TG_CAPTION_LIMIT` 1024.
- SD `server.py:_split_telegram_lines`·`send_telegram_long`(:5273) — 조각이 둘 이상이면 머리에
  `(i/n)` 을 붙인다(안 붙이면 둘째 조각이 왜 제목 없이 시작하는지 모른다). SD 는 머리 자리로 196자를
  비워 3,900자로 잘랐다 — 여기서는 머리 길이만큼 비운다.

HTML 파스 모드에서 줄 하나가 한도를 넘어 줄 안에서 잘라야 하면 태그(`<…>`)·엔티티(`&…;`) 가운데서
자르지 않고, 조각 끝에 열린 태그가 남으면 그 조각에서 닫고 다음 조각 앞에서 다시 연다 — 텔레그램은
짝이 안 맞는 HTML 을 400 으로 거절한다(SD 머리말).
"""

from __future__ import annotations

import re
from typing import Final

TG_LIMIT: Final = 4096  # sendMessage 본문 상한. 넘으면 텔레그램이 거절하거나 자른다
TG_CAPTION_LIMIT: Final = 1024  # sendDocument·sendMediaGroup 캡션 상한
MEDIA_GROUP_MAX: Final = 10  # sendMediaGroup 한 묶음의 사진 수(텔레그램 규약 — ET flow)
ELLIPSIS: Final = "…"

# 조각 머리 자리. "(999/999)" + 서식 태그 + 줄바꿈이 들어갈 만큼
_HEADER_RESERVE: Final = 24
# HTML 태그 다시 열기·닫기 자리(조각마다). 이보다 많이 열린 채 잘리는 본문은 거의 없다
_HTML_RESERVE: Final = 160
_TAG = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9-]*)(?:\s[^<>]*)?>")
_VOID_TAGS: Final = frozenset({"br"})


def _split_lines(text: str, limit: int) -> list[str]:
    """ET `_split` 그대로 — 줄 경계에서 자르고, 한 줄이 한도를 넘으면 공백·글자 수로."""
    out: list[str] = []
    cur = ""
    for raw in (text or "").split("\n"):
        line = raw
        while len(line) > limit:  # 한 줄이 통째로 한도를 넘는 병적인 경우
            cut = line.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:cut])
            line = line[cut:].lstrip()
        if not cur:
            cur = line
        elif len(cur) + 1 + len(line) <= limit:
            cur += "\n" + line
        else:
            out.append(cur)
            cur = line
    if cur:
        out.append(cur)
    return [x for x in out if x.strip()] or [""]


def _safe_cut(line: str, limit: int) -> int:
    """HTML 한 줄을 `limit` 안에서 자를 자리 — 공백 우선, 태그·엔티티 가운데는 피한다."""
    cut = line.rfind(" ", 0, limit)
    cut = cut if cut > 0 else limit
    lt = line.rfind("<", 0, cut)
    if lt != -1 and line.rfind(">", 0, cut) < lt:  # 태그 안 — 태그 앞에서
        cut = lt
    amp = line.rfind("&", 0, cut)
    if amp != -1 and ";" not in line[amp:cut] and cut - amp <= 10:  # 엔티티 안
        cut = amp
    return cut if cut > 0 else limit


def _split_lines_html(text: str, limit: int) -> list[str]:
    """`_split_lines` 와 같지만 줄 안에서 자를 때 태그·엔티티를 피한다."""
    out: list[str] = []
    cur = ""
    for raw in (text or "").split("\n"):
        line = raw
        while len(line) > limit:
            cut = _safe_cut(line, limit)
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:cut])
            line = line[cut:].lstrip()
        if not cur:
            cur = line
        elif len(cur) + 1 + len(line) <= limit:
            cur += "\n" + line
        else:
            out.append(cur)
            cur = line
    if cur:
        out.append(cur)
    return [x for x in out if x.strip()] or [""]


def _open_tags_after(chunk: str, opened: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """앞 조각에서 열린 채 넘어온 태그 + 이 조각 → 이 조각 끝에 열려 있는 태그(이름, 여는 태그)."""
    stack = list(opened)
    for m in _TAG.finditer(chunk):
        closing, name = m.group(1) == "/", m.group(2).lower()
        if name in _VOID_TAGS:
            continue
        if not closing:
            stack.append((name, m.group(0)))
            continue
        for i in range(len(stack) - 1, -1, -1):  # 가장 가까운 같은 이름을 닫는다
            if stack[i][0] == name:
                del stack[i]
                break
    return stack


def _balance_html(chunks: list[str]) -> list[str]:
    """조각 끝에 열린 태그는 닫고, 다음 조각 앞에서 같은 태그로 다시 연다."""
    out: list[str] = []
    carried: list[tuple[str, str]] = []
    for chunk in chunks:
        head = "".join(tag for _, tag in carried)
        still_open = _open_tags_after(chunk, carried)
        tail = "".join(f"</{name}>" for name, _ in reversed(still_open))
        out.append(head + chunk + tail)
        carried = still_open
    return out


def split_text(text: str, limit: int = TG_LIMIT, parse_mode: str | None = None) -> list[str]:
    """발송용 분할 — 조각마다 `limit` 자 이하, 줄 경계에서 자른다(ET `_split`).

    parse_mode 가 HTML 이면 태그가 조각을 넘지 않게 맞춘다(그 몫만큼 `limit` 안에서 비운다).
    빈 본문은 `[""]`(보낼지 말지는 부르는 쪽이 정한다 — ET 와 같다).
    """
    if limit < 1:
        raise ValueError("limit 는 1 이상")
    if (parse_mode or "").upper() != "HTML":
        return _split_lines(text, limit)
    if limit <= _HTML_RESERVE * 2:
        raise ValueError(f"HTML 분할 limit 는 {_HTML_RESERVE * 2} 보다 커야 한다")
    out = _balance_html(_split_lines_html(text, limit - _HTML_RESERVE))
    over = [i for i, c in enumerate(out, 1) if len(c) > limit]
    if over:  # 긴 속성(<a href>)이 여러 겹 열린 채 잘린 경우 — 조용히 넘기지 않는다
        raise ValueError(f"HTML 태그를 다시 열 자리가 모자란다(조각 {over[0]}/{len(out)})")
    return out


def part_header(i: int, n: int, parse_mode: str | None) -> str:
    """조각 머리 한 줄(줄바꿈 포함). SD `send_telegram_long` 의 `<i>(i/n)</i>`."""
    mode = (parse_mode or "").upper()
    if mode == "HTML":
        return f"<i>({i}/{n})</i>\n"
    if mode == "MARKDOWN":
        return f"_({i}/{n})_\n"
    if mode == "MARKDOWNV2":
        return f"_\\({i}/{n}\\)_\n"
    return f"({i}/{n})\n"


def with_part_headers(chunks: list[str], parse_mode: str | None) -> list[str]:
    """조각이 둘 이상이면 각 조각 머리에 `(i/n)` — 하나면 그대로."""
    if len(chunks) < 2:
        return list(chunks)
    n = len(chunks)
    return [part_header(i, n, parse_mode) + c for i, c in enumerate(chunks, 1)]


def split_numbered(text: str, limit: int = TG_LIMIT, parse_mode: str | None = None) -> list[str]:
    """머리 자리를 비우고 나눈 뒤 `(i/n)` 머리를 붙인다 — 머리를 붙여도 조각마다 `limit` 이하."""
    chunks = split_text(text, limit, parse_mode)
    if len(chunks) < 2:
        return chunks
    return with_part_headers(split_text(text, limit - _HEADER_RESERVE, parse_mode), parse_mode)


def cap_caption(text: str | None, limit: int = TG_CAPTION_LIMIT) -> str:
    """캡션 상한. 넘으면 `limit - 1` 자 + `…` — 잘렸다는 것을 보이게(ET 는 소리 없이 잘랐다)."""
    if limit < 1:
        raise ValueError("limit 는 1 이상")
    s = text or ""
    return s if len(s) <= limit else s[: limit - 1] + ELLIPSIS
