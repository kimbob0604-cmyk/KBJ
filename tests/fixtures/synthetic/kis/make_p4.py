"""SYNTHETIC KIS P4 컨센서스 응답 fixture 생성기 — 묶음 K 파서 시험용(시드 고정, 실측 아님).

KIS 응답은 로그인 등급이라 실측 원본과 그것으로 만든 fixture 를 레포에 넣지 않는다(CLAUDE.md §2,
DATA_TIERS §4, ADR 0001 U3). 이 스크립트는 가짜 KIS 서버의 컨센서스 출력
(`tests.fakes.kis_server.consensus_output` — 합성 20종목 세계 `p4_world`(시드 20261007)의 의미값,
세계 밖 종목은 sha256 고정 시드)을 정해진 (TR, 파라미터, 날짜)로 불러 응답 **모양**을 파일로 남긴다.
TR id·경로는 `kbj/data/private/kis/datasets.py:CONSENSUS_TRS`, 출력 이름·행 순서·단위는
`kbj.data.private.kis.consensus` 의 상수 표를 따른다 — 그 자체가 [확인 필요 #29](probe_results §7).

    uv run python tests/fixtures/synthetic/kis/make_p4.py          # 커밋본과 비교(다르면 1)
    uv run python tests/fixtures/synthetic/kis/make_p4.py --write  # 다시 쓴다

만드는 것: `p4_consensus.json` — {이름: {tr_id, params, day, body}}.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Final

ROOT: Final = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kbj.data.private.kis.consensus import (  # noqa: E402
    TR_ESTIMATE,
    TR_OPINION,
    params_estimate,
    params_opinion,
)
from tests.fakes.kis_server import SYMBOLS, consensus_output  # noqa: E402
from tests.fixtures.synthetic.p4_world import TODAY, build_world  # noqa: E402

OUT: Final = Path(__file__).with_name("p4_consensus.json")
WINDOW: Final = 90  # config/fin.yaml consensus.opinion_window_days 와 같은 값(시험이 본다)


def _opinion(code: str, day: date) -> dict[str, str]:
    return params_opinion(code, day - timedelta(days=WINDOW), day)


# 이름 → (tr_id, 파라미터, 날짜, 세계를 쓰는가)
CASES: Final[dict[str, tuple[str, dict[str, str], date, bool]]] = {
    # 세계 #1 — 실적 2열(2024·2025)·추정 3열(2026~2028)
    "estimate_full": (TR_ESTIMATE.tr_id, params_estimate("994010"), TODAY, True),
    # 세계 #3 신규상장 — 증권사 2곳(thin), 실적 1열·추정 2열
    "estimate_thin": (TR_ESTIMATE.tr_id, params_estimate("994020"), TODAY, True),
    # 세계 #7 3월 결산 — 결산년월 YYYY03
    "estimate_march": (TR_ESTIMATE.tr_id, params_estimate("994060"), TODAY, True),
    # 세계 #8 적자 — FY1 순이익 부호 전환(9월 스냅은 음수)
    "estimate_loss_sep": (TR_ESTIMATE.tr_id, params_estimate("994070"), date(2026, 9, 1), True),
    # 세계 #9 재무 미제출 — 유니버스 안이지만 커버 없음(빈 출력 → KisEmpty)
    "estimate_empty": (TR_ESTIMATE.tr_id, params_estimate("994080"), TODAY, True),
    # 세계 밖(시뮬레이션 SYMBOLS) — 고정 시드
    "estimate_seeded": (TR_ESTIMATE.tr_id, params_estimate(SYMBOLS[0]), date(2026, 10, 7), False),
    # 세계 #4 — 마지막 의견이 'Not Rated'(목표가 0)인 증권사 하나
    "opinion_not_rated": (TR_OPINION.tr_id, _opinion("994030", TODAY), TODAY, True),
    # 세계 #8 — 증권사 하나가 '중립'
    "opinion_neutral": (TR_OPINION.tr_id, _opinion("994070", TODAY), TODAY, True),
    # 세계 #1 — 증권사 6곳 중 하나는 마지막 의견이 창(90일) 밖
    "opinion_full": (TR_OPINION.tr_id, _opinion("994010", TODAY), TODAY, True),
    "opinion_empty": (TR_OPINION.tr_id, _opinion("994080", TODAY), TODAY, True),
    "opinion_seeded": (
        TR_OPINION.tr_id,
        _opinion(SYMBOLS[0], date(2026, 10, 7)),
        date(2026, 10, 7),
        False,
    ),
}


def build() -> dict[str, Any]:
    world = build_world()
    out: dict[str, Any] = {
        "_source": (
            "SYNTHETIC — tests/fixtures/synthetic/kis/make_p4.py 가 가짜 KIS 서버의 컨센서스 출력"
            "(합성 20종목 세계 p4_world 시드 20261007·고정 시드)으로 만든 응답 모양. 실측 아님"
        )
    }
    for name, (tr, params, day, use_world) in CASES.items():
        ymd = day.strftime("%Y%m%d")
        out[name] = {
            "tr_id": tr,
            "params": params,
            "day": ymd,
            "body": consensus_output(tr, params, ymd, world if use_world else None),
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
