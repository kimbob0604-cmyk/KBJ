"""미국 일봉 출처 고르기·수정주가 점검 — Nasdaq → (실패) Stooq 폴백(docs/p5_design.md §4.6).

승격 원본: ETF-Traker `board/us/sources.py:history`:301·`SPLIT_PROBE`·`adjust_probe`:319. 바꾼 것:
- ET `history` 는 설정(`sources.history`) 하나만 불렀다(한쪽이 막히면 워크플로가 환경변수로 갈아
  끼웠다 — run.py `us_cfg`). KBJ 는 설정이 고른 출처를 먼저, 실패하면 남은 출처를 순서대로
  부른다(`FALLBACK` — D-P5-18). 지수는 여기서 받지 않는다(Yahoo — `kbj.data.private.yahoo`).
- 받은 행 `source` 에 실제 출처가 남는다(nasdaq·stooq). 어느 출처도 안 되면 각 사유를 이어 예외.
- `adjust_probe` 는 판정하지 않고 사실만 돌려준다(ET 그대로) — 일봉 받는 함수를 주입한다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from itertools import pairwise
from typing import Any, Final

from kbj.data.http import FetchError
from kbj.data.private.nasdaq.client import NasdaqClient, NasdaqThrottled
from kbj.data.private.stooq.client import StooqClient

__all__ = [
    "FALLBACK",
    "SPLIT_PROBE",
    "HistoryError",
    "adjust_probe",
    "adjusted_flag",
    "history",
    "order",
]

FALLBACK: Final[tuple[str, ...]] = ("nasdaq", "stooq")
# 수정주가 여부를 확인할 대조군. 최근 2년 안에 분할이 있었다(ET 원본 그대로).
#   NVDA 2024-06-10 10:1
SPLIT_PROBE: Final = (("NVDA", "2024-06-10", "10:1 분할"),)


class HistoryError(FetchError):
    """모든 출처가 실패했다(출처별 사유를 잇는다)."""

    def __init__(self, ticker: str, reasons: Mapping[str, str]) -> None:
        why = " · ".join(f"{k}: {v}" for k, v in reasons.items())
        super().__init__("NASDAQ", f"daily:{ticker}", None, f"일봉 출처 전부 실패 — {why}"[:400])
        self.reasons = dict(reasons)


def order(cfg: Mapping[str, Any]) -> list[str]:
    """설정이 고른 출처를 맨 앞에, 나머지는 `FALLBACK` 순서. 모르는 이름이면 ValueError."""
    first = (cfg.get("sources") or {}).get("history", "nasdaq")
    if first not in FALLBACK:
        raise ValueError(f"모르는 일봉 소스: {first}")
    return [first, *(s for s in FALLBACK if s != first)]


def history(
    ticker: str,
    cfg: Mapping[str, Any],
    *,
    nasdaq: NasdaqClient | None,
    stooq: StooqClient | None,
    start: date,
    end: date,
    assetclass: str = "stocks",
) -> list[dict[str, Any]]:
    """한 종목의 일봉 — 출처 순서대로 처음 성공한 것. 429 는 폴백하지 않고 올린다(감속 신호)."""
    reasons: dict[str, str] = {}
    for src in order(cfg):
        try:
            if src == "nasdaq":
                if nasdaq is None:
                    reasons[src] = "클라이언트 없음"
                    continue
                return nasdaq.daily(ticker, start, end, assetclass)
            if stooq is None:
                reasons[src] = "클라이언트 없음"
                continue
            return [r for r in stooq.daily(ticker, start) if r["asof"] <= end.isoformat()]
        except NasdaqThrottled:
            raise
        except FetchError as e:
            reasons[src] = e.reason[:160]
    raise HistoryError(ticker, reasons)


def adjust_probe(
    fetch: Callable[[str], Sequence[Mapping[str, Any]]],
) -> list[tuple[str, bool, str]]:
    """일봉이 수정주가인지 본다. 판정하지 말고 **사실만** 돌려준다.

    분할일 근처에 하루 −50% 넘는 계단이 있으면 미조정이다. 미조정이면 그 종목의
    52주 창이 분할 지점에서 잘려(D-001 가드) 한동안 52주 라벨이 안 뜬다.
    어느 쪽인지 알아야 배너에 적을 수 있다.
    """
    out: list[tuple[str, bool, str]] = []
    for tic, when, note in SPLIT_PROBE:
        try:
            rows = list(fetch(tic))
        except FetchError as e:
            out.append((f"수정주가 {tic}", False, e.reason[:120]))
            continue
        step = None
        for a, b in pairwise(rows):
            if a.get("close") and b.get("close"):
                r = b["close"] / a["close"] - 1
                if abs(r) > 0.45:
                    step = (b["asof"], round(r * 100, 1))
        if step:
            out.append(
                (
                    f"수정주가 {tic}", False,
                    f"{step[0]} 하루 {step[1]:+.1f}% 계단 — **미조정**({note}). "
                    "분할 종목은 그 지점부터 룩백이 잘린다",
                )
            )  # fmt: skip
        else:
            out.append(
                (
                    f"수정주가 {tic}", True,
                    f"{when} {note} 근처에 계단 없음 — 수정주가로 보인다 ({len(rows)}행)",
                )
            )  # fmt: skip
    return out


def adjusted_flag(probe: Sequence[tuple[str, bool, str]]) -> bool | None:
    """점검 결과 → daily_bar.adjusted(점검이 없거나 실패면 None — 모른다)."""
    oks = [ok for _, ok, why in probe if "계단" in why]
    return None if not oks else all(oks)
