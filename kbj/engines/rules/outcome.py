"""알림 후 성과 — 신호 하나 → 기간별 수익률·지수 초과·MFE/MAE(P8 묶음 BKT, docs/p8_design.md §4.5·
D-P8-12, metrics §30.4).

순수 계산. 실시간 신호(`rules.outcome` 작업)와 백테스트 신호(`backtest.run` — '백테스트 기대' 칸)가
**같은 함수**를 부른다(D-P8-6 — 페이지 12 가 둘을 같은 칸으로 나란히 보인다).

- 진입가 P0 = 장중 신호의 슬롯 현재가 / 마감 신호의 그날 종가(부르는 쪽이 넘긴다 — 신호 행의
  `entry_price`). D = 신호 거래일.
- 기간 h 의 만기일 = D 뒤 h 번째 거래일(`days[h]` — `days[0] == D`). 만기일이 `upto`(확정 원장의
  마지막 날) 뒤면 행을 만들지 않는다(미도래 — 다음 실행이 채운다).
- `ret = close(D+h)/P0 − 1`, `excess = ret − (지수 close(D+h)/지수 close(D) − 1)`,
  `mfe = max(high[D+1..D+h])/P0 − 1`, `mae = min(low[D+1..D+h])/P0 − 1`(비율 — 0.05 = 5%).
- 만기일 종가가 없으면 사유 `halted`(그 뒤 봉이 있거나 상장 중) / `delisted`(그 뒤 봉도 없고
  상장 목록에 없다) — 값은 None(지어내지 않는다). 창(D..D+h) 안에 분할 의심(board
  `split_guard` — 정본 문턱)이 있으면 `split_suspect`. 지수 값이 없으면 ret 은 두고 excess 만
  None + 사유 `no_index`.
- 품질 = 쓴 봉(종목·지수) 중 가장 나쁜 것. 값이 없는 행은 ok 가 아니다(`stale`).
- D 이후 봉만 읽는다(진입 이전 값은 P0 하나뿐) — 같은 원장이면 같은 결과.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final

from kbj.core.quality import Quality
from kbj.core.rows import Bar, IndexBar
from kbj.engines.board.newhigh import split_guard
from kbj.engines.flows.ledger import worst_quality

__all__ = ["OutcomeCalc", "signal_outcomes"]

_NO_VALUE_Q: Final = Quality.STALE


@dataclass(frozen=True)
class OutcomeCalc:
    """기간 h 한 칸(`OutcomeRow` 의 계산 부분 — 저장 칸은 부르는 쪽이 붙인다)."""

    horizon: int
    due_date: date
    close_at: float | None
    ret: float | None
    excess: float | None
    mfe: float | None
    mae: float | None
    reason: str | None
    quality: Quality


def _has_split(closes: Sequence[tuple[date, float]], ratio: float) -> bool:
    rows = [{"asof": d.isoformat(), "close": c} for d, c in closes]
    return bool(split_guard(rows, ratio)[0])


def signal_outcomes(
    *,
    entry_price: float | None,
    days: Sequence[date],
    bars: Mapping[date, Bar],
    index_bars: Mapping[date, IndexBar] | None,
    horizons: Sequence[int],
    upto: date,
    split_ratio: float,
    listed: bool = True,
) -> list[OutcomeCalc]:
    """신호 하나의 기간별 성과. `days` = 신호 거래일 D 부터의 거래일(오름차순, D 포함 — 가장 긴
    기간까지), `bars`·`index_bars` = 날짜 → 그 종목·지수 확정 일봉, `listed` = 지금 상장 목록에
    있나(정지·상폐 구분)."""
    if entry_price is not None and not entry_price > 0:
        raise ValueError("진입가는 0 보다 커야 한다")
    if any(h < 1 for h in horizons):
        raise ValueError("기간은 1 이상")
    if not days:
        raise ValueError("days 가 비었다 — 신호 거래일부터 넘긴다")
    d0 = days[0]
    idx = index_bars or {}
    i0 = idx.get(d0)
    out: list[OutcomeCalc] = []
    if entry_price is None:
        return out  # 진입가 없는 신호(평가 못 한 칸)는 성과가 없다
    for h in sorted(set(horizons)):
        if h >= len(days):
            continue  # 거래일 목록이 모자라다 = 아직 먼 미래(미도래)
        due = days[h]
        if due > upto:
            continue
        window = days[1 : h + 1]
        used = [bars[d] for d in window if d in bars]
        b = bars.get(due)
        if b is None or b.close is None:
            after = any(d > due for d in bars)
            reason = "halted" if (after or listed) else "delisted"
            out.append(OutcomeCalc(h, due, None, None, None, None, None, reason, _NO_VALUE_Q))
            continue
        seq = [(d0, entry_price)] + [(x.date, x.close) for x in used if x.close is not None]
        if _has_split(seq, split_ratio):
            out.append(
                OutcomeCalc(h, due, b.close, None, None, None, None, "split_suspect", _NO_VALUE_Q)
            )
            continue
        ret = b.close / entry_price - 1
        highs = [x.high for x in used if x.high is not None]
        lows = [x.low for x in used if x.low is not None]
        mfe = max(highs) / entry_price - 1 if highs else None
        mae = min(lows) / entry_price - 1 if lows else None
        qs = [x.quality for x in used]
        ib = idx.get(due)
        excess: float | None = None
        reason: str | None = None
        if i0 is not None and ib is not None and i0.close and ib.close is not None:
            excess = ret - (ib.close / i0.close - 1)
            qs += [i0.quality, ib.quality]
        else:
            reason = "no_index"
        q = worst_quality(qs) or Quality.INVALID
        out.append(OutcomeCalc(h, due, b.close, ret, excess, mfe, mae, reason, q))
    return out
