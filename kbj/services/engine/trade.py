"""수출입 파생 작업 — `trade.derive`(14~20일 10:40·15:40, as_of = 지난달 `YYYY-MM`,
`trade.customs_monthly` 굳은 의존).

docs/p6_design.md §1.4·§3.2, metrics §26·§29, D-P6-9. 순수 계산은 `kbj.engines.trade`
(explore·surge·items104), SQL 은 `kbj.store.repos.trade`, 이 모듈은 둘을 잇는 실행만 한다
(P3 관례 — `kbj.services.engine.board`). 외부 호출 없음(pub_trade 월간만 읽는다).

순서
- ① `pub_trade.item_monthly` 의 마지막 기준 월이 as_of 보다 앞이면(또는 비면) `not_ready`
  (월간 수집 전 — 재시도).
- ② 품목 월간(as_of − `READ_MONTHS` 개월부터)·국가 월간을 읽어 흐름(수출·수입)마다 접두
  색인을 만든다.
- ③ 급등·급락(`config/trade.yaml surge.levels` — hs2·4·6·10·country)을 as_of 달에 대해
  `pub_trade.surge` 에 쓴다.
- ④ 104품목: `items104.mode` 가 fixed 이고 `knowledge/trade_items.yaml` 에 묶음이 있으면 그
  묶음, 아니면(top_n 또는 파일이 비면) 직전 `top_n_months` 개월 수출 상위 HS4 n 개(이름은
  `pub_trade.hs_dim`)를 고른다. 최근 `GROUP_MONTHS` 개월 행을
  `pub_trade.item_group_monthly` 에 쓴다(같은 키는 덮어쓴다).

알려진 한계: 저장소 쓰기가 upsert 라 같은 달을 다시 돌려 더 이상 급등이 아닌 열쇠의 옛 행은
남는다(정정이 드물고 화면은 순위로 다시 정렬 — 메인에 `replace_surge(ym)` 요청). 오류는 삼키지
않는다 — 설정 오류(묶음 겹침)·저장소 오류는 그대로 올라가 실행기가 `failed` 로 기록한다
(절대 규칙 4).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Final, Literal

from kbj.config.trade import TradeConfig, TradeItems, load_trade, load_trade_items
from kbj.core.quality import Quality
from kbj.core.trade_rows import Flow, SurgeLevel, SurgeRow, prev_ym, ym_ok
from kbj.engines.trade.explore import ItemIndex, item_index, months_between
from kbj.engines.trade.items104 import Group, group_rows, groups_from_config, top_n_groups
from kbj.engines.trade.surge import SurgeInput, detect
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos.trade import PgTradeRepo, TradeRepo

__all__ = ["GROUP_MONTHS", "JOB", "READ_MONTHS", "derive", "run_derive"]

JOB: Final = "trade.derive"
GROUP_MONTHS: Final = 24  # 104품목 행을 다시 쓰는 기간(화면·공개 파일 24개월)
READ_MONTHS: Final = GROUP_MONTHS + 12 + 2  # 24개월 YoY + 3개월 YoY 분모
FLOWS: Final[tuple[Flow, ...]] = ("exp", "imp")


def _repo(ctx: JobContext) -> TradeRepo:
    got = ctx.resources.get("trade_repo")
    if got is not None:
        return got
    repos = ctx.resources.get("repos")
    if repos is not None:
        return repos.trade
    return PgTradeRepo(ctx.resource("connect"))


def _surges(
    repo: TradeRepo, idx: dict[Flow, ItemIndex], *, ym: str, cfg: TradeConfig, ctx: JobContext
) -> list[SurgeRow]:
    out: list[SurgeRow] = []
    for level in cfg.surge.levels:
        lv: SurgeLevel = level
        for flow in FLOWS:
            if lv == "country":
                rows = repo.country_monthly(ym_from=prev_ym(ym, 14), ym_to=ym)
                series: dict[str, dict[str, Decimal | None]] = {}
                for r in rows:
                    v = r.exp_usd if flow == "exp" else r.imp_usd
                    series.setdefault(r.country, {})[r.ym] = (
                        None if r.quality is Quality.INVALID else v
                    )
                inputs = [SurgeInput(k, s) for k, s in sorted(series.items())]
            else:
                ix = idx[flow]
                n = int(lv[2:])
                inputs = [
                    SurgeInput(k, {m: ix.usd(k, m) for m in ix.months}) for k in ix.codes_at(n)
                ]
            out += detect(inputs, ym=ym, level=lv, flow=flow, cfg=cfg.surge, as_of=ctx.now)
    return out


def _groups(
    repo: TradeRepo, exp: ItemIndex, *, ym: str, cfg: TradeConfig, items: TradeItems
) -> tuple[list[Group], Literal["fixed", "top_n"]]:
    if cfg.items104.mode == "fixed" and items.groups:
        return groups_from_config(items.groups), "fixed"
    names = {d.hs: d.name_ko for d in repo.hs_dim(hs_len=4)}
    return (
        top_n_groups(exp, names, ym=ym, n=cfg.items104.n, months=cfg.items104.top_n_months),
        "top_n",
    )


def run_derive(
    repo: TradeRepo,
    ctx: JobContext,
    *,
    cfg: TradeConfig,
    items: TradeItems,
) -> JobResult:
    ym = ctx.as_of[:7]
    if not ym_ok(ym):
        return JobResult(
            status="failed", detail={"reason": f"as_of 는 YYYY-MM: {ctx.as_of[:12]!r}"}
        )
    last = repo.last_ym("pub_trade.item_monthly")
    if last is None or last < ym:
        return JobResult(status="not_ready", detail={"reason": "월간 품목 없음", "last_ym": last})
    rows = repo.item_monthly(ym_from=prev_ym(ym, READ_MONTHS - 1), ym_to=ym)
    idx: dict[Flow, ItemIndex] = {f: item_index(rows, f) for f in FLOWS}
    surges = _surges(repo, idx, ym=ym, cfg=cfg, ctx=ctx)
    groups, mode = _groups(repo, idx["exp"], ym=ym, cfg=cfg, items=items)
    months: Sequence[str] = months_between(prev_ym(ym, GROUP_MONTHS - 1), ym)
    group_out = group_rows(groups, idx["exp"], idx["imp"], months=months, mode=mode, as_of=ctx.now)
    n_s = repo.replace_surge(surges, ym=ym, loaded_by=JOB, computed_at=ctx.now)  # 그 달 통째
    n_g = repo.upsert_item_group_monthly(group_out, loaded_by=JOB, computed_at=ctx.now)
    return JobResult(
        status="ok",
        rows=n_s + n_g,
        detail={
            "ym": ym,
            "surge": len(surges),
            "groups": len(groups),
            "group_rows": len(group_out),
            "mode": mode,
        },
    )


def derive(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.engine.trade:derive`)."""
    cfg: TradeConfig = ctx.resources.get("trade_config") or load_trade(settings=ctx.settings)
    items: TradeItems = ctx.resources.get("trade_items") or load_trade_items(settings=ctx.settings)
    return run_derive(_repo(ctx), ctx, cfg=cfg, items=items)
