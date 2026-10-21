"""관세청 수출입 수집 — `trade.customs_tenday`·`trade.customs_monthly` → `pub_trade.*`(공개).

근거: docs/p6_design.md §1.2(묶음 COL)·§3.2~§3.4, D-P6-6·10·11·12·13, metrics §25·§26.
어댑터 `kbj.data.public.customs`(공공데이터포털 XML — 공개 등급, DATA_TIERS §1).

**10일 잠정치**(`tenday` — 데이터 키 `DATAGO:<15157908|941|901|909>@YYYYMM-n`)
- 폴링마다 `strtYymm = 이번 달 − tenday.lookback_months` ~ 이번 달을 받는다(정정 반영).
- 금액은 응답 천 달러 × 1,000(Decimal — 달러 정수, D-P6-11).
- **새 칸·바뀐 칸만** 쓴다. 처음 본 값(`first_usd`·`first_seen_at`)은 저장소가 덮지 않는다(D-P6-12);
  값이 바뀌면 `revised_at = 지금`. 값이 빈 칸(None)은 invalid 로 쓰되 있는 값을 지우지 않는다.
- 열 번호 → 이름 대응은 어댑터 `TEN_DAY_COLUMNS`. 저장한 마지막 이름과 다르면(코드 표를 고친 경우)
  `ten_day_columns` 에 새 이력을 남긴다. 응답 열 구성이 바뀌면 어댑터가 `TenDayColumnsChanged` —
  **그 종류(키)만 실패**, 나머지 종류는 계속(D-P6-13, 옛 값 유지).
- 데이터 키의 구간(그 달 seg n 의 전체 열 `00`)이 아직 없으면 `not_ready` — 20분 뒤 다시 본다.
  받은 다른 구간·정정은 그래도 쓴다.

**월간**(`monthly` — 데이터 키 `DATAGO:<15101609|15101612|15100475|15134343>@YYYY-MM`)
- 15101609 품목: `monthly.items_mode` — `by_chapter`(HS2 장마다 `hsSgn`, 장 목록은
  `knowledge/hs_chapters.yaml` — 비면 01~97) 또는 `all`(`hsSgn` 생략 한 번 — 잘리면 장별로 다시).
  응답 자릿수 그대로(`hs_len`) + `hs_dim`(처음·마지막 본 달) [실측 필요 #2·#3].
- 15101612 국가: 한 번.
- 15100475 품목×국가: **국가 목록 = 같은 달 15101612 에서 받은 국가**(국가 필수 파라미터 —
  코드표 전체 대신 실제 거래가 있는 국가만 부른다 [확인 필요: 코드표 국가 수 약 269 — #57]).
  같은 실행에서 국가 키를 먼저 처리한다. 국가 행이 아직 없으면 `not_ready`.
- 15134343 시군구: 매핑표(공개 + VM local)의 지역 행 HS6 × 그 달에 쓸 수 있는 시도(2026-07 개편 —
  `SIDO_CODES`). 시군구 이름 → 통합 지역 ID 는 `knowledge/trade_regions.yaml`(못 찾으면 None).
  매핑 HS6 가 없으면 `skipped`.
- 그 달 행이 하나도 없으면 공표 전(`not_ready`). 합계 행(기간·코드 없음)은 버린다(합계를 행으로
  두지 않는다 — 엔진이 접두로 합산, metrics §26).
- 응답이 잘리면(`CustomsTruncated` — 페이징 없음, 실측 #1) 그 키는 실패(조용히 줄이지 않는다).

**백필**(`python -m kbj.services.collectors.customs backfill --from YYYY-MM --to YYYY-MM`)
- 등록부 작업이 아니다(스케줄러 backfill 은 거래일 단위). 12개월 창(15100475 기간 한도)으로
  데이터셋마다 부르고, 창 안의 달이 그 표에 이미 다 있으면 건너뛴다(선점 키 = 창의 달들 — 다음 날
  다시 돌리면 이어서 받는다). 부르기 전에 그 창의 예상 호출 수를 더해 데이터셋 하루 한도
  (`limits.yaml datago.daily_cap_per_dataset`) × `backfill.stop_at_ratio` 를 넘으면 그 데이터셋은
  멈춘다(다른 데이터셋은 계속). 포털 게이트웨이가 한도 초과로 끊어도(`DatagoQuotaExceeded`) 같은
  멈춤(종료 0 — 내일 다시). 호출·형식 오류(`FetchError`·`CustomsFormatError`·리미터 대기 초과)는
  그 데이터셋만 **실패**로 멈추고(받은 창은 남는다 — 다른 데이터셋은 계속) 사유를 한 줄로 내며
  종료 코드 1(2026-10-22: 시군구 첫 실호출의 오류가 트레이스백으로만 나와 무엇이 실패했는지 몰랐다).

공개 작업(계약 ⑭ 방식): 로그인 원천·저장소를 import 하지 않는다 — 저장소는 `kbj.store.repos.trade`.
텔레그램은 보내지 않는다(16:00 `brief.closing` 한 줄 — D-P6-8, ADR 0018).

자원: `trade_repo`(없으면 `connect` 로 Pg), `customs`(CustomsClient — 없으면 설정 키·`redis`),
`trade_config`·`trade_maps`(TradeMap 목록)·`trade_regions`·`hs_chapters`(없으면 config 파일).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import IO, Any, Final

from kbj.config.trade import (
    HsChapters,
    TradeConfig,
    TradeMap,
    TradeRegions,
    load_hs_chapters,
    load_local_map,
    load_trade,
    load_trade_map,
    load_trade_regions,
)
from kbj.core.quality import Quality
from kbj.core.time import to_kst
from kbj.core.trade_rows import (
    CountryMonthRow,
    HsDim,
    ItemCountryMonthRow,
    ItemMonthRow,
    SigunguMonthRow,
    TenDayColumn,
    TenDayRow,
)
from kbj.data.budget import BudgetExhausted
from kbj.data.datago import DatagoQuotaExceeded, DatagoTransport
from kbj.data.http import FetchError
from kbj.data.public.customs.client import (
    COUNTRIES,
    DATASET_IDS,
    ITEM_COUNTRY,
    ITEMS,
    SIGUNGU,
    TEN_DAY,
    CustomsClient,
    CustomsTruncated,
    month_index,
)
from kbj.data.public.customs.models import (
    SIDO_CODES,
    TEN_DAY_COLUMNS,
    CustomsFormatError,
    SigunguRow,
    TenDayKind,
    TradeRow,
)
from kbj.data.public.customs.models import TenDayRow as RawTenDay
from kbj.data.ratelimit import RateLimitTimeout
from kbj.data.spec import DataKey
from kbj.services.collectors.macro import REASON_MAX, KeyRun, NotConfigured, NotReady
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos.trade import PgTradeRepo, TradeRepo

__all__ = [
    "MONTHLY_ORDER",
    "SOURCE",
    "BackfillReport",
    "backfill",
    "chapters_of",
    "main",
    "monthly",
    "sigungu_hs6",
    "ten_day_rows",
    "tenday",
    "ym_dash",
    "ym_flat",
]

SOURCE: Final = "DATAGO"
THOUSAND: Final = Decimal(1000)
KIND_OF: Final[Mapping[str, TenDayKind]] = {v: k for k, v in TEN_DAY.items()}
# 같은 실행에서 키를 처리하는 순서 — 15100475 는 같은 달 15101612 의 국가 목록을 쓴다
MONTHLY_ORDER: Final[tuple[str, ...]] = (COUNTRIES, ITEMS, ITEM_COUNTRY, SIGUNGU)
ALL_CHAPTERS: Final[tuple[str, ...]] = tuple(f"{i:02d}" for i in range(1, 98))
WINDOW_MONTHS: Final = 12  # 15100475 기간 한도(ITEM_COUNTRY_MAX_MONTHS)와 같다


# ── 작은 도우미 ──────────────────────────────────────────────────────────────────────────────


def ym_flat(ym: str) -> str:
    """`YYYY-MM` → `YYYYMM`."""
    if len(ym) != 7 or ym[4] != "-":
        raise ValueError(f"기준 월은 YYYY-MM: {ym!r}")
    out = ym[:4] + ym[5:]
    month_index(out)
    return out


def ym_dash(ym: str) -> str:
    """`YYYYMM` → `YYYY-MM`."""
    month_index(ym)
    return f"{ym[:4]}-{ym[4:]}"


def _shift(ym: str, months: int) -> str:
    """`YYYYMM` 에서 months 개월 뒤(음수면 앞)."""
    n = month_index(ym) + months
    return f"{n // 12:04d}{n % 12 + 1:02d}"


def _months(start: str, end: str) -> list[str]:
    """`YYYYMM` 양 끝 포함."""
    return [_shift(start, i) for i in range(month_index(end) - month_index(start) + 1)]


def _int(v: Decimal | None, what: str) -> int | None:
    if v is None:
        return None
    if v != v.to_integral_value():
        raise ValueError(f"{what} 가 정수가 아니다: {v}")
    return int(v)


def _usd(v: Decimal | None) -> Decimal | None:
    """달러 정수(소수면 `ValueError` — 행 자료형이 다시 본다)."""
    return None if v is None else _exact(v)


def _exact(v: Decimal) -> Decimal:
    if v != v.to_integral_value():
        raise ValueError(f"금액이 달러 정수가 아니다: {v}")
    return v.quantize(Decimal(1))


def _quality(*values: Decimal | None) -> Quality:
    """수출·수입 금액이 둘 다 있으면 ok, 하나라도 비면 invalid(0 으로 채우지 않는다)."""
    return Quality.OK if all(v is not None for v in values) else Quality.INVALID


def _fail(dataset: str, e: Exception) -> CustomsFormatError:
    return CustomsFormatError(f"{dataset}: 행을 저장 형식으로 못 바꿨다 — {e}")


def _unique_keys(dataset: str, keys: Iterable[tuple[object, ...]]) -> None:
    seen: set[tuple[object, ...]] = set()
    for k in keys:
        if k in seen:
            raise CustomsFormatError(f"{dataset}: 같은 키 행이 두 번 왔다({k}) — 응답 형식 확인")
        seen.add(k)


# ── 설정·자원 ────────────────────────────────────────────────────────────────────────────────


@dataclass
class _Deps:
    repo: TradeRepo
    client: Callable[[], CustomsClient]
    cfg: TradeConfig
    maps: Callable[[], Sequence[TradeMap]]
    regions: Callable[[], TradeRegions]
    chapters: Callable[[], HsChapters]
    _client: CustomsClient | None = field(default=None, init=False)

    def customs(self) -> CustomsClient:
        if self._client is None:
            self._client = self.client()
        return self._client


def _deps(ctx: JobContext) -> _Deps:
    r = ctx.resources
    repo: TradeRepo = r.get("trade_repo") or PgTradeRepo(ctx.resource("connect"))
    cfg: TradeConfig = r.get("trade_config") or load_trade(settings=ctx.settings)

    def make_client() -> CustomsClient:
        got = r.get("customs")
        if got is not None:
            return got
        if ctx.settings is None or ctx.settings.datago_key is None:
            raise NotConfigured("KBJ_DATAGO_KEY 가 없다(공공데이터포털)")
        t = DatagoTransport.for_datasets(
            ctx.settings.datago_key, ctx.resource("redis"), DATASET_IDS
        )
        return CustomsClient(t)

    def maps() -> Sequence[TradeMap]:
        got = r.get("trade_maps")
        if got is not None:
            return tuple(got)
        out = [load_trade_map(settings=ctx.settings)]
        if cfg.map.local_file:
            out.append(load_local_map(Path(cfg.map.local_file)))
        return out

    def regions() -> TradeRegions:
        return r.get("trade_regions") or load_trade_regions(settings=ctx.settings)

    def chapters() -> HsChapters:
        return r.get("hs_chapters") or load_hs_chapters(settings=ctx.settings)

    return _Deps(repo, make_client, cfg, maps, regions, chapters)


def chapters_of(ch: HsChapters) -> tuple[str, ...]:
    """장별 수집의 장 목록 — 설정 파일이 비면 01~97 전부(결번 77 포함 — 빈 응답이면 건너뛴다)."""
    return tuple(sorted(ch.chapters)) if ch.chapters else ALL_CHAPTERS


def sigungu_hs6(maps: Iterable[TradeMap]) -> tuple[str, ...]:
    """매핑표(공개 + local)의 시군구 흐름 HS6(정렬·중복 없음). 판 1 의 regions 는 `flow_list()` 가
    시군구 흐름으로 바꿔 준다(판 2 는 regions 칸이 비어 있다 — 2026-10-22 실측: 백필이 0건)."""
    return tuple(
        sorted(
            {
                p.hs
                for m in maps
                for s in m.stocks
                for f in s.flow_list()
                if f.kind == "sigungu"
                for p in f.products
            }
        )
    )


def _region_id(regions: TradeRegions, sido: str, name: str, ym: str) -> str | None:
    for rg in regions.regions:
        if rg.sido_cd != sido or rg.sgg_nm != name:
            continue
        if (rg.valid_from is None or ym >= rg.valid_from) and (
            rg.valid_to is None or ym <= rg.valid_to
        ):
            return rg.id
    return None


def _sidos(start: str, end: str) -> list[tuple[str, str, str]]:
    """그 기간에 쓸 수 있는 시도마다 (코드, 시작, 끝) — 개편 전후로 자른다(`YYYYMM`)."""
    out: list[tuple[str, str, str]] = []
    for code, s in sorted(SIDO_CODES.items()):
        lo = max(start, s.valid_from) if s.valid_from else start
        hi = min(end, s.valid_to) if s.valid_to else end
        if lo <= hi:
            out.append((code, lo, hi))
    return out


# ── 어댑터 행 → 저장 행 ──────────────────────────────────────────────────────────────────────


def _item_rows(raw: Sequence[TradeRow], months: set[str], now: datetime) -> list[ItemMonthRow]:
    out: list[ItemMonthRow] = []
    for r in raw:
        if r.period is None or r.hs is None or r.period not in months:
            continue  # 합계 행·다른 달
        try:
            out.append(
                ItemMonthRow(
                    ym=ym_dash(r.period),
                    hs=r.hs,
                    hs_len=len(r.hs),
                    exp_usd=_usd(r.exp_usd),
                    exp_kg=r.exp_weight_kg,
                    imp_usd=_usd(r.imp_usd),
                    imp_kg=r.imp_weight_kg,
                    bal_usd=_usd(r.balance_usd),
                    source=r.source,
                    as_of=now,
                    quality=_quality(r.exp_usd, r.imp_usd),
                )
            )
        except (ValueError, TypeError) as e:
            raise _fail(ITEMS, e) from None
    _unique_keys(ITEMS, ((x.ym, x.hs) for x in out))
    return out


def _hs_dims(
    raw: Sequence[TradeRow], months: set[str], old: Mapping[str, HsDim], now: datetime
) -> list[HsDim]:
    """응답에서 본 HS 코드 → 차원 행(이름 없는 코드는 건너뛴다). 처음·마지막 달은 있는 값과
    합친다."""
    seen: dict[str, tuple[str, str, str]] = {}
    for r in raw:
        if r.period is None or r.hs is None or r.period not in months or not r.item_name:
            continue
        ym = ym_dash(r.period)
        lo, hi, name = seen.get(r.hs, (ym, ym, r.item_name))
        if ym >= hi:
            name = r.item_name
        seen[r.hs] = (min(lo, ym), max(hi, ym), name)
    out: list[HsDim] = []
    for hs, (lo, hi, name) in sorted(seen.items()):
        prev = old.get(hs)
        first = min(lo, prev.first_ym) if prev else lo
        last = max(hi, prev.last_ym) if prev else hi
        if prev is not None and prev.last_ym > hi:
            name = prev.name_ko  # 백필 — 더 최근 이름을 지킨다
        out.append(HsDim(hs, len(hs), name, first, last, f"{SOURCE}:{ITEMS}", now, Quality.OK))
    return out


def _country_rows(
    raw: Sequence[TradeRow], months: set[str], now: datetime
) -> list[CountryMonthRow]:
    out: list[CountryMonthRow] = []
    for r in raw:
        if r.period is None or r.country is None or r.period not in months:
            continue
        try:
            out.append(
                CountryMonthRow(
                    ym=ym_dash(r.period),
                    country=r.country.upper(),
                    country_name=r.country_name or r.country.upper(),
                    exp_cnt=_int(r.exp_count, "수출 건수"),
                    exp_usd=_usd(r.exp_usd),
                    imp_cnt=_int(r.imp_count, "수입 건수"),
                    imp_usd=_usd(r.imp_usd),
                    bal_usd=_usd(r.balance_usd),
                    source=r.source,
                    as_of=now,
                    quality=_quality(r.exp_usd, r.imp_usd),
                )
            )
        except (ValueError, TypeError) as e:
            raise _fail(COUNTRIES, e) from None
    _unique_keys(COUNTRIES, ((x.ym, x.country) for x in out))
    return out


def _item_country_rows(
    raw: Sequence[TradeRow], country: str, months: set[str], now: datetime
) -> list[ItemCountryMonthRow]:
    out: list[ItemCountryMonthRow] = []
    for r in raw:
        if r.period is None or r.hs is None or r.period not in months:
            continue
        try:
            out.append(
                ItemCountryMonthRow(
                    ym=ym_dash(r.period),
                    country=(r.country or country).upper(),
                    hs=r.hs,
                    hs_len=len(r.hs),
                    exp_usd=_usd(r.exp_usd),
                    exp_kg=r.exp_weight_kg,
                    imp_usd=_usd(r.imp_usd),
                    imp_kg=r.imp_weight_kg,
                    bal_usd=_usd(r.balance_usd),
                    source=r.source,
                    as_of=now,
                    quality=_quality(r.exp_usd, r.imp_usd),
                )
            )
        except (ValueError, TypeError) as e:
            raise _fail(ITEM_COUNTRY, e) from None
    return out


def _sigungu_rows(
    raw: Sequence[SigunguRow], months: set[str], regions: TradeRegions, now: datetime
) -> list[SigunguMonthRow]:
    out: list[SigunguMonthRow] = []
    for r in raw:
        if r.period is None or r.hs6 is None or r.period not in months:
            continue
        ym = ym_dash(r.period)
        try:
            out.append(
                SigunguMonthRow(
                    ym=ym,
                    sido_cd=r.sido,
                    sgg_nm=r.sigungu_name,
                    hs6=r.hs6,
                    region_id=_region_id(regions, r.sido, r.sigungu_name, ym),
                    exp_usd=_usd(r.exp_usd),
                    exp_cnt=_int(r.exp_count, "수출 건수"),
                    imp_usd=_usd(r.imp_usd),
                    imp_cnt=_int(r.imp_count, "수입 건수"),
                    bal_usd=_usd(r.balance_usd),
                    source=r.source,
                    as_of=now,
                    quality=_quality(r.exp_usd, r.imp_usd),
                )
            )
        except (ValueError, TypeError) as e:
            raise _fail(SIGUNGU, e) from None
    return out


def ten_day_rows(
    raw: Sequence[RawTenDay], old: Iterable[TenDayRow], now: datetime
) -> list[TenDayRow]:
    """어댑터 행 → 저장 행 중 **새 칸·값이 바뀐 칸만**(D-P6-12).

    새 칸: first_usd = usd, first_seen_at = 지금. 바뀐 칸: revised_at = 지금(처음 값은 저장소가
    지킨다). 값이 같은 칸은 다시 쓰지 않는다. 응답 값 None 은 invalid(있는 값을 지우지 않는다).
    """
    have = {(r.kind, r.ym, r.seg, r.col_no): r for r in old}
    out: list[TenDayRow] = []
    for r in raw:
        names = TEN_DAY_COLUMNS[r.kind]
        ym = ym_dash(r.month)
        end = r.span.split("~")[-1].strip()
        span = f"01~{end}"
        for col, thousand in sorted(r.amounts.items()):
            no = int(col)
            usd = None if thousand is None else _exact(thousand * THOUSAND)
            prev = have.get((r.kind, ym, r.segment, no))
            if prev is not None and (usd is None or prev.usd == usd):
                continue
            q = Quality.ESTIMATED if usd is not None else Quality.INVALID
            try:
                out.append(
                    TenDayRow(
                        kind=r.kind,
                        ym=ym,
                        seg=r.segment,
                        col_no=no,
                        name=names[no],
                        usd=usd,
                        first_usd=usd if prev is None else prev.first_usd,
                        first_seen_at=now if prev is None else prev.first_seen_at,
                        span_text=span,
                        source=r.source,
                        as_of=now,
                        quality=q,
                        revised_at=None if prev is None else now,
                    )
                )
            except (ValueError, TypeError) as e:
                raise _fail(TEN_DAY[r.kind], e) from None
    _unique_keys(SOURCE, ((x.kind, x.ym, x.seg, x.col_no) for x in out))
    return out


def _column_rows(
    kind: TenDayKind, raw: Sequence[RawTenDay], old: Sequence[TenDayColumn], now: datetime
) -> list[TenDayColumn]:
    """열 이름 이력 — 저장한 마지막 이름과 다를 때만 새 줄(처음이면 받은 가장 이른 달부터)."""
    if not raw:
        return []
    months = sorted(ym_dash(r.month) for r in raw)
    latest: dict[int, TenDayColumn] = {}
    for c in sorted(old, key=lambda c: c.valid_from):
        latest[c.col_no] = c
    out: list[TenDayColumn] = []
    for no, name in enumerate(TEN_DAY_COLUMNS[kind]):
        prev = latest.get(no)
        if prev is not None and prev.name == name:
            continue
        valid_from = months[0] if prev is None else max(months[-1], prev.valid_from)
        out.append(TenDayColumn(kind, no, valid_from, name, now))
    return out


# ── 10일 잠정치 처리기 ───────────────────────────────────────────────────────────────────────


def _parse_ten_key(as_of: str) -> tuple[str, int]:
    """`YYYYMM-n` → (`YYYYMM`, n)."""
    ym, _, seg = as_of.partition("-")
    month_index(ym)
    if seg not in ("1", "2", "3"):
        raise ValueError(f"10일 데이터 키는 YYYYMM-1|2|3: {as_of!r}")
    return ym, int(seg)


def _tenday_one(ctx: JobContext, d: _Deps, key: DataKey) -> int:
    kind = KIND_OF.get(key.dataset)
    if kind is None:
        raise NotConfigured(f"10일 잠정치 데이터셋이 아니다: {key.dataset}")
    want_ym, want_seg = _parse_ten_key(key.as_of)
    now = ctx.now
    end = max(to_kst(now).strftime("%Y%m"), want_ym)
    start = _shift(end, -d.cfg.tenday.lookback_months)
    raw = d.customs().ten_day(kind, start, end)
    old = d.repo.ten_day(kind, ym_dash(start), ym_dash(end))
    rows = ten_day_rows(raw, old, now)
    cols = _column_rows(kind, raw, d.repo.ten_day_columns(kind), now)
    n = 0
    if cols:
        n += d.repo.upsert_ten_day_columns(cols, loaded_by=ctx.job)
    if rows:
        n += d.repo.upsert_ten_day(rows, loaded_by=ctx.job, received_at=now)
    got = any(
        r.month == want_ym and r.segment == want_seg and r.amounts.get("00") is not None
        for r in raw
    )
    if not got:
        raise NotReady(f"{want_ym} {want_seg}구간 공표 전(정정·다른 구간 {len(rows)}칸은 썼다)")
    return n


def _run(ctx: JobContext, order: Sequence[str], one: Callable[[DataKey], int]) -> JobResult:
    """키마다 한 번 — 한 키의 실패(형식·전송)가 다른 키를 멈추지 않는다."""
    run = KeyRun()
    rank = {d: i for i, d in enumerate(order)}
    keys = sorted(ctx.keys, key=lambda k: (rank.get(k.dataset, len(rank)), k.as_of))
    for key in keys:
        label = key.label()
        if key.source != SOURCE or key.dataset not in rank:
            run.failed[label] = "이 작업이 모르는 데이터셋"
            continue
        try:
            run.rows += one(key)
        except NotReady as e:
            run.not_ready[label] = str(e)[:REASON_MAX]
        except NotConfigured as e:
            run.skipped[label] = str(e)[:REASON_MAX]
        except FetchError as e:
            run.failed[label] = e.reason[:REASON_MAX]
        except BudgetExhausted as e:
            run.failed[label] = f"일 예산: {e}"[:REASON_MAX]
        except CustomsFormatError as e:  # TenDayColumnsChanged 포함 — 그 종류만 실패
            run.failed[label] = f"{type(e).__name__}: {e}"[:REASON_MAX]
        else:
            run.collected.append(key)
    return run.result()


def tenday(ctx: JobContext) -> JobResult:
    """`trade.customs_tenday` 처리기."""
    d = _deps(ctx)
    return _run(ctx, tuple(TEN_DAY[k] for k in TEN_DAY), lambda k: _tenday_one(ctx, d, k))


# ── 월간 처리기 ──────────────────────────────────────────────────────────────────────────────


def _fetch_items(d: _Deps, start: str, end: str) -> list[TradeRow]:
    c = d.customs()
    if d.cfg.monthly.items_mode == "all":
        try:
            return c.items(start, end)
        except CustomsTruncated:
            pass  # 한 번에 다 못 받았다 — 장별로 다시(R6)
    out: list[TradeRow] = []
    for ch in chapters_of(d.chapters()):
        out.extend(c.items(start, end, hs=ch))
    return out


def _write_items(
    d: _Deps, raw: Sequence[TradeRow], months: set[str], at: datetime, loaded_by: str
) -> int:
    rows = _item_rows(raw, months, at)
    if not rows:
        return 0
    old = {h.hs: h for h in d.repo.hs_dim()}
    dims = _hs_dims(raw, months, old, at)
    n = d.repo.upsert_item_monthly(rows, loaded_by=loaded_by, received_at=at)
    if dims:
        n += d.repo.upsert_hs_dim(dims, loaded_by=loaded_by, received_at=at)
    return n


def _countries_in(d: _Deps, start: str, end: str) -> list[str]:
    rows = d.repo.country_monthly(ym_from=ym_dash(start), ym_to=ym_dash(end))
    return sorted({r.country for r in rows})


def _fetch_item_country(
    d: _Deps, start: str, end: str, countries: Sequence[str], months: set[str], now: datetime
) -> list[ItemCountryMonthRow]:
    out: list[ItemCountryMonthRow] = []
    c = d.customs()
    for cc in countries:
        out.extend(_item_country_rows(c.item_country(start, end, cc), cc, months, now))
    _unique_keys(ITEM_COUNTRY, ((x.ym, x.country, x.hs) for x in out))
    return out


def _fetch_sigungu(
    d: _Deps, start: str, end: str, hs6s: Sequence[str], now: datetime
) -> list[SigunguMonthRow]:
    regions = d.regions()
    c = d.customs()
    out: list[SigunguMonthRow] = []
    for sido, lo, hi in _sidos(start, end):
        months = set(_months(lo, hi))
        for hs6 in hs6s:
            out.extend(_sigungu_rows(c.sigungu(lo, hi, sido, hs6), months, regions, now))
    _unique_keys(SIGUNGU, ((x.ym, x.sido_cd, x.sgg_nm, x.hs6) for x in out))
    return out


def _monthly_one(ctx: JobContext, d: _Deps, key: DataKey) -> int:
    ym = ym_flat(key.as_of[:7])
    months = {ym}
    now = ctx.now
    c = d.customs
    if key.dataset == COUNTRIES:
        rows = _country_rows(c().countries(ym, ym), months, now)
        if not rows:
            raise NotReady(f"{ym_dash(ym)} 국가별 공표 전")
        return d.repo.upsert_country_monthly(rows, loaded_by=ctx.job, received_at=now)
    if key.dataset == ITEMS:
        raw = _fetch_items(d, ym, ym)
        n = _write_items(d, raw, months, now, ctx.job)
        if n == 0:
            raise NotReady(f"{ym_dash(ym)} 품목별 공표 전")
        return n
    if key.dataset == ITEM_COUNTRY:
        countries = _countries_in(d, ym, ym)
        if not countries:
            raise NotReady(f"{ym_dash(ym)} 국가 목록(15101612) 이 아직 없다")
        rows = _fetch_item_country(d, ym, ym, countries, months, now)
        if not rows:
            raise NotReady(f"{ym_dash(ym)} 품목×국가 공표 전")
        return d.repo.upsert_item_country_monthly(rows, loaded_by=ctx.job, received_at=now)
    hs6s = sigungu_hs6(d.maps())
    if not hs6s:
        raise NotConfigured("매핑표에 지역 행(HS6)이 없다 — 시군구는 매핑 HS6 만 받는다")
    srows = _fetch_sigungu(d, ym, ym, hs6s, now)
    if not srows:
        raise NotReady(f"{ym_dash(ym)} 시군구 공표 전")
    return d.repo.upsert_sigungu_monthly(srows, loaded_by=ctx.job, received_at=now)


def monthly(ctx: JobContext) -> JobResult:
    """`trade.customs_monthly` 처리기."""
    d = _deps(ctx)
    return _run(ctx, MONTHLY_ORDER, lambda k: _monthly_one(ctx, d, k))


# ── 백필(사람이 실행 — 등록부 작업 아님) ─────────────────────────────────────────────────────


@dataclass
class BackfillReport:
    """데이터셋마다 받은 창·건너뛴 창·멈춘 이유(값 없음 — 건수·기간만)."""

    done: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    skipped: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    stopped: dict[str, str] = field(default_factory=dict[str, str])
    failed: dict[str, str] = field(default_factory=dict[str, str])  # 호출·형식 오류 — 종료 1
    rows: int = 0

    def lines(self) -> list[str]:
        out: list[str] = []
        for ds in MONTHLY_ORDER:
            if not any(ds in m for m in (self.done, self.skipped, self.stopped, self.failed)):
                continue  # 고르지 않은 데이터셋(--datasets)은 줄을 내지 않는다
            d = len(self.done.get(ds, []))
            s = len(self.skipped.get(ds, []))
            tail = f" · 멈춤: {self.stopped[ds]}" if ds in self.stopped else ""
            if ds in self.failed:
                tail += f" · 실패: {self.failed[ds]}"
            out.append(f"{SOURCE}:{ds} 받은 창 {d} · 이미 있음 {s}{tail}")
        out.append(f"쓴 행 {self.rows}")
        return out


def _windows(start: str, end: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    cur = start
    while cur <= end:
        hi = min(end, _shift(cur, WINDOW_MONTHS - 1))
        out.append((cur, hi))
        cur = _shift(hi, 1)
    return out


def _present(repo: TradeRepo, ds: str, start: str, end: str) -> set[str]:
    """그 표에 이미 있는 달(`YYYYMM`)."""
    lo, hi = ym_dash(start), ym_dash(end)
    rows: Sequence[Any]
    if ds == COUNTRIES:
        rows = repo.country_monthly(ym_from=lo, ym_to=hi)
    elif ds == ITEMS:
        rows = repo.item_monthly(hs_len=2, ym_from=lo, ym_to=hi) or repo.item_monthly(
            ym_from=lo, ym_to=hi
        )
    elif ds == ITEM_COUNTRY:
        rows = repo.item_country_monthly(ym_from=lo, ym_to=hi)
    else:
        rows = repo.sigungu_monthly(ym_from=lo, ym_to=hi)
    return {ym_flat(r.ym) for r in rows}


def backfill(
    *,
    repo: TradeRepo,
    client: CustomsClient,
    cfg: TradeConfig,
    from_ym: str,
    to_ym: str,
    now: Callable[[], datetime],
    used: Callable[[str], int],
    cap: int,
    chapters: Sequence[str] = ALL_CHAPTERS,
    hs6s: Sequence[str] = (),
    regions: TradeRegions | None = None,
    datasets: Sequence[str] = MONTHLY_ORDER,
    loaded_by: str = "trade.customs_backfill",
) -> BackfillReport:
    """`from_ym`~`to_ym`(`YYYY-MM`) 월간 4종을 12개월 창으로 받는다(머리말 '백필').

    `used(데이터셋)` = 그날 이미 쓴 호출 수(Redis 예산 카운터), `cap` = 데이터셋 하루 한도.
    """
    start, end = ym_flat(from_ym), ym_flat(to_ym)
    if start > end:
        raise ValueError(f"--from({from_ym})이 --to({to_ym})보다 늦다")
    limit = int(cap * cfg.backfill.stop_at_ratio)
    regs = regions if regions is not None else TradeRegions(version=1)
    d = _Deps(
        repo,
        lambda: client,
        cfg,
        lambda: (),
        lambda: regs,
        lambda: HsChapters(version=1, chapters={c: c for c in chapters}),
    )
    rep = BackfillReport()
    for ds in [x for x in MONTHLY_ORDER if x in datasets]:
        for lo, hi in _windows(start, end):
            label = f"{ym_dash(lo)}~{ym_dash(hi)}"
            want = set(_months(lo, hi))
            if want <= _present(repo, ds, lo, hi):
                rep.skipped.setdefault(ds, []).append(label)
                continue
            countries = _countries_in(d, lo, hi) if ds == ITEM_COUNTRY else []
            if ds == SIGUNGU and not hs6s:
                rep.stopped[ds] = "매핑표에 지역 행(HS6)이 없다"
                break
            if ds == ITEM_COUNTRY and not countries:
                rep.stopped[ds] = f"{label} 국가 목록(15101612)이 없다 — 국가부터 받는다"
                break
            est = {
                COUNTRIES: 1,
                ITEMS: 1 if cfg.monthly.items_mode == "all" else len(chapters),
                ITEM_COUNTRY: len(countries),
                SIGUNGU: len(_sidos(lo, hi)) * len(hs6s),
            }[ds]
            if used(ds) + est > limit:
                rep.stopped[ds] = (
                    f"{label} 앞에서 멈춤 — 하루 한도 {cap} × {cfg.backfill.stop_at_ratio} "
                    f"(예상 {est}건) — 내일 다시 실행하면 이어 받는다"
                )
                break
            at = now()
            try:
                if ds == COUNTRIES:
                    crow = _country_rows(client.countries(lo, hi), want, at)
                    n = repo.upsert_country_monthly(crow, loaded_by=loaded_by, received_at=at)
                elif ds == ITEMS:
                    n = _write_items(d, _fetch_items(d, lo, hi), want, at, loaded_by)
                elif ds == ITEM_COUNTRY:
                    irow = _fetch_item_country(d, lo, hi, countries, want, at)
                    n = repo.upsert_item_country_monthly(irow, loaded_by=loaded_by, received_at=at)
                else:
                    srow = _fetch_sigungu(d, lo, hi, hs6s, at)
                    n = repo.upsert_sigungu_monthly(srow, loaded_by=loaded_by, received_at=at)
            except BudgetExhausted as e:
                rep.stopped[ds] = f"{label} 일 예산 닫힘: {e}"[:REASON_MAX]
                break
            except DatagoQuotaExceeded as e:  # 포털이 그날 한도로 끊었다 — 예산 닫힘과 같다
                rep.stopped[ds] = f"{label} 포털 한도 초과: {e.reason} — 내일 다시"[:REASON_MAX]
                break
            except (FetchError, CustomsFormatError, RateLimitTimeout) as e:
                why = str(e)
                if isinstance(e, FetchError):  # 키는 이미 가렸다 — 상태 코드는 원인을 가른다
                    why = e.reason if e.status is None else f"HTTP {e.status} {e.reason}"
                rep.failed[ds] = f"{label} {type(e).__name__}: {why}"[:REASON_MAX]
                break
            rep.rows += n
            rep.done.setdefault(ds, []).append(label)
    return rep


def _production(settings: Any) -> dict[str, Any]:  # pragma: no cover — 운영(DB·Redis)
    from kbj.data.budget import DailyBudget
    from kbj.data.limits import load_limits
    from kbj.services.runtime.heartbeat import connect_redis
    from kbj.store.db import connect

    if settings.datago_key is None or settings.redis_url is None or settings.database_url is None:
        raise RuntimeError("KBJ_DATAGO_KEY·KBJ_REDIS_URL·KBJ_DATABASE_URL 이 필요하다")
    redis = connect_redis(settings.redis_url)
    cap = load_limits(settings=settings).source("datago").daily_cap_per_dataset
    if cap is None:
        raise RuntimeError("limits.yaml datago.daily_cap_per_dataset 가 없다")
    t = DatagoTransport.for_datasets(settings.datago_key, redis, DATASET_IDS)
    budgets = {ds: DailyBudget(redis, "datago", cap, scope=ds) for ds in MONTHLY_ORDER}
    from kbj.core.time import utcnow

    return {
        "repo": PgTradeRepo(lambda: connect(settings, service="customs_backfill")),
        "client": CustomsClient(t),
        "cap": cap,
        "used": lambda ds: budgets[ds].used(utcnow()),
        "now": utcnow,
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    out: IO[str] | None = None,
    build: Callable[[], dict[str, Any]] | None = None,
) -> int:
    """CLI `backfill --from YYYY-MM --to YYYY-MM [--datasets ...]`. `build` 는 시험이 주입."""
    o: IO[str] = out if out is not None else sys.stdout
    p = argparse.ArgumentParser(prog="python -m kbj.services.collectors.customs")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill", help="월간 4종 백필(12개월 창, 하루 한도 90% 에서 멈춤)")
    b.add_argument("--from", dest="from_ym", required=True, help="YYYY-MM")
    b.add_argument("--to", dest="to_ym", required=True, help="YYYY-MM")
    b.add_argument("--datasets", nargs="+", choices=MONTHLY_ORDER, default=list(MONTHLY_ORDER))
    args = p.parse_args(argv)
    from kbj.config.settings import Settings

    settings = Settings() if build is None else None
    deps = build() if build is not None else _production(settings)
    cfg: TradeConfig = deps.get("cfg") or load_trade(settings=settings)
    maps: Sequence[TradeMap] = deps.get("maps") or [load_trade_map(settings=settings)]
    if "maps" not in deps and cfg.map.local_file:
        maps = [*maps, load_local_map(Path(cfg.map.local_file))]
    regions: TradeRegions = deps.get("regions") or load_trade_regions(settings=settings)
    chapters = chapters_of(deps.get("chapters") or load_hs_chapters(settings=settings))
    rep = backfill(
        repo=deps["repo"],
        client=deps["client"],
        cfg=cfg,
        from_ym=args.from_ym,
        to_ym=args.to_ym,
        now=deps["now"],
        used=deps["used"],
        cap=deps["cap"],
        chapters=chapters,
        hs6s=sigungu_hs6(maps),
        regions=regions,
        datasets=args.datasets,
    )
    for line in rep.lines():
        o.write(line + "\n")
    # 한도에서 멈춘 것은 실패가 아니다 — 내일 다시 실행하면 이어 받는다. 호출·형식 오류만 1
    return 1 if rep.failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
