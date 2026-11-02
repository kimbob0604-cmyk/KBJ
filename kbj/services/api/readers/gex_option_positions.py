"""옵션 행사가별 투자자 추정 포지션 리더 — `/api/gex/option-positions`(metrics §34, ADR 0044).

로그인 전용. `ctx.repos.opt_pos`(DB 만 — 계약 ⑩)의 저장 행(작업 `gex.option_positions` 하루
배분)과 KRX 확정 OI 를 순수 엔진 `kbj.engines.gex.option_positions.build_positions` 에
넣는다 — 누적·OI 검산·벽·라벨·합성 델타·레벨 표는 엔진이 계산하고 여기서는 모양만 맞춘다
(절대 규칙 3).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Final

from kbj.core.quality import Quality
from kbj.engines.gex.option_positions import (
    ADD_PCT,
    CLEAR_PCT,
    INVESTORS,
    MIN_JUDGE,
    NEAR_SHARE,
    OPTION_MULTIPLIER,
    PRICE_STALE,
    REDUCE_PCT,
    DayStat,
    SeriesBoard,
    StrikeDay,
    Wall,
    build_positions,
)
from kbj.engines.gex.series import series_label
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.gex_optpos import (
    OptionPositions,
    OptPosDayStat,
    OptPosEwy,
    OptPosLevel,
    OptPosSeries,
    OptPosSeriesDelta,
    OptPosStrike,
    OptPosSynthetic,
    OptPosThresholds,
    OptPosTop,
    OptPosWall,
)
from kbj.services.api.readers._common import NoData, ReadContext, envelope, worst

__all__ = ["LOOKBACK", "NO_DATA", "SOURCE", "option_positions"]

NO_DATA: Final = "아직 없음 — gex.option_positions(평일 16:05 — 파생 마감 수집 15:55 뒤)"
SOURCE: Final = (
    "KBJ:option_positions(KIS 투자자별 합계+옵션 체인 — 키움 방식 추정)+KRX:opt_bydd_trd(OI 검산)"
)
LOOKBACK: Final = timedelta(days=200)  # 누적 시작일을 찾는 범위(분기물 수명보다 넉넉히)
EWY_REASON: Final = "EWY 원천 없음 — 보류 [확인 필요](metrics §34.6)"
LIMIT_NOTE: Final = (
    "추정치(키움 방식 — 1분 해상도, 누락 분은 다음 분 시세, 월물 전 만기 합을 근월물로 배분)"
)


def _label(cls: str, expiry: str) -> str:
    return series_label(("WKM" if cls == "WKM" else "WKI" if cls == "WKI" else "", expiry))


def _wall(w: Wall | None) -> OptPosWall | None:
    return None if w is None else OptPosWall(strike=w.strike, holding=w.holding, label=w.label)


def _series(b: SeriesBoard) -> OptPosSeries:
    ok = [s.quality for s in b.strikes if s.quality is not Quality.INVALID]
    return OptPosSeries.model_validate(
        {
            "mrkt_cls": b.mrkt_cls, "expiry": b.expiry, "label": _label(b.mrkt_cls, b.expiry),
            "last_trade_date": b.last_trade_date, "accum_from": b.accum_from,
            "days_used": b.days_used, "days_missing": b.days_missing,
            "strikes": [
                OptPosStrike(
                    strike=s.strike, cp=s.cp, holding=s.holding, prev_holding=s.prev_holding,
                    change=s.change, buy=s.buy, sell=s.sell, oi=s.oi, oi_source=s.oi_source,
                    oi_ratio=s.oi_ratio, delta=s.delta, label=s.label, quality=s.quality,
                    reason=s.reason or "",
                )
                for s in b.strikes
            ],
            "call_wall": _wall(b.call_wall), "put_wall": _wall(b.put_wall),
            "prev_call_wall": _wall(b.prev_call_wall), "prev_put_wall": _wall(b.prev_put_wall),
            "delta_contracts": b.delta_contracts, "missing_delta": b.missing_delta,
            "invalid": b.invalid, "quality": worst(ok) or Quality.INVALID,
            "reasons": list(b.reasons),
        }
    )  # fmt: skip


def option_positions(
    ctx: ReadContext, *, investor: str = "frgn", day: date | None = None
) -> Envelope[OptionPositions]:
    if investor not in INVESTORS:
        raise ValueError(f"investor 는 {'·'.join(INVESTORS)} 중 하나: {investor!r}")
    now = ctx.now()
    repo = ctx.repos.opt_pos
    d = repo.latest_day(investor, day or ctx.today())
    if d is None:
        raise NoData(NO_DATA)
    first = d - LOOKBACK
    srows = repo.strike_rows(investor, first, d)
    drows = repo.day_rows(investor, first, d)
    rows = [
        StrikeDay(
            r.trade_date,
            r.mrkt_cls,
            r.expiry,
            r.strike,
            "C" if r.cp == "C" else "P",
            r.buy_qty,
            r.sell_qty,
            r.close_price,
            r.board_oi,
            r.delta,
        )
        for r in srows
    ]
    stats = [
        DayStat(
            r.trade_date,
            r.market_code,
            r.sector_code,
            r.minutes,
            r.gap_minutes,
            r.unallocated_buy + r.unallocated_sell,
            r.reasons,
            r.futures_net,
            r.fut_price,
            r.last_ts,
        )
        for r in drows
    ]
    krx = repo.krx_oi(d)
    b = build_positions(rows, stats, day=d, investor=investor, krx_oi=krx, cal=ctx.cal)
    series = [_series(s) for s in b.series]
    syn_q = Quality.ESTIMATED if b.synthetic.total is not None else Quality.INVALID
    data = OptionPositions.model_validate(
        {
            "trade_date": d, "investor": investor, "series": series,
            "synthetic": OptPosSynthetic(
                futures=b.synthetic.futures, futures_from=b.synthetic.futures_from,
                options=b.synthetic.options, total=b.synthetic.total,
                by_series=[OptPosSeriesDelta(series=_label(c, e), delta=v)
                           for c, e, v in b.synthetic.by_series],
                missing_delta=b.synthetic.missing_delta, quality=syn_q,
            ),
            "top_changes": [
                OptPosTop(series=_label(t.mrkt_cls, t.expiry), strike=t.strike, cp=t.cp,
                          change=t.change, holding=t.holding, prev_holding=t.prev_holding,
                          label=t.label)
                for t in b.top_changes
            ],
            "levels": [OptPosLevel(kind=lv.kind, level=lv.level, distance_pct=lv.distance_pct,
                                   ewy=lv.ewy) for lv in b.levels],
            "futures_price": b.futures_price, "futures_today": b.futures_today,
            "day_stats": [
                OptPosDayStat(market_code=s.market_code, sector_code=s.sector_code,
                              minutes=s.minutes, gap_minutes=s.gap_minutes,
                              unallocated=round(s.unallocated, 4), last_ts=s.last_ts,
                              reasons=list(s.reasons))
                for s in stats if s.trade_date == d
            ],
            "ewy": OptPosEwy(status="unavailable", reason=EWY_REASON),
            "thresholds": OptPosThresholds(
                min_judge=MIN_JUDGE, clear_pct=CLEAR_PCT, reduce_pct=REDUCE_PCT, add_pct=ADD_PCT,
                price_stale_min=int(PRICE_STALE.total_seconds() // 60),
                near_share=round(NEAR_SHARE, 6), option_multiplier=OPTION_MULTIPLIER,
            ),
            "reasons": list(b.reasons),
        }
    )  # fmt: skip
    stamps = [r.computed_at for r in drows if r.trade_date == d and r.computed_at is not None]
    invalid = sum(s.invalid for s in series)
    quals = [s.quality for s in series if s.quality is not Quality.INVALID]
    notes = [
        LIMIT_NOTE,
        f"OI 검산 실패 {invalid}행(invalid — 벽·합성에서 뺌)" if invalid else "",
        ""
        if krx
        else f"{d.isoformat()} KRX 확정 OI 전 — 전광판 OI 로 검산(다음 거래일 08:05 뒤 확정)",
        *(f"{s.label} 누적 시작 {s.accum_from.isoformat()}" for s in series),
    ]
    return envelope(
        data, source=SOURCE, as_of=max(stamps) if stamps else now,
        quality=worst(quals) if quals else (Quality.ESTIMATED if not series else Quality.INVALID),
        notes=notes, generated_at=now,
    )  # fmt: skip
