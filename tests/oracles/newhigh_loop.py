"""신고가 독립 오라클 — 엔진을 쓰지 않는 단순 루프(docs/p3_design.md §4.1 6단계·§8.1, ADR 0017).

신고가 3축(사용자 요청 2026-10-08)을 정의 문장 그대로 **무식하게** 다시 센다. 엔진
(`kbj.engines.board.newhigh`)의 `bisect`·인덱스 계산을 하나도 쓰지 않는다 — 날짜 비교와 목록
훑기만 한다. 엔진과 이 루프가 같은 답을 내는 것이 정의가 맞게 구현됐다는 근거다
(tests/property/test_newhigh_oracle.py — 골든을 새 엔진으로 다시 캡처한 뒤로는 이 시험이 정확성을
지킨다. 처음 판은 ET `flowlab/verify.py:check_newhigh`:132 의 '직전 n봉' 루프였다).

  d120  판정일 직전 120 **시장 거래일**: 시장 거래일 중 판정일보다 앞인 날을 거꾸로 120개 센 마지막
        날이 창의 시작. 창 = [시작, 판정일) 안의 그 종목 봉 전부(거래정지로 빈 날은 비어 있을 뿐).
  w52   달력 52주: 판정일 − 364일 ≤ 봉 날짜 < 판정일.
  창 채움  종목의 첫 봉이 '창 안의 첫 시장 거래일' 이하일 때만(아니면 None — 상장·이력이 짧다).
  비교  당일 값 > 창 최고가면 신고가(엄격 — 같은 값은 아니다).

수정주가 가드(분할 의심 지점 이후로 창을 자르는 것)는 여기 없다 — 엔진이 거기에 더하는 조건이다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any

BASES = ("high", "close")


def _iso(d: date | str) -> str:
    return d.isoformat() if isinstance(d, date) else str(d)


def window_by_loop(
    market: Iterable[date | str], asof: str, unit: str, n: int
) -> tuple[str, str] | None:
    """(창 시작일, 창 안의 첫 시장 거래일) — 루프로. 시장 달력이 창을 못 덮으면 None."""
    days = sorted({_iso(d) for d in market})
    before = [d for d in days if d < asof]
    if unit == "trading":
        count = 0
        for d in reversed(before):
            count += 1
            if count == n:
                return d, d
        return None
    start = (date.fromisoformat(asof) - timedelta(days=n)).isoformat()
    # 달력이 시작일 이전까지 닿지 않으면 창 안 첫 거래일을 모른다 → 시작일 그대로(보수)
    if not days or days[0] > start:
        return start, start
    for d in before:
        if d >= start:
            return start, d
    return start, start


def labels_by_loop(
    series: Sequence[Mapping[str, Any]],
    asof: str,
    windows: Mapping[str, tuple[str, int]],
    market: Iterable[date | str] | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """{기준: {라벨: {'ref': 창 최고가|None, 'hit': bool|None}}}.

    series   오름차순 일봉 [{asof, high, close, …}] — asof 이후 행은 보지 않는다
    windows  {라벨: ('trading'|'calendar', n)} — hist 는 넣지 않는다(스칼라라 창이 아니다)
    market   시장 거래일. None 이면 그 종목 봉 날짜(엔진의 달력 없는 호출과 같은 뜻)
    """
    rows = [r for r in series if r["asof"] <= asof]
    if not rows or rows[-1]["asof"] != asof:
        return {b: {} for b in BASES}
    prior = rows[:-1]
    cal = [r["asof"] for r in rows] if market is None else list(market)
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for basis in BASES:
        today = rows[-1].get(basis)
        got: dict[str, dict[str, Any]] = {}
        for kind, (unit, n) in windows.items():
            w = window_by_loop(cal, asof, unit, n)
            if w is None or not prior or prior[0]["asof"] > w[1]:
                got[kind] = {"ref": None, "hit": None}
                continue
            vals = [r.get(basis) for r in prior if r["asof"] >= w[0]]
            vals = [v for v in vals if v]
            ref = max(vals) if vals else None
            hit = None if ref is None or today is None else today > ref
            got[kind] = {"ref": ref, "hit": hit}
        out[basis] = got
    return out


def hist_by_loop(series: Sequence[Mapping[str, Any]], asof: str) -> dict[str, bool | None]:
    """역사적(상장 이후 전체 — series 가 상장일부터라고 본다): 당일 값 > 판정일 전 전체 최고가."""
    rows = [r for r in series if r["asof"] <= asof]
    if len(rows) < 2 or rows[-1]["asof"] != asof:
        return {b: None for b in BASES}
    out: dict[str, bool | None] = {}
    for basis in BASES:
        vals: list[float] = [v for r in rows[:-1] if (v := r.get(basis))]
        today = rows[-1].get(basis)
        out[basis] = None if not vals or today is None else today > max(vals)
    return out
