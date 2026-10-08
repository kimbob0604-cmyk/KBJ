"""SYNTHETIC KIS P3 응답 fixture 생성기 — 묶음 C 파서 시험용(시드 고정, 실측 아님).

KIS 응답은 로그인 등급이라 실측 원본과 그것으로 만든 fixture 를 레포에 넣지 않는다(CLAUDE.md §2,
DATA_TIERS §4, ADR 0001 U3). 이 스크립트는 가짜 KIS 서버의 합성 출력
(`tests.fakes.kis_server.synthetic_output` — sha256 고정 시드)을 정해진 (TR, 파라미터, 날짜,
슬롯)으로 불러 응답 **모양**을 파일로 남긴다. 필드 이름은 KIS 문서 기준이라 그 자체가 [실측 필요]다
(probe_results §7 #22~#24).

    uv run python tests/fixtures/synthetic/kis/make_p3.py          # 커밋본과 비교(다르면 1)
    uv run python tests/fixtures/synthetic/kis/make_p3.py --write  # 다시 쓴다

만드는 것: `p3_responses.json` — {이름: {tr_id, params, day, slot, body}}.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Final

ROOT: Final = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.fakes.kis_server import SYMBOLS, synthetic_output  # noqa: E402

OUT: Final = Path(__file__).with_name("p3_responses.json")
DAY: Final = "20261007"
SLOT: Final = "1000"
ETF_CODE: Final = "995010"

# 이름 → (tr_id, 파라미터). 파라미터는 kbj 파서 모듈의 params_* 와 같은 모양(값은 합성)
CASES: Final[dict[str, tuple[str, dict[str, str]]]] = {
    "stock_quote": ("FHKST01010100", {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": SYMBOLS[0]}),
    "stock_investor": (
        "FHKST01010900",
        {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": SYMBOLS[0]},
    ),
    "market_investor": (
        "FHPTJ04040000",
        {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001"},
    ),
    "inst_foreign_total": (
        "FHPTJ04400000",
        {"FID_COND_MRKT_DIV_CODE": "V", "FID_INPUT_ISCD": "0001", "FID_ETC_CLS_CODE": "1"},
    ),
    "volume_rank": (
        "FHPST01710000",
        {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "1001", "FID_BLNG_CLS_CODE": "3"},
    ),
    "etf_quote": ("FHPST02400000", {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ETF_CODE}),
    "index_quote": ("FHPUP02100000", {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001"}),
    "sector_quotes": (
        "FHPUP02140000",
        {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "1001"},
    ),
}


def build() -> dict[str, Any]:
    out: dict[str, Any] = {
        "_source": (
            "SYNTHETIC — tests/fixtures/synthetic/kis/make_p3.py 가 가짜 KIS 서버의 합성 출력"
            "(고정 시드)으로 만든 응답 모양. 실측 아님"
        )
    }
    for name, (tr, params) in CASES.items():
        out[name] = {
            "tr_id": tr,
            "params": params,
            "day": DAY,
            "slot": SLOT,
            "body": synthetic_output(tr, params, DAY, SLOT),
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
