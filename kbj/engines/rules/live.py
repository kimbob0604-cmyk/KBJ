"""실시간 평가 도구 — 슬롯 시세 → 부분 봉, 평가 대상 고르기·자르기, 신호 키(P8 묶음 EVL,
docs/p8_design.md D-P8-9·20, §4.3, metrics §30).

순수 계산(계약 ⑨ — 저장소·어댑터를 모른다). 실시간 작업(`kbj.services.engine.rules`)과 장중 재생
백테스트(BKT)가 같은 함수를 부른다:

- `slot_from_quote(snap, bar, slot_ts=…)`: KIS 현재가(FHKST01010100 — 파서는 어댑터 쪽 `quotes.
  parse_quote`·`bar_from_quote`) → `QuoteSlot`(시가·지금까지 고가·저가·현재가·누적 거래량, 장중이라
  quality=estimated). 상태 칸(관리·정지·정리매매)과 등락률로 본 상·하한가(`upper_limit`·
  `lower_limit` — 문턱 `LIMIT_PCT` [확인 필요 — 가격제한폭 30%, 끝자리 호가 반올림])를 `flags` 에.
- `universe_codes(rule, …)`: 규칙의 우니버스 → 종목코드(순서 유지). `market` 우니버스는 마감 전용.
- `select_targets(...)`: 장중 평가 대상 = 장중 규칙 우니버스의 합집합, 상한 `cap`(config/rules.yaml
  `intraday_max_codes`). 넘으면 (규칙 우선순위 — 1 이 먼저, 우니버스 안 순서 —
  관심종목 순서)로 자르고
  잘린 종목을 돌려준다(근거·health 에 '잘림').
- `holdings(trades, upto)`: 매매 기록의 순수량 > 0 인 종목과 보유 시작일(마지막으로 0 에서 양이 된
  날 — `trail_from_peak` 의 시작점). 평균 단가·손익은 저널 엔진(JNL)의 몫.
- `close_eval_ts(d)`: 마감 신호의 평가 시각(그날 15:30 KST — 종가 시각). 16:00
  발송·20:30 캐치업 어느
  때 돌아도 같은 키(멱등, 실시간·백테스트 같은 쿨다운 시각).
- `signal_id(mode, ts, …)`: `si_`/`sc_` + ULID(시각 48비트 + 키의 sha256 80비트 — 결정적). 같은
  (규칙·판·종목·평가 시각)은 같은 id(저장소도 처음 id 를 지킨다).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Final

from kbj.core.quality import Quality
from kbj.core.rows import Bar, Snap
from kbj.core.rules_rows import QuoteSlot, SignalMode, TradeRow, signal_id_prefix
from kbj.core.time import KST
from kbj.engines.rules.dsl import Rule, UCodes, UMarket, UPortfolio, UWatchlist

__all__ = [
    "CLOSE_EVAL_TIME",
    "LIMIT_PCT",
    "SLOT_SOURCE",
    "Targets",
    "close_eval_ts",
    "holdings",
    "limit_flags",
    "select_targets",
    "signal_id",
    "slot_from_quote",
    "universe_codes",
]

SLOT_SOURCE: Final = "KIS:watch_quotes_intraday"
# 가격제한폭 ±30% — 호가 단위 반올림으로 29.x% 에서 닿는다 [확인 필요 — 실측 #65]
LIMIT_PCT: Final = 29.5
CLOSE_EVAL_TIME: Final = time(15, 30)
_CROCKFORD: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


# ── 슬롯 시세 → 부분 봉 ──────────────────────────────────────────────────────────────────────


def limit_flags(change_pct: float | None) -> tuple[str, ...]:
    """등락률로 본 상·하한가 플래그."""
    if change_pct is None:
        return ()
    if change_pct >= LIMIT_PCT:
        return ("upper_limit",)
    if change_pct <= -LIMIT_PCT:
        return ("lower_limit",)
    return ()


def slot_from_quote(
    snap: Snap,
    bar: Bar,
    *,
    slot_ts: datetime,
    received_at: datetime | None = None,
    source: str = SLOT_SOURCE,
) -> QuoteSlot:
    """같은 FHKST01010100 응답의 스냅(현재가·등락률·누적 거래량·상태)과 일봉(시가·고가·저가) →
    그 슬롯의 부분 봉. 장중 값이라 quality 는 estimated(ok 로 올리지 않는다)."""
    if snap.code != bar.code:
        raise ValueError("스냅과 일봉의 종목이 다르다")
    flags: list[str] = list(snap.status_flags or ())
    flags.extend(f for f in limit_flags(snap.chg_pct) if f not in flags)
    q = Quality.ESTIMATED if snap.quality is Quality.OK else snap.quality
    return QuoteSlot(
        slot_ts=slot_ts,
        code=snap.code,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        price=snap.close,
        volume=snap.volume,
        turnover=snap.turnover,
        change_pct=snap.chg_pct,
        flags=tuple(flags),
        quality=q,
        source=source,
        received_at=received_at,
    )


# ── 우니버스·대상 ─────────────────────────────────────────────────────────────────────────────


def _uniq(codes: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def universe_codes(
    rule: Rule,
    *,
    watchlists: Mapping[str, Sequence[str]],
    holdings: Sequence[str],
    market: Mapping[str, Sequence[str]] | None = None,
) -> list[str]:
    """규칙 우니버스 → 종목코드(순서 유지, 중복 없음). 없는 관심 목록은 빈 목록.

    `market` 은 {"KOSPI": [...], "KOSDAQ": [...]} — 마감 평가만 넘긴다(장중은 market 우니버스를
    DSL 이 거절한다). 넘기지 않았는데 market 우니버스면 ValueError(지어내지 않는다)."""
    u = rule.universe
    if isinstance(u, UCodes):
        return _uniq(u.codes)
    if isinstance(u, UWatchlist):
        return _uniq(watchlists.get(u.watchlist, ()))
    if isinstance(u, UPortfolio):
        return _uniq(holdings)
    if isinstance(u, UMarket):
        if market is None:
            raise ValueError("시장 우니버스는 마감 평가만(종목 목록이 주어지지 않았다)")
        if u.market == "ALL":
            return _uniq([*market.get("KOSPI", ()), *market.get("KOSDAQ", ())])
        return _uniq(market.get(u.market, ()))
    raise TypeError(f"모르는 우니버스: {type(u).__name__}")  # pragma: no cover


@dataclass(frozen=True)
class Targets:
    codes: tuple[str, ...]  # 평가할 종목(우선순위 순)
    truncated: tuple[str, ...]  # 상한으로 잘린 종목


def select_targets(entries: Sequence[tuple[int, Sequence[str]]], cap: int) -> Targets:
    """`entries` = [(규칙 우선순위 1~3, 그 규칙의 우니버스 코드 — 순서 있음)]. 종목마다 (가장 높은
    우선순위, 처음 나온 자리)로 정렬해 앞의 `cap` 개."""
    if cap < 1:
        raise ValueError("cap 은 1 이상")
    best: dict[str, tuple[int, int, int]] = {}
    seq = 0
    for prio, codes in entries:
        for k, c in enumerate(codes):
            cur = best.get(c)
            if cur is None:
                best[c] = (prio, k, seq)
                seq += 1
            elif (prio, k) < cur[:2]:
                best[c] = (prio, k, cur[2])
    order = sorted(best, key=lambda c: best[c])
    return Targets(tuple(order[:cap]), tuple(order[cap:]))


def holdings(trades: Iterable[TradeRow], upto: date) -> dict[str, date]:
    """upto 이하 매매로 본 보유 종목 → 보유 시작일(순수량 > 0). 같은 날은 매수 먼저 센다."""
    by: dict[str, list[TradeRow]] = {}
    for t in trades:
        if t.trade_date <= upto:
            by.setdefault(t.code, []).append(t)
    out: dict[str, date] = {}
    for code, ts in by.items():
        qty = 0
        since: date | None = None
        for t in sorted(ts, key=lambda r: (r.trade_date, r.side != "buy", r.id or 0)):
            before = qty
            qty += t.qty if t.side == "buy" else -t.qty
            if before <= 0 < qty:
                since = t.trade_date
            if qty <= 0:
                since = None
        if qty > 0 and since is not None:
            out[code] = since
    return dict(sorted(out.items()))


# ── 신호 키 ───────────────────────────────────────────────────────────────────────────────────


def close_eval_ts(d: date) -> datetime:
    return datetime.combine(d, CLOSE_EVAL_TIME, tzinfo=KST)


def _b32(n: int, width: int) -> str:
    out: list[str] = []
    for _ in range(width):
        n, r = divmod(n, 32)
        out.append(_CROCKFORD[r])
    return "".join(reversed(out))


def signal_id(mode: SignalMode, ts: datetime, rule_id: str, version: int, code: str) -> str:
    """결정적 ULID — 시각(밀리초 48비트) + sha256(키) 80비트."""
    if ts.tzinfo is None:
        raise ValueError("naive datetime 은 받지 않는다")
    ms = int(ts.timestamp() * 1000)
    h = hashlib.sha256(f"{mode}|{rule_id}|{version}|{code}|{ts.isoformat()}".encode()).digest()
    rnd = int.from_bytes(h[:10], "big")
    return signal_id_prefix(mode) + _b32(ms, 10) + _b32(rnd, 16)
