"""`fin.quarterly`·`fin.backfill`(kbj.services.collectors.fin) — 메모리 저장소 + 합성 세계 가짜
DART.

docs/p4_design.md §1.5·§3.2·§8.3. 본다: 상세 층 원시 행·정규화·비율·주식총수, OFS 폴백(#12),
주요계정 층(07:30 뒤 첫 실행·100개 묶음·직전 누적 되살리기), 정정 보고서 다시 받기(새 키), 선점(같은
실행 두 번 → 중복 0), 예산 닫힘(멈춤·failed), 백필(층·건너뛰기·백필 상한 → ok + stopped).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from typing import Any

import pytest

from kbj.config.fin import BackfillCfg
from kbj.core.fin_rows import QuarterRow
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.data.budget import BudgetExhausted
from kbj.data.spec import DataKey
from kbj.services.collectors import fin
from tests.fixtures.synthetic.p4_world import CLOCK
from tests.unit.reports.world_state import WorldState, make_state

DETAIL_CORPS = {"99400010", "99400110", "99400030"}  # #1·#12·#4


def _state(**kw: Any) -> WorldState:
    return make_state(
        corps=DETAIL_CORPS | {"99400150"}, detail={"994010", "994110", "994030"}, **kw
    )


def _truth(st: WorldState, corp: str, fs: str) -> dict[tuple[int, int], Any]:
    return {t.key: t for t in st.world.quarter_truth(corp, fs)}


def test_상세_층은_원시_행_정규화_비율_주식총수() -> None:
    st = _state()
    res = fin.quarterly(st.ctx("fin.quarterly"))
    assert res.status == "ok", res.detail
    raw_keys = st.repos.fin.raw_keys("99400010")
    assert (2026, "11014", "CFS") in raw_keys and not any(k[2] == "OFS" for k in raw_keys)
    assert {k[2] for k in st.repos.fin.raw_keys("99400110")} == {"OFS"}  # 연결 미작성 → 폴백
    truth = _truth(st, "99400010", "CFS")
    qs = st.repos.fin.quarters("99400010")
    assert len(qs) == len(truth)
    q = next(x for x in qs if x.period == "2026Q3")
    t = truth[(2026, 3)]
    assert (q.revenue, q.op, q.ebitda, q.tier) == (t.revenue, t.op, t.ebitda, "full")
    rr = st.repos.fin.ratios("99400010")
    assert rr[-1].period == "2026Q3" and rr[-1].opm is not None and rr[-1].bps is not None
    assert st.repos.filings.latest_share_count("99400010") is not None
    assert st.repos.filings.fin_unloaded() == []  # 처리 표시
    assert res.detail["detail_source"] == "resource"
    # 3개월값 대조(#4 2025 3Q 1.5%)
    q4 = next(x for x in st.repos.fin.quarters("99400030") if x.period == "2025Q3")
    assert q4.quality == Quality.ESTIMATED


def test_주요계정_층은_0730_뒤_첫_실행이_묶음으로() -> None:
    early = datetime(2026, 11, 16, 7, 0, tzinfo=KST)
    st = _state(now=CLOCK)
    res = fin.quarterly(st.ctx("fin.quarterly", now=early))
    assert res.detail.get("main.waiting", 0) > 0
    assert st.repos.fin.quarters("99400150") == []
    res2 = fin.quarterly(st.ctx("fin.quarterly", run_id="run-2"))
    assert res2.status == "ok", res2.detail
    qs = st.repos.fin.quarters("99400150", fs_div="CFS")
    assert st.repos.fin.quarters("99400150", fs_div="OFS")  # 주요계정 응답은 두 기준 다
    truth = _truth(st, "99400150", "CFS")
    assert {x.period for x in qs} == {f"{y}Q{q}" for y, q in truth}
    for x in qs:
        t = truth[(x.fiscal_year, x.fiscal_q)]
        assert (x.revenue, x.op, x.ni) == (t.revenue, t.op, t.ni) and x.tier == "main"
        assert not x.derived
    keys = [r.key for r in st.claims.rows if r.key.dataset == "fnlttMultiAcnt"]
    assert keys and all(k.as_of.startswith("2026-11-16:") for k in keys)


def test_같은_실행을_두_번_돌려도_중복_호출이_없다() -> None:
    st = _state()
    fin.quarterly(st.ctx("fin.quarterly"))
    n = len(st.dart.calls)
    # 처리 표시를 지워 대기 상태를 되살려도 이미 끝낸 키는 다시 받지 않는다(원시 행으로 다시
    # 정규화만)
    for stored in list(st.repos.filings.disclosure_t.rows.values()):
        stored.row = replace(stored.row, fin_loaded_at=None)
    res = fin.quarterly(st.ctx("fin.quarterly", run_id="run-2"))
    assert res.status == "ok"
    again = [c[0] for c in st.dart.calls[n:]]
    assert "fnlttSinglAcntAll.json" not in again and "fnlttMultiAcnt.json" not in again
    # 상세 층은 원시 행으로 다시 정규화하고 처리 표시, 주요계정 층은 그날 묶음을 이미 썼다(내일)
    assert {d.corp_code for d in st.repos.filings.fin_unloaded()} == {"99400150"}
    assert res.detail["fnlttMultiAcnt.duplicate"] > 0


def test_정정_보고서는_새_키로_다시_받는다() -> None:
    st = make_state(corps={"99400010"}, detail={"994010"}, upto=date(2026, 4, 9))
    fin.quarterly(st.ctx("fin.quarterly", now=datetime(2026, 4, 9, 21, 0, tzinfo=KST)))
    st2 = make_state(corps={"99400010"}, detail={"994010"})
    st2.repos.fin = st.repos.fin  # 앞 실행이 받아 둔 원시 행
    st2.claims = st.claims
    for d in st.repos.filings.disclosures(start=date(2020, 1, 1), end=CLOCK.date()):
        st2.repos.filings.mark_fin_loaded([d.rcept_no], now=CLOCK)
    res = fin.quarterly(st2.ctx("fin.quarterly"))
    assert res.status == "ok", res.detail
    fix = st2.world.rcept("r1.2025.11011.fix")
    assert any(r.key.as_of.endswith(fix) for r in st2.claims.rows)
    raw = st2.repos.fin.raw("99400010", 2025, "11011", "CFS")
    assert raw is not None and raw.rcept_no == fix


def test_예산이_닫히면_멈추고_실패() -> None:
    st = _state()

    class Closed:
        def financials(self, *a: Any, **k: Any) -> list[dict[str, Any]]:
            raise BudgetExhausted("dart", CLOCK.date(), 18000, 18000)

        def get_json(self, *a: Any, **k: Any) -> dict[str, Any]:
            raise BudgetExhausted("dart", CLOCK.date(), 18000, 18000)

    st.client = Closed()  # type: ignore[assignment]
    res = fin.quarterly(st.ctx("fin.quarterly"))
    assert res.status == "failed" and res.detail["stopped"] == "BudgetExhausted"
    assert st.repos.fin.quarter_corps() == set()
    assert any(r.status == "failed" for r in st.claims.rows)
    assert st.repos.filings.fin_unloaded()  # 처리 표시 안 함 — 다음 실행이 다시


def test_건별_오류는_그_키만_실패() -> None:
    st = _state()
    real = st.client

    class Flaky:
        def financials(self, corp: str, *a: Any, **k: Any) -> list[dict[str, Any]]:
            if corp == "99400030":
                raise ValueError("깨진 응답")
            return real.financials(corp, *a, **k)

        def get_json(self, *a: Any, **k: Any) -> dict[str, Any]:
            return real.get_json(*a, **k)

    st.client = Flaky()  # type: ignore[assignment]
    res = fin.quarterly(st.ctx("fin.quarterly"))
    assert res.status == "failed" and res.detail["failures"] > 0
    assert st.repos.fin.quarters("99400010") and not st.repos.fin.quarters("99400030")
    assert any(d.corp_code == "99400030" for d in st.repos.filings.fin_unloaded())


def test_상세_대상_자원이_없으면_공개_데모만() -> None:
    st = _state()
    ctx = st.ctx("fin.quarterly")
    res_map = dict(ctx.resources)
    del res_map["detail_universe"]
    res = fin.quarterly(replace(ctx, resources=res_map))
    assert res.detail["detail_source"] == "public_demo_only"
    assert st.repos.fin.raw_keys("99400010") == set()
    assert all(q.tier == "main" for q in st.repos.fin.quarters("99400010"))


def test_비상장_제목_못_읽음은_처리_표시만() -> None:
    def odd(r: Any) -> Any:
        if r.kind == "periodic" and r.rcept_no.endswith("1"):
            return replace(r, title="분기보고서", base_title="분기보고서")
        return r

    st = _state(transform=odd)
    res = fin.quarterly(st.ctx("fin.quarterly"))
    assert res.detail.get("skipped.title", 0) > 0
    assert st.repos.filings.fin_unloaded() == []


def test_결산월을_모르면_비12월_제목은_미룬다() -> None:
    st = make_state(corps={"99400060"}, detail={"994060"})  # #7 3월 결산
    st.repos.filings.profile_t.remove(lambda p: True)
    res = fin.quarterly(st.ctx("fin.quarterly"))
    assert res.detail.get("waiting.profile", 0) > 0
    # 3월 결산의 제목은 12월로 읽으면 이름과 분기가 어긋난다('반기보고서 (2026.09)' → 3분기) —
    # 하나도 잘못 적재하지 않고 전부 다음 실행(기업개황 수집 뒤)으로 미룬다
    pending = st.repos.filings.fin_unloaded()
    assert len(pending) == res.detail["waiting.profile"]
    assert st.repos.fin.quarters("99400060") == []
    assert st.dart.calls == []


def test_없으면_건너뜀() -> None:
    st = make_state(corps=set())
    assert fin.quarterly(st.ctx("fin.quarterly")).status == "skipped"


@pytest.mark.parametrize("tier", ["main", "full"])
def test_백필은_층별로_받고_이미_받은_것은_건너뛴다(tier: str) -> None:
    st = _state()
    st.fin_cfg = st.fin_cfg.model_copy(update={"backfill": BackfillCfg(from_year=2025, tier=tier)})  # type: ignore[arg-type]
    key = DataKey("DART", "fnlttSinglAcntAll", "2026-11-16T21:30")
    res = fin.backfill(st.ctx("fin.backfill", keys=(key,)))
    assert res.status == "ok", res.detail and res.collected == (key,)
    if tier == "full":
        assert (2025, "11013", "CFS") in st.repos.fin.raw_keys("99400010")
        assert not any(y < 2025 for y, _r, _f in st.repos.fin.raw_keys("99400010"))
        n = len(st.dart.calls)
        fin.backfill(st.ctx("fin.backfill", run_id="run-2"))
        assert [c for c in st.dart.calls[n:] if c[0] == "fnlttSinglAcntAll.json"] == []
    else:
        qs: list[QuarterRow] = st.repos.fin.quarters("99400150", fs_div="CFS")
        assert {x.period for x in qs} >= {"2025Q1", "2025Q4", "2026Q3"}
        assert all(not x.derived for x in qs)


def test_백필_상한에_닿으면_멈추고_ok() -> None:
    st = _state()
    st.fin_cfg = st.fin_cfg.model_copy(
        update={"backfill": BackfillCfg(from_year=2025, tier="full")}
    )
    real = st.client
    calls = {"n": 0}

    class Capped:
        def financials(self, *a: Any, **k: Any) -> list[dict[str, Any]]:
            calls["n"] += 1
            if calls["n"] > 3:
                raise BudgetExhausted("dart:backfill", CLOCK.date(), 8000, 8000)
            return real.financials(*a, **k)

        def get_json(self, *a: Any, **k: Any) -> dict[str, Any]:
            return real.get_json(*a, **k)

    st.client = Capped()  # type: ignore[assignment]
    res = fin.backfill(st.ctx("fin.backfill"))
    assert res.status == "ok" and res.detail["stopped"] == "BudgetExhausted"


def test_백필은_주식총수가_없는_상세_대상_회사에_최근_보고서의_주식총수를_넣는다() -> None:
    """분기 증분은 새 정기보고서가 올 때만 주식총수를 받아 지난 보고서의 회사는 비었다(2026-10-20 —
    오버행·내부자 비율, 지수 유동비율). 백필이 최근 보고서부터 찾아 하나 넣고, 이미 있으면 다시
    부르지 않는다."""
    st = _state()
    assert all(st.repos.filings.latest_share_count(c) is None for c in DETAIL_CORPS)
    fin.backfill(st.ctx("fin.backfill"))
    got = {c for c in DETAIL_CORPS if st.repos.filings.latest_share_count(c) is not None}
    assert got  # 합성 세계에 주식총수가 있는 상세 회사
    for c in got:  # 접수일(접수번호 앞 8자리)이 있어야 PIT 읽기(upto)에 걸린다
        sc = st.repos.filings.latest_share_count(c)
        assert sc is not None and sc.rcept_dt is not None
        assert st.repos.filings.latest_share_count(c, upto=CLOCK.date()) is not None
    n = len([c for c in st.dart.calls if c[0] == "stockTotqySttus.json"])
    fin.backfill(st.ctx("fin.backfill", run_id="run-2"))
    after = [c for c in st.dart.calls if c[0] == "stockTotqySttus.json"]
    assert len(after) - n <= 3 * (len(DETAIL_CORPS) - len(got))  # 있는 회사는 다시 부르지 않는다
    # 최대주주 현황(2026-10-21 — 지수 유동비율의 전략적 보유)도 같은 회사에 들어간다
    for c in got:
        lh = st.repos.filings.latest_largest_holder(c, upto=CLOCK.date())
        sc = st.repos.filings.latest_share_count(c)
        assert lh is not None and sc is not None and sc.common_issued is not None
        assert lh.common_held == sc.common_issued * 3 // 10


def test_백필의_응답_모양_실패는_작업_실패가_아니다(monkeypatch: pytest.MonkeyPatch) -> None:
    """회사 하나의 주식총수·최대주주 응답을 못 읽으면 적기만 하고 작업은 ok(2026-10-21 — 실패로
    두어 '키 0개를 받지 못했다'로 재시도가 돌았다, 다시 받아도 같다)."""
    from kbj.data.public.dart.doctext import ParseError

    bad = min(DETAIL_CORPS)
    real_sc, real_lh = fin.fetch_share_count, fin.fetch_largest_holder

    def sc(client: Any, corp: str, *a: Any, **kw: Any) -> Any:
        if corp == bad:
            raise ParseError(f"{corp}: 주식총수 응답에 보통주 행이 없다")
        return real_sc(client, corp, *a, **kw)

    def lh(client: Any, corp: str, *a: Any, **kw: Any) -> Any:
        if corp == bad:
            raise ParseError(f"{corp}: 최대주주 현황 응답의 접수번호가 이상하다")
        return real_lh(client, corp, *a, **kw)

    monkeypatch.setattr(fin, "fetch_share_count", sc)
    monkeypatch.setattr(fin, "fetch_largest_holder", lh)
    st = _state()
    res = fin.backfill(st.ctx("fin.backfill"))
    assert res.status == "ok", res.detail
    assert res.detail["n_unparsed"] == 2 and "failed_keys" not in res.detail
    assert st.repos.filings.latest_share_count(bad) is None
    assert any(st.repos.filings.latest_share_count(c) for c in DETAIL_CORPS - {bad})
