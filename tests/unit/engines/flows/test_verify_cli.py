"""실데이터 검산 CLI(`kbj.services.engine.verify`) — 메모리 저장소 + 합성 원장(설계 §8.7 #20).

건수·분포만 내고 값·종목코드는 내지 않는다. 불가한 검산은 사유로 센다(메인 결정 R2).
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from kbj.core.rows import Investor
from kbj.services.engine import verify as V
from kbj.store.repos import memory_repos
from tests.fixtures.synthetic.ledger_gen import (
    SyntheticMarket,
    corrupt_investor,
    drop_investor,
    generate,
)

NOW = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)


def _load(m: SyntheticMarket):
    repos = memory_repos()
    repos.market.upsert_snapshots(m.snaps, loaded_by="t", received_at=NOW)
    repos.market.upsert_daily_bars(m.bars, loaded_by="t", received_at=NOW)
    repos.market.upsert_universe(m.universe, loaded_by="t", received_at=NOW)
    repos.flows.upsert_investor_days(m.investors, revise=True, loaded_by="t", now=NOW)
    repos.flows.upsert_market_days(m.market_investors, loaded_by="t", received_at=NOW)
    repos.etf.upsert_etf_days(m.etf_days, loaded_by="t", received_at=NOW)
    for ev in m.split_events:
        repos.etf.put_split_event(ev, loaded_by="t", now=NOW)
    return repos


def _no_engine(name: str) -> Any:
    raise ModuleNotFoundError(f"No module named {name!r}", name=name)  # importlib 과 같은 모양


@pytest.fixture(scope="module")
def m():
    return generate(77)


def test_합성_원장_하루는_모두_0_차이(m) -> None:
    day = m.trading_days[-2]
    rep = V.verify_day(_load(m), day, now=NOW)
    st = rep["stock"]
    assert st["c1"]["failed"] == 0 and st["c2"]["failed"] == 0
    assert st["c1"]["checked"] > 30
    assert rep["market"]["c1"] == {"status": "ok", "bucket": "le_1won"}
    assert rep["market"]["c2"]["status"] == "ok"
    assert rep["etf"]["status"] in ("ok", "unavailable")
    if rep["etf"]["status"] == "ok":  # 묶음 E3 엔진이 있으면 ③ 도 0 차이
        assert rep["etf"]["failed"] == 0 and rep["etf"]["checked"] > 0
    assert V._exit_code([rep]) == 0


def test_ETF_엔진이_없으면_불가_사유(m) -> None:
    rep = V.verify_day(_load(m), m.trading_days[-2], now=NOW, load=_no_engine)
    assert rep["etf"] == {
        "status": "unavailable",
        "reason": "kbj.engines.etf.flows 없음(묶음 E3 전)",
    }


def test_ETF_엔진_대조와_오류(m) -> None:
    calls: list[Any] = []

    def daily_flow(prev, cur, split, *, unit):
        calls.append((cur.code, split, unit))
        return SimpleNamespace(status="new" if prev is None else "ok", residual=0.4)

    fake = SimpleNamespace(daily_flow=daily_flow, check3=lambda f: f.status == "ok")
    rep = V.verify_day(
        _load(m), m.trading_days[12], now=NOW, load=lambda _: fake, net_asset_unit=1_000_000
    )
    assert rep["etf"]["checked"] >= 1 and rep["etf"]["failed"] == 0
    assert rep["etf"]["residual_hist"] == {"le_1won": rep["etf"]["checked"]}
    assert any(s is not None and s.ratio == 10.0 for _, s, _ in calls)  # 1:10 분할일 이벤트 전달
    assert {u for *_, u in calls} == {1_000_000}

    def boom(*_a, **_k):
        raise ZeroDivisionError

    bad = SimpleNamespace(daily_flow=boom, check3=lambda f: True)
    rep2 = V.verify_day(_load(m), m.trading_days[12], now=NOW, load=lambda _: bad)
    assert rep2["etf"] == {"status": "error", "error": "ZeroDivisionError"}
    assert V._exit_code([rep2]) == 2


def test_ETF_엔진_안의_import_가_깨지면_불가가_아니라_오류(m) -> None:
    """엔진은 있는데 그 안의 의존 모듈이 없으면 '불가'(종료 0)로 삼키지 않는다 — 오류·종료 2."""

    def broken(_name: str) -> Any:
        raise ModuleNotFoundError("No module named 'some_dep'", name="some_dep")

    rep = V.verify_day(_load(m), m.trading_days[-2], now=NOW, load=broken)
    assert rep["etf"] == {"status": "error", "error": "ModuleNotFoundError"}
    assert V._exit_code([rep]) == 2

    def parent_missing(_name: str) -> Any:  # 상위 패키지가 없으면 엔진이 없는 것
        raise ModuleNotFoundError("No module named 'kbj.engines.etf'", name="kbj.engines.etf")

    rep2 = V.verify_day(_load(m), m.trading_days[-2], now=NOW, load=parent_missing)
    assert rep2["etf"]["status"] == "unavailable"


def test_기타법인이_없으면_불가로_센다_R2(m) -> None:
    rep = V.verify_day(
        _load(drop_investor(m, Investor.OTHER_CORP)), m.trading_days[-2], now=NOW, load=_no_engine
    )
    c1 = rep["stock"]["c1"]
    assert c1["checked"] == 0 and c1["failed"] == 0
    assert c1["reasons"]["기타법인 미제공"] == c1["unavailable"] - c1["reasons"].get(
        "투자자별 행 없음", 0
    )
    assert rep["market"]["c1"] == {"status": "unavailable"}
    assert V._exit_code([rep]) == 0  # 불가는 실패가 아니다


def test_실패는_분포와_종료_코드_1_기록(m) -> None:
    day = m.trading_days[-2]
    bad = corrupt_investor(m, "Q00000", day, Investor.OTHER_CORP, 250_000_000)
    repos = _load(bad)
    rep = V.verify_day(repos, day, now=NOW, record=True, load=_no_engine)
    assert rep["stock"]["c1"]["failed"] == 1
    assert rep["stock"]["c1"]["residual_hist"] == {"gt_100m": 1}
    assert rep["recorded"] == 1
    assert [c.check_id for c in repos.flows.checks(day, "stock")] == ["c1"]
    assert V._exit_code([rep]) == 1


def test_main_은_JSON_건수만(m) -> None:
    day = m.trading_days[-2]
    out = io.StringIO()
    rc = V.main(
        ["--date", day.isoformat(), "--days", "3"], repos=_load(m), now=NOW, out=out,
        load=_no_engine,
    )  # fmt: skip
    assert rc == 0
    text = out.getvalue()
    data = json.loads(text)
    assert [r["date"] for r in data["reports"]] == [d.isoformat() for d in m.trading_days[-4:-1]]
    assert "Q0000" not in text  # 종목코드·값을 내지 않는다


def test_main_은_KRX_전_KIS_마감일도_본다(m) -> None:
    out = io.StringIO()
    rc = V.main(["--date", m.last_day.isoformat()], repos=_load(m), now=NOW, out=out,
                load=_no_engine)  # fmt: skip
    assert rc == 0
    assert json.loads(out.getvalue())["reports"][0]["stock"]["c1"]["checked"] > 30


def test_main_인자_검증() -> None:
    with pytest.raises(SystemExit):
        V.main(["--date", "2026-09-01", "--days", "0"], repos=memory_repos(), now=NOW)


def test_DB_오류는_가린_문구로_종료_2() -> None:
    from kbj.store.db import StoreError

    class Broken:
        @property
        def market(self):
            raise StoreError("DB 접속 실패: OperationalError")

        flows = etf = market

    out = io.StringIO()
    rc = V.main(["--date", "2026-09-01"], repos=Broken(), now=NOW, out=out)  # type: ignore[arg-type]
    assert rc == 2
    assert json.loads(out.getvalue())["error"] == "store"


def test_잔차_칸() -> None:
    assert [V.residual_bucket(x) for x in (0, -1, 2, 1_000_000, 1e8, 1e8 + 1)] == [
        "le_1won", "le_1won", "le_1m", "le_1m", "le_100m", "gt_100m",
    ]  # fmt: skip
