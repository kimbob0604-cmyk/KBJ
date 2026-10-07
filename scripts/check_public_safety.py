"""공개 레포 안전 검사 — git 이 추적할 파일에 비밀·개인정보·대용량·DB 파일이 없는지 본다.

KBJ 는 공개 레포다(CLAUDE.md §2). 커밋 전에, 그리고 CI 에서 이 검사가 통과해야 한다.

대상: ``git ls-files --cached --others --exclude-standard`` — 이미 추적 중이거나, 추적되지 않았지만
.gitignore 에 걸리지 않아 다음 ``git add`` 로 들어갈 파일 전부.

규칙(이름은 허용 목록에 쓴다)
- 내용: kbj.core.masking.SECRET_SHAPES 의 비밀값 형태(텔레그램 봇 토큰·sk-ant·GitHub 토큰·
  AWS 키·JWT·PEM 개인키·KIS 앱키/앱시크릿 형태·계좌번호 8-2),
  ``user_home_path``(/Users/<이름>/ 같은 개인 홈 경로), ``render_host``(Render 서비스 호스트명),
  ``env_secret_value``(.env* 파일에 비밀 이름의 값이 채워짐)
- 파일: ``file_size``(5MB 초과), ``data_extension``(.db·.sqlite·.sqlite3·.duckdb·.parquet·.xlsx),
  ``secret_filename``(.env·*.key·*.pem·토큰 캐시 이름)

오탐은 scripts/public_safety_allow.txt 에 경로 단위로, 규칙과 사유를 적어 허용한다.

사용:
    uv run python scripts/check_public_safety.py                    # 레포 전체
    uv run python scripts/check_public_safety.py --exclude legacy   # legacy/ 제외

종료 코드 0 = 통과, 1 = 발견, 2 = 사용법·허용 목록 오류. 출력은 ``경로:줄: 규칙 — 설명`` 뿐이고
찾은 값은 출력하지 않는다(절대 규칙 5).
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from kbj.core.masking import SECRET_SHAPES

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ALLOW = REPO_ROOT / "scripts" / "public_safety_allow.txt"
MAX_BYTES = 5 * 1024 * 1024
_BINARY_PROBE = 8192

DATA_EXTENSIONS = frozenset({".db", ".sqlite", ".sqlite3", ".duckdb", ".parquet", ".xlsx"})
_SECRET_FILE_GLOBS = ("*.key", "*.pem", "*.token.json", "*_token.json", "kis_token.json")

# 내용 규칙(줄 단위). 비밀값 형태는 masking 과 한 벌(SECRET_SHAPES)이다
_PATH_SHAPES: dict[str, re.Pattern[str]] = {
    # 맥 /Users/<이름>/ 와 윈도우 C:\Users\<이름>\ — 대괄호·꺾쇠 자리표시자는 잡지 않는다
    "user_home_path": re.compile(
        r"/Users/[A-Za-z0-9._-]+/|\b[A-Za-z]:\\+Users\\+[A-Za-z0-9._-]+\\"
    ),
    "render_host": re.compile(r"\b[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.onrender\.com\b"),
}
_ENV_LINE = re.compile(
    r"^\s*(?:export\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*?)\s*$"
)
_ENV_SECRET_NAME = re.compile(r"(?i)(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD|_PW|ACCOUNT|_IDS?|_URL)$")

RULES: dict[str, str] = {
    "telegram_bot_token": "텔레그램 봇 토큰 형태",
    "anthropic_key": "Anthropic API 키 형태(sk-ant-)",
    "github_token": "GitHub 토큰 형태",
    "aws_access_key_id": "AWS 액세스 키 ID 형태",
    "aws_secret_access_key": "AWS 시크릿 키 형태",
    "jwt": "JWT 형태(접근토큰)",
    "private_key_pem": "PEM 개인키",
    "kis_app_key": "KIS 앱키 형태(PS + 34자)",
    "kis_app_secret": "KIS 앱시크릿 형태(base64 180자)",
    "account_number": "계좌번호 형태(8자리-2자리)",
    "user_home_path": "사용자 이름이 든 로컬 홈 경로",
    "render_host": "Render 서비스 호스트명",
    "env_secret_value": ".env 계열 파일에 비밀 이름의 값이 채워져 있다",
    "file_size": "5MB 초과 파일",
    "data_extension": "DB·데이터 파일 확장자",
    "secret_filename": "비밀·토큰 캐시 파일 이름",
}


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int  # 파일 단위 규칙은 0
    rule: str

    def render(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: {self.rule} — {RULES[self.rule]}"


@dataclass(frozen=True)
class AllowEntry:
    pattern: str  # 레포 루트 기준 posix 경로 또는 fnmatch 패턴
    rules: frozenset[str]  # {"*"} 이면 모든 규칙
    reason: str
    lineno: int

    def covers(self, finding: Finding) -> bool:
        rule_ok = "*" in self.rules or finding.rule in self.rules
        return rule_ok and fnmatch.fnmatchcase(finding.path, self.pattern)


class AllowListError(ValueError):
    """허용 목록 형식 오류 — 사유가 없거나 규칙 이름이 틀렸다."""


def parse_allow(text: str) -> list[AllowEntry]:
    """``경로 | 규칙[,규칙…] | 사유`` 한 줄에 하나. ``#`` 으로 시작하는 줄과 빈 줄은 건너뛴다."""
    entries: list[AllowEntry] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|", 2)]
        if len(parts) != 3:
            raise AllowListError(f"{lineno}줄: '경로 | 규칙 | 사유' 세 칸이어야 한다")
        pattern, rules_text, reason = parts
        if not pattern or pattern.startswith("/") or "\\" in pattern:
            raise AllowListError(f"{lineno}줄: 경로는 레포 루트 기준 상대 posix 경로")
        if len(reason) < 4:
            raise AllowListError(f"{lineno}줄: 사유가 없다(4자 이상)")
        rules = frozenset(r.strip() for r in rules_text.split(",") if r.strip())
        unknown = rules - set(RULES) - {"*"}
        if not rules or unknown:
            raise AllowListError(f"{lineno}줄: 모르는 규칙 {sorted(unknown) or '(없음)'}")
        entries.append(AllowEntry(pattern, rules, reason, lineno))
    return entries


def load_allow(path: Path) -> list[AllowEntry]:
    return parse_allow(path.read_text(encoding="utf-8")) if path.exists() else []


def tracked_files(root: Path) -> list[str]:
    """git 이 추적하거나 다음 add 로 추적할 파일(레포 루트 기준 posix 경로, 정렬)."""
    out = subprocess.run(  # noqa: S603 — 고정 인자, 셸 없음
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],  # noqa: S607
        check=True,
        capture_output=True,
    ).stdout
    return sorted({p for p in out.decode("utf-8").split("\0") if p})


def _excluded(rel: str, exclude: Sequence[str]) -> bool:
    for prefix in exclude:
        p = prefix.strip("/")
        if rel == p or rel.startswith(p + "/"):
            return True
    return False


def _file_rules(rel: str, size: int) -> list[Finding]:
    name = PurePosixPath(rel).name
    found: list[Finding] = []
    if size > MAX_BYTES:
        found.append(Finding(rel, 0, "file_size"))
    if PurePosixPath(name).suffix.lower() in DATA_EXTENSIONS:
        found.append(Finding(rel, 0, "data_extension"))
    is_env = name == ".env" or (name.startswith(".env.") and name != ".env.example")
    if is_env or any(fnmatch.fnmatchcase(name, g) for g in _SECRET_FILE_GLOBS):
        found.append(Finding(rel, 0, "secret_filename"))
    return found


def scan_text(rel: str, text: str) -> list[Finding]:
    """내용 규칙. 같은 줄·같은 규칙은 한 번만."""
    name = PurePosixPath(rel).name
    is_env_file = name.startswith(".env")
    found: set[Finding] = set()
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in (*SECRET_SHAPES.items(), *_PATH_SHAPES.items()):
            if pattern.search(line):
                found.add(Finding(rel, lineno, rule))
        if is_env_file and not line.lstrip().startswith("#"):
            m = _ENV_LINE.match(line)
            if m and m["value"].strip("\"'") and _ENV_SECRET_NAME.search(m["name"]):
                found.add(Finding(rel, lineno, "env_secret_value"))
    return sorted(found)


def scan_file(root: Path, rel: str) -> list[Finding]:
    path = root / rel
    if path.is_symlink() or not path.is_file():
        return []  # 지워졌지만 아직 색인에 있는 파일·링크는 내용이 없다
    size = path.stat().st_size
    found = _file_rules(rel, size)
    if size > MAX_BYTES:
        return found
    data = path.read_bytes()
    if b"\0" in data[:_BINARY_PROBE]:
        return found  # 바이너리 — 파일 규칙만
    return found + scan_text(rel, data.decode("utf-8", errors="replace"))


@dataclass(frozen=True)
class Report:
    findings: list[Finding]  # 허용 목록을 뺀 나머지
    allowed: int
    files: int
    unused_allow: list[AllowEntry]


def scan(root: Path, *, allow: Iterable[AllowEntry] = (), exclude: Sequence[str] = ()) -> Report:
    entries = list(allow)
    used: set[int] = set()
    findings: list[Finding] = []
    allowed = 0
    files = [rel for rel in tracked_files(root) if not _excluded(rel, exclude)]
    for rel in files:
        for f in scan_file(root, rel):
            hit = next((e for e in entries if e.covers(f)), None)
            if hit is None:
                findings.append(f)
            else:
                allowed += 1
                used.add(hit.lineno)
    unused = [
        e
        for e in entries
        if e.lineno not in used and not _excluded(e.pattern.split("*", 1)[0], exclude)
    ]
    return Report(sorted(findings), allowed, len(files), unused)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="공개 레포 안전 검사(KBJ)")
    ap.add_argument(
        "--root", type=Path, default=REPO_ROOT, help="레포 루트(기본: 이 스크립트의 레포)"
    )
    ap.add_argument("--allow", type=Path, default=DEFAULT_ALLOW, help="허용 목록 파일")
    ap.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="경로",
        help="이 경로 아래는 건너뛴다(여러 번 줄 수 있다). 예: --exclude legacy",
    )
    args = ap.parse_args(argv)
    try:
        entries = load_allow(args.allow)
    except AllowListError as e:
        print(f"허용 목록 오류({args.allow.name}): {e}", file=sys.stderr)
        return 2
    try:
        report = scan(args.root, allow=entries, exclude=args.exclude)
    except subprocess.CalledProcessError:
        print("git ls-files 실패 — git 레포 안에서 돌린다", file=sys.stderr)
        return 2
    for e in report.unused_allow:
        print(f"경고: 허용 목록 {e.lineno}줄은 걸리는 것이 없다: {e.pattern}", file=sys.stderr)
    for f in report.findings:
        print(f.render())
    scope = f" (제외: {', '.join(args.exclude)})" if args.exclude else ""
    verdict = "실패" if report.findings else "통과"
    print(
        f"공개 안전 검사 {verdict}: 파일 {report.files}개{scope}, 발견 {len(report.findings)}건, "
        f"허용 {report.allowed}건"
    )
    return 1 if report.findings else 0


if __name__ == "__main__":
    sys.exit(main())
