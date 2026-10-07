"""GEX·순GEX·DEX (docs/metrics.md §1·§2, PLAN §2.4·§5.1·§5.2).

부호(naive 딜러 — 콜 롱·풋 숏)는 tests/test_sign_conventions.py 가 고정한다:
- `GEX_call(K) = +Γ·OI·m·F²·0.01`, `GEX_put(K) = −Γ·OI·m·F²·0.01` — 원 / 기초 1%, 표시 억원
- `DEX = Σ OI_c·Δ_c·m·F − Σ OI_p·Δ_p·m·F` — 원. 풋 Δ 가 음수라 딜러 풋 숏 델타는 양수
m 은 `core.specs` 옵션 승수(250,000원), F 는 그 만기의 합성선물(§1.3).

만기 하나(`evaluate_expiry`): 종목별 §1.1 가격 → 만기 §1.3 F·§1.4 T → §1.5 IV → §1.6 그릭스.
그릭스는 IV 의 sigma 가 있고 §1.2 제외(`IvResult.below_min_premium`)가 아닐 때만 계산하고 그
종목만 GEX·DEX 에 넣는다(core.iv 인계 규칙 — KIS 폴백 sigma 면 estimated 로 포함). 나머지 — F 없음·
가격 없음·§1.2 제외·IV invalid — 는 0 으로 두고 OI 를 `excluded_oi_ratio` 분자에 넣는다
[확인 필요].

KIS IV 폴백(§1.5)은 만기의 T_KIS(§1.7 `core.forward.kis_time_to_expiry`, `ExpiryEval.T_kis`)를
넘겨 σ_KIS·√(T_KIS / T) 로 자체 T 에 옮긴다(2026-09-28 사용자 결정 — 검증 수정 3). 그릭스·GEX 의
T 는 언제나 자체 T 다. 만기일이 지난 만기(now 의 KST 날짜 > 만기일 — §1.4 대로 상위에서 빠질
만기)는 KIS 관례 T 가 없어 T_KIS 가 None 이고 KIS σ 를 그대로 쓴다 [확인 필요].

§1.3 선물 교차 확인 기준가(`core.forward.futures_reference`)는 여기서 만든다 — 분기 월물의 같은
결제월 선물가, 없으면 근월물 선물가(s_ref) + 호출 쪽이 넘긴 확정 베이시스. 쓸 행사가가 2 개
미만이라 F 가 기준가로 대체되면(`futures_fallback`) 그 F 로 IV·그릭스를 내고 만기는 estimated 다.
다음 스냅샷용 베이시스는 호출 쪽이 `core.forward.confirm_basis` 로 갱신한다(ok F 만).

전 세션 가격(§1.1 — last 인데 당일 거래량 0, `PriceChoice.prev_session`) [확인 필요]: §1.3 F 의
C − P + K 에는 쓰지 않고(`PREV_SESSION_IN_FORWARD`), IV·그릭스·GEX 에는 쓰되 그 종목 품질
(`OptionEval.quality`)을 estimated 이상으로 둔다(사유 `prev_session_last`). 거래량을 모르면(None)
지금 동작 그대로.

범위 합산(`net_gex`·`dex`, §2.2): 범위는 all(전체)·nearest(가장 이른 만기일)·0dte(만기일 =
거래일). 품질은 범위 안 만기의 F 품질과 포함 종목 품질(IV 품질, 전 세션 가격 estimated) 중 가장
나쁜 것이고, excluded_oi_ratio > 10% 면 estimated 이상이다. F 없는 만기가 있으면 invalid(값은 F
있는 만기만의 합). OI 가 있는데 전부 제외됐으면(포함 OI 0) 값 None·invalid — 0 이 아니라 모른다.
만기 지난 만기는 호출 쪽이 뺀다(§1.4).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field

from core.calendar import KST, expiry_at
from core.forward import (
    FUTURES_REF_TOLERANCE,
    MAX_BASIS_CARRY_DAYS,
    MIN_FORWARD_STRIKES,
    MIN_PARITY_STRIKES,
    T_FLOOR_MINUTES,
    ForwardResult,
    futures_reference,
    kis_time_to_expiry,
    synthetic_forward,
    time_to_expiry,
)
from core.greeks import Greeks, greeks
from core.iv import MIN_PREMIUM, IvResult, implied_vol
from core.preprocess import PriceChoice, Quality, excluded_oi_ratio, select_price, worst
from core.specs import Product, as_decimal, spec

CallPut = Literal["C", "P"]
QuoteSource = Literal["board", "fill"]
Scope = Literal["all", "nearest", "0dte"]
ExcludeReason = Literal["no_forward", "no_price", "below_min_premium", "iv_invalid"]
QualityReason = Literal["prev_session_last"]

OPTION_MULTIPLIER = spec(Product.KOSPI200_OPTIONS).multiplier  # 원/pt
MAX_EXCLUDED_OI_RATIO = 0.10  # PLAN §5.2 — 넘으면 estimated
EOK = 100_000_000  # 억원
PREV_SESSION_IN_FORWARD = False  # §1.3 — 전 세션 가격 행사가를 F 에 쓰는가 [확인 필요]
_DIGITS = re.compile(r"[0-9]+")  # KIS 수량 문자열
_YMD = re.compile(r"[0-9]{8}")  # KIS 날짜 문자열 YYYYMMDD
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")  # model_dump_json 의 date 표기


# ── 입력 ──────────────────────────────────────────────────────────────────────


def _no_bool(v: object) -> object:
    if isinstance(v, bool):
        raise ValueError("bool 은 숫자가 아니다")
    return v


def _decimal_in(v: object) -> object:
    """빈 문자열 → None, float·int·str → `as_decimal`(float 는 최단 표기라 0.1 → 0.1)."""
    v = _no_bool(v)
    if isinstance(v, str) and not v.strip():
        return None
    if isinstance(v, Decimal | float | int | str):
        return as_decimal(v)
    return v


def _non_negative(v: Decimal | None) -> Decimal | None:
    if v is not None and v < 0:
        raise ValueError(f"음수 가격: {v}")
    return v


def _positive(v: Decimal) -> Decimal:
    if v <= 0:
        raise ValueError(f"행사가는 양수: {v}")
    return v


def _count_in(v: object) -> object:
    """KIS 수량 문자열(숫자만, `"0012"` 포함) → int. 그 밖(부호·소수점·공백·빈 문자열·bool·float)은
    그대로 넘겨 strict int 가 거른다."""
    if isinstance(v, str) and _DIGITS.fullmatch(v):
        return int(v)
    return v


def _volume_in(v: object) -> object:
    """KIS 거래량 문자열 — 빈 값은 모름(None), 숫자만이면 int(0 은 '거래 없음' 이라는 값)."""
    if isinstance(v, str) and not v.strip():
        return None
    return _count_in(v)


def _ymd_in(v: object) -> object:
    """KIS 날짜 문자열 `YYYYMMDD` 와 ISO 날짜 `YYYY-MM-DD` → date(없는 날짜는 ValueError).
    ISO 는 `model_dump_json` 이 쓰는 표기라 받는다 — JSON 왕복(`model_validate_json`).
    그 밖(정수·datetime·시각이 붙은 ISO 문자열)은 그대로 넘겨 strict date 가 거른다 —
    pydantic 기본은 숫자를 Unix 시각으로 읽는다."""
    if isinstance(v, str):
        if _YMD.fullmatch(v):
            return date(int(v[:4]), int(v[4:6]), int(v[6:]))
        if _ISO_DATE.fullmatch(v):
            return date.fromisoformat(v)
    return v


Price = Annotated[Decimal | None, BeforeValidator(_decimal_in), AfterValidator(_non_negative)]
Strike = Annotated[Decimal, BeforeValidator(_decimal_in), AfterValidator(_positive)]
KisIv = Annotated[float | None, BeforeValidator(_no_bool)]
Count = Annotated[int, BeforeValidator(_count_in), Field(ge=0, strict=True)]
Volume = Annotated[Annotated[int, Field(ge=0, strict=True)] | None, BeforeValidator(_volume_in)]
KisDate = Annotated[date, BeforeValidator(_ymd_in), Field(strict=True)]


class OptionQuote(BaseModel):
    """옵션 한 종목의 스냅샷 — 전광판(`board`) 또는 단건 현재가 보강(`fill`) 행.

    expiry 는 월물리스트 6자리 만기 코드(월물 YYYYMM, 위클리 YYMMWW), expiry_date 는 최종거래일
    (`futs_last_tr_date`) — `date`, KIS 문자열 `YYYYMMDD`, ISO 날짜 `YYYY-MM-DD`(`model_dump_json`
    표기 — JSON 왕복)만(정수·datetime·시각이 붙은 문자열은 거부 — 숫자를 Unix 시각으로 읽는
    pydantic 변환을 막는다). 가격 0·빈 값은 없음(§1.1). oi 는 KIS
    `hts_otst_stpl_qty` — int 또는 숫자만인 문자열(bool·float·음수는 거부). kis_iv_pct 는
    `hts_ints_vltl`(%) — IV 폴백용(§1.5, nan·0 은 core.iv 가 없음으로 본다). volume 은 당일 누적
    거래량 `acml_vol`(oi 와 같은 형식, 빈 값·None 은 모름) — last 를 골랐는데 0 이면 전 세션
    가격(§1.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    expiry: str = Field(pattern=r"^[0-9]{6}$")
    expiry_date: KisDate
    strike: Strike
    cp: CallPut
    bid: Price = None
    ask: Price = None
    last: Price = None
    oi: Count
    kis_iv_pct: KisIv = None
    volume: Volume = None
    source: QuoteSource = "board"

    def price(self) -> PriceChoice:
        """§1.1 가격. fill 행은 호가가 없어 last 만 본다(호가 필드가 와도 무시)."""
        if self.source == "fill":
            return select_price(None, None, self.last, self.volume)
        return select_price(self.bid, self.ask, self.last, self.volume)


# ── 종목·만기 평가 ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OptionEval:
    """종목 하나의 평가 결과.

    iv: F 나 가격이 없으면 None(역산 안 함). greeks: GEX·DEX 에 들어가는 종목만 — excluded 와
    반대다. reason: 제외 사유(no_forward·no_price·below_min_premium·iv_invalid), 포함이면 None.
    품질(`quality`)은 IV 품질에 전 세션 가격 규칙을 더한 것 — 제외 사유와 따로다.
    """

    quote: OptionQuote
    choice: PriceChoice
    iv: IvResult | None
    greeks: Greeks | None
    excluded: bool
    reason: ExcludeReason | None

    def __post_init__(self) -> None:
        if (
            self.excluded != (self.greeks is None)
            or self.excluded != (self.reason is not None)
            or (self.iv is None) != (self.reason in ("no_forward", "no_price"))
        ):
            raise ValueError(f"OptionEval 필드 조합이 맞지 않는다: {self}")

    @property
    def quality_reasons(self) -> tuple[QualityReason, ...]:
        """IV 품질 밖에서 종목 품질을 떨어뜨린 사유 — 전 세션 가격이면 `prev_session_last`."""
        return ("prev_session_last",) if self.choice.prev_session else ()

    @property
    def quality(self) -> Quality:
        """종목 품질 — IV 품질(IV 없으면 invalid), 전 세션 가격이면 estimated 이상 [확인 필요]."""
        base: Quality = "invalid" if self.iv is None else self.iv.quality
        return worst(base, "estimated") if self.quality_reasons else base


@dataclass(frozen=True, slots=True)
class ExpiryEval:
    """만기 하나의 평가 결과. options 는 행사가 오름차순, 같은 행사가는 콜 먼저.

    T: 자체 잔존기간(년, §1.4) — IV·그릭스·GEX 가 쓰는 T. T_kis: KIS 관례 T(년, §1.7) — KIS IV
    폴백 σ 를 T 로 옮기는 데만 쓴 값(만기일이 지났으면 None).
    """

    expiry: str
    expiry_date: date
    forward: ForwardResult
    T: float
    options: tuple[OptionEval, ...]
    T_kis: float | None = None

    @property
    def F(self) -> float | None:
        return self.forward.F

    @property
    def quality(self) -> Quality:
        """F 품질과 GEX 에 들어간 종목 품질(`OptionEval.quality` — IV 품질, 전 세션 가격 estimated)
        중 가장 나쁜 것(제외 비율 규칙은 범위 합산 몫)."""
        included: list[Quality] = [o.quality for o in self.options if not o.excluded]
        return worst(self.forward.quality, *included)


def _evaluate_option(
    q: OptionQuote,
    choice: PriceChoice,
    F: float | None,
    T: float,
    t_kis: float | None,
    r: float,
    min_premium: float,
) -> OptionEval:
    if F is None:
        return OptionEval(q, choice, None, None, True, "no_forward")
    if choice.price is None:
        return OptionEval(q, choice, None, None, True, "no_price")
    flag = "c" if q.cp == "C" else "p"
    K = float(q.strike)
    iv = implied_vol(
        float(choice.price),
        F,
        K,
        T,
        flag,
        r=r,
        kis_iv_pct=q.kis_iv_pct,
        min_premium=min_premium,
        t_kis=t_kis,
    )
    # core.iv 인계 규칙: sigma 유무보다 §1.2 제외 플래그를 먼저 본다
    if iv.below_min_premium:
        return OptionEval(q, choice, iv, None, True, "below_min_premium")
    if iv.sigma is None:
        return OptionEval(q, choice, iv, None, True, "iv_invalid")
    return OptionEval(q, choice, iv, greeks(flag, F, K, T, iv.sigma, r=r), False, None)


def evaluate_expiry(
    quotes: Iterable[OptionQuote],
    now: datetime,
    s_ref: Decimal | float,
    quarterly_futures_price: Decimal | float | None = None,
    *,
    forward_basis: Decimal | float | None = None,
    forward_basis_age: int = 0,
    max_basis_carry: int = MAX_BASIS_CARRY_DAYS,
    strikes: Iterable[Decimal] | None = None,
    r: float = 0.0,
    min_premium: float = MIN_PREMIUM,
    min_forward_strikes: int = MIN_FORWARD_STRIKES,
    min_parity_strikes: int = MIN_PARITY_STRIKES,
    futures_ref_tolerance: Decimal | float = FUTURES_REF_TOLERANCE,
    t_floor_minutes: float = T_FLOOR_MINUTES,
    prev_session_in_forward: bool = PREV_SESSION_IN_FORWARD,
) -> ExpiryEval:
    """만기 하나의 종목들을 §1.1~§1.6 으로 평가한다.

    s_ref: 선물 근월물 현재가(ATM 기준, §1.3 기준가의 근월물 선물가). quarterly_futures_price: 분기
    월물 만기일 때만 같은 결제월 선물가(`is_quarterly_monthly`가 아닌 만기에 넘기면 ValueError).
    forward_basis: 이 만기의 확정 베이시스(`core.forward.confirm_basis`) — 같은 결제월 선물가가
    없으면 §1.3 기준가 = s_ref + forward_basis, 둘 다 없으면 교차 확인을 건너뛴다(`no_futures_ref`)
    [확인 필요]. forward_basis_age: 그 베이시스의 나이(`core.forward.basis_age` — 같은 저녁
    야간은 0) — max_basis_carry(2) 를 넘으면 선물 대체·교차 확인에 쓰지 않는다(§1.3 이월 상한,
    2026-09-29 사용자 결정) [확인 필요]. min_parity_strikes·futures_ref_tolerance: §1.3 선물
    대체(2 개 미만)·기준가 허용(2.0pt) [확인 필요]. strikes: ATM 선정용 만기 전 행사가(마스터).
    prev_session_in_forward: 전 세션 가격 행사가를 §1.3 F 에 쓸지(기본 안 씀) [확인 필요] —
    IV·그릭스에는 어느 쪽이든 쓴다.
    빈 입력, 만기가 섞인 입력, 같은 (행사가, 콜/풋) 중복은 ValueError.
    """
    qs = sorted(quotes, key=lambda q: (q.strike, q.cp))
    if not qs:
        raise ValueError("만기의 종목이 비었다")
    expiry, expiry_date = qs[0].expiry, qs[0].expiry_date
    seen: set[tuple[Decimal, CallPut]] = set()
    for q in qs:
        if (q.expiry, q.expiry_date) != (expiry, expiry_date):
            raise ValueError(f"한 만기의 종목만 넘긴다: {expiry}/{expiry_date}, {q.expiry}")
        if (q.strike, q.cp) in seen:
            raise ValueError(f"같은 종목이 두 번: {q.strike} {q.cp}")
        seen.add((q.strike, q.cp))
    reference = futures_reference(
        expiry,
        expiry_date,
        s_ref,
        same_month_futures=quarterly_futures_price,
        basis=forward_basis,
        basis_age=forward_basis_age,
        max_basis_carry=max_basis_carry,
    )

    choices = [q.price() for q in qs]
    calls = {q.strike: c.price for q, c in zip(qs, choices, strict=True) if q.cp == "C"}
    puts = {q.strike: c.price for q, c in zip(qs, choices, strict=True) if q.cp == "P"}
    prev_strikes = (
        ()
        if prev_session_in_forward
        else {q.strike for q, c in zip(qs, choices, strict=True) if c.prev_session}
    )
    fwd = synthetic_forward(
        calls,
        puts,
        s_ref,
        strikes=strikes,
        reference=reference,
        min_strikes=min_forward_strikes,
        min_parity_strikes=min_parity_strikes,
        ref_tolerance=futures_ref_tolerance,
        prev_session_strikes=prev_strikes,
    )
    T = time_to_expiry(now, expiry_at(expiry_date), t_floor_minutes)
    t_kis = _kis_t(now, expiry_date)
    options = tuple(
        _evaluate_option(q, c, fwd.F, T, t_kis, r, min_premium)
        for q, c in zip(qs, choices, strict=True)
    )
    return ExpiryEval(expiry, expiry_date, fwd, T, options, t_kis)


def _kis_t(now: datetime, expiry_date: date) -> float | None:
    """§1.7 T_KIS — 만기일이 지난 만기(now 의 KST 날짜 > 만기일)는 KIS 관례 T 가 없어 None
    [확인 필요]. now 는 `time_to_expiry` 가 aware 인지 이미 봤다."""
    if expiry_date < now.astimezone(KST).date():
        return None
    return kis_time_to_expiry(now, expiry_date)


# ── 익스포저 ──────────────────────────────────────────────────────────────────


def _sign(cp: CallPut) -> float:
    if cp == "C":
        return 1.0
    if cp == "P":
        return -1.0
    raise ValueError(f"cp 는 'C' 또는 'P': {cp!r}")


def option_gex(
    cp: CallPut, gamma: float, oi: int, F: float, multiplier: int = OPTION_MULTIPLIER
) -> float:
    """§2.1 한 종목의 딜러 GEX(원 / 기초 1%) = ±Γ·OI·m·F²·0.01. 콜 +, 풋 −."""
    return _sign(cp) * gamma * oi * multiplier * F * F * 0.01


def option_dex(
    cp: CallPut, delta: float, oi: int, F: float, multiplier: int = OPTION_MULTIPLIER
) -> float:
    """§2.3 한 종목의 딜러 DEX(원) = ±OI·Δ·m·F. 콜 +, 풋 −(풋 Δ 가 음수라 결과는 양수)."""
    return _sign(cp) * delta * oi * multiplier * F


def to_eok(krw: float) -> float:
    """원 → 억원(표시용)."""
    return krw / EOK


@dataclass(frozen=True, slots=True)
class _Coverage:
    """범위 종목들의 §1.2 제외 비율과 §2.2 품질. none_included: OI 가 있는데 포함 OI 가 0."""

    ratio: float
    quality: Quality
    none_included: bool


def _coverage(chosen: tuple[ExpiryEval, ...], max_excluded_ratio: float) -> _Coverage:
    """§2.2 품질 — 만기 품질(F·포함 종목) 중 가장 나쁜 것, 제외 비율 > max 면 estimated 이상,
    OI 가 있는데 전부 제외면 invalid(값이 0 이 아니라 없다 — 덮은 OI 가 없다)."""
    options = [o for e in chosen for o in e.options]
    total = sum(o.quote.oi for o in options)
    excluded = sum(o.quote.oi for o in options if o.excluded)
    ratio = excluded_oi_ratio(excluded, total)
    qualities: list[Quality] = [e.quality for e in chosen]
    quality = worst(*qualities)
    if ratio > max_excluded_ratio:
        quality = worst(quality, "estimated")
    none_included = total > 0 and excluded == total
    if none_included:
        quality = "invalid"
    return _Coverage(ratio, quality, none_included)


@dataclass(frozen=True, slots=True)
class StrikeGex:
    """§2.1 행사가 하나(만기 하나)의 GEX, 원/1%. 제외 종목은 0."""

    strike: Decimal
    gex_call: float
    gex_put: float

    @property
    def gex(self) -> float:
        return self.gex_call + self.gex_put


@dataclass(frozen=True, slots=True)
class StrikeGexTable:
    """§2.1 행사가별 GEX 와 그 품질.

    rows: 행사가 오름차순(제외 종목은 0). quality·excluded_oi_ratio 는 같은 종목의 §2.2 합산과
    같다 — F 품질·포함 종목 품질 중 가장 나쁜 것, 제외 OI 비율 > 10% 면 estimated, F 없거나(rows
    비움) OI 가 전부 제외면 invalid.
    """

    rows: tuple[StrikeGex, ...]
    quality: Quality
    excluded_oi_ratio: float


def strike_gex(
    ev: ExpiryEval, *, max_excluded_ratio: float = MAX_EXCLUDED_OI_RATIO
) -> StrikeGexTable:
    """만기 하나의 행사가별 GEX(원/1%, 행사가 오름차순)와 품질. F 가 없으면 rows 는 빈 튜플."""
    cov = _coverage((ev,), max_excluded_ratio)
    F = ev.forward.F
    if F is None:
        return StrikeGexTable((), cov.quality, cov.ratio)
    rows: dict[Decimal, list[float]] = {}
    for o in ev.options:
        row = rows.setdefault(o.quote.strike, [0.0, 0.0])
        if o.greeks is not None:
            row[0 if o.quote.cp == "C" else 1] = option_gex(
                o.quote.cp, o.greeks.gamma, o.quote.oi, F
            )
    table = tuple(StrikeGex(k, c, p) for k, (c, p) in sorted(rows.items()))
    return StrikeGexTable(table, cov.quality, cov.ratio)


def select_scope(
    evals: Iterable[ExpiryEval], scope: Scope, trade_date: date | None = None
) -> tuple[ExpiryEval, ...]:
    """§2.2 범위의 만기들. nearest 는 가장 이른 만기일, 0dte 는 만기일 = trade_date(필수)."""
    evs = tuple(evals)
    codes = [e.expiry for e in evs]
    if len(set(codes)) != len(codes):
        raise ValueError(f"같은 만기가 두 번: {codes}")
    if scope == "all":
        return evs
    if scope == "nearest":
        first = min((e.expiry_date for e in evs), default=None)
        return tuple(e for e in evs if e.expiry_date == first)
    if scope == "0dte":
        if trade_date is None or isinstance(trade_date, datetime):
            raise ValueError(f"0dte 는 trade_date(date)가 필요하다: {trade_date!r}")
        return tuple(e for e in evs if e.expiry_date == trade_date)
    raise ValueError(f"scope 는 all·nearest·0dte: {scope!r}")


@dataclass(frozen=True, slots=True)
class Exposure:
    """범위 합산(§2.2·§2.3). value 는 원(GEX 는 원/1%) — 범위가 비었거나, F 있는 만기가 없거나,
    OI 가 있는데 전부 제외됐으면(포함 OI 0) None.

    범위가 비면(만기일이 아닌 날의 0dte 등) quality 는 ok — 값 없음은 해당 없음이지 품질 문제가
    아니다 [확인 필요]. 전부 제외면 invalid. expiries 는 범위에 든 만기 코드.
    """

    value: float | None
    quality: Quality
    excluded_oi_ratio: float
    expiries: tuple[str, ...]


def _aggregate(
    chosen: tuple[ExpiryEval, ...],
    term: Callable[[OptionEval, Greeks, float], float],
    max_excluded_ratio: float,
) -> Exposure:
    if not chosen:
        return Exposure(None, "ok", 0.0, ())
    cov = _coverage(chosen, max_excluded_ratio)
    ratio, quality = cov.ratio, cov.quality
    value: float | None = None
    if not cov.none_included and any(e.forward.F is not None for e in chosen):
        value = math.fsum(
            term(o, o.greeks, e.forward.F)
            for e in chosen
            if e.forward.F is not None
            for o in e.options
            if o.greeks is not None
        )
    return Exposure(value, quality, ratio, tuple(e.expiry for e in chosen))


def net_gex(
    evals: Iterable[ExpiryEval],
    scope: Scope = "all",
    trade_date: date | None = None,
    *,
    max_excluded_ratio: float = MAX_EXCLUDED_OI_RATIO,
) -> Exposure:
    """§2.2 순GEX(원/1%) — 범위 안 모든 행사가·만기의 GEX 합. 양수 롱감마, 음수 숏감마."""
    return _aggregate(
        select_scope(evals, scope, trade_date),
        lambda o, g, F: option_gex(o.quote.cp, g.gamma, o.quote.oi, F),
        max_excluded_ratio,
    )


def dex(
    evals: Iterable[ExpiryEval],
    scope: Scope = "all",
    trade_date: date | None = None,
    *,
    max_excluded_ratio: float = MAX_EXCLUDED_OI_RATIO,
) -> Exposure:
    """§2.3 딜러 DEX(원). 품질은 순GEX 와 같게 합성한다 [확인 필요]."""
    return _aggregate(
        select_scope(evals, scope, trade_date),
        lambda o, g, F: option_dex(o.quote.cp, g.delta, o.quote.oi, F),
        max_excluded_ratio,
    )
