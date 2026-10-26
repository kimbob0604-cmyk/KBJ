"""cis30 시험용 가짜 KIS 주식일별분봉조회 — (종목, 날짜, 시각) → 그 시각부터 과거로 120개 1분봉.

가격은 (종목, 날짜, 분)으로 정해지는 합성 값. `boom` 날에는 10:00 부터 거래량이 뛰며 오른다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from kbj.core.time import KST
from kbj.data.private.kis.rest import KisResponse
from kbj.data.ratelimit import Priority

MINUTES = [time(9, 0)] + [
    (datetime(2000, 1, 3, 9, 0, tzinfo=KST) + timedelta(minutes=k)).time() for k in range(1, 381)
]  # 09:00 ~ 15:20 연속 + 15:30 은 아래에서 따로
SESSION = [*MINUTES[:381], time(15, 30)]


def price(code: str, day: date, at: time, boom: set[tuple[str, date]]) -> tuple[float, int]:
    m = (at.hour - 9) * 60 + at.minute
    base = 10_000.0
    if (code, day) in boom and m >= 60:
        return base * (1 + 0.0004 * (m - 59)), 20_000  # 10:00 부터 꾸준히 오른다
    return base + (m % 7) - 3, 1_000


@dataclass
class FakeKis:
    boom: set[tuple[str, date]] = field(default_factory=set[tuple[str, date]])
    fail: set[tuple[str, date]] = field(default_factory=set[tuple[str, date]])
    calls: list[tuple[str, str, str]] = field(default_factory=list[tuple[str, str, str]])
    closed: bool = False

    def get(
        self, path: str, tr_id: str, params: dict[str, str], tr_cont: str = "", *,
        priority: Priority = Priority.P2, timeout: float | None = None,
    ) -> KisResponse:  # fmt: skip
        code, ymd, hour = (
            params["FID_INPUT_ISCD"],
            params["FID_INPUT_DATE_1"],
            params["FID_INPUT_HOUR_1"],
        )
        self.calls.append((code, ymd, hour))
        day = date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:]))
        if (code, day) in self.fail:
            return KisResponse(200, {"rt_cd": "1", "msg_cd": "OPSQ0001", "msg1": "x"}, 1.0)
        upto = time(int(hour[:2]), int(hour[2:4]), int(hour[4:]))
        mins = [t for t in SESSION if t <= upto][::-1][:120]
        rows: list[dict[str, Any]] = []
        for t in mins:
            p, v = price(code, day, t, self.boom)
            rows.append({
                "stck_bsop_date": ymd, "stck_cntg_hour": t.strftime("%H%M%S"),
                "stck_oprc": f"{p:.0f}", "stck_hgpr": f"{p + 5:.0f}", "stck_lwpr": f"{p - 5:.0f}",
                "stck_prpr": f"{p:.0f}", "cntg_vol": str(v), "acml_tr_pbmn": "0",
            })  # fmt: skip
        if len(rows) < 120:  # 과거 포함 — 앞 거래일 봉이 섞여 온다
            prev = (day - timedelta(days=1)).strftime("%Y%m%d")
            rows.append(
                {
                    "stck_bsop_date": prev,
                    "stck_cntg_hour": "152000",
                    "stck_oprc": "1",
                    "stck_hgpr": "1",
                    "stck_lwpr": "1",
                    "stck_prpr": "1",
                    "cntg_vol": "1",
                }
            )
        return KisResponse(
            200, {"rt_cd": "0", "msg_cd": "MCA00000", "output1": {}, "output2": rows}, 1.0
        )

    def close(self) -> None:
        self.closed = True
