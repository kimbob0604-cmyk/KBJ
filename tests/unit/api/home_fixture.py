"""첫 화면 묶음 '모든 카드에 값' 픽스처 — `web/test/fixtures/api/home_full.json`(합성 — 화면
그리기 시험용).

기본 합성 세계(지수·시장 폭·급증·급등락·외국인+기관·신고가·ETF) + 테마 마스터·보드 종목
하루(`theme_world`)에, 기본 세계에 원천이 없는 카드는 **그 원천이 있는 다른 합성 세계의 읽기
문맥으로 같은 읽기 함수**를 부르게 바꿔 끼운다:
- 공시·잠정실적 = P4 세계(`api_world_p4`)
- 프로그램(일별·장중 누적)·1분 투자자·당일 지수 10분 점 = P5 세계(`web_fixtures_p5.program`)
- 수출 속보 = P6 세계(`web_fixtures_p6.flash`)
- GEX = P7 세계(`web_fixtures_p7.gex`), 진단 = P7 진단 세계(`web_fixtures_p7.diag`). 선물 전일
  정산가는 GEX 세계 저장소에 합성 KRX 정산가 한 줄(`SETTLE` — 근월물)을 넣고 같은 함수로 읽는다

카드 값·모양은 실제 읽기 함수 그대로지만 **카드마다 기준일이 다르다**(세계마다 날짜가 다르다 —
기본 세계 2026-09, P4·P5 2026-11, P6·P7 2026-12). 응답 시각은 모든 세계보다 뒤(`CLOCK`)로 둔다
— 카드 기준 시각이 응답 시각으로 당겨지지 않게. 실데이터는 쓰지 않는다(전부 고정 시드 합성).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final

import pytest

from kbj.core.time import KST
from kbj.services.api.readers import home as home_reader
from kbj.services.api.readers._common import ReadContext
from tests.unit.api.api_world import World, build_world
from tests.unit.api.theme_world import seed_board, seed_themes

__all__ = ["CLOCK", "render_home_full"]

CLOCK: Final = datetime(2026, 12, 11, 12, 0, tzinfo=KST)  # 다른 세계(최대 12-11 11:30)보다 뒤
SETTLE: Final = Decimal("349.00")  # 합성 전일 정산가(pt — GEX 세계 근월물)


def _ctx(client: Any) -> ReadContext:
    """세계 앱의 읽기 문맥(그 세계의 저장소·시계·설정)."""
    read: ReadContext = client.app.state.kbj.read
    return read


def _base() -> World:
    w = build_world()
    w.clock.set(CLOCK)
    seed_themes(w.repos, now=CLOCK)
    day = w.repos.board.last_day(CLOCK.date())
    if day is None:
        raise RuntimeError("기본 세계에 보드가 없다")
    seed_board(w.repos, day)
    return w


def _seed_settle(ctx: ReadContext, trade_date: date) -> None:
    """GEX 세계 저장소에 그날 근월물의 전일 정산가 한 줄(합성)."""
    from kbj.engines.gex.daily import monthly_last_trade

    y, m = trade_date.year, trade_date.month
    code = ""
    for _ in range(24):
        code = f"{y:04d}{m:02d}"
        last = monthly_last_trade(code, {}, ctx.cal)
        if last is not None and last >= trade_date:
            break
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    repo: Any = ctx.repos.gex
    repo.krx_futures.append({
        "family": "kospi200", "session": "day", "expiry": code, "setl_prc": SETTLE,
        "trade_date": ctx.cal.prev_trading_day(trade_date),
    })  # fmt: skip


def render_home_full() -> str:
    """합성 세계 여럿을 이어 붙인 `/api/home` 응답 글자(결정적 — 고정 시드·가짜 시계)."""
    from tests.unit.api.api_world_p4 import shared
    from tests.unit.api.web_fixtures_p5.program import program_api_world
    from tests.unit.api.web_fixtures_p6.flash import flash_api_world
    from tests.unit.api.web_fixtures_p7.diag import diag_api_world
    from tests.unit.api.web_fixtures_p7.gex import gex_api_world

    p4 = shared()
    p4_ctx = _ctx(p4.client(data_versions=None))
    p5_ctx = _ctx(program_api_world().client())
    p6_ctx = _ctx(flash_api_world().client())
    gex_ctx = _ctx(gex_api_world().client())
    diag_ctx = _ctx(diag_api_world().client())
    filings_day: date = p4_ctx.today()
    r = home_reader
    feed, earn, prog, minute = r.filings_feed, r.filings_earnings, r.program_daily, r.market_minute
    prog_live, settle = r.program_intraday, r.futures_prev_settle
    flash, levels, brief, diag = r.export_flash, r.gex_levels, r.gex_briefing, r.diag_summary
    _seed_settle(gex_ctx, levels(gex_ctx, scope="all").data.trade_date)
    w = _base()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(r, "_filings_day", lambda _c: filings_day)
        mp.setattr(r, "filings_feed", lambda _c, **kw: feed(p4_ctx, **kw))
        mp.setattr(r, "filings_earnings", lambda _c, **kw: earn(p4_ctx, **kw))
        mp.setattr(r, "program_daily", lambda _c, **kw: prog(p5_ctx, **kw))
        mp.setattr(r, "program_intraday", lambda _c, **kw: prog_live(p5_ctx, **kw))
        mp.setattr(r, "futures_prev_settle", lambda _c, d: settle(gex_ctx, d))
        mp.setattr(r, "market_minute", lambda _c, **kw: minute(p5_ctx, **kw))
        mp.setattr(r, "export_flash", lambda _c, _now: flash(p6_ctx, p6_ctx.now_kst()))
        mp.setattr(r, "gex_levels", lambda _c, **kw: levels(gex_ctx, **kw))
        mp.setattr(r, "gex_briefing", lambda _c, **kw: brief(gex_ctx, **kw))
        mp.setattr(r, "diag_summary", lambda _c, **kw: diag(diag_ctx, **kw))
        c = w.client()
        w.login(c)
        resp = c.get("/api/home")
    if resp.status_code != 200:
        raise RuntimeError(f"/api/home: HTTP {resp.status_code}")
    body = resp.json()
    bad = {k: v["status"] for k, v in body["data"]["cards"].items() if v["status"] != "ok"}
    if bad:
        raise RuntimeError(f"값이 없는 카드: {bad}")
    return json.dumps(body, ensure_ascii=False, indent=2) + "\n"
