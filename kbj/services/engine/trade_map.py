"""종목↔HS 매핑 작업 — `trade.map_load`(매일 07:35)·`trade.stock_index`(매일 21:00).

docs/p6_design.md §1.5·§3.2·§4.5·§4.6, metrics §28, D-P6-14. 순수 계산은 `kbj.engines.trade`
(mapping·overlay), SQL 은 `kbj.store.repos`(trade·filings·fin — 모두 공개 표), 이 모듈은 둘을 잇는
실행만 한다(P3 관례 — `kbj.services.engine.board`). 외부 호출 없음. 로그인 원천·로그인 저장소는
import 하지 않는다(계약 ⑰ — WRAP).

`trade.map_load`
- 공개 매핑표(`config/knowledge/trade_map.yaml`)를 해석·파일 사이 대조(`mapping.cross_check`)한 뒤
  `origin = public` 판을 통째로 바꾼다. 문제가 하나라도 있으면 **실패**(쓰지 않는다 — 옛 판 유지).
- VM local 매핑(`config/trade.yaml map.local_file` — 상대 경로는 `KBJ_DATA_DIR` 기준. 관심종목
  전용)이 설정돼 있으면 같은 검증 뒤 `origin = local` 판을 바꾼다. 파일이 없으면 실패(조용히 비우지
  않는다). 설정이 없으면 local 판을 비운다(설정을 지운 뜻). 기록(`detail`)에는 local 의
  **건수만**(종목코드는 관심종목이라 남기지 않는다).

`trade.stock_index`
- 매핑(`pub_trade.stock_map` — local 우선 병합)·품목 월간·품목×국가·시군구(매핑 접두만 읽는다)와
  `pub_fin.quarterly` 매출(상장사 고유번호 표 `pub_filings.corp_code` 로 종목 → 회사)을 읽어
  종목마다 월·분기 지수, 매출 YoY, 시차 상관, 상태를 `pub_trade.stock_export` 에 쓴다.
- 매출: 연결(CFS) 매출이 하나라도 있으면 CFS, 아니면 별도(OFS) — 기준을 섞지 않는다. 분기 이름은
  분기말(`period_end`)의 달력 분기(결산월이 12월이 아닌 회사도 같은 축).
- 우선주 등 고유번호 표에 없는 종목은 `no_revenue`(종류주식 표는 로그인 저장소라 읽지 않는다 —
  우선주 매핑은 보통주 코드로 적는다).
- 매핑이 비었거나 품목 월간이 없으면 `not_ready`.

종목 수출 지수 표는 실행마다 통째로 바꾼다(`replace_stock_export` — 매핑표에서 빠진 종목의 옛 행이
남지 않게, WRAP). 오류는 삼키지 않는다(절대 규칙 4).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Final

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
from kbj.core.fin_rows import QuarterRow
from kbj.core.trade_rows import (
    ItemCountryMonthRow,
    ItemMonthRow,
    SigunguMonthRow,
    StockMapRow,
    prev_ym,
)
from kbj.engines.trade.explore import months_between
from kbj.engines.trade.mapping import Problem, cross_check, map_rows, map_version, resolve
from kbj.engines.trade.overlay import build_overlay, sources, to_rows
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos.filings import FilingsRepo, PgFilingsRepo
from kbj.store.repos.fin import FinRepo, PgFinRepo
from kbj.store.repos.trade import PgTradeRepo, TradeRepo

__all__ = [
    "JOB_INDEX",
    "JOB_LOAD",
    "READ_MONTHS",
    "load",
    "quarter_revenue",
    "run_load",
    "run_stock_index",
    "stock_index",
]

JOB_LOAD: Final = "trade.map_load"
JOB_INDEX: Final = "trade.stock_index"
READ_MONTHS: Final = 60  # 5년(백필 기간과 같다) — 분기 YoY·8분기 상관에 충분


# ── 자원 ────────────────────────────────────────────────────────────────────────────────────


def _repos(ctx: JobContext) -> Any:
    return ctx.resources.get("repos")


def _trade(ctx: JobContext) -> TradeRepo:
    got = ctx.resources.get("trade_repo")
    if got is not None:
        return got
    repos = _repos(ctx)
    return repos.trade if repos is not None else PgTradeRepo(ctx.resource("connect"))


def _fin(ctx: JobContext) -> FinRepo:
    got = ctx.resources.get("fin_repo")
    if got is not None:
        return got
    repos = _repos(ctx)
    return repos.fin if repos is not None else PgFinRepo(ctx.resource("connect"))


def _filings(ctx: JobContext) -> FilingsRepo:
    got = ctx.resources.get("filings_repo")
    if got is not None:
        return got
    repos = _repos(ctx)
    return repos.filings if repos is not None else PgFilingsRepo(ctx.resource("connect"))


def _cfg(ctx: JobContext) -> TradeConfig:
    return ctx.resources.get("trade_config") or load_trade(settings=ctx.settings)


# ── trade.map_load ──────────────────────────────────────────────────────────────────────────


def _local_path(cfg: TradeConfig, ctx: JobContext) -> Path | None:
    raw = cfg.map.local_file
    if raw is None:
        return None
    p = Path(raw)
    if p.is_absolute():
        return p
    base = ctx.settings.data_dir if ctx.settings is not None else Path("state")
    return base / p


def run_load(
    repo: TradeRepo,
    ctx: JobContext,
    *,
    public: TradeMap,
    local: TradeMap | None,
    chapters: HsChapters,
    regions: TradeRegions,
) -> JobResult:
    probs = cross_check(public, chapters=chapters, regions=regions)
    local_probs: list[Problem] = (
        [] if local is None else cross_check(local, chapters=chapters, regions=regions)
    )
    if probs or local_probs:
        return JobResult(
            status="failed",
            detail={
                "reason": "매핑표 검증 실패 — 옛 판 유지",
                "public_problems": len(probs),
                "public_first": [p.line() for p in probs[:5]],
                "local_problems": len(local_probs),  # 관심종목 — 건수만
            },
        )
    pv = map_version(public)
    pub_rows = map_rows(public, origin="public", version=pv, as_of=ctx.now)
    n = repo.replace_stock_map(pub_rows, origin="public", loaded_by=JOB_LOAD, received_at=ctx.now)
    loc_rows: list[StockMapRow] = []
    if local is not None:
        lv = f"local-{map_version(local)}"
        loc_rows = map_rows(local, origin="local", version=lv, as_of=ctx.now)
    n += repo.replace_stock_map(loc_rows, origin="local", loaded_by=JOB_LOAD, received_at=ctx.now)
    return JobResult(
        status="ok",
        rows=n,
        detail={
            "map_version": pv,
            "public_stocks": len(public.stocks),
            "public_lines": len(pub_rows),
            "local_stocks": 0 if local is None else len(local.stocks),
            "local_lines": len(loc_rows),
        },
    )


def load(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.engine.trade_map:load`)."""
    cfg = _cfg(ctx)
    public: TradeMap = ctx.resources.get("trade_map") or load_trade_map(settings=ctx.settings)
    local: TradeMap | None = ctx.resources.get("trade_local_map")
    if local is None:
        path = _local_path(cfg, ctx)
        local = None if path is None else load_local_map(path)  # 없으면 FileNotFoundError
    chapters: HsChapters = ctx.resources.get("hs_chapters") or load_hs_chapters(
        settings=ctx.settings
    )
    regions: TradeRegions = ctx.resources.get("trade_regions") or load_trade_regions(
        settings=ctx.settings
    )
    return run_load(
        _trade(ctx), ctx, public=public, local=local, chapters=chapters, regions=regions
    )


# ── trade.stock_index ───────────────────────────────────────────────────────────────────────


def _quarter_label(q: QuarterRow) -> str:
    end = q.period_end
    return f"{end.year}Q{(end.month - 1) // 3 + 1}"


def quarter_revenue(rows: Iterable[QuarterRow]) -> dict[str, int | None]:
    """분기 재무 → {달력 분기: 매출}. CFS 매출이 하나라도 있으면 CFS 만, 아니면 OFS(섞지 않는다)."""
    rows = list(rows)
    cfs = [r for r in rows if r.fs_div == "CFS"]
    pick = (
        cfs if any(r.revenue is not None for r in cfs) else [r for r in rows if r.fs_div == "OFS"]
    )
    out: dict[str, int | None] = {}
    for r in sorted(pick, key=lambda x: x.period_end):
        out[_quarter_label(r)] = r.revenue
    return out


def _dedupe[R](rows: Iterable[R], key: Any) -> list[R]:
    seen: dict[Any, R] = {}
    for r in rows:
        seen.setdefault(key(r), r)
    return list(seen.values())


def run_stock_index(
    repo: TradeRepo, fin: FinRepo, filings: FilingsRepo, ctx: JobContext, *, cfg: TradeConfig
) -> JobResult:
    smap = repo.stock_map()
    if not smap:
        return JobResult(status="not_ready", detail={"reason": "매핑 없음 — trade.map_load 먼저"})
    last = repo.last_ym("pub_trade.item_monthly")
    if last is None:
        return JobResult(status="not_ready", detail={"reason": "월간 품목 없음"})
    ym_from = prev_ym(last, READ_MONTHS - 1)
    res = resolve(smap)
    lines = [ln for code in res.codes() for ln in res.lines[code]]
    items: list[ItemMonthRow] = []
    ics: list[ItemCountryMonthRow] = []
    sgg: list[SigunguMonthRow] = []
    for hs in sorted({ln.hs for ln in lines if not ln.region_id}):
        items += repo.item_monthly(prefix=hs, ym_from=ym_from, ym_to=last)
    for hs, cc in sorted({(ln.hs, c) for ln in lines if not ln.region_id for c in ln.countries}):
        ics += repo.item_country_monthly(prefix=hs, country=cc, ym_from=ym_from, ym_to=last)
    for rid, hs6 in sorted({(ln.region_id, ln.hs) for ln in lines if ln.region_id}):
        sgg += repo.sigungu_monthly(hs6=hs6, region_id=rid, ym_from=ym_from, ym_to=last)
    src = sources(
        _dedupe(items, lambda r: (r.ym, r.hs)),
        _dedupe(ics, lambda r: (r.ym, r.country, r.hs)),
        _dedupe(sgg, lambda r: (r.ym, r.sido_cd, r.sgg_nm, r.hs6)),
        months=months_between(ym_from, last),
    )
    corp_of: Mapping[str, tuple[str, str]] = filings.corp_index()
    out = []
    statuses: Counter[str] = Counter()
    for code in res.codes():
        hit = corp_of.get(code)
        revenue = {} if hit is None else quarter_revenue(fin.quarters(hit[0]))
        ov = build_overlay(code, res.lines[code], src, revenue=revenue, cfg=cfg.overlay)
        statuses[ov.status] += 1
        version = res.lines[code][0].map_version
        out += to_rows(ov, map_version=version, as_of=ctx.now)
    n = repo.replace_stock_export(out, loaded_by=JOB_INDEX, computed_at=ctx.now)  # 표 통째
    return JobResult(
        status="ok",
        rows=n,
        detail={
            "last_ym": last,
            "stocks": len(res.codes()),
            "statuses": dict(sorted(statuses.items())),
            "conflicts": len(res.warnings),  # 공개·local 겹침 — 건수만(관심종목)
        },
    )


def stock_index(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.engine.trade_map:stock_index`)."""
    return run_stock_index(_trade(ctx), _fin(ctx), _filings(ctx), ctx, cfg=_cfg(ctx))
