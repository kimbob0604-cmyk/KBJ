"""입력 전처리 공통 — 옵션 가격 선택·제외 OI 비율·품질 합성 (docs/metrics.md §0·§1.1·§1.2).

- `select_price`: IV 역산에 쓸 가격. 매수·매도 호가가 모두 있고 `(ask − bid) ≤ 3 × tick(bid)` 면
  mid, 아니면 last, 둘 다 없으면 없음(그 종목 제외)
  - 틱은 **bid 의 옵션 호가단위**로 센다(`core.specs.ticks_between`) — 10pt 경계를 걸친 호가
    [확인 필요]. 9.98 / 10.05 는 0.01 틱으로 7틱이라 last
  - 0·None 은 값 없음(KIS 는 없음을 0 으로 준다). ask < bid(역전 호가)도 호가 없음으로 본다
    [확인 필요]
  - 가격은 `Decimal` 만(float 는 `TypeError` — `core.specs.as_decimal` 로 먼저 바꾼다). 음수·NaN·
    무한대는 §6.1 게이트가 먼저 걸러야 하므로 `ValueError`
  - 전 세션 가격: 고른 가격이 last 인데 당일 누적 거래량(KIS `acml_vol`)이 0 이면 그 last 는 전
    세션 값이다 — `PriceChoice.prev_session`. 거래량을 모르면(None) 세우지 않는다(지금 동작).
    쓰는 쪽 규칙(§1.3 F 에서 뺌, 종목 품질 estimated 이상)은 `core.forward`·`core.gex` 몫
    [확인 필요]. mid 는 살아 있는 호가라 거래량과 무관하다
- 프리미엄 하한(§1.2)은 `core.iv.MIN_PREMIUM` 하나를 쓰고, 판정은 `IvResult.below_min_premium`
  으로 한다 — 여기서 따로 비교하지 않는다
- `excluded_oi_ratio`: GEX 에서 빠진 OI / 전체 OI, 전체 0 이면 0
- `worst`: §0 품질 합성 — invalid > estimated > stale > ok
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
from typing import Literal, cast

from core.specs import Product, ticks_between

Quality = Literal["ok", "stale", "estimated", "invalid"]
PriceKind = Literal["mid", "last"]

MAX_SPREAD_TICKS = 3  # metrics §1.1

_RANK: dict[Quality, int] = {"ok": 0, "stale": 1, "estimated": 2, "invalid": 3}
_PRECISE = Context(prec=60)  # 주변 문맥이 좁혀 둬도 mid 를 반올림 없이


def worst(*qualities: Quality) -> Quality:
    """가장 나쁜 품질(§0). 입력이 없으면 ok. 모르는 값은 `ValueError`."""
    result: Quality = "ok"
    for q in qualities:
        if q not in _RANK:
            raise ValueError(f"모르는 품질: {q!r}")
        if _RANK[q] > _RANK[result]:
            result = q
    return result


@dataclass(frozen=True, slots=True)
class PriceChoice:
    """§1.1 결과. 가격이 없으면 price·kind 둘 다 None — 그 종목은 IV·GEX 에서 빠진다.

    prev_session: last 인데 당일 거래량이 0 — 전 세션 가격이다(kind 가 last 일 때만 설 수 있다).
    """

    price: Decimal | None
    kind: PriceKind | None
    prev_session: bool = False

    def __post_init__(self) -> None:
        if (
            (self.price is None) != (self.kind is None)
            or self.kind not in (None, "mid", "last")
            or (self.prev_session and self.kind != "last")
        ):
            raise ValueError(f"PriceChoice 필드 조합이 맞지 않는다: {self}")


NO_PRICE = PriceChoice(None, None)


def _present(name: str, v: Decimal | None) -> Decimal | None:
    if v is None:
        return None
    if not isinstance(cast(object, v), Decimal):
        kind = type(v).__name__
        raise TypeError(f"{name} 는 Decimal 이어야 한다(float 는 as_decimal 로): {kind}")
    if not v.is_finite() or v < 0:
        raise ValueError(f"잘못된 {name}: {v}")
    return v if v > 0 else None


def _volume(v: int | None) -> int | None:
    if v is None:
        return None
    if isinstance(cast(object, v), bool) or not isinstance(cast(object, v), int):
        raise TypeError(f"volume 은 int 여야 한다: {type(v).__name__}")
    if v < 0:
        raise ValueError(f"음수 거래량: {v}")
    return v


def select_price(
    bid: Decimal | None,
    ask: Decimal | None,
    last: Decimal | None,
    volume: int | None = None,
) -> PriceChoice:
    """옵션 한 종목의 §1.1 가격. 단건 보강 행(`source=fill`)은 호가가 없어 bid·ask 에 None.

    volume: 당일 누적 거래량(KIS `acml_vol`, 모르면 None). last 를 골랐는데 0 이면
    `prev_session` 을 세운다.
    """
    b, a, lst = _present("bid", bid), _present("ask", ask), _present("last", last)
    vol = _volume(volume)
    if (
        b is not None
        and a is not None
        and a >= b
        and ticks_between(Product.KOSPI200_OPTIONS, b, a) <= MAX_SPREAD_TICKS
    ):
        with localcontext(_PRECISE):
            return PriceChoice((b + a) / 2, "mid")
    if lst is not None:
        return PriceChoice(lst, "last", prev_session=vol == 0)
    return NO_PRICE


def excluded_oi_ratio(excluded_oi: int, total_oi: int) -> float:
    """§1.2 `제외된 OI 합 / 전체 OI 합` (0~1). 전체가 0 이면 0."""
    if not 0 <= excluded_oi <= total_oi:
        raise ValueError(f"0 ≤ 제외 OI ≤ 전체 OI 여야 한다: {excluded_oi}, {total_oi}")
    return excluded_oi / total_oi if total_oi else 0.0
