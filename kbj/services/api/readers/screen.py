"""종목 스크리너 reader — `/api/screen/cis`(페이지 11 '스크리너' 패널).

- 기준일: 요청한 날(없으면 오늘) 이하 일봉이 있는 마지막 거래일 — 장중엔 전 거래일 종가 기준이다.
- 입력: 시세 저장소의 전 종목 일봉(직전 `lookback_days` + 그날), 그날 스냅(이름·시장·종류·상태 —
  없으면 유니버스), 지수 일봉(`regime_ma` 개). 계산은 엔진(`kbj.engines.screen.cis`).
- 값은 저장 그대로(KRX·KIS 일봉) — 로그인 전용(공개 파일 없음).
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Final

from kbj.config.screen import CisCfg, load_screen
from kbj.core.time import KST
from kbj.engines.screen.cis import CisRow, StockMeta, screen_cis
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.screen import CisParams, CisRegime, CisRowOut, ScreenCis
from kbj.services.api.readers._common import NoData, ReadContext, envelope, label, worst

__all__ = ["NO_BARS", "screen_cis_read"]

NO_BARS: Final = "아직 없음 — krx.daily 08:05 · market.close_collect 15:35(일봉)"
_CLOSE: Final = time(15, 30)


def _meta(ctx: ReadContext, day: date) -> dict[str, StockMeta]:
    snaps, _ = ctx.repos.market.snapshot(day)
    out = {
        c: StockMeta(name=s.name, market=s.market, kind=s.kind, flagged=s.flagged)
        for c, s in snaps.items()
    }
    for u in ctx.repos.market.universe(day):
        if u.code not in out:
            out[u.code] = StockMeta(name=u.name, market=u.market, kind=u.kind, flagged=None)
    return out


def _params(cfg: CisCfg) -> CisParams:
    p = cfg.plan
    return CisParams(
        lookback_days=cfg.lookback_days, vol_mult=cfg.vol_mult,
        min_turnover_won=cfg.min_turnover_won, min_avg_turnover_won=cfg.min_avg_turnover_won,
        min_price=cfg.min_price, close_pos_min=cfg.close_pos_min, limit_pct=cfg.limit_pct,
        regime_ma=cfg.regime_ma, stop_pct=p.stop_pct, trail_pct=p.trail_pct,
        gap_skip_pct=p.gap_skip_pct, add_at_pct=p.add_at_pct, risk_pct=p.risk_pct,
        max_positions=p.max_positions,
    )  # fmt: skip


def _row(r: CisRow) -> CisRowOut:
    return CisRowOut(
        code=r.code, name=r.name, market=r.market, close=r.close,  # pyright: ignore[reportArgumentType]
        chg_pct=round(r.chg_pct, 2), volume=r.volume, vol_ratio=round(r.vol_ratio, 2),
        turnover=r.turnover, avg_turnover=round(r.avg_turnover), box_high=r.box_high,
        breakout=r.breakout, close_pos=round(r.close_pos, 3), limit_up=r.limit_up,
        regime_on=r.regime_on, passed=r.passed, why_not=list(r.why_not),
        stop=round(r.stop), gap_skip=round(r.gap_skip),
    )  # fmt: skip


def screen_cis_read(
    ctx: ReadContext, *, day: date | None = None, cfg: CisCfg | None = None
) -> Envelope[ScreenCis]:
    c = cfg if cfg is not None else load_screen().cis
    end = day if day is not None else ctx.now_kst().date()
    days = ctx.repos.market.trading_days(end, 1)
    if not days:
        raise NoData(NO_BARS)
    as_of = days[0]
    bars = ctx.repos.market.series(None, as_of, c.lookback_days + 1)
    codes = sorted({x for v in c.index.values() for x in v})
    idx = ctx.repos.market.index_series(codes, as_of, c.regime_ma)
    res = screen_cis(bars, _meta(ctx, as_of), idx, c, as_of)
    today = [b for v in bars.values() for b in v if b.date == as_of]
    notes = [f"{as_of:%m/%d} 종가 기준 — 다음 거래일 계획(매매 권유 아님)"]
    if res.status_unknown:
        notes.append(f"관리·정지 상태를 모르는 종목 {res.status_unknown}개 포함(스냅 없음)")
    if any(g.on is None for g in res.regimes):
        notes.append("지수 일봉이 모자라 시장 국면을 모르는 시장이 있다(통과를 막지 않는다)")
    data = ScreenCis(
        date=as_of,
        scanned=res.scanned,
        spikes=res.spikes,
        passed=res.passed,
        excluded=dict(res.excluded),
        status_unknown=res.status_unknown,
        regimes=[
            CisRegime(market=g.market, index_code=g.index_code, close=g.close, ma=g.ma, on=g.on)  # pyright: ignore[reportArgumentType]
            for g in res.regimes
        ],
        params=_params(c),
        rows=[_row(r) for r in res.rows],
    )
    return envelope(
        data,
        source=label(b.source for b in today),
        as_of=datetime.combine(as_of, _CLOSE, tzinfo=KST),
        quality=worst(b.quality for b in today),
        notes=notes,
        generated_at=ctx.now(),
    )
