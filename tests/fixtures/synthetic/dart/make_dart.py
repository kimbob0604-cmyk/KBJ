"""SYNTHETIC DART 공시 원문 fixture 생성기 — 묶음 F 파서 시험용(고정 시드, 실측 아님).

DART 는 공개 등급이라 실응답 fixture 도 커밋할 수 있지만(DATA_TIERS §1) 키를 받기 전이라 원문
**모양**을 합성으로 만든다(docs/p4_design.md §1.3·§8.2). 원문 대부분은 합성 20종목 세계
(`tests/fixtures/synthetic/p4_world.py` — 시드 20261007)의 `documents()` 를 그대로 꺼내고, 세계에
없는 폼 (단위 '원'·괄호·△ 음수, 단위 표기 없음, 연간 기간, 회차 둘인 조정·청구)은 이 파일의 작은
틀로 만든다 (M 보고: 세계의 잠정실적 단위는 억원·백만원·천원뿐 — '단위 4종'은 파서 시험이 따로
덮는다).

    uv run python tests/fixtures/synthetic/dart/make_dart.py          # 커밋본과 비교(다르면 1)
    uv run python tests/fixtures/synthetic/dart/make_dart.py --write  # 다시 쓴다

만드는 것: `p4_docs.json` — {"_source", "docs": {이름: {ident, title, rcept_no, rcept_dt, corp_code,
html}}}. 표 모양(잠정실적 6열·2열, 결정 공시 문단, 행사·조정 표, 결산실적공시예고 표, 소유보고 표)은
[확인 필요 #30~#32] 추정이다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Final

ROOT: Final = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.fixtures.synthetic.p4_world import SYNTHETIC, build_world  # noqa: E402

OUT: Final = Path(__file__).with_name("p4_docs.json")
HEAD: Final = (
    "SYNTHETIC — tests/fixtures/synthetic/dart/make_dart.py(p4_world 시드 20261007 + 고정 틀)"
    "가 만든 합성 공시 원문. 실측 아님"
)

# 이름 → 세계 공시 ident(그 공시의 원문을 그대로)
FROM_WORLD: Final[tuple[tuple[str, str], ...]] = (
    ("prelim_consolidated_eok", "e1.prelim_26q3"),
    ("prelim_mil_no_period", "e8.prelim_26q3"),
    ("prelim_2col", "e18.prelim"),
    ("prelim_standalone_thousand", "e16.prelim"),
    ("prelim_subsidiary", "e11.sub_prelim"),
    ("prelim_correction", "e15.prelim_fix"),
    ("cb_decision", "e14.cb2"),
    ("bw_decision", "e14.bw1"),
    ("paid_in_decision", "e14.paid"),
    ("cb_exercise", "e14.ex1"),
    ("bw_exercise", "e14.bwex"),
    ("price_adjust", "e14.adj1"),
    ("pre_announce", "e1.preann"),
    ("insider_doc", "e15.ins_ceo2"),
    ("generic_notice", "e9.notice"),
)


def _html(title: str, body: str) -> str:
    return (
        f'<html><head><meta charset="utf-8"><title>{title}</title></head><body>'
        f"<!-- {SYNTHETIC} -->{body}</body></html>"
    )


def _table(rows: Iterable[Sequence[str]]) -> str:
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table border=1>{trs}</table>"


def _prelim(
    title: str, unit: str | None, rows: Sequence[Sequence[str]], period: str | None, scope: str
) -> str:
    head = [["1. 실적내용", f"(단위 : {unit}, %)" if unit else "(%)"],
            ["구분", "", "당기", "전기", "전기대비증감율(%)", "흑자적자전환여부", "전년동기",
             "전년동기대비증감율(%)", "흑자적자전환여부"]]  # fmt: skip
    extra = [["2. 실적기간", period]] if period else []
    extra.append(["3. 정보제공내역", "정보제공자: IR팀"])
    body = (
        f"<p>{title}</p><p>※ 동 정보는 {scope} 기준의 잠정 영업실적입니다.</p>"
        + _table([*head, *rows])
        + _table(extra)
    )
    return _html(title, body)


def handmade() -> dict[str, dict[str, Any]]:
    """세계에 없는 폼 — 숫자는 시험이 손으로 대조한다."""
    won = _prelim(
        "영업(잠정)실적(공정공시)",
        "원",
        [
            ["매출액", "당해실적", "1,234,567,890", "1,000,000,000", "23.5", "-", "(987,654,321)",
             "-", "흑자전환"],
            ["누계실적", "3,000,000,000", "-", "-", "-", "2,000,000,000", "50.0", "-"],
            ["영업이익", "당해실적", "△120,000,000", "-50,000,000", "-", "적자지속",
             "80,000,000", "-", "적자전환"],
            ["누계실적", "-100,000,000", "-", "-", "-", "200,000,000", "-", "적자전환"],
        ],
        "2026.07.01 ~ 2026.09.30",
        "별도재무제표",
    )  # fmt: skip
    no_unit = _prelim(
        "연결재무제표기준영업(잠정)실적(공정공시)",
        None,
        [["매출액", "당해실적", "5,000", "4,000", "25.0", "-", "4,500", "11.1", "-"],
         ["영업이익", "당해실적", "500", "400", "25.0", "-", "450", "11.1", "-"]],
        None,
        "연결재무제표",
    )  # fmt: skip
    annual = _prelim(
        "연결재무제표기준영업(잠정)실적(공정공시)",
        "억원",
        [["매출액", "당해실적", "12,000", "11,000", "9.1", "-", "10,000", "20.0", "-"],
         ["영업이익", "당해실적", "1,200", "1,000", "20.0", "-", "900", "33.3", "-"],
         ["당기순이익", "당해실적", "900", "800", "12.5", "-", "700", "28.6", "-"]],
        "2025.01.01 ~ 2025.12.31",
        "연결재무제표",
    )  # fmt: skip
    adjust_multi = _html(
        "전환가액의조정",
        _table([
            ["1. 조정에 관한 사항", "", ""],
            ["회차", "조정전 전환가액(원)", "조정후 전환가액(원)"],
            ["3", "9,000", "8,100"],
            ["4", "12,000", "10,800"],
            ["2. 조정가액 적용일", "2026-10-02", ""],
            ["3. 조정사유", "시가하락에 따른 조정", ""],
        ]),
    )  # fmt: skip
    exercise_multi = _html(
        "전환청구권행사",
        _table([
            ["1. 전환청구권행사 주식수 누계(주)", "300,000"],
            ["2. 전환청구권행사 내역", ""],
            ["청구일자", "회차", "청구금액(원)", "가액(원)", "발행주식수(주)", "상장(예정)일"],
            ["2026-09-01", "5", "1,000,000,000", "10,000", "100,000", "2026-09-15"],
            ["2026-09-03", "5", "2,000,000,000", "10,000", "200,000", "2026-09-17"],
        ]),
    )  # fmt: skip
    return {
        "prelim_won_negative": {"title": "영업(잠정)실적(공정공시)", "html": won},
        "prelim_no_unit": {"title": "연결재무제표기준영업(잠정)실적(공정공시)", "html": no_unit},
        "prelim_annual": {"title": "연결재무제표기준영업(잠정)실적(공정공시)", "html": annual},
        "price_adjust_multi": {"title": "전환가액의조정", "html": adjust_multi},
        "exercise_multi": {"title": "전환청구권행사", "html": exercise_multi},
    }


def build() -> dict[str, Any]:
    w = build_world()
    docs: dict[str, dict[str, Any]] = {}
    for name, ident in FROM_WORLD:
        d = w.disclosure(ident)
        files = w.documents(d.rcept_no)
        html = max(files, key=lambda f: len(f[1]))[1]
        docs[name] = {
            "ident": ident,
            "title": d.title,
            "rcept_no": d.rcept_no,
            "rcept_dt": d.rcept_dt.isoformat(),
            "corp_code": d.corp_code,
            "html": html,
        }
    for name, doc in handmade().items():
        docs[name] = {"ident": None, **doc}
    return {"_source": HEAD, "docs": docs}


def render() -> str:
    return json.dumps(build(), ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="p4_docs.json 을 다시 쓴다")
    args = ap.parse_args(argv)
    text = render()
    if args.write:
        OUT.write_text(text, encoding="utf-8")
        print(f"썼다: {OUT.relative_to(ROOT)} ({len(text):,} B)")
        return 0
    same = OUT.exists() and OUT.read_text(encoding="utf-8") == text
    print("같다" if same else "다르다 — --write 로 다시 만든다")
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
