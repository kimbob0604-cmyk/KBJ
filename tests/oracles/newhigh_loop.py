"""신고가 독립 오라클 — 엔진을 쓰지 않는 단순 루프(docs/p3_design.md §4.1 6단계·§8.1).

ET `flowlab/verify.py:check_newhigh`:132 의 재현 방식을 이식했다: 기준(고가·종가)마다, 라벨이
가리키는 룩백 창 n 에 대해 **당일 값 > 직전 n 봉(당일 제외)의 최고값** 이면 그 라벨을 갱신한
것이다. 역사적 신고가는 사상 최고가 스칼라라 창으로 재현할 수 없으므로 'w52 를 넘었다' 는
필요조건만 본다.

엔진(`kbj.engines.board.newhigh.evaluate`)은 여기에 수정주가 가드·이력 하한·시총 같은 조건을 더할
뿐이므로 **엔진 라벨 ⊆ 루프 라벨** 이어야 한다(tests/property/test_newhigh_oracle.py).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

BASES = ("high", "close")


def labels_by_loop(
    series: Sequence[Mapping[str, Any]], asof: str, lookback: Mapping[str, int]
) -> dict[str, dict[str, bool | None]]:
    """{기준: {라벨: 갱신(True)·못 함(False)·창 부족(None)}}.

    `hist` 는 w52 필요조건(창 부족이면 None).

    series  오름차순 일봉 [{asof, high, close, …}] — asof 이후 행은 보지 않는다
    """
    rows = [r for r in series if r["asof"] <= asof]
    if not rows or rows[-1]["asof"] != asof:
        return {b: {} for b in BASES}
    out: dict[str, dict[str, bool | None]] = {}
    for basis in BASES:
        vals = [r.get(basis) for r in rows]
        today = vals[-1]
        got: dict[str, bool | None] = {}
        for kind, n in lookback.items():
            if today is None or len(vals) <= n:
                got[kind] = None
                continue
            win = [v for v in vals[-1 - n : -1] if v is not None]
            got[kind] = bool(win) and today > max(win)
        got["hist"] = got.get("w52")
        out[basis] = got
    return out
