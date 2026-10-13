"""금융위 일별(kbj/services/collectors/fsc_daily.py — D-P5-19, metrics §23). 합성만.

금융위 시세 → daily_bar(source fsc), 같은 날 KRX 종가와 대조 — 허용치 0(정확히 같아야), 불일치
비율이 문턱 이상이면 health 경고만(수집 실패 아님). 대조는 손계산으로 확인한다.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

import pytest

from kbj.config.markets import ReconcileCfg, load_markets
from kbj.core.quality import Quality
from kbj.core.rows import Bar, IndexBar
from kbj.data.private.fsc_index_price.client import FscIndexRow
from kbj.data.private.fsc_stock_price.client import FscPriceRow
from kbj.data.spec import DataKey
from kbj.services.collectors import fsc_daily as F
from kbj.services.scheduler.handlers import JobContext
from kbj.store.repos.memory import memory_repos

DAY = date(2026, 10, 15)
NOW = datetime(2026, 10, 16, 4, 10, tzinfo=UTC)  # 13:10 KST(D+1)
CFG = ReconcileCfg(
    close_tol_pct=0.0, turnover_tol_pct=0.5, mktcap_tol_pct=0.5, warn_mismatch_pct=10
)
CODES = [f"{i:06d}" for i in range(100, 120)]  # 합성 20종목


def _fsc(code: str, close: float | None) -> FscPriceRow:
    return FscPriceRow.model_validate(
        {"basDt": "20261015", "srtnCd": f"A{code}", "itmsNm": f"합성{code}", "mrktCtg": "KOSPI",
         "clpr": None if close is None else str(int(close)), "mkp": "1000", "hipr": "1100",
         "lopr": "900", "trqu": "1000", "trPrc": "1000000"}
    )  # fmt: skip


def _idx(name: str, close: str) -> FscIndexRow:
    return FscIndexRow.model_validate(
        {"basDt": "20261015", "idxNm": name, "clpr": close, "mkp": close, "hipr": close,
         "lopr": close}
    )  # fmt: skip


def _krx(code: str, close: float) -> Bar:
    return Bar(code, DAY, close, close, close, close, 10, 10_000, "krx", "KRX", Quality.OK)


class _Stock:
    def __init__(self, rows: list[FscPriceRow]) -> None:
        self.rows = rows

    def day(self, d: date) -> tuple[list[FscPriceRow], list[Any]]:
        return ([r for r in self.rows if r.bas_dt == d], [])


class _Index:
    def __init__(self, rows: list[FscIndexRow]) -> None:
        self.rows = rows

    def index(self, name: str, start: date, end: date) -> tuple[list[FscIndexRow], list[Any]]:
        return ([r for r in self.rows if r.idx_nm == name and start <= r.bas_dt <= end], [])


def _ctx(
    repos: Any, stock: _Stock, index: _Index, *, ds: tuple[str, ...] = ("15094808",)
) -> JobContext:
    keys = tuple(DataKey("DATAGO", d, DAY.isoformat()) for d in ds)
    res = {"repos": repos, "markets": _markets(), "fsc_stock": stock, "fsc_index": index}
    return JobContext("market.fsc_daily", DAY.isoformat(), "r1", 1, NOW, keys, None, res)


def _markets() -> Any:
    m = load_markets()
    return m.model_copy(update={"reconcile": CFG})


# ── 대조(손계산) ─────────────────────────────────────────────────────────────────────────
def test_대조_손계산_정확히_같아야_하고_문턱_이상이면_경고() -> None:
    krx = {c: 1000.0 for c in CODES}
    fsc: dict[str, float | None] = {c: 1000.0 for c in CODES}
    fsc[CODES[0]] = 1001.0  # 1원 차이도 불일치(허용치 0)
    fsc[CODES[1]] = 999.0
    fsc["999999"] = 5.0  # 금융위에만
    del fsc[CODES[2]]  # KRX 에만
    fsc[CODES[3]] = None  # 종가 없음 — 대조에서 뺀다
    chk = F.compare_close(krx, fsc, CFG)
    # 둘 다 종가 있는 종목 = 20 − 1(KRX 만) − 1(종가 없음) = 18, 불일치 2 → 11.111% ≥ 10%
    assert (chk.checked, chk.mismatch, chk.only_fsc, chk.only_krx) == (18, 2, 1, 1)
    assert chk.mismatch_pct == pytest.approx(11.111, abs=1e-3)
    assert chk.warn is True
    assert chk.examples[0] == f"{CODES[0]} 1000→1001"
    calm = F.compare_close(krx, {**fsc, CODES[1]: 1000.0}, CFG)  # 1/18 = 5.6% < 10%
    assert calm.mismatch == 1 and calm.warn is False


def test_허용치가_있으면_그_안은_같다고_본다() -> None:
    cfg = CFG.model_copy(update={"close_tol_pct": 0.2})
    chk = F.compare_close({"000100": 1000.0}, {"000100": 1001.0}, cfg)  # 0.1% ≤ 0.2%
    assert chk.mismatch == 0 and chk.checked == 1


def test_대조할_것이_없으면_경고하지_않는다() -> None:
    chk = F.compare_close({}, {"000100": 1.0}, CFG)
    assert chk.checked == 0 and chk.warn is False and chk.mismatch_pct is None


# ── 처리기 ───────────────────────────────────────────────────────────────────────────────
def test_금융위_행은_source_fsc_로_쓰고_KRX_원장을_밀어내지_않는다(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repos = memory_repos()
    repos.market.upsert_daily_bars([_krx(c, 1000.0) for c in CODES], loaded_by="krx.daily")
    rows = [_fsc(c, 1000.0) for c in CODES]
    rows[0] = _fsc(CODES[0], 1010.0)
    rows[1] = _fsc(CODES[1], 990.0)
    rows[2] = _fsc(CODES[2], 1020.0)  # 3/20 = 15% ≥ 10% → 경고
    rows.append(_fsc("000999", 500.0))  # 금융위에만 있는 종목
    with caplog.at_level(logging.WARNING):
        res = F.run(_ctx(repos, _Stock(rows), _Index([])))
    assert res.status == "ok", res.detail  # 불일치는 경고만 — 수집은 성공
    d = res.detail["stock"]
    assert (d["checked"], d["mismatch"], d["only_fsc"], d["warn"]) == (20, 3, 1, True)
    assert res.detail["warn"] is True
    assert any("fsc_mismatch" in r.getMessage() for r in caplog.records)
    best = repos.market.series(CODES[:1], DAY, 1)[CODES[0]][-1]
    assert best.source == "krx" and best.close == 1000.0  # 원장은 KRX 그대로
    only = repos.market.series(["000999"], DAY, 1)["000999"][-1]
    assert only.source == "fsc" and only.close == 500.0 and only.turnover == 1_000_000


def test_빈_응답은_공표_전() -> None:
    res = F.run(_ctx(memory_repos(), _Stock([]), _Index([])))
    assert res.status == "not_ready" and "공표 전" in res.detail["reason"]


def test_지수는_KRX_지수와_같은_코드로_대조한다() -> None:
    repos = memory_repos()
    repos.market.upsert_index_bars(
        [IndexBar("코스피", DAY, "코스피", None, None, None, 2500.5, None, None, "krx", Quality.OK),
         IndexBar("코스닥", DAY, "코스닥", None, None, None, 800.0, None, None, "krx", Quality.OK)],
        loaded_by="krx.daily",
    )  # fmt: skip
    idx = _Index([_idx("코스피", "2500.5"), _idx("코스닥", "801"), _idx("코스피 200", "330")])
    res = F.run(_ctx(repos, _Stock([]), idx, ds=("15094807",)))
    assert res.status == "ok", res.detail
    d = res.detail["index"]
    assert (d["checked"], d["mismatch"], d["only_fsc"], d["rows"]) == (2, 1, 1, 3)
    got = repos.market.index_series(["코스피200"], DAY, 1)["코스피200"][-1]
    assert got.source == "fsc" and got.close == 330.0
