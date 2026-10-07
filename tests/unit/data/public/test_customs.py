"""관세청 수출입 어댑터(kbj.data.public.customs) — XML 파서·HS 앞자리 0·10일 잠정치 열 대응·
기간 한도.

새로 쓴 시험(설계 §1.4 — 원본 시험 없음). 응답은 공식 문서 모양의 합성 fixture
(`tests/fixtures/synthetic/datago/` — 고정 시드 생성기). 키를 받은 뒤 공개 실데이터로 바꾼다(R21).
"""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from types import ModuleType

import httpx
import pytest

from kbj.data.datago import DatagoAgencyError, DatagoQuotaExceeded
from kbj.data.public.customs.client import (
    DATASET_IDS,
    PATHS,
    TEN_DAY,
    CustomsClient,
    CustomsTruncated,
    months_between,
)
from kbj.data.public.customs.models import (
    SIDO_CODES,
    TEN_DAY_COLUMNS,
    CustomsFormatError,
    TenDayColumnsChanged,
    normalize_hs,
    parse_period,
    ten_day_segment,
    verify_ten_day_columns,
)
from tests.unit.data.public._support import SYNTH, FakeServer, datago_transport, synth


def _client(body: str | dict[str, str], status: int = 200) -> tuple[CustomsClient, FakeServer]:
    """경로별 본문(dict) 또는 모든 경로에 같은 본문."""

    def reply(req: httpx.Request) -> tuple[int, str]:
        if isinstance(body, dict):
            return status, body[req.url.path]
        return status, body

    server = FakeServer(reply)
    t, _, _ = datago_transport(server, DATASET_IDS)
    return CustomsClient(t), server


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_fixtures", SYNTH / "make_fixtures.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_synthetic_fixtures_match_the_generator() -> None:
    gen = _load_generator()
    built: dict[str, str] = gen.build()
    for name, text in built.items():
        assert (SYNTH / name).read_text(encoding="utf-8") == text, name
        assert "SYNTHETIC" in text.splitlines()[1] or '"_source": "SYNTHETIC' in text


# ── 월간 ────────────────────────────────────────────────────────────────────────────────────


def test_item_country_parses_rows_and_sends_required_params() -> None:
    c, server = _client(synth("customs_item_country.xml"))
    rows = c.item_country("202607", "202608", "us", hs="8542")
    assert server.paths() == [PATHS["15100475"]]
    p = server.params()
    assert (p["strtYymm"], p["endYymm"], p["cntyCd"], p["hsSgn"]) == (
        "202607",
        "202608",
        "US",
        "8542",
    )
    assert "serviceKey" in p  # 전송층이 넣는다
    assert len(rows) == 6
    r = rows[0]
    assert (r.source, r.period, r.country, r.country_name) == (
        "DATAGO:15100475",
        "202607",
        "US",
        "미국",
    )
    assert r.hs == "8542" and r.item_name == "전자집적회로"
    assert (
        r.exp_usd is not None and r.imp_usd is not None and r.balance_usd == r.exp_usd - r.imp_usd
    )
    assert r.exp_count is None  # 이 데이터셋에는 건수가 없다
    assert {x.hs for x in rows} == {"8542", "8703", "0303"}


def test_item_country_checks_period_and_codes_before_calling() -> None:
    c, server = _client(synth("customs_item_country.xml"))
    with pytest.raises(ValueError, match="12개월"):
        c.item_country("202501", "202601", "US")  # 13개월
    with pytest.raises(ValueError, match="ISO"):
        c.item_country("202601", "202601", "USA")
    with pytest.raises(ValueError, match="HS"):
        c.item_country("202601", "202601", "US", hs="85A2")
    with pytest.raises(ValueError, match="늦다"):
        c.item_country("202602", "202601", "US")
    with pytest.raises(ValueError, match="YYYYMM"):
        c.item_country("2026-01", "202601", "US")
    assert server.requests == []  # 예산을 쓰지 않았다
    c.item_country("202501", "202512", "US")  # 12개월은 된다
    assert len(server.requests) == 1


def test_items_restores_leading_zero_of_numeric_hs_codes() -> None:
    c, _ = _client(synth("customs_items.xml"))
    rows = c.items("202608", "202608")
    assert [r.hs for r in rows] == ["0106191000", "8542", "0303"]
    assert all(r.exp_weight_kg is not None for r in rows)


def test_countries_has_counts_instead_of_weights() -> None:
    c, server = _client(synth("customs_countries.xml"))
    rows = c.countries("202608", "202608", country="cn")
    assert server.params()["cntyCd"] == "CN"
    assert [r.country for r in rows] == ["CN", "US", "VN"]
    assert all(r.exp_count is not None and r.exp_weight_kg is None for r in rows)
    c.countries("202608", "202608")
    assert "cntyCd" not in server.params()


def test_sigungu_parses_comma_strings_and_keeps_requested_sido() -> None:
    c, server = _client(synth("customs_sigungu.xml"))
    rows = c.sigungu("202607", "202608", "41", "330499")
    p = server.params()
    assert (p["HsSgn"], p["sidoCd"]) == ("330499", "41")  # 대문자 H
    assert len(rows) == 4
    r = rows[0]
    assert (r.source, r.sido, r.sigungu_name, r.hs6, r.period) == (
        "DATAGO:15134343",
        "41",
        "가상시",
        "330499",
        "202607",
    )
    assert r.exp_usd is not None and r.imp_usd is not None and r.balance_usd is not None
    assert r.exp_usd == r.imp_usd + r.balance_usd


def test_sigungu_refuses_sido_codes_outside_their_reform_window() -> None:
    c, server = _client(synth("customs_sigungu.xml"))
    assert SIDO_CODES["12"].valid_in("202607") and not SIDO_CODES["12"].valid_in("202606")
    with pytest.raises(ValueError, match="개편"):
        c.sigungu("202601", "202606", "12", "330499")  # 통합특별시는 7월부터
    with pytest.raises(ValueError, match="개편"):
        c.sigungu("202607", "202608", "29", "330499")  # 광주는 6월까지
    with pytest.raises(ValueError, match="개편"):
        c.sigungu("202606", "202607", "46", "330499")  # 개편을 걸치면 나눠 부른다
    with pytest.raises(ValueError, match="시도"):
        c.sigungu("202607", "202608", "99", "330499")
    with pytest.raises(ValueError, match="6자리"):
        c.sigungu("202607", "202608", "41", "3304")
    assert server.requests == []
    c.sigungu("202601", "202606", "29", "330499")
    c.sigungu("202607", "202612", "12", "330499")
    assert len(server.requests) == 2


# ── 10일 단위 잠정치 ──────────────────────────────────────────────────────────────────────────


def test_ten_day_parses_padded_amounts_segments_and_names() -> None:
    c, server = _client(synth("customs_ten_day_exp_item.xml"))
    rows = c.ten_day("exp_item", "202608", "202609")
    assert server.paths() == [PATHS[TEN_DAY["exp_item"]]]
    assert set(server.params()) == {"strtYymm", "endYymm", "serviceKey"}
    assert len(rows) == 6
    assert [r.as_of for r in rows] == [
        "202608-1",
        "202608-2",
        "202608-3",
        "202609-1",
        "202609-2",
        "202609-3",
    ]
    first = rows[0]
    assert first.source == "DATAGO:15157908" and first.span == "01~10"
    assert first.amounts["00"] == Decimal("6646377")  # 원문 " 6,646,377"(천 달러)
    named = first.by_name()
    assert list(named) == list(TEN_DAY_COLUMNS["exp_item"])
    assert named["반도체"] == Decimal("353492")
    # 누계: 같은 달 안에서 줄지 않는다, 00(전체)은 10대 품목 합 이상
    for r in rows:
        total = r.amounts["00"]
        parts = [v for k, v in r.amounts.items() if k != "00"]
        assert total is not None and all(v is not None for v in parts)
        assert total >= sum(v for v in parts if v is not None)
    assert rows[2].span == "01~31" and rows[5].span == "01~30"


def test_ten_day_fails_when_columns_change() -> None:
    xml = synth("customs_ten_day_exp_item.xml")
    extra = xml.replace("</itemUsdAmt10>", "</itemUsdAmt10><itemUsdAmt11> 1</itemUsdAmt11>", 1)
    c, _ = _client(extra)
    with pytest.raises(TenDayColumnsChanged, match="11"):
        c.ten_day("exp_item", "202608", "202609")
    missing = xml.replace("<itemUsdAmt07>", "<renamed07>", 1).replace(
        "</itemUsdAmt07>", "</renamed07>", 1
    )
    c, _ = _client(missing)
    with pytest.raises(TenDayColumnsChanged, match="00~10"):
        c.ten_day("exp_item", "202608", "202609")


def test_ten_day_rejects_bad_kinds_periods_and_spans() -> None:
    c, server = _client(synth("customs_ten_day_exp_item.xml"))
    with pytest.raises(ValueError, match="120개월"):
        c.ten_day("imp_country", "201601", "202601")  # 121개월
    with pytest.raises(ValueError, match="종류"):
        c.ten_day("exp_total", "202601", "202601")  # type: ignore[arg-type]
    assert server.requests == []
    assert [ten_day_segment(s) for s in ("01~10", "01~20", "01~28", "01 ~ 31")] == [1, 2, 3, 3]
    with pytest.raises(CustomsFormatError):
        ten_day_segment("11~20")
    with pytest.raises(CustomsFormatError):
        ten_day_segment("01~15")


def test_verify_ten_day_columns_against_swagger_descriptions() -> None:
    names = TEN_DAY_COLUMNS["imp_item"]
    good = {f"itemUsdAmt{i:02d}": f"{n} 수입금액(천달러)" for i, n in enumerate(names)}
    verify_ten_day_columns("imp_item", good)
    changed = {**good, "itemUsdAmt02": "액화천연가스 수입금액"}
    with pytest.raises(TenDayColumnsChanged, match="itemUsdAmt02"):
        verify_ten_day_columns("imp_item", changed)
    grown = {**good, "itemUsdAmt11": "새 품목"}
    with pytest.raises(TenDayColumnsChanged, match="새 열"):
        verify_ten_day_columns("imp_item", grown)
    shrunk = {k: v for k, v in good.items() if k != "itemUsdAmt10"}
    with pytest.raises(TenDayColumnsChanged, match="없음"):
        verify_ten_day_columns("imp_item", shrunk)
    assert all(len(v) == 11 and v[0] == "전체" for v in TEN_DAY_COLUMNS.values())


# ── 오류·정규화 ───────────────────────────────────────────────────────────────────────────────


def test_truncated_response_is_an_error_not_a_short_list() -> None:
    xml = synth("customs_items.xml").replace(
        "<totalCount>3</totalCount>", "<totalCount>9</totalCount>"
    )
    c, _ = _client(xml)
    with pytest.raises(CustomsTruncated, match="잘렸다"):
        c.items("202608", "202608")


def test_agency_error_99_and_gateway_quota_surface() -> None:
    err = (
        "<response><header><resultCode>99</resultCode>"
        "<resultMsg>조회기간을 초과하였습니다</resultMsg></header></response>"
    )
    c, _ = _client(err)
    with pytest.raises(DatagoAgencyError, match="조회기간"):
        c.items("202001", "202608")
    quota = (
        "<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg>"
        "<returnAuthMsg>LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR</returnAuthMsg>"
        "<returnReasonCode>22</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"
    )
    c, _ = _client(quota)
    with pytest.raises(DatagoQuotaExceeded):
        c.countries("202608", "202608")


def test_missing_columns_and_bad_values_fail_loudly() -> None:
    xml = synth("customs_countries.xml")
    c, _ = _client(xml.replace("<expCnt>", "<expCount>").replace("</expCnt>", "</expCount>"))
    with pytest.raises(CustomsFormatError, match="expCnt"):
        c.countries("202608", "202608")
    bad = xml.replace("<year>2026.08</year>", "<year>지난달</year>", 1)
    c, _ = _client(bad)
    with pytest.raises(CustomsFormatError, match="기간"):
        c.countries("202608", "202608")


def test_normalizers() -> None:
    assert normalize_hs("106191000") == "0106191000"
    assert normalize_hs("303") == "0303"
    assert normalize_hs("1") == "01"
    assert normalize_hs("85") == "85" and normalize_hs("854231") == "854231"
    assert normalize_hs("106.0") == "0106"
    assert normalize_hs("") is None and normalize_hs("총계") is None
    with pytest.raises(ValueError):
        normalize_hs("85-42")
    assert [parse_period(s) for s in ("2016.01", "201601", "2016-1", "2016년 01월")] == [
        "201601"
    ] * 4
    assert parse_period("총계") is None
    with pytest.raises(ValueError):
        parse_period("2016.13")
    assert months_between("202512", "202601") == 2


def test_client_requires_every_customs_dataset_on_the_transport() -> None:
    t, _, _ = datago_transport(FakeServer(lambda r: (200, "")), ["15101609"])
    with pytest.raises(ValueError, match="15100475"):
        CustomsClient(t)


def test_rows_carry_as_of_and_quality() -> None:
    """절대 규칙 1 — 월간 행은 기준 월·품질, 10일 잠정치는 `ten_day` 키·estimated."""
    from kbj.core.quality import Quality
    from kbj.data.public.customs.models import SigunguRow, TenDayRow, TradeRow

    c, _ = _client(synth("customs_items.xml"))
    row = c.items("202608", "202608")[0]
    assert (row.as_of, row.quality) == (row.period, Quality.OK)
    missing = TradeRow(source="DATAGO:15101609", period="202608", exp_usd=Decimal(1))
    assert missing.quality is Quality.INVALID  # 수입 금액이 비었다 — 0 으로 채우지 않는다
    sgg = SigunguRow(
        source="DATAGO:15134343",
        period=None,
        sido="41",
        sigungu_name="가상시",
        hs6="330499",
        item_name=None,
        exp_count=None,
        exp_usd=None,
        imp_count=None,
        imp_usd=Decimal(1),
        balance_usd=None,
    )
    assert sgg.as_of is None and sgg.quality is Quality.INVALID
    c, _ = _client(synth("customs_ten_day_exp_item.xml"))
    ten = c.ten_day("exp_item", "202608", "202609")[0]
    assert (ten.as_of, ten.quality) == ("202608-1", Quality.ESTIMATED)
    blank = TenDayRow(
        source=ten.source,
        kind=ten.kind,
        month=ten.month,
        span=ten.span,
        segment=ten.segment,
        amounts={**ten.amounts, "00": None},
    )
    assert blank.quality is Quality.INVALID
