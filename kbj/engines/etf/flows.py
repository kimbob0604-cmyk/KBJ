"""ETF 순유입·가격효과·검산 ③·기간 창·유형별 합(docs/metrics.md §4·§8, docs/p3_design.md §4.3).

공식(원, 좌수 S·NAV 는 KRX 일별 원장 — 마감 NAV 만, 장중 iNAV 는 괴리율에만)

- 순유입 = (Sₜ − Sₜ₋₁) × NAVₜ — 가격이 올라 늘어난 순자산은 넣지 않는다
- 가격효과 = Sₜ₋₁ × (NAVₜ − NAVₜ₋₁)
- 검산 ③ = 순자산 변화 − (순유입 + 가격효과). 계산 순자산(S×NAV)으로는 항등식이라 잔차 0(부동소수
  오차뿐). **보고 순자산**(KRX `INVSTASST_NETASST_TOTAMT` → `EtfDay.net_asset`)이 두 날 다 있으면
  그것으로 대조하고, 허용오차 = 0.005원 × (Sₜ + Sₜ₋₁) + 공표 단위(`unit`, 기본 1원 —
  [실측 필요: 원·백만원])를 넘으면 그날 흐름은 `invalid`(집계에서 빠지고 `ledger_check(c3)`).

예외(metrics §4 오류 1~5)

- 분할·병합: 그날 이벤트(수동 표 우선, 없으면 감지 — `splits.detect_split`)가 있으면 전날 행을
  `splits.adjust` 와 같은 식(Sₜ₋₁ × ratio, NAVₜ₋₁ ÷ ratio — 좌수는 반올림하지 않는다)으로 맞춘 뒤
  같은 공식 → `split_adjusted`. 감지한 것은 품질 `estimated`.
- 분배금: 좌수 공식이라 자동(좌수 불변·NAV −D → 순유입 0, 가격효과 −Sₜ₋₁·D). 따로 처리하지 않는다.
- 신규 상장: 전 거래일 행이 없고 그 앞에도 행이 없으면 `new` — 순유입 `None`, 첫날 순자산은
  `new_net_asset`('신규' 목록). 앞에 행이 있는데 전 거래일만 빠졌으면 `invalid`(공백 — 여러 날
  흐름을 하루로 세지 않는다).
- 상장폐지: `delisted_on` 이상인 날의 행은 쓰지 않는다(전날까지 계산).
- 레버리지·인버스: 유형 필터(`by_type`)로 따로 본다. 시장 거래대금에는 넣지 않는다(엔진 market 몫).
- 투자자별 ETF 순매수(장내 — KIS)는 이 모듈이 다루지 않는다 — 순유입과 **합치지 않는다**.

금액은 부동소수(원)다 — NAV 가 소수라 순유입이 원 단위로 떨어지지 않는다. 화면·API 는 반올림
정수로 보인다(`EtfFlow.won`).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from itertools import pairwise
from typing import Any, Final, Literal

from kbj.core.quality import Quality
from kbj.core.rows import EtfDay, EtfMeta, EtfType, LedgerCheck, SplitEvent
from kbj.engines.etf.splits import SPLIT_RATIOS, SPLIT_TOL, detect_split
from kbj.engines.etf.types import etf_type

__all__ = [
    "NAV_ROUND_KRW",
    "EtfFlow",
    "FlowStatus",
    "PeriodFlow",
    "TypeRollup",
    "by_type",
    "c3_checks",
    "c3_tol",
    "check3",
    "daily_flow",
    "period_totals",
    "type_of",
    "window_flows",
]

FlowStatus = Literal["ok", "new", "split_adjusted", "invalid"]
NAV_ROUND_KRW: Final = 0.005  # NAV 공표 반올림(소수 둘째 자리)의 좌당 최대 오차
_COMPUTED_REL_TOL: Final = 1e-9  # 계산 순자산 항등식의 부동소수 오차 허용(상대)
_Q_ORDER: Final[Mapping[Quality, int]] = {
    Quality.OK: 0,
    Quality.STALE: 1,
    Quality.ESTIMATED: 2,
    Quality.INVALID: 3,
}

REASON_INVALID_ROW: Final = "원장 행이 invalid"
REASON_MISSING: Final = "좌수·NAV 결측"
REASON_GAP: Final = "전 거래일 행 없음(공백 — 여러 날 흐름을 하루로 세지 않는다)"
REASON_C3: Final = "검산 ③ 잔차가 허용오차를 넘는다"


@dataclass(frozen=True)
class EtfFlow:
    """ETF 하루 흐름. 금액은 원(부동소수). status 가 ok·split_adjusted 인 것만 집계에 쓴다."""

    code: str
    date: date
    status: FlowStatus
    net_inflow: float | None
    price_effect: float | None
    net_asset_chg: float | None  # 대조에 쓴 순자산 변화(basis 의 것)
    residual: float | None  # 검산 ③ 잔차 = net_asset_chg − (순유입 + 가격효과)
    tol: float | None  # 검산 ③ 허용오차(원)
    basis: Literal["reported", "computed"] | None
    source: str
    quality: Quality
    ratio: float = 1.0  # 그날 분할·병합 비율(새 좌수 ÷ 옛 좌수), 없으면 1
    split: SplitEvent | None = None  # 적용한 이벤트(감지면 origin=detected)
    new_net_asset: float | None = None  # status=new — 첫날 순자산('신규' 목록)
    reason: str | None = None

    @property
    def counted(self) -> bool:
        """집계에 넣는가 — ok·split_adjusted 만."""
        return self.status in ("ok", "split_adjusted")

    @staticmethod
    def won(v: float | None) -> int | None:
        """화면·API 용 원 단위 정수(반올림)."""
        return None if v is None else round(v)


def _worst(*qs: Quality) -> Quality:
    return max(qs, key=lambda q: _Q_ORDER[q])


def c3_tol(s_cur: int, s_prev: int, unit: float = 1.0) -> float:
    """보고 순자산 대조 허용오차(원) = 0.005원 × (Sₜ + Sₜ₋₁) + 공표 단위."""
    if unit < 0 or s_cur < 0 or s_prev < 0:
        raise ValueError("좌수·단위는 0 이상")
    return NAV_ROUND_KRW * (s_cur + s_prev) + unit


def _invalid(cur: EtfDay, why: str, *, source: str | None = None) -> EtfFlow:
    return EtfFlow(
        code=cur.code, date=cur.date, status="invalid", net_inflow=None, price_effect=None,
        net_asset_chg=None, residual=None, tol=None, basis=None, source=source or cur.source,
        quality=Quality.INVALID, reason=why,
    )  # fmt: skip


def _new(cur: EtfDay) -> EtfFlow:
    first = cur.net_asset if cur.net_asset is not None else None
    if first is None and cur.list_shrs is not None and cur.nav is not None:
        first = cur.list_shrs * cur.nav
    return EtfFlow(
        code=cur.code, date=cur.date, status="new", net_inflow=None, price_effect=None,
        net_asset_chg=None, residual=None, tol=None, basis=None, source=cur.source,
        quality=cur.quality, new_net_asset=None if first is None else float(first),
    )  # fmt: skip


def daily_flow(
    prev: EtfDay | None,
    cur: EtfDay,
    split: SplitEvent | None = None,
    *,
    unit: float = 1.0,
) -> EtfFlow:
    """하루 흐름. prev = 전 거래일 행(없으면 신규 상장), split = 그날(cur.date) 분할·병합 이벤트."""
    if cur.quality is Quality.INVALID:
        return _invalid(cur, REASON_INVALID_ROW)
    if prev is None:
        return _new(cur)
    if prev.code != cur.code:
        raise ValueError(f"다른 ETF 의 행이다: {prev.code} ≠ {cur.code}")
    if prev.date >= cur.date:
        raise ValueError("prev 는 cur 보다 앞 날짜여야 한다")
    if split is not None and (split.code != cur.code or split.effective_date != cur.date):
        raise ValueError("split 은 그날(cur.date) 그 ETF 의 이벤트여야 한다")
    if prev.quality is Quality.INVALID:
        return _invalid(cur, f"전날 {REASON_INVALID_ROW}")
    if prev.list_shrs is None or prev.nav is None or cur.list_shrs is None or cur.nav is None:
        return _invalid(cur, REASON_MISSING)

    ratio = 1.0 if split is None else split.ratio
    # 보정은 `splits.adjust` 와 같은 식(Sₜ₋₁ × ratio, NAVₜ₋₁ ÷ ratio)이되 좌수를 정수로 반올림하지
    # 않는다 — 병합(5:1)에서 Sₜ₋₁ 이 k 로 나누어떨어지지 않으면(단주 현금 정산) 반올림한 좌수 × NAV
    # 만큼 가짜 순유입·잔차가 생긴다. 보정 좌수 × 보정 NAV = Sₜ₋₁ × NAVₜ₋₁ 가 그대로 남는다.
    p_s = prev.list_shrs * ratio
    p_nav = prev.nav / ratio
    s1, nav1 = cur.list_shrs, cur.nav
    inflow = (s1 - p_s) * nav1
    price = p_s * (nav1 - p_nav)

    if cur.net_asset is not None and prev.net_asset is not None:
        basis: Literal["reported", "computed"] = "reported"
        chg = float(cur.net_asset - prev.net_asset)
        tol = c3_tol(s1, prev.list_shrs, unit)
    else:
        basis = "computed"
        chg = s1 * nav1 - prev.list_shrs * prev.nav
        tol = _COMPUTED_REL_TOL * max(1.0, abs(s1 * nav1), abs(prev.list_shrs * prev.nav))
    residual = chg - (inflow + price)

    q = _worst(prev.quality, cur.quality)
    if split is not None and split.origin == "detected":
        q = _worst(q, Quality.ESTIMATED)
    status: FlowStatus = "ok" if split is None else "split_adjusted"
    reason: str | None = None
    if abs(residual) > tol:
        status, q, reason = "invalid", Quality.INVALID, REASON_C3
    return EtfFlow(
        code=cur.code, date=cur.date, status=status, net_inflow=inflow, price_effect=price,
        net_asset_chg=chg, residual=residual, tol=tol, basis=basis, source=cur.source,
        quality=q, ratio=ratio, split=split, reason=reason,
    )  # fmt: skip


def check3(flow: EtfFlow, tol: float | None = None) -> bool | None:
    """검산 ③ — |잔차| ≤ 허용오차면 True. 검산할 수 없는 흐름(신규·입력 결측)은 None(0 으로 바꾸지
    않는다)."""
    if flow.residual is None:
        return None
    limit = flow.tol if tol is None else tol
    if limit is None:
        return None
    return abs(flow.residual) <= limit


def _events_by_date(code: str, splits: Iterable[SplitEvent]) -> dict[date, SplitEvent]:
    """그 ETF 의 이벤트 — 같은 날 manual 과 detected 가 있으면 manual."""
    out: dict[date, SplitEvent] = {}
    for ev in splits:
        if ev.code != code:
            continue
        old = out.get(ev.effective_date)
        if old is None or (old.origin == "detected" and ev.origin == "manual"):
            out[ev.effective_date] = ev
    return out


def window_flows(
    days: Sequence[EtfDay],
    n: int,
    *,
    trading_days: Sequence[date] | None = None,
    splits: Iterable[SplitEvent] = (),
    detect: bool = True,
    tol: float = SPLIT_TOL,
    ratios: Sequence[int] = SPLIT_RATIOS,
    unit: float = 1.0,
    listed_on: date | None = None,
    delisted_on: date | None = None,
) -> list[EtfFlow]:
    """한 ETF 의 최근 n 거래일 흐름(날짜 오름차순).

    - `days`: 그 ETF 의 원장 행(날짜 오름차순, 한 코드). 창 첫날의 전날 행까지 넘겨야 첫날 흐름이
      나온다(저장소 `etf_days(end, n + 1)`).
    - `trading_days`(권장): 거래일 목록(오름차순). 주면 흐름은 그 목록의 마지막 n 날 중 행이 있는
      날마다 계산하고, 전 거래일 행이 없으면 신규(앞에 행이 없을 때)·공백 invalid(있을 때)를
      가린다. 안 주면 이어진 두 행을 짝짓고 첫 행은 기준으로만 쓴다(신규 판정 없음).
    - `listed_on`(메타 상장일): 그날 행은 전 거래일을 몰라도 신규로 본다(창 첫날이 상장일일 때).
    - `delisted_on`(메타 상장폐지일): 그날부터의 행은 쓰지 않는다.
    - `splits`: 이벤트 표(수동·저장된 감지). 그날 이벤트가 없고 `detect` 면 감지한다.
    """
    if n < 1:
        raise ValueError("n 은 1 이상")
    if not days:
        return []
    code = days[0].code
    for a, b in pairwise(days):
        if b.code != code:
            raise ValueError(f"한 ETF 의 행만 받는다: {code} ≠ {b.code}")
        if b.date <= a.date:
            raise ValueError("행은 날짜 오름차순이고 겹치지 않아야 한다")
    rows = [d for d in days if delisted_on is None or d.date < delisted_on]
    events = _events_by_date(code, splits)

    def one(prev: EtfDay | None, cur: EtfDay) -> EtfFlow:
        ev = events.get(cur.date)
        if ev is None and detect and prev is not None:
            cand = detect_split(prev, cur, tol=tol, ratios=ratios)
            ev = None if cand is None else cand.to_event()
        return daily_flow(prev, cur, ev, unit=unit)

    out: list[EtfFlow] = []
    if trading_days is None:
        for prev, cur in pairwise(rows):
            out.append(one(prev, cur))
        return out[-n:]

    tds = sorted(set(trading_days))
    by_date = {d.date: d for d in rows}
    index = {d: i for i, d in enumerate(tds)}
    for d in tds[-n:]:
        cur = by_date.get(d)
        if cur is None:
            continue
        i = index[d]
        prev = by_date.get(tds[i - 1]) if i > 0 else None
        if prev is None and d != listed_on:
            earlier = any(r.date < d for r in rows)
            if earlier or i == 0:
                # 앞에 행이 있는데 전 거래일만 없다(공백) / 거래일 목록이 창 첫날에서 시작해 전날을
                # 모른다 — 둘 다 그날 흐름을 셀 수 없다
                why = REASON_GAP if earlier else "창 첫날 — 전 거래일을 모른다"
                out.append(_invalid(cur, why))
                continue
        out.append(one(prev, cur))
    return out


def type_of(meta: EtfMeta | None) -> EtfType:
    """메타의 유형. 비었으면 이름·기초지수로 정하고, 이름도 없으면 기타."""
    if meta is None:
        return EtfType.OTHER
    if meta.etf_type is not None:
        return meta.etf_type
    if meta.name and meta.name.strip():
        return etf_type(meta.name, meta.base_index)
    return EtfType.OTHER


@dataclass(frozen=True)
class TypeRollup:
    """유형 하나의 합(집계에 넣는 흐름만). 7 유형이 늘 다 나온다(수 0 이어도)."""

    etf_type: EtfType
    label: str
    net_inflow: float
    price_effect: float
    n_etfs: int  # 집계에 들어간 ETF 수
    n_new: int  # 신규 상장 흐름 수
    n_invalid: int  # 빠진(invalid) 흐름 수


def by_type(flows: Iterable[EtfFlow], meta: Mapping[str, EtfMeta]) -> list[TypeRollup]:
    """유형별 순유입 합(metrics §4) — `EtfType` 정의 순서, 7개 전부."""
    inflow: dict[EtfType, float] = dict.fromkeys(EtfType, 0.0)
    price: dict[EtfType, float] = dict.fromkeys(EtfType, 0.0)
    codes: dict[EtfType, set[str]] = {t: set() for t in EtfType}
    n_new: dict[EtfType, int] = dict.fromkeys(EtfType, 0)
    n_bad: dict[EtfType, int] = dict.fromkeys(EtfType, 0)
    for f in flows:
        t = type_of(meta.get(f.code))
        if f.counted:
            inflow[t] += f.net_inflow or 0.0
            price[t] += f.price_effect or 0.0
            codes[t].add(f.code)
        elif f.status == "new":
            n_new[t] += 1
        else:
            n_bad[t] += 1
    return [
        TypeRollup(t, t.label, inflow[t], price[t], len(codes[t]), n_new[t], n_bad[t])
        for t in EtfType
    ]


@dataclass(frozen=True)
class PeriodFlow:
    """한 ETF 의 기간 합 — 기간 합 = 일별 합(집계에 넣는 날만)."""

    code: str
    start: date
    end: date
    net_inflow: float
    price_effect: float
    days: int  # 집계에 넣은 날 수
    n_invalid: int
    new_on: date | None = None  # 기간 안에 신규 상장했으면 그날
    new_net_asset: float | None = None
    splits: tuple[SplitEvent, ...] = field(default=())


def period_totals(flows: Iterable[EtfFlow]) -> dict[str, PeriodFlow]:
    """ETF 마다 기간 합. 흐름이 하나도 없는 ETF 는 나오지 않는다."""
    acc: dict[str, dict[str, Any]] = {}
    for f in sorted(flows, key=lambda x: (x.code, x.date)):
        a = acc.setdefault(
            f.code,
            {"start": f.date, "end": f.date, "in": 0.0, "px": 0.0, "days": 0, "bad": 0,
             "new_on": None, "new_na": None, "splits": []},
        )  # fmt: skip
        a["end"] = f.date
        if f.counted:
            a["in"] += f.net_inflow or 0.0
            a["px"] += f.price_effect or 0.0
            a["days"] += 1
            if f.split is not None:
                a["splits"].append(f.split)
        elif f.status == "new":
            a["new_on"], a["new_na"] = f.date, f.new_net_asset
        else:
            a["bad"] += 1
    return {
        code: PeriodFlow(
            code, a["start"], a["end"], a["in"], a["px"], a["days"], a["bad"], a["new_on"],
            a["new_na"], tuple(a["splits"]),
        )
        for code, a in acc.items()
    }  # fmt: skip


def c3_checks(
    flows: Iterable[EtfFlow], checked_at: datetime, *, codes: Collection[str] | None = None
) -> list[LedgerCheck]:
    """검산 ③ 실패 기록(`prv_flows.ledger_check` — domain etf, check_id c3). 실패한 흐름만."""
    out: list[LedgerCheck] = []
    for f in flows:
        if codes is not None and f.code not in codes:
            continue
        if f.reason != REASON_C3 or f.residual is None:
            continue
        out.append(
            LedgerCheck(
                domain="etf", trade_date=f.date, code=f.code, check_id="c3",
                residual=f.residual, checked_at=checked_at,
                detail={"tol": f.tol, "basis": f.basis, "ratio": f.ratio,
                        "net_asset_chg": f.net_asset_chg, "net_inflow": f.net_inflow,
                        "price_effect": f.price_effect, "source": f.source},
            )
        )  # fmt: skip
    return out
