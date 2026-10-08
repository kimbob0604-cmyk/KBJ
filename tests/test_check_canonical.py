"""scripts/check_canonical.py — 임시 git 트리에 위반을 심어 시험한다(docs/p2_design.md §9.8).

호스트 문자열은 조각을 이어 만든다 — 이 파일을 grep 하는 다른 검사가 걸리지 않게.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import check_canonical as cc

ROOT = Path(__file__).resolve().parent.parent

KIS_TOKEN_PATH = "/oauth2/" + "tokenP"
KIS_APPROVAL_PATH = "/oauth2/" + "Approval"
KIS_REST = "https://openapi" + ".koreainvestment" + ".com:9443"
KIS_MASTER = "https://new.real.download" + ".dws.co.kr/common/master/x.zip"
KRX_API = "https://data-dbg" + ".krx.co.kr/svc/apis/sto/stk_bydd_trd"
DART = "https://opendart" + ".fss.or.kr/api/list.json"
TELEGRAM = "https://api" + ".telegram.org/bot"
NAVER = "https://finance" + ".naver.com/item/main.naver"
DATAGO = "https://apis" + ".data.go.kr/1160100/service"

SAMPLES = {
    "kis_oauth": KIS_TOKEN_PATH,
    "kis_rest": KIS_REST,
    "kis_master": KIS_MASTER,
    "krx_api": KRX_API,
    "dart": DART,
    "telegram": TELEGRAM,
    "ecos": "https://ecos" + ".bok.or.kr/api/StatisticSearch",
    "datago": DATAGO,
    "kosis": "https://kosis" + ".kr/openapi/Param/statisticsParameterData.do",
    "kis_ws": "ws://ops" + ".koreainvestment.com:21000",
    "naver": NAVER,
    "etf_issuers": "https://www.samsung" + "fund.com/api/v1/fund/product/pdf",  # P3 묶음 S
    "krx_scrape": "http://data" + ".krx.co.kr/comm/bldAttendant/getJsonData.cmd",
}


def git_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)  # noqa: S603, S607
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


def run(
    root: Path, capsys: pytest.CaptureFixture[str], *args: str, baseline: str | None = None
) -> tuple[int, str]:
    base = root / "scripts" / "canonical_baseline.txt"
    if baseline is not None:
        base.parent.mkdir(parents=True, exist_ok=True)
        base.write_text(baseline, encoding="utf-8")
    code = cc.main(["--root", str(root), "--baseline", str(base), *args])
    out = capsys.readouterr()
    return code, out.out + out.err


# ── 그룹 패턴 ─────────────────────────────────────────────────────────────────────────────
def test_every_group_matches_its_sample_and_only_its_group() -> None:
    assert set(SAMPLES) == set(cc.GROUPS)
    for name, sample in SAMPLES.items():
        hit = {g.name for g in cc.GROUPS.values() if g.pattern.search(sample)}
        assert hit == {name}, (name, hit)
    assert cc.GROUPS["kis_oauth"].pattern.search(KIS_APPROVAL_PATH)
    assert cc.GROUPS["kis_rest"].pattern.search("https://openapivts" + ".koreainvestment.com:29443")
    assert cc.GROUPS["krx_scrape"].pattern.search("import py" + "krx")


def test_etf_issuer_hosts_all_nine_and_only_inside_the_adapter_package() -> None:
    """P3 묶음 S — 운용사 9곳 호스트는 kbj.data.private.etf_issuers 안에서만(나머지 kbj 는 실패)."""
    hosts = [
        "www.samsung" + "fund.com",
        "investments.mirae" + "asset.com",
        "time" + "etf.co.kr",
        "www.sol" + "etf.com",
        "papi.ace" + "etf.co.kr",
        "www.hanaro" + "etf.com",
        "www.samsung" + "active.co.kr",
        "www.plus" + "etf.co.kr",
        "www.rise" + "etf.co.kr",
    ]
    g = cc.GROUPS["etf_issuers"]
    assert all(g.pattern.search(f"https://{h}/x") for h in hosts)
    assert not g.pattern.search("https://www.samsung.com")  # 비슷한 이름은 아니다
    assert g.allowed == ("kbj/data/private/etf_issuers/*",) and not g.zero


def test_dart_needs_scheme_and_krx_scrape_skips_dbg_host() -> None:
    # 엑셀 출처 표기·도움말 문구(스킴 없음)는 걸리지 않는다(§9.1)
    assert not cc.GROUPS["dart"].pattern.search("출처: opendart" + ".fss.or.kr")
    # KRX OPEN API 호스트(data-dbg)는 krx_api 그룹이지 스크랩이 아니다
    assert not cc.GROUPS["krx_scrape"].pattern.search(KRX_API)


def test_script_itself_has_no_whole_host_strings() -> None:
    text = (ROOT / "scripts" / "check_canonical.py").read_text(encoding="utf-8")
    assert cc.scan_hosts("legacy/x.py", text, cc.GROUPS.values()) == []


# ── 목표 0 그룹 ───────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("group", ["kis_oauth", "kis_rest", "kis_master", "krx_api", "dart"])
def test_zero_group_in_legacy_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], group: str
) -> None:
    root = git_repo(tmp_path, {"legacy/sd/api.py": f"URL = '{SAMPLES[group]}'\n"})
    code, out = run(root, capsys)
    assert code == 1
    assert f"legacy/sd/api.py:1: {group} —" in out


def test_zero_group_cannot_be_baselined(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = git_repo(tmp_path, {"legacy/sd/kis.py": f"P = '{KIS_TOKEN_PATH}'\n"})
    code, out = run(root, capsys, baseline="kis_oauth | legacy/sd/kis.py | 1 | P3 — 나중에\n")
    assert code == 1
    assert "목표 0 그룹이라 기준선에 둘 수 없다" in out


def test_zero_group_outside_kbj_and_legacy_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = git_repo(tmp_path, {"scripts/probe.sh": f"curl {KRX_API}\n"})
    code, out = run(root, capsys)
    assert code == 1
    assert "scripts/probe.sh:1: krx_api" in out


# ── kbj 허용 위치 ─────────────────────────────────────────────────────────────────────────
def test_kbj_outside_allowed_location_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = git_repo(
        tmp_path,
        {
            "kbj/services/scheduler/x.py": f"T = '{KIS_TOKEN_PATH}'\n",
            "kbj/data/public/ecos/client.py": f"U = '{TELEGRAM}'\n",
            "kbj/engines/n.py": f"N = '{NAVER}'\n",
        },
    )
    code, out = run(root, capsys)
    assert code == 1
    assert "kbj/services/scheduler/x.py:1: kis_oauth" in out
    assert "kbj/data/public/ecos/client.py:1: telegram" in out
    assert "kbj/engines/n.py:1: naver" in out  # 네이버는 kbj 어디에도 금지(U4)


def test_kbj_inside_allowed_location_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = git_repo(
        tmp_path,
        {
            "kbj/services/auth/issuer.py": f"A = '{KIS_TOKEN_PATH}'\nB = '{KIS_APPROVAL_PATH}'\n",
            "kbj/data/private/kis/credentials.py": f"R = '{KIS_REST}'\n",
            "kbj/data/private/kis/master.py": f"M = '{KIS_MASTER}'\n",
            "kbj/data/private/krx/client.py": f"K = '{KRX_API}'\n",
            "kbj/data/public/dart/client.py": f"D = '{DART}'\n",
            "kbj/services/notifier/telegram_api.py": f"T = '{TELEGRAM}'\n",
            "kbj/data/datago.py": f"G = '{DATAGO}'\n",
            "kbj/data/private/fsc_stock_price/client.py": f"G = '{DATAGO}'\n",
        },
    )
    code, out = run(root, capsys)
    assert code == 0, out
    assert "통과" in out


def test_kbj_path_cannot_be_baselined(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = git_repo(tmp_path, {"kbj/engines/n.py": f"N = '{NAVER}'\n"})
    code, out = run(root, capsys, baseline="naver | kbj/engines/n.py | 1 | P3 — 나중에\n")
    assert code == 1
    assert "kbj/ 경로는 기준선에 둘 수 없다" in out


# ── 기준선 ───────────────────────────────────────────────────────────────────────────────
def _naver_lines(n: int) -> str:
    return "".join(f"U{i} = '{NAVER}'\n" for i in range(n))


def test_baseline_exact_count_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = git_repo(tmp_path, {"legacy/sd/news.py": _naver_lines(3)})
    code, out = run(root, capsys, baseline="naver | legacy/sd/news.py | 3 | P3 — KRX 로 교체\n")
    assert code == 0, out


def test_new_file_not_in_baseline_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = git_repo(
        tmp_path, {"legacy/sd/news.py": _naver_lines(1), "legacy/sd/new.py": _naver_lines(1)}
    )
    code, out = run(root, capsys, baseline="naver | legacy/sd/news.py | 1 | P3 — KRX 로 교체\n")
    assert code == 1
    assert "legacy/sd/new.py:1: naver" in out
    assert "기준선에 없는 파일" in out


def test_baseline_increase_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = git_repo(tmp_path, {"legacy/sd/news.py": _naver_lines(4)})
    code, out = run(root, capsys, baseline="naver | legacy/sd/news.py | 3 | P3 — KRX 로 교체\n")
    assert code == 1
    assert "기준선 3건보다 늘었다(지금 4건)" in out


def test_baseline_decrease_without_update_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = git_repo(tmp_path, {"legacy/sd/news.py": _naver_lines(1)})
    code, out = run(root, capsys, baseline="naver | legacy/sd/news.py | 3 | P3 — KRX 로 교체\n")
    assert code == 1
    assert "3건에서 1건으로 줄었다" in out


def test_baseline_entry_for_cleaned_file_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = git_repo(tmp_path, {"legacy/sd/news.py": "x = 1\n"})
    code, out = run(root, capsys, baseline="naver | legacy/sd/news.py | 2 | P3 — KRX 로 교체\n")
    assert code == 1
    assert "2건에서 0건으로 줄었다" in out


def test_update_baseline_only_shrinks(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = git_repo(tmp_path, {"legacy/sd/a.py": _naver_lines(1), "legacy/sd/b.py": "x = 1\n"})
    base = (
        "# 머리말은 남는다\n"
        "naver | legacy/sd/a.py | 3 | P3 — KRX 로 교체\n"
        "naver | legacy/sd/b.py | 2 | P3 — KRX 로 교체\n"
    )
    code, out = run(root, capsys, "--update-baseline", baseline=base)
    assert code == 0, out
    text = (root / "scripts" / "canonical_baseline.txt").read_text(encoding="utf-8")
    assert text == "# 머리말은 남는다\nnaver | legacy/sd/a.py | 1 | P3 — KRX 로 교체\n"
    code, out = run(root, capsys)
    assert code == 0, out


def test_update_baseline_refuses_to_grow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = git_repo(
        tmp_path, {"legacy/sd/a.py": _naver_lines(5), "legacy/sd/new.py": _naver_lines(1)}
    )
    base = "naver | legacy/sd/a.py | 3 | P3 — KRX 로 교체\n"
    code, out = run(root, capsys, "--update-baseline", baseline=base)
    assert code == 1
    assert "기준선 갱신 거부" in out
    text = (root / "scripts" / "canonical_baseline.txt").read_text(encoding="utf-8")
    assert text == base  # 그대로


@pytest.mark.parametrize(
    ("line", "msg"),
    [
        ("naver | legacy/a.py | 1 |", "사유가 없다"),
        ("naver | legacy/a.py | 1", "네 칸"),
        ("nope | legacy/a.py | 1 | P3 — 사유", "모르는 그룹"),
        ("naver | legacy/a.py | 0 | P3 — 사유", "1 이상"),
        ("naver | legacy/*.py | 1 | P3 — 사유", "패턴 금지"),
        ("naver | /abs/a.py | 1 | P3 — 사유", "상대 posix"),
    ],
)
def test_baseline_format_errors_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], line: str, msg: str
) -> None:
    root = git_repo(tmp_path, {"legacy/a.py": "x = 1\n"})
    code, out = run(root, capsys, baseline=line + "\n")
    assert code == 2
    assert msg in out


def test_duplicate_baseline_line_exit_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = git_repo(tmp_path, {"legacy/a.py": _naver_lines(1)})
    line = "naver | legacy/a.py | 1 | P3 — 사유\n"
    code, out = run(root, capsys, baseline=line + line)
    assert code == 2
    assert "두 번" in out


# ── 범위 ────────────────────────────────────────────────────────────────────────────────
def test_tests_docs_and_fixtures_are_excluded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = git_repo(
        tmp_path,
        {
            "legacy/et/board/tests/test_kis.py": f"ERR = '{KIS_REST} 500'\n",
            "legacy/gx/tests/fakes/kis_server.py": f"P = '{KIS_TOKEN_PATH}'\n",
            "legacy/sd/test_dart.py": f"D = '{DART}'\n",
            "legacy/et/dart-report/tests_smoke.py": f"D = '{DART}'\n",
            "tests/fakes/kis_server.py": f"P = '{KIS_TOKEN_PATH}'\n",
            "tests/fixtures/x.yaml": f"u: {KRX_API}\n",
            "docs/notes.txt": f"{KIS_REST}\n",  # 확장자 밖
            "legacy/sd/README.md": f"{KIS_REST}\n",
            "legacy/sd/data.json": f'{{"u": "{KIS_REST}"}}\n',  # 확장자 밖
        },
    )
    code, out = run(root, capsys)
    assert code == 0, out


def test_untracked_but_not_ignored_files_are_scanned(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = git_repo(
        tmp_path,
        {".gitignore": "ignored/\n", "ignored/a.py": f"{DART}\n", "legacy/x.sh": f"curl {DART}\n"},
    )
    code, out = run(root, capsys)
    assert code == 1
    assert "legacy/x.sh:1: dart" in out
    assert "ignored/a.py" not in out


def test_output_never_prints_matched_string(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secretish = KIS_REST + "/uapi?appkey=" + "PS" + "k7Qm2" * 6
    root = git_repo(
        tmp_path,
        {
            "legacy/sd/kis.py": f"URL = '{secretish}'\n",
            "legacy/sd/news.py": _naver_lines(2),
        },
    )
    code, out = run(root, capsys)
    assert code == 1
    for s in (secretish, "koreainvestment", "naver.com", "appkey"):
        assert s not in out


def test_groups_option_limits_scope(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = git_repo(
        tmp_path, {"legacy/sd/news.py": _naver_lines(1), "legacy/sd/k.py": f"{KIS_REST}\n"}
    )
    code, out = run(root, capsys, "--groups", "naver")
    assert code == 1
    assert "kis_rest" not in out
    code, out = run(root, capsys, "--groups", "dart,krx_api")
    assert code == 0, out
    code, out = run(root, capsys, "--groups", "dart,nope")
    assert code == 2


def test_list_option(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = git_repo(tmp_path, {"a.py": "x = 1\n"})
    code, out = run(root, capsys, "--list")
    assert code == 0
    for name in [*cc.GROUPS, *cc.AST_RULES]:
        assert name in out


# ── AST 규칙 ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("source", "rule"),
    [
        ("from kbj.services.auth.issuer import KisTokenIssuer\n", "legacy_auth_import"),
        ("import kbj.services.auth\n", "legacy_auth_import"),
        ("from kbj.services import auth\n", "legacy_auth_import"),
        (
            "import importlib\nimportlib.import_module('kbj.services.auth.issuer')\n",
            "legacy_auth_import",
        ),
        ("from kbj.data.private.kis.credentials import _REAL_BASE\n", "legacy_private_name"),
        ("from kbj.data.private.krx import client\nU = client._KRX_BASE\n", "legacy_private_name"),
        (
            "import kbj.data.private.kis.credentials\nU = kbj.data.private.kis.credentials._X\n",
            "legacy_private_name",
        ),
        ("from kbj.data.private.kis._impl import x\n", "legacy_private_name"),
        (
            "from kbj.data.private.kis import credentials as c\nU = getattr(c, '_REAL_BASE')\n",
            "legacy_private_name",
        ),
        (
            "import kbj.data.private.krx.client\n"
            "U = getattr(kbj.data.private.krx.client, '_KRX_BASE', '')\n",
            "legacy_private_name",
        ),
        ("from kbj.services.scheduler.runner import JobRunner\n", "legacy_kbj_module"),
        ("from kbj.data.private import kis\n", "legacy_kbj_module"),
        ("import kbj.data.private.kis.errors\n", "legacy_kbj_module"),
        ("from kbj.services.notifier.telegram_api import TelegramApi\n", "legacy_kbj_module"),
    ],
)
def test_ast_rules_catch_bypass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], source: str, rule: str
) -> None:
    root = git_repo(tmp_path, {"legacy/sd/mod.py": source})
    code, out = run(root, capsys)
    assert code == 1, out
    assert "legacy/sd/mod.py:" in out and f": {rule} —" in out


@pytest.mark.parametrize(
    "source",
    [
        "from kbj.data import legacy_bridge as requests\n",
        "from kbj.data.legacy_bridge import session\n",
        "from kbj.services.notifier.client import legacy_send, webhook_status\n",
        "from kbj.core.calendar import *\n",
        "from kbj.core.time import now_kst as _kbj_now_kst\n",  # 별칭이 _ 인 것은 괜찮다
        "from kbj.data.private.kis.token import reader as _kbj_reader\n",
        "import kbj.data.private.kis.master\n",
        "from kbj.data.public.dart.client import DartClient\n",
        "from kbj.services.runtime import tagger_for\n",
        "from kbj.config.settings import Settings\n",
        "from kbj.store.spool import DiskSpool\n",
        "import kbj.core.calendar\nx = kbj.core.calendar.__name__\n",  # dunder 는 공개
        "from . import _local\nfrom ..pkg import _x\n",  # 상대 import 는 kbj 가 아니다
    ],
)
def test_ast_rules_allow_public_api(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], source: str
) -> None:
    root = git_repo(tmp_path, {"legacy/sd/mod.py": source})
    code, out = run(root, capsys)
    assert code == 0, out


def test_unparsable_legacy_file_fails_instead_of_being_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """파싱하지 못한 파일은 import 규칙을 볼 수 없다 — 통과로 두지 않는다(절대 규칙 4)."""
    root = git_repo(tmp_path, {"legacy/sd/broken.py": "x = 1\ndef f(:\n    pass\n"})
    code, out = run(root, capsys)
    assert code == 1, out
    assert "legacy/sd/broken.py:2: legacy_unparsable —" in out
    code, out = run(root, capsys, "--groups", "kis_oauth")  # 호스트 그룹만 고르면 AST 는 안 본다
    assert code == 0, out


def test_getattr_with_public_name_is_allowed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = (
        "from kbj.data import legacy_bridge as rq\n"
        "get = getattr(rq, 'get')\n"
        "_cache = {}\nv = getattr(_cache, '_x', None)\n"  # kbj 에 묶이지 않은 이름은 상관없다
    )
    root = git_repo(tmp_path, {"legacy/sd/mod.py": src})
    code, out = run(root, capsys)
    assert code == 0, out


def test_ast_rules_skip_legacy_tests_and_kbj(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = git_repo(
        tmp_path,
        {
            "legacy/gx/tests/unit/test_x.py": (
                "from kbj.services.auth.issuer import KisTokenIssuer\n"
            ),
            "kbj/services/scheduler/x.py": "from kbj.data.private.kis._impl import y\n",
        },
    )
    code, out = run(root, capsys)
    assert code == 0, out  # kbj 안은 import-linter 계약 ④~⑦ 몫


# ── 실제 레포 ─────────────────────────────────────────────────────────────────────────────
def test_real_baseline_parses_and_has_no_zero_group_or_kbj_path() -> None:
    base = cc.load_baseline(ROOT / "scripts" / "canonical_baseline.txt")
    assert base.entries
    for e in base.entries.values():
        assert not cc.GROUPS[e.group].zero
        assert cc.area(e.path) != "kbj"


def test_real_repo_has_no_direct_kis_krx_dart_calls(capsys: pytest.CaptureFixture[str]) -> None:
    """PLAN §8 P2 완료 기준: legacy 의 KIS·KRX·DART 직접 호출 0(기준선 없이)."""
    code = cc.main(["--groups", "kis_oauth,kis_rest,kis_master,krx_api,dart"])
    out = capsys.readouterr().out
    assert code == 0, out


def test_real_repo_passes_with_baseline(capsys: pytest.CaptureFixture[str]) -> None:
    code = cc.main([])
    out = capsys.readouterr().out
    assert code == 0, out
