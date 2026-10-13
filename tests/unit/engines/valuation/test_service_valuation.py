"""`fin.valuation_band` 처리기(kbj.services.engine.valuation) — 메모리 저장소.

본다: 합성 20종목 세계(`p4_world` — R 의 `fin.quarterly` 로 분기 적재)에서 valuation·scenario 상태가
기대 행렬(`p4_expect.yaml`)과 같은지, 우선주 = 보통주 배수, 분할 앞뒤 배수 연속, PIT(11-16 접수
분기는 11-13 에 안 보임), 같은 as_of 재실행 멱등, 다음 날은 그날만, 과거 재무가 바뀌면 다시
계산(재무 지문), 전 거래일 스냅이 없으면 not_ready, 한 종목 실패 격리, 수동 재계산 CLI(건수만).
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from kbj.config.fin import FinConfig, ValuationCfg, load_fin
from kbj.core.fin_rows import ValuationDay
from kbj.core.quality import Quality
from kbj.core.rows import Bar, UniverseRow
from kbj.core.time import KST
from kbj.data.spec import DataKey
from kbj.engines.valuation.multiples import cells_of
from kbj.services.collectors import fin
from kbj.services.engine import valuation as svc
from kbj.services.scheduler.handlers import JobContext
from kbj.store.repos.memory import MemoryRepos
from tests.unit.engines.valuation.val_rows import CODE, CORP, PREF, snap, year_of_quarters
from tests.unit.reports.world_state import make_state

AS_OF = date(2026, 11, 13)
EXPECT = Path(__file__).parents[3] / "fixtures" / "synthetic" / "p4_expect.yaml"


def _ctx(
    repos: Any, cfg: FinConfig, *, as_of: date = AS_OF, keys: tuple[DataKey, ...] = ()
) -> JobContext:
    now = datetime.combine(as_of + timedelta(days=3), datetime.min.time(), tzinfo=KST)
    return JobContext(
        job="fin.valuation_band",
        as_of=as_of.isoformat(),
        run_id="run-1",
        attempt=1,
        now=now,
        keys=keys,
        resources={"repos": repos, "fin_config": cfg},
    )


@dataclass
class World:
    repos: MemoryRepos
    cfg: FinConfig
    first: svc.ValuationReport


@pytest.fixture(scope="module")
def world() -> World:
    st = make_state()
    res = fin.quarterly(st.ctx("fin.quarterly"))
    assert res.status == "ok", res.detail
    w = st.world
    repos = MemoryRepos(filings=st.repos.filings, fin=st.repos.fin)
    repos.market.upsert_daily_bars(w.bars(), loaded_by="test", received_at=st.now)
    repos.market.upsert_snapshots(w.snaps(), loaded_by="test", received_at=st.now)
    repos.market.upsert_universe(w.universe(), loaded_by="test", received_at=st.now)
    repos.consensus.put_snapshot(
        [c for c in w.consensus if c.snap_date <= AS_OF], loaded_by="test", now=st.now
    )
    cfg = load_fin()
    rep = svc.run_valuation(repos, AS_OF, cfg=cfg, now=st.now, loaded_by="fin.valuation_band")
    return World(repos, cfg, rep)


def _state(day: ValuationDay, metric: str) -> str:
    c = cells_of(day)[metric]
    return "ok" if c.value is not None else f"na:{c.na_reason}"


def test_첫_실행은_5년_창을_채운다(world: World) -> None:
    d = world.first.detail()
    assert world.first.status == "ok", d
    assert d["codes"] == 20 and d["days_read"] == 1240
    assert d["bands"] == 80 and d["scenarios"] == 60 and d["share_classes"] == 20
    assert "n_errors" not in d


def test_p4_expect_행렬(world: World) -> None:
    exp = yaml.safe_load(EXPECT.read_text(encoding="utf-8"))
    assert exp["valuation_as_of"] == AS_OF.isoformat()
    repo = world.repos.consensus
    for code, e in exp["stocks"].items():
        days = repo.valuation_days(code, AS_OF, AS_OF)
        assert len(days) == 1, code
        cur = {m: _state(days[0], m) for m in ("per", "pbr", "psr", "ev_ebitda")}
        bands = {b.metric: b for b in repo.bands(code, AS_OF)}
        band = {m: "ok" if b.p50 is not None else f"na:{b.na_reason}" for m, b in bands.items()}
        sc = repo.scenarios(code, AS_OF)
        assert [r.case for r in sc] == ["bear", "base", "bull"], code
        base = sc[1]
        scen = f"ok:{base.method}" if base.method != "INSUFFICIENT" else f"na:{base.na_reason}"
        want_scen = e["scenario"]  # #8 994070 = ok:PER(p4_expect — 웨이브 3 S 가 V·A 요청대로 고침)
        assert cur == e["valuation"]["current"], code
        assert band == e["valuation"]["band"], code
        assert scen == want_scen, code


def test_우선주는_회사_배수와_보통주_기준_시나리오(world: World) -> None:
    repo = world.repos.consensus
    common = repo.valuation_days("994010", AS_OF, AS_OF)[0]
    pref = repo.valuation_days("994015", AS_OF, AS_OF)[0]
    assert pref.mktcap_company == common.mktcap_company
    for m in ("per", "pbr", "psr", "ev_ebitda", "per_fwd"):
        assert getattr(pref, m) == pytest.approx(getattr(common, m)), m
    assert pref.price != common.price
    sc_c, sc_p = repo.scenarios("994010", AS_OF), repo.scenarios("994015", AS_OF)
    assert [(r.tp, r.price) for r in sc_p] == [(r.tp, r.price) for r in sc_c]  # 보통주 종가 기준
    classes = repo.share_classes(["994015"])
    assert classes["994015"].share_class == "pref" and classes["994015"].common_code == "994010"


def test_액면분할_앞뒤_배수_연속(world: World) -> None:
    rows = world.repos.consensus.valuation_days("994160", date(2026, 10, 20), date(2026, 10, 30))
    split = date(2026, 10, 26)
    before = [r for r in rows if r.trade_date < split][-1]
    after = next(r for r in rows if r.trade_date >= split)
    assert after.price is not None and before.price is not None
    assert after.price < before.price / 4  # 주당 가격은 5분의 1
    assert after.per == pytest.approx(before.per, rel=0.1)  # 배수는 총액 ÷ 총액 — 연속
    base = world.repos.consensus.scenarios("994160", AS_OF)[1]
    assert base.eps_source == "CONSENSUS_NI_PER_SHARE"  # 분할 전 추정 EPS 를 그날 주식 수로
    assert base.upside_pct is not None and abs(base.upside_pct) < 100


def test_PIT_그날_뒤에_접수된_분기는_보지_않는다(world: World) -> None:
    repo = world.repos.consensus
    assert repo.valuation_days("994010", AS_OF, AS_OF)[0].ttm_ref == "2026Q3:CFS"  # 11-13 접수
    assert repo.valuation_days("994140", AS_OF, AS_OF)[0].ttm_ref == "2026Q2:CFS"  # 11-16 접수
    early = repo.valuation_days("994010", date(2026, 11, 12), date(2026, 11, 12))[0]
    assert early.ttm_ref == "2026Q2:CFS"


def test_같은_as_of_재실행_멱등(world: World) -> None:
    repo = world.repos.consensus
    before = (
        repo.valuation_days("994010", date(2021, 1, 1), AS_OF),
        repo.bands("994010", AS_OF),
        repo.scenarios("994010", AS_OF),
    )
    rep = svc.run_valuation(
        world.repos, AS_OF, cfg=world.cfg, now=datetime(2026, 11, 16, 9, tzinfo=KST)
    )
    assert rep.status == "ok" and rep.days_read == 1 and rep.rebuilt == 0
    after = (
        repo.valuation_days("994010", date(2021, 1, 1), AS_OF),
        repo.bands("994010", AS_OF),
        repo.scenarios("994010", AS_OF),
    )
    assert after == before


# ── 작은 합성 저장소 ────────────────────────────────────────────────────────────────


DAYS = [date(2026, 4, 1) + timedelta(days=i) for i in range(12)]


def _small_cfg() -> FinConfig:
    cfg = load_fin()
    v = ValuationCfg(
        band_days=20,
        min_days=5,
        caps=dict(cfg.valuation.caps),
        financial_ksic_prefixes=cfg.valuation.financial_ksic_prefixes,
    )
    return cfg.model_copy(update={"valuation": v})


def _small(days: list[date], *, no_common: frozenset[date] = frozenset()) -> MemoryRepos:
    r = MemoryRepos()
    now = datetime(2026, 4, 1, tzinfo=KST)
    snaps = []
    bars = []
    for i, d in enumerate(days):
        if d not in no_common:
            snaps.append(snap(CODE, d, 100.0 + i, 100))
        snaps.append(snap(PREF, d, 80.0 + i, 10, kind="pref"))
        bars.append(Bar(CODE, d, 1.0, 1.0, 1.0, 100.0 + i, 1, 1, "krx", "KRX", Quality.OK))
    r.market.upsert_snapshots(snaps, loaded_by="t", received_at=now)
    r.market.upsert_daily_bars(bars, loaded_by="t", received_at=now)
    r.market.upsert_universe(
        [
            UniverseRow(
                CODE, days[0], "모의", "KOSPI", "common", date(2010, 1, 1), "krx", Quality.OK
            ),
            UniverseRow(
                PREF, days[0], "모의우", "KOSPI", "pref", date(2010, 1, 1), "krx", Quality.OK
            ),
        ],
        loaded_by="t",
        received_at=now,
    )
    r.filings.corp_codes[CODE] = (CORP, "모의")
    r.fin.upsert_quarters(year_of_quarters(2025), loaded_by="t", now=now)
    return r


def test_전_거래일_스냅이_없으면_not_ready() -> None:
    r = _small(DAYS[:3])
    res = svc.band(_ctx(r, _small_cfg(), as_of=DAYS[5]))
    assert res.status == "not_ready" and res.collected == ()
    assert r.consensus.valuation_days(CODE, DAYS[0], DAYS[5]) == []


def test_다음_날은_그날만_계산하고_ok_면_키를_돌려준다() -> None:
    r = _small(DAYS[:8])
    cfg = _small_cfg()
    key = DataKey("KRX", "daily", DAYS[6].isoformat())
    res = svc.band(_ctx(r, cfg, as_of=DAYS[6], keys=(key,)))
    assert res.status == "ok" and res.collected == (key,)
    assert res.detail["days_read"] == 7 and res.detail["share_classes"] == 2
    res2 = svc.band(_ctx(r, cfg, as_of=DAYS[7]))
    assert res2.status == "ok" and res2.detail["days_read"] == 1
    rows = r.consensus.valuation_days(CODE, DAYS[0], DAYS[7])
    assert [x.trade_date for x in rows] == DAYS[:8]
    assert r.consensus.bands(CODE, DAYS[7])[0].n_days == 8  # 창 20 · 유효 8 ≥ 5
    mc = rows[-1].mktcap_company
    assert mc == round((100.0 + 7) * 100) + round((80.0 + 7) * 10)  # 보통 + 우선


def test_과거_재무가_바뀌면_창을_다시_계산한다() -> None:
    r = _small(DAYS[:8])
    cfg = _small_cfg()
    now = datetime(2026, 4, 10, tzinfo=KST)
    svc.run_valuation(r, DAYS[6], cfg=cfg, now=now)
    old = r.consensus.valuation_days(CODE, DAYS[0], DAYS[0])[0]
    fixed = [replace(q, ni_owner=150) for q in year_of_quarters(2025)]  # 백필·정정
    r.fin.upsert_quarters(fixed, loaded_by="t", now=now)
    rep = svc.run_valuation(r, DAYS[7], cfg=cfg, now=now)
    assert rep.rebuilt == 2 and rep.days_read == 8  # 두 종목 · 창 전부
    new = r.consensus.valuation_days(CODE, DAYS[0], DAYS[0])[0]
    assert new.per == pytest.approx((old.per or 0) / 2)


def test_한_종목_오류는_그_종목만(monkeypatch: pytest.MonkeyPatch) -> None:
    r = _small(DAYS[:3])
    real = svc.value_day

    def boom(code: str, *a: Any, **kw: Any) -> ValuationDay:
        if code == PREF:
            raise ValueError("합성 오류")
        return real(code, *a, **kw)

    monkeypatch.setattr(svc, "value_day", boom)
    rep = svc.run_valuation(r, DAYS[2], cfg=_small_cfg(), now=datetime(2026, 4, 5, tzinfo=KST))
    assert rep.status == "failed" and rep.errors == {PREF: "ValueError: 합성 오류"}
    d = rep.detail()
    assert d["error_kinds"] == {"ValueError": 1} and d["n_errors"] == 1
    assert d["errors"] == {PREF: "ValueError: 합성 오류"}  # 어느 종목이 왜 — 삼키지 않는다
    assert len(r.consensus.valuation_days(CODE, DAYS[0], DAYS[2])) == 3
    assert r.consensus.valuation_days(PREF, DAYS[0], DAYS[2]) == []
    assert r.consensus.bands(CODE) and not r.consensus.bands(PREF)


def test_보통주_시세가_빠진_날_우선주_행은_회사_시총_없이_missing_input() -> None:
    gap = DAYS[1]
    r = _small(DAYS[:3], no_common=frozenset({gap}))
    rep = svc.run_valuation(r, DAYS[2], cfg=_small_cfg(), now=datetime(2026, 4, 5, tzinfo=KST))
    assert rep.status == "ok", rep.detail()
    row = r.consensus.valuation_days(PREF, gap, gap)[0]
    assert row.mktcap_company is None and row.per is None  # 우선주만의 합으로 배수를 내지 않는다
    assert cells_of(row)["per"].na_reason is not None
    assert row.flags["na"]["per"] == "missing_input"
    ok = r.consensus.valuation_days(PREF, DAYS[2], DAYS[2])[0]
    assert ok.mktcap_company == round(102.0 * 100) + round(82.0 * 10)


def test_rebuild_는_지정_종목_창_전부() -> None:
    r = _small(DAYS[:6])
    cfg = _small_cfg()
    now = datetime(2026, 4, 10, tzinfo=KST)
    svc.run_valuation(r, DAYS[5], cfg=cfg, now=now)
    rep = svc.rebuild(r, [CODE], DAYS[5], cfg=cfg, now=now)
    assert rep.codes == 1 and rep.days_read == 6 and rep.status == "ok"


# ── 수동 재계산 CLI ────────────────────────────────────────────────────────────────


def test_CLI_rebuild_는_건수만_낸다() -> None:
    r = _small(DAYS[:6])
    cfg = _small_cfg()
    now = datetime(2026, 4, 10, tzinfo=KST)
    svc.run_valuation(r, DAYS[5], cfg=cfg, now=now)
    out = io.StringIO()
    code = svc.main(
        ["rebuild", "--as-of", DAYS[5].isoformat(), "--code", CODE],
        repos=r,
        cfg=cfg,
        now=now,
        out=out,
    )
    got = json.loads(out.getvalue())
    assert code == 0 and got["status"] == "ok"
    assert got["codes"] == 1 and got["days_read"] == 6
    assert CODE not in out.getvalue()  # 종목별 결과·값은 싣지 않는다


def test_CLI_스냅이_없으면_종료_코드_1() -> None:
    r = _small(DAYS[:3])
    out = io.StringIO()
    code = svc.main(["rebuild", "--as-of", DAYS[5].isoformat()], repos=r, cfg=_small_cfg(), out=out)
    assert code == 1 and json.loads(out.getvalue())["status"] == "not_ready"


def test_CLI_코드_형식() -> None:
    with pytest.raises(SystemExit):
        svc.main(["rebuild", "--as-of", "2026-04-01", "--code", "abc"], repos=MemoryRepos())
