"""`etf.collect` — 운용사 9곳 구성종목 수집 + 변동 분석(가짜 운용사 서버·메모리 저장소).

ET tracker.py `build_universe`·`snapshot`·`analyze` 의 규칙: 운용사 격리, 빈 응답 연속 → 추적 제외,
실제 기준일로 적재, 실행일 변동을 한 번에 통째로.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import httpx
import pytest

from kbj.core.quality import Quality
from kbj.core.rows import EtfDay, EtfMeta, EtfType, HoldingRow, SplitEvent
from kbj.data.private.etf_issuers.base import FundRef, IssuerHttp
from kbj.data.private.etf_issuers.registry import build
from kbj.data.spec import DataKey
from kbj.services.collectors import etf_holdings
from kbj.services.collectors.etf_holdings import EMPTY_LIMIT, ENGINE_VERSION, accept
from kbj.services.scheduler.handlers import JobResult
from kbj.store.repos import MemoryRepos, memory_repos
from tests.fakes.etf_issuer_server import ETF_IN_FUND, ISSUERS, FakeIssuers, funds_of
from tests.unit.collectors.p3_fakes import ctx, kst

JOB = "etf.collect"


def key(day: str) -> DataKey:
    return DataKey("ETF_ISSUERS", "pdf", day)


def seed_meta(repos: MemoryRepos) -> None:
    """KRX 메타 흉내 — 펀드 티커 이름(TIGER 는 목록에 이름이 없어 여기서 온다) + 펀드가 담은 ETF."""
    rows = [EtfMeta(f.ticker, f.name, None, None, None, None, None, None, None, None, "krx",
                    Quality.OK) for iss in ISSUERS for f in funds_of(iss)]  # fmt: skip
    rows.append(EtfMeta(ETF_IN_FUND, "KODEX 합성ETF200", None, None, None, None, None, None, None,
                        None, "krx", Quality.OK))  # fmt: skip
    repos.etf.upsert_meta(rows, loaded_by="test", now=kst(2026, 10, 1))


def run(repos: MemoryRepos, w: FakeIssuers, day: str, now: datetime, **res: Any) -> JobResult:
    http = IssuerHttp(httpx.Client(transport=w.transport()), sleep=lambda s: None)
    adapters = build(http, res.pop("keys", None), today=lambda: now.date())
    c = ctx(JOB, day, [key(day)], now=now, repos=repos, etf_issuers=adapters,
            parallel=res.pop("parallel", False), **res)  # fmt: skip
    return etf_holdings.run(c)


@pytest.fixture
def repos() -> MemoryRepos:
    r = memory_repos()
    seed_meta(r)
    return r


def test_first_run_snapshots_and_no_changes(repos: MemoryRepos) -> None:
    w = FakeIssuers(latest=date(2026, 10, 6))
    res = run(repos, w, "2026-10-07", kst(2026, 10, 7, 8))
    assert res.status == "ok" and res.collected == (key("2026-10-07"),)
    funds = repos.etf.funds()
    tracked = {f for f, x in funds.items() if x.track}
    assert len(tracked) == 2 * len(ISSUERS)  # kr·idx 만 — 해외형은 목록에서 뺀다
    assert all(f.split(":")[0] in ISSUERS for f in tracked)
    dates = repos.etf.holding_dates()
    assert set(dates) == tracked and all(v == [date(2026, 10, 6)] for v in dates.values())
    assert res.detail["changes"] == 0 and repos.etf.changes(date(2026, 10, 7)) == []
    assert res.detail["issuers"]["kodex"] == {
        "funds": 2,
        "snapshots": 2,
        "empty": 0,
        "errors": 0,
        "dropped": 0,
        "skipped": 1,
    }
    # 메타 분류(운용사·테마·유형)를 채웠다
    m = repos.etf.meta()[funds_of("kodex")[0].ticker]
    assert (m.issuer, m.theme, m.etf_type) == ("삼성", "반도체", EtfType.KR_THEME)
    assert repos.etf.meta()[funds_of("kodex")[2].ticker].etf_type is EtfType.OVERSEAS
    assert res.detail["meta_typed"] == len(ISSUERS) * 3 + 1
    h = repos.etf.holdings(f"kodex:{funds_of('kodex')[0].key}", date(2026, 10, 6))
    assert all(isinstance(x, HoldingRow) and x.source == "ETF_ISSUERS:kodex" for x in h.values())


def test_second_day_changes_written_once_for_all_funds(repos: MemoryRepos) -> None:
    w = FakeIssuers(latest=date(2026, 10, 6))
    run(repos, w, "2026-10-07", kst(2026, 10, 7, 8))
    w.latest = date(2026, 10, 7)
    res = run(repos, w, "2026-10-08", kst(2026, 10, 8, 8), parallel=True)
    assert res.status == "ok"
    ch = repos.etf.changes(date(2026, 10, 8))
    assert len(ch) == res.detail["changes"] > 0
    funds_with = {c.fund_id for c in ch}
    assert len(funds_with) == 2 * len(ISSUERS)  # 한 번에 — 앞 펀드 변동이 지워지지 않았다
    kinds = {c.kind for c in ch}
    assert {"NEW", "DROP"} <= kinds
    assert ETF_IN_FUND not in {c.code for c in ch}  # ETF 가 담은 ETF 는 뺀다
    assert all((c.asof, c.prev_asof) == (date(2026, 10, 7), date(2026, 10, 6)) for c in ch)
    # 다시 돌려도 같은 실행일 행이 통째로 바뀐다(멱등)
    again = run(repos, w, "2026-10-08", kst(2026, 10, 8, 8, 15))
    assert again.detail["changes"] == len(repos.etf.changes(date(2026, 10, 8))) == len(ch)
    assert ENGINE_VERSION


def test_one_issuer_failure_is_isolated(repos: MemoryRepos) -> None:
    w = FakeIssuers(latest=date(2026, 10, 6), fail={"kodex": 500})
    res = run(repos, w, "2026-10-07", kst(2026, 10, 7, 8))
    assert res.status == "failed" and res.collected == ()
    assert "kodex" in res.detail["reason"]
    assert "xyz" not in str(res.detail)  # 사유의 키 꼴 값은 가렸다
    funds = repos.etf.funds()
    assert not any(f.startswith("kodex:") for f in funds)
    assert sum(1 for f in funds if f.startswith("tiger:")) == 2  # 해외형은 저장하지 않는다
    assert any(f.startswith("tiger:") for f in repos.etf.holding_dates())


def test_universe_failure_falls_back_to_known_funds(repos: MemoryRepos) -> None:
    w = FakeIssuers(latest=date(2026, 10, 6))
    run(repos, w, "2026-10-07", kst(2026, 10, 7, 8))
    w.latest = date(2026, 10, 7)
    w.fail_paths["/api/v1/kodex/product.do"] = 503
    res = run(repos, w, "2026-10-08", kst(2026, 10, 8, 8))
    assert res.status == "ok"
    assert "kodex:universe" in res.detail["errors"]
    assert res.detail["issuers"]["kodex"]["snapshots"] == 2


def test_empty_streak_drops_fund_after_limit(repos: MemoryRepos) -> None:
    f = funds_of("sol")[1]
    w = FakeIssuers(latest=date(2026, 10, 6), empty_funds={f.fund_id})
    days = ["2026-10-07", "2026-10-08", "2026-10-09"]
    dropped: list[int] = []
    for i, d in enumerate(days):
        res = run(repos, w, d, kst(2026, 10, 7 + i, 8), keys=["sol"])
        dropped.append(res.detail["issuers"]["sol"]["dropped"])
        fund = repos.etf.funds()[f.fund_id]
        assert fund.empty_streak == i + 1
        assert fund.track is (i + 1 < EMPTY_LIMIT)
    assert dropped == [0, 0, 1]
    w.empty_funds.clear()
    res = run(repos, w, "2026-10-12", kst(2026, 10, 12, 8), keys=["sol"])
    assert res.detail["issuers"]["sol"]["snapshots"] == 1  # 제외된 펀드는 더 받지 않는다


class _FutureAdapter:
    KEY = "kodex"
    NAME = "가짜"
    DEPTH = "full"
    HISTORY = True

    def universe(self) -> list[FundRef]:
        f = funds_of("kodex")[0]
        return [FundRef(f.key, f.ticker, f.name)]

    def holdings(self, fund_key: str, day: date) -> tuple[dict[str, HoldingRow], date]:
        h = HoldingRow("990010", "x", 1.0, 1.0, 1.0, "ETF_ISSUERS:kodex", Quality.OK)
        return {"990010": h}, date(2026, 12, 31)


def test_real_date_after_request_is_rejected(repos: MemoryRepos) -> None:
    c = ctx(JOB, "2026-10-07", [key("2026-10-07")], now=kst(2026, 10, 7, 8), repos=repos,
            etf_issuers=[_FutureAdapter()], parallel=False)  # fmt: skip
    res = etf_holdings.run(c)
    assert res.status == "failed"
    assert repos.etf.holding_dates() == {}
    assert "요청일" in next(iter(res.detail["errors"].values()))


def test_accept_rule() -> None:
    assert accept("KODEX 반도체", None) == ("반도체", False)
    assert accept("TIME 코스피액티브", None) == ("시장대표", True)
    assert accept("KODEX 미국S&P500", None) is None
    assert accept("KODEX 레버리지", None) is None
    assert accept("KODEX 합성테마", "S&P 500") is None  # 기초지수가 해외


def _eday(code: str, d: date, s: int, nav: float, extra: int = 0) -> EtfDay:
    net = round(s * nav) + extra
    return EtfDay(code, d, None, nav, nav, s, net, 0, 0, None, None, "krx", "KRX", Quality.OK)


def test_record_flow_checks_writes_c3_and_detected_splits(repos: MemoryRepos) -> None:
    d0, d1 = date(2026, 10, 5), date(2026, 10, 6)
    rows = [
        _eday("E1", d0, 1_000_000, 10_000.0), _eday("E1", d1, 1_050_000, 10_010.0),
        _eday("E2", d0, 1_000_000, 10_000.0), _eday("E2", d1, 10_000_000, 1_000.0),  # 1:10
        _eday("E3", d0, 1_000_000, 10_000.0), _eday("E3", d1, 1_000_000, 10_000.0, 10**9),
        _eday("E4", d0, 1_000_000, 10_000.0), _eday("E4", d1, 2_000_000, 5_000.0),  # 수동 1:2
    ]  # fmt: skip
    repos.etf.upsert_etf_days(rows, loaded_by="test")
    manual = SplitEvent("E4", d1, 2.0, "manual", "공시", "manual", Quality.OK)
    repos.etf.put_split_event(manual, loaded_by="test", now=kst(2026, 10, 6))
    now = kst(2026, 10, 7, 8, 30)
    out = etf_holdings.record_flow_checks(repos, d1, now=now, loaded_by="krx.daily", tol=0.02,
                                          ratios=(2, 3, 4, 5, 10))  # fmt: skip
    assert out == {"flows": 4, "split_detected": 1, "c3_failed": 1}
    ev = repos.etf.split_events()
    assert ev["E2"][0].origin == "detected" and ev["E2"][0].ratio == 10.0
    assert ev["E4"] == [manual]  # 수동 표는 그대로
    [chk] = repos.flows.checks(d1, "etf")
    assert (chk.code, chk.check_id) == ("E3", "c3")
    again = etf_holdings.record_flow_checks(repos, d1, now=now, loaded_by="krx.daily", tol=0.02,
                                            ratios=(2, 3, 4, 5, 10))  # fmt: skip
    assert again["c3_failed"] == 1 and len(repos.flows.checks(d1, "etf")) == 1  # 멱등
    empty = etf_holdings.record_flow_checks(repos, date(2026, 10, 9), now=now, loaded_by="x",
                                            tol=0.02, ratios=(2,))  # fmt: skip
    assert empty == {"flows": 0, "split_detected": 0, "c3_failed": 0}
