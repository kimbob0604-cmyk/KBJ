"""커밋된 fixture·골든에 토큰·키처럼 보이는 문자열이 없어야 한다 (CLAUDE.md 비밀정보 규칙).

- tests/fixtures 의 모든 파일과 tests/golden 의 데이터 파일(시험 코드 .py 는 뺀다 — 거부되는지
  보려고 비밀 같은 문자열을 일부러 쓴다). 골든 녹화는 raw_messages 원문을 잘라 오므로 여기서 한 번
  더 막는다
- 녹화 골든을 자르는 도구(scripts/make_golden.py `SECRET_PATTERNS`)는 이 목록을 모두 담는다 —
  tests/golden/test_raw_golden.py 가 확인한다
"""

import re
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parents[1]
FIXTURES = TESTS / "fixtures"
GOLDEN = TESTS / "golden"

# JWT(eyJ…), Authorization 헤더, KIS 인증 필드 이름
PATTERNS = [
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}"),
    re.compile(r"Bearer\s", re.IGNORECASE),
    re.compile(r"authorization", re.IGNORECASE),
    re.compile(r"app_?key", re.IGNORECASE),
    re.compile(r"app_?secret", re.IGNORECASE),
    re.compile(r"access_token", re.IGNORECASE),
    re.compile(r"approval_key", re.IGNORECASE),
]


def fixture_files() -> list[Path]:
    return sorted(p for p in FIXTURES.rglob("*") if p.is_file())


def golden_files() -> list[Path]:
    """골든 데이터 파일 — 시험 코드(.py)와 캐시는 뺀다."""
    return sorted(
        p
        for p in GOLDEN.rglob("*")
        if p.is_file() and p.suffix not in (".py", ".pyc") and "__pycache__" not in p.parts
    )


def test_fixtures_exist() -> None:
    assert fixture_files(), "tests/fixtures 가 비었다"


def test_golden_data_exists() -> None:
    names = {p.relative_to(GOLDEN).as_posix() for p in golden_files()}
    assert "core/chain_synthetic_20260928_small.json" in names
    assert {"raw/synthetic_20260928.jsonl", "raw/synthetic_20260928.parsed.json"} <= names


@pytest.mark.parametrize(
    "path", [*fixture_files(), *golden_files()], ids=lambda p: str(p.relative_to(TESTS))
)
def test_fixture_has_no_secret_like_text(path: Path) -> None:
    text = path.read_bytes().decode("utf-8", errors="replace")
    hits = [p.pattern for p in PATTERNS if p.search(text)]
    assert not hits, f"{path.name}: {hits}"


@pytest.mark.parametrize(
    "sample",
    [
        '{"access_token": "x"}',
        "Authorization: Bearer abc",
        '{"authorization": "x"}',
        '{"appkey": "PS..."}',
        '{"appsecret": "..."}',
        '{"approval_key": "..."}',
        "eyJhbGciOiJIUzI1NiJ9.payload",
    ],
)
def test_patterns_catch_samples(sample: str) -> None:
    assert any(p.search(sample) for p in PATTERNS)
