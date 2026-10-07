"""선물 분봉 이어 가기·만기 지난 종목·휴장 앞 금요일 밤.

docs/phase1_design.md §8·§12, PLAN §3 #17 후속.

설계 §8 은 다음 조회를 '직전 응답의 가장 이른 봉 − 1분'부터 이어 가는데, 23:59:59·05:59:59
이외 기준 시각의 동작(24~30시 구간의 입력 시각 표기 포함)이 미실측이다. 이 probe 는

1. 주간(`F`)·야간 18~24시(`CM`)에서 가장 이른 봉 − 1분으로 이어 부르면 겹침·빈틈 없이 이어지는지
2. 야간 24~30시(`CM`)를 이어 부를 때 입력 표기 후보 셋 — (다음 날, 달력 시각 04:03),
   (다음 날, 확장 28:03), (야간 시작일, 확장 28:03) — 중 무엇이 앞 봉을 주는지
3. 만기 지난 근월물 코드(A01609·A01606·A01603·A01512)의 주간 봉이 오는지(분봉 백필 가능 여부)
4. 월요일 휴장 앞 금요일 밤(2026-05-22·08-14, 월 05-25·08-17 휴장)에 야간장이 열렸는지
   — 대조군은 같은 코드의 목요일 밤

을 본다. 모두 과거 봉 조회(REST 약 16건, 0.6초 간격)다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from scripts.probe_common import Ctx, futures_codes, now_kst, rows
from scripts.probe_minute_history import bar_stamps, fetch_resp

# 월요일 휴장 앞 금요일 밤 (월 05-25 부처님오신날 대체·08-17 광복절 대체 — XKRX 휴장 확인)
# 과 대조군 목요일 밤. 코드는 그때의 근월물
FRIDAY_NIGHTS = (
    ("A01606", date(2026, 5, 22), date(2026, 5, 21)),
    ("A01609", date(2026, 8, 14), date(2026, 8, 13)),
)
# 만기 지난 근월물 — 만기 전 거래일 하나씩
EXPIRED = (
    ("A01609", date(2026, 9, 1)),
    ("A01606", date(2026, 6, 1)),
    ("A01603", date(2026, 3, 3)),
    ("A01512", date(2025, 12, 1)),
)


def minus_one_minute(hhmmss: str) -> str:
    """HHMMSS 에서 1분 뺀 값. 확장 표기(24~30시)는 그대로 확장 표기로 둔다."""
    total = int(hhmmss[:2]) * 60 + int(hhmmss[2:4]) - 1
    return f"{total // 60:02d}{total % 60:02d}{hhmmss[4:6]}"


def to_clock(hhmmss: str) -> str:
    """확장 표기 28:03:00 → 달력 시각 04:03:00."""
    return f"{int(hhmmss[:2]) % 24:02d}{hhmmss[2:]}"


def query(ctx: Ctx, code: str, d: date, hh: str, market: str) -> dict[str, Any]:
    r = fetch_resp(ctx, code, d, hh, market)
    st = bar_stamps(rows(r.body, "output2"))
    return {
        "input": f"{market} {code} {d:%Y%m%d} {hh}",
        "rt_cd": r.rt_cd,
        "msg_cd": r.msg_cd,
        "msg1": r.body.get("msg1"),
        "bars": len(st),
        "latest": max(st) if st else None,
        "earliest": min(st) if st else None,
    }


def continues(first: dict[str, Any], nxt: dict[str, Any]) -> dict[str, Any]:
    """nxt 가 first 의 가장 이른 봉 바로 앞에서 끝나는가 (겹침 없음 + 1분 이내 빈틈)."""
    e, lt = first["earliest"], nxt["latest"]
    if not e or not lt:
        return {"ok": False, "why": "봉 없음"}
    if tuple(lt) >= tuple(e):
        return {"ok": False, "why": f"겹침 {lt} >= {e}"}
    same_day = lt[0] == e[0]
    gap_min = (
        (int(e[1][:2]) * 60 + int(e[1][2:4])) - (int(lt[1][:2]) * 60 + int(lt[1][2:4]))
        if same_day
        else None
    )
    return {"ok": same_day and gap_min is not None and gap_min >= 1, "gap_min": gap_min}


def run(ctx: Ctx) -> None:
    codes = futures_codes(ctx)
    if not codes:
        raise RuntimeError("선물 코드를 못 찾았다")
    code = codes[0]
    today = now_kst().date()
    ctx.findings["code"] = code

    # 1a. 주간 F — 오늘(장 마감 뒤) 봉의 가장 이른 봉 − 1분으로 이어 부른다
    day0 = query(ctx, code, today, "160000", "F")
    day: dict[str, Any] = {"first": day0}
    if day0["earliest"]:
        d0 = date.fromisoformat(
            f"{day0['earliest'][0][:4]}-{day0['earliest'][0][4:6]}-{day0['earliest'][0][6:]}"
        )
        day["next"] = query(ctx, code, d0, minus_one_minute(day0["earliest"][1]), "F")
        day["continues"] = continues(day0, day["next"])
    ctx.findings["day_continuation"] = day

    # 1b·2. 어젯밤(야간 시작일 N = 오늘의 앞 거래일 — 최근 봉 날짜로 찾는다)
    n0 = query(ctx, code, today, "055959", "CM")  # 24~30시: 오늘 05:59:59 → 앞 거래일 밤
    night: dict[str, Any] = {"late_first": n0}
    if n0["earliest"]:
        nd = n0["earliest"][0]
        night_start = date.fromisoformat(f"{nd[:4]}-{nd[4:6]}-{nd[6:]}")
        ext = minus_one_minute(n0["earliest"][1])  # 확장 표기 (예: 280300)
        night["late_candidates"] = {
            "next_day_clock": query(ctx, code, today, to_clock(ext), "CM"),
            "next_day_extended": query(ctx, code, today, ext, "CM"),
            "start_day_extended": query(ctx, code, night_start, ext, "CM"),
        }
        night["late_continues"] = {k: continues(n0, v) for k, v in night["late_candidates"].items()}
        e0 = query(ctx, code, night_start, "235959", "CM")  # 18~24시
        night["early_first"] = e0
        if e0["earliest"]:
            night["early_next"] = query(
                ctx, code, night_start, minus_one_minute(e0["earliest"][1]), "CM"
            )
            night["early_continues"] = continues(e0, night["early_next"])
    ctx.findings["night_continuation"] = night

    # 3. 만기 지난 근월물 주간 봉
    ctx.findings["expired_codes"] = {
        f"{c} {d}": query(ctx, c, d, "160000", "F") for c, d in EXPIRED
    }

    # 4. 월요일 휴장 앞 금요일 밤 vs 목요일 밤 (18~24시 봉의 날짜가 그 날인가)
    fri: dict[str, Any] = {}
    for c, friday, thursday in FRIDAY_NIGHTS:
        for d in (friday, thursday):
            q = query(ctx, c, d, "235959", "CM")
            q["night_of_that_date"] = bool(q["latest"]) and q["latest"][0] == f"{d:%Y%m%d}"
            fri[f"{c} {d} ({d:%a})"] = q
    ctx.findings["friday_before_holiday"] = fri

    ctx.note(
        "주간 이어 가기 "
        + str(day.get("continues"))
        + " · 야간 18~24시 "
        + str(night.get("early_continues"))
        + " · 24~30시 후보 "
        + str(night.get("late_continues"))
    )
    ctx.note(
        "만기 지난 코드 봉 수 "
        + str({k: v["bars"] for k, v in ctx.findings["expired_codes"].items()})
    )
    ctx.note(
        "휴장 앞 금요일 밤 "
        + str({k: (v["night_of_that_date"], v["latest"]) for k, v in fri.items()})
    )


def run_followup(ctx: Ctx) -> None:
    """15:52 런 후속.

    24~30시 첫 조회를 (야간 시작일, 확장 300000·295959)로 하면 되는지, 확장 시각 이어 가기가
    24:00 을 넘어 18~24시로 한 줄로 이어지는지, (오늘, 055959) 가 또 엉뚱한 밤을 주는지.
    """
    code = futures_codes(ctx)[0]
    today = now_kst().date()
    start = date(2026, 9, 28)  # 월 밤(화 귀속) — 15:52 런에서 확장 표기로 봉이 온 밤
    qs = {
        "start_300000": query(ctx, code, start, "300000", "CM"),
        "start_295959": query(ctx, code, start, "295959", "CM"),
        "start_240400": query(ctx, code, start, "240400", "CM"),
        "today_055959_again": query(ctx, code, today, "055959", "CM"),
    }
    ctx.findings["followup"] = qs
    ctx.note("후속 " + str({k: (v["bars"], v["earliest"], v["latest"]) for k, v in qs.items()}))
