"""공개 매크로·신용 칩 파일(kbj/services/public_export/{macro_json,credit_json}.py —
docs/p5_design.md §7).

공개 행만·로그인 탭·예상치 없음·파일 모양 = API 모델·파생(spread·yoy)이 엔진과 같은 값·빈 표면 파일
0.
합성 세계(p5_world)를 메모리 저장소에 올리고 같은 뜻의 메모리 읽기(`memory_pub_reader`)로 만든다."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from kbj.engines.macro.derive import spread
from kbj.engines.macro.ribbon import CREDIT_AA, CREDIT_KTB, credit_balance_chip, credit_spread_chip
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.macro import EconCalendar, MacroSeriesSet, MacroTabs
from kbj.services.public_export.credit_json import NAME, build_credit, expected_day
from kbj.services.public_export.macro_json import build_macro
from kbj.services.public_export.manifest import LOGIN_SOURCES, login_source_paths
from kbj.store.repos import memory_repos
from tests.fixtures.synthetic.p5_world import CLOCK, build_world
from tests.unit.api.web_fixtures_p5.macro import load_macro, memory_pub_reader


@pytest.fixture(scope="module")
def built() -> tuple[dict[str, dict[str, object]], object]:
    repos = memory_repos()
    w = build_world()
    load_macro(repos, w)
    read = memory_pub_reader(repos)
    files = {f.name: json.loads(f.render(CLOCK)) for f in build_macro(read, now=CLOCK).files}
    files |= {f.name: json.loads(f.render(CLOCK)) for f in build_credit(read, now=CLOCK).files}
    return files, w


def test_public_files_are_public_only_and_match_api_models(built: tuple[dict, object]) -> None:  # type: ignore[type-arg]
    files, _ = built
    assert set(files) == {
        "macro/tabs.json",
        "macro/rates.json",
        "macro/prices.json",
        "macro/us.json",
        "macro/calendar.json",
        NAME,
    }
    Envelope[MacroTabs].model_validate(files["macro/tabs.json"])
    for tab in ("rates", "prices", "us"):
        Envelope[MacroSeriesSet].model_validate(files[f"macro/{tab}.json"])
    Envelope[EconCalendar].model_validate(files["macro/calendar.json"])
    tabs = files["macro/tabs.json"]["data"]["tabs"]  # type: ignore[index]
    assert "market" not in {t["id"] for t in tabs} and all(t["public"] for t in tabs)
    for name, body in files.items():
        assert login_source_paths(body) == [], name  # 로그인 출처 이름이 없다
        text = json.dumps(body, ensure_ascii=False)
        assert "YAHOO" not in text and "VIXCLS" not in text and "901Y056" not in text
        assert "prv_" not in text
    rows = files["macro/calendar.json"]["data"]["rows"]  # type: ignore[index]
    assert rows and all(r["forecast"] is None and r["source"] != "ff" for r in rows)
    assert not LOGIN_SOURCES.search(files["macro/calendar.json"]["source"])  # type: ignore[arg-type]


def test_spread_and_latest_match_the_engine(built: tuple[dict, object]) -> None:  # type: ignore[type-arg]
    files, w = built
    series = {s["series_id"]: s for s in files["macro/rates.json"]["data"]["series"]}  # type: ignore[index]
    pub = series["DERIVED:credit_aa"]
    eng = {p.period: p.value for p in spread(w.series(CREDIT_AA), w.series(CREDIT_KTB))}  # type: ignore[attr-defined]
    for p in pub["points"][-5:]:
        assert p["value"] == pytest.approx(eng[p["period"]])
    assert series["ECOS:817Y002:ktb10y"]["na_reason"] == "확인 전(#43)"  # 실측 전 — 값 없음
    us = {s["series_id"]: s for s in files["macro/us.json"]["data"]["series"]}  # type: ignore[index]
    dgs2 = us["FRED:DGS2"]
    assert any(p["value"] is None for p in dgs2["points"])  # 결측은 null(0 아님)
    assert dgs2["latest"]["value"] is not None and dgs2["change"] is not None
    prices = {s["series_id"]: s for s in files["macro/prices.json"]["data"]["series"]}  # type: ignore[index]
    assert prices["DERIVED:cpi_yoy"]["points"] and prices["DERIVED:cpi_yoy"]["latest"] is not None


def test_credit_chips_match_the_engine_chips(built: tuple[dict, object]) -> None:  # type: ignore[type-arg]
    files, w = built
    chips = {c["key"]: c for c in files[NAME]["data"]["chips"]}  # type: ignore[index]
    assert set(chips) == {"credit_spread", "credit_balance", "investor_deposit"}
    eng = credit_spread_chip(w.series(CREDIT_AA), w.series(CREDIT_KTB))  # type: ignore[attr-defined]
    assert eng is not None and eng.value is not None
    assert chips["credit_spread"]["value"]["value"] == pytest.approx(eng.value.value)
    assert chips["credit_spread"]["value"]["quality"] == eng.value.quality.value == "stale"
    from kbj.core.calendar import TradingCalendar

    exp = expected_day(CLOCK, TradingCalendar.default())
    bal = credit_balance_chip(list(w.market_stats), expected=exp)  # type: ignore[attr-defined]
    assert bal is not None and bal.value is not None
    assert chips["credit_balance"]["value"]["value"] == pytest.approx(bal.value.value)
    assert chips["credit_balance"]["value"]["quality"] == bal.value.quality.value
    assert all(c["tier"] == "public" and c["phase_pending"] is None for c in chips.values())


def test_empty_tables_make_no_files_and_no_reasons() -> None:
    read = memory_pub_reader(memory_repos())
    assert build_macro(read, now=CLOCK).files == () and build_macro(read, now=CLOCK).skipped == ()
    assert build_credit(read, now=CLOCK).files == ()


def test_calendar_window_follows_config(built: tuple[dict, object]) -> None:  # type: ignore[type-arg]
    files, _ = built
    cal = files["macro/calendar.json"]["data"]  # type: ignore[index]
    assert cal["start"] == (CLOCK.date() - timedelta(days=7)).isoformat()
    assert cal["end"] == (CLOCK.date() + timedelta(days=30)).isoformat()
