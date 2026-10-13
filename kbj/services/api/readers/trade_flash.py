"""수출 속보 reader — `/api/trade/flash`(docs/p6_design.md §5, metrics §25 — 묶음 FLASH).

`pub_trade.ten_day` 의 그 종류(kind) 행을 최근 `months` 개월 + 전년 같은 구간(12개월)만큼 읽어
엔진(`kbj.engines.trade.flash.segments`)에 넘긴다. 표는 **최신 값**(정정 반영) 기준 YoY, 칸마다
발표 당시 값(`first_usd`)을 함께 싣는다(D-P6-12). 열 이름은 `ten_day_columns` 의 최신 이름.

데이터가 없으면 404 `no_data`(작업·예정 시각·막는 실측 번호). 관세청 수집은 실측(#6·#17·#55) 전
꺼져 있다. 누계가 줄어든 칸(정정)은 notes 경고 — 값은 그대로 싣는다.
"""

from __future__ import annotations

from typing import Final

from kbj.core.trade_rows import prev_ym
from kbj.engines.trade.flash import FlashSegment, monotonic_breaks, segments
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.trade import (
    TenDayKindT,
    TradeFlash,
    TradeFlashCell,
    TradeFlashSegment,
)
from kbj.services.api.readers._common import NoData, ReadContext, envelope, worst

__all__ = ["NO_FLASH", "SOURCE", "trade_flash"]

SOURCE: Final = "관세청(공공데이터포털)"
NO_FLASH: Final = "아직 없음 — trade.customs_tenday 1·11·21일(+2일) 09:00~17:40(확인 전 #6·#17·#55)"
TABLE: Final = "pub_trade.ten_day"
COLUMNS: Final = 11
NOTE_USD: Final = "금액은 미화 달러(원화 환산 없음 — 환율 미조정)"


def _segment(s: FlashSegment) -> TradeFlashSegment:
    return TradeFlashSegment(
        ym=s.ym,
        seg=s.seg,
        span_text=s.span_text,
        first_seen_at=s.first_seen_at,
        revised_at=s.revised_at,
        quality=s.quality,
        cells=[
            TradeFlashCell(
                col_no=c.col_no,
                name=c.name,
                usd=c.usd,
                first_usd=c.first_usd,
                yoy=c.yoy,
                yoy_reason=c.yoy_reason,
                contribution=c.contribution,
                mom=c.mom,
            )
            for c in s.cells
        ],
    )


def _columns(ctx: ReadContext, kind: str, segs: list[FlashSegment]) -> list[str]:
    """열 00~10 의 최신 이름 — `ten_day_columns`(valid_from 최대), 없으면 최신 구간 칸 이름."""
    names: dict[int, tuple[str, str]] = {}
    for c in ctx.repos.trade.ten_day_columns(kind):
        if c.col_no not in names or c.valid_from > names[c.col_no][0]:
            names[c.col_no] = (c.valid_from, c.name)
    latest = {c.col_no: c.name for c in segs[0].cells} if segs else {}
    out: list[str] = []
    for no in range(COLUMNS):
        n = names[no][1] if no in names else latest.get(no)
        if n is None:
            break
        out.append(n)
    return out


def trade_flash(ctx: ReadContext, *, kind: TenDayKindT, months: int) -> Envelope[TradeFlash]:
    """10일 잠정치 구간 표·YoY·열 이름·발표 당시/최신."""
    last = ctx.repos.trade.last_ym(TABLE)
    if last is None:
        raise NoData(NO_FLASH)
    rows = ctx.repos.trade.ten_day(kind=kind, ym_from=prev_ym(last, months - 1 + 12))
    segs = segments(rows, kind=kind, months=months)
    if not segs:
        raise NoData(NO_FLASH)
    breaks = [b for b in monotonic_breaks(rows) if b[1] >= segs[-1].ym]
    notes = [NOTE_USD, "관세청 잠정치 — 정정되면 최신 값으로 바뀐다(발표 당시 값은 first_usd)"]
    if breaks:
        notes.append(f"누계가 앞 구간보다 작은 칸 {len(breaks)}개(정정 반영 중) — 값은 그대로")
    revised = sum(1 for s in segs for c in s.cells if c.first_usd != c.usd)
    if revised:
        notes.append(f"발표 당시와 최신 값이 다른 칸 {revised}개")
    return envelope(
        TradeFlash(
            kind=kind,
            months=months,
            columns=_columns(ctx, kind, segs),
            segments=[_segment(s) for s in segs],
        ),
        source=SOURCE,
        as_of=max(s.as_of for s in segs),
        quality=worst(s.quality for s in segs),
        notes=notes,
        generated_at=ctx.now(),
    )
