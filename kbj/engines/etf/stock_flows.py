"""종목별 ETF 수급 — ETF 단위 흐름을 구성종목 비중으로 종목에 나눠 합친다.

ETF 한 개의 기간 값(순유입·거래대금·투자자별 장내 순매수)에 그 ETF 구성종목 비중(PDF `wt` %)을 곱해
종목마다 더한다. 예: 순유입 100억 ETF 가 삼성전자를 25% 담았으면 삼성전자 몫 25억.

- 순유입 몫 = 설정·환매로 생긴 그 종목 매수(+)·매도(−) 추정 — LP·운용사가 바스켓을 사고판 금액.
- 거래대금 몫 = ETF 장내 거래대금 가운데 그 종목 노출분(바스켓이 실제로 사고팔렸다는 뜻은 아니다).
- 투자자별 몫 = ETF 장내 순매수(KIS — LP 상대)를 같은 비중으로 나눈 노출.
- 구성종목을 모르는 ETF(운용사 PDF 를 받지 않는 ETF)는 빠진다 — 덮은 비율을 함께 낸다.
- 비중이 없거나 종목이 아닌 행(현금·선물·스왑·다른 ETF)은 넣지 않는다.
  인버스·레버리지의 선물 노출은 빠진다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Final

__all__ = ["TOP_ETFS", "EtfSide", "StockAgg", "aggregate", "coverage"]

TOP_ETFS: Final = 3  # 종목마다 순유입 몫이 큰 ETF 몇 개를 같이 싣는가


@dataclass(frozen=True)
class EtfSide:
    """ETF 한 개의 기간 값(원). 모르면 None."""

    code: str
    name: str | None
    inflow: float | None
    turnover: float | None
    indiv: float | None = None
    foreign: float | None = None
    inst: float | None = None


@dataclass
class StockAgg:
    code: str
    name: str | None = None
    n_etfs: int = 0
    weight_sum: float = 0.0  # 담은 ETF 비중 합(%) — 참고
    inflow: float | None = None
    turnover: float | None = None
    indiv: float | None = None
    foreign: float | None = None
    inst: float | None = None
    parts: list[tuple[str, str | None, float]] = field(
        default_factory=list
    )  # (ETF, 이름, 순유입 몫)

    def top(self, n: int = TOP_ETFS) -> list[tuple[str, str | None, float]]:
        return sorted(self.parts, key=lambda p: (-abs(p[2]), p[0]))[:n]


def _add(acc: float | None, v: float | None, w: float) -> float | None:
    if v is None:
        return acc
    return (acc or 0.0) + v * w


def aggregate(
    etfs: Mapping[str, EtfSide],
    holdings: Mapping[str, Mapping[str, tuple[str | None, float | None]]],
    *,
    stocks: Iterable[str] | None = None,
) -> dict[str, StockAgg]:
    """{종목: 합}. `holdings` = {ETF 코드: {종목 코드: (종목 이름, 비중 %)}}.

    `stocks` 를 주면 그 안의 코드만 종목으로 본다(현금·선물·ETF 행 거르기). ETF 코드는 늘 뺀다.
    """
    allowed = None if stocks is None else set(stocks)
    out: dict[str, StockAgg] = {}
    for etf_code, held in holdings.items():
        e = etfs.get(etf_code)
        if e is None:
            continue
        for code, (name, wt) in held.items():
            if wt is None or wt <= 0 or code in etfs or code == etf_code:
                continue
            if allowed is not None and code not in allowed:
                continue
            w = wt / 100.0
            a = out.setdefault(code, StockAgg(code=code))
            a.name = a.name or name
            a.n_etfs += 1
            a.weight_sum += wt
            a.inflow = _add(a.inflow, e.inflow, w)
            a.turnover = _add(a.turnover, e.turnover, w)
            a.indiv = _add(a.indiv, e.indiv, w)
            a.foreign = _add(a.foreign, e.foreign, w)
            a.inst = _add(a.inst, e.inst, w)
            if e.inflow is not None:
                a.parts.append((etf_code, e.name, e.inflow * w))
    return out


def coverage(etfs: Mapping[str, EtfSide], known: Iterable[str]) -> tuple[int, float | None]:
    """(구성종목을 아는 ETF 수, 그 ETF 들의 |순유입| 합 ÷ 전체 |순유입| 합 × 100)."""
    k = {c for c in known if c in etfs}
    tot = sum(abs(e.inflow) for e in etfs.values() if e.inflow is not None)
    got = sum(abs(etfs[c].inflow or 0.0) for c in k)
    return len(k), (got / tot * 100 if tot else None)
