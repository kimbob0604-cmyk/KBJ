"""`market.close_collect` 처리기 — 가짜 KIS + 메모리 저장소(묶음 C, 새 시험).

덮어쓰기·차이 기록(D-P3-7), 일부 종목 실패 시 그 키만 미완(재시도는 남은 종목만), 호출 수 = 기대,
자릿수 대조(옮긴 원본: ET monitor/flow test_kis::단위를_믿지_않고_확인한다::
test_자릿수가_틀리면_리포트를_쓰지_않는다 — 여기서는 '쓰지 않는다'), 못 받은 종목은 사유와 함께
남긴다 (ET test_stockflows::test_failures_are_named_not_swallowed).
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import Investor, InvestorDay
from kbj.data.spec import DataKey
from kbj.services.collectors import market_close
from tests.fakes.kis_server import RANK_ROWS, SYMBOLS, FakeKisServer
from tests.unit.collectors.p3_fakes import (
    Clock,
    DirectKis,
    ctx,
    kst,
    new_repos,
    seed_etfs,
    seed_universe,
)

DAY = date(2026, 10, 7)
PREV = date(2026, 10, 6)
NOW = kst(2026, 10, 7, 15, 40)
AS_OF = DAY.isoformat()
DATASETS = (
    "stock_quote_eod",
    "stock_investor_daily",
    "market_investor_daily",
    "inst_foreign_top",
    "etf_investor_daily",
)


def keys(*datasets: str, venue: str = "KRX") -> list[DataKey]:
    return [DataKey("KIS", d, AS_OF, venue) for d in (datasets or DATASETS)]


def setup(hook: Any = None) -> tuple[Any, FakeKisServer, DirectKis]:
    repos = new_repos()
    seed_universe(repos, PREV)
    seed_etfs(repos, PREV)
    server = FakeKisServer(Clock(NOW))
    return repos, server, DirectKis(server, hook=hook)


def run(repos: Any, kis: DirectKis, ks: list[DataKey], attempt: int = 1) -> Any:
    return market_close.run(
        ctx("market.close_collect", AS_OF, ks, now=NOW, repos=repos, kis=kis, attempt=attempt)
    )


def test_full_run_collects_every_key_with_expected_calls() -> None:
    repos, _server, kis = setup()
    res = run(repos, kis, keys())
    assert res.status == "ok", res.detail
    assert set(res.collected) == set(keys())
    # 호출 수 = 기대: 가집계 4 + 시장 2 + 현재가 20 + 종목 투자자 20 + ETF 투자자(1,000억 이상 2)
    assert kis.count("FHPTJ04400000") == 4
    assert kis.count("FHPTJ04040000") == 2
    assert kis.count("FHKST01010100") == len(SYMBOLS)
    assert kis.count("FHKST01010900") == len(SYMBOLS) + 2
    assert {c[2] for c in kis.calls} == {market_close.Priority.P3}  # close_collect 우선순위
    snaps = repos.market.snapshots(DAY, source="kis")
    assert len(snaps) == len(SYMBOLS)
    assert {(s.quality, s.venue) for s in snaps} == {(Quality.OK, "KRX")}
    assert len(repos.market.series(None, DAY, 1)) == len(SYMBOLS)  # 고가 기준용 당일 일봉
    md = repos.flows.market_days(DAY, 1)
    assert set(md) == {"0001", "1001"}
    assert res.detail["stock_investor_daily"]["unit_check"] == "ok"


def test_close_overwrites_prelim_and_records_the_difference() -> None:
    """D-P3-7 — 장중 잠정(kis.prelim) 행을 마감 확정(kis)이 덮고 차이를 같은 트랜잭션으로 남긴다."""
    repos, _server, kis = setup()
    code = SYMBOLS[0]
    est = InvestorDay(code, DAY, Investor.FOREIGN, 123_000_000, 10, "kis.prelim", "KRX",
                      Quality.ESTIMATED)  # fmt: skip
    repos.flows.upsert_investor_days([est], revise=False, loaded_by="t", now=NOW)
    res = run(repos, kis, keys("stock_investor_daily"))
    assert res.status == "ok"
    best = {r.investor: r for r in repos.flows.days([code], DAY, DAY)[code]}
    assert best[Investor.FOREIGN].source == "kis"
    assert best[Investor.FOREIGN].quality is Quality.OK
    rev = {(v.code, v.investor): v for v in repos.flows.revisions(DAY)}
    v = rev[(code, Investor.FOREIGN)]
    assert v.est_value == 123_000_000
    assert v.diff == (best[Investor.FOREIGN].net_value or 0) - 123_000_000
    assert v.revised_at == NOW
    assert res.detail["stock_investor_daily"]["revised"] >= 1


def test_inst_foreign_top_goes_first_and_is_revised_by_the_confirmed_rows() -> None:
    repos, _server, kis = setup()
    res = run(repos, kis, keys("inst_foreign_top", "stock_investor_daily"))
    assert res.status == "ok"
    hist = repos.flows.intraday(kst(2026, 10, 7), kst(2026, 10, 8))
    assert len({q.code for q in hist}) == 2 * RANK_ROWS
    assert {q.ts for q in hist} == {kst(2026, 10, 7, 15, 30)}  # ts = 장 마감
    revs = repos.flows.revisions(DAY)
    assert len(revs) == 2 * RANK_ROWS * 2  # 종목 20 × 외국인·기관
    assert all(r.final_source == "kis" for r in revs)


def test_transient_failures_leave_only_that_key_and_retry_fetches_the_rest() -> None:
    """일부 종목 실패 → 그 키만 미완, 받은 종목은 쓴다. 재시도는 남은 종목만 부른다."""
    repos, server, kis = setup()
    server.inject("HTTP500", 3, tr_id="FHKST01010100")
    res = run(repos, kis, keys("stock_quote_eod", "market_investor_daily"))
    assert res.status == "failed"
    assert res.collected == (DataKey("KIS", "market_investor_daily", AS_OF, "KRX"),)
    assert "3/20 종목 미완" in res.detail["reason"]
    assert len(repos.market.snapshots(DAY, source="kis")) == 17
    before = kis.count("FHKST01010100")
    res2 = run(repos, kis, keys("stock_quote_eod"), attempt=2)
    assert res2.status == "ok"
    assert kis.count("FHKST01010100") - before == 3  # 남은 종목만
    assert len(repos.market.snapshots(DAY, source="kis")) == 20


def test_stock_specific_failures_are_named_and_tolerated_up_to_a_cap() -> None:
    """ET test_failures_are_named_not_swallowed — 못 받은 종목은 빼지 않고 사유와 함께."""
    bad = {SYMBOLS[3]}

    def hook(tr: str, params: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        if tr == "FHKST01010100" and params.get("FID_INPUT_ISCD") in bad:
            return {"rt_cd": "1", "msg_cd": "OPSQ9999", "msg1": "없는 종목"}
        return body

    repos, _server, kis = setup(hook)
    res = run(repos, kis, keys("stock_quote_eod"))
    assert res.status == "ok"
    d = res.detail["stock_quote_eod"]
    assert d["skipped_codes"] == 1
    assert SYMBOLS[3] in d["skipped_sample"]
    assert "OPSQ9999" in d["skipped_sample"][SYMBOLS[3]]


def test_too_many_stock_specific_failures_fail_the_key() -> None:
    def hook(tr: str, params: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        if tr == "FHKST01010100":
            return {"rt_cd": "0", "output": {"stck_shrn_iscd": params["FID_INPUT_ISCD"]}}
        return body

    repos, _server, kis = setup(hook)
    res = run(repos, kis, keys("stock_quote_eod"))
    assert res.status == "failed"
    assert "종목 고유 실패 20/20" in res.detail["reason"]


def test_unit_mismatch_writes_nothing() -> None:
    """test_kis::test_자릿수가_틀리면_리포트를_쓰지_않는다 — 금액이 10⁶ 배면 하나도 쓰지 않는다."""

    def hook(tr: str, params: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        if tr == "FHKST01010900":
            for r in body["output"]:
                for k in list(r):
                    if k.endswith("_tr_pbmn"):
                        r[k] = str(int(r[k]) * 1_000_000)
        return body

    repos, _server, kis = setup(hook)
    res = run(repos, kis, keys("stock_investor_daily"))
    assert res.status == "failed"
    assert "UnitMismatch" in res.detail["reason"]
    assert repos.flows.days(None, DAY, DAY) == {}
    assert res.detail["stock_investor_daily"]["unit_check"] == "bad"


def test_today_rows_not_there_yet_is_not_ready() -> None:
    """당일 행이 아직 없으면 직전 날짜로 대신하지 않고 not_ready(ET test_stockflows::older_day)."""

    def hook(tr: str, params: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        if tr == "FHKST01010900":
            body["output"] = [r for r in body["output"] if r["stck_bsop_date"] != "20261007"]
        return body

    repos, _server, kis = setup(hook)
    res = run(repos, kis, keys("stock_investor_daily"))
    assert res.status == "not_ready"
    assert repos.flows.days(None, DAY, DAY) == {}


def test_empty_universe_is_a_failure_not_a_guess() -> None:
    repos = new_repos()
    kis = DirectKis(FakeKisServer(Clock(NOW)))
    res = run(repos, kis, keys("stock_quote_eod"))
    assert res.status == "failed"
    assert "유니버스" in res.detail["reason"]
    assert kis.count() == 0


def test_market_all_zero_is_a_failure() -> None:
    def hook(tr: str, params: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        if tr == "FHPTJ04040000":
            for r in body["output1"]:
                for k in list(r):
                    if k.endswith("_tr_pbmn"):
                        r[k] = "0"
        return body

    repos, _server, kis = setup(hook)
    res = run(repos, kis, keys("market_investor_daily"))
    assert res.status == "failed"
    assert "KisEmpty" in res.detail["reason"]


def test_venue_outside_config_is_not_called() -> None:
    repos, _server, kis = setup()
    ks = keys("market_investor_daily", venue="NXT")
    res = run(repos, kis, ks)
    assert res.status == "ok"
    assert res.detail["venues_off"] == ["NXT"]
    assert kis.count() == 0


def test_etf_targets_use_the_net_asset_floor() -> None:
    repos = new_repos()
    seed_etfs(repos, PREV, (900, 100, 99))
    got = market_close.etf_targets(repos, DAY, 100_000_000_000)
    assert [c for c, _ in got] == ["995010", "995020"]
    with pytest.raises(RuntimeError, match="ETF 일별"):
        market_close.etf_targets(new_repos(), DAY, 1)


def test_rerun_is_idempotent() -> None:
    repos, _server, kis = setup()
    run(repos, kis, keys())
    n = kis.count()
    res = run(repos, kis, keys(), attempt=2)
    assert res.status == "ok"
    # 이미 받은 종목은 다시 부르지 않는다(현재가·종목·ETF 투자자) — 시장·가집계만 다시
    assert kis.count() - n == 4 + 2
    assert len(repos.market.snapshots(DAY, source="kis")) == len(SYMBOLS)


def test_halted_stock_without_a_today_row_does_not_block_the_key() -> None:
    """거래정지 종목은 투자자 당일 행이 없을 수 있다 — 재시도로 기다리지 않고 사유를 남긴다(행은
    만들지 않는다). 상태를 모르는 종목의 '당일 행 없음'은 여전히 미완이다(검증에서 찾은 경계)."""
    halted, lagging = SYMBOLS[4], SYMBOLS[5]

    def hook(tr: str, params: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        code = params.get("FID_INPUT_ISCD")
        if tr == "FHKST01010900" and code in (halted, lagging):
            body["output"] = [r for r in body["output"] if r["stck_bsop_date"] != "20261007"]
        if tr == "FHKST01010100" and code == halted:
            quote = cast("dict[str, Any]", body["output"])
            quote.update(temp_stop_yn="Y", acml_vol="0", acml_tr_pbmn="0")
        return body

    repos, _server, kis = setup(hook)
    res = run(repos, kis, keys("stock_quote_eod", "stock_investor_daily"))
    assert res.status == "failed"  # lagging 은 공표 지연일 수 있다 — 미완
    d = res.detail["stock_investor_daily"]
    assert d["no_trade_codes"] == 1 and halted in d["no_trade_sample"]
    assert d["retry_codes"] == 1
    assert "1/20 종목 미완" in res.detail["reason"] and lagging in res.detail["reason"]
    assert halted not in repos.flows.days([halted], DAY, DAY)

    # 지연이 풀렸다 — 거래정지 종목은 여전히 당일 행 없음
    kis.hook = lambda tr, p, b: hook(tr, p, b) if p.get("FID_INPUT_ISCD") == halted else b
    before = kis.count("FHKST01010900")
    res2 = run(repos, kis, keys("stock_investor_daily"), attempt=2)
    assert res2.status == "ok", res2.detail
    assert kis.count("FHKST01010900") - before == 2  # 남은 둘만 다시
    assert lagging in repos.flows.days([lagging], DAY, DAY)
    assert halted not in repos.flows.days([halted], DAY, DAY)


def test_universe_takes_a_missing_market_from_an_older_day_and_says_so() -> None:
    """그날 코스닥 기본정보만 실패했다 — 코스닥이 조용히 빠지지 않고 전날 목록으로 받고 적는다.
    'KOSDAQ GLOBAL' 같은 소속 이름도 코스닥으로 센다(검증에서 찾은 경계)."""
    from dataclasses import replace

    repos = new_repos()
    seed_universe(repos, date(2026, 10, 5))
    newer = [
        replace(u, as_of=PREV, market="KOSDAQ GLOBAL" if u.market == "KOSDAQ" else u.market)
        for u in repos.market.universe(date(2026, 10, 5))
    ]
    repos.market.upsert_universe(
        [u for u in newer if u.market == "KOSPI"], loaded_by="t", received_at=NOW
    )
    stale: dict[str, str] = {}
    got = market_close.stock_universe(repos, DAY, stale=stale)
    assert {m for _c, _n, m in got} == {"KOSPI", "KOSDAQ"}
    assert len(got) == len(SYMBOLS)
    assert stale == {"KOSDAQ": "2026-10-05"}

    repos.market.upsert_universe(
        [u for u in newer if u.market != "KOSPI"], loaded_by="t", received_at=NOW
    )
    stale.clear()
    got = market_close.stock_universe(repos, DAY, stale=stale)
    assert len(got) == len(SYMBOLS) and stale == {}


def test_universe_without_one_market_at_all_is_a_failure() -> None:
    repos = new_repos()
    seed_universe(repos, PREV)
    only = [u for u in repos.market.universe(PREV) if u.market == "KOSPI"]
    bare = new_repos()
    bare.market.upsert_universe(only, loaded_by="t", received_at=NOW)
    with pytest.raises(RuntimeError, match="KOSDAQ 가 없다"):
        market_close.stock_universe(bare, DAY)
