"""장중 가집계(KIS FHPTJ04400000 — `prv_flows.investor_intraday`) 회차 고르기 → 오늘 원장 행.

근거: 공식 예제(koreainvestment/open-trading-api `examples_llm/domestic_stock/
foreign_institution_total`, 2026-10-14 확인 — docs/probe_results.md #66). 가집계는 증권사 직원이
장중에 입력한 값을 단순 누계한 **순위 목록**(순매수 상위·순매도 상위)이고, 입력은 하루 4회다(±10분
차이, 장 사정에 따라 바뀔 수 있음). 그래서 10분 슬롯마다 받아도 회차 사이에는 같은 값이다.

`rules.intraday`(실시간)·백테스트 장중 재생·규칙 검증이 같은 함수를 부른다(D-P8-6 — 입력이 같아야
신호·지문이 같다). 순수 함수 — 저장소를 읽는 일은
`kbj.services.engine.rules_input.live_flow_rounds`.

- 슬롯 ts 에서 구분(외국인·기관)마다 **확실히 들어온 가장 최근 회차**(회차 + 10분 ≤ ts)를 고르고,
  그 회차 창 [회차 + 10분, 다음 회차 − 10분) 안·ts 이하에서 **가장 늦게 받은 슬롯의 목록
  하나만** 쓴다. 다음 회차 값이 들어왔을 수 있는 ±10분 창에서 받은 목록은 어느 회차인지 단정할
  수 없어 쓰지 않는다.
- 첫 회차 전이거나 그 회차 목록을 못 받았거나(수집 실패), 두 목록(순매수·순매도 상위) 어디에도 없는
  종목·구분은 값이 없다 → 평가기가 '판단 불가'(지어내지 않는다, 0 으로 세지 않는다).
- 오늘 원장 행: (종목, 구분, 거래소)마다 처음 나온 값 하나(source `kis.prelim`, estimated — 수집기의
  `prelim_days` 와 같은 규칙).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Final

from kbj.core.quality import Quality
from kbj.core.rows import IntradayInvestor, Investor, InvestorDay
from kbj.core.time import KST, to_kst
from kbj.engines.flows.ledger import LedgerRow, Who, build_ledger

__all__ = [
    "PRELIM_ROUNDS",
    "PRELIM_ROUND_SLACK",
    "PRELIM_SOURCE",
    "PrelimRound",
    "latest_round",
    "live_rows",
    "settled_round",
]

PRELIM_SOURCE: Final = "kis.prelim"  # kbj.data.private.kis.investors.PRELIM_SOURCE 와 같은 값
# 입력 회차(KST — 공식 예제 docstring). 장 운영 사정에 따라 바뀔 수 있다 [실측 필요 #66: 실제 편차]
PRELIM_ROUNDS: Final[Mapping[Investor, tuple[time, ...]]] = {
    Investor.FOREIGN: (time(9, 30), time(11, 20), time(13, 20), time(14, 30)),
    Investor.INSTITUTION: (time(10, 0), time(11, 20), time(13, 20), time(14, 30)),
}
PRELIM_ROUND_SLACK: Final = timedelta(minutes=10)  # 공식 예제 '±10분 정도 차이'
_WHO: Final[Mapping[Investor, Who]] = {Investor.FOREIGN: "foreign", Investor.INSTITUTION: "inst"}


@dataclass(frozen=True)
class PrelimRound:
    """가집계 한 회차. `start`(회차 + 10분)부터 `end`(다음 회차 − 10분, 마지막 회차면 None) 전까지
    받은 목록이 이 회차 값이다."""

    investor: Investor
    at: datetime  # 회차 시각(KST)
    start: datetime
    end: datetime | None

    @property
    def label(self) -> str:
        """'13:20' — 근거 문구('장중 잠정 13:20 회차')."""
        return f"{to_kst(self.at):%H:%M}"


def settled_round(investor: Investor, ts: datetime) -> PrelimRound | None:
    """ts 에 값이 들어왔다고 확신할 수 있는 가장 최근 회차(회차 + 10분 ≤ ts). 첫 회차 전이면
    None."""
    t = to_kst(ts)
    times = PRELIM_ROUNDS[investor]
    found: PrelimRound | None = None
    for k, at in enumerate(times):
        a = datetime.combine(t.date(), at, tzinfo=KST)
        if a + PRELIM_ROUND_SLACK > t:
            break
        end = (
            datetime.combine(t.date(), times[k + 1], tzinfo=KST) - PRELIM_ROUND_SLACK
            if k + 1 < len(times)
            else None
        )
        found = PrelimRound(investor, a, a + PRELIM_ROUND_SLACK, end)
    return found


def latest_round(
    rows: Sequence[IntradayInvestor], ts: datetime
) -> tuple[list[IntradayInvestor], dict[Investor, PrelimRound]]:
    """슬롯 ts 의 평가에 쓸 가집계 행 — 구분마다 확실히 들어온 가장 최근 회차의 목록 하나(그 회차 창
    안·ts 이하에서 가장 늦게 받은 슬롯). 돌려주는 회차는 실제로 행이 있는 구분만."""
    out: list[IntradayInvestor] = []
    used: dict[Investor, PrelimRound] = {}
    for inv in PRELIM_ROUNDS:
        r = settled_round(inv, ts)
        if r is None:
            continue
        cand = [
            q
            for q in rows
            if q.investor is inv and r.start <= q.ts <= ts and (r.end is None or q.ts < r.end)
        ]
        if not cand:
            continue
        last = max(q.ts for q in cand)
        out += [q for q in cand if q.ts == last]
        used[inv] = r
    return out, used


def live_rows(
    rows: Sequence[IntradayInvestor], ts: datetime
) -> tuple[dict[str, LedgerRow], dict[str, str]]:
    """슬롯 ts 의 장중 수급 — 종목별 오늘 원장 행(kis.prelim, estimated)과 구분별 회차 시각
    (`{"foreign": "13:20", "inst": "13:20"}` — 행이 있는 구분만)."""
    day: date = to_kst(ts).date()
    picked, used = latest_round(rows, ts)
    seen: dict[tuple[str, Investor, str], InvestorDay] = {}
    for q in picked:
        k = (q.code, q.investor, q.venue)
        if k not in seen:
            seen[k] = InvestorDay(q.code, day, q.investor, q.net_value, q.net_qty, PRELIM_SOURCE,
                                  q.venue, Quality.ESTIMATED)  # fmt: skip
    if not seen:
        return {}, {}
    ledger = build_ledger([], [], list(seen.values()), [], [day])
    rounds = {_WHO[inv]: r.label for inv, r in used.items()}
    return {code: r for (code, d), r in ledger.rows.items() if d == day}, rounds
