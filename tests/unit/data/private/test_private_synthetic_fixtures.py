"""로그인 등급 합성 fixture(tests/fixtures/synthetic/krx) — 합성 표시·생성기 출력과 같음.

KRX·금융위 시세 원본은 레포에 넣지 않는다(CLAUDE.md §2). 커밋된 JSON 이 시드 고정 생성기의 출력과
같은지, 파생 2종은 legacy GX 생성기 출력과 바이트까지 같은지 본다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fixtures.synthetic.krx import make_synthetic

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic" / "krx"
GX_FIX = Path(__file__).resolve().parents[4] / "legacy" / "gexlab" / "tests" / "fixtures" / "krx"


def test_every_json_is_marked_synthetic() -> None:
    names = sorted(p.name for p in FIX.glob("*.json"))
    assert names == sorted([*make_synthetic.build(), "opt_daily.json", "fut_daily.json"])
    for name in names:
        assert json.loads((FIX / name).read_text(encoding="utf-8"))["_source"].startswith(
            "SYNTHETIC"
        ), name


@pytest.mark.parametrize("name", sorted(make_synthetic.build()))
def test_committed_fixture_is_the_generator_output(name: str) -> None:
    assert (FIX / name).read_text(encoding="utf-8") == make_synthetic.dump(
        make_synthetic.build()[name]
    )


def test_generator_check_mode_passes_and_is_deterministic() -> None:
    lines: list[str] = []
    assert make_synthetic.main([], out=lines.append) == 0
    assert lines == []
    assert make_synthetic.build() == make_synthetic.build()


@pytest.mark.parametrize("name", ["opt_daily.json", "fut_daily.json"])
def test_derivative_copies_match_the_legacy_gx_generator_output(name: str) -> None:
    original = GX_FIX / name
    if not original.exists():
        pytest.skip("legacy GX fixture 가 없다(P9 정리 뒤) — GX 생성기 KRX 부분을 옮긴 뒤 바꾼다")
    assert (FIX / name).read_bytes() == original.read_bytes()


def test_synthetic_isin_check_digit() -> None:
    """합성 ISIN 의 검사 숫자는 Luhn — 실제 ISIN 규칙과 같은 꼴(값은 가짜)."""
    assert make_synthetic.isin("005930") == "KR7005930003"  # 규칙 확인용 공개 예(ISIN 표기 예시)
    alnum = make_synthetic.isin("9900K0")
    assert len(alnum) == 12 and alnum.startswith("KR79900K000") and alnum[-1].isdigit()


def test_etf_net_assets_equal_shares_times_nav() -> None:
    """합성 ETF 는 검산 ③ 의 전제(순자산 = 상장좌수 × NAV, 원 반올림)를 지킨다."""
    doc = make_synthetic.build()["etf_daily.json"]
    for day in make_synthetic.DAYS:
        for r in doc[day]:
            want = round(int(r["LIST_SHRS"]) * float(r["NAV"]))
            assert abs(int(r["INVSTASST_NETASST_TOTAMT"]) - want) <= 1
