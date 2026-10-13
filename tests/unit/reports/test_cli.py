"""`python -m kbj.reports.dart_excel` — legacy `run.py` CLI 와 같은 화면·같은 엑셀·같은 종료 코드.

legacy 표준출력(골든 G2 `pipeline/<종목>.json.gz` 의 `stdout`)과 줄 단위로 같다(임시 경로 자리표시만
바꾼다). 진단 두 개와 재무제표 없음(종료 코드 2)도 본다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kbj.data.public.dart.client import DartClient
from kbj.reports.dart_excel.__main__ import main
from tests.fixtures.synthetic.p4_world import build_world
from tests.golden.dart_report.golden import PIPELINE, WorldDart, read_json, world_now
from tests.oracles.xlsx_normalize import diff, normalize


def _client() -> DartClient:
    return WorldDart(build_world(), world_now()).client(DartClient)


@pytest.mark.parametrize("stock", ["994010", "994110"])
def test_화면과_엑셀이_legacy_와_같다(stock: str, tmp_path: Path) -> None:
    exp = read_json(PIPELINE / f"{stock}.json.gz")
    out_file = tmp_path / "r.xlsx"
    lines: list[str] = []
    rc = main(
        ["--stock", stock, "--years", "2023", "2026", "--out", str(out_file)],
        client=_client(), now=world_now(), out=lines.append,
    )  # fmt: skip
    assert rc == 0
    got = "\n".join(lines).replace(str(out_file), "<out>").splitlines()
    assert got == exp["stdout"]
    out: list[str] = []
    diff(stock, exp["workbook"], normalize(out_file.read_bytes()), out)
    assert not out, "\n".join(out)


def test_기본_출력_경로와_조용히(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    lines: list[str] = []
    rc = main(["--stock", "994110", "-q", "--no-orders"], client=_client(), now=world_now(),
              out=lines.append)  # fmt: skip
    assert rc == 0
    assert (tmp_path / "out" / "모의기계_994110_재무리포트.xlsx").is_file()
    assert lines[0].startswith("\n✔ 생성 완료: out/") and "[수주]" not in "\n".join(lines)


def test_재무제표가_없으면_종료_코드_2(tmp_path: Path) -> None:
    err: list[str] = []
    rc = main(["--stock", "994080", "--out", str(tmp_path / "x.xlsx")], client=_client(),
              now=world_now(), out=lambda s: None, err=err.append)  # fmt: skip
    assert rc == 2 and err[0].startswith("[오류] 모의테크 의 재무제표를 하나도 받지 못했습니다.")
    assert not (tmp_path / "x.xlsx").exists()


def test_진단_두_개() -> None:
    lines: list[str] = []
    rc = main(["--stock", "994110", "--years", "2025", "2025", "--diagnose-costs"],
              client=_client(), now=world_now(), out=lines.append)  # fmt: skip
    text = "\n".join(lines)
    assert rc == 0 and "=== 주석 진단: 4개 보고서 ===" in text and "미매핑 계정 없음." in text
    lines.clear()
    rc = main(["--stock", "994010", "--years", "2025", "2025", "--diagnose-bridge"],
              client=_client(), now=world_now(), out=lines.append)  # fmt: skip
    text = "\n".join(lines)
    assert rc == 0 and "=== 브릿지 진단: 2025 4Q" in text and "[파일]" in text
