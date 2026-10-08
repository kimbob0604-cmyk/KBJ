"""직접 호출 금지 검사 — 정본 밖에서 외부 API 를 직접 부르는 코드를 찾는다.

KBJ 는 같은 일을 하는 코드를 두 벌 두지 않는다(CLAUDE.md §3, docs/PLAN.md §2).
KIS 토큰 발급은 auth 한 곳, KIS·KRX·DART 호출은 kbj 어댑터, 텔레그램은 notifier 하나다.
이 검사는 그 밖에서 호스트 주소·발급 경로를 문자열로 갖는 코드와, legacy 가 kbj 의
정본 경계를 우회하는 import 를 찾는다(docs/p2_design.md §9).

대상: ``git ls-files --cached --others --exclude-standard`` 중
.py .js .mjs .sh .yml .yaml .toml .cfg .ini .html.
제외: 문서(*.md·docs/), 시험(tests/·test_*.py·*_test.py·tests_smoke.py·fakes/·fixtures/
— 가짜 오류 문구에 호스트가 들어간다), 이 스크립트 자신(패턴은 조각을 이어 만든다).

규칙
- 호스트 그룹(``--list`` 로 본다). 목표 0 그룹(kis_oauth·kis_rest·kis_master·krx_api·dart)은
  허용 위치 밖 어디서든 한 줄이라도 있으면 실패한다. 기준선 그룹(telegram·ecos·datago·
  kosis·kis_ws·naver·etf_issuers·krx_scrape)은 ``kbj/`` 에서는 허용 위치 밖이면 실패, 그
  밖(legacy 등)은 ``scripts/canonical_baseline.txt`` 와 견준다.
- 기준선은 줄어들기만 한다: 기준선에 없는 파일의 위반, 건수 증가, 건수가 줄었는데
  기준선을 안 줄임, 목표 0 그룹·kbj 경로가 기준선에 있음 — 모두 실패.
  ``--update-baseline`` 은 줄이는 방향으로만 다시 쓴다.
- AST 규칙(legacy 의 .py, 시험 제외): kbj.services.auth(발급자) import 금지, kbj 의 ``_`` 로
  시작하는 이름 import·속성 접근(``getattr`` 문자열 포함) 금지(비공개 주소 상수로 문자열 검사를
  피하는 것을 막는다), kbj 모듈은 허용 목록(§9.5)만. 파싱하지 못한 파일은 실패로 낸다.

사용:
    uv run python scripts/check_canonical.py                       # 전체
    uv run python scripts/check_canonical.py --groups kis_oauth,dart
    uv run python scripts/check_canonical.py --list                # 그룹·허용 위치
    uv run python scripts/check_canonical.py --update-baseline     # 줄어든 건수로 기준선 고정

종료 코드 0 = 통과, 1 = 위반, 2 = 사용법·기준선 형식 오류. 출력은
``경로:줄: 그룹 — 설명`` 뿐이고 찾은 문자열은 출력하지 않는다(주소에 키가 섞일 수 있다 —
절대 규칙 5).
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = REPO_ROOT / "scripts" / "canonical_baseline.txt"
SELF = "scripts/check_canonical.py"

SCAN_SUFFIXES = frozenset(
    {".py", ".js", ".mjs", ".sh", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".html"}
)
# fnmatch 의 `*` 는 `/` 도 넘는다 — 접두사 없는 형태와 `*/` 형태를 둘 다 둔다
EXCLUDE_GLOBS = (
    "*.md",
    "docs/*",
    "tests/*",
    "*/tests/*",
    "test_*.py",
    "*/test_*.py",
    "*_test.py",
    "tests_smoke.py",
    "*/tests_smoke.py",
    "fakes/*",
    "*/fakes/*",
    "fixtures/*",
    "*/fixtures/*",
)
_BINARY_PROBE = 8192


def _dots(*parts: str) -> str:
    """호스트 조각을 정규식 ``\\.`` 로 잇는다 — 이 파일에 호스트 문자열이 통째로 들어가지 않게."""
    return r"\.".join(parts)


@dataclass(frozen=True)
class Group:
    name: str
    pattern: re.Pattern[str]
    allowed: tuple[str, ...]  # kbj 안 허용 위치(fnmatch). 비어 있으면 kbj 어디에도 금지
    zero: bool  # True = 목표 0(기준선 없음), False = 줄어들기만 하는 기준선
    desc: str
    stage: str  # 정리 단계(--list·표)


GROUPS: dict[str, Group] = {
    g.name: g
    for g in (
        Group(
            "kis_oauth",
            re.compile("oauth2/(?:token" + "P|Appro" + "val)"),
            ("kbj/services/auth/*",),
            True,
            "KIS 접근토큰·접속키 발급 경로 — 발급은 kbj.services.auth 만(ADR 0004)",
            "P2",
        ),
        Group(
            "kis_rest",
            re.compile(_dots("openapi(?:vts)?", "koreainvestment", "com")),
            ("kbj/data/private/kis/*",),
            True,
            "KIS REST 호스트 — kbj.data.private.kis(브리지 kis:)로",
            "P2",
        ),
        Group(
            "kis_master",
            re.compile(_dots("download", "dws", "co", "kr")),
            ("kbj/data/private/kis/*",),
            True,
            "KIS 마스터 배포 호스트 — kbj.data.private.kis.master(브리지 kis-master:)로",
            "P2",
        ),
        Group(
            "krx_api",
            re.compile(_dots("data-dbg", "krx", "co", "kr")),
            ("kbj/data/private/krx/*",),
            True,
            "KRX OPEN API 호스트 — kbj.data.private.krx(브리지 krx:)로",
            "P2",
        ),
        Group(
            "dart",
            re.compile("https?://" + _dots("opendart", "fss", "or", "kr")),
            ("kbj/data/public/dart/*",),
            True,
            "DART OpenAPI 주소 — kbj.data.public.dart(브리지 dart:)로",
            "P2",
        ),
        Group(
            "telegram",
            re.compile(_dots("api", "telegram", "org")),
            ("kbj/services/notifier/telegram_api.py",),
            False,
            "텔레그램 Bot API 호스트 — notifier(kbj.services.notifier.client)로",
            "P2~P5",
        ),
        Group(
            "ecos",
            re.compile(_dots("ecos", "bok", "or", "kr")),
            ("kbj/data/public/ecos/*",),
            False,
            "ECOS 호스트 — kbj.data.public.ecos 로",
            "P5",
        ),
        Group(
            "datago",
            re.compile(_dots("apis", "data", "go", "kr")),
            (
                "kbj/data/datago.py",
                "kbj/data/public/customs/*",
                "kbj/data/public/fsc_kofia_stats/*",
                "kbj/data/private/fsc_*/*",
            ),
            False,
            "공공데이터포털 호스트 — kbj.data.datago 로",
            "P3~P6",
        ),
        Group(
            "kosis",
            re.compile(_dots("kosis", "kr") + "/openapi"),
            ("kbj/data/public/kosis/*",),
            False,
            "KOSIS OpenAPI 주소 — kbj.data.public.kosis 로",
            "P5",
        ),
        Group(
            "kis_ws",
            re.compile(r"\b" + _dots("ops", "koreainvestment", "com")),
            ("kbj/services/ws_gateway/*",),
            False,
            "KIS 웹소켓 호스트 — ws-gateway 승격(P7)까지 legacy GX(메인 결정 D2)",
            "P7",
        ),
        Group(
            "naver",
            re.compile(
                "(?:finance|m"
                + r"\.stock|api"
                + r"\.stock|polling"
                + r"\.finance|fchart"
                + r"\.stock|openapi|search)"
                + r"\."
                + _dots("naver", "com")
                + "|"
                + _dots("navercomp", "wisereport", "co", "kr")
            ),
            (),
            False,
            "네이버 금융 호출 — 어댑터를 만들지 않는다(U4), 기능별로 KRX·KIS 로 교체",
            "P3~P5",
        ),
        Group(
            "etf_issuers",
            re.compile(
                r"\b(?:"
                + "|".join(
                    (
                        _dots("samsungfund", "com"),
                        _dots("investments", "miraeasset", "com"),
                        _dots("timeetf", "co", "kr"),
                        _dots("soletf", "com"),
                        _dots("aceetf", "co", "kr"),
                        _dots("hanaroetf", "com"),
                        _dots("samsungactive", "co", "kr"),
                        _dots("plusetf", "co", "kr"),
                        _dots("riseetf", "co", "kr"),
                    )
                )
                + ")"
            ),
            ("kbj/data/private/etf_issuers/*",),
            False,
            "ETF 운용사 9곳 호스트(구성종목 PDF) — kbj.data.private.etf_issuers 로(P3 묶음 E3·S)",
            "P3~P5",
        ),
        Group(
            "krx_scrape",
            re.compile(r"(?<![-\w])" + _dots("data", "krx", "co", "kr") + r"|\bpy" + r"krx\b"),
            (),
            False,
            "KRX 웹 스크랩·스크랩 라이브러리 — KRX OPEN API(krx.daily)로",
            "P3",
        ),
    )
}

# ── AST 규칙(legacy/**/*.py) — docs/p2_design.md §9.5 ──────────────────────────────────────────
AST_RULES: dict[str, str] = {
    "legacy_auth_import": (
        "legacy 가 KIS 발급 서비스(kbj.services.auth)를 import 한다 — 토큰은 읽기만"
    ),
    "legacy_private_name": "legacy 가 kbj 의 비공개 이름(_로 시작)을 가져온다 — 공개 API 만",
    "legacy_kbj_module": (
        "legacy 가 허용 목록 밖 kbj 모듈을 import 한다 — 브리지·shim·다시 내보내기 자리만"
    ),
    "legacy_unparsable": (
        "legacy .py 를 파싱하지 못해 import 규칙을 볼 수 없다 — 문법을 고치거나 파일을 지운다"
    ),
}
AUTH_MODULE = "kbj.services.auth"
# legacy 가 import 할 수 있는 kbj 모듈(접두사 — 하위 모듈·이름 포함). §9.5 목록 + 묶음 H 요청
# (notifier.format: ET `_split` 다시 내보내기, notifier.store·store.db: ET tg_inbox
# `_default_store` 의 지연 import — notifier 에 기본 저장소 helper 가 생기면 뒤의 둘은 뺀다)
LEGACY_KBJ_ALLOW: tuple[str, ...] = (
    "kbj.data.legacy_bridge",
    "kbj.services.notifier.client",
    "kbj.services.notifier.inbox",
    "kbj.services.notifier.format",
    "kbj.services.notifier.store",
    "kbj.core",
    "kbj.config.settings",
    "kbj.data.private.kis.token",
    "kbj.data.private.kis.rest",
    "kbj.data.private.kis.master",
    "kbj.data.private.kis.credentials",
    "kbj.data.private.krx.client",
    "kbj.data.private.krx.models",
    "kbj.data.private.krx.stocks",
    "kbj.data.public.dart",
    "kbj.data.ratelimit",
    "kbj.data.budget",
    "kbj.data.http",
    "kbj.services.runtime",
    "kbj.store.spool",
    "kbj.store.db",
    # P3 묶음 E1(docs/p3_design.md §1.3·D-P3-10): ET board/engine/* 가 kbj 신고가 엔진을
    # 다시 내보내는 shim 이 되었다(공개 이름만 — 비공개 이름은 kbj 쪽 공개 별칭).
    # 웨이브 3 묶음 S 확정
    "kbj.engines.board",
    # P3 묶음 E3(docs/p3_design.md §1.5·D-P3-12·13): ET etf_tracker_v9 의 themes·tracker(fund_pairs·
    # analyze)·collectors·adapters/* 가 kbj ETF 엔진·운용사 어댑터를 쓰는 shim, tracker 의 기준값은
    # config/markets.yaml(옛 환경변수 QTY_FLOOR·ACTION_PP 대체). 웨이브 3 묶음 S 확정 —
    # tracker 의 추적 대상 판정(네이버 탭 대신 kbj.engines.etf.types.etf_type)도 이 허용으로 쓴다
    "kbj.engines.etf",
    "kbj.data.private.etf_issuers",
    "kbj.config.markets",
)

ALL_RULES: dict[str, str] = {**{g.name: g.desc for g in GROUPS.values()}, **AST_RULES}


@dataclass(frozen=True, order=True)
class Hit:
    path: str
    line: int
    group: str

    def render(self, note: str = "") -> str:
        return f"{self.path}:{self.line}: {self.group} — {ALL_RULES[self.group]}{note}"


# ── 대상 파일 ──────────────────────────────────────────────────────────────────────────────
def tracked_files(root: Path) -> list[str]:
    """git 이 추적하거나 다음 add 로 추적할 파일(레포 루트 기준 posix 경로, 정렬)."""
    out = subprocess.run(  # noqa: S603 — 고정 인자, 셸 없음
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],  # noqa: S607
        check=True,
        capture_output=True,
    ).stdout
    return sorted({p for p in out.decode("utf-8").split("\0") if p})


def in_scope(rel: str) -> bool:
    if rel == SELF or PurePosixPath(rel).suffix.lower() not in SCAN_SUFFIXES:
        return False
    return not any(fnmatch.fnmatchcase(rel, g) for g in EXCLUDE_GLOBS)


def _read_text(path: Path) -> str | None:
    if path.is_symlink() or not path.is_file():
        return None  # 지워졌지만 아직 색인에 있는 파일
    data = path.read_bytes()
    if b"\0" in data[:_BINARY_PROBE]:
        return None
    return data.decode("utf-8", errors="replace")


def area(rel: str) -> str:
    """``kbj``(기준선 없음) · ``legacy`` · ``other``(scripts·config·루트 — legacy 처럼)."""
    head = rel.split("/", 1)[0]
    return head if head in ("kbj", "legacy") else "other"


def scan_hosts(rel: str, text: str, groups: Iterable[Group]) -> list[Hit]:
    """호스트 그룹. 같은 줄·같은 그룹은 한 번. kbj 허용 위치 안은 건너뛴다."""
    found: set[Hit] = set()
    gs = list(groups)
    for lineno, line in enumerate(text.splitlines(), start=1):
        for g in gs:
            if g.pattern.search(line) is None:
                continue
            if rel.startswith("kbj/") and any(fnmatch.fnmatchcase(rel, a) for a in g.allowed):
                continue
            found.add(Hit(rel, lineno, g.name))
    return sorted(found)


# ── AST ────────────────────────────────────────────────────────────────────────────────────
def _is_kbj(name: str) -> bool:
    return name == "kbj" or name.startswith("kbj.")


def _private(part: str) -> bool:
    return part.startswith("_") and not (part.startswith("__") and part.endswith("__"))


def _allowed_module(name: str) -> bool:
    return any(name == a or name.startswith(a + ".") for a in LEGACY_KBJ_ALLOW)


def _chain(node: ast.expr) -> list[str] | None:
    """``a.b.c`` 꼴이면 ``["a", "b", "c"]``, 아니면 None."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return parts[::-1]


_DYNAMIC_IMPORTS = frozenset({"import_module", "__import__"})


def _import_rules(module: str, names: Sequence[str], line: int) -> list[tuple[int, str]]:
    """``from module import names``(names 가 비면 ``import module``) 한 문장의 위반."""
    out: list[tuple[int, str]] = []
    targets = [f"{module}.{n}" for n in names if n != "*"] or [module]
    if any(t == AUTH_MODULE or t.startswith(AUTH_MODULE + ".") for t in [*targets, module]):
        out.append((line, "legacy_auth_import"))
    if any(_private(p) for p in module.split(".")) or any(_private(n) for n in names):
        out.append((line, "legacy_private_name"))
    # from M import n: n 이 모듈(M.n)이든 M 의 속성이든 둘 중 하나가 허용이면 된다
    ok = all(_allowed_module(t) or _allowed_module(module) for t in targets)
    if not ok:
        out.append((line, "legacy_kbj_module"))
    return out


def scan_ast(rel: str, text: str) -> list[Hit]:
    """legacy 의 .py — kbj import 규칙.

    파싱하지 못한 파일은 건너뛰지 않고 실패(``legacy_unparsable``)로 낸다 — 규칙을 볼 수 없는 파일을
    통과로 두지 않는다(절대 규칙 4)."""
    try:
        tree = ast.parse(text, filename=rel)
    except (SyntaxError, ValueError) as e:  # ValueError: 소스에 NUL 등
        return [Hit(rel, getattr(e, "lineno", None) or 1, "legacy_unparsable")]
    found: set[tuple[int, str]] = set()
    bound: set[str] = set()  # kbj 모듈·이름에 묶인 지역 이름
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if _is_kbj(a.name):
                    found.update(_import_rules(a.name, (), node.lineno))
                    bound.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if _is_kbj(node.module):
                names = [a.name for a in node.names]
                found.update(_import_rules(node.module, names, node.lineno))
                bound.update(a.asname or a.name for a in node.names if a.name != "*")
        elif isinstance(node, ast.Call) and node.args:
            fn = node.func
            fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            arg = node.args[0]
            if (
                fname in _DYNAMIC_IMPORTS
                and isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and _is_kbj(arg.value)
            ):
                found.update(_import_rules(arg.value, (), node.lineno))
    # 속성 접근: kbj 에 묶인 이름 뒤의 비공개 속성(`credentials._REAL_BASE`), 문자열로 꺼내는
    # `getattr(credentials, "_REAL_BASE")` 도 같다
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            chain = _chain(node)
            if chain and chain[0] in bound and any(_private(p) for p in chain[1:]):
                found.add((node.lineno, "legacy_private_name"))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("getattr", "hasattr")
            and len(node.args) >= 2
        ):
            chain = _chain(node.args[0])
            name = node.args[1]
            if (
                chain
                and chain[0] in bound
                and isinstance(name, ast.Constant)
                and isinstance(name.value, str)
                and _private(name.value)
            ):
                found.add((node.lineno, "legacy_private_name"))
    return sorted(Hit(rel, line, rule) for line, rule in found)


# ── 기준선 ─────────────────────────────────────────────────────────────────────────────────
class BaselineError(ValueError):
    """기준선 형식 오류 — 종료 코드 2."""


@dataclass(frozen=True)
class BaselineEntry:
    group: str
    path: str
    count: int
    reason: str
    lineno: int


@dataclass
class Baseline:
    header: list[str] = field(default_factory=list[str])
    entries: dict[tuple[str, str], BaselineEntry] = field(
        default_factory=dict[tuple[str, str], BaselineEntry]
    )


def parse_baseline(text: str) -> Baseline:
    """``그룹 | 경로 | 건수 | 정리 단계·사유`` 한 줄에 하나.

    첫 항목 앞의 ``#`` 줄은 머리말로 보존한다."""
    base = Baseline()
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            if not base.entries:
                base.header.append(raw.rstrip())
            continue
        parts = [p.strip() for p in line.split("|", 3)]
        if len(parts) != 4:
            raise BaselineError(f"{lineno}줄: '그룹 | 경로 | 건수 | 사유' 네 칸이어야 한다")
        group, path, count_text, reason = parts
        if group not in GROUPS:
            raise BaselineError(f"{lineno}줄: 모르는 그룹 {group!r}")
        if not path or path.startswith("/") or "\\" in path or "*" in path:
            raise BaselineError(f"{lineno}줄: 경로는 레포 루트 기준 상대 posix 경로(패턴 금지)")
        if not count_text.isdigit() or int(count_text) < 1:
            raise BaselineError(f"{lineno}줄: 건수는 1 이상의 정수")
        if len(reason) < 4:
            raise BaselineError(f"{lineno}줄: 정리 단계·사유가 없다(4자 이상)")
        key = (group, path)
        if key in base.entries:
            raise BaselineError(f"{lineno}줄: 같은 그룹·경로가 두 번 있다")
        base.entries[key] = BaselineEntry(group, path, int(count_text), reason, lineno)
    return base


def load_baseline(path: Path) -> Baseline:
    return parse_baseline(path.read_text(encoding="utf-8")) if path.exists() else Baseline()


def render_baseline(base: Baseline) -> str:
    lines = list(base.header)
    for e in sorted(base.entries.values(), key=lambda e: (e.group, e.path)):
        lines.append(f"{e.group} | {e.path} | {e.count} | {e.reason}")
    return "\n".join(lines) + "\n"


# ── 판정 ───────────────────────────────────────────────────────────────────────────────────
@dataclass
class Report:
    files: int = 0
    hits: list[Hit] = field(default_factory=list[Hit])  # 찾은 줄 전부(kbj 허용 위치 제외)
    errors: list[str] = field(default_factory=list[str])  # 실패 줄(출력용)
    stale: dict[tuple[str, str], int] = field(default_factory=dict[tuple[str, str], int])
    counts: dict[tuple[str, str], int] = field(default_factory=dict[tuple[str, str], int])

    @property
    def failed(self) -> bool:
        return bool(self.errors)


def evaluate(
    hits: Sequence[Hit], baseline: Baseline, groups: Sequence[str], files: int = 0
) -> Report:
    rep = Report(files=files, hits=sorted(hits))
    selected = set(groups)
    counts: Counter[tuple[str, str]] = Counter((h.group, h.path) for h in hits)
    rep.counts = dict(counts)
    by_file: dict[tuple[str, str], list[Hit]] = {}
    for h in rep.hits:
        by_file.setdefault((h.group, h.path), []).append(h)

    # 기준선 자체의 위반(④ 목표 0 그룹, ⑤ kbj 경로)
    for e in baseline.entries.values():
        if e.group not in selected:
            continue
        if GROUPS[e.group].zero:
            rep.errors.append(
                f"기준선 {e.lineno}줄: {e.group} 는 목표 0 그룹이라 기준선에 둘 수 없다 — {e.path}"
            )
        if area(e.path) == "kbj":
            rep.errors.append(
                f"기준선 {e.lineno}줄: kbj/ 경로는 기준선에 둘 수 없다"
                f"(허용 위치로 옮긴다) — {e.path}"
            )

    for (group, path), file_hits in sorted(by_file.items()):
        if group in AST_RULES or GROUPS[group].zero or area(path) == "kbj":
            rep.errors += [h.render() for h in file_hits]
            continue
        entry = baseline.entries.get((group, path))
        if entry is None:
            rep.errors += [h.render(" (기준선에 없는 파일)") for h in file_hits]
        elif len(file_hits) > entry.count:
            rep.errors.append(
                f"{path}: {group} — 기준선 {entry.count}건보다 늘었다(지금 {len(file_hits)}건)"
            )
            rep.errors += [h.render(" (기준선 초과 파일)") for h in file_hits]

    for key, entry in sorted(baseline.entries.items()):
        if entry.group not in selected or GROUPS[entry.group].zero or area(entry.path) == "kbj":
            continue
        now = counts.get(key, 0)
        if now < entry.count:
            rep.stale[key] = now
            rep.errors.append(
                f"{entry.path}: {entry.group} — 기준선 {entry.count}건에서 {now}건으로 줄었다: "
                "기준선을 줄여 고정한다(--update-baseline)"
            )
    return rep


def collect(root: Path, groups: Sequence[str]) -> tuple[list[Hit], int]:
    host_groups = [GROUPS[g] for g in groups if g in GROUPS]
    want_ast = any(g in AST_RULES for g in groups)
    hits: list[Hit] = []
    files = [rel for rel in tracked_files(root) if in_scope(rel)]
    for rel in files:
        text = _read_text(root / rel)
        if text is None:
            continue
        hits += scan_hosts(rel, text, host_groups)
        if want_ast and rel.startswith("legacy/") and rel.endswith(".py"):
            hits += [h for h in scan_ast(rel, text) if h.group in groups]
    return hits, len(files)


def shrink(baseline: Baseline, rep: Report, groups: Sequence[str]) -> Baseline:
    """줄어든 건수로만 다시 쓴다. 0 건이 된 줄은 지운다. 늘리는 일은 없다."""
    out = Baseline(header=list(baseline.header))
    for key, e in baseline.entries.items():
        if e.group in groups and key in rep.stale:
            now = rep.stale[key]
            if now > 0:
                out.entries[key] = BaselineEntry(e.group, e.path, now, e.reason, e.lineno)
        else:
            out.entries[key] = e
    return out


def summary_table(rep: Report, baseline: Baseline, groups: Sequence[str]) -> list[str]:
    rows = ["그룹 | 목표 | kbj 위반 | legacy·기타 건수 | 기준선 | 정리 단계"]
    for name in groups:
        if name in AST_RULES:
            n = sum(1 for h in rep.hits if h.group == name)
            rows.append(f"{name} | 0 | - | {n} | - | P2")
            continue
        g = GROUPS[name]
        kbj = sum(1 for h in rep.hits if h.group == name and area(h.path) == "kbj")
        rest = sum(1 for h in rep.hits if h.group == name and area(h.path) != "kbj")
        base = sum(e.count for e in baseline.entries.values() if e.group == name)
        rows.append(
            f"{name} | {'0' if g.zero else '기준선'} | {kbj} | {rest} | "
            f"{'-' if g.zero else base} | {g.stage}"
        )
    return rows


def _parse_groups(text: str | None) -> list[str]:
    names = list(GROUPS) + list(AST_RULES)
    if not text:
        return names
    picked = [t.strip() for t in text.split(",") if t.strip()]
    unknown = [t for t in picked if t not in names]
    if unknown or not picked:
        raise ValueError(f"모르는 그룹: {', '.join(unknown) or '(없음)'}")
    return [n for n in names if n in picked]


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="직접 호출 금지 검사(KBJ 정본 경계)")
    ap.add_argument("--root", type=Path, default=REPO_ROOT, help="레포 루트")
    ap.add_argument("--baseline", type=Path, default=None, help="기준선 파일(기본: 루트 아래)")
    ap.add_argument("--groups", default=None, help="쉼표로 고른 그룹만(기본: 전부)")
    ap.add_argument("--update-baseline", action="store_true", help="줄어든 건수로 기준선 고정")
    ap.add_argument("--list", action="store_true", help="그룹·허용 위치·정리 단계 출력")
    args = ap.parse_args(argv)
    baseline_path: Path = args.baseline or args.root / "scripts" / "canonical_baseline.txt"
    try:
        groups = _parse_groups(args.groups)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if args.list:
        for g in GROUPS.values():
            where = ", ".join(g.allowed) or "(kbj 어디에도 금지)"
            kind = "목표 0" if g.zero else "기준선"
            print(f"{g.name} | {kind} | 허용: {where} | {g.stage} | {g.desc}")
        for name, desc in AST_RULES.items():
            print(f"{name} | 목표 0 | legacy/**/*.py AST | P2 | {desc}")
        return 0
    try:
        baseline = load_baseline(baseline_path)
    except BaselineError as e:
        print(f"기준선 오류({baseline_path.name}): {e}", file=sys.stderr)
        return 2
    try:
        hits, files = collect(args.root, groups)
    except subprocess.CalledProcessError:
        print("git ls-files 실패 — git 레포 안에서 돌린다", file=sys.stderr)
        return 2
    rep = evaluate(hits, baseline, groups, files)

    if args.update_baseline:
        growing = [e for e in rep.errors if "기준선을 줄여" not in e]
        if growing:
            for line in growing:
                print(line)
            print("기준선 갱신 거부: 새 위반·증가가 있다 — 기준선은 줄이는 방향으로만 고친다")
            return 1
        new = shrink(baseline, rep, groups)
        if rep.stale:
            baseline_path.write_text(render_baseline(new), encoding="utf-8")
        print(f"기준선 갱신: {len(rep.stale)}줄 줄임({baseline_path.name})")
        return 0

    for line in rep.errors:
        print(line)
    for row in summary_table(rep, baseline, groups):
        print(row)
    verdict = "실패" if rep.failed else "통과"
    print(f"직접 호출 금지 검사 {verdict}: 파일 {files}개, 실패 {len(rep.errors)}건")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
