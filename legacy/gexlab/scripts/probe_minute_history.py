"""선물 분봉 과거 조회 범위 (PLAN §3 #17).

근월물 코드로 날짜를 과거로 점프해 가며 그 날짜의 봉이 오는지 본다. 봉이 오는
가장 오래된 날짜를 이분 탐색으로 좁힌다. 종목 상장일보다 앞은 원래 없으니 결과는
'이 종목 기준 하한'이다. 야간 봉(18:00~06:00)이 섞여 오는지도 함께 본다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from data.kis.rest import KisResponse
from scripts.probe_common import (
    P_MINUTE,
    TR_MINUTE,
    Ctx,
    find_key,
    futures_codes,
    last_business_days,
    now_kst,
    rows,
)

# 야간 봉 시각. 시장구분 CM 은 야간 시작일 날짜에 24~30시 확장 표기로 준다
# (2026-09-28 실측: 09-22 밤 봉이 '20260922 240000'·'20260922 300000')
NIGHT_HOURS = {f"{h:02d}" for h in (*range(18, 31), *range(0, 6))}
# 시장구분별 하루 조회 기준 시각 — CM 은 밤이라 23:59 로 본다
DAY_END = {"F": "160000", "CM": "235959"}
# 야간 봉을 볼 시장구분. F(지수선물)에 야간 봉이 안 섞여 오면 야간선물 코드 후보(CM)가
# 받아지는지도 잰다
NIGHT_MARKETS = ("F", "CM")


def fetch(
    ctx: Ctx, code: str, d: date, hour: str = "160000", market: str = "F"
) -> list[dict[str, Any]]:
    return rows(fetch_resp(ctx, code, d, hour, market).body, "output2")


def fetch_resp(ctx: Ctx, code: str, d: date, hour: str, market: str) -> KisResponse:
    r = ctx.call(
        P_MINUTE,
        TR_MINUTE,
        {
            "FID_COND_MRKT_DIV_CODE": market,
            "FID_INPUT_ISCD": code,
            "FID_HOUR_CLS_CODE": "60",
            "FID_PW_DATA_INCU_YN": "Y",
            "FID_FAKE_TICK_INCU_YN": "N",
            "FID_INPUT_DATE_1": d.strftime("%Y%m%d"),
            "FID_INPUT_HOUR_1": hour,
        },
        min_gap=0.6,
    )
    if "minute_first_response" not in ctx.findings:
        ctx.findings["minute_first_response"] = {
            "rt_cd": r.rt_cd,
            "msg1": r.body.get("msg1"),
            "output1": r.body.get("output1"),
            "output2_head": rows(r.body, "output2")[:3],
            "output2_rows": len(rows(r.body, "output2")),
        }
    return r


def bar_dates(rs: list[dict[str, Any]]) -> list[str]:
    if not rs:
        return []
    k = find_key(rs[0], "bsop_date") or find_key(rs[0], "date")
    return [str(r.get(k, "")) for r in rs] if k else []


def bar_stamps(rs: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(날짜 YYYYMMDD, 시각 HHMMSS) 목록."""
    if not rs:
        return []
    kd = find_key(rs[0], "bsop_date") or find_key(rs[0], "date")
    kh = find_key(rs[0], "cntg_hour") or find_key(rs[0], "hour")
    if not kd or not kh:
        return []
    return [(str(r.get(kd, "")), str(r.get(kh, ""))) for r in rs]


def night_bars(stamps: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(d, h) for d, h in stamps if h[:2] in NIGHT_HOURS]


def pick_night_pair(candidates: list[date], traded: Callable[[date], bool]) -> date | None:
    """d 와 d+1(달력상 다음 날)이 모두 거래일인 가장 최근 d.

    그 사이 밤엔 야간장이 열렸다고 볼 수 있다. 휴장 전날 밤은 야간장이 열렸는지
    모르므로 쓰지 않는다 (09-28 런은 휴장일 09-24·25 를 골라 판정을 못 했다).
    """
    for d in sorted(candidates, reverse=True):
        if traded(d) and traded(d + timedelta(days=1)):
            return d
    return None


def has_bars_near(ctx: Ctx, code: str, d: date, market: str = "F") -> bool:
    """d 이전 7일 안의 봉이 오면 참 (d 가 휴장일이어도 앞 거래일 봉이 온다)."""
    ds = bar_dates(fetch(ctx, code, d, DAY_END.get(market, "160000"), market))
    lo = (d - timedelta(days=7)).strftime("%Y%m%d")
    return any(lo <= x <= d.strftime("%Y%m%d") for x in ds)


def run(ctx: Ctx) -> None:
    codes = futures_codes(ctx)
    if not codes:
        raise RuntimeError("선물 코드를 못 찾았다")
    code = codes[0]
    today = now_kst().date()
    rs = fetch(ctx, code, today)
    hours = sorted(
        {str(r.get(find_key(r, "cntg_hour") or find_key(r, "hour") or "", ""))[:2] for r in rs}
    )
    ctx.findings["code"] = code
    ctx.findings["today_hours_seen"] = hours
    # 야간 봉: 이틀 연속 거래일(d, d+1)을 찾아 그 사이 밤을 23:59·05:59 로 직접 조회한다
    seen: dict[date, bool] = {}

    def traded(d: date) -> bool:
        """d 의 주간 봉이 오는가 — 응답의 가장 최근 봉 날짜가 d 인가."""
        if d not in seen:
            st = bar_stamps(rs if d == today else fetch(ctx, code, d))
            seen[d] = bool(st) and max(x for x, _ in st) == d.strftime("%Y%m%d")
        return seen[d]

    pair = pick_night_pair(last_business_days(today, 10), traded)
    ctx.findings["night_pair"] = pair.isoformat() if pair else None
    ctx.findings["traded_checked"] = {d.isoformat(): v for d, v in sorted(seen.items())}
    night: dict[str, Any] = {}
    if pair is not None:
        for market in NIGHT_MARKETS:
            for d, hh in ((pair, "235959"), (pair + timedelta(days=1), "055959")):
                r = fetch_resp(ctx, code, d, hh, market)
                st = bar_stamps(rows(r.body, "output2"))
                night[f"{market} {d:%Y%m%d} {hh}"] = {
                    "rt_cd": r.rt_cd,
                    "msg_cd": r.msg_cd,
                    "msg1": r.body.get("msg1"),
                    "bars": len(st),
                    "night_bars": len(night_bars(st)),
                    "first": st[:1],
                    "last": st[-1:],
                }
    ctx.findings["night_queries"] = night
    ctx.findings["night_bars_present"] = any(v["night_bars"] for v in night.values())
    ctx.note(
        f"야간 봉 조회 짝 {pair}: "
        + "; ".join(
            f"{k} rt_cd={v['rt_cd']} {v['bars']}봉 야간 {v['night_bars']} 최신 {v['first']}"
            for k, v in night.items()
        )
    )

    ranges = {m: past_range(ctx, code, today, m) for m in NIGHT_MARKETS}
    ctx.findings["ranges"] = ranges
    ctx.findings.update(ranges["F"])  # 13:25·13:33 런과 같은 키 (F 기준)
    for m, v in ranges.items():
        floor = v.get("earliest_date", v["earliest_days_back"])
        ctx.note(f"{code} {m}: 분봉 과거 하한 {floor} · 점프 {v['jumps']}")
    ctx.note(f"오늘 시간대 {hours}")


def past_range(ctx: Ctx, code: str, today: date, market: str) -> dict[str, Any]:
    """봉이 오는 가장 오래된 날짜 — 점프로 경계를 찾고 이분 탐색으로 좁힌다(±3일)."""
    out: dict[str, Any] = {}
    jumps: dict[str, bool] = {}
    ok_days, bad_days = 0, None
    for days in (7, 30, 90, 180, 365, 730, 1095):
        hit = has_bars_near(ctx, code, today - timedelta(days=days), market)
        jumps[f"D-{days}"] = hit
        if hit:
            ok_days = days
        else:
            bad_days = days
            break
    out["jumps"] = jumps
    if bad_days is not None:
        lo, hi = ok_days, bad_days  # lo 는 되고 hi 는 안 된다
        while hi - lo > 3:
            mid = (lo + hi) // 2
            if has_bars_near(ctx, code, today - timedelta(days=mid), market):
                lo = mid
            else:
                hi = mid
        out["earliest_days_back"] = lo
        out["earliest_date"] = (today - timedelta(days=lo)).isoformat()
    else:
        out["earliest_days_back"] = f">={ok_days}"
    return out
