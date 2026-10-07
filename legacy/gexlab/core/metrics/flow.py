"""플로우 지표 (docs/metrics.md §6.1~§6.7, PLAN §5.5).

- HIRO-lite(§6.1): 옵션 체결 틱마다 `Δbuy = cum_buy − 직전 cum_buy`,
  `Δsell = cum_sell − 직전 cum_sell`, `signed_qty = Δbuy − Δsell`(매수 주도 +), 고객 델타 흐름
  `Σ signed_qty × Δ × m × F`(원), 딜러 헤지 수요 = 부호 반전. Δ 는 그 종목의 최근 자체 델타(체인
  평가 값), F 는 그 만기 F — 호출 쪽이 틱에 붙인다. 누적 수량이 줄면(역행) 그 틱은 버린다(직전
  누적은 그대로 — 호출 쪽이 health). 리셋(웹소켓 끊김·시퀀스 공백·세션 전환)은 누적 흐름과
  종목별 직전 누적을 모두 비우고 사유·시각을 남긴다(표시). 품질은 늘 estimated(PLAN). 기본값
  [확인 필요]: 종목의 첫 틱(리셋 뒤 포함)은 기준만 잡고 흐름에 넣지 않는다(직전 누적이 없다 — 세션
  누적 전체를 한 틱에 넣지 않게), 누적 필드가 없는 틱은 건너뛴다, Δ·F 를 모르는 틱은 기준은 옮기고
  그 수량을 `unpriced_qty` 로만 센다
- 딜러 가정 점검(§6.3): 거래일마다 증권(`scrt`) 계정의 콜 순매수 수량 합·풋 순매수 수량 합(월물·
  위클리 합산 — 호출 쪽이 조합마다 넘긴다). naive 가정(딜러 콜 롱·풋 숏)과 일치 = 콜 순매수 > 0
  그리고 풋 순매수 < 0. 불일치가 연속 5거래일(`DEALER_WARN_DAYS` [확인 필요])이면 경고. 기본값
  [확인 필요]: 모르는 조합(None)은 빼고 estimated(`pairs_missing`), 한쪽을 통째로 모르면 판정 없음
  (None)·invalid, 판정 없는 날은 연속을 끊는다(모르는 날을 불일치로 세지 않는다)
- 대량 체결(§6.4): 1틱 체결량 ≥ 최근 20거래일 같은 머니니스 구간의 1틱 체결량 p99. 구간 =
  `K/F − 1` 을 1% 단위로 자른 것(내림 — [n%, n+1%)), 콜·풋 따로 [확인 필요]. 기록이 20거래일
  미만이면 비활성. 기본값 [확인 필요]: p99 는 nearest-rank(오름차순 ⌈0.99·n⌉ 번째 — 관측된
  체결량), 창 = 기록이 있는 가장 최근 20거래일, 기록이 없는 구간은 판정하지 않는다(None)
- PCR(§6.5): OI 기준 `Σ OI_put / Σ OI_call`, 거래량 기준 `Σ 거래량_put / Σ 거래량_call` — 만기별·
  전체(호출 쪽이 종목을 모아 넘긴다). 분모 0 이면 null. 기본값 [확인 필요]: 값을 모르는 종목(OI·
  거래량 None)은 합에서 빼고 그 비율을 estimated(`oi_missing`·`volume_missing`) — 0 으로 세지
  않는다. 분모 0 의 null 은 품질 ok(해당 없음)
- 맥스페인(§6.6): 만기 하나의 상장 행사가 K 중
  `Σ_{K'} [OI_c(K')·max(K − K', 0) + OI_p(K')·max(K' − K, 0)]` 가 최소인 K. 동률이면 F 에 가까운
  쪽, 그다음 낮은 쪽. OI 가 전부 0 이면 null. 계산은 Decimal 로 정확히(동률이 부동소수 오차로
  갈리지 않게). 기본값 [확인 필요]: 후보는 넘긴 상장 행사가(마스터 — 없으면 OI 가 온 행사가), F 를
  모르는데 동률이면 낮은 행사가 + estimated(`tie_without_forward`)
- OI 증감(§6.7): 종목(행사가·콜풋) 하나의 스냅샷 시각별 `OI(t) − OI(t−1)`. 이상치: 한 스냅샷에서
  줄었다가 다음 스냅샷에 줄어든 양의 90% 이상(`OI_RECOVERY`, 경계 포함 — 넘친 복구도) 복구되면 두
  칸 모두 이상치(표시 제외 — 호출 쪽이 health). 증감이 0 인 스냅샷도 '다음 스냅샷'이다. 첫
  스냅샷은 증감 없음(None). `oi_step` 은 새 스냅샷 하나씩(engine), `oi_change_cells` 는 그 접기
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, cast

from core.chain import by_distance
from core.gex import OPTION_MULTIPLIER, CallPut
from core.preprocess import Quality

# ── PCR (§6.5) ────────────────────────────────────────────────────────────────


def _count(name: str, v: int | None) -> int | None:
    if v is None:
        return None
    if isinstance(cast(object, v), bool) or not isinstance(cast(object, v), int):
        raise TypeError(f"{name} 는 int 여야 한다: {type(v).__name__}")
    if v < 0:
        raise ValueError(f"음수 {name}: {v}")
    return v


def _cp(cp: str) -> CallPut:
    if cp == "C":
        return "C"
    if cp == "P":
        return "P"
    raise ValueError(f"cp 는 'C' 또는 'P': {cp!r}")


@dataclass(frozen=True, slots=True)
class Ratio:
    """PCR 한 칸 — value = put ÷ call(분모 0 이면 None). missing: 값을 몰라 뺀 종목 수."""

    value: float | None
    put: int
    call: int
    missing: int
    quality: Quality
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Pcr:
    """§6.5 결과 — OI 기준·거래량 기준."""

    oi: Ratio
    volume: Ratio


def _ratio(put: int, call: int, missing: int, reason: str) -> Ratio:
    value = None if call == 0 else put / call
    reasons = (reason,) if missing else ()
    return Ratio(value, put, call, missing, "estimated" if missing else "ok", reasons)


def pcr(legs: Iterable[tuple[str, int | None, int | None]]) -> Pcr:
    """§6.5 PCR. legs: 종목마다 (콜풋, OI, 당일 누적 거래량) — 모르면 None."""
    oi = {"C": 0, "P": 0}
    vol = {"C": 0, "P": 0}
    oi_missing = vol_missing = 0
    for cp, o, v in legs:
        side = _cp(cp)
        o, v = _count("oi", o), _count("volume", v)
        if o is None:
            oi_missing += 1
        else:
            oi[side] += o
        if v is None:
            vol_missing += 1
        else:
            vol[side] += v
    return Pcr(
        _ratio(oi["P"], oi["C"], oi_missing, "oi_missing"),
        _ratio(vol["P"], vol["C"], vol_missing, "volume_missing"),
    )


# ── 맥스페인 (§6.6) ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MaxPain:
    """§6.6 결과. strike: 옵션 매수자 총 내재가치(pain)가 최소인 상장 행사가 — OI 가 전부 0 이거나
    후보가 없으면 None. pain: 그 최소 합(계약·pt, `pain_won` 은 × 승수 = 원). tied: 최소인 행사가
    수(1 이면 동률 없음). candidates: 본 후보 행사가 수."""

    strike: Decimal | None
    pain: Decimal | None
    tied: int
    candidates: int
    quality: Quality
    reasons: tuple[str, ...]

    @property
    def pain_won(self) -> float | None:
        return None if self.pain is None else float(self.pain * OPTION_MULTIPLIER)


def _strike(v: Decimal) -> Decimal:
    if not isinstance(cast(object, v), Decimal):
        raise TypeError(f"행사가는 Decimal 이어야 한다: {type(v).__name__}")
    if not v.is_finite() or v <= 0:
        raise ValueError(f"행사가는 유한한 양수: {v}")
    return v


def pain_at(k: Decimal, calls: dict[Decimal, int], puts: dict[Decimal, int]) -> Decimal:
    """행사가 K 에서 만기면 옵션 매수자 총 내재가치(계약·pt) — §6.6 식 그대로(검산용)."""
    total = Decimal(0)
    for kk, n in calls.items():
        if k > kk:
            total += n * (k - kk)
    for kk, n in puts.items():
        if kk > k:
            total += n * (kk - k)
    return total


def max_pain(
    oi: Iterable[tuple[Decimal, str, int]],
    strikes: Iterable[Decimal] | None = None,
    forward: float | None = None,
) -> MaxPain:
    """§6.6 맥스페인. oi: (행사가, 콜풋, OI). strikes: 상장 행사가(후보 — 없으면 OI 가 온 행사가).
    forward: 그 만기 F(동률 규칙). 같은 (행사가, 콜풋)이 두 번이면 ValueError."""
    calls: dict[Decimal, int] = {}
    puts: dict[Decimal, int] = {}
    for k, cp, n in oi:
        book = calls if _cp(cp) == "C" else puts
        k = _strike(k)
        count = _count("oi", n)
        if count is None:
            raise ValueError("OI 는 None 일 수 없다 — 모르는 종목은 빼고 넘긴다")
        if k in book:
            raise ValueError(f"같은 종목이 두 번: {k} {cp}")
        book[k] = count
    cands = sorted({_strike(k) for k in strikes} if strikes is not None else {*calls, *puts})
    if not cands or (not any(calls.values()) and not any(puts.values())):
        return MaxPain(None, None, 0, len(cands), "ok", ())
    pains = _pains(cands, calls, puts)
    best = min(pains)
    tied = [k for k, p in zip(cands, pains, strict=True) if p == best]
    reasons: tuple[str, ...] = ()
    quality: Quality = "ok"
    if len(tied) == 1:
        pick = tied[0]
    elif forward is not None:
        pick = by_distance(tied, forward)[0]
    else:
        pick, quality, reasons = tied[0], "estimated", ("tie_without_forward",)
    return MaxPain(pick, best, len(tied), len(cands), quality, reasons)


def _pains(
    cands: list[Decimal], calls: dict[Decimal, int], puts: dict[Decimal, int]
) -> list[Decimal]:
    """후보마다 §6.6 합 — 누적합으로 O(후보 + 종목). `pain_at` 과 같은 값(정확한 Decimal)."""
    c_items = sorted(calls.items())
    p_items = sorted(puts.items())
    out: list[Decimal] = []
    # K 보다 낮은 콜: Σ n·(K − K') = K·Σn − Σ n·K'
    ci = 0
    c_n = 0
    c_nk = Decimal(0)
    # K 보다 높은 풋: Σ n·(K' − K) = Σ n·K' − K·Σn — 뒤에서부터 빼 나간다
    p_n = sum(n for _, n in p_items)
    p_nk = sum((n * k for k, n in p_items), Decimal(0))
    pi = 0
    for k in cands:
        while ci < len(c_items) and c_items[ci][0] < k:
            c_n += c_items[ci][1]
            c_nk += c_items[ci][1] * c_items[ci][0]
            ci += 1
        while pi < len(p_items) and p_items[pi][0] <= k:
            p_n -= p_items[pi][1]
            p_nk -= p_items[pi][1] * p_items[pi][0]
            pi += 1
        out.append(k * c_n - c_nk + p_nk - k * p_n)
    return out


# ── OI 증감 (§6.7) ────────────────────────────────────────────────────────────

OI_RECOVERY = Decimal("0.9")  # §6.7 — 줄어든 양의 90% 이상 복구되면 이상치 [확인 필요]


@dataclass(frozen=True, slots=True)
class OiSnapshot:
    """종목 하나의 OI 스냅샷 — ts 는 aware, oi 는 0 이상 정수."""

    ts: datetime
    oi: int

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None or self.ts.utcoffset() is None:
            raise ValueError("naive datetime 금지")
        _count("oi", self.oi)


@dataclass(frozen=True, slots=True)
class OiCell:
    """히트맵 한 칸 — 스냅샷 시각 ts 의 OI 와 직전 스냅샷 대비 증감(첫 스냅샷이면 None).
    outlier: 줄었다가 다음 스냅샷에 90% 이상 복구된 두 칸(격리 — 표시 제외)."""

    ts: datetime
    oi: int
    prev_ts: datetime | None
    prev_oi: int | None
    change: int | None
    outlier: bool = False


def oi_step(
    last: OiCell | None, snap: OiSnapshot, *, recovery: Decimal = OI_RECOVERY
) -> tuple[OiCell, OiCell | None]:
    """새 스냅샷 하나 → (새 칸, 이상치로 바뀐 직전 칸 — 없으면 None). last 는 직전 스냅샷의 칸
    (없으면 첫 스냅샷). 시각이 직전보다 늦지 않으면 ValueError."""
    if not Decimal(0) < recovery:
        raise ValueError(f"recovery 는 양수: {recovery}")
    if last is None:
        return OiCell(snap.ts, snap.oi, None, None, None), None
    if snap.ts <= last.ts:
        raise ValueError(f"스냅샷 시각이 거꾸로다: {last.ts} → {snap.ts}")
    change = snap.oi - last.oi
    dip = last.change
    if dip is not None and dip < 0 and change >= recovery * -dip:
        cell = OiCell(snap.ts, snap.oi, last.ts, last.oi, change, True)
        return cell, replace(last, outlier=True)
    return OiCell(snap.ts, snap.oi, last.ts, last.oi, change), None


def oi_change_cells(
    snaps: Sequence[OiSnapshot], *, recovery: Decimal = OI_RECOVERY
) -> tuple[OiCell, ...]:
    """스냅샷 열(시각 오름차순) → 칸마다 증감·이상치(`oi_step` 을 차례로 접은 것)."""
    cells: list[OiCell] = []
    for snap in snaps:
        cell, prev = oi_step(cells[-1] if cells else None, snap, recovery=recovery)
        if prev is not None:
            cells[-1] = prev
        cells.append(cell)
    return tuple(cells)


# ── HIRO-lite (§6.1) ──────────────────────────────────────────────────────────

HiroEvent = Literal["applied", "baseline", "no_price", "reversal", "no_cum"]
ResetReason = Literal["start", "ws_disconnect", "seq_gap", "session_change"]
HIRO_QUALITY: Quality = "estimated"  # PLAN §5.5 — 늘 estimated


def _aware(name: str, t: datetime) -> datetime:
    if t.tzinfo is None or t.utcoffset() is None:
        raise ValueError(f"{name}: naive datetime 금지")
    return t


@dataclass(frozen=True, slots=True)
class HiroTick:
    """옵션 체결 틱 하나 — 누적 매수·매도 체결수량(KIS `shnu_cntg_smtn`·`seln_cntg_smtn`, 모르면
    None)과 호출 쪽이 붙인 그 종목 최근 자체 델타·그 만기 F(모르면 None)."""

    code: str
    trade_date: date
    session: Literal["day", "night"]
    ts: datetime
    cum_buy: int | None
    cum_sell: int | None
    delta: float | None = None
    forward: float | None = None

    def __post_init__(self) -> None:
        _aware("ts", self.ts)
        _count("cum_buy", self.cum_buy)
        _count("cum_sell", self.cum_sell)
        if self.delta is not None and not math.isfinite(self.delta):
            raise ValueError(f"델타는 유한해야 한다: {self.delta}")
        if self.forward is not None and not (math.isfinite(self.forward) and self.forward > 0):
            raise ValueError(f"F 는 유한한 양수: {self.forward}")


@dataclass(frozen=True, slots=True)
class HiroState:
    """HIRO-lite 누적 — (trade_date, session) 한 세션 안, 마지막 리셋(`reset_reason`·`reset_at`)
    뒤부터. customer_flow: 고객 델타 흐름(원), signed_qty: 반영한 틱의 signed 합(계약),
    unpriced_qty: Δ·F 를 몰라 반영하지 못한 |signed| 합, ticks: 반영한 틱 수, reversals: 버린 역행
    틱 수, cums: 종목 → 직전 (누적 매수, 누적 매도)."""

    trade_date: date | None = None
    session: str | None = None
    customer_flow: float = 0.0
    signed_qty: int = 0
    unpriced_qty: int = 0
    ticks: int = 0
    reversals: int = 0
    reset_reason: ResetReason = "start"
    reset_at: datetime | None = None
    last_ts: datetime | None = None
    cums: Mapping[str, tuple[int, int]] = field(default_factory=dict[str, tuple[int, int]])

    @property
    def dealer_hedge(self) -> float:
        """딜러 헤지 수요(원) = 고객 델타 흐름의 부호 반전."""
        return -self.customer_flow

    @property
    def quality(self) -> Quality:
        return HIRO_QUALITY


def hiro_reset(state: HiroState, reason: ResetReason, at: datetime) -> HiroState:
    """누적과 종목별 직전 누적을 비운다 — 세션(태그)은 그대로, 사유·시각을 남긴다."""
    return HiroState(
        state.trade_date,
        state.session,
        reset_reason=reason,
        reset_at=_aware("at", at),
        last_ts=state.last_ts,
    )


def hiro_step(
    state: HiroState, tick: HiroTick, *, multiplier: int = OPTION_MULTIPLIER
) -> tuple[HiroState, HiroEvent]:
    """틱 하나를 반영한 새 상태와 무엇을 했는지. 세션(귀속 거래일·세션)이 바뀌면 먼저 리셋
    (`session_change` — 처음 틱이면 `start` 그대로 시작 시각만)."""
    tag = (tick.trade_date, tick.session)
    if (state.trade_date, state.session) != tag:
        reason: ResetReason = "session_change" if state.trade_date is not None else "start"
        state = replace(hiro_reset(state, reason, tick.ts), trade_date=tag[0], session=tag[1])
    last_ts = tick.ts if state.last_ts is None else max(state.last_ts, tick.ts)
    state = replace(state, last_ts=last_ts)
    if tick.cum_buy is None or tick.cum_sell is None:
        return state, "no_cum"
    prev = state.cums.get(tick.code)
    cums = {**state.cums, tick.code: (tick.cum_buy, tick.cum_sell)}
    if prev is None:
        return replace(state, cums=cums), "baseline"
    if tick.cum_buy < prev[0] or tick.cum_sell < prev[1]:
        return replace(state, reversals=state.reversals + 1), "reversal"
    signed = (tick.cum_buy - prev[0]) - (tick.cum_sell - prev[1])
    if tick.delta is None or tick.forward is None:
        return replace(state, cums=cums, unpriced_qty=state.unpriced_qty + abs(signed)), "no_price"
    flow = signed * tick.delta * multiplier * tick.forward
    return (
        replace(
            state,
            cums=cums,
            customer_flow=state.customer_flow + flow,
            signed_qty=state.signed_qty + signed,
            ticks=state.ticks + 1,
        ),
        "applied",
    )


def is_seq_gap(prev: int | None, seq: int) -> bool:
    """틱 수신 순번(ws-gateway seq — 선물·옵션 공통)이 이어지지 않는가 — 직전 + 1 이 아니면(되돌아감
    포함) 공백. 처음 틱(prev None)은 공백이 아니다."""
    return prev is not None and seq != prev + 1


# ── 딜러 가정 점검 (§6.3) ─────────────────────────────────────────────────────

DEALER_WARN_DAYS = 5  # §6.3 불일치 연속 5거래일이면 경고 [확인 필요]


@dataclass(frozen=True, slots=True)
class DealerCheck:
    """§6.3 하루 판정. call_net·put_net: 증권 계정 콜·풋 순매수 수량 합(계약, 모르면 None).
    consistent: naive 가정(딜러 콜 롱·풋 숏)과 일치 — 콜 순매수 > 0 그리고 풋 순매수 < 0(모르면
    None). 플로우(거래)이지 포지션(OI)이 아니다."""

    call_net: int | None
    put_net: int | None
    consistent: bool | None
    quality: Quality
    reasons: tuple[str, ...]


def _net_sum(name: str, nets: Iterable[int | None]) -> tuple[int | None, int]:
    total: int | None = None
    missing = 0
    for v in nets:
        if v is None:
            missing += 1
            continue
        if isinstance(cast(object, v), bool) or not isinstance(cast(object, v), int):
            raise TypeError(f"{name} 순매수는 int 여야 한다: {type(v).__name__}")
        total = v if total is None else total + v
    return total, missing


def dealer_check(call_nets: Iterable[int | None], put_nets: Iterable[int | None]) -> DealerCheck:
    """§6.3 — 조합(월물·위클리)마다 증권 계정 콜·풋 순매수 수량(모르면 None)."""
    call, call_missing = _net_sum("콜", call_nets)
    put, put_missing = _net_sum("풋", put_nets)
    reasons: list[str] = []
    quality: Quality = "ok"
    if call_missing or put_missing:
        quality, reasons = "estimated", ["pairs_missing"]
    if call is None or put is None:
        reasons += [r for r, v in (("no_call_flow", call), ("no_put_flow", put)) if v is None]
        return DealerCheck(call, put, None, "invalid", tuple(reasons))
    return DealerCheck(call, put, call > 0 and put < 0, quality, tuple(reasons))


def mismatch_streak(days: Sequence[bool | None]) -> int:
    """거래일별 일치 여부(오래된 → 최근, 모르면 None) → 끝에서부터 이어진 불일치 날 수. 모르는
    날에서 끊는다 [확인 필요]."""
    n = 0
    for ok in reversed(days):
        if ok is not False:
            break
        n += 1
    return n


def dealer_warning(streak: int, warn_days: int = DEALER_WARN_DAYS) -> bool:
    """불일치가 warn_days 거래일 이상 이어졌나 — 대시보드 경고 배지."""
    if warn_days < 1:
        raise ValueError(f"warn_days 는 1 이상: {warn_days}")
    return streak >= warn_days


# ── 대량 체결 (§6.4) ──────────────────────────────────────────────────────────

BLOCK_DAYS = 20  # §6.4 최근 20거래일 (PLAN — 기록이 이만큼 모이기 전엔 비활성)
BLOCK_QUANTILE = 0.99  # §6.4 p99
MONEYNESS_STEP = 0.01  # §6.4 `K/F − 1` 1% 구간 [확인 필요]


def moneyness_bucket(strike: Decimal | float, forward: float, step: float = MONEYNESS_STEP) -> int:
    """§6.4 머니니스 구간 — `floor((K/F − 1) / step)`. 0 은 [0%, 1%) (콜·풋은 호출 쪽이 따로)."""
    if not (math.isfinite(forward) and forward > 0):
        raise ValueError(f"F 는 유한한 양수: {forward}")
    if not step > 0:
        raise ValueError(f"step 은 양수: {step}")
    k = float(strike)
    if not (math.isfinite(k) and k > 0):
        raise ValueError(f"행사가는 유한한 양수: {strike}")
    # 경계에 딱 걸친 값(970/1000 − 1 = −3%)이 부동소수 오차로 한 칸 밀리지 않게 1e-9 자리에서 반올림
    return math.floor(round((k / forward - 1) / step, 9))


def nearest_rank(values: Sequence[int], q: float = BLOCK_QUANTILE) -> int:
    """q 분위 — nearest-rank(오름차순 ⌈q·n⌉ 번째, 관측된 값) [확인 필요]. 빈 목록은 ValueError."""
    if not values:
        raise ValueError("빈 표본의 분위는 없다")
    if not 0 < q <= 1:
        raise ValueError(f"q 는 (0, 1]: {q}")
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered) - 1e-12))
    return ordered[rank - 1]


BlockKey = tuple[CallPut, int]  # (콜풋, 머니니스 구간)


@dataclass(frozen=True, slots=True)
class BlockThresholds:
    """§6.4 기준 — days: 쓴 거래일(기록이 있는 가장 최근 BLOCK_DAYS 개, 오름차순). active: 기록이
    BLOCK_DAYS 거래일 이상(아니면 table 은 비고 판정하지 않는다). table: (콜풋, 구간) → p99 1틱
    체결량. samples: 창 안 표본 수."""

    days: tuple[date, ...]
    active: bool
    table: Mapping[BlockKey, int]
    samples: int


INACTIVE = BlockThresholds((), False, {}, 0)


def block_thresholds(
    samples: Iterable[tuple[date, str, int, int]],
    *,
    days: int = BLOCK_DAYS,
    q: float = BLOCK_QUANTILE,
) -> BlockThresholds:
    """§6.4 — 표본 (거래일, 콜풋, 머니니스 구간, 1틱 체결량)에서 기록이 있는 가장 최근 days
    거래일의 구간별 p99. 거래일이 days 개 미만이면 비활성."""
    if days < 1:
        raise ValueError(f"days 는 1 이상: {days}")
    rows: list[tuple[date, CallPut, int, int | None]] = [
        (d, _cp(cp), b, _count("qty", n)) for d, cp, b, n in samples
    ]
    window = tuple(sorted({d for d, *_ in rows})[-days:])
    if len(window) < days:
        return BlockThresholds(window, False, {}, 0)
    keep = set(window)
    groups: dict[BlockKey, list[int]] = {}
    count = 0
    for d, cp, b, n in rows:
        if d in keep and n is not None:
            groups.setdefault((cp, b), []).append(n)
            count += 1
    table = {k: nearest_rank(v, q) for k, v in sorted(groups.items())}
    return BlockThresholds(window, True, table, count)


def is_block(th: BlockThresholds, cp: str, bucket: int, qty: int) -> bool | None:
    """1틱 체결량이 그 구간 p99 이상인가. 비활성이거나 그 구간 기록이 없으면 None(판정 없음)."""
    if not th.active:
        return None
    limit = th.table.get((_cp(cp), bucket))
    if limit is None:
        return None
    return qty >= limit
