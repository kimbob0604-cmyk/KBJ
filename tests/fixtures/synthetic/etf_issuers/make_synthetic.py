"""SYNTHETIC ETF 운용사 응답 견본 생성기 — 묶음 E3 시험용(고정 시드, 실측 아님).

운용사 PDF·목록 응답은 로그인 등급이라(운용사마다 약관이 다르다 — DATA_TIERS §1, D-P3-12) 실응답과
그것으로 만든 fixture 를 레포에 넣지 않는다(CLAUDE.md §2). 이 스크립트는 **실측을 읽지 않고**
가짜 운용사 서버(`tests/fakes/etf_issuer_server.py` — 고정 시드 `SEED`)가 내는 응답을 운용사마다
목록·PDF 한 벌씩 파일로 떨군다. 키 이름·HTML 구조는 ET `etf_tracker_v9` 어댑터 머리말의 실측 기록을
따른 것이라 그 자체가 [실측 필요]다(체크리스트 #27). 이름·코드·수량은 모두 지어낸 값이다
(`합성…`, 종목코드 99xxx0·펀드 티커 97xx00 대역).

    uv run python tests/fixtures/synthetic/etf_issuers/make_synthetic.py  # 커밋본과 비교(다르면 1)
    uv run python tests/fixtures/synthetic/etf_issuers/make_synthetic.py --write  # 다시 쓴다

만드는 것(이 폴더): `<운용사>_universe.{json,html}`, `<운용사>_pdf.{json,html}` — 운용사 9곳
(kodex·tiger·timefolio·sol·ace·hanaro·koact·plus·rise). 기준일 2026-10-06(화).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3]))

from tests.fakes.etf_issuer_server import samples  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--write", action="store_true", help="파일을 다시 쓴다")
    args = ap.parse_args(argv)
    bad = 0
    for name, text in samples().items():
        path = HERE / name
        if args.write:
            path.write_text(text, encoding="utf-8")
            continue
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            print(f"다름: {name}")
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
