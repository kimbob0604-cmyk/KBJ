"""실적 프리뷰 작업 `trade.preview`(P10 단계 3 — `kbj.services.engine.trade_preview`): 환율 달 평균·
연간 컨센서스(12월 결산 추정만)·판정 변화 묶음 한 통(마지막 달 subject)·0건이면 안 보냄.
손으로 만든 값(실데이터 아님)."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

from kbj.core.fin_rows import ConsensusRow, QuarterRow
from kbj.core.p5_rows import MacroSeriesRow
from kbj.core.quality import Quality
from kbj.core.trade_rows import StockExportRow
from kbj.engines.trade.preview import quarter_months
from kbj.services.engine.trade_preview import (
    KIND,
    PreviewRepos,
    annual_consensus,
    digest,
    monthly_fx,
    run_preview,
    stock_preview,
)

NOW = datetime(2026, 10, 21, 7, 0, tzinfo=UTC)
TODAY = date(2026, 10, 21)
CODE = "000001"


def _months(last: str) -> list[str]:
    out, y, m = [], 2024, 1
    while f"{y:04d}-{m:02d}" <= last:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _export(code: str, ym: str, usd: int) -> StockExportRow:
    return StockExportRow(
        code=code, period=ym, export_usd=Decimal(usd), yoy=None, revenue_yoy=None, status="ok",
        map_version="v2", source="KBJ:trade.stock_index", as_of=NOW, quality=Quality.OK,
    )  # fmt: skip


def _quarter(y: int, q: int, rev: int) -> QuarterRow:
    end = {1: date(y, 3, 31), 2: date(y, 6, 30), 3: date(y, 9, 30), 4: date(y, 12, 31)}[q]
    return QuarterRow(
        corp_code="99000001", fiscal_year=y, fiscal_q=q, fs_div="CFS",  # pyright: ignore[reportArgumentType]
        period_start=date(y, 3 * (q - 1) + 1, 1), period_end=end, rcept_dt=None, tier="main",
        revenue=rev,
    )  # fmt: skip


@dataclass
class _Macro:
    rows: dict[str, list[MacroSeriesRow]] = field(default_factory=dict[str, list[MacroSeriesRow]])

    def series(
        self, series_ids: Collection[str], start: str | None = None, end: str | None = None
    ) -> dict[str, list[MacroSeriesRow]]:
        return {s: self.rows[s] for s in series_ids if s in self.rows}


@dataclass
class _World:
    exports: list[StockExportRow]
    quarters: list[QuarterRow]
    snaps: list[ConsensusRow]
    macro: _Macro = field(default_factory=_Macro)
    macro_prv: _Macro = field(default_factory=_Macro)

    @property
    def trade(self) -> Any:
        ex = self.exports
        return SimpleNamespace(
            stock_export=lambda code=None: [r for r in ex if code is None or r.code == code],
            stock_map=lambda: [SimpleNamespace(code=CODE, name="가상전자")],
        )

    @property
    def filings(self) -> Any:
        return SimpleNamespace(corp_index=lambda: {CODE: ("99000001", "가상전자")})

    @property
    def fin(self) -> Any:
        return SimpleNamespace(quarters=lambda corp: self.quarters)

    @property
    def consensus(self) -> Any:
        return SimpleNamespace(
            snapshots=lambda code, s, e: [r for r in self.snaps if r.code == code]
        )


def _world(last: str, *, annual_ratio: float) -> _World:
    """매출 = 1000 + 2 × 분기 수출(환율 1 — 환율 시리즈 없음) 이 맞는 세계."""
    months = _months(last)
    usd = {ym: 100 + k for k, ym in enumerate(months)}
    quarters = []
    for y in (2024, 2025, 2026):
        for q in range(1, 5):
            qs = f"{y}Q{q}"
            if qs > "2026Q2":
                break
            quarters.append(_quarter(y, q, 1000 + 2 * sum(usd[m] for m in quarter_months(qs))))
    seen = [usd[m] for m in quarter_months("2026Q3") if m in usd]
    q3 = 1000 + 2 * sum(seen) / len(seen) * 3
    done = sum(r.revenue or 0 for r in quarters if r.fiscal_year == 2026)
    annual = int(done + q3 * annual_ratio * 2)
    snaps = [
        ConsensusRow(CODE, date(2026, 10, 1), "202612", True, annual, None, None, None, None),
        ConsensusRow(CODE, date(2026, 10, 1), "202706", True, 10**15, None, None, None, None),
        ConsensusRow(CODE, date(2026, 9, 1), "202612", True, 1, None, None, None, None),  # 옛 스냅
    ]
    return _World([_export(CODE, ym, v) for ym, v in usd.items()], quarters, snaps)


def _repos(w: _World) -> PreviewRepos:
    return cast(PreviewRepos, w)


def test_연간_컨센서스는_최근_스냅의_12월_결산만() -> None:
    w = _world("2026-08", annual_ratio=1.0)
    got = annual_consensus(_repos(w), CODE, TODAY)
    assert list(got) == [2026] and got[2026] > 1


def test_환율은_앞_시리즈_달_평균() -> None:
    def row(sid: str, d: str, v: int) -> MacroSeriesRow:
        return MacroSeriesRow(sid, d, Decimal(v), "원", "ECOS", NOW, Quality.OK)

    w = _world("2026-08", annual_ratio=1.0)
    w.macro_prv.rows["ECOS:731Y001:usdkrw_close"] = [
        row("ECOS:731Y001:usdkrw_close", "2026-08-03", 9)
    ]
    assert monthly_fx(_repos(w), "2026-01", "2026-08") == {"2026-08": 9.0}  # 공개가 비면 로그인
    pub = "ECOS:731Y003:usdkrw"
    w.macro.rows[pub] = [row(pub, "2026-08-03", 1300), row(pub, "2026-08-04", 1310)]
    assert monthly_fx(_repos(w), "2026-01", "2026-08") == {"2026-08": 1305.0}


def test_판정이_그대로면_보내지_않는다() -> None:
    w = _world("2026-08", annual_ratio=1.2)  # 컨센서스가 정답의 120% → 7월만이어도 8월까지도 MISS
    now, prev = stock_preview(_repos(w), CODE, today=TODAY)
    assert now is not None and now.verdict == "MISS" and now.quarter == "2026Q3"
    assert prev is not None and prev.quarter == "2026Q3" and prev.verdict == "MISS"
    sent: list[str] = []
    res = run_preview(_repos(w), today=TODAY, notify=lambda t, **kw: sent.append(t))
    assert res.status == "ok" and res.detail["verdicts"] == {"MISS": 1}
    assert sent == [] and res.rows == 0


def test_새_분기_판정은_신규로_한_통_마지막_달_subject() -> None:
    w = _world("2026-07", annual_ratio=0.8)  # 7월이 첫 달 — 6월까지는 2026Q2(공시됨, 판정 없음)
    sent: list[tuple[str, dict[str, Any]]] = []
    res = run_preview(_repos(w), today=TODAY, notify=lambda t, **kw: sent.append((t, kw)))
    assert res.rows == 1 and len(sent) == 1
    text, kw = sent[0]
    assert kw["kind"] == KIND and kw["subject"] == "2026-07"
    assert "가상전자(000001) 2026Q3 신규 → BEAT" in text and "매매 권유가 아니다" in text


def test_지수_없으면_not_ready_묶음_상한() -> None:
    w = _world("2026-08", annual_ratio=1.0)
    w.exports = []
    assert run_preview(_repos(w), today=TODAY, notify=None).status == "not_ready"
    w = _world("2026-07", annual_ratio=0.8)
    now, _ = stock_preview(_repos(w), CODE, today=TODAY)
    assert now is not None
    text = digest("2026-07", [(f"{i:06d}", "가상", now, None) for i in range(35)])
    assert "외 5종목" in text and text.count("\n- ") == 30
