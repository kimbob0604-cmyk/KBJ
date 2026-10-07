"""scripts/check_public_safety.py — 합성 문자열로만 시험한다.

비밀값 모양의 문자열은 실행할 때 이어 붙여 만든다 — 이 파일 자체가 검사에 걸리지 않게.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import check_public_safety as cps
from kbj.core.masking import SECRET_SHAPES

ROOT = Path(__file__).resolve().parent.parent

SYNTH: dict[str, str] = {
    "telegram_bot_token": "TOKEN = '" + "7012345678" + ":AAF" + "q7Zx2" * 6 + "w9'",
    "anthropic_key": "key = '" + "sk-" + "ant-" + "api03-" + "x7Y" * 10 + "'",
    "github_token": "t = '" + "gh" + "p_" + "aB3dE" * 8 + "'",
    "aws_access_key_id": "id = " + "AK" + "IA" + "Q7Z2" * 4,
    "aws_secret_access_key": "aws_secret_" + "access_key = " + "Ab3/" * 10,
    "jwt": "auth " + ".".join(["eyJ" + "hbGci" * 3, "eyJ" + "zdWIi" * 3, "Sig_n" * 3]),
    "private_key_pem": "-" * 5 + "BEGIN " + "OPENSSH PRIVATE KEY" + "-" * 5,
    "kis_app_key": "appkey: " + "P" + "S" + "k7Qm2" * 6 + "Zx9a",
    "kis_app_secret": "secret " + "Qm9" * 60,
    "account_number": "계좌 " + "5012" + "3456-" + "01",
    "user_home_path": "path = '/" + "Users/" + "hong/" + "work/x.py'",
    "render_host": "url = 'https://" + "my-dash" + ".onrender" + ".com/api'",
}


def git_repo(tmp_path: Path, files: dict[str, str | bytes]) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)  # noqa: S603, S607
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
    return tmp_path


def rules(report: cps.Report) -> set[tuple[str, str]]:
    return {(f.path, f.rule) for f in report.findings}


def test_every_shape_has_a_description() -> None:
    assert set(SECRET_SHAPES) <= set(cps.RULES)


def test_synthetic_samples_cover_every_content_rule() -> None:
    content_rules = set(cps.RULES) - {
        "env_secret_value",
        "file_size",
        "data_extension",
        "secret_filename",
    }
    assert set(SYNTH) == content_rules


@pytest.mark.parametrize("rule", sorted(SYNTH))
def test_each_content_rule_fires(tmp_path: Path, rule: str) -> None:
    repo = git_repo(tmp_path, {"src/sample.py": f"x = 1\n{SYNTH[rule]}\n"})
    report = cps.scan(repo)
    assert [(f.path, f.line, f.rule) for f in report.findings] == [("src/sample.py", 2, rule)]


def test_clean_repo_passes(tmp_path: Path) -> None:
    repo = git_repo(
        tmp_path,
        {
            "README.md": "# 제목\n경로 /Users/<이름>/ 는 자리표시자다. 2026-10-06 KOSPI 2,612.35\n",
            "kbj/x.py": "TRADE_DATE = '20261006'\nRUN = '20261006-153000'\n",
            ".env.example": "# 주석 KBJ_X_KEY=값\nKBJ_KIS_APP_KEY=\nKBJ_KIS_ENV=real\n",
        },
    )
    report = cps.scan(repo)
    assert report.findings == []
    assert report.files == 3


def test_output_never_contains_the_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "P" + "S" + "k7Qm2" * 6 + "Zx9a"
    repo = git_repo(tmp_path, {"a.txt": f"key {secret}\n"})
    code = cps.main(["--root", str(repo), "--allow", str(repo / "없음.txt")])
    out = capsys.readouterr()
    assert code == 1
    assert "a.txt:1: kis_app_key" in out.out
    assert secret not in out.out + out.err
    assert secret[:6] not in out.out + out.err


class TestEnvFiles:
    def test_filled_secret_value_in_env_example(self, tmp_path: Path) -> None:
        repo = git_repo(
            tmp_path,
            {".env.example": "KBJ_KIS_ENV=real\nKBJ_DART_API_KEY=abc123\nKBJ_LIVE_TRADING=false\n"},
        )
        assert [(f.line, f.rule) for f in cps.scan(repo).findings] == [(2, "env_secret_value")]

    def test_dot_env_is_a_secret_file_unless_ignored(self, tmp_path: Path) -> None:
        repo = git_repo(tmp_path, {".env.local": "A=1\n", "cache/kis_token.json": "{}"})
        assert rules(cps.scan(repo)) >= {
            (".env.local", "secret_filename"),
            ("cache/kis_token.json", "secret_filename"),
        }
        (repo / ".gitignore").write_text(".env.*\ncache/\n", encoding="utf-8")
        assert rules(cps.scan(repo)) == set()


class TestFileRules:
    @pytest.mark.parametrize(
        "name", ["x.db", "x.sqlite", "x.sqlite3", "x.parquet", "x.XLSX", "x.duckdb"]
    )
    def test_data_extensions(self, tmp_path: Path, name: str) -> None:
        repo = git_repo(tmp_path, {f"d/{name}": b"\0\1\2"})
        assert rules(cps.scan(repo)) == {(f"d/{name}", "data_extension")}

    @pytest.mark.parametrize("name", ["id.key", "cert.pem", "kis.token.json", ".kis_token.json"])
    def test_secret_filenames(self, tmp_path: Path, name: str) -> None:
        repo = git_repo(tmp_path, {f"s/{name}": "x"})
        assert (f"s/{name}", "secret_filename") in rules(cps.scan(repo))

    def test_size_limit(self, tmp_path: Path) -> None:
        repo = git_repo(
            tmp_path,
            {"big.bin": b"\0" * (cps.MAX_BYTES + 1), "edge.txt": "a" * cps.MAX_BYTES},
        )
        assert rules(cps.scan(repo)) == {("big.bin", "file_size")}

    def test_binary_content_is_not_scanned(self, tmp_path: Path) -> None:
        payload = b"\0\0" + SYNTH["kis_app_key"].encode()
        repo = git_repo(tmp_path, {"img.png": payload})
        assert cps.scan(repo).findings == []

    def test_gitignored_files_are_skipped(self, tmp_path: Path) -> None:
        repo = git_repo(
            tmp_path,
            {".gitignore": "/state/\n*.db\n", "state/a.txt": SYNTH["jwt"], "b.db": "x"},
        )
        assert cps.scan(repo).findings == []


class TestAllowList:
    def test_entry_allows_only_named_rule_and_path(self, tmp_path: Path) -> None:
        repo = git_repo(
            tmp_path,
            {
                "docs/a.md": SYNTH["account_number"] + "\n" + SYNTH["render_host"] + "\n",
                "docs/b.md": SYNTH["account_number"] + "\n",
            },
        )
        allow = cps.parse_allow("docs/a.md | account_number | 날짜 형식 오탐(합성 시험)\n")
        report = cps.scan(repo, allow=allow)
        assert rules(report) == {("docs/a.md", "render_host"), ("docs/b.md", "account_number")}
        assert report.allowed == 1

    def test_glob_and_star_rule(self, tmp_path: Path) -> None:
        repo = git_repo(
            tmp_path, {"fixtures/synthetic/a.json": SYNTH["jwt"] + SYNTH["kis_app_key"]}
        )
        allow = cps.parse_allow("fixtures/synthetic/*.json | * | 합성 데이터 생성기 출력\n")
        report = cps.scan(repo, allow=allow)
        assert report.findings == [] and report.allowed == 2

    def test_allow_list_also_covers_file_rules(self, tmp_path: Path) -> None:
        repo = git_repo(tmp_path, {"tests/fixtures/sample.xlsx": b"PK\0\0"})
        allow = cps.parse_allow("tests/fixtures/sample.xlsx | data_extension | 합성 엑셀 골든\n")
        assert cps.scan(repo, allow=allow).findings == []

    @pytest.mark.parametrize(
        "line",
        [
            "docs/a.md | account_number",  # 칸 부족
            "docs/a.md | account_number |  ",  # 사유 없음
            "docs/a.md | account_number | 짧음",  # 사유가 너무 짧음(3자)
            "docs/a.md | no_such_rule | 사유가 있다",  # 모르는 규칙
            "/abs/a.md | account_number | 절대경로는 안 된다",
            "docs/a.md |  | 규칙이 비었다",
        ],
    )
    def test_malformed_entries_are_errors(self, line: str) -> None:
        with pytest.raises(cps.AllowListError):
            cps.parse_allow(line)

    def test_comments_and_blank_lines(self) -> None:
        assert cps.parse_allow("# 주석\n\n   \n") == []

    def test_bad_allow_file_exits_2(self, tmp_path: Path) -> None:
        repo = git_repo(tmp_path, {"a.txt": "ok\n"})
        bad = tmp_path / "allow.txt"
        bad.write_text("a.txt | account_number\n", encoding="utf-8")
        assert cps.main(["--root", str(repo), "--allow", str(bad)]) == 2

    def test_unused_entries_are_reported(self, tmp_path: Path) -> None:
        repo = git_repo(tmp_path, {"a.txt": "ok\n"})
        allow = cps.parse_allow("gone.txt | jwt | 지워진 파일의 옛 허용\n")
        assert [e.pattern for e in cps.scan(repo, allow=allow).unused_allow] == ["gone.txt"]


class TestExclude:
    def test_prefix_is_skipped(self, tmp_path: Path) -> None:
        repo = git_repo(
            tmp_path,
            {"legacy/x/a.py": SYNTH["jwt"], "legacyish.py": SYNTH["jwt"], "kbj/a.py": "ok"},
        )
        report = cps.scan(repo, exclude=["legacy"])
        assert rules(report) == {("legacyish.py", "jwt")}
        assert report.files == 2

    def test_cli_exit_codes(self, tmp_path: Path) -> None:
        repo = git_repo(tmp_path, {"legacy/a.py": SYNTH["jwt"]})
        none = str(tmp_path / "없음.txt")
        assert cps.main(["--root", str(repo), "--allow", none]) == 1
        assert cps.main(["--root", str(repo), "--allow", none, "--exclude", "legacy/"]) == 0


def test_repo_allow_list_is_well_formed() -> None:
    cps.load_allow(ROOT / "scripts" / "public_safety_allow.txt")
