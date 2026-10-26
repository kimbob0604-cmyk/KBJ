"""cis 순추세 백테스트 CLI(kbj.services.engine.backtest_cis) — 합성 일봉 주입(DB 없음)."""

from __future__ import annotations

import io
import json
import random
from datetime import date, timedelta

from kbj.config.screen import CisCfg
from kbj.core.quality import Quality
from kbj.core.rows import IndexBar
from kbj.engines.screen.backtest import OhlcSeries
from kbj.engines.screen.cis import StockMeta
from kbj.services.engine.backtest_cis import Loaded, main

D0 = date(2024, 1, 1)
NDAYS = 320


def _world(seed: int = 7) -> Loaded:
    rnd = random.Random(seed)  # noqa: S311 — 시험 데이터(암호 아님)
    days = [D0 + timedelta(days=i) for i in range(NDAYS)]
    series: dict[str, OhlcSeries] = {}
    meta: dict[str, StockMeta] = {}
    for k in range(12):
        code = f"{100100 + k * 100:06d}"
        px = 10_000.0
        o: list[float] = []
        h: list[float] = []
        lo: list[float] = []
        c: list[float] = []
        v: list[int] = []
        t: list[int] = []
        for i in range(NDAYS):
            spike = i % 37 == k + 25  # 종목마다 다른 날 거래량 급증 + 급등
            drift = 0.06 if spike else rnd.uniform(-0.02, 0.021)
            op = px * (1 + rnd.uniform(-0.005, 0.005))
            cl = px * (1 + drift)
            hi, lw = max(op, cl) * 1.005, min(op, cl) * 0.995
            vol = 1_000_000 * (6 if spike else 1)
            for col, x in ((o, op), (h, hi), (lo, lw), (c, cl)):
                col.append(x)
            v.append(vol)
            t.append(int(cl * vol))
            px = cl
        series[code] = OhlcSeries(code, tuple(days), tuple(o), tuple(h), tuple(lo), tuple(c),
                                  tuple(v), tuple(t))  # fmt: skip
        meta[code] = StockMeta(f"합성{k}", "KOSPI" if k % 2 else "KOSDAQ", "common", False)
    meta["999990"] = StockMeta("합성우", "KOSPI", "pref", False)
    idx = {
        "코스피": [
            IndexBar(code="코스피", date=d, name=None, open=None, high=None, low=None,
                     close=2_500 + i, volume=None, turnover=None, source="krx",
                     quality=Quality.OK)
            for i, d in enumerate(days)
        ]
    }  # fmt: skip
    return Loaded(series=series, meta=meta, index_bars=idx, days=days)


def _load(n: int, end: date | None, cfg: CisCfg) -> Loaded:
    w = _world()
    return Loaded(w.series, w.meta, w.index_bars, w.days[-n:])


def _run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    rc = main(list(argv), out=out, load=_load)
    return rc, out.getvalue()


def test_보고서() -> None:
    rc, text = _run("--years", "1", "--trades", "3")
    assert rc == 0
    words = ("cis 순추세 백테스트", "총수익", "이김", "손익비", "최대 낙폭", "주의:", "최근 거래")
    for word in words:
        assert word in text, word
    assert "NaN" not in text


def test_json·csv·옵션() -> None:
    rc, text = _run("--years", "1", "--json", "--no-regime", "--stop", "3", "--no-pyramid")
    s = json.loads(text)
    assert rc == 0 and s["summary"]["trades"] >= 1 and s["signals"] >= s["summary"]["trades"]
    total = s["summary"]["wins"] + s["summary"]["losses"] + s["summary"]["flats"]
    assert total == s["summary"]["trades"]
    rc, text = _run("--years", "1", "--csv")
    head = text.splitlines()[0]
    assert rc == 0 and head.startswith("entry_date,exit_date,code")


def test_일봉이_모자라면_오류() -> None:
    def few(n: int, end: date | None, cfg: CisCfg) -> Loaded:
        w = _world()
        return Loaded(w.series, w.meta, w.index_bars, w.days[:5])

    out = io.StringIO()
    assert main([], out=out, load=few) == 2 and "오류" in out.getvalue()


def test_sweep_비교_표() -> None:
    from kbj.services.engine.backtest_cis import SWEEP

    rc, text = _run("--years", "1", "--sweep")
    assert rc == 0 and "비교" in text
    for name, *_ in SWEEP:
        assert name in text, name
    rc, text = _run("--years", "1", "--stop-mode", "atr", "--entry", "confirm", "--trades", "0")
    assert rc == 0 and "손절 방식 atr" in text and "진입 confirm" in text


def test_sweep_청산_규칙_비교() -> None:
    from kbj.services.engine.backtest_cis import EXIT_SWEEP

    rc, text = _run("--years", "1", "--sweep", "exit")
    assert rc == 0 and "청산 규칙만 바꾼다" in text and "평균R" in text
    for name, *_ in EXIT_SWEEP:
        assert name in text, name


def test_edge_신호_우위_표() -> None:
    rc, text = _run("--years", "1", "--edge")
    assert rc == 0 and "기준: 전 종목·모든 날" in text and "강세·확인 진입" in text
    assert "NaN" not in text
    rc, text = _run("--years", "1", "--edge", "--no-regime")
    assert rc == 0 and "시장 국면 끔" in text


def test_sweep_견고성_격자·연도별() -> None:
    from kbj.services.engine.backtest_cis import ROBUST_SWEEP

    rc, text = _run("--years", "1", "--sweep", "robust")
    assert rc == 0 and "격자" in text and "연도별" in text and "24년" in text
    for name, *_ in ROBUST_SWEEP:
        assert name in text, name


def test_배수_상한·종가_진입·청산_끔() -> None:
    rc, text = _run("--years", "1", "--vol-max", "5", "--entry", "close", "--no-time-stop",
                    "--no-ma-exit", "--trades", "0")  # fmt: skip
    assert rc == 0 and "×3~5" in text and "진입 close" in text
    assert "시간 손절 끔" in text and "이평 청산 끔" in text
    _, capped = _run("--years", "1", "--vol-max", "5.5", "--json")
    _, free = _run("--years", "1", "--json")
    assert json.loads(capped)["signals"] < json.loads(free)["signals"]  # 합성 급증은 6배
