"""export_all·write_files·처리기 run — 시계·연결·디렉터리를 주입한다(DB·네트워크 없음).

핵심 단언: 공개 산출물(모든 파일 글자)에 로그인 등급 출처 이름·로그인 API 주소가 0 이다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import httpx
import psycopg
import pytest
from pydantic import SecretStr

from kbj.config.fin import load_fin, load_reports
from kbj.config.settings import Settings
from kbj.core.calendar import TradingCalendar
from kbj.core.time import KST
from kbj.services.public_export import export as ex
from kbj.services.public_export.corp_index import NAME as CORP_INDEX_NAME
from kbj.services.public_export.filings_json import NAMES as FILINGS_NAMES
from kbj.services.public_export.manifest import (
    DART_ONLY_NAMES,
    LOGIN_SOURCES,
    MANIFEST_NAME,
    BlobFile,
    BuildOut,
    PublicFile,
    check_tree,
    dir_digest,
    forbidden_hits,
    listed_paths,
    sha256_bytes,
)
from kbj.services.public_export.push import push_public_data
from kbj.services.scheduler.handlers import JobContext
from tests.unit.public_export.pub_rows import (
    CODES,
    ELEC,
    EVENTS,
    P4_MANIFEST,
    REPORT_CODE,
    WEB_PUBLIC,
    XLSX,
    reader,
    web_fixture_files,
)
from tests.unit.public_export.pub_rows import NOW as P4_NOW

NOW = datetime(2026, 10, 7, 5, 30, tzinfo=KST)


@pytest.fixture(scope="module")
def cal() -> TradingCalendar:
    return TradingCalendar.default()


class FakeCursor:
    def __init__(self, row: tuple[object, object] | None, fail: bool = False) -> None:
        self.row = row
        self.fail = fail
        self.sql: list[str] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, params: object = None) -> None:
        self.sql.append(sql)
        if self.fail:
            raise psycopg.errors.InsufficientPrivilege("permission denied")

    def fetchone(self) -> tuple[object, object] | None:
        return self.row


class FakeConn:
    def __init__(
        self, row: tuple[object, object] | None = ("on", True), fail: bool = False
    ) -> None:
        self.cur = FakeCursor(row, fail)
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self.cur

    def __enter__(self) -> FakeConn:
        return self

    def __exit__(self, *_: object) -> None:
        self.closed = True


def _conn(c: FakeConn) -> psycopg.Connection[Any]:
    return cast("psycopg.Connection[Any]", c)


def _all_text(d: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(d.glob("*.json"))}


def test_export_all_writes_envelopes_and_manifest(tmp_path: Path, cal: TradingCalendar) -> None:
    m = ex.export_all(None, tmp_path, now=NOW, cal=cal)
    assert m.names == ("calendar.json", "events.json")
    files = _all_text(tmp_path)
    assert set(files) == {"calendar.json", "events.json", MANIFEST_NAME}
    man = json.loads(files[MANIFEST_NAME])
    # 프런트·merge-public-data.mjs 와 맞춘 모양(W 요청)
    assert man["schema_version"] == 1
    assert man["generated_at"] == "2026-10-07T05:30:00+09:00"
    assert [e["name"] for e in man["files"]] == ["calendar.json", "events.json"]
    for e in man["files"]:
        env = json.loads(files[e["name"]])
        assert {"source", "as_of", "quality", "notes", "generated_at", "data"} <= set(env)
        assert (env["source"], env["as_of"], env["quality"]) == (
            e["source"],
            e["as_of"],
            e["quality"],
        )
        assert env["as_of"].endswith("+09:00")
    assert check_tree(tmp_path) == []


def test_public_output_has_zero_login_sources_and_api_paths(
    tmp_path: Path, cal: TradingCalendar
) -> None:
    """공개 산출물 전체 글자에 로그인 등급 출처 이름(어느 키든)·/api 등 로그인 흔적이 0."""
    ex.export_all(None, tmp_path, now=NOW, cal=cal)
    for name, text in _all_text(tmp_path).items():
        assert forbidden_hits(text) == [], name
        assert "/api" not in text and "/telegram" not in text, name
        # source 키만이 아니라 글자 전체(notes·title 포함)에도 로그인 등급 출처 이름이 없다
        assert LOGIN_SOURCES.search(text) is None, name


def test_rerun_is_deterministic_and_removes_stale(tmp_path: Path, cal: TradingCalendar) -> None:
    ex.export_all(None, tmp_path, now=NOW, cal=cal)
    first = _all_text(tmp_path)
    (tmp_path / "old_widget.json").write_text("{}", encoding="utf-8")
    (tmp_path / "README.txt").write_text("keep", encoding="utf-8")
    ex.export_all(None, tmp_path, now=NOW, cal=cal)
    assert _all_text(tmp_path) == first  # 같은 시각 → 같은 글자, 옛 json 은 지워짐
    assert (tmp_path / "README.txt").exists()  # 우리 형식이 아닌 파일은 건드리지 않는다
    assert not list(tmp_path.glob(".*.part"))


def test_write_files_refuses_bad_payload_without_touching_dir(tmp_path: Path) -> None:
    good = PublicFile(name="a.json", source="DART", as_of=NOW, quality="ok", data={})
    ex.write_files([good], tmp_path, now=NOW)
    before = _all_text(tmp_path)
    bad = PublicFile(name="b.json", source="KRX 일별", as_of=NOW, quality="ok", data={})
    with pytest.raises(ex.PublicExportError, match="검사 실패"):
        ex.write_files([good, bad], tmp_path, now=NOW)
    leak = PublicFile(name="c.json", source="DART", as_of=NOW, quality="ok", data={"u": "/api/x"})
    with pytest.raises(ex.PublicExportError):
        ex.write_files([leak], tmp_path, now=NOW)
    dup = [good, good]
    with pytest.raises(ex.PublicExportError, match="두 번"):
        ex.write_files(dup, tmp_path, now=NOW)
    assert _all_text(tmp_path) == before


def test_export_all_rejects_naive_now(tmp_path: Path, cal: TradingCalendar) -> None:
    with pytest.raises(ValueError):
        ex.export_all(None, tmp_path, now=datetime(2026, 10, 7), cal=cal)  # noqa: DTZ001


@pytest.mark.parametrize(
    ("row", "fail", "match"),
    [
        (("off", True), False, "읽기 전용"),
        (("on", False), False, "구성원"),
        (None, False, "결과 없음"),
        (("on", True), True, "InsufficientPrivilege"),
    ],
)
def test_connection_check_refuses_non_export_role(
    tmp_path: Path, cal: TradingCalendar, row: tuple[object, object] | None, fail: bool, match: str
) -> None:
    conn = FakeConn(row, fail)
    with pytest.raises(ex.PublicExportError, match=match):
        ex.export_all(_conn(conn), tmp_path, now=NOW, cal=cal)
    assert not list(tmp_path.glob("*.json"))  # 확인 실패면 아무것도 쓰지 않는다


def test_connection_check_sql_names_no_schema(tmp_path: Path, cal: TradingCalendar) -> None:
    conn = FakeConn()
    ex.export_all(_conn(conn), tmp_path, now=NOW, cal=cal)
    assert conn.cur.sql == [ex._CHECK_SQL]  # pyright: ignore[reportPrivateUsage]
    assert "prv_" not in conn.cur.sql[0] and "ops." not in conn.cur.sql[0]


def test_default_connect_never_uses_app_dsn() -> None:
    s = Settings(_env_file=None, database_url=SecretStr("postgresql://app@db/kbj"))  # pyright: ignore[reportCallIssue]
    assert s.public_export_database_url is None
    assert ex.default_connect(s) is None


def test_default_connect_bad_dsn_names_export_variable() -> None:
    """형식 오류 문구는 앱 DSN 이름이 아니라 공개 내보내기 변수 이름으로 나온다(값 없음)."""
    s = Settings(_env_file=None, public_export_database_url=SecretStr("not a dsn ::: pw-SECRET"))  # pyright: ignore[reportCallIssue]
    fn = ex.default_connect(s)
    assert fn is not None
    with pytest.raises(ex.PublicExportError) as ei:
        fn()
    msg = str(ei.value)
    assert "KBJ_PUBLIC_EXPORT_DATABASE_URL" in msg
    assert "pw-SECRET" not in msg


# ── 처리기 run ────────────────────────────────────────────────────────────────────────────


def _ctx(tmp_path: Path, settings: Settings, **resources: Any) -> JobContext:
    return JobContext(
        job="public.export",
        as_of="2026-10-07",
        run_id="r1",
        attempt=1,
        now=NOW,
        settings=settings,
        resources={"public_out_dir": tmp_path / "public", **resources},
    )


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, data_dir=Path("unused"), **kw)  # pyright: ignore[reportCallIssue]


def test_run_ok_push_disabled_by_default(tmp_path: Path, cal: TradingCalendar) -> None:
    conns: list[FakeConn] = []

    @contextmanager
    def connect() -> Iterator[FakeConn]:
        c = FakeConn()
        conns.append(c)
        yield c

    res = ex.run(_ctx(tmp_path, _settings(), public_connect=connect, kr=cal))
    assert res.status == "ok", res.detail
    assert res.rows == 2
    assert res.detail["push"] == "disabled"
    assert res.detail["files"] == ["calendar.json", "events.json"]
    assert res.detail["quality"]["calendar.json"] == "ok"
    assert len(conns) == 1
    assert check_tree(tmp_path / "public") == []


def test_run_without_export_dsn_fails_loudly(tmp_path: Path, cal: TradingCalendar) -> None:
    s = _settings(database_url=SecretStr("postgresql://app:pw-SECRET@db/kbj"))
    res = ex.run(_ctx(tmp_path, s, kr=cal))
    assert res.status == "failed"
    assert "앱 DSN 으로 대신하지 않는다" in res.detail["reason"]
    assert not (tmp_path / "public").exists()


def test_run_masks_secret_in_failure(tmp_path: Path, cal: TradingCalendar) -> None:
    secret = "pw-SECRET-123456"
    s = _settings(public_export_database_url=SecretStr(f"postgresql://exp:{secret}@db/kbj"))

    def boom() -> FakeConn:
        raise RuntimeError(f"cannot connect postgresql://exp:{secret}@db/kbj")

    res = ex.run(_ctx(tmp_path, s, public_connect=boom, kr=cal))
    assert res.status == "failed"
    assert secret not in json.dumps(dict(res.detail), ensure_ascii=False)


def test_run_without_settings_fails(tmp_path: Path) -> None:
    ctx = JobContext(job="public.export", as_of="d", run_id="r", attempt=1, now=NOW)
    assert ex.run(ctx).status == "failed"


def test_run_push_enabled_without_approved_secrets_fails(
    tmp_path: Path, cal: TradingCalendar
) -> None:
    @contextmanager
    def connect() -> Iterator[FakeConn]:
        yield FakeConn()

    res = ex.run(
        _ctx(tmp_path, _settings(public_push_enabled=True), public_connect=connect, kr=cal)
    )
    assert res.status == "failed"
    assert "사용자 승인 필요" in res.detail["reason"]
    # 파일은 만들어졌다(푸시만 못 함)
    assert check_tree(tmp_path / "public") == []


@pytest.mark.skipif(shutil.which("git") is None, reason="git 이 없다")
def test_run_push_and_dispatch_when_approved(tmp_path: Path, cal: TradingCalendar) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)  # noqa: S603, S607
    key = tmp_path / "deploy.key"
    key.write_text("not a real key", encoding="utf-8")
    sent: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(req)
        return httpx.Response(204)

    @contextmanager
    def connect() -> Iterator[FakeConn]:
        yield FakeConn()

    s = _settings(
        public_push_enabled=True,
        public_deploy_key_path=key,
        github_dispatch_token=SecretStr("tok-SECRET"),
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        res = ex.run(
            _ctx(
                tmp_path,
                s,
                public_connect=connect,
                kr=cal,
                public_remote=str(remote),
                dispatch_client=client,
            )
        )
    assert res.status == "ok", res.detail
    assert (res.detail["push"], res.detail["dispatch"]) == ("pushed", "sent")
    assert len(sent) == 1
    assert "tok-SECRET" not in json.dumps(dict(res.detail))


# ── P4 — 공시·재무 파일(합성 공개 표 메모리 읽기 — docs/p4_design.md §1.9·§7) ─────────────────────


def _p4(tmp_path: Path, cal: TradingCalendar, **kw: Any) -> ex.Manifest:
    rcfg = load_reports().model_copy(update={"public_demo": (REPORT_CODE,)})
    return ex.export_all(
        None, tmp_path, now=P4_NOW, cal=cal, events=EVENTS, reader=reader(), reports_cfg=rcfg, **kw
    )


def _tree(d: Path) -> dict[str, bytes]:
    return {
        p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()
    }


def test_p4_export_writes_lists_index_and_subdirs(tmp_path: Path, cal: TradingCalendar) -> None:
    m = _p4(tmp_path, cal)
    assert m.names == (
        "calendar.json",
        "events.json",
        *FILINGS_NAMES,
        CORP_INDEX_NAME,
    )
    assert [(d.name, d.count) for d in m.dirs] == [("fin", 4), ("reports", 2)]
    assert m.skipped == ()
    assert check_tree(tmp_path) == []
    man = json.loads((tmp_path / MANIFEST_NAME).read_text(encoding="utf-8"))
    # 하위 폴더 파일은 manifest files 에 하나씩 적지 않는다 — 폴더 한 항목(개수·크기·요약 해시)
    assert all("/" not in e["name"] for e in man["files"])
    fin = next(d for d in man["dirs"] if d["name"] == "fin")
    files = sorted((tmp_path / "fin").iterdir())
    assert fin["count"] == len(files) == 4
    assert fin["bytes"] == sum(p.stat().st_size for p in files)
    assert fin["digest"] == dir_digest(
        (f"fin/{p.name}", sha256_bytes(p.read_bytes())) for p in files
    )
    assert fin["source"] == "DART"
    for p in files:
        env = json.loads(p.read_text(encoding="utf-8"))
        assert env["source"] == "DART" and env["as_of"].endswith("+09:00")  # 파일 자체가 봉투
        assert "\n " not in p.read_text(encoding="utf-8")  # 공백 없는 JSON
    assert (tmp_path / "reports" / f"{REPORT_CODE}.xlsx").read_bytes() == XLSX


def test_p4_output_has_zero_login_traces(tmp_path: Path, cal: TradingCalendar) -> None:
    _p4(tmp_path, cal)
    for rel, body in _tree(tmp_path).items():
        if rel.endswith(".xlsx"):
            continue
        text = body.decode("utf-8")
        assert forbidden_hits(text) == [], rel
        assert LOGIN_SOURCES.search(text) is None, rel
        assert "prv_" not in text and "is_watch" not in text, rel


def test_p4_rerun_deterministic_and_stale_subdir_files_removed(
    tmp_path: Path, cal: TradingCalendar
) -> None:
    _p4(tmp_path, cal)
    first = _tree(tmp_path)
    (tmp_path / "fin" / "Q99999.json").write_text("{}", encoding="utf-8")
    (tmp_path / "reports" / "Q99999.xlsx").write_bytes(b"old")
    _p4(tmp_path, cal)
    assert _tree(tmp_path) == first


def test_interrupted_part_files_in_subdirs_are_cleared(
    tmp_path: Path, cal: TradingCalendar
) -> None:
    """쓰다 끊긴 임시 파일(`.<이름>.part`)이 하위 폴더에 남아도 다음 실행이 지운다 — 남으면
    check_tree('허용하지 않는 이름')가 푸시를 계속 막는다."""
    _p4(tmp_path, cal)
    first = _tree(tmp_path)
    (tmp_path / "fin" / ".Q00001.json.part").write_text("{", encoding="utf-8")
    (tmp_path / "reports" / ".Q00001.xlsx.part").write_bytes(b"PK")
    assert any("허용하지 않는 이름" in p for p in check_tree(tmp_path))
    _p4(tmp_path, cal)
    assert _tree(tmp_path) == first
    assert check_tree(tmp_path) == []


def test_no_db_skips_p4_files_with_reason(tmp_path: Path, cal: TradingCalendar) -> None:
    m = ex.export_all(None, tmp_path, now=NOW, cal=cal)
    assert m.dirs == () and m.skipped == (ex.NO_DB_SKIP,)
    assert not (tmp_path / "fin").exists()
    man = json.loads((tmp_path / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert man["dirs"] == []


def test_stub_connection_reads_nothing_and_says_so(tmp_path: Path, cal: TradingCalendar) -> None:
    conn = FakeConn()
    m = ex.export_all(_conn(conn), tmp_path, now=NOW, cal=cal)
    assert m.skipped == (ex.NOT_PG_SKIP,)
    assert conn.cur.sql == [ex._CHECK_SQL]  # pyright: ignore[reportPrivateUsage]


def test_previous_p4_tree_is_cleared_when_p4_not_built(
    tmp_path: Path, cal: TradingCalendar
) -> None:
    _p4(tmp_path, cal)
    ex.export_all(None, tmp_path, now=NOW, cal=cal)
    assert not (tmp_path / "fin").exists() and not (tmp_path / "reports").exists()
    assert not (tmp_path / "filings_feed.json").exists()
    assert check_tree(tmp_path) == []


def test_fin_size_cap_fails_loudly_and_writes_nothing(tmp_path: Path, cal: TradingCalendar) -> None:
    ex.export_all(None, tmp_path, now=NOW, cal=cal)
    before = _tree(tmp_path)
    tiny = load_fin()
    tiny = tiny.model_copy(
        update={"public_export": tiny.public_export.model_copy(update={"fin_max_total_mb": 0.001})}
    )
    with pytest.raises(ex.PublicExportError, match="상한") as ei:
        _p4(tmp_path, cal, fin_cfg=tiny)
    assert "자르지 않는다" in str(ei.value)
    assert _tree(tmp_path) == before  # 아무것도 바꾸지 않았다


def test_blob_needs_matching_pair_json(tmp_path: Path) -> None:
    f = PublicFile(
        name=f"reports/{REPORT_CODE}.json",
        source="DART",
        as_of=NOW,
        quality="ok",
        data={"xlsx_sha256": "0" * 64},
    )
    blob = BlobFile(f"reports/{REPORT_CODE}.xlsx", XLSX)
    with pytest.raises(ex.PublicExportError, match="xlsx_sha256"):
        ex.write_files([f], tmp_path, now=NOW, blobs=[blob])
    with pytest.raises(ex.PublicExportError, match="짝 JSON"):
        ex.write_files([], tmp_path, now=NOW, blobs=[blob])
    assert not list(tmp_path.rglob("*"))


def test_nan_is_refused_not_written(tmp_path: Path) -> None:
    bad = PublicFile(
        name="fin/Q00001.json", source="DART", as_of=NOW, quality="ok", data={"opm": float("nan")}
    )
    with pytest.raises(ex.PublicExportError, match="JSON 으로 쓸 수 없는 값"):
        ex.write_files([bad], tmp_path, now=NOW)


def test_non_dart_source_in_p4_file_is_refused(tmp_path: Path) -> None:
    bad = PublicFile(
        name="fin/Q00001.json",
        source="DART",
        as_of=NOW,
        quality="ok",
        data={"rows": [{"source": "공시 요약"}]},
    )
    with pytest.raises(ex.PublicExportError, match="DART 가 아님"):
        ex.write_files([bad], tmp_path, now=NOW)


def test_run_with_injected_reader_exports_p4(tmp_path: Path, cal: TradingCalendar) -> None:
    @contextmanager
    def connect() -> Iterator[FakeConn]:
        yield FakeConn()

    ctx = JobContext(
        job="public.export",
        as_of="2026-11-16",
        run_id="r1",
        attempt=1,
        now=P4_NOW,
        settings=_settings(),
        resources={
            "public_out_dir": tmp_path / "public",
            "public_connect": connect,
            "kr": cal,
            "public_reader": reader(),
        },
    )
    res = ex.run(ctx)
    assert res.status == "ok", res.detail
    assert res.detail["dirs"] == {"fin": 4}  # 공개 데모는 설정(reports.yaml — 비어 있음)
    assert CORP_INDEX_NAME in res.detail["files"]
    assert res.rows == len(res.detail["files"]) + 4
    assert check_tree(tmp_path / "public") == []


def test_run_detail_lists_skip_reason(tmp_path: Path, cal: TradingCalendar) -> None:
    @contextmanager
    def connect() -> Iterator[FakeConn]:
        yield FakeConn()

    res = ex.run(_ctx(tmp_path, _settings(), public_connect=connect, kr=cal))
    assert res.status == "ok"
    assert res.detail["skipped"] == [ex.NOT_PG_SKIP]


@pytest.mark.skipif(shutil.which("git") is None, reason="git 이 없다")
def test_push_carries_subdir_files(tmp_path: Path, cal: TradingCalendar) -> None:
    out = tmp_path / "out"
    _p4(out, cal)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)  # noqa: S603, S607
    res = push_public_data(out, enabled=True, remote=str(remote), now=P4_NOW)
    assert res.pushed
    ls = subprocess.run(  # noqa: S603
        ["git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "public-data"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert sorted(ls) == sorted([*listed_paths(out), MANIFEST_NAME])
    assert f"fin/{CODES[ELEC]}.json" in ls and f"reports/{REPORT_CODE}.xlsx" in ls


def test_web_fixtures_are_fresh(tmp_path: Path) -> None:
    """`web/test/fixtures/public` 의 P4 파일이 지금 코드의 출력과 같다(`python -m
    tests.unit.public_export.pub_rows` 로 다시 만든다). manifest_p4 의 P3 항목(캘린더 — 거래소 달력
    판에 따라 바뀐다)은 빼고 본다."""
    want = web_fixture_files(tmp_path)
    for rel, body in want.items():
        path = WEB_PUBLIC / rel
        assert path.exists(), f"{rel} 없음 — python -m tests.unit.public_export.pub_rows"
        if rel == P4_MANIFEST:
            got = json.loads(path.read_text(encoding="utf-8"))
            new = json.loads(body.decode("utf-8"))
            for m in (got, new):
                m["files"] = [e for e in m["files"] if e["name"] in DART_ONLY_NAMES]
            assert got == new, "manifest_p4.json 이 낡았다"
        else:
            assert path.read_bytes() == body, f"{rel} 이 낡았다"


# ── P5 — macro/·credit.json 고리(docs/p5_design.md §1.1·§7 — 본문은 MAC, 테마는 사용자 별도) ──


def test_p5_empty_hook_adds_no_files(tmp_path: Path, cal: TradingCalendar) -> None:
    """FND 의 빈 고리: P5 파일 0·이유 0 — P4 출력이 그대로다."""
    (tmp_path / "macro").mkdir()
    (tmp_path / "macro" / "old.json").write_text("{}", encoding="utf-8")  # 옛 파일은 지운다
    m = _p4(tmp_path, cal)
    assert [d.name for d in m.dirs] == ["fin", "reports"]
    assert "credit.json" not in m.names and m.skipped == ()
    assert not (tmp_path / "macro").exists()
    # 페이지 9 대테마 분류(ADR 0039): pub_themes 가 비면 themes.json 도 0·이유 0
    assert "themes.json" not in m.names
    assert check_tree(tmp_path) == []


def _fake_p5(monkeypatch: pytest.MonkeyPatch, *, pad: int = 0) -> None:
    def macro(read: object, *, now: datetime) -> BuildOut:
        assert callable(read)
        return BuildOut(
            files=(
                PublicFile(name="macro/tabs.json", source="ECOS", as_of=now, quality="ok", data={}),
                PublicFile(
                    name="macro/calendar.json",
                    source="KBJ",
                    as_of=now,
                    quality="ok",
                    data={"pad": "x" * pad},
                ),
            ),
            skipped=("macro/us.json: 시리즈 없음",),
        )

    def credit(read: object, *, now: datetime) -> BuildOut:
        f = PublicFile(name="credit.json", source="DATAGO", as_of=now, quality="stale", data={})
        return BuildOut(files=(f,))

    monkeypatch.setattr(ex, "build_macro", macro)
    monkeypatch.setattr(ex, "build_credit", credit)


def test_p5_hook_writes_builder_files_safely(
    tmp_path: Path, cal: TradingCalendar, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_p5(monkeypatch)
    m = _p4(tmp_path, cal)
    assert [d.name for d in m.dirs] == ["fin", "reports", "macro"]
    assert "credit.json" in m.names and m.skipped == ("macro/us.json: 시리즈 없음",)
    assert check_tree(tmp_path) == []
    for rel, body in _tree(tmp_path).items():
        if rel.endswith(".xlsx"):
            continue
        text = body.decode("utf-8")
        assert forbidden_hits(text) == [] and LOGIN_SOURCES.search(text) is None, rel
        assert "prv_" not in text, rel


def test_p5_hook_without_db_makes_nothing(
    tmp_path: Path, cal: TradingCalendar, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_p5(monkeypatch)
    m = ex.export_all(None, tmp_path, now=NOW, cal=cal)
    assert "credit.json" not in m.names and m.dirs == ()


def test_p5_over_cap_fails_without_truncating(
    tmp_path: Path, cal: TradingCalendar, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_p5(monkeypatch, pad=200 * 1024)
    with pytest.raises(ex.PublicExportError, match="상한"):
        _p4(tmp_path, cal)
    assert not (tmp_path / MANIFEST_NAME).exists()
