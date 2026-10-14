"""정적 시험 — 공개 작업(공시·재무 수집·파생, P5 매크로·금투협 통계)이 쓰는 SQL 의 스키마는
`pub_` 뿐이다(docs/p4_design.md §2·§8.6, docs/p5_design.md §1.8-6 — 분류 사전 작업은 P5 제외).

import-linter 계약 ⑬ 은 공개 작업이 로그인 원천·엔진·저장소 **모듈**을 끌어오지 않게 막는다. 이
시험은 표 쪽 겹이다: 공개 작업 모듈과 그 모듈이 (정적으로, 간접 포함) 끌어오는 저장소
모듈(`kbj.store.*`)의 SQL 문자열이 가리키는 스키마가 전부 `pub_*` 이고, 로그인
등급(`prv_*`)·기본(`public.`) 스키마 이름이 문자열에 나오지 않는다. 문자열 검사기는 공개 내보내기
시험 (`tests/unit/public_export/test_sql_schemas.py`)의 것을 그대로 쓴다(두 벌 금지).

- 의존 그래프는 import-linter 가 쓰는 grimp 로 만든다(정적 — 실행 중 패키지 `__init__` 이 같이
  올라오는 것은 셈하지 않는다. `kbj.store.repos.public` 을 import 하면 파이썬은
  `kbj.store.repos/__init__` 도 실행하지만 그 모듈의 코드를 공개 작업이 쓰지는 않는다 — 계약 ⑬ 과
  같은 기준).
- 운영 기반 — 선점 장부(`kbj.services.scheduler.claims` — `ops.data_claim`)와 DB 접속·건강
  사건(`kbj.store.db` — `ops.health_events`) — 은 대상 밖이다(데이터 표 쓰기가 아니다).
  `kbj.store.*` 밖 모듈은 SQL 을 두지 않는다는 것도 함께 본다(SQL 은 저장소에만).
- dart-report 작업(`kbj.reports.dart_excel.job` — `pub_fin.report`)은 대상을 고르려고 관심종목
  (`prv_journal.watchlist`)을 **읽는다**(계약 ⑪ 이 허용, 산출물에는 남기지 않음 — 공개 내보내기
  시험이 관심 흔적 0 을 본다). 그래서 `kbj.store.repos.journal` 하나만 예외로 두고 나머지는 같은
  규칙.
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path

import grimp
import pytest

from tests.unit.public_export.test_sql_schemas import violations

ROOT = Path(__file__).resolve().parents[2]

# 계약 ⑬ 의 소스 모듈(공개 작업) — pyproject.toml 과 같은 목록
PUBLIC_JOBS = (
    "kbj.services.collectors.dart_feed",
    "kbj.services.collectors.dart_company",
    "kbj.services.collectors.fin",
    "kbj.services.engine.filings",
)
# P5 공개 작업(docs/p5_design.md D-P5-25 — 계약 ⑭ 의 작업 소스, WRAP): 공개 매크로·금투협 통계
P5_PUBLIC_JOBS = (
    "kbj.services.collectors.macro",
    "kbj.services.collectors.kofia",
)
# P7 공개 진단(docs/p7_design.md D-P7-15·20 — 계약 ⑱ 의 작업 소스, WRAP): pub_diag 만 쓰고
# 공개 표만 읽는다
P7_PUBLIC_JOBS = ("kbj.services.engine.diag_pub",)
REPORT_JOB = "kbj.reports.dart_excel.job"
REPORT_LOGIN_READS = frozenset({"kbj.store.repos.journal"})  # 관심종목 읽기(대상 고르기)만
# 운영 기반(데이터 표 쓰기가 아님): 선점 장부(ops.data_claim)·DB 접속과 건강 사건(ops.health_events)
INFRA = frozenset({"kbj.services.scheduler.claims", "kbj.store.db"})
# 표 이름 상수만 두는 행 모듈(SQL 이 아니다) — 쓰기 주인 시험(등록부)이 읽는 표 목록. 이 모듈의
# 위반은 그 상수 정의 안에만 있어야 한다(아래 _sql_violations — 다른 곳에 로그인 표 이름이
# 생기면 실패)
TABLE_NAME_CONSTANTS = {"kbj.core.p7_rows": ("DIAG_PRV_TABLES",)}


@functools.cache
def _graph() -> grimp.ImportGraph:
    return grimp.build_graph("kbj")


def _path(module: str) -> Path:
    base = ROOT.joinpath(*module.split("."))
    pkg = base / "__init__.py"
    return pkg if pkg.exists() else base.with_suffix(".py")


def _sql_violations(module: str) -> list[str]:
    """그 모듈 소스의 스키마 위반 — 표 이름 상수 모듈은 허용한 상수 정의를 빼고 본다."""
    text = _path(module).read_text(encoding="utf-8")
    names = TABLE_NAME_CONSTANTS.get(module, ())
    if names:
        lines = text.splitlines(keepends=True)
        found = set()
        for node in ast.parse(text).body:
            target = node.target if isinstance(node, ast.AnnAssign) else None
            if isinstance(target, ast.Name) and target.id in names:
                found.add(target.id)
                seg = "".join(lines[node.lineno - 1 : node.end_lineno])
                assert violations(seg), (module, target.id)  # 예외가 실제로 쓰인다
                for i in range(node.lineno - 1, node.end_lineno or node.lineno):
                    lines[i] = "\n"
        assert found == set(names), (module, names)
        text = "".join(lines)
    return violations(text)


def _checked(job: str) -> list[str]:
    """그 작업 모듈 + 정적으로 끌어오는 kbj.store.* 모듈(운영 기반 빼고)."""
    up = _graph().find_upstream_modules(job)
    return [job, *sorted(m for m in up if m.startswith("kbj.store.") and m not in INFRA)]


def test_contract_13_sources_match_pyproject() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    _, _, contract = text.partition('name = "⑬')
    for job in PUBLIC_JOBS:
        assert f'"{job}"' in contract.split("forbidden_modules", 1)[0], job


def test_contract_14_sources_match_pyproject() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    _, _, contract = text.partition('name = "⑭')
    for job in P5_PUBLIC_JOBS:
        assert f'"{job}"' in contract.split("forbidden_modules", 1)[0], job


def test_contract_18_sources_match_pyproject() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    _, _, contract = text.partition('name = "⑱')
    for job in P7_PUBLIC_JOBS:
        assert f'"{job}"' in contract.split("forbidden_modules", 1)[0], job


ALL_PUBLIC_JOBS = PUBLIC_JOBS + P5_PUBLIC_JOBS + P7_PUBLIC_JOBS


@pytest.mark.parametrize("job", ALL_PUBLIC_JOBS)
def test_public_job_sql_is_pub_only(job: str) -> None:
    mods = _checked(job)
    # 공개 저장소 묶음을 실제로 지나간다(빈 검사가 아님)
    assert {
        "kbj.store.repos.public", "kbj.store.repos.filings", "kbj.store.repos.macro",
        "kbj.store.repos.market_stats", "kbj.store.repos.diag",
    } & set(mods), mods  # fmt: skip
    assert not {
        "kbj.store.repos.consensus", "kbj.store.repos.journal", "kbj.store.repos.gex",
        "kbj.store.repos.diag_prv",
    } & set(mods), mods  # fmt: skip
    bad = {m: v for m in mods if (v := _sql_violations(m))}
    assert bad == {}


def test_diag_public_job_reaches_pub_diag_repo() -> None:
    """diag.public 은 pub_diag 저장소(diag·_diag)를 실제로 지나간다 — 위 SQL 검사가 그 모듈을
    본다."""
    assert {"kbj.store.repos.diag", "kbj.store.repos._diag"} <= set(_checked(P7_PUBLIC_JOBS[0]))


@pytest.mark.parametrize("job", ALL_PUBLIC_JOBS)
def test_public_job_reaches_no_other_sql_module(job: str) -> None:
    """kbj.store 밖에서 SQL 을 쓰는 모듈(선점 장부 빼고)을 끌어오지 않는다."""
    up = _graph().find_upstream_modules(job)
    sqlish = sorted(
        m
        for m in up
        if not m.startswith("kbj.store.")
        and m not in INFRA
        and _path(m).exists()
        and _sql_violations(m)
    )
    assert sqlish == []


def test_report_job_writes_pub_only_and_reads_watchlist_only() -> None:
    mods = _checked(REPORT_JOB)
    assert REPORT_LOGIN_READS <= set(mods)  # 예외가 실제로 쓰인다(죽은 예외가 아님)
    login = (
        {m for m in mods if m.startswith("kbj.store.repos.")}
        - REPORT_LOGIN_READS
        - {
            "kbj.store.repos.public",
            "kbj.store.repos.filings",
            "kbj.store.repos.fin",
            "kbj.store.repos._table",
            "kbj.store.repos._common",
            # P5 공개 저장소(PublicRepos — pub_macro·pub_market_stats, 아래 SQL 검사가
            # pub_ 만인지 본다). _macro 는 공개·로그인 공용 바탕(스키마는 하위 클래스 속성)
            "kbj.store.repos.macro",
            "kbj.store.repos._macro",
            "kbj.store.repos.market_stats",
            # P7 공개 진단 저장소(PublicRepos.diag — pub_diag, _diag 는 공개·로그인 공용 바탕)
            "kbj.store.repos.diag",
            "kbj.store.repos._diag",
        }
    )
    assert login == set()
    bad = {m: v for m in mods if m not in REPORT_LOGIN_READS and (v := _sql_violations(m))}
    assert bad == {}


def test_checker_is_not_vacuous() -> None:
    """심은 위반을 잡는다 — 로그인 저장소 모듈은 실제로 걸린다."""
    for login_repo in ("kbj.store.repos.consensus", "kbj.store.repos.journal"):
        assert violations(_path(login_repo).read_text(encoding="utf-8")), login_repo
    assert violations('Q = "INSERT INTO prv_fin.scenario VALUES (1)"\n')
    # 표 이름 상수 예외는 그 상수 밖의 로그인 표 이름을 놓치지 않는다
    assert violations('DIAG_PRV_TABLES: Final = ("prv_diag.x",)\nQ = "SELECT 1 FROM prv_diag.y"\n')
