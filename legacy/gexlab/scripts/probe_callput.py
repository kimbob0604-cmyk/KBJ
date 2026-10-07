"""전광판 콜/풋 — 행 수 한도·연속조회, 위클리 파라미터, 1초 1건 권장 (PLAN §3 #11, #12)."""

from __future__ import annotations

import time
from typing import Any

from scripts.probe_common import P_CALLPUT, TR_CALLPUT, Ctx, brief, classify_mtrt, now_kst, rows
from scripts.probe_option_list import alt_code, expiries


def callput_params(mtrt: str, cls: str) -> dict[str, str]:
    return {
        "FID_COND_MRKT_DIV_CODE": "O",
        "FID_COND_SCR_DIV_CODE": "20503",
        "FID_MRKT_CLS_CODE": "CO",
        "FID_MTRT_CNT": mtrt,
        "FID_MRKT_CLS_CODE1": "PO",
        "FID_COND_MRKT_CLS_CODE": cls,
    }


def targets(ctx: Ctx) -> list[tuple[str, str]]:
    """(시장구분, 만기코드) — 월물 최근 2개, 위클리 월·목 최근 1개씩."""
    ex = expiries(ctx)
    monthly = ex.get("(blank)") or ex.get("K21") or [now_kst().strftime("%Y%m")]
    out = [("", m) for m in monthly[:2]]
    for cls in ("WKM", "WKI"):
        if ex.get(cls):
            out.append((cls, ex[cls][0]))
    return out


def strike_summary(rs: list[dict[str, Any]]) -> dict[str, Any]:
    ks: list[float] = []
    for r in rs:
        try:
            ks.append(float(r.get("acpr", "")))
        except (TypeError, ValueError):
            continue
    atm = [r.get("acpr") for r in rs if str(r.get("atm_cls_name", "")).strip().upper() == "ATM"]
    oi = sum(1 for r in rs if str(r.get("hts_otst_stpl_qty", "0")).strip() not in ("", "0"))
    return {
        "n": len(rs),
        "strike_min": min(ks) if ks else None,
        "strike_max": max(ks) if ks else None,
        "atm_rows": atm,
        "rows_with_oi": oi,
    }


def run(ctx: Ctx) -> None:
    res: dict[str, Any] = {}
    for cls, mtrt in targets(ctx):
        label = f"{cls or '월물'}:{mtrt}"
        r = ctx.call(P_CALLPUT, TR_CALLPUT, callput_params(mtrt, cls))
        d = brief(r, ("output1", "output2"))
        c, p = rows(r.body, "output1"), rows(r.body, "output2")
        d["calls"], d["puts"] = strike_summary(c), strike_summary(p)
        d["mtrt_format"] = classify_mtrt(mtrt)
        # 6자리가 0행이면 4자리 코드로 한 번 대조한다 — 어느 쪽이 맞는지 기록
        alt = alt_code(ctx, cls, mtrt)
        if not c and not p and alt:
            ra = ctx.call(P_CALLPUT, TR_CALLPUT, callput_params(alt, cls))
            d["alt_code"] = {
                "mtrt": alt,
                "rt_cd": ra.rt_cd,
                "calls": strike_summary(rows(ra.body, "output1")),
                "puts": strike_summary(rows(ra.body, "output2")),
            }
        # 연속조회: tr_cont 가 M/F 이면 다음 페이지를 한 번 더 청해 본다
        if r.tr_cont in ("M", "F"):
            r2 = ctx.call(P_CALLPUT, TR_CALLPUT, callput_params(mtrt, cls), tr_cont="N")
            d["next_page"] = {
                "tr_cont": r2.tr_cont,
                "calls": strike_summary(rows(r2.body, "output1")),
                "puts": strike_summary(rows(r2.body, "output2")),
                "rt_cd": r2.rt_cd,
                "msg1": r2.body.get("msg1"),
            }
        res[label] = d
        ctx.note(
            f"{label} rt_cd={r.rt_cd} 콜 {d['calls']['n']}행 풋 {d['puts']['n']}행 "
            f"행사가 {d['calls']['strike_min']}~{d['calls']['strike_max']} tr_cont={r.tr_cont!r}"
        )
    ctx.findings["boards"] = res
    ctx.findings["row_cap_hit"] = any(
        v["calls"]["n"] >= 100 or v["puts"]["n"] >= 100 for v in res.values()
    )

    # 1초 1건 권장이 실제 한도인지: 0.25초 간격 6회
    cls, mtrt = targets(ctx)[0]
    burst: list[dict[str, Any]] = []
    for _ in range(6):
        t0 = time.monotonic()
        r = ctx.kis.get(P_CALLPUT, TR_CALLPUT, callput_params(mtrt, cls))
        burst.append({"rt_cd": r.rt_cd, "msg_cd": r.msg_cd, "ms": round(r.elapsed_ms)})
        time.sleep(max(0.0, 0.25 - (time.monotonic() - t0)))
    ctx.findings["burst_4rps"] = burst
    limited = sum(1 for b in burst if b["msg_cd"] == "EGW00201")
    ctx.note(f"전광판 0.25초 간격 6회: 한도초과 {limited}건")
