"""일지·매매 쓰기 모듈 `kbj.services.personal.journal`(묶음 JNL — D-P8-15·16·19): 일지 만들기·
고치기·지우기, 매매 넣기·지우기(음수 보유 거절), CSV 가져오기, SD 이관. 메모리 저장소·합성 값."""

from __future__ import annotations

import io
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import UniverseRow
from kbj.services import personal
from kbj.services.personal.journal import (
    AnalysisNotFound,
    NegativeHolding,
    TradeNotFound,
    add_trade,
    cli,
    create_analysis,
    delete_analysis,
    delete_trade,
    import_sd,
    plan_csv,
    update_analysis,
)
from kbj.store.repos.memory import MemoryRepos
from tests.fixtures.synthetic.sqlite.sd_p8_gen import build_sd_p8

NOW = datetime(2026, 10, 13, 7, 0, tzinfo=UTC)
A = "990001"
B = "990002"


def _repos() -> MemoryRepos:
    r = MemoryRepos()
    r.market.upsert_universe(
        [
            UniverseRow(
                A, date(2026, 10, 13), "합성전자", "KOSPI", "common", None, "KRX", Quality.OK
            ),
            UniverseRow(
                B, date(2026, 10, 13), "합성화학", "KOSDAQ", "common", None, "KRX", Quality.OK
            ),
        ],
        loaded_by="test",
    )
    return r


def _analysis(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "code": A,
        "analyzed_on": date(2026, 10, 13),
        "price": 12345.67,
        "mktcap": 10**12,
        "eps": {"bear": 1000.5, "base": None, "bull": 1500},
        "per": {"bear": 8.0},
        "tp": {"base": 15000},
        "tags": ["반도체"],
        "value_sources": {"price": "kbj"},
    }
    return {**base, **kw}


def _trade(side: str, qty: int, day: int = 1, price: float = 100.0, **kw: Any) -> dict[str, Any]:
    return {"code": A, "side": side, "trade_date": date(2026, 9, day), "price": price, "qty": qty,
            **kw}  # fmt: skip


def test_analysis_create_update_delete() -> None:
    r = _repos()
    a = create_analysis(r, _analysis(), source="api", now=NOW)
    assert a.id == 1 and a.source == "api"
    assert a.price == Decimal("12345.67")  # float → 문자열 → Decimal(끝자리 흔들림 없음)
    assert a.eps == {"bear": Decimal("1000.5"), "base": None, "bull": Decimal("1500")}
    assert a.per == {"bear": 8.0, "base": None, "bull": None}
    later = NOW.replace(hour=8)
    b = update_analysis(r, 1, _analysis(memo="고침"), now=later)
    assert b.memo == "고침" and b.updated_at == later and b.source == "api"
    with pytest.raises(AnalysisNotFound):
        update_analysis(r, 99, _analysis(), now=NOW)
    assert delete_analysis(r, 1) == 1
    with pytest.raises(AnalysisNotFound):
        delete_analysis(r, 1)


def test_analysis_bad_field_is_value_error() -> None:
    with pytest.raises(ValueError):
        create_analysis(_repos(), _analysis(code="bad"), source="api", now=NOW)
    with pytest.raises(ValueError):
        create_analysis(_repos(), _analysis(eps={"bear": "x"}), source="api", now=NOW)


def test_trade_add_names_and_negative_holding() -> None:
    r = _repos()
    t = add_trade(r, _trade("buy", 10), source="api", now=NOW)
    assert t.id == 1 and t.name_at_trade is None  # 9/1 유니버스 없음 — 지어내지 않는다
    t2 = add_trade(r, _trade("buy", 1, day=13, fee=15), source="api", now=NOW)
    assert t2.fee == Decimal("15")
    with pytest.raises(NegativeHolding):
        add_trade(r, _trade("sell", 12, day=20), source="api", now=NOW)
    assert len(r.journal.trades()) == 2  # 거절은 쓰지 않는다
    add_trade(r, _trade("sell", 11, day=20), source="api", now=NOW)
    # 매수를 지우면 뒤 매도가 보유보다 많아진다 → 거절
    with pytest.raises(NegativeHolding):
        delete_trade(r, 1)
    assert delete_trade(r, 3) == 3
    assert delete_trade(r, 1) == 1
    with pytest.raises(TradeNotFound):
        delete_trade(r, 1)


def test_trade_name_from_universe() -> None:
    r = _repos()
    t = add_trade(r, {**_trade("buy", 1), "trade_date": date(2026, 10, 13)}, source="api", now=NOW)
    assert t.name_at_trade == "합성전자"


CSV = """date,code,side,price,qty,fee,tax,strategy,memo
2026-09-01,990001,buy,"10,000",10,15,0,스윙,
2026-09-02,990001,매도,11000,4,10,22,,

2026-09-03,1,buy,100,1,,,,
2026-09-04,990002,sell,100,1,,,,
2026-09-05,990002,hold,100,1,,,,
2026-09-06,990002,buy,abc,1,,,,
2026-13-01,990002,buy,1,1,,,,
"""


def test_plan_csv_rejects_by_line() -> None:
    plan = plan_csv(CSV, now=NOW, existing=[])
    assert [(n, r.code, r.side) for n, r in plan.rows] == [
        (2, A, "buy"), (3, A, "sell"), (5, "000001", "buy"),
    ]  # fmt: skip
    assert [(x.line, x.reason) for x in plan.rejects] == [
        (6, "negative_holding"), (7, "bad_side"), (8, "bad_number"), (9, "bad_date"),
    ]  # fmt: skip
    assert plan.rows[0][1].price == Decimal("10000") and plan.rows[0][1].source == "import:csv"
    assert plan_csv("a,b\n1,2\n", now=NOW, existing=[]).rejects[0].reason == "bad_header"


def test_cli_csv_import_all_or_nothing(tmp_path: Path) -> None:
    r = _repos()
    p = tmp_path / "trades.csv"
    p.write_text(CSV, encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    assert cli(["trades", "import", "--csv", str(p)], r, now=lambda: NOW, out=out, err=err) == 1
    assert r.journal.trades() == []
    assert "10,000" not in out.getvalue() and "거절 6행 — negative_holding" in out.getvalue()
    out = io.StringIO()
    code = cli(["trades", "import", "--csv", str(p), "--skip-rejects"], r, now=lambda: NOW,
               out=out, err=err)  # fmt: skip
    assert code == 0
    assert len(r.journal.trades()) == 3
    assert cli(["trades", "list"], r, out=(o := io.StringIO())) == 0
    assert "990001 2건" in o.getvalue()
    missing = tmp_path / "none.csv"
    assert cli(["trades", "import", "--csv", str(missing)], r, err=io.StringIO()) == 2


def test_cli_through_package_main(tmp_path: Path) -> None:
    r = _repos()
    create_analysis(r, _analysis(), source="cli", now=NOW)
    out = io.StringIO()
    import contextlib

    with contextlib.redirect_stdout(out):
        assert personal.main(["journal", "analysis", "list"], repos=r) == 0
    assert out.getvalue().startswith("1 990001 2026-10-13 cli")


def test_import_sd_all_sources_and_idempotent(tmp_path: Path) -> None:
    paths = build_sd_p8(tmp_path)
    r = _repos()
    rep = import_sd(r, now=NOW, db=paths["db"], rules=paths["rules"], portfolio=paths["portfolio"])
    assert rep.analyses == 3 and rep.picks == 4
    # 매매: SD 3건(990001) + 포지션 기초 1건(990003 — 990001 은 매매 기록이 있어 빠짐)
    assert rep.trades == 4
    assert {(d.origin, d.reason) for d in rep.drops} >= {
        ("analysis_journal", "bad_code"),
        ("trade_journal", "not_kr"),
        ("trade_journal", "bad_side"),
        ("recommendation_history", "not_kr"),
        ("server_portfolio", "covered_by_trades"),
        ("server_portfolio", "bad_number"),
    }
    assert rep.rules == 3
    assert sorted(u.reason for u in rep.rules_unconverted) == ["not_kr", "unsupported_type"]
    heads = r.rules.rules(include_archived=True)
    assert all(not h.enabled and h.source == "import:sd" for h in heads)  # 꺼진 채
    a = r.journal.analyses()
    assert {x.source for x in a} == {"import:sd"}
    first = next(x for x in a if x.code == A)
    assert first.mktcap == Decimal(1_234_000_000)  # 1234 백만원 → 원
    assert first.tags == ("반도체", "HBM")
    # 두 번째는 일지·매매를 다시 넣지 않고, 규칙은 같은 이름이라 건너뛴다, 추천은 덮는다
    rep2 = import_sd(r, now=NOW, db=paths["db"], rules=paths["rules"], portfolio=paths["portfolio"])
    assert rep2.analyses == 0 and rep2.trades == 0 and rep2.rules == 0
    assert rep2.rules_existing == 3 and len(r.journal.picks()) == 4
    assert any("이미 이관됨" in s for s in rep2.skipped)
    lines = "\n".join(rep2.lines())
    assert "합성" not in lines  # 이름·값은 찍지 않는다


def test_cli_import_sd(tmp_path: Path) -> None:
    paths = build_sd_p8(tmp_path)
    r = _repos()
    out = io.StringIO()
    assert cli(["import-sd", "--db", str(paths["db"])], r, now=lambda: NOW, out=out) == 0
    assert "일지 3건" in out.getvalue()
    assert cli(["import-sd"], r, err=io.StringIO()) == 2
    bad = tmp_path / "kis_token.json"
    bad.write_text("{}", encoding="utf-8")
    assert cli(["import-sd", "--rules", str(bad)], r, err=(e := io.StringIO())) == 2
    assert "토큰" in e.getvalue()
