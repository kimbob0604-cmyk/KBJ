"""ETF 구성종목 변동(docs/metrics.md §8.3, docs/p3_design.md §4.3).

승격 원본: ET `etf_tracker_v9/tracker.py` — `QTY_FLOOR`:63(100)·`ACTION_PP`:64(2.0) — 옛 환경변수
(`_envnum`) 대신 `config/markets.yaml` `etf.holdings.*` 를 부르는 쪽이 넘긴다, **`fund_pairs`**:251,
**`analyze`**:274, `KIND_ORDER`·`LABEL`·`PASSIVE_THEMES`(화면 정렬). 계산은 그대로 옮기고 DB
읽기·쓰기만 뺐다(legacy `tracker.py` 의 두 함수는 이 모듈을 부르는 shim).

- 비교는 **펀드 단위**로 한다: 운용사마다 PDF 기준일이 제각각이라(같은 날 받아도 A사는 8/4, B사는
  8/3) 전체를 한 날짜쌍으로 묶으면 대부분이 빠진다(ET 실측: 401개 중 0개 비교). 그래서 펀드마다
  가장 최근 스냅 두 개를 짝짓고, 간격이 `max_gap`(14일)을 넘으면 '하루 변동'이 아니라 누적이라
  빼다.
- 변동 종류: 신규 `NEW`·제외 `DROP`(TOP10 만 주는 원천은 `IN10`·`OUT10`), 공통 종목은 수량이
  `qty_floor` 이상인 것만 변동률을 보고, **CU 재산정 보정**(설정단위 변경 효과 = 유의미 수량 종목
  변동률의 중앙값 — `min_base` 5종목 미만이면 0, TOP10 원천은 보정 없음) 뒤 ±`action_pp`%p 이상이면
  `ADD`·`CUT`.
- ETF 가 다른 ETF 를 담은 것(커버드콜의 모 ETF·재간접)은 종목 시그널이 아니라 뺀다(`etf_codes`).
- 결과 순서는 결정적이다: NEW·IN10(코드 순) → DROP·OUT10(코드 순) → ADD·CUT(코드 순). ET 는
  집합 순회 순서였다(값은 같다).
"""

from __future__ import annotations

import statistics as st
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal

from kbj.core.rows import Change, HoldingRow
from kbj.engines.etf.types import PASSIVE_THEMES

__all__ = [
    "ACTION_PP",
    "KIND_ORDER",
    "LABEL",
    "MAX_GAP_DAYS",
    "MIN_BASE",
    "PASSIVE_THEMES",
    "QTY_FLOOR",
    "FundPair",
    "analyze",
    "fund_pairs",
    "sort_key",
]

# 기본값은 config/markets.yaml etf.holdings 와 같다(ET tracker.py:63·64) — 서비스는 설정값을 넘긴다
QTY_FLOOR: Final = 100.0
ACTION_PP: Final = 2.0
MAX_GAP_DAYS: Final = 14
MIN_BASE: Final = 5

KIND_ORDER: Final[tuple[str, ...]] = ("NEW", "DROP", "IN10", "OUT10", "ADD", "CUT")
LABEL: Final[Mapping[str, str]] = {
    "NEW": "신규편입", "DROP": "전량제외", "ADD": "비중확대", "CUT": "비중축소",
    "IN10": "TOP10 진입", "OUT10": "TOP10 이탈",
}  # fmt: skip


@dataclass(frozen=True)
class FundPair:
    """펀드 하나의 비교 짝 — 가장 최근 스냅(asof)과 그 앞 스냅(prev_asof)."""

    fund_id: str
    asof: date
    prev_asof: date
    gap_days: int


def fund_pairs(
    dates_by_fund: Mapping[str, Iterable[date]], max_gap: int = MAX_GAP_DAYS
) -> list[FundPair]:
    """펀드별 '가장 최근 스냅샷 두 개'(ET `fund_pairs`:251). 스냅이 하나뿐이거나 간격이 max_gap 을
    넘으면 뺀다. 결과는 fund_id 순."""
    if max_gap < 1:
        raise ValueError("max_gap 은 1 이상")
    out: list[FundPair] = []
    for fid in sorted(dates_by_fund):
        dates = sorted(set(dates_by_fund[fid]), reverse=True)
        if len(dates) < 2:
            continue
        cur, prev = dates[0], dates[1]
        gap = (cur - prev).days
        if gap > max_gap:  # 공백이 너무 길면 '하루 변동'이 아니라 누적이라 신호가 흐려진다
            continue
        out.append(FundPair(fid, cur, prev, gap))
    return out


def _qty(h: HoldingRow) -> float:
    return 0.0 if h.qty is None else h.qty


def analyze(
    pair: FundPair,
    cur: Mapping[str, HoldingRow],
    prev: Mapping[str, HoldingRow],
    *,
    run_date: date,
    depth: Literal["full", "top10"] = "full",
    etf_codes: Collection[str] = (),
    qty_floor: float = QTY_FLOOR,
    action_pp: float = ACTION_PP,
    min_base: int = MIN_BASE,
) -> list[Change]:
    """펀드 하나의 변동(ET `analyze`:274 의 펀드 한 짝 몫). 두 스냅 중 하나라도 비면 []."""
    if action_pp <= 0 or qty_floor < 0 or min_base < 1:
        raise ValueError("action_pp > 0, qty_floor ≥ 0, min_base ≥ 1")
    is_top10 = depth == "top10"
    c = {k: v for k, v in cur.items() if k not in etf_codes}
    p = {k: v for k, v in prev.items() if k not in etf_codes}
    if not p or not c:
        return []

    def row(
        code: str,
        kind: Literal["NEW", "DROP", "IN10", "OUT10", "ADD", "CUT"],
        name: str | None,
        prev_h: HoldingRow | None,
        cur_h: HoldingRow | None,
        pct: float | None = None,
        adj: float | None = None,
    ) -> Change:
        return Change(
            run_date=run_date, fund_id=pair.fund_id, code=code, kind=kind, name=name,
            asof=pair.asof, prev_asof=pair.prev_asof, gap_days=pair.gap_days,
            prev_qty=None if prev_h is None else prev_h.qty,
            cur_qty=None if cur_h is None else cur_h.qty,
            prev_wt=None if prev_h is None else prev_h.wt,
            cur_wt=None if cur_h is None else cur_h.wt,
            qty_pct=None if pct is None else round(pct, 2),
            qty_pct_adj=None if adj is None else round(adj, 2),
        )  # fmt: skip

    common = sorted(c.keys() & p.keys())
    # CU 재산정(설정단위 변경) 보정계수 = 유의미 수량 종목들의 변동률 중앙값.
    # TOP10 소스는 CU 재산정 효과를 추정할 표본이 부족하므로 보정하지 않는다
    # 수량을 모르는(None) 종목은 변동률을 낼 수 없다 — 0 으로 바꿔 −100% 가짜 CUT 을 만들지 않는다
    known = [k for k in common if c[k].qty is not None and p[k].qty is not None]
    base = [k for k in known if _qty(p[k]) >= qty_floor and _qty(p[k]) > 0]
    med = 0.0
    if not is_top10 and len(base) >= min_base:
        med = st.median([(_qty(c[k]) / _qty(p[k]) - 1) * 100 for k in base])

    out: list[Change] = []
    for k in sorted(c.keys() - p.keys()):
        out.append(row(k, "IN10" if is_top10 else "NEW", c[k].name, None, c[k]))
    for k in sorted(p.keys() - c.keys()):
        out.append(row(k, "OUT10" if is_top10 else "DROP", p[k].name, p[k], None))
    for k in known:
        pq = _qty(p[k])
        if pq < qty_floor or pq <= 0:
            continue
        pct = (_qty(c[k]) / pq - 1) * 100
        adj = pct - med
        if abs(adj) >= action_pp:
            out.append(row(k, "ADD" if adj > 0 else "CUT", c[k].name, p[k], c[k], pct, adj))
    return out


def sort_key(theme: str | None, is_active: bool, n_funds: int) -> tuple[bool, bool, int]:
    """화면 정렬 키 — 액티브 우선, 패시브 테마(시장대표·코스닥·팩터·ESG·기타) 후순위, 여러 ETF 에서
    동시에 나온 변동이 위(ET `build_report` 정렬)."""
    passive = (theme or "기타") in PASSIVE_THEMES
    return (not is_active, passive, -n_funds)
