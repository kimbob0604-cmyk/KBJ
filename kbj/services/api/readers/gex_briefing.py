"""`/api/gex/briefing` — 감마 국면·머리 문장·레벨표·행사가별 콜/풋/순 GEX·옵션 포지셔닝(로그인).

사용자 요청(2026-10-21): 행사가별 막대 + '무슨 뜻인지' + 원문 옵션 전략(양매수·양매수+외가 매도·
숏 스트랭글·합성 매수)의 손익분기를 장중 사이클마다. 계산은 `kbj.engines.gex.briefing`(순수 함수 —
문장은 규칙 템플릿, LLM 아님). 이 리더는 저장소에서 꺼내 엔진에 넘기고 봉투를 씌운다.

- 행사가 막대는 `/api/gex/profile` 과 같은 사이클·같은 범위(그 응답을 그대로 쓴다). 콜월·풋월은
  그 막대(만기 합산)에서 현재가 위 콜 감마 최대·아래 풋 감마 최대 — 화면 막대와 어긋나지 않게
  (실데이터 10/21: 엔진 풋월 950·850 이 막대의 880 과 달랐다). 엔진과 다르면 notes 에 적는다.
- 1일 예상폭은 ATM IV × √(1/252)(엔진 값은 남은 장 시간 기준이라 마감 뒤 0 에 가깝다 — IV 가 없을
  때만 쓴다).
- '평소 대비': 최근 `AVG_DAYS` 세션의 세션 마지막 순GEX 절댓값 평균(오늘 제외).
- 전략: 그 사이클 옵션 체인에서 오늘 이후 가장 가까운 만기 하나. 가격은 호가 중간, 없으면 현재가.
  외가 행사가는 만기까지 ±1σ(ATM IV × √T, KIS IV — 없으면 ±5%). 호가 폭이 넓으면 현재가를 쓰고,
  패리티로 본 선도가 튀는 행사가는 뺀다(엔진).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Final

from kbj.core.calendar import State, state_at
from kbj.core.gex.gex import OPTION_MULTIPLIER
from kbj.core.gex.records import ChainRecord
from kbj.engines.gex.briefing import (
    ChainQuote,
    StrikeBar,
    briefing,
    one_day_move,
    sigma_to_expiry,
    strategies,
    strike_table,
    table_walls,
)
from kbj.services.api.models.common import Envelope
from kbj.services.api.models.gex import (
    GexBriefing,
    GexKeyLevel,
    GexLeg,
    GexScopeT,
    GexStrategy,
    GexStrikeNet,
)
from kbj.services.api.readers._common import ReadContext, envelope
from kbj.services.api.readers.gex import SOURCE, gex_profile

__all__ = ["AVG_DAYS", "NEAR_STRIKES", "expiry_label", "gex_briefing"]

AVG_DAYS: Final = 20
NEAR_STRIKES: Final = 21  # 행사가 표 — 현재가에 가까운 순
MIN_PAIRS: Final = 3  # 콜·풋 가격이 다 있는 행사가가 이보다 적은 만기는 전략에 쓰지 않는다
SPREAD_ABS: Final = 0.5  # pt — 호가 폭이 이것과 중간값×SPREAD_REL 중 큰 값을 넘으면 현재가를 쓴다
SPREAD_REL: Final = 0.3
CLS_LABEL: Final = {"": "월물", "M": "월물", "WKM": "위클리(월)", "WKI": "위클리(목)"}
WALL_NOTE: Final = "벽은 행사가 막대(만기 합산) 기준 — 'GEX 레벨' 패널(엔진 범위 기준)과 다름: "
STRATEGY_NOTE: Final = (
    "옵션 포지셔닝은 만기 손익(pt × 승수, 수수료·증거금·세금 제외) — 매매 권유 아님"
)


def _mid(r: ChainRecord) -> float | None:
    """호가 중간(폭이 좁을 때만) — 아니면 현재가."""
    last = float(r.last) if r.last is not None and r.last > 0 else None
    if r.bid is not None and r.ask is not None and r.bid > 0 and r.ask >= r.bid:
        mid = float((r.bid + r.ask) / 2)
        if float(r.ask - r.bid) <= max(SPREAD_ABS, SPREAD_REL * mid) or last is None:
            return mid
    return last


def expiry_label(cls: str, d: date) -> str:
    """'WKI' + 2026-10-22 → '위클리(목) 10/22'."""
    return f"{CLS_LABEL.get(cls, cls)} {d.month}/{d.day}"


def _pick_expiry(
    chain: list[ChainRecord], dates: dict[tuple[str, str], date], today: date
) -> tuple[str, str, date] | None:
    """오늘 이후 가장 가까운 만기(콜·풋 가격이 함께 있는 행사가가 MIN_PAIRS 이상)."""
    have: dict[tuple[str, str], dict[tuple[Decimal, str], float]] = defaultdict(dict)
    for r in chain:
        p = _mid(r)
        if p is not None:
            have[(r.mrkt_cls, r.expiry)][(r.strike, r.cp)] = p
    ok: list[tuple[date, str, str]] = []
    for (cls, exp), px in have.items():
        d = dates.get((cls, exp))
        pairs = {k for (k, cp) in px if cp == "C" and (k, "P") in px}
        if d is not None and d >= today and len(pairs) >= MIN_PAIRS:
            ok.append((d, cls, exp))
    if not ok:
        return None
    d, cls, exp = min(ok)
    return cls, exp, d


def _atm_iv(chain: list[ChainRecord], price: float) -> float | None:
    ivs = [(abs(float(r.strike) - price), float(r.iv_kis) / 100) for r in chain
           if r.iv_kis is not None and r.iv_kis > 0]  # fmt: skip
    if not ivs:
        return None
    near = min(d for d, _ in ivs)
    vals = [v for d, v in ivs if d == near]
    return sum(vals) / len(vals)


def _recent_net(ctx: ReadContext, scope: GexScopeT, today: date) -> list[float]:
    end = ctx.now()
    rows = ctx.repos.gex.metrics_series("net_gex", end - timedelta(days=AVG_DAYS * 2), end,
                                        scope=scope)  # fmt: skip
    last: dict[date, float] = {}
    for m in rows:  # ts 순 — 날마다 마지막 값
        if m.trade_date != today and m.value is not None and m.flag == "visible":
            last[m.trade_date] = m.value
    return [last[d] for d in sorted(last)[-AVG_DAYS:]]


def _pt(v: float) -> str:
    return f"{v:,.1f}" if v % 1 else f"{v:,.0f}"


def gex_briefing(ctx: ReadContext, *, scope: GexScopeT) -> Envelope[GexBriefing]:
    prof = gex_profile(ctx, scope=scope)
    p = prof.data
    notes = list(prof.notes)
    today = ctx.now_kst().date()
    rows = strike_table(StrikeBar(b.strike, b.gex_call, b.gex_put) for b in p.bars)
    head = ctx.repos.gex.levels_latest(scope, upto=ctx.now())[0]
    chain = ctx.repos.gex.chain_latest(head.trade_date, head.session, head.ts)
    dates = {(c, e): d for c, e, d, _src in ctx.repos.gex.expiry_dates(today - timedelta(days=7))}
    pick = _pick_expiry(chain, dates, today) if p.price is not None else None
    ch = [r for r in chain if pick is not None and (r.mrkt_cls, r.expiry) == pick[:2]]
    iv = _atm_iv(ch or chain, p.price) if p.price is not None else None
    em = one_day_move(p.price, iv) if p.price is not None else None
    if em is None:
        em = p.expected_move
        if em is not None and p.price and em < p.price * 0.001:
            em = None
            notes.append("ATM IV 없음·장 마감 — 1일 예상폭을 만들지 않았다")
    regime = regime_label = headline = None
    net = vs = None
    levels: list[GexKeyLevel] = []
    if p.price is not None and rows:
        # 벽은 화면 막대(행사가 표)와 같은 기준 — 현재가 위 콜 감마 최대·아래 풋 감마 최대
        cw, pw = table_walls(rows, p.price)
        moved = [f"{name} {_pt(a)}→{_pt(b)}" for name, a, b in
                 (("콜월", p.call_wall, cw), ("풋월", p.put_wall, pw))
                 if a is not None and b is not None and a != b]  # fmt: skip
        if moved:
            notes.append(WALL_NOTE + ", ".join(moved))
        b = briefing(rows, price=p.price, flip=p.flip, call_wall=cw, put_wall=pw,
                     expected_move=em, recent_net=_recent_net(ctx, scope, today))  # fmt: skip
        regime, regime_label, headline = b.regime, b.regime_label, b.headline
        net, vs = b.net_gex, b.net_vs_avg
        levels = [GexKeyLevel(kind=x.kind, label=x.label, value=x.value, dist_pt=x.dist_pt,
                              dist_pct=x.dist_pct, lines=list(x.lines), strength=x.strength,
                              gex=x.gex) for x in b.levels]  # fmt: skip
    elif p.price is None:
        notes.append("현재가가 없어 레벨 해설을 만들지 않았다")
    px = p.price
    near = sorted(rows, key=lambda r: abs(r.strike - px))[:NEAR_STRIKES] if px is not None else rows
    strikes = [GexStrikeNet(strike=r.strike, call=r.call, put=r.put, net=r.net)
               for r in sorted(near, key=lambda r: -r.strike)]  # fmt: skip

    strats: list[GexStrategy] = []
    expiry = expiry_date = days = None
    if pick is not None and p.price is not None:
        cls, _exp, expiry_date = pick
        expiry = expiry_label(cls, expiry_date)
        days = (expiry_date - today).days
        quotes = [ChainQuote(float(r.strike), r.cp, m) for r in ch if (m := _mid(r)) is not None]
        sig = sigma_to_expiry(p.price, iv, max(days, 1) / 365)
        if sig is None:
            notes.append("ATM IV 없음 — 외가 행사가는 현재가 ±5% 로 골랐다")
        strats = [
            GexStrategy(
                key=s.key, name=s.name, idea=s.idea, net_premium=s.net_premium,
                legs=[GexLeg(side=g.side, cp=g.cp, strike=g.strike, price=g.price)
                      for g in s.legs],
                breakevens=list(s.breakevens), max_profit=s.max_profit, max_loss=s.max_loss,
                scenario=[(a, b) for a, b in s.scenario], note=s.note,
            )
            for s in strategies(quotes, price=p.price, sigma_to_expiry=sig)
        ]  # fmt: skip
        notes.append(STRATEGY_NOTE)
    else:
        notes.append("옵션 포지셔닝 없음 — 오늘 이후 만기의 콜·풋 가격이 부족하다")
    data = GexBriefing(
        scope=scope, cycle_at=p.cycle_at,
        session_open=state_at(ctx.now_kst(), ctx.cal).state in (State.DAY, State.NIGHT),
        price=p.price, regime=regime, regime_label=regime_label, net_gex=net, net_vs_avg=vs,
        headline=headline, expected_move=em, levels=levels, strikes=strikes,
        expiry=expiry, expiry_date=expiry_date, days_to_expiry=days,
        multiplier=OPTION_MULTIPLIER, strategies=strats,
    )  # fmt: skip
    return envelope(
        data, source=SOURCE, as_of=prof.as_of, quality=prof.quality, notes=notes,
        generated_at=ctx.now(),
    )  # fmt: skip
