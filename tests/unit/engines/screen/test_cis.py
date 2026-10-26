"""cis 순추세 스크리너 엔진(kbj.engines.screen.cis) — 합성 일봉만."""

from __future__ import annotations

from datetime import date, timedelta

from kbj.config.screen import load_screen
from kbj.core.quality import Quality
from kbj.core.rows import Bar, IndexBar
from kbj.engines.screen.cis import (
    WHY_BOX,
    WHY_HOT,
    WHY_LIMIT,
    WHY_REGIME,
    WHY_WICK,
    StockMeta,
    regime_of,
    screen_cis,
)

CFG = load_screen().cis
D0 = date(2026, 9, 1)
N = CFG.lookback_days
AS_OF = D0 + timedelta(days=N)


def _bars(code: str, last: tuple[float, float, float, float, int], *, base: float = 10_000.0,
          vol: int = 100_000) -> list[Bar]:  # fmt: skip
    """직전 N 일은 base 근처 박스(고가 base×1.02), 마지막 날은 (시가, 고가, 저가, 종가, 거래량)."""
    out = [
        Bar(code=code, date=D0 + timedelta(days=i), open=base, high=base * 1.02, low=base * 0.98,
            close=base, volume=vol, turnover=int(base * vol), source="krx", venue="KRX",
            quality=Quality.OK)
        for i in range(N)
    ]  # fmt: skip
    o, h, lo, c, v = last
    out.append(
        Bar(code=code, date=AS_OF, open=o, high=h, low=lo, close=c, volume=v,
            turnover=int(c * v), source="krx", venue="KRX", quality=Quality.OK)
    )  # fmt: skip
    return out


def _idx(code: str, closes: list[float]) -> list[IndexBar]:
    return [
        IndexBar(code=code, date=AS_OF - timedelta(days=len(closes) - 1 - i), name=None, open=c,
                 high=c, low=c, close=c, volume=None, turnover=None, source="krx",
                 quality=Quality.OK)
        for i, c in enumerate(closes)
    ]  # fmt: skip


UP = [100.0 + i for i in range(CFG.regime_ma)]  # 오르는 지수 — 국면 켜짐
DOWN = [200.0 - i for i in range(CFG.regime_ma)]
META = {
    "000100": StockMeta("돌파전자", "KOSPI", "common", False),
    "000200": StockMeta("꼬리화학", "KOSPI", "common", False),
    "000300": StockMeta("조용상사", "KOSPI", "common", False),
    "000400": StockMeta("상한바이오", "KOSDAQ", "common", None),
    "000500": StockMeta("박스안", "KOSDAQ", "common", False),
    "000605": StockMeta("돌파전자우", "KOSPI", None, False),
    "000700": StockMeta("관리종목", "KOSPI", "common", True),
}


def _world() -> dict[str, list[Bar]]:
    big = 600_000  # 평균 10만 주의 6배
    return {
        # 박스 위 마감·양봉·고가 근처 → 통과
        "000100": _bars("000100", (10_100, 11_000, 10_050, 10_950, big)),
        # 돌파했지만 윗꼬리 길다(고가 12,000, 종가 10,500)
        "000200": _bars("000200", (10_100, 12_000, 10_000, 10_500, big)),
        # 거래량이 안 튐 → 표에 없음
        "000300": _bars("000300", (10_100, 11_000, 10_050, 10_950, 150_000)),
        # 상한가 근처(+29.9%)
        "000400": _bars("000400", (10_500, 13_000, 10_400, 12_990, big)),
        # 거래량은 튀었는데 박스 안(고가 10,200 아래 마감)
        "000500": _bars("000500", (9_900, 10_150, 9_850, 10_100, big)),
        "000605": _bars("000605", (10_100, 11_000, 10_050, 10_950, big)),
        "000700": _bars("000700", (10_100, 11_000, 10_050, 10_950, big)),
    }


def test_통과·거래량_급증·제외() -> None:
    r = screen_cis(_world(), META, {"코스피": _idx("코스피", UP), "코스닥": _idx("코스닥", UP)},
                   CFG, AS_OF)  # fmt: skip
    by = {x.code: x for x in r.rows}
    assert set(by) == {"000100", "000200", "000400", "000500"}  # 000300 은 거래량이 안 튐
    assert r.passed == 1 and by["000100"].passed and r.rows[0].code == "000100"
    assert by["000100"].vol_ratio == 6.0 and by["000100"].breakout
    assert by["000100"].stop == 10_950 * (1 - CFG.plan.stop_pct / 100)
    assert by["000200"].why_not == (WHY_WICK,)
    assert WHY_LIMIT in by["000400"].why_not and by["000400"].limit_up
    assert by["000500"].why_not[0] == WHY_BOX
    assert r.excluded == {"종류(우선주·스팩·리츠·ETF)": 1, "관리·정지": 1}
    assert r.status_unknown == 1  # 000400 상태 모름 — 넣고 센다
    assert r.scanned == 5 and r.spikes == 4


def test_시장_약세면_통과하지_않고_까닭을_적는다() -> None:
    r = screen_cis(_world(), META, {"코스피": _idx("코스피", DOWN)}, CFG, AS_OF)
    by = {x.code: x for x in r.rows}
    assert not by["000100"].passed and by["000100"].why_not == (WHY_REGIME,)
    kq = next(g for g in r.regimes if g.market == "KOSDAQ")
    assert kq.on is None and kq.index_code is None  # 코스닥 지수 봉 없음 — 막지 않는다


def test_지수_코드는_앞에서부터_봉이_충분한_것() -> None:
    g = regime_of("KOSPI", ("코스피", "0001"), {"0001": _idx("0001", UP)}, CFG.regime_ma, AS_OF)
    assert g.index_code == "0001" and g.on is True and g.ma is not None


def test_그날_봉이_없거나_이력이_짧으면_뺀다() -> None:
    w = _world()
    w["000100"] = w["000100"][:-1]  # 그날 봉 없음(정지)
    w["000200"] = w["000200"][-5:]  # 이력 부족
    r = screen_cis(w, META, {}, CFG, AS_OF)
    assert "000100" not in {x.code for x in r.rows}
    assert r.excluded["이력 부족"] == 1


def test_거래량_배수_상한을_넘으면_과열() -> None:
    idx = {"코스피": _idx("코스피", UP), "코스닥": _idx("코스닥", UP)}
    hot = CFG.model_copy(update={"vol_max": 5.0})
    by = {x.code: x for x in screen_cis(_world(), META, idx, hot, AS_OF).rows}
    assert not by["000100"].passed and by["000100"].why_not == (WHY_HOT,)  # 6배 > 5배
    ok = CFG.model_copy(update={"vol_max": 6.0})
    assert {x.code: x for x in screen_cis(_world(), META, idx, ok, AS_OF).rows}["000100"].passed
