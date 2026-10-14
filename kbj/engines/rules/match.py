"""일치 감시 — 실시간 신호 대 백테스트 신호, 차이마다 사유(P8 묶음 BKT, docs/p8_design.md D-P8-14,
metrics §30.4).

순수 계산. 같은 (규칙 판, 종목, 시점) 집합을 날마다 비교한다.

- 비교 대상 = 규칙이 **참**이었던 시점(실시간 상태 sent·suppressed·dry·cooldown, 백테스트 dry·
  cooldown). skipped(평가 못 함·판단 불가)는 신호가 아니다. 쿨다운 상태는 창 시작 전 이력에 따라
  달라질 수 있어 비교하지 않는다(참/거짓만).
- 키: 마감 = (모드, 종목, 거래일) — 실행 시각이 달라도 같은 칸, 장중 = (모드, 종목, 슬롯 시각).
- 사유: 두 지문(입력 지문 `input_fp` — 칸까지 마지막 260칸 OHLCV·수급)을 알면 같음 → `logic`
  (같은 입력에 다른 결과 = 버그, health 오류), 다름 → `data_revised`(KIS 잠정 → KRX 확정, 장중 잠정
  수급 → 확정). 한쪽 지문을 모르면(실시간이 거짓이라 행을 남기지 않았다) `revised(e)` 로 판단 —
  그 칸 입력이 실시간 평가 뒤에 바뀌었을 수 있으면(마감 평가 뒤 KRX 확정 도착) `data_revised`,
  아니면(저장된 슬롯·이미 확정이던 원장) `logic`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Literal

__all__ = [
    "LIVE_TRUE_STATUSES",
    "MatchDay",
    "MatchDiffCalc",
    "MatchEvent",
    "classify",
    "compare",
    "match_days",
]

MatchMode = Literal["close", "intraday_replay"]
Reason = Literal["data_revised", "logic"]
Side = Literal["live_only", "backtest_only"]
# 실시간 신호 중 '규칙이 참' 인 상태(skipped 만 아니면)
LIVE_TRUE_STATUSES: Final = frozenset({"sent", "suppressed", "dry", "cooldown"})


@dataclass(frozen=True)
class MatchEvent:
    code: str
    eval_ts: datetime
    trade_date: date
    mode: MatchMode
    input_fp: str


@dataclass(frozen=True)
class MatchDiffCalc:
    code: str
    eval_ts: datetime
    side: Side
    reason: Reason

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "eval_ts": self.eval_ts.isoformat(), "side": self.side,
                "reason": self.reason}  # fmt: skip


@dataclass(frozen=True)
class MatchDay:
    trade_date: date
    live_n: int
    backtest_n: int
    diffs: tuple[MatchDiffCalc, ...]

    @property
    def logic_n(self) -> int:
        return sum(1 for d in self.diffs if d.reason == "logic")


def _key(e: MatchEvent) -> tuple[str, str, object]:
    return (e.mode, e.code, e.trade_date if e.mode == "close" else e.eval_ts)


def classify(fp_a: str | None, fp_b: str | None, *, revised: bool) -> Reason:
    """두 지문 → 사유. 하나라도 모르면 `revised`(입력이 그 뒤 바뀌었을 수 있나)로."""
    if fp_a is not None and fp_b is not None:
        return "logic" if fp_a == fp_b else "data_revised"
    return "data_revised" if revised else "logic"


def compare(
    live: Iterable[MatchEvent],
    backtest: Iterable[MatchEvent],
    *,
    backtest_fp: Callable[[MatchEvent], str | None],
    revised: Callable[[MatchEvent], bool],
) -> list[MatchDiffCalc]:
    """차이 목록(시각·종목 순). `backtest_fp(e)` = 실시간에만 있는 칸의 백테스트 입력 지문(그 칸을
    다시 만들어 본 값 — 없으면 None), `revised(e)` = 실시간 지문이 없는 칸의 입력이 실시간 평가 뒤
    바뀌었을 수 있나."""
    lv = {_key(e): e for e in live}
    bt = {_key(e): e for e in backtest}
    out: list[MatchDiffCalc] = []
    for k, e in lv.items():
        if k not in bt:
            out.append(
                MatchDiffCalc(e.code, e.eval_ts, "live_only",
                              classify(e.input_fp, backtest_fp(e), revised=revised(e)))
            )  # fmt: skip
    for k, e in bt.items():
        if k not in lv:
            out.append(
                MatchDiffCalc(e.code, e.eval_ts, "backtest_only",
                              classify(None, e.input_fp, revised=revised(e)))
            )  # fmt: skip
    out.sort(key=lambda d: (d.eval_ts, d.code, d.side))
    return out


def match_days(
    days: Sequence[date],
    live: Sequence[MatchEvent],
    backtest: Sequence[MatchEvent],
    *,
    backtest_fp: Callable[[MatchEvent], str | None],
    revised: Callable[[MatchEvent], bool],
) -> list[MatchDay]:
    """창의 거래일마다 한 줄(차이가 없어도 — 화면 '최근 20일')."""
    by_l: Mapping[date, list[MatchEvent]] = {d: [] for d in days}
    by_b: Mapping[date, list[MatchEvent]] = {d: [] for d in days}
    for e in live:
        if e.trade_date in by_l:
            by_l[e.trade_date].append(e)
    for e in backtest:
        if e.trade_date in by_b:
            by_b[e.trade_date].append(e)
    out: list[MatchDay] = []
    for d in days:
        diffs = compare(by_l[d], by_b[d], backtest_fp=backtest_fp, revised=revised)
        out.append(
            MatchDay(d, len({_key(e) for e in by_l[d]}), len({_key(e) for e in by_b[d]}),
                     tuple(diffs))
        )  # fmt: skip
    return out
