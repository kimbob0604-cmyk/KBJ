"""합성선물 F·잔존기간 T (docs/metrics.md §1.3·§1.4, PLAN §2.3·§5.1·§6.1).

F (§1.3): 기준가 S_ref(선물 근월물 현재가)에 가장 가까운 행사가가 ATM(`core.chain`, 동률이면 낮은
행사가). ATM±2(최대 5개) 행사가 중 콜·풋 가격(§1.1)이 모두 있는 행사가마다 `C − P + K`, 그 중앙값
(짝수 개면 가운데 둘의 평균). Decimal 로 계산하고 F 만 float 로 돌려준다(Black-76 입력).
- ATM 은 만기의 전 행사가(마스터)로 고른다 — 전광판은 잘릴 수 있어 `strikes` 로 넘긴다(#11).
  안 넘기면 가격 행사가의 합집합
- 콜·풋 중 하나라도 전 세션 가격(§1.1 `PriceChoice.prev_session`)인 행사가는 `prev_session_strikes`
  로 넘겨 쓰지 않는다 [확인 필요] — 뺀 행사가는 `ForwardResult.prev_session_skipped`. 창을 넓혀
  채우지 않는다(ATM±2 그대로). 아래 행사가 수는 모두 뺀 뒤의 수다
- 3 개 미만 → `estimated`(`few_strikes`) [확인 필요] — `min_strikes`

선물 교차 확인 (2026-09-28 사용자 결정 — 검증 수정 2). 기준가 `FuturesRef` 는 `futures_reference`:
① 같은 결제월 선물가 — 분기 월물(`is_quarterly_monthly`, 결제월은 `core.specs`)만 ② 없으면 근월물
선물가 + 확정 베이시스(그 만기의 마지막 품질 ok F − 그때 근월물 선물가, `confirm_basis` — 호출
쪽이 들고 있다 넘긴다) ③ 둘 다 없으면 None — 확인을 건너뛰고 품질 그대로, 기록 코드
`no_futures_ref`(`ForwardResult.notes`) [확인 필요].
- |F − 기준가| > 2.0pt → `estimated`(`futures_ref_gap`, PLAN §6.1) [확인 필요] — `ref_tolerance`
- 같은 결제월 선물가면 PLAN §5.1 규칙도: |F − 선물가| > 0.5pt → `estimated`(`futures_gap`,
  0.5 정확히는 ok). 0.5 가 더 엄격해 2pt 를 넘으면 두 사유가 함께 선다. 근월물 + 베이시스
  기준가에는 0.5 규칙을 쓰지 않는다
- 쓸 행사가 2 개 미만(`min_parity_strikes`) [확인 필요]: 기준가가 있으면 F = 기준가, `estimated`
  (`futures_fallback` — strikes 는 비우고 비교하지 않는다). 없으면 전과 같다 — 1 개면 그
  행사가로 `few_strikes`, 0 개면 F 없음 `invalid`(그 만기 전 지표 invalid)

베이시스 이월 상한 (2026-09-29 사용자 결정): 확정 베이시스는 확정한 거래일에서 최대
`MAX_BASIS_CARRY_DAYS`(2) 거래일까지만 이어 쓴다 [확인 필요] — 나이는 호출 쪽이 `basis_age` 로
세어 `futures_reference(basis_age=…)` 로 넘긴다. 주간과 바로 이어지는 야간은 한 단위라 이월이 아니다
(월 주간 확정 → 월 야간 0 · 화 주간 1 · 수 주간 2 · 목 주간 3 만료).
- 넘으면(`FuturesRef.expired`) 선물 대체 F 를 쓰지 않는다 — 쓸 행사가 2 개 미만이면 F 없음
  `invalid`(`basis_carry_expired`), 2 개 이상이면 F 는 내되 교차 확인은 건너뛴다(기록 코드
  `basis_carry_expired`)
- 이어 받은 베이시스(나이 1 이상)로 만든 선물 대체 F 는 사유에 `basis_carried` 를 더한다. 교차
  확인에만 쓴 경우는 F 가 그 베이시스를 쓰지 않았으니 품질 사유가 아니라 기록 코드(`notes`)
  [확인 필요]

S_ref 출처 (2026-09-29 사용자 결정, `select_s_ref`): 주간 = 선물 전광판(`F`, board), 야간 = 단건
`CM` 시세(single) — 전광판 `F` 는 야간에 주간 종가로 고정된다(#19 분기 B). 다른 출처로 대체하지
않는다(야간에 주간 전광판 행을 쓰지 않는다). 90초 넘은 시세는 `stale`(PLAN §6.1).

T (§1.4): 달력 분(야간·주말 포함) `max(남은 분, 5) / (365×24×60)`. 만기 시각은
`core.calendar.expiry_at`(만기일 15:20 KST). 만기 뒤(음수)도 하한 5분.

T_KIS (§1.7 KIS 관례, `kis_time_to_expiry`): `max(D, 0.5) / 365`, D = 만기일 − 오늘(KST 달력일).
시각을 보지 않아 만기일은 늘 0.5일이다(2026-09-28 실측). §1.5 KIS IV 폴백 σ 를 자체 T 로 옮길 때만
쓴다 — 그릭스·GEX 의 T 는 언제나 위 자체 T 다.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Context, Decimal, localcontext
from typing import Literal, cast

from core.calendar import KST, Session, TradingCalendar
from core.chain import atm_strike, atm_window
from core.preprocess import Quality
from core.specs import Product, as_decimal, spec

FORWARD_WINDOW = 2  # ATM±2
MIN_FORWARD_STRIKES = 3  # 미만이면 estimated [확인 필요]
MIN_PARITY_STRIKES = 2  # 미만이면 F = 선물 기준가(futures_fallback) [확인 필요]
MAX_BASIS_CARRY_DAYS = 2  # 확정 베이시스 이월 상한(거래일) — 2026-09-29 사용자 결정 [확인 필요]
FUTURES_GAP_TOLERANCE = Decimal("0.5")  # pt, PLAN §5.1 — 같은 결제월 선물가
FUTURES_REF_TOLERANCE = Decimal("2.0")  # pt, PLAN §6.1 — 선물 기준가 [확인 필요]
T_FLOOR_MINUTES = 5.0  # PLAN §2.3
MINUTES_PER_YEAR = 365 * 24 * 60
DAYS_PER_YEAR = 365
KIS_EXPIRY_DAY_DAYS = 0.5  # §1.7 — KIS 는 만기일(D = 0)을 시각과 무관하게 0.5일로 센다(실측)

FuturesRefKind = Literal["same_month", "near_basis"]
FuturesMarket = Literal["F", "CM"]
FuturesSource = Literal["board", "single"]

# 세션별 S_ref 출처 (시장구분, 원천) — 2026-09-29 사용자 결정. 야간 전광판 F 는 주간 종가 고정(#19)
S_REF_SOURCE: Mapping[Session, tuple[FuturesMarket, FuturesSource]] = {
    "day": ("F", "board"),
    "night": ("CM", "single"),
}
S_REF_STALE_AFTER_S = 90.0  # PLAN §6.1 — 90초 안 갱신이면 ok, 넘으면 stale
_SESSIONS: frozenset[str] = frozenset({"day", "night"})
_MARKETS: frozenset[str] = frozenset({"F", "CM"})
_SOURCES: frozenset[str] = frozenset({"board", "single"})
_QUALITIES: frozenset[str] = frozenset({"ok", "stale", "estimated", "invalid"})
_QUALITY_RANK: Mapping[str, int] = {"ok": 0, "stale": 1, "estimated": 2, "invalid": 3}

_REF_KINDS: frozenset[str] = frozenset({"same_month", "near_basis"})
_PRECISE = Context(prec=60)
_EXPIRY_CODE = re.compile(r"[0-9]{6}")


@dataclass(frozen=True, slots=True)
class FuturesRef:
    """§1.3 선물 교차 확인의 기준가(pt) — `futures_reference` 가 만든다.

    kind: `same_month` 같은 결제월 선물가(분기 월물만), `near_basis` 근월물 선물가 + 확정
    베이시스. basis: near_basis 일 때 더한 확정 베이시스(pt), same_month 면 None.
    carried_days: 베이시스를 확정한 거래일에서 지금 거래일까지의 거래일 수(0 = 같은 거래일,
    near_basis 만). expired: carried_days 가 이월 상한(`MAX_BASIS_CARRY_DAYS`)을 넘었다 — 선물
    대체·교차 확인에 쓰지 않는다.
    """

    price: Decimal
    kind: FuturesRefKind
    basis: Decimal | None = None
    carried_days: int = 0
    expired: bool = False

    def __post_init__(self) -> None:
        for name, v in (("price", self.price), ("basis", self.basis)):
            if v is not None and not isinstance(cast(object, v), Decimal):
                raise TypeError(f"FuturesRef.{name} 는 Decimal 이어야 한다: {type(v).__name__}")
        if isinstance(cast(object, self.carried_days), bool) or not isinstance(
            cast(object, self.carried_days), int
        ):
            raise TypeError(f"FuturesRef.carried_days 는 int: {self.carried_days!r}")
        if (
            not self.price.is_finite()
            or self.price <= 0
            or self.kind not in _REF_KINDS
            or (self.basis is None) != (self.kind == "same_month")
            or (self.basis is not None and not self.basis.is_finite())
            or self.carried_days < 0
            or (self.kind == "same_month" and (self.carried_days or self.expired))
            or (self.expired and self.carried_days == 0)
        ):
            raise ValueError(f"FuturesRef 필드 조합이 맞지 않는다: {self}")


@dataclass(frozen=True, slots=True)
class FuturesQuote:
    """선물 시세 한 행 — `fut_board`(poller `FuturesRecord`)에서 S_ref 고르기에 필요한 칸만.

    ts: 수신 시각(aware). session: 그 행의 세션 태그. market: `F`(전광판·주간 단건) · `CM`(야간
    단건). source: `board` 전광판 · `single` 단건 현재가. price: pt(KIS 값 없음이면 None).
    quality: 저장 품질.
    """

    ts: datetime
    session: Session
    market: FuturesMarket
    source: FuturesSource
    code: str
    price: Decimal | None
    quality: Quality

    def __post_init__(self) -> None:
        _aware("ts", self.ts)
        if self.price is not None and not isinstance(cast(object, self.price), Decimal):
            raise TypeError(
                f"FuturesQuote.price 는 Decimal 이어야 한다: {type(self.price).__name__}"
            )
        if (
            self.session not in _SESSIONS
            or self.market not in _MARKETS
            or self.source not in _SOURCES
            or self.quality not in _QUALITIES
            or not self.code
            or (self.price is not None and not (self.price.is_finite() and self.price > 0))
        ):
            raise ValueError(f"FuturesQuote 필드 조합이 맞지 않는다: {self}")


@dataclass(frozen=True, slots=True)
class SRef:
    """고른 S_ref. quality: 행 품질과 나이(90초) 판정 중 나쁜 쪽 — ok·stale·estimated."""

    price: Decimal
    quality: Quality
    quote: FuturesQuote


@dataclass(frozen=True, slots=True)
class ForwardResult:
    """§1.3 결과. F 는 pt — invalid 면 None.

    atm: 고른 ATM 행사가(행사가가 하나도 없으면 None). strikes: `C − P + K` 를 쓴 행사가(오름차순,
    futures_fallback 이면 비움). futures_gap: 기준가와 비교했으면 F − 기준가(pt), 아니면 None.
    reasons: 품질을 떨어뜨린 사유 코드 — no_strikes·no_parity_strikes·nonpositive_forward·
    basis_carry_expired(invalid), few_strikes·futures_gap·futures_ref_gap·futures_fallback·
    basis_carried(estimated — 선물 대체 F 가 이어 받은 베이시스를 썼다, futures_fallback 과 함께).
    prev_session_skipped: 창 안에서 콜·풋 가격이 다 있었지만 전 세션 가격이라 뺀 행사가(오름차순).
    reference: 선물 기준가(없으면 None). notes: 품질과 무관한 기록 코드 — no_futures_ref(기준가가
    없어 선물 교차 확인을 건너뜀), basis_carry_expired(베이시스 이월 상한을 넘어 건너뜀),
    basis_carried(이어 받은 베이시스 기준가로 교차 확인만 했다).
    """

    F: float | None
    quality: Quality
    atm: Decimal | None
    strikes: tuple[Decimal, ...]
    futures_gap: float | None
    reasons: tuple[str, ...]
    prev_session_skipped: tuple[Decimal, ...] = ()
    reference: FuturesRef | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        ref = self.reference
        fallback_ok = (
            ref is not None
            and not ref.expired
            and self.F == float(ref.price)
            and not self.strikes
            and self.futures_gap is None
        )
        expired = ref is not None and ref.expired
        carried_fallback = fallback_ok and ref is not None and ref.carried_days > 0
        if (
            (self.F is None) != (self.quality == "invalid")
            or (self.quality == "ok") != (not self.reasons)
            or (self.F is not None and not (math.isfinite(self.F) and self.F > 0))
            or not set(self.strikes).isdisjoint(self.prev_session_skipped)
            or (self.futures_gap is not None and ref is None)
            or ("no_futures_ref" in self.notes and ref is not None)
            or ("futures_fallback" in self.reasons and not fallback_ok)
            or (expired and self.futures_gap is not None)
            or (("basis_carry_expired" in self.notes) != expired)
            or ("basis_carry_expired" in self.reasons and not expired)
            or (
                "basis_carried" in self.reasons
                and not ("futures_fallback" in self.reasons and carried_fallback)
            )
        ):
            raise ValueError(f"ForwardResult 필드 조합이 맞지 않는다: {self}")


def _invalid(
    reason: str,
    atm: Decimal | None,
    skipped: tuple[Decimal, ...],
    reference: FuturesRef | None,
    notes: tuple[str, ...],
) -> ForwardResult:
    return ForwardResult(None, "invalid", atm, (), None, (reason,), skipped, reference, notes)


def _fallback(
    reference: FuturesRef, atm: Decimal | None, skipped: tuple[Decimal, ...]
) -> ForwardResult:
    """쓸 행사가가 모자라 F = 선물 기준가(`futures_fallback`) — 패리티 F 가 아니라 비교 안 함.

    이어 받은 베이시스(carried_days ≥ 1)면 사유에 `basis_carried` 를 더한다(2026-09-29 사용자 결정).
    """
    F = float(reference.price)
    reasons = (
        ("futures_fallback", "basis_carried") if reference.carried_days else ("futures_fallback",)
    )
    return ForwardResult(F, "estimated", atm, (), None, reasons, skipped, reference)


def _price(k: Decimal, v: Decimal | None) -> Decimal | None:
    if v is None:
        return None
    if not isinstance(cast(object, v), Decimal):
        raise TypeError(f"가격은 Decimal 이어야 한다({k}): {type(v).__name__}")
    if not v.is_finite() or v <= 0:
        raise ValueError(f"가격은 유한한 양수여야 한다({k}): {v}")
    return v


def _median(xs: list[Decimal]) -> Decimal:
    s = sorted(xs)
    n = len(s)
    with localcontext(_PRECISE):
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _tolerance(name: str, v: Decimal | float) -> Decimal:
    d = as_decimal(v)
    if d < 0:
        raise ValueError(f"{name} 는 0 이상: {v!r}")
    return d


def synthetic_forward(
    calls: Mapping[Decimal, Decimal | None],
    puts: Mapping[Decimal, Decimal | None],
    s_ref: Decimal | float,
    *,
    strikes: Iterable[Decimal] | None = None,
    reference: FuturesRef | None = None,
    window: int = FORWARD_WINDOW,
    min_strikes: int = MIN_FORWARD_STRIKES,
    min_parity_strikes: int = MIN_PARITY_STRIKES,
    tolerance: Decimal | float = FUTURES_GAP_TOLERANCE,
    ref_tolerance: Decimal | float = FUTURES_REF_TOLERANCE,
    prev_session_strikes: Iterable[Decimal] = (),
) -> ForwardResult:
    """한 만기의 합성선물 F. calls·puts 는 행사가 → §1.1 가격(없으면 None).

    reference: 선물 교차 확인 기준가(`futures_reference`, 없으면 확인 건너뜀 — `no_futures_ref`).
    tolerance: 같은 결제월 선물가 허용(0.5pt), ref_tolerance: 기준가 허용(2.0pt) [확인 필요].
    min_parity_strikes: 쓸 행사가가 이보다 적고 기준가가 있으면 F = 기준가 [확인 필요].
    prev_session_strikes: 콜·풋 중 하나라도 전 세션 가격인 행사가 — F 에 쓰지 않는다 [확인 필요].
    s_ref 가 0 이하이거나 행사가·가격이 Decimal 이 아니면 `core.chain` 과 같게 에러.
    """
    tol = _tolerance("tolerance", tolerance)
    ref_tol = _tolerance("ref_tolerance", ref_tolerance)
    if min_strikes < 1 or min_parity_strikes < 1:
        raise ValueError(
            f"min_strikes·min_parity_strikes 는 1 이상: {min_strikes!r}, {min_parity_strikes!r}"
        )
    if reference is not None and not isinstance(cast(object, reference), FuturesRef):
        raise TypeError(f"reference 는 FuturesRef 여야 한다: {type(reference).__name__}")
    prev_session = set(prev_session_strikes)
    for k in prev_session:
        if not isinstance(cast(object, k), Decimal):
            raise TypeError(f"전 세션 행사가는 Decimal 이어야 한다: {type(k).__name__}")
    # 이월 상한을 넘은 기준가는 선물 대체·교차 확인에 쓰지 않는다(기록만 남긴다)
    usable = reference if reference is not None and not reference.expired else None
    notes: tuple[str, ...]
    if reference is None:
        notes = ("no_futures_ref",)
    elif reference.expired:
        notes = ("basis_carry_expired",)
    else:
        notes = ("basis_carried",) if reference.carried_days else ()

    universe = [*calls, *puts] if strikes is None else list(strikes)
    if not universe:
        if usable is not None:
            return _fallback(usable, None, ())
        if reference is not None:  # 만료 — 대체하지 않고 invalid
            return _invalid("basis_carry_expired", None, (), reference, notes)
        return _invalid("no_strikes", None, (), None, notes)
    atm = atm_strike(universe, s_ref)
    used: list[Decimal] = []
    skipped: list[Decimal] = []
    parity: list[Decimal] = []
    for k in atm_window(universe, s_ref, window):
        c, p = _price(k, calls.get(k)), _price(k, puts.get(k))
        if c is None or p is None:
            continue
        if k in prev_session:
            skipped.append(k)
            continue
        with localcontext(_PRECISE):
            parity.append(c - p + k)
        used.append(k)
    if len(used) < min_parity_strikes and usable is not None:
        return _fallback(usable, atm, tuple(skipped))
    if len(used) < min_parity_strikes and reference is not None:  # 만료 — 대체하지 않고 invalid
        return _invalid("basis_carry_expired", atm, tuple(skipped), reference, notes)
    if not parity:
        return _invalid("no_parity_strikes", atm, tuple(skipped), reference, notes)
    med = _median(parity)
    if med <= 0:
        return _invalid("nonpositive_forward", atm, tuple(skipped), reference, notes)

    reasons: list[str] = []
    if len(used) < min_strikes:
        reasons.append("few_strikes")
    gap: Decimal | None = None
    if usable is not None:
        with localcontext(_PRECISE):
            gap = med - usable.price
        if usable.kind == "same_month" and abs(gap) > tol:
            reasons.append("futures_gap")
        if abs(gap) > ref_tol:
            reasons.append("futures_ref_gap")
    return ForwardResult(
        F=float(med),
        quality="estimated" if reasons else "ok",
        atm=atm,
        strikes=tuple(used),
        futures_gap=None if gap is None else float(gap),
        reasons=tuple(reasons),
        prev_session_skipped=tuple(skipped),
        reference=reference,
        notes=notes,
    )


def is_quarterly_monthly(expiry: str, expiry_date: date) -> bool:
    """분기 월물(선물 결제월 3·6·9·12월의 월물)인가 — 같은 결제월 선물가와 비교할 만기.

    월물 만기 코드는 만기일의 YYYYMM(`202612`), 위클리는 YYMMWW(`261001`)라(probe_results #12)
    코드가 만기일의 YYYYMM 과 같으면 월물이다. 위클리 코드는 월 자리에 연도가 와서 겹치지 않는다.
    """
    if not _EXPIRY_CODE.fullmatch(expiry):
        raise ValueError(f"만기 코드는 6자리 숫자: {expiry!r}")
    months = spec(Product.KOSPI200_FUTURES).settlement_months
    return expiry == f"{expiry_date:%Y%m}" and expiry_date.month in months


def _futures_price(name: str, v: Decimal | float) -> Decimal:
    d = as_decimal(v)
    if d <= 0:
        raise ValueError(f"{name} 는 양수여야 한다(KIS 값 없음 0 은 None 으로): {v!r}")
    return d


def futures_reference(
    expiry: str,
    expiry_date: date,
    near_futures: Decimal | float | None,
    *,
    same_month_futures: Decimal | float | None = None,
    basis: Decimal | float | None = None,
    basis_age: int = 0,
    max_basis_carry: int = MAX_BASIS_CARRY_DAYS,
) -> FuturesRef | None:
    """§1.3 선물 교차 확인 기준가. 못 만들면 None — 확인 건너뜀(`no_futures_ref`) [확인 필요].

    ① same_month_futures: 같은 결제월 선물가 — 분기 월물(`is_quarterly_monthly`)만(아닌 만기에
    넘기면 ValueError). 위클리는 분기월이라도 만기일이 선물 최종거래일과 달라 여기 넘기지 않는다
    [확인 필요] ② 없으면 near_futures(근월물 선물가 — `evaluate_expiry` 의 s_ref) + basis(그 만기의
    확정 베이시스, `confirm_basis`) — 하나라도 없으면 None. 선물가 0 이하, 기준가가 0 이하가 되는
    베이시스는 ValueError.
    basis_age: 그 베이시스를 확정한 세션에서 지금 세션까지의 나이(`basis_age`).
    max_basis_carry(2) 를 넘으면 `expired` 기준가 — 선물 대체·교차 확인에
    쓰지 않는다(2026-09-29 사용자 결정) [확인 필요]. 음수면 ValueError.
    """
    for name, v in (("basis_age", basis_age), ("max_basis_carry", max_basis_carry)):
        if isinstance(cast(object, v), bool) or not isinstance(cast(object, v), int) or v < 0:
            raise ValueError(f"{name} 는 0 이상의 int: {v!r}")
    quarterly = is_quarterly_monthly(expiry, expiry_date)
    if same_month_futures is not None:
        if not quarterly:
            raise ValueError(f"분기 월물이 아닌 만기에 같은 결제월 선물가를 넘겼다: {expiry}")
        return FuturesRef(_futures_price("same_month_futures", same_month_futures), "same_month")
    if near_futures is None or basis is None:
        return None
    near = _futures_price("near_futures", near_futures)
    b = as_decimal(basis)
    with localcontext(_PRECISE):
        price = near + b
    if price <= 0:
        raise ValueError(f"근월물 선물가 + 베이시스가 0 이하: {near} + {b}")
    return FuturesRef(price, "near_basis", b, basis_age, basis_age > max_basis_carry)


def basis_age(
    cal: TradingCalendar,
    confirmed_trade_date: date,
    confirmed_session: Session,
    trade_date: date,
    session: Session,
) -> int:
    """확정 베이시스의 나이(거래일) — `futures_reference(basis_age=…)` 에 넘길 값.

    세션을 `TradingCalendar.session_day` 로 옮겨(야간 → 그 밤이 이어지는 주간의 거래일) 그 사이
    거래일 수를 센다. 주간에 확정한 베이시스를 바로 이어지는 야간에 쓰면 0, 다음 주간부터 1
    (2026-09-29 사용자 결정). 야간에 확정하면 그 밤의 주간과 한 단위라 다음 주간이 1. 지금이 확정
    세션보다 앞이면 ValueError(거래일이 아닌 귀속 거래일도 ValueError).
    """
    start = cal.session_day(confirmed_trade_date, confirmed_session)
    end = cal.session_day(trade_date, session)
    return cal.trading_days_between(start, end)


def confirm_basis(
    forward: ForwardResult, near_futures: Decimal | float, previous: Decimal | None = None
) -> Decimal | None:
    """확정 베이시스 갱신 — F 품질이 ok 일 때만 `F − 근월물 선물가`(pt), 아니면 previous 그대로.

    호출 쪽이 만기별로 들고 있다가 다음 스냅샷의 `futures_reference(basis=…)`(`evaluate_expiry` 의
    forward_basis)로 넘긴다. near_futures 는 그 F 를 낸 스냅샷의 근월물 선물가(s_ref). 근월물이
    바뀌면(분기 만기일 15:20 뒤 차월물) 옛 근월물 기준이라 호출 쪽이 버린다.
    """
    if forward.quality != "ok" or forward.F is None:
        return previous
    near = _futures_price("near_futures", near_futures)
    with localcontext(_PRECISE):
        return as_decimal(forward.F) - near


def select_s_ref(
    quotes: Iterable[FuturesQuote],
    *,
    session: Session,
    code: str,
    now: datetime,
    stale_after_s: float = S_REF_STALE_AFTER_S,
) -> SRef | None:
    """세션에 맞는 출처(`S_REF_SOURCE`)의 근월물 `code` 시세 중 가장 최근 것 — 없으면 None.

    주간은 전광판 `F`, 야간은 단건 `CM` 만 본다. 야간에 전광판 `F` 행은 주간 종가로 고정돼 있어
    (#19) 더 최근이어도 쓰지 않고, 없다고 다른 출처로 대체하지 않는다(호출 쪽이 None 을 처리 —
    S_ref 가 없으면 그 사이클의 F 를 만들 수 없다). 세션 태그가 다른 행·값 없는 행·invalid 행·now
    보다 뒤의 행은 건너뛴다. 품질: 나이 > stale_after_s 면 stale, 행 품질이 더 나쁘면 그것.
    now 가 naive 면 ValueError.
    """
    at = _aware("now", now)
    if not (math.isfinite(stale_after_s) and stale_after_s > 0):
        raise ValueError(f"stale_after_s 는 유한한 양수: {stale_after_s!r}")
    market, source = S_REF_SOURCE[session]
    best: FuturesQuote | None = None
    for q in quotes:
        if (
            q.session != session
            or q.market != market
            or q.source != source
            or q.code != code
            or q.price is None
            or q.quality == "invalid"
            or q.ts > at
        ):
            continue
        if best is None or q.ts > best.ts:
            best = q
    if best is None or best.price is None:
        return None
    age_quality: Quality = "ok" if (at - best.ts).total_seconds() <= stale_after_s else "stale"
    worse = _QUALITY_RANK[best.quality] > _QUALITY_RANK[age_quality]
    return SRef(best.price, best.quality if worse else age_quality, best)


def _aware(name: str, ts: datetime) -> datetime:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"naive datetime 은 받지 않는다: {name}={ts!r}")
    return ts.astimezone(UTC)


def time_to_expiry(
    now: datetime, expiry: datetime, floor_minutes: float = T_FLOOR_MINUTES
) -> float:
    """§1.4 잔존기간(년). expiry 는 `core.calendar.expiry_at(만기일)`. 둘 다 aware 여야 한다."""
    if not (math.isfinite(floor_minutes) and floor_minutes > 0):
        raise ValueError(f"floor_minutes 는 유한한 양수: {floor_minutes!r}")
    # UTC 로 바꿔 뺀다 — 같은 tzinfo 끼리의 뺄셈은 벽시계 차라 서머타임이 있는 시간대에서 틀린다
    minutes = (_aware("expiry", expiry) - _aware("now", now)).total_seconds() / 60
    return max(minutes, floor_minutes) / MINUTES_PER_YEAR


def kis_time_to_expiry(now: datetime, expiry_date: date) -> float:
    """§1.7 KIS 잔존기간 관례(년) `max(D, 0.5) / 365` — D = 만기일 − now 의 KST 달력 날짜(일).

    §1.5 KIS IV 폴백 σ 를 자체 T 로 옮길 때의 T_KIS 다(GEX 의 T 가 아니다). 시각은 보지 않는다:
    만기일(D = 0)은 장중 어느 시각이든 0.5일. 주말·휴일도 센다(달력일), D = KIS
    `hts_rmnn_dynu` − 1. 야간 세션도 now 의 KST 달력 날짜로 센다(귀속 거래일 T+1 이 아니다 — 미실측)
    [확인 필요]. expiry_date 는 §1.4 와 같은 만기일(KIS `futs_last_tr_date`).
    now 가 naive 거나 만기일이 지났으면(D < 0) ValueError, expiry_date 가 datetime 이면 TypeError.
    """
    if isinstance(expiry_date, datetime):
        raise TypeError(f"expiry_date 는 시각 없는 date 여야 한다: {expiry_date!r}")
    today = _aware("now", now).astimezone(KST).date()
    days = (expiry_date - today).days
    if days < 0:
        raise ValueError(f"만기 지남: 만기일 {expiry_date} < 오늘 {today}")
    return max(days, KIS_EXPIRY_DAY_DAYS) / DAYS_PER_YEAR
