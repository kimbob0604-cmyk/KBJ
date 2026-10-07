"""KRX Open API 파생 일별 — 필드, 코스피200 행 수, 위클리 포함, 2010 백필 (PLAN §3 #15, #16)."""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from data.krx.eod import FUT_DAILY, OPT_DAILY, fetch_daily
from scripts.probe_common import Ctx, last_business_days, now_kst


def block(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, dict):
        v = body.get("OutBlock_1")  # pyright: ignore[reportUnknownMemberType]
        if isinstance(v, list):
            return [r for r in v if isinstance(r, dict)]  # pyright: ignore[reportUnknownVariableType]
    return []


def summarize(rs: list[dict[str, Any]]) -> dict[str, Any]:
    prods = Counter(str(r.get("PROD_NM", "")) for r in rs)
    k200 = [
        r
        for r in rs
        if "코스피200" in str(r.get("PROD_NM", "")).replace(" ", "")
        or "KOSPI200" in str(r.get("PROD_NM", "")).upper().replace(" ", "")
    ]
    weekly = [
        r
        for r in k200
        if "위클리" in str(r.get("PROD_NM", "")) or "W" in str(r.get("ISU_NM", ""))[:12]
    ]
    return {
        "rows": len(rs),
        "fields": sorted(rs[0].keys()) if rs else [],
        "prod_names": dict(prods.most_common(30)),
        "k200_rows": len(k200),
        "k200_head": k200[:3],
        "weekly_like_rows": len(weekly),
        "weekly_head": weekly[:3],
    }


def first_nonempty(ctx: Ctx, endpoint: str, days: list[date]) -> tuple[str, dict[str, Any]]:
    for d in days:
        st, body = fetch_daily(ctx.settings, endpoint, d.strftime("%Y%m%d"))
        rs = block(body)
        if rs:
            return d.isoformat(), summarize(rs)
        ctx.note(f"{endpoint} {d} HTTP {st} 비어 있음 {str(body)[:120]}")
    return "", {}


def run(ctx: Ctx) -> None:
    days = last_business_days(now_kst().date(), 7)
    for name, ep in (("futures", FUT_DAILY), ("options", OPT_DAILY)):
        d, s = first_nonempty(ctx, ep, days)
        ctx.findings[f"{name}_latest"] = {"date": d, **s}
        ctx.note(
            f"{name} 최신 {d}: {s.get('rows')}행, 코스피200 {s.get('k200_rows')}행, "
            f"위클리 유사 {s.get('weekly_like_rows')}행"
        )
    # 백필 시작점 (2010-01-04)
    d, s = first_nonempty(ctx, OPT_DAILY, [date(2010, 1, 4), date(2010, 1, 5)])
    ctx.findings["options_2010"] = {"date": d, **s}
    ctx.note(f"옵션 2010 첫날 {d or '없음'}: {s.get('k200_rows')}행")
