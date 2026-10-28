"""장중 투자자 1분 reader — `/api/flows/market-minute`(P5 F2 — docs/p5_design.md §5, F2 §0-1·§3.5).

`prv_gex.investor_flow`(대금 **백만원** — GX 계약)를 읽어 **원**으로 바꾸고(D-P5-21 ②), 수집 범위
분 축(개장 ~ 마감 + 10분)에 맞춰 선을 낸다. 빈 분은 `None`(서버는 값을 지어내지 않는다 — 앞뒤를
잇는 것은 화면의 몫이고, 화면은 이은 구간을 점선으로 구분한다). 계산은 순수 엔진
`kbj.engines.flows.minute`(7카드·검산 실패 분 제외)가 한다. KIS TR 이 분 이력을 주지 않아(오늘 누적
한 줄) 못 받은 분은 뒤에서 채울 수 없다(`kbj.data.private.kis.investors_minute` 머리말).

- `market` = 탭 id(`config/markets.yaml flows.minute.pairs[].tab`). 모르는 탭은 404 `unknown_code`.
- 화면 상태(`status`):
  - `not_confirmed` — 그 탭의 조합이 꺼져 있다('확인 전'). 값을 지어내지 않는다.
  - `holiday` — 오늘 휴장. 직전 거래일 선을 보인다(잠정 아님).
  - `before_open` — 오늘 개장 전. 직전 거래일 선을 보인다(잠정 아님).
  - `ok` — 오늘 장중·마감 뒤(잠정). 오늘 행이 하나도 없으면 404 `no_data`(작업 이름·예정 시각 —
    P5 404 계약, 화면이 사용자 문장으로 다듬는다).
- 지수 겹침(`index`): 조합의 `index`(KIS 업종 코드)를 `prv_market.index_intraday`(장중 10분 —
  `market.intraday`)에서 읽어 같은 분 축에 놓는다. 값이 없거나 정하지 않은 탭이면 None + 메모 한 줄.
- 거래소: 이 TR 은 거래소 입력이 없다(F2 §1.3) → 응답 `venue` 는 언제나 `KRX`, `venue_locked`
  true. 다른 거래소를 물어도 KRX 값을 주고 메모로 알린다(통합 수치를 지어내지 않는다).
- 메모(`notes`)는 짧은 사용자 문장(작업 이름·실측 번호 없음).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Final, Literal

from kbj.config.markets import MinutePair
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.engines.flows.minute import MinuteBoard, minute_axis, minute_board
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.flows import MarketMinute, MinuteCard, MinuteIndex, MinuteLine
from kbj.services.api.readers._common import NoData, ReadContext, UnknownCode, day_start, envelope

__all__ = [
    "LOCKED_VENUE",
    "NOT_CONFIRMED",
    "NO_DATA",
    "REFRESH_S",
    "SOURCE",
    "VENUE_NOTE",
    "index_overlay",
    "market_minute",
    "pair_for_tab",
]

# 404 계약(작업·시각 — P5 라우트 공통, 화면이 사용자 문장으로 다듬는다)
NO_DATA: Final = "아직 없음 — flows.market_minute 09:00~15:40 매분(선물·옵션은 15:55 까지)"
SOURCE: Final = "KIS"
REFRESH_S: Final = 60
LOCKED_VENUE: Final = "KRX"
VENUE_NOTE: Final = "거래소 구분 없는 값(KRX 표시)"
NOT_CONFIRMED: Final = "확인 전 — 이 시장은 아직 받지 않는다"
Status = Literal["ok", "not_confirmed", "before_open", "holiday"]


def pair_for_tab(pairs: tuple[MinutePair, ...], tab: str) -> MinutePair | None:
    """그 탭의 주간 조합 — 켠 것(코드 있음)을 먼저, 없으면 첫 조합(꺼진 탭 '확인 전')."""
    mine = [p for p in pairs if p.tab == tab and p.session == "day"]
    if not mine:
        return None
    on = [p for p in mine if p.enabled and p.market is not None and p.sector is not None]
    return on[0] if on else mine[0]


def _empty(tab: str, day: date, status: Status) -> MarketMinute:
    return MarketMinute(
        market=tab, venue=LOCKED_VENUE, venue_locked=True, trade_date=day, axis=[], lines=[],
        cards=[], collected_at=None, refresh_s=REFRESH_S, provisional=False, status=status,
    )  # fmt: skip


def _model(tab: str, board: MinuteBoard, status: Status, index: MinuteIndex | None) -> MarketMinute:
    return MarketMinute(
        market=tab,
        venue=LOCKED_VENUE,
        venue_locked=True,
        trade_date=board.trade_date,
        axis=[ts.strftime("%H:%M") for ts in board.axis],
        lines=[
            MinuteLine(
                investor=ln.investor,
                label=ln.label,
                default_on=ln.default_on,
                values=list(ln.values),
            )
            for ln in board.lines
        ],
        cards=[
            MinuteCard(investor=c.investor, label=c.label, net_value=c.net_value, quality=c.quality)
            for c in board.cards
        ],
        collected_at=board.last_ts,
        refresh_s=REFRESH_S,
        provisional=status == "ok",
        status=status,
        index=index,
    )


def _board(ctx: ReadContext, pair: MinutePair, day: date) -> MinuteBoard:
    open_at, close_at = ctx.cal.equity_bounds(day)
    rows = ctx.repos.flows.investor_minutes(day, [(pair.market or "", pair.sector or "")])
    return minute_board(rows, minute_axis(open_at, close_at), day)


def index_overlay(
    ctx: ReadContext, code: str | None, day: date, axis: tuple[datetime, ...]
) -> MinuteIndex | None:
    """그날 장중 지수(10분 슬롯)를 분 축에 놓는다 — 슬롯 시작 분 자리에만 값(나머지 None). 값이
    하나도 없으면 None(지어내지 않는다)."""
    if code is None or not axis:
        return None
    start = day_start(day)
    quotes = [
        q
        for q in ctx.repos.market.index_quotes(start, start + timedelta(days=1))
        if q.code == code and q.value is not None
    ]
    if not quotes:
        return None
    pos = {ts.astimezone(KST).strftime("%H:%M"): i for i, ts in enumerate(axis)}
    values: list[float | None] = [None] * len(axis)
    for q in quotes:  # ts 순 — 같은 분이 둘이면 뒤의 것
        i = pos.get(q.ts.astimezone(KST).strftime("%H:%M"))
        if i is not None:
            values[i] = q.value
    last = quotes[-1]
    return MinuteIndex(
        code=code,
        label=ctx.markets.market.intraday_indices.get(code) or last.name or code,
        values=values,
        last=last.value,
        chg_pct=last.chg_pct,
        as_of=last.ts,
        quality=last.quality,
    )


def _notes(
    board: MinuteBoard, venue: str, pair: MinutePair, index: MinuteIndex | None
) -> list[str]:
    notes = [VENUE_NOTE]
    if venue != LOCKED_VENUE:
        notes.append(f"요청한 거래소 {venue} 값은 없다 — KRX 값을 보인다")
    if board.missing:
        notes.append(f"못 받은 {board.missing}분은 앞뒤를 점선으로 이었다")
    if board.invalid_slots:
        notes.append(f"합계가 맞지 않은 {board.invalid_slots}분은 뺐다(점선)")
    if board.quality == Quality.ESTIMATED:
        notes.append("늦게 받은 분이 섞여 있다(추정)")
    if index is None:
        notes.append(
            "지수 선 없음" if pair.index is None else "지수 선 없음 — 장중 지수 값이 아직 없다"
        )
    return notes


def market_minute(ctx: ReadContext, *, market: str, venue: str) -> Envelope[MarketMinute]:
    pair = pair_for_tab(ctx.markets.flows.minute.pairs, market)
    if pair is None:
        raise UnknownCode()
    now = ctx.now_kst()
    today = now.date()
    if not pair.enabled or pair.market is None or pair.sector is None:
        return envelope(
            _empty(market, today, "not_confirmed"),
            source=SOURCE, as_of=now, quality=None, notes=[NOT_CONFIRMED], generated_at=now,
        )  # fmt: skip
    cal = ctx.cal
    status: Status = "ok"
    day = today
    if not cal.is_trading_day(today):
        status, day = "holiday", cal.prev_trading_day(today)
    elif now < cal.equity_bounds(today)[0]:
        status, day = "before_open", cal.prev_trading_day(today)
    board = _board(ctx, pair, day)
    if status == "ok" and board.last_ts is None:
        raise NoData(NO_DATA)
    index = index_overlay(ctx, pair.index, day, board.axis)
    return envelope(
        _model(market, board, status, index),
        source=SOURCE,
        as_of=board.last_ts or now,
        quality=board.quality,
        notes=_notes(board, venue, pair, index),
        generated_at=now,
    )
