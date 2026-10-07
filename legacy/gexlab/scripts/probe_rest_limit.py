"""KIS REST 초당 호출 한도 실측 (PLAN §3 #10).

가벼운 조회(선물 현재가)를 목표 초당 호출 수 단계별로 2초씩 쏜다. 러너가 해외라
왕복이 길어서 순차 호출로는 목표 속도가 안 나오므로 스레드로 예약 시각에 보낸다.

같은 앱키를 쓰는 다른 작업이 동시에 돌면 결과가 낮게 나온다.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from data.kis.rest import KisResponse
from scripts.probe_common import (
    P_PRICE,
    TR_PRICE,
    BurstLevel,
    Ctx,
    futures_codes,
    max_clean_rps,
)

LEVELS = (5, 10, 15, 18, 20, 22, 25, 30)
SECONDS = 2.0


def burst(ctx: Ctx, code: str, rps: int) -> BurstLevel:
    n = int(rps * SECONDS)
    params = {"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": code}
    start = time.monotonic() + 0.2

    def one(i: int) -> KisResponse:
        delay = start + i / rps - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        return ctx.kis.get(P_PRICE, TR_PRICE, params)

    with ThreadPoolExecutor(max_workers=min(n, 32)) as ex:
        results = list(ex.map(one, range(n)))
    wall = time.monotonic() - start
    ok = sum(1 for r in results if r.ok)
    lim = sum(1 for r in results if r.rate_limited)
    return BurstLevel(rps, n, ok, lim, n - ok - lim, round(wall, 2))


def run(ctx: Ctx) -> None:
    codes = futures_codes(ctx)
    if not codes:
        raise RuntimeError("선물 코드를 못 찾았다")
    code = codes[0]
    probe = ctx.kis.get(P_PRICE, TR_PRICE, {"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": code})
    ctx.findings["single_call"] = {
        "code": code,
        "rt_cd": probe.rt_cd,
        "msg1": probe.body.get("msg1"),
        "ms": round(probe.elapsed_ms),
    }
    if not probe.ok:
        raise RuntimeError(f"현재가 단건 호출 실패: {probe.body.get('msg1')}")
    levels: list[BurstLevel] = []
    for rps in LEVELS:
        time.sleep(2.0)  # 앞 단계의 1초 창이 비도록
        lv = burst(ctx, code, rps)
        levels.append(lv)
        ctx.note(
            f"{rps}/s 목표: 보냄 {lv.sent} 성공 {lv.ok} 한도초과 {lv.rate_limited} "
            f"기타오류 {lv.other_error} 실제 {lv.achieved_rps:.1f}/s"
        )
        if lv.rate_limited > lv.sent // 2:
            break  # 절반 넘게 걸리면 더 올릴 이유가 없다
    ctx.findings["levels"] = [
        asdict(lv) | {"achieved_rps": round(lv.achieved_rps, 1)} for lv in levels
    ]
    ctx.findings["max_clean_rps"] = max_clean_rps(levels)
    ctx.note(f"한도초과 없이 통과한 최고 단계: {ctx.findings['max_clean_rps']}/s")
