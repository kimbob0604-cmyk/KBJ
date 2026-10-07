"""선물 지표 (docs/metrics.md §7, PLAN §5.6).

시장 베이시스는 자체 `선물가 − 지수`, 이론 베이시스는 `이론가 − 지수`. KIS 선물 필드는 그대로
표시한다 — KIS 베이시스(`basis` — 실측으로 이론 베이시스), 괴리율(`dprt`, %), 선물 OI 증감
(`otst_stpl_qty_icdc`, 계약), 체결강도(`tday_rltv`, %), 코스피200 지수(`kospi200_nmix`). 필드는
KIS 선물옵션 분봉 조회(`inquire-time-fuopchartprice`) output1 의 것이다(실측
`tests/fixtures/kis/minute_day.json`) — KIS 이름에서 `FuturesQuote` 로 옮기는 것은 호출 쪽(검증은
`data/kis/models.py`). core 는 KIS 필드 이름을 쓰지 않는다(tests/unit/test_gex.py 가 막는다).

- 교차검증(2026-09-30 실측 반영 — 아래 메모): `이론가 − 지수` 와 KIS 베이시스의 차이가 0.05pt 를
  넘으면 실패(`BASIS_CHECK_TOLERANCE` [확인 필요] — 0.05 정확히는 통과, Decimal 로 정확히). 호출
  쪽이 health — KIS 필드 뜻이 바뀌었거나 응답이 서로 맞지 않는다는 신호
- 이론 베이시스: KIS 이론가 − 지수. 자체 이론 베이시스는 금리·배당 추정이 필요해 계산하지 않는다
  (PLAN §2.4)
- 기본값 [확인 필요]: 값이 없으면(빈 필드) 그 값만 null·invalid(`field_missing`), 지수·선물가·
  이론가가 0 이하면 없는 것으로 본다(야간 응답에 전일 지수 `0.00` 실측), 교차검증은 이론가·지수·
  KIS 베이시스가 다 있을 때만(아니면 판정 없음 None)
- 실측 메모(2026-09-28 14:01·09-29 15:52·16:07 등 여섯 응답): KIS 베이시스는 `이론가 − 지수`(이론
  베이시스, 5.97·6.37·−0.55)와 같고 `선물가 − 지수`(0.60·7.95·−5.73)와 다르다 — 그래서 시장
  베이시스는 KIS 필드가 아니라 자체 계산으로 표시하고, 교차검증은 이론 베이시스와 한다
  (metrics §7, 2026-09-30) [확인 필요]
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import cast

from core.preprocess import Quality

BASIS_CHECK_TOLERANCE = Decimal("0.05")  # §7 교차검증 허용 (pt) [확인 필요]


def _dec(name: str, v: Decimal | None) -> None:
    if v is None:
        return
    if not isinstance(cast(object, v), Decimal):
        raise TypeError(f"{name} 는 Decimal 이어야 한다: {type(v).__name__}")
    if not v.is_finite():
        raise ValueError(f"{name} 는 유한해야 한다: {v}")


@dataclass(frozen=True, slots=True)
class FuturesQuote:
    """KIS 선물 시세 한 건(모르면 None). price: 선물 현재가, basis: KIS 베이시스, divergence:
    괴리율(%), oi_change: 미결제약정 증감(계약), strength: 체결강도(%), index: 코스피200 지수,
    theory_price: KIS 이론가."""

    code: str
    price: Decimal | None = None
    basis: Decimal | None = None
    divergence: Decimal | None = None
    oi_change: int | None = None
    strength: Decimal | None = None
    index: Decimal | None = None
    theory_price: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("종목코드가 비었다")
        for name in ("price", "basis", "divergence", "strength", "index", "theory_price"):
            _dec(name, getattr(self, name))
        oi = cast(object, self.oi_change)
        if oi is not None and (isinstance(oi, bool) or not isinstance(oi, int)):
            raise TypeError(f"oi_change 는 int 여야 한다: {type(oi).__name__}")


@dataclass(frozen=True, slots=True)
class FuturesValue:
    """표시 값 하나 — 없으면 None·invalid(`field_missing`)."""

    value: float | None
    quality: Quality
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FuturesMetrics:
    """§7 결과. market_basis = 자체 선물가 − 지수, theory_basis = 이론가 − 지수, kis_basis·
    divergence·oi_change·strength 는 KIS 값 그대로. self_basis = 선물가 − 지수(Decimal),
    basis_gap = 이론 베이시스 − KIS 베이시스, basis_check: |basis_gap| ≤ 허용이면 True(입력이
    모자라면 None)."""

    code: str
    market_basis: FuturesValue
    kis_basis: FuturesValue
    divergence: FuturesValue
    oi_change: FuturesValue
    strength: FuturesValue
    theory_basis: FuturesValue
    price: Decimal | None
    index: Decimal | None
    self_basis: Decimal | None
    basis_gap: Decimal | None
    basis_check: bool | None


def _positive(v: Decimal | None) -> Decimal | None:
    return v if v is not None and v > 0 else None


def _shown(v: Decimal | int | None) -> FuturesValue:
    if v is None:
        return FuturesValue(None, "invalid", ("field_missing",))
    return FuturesValue(float(v), "ok")


def futures_metrics(
    q: FuturesQuote, *, tolerance: Decimal = BASIS_CHECK_TOLERANCE
) -> FuturesMetrics:
    """§7 — 시장·이론 베이시스, KIS 값 표시, 이론 베이시스 대 KIS basis 교차검증."""
    if not tolerance >= 0:
        raise ValueError(f"tolerance 는 0 이상: {tolerance}")
    price, index, theory = _positive(q.price), _positive(q.index), _positive(q.theory_price)
    theory_basis = None if theory is None or index is None else theory - index
    self_basis = None if price is None or index is None else price - index
    gap = None if theory_basis is None or q.basis is None else theory_basis - q.basis
    return FuturesMetrics(
        code=q.code,
        market_basis=_shown(self_basis),
        kis_basis=_shown(q.basis),
        divergence=_shown(q.divergence),
        oi_change=_shown(q.oi_change),
        strength=_shown(q.strength),
        theory_basis=_shown(theory_basis),
        price=price,
        index=index,
        self_basis=self_basis,
        basis_gap=gap,
        basis_check=None if gap is None else abs(gap) <= tolerance,
    )
