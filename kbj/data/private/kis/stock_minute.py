"""KIS 주식일별분봉조회(FHKST03010230) — 파라미터·파서·5분 묶기.

등록부 작업이 아니라 수동 CLI(`kbj.sh cis30 fetch`)만 부르므로 카탈로그(`datasets.py`)에
넣지 않는다 — 선점 대상이 아니고, 리미터(앱키 `kis`, P4)는 그대로 탄다.

공식 예제(koreainvestment/open-trading-api `inquire_time_dailychartprice`): 실전 1회 최대 120봉,
입력
(날짜, 시각)부터 과거로, **최대 1년 보관**. 시장 J(KRX)·NX(NXT)·UN(통합) — 여기서는 J 만 쓴다.
output2 칸 이름(`stck_bsop_date`·`stck_cntg_hour`·`stck_oprc`·`stck_hgpr`·`stck_lwpr`·`stck_prpr`·
`cntg_vol`)과 봉 시각이 '시작'인지 '끝'인지는 **[실측 필요 — `kbj.sh minutes probe`]**. 5분 묶기는
분을
5로 내림하므로 어느 쪽이든 1분 안에서만 어긋난다.

하루 받기: (D, 153000) → 받은 가장 이른 봉 − 1분 → … 09:00 에 닿거나 새 봉이 없으면 끝(약 4회). 과거
데이터 포함(Y)이라 앞 거래일 봉이 섞여 오면 버린다(날짜가 D 인 것만).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Final

from kbj.core.intraday_rows import IntraBar
from kbj.core.time import KST
from kbj.data.private.kis.parse import KisParseError, kis_float, kis_int, rows_of, ymd

__all__ = [
    "DAY_END",
    "DAY_START",
    "STOCK_MINUTE_PATH",
    "STOCK_MINUTE_TR",
    "MinuteRow",
    "earlier_hour",
    "parse_minutes",
    "stock_minute_params",
    "to_5m",
]

STOCK_MINUTE_PATH: Final = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
STOCK_MINUTE_TR: Final = "FHKST03010230"
DAY_START: Final = time(9, 0)
DAY_END: Final = time(15, 30)  # 장 마감 동시호가 봉까지


@dataclass(frozen=True)
class MinuteRow:
    day: date
    at: time
    open: float
    high: float
    low: float
    close: float
    volume: int


def stock_minute_params(code: str, day: date, hour: str) -> dict[str, str]:
    if len(hour) != 6 or not hour.isdigit():
        raise ValueError(f"시각은 HHMMSS 6자리: {hour!r}")
    return {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_HOUR_1": hour,
        "FID_INPUT_DATE_1": day.strftime("%Y%m%d"),
        "FID_PW_DATA_INCU_YN": "Y",
        "FID_FAKE_TICK_INCU_YN": "",
    }


def _hms(v: object) -> time | None:
    s = str(v or "").strip()
    if len(s) != 6 or not s.isdigit():
        return None
    h, m, sec = int(s[:2]), int(s[2:4]), int(s[4:])
    if h > 23 or m > 59 or sec > 59:
        return None
    return time(h, m, sec)


def parse_minutes(body: Mapping[str, Any]) -> list[MinuteRow]:
    """output2 → 1분봉(받은 순서 그대로 — KIS 는 최근 봉 먼저). 날짜·시각·가격 없는 행은 버린다."""
    out: list[MinuteRow] = []
    for r in rows_of(body, "output2", what="주식 분봉"):
        d, t = ymd(r.get("stck_bsop_date")), _hms(r.get("stck_cntg_hour"))
        try:
            o, h = kis_float(r.get("stck_oprc")), kis_float(r.get("stck_hgpr"))
            lo, c = kis_float(r.get("stck_lwpr")), kis_float(r.get("stck_prpr"))
            v = kis_int(r.get("cntg_vol"))
        except KisParseError:
            continue
        if d is None or t is None or not (o and h and lo and c) or min(o, h, lo, c) <= 0:
            continue
        out.append(MinuteRow(d, t, o, h, lo, c, v or 0))
    return out


def earlier_hour(at: time) -> str:
    """다음 조회 시각 — 받은 가장 이른 봉 − 1분(HHMMSS)."""
    t = (datetime.combine(date(2000, 1, 3), at) - timedelta(minutes=1)).time()
    return t.strftime("%H%M%S")


def to_5m(code: str, rows: Sequence[MinuteRow], day: date) -> list[IntraBar]:
    """그날 정규장(09:00~15:30) 1분봉 → 5분봉(봉 시작 시각, KST). 겹친 분은 한 번만 센다."""
    seen: dict[time, MinuteRow] = {}
    for r in rows:
        if r.day == day and DAY_START <= r.at <= DAY_END:
            seen.setdefault(r.at.replace(second=0), r)
    buckets: dict[int, list[MinuteRow]] = {}
    for at in sorted(seen):
        m = at.hour * 60 + at.minute
        buckets.setdefault(m - m % 5, []).append(seen[at])
    out: list[IntraBar] = []
    for m, rs in sorted(buckets.items()):
        ts = datetime.combine(day, time(m // 60, m % 60), KST)
        out.append(
            IntraBar(code=code, ts=ts, trade_date=day, open=rs[0].open,
                     high=max(r.high for r in rs), low=min(r.low for r in rs), close=rs[-1].close,
                     volume=sum(r.volume for r in rs), minutes=len(rs))
        )  # fmt: skip
    return out
