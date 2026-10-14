"""`diag.daily`(kbj.services.engine.diag) — 메모리 저장소 + p7_world 합성 입력.

D−1 확정·D estimated 덮기, 성분 빠짐·대체(ATM IV)·VKOSPI 이력, GEX 체제(visible 일 때만), 수급
합(백만원 →
원), 재계산 같음(저장한 이력으로), 입력이 하나도 없어도 판정 보류로 끝난다(오류 아님).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from kbj.config.diag import DiagConfig, load_diag
from kbj.config.markets import load_markets
from kbj.core.calendar import TradingCalendar
from kbj.core.gex.records import InvestorRecord, LevelRecord, MetricRecord
from kbj.core.p7_rows import VolIndexRow
from kbj.core.quality import Quality
from kbj.core.rows import Investor, InvestorDay
from kbj.core.time import KST
from kbj.engines.diagnosis.fear_greed import SUBSTITUTE_ATM
from kbj.services.engine import diag, diag_pub
from kbj.services.engine.diag import TodayValue, compute_daily, run_day
from kbj.services.scheduler.handlers import JobContext
from kbj.store.repos.memory import MemoryRepos, memory_repos
from tests.unit.engines.diagnosis.diag_seed import seed_public, world

CFG = load_diag()
MK = load_markets()
CAL = TradingCalendar.default()
DAY = date(2026, 12, 10)
NOW = datetime(2026, 12, 10, 16, 0, tzinfo=KST)


def _values(v: str = "1", q: Quality = Quality.OK):
    def f(repos: object, day: date, markets: object, cfg: DiagConfig) -> dict[str, TodayValue]:
        del repos, markets
        base = Decimal(v) + Decimal(day.toordinal() % 7)
        return {k: TodayValue(base, q, "SYN") for k in cfg.keys("login")}

    return f


def _seeded() -> MemoryRepos:
    r = memory_repos()
    w = world()
    seed_public(r, w, received_at=NOW)
    w.fill_gex(r.gex)
    diag_pub.run(JobContext(
        job="diag.public", as_of=DAY.isoformat(), run_id="t", attempt=1,
        now=datetime(2026, 12, 10, 8, 40, tzinfo=KST),
        resources={"repos": r, "kr": CAL, "diag_config": CFG},
    ))  # fmt: skip
    return r


def test_run_day_writes_prev_confirmed_and_today_estimated() -> None:
    r = _seeded()
    detail = run_day(r, day=DAY, now=NOW, cfg=CFG, markets=MK, cal=CAL, market_values=_values())
    prev = CAL.prev_trading_day(DAY)
    assert detail["prev"]["day"] == prev.isoformat()
    today = {x.key: x for x in r.diag.indicators_on(DAY)}
    before = {x.key: x for x in r.diag.indicators_on(prev)}
    assert set(today) == set(CFG.keys("login")) == set(before)
    assert all(x.quality is not Quality.OK for x in today.values())  # 그날은 estimated
    assert all("이력 부족" in " ".join(x.reasons) for x in today.values())
    v = r.diag.verdict_on(DAY)
    assert v is not None and v.source == "KBJ:diag.daily"
    # 그날 이벤트(월물 만기) → 이벤트 주의 꼬리표
    assert "expiry" in v.event_flags
    fg = r.diag.fear_greed_history(DAY, DAY)
    assert len(fg) == 1


def test_gex_regime_follows_last_day_cycle_flip_distance() -> None:
    r = _seeded()
    w = world()
    last = max(x.ts for x in w.levels)
    dist = next(
        x for x in w.levels if x.ts == last and x.scope == "all" and x.name == "flip_distance"
    )
    out = compute_daily(r, day=DAY, cutoff=NOW, cfg=CFG, markets=MK, cal=CAL,
                        quality_cap=Quality.ESTIMATED, market_values=_values())  # fmt: skip
    want = 1 if (dist.value or 0) > 0 else -1 if (dist.value or 0) < 0 else 0
    assert out.verdict.parts.get("gex") == want


def test_gex_not_visible_is_left_out() -> None:
    r = memory_repos()
    at = datetime.combine(DAY, time(13, 30), tzinfo=KST)
    base = dict(ts=at, trade_date=DAY, session="day", scope="all", quality="ok", flag="shadow")
    r.gex.write_levels([
        LevelRecord.model_validate({**base, "name": "flip", "value": 350.0}),
        LevelRecord.model_validate({**base, "name": "flip_distance", "value": 0.5}),
    ])  # fmt: skip
    out = compute_daily(r, day=DAY, cutoff=NOW, cfg=CFG, markets=MK, cal=CAL,
                        quality_cap=None, market_values=_values())  # fmt: skip
    assert "gex" not in out.verdict.parts


def test_empty_inputs_hold_without_error() -> None:
    r = memory_repos()
    out = compute_daily(r, day=DAY, cutoff=NOW, cfg=CFG, markets=MK, cal=CAL, quality_cap=None)
    assert all(x.value is None and x.reasons for x in out.indicators)
    assert out.fear.score is None and out.fear.quality is Quality.INVALID
    assert out.verdict.label == "hold" and out.verdict.score is None


def test_vol_component_atm_substitute_then_vkospi() -> None:
    r = _seeded()
    # 월물 ATM IV(시리즈 'M:', visible)를 min_history 거래일 넣는다
    d = DAY
    metrics: list[MetricRecord] = []
    for i in range(CFG.min_history):
        at = datetime.combine(d, time(15, 0), tzinfo=KST)
        metrics.append(MetricRecord(ts=at, trade_date=d, session="day", metric="atm_iv",
                                    scope="series", key="M:270114", value=0.15 + i / 1000,
                                    payload={}, quality="ok", flag="visible"))  # fmt: skip
        d = CAL.prev_trading_day(d)
    r.gex.write_metrics(metrics)
    out = compute_daily(r, day=DAY, cutoff=NOW, cfg=CFG, markets=MK, cal=CAL,
                        quality_cap=None, market_values=_values())  # fmt: skip
    assert SUBSTITUTE_ATM in out.fear.reasons
    assert out.fear.components["vol"] == Decimal(
        "100.00"
    )  # 그날 ATM IV 가 창에서 가장 낮다 → 100 − 0
    # VKOSPI 이력이 충분하면 그것을 쓴다(대체 아님)
    rows: list[VolIndexRow] = []
    d = CAL.prev_trading_day(DAY)
    for i in range(CFG.min_history):
        rows.append(
            VolIndexRow(trade_date=d, index_id="vkospi", close=Decimal(20 - i / 10),
                        source="KRX:idx/drvprod_dd_trd", as_of=NOW, quality=Quality.OK)
        )  # fmt: skip
        d = CAL.prev_trading_day(d)
    r.diag.upsert_vol_index(rows, received_at=NOW, loaded_by="krx.vkospi")
    out2 = compute_daily(r, day=DAY, cutoff=NOW, cfg=CFG, markets=MK, cal=CAL,
                         quality_cap=None, market_values=_values())  # fmt: skip
    assert SUBSTITUTE_ATM not in out2.fear.reasons
    assert out2.fear.components["vol"] == Decimal("0.00")  # 마지막 VKOSPI 가 가장 높다 → 100 − 100


def test_foreign_flow_spot_plus_futures_in_won() -> None:
    r = memory_repos()
    days = [CAL.prev_trading_day(DAY), DAY]
    spot = [
        InvestorDay(code=c, date=d, investor=Investor.FOREIGN, net_value=v, net_qty=None,
                    source="kis", venue="KRX", quality=Quality.OK)
        for d in days for c, v in (("0001", 1_000), ("1001", 500))
    ]  # fmt: skip
    r.flows.upsert_market_days(spot, loaded_by="t", received_at=NOW)
    fut = [
        InvestorRecord(
            ts=datetime.combine(d, time(15, 30), tzinfo=KST), trade_date=d, session="day",
            market_code="K2I", sector_code="F001", investor="frgn", net_value=7,
        )
        for d in days
    ]  # fmt: skip
    r.gex.seed_investor(fut)
    got = diag._foreign(r, DAY, 2)  # pyright: ignore[reportPrivateUsage]
    # 현물 (1,000 + 500) × 2일 + 선물 7백만원 × 2일
    assert got.value == Decimal(3_000 + 14_000_000) and got.quality is Quality.OK
    r2 = memory_repos()
    r2.flows.upsert_market_days(spot, loaded_by="t", received_at=NOW)
    miss = diag._foreign(r2, DAY, 2)  # pyright: ignore[reportPrivateUsage]
    assert miss.value is None and "K200 선물" in (miss.reason or "")


def test_recompute_same_from_stored_history() -> None:
    r = _seeded()
    run_day(r, day=DAY, now=NOW, cfg=CFG, markets=MK, cal=CAL, market_values=_values())
    first = compute_daily(r, day=DAY, cutoff=NOW, cfg=CFG, markets=MK, cal=CAL,
                          quality_cap=None, market_values=_values())  # fmt: skip
    assert first.indicators[0].inputs["n"] == 2  # D−1(확정) + D
    # 다음 날 행이 생겨도 그날 계산은 같다(이력은 그날 앞의 행만)
    nxt = CAL.next_trading_day(DAY)
    run_day(r, day=nxt, now=NOW + timedelta(days=1), cfg=CFG, markets=MK, cal=CAL,
            market_values=_values(), confirm_prev=False)  # fmt: skip
    again = compute_daily(r, day=DAY, cutoff=NOW, cfg=CFG, markets=MK, cal=CAL,
                          quality_cap=None, market_values=_values())  # fmt: skip
    assert again.indicators == first.indicators


def test_backfill_fills_history_in_order() -> None:
    r = memory_repos()
    start = date(2026, 12, 1)
    res = diag.backfill(
        r, start=start, end=DAY, now=lambda d: datetime.combine(d, time(16), tzinfo=KST),
        cfg=CFG, markets=MK, cal=CAL,
    )  # fmt: skip
    assert [x["day"] for x in res] == [
        d.isoformat() for d in (date(2026, 12, n) for n in range(1, 11)) if CAL.is_trading_day(d)
    ]
