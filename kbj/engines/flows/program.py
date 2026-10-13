"""프로그램매매 — 검산 ④·일별 막대·상위 순위·범위 꼬리표·장중 훑기 범위(순수 계산, P5 F2).

근거: docs/p5_flow_program_prompt.md §0-2·§0-3·§1.4·§4, docs/metrics.md §22. 입력은
`kbj.core.p5_rows.ProgramMarketRow`·`ProgramStockRow`(금액 **원**). I/O 없음(계약 ⑨).

- 검산 ④: 한 (날짜·시장·거래소·슬롯) 묶음에서 측정값마다 `전체 = 차익 + 비차익`(위탁·자기·합계
  각각), `합계 = 위탁 + 자기`(차익·비차익·전체 각각). 허용 0(합성 원장 — 실데이터는 단위 반올림이
  있으면 [실측 필요 #42]). 항이 빠지면 그 식은 **불가**(세기만 — 0 으로 채우지 않는다). 실패 묶음의
  행은 `invalid`(집계 제외 — 수는 보인다).
- 일별 막대: 순매수 = 전체(whol) 합계(smtn) 순매수 대금. 상세 = 차익·비차익(합계) × 위탁·자기(전체).
- 상위 표: 순매수 상위 = 순매수 대금 > 0 을 큰 순, 순매도 상위 = 순매수 대금 < 0 을 작은 순(같으면
  코드 순). invalid·값 없음은 뺀다.
- **범위 꼬리표**(F2 §1.4 — 검산 ⑤): KIS 에 프로그램 순위 TR 이 없어 종목 TR 로 범위를 훑는다. 장중
  =
  `거래대금 상위 N + 관심종목`, 마감 = `전 종목(마감 확정)`(훑는 중이면 `훑은 종목 n/N`). 꼬리표는
  비울 수 없다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Final, Literal

from kbj.core.p5_rows import PROGRAM_KINDS, PROGRAM_SIDES, ProgramMarketRow, ProgramStockRow
from kbj.core.quality import Quality
from kbj.core.rows import UniverseRow

__all__ = [
    "MARKET_NAME",
    "MEASURES",
    "SWEEP_SKIP_KINDS",
    "Check4Failure",
    "DayBar",
    "GroupKey",
    "apply_program_checks",
    "check4",
    "day_bars",
    "program_report",
    "rank_top",
    "scan_codes",
    "scope_label",
    "sweep_universe",
]

MEASURES: Final[tuple[str, ...]] = (
    "sell_qty", "buy_qty", "net_qty", "sell_value", "buy_value", "net_value",
)  # fmt: skip
# 프로그램 시장(K·Q) → 유니버스·거래대금 순위의 시장 이름
MARKET_NAME: Final[Mapping[str, str]] = {"K": "KOSPI", "Q": "KOSDAQ"}
SWEEP_SKIP_KINDS: Final = frozenset({"etf", "etn"})  # 마감 훑기에서 빼는 종류(주식 프로그램 TR)
GroupKey = tuple[date, str, str, datetime | None]  # (날짜, 시장, 거래소, 슬롯 — 일별은 None)
Side = Literal["buy", "sell"]
Scope = Literal["intraday", "close"]


@dataclass(frozen=True)
class Check4Failure:
    key: GroupKey
    rule: str  # 'whol=arbt+nabt' | 'smtn=entm+onsl'
    at: str  # side(앞 식) 또는 kind(뒤 식)
    measure: str
    residual: int


def _groups(
    rows: Iterable[ProgramMarketRow],
) -> dict[GroupKey, dict[tuple[str, str], ProgramMarketRow]]:
    out: dict[GroupKey, dict[tuple[str, str], ProgramMarketRow]] = {}
    for r in rows:
        out.setdefault((r.trade_date, r.market, r.venue, r.ts), {})[(r.kind, r.side)] = r
    return out


def _get(g: Mapping[tuple[str, str], ProgramMarketRow], kind: str, side: str, m: str) -> int | None:
    r = g.get((kind, side))
    return None if r is None else getattr(r, m)


def _check_group(
    key: GroupKey, g: Mapping[tuple[str, str], ProgramMarketRow]
) -> tuple[list[Check4Failure], int, int]:
    """(실패, 본 식 수, 불가 식 수)."""
    fails: list[Check4Failure] = []
    checked = na = 0
    for m in MEASURES:
        for side in PROGRAM_SIDES:
            w, a, n = (_get(g, k, side, m) for k in ("whol", "arbt", "nabt"))
            if w is None or a is None or n is None:
                na += 1
                continue
            checked += 1
            if w != a + n:
                fails.append(Check4Failure(key, "whol=arbt+nabt", side, m, w - (a + n)))
        for kind in PROGRAM_KINDS:
            t, e, o = (_get(g, kind, s, m) for s in ("smtn", "entm", "onsl"))
            if t is None or e is None or o is None:
                na += 1
                continue
            checked += 1
            if t != e + o:
                fails.append(Check4Failure(key, "smtn=entm+onsl", kind, m, t - (e + o)))
    return fails, checked, na


def check4(rows: Sequence[ProgramMarketRow]) -> list[Check4Failure]:
    """검산 ④ 실패 목록(묶음·식·측정값 — 잔차는 원·주)."""
    out: list[Check4Failure] = []
    for key, g in _groups(rows).items():
        out += _check_group(key, g)[0]
    return out


def apply_program_checks(
    rows: Sequence[ProgramMarketRow],
) -> tuple[list[ProgramMarketRow], list[Check4Failure]]:
    """실패 묶음의 행을 invalid 로 바꾼 행(받은 순서)과 실패 목록."""
    fails = check4(rows)
    bad = {f.key for f in fails}
    out = [
        replace(r, quality=Quality.INVALID) if (r.trade_date, r.market, r.venue, r.ts) in bad else r
        for r in rows
    ]
    return out, fails


# ── 일별 막대 ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DayBar:
    date: date
    net: int | None  # 전체 합계 순매수(원)
    arbt_net: int | None  # 차익 합계
    nabt_net: int | None  # 비차익 합계
    entm_net: int | None  # 전체 위탁
    onsl_net: int | None  # 전체 자기
    index_close: float | None
    quality: Quality


def _worst(qs: Iterable[Quality]) -> Quality:
    order = (Quality.OK, Quality.STALE, Quality.ESTIMATED, Quality.INVALID)
    got = list(qs)
    return max(got, key=order.index) if got else Quality.INVALID


def day_bars(rows: Sequence[ProgramMarketRow]) -> list[DayBar]:
    """일별 행(한 시장·거래소) → 날짜 오름차순 막대. invalid 묶음은 값 없이(quality invalid) 남긴다
    — 화면이 빈 칸으로 두고 수를 센다."""
    by_day: dict[date, dict[tuple[str, str], ProgramMarketRow]] = {}
    for r in rows:
        by_day.setdefault(r.trade_date, {})[(r.kind, r.side)] = r
    out: list[DayBar] = []
    for d in sorted(by_day):
        g = by_day[d]
        q = _worst(r.quality for r in g.values())
        if q == Quality.INVALID:
            out.append(DayBar(d, None, None, None, None, None, None, q))
            continue
        idx = next((r.index_close for r in g.values() if r.index_close is not None), None)
        out.append(
            DayBar(
                d,
                _get(g, "whol", "smtn", "net_value"),
                _get(g, "arbt", "smtn", "net_value"),
                _get(g, "nabt", "smtn", "net_value"),
                _get(g, "whol", "entm", "net_value"),
                _get(g, "whol", "onsl", "net_value"),
                idx,
                q,
            )
        )
    return out


# ── 상위 표·범위 ──────────────────────────────────────────────────────────────────────


def rank_top(rows: Sequence[ProgramStockRow], side: Side, n: int) -> list[ProgramStockRow]:
    """순매수(buy) 또는 순매도(sell) 상위 n — 값 없음·invalid 제외, 같으면 코드 순."""
    if n < 1:
        raise ValueError("n 은 1 이상")
    ok = [r for r in rows if r.quality != Quality.INVALID and r.net_value is not None]
    if side == "buy":
        pick = [r for r in ok if (r.net_value or 0) > 0]
        pick.sort(key=lambda r: (-(r.net_value or 0), r.code))
    else:
        pick = [r for r in ok if (r.net_value or 0) < 0]
        pick.sort(key=lambda r: ((r.net_value or 0), r.code))
    return pick[:n]


def scope_label(
    scope: Scope, *, top_n: int, swept: int | None = None, total: int | None = None
) -> str:
    """순위 표 범위 꼬리표(비울 수 없다 — F2 §1.4). 마감 표는 훑기가 덜 끝났으면 진행 수를
    붙인다."""
    if scope == "intraday":
        return f"거래대금 상위 {top_n} + 관심종목"
    if swept is not None and total is not None and swept < total:
        return f"전 종목(마감 확정 — 훑은 종목 {swept}/{total})"
    return "전 종목(마감 확정)"


def scan_codes(
    ranked: Sequence[str], watch: Sequence[str], *, top_n: int, watch_max: int
) -> list[str]:
    """장중 훑기 범위 = 거래대금 상위 top_n(받은 순서) + 관심종목 앞 watch_max(겹치면 한 번)."""
    out = list(dict.fromkeys(ranked[:top_n]))
    for c in watch[:watch_max]:
        if c not in out:
            out.append(c)
    return out


def sweep_universe(
    universe: Iterable[UniverseRow], markets: Sequence[str]
) -> list[tuple[str, str]]:
    """마감 훑기 대상 (종목, 시장 K·Q) — 유니버스의 그 시장 종목(ETF·ETN 제외), 코드 순. 화면의
    '훑은 종목 n/N' 의 N 도 이것으로 센다(수집·API 가 같은 함수)."""
    names = {MARKET_NAME[m]: m for m in markets}
    out = [
        (u.code, names[u.market])
        for u in universe
        if u.market in names and (u.kind or "") not in SWEEP_SKIP_KINDS
    ]
    return sorted(out)


def program_report(rows: Sequence[ProgramMarketRow]) -> dict[str, int]:
    """검산 ④ 건수(값 없음 — `verify --program`)."""
    groups = _groups(rows)
    failed_groups = checked = na = fails = 0
    for key, g in groups.items():
        f, c, n = _check_group(key, g)
        checked += c
        na += n
        fails += len(f)
        failed_groups += bool(f)
    return {
        "groups": len(groups),
        "checked": checked,
        "failed": fails,
        "failed_groups": failed_groups,
        "na": na,
    }
