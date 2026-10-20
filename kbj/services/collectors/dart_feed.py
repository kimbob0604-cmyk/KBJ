"""`filings.dart_feed` — DART 공시 증분 피드 + 새 공시의 구조화 API·원문 파싱(공개 작업).

승격 원본: SD `server.py:poll_dart_disclosures`:10166(매분 1쪽부터 최대 10쪽 — 처음부터 다시),
`earnings_parser.process_disclosure`:396, `overhang_parser.collect_overhang_for_stock`:266.
docs/p4_design.md §1.3·§3.2·§4.1~§4.4·D-P4-3·D-P4-25, ADR 0013.

한 실행(평일 07:00~19:59 매분 — 등록부 `as_of: minute`)
1. **다시 훑기**(그날 첫 실행 — `config/fin.yaml filings.feed.rescan_at` 이후): 전 영업일(KRX 달력의
   전 거래일)부터 어제까지 목록 전체를 한 번 더 읽어 19:59 뒤 접수분·놓친 행을 채운다. 하루 한 번은
   선점 장부의 키 `DART:list @ <시작일>~<끝일>` 로 보장한다(같은 날 두 번 훑지 않는다).
2. **증분**: 그날 목록을 1쪽부터 읽다 이미 본 접수번호가 나온 쪽에서 멈춘다(`feed.scan_new` — 정렬 =
   접수 내림차순 [확인 필요 #28]). 새 행을 `pub_filings.disclosure` 에 넣는다(`kind` = `feed_kind` —
   키워드 점수는 `filings.derive`(묶음 E)가 채운다. 이 모듈은 E 를 import 하지 않는다).
3. **파싱**: 상장사 공시 중 `prelim`·`pre_announce`·`overhang`·`overhang_exercise`·`price_adjust`·
   `buyback`(취득·처분 결정)·`insider`·`major_holder` 는 `parse_state=pending` 으로 넣고, 대기 행(앞
   실행의 실패 포함 — 시도 < `retry_parse_max`, n 번 실패한 건은 처음 본 뒤 `retry_wait_min[n-1]`
   분이 지나야 — DART 원문·구조화 응답이 늦게 붙는다)마다 접수번호 데이터 키 `DART:<데이터셋> @
   <접수번호>` 를 자원 `claims`(실행기와 같은 선점 장부)로 직접 잡고 → 구조화 API 또는 원문 → 각 표
   → 키 complete/fail(D-P4-25 ② — 이 키는 `JobResult.collected` 에 넣지 않는다).

실패 격리(절대 규칙 4)
- 한 건의 해석 실패 = 그 건만 `parse_state=failed`(시도 + 1), 상한이면 `na` + 사유. 다른 건은 계속.
  예상 밖 예외(해석기 버그 등)도 그 건만(traceback 로그 + 사유 — ADR 0013 §4.1-12).
- 전송 오류·DART 점검(800)·5xx = 그 실행의 파싱을 멈추고 실패(시도 수를 쓰지 않는다 — 다음 분 다시).
- `020`(일 한도)·예산 소진 = 그날 파싱 중단, 실행 실패(사유). 다음 날 07:00 다시 훑기가 빠진 목록을
  채운다(§3.5). 키·IP 오류(critical)는 그대로 올린다(실행기가 실패 + 알림).
- 증분 목록 조회 실패는 그대로 올린다 — 그 실행 실패(다음 분). 다시 훑기 실패는 그 실행을
  실패(사유)로 남기되 증분·파싱은 돈다(§4.1-13). 증분이 `max_pages` 에서 끊기면 같은 실행에서 그날
  목록 전체를 이어 읽는다(§4.1-17).
- 동시 실행: 실행기는 분이 다른 실행을 동시에 돌린다 — 같은 작업의 다른 실행이 방금 잡은 키는
  건너뛴다(`CLAIM_LIVE_MIN` — §4.1-11).

알림은 하지 않는다(일반 공시 push 없음 — D-P4-14). 관심종목 잠정실적 알림은 `earnings.alerts`(묶음
E)가 이 작업이 쓴 `pub_filings.earnings_prelim` 을 읽어 보낸다(DB 대기 상태 폴링 — D-P4-4).
결산실적공시예고는 표를 쓰지 않고 해석 결과를 `parse_note`(`prelim_doc.encode_pre_announce`)에
남긴다 — 실적 일정 표는 `filings.derive` 몫(두 작업이 한 표를 쓰지 않게).

자원(`JobContext.resources`): `claims`(필수), `public_repos`(없으면 `connect` 로 `pg_public_repos`),
`dart`(없으면 `DartClient.from_settings(settings, redis)`), `fin_config`(없으면 `load_fin`),
`kr`(없으면 `TradingCalendar.default()`). 계약 ⑬ — 로그인 모듈·`kbj.store.repos` 패키지
이름·`collectors._p3` 를 import 하지 않는다(공개 작업).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any, Final, Protocol

from kbj.config.fin import FinConfig, load_fin
from kbj.core.calendar import TradingCalendar
from kbj.core.fin_rows import (
    CORP_CLS,
    BuybackEvent,
    DisclosureRow,
    InsiderReport,
    Instrument,
    MajorHolderReport,
    OverhangEvent,
    PrelimRow,
    is_corp_code,
    is_stock_code,
)
from kbj.core.masking import mask_text
from kbj.core.time import to_kst
from kbj.data.budget import BudgetExhausted
from kbj.data.public.dart import overhang_doc, ownership, prelim_doc, structured
from kbj.data.public.dart.client import DartClient, DartError, DartQuotaExceeded
from kbj.data.public.dart.disclosures import DartJson, Disclosure, feed_kind
from kbj.data.public.dart.doctext import ParseError, largest
from kbj.data.public.dart.feed import FeedScan, base_title, is_correction, scan_new
from kbj.data.spec import DataKey
from kbj.services.scheduler.claims import ClaimRow, ClaimStore
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos.public import PublicRepos, pg_public_repos

__all__ = [
    "JOB",
    "PARSE_KINDS",
    "PARSE_LIMIT",
    "RESCAN_MAX_PAGES",
    "FeedClient",
    "FeedReport",
    "collect",
    "datasets_for",
    "run",
    "to_row",
]

JOB: Final = "filings.dart_feed"
SOURCE: Final = "DART"
PARSE_KINDS: Final[tuple[str, ...]] = (
    "prelim",
    "pre_announce",
    "overhang",
    "overhang_exercise",
    "price_adjust",
    "buyback",
    "insider",
    "major_holder",
)
PARSE_LIMIT: Final = 200  # 한 실행이 파싱할 대기 행 상한(8/s → 약 25초 + 원문) [제안]
RESCAN_MAX_PAGES: Final = 100  # 다시 훑기 쪽 상한(10,000건 — 3월 말 사업보고서 마감일 대비) [제안]
LINK_LOOKBACK_YEARS: Final = 7  # 조정·청구 공시의 옛 결정 공시를 찾는 기간(CB 만기 보통 3~5년)
DOC_DATASET: Final = "document"
# 같은 작업의 **다른 실행**이 쥔 선점을 '진행 중'으로 보는 시간 [제안]. 선점 장부는 같은 작업이면
# 다시 잡게 해 준다(재기동이 이어 쓰게 — claims.py) — 그래서 앞 분 실행이 아직 도는 동안(실행기는
# as_of 가 다른 실행을 동시에 돌린다) 다음 분 실행이 같은 접수번호를 또 받지 않도록 처리기가 거른다.
# 이 시간보다 오래된 선점은 죽은 실행의 것으로 보고 이어 잡는다(작업 마감 5분의 두 배).
CLAIM_LIVE_MIN: Final = 10
# 정정 공시가 원 공시를 찾는 기간 — 이보다 오래된 잠정실적은 원 공시로 보지 않는다 [제안]
CORRECTION_LOOKBACK_DAYS: Final = 92

log = logging.getLogger(__name__)


class FeedClient(DartJson, Protocol):
    """처리기가 쓰는 DART 클라이언트 모양(`DartClient` 가 맞는다 — 시험은 가짜 서버 전송)."""

    def document_texts(self, rcept_no: str) -> list[tuple[str, str]]: ...


class _Stop(Exception):
    """이 실행의 파싱을 멈춘다(전송 오류·한도). `day_closed` 면 그날 한도."""

    def __init__(self, reason: str, *, day_closed: bool) -> None:
        super().__init__(reason)
        self.reason = reason
        self.day_closed = day_closed


@dataclass
class FeedReport:
    """한 실행의 요약(기록 `detail` — 값·이름은 싣지 않는다)."""

    new: int = 0
    pages: int = 0
    complete: bool = True
    invalid: int = 0
    rescan: dict[str, Any] | None = None
    parsed: int = 0
    failed: int = 0
    na: int = 0
    skipped: int = 0
    rows: int = 0
    stop: str | None = None
    day_closed: bool = False
    warnings: list[str] = field(default_factory=list[str])

    def detail(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "new": self.new,
            "pages": self.pages,
            "complete": self.complete,
            "parsed": self.parsed,
            "failed": self.failed,
            "na": self.na,
            "rows": self.rows,
        }
        if self.invalid:
            out["invalid"] = self.invalid
        if self.skipped:
            out["claim_skipped"] = self.skipped
        if self.rescan is not None:
            out["rescan"] = self.rescan
        if self.stop is not None:
            out["reason"] = self.stop
        if self.warnings:
            out["warnings"] = self.warnings[:5]
        return out


@dataclass
class _Env:
    repos: PublicRepos
    claims: ClaimStore
    client: FeedClient
    cfg: FinConfig
    kr: TradingCalendar
    now: datetime
    run_id: str
    job: str = JOB
    insider_cache: dict[str, ownership.Ownership[InsiderReport]] = field(
        default_factory=dict[str, ownership.Ownership[InsiderReport]]
    )
    major_cache: dict[str, ownership.Ownership[MajorHolderReport]] = field(
        default_factory=dict[str, ownership.Ownership[MajorHolderReport]]
    )


def _held_elsewhere(env: _Env, live: ClaimRow | None) -> bool:
    """같은 작업의 다른 실행이 방금(CLAIM_LIVE_MIN 안) 잡아 아직 쥐고 있는 선점인가."""
    if live is None or live.status != "claimed" or live.run_id == env.run_id:
        return False
    if live.job != env.job:
        return True  # 다른 작업 — claim 이 거절한다(여기서도 바쁨)
    return env.now - live.claimed_at < timedelta(minutes=CLAIM_LIVE_MIN)


# ── 목록 → 행 ───────────────────────────────────────────────────────────────────────────


def datasets_for(kind: str, title: str) -> tuple[str, ...]:
    """공시 종류·제목 → 받아야 할 데이터셋(선점 키 `DART:<데이터셋> @ <접수번호>`). 빈 튜플 = 파싱
    안 함."""
    if kind == "prelim":
        return (DOC_DATASET,) if prelim_doc.classify(title) not in (None, "PRE_ANNOUNCE") else ()
    if kind in ("pre_announce", "overhang_exercise", "price_adjust"):
        return (DOC_DATASET,)
    if kind in ("overhang", "buyback"):
        return structured.endpoints(structured.targets_of(title))
    if kind == "insider":
        return ("elestock",)
    if kind == "major_holder":
        return ("majorstock",)
    return ()


def to_row(d: Disclosure, *, now: datetime, prefixes: Sequence[str]) -> DisclosureRow:
    """목록 한 행 → `pub_filings.disclosure` 행. 상장사의 파싱 대상이면 `pending`."""
    stock = d.stock_code if is_stock_code(d.stock_code) else None
    kind = feed_kind(d.title)
    parse = stock is not None and bool(datasets_for(kind, d.title))
    return DisclosureRow(
        rcept_no=d.rcept_no,
        corp_code=d.corp_code,
        stock_code=stock,
        corp_name=d.corp_name,
        corp_cls=d.corp_cls if d.corp_cls in CORP_CLS else None,
        title=d.title,
        base_title=base_title(d.title),
        filer=d.filer,
        rcept_dt=date.fromisoformat(d.date),
        remark=d.remark or None,
        kind=kind,
        is_correction=is_correction(d.title, prefixes),
        first_seen_at=now,
        parse_state="pending" if parse else "none",
    )


def _rows(items: Sequence[Disclosure], env: _Env, report: FeedReport) -> list[DisclosureRow]:
    out: list[DisclosureRow] = []
    for d in items:
        try:
            if not is_corp_code(d.corp_code):
                raise ValueError("corp_code 형식")
            out.append(to_row(d, now=env.now, prefixes=env.cfg.filings.correction_prefixes))
        except (ValueError, TypeError) as e:
            report.invalid += 1
            log.warning("공시 목록 행을 넣지 못했다 rcept_no=%s: %s", d.rcept_no[:14], e)
    return out


def _store(scan: FeedScan, env: _Env, report: FeedReport) -> int:
    rows = _rows(scan.items, env, report)
    if not rows:
        return 0
    return env.repos.filings.upsert_disclosures(rows, loaded_by=env.job, now=env.now)


# ── 다시 훑기·증분 ───────────────────────────────────────────────────────────────────────


def _rescan(env: _Env, day: date, report: FeedReport) -> None:
    """그날 첫 실행(rescan_at 이후): 전 거래일 ~ 어제 목록 전체(하루 한 번 — 선점 키)."""
    kst = to_kst(env.now)
    if kst.date() == day and kst.strftime("%H:%M") < env.cfg.filings.feed.rescan_at:
        return
    start = env.kr.prev_trading_day(day)
    end = day - timedelta(days=1)
    key = DataKey(SOURCE, "list", f"{start.isoformat()}~{end.isoformat()}")
    live = env.claims.live(key)
    if live is not None and (live.status == "done" or _held_elsewhere(env, live)):
        return  # 끝났거나 다른 실행이 하는 중(오래된 선점은 죽은 실행 — 이어 잡는다)
    if not env.claims.claim(key, env.job, env.run_id, env.now):
        return
    try:
        scan = scan_new(
            env.client, start, env.repos.filings.seen, max_pages=RESCAN_MAX_PAGES, end=end,
            full=True,
        )  # fmt: skip
        n = _store(scan, env, report)
    except (DartQuotaExceeded, BudgetExhausted):
        env.claims.fail(key, env.run_id, "다시 훑기 실패: 일 한도·예산", env.now)
        raise  # 증분도 같은 한도에 걸린다 — 그 실행 실패
    except DartError as e:
        env.claims.fail(key, env.run_id, f"다시 훑기 실패: {_why(e)}", env.now)
        if e.critical:
            raise
        _rescan_failed(report, e)
        return
    except Exception as e:
        env.claims.fail(key, env.run_id, f"다시 훑기 실패: {_why(e)}", env.now)
        _rescan_failed(report, e)
        return
    env.claims.complete(key, env.run_id, n, env.now)
    report.rescan = {"pages": scan.pages, "new": scan.new, "complete": scan.complete}
    report.new += scan.new
    if not scan.complete:
        msg = f"다시 훑기가 {RESCAN_MAX_PAGES}쪽에서 끊겼다 — 남은 목록은 못 읽었다"
        report.warnings.append(msg)
        log.warning("%s(%s~%s)", msg, start, end)


def _rescan_failed(report: FeedReport, e: BaseException) -> None:
    """다시 훑기 실패는 그 실행의 실패로 남기되(사유) 그날 증분·파싱은 멈추지 않는다(격리 —
    절대 규칙 4). 선점 키는 실패로 놓아 다음 분 실행이 다시 훑는다."""
    report.stop = f"전 영업일 다시 훑기 실패 — 다음 실행이 다시({_why(e)[:160]})"
    report.warnings.append(report.stop)
    log.warning("%s", report.stop, exc_info=not isinstance(e, DartError))


def _incremental(env: _Env, day: date, report: FeedReport) -> None:
    """그날 목록 증분. `max_pages` 에서 끊기면(장애 뒤 첫 실행처럼 밀린 목록) **같은 실행에서**
    그날 목록 전체를 다시 읽어(이미 넣은 것은 거른다 — `full=True`) 나머지를 채운다. 다음 실행의
    증분은 1쪽에서 이미 본 접수번호를 만나 멈추므로, 여기서 채우지 않으면 끊긴 뒤쪽(그날 앞선
    접수분)은 다음 날 07:00 다시 훑기까지 빠진다 — 그 사이의 관심종목 잠정실적이 하루 늦게 울린다
    (설계 §4.1 '다음 분에 이어서')."""
    max_pages = env.cfg.filings.feed.max_pages
    scan = scan_new(env.client, day, env.repos.filings.seen, max_pages=max_pages)
    _store(scan, env, report)
    report.new += scan.new
    report.pages = scan.pages
    report.complete = scan.complete
    if scan.complete:
        return
    log.warning("증분 목록이 %d쪽에서 끊겼다 — 그날 목록 전체를 이어 읽는다(%s)", max_pages, day)
    rest = scan_new(env.client, day, env.repos.filings.seen, max_pages=RESCAN_MAX_PAGES, full=True)
    _store(rest, env, report)
    report.new += rest.new
    report.pages += rest.pages
    report.complete = rest.complete
    report.warnings.append(f"증분 목록이 {max_pages}쪽에서 끊겨 그날 목록 전체를 이어 읽었다")
    if not rest.complete:
        msg = f"그날 목록이 {RESCAN_MAX_PAGES}쪽을 넘는다 — 나머지는 다음 날 다시 훑기가 채운다"
        report.warnings.append(msg)
        log.warning("%s(%s, 새 공시 %d건)", msg, day, scan.new + rest.new)


# ── 파싱 ────────────────────────────────────────────────────────────────────────────────


def _document(env: _Env, rcept_no: str) -> str:
    html = largest(env.client.document_texts(rcept_no))
    if not html:
        raise ParseError("원문이 없다(013·빈 zip)")
    return html


def _prelim(env: _Env, d: DisclosureRow) -> tuple[int, str | None]:
    parsed = prelim_doc.parse(_document(env, d.rcept_no))
    profile = env.repos.filings.profiles([d.corp_code]).get(d.corp_code)
    row = prelim_doc.to_row(
        parsed,
        rcept_no=d.rcept_no,
        corp_code=d.corp_code,
        rcept_dt=d.rcept_dt,
        title=d.title,
        acc_mt=None if profile is None else profile.acc_mt,
        yoy_tol_pp=env.cfg.earnings.yoy_reported_tol_pp,
    )
    if d.is_correction and row.period_inferred:
        row = _period_of_original(env, d, row)
    return env.repos.filings.put_prelims([row], loaded_by=env.job, now=env.now), None


def _period_of_original(env: _Env, d: DisclosureRow, row: PrelimRow) -> PrelimRow:
    """정정 잠정실적의 기간을 원 공시에서(본문 기간이 없어 접수일로 추정했을 때만).

    접수일 추정은 '접수일 직전에 끝난 분기'라, 정정이 분기말을 넘겨 접수되면(예: 2분기 실적을
    10월에 정정) 다음 분기를 가리킨다 — 알림 대상(`회사:기간:범위`)이 갈라져 같은 실적이 두 번
    울리고 기간도 틀린다. 원 공시 = 같은 회사·같은 범위의 앞 접수번호 중 가장 늦은 잠정실적
    (`CORRECTION_LOOKBACK_DAYS` 안 — 그보다 오래된 것은 다른 분기의 실적일 가능성이 크다).
    """
    prior = [
        p
        for p in env.repos.filings.prelims(
            start=d.rcept_dt - timedelta(days=CORRECTION_LOOKBACK_DAYS),
            end=d.rcept_dt,
            corp_code=d.corp_code,
        )
        if p.scope is row.scope and p.rcept_no < row.rcept_no
    ]
    if not prior:
        return row
    orig = max(prior, key=lambda p: p.rcept_no)
    if (orig.fiscal_year, orig.fiscal_q, orig.is_annual, orig.period_end) == (
        row.fiscal_year, row.fiscal_q, row.is_annual, row.period_end,
    ):  # fmt: skip
        return row
    note = (
        f"정정 공시 — 원 공시({orig.rcept_no})의 기간을 따랐다(접수일 추정은 "
        f"{row.fiscal_year} {row.fiscal_q}Q)"
    )
    return replace(
        row,
        fiscal_year=orig.fiscal_year,
        fiscal_q=orig.fiscal_q,
        is_annual=orig.is_annual,
        period_end=orig.period_end,
        notes=(*row.notes, note),
    )


def _pre_announce(env: _Env, d: DisclosureRow) -> tuple[int, str | None]:
    got = prelim_doc.parse_pre_announce_detail(_document(env, d.rcept_no))
    if got is None:
        raise ParseError("결산실적공시예고의 공시 예정일을 읽지 못했다")
    return 0, prelim_doc.encode_pre_announce(got)


def _from_document(d: DisclosureRow, inst: Instrument, doc: dict[str, object]) -> OverhangEvent:
    """구조화 응답에 행이 없을 때 원문만으로 만든 결정 공시(parse_source=document)."""
    base = OverhangEvent(
        rcept_no=d.rcept_no,
        corp_code=d.corp_code,
        rcept_dt=d.rcept_dt,
        instrument=inst,
        face_value=None,
        price=None,
        shares=None,
        refix_floor=None,
        exercise_start=None,
        exercise_end=None,
        maturity=None,
        listing_date=None,
        alloc_method=None,
        parse_source="document",
        notes=("구조화 응답에 이 접수번호가 없다 — 원문으로 읽었다",),
    )
    if inst in (Instrument.PAID_IN, Instrument.BONUS):
        doc = {k: v for k, v in doc.items() if k in ("shares", "price", "listing_date",
                                                      "alloc_method")}  # fmt: skip
    return structured.supplement(base, doc, source="원문")


def _doc_fields(html: str, inst: Instrument) -> dict[str, object]:
    if inst in (Instrument.PAID_IN, Instrument.BONUS):
        return overhang_doc.parse_paid_in(html)
    return overhang_doc.parse_cb_bw(html)


def _decision(env: _Env, d: DisclosureRow) -> tuple[int, str | None]:
    targets = structured.targets_of(d.title)
    if not targets:
        raise ParseError("결정 공시 종류를 제목에서 정하지 못했다")
    events: list[OverhangEvent] = []
    buybacks: list[BuybackEvent] = []
    html: str | None = None
    for target in targets:
        got = structured.fetch_decision(env.client, d.corp_code, d.rcept_dt, d.rcept_no, target)
        if isinstance(got, BuybackEvent):
            buybacks.append(got)
            continue
        if target in structured.BUYBACK_TARGETS:
            raise ParseError("자기주식 결정이 구조화 응답에 없다")
        inst = Instrument(target)
        if got is None or structured.missing(got):
            html = html if html is not None else _document(env, d.rcept_no)
            doc = _doc_fields(html, inst)
            if got is None:
                ev = _from_document(d, inst, doc)
                if not doc:
                    raise ParseError("구조화 응답에도 원문에도 결정 내용이 없다")
            else:
                ev = structured.supplement(got, doc, source="원문")
        else:
            ev = got
        events.append(ev)
    n = 0
    if events:
        n += env.repos.filings.put_overhang_events(events, loaded_by=env.job, now=env.now)
    if buybacks:
        n += env.repos.filings.put_buybacks(buybacks, loaded_by=env.job, now=env.now)
    return n, None


def _years_back(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 2월 29일
        return day.replace(year=day.year - years, day=28)


def _adjust(env: _Env, d: DisclosureRow) -> tuple[int, str | None]:
    html = _document(env, d.rcept_no)
    if d.kind == "overhang_exercise":
        p = overhang_doc.parse_exercise(html)
    else:
        p = overhang_doc.parse_price_adjust(html)
    inst = overhang_doc.adjust_instrument(d.title)
    if inst is None:
        raise ParseError("조정·청구 공시의 상품(CB·BW·EB)을 제목에서 정하지 못했다")
    known = env.repos.filings.overhang_events(d.corp_code)
    target = overhang_doc.link_target(known, inst, p.seq)
    why: str | None = None
    n = 0
    if target is None:
        # 연결 폴백 — 옛 결정 공시가 저장소에 없으면(이 피드가 생기기 전 발행) 구조화 API 로 찾아
        # 넣는다
        found, bad = structured.fetch_decisions(
            env.client, d.corp_code, inst, _years_back(d.rcept_dt, LINK_LOOKBACK_YEARS), d.rcept_dt
        )
        have = {e.rcept_no for e in known}
        fresh = [
            structured.supplement(e, {}, source="원문 없음") if structured.missing(e) else e
            for e in found
            if isinstance(e, OverhangEvent) and e.rcept_no not in have
        ]
        if fresh:
            n += env.repos.filings.put_overhang_events(fresh, loaded_by=env.job, now=env.now)
        target = overhang_doc.link_target([*known, *fresh], inst, p.seq)
        if target is None:
            why = (
                f"대상 결정 공시를 찾지 못했다({inst.value} 회차 {p.seq or '모름'} — 저장소·"
                f"구조화 {LINK_LOOKBACK_YEARS}년)"
            )
            if bad:
                why += f", 읽지 못한 결정 {len(bad)}건"
    adj = overhang_doc.to_adjust(
        p,
        rcept_no=d.rcept_no,
        corp_code=d.corp_code,
        rcept_dt=d.rcept_dt,
        target=target,
        why_unlinked=why,
    )
    n += env.repos.filings.put_overhang_adjusts([adj], loaded_by=env.job, now=env.now)
    return n, None


def _reason(env: _Env, rcept_no: str) -> str | None:
    """소유보고 원문의 변동 사유(못 읽으면 None — 사유는 선택 칸이라 그 건을 실패로 만들지
    않는다)."""
    try:
        html = largest(env.client.document_texts(rcept_no))
    except DartError as e:
        if _transient(e) or isinstance(e, DartQuotaExceeded) or e.critical:
            raise
        log.info("소유보고 원문을 못 받아 변동 사유 없음 rcept_no=%s: %s", rcept_no, _why(e)[:120])
        return None
    return ownership.parse_reason(html) if html else None


def _log_bad(what: str, corp: str, bad: Sequence[str]) -> None:
    """응답 중 읽지 못한 행(보고자 이름은 싣지 않는다 — 접수번호만). 그 행들은 저장하지 않는다."""
    if bad:
        log.warning(
            "%s 응답에서 읽지 못한 행 %d건 corp_code=%s rcept_no=%s",
            what, len(bad), corp, ",".join(bad[:5]),
        )  # fmt: skip


def _insider(env: _Env, d: DisclosureRow) -> tuple[int, str | None]:
    own = env.insider_cache.get(d.corp_code)
    if own is None:
        own = ownership.fetch_insider(env.client, d.corp_code)
        env.insider_cache[d.corp_code] = own
        _log_bad("소유보고", d.corp_code, own.bad)
    mine = [r for r in own.rows if r.rcept_no == d.rcept_no]
    if not mine:
        raise ParseError(
            "소유보고 응답에 이 접수번호가 없다(구조화 반영 지연 또는 해석 실패 [확인 필요 #32])"
        )
    have = {r.rcept_no for r in env.repos.filings.insider_reports(d.corp_code)}
    fresh = [r for r in own.rows if r.rcept_no not in have and r.rcept_no != d.rcept_no]
    reason = _reason(env, d.rcept_no)
    rows = [*fresh, *(replace(r, reason=reason) for r in mine)]
    return env.repos.filings.put_insider_reports(rows, loaded_by=env.job, now=env.now), None


def _major(env: _Env, d: DisclosureRow) -> tuple[int, str | None]:
    own = env.major_cache.get(d.corp_code)
    if own is None:
        own = ownership.fetch_major(env.client, d.corp_code)
        env.major_cache[d.corp_code] = own
        _log_bad("대량보유", d.corp_code, own.bad)
    mine = [r for r in own.rows if r.rcept_no == d.rcept_no]
    if not mine:
        raise ParseError(
            "대량보유 응답에 이 접수번호가 없다(구조화 반영 지연 또는 해석 실패 [확인 필요 #32])"
        )
    have = {r.rcept_no for r in env.repos.filings.major_holder_reports(d.corp_code)}
    rows = [r for r in own.rows if r.rcept_no not in have or r.rcept_no == d.rcept_no]
    return env.repos.filings.put_major_holder_reports(rows, loaded_by=env.job, now=env.now), None


_HANDLERS: Final = {
    "prelim": _prelim,
    "pre_announce": _pre_announce,
    "overhang": _decision,
    "buyback": _decision,
    "overhang_exercise": _adjust,
    "price_adjust": _adjust,
    "insider": _insider,
    "major_holder": _major,
}


def _transient(e: DartError) -> bool:
    """전송 오류·5xx·DART 점검(800)·정의되지 않은 오류(900) — 그 건 탓이 아니다(HTTP 4xx 는
    제외)."""
    http = e.status
    if http is not None and 400 <= http < 500:
        return False
    return e.code in ("", "800", "900") or (http is not None and http >= 500)


def _why(e: BaseException) -> str:
    return mask_text(f"{type(e).__name__}: {e}")[:300]


def _parse_one(env: _Env, d: DisclosureRow, report: FeedReport) -> None:
    datasets = datasets_for(d.kind, d.title)
    max_tries = env.cfg.filings.feed.retry_parse_max
    if not datasets or d.stock_code is None:
        env.repos.filings.mark_parsed(d.rcept_no, "none", note="파싱 대상 아님", now=env.now)
        return
    keys = [DataKey(SOURCE, ds, d.rcept_no) for ds in datasets]
    held: list[DataKey] = []
    busy = 0
    for k in keys:
        live = env.claims.live(k)
        if live is not None and live.status == "done":
            continue  # 앞 실행이 끝냈다(그 뒤 상태 기록 전에 멈췄던 경우) — 다시 잡지 않는다
        if _held_elsewhere(env, live):
            busy += 1  # 앞 분 실행이 아직 이 공시를 처리 중 — 잡으면 run_id 를 빼앗는다
            continue
        if env.claims.claim(k, env.job, env.run_id, env.now):
            held.append(k)
        else:
            busy += 1  # 다른 실행이 쥐고 있다 — 이번에는 건너뛴다(대기 상태 그대로)
    if busy:
        for k in held:
            env.claims.fail(k, env.run_id, "같은 공시의 다른 키를 다른 실행이 쥐고 있다", env.now)
        report.skipped += 1
        return
    if not held:
        env.repos.filings.mark_parsed(
            d.rcept_no, "done", note="선점 장부에 완료 기록 — 앞 실행이 썼다", now=env.now
        )
        report.skipped += 1
        return
    try:
        n, note = _HANDLERS[d.kind](env, d)
    except (DartQuotaExceeded, BudgetExhausted) as e:
        for k in held:
            env.claims.fail(k, env.run_id, _why(e), env.now)
        raise _Stop(
            f"DART 일 한도·예산 소진 — 그날 파싱 중단({type(e).__name__})", day_closed=True
        ) from None
    except DartError as e:
        for k in held:
            env.claims.fail(k, env.run_id, _why(e), env.now)
        if e.critical:
            raise
        if _transient(e):
            raise _Stop(f"DART 전송·점검 오류 — 이 실행의 파싱을 멈춘다({_why(e)[:120]})",
                        day_closed=False) from None  # fmt: skip
        _failed(env, d, _why(e), max_tries, report)
        return
    except (ParseError, ValueError, TypeError, KeyError) as e:
        for k in held:
            env.claims.fail(k, env.run_id, _why(e), env.now)
        _failed(env, d, _why(e), max_tries, report)
        return
    except Exception as e:
        # 예상 밖 오류(해석기 버그·그 행의 저장 거절 등)도 그 건만 실패 — 다음 건을 멈추지 않고
        # (격리 — 절대 규칙 4), 시도 수를 올려 3회면 na. 삼키지 않는다: traceback 로그 + parse_note.
        # DB 가 통째로 죽었으면 아래 상태 기록이 다시 실패해 그 실행이 실패로 올라간다.
        log.exception("공시 파싱 중 예상 밖 오류 rcept_no=%s kind=%s", d.rcept_no, d.kind)
        for k in held:
            env.claims.fail(k, env.run_id, _why(e), env.now)
        _failed(env, d, _why(e), max_tries, report)
        return
    for k in held:
        env.claims.complete(k, env.run_id, n, env.now)
    env.repos.filings.mark_parsed(d.rcept_no, "done", note=note, now=env.now)
    report.parsed += 1
    report.rows += n


def _failed(env: _Env, d: DisclosureRow, why: str, max_tries: int, report: FeedReport) -> None:
    tries = env.repos.filings.mark_parsed(d.rcept_no, "failed", note=why, now=env.now)
    if tries >= max_tries:
        env.repos.filings.mark_parsed(
            d.rcept_no, "na", note=f"{max_tries}회 실패 — {why}"[:300], now=env.now
        )
        report.na += 1
        log.warning("공시 파싱 %d회 실패 — na rcept_no=%s kind=%s", tries, d.rcept_no, d.kind)
    else:
        report.failed += 1
        log.info("공시 파싱 실패(%d/%d) rcept_no=%s kind=%s", tries, max_tries, d.rcept_no, d.kind)


def _parse_pending(env: _Env, report: FeedReport) -> None:
    feed = env.cfg.filings.feed
    pending = env.repos.filings.pending(
        kinds=PARSE_KINDS, max_tries=feed.retry_parse_max, limit=PARSE_LIMIT,
        now=env.now, wait_min=feed.retry_wait_min,
    )  # fmt: skip
    for d in pending:
        _parse_one(env, d, report)


# ── 처리기 ──────────────────────────────────────────────────────────────────────────────


def _run_day(ctx: JobContext) -> date:
    """실행 as_of(`YYYY-MM-DDTHH:MM` KST)의 날짜. 형식이 아니면 지금의 KST 날짜."""
    try:
        return date.fromisoformat(ctx.as_of[:10])
    except ValueError:
        return to_kst(ctx.now).date()


def collect(env: _Env, day: date) -> FeedReport:
    """다시 훑기 → 증분 → 파싱. 파싱 중 한도·전송 오류는 `report.stop` 으로 돌려준다."""
    report = FeedReport()
    _rescan(env, day, report)
    _incremental(env, day, report)
    try:
        _parse_pending(env, report)
    except _Stop as s:
        report.stop = s.reason
        report.day_closed = s.day_closed
        log.warning("공시 파싱 중단: %s", s.reason)
    return report


def run(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.collectors.dart_feed:run`)."""
    claims: ClaimStore = ctx.resource("claims")
    repos: PublicRepos | None = ctx.resources.get("public_repos")
    if repos is None:
        repos = pg_public_repos(ctx.resource("connect"))
    cfg: FinConfig = ctx.resources.get("fin_config") or load_fin(settings=ctx.settings)
    kr: TradingCalendar = ctx.resources.get("kr") or TradingCalendar.default()
    client: FeedClient | None = ctx.resources.get("dart")
    owned: DartClient | None = None
    if client is None:
        if ctx.settings is None:
            raise RuntimeError("설정이 주입되지 않았다(KBJ_DART_API_KEY)")
        owned = DartClient.from_settings(ctx.settings, ctx.resource("redis"))
        client = owned
    env = _Env(repos, claims, client, cfg, kr, ctx.now, ctx.run_id, job=ctx.job or JOB)
    try:
        report = collect(env, _run_day(ctx))
    finally:
        if owned is not None:
            owned.close()
    # run-once(수동)는 event 데이터셋 키도 '<dataset>@<as_of>' 로 미리 잡는다 — 받은 키를 그대로
    # 돌려줘야 실패로 남지 않는다(묶음 M 요청). 접수번호 키는 위에서 직접 complete/fail 했다.
    if report.stop is not None:
        return JobResult("failed", rows=report.new, detail=report.detail())
    return JobResult("ok", collected=ctx.keys, rows=report.new, detail=report.detail())
