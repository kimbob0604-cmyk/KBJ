"""시나리오 적정가(구 TAM) — Bear·Base·Bull 의 EPS × PER 밴드(docs/p4_design.md §4.9·D-P4-12,
docs/metrics.md §15, ADR 0016).

SD `tam_modeler.py` 는 시장 규모(Total Addressable Market)가 아니라 EPS × 배수 목표가 시나리오다 —
이름을 **'시나리오 적정가'**로 바꿨다(코드·표·API·화면에 'TAM' 식별자를 새로 만들지 않는다). 승격
원본(폴백 순서 표): `calc_base_eps`:163, `calc_bear_eps`:184, `calc_bull_eps`:201,
`calc_per_scenarios`:222, `calc_pbr_based_tp`:250, `build_auto_tam`:364,
`build_auto_tam_with_label`:456.

- Base EPS: FY1 지배 EPS 추정 > 0(`CONSENSUS`) → TTM EPS > 0(`TTM_EPS` — DART Σ 기본주당이익,
  없으면 TTM 지배순익 ÷ 주식 수 `TTM_NI_PER_SHARE`). 배수 PER 밴드 p50(`5Y_P50`).
- Bear EPS: FY1 × 0.7(`CONSENSUS_×0.7`) → Base × 0.7(`BASE_×0.7`). 배수 p25(`5Y_P25`).
- Bull EPS: 컨센서스 최고(KIS 교체로 없음 — D-P4-10) → Base × 1.2(`BASE_×1.2`). 배수 피어 평균 ×
  1.1 (P5 전에는 없음) → p75(`5Y_P75`).
- PBR 폴백(SD `calc_pbr_based_tp`): Base EPS 가 없거나(적자) PER 밴드가 없으면 BPS × PBR 밴드
  p25·p50·p75(`method=PBR`, 근거 `BPS`·`PBR_5Y_P25`…). 칸 `eps` 에 BPS 를 둔다.
- 산출 불가: PBR 밴드도 없거나 BPS ≤ 0 이면 `method=INSUFFICIENT` + 사유(PBR 밴드의 사유·
  `capital_impairment`·`missing_input` 중 `NA_PRIORITY` 앞의 것). 막는 사유(`blocked` — 재무 통화 ≠
  KRW·정기보고서 없음)가 있으면 계산하지 않고 그 사유.
- 적정가 = EPS × 배수(원 — 반올림은 화면), 업사이드 = (적정가 ÷ 전 거래일 종가 − 1) × 100.
- 품질: TTM 폴백·×배수 폴백·PBR 폴백·컨센서스 단일 추정(입력 품질) → estimated. 근거 문자열 필수.

배수 0.7·1.2 는 `config/fin.yaml scenario` 를 부르는 쪽이 넘긴다. I/O 를 하지 않는다(계약 ⑨).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal

from kbj.core.fin_rows import BandRow, NaReason, ScenarioRow
from kbj.core.quality import Quality
from kbj.engines.consensus.forward import Ntm
from kbj.engines.valuation.multiples import blocked_reason, first_reason, worst
from kbj.engines.valuation.pit import PitView, per_share, share_basis

__all__ = [
    "EPS_SOURCES",
    "ScenarioInputs",
    "inputs_from",
    "scenarios",
]

Case = Literal["bear", "base", "bull"]
_CASES: Final[tuple[Case, ...]] = ("bear", "base", "bull")
_PCT: Final[dict[Case, str]] = {"bear": "P25", "base": "P50", "bull": "P75"}
# 근거 문자열(SD 와 같은 체계) — 화면·API 가 이 값으로 라벨을 고른다
EPS_SOURCES: Final[tuple[str, ...]] = (
    "CONSENSUS",
    "CONSENSUS_NI_PER_SHARE",
    "TTM_EPS",
    "TTM_NI_PER_SHARE",
    "CONSENSUS_×0.7",
    "CONSENSUS_NI_PER_SHARE_×0.7",
    "BASE_×0.7",
    "BASE_×1.2",
    "BPS",
)


@dataclass(frozen=True)
class ScenarioInputs:
    """한 종목(as_of)의 시나리오 입력.

    `fy1_eps`: FY1 지배 EPS 추정(원/주 — 없으면 None), `ttm_eps`: TTM EPS(DART Σ
    기본주당이익, 없으면 부르는 쪽이 TTM 지배순익 ÷ 주식 수로 — 근거 `ttm_eps_source`), `bps`:
    최근 분기말 지배자본 ÷ 주식 수, `per_band`·`pbr_band`: as_of 밴드 행, `price`: 전 거래일
    종가, `blocked`: 계산하지 않는 사유. `mult_label` 은 배수 근거 머리('5Y' — 밴드 창 5년)."""

    code: str
    as_of: date
    price: float | None
    fy1_eps: float | None = None
    fy1_eps_source: str = "CONSENSUS"
    fy1_quality: Quality = Quality.ESTIMATED
    ttm_eps: float | None = None
    ttm_eps_source: str = "TTM_EPS"
    ttm_quality: Quality = Quality.OK
    bps: float | None = None
    bps_quality: Quality = Quality.OK
    per_band: BandRow | None = None
    pbr_band: BandRow | None = None
    blocked: NaReason | None = None
    source: str = "krx+DART"
    mult_label: str = "5Y"


def _band_ok(b: BandRow | None) -> bool:
    return b is not None and b.p25 is not None and b.p50 is not None and b.p75 is not None


def _pick(b: BandRow, case: Case) -> float:
    v = {"bear": b.p25, "base": b.p50, "bull": b.p75}[case]
    if v is None:  # _band_ok 가 막는다
        raise ValueError("밴드 백분위가 없다")
    return v


def _upside(tp: float, price: float | None) -> float | None:
    if price is None or price <= 0:
        return None
    return (tp / price - 1.0) * 100.0


def _insufficient(inp: ScenarioInputs, reason: NaReason) -> list[ScenarioRow]:
    return [
        ScenarioRow(
            code=inp.code,
            as_of=inp.as_of,
            case=c,
            method="INSUFFICIENT",
            eps=None,
            eps_source=None,
            multiple=None,
            multiple_source=None,
            tp=None,
            upside_pct=None,
            price=inp.price,
            na_reason=reason,
            source=inp.source,
            quality=Quality.OK,
        )
        for c in _CASES
    ]


def scenarios(
    inp: ScenarioInputs, *, bear_eps_mult: float = 0.7, bull_eps_mult: float = 1.2
) -> list[ScenarioRow]:
    """Bear·Base·Bull 세 행(그 순서)."""
    if not 0 < bear_eps_mult < 1 < bull_eps_mult:
        raise ValueError("0 < bear_eps_mult < 1 < bull_eps_mult")
    if inp.blocked is not None:
        return _insufficient(inp, inp.blocked)

    base_eps: float | None = None
    base_src = ""
    base_q = Quality.OK
    consensus = False
    if inp.fy1_eps is not None and inp.fy1_eps > 0:
        base_eps, base_src, base_q = inp.fy1_eps, inp.fy1_eps_source, inp.fy1_quality
        consensus = True
    elif inp.ttm_eps is not None and inp.ttm_eps > 0:
        base_eps, base_src = inp.ttm_eps, inp.ttm_eps_source
        base_q = worst(inp.ttm_quality, Quality.ESTIMATED)  # TTM 폴백 → estimated(metrics §15)
    source = inp.source + ("+KIS" if consensus else "")

    if base_eps is not None and inp.per_band is not None and _band_ok(inp.per_band):
        b = inp.per_band
        eps_of: dict[Case, tuple[float, str, Quality]] = {
            "bear": (
                (base_eps * bear_eps_mult, f"{base_src}_×{bear_eps_mult:g}", base_q)
                if consensus
                else (
                    base_eps * bear_eps_mult,
                    f"BASE_×{bear_eps_mult:g}",
                    worst(base_q, Quality.ESTIMATED),
                )
            ),
            "base": (base_eps, base_src, base_q),
            # 컨센서스 최고는 KIS 교체로 없다(D-P4-10) → 언제나 Base × 1.2 폴백
            "bull": (
                base_eps * bull_eps_mult,
                f"BASE_×{bull_eps_mult:g}",
                worst(base_q, Quality.ESTIMATED),
            ),
        }
        rows: list[ScenarioRow] = []
        for c in _CASES:
            eps, src, q = eps_of[c]
            mult = _pick(b, c)
            tp = eps * mult
            rows.append(
                ScenarioRow(
                    code=inp.code,
                    as_of=inp.as_of,
                    case=c,
                    method="PER",
                    eps=eps,
                    eps_source=src,
                    multiple=mult,
                    multiple_source=f"{inp.mult_label}_{_PCT[c]}",
                    tp=tp,
                    upside_pct=_upside(tp, inp.price),
                    price=inp.price,
                    source=source,
                    quality=worst(q, b.quality),
                )
            )
        return rows

    bps = inp.bps
    pb = inp.pbr_band
    if bps is not None and bps > 0 and pb is not None and _band_ok(pb):
        rows = []
        for c in _CASES:
            mult = _pick(pb, c)
            tp = bps * mult
            rows.append(
                ScenarioRow(
                    code=inp.code,
                    as_of=inp.as_of,
                    case=c,
                    method="PBR",
                    eps=bps,
                    eps_source="BPS",
                    multiple=mult,
                    multiple_source=f"PBR_{inp.mult_label}_{_PCT[c]}",
                    tp=tp,
                    upside_pct=_upside(tp, inp.price),
                    price=inp.price,
                    source=inp.source,
                    quality=worst(inp.bps_quality, pb.quality, Quality.ESTIMATED),
                )
            )
        return rows

    cands: list[NaReason | None] = []
    if pb is None:
        cands.append(NaReason.MISSING_INPUT)
    elif not _band_ok(pb):
        cands.append(pb.na_reason or NaReason.MISSING_INPUT)
    if bps is None:
        cands.append(NaReason.MISSING_INPUT)
    elif bps <= 0:
        cands.append(NaReason.CAPITAL_IMPAIRMENT)
    return _insufficient(inp, first_reason(cands) or NaReason.MISSING_INPUT)


def inputs_from(
    code: str,
    as_of: date,
    view: PitView,
    estimate: Ntm | None,
    band_rows: Sequence[BandRow],
    *,
    price: float | None,
    krx_common_shares: int | None,
    blocked: NaReason | None = None,
    source: str = "krx+DART",
) -> ScenarioInputs:
    """PIT 재무(`view`)·NTM(`estimate`)·밴드 → 시나리오 입력.

    주당 값은 **그날의 주식 수**로(§4.8 — `pit.share_basis`): FY1 EPS·TTM EPS 는 보고된 주당 값을
    쓰되 그날 주식 수와 어긋나면(분할·병합 — `pit.per_share`) 총액 ÷ 주식 수로 다시 세고 근거를
    `…_NI_PER_SHARE` 로 적는다. TTM EPS 가 없으면(주요계정 층) TTM 지배순익 ÷ 주식 수. BPS =
    최근 분기말 지배자본 ÷ 주식 수. `blocked` 를 주지 않으면 재무 통화·재무 없음 사유를 `view` 에서
    찾는다. FY1 추정은 KIS 단일 추정일 수 있어 품질 estimated([확인 필요 #29])."""
    shares, basis = share_basis(view, krx_common_shares)
    basis_q = Quality.OK if basis == "DART" else Quality.ESTIMATED
    fy1_eps: float | None = None
    fy1_src = "CONSENSUS"
    fy1_q = Quality.ESTIMATED
    if estimate is not None and estimate.fy1.eps is not None:
        fy = estimate.fy1
        total = fy.ni_owner if fy.ni_owner is not None else fy.ni
        fy1_eps, redone = per_share(total, fy.eps, shares)
        fy1_q = worst(fy.quality, Quality.ESTIMATED)
        if redone:
            fy1_src = "CONSENSUS_NI_PER_SHARE"
    ttm_eps: float | None = None
    ttm_src = "TTM_EPS"
    ttm_q = Quality.OK
    if view.ttm is not None:
        ttm_q = view.ttm.quality
        ttm_eps, redone = per_share(view.ttm.ni_owner, view.ttm.eps, shares)
        if redone:
            ttm_src = "TTM_NI_PER_SHARE"
            ttm_q = worst(ttm_q, basis_q, Quality.ESTIMATED)
    bps: float | None = None
    bps_q = Quality.OK
    if view.balance is not None and view.balance.equity_owner is not None and shares:
        bps = view.balance.equity_owner / shares
        bps_q = worst(view.balance.quality, basis_q)
    by_metric = {b.metric: b for b in band_rows}
    return ScenarioInputs(
        code=code,
        as_of=as_of,
        price=price,
        fy1_eps=fy1_eps,
        fy1_eps_source=fy1_src,
        fy1_quality=fy1_q,
        ttm_eps=ttm_eps,
        ttm_eps_source=ttm_src,
        ttm_quality=ttm_q,
        bps=bps,
        bps_quality=bps_q,
        per_band=by_metric.get("per"),
        pbr_band=by_metric.get("pbr"),
        blocked=blocked if blocked is not None else blocked_reason(view.ttm, view.balance),
        source=source,
    )
