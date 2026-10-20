"""주식의 총수 현황 — kbj.data.public.dart.shares(묶음 F, §1.3·§3.3 — [확인 필요 #33]).

오버행·내부자 비율·BPS 의 공개 분모(`common_issued` — 자기주식 포함). 합성 세계의 정기보고서마다
같은 값이 나와야 한다(접수일은 호출자가 넘긴다). 정정 보고서가 접수되면 DART 는 최신 판의 접수번호를
준다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from kbj.data.public.dart import shares as S
from kbj.data.public.dart.client import DartClient
from kbj.data.public.dart.doctext import ParseError
from tests.fakes.dart_server import KEY, FakeDart
from tests.fixtures.synthetic.p4_world import CLOCK, build_world
from tests.unit.data.public._support import RecordingLimiter


def test_world_share_counts_equal_truth() -> None:
    w = build_world()
    c = DartClient(KEY, transport=FakeDart(lambda: CLOCK, world=w).transport("t"),
                   limiter=RecordingLimiter(), retries=1)  # fmt: skip
    checked = 0
    for r in w._reports:  # pyright: ignore[reportPrivateUsage] — 세계의 정기보고서 목록
        t = next(x for x in w.share_counts if x.rcept_no == w.rcept(r.ident))
        got = S.fetch_share_count(c, r.corp_code, r.year, r.reprt_code, rcept_dt=t.rcept_dt)
        assert got is not None
        if r.fix is not None:  # 정정본이 있으면 최신 판의 접수번호
            assert got.rcept_no == w.rcept(r.fix)
            continue
        # 세계 참값은 우선주 자기주식을 None 으로 둔다 — 응답 '-'(발행 수 있음)는 0 으로 읽는다(묶음
        # M 요청)
        assert replace(got, treasury_pref=t.treasury_pref) == t
        assert got.treasury_pref == (None if got.pref_issued is None else 0)
        checked += 1
    assert checked > 100
    assert S.fetch_share_count(c, "99400010", 2019, "11011") is None  # 013


def test_pref_and_treasury_blanks_are_none() -> None:
    js = {"status": "000", "list": [
        {"rcept_no": "20261113000001", "se": "보통주", "istc_totqy": "41,250,000",
         "tesstk_co": "-", "stlm_dt": "2026-09-30"},
        {"rcept_no": "20261113000001", "se": "합계", "istc_totqy": "41,250,000",
         "tesstk_co": "-", "stlm_dt": "2026-09-30"},
    ]}  # fmt: skip
    got = S.parse_share_count(js, "99400130", rcept_dt=date(2026, 11, 13))
    assert got is not None
    assert (got.common_issued, got.pref_issued, got.treasury_common, got.treasury_pref) == (
        41_250_000, None, 0, None,
    )  # fmt: skip
    assert got.common_outstanding == 41_250_000  # 발행 수가 있는 행의 자기주식 '-' 는 0
    blank = S.parse_share_count(
        {"status": "000", "list": [{"rcept_no": "20261113000001", "se": "보통주",
                                    "istc_totqy": "-", "tesstk_co": "-", "stlm_dt": "2026-09-30"}]},
        "99400130",
    )  # fmt: skip
    assert blank is not None and blank.common_issued is None and blank.treasury_common is None
    assert blank.quality.value == "estimated"


def test_missing_common_row_is_an_error_and_bad_reprt_is_refused() -> None:
    js = {"status": "000", "list": [{"rcept_no": "20261113000001", "se": "우선주",
                                     "istc_totqy": "1", "stlm_dt": "2026-09-30"}]}  # fmt: skip
    with pytest.raises(ParseError):
        S.parse_share_count(js, "99400130")
    c = DartClient(KEY, transport=FakeDart(lambda: CLOCK).transport("t"),
                   limiter=RecordingLimiter(), retries=1)  # fmt: skip
    with pytest.raises(ValueError, match="보고서 코드"):
        S.fetch_share_count(c, "99400130", 2026, "11015")


def test_voting_share_labels_with_line_breaks_read_as_common_and_pref() -> None:
    """'의결권 있는·없는 주식'(줄바꿈 포함)으로 적는 회사(실측 2026-10-20) — 보통주·우선주로."""
    js = {"status": "000", "list": [
        {"rcept_no": "20260814000001", "se": "의결권 있는\n주식", "istc_totqy": "8,592,896",
         "tesstk_co": "1,238,725", "stlm_dt": "2026-06-30"},
        {"rcept_no": "20260814000001", "se": "의결권 없는\n주식", "istc_totqy": "-",
         "tesstk_co": "-", "stlm_dt": "2026-06-30"},
        {"rcept_no": "20260814000001", "se": "합계", "istc_totqy": "8,592,896",
         "tesstk_co": "1,238,725", "stlm_dt": "2026-06-30"},
    ]}  # fmt: skip
    got = S.parse_share_count(js, "99400130")
    assert got is not None and (got.common_issued, got.treasury_common) == (8_592_896, 1_238_725)
