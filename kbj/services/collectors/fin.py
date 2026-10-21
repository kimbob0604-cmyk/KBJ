"""재무 수집 — `fin.quarterly`(30분 폴링)·`fin.backfill`(수동 5년 백필). 공개 작업(원천 DART).

docs/p4_design.md §1.5·§3.2·§3.5·§4.5, D-P4-4·5·25, ADR 0014. 승격·대체 원본: ET
`board/ingest/financials.py:collect`:173·`fetch_batch`:165, SD
`dart_collector.fetch_5y_quarterly`:298.

흐름(`quarterly`)
1. DB 대기 상태 폴링(D-P4-4): 정기보고서 공시(`pub_filings.disclosure` kind=periodic,
   `fin_loaded_at` 없음)를 읽는다. 제목('분기보고서 (2026.09)')과 결산월(`corp_profile.acc_mt`)로
   (사업연도, 보고서)를 정한다(`period.report_of_title`). 상장사가 아니거나 제목을 못 읽으면
   적재하지 않고 처리 표시만 한다 (작업 detail 에 사유별 수 — 대기 목록이 쓰레기로 막히지 않게).
2. 재무 2층(D-P4-5): **상세 대상**(관심종목 ∪ 컨센서스 유니버스 ∪ 공개 데모 — 자원
   `detail_universe`) 이면 단일회사 전체 재무제표(`fnlttSinglAcntAll`, CFS → 비면 OFS)를 받아 원시
   행을 `pub_fin.statement_raw` 에 두고 그 회사를 원시 행 전부로 다시 정규화한다(+ 주식총수
   `stockTotqySttus`). 나머지는 KST 07:30 뒤 그날 첫 실행이 다중회사 주요계정(`fnlttMultiAcnt`,
   100개 묶음)으로 받는다 — 원시 행은 저장하지 않고, 직전 분기 누적은 저장한 분기 단독 행에서
   되살린다.
   차분할 직전 누적이 없으면 그 자리에서 받는다(2026-10-21 — 한 분기가 비면 그 해 남은 분기가
   전부 '차분 불가'로 굳었다): 상세 층은 그 회계연도 앞 보고서 원시 행(`_ensure_prior_raws`),
   주요계정 층은 직전 보고서 묶음 한 번(`_main_prev`). 이미 굳은 행은 `fin.backfill` 이 고친다
   (상세 층 `_repair_full`, 주요계정 층은 차분 불가 행을 다시 받는다).
3. 분기 단독(`normalize` — dart-report `to_quarterly` 하나)·비율(`ratios`)을
   `pub_fin.quarterly`·`ratio` 에 쓰고, 처리한 공시에 `fin_loaded_at` 을 찍는다.

데이터 키(D-P4-25 ② — event 키는 실행기가 미리 잡지 않는다 → 이 처리기가 자원 `claims` 로 claim →
complete/fail. `JobResult.collected` 에는 넣지 않는다 — run-once 때 실행기가 미리 잡은
`<데이터셋>@<as_of>` 키(`ctx.keys`)만 돌려준다)
- `DART:fnlttSinglAcntAll` `<corp>:<사업연도>:<보고서>:<CFS|OFS>:<접수번호>` — 접수번호(촉발한
  정기보고서)를 키에 넣어 정정본이 새 키로 다시 받히게 한다 [제안 — 카탈로그 노트 갱신은 M 에 요청]
- `DART:fnlttMultiAcnt` `<실행일>:<사업연도>:<보고서>:<묶음 n>`(카탈로그 노트 그대로)
- `DART:stockTotqySttus` `<corp>:<접수번호>`(카탈로그 노트 그대로)

경계(계약 ⑬ — 공개 작업): 로그인 원천·엔진·저장소를 import 하지 않는다. 저장소는
`kbj.store.repos.public`(패키지 이름 `kbj.store.repos` 를 쓰지 않는다), DART 는
`DartClient.from_settings`(P3 `_p3` 도우미를 쓰지 않는다 — KIS·KRX 를 끌어온다). 상세
대상(관심종목이 섞인 종목코드 집합)은 로그인 쪽이 자원 `detail_universe`(호출 가능 — `(KST 날짜) →
종목코드들`)로 넘긴다 [제안 — 스케줄러 자원은 M·S 에 요청]. 없으면 `config/reports.yaml public_demo`
만 상세 층이고 detail 에 그 사실을 남긴다.

오류는 삼키지 않는다: 건별 실패는 그 키를 fail 로 남기고(다음 실행이 다시 잡는다) 나머지를 계속한 뒤
작업은 `failed`(detail 에 수). 예산 닫힘·한도 초과(`BudgetExhausted`·`DartQuotaExceeded`)는 그
자리에서 멈추고 `failed`. 백필(`backfill`)은 백필 상한에 닿으면 멈추고 `ok` + detail
`stopped=budget`(여러 날에 나눠 돈다 — 이미 받은 원시 행·분기는 건너뛴다).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time
from typing import Any, Final, Literal, Protocol, cast

from kbj.config.fin import FinConfig, ReportsConfig, load_fin, load_reports
from kbj.core.fin_rows import (
    REPRT,
    CorpProfile,
    DisclosureRow,
    QuarterRow,
    StatementRaw,
    quarter_of_reprt,
    reprt_of_quarter,
)
from kbj.core.time import KST
from kbj.data.budget import BudgetExhausted
from kbj.data.public.dart.client import DartClient, DartQuotaExceeded
from kbj.data.public.dart.disclosures import DartJson
from kbj.data.public.dart.doctext import ParseError
from kbj.data.public.dart.largest_holder import fetch_largest_holder
from kbj.data.public.dart.shares import fetch_share_count
from kbj.data.ratelimit import Priority
from kbj.data.spec import DataKey
from kbj.engines.financials.normalize import (
    Report,
    cumulative_from_quarters,
    quarter_rows,
    report_of_raw,
)
from kbj.engines.financials.period import fiscal_period, report_of_title
from kbj.engines.financials.ratios import ratios
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos.public import PublicRepos, pg_public_repos

__all__ = [
    "BATCH",
    "MAIN_FROM",
    "DartFin",
    "FinRun",
    "backfill",
    "detail_corps",
    "quarterly",
    "renormalize",
]

SOURCE: Final = "DART"
BATCH: Final = 100  # fnlttMultiAcnt 한 번에 회사 수(DART 상한)
MAIN_FROM: Final = time(7, 30)  # 주요계정 묶음은 KST 07:30 뒤 그날 첫 실행
LISTED: Final = frozenset({"Y", "K", "N"})  # 유가·코스닥·코넥스(DART corp_cls)
_STOP: Final = (BudgetExhausted, DartQuotaExceeded)
FsDiv = Literal["CFS", "OFS"]
FS_DIVS: Final[tuple[FsDiv, ...]] = ("CFS", "OFS")


def _fs(value: str) -> FsDiv:
    if value == "CFS":
        return "CFS"
    if value == "OFS":
        return "OFS"
    raise ValueError(f"fs_div 는 CFS·OFS: {value!r}")


class DartFin(Protocol):
    """이 작업이 부르는 DART(`DartClient` 그대로 맞음)."""

    def financials(
        self, corp_code: str, year: int, reprt_code: str, fs_div: str = "CFS"
    ) -> list[dict[str, Any]]: ...

    def get_json(
        self, endpoint: str, params: Mapping[str, Any], *, use_cache: bool = True
    ) -> dict[str, Any]: ...


@dataclass
class FinRun:
    """한 실행의 의존성(시험은 메모리 저장소·가짜 DART·메모리 선점을 넣는다)."""

    repos: PublicRepos
    client: DartFin
    claims: Any  # kbj.services.scheduler.claims.ClaimStore
    fin_cfg: FinConfig
    reports_cfg: ReportsConfig
    job: str
    run_id: str
    now: datetime
    detail_codes: frozenset[str]  # 종목코드(관심종목 ∪ 컨센 유니버스 ∪ 공개 데모)
    detail_source: str  # 'resource' | 'public_demo_only'
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    failures: list[str] = field(default_factory=list[str])
    # 응답 모양을 못 읽은 회사(백필 주식총수·최대주주 — 다시 받아도 같다): 실패로 세지 않고 적기만
    unparsed: list[str] = field(default_factory=list[str])

    @property
    def kst(self) -> datetime:
        return self.now.astimezone(KST)

    def bump(self, key: str, n: int = 1) -> None:
        self.counts[key] += n

    def claim(self, dataset: str, as_of: str) -> DataKey | None:
        key = DataKey(SOURCE, dataset, as_of)
        if not self.claims.claim(key, self.job, self.run_id, self.now):
            self.bump(f"{dataset}.duplicate")
            return None
        return key

    def is_done(self, dataset: str, as_of: str) -> bool:
        """그 키를 이미 누가 끝냈는가(선점 거절 — 진행 중과 구별)."""
        row = self.claims.live(DataKey(SOURCE, dataset, as_of))
        return row is not None and row.status == "done"

    def done(self, key: DataKey, rows: int | None) -> None:
        self.claims.complete(key, self.run_id, rows, self.now)

    def fail(self, key: DataKey, exc: BaseException) -> None:
        reason = f"{type(exc).__name__}: {exc}"[:300]
        self.claims.fail(key, self.run_id, reason, self.now)
        self.failures.append(f"{key.dataset}@{key.as_of}: {type(exc).__name__}")


# ── 의존성 ──────────────────────────────────────────────────────────────────────────


def detail_corps(run: FinRun, corp_index: Mapping[str, tuple[str, str]] | None = None) -> set[str]:
    """상세 대상 종목코드 → corp_code(상장사 고유번호 표에 있는 것만)."""
    idx = corp_index if corp_index is not None else run.repos.filings.corp_index()
    return {idx[c][0] for c in run.detail_codes if c in idx}


def _detail_codes(
    ctx: JobContext, reports_cfg: ReportsConfig, day: date
) -> tuple[frozenset[str], str]:
    demo = set(reports_cfg.public_demo)
    provider: Callable[[date], Iterable[str]] | None = ctx.resources.get("detail_universe")
    if provider is None:
        return frozenset(demo), "public_demo_only"
    return frozenset(set(provider(day)) | demo), "resource"


def _run(ctx: JobContext, *, backfill: bool) -> FinRun:
    fin_cfg: FinConfig = ctx.resources.get("fin_config") or load_fin(settings=ctx.settings)
    reports_cfg: ReportsConfig = ctx.resources.get("reports_config") or load_reports(
        settings=ctx.settings
    )
    repos: PublicRepos = ctx.resources.get("public_repos") or pg_public_repos(
        ctx.resource("connect")
    )
    client = ctx.resources.get("dart")
    if client is None:
        if ctx.settings is None:
            raise RuntimeError("설정이 주입되지 않았다(KBJ_DART_API_KEY)")
        client = DartClient.from_settings(
            ctx.settings,
            ctx.resource("redis"),
            backfill=backfill,
            priority=Priority.P4 if backfill else Priority.P3,
        )
    codes, how = _detail_codes(ctx, reports_cfg, ctx.now.astimezone(KST).date())
    return FinRun(
        repos=repos,
        client=client,
        claims=ctx.resource("claims"),
        fin_cfg=fin_cfg,
        reports_cfg=reports_cfg,
        job=ctx.job,
        run_id=ctx.run_id,
        now=ctx.now,
        detail_codes=codes,
        detail_source=how,
    )


def _close(run: FinRun, ctx: JobContext) -> None:
    if ctx.resources.get("dart") is None and isinstance(run.client, DartClient):
        run.client.close()


# ── 정규화(회사 단위) ────────────────────────────────────────────────────────────────


def renormalize(run: FinRun, corp_code: str, profile: CorpProfile | None) -> tuple[int, int]:
    """원시 행(상세 층) 전부로 그 회사의 분기·비율을 다시 만든다 → (분기 행 수, 비율 행 수)."""
    fin = run.repos.fin
    by_fs: dict[str, list[Report]] = defaultdict(list)
    for year, reprt, fs in sorted(fin.raw_keys(corp_code)):
        raw = fin.raw(corp_code, year, reprt, fs)
        if raw is not None and raw.rows:
            by_fs[fs].append(report_of_raw(raw))
    rows: list[QuarterRow] = []
    for fs in sorted(by_fs):
        rows += quarter_rows(
            by_fs[fs],
            profile,
            tier="full",
            corp_code=corp_code,
            financial_prefixes=run.fin_cfg.valuation.financial_ksic_prefixes,
        )
    nq = fin.upsert_quarters(rows, loaded_by=run.job, now=run.now) if rows else 0
    return nq, _ratios(run, corp_code)


def _fetch_raw(run: FinRun, corp_code: str, year: int, reprt: str) -> bool:
    """전체 재무제표 한 보고서(CFS → 비면 OFS)를 받아 원시 행으로 둔다. 받았으면 True."""
    for fs in FS_DIVS:
        rows = run.client.financials(corp_code, year, reprt, fs)
        if rows:
            rcept = next((str(r["rcept_no"]) for r in rows if r.get("rcept_no")), None)
            currency = next((str(r["currency"]) for r in rows if r.get("currency")), None)
            run.repos.fin.put_raw(
                StatementRaw(corp_code, year, reprt, fs, rcept, tuple(rows), currency),
                loaded_by=run.job,
                now=run.now,
            )
            return True
    return False


def _ensure_prior_raws(
    run: FinRun, corp_code: str, year: int, q: int, profile: CorpProfile | None
) -> int:
    """그 회계연도의 앞 보고서(1 … q-1) 원시 행이 없으면 받는다 → 받은 보고서 수.

    분기 단독은 직전 누적과의 차분이라(2Q = 반기 − 1Q, 4Q = 연간 − 3분기 누적) 앞 보고서 원시
    행이 없으면 그 분기가 '차분 불가'로 굳는다. 상세 대상이 된 뒤 처음 온 보고서(그 전 분기는
    주요계정 층이었거나 수집 전)가 그랬다(2026-10-21). 이미 있는 보고서는 다시 부르지 않는다.
    """
    have = run.repos.fin.raw_keys(corp_code)
    added = 0
    for pq in range(1, q):
        reprt = reprt_of_quarter(pq)
        if any((year, reprt, fs) in have for fs in FS_DIVS):
            continue
        if not _ended(run, year, reprt, profile):
            continue
        if _fetch_raw(run, corp_code, year, reprt):
            added += 1
    if added:
        run.bump("full.prior", added)
    return added


def _repair_full(run: FinRun, profiles: Mapping[str, CorpProfile]) -> None:
    """상세 대상 회사의 전체 재무제표 층 '차분 불가' 분기 — 앞 보고서 원시 행을 받아 다시 정규화.

    주요계정 백필은 full 행을 덮지 못하므로(저장소 층 규칙) 층이 main 이어도 이 길이 따로 돈다.
    """
    for corp in sorted(detail_corps(run)):
        broken = sorted(
            {
                (r.fiscal_year, r.fiscal_q)
                for r in run.repos.fin.quarters(corp)
                if r.tier == "full" and r.derived
            }
        )
        added = sum(_ensure_prior_raws(run, corp, y, q, profiles.get(corp)) for y, q in broken)
        if added:
            nq, nr = renormalize(run, corp, profiles.get(corp))
            run.bump("repaired.quarters", nq)
            run.bump("ratios", nr)


def _ratios(run: FinRun, corp_code: str) -> int:
    quarters = run.repos.fin.quarters(corp_code)
    if not quarters:
        return 0
    rr = ratios(quarters, run.repos.filings.share_counts(corp_code))
    return run.repos.fin.upsert_ratios(rr, loaded_by=run.job, now=run.now) if rr else 0


def _raw_of(
    corp_code: str, year: int, reprt: str, fs: str, rows: Sequence[Mapping[str, Any]]
) -> StatementRaw:
    rcept = next((str(r["rcept_no"]) for r in rows if r.get("rcept_no")), None)
    return StatementRaw(corp_code, year, reprt, _fs(fs), rcept, tuple(rows))


def _main_needs_prev(run: FinRun, corp_code: str, year: int, q: int, fss: Iterable[str]) -> bool:
    """그 분기를 차분할 직전 누적을 저장한 분기 단독 행으로 되살릴 수 없는가(어느 기준이든)."""
    if q <= 1:
        return False
    existing = run.repos.fin.quarters(corp_code)
    return any(cumulative_from_quarters(existing, year, q - 1, fs) is None for fs in fss)


def _main_prev(
    run: FinRun,
    got: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]],
    year: int,
    reprt: str,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """직전 누적을 되살릴 수 없는 회사만 직전 보고서 주요계정을 한 번 더 받는다(같은 묶음 하나).

    저장한 분기가 비었거나(수집 시작 전·백필 전) 차분 불가였으면 이 분기도 차분 불가로 굳고, 그 해
    남은 분기가 줄줄이 같이 굳었다(2026-10-21 — 2Q·3Q·4Q 전부 '차분 불가'). 직전 보고서의 누적
    (`thstrm_add_amount`)을 바로 쓰면 앞 분기 단독 행이 없어도 차분이 된다.
    """
    q = quarter_of_reprt(reprt)
    need = sorted(c for c, by_fs in got.items() if _main_needs_prev(run, c, year, q, by_fs))
    if not need:
        return {}
    prev_reprt = reprt_of_quarter(q - 1)
    run.bump("main.prev_fetch", len(need))
    return _multi(run, need, year, prev_reprt)


def _main_rows(
    run: FinRun,
    corp_code: str,
    year: int,
    reprt: str,
    rows_by_fs: Mapping[str, Sequence[Mapping[str, Any]]],
    profile: CorpProfile | None,
    prev_by_fs: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> list[QuarterRow]:
    """주요계정 응답(회사 하나) → 그 분기의 분기 단독 행.

    직전 누적은 저장한 분기에서 되살리고, 되살릴 수 없으면 `prev_by_fs`(직전 보고서 주요계정 응답 —
    `_main_prev`)의 누적을 쓴다. 둘 다 없으면 차분 불가(derived).
    """
    q = quarter_of_reprt(reprt)
    out: list[QuarterRow] = []
    existing = run.repos.fin.quarters(corp_code)
    for fs, rows in sorted(rows_by_fs.items()):
        reports = [report_of_raw(_raw_of(corp_code, year, reprt, fs, rows))]
        if q > 1:
            prev = cumulative_from_quarters(existing, year, q - 1, fs)
            prev_rows = (prev_by_fs or {}).get(fs)
            if prev is None and prev_rows:
                prev_raw = _raw_of(corp_code, year, reprt_of_quarter(q - 1), fs, prev_rows)
                prev = report_of_raw(prev_raw)
            if prev is not None:
                reports.insert(0, prev)
        out += quarter_rows(
            reports,
            profile,
            tier="main",
            corp_code=corp_code,
            financial_prefixes=run.fin_cfg.valuation.financial_ksic_prefixes,
            keep=[(year, q)],
        )
    return out


# ── fin.quarterly ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Job:
    disclosure: DisclosureRow
    year: int
    reprt: str


def _plan(
    run: FinRun, pending: Sequence[DisclosureRow]
) -> tuple[list[_Job], list[_Job], dict[str, CorpProfile], list[str]]:
    """대기 공시 → (상세 층, 주요계정 층, 기업개황, 처리 표시만 할 접수번호)."""
    profiles = run.repos.filings.profiles({d.corp_code for d in pending})
    detail = detail_corps(run)
    full: list[_Job] = []
    main: list[_Job] = []
    skip: list[str] = []
    for d in pending:
        if d.corp_cls not in LISTED or not d.stock_code:
            run.bump("skipped.unlisted")
            skip.append(d.rcept_no)
            continue
        prof = profiles.get(d.corp_code)
        acc_mt = prof.acc_mt if prof is not None else None
        rep = report_of_title(d.title, acc_mt)
        if rep is None and (prof is None or prof.acc_mt is None):
            # 결산월을 아직 모른다(기업개황은 토요일 수집) — 12월로 읽히지 않는 제목(비12월 결산)은
            # 처리 표시를 하지 않고 다음 실행으로 미룬다(잘못된 분기로 적재하지 않게)
            run.bump("waiting.profile")
            continue
        if rep is None:
            run.bump("skipped.title")
            skip.append(d.rcept_no)
            continue
        job = _Job(d, rep[0], rep[1])
        (full if d.corp_code in detail else main).append(job)
    return full, main, profiles, skip


def _load_full(run: FinRun, job: _Job, profile: CorpProfile | None) -> bool:
    """상세 층 한 건 — CFS(비면 OFS) 원시 행 + 주식총수. 처리했으면 True."""
    d = job.disclosure
    corp = d.corp_code
    got_rows = False
    for fs in FS_DIVS:
        as_of = f"{corp}:{job.year}:{job.reprt}:{fs}:{d.rcept_no}"
        key = run.claim("fnlttSinglAcntAll", as_of)
        if key is None:
            if not run.is_done("fnlttSinglAcntAll", as_of):
                return False  # 다른 실행이 받는 중 — 그 실행이 처리 표시를 한다
            # 이미 받았다(앞 실행이 처리 표시 전에 멈췄다) — 원시 행이 있으면 다시 정규화만
            if run.repos.fin.raw(corp, job.year, job.reprt, fs) is not None:
                got_rows = True
                break
            continue
        try:
            rows = run.client.financials(corp, job.year, job.reprt, fs)
            if rows:
                rcept = next((str(r["rcept_no"]) for r in rows if r.get("rcept_no")), d.rcept_no)
                currency = next((str(r["currency"]) for r in rows if r.get("currency")), None)
                run.repos.fin.put_raw(
                    StatementRaw(corp, job.year, job.reprt, fs, rcept, tuple(rows), currency),
                    loaded_by=run.job,
                    now=run.now,
                )
                got_rows = True
            run.done(key, len(rows))
        except _STOP:
            run.fail(key, RuntimeError("예산 닫힘·한도 초과"))
            raise
        except Exception as exc:
            run.fail(key, exc)
            return False
        if got_rows:
            break
    run.bump("full.reports" if got_rows else "full.empty")
    if got_rows:
        try:
            _ensure_prior_raws(run, corp, job.year, quarter_of_reprt(job.reprt), profile)
        except _STOP:
            raise
        except Exception as exc:  # 다음 실행이 다시(받은 원시 행은 남는다 — 다시 정규화만)
            run.failures.append(f"fnlttSinglAcntAll@{corp}:{job.year}:prior: {type(exc).__name__}")
            return False
    skey = run.claim("stockTotqySttus", f"{corp}:{d.rcept_no}")
    if skey is not None:
        try:
            sc = fetch_share_count(
                cast(DartJson, run.client),
                corp,
                job.year,
                job.reprt,
                rcept_dt=d.rcept_dt,
            )
            if sc is not None:
                run.repos.filings.put_share_counts([sc], loaded_by=run.job, now=run.now)
            run.done(skey, 0 if sc is None else 1)
        except _STOP:
            run.fail(skey, RuntimeError("예산 닫힘·한도 초과"))
            raise
        except Exception as exc:
            run.fail(skey, exc)
    hkey = run.claim("hyslrSttus", f"{corp}:{d.rcept_no}")
    if hkey is not None:
        try:
            lh = fetch_largest_holder(
                cast(DartJson, run.client), corp, job.year, job.reprt, rcept_dt=d.rcept_dt
            )
            if lh is not None:
                run.repos.filings.put_largest_holders([lh], loaded_by=run.job, now=run.now)
            run.done(hkey, 0 if lh is None else 1)
        except _STOP:
            run.fail(hkey, RuntimeError("예산 닫힘·한도 초과"))
            raise
        except Exception as exc:
            run.fail(hkey, exc)
    nq, nr = renormalize(run, corp, profile)
    run.bump("quarters", nq)
    run.bump("ratios", nr)
    return True


def _multi(
    run: FinRun, corps: Sequence[str], year: int, reprt: str
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """다중회사 주요계정 → {corp: {fs_div: 행들}}."""
    js = run.client.get_json(
        "fnlttMultiAcnt.json",
        {"corp_code": ",".join(corps), "bsns_year": str(year), "reprt_code": reprt},
        use_cache=False,
    )
    out: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for r in js.get("list") or []:
        if not isinstance(r, Mapping):
            continue
        corp, fs = str(r.get("corp_code") or ""), str(r.get("fs_div") or "")
        if corp in corps and fs in ("CFS", "OFS"):
            out[corp][fs].append({str(k): v for k, v in cast(Mapping[Any, Any], r).items()})
    return out


def _load_main(
    run: FinRun,
    jobs: Sequence[_Job],
    profiles: Mapping[str, CorpProfile],
    *,
    day: date,
    batch_key: Callable[[int, str, int], str],
) -> list[str]:
    """주요계정 층 — (사업연도, 보고서)별 100개 묶음. 처리한 공시 접수번호."""
    loaded: list[str] = []
    groups: dict[tuple[int, str], list[_Job]] = defaultdict(list)
    for j in jobs:
        groups[(j.year, j.reprt)].append(j)
    # 1Q → 4Q 순서(직전 누적을 저장한 분기에서 되살린다 — 보고서 코드 문자열 순서가 아니다)
    for (year, reprt), js in sorted(
        groups.items(), key=lambda kv: (kv[0][0], quarter_of_reprt(kv[0][1]))
    ):
        corps = sorted({j.disclosure.corp_code for j in js})
        for n, i in enumerate(range(0, len(corps), BATCH), 1):
            chunk = corps[i : i + BATCH]
            key = run.claim("fnlttMultiAcnt", batch_key(year, reprt, n))
            if key is None:
                continue
            try:
                got = _multi(run, chunk, year, reprt)
                prev = _main_prev(run, got, year, reprt)
                rows: list[QuarterRow] = []
                for corp in chunk:
                    if corp in got:
                        rows += _main_rows(
                            run, corp, year, reprt, got[corp], profiles.get(corp), prev.get(corp)
                        )
                if rows:
                    run.repos.fin.upsert_quarters(rows, loaded_by=run.job, now=run.now)
                for corp in chunk:
                    run.bump("ratios", _ratios(run, corp))
                run.done(key, len(rows))
                run.bump("main.quarters", len(rows))
            except _STOP:
                run.fail(key, RuntimeError("예산 닫힘·한도 초과"))
                raise
            except Exception as exc:
                run.fail(key, exc)
                continue
            loaded += [j.disclosure.rcept_no for j in js if j.disclosure.corp_code in chunk]
    return loaded


def _result(
    run: FinRun, ctx: JobContext, *, stopped: str | None = None, ok_on_stop: bool = False
) -> JobResult:
    """건별 실패가 있거나(fail 로 남긴 키) 예산이 닫혀 멈췄으면 failed(백필의 멈춤은 ok)."""
    detail: dict[str, Any] = {
        **dict(sorted(run.counts.items())),
        "detail_source": run.detail_source,
        "failures": len(run.failures),
    }
    if run.failures:
        detail["failed_keys"] = run.failures[:10]
        detail["reason"] = f"{len(run.failures)}건 실패 — 첫 {run.failures[0]}"[:200]
    if run.unparsed:  # 회사 하나의 응답 모양 — 재시도로 바뀌지 않아 실패로 두지 않는다
        detail["unparsed"] = run.unparsed[:10]
        detail["n_unparsed"] = len(run.unparsed)
    if stopped:
        detail["stopped"] = stopped
    status: Literal["ok", "failed"] = "ok"
    if run.failures or (stopped and not ok_on_stop):
        status = "failed"
    return JobResult(status, collected=ctx.keys, rows=run.counts.get("quarters", 0), detail=detail)


def quarterly(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.collectors.fin:quarterly`)."""
    run = _run(ctx, backfill=False)
    try:
        pending = run.repos.filings.fin_unloaded(kinds=("periodic",), limit=500)
        if not pending:
            return JobResult(
                "skipped", collected=ctx.keys, detail={"reason": "대기 정기보고서 없음"}
            )
        full, main, profiles, skip = _plan(run, pending)
        loaded = list(skip)
        stopped: str | None = None
        try:
            for job in full:
                if _load_full(run, job, profiles.get(job.disclosure.corp_code)):
                    loaded.append(job.disclosure.rcept_no)
            if main:
                day = run.kst.date()
                if run.kst.time() >= MAIN_FROM:
                    loaded += _load_main(
                        run,
                        main,
                        profiles,
                        day=day,
                        batch_key=lambda y, r, n: f"{day.isoformat()}:{y}:{r}:{n}",
                    )
                else:
                    run.bump("main.waiting", len(main))
        except _STOP as exc:
            stopped = type(exc).__name__
        if loaded:
            run.repos.filings.mark_fin_loaded(loaded, now=run.now)
        run.bump("loaded", len(loaded))
        return _result(run, ctx, stopped=stopped)
    finally:
        _close(run, ctx)


# ── fin.backfill ───────────────────────────────────────────────────────────────────


def _years(run: FinRun) -> list[int]:
    return list(range(run.fin_cfg.backfill.from_year, run.kst.year + 1))


def _ended(run: FinRun, year: int, reprt: str, profile: CorpProfile | None) -> bool:
    """그 보고서의 기간이 오늘(KST) 전에 끝났는가 — 아직 오지 않은 보고서는 부르지 않는다."""
    acc_mt = profile.acc_mt if profile is not None else None
    return fiscal_period(year, reprt, acc_mt).end < run.kst.date()


SHARE_TRIES: Final = 3  # 회사마다 최근 보고서부터 몇 개까지 주식총수를 찾아볼지


def _recent_periods(run: FinRun, prof: CorpProfile | None) -> list[tuple[int, str]]:
    """기간이 끝난 정기보고서(연도, 보고서) — 최근부터 SHARE_TRIES 개."""
    periods = sorted(
        ((y, r) for y in _years(run) for r in REPRT if _ended(run, y, r, prof)),
        key=lambda p: (p[0], quarter_of_reprt(p[1])),
        reverse=True,
    )
    return periods[:SHARE_TRIES]


def _backfill_shares(run: FinRun, profiles: Mapping[str, CorpProfile]) -> None:
    """상세 대상 회사 중 주식총수가 없는 회사 — 기간이 끝난 최근 정기보고서부터 찾아 하나 넣는다.

    분기 증분(`_load_full`)은 새 정기보고서가 올 때만 주식총수를 받아, 이미 지난 보고서의 회사는
    다음 분기 공시(11월 중순)까지 비었다(2026-10-20 — 오버행·내부자 비율, 지수 유동비율).
    최대주주 현황(2026-10-21 — 지수 유동비율의 전략적 보유)도 같은 방식으로 채운다."""
    for corp in sorted(detail_corps(run)):
        if run.repos.filings.latest_share_count(corp) is not None:
            continue
        for year, reprt in _recent_periods(run, profiles.get(corp)):
            try:
                sc = fetch_share_count(cast(DartJson, run.client), corp, year, reprt)
            except ParseError as e:  # 그 회사만 — 다음 회사는 계속(격리)
                run.unparsed.append(f"stockTotqySttus@{corp}: {e}"[:200])
                break
            if sc is not None:
                if sc.rcept_dt is None:  # 접수일 = 접수번호 앞 8자리(없으면 PIT 읽기에서 빠진다)
                    sc = replace(sc, rcept_dt=date(int(sc.rcept_no[:4]), int(sc.rcept_no[4:6]),
                                                   int(sc.rcept_no[6:8])))  # fmt: skip
                run.repos.filings.put_share_counts([sc], loaded_by=run.job, now=run.now)
                run.bump("shares")
                break
        else:
            run.bump("shares.none")
    for corp in sorted(detail_corps(run)):
        if run.repos.filings.latest_largest_holder(corp) is not None:
            continue
        for year, reprt in _recent_periods(run, profiles.get(corp)):
            try:
                lh = fetch_largest_holder(cast(DartJson, run.client), corp, year, reprt)
            except ParseError as e:
                run.unparsed.append(f"hyslrSttus@{corp}: {e}"[:200])
                break
            if lh is not None:
                run.repos.filings.put_largest_holders([lh], loaded_by=run.job, now=run.now)
                run.bump("holders")
                break
        else:
            run.bump("holders.none")


def _backfill_full(run: FinRun, profiles: Mapping[str, CorpProfile]) -> None:
    corps = sorted(detail_corps(run))
    for corp in corps:
        have = run.repos.fin.raw_keys(corp)
        added = 0
        for year in _years(run):
            for reprt in REPRT:
                if any((year, reprt, fs) in have for fs in FS_DIVS):
                    continue
                if not _ended(run, year, reprt, profiles.get(corp)):
                    continue
                if _fetch_raw(run, corp, year, reprt):
                    added += 1
        run.bump("full.reports", added)
        nq, nr = renormalize(run, corp, profiles.get(corp))
        run.bump("quarters", nq)
        run.bump("ratios", nr)


def _main_missing(run: FinRun, corp_code: str, year: int, q: int) -> bool:
    """주요계정 백필이 그 분기를 (다시) 받아야 하는가 — 행이 없거나, 주요계정 행뿐인데 차분 불가.

    전체 재무제표 행(full)은 주요계정 행이 덮지 못한다(저장소 층 규칙) — 그 차분 불가는
    `_repair_full` 이 직전 원시 행을 받아 고친다.
    """
    rows = [
        r for r in run.repos.fin.quarters(corp_code) if (r.fiscal_year, r.fiscal_q) == (year, q)
    ]
    if not rows:
        return True
    return all(r.tier == "main" for r in rows) and any(r.derived for r in rows)


def _backfill_main(run: FinRun, profiles: Mapping[str, CorpProfile]) -> None:
    """주요계정 층 백필 — 상장사 전부(상세 대상 포함: 층이 main 이면 상세 대상의 지난 분기를 채우는
    길이 이것뿐이다 — 2026-10-21 관심종목의 지난 분기가 비어 차분 불가). 주요계정 행은 전체 재무제표
    행을 덮지 않는다."""
    idx = run.repos.filings.corp_index()
    corps = sorted({c for c, _n in idx.values()})
    for year in _years(run):
        for reprt in REPRT:  # 1Q → 4Q 순서(직전 누적을 되살릴 수 있게)
            q = quarter_of_reprt(reprt)
            need = [
                c
                for c in corps
                if _ended(run, year, reprt, profiles.get(c)) and _main_missing(run, c, year, q)
            ]
            for i in range(0, len(need), BATCH):
                chunk = need[i : i + BATCH]
                got = _multi(run, chunk, year, reprt)
                prev = _main_prev(run, got, year, reprt)
                rows: list[QuarterRow] = []
                for corp in chunk:
                    if corp in got:
                        rows += _main_rows(
                            run, corp, year, reprt, got[corp], profiles.get(corp), prev.get(corp)
                        )
                if rows:
                    run.repos.fin.upsert_quarters(rows, loaded_by=run.job, now=run.now)
                run.bump("main.quarters", len(rows))
    for corp in corps:
        run.bump("ratios", _ratios(run, corp))


def backfill(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.collectors.fin:backfill` — run-once 로만).

    층·시작 연도는 `config/fin.yaml backfill`. 백필 상한(`dart.backfill_cap`)에 닿으면 멈추고 `ok` +
    detail `stopped` — 다음 날 다시 돌리면 이미 받은 원시 행·분기는 건너뛴다.
    """
    run = _run(ctx, backfill=True)
    try:
        profiles = run.repos.filings.profiles()
        stopped: str | None = None
        try:
            _backfill_shares(run, profiles)
            _repair_full(run, profiles)
            if run.fin_cfg.backfill.tier == "full":
                _backfill_full(run, profiles)
            else:
                _backfill_main(run, profiles)
        except _STOP as exc:
            stopped = type(exc).__name__
        run.bump(f"tier.{run.fin_cfg.backfill.tier}")
        return _result(run, ctx, stopped=stopped, ok_on_stop=True)
    finally:
        _close(run, ctx)
