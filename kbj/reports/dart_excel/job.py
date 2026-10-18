"""등록부 작업 `reports.dart_excel` — 관심종목 ∪ 공개 데모 중 재무가 바뀐 종목의 리포트(엑셀 +
블록).

docs/p4_design.md §0.4·§1.5·§3.2·§4.6, ADR 0014. 매일 21:00(`as_of: run_date`). legacy 의 분기 cron
(2·5·8·11월 16일 — ET `report.yml`)은 이 작업이 덮는다.

- 대상: 관심종목(`prv_journal.watchlist` 'main' — 자원 `journal`, 없으면 Pg) ∪ `config/reports.yaml
  public_demo`. DART 고유번호가 없는 종목(우선주 등)·분기 재무가 아직 없는 회사·재무 통화가 원화가
  아닌 회사(엑셀이 억원 단위 — §8.4 #19)는 만들지 않는다(detail 에 사유별 수).
- **재무가 바뀐 종목만**: 입력 요약(`input_digest` — 엔진 판·매핑·연도·분기 재무 행·공급계약 공시)이
  최근 리포트와 같으면 건너뛴다(DART 를 부르지 않는다).
- DART 접근(`ReportSource`): 재무제표는 `pub_fin.statement_raw`(수집 작업이 받아 둔 원시 행 — 같은
  행이면 legacy 와 같은 결과)를 먼저 읽고, 공시 목록·원문은 한 실행 안에서 한 번만 받는다(주석 비용·
  브릿지가 같은 원문을 두 번 읽던 legacy 호출을 반으로).
- 데이터 키 `DART:report_inputs @ <종목>:<실행일>`(event — 처리기가 자원 `claims` 로 선점).
  run-once 때 실행기가 잡은 키(`ctx.keys`)는 그대로 돌려준다.
- 결과: `pub_fin.report`(payload·xlsx ≤ 2 MiB·경고·엔진 판·입력 요약) + 사업보고서 원문의 부문 매출
  (`pub_fin.segment` — best-effort, estimated) [제안 — 등록부 `writes` 에 더하기는 S 에 요청].
- 오류는 삼키지 않는다: 종목별 실패는 키를 fail 로 남기고 다음 종목을 계속한 뒤 `failed`.
  예산 닫힘·한도 초과는 그 자리에서 멈추고 `failed`.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Final, Literal

from kbj.config.files import config_path
from kbj.config.fin import ReportsConfig, load_reports
from kbj.core.fin_rows import ReportRow
from kbj.core.time import KST
from kbj.data.budget import BudgetExhausted
from kbj.data.public.dart.client import DartClient, DartQuotaExceeded
from kbj.data.spec import DataKey
from kbj.engines.financials.costs import find_periodic_reports, load_mapping
from kbj.engines.financials.orders import CONTRACT_PATTERNS
from kbj.engines.financials.segments import parse_segments
from kbj.reports.dart_excel.pipeline import NoStatements, ReportInputs, collect_inputs, render
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos.fin import FinRepo
from kbj.store.repos.journal import JournalRepo, PgJournalRepo
from kbj.store.repos.public import PublicRepos, pg_public_repos

__all__ = [
    "ENGINE_VERSION",
    "CachedSource",
    "ReportTooLarge",
    "build_report",
    "input_digest",
    "run",
    "targets",
]

ENGINE_VERSION: Final = "dart_excel/1"  # 빌더·파이프라인을 바꾸면 올린다(입력이 같아도 다시 만든다)
SOURCE: Final = "DART"
DATASET: Final = "report_inputs"
_STOP: Final = (BudgetExhausted, DartQuotaExceeded)


class ReportTooLarge(ValueError):
    """xlsx 가 저장 상한(`config/reports.yaml xlsx_max_bytes` — 0011 CHECK)보다 크다."""


class CachedSource:
    """리포트 한 벌의 DART 접근 — 원시 행 캐시 + 한 실행 안의 공시 목록·원문 메모.

    재무제표: `pub_fin.statement_raw` 에 있으면 그 행. 요청 기준(CFS)이 없고 다른 기준(OFS)만 있으면
    수집 때 CFS 가 비었다는 뜻이라 빈 목록(legacy 폴백과 같은 결과 — 호출 없이). 둘 다 없으면 DART.
    """

    def __init__(
        self, client: Any, fin: FinRepo, corp_index: Mapping[str, tuple[str, str]]
    ) -> None:
        self._client = client
        self._fin = fin
        self._idx = corp_index
        self._lists: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        self._docs: dict[str, list[tuple[str, str]]] = {}
        self.hits = 0
        self.calls = 0

    def corp_code(self, stock_code: str) -> tuple[str, str]:
        code = str(stock_code).strip().zfill(6)
        if code not in self._idx:
            raise LookupError(f"종목코드 {code} 가 DART 고유번호 표(pub_filings.corp_code)에 없다")
        return self._idx[code]

    def financials(
        self, corp_code: str, year: int, reprt_code: str, fs_div: str = "CFS"
    ) -> list[dict[str, Any]]:
        raw = self._fin.raw(corp_code, year, reprt_code, fs_div)
        if raw is not None:
            self.hits += 1
            return [dict(r) for r in raw.rows]
        other = "OFS" if fs_div == "CFS" else "CFS"
        if fs_div == "CFS" and self._fin.raw(corp_code, year, reprt_code, other) is not None:
            self.hits += 1
            return []
        self.calls += 1
        return list(self._client.financials(corp_code, year, reprt_code, fs_div))

    def disclosures(
        self, corp_code: str, bgn_de: str, end_de: str, pblntf_ty: str | None = None
    ) -> list[dict[str, Any]]:
        key = (corp_code, bgn_de, end_de, pblntf_ty)
        if key not in self._lists:
            self.calls += 1
            self._lists[key] = list(
                self._client.disclosures(corp_code, bgn_de, end_de, pblntf_ty=pblntf_ty)
            )
        return [dict(r) for r in self._lists[key]]

    def document_texts(self, rcept_no: str) -> list[tuple[str, str]]:
        if rcept_no not in self._docs:
            self.calls += 1
            self._docs[rcept_no] = list(self._client.document_texts(rcept_no))
        return list(self._docs[rcept_no])


def _years(reports_cfg: ReportsConfig, day: date) -> tuple[int, int]:
    return day.year - reports_cfg.years_back, day.year


def input_digest(
    repos: PublicRepos,
    corp_code: str,
    *,
    years: tuple[int, int],
    fs_div: str,
    mapping_sha: str,
    day: date,
) -> str:
    """리포트 입력 요약 — 같으면 다시 만들지 않는다(DART 를 부르지 않고 DB 만으로)."""
    quarters = [
        [q.fiscal_year, q.fiscal_q, q.fs_div, q.tier, str(q.rcept_dt), q.revenue, q.op, q.ni]
        for q in repos.fin.quarters(corp_code)
        if years[0] <= q.fiscal_year <= years[1] + 1
    ]
    contracts = sorted(
        d.rcept_no
        for d in repos.filings.disclosures(start=date(years[0], 1, 1), end=day, corp_code=corp_code)
        if any(p.search(d.title) for p in CONTRACT_PATTERNS)
    )
    blob = json.dumps(
        {
            "engine": ENGINE_VERSION,
            "mapping": mapping_sha,
            "years": list(years),
            "fs_div": fs_div,
            "quarters": quarters,
            "contracts": contracts,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def targets(journal: JournalRepo, reports_cfg: ReportsConfig) -> list[str]:
    """관심종목('main') ∪ 공개 데모 — 종목코드 오름차순."""
    return sorted(journal.watch_codes("main") | set(reports_cfg.public_demo))


def build_report(
    inputs: ReportInputs, *, day: date, now: datetime, digest: str, max_bytes: int
) -> ReportRow:
    """리포트 입력 → `pub_fin.report` 행(xlsx + 블록 — 같은 입력)."""
    xlsx, payload = render(inputs, now)
    if len(xlsx) > max_bytes:
        raise ReportTooLarge(f"{inputs.stock}: xlsx {len(xlsx)}B > {max_bytes}B")
    fs_used: Literal["CFS", "OFS"] = "CFS" if "CFS" in inputs.fs_used else "OFS"
    return ReportRow(
        stock_code=inputs.stock,
        built_on=day,
        corp_code=inputs.corp_code,
        year_from=inputs.years[0],
        year_to=inputs.years[-1],
        fs_div_used=fs_used,
        payload=payload,
        xlsx=xlsx,
        warnings=tuple(inputs.warnings),
        engine_version=ENGINE_VERSION,
        input_digest=digest,
        generated_at=now,
    )


def _segments(source: CachedSource, inputs: ReportInputs) -> list[Any]:
    """사업보고서(4Q) 원문의 부문 매출(연도마다 첫 표). 원문은 주석 파싱 때 받은 것을 다시 쓴다."""
    rows: list[Any] = []
    reports = find_periodic_reports(source, inputs.corp_code, inputs.years)
    for (year, quarter), rcept in sorted(reports.items()):
        if quarter != "4Q":
            continue
        for _fname, html in source.document_texts(rcept):
            got = parse_segments(html, corp_code=inputs.corp_code, bsns_year=year, rcept_no=rcept)
            if got:
                rows += got
                break
    return rows


@dataclass
class _Tally:
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    failures: list[str] = field(default_factory=list[str])

    def bump(self, key: str, n: int = 1) -> None:
        self.counts[key] += n


def run(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.reports.dart_excel.job:run`)."""
    reports_cfg: ReportsConfig = ctx.resources.get("reports_config") or load_reports(
        settings=ctx.settings
    )
    repos: PublicRepos = ctx.resources.get("public_repos") or pg_public_repos(
        ctx.resource("connect")
    )
    journal: JournalRepo = ctx.resources.get("journal") or PgJournalRepo(ctx.resource("connect"))
    claims = ctx.resource("claims")
    mapping_path = config_path(reports_cfg.mapping, settings=ctx.settings)
    mapping = load_mapping(mapping_path)
    mapping_sha = hashlib.sha256(mapping_path.read_bytes()).hexdigest()
    client = ctx.resources.get("dart")
    own_client = client is None
    if client is None:
        if ctx.settings is None:
            raise RuntimeError("설정이 주입되지 않았다(KBJ_DART_API_KEY)")
        client = DartClient.from_settings(ctx.settings, ctx.resource("redis"))
    log: Callable[[str], None] | None = ctx.resources.get("report_log")
    now = ctx.now
    day = now.astimezone(KST).date()
    years = _years(reports_cfg, day)
    tally = _Tally()
    stopped: str | None = None
    try:
        idx = repos.filings.corp_index()
        for code in targets(journal, reports_cfg):
            if code not in idx:
                tally.bump("skipped.not_in_dart")  # 우선주·상장폐지 — 보통주 리포트를 쓴다
                continue
            corp, _name = idx[code]
            quarters = repos.fin.quarters(corp)
            if not quarters:
                tally.bump("skipped.no_financials")
                continue
            if any(q.currency != "KRW" for q in quarters):
                tally.bump("skipped.currency")  # 엑셀은 억원(원화) 전제 — 환산하지 않는다
                continue
            digest = input_digest(
                repos,
                corp,
                years=years,
                fs_div=reports_cfg.fs_div,
                mapping_sha=mapping_sha,
                day=day,
            )
            prev = repos.fin.report(code)
            if prev is not None and prev.input_digest == digest:
                tally.bump("skipped.unchanged")
                continue
            key = DataKey(SOURCE, DATASET, f"{code}:{day.isoformat()}")
            if not claims.claim(key, ctx.job, ctx.run_id, now):
                tally.bump("skipped.duplicate")
                continue
            source = CachedSource(client, repos.fin, idx)
            try:
                inputs = collect_inputs(
                    source, code, years, reports_cfg.fs_div, mapping, now=now, log=log
                )
                row = build_report(
                    inputs,
                    day=day,
                    now=now,
                    digest=digest,
                    max_bytes=reports_cfg.xlsx_max_bytes,
                )
                repos.fin.put_report(row, loaded_by=ctx.job)
                segs = _segments(source, inputs)
                if segs:
                    repos.fin.put_segments(segs, loaded_by=ctx.job, now=now)
                claims.complete(key, ctx.run_id, 1, now)
                tally.bump("built")
                tally.bump("segments", len(segs))
                tally.bump("dart_calls", source.calls)
                tally.bump("raw_hits", source.hits)
            except NoStatements:
                claims.complete(key, ctx.run_id, 0, now)
                tally.bump("skipped.no_statements")
            except _STOP as exc:
                claims.fail(key, ctx.run_id, type(exc).__name__, now)
                stopped = type(exc).__name__
                break
            except Exception as exc:
                claims.fail(key, ctx.run_id, f"{type(exc).__name__}: {exc}"[:300], now)
                tally.failures.append(f"{code}: {type(exc).__name__}")
    finally:
        if own_client and isinstance(client, DartClient):
            client.close()
    detail: dict[str, Any] = {**dict(sorted(tally.counts.items())), "failures": len(tally.failures)}
    if tally.failures:
        detail["failed"] = tally.failures[:10]
    if stopped:
        detail["stopped"] = stopped
    status: Literal["ok", "failed"] = "failed" if tally.failures or stopped else "ok"
    if status == "failed":
        # 실행 기록(ops.job_run)에 사유가 남게 — 없으면 '키 0개를 받지 못했다' 만 보인다
        detail["reason"] = (
            f"중단: {stopped}(예산·한도 — 다음 실행이 이어 받는다)"
            if stopped
            else f"{len(tally.failures)}종목 실패: {', '.join(tally.failures[:3])}"
        )
    return JobResult(status, collected=ctx.keys, rows=tally.counts.get("built", 0), detail=detail)
