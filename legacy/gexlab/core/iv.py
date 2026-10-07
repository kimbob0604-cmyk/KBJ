"""IV 역산과 폴백 (docs/metrics.md §1.2·§1.5·§1.7, PLAN §5.1·§6.1).

1순위는 자체 Black-76 역산 — vollib.black.implied_volatility(Let's Be Rational) → `ok`.
역산할 수 없으면 KIS `hts_ints_vltl`(% 단위, /100)로 폴백해 `estimated`, 그것도 못 쓰면
`invalid`. 어느 쪽이든 IV ≤ 0 또는 > 300% 는 §6.1 이상치라 쓰지 않는다.

KIS IV 폴백의 T 환산 (2026-09-28 사용자 결정 — 검증 수정 3): KIS IV 는 KIS T(§1.7
`core.forward.kis_time_to_expiry` — 달력일/365, 만기일 0.5일)로 역산된 값이라 자체 T(§1.4 달력 분)에
그대로 쓰면 0DTE 감마가 사실상 0 이 된다(validation_greeks §5.3). 그래서 총분산을 보존해
`σ = σ_KIS·√(T_KIS / T)` 로 옮긴다(`rescale_sigma`). §6.1 이상치 판정은 옮긴 뒤 σ(실제로 쓰는
값)로 한다 [확인 필요]. 폴백·환산 여부는 `IvResult.source`(`kis`)·`rescaled`·`t_kis` 에 남는다 —
옮긴 σ 가 이상치라 `invalid` 가 된 행에도 `rescaled`·`t_kis` 는 남고, 사유가 옮긴 탓
(`kis_out_of_range_rescaled`)인지 KIS 값 자체 탓(`kis_out_of_range`)인지 가른다.
t_kis 를 안 주면 옮기지 않고 KIS σ 그대로다(`t_kis` None) — `core.gex` 는 만기일이 지난 만기 말고는
늘 준다.

역산하지 않은 이유(`IvResult.reason`, metrics §1.5 사유 코드) — 아래는 KIS 폴백 대상:
- invalid_price: 가격이 nan·inf
- below_min_premium: 가격 < 프리미엄 하한(기본 0.02pt, §1.2)
- below_intrinsic: 가격 ≤ 할인 내재가치 — 같음 포함 [확인 필요]
- above_max: 가격 ≥ 무차익 상한(콜 e^(−rT)·F, 풋 e^(−rT)·K) [확인 필요]
- model_error: vollib 예외 또는 nan 결과(수렴 실패)
폴백도 못 쓰면 뒤에 사유를 하나 더 붙인다:
- kis_missing: KIS 값 없음·0·nan
- kis_out_of_range: KIS 값 자체가 음수·inf·> 300% — 옮겼어도 여전히 > 300%(옮긴 σ 가 300% 안이면
  쓴다)
- kis_out_of_range_rescaled: KIS 값은 0 < σ ≤ 300% 인데 T 환산으로 > 300%(또는 0 으로 넘침)가 됐다

역산 결과가 ≤ 0·> 300% 면 `model_out_of_range` 로 KIS 폴백 없이 바로 `invalid` 다(§1.5 품질).

below_min_premium 이면 `IvResult.below_min_premium` 이 KIS 폴백 여부와 무관하게 선다 — 그릭스·GEX
는 sigma 유무가 아니라 이 플래그로 그 종목을 뺀다(§1.2).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, cast

import vollib.black.implied_volatility as _vbi  # pyright: ignore[reportMissingTypeStubs]

from core import black76
from core.black76 import Flag, check_inputs

# vollib 은 타입이 없다 — 쓰는 함수에만 시그니처를 붙인다.
# implied_volatility(discounted_price, F, K, r, t, flag) — r 이 t 앞이다
_vl_implied_volatility = cast(
    Callable[[float, float, float, float, float, str], float],
    _vbi.implied_volatility,  # pyright: ignore[reportUnknownMemberType]
)

MIN_PREMIUM = 0.02  # pt, metrics §1.2
IV_MAX = 3.0  # 300%, PLAN §6.1 이상치 상한
_MODEL_OUT_OF_RANGE = "model_out_of_range"
_BELOW_MIN_PREMIUM = "below_min_premium"
_KIS_MISSING = "kis_missing"
_KIS_OUT_OF_RANGE = "kis_out_of_range"  # KIS 값 자체가 이상치(옮겼어도 여전히)
_KIS_OUT_OF_RANGE_RESCALED = "kis_out_of_range_rescaled"  # 범위 안 KIS 값이 T 환산으로 이상치
_KIS_RANGE_REASONS = frozenset({_KIS_OUT_OF_RANGE, _KIS_OUT_OF_RANGE_RESCALED})

IvQuality = Literal["ok", "estimated", "invalid"]
IvSource = Literal["model", "kis"]


_SOURCE_OF: dict[str, IvSource | None] = {"ok": "model", "estimated": "kis", "invalid": None}


def usable(sigma: float) -> bool:
    """§6.1 이상치가 아닌 IV: 0 < σ ≤ 300%."""
    return math.isfinite(sigma) and 0 < sigma <= IV_MAX


def rescale_sigma(sigma: float, t_from: float, t_to: float) -> float:
    """총분산 보존 σ 환산 `σ·√(t_from / t_to)` — t_from 으로 매긴 σ 를 t_to 에서 쓸 때.

    §1.5 KIS IV 폴백: t_from = T_KIS(§1.7), t_to = 자체 T(§1.4). σ²·T 가 같아 r = 0 Black 가격·델타·
    감마(σ√T 로만 정해진다)가 (σ_KIS, T_KIS) 로 낸 값과 같다 — 베가·세타는 T 에 따로 달려 다르다.
    t_from == t_to 면 σ 그대로. 셋 중 하나라도 유한한 양수가 아니면 ValueError. 결과는 넘쳐서
    inf·0 이 될 수 있다 — 쓰기 전에 `usable` 로 본다.
    """
    for name, v in (("sigma", sigma), ("t_from", t_from), ("t_to", t_to)):
        if not (math.isfinite(v) and v > 0):
            raise ValueError(f"{name} 는 유한한 양수여야 한다: {v!r}")
    if t_from == t_to:
        return sigma
    return sigma * math.sqrt(t_from / t_to)


@dataclass(frozen=True, slots=True)
class IvResult:
    """sigma 는 연율 소수(0.25 = 25%). invalid 면 sigma·source 가 None, ok 면 reason 이 None.

    below_min_premium: 가격 < 프리미엄 하한이라 §1.2 로 제외된 종목. KIS IV 가 있으면 sigma 가
    estimated 로 채워지지만(표시용) 자체 그릭스는 계산하지 않고 GEX 에서도 뺀다 — 호출 쪽은
    `sigma is not None` 만 보지 말고 이 플래그를 먼저 본다(metrics §1.2·§1.6·§2.1).

    KIS 폴백의 T 환산 기록(§1.5): t_kis 는 KIS σ 를 자체 T 로 옮길 때 쓴 T_KIS(년, §1.7).
    None 이면 옮기지 않았다(호출 쪽이 T_KIS 를 주지 않았거나 KIS 값이 없음·음수·inf).
    rescaled 는 옮기면서 σ 가 바뀌었는가 — t_kis 가 있는데 False 면 T_KIS 가 자체 T 와 같아
    그대로 둔 것이다. 둘 다 KIS 값을 옮긴 폴백에만 선다: 쓴 폴백(source `kis`)과, 옮긴 σ 가
    §6.1 이상치라 invalid 가 된 폴백(사유 `…/kis_out_of_range`·`…/kis_out_of_range_rescaled`).
    `…_rescaled` 면 rescaled 가 늘 True 다 — 옮긴 탓에 빠진 행이 기록으로 드러난다.
    """

    sigma: float | None
    quality: IvQuality
    source: IvSource | None
    reason: str | None
    below_min_premium: bool = False
    rescaled: bool = False
    t_kis: float | None = None

    def __post_init__(self) -> None:
        if (
            self.quality not in _SOURCE_OF
            or self.source != _SOURCE_OF[self.quality]
            or (self.sigma is None) != (self.quality == "invalid")
            or (self.reason is None) != (self.quality == "ok")
            or (self.sigma is not None and not usable(self.sigma))
            or self.below_min_premium
            != (self.reason is not None and self.reason.split("/")[0] == _BELOW_MIN_PREMIUM)
            or (self.rescaled and self.t_kis is None)
            or (
                self.t_kis is not None
                and (
                    not (self.source == "kis" or self._kis_reason() in _KIS_RANGE_REASONS)
                    or not (math.isfinite(self.t_kis) and self.t_kis > 0)
                )
            )
            or (self._kis_reason() == _KIS_OUT_OF_RANGE_RESCALED and not self.rescaled)
        ):
            raise ValueError(f"IvResult 필드 조합이 맞지 않는다: {self}")

    def _kis_reason(self) -> str | None:
        """invalid 폴백 사유의 KIS 쪽(`<사유>/<kis 사유>` 의 뒤). 없으면 None."""
        if self.reason is None or "/" not in self.reason:
            return None
        return self.reason.split("/", 1)[1]


def implied_vol(
    price: float,
    F: float,
    K: float,
    T: float,
    flag: Flag,
    r: float = 0.0,
    kis_iv_pct: float | None = None,
    min_premium: float = MIN_PREMIUM,
    t_kis: float | None = None,
) -> IvResult:
    """옵션 한 종목의 IV. F·K·T 가 양수·유한이 아니거나 flag 가 틀리면 ValueError.

    가격(price)은 §1.1 로 고른 mid 또는 last, T 는 자체 T(§1.4), kis_iv_pct 는 같은 종목의 KIS
    `hts_ints_vltl`(%). t_kis 는 그 만기의 T_KIS(년, §1.7 `core.forward.kis_time_to_expiry`) —
    KIS 로 폴백하면 σ = σ_KIS·√(t_kis / T) 로 옮기고 §6.1 이상치는 옮긴 σ 로 본다. 안 주면 KIS σ
    그대로(`IvResult.t_kis` None). 주었는데 유한한 양수가 아니면 ValueError. 역산이 되면 안 쓴다.
    """
    check_inputs(flag, F, K, T, r)
    if not (math.isfinite(min_premium) and min_premium >= 0):
        raise ValueError(f"min_premium 은 0 이상이어야 한다: {min_premium!r}")
    if t_kis is not None and not (math.isfinite(t_kis) and t_kis > 0):
        raise ValueError(f"t_kis 는 유한한 양수여야 한다: {t_kis!r}")

    sigma, reason = _invert(price, F, K, T, flag, r, min_premium)
    if sigma is not None:
        return IvResult(sigma=sigma, quality="ok", source="model", reason=None)
    if reason == _MODEL_OUT_OF_RANGE:
        # 역산은 됐는데 §6.1 이상치 — 가격·F 쪽 이상 신호라 KIS 값으로 덮지 않는다
        return IvResult(sigma=None, quality="invalid", source=None, reason=reason)

    excluded = reason == _BELOW_MIN_PREMIUM
    kis_sigma, kis_reason, moved = _from_kis(kis_iv_pct, T, t_kis)
    # 옮긴 기록 — KIS 값을 옮겼으면 쓰든(estimated) 이상치로 버리든(invalid) 남긴다(§1.5 기록)
    record_t_kis = t_kis if moved else None
    rescaled = moved and t_kis != T
    if kis_sigma is not None:
        return IvResult(
            kis_sigma,
            "estimated",
            "kis",
            reason,
            below_min_premium=excluded,
            rescaled=rescaled,
            t_kis=record_t_kis,
        )
    return IvResult(
        None,
        "invalid",
        None,
        f"{reason}/{kis_reason}",
        below_min_premium=excluded,
        rescaled=rescaled,
        t_kis=record_t_kis,
    )


def _invert(
    price: float, F: float, K: float, T: float, flag: Flag, r: float, min_premium: float
) -> tuple[float, None] | tuple[None, str]:
    if not math.isfinite(price):
        return None, "invalid_price"
    if price < min_premium:
        return None, _BELOW_MIN_PREMIUM
    if price <= black76.intrinsic(flag, F, K, T, r):
        return None, "below_intrinsic"
    if price >= black76.upper_bound(flag, F, K, T, r):
        return None, "above_max"
    try:
        sigma = float(_vl_implied_volatility(price, F, K, r, T, flag))
    except Exception:  # vollib 이 내는 예외는 무엇이든 역산 실패로 본다
        return None, "model_error"
    if math.isnan(sigma):  # 수렴 실패
        return None, "model_error"
    if not usable(sigma):
        return None, _MODEL_OUT_OF_RANGE
    return sigma, None


def _from_kis(
    kis_iv_pct: float | None, T: float, t_kis: float | None
) -> tuple[float, None, bool] | tuple[None, str, bool]:
    """(쓸 σ, None, 옮겼나) 또는 (None, KIS 사유, 옮겼나).

    옮겼나: KIS 값을 t_kis 로 rescale_sigma 에 넣었다(쓰든 이상치로 버리든).
    """
    # 0 은 값 없음과 같게 본다(metrics §1.5 "KIS 값도 없거나 0 이면 invalid")
    if kis_iv_pct is None or math.isnan(kis_iv_pct) or kis_iv_pct == 0:
        return None, _KIS_MISSING, False
    raw = kis_iv_pct / 100
    if not (math.isfinite(raw) and raw > 0):  # 음수·inf — 옮길 수 있는 σ 가 아니다
        return None, _KIS_OUT_OF_RANGE, False
    moved = t_kis is not None
    sigma = raw if t_kis is None else rescale_sigma(raw, t_kis, T)
    if usable(sigma):  # §6.1 이상치 — 옮긴 뒤(실제로 쓸) σ 로 본다 [확인 필요]
        return sigma, None, moved
    # 옮기기 전엔 범위 안이었다면 옮긴 탓이다 — 사유로 가른다
    return None, _KIS_OUT_OF_RANGE_RESCALED if usable(raw) else _KIS_OUT_OF_RANGE, moved
