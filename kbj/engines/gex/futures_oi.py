"""코스피200 선물 미결제약정(OI)·해석 라벨·파생 투자자 일별 — 순수 계산.

근거: metrics §33, ADR 0043. 계약 ⑨(엔진은 저장소·어댑터를 모른다) — 입력은 리더가 저장소
행을 옮긴 값, 출력은 화면이 그대로 싣는 일별 행.

- 확정 행: KRX 선물 일별 정규 행(`prv_gex.krx_fut_daily` — family kospi200). 합계 OI = 전 월물
  합, 근월물 = 그날 장 마감 뒤에도 살아 있는 가장 가까운 월물(최종거래일 > 그날 — 만기일엔
  다음 월물). 라벨에 쓰는 OI 증감은 그날 행이 있는 종목끼리의 증감 — 전날 있다가 사라진
  만기 종목(최종결제 소멸분)을 청산으로 읽지 않는다. 단순 차는 표시만.
- 잠정 행: KRX 확정 전 그날 — KIS 선물 전광판(`prv_gex.fut_board`) 종목별 마지막 행.
  quality estimated, 구성 검사(전일 기준 ±`BOARD_SANITY_PCT`%) 밖이면 invalid. 만기일·만기
  다음 날은 라벨 보류.
- 투자자 일별: `prv_gex.investor_flow` 그날 정규장 마지막 행(대금 백만원 → 원). 마지막 행이
  선물 정규장 마감(15:45) 전이면 estimated + 사유(장중 수집이 15:40 까지라 종가 단일가가
  빠질 수 있다 [확인 필요]).
- 라벨 임계(`PRICE_FLAT_PCT`·`OI_FLAT_PCT`)는 기본값 [확인 필요]. 값을 지어내지 않는다 —
  입력이 없으면 None·unknown + 사유(절대 규칙 2).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Final, Literal

from kbj.core.calendar import TradingCalendar, monthly_expiry, session_bounds
from kbj.core.quality import Quality
from kbj.core.time import KST

__all__ = [
    "BOARD_SANITY_PCT",
    "BOARD_SOURCE",
    "FLOW_SOURCE",
    "FUT_MARKET",
    "FUT_SECTOR",
    "INVESTORS",
    "KRX_SOURCE",
    "LABEL_KO",
    "MILLION",
    "OI_FLAT_PCT",
    "OPTION_DAYS",
    "OPTION_SECTORS",
    "PRICE_FLAT_PCT",
    "BoardRow",
    "FlowRow",
    "InvestorNet",
    "Label",
    "OiBoard",
    "OiDay",
    "OptionNet",
    "SeriesDay",
    "build_oi_board",
    "confirmed_days",
    "foreign_streak",
    "last_trade_date",
    "oi_label",
    "provisional_day",
]

PRICE_FLAT_PCT: Final = 0.10  # |근월물 등락률| 이 이보다 작으면 가격 보합(%) [확인 필요]
OI_FLAT_PCT: Final = 0.30  # |OI 증감률| 이 이보다 작으면 OI 보합(%) [확인 필요]
BOARD_SANITY_PCT: Final = (
    20.0  # 잠정 합계가 전일 기준에서 이만큼 넘게 벗어나면 구성 불일치 의심(%) [확인 필요]
)
MILLION: Final = 1_000_000  # investor_flow 대금 백만원 → 원
OPTION_DAYS: Final = 5  # 옵션 투자자 일별은 최근 이만큼의 거래일만(패널은 최근 날만 크게 보인다)
PCT_DIGITS: Final = 4  # 비율 반올림 자리(표시용 — 플랫폼 간 float 끝자리 차이를 줄인다)

KRX_SOURCE: Final = "KRX:drv/fut_bydd_trd"
BOARD_SOURCE: Final = "KIS:FHPIF05030200(선물 전광판 — 잠정)"
FLOW_SOURCE: Final = "KIS:FHPTJ04030000(investor_flow)"

FUT_MARKET: Final = "K2I"
FUT_SECTOR: Final = "F001"
# (시장, 업종) → (시리즈, 콜/풋) — config/markets.yaml flows.minute.pairs 의 옵션 조합(GX 실측 #13)
OPTION_SECTORS: Final[Mapping[tuple[str, str], tuple[str, Literal["C", "P"]]]] = {
    ("K2I", "OC01"): ("monthly", "C"),
    ("K2I", "OP01"): ("monthly", "P"),
    ("WKM", "OC05"): ("weekly_mon", "C"),
    ("WKM", "OP05"): ("weekly_mon", "P"),
    ("WKI", "OC04"): ("weekly_thu", "C"),
    ("WKI", "OP04"): ("weekly_thu", "P"),
}
# investor_flow 투자자(GX 표기) → 화면 이름(외국인·기관 합계·개인)
INVESTORS: Final[Mapping[str, str]] = {
    "frgn": "foreign",
    "orgn": "institution",
    "prsn": "individual",
}

Label = Literal["new_long", "new_short", "short_cover", "long_liquidation", "flat", "unknown"]
LABEL_KO: Final[Mapping[Label, str]] = {
    "new_long": "신규 매수 우위",
    "new_short": "신규 매도 우위",
    "short_cover": "숏 커버 우위",
    "long_liquidation": "롱 청산 우위",
    "flat": "보합(해석 보류)",
    "unknown": "판단 불가",
}
_ORDER: Final = (Quality.OK, Quality.STALE, Quality.ESTIMATED, Quality.INVALID)


# ── 입력 ────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SeriesDay:
    """KRX 선물 일별 정규 행 하나(코스피200 — 만기 있는 종목)."""

    trade_date: date
    expiry: str  # YYYYMM
    close: Decimal | None
    cmp_prev: Decimal | None  # 전일비(pt — KRX cmpprevdd_prc 그대로)
    oi: int | None  # 계약
    received_at: datetime  # 행 ts(조회 시각)


@dataclass(frozen=True)
class BoardRow:
    """선물 전광판 종목별 마지막 행(market F·source board·session day)."""

    ts: datetime
    code: str
    price: Decimal | None
    oi: int | None
    remaining_days: int | None
    quality: Quality


@dataclass(frozen=True)
class FlowRow:
    """investor_flow 그날 정규장 마지막 행(투자자 하나·조합 하나). 대금 백만원."""

    trade_date: date
    ts: datetime
    market_code: str
    sector_code: str
    investor: str
    net_qty: int | None
    net_value: int | None
    quality: Quality


# ── 출력 ────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InvestorNet:
    investor: str  # foreign·institution·individual
    net_qty: int | None  # 계약
    net_value_won: int | None  # 원
    ts: datetime
    quality: Quality
    reason: str = ""


@dataclass(frozen=True)
class OptionNet:
    trade_date: date
    series: str  # monthly·weekly_mon·weekly_thu
    cp: Literal["C", "P"]
    investor: str
    net_qty: int | None
    net_value_won: int | None
    ts: datetime
    quality: Quality
    reason: str = ""


@dataclass(frozen=True)
class OiDay:
    trade_date: date
    provisional: bool
    source: str
    as_of: datetime
    quality: Quality
    total_oi: int | None
    oi_change: int | None  # 만기 종목 제외 증감(라벨 입력)
    oi_change_raw: int | None  # 합계 단순 차(표시만)
    oi_change_pct: float | None
    near_expiry: str | None
    near_oi: int | None
    near_close: Decimal | None
    near_change: Decimal | None
    near_change_pct: float | None
    expiry_day: bool
    label: Label
    reasons: tuple[str, ...]
    futures_flow: tuple[InvestorNet, ...] = ()


@dataclass(frozen=True)
class OiBoard:
    days: tuple[OiDay, ...]  # 확정 — 오래된 것부터
    provisional: OiDay | None
    options: tuple[OptionNet, ...]
    foreign_streak: int | None


# ── 공용 ────────────────────────────────────────────────────────────────────────────────


def _worst(qs: Iterable[Quality]) -> Quality:
    got = list(qs)
    return max(got, key=_ORDER.index) if got else Quality.INVALID


def last_trade_date(expiry: str, cal: TradingCalendar) -> date:
    """월물 'YYYYMM' → 최종거래일(정본 `kbj.core.calendar.monthly_expiry` — 둘째 목요일,
    휴장이면 앞당김)."""
    if len(expiry) != 6 or not expiry.isdigit():
        raise ValueError(f"만기는 YYYYMM: {expiry!r}")
    return monthly_expiry(int(expiry[:4]), int(expiry[4:]), cal)


def _pct(num: Decimal | int, den: Decimal | int) -> float | None:
    if den == 0:
        return None
    return round(float(Decimal(num) / Decimal(den) * 100), PCT_DIGITS)


def _chg_pct(close: Decimal | None, cmp_prev: Decimal | None) -> float | None:
    """등락률 = 전일비 ÷ 전일 종가(close − 전일비) × 100."""
    if close is None or cmp_prev is None:
        return None
    return _pct(cmp_prev, close - cmp_prev)


def oi_label(price_pct: float | None, oi_pct: float | None) -> tuple[Label, str]:
    """가격 등락률·OI 증감률(%) → 라벨과 사유(metrics §33 해석 라벨)."""
    if price_pct is None or oi_pct is None:
        missing = [n for n, v in (("가격 등락", price_pct), ("OI 증감", oi_pct)) if v is None]
        return "unknown", f"{'·'.join(missing)} 없음"
    price_flat = abs(price_pct) < PRICE_FLAT_PCT
    oi_flat = abs(oi_pct) < OI_FLAT_PCT
    if price_flat or oi_flat:
        what = "·".join(n for n, f in (("가격", price_flat), ("OI", oi_flat)) if f)
        return "flat", f"{what} 보합(가격 ±{PRICE_FLAT_PCT}%·OI ±{OI_FLAT_PCT}% 안)"
    if price_pct > 0:
        return ("new_long", "가격↑·OI↑") if oi_pct > 0 else ("short_cover", "가격↑·OI↓")
    return ("new_short", "가격↓·OI↑") if oi_pct > 0 else ("long_liquidation", "가격↓·OI↓")


# ── 확정 행 ───────────────────────────────────────────────────────────────────────────────


def _by_day(rows: Iterable[SeriesDay]) -> dict[date, list[SeriesDay]]:
    out: dict[date, list[SeriesDay]] = {}
    for r in rows:
        out.setdefault(r.trade_date, []).append(r)
    for v in out.values():
        v.sort(key=lambda r: r.expiry)
    return out


def _total(rows: Sequence[SeriesDay]) -> int | None:
    if not rows or any(r.oi is None for r in rows):
        return None
    return sum(r.oi or 0 for r in rows)


def _near(rows: Sequence[SeriesDay], d: date, cal: TradingCalendar) -> SeriesDay | None:
    live = [r for r in rows if last_trade_date(r.expiry, cal) > d]
    return min(live, key=lambda r: r.expiry) if live else None


def _counted(r: SeriesDay, d: date, cal: TradingCalendar) -> bool:
    """증감에 넣는 종목: 최종거래일 > d, 또는 만기일(= d) 행인데 KRX 가 OI 를 0 보다 크게 낸 것
    (최종결제 전 잔고 — 다음 날 행이 사라지면 그날 양쪽에서 빠진다). 만기일 OI 0 은 소멸분이라 뺀다
    [확인 필요 — KRX 만기일 행 OI 실측]."""
    ltd = last_trade_date(r.expiry, cal)
    return ltd > d or (ltd == d and r.oi is not None and r.oi > 0)


def _change(
    live: Sequence[SeriesDay], prev: Sequence[SeriesDay]
) -> tuple[int | None, int | None, str]:
    """만기 종목 제외 증감·그 분모(전일 기준). 전일에 없던 종목은 0."""
    prev_oi = {r.expiry: r.oi for r in prev}
    if any(r.oi is None for r in live) or any(
        r.expiry in prev_oi and prev_oi[r.expiry] is None for r in live
    ):
        return None, None, "OI 빈 값"
    cur = sum(r.oi or 0 for r in live)
    base = sum(prev_oi.get(r.expiry) or 0 for r in live)
    return cur - base, base, ""


def confirmed_days(rows: Iterable[SeriesDay], cal: TradingCalendar) -> list[OiDay]:
    """KRX 정규 행 → 일별 확정 행(오래된 것부터). 첫날처럼 전일 행이 없으면 증감·라벨 None."""
    by = _by_day(rows)
    out: list[OiDay] = []
    for d in sorted(by):
        rows_d = by[d]
        reasons: list[str] = []
        total = _total(rows_d)
        live = [r for r in rows_d if _counted(r, d, cal)]
        near = _near(rows_d, d, cal)
        expiry_day = any(last_trade_date(r.expiry, cal) == d for r in rows_d)
        if expiry_day:
            reasons.append("만기일 — 근월물은 다음 월물(만기 종목 OI 0 이면 증감에서 뺀다)")
        prev = by.get(cal.prev_trading_day(d))
        change = base = raw = None
        if prev is None:
            reasons.append("전일 없음")
        else:
            change, base, why = _change(live, prev)
            if why:
                reasons.append(why)
            prev_total = _total(prev)
            if total is not None and prev_total is not None:
                raw = total - prev_total
        if total is None:
            reasons.append("합계 OI 없음(빈 종목)")
        oi_pct = _pct(change, base) if change is not None and base is not None else None
        near_close = near.close if near else None
        near_cmp = near.cmp_prev if near else None
        price_pct = _chg_pct(near_close, near_cmp)
        label, why = oi_label(price_pct, oi_pct)
        reasons.append(why)
        out.append(
            OiDay(
                trade_date=d, provisional=False, source=KRX_SOURCE,
                as_of=max(r.received_at for r in rows_d),
                quality=Quality.OK if total is not None else Quality.INVALID,
                total_oi=total, oi_change=change, oi_change_raw=raw, oi_change_pct=oi_pct,
                near_expiry=near.expiry if near else None, near_oi=near.oi if near else None,
                near_close=near_close, near_change=near_cmp, near_change_pct=price_pct,
                expiry_day=expiry_day, label=label, reasons=tuple(reasons),
            )
        )  # fmt: skip
    return out


# ── 잠정 행 ───────────────────────────────────────────────────────────────────────────────


def provisional_day(
    target: date,
    board: Sequence[BoardRow],
    confirmed: Sequence[SeriesDay],
    cal: TradingCalendar,
    *,
    flows_as_of: datetime | None = None,
) -> OiDay | None:
    """KRX 확정 전 `target` 의 잠정 행. 확정 행이 이미 있거나 전광판·수급이 다 없으면 None."""
    by = _by_day(confirmed)
    last = max(by) if by else None
    if last is not None and last >= target:
        return None
    if not board and flows_as_of is None:
        return None
    reasons = ["KRX 확정 전 — 선물 전광판 잠정"]
    quality = Quality.ESTIMATED
    priced = [r for r in board if r.price is not None and r.remaining_days is not None]
    near_b = min(priced, key=lambda r: (r.remaining_days or 0, r.code)) if priced else None
    total = None if not board or any(r.oi is None for r in board) else sum(r.oi or 0 for r in board)
    if not board:
        reasons.append("전광판 행 없음")
    elif total is None:
        reasons.append("전광판 OI 빈 값")
    prev_ok = last is not None and last == cal.prev_trading_day(target)
    prev_rows = by[last] if last is not None and prev_ok else []
    expiry_day = any(last_trade_date(r.expiry, cal) == target for r in prev_rows)
    after_expiry = any(last_trade_date(r.expiry, cal) == last for r in prev_rows)
    change = raw = None
    oi_pct = None
    near_prev = _near(prev_rows, last, cal) if prev_ok and last is not None else None
    near_change = None
    price_pct = None
    if not prev_ok:
        reasons.append("전일 확정 없음")
    else:
        base_rows = [r for r in prev_rows if last_trade_date(r.expiry, cal) >= target]
        base = _total(base_rows)
        prev_total = _total(prev_rows)
        if total is not None and base is not None:
            change = total - base
            oi_pct = _pct(change, base)
            if base > 0 and abs(total / base - 1) * 100 > BOARD_SANITY_PCT:
                quality = Quality.INVALID
                reasons.append(f"전광판 구성 불일치 의심(전일 기준 ±{BOARD_SANITY_PCT:g}% 밖)")
                change = oi_pct = None
        if total is not None and prev_total is not None and quality is not Quality.INVALID:
            raw = total - prev_total
        if near_b is not None and near_b.price is not None and near_prev is not None:
            if near_prev.close is not None:
                near_change = near_b.price - near_prev.close
                price_pct = _pct(near_change, near_prev.close)
    if expiry_day or after_expiry:
        label: Label = "unknown"
        reasons.append("만기일·만기 다음 날 — 전광판 종목 구성이 바뀌어 잠정 라벨 보류")
    else:
        label, why = oi_label(price_pct, oi_pct)
        reasons.append(why)
    stamps = [r.ts for r in board] + ([flows_as_of] if flows_as_of is not None else [])
    if any(r.quality is not Quality.OK for r in board):
        quality = _worst([quality, *(r.quality for r in board)])
    return OiDay(
        trade_date=target, provisional=True, source=BOARD_SOURCE, as_of=max(stamps),
        quality=quality, total_oi=total, oi_change=change, oi_change_raw=raw,
        oi_change_pct=oi_pct, near_expiry=near_prev.expiry if near_prev else None,
        near_oi=near_b.oi if near_b else None, near_close=near_b.price if near_b else None,
        near_change=near_change, near_change_pct=price_pct, expiry_day=expiry_day, label=label,
        reasons=tuple(reasons),
    )  # fmt: skip


# ── 투자자 일별 ────────────────────────────────────────────────────────────────────────────


def _net(r: FlowRow) -> tuple[Quality, str, int | None]:
    close = session_bounds(r.trade_date, "day")[1]
    q = Quality(r.quality)
    reason = ""
    if r.ts < close:
        q = _worst([q, Quality.ESTIMATED])
        reason = f"선물 마감(15:45) 전 마지막 슬롯 {r.ts.astimezone(KST):%H:%M}"
    won = None if r.net_value is None else r.net_value * MILLION
    return q, reason, won


def futures_flows(rows: Iterable[FlowRow]) -> dict[date, tuple[InvestorNet, ...]]:
    """선물(K2I·F001) 외국인·기관·개인 일별 — 날짜 → (외국인, 기관, 개인 순서로 있는 것만)."""
    out: dict[date, list[InvestorNet]] = {}
    order = list(INVESTORS)
    for r in sorted(
        rows, key=lambda r: (r.trade_date, order.index(r.investor) if r.investor in order else 99)
    ):
        if (r.market_code, r.sector_code) != (
            FUT_MARKET,
            FUT_SECTOR,
        ) or r.investor not in INVESTORS:
            continue
        q, reason, won = _net(r)
        out.setdefault(r.trade_date, []).append(
            InvestorNet(INVESTORS[r.investor], r.net_qty, won, r.ts, q, reason)
        )
    return {d: tuple(v) for d, v in out.items()}


def option_flows(rows: Iterable[FlowRow]) -> list[OptionNet]:
    """옵션(월물·위클리 콜/풋) 외국인·기관·개인 일별 — (날짜, 시리즈, 콜풋, 투자자) 순."""
    series_order = ["monthly", "weekly_mon", "weekly_thu"]
    inv_order = list(INVESTORS.values())
    out: list[OptionNet] = []
    for r in rows:
        key = OPTION_SECTORS.get((r.market_code, r.sector_code))
        if key is None or r.investor not in INVESTORS:
            continue
        q, reason, won = _net(r)
        out.append(
            OptionNet(r.trade_date, key[0], key[1], INVESTORS[r.investor], r.net_qty, won, r.ts, q,
                      reason)
        )  # fmt: skip
    out.sort(
        key=lambda o: (
            o.trade_date,
            series_order.index(o.series),
            o.cp,
            inv_order.index(o.investor),
        )
    )
    return out


def foreign_streak(
    flows: Mapping[date, tuple[InvestorNet, ...]], cal: TradingCalendar
) -> int | None:
    """가장 최근 날부터 외국인 선물 순매수 수량 부호가 같은 연속 거래일 수(+ 매수·− 매도)."""

    def qty(d: date) -> int | None:
        return next((n.net_qty for n in flows.get(d, ()) if n.investor == "foreign"), None)

    if not flows:
        return None
    d = max(flows)
    first = qty(d)
    if first is None:
        return None
    if first == 0:
        return 0
    sign = 1 if first > 0 else -1
    n = 0
    while True:
        v = qty(d)
        if v is None or v == 0 or (v > 0) != (sign > 0):
            break
        n += 1
        d = cal.prev_trading_day(d)
    return sign * n


# ── 묶음 ──────────────────────────────────────────────────────────────────────────────────


def build_oi_board(
    series: Sequence[SeriesDay],
    flows: Sequence[FlowRow],
    board: Sequence[BoardRow],
    *,
    target: date | None,
    cal: TradingCalendar,
    days: int,
) -> OiBoard:
    """확정 행(최근 `days` 거래일)·잠정 행(`target` — KRX 확정 전일 때만)·옵션 일별(창 안 최근
    `OPTION_DAYS` 거래일)·외국인 연속일."""
    if days < 1:
        raise ValueError(f"days 는 1 이상: {days}")
    fut = futures_flows(flows)
    shown = [
        replace(d, futures_flow=fut.get(d.trade_date, ()))
        for d in confirmed_days(series, cal)[-days:]
    ]
    prov: OiDay | None = None
    if target is not None:
        tf = fut.get(target, ())
        prov = provisional_day(
            target, board, series, cal, flows_as_of=max((n.ts for n in tf), default=None)
        )
        if prov is not None:
            prov = replace(prov, futures_flow=tf)
    first = shown[0].trade_date if shown else (prov.trade_date if prov else None)
    in_window = [o for o in option_flows(flows) if first is not None and o.trade_date >= first]
    recent = set(sorted({o.trade_date for o in in_window})[-OPTION_DAYS:])
    options = tuple(o for o in in_window if o.trade_date in recent)
    return OiBoard(
        days=tuple(shown),
        provisional=prov,
        options=options,
        foreign_streak=foreign_streak(fut, cal),
    )
