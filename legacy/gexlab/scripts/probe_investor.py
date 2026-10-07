"""장중 투자자별 선물·옵션 매매동향 응답 (PLAN §3 #13, FHPTJ04030000)."""

from __future__ import annotations

from typing import Any

from scripts.probe_common import P_INVESTOR, TR_INVESTOR, Ctx, brief, rows

PAIRS = (
    ("K2I", "F001"),  # 선물
    ("K2I", "OC01"),  # 콜
    ("K2I", "OP01"),  # 풋
    ("WKM", "OC05"),  # 위클리(월) 콜
    ("WKM", "OP05"),
    ("WKI", "OC04"),  # 위클리(목) 콜
    ("WKI", "OP04"),
)


def run(ctx: Ctx) -> None:
    res: dict[str, Any] = {}
    for mkt, sector in PAIRS:
        r = ctx.call(
            P_INVESTOR,
            TR_INVESTOR,
            {"FID_INPUT_ISCD": mkt, "FID_INPUT_ISCD_2": sector},
            min_gap=0.6,
        )
        d = brief(r)
        d["all_rows"] = rows(r.body, "output")
        res[f"{mkt}/{sector}"] = d
        ctx.note(f"{mkt}/{sector} rt_cd={r.rt_cd} 행 {len(d['all_rows'])} {r.body.get('msg1')}")
    ctx.findings["pairs"] = res
