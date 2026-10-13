"""장중 투자자 1분 reader — `/api/flows/market-minute`(P5 F2 — docs/p5_design.md §5, F2 §0-1·§3.5).

`prv_gex.investor_flow`(대금 **백만원** — GX 계약)를 읽어 **원**으로 바꾸고(D-P5-21 ②), 수집 범위
분 축(개장 ~ 마감 + 10분)에 맞춰 선을 낸다. 빈 분은 `None`(잇지 않는다 — 보간 금지). 계산은 순수
엔진 `kbj.engines.flows.minute`(7카드·검산 실패 분 제외)가 한다.

- `market` = 탭 id(`config/markets.yaml flows.minute.pairs[].tab`). 모르는 탭은 404 `unknown_code`.
- 화면 상태(`status`):
  - `not_confirmed` — 그 탭의 조합이 꺼져 있다(코드 [확인 필요 #39] — '확인 전'). 값을 지어내지
    않는다.
  - `holiday` — 오늘 휴장. 직전 거래일 선을 보인다(잠정 아님).
  - `before_open` — 오늘 개장 전. 직전 거래일 선을 보인다(잠정 아님).
  - `ok` — 오늘 장중·마감 뒤(잠정). 오늘 행이 하나도 없으면 404 `no_data`(작업 이름·예정 시각).
- 거래소: 이 TR 은 거래소 입력이 없다(F2 §1.3) → 응답 `venue` 는 언제나 `KRX`, `venue_locked`
  true. 다른 거래소를 물어도 KRX 값을 주고 메모로 알린다(통합 수치를 지어내지 않는다).
- 품질: 제때 받은 분 `ok`, 늦게 받은 분 `estimated`, 검산 실패 분은 빼고 수를 메모로. 머리 꼬리표
  `수집 HH:MM · 60초 갱신 · 잠정`(`collected_at`·`refresh_s`·`provisional`).
"""

from __future__ import annotations

from datetime import date
from typing import Final, Literal

from kbj.config.markets import MinutePair
from kbj.core.quality import Quality
from kbj.engines.flows.minute import MinuteBoard, minute_axis, minute_board
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.flows import MarketMinute, MinuteCard, MinuteLine
from kbj.services.api.readers._common import NoData, ReadContext, UnknownCode, envelope

__all__ = [
    "LOCKED_VENUE",
    "NO_DATA",
    "REFRESH_S",
    "SOURCE",
    "VENUE_NOTE",
    "market_minute",
    "pair_for_tab",
]

NO_DATA: Final = "아직 없음 — flows.market_minute 09:00~15:40 매분"
SOURCE: Final = "KIS"
REFRESH_S: Final = 60
LOCKED_VENUE: Final = "KRX"
VENUE_NOTE: Final = "분 단위 선은 거래소 입력이 없는 TR — KIS 기본값 하나(KRX 표시, 통합 아님)"
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


def _model(tab: str, board: MinuteBoard, status: Status) -> MarketMinute:
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
    )


def _board(ctx: ReadContext, pair: MinutePair, day: date) -> MinuteBoard:
    open_at, close_at = ctx.cal.equity_bounds(day)
    rows = ctx.repos.flows.investor_minutes(day, [(pair.market or "", pair.sector or "")])
    return minute_board(rows, minute_axis(open_at, close_at), day)


def _notes(board: MinuteBoard, venue: str) -> list[str]:
    notes = [VENUE_NOTE]
    if venue != LOCKED_VENUE:
        notes.append(f"요청한 거래소 {venue} 값은 없다 — KRX 값을 보인다")
    if board.missing:
        notes.append(f"빈 분 {board.missing}개 — 수집 실패(잇지 않는다, 뒤에서 채울 수 없다)")
    if board.invalid_slots:
        notes.append(f"검산 실패 {board.invalid_slots}분 제외")
    if board.quality == Quality.ESTIMATED:
        notes.append("슬롯 + 45초 넘어 받은 분이 있다(그 분보다 뒤의 누적 — 추정)")
    return notes


def market_minute(ctx: ReadContext, *, market: str, venue: str) -> Envelope[MarketMinute]:
    pair = pair_for_tab(ctx.markets.flows.minute.pairs, market)
    if pair is None:
        raise UnknownCode()
    now = ctx.now_kst()
    today = now.date()
    if not pair.enabled or pair.market is None or pair.sector is None:
        note = f"확인 전 — {pair.label} 조합 코드 [확인 필요 #39]"
        return envelope(
            _empty(market, today, "not_confirmed"),
            source=SOURCE, as_of=now, quality=None, notes=[note, VENUE_NOTE], generated_at=now,
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
    return envelope(
        _model(market, board, status),
        source=SOURCE,
        as_of=board.last_ts or now,
        quality=board.quality,
        notes=_notes(board, venue),
        generated_at=now,
    )
