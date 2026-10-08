"""`krx.daily`·`market.backfill` 처리기 + 백필 CLI — 가짜 KRX + 메모리 저장소(묶음 C).

옮긴 원본(ET `board/tests/test_close_source.py` — 원본은 sqlite·네이버 경로 함수 대상이라 import
경로만 바꿔 옮길 수 없어 **같은 규칙을 KRX 행·원장 우선순위로** 옮겼다. 원본 이름을 시험 머리에
적는다): SourceConstantTest·BarOverrideTest(당일 종가를 확정치로 덮는다·전일 봉은 건드리지
않는다·확정치에 없는 종목은 그대로 둔다)·SnapshotOverrideTest(시총·거래대금까지 확정치로·3퍼센트를
넘으면 예시로 남긴다·확정치에 없는 종목은 남는다)·GateTest(인증키가 없으면 사유를 돌려준다·과거 세션
확정치로 오늘 스냅을 덮지 않는다). ProvenanceTest(확정 비율 배지)는 board 엔진(묶음 E1)의 몫이다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from kbj.core.quality import Quality
from kbj.core.rows import SOURCE_RANK, Bar, EtfDay, EtfMeta, EtfType, Snap, UniverseRow
from kbj.data.spec import DataKey
from kbj.engines.etf.types import etf_type
from kbj.services.collectors import krx_daily
from kbj.services.collectors.krx_daily import Coverage
from kbj.services.scheduler.__main__ import backfill_days, listing_backfill_range, run_backfill
from kbj.services.scheduler.runner import RunEvent
from tests.fakes.krx_server import FakeKrx
from tests.unit.collectors.p3_fakes import KR, Clock, ctx, krx_client, kst, new_repos

D = date(2026, 10, 6)  # 전 거래일(화)
NOW = kst(2026, 10, 7, 8, 5)
AS_OF = D.isoformat()
NO_FLOOR = Coverage(min_rows={})
ALL = (
    "sto/stk_bydd_trd",
    "sto/ksq_bydd_trd",
    "sto/stk_isu_base_info",
    "sto/ksq_isu_base_info",
    "idx/kospi_dd_trd",
    "idx/kosdaq_dd_trd",
    "idx/krx_dd_trd",
    "etp/etf_bydd_trd",
    "etp/etn_bydd_trd",
)


def keys(*datasets: str, as_of: str = AS_OF) -> list[DataKey]:
    return [DataKey("KRX", d, as_of) for d in (datasets or ALL)]


def setup(*, publish: bool = True, stale: Any = "empty") -> tuple[Any, FakeKrx]:
    fake = FakeKrx(Clock(NOW), stale=stale)
    if publish:
        fake.publish(D)
    return new_repos(), fake


def run(repos: Any, fake: FakeKrx, ks: list[DataKey], **res: Any) -> Any:
    res.setdefault("krx_coverage", NO_FLOOR)
    return krx_daily.run(
        ctx("krx.daily", ks[0].as_of, ks, now=NOW, repos=repos, krx=krx_client(fake), **res)
    )


def kis_snap(code: str, close: float, turnover: int, market: str = "KOSPI") -> Snap:
    return Snap(code, D, None, market, None, close, 0.0, 1, turnover, False, None, None, (),
                "kis", "KRX", Quality.OK)  # fmt: skip


def test_source_constant_is_the_ledger_name() -> None:
    """SourceConstantTest — 수집과 원장이 같은 원천 이름(소문자 'krx' — 원장 1순위)을 쓴다."""
    assert krx_daily.SOURCE == "krx"
    assert SOURCE_RANK[krx_daily.SOURCE] == 0


def test_full_run_writes_every_table() -> None:
    repos, fake = setup()
    res = run(repos, fake, keys())
    assert res.status == "ok", res.detail
    snaps = repos.market.snapshots(D, source="krx")
    assert len(snaps) == 6 + 4
    assert {(s.venue, s.source) for s in snaps} == {("KRX", "krx")}
    halted = next(s for s in snaps if s.code == "990040")
    assert halted.turnover == 0  # 0 은 '거래 없음' — None('못 받음')과 다르다
    assert len(repos.market.series(None, D, 1)) == 10
    uni = repos.market.universe(D)
    stocks = [u for u in uni if u.kind is None]
    assert len(stocks) == 6 + 4
    assert {u.kind for u in uni} == {None, "etf", "etn"}
    assert stocks[0].listed_on is not None
    assert "stock_kind" in stocks[0].flags  # 주식종류는 사실로 flags 에
    idx = repos.market.index_series(None, D, 1)
    assert "코스피200" in idx  # 코드 = 공백 뺀 지수 이름
    assert idx["코스피200"][0].close is not None
    etf = repos.etf.etf_days(D, 1)
    assert len(etf) == 4
    assert all(v[-1].net_asset for v in etf.values())
    assert set(repos.etf.meta()) == set(etf)
    assert len(repos.market.series(None, D, 1, asset="etn")) == 2
    assert fake.calls() == len(ALL)


def test_empty_response_is_not_ready() -> None:
    repos, fake = setup(publish=False)
    res = run(repos, fake, keys("sto/stk_bydd_trd"))
    assert res.status == "not_ready"
    assert repos.market.snapshots(D) == []


def test_past_session_rows_never_overwrite_the_requested_day() -> None:
    """GateTest::과거_세션_확정치로_오늘_스냅을_덮지_않는다 — 응답 기준일이 다르면 쓰지 않는다."""
    fake = FakeKrx(Clock(NOW), stale="previous")
    fake.publish(date(2026, 10, 2))
    repos = new_repos()
    run(repos, fake, keys("sto/stk_bydd_trd", as_of="2026-10-02"))  # 지난 세션을 한 번 받아 둔다
    res = run(repos, fake, keys("sto/stk_bydd_trd"))  # 10-06 은 아직 — 서버가 지난 날 행을 준다
    assert res.status == "failed"
    assert "WrongDay" in res.detail["reason"]
    assert repos.market.snapshots(D) == []


def test_key_error_gives_a_reason() -> None:
    """GateTest::인증키가_없으면_사유를_돌려준다."""
    repos, fake = setup()
    res = krx_daily.run(
        ctx("krx.daily", AS_OF, keys("sto/stk_bydd_trd"), now=NOW, repos=repos,
            krx=krx_client(fake, key="wrong-key"), krx_coverage=NO_FLOOR)
    )  # fmt: skip
    assert res.status == "failed"
    assert "KrxKeyError" in res.detail["reason"]
    assert "wrong-key" not in str(res.detail)


def test_coverage_floor_and_absolute_minimum() -> None:
    repos, fake = setup()
    res = run(repos, fake, keys("sto/stk_bydd_trd"), krx_coverage=Coverage())  # 기본 하한 700
    assert res.status == "failed"
    assert "CoverageDrop" in res.detail["reason"]
    assert repos.market.snapshots(D) == []
    # 직전 거래일 같은 시장 10행 → 오늘 6행(40% 감소) — 쓰지 않는다
    prev = date(2026, 10, 2)
    repos.market.upsert_daily_bars(
        [Bar(f"9{i:05d}", prev, 1, 1, 1, 1, 1, 1, "krx", "KRX", Quality.OK) for i in range(10)],
        loaded_by="t",
    )
    repos.market.upsert_snapshots([kis_snap(f"9{i:05d}", 1, 1) for i in range(10)], loaded_by="t")
    repos.market.upsert_snapshots(
        [Snap(f"9{i:05d}", prev, None, "KOSPI", None, 1.0, 0.0, 1, 1, False, None, None, None,
              "krx", "KRX", Quality.OK) for i in range(10)],
        loaded_by="t",
    )  # fmt: skip
    res2 = run(repos, fake, keys("sto/stk_bydd_trd"))
    assert res2.status == "failed"
    assert "줄었다" in res2.detail["reason"]


def test_krx_bar_wins_over_kis_bar_and_leaves_other_rows_alone() -> None:
    """BarOverrideTest — 당일 종가를 확정치로 덮는다(원장 우선순위로 krx 를 고른다), 전일 봉은
    건드리지 않는다, 확정치에 없는 종목은 그대로 둔다."""
    repos, fake = setup()
    prev = date(2026, 10, 2)
    repos.market.upsert_daily_bars(
        [
            Bar("990010", D, 1, 1, 1, 59_000.0, 1, 1, "kis", "KRX", Quality.OK),
            Bar("990010", prev, 1, 1, 1, 58_000.0, 1, 1, "kis", "KRX", Quality.OK),
            Bar("123456", D, 1, 1, 1, 777.0, 1, 1, "kis", "KRX", Quality.OK),
        ],
        loaded_by="t",
    )
    assert run(repos, fake, keys("sto/stk_bydd_trd")).status == "ok"
    s = repos.market.series(["990010", "123456"], D, 2)
    by_day = {b.date: b for b in s["990010"]}
    assert (by_day[D].source, by_day[D].close) == ("krx", 60_400.0)
    assert (by_day[prev].source, by_day[prev].close) == ("kis", 58_000.0)
    assert s["123456"][-1].source == "kis"


def test_reconcile_runs_after_stock_keys_and_invalidates_mismatches() -> None:
    """SnapshotOverrideTest — 시총·거래대금까지 확정치로 대조, 3% 넘는 차이는 예시로, 확정치에 없는
    종목은 missing_krx 로 남는다. 불일치 KIS 스냅은 invalid, 원장은 krx 를 고른다."""
    repos, fake = setup()
    repos.market.upsert_snapshots(
        [
            kis_snap("990010", 60_400.0, 2_000_568_286_800),  # 같다
            kis_snap("990015", 49_000.0, 163_318_389_000),  # 종가 −4.2% — 예시
            kis_snap("990020", 410_500.0, 109_761_349_375),  # 거래대금 +0.92% > 0.5%
            kis_snap("123456", 1_000.0, 1),  # KRX 에 없다
        ],
        loaded_by="market.close_collect",
    )
    res = run(repos, fake, keys("sto/stk_bydd_trd"))
    assert res.status == "ok"
    rec = res.detail["reconcile"]
    assert rec["mismatch"] == 2
    assert rec["missing_krx"] == 1
    assert rec["invalidated"] == 2
    assert any(e.startswith("990015 close") for e in rec["examples"])
    verdicts = {(r.code, r.field): r.verdict for r in repos.market.reconcile(D)}
    assert verdicts[("990010", "close")] == "ok"
    assert verdicts[("990020", "turnover")] == "mismatch"
    assert verdicts[("123456", "close")] == "missing_krx"
    bad = {s.code: s for s in repos.market.snapshots(D, source="kis")}
    assert bad["990015"].quality is Quality.INVALID
    assert bad["990010"].quality is Quality.OK
    snap, _ = repos.market.snapshot(D)
    assert snap["990015"].source == "krx"


def test_reconcile_is_skipped_without_kis_close() -> None:
    repos, fake = setup()
    res = run(repos, fake, keys("sto/stk_bydd_trd"))
    assert res.detail["reconcile"] == {"skipped": "그날 KIS 마감값이 없다"}
    assert repos.market.reconcile(D) == []


def test_etf_meta_keeps_existing_classification() -> None:
    repos, fake = setup()
    old = EtfMeta("995010", "옛이름", "합성운용", "합성", "시장대표", EtfType.KR_INDEX, None,
                  "옛지수", date(2010, 1, 4), None, "ETF_ISSUERS:x", Quality.OK)  # fmt: skip
    repos.etf.upsert_meta([old], loaded_by="t", now=NOW)
    assert run(repos, fake, keys("etp/etf_bydd_trd")).status == "ok"
    m = repos.etf.meta()["995010"]
    assert m.name == "합성 200"  # KRX 이름으로
    assert (m.issuer, m.etf_type, m.listed_on) == ("합성운용", EtfType.KR_INDEX, date(2010, 1, 4))
    new = repos.etf.meta()["995020"]
    # 처음 보는 ETF 는 그날 규칙 분류(typed_meta — 묶음 E3 요청, 묶음 S 연결). 상장일은 모른다
    assert new.etf_type == etf_type(new.name or "", new.base_index)
    assert new.etf_type is not None and new.listed_on is None


def _etf_day(code: str, day: date, *, shrs: int, nav: float, close: float, net: int) -> EtfDay:
    return EtfDay(code, day, "합성 200", close, nav, shrs, net, 1, 1, 1, "코스피 200", "krx",
                  "KRX", Quality.OK)  # fmt: skip


def test_etf_flow_checks_are_recorded_at_the_end_of_krx_daily() -> None:
    """메인이 본 열린 항목(묶음 S) — record_flow_checks 가 krx.daily 끝 단계에 붙었다.

    전 거래일 995010 을 1:10 분할 전 모양(좌수 1/10, NAV ×10)으로 심으면 그날 감지 분할이
    `prv_etf.split_event`(origin detected)에 남고 detail 에 수가 실린다. 다시 돌려도 같다(멱등)."""
    repos, fake = setup()
    prev = date(2026, 10, 2)  # 10-05 대체공휴일 — 전 거래일
    repos.etf.upsert_etf_days(
        [_etf_day("995010", prev, shrs=9_265_000, nav=529_920.2, close=530_000.0,
                  net=4_909_710_653_000)],
        loaded_by="t",
    )  # fmt: skip
    res = run(repos, fake, keys("etp/etf_bydd_trd"))
    assert res.status == "ok", res.detail
    assert res.detail["etf_checks"]["flows"] >= 1
    assert res.detail["etf_checks"]["split_detected"] == 1
    ev = repos.etf.split_events()["995010"]
    assert [(e.effective_date, e.origin, round(e.ratio)) for e in ev] == [(D, "detected", 10)]
    again = run(repos, fake, keys("etp/etf_bydd_trd"))
    assert again.status == "ok"
    assert len(repos.etf.split_events()["995010"]) == 1


def test_backfill_does_not_record_etf_flow_checks() -> None:
    repos, fake = setup()
    res = krx_daily.backfill(
        ctx("market.backfill", AS_OF, keys("etp/etf_bydd_trd"), now=NOW, repos=repos,
            krx_backfill=krx_client(fake), krx_coverage=NO_FLOOR)
    )  # fmt: skip
    assert res.status == "ok"
    assert "etf_checks" not in res.detail


def test_universe_change_is_notified_once() -> None:
    repos, fake = setup()
    repos.market.upsert_universe(
        [UniverseRow("990099", date(2026, 10, 2), "빠진종목", "KOSPI", None, None, "krx",
                     Quality.OK)],
        loaded_by="t",
    )  # fmt: skip
    sent: list[tuple[str, dict[str, Any]]] = []

    def notify(text: str, **kw: Any) -> Any:
        sent.append((text, kw))
        return type("T", (), {"ok": True})()

    res = run(repos, fake, keys("sto/stk_isu_base_info"), notify=notify)
    assert res.status == "ok"
    assert res.detail["universe_changed"] == {"KOSPI": {"added": 6, "removed": 1}}
    assert len(sent) == 1
    assert sent[0][1]["kind"] == "ops.universe"
    assert "990099" in sent[0][0]


def test_backfill_handler_skips_reconcile_and_notify() -> None:
    repos, fake = setup()
    repos.market.upsert_snapshots([kis_snap("990010", 1.0, 1)], loaded_by="t")
    sent: list[str] = []
    res = krx_daily.backfill(
        ctx("market.backfill", AS_OF, keys("sto/stk_bydd_trd", "sto/stk_isu_base_info"), now=NOW,
            repos=repos, krx_backfill=krx_client(fake), krx_coverage=NO_FLOOR,
            notify=lambda t, **k: sent.append(t))
    )  # fmt: skip
    assert res.status == "ok"
    assert "reconcile" not in res.detail
    assert repos.market.reconcile(D) == []
    assert sent == []


def test_one_key_failing_does_not_stop_the_others() -> None:
    repos, fake = setup()
    fake.fail["/idx/kospi_dd_trd"] = 500
    res = run(repos, fake, keys("idx/kospi_dd_trd", "sto/ksq_bydd_trd"))
    assert res.status == "failed"
    assert res.collected == (DataKey("KRX", "sto/ksq_bydd_trd", AS_OF),)
    assert len(repos.market.snapshots(D, source="krx")) == 4


# ── 백필 CLI(`python -m kbj.services.scheduler backfill`) ─────────────────────────────────


def test_backfill_days_newest_first_trading_days_only() -> None:
    days = backfill_days(KR, date(2026, 9, 28), date(2026, 10, 7), 100)
    assert days[0] == date(2026, 10, 7)
    assert days == sorted(days, reverse=True)
    assert all(KR.is_trading_day(d) for d in days)
    assert date(2026, 10, 5) not in days  # 대체공휴일
    assert backfill_days(KR, date(2026, 9, 28), date(2026, 10, 7), 2) == days[:2]


def test_listing_backfill_range_continues_back_to_listing_or_source_floor() -> None:
    """`--to-listing`(ADR 0017): 받은 첫날 전날부터 max(최초 상장일, 원천 바닥)까지 이어 받기."""
    floor, today = date(2010, 1, 4), date(2026, 10, 7)
    listed = [date(1975, 6, 11), date(2015, 3, 2), None]
    # 아직 아무것도 없으면 오늘부터 바닥까지(상장일이 바닥보다 앞)
    assert listing_backfill_range(listed, None, floor, today) == (floor, today)
    # 받은 첫날 전날부터 이어 간다(여러 날에 나눠 받는다)
    assert listing_backfill_range(listed, date(2021, 10, 1), floor, today) == (
        floor,
        date(2021, 9, 30),
    )
    # 가장 이른 상장일이 바닥보다 뒤면 그 상장일까지
    assert listing_backfill_range([date(2015, 3, 2)], date(2021, 10, 1), floor, today) == (
        date(2015, 3, 2),
        date(2021, 9, 30),
    )
    # 다 받았으면 None
    assert listing_backfill_range(listed, floor, floor, today) is None
    assert listing_backfill_range([date(2015, 3, 2)], date(2015, 3, 2), floor, today) is None
    # 상장일을 아는 종목이 없으면 바닥까지
    assert listing_backfill_range([None], None, floor, today) == (floor, today)


class _Runner:
    def __init__(self, kinds: dict[str, str], backfill_of: tuple[str, ...] = ("krx.daily",)):
        self.kinds = kinds
        self.seen: list[str] = []
        self.registry = self
        self.backfill_of = backfill_of

    def by_name(self, _name: str) -> Any:
        return self

    def run_once(self, job: str, now: datetime, *, as_of: str) -> list[RunEvent]:
        self.seen.append(as_of)
        kind: Any = self.kinds.get(as_of, "ok")
        return [RunEvent(kind, job, as_of, now, 1, {"reason": "예산"} if kind != "ok" else {})]


def test_run_backfill_stops_at_the_first_failure() -> None:
    days = [date(2026, 10, 7), date(2026, 10, 6), date(2026, 10, 2)]
    runner = _Runner({"2026-10-06": "failed"})
    out: list[str] = []
    rc = run_backfill(runner, "market.backfill", days, lambda: NOW, out.append)
    assert rc == 1
    assert runner.seen == ["2026-10-07", "2026-10-06"]
    assert out[-1].startswith("멈춤: 2026-10-06 failed")
    runner2 = _Runner({"2026-10-06": "duplicate"})  # 이미 받은 날 — 이어 간다
    assert run_backfill(runner2, "market.backfill", days, lambda: NOW, out.append) == 0
    assert run_backfill(_Runner({}, ()), "krx.daily", days, lambda: NOW, out.append) == 1


def test_run_backfill_rebuilds_alltime_only_after_the_whole_range() -> None:
    """범위를 다 받은 뒤에만 역사적 신고가를 다시 쌓는다(§3.8 board 연결). 실패는 종료 코드 1."""
    days = [date(2026, 10, 7), date(2026, 10, 6)]
    calls: list[int] = []

    def after() -> int:
        calls.append(1)
        return 42

    out: list[str] = []
    assert run_backfill(_Runner({}), "market.backfill", days, lambda: NOW, out.append, after) == 0
    assert calls == [1]
    assert out[-1] == "역사적 신고가 다시 쌓기: 42종목"
    stopped = _Runner({"2026-10-06": "retry"})
    assert run_backfill(stopped, "market.backfill", days, lambda: NOW, out.append, after) == 1
    assert calls == [1]  # 멈춘 범위에서는 다시 쌓지 않는다

    def broken() -> int:
        raise RuntimeError("db down")

    assert run_backfill(_Runner({}), "market.backfill", days, lambda: NOW, out.append, broken) == 1
    assert out[-1] == "역사적 신고가 다시 쌓기 실패: RuntimeError"


def test_rebuild_hook_is_wired_only_for_market_backfill() -> None:
    from kbj.config.settings import Settings
    from kbj.services.scheduler.__main__ import rebuild_alltime_fn

    s = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert rebuild_alltime_fn(s, "market.backfill") is not None
    assert rebuild_alltime_fn(s, "krx.daily") is None


def test_registry_owners_of_bundle_c_resolve() -> None:
    """등록부의 owner 가 그대로 import 된다(enabled 뒤집기 전 확인 — 묶음 S 가 켠다)."""
    import yaml

    from kbj.services.scheduler.handlers import resolve
    from tests.unit.collectors.p3_fakes import ROOT

    jobs = yaml.safe_load((ROOT / "config" / "jobs.yaml").read_text(encoding="utf-8"))["jobs"]
    names = {
        "krx.daily", "market.backfill", "market.close_collect", "flows.intraday", "market.intraday",
    }  # fmt: skip
    owners = {j["name"]: j["owner"] for j in jobs if j["name"] in names}
    assert set(owners) == names
    for owner in owners.values():
        assert callable(resolve(owner))


def test_universe_market_comes_from_the_endpoint_not_the_segment_name() -> None:
    """KRX 기본정보 MKT_TP_NM 이 'KOSDAQ GLOBAL' 같은 소속 이름이어도 시장은 KOSDAQ 이다(그
    이름은 flags 에 사실로) — 마감 수집 대상에서 빠지지 않게(검증에서 찾은 경계)."""

    class OneDay:
        def daily(self, endpoint: str, bas_dd: date) -> list[dict[str, Any]]:
            assert endpoint == "/sto/ksq_isu_base_info"
            return [
                {
                    "ISU_CD": "KR7990110009", "ISU_SRT_CD": "990110", "ISU_NM": "합성글로벌",
                    "ISU_ABBRV": "합성글로벌", "LIST_DD": "20100105",
                    "MKT_TP_NM": "KOSDAQ GLOBAL", "SECUGRP_NM": "주권", "SECT_TP_NM": "",
                    "KIND_STKCERT_TP_NM": "보통주", "PARVAL": "500", "LIST_SHRS": "1000000",
                }
            ]  # fmt: skip

    repos = new_repos()
    out, _changes = krx_daily.collect(
        keys("sto/ksq_isu_base_info"), r=repos, client=OneDay(),  # type: ignore[arg-type]
        day=D, now=NOW, job="krx.daily", coverage=NO_FLOOR,
    )  # fmt: skip
    assert out.result().status == "ok", out.result().detail
    (u,) = repos.market.universe(D)
    assert u.market == "KOSDAQ"
    assert (u.flags or {}).get("mkt_tp_nm") == "KOSDAQ GLOBAL"
