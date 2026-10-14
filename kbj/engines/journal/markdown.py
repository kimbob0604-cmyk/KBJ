"""분석 일지 → 마크다운(`GET /api/journal/analysis/{id}/markdown` — docs/p8_design.md §5). 순수.

칸마다 출처(kbj 계산·컨센서스·수기)를 함께 적는다. 빈칸은 '—'. 메모·결론은 사용자가 쓴 글 그대로
(머리 줄 기호를 이스케이프하지 않는다 — 내보내기는 사용자 본인용).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Final

from kbj.core.rules_rows import AnalysisRow

__all__ = ["SOURCE_LABEL", "to_markdown"]

SOURCE_LABEL: Final = {"kbj": "KBJ 계산", "consensus": "컨센서스", "manual": "수기"}
_DASH: Final = "—"


def _num(v: float | Decimal | None, digits: int = 2) -> str:
    if v is None:
        return _DASH
    x = float(v)
    if x.is_integer() and abs(x) >= 1000:
        return f"{int(x):,}"
    return f"{x:,.{digits}f}"


def _src(sources: Mapping[str, str], key: str) -> str:
    s = sources.get(key)
    return f" ({SOURCE_LABEL.get(s, s)})" if s else ""


def to_markdown(row: AnalysisRow, *, name: str | None = None) -> str:
    vs = row.value_sources
    title = f"{name} ({row.code})" if name else row.code
    lines = [
        f"# 분석 일지 — {title}",
        "",
        f"- 분석일: {row.analyzed_on.isoformat()}",
        f"- 반영도: {row.conviction or _DASH}",
        f"- 태그: {', '.join(row.tags) if row.tags else _DASH}",
        f"- 밸류체인: {' / '.join(c for c in row.chain_ids if c) or _DASH}",
        "",
        "## 지표",
        "",
        "| 칸 | 값 |",
        "|---|---|",
        f"| 현재가(원) | {_num(row.price)}{_src(vs, 'price')} |",
        f"| 52주 수익률(%) | {_num(row.ret_52w)}{_src(vs, 'ret_52w')} |",
        f"| Fwd PER(배) | {_num(row.fwd_per)}{_src(vs, 'fwd_per')} |",
        f"| PER 밴드 위치(%) | {_num(row.band_pos, 1)}{_src(vs, 'band_pos')} |",
        f"| EV/EBITDA(배) | {_num(row.ev_ebitda)}{_src(vs, 'ev_ebitda')} |",
        f"| 시가총액(원) | {_num(row.mktcap, 0)}{_src(vs, 'mktcap')} |",
        f"| 영업이익률(%) | {_num(row.opm)}{_src(vs, 'opm')} |",
        "",
        "## 시나리오",
        "",
        "| 경우 | EPS(원) | PER(배) | 적정가(원) |",
        "|---|---|---|---|",
    ]
    for case, label in (("bear", "약세"), ("base", "기본"), ("bull", "강세")):
        lines.append(
            f"| {label} | {_num(row.eps.get(case))} | {_num(row.per.get(case))} | "
            f"{_num(row.tp.get(case))} |"
        )
    lines += [
        "",
        f"적정가 출처: {row.tp_source or _DASH}{_src(vs, 'tp')}",
        "",
        "## 메모",
        "",
        row.memo or _DASH,
        "",
        "## 결론",
        "",
        row.conclusion or _DASH,
        "",
    ]
    return "\n".join(lines)
