"""SYNTHETIC KIS P5 F2 응답 fixture 생성기 — 장중 투자자 1분·프로그램매매 파서 시험용(실측 아님).

KIS 응답은 로그인 등급이라 실측 원본과 그것으로 만든 fixture 를 레포에 넣지 않는다(CLAUDE.md §2,
DATA_TIERS §4, ADR 0001 U3). 이 스크립트는 가짜 KIS 서버의 F2
출력(`tests.fakes.kis_server.f2_output`
— sha256 고정 시드 + 지난 분 수)을 정해진 (TR, 파라미터, 날짜, 분)으로 불러 응답 **모양**을 파일로
남긴다. TR id·경로·칸 이름은 파서 모듈(`kbj.data.private.kis.{investors_minute,program}`)의 표를
따른다 — 그 자체가 [확인 필요 #39~#41](probe_results §7).

    uv run python tests/fixtures/synthetic/kis/p5_make.py          # 커밋본과 비교(다르면 1)
    uv run python tests/fixtures/synthetic/kis/p5_make.py --write  # 다시 쓴다

만드는 것: `p5_flows_program.json` — {이름: {tr_id, params, day, hhmm, body}}.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Final

ROOT: Final = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kbj.data.private.kis.investors_minute import (  # noqa: E402
    TR_MARKET_MINUTE,
    params_market_minute,
)
from kbj.data.private.kis.program import (  # noqa: E402
    PROGRAM_TRS,
    params_investor_today,
    params_market_daily,
    params_market_intraday,
    params_stock_daily,
    params_stock_intraday,
)
from tests.fakes.kis_server import f2_output  # noqa: E402

OUT: Final = Path(__file__).with_name("p5_flows_program.json")
DAY: Final = date(2026, 11, 17)
_T = PROGRAM_TRS

# 이름 → (tr_id, 파라미터, 날짜, HHMM)
CASES: Final[dict[str, tuple[str, dict[str, str], date, str]]] = {
    # 1분 누적 — K2I 선물(GX 실측 조합) 09:01(2분째)·13:00, 장전 08:30(모두 0)
    "minute_fut_0901": (TR_MARKET_MINUTE.tr_id, params_market_minute("K2I", "F001"), DAY, "0901"),
    "minute_fut_1300": (TR_MARKET_MINUTE.tr_id, params_market_minute("K2I", "F001"), DAY, "1300"),
    "minute_preopen": (TR_MARKET_MINUTE.tr_id, params_market_minute("K2I", "OC01"), DAY, "0830"),
    # 현물 자리표시 코드(SYNK·SYN0 — [확인 필요 #39]) 마감 뒤
    "minute_spot_close": (TR_MARKET_MINUTE.tr_id, params_market_minute("SYNK", "SYN0"), DAY, ""),
    # 프로그램 시장 일별 — 5거래일 구간(최근 먼저)
    "program_daily_K": (
        _T["program_market_daily"].tr_id,
        params_market_daily("K", "KRX", date(2026, 11, 11), DAY),
        DAY,
        "",
    ),
    "program_intraday_Q_1300": (
        _T["program_market_intraday"].tr_id,
        params_market_intraday("Q", "KRX"),
        DAY,
        "1300",
    ),
    "program_intraday_preopen": (
        _T["program_market_intraday"].tr_id,
        params_market_intraday("K", "KRX"),
        DAY,
        "0850",
    ),
    "program_investor_K": (
        _T["program_investor_today"].tr_id,
        params_investor_today("K", "KRX"),
        DAY,
        "",
    ),
    "program_stock_1300": (
        _T["program_stock_intraday"].tr_id,
        params_stock_intraday("990010", "KRX"),
        DAY,
        "1300",
    ),
    "program_stock_daily": (
        _T["program_stock_daily"].tr_id,
        params_stock_daily("990010", "KRX", DAY),
        DAY,
        "",
    ),
}


def build() -> dict[str, Any]:
    out: dict[str, Any] = {
        "_source": (
            "SYNTHETIC — tests/fixtures/synthetic/kis/p5_make.py 가 가짜 KIS 서버의 F2 출력"
            "(f2_output — sha256 고정 시드 + 지난 분 수)으로 만든 응답 모양. 실측 아님"
        )
    }
    for name, (tr, params, day, hhmm) in CASES.items():
        ymd = day.strftime("%Y%m%d")
        out[name] = {
            "tr_id": tr,
            "params": params,
            "day": ymd,
            "hhmm": hhmm,
            "body": f2_output(tr, params, ymd, hhmm),
        }
    return out


def render() -> str:
    return json.dumps(build(), ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--write", action="store_true")
    args = p.parse_args(argv)
    text = render()
    if args.write:
        OUT.write_text(text, encoding="utf-8")
        print(f"썼다: {OUT.name}")
        return 0
    same = OUT.exists() and OUT.read_text(encoding="utf-8") == text
    print("같다" if same else "다르다 — --write 로 다시 만든다")
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
