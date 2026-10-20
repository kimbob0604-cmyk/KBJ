"""합성 P8 세계 `p8_world` — P8 묶음(EVL·BKT·JNL·WRAP)이 같이 쓰는 고정 시드 합성 데이터.

근거: docs/p8_design.md §1.1(FND 표 `p8_world` 행)·§0.3(완료 기준)·§4·부록 A(metrics §30). KIS·KRX
원자료와 그것으로 만든 픽스처는 공개 레포에 넣지 않는다 — **전부
합성**(`random.Random(f"{seed}:{이름}")` —
같은 시드 → 같은 `digest()`). 종목코드·이름은 가상(`Q8` 접두 합성 코드).

쓰는 법
    from tests.fixtures.synthetic.p8_world import build_world
    w = build_world()                  # 시드 20261102(캐시 — 같은 객체)
    w.bars["Q80010"]                   # 일봉(오름차순 — kbj.core.rows.Bar, 정지일은 행 없음)
    w.flows["Q80013"][d]               # 수급 원장 행(kbj.engines.flows.ledger.LedgerRow — 확정)
    w.slots_for("Q80015", d)           # 장중 10분 슬롯(QuoteSlot — 09:00~15:30, 40개)
    w.live_flow("Q80016", ts)          # 장중 가집계 누적(LedgerRow, estimated) — 없으면 None
    w.fill(repos)                      # MemoryRepos 에
    시세·지수·유니버스·수급·관심종목·매매·일지·슬롯
    w.examples                         # config/rule_examples.yaml 예시 10개(검사·정규화)

무엇이 있나(손계산 기대값은 `p8_expect.yaml`)
- 거래일 320(정본 캘린더 — 마지막 2026-10-30 금), 종목 80(코스피 홀수·코스닥 짝수 번호).
  특수: Q80002 분할(칸 200 에서 종가 절반 — split_guard), Q80003 거래정지(칸 150~152 일봉 없음),
  Q80004 신규상장(칸 250 부터), Q80005 우선주(kind pref).
- 지수 2(1001 코스피·2001 코스닥 — config/rules.yaml index), 관심종목 main 20(Q80001~Q80020),
  매매 30건(보유 Q80006~Q80012), 분석 일지 5건.
- 장중: 마지막 5 거래일 × 40 슬롯(09:00~15:30) × 관심종목. 그날 일봉 = 슬롯에서 만든 값(시가 = 첫
  슬롯 시가,
  고저 = 슬롯 고저, 종가 = 마지막 슬롯 현재가, 거래량 = 마지막 누적) — 장중 재생과 마감 평가가 같은
  입력.
  장중 가집계(외국인·기관 누적, estimated)는 관심종목 슬롯마다, **Q80014 는 전부 없음**(판단 불가 —
  사용자 결정 2026-10-13).
- 심은 패턴(`PLANTED` — 손으로 확인한 칸, 예시 규칙이 그 칸에서 참이어야 한다):
  E1·E3 Q80010 11일 연속 −2.5%(칸 289~299) + 칸 300 −8% → 칸 300 RSI(14) < 30, 저가 ≤ 볼린저 하단 /
  E4 Q80017 20일 −0.6% 뒤 +8%(칸 305) → 종가가 20일선 상향 돌파 /
  E5 Q80013 외국인 4일 연속 순매수(칸 307~310, 306 은 순매도) → 칸 309·310 연속 ≥ 3 /
  E6 Q80018 4일 급락(×0.8, 칸 274~277)·13일 약보합·반등(칸 291~) + 외국인 순매수(칸 293~300) → 칸
  296 RSI < 30 이면서 5일선이 20일선 상향 돌파 /
  E8·E9 Q80015 칸 317 슬롯 20 에서 +7%·누적 거래량 평소의 3배 /
  E10 Q80016 칸 318 에 250일 최고 근처(97% 이상) + 장중 외국인 순매수.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from functools import cache
from typing import Any, Final

from kbj.core.calendar import TradingCalendar
from kbj.core.fin_rows import WatchItem
from kbj.core.quality import Quality
from kbj.core.rows import Bar, IndexBar, Investor, InvestorDay, UniverseRow
from kbj.core.rules_rows import AnalysisRow, QuoteSlot, TradeRow
from kbj.core.time import KST
from kbj.engines.flows.ledger import LedgerRow
from kbj.engines.rules.dsl import Rule, load_example_rules

__all__ = ["DAYS", "PLANTED", "SEED", "SYN", "TODAY", "P8World", "Planted", "build_world"]

SEED: Final = 20261102
TODAY: Final = date(2026, 10, 30)
DAYS: Final = 320
N_CODES: Final = 80
SLOTS: Final = 40  # 09:00~15:30 10분
INTRADAY_DAYS: Final = 5
SYN: Final = "SYNTHETIC — tests/fixtures/synthetic/p8_world.py(고정 시드 생성기). 실데이터 아님"
LOADER: Final = "p8_world"

SPLIT, HALTED, NEW_LISTING, PREF = "Q80002", "Q80003", "Q80004", "Q80005"
NO_LIVE_FLOW: Final = "Q80014"
SPLIT_AT, HALT_FROM, HALT_TO, LIST_AT = 200, 150, 152, 250


def code_of(i: int) -> str:
    return f"Q8{i:04d}"


CODES: Final[tuple[str, ...]] = tuple(code_of(i) for i in range(1, N_CODES + 1))
WATCH: Final[tuple[str, ...]] = CODES[:20]
HOLD: Final[tuple[str, ...]] = tuple(code_of(i) for i in range(6, 13))


@dataclass(frozen=True)
class Planted:
    """심은 칸 — 예시 규칙 `rule` 이 종목 `code` 의 칸 `day`(그리고 장중이면 슬롯 `slot`)에서 참."""

    rule: str
    code: str
    day: int
    slot: int | None = None


PLANTED: Final[tuple[Planted, ...]] = (
    Planted("E1", "Q80010", 300),
    Planted("E3", "Q80010", 300),
    Planted("E4", "Q80017", 305),
    Planted("E5", "Q80013", 309),
    Planted("E6", "Q80018", 296),
    Planted("E5", "Q80013", 310),
    Planted("E8", "Q80015", 317, 20),
    Planted("E9", "Q80015", 317, 20),
    Planted("E10", "Q80016", 318, 30),
)


def _rng(seed: int, *parts: object) -> random.Random:
    return random.Random(":".join([str(seed), *map(str, parts)]))  # noqa: S311 — 합성 데이터


def _trading_days(cal: TradingCalendar) -> tuple[date, ...]:
    out = [TODAY]
    d = TODAY
    while len(out) < DAYS:
        d = cal.prev_trading_day(d)
        out.append(d)
    return tuple(reversed(out))


def _slot_ts(d: date, k: int) -> datetime:
    t = datetime.combine(d, time(9, 0), tzinfo=KST) + timedelta(minutes=10 * k)
    return t


def _r2(x: float) -> float:
    return round(x, 2)


def _flow_row(code: str, d: date, foreign: int, inst: int, q: Quality) -> LedgerRow:
    return LedgerRow(
        code=code,
        date=d,
        name=None,
        market=None,
        kind=None,
        close=None,
        chg_pct=None,
        volume=None,
        turnover=None,
        mktcap=None,
        foreign=foreign,
        inst=inst,
        other_corp=None,
        indiv=None,
        inst7=None,
        traded=True,
        flags=(),
        quality=q,
        sources={"flows": "kis" if q is Quality.ESTIMATED else "krx"},
    )


@dataclass
class P8World:
    seed: int
    clock: datetime
    days: tuple[date, ...]
    universe: tuple[UniverseRow, ...]
    bars: dict[str, list[Bar]]
    flows: dict[str, dict[date, LedgerRow]]
    index_bars: dict[str, list[IndexBar]]
    slots: dict[tuple[str, date], list[QuoteSlot]]
    live: dict[tuple[str, datetime], LedgerRow]
    watchlist: tuple[WatchItem, ...]
    trades: tuple[TradeRow, ...]
    analyses: tuple[AnalysisRow, ...]
    examples: Mapping[str, Rule] = field(default_factory=dict[str, Rule])

    @property
    def intraday_days(self) -> tuple[date, ...]:
        return self.days[-INTRADAY_DAYS:]

    def slots_for(self, code: str, d: date) -> list[QuoteSlot]:
        return self.slots.get((code, d), [])

    def live_flow(self, code: str, ts: datetime) -> LedgerRow | None:
        return self.live.get((code, ts))

    def investor_days(self) -> list[InvestorDay]:
        out: list[InvestorDay] = []
        for code, by_day in self.flows.items():
            for d, r in by_day.items():
                for inv, v in ((Investor.FOREIGN, r.foreign), (Investor.INSTITUTION, r.inst)):
                    if v is not None:
                        out.append(InvestorDay(code, d, inv, v, None, "krx", "KRX", r.quality))
        return out

    def records(self) -> Iterator[dict[str, Any]]:
        for code in sorted(self.bars):
            for b in self.bars[code]:
                yield {
                    "_source": SYN,
                    "_kind": "bar",
                    "code": code,
                    "date": b.date.isoformat(),
                    "o": b.open,
                    "h": b.high,
                    "l": b.low,
                    "c": b.close,
                    "v": b.volume,
                }
        for code in sorted(self.flows):
            for d, r in sorted(self.flows[code].items()):
                yield {
                    "_source": SYN,
                    "_kind": "flow",
                    "code": code,
                    "date": d.isoformat(),
                    "f": r.foreign,
                    "i": r.inst,
                }
        for (code, _), ss in sorted(self.slots.items()):
            for s in ss:
                yield {
                    "_source": SYN,
                    "_kind": "slot",
                    "code": code,
                    "ts": s.slot_ts.isoformat(),
                    "p": s.price,
                    "v": s.volume,
                }
        for t in self.trades:
            yield {
                "_source": SYN,
                "_kind": "trade",
                "code": t.code,
                "side": t.side,
                "date": t.trade_date.isoformat(),
                "price": str(t.price),
                "qty": t.qty,
            }

    def digest(self) -> str:
        h = hashlib.sha256()
        for r in self.records():
            h.update(json.dumps(r, sort_keys=True, ensure_ascii=False).encode("utf-8"))
        return h.hexdigest()

    def fill(self, repos: Any) -> None:
        """`MemoryRepos`(또는 같은 모양)에 쓴다 —
        시세·지수·유니버스·수급(확정)·관심종목·매매·일지·슬롯.
        장중 가집계(`live`)는 저장소에 쓰지 않는다(가짜 KIS 가 주는 값 — EVL 이 슬롯마다 넘긴다)."""
        repos.market.upsert_daily_bars(
            [b for bs in self.bars.values() for b in bs], loaded_by=LOADER
        )
        repos.market.upsert_index_bars(
            [b for bs in self.index_bars.values() for b in bs], loaded_by=LOADER
        )
        repos.market.upsert_universe(list(self.universe), loaded_by=LOADER)
        repos.flows.upsert_investor_days(
            self.investor_days(), revise=False, loaded_by=LOADER, now=self.clock
        )
        repos.journal.replace_watchlist(
            "main", list(self.watchlist), loaded_by=LOADER, now=self.clock
        )
        for t in self.trades:
            repos.personal.add_trade_personal(t)
        for a in self.analyses:
            repos.personal.add_analysis_personal(a)
        repos.rules.put_quote_slots_intraday([s for ss in self.slots.values() for s in ss])


def _path(seed: int, code: str, n: int) -> list[float]:
    """종가 경로(기하 랜덤워크 — ±29% 안)."""
    rng = _rng(seed, "path", code)
    c = rng.uniform(5_000, 120_000)
    out = [c]
    for _ in range(n - 1):
        r = max(-0.29, min(0.29, rng.gauss(0.0004, 0.02)))
        c *= 1 + r
        out.append(c)
    return out


def _plant(closes: list[float], code: str) -> None:
    """심은 패턴(모듈 머리말) — 종가 경로를 그 자리에서 고친다(이후 칸은 비율 유지)."""

    def scale_from(i: int, f: float) -> None:
        for j in range(i, len(closes)):
            closes[j] *= f

    if code == "Q80010":  # 11일 연속 −2.5%(칸 289~299) + 칸 300 −8% — RSI 과매도·볼린저 하단
        for i in range(289, 301):
            step = 0.92 if i == 300 else 0.975
            scale_from(i, (closes[i - 1] * step) / closes[i])
    elif code == "Q80017":  # 20일 −0.6% 뒤 칸 305 에 +8%
        for i in range(285, 305):
            scale_from(i, (closes[i - 1] * 0.994) / closes[i])
        scale_from(305, (closes[304] * 1.08) / closes[305])
    elif code == "Q80016":  # 250일 최고 근처 — 칸 260~318 완만한 상승
        top = max(closes[68:300])
        scale_from(260, top * 0.9 / closes[260])
        for i in range(261, 319):
            scale_from(i, (closes[i - 1] * 1.002) / closes[i])
        scale_from(318, max(closes[68:318]) * 0.99 / closes[318])
    elif (
        code == "Q80018"
    ):  # E6 — 4일 급락(×0.8) → 13일 약보합(×0.998) → 반등(×1.006): RSI 낮은 채 5·20일선 교차
        for i in range(274, DAYS):
            step = 0.80 if i <= 277 else 0.998 if i <= 290 else 1.006
            scale_from(i, (closes[i - 1] * step) / closes[i])
    elif code == SPLIT:  # 칸 200 에서 액면분할 2:1(비수정 — 종가 절반)
        scale_from(SPLIT_AT, 0.5)


def _intraday(
    seed: int, code: str, d: date, prev_close: float, close: float, avg_vol: float, k_day: int
) -> list[QuoteSlot]:
    rng = _rng(seed, "slot", code, d)
    pts = [prev_close * (1 + rng.uniform(-0.01, 0.01))]
    for k in range(1, SLOTS):
        frac = k / (SLOTS - 1)
        target = pts[0] + (close - pts[0]) * frac
        pts.append(target * (1 + rng.uniform(-0.004, 0.004)))
    pts[-1] = close
    vol_per = [max(1.0, rng.uniform(0.5, 1.5) * avg_vol / SLOTS) for _ in range(SLOTS)]
    if code == "Q80015" and k_day == DAYS - 3:  # E8·E9 — 슬롯 20 에 +7%·거래량 3배
        for k in range(20, SLOTS):
            pts[k] = prev_close * 1.07 * (1 + rng.uniform(0, 0.005))
        boost = 3.0 * avg_vol / 21
        for k in range(21):
            vol_per[k] = boost
    out: list[QuoteSlot] = []
    hi = lo = pts[0]
    cum = 0
    for k, p in enumerate(pts):
        hi, lo = max(hi, p * 1.001), min(lo, p * 0.999)
        cum += int(vol_per[k])
        out.append(
            QuoteSlot(
                slot_ts=_slot_ts(d, k),
                code=code,
                open=_r2(pts[0]),
                high=_r2(hi),
                low=_r2(lo),
                price=_r2(p),
                volume=cum,
                turnover=None,
                change_pct=round((p / prev_close - 1) * 100, 4),
                flags=(),
                quality=Quality.ESTIMATED,
                source="KIS:watch_quotes_intraday",
                received_at=_slot_ts(d, k) + timedelta(seconds=20),
            )
        )
    return out


@cache
def build_world(seed: int = SEED) -> P8World:
    cal = TradingCalendar.default()
    days = _trading_days(cal)
    clock = datetime.combine(TODAY, time(16, 0), tzinfo=KST)
    universe: list[UniverseRow] = []
    bars: dict[str, list[Bar]] = {}
    flows: dict[str, dict[date, LedgerRow]] = {}
    slots: dict[tuple[str, date], list[QuoteSlot]] = {}
    live: dict[tuple[str, datetime], LedgerRow] = {}
    for idx, code in enumerate(CODES, start=1):
        market = "KOSPI" if idx % 2 else "KOSDAQ"
        kind = "pref" if code == PREF else "common"
        first = LIST_AT if code == NEW_LISTING else 0
        universe.append(
            UniverseRow(
                code=code,
                as_of=TODAY,
                name=f"합성{idx:02d}",
                market=market,
                kind=kind,
                listed_on=days[first],
                source="krx",
                quality=Quality.OK,
            )
        )
        closes = _path(seed, code, DAYS)
        _plant(closes, code)
        rng = _rng(seed, "ohlc", code)
        vols = [int(rng.lognormvariate(11, 0.4)) for _ in range(DAYS)]
        out: list[Bar] = []
        fl: dict[date, LedgerRow] = {}
        frng = _rng(seed, "flow", code)
        for i, d in enumerate(days):
            f_net = int(frng.gauss(0, 2e8))
            i_net = int(frng.gauss(0, 1.5e8))
            if code == "Q80013" and 306 <= i <= 310:
                f_net = -abs(f_net) - 1 if i == 306 else abs(f_net) + 1
            if code == "Q80018" and 293 <= i <= 300:
                f_net = abs(f_net) + 1
            if code == "Q80016" and i >= 316:
                f_net = abs(f_net) + 1
            if i < first or (code == HALTED and HALT_FROM <= i <= HALT_TO):
                continue
            fl[d] = _flow_row(code, d, f_net, i_net, Quality.OK)
            c = closes[i]
            prev = closes[i - 1] if i > 0 else c
            if code in WATCH and i >= DAYS - INTRADAY_DAYS:
                avg = sum(vols[i - 20 : i]) / 20
                ss = _intraday(seed, code, d, prev, c, avg, i)
                slots[(code, d)] = ss
                last = ss[-1]
                o, hi, lo = ss[0].open, max(s.high or 0 for s in ss), min(s.low or 1e18 for s in ss)
                vols[i] = last.volume or 0
                out.append(
                    Bar(
                        code,
                        d,
                        o,
                        _r2(hi),
                        _r2(lo),
                        last.price,
                        vols[i],
                        None,
                        "krx",
                        "KRX",
                        Quality.OK,
                    )
                )
                for s in ss:
                    if code == NO_LIVE_FLOW:
                        continue
                    frac = (s.slot_ts.hour * 60 + s.slot_ts.minute - 540) / 390
                    live[(code, s.slot_ts)] = _flow_row(
                        code, d, int(f_net * frac), int(i_net * frac), Quality.ESTIMATED
                    )
                continue
            o = prev * (1 + rng.uniform(-0.006, 0.006)) if code != SPLIT or i != SPLIT_AT else c
            hi = max(o, c) * (1 + abs(rng.gauss(0, 0.006)))
            lo = min(o, c) * (1 - abs(rng.gauss(0, 0.006)))
            out.append(
                Bar(
                    code,
                    d,
                    _r2(o),
                    _r2(hi),
                    _r2(lo),
                    _r2(c),
                    vols[i],
                    None,
                    "krx",
                    "KRX",
                    Quality.OK,
                )
            )
        bars[code] = out
        flows[code] = fl
    index_bars: dict[str, list[IndexBar]] = {}
    # 원장 지수 코드 = KRX 지수 일별 코드(공백 뺀 이름 — krx_daily.index_code). 값의 씨앗은 그대로
    for icode, seed_name in (("코스피", "1001"), ("코스닥", "2001")):
        path = _path(seed, f"idx{seed_name}", DAYS)
        index_bars[icode] = [
            IndexBar(
                icode,
                d,
                None,
                _r2(p),
                _r2(p * 1.004),
                _r2(p * 0.996),
                _r2(p),
                None,
                None,
                "krx",
                Quality.OK,
            )
            for d, p in zip(days, path, strict=True)
        ]
    watch = tuple(
        WatchItem("main", c, f"합성{i + 1:02d}", clock - timedelta(days=30), i, None, "cli")
        for i, c in enumerate(WATCH)
    )
    trng = _rng(seed, "trades")
    trades: list[TradeRow] = []
    for n in range(30):
        code = HOLD[n % len(HOLD)]
        di = 200 + n * 3
        d = days[di]
        bar = next(b for b in bars[code] if b.date == d)
        side = "sell" if n % 5 == 4 else "buy"
        trades.append(
            TradeRow(
                None,
                code,
                None,
                side,
                d,
                Decimal(str(bar.close)),
                trng.randint(1, 20) * 10,
                Decimal("100"),
                Decimal("0") if side == "buy" else Decimal("250"),
                "합성",
                None,
                None,
                "cli",
                clock,
            )
        )
    analyses = tuple(
        AnalysisRow(
            None,
            code,
            days[300 + i],
            (None, None, None),
            Decimal("50000"),
            12.5,
            10.0,
            0.4,
            6.0,
            Decimal("1000000000000"),
            0.12,
            {"base": Decimal("5000")},
            {"base": 10.0},
            {"base": Decimal("60000")},
            "kbj",
            "중",
            "합성 메모",
            ("합성",),
            "관찰",
            {"price": "kbj"},
            "cli",
            clock,
        )
        for i, code in enumerate(WATCH[5:10])
    )
    return P8World(
        seed=seed,
        clock=clock,
        days=days,
        universe=tuple(universe),
        bars=bars,
        flows=flows,
        index_bars=index_bars,
        slots=slots,
        live=live,
        watchlist=watch,
        trades=tuple(trades),
        analyses=analyses,
        examples=load_example_rules(),
    )
