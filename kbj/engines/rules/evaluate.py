"""평가기 — (규칙, 프레임, 칸) → 판정(P8 묶음 FND, docs/p8_design.md §4.2-2, metrics §30.3).

순수 함수: 같은 입력이면 같은 출력. 실시간·백테스트가 같은 함수를 부른다(D-P8-6). 칸 i 의 판정은 칸
≤ i 의 값만 읽는다(지표가 그렇게 정의돼 있다 — 속성 시험 `test_rules_eval_props.py`).

- 조건마다 `CondResult(ok, value, ref_value, prev_value, prev_ref, reason)` — 근거 메시지(EVL)가
  그대로 싣는다. `all`·`any` 는 단락하지 않고 잎을 모두 평가한다.
- 값이 None 이면 그 조건은 거짓 + 사유(`insufficient`·`missing`·`halted`) — 참으로 처리하지 않는다.
  장중 수급이 없으면(`no_live_flow` — 사용자 결정 2026-10-13) '판단 불가'(ok=None)이고 3값 논리로
  묶는다. 부분 봉 칸의 가집계 행이 있어도 **그 지표의 구분(외국인·기관) 값이 비면** 판단 불가다(회차
  전이거나 그 구분의 순위 목록 밖 — 0 으로 세지 않는다). 결과가 판단 불가면 신호 없음
  (`state="unknown"` — 기록은 skipped/no_live_flow).
- 칸 자체를 평가하지 않는 경우(`Frame.skip_reason` — split_suspect·halted)는 `state="skipped"`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final, Literal

from kbj.core.quality import Quality
from kbj.engines.flows.ledger import Who
from kbj.engines.rules.dsl import INDICATORS, AllGroup, AnyGroup, Cond, Ref, Rule, conditions
from kbj.engines.rules.frame import Frame

__all__ = ["CondResult", "State", "Verdict", "evaluate", "uses_flows", "warmup"]

State = Literal["true", "false", "unknown", "skipped"]
_PREV_OPS: Final = frozenset({"cross_above", "cross_below", "turns_positive", "turns_negative"})


@dataclass(frozen=True)
class CondResult:
    path: str  # JSON 포인터(/when/all/0)
    cond: Cond
    ok: bool | None  # None = 판단 불가(no_live_flow)
    value: float | None
    ref_value: float | None  # ref × scale 또는 상수(between 이면 None)
    prev_value: float | None
    prev_ref: float | None
    reason: str | None  # 거짓·판단 불가의 사유(값이 없을 때)


@dataclass(frozen=True)
class Verdict:
    ok: bool  # 신호인가(state == "true")
    state: State
    index: int
    date: date
    conds: tuple[CondResult, ...]
    skipped_reason: str | None
    quality: Quality
    input_fp: str
    partial: bool
    live_flow: bool


def uses_flows(rule: Rule) -> bool:
    for _, c in conditions(rule.when):
        if INDICATORS[c.ind].flow or (c.ref is not None and INDICATORS[c.ref.ind].flow):
            return True
    return False


def _pint(p: dict[str, int | float | str], k: str, default: int) -> int:
    v = p.get(k, default)
    return int(v) if not isinstance(v, str) else default


def warmup(name: str, p: dict[str, int | float | str]) -> int:
    """지표가 처음 값을 내는 데 필요한 유효 칸 수(사유 insufficient 판정용)."""
    if name in ("sma", "ema"):
        return _pint(p, "n", 5)
    if name == "rsi":
        return _pint(p, "n", 14) + 1
    if name == "macd":
        return _pint(p, "slow", 26) + _pint(p, "signal", 9) - 1
    if name == "bb":
        return _pint(p, "n", 20)
    if name == "bb_width_rank":
        return _pint(p, "n", 20) + _pint(p, "lookback", 120) - 1
    if name == "adx":
        return 2 * _pint(p, "n", 14)
    if name == "atr":
        return _pint(p, "n", 14) + 1
    if name in ("vol_ratio", "high_n", "low_n"):
        return _pint(p, "n", 20) + 1
    if name == "change_pct":
        return 2
    if name in ("foreign_sum", "inst_sum"):
        return _pint(p, "n", 5)
    return 1


def _live_gap(frame: Frame, x: Cond | Ref, i: int) -> bool:
    """장중 부분 봉 칸의 수급 지표인데 그 구분의 가집계 값이 없다(행 없음·그 구분 칸 비어 있음)."""
    if not (INDICATORS[x.ind].flow and frame.partial and i == frame.last):
        return False
    r = frame.flow_rows[i]
    who: Who = "foreign" if x.ind.startswith("foreign") else "inst"
    return r is None or r.value(who) is None


def _series_at(frame: Frame, x: Cond | Ref, i: int) -> float | None:
    if i < 0 or _live_gap(frame, x, i):
        return None
    return frame.series(x.ind, x.p, x.field)[i]


def _why(frame: Frame, x: Cond | Ref, i: int) -> str:
    if _live_gap(frame, x, i):
        return "no_live_flow"
    if i >= 0 and frame.halted[i]:
        return "halted"
    valid = sum(1 for k in range(0, max(i, -1) + 1) if frame.close[k] is not None)
    if valid < warmup(x.ind, dict(x.p)):
        return "insufficient"
    return "missing"


def _leaf(frame: Frame, path: str, c: Cond, i: int) -> CondResult:
    a = _series_at(frame, c, i)
    need_prev = c.op in _PREV_OPS
    pa = _series_at(frame, c, i - 1) if need_prev else None
    b: float | None = None
    pb: float | None = None
    reason: str | None = None
    if c.ref is not None:
        rb = _series_at(frame, c.ref, i)
        b = None if rb is None else rb * c.ref.scale
        if need_prev:
            prb = _series_at(frame, c.ref, i - 1)
            pb = None if prb is None else prb * c.ref.scale
    elif isinstance(c.value, float | int):
        b = float(c.value)
        pb = b
    # 값이 없는 쪽의 사유(앞에서부터)
    missing: list[tuple[Cond | Ref, int]] = []
    if a is None:
        missing.append((c, i))
    if c.ref is not None and b is None:
        missing.append((c.ref, i))
    if need_prev and pa is None:
        missing.append((c, i - 1))
    if need_prev and c.ref is not None and pb is None:
        missing.append((c.ref, i - 1))
    if missing or a is None:
        reasons = [_why(frame, x, k) for x, k in missing]
        reason = "no_live_flow" if "no_live_flow" in reasons else reasons[0]
        ok: bool | None = None if reason == "no_live_flow" else False
        return CondResult(path, c, ok, a, b, pa, pb, reason)
    return _compare(path, c, a, b, pa, pb)


def _compare(
    path: str, c: Cond, a: float, b: float | None, pa: float | None, pb: float | None
) -> CondResult:
    op = c.op
    if op == "between":
        if not isinstance(c.value, tuple):  # pragma: no cover — dsl 이 막는다
            raise TypeError("between 은 [lo, hi]")
        lo, hi = c.value
        return CondResult(path, c, lo <= a <= hi, a, None, pa, None, None)
    if op in ("turns_positive", "turns_negative"):
        if pa is None:  # pragma: no cover — 위에서 사유로 걸렀다
            raise ValueError("전 칸 값이 없다")
        res = pa <= 0 < a if op == "turns_positive" else pa >= 0 > a
        return CondResult(path, c, res, a, None, pa, None, None)
    if b is None:  # pragma: no cover
        raise ValueError("비교 값이 없다")
    if op == "<":
        res = a < b
    elif op == "<=":
        res = a <= b
    elif op == ">":
        res = a > b
    elif op == ">=":
        res = a >= b
    elif op == "==":
        res = a == b
    else:
        if pa is None or pb is None:  # pragma: no cover
            raise ValueError("전 칸 값이 없다")
        res = (pa <= pb and a > b) if op == "cross_above" else (pa >= pb and a < b)
    return CondResult(path, c, res, a, b, pa, pb, None)


def _walk(
    frame: Frame, node: AllGroup | AnyGroup | Cond, i: int, path: str, out: list[CondResult]
) -> bool | None:
    if isinstance(node, Cond):
        r = _leaf(frame, path, node, i)
        out.append(r)
        return r.ok
    if isinstance(node, AllGroup):
        vals = [_walk(frame, k, i, f"{path}/all/{j}", out) for j, k in enumerate(node.all)]
        if any(v is False for v in vals):
            return False
        return None if any(v is None for v in vals) else True
    vals = [_walk(frame, k, i, f"{path}/any/{j}", out) for j, k in enumerate(node.any)]
    if any(v is True for v in vals):
        return True
    return None if any(v is None for v in vals) else False


def evaluate(rule: Rule, frame: Frame, i: int) -> Verdict:
    """칸 i 에서 규칙을 평가한다. 규칙은 `dsl.validate_rule`/`normalize` 를 지난 것(기본값이
    채워진)."""
    if not 0 <= i < frame.n:
        raise IndexError(f"칸 범위 밖: {i} (0~{frame.n - 1})")
    flows = uses_flows(rule)
    q = frame.quality_at(i, flows=flows)
    fp = frame.input_fp(i)
    skip = frame.skip_reason(i)
    if skip is not None:
        return Verdict(False, "skipped", i, frame.dates[i], (), skip, q, fp, frame.partial,
                       frame.live_flow)  # fmt: skip
    out: list[CondResult] = []
    res = _walk(frame, rule.when, i, "/when", out)
    state: State = "true" if res is True else "false" if res is False else "unknown"
    reason = "no_live_flow" if state == "unknown" else None
    return Verdict(res is True, state, i, frame.dates[i], tuple(out), reason, q, fp,
                   frame.partial, frame.live_flow)  # fmt: skip
