"""KIS 현재가·순위·ETF·지수 파서 — 새 시험(합성 응답).

현재가(FHKST01010100) → Snap·Bar, 거래대금 순위(FHPST01710000) → RankRow·잠정 Snap, ETF 현재가
(FHPST02400000) → EtfQuote, 지수·업종(FHPUP02100000·FHPUP02140000 [추정 TR]) →
IndexQuote·SectorQuote.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from kbj.core.quality import Quality
from kbj.data.private.kis.etf import parse_etf_quote
from kbj.data.private.kis.index import (
    params_sector_quotes,
    parse_index_quote,
    parse_sector_quotes,
)
from kbj.data.private.kis.parse import KisParseError, KisRejected, require_ok
from kbj.data.private.kis.quotes import bar_from_quote, parse_quote, status_flags
from kbj.data.private.kis.ranks import params_volume_rank, parse_rank_snaps, parse_volume_rank

OK = {"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상"}
TS = datetime(2026, 10, 7, 1, 10, tzinfo=UTC)
DAY = date(2026, 10, 7)


def quote(**kw: str) -> dict[str, Any]:
    out = {
        "stck_shrn_iscd": "990010",
        "hts_kor_isnm": "합성전자",
        "rprs_mrkt_kor_name": "KOSPI",
        "stck_prpr": "60,400",
        "stck_oprc": "61500",
        "stck_hgpr": "62250",
        "stck_lwpr": "59750",
        "prdy_ctrt": "-1.47",
        "acml_vol": "32809648",
        "acml_tr_pbmn": "2000568286800",
        "hts_avls": "3605748",
        "lstn_stcn": "5969782550",
        "iscd_stat_cls_code": "55",
        "mang_issu_cls_code": "N",
        "temp_stop_yn": "N",
        "sltr_yn": "N",
        **kw,
    }
    return {**OK, "output": out}


def test_quote_becomes_snap_with_won_amounts() -> None:
    s = parse_quote(quote(), "990010", day=DAY)
    assert s.close == 60400.0
    assert s.turnover == 2_000_568_286_800
    assert s.mktcap == 3_605_748 * 100_000_000  # 억원 → 원 [실측 필요]
    assert s.market == "KOSPI"
    assert s.status_flags == ()  # 상태 칸이 있고 아무 상태도 없다(확인)
    assert s.flagged is False
    assert (s.source, s.quality, s.venue) == ("kis", Quality.OK, "KRX")
    assert s.turnover_is_estimate is False


def test_quote_bar_keeps_ohlc_and_does_not_fill_missing() -> None:
    body = quote(stck_oprc="")
    s = parse_quote(body, "990010", day=DAY)
    b = bar_from_quote(body, s)
    assert (b.open, b.high, b.low, b.close) == (None, 62250.0, 59750.0, 60400.0)
    assert (b.source, b.venue, b.date) == ("kis", "KRX", DAY)


@pytest.mark.parametrize(
    ("kw", "flags"),
    [
        ({"iscd_stat_cls_code": "51"}, ("managed",)),
        ({"mang_issu_cls_code": "Y"}, ("managed",)),
        ({"iscd_stat_cls_code": "58"}, ("halted",)),
        ({"temp_stop_yn": "Y"}, ("halted",)),
        ({"sltr_yn": "Y"}, ("liquidation",)),
    ],
)
def test_status_flags(kw: dict[str, str], flags: tuple[str, ...]) -> None:
    s = parse_quote(quote(**kw), "990010", day=DAY)
    assert s.status_flags == flags
    assert s.flagged is True


def test_status_unknown_when_no_status_columns() -> None:
    """R22 — 상태 칸이 없으면 '모름'(None), '상태 없음'(빈 튜플)과 다르다."""
    out = {"stck_prpr": "1000"}
    assert status_flags(out) is None
    s = parse_quote({**OK, "output": {"stck_prpr": "1000"}}, "990010", day=DAY)
    assert s.status_flags is None
    assert s.flagged is None
    assert s.turnover is None
    assert s.turnover_is_estimate is True


def test_quote_without_close_or_with_other_code_fails() -> None:
    with pytest.raises(KisParseError, match="종가"):
        parse_quote(quote(stck_prpr=""), "990010", day=DAY)
    with pytest.raises(KisParseError, match="종목코드가 다르다"):
        parse_quote(quote(), "990020", day=DAY)


def test_require_ok_names_the_reason_without_secrets() -> None:
    require_ok("x", 200, OK)
    with pytest.raises(KisRejected) as e:
        require_ok("FHKST01010100", 500, {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당"})
    assert e.value.retryable
    assert "EGW00201" in str(e.value)
    with pytest.raises(KisRejected) as e2:
        require_ok("x", 200, {"rt_cd": "1", "msg_cd": "OPSQ0002", "msg1": "없는 서비스"})
    assert not e2.value.retryable


# ── 순위 ────────────────────────────────────────────────────────────────────────────────


def rank_body() -> dict[str, Any]:
    return {
        **OK,
        "output": [
            {
                "data_rank": "1",
                "mksc_shrn_iscd": "990010",
                "hts_kor_isnm": "합성전자",
                "stck_prpr": "60400",
                "prdy_ctrt": "1.2",
                "acml_vol": "1000",
                "acml_tr_pbmn": "90000000000",
            },
            {"data_rank": "2", "mksc_shrn_iscd": "990020", "acml_tr_pbmn": "80000000000"},
            {"data_rank": "3", "hts_kor_isnm": "코드없음"},
        ],
    }


def test_volume_rank_rows_and_prelim_snaps() -> None:
    rows = parse_volume_rank(rank_body(), market="KOSPI", ts=TS)
    assert [(r.rank, r.code, r.turnover) for r in rows] == [
        (1, "990010", 90_000_000_000),
        (2, "990020", 80_000_000_000),
    ]
    assert {r.quality for r in rows} == {Quality.ESTIMATED}
    snaps = parse_rank_snaps(rank_body(), market="KOSPI", day=DAY)
    assert [s.code for s in snaps] == ["990010"]  # 현재가 없는 행은 스냅을 만들지 않는다
    s = snaps[0]
    assert (s.source, s.quality, s.turnover_is_estimate) == ("kis.prelim", Quality.ESTIMATED, True)


def test_volume_rank_duplicate_rank_fails() -> None:
    body = {**OK, "output": [{"data_rank": "1", "mksc_shrn_iscd": c} for c in ("a", "b")]}
    with pytest.raises(KisParseError, match="겹친다"):
        parse_volume_rank(body, market="KOSPI", ts=TS)


def test_volume_rank_params() -> None:
    p = params_volume_rank("KOSDAQ")
    assert p["FID_INPUT_ISCD"] == "1001"
    assert p["FID_BLNG_CLS_CODE"] == "3"


# ── ETF ─────────────────────────────────────────────────────────────────────────────────


def test_etf_quote_premium_from_response_or_computed() -> None:
    q = parse_etf_quote({**OK, "output": {"stck_prpr": "10050", "nav": "10000.00"}}, "995010", TS)
    assert q.premium_pct == pytest.approx(0.5)
    assert q.quality is Quality.ESTIMATED
    q2 = parse_etf_quote(
        {**OK, "output": {"stck_prpr": "10050", "nav": "10000", "dprt": "0.49"}}, "995010", TS
    )
    assert q2.premium_pct == 0.49
    q3 = parse_etf_quote({**OK, "output": {"stck_prpr": "10050"}}, "995010", TS)
    assert q3.inav is None
    assert q3.premium_pct is None  # NAV 가 없으면 지어내지 않는다


def test_etf_quote_without_price_fails() -> None:
    with pytest.raises(KisParseError):
        parse_etf_quote({**OK, "output": {"nav": "1"}}, "995010", TS)


# ── 지수·업종 ────────────────────────────────────────────────────────────────────────────


def test_index_quote_turnover_in_won() -> None:
    body = {
        **OK,
        "output": {
            "bstp_nmix_prpr": "2,650.12",
            "bstp_nmix_prdy_ctrt": "-0.35",
            "acml_vol": "300000000",
            "acml_tr_pbmn": "8123456",
        },
    }
    q = parse_index_quote(body, "0001", TS, name="코스피")
    assert q.value == 2650.12
    assert q.turnover == 8_123_456_000_000  # 백만원 → 원 [실측 필요]
    assert q.name == "코스피"
    with pytest.raises(KisParseError):
        parse_index_quote({**OK, "output": {"acml_vol": "1"}}, "0001", TS)


def test_sector_quotes_skip_rows_without_value_and_fail_when_none() -> None:
    body = {
        **OK,
        "output1": {"bstp_nmix_prpr": "1"},
        "output2": [
            {"bstp_cls_code": "0005", "hts_kor_isnm": "음식료", "bstp_nmix_prpr": "4000.1"},
            {"bstp_cls_code": "0006", "bstp_nmix_prpr": ""},
        ],
    }
    rows = parse_sector_quotes(body, "KOSPI", TS)
    assert [(r.market, r.code) for r in rows] == [("KOSPI", "0005")]
    with pytest.raises(KisParseError):
        parse_sector_quotes({**OK, "output2": []}, "KOSPI", TS)
    assert params_sector_quotes("KOSDAQ")["FID_INPUT_ISCD"] == "1001"
