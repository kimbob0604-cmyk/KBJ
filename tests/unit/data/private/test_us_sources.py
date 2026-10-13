"""Nasdaq·Stooq 어댑터(kbj/data/private/{nasdaq,stooq} — docs/p5_design.md §1.6 US).

1) 승격: ETF-Traker `board/tests/test_us_sources.py` 21 중 Yahoo 3(`TestYahoo` — FND 가
   `test_yahoo.py` 로 옮김)을 뺀 18 — 같은 단언, import 경로만(`from board.us import sources as S` →
   kbj 파서 모음 `S`, 비공개 `_num` 은 공개 `num`). 설정은 `config/us.yaml`(git mv).
2) 새 시험: 가짜 서버(tests/fakes/nasdaq_server.py)로 클라이언트 — 리미터·오류 사유·클래스주 표기
   차례·Stooq 폴백·수정주가 점검.

미국장 수집기 파서 검증 — 픽스처만 쓴다. 네트워크가 필요 없다.

수집기는 '받아서 정규화만' 하므로 시험할 것도 그것뿐이다.
못 받은 값을 0 으로 채우지 않는지가 핵심이다 — 0 은 '거래 없음' 이라는 사실이고
None 은 '못 받았다' 는 사실이라 섞이면 브레드스가 통째로 틀린다.
"""

from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from kbj.data.private.nasdaq import client as nq
from kbj.data.private.nasdaq.client import NasdaqClient, NasdaqError, NasdaqThrottled
from kbj.data.private.nasdaq.history import HistoryError, adjust_probe, adjusted_flag, history
from kbj.data.private.stooq import client as sq
from kbj.data.private.stooq.client import StooqClient, StooqError
from kbj.engines.us.config import load_us
from tests.fakes.nasdaq_server import FakeNasdaq, FakeStooq, StubLimiter

# 원본 시험의 `S` — 출처마다 나뉜 파서를 한 이름으로 모은다(단언은 그대로)
S = SimpleNamespace(
    _num=nq.num,
    parse_screener=nq.parse_screener,
    parse_nasdaq_history=nq.parse_nasdaq_history,
    nasdaq_symbol=nq.nasdaq_symbol,
    nasdaq_symbol_forms=nq.nasdaq_symbol_forms,
    stooq_symbol=sq.stooq_symbol,
    parse_stooq=sq.parse_stooq,
)

CFG = load_us()

SCREENER = {'data': {'rows': [
    {'symbol': 'AAPL', 'name': 'Apple Inc. Common Stock', 'lastsale': '$226.80',
     'netchange': '1.50', 'pctchange': '0.666%', 'volume': '41,000,000',
     'marketCap': '3450000000000', 'sector': 'Technology',
     'industry': 'Computer Manufacturing', 'country': 'United States'},
    {'symbol': 'SPY', 'name': 'SPDR S&P 500 ETF Trust', 'lastsale': '$500.00',
     'pctchange': '0.10%', 'volume': '1000', 'marketCap': '',
     'sector': '', 'industry': ''},
    {'symbol': 'ZZZ', 'name': 'Zed Inc. Common Stock', 'lastsale': '',
     'pctchange': '', 'volume': '', 'marketCap': '', 'sector': '', 'industry': ''},
    {'symbol': 'LOSS', 'name': 'Loss Corp Common Stock', 'lastsale': '$10.00',
     'pctchange': '-4.50%', 'netchange': '-0.47', 'volume': '2,000,000',
     'marketCap': '900000000', 'sector': 'Finance', 'industry': 'Major Banks'},
]}}  # fmt: skip


# ── 승격(원본 그대로) ──────────────────────────────────────────────────────────────────────
class TestNum(unittest.TestCase):
    def test_strips_symbols(self):
        self.assertEqual(S._num("$226.80"), 226.8)
        self.assertEqual(S._num("1,234,567"), 1234567.0)
        self.assertEqual(S._num("0.666%"), 0.666)
        self.assertEqual(S._num("-4.50%"), -4.5)

    def test_missing_is_none_not_zero(self):
        for v in ("", None, "--", "N/A"):
            self.assertIsNone(S._num(v), v)

    def test_parenthesis_is_negative(self):
        self.assertEqual(S._num("(1.25)"), -1.25)


class TestScreener(unittest.TestCase):
    def test_parses_and_drops_funds(self):
        rows, drop = S.parse_screener(SCREENER, CFG)
        tickers = [r["ticker"] for r in rows]
        self.assertIn("AAPL", tickers)
        self.assertNotIn("SPY", tickers)  # ETF 는 기업이 아니다
        self.assertNotIn("ZZZ", tickers)  # 가격이 없으면 담지 않는다
        self.assertEqual(drop["fund"], 1)
        self.assertEqual(drop["no_price"], 1)

    def test_turnover_is_marked_estimate(self):
        rows, _ = S.parse_screener(SCREENER, CFG)
        a = next(r for r in rows if r["ticker"] == "AAPL")
        self.assertEqual(a["turnover"], 226.8 * 41_000_000)
        self.assertTrue(a["turnover_is_estimate"])

    def test_negative_change_kept(self):
        rows, _ = S.parse_screener(SCREENER, CFG)
        r = next(r for r in rows if r["ticker"] == "LOSS")
        self.assertEqual(r["chg_pct"], -4.5)

    def test_empty_payload_is_empty_not_crash(self):
        rows, _ = S.parse_screener({}, CFG)
        self.assertEqual(rows, [])


class TestNasdaqHistory(unittest.TestCase):
    """러너에서 stooq 가 빈 응답, yahoo 가 429 라 이쪽이 주 경로가 됐다 (D-077)."""

    PAYLOAD: ClassVar[dict[str, Any]] = {'data': {'tradesTable': {'rows': [
        {'date': '09/12/2025', 'close': '$229.35', 'volume': '55,000,000',
         'open': '$228.00', 'high': '$230.00', 'low': '$227.00'},
        {'date': '09/11/2025', 'close': '$227.00', 'volume': '--',
         'open': '$226.00', 'high': '$228.00', 'low': '$225.00'},
    ]}}}  # fmt: skip

    def test_parse_and_sort(self):
        rows = S.parse_nasdaq_history(self.PAYLOAD)
        self.assertEqual([r["asof"] for r in rows], ["2025-09-11", "2025-09-12"])
        self.assertEqual(rows[-1]["close"], 229.35)
        self.assertEqual(rows[-1]["volume"], 55_000_000.0)

    def test_missing_volume_is_none(self):
        rows = S.parse_nasdaq_history(self.PAYLOAD)
        self.assertIsNone(rows[0]["volume"])  # '--' 는 0 이 아니다

    def test_bad_date_dropped(self):
        p = {"data": {"tradesTable": {"rows": [{"date": "N/A", "close": "$1"}]}}}
        self.assertEqual(S.parse_nasdaq_history(p), [])

    def test_empty_payload(self):
        self.assertEqual(S.parse_nasdaq_history({}), [])

    def test_symbol_forms(self):
        """클래스주 표기가 소스마다 다르다. 한 곳에서 바꾼다.

        나스닥 쪽은 **인코딩까지** 해야 한다. 슬래시를 그대로 두면 URL 경로가
        갈라져 엉뚱한 주소가 된다 — run #2 에서 BRK/B 한 종목만 실패했다.
        """
        self.assertEqual(S.nasdaq_symbol("BRK.B"), "BRK%2FB")
        self.assertEqual(S.nasdaq_symbol("BRK/B"), "BRK%2FB")
        self.assertEqual(S.nasdaq_symbol("aapl"), "AAPL")
        self.assertEqual(S.stooq_symbol("BRK.B"), "brk-b.us")


class TestStooq(unittest.TestCase):
    CSV = ('Date,Open,High,Low,Close,Volume\n'
           '2026-09-11,10,11,9.5,10.5,1000\n'
           '2026-09-14,10.5,12,10.4,11.8,2200\n')  # fmt: skip

    def test_parse(self):
        rows = S.parse_stooq(self.CSV)
        self.assertEqual([r["asof"] for r in rows], ["2026-09-11", "2026-09-14"])
        self.assertEqual(rows[-1]["close"], 11.8)
        self.assertEqual(rows[-1]["source"], "stooq")

    def test_sorted_ascending(self):
        rows = S.parse_stooq('Date,Open,High,Low,Close,Volume\n'
                             '2026-09-14,1,1,1,1,1\n2026-09-11,1,1,1,1,1\n')  # fmt: skip
        self.assertEqual([r["asof"] for r in rows], ["2026-09-11", "2026-09-14"])

    def test_garbage_lines_dropped(self):
        self.assertEqual(S.parse_stooq("No data\n"), [])
        self.assertEqual(S.parse_stooq(""), [])


class TestSymbolFallback(unittest.TestCase):
    """클래스주는 표기가 소스마다 다르다. 추측해서 하나를 고르지 않는다.

    인코딩만으로는 안 됐다 — run #4 에서 BRK/A·BRK/B 가 HTTP 404 였다.
    순서대로 물어보고 되는 표기를 쓴다.
    """

    def test_class_share_has_several_forms(self):
        forms = S.nasdaq_symbol_forms("BRK/B")
        self.assertEqual(forms[0], "BRK%2FB")
        self.assertIn("BRK.B", forms)
        self.assertIn("BRK-B", forms)

    def test_plain_ticker_has_exactly_one_form(self):
        self.assertEqual(S.nasdaq_symbol_forms("AAPL"), ["AAPL"])

    def test_forms_are_deduped_and_ordered(self):
        forms = S.nasdaq_symbol_forms("BRK.B")
        self.assertEqual(len(forms), len(set(forms)))
        self.assertEqual(forms[0], "BRK.B")


# ── 새 시험(가짜 서버) ─────────────────────────────────────────────────────────────────────
def _limiter() -> StubLimiter:
    return StubLimiter()


def _nasdaq(fake: FakeNasdaq) -> NasdaqClient:
    return NasdaqClient(_limiter(), transport=fake.transport(), acquire_timeout_s=1.0)


def _stooq(fake: FakeStooq) -> StooqClient:
    return StooqClient(_limiter(), transport=fake.transport(), acquire_timeout_s=1.0)


D0, D1 = date(2026, 9, 1), date(2026, 9, 15)


def test_스크리너는_거래소마다_부르고_겹친_종목은_한번만() -> None:
    fake = FakeNasdaq.synthetic(n=6, seed=3)
    rows, drop = _nasdaq(fake).screener(CFG)
    assert [c["exchange"] for c in fake.calls if c["kind"] == "screener"] == [
        "NASDAQ",
        "NYSE",
        "AMEX",
    ]
    assert len({r["ticker"] for r in rows}) == len(rows)
    assert drop["fund"] >= 1  # 합성 세계에 ETF 한 줄을 심었다
    assert all(r["source"] == "nasdaq-screener" for r in rows)


def test_스크리너_0행이면_예외() -> None:
    fake = FakeNasdaq.synthetic(n=3, seed=1)
    fake.empty_exchanges.add("NYSE")
    with pytest.raises(NasdaqError, match="0행"):
        _nasdaq(fake).screener(CFG)


def test_스크리너_403_은_사유를_담아_올린다() -> None:
    fake = FakeNasdaq.synthetic(n=3, seed=1)
    fake.status["screener"] = 403
    with pytest.raises(NasdaqError) as e:
        _nasdaq(fake).screener(CFG, exchanges=["NASDAQ"])
    assert e.value.status == 403


def test_클래스주는_표기_후보를_차례로_물어_되는_표기를_쓴다() -> None:
    fake = FakeNasdaq.synthetic(n=3, seed=2)
    fake.add_series("BRK.B", n=30, accept_form="BRK-B")
    rows = _nasdaq(fake).daily("BRK.B", D0, D1)
    tried = [c["symbol"] for c in fake.calls if c["kind"] == "history"]
    assert tried[0] == "BRK.B" and tried[-1] == "BRK-B"
    assert rows and all(r["source"] == "nasdaq" for r in rows)


def test_히스토리_0행은_거래없음이_아니라_예외() -> None:
    fake = FakeNasdaq.synthetic(n=3, seed=2)
    fake.add_series("NONE", n=0)
    with pytest.raises(NasdaqError, match="0행"):
        _nasdaq(fake).daily("NONE", D0, D1)


def test_429_는_감속하고_폴백하지_않는다() -> None:
    fake = FakeNasdaq.synthetic(n=3, seed=2)
    fake.status["history"] = 429
    st = FakeStooq.synthetic(["AAPL"], seed=2)
    lim = StubLimiter()
    client = NasdaqClient(lim, transport=fake.transport())
    with pytest.raises(NasdaqThrottled):
        history("AAPL", CFG, nasdaq=client, stooq=_stooq(st), start=D0, end=D1)
    assert st.calls == [] and lim.slowed == 1


def test_나스닥이_실패하면_stooq_로_받고_source_에_실제_출처() -> None:
    fake = FakeNasdaq.synthetic(n=3, seed=4)
    st = FakeStooq.synthetic(["ZZQQ"], seed=4)
    rows = history("ZZQQ", CFG, nasdaq=_nasdaq(fake), stooq=_stooq(st), start=D0, end=D1)
    assert rows and {r["source"] for r in rows} == {"stooq"}
    assert all(r["asof"] <= D1.isoformat() for r in rows)


def test_모든_출처가_실패하면_사유를_잇는다() -> None:
    fake = FakeNasdaq.synthetic(n=3, seed=4)
    st = FakeStooq.synthetic([], seed=4)
    with pytest.raises(HistoryError) as e:
        history("ZZQQ", CFG, nasdaq=_nasdaq(fake), stooq=_stooq(st), start=D0, end=D1)
    assert set(e.value.reasons) == {"nasdaq", "stooq"}


def test_stooq_빈_본문과_한도_문구는_예외() -> None:
    st = FakeStooq.synthetic(["AAA"], seed=1)
    st.blank.add("AAA")
    with pytest.raises(StooqError, match="0행"):
        _stooq(st).daily("AAA")
    st.blank.clear()
    st.limit_hit = True
    with pytest.raises(StooqError, match=r"데이터 없음|Exceeded"):
        _stooq(st).daily("AAA")


def test_수정주가_점검은_사실만_돌려준다() -> None:
    flat = [dict(asof=f"2024-06-{d:02d}", close=100.0) for d in range(1, 20)]
    stepped = [*flat[:9], *(dict(r, close=10.0) for r in flat[9:])]
    ok = adjust_probe(lambda t: flat)
    bad = adjust_probe(lambda t: stepped)
    assert ok[0][1] is True and adjusted_flag(ok) is True
    assert bad[0][1] is False and "미조정" in bad[0][2] and adjusted_flag(bad) is False

    def boom(t: str) -> list[dict[str, Any]]:
        raise NasdaqError("history:NVDA", 500, "서버 오류")

    failed = adjust_probe(boom)
    assert failed[0][1] is False and adjusted_flag(failed) is None


def test_설정이_모르는_출처면_ValueError() -> None:
    cfg = dict(CFG, sources=dict(CFG["sources"], history="yahoo"))
    with pytest.raises(ValueError, match="모르는 일봉 소스"):
        history("AAPL", cfg, nasdaq=None, stooq=None, start=D0, end=D1)
