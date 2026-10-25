"""관세청 수집(kbj/services/collectors/customs.py — P6 COL): 10일 잠정치 폴링·정정·열 구성 변경·
공표 전, 월간 4종(장별·국가·품목×국가·시군구)·잘림·개편, 백필 창·한도 멈춤. 가짜 관세청
(tests/fakes/customs_world.py) + 메모리 저장소 — 값은 전부 합성(p6_world)."""

from __future__ import annotations

import io
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import SecretStr

from kbj.config.trade import HsChapters, TradeConfig, TradeMap, load_trade, parse_trade_map
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.data.budget import DailyBudget
from kbj.data.datago import DatagoTransport
from kbj.data.public.customs.client import DATASET_IDS, TEN_DAY, CustomsClient
from kbj.data.public.customs.models import SIDO_CODES
from kbj.data.spec import DataKey
from kbj.services.collectors import customs as C
from kbj.services.scheduler.conditions import resolve_as_of
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos.trade import MemoryTradeRepo
from tests.fakes.clock import FakeClock
from tests.fakes.customs_world import CustomsWorld
from tests.fakes.datago_server import KEY, FakeDatago
from tests.fixtures.synthetic.p6_world import build_world

W = build_world()
CFG = load_trade()
EMPTY_CH = HsChapters(version=1)


class _NoLimit:
    def acquire(self, priority: object, tr_id: str, timeout: float | None = None) -> None:
        return None

    def on_rate_limited(self) -> float:
        return 0.0


class Rig:
    """가짜 관세청 + 실제 어댑터 + 메모리 저장소."""

    def __init__(self, at: datetime, *, cap: int = 9500) -> None:
        self.clock = FakeClock(at)
        self.world = CustomsWorld(W)
        self.fake = FakeDatago(self.clock, customs=self.world)
        self.budgets = {d: DailyBudget(None, "datago", cap, scope=d) for d in DATASET_IDS}
        t = DatagoTransport(
            SecretStr(KEY),
            limiters={d: _NoLimit() for d in DATASET_IDS},
            budgets=self.budgets,
            transport=self.fake.transport("test"),
            retry_waits_s=(),
            now=self.clock,
            sleep=lambda _: None,
            monotonic=self.clock.monotonic,
        )
        self.client = CustomsClient(t)
        self.repo = MemoryTradeRepo()

    def ctx(
        self,
        job: str,
        datasets: Sequence[str],
        as_of: str,
        *,
        cfg: TradeConfig = CFG,
        maps: Sequence[TradeMap] | None = None,
        chapters: HsChapters = EMPTY_CH,
    ) -> JobContext:
        keys = tuple(DataKey("DATAGO", d, as_of) for d in datasets)
        res: dict[str, Any] = {
            "trade_repo": self.repo,
            "customs": self.client,
            "trade_config": cfg,
            "trade_maps": [self.world.trade_map()] if maps is None else maps,
            "trade_regions": self.world.trade_regions(),
            "hs_chapters": chapters,
        }
        return JobContext(job, as_of, "r", 1, self.clock(), keys, None, res)

    def tenday(self, at: datetime, kinds: Sequence[str] = tuple(TEN_DAY)) -> JobResult:
        self.clock.set(at)
        cal: Any = _Cal()
        as_of = resolve_as_of("ten_day", at, cal, cal)
        ds = [TEN_DAY[k] for k in kinds]  # pyright: ignore[reportArgumentType]
        return C.tenday(self.ctx("trade.customs_tenday", ds, as_of))

    def monthly(self, at: datetime, ym: str, **kw: Any) -> JobResult:
        self.clock.set(at)
        return C.monthly(self.ctx("trade.customs_monthly", C.MONTHLY_ORDER, ym, **kw))


class _Cal:
    """resolve_as_of 의 ten_day 는 달력을 보지 않는다."""

    def is_trading_day(self, d: object) -> bool:
        return True


def kst(*a: int) -> datetime:
    return datetime(*a, tzinfo=KST)  # type: ignore[misc]


def _world_cell(kind: str, ym: str, seg: int, col: int) -> Any:
    return next(r for r in W.ten_day if (r.kind, r.ym, r.seg, r.col_no) == (kind, ym, seg, col))


# ── 10일 잠정치 ──────────────────────────────────────────────────────────────────────────────


def test_tenday_not_ready_before_publish_then_collects_the_segment() -> None:
    rig = Rig(kst(2026, 11, 11, 10, 40))
    res = rig.tenday(kst(2026, 11, 11, 10, 40))
    assert res.status == "not_ready" and not res.collected
    assert "202611 1구간 공표 전" in next(iter(res.detail["not_ready"].values()))
    # 지난달 구간(정정 반영 창)은 그래도 썼다
    assert {r.ym for r in rig.repo.ten_day("exp_item")} == {"2026-09", "2026-10"}
    at = kst(2026, 11, 11, 11, 0)
    res = rig.tenday(at)
    assert res.status == "ok" and len(res.collected) == 4
    assert {k.as_of for k in res.collected} == {"202611-1"}
    got = {r.col_no: r for r in rig.repo.ten_day("exp_item", "2026-11", "2026-11")}
    assert len(got) == 11
    for no, r in got.items():
        w = _world_cell("exp_item", "2026-11", 1, no)
        assert r.usd == w.usd and r.first_usd == w.usd and r.first_seen_at == at
        assert r.quality is Quality.ESTIMATED and r.source == "DATAGO:15157908"
        assert r.span_text == "01~10" and r.revised_at is None and r.as_of == at
    assert got[0].name == "전체" and got[1].name == "반도체"
    cols = rig.repo.ten_day_columns("exp_item")
    assert len(cols) == 11 and {c.valid_from for c in cols} == {"2026-09"}


def test_tenday_repoll_writes_nothing_new() -> None:
    rig = Rig(kst(2026, 11, 11, 11, 0))
    first = rig.tenday(kst(2026, 11, 11, 11, 0))
    again = rig.tenday(kst(2026, 11, 11, 11, 20))
    assert first.rows and first.rows > 0 and again.status == "ok" and again.rows == 0
    assert all(r.first_seen_at == kst(2026, 11, 11, 11, 0) for r in rig.repo.ten_day())


def _cell(rig: Rig, seg: int, col: int) -> Any:
    rows = rig.repo.ten_day("exp_item", "2026-11", "2026-11")
    return next(r for r in rows if (r.seg, r.col_no) == (seg, col))


def test_tenday_revision_keeps_first_value_and_marks_revised_at() -> None:
    rig = Rig(kst(2026, 12, 1, 11, 0))
    assert rig.tenday(kst(2026, 12, 1, 11, 0), ["exp_item"]).status == "ok"
    w = _world_cell("exp_item", "2026-11", 3, 0)
    r0 = next(
        r for r in rig.repo.ten_day("exp_item", "2026-11", "2026-11") if (r.seg, r.col_no) == (3, 0)
    )
    assert r0.usd == w.first_usd and r0.revised_at is None
    at = kst(2026, 12, 2, 11, 0)
    res = rig.tenday(at, ["exp_item"])
    assert res.status == "ok" and res.rows == 1  # 바뀐 칸 하나만
    r1 = next(
        r for r in rig.repo.ten_day("exp_item", "2026-11", "2026-11") if (r.seg, r.col_no) == (3, 0)
    )
    assert (r1.usd, r1.first_usd) == (w.usd, w.first_usd)
    assert r1.revised_at == at and r1.first_seen_at == kst(2026, 12, 1, 11, 0)
    assert r1.span_text == "01~30"


def test_tenday_late_and_weekend_publish() -> None:
    rig = Rig(kst(2026, 11, 11, 9, 0))
    rig.world.delay_ten("2026-11", 1, kst(2026, 11, 12, 9, 20))
    assert rig.tenday(kst(2026, 11, 11, 17, 40)).status == "not_ready"
    assert rig.tenday(kst(2026, 11, 12, 9, 20)).status == "ok"
    res = rig.tenday(kst(2026, 11, 21, 11, 0))  # 토요일
    assert res.status == "ok" and {k.as_of for k in res.collected} == {"202611-2"}
    res = rig.tenday(kst(2026, 12, 1, 11, 0))  # 월 전체
    assert {k.as_of for k in res.collected} == {"202611-3"}


def test_tenday_column_change_fails_only_that_kind_and_keeps_old_values() -> None:
    rig = Rig(kst(2026, 11, 11, 11, 0))
    rig.tenday(kst(2026, 11, 11, 11, 0))
    before = rig.repo.ten_day("exp_country")
    rig.world.change_columns("exp_country")
    res = rig.tenday(kst(2026, 11, 21, 11, 0))
    assert res.status == "failed"
    failed = res.detail["failed"]
    assert list(failed) == ["DATAGO:15157941@202611-2"]
    assert "TenDayColumnsChanged" in failed["DATAGO:15157941@202611-2"]
    assert {k.dataset for k in res.collected} == {"15157908", "15157901", "15157909"}
    assert rig.repo.ten_day("exp_country") == before


def test_ten_day_column_history_records_a_renamed_column() -> None:
    rig = Rig(kst(2026, 11, 11, 11, 0))
    W.fill(rig.repo)  # 세계의 열 이력: exp_country 10 이 2025-01 부터 '가상국-10'
    rig.tenday(kst(2026, 11, 11, 11, 0), ["exp_country"])
    cols = [c for c in rig.repo.ten_day_columns("exp_country") if c.col_no == 10]
    assert [c.name for c in sorted(cols, key=lambda c: c.valid_from)][-1] == "말레이시아"


def test_unknown_dataset_key_fails() -> None:
    rig = Rig(kst(2026, 11, 11, 11, 0))
    res = C.tenday(rig.ctx("trade.customs_tenday", ["15101609"], "202611-1"))
    assert res.status == "failed"


# ── 월간 ────────────────────────────────────────────────────────────────────────────────────


def _world_month(seq: Sequence[Any], ym: str) -> list[Any]:
    return [r for r in seq if r.ym == ym]


def _sido_ok(r: Any) -> bool:
    """세계의 시군구 행 중 그 신고 월에 쓸 수 있는 시도 코드인 것(p6_world 는 광주 29 를 개편 뒤에도
    만든다 — 실제 포털은 7월 이후 29 로 받지 않는다)."""
    return SIDO_CODES[r.sido_cd].valid_in(r.ym.replace("-", ""))


def test_monthly_four_datasets_match_the_world() -> None:
    rig = Rig(kst(2026, 11, 16, 10, 10))
    at = kst(2026, 11, 16, 10, 10)
    res = rig.monthly(at, "2026-10")
    assert res.status == "ok", res.detail
    assert [k.dataset for k in res.collected] == list(C.MONTHLY_ORDER)
    items = rig.repo.item_monthly(ym_from="2026-10", ym_to="2026-10")
    want = _world_month(W.items, "2026-10")
    assert {(r.hs, r.exp_usd, r.exp_kg, r.imp_usd) for r in items} == {
        (r.hs, r.exp_usd, r.exp_kg, r.imp_usd) for r in want
    }
    assert all(r.hs_len == len(r.hs) and r.as_of == at for r in items)
    assert any(r.hs.startswith("0") for r in items)  # 앞자리 0 복원
    assert len(rig.fake.calls("getItemtradeList")) == 97  # 장별(설정 비면 01~97)
    countries = rig.repo.country_monthly(ym_from="2026-10", ym_to="2026-10")
    assert len(countries) == 40 and countries[0].country_name.startswith("가상국-")
    ic = rig.repo.item_country_monthly(ym_from="2026-10", ym_to="2026-10")
    assert len(ic) == len(_world_month(W.item_countries, "2026-10"))
    assert len(rig.fake.calls("getNitemtradeList")) == 40  # 같은 달 국가별의 국가만
    sg = rig.repo.sigungu_monthly(ym_from="2026-10", ym_to="2026-10")
    wsg = [r for r in _world_month(W.sigungu, "2026-10") if _sido_ok(r)]
    assert {(r.sido_cd, r.sgg_nm, r.hs6, r.region_id, r.exp_usd) for r in sg} == {
        (r.sido_cd, r.sgg_nm, r.hs6, r.region_id, r.exp_usd) for r in wsg
    }
    dims = {d.hs: d for d in rig.repo.hs_dim()}
    assert set(dims) == {r.hs for r in want}
    assert dims["8502"].name_ko == "가상품목 8502" and dims["8502"].last_ym == "2026-10"


def test_monthly_not_ready_before_publish() -> None:
    rig = Rig(kst(2026, 11, 14, 10, 10))
    res = rig.monthly(kst(2026, 11, 14, 10, 10), "2026-10")
    assert res.status == "not_ready" and not res.collected
    assert len(res.detail["not_ready"]) == 4
    assert "국가 목록" in res.detail["not_ready"]["DATAGO:15100475@2026-10"]


def test_items_all_mode_falls_back_to_chapters_when_truncated() -> None:
    rig = Rig(kst(2026, 11, 16, 10, 10))
    cfg = CFG.model_copy(update={"monthly": CFG.monthly.model_copy(update={"items_mode": "all"})})
    rig.world.truncate("getItemtradeList")
    res = rig.monthly(kst(2026, 11, 16, 10, 10), "2026-10", cfg=cfg)
    assert res.status == "ok"
    assert len(rig.fake.calls("getItemtradeList")) == 1 + 97


def test_chapter_truncation_fails_the_items_key_only() -> None:
    rig = Rig(kst(2026, 11, 16, 10, 10))
    rig.world.truncate("getItemtradeList")
    res = rig.monthly(kst(2026, 11, 16, 10, 10), "2026-10")
    assert res.status == "failed" and list(res.detail["failed"]) == ["DATAGO:15101609@2026-10"]
    assert "잘렸다" in res.detail["failed"]["DATAGO:15101609@2026-10"]
    assert len(res.collected) == 3 and rig.repo.item_monthly() == []


def test_sigungu_skipped_without_map_regions_and_reorg_split() -> None:
    rig = Rig(kst(2026, 8, 16, 10, 10))
    empty = TradeMap(version=1)
    res = rig.monthly(kst(2026, 8, 16, 10, 10), "2026-07", maps=[empty])
    assert res.detail["skipped"]["DATAGO:15134343@2026-07"].startswith("매핑표에 지역 행")
    res = rig.monthly(kst(2026, 8, 16, 15, 10), "2026-07")
    sidos = {s.params.get("sidoCd") for s in rig.fake.calls("getSigunguPerPrlstPerAcrs")}
    assert "12" in sidos and not {"29", "46"} & sidos  # 7월 신고분은 개편 뒤 코드만
    reorg = [r for r in rig.repo.sigungu_monthly(region_id="KR-46-a01")]
    assert reorg and {r.sido_cd for r in reorg} == {"12"}


def test_chapters_and_hs6_helpers() -> None:
    assert C.chapters_of(EMPTY_CH)[:2] == ("01", "02") and len(C.chapters_of(EMPTY_CH)) == 97
    assert C.chapters_of(HsChapters(version=1, chapters={"85": "x", "03": "y"})) == ("03", "85")
    cw = CustomsWorld(W)
    assert C.sigungu_hs6([cw.trade_map()]) == tuple(
        sorted({r.hs for r in W.stock_map if r.region_id})
    )
    assert C.ym_flat("2026-10") == "202610" and C.ym_dash("202610") == "2026-10"
    # 판 2(flows) — 시군구 흐름 HS6 만(전국 흐름은 빼고). 2026-10-22 실측: 판 2 를 못 읽어 백필 0건
    v2 = parse_trade_map(
        {
            "version": 2,
            "stocks": [
                {
                    "code": "000001", "name": "가상", "reviewed": False,
                    "evidence": [{"kind": "synthetic", "note": "x"}],
                    "flows": [
                        {"id": "a", "name": "사업장", "kind": "sigungu",
                         "regions": ["KR-41-hwaseong"],
                         "products": [{"hs": "854232", "weight": 0.3}]},
                        {"id": "b", "name": "전국", "kind": "national",
                         "products": [{"hs": "8542", "weight": 0.2}]},
                    ],
                }
            ],
        }
    )  # fmt: skip
    assert C.sigungu_hs6([v2]) == ("854232",)


def test_ten_day_rows_skips_unchanged_and_none_keeps_values() -> None:
    from kbj.data.public.customs.models import TenDayRow as Raw

    now = kst(2026, 11, 11, 11, 0)
    raw = [Raw(source="DATAGO:15157908", kind="exp_item", month="202611", span="01~10",
               segment=1, amounts={f"{i:02d}": Decimal(5) for i in range(11)})]  # fmt: skip
    rows = C.ten_day_rows(raw, [], now)
    assert len(rows) == 11 and rows[0].usd == Decimal(5000)
    assert C.ten_day_rows(raw, rows, now) == []
    none = [raw[0].model_copy(update={"amounts": {f"{i:02d}": None for i in range(11)}})]
    assert C.ten_day_rows(none, rows, now) == []  # 빈 값은 있는 값을 지우지 않는다
    fresh = C.ten_day_rows(none, [], now)
    assert all(r.usd is None and r.quality is Quality.INVALID for r in fresh)


# ── 백필 ────────────────────────────────────────────────────────────────────────────────────


def _backfill(rig: Rig, lo: str, hi: str, **kw: Any) -> C.BackfillReport:
    args: dict[str, Any] = {
        "repo": rig.repo,
        "client": rig.client,
        "cfg": CFG,
        "from_ym": lo,
        "to_ym": hi,
        "now": rig.clock,
        "used": lambda ds: rig.budgets[ds].used(rig.clock()),
        "cap": 9500,
        "hs6s": C.sigungu_hs6([rig.world.trade_map()]),
        "regions": rig.world.trade_regions(),
        **kw,
    }
    return C.backfill(**args)


def test_backfill_two_windows_then_resumes_with_nothing_to_do() -> None:
    rig = Rig(kst(2026, 11, 20, 9, 0))
    rep = _backfill(rig, "2024-11", "2026-10", chapters=("03", "85"))
    assert not rep.stopped, rep.stopped
    assert rep.done[C.ITEMS] == ["2024-11~2025-10", "2025-11~2026-10"]
    assert len(rig.fake.calls("getNationtradeList")) == 2
    assert len(rig.fake.calls("getItemtradeList")) == 4
    months = {r.ym for r in rig.repo.country_monthly()}
    assert len(months) == 24 and min(months) == "2024-11"
    sg = rig.repo.sigungu_monthly()
    want = [r for r in W.sigungu if "2024-11" <= r.ym <= "2026-10" and _sido_ok(r)]
    assert len(sg) == len(want)
    n = len(rig.fake.seen)
    again = _backfill(rig, "2024-11", "2026-10", chapters=("03", "85"))
    assert len(rig.fake.seen) == n and not again.done
    assert again.skipped[C.SIGUNGU] == ["2024-11~2025-10", "2025-11~2026-10"]


def test_backfill_stops_a_dataset_at_the_daily_ratio() -> None:
    rig = Rig(kst(2026, 11, 20, 9, 0), cap=100)
    rep = _backfill(rig, "2025-11", "2026-10", cap=100)  # 장 97 > 100 × 0.9
    assert C.COUNTRIES in rep.done and C.ITEMS in rep.stopped
    assert "내일 다시" in rep.stopped[C.ITEMS]
    assert rig.fake.calls("getItemtradeList") == []
    out = "\n".join(rep.lines())
    assert "DATAGO:15101609 받은 창 0" in out and "멈춤" in out


def test_backfill_cli_with_injected_deps() -> None:
    rig = Rig(kst(2026, 11, 20, 9, 0))
    buf = io.StringIO()
    deps = {
        "repo": rig.repo,
        "client": rig.client,
        "cap": 9500,
        "used": lambda ds: 0,
        "now": rig.clock,
        "cfg": CFG,
        "maps": [rig.world.trade_map()],
        "regions": rig.world.trade_regions(),
        "chapters": HsChapters(version=1, chapters={"85": "가상"}),
    }
    code = C.main(["backfill", "--from", "2026-09", "--to", "2026-10", "--datasets",
                   "15101612", "15101609"], out=buf, build=lambda: deps)  # fmt: skip
    assert code == 0 and "DATAGO:15101609 받은 창 1" in buf.getvalue()
    assert {r.ym for r in rig.repo.item_monthly()} == {"2026-09", "2026-10"}
    assert all(r.hs.startswith("85") for r in rig.repo.item_monthly())


def _cli_deps(rig: Rig) -> dict[str, Any]:
    return {
        "repo": rig.repo,
        "client": rig.client,
        "cap": 9500,
        "used": lambda ds: 0,
        "now": rig.clock,
        "cfg": CFG,
        "maps": [rig.world.trade_map()],
        "regions": rig.world.trade_regions(),
        "chapters": HsChapters(version=1, chapters={"85": "가상"}),
    }


def test_backfill_cli_call_error_fails_that_dataset_with_a_reason() -> None:
    # 2026-10-22 fill-trade: 시군구 첫 실호출의 오류가 트레이스백으로 끝나 무엇이 실패했는지 몰랐다
    rig = Rig(kst(2026, 11, 20, 9, 0))
    rig.fake.inject("30", 10_000, path_part="getSigunguPerPrlstPerAcrs")  # 활용신청 없는 키
    buf = io.StringIO()
    code = C.main(["backfill", "--from", "2026-09", "--to", "2026-10", "--datasets",
                   "15101612", "15134343"], out=buf, build=lambda: _cli_deps(rig))  # fmt: skip
    out = buf.getvalue()
    assert code == 1
    assert "DATAGO:15101612 받은 창 1" in out  # 다른 데이터셋은 계속
    line = next(x for x in out.splitlines() if x.startswith("DATAGO:15134343"))
    assert "받은 창 0" in line and "실패: 2026-09~2026-10 DatagoKeyError" in line
    assert rig.repo.sigungu_monthly() == []


def test_backfill_cli_portal_quota_stops_like_the_budget() -> None:
    rig = Rig(kst(2026, 11, 20, 9, 0))
    rig.fake.inject("22", path_part="getSigunguPerPrlstPerAcrs")  # 포털 일 한도 초과
    buf = io.StringIO()
    code = C.main(["backfill", "--from", "2026-09", "--to", "2026-10", "--datasets",
                   "15134343"], out=buf, build=lambda: _cli_deps(rig))  # fmt: skip
    out = buf.getvalue()
    assert code == 0 and "멈춤: 2026-09~2026-10 포털 한도 초과" in out and "실패" not in out


def test_region_id_matches_api_name_with_sido_prefix() -> None:
    from kbj.config.trade import load_trade_regions

    regions = load_trade_regions()
    # 시군구 API 의 sggNm 은 시도를 붙여 온다(2026-10-26 실측) — 지역표는 '아산시'
    assert C._region_id(regions, "44", "충청남도 아산시", "2026-08") == "KR-44-asan"
    assert C._region_id(regions, "44", "아산시", "2026-08") == "KR-44-asan"
    assert C._region_id(regions, "43", "충청남도 아산시", "2026-08") is None
