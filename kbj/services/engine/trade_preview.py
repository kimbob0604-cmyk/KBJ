"""실적 프리뷰 — 종목별 계산(조회 API 와 같이 쓴다)·작업 `trade.preview`(P10 단계 3,
docs/p10_trade_stocks_plan.md).

로그인 등급(컨센서스 = KIS)이라 계약 ⑰ 의 공개 수출입 작업과 따로 둔다. 순수 계산은
`kbj.engines.trade.preview`, 이 모듈은 저장소에서 입력을 모은다.

- 수출 지수: `pub_trade.stock_export` 월 행(매핑 흐름 가중합, 달러).
- 분기 매출: `pub_fin.quarterly`(CFS 우선 — `trade_map.quarter_revenue` 와 같은 규칙).
- 환율: 달 평균 원/달러(`ECOS:731Y003:usdkrw` 공개 → 없으면 로그인 `ECOS:731Y001:usdkrw_close`).
- 연간 컨센서스: 최근 스냅(`CONSENSUS_LOOKBACK_DAYS` 안)의 12월 결산 추정 매출.

작업 `trade.preview`(거래일 16:00 — ADR 0018, 전날 21:00 `trade.stock_index` 결과):
매핑 종목마다 지금 프리뷰와 '마지막 달을 뺀'
프리뷰를 견줘 판정이 바뀐(또는 새로 생긴) 종목만 한 통으로 묶는다(`notify(kind='trade.preview',
subject=<마지막 달>)` — 달마다 한 번, 0건이면 보내지 않는다). 본문은 규칙 문장(LLM 아님)이고
매매 권유가 아니다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Final, Protocol, cast

from kbj.core.time import to_kst
from kbj.core.trade_rows import StockExportRow, prev_ym
from kbj.engines.consensus.forward import latest_snapshot
from kbj.engines.trade.preview import Preview, PreviewCfg, preview
from kbj.services.engine.trade_map import quarter_revenue
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos import (
    ConsensusRepo,
    FilingsRepo,
    FinRepo,
    MacroPrivateRepo,
    MacroPublicRepo,
    TradeRepo,
    pg_repos,
)

__all__ = [
    "CONSENSUS_LOOKBACK_DAYS",
    "DISCLAIMER",
    "FX_SERIES",
    "JOB",
    "KIND",
    "PreviewRepos",
    "annual_consensus",
    "digest",
    "monthly_fx",
    "monthly_index",
    "run_job",
    "run_preview",
    "stock_preview",
]

JOB: Final = "trade.preview"
KIND: Final = "trade.preview"
FX_SERIES: Final = (("pub", "ECOS:731Y003:usdkrw"), ("prv", "ECOS:731Y001:usdkrw_close"))
CONSENSUS_LOOKBACK_DAYS: Final = 120
DIGEST_MAX: Final = 30
DISCLAIMER: Final = "통관 기반 추정 — 매매 권유가 아니다"


class PreviewRepos(Protocol):
    """`kbj.store.repos.Repos`·`MemoryRepos`·API 저장소 묶음 모두 맞는다."""

    @property
    def trade(self) -> TradeRepo: ...

    @property
    def fin(self) -> FinRepo: ...

    @property
    def filings(self) -> FilingsRepo: ...

    @property
    def consensus(self) -> ConsensusRepo: ...

    @property
    def macro(self) -> MacroPublicRepo: ...

    @property
    def macro_prv(self) -> MacroPrivateRepo: ...


def monthly_index(rows: Sequence[StockExportRow]) -> dict[str, Decimal | None]:
    return {r.period: r.export_usd for r in rows if len(r.period) == 7}


def monthly_fx(repos: PreviewRepos, ym_from: str, ym_to: str) -> dict[str, float]:
    """{YYYY-MM: 그 달 일별 값 평균}. 앞 시리즈에 값이 하나라도 있으면 그것만."""
    start, end = f"{ym_from}-01", f"{ym_to}-31"
    for tier, sid in FX_SERIES:
        src = repos.macro_prv if tier == "prv" else repos.macro
        acc: dict[str, list[float]] = defaultdict(list)
        for r in src.series([sid], start, end).get(sid, []):
            if r.value is not None:
                acc[r.period[:7]].append(float(r.value))
        if acc:
            return {ym: sum(v) / len(v) for ym, v in sorted(acc.items())}
    return {}


def annual_consensus(repos: PreviewRepos, code: str, today: date) -> dict[int, int]:
    """최근 스냅의 12월 결산 추정 매출 {연도: 원}. 스냅이 없으면 빈 dict."""
    snaps = repos.consensus.snapshots(code, today - timedelta(days=CONSENSUS_LOOKBACK_DAYS), today)
    out: dict[int, int] = {}
    for r in latest_snapshot(snaps, today) if snaps else []:
        if r.is_estimate and r.fiscal_ym.endswith("12") and r.revenue is not None:
            out[int(r.fiscal_ym[:4])] = r.revenue
    return out


def stock_preview(
    repos: PreviewRepos,
    code: str,
    *,
    today: date,
    cfg: PreviewCfg = PreviewCfg(),  # noqa: B008 — 얼린 dataclass
    fx: Mapping[str, float] | None = None,
    corp_of: Mapping[str, tuple[str, str]] | None = None,
    exports: Sequence[StockExportRow] | None = None,
) -> tuple[Preview | None, Preview | None]:
    """(지금 프리뷰, 마지막 달을 뺀 프리뷰). 지수가 없으면 (None, None)."""
    monthly = monthly_index(exports if exports is not None else repos.trade.stock_export(code=code))
    have = sorted(ym for ym, v in monthly.items() if v is not None)
    if not have:
        return None, None
    if fx is None:
        fx = monthly_fx(repos, prev_ym(have[-1], 60), have[-1])
    hit = (corp_of if corp_of is not None else repos.filings.corp_index()).get(code)
    revenue = {} if hit is None else quarter_revenue(repos.fin.quarters(hit[0]))
    cons = annual_consensus(repos, code, today)
    now = preview(monthly, revenue, fx=fx, annual_consensus=cons, cfg=cfg)
    before = {ym: v for ym, v in monthly.items() if ym != have[-1]}
    prev = (
        preview(before, revenue, fx=fx, annual_consensus=cons, cfg=cfg) if len(have) > 1 else None
    )
    return now, prev


def _pct(v: float) -> str:
    return f"{v * 100:+.1f}%"


def _line(code: str, name: str, now: Preview, prev: Preview | None) -> str:
    was = "신규" if prev is None or prev.verdict is None else prev.verdict
    diff = "" if now.diff is None else f" {_pct(now.diff)}"
    return f"- {name}({code}) {now.quarter} {was} → {now.verdict}{diff} · 신뢰 {now.confidence}"


def digest(last_ym: str, changes: Sequence[tuple[str, str, Preview, Preview | None]]) -> str:
    """판정 변화 묶음 본문(규칙 문장). changes = (코드, 이름, 지금, 마지막 달 뺀 것)."""
    head = f"[실적 프리뷰] {last_ym} 통관 반영 — 판정이 바뀐 종목 {len(changes)}개"
    body = [_line(*c) for c in changes[:DIGEST_MAX]]
    if len(changes) > DIGEST_MAX:
        body.append(f"외 {len(changes) - DIGEST_MAX}종목 — 페이지 7 '종목' 탭")
    tail = (
        "추정 = 분기 수출(원 환산) 회귀, 컨센서스 = 연간 추정 − 공시 분기의 남은 분기 평균. "
        + DISCLAIMER
    )
    return "\n".join([head, *body, tail])


Notify = Callable[..., Any]


def run_preview(
    repos: PreviewRepos,
    *,
    today: date,
    notify: Notify | None,
    loaded_by: str = JOB,
    cfg: PreviewCfg = PreviewCfg(),  # noqa: B008 — 얼린 dataclass
) -> JobResult:
    exports = repos.trade.stock_export()
    if not exports:
        return JobResult(
            status="not_ready", detail={"reason": "수출 지수 없음 — trade.stock_index 먼저"}
        )
    by_code: dict[str, list[StockExportRow]] = defaultdict(list)
    for r in exports:
        by_code[r.code].append(r)
    months = sorted({r.period for r in exports if len(r.period) == 7})
    last_ym = months[-1]
    fx = monthly_fx(repos, prev_ym(last_ym, 60), last_ym)
    corp_of = repos.filings.corp_index()
    names = {r.code: r.name for r in repos.trade.stock_map()}
    changes: list[tuple[str, str, Preview, Preview | None]] = []
    verdicts: dict[str, int] = defaultdict(int)
    for code in sorted(by_code):
        now, prev = stock_preview(
            repos, code, today=today, cfg=cfg, fx=fx, corp_of=corp_of, exports=by_code[code]
        )
        if now is None or now.verdict is None:
            continue
        verdicts[now.verdict] += 1
        if prev is None or prev.verdict != now.verdict:
            changes.append((code, names.get(code, code), now, prev))
    detail: dict[str, Any] = {
        "last_ym": last_ym, "stocks": len(by_code), "verdicts": dict(sorted(verdicts.items())),
        "changes": len(changes), "fx_months": len(fx),
    }  # fmt: skip
    if changes:
        if notify is None:
            raise RuntimeError("자원 notify 가 주입되지 않았다 — 알림을 넣지 못했다")
        notify(digest(last_ym, changes), kind=KIND, source=loaded_by, as_of=today, subject=last_ym)
    return JobResult(status="ok", rows=len(changes), detail=detail)


def _repos(ctx: JobContext) -> PreviewRepos:
    repos = ctx.resources.get("repos")
    if repos is not None:
        return cast(PreviewRepos, repos)
    return pg_repos(ctx.resource("connect"))


def run_job(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.engine.trade_preview:run_job`)."""
    try:
        today = date.fromisoformat(ctx.as_of[:10])
    except ValueError:
        today = to_kst(ctx.now).date()
    return run_preview(
        _repos(ctx), today=today, notify=ctx.resources.get("notify"), loaded_by=ctx.job or JOB
    )
