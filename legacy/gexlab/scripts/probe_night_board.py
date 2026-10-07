"""야간 시간대에 전광판 REST 가 야간 체인을 돌려주는가 (PLAN §3 #19).

같은 조회를 60초 간격으로 두 번 해서 값이 움직이는지 본다. 주간에 돌리면 대조군이
된다 — 주간엔 움직이고 야간엔 멈춰 있으면 REST 는 주간 스냅샷만 준다는 뜻이다.

분봉은 시장구분 F 로는 야간 봉이 안 오고 CM(야간선물)으로 와서(2026-09-28 실측),
전광판·단건 현재가도 시장구분별로 잰다: 선물 F·CM, 옵션 O·EU(야간옵션 후보).
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from data.kis.rest import KisResponse
from scripts.probe_callput import callput_params
from scripts.probe_common import (
    P_CALLPUT,
    P_FUT_BOARD,
    P_PRICE,
    TR_CALLPUT,
    TR_FUT_BOARD,
    TR_PRICE,
    Ctx,
    brief,
    now_kst,
    rows,
    session_of,
)
from scripts.probe_option_list import expiries

WATCH = ("optn_prpr", "acml_vol", "hts_otst_stpl_qty", "optn_bidp", "optn_askp")
PX_WATCH = ("futs_prpr", "acml_vol", "hts_otst_stpl_qty")
FUT_MARKETS = ("F", "CM")
OPT_MARKETS = ("O", "EU")


def board(ctx: Ctx, cls: str, mtrt: str, market: str) -> tuple[KisResponse, dict[str, Any]]:
    params = callput_params(mtrt, cls) | {"FID_COND_MRKT_DIV_CODE": market}
    r = ctx.call(P_CALLPUT, TR_CALLPUT, params)
    snap: dict[str, dict[str, Any]] = {}
    for side, key in (("C", "output1"), ("P", "output2")):
        for row in rows(r.body, key):
            snap[f"{side}{row.get('acpr')}"] = {
                **{k: row.get(k) for k in WATCH},
                "code": row.get("optn_shrn_iscd"),
            }
    return r, snap


def snapshot(ctx: Ctx, cls: str, mtrt: str, market: str = "O") -> dict[str, dict[str, Any]]:
    return board(ctx, cls, mtrt, market)[1]


def diff(a: dict[str, dict[str, Any]], b: dict[str, dict[str, Any]]) -> dict[str, int]:
    changed = {k: 0 for k in WATCH}
    for sym, va in a.items():
        vb = b.get(sym)
        if vb is None:
            continue
        for k in WATCH:
            if va.get(k) != vb.get(k):
                changed[k] += 1
    return changed


def nearest_call_code(snap: dict[str, dict[str, Any]], px: float) -> str | None:
    """스냅샷에서 행사가가 px 에 가장 가까운 콜 코드 (전광판 ATM 표시는 믿지 않는다)."""
    best: tuple[float, str] | None = None
    for key, v in snap.items():
        if not key.startswith("C") or not v.get("code"):
            continue
        try:
            dist = abs(float(key[1:]) - px)
        except ValueError:
            continue
        if best is None or dist < best[0]:
            best = (dist, str(v["code"]).strip())
    return best[1] if best else None


def price_changes(a: dict[str, dict[str, Any]], b: dict[str, dict[str, Any]]) -> dict[str, bool]:
    """두 번의 단건 조회 사이 PX_WATCH 중 하나라도 바뀌었는가 (둘 다 성공한 것만)."""
    out: dict[str, bool] = {}
    for key, va in a.items():
        vb = b.get(key)
        if vb is None or va.get("rt_cd") != "0" or vb.get("rt_cd") != "0":
            continue
        out[key] = any(va.get(k) != vb.get(k) for k in PX_WATCH)
    return out


def fut_board(ctx: Ctx, market: str) -> KisResponse:
    params = {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_COND_SCR_DIV_CODE": "20503",
        "FID_COND_MRKT_CLS_CODE": "",
    }
    return ctx.call(P_FUT_BOARD, TR_FUT_BOARD, params)


def fut_head(r: KisResponse) -> dict[str, Any]:
    o = rows(r.body, "output")
    return {k: o[0].get(k) for k in ("futs_shrn_iscd", *PX_WATCH)} if o else {}


def prices(ctx: Ctx, fut_code: str | None, opt_code: str | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for label, code, markets in (("fut", fut_code, FUT_MARKETS), ("opt", opt_code, OPT_MARKETS)):
        if not code:
            continue
        for mk in markets:
            params = {"FID_COND_MRKT_DIV_CODE": mk, "FID_INPUT_ISCD": code}
            r = ctx.call(P_PRICE, TR_PRICE, params)
            o = rows(r.body, "output1")
            row = o[0] if o else {}
            out[f"{label}:{mk}"] = {
                "code": code,
                "rt_cd": r.rt_cd,
                "msg_cd": r.msg_cd,
                "msg1": r.body.get("msg1"),
                "futs_last_tr_date": row.get("futs_last_tr_date"),
                **{k: row.get(k) for k in PX_WATCH},
            }
    return out


def is_expired(last_tr_date: str | None, now: datetime) -> bool:
    """최종거래일(YYYYMMDD)이 지났는가 — 그날 15:20 KST(만기 시각) 이후면 지난 것."""
    if not last_tr_date or len(last_tr_date) != 8 or not last_tr_date.isdigit():
        return False
    today = now.strftime("%Y%m%d")
    return last_tr_date < today or (last_tr_date == today and now.strftime("%H%M") >= "1520")


def series_targets(ctx: Ctx) -> list[tuple[str, str]]:
    """잴 시리즈: 월물리스트의 위클리 전부 + 월물 최근 1개.

    21:07 예약 런이 그날 15:20 에 만기된 위클리 하나만 보고 '멈춤'으로 오판하지 않게
    전부 재고, 시리즈마다 최종거래일을 함께 남긴다(2026-09-28 문서 검증 지적).
    """
    ex = expiries(ctx)
    out = [(c, m) for c in ("WKM", "WKI") for m in ex.get(c, [])]
    monthly = ex.get("(blank)") or []
    return [*out, ("", monthly[0])] if monthly else out


def run(ctx: Ctx) -> None:
    ctx.findings["session_by_clock"] = session_of(now_kst())
    fb1 = {m: fut_board(ctx, m) for m in FUT_MARKETS}
    ctx.findings["futures_board_1"] = brief(fb1["F"])
    head = next((fut_head(r) for r in fb1.values() if fut_head(r)), {})
    fut_code = head.get("futs_shrn_iscd")
    try:
        f_px: float | None = float(head.get("futs_prpr") or "")
    except ValueError:
        f_px = None

    series = series_targets(ctx)
    a = {key: {m: board(ctx, key[0], key[1], m) for m in OPT_MARKETS} for key in series}
    codes: dict[tuple[str, str], str | None] = {}
    for key, bd in a.items():
        snap = next((s for _, s in bd.values() if s), {})
        codes[key] = nearest_call_code(snap, f_px) if f_px is not None else None
    pa = {key: prices(ctx, None, codes[key]) for key in series}
    fpa = prices(ctx, fut_code, None)
    time.sleep(60)
    b = {key: {m: board(ctx, key[0], key[1], m) for m in OPT_MARKETS} for key in series}
    pb = {key: prices(ctx, None, codes[key]) for key in series}
    fpb = prices(ctx, fut_code, None)
    fb2 = {m: fut_board(ctx, m) for m in FUT_MARKETS}
    ctx.findings["futures_board_2"] = brief(fb2["F"])

    now = now_kst()
    per: dict[str, dict[str, Any]] = {}
    for key in series:
        label = f"{key[0] or '월물'}:{key[1]}"
        last = (pa[key].get("opt:O") or {}).get("futs_last_tr_date")
        per[label] = {
            "option_code": codes[key],
            "last_tr_date": last,
            "expired": is_expired(last, now),
            "boards": {
                m: {
                    "rt_cd": a[key][m][0].rt_cd,
                    "msg_cd": a[key][m][0].msg_cd,
                    "msg1": a[key][m][0].body.get("msg1"),
                    "symbols": len(a[key][m][1]),
                    "changed_60s": diff(a[key][m][1], b[key][m][1]),
                }
                for m in OPT_MARKETS
            },
            "prices_1": pa[key],
            "prices_2": pb[key],
            "prices_changed_60s": price_changes(pa[key], pb[key]),
        }
    boards = {
        m: {
            "rt_cd": fb1[m].rt_cd,
            "msg1": fb1[m].body.get("msg1"),
            "head_1": fut_head(fb1[m]),
            "head_2": fut_head(fb2[m]),
        }
        for m in FUT_MARKETS
    }
    live = [k for k, v in per.items() if not v["expired"] and k.split(":")[0] in ("WKM", "WKI")]
    first = live[0] if live else next(iter(per), None)
    ctx.findings |= {
        "series": per,
        "futures_boards": boards,
        "fut_prices_1": fpa,
        "fut_prices_2": fpb,
        "fut_prices_changed_60s": price_changes(fpa, fpb),
        "atm_ref": {"futures": fut_code, "price": f_px},
        "chain": (
            {
                "expiry": first,
                "symbols": per[first]["boards"]["O"]["symbols"],
                "changed_60s": per[first]["boards"]["O"]["changed_60s"],
            }
            if first
            else None
        ),
    }
    sess = ctx.findings["session_by_clock"]
    ctx.note(f"세션(시계)={sess} · 시리즈 {len(per)}개 · 대표(만기 전 첫 위클리) {first}")
    for label, v in per.items():
        bd = "; ".join(
            f"전광판 {m} rt_cd={x['rt_cd']} {x['symbols']}종목 변경 {x['changed_60s']}"
            for m, x in v["boards"].items()
        )
        px = "; ".join(
            f"단건 {k} rt_cd={x['rt_cd']} 가격 {x.get('futs_prpr')}"
            for k, x in v["prices_1"].items()
        )
        tag = "만기 지남" if v["expired"] else "거래 중"
        ctx.note(
            f"{label} 최종거래일 {v['last_tr_date']}({tag}) · {bd} · {px}"
            f" · 60초 사이 단건 변경 {v['prices_changed_60s']}"
        )
    fut_part = "; ".join(
        f"선물 전광판 {m} rt_cd={v['rt_cd']} "
        f"{v['head_1'].get('futs_prpr')}→{v['head_2'].get('futs_prpr')}"
        for m, v in boards.items()
    )
    ctx.note(f"{fut_part} · 선물 단건 60초 사이 변경 {price_changes(fpa, fpb)}")
