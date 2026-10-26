"""주식 분봉 받기 — KIS 주식일별분봉조회(FHKST03010230) → 5분봉(`prv_market.stock_bar_5m`).

cis 30분봉 백테스트(`kbj.sh cis30`)가 부른다. 전 종목이 아니라 일봉 신호 종목의 (종목, 거래일)
묶음만
받는다(앱키 리미터 4/s 를 다른 수집과 나눠 쓴다 — 우선순위 P4, 장 마감 뒤에 돌린다).

- 하루(`fetch_day`): (D, 153000) 부터 받은 가장 이른 봉 − 1분으로 이어 가며 09:00 에 닿거나 새 봉이
  없을 때까지(약 4회, 상한 `MAX_CALLS`). 그날 봉만 5분으로 묶는다.
- 묶음(`collect`): 오래된 날부터(KIS 가 1년 넘은 분봉을 지우므로). 이미 표에 있는 (종목, 날)은
  부르는
  쪽이 빼서 준다 — 끊겨도 다시 돌리면 남은 것만 받는다. 날마다 바로 쓴다.
- 리미터 대기 초과·5xx 는 잠깐 쉬고 그 날을 다시(최대 `RETRIES`), KIS 거절(형식·종목)은 세고
  넘어간다.
- 응답 본문은 저장하지 않는다(5분봉만). 토큰·앱키는 출력하지 않는다(클라이언트가 가린다).
"""

from __future__ import annotations

import time as _time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import IO, Any, Final, Protocol

from kbj.core.intraday_rows import IntraBar
from kbj.core.time import KST
from kbj.data.private.kis.rest import KisResponse
from kbj.data.private.kis.stock_minute import (
    DAY_START,
    STOCK_MINUTE_PATH,
    STOCK_MINUTE_TR,
    earlier_hour,
    parse_minutes,
    stock_minute_params,
    to_5m,
)
from kbj.data.ratelimit import Priority, RateLimitTimeout

__all__ = ["LOADED_BY", "MAX_CALLS", "Collected", "DayResult", "collect", "fetch_day", "probe"]

LOADED_BY: Final = "stock_minutes"
MAX_CALLS: Final = 6  # 하루 381분 ÷ 120 = 4회 + 여유
RETRIES: Final = 3
WAIT_S: Final = 5.0
LIMIT_TIMEOUT_S: Final = 60.0
CLOSE_HOUR: Final = "153000"


class KisGetter(Protocol):
    def get(
        self, path: str, tr_id: str, params: dict[str, str], tr_cont: str = "", *,
        priority: Priority = ..., timeout: float | None = ...,
    ) -> KisResponse: ...  # fmt: skip


class MinuteSink(Protocol):
    def put(self, rows: Sequence[IntraBar], *, loaded_by: str, received_at: datetime) -> int: ...


@dataclass(frozen=True)
class DayResult:
    bars: list[IntraBar]
    calls: int
    error: str | None = None  # KIS 거절(msg_cd) — 다시 해도 같을 것
    retry: bool = False  # 잠깐 뒤 다시 하면 될 것(5xx·한도·리미터 대기)


def _get(kis: KisGetter, code: str, day: date, hour: str) -> KisResponse:
    return kis.get(STOCK_MINUTE_PATH, STOCK_MINUTE_TR, stock_minute_params(code, day, hour),
                   priority=Priority.P4, timeout=LIMIT_TIMEOUT_S)  # fmt: skip


def fetch_day(kis: KisGetter, code: str, day: date) -> DayResult:
    hour, calls = CLOSE_HOUR, 0
    earliest = time(23, 59, 59)
    rows: list[Any] = []
    while calls < MAX_CALLS:
        try:
            resp = _get(kis, code, day, hour)
        except RateLimitTimeout:
            return DayResult([], calls, "리미터 대기 초과", retry=True)
        calls += 1
        if not resp.ok:
            retry = resp.status >= 500 or resp.rate_limited
            return DayResult([], calls, f"HTTP {resp.status} {resp.msg_cd}".strip(), retry=retry)
        new = [r for r in parse_minutes(resp.body) if r.day == day and r.at < earliest]
        if not new:
            break
        rows += new
        earliest = min(r.at for r in new)
        if earliest <= DAY_START:
            break
        hour = earlier_hour(earliest)
    return DayResult(to_5m(code, rows, day), calls)


@dataclass
class Collected:
    days: int = 0  # 봉을 받아 쓴 (종목, 날)
    empty: int = 0  # 그날 봉이 없음(정지·보관 기간 밖)
    bars: int = 0
    calls: int = 0
    errors: Counter[str] = field(default_factory=Counter[str])


def collect(
    kis: KisGetter, sink: MinuteSink, pairs: Sequence[tuple[str, date]], *,
    out: IO[str] | None = None, every: int = 200, sleep: Callable[[float], None] = _time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(KST),
) -> Collected:  # fmt: skip
    """(종목, 날) 묶음을 차례로 받아 쓴다. 진행은 every 개마다 한 줄."""
    got = Collected()
    t0 = _time.monotonic()
    for k, (code, day) in enumerate(pairs, 1):
        res = fetch_day(kis, code, day)
        tries = 0
        while res.retry and tries < RETRIES:
            tries += 1
            sleep(WAIT_S * tries)
            res = fetch_day(kis, code, day)
        got.calls += res.calls
        if res.error:
            got.errors[res.error] += 1
        elif res.bars:
            got.bars += sink.put(res.bars, loaded_by=LOADED_BY, received_at=now())
            got.days += 1
        else:
            got.empty += 1
        if out is not None and (k % every == 0 or k == len(pairs)):
            el = _time.monotonic() - t0
            eta = el / k * (len(pairs) - k)
            out.write(
                f"  {k:,}/{len(pairs):,} · 받음 {got.days:,} · 빈 날 {got.empty:,} · 오류 "
                f"{sum(got.errors.values()):,} · 호출 {got.calls:,} · "
                f"남은 시간 약 {eta / 60:.0f}분\n"
            )
            out.flush()
    return got


def probe(kis: KisGetter, code: str, days: Sequence[date], out: IO[str]) -> None:
    """한 종목의 여러 날짜를 한 번씩(장 마감 시각부터 120봉) — 보관 기간·칸 이름·봉 시각 확인.

    값(가격)은 찍지 않는다 — 날짜·개수·시각 범위·칸 이름만."""
    shown_keys = False
    for d in days:
        try:
            resp = _get(kis, code, d, CLOSE_HOUR)
        except RateLimitTimeout:
            out.write(f"{d}: 리미터 대기 초과\n")
            continue
        if not resp.ok:
            out.write(f"{d}: 실패 HTTP {resp.status} {resp.msg_cd}\n")
            continue
        raw = resp.body.get("output2") or []
        if not shown_keys and isinstance(raw, list) and raw and isinstance(raw[0], dict):
            keys = ",".join(sorted(str(k) for k in raw[0]))  # pyright: ignore[reportUnknownArgumentType,reportUnknownVariableType]
            out.write(f"칸 이름: {keys}\n")
            shown_keys = True
        rows = parse_minutes(resp.body)
        same = [r for r in rows if r.day == d]
        other = sorted({r.day for r in rows if r.day != d})
        span = f"{min(r.at for r in same)}~{max(r.at for r in same)}" if same else "—"
        out.write(
            f"{d}: 응답 {len(raw) if isinstance(raw, list) else 0}행 · 그날 {len(same)}봉 {span}"
            + (f" · 다른 날 {', '.join(map(str, other))}" if other else "")
            + "\n"
        )
