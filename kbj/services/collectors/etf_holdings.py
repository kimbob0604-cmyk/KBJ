"""`etf.collect` — 운용사 9곳 구성종목(PDF) 수집 + 구성종목 변동 분석(docs/p3_design.md §1.5·
D-P3-12).

승격 원본: ET `etf_tracker_v9/tracker.py` — `build_universe`:109(네이버 종목명 조인·TOP10 폴백은
버리고 KRX 메타·유형 규칙으로), `snapshot`:188(빈 응답 연속 `empty_streak` → 추적 제외), `analyze`
(엔진 `kbj.engines.etf.holdings`). 텔레그램 리포트(`build_report`·발송)는 P5(등록부 note).

한 번 실행(데이터 키 `ETF_ISSUERS:pdf @ trade_date`)

1. 메타 분류: `prv_etf.meta` 의 이름·기초지수로 운용사·브랜드·테마(`classify`)·유형(`etf_type`)·
   배수를 채운다(`kbj.engines.etf.types.typed_meta` — 바뀐 행만 쓴다). KRX 메타 갱신(`krx.daily`)은
   이름·기초지수만 바꾸고 분류 칸은 지킨다.
2. 운용사마다 **격리**해 받는다(스레드 하나씩 — 호스트 리미터가 운용사마다 따로라 서로 기다리지
   않는다. 한 운용사 실패가 다른 운용사를 멈추지 않는다 — 절대 규칙 4):
   - 목록(`universe`) → 추적 대상 = 유형이 국내 대표지수·국내 테마인 펀드(ET: 네이버 탭 1·2 +
     `classify` 가 None 아님. 해외·레버리지·채권·원자재는 국내 주식 시그널이 아니라 뺀다). 이름은
     KRX 메타(티커로) → 운용사 목록 순. 이름이 없으면 건너뛴다. 목록이 실패하면 저장된 그 운용사의
     추적 펀드로 PDF 만 받는다.
   - PDF(`holdings`) → 국내 종목이 있으면 그 **실제 기준일**로 `prv_etf.holding` 스냅을 통째로
     바꾼다(재수집 멱등). 비면 `empty_streak` +1, `EMPTY_LIMIT`(3)회 연속이면 추적 제외(해외형
     오분류 — ET 그대로). 네트워크 오류는 연속에 세지 않는다. 기준일이 요청일보다 뒤면 받지 않는다.
3. 저장은 이 스레드(주 스레드)에서만 한다.
4. 변동: 펀드별 최근 두 스냅(`fund_pairs` — 간격 ≤ `etf.holdings.max_gap_days`)을
   `analyze`(qty_floor·action_pp·min_base_for_cu)로 비교해 **실행일 행을 통째로 한 번에** 바꾼다
   (`EtfRepo.put_changes` — 펀드마다 부르면 앞 펀드 변동이 지워진다). ETF 가 담은 ETF 는 뺀다
   (코드 = `prv_etf.meta` 의 ETF 코드).

결과: 운용사 하나라도 통째로 실패(목록·PDF 모두 못 받음)하면 `failed`(등록부 재시도 3×900초 —
다시 돌려도 같은 결과), 아니면 `ok`. detail 에 운용사별 수(펀드·스냅·빈 응답·오류·제외)와 변동 수.

자원(`JobContext.resources`): `repos`(없으면 Pg), `markets`, `etf_issuers`(어댑터 목록 — 없으면
`IssuerHttp.for_service(redis)` 로 9곳), `etf_issuer_keys`(일부만), `parallel`(기본 True).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any, Final

from kbj.config.markets import HoldingsCfg
from kbj.core.rows import Change, EtfMeta, EtfType, Fund, HoldingRow
from kbj.core.time import kst_date
from kbj.data.private.etf_issuers.base import FundRef, IssuerAdapter, IssuerHttp, source_of
from kbj.data.private.etf_issuers.registry import build
from kbj.engines.etf.flows import c3_checks, window_flows
from kbj.engines.etf.holdings import analyze, fund_pairs
from kbj.engines.etf.types import classify, etf_type, is_active, typed_meta
from kbj.services.collectors._p3 import RepoSet, markets, reason, repos
from kbj.services.runtime.log import log_event
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos import EtfRepo

__all__ = [
    "EMPTY_LIMIT",
    "ENGINE_VERSION",
    "IssuerRun",
    "accept",
    "analyze_changes",
    "collect_issuer",
    "record_flow_checks",
    "run",
]

log = logging.getLogger("kbj.services.collectors.etf_holdings")
SERVICE: Final = "scheduler"
ENGINE_VERSION: Final = "kbj.engines.etf.holdings/1"
# ET tracker.py EMPTY_LIMIT — [확인 필요] config/markets.yaml 로 옮길 것(묶음 M 요청)
EMPTY_LIMIT: Final = 3
TRACKED_TYPES: Final = frozenset({EtfType.KR_INDEX, EtfType.KR_THEME})
DETAIL_ERRORS_MAX: Final = 8


@dataclass
class IssuerRun:
    """운용사 하나의 수집 결과(저장 전)."""

    key: str
    funds: dict[str, Fund] = field(default_factory=dict[str, Fund])
    snaps: list[tuple[str, date, list[HoldingRow]]] = field(
        default_factory=list[tuple[str, date, list[HoldingRow]]]
    )
    empty: set[str] = field(default_factory=set[str])
    errors: dict[str, str] = field(default_factory=dict[str, str])
    universe_error: str | None = None
    skipped: int = 0  # 목록에서 뺀 펀드(이름 없음·추적 유형 아님)

    @property
    def failed(self) -> bool:
        """통째로 실패 — 목록도 PDF 도 하나도 못 받았다."""
        attempted = len(self.snaps) + len(self.empty) + len(self.errors)
        if self.universe_error is not None and not self.snaps and not self.empty:
            return True
        return attempted > 0 and not self.snaps and not self.empty


def accept(name: str, base_index: str | None) -> tuple[str, bool] | None:
    """추적 대상이면 (테마, 액티브 여부), 아니면 None. 유형이 국내 대표지수·국내 테마인 것만."""
    if etf_type(name, base_index) not in TRACKED_TYPES:
        return None
    theme = classify(name)
    if theme is None:
        return None
    return theme, is_active(name)


def _fund_rows(
    adapter: IssuerAdapter,
    refs: Sequence[FundRef],
    meta: Mapping[str, EtfMeta],
    old: Mapping[str, Fund],
) -> tuple[dict[str, Fund], int]:
    out: dict[str, Fund] = {}
    skipped = 0
    for ref in refs:
        fid = f"{adapter.KEY}:{ref.fund_key}"
        m = meta.get(ref.ticker or "")
        name = ((m.name if m is not None else None) or ref.name or "").strip()
        prev = old.get(fid)
        a = accept(name, None if m is None else m.base_index) if name else None
        if a is None:
            skipped += 1
            if prev is not None and prev.track:  # 다시 분류해 보니 대상이 아니다 — 추적 끄기
                out[fid] = replace(prev, track=False)
            continue
        theme, active = a
        out[fid] = Fund(
            fund_id=fid, issuer=adapter.KEY, fund_key=ref.fund_key, ticker=ref.ticker, name=name,
            theme=theme, is_active=active, depth=adapter.DEPTH,
            track=True if prev is None else prev.track,
            empty_streak=0 if prev is None else prev.empty_streak, source=source_of(adapter.KEY),
        )  # fmt: skip
    return out, skipped


def collect_issuer(
    adapter: IssuerAdapter,
    day: date,
    meta: Mapping[str, EtfMeta],
    old_funds: Mapping[str, Fund],
) -> IssuerRun:
    """운용사 하나 — 목록 → 추적 펀드마다 PDF. 저장하지 않는다(주 스레드가 쓴다)."""
    res = IssuerRun(adapter.KEY)
    mine = {k: f for k, f in old_funds.items() if f.issuer == adapter.KEY}
    try:
        refs = adapter.universe()
    except Exception as e:  # 격리 — 저장된 추적 펀드로 PDF 만 받는다(사유는 남긴다)
        res.universe_error = reason(e)
        res.funds = dict(mine)
    else:
        res.funds, res.skipped = _fund_rows(adapter, refs, meta, mine)
    for fid in sorted(res.funds):
        f = res.funds[fid]
        if not f.track:
            continue
        try:
            rows, real = adapter.holdings(f.fund_key, day)
        except Exception as e:  # 격리 — 이 펀드만 미완(연속 빈 응답에 세지 않는다)
            res.errors[fid] = reason(e)
            continue
        if real > day:
            res.errors[fid] = f"기준일 {real} 이 요청일 {day} 보다 뒤 — 받지 않았다"
            continue
        if not rows:
            res.empty.add(fid)
            continue
        res.snaps.append((fid, real, list(rows.values())))
    return res


def _streaks(res: IssuerRun, limit: int) -> tuple[list[Fund], int]:
    """빈 응답 연속 수 갱신 — 받은 펀드 0, 빈 펀드 +1, 연속 limit 회면 추적 제외."""
    got = {fid for fid, _, _ in res.snaps}
    out: list[Fund] = []
    dropped = 0
    for fid in sorted(res.funds):
        f = res.funds[fid]
        if fid in got:
            f = replace(f, empty_streak=0)
        elif fid in res.empty:
            n = f.empty_streak + 1
            off = f.track and n >= limit
            dropped += int(off)
            f = replace(f, empty_streak=n, track=f.track and not off)
        out.append(f)
    return out, dropped


def analyze_changes(
    repo: EtfRepo,
    run_date: date,
    cfg: HoldingsCfg,
    etf_codes: set[str],
    funds: Mapping[str, Fund],
) -> list[Change]:
    """저장된 스냅으로 펀드별 변동(엔진 `fund_pairs`·`analyze`). 저장하지 않는다."""
    out: list[Change] = []
    for pair in fund_pairs(repo.holding_dates(), cfg.max_gap_days):
        f = funds.get(pair.fund_id)
        if f is None:
            continue
        out.extend(
            analyze(
                pair, repo.holdings(pair.fund_id, pair.asof),
                repo.holdings(pair.fund_id, pair.prev_asof), run_date=run_date, depth=f.depth,
                etf_codes=etf_codes, qty_floor=cfg.qty_floor, action_pp=cfg.action_pp,
                min_base=cfg.min_base_for_cu,
            )
        )  # fmt: skip
    return out


def _adapters(ctx: JobContext, today: Callable[[], date]) -> tuple[list[IssuerAdapter], Any]:
    got = ctx.resources.get("etf_issuers")
    if got is not None:
        return list(got), None
    http = IssuerHttp.for_service(ctx.resource("redis"))
    return build(http, ctx.resources.get("etf_issuer_keys"), today=today), http


def _classify_meta(repo: EtfRepo, ctx: JobContext) -> tuple[dict[str, EtfMeta], int]:
    meta = repo.meta()
    changed = [t for m in meta.values() if (t := typed_meta(m)) != m]
    if changed:
        repo.upsert_meta(changed, loaded_by=ctx.job, now=ctx.now)
        meta = {**meta, **{m.code: m for m in changed}}
    return meta, len(changed)


def run(ctx: JobContext) -> JobResult:
    day = date.fromisoformat(ctx.as_of[:10])
    now: datetime = ctx.now
    cfg = markets(ctx).etf.holdings
    repo = repos(ctx).etf
    meta, n_typed = _classify_meta(repo, ctx)
    old_funds = repo.funds()
    adapters, http = _adapters(ctx, lambda: kst_date(now))
    try:
        jobs = [lambda a=a: collect_issuer(a, day, meta, old_funds) for a in adapters]
        if ctx.resources.get("parallel", True) and len(jobs) > 1:
            with ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="etf-issuer") as ex:
                results = list(ex.map(lambda j: j(), jobs))
        else:
            results = [j() for j in jobs]
    finally:
        if http is not None:
            http.close()

    detail: dict[str, Any] = {"meta_typed": n_typed, "issuers": {}}
    errors: dict[str, str] = {}
    failed: list[str] = []
    rows = 0
    for res in results:
        funds, dropped = _streaks(res, EMPTY_LIMIT)
        repo.upsert_funds(funds, loaded_by=ctx.job, now=now)
        for fid, asof, hs in res.snaps:
            rows += repo.put_holdings(fid, asof, hs, loaded_by=ctx.job, received_at=now)
        detail["issuers"][res.key] = {
            "funds": sum(1 for f in funds if f.track), "snapshots": len(res.snaps),
            "empty": len(res.empty), "errors": len(res.errors), "dropped": dropped,
            "skipped": res.skipped,
        }  # fmt: skip
        if res.universe_error is not None:
            errors[f"{res.key}:universe"] = res.universe_error
        for fid, why in sorted(res.errors.items())[:2]:
            errors[fid] = why
        if res.failed:
            failed.append(res.key)
        if res.errors or res.universe_error:
            log_event(log, logging.WARNING, SERVICE, "etf_issuer_partial", job=ctx.job,
                      issuer=res.key, errors=len(res.errors),
                      universe_failed=res.universe_error is not None)  # fmt: skip

    funds_now = repo.funds()
    changes = analyze_changes(repo, day, cfg, set(meta), funds_now)
    repo.put_changes(day, changes, engine_version=ENGINE_VERSION, now=now)
    detail["changes"] = len(changes)
    detail["pairs_funds"] = len({c.fund_id for c in changes})
    if errors:
        detail["errors"] = dict(sorted(errors.items())[:DETAIL_ERRORS_MAX])
    detail["rows"] = rows
    if failed:
        detail["reason"] = f"운용사 {len(failed)}곳 통째로 실패 — {', '.join(failed)}"
        return JobResult("failed", collected=(), rows=rows, detail=detail)
    return JobResult("ok", collected=tuple(ctx.keys), rows=rows, detail=detail)


def record_flow_checks(
    rs: RepoSet,
    day: date,
    *,
    now: datetime,
    loaded_by: str,
    tol: float,
    ratios: Sequence[int],
    unit: float = 1.0,
) -> dict[str, int]:
    """그날 ETF 흐름의 검산 ③ 실패(`prv_flows.ledger_check` c3)와 감지한 분할·병합
    (`prv_etf.split_event` origin=detected — 수동 표가 있으면 저장소가 덮지 않는다)을 기록한다.

    KRX ETF 일별(`krx.daily` 의 `etp/etf_bydd_trd`)을 쓴 뒤 부르는 자리다(묶음 C 요청 — 이 모듈은
    함수만 둔다). 원장에 그날과 그 전 거래일이 둘 다 있어야 한다(없으면 0건). 멱등이다.
    """
    by_code = rs.etf.etf_days(day, 2)
    tdays = sorted({d.date for rows in by_code.values() for d in rows})
    if len(tdays) < 2 or tdays[-1] != day:
        return {"flows": 0, "split_detected": 0, "c3_failed": 0}
    meta = rs.etf.meta()
    events = rs.etf.split_events()
    flows = []
    detected = 0
    for code in sorted(by_code):
        m = meta.get(code)
        for f in window_flows(
            by_code[code], 1, trading_days=tdays, splits=events.get(code, ()), tol=tol,
            ratios=ratios, unit=unit, listed_on=None if m is None else m.listed_on,
            delisted_on=None if m is None else m.delisted_on,
        ):  # fmt: skip
            if f.date != day:
                continue
            flows.append(f)
            if f.split is not None and f.split.origin == "detected":
                detected += int(rs.etf.put_split_event(f.split, loaded_by=loaded_by, now=now))
    checks = c3_checks(flows, now)
    if checks:
        rs.flows.put_checks(checks)
    return {"flows": len(flows), "split_detected": detected, "c3_failed": len(checks)}
