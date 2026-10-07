"""영업이익 → 당기순이익 워터폴 브릿지 — 이미지 3 블록.

골격(영업이익 / 세전이익 / 법인세 / 순이익)은 정형 API에서 확실히 나온다.
그 사이의 영업외손익 분해(FVPL 평가이익, 순이자, 순외환, 배당·기타)는 주석에서 긁는다.
주석 파싱이 실패해도 브릿지가 깨지지 않도록 **잔차(기타 영업외손익)** 플러그를 둬서
항상 세전이익에 정확히 맞춘다. 잔차가 크면 그 자체가 '주석 파싱을 손봐야 한다'는 신호다.
"""

from __future__ import annotations

from typing import Any

from .client import DartClient
from .costs import (
    _norm,
    _num,
    _parse_cost_table,
    _strip_footnote,
    _tables_with_title,
    find_periodic_reports,
)
from .statements import EOK, Period

BRIDGE_ORDER = ["FVPL 평가이익", "순이자손익", "순외환손익", "배당·기타"]


def _bucketize_bridge(
    rows: list[tuple[str, float]], cfg: dict[str, Any]
) -> tuple[dict[str, float], list[tuple[str, float]]]:
    buckets = {b: 0.0 for b in BRIDGE_ORDER}
    negatives = [_norm(k) for k in cfg.get("negative_keywords", [])]
    unmapped: list[tuple[str, float]] = []

    for label, value in rows:
        n = _norm(label)
        if _strip_footnote(n) in {"합계", "계", "총계"}:
            continue
        placed = False
        for bucket, keywords in cfg["buckets"].items():
            if any(_norm(k) in n for k in keywords):
                signed = -abs(value) if any(k in n for k in negatives) else value
                buckets[bucket] += signed
                placed = True
                break
        if not placed:
            unmapped.append((label, value))
    return buckets, unmapped


def _file_estimate(rows: list[tuple[str, float]], cfg: dict[str, Any]) -> float:
    """한 파일의 영업외손익 추정: 버킷합 + 부호 적용한 미매핑 합. 파일 선택용."""
    buckets, un = _bucketize_bridge(rows, cfg)
    negatives = [_norm(k) for k in cfg.get("negative_keywords", [])]
    signed_un = sum(
        (-abs(v) if any(k in _norm(l) for k in negatives) else v) for l, v in un
    )
    return sum(buckets.values()) + signed_un


def fetch_bridge_details(
    client: DartClient,
    corp_code: str,
    years: list[int],
    mapping: dict[str, Any],
    verbose: bool = False,
    expected: dict[tuple[int, str], float] | None = None,
) -> dict[tuple[int, str], dict[str, float]]:
    """(연도, 분기) → 영업외손익 버킷 (원 단위, 누적).

    같은 공시 ZIP 에 본문·연결 감사보고서·별도 감사보고서가 각각 같은 표를
    싣고 있어, 전부 합치면 이중·삼중 합산이 된다 (006110 실측 — 잔차 37.6억의
    정체). expected(세전이익-영업이익, 정형 API)에 가장 가까운 **한 파일**만 쓴다.
    """
    cfg = mapping["bridge"]
    reports = find_periodic_reports(client, corp_code, years)
    expected = expected or {}
    out: dict[tuple[int, str], dict[str, float]] = {}

    for key, rcept_no in sorted(reports.items()):
        exp = expected.get(key)
        candidates: list[list[tuple[str, float]]] = []
        for _fname, html in client.document_texts(rcept_no):
            rows: list[tuple[str, float]] = []
            for _kw, table, scale in _tables_with_title(html, cfg["note_titles"]):
                rows.extend(_parse_cost_table(table, scale))
            if rows:
                candidates.append(rows)
        if not candidates:
            continue
        if exp is not None and len(candidates) > 1:
            chosen = min(candidates, key=lambda r: abs(_file_estimate(r, cfg) - exp))
        else:
            chosen = candidates[0]
        buckets, un = _bucketize_bridge(chosen, cfg)
        out[key] = buckets
        if verbose and un:
            print(f"  [브릿지] {key} 미매핑 {len(un)}건 (잔차로 흡수)")
    return out


def build_bridge(
    period: Period, details: dict[str, float] | None
) -> list[dict[str, Any]]:
    """한 기간의 워터폴 단계 리스트. 값은 억원."""
    cum = period.cumulative
    op = cum.get("operating_income")
    pretax = cum.get("pretax_income")
    tax = cum.get("tax")
    net = cum.get("net_income")

    if op is None or pretax is None or net is None:
        return []

    steps: list[dict[str, Any]] = [
        {"name": "영업이익", "value": op / EOK, "kind": "total"},
    ]

    parts = dict(details or {})
    explained = sum(parts.values())
    residual = (pretax - op) - explained

    for name in BRIDGE_ORDER:
        v = parts.get(name)
        if v is None or abs(v) < 1_000_000:  # 0.01억 미만은 생략
            continue
        steps.append({"name": name, "value": v / EOK, "kind": "delta"})

    if abs(residual) >= 1_000_000:
        steps.append(
            {
                "name": "기타 영업외손익" if parts else "영업외손익",
                "value": residual / EOK,
                "kind": "delta",
                "note": "주석 미분해 잔차" if parts else "주석 분해 미적용",
            }
        )

    steps.append({"name": "법인세차감전순이익", "value": pretax / EOK, "kind": "total"})
    if tax is not None:
        steps.append({"name": "법인세비용", "value": -abs(tax) / EOK, "kind": "delta"})
    steps.append({"name": "당기순이익", "value": net / EOK, "kind": "total"})
    return steps


def waterfall_layout(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """엑셀 누적막대로 워터폴을 그리기 위한 (받침, 증가, 감소, 합계) 분해."""
    out: list[dict[str, Any]] = []
    running = 0.0
    for step in steps:
        v = step["value"]
        if step["kind"] == "total":
            running = v
            out.append({**step, "base": 0.0, "up": 0.0, "down": 0.0, "total": v})
        else:
            if v >= 0:
                out.append({**step, "base": running, "up": v, "down": 0.0, "total": 0.0})
            else:
                out.append({**step, "base": running + v, "up": 0.0, "down": -v, "total": 0.0})
            running += v
    return out
