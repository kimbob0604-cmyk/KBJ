"""`market.close_collect` — 장 마감 KIS 수집: 종목 현재가·종목/ETF 투자자·시장 투자자·가집계.

근거: docs/p3_design.md §1.2·§3.2·§3.5·§3.7, D-P3-7·9, metrics §2. 승격 원본: ET
`board/ingest/stockflows.py`(KIS 주 경로 — 못 받은 종목은 빼지 않고 사유와 함께 남긴다, 0 으로
채우지 않는다, 기준일 뒤 날짜를 고르지 않는다), SD `server.py:_refresh_flow_batch`:3045(대체).

선점한 데이터 키(as_of = 그날, 거래소 = `config/markets.yaml` `kis.venues`)마다:

- `inst_foreign_top` → FHPTJ04400000 시장 2 × 구분 2 — `prv_flows.investor_intraday`(ts = 장 마감) +
  일별 원장 `kis.prelim`(estimated)
- `market_investor_daily` → FHPTJ04040000 시장 2 — `prv_flows.market_investor_daily`
- `stock_quote_eod` → FHKST01010100 유니버스 전 종목 —
  `prv_market.stock_snapshot`·`daily_bar`(source `kis`, ok)
- `stock_investor_daily` → FHKST01010900 유니버스 전 종목 — `prv_flows.stock_investor_daily`(source
  `kis`, ok — **revise**)
- `etf_investor_daily` → FHKST01010900 순자산 하한 이상 ETF — 같음

- 처리 순서는 위 표 순서 — 가집계 스냅(`kis.prelim`)을 먼저 넣고 확정(`kis`)이 같은 키를 덮어
  **차이를 `prv_flows.investor_revision` 에 같은 트랜잭션으로** 남긴다(저장소 `revise=True` + now
  주입).
- 유니버스 = 그날 이하 가장 최근 KRX 기본정보(`krx.daily` 가 전 거래일 것을 넣는다)의 코스피·코스닥
  주식 (ETF·ETN 제외). 당일 신규 상장은 KRX 기본정보에 아직 없다 [확인 필요 — 다음 날 들어온다].
  유니버스가 비면 실패(무엇을 받을지 지어내지 않는다).
- 종목 단위 실패: **일시 실패**(HTTP 5xx·한도초과·토큰·리미터 대기 초과·당일 행 아직 없음)는 그 키를
  미완으로 두고(받은 종목은 쓴다) 재시도가 **남은 종목만** 다시 받는다. **종목 고유 실패**(응답
  형식·빈 결과·거절)는 이름과 사유를 detail 에 남기고 넘어간다 — 단 전체의 5% 를 넘으면 체계적
  문제로 보고 키를 실패로 둔다. 오늘 KIS 마감 스냅이 **거래정지·거래량 0** 인 종목은 투자자 당일
  행이 없어도 기다리지 않는다(`no_trade_codes` — 행은 만들지 않는다). 오늘 스냅이 없는 종목(ETF)은
  당일 행이 비어 남은 것이 허용 수 이하일 때만 현재가를 한 번 보고 같은 규칙을 쓴다
  (`_settle_unknown` — 거래 없는 채권 ETF 셋이 마감 수집 전체와 보드를 막았다, 2026-10-19·20).
- 네트워크 오류(이름풀이·접속 — `httpx.TransportError`)는 일시 실패다. `NET_DOWN_AFTER` 번
  이어지면 남은 종목은 부르지 않고 일시 실패로 둔다(받은 것은 쓴다 — 재시도가 이어 받는다). 맥이
  잠든 중 몇 초씩 깬 때 네트워크 없이 ETF 437종목을 다 넘겨 '종목 고유 실패'로 끝났다(2026-10-22).
- 투자자 금액은 쓰기 전에 **자릿수 대조**(`investors.unit_check` — 수량 × 종가)를 한다. 배수가 0.2~5
  밖이면 단위를 잘못 안 것으로 보고 **하나도 쓰지 않는다**(ET monitor/flow '자릿수가 틀리면 리포트를
  쓰지 않는다'). 대조할 규모가 모자라면 쓰되 `unit_check: unverified` 로 적는다(통과라고 하지
  않는다).
- KIS 우선순위는 `config/limits.yaml` `kis.priorities.close_collect`(P3).
- 기타법인이 없는 3구분 TR 이라 검산 ① 은 실데이터에서 불가(R2) — 수집은 받은 구분만 넣고, 불가
  사유는 엔진(묶음 E2)이 `None` + notes 로 낸다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Final

import httpx

from kbj.config.markets import MarketsConfig
from kbj.core.rows import Bar, IntradayInvestor, InvestorDay, Snap, UniverseRow
from kbj.core.time import KST
from kbj.data.limits import load_limits
from kbj.data.private.kis.investors import (
    INST_FOREIGN_WHO,
    closes_of,
    market_history,
    merge_inst_foreign,
    on_day,
    params_inst_foreign,
    params_market_investor,
    params_stock_investor,
    parse_inst_foreign_total,
    parse_market_investor,
    parse_stock_investor,
    prelim_days,
    recent,
    unit_check,
)
from kbj.data.private.kis.parse import KisEmpty, KisRejected
from kbj.data.private.kis.quotes import bar_from_quote, params_quote, parse_quote
from kbj.data.private.kis.token import TokenError
from kbj.data.ratelimit import Priority, RateLimitTimeout
from kbj.data.spec import DataKey
from kbj.services.collectors._p3 import (
    KisGetter,
    NotReady,
    Outcome,
    PartialFailure,
    RepoSet,
    calendar,
    kis_client,
    kis_get,
    markets,
    reason,
    repos,
    split_venues,
)
from kbj.services.scheduler.handlers import JobContext, JobResult

__all__ = [
    "MARKETS",
    "STOCK_MARKETS",
    "UnitMismatch",
    "collect",
    "etf_targets",
    "run",
    "stock_universe",
]

MARKETS: Final[dict[str, str]] = {"0001": "KOSPI", "1001": "KOSDAQ"}  # KIS 업종 코드 [추정 #24]
STOCK_MARKETS: Final = frozenset({"KOSPI", "KOSDAQ"})
ORDER: Final[tuple[str, ...]] = (
    "inst_foreign_top",
    "market_investor_daily",
    "stock_quote_eod",
    "stock_investor_daily",
    "etf_investor_daily",
)
PERMANENT_MAX_RATIO: Final = 0.05  # 종목 고유 실패 허용 비율 [확인 필요]
PERMANENT_MIN: Final = 3  # 작은 목록에서도 이만큼은 넘어간다
NET_DOWN_AFTER: Final = 5  # 네트워크 오류가 이만큼 이어지면 남은 종목은 부르지 않는다
OFFLINE_REASON: Final = "네트워크 끊김 — 부르지 않았다(다음 시도에 받는다)"
SAMPLE_MAX: Final = 5
STALE_AFTER_DAYS: Final = 2  # 이만큼 지난 날의 '행 없음'은 공표 지연이 아니라 사실이다
MARKET_HISTORY_DAYS: Final = 450  # 시장 TR 은 최근 300영업일을 준다 — 받은 만큼 저장
HISTORY_DAYS: Final = 45  # 종목 TR 이 주는 최근 한 달 남짓을 같이 저장 — 20일 스크리너가 바로 찬다
UNIVERSE_LOOKBACK: Final = 10  # 시장 하나가 빠진 날 더 이전 유니버스를 찾는 횟수 [확인 필요]


class UnitMismatch(RuntimeError):
    """투자자 금액의 자릿수가 수량 × 종가와 맞지 않는다 — 쓰지 않았다."""


def _transient(e: BaseException) -> bool:
    if isinstance(e, KisRejected):
        return e.retryable
    return isinstance(e, TokenError | RateLimitTimeout | OSError | httpx.TransportError | NotReady)


@dataclass
class _Tally:
    """종목 단위 결과 — 일시 실패·고유 실패를 나눠 센다."""

    total: int
    transient: dict[str, str] = field(default_factory=dict[str, str])
    permanent: dict[str, str] = field(default_factory=dict[str, str])
    no_trade: dict[str, str] = field(default_factory=dict[str, str])
    not_ready: int = 0
    net_streak: int = 0  # 이어진 네트워크 오류 수(응답을 받으면 0)
    offline_skipped: int = 0

    def answered(self) -> None:
        self.net_streak = 0

    def offline(self) -> bool:
        return self.net_streak >= NET_DOWN_AFTER

    def skip_rest(self, codes: Sequence[str]) -> None:
        """네트워크가 끊겼다 — 남은 종목은 부르지 않고 일시 실패로 둔다."""
        for code in codes:
            self.transient[code] = OFFLINE_REASON
        self.offline_skipped += len(codes)

    def fail(self, code: str, e: BaseException) -> None:
        self.net_streak = self.net_streak + 1 if isinstance(e, httpx.TransportError) else 0
        if isinstance(e, NotReady):
            self.not_ready += 1
            self.transient[code] = reason(e)
        elif _transient(e):
            self.transient[code] = reason(e)
        else:
            self.permanent[code] = reason(e)

    def detail(self) -> dict[str, Any]:
        d: dict[str, Any] = {"targets": self.total}
        if self.permanent:
            d["skipped_codes"] = len(self.permanent)
            d["skipped_sample"] = dict(sorted(self.permanent.items())[:SAMPLE_MAX])
        if self.transient:
            d["retry_codes"] = len(self.transient)
        if self.offline_skipped:
            d["offline_skipped"] = self.offline_skipped
        if self.no_trade:
            d["no_trade_codes"] = len(self.no_trade)
            d["no_trade_sample"] = dict(sorted(self.no_trade.items())[:SAMPLE_MAX])
        return d

    def cap(self) -> int:
        return max(PERMANENT_MIN, int(self.total * PERMANENT_MAX_RATIO))

    def settle(self, what: str, done: int) -> None:
        """끝에 — 일시 실패가 있으면 미완, 고유 실패가 많으면 실패."""
        cap = self.cap()
        if len(self.permanent) > cap:
            first = next(iter(sorted(self.permanent.items())))
            raise PartialFailure(
                f"{what}: 종목 고유 실패 {len(self.permanent)}/{self.total} — 허용 {cap} 초과"
                f"(첫 사유 {first[0]}: {first[1]})"
            )
        if self.transient:
            if done == 0 and self.not_ready == len(self.transient):
                raise NotReady(f"{what}: 당일 행이 아직 없다({self.not_ready}종목)")
            first = next(iter(sorted(self.transient.items())))
            raise PartialFailure(
                f"{what}: {len(self.transient)}/{self.total} 종목 미완 — 재시도가 남은 것만 받는다"
                f"(첫 사유 {first[0]}: {first[1]})"
            )


# ── 대상 ────────────────────────────────────────────────────────────────────────────────


def _stock_market(u: UniverseRow) -> str | None:
    if u.kind in ("etf", "etn"):
        return None
    parts = (u.market or "").upper().split()  # 'KOSDAQ GLOBAL' → KOSDAQ
    return parts[0] if parts and parts[0] in STOCK_MARKETS else None


def stock_universe(
    r: RepoSet, day: date, *, stale: dict[str, str] | None = None
) -> list[tuple[str, str | None, str]]:
    """(코드, 이름, 시장) — 그날 이하 최근 KRX 기본정보의 코스피·코스닥 주식(ETF·ETN 제외).

    시장마다 따로 본다: 가장 최근 유니버스에 한 시장이 없으면(그날 그 시장 기본정보만 실패) 그
    시장은 더 이전 유니버스에서 가져오고 `stale` 에 그 날짜를 적는다 — 한 시장이 **조용히 빠지지
    않게**. `UNIVERSE_LOOKBACK` 안에서도 없으면 실패."""
    found: dict[str, list[tuple[str, str | None, str]]] = {}
    probe = day
    for _ in range(UNIVERSE_LOOKBACK):
        rows = r.market.universe(probe)
        if not rows:
            break
        newest = max(u.as_of for u in rows)
        got: dict[str, list[tuple[str, str | None, str]]] = {}
        for u in rows:
            m = _stock_market(u)
            if m is not None and m not in found:
                got.setdefault(m, []).append((u.code, u.name, m))
        for m, items in got.items():
            found[m] = items
            if probe != day and stale is not None:
                stale[m] = newest.isoformat()
        if set(found) >= STOCK_MARKETS:
            break
        probe = min(u.as_of for u in rows) - timedelta(days=1)
    missing = sorted(STOCK_MARKETS - set(found))
    if len(missing) == len(STOCK_MARKETS):
        raise RuntimeError(f"{day} 이하 유니버스(KRX 기본정보)가 없다 — krx.daily 선행 필요")
    if missing:
        raise RuntimeError(
            f"{day} 이하 유니버스에 {','.join(missing)} 가 없다 — 그 시장을 빼고 받지 않는다"
        )
    return [t for m in sorted(found) for t in found[m]]


def etf_targets(r: RepoSet, day: date, min_net_asset: int) -> list[tuple[str, str | None]]:
    """순자산 하한 이상 ETF(그날 이하 가장 최근 KRX ETF 일별) — 순자산 큰 순."""
    latest = r.etf.etf_days(day, 1)
    if not latest:
        raise RuntimeError(f"{day} 이하 ETF 일별(KRX)이 없다 — krx.daily 선행 필요")
    rows = [v[-1] for v in latest.values() if v]
    picked = [d for d in rows if d.net_asset is not None and d.net_asset >= min_net_asset]
    picked.sort(key=lambda d: (-(d.net_asset or 0), d.code))
    return [(d.code, d.name) for d in picked]


# ── 데이터셋별 ───────────────────────────────────────────────────────────────────────────


@dataclass
class _Ctx:
    repos: RepoSet
    kis: KisGetter
    day: date
    now: datetime
    job: str
    cfg: MarketsConfig
    close_ts: datetime
    priority: Priority
    detail: dict[str, Any]


def _get(c: _Ctx, name: str, params: dict[str, str], what: str) -> dict[str, Any]:
    return kis_get(c.kis, name, params, what=what, priority=c.priority, timeout=None)


def _inst_foreign_top(c: _Ctx, venue: str) -> int:
    lists: list[tuple[str, list[IntradayInvestor]]] = []
    for market in MARKETS:
        for who in INST_FOREIGN_WHO:
            body = _get(c, "inst_foreign_total", params_inst_foreign(market, who, venue),
                        f"FHPTJ04400000 {market} {who}")  # fmt: skip
            lists.append((who, parse_inst_foreign_total(body, ts=c.close_ts, venue=venue)))
    rows = merge_inst_foreign(lists)
    c.repos.flows.put_intraday(rows, loaded_by=c.job, received_at=c.now)
    rep = c.repos.flows.upsert_investor_days(
        prelim_days(rows, c.day), revise=False, loaded_by=c.job, now=c.now, received_at=c.now
    )
    c.detail["inst_foreign_top"] = {"rows": len(rows), "skipped": rep.skipped}
    return len(rows)


def _market_investor(c: _Ctx, venue: str) -> int:
    out: list[InvestorDay] = []
    failure: Exception | None = None
    for market in MARKETS:
        body = _get(c, "market_investor", params_market_investor(market, c.day, venue),
                    f"FHPTJ04040000 {market}")  # fmt: skip
        try:
            rows = parse_market_investor(body, market, venue=venue, day=c.day)
        except KisEmpty as e:
            # 그날이 0 이어도(주말·장 마감 직후) 응답이 준 지난 날들은 사실이라 저장한다
            failure = failure or e
            out += recent(market_history(body, market, venue=venue), c.day, MARKET_HISTORY_DAYS)
            continue
        today = on_day(rows, c.day)
        if not today:
            failure = failure or NotReady(f"FHPTJ04040000 {market}: {c.day} 행이 아직 없다")
        out += recent(rows, c.day, MARKET_HISTORY_DAYS)
    if out:
        c.repos.flows.upsert_market_days(out, loaded_by=c.job, received_at=c.now)
    if failure is not None:
        raise failure
    return len(out)


def _universe(c: _Ctx) -> list[tuple[str, str | None, str]]:
    stale: dict[str, str] = {}
    rows = stock_universe(c.repos, c.day, stale=stale)
    if stale:
        c.detail["universe_stale"] = stale  # 그 시장은 더 이전 KRX 기본정보로 받았다
    return rows


def _stock_quotes(c: _Ctx, venue: str) -> int:
    targets = _universe(c)
    have = {s.code for s in c.repos.market.snapshots(c.day, source="kis") if s.venue == venue}
    todo = [t for t in targets if t[0] not in have]
    tally = _Tally(len(targets))
    snaps: list[Snap] = []
    bars: list[Bar] = []
    for i, (code, name, market) in enumerate(todo):
        if tally.offline():
            tally.skip_rest([t[0] for t in todo[i:]])
            break
        try:
            body = _get(c, "stock_quote", params_quote(code, venue), f"FHKST01010100 {code}")
            tally.answered()
            s = parse_quote(body, code, day=c.day, venue=venue, market=market, name=name)
            bars.append(bar_from_quote(body, s))
            snaps.append(s)
        except Exception as e:  # 종목 단위 격리 — 사유를 세고 남긴다
            tally.fail(code, e)
    c.repos.market.upsert_snapshots(snaps, loaded_by=c.job, received_at=c.now)
    c.repos.market.upsert_daily_bars(bars, loaded_by=c.job, asset="stock", received_at=c.now)
    c.detail["stock_quote_eod"] = {**tally.detail(), "written": len(snaps), "had": len(have)}
    tally.settle("FHKST01010100", len(snaps) + len(have))
    return len(snaps)


def _no_trade_today(c: _Ctx, venue: str) -> dict[str, str]:
    """오늘 KIS 마감 스냅(`stock_quote_eod` 가 먼저 넣는다)으로 본 '오늘 거래가 없는' 종목 → 사유.

    거래정지(`halted`)이거나 거래량 0 인 종목은 투자자 일별에 당일 행이 오지 않을 수 있다
    [실측 필요 #22]. 그 종목의 '당일 행 없음'은 공표 지연이 아니라 사실이라 재시도로 기다리지
    않는다(행은 만들지 않는다 — 0 으로 채우지 않는다). 스냅이 없으면 모른다 — 여기 넣지 않는다.
    KIS 스냅이 없어도 KRX 일별 거래량 0 이면 같다(지난 날 재수집·ETF)."""
    out: dict[str, str] = {}
    for s in c.repos.market.snapshots(c.day, source="kis"):
        if s.venue != venue:
            continue
        if s.status_flags is not None and "halted" in s.status_flags:
            out[s.code] = "거래정지(KIS 현재가 상태)"
        elif s.volume == 0:
            out[s.code] = "거래량 0(KIS 현재가)"
    for s in c.repos.market.snapshots(c.day, source="krx"):
        if s.code not in out and s.venue == venue and s.volume == 0:
            out[s.code] = "거래량 0(KRX 일별)"  # KIS 스냅이 없는 날(지난 날 재수집·ETF)에도 안다
    return out


def _snapped_today(c: _Ctx, venue: str) -> set[str]:
    """오늘 스냅(KIS 마감·KRX 일별)이 있어 거래 여부를 아는 종목. ETF 는 KIS 스냅을 받지 않는다."""
    return {
        s.code
        for src in ("kis", "krx")
        for s in c.repos.market.snapshots(c.day, source=src)
        if s.venue == venue
    }


def _settle_unknown(c: _Ctx, venue: str, codes: Sequence[str], tally: _Tally) -> None:
    """당일 행이 비었는데 오늘 스냅이 없는 종목(대개 ETF — 거래 없는 채권 ETF 는 KIS 가 당일 행을
    빈 칸으로 준다, 실측 2026-10-20). 남은 것이 허용 수 이하면 현재가(FHKST01010100)를 한 번씩 보고
    거래량 0·거래정지면 '거래 없음'으로 넘긴다(행은 만들지 않는다). 많으면 공표 지연으로 보고
    부르지 않는다 — 재시도가 다시 본다."""
    if not codes:
        return
    if tally.offline():
        tally.skip_rest(codes)
        return
    if len(codes) > tally.cap():
        for code in codes:
            tally.fail(code, NotReady(f"FHKST01010900 {code}: {c.day} 행이 아직 없다"))
        return
    for code in codes:
        try:
            body = _get(c, "stock_quote", params_quote(code, venue), f"FHKST01010100 {code}")
            s = parse_quote(body, code, day=c.day, venue=venue)
        except Exception as e:  # 확인 못 함 — 기다린다
            why = f"FHKST01010900 {code}: {c.day} 행 없음·현재가 확인 실패({reason(e)})"
            tally.fail(code, NotReady(why))
            continue
        if s.status_flags is not None and "halted" in s.status_flags:
            tally.no_trade[code] = f"{c.day} 행 없음 — 거래정지(KIS 현재가 확인)"
        elif s.volume == 0:
            tally.no_trade[code] = f"{c.day} 행 없음 — 거래량 0(KIS 현재가 확인)"
        else:
            tally.fail(code, NotReady(f"FHKST01010900 {code}: {c.day} 행이 아직 없다"))


def _investors(c: _Ctx, venue: str, targets: Sequence[tuple[str, str | None]], label: str) -> int:
    done_codes = {
        code
        for code, rows in c.repos.flows.days([t[0] for t in targets], c.day, c.day).items()
        if any(r.source == "kis" and r.venue == venue for r in rows)
    }
    todo = [t for t in targets if t[0] not in done_codes]
    tally = _Tally(len(targets))
    rows: list[InvestorDay] = []
    closes: dict[tuple[str, date], float] = {}
    no_trade = _no_trade_today(c, venue)
    snapped = _snapped_today(c, venue)
    unknown: list[str] = []
    stale = (c.now.astimezone(KST).date() - c.day).days >= STALE_AFTER_DAYS
    for i, (code, _name) in enumerate(todo):
        if tally.offline():
            tally.skip_rest([t[0] for t in todo[i:]])
            break
        try:
            body = _get(c, "stock_investor", params_stock_investor(code, venue),
                        f"FHKST01010900 {code}")  # fmt: skip
            tally.answered()
            parsed = parse_stock_investor(body, code, venue=venue)
            today = on_day(parsed, c.day)
            if not today and code in no_trade:
                tally.no_trade[code] = f"{c.day} 행 없음 — {no_trade[code]}"
                continue
            if not today and stale:
                tally.no_trade[code] = f"{c.day} 행 없음 — 이틀 넘게 지나도 공표가 없다"
                continue
            if not today and code not in snapped:
                unknown.append(code)  # 오늘 거래가 있었는지 모른다 — 끝에 현재가로 확인
                continue
            if not today:
                raise NotReady(f"FHKST01010900 {code}: {c.day} 행이 아직 없다")
            rows += recent(parsed, c.day, HISTORY_DAYS)
            for d, px in closes_of(body).items():
                closes[(code, d)] = px
        except Exception as e:  # 종목 단위 격리
            tally.fail(code, e)
    _settle_unknown(c, venue, unknown, tally)
    check = unit_check(rows, closes)
    detail: dict[str, Any] = {**tally.detail(), "had": len(done_codes)}
    detail["unit_check"] = "ok" if check.ok else ("unverified" if check.ratio is None else "bad")
    if check.ratio is not None:
        detail["unit_ratio"] = check.ratio
    c.detail[label] = detail
    if check.ratio is not None and not check.ok:
        raise UnitMismatch(
            f"{label}: {check.label} 배수 {check.ratio} — 단위를 잘못 안 것으로 보고 쓰지 않았다"
        )
    rep = c.repos.flows.upsert_investor_days(
        rows, revise=True, loaded_by=c.job, now=c.now, received_at=c.now
    )
    detail.update(written=rep.written, revised=rep.revised)
    if rep.max_abs_diff is not None:
        detail["max_abs_revision"] = rep.max_abs_diff
    tally.settle(f"FHKST01010900({label})", rep.written + len(done_codes) + len(tally.no_trade))
    return rep.written


def _stock_investors(c: _Ctx, venue: str) -> int:
    targets = [(code, name) for code, name, _m in _universe(c)]
    return _investors(c, venue, targets, "stock_investor_daily")


def _etf_investors(c: _Ctx, venue: str) -> int:
    targets = etf_targets(c.repos, c.day, c.cfg.etf.investor_min_net_asset_krw)
    return _investors(c, venue, targets, "etf_investor_daily")


_HANDLERS: Final[dict[str, Callable[[_Ctx, str], int]]] = {
    "inst_foreign_top": _inst_foreign_top,
    "market_investor_daily": _market_investor,
    "stock_quote_eod": _stock_quotes,
    "stock_investor_daily": _stock_investors,
    "etf_investor_daily": _etf_investors,
}


def collect(keys: Sequence[DataKey], c: _Ctx) -> Outcome:
    out = Outcome(c.job, tuple(keys))
    c.detail = out.detail
    active, off = split_venues(keys, c.cfg.kis.venues)
    out.skip_off(off)
    order = {name: i for i, name in enumerate(ORDER)}
    for key in sorted(active, key=lambda k: (order.get(k.dataset, len(order)), k.venue)):
        fn = _HANDLERS.get(key.dataset)
        if fn is None:
            out.errors[key.label()] = f"모르는 데이터셋: {key.dataset}"
            continue
        out.run(key, lambda fn=fn, v=key.venue: fn(c, v or "KRX"))
    return out


def _priority(ctx: JobContext) -> Priority:
    got = ctx.resources.get("kis_priority")
    if got is not None:
        return Priority(got)
    return load_limits(settings=ctx.settings).source("kis").priority("close_collect")


def run(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.collectors.market_close:run` — `market.close_collect`)."""
    if not ctx.keys:
        return JobResult("skipped", detail={"reason": "선점한 키가 없다"})
    day = date.fromisoformat(ctx.as_of)
    _open, close_ts = calendar(ctx).equity_bounds(day)
    with kis_client(ctx) as kis:
        c = _Ctx(
            repos=repos(ctx),
            kis=kis,
            day=day,
            now=ctx.now,
            job=ctx.job,
            cfg=markets(ctx),
            close_ts=close_ts,
            priority=_priority(ctx),
            detail={},
        )
        out = collect(ctx.keys, c)
    return out.result()
