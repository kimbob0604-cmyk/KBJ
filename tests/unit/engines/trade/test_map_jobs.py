"""매핑 작업(묶음 MAP): `trade.map_load`(검증 실패면 옛 판 유지·local 건수만)·`trade.stock_index`
(합성 세계 — 고정 시드, 실데이터 아님 — 에서 네 상태·NaN 0·매출 기준 CFS/OFS).
"""

from __future__ import annotations

import math
from datetime import UTC, date
from pathlib import Path

import pytest

from kbj.config.trade import (
    TradeMap,
    load_hs_chapters,
    load_trade,
    load_trade_map,
    load_trade_regions,
    parse_trade_map,
)
from kbj.core.fin_rows import QuarterRow
from kbj.services.engine.trade_map import (
    JOB_INDEX,
    JOB_LOAD,
    load,
    quarter_revenue,
    run_load,
    stock_index,
)
from kbj.services.scheduler.handlers import JobContext
from kbj.store.repos import memory_repos
from tests.fixtures.synthetic.p6_world import CLOCK, build_world
from tests.unit.api.web_fixtures_p6.map import EXTRA, fill_map_world, map_api_world

NOW = CLOCK.astimezone(UTC)
EV = [{"kind": "synthetic", "note": "합성 근거"}]


def _ctx(job: str, **res: object) -> JobContext:
    return JobContext(
        job=job, as_of=CLOCK.date().isoformat(), run_id="t", attempt=1, now=NOW, resources=res
    )


def _tm(code: str, hs: str, region: str | None = None) -> TradeMap:
    st: dict[str, object] = {
        "code": code, "name": "가상", "reviewed": False, "evidence": EV,
        "items": [{"hs": hs, "flow": "exp", "weight": 0.2}],
    }  # fmt: skip
    if region:
        st["regions"] = [{"region_id": region, "hs6": "330499", "weight": 0.1}]
    return parse_trade_map({"version": 1, "stocks": [st]})


def test_map_load_공개_판_교체() -> None:
    repos = memory_repos()
    res = load(_ctx(JOB_LOAD, repos=repos))
    assert res.status == "ok", res.detail
    tm = load_trade_map()
    rows = repos.trade.stock_map(origin="public")
    assert {r.code for r in rows} == {s.code for s in tm.stocks}
    assert res.detail["public_stocks"] == len(tm.stocks) and res.detail["local_lines"] == 0
    assert all(r.map_version == res.detail["map_version"] for r in rows)


def test_map_load_검증_실패면_옛_판_유지() -> None:
    repos = memory_repos()
    ch, rg = load_hs_chapters(), load_trade_regions()
    ok = run_load(repos.trade, _ctx(JOB_LOAD), public=_tm("000001", "85"), local=None,
                  chapters=ch, regions=rg)  # fmt: skip
    assert ok.status == "ok"
    bad = run_load(repos.trade, _ctx(JOB_LOAD), public=_tm("000002", "85", "KR-41-nowhere"),
                   local=None, chapters=ch, regions=rg)  # fmt: skip
    assert bad.status == "failed" and bad.detail["public_problems"] == 1
    assert {r.code for r in repos.trade.stock_map()} == {"000001"}


def test_map_load_local_은_건수만_남긴다() -> None:
    repos = memory_repos()
    ch, rg = load_hs_chapters(), load_trade_regions()
    res = run_load(repos.trade, _ctx(JOB_LOAD), public=_tm("000001", "85"),
                   local=_tm("990077", "8542"), chapters=ch, regions=rg)  # fmt: skip
    assert res.status == "ok"
    assert repos.trade.stock_map(origin="local")[0].code == "990077"
    assert "990077" not in str(dict(res.detail))
    bad = run_load(repos.trade, _ctx(JOB_LOAD), public=_tm("000001", "85"),
                   local=_tm("990077", "9901"), chapters=ch, regions=rg)  # fmt: skip
    assert bad.status == "failed" and bad.detail["local_problems"] == 1
    assert "990077" not in str(dict(bad.detail))


def test_map_load_local_파일이_없으면_실패(tmp_path: Path) -> None:
    from kbj.config.trade import MapCfg

    cfg = load_trade().model_copy(update={"map": MapCfg(local_file=str(tmp_path / "없음.yaml"))})
    with pytest.raises(FileNotFoundError):
        load(_ctx(JOB_LOAD, repos=memory_repos(), trade_config=cfg))


def test_stock_index_빈_입력은_not_ready() -> None:
    repos = memory_repos()
    assert stock_index(_ctx(JOB_INDEX, repos=repos)).status == "not_ready"
    run_load(repos.trade, _ctx(JOB_LOAD), public=_tm("000001", "85"), local=None,
             chapters=load_hs_chapters(), regions=load_trade_regions())  # fmt: skip
    assert stock_index(_ctx(JOB_INDEX, repos=repos)).status == "not_ready"


def test_stock_index_합성_세계_네_상태() -> None:
    aw = map_api_world(index=False)
    res = stock_index(_ctx(JOB_INDEX, repos=aw.repos))
    assert res.status == "ok", res.detail
    w = build_world()
    n_codes = len({r.code for r in w.stock_map}) + len(EXTRA)
    assert res.detail["stocks"] == n_codes
    assert set(res.detail["statuses"]) == {"ok", "no_trade", "no_revenue", "short_overlap"}
    by: dict[str, str] = {}
    for r in aw.repos.trade.stock_export():
        by[r.code] = r.status
        for v in (r.yoy, r.revenue_yoy, *r.lag_corr.values()):
            assert v is None or math.isfinite(v)
    assert by["992060"] == "no_trade"
    assert by["994015"] == "no_revenue"
    assert by["992059"] == "short_overlap"
    assert "local" not in str(dict(res.detail)) or res.detail["conflicts"] == 0


def test_stock_index_다시_돌려도_같다() -> None:
    repos = memory_repos()
    fill_map_world(repos, build_world())
    a = stock_index(_ctx(JOB_INDEX, repos=repos))
    first = {(r.code, r.period): r for r in repos.trade.stock_export()}
    b = stock_index(_ctx(JOB_INDEX, repos=repos))
    assert a.detail == b.detail
    assert {(r.code, r.period): r for r in repos.trade.stock_export()} == first


def _q(fs: str, y: int, q: int, rev: int | None) -> QuarterRow:
    end = {1: date(y, 3, 31), 2: date(y, 6, 30), 3: date(y, 9, 30), 4: date(y, 12, 31)}[q]
    start = date(y, 3 * (q - 1) + 1, 1)
    return QuarterRow(
        corp_code="99000001", fiscal_year=y, fiscal_q=q, fs_div=fs,  # pyright: ignore[reportArgumentType]
        period_start=start, period_end=end, rcept_dt=None, tier="main", revenue=rev,
    )  # fmt: skip


def test_매출은_CFS_우선_섞지_않는다() -> None:
    rows = [_q("CFS", 2025, 1, 100), _q("OFS", 2025, 1, 90), _q("OFS", 2025, 2, 95)]
    assert quarter_revenue(rows) == {"2025Q1": 100}
    assert quarter_revenue([r for r in rows if r.fs_div == "OFS"]) == {"2025Q1": 90, "2025Q2": 95}
    assert quarter_revenue([_q("CFS", 2025, 1, None), _q("OFS", 2025, 1, 90)]) == {"2025Q1": 90}
