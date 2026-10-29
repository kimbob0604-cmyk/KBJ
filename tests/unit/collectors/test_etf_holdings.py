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
from kbj.data.private.etf_issuers.base import FundRef, IssuerHttp, holding
from kbj.data.private.etf_issuers.registry import build
from kbj.data.spec import DataKey
from kbj.services.collectors import etf_holdings
from kbj.services.collectors.etf_holdings import DOWN_AFTER, EMPTY_LIMIT, ENGINE_VERSION, accept
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
        "down_skipped": 0,
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
    """한 운용사가 통째로 무너져도 작업은 ok — 굳은 의존인 etf.report 가 막히지 않는다(2026-10-27:
    운용사 한 곳 실패가 작업 failed → 리포트 미발송이었다). 빠진 운용사는 detail 에 이름으로."""
    w = FakeIssuers(latest=date(2026, 10, 6), fail={"kodex": 500})
    res = run(repos, w, "2026-10-07", kst(2026, 10, 7, 8))
    assert res.status == "ok" and res.collected == (key("2026-10-07"),)
    assert res.detail["failed_issuers"] == {"kodex": "삼성 KODEX"}
    assert "kodex" in res.detail["degraded"] and "reason" not in res.detail
    assert "xyz" not in str(res.detail)  # 사유의 키 꼴 값은 가렸다
    funds = repos.etf.funds()
    assert not any(f.startswith("kodex:") for f in funds)
    assert sum(1 for f in funds if f.startswith("tiger:")) == 2  # 해외형은 저장하지 않는다
    assert any(f.startswith("tiger:") for f in repos.etf.holding_dates())


def test_most_issuers_failing_fails_the_job(repos: MemoryRepos) -> None:
    """절반을 넘게 통째로 실패하면 네트워크·우리 쪽 문제로 보고 failed(등록부 재시도)."""
    w = FakeIssuers(latest=date(2026, 10, 6), fail={"kodex": 500, "sol": 503})
    res = run(repos, w, "2026-10-07", kst(2026, 10, 7, 8), keys=["kodex", "sol", "ace"])
    assert res.status == "failed" and res.collected == ()
    assert "kodex" in res.detail["reason"] and "sol" in res.detail["reason"]
    assert set(res.detail["failed_issuers"]) == {"kodex", "sol"}
    half = run(repos, w, "2026-10-07", kst(2026, 10, 7, 8, 15), keys=["kodex", "ace"])
    assert half.status == "ok"  # 절반은 넘지 않는다
    alone = run(repos, w, "2026-10-07", kst(2026, 10, 7, 8, 30), keys=["kodex"])
    assert alone.status == "failed"  # 하나뿐인 운용사가 실패 — 받은 것이 없다


def test_universe_failure_falls_back_to_known_funds(repos: MemoryRepos) -> None:
    w = FakeIssuers(latest=date(2026, 10, 6))
    run(repos, w, "2026-10-07", kst(2026, 10, 7, 8))
    w.latest = date(2026, 10, 7)
    w.fail_paths["/api/v1/kodex/product.do"] = 503
    res = run(repos, w, "2026-10-08", kst(2026, 10, 8, 8))
    assert res.status == "ok"
    assert "kodex:universe" in res.detail["errors"]
    assert res.detail["issuers"]["kodex"]["snapshots"] == 2


def test_empty_universe_is_a_universe_failure(
    repos: MemoryRepos, monkeypatch: pytest.MonkeyPatch
) -> None:
    """목록 0건 = 파서가 못 찾은 것(TIGER 2026-10-27 모양 변경) — 저장된 추적 펀드로 PDF 를 받고
    사유를 남긴다(전에는 0건을 정상으로 받아 TIGER 가 조용히 빠졌다)."""
    w = FakeIssuers(latest=date(2026, 10, 6))
    run(repos, w, "2026-10-07", kst(2026, 10, 7, 8), keys=["tiger"])
    w.latest = date(2026, 10, 7)
    http = IssuerHttp(httpx.Client(transport=w.transport()), sleep=lambda s: None)
    [tiger] = build(http, ["tiger"])
    monkeypatch.setattr(tiger, "universe", list)  # 바뀐 모양 — 0건
    c = ctx(JOB, "2026-10-08", [key("2026-10-08")], now=kst(2026, 10, 8, 8), repos=repos,
            etf_issuers=[tiger], parallel=False)  # fmt: skip
    res = etf_holdings.run(c)
    assert res.status == "ok"
    assert "0건" in res.detail["errors"]["tiger:universe"]
    assert res.detail["issuers"]["tiger"]["snapshots"] == 2
    assert all(f.track for f in repos.etf.funds().values() if f.issuer == "tiger")


class _DownAdapter:
    """펀드 8개, PDF 는 늘 접속 오류 — 내려간 운용사."""

    KEY = "kodex"
    NAME = "가짜 KODEX"
    DEPTH = "full"
    HISTORY = True

    def __init__(self) -> None:
        self.calls = 0

    def universe(self) -> list[FundRef]:
        f = funds_of("kodex")[0]
        return [FundRef(f"KD{i:03d}", None, f"{f.name} {i}") for i in range(8)]

    def holdings(self, fund_key: str, day: date) -> tuple[dict[str, HoldingRow], date]:
        self.calls += 1
        raise httpx.ConnectTimeout("handshake timed out")


def test_issuer_down_skips_remaining_funds(repos: MemoryRepos) -> None:
    """PDF 오류가 DOWN_AFTER 번 이어지면 남은 펀드는 부르지 않는다 — 접속 안 되는 호스트에 펀드마다
    재시도·시간 상한을 다 쓰지 않게(한 운용사가 작업을 수십 분 붙잡았다)."""
    down = _DownAdapter()
    c = ctx(JOB, "2026-10-07", [key("2026-10-07")], now=kst(2026, 10, 7, 8), repos=repos,
            etf_issuers=[down], parallel=False)  # fmt: skip
    res = etf_holdings.run(c)
    assert down.calls == DOWN_AFTER
    d = res.detail["issuers"]["kodex"]
    assert d["errors"] == 8 and d["down_skipped"] == 8 - DOWN_AFTER
    assert res.status == "failed" and res.detail["failed_issuers"] == {"kodex": "가짜 KODEX"}


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


def test_whole_issuer_empty_is_a_failure_not_a_streak(repos: MemoryRepos) -> None:
    """운용사 추적 펀드가 전부 빈 응답이면 통째 실패 — 빈 연속을 올리지 않아 사이트가 돌아오면
    그대로 다시 받는다(2026-10-29 RISE 45개 전부 빈 응답 → 3일 뒤 통째로 추적 제외될 뻔했다)."""
    sol = [f.fund_id for f in funds_of("sol")]
    w = FakeIssuers(latest=date(2026, 10, 6), empty_funds=set(sol))
    for i, d in enumerate(["2026-10-07", "2026-10-08", "2026-10-09", "2026-10-12"]):
        res = run(repos, w, d, kst(2026, 10, 7 + i, 8), keys=["sol"])
        iss = res.detail["issuers"]["sol"]
        assert iss["snapshots"] == 0 and iss["dropped"] == 0
        assert res.status == "failed" and set(res.detail["failed_issuers"]) == {"sol"}
        assert "sol:pdf" in res.detail["errors"]
    tracked = {fid: f for fid, f in repos.etf.funds().items() if fid.startswith("sol:")}
    assert tracked and all(f.track and f.empty_streak == 0 for f in tracked.values())
    w.empty_funds.clear()
    res = run(repos, w, "2026-10-13", kst(2026, 10, 13, 8), keys=["sol"])
    assert res.detail["issuers"]["sol"]["snapshots"] == len(tracked)


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
    """분할 감지는 그날(d4), 검산 ③ 은 LAG_MAX(3) 거래일 전(d1 — 뒤 3거래일 상장좌수로 유효 좌수가
    확정된 날 — metrics §4)."""
    d0, d1, d2, d3, d4 = (date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 2),
                          date(2026, 10, 5), date(2026, 10, 6))  # fmt: skip
    flat = lambda code: [_eday(code, d, 1_000_000, 10_000.0) for d in (d0, d1, d2, d3)]  # noqa: E731
    rows = [
        *flat("E1"), _eday("E1", d4, 1_050_000, 10_010.0),
        *flat("E2"), _eday("E2", d4, 10_000_000, 1_000.0),  # 1:10
        # d1 보고 순자산이 그날·뒤 3거래일 상장좌수 어느 것과도 맞지 않는다 → d1 검산 ③ 실패
        _eday("E3", d0, 1_000_000, 10_000.0), _eday("E3", d1, 1_000_000, 10_000.0, 10**9),
        _eday("E3", d2, 1_000_000, 10_000.0), _eday("E3", d3, 1_000_000, 10_000.0),
        _eday("E3", d4, 1_000_000, 10_000.0),
        *flat("E4"), _eday("E4", d4, 2_000_000, 5_000.0),  # 수동 1:2
    ]  # fmt: skip
    repos.etf.upsert_etf_days(rows, loaded_by="test")
    manual = SplitEvent("E4", d4, 2.0, "manual", "공시", "manual", Quality.OK)
    repos.etf.put_split_event(manual, loaded_by="test", now=kst(2026, 10, 6))
    now = kst(2026, 10, 7, 8, 30)
    out = etf_holdings.record_flow_checks(repos, d4, now=now, loaded_by="krx.daily", tol=0.02,
                                          ratios=(2, 3, 4, 5, 10))  # fmt: skip
    assert out == {"flows": 4, "split_detected": 1, "c3_failed": 1}
    ev = repos.etf.split_events()
    assert ev["E2"][0].origin == "detected" and ev["E2"][0].ratio == 10.0
    assert ev["E4"] == [manual]  # 수동 표는 그대로
    [chk] = repos.flows.checks(d1, "etf")
    assert (chk.code, chk.check_id) == ("E3", "c3")
    again = etf_holdings.record_flow_checks(repos, d4, now=now, loaded_by="krx.daily", tol=0.02,
                                            ratios=(2, 3, 4, 5, 10))  # fmt: skip
    assert again["c3_failed"] == 1 and len(repos.flows.checks(d1, "etf")) == 1  # 멱등
    empty = etf_holdings.record_flow_checks(repos, date(2026, 10, 9), now=now, loaded_by="x",
                                            tol=0.02, ratios=(2,))  # fmt: skip
    assert empty == {"flows": 0, "split_detected": 0, "c3_failed": 0}


class _ThrottledAdapter(_DownAdapter):
    """펀드 8개 중 홀수 번째 PDF 는 요청 과다로 실패 — 이어서 실패하지 않아 건너뛰기는 없다."""

    def holdings(self, fund_key: str, day: date) -> tuple[dict[str, HoldingRow], date]:
        self.calls += 1
        if self.calls % 2:
            raise RuntimeError("HTTP 429 요청 과다")
        return {"005930": holding("kodex", "005930", "가짜", 10.0, 5.0, 1_000.0)}, day


def test_partially_fetched_issuer_is_reported(repos: MemoryRepos) -> None:
    """통째 실패는 아니지만 펀드 10% 넘게 못 받은 운용사는 detail `partial_issuers` — 리포트
    머리 줄이
    '일부만 받음'으로 적는다(KODEX 7/94 가 10-20 부터 매일 드러나지 않았다)."""
    thr = _ThrottledAdapter()
    c = ctx(JOB, "2026-10-07", [key("2026-10-07")], now=kst(2026, 10, 7, 8), repos=repos,
            etf_issuers=[thr], parallel=False)  # fmt: skip
    res = etf_holdings.run(c)
    assert res.status == "ok" and "failed_issuers" not in res.detail
    assert res.detail["partial_issuers"] == {"kodex": "가짜 KODEX 4/8"}
