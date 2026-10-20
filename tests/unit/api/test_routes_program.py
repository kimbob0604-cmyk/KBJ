"""`/api/flows/market-minute`·`/api/program/{daily,top}` 라우트 — 합성 세계(P5 F2 §3.5·§4).

리더 본문(F2): 분 축 401칸(빈 분 None)·원 단위(백만원 × 1e6)·7카드(사모 제외)·거래소 잠금·
겹칠 지수(장중 10분 → 분 축, 없으면 메모)·짧은 사용자 메모(작업 이름·실측 번호 없음)·
상태(확인 전·장 시작 전·휴장일)·범위 꼬리표(장중·마감)·검산 ④ 실패 일 제외·빈 저장소 404·
모르는 탭 404.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time
from typing import Any

import pytest

from kbj.core.quality import Quality
from kbj.core.rows import UniverseRow
from kbj.core.time import KST
from kbj.engines.flows.program import CHECK4_TOL
from tests.fixtures.synthetic.p5_world import TODAY
from tests.unit.api.web_fixtures_p5.program import (
    BEFORE_OPEN,
    DROP_KOSPI,
    HOLIDAY,
    INTRADAY,
    SWEPT,
    ProgramApiWorld,
    program_api_world,
)


def get(api: ProgramApiWorld, path: str) -> Any:
    r = api.authed().get(path)
    assert r.status_code == 200, r.text
    return r.json()


def test_minute_line_in_won_with_gap_and_cards() -> None:
    api = program_api_world(at=INTRADAY)
    body = get(api, "/api/flows/market-minute?market=kospi")
    d = body["data"]
    assert isinstance(d, dict)
    assert d["status"] == "ok" and d["venue"] == "KRX" and d["venue_locked"] is True
    assert d["provisional"] is True and d["refresh_s"] == 60
    axis = d["axis"]
    assert len(axis) == 401 and axis[0] == "09:00" and axis[-1] == "15:40"
    assert api.world is not None
    raw = {m.ts: m for m in api.world.minutes("kospi") if m.investor == "frgn"}
    frgn = next(ln for ln in d["lines"] if ln["investor"] == "frgn")
    i1000 = axis.index("10:00")
    assert frgn["values"][i1000 : i1000 + 5] == [None] * len(DROP_KOSPI)  # 빈 분은 잇지 않는다
    t = datetime.combine(TODAY, time(9, 30), tzinfo=KST)
    assert frgn["values"][axis.index("09:30")] == (raw[t].net_value or 0) * 1_000_000  # 원
    assert all(v is None for v in frgn["values"][axis.index("13:01") :])  # 아직 오지 않은 분
    assert [c["investor"] for c in d["cards"]] == [
        "prsn",
        "frgn",
        "orgn",
        "scrt",
        "ivtr",
        "fund",
        "etc_corp",
    ]
    assert d["collected_at"].startswith("2026-11-17T13:00")
    assert {ln["investor"] for ln in d["lines"] if ln["default_on"]} == {"prsn", "frgn", "orgn"}
    notes = " ".join(body["notes"])
    assert "못 받은 5분" in notes and "KRX" in notes and body["source"] == "KIS"
    assert "flows." not in notes and "#" not in notes  # 작업 이름·실측 번호 없음


def test_minute_index_overlay_on_ten_minute_slots() -> None:
    body = get(program_api_world(at=INTRADAY), "/api/flows/market-minute?market=kospi")
    d = body["data"]
    idx = d["index"]
    assert idx["code"] == "0001" and idx["label"] == "코스피"
    axis = d["axis"]
    assert len(idx["values"]) == len(axis)
    have = [axis[i] for i, v in enumerate(idx["values"]) if v is not None]
    assert have[0] == "09:00" and have[-1] == "13:00" and "09:10" in have and "09:05" not in have
    assert idx["last"] == idx["values"][axis.index("13:00")] and idx["chg_pct"] is not None
    fut = get(program_api_world(at=INTRADAY), "/api/flows/market-minute?market=k200_fut")
    assert fut["data"]["index"]["code"] == "2001"
    assert not any("지수" in n for n in fut["notes"])


def test_minute_without_index_values_says_so_in_one_note() -> None:
    pre = get(program_api_world(at=BEFORE_OPEN), "/api/flows/market-minute?market=k200_fut")
    assert pre["data"]["index"] is None  # 직전 거래일 지수 값이 없다 — 지어내지 않는다
    assert sum("지수 선 없음" in n for n in pre["notes"]) == 1


def test_minute_other_venue_still_krx_locked_with_note() -> None:
    body = get(program_api_world(at=INTRADAY), "/api/flows/market-minute?market=k200_fut&venue=NXT")
    d = body["data"]
    assert isinstance(d, dict) and d["venue"] == "KRX" and d["venue_locked"] is True
    assert any("NXT" in n for n in body["notes"])


def test_minute_states_not_confirmed_before_open_holiday() -> None:
    off = get(program_api_world(at=INTRADAY), "/api/flows/market-minute?market=kosdaq")
    assert off["data"]["status"] == "not_confirmed"
    assert off["data"]["lines"] == [] and "확인 전" in off["notes"][0]
    pre = get(program_api_world(at=BEFORE_OPEN), "/api/flows/market-minute?market=k200_fut")
    assert pre["data"]["status"] == "before_open" and pre["data"]["provisional"] is False
    assert pre["data"]["trade_date"] == "2026-11-16"
    hol = get(program_api_world(at=HOLIDAY), "/api/flows/market-minute?market=k200_fut")
    assert hol["data"]["status"] == "holiday" and hol["data"]["trade_date"] == "2026-11-20"


def test_minute_unknown_tab_and_empty_store() -> None:
    api = program_api_world(at=INTRADAY)
    r = api.authed().get("/api/flows/market-minute?market=nope")
    assert r.status_code == 404 and r.json()["code"] == "unknown_code"
    empty = program_api_world(empty=True, at=INTRADAY)
    r = empty.authed().get("/api/flows/market-minute?market=k200_fut")
    assert r.status_code == 404 and r.json()["code"] == "no_data"
    assert "flows.market_minute" in r.json()["message"]  # 404 계약(작업·시각)


def test_program_daily_bars_sixty_days() -> None:
    body = get(program_api_world(), "/api/program/daily?market=K&days=60")
    d = body["data"]
    assert isinstance(d, dict) and d["days"] == 60 and len(d["rows"]) == 60
    last = d["rows"][-1]
    assert last["date"] == "2026-11-17"
    assert last["net"] == last["arbt_net"] + last["nabt_net"] == last["entm_net"] + last["onsl_net"]
    short = get(program_api_world(), "/api/program/daily?market=Q&days=5")["data"]
    assert short["days"] == 5


def test_program_daily_marks_check4_failures() -> None:
    api = program_api_world()
    assert api.world is not None
    bad = [
        replace(r, net_value=(r.net_value or 0) + 2 * CHECK4_TOL["net_value"])
        for r in api.world.program_days
        if r.trade_date == TODAY and r.market == "K" and (r.kind, r.side) == ("whol", "smtn")
    ]
    api.repos.program.put_market_days(bad, loaded_by="test", received_at=SWEPT)
    body = get(api, "/api/program/daily?market=K&days=60")
    last = body["data"]["rows"][-1]
    assert last["quality"] == Quality.INVALID.value and last["net"] is None
    assert any("검산 ④" in n for n in body["notes"])


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_program_top_intraday_says_its_scope(side: str) -> None:
    body = get(
        program_api_world(at=INTRADAY), f"/api/program/top?market=K&side={side}&scope=intraday"
    )
    d = body["data"]
    assert isinstance(d, dict)
    assert d["scope_label"].startswith("거래대금 상위") and "관심종목" in d["scope_label"]
    assert d["slot"].startswith("2026-11-17T12:55")
    nets = [r["net_value"] for r in d["rows"]]
    assert nets == sorted(nets, reverse=(side == "buy"))
    assert all((n > 0) if side == "buy" else (n < 0) for n in nets)
    assert [r["rank"] for r in d["rows"]] == list(range(1, len(nets) + 1))
    assert all(r["name"] for r in d["rows"])


def test_program_top_close_full_and_partial_sweep() -> None:
    api = program_api_world(at=SWEPT)
    body = get(api, "/api/program/top?market=K&side=buy&scope=close")
    d = body["data"]
    assert isinstance(d, dict) and d["scope_label"] == "전 종목(마감 확정)" and d["slot"] is None
    assert api.world is not None
    want = [c for c in api.world.expect_program_top(20) if c in {r["code"] for r in d["rows"]}]
    assert [r["code"] for r in d["rows"]] == want
    # 훑기가 덜 끝난 날 — 꼬리표가 진행 수를 말하고 quality 는 추정
    partial = program_api_world(at=SWEPT)
    assert partial.world is not None
    partial.repos.market.upsert_universe(
        [*partial.repos.market.universe(TODAY), *_extra_universe()],
        loaded_by="t",
        received_at=SWEPT,
    )
    body2 = get(partial, "/api/program/top?market=K&side=buy&scope=close")
    assert "훑은 종목" in body2["data"]["scope_label"] and body2["quality"] == "estimated"


def _extra_universe() -> list[UniverseRow]:
    return [UniverseRow("990001", TODAY, "합성추가", "KOSPI", "common", None, "krx", Quality.OK)]


def test_program_empty_store_404_names_jobs() -> None:
    api = program_api_world(empty=True)
    c = api.authed()
    r = c.get("/api/program/daily?market=K")
    assert r.status_code == 404 and "program.daily" in r.json()["message"]
    r = c.get("/api/program/top?market=K&scope=close")
    assert r.status_code == 404 and "program.stock_sweep" in r.json()["message"]
