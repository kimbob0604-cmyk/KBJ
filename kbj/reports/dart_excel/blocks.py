"""화면용 리포트 블록(JSON) — 엑셀과 **같은 입력·같은 셈**(docs/p4_design.md §4.6·§6.2 '리포트' 탭).

페이지 8 '리포트' 탭은 엑셀을 열지 않고 이 블록을 그린다. 화면과 엑셀 숫자가 갈라지지 않게 값은
엑셀 칸과 똑같이 만든다: 원천 칸은 `round(v, 1)`(억원), 합계·비율은 엑셀 수식이 계산할 값 그대로
(빈 칸은 산술에서 0, `IFERROR(…, "")` 는 None). `tests/unit/reports/test_blocks.py` 가 수식 평가기
(`tests/oracles/formula_eval.py`)로 계산한 엑셀 칸과 이 값이 같은지 본다.

블록(순서는 화면 순서)
- `quarterly`: 분기 실적(매출·영업이익·순이익·OPM·순이익률) — 엑셀 '분기실적'
- `annual_costs`: 연간 비용구조(7버킷·합계·역산 영업이익·OPM) — '연간비용구조'
- `bridge`: 영업이익 → 순이익 워터폴(받침·증가·감소·합계·비고) — '이익브릿지'
- `orders`: 수주(합계·NIPA·행) — '수주현황'
- `quarterly_costs`: 분기 비용구조(+ 변동/고정·공헌이익률·매출 대비 비중) — '분기비용구조'
- `warnings`·`unmapped`: 경고 줄·미매핑 계정(이름·원 — 'RAW_미매핑계정')

모든 숫자는 `source: "DART"` 의 공개 원천이다(봉투의 as_of·quality 는 읽는 쪽 — API·공개 내보내기).
JSON 안전: NaN·inf 를 넣지 않는다(없는 값은 None).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final, cast

from kbj.engines.financials.bridge import waterfall_layout
from kbj.engines.financials.costs import BUCKET_ORDER

if TYPE_CHECKING:
    from kbj.reports.dart_excel.pipeline import ReportInputs

__all__ = ["BLOCKS_VERSION", "build_blocks"]

BLOCKS_VERSION: Final = 1


def _r1(v: float | None) -> float | None:
    """엑셀 원천 칸과 같은 값(`round(v, 1)`)."""
    if v is None:
        return None
    return round(v, 1)


def _n(v: float | None) -> float:
    """엑셀 산술의 빈 칸 = 0."""
    return 0.0 if v is None else float(v)


def _div(a: float | None, b: float | None) -> float | None:
    """`IFERROR(a/b, "")` — 분모가 빈 칸·0 이면 None."""
    if b is None or b == 0:
        return None
    return _n(a) / b


def _finite(v: Any) -> Any:
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _quarterly(quarters: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for q in quarters:
        rev, op, ni = (_r1(q.get(k)) for k in ("revenue", "operating_income", "net_income"))
        rows.append(
            {
                "label": q["label"],
                "year": q["year"],
                "quarter": q["quarter"],
                "fs_div": q.get("fs_div"),
                "derived": bool(q.get("derived")),
                "revenue": rev,
                "operating_income": op,
                "net_income": ni,
                "opm": _div(op, rev),
                "npm": _div(ni, rev),
            }
        )
    return {"unit": "억원", "rows": rows}


def _annual_costs(annual: list[dict[str, Any]], acosts: list[dict[str, Any]]) -> dict[str, Any]:
    cost_by_label = {c["label"]: c for c in acosts}
    cols = [a for a in annual if a["label"] in cost_by_label] or annual
    revenue = [_r1(a.get("revenue")) for a in cols]
    buckets = {
        b: [_r1(cost_by_label.get(a["label"], {}).get(b)) for a in cols] for b in BUCKET_ORDER
    }
    total = [sum(_n(buckets[b][i]) for b in BUCKET_ORDER) for i in range(len(cols))]
    op = [_n(revenue[i]) - total[i] for i in range(len(cols))]
    return {
        "unit": "억원",
        "columns": [a["label"] for a in cols],
        "revenue": revenue,
        "buckets": buckets,
        "total": total,
        "operating_income": op,
        "opm": [_div(op[i], revenue[i]) for i in range(len(cols))],
        "has_costs": bool(acosts),
    }


def _bridge(steps: list[dict[str, Any]], label: str) -> dict[str, Any]:
    layout = waterfall_layout(steps)
    rows = [
        {
            "name": s["name"],
            "kind": s["kind"],
            "value": round(s["value"], 1),
            "base": round(s["base"], 1),
            "up": round(s["up"], 1),
            "down": round(s["down"], 1),
            "total": round(s["total"], 1),
            "note": s.get("note", ""),
        }
        for s in layout
    ]
    residual = next((r for r in rows if r["note"]), None)
    return {
        "unit": "억원",
        "label": label,
        "steps": rows,
        "residual": None if residual is None else residual["value"],
        "complete": bool(rows),
    }


def _orders(table: dict[str, Any]) -> dict[str, Any]:
    rows = [dict(r) for r in table["rows"]]
    total = sum(_n(r.get("수주액(억원)")) for r in rows)
    nipa = sum(_n(r.get("수주액(억원)")) for r in rows if r.get("NIPA") == "Y")
    return {
        "unit": "억원",
        "total_eok": total,
        "nipa_eok": nipa,
        "nipa_ratio": _div(nipa, total),
        "rows": rows,
    }


def _quarterly_costs(
    quarters: list[dict[str, Any]], qcosts: list[dict[str, Any]], mapping: dict[str, Any]
) -> dict[str, Any]:
    rev_by = {q["label"]: q.get("revenue") for q in quarters}
    cols = [c for c in qcosts if c["label"] in rev_by]
    behavior: dict[str, str] = {}
    for kind, buckets in mapping.get("cost_behavior", {}).items():
        for b in buckets:
            behavior[b] = kind
    revenue = [_r1(rev_by.get(c["label"])) for c in cols]
    buckets = {b: [_r1(c.get(b)) for c in cols] for b in BUCKET_ORDER}
    n = len(cols)
    total = [sum(_n(buckets[b][i]) for b in BUCKET_ORDER) for i in range(n)]
    op = [_n(revenue[i]) - total[i] for i in range(n)]
    variable = sorted(
        (BUCKET_ORDER.index(b) for b, k in behavior.items() if k == "변동비" and b in BUCKET_ORDER)
    )
    var_sum = [sum(_n(buckets[BUCKET_ORDER[j]][i]) for j in variable) for i in range(n)]
    return {
        "unit": "억원",
        "columns": [c["label"] for c in cols],
        "revenue": revenue,
        "buckets": buckets,
        "behavior": {b: behavior.get(b, "") for b in BUCKET_ORDER},
        "total": total,
        "operating_income": op,
        "opm": [_div(op[i], revenue[i]) for i in range(n)],
        "contribution_margin": [_div(_n(revenue[i]) - var_sum[i], revenue[i]) for i in range(n)],
        "share": {b: [_div(buckets[b][i], revenue[i]) for i in range(n)] for b in BUCKET_ORDER},
    }


def _unmapped(unmapped: list[tuple[str, float]]) -> list[dict[str, Any]]:
    agg: dict[str, float] = {}
    for label, value in unmapped:
        agg[label] = agg.get(label, 0.0) + value
    return [
        {"label": label, "amount": value}
        for label, value in sorted(agg.items(), key=lambda kv: -abs(kv[1]))
    ]


def _clean(obj: Any) -> Any:
    if isinstance(obj, Mapping):
        items = cast(Mapping[Any, Any], obj)
        return {str(k): _clean(v) for k, v in items.items()}
    if isinstance(obj, list | tuple):
        seq = cast(Sequence[Any], obj)
        return [_clean(v) for v in seq]
    return _finite(obj)


def build_blocks(inputs: ReportInputs, generated_at: datetime) -> dict[str, Any]:
    """리포트 입력 → 화면 블록 JSON(엑셀 칸과 같은 값)."""
    out = {
        "version": BLOCKS_VERSION,
        "source": "DART",
        "unit": "억원",
        "generated_at": generated_at.isoformat(),
        "meta": inputs.meta,
        "quarterly": _quarterly(inputs.quarters),
        "annual_costs": _annual_costs(inputs.annual, inputs.acosts),
        "bridge": _bridge(inputs.bridge_steps, inputs.bridge_label),
        "orders": _orders(inputs.orders),
        "quarterly_costs": _quarterly_costs(inputs.quarters, inputs.qcosts, inputs.mapping),
        "warnings": list(inputs.warnings),
        "unmapped": _unmapped(inputs.unmapped),
    }
    return _clean(out)
