"""주요사항보고서 구조화 응답 — kbj.data.public.dart.structured(묶음 F, §1.3·§3.3·§4.3).

필드 이름은 [확인 필요 #31](모듈 상수 `FIELDS` 한 곳). 합성 세계 응답 행으로 CB·BW·유상·자기주식을
읽고 참값과 맞추고, 손으로 만든 행으로 EB·무상증자·처분·빈 칸·이상한 행을 본다. DART 호출은 가짜
서버 (`tests/fakes/dart_server.FakeDart(world=…)`) + 정본 `DartClient` 로.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from kbj.core.fin_rows import BuybackEvent, Instrument, OverhangEvent
from kbj.core.quality import Quality
from kbj.data.public.dart import structured as T
from kbj.data.public.dart.client import DartClient, DartQuotaExceeded
from kbj.data.public.dart.doctext import ParseError
from tests.fakes.dart_server import KEY, FakeDart
from tests.fixtures.synthetic.p4_world import CLOCK, build_world
from tests.unit.data.public._support import RecordingLimiter


def client_for(fake: FakeDart) -> DartClient:
    return DartClient(
        KEY, transport=fake.transport("test"), limiter=RecordingLimiter(), retries=1,
        backoff_s=0, now=lambda: CLOCK,
    )  # fmt: skip


def world_row(name: str, rcept_no: str) -> dict[str, Any]:
    w = build_world()
    d = w.disclosure_by_rcept(rcept_no)
    ymd = d.rcept_dt.strftime("%Y%m%d")
    js = w.dart_json(name, {"corp_code": d.corp_code, "bgn_de": ymd, "end_de": ymd}, now=CLOCK)
    return next(r for r in js["list"] if r["rcept_no"] == rcept_no)


@pytest.mark.parametrize(
    ("title", "targets"),
    [
        ("주요사항보고서(전환사채권발행결정)", (Instrument.CB,)),
        ("[기재정정]주요사항보고서(전환사채권발행결정)", (Instrument.CB,)),
        ("주요사항보고서(신주인수권부사채권발행결정)", (Instrument.BW,)),
        ("주요사항보고서(교환사채권발행결정)", (Instrument.EB,)),
        ("주요사항보고서(유상증자결정)", (Instrument.PAID_IN,)),
        ("주요사항보고서(무상증자결정)", (Instrument.BONUS,)),
        ("주요사항보고서(유무상증자결정)", (Instrument.PAID_IN, Instrument.BONUS)),
        ("주요사항보고서(자기주식취득결정)", ("acquire",)),
        ("주요사항보고서(자기주식처분결정)", ("dispose",)),
        ("주요사항보고서(자기주식취득신탁계약체결결정)", ()),  # 구조화 데이터셋이 카탈로그에 없다
        ("전환청구권행사", ()),
        (None, ()),
    ],
)
def test_targets_of_title(title: str | None, targets: tuple[object, ...]) -> None:
    assert T.targets_of(title) == targets


def test_endpoints_are_catalog_dataset_names() -> None:
    from kbj.data.public.dart.datasets import DATASETS

    names = {d.dataset for d in DATASETS}
    assert set(T.ENDPOINT_OF.values()) <= names
    assert set(T.FIELDS) == set(T.ENDPOINT_OF.values())
    assert T.endpoints((Instrument.PAID_IN, Instrument.BONUS)) == ("piicDecsn", "fricDecsn")
    assert T.instrument_of_dataset("exbdIsDecsn") is Instrument.EB
    assert T.instrument_of_dataset("tsstkAqDecsn") is None


def test_world_decisions_equal_the_truth() -> None:
    w = build_world()
    for ev in w.overhang_events:
        d = w.disclosure_by_rcept(ev.rcept_no)
        (target,) = T.targets_of(d.title)
        got = T.parse_decision(
            target, world_row(T.ENDPOINT_OF[target] + ".json", d.rcept_no), rcept_dt=d.rcept_dt
        )
        assert isinstance(got, OverhangEvent)
        if ev.instrument is Instrument.PAID_IN:
            # 발행가·상장 예정일은 구조화 응답에 없다 → 원문 보충 대상
            assert T.missing(got) == ("price", "listing_date")
            assert (got.shares, got.alloc_method, got.face_value) == (
                1_000_000,
                "제3자배정증자",
                None,
            )
        else:
            assert got == ev and T.missing(got) == ()


def test_buyback_acquire_sums_common_and_other_and_keeps_the_raw_method() -> None:
    w = build_world()
    b = w.buybacks[0]
    got = T.parse_decision(
        "acquire", world_row("tsstkAqDecsn.json", b.rcept_no), rcept_dt=b.rcept_dt
    )
    assert isinstance(got, BuybackEvent)
    assert (got.kind, got.shares_planned, got.amount_planned, got.start, got.end) == (
        "acquire", 1_000_000, 52_000_000_000, date(2026, 8, 21), date(2026, 11, 20),
    )  # fmt: skip
    assert got.method == "유가증권시장을 통한 장내 직접 취득"  # 원문 그대로(줄이지 않는다)


def test_eb_bonus_dispose_hand_rows() -> None:
    eb = T.parse_eb({
        "rcept_no": "20261001000001", "corp_code": "99400130", "bd_tm": "4",
        "bd_fta": "3,000,000,000", "ex_prc": "15,000", "extg_stkcnt": "200,000",
        "exrqpd_bgd": "2027년 01월 01일", "exrqpd_edd": "2029년 09월 01일",
        "bd_mtd": "2029년 10월 01일", "bdis_mthn": "공모",
    })  # fmt: skip
    assert (eb.instrument, eb.face_value, eb.price, eb.shares, eb.seq) == (
        Instrument.EB, 3_000_000_000, 15_000.0, 200_000, "4",
    )  # fmt: skip
    assert eb.rcept_dt == date(2026, 10, 1)  # 접수일을 안 주면 접수번호 앞 8자리
    assert eb.refix_floor is None and T.missing(eb) == ()
    bonus = T.parse_bonus({
        "rcept_no": "20261002000001", "corp_code": "99400130", "nstk_ostk_cnt": "10,000,000",
        "nstk_estk_cnt": "500,000", "nstk_lstprd": "2026년 11월 05일",
    })  # fmt: skip
    assert (bonus.shares, bonus.listing_date, bonus.price) == (10_000_000, date(2026, 11, 5), None)
    assert bonus.notes and "기타주식" in bonus.notes[0]
    assert T.missing(bonus) == ()
    dp = T.parse_buyback({
        "rcept_no": "20261003000001", "corp_code": "99400010", "dppln_stk_ostk": "300,000",
        "dppln_stk_estk": "-", "dppln_prc_ostk": "15,000,000,000", "dpprpd_bgd": "2026.10.04",
        "dpprpd_edd": "2026.12.31", "dp_m_mkt": "장내매도",
    }, "dispose")  # fmt: skip
    assert (dp.kind, dp.shares_planned, dp.amount_planned, dp.start, dp.method) == (
        "dispose", 300_000, 15_000_000_000, date(2026, 10, 4), "장내매도",
    )  # fmt: skip


def test_blank_fields_are_none_not_zero_and_bad_rows_raise() -> None:
    ev = T.parse_cb({"rcept_no": "20261001000002", "corp_code": "99400130", "bd_fta": "-",
                     "cv_prc": "", "cvrqpd_bgd": "-"})  # fmt: skip
    assert (ev.face_value, ev.price, ev.exercise_start) == (None, None, None)
    assert set(T.missing(ev)) == {"face_value", "price", "shares", "exercise_start", "exercise_end"}
    with pytest.raises(ParseError):
        T.parse_cb({"rcept_no": "bad", "corp_code": "99400130"})
    # 청구기간이 거꾸로면 비우고 notes
    rev = T.parse_cb({"rcept_no": "20261001000003", "corp_code": "99400130",
                      "cvrqpd_bgd": "2027.01.01", "cvrqpd_edd": "2026.01.01"})  # fmt: skip
    assert rev.exercise_start is None and rev.notes


def test_supplement_fills_only_blanks_and_marks_still_missing_estimated() -> None:
    ev = T.parse_paid_in({"rcept_no": "20261001000004", "corp_code": "99400130",
                          "nstk_ostk_cnt": "1,000", "ic_mthn": "제3자배정증자"})  # fmt: skip
    full = T.supplement(
        ev,
        {"price": 8_000.0, "listing_date": date(2026, 11, 1), "shares": 9, "alloc_method": "x"},
        source="원문",
    )
    assert (full.price, full.listing_date, full.shares, full.alloc_method) == (
        8_000.0, date(2026, 11, 1), 1_000, "제3자배정증자",
    )  # fmt: skip
    assert full.quality is Quality.OK and full.parse_source == "api"
    assert full.notes == ("원문 보충: listing_date·price",)
    half = T.supplement(ev, {"price": 8_000.0}, source="원문")
    assert half.quality is Quality.ESTIMATED
    assert half.notes[-1] == "필수 칸 비어 있음: listing_date"


def test_fetch_decision_picks_the_same_receipt_and_none_when_absent() -> None:
    w = build_world()
    fake = FakeDart(lambda: CLOCK, world=w)
    c = client_for(fake)
    cb2 = next(e for e in w.overhang_events if e.seq == "2")
    got = T.fetch_decision(c, cb2.corp_code, cb2.rcept_dt, cb2.rcept_no, Instrument.CB)
    assert got == cb2
    p = fake.calls("cvbdIsDecsn.json")[-1].params
    assert (p["bgn_de"], p["end_de"]) == (f"{cb2.rcept_dt:%Y%m%d}",) * 2
    assert (
        T.fetch_decision(c, cb2.corp_code, date(2026, 1, 2), "20260102000001", Instrument.CB)
        is None
    )


def test_fetch_decisions_history_for_link_fallback() -> None:
    w = build_world()
    c = client_for(FakeDart(lambda: CLOCK, world=w))
    corp = w.by_no(14).company_corp
    got, bad = T.fetch_decisions(c, corp, Instrument.CB, date(2019, 11, 16), date(2026, 11, 16))
    assert bad == []
    assert sorted(e.seq or "" for e in got if isinstance(e, OverhangEvent)) == ["1", "2", "3"]


def test_quota_status_is_raised_not_swallowed() -> None:
    fake = FakeDart(lambda: CLOCK, world=build_world())
    fake.inject("020", 1, endpoint="cvbdIsDecsn.json")
    with pytest.raises(DartQuotaExceeded):
        T.fetch_decision(client_for(fake), "99400130", date(2025, 1, 20), "x" * 14, Instrument.CB)


@pytest.mark.parametrize("raw", ["sNaN", "NaN", "Infinity", "1e400", str(2**63), "1.5"])
def test_odd_numbers_are_blank_not_an_arithmetic_crash(raw: str) -> None:
    """검증(묶음 F): `decimal` 의 InvalidOperation(ValueError 가 아니다)·float 넘침·bigint 밖 값이
    호출자 밖으로 새지 않는다 — 비엄격은 None, 엄격은 ValueError."""
    from kbj.data.public.dart.doctext import parse_float, parse_int

    assert parse_int(raw) is None
    with pytest.raises(ValueError):
        parse_int(raw, strict=True)
    if raw not in ("1.5", str(2**63)):
        assert parse_float(raw) is None
        with pytest.raises(ValueError):
            parse_float(raw, strict=True)
    row = {"rcept_no": "20261116000001", "corp_code": "99400130", "bd_fta": raw, "cv_prc": raw,
           "cvisstk_cnt": raw}  # fmt: skip
    ev = T.parse_cb(row)
    assert (ev.face_value, ev.shares) == (None, None)
