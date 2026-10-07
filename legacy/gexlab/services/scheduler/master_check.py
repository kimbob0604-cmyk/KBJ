"""마스터 ⊇ KRX 행사가 대조 (docs/phase1_design.md §5 마스터, §10 단위).

KIS 마스터(오늘 받은 것)에 KRX 전 거래일 상장 목록(`krx_opt_daily`)의 행사가가 모두 있는지 본다.

- 만기 = (상품군, KIS 6자리 만기). **둘 다에 있는 만기만** 대조하고, 그 만기의 KRX (콜풋, 행사가)가
  마스터에 없으면 빠진 것 → health(부르는 쪽, services/scheduler/krx.py)
- 개수 일치는 요구하지 않는다: 마스터에만 있는 행사가(지수가 움직여 새로 상장 — 2609W4: KRX 09-23
  106개 970.0~1232.5, 09-28 마스터 111개 970.0~1245.0)와 마스터에만 있는 만기(오늘 새로 상장된
  위클리 — KRX 전일 행이 없다)는 정상이다. KRX 에만 있는 만기(전 거래일에 만기가 지나 새 마스터에서
  빠진 시리즈)도 대조하지 않는다
- 상품군은 KRX 적재 대상(코스피200 계열, data/store.py `KRX_FAMILIES`)만. 행사가는 0.01 로 맞춰
  비교한다(마스터 `01125.00`·KRX 이름 `1,125.0`·DB numeric(8,2))
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from data.kis.master import MasterRow
from data.store import KRX_FAMILIES

Expiry = tuple[str, str]  # (상품군, 만기 6자리)
Listing = Iterable[tuple[str, str, str, Decimal]]  # (상품군, 만기, 콜풋, 행사가)

CENT = Decimal("0.01")
SHOW_SERIES = 3
SHOW_STRIKES = 3
FAMILY_KO = {
    "kospi200": "코스피200",
    "mini_kospi200": "미니",
    "kospi200_weekly_thu": "위클리(목)",
    "kospi200_weekly_mon": "위클리(월)",
}


@dataclass(frozen=True)
class SeriesGap:
    """한 시리즈(상품군·만기·콜풋)에서 마스터에 없는 KRX 행사가."""

    family: str
    expiry: str
    cp: str
    missing: tuple[Decimal, ...]

    def describe(self) -> str:
        shown = "·".join(f"{k:,.1f}" for k in self.missing[:SHOW_STRIKES])
        rest = len(self.missing) - SHOW_STRIKES
        more = f" 외 {rest}개" if rest > 0 else ""
        name = FAMILY_KO.get(self.family, self.family)
        return f"{name} {self.expiry} {self.cp} {shown}{more}"


@dataclass(frozen=True)
class MasterKrxReport:
    krx_date: date  # 대조한 KRX 거래일(BAS_DD)
    compared: int  # 둘 다에 있어 대조한 만기 수
    krx_strikes: int  # 대조한 만기의 KRX (콜풋, 행사가) 수
    gaps: tuple[SeriesGap, ...]  # 마스터에 없는 KRX 행사가 — 시리즈별
    new_strikes: int  # 대조한 만기에서 마스터에만 있는 (콜풋, 행사가) — 신규 상장, 정상
    krx_only: tuple[Expiry, ...]  # KRX 에만 있는 만기 — 만기 지남 등, 대조 안 함
    master_only: tuple[Expiry, ...]  # 마스터에만 있는 만기 — 오늘 새로 상장, 정상

    @property
    def ok(self) -> bool:
        return not self.gaps

    @property
    def missing(self) -> int:
        return sum(len(g.missing) for g in self.gaps)

    def counts(self) -> str:
        return (
            f"대조 만기 {self.compared}개·KRX 행사가 {self.krx_strikes:,}개, "
            f"신규 행사가 {self.new_strikes}개·KRX 만 있는 만기 {len(self.krx_only)}개·"
            f"마스터만 있는 만기 {len(self.master_only)}개는 정상"
        )

    def describe(self) -> str:
        if self.ok:
            return f"마스터 ⊇ KRX {self.krx_date} — {self.counts()}"
        shown = ", ".join(g.describe() for g in self.gaps[:SHOW_SERIES])
        rest = len(self.gaps) - SHOW_SERIES
        more = f" 외 {rest}개 시리즈" if rest > 0 else ""
        return (
            f"마스터에 없는 KRX {self.krx_date} 행사가 {self.missing}개(시리즈 {len(self.gaps)}개) "
            f"— {shown}{more}; {self.counts()}"
        )


def _strike(k: Decimal) -> Decimal:
    return k.quantize(CENT)


def master_covers_krx(master: Iterable[MasterRow], krx: Listing, krx_date: date) -> MasterKrxReport:
    """마스터 ⊇ KRX (모듈 설명). krx 는 `krx_option_listing` 꼴 (상품군, 만기, 콜풋, 행사가)."""
    have: dict[Expiry, set[tuple[str, Decimal]]] = {}
    for r in master:
        if r.strike is None or r.cp == "" or r.family not in KRX_FAMILIES:
            continue
        expiry = r.expiry
        if expiry is None:
            continue
        have.setdefault((r.family, expiry), set()).add((r.cp, _strike(r.strike)))
    want: dict[Expiry, set[tuple[str, Decimal]]] = {}
    for family, expiry, cp, strike in krx:
        if family in KRX_FAMILIES:
            want.setdefault((family, expiry), set()).add((cp, _strike(strike)))
    both = sorted(have.keys() & want.keys())
    gaps: list[SeriesGap] = []
    new = 0
    for key in both:
        lost = want[key] - have[key]
        new += len(have[key] - want[key])
        for cp in sorted({c for c, _ in lost}):
            ks = tuple(sorted(k for c, k in lost if c == cp))
            gaps.append(SeriesGap(key[0], key[1], cp, ks))
    return MasterKrxReport(
        krx_date=krx_date,
        compared=len(both),
        krx_strikes=sum(len(want[k]) for k in both),
        gaps=tuple(gaps),
        new_strikes=new,
        krx_only=tuple(sorted(want.keys() - have.keys())),
        master_only=tuple(sorted(have.keys() - want.keys())),
    )
