"""유동비율(FIF) 반올림·완충·FOL 조정(metrics §24.2).

규칙은 리뷰 날짜로 고른다(`IndexReviewsConfig.fif_rule_for`) — MSCI 새 규칙(2026-05 리뷰부터: > 25%
2.5%
단위, 5~25% 0.5%, < 5% 0.1%, 같은 폭 완충), 옛 규칙(> 15% 5% 단위 올림, 이하 1% 반올림).
띠·폭·완충은
설정 값. 계산은 십진(Decimal)으로 — 이진 소수 끝자리로 띠 경계를 잘못 넘지 않게.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from kbj.config.index_reviews import FifBand, FifRule

__all__ = ["apply_fol", "band_of", "estimate_fif_raw", "resolve_fif", "round_fif"]

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

    완충: 직전 값이 있고 그 띠에 완충 폭이 있으며 |raw − 직전| ≤ 폭이면 직전 값을 그대로 쓴다."""
    if not 0 < raw <= 1:
        raise ValueError(f"유동비율은 0 < raw ≤ 1: {raw!r}")
    b = band_of(raw, rule)
    r = _d(raw)
    if prev is not None and b.buffer is not None:
        if not 0 < prev <= 1:
            raise ValueError(f"직전 유동비율은 0 < prev ≤ 1: {prev!r}")
        if abs(r - _d(prev)) <= _d(b.buffer):
            return prev, "buffer"
    step = _d(b.step)
    mode = ROUND_HALF_UP if b.mode == "nearest" else ROUND_CEILING
    out = (r / step).to_integral_value(rounding=mode) * step
    if out > _ONE:
        out = _ONE
    if out <= 0:
        out = step  # 양수 유동비율은 가장 작은 단위 아래로 내리지 않는다
    return float(out), "rounded"


def apply_fol(fif: float, limit: float | None) -> tuple[float, bool]:
    """FOL 조정 — FIF = min(FIF, 외국인 한도). (값, 조정했나). 여유분 조정은 [확인 필요]."""
    if limit is None or fif <= limit:
        return fif, False
    return limit, True


def resolve_fif(
    raw: float | None, prev: float | None, rule: FifRule, fol_limit: float | None
) -> tuple[float | None, str | None]:
    """종목의 적용 유동비율. raw 가 없으면 직전 값(목록의 FIF), 둘 다 없으면 None(지어내지
    않는다)."""
    if raw is None or raw <= 0:
        if prev is None:
            return None, None
        fif, how = prev, "prev"
    else:
        fif, how = round_fif(min(raw, 1.0), rule, prev)
    fif, cut = apply_fol(fif, fol_limit)
    return fif, (f"{how}+fol" if cut else how)


def estimate_fif_raw(
    issued: int | None,
    treasury: int | None,
    holders: list[tuple[str, int]],
    financial_patterns: tuple[str, ...],
) -> float | None:
    """DART 지분 공시로 유동비율 추정(반올림 전) — 1 − (자기주식 + 전략적 보유) ÷ 발행주식.

    holders = (보고자, 최근 보유 주식) — 5% 대량보유 보고의 보고자별 마지막 값. 보고자 이름이 재무적
    투자자 패턴(설정 — 연기금·운용사 등)에 맞으면 빼지 않는다(유동으로 본다). 발행주식을 모르면
    None.
    비유동이 발행주식 이상이면 None(자료가 서로 맞지 않는다 — 지어내지 않는다). 정의는 [확인
    필요]."""
    if not issued or issued <= 0:
        return None
    locked = treasury or 0
    for reporter, shares in holders:
        if any(p in reporter for p in financial_patterns):
            continue
        locked += max(shares, 0)
    if locked >= issued:
        return None
    return (issued - locked) / issued
