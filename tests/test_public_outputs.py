"""scripts/check_public_outputs.py — 단계마다 일부러 만든 위반은 1, 깨끗한 임시 트리는 0.

비밀값 모양 문자열은 실행할 때 이어 붙여 만든다(이 파일이 검사에 걸리지 않게 —
test_public_safety 와 같다).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, PngImagePlugin

import check_public_outputs as cpo

ROOT = Path(__file__).resolve().parent.parent
FAKE_TOKEN = "7012345678" + ":AAF" + "q7Zx2" * 6 + "w9"  # 텔레그램 봇 토큰 '모양'(실제 값 아님)
MANIFEST_OK = (
    "# 시험용\n"
    "command: bash scripts/demo.sh --gif docs/media\n"
    "commit: abc1234\n"
    "world: 합성\n"
    "seed: api_world=1\n"
    "server_clock: 2026-09-15T18:00+09:00\n"
    "file: kbj_demo.gif | GIF\n"
    "file: kbj_demo_p1.png | PNG\n"
)


def _png(
    path: Path, *, text: dict[str, str] | None = None, size: tuple[int, int] = (64, 40)
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    info = PngImagePlugin.PngInfo()
    for k, v in (text or {}).items():
        info.add_text(k, v)
    Image.new("RGB", size, (240, 240, 240)).save(path, format="PNG", pnginfo=info)


def _gif(path: Path, *, comment: bytes | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = [Image.new("RGB", (64, 40), c).quantize(16) for c in ((255, 0, 0), (0, 0, 255))]
    kw: dict[str, Any] = {"comment": comment} if comment is not None else {}
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0, **kw)


def clean_tree(tmp_path: Path) -> Path:
    """깨끗한 최소 레포 — 허용 목록·출처 목록·미디어·데모 설정(레포의 실제 두 파일 그대로)."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)  # noqa: S603, S607
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "public_safety_allow.txt").write_text("# 없음\n", encoding="utf-8")
    (tmp_path / "scripts" / "gen.py").write_text("# 합성 생성기\n", encoding="utf-8")
    (tmp_path / "scripts" / "fixture_provenance.txt").write_text(
        "tests/golden/x/* | synthetic:scripts/gen.py | 시험용 합성 골든\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "golden" / "x").mkdir(parents=True)
    (tmp_path / "tests" / "golden" / "x" / "a.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "tests" / "fixtures" / "synthetic").mkdir(parents=True)
    (tmp_path / "tests" / "fixtures" / "synthetic" / "b.csv").write_text("a,b\n", encoding="utf-8")
    media = tmp_path / "docs" / "media"
    _gif(media / "kbj_demo.gif")
    _png(media / "kbj_demo_p1.png")
    (media / "MANIFEST.txt").write_text(MANIFEST_OK, encoding="utf-8")
    for name in (cpo.DEMO_COMPOSE, cpo.DEMO_IGNORE):
        shutil.copy(ROOT / name, tmp_path / name)
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "kbj").mkdir()
    (tmp_path / "kbj" / "__init__.py").write_text("", encoding="utf-8")
    return tmp_path


def rules_of(found: list[cpo.Finding]) -> set[tuple[str, str]]:
    return {(f.stage, f.rule) for f in found}


def test_clean_tree_passes(tmp_path: Path) -> None:
    root = clean_tree(tmp_path)
    assert cpo.run(root, skip_site=True) == []
    assert cpo.main(["--root", str(root), "--skip-site"]) == 0


def test_real_repo_passes() -> None:
    """레포 자신(① repo·② site·③ media·④ demo) — CI public-outputs 잡과 같은 명령."""
    found = cpo.run(ROOT)
    assert found == [], [f.render() for f in found]


# ── ① repo ──


def test_repo_secret_shape_is_reported_without_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = clean_tree(tmp_path)
    (root / "kbj" / "leak.py").write_text(f"TOKEN = '{FAKE_TOKEN}'\n", encoding="utf-8")
    assert ("repo", "secret_shape") in rules_of(cpo.run(root, skip_site=True))
    assert cpo.main(["--root", str(root), "--skip-site"]) == 1
    out = capsys.readouterr().out
    assert "kbj/leak.py:1: secret_shape" in out and FAKE_TOKEN not in out


def test_repo_fixture_without_provenance_fails(tmp_path: Path) -> None:
    root = clean_tree(tmp_path)
    (root / "web" / "test" / "fixtures").mkdir(parents=True)
    (root / "web" / "test" / "fixtures" / "real_quotes.json").write_text("[]\n", encoding="utf-8")
    (root / "tests" / "data.yml").write_text("a: 1\n", encoding="utf-8")
    got = {(f.path, f.rule) for f in cpo.run(root, skip_site=True)}
    assert ("web/test/fixtures/real_quotes.json", "fixture_provenance") in got
    assert ("tests/data.yml", "fixture_provenance") in got
    # synthetic 경로·코드 파일·목록에 적힌 것은 통과
    assert not any(p.startswith("tests/fixtures/synthetic") or p.endswith(".py") for p, _ in got)


@pytest.mark.parametrize(
    "line",
    [
        "tests/golden/x/* | synthetic:scripts/없는_생성기.py | 시험용 합성 골든",
        "tests/golden/x/* | 어딘가 | 시험용 합성 골든",
        "tests/golden/x/* | synthetic:hand-written |",
        "tests/golden/x/*",
    ],
)
def test_repo_provenance_list_errors(tmp_path: Path, line: str) -> None:
    root = clean_tree(tmp_path)
    (root / "scripts" / "fixture_provenance.txt").write_text(line + "\n", encoding="utf-8")
    assert ("repo", "provenance_list") in rules_of(cpo.run(root, skip_site=True))


def test_repo_stale_provenance_line_is_reported(tmp_path: Path) -> None:
    root = clean_tree(tmp_path)
    with (root / "scripts" / "fixture_provenance.txt").open("a", encoding="utf-8") as f:
        f.write("web/test/fixtures/gone/* | synthetic:hand-written | 지운 픽스처\n")
    got = cpo.run(root, skip_site=True)
    assert any(f.rule == "provenance_list" and f.detail == "걸리는 파일 없음" for f in got)


def test_real_provenance_list_parses_and_names_existing_generators() -> None:
    entries, bad = cpo.parse_provenance(cpo.PROVENANCE.read_text(encoding="utf-8"), ROOT)
    assert bad == [] and len(entries) >= 10


# ── ② site ──


def test_site_dir_rules(tmp_path: Path) -> None:
    site = tmp_path / "dist-public"
    (site / "assets").mkdir(parents=True)
    (site / "data").mkdir()
    (site / "assets" / "main.js").write_text(
        f'const a="{FAKE_TOKEN}";const s="KRX 표시";\n', encoding="utf-8"
    )
    env = {"source": "DART", "as_of": "2026-10-14T00:00:00+09:00", "quality": "ok", "data": {}}
    (site / "data" / "events.json").write_text(
        json.dumps({**env, "data": {"rows": [{"close": 1}]}}), encoding="utf-8"
    )
    (site / "data" / "calendar.json").write_text(
        json.dumps({**env, "source": "KIS", "data": {"session": {"open": "09:00"}}}),
        encoding="utf-8",
    )
    (site / "data" / "bad.json").write_text("{", encoding="utf-8")
    got = {(f.path, f.rule, f.detail) for f in cpo.scan_site_dir("site", site, "dist-public")}
    assert ("dist-public/assets/main.js", "secret_shape", "telegram_bot_token") in got
    assert not any(
        p == "dist-public/assets/main.js" and r == "login_source" for p, r, _ in got
    )  # JS 문구는 허용
    assert ("dist-public/data/events.json", "price_fields", "시세 칸($.data.rows[0].close)") in got
    assert ("dist-public/data/calendar.json", "login_source", "$.source") in got
    assert not any(
        p == "dist-public/data/calendar.json" and r == "price_fields" for p, r, _ in got
    )  # 예외
    assert ("dist-public/data/bad.json", "not_json", "") in got
    assert cpo.main(["--site", str(site), "--skip-site"]) in (
        0,
        1,
    )  # --skip-site 가 이기면 site 를 안 본다


def test_site_cli_on_bad_site_exits_1(tmp_path: Path) -> None:
    site = tmp_path / "dist-public"
    (site / "data").mkdir(parents=True)
    (site / "data" / "x.json").write_text('{"source":"DART","data":{"nav":1}}', encoding="utf-8")
    assert [f.rule for f in cpo.stage_site(ROOT, site)] == ["price_fields"]
    assert cpo.main(["--site", str(tmp_path / "없음")]) == 2


def test_public_web_fixtures_are_scanned() -> None:
    names = dict(cpo.public_fixture_files(ROOT))
    assert names["web/test/fixtures/public/calendar.json"] == "calendar.json"
    assert names["web/test/fixtures/public/fin/Q00001.json"] == "fin/Q00001.json"


# ── ③ media ──


def test_media_rules(tmp_path: Path) -> None:
    root = clean_tree(tmp_path)
    media = root / "docs" / "media"
    big = media / "big.gif"
    big.write_bytes(b"GIF89a" + b"\0" * (cpo.MAX_GIF + 1))  # 4MB 가까운 가짜 GIF(못 여는 것까지)
    _png(media / "meta.png", text={"Source": "/" + "home/kim/shots/p1.png"})
    _gif(media / "comment.gif", comment=f"tok {FAKE_TOKEN}".encode())
    (media / "notes.md").write_text("x\n", encoding="utf-8")
    got = {(f.path.removeprefix("docs/media/"), f.rule) for f in cpo.stage_media(root)}
    assert ("big.gif", "media_size") in got
    assert ("meta.png", "media_metadata") in got
    assert ("comment.gif", "media_metadata") in got
    assert ("notes.md", "media_kind") in got
    assert ("MANIFEST.txt", "media_manifest") in got  # 새 파일 넷이 MANIFEST 에 없다


def test_media_manifest_fields_required(tmp_path: Path) -> None:
    root = clean_tree(tmp_path)
    man = root / "docs" / "media" / "MANIFEST.txt"
    man.write_text(MANIFEST_OK.replace("seed: api_world=1\n", "seed:\n"), encoding="utf-8")
    assert any(f.detail == "칸 'seed' 비었음" for f in cpo.stage_media(root))
    man.write_text(MANIFEST_OK + "file: gone.png | PNG\n", encoding="utf-8")
    assert any(f.detail == "없는 파일 gone.png" for f in cpo.stage_media(root))
    man.unlink()
    assert any(f.detail == "MANIFEST.txt 없음" for f in cpo.stage_media(root))


def test_media_png_over_1mb(tmp_path: Path) -> None:
    root = clean_tree(tmp_path)
    import os

    noise = os.urandom(700 * 700 * 3)  # 압축이 안 되는 잡음 — PNG 가 1MB 를 넘는다
    im = Image.frombytes("RGB", (700, 700), noise)
    im.save(root / "docs" / "media" / "kbj_demo_p1.png", format="PNG")
    assert ("media", "media_size") in rules_of(cpo.stage_media(root))


# ── ④ demo ──


def test_demo_dockerignore_must_be_allowlist_and_closed(tmp_path: Path) -> None:
    root = clean_tree(tmp_path)
    ig = root / cpo.DEMO_IGNORE
    ig.write_text(ig.read_text(encoding="utf-8") + "!.env\n!legacy/**\n", encoding="utf-8")
    got = {f.detail for f in cpo.stage_demo(root) if f.rule == "dockerignore_opens"}
    assert ".env" in got and "legacy/etf_traker/app.py" in got
    ig.write_text("kbj/__pycache__\n", encoding="utf-8")
    assert ("demo", "dockerignore_allowlist") in rules_of(cpo.stage_demo(root))


def test_dockerignore_matcher_semantics() -> None:
    rules, first = cpo.parse_dockerignore("# c\n*\n!kbj/**\n**/*.key\n!web/**\nweb/node_modules\n")
    assert first == "*"
    allows = lambda p: cpo.dockerignore_allows(rules, p)  # noqa: E731
    assert allows("kbj/core/x.py") and allows("web/src/main.ts")
    assert not allows("kbj/a.key") and not allows("web/node_modules/vite/index.js")
    assert not allows("README.md") and not allows("legacy/x.py")


def test_demo_compose_violations(tmp_path: Path) -> None:
    root = clean_tree(tmp_path)
    c = root / cpo.DEMO_COMPOSE
    text = c.read_text(encoding="utf-8")
    c.write_text(
        text.replace('"127.0.0.1:8765:8765"', '"8765:8765"').replace(
            "    read_only: true", "    env_file: .env\n    read_only: true"
        ),
        encoding="utf-8",
    )
    got = {f.detail for f in cpo.stage_demo(root) if f.rule == "compose_rule"}
    assert "services.demo: 포트가 127.0.0.1 에만 열리지 않는다" in got
    assert "services.demo: env_file 이 있다(.env 를 읽지 않는다)" in got


def test_demo_password_must_not_reach_ops_files(tmp_path: Path) -> None:
    root = clean_tree(tmp_path)
    (root / "docker-compose.yml").write_text(
        "services:\n  api:\n    environment:\n      X: " + cpo.DEMO_PASSWORD_DEFAULT + "\n",
        encoding="utf-8",
    )
    found = [f for f in cpo.stage_demo(root) if f.rule == "demo_account_in_ops"]
    assert [f.path for f in found] == ["docker-compose.yml"]


def test_cli_json_and_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = clean_tree(tmp_path)
    (root / "tests" / "q.csv").write_text("a\n", encoding="utf-8")
    assert cpo.main(["--root", str(root), "--skip-site", "--json"]) == 1
    rows = json.loads(capsys.readouterr().out)
    assert rows == [
        {
            "stage": "repo",
            "path": "tests/q.csv",
            "line": 0,
            "rule": "fixture_provenance",
            "detail": "",
        }
    ]
