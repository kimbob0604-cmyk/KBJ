"""kbj.core.rows — 행 자료형(docs/p3_design.md §1.1).

frozen, naive 시각 거부, 금액 정수, 원장 우선순위(D-P3-7).
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime
from typing import Any

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import (
    BASES,
    GROUP4,
    INST7,
    VENUES,
    AllTime,
    Bar,
    BoardArtifact,
    BoardDayRecord,
    Change,
    EtfDay,
    EtfMeta,
    EtfQuote,
    EtfType,
    HoldingRow,
    IndexQuote,
    IntradayInvestor,
    Investor,
    InvestorDay,
    Label,
    LedgerCheck,
    RankRow,
    ReconcileRow,
    Snap,
    SplitEvent,
    StockDay,
    UniverseRow,
    ledger_rank,
    pick_best,
    row_rank,
)

D = date(2026, 10, 6)
TS = datetime(2026, 10, 6, 1, 0, tzinfo=UTC)
NAIVE = datetime(2026, 10, 6, 10, 0)  # noqa: DTZ001 — 거부되는지 본다
OK = Quality.OK


def bar(**kw: Any) -> Bar:
    base: dict[str, Any] = dict(
        code="005930", date=D, open=1.0, high=2.0, low=0.5, close=1.5, volume=10,
        turnover=1_000, source="krx", venue="KRX", quality=OK,
    )  # fmt: skip
    return Bar(**{**base, **kw})


def snap(**kw: Any) -> Snap:
    base: dict[str, Any] = dict(
        code="005930", date=D, name="합성", market="KOSPI", kind="common", close=70_000.0,
        chg_pct=1.0, volume=10, turnover=700_000, turnover_is_estimate=False, mktcap=10**13,
        shares=10**6, status_flags=(), source="kis", venue="KRX", quality=OK,
    )  # fmt: skip
    return Snap(**{**base, **kw})


def test_rows_are_frozen() -> None:
    b = bar()
    with pytest.raises(dataclasses.FrozenInstanceError):
        b.close = 2.0  # type: ignore[misc]
    s = snap()
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.quality = Quality.INVALID  # type: ignore[misc]


@pytest.mark.parametrize("field", ["turnover", "volume"])
def test_money_and_quantity_must_be_int(field: str) -> None:
    with pytest.raises(TypeError, match="정수"):
        bar(**{field: 1000.0})
    with pytest.raises(TypeError, match="정수"):
        bar(**{field: True})
    with pytest.raises(ValueError, match="0 이상"):
        bar(**{field: -1})


def test_snap_money_fields_are_int_and_investor_values_may_be_negative() -> None:
    for f in ("turnover", "mktcap", "shares"):
        with pytest.raises(TypeError):
            snap(**{f: 1.5})
    row = InvestorDay("005930", D, Investor.FOREIGN, -5_000_000, -10, "kis", "KRX", OK)
    assert row.net_value == -5_000_000
    with pytest.raises(TypeError):
        InvestorDay("005930", D, Investor.FOREIGN, 1.0, None, "kis", "KRX", OK)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="둘 다"):
        InvestorDay("005930", D, Investor.FOREIGN, None, None, "kis", "KRX", OK)
    for f in ("net_asset", "list_shrs", "turnover", "mktcap"):
        kw: dict[str, Any] = dict(
            code="069500", date=D, name=None, close=1.0, nav=1.0, list_shrs=1, net_asset=1,
            turnover=1, volume=1, mktcap=1, base_index=None, source="krx", venue="KRX", quality=OK,
        )  # fmt: skip
        kw[f] = 1.0
        with pytest.raises(TypeError):
            EtfDay(**kw)


def test_naive_times_are_rejected() -> None:
    with pytest.raises(ValueError, match="naive"):
        IndexQuote("0001", NAIVE, "코스피", 1.0, 0.0, 1, 1, "kis", Quality.ESTIMATED)
    with pytest.raises(ValueError, match="naive"):
        EtfQuote("069500", NAIVE, 1.0, 1.0, 0.0, 1, 1, "kis", Quality.ESTIMATED)
    with pytest.raises(ValueError, match="naive"):
        RankRow("KOSPI", NAIVE, 1, "KRX", "005930", None, 1, 0.0, "kis", Quality.ESTIMATED)
    with pytest.raises(ValueError, match="naive"):
        IntradayInvestor("005930", NAIVE, Investor.FOREIGN, "KRX", 1, None, 1, "kis.prelim",
                         Quality.ESTIMATED)  # fmt: skip
    with pytest.raises(ValueError, match="naive"):
        ReconcileRow(D, "005930", "close", 1.0, 1.0, 0.0, "ok", NAIVE)
    with pytest.raises(ValueError, match="naive"):
        LedgerCheck("stock", D, "005930", "c1", 0.0, NAIVE)


def test_date_fields_reject_datetime_and_strings() -> None:
    with pytest.raises(TypeError, match="date"):
        bar(date=TS)
    with pytest.raises(TypeError, match="date"):
        bar(date="2026-10-06")


def test_quality_venue_and_kind_are_checked() -> None:
    assert bar(quality="estimated").quality is Quality.ESTIMATED  # 문자열도 받아 Quality 로
    with pytest.raises(ValueError, match="quality"):
        bar(quality="good")
    with pytest.raises(ValueError, match="venue"):
        bar(venue="NASDAQ")
    with pytest.raises(ValueError, match="kind"):
        snap(kind="warrant")
    with pytest.raises(ValueError, match="source"):
        bar(source=" ")
    with pytest.raises(ValueError, match="code"):
        bar(code="00 5930")
    with pytest.raises(ValueError, match="유한"):
        bar(close=float("nan"))
    assert set(VENUES) == {"", "KRX", "NXT", "TOTAL"}


def test_status_flags_none_means_unknown() -> None:
    assert snap(status_flags=None).flagged is None
    assert snap(status_flags=()).flagged is False
    assert snap(status_flags=("halted",)).flagged is True
    assert snap(status_flags=("caution",)).flagged is False
    assert snap(status_flags=["managed"]).status_flags == ("managed",)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        snap(status_flags=("관리",))


def test_investor_names_match_0003_and_groups() -> None:
    assert [i.value for i in Investor] == [
        "foreign", "foreign_other", "institution", "fin_invest", "trust", "private_fund",
        "insurance", "bank", "pension", "other_fin", "other_corp", "individual",
    ]  # fmt: skip
    assert len(INST7) == 7 and Investor.INSTITUTION not in INST7
    assert GROUP4 == (Investor.FOREIGN, Investor.INSTITUTION, Investor.OTHER_CORP,
                      Investor.INDIVIDUAL)  # fmt: skip
    row = InvestorDay("005930", D, "pension", 1, None, "kis", "KRX", OK)  # type: ignore[arg-type]
    assert row.investor is Investor.PENSION
    with pytest.raises(ValueError):
        InvestorDay("005930", D, "nps", 1, None, "kis", "KRX", OK)  # type: ignore[arg-type]


def test_etf_types_have_korean_labels() -> None:
    assert [t.label for t in EtfType] == [
        "국내 대표지수", "국내 테마", "해외주식", "레버리지·인버스", "채권·현금", "원자재", "기타",
    ]  # fmt: skip
    m = EtfMeta("069500", None, None, None, None, "kr_index", None, None, None, None, "krx", OK)  # type: ignore[arg-type]
    assert m.etf_type is EtfType.KR_INDEX


def test_ledger_rank_order_follows_d_p3_7() -> None:
    ordered = [
        ("krx", "ok"), ("KIS", "ok"), ("kis", "estimated"), ("kis.prelim", "estimated"),
        ("legacy:board.px", "ok"), ("krx", "invalid"),
    ]  # fmt: skip
    ranks = [ledger_rank(s, q) for s, q in ordered]
    assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)
    assert row_rank("krx", "ok", "KRX") < row_rank("krx", "ok", "") < row_rank("krx", "ok", "NXT")


def test_pick_best_is_deterministic() -> None:
    rows = [
        bar(source="kis", quality="estimated", close=1.0),
        bar(source="krx", close=2.0),
        bar(source="krx", close=3.0),  # 같은 순위 — 먼저 온 행이 남는다
        bar(code="000660", source="kis", close=4.0),
    ]
    best = pick_best(rows, lambda b: b.code, lambda b: row_rank(b.source, b.quality, b.venue))
    assert {c: b.close for c, b in best.items()} == {"005930": 2.0, "000660": 4.0}


def test_other_rows_validate_their_choices() -> None:
    with pytest.raises(ValueError, match="basis"):
        Label("005930", D, "open", "w52", 1, "krx", OK)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="kind"):
        Label("005930", D, "close", "w26", 1, "krx", OK)  # type: ignore[arg-type]
    assert BASES == ("close", "high")
    with pytest.raises(ValueError, match="kind"):
        Change(D, "f", "005930", "BUY", None, D, D, 1, None, None, None, None, None, None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="ratio"):
        SplitEvent("069500", D, 0.0, "manual", "", "manual", OK)
    with pytest.raises(ValueError, match="rank"):
        RankRow("KOSPI", TS, 0, "KRX", "005930", None, 1, 0.0, "kis", Quality.ESTIMATED)
    with pytest.raises(ValueError, match="origin"):
        SplitEvent("069500", D, 10.0, "guess", "", "x", OK)  # type: ignore[arg-type]
    h = HoldingRow("005930", "합성", 10.5, 1.2, 1_000.0, "ETF_ISSUERS:kodex", OK)
    assert h.qty == 10.5  # 운용사 수량은 소수가 올 수 있다
    u = UniverseRow("005930", D, None, None, None, None, "krx", OK, flags={"a": 1})
    assert u.flags == {"a": 1}
    a = AllTime("005930", None, None, None, None, None, None, None, None, 0, False, None, None,
                None, "krx", OK)  # fmt: skip
    assert a.history_from is None


def _stock_day(day: date) -> StockDay:
    return StockDay("005930", day, None, None, None, None, False, None, None, None, None, None,
                    False, None, None, None, None, None, "kis", Quality.ESTIMATED)  # fmt: skip


def test_board_day_record_keeps_one_date() -> None:
    art = BoardArtifact(D, "newhigh", {"labels": []}, "board-1", "sha", TS, TS, "kis",
                        Quality.ESTIMATED)  # fmt: skip
    rec = BoardDayRecord(D, (), (_stock_day(D),), (art,), "kis", Quality.ESTIMATED)
    assert rec.stock_days[0].date == D
    with pytest.raises(ValueError, match="다른 행"):
        BoardDayRecord(D, (), (_stock_day(date(2026, 10, 2)),), (), "kis", Quality.ESTIMATED)
    with pytest.raises(ValueError, match="두 번"):
        BoardDayRecord(D, (), (), (art, art), "kis", Quality.ESTIMATED)
    with pytest.raises(ValueError, match="name"):
        BoardArtifact(D, "market", {}, "board-1", "sha", TS, TS, "kis", OK)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="label"):
        dataclasses.replace(_stock_day(D), label="new")
