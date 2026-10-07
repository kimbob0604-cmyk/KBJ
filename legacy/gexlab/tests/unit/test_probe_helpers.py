from datetime import date, datetime

import pytest
from pydantic import SecretStr

from config.settings import Settings
from data.kis.rest import KisClient, redact
from scripts.probe_callput import strike_summary
from scripts.probe_chain_fill import (
    MasterRow,
    atm_window,
    code_pairs,
    parse_master_line,
    series_rows,
)
from scripts.probe_common import (
    KST,
    BurstLevel,
    classify_mtrt,
    last_business_days,
    max_clean_rps,
    session_of,
)
from scripts.probe_krx import summarize
from scripts.probe_minute_history import night_bars, pick_night_pair
from scripts.probe_minute_paging import continues, minus_one_minute, to_clock
from scripts.probe_night_board import diff, is_expired, nearest_call_code, price_changes
from scripts.probe_option_list import expiry_value


def kst(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=KST)


@pytest.mark.parametrize(
    ("ts", "expected"),
    [
        (kst(2026, 9, 28, 8, 44), "closed"),  # 월 개장 직전
        (kst(2026, 9, 28, 8, 45), "day"),
        (kst(2026, 9, 28, 15, 44), "day"),
        (kst(2026, 9, 28, 15, 45), "closed"),
        (kst(2026, 9, 28, 18, 0), "night"),
        (kst(2026, 9, 29, 5, 59), "night"),  # 월 야간의 화요일 새벽
        (kst(2026, 9, 29, 6, 0), "closed"),
        (kst(2026, 9, 28, 3, 0), "closed"),  # 월 새벽 — 일요일 야간은 없다
        (kst(2026, 10, 3, 5, 0), "night"),  # 금요일 야간 → 토요일 새벽
        (kst(2026, 10, 3, 18, 0), "closed"),  # 토요일 저녁
    ],
)
def test_session_of(ts: datetime, expected: str) -> None:
    assert session_of(ts) == expected


def test_session_of_rejects_naive() -> None:
    with pytest.raises(ValueError, match="naive"):
        session_of(datetime(2026, 9, 28, 10, 0))  # noqa: DTZ001


@pytest.mark.parametrize(
    ("code", "fmt"),
    [
        ("202612", "YYYYMM"),
        ("260903", "YYMMWW"),  # 2026년 9월 3주차 위클리
        ("240703", "YYMMWW"),  # PLAN §3 #12 예시
        ("202613", "unknown"),
        ("261306", "unknown"),
        ("20261", "unknown"),
        ("abcdef", "unknown"),
    ],
)
def test_classify_mtrt(code: str, fmt: str) -> None:
    assert classify_mtrt(code) == fmt


def test_max_clean_rps_stops_at_first_failure() -> None:
    levels = [
        BurstLevel(5, 10, 10, 0, 0, 2.0),
        BurstLevel(10, 20, 20, 0, 0, 2.0),
        BurstLevel(20, 40, 35, 5, 0, 2.0),
        BurstLevel(15, 30, 30, 0, 0, 2.0),
    ]
    assert max_clean_rps(levels) == 15


def test_max_clean_rps_none_when_first_level_fails() -> None:
    assert max_clean_rps([BurstLevel(5, 10, 9, 1, 0, 2.0)]) is None


def test_last_business_days_skips_weekend() -> None:
    assert last_business_days(date(2026, 9, 28), 2) == [date(2026, 9, 25), date(2026, 9, 24)]


def test_redact_hides_secrets() -> None:
    s = Settings(kis_app_key=SecretStr("KEY123456"), kis_app_secret=SecretStr("SECRET987"))
    c = KisClient(s)
    assert redact("key=KEY123456 sec=SECRET987", c) == "key=*** sec=***"


def test_live_trading_defaults_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIVE_TRADING", raising=False)
    assert Settings(_env_file=None).live_trading is False  # pyright: ignore[reportCallIssue]


def test_strike_summary() -> None:
    rs = [
        {"acpr": "400.00", "atm_cls_name": "OTM", "hts_otst_stpl_qty": "0"},
        {"acpr": "402.50", "atm_cls_name": "ATM", "hts_otst_stpl_qty": "1200"},
        {"acpr": "bad"},
    ]
    s = strike_summary(rs)
    assert s["n"] == 3
    assert s["strike_min"] == 400.0
    assert s["strike_max"] == 402.5
    assert s["atm_rows"] == ["402.50"]
    assert s["rows_with_oi"] == 1


def test_night_diff_counts_changes() -> None:
    a = {"C400": {"optn_prpr": "1.0", "acml_vol": "10"}}
    b = {"C400": {"optn_prpr": "1.1", "acml_vol": "10"}, "C405": {"optn_prpr": "0.5"}}
    d = diff(a, b)
    assert d["optn_prpr"] == 1
    assert d["acml_vol"] == 0


def test_krx_summarize_finds_k200_and_weekly() -> None:
    rs = [
        {"PROD_NM": "코스피200 옵션", "ISU_NM": "코스피200 C 202610 400.0"},
        {"PROD_NM": "코스피200 위클리 옵션(월)", "ISU_NM": "코스피200 WKM C 2610W1 400.0"},
        {"PROD_NM": "미니코스피200 옵션", "ISU_NM": "미니 C"},
    ]
    s = summarize(rs)
    assert s["rows"] == 3
    assert s["k200_rows"] == 3  # '미니코스피200' 도 포함 — probe 는 넓게 잡는다
    assert s["weekly_like_rows"] == 1


def test_blank_secret_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIS_APP_KEY", "")
    monkeypatch.setenv("KRX_API_KEY", "   ")
    s = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert s.kis_app_key is None
    assert s.krx_api_key is None


def test_expiry_value_prefers_six_digit() -> None:
    # 2026-09-28 실측 행 형태
    assert expiry_value({"mtrt_yymm": "202610", "mtrt_yymm_code": "0610"}) == "202610"
    assert expiry_value({"mtrt_yymm": "260904", "mtrt_yymm_code": "0904"}) == "260904"
    assert expiry_value({"mtrt_yymm_code": "0610"}) == "0610"
    assert expiry_value({}) == ""


def test_night_bars() -> None:
    st = [("20260925", "235900"), ("20260926", "050000"), ("20260925", "150000")]
    assert night_bars(st) == [("20260925", "235900"), ("20260926", "050000")]
    # 시장구분 CM 은 야간 시작일 날짜에 24~30시로 준다 (2026-09-28 실측)
    cm = [("20260922", "300000"), ("20260922", "240000"), ("20260922", "221900")]
    assert night_bars(cm) == cm
    assert night_bars([("20260922", "154500"), ("20260922", "084600")]) == []


def test_pick_night_pair_skips_holiday_eve() -> None:
    # 2026-09 실측 모양: 09-24·25 봉 없음. 09-23 은 거래일이지만 다음 날이 휴장
    traded = {date(2026, 9, d) for d in (21, 22, 23, 28)}
    cands = [date(2026, 9, d) for d in (25, 24, 23, 22, 21, 18)]
    assert pick_night_pair(cands, lambda d: d in traded) == date(2026, 9, 22)
    # d+1 은 달력상 다음 날이다 — 금요일(09-25)의 d+1 은 토요일이라 평일만 거래일이면 안 뽑힌다
    assert pick_night_pair([date(2026, 9, 25)], lambda d: d.weekday() < 5) is None
    assert pick_night_pair([date(2026, 9, 25)], lambda d: True) == date(2026, 9, 25)
    assert pick_night_pair([], lambda d: True) is None


def test_code_pairs() -> None:
    rs = [{"acpr": "1100.00", "optn_shrn_iscd": " B01610AB1 "}, {"acpr": "1097.50"}]
    assert code_pairs(rs) == [("1100.00", "B01610AB1"), ("1097.50", "")]


# 2026-09-28 마스터(fo_idx_code_mts) 실측 줄 형태
_MASTER = [
    "1|A01612|KR4A016C0004|F 202612| |00000.00|1|2001|KOSPI200",
    "5|B01610745|KR4B016A7454|C 202610   745.0|2|00745.00| |2001|KOSPI200",
    "5|B01610747|KR4B016A7470|C 202610   747.5|2|00747.50| |2001|KOSPI200",
    "5|B01610750|KR4B016A7504|C 202610   750.0|2|00750.00| |2001|KOSPI200",
    "5|B01610A51|KR4B016AA511|C 202610 1,125.0|1|01125.00| |2001|KOSPI200",
    "6|C01610745|KR4C016A7452|P 202610   745.0|3|00745.00| |2001|KOSPI200",
    "N|BAFBZW970|KR4BAFBZ9705|위클리M C 2609W4   970.0|2|00970.00| |2001|KOSPI200",
    "J|B06610001|KR4B066A0011|코스닥150C 202610 1,275|2|01275.00| |3003|KSQ150",
    "",
]


def test_parse_master_line() -> None:
    fut = parse_master_line(_MASTER[0])
    assert fut is not None and fut.strike is None and fut.series == "F 202612"
    c = parse_master_line(_MASTER[1])
    assert c is not None and c == MasterRow("5", "B01610745", "C 202610   745.0", "C", 745.0, "2")
    assert c.series == "C 202610"
    # 다섯째 필드는 ATM 구분(1 ATM) — 콜/풋이 아니다. ATM 표시 줄도 옵션으로 읽어야 한다
    atm = parse_master_line(_MASTER[4])
    assert atm is not None and (atm.cp, atm.strike, atm.moneyness) == ("C", 1125.0, "1")
    assert atm.series == "C 202610"
    put = parse_master_line(_MASTER[5])
    assert put is not None and (put.cp, put.series) == ("P", "P 202610")
    wk = parse_master_line(_MASTER[6])
    assert wk is not None and (wk.cp, wk.strike, wk.series) == ("C", 970.0, "위클리M C 2609W4")
    kq = parse_master_line(_MASTER[7])
    assert kq is not None and kq.strike == 1275.0 and kq.series == "코스닥150C 202610"
    assert parse_master_line("") is None


def test_series_rows_joins_board_codes_to_master_series() -> None:
    master = [m for m in map(parse_master_line, _MASTER) if m is not None]
    out = series_rows(master, ["B01610750", "C01610745", "UNKNOWN"])
    assert list(out) == ["C 202610", "P 202610"]
    assert [m.strike for m in out["C 202610"]] == [745.0, 747.5, 750.0, 1125.0]
    assert series_rows(master, ["A01612"]) == {}  # 선물은 옵션 시리즈가 아니다


def test_atm_window() -> None:
    rs = [MasterRow("5", f"X{k}", f"C 202610 {k}", "C", k) for k in (745.0, 747.5, 750.0, 752.5)]
    assert [m.strike for m in atm_window(rs, 748.0, 1)] == [745.0, 747.5, 750.0]
    assert [m.strike for m in atm_window(rs, 760.0, 1)] == [750.0, 752.5]  # 끝에서 잘린다
    assert atm_window([], 1100.0, 5) == []


def test_nearest_call_code_uses_futures_price_not_atm_label() -> None:
    # 2026-09-28 실측: 전광판 ATM 표시(1125)는 선물가(약 1098)와 달랐다
    snap = {
        "C1125.00": {"code": "BAFBZWA51"},
        "C1097.50": {"code": "BAFBZWA40"},
        "C1100.00": {"code": "BAFBZWA41"},
        "P1097.50": {"code": "CAFBZWA40"},
        "C1102.50": {"code": None},
    }
    assert nearest_call_code(snap, 1098.3) == "BAFBZWA40"
    assert nearest_call_code({}, 1098.3) is None


def test_price_changes_only_compares_successful_calls() -> None:
    a = {
        "fut:F": {"rt_cd": "0", "futs_prpr": "1098.30", "acml_vol": "10"},
        "fut:CM": {"rt_cd": "0", "futs_prpr": "1098.30", "acml_vol": "10"},
        "opt:EU": {"rt_cd": "2", "futs_prpr": None},
    }
    b = {
        "fut:F": {"rt_cd": "0", "futs_prpr": "1098.30", "acml_vol": "12"},
        "fut:CM": {"rt_cd": "0", "futs_prpr": "1098.30", "acml_vol": "10"},
        "opt:EU": {"rt_cd": "2", "futs_prpr": None},
    }
    assert price_changes(a, b) == {"fut:F": True, "fut:CM": False}


def test_is_expired_uses_1520_on_last_trading_day() -> None:
    assert not is_expired("20260928", kst(2026, 9, 28, 15, 19))
    assert is_expired("20260928", kst(2026, 9, 28, 15, 20))
    assert is_expired("20260928", kst(2026, 9, 28, 21, 7))  # 21:07 예약 런이 260904 를 거를 것
    assert is_expired("20260925", kst(2026, 9, 28, 10, 0))
    assert not is_expired("20261005", kst(2026, 9, 28, 21, 7))
    assert not is_expired(None, kst(2026, 9, 28, 21, 7))
    assert not is_expired("", kst(2026, 9, 28, 21, 7))


def test_minus_one_minute_keeps_extended_hours() -> None:
    assert minus_one_minute("135400") == "135300"
    assert minus_one_minute("280400") == "280300"
    assert minus_one_minute("240000") == "235900"
    assert minus_one_minute("220000") == "215900"


def test_to_clock_maps_extended_night_hours() -> None:
    assert to_clock("280300") == "040300"
    assert to_clock("235900") == "235900"


def test_continues_requires_no_overlap_on_the_same_date() -> None:
    first = {"earliest": ("20260928", "280400")}
    assert continues(first, {"latest": ("20260928", "280300")}) == {"ok": True, "gap_min": 1}
    assert continues(first, {"latest": ("20260928", "280400")})["ok"] is False
    assert continues(first, {"latest": ("20260929", "040300")})["ok"] is False  # 날짜가 다르다
    assert continues(first, {"latest": None})["ok"] is False
