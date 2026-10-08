"""DB 스키마 마이그레이션 0001~0009 와 docker-compose.yml 의 구조 검사(정적 — DB·데몬 없이).

ADR 0002: 공개 등급은 pub_*, 로그인 등급은 prv_*, 운영은 ops. kbj_public_export 는 pub_* 에만
USAGE·SELECT. compose 는 비밀번호 기본값이 없고 포트는 127.0.0.1 에만 연다.
P2(docs/p2_design.md §8.2·§8.8): 0002~0006 은 파일 이름·멱등 문장·스키마 접두사를 지키고,
pub_* 표에는 로그인 등급 출처의 값 열이 없으며, GX 표(0002 의 session_log·health_events, 0006)는
GEXLAB db/migrations 001~004 의 열을 그대로 갖는다.
실제 적용은 tests/integration/test_migrations_pg.py.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "kbj" / "store" / "migrations"
SQL = (MIGRATIONS / "0001_schemas.sql").read_text(encoding="utf-8")
# 주석을 뺀 본문
BODY = "\n".join(line.split("--", 1)[0] for line in SQL.splitlines())
REQUIRED = {
    "pub_filings", "pub_trade", "pub_macro", "pub_market_stats",
    "prv_market", "prv_flows", "prv_board", "prv_gex", "prv_fin", "prv_themes",
    "prv_alerts", "ops",
}  # fmt: skip


def schemas() -> set[str]:
    return set(re.findall(r"CREATE SCHEMA IF NOT EXISTS (\w+)", BODY))


def grants_to_export() -> list[str]:
    stmts = [s.strip() for s in BODY.split(";")]
    return [s for s in stmts if re.search(r"\bTO\s+kbj_public_export\b", s)]


def test_migration_file_names_follow_migrate_rule() -> None:
    # GEXLAB db/migrate.py 규칙(P2 에서 승계): NNN_소문자_이름.sql
    for p in MIGRATIONS.glob("*.sql"):
        assert re.fullmatch(r"\d{3,}_[a-z0-9_]+\.sql", p.name), p.name


def test_schema_names_are_tiered() -> None:
    found = schemas()
    assert found >= REQUIRED, sorted(REQUIRED - found)
    assert all(re.fullmatch(r"(pub|prv)_[a-z_]+|ops", s) for s in found), sorted(found)
    assert "public" not in found


def test_export_role_sees_only_pub_schemas() -> None:
    grants = grants_to_export()
    assert len(grants) == 3  # USAGE, SELECT ON ALL TABLES, DEFAULT PRIVILEGES
    pub = {s for s in schemas() if s.startswith("pub_")}
    for g in grants:
        named = set(re.findall(r"\b(?:pub|prv)_[a-z_]+\b|\bops\b", g))
        assert named == pub, g
        assert (
            "INSERT" not in g
            and "UPDATE" not in g
            and "DELETE" not in g
            and "ALL PRIVILEGES" not in g
        )


def test_export_role_is_nologin_without_password() -> None:
    assert re.search(r"CREATE ROLE kbj_public_export NOLOGIN;", BODY)
    assert "PASSWORD" not in BODY.upper()


def test_idempotent_statements() -> None:
    assert "CREATE EXTENSION IF NOT EXISTS timescaledb" in BODY
    assert not re.search(r"CREATE (SCHEMA|TABLE|EXTENSION)(?! IF NOT EXISTS)", BODY)


def _compose() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def test_compose_services_ports_and_secrets() -> None:
    services = _compose()["services"]
    assert {"db", "redis"} <= set(services)
    assert "timescaledb" in services["db"]["image"] and "pg16" in services["db"]["image"]
    assert services["redis"]["image"].startswith("redis:7")
    for name, svc in services.items():
        for port in svc.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), port
        if svc.get("restart") != "no":  # 한 번 돌고 끝나는 migrate 말고는 상시 — 헬스체크 필수
            assert "healthcheck" in svc, name
    pw = services["db"]["environment"]["POSTGRES_PASSWORD"]
    assert re.fullmatch(r"\$\{KBJ_POSTGRES_PASSWORD:\?[^}]*\}", pw), "기본값 없이 필수"
    rpw = services["redis"]["environment"]["KBJ_REDIS_PASSWORD"]
    assert re.fullmatch(r"\$\{KBJ_REDIS_PASSWORD:\?[^}]*\}", rpw), "기본값 없이 필수"


def test_compose_applies_0001_as_initdb() -> None:
    vols = _compose()["services"]["db"]["volumes"]
    mount = [v for v in vols if "0001_schemas.sql" in v]
    assert mount == [
        "./kbj/store/migrations/0001_schemas.sql:/docker-entrypoint-initdb.d/100_kbj_0001_schemas.sql:ro"
    ]


# ── P2 앱 서비스(묶음 I — docs/p2_design.md §11.2, 메인 결정 D1·D4, ADR 0004) ──────────────────

APP = ("migrate", "auth", "scheduler", "notifier")
SECRET_ENV = re.compile(r"KBJ_\w*(KEY|SECRET|TOKEN|PASSWORD|_ID|_IDS|_URL)$")
_ENTRY = {
    "migrate": ["python", "-m", "kbj.store.migrate"],
    "auth": ["python", "-m", "kbj.services.auth"],
    "scheduler": ["python", "-m", "kbj.services.scheduler", "run"],
    "notifier": ["python", "-m", "kbj.services.notifier", "run"],
}


def _env(name: str) -> dict[str, str]:
    return {k: str(v) for k, v in _compose()["services"][name]["environment"].items()}


def test_app_services_share_one_image_behind_the_app_profile() -> None:
    services = _compose()["services"]
    assert set(APP) <= set(services)
    for name in APP:
        svc = services[name]
        assert svc["profiles"] == ["app"], name  # 기본 `up` 은 지금처럼 db·redis 만
        assert svc["image"] == "kbj-app:local" and svc["build"]["dockerfile"] == "Dockerfile"
        assert svc["command"] == _ENTRY[name]
        env = _env(name)
        assert env["KBJ_SERVICE"] == name and env["TZ"] == "UTC"
        assert "env_file" not in svc, name  # .env 를 통째로 넣지 않는다 — 필요한 값만
        assert "ports" not in svc, name  # 웹훅·API HTTP 서버는 P3(D4)
    for name in ("db", "redis"):
        assert "profiles" not in services[name]


def test_app_services_wait_for_storage_and_migration() -> None:
    services = _compose()["services"]
    assert services["migrate"]["restart"] == "no"
    assert services["migrate"]["depends_on"] == {"db": {"condition": "service_healthy"}}
    assert services["auth"]["depends_on"] == {"redis": {"condition": "service_healthy"}}
    for name in ("scheduler", "notifier"):
        deps = services[name]["depends_on"]
        assert deps["migrate"] == {"condition": "service_completed_successfully"}
        assert deps["db"]["condition"] == deps["redis"]["condition"] == "service_healthy"
    for name in ("auth", "scheduler", "notifier"):
        test = services[name]["healthcheck"]["test"]
        assert test[:5] == ["CMD", "python", "-m", "kbj.services.runtime.healthcheck", name]


def test_kis_app_key_goes_only_to_processes_that_call_kis() -> None:
    """D1 안 A: 앱키는 KIS 를 부르는 프로세스에만 — P2 는 auth 하나(발급자), P3 부터 KIS 수집 작업을
    돌리는 scheduler 도(ADR 0004 'P3 에 scheduler 에도' — 묶음 S). 발급은 그래도 auth 만(계약 ④)."""
    holders = {n for n in APP if "KBJ_KIS_APP_KEY" in _env(n) or "KBJ_KIS_APP_SECRET" in _env(n)}
    assert holders == {"auth", "scheduler"}
    tg = {n for n in APP if any(k.startswith("KBJ_TELEGRAM_") for k in _env(n))}
    assert tg == {"notifier"}
    assert {n for n in APP if "KBJ_DART_API_KEY" in _env(n)} == {"scheduler"}


def test_app_secrets_have_no_default_values() -> None:
    for name in APP:
        for key, value in _env(name).items():
            if not SECRET_ENV.search(key):
                continue
            for m in re.finditer(r"\$\{(\w+)(:[-?])([^}]*)\}", value):
                op, rest = m.group(2), m.group(3)
                assert op == ":?" or rest == "", f"{name}.{key} 에 기본값이 있다"


def test_app_urls_point_inside_compose_with_required_passwords() -> None:
    raw = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    db = "postgresql://kbj:${KBJ_POSTGRES_PASSWORD:?"
    rd = "redis://:${KBJ_REDIS_PASSWORD:?"
    assert db in raw and "@db:5432/kbj" in raw
    assert rd in raw and "@redis:6379/0" in raw
    for name in APP:
        env = _env(name)
        if "KBJ_DATABASE_URL" in env:
            assert env["KBJ_DATABASE_URL"].startswith(db), name
        if "KBJ_REDIS_URL" in env:
            assert env["KBJ_REDIS_URL"].startswith(rd), name
    sched = _compose()["services"]["scheduler"]
    assert "state:/data" in sched["volumes"] and _env("scheduler")["KBJ_DATA_DIR"] == "/data"
    assert "state" in _compose()["volumes"]


def test_dockerfile_keeps_secrets_and_legacy_out() -> None:
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert {".env", ".env.*", "!.env.example", "legacy", "tests", "state"} <= set(ignore)
    copies = [ln.split()[1:-1] for ln in docker.splitlines() if ln.startswith("COPY ")]
    flat = {c for group in copies for c in group}
    assert flat & {".", ".env", "legacy", "tests"} == set()
    assert "uv sync --frozen --no-dev" in docker  # 런타임 의존성만(dev·legacy 그룹 없음)
    assert "postgresql-client" in docker  # ops.nightly pg_dump
    assert "\nUSER kbj" in docker
    assert "ENV TZ=UTC" in docker


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI 없음")
def test_compose_config_is_valid_with_and_without_app_profile(tmp_path: Path) -> None:
    env = tmp_path / "env"
    env.write_text(
        "KBJ_POSTGRES_PASSWORD=simpw123\nKBJ_REDIS_PASSWORD=simpw456\n", encoding="utf-8"
    )
    base = ["docker", "compose", "--project-directory", str(ROOT), "--env-file", str(env)]
    # 셸의 KBJ_*·COMPOSE_*(COMPOSE_FILE·COMPOSE_PROFILES 등)가 결과를 바꾸지 않게 뺀다
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("KBJ_", "COMPOSE_"))}
    for extra in ([], ["--profile", "app"]):
        r = subprocess.run(  # noqa: S603 — 고정 인자
            [*base, *extra, "config", "-q"], capture_output=True, text=True, check=False, env=clean
        )
        if "docker" in r.stderr and "not found" in r.stderr:  # compose 플러그인 없음
            pytest.skip("docker compose 플러그인 없음")
        assert r.returncode == 0, r.stderr
    for extra in ([], ["--profile", "app"]):
        missing = subprocess.run(  # noqa: S603
            [*base[:4], "--env-file", "/dev/null", *extra, "config", "-q"],
            capture_output=True,
            text=True,
            check=False,
            env=clean,
        )
        assert missing.returncode != 0  # 비밀번호가 없으면 멈춘다(기본값 없음)


# ── P2 마이그레이션 0002~0006 (docs/p2_design.md §8.2·§8.8) ──────────────────────────────────

P2_FILES = (
    "0002_ops_core.sql",
    "0003_market_flows.sql",
    "0004_filings_corp.sql",
    "0005_alerts_inbox.sql",
    "0006_gex.sql",
)
GX_MIGRATIONS = ROOT / "legacy" / "gexlab" / "db" / "migrations"
_CONSTRAINT = re.compile(r"(CHECK|UNIQUE|PRIMARY KEY|CONSTRAINT|FOREIGN KEY)\b")


def _body(name: str) -> str:
    """주석을 뺀 본문(문자열 안의 '--' 는 이 파일들에 없다)."""
    text = (MIGRATIONS / name).read_text(encoding="utf-8")
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines())


def _tables(body: str) -> dict[str, list[str]]:
    """`CREATE TABLE … 스키마.표 (…);` → {스키마.표: 열 이름 목록(정의 순서)}."""
    out: dict[str, list[str]] = {}
    for m in re.finditer(
        r"CREATE TABLE (?:IF NOT EXISTS )?([\w.]+) \((.*?)\n\);", body, flags=re.S
    ):
        cols = []
        for line in m.group(2).splitlines():
            s = line.strip()
            if not s or _CONSTRAINT.match(s) or s.startswith(")"):
                continue
            cols.append(s.split()[0])
        out[m.group(1)] = cols
    return out


def _all_p2_tables() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for name in P2_FILES:
        found.update(_tables(_body(name)))
    return found


def test_p2_migration_files_exist_in_order() -> None:
    names = sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    assert names[: 1 + len(P2_FILES)] == ["0001_schemas.sql", *P2_FILES]


def test_p2_statements_are_idempotent() -> None:
    for name in P2_FILES:
        body = _body(name)
        assert not re.search(r"CREATE (SCHEMA|TABLE|EXTENSION)(?! IF NOT EXISTS)", body), name
        assert not re.search(r"CREATE (UNIQUE )?INDEX(?! IF NOT EXISTS)", body), name
        assert not re.search(r"CREATE (?!OR REPLACE )VIEW", body), name
        starts = {s.strip().split(maxsplit=1)[0].upper() for s in body.split(";") if s.strip()}
        assert not starts & {"DROP", "TRUNCATE", "DELETE", "INSERT", "UPDATE"}, name
        assert not re.search(r"ALTER TABLE [\w.]+ (ADD|DROP|RENAME|ALTER)", body), name
        for call in re.findall(r"create_hypertable\((.*?)\);", body, flags=re.S):
            assert "if_not_exists => TRUE" in call, (name, call)
        for call in re.findall(r"add_compression_policy\((.*?)\);", body, flags=re.S):
            assert "if_not_exists => TRUE" in call, (name, call)


def test_p2_objects_are_schema_qualified_with_tier_prefixes() -> None:
    allowed = re.compile(r"(pub|prv)_[a-z_]+|ops")
    for name in P2_FILES:
        body = _body(name)
        objs = re.findall(r"CREATE (?:TABLE|OR REPLACE VIEW) (?:IF NOT EXISTS )?([\w.]+)", body)
        objs += re.findall(r"\bON ([\w.]+) \(", body)  # 색인
        objs += re.findall(r"create_hypertable\(\s*'([\w.]+)'", body)
        objs += re.findall(r"add_compression_policy\('([\w.]+)'", body)
        assert objs, name
        for obj in objs:
            schema, _, table = obj.partition(".")
            assert table and allowed.fullmatch(schema), (name, obj)
        assert not re.search(r"\bpublic\.", body), name


def test_value_tables_carry_source_and_quality() -> None:
    """절대 규칙 1 — 값 표(pub_*·prv_market·prv_flows)는 source·quality 열을 갖는다."""
    for table, cols in _all_p2_tables().items():
        if table.startswith(("pub_", "prv_market.", "prv_flows.")):
            assert {"source", "quality"} <= set(cols), table


def test_pub_tables_hold_no_login_tier_values() -> None:
    """ADR 0002 §2.1 — pub_* 표에는 로그인 등급 출처(시세·수급·KIS·KRX)의 값 열이 없다."""
    login_cols = {
        "open", "high", "low", "close", "price", "volume", "turnover", "mktcap", "chg_pct",
        "net_qty", "net_value", "shares", "nav", "loaded_by",
    }  # fmt: skip
    pub = {t: c for t, c in _all_p2_tables().items() if t.startswith("pub_")}
    assert pub == {
        "pub_filings.corp_code": [
            "corp_code", "stock_code", "corp_name", "modify_date", "source", "quality",
            "received_at",
        ]
    }  # fmt: skip
    for table, cols in pub.items():
        assert not login_cols & set(cols), table
    body = _body("0004_filings_corp.sql")
    assert "CHECK (source = 'DART')" in body
    assert not re.search(r"\b(KIS|KRX|naver|yahoo|nasdaq)\b", body, flags=re.I)


def test_p2_grants_to_export_role_are_select_on_pub_only() -> None:
    for name in P2_FILES:
        stmts = [s.strip() for s in _body(name).split(";")]
        for g in (s for s in stmts if re.search(r"\bkbj_public_export\b", s)):
            assert re.fullmatch(r"GRANT SELECT ON pub_[a-z_]+\.[a-z_]+ TO kbj_public_export", g), g


def test_prv_and_ops_tables_live_in_the_expected_files() -> None:
    expect = {
        "0002_ops_core.sql": {
            "ops.job_run", "ops.data_claim", "ops.kv", "ops.notify_log", "ops.session_log",
            "ops.health_events", "ops.legacy_import",
        },
        "0003_market_flows.sql": {
            "prv_market.stock_snapshot", "prv_market.daily_bar", "prv_market.universe",
            "prv_flows.stock_investor_daily", "prv_flows.market_investor_daily",
        },
        "0004_filings_corp.sql": {"pub_filings.corp_code"},
        "0005_alerts_inbox.sql": {"prv_alerts.tg_inbox", "prv_alerts.notify_message"},
    }  # fmt: skip
    for name, tables in expect.items():
        assert set(_tables(_body(name))) == tables, name


def test_data_claim_key_is_unique_only_while_claimed_or_done() -> None:
    body = _body("0002_ops_core.sql")
    m = re.search(
        r"CREATE UNIQUE INDEX IF NOT EXISTS data_claim_live_key\s+ON ops\.data_claim "
        r"\(source, dataset, as_of, venue\)\s+WHERE status IN \('claimed', 'done'\);",
        body,
    )
    assert m, "부분 유일 인덱스(§6.5)"


def _gx_tables() -> dict[str, list[str]]:
    """GEXLAB 001~004 적용 뒤의 표·열(004 의 ADD COLUMN flag 포함)."""
    out: dict[str, list[str]] = {}
    for p in sorted(GX_MIGRATIONS.glob("*.sql")):
        body = "\n".join(line.split("--", 1)[0] for line in p.read_text("utf-8").splitlines())
        out.update(_tables(body))
        for table, col in re.findall(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", body):
            out[table].append(col)
    return out


def test_gx_tables_keep_their_names_and_columns() -> None:
    """conflict_map §1.5 — GX 표는 이름·열 그대로 prv_gex(시장·engine)·ops(운영)로 옮긴다."""
    gx = _gx_tables()
    assert len(gx) == 21
    ops_names = {"session_log", "health_events", "collection_gaps", "quarantine",
                 "collection_reports"}  # fmt: skip
    kbj = _all_p2_tables()
    for table, cols in gx.items():
        schema = "ops" if table in ops_names else "prv_gex"
        assert kbj.get(f"{schema}.{table}") == cols, table
    assert len([t for t in kbj if t.startswith("prv_gex.")]) == 16


def test_market_investor_intraday_is_a_view_over_gx_investor_flow() -> None:
    body = _body("0006_gex.sql")
    m = re.search(
        r"CREATE OR REPLACE VIEW prv_flows\.market_investor_intraday AS(.*?);", body, re.S
    )
    assert m and re.search(r"FROM prv_gex\.investor_flow\s*$", m.group(1).strip() + "\n")


# ── P3 마이그레이션 0007~0009 (docs/p3_design.md §3.4, D-P3-6) ──────────────────────────────

P3_FILES = ("0007_board.sql", "0008_market_flows_p3.sql", "0009_etf.sql")
ET_BOARD_DB = ROOT / "legacy" / "etf_traker" / "board" / "engine" / "db.py"
ET_TRACKER = ROOT / "legacy" / "etf_traker" / "etf_tracker_v9" / "tracker.py"
# 값이 아니라 대조·차이·검산 결과를 담는 기록 표 — 출처는 열 이름(kis_value·final_source …)이 말한다
P3_RECORD_TABLES = {
    "prv_market.eod_reconcile",
    "prv_flows.investor_revision",
    "prv_flows.ledger_check",
    "prv_board.split_check",
    "prv_etf.fund",
    "prv_etf.change_log",
}


def _all_p3_tables() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for name in P3_FILES:
        found.update(_tables(_body(name)))
    return found


def _et_sqlite_tables(path: Path) -> dict[str, list[str]]:
    """ET 원본의 DDL 문자열(sqlite)에서 표 열 이름."""
    text = path.read_text(encoding="utf-8")
    out: dict[str, list[str]] = {}
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\);", text, flags=re.S):
        body = "\n".join(line.split("--", 1)[0] for line in m.group(2).splitlines())
        cols = []
        for part in body.split(","):
            s = part.strip()
            if not s or s.startswith(("PRIMARY", "UNIQUE")) or s[0].isdigit() or "(" in s[:1]:
                continue
            word = s.split()[0]
            if word.isidentifier() and not word.isupper():
                cols.append(word)
        out[m.group(1)] = cols
    return out


def test_p3_migration_files_follow_p2_in_order() -> None:
    names = sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    assert names[: 1 + len(P2_FILES) + len(P3_FILES)] == ["0001_schemas.sql", *P2_FILES, *P3_FILES]


def test_p3_statements_are_idempotent_and_never_alter_or_write() -> None:
    for name in P3_FILES:
        body = _body(name)
        assert not re.search(r"CREATE (SCHEMA|TABLE|EXTENSION)(?! IF NOT EXISTS)", body), name
        assert not re.search(r"CREATE (UNIQUE )?INDEX(?! IF NOT EXISTS)", body), name
        starts = {s.strip().split(maxsplit=1)[0].upper() for s in body.split(";") if s.strip()}
        assert starts <= {"CREATE", "SELECT"}, (name, starts)  # ALTER·GRANT·DROP·INSERT 없음
        for fn in ("create_hypertable", "add_retention_policy", "add_compression_policy"):
            for call in re.findall(rf"{fn}\((.*?)\);", body, flags=re.S):
                assert "if_not_exists => TRUE" in call, (name, call)


def test_p3_objects_live_in_prv_schemas_only() -> None:
    """P3 의 새 표는 모두 로그인 등급(원천 KIS·KRX·운용사) — prv_* 에만, pub_* 권한 문장 없음."""
    for name in P3_FILES:
        body = _body(name)
        objs = re.findall(r"CREATE TABLE IF NOT EXISTS ([\w.]+)", body)
        objs += re.findall(r"\bON ([\w.]+) \(", body)
        objs += re.findall(r"(?:create_hypertable|add_retention_policy)\(\s*'([\w.]+)'", body)
        assert objs, name
        for obj in objs:
            schema, _, table = obj.partition(".")
            assert table and re.fullmatch(r"prv_(board|market|flows|etf)", schema), (name, obj)
        assert "kbj_public_export" not in body and not re.search(r"\bpublic\.", body), name


def test_p3_tables_live_in_the_expected_files() -> None:
    expect = {
        "0007_board.sql": {
            "prv_board.alltime", "prv_board.label", "prv_board.split_check",
            "prv_board.stock_day", "prv_board.artifact",
        },
        "0008_market_flows_p3.sql": {
            "prv_market.index_intraday", "prv_market.sector_intraday",
            "prv_market.turnover_rank_intraday", "prv_market.eod_reconcile",
            "prv_flows.investor_intraday", "prv_flows.investor_revision", "prv_flows.ledger_check",
        },
        "0009_etf.sql": {
            "prv_etf.etf_daily", "prv_etf.quote_intraday", "prv_etf.meta", "prv_etf.split_event",
            "prv_etf.fund", "prv_etf.holding", "prv_etf.change_log",
        },
    }  # fmt: skip
    for name, tables in expect.items():
        assert set(_tables(_body(name))) == tables, name
    assert not set(_all_p3_tables()) & set(_all_p2_tables())  # 앞 번호 표를 다시 만들지 않는다


def test_p3_value_tables_carry_source_quality_and_loader() -> None:
    """절대 규칙 1 — 값 표는 source·quality(+ 쓴 작업 loaded_by). 기록 표는 위 목록뿐."""
    tables = _all_p3_tables()
    assert set(tables) >= P3_RECORD_TABLES
    for table, cols in tables.items():
        if table in P3_RECORD_TABLES:
            continue
        assert {"source", "quality", "loaded_by"} <= set(cols), table


def test_p3_quality_and_venue_checks_match_0003() -> None:
    for name in P3_FILES:
        body = _body(name)
        for m in re.finditer(r"quality\s+text NOT NULL CHECK \((quality IN \([^)]*\))\)", body):
            assert m.group(1) == "quality IN ('ok', 'stale', 'estimated', 'invalid')", name
        for m in re.finditer(
            r"venue\s+text NOT NULL DEFAULT '' CHECK \((venue IN \([^)]*\))\)", body
        ):
            assert m.group(1) == "venue IN ('', 'KRX', 'NXT', 'TOTAL')", name
        n_q = len(re.findall(r"\bquality\s+text", body))
        assert n_q == len(re.findall(r"quality\s+text NOT NULL CHECK \(quality IN", body)), name


def test_p3_board_tables_keep_et_columns() -> None:
    """ET board/engine/db.py 의 alltime·label·split_check 열을 그대로 옮겼다(+ KBJ 열)."""
    et = _et_sqlite_tables(ET_BOARD_DB)
    kbj = _all_p3_tables()
    renamed = {"asof": "trade_date"}  # ET 의 날짜 열 이름 → KBJ 공통 이름
    for table in ("alltime", "label", "split_check"):
        want = {renamed.get(c, c) for c in et[table]}
        assert want <= set(kbj[f"prv_board.{table}"]), (
            table,
            want - set(kbj[f"prv_board.{table}"]),
        )
    assert {"history_from", "source", "quality"} <= set(kbj["prv_board.alltime"])  # D-P3-11


def test_p3_etf_tables_keep_et_columns() -> None:
    """ET etf_tracker_v9/tracker.py DDL 의 fund·holding·change_log 열.

    fund.mktcap 은 네이버 출처라 뺐다(U4)."""
    et = _et_sqlite_tables(ET_TRACKER)
    kbj = _all_p3_tables()
    assert set(et["fund"]) - {"mktcap"} <= set(kbj["prv_etf.fund"])
    assert set(et["holding"]) <= set(kbj["prv_etf.holding"])
    assert set(et["change_log"]) <= set(kbj["prv_etf.change_log"])
    assert "mktcap" not in kbj["prv_etf.fund"]


def test_p3_intraday_tables_have_retention_and_daily_tables_do_not() -> None:
    body = "\n".join(_body(n) for n in P3_FILES)
    kept = dict(re.findall(r"add_retention_policy\(\s*'([\w.]+)', INTERVAL '(\d+ days)'", body))
    assert kept == {
        "prv_market.index_intraday": "90 days",
        "prv_market.sector_intraday": "90 days",
        "prv_market.turnover_rank_intraday": "90 days",
        "prv_flows.investor_intraday": "90 days",
        "prv_etf.quote_intraday": "30 days",
    }
    found = re.findall(
        r"create_hypertable\(\s*'([\w.]+)', by_range\('\w+', INTERVAL '([^']+)'", body
    )
    hyper = dict(found)
    assert len(hyper) == len(found)
    assert {t for t, iv in hyper.items() if iv == "1 day"} == set(kept)
    assert {t for t, iv in hyper.items() if iv == "365 days"} == {
        "prv_board.label", "prv_board.stock_day", "prv_market.eod_reconcile",
        "prv_flows.investor_revision", "prv_etf.etf_daily", "prv_etf.holding",
    }  # fmt: skip


def test_p3_etf_type_check_matches_rows_enum() -> None:
    from kbj.core.rows import EtfType

    m = re.search(r"etf_type\s+text CHECK \(etf_type IN \(([^)]*)\)\)", _body("0009_etf.sql"))
    assert m is not None
    assert set(re.findall(r"'(\w+)'", m.group(1))) == {t.value for t in EtfType}
