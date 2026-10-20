"""유동비율(FIF) 반올림·완충·FOL 조정(metrics §24.2).

규칙은 리뷰 날짜로 고른다(`IndexReviewsConfig.fif_rule_for`) — MSCI 새 규칙(2026-05 리뷰부터: > 25%
2.5% 단위, 5~25% 0.5%, < 5% 0.1%, 같은 폭 완충 — GIMI 2026-08판 App.VI·§3.1.7), 옛 규칙(> 15% 5%
단위 올림, 이하 1% 반올림). 띠·폭·완충은 설정 값. 계산은 십진(Decimal)으로 — 이진 소수 끝자리로 띠
경계를 잘못 넘지 않게.

- 완충: 기존 구성종목의 변화가 단위 **미만**(|raw − 직전| < 폭)이면 직전 값 유지, 이상이면 반영.
- 완충 예외(완충 없이 반영): 비구성종목(신규 편입·세그먼트 이동), FOL 이 묶어 생긴 FIF 변경.
- FOL: FIF = min(외국인 가용 유동비율(반올림), FOL(설정 단위 0.1% 반올림)) × foreign room 계수
  (`room_factor` — 신규·기존 표는 설정 `fol`).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Final

from kbj.config.index_reviews import FifBand, FifRule, FolCfg

__all__ = [
    "apply_fol", "band_of", "estimate_fif_raw", "resolve_fif", "room_factor", "round_fif",
    "round_fol",
]  # fmt: skip

_ONE = Decimal(1)


def _d(x: float) -> Decimal:
    return Decimal(str(x))


def band_of(raw: float, rule: FifRule) -> FifBand:
    """raw > above 인 첫 띠(경계값 자체는 아래 띠 — [확인 필요])."""
    r = _d(raw)
    for b in rule.bands:
        if r > _d(b.above):
            return b
    raise ValueError(f"유동비율은 0 보다 커야 한다: {raw!r}")


def round_fif(raw: float, rule: FifRule, prev: float | None = None) -> tuple[float, str]:
    """(적용 유동비율, 어떻게) — 'buffer'(직전 값 유지) · 'rounded'.

    완충: 직전 값이 있고 그 띠에 완충 폭이 있으며 |raw − 직전| < 폭이면 직전 값을 그대로 쓴다(변화가
    폭과 같거나 크면 반영 — GIMI §3.1.7). 비구성종목은 호출자가 prev 를 넘기지 않는다(완충 예외)."""
    if not 0 < raw <= 1:
        raise ValueError(f"유동비율은 0 < raw ≤ 1: {raw!r}")
    b = band_of(raw, rule)
    r = _d(raw)
    if prev is not None and b.buffer is not None:
        if not 0 < prev <= 1:
            raise ValueError(f"직전 유동비율은 0 < prev ≤ 1: {prev!r}")
        if abs(r - _d(prev)) < _d(b.buffer):
            return prev, "buffer"
    step = _d(b.step)
    mode = ROUND_HALF_UP if b.mode == "nearest" else ROUND_CEILING
    out = (r / step).to_integral_value(rounding=mode) * step
    if out > _ONE:
        out = _ONE
    if out <= 0:
        out = step  # 양수 유동비율은 가장 작은 단위 아래로 내리지 않는다
    return float(out), "rounded"


def round_fol(limit: float, step: float) -> float:
    """FOL 을 step(설정 — 0.1%) 단위로 반올림(반은 올림). 0 이하로 내리지 않는다."""
    if not 0 < limit <= 1:
        raise ValueError(f"외국인 한도는 0 < limit ≤ 1: {limit!r}")
    st = _d(step)
    out = (_d(limit) / st).to_integral_value(rounding=ROUND_HALF_UP) * st
    return float(max(out, st))


def apply_fol(fif: float, limit: float | None) -> tuple[float, bool]:
    """FOL 조정 — FIF = min(FIF, 외국인 한도(반올림한 값)). (값, 조정했나)."""
    if limit is None or fif <= limit:
        return fif, False
    return limit, True


def resolve_fif(
    raw: float | None,
    prev: float | None,
    rule: FifRule,
    fol_limit: float | None,
    *,
    member: bool = True,
    fol_step: float | None = None,
) -> tuple[float | None, str | None]:
    """종목의 적용 유동비율(foreign room 계수 전). raw 가 없으면 직전 값(목록의 FIF), 둘 다 없으면
    None(지어내지 않는다).

    완충은 기존 구성종목(member)에만 — 비구성종목은 완충 없이 반올림 값. FOL(fol_step 단위로
    반올림)이 반올림 값을 묶으면 FOL 로 인한 변경이라 완충 없이 FOL 값."""
    limit = None if fol_limit is None else (
        round_fol(fol_limit, fol_step) if fol_step is not None else fol_limit
    )  # fmt: skip
    if raw is None or raw <= 0:
        if prev is None:
            return None, None
        fif, how = prev, "prev"
    else:
        r = min(raw, 1.0)
        plain, _ = round_fif(r, rule)
        if limit is not None and limit < plain:  # FOL 이 묶는다 — 완충 예외
            return limit, "rounded+fol"
        fif, how = round_fif(r, rule, prev if member else None)
    fif, cut = apply_fol(fif, limit)
    return fif, (f"{how}+fol" if cut else how)


def room_factor(
    fol: FolCfg, limit: float | None, held: float | None, *, member: bool
) -> tuple[float, bool, str | None]:
    """foreign room 계수 — (계수, 편입 가능한가, 사유). 한도가 없으면 (1, 가능, None).

    room = (FOL − 외국인 보유) ÷ FOL(FOL 은 설정 단위로 반올림). 신규: room ≥ room_full → 1,
    room_min_add 이상 → room_add_factor, 미만 → 편입 불가. 기존: room < room_keep_floor →
    room_keep_floor_factor, 그 사이는 room_keep_mid_factor(null 이면 조정 안 함 — [확인 필요]).
    외국인 보유를 모르면 계수를 적용하지 않는다(사유)."""
    if limit is None:
        return 1.0, True, None
    if held is None:
        return 1.0, True, "외국인 보유 자료 없음 — foreign room 계수 미적용"
    if not 0 <= held <= 1:
        raise ValueError(f"외국인 보유 비율은 0~1: {held!r}")
    lim = _d(round_fol(limit, fol.val("limit_step")))
    room = max(Decimal(0), (lim - _d(held)) / lim)
    tag = f"외국인 여유 {float(room):.2%}"
    if room >= _d(fol.val("room_full")):
        return 1.0, True, None
    if not member:
        if room < _d(fol.val("room_min_add")):
            return 1.0, False, f"{tag} < {fol.val('room_min_add'):.0%} — 신규 편입 불가"
        f = fol.val("room_add_factor")
        return f, True, f"{tag} — 신규 조정계수 {f:g}"
    if room < _d(fol.val("room_keep_floor")):
        f = fol.val("room_keep_floor_factor")
        return f, True, f"{tag} — 기존 조정계수 {f:g}"
    mid = fol.room_keep_mid_factor.v
    if mid is None:
        return 1.0, True, f"{tag} — 기존 종목 중간 단계 계수 [확인 필요](조정 안 함)"
    return float(mid), True, f"{tag} — 기존 조정계수 {float(mid):g}"


_CORP_MARKS: Final = ("㈜", "(주)", "주식회사")
SAME_GROUP_TOL: Final = 0.03  # 5% 보고 보유가 최대주주 '계'의 ±3% 안이면 같은 지분(그룹 대표 보고)


def _holder_key(name: str) -> str:
    t = "".join(name.split())
    for m in _CORP_MARKS:
        t = t.replace(m, "")
    return t


def _in_group(reporter: str, shares: int, group: int | None, names: Sequence[str]) -> bool:
    key = _holder_key(reporter)
    if key and any(key == _holder_key(n) for n in names):
        return True
    return bool(group) and abs(shares - (group or 0)) <= SAME_GROUP_TOL * (group or 0)


def estimate_fif_raw(
    issued: int | None,
    treasury: int | None,
    holders: list[tuple[str, int]],
    financial_patterns: tuple[str, ...],
    *,
    group: int | None = None,
    group_names: Sequence[str] = (),
) -> float | None:
    """DART 지분 공시로 유동비율 추정(반올림 전) — 1 − (자기주식 + 전략적 보유) ÷ 발행주식.

    전략적 보유 = 최대주주 + 특수관계인 기말 보유(`group` — 정기보고서 최대주주 현황)
    + 그 밖의 5% 대량보유 보고자(`holders` = (보고자, 최근 보유) — 보고자별 마지막 값).
    5% 보고자가 최대주주 명단에 있거나 보유가 `group` 의 ±3% 안이면 이미 센 지분(그룹 대표
    보고)이라 더하지 않는다. 보고자 이름이 재무적 투자자 패턴(설정 — 연기금·운용사 등)에
    맞으면 빼지 않는다(유동으로 본다). 발행주식을 모르면 None. 비유동이 발행주식 이상이면
    None(자료가 서로 맞지 않는다 — 지어내지 않는다). 정의는 [확인 필요]."""
    if not issued or issued <= 0:
        return None
    locked = (treasury or 0) + max(group or 0, 0)
    for reporter, shares in holders:
        if any(p in reporter for p in financial_patterns):
            continue
        if _in_group(reporter, shares, group, group_names):
            continue
        locked += max(shares, 0)
    if locked >= issued:
        return None
    return (issued - locked) / issued
