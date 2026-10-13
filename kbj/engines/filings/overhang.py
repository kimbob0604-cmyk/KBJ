"""오버행 — CB·BW·EB·유상증자의 t 시점 상태·요약·추정 보호예수(docs/p4_design.md §4.3, metrics §12).

SD `overhang_parser.get_active_overhang`:171(가치 합)·`collect_overhang_for_stock`:266(만료만 보고
그날의 네이버 주식 수로 희석률)을 대신한다. 다른 점(설계 §4.3 'legacy 와 다른 점'): 청구·상환·전환가
조정을 반영하고, 분모는 DART 주식총수(공개)이며, EV 에 오버행 가치를 더하지 않는다(D-P4-11).

정의(metrics §12 — 정본)
- 잔여 권면 = 권면총액 − Σ(청구·상환 금액, 적용일 ≤ t). 조정 공시는 **접수일 ≤ t**(PIT — 그때 알 수
  있던 것)이고 적용일(없으면 접수일) ≤ t 인 것만 반영한다.
- 현재 가격 = 마지막 전환·행사가 조정(적용일 ≤ t)의 새 가격, 없으면 발행 때 가격. 리픽싱 하한은
  표시만.
- 잠재 주식 = 잔여 ÷ 현재 가격(버림) — CB·BW(분리형도 같은 식)·EB(교환 대상 주식 — **신주가
  아니다**, 요약에서 따로 `exchange_shares`). 유상증자 = 신주 수(결정일 ≤ t < 신주 상장일인 동안만).
- active: 결정일 ≤ t ≤ min(청구기간 끝, 만기) 이고 잔여 > 0. exercisable: active 이고 청구기간
  시작 ≤ t. 유상증자는 행사 개념이 없어 exercisable 이 아니다.
- 오버행 비율(%) = Σ 잠재 신주(CB·BW·유증, active) ÷ 분모 × 100. 공개 분모 = 그 시점 최근 DART
  주식총수(보통주 발행 — 자기주식 포함). 분모가 없으면 비율 None.
- 추정 보호예수 해제일 = 제3자배정 유상증자 신주 상장일 + N개월(`config/fin.yaml
  overhang.lockup_months_third_party` [확인 필요: 규정]) — 출처가 없어 **추정**(D-P4-18).

정정·연결
- 결정 공시의 정정(새 접수번호)은 같은 대상의 **가장 늦은 접수번호**만 쓴다(`dedupe_events`): 회차를
  아는 CB·BW·EB 는 (상품, 회차)가 같으면 같은 사채다. 회차가 없으면 정정 공시(`corrections`)가 같은
  상품의 바로 앞 결정 공시를 대체한다 [추정]. 옛 접수번호를 가리키는 조정은 새 접수번호로 옮긴다.
- 조정 공시의 정정은 같은 (대상, 종류)의 바로 앞 조정을 대체한다 [추정]. 대상을 잇지 못한 조정
  (quality invalid)은 계산에 쓰지 않는다.
- 청구·조정 공시를 파싱하지 못했으면(`unparsed`) 그 상품의 상태는 권면 그대로(상한) + estimated +
  "청구분 미반영".
- 무상증자(BONUS)는 오버행이 아니다(가치 희석 없음) — 상태를 만들지 않는다.

로그인 덧붙임(읽을 때 계산 — `pub_*` 에 쓰지 않는다): `itm`·`overhang_value`·`login_ratio`.
"""

from __future__ import annotations

import calendar
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_FLOOR, Decimal
from typing import Final

from kbj.core.fin_rows import (
    Instrument,
    OverhangAdjust,
    OverhangEvent,
    OverhangState,
    OverhangSummary,
    ShareCount,
)
from kbj.core.quality import Quality

__all__ = [
    "BOND_INSTRUMENTS",
    "DENOM_SOURCE",
    "NEW_SHARE_INSTRUMENTS",
    "LockupEstimate",
    "add_months",
    "dedupe_adjusts",
    "dedupe_events",
    "event_horizon",
    "is_third_party",
    "itm",
    "lockup_estimates",
    "login_ratio",
    "overhang_value",
    "potential_shares",
    "state_at",
    "summary",
]

BOND_INSTRUMENTS: Final = frozenset({Instrument.CB, Instrument.BW, Instrument.EB})
# 잠재 '신주'(오버행 비율 분자) — EB 는 이미 있는 주식과 바꾸므로 빠진다
NEW_SHARE_INSTRUMENTS: Final = frozenset({Instrument.CB, Instrument.BW, Instrument.PAID_IN})
DENOM_SOURCE: Final = "dart_share_count"  # 공개 분모 = DART 정기보고서 주식의 총수(보통주 발행)
LOCKUP_BASIS: Final = "보호예수 출처 없음 — 법정 전매제한으로 계산한 추정 해제일"
_UNPARSED_NOTE: Final = "청구분 미반영(파싱하지 못한 조정·청구 공시 {n}건 — 권면 상한)"


@dataclass(frozen=True)
class LockupEstimate:
    """추정 보호예수 해제(D-P4-18) — 언제나 estimated(출처 없음)."""

    rcept_no: str
    corp_code: str
    listing_date: date
    lockup_end_est: date
    shares: int | None
    months: int
    basis: str = LOCKUP_BASIS
    quality: Quality = Quality.ESTIMATED


# ── 날짜·수 도구 ─────────────────────────────────────────────────────────────────────────


def add_months(d: date, n: int) -> date:
    """d + n 개월(말일 넘침은 그달 말일로)."""
    y, m = divmod(d.month - 1 + n, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def potential_shares(remaining: int, price: float) -> int:
    """잔여 금액 ÷ 가격(버림). 가격은 10진 표기 그대로 나눈다(이진 소수 오차로 1주가 빠지지
    않게)."""
    if remaining < 0 or price <= 0:
        raise ValueError(f"잔여 ≥ 0, 가격 > 0 이어야 한다: {remaining!r}, {price!r}")
    q = Decimal(remaining) / Decimal(repr(price))
    return int(q.to_integral_value(rounding=ROUND_FLOOR))


def _seq(seq: str | None) -> str | None:
    if seq is None or not seq.strip():
        return None
    s = seq.strip().lstrip("0")
    return s or "0"


def is_third_party(alloc_method: str | None) -> bool:
    """배정 방식이 제3자배정인가(원문 그대로의 글자에서 — '제3자배정증자'·'제3자 배정')."""
    return alloc_method is not None and "제3자" in alloc_method.replace(" ", "")


def event_horizon(ev: OverhangEvent, lockup_months: int) -> date | None:
    """이 결정이 상태를 바꿀 수 있는 마지막 날(서비스가 '아직 볼 회사'를 고를 때). 모르면 None.

    CB·BW·EB = min(청구기간 끝, 만기), 유상증자 = 상장일(제3자배정이면 + 보호예수 개월).
    """
    if ev.instrument in BOND_INSTRUMENTS:
        ends = [x for x in (ev.exercise_end, ev.maturity) if x is not None]
        return min(ends) if ends else None
    if ev.instrument is Instrument.PAID_IN and ev.listing_date is not None:
        if is_third_party(ev.alloc_method):
            return add_months(ev.listing_date, lockup_months)
        return ev.listing_date
    return None


# ── 정정 대체 ───────────────────────────────────────────────────────────────────────────


def _resolve(alias: Mapping[str, str], rcept_no: str) -> str:
    seen: set[str] = set()
    r = rcept_no
    while r in alias and r not in seen:
        seen.add(r)
        r = alias[r]
    return r


def dedupe_events(
    events: Iterable[OverhangEvent], corrections: Collection[str] = ()
) -> tuple[list[OverhangEvent], dict[str, str]]:
    """같은 대상의 결정 공시 중 가장 늦은 접수번호만 남긴다 → (남긴 것 — 접수번호 순, {옛 → 새}).

    invalid 결정(계산에 쓰면 안 되는 행)은 뺀다.
    """
    evs = sorted((e for e in events if e.quality is not Quality.INVALID), key=lambda e: e.rcept_no)
    alias: dict[str, str] = {}
    latest: dict[tuple[Instrument, str], OverhangEvent] = {}
    for e in evs:
        s = _seq(e.seq)
        if e.instrument in BOND_INSTRUMENTS and s is not None:
            key = (e.instrument, s)
            old = latest.get(key)
            if old is not None:
                alias[old.rcept_no] = e.rcept_no
            latest[key] = e
    fixes = set(corrections)
    for e in evs:
        if e.rcept_no not in fixes or e.rcept_no in alias:
            continue
        if e.instrument in BOND_INSTRUMENTS and _seq(e.seq) is not None:
            continue  # 회차로 이미 짝지었다
        prior = [
            p
            for p in evs
            if p.rcept_no < e.rcept_no
            and p.instrument is e.instrument
            and p.rcept_no not in alias
            and (p.instrument not in BOND_INSTRUMENTS or _seq(p.seq) is None)
        ]
        if prior:
            alias[max(prior, key=lambda p: p.rcept_no).rcept_no] = e.rcept_no
    kept = [e for e in evs if e.rcept_no not in alias]
    return kept, {k: _resolve(alias, k) for k in alias}


def dedupe_adjusts(
    adjusts: Iterable[OverhangAdjust],
    corrections: Collection[str] = (),
    alias: Mapping[str, str] | None = None,
) -> list[OverhangAdjust]:
    """대상을 잇지 못한(invalid) 조정을 빼고, 대상을 정정 뒤 접수번호로 옮기고, 조정의 정정은 같은
    (대상, 종류)의 바로 앞 조정을 대체한다. 접수번호 순."""
    al = alias or {}
    rows: list[OverhangAdjust] = []
    for a in sorted(adjusts, key=lambda x: x.rcept_no):
        if a.quality is Quality.INVALID or a.target_rcept_no is None:
            continue
        tgt = _resolve(al, a.target_rcept_no)
        rows.append(a if tgt == a.target_rcept_no else _retarget(a, tgt))
    fixes = set(corrections)
    dropped: set[str] = set()
    for a in rows:
        if a.rcept_no not in fixes:
            continue
        prior = [
            p
            for p in rows
            if p.rcept_no < a.rcept_no
            and p.target_rcept_no == a.target_rcept_no
            and p.kind == a.kind
            and p.rcept_no not in dropped
        ]
        if prior:
            dropped.add(max(prior, key=lambda p: p.rcept_no).rcept_no)
    return [a for a in rows if a.rcept_no not in dropped]


def _retarget(a: OverhangAdjust, target: str) -> OverhangAdjust:
    return OverhangAdjust(
        rcept_no=a.rcept_no,
        corp_code=a.corp_code,
        rcept_dt=a.rcept_dt,
        target_rcept_no=target,
        kind=a.kind,
        new_price=a.new_price,
        amount=a.amount,
        shares=a.shares,
        effective_date=a.effective_date,
        note=a.note,
        source=a.source,
        quality=a.quality,
    )


# ── 상태 ──────────────────────────────────────────────────────────────────────────────


def _applied(a: OverhangAdjust, t: date) -> bool:
    return a.rcept_dt <= t and (a.effective_date or a.rcept_dt) <= t


def _bond_state(
    ev: OverhangEvent,
    adjs: Sequence[OverhangAdjust],
    t: date,
    n_unparsed: int,
) -> OverhangState:
    notes: list[str] = list(ev.notes)
    estimated = ev.quality is Quality.ESTIMATED
    used = [a for a in adjs if _applied(a, t)]
    claims = [a for a in used if a.kind in ("exercise", "redemption")]
    unknown_amount = [a for a in claims if a.amount is None]
    if unknown_amount:
        estimated = True
        notes.append(
            f"청구·상환 금액을 모르는 조정 {len(unknown_amount)}건 — 반영 못 함(권면 상한)"
        )
    if n_unparsed:
        estimated = True
        notes.append(_UNPARSED_NOTE.format(n=n_unparsed))
    remaining: int | None = None
    if ev.face_value is not None:
        spent = sum(a.amount or 0 for a in claims)
        remaining = ev.face_value - spent
        if remaining < 0:
            estimated = True
            notes.append("청구·상환 합이 권면총액을 넘는다 — 잔여 0 으로 본다")
            remaining = 0
    prices = sorted(
        (a for a in used if a.kind == "price_adjust" and a.new_price is not None),
        key=lambda a: (a.effective_date or a.rcept_dt, a.rcept_no),
    )
    price = prices[-1].new_price if prices else ev.price
    ends = [x for x in (ev.exercise_end, ev.maturity) if x is not None]
    live = ev.rcept_dt <= t and (not ends or t <= min(ends))
    potential: int | None
    if remaining is not None:
        active = live and remaining > 0
        if price is not None:
            potential = potential_shares(remaining, price) if active else 0
        elif not claims and ev.shares is not None:
            potential = ev.shares if active else 0
            estimated = True
            notes.append("가격 없음 — 결정 공시의 주식 수(조정 미반영)")
        else:
            potential = None if active else 0
            estimated = True
            notes.append("가격 없음 — 잠재 주식을 계산하지 못했다")
    else:
        active = live
        estimated = True
        if ev.shares is not None and not claims:
            potential = ev.shares if active else 0
            notes.append("권면총액 없음 — 결정 공시의 주식 수(청구·조정 미반영)")
        else:
            potential = None if active else 0
            notes.append("권면총액 없음 — 잠재 주식을 계산하지 못했다")
    exercisable = active and ev.exercise_start is not None and ev.exercise_start <= t
    return OverhangState(
        corp_code=ev.corp_code,
        as_of=t,
        rcept_no=ev.rcept_no,
        instrument=ev.instrument,
        remaining_face=remaining,
        price=price,
        potential_shares=potential,
        active=active,
        exercisable=exercisable,
        lockup_end_est=None,
        notes=tuple(dict.fromkeys(notes)),
        quality=Quality.ESTIMATED if estimated else Quality.OK,
    )


def _paid_in_state(ev: OverhangEvent, t: date, lockup_months: int) -> OverhangState:
    notes: list[str] = list(ev.notes)
    estimated = ev.quality is Quality.ESTIMATED
    lockup: date | None = None
    if ev.listing_date is None:
        active = False
        potential: int | None = None
        estimated = True
        notes.append("신주 상장일 모름 — 잠재 주식에서 뺐다 [확인 필요]")
    else:
        active = ev.rcept_dt <= t < ev.listing_date
        if ev.shares is None:
            potential = None if active else 0
            if active:
                estimated = True
                notes.append("신주 수 없음 — 잠재 주식을 계산하지 못했다")
        else:
            potential = ev.shares if active else 0
        if is_third_party(ev.alloc_method):
            # 해제일 칸 자체가 추정이다(칸 이름 `_est` + 사유 한 줄) — 잠재 주식 값은 사실이라
            # 행 품질은 바꾸지 않는다
            lockup = add_months(ev.listing_date, lockup_months)
            notes.append(LOCKUP_BASIS)
    return OverhangState(
        corp_code=ev.corp_code,
        as_of=t,
        rcept_no=ev.rcept_no,
        instrument=ev.instrument,
        remaining_face=None,
        price=ev.price,
        potential_shares=potential,
        active=active,
        exercisable=False,
        lockup_end_est=lockup,
        notes=tuple(dict.fromkeys(notes)),
        quality=Quality.ESTIMATED if estimated else Quality.OK,
    )


def state_at(
    events: Iterable[OverhangEvent],
    adjusts: Iterable[OverhangAdjust],
    t: date,
    *,
    lockup_months: int,
    corrections: Collection[str] = (),
    unparsed: Mapping[str, Instrument | None] | None = None,
) -> list[OverhangState]:
    """t 시점의 결정 공시별 상태(접수일 ≤ t 인 결정만 — PIT). 접수번호 순.

    `corrections` = 정정 공시 접수번호(결정·조정 모두), `unparsed` = 파싱하지 못한 조정·청구 공시
    {접수번호: 상품(모르면 None — 모든 사채 상품에 걸린다)} — 접수일 ≤ t 인 것만 넘긴다.
    """
    evs, alias = dedupe_events((e for e in events if e.rcept_dt <= t), corrections)
    adjs = dedupe_adjusts((a for a in adjusts if a.rcept_dt <= t), corrections, alias)
    by_target: dict[str, list[OverhangAdjust]] = {}
    for a in adjs:
        if a.target_rcept_no is not None:  # dedupe_adjusts 가 이미 걸렀다
            by_target.setdefault(a.target_rcept_no, []).append(a)
    pending = list((unparsed or {}).values())
    out: list[OverhangState] = []
    for ev in evs:
        if ev.instrument in BOND_INSTRUMENTS:
            n = sum(1 for inst in pending if inst is None or inst is ev.instrument)
            out.append(_bond_state(ev, by_target.get(ev.rcept_no, []), t, n))
        elif ev.instrument is Instrument.PAID_IN:
            out.append(_paid_in_state(ev, t, lockup_months))
        # 무상증자(BONUS)는 오버행이 아니다 — 상태 없음
    return out


def summary(
    states: Sequence[OverhangState],
    shares: ShareCount | None,
    t: date,
    *,
    corp_code: str | None = None,
    events: Iterable[OverhangEvent] = (),
) -> OverhangSummary:
    """회사의 t 기준 요약(공개 칸 — 분모 = DART 주식총수).

    `shares` 는 호출자가 PIT(접수일 ≤ t)로 고른 가장 최근 행. `events` 를 주면 그 분모 기준일 뒤에
    상장한 신주(유상·무상)를 notes 에 적는다.
    """
    corp = corp_code or (states[0].corp_code if states else None)
    if corp is None:
        raise ValueError("상태가 없으면 corp_code 를 넘겨야 한다")
    notes: list[str] = []
    # 요약 품질은 합에 들어가는(활성) 상태만 본다
    estimated = any(s.quality is Quality.ESTIMATED and s.active for s in states)
    new_shares = exercisable = exchange = 0
    unknown = 0
    for s in states:
        if s.corp_code != corp:
            raise ValueError(f"다른 회사의 상태가 섞였다: {s.corp_code} ≠ {corp}")
        if s.potential_shares is None:
            # 활성인데 수를 모르거나(가격·권면 없음), 활성 여부 자체를 모른다(상장일 모름)
            unknown += 1
            continue
        if not s.active:
            continue
        if s.instrument in NEW_SHARE_INSTRUMENTS:
            new_shares += s.potential_shares
            if s.exercisable:
                exercisable += s.potential_shares
        elif s.instrument is Instrument.EB:
            exchange += s.potential_shares
    if unknown:
        estimated = True
        notes.append(f"잠재 주식을 모르는 결정 {unknown}건 — 합에서 뺐다")
    den = shares.common_issued if shares is not None else None
    if den is not None and den <= 0:
        den = None
    if shares is not None and shares.quality is Quality.INVALID:
        den = None  # 계산에 쓰면 안 되는 분모
    elif den is not None and shares is not None and shares.quality is not Quality.OK:
        estimated = True
        notes.append(f"분모(주식총수) 품질 {shares.quality.value} — 비율도 추정")
    if den is None or shares is None:
        notes.append("주식총수(정기보고서) 없음 — 비율 없음")
    else:
        later = [
            e
            for e in events
            if e.corp_code == corp
            and e.instrument in (Instrument.PAID_IN, Instrument.BONUS)
            and e.listing_date is not None
            and shares.period_end < e.listing_date <= t
        ]
        if later:
            notes.append(
                f"분모(주식총수 {shares.period_end.isoformat()}) 뒤 신주 상장 {len(later)}건 — "
                "분모가 작게 잡혔을 수 있다"
            )
    return OverhangSummary(
        corp_code=corp,
        as_of=t,
        potential_new_shares=new_shares,
        exercisable_shares=exercisable,
        exchange_shares=exchange,
        denominator=den,
        denom_source=DENOM_SOURCE if den is not None else None,
        ratio_pct=None if den is None else new_shares / den * 100,
        exercisable_ratio_pct=None if den is None else exercisable / den * 100,
        notes=tuple(notes),
        quality=Quality.ESTIMATED if estimated else Quality.OK,
    )


def lockup_estimates(events: Iterable[OverhangEvent], months: int) -> list[LockupEstimate]:
    """제3자배정 유상증자의 추정 보호예수 해제일(상장일 + months). 상장일 모름은 뺀다."""
    out: list[LockupEstimate] = []
    for e in sorted(events, key=lambda x: x.rcept_no):
        if (
            e.instrument is Instrument.PAID_IN
            and e.listing_date is not None
            and is_third_party(e.alloc_method)
        ):
            out.append(
                LockupEstimate(
                    rcept_no=e.rcept_no,
                    corp_code=e.corp_code,
                    listing_date=e.listing_date,
                    lockup_end_est=add_months(e.listing_date, months),
                    shares=e.shares,
                    months=months,
                )
            )
    return out


# ── 로그인 덧붙임(읽을 때 — pub_* 에 쓰지 않는다) ──────────────────────────────────────────


def itm(state: OverhangState, close: float | None) -> bool | None:
    """전 거래일 종가 ≥ 현재 전환·행사·교환가 — 사채만. 가격·종가를 모르면 None."""
    if state.instrument not in BOND_INSTRUMENTS or state.price is None or close is None:
        return None
    return close >= state.price


def overhang_value(state: OverhangState, close: float | None) -> int | None:
    """잠재 주식 × 전 거래일 종가(원 — 추정, 시가가 아님). 비활성은 0, 모르면 None."""
    if not state.active:
        return 0
    if state.potential_shares is None or close is None:
        return None
    return round(state.potential_shares * close)


def login_ratio(s: OverhangSummary, listed_shares: int | None) -> float | None:
    """로그인 분모(전 거래일 KRX 상장주식수 — 보통주)로 다시 낸 오버행 비율(%)."""
    if listed_shares is None or listed_shares <= 0:
        return None
    return s.potential_new_shares / listed_shares * 100
