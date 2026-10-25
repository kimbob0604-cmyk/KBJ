"""합성 P6 세계 `p6_world` — P6 묶음(COL·FLASH·EXP·MAP·WRAP)이 같이 쓰는 고정 시드 합성 데이터.

근거: docs/p6_design.md §1.1(FND 표 `p6_world` 행)·§8·부록 A(metrics §25~§29).
관세청 자료는 공개 등급이지만 **본 브랜치·픽스처에는 합성만**(사용자 결정 2026-10-13 Q3 —
실집계는 public-data 브랜치에만). 이 생성기가 `random.Random(f"{seed}:{이름}")` 으로 결정적으로
만든다(같은 시드 → 같은 `digest()`). **실데이터 아님** — HS·국가·시군구 이름은 전부 '가상'.
행 객체(`kbj.core.trade_rows`)의 `source` 칸은 저장소·DB 제약이 받는 출처 이름(`DATAGO:<포털 ID>`)을
쓰지만 값은 전부 합성이다.

쓰는 법
    from tests.fixtures.synthetic.p6_world import build_world
    w = build_world()                    # 시드 20261211(캐시 — 같은 객체)
    w.items                              # ItemMonthRow — HS2·4·6·10 × 60개월
    w.fill(repo)                         # TradeRepo(메모리·Pg)에 전부 쓴다
    w.revenue("994010")                  # 분기 매출(YYYYQn, 원 — p4_world 재사용)

무엇이 있나(설계 §1.1 — 손계산 기대값은 `p6_expect.yaml`)
- HS 계층: 가상 장 12(실제 2자리 번호, 이름은 '가상') × HS4 2 × HS6 2 × HS10 1(850110 만 2).
  상위·하위 행이 함께 온다(응답처럼). 60개월(`FIRST_YM`~`LAST_YM`) 수출·수입 금액·중량.
- 심은 것: 급등(HS4 8502 — 최근 3개월 ×2, 평평한 값이라 YoY 정확히 +100%)·급락(2701 — ×0.5)·
  중량 0(8401100000 마지막 달)·결측(390120 과 그 HS10 이 2026-06 에 없음 — 상위 행은 그대로).
- 국가 40(ISO2 실제 부호, 이름 '가상국-XX') — 국가 합 = HS2 합(달마다).
  품목×국가 = HS4 × 상위 10개국.
- 시도 18(2026-07 개편 — 12 신설) × 가상 시군구, 매핑 HS6 만.
  개편: 'KR-46-a01' 이 6월까지 46, 7월부터 12.
- 10일 잠정치 4종 × 2016-12~2026-12(오늘 공표된 12월 1~10일까지) × 11열.
  정정 1건(exp_item 2026-11 seg 3 col 0 — first_usd ≠ usd),
  열 이름 바뀜 1건(exp_country col 10 — 2025-01 부터 '가상국-10').
- 합성 상장사 80(p4_world 20 `994xxx` + 새 60 `9920xx` — 다른 합성 대역과 겹치지 않는다) +
  합성 관심목록 50 + 매핑표(공개 30 — 관심목록과 따로 고름, `origin: local` 3 — 관심목록 종목) +
  분기 매출(p4_world 의 분기 참값을 수정 없이 import, 나머지는 합성).
  심은 상태: no_trade(장 99 접두)·no_revenue(994015 — DART 없음)·short_overlap(분기 6개뿐).

가짜 시계: `CLOCK` = 2026-12-11(금) 11:30 KST — 12월 1~10일 잠정치 공표일, 월간 마지막 달 2026-10.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from functools import cache
from typing import Any, Final

from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.core.trade_rows import (
    CountryMonthRow,
    Flow,
    HsDim,
    ItemCountryMonthRow,
    ItemMonthRow,
    MapOrigin,
    SigunguMonthRow,
    StockMapRow,
    TenDayColumn,
    TenDayRow,
    prev_ym,
)

SEED: Final = 20261211
CLOCK: Final = datetime(2026, 12, 11, 11, 30, tzinfo=KST)
FIRST_YM: Final = "2021-11"
LAST_YM: Final = "2026-10"
N_MONTHS: Final = 60
TEN_FIRST_YM: Final = "2016-12"
TEN_LAST_YM: Final = "2026-12"  # 오늘(12-11) 1~10일 구간만 공표
SYN: Final = (
    "SYNTHETIC — tests/fixtures/synthetic/p6_world.py(고정 시드 생성기)가 만든 합성 데이터."
    " 실측 아님"
)
MAP_VERSION: Final = "p6world-1"

CHAPTERS: Final[tuple[str, ...]] = (
    "03",
    "27",
    "29",
    "30",
    "39",
    "72",
    "84",
    "85",
    "87",
    "89",
    "90",
    "94",
)
SURGE_HS4: Final = "8502"
PLUNGE_HS4: Final = "2701"
ZERO_KG_HS10: Final = "8401100000"
MISSING_HS6: Final = "390120"
MISSING_YM: Final = "2026-06"
PLANT_FROM: Final = "2026-08"  # 급등·급락을 심은 첫 달(마지막 3개월)
REVISION: Final = ("exp_item", "2026-11", 3, 0)
COLUMN_CHANGE: Final = ("exp_country", 10, "2025-01", "가상국-10")
COUNTRIES: Final[tuple[str, ...]] = (
    "CN",
    "US",
    "VN",
    "HK",
    "JP",
    "TW",
    "IN",
    "SG",
    "MY",
    "MX",
    "AU",
    "DE",
    "ID",
    "PH",
    "TH",
    "CA",
    "PL",
    "TR",
    "GB",
    "NL",
    "AE",
    "SA",
    "BR",
    "HU",
    "FR",
    "IT",
    "ES",
    "CZ",
    "SK",
    "BE",
    "RU",
    "CL",
    "PE",
    "NZ",
    "BD",
    "PK",
    "EG",
    "ZA",
    "KZ",
    "UZ",
)
SIDO: Final[tuple[str, ...]] = (
    "11",
    "12",
    "26",
    "27",
    "28",
    "29",
    "30",
    "31",
    "36",
    "41",
    "43",
    "44",
    "46",
    "47",
    "48",
    "50",
    "51",
    "52",
)
REORG_REGION: Final = "KR-46-a01"
REORG_FROM: Final = "2026-07"
P4_CODES_FROM: Final = "994"
NEW_CODES: Final[tuple[str, ...]] = tuple(f"9920{n:02d}" for n in range(1, 61))
NO_TRADE_CODE: Final = "992060"  # 매핑 접두가 세계에 없는 장(99)
NO_REVENUE_CODE: Final = "994015"  # p4_world — DART 고유번호 없음
SHORT_CODE: Final = "992059"  # 분기 매출 6개뿐


def _rng(seed: int, *parts: object) -> random.Random:
    return random.Random(f"{seed}:" + ":".join(str(p) for p in parts))  # noqa: S311 — 합성 데이터


def _months(first: str, n: int) -> tuple[str, ...]:
    return tuple(prev_ym(first, -i) for i in range(n))


MONTHS: Final = _months(FIRST_YM, N_MONTHS)


@dataclass(frozen=True)
class Region:
    id: str
    sido_cd: str
    sgg_nm: str
    valid_from: str | None = None
    valid_to: str | None = None


@dataclass(frozen=True)
class P6World:
    seed: int
    clock: datetime
    months: tuple[str, ...]
    hs: tuple[HsDim, ...]
    items: tuple[ItemMonthRow, ...]
    countries: tuple[CountryMonthRow, ...]
    item_countries: tuple[ItemCountryMonthRow, ...]
    regions: tuple[Region, ...]
    sigungu: tuple[SigunguMonthRow, ...]
    ten_day: tuple[TenDayRow, ...]
    ten_day_columns: tuple[TenDayColumn, ...]
    stocks: tuple[tuple[str, str], ...]  # (코드, 이름)
    watchlist: tuple[str, ...]
    stock_map: tuple[StockMapRow, ...]
    _revenue: Mapping[str, tuple[tuple[str, int], ...]]

    def revenue(self, code: str) -> tuple[tuple[str, int], ...]:
        """분기 매출 [(YYYYQn, 원)] — 오름차순. 재무 미제출 종목은 빈 튜플."""
        return self._revenue.get(code, ())

    def map_codes(self, origin: str = "public") -> tuple[str, ...]:
        return tuple(sorted({r.code for r in self.stock_map if r.origin == origin}))

    def records(self) -> Iterator[dict[str, Any]]:
        for kind, seq in (
            ("hs", self.hs),
            ("item", self.items),
            ("country", self.countries),
            ("item_country", self.item_countries),
            ("region", self.regions),
            ("sigungu", self.sigungu),
            ("ten_day", self.ten_day),
            ("ten_day_columns", self.ten_day_columns),
            ("stock_map", self.stock_map),
        ):
            for r in seq:
                yield {"_source": SYN, "_kind": kind, **asdict(r)}
        yield {"_source": SYN, "_kind": "stocks", "rows": self.stocks, "watch": self.watchlist}
        yield {"_source": SYN, "_kind": "revenue", "rows": dict(sorted(self._revenue.items()))}

    def digest(self) -> str:
        """세계 전체의 sha256(같은 시드 → 같은 값)."""
        h = hashlib.sha256()
        for rec in self.records():
            h.update(json.dumps(rec, ensure_ascii=False, sort_keys=True, default=str).encode())
        return h.hexdigest()

    def fill(self, repo: Any, *, loaded_by: str = "p6_world") -> None:
        """`TradeRepo`(메모리·Pg)에 세계를 쓴다 — 매핑은 origin 마다 통째로."""
        at = self.clock
        repo.upsert_ten_day_columns(self.ten_day_columns, loaded_by=loaded_by)
        repo.upsert_ten_day(self.ten_day, loaded_by=loaded_by, received_at=at)
        repo.upsert_item_monthly(self.items, loaded_by=loaded_by, received_at=at)
        repo.upsert_country_monthly(self.countries, loaded_by=loaded_by, received_at=at)
        repo.upsert_item_country_monthly(self.item_countries, loaded_by=loaded_by, received_at=at)
        repo.upsert_sigungu_monthly(self.sigungu, loaded_by=loaded_by, received_at=at)
        repo.upsert_hs_dim(self.hs, loaded_by=loaded_by, received_at=at)
        for origin in ("public", "local"):
            rows = [r for r in self.stock_map if r.origin == origin]
            repo.replace_stock_map(rows, origin=origin, loaded_by=loaded_by, received_at=at)


# ── HS 계층·품목 월간 ──────────────────────────────────────────────────────────────────────


def _tree() -> tuple[
    tuple[str, tuple[tuple[str, tuple[tuple[str, tuple[str, ...]], ...]], ...]], ...
]:
    out = []
    for ch in CHAPTERS:
        hs4s = []
        for a in ("01", "02"):
            hs4 = ch + a
            hs6s = []
            for b in ("10", "20"):
                hs6 = hs4 + b
                leaves = (hs6 + "1000", hs6 + "2000") if hs6 == "850110" else (hs6 + "0000",)
                hs6s.append((hs6, leaves))
            hs4s.append((hs4, tuple(hs6s)))
        out.append((ch, tuple(hs4s)))
    return tuple(out)


def _leaves() -> tuple[str, ...]:
    return tuple(lf for _, h4 in _tree() for _, h6 in h4 for _, lfs in h6 for lf in lfs)


def _leaf_value(seed: int, leaf: str, ym: str, flow: str) -> tuple[int, int]:
    """(달러, kg) — 심은 품목은 평평한 값(손계산 기대값이 정확히 나오게)."""
    i = MONTHS.index(ym)
    if leaf.startswith((SURGE_HS4, PLUNGE_HS4)):
        base = 30_000_000 if flow == "exp" else 4_000_000
        if ym >= PLANT_FROM:
            base = base * 2 if leaf.startswith(SURGE_HS4) else base // 2
        return base, base // 10
    r = _rng(seed, "leaf", leaf, flow)
    level = r.uniform(2e6, 4e8) * (1.0 if flow == "exp" else 0.6)
    price = r.uniform(2.0, 60.0)
    trend = r.uniform(-0.004, 0.012)
    noise = _rng(seed, "noise", leaf, flow, ym).uniform(0.85, 1.15)
    season = 1.0 + 0.06 * ((i % 12) - 5.5) / 5.5
    usd = int(level * (1 + trend) ** i * noise * season)
    return usd, int(usd / price)


def _make_items(seed: int) -> tuple[tuple[ItemMonthRow, ...], tuple[HsDim, ...]]:
    rows: list[ItemMonthRow] = []
    names: dict[str, str] = {}
    for ym in MONTHS:
        for ch, hs4s in _tree():
            ch_tot = [0, 0, 0, 0]
            ch_rows: list[ItemMonthRow] = []
            for hs4, hs6s in hs4s:
                h4 = [0, 0, 0, 0]
                for hs6, leaves in hs6s:
                    h6 = [0, 0, 0, 0]
                    missing = hs6 == MISSING_HS6 and ym == MISSING_YM
                    for lf in leaves:
                        eu, ek = _leaf_value(seed, lf, ym, "exp")
                        iu, ik = _leaf_value(seed, lf, ym, "imp")
                        if lf == ZERO_KG_HS10 and ym == LAST_YM:
                            ek = 0
                        h6 = [h6[0] + eu, h6[1] + ek, h6[2] + iu, h6[3] + ik]
                        if not missing:
                            ch_rows.append(_item(ym, lf, eu, ek, iu, ik))
                    if not missing:
                        ch_rows.append(_item(ym, hs6, *h6))
                    h4 = [a + b for a, b in zip(h4, h6, strict=True)]
                ch_rows.append(_item(ym, hs4, *h4))
                ch_tot = [a + b for a, b in zip(ch_tot, h4, strict=True)]
            ch_rows.append(_item(ym, ch, *ch_tot))
            rows += ch_rows
    for ch, hs4s in _tree():
        names[ch] = f"가상 장 {ch}"
        for hs4, hs6s in hs4s:
            names[hs4] = f"가상품목 {hs4}"
            for hs6, leaves in hs6s:
                names[hs6] = f"가상품목 {hs6}"
                for lf in leaves:
                    names[lf] = f"가상품목 {lf}"
    dims = tuple(
        HsDim(
            hs=h,
            hs_len=len(h),
            name_ko=n,
            first_ym=FIRST_YM,
            last_ym=LAST_YM,
            source="DATAGO:15101609",
            as_of=CLOCK,
            quality=Quality.OK,
        )
        for h, n in sorted(names.items())
    )
    rows.sort(key=lambda r: (r.ym, r.hs))
    return tuple(rows), dims


def _item(ym: str, hs: str, eu: int, ek: int, iu: int, ik: int) -> ItemMonthRow:
    return ItemMonthRow(
        ym=ym,
        hs=hs,
        hs_len=len(hs),
        exp_usd=Decimal(eu),
        exp_kg=Decimal(ek),
        imp_usd=Decimal(iu),
        imp_kg=Decimal(ik),
        bal_usd=Decimal(eu - iu),
        source="DATAGO:15101609",
        as_of=CLOCK,
        quality=Quality.OK,
    )


# ── 국가·품목×국가 ──────────────────────────────────────────────────────────────────────────


def _shares(seed: int, key: str, n: int) -> list[float]:
    r = _rng(seed, "share", key)
    w = [r.paretovariate(1.3) for _ in range(n)]
    s = sum(w)
    return [x / s for x in w]


def _split(total: int, shares: Sequence[float]) -> list[int]:
    parts = [int(total * s) for s in shares]
    parts[0] += total - sum(parts)  # 반올림 나머지는 첫 칸 — 합이 정확히 맞게
    return parts


def _make_countries(
    seed: int, items: Sequence[ItemMonthRow]
) -> tuple[tuple[CountryMonthRow, ...], tuple[ItemCountryMonthRow, ...]]:
    out: list[CountryMonthRow] = []
    ic: list[ItemCountryMonthRow] = []
    sh = _shares(seed, "country", len(COUNTRIES))
    by = {(r.ym, r.hs): r for r in items}
    # 품목×국가: HS4 마다 상위 10개국 몫(나머지 국가는 응답 밖)
    ic_share = {h4: _shares(seed, f"ic:{h4}", 12)[:10] for _, hs4s in _tree() for h4, _ in hs4s}
    hs2_by_ym: dict[str, list[ItemMonthRow]] = {}
    for r in items:
        if r.hs_len == 2:
            hs2_by_ym.setdefault(r.ym, []).append(r)
    for ym in MONTHS:
        hs2 = hs2_by_ym[ym]
        exp = sum(int(r.exp_usd or 0) for r in hs2)
        imp = sum(int(r.imp_usd or 0) for r in hs2)
        for cc, e, i in zip(COUNTRIES, _split(exp, sh), _split(imp, sh[::-1]), strict=True):
            out.append(
                CountryMonthRow(
                    ym=ym,
                    country=cc,
                    country_name=f"가상국-{cc}",
                    exp_cnt=e // 50_000 + 1,
                    exp_usd=Decimal(e),
                    imp_cnt=i // 80_000 + 1,
                    imp_usd=Decimal(i),
                    bal_usd=Decimal(e - i),
                    source="DATAGO:15101612",
                    as_of=CLOCK,
                    quality=Quality.OK,
                )
            )
        for _, hs4s in _tree():
            for hs4, _ in hs4s:
                row = by[(ym, hs4)]
                top = ic_share[hs4]
                for cc, s in zip(COUNTRIES[:10], top, strict=True):
                    e, i = int(int(row.exp_usd or 0) * s), int(int(row.imp_usd or 0) * s)
                    k = int(int(row.exp_kg or 0) * s)
                    ic.append(
                        ItemCountryMonthRow(
                            ym=ym,
                            country=cc,
                            hs=hs4,
                            hs_len=4,
                            exp_usd=Decimal(e),
                            exp_kg=Decimal(k),
                            imp_usd=Decimal(i),
                            imp_kg=None,
                            bal_usd=Decimal(e - i),
                            source="DATAGO:15100475",
                            as_of=CLOCK,
                            quality=Quality.OK,
                        )
                    )
    return tuple(out), tuple(ic)


# ── 시군구(매핑 HS6 만 — 개편 대응) ──────────────────────────────────────────────────────────


def _make_regions() -> tuple[Region, ...]:
    out: list[Region] = []
    for sido in SIDO:
        if sido in ("12", "46"):
            continue
        out.append(Region(id=f"KR-{sido}-a01", sido_cd=sido, sgg_nm=f"가상시{sido}A"))
    out.append(Region(id=REORG_REGION, sido_cd="46", sgg_nm="가상군갑", valid_to="2026-06"))
    out.append(Region(id=REORG_REGION, sido_cd="12", sgg_nm="가상군갑", valid_from=REORG_FROM))
    out.append(Region(id="KR-12-a02", sido_cd="12", sgg_nm="가상시을", valid_from=REORG_FROM))
    return tuple(out)


def _make_sigungu(
    seed: int, regions: Sequence[Region], hs6s: Sequence[str]
) -> tuple[SigunguMonthRow, ...]:
    out: list[SigunguMonthRow] = []
    for reg in regions:
        for hs6 in hs6s:
            r = _rng(seed, "sgg", reg.id, hs6)
            level = r.uniform(1e5, 5e7)
            for ym in MONTHS:
                noise = r.uniform(0.8, 1.2)  # 달마다 한 번(결정적 — 순서 고정)
                if (reg.valid_from and ym < reg.valid_from) or (reg.valid_to and ym > reg.valid_to):
                    continue
                # 시군구 API 는 천 달러 단위다 — 가짜 서버가 1000 으로 나눠 낸다
                e = int(level * noise) // 1000 * 1000
                i = e // 3 // 1000 * 1000
                out.append(
                    SigunguMonthRow(
                        ym=ym,
                        sido_cd=reg.sido_cd,
                        sgg_nm=reg.sgg_nm,
                        hs6=hs6,
                        region_id=reg.id,
                        exp_usd=Decimal(e),
                        exp_cnt=e // 40_000 + 1,
                        imp_usd=Decimal(i),
                        imp_cnt=i // 40_000 + 1,
                        bal_usd=Decimal(e - i),
                        source="DATAGO:15134343",
                        as_of=CLOCK,
                        quality=Quality.OK,
                    )
                )
    out.sort(key=lambda x: (x.ym, x.sido_cd, x.sgg_nm, x.hs6))
    return tuple(out)


# ── 10일 잠정치 ────────────────────────────────────────────────────────────────────────────

_TEN_SOURCE: Final = {
    "exp_item": "DATAGO:15157908",
    "exp_country": "DATAGO:15157941",
    "imp_item": "DATAGO:15157901",
    "imp_country": "DATAGO:15157909",
}
_SEG_SHARE: Final = {1: 0.31, 2: 0.64, 3: 1.0}


def _last_day(ym: str) -> int:
    y, m = int(ym[:4]), int(ym[5:])
    nxt = (y + (m == 12), m % 12 + 1)
    from datetime import date, timedelta

    return (date(nxt[0], nxt[1], 1) - timedelta(days=1)).day


def _make_ten_day(seed: int) -> tuple[tuple[TenDayRow, ...], tuple[TenDayColumn, ...]]:
    from kbj.data.public.customs.models import TEN_DAY_COLUMNS

    cols: list[TenDayColumn] = []
    for kind, names in TEN_DAY_COLUMNS.items():
        for no, name in enumerate(names):
            cols.append(
                TenDayColumn(
                    kind=kind, col_no=no, valid_from=TEN_FIRST_YM, name=name, seen_at=CLOCK
                )
            )
    ck, cno, cfrom, cname = COLUMN_CHANGE
    cols.append(TenDayColumn(kind=ck, col_no=cno, valid_from=cfrom, name=cname, seen_at=CLOCK))
    rows: list[TenDayRow] = []
    n = (int(TEN_LAST_YM[:4]) - int(TEN_FIRST_YM[:4])) * 12 + int(TEN_LAST_YM[5:]) - 11
    for ym in _months(TEN_FIRST_YM, n):
        for kind, names in TEN_DAY_COLUMNS.items():
            r = _rng(seed, "ten", kind)
            base = r.uniform(4.0e10, 6.0e10) * (1.0 if kind.startswith("exp") else 0.9)
            i = (int(ym[:4]) - 2016) * 12 + int(ym[5:])
            month_total = base * (1.004**i) * _rng(seed, "tenm", kind, ym).uniform(0.9, 1.1)
            col_sh = _shares(seed, f"tencol:{kind}", 11)[1:]
            segs = (1,) if ym == TEN_LAST_YM else (1, 2, 3)
            for seg in segs:
                tot = int(month_total * _SEG_SHARE[seg] / 1000) * 1000
                vals = [tot] + [int(tot * 0.7 * s / 1000) * 1000 for s in col_sh]
                span = {1: "01~10", 2: "01~20"}.get(seg, f"01~{_last_day(ym)}")
                for no, v in enumerate(vals):
                    name = names[no]
                    if (kind, no) == (ck, cno) and ym >= cfrom:
                        name = cname
                    first = v
                    revised = None
                    if (kind, ym, seg, no) == REVISION:
                        first = v - 120_000_000  # 발표 당시 값(정정 전)
                        revised = datetime(2026, 12, 2, 10, 0, tzinfo=KST)
                    rows.append(
                        TenDayRow(
                            kind=kind,
                            ym=ym,
                            seg=seg,
                            col_no=no,
                            name=name,
                            usd=Decimal(v),
                            first_usd=Decimal(first),
                            first_seen_at=datetime(int(ym[:4]), int(ym[5:]), 1, tzinfo=KST),
                            span_text=span,
                            source=_TEN_SOURCE[kind],
                            as_of=CLOCK,
                            quality=Quality.ESTIMATED,
                            revised_at=revised,
                        )
                    )
    return tuple(rows), tuple(cols)


# ── 상장사·관심목록·매핑·분기 매출 ─────────────────────────────────────────────────────────


def _make_stocks(
    seed: int,
) -> tuple[tuple[tuple[str, str], ...], Mapping[str, tuple[tuple[str, int], ...]]]:
    from tests.fixtures.synthetic.p4_world import build_world as build_p4

    p4 = build_p4()
    stocks: list[tuple[str, str]] = []
    rev: dict[str, tuple[tuple[str, int], ...]] = {}
    for s in p4.stocks:
        stocks.append((s.code, s.name))
        if s.corp_code is None:
            continue
        fs = p4.primary_fs(s.corp_code)
        if fs is None:
            continue
        q = p4.quarter_truth(s.corp_code, fs)
        rev[s.code] = tuple((f"{t.period.year}Q{t.period.q}", t.revenue) for t in q)
    for code in NEW_CODES:
        stocks.append((code, f"가상상장{code[-2:]}"))
        r = _rng(seed, "rev", code)
        level = r.uniform(2e10, 8e11)
        periods = [f"{y}Q{q}" for y in range(2020, 2027) for q in range(1, 5)][:-1]  # ~2026Q3
        if code == SHORT_CODE:
            periods = periods[-6:]
        rev[code] = tuple(
            (p, int(level * (1 + r.uniform(-0.05, 0.08)) ** k)) for k, p in enumerate(periods)
        )
    return tuple(stocks), rev


def _make_map(
    seed: int, stocks: Sequence[tuple[str, str]], watch: Sequence[str], regions: Sequence[Region]
) -> tuple[StockMapRow, ...]:
    r = _rng(seed, "map")
    names = dict(stocks)
    pool = [c for c, _ in stocks if c not in (NO_TRADE_CODE,)]
    public = [*sorted(r.sample(pool, 29)), NO_TRADE_CODE]
    local = sorted(r.sample([c for c in watch if c not in public], 3))
    leaves = _leaves()
    hs6s = sorted({lf[:6] for lf in leaves})
    region_ids = sorted({g.id for g in regions})
    out: list[StockMapRow] = []
    origins: tuple[tuple[MapOrigin, list[str]], ...] = (("public", public), ("local", local))
    for origin, codes in origins:
        for code in codes:
            rr = _rng(seed, "map", code)
            lines: list[tuple[str, Flow, str, float, tuple[str, ...]]] = []
            if code == NO_TRADE_CODE:
                lines.append(("9901", "exp", "", 0.4, ()))
            else:
                for _ in range(rr.randint(1, 3)):
                    lf = rr.choice(leaves)
                    hs = lf[: rr.choice((2, 4, 6, 10))]
                    cs = tuple(sorted(rr.sample(COUNTRIES[:10], rr.randint(0, 2))))
                    if all(hs != x[0] for x in lines):
                        lines.append((hs, "exp", "", round(rr.uniform(0.05, 0.3), 2), cs))
                if rr.random() < 0.4:
                    lines.append((rr.choice(hs6s), "exp", rr.choice(region_ids), 0.1, ()))
            for hs, flow, reg, w, cs in lines:
                out.append(
                    StockMapRow(
                        code=code,
                        hs=hs,
                        flow=flow,
                        region_id=reg,
                        name=names[code],
                        weight=w,
                        origin=origin,
                        reviewed=False,
                        map_version=MAP_VERSION,
                        source="KBJ:trade_map",
                        as_of=CLOCK,
                        quality=Quality.OK,
                        countries=cs,
                        evidence=({"kind": "synthetic", "note": "합성 근거(실측 아님)"},),
                    )
                )
    return tuple(out)


@cache
def build_world(seed: int = SEED) -> P6World:
    """합성 세계(캐시 — 같은 시드는 같은 객체). 시드가 다르면 값이 달라진다 —
    구조(계층·심은 것)는 같다."""
    items, dims = _make_items(seed)
    countries, item_countries = _make_countries(seed, items)
    stocks, rev = _make_stocks(seed)
    watch = tuple(sorted(_rng(seed, "watch").sample([c for c, _ in stocks], 50)))
    regions = _make_regions()
    smap = _make_map(seed, stocks, watch, regions)
    map_hs6 = sorted({m.hs for m in smap if m.region_id})
    ten, cols = _make_ten_day(seed)
    return P6World(
        seed=seed,
        clock=CLOCK,
        months=MONTHS,
        hs=dims,
        items=items,
        countries=countries,
        item_countries=item_countries,
        regions=regions,
        sigungu=_make_sigungu(seed, regions, map_hs6),
        ten_day=ten,
        ten_day_columns=cols,
        stocks=stocks,
        watchlist=watch,
        stock_map=smap,
        _revenue=rev,
    )
