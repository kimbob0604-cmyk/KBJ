"""실적 프리뷰 reader — `/api/trade/stock/{code}/preview`(P10 단계 3 —
docs/p10_trade_stocks_plan.md).

계산은 작업 `trade.preview` 와 같은 `kbj.services.engine.trade_preview.stock_preview`(DB 만 읽는다 —
계약 ⑩). 로그인 전용(컨센서스 = KIS). 화면 문구는 규칙 문장이고 매매 권유가 아니다.

- 매핑 없음 404 '매핑 없음', 수출 지수 없음 404(trade.stock_index 예정 시각).
- 컨센서스가 없으면 판정 없이 추정만(메모). 회귀 점이 부족하면 추정도 없음(메모).
"""

from __future__ import annotations

from typing import Final

from kbj.core.quality import Quality
from kbj.core.time import to_kst
from kbj.engines.trade.mapping import resolve
from kbj.engines.trade.preview import PreviewCfg
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.trade import TradePreview, TradePreviewQuarter
from kbj.services.api.readers._common import NoData, ReadContext, envelope
from kbj.services.api.readers.trade_map import NO_MAP, NO_STOCK_INDEX, NOT_MAPPED
from kbj.services.engine.trade_preview import DISCLAIMER, stock_preview

__all__ = ["NOTE_METHOD", "NO_CONSENSUS", "SOURCE", "trade_stock_preview"]

SOURCE: Final = "관세청(공공데이터포털)+DART+KIS 추정실적"
NOTE_METHOD: Final = (
    "추정 = 최근 분기들의 (분기 수출 × 달 평균 원/달러) 대비 매출 회귀(OLS) — 수출 비중이 낮은"
    " 회사는 신뢰가 낮다"
)
NO_CONSENSUS: Final = "컨센서스 없음(수집 대상 아님·12월 결산 아님) — 판정 없이 추정만"


def trade_stock_preview(ctx: ReadContext, *, code: str) -> Envelope[TradePreview]:
    smap = ctx.repos.trade.stock_map()
    if not smap:
        raise NoData(NO_MAP)
    lines = resolve(r for r in smap if r.code == code).lines.get(code)
    if not lines:
        raise NoData(NOT_MAPPED)
    exports = ctx.repos.trade.stock_export(code=code)
    cfg = PreviewCfg()
    today = to_kst(ctx.now()).date()
    now, prev = stock_preview(ctx.repos, code, today=today, cfg=cfg, exports=exports)
    if now is None:
        raise NoData(NO_STOCK_INDEX)
    data = TradePreview(
        code=code, name=lines[0].name, quarter=now.quarter, months_seen=now.months_seen,
        reported=now.reported, export_krw=now.export_krw, revenue_est=now.revenue_est,
        consensus=now.consensus, consensus_note=now.consensus_note, diff=now.diff,
        verdict=now.verdict, prev_verdict=None if prev is None else prev.verdict, r2=now.r2,
        points=now.points, confidence=now.confidence, band=cfg.band,
        history=[
            TradePreviewQuarter(
                quarter=h.quarter, export_krw=h.export_krw, revenue=h.revenue, fitted=h.fitted,
                months=h.months,
            )
            for h in now.history
        ],
    )  # fmt: skip
    notes = [*now.notes, NOTE_METHOD, DISCLAIMER]
    if now.consensus is None and not now.reported:
        notes.insert(0, NO_CONSENSUS)
    monthly = [r for r in exports if len(r.period) == 7]
    return envelope(
        data, source=SOURCE, as_of=max(r.as_of for r in monthly) if monthly else ctx.now(),
        quality=Quality.ESTIMATED, notes=notes, generated_at=ctx.now(),
    )  # fmt: skip
