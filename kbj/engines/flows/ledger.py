"""일별 원장 — (종목, 날짜) 마다 한 행(docs/metrics.md §0·§1·§2, docs/p3_design.md §4.2·D-P3-7).

원장 하나에서 1·5·20일 값·순위·연속일·상단 띠 숫자를 모두 계산한다(metrics §0 — 페이지끼리 숫자가
어긋나지 않게). 이 모듈은 순수 계산이다(I/O 없음).

고르기 규칙(D-P3-7 — `kbj.core.rows.row_rank`·`pick_best` 를 그대로 쓴다. 두 벌 금지)
- 가격(종가·거래량·거래대금): 같은 (종목, 날짜) 의 스냅·일봉 중 원천 순위가 앞선 **쓸 수 있는**
  (invalid 아닌) 행. 앞선 행에 값이 없으면 다음 행에서 채우고 원천을 `sources` 에 남긴다.
- 등락률·시총·상태 플래그: 쓸 수 있는 스냅만(일봉에는 없다). 없으면 None — 지어내지 않는다.
- 투자자: (종목, 날짜, 투자자) 마다 순위가 앞선 행 하나. 외국인 = 외국인 + 기타외국인(응답이 나눠
  줄 때만 따로 있다). 기관 7구분은 **7개가 모두 있을 때만** 쓴다(일부만이면 None + 사유).
- 고른 행이 invalid 뿐이면(더 나은 행이 없으면) 원장 행은 invalid — 집계에서 빠지고 수가 세어진다.
- 휴장일(주어진 거래일 밖) 행은 버리고 수를 `notes` 에 남긴다. 기간 = 영업일(metrics §0).

거래 여부(`traded`): 상태에 거래정지(`halted`)가 있으면 False, 아니면 거래대금(없으면 거래량) > 0.
값을 모르면 False — 일평균 거래대금의 분모(실제 거래된 영업일 수)에 넣지 않는다(metrics §1).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Final, Literal

from kbj.core.quality import Quality
from kbj.core.rows import (
    EXCLUDE_FLAGS,
    INST7,
    SOURCE_RANK,
    Bar,
    Investor,
    InvestorDay,
    Snap,
    UniverseRow,
    ledger_rank,
    pick_best,
    row_rank,
)
from kbj.core.time import KST

if TYPE_CHECKING:
    from kbj.engines.flows.checks import CheckSummary

__all__ = [
    "CLOSE_TIME",
    "MAIN_MARKETS",
    "STOCK_KINDS",
    "WHO",
    "InvestorValues",
    "Ledger",
    "LedgerRow",
    "Who",
    "WindowAgg",
    "build_ledger",
    "close_as_of",
    "investor_values",
    "source_label",
    "sum_known",
    "worst_quality",
]

Who = Literal["foreign", "inst", "other_corp", "indiv"]
# 검산 ①의 4구분(metrics §2) — 원장 열 이름
WHO: Final[tuple[Who, ...]] = ("foreign", "inst", "other_corp", "indiv")
WHO_NAMES: Final[Mapping[str, str]] = {
    "foreign": "외국인",
    "inst": "기관",
    "other_corp": "기타법인",
    "indiv": "개인",
}
# 시장 거래대금·시장폭에 넣는 종류 — 주식만(ETF·ETN·리츠 제외, metrics §1·§6.1) [확인 필요 — 스팩]
STOCK_KINDS: Final[tuple[str, ...]] = ("common", "pref", "spac")
MAIN_MARKETS: Final[tuple[str, ...]] = ("KOSPI", "KOSDAQ")
CLOSE_TIME: Final = time(15, 30)  # 마감 값의 as_of(KST — docs/p3_design.md §5.2)

_Q_ORDER: Final[Mapping[Quality, int]] = {
    Quality.OK: 0,
    Quality.STALE: 1,
    Quality.ESTIMATED: 2,
    Quality.INVALID: 3,
}
_SOURCE_NAMES: Final[Mapping[str, str]] = {"krx": "KRX", "kis": "KIS", "kis.prelim": "KIS(잠정)"}


def worst_quality(qs: Iterable[Quality]) -> Quality | None:
    """가장 나쁜 품질(ok < stale < estimated < invalid). 비었으면 None."""
    out: Quality | None = None
    for q in qs:
        if out is None or _Q_ORDER[q] > _Q_ORDER[out]:
            out = q
    return out


def close_as_of(d: date) -> datetime:
    """마감 값의 기준 시각 — 그날 15:30 KST(aware)."""
    return datetime.combine(d, CLOSE_TIME, tzinfo=KST)


def source_label(sources: Iterable[str]) -> str:
    """원천 묶음 표기 — 예 `KRX`, `KRX+KIS`, `KIS(잠정)`(원장 순위 순, 겹치지 않게)."""
    uniq = {s.lower() for s in sources if s}
    ordered = sorted(uniq, key=lambda s: (SOURCE_RANK.get(s, 9), s))
    return "+".join(_SOURCE_NAMES.get(s, s.upper()) for s in ordered)


def sum_known(values: Iterable[int | None]) -> int | None:
    """값이 있는 것만 더한다. 하나도 없으면 None — 없는 날을 0 으로 세지 않는다(ET
    `monitor/flow/analyze.py:_sum`)."""
    total = 0
    seen = False
    for v in values:
        if v is not None:
            total += v
            seen = True
    return total if seen else None


# ── 투자자 값 ────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InvestorValues:
    """(종목 또는 시장, 날짜) 하나의 투자자 값 — 원장 행과 시장 합계가 같은 규칙을 쓴다."""

    foreign: int | None
    inst: int | None
    other_corp: int | None
    indiv: int | None
    inst7: Mapping[Investor, int] | None
    qualities: tuple[Quality, ...]
    sources: tuple[str, ...]
    invalid: bool
    notes: tuple[str, ...]


def investor_values(rows: Mapping[Investor, InvestorDay]) -> InvestorValues:
    """투자자별로 이미 고른 행(투자자 → 한 행)에서 4구분·7구분 값을 만든다.

    invalid 행의 값은 쓰지 않는다(그 구분은 None, `invalid=True`). 외국인은 외국인 + 기타외국인
    (기타외국인 행에 금액이 없으면 외국인 합을 모른다 — None).
    """
    notes: list[str] = []
    qualities: list[Quality] = []
    sources: list[str] = []
    invalid = False

    def get(inv: Investor) -> int | None:
        nonlocal invalid
        r = rows.get(inv)
        if r is None:
            return None
        if not r.quality.usable:
            invalid = True
            return None
        if r.net_value is not None:
            qualities.append(r.quality)
            sources.append(r.source)
        return r.net_value

    foreign = get(Investor.FOREIGN)
    if Investor.FOREIGN_OTHER in rows:
        fo = get(Investor.FOREIGN_OTHER)
        if fo is None:
            foreign = None
            notes.append("기타외국인 금액 없음 — 외국인 합을 모른다")
        elif foreign is not None:
            foreign += fo
    inst = get(Investor.INSTITUTION)
    other_corp = get(Investor.OTHER_CORP)
    indiv = get(Investor.INDIVIDUAL)
    seven = {i: get(i) for i in INST7}
    have = {i: v for i, v in seven.items() if v is not None}
    inst7: dict[Investor, int] | None = None
    if len(have) == len(INST7):
        inst7 = have
    elif have:
        notes.append(f"기관 7구분 일부만 있음({len(have)}/7) — 7구분 없음으로 본다")
    return InvestorValues(
        foreign=foreign,
        inst=inst,
        other_corp=other_corp,
        indiv=indiv,
        inst7=inst7,
        qualities=tuple(qualities),
        sources=tuple(dict.fromkeys(sources)),
        invalid=invalid,
        notes=tuple(notes),
    )


# ── 원장 행 ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LedgerRow:
    """원장 한 행. 금액은 원 단위 정수(metrics §0). 값을 모르면 None(0 으로 채우지 않는다)."""

    code: str
    date: date
    name: str | None
    market: str | None
    kind: str | None
    close: float | None
    chg_pct: float | None
    volume: int | None
    turnover: int | None
    mktcap: int | None
    foreign: int | None
    inst: int | None
    other_corp: int | None
    indiv: int | None
    inst7: Mapping[Investor, int] | None
    traded: bool
    flags: tuple[str, ...] | None  # None = 상태를 모른다(metrics §3 n_status_unknown)
    quality: Quality
    sources: Mapping[str, str] = field(default_factory=dict[str, str])
    notes: tuple[str, ...] = ()
    failed_checks: tuple[str, ...] = ()  # 검산 실패 id(c1·c2) — `checks.apply_checks` 가 채운다

    @property
    def usable(self) -> bool:
        return self.quality.usable

    @property
    def flagged(self) -> bool | None:
        """관리종목·거래정지·정리매매(`EXCLUDE_FLAGS`)인가. 상태를 모르면 None."""
        if self.flags is None:
            return None
        return bool(EXCLUDE_FLAGS & set(self.flags))

    def value(self, who: Who) -> int | None:
        return getattr(self, who)

    def invalidated(self, note: str, *, failed_checks: Sequence[str] = ()) -> LedgerRow:
        """invalid 로 바꾼 새 행(사유를 notes 에, 실패한 검산 id 를 failed_checks 에)."""
        return replace(
            self,
            quality=Quality.INVALID,
            notes=(*self.notes, note),
            failed_checks=(*self.failed_checks, *failed_checks),
        )


@dataclass(frozen=True)
class WindowAgg:
    """종목 하나의 기간 집계(최근 n 영업일, end 포함). invalid 행은 빼고 `n_invalid` 로 센다."""

    code: str
    end: date
    dates: tuple[date, ...]
    n_rows: int  # 창 안에서 행이 있는 날
    n_traded: int  # 실제 거래된 영업일(일평균 분모)
    n_invalid: int
    sum_turnover: int | None
    avg_turnover: float | None
    sums_by_investor: Mapping[str, int | None]  # WHO 4구분 + 7구분(Investor 값 이름)
    last_mktcap: int | None  # 기간 마지막 날 시총(회전율 분모)
    quality: Quality | None  # 쓴 행 중 가장 나쁜 것(쓴 행이 없으면 None)
    sources: tuple[str, ...]


# ── 원장 ────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Ledger:
    """원장. `rows` 의 키는 (code, date). `trading_days` 는 오름차순 영업일(가짜 캘린더 가능)."""

    rows: Mapping[tuple[str, date], LedgerRow]
    trading_days: tuple[date, ...]
    notes: tuple[str, ...] = ()
    checks: CheckSummary | None = None  # `checks.apply_checks` 가 채운다

    def __post_init__(self) -> None:
        days = tuple(sorted(set(self.trading_days)))
        object.__setattr__(self, "trading_days", days)
        dayset = set(days)
        by_code: dict[str, dict[date, LedgerRow]] = {}
        for (code, d), r in self.rows.items():
            if (r.code, r.date) != (code, d):
                raise ValueError(f"원장 키와 행이 다르다: {(code, d)} ≠ {(r.code, r.date)}")
            if d not in dayset:
                raise ValueError(f"영업일이 아닌 날의 행: {code} {d}")
            by_code.setdefault(code, {})[d] = r
        object.__setattr__(self, "rows", dict(self.rows))
        object.__setattr__(self, "_by_code", by_code)

    # 조회 -------------------------------------------------------------------------------

    def codes(self) -> tuple[str, ...]:
        return tuple(sorted(self._index()))

    def _index(self) -> dict[str, dict[date, LedgerRow]]:
        return self.__dict__["_by_code"]

    def get(self, code: str, d: date) -> LedgerRow | None:
        return self._index().get(code, {}).get(d)

    def series(self, code: str) -> list[LedgerRow]:
        """종목의 행 전부(오름차순)."""
        rows = self._index().get(code, {})
        return [rows[d] for d in sorted(rows)]

    def day(self, d: date) -> list[LedgerRow]:
        """그날 행 전부(코드 순)."""
        return [rows[d] for _, rows in sorted(self._index().items()) if d in rows]

    @property
    def last_day(self) -> date | None:
        return self.trading_days[-1] if self.trading_days else None

    def days_upto(self, end: date, n: int) -> tuple[date, ...]:
        """end 이하 최근 n 영업일(오름차순). 원장 영업일 밖은 없다."""
        if n < 1:
            raise ValueError("n 은 1 이상")
        upto = [d for d in self.trading_days if d <= end]
        return tuple(upto[-n:])

    def rows_desc(self, code: str, end: date, n: int | None = None) -> list[LedgerRow | None]:
        """end 이하 영업일을 최신부터 — 그날 행이 없으면 None(연속일이 끊기는 자리)."""
        days = [d for d in self.trading_days if d <= end]
        days.reverse()
        if n is not None:
            days = days[:n]
        idx = self._index().get(code, {})
        return [idx.get(d) for d in days]

    def window(self, code: str, end: date, n: int) -> WindowAgg:
        """최근 n 영업일 집계 — 기간 거래대금·일평균(정지일 제외)·투자자별 합(metrics §1·§2)."""
        dates = self.days_upto(end, n)
        present = [r for r in (self.get(code, d) for d in dates) if r is not None]
        usable = [r for r in present if r.usable]
        sum_t = sum_known(r.turnover for r in usable)
        n_traded = sum(1 for r in usable if r.traded)
        # 일평균 분모 = 거래된 날 중 거래대금을 아는 날 — 거래량만 있고 금액을 모르는 날을 분모에
        # 넣으면 그날을 0원으로 센 것과 같다(없는 날을 0 으로 세지 않는다 — ET `_sum`)
        n_avg = sum(1 for r in usable if r.traded and r.turnover is not None)
        avg = sum_t / n_avg if sum_t is not None and n_avg > 0 else None
        sums: dict[str, int | None] = {w: sum_known(r.value(w) for r in usable) for w in WHO}
        for inv in INST7:
            sums[inv.value] = sum_known(
                (r.inst7[inv] if r.inst7 is not None else None) for r in usable
            )
        end_row = self.get(code, dates[-1]) if dates else None
        last_mktcap = end_row.mktcap if end_row is not None and end_row.usable else None
        srcs = sorted({p for r in usable for s in r.sources.values() for p in s.split("+")})
        return WindowAgg(
            code=code,
            end=end,
            dates=dates,
            n_rows=len(present),
            n_traded=n_traded,
            n_invalid=len(present) - len(usable),
            sum_turnover=sum_t,
            avg_turnover=avg,
            sums_by_investor=sums,
            last_mktcap=last_mktcap,
            quality=worst_quality(r.quality for r in usable),
            sources=tuple(srcs),
        )

    def with_rows(
        self,
        rows: Iterable[LedgerRow],
        *,
        notes: Sequence[str] = (),
        checks: CheckSummary | None = None,
    ) -> Ledger:
        """행을 바꾼 새 원장(같은 키는 덮어쓴다). notes 는 덧붙인다."""
        merged = dict(self.rows)
        for r in rows:
            merged[(r.code, r.date)] = r
        return Ledger(
            rows=merged,
            trading_days=self.trading_days,
            notes=(*self.notes, *notes),
            checks=checks if checks is not None else self.checks,
        )


# ── 만들기 ──────────────────────────────────────────────────────────────────────────────


def _snap_or_bar_rank(x: Snap | Bar) -> tuple[int, ...]:
    # 같은 순위면 스냅 먼저(등락률·시총이 같은 행에서 나오게)
    return (*row_rank(x.source, x.quality, x.venue), 0 if isinstance(x, Snap) else 1)


def _first[T](
    cands: Sequence[Snap | Bar], get: Callable[[Snap | Bar], T | None]
) -> tuple[T | None, Snap | Bar | None]:
    for c in cands:
        v = get(c)
        if v is not None:
            return v, c
    return None, None


def _make_row(
    code: str,
    d: date,
    snap: Snap | None,
    bar: Bar | None,
    inv: Mapping[Investor, InvestorDay],
    uni: UniverseRow | None,
) -> LedgerRow:
    notes: list[str] = []
    qualities: list[Quality] = []
    sources: dict[str, str] = {}
    invalid = False

    cands = [x for x in (snap, bar) if x is not None]
    usable = sorted((x for x in cands if x.quality.usable), key=_snap_or_bar_rank)
    if cands and not usable:
        invalid = True
        notes.append("가격 행이 invalid 뿐")
    close, c_src = _first(usable, lambda x: x.close)
    turnover, t_src = _first(usable, lambda x: x.turnover)
    volume, v_src = _first(usable, lambda x: x.volume)
    for what, src in (("close", c_src), ("turnover", t_src), ("volume", v_src)):
        if src is not None:
            sources[what] = src.source
            qualities.append(src.quality)

    good_snap = snap if snap is not None and snap.quality.usable else None
    chg_pct = good_snap.chg_pct if good_snap is not None else None
    mktcap = good_snap.mktcap if good_snap is not None else None
    flags = good_snap.status_flags if good_snap is not None else None
    if good_snap is not None and (chg_pct is not None or mktcap is not None):
        sources["snap"] = good_snap.source
        qualities.append(good_snap.quality)
    if good_snap is not None and good_snap.turnover_is_estimate and t_src is good_snap:
        notes.append("거래대금 추정치(원천 표시)")

    # 이름·시장·종류는 신원 정보라 invalid 스냅에서도 쓴다(값이 아니다)
    name = (snap.name if snap is not None else None) or (uni.name if uni is not None else None)
    market = (snap.market if snap is not None else None) or (
        uni.market if uni is not None else None
    )
    kind = (snap.kind if snap is not None else None) or (uni.kind if uni is not None else None)

    iv = investor_values(inv)
    if iv.invalid:
        invalid = True
        notes.append("투자자 행이 invalid")
    notes.extend(iv.notes)
    qualities.extend(iv.qualities)
    if iv.sources:
        sources["flows"] = "+".join(iv.sources)

    if flags is not None and "halted" in flags:
        traded = False
    elif turnover is not None:
        traded = turnover > 0
    elif volume is not None:
        traded = volume > 0
    else:
        traded = False

    quality = Quality.INVALID if invalid else (worst_quality(qualities) or Quality.INVALID)
    if not qualities and not invalid:
        notes.append("쓸 값이 없다")
    return LedgerRow(
        code=code,
        date=d,
        name=name,
        market=market,
        kind=kind,
        close=close,
        chg_pct=chg_pct,
        volume=volume,
        turnover=turnover,
        mktcap=mktcap,
        foreign=iv.foreign,
        inst=iv.inst,
        other_corp=iv.other_corp,
        indiv=iv.indiv,
        inst7=iv.inst7,
        traded=traded,
        flags=flags,
        quality=quality,
        sources=sources,
        notes=tuple(notes),
    )


def build_ledger(
    snaps: Iterable[Snap],
    bars: Iterable[Bar],
    investors: Iterable[InvestorDay],
    universe: Iterable[UniverseRow],
    trading_days: Iterable[date],
) -> Ledger:
    """스냅·일봉·투자자 행(원천이 섞여 있어도 된다)과 영업일로 원장을 만든다.

    `investors` 는 종목 행만(시장 합계 행은 `kbj.engines.flows.totals`). `universe` 는 이름·시장·
    종류를 스냅이 없을 때 채운다.
    """
    days = tuple(sorted(set(trading_days)))
    dayset = set(days)
    uni = pick_best(
        universe,
        lambda u: u.code,
        lambda u: (-u.as_of.toordinal(), *ledger_rank(u.source, u.quality)),
    )
    best_snap = pick_best(
        snaps, lambda s: (s.code, s.date), lambda s: row_rank(s.source, s.quality, s.venue)
    )
    best_bar = pick_best(
        bars, lambda b: (b.code, b.date), lambda b: row_rank(b.source, b.quality, b.venue)
    )
    best_inv = pick_best(
        investors,
        lambda r: (r.code, r.date, r.investor),
        lambda r: row_rank(r.source, r.quality, r.venue),
    )
    inv_by: dict[tuple[str, date], dict[Investor, InvestorDay]] = {}
    for (code, d, who), r in best_inv.items():
        inv_by.setdefault((code, d), {})[who] = r

    keys = set(best_snap) | set(best_bar) | set(inv_by)
    off = sorted(k for k in keys if k[1] not in dayset)
    rows: dict[tuple[str, date], LedgerRow] = {}
    for code, d in sorted(k for k in keys if k[1] in dayset):
        rows[(code, d)] = _make_row(
            code,
            d,
            best_snap.get((code, d)),
            best_bar.get((code, d)),
            inv_by.get((code, d), {}),
            uni.get(code),
        )
    notes: list[str] = []
    if off:
        notes.append(f"영업일 밖 행 {len(off)}개를 버렸다(휴장일 — 기간 = 영업일)")
    return Ledger(rows=rows, trading_days=days, notes=tuple(notes))
