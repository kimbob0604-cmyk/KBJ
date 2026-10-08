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

from kbj.config.settings import Settings
from kbj.core.calendar import TradingCalendar
from kbj.core.time import KST
from kbj.services.public_export import export as ex
from kbj.services.public_export.manifest import (
    LOGIN_SOURCES,
    MANIFEST_NAME,
    PublicFile,
    check_tree,
    forbidden_hits,
)
from kbj.services.scheduler.handlers import JobContext

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
