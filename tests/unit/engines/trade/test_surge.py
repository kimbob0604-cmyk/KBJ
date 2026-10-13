"""급등·급락(metrics §26 — 묶음 EXP): 문턱 경계(≥·≤ 포함)·수준 미달 제외·순위·상위 n·
합성 세계의 심은 급등(8502)·급락(2701)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from kbj.config.trade import SurgeCfg, load_trade
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.core.trade_rows import prev_ym
from kbj.engines.trade.explore import item_index
from kbj.engines.trade.surge import SOURCE, SurgeInput, classify, detect
from tests.fixtures.synthetic.p6_world import build_world

T = datetime(2026, 12, 11, 11, 30, tzinfo=KST)
D = Decimal
YM = "2026-10"


@pytest.fixture(scope="module")
def cfg() -> SurgeCfg:
    return load_trade().surge


def _flat(prev: int, cur: int) -> dict[str, Decimal | None]:
    """전년 3개월 prev, 올해 3개월 cur(평평 — YoY 와 3개월 YoY 가 같다)."""
    out: dict[str, Decimal | None] = {}
    for k in range(3):
        out[prev_ym(YM, k)] = D(cur)
        out[prev_ym(YM, 12 + k)] = D(prev)
    return out


def test_경계값_포함(cfg: SurgeCfg) -> None:
    lim = D(cfg.min_usd)
    assert classify(lim, lim / 2, cfg.surge_yoy, cfg.surge_yoy_3m, cfg) == "surge"
    assert classify(lim - 1, lim, cfg.surge_yoy, cfg.surge_yoy_3m, cfg) is None  # 수준 미달
    assert classify(lim, lim, cfg.surge_yoy - 1e-9, cfg.surge_yoy_3m, cfg) is None
    assert classify(lim, lim, cfg.surge_yoy, cfg.surge_yoy_3m - 1e-9, cfg) is None
    assert classify(D(1), lim, cfg.plunge_yoy, cfg.plunge_yoy_3m, cfg) == "plunge"
    assert classify(D(1), lim - 1, cfg.plunge_yoy, cfg.plunge_yoy_3m, cfg) is None  # 전년 수준 미달
    assert classify(D(1), lim, cfg.plunge_yoy + 1e-9, cfg.plunge_yoy_3m, cfg) is None
    assert classify(lim, lim, None, 1.0, cfg) is None and classify(lim, lim, 1.0, None, cfg) is None


def test_순위·상위n·행_모양(cfg: SurgeCfg) -> None:
    small = cfg.model_copy(update={"top": 2})
    ins = [
        SurgeInput("8501", _flat(20_000_000, 40_000_000)),
        SurgeInput("8502", _flat(20_000_000, 60_000_000), Quality.ESTIMATED),
        SurgeInput("8503", _flat(20_000_000, 40_000_000)),
        SurgeInput("8504", _flat(1_000_000, 9_000_000)),  # 수준 미달
        SurgeInput("2701", _flat(40_000_000, 10_000_000)),
        SurgeInput("2702", _flat(40_000_000, 39_000_000)),  # 문턱 미달
    ]
    rows = detect(ins, ym=YM, level="hs4", flow="exp", cfg=small, as_of=T)
    got = [(r.rule, r.rank, r.key) for r in rows]
    assert got == [("surge", 1, "8502"), ("surge", 2, "8501"), ("plunge", 1, "2701")]
    r = rows[0]
    assert r.yoy == pytest.approx(2.0) and r.yoy_3m == pytest.approx(2.0) and r.usd == D(60_000_000)
    assert r.source == SOURCE and r.quality is Quality.ESTIMATED and r.as_of == T


def test_세계_심은_급등·급락(cfg: SurgeCfg) -> None:
    w = build_world()
    idx = item_index(w.items, "exp")
    ins = [SurgeInput(k, {m: idx.usd(k, m) for m in idx.months}) for k in idx.codes_at(4)]
    rows = detect(ins, ym=YM, level="hs4", flow="exp", cfg=cfg, as_of=T)
    by = {(r.rule, r.key): r for r in rows}
    assert by[("surge", "8502")].yoy == pytest.approx(1.0)
    assert by[("plunge", "2701")].yoy == pytest.approx(-0.5)
    assert by[("plunge", "2701")].usd == D(30_000_000)
