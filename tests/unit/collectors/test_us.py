"""간밤 미국 수집(kbj/services/collectors/us.py — docs/p5_design.md §1.6·§4.6, D-P5-18). 합성만.

1) 승격: ETF-Traker `board/tests/test_us_pipeline.py` 9 — 같은 단언, 대상만 kbj 로. ET 의 sqlite
   (`board/us/db.py`)·state 파일(`build.run`)·정적 HTML(`render`)·발송(`run.cmd_us_send`)은 KBJ 에서
   저장소(`prv_market.us_universe`·`daily_bar` US·`prv_board.artifact` US — 메모리 저장소로 시험)·
   산출 us_summary·us_brief(텔레그램 HTML 조각 — 아침 브리핑 ① 이 싣는다)가 맡는다. 그래서
   `TestRoundTrip`·`TestSendPath` 는 '저장소 왕복'·'산출 → 발송 조각'을 같은 단언으로 본다(페이지
   HTML 은 SPA 페이지 2 — `web/test/pages/p2_us.test.ts`).
2) 새 시험: 가짜 Nasdaq·Stooq·Yahoo 로 처리기 끝까지 — 기준일 판정(공표 전), 실패 종목 사유,
   지수 일봉, 엔진 직접 호출과 같은 수, P5 합성 세계의 심은 신고가·보류·펀드 제외.

미국장 적재·조립 검증 — 네트워크는 대역으로 대체한다. 여기서 보는 것은 '배관' 이다. 수집기가 준 것이
저장소에 그대로 들어가고, 산출이 그 저장소만 읽어 만들어지고, 기준일이 달력이 아니라 **받은
일봉**으로 정해지는가.
"""

from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from typing import Any

import pytest

from kbj.core.p5_rows import UsUniverseRow
from kbj.core.quality import Quality
from kbj.data.private.nasdaq.client import NasdaqClient
from kbj.data.private.stooq.client import StooqClient
from kbj.data.private.yahoo.client import YahooClient
from kbj.data.spec import DataKey
from kbj.engines.us import board as us_board
from kbj.engines.us import brief as BR
from kbj.engines.us import engine as E
from kbj.engines.us.config import load_us
from kbj.services.collectors import us as P
from kbj.services.collectors._p3 import NotReady
from kbj.services.scheduler.handlers import JobContext
from kbj.store.repos.memory import memory_repos
from tests.fakes.nasdaq_server import FakeNasdaq, FakeStooq, StubLimiter
from tests.fakes.yahoo_server import FakeYahoo
from tests.fixtures.synthetic.us import demo

CFG = load_us()
DAY = "2026-08-28"  # demo.make 의 마지막 봉(금 — 300 평일)
D = date(2026, 8, 28)
NOW = datetime(2026, 8, 28, 20, 20, tzinfo=UTC)  # 16:20 ET


def key(ds: str, as_of: str = DAY) -> DataKey:
    src, name = ds.split(":", 1)
    return DataKey(src, name, as_of)


def ctx(keys: list[DataKey], res: dict[str, Any], *, job: str = "us.eod") -> JobContext:
    return JobContext(job, keys[0].as_of, "run-1", 1, NOW, tuple(keys), None, res)


def resources(
    repos: Any, nq: FakeNasdaq, *, stooq: FakeStooq | None = None, now: datetime = NOW
) -> dict[str, Any]:
    return {
        "us_config": CFG,
        "repos": repos,
        "nasdaq": NasdaqClient(StubLimiter(), transport=nq.transport()),
        "stooq": StooqClient(StubLimiter(), transport=(stooq or FakeStooq()).transport()),
        "yahoo": YahooClient(
            StubLimiter(), transport=FakeYahoo(lambda: now).transport("t"), base_url="https://y"
        ),
    }


def world(n: int = 40, seed: int = 3) -> tuple[Any, FakeNasdaq, dict[str, Any], dict[str, Any]]:
    series, snaps, asof = demo.make(n_tickers=n, seed=seed)
    assert asof == DAY
    repos = memory_repos()
    nq = FakeNasdaq.from_series(series, snaps)
    return repos, nq, series, snaps


def run_universe(repos: Any, nq: FakeNasdaq) -> Any:
    return P.universe(ctx([key("NASDAQ:screener")], resources(repos, nq), job="us.universe"))


# ── 승격(원본 test_us_pipeline 9) ─────────────────────────────────────────────────────────
class TestAsof(unittest.TestCase):
    def test_modal_last_bar_wins(self):
        """기준일은 달력이 아니라 받은 일봉이 정한다.

        한 종목이 거래정지로 하루 전에 멈춰 있어도 기준일은 흔들리지 않는다.
        """
        series = {'A': [{'asof': '2026-09-14'}], 'B': [{'asof': '2026-09-14'}],
                  'C': [{'asof': '2026-09-11'}]}  # fmt: skip
        self.assertEqual(P.asof_from(series), ("2026-09-14", 2))

    def test_empty(self):
        self.assertEqual(P.asof_from({}), (None, 0))


class TestUniverseFilter(unittest.TestCase):
    """스크리너를 대역으로 갈아 끼우고 하한이 생성 단계에서 걸리는지 본다."""

    def test_floor_applied_at_build_time(self):
        rows = [dict(ticker='BIG', name='Big Inc', close=100.0, volume=1e6,
                     mktcap=1e10, turnover=1e8, sector_raw='Technology',
                     industry_raw='Semiconductors'),
                dict(ticker='TINY', name='Tiny Inc', close=100.0, volume=10.0,
                     mktcap=1e8, turnover=1e3, sector_raw='Technology',
                     industry_raw='Semiconductors')]  # fmt: skip
        keep, _meta = P.select_universe(rows, CFG)
        self.assertEqual([r["ticker"] for r in keep], ["BIG"])
        self.assertEqual(keep[0]["sector"], "반도체")


class TestHistoryDepth(unittest.TestCase):
    """새로 들어온 종목은 전 구간을 받아야 한다.

    run #4 에서 유니버스를 300 → 1,719 로 늘린 날, 새 종목이 90일치뿐이라
    52주 판정이 안 되고 ②의 5일 추이가 날짜마다 다른 집합을 셌다. 같은 표의
    칸들이 서로 다른 유니버스를 세면 그건 추이가 아니다.
    """

    def test_new_ticker_gets_deep_window(self):
        series, _snaps, _asof = demo.make(n_tickers=2, seed=5)
        old, new = sorted(series)
        repos = memory_repos()
        # OLD 는 이력이 넉넉하고 NEW 는 오늘 처음 본다.
        repos.us.upsert_daily([P._daily_row(old, r, True) for r in series[old]],  # pyright: ignore[reportPrivateUsage]
                              loaded_by='seed', received_at=NOW)  # fmt: skip
        repos.us.put_universe(D, [
            UsUniverseRow(t, D, f'{t} Inc', 'NASDAQ', 'Technology',
                          'Semiconductors', 1e10, False, 'nasdaq', Quality.OK)
            for t in (old, new)
        ], loaded_by='seed', received_at=NOW)  # fmt: skip
        nq = FakeNasdaq()
        for t in (old, new):
            nq.add_series(t, bars=series[t])
        P.eod(ctx([key("NASDAQ:daily")], resources(repos, nq)))
        by_ticker = {c['symbol']: c['params']['fromdate'] for c in nq.calls
                     if c['kind'] == 'history' and c['symbol'] in (old, new)}  # fmt: skip
        self.assertIn(old, by_ticker)
        self.assertIn(new, by_ticker)
        # 처음 보는 종목의 시작일이 더 과거여야 한다.
        self.assertLess(by_ticker[new], by_ticker[old])


class TestRoundTrip(unittest.TestCase):
    def setUp(self):
        self.repos, self.nq, self.series, self.snaps = world(n=40, seed=3)
        self.asof = DAY
        run_universe(self.repos, self.nq)
        self.res = P.eod(ctx([key('NASDAQ:daily'), key('YAHOO:us_index_daily')],
                             resources(self.repos, self.nq)))  # fmt: skip
        self.day = date.fromisoformat(self.asof)

    def test_series_survive_db(self):
        kept = {u.symbol for u in self.repos.us.universe(self.day)}
        got = self.repos.us.daily(kept, self.day, 520)
        self.assertEqual(len(got), len(kept))
        t = next(iter(sorted(kept)))
        self.assertEqual(len(got[t]), len(self.series[t]))
        self.assertEqual(self.repos.us.last_trade_date(self.day), self.day)

    def test_build_writes_state_and_labels(self):
        self.assertEqual(self.res.status, "ok", self.res.detail)
        art = self.repos.board.us_artifact(self.day, "us_summary")
        assert art is not None
        board, rows = art.payload["board"], art.payload["rows"]
        labels = [r for r in rows if r.get("label")]
        self.assertEqual(len(labels), board["summary"]["newhigh"])

    def test_render_is_self_contained(self):
        art = self.repos.board.us_artifact(self.day, "us_brief")
        assert art is not None
        html = "\n\n".join(art.payload["parts"])
        self.assertIn("간밤 미국장", art.payload["text"])
        self.assertNotIn("<script", html)  # 외부 의존이 없다
        self.assertIn("간밤 미국장", html)

    def test_render_escapes(self):
        art = self.repos.board.us_artifact(self.day, "us_summary")
        assert art is not None
        rows = [dict(ticker='<script>', name='a&b', sector='x', industry='y',
                     chg_pct=1.0, ret_5d=None, turnover=None, mktcap=None,
                     streak=0, label='d60')]  # fmt: skip
        html = BR.telegram_html(art.payload["board"], rows, CFG)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)


class TestSendPath(unittest.TestCase):
    """발송 경로를 끝까지 태운다 — 산출(us_summary) → 텔레그램 조각.

    ET 에서는 이 시험이 없어서 `cmd_us_send` 의 import 누락이 러너에서야 드러났다. KBJ 는 발송을
    아침 브리핑(brief.morning)이 하고, 이 작업은 조각(us_brief)을 저장한다 — 그 조각을 본다.
    """

    def setUp(self):
        self.repos, self.nq, _s, _n = world(n=40, seed=21)
        run_universe(self.repos, self.nq)
        P.eod(ctx([key("NASDAQ:daily")], resources(self.repos, self.nq)))

    def test_send_builds_and_calls_telegram(self):
        art = self.repos.board.us_artifact(date.fromisoformat(DAY), "us_summary")
        assert art is not None
        sent = BR.telegram_chunks(art.payload["board"], art.payload["rows"], CFG)
        self.assertTrue(sent, "발송 조각이 없다")
        for text in sent:
            self.assertIn("간밤 미국장", text)
            self.assertEqual(text.count("<pre>"), text.count("</pre>"))


# ── 새 시험 ──────────────────────────────────────────────────────────────────────────────
def test_유니버스는_펀드를_빼고_하한을_건_판을_통째로_쓴다() -> None:
    repos, nq, _series, snaps = world(n=30, seed=8)
    nq.add_screener("NASDAQ", dict(symbol="ZETF", name="Zeta Growth ETF", lastsale="$90.00",
                                   volume="9,000,000", marketCap="9000000000"))  # fmt: skip
    nq.add_screener("NYSE", dict(symbol="TINY", name="Tiny Corp", lastsale="$3.00",
                                 volume="1,000", marketCap="90000000"))  # fmt: skip
    res = run_universe(repos, nq)
    assert res.status == "ok", res.detail
    uni = repos.us.universe(D)
    syms = {u.symbol for u in uni}
    assert "ZETF" not in syms and res.detail["dropped"]["fund"] == 1
    floor = CFG["universe"]
    for u in uni:
        s = snaps[u.symbol]
        assert u.market_cap is not None and u.market_cap >= floor["min_mktcap_usd"]
        assert s["close"] * s["volume"] >= floor["min_turnover_usd"] * 0.99
        assert u.sector == s["sector_raw"] and u.source == "nasdaq"  # 원본 열(분류는 엔진 단계)
    assert res.detail["kept"] == len(uni) < res.detail["screened"]


def test_수집_경로와_엔진_직접_호출이_같은_수를_말한다() -> None:
    repos, nq, series, snaps = world(n=60, seed=7)
    run_universe(repos, nq)
    res = P.eod(ctx([key("NASDAQ:daily")], resources(repos, nq)))
    assert res.status == "ok", res.detail
    kept = {u.symbol for u in repos.us.universe(D)}
    # 가짜 Nasdaq 은 실 응답처럼 센트 단위('$12.34')로 준다 — 직접 호출도 같은 값으로
    cents = {
        t: [
            dict(b, **{k: round(b[k], 2) for k in ("open", "high", "low", "close")})
            for b in series[t]
        ]
        for t in kept
    }
    rows, board = us_board.build(cents, snaps, DAY, CFG, log=lambda *a: None)
    art = repos.board.us_artifact(D, "us_summary")
    assert art is not None
    got = art.payload["board"]["summary"]
    for k in ("newhigh", "w52_high", "w52_low", "up", "down"):
        assert got[k] == board["summary"][k], k
    assert sorted(r["ticker"] for r in art.payload["rows"] if r.get("label")) == sorted(
        r["ticker"] for r in rows if r.get("label")
    )
    assert res.detail["newhigh"] == got["newhigh"]
    assert art.source == "nasdaq" and art.engine_version and art.quality is Quality.OK


def test_그날_봉이_아직_없으면_공표_전() -> None:
    repos, nq, _series, _snaps = world(n=10, seed=2)
    run_universe(repos, nq)
    res = P.eod(ctx([key("NASDAQ:daily", "2026-08-31")], resources(repos, nq)))
    assert res.status == "not_ready"
    assert "2026-08-31" in res.detail["reason"]
    assert repos.board.us_artifact(date(2026, 8, 31), "us_summary") is None
    # 전 종목을 받기 전에 앞 몇 종목으로 알아챈다 — 1,700종목을 다 받고서야 알던 것(2026-10-19 —
    # 재시도 한 번이 120분 마감을 다 썼다)
    hist = [c for c in nq.calls if "fromdate" in c.get("params", {})]
    assert len(hist) <= P.FRESH_PROBE_N


def test_앞_종목이_하나라도_그날_봉이_없으면_공표_전() -> None:
    """공표가 막 시작돼 앞 종목 일부만 그날 봉이 있으면 전 종목을 받지 않는다(2026-10-20 점검 —
    하나만 보고 통과해 67분 받고 다시 '공표 전'이었다)."""
    day = date(2026, 10, 19)
    bars = {
        "A": [{"asof": "2026-10-19"}],
        "B": [{"asof": "2026-10-16"}],
        "C": [{"asof": "2026-10-19"}],
    }
    with pytest.raises(NotReady, match="B"):
        P._require_day_bar(["A", "B", "C"], lambda t: bars[t], day)  # pyright: ignore[reportPrivateUsage]
    P._require_day_bar(["A", "C"], lambda t: bars[t], day)  # pyright: ignore[reportPrivateUsage]


def test_유니버스가_없으면_공표_전() -> None:
    repos, nq, _series, _snaps = world(n=5, seed=2)
    res = P.eod(ctx([key("NASDAQ:daily")], resources(repos, nq)))
    assert res.status == "not_ready" and "us.universe" in res.detail["reason"]


def test_실패_종목은_사유와_함께_빠진_데이터에_적고_stooq_폴백을_쓴다() -> None:
    repos, nq, series, _snaps = world(n=20, seed=4)
    run_universe(repos, nq)
    kept = sorted(u.symbol for u in repos.us.universe(D))
    gone, via_stooq = kept[0], kept[1]
    for t in (gone, via_stooq):
        nq.accept.pop(t)  # Nasdaq 은 이 둘을 모른다(404)
    st = FakeStooq()
    st.series[via_stooq] = [dict(b) for b in series[via_stooq]]
    res = P.eod(ctx([key("NASDAQ:daily")], resources(repos, nq, stooq=st)))
    assert res.status == "ok", res.detail
    assert res.detail["fetch_failed"] == 1 and gone in res.detail["fail_head"][0]
    bars = repos.us.daily([via_stooq], D, 5)[via_stooq]
    assert {b.source for b in bars} == {"stooq"} and bars[-1].adjusted is None
    art = repos.board.us_artifact(D, "us_summary")
    assert art is not None
    missing = art.payload["board"]["missing"]
    assert any("일봉 수집 실패 1종목" in m and gone in m for m in missing)
    assert art.source == "nasdaq+stooq" or "stooq" in art.source


def test_지수_일봉은_yahoo_로_asset_index() -> None:
    repos, nq, _series, _snaps = world(n=5, seed=2)
    res = P.eod(ctx([key("YAHOO:us_index_daily")], resources(repos, nq)))
    assert res.status == "ok", res.detail
    got = repos.us.daily(["^GSPC", "^IXIC", "^DJI"], D, 3)
    assert set(got) == {"^GSPC", "^IXIC", "^DJI"}
    assert all(b.asset == "index" and b.source == "yahoo" for rs in got.values() for b in rs)
    assert got["^GSPC"][-1].date == D


def test_us_brief_는_아침_브리핑이_싣는_조각이다() -> None:
    repos, nq, _series, _snaps = world(n=30, seed=9)
    run_universe(repos, nq)
    P.eod(ctx([key("NASDAQ:daily")], resources(repos, nq)))
    art = repos.board.us_artifact(D, "us_brief")
    assert art is not None and art.name == "us_brief"
    parts = art.payload["parts"]
    assert parts[0].startswith("🇺🇸 <b>간밤 미국장 신고가</b> · 8/28(금) 마감")
    for p in parts:
        for tag in ("b", "i", "pre"):
            assert p.count(f"<{tag}>") == p.count(f"</{tag}>")
    assert repos.board.us_last_day(date(2026, 8, 31)) == D


def test_P5_합성_세계의_심은_신고가_보류_펀드() -> None:
    """p5_world `expect_us` — 고가 기준 52주 신고가 = 심은 종목, 이력 짧은 종목 보류, 펀드 제외."""
    from tests.fixtures.synthetic.p5_world import US_DAY, build_world

    w = build_world()
    want = w.expect_us()
    cfg = dict(CFG, newhigh=dict(CFG["newhigh"], default_basis="high"))
    uni = [u for u in w.us_universe if not u.is_fund]
    bars = {u.symbol: list(w.us_bars(u.symbol)) for u in uni}
    summary, _brief, info = P.build_artifacts(day=US_DAY, uni=uni, bars=bars, cfg=cfg,
                                              now=NOW)  # fmt: skip
    series = {s: [P._bar_dict(b) for b in rs] for s, rs in bars.items()}  # pyright: ignore[reportPrivateUsage]
    rows, _skipped = E.evaluate_all(series, {}, US_DAY.isoformat(), cfg)
    w52 = sorted(r["ticker"] for r in rows if r["label"] == "w52")
    assert w52 == want["new_highs"]
    assert info["pending"] == len(want["held"])
    assert not set(want["funds"]) & {r["ticker"] for r in summary.payload["rows"]}


# ── 스크리너로 그날 종가 붙이기(2026-10-20 — 신고가는 종가 기준, 종목마다 90일씩 받던 약 70분) ──
THU = "2026-08-27"


def _screener_world(
    n: int = 8, *, bump: dict[str, float] | None = None, chg_shift: dict[str, float] | None = None
) -> tuple[Any, FakeNasdaq, dict[str, Any]]:
    """이력은 08-26 까지 저장, 스크리너·일봉 서버는 08-27(목)까지. bump = 스크리너 종가 배수,
    chg_shift = 스크리너 등락률에 더할 %p(분할 흉내)."""
    series, snaps, _ = demo.make(n_tickers=n, seed=3)
    upto = {t: [b for b in bars if b["asof"] <= THU] for t, bars in series.items()}
    snap27: dict[str, Any] = {}
    for t, bars in upto.items():
        last, prev = bars[-1], bars[-2]
        close = last["close"] * (bump or {}).get(t, 1.0)
        chg = (last["close"] / prev["close"] - 1) * 100 + (chg_shift or {}).get(t, 0.0)
        snap27[t] = {**snaps[t], "close": close, "chg_pct": chg, "volume": last["volume"]}
    repos = memory_repos()
    nq = FakeNasdaq.from_series(upto, snap27)
    P.universe(ctx([key("NASDAQ:screener", THU)], resources(repos, nq), job="us.universe"))
    repos.us.upsert_daily(
        [P._daily_row(t, r, True) for t, bars in upto.items() for r in bars if r["asof"] < THU],  # pyright: ignore[reportPrivateUsage]
        loaded_by="seed", received_at=NOW,
    )  # fmt: skip
    nq.calls.clear()
    return repos, nq, upto


def _hist(nq: FakeNasdaq) -> set[str]:
    return {c["symbol"] for c in nq.calls if c["kind"] == "history"}


def test_이력이_이어진_종목은_스크리너_한_번으로_그날_종가() -> None:
    repos, nq, upto = _screener_world()
    res = P.eod(ctx([key("NASDAQ:daily", THU)], resources(repos, nq)))
    assert res.status == "ok", res.detail
    assert res.detail["screener"] == len(upto) and res.detail["screener_note"] is None
    # 일봉은 앞 종목 확인(3)·수정주가 확인만 — 나머지 종목은 부르지 않는다
    assert len(_hist(nq) & set(upto)) <= P.FRESH_PROBE_N
    t = sorted(upto)[-1]
    bar = repos.us.daily([t], date(2026, 8, 27), 1)[t][-1]
    assert bar.close == pytest.approx(upto[t][-1]["close"], abs=0.01)  # 스크리너는 센트 단위
    assert bar.source == "nasdaq-screener" and bar.high is None


def test_스크리너_종가가_일봉과_다르면_버리고_종목마다_받는다() -> None:
    series, _snaps, _ = demo.make(n_tickers=8, seed=3)
    probe = sorted(series)[0]  # 유니버스 앞 종목(앞 종목 확인 대상) 하나만 1% 어긋나게
    repos, nq, upto = _screener_world(bump={probe: 1.01})
    res = P.eod(ctx([key("NASDAQ:daily", THU)], resources(repos, nq)))
    assert res.status == "ok", res.detail
    assert res.detail["screener"] == 0 and "다르다" in str(res.detail["screener_note"])
    assert _hist(nq) >= set(upto)


def test_분할_의심_종목만_일봉으로_다시() -> None:
    series, _snaps, _ = demo.make(n_tickers=8, seed=3)
    odd = sorted(series)[-1]
    repos, nq, upto = _screener_world(chg_shift={odd: 50.0})
    res = P.eod(ctx([key("NASDAQ:daily", THU)], resources(repos, nq)))
    assert res.status == "ok", res.detail
    assert res.detail["screener"] == len(upto) - 1
    assert odd in _hist(nq)
