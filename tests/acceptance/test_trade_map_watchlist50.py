"""P6 완료 기준 — "관심 종목 50개의 매핑과 겹쳐 보기 차트가 나온다"
(docs/PLAN.md §8 P6, p6_design §0.3).

합성 관심목록 50종목 세계(`p6_world` — 수출주·내수주·지역 매핑·HS 접두 2~10자리·재무 미제출·
신규상장·매핑 접두가 세계에 없는 장)에 P6 **실제 처리기**(`trade.map_load` →
`trade.customs_monthly`(가짜 관세청)
→ `trade.derive` → `trade.stock_index`)를 돌린 뒤(`p6_pipeline.py`) `/api/trade/stock/{code}` × 50:

① 500 = 0 ② 매핑 50/50(전부 200 — 공개 매핑 + VM local 매핑) ③ 상태 = `p6_expect.yaml status` 행렬
(no_trade·no_revenue·short_overlap — 그 밖은 분기 매출 8개 미만이면 short_overlap, 아니면 ok)
④ `NaN`·`Infinity` 0 ⑤ 공개 내보내기 `trade/stock/<code>.json` 은 `origin: public` 종목만 — local
(관심종목 전용) 종목·관심종목 흔적 0.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from kbj.services.public_export.manifest import trade_problems
from kbj.services.public_export.trade_stock_json import Q_MAP, build_trade_stock
from tests.acceptance.p6_pipeline import P6Pipeline, built
from tests.fixtures.synthetic.p6_world import CLOCK
from tests.unit.api.web_fixtures_p6.map import memory_stock_reader

ROOT = Path(__file__).resolve().parents[2]
EXPECT: dict[str, Any] = yaml.safe_load(
    (ROOT / "tests/fixtures/synthetic/p6_expect.yaml").read_text("utf-8")
)
MIN_QUARTERS = 8  # 겹친 분기 하한(metrics §28 — 상관을 낼 최소)


@pytest.fixture(scope="module")
def pipe() -> P6Pipeline:
    return built()


@pytest.fixture(scope="module")
def client(pipe: P6Pipeline) -> TestClient:
    return pipe.api().authed()


@pytest.fixture(scope="module")
def bodies(pipe: P6Pipeline, client: TestClient) -> dict[str, tuple[int, str]]:
    out: dict[str, tuple[int, str]] = {}
    for code in pipe.world.watchlist:
        r = client.get(f"/api/trade/stock/{code}")
        out[code] = (r.status_code, r.text)
    return out


def expected_status(pipe: P6Pipeline, code: str) -> str:
    special = {v: k for k, v in EXPECT["status"].items()}
    if code in special:
        return special[code]
    return "short_overlap" if len(pipe.world.revenue(code)) < MIN_QUARTERS else "ok"


def test_처리기가_모두_ok(pipe: P6Pipeline) -> None:
    assert {k: r.status for k, r in pipe.results.items()} == {
        "trade.map_load": "ok",
        "trade.customs_monthly": "ok",
        "trade.derive": "ok",
        "trade.stock_index": "ok",
    }
    # 관세청 월간은 가짜 포털에서 실제 어댑터로 받았다(창 앞 이력에는 2026-10 이 없다)
    assert pipe.repos.trade.last_ym("pub_trade.item_monthly") == "2026-10"


def test_관심종목_50_매핑_50_오류_0(pipe: P6Pipeline, bodies: dict[str, tuple[int, str]]) -> None:
    assert len(pipe.world.watchlist) == 50
    assert not [c for c, (st, _) in bodies.items() if st >= 500]
    assert [c for c, (st, _) in bodies.items() if st != 200] == []
    mapped = {r.code for r in pipe.repos.trade.stock_map()}
    assert set(pipe.world.watchlist) <= mapped


def test_상태_행렬(pipe: P6Pipeline, bodies: dict[str, tuple[int, str]]) -> None:
    got = {c: json.loads(t)["data"]["status"] for c, (_, t) in bodies.items()}
    want = {c: expected_status(pipe, c) for c in pipe.world.watchlist}
    assert got == want
    # 행렬에 네 상태가 다 있다(겹쳐 보기 ok·매출 없음·겹침 부족·관세청 자료 없음)
    assert set(got.values()) == {"ok", "no_revenue", "short_overlap", "no_trade"}


def test_봉투와_NaN_Infinity_0(bodies: dict[str, tuple[int, str]]) -> None:
    for code, (_, text) in bodies.items():
        assert "NaN" not in text and "Infinity" not in text, code
        body = json.loads(text)
        assert body["source"] and body["as_of"] and body["quality"], code
        assert body["data"]["code"] == code


def test_공개_내보내기는_public_매핑_종목만(pipe: P6Pipeline) -> None:
    out = build_trade_stock(memory_stock_reader(pipe.repos), now=CLOCK)
    public = set(pipe.world.map_codes("public"))
    local_only = {r.code for r in pipe.repos.trade.stock_map(origin="local")} - public
    stock_files = {f.name for f in out.files if f.name.startswith("trade/stock/")}
    assert stock_files == {f"trade/stock/{c}.json" for c in public}
    text = "\n".join(json.dumps(f.data, ensure_ascii=False) for f in out.files)
    assert not [c for c in local_only if c in text]  # 관심종목 전용 매핑 흔적 0
    assert '"local"' not in text
    for f in out.files:
        assert trade_problems(f.name, f.data) == [], f.name
    # 실제 SQL 도 public 줄만 읽는다(메모리 읽기와 같은 뜻)
    assert "origin = 'public'" in Q_MAP.sql
