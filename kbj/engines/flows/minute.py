"""장중 투자자 1분 — 분 축·선·7카드·검산 ①②③(순수 계산, P5 F2 — docs/metrics.md §21).

근거: docs/p5_flow_program_prompt.md §0-1·§1.1·§4, docs/p5_design.md §4.7. 입력은
`kbj.core.p5_rows.InvestorMinute`(대금 **백만원** — `prv_gex.investor_flow` 계약), 출력은 이 모듈의
frozen dataclass(대금 **원** — 화면·API 가 쓰는 단위). I/O 없음(계약 ⑨).

- 분 축: 수집 범위 그대로(정규장 시작 ~ 마감 + 10분, 양 끝 포함 — `flows.market_minute` 의 등록부
  `equity {start: open, end: close+10}` 와 같다). 행이 없는 분은 **None**(잇지 않는다 — 보간 금지).
- 7카드(metrics §21): 개인 `prsn`·외국인 `frgn`·기관 합계 `orgn`·금융투자 `scrt`·투신 `ivtr`(**사모
  `pe_fund` 제외** — 사용자 결정 2026-10-13 Q7, KRX 표기)·연기금 `fund`·기타법인 `etc_corp`. 값 =
  그날
  마지막으로 받은(쓸 수 있는) 분의 누적 순매수 대금.
- 선: 기본 켬 = 개인·외국인·기관. 세부(금융투자·투신·사모·은행·보험·종금·연기금·기타단체)와
  기타법인은
  기본 끔.
- 품질(metrics §21 + 사용자 보정 ③): 슬롯 + 45초 안에 받은 값 `ok`, 늦게 받은 값 `estimated`(그
  분보다
  뒤의 누적이다), 검산 실패 분은 `invalid`(선·카드에서 빼고 수를 보인다). 화면의 '잠정' 꼬리표는
  장중
  내내(값 품질과 별개).
- 검산 ②: `orgn = scrt + ivtr + pe_fund + bank + insu + mrbn + fund`(대금은 ± 구분 수
  백만원 — 반올림, 수량은 0). KIS '기관계'가 기타단체(`etc_orgt`)를 넣는지는 공식 문서에 없다
  [실측 필요] — 7세부 합으로 맞지 않으면 `+ etc_orgt` 로도 본다(둘 중 하나가 맞으면 통과,
  `orgn_has_etc_orgt`). 정의 하나만 받으면 기타단체가 거래한 뒤의 분이 줄줄이 빠질 수 있다
  (누적 매도·매수 칸은 줄지 않는다).
- 검산 ①(현물만): `prsn + frgn + orgn + etc_corp (+ etc_orgt — 기관계에 들지 않았을 때만)`
  순매수 대금 = 0 ± `C1_TOL_MILLION`(백만원 반올림 × 5 [확인 필요]). 선물·옵션은 정의 별도 —
  하지 않는다(불가).
- 검산 ③: 마감 뒤 마지막 분 값(× 1,000,000) 대 `prv_flows.market_investor_daily`(원). 차이는
  기록(잔차)하고 quality 로 표시한다. 현물 조합만 — 시장 코드 대응 `SPOT_DAILY_MARKET` [확인 필요
  #23·#39].
- 빠진 구분이 있으면 그 검산은 **불가**(None — 0 으로 채우지 않는다, 메인 결정 R2).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Final

from kbj.core.p5_rows import MINUTE_CARDS, MINUTE_INVESTORS, InvestorMinute
from kbj.core.quality import Quality
from kbj.core.rows import Investor

__all__ = [
    "C1_TERMS",
    "C1_TOL_MILLION",
    "DEFAULT_ON",
    "LATE_AFTER",
    "LINE_LABELS",
    "LINE_ORDER",
    "MILLION",
    "ORGN_PARTS",
    "SPOT_DAILY_MARKET",
    "TO_DAILY_INVESTOR",
    "MinuteBoard",
    "MinuteCardValue",
    "MinuteLineValues",
    "SlotCheck",
    "apply_minute_checks",
    "check3",
    "check_slot",
    "minute_axis",
    "minute_board",
    "minute_report",
    "slot_quality",
]

MILLION: Final = 1_000_000  # 백만원 → 원
LATE_AFTER: Final = timedelta(seconds=45)  # 슬롯 + 45초 넘어 받은 값은 estimated(§3.4 R5)
ORGN_PARTS: Final[tuple[str, ...]] = ("scrt", "ivtr", "pe_fund", "bank", "insu", "mrbn", "fund")
C1_TERMS: Final[tuple[str, ...]] = ("prsn", "frgn", "orgn", "etc_corp", "etc_orgt")
C1_TOL_MILLION: Final = 5  # 백만원 반올림 × 5 [확인 필요 — metrics §21]
DEFAULT_ON: Final[tuple[str, ...]] = ("prsn", "frgn", "orgn")
LINE_ORDER: Final[tuple[str, ...]] = (
    "prsn", "frgn", "orgn", "scrt", "ivtr", "pe_fund", "bank", "insu", "mrbn", "fund", "etc_orgt",
    "etc_corp",
)  # fmt: skip
LINE_LABELS: Final[Mapping[str, str]] = {
    "prsn": "개인",
    "frgn": "외국인",
    "orgn": "기관",
    "scrt": "금융투자",
    "ivtr": "투신",
    "pe_fund": "사모",
    "bank": "은행",
    "insu": "보험",
    "mrbn": "종금",
    "fund": "연기금",
    "etc_orgt": "기타단체",
    "etc_corp": "기타법인",
}
# 현물 조합 id → `prv_flows.market_investor_daily` 의 시장 코드(P3 `INVESTOR_MARKETS` — [확인 필요
# #23·#39])
SPOT_DAILY_MARKET: Final[Mapping[str, str]] = {"kospi": "0001", "kosdaq": "1001"}
# 1분 투자자 이름 → 일별 원장 투자자(검산 ③). 기타단체(etc_orgt)는 일별 원장에 없다(대조 불가).
TO_DAILY_INVESTOR: Final[Mapping[str, Investor]] = {
    "prsn": Investor.INDIVIDUAL,
    "frgn": Investor.FOREIGN,
    "orgn": Investor.INSTITUTION,
    "scrt": Investor.FIN_INVEST,
    "ivtr": Investor.TRUST,
    "pe_fund": Investor.PRIVATE_FUND,
    "bank": Investor.BANK,
    "insu": Investor.INSURANCE,
    "mrbn": Investor.OTHER_FIN,
    "fund": Investor.PENSION,
    "etc_corp": Investor.OTHER_CORP,
}
_MEASURES: Final = ("sell_qty", "buy_qty", "net_qty", "sell_value", "buy_value", "net_value")


def _won(v: int | None) -> int | None:
    return None if v is None else v * MILLION


def minute_axis(open_at: datetime, close_at: datetime, tail_min: int = 10) -> list[datetime]:
    """[개장, 마감 + tail_min] 의 분(양 끝 포함). 평소 09:00~15:40 = 401분."""
    if open_at.tzinfo is None or close_at.tzinfo is None:
        raise ValueError("분 축 시각은 시간대가 있어야 한다")
    if close_at <= open_at:
        raise ValueError("마감이 개장보다 앞이다")
    end = close_at + timedelta(minutes=tail_min)
    n = int((end - open_at).total_seconds() // 60) + 1
    return [open_at + timedelta(minutes=i) for i in range(n)]


def slot_quality(slot: datetime, received_at: datetime) -> Quality:
    """제때(슬롯 + 45초 안) 받았으면 ok, 늦었으면 estimated(그 분보다 뒤의 누적이다)."""
    return Quality.ESTIMATED if received_at - slot > LATE_AFTER else Quality.OK


# ── 검산 ①② ────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SlotCheck:
    """한 분의 검산. c1/c2 = None 이면 불가(구분이 빠졌거나 현물이 아님)."""

    ts: datetime
    c1_residual: int | None  # 백만원 — 검산 ① 의 합(0 이어야 한다)
    c1_ok: bool | None
    c2_failed: tuple[str, ...]  # 어긋난 측정값 이름(두 정의 다 안 맞을 때 — 7세부 합 기준)
    c2_ok: bool | None
    orgn_has_etc_orgt: bool | None = None  # 기관계가 기타단체를 넣은 정의로 맞았다(None = 모름)

    @property
    def ok(self) -> bool:
        return self.c1_ok is not False and self.c2_ok is not False


def _orgn_failed(
    by_investor: Mapping[str, InvestorMinute], parts: Sequence[str]
) -> list[str] | None:
    """기관계 = parts 합인지 — 어긋난 측정값 이름(빈 목록 = 맞음). 볼 측정값이 없으면 None."""
    orgn = by_investor.get("orgn")
    if orgn is None or not all(p in by_investor for p in parts):
        return None
    failed: list[str] = []
    checked = False
    for m in _MEASURES:
        total = getattr(orgn, m)
        vals = [getattr(by_investor[p], m) for p in parts]
        if total is None or any(x is None for x in vals):
            continue
        checked = True
        tol = len(parts) if m.endswith("_value") else 0
        if abs(total - sum(x for x in vals if x is not None)) > tol:
            failed.append(m)
    return failed if checked else None


def check_slot(
    by_investor: Mapping[str, InvestorMinute], *, spot: bool, ts: datetime | None = None
) -> SlotCheck:
    """한 분(같은 ts)의 12구분 → 검산 ②(기관계 두 정의)·①(현물만)."""
    stamp = ts if ts is not None else next(iter(by_investor.values())).ts
    failed = _orgn_failed(by_investor, ORGN_PARTS)
    with_etc: bool | None = None
    if failed is not None:
        with_etc = False
        etc = by_investor.get("etc_orgt")
        etc_zero = etc is None or all(getattr(etc, m) in (None, 0) for m in _MEASURES)
        if failed and not etc_zero:  # 기타단체가 0 이면 두 정의가 같다(허용폭만 커질 뿐)
            alt = _orgn_failed(by_investor, (*ORGN_PARTS, "etc_orgt"))
            if alt is not None and not alt:
                failed, with_etc = [], True
            elif alt is None:
                with_etc = None
    c2_ok = None if failed is None else not failed
    c1_res: int | None = None
    c1_ok: bool | None = None
    if spot:
        terms = tuple(t for t in C1_TERMS if not (with_etc and t == "etc_orgt"))
        vals = [by_investor[t].net_value if t in by_investor else None for t in terms]
        if all(v is not None for v in vals):
            c1_res = sum(v for v in vals if v is not None)
            c1_ok = abs(c1_res) <= C1_TOL_MILLION
    return SlotCheck(stamp, c1_res, c1_ok, tuple(failed or ()), c2_ok, with_etc)


def _by_ts(rows: Iterable[InvestorMinute]) -> dict[datetime, dict[str, InvestorMinute]]:
    out: dict[datetime, dict[str, InvestorMinute]] = {}
    for r in rows:
        out.setdefault(r.ts, {})[r.investor] = r
    return out


def apply_minute_checks(
    rows: Sequence[InvestorMinute], *, spot: bool
) -> tuple[list[InvestorMinute], list[SlotCheck]]:
    """한 조합의 행 → (검산 실패 분의 행을 invalid 로 바꾼 행, 분마다 검산). 순서는 받은 그대로."""
    checks = {ts: check_slot(inv, spot=spot, ts=ts) for ts, inv in _by_ts(rows).items()}
    bad = {ts for ts, c in checks.items() if not c.ok}
    out = [replace(r, quality=Quality.INVALID) if r.ts in bad else r for r in rows]
    return out, [checks[ts] for ts in sorted(checks)]


def check3(
    last: Mapping[str, InvestorMinute], daily_won: Mapping[Investor, int | None]
) -> dict[str, int | None]:
    """검산 ③ — 투자자마다 (마지막 분 × 1,000,000) − 일별 확정(원). 어느 한쪽이 없으면 None."""
    out: dict[str, int | None] = {}
    for inv, daily_inv in TO_DAILY_INVESTOR.items():
        m = last.get(inv)
        d = daily_won.get(daily_inv)
        mv = None if m is None else _won(m.net_value)
        out[inv] = None if mv is None or d is None else mv - d
    return out


# ── 화면(선·카드) ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MinuteLineValues:
    investor: str
    label: str
    default_on: bool
    values: tuple[int | None, ...]  # 원 — 분 축과 같은 길이, 빈 분 None


@dataclass(frozen=True)
class MinuteCardValue:
    investor: str
    label: str
    net_value: int | None  # 원 — 마지막 쓸 수 있는 분의 누적 순매수
    quality: Quality | None


@dataclass(frozen=True)
class MinuteBoard:
    trade_date: date
    axis: tuple[datetime, ...]
    lines: tuple[MinuteLineValues, ...]
    cards: tuple[MinuteCardValue, ...]
    last_ts: datetime | None  # 마지막으로 쓸 수 있는 분
    quality: Quality | None  # 쓸 수 있는 행 중 가장 나쁜 품질
    filled: int  # 값이 있는 분 수
    missing: int  # 축 안의 빈 분 수(마지막 분까지 — 앞으로 올 분은 세지 않는다)
    invalid_slots: int  # 검산 실패로 뺀 분 수


def _worst(qs: Iterable[Quality]) -> Quality | None:
    order = (Quality.OK, Quality.STALE, Quality.ESTIMATED, Quality.INVALID)
    got = list(qs)
    return max(got, key=order.index) if got else None


def minute_board(
    rows: Sequence[InvestorMinute], axis: Sequence[datetime], trade_date: date
) -> MinuteBoard:
    """한 조합의 그날 행 → 선(분 축 — 빈 분 None)·7카드. invalid 행은 빼고 수만 센다."""
    usable = [r for r in rows if r.quality != Quality.INVALID]
    invalid_slots = len({r.ts for r in rows if r.quality == Quality.INVALID})
    by_ts = _by_ts(usable)
    pos = {ts: i for i, ts in enumerate(axis)}
    lines: list[MinuteLineValues] = []
    for inv in LINE_ORDER:
        vals: list[int | None] = [None] * len(axis)
        for ts, invs in by_ts.items():
            i = pos.get(ts)
            if i is not None and inv in invs:
                vals[i] = _won(invs[inv].net_value)
        lines.append(MinuteLineValues(inv, LINE_LABELS[inv], inv in DEFAULT_ON, tuple(vals)))
    last_ts = max(by_ts) if by_ts else None
    cards: list[MinuteCardValue] = []
    for inv, label in MINUTE_CARDS:
        latest = None
        for ts in sorted(by_ts, reverse=True):
            if inv in by_ts[ts]:
                latest = by_ts[ts][inv]
                break
        cards.append(
            MinuteCardValue(
                inv,
                label,
                None if latest is None else _won(latest.net_value),
                None if latest is None else latest.quality,
            )
        )
    filled = sum(1 for ts in by_ts if ts in pos)
    upto = 0 if last_ts is None else sum(1 for ts in axis if ts <= last_ts)
    return MinuteBoard(
        trade_date=trade_date,
        axis=tuple(axis),
        lines=tuple(lines),
        cards=tuple(cards),
        last_ts=last_ts,
        quality=_worst(r.quality for r in usable),
        filled=filled,
        missing=max(0, upto - filled),
        invalid_slots=invalid_slots,
    )


# ── 보고(verify — 건수만) ──────────────────────────────────────────────────────────────


def minute_report(
    rows: Sequence[InvestorMinute],
    *,
    spot: bool,
    daily_won: Mapping[Investor, int | None] | None = None,
) -> dict[str, object]:
    """한 조합의 하루 검산 건수(값·코드 없음 — `verify --program`). c3 는 현물·일별이 있을 때만."""
    _, checks = apply_minute_checks(rows, spot=spot)
    rep: dict[str, object] = {
        "slots": len(checks),
        "c1": _count(c.c1_ok for c in checks) if spot else {"status": "n/a", "reason": "현물만"},
        "c2": _count(c.c2_ok for c in checks),
    }
    if spot and daily_won is not None and checks:
        last_ts = max(c.ts for c in checks)
        last = {r.investor: r for r in rows if r.ts == last_ts}
        res = check3(last, daily_won)
        rep["c3"] = {
            "checked": sum(1 for v in res.values() if v is not None),
            "failed": sum(1 for v in res.values() if v not in (None, 0)),
            "na": sum(1 for v in res.values() if v is None),
        }
    else:
        rep["c3"] = {"status": "n/a", "reason": "현물 일별 확정이 없다"}
    return rep


def _count(oks: Iterable[bool | None]) -> dict[str, int]:
    got = list(oks)
    return {
        "ok": sum(1 for x in got if x is True),
        "failed": sum(1 for x in got if x is False),
        "na": sum(1 for x in got if x is None),
    }


# 12구분 이름이 p5_rows 와 같은지 — 표가 갈라지면 import 때 바로 실패(같은 표 두 벌 금지)
if set(LINE_ORDER) != set(MINUTE_INVESTORS):  # pragma: no cover - 정의 검사
    raise RuntimeError("LINE_ORDER 가 MINUTE_INVESTORS 와 다르다")
