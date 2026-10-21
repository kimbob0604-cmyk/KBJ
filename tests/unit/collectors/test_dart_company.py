"""`filings.company_profile` 처리기 — 가짜 DART(합성 세계) + 메모리 저장소(묶음 F, §1.3·§3.2).

상장사(고유번호 표에 종목코드가 있는 회사)마다 기업개황 1호출, 100개씩 저장, 한 회사 실패는 그
회사만(10% 넘으면 실행 실패), 020·전송 오류는 멈추고 실패, 키 오류는 그대로 올린다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kbj.data.public.dart.client import DartClient, DartError
from kbj.data.spec import DataKey
from kbj.services.collectors import dart_company as C
from kbj.services.scheduler.handlers import JobContext
from kbj.store.repos.public import MemoryPublicRepos, memory_public_repos
from tests.fakes.dart_server import KEY, FakeDart
from tests.fixtures.synthetic.p4_world import build_world
from tests.unit.data.public._support import RecordingLimiter

T0 = datetime(2026, 11, 13, 19, 0, tzinfo=UTC)  # 토 04:00 KST
RUN_KEY = DataKey("DART", "company", "2026-11-14")


def setup(*, key: str = KEY) -> tuple[FakeDart, DartClient, MemoryPublicRepos]:
    w = build_world()
    fake = FakeDart(lambda: T0, world=w)
    client = DartClient(key, transport=fake.transport("company"), limiter=RecordingLimiter(),
                        retries=1, backoff_s=0)  # fmt: skip
    repos = memory_public_repos()
    for c in w.corps:
        if c.stock_code:
            repos.filings.corp_codes[c.stock_code] = (c.corp_code, c.corp_name)
    return fake, client, repos


def ctx(
    client: DartClient, repos: MemoryPublicRepos, keys: tuple[DataKey, ...] = (RUN_KEY,)
) -> JobContext:
    return JobContext(
        job=C.JOB, as_of="2026-11-14", run_id=f"{C.JOB}:2026-11-14:1", attempt=1, now=T0,
        keys=keys, resources={"public_repos": repos, "dart": client},
    )  # fmt: skip


def test_every_listed_company_profile_is_stored() -> None:
    fake, client, repos = setup()
    r = C.run(ctx(client, repos))
    assert r.status == "ok" and r.collected == (RUN_KEY,) and r.rows == 19
    assert r.detail == {"targets": 19, "fresh": 0, "stored": 19, "failed": 0}
    w = build_world()
    assert repos.filings.profiles() == {p.corp_code: p for p in w.profiles}
    assert len(fake.calls("company.json")) == 19  # 비상장 잡음 회사는 부르지 않는다


def test_quota_stops_and_fails_with_a_reason_but_keeps_what_was_stored() -> None:
    fake, client, repos = setup()
    fake.inject("020", 1, endpoint="company.json")
    r = C.run(ctx(client, repos))
    assert r.status == "failed" and "한도" in str(r.detail["reason"]) and r.collected == ()
    assert len(fake.calls("company.json")) == 1


def test_a_few_failures_are_counted_but_many_fail_the_run() -> None:
    fake, client, repos = setup()
    fake.inject("100", 1, endpoint="company.json")  # 한 회사 — 필드 값 오류
    r = C.run(ctx(client, repos))
    assert r.status == "ok" and r.detail["failed"] == 1 and r.rows == 18
    assert len(r.detail["failed_corps"]) == 1  # type: ignore[arg-type]
    fake2, client2, repos2 = setup()
    fake2.inject("100", 3, endpoint="company.json")  # 3/19 > 10%
    r2 = C.run(ctx(client2, repos2))
    assert r2.status == "failed" and "초과" in str(r2.detail["reason"])


def test_transient_error_stops() -> None:
    fake, client, repos = setup()
    fake.inject("800", 1, endpoint="company.json")
    r = C.run(ctx(client, repos))
    assert r.status == "failed" and "전송·점검" in str(r.detail["reason"])


def test_key_error_is_raised() -> None:
    _fake, client, repos = setup(key="x" * 40)  # 가짜 서버가 010(등록되지 않은 키)
    with pytest.raises(DartError) as e:
        C.run(ctx(client, repos))
    assert e.value.critical


def test_no_keys_or_no_listed_companies_is_skipped() -> None:
    _fake, client, repos = setup()
    assert C.run(ctx(client, repos, keys=())).status == "skipped"
    empty = memory_public_repos()
    r = C.run(ctx(client, empty))
    assert r.status == "skipped" and "고유번호" in str(r.detail["reason"])


def test_recent_profiles_are_not_fetched_again() -> None:
    """같은 주 안의 재시도·수동 fill-dart 는 이미 받은 회사를 다시 부르지 않는다(2026-10-21 —
    전체 2,700건을 처음부터 다시 불러 DART 일 예산을 다 썼다). 멈춘 자리부터 이어 받는다."""
    fake, client, repos = setup()
    fake.inject("020", 1, endpoint="company.json")
    first = C.run(ctx(client, repos))
    assert first.status == "failed"
    fake2 = FakeDart(lambda: T0, world=build_world())
    client2 = DartClient(KEY, transport=fake2.transport("company"), limiter=RecordingLimiter(),
                         retries=1, backoff_s=0)  # fmt: skip
    ok = C.run(ctx(client2, repos))
    assert ok.status == "ok" and ok.detail["fresh"] == 0 and len(fake2.calls("company.json")) == 19
    fake3 = FakeDart(lambda: T0, world=build_world())
    client3 = DartClient(KEY, transport=fake3.transport("company"), limiter=RecordingLimiter(),
                         retries=1, backoff_s=0)  # fmt: skip
    again = C.run(ctx(client3, repos))
    assert again.status == "ok" and again.detail["fresh"] == 19
    assert fake3.calls("company.json") == []
