"""engine 확장 지표 등록 — Phase 3 항목 2 (docs/phase3_design.md §3·§7-2, metrics §4·§5).

사이클마다 `services/engine/evaluate.py` 가 부르는 플러그인(`MetricPlugin`) — 계산은 모두
`core.metrics`(순수 함수), 여기는 사이클의 범위·시리즈를 넘기고 입력 품질을 합성한다.

| 지표(`metrics.metric`) | 범위·키 | 값(단위) | metrics.md |
|---|---|---|---|
| `vex` | all·nearest·0dte | 딜러 Vanna 익스포저(원 / IV 1%p — 표시 억원) | §4.1 |
| `cex` | all·nearest·0dte | 딜러 Charm 익스포저(원 / 달력 1일 — 표시 억원). **2분 주기** | §4.2 |
| `gex_pc` | all·nearest·0dte | GEX P/C 비율(무차원) | §4.3 |
| `iv_term` | all · key `0dte`·`next_weekly`·`monthly` | 그 칸 만기의 ATM IV(연율) | §5.2 |
| `skew_25d` | series · key 시리즈 라벨 | 25Δ 스큐(연율 — IV(25Δ 풋) − IV(25Δ 콜)) | §5.3 |

- 품질 = core 품질 ⊕ 범위 입력 품질(`CycleView.scope_quality` — S_ref·행 품질·실패한 시리즈) 또는
  시리즈 입력 품질(`series_quality` — S_ref ⊕ 그 시리즈 행 품질). payload 에 입력 사유(`reasons`)
- 기간구조 칸은 평가하지 못한 시리즈(`CycleView.gaps`)도 본다 — 그 칸에 들 수 있었던 실패한
  시리즈면 invalid(`series_failed`), 최종거래일을 모르는 같은 종류 시리즈면 estimated
  (`series_no_expiry`). 빈 칸이어도 같다(범위 지표 `evaluate._scope_input` 과 같은 규칙)
- 새 지표라 기본 플래그 shadow(설계 §4) — 저장만, 발행 안 함
- 일별 지표(IV 랭크·퍼센타일·IV − HV)는 사이클이 아니라 `POST_DAY` 한 번(설계 §1 평가 주기)
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date
from typing import Any

from core.gex import ExpiryEval, Exposure, Scope
from core.metrics.exposure import cex, gex_put_call_ratio, vex
from core.metrics.vol import ExpiryKind, TermSlot, skew_25d, term_structure
from core.preprocess import Quality, worst
from services.engine.registry import CycleView, MetricPlugin, PluginValue, SeriesGap

SCOPES: tuple[Scope, ...] = ("all", "nearest", "0dte")
CHARM_EVERY_S = 120.0  # metrics §4.2 "계산 주기 2분(PLAN)" — 사이클 기준 시각(as_of)으로 센다
_SLOT_KIND: dict[TermSlot, ExpiryKind] = {"next_weekly": "weekly", "monthly": "monthly"}


def _scope_payload(view: CycleView, scope: Scope, x: Exposure) -> dict[str, Any]:
    return {
        "excluded_oi_ratio": x.excluded_oi_ratio,
        "expiries": list(x.expiries),
        "reasons": list(view.scope_reasons.get(scope, ())),
    }


def _exposure(view: CycleView, fn: Callable[[Iterable[ExpiryEval]], Exposure]) -> list[PluginValue]:
    out: list[PluginValue] = []
    for scope in SCOPES:
        x = fn(view.scopes.get(scope, ()))
        q = worst(x.quality, view.scope_quality.get(scope, "ok"))
        out.append(PluginValue(scope, x.value, q, payload=_scope_payload(view, scope, x)))
    return out


def compute_vex(view: CycleView) -> list[PluginValue]:
    """§4.1 범위마다 VEX(원 / IV 1%p)."""
    return _exposure(view, vex)


def compute_cex(view: CycleView) -> list[PluginValue]:
    """§4.2 범위마다 CEX(원 / 달력 1일) — 2분 주기(`CHARM_EVERY_S`)."""
    return _exposure(view, cex)


def compute_gex_pc(view: CycleView) -> list[PluginValue]:
    """§4.3 범위마다 |Σ GEX_put| ÷ Σ GEX_call."""
    out: list[PluginValue] = []
    for scope in SCOPES:
        r = gex_put_call_ratio(view.scopes.get(scope, ()))
        q = worst(r.quality, view.scope_quality.get(scope, "ok"))
        payload = {
            "call_gex": r.call_gex,
            "put_gex": r.put_gex,
            "excluded_oi_ratio": r.excluded_oi_ratio,
            "expiries": list(r.expiries),
            "reasons": list(view.scope_reasons.get(scope, ())),
        }
        out.append(PluginValue(scope, r.value, q, payload=payload))
    return out


def _cls_kind(cls: str) -> ExpiryKind:
    """시장분류의 만기 종류 — '' 월물(라벨 `M:`), WKM·WKI 위클리."""
    return "monthly" if cls in ("", "M") else "weekly"


def _kind(view: CycleView, label: str) -> ExpiryKind:
    """시리즈 라벨의 만기 종류."""
    return _cls_kind(view.series_class.get(label, label.split(":", 1)[0]))


def _could_take(g: SeriesGap, slot: TermSlot, picked: date | None, trade_date: date) -> bool:
    """평가하지 못한 시리즈가 그 칸에 들 수 있었나 — `core.metrics.vol.term_structure` 의 고르는
    규칙으로: 0DTE 는 만기일 = 귀속 거래일(종류 무관), 차기 위클리는 귀속 거래일보다 뒤인 위클리,
    월물은 귀속 거래일 이후 월물 — 고른 만기보다 늦지 않으면(칸이 비었으면 늘). 최종거래일을
    모르면 같은 종류면 들 수 있다고 본다."""
    if slot != "0dte" and _cls_kind(g.mrkt_cls) != _SLOT_KIND[slot]:
        return False
    d = g.last_trade_date
    if d is None:
        return True
    if slot == "0dte":
        return d == trade_date
    after = d > trade_date if slot == "next_weekly" else d >= trade_date
    return after and (picked is None or d <= picked)


def _gap_input(
    view: CycleView, slot: TermSlot, picked: date | None
) -> tuple[Quality, tuple[str, ...]]:
    """기간구조 칸의 입력 품질 — 그 칸에 들 수 있었던 실패한 시리즈 invalid(`series_failed`),
    최종거래일을 모르는 시리즈 estimated(`series_no_expiry` — 범위 nearest·0dte 와 같게)."""
    qs: list[Quality] = ["ok"]
    reasons: list[str] = []
    for g in view.gaps:
        if not _could_take(g, slot, picked, view.trade_date):
            continue
        q, why = (
            ("invalid", "series_failed")
            if g.status == "failed"
            else ("estimated", "series_no_expiry")
        )
        qs.append(q)
        if why not in reasons:
            reasons.append(why)
    return worst(*qs), tuple(reasons)


def compute_iv_term(view: CycleView) -> list[PluginValue]:
    """§5.2 기간구조 — 0DTE·차기 위클리·월물 칸의 ATM IV. 없는 칸은 null(ok). 평가하지 못한
    시리즈가 그 칸에 들 수 있었으면(`_gap_input`) 빈 칸이어도 해당 없음이 아니다 — invalid·
    estimated, 고른 칸이면 그 값의 품질을 떨어뜨린다(다음 만기가 조용히 칸을 채우지 않게)."""
    pts = term_structure(((_kind(view, e.expiry), e) for e in view.evals), view.trade_date)
    out: list[PluginValue] = []
    for p in pts:
        gap_q, gap_r = _gap_input(view, p.slot, p.expiry_date)
        if p.atm is None or p.expiry is None or p.expiry_date is None:
            payload = {"empty": True, "reasons": list(gap_r)}
            out.append(PluginValue("all", None, gap_q, key=p.slot, payload=payload))
            continue
        q = worst(p.atm.quality, view.series_quality.get(p.expiry, "ok"), gap_q)
        payload = {
            "series": p.expiry,
            "expiry_date": p.expiry_date.isoformat(),
            "strikes": [float(k) for k in p.atm.strikes],
            "reasons": [*p.atm.reasons, *view.series_reasons.get(p.expiry, ()), *gap_r],
        }
        out.append(PluginValue("all", p.value, q, key=p.slot, payload=payload))
    return out


def compute_skew(view: CycleView) -> list[PluginValue]:
    """§5.3 시리즈마다 25Δ 스큐."""
    out: list[PluginValue] = []
    for e in view.evals:
        s = skew_25d(e)
        q = worst(s.quality, view.series_quality.get(e.expiry, "ok"))
        payload = {
            "put_iv": s.put_iv,
            "call_iv": s.call_iv,
            "put_strikes": [float(k) for k in s.put_points],
            "call_strikes": [float(k) for k in s.call_points],
            "reasons": [*s.reasons, *view.series_reasons.get(e.expiry, ())],
        }
        out.append(PluginValue("series", s.value, q, key=e.expiry, payload=payload))
    return out


REGISTRY: tuple[MetricPlugin, ...] = (
    MetricPlugin("vex", "vex", compute_vex),
    MetricPlugin("cex", "cex", compute_cex, every_s=CHARM_EVERY_S),
    MetricPlugin("gex_pc", "gex_pc", compute_gex_pc),
    MetricPlugin("iv_term", "iv_term", compute_iv_term),
    MetricPlugin("skew_25d", "skew_25d", compute_skew),
)
