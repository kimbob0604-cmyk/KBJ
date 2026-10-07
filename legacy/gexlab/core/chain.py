"""옵션 체인 행사가 도우미 — ATM 선정·ATM±n 창·커버리지·거리순 (PLAN §4.3·§4.4, 설계안 §4·§5).

- ATM 기준가는 **선물 근월물 현재가**다. 전광판 `atm_cls_name`·마스터 ATM 구분 필드는 쓰지 않는다
  (probe_results #11·#11a — 전광판 ATM 표시는 선물가 기준 ATM 과 다르다)
- 참 ATM 을 고르려면 만기의 전 행사가(마스터)를 넘긴다. 전광판은 100행에서 잘려 ATM 구간이 빠질 수
  있다(#11) — 잘렸는지는 `covers()` 로 본다
- 행사가는 `Decimal`(float 는 `TypeError`), 기준가는 float 도 받는다(`as_decimal` — 최단 표기로
  바꿔 거리·동률을 정확히 잰다)
- 같은 값의 행사가(콜·풋 두 행 등)는 하나로 친다. 빈 입력, 0 이하·NaN 행사가, 0 이하 기준가는
  `ValueError` — KIS 는 값 없음을 "0" 으로 주므로 0 기준가로 가장 낮은 행사가를 ATM 삼지 않게 막는다
- 거리 `|K − ref|` 는 정밀 문맥(`abs_diff`)에서 잰다 — 주변 문맥(기본 28자리)의 반올림이 가짜 동률을
  만들어 낮은 행사가로 기울지 않게. 정확히 못 잴 만큼 자릿수가 많은 기준가는 `ValueError`
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import cast

from core.specs import abs_diff, as_decimal


def _strikes(strikes: Iterable[Decimal]) -> list[Decimal]:
    """검증 후 중복 없이 오름차순."""
    vals = list(strikes)
    for k in vals:
        if not isinstance(cast(object, k), Decimal):
            raise TypeError(f"행사가는 Decimal 이어야 한다: {type(k).__name__}")
        if not k.is_finite() or k <= 0:
            raise ValueError(f"잘못된 행사가: {k}")
    if not vals:
        raise ValueError("행사가가 비었다")
    return sorted(set(vals))


def _ref(ref_price: Decimal | float) -> Decimal:
    r = as_decimal(ref_price)
    if r <= 0:
        raise ValueError(f"기준가는 양수여야 한다: {ref_price!r}")
    return r


def _check_n(n: int) -> None:
    if isinstance(cast(object, n), bool) or not isinstance(cast(object, n), int) or n < 0:
        raise ValueError(f"n 은 0 이상 정수: {n!r}")


def _atm_index(ks: list[Decimal], ref: Decimal) -> int:
    # ks 오름차순 — min 은 동률 중 첫 번째(낮은 행사가)를 준다
    dist = [abs_diff(k, ref) for k in ks]
    return min(range(len(ks)), key=dist.__getitem__)


def atm_strike(strikes: Iterable[Decimal], ref_price: Decimal | float) -> Decimal:
    """`ref_price` 에 가장 가까운 행사가. 동률(정확히 가운데)이면 낮은 행사가."""
    ks = _strikes(strikes)
    return ks[_atm_index(ks, _ref(ref_price))]


def atm_window(strikes: Iterable[Decimal], ref_price: Decimal | float, n: int) -> list[Decimal]:
    """ATM 과 양쪽 n 개씩(오름차순). 끝에 닿으면 있는 만큼만 — 반대쪽으로 채우지 않는다."""
    _check_n(n)
    ks = _strikes(strikes)
    i = _atm_index(ks, _ref(ref_price))
    return ks[max(0, i - n) : i + n + 1]


def covers(strikes: Iterable[Decimal], ref_price: Decimal | float, n: int = 0) -> bool:
    """받은 행사가 집합이 참 ATM±n 을 다 담는가 — 전광판 잘림 검사(설계안 §5).

    집합이 행사가 격자의 이어진 구간이라고 본다(전광판 100행은 연속 구간). 기준가가 집합 범위 안이면
    바깥 행사가는 범위 끝보다 멀어 집합 안 최근접이 참 ATM 이고, 그 양쪽에 n 개씩 있으면 True.
    기준가가 범위 밖이면 바깥 행사가 간격을 모르므로 False(보수적 — 경고가 한 번 더 나는 쪽).
    """
    _check_n(n)
    ks = _strikes(strikes)
    r = _ref(ref_price)
    if not ks[0] <= r <= ks[-1]:
        return False
    i = _atm_index(ks, r)
    return i - n >= 0 and i + n < len(ks)


def by_distance(strikes: Iterable[Decimal], ref_price: Decimal | float) -> list[Decimal]:
    """`|K − ref|` 오름차순, 같으면 낮은 행사가 먼저 — 보강 2 순환 순서(설계안 §5)."""
    ks = _strikes(strikes)
    r = _ref(ref_price)
    return sorted(ks, key=lambda k: (abs_diff(k, r), k))
