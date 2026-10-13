"""P6 완료 시험 공용 — 합성 관심목록 50종목 세계에 P6 **실제 처리기**를 돌린다(한 프로세스에 한 번).

근거: docs/p6_design.md §0.3('관심 종목 50개의 매핑과 겹쳐 보기 차트')·§1.6 4(WRAP), D-P6-14.
세계: `tests/fixtures/synthetic/p6_world.py`(고정 시드 — 실데이터 아님). 순서(등록부 cron 순서):

1. 창 앞 이력 — 세계의 월간 4표·HS 차원 중 마지막 달(2026-10) **전** 달만 저장소에(운영에서는 지난
   달들까지 이미 받아 둔 상태). 상장사 고유번호 표·분기 매출(DART 공개 표 — p4_world 재사용 + 신규
   상장)은 매출이 있는 종목만(없으면 `no_revenue`).
2. `trade.map_load` — 공개 매핑표 = 세계의 `origin: public` 줄 전부, VM local 매핑(관심종목 전용 —
   D-P6-14) = 세계의 local 줄 + **공개 매핑에 없는 관심종목**에 합성 줄을 하나씩 더한 파일
   (운영에서는
   사용자가 VM 파일로 준다 — 공개 레포에 관심종목을 남기지 않는다). 그래서 관심목록 50 = 매핑 50.
3. `trade.customs_monthly`(가짜 관세청 — 2026-11-15 10:00 공표, 11:00 실행) — 2026-10 한 달을 실제
   어댑터로 받는다(장별 품목·국가·품목×국가·시군구).
4. `trade.derive`(2026-10) → 5. `trade.stock_index`(세계 시계 2026-12-11).

장 목록(`hs_chapters`)은 세계의 12장 + 세계에 없는 장 99(매핑 접두 9901 — `no_trade` 종목)이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import cache
from typing import Any, Final

from pydantic import SecretStr

from kbj.config.trade import HsChapters, TradeMap, load_trade, parse_trade_map
from kbj.core.fin_rows import QuarterRow
from kbj.core.time import KST
from kbj.core.trade_rows import StockMapRow
from kbj.data.budget import DailyBudget
from kbj.data.datago import DatagoTransport
from kbj.data.public.customs.client import DATASET_IDS, CustomsClient
from kbj.data.spec import DataKey
from kbj.services.collectors import customs
from kbj.services.engine import trade as trade_job
from kbj.services.engine import trade_map as map_job
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos import MemoryRepos, memory_repos
from tests.fakes.clock import FakeClock
from tests.fakes.customs_world import CustomsWorld
from tests.fakes.datago_server import KEY, FakeDatago
from tests.fixtures.synthetic.p6_world import CHAPTERS, CLOCK, LAST_YM, P6World, build_world
from tests.unit.api.api_world import FakeClock as ApiClock
from tests.unit.api.web_fixtures_p6.flash import FlashApiWorld
from tests.unit.api.web_fixtures_p6.map import corp_of

MONTHLY_AT: Final = datetime(2026, 11, 15, 11, 0, tzinfo=KST)  # 2026-10 공표(15일 10:00) 뒤
SYN_EVIDENCE: Final = ({"kind": "synthetic", "note": "합성 근거(실측 아님)"},)


class _NoLimit:
    def acquire(self, priority: object, tr_id: str, timeout: float | None = None) -> None:
        return None

    def on_rate_limited(self) -> float:
        return 0.0


def chapters() -> HsChapters:
    names = {c: f"합성 장 {c}" for c in CHAPTERS}
    return HsChapters(version=1, chapters={**names, "99": "합성 장 99(세계에 없음)"})


def to_trade_map(rows: list[StockMapRow]) -> TradeMap:
    """세계의 매핑 줄 → 매핑표 파일 모양(공개 `trade_map.yaml`·VM local 같은 스키마)."""
    stocks: dict[str, dict[str, Any]] = {}
    for r in sorted(rows, key=lambda x: (x.code, x.hs, x.region_id)):
        s = stocks.setdefault(
            r.code,
            {"code": r.code, "name": r.name, "reviewed": False, "items": [], "regions": [],
             "evidence": [dict(e) for e in SYN_EVIDENCE]},
        )  # fmt: skip
        if r.region_id:
            s["regions"].append({"region_id": r.region_id, "hs6": r.hs, "weight": r.weight})
        else:
            s["items"].append(
                {"hs": r.hs, "flow": r.flow, "weight": r.weight, "countries": list(r.countries)}
            )
    return parse_trade_map({"version": 1, "stocks": list(stocks.values())})


def local_map(w: P6World) -> TradeMap:
    """VM local 매핑(관심종목 전용): 세계의 local 줄 + 공개 매핑에 없는 관심종목마다 HS4 합성 줄
    하나."""
    public = set(w.map_codes("public"))
    rows = [r for r in w.stock_map if r.origin == "local"]
    have = public | {r.code for r in rows}
    hs4 = sorted({h.hs for h in w.hs if h.hs_len == 4})
    names = dict(w.stocks)
    for i, code in enumerate(c for c in w.watchlist if c not in have):
        rows.append(
            StockMapRow(
                code=code, hs=hs4[i % len(hs4)], flow="exp", region_id="", name=names[code],
                weight=0.2, origin="local", reviewed=False, map_version="local",
                source="KBJ:trade_map",
                as_of=w.clock, quality=rows[0].quality if rows else w.stock_map[0].quality,
                countries=(), evidence=SYN_EVIDENCE,
            )
        )  # fmt: skip
    return to_trade_map(rows)


def _quarter(period: str) -> tuple[date, date]:
    y, q = int(period[:4]), int(period[5])
    start = date(y, 3 * (q - 1) + 1, 1)
    nxt = date(y + (q == 4), (3 * q) % 12 + 1, 1)
    return start, nxt - timedelta(days=1)


@dataclass
class P6Pipeline:
    world: P6World
    repos: MemoryRepos
    public_map: TradeMap
    local_map: TradeMap
    results: dict[str, JobResult] = field(default_factory=dict[str, JobResult])

    def api(self) -> FlashApiWorld:
        return FlashApiWorld(self.world, self.repos, ApiClock(CLOCK))


def _seed(w: P6World, repos: MemoryRepos) -> None:
    t = repos.trade
    old = lambda rows: [r for r in rows if r.ym < LAST_YM]  # noqa: E731
    t.upsert_item_monthly(old(w.items), loaded_by="sim.seed", received_at=MONTHLY_AT)
    t.upsert_country_monthly(old(w.countries), loaded_by="sim.seed", received_at=MONTHLY_AT)
    t.upsert_item_country_monthly(old(w.item_countries), loaded_by="sim.seed",
                                  received_at=MONTHLY_AT)  # fmt: skip
    t.upsert_sigungu_monthly(old(w.sigungu), loaded_by="sim.seed", received_at=MONTHLY_AT)
    names = dict(w.stocks)
    quarters: list[QuarterRow] = []
    for code, _ in w.stocks:
        rev = w.revenue(code)
        if not rev:
            continue
        corp = corp_of(code)
        repos.filings.corp_codes[code] = (corp, names[code])
        for period, value in rev:
            start, end = _quarter(period)
            quarters.append(
                QuarterRow(
                    corp_code=corp, fiscal_year=start.year, fiscal_q=int(period[5]), fs_div="CFS",
                    period_start=start, period_end=end, rcept_dt=end + timedelta(days=45),
                    tier="main", revenue=value,
                )
            )  # fmt: skip
    repos.fin.upsert_quarters(quarters, loaded_by="sim.seed", now=MONTHLY_AT)


def _ctx(
    job: str, as_of: str, now: datetime, keys: tuple[DataKey, ...], res: dict[str, Any]
) -> JobContext:
    return JobContext(job, as_of, f"accept-{job}", 1, now.astimezone(UTC), keys, None, res)


def _ok(name: str, res: JobResult) -> JobResult:
    if res.status != "ok":
        raise RuntimeError(f"{name}: {res.status} {dict(res.detail)}")
    return res


def run() -> P6Pipeline:
    w = build_world()
    repos = memory_repos()
    _seed(w, repos)
    p = P6Pipeline(w, repos, to_trade_map([r for r in w.stock_map if r.origin == "public"]),
                   local_map(w))  # fmt: skip
    cfg = load_trade()
    ch = chapters()
    cw = CustomsWorld(w)
    base: dict[str, Any] = {
        "trade_repo": repos.trade,
        "fin_repo": repos.fin,
        "filings_repo": repos.filings,
        "trade_config": cfg,
        "hs_chapters": ch,
        "trade_regions": cw.trade_regions(),
    }
    # 2. trade.map_load(07:35)
    res = {**base, "trade_map": p.public_map, "trade_local_map": p.local_map}
    p.results["trade.map_load"] = _ok(
        "trade.map_load",
        map_job.load(_ctx(map_job.JOB_LOAD, CLOCK.date().isoformat(), MONTHLY_AT, (), res)),
    )
    # 3. trade.customs_monthly — 가짜 관세청(실제 어댑터)
    clock = FakeClock(MONTHLY_AT)
    fake = FakeDatago(clock, customs=cw)
    transport = DatagoTransport(
        SecretStr(KEY),
        limiters={d: _NoLimit() for d in DATASET_IDS},
        budgets={d: DailyBudget(None, "datago", 9500, scope=d) for d in DATASET_IDS},
        transport=fake.transport("scheduler"),
        retry_waits_s=(),
        now=clock,
        sleep=lambda _: None,
        monotonic=clock.monotonic,
    )
    keys = tuple(DataKey("DATAGO", d, LAST_YM) for d in customs.MONTHLY_ORDER)
    res = {**base, "customs": CustomsClient(transport), "trade_maps": [p.public_map, p.local_map]}
    p.results["trade.customs_monthly"] = _ok(
        "trade.customs_monthly",
        customs.monthly(_ctx("trade.customs_monthly", LAST_YM, MONTHLY_AT, keys, res)),
    )
    # 4. trade.derive(10:40) — 같은 달
    p.results["trade.derive"] = _ok(
        "trade.derive",
        trade_job.derive(
            _ctx("trade.derive", LAST_YM, MONTHLY_AT + timedelta(minutes=40), (), base)
        ),
    )
    # 5. trade.stock_index(21:00) — 세계 시계
    p.results["trade.stock_index"] = _ok(
        "trade.stock_index",
        map_job.stock_index(_ctx(map_job.JOB_INDEX, CLOCK.date().isoformat(), CLOCK, (), base)),
    )
    return p


@cache
def built() -> P6Pipeline:
    return run()
