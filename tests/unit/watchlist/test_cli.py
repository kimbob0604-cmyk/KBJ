"""`python -m kbj.services.watchlist` — 관심종목 가져오기·보기(docs/p4_design.md §1.8, D-P4-13).

본다: SD JSON(합성 세계의 파일 모양)·CSV 가져오기, 목록 통째 교체, 거절(형식·모르는 코드·중복)이
있으면 쓰지 않음(종료 코드 1)·`--skip-rejects`, `--dry-run`, 미국 티커 건너뜀, `--no-verify`, 빈
기준표·없는 파일·토큰 캐시 이름은 종료 코드 2, show. 실제 관심종목 파일은 쓰지 않는다(합성만 —
U-P4-5).
"""

from __future__ import annotations

import io
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import pytest

from kbj.core.fin_rows import WatchItem
from kbj.core.time import KST
from kbj.services.watchlist import LOADED_BY, plan_import, read_csv
from kbj.services.watchlist.__main__ import main
from kbj.store.repos import MemoryRepos, memory_repos
from tests.fixtures.synthetic.p4_world import CLOCK, build_world

NOW = datetime(2026, 11, 16, 22, 0, tzinfo=KST)


@pytest.fixture
def repos() -> MemoryRepos:
    w = build_world()
    r = memory_repos()
    r.market.upsert_universe(w.universe(), loaded_by="test")
    r.filings.corp_codes.update(
        {c.stock_code: (c.corp_code, c.corp_name) for c in w.corps if c.stock_code}
    )
    r.journal.replace_watchlist(
        "main", [WatchItem("main", "994150", "옛종목", None, 0, None, "cli")],
        loaded_by="test", now=CLOCK,
    )  # fmt: skip
    return r


def _run(args: Sequence[str], repos: MemoryRepos) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(args), repos=repos, now=lambda: NOW, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def _sd_file(tmp_path: Path, rows: list[dict[str, str]] | None = None) -> Path:
    p = tmp_path / "server_watchlist.json"
    p.write_text(json.dumps(rows if rows is not None else build_world().sd_watchlist_doc(),
                            ensure_ascii=False), encoding="utf-8")  # fmt: skip
    return p


def test_import_sd_json_replaces_list(tmp_path: Path, repos: MemoryRepos) -> None:
    doc = build_world().sd_watchlist_doc()
    kr = [d for d in doc if d.get("market", "kr") == "kr"]
    code, out, _ = _run(["import", "--from", "sd-json", str(_sd_file(tmp_path))], repos)
    assert code == 0, out
    items = repos.journal.watchlist("main")
    assert [i.code for i in items] == [d["code"] for d in kr]  # 파일 순서 = 정렬
    assert "994150" not in {i.code for i in items}  # 옛 목록은 통째로 바뀌었다
    assert all(i.source == "import:sd" for i in items)
    assert items[0].added_at is not None and items[0].added_at.utcoffset() is not None
    assert [i.sort for i in items] == list(range(len(items)))
    assert f"{len(kr)}개로 바꿨다" in out
    if len(kr) < len(doc):
        assert "미국 티커" in out
    row = repos.journal.watch_t.get(("main", items[0].code))
    assert row is not None


def test_rejects_block_the_write(tmp_path: Path, repos: MemoryRepos) -> None:
    rows = [
        {"code": "994010", "name": "모의전자", "market": "kr"},
        {"code": "12", "name": "앞자리 0 채움", "market": "kr"},  # → 000012(모르는 코드)
        {"code": "bad!", "name": "형식", "market": "kr"},
        {"code": "994010", "name": "중복", "market": "kr"},
        {"code": "AAPL", "name": "미국", "market": "us"},
    ]
    p = _sd_file(tmp_path, rows)
    code, out, _ = _run(["import", "--from", "sd-json", str(p)], repos)
    assert code == 1
    assert "000012 — unknown_code" in out and "bad! — bad_code" in out
    assert "994010 — duplicate" in out and "미국 티커 1개" in out
    assert [i.code for i in repos.journal.watchlist("main")] == ["994150"]  # 그대로
    code, out, _ = _run(["import", "--from", "sd-json", str(p), "--skip-rejects"], repos)
    assert code == 0
    assert [i.code for i in repos.journal.watchlist("main")] == ["994010"]


def test_dry_run_writes_nothing(tmp_path: Path, repos: MemoryRepos) -> None:
    code, out, _ = _run(["import", "--from", "sd-json", str(_sd_file(tmp_path)), "--dry-run"],
                        repos)  # fmt: skip
    assert code == 0 and "--dry-run" in out
    assert [i.code for i in repos.journal.watchlist("main")] == ["994150"]


def test_csv_import_with_header_and_padding(tmp_path: Path, repos: MemoryRepos) -> None:
    p = tmp_path / "watch.csv"
    p.write_text(
        "code,name,note,added_at\n"
        "994010,모의전자,핵심,2026-01-02T09:00:00+09:00\n"
        "# 주석 줄\n"
        "99K170,모의로보틱스,,\n"
        "\n",
        encoding="utf-8-sig",
    )
    code, out, _ = _run(["import", "--from", "csv", str(p), "--list", "swing"], repos)
    assert code == 0, out
    items = repos.journal.watchlist("swing")
    assert [(i.code, i.note, i.source) for i in items] == [
        ("994010", "핵심", "import:csv"),
        ("99K170", None, "import:csv"),
    ]
    assert items[0].added_at is not None
    assert [i.code for i in repos.journal.watchlist("main")] == ["994150"]  # 다른 목록은 그대로


def test_csv_zero_padding(tmp_path: Path) -> None:
    p = tmp_path / "w.csv"
    p.write_text("5930,삼성\n", encoding="utf-8")
    rows, rejects, _ = read_csv(p)
    assert [r.code for r in rows] == ["005930"] and rejects == []
    plan = plan_import(rows, rejects, list_name="main", source="import:csv", known={"005930"})
    assert plan.codes == ["005930"]


def test_no_verify_and_empty_reference(tmp_path: Path) -> None:
    empty = memory_repos()
    p = _sd_file(tmp_path, [{"code": "994010", "name": "x", "market": "kr"}])
    code, out, _ = _run(["import", "--from", "sd-json", str(p)], empty)
    assert code == 2 and "--no-verify" in out
    code, _out, _ = _run(["import", "--from", "sd-json", str(p), "--no-verify"], empty)
    assert code == 0
    assert [i.code for i in empty.journal.watchlist("main")] == ["994010"]


def test_file_errors_exit_2(tmp_path: Path, repos: MemoryRepos) -> None:
    code, _out, err = _run(["import", "--from", "sd-json", str(tmp_path / "없음.json")], repos)
    assert code == 2 and "실패" in err
    bad = tmp_path / "w.json"
    bad.write_text('{"code": "994010"}', encoding="utf-8")  # 목록이 아님
    assert _run(["import", "--from", "sd-json", str(bad)], repos)[0] == 2
    token = tmp_path / "kis_token_cache.json"
    token.write_text("[]", encoding="utf-8")
    code, _out, err = _run(["import", "--from", "sd-json", str(token)], repos)
    assert code == 2 and "토큰" in err
    code, _out, err = _run(["import", "--from", "csv", str(tmp_path / "w.csv"), "--list", "Bad"],
                           repos)  # fmt: skip
    assert code == 2


def test_empty_file_writes_nothing(tmp_path: Path, repos: MemoryRepos) -> None:
    code, out, _ = _run(["import", "--from", "sd-json", str(_sd_file(tmp_path, []))], repos)
    assert code == 1 and "넣을 종목이 없다" in out
    assert [i.code for i in repos.journal.watchlist("main")] == ["994150"]


def test_show(repos: MemoryRepos) -> None:
    code, out, _ = _run(["show"], repos)
    assert code == 0
    assert "목록 main: 1개" in out and "994150 옛종목 cli" in out


def test_loaded_by_is_cli(tmp_path: Path, repos: MemoryRepos) -> None:
    _run(["import", "--from", "sd-json", str(_sd_file(tmp_path))], repos)
    stored = next(iter(repos.journal.watch_t.rows.values()))
    assert stored.extra["loaded_by"] == LOADED_BY


def test_known_codes_use_kst_date(repos: MemoryRepos) -> None:
    """운영 시계는 UTC — 00:00~09:00 KST 에는 UTC 날짜가 하루 앞이라 그날(KST) 유니버스를 놓치면
    안 된다(오늘 상장한 코드가 '모르는 코드'로 거절되지 않게)."""
    from datetime import UTC, date

    from kbj.core.quality import Quality
    from kbj.core.rows import UniverseRow
    from kbj.services.watchlist import known_codes

    new = UniverseRow("994990", date(2026, 11, 17), "모의신규", "KOSDAQ", "common",
                      date(2026, 11, 17), "krx", Quality.OK)  # fmt: skip
    rows = [UniverseRow(u.code, date(2026, 11, 17), u.name, u.market, u.kind, u.listed_on,
                        u.source, u.quality) for u in repos.market.universe(date(2026, 11, 16))
            if u.source == "krx"]  # fmt: skip
    repos.market.upsert_universe([*rows, new], loaded_by="test")
    utc_now = datetime(2026, 11, 16, 15, 30, tzinfo=UTC)  # = 2026-11-17 00:30 KST
    assert "994990" in known_codes(repos, utc_now)
    assert "994990" not in known_codes(repos, datetime(2026, 11, 16, 14, 30, tzinfo=UTC))
