"""분석 일지 초안(prefill — docs/p8_design.md §4.7, ADR 0033).

P4 산출(밸류에이션 일별 배수·밴드·시나리오 적정가)과 컨센서스(목표가·추정실적), 일봉(52주 수익률)을
읽은 값으로 칸을 채우고, 칸마다 출처(`value_sources` — `kbj` 계산·`consensus`)를 적는다. 값이 없으면
빈칸(None) — 지어내지 않는다. 밸류체인 칸(chain_ids)은 사전 통합 전까지 빈칸.

- price·mktcap·fwd_per·ev_ebitda: 가장 최근 PIT 배수 행(`ValuationDay` —
  price·mktcap_company·per_fwd·
  ev_ebitda_fwd, 없으면 ev_ebitda) → `kbj`.
- band_pos: PER 밴드의 현재 백분위(`BandRow.current_rank_pct`, metric per) → `kbj`.
- ret_52w: 최근 `RET_52W_DAYS`(250) 거래일 창의 종가 (끝 ÷ 처음 − 1) × 100, 창 안 유효 종가가
  `RET_52W_MIN`(240) 미만이면 None [확인 필요 — 52주 = 250 거래일] → `kbj`.
- opm: 가장 최근 추정(E) 결산 행의 영업이익 ÷ 매출 × 100 → `consensus`.
- eps·per·tp: 시나리오 적정가(bear·base·bull) → `kbj`. 시나리오가 없고 컨센서스 목표가가 있으면
  tp.base =
  목표가 평균, tp_source '컨센서스 평균(n곳)' → `consensus`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Final, Literal

__all__ = [
    "CASES",
    "RET_52W_DAYS",
    "RET_52W_MIN",
    "Prefill",
    "PrefillInput",
    "ScenarioCase",
    "build_prefill",
    "ret_52w",
]

CASES: Final = ("bear", "base", "bull")
RET_52W_DAYS: Final = 250
RET_52W_MIN: Final = 240
ValueSource = Literal["kbj", "consensus", "manual"]


@dataclass(frozen=True)
class ScenarioCase:
    eps: float | None
    multiple: float | None  # PER(method PER 일 때만 per 칸으로)
    tp: float | None
    method: str


@dataclass(frozen=True)
class PrefillInput:
    code: str
    day: date
    price: float | None = None
    mktcap: int | None = None
    fwd_per: float | None = None
    ev_ebitda: float | None = None
    band_pos: float | None = None
    closes: Sequence[float | None] = ()  # 최근 거래일 종가(오래된 것 먼저)
    revenue_e: int | None = None
    op_e: int | None = None
    scenarios: Mapping[str, ScenarioCase] = field(default_factory=dict[str, ScenarioCase])
    target_mean: float | None = None
    target_n: int | None = None


@dataclass(frozen=True)
class Prefill:
    code: str
    analyzed_on: date
    price: float | None
    ret_52w: float | None
    fwd_per: float | None
    band_pos: float | None
    ev_ebitda: float | None
    mktcap: int | None
    opm: float | None
    eps: dict[str, float | None]
    per: dict[str, float | None]
    tp: dict[str, float | None]
    tp_source: str | None
    value_sources: dict[str, ValueSource]

    @property
    def filled(self) -> int:
        return len(self.value_sources)


def _money(x: float | None) -> float | None:
    """원 금액은 소수 둘째까지(Decimal 반올림 — 저장 numeric(20,2) 와 같다)."""
    if x is None:
        return None
    return float(Decimal(str(x)).quantize(Decimal("0.01")))


def ret_52w(closes: Sequence[float | None]) -> float | None:
    window = list(closes)[-RET_52W_DAYS:]
    valid = [c for c in window if c is not None and c > 0]
    if len(valid) < RET_52W_MIN:
        return None
    return (valid[-1] / valid[0] - 1.0) * 100.0


def build_prefill(inp: PrefillInput) -> Prefill:
    src: dict[str, ValueSource] = {}

    def kbj(name: str, v: object) -> None:
        if v is not None:
            src[name] = "kbj"

    price = _money(inp.price)
    kbj("price", price)
    kbj("mktcap", inp.mktcap)
    kbj("fwd_per", inp.fwd_per)
    kbj("ev_ebitda", inp.ev_ebitda)
    kbj("band_pos", inp.band_pos)
    r52 = ret_52w(inp.closes)
    kbj("ret_52w", r52)
    opm: float | None = None
    if inp.revenue_e and inp.op_e is not None and inp.revenue_e > 0:
        opm = inp.op_e / inp.revenue_e * 100.0
        src["opm"] = "consensus"

    eps: dict[str, float | None] = dict.fromkeys(CASES)
    per: dict[str, float | None] = dict.fromkeys(CASES)
    tp: dict[str, float | None] = dict.fromkeys(CASES)
    tp_source: str | None = None
    usable = {c: s for c, s in inp.scenarios.items() if c in CASES and s.method != "INSUFFICIENT"}
    for case, s in usable.items():
        eps[case] = _money(s.eps)
        per[case] = s.multiple if s.method == "PER" else None
        tp[case] = _money(s.tp)
    if any(v is not None for v in tp.values()):
        tp_source = "kbj 시나리오 적정가"
        for name, m in (("eps", eps), ("per", per), ("tp", tp)):
            if any(v is not None for v in m.values()):
                src[name] = "kbj"
    elif inp.target_mean is not None:
        tp["base"] = _money(inp.target_mean)
        n = f"{inp.target_n}곳" if inp.target_n else "곳 수 모름"
        tp_source = f"컨센서스 목표가 평균({n})"
        src["tp"] = "consensus"
    if tp_source is not None:
        src["tp_source"] = src["tp"]
    return Prefill(
        code=inp.code,
        analyzed_on=inp.day,
        price=price,
        ret_52w=r52,
        fwd_per=inp.fwd_per,
        band_pos=inp.band_pos,
        ev_ebitda=inp.ev_ebitda,
        mktcap=inp.mktcap,
        opm=opm,
        eps=eps,
        per=per,
        tp=tp,
        tp_source=tp_source,
        value_sources=src,
    )
