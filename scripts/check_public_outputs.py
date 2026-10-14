"""공개 산출물 검사(우산) — 레포·공개 사이트·README 미디어·데모 설정을 한 번에 본다.

docs/p9_design.md §3.2, ADR 0035. 기존 검사를 import 해서 부른다(규칙을 두 벌 두지 않는다).

    uv run python scripts/check_public_outputs.py [--site DIR] [--skip-site] [--json]

단계
- ① repo : `check_public_safety.scan`(레포 전체 — 비밀 형태·홈 경로·큰 파일·DB 파일) +
  `fixture_provenance`(tests/**·web/test/** 의 데이터 파일은 경로에 `synthetic` 이 있거나
  `scripts/fixture_provenance.txt` 에 출처가 적혀 있어야 한다).
- ② site : `--site DIR`(빌드한 공개 사이트 — dist-public)이 있으면 그 파일 전부. 없으면
  `build_public_site.sh --dry-run` 과 같은 길(`public_export build --no-db`)로 임시 공개 데이터를
  만들어 `public_export check_tree` + 공개 모양 웹 픽스처(web/test/fixtures/**/public/**)까지.
  파일마다 비밀 형태(SECRET_SHAPES), JSON 은 source 칸의 로그인 출처 이름(LOGIN_SOURCES)·
  원시세 칸(`price_problems`). 번들 JS 는 비밀 형태만 본다(화면 문구에 출처 이름이 정당하게 나온다).
- ③ media: docs/media/** — 크기(GIF ≤ 3MB·PNG ≤ 1MB), 종류(.gif·.png·MANIFEST.txt 만), PNG 글자
  청크·GIF 주석에 경로·비밀 형태 0, MANIFEST.txt 에 모든 파일이 적히고 명령·커밋·합성 세계·시드·
  서버 시계 칸이 채워짐.
- ④ demo : Dockerfile.demo.dockerignore 가 '*' 로 시작하는 허용 목록이고 .env*·legacy·state·
  *.key·*.pem 을 열지 않음, docker-compose.demo.yml 규칙(루프백 포트·env_file·볼륨 없음·환경변수
  이름·이미지 이름), 운영 compose·이미지 입력에 데모 계정 값이 없음.

종료 코드: 0 통과 · 1 발견 · 2 사용법·도구 없음. 출력은 `단계 경로:줄: 규칙 — 설명` 뿐(찾은 값
없음 — 절대 규칙 5).
"""

from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any, Final

REPO_ROOT: Final = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from kbj.core.masking import SECRET_SHAPES  # noqa: E402 — 레포 루트를 sys.path 에 넣은 뒤
from kbj.services.public_export.manifest import (  # noqa: E402
    check_tree,
    login_source_paths,
    price_problems,
)

PROVENANCE: Final = REPO_ROOT / "scripts" / "fixture_provenance.txt"
DATA_SUFFIXES: Final = frozenset({".json", ".jsonl", ".csv", ".yaml", ".yml", ".xml", ".gz"})
FIXTURE_ROOTS: Final = ("tests/", "web/test/")
MEDIA_DIR: Final = "docs/media"
MEDIA_MANIFEST: Final = "MANIFEST.txt"
MAX_GIF: Final = 3 * 1024 * 1024
MAX_PNG: Final = 1024 * 1024
MANIFEST_KEYS: Final = ("command", "commit", "world", "seed", "server_clock")
DEMO_COMPOSE: Final = "docker-compose.demo.yml"
DEMO_IGNORE: Final = "Dockerfile.demo.dockerignore"
DEMO_PASSWORD_ENV: Final = "KBJ_DEMO_PASSWORD"  # noqa: S105 — 환경변수 이름
DEMO_PASSWORD_DEFAULT: Final = "kbj-demo-only"  # noqa: S105 — 합성 데모 전용 고정값(README)을 '찾으려고'
# 데모 빌드 문맥에 들어오면 안 되는 경로(허용 목록이 열면 실패) — ④
DEMO_FORBIDDEN_PROBES: Final = (
    ".env", ".env.local", "config/.env", "kbj/.env.prod", "web/.env", "tests/.env.test",
    "legacy/etf_traker/app.py", "state/kis_token.json", "kbj/services/x.key", "config/tls.pem",
    "kbj/secret_token.json",
)  # fmt: skip
# 데모 계정 값이 없어야 하는 운영 쪽 파일(운영 compose·운영 이미지 입력)
OPS_FILES: Final = ("docker-compose.yml", "Dockerfile", ".dockerignore")
OPS_DIRS: Final = ("kbj", "config")
_PATH_IN_TEXT: Final = re.compile(r"(?:/home/|/Users/|/root/|/tmp/|/var/|[A-Za-z]:\\)")

RULES: Final[dict[str, str]] = {
    "fixture_provenance": "시험 데이터 파일의 출처가 없다(경로에 synthetic 도, 목록 줄도 없음)",
    "provenance_list": "fixture_provenance.txt 형식 오류·없는 생성기·걸리는 것 없는 줄",
    "site_build": "임시 공개 데이터를 만들지 못했다",
    "site_check": "공개 데이터 검사(public_export check_tree) 실패",
    "secret_shape": "비밀값 형태",
    "login_source": "source 칸에 로그인 등급 출처 이름",
    "price_fields": "원시세 칸(close·price·volume·nav …) — 공개 출처 예외 밖",
    "not_json": "JSON 이 아니다",
    "media_kind": "docs/media 에 허용하지 않는 종류(.gif·.png·MANIFEST.txt 만)",
    "media_size": "미디어 크기 상한 초과(GIF 3MB·PNG 1MB)",
    "media_metadata": "PNG 글자 청크·GIF 주석에 경로·비밀 형태",
    "media_unreadable": "이미지를 열 수 없다",
    "media_manifest": "MANIFEST.txt 에 파일이 빠졌거나 칸(명령·커밋·세계·시드·시계)이 비었다",
    "dockerignore_allowlist": "데모 dockerignore 가 '*' 로 시작하는 허용 목록이 아니다",
    "dockerignore_opens": "데모 dockerignore 가 .env*·legacy·state·*.key·*.pem 을 연다",
    "compose_rule": "데모 compose 규칙 위반",
    "demo_account_in_ops": "운영 compose·운영 이미지 입력에 데모 계정 값",
    "missing_file": "검사할 파일이 없다",
}


@dataclass(frozen=True, order=True)
class Finding:
    stage: str  # repo·site·media·demo
    path: str
    line: int  # 파일 단위는 0
    rule: str
    detail: str = ""  # 값이 아니라 위치·이름만(JSON 경로·규칙 이름)

    def render(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        extra = f" ({self.detail})" if self.detail else ""
        return f"{self.stage} {where}: {self.rule} — {RULES[self.rule]}{extra}"


def _load_safety() -> ModuleType:
    """scripts/check_public_safety.py 를 모듈로(스크립트 폴더는 패키지가 아니다)."""
    path = REPO_ROOT / "scripts" / "check_public_safety.py"
    spec = importlib.util.spec_from_file_location("check_public_safety", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"불러올 수 없다: {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("check_public_safety", mod)
    spec.loader.exec_module(mod)
    return mod


# ── ① repo ────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Provenance:
    pattern: str
    origin: str  # synthetic:<경로>·synthetic:hand-written·public:<출처>
    reason: str
    lineno: int


def parse_provenance(text: str, root: Path) -> tuple[list[Provenance], list[Finding]]:
    """`경로 패턴 | 출처 | 사유`. 형식 오류·없는 생성기는 Finding(provenance_list)."""
    entries: list[Provenance] = []
    bad: list[Finding] = []
    rel = "scripts/fixture_provenance.txt"
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|", 2)]
        if len(parts) != 3 or not parts[0] or len(parts[2]) < 4:
            bad.append(Finding("repo", rel, lineno, "provenance_list", "세 칸·사유 4자 이상"))
            continue
        pattern, origin, reason = parts
        kind, _, what = origin.partition(":")
        if kind not in ("synthetic", "public") or not what:
            bad.append(
                Finding("repo", rel, lineno, "provenance_list", "출처는 synthetic:… 또는 public:…")
            )
            continue
        if kind == "synthetic" and what != "hand-written" and not (root / what).is_file():
            bad.append(Finding("repo", rel, lineno, "provenance_list", "생성기 파일이 없다"))
            continue
        entries.append(Provenance(pattern, origin, reason, lineno))
    return entries, bad


def is_fixture_data(rel: str) -> bool:
    p = PurePosixPath(rel)
    return rel.startswith(FIXTURE_ROOTS) and p.suffix.lower() in DATA_SUFFIXES


def check_provenance(files: Iterable[str], entries: Sequence[Provenance]) -> list[Finding]:
    """데이터 파일마다 synthetic 경로 또는 목록 한 줄.

    아무 파일에도 안 걸리는 줄도 알린다(묵은 줄)."""
    found: list[Finding] = []
    used: set[int] = set()
    for rel in files:
        if (
            not is_fixture_data(rel)
            or "synthetic" in PurePosixPath(rel).parts
            or "synthetic" in rel
        ):
            continue
        hit = next((e for e in entries if fnmatch.fnmatchcase(rel, e.pattern)), None)
        if hit is None:
            found.append(Finding("repo", rel, 0, "fixture_provenance"))
        else:
            used.add(hit.lineno)
    for e in entries:
        if e.lineno not in used:
            found.append(
                Finding(
                    "repo",
                    "scripts/fixture_provenance.txt",
                    e.lineno,
                    "provenance_list",
                    "걸리는 파일 없음",
                )
            )
    return found


def stage_repo(root: Path) -> list[Finding]:
    safety = _load_safety()
    report = safety.scan(
        root, allow=safety.load_allow(root / "scripts" / "public_safety_allow.txt")
    )
    out = [Finding("repo", f.path, f.line, "secret_shape", f.rule) for f in report.findings]
    prov = root / "scripts" / "fixture_provenance.txt"
    if not prov.is_file():
        return [*out, Finding("repo", "scripts/fixture_provenance.txt", 0, "missing_file")]
    entries, bad = parse_provenance(prov.read_text(encoding="utf-8"), root)
    return [*out, *bad, *check_provenance(safety.tracked_files(root), entries)]


# ── ② site ────────────────────────────────────────────────────────────────────────────


def scan_secret_text(stage: str, rel: str, text: str) -> list[Finding]:
    out: set[Finding] = set()
    for lineno, line in enumerate(text.splitlines(), start=1):
        for name, pat in SECRET_SHAPES.items():
            if pat.search(line):
                out.add(Finding(stage, rel, lineno, "secret_shape", name))
    return sorted(out)


def scan_public_json(stage: str, rel: str, name: str, text: str) -> list[Finding]:
    """공개 JSON 한 파일 — 비밀 형태·source 의 로그인 출처 이름·원시세 칸.

    name = 공개 이름(원시세 예외 표의 키)."""
    out = scan_secret_text(stage, rel, text)
    try:
        raw: object = json.loads(text)
    except ValueError:
        return [*out, Finding(stage, rel, 0, "not_json")]
    out += [Finding(stage, rel, 0, "login_source", p) for p in login_source_paths(raw)]
    for msg in price_problems(name, raw):
        out.append(Finding(stage, rel, 0, "price_fields", msg.split(": ", 1)[-1]))
    return out


def scan_site_dir(stage: str, site: Path, label: str) -> list[Finding]:
    """빌드한 공개 사이트(또는 공개 데이터 디렉터리) — 파일 전부 비밀 형태, JSON 은 공개 규칙."""
    out: list[Finding] = []
    for path in sorted(p for p in site.rglob("*") if p.is_file()):
        rel_in = path.relative_to(site).as_posix()
        rel = f"{label}/{rel_in}"
        data = path.read_bytes()
        if b"\0" in data[:8192]:
            continue  # 바이너리(엑셀·이미지) — 공개 데이터 검사(check_tree)가 이름·해시를 본다
        text = data.decode("utf-8", errors="replace")
        if path.suffix == ".json" and rel_in != "manifest.webmanifest":
            name = rel_in.removeprefix("data/")
            out += scan_public_json(stage, rel, name, text)
        else:
            out += scan_secret_text(stage, rel, text)
    return out


def public_fixture_files(root: Path) -> list[tuple[str, str]]:
    """공개 모양 웹 픽스처 — (레포 경로, 공개 이름 = 'public' 폴더 아래 경로)."""
    out: list[tuple[str, str]] = []
    base = root / "web" / "test" / "fixtures"
    for path in sorted(base.rglob("*.json")):
        parts = path.relative_to(base).parts
        if "public" not in parts:
            continue
        i = parts.index("public")
        out.append((path.relative_to(root).as_posix(), "/".join(parts[i + 1 :])))
    return out


def stage_site(root: Path, site: Path | None) -> list[Finding]:
    if site is not None:
        return scan_site_dir("site", site, site.name or "site")
    out: list[Finding] = []
    with tempfile.TemporaryDirectory(prefix="kbj-public-outputs-") as tmp:
        data = Path(tmp) / "data"
        p = subprocess.run(  # noqa: S603 — 고정 인자(이 파이썬·이 패키지)
            [
                sys.executable,
                "-m",
                "kbj.services.public_export",
                "build",
                "--out",
                str(data),
                "--no-db",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if p.returncode != 0:
            return [
                Finding(
                    "site", "(public_export build --no-db)", 0, "site_build", f"종료 {p.returncode}"
                )
            ]
        out += [
            Finding("site", "(임시 공개 데이터)", 0, "site_check", m.split(":", 1)[0])
            for m in check_tree(data)
        ]
        out += scan_site_dir("site", data, "(임시 공개 데이터)")
    for rel, name in public_fixture_files(root):
        out += scan_public_json("site", rel, name, (root / rel).read_text(encoding="utf-8"))
    return out


# ── ③ media ───────────────────────────────────────────────────────────────────────────


def _text_bad(text: str) -> str | None:
    if _PATH_IN_TEXT.search(text):
        return "경로"
    for name, pat in SECRET_SHAPES.items():
        if pat.search(text):
            return name
    return None


def image_text_problems(path: Path) -> list[str] | None:
    """PNG 글자 청크(tEXt·iTXt·zTXt)·GIF 주석(프레임마다)에 경로·비밀 형태. 못 열면 None."""
    from PIL import Image, ImageSequence

    try:
        with Image.open(path) as im:
            texts: list[str] = []
            if im.format == "PNG":
                texts += [f"{k}={v}" for k, v in getattr(im, "text", {}).items()]
            elif im.format == "GIF":
                for frame in ImageSequence.Iterator(im):
                    c = frame.info.get("comment")
                    if isinstance(c, bytes):
                        texts.append(c.decode("utf-8", errors="replace"))
            else:
                return None
    except OSError:
        return None
    return [why for t in texts if (why := _text_bad(t)) is not None]


def parse_media_manifest(text: str) -> tuple[dict[str, str], set[str]]:
    keys: dict[str, str] = {}
    files: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if k == "file":
            files.add(v.split("|", 1)[0].strip())
        else:
            keys[k] = v
    return keys, files


def stage_media(root: Path) -> list[Finding]:
    media = root / MEDIA_DIR
    if not media.is_dir():
        return []
    out: list[Finding] = []
    names: list[str] = []
    for path in sorted(p for p in media.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        name = path.relative_to(media).as_posix()
        if name == MEDIA_MANIFEST:
            continue
        names.append(name)
        suffix = path.suffix.lower()
        if suffix not in (".gif", ".png"):
            out.append(Finding("media", rel, 0, "media_kind"))
            continue
        cap = MAX_GIF if suffix == ".gif" else MAX_PNG
        if path.stat().st_size > cap:
            out.append(Finding("media", rel, 0, "media_size", f"{path.stat().st_size:,} B"))
        bad = image_text_problems(path)
        if bad is None:
            out.append(Finding("media", rel, 0, "media_unreadable"))
        else:
            out += [Finding("media", rel, 0, "media_metadata", why) for why in bad]
    man = media / MEDIA_MANIFEST
    man_rel = f"{MEDIA_DIR}/{MEDIA_MANIFEST}"
    if not names:
        return out
    if not man.is_file():
        return [*out, Finding("media", man_rel, 0, "media_manifest", "MANIFEST.txt 없음")]
    text = man.read_text(encoding="utf-8")
    out += [
        Finding("media", man_rel, f.line, f.rule, f.detail)
        for f in scan_secret_text("media", man_rel, text)
    ]
    keys, listed = parse_media_manifest(text)
    out += [
        Finding("media", man_rel, 0, "media_manifest", f"칸 '{k}' 비었음")
        for k in MANIFEST_KEYS
        if not keys.get(k)
    ]
    out += [
        Finding("media", man_rel, 0, "media_manifest", f"빠진 파일 {n}")
        for n in names
        if n not in listed
    ]
    out += [
        Finding("media", man_rel, 0, "media_manifest", f"없는 파일 {n}")
        for n in sorted(listed - set(names))
    ]
    if _PATH_IN_TEXT.search(text):
        out.append(Finding("media", man_rel, 0, "media_metadata", "경로"))
    return out


# ── ④ demo ────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class IgnoreRule:
    regex: re.Pattern[str]
    negate: bool


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """dockerignore 패턴(Go filepath.Match + `**`) → 정규식(전체 일치)."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**", i):
            i += 2
            if pattern.startswith("/", i):
                i += 1
                out.append("(?:.*/)?")
            else:
                out.append(".*")
            continue
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out))


def parse_dockerignore(text: str) -> tuple[list[IgnoreRule], str | None]:
    """규칙 목록과 첫 규칙 글자(주석·빈 줄 뺀 첫 줄)."""
    rules: list[IgnoreRule] = []
    first: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if first is None:
            first = line
        negate = line.startswith("!")
        pat = line[1:].strip() if negate else line
        pat = str(PurePosixPath(pat.lstrip("/"))) if pat not in ("", "/") else "*"
        rules.append(IgnoreRule(_glob_regex(pat), negate))
    return rules, first


def dockerignore_allows(rules: Sequence[IgnoreRule], rel: str) -> bool:
    """빌드 문맥에 들어오는가 — 마지막으로 걸린 규칙이 정한다(패턴은 경로 또는 그 부모에 걸린다)."""
    parts = PurePosixPath(rel).parts
    prefixes = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    excluded = False
    for r in rules:
        if any(r.regex.fullmatch(p) for p in prefixes):
            excluded = not r.negate
    return not excluded


def dockerignore_problems(root: Path) -> list[Finding]:
    path = root / DEMO_IGNORE
    if not path.is_file():
        return [Finding("demo", DEMO_IGNORE, 0, "missing_file")]
    rules, first = parse_dockerignore(path.read_text(encoding="utf-8"))
    out: list[Finding] = []
    if first != "*":
        out.append(Finding("demo", DEMO_IGNORE, 0, "dockerignore_allowlist"))
    out += [
        Finding("demo", DEMO_IGNORE, 0, "dockerignore_opens", probe)
        for probe in DEMO_FORBIDDEN_PROBES
        if dockerignore_allows(rules, probe)
    ]
    return out


def compose_problems(doc: Mapping[str, Any]) -> list[str]:
    """데모 compose 규칙(§2.5) — 문제 설명 목록(값 없음).

    tests/unit/demo/test_demo_compose.py 가 같은 함수를 쓴다."""
    out: list[str] = []
    if doc.get("name") != "kbj-demo":
        out.append("name 이 kbj-demo 가 아니다")
    services = doc.get("services")
    if not isinstance(services, Mapping) or not services:
        return [*out, "services 가 없다"]
    for sname, raw in services.items():  # pyright: ignore[reportUnknownVariableType]
        svc: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}  # pyright: ignore[reportUnknownVariableType]
        where = f"services.{sname}"
        if "env_file" in svc:
            out.append(f"{where}: env_file 이 있다(.env 를 읽지 않는다)")
        if "volumes" in svc:
            out.append(f"{where}: volumes 가 있다(호스트 파일을 붙이지 않는다)")
        if svc.get("network_mode") == "host":
            out.append(f"{where}: network_mode host")
        image = str(svc.get("image", ""))
        if not image.startswith("kbj-demo:"):
            out.append(f"{where}: 이미지 이름이 kbj-demo:… 가 아니다")
        build = svc.get("build")
        if not isinstance(build, Mapping) or build.get("dockerfile") != "Dockerfile.demo":
            out.append(f"{where}: build.dockerfile 이 Dockerfile.demo 가 아니다")
        for port in svc.get("ports") or []:  # pyright: ignore[reportUnknownVariableType]
            if not str(port).startswith("127.0.0.1:"):  # pyright: ignore[reportUnknownArgumentType]
                out.append(f"{where}: 포트가 127.0.0.1 에만 열리지 않는다")
        env = svc.get("environment") or {}
        names: list[str] = (
            [str(k) for k in env]  # pyright: ignore[reportUnknownVariableType]
            if isinstance(env, Mapping)
            else [str(x).split("=", 1)[0] for x in env]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        )
        for n in names:
            upper = n.upper()
            if any(w in upper for w in ("KEY", "SECRET", "TOKEN")):
                out.append(f"{where}: 비밀 이름의 환경변수 {n}")
            if "PASSWORD" in upper and n != DEMO_PASSWORD_ENV:
                out.append(f"{where}: 데모 비밀번호 밖의 비밀번호 환경변수 {n}")
    return out


def stage_demo(root: Path) -> list[Finding]:
    import yaml

    out = dockerignore_problems(root)
    compose = root / DEMO_COMPOSE
    if not compose.is_file():
        out.append(Finding("demo", DEMO_COMPOSE, 0, "missing_file"))
    else:
        doc: object = yaml.safe_load(compose.read_text(encoding="utf-8"))
        problems = compose_problems(doc if isinstance(doc, Mapping) else {})  # pyright: ignore[reportUnknownArgumentType]
        out += [Finding("demo", DEMO_COMPOSE, 0, "compose_rule", p) for p in problems]
    ops: list[Path] = [root / f for f in OPS_FILES if (root / f).is_file()]
    for d in OPS_DIRS:
        ops += [
            p for p in sorted((root / d).rglob("*")) if p.is_file() and "__pycache__" not in p.parts
        ]
    for path in ops:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if DEMO_PASSWORD_DEFAULT in text:
            out.append(Finding("demo", path.relative_to(root).as_posix(), 0, "demo_account_in_ops"))
    return out


# ── 실행 ──────────────────────────────────────────────────────────────────────────────


def run(root: Path, *, site: Path | None = None, skip_site: bool = False) -> list[Finding]:
    found = stage_repo(root)
    if not skip_site:
        found += stage_site(root, site)
    found += stage_media(root)
    found += stage_demo(root)
    return sorted(set(found))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_public_outputs", description="공개 산출물 검사(우산)")
    ap.add_argument("--root", type=Path, default=REPO_ROOT, help="레포 루트(시험용)")
    ap.add_argument("--site", type=Path, default=None, help="빌드한 공개 사이트(dist-public)")
    ap.add_argument("--skip-site", action="store_true", help="② site 단계를 건너뛴다")
    ap.add_argument("--json", action="store_true", help="발견을 JSON 으로")
    args = ap.parse_args(argv)
    if args.site is not None and not args.site.is_dir():
        print(f"사이트 디렉터리가 없다: {args.site}", file=sys.stderr)
        return 2
    try:
        found = run(args.root, site=args.site, skip_site=args.skip_site)
    except subprocess.CalledProcessError:
        print("git ls-files 실패 — git 레포 안에서 돌린다", file=sys.stderr)
        return 2
    except ModuleNotFoundError as e:
        print(f"도구 없음: {e.name}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps([asdict(f) for f in found], ensure_ascii=False, indent=1))
    else:
        for f in found:
            print(f.render())
        stages = "repo·media·demo" if args.skip_site else "repo·site·media·demo"
        verdict = "실패" if found else "통과"
        print(f"공개 산출물 검사 {verdict}({stages}): 발견 {len(found)}건")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
