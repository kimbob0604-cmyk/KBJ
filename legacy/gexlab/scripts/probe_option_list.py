"""월물리스트(만기 목록)가 위클리 만기를 돌려주는가, 만기 코드 형식은 무엇인가 (PLAN §3 #12)."""

from __future__ import annotations

from typing import Any

from scripts.probe_common import (
    P_OPTION_LIST,
    TR_OPTION_LIST,
    Ctx,
    brief,
    classify_mtrt,
    find_key,
    rows,
)

# '' = KOSPI200 월물. 문서마다 K21 표기가 섞여 있어 둘 다 본다.
CLASSES = ("", "K21", "WKM", "WKI", "MKI")


def expiry_value(row: dict[str, Any]) -> str:
    """전광판 FID_MTRT_CNT 에 넣을 만기값.

    월물리스트 행에는 `mtrt_yymm`(6자리: 월물 YYYYMM, 위클리 YYMMWW)과
    `mtrt_yymm_code`(4자리)가 함께 온다. 2026-09-28 실측에서 4자리로는 전광판이
    0행을 돌려줬다 — 6자리를 쓴다(KIS 샘플도 '202508' 형식).
    """
    v = row.get("mtrt_yymm")
    if v:
        return str(v).strip()
    k = find_key(row, "mtrt")
    return str(row.get(k, "")).strip() if k else ""


def alt_code(ctx: Ctx, cls: str, mtrt: str) -> str | None:
    """같은 만기의 4자리 `mtrt_yymm_code` — 6자리가 0행일 때 대조용으로만 쓴다."""
    detail = ctx.findings.get("option_list", {}).get("detail", {}).get(cls or "(blank)", {})
    for row in detail.get("all_rows", []):
        if str(row.get("mtrt_yymm", "")).strip() == mtrt and row.get("mtrt_yymm_code"):
            return str(row["mtrt_yymm_code"]).strip()
    return None


def expiries(ctx: Ctx) -> dict[str, list[str]]:
    """시장구분별 만기 코드 목록. 다른 probe 가 재사용한다."""
    cached = ctx.findings.get("option_list", {}).get("expiries")
    if cached is not None:
        return cached
    out: dict[str, list[str]] = {}
    detail: dict[str, Any] = {}
    for cls in CLASSES:
        r = ctx.call(
            P_OPTION_LIST,
            TR_OPTION_LIST,
            {
                "FID_COND_SCR_DIV_CODE": "509",
                "FID_COND_MRKT_DIV_CODE": "",
                "FID_COND_MRKT_CLS_CODE": cls,
            },
        )
        rs = rows(r.body, "output")
        codes: list[str] = []
        for row in rs:
            v = expiry_value(row)
            if v:
                codes.append(v)
        out[cls or "(blank)"] = codes
        d = brief(r)
        d["all_rows"] = rs
        d["formats"] = sorted({classify_mtrt(c) for c in codes})
        detail[cls or "(blank)"] = d
    ctx.findings["option_list"] = {"expiries": out, "detail": detail}
    return out


def run(ctx: Ctx) -> None:
    ex = expiries(ctx)
    for cls, codes in ex.items():
        ctx.note(
            f"{cls}: {len(codes)}개 {codes[:8]} 형식={sorted({classify_mtrt(c) for c in codes})}"
        )
    weekly = [c for cls in ("WKM", "WKI") for c in ex.get(cls, [])]
    ctx.findings["weekly_returned"] = bool(weekly)
