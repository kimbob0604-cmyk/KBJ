"""매핑 해석·검증(metrics §28 — 묶음 MAP): 행 펴기·판 이름·파일 사이 대조·DB 대조·local 우선 병합.
손으로 만든 작은 매핑(합성 — 실데이터 아님)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from kbj.config.trade import (
    TradeMap,
    parse_hs_chapters,
    parse_trade_map,
    parse_trade_regions,
)
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.core.trade_rows import StockMapRow
from kbj.engines.trade.mapping import (
    MAP_SOURCE,
    cross_check,
    db_check,
    map_rows,
    map_version,
    resolve,
)

T = datetime(2026, 12, 11, 7, 35, tzinfo=KST)
EV = [{"kind": "synthetic", "note": "합성 근거"}]


def _map(*stocks: dict[str, Any]) -> TradeMap:
    return parse_trade_map({"version": 1, "stocks": list(stocks)})


def _stock(code: str, **kw: Any) -> dict[str, Any]:
    return {"code": code, "name": f"가상{code}", "reviewed": False, "evidence": EV, **kw}


CH = parse_hs_chapters({"version": 1, "chapters": {"85": "전기기기", "33": "화장품"}})
RG = parse_trade_regions(
    {"version": 1, "regions": [{"id": "KR-41-a01", "sido_cd": "41", "sgg_nm": "가상시"}]}
)


def test_행_펴기_품목·지역() -> None:
    tm = _map(
        _stock(
            "990001",
            items=[{"hs": "854232", "flow": "exp", "weight": 0.5, "countries": ["US", "CN"]}],
            regions=[{"region_id": "KR-41-a01", "hs6": "330499", "weight": 0.2}],
        )
    )
    rows = map_rows(tm, origin="public", version="v1-x", as_of=T)
    assert [(r.hs, r.region_id) for r in rows] == [("330499", "KR-41-a01"), ("854232", "")]
    item = rows[1]
    assert item.countries == ("US", "CN") and item.flow == "exp" and item.weight == 0.5
    assert rows[0].flow == "exp" and rows[0].countries == ()
    assert all(r.source == MAP_SOURCE and r.quality is Quality.OK for r in rows)
    assert rows[0].evidence == ({"kind": "synthetic", "note": "합성 근거"},)


def test_판_이름은_내용만으로_정해진다() -> None:
    a = _map(_stock("990001", items=[{"hs": "85", "flow": "exp", "weight": 0.1}]))
    b = _map(_stock("990001", items=[{"hs": "85", "flow": "exp", "weight": 0.1}]))
    c = _map(_stock("990001", items=[{"hs": "85", "flow": "exp", "weight": 0.2}]))
    assert map_version(a) == map_version(b) != map_version(c)
    assert map_version(a).startswith("v1-")


def test_파일_사이_대조() -> None:
    tm = _map(
        _stock(
            "990001",
            items=[
                {"hs": "8542", "flow": "exp", "weight": 0.1},
                {"hs": "27", "flow": "exp", "weight": 0.1},
            ],
            regions=[{"region_id": "KR-41-zzz", "hs6": "330499", "weight": 0.1}],
        )
    )
    probs = cross_check(tm, chapters=CH, regions=RG)
    assert [(p.kind, p.detail) for p in probs] == [
        ("chapter_missing", "HS 27 의 장 27"),
        ("region_missing", "KR-41-zzz"),
    ]
    ok = _map(_stock("990002", items=[{"hs": "85", "flow": "exp", "weight": 0.1}]))
    assert cross_check(ok, chapters=CH, regions=RG) == []


def _row(code: str, hs: str, origin: str = "public", region: str = "") -> StockMapRow:
    return StockMapRow(
        code=code, hs=hs, flow="exp", region_id=region, name="가상", weight=0.1,
        origin=origin, reviewed=False, map_version="v", source=MAP_SOURCE, as_of=T,  # pyright: ignore[reportArgumentType]
        quality=Quality.OK, evidence=({"kind": "synthetic", "note": "x"},),
    )  # fmt: skip


def test_DB_대조_종목·HS_접두() -> None:
    rows = [_row("990001", "85"), _row("990001", "854232"), _row("990002", "9901")]
    probs = db_check(rows, listed={"990001"}, hs_codes={"8542320000", "8501"})
    assert [p.line() for p in probs] == [
        "990002: code_missing — 상장사 목록에 없음",
        "990002: hs_missing — HS 9901",
    ]


def test_local_우선_경고() -> None:
    rows = [
        _row("990001", "85"),
        _row("990001", "8542", origin="local"),
        _row("990002", "33"),
    ]
    res = resolve(rows)
    assert res.codes() == ["990001", "990002"]
    assert [r.hs for r in res.lines["990001"]] == ["8542"]
    assert [r.origin for r in res.lines["990001"]] == ["local"]
    assert res.warnings == ("990001: 공개·local 매핑이 둘 다 있다 — local 우선",)
