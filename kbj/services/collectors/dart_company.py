"""`filings.company_profile` — 상장사 기업개황(결산월·업종·법인구분·설립일)을 주 1회 받는다(공개
작업).

승격 원본: ET `run.py:_dart_ksic`:837(업종만), P2 `disclosures.company`. docs/p4_design.md
§1.3·§3.2, `pub_filings.corp_profile`(0010).

- 대상: P2 `pub_filings.corp_code`(filings.corp_code 가 매일 갈아 넣는 표)의 상장사(종목코드 있음).
  약 2,700 호출(토 04:00 — §3.5). 데이터 키 `DART:company @ run_date` 는 실행기가 먼저 잡는다.
- 100개씩 끊어 저장한다(중간에 멈춰도 받은 것은 남는다).
- 한 회사 실패(형식·해석·DART 오류 코드)는 그 회사만 건너뛰고 세어 둔다(절대 규칙 4 — 결과 `detail`
  에 수·앞 몇 개 corp_code). 실패가 대상의 10% 를 넘으면 그 실행을 실패로(체계적 문제 — 재시도).
- `020`·예산 소진·전송 오류(5xx·점검)는 그 실행을 멈추고 실패(재시도 규칙대로). 키·IP 오류는 그대로
  올린다(실행기가 실패 + 알림).

자원: `public_repos`(없으면 `connect` 로 `pg_public_repos`), `dart`(없으면
`DartClient.from_settings`). 계약 ⑬ — 로그인 모듈·`kbj.store.repos` 패키지 이름을 import 하지
않는다(공개 작업).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Final

from kbj.core.fin_rows import CorpProfile
from kbj.core.masking import mask_text
from kbj.data.budget import BudgetExhausted
from kbj.data.public.dart.client import DartClient, DartError, DartQuotaExceeded
from kbj.data.public.dart.company import fetch_profile
from kbj.data.public.dart.disclosures import DartJson
from kbj.data.public.dart.doctext import ParseError
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos.public import PublicRepos, pg_public_repos

__all__ = ["BATCH", "FAIL_RATIO", "JOB", "collect", "run"]

JOB: Final = "filings.company_profile"
BATCH: Final = 100
FAIL_RATIO: Final = 0.10

log = logging.getLogger(__name__)


def _transient(e: DartError) -> bool:
    http = e.status
    if http is not None and 400 <= http < 500:
        return False
    return e.code in ("", "800", "900") or (http is not None and http >= 500)


def collect(
    client: DartJson,
    repos: PublicRepos,
    corp_codes: Sequence[str],
    *,
    now: datetime,
    job: str = JOB,
) -> tuple[int, list[str], str | None]:
    """회사마다 기업개황 → 100개씩 저장. 반환 (저장 행 수, 실패 corp_code, 멈춘 사유 | None)."""
    batch: list[CorpProfile] = []
    stored = 0
    failed: list[str] = []
    stop: str | None = None
    for corp in corp_codes:
        try:
            got = fetch_profile(client, corp)
        except (DartQuotaExceeded, BudgetExhausted) as e:
            stop = f"DART 일 한도·예산 소진 — 중단({type(e).__name__})"
            break
        except DartError as e:
            if e.critical:
                raise
            if _transient(e):
                stop = mask_text(f"DART 전송·점검 오류 — 중단({e})")[:200]
                break
            failed.append(corp)
            log.warning("기업개황 실패 corp_code=%s: %s", corp, mask_text(str(e))[:120])
            continue
        except (ParseError, ValueError, TypeError) as e:
            failed.append(corp)
            log.warning("기업개황 해석 실패 corp_code=%s: %s", corp, type(e).__name__)
            continue
        if got is None:
            failed.append(corp)  # 013 — 고유번호 표에 있는데 개황이 없다
            continue
        batch.append(got)
        if len(batch) >= BATCH:
            stored += repos.filings.upsert_profiles(batch, loaded_by=job, now=now)
            batch = []
    if batch:
        stored += repos.filings.upsert_profiles(batch, loaded_by=job, now=now)
    return stored, failed, stop


def run(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.collectors.dart_company:run`)."""
    if not ctx.keys:
        return JobResult("skipped", detail={"reason": "선점한 키가 없다"})
    repos: PublicRepos | None = ctx.resources.get("public_repos")
    if repos is None:
        repos = pg_public_repos(ctx.resource("connect"))
    corps = sorted({corp for corp, _name in repos.filings.corp_index().values()})
    if not corps:
        return JobResult(
            "skipped", detail={"reason": "상장사 고유번호가 없다(filings.corp_code 전)"}
        )
    client: DartJson | None = ctx.resources.get("dart")
    owned: DartClient | None = None
    if client is None:
        if ctx.settings is None:
            raise RuntimeError("설정이 주입되지 않았다(KBJ_DART_API_KEY)")
        owned = DartClient.from_settings(ctx.settings, ctx.resource("redis"))
        client = owned
    try:
        stored, failed, stop = collect(client, repos, corps, now=ctx.now, job=ctx.job or JOB)
    finally:
        if owned is not None:
            owned.close()
    detail: dict[str, object] = {"targets": len(corps), "stored": stored, "failed": len(failed)}
    if failed:
        detail["failed_corps"] = failed[:5]
    if stop is not None:
        detail["reason"] = stop
        return JobResult("failed", rows=stored, detail=detail)
    if len(failed) > len(corps) * FAIL_RATIO:
        detail["reason"] = f"실패 {len(failed)}/{len(corps)} — {FAIL_RATIO:.0%} 초과"
        return JobResult("failed", rows=stored, detail=detail)
    return JobResult("ok", collected=ctx.keys, rows=stored, detail=detail)
