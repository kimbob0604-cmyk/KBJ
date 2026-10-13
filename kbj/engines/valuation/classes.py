"""종류주식 → 회사 — `prv_market.share_class`(docs/p4_design.md §3.4·§4.5·§4.8, R13).

우선주는 DART 고유번호가 없다 → 보통주(`common_code`)의 회사 재무를 쓰고, 회사 시가총액은 보통주 +
우선주를 더한다(D-P4-11). 짝은 **코드 규칙과 이름 규칙이 둘 다 맞을 때만** 만든다(R13 — P3
`kbj.engines.board.kinds` 원칙: 확신이 없으면 단정하지 않는다).

- 코드 규칙: 우선주 코드의 끝자리를 0 으로 바꾼 코드가 같은 날 유니버스에 보통주로 있다.
- 이름 규칙: 우선주 이름에서 접미('우'·'우B'·'2우B')를 뗀 이름(`kinds.common_name`)이 그 보통주
  이름과 같다.
- 둘 중 하나라도 어긋나면 그 우선주는 짝 없음(`unmatched` — 사유 문자열). 처리기는 그 종목의 배수를
  만들지 않고(missing_input) 사유를 남긴다.
- 스팩·리츠·이름 모름(None)은 그 종목 하나가 회사의 유일한 종류로 본다('common'). ETF·ETN 은 뺀다.
- corp_code 는 P2 고유번호 표(`{종목코드: (corp_code, 회사명)}`)의 보통주 행 — 없으면 None.

I/O 를 하지 않는다(계약 ⑨).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Final

from kbj.core.fin_rows import ShareClass
from kbj.core.rows import UniverseRow
from kbj.engines.board.kinds import common_name

__all__ = ["FUND_KINDS", "ShareClasses", "share_classes"]

FUND_KINDS: Final = frozenset({"etf", "etn"})
SOURCE: Final = "krx"


@dataclass(frozen=True)
class ShareClasses:
    """종류주식 표 + 회사(보통주 코드)별 종목 목록(보통주 먼저) + 짝 없는 우선주 {코드: 사유}."""

    rows: tuple[ShareClass, ...]
    groups: Mapping[str, tuple[str, ...]]
    unmatched: Mapping[str, str] = field(default_factory=dict[str, str])

    def of(self, code: str) -> ShareClass | None:
        for r in self.rows:
            if r.code == code:
                return r
        return None

    def by_code(self) -> dict[str, ShareClass]:
        return {r.code: r for r in self.rows}


def share_classes(
    universe: Iterable[UniverseRow], corp_index: Mapping[str, tuple[str, str]]
) -> ShareClasses:
    """유니버스(한 날) + 고유번호 표 → 종류주식 표."""
    stocks = {u.code: u for u in universe if (u.kind or "common") not in FUND_KINDS}
    rows: list[ShareClass] = []
    groups: dict[str, list[str]] = {}
    unmatched: dict[str, str] = {}

    def corp_of(code: str) -> str | None:
        got = corp_index.get(code)
        return got[0] if got else None

    for code, u in sorted(stocks.items()):
        if u.kind != "pref":
            rows.append(ShareClass(code, corp_of(code), "common", code, source=SOURCE))
            groups.setdefault(code, []).insert(0, code)
            continue
        common = code[:-1] + "0"
        cu = stocks.get(common)
        if code[-1] == "0" or cu is None or cu.kind not in (None, "common"):
            unmatched[code] = f"코드 규칙: 보통주 {common} 가 유니버스에 없다"
            continue
        if common_name(u.name or "") != (cu.name or "").strip() or not (cu.name or "").strip():
            unmatched[code] = f"이름 규칙: '{u.name}' 의 보통주 이름이 '{cu.name}' 와 다르다"
            continue
        rows.append(ShareClass(code, corp_of(common), "pref", common, source=SOURCE))
        groups.setdefault(common, []).append(code)
    ordered = {
        c: tuple([c, *sorted(x for x in codes if x != c)]) for c, codes in sorted(groups.items())
    }
    return ShareClasses(tuple(rows), ordered, unmatched)
