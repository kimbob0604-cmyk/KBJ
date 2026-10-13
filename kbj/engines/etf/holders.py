"""보유 탐색·액티브 동시 편입(docs/p5_design.md §4.4, metrics §19 — 로그인).

- `of_stock(holdings, code)` — 종목 → 담은 ETF(펀드마다 마지막 PDF 스냅 한 줄 — 비중 내림차순,
  같으면
  액티브 먼저, 그다음 fund_id). 운용사 PDF 기준일이 다르면 그대로 둔다(섞지 않는다).
- `of_fund(holdings)` — ETF → 구성종목(비중 내림차순, 같으면 코드).
- `active_consensus(changes, funds, min_funds)` — 같은 날(run_date) 액티브 ETF 가 `min_funds` 개
  이상
  새로 담았거나(NEW·IN10) 늘린(ADD) 종목. ET `etf_tracker_v9/report.py:_consensus`:124 가 보여 주던
  '여러 ETF 가 같은 방향으로 움직인 종목'의 정의(ET `dash.theme_moves` — 같은 날·같은 종목·
  `COUNT(DISTINCT fund_id) >= 2`)를 액티브 ETF·편입 방향으로 좁힌 것이다(metrics §19 — 1개는 제외).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal

from kbj.core.p5_rows import EtfHolder
from kbj.core.rows import Change, Fund

__all__ = [
    "CONSENSUS_KINDS",
    "MIN_FUNDS",
    "Consensus",
    "active_consensus",
    "fund_label",
    "of_fund",
    "of_stock",
]

ConsensusKind = Literal["NEW", "IN10", "ADD"]
CONSENSUS_KINDS: Final[tuple[ConsensusKind, ...]] = ("NEW", "IN10", "ADD")
MIN_FUNDS: Final = 2  # ET theme_moves `HAVING n_etf>=2`


@dataclass(frozen=True)
class Consensus:
    """동시 편입 한 줄 — 종목 하나. funds = 같이 담은·늘린 액티브 펀드 이름(이름 순)."""

    code: str
    name: str | None
    funds: tuple[str, ...]
    kinds: tuple[ConsensusKind, ...]  # CONSENSUS_KINDS 순서
    n_funds: int
    run_dates: tuple[date, ...]  # 조건을 채운 날(오름차순)


def _wt(e: EtfHolder) -> float:
    return e.wt if e.wt is not None else float("-inf")


def of_stock(holdings: Iterable[EtfHolder], code: str) -> list[EtfHolder]:
    """종목 → 담은 ETF. 같은 펀드가 여러 번 오면 가장 늦은 기준일 한 줄만(PDF 기준일이 다르면
    그대로)."""
    last: dict[str, EtfHolder] = {}
    for e in holdings:
        if e.code != code:
            continue
        prev = last.get(e.fund_id)
        if prev is None or e.asof > prev.asof:
            last[e.fund_id] = e
    return sorted(last.values(), key=lambda e: (-_wt(e), not e.is_active, e.fund_id))


def of_fund(holdings: Iterable[EtfHolder]) -> list[EtfHolder]:
    """ETF → 구성종목(비중 내림차순, 같으면 코드)."""
    return sorted(holdings, key=lambda e: (-_wt(e), e.code))


def fund_label(fund_id: str, fund: Fund | None) -> str:
    """펀드 표시 이름(없으면 fund_id — 지어내지 않는다)."""
    if fund is not None and fund.name:
        return fund.name
    return fund_id


def active_consensus(
    changes: Iterable[Change],
    funds: Mapping[str, Fund],
    min_funds: int = MIN_FUNDS,
) -> list[Consensus]:
    """같은 날 액티브 ETF `min_funds` 개 이상이 NEW·IN10·ADD 한 종목.

    날(run_date)마다 따로 센다 — 다른 날의 편입을 합쳐 '동시'로 세지 않는다. 여러 날을 넘기면(창)
    조건을 채운 날들의 펀드·종류를 종목별로 합친다. 순서 = 펀드 수 내림차순, 같으면 코드."""
    if min_funds < 2:
        raise ValueError("min_funds 는 2 이상(1개는 동시 편입이 아니다)")
    by_day: dict[tuple[date, str], dict[str, set[ConsensusKind]]] = {}
    names: dict[str, str | None] = {}
    for c in changes:
        f = funds.get(c.fund_id)
        if f is None or not f.is_active or c.kind not in CONSENSUS_KINDS:
            continue
        kind: ConsensusKind = c.kind  # pyright: ignore[reportAssignmentType]
        by_day.setdefault((c.run_date, c.code), {}).setdefault(c.fund_id, set()).add(kind)
        if c.name or c.code not in names:
            names[c.code] = c.name or names.get(c.code)
    merged: dict[str, tuple[set[str], set[ConsensusKind], set[date]]] = {}
    for (day, code), per_fund in by_day.items():
        if len(per_fund) < min_funds:
            continue
        fs, ks, ds = merged.setdefault(code, (set(), set(), set()))
        fs.update(per_fund)
        for k in per_fund.values():
            ks.update(k)
        ds.add(day)
    out = [
        Consensus(
            code=code,
            name=names.get(code),
            funds=tuple(sorted(fund_label(f, funds.get(f)) for f in fs)),
            kinds=tuple(k for k in CONSENSUS_KINDS if k in ks),
            n_funds=len(fs),
            run_dates=tuple(sorted(ds)),
        )
        for code, (fs, ks, ds) in merged.items()
    ]
    out.sort(key=lambda x: (-x.n_funds, x.code))
    return out
