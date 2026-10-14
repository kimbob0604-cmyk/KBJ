"""DB 스키마 마이그레이션 0001~0021 과 docker-compose.yml 의 구조 검사(정적 — DB·데몬 없이).

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
# ET etf_tracker_v9/tracker.py 의 sqlite DDL 열(P5 WRAP 이 tracker.py 를 지우기 전에
# 원본에서 그대로 옮겼다 —
# `_et_sqlite_tables` 가 원본에서 읽던 값과 같다. ETF 리포트는 kbj etf.report 가 대체)
ET_TRACKER_TABLES: dict[str, list[str]] = {
    "fund": ["fund_id", "issuer", "fund_key", "ticker", "name", "theme", "is_active", "mktcap",
             "depth", "track", "empty_streak"],
    "holding": ["fund_id", "asof", "code", "name", "qty", "wt", "val", "asof"],
    "change_log": ["run_date", "fund_id", "code", "kind", "name", "asof", "prev_asof", "gap_days",
                   "prev_qty", "cur_qty", "prev_wt", "cur_wt", "qty_pct", "qty_pct_adj", "fund_id",
                   "code"],
}  # fmt: skip
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
    et = ET_TRACKER_TABLES
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


# ── P4 마이그레이션 0010~0013 (docs/p4_design.md §3.4, D-P4-2) ──────────────────────────────

P4_FILES = (
    "0010_filings.sql",
    "0011_fin.sql",
    "0012_fin_prv.sql",
    "0013_journal_watchlist.sql",
)
P4_EXPECT = {
    "0010_filings.sql": {
        "pub_filings.disclosure", "pub_filings.corp_profile", "pub_filings.earnings_prelim",
        "pub_filings.earnings_schedule", "pub_filings.overhang_event",
        "pub_filings.overhang_adjust", "pub_filings.overhang_state",
        "pub_filings.overhang_summary", "pub_filings.insider_report",
        "pub_filings.major_holder_report", "pub_filings.insider_window",
        "pub_filings.buyback_event", "pub_filings.share_count",
    },
    "0011_fin.sql": {
        "pub_fin.statement_raw", "pub_fin.quarterly", "pub_fin.ratio", "pub_fin.report",
        "pub_fin.segment",
    },
    "0012_fin_prv.sql": {
        "prv_fin.consensus_snapshot", "prv_fin.invest_opinion", "prv_fin.target_consensus",
        "prv_fin.revision", "prv_fin.valuation_daily", "prv_fin.valuation_band",
        "prv_fin.scenario", "prv_fin.earnings_surprise", "prv_market.share_class",
    },
    "0013_journal_watchlist.sql": {"prv_journal.watchlist"},
}  # fmt: skip
# 시세·수급(로그인 원천)에서만 나오는 열 이름 — pub_* 에 있으면 안 된다(ADR 0002 §2.1).
# 오버행 price·shares 는 DART 결정 공시의 전환가·주식 수라 허용한다.
# 혼합 값(오버행 금액·ITM·관심종목·서프라이즈)도 pub_* 에 없다.
LOGIN_ONLY_COLS = {
    "open", "high", "low", "close", "volume", "turnover", "mktcap", "mktcap_company", "chg_pct",
    "net_qty", "net_value", "nav", "itm", "itm_shares", "amount_krw", "per", "pbr", "psr",
    "ev_ebitda", "is_watch", "login_score", "rev_surprise", "op_surprise", "consensus_ref",
}  # fmt: skip


def _all_p4_tables() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for name in P4_FILES:
        found.update(_tables(_body(name)))
    return found


def test_p4_migration_files_follow_p3_in_order() -> None:
    names = sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    assert names[: 1 + len(P2_FILES) + len(P3_FILES) + len(P4_FILES)] == [
        "0001_schemas.sql", *P2_FILES, *P3_FILES, *P4_FILES,
    ]  # fmt: skip


def test_p4_statements_are_idempotent_and_never_alter_or_write() -> None:
    for name in P4_FILES:
        body = _body(name)
        assert not re.search(r"CREATE (SCHEMA|TABLE|EXTENSION)(?! IF NOT EXISTS)", body), name
        assert not re.search(r"CREATE (UNIQUE )?INDEX(?! IF NOT EXISTS)", body), name
        starts = {s.strip().split(maxsplit=1)[0].upper() for s in body.split(";") if s.strip()}
        assert starts <= {"CREATE", "SELECT", "GRANT"}, (name, starts)  # ALTER·DROP·INSERT 없음
        for call in re.findall(r"create_hypertable\((.*?)\);", body, flags=re.S):
            assert "if_not_exists => TRUE" in call, (name, call)


def test_p4_tables_live_in_the_expected_files_and_tiers() -> None:
    for name, tables in P4_EXPECT.items():
        assert set(_tables(_body(name))) == tables, name
    earlier = set(_all_p2_tables()) | set(_all_p3_tables())
    assert not set(_all_p4_tables()) & earlier  # 앞 번호 표를 다시 만들지 않는다
    for name in ("0010_filings.sql", "0011_fin.sql"):  # 공개 — pub_* 에만
        for t in _tables(_body(name)):
            assert t.startswith(("pub_filings.", "pub_fin.")), (name, t)
    for name in ("0012_fin_prv.sql", "0013_journal_watchlist.sql"):  # 로그인·개인 — prv_* 에만
        body = _body(name)
        for t in _tables(body):
            assert t.startswith(("prv_fin.", "prv_market.", "prv_journal.")), (name, t)
        assert "kbj_public_export" not in body and not re.search(r"\bpub_", body), name
        assert not re.search(r"\bpublic\.", body), name


def test_p4_pub_tables_hold_dart_values_only() -> None:
    """pub_* 표: source 는 'DART' 만(CHECK), 시세·혼합 값 열이 없다(ADR 0002 §2.1, 설계 §2)."""
    for name in ("0010_filings.sql", "0011_fin.sql"):
        body = _body(name)
        for table, cols in _tables(body).items():
            assert {"source", "quality", "loaded_by"} <= set(cols), table
            assert not LOGIN_ONLY_COLS & set(cols), (table, LOGIN_ONLY_COLS & set(cols))
        n_tables = len(_tables(body))
        assert body.count("CHECK (source = 'DART')") == n_tables, name
        assert not re.search(r"\b(KIS|KRX|naver|yahoo|fnguide|wisereport)\b", body, flags=re.I)


def test_p4_pub_tables_are_granted_select_only() -> None:
    for name in ("0010_filings.sql", "0011_fin.sql"):
        body = _body(name)
        stmts = [s.strip() for s in body.split(";")]
        grants = [s for s in stmts if re.search(r"\bkbj_public_export\b", s)]
        for g in grants:
            assert re.fullmatch(r"GRANT SELECT ON pub_[a-z_]+\.[a-z_]+ TO kbj_public_export", g), g
        granted = {re.search(r"ON ([\w.]+) TO", g).group(1) for g in grants}  # type: ignore[union-attr]
        assert granted == set(_tables(body)), name  # 표마다 명시 GRANT(0004 방식)


def test_p4_value_tables_carry_source_quality_and_loader() -> None:
    for table, cols in _all_p4_tables().items():
        if table == "prv_journal.watchlist":  # 개인 데이터 — 출처는 source(import:sd·cli·api)
            assert {"source", "loaded_by", "updated_at"} <= set(cols)
            continue
        assert {"source", "quality", "loaded_by"} <= set(cols), table
        assert {"received_at", "computed_at", "updated_at", "generated_at"} & set(cols), table


def test_p4_quality_checks_and_keys_match_the_design() -> None:
    for name in P4_FILES:
        body = _body(name)
        for m in re.finditer(r"quality\s+text NOT NULL[^,]*CHECK \((quality IN \([^)]*\))\)", body):
            assert m.group(1) == "quality IN ('ok', 'stale', 'estimated', 'invalid')", name
        n_q = len(re.findall(r"\bquality\s+text", body))
        assert n_q == len(re.findall(r"quality\s+text NOT NULL[^,]*CHECK \(quality IN", body)), name
    body = "\n".join(_body(n) for n in P4_FILES)

    def pk(table: str) -> str:
        m = re.search(rf"CREATE TABLE IF NOT EXISTS {re.escape(table)} \((.*?)\n\);", body, re.S)
        assert m is not None, table
        inline = re.search(r"^\s*(\w+)\s+\w+ PRIMARY KEY", m.group(1), re.M)
        if inline:
            return inline.group(1)
        found = re.search(r"PRIMARY KEY \(([^)]*)\)", m.group(1))
        assert found is not None, table
        return found.group(1)

    assert pk("pub_filings.disclosure") == "rcept_no"
    assert pk("pub_filings.overhang_state") == "corp_code, as_of, rcept_no"
    assert pk("pub_filings.insider_window") == "corp_code, as_of, days"
    assert pk("pub_filings.share_count") == "corp_code, period_end, rcept_no"
    assert pk("pub_fin.statement_raw") == "corp_code, bsns_year, reprt_code, fs_div"
    assert pk("pub_fin.quarterly") == "corp_code, fiscal_year, fiscal_q, fs_div"
    assert pk("pub_fin.report") == "stock_code, built_on"
    assert pk("prv_fin.consensus_snapshot") == "code, snap_date, fiscal_ym, source"
    assert pk("prv_fin.invest_opinion") == "code, broker, opinion_date, source"
    assert pk("prv_fin.revision") == "code, as_of, metric, fiscal_ym, window_days"
    assert pk("prv_fin.valuation_daily") == "code, trade_date"
    assert pk("prv_fin.scenario") == "code, as_of, scenario_case"
    assert pk("prv_journal.watchlist") == "list_name, code"
    assert "octet_length(xlsx) <= 2097152" in body  # 엑셀 2 MiB 상한(§3.4)
    hyper = re.findall(
        r"create_hypertable\(\s*'([\w.]+)', by_range\('(\w+)', INTERVAL '([^']+)'", body
    )
    assert hyper == [("prv_fin.valuation_daily", "trade_date", "365 days")]
    assert "add_retention_policy" not in body  # 밸류 일별은 보존 없음(§3.4)


def test_p4_enum_checks_match_rows() -> None:
    """DB CHECK 의 값 목록 = kbj.core.fin_rows 의 열거(같은 말을 쓴다)."""
    from kbj.core import fin_rows as fr

    body = "\n".join(_body(n) for n in P4_FILES)

    def values(col: str) -> set[str]:
        """그 열 이름의 CHECK 목록 전부의 합(같은 이름이 여러 표에 있으면 — metric)."""
        found = re.findall(rf"\b{col}\s+text NOT NULL CHECK \({col} IN \(([^)]*)\)\)", body)
        assert found, col
        return {v for f in found for v in re.findall(r"'([\w:]+)'", f)}

    assert values("instrument") == {i.value for i in fr.Instrument}
    assert values("scope") == {s.value for s in fr.PrelimScope}
    assert values("metric") == {*fr.BAND_METRICS, "revenue", "op", "ni", "eps", "target_mean"}
    assert values("scenario_case") == set(fr.SCENARIO_CASES)
    assert values("method") == set(fr.SCENARIO_METHODS)
    assert values("source") == set(fr.WATCH_SOURCES)
    m = re.search(
        r"parse_state\s+text NOT NULL DEFAULT 'none' CHECK \(parse_state IN \(([^)]*)\)", body
    )
    assert m is not None and set(re.findall(r"'(\w+)'", m.group(1))) == set(fr.PARSE_STATES)
    signals = re.search(r"signal IN \(([^)]*)\)", body)
    assert signals is not None
    assert set(re.findall(r"'(\w+)'", signals.group(1))) == set(fr.REVISION_SIGNALS)


# ── P5 마이그레이션 0014~0017 (docs/p5_design.md §3.3, D-P5-3 — 테마 표는 P5 에서 뺐다) ──

P5_FILES = (
    "0014_macro.sql",
    "0015_us_board.sql",
    "0016_market_stats.sql",
    "0017_program.sql",
)
P5_EXPECT = {
    "0014_macro.sql": {
        "pub_macro.series_meta", "pub_macro.series", "pub_macro.calendar",
        "prv_macro.series_meta", "prv_macro.series", "prv_macro.calendar",
    },
    "0015_us_board.sql": {"prv_market.us_universe"},
    "0016_market_stats.sql": {
        "pub_market_stats.credit_balance", "pub_market_stats.market_capital",
        "pub_market_stats.fund_nav", "pub_market_stats.cma",
    },
    "0017_program.sql": {
        "prv_flows.program_market_daily", "prv_flows.program_market_intraday",
        "prv_flows.program_investor_daily", "prv_flows.program_stock_intraday",
        "prv_flows.program_stock_daily",
    },
}  # fmt: skip
# 공개 표에 들어오면 안 되는 로그인 출처 이름(DATA_TIERS §1 — 시세·운용사·비공식 피드)
LOGIN_SOURCES = re.compile(r"\b(KIS|KRX|yahoo|nasdaq|stooq|ff|forexfactory|naver)\b", re.I)


def _no_do_blocks(body: str) -> str:
    """DO $$ … $$; 블록을 뺀 본문(블록 안의 ';' 로 문장을 잘못 나누지 않게)."""
    return re.sub(r"DO \$\$.*?\$\$;", "", body, flags=re.S)


def _all_p5_tables() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for name in P5_FILES:
        found.update(_tables(_body(name)))
    return found


def _p5_pub_tables() -> dict[str, list[str]]:
    return {t: c for t, c in _all_p5_tables().items() if t.startswith("pub_")}


def test_p5_migration_files_follow_p4_in_order() -> None:
    names = sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    n = 1 + len(P2_FILES) + len(P3_FILES) + len(P4_FILES)
    assert names[: n + len(P5_FILES)] == [
        "0001_schemas.sql", *P2_FILES, *P3_FILES, *P4_FILES, *P5_FILES,
    ]  # fmt: skip


def test_p5_statements_are_idempotent_and_alter_only_in_guarded_blocks() -> None:
    for name in P5_FILES:
        body = _body(name)
        assert not re.search(r"CREATE (SCHEMA|TABLE|EXTENSION)(?! IF NOT EXISTS)", body), name
        assert not re.search(r"CREATE (UNIQUE )?INDEX(?! IF NOT EXISTS)", body), name
        plain = _no_do_blocks(body)
        starts = {s.strip().split(maxsplit=1)[0].upper() for s in plain.split(";") if s.strip()}
        assert starts <= {"CREATE", "SELECT", "GRANT", "COMMENT"}, (name, starts)  # ALTER·DROP 없음
        for call in re.findall(r"create_hypertable\((.*?)\);", body, flags=re.S):
            assert "if_not_exists => TRUE" in call, (name, call)
        for call in re.findall(r"add_retention_policy\((.*?)\);", body, flags=re.S):
            assert "if_not_exists => TRUE" in call, (name, call)
        if name != "0015_us_board.sql":
            assert "DO $$" not in body, name
    # 0015 의 DO 블록은 새 제약이 있으면 아무것도 하지 않는다(두 번 적용해도 같다) — 이름에 기대지
    # 않고 정의('universe_meta' 를 담은 CHECK)로 옛 제약을 찾는다(0007 의 자동 이름 [확인 필요])
    us = _body("0015_us_board.sql")
    assert us.count("DO $$") == 1
    assert "conname = 'artifact_name_p5_check'" in us and "IF NOT EXISTS" in us
    assert "pg_get_constraintdef(oid) LIKE '%universe_meta%'" in us
    assert "'us_summary', 'us_brief'" in us and "market = 'US'" in us
    assert not re.search(r"ALTER TABLE (?!prv_board\.artifact )", us)


def test_p5_tables_live_in_the_expected_files_and_tiers() -> None:
    for name, tables in P5_EXPECT.items():
        assert set(_tables(_body(name))) == tables, name
    earlier = set(_all_p2_tables()) | set(_all_p3_tables()) | set(_all_p4_tables())
    assert not set(_all_p5_tables()) & earlier  # 앞 번호 표를 다시 만들지 않는다
    for name in ("0015_us_board.sql", "0017_program.sql"):  # 로그인 — prv_* 에만
        body = _body(name)
        assert "kbj_public_export" not in body and not re.search(r"\bpub_", body), name
    for name in P5_FILES:
        assert not re.search(r"\bpublic\.", _body(name)), name
    # 0017 은 GX 표에 손대지 않는다 — 쓰기 주인·단위를 COMMENT 로만(D-P5-3·21)
    prog = _body("0017_program.sql")
    assert re.search(r"COMMENT ON TABLE prv_gex\.investor_flow IS", prog)
    assert "flows.market_minute" in prog and "백만원" in prog
    assert not re.search(r"(ALTER|CREATE) TABLE (IF NOT EXISTS )?prv_gex\.", prog)


def test_p5_pub_tables_hold_public_sources_only() -> None:
    """pub_* 표: 로그인 출처 이름·시세 값 열이 없다. 공개 매크로·캘린더의 source CHECK 는 공개 목록,
    예상치(forecast)는 prv_macro.calendar 에만(D-P5-8)."""
    pub = _p5_pub_tables()
    assert pub
    for table, cols in pub.items():
        assert not LOGIN_ONLY_COLS & set(cols), (table, LOGIN_ONLY_COLS & set(cols))
        assert "forecast" not in cols, table
        assert {"source"} <= set(cols), table
    for name in ("0014_macro.sql", "0016_market_stats.sql"):
        pub_part = "\n".join(
            m.group(0)
            for m in re.finditer(
                r"CREATE TABLE IF NOT EXISTS pub_\w+\.\w+ \(.*?\n\);", _body(name), re.S
            )
        )
        assert not LOGIN_SOURCES.search(pub_part), name
    macro = _body("0014_macro.sql")
    pub_cal = "CHECK (source IN ('bok', 'fomc', 'fred', 'kosis', 'ecos', 'manual'))"
    assert macro.count(pub_cal) == 1
    assert (
        macro.count("CHECK (source IN ('ECOS', 'KOSIS', 'FRED', 'TREASURY', 'NYFED'))") == 2
    )  # pub series_meta·series
    assert "CHECK (tier = 'public')" in macro and "CHECK (tier = 'private')" in macro
    assert "dataset <> 'FRED:restricted_series'" in macro  # 저작권 시리즈는 공개 메타에 못 들어온다
    cols = _all_p5_tables()
    assert "forecast" in cols["prv_macro.calendar"]
    assert _body("0016_market_stats.sql").count("CHECK (source = 'DATAGO')") == 4


def test_p5_values_are_null_not_zero() -> None:
    """값 칸은 NULL 허용(0 으로 채우지 않는다) — 값이 없으면 quality 가 ok 일 수 없다
    (절대 규칙 2)."""
    for table in (
        "pub_macro.series",
        "prv_macro.series",
        *sorted(P5_EXPECT["0016_market_stats.sql"]),
    ):
        name = "0014_macro.sql" if "macro" in table else "0016_market_stats.sql"
        m = re.search(
            rf"CREATE TABLE IF NOT EXISTS {re.escape(table)} \((.*?)\n\);", _body(name), re.S
        )
        assert m is not None, table
        assert re.search(r"^\s*value\s+numeric,$", m.group(1), re.M), (
            table
        )  # NOT NULL·DEFAULT 0 없음
        assert "CHECK (value IS NOT NULL OR quality <> 'ok')" in m.group(1), table
    assert "DEFAULT 0" not in "\n".join(_body(n) for n in P5_FILES)


def test_p5_pub_tables_are_granted_select_only() -> None:
    for name in ("0014_macro.sql", "0016_market_stats.sql"):
        body = _body(name)
        stmts = [s.strip() for s in body.split(";")]
        grants = [s for s in stmts if re.search(r"\bkbj_public_export\b", s)]
        for g in grants:
            assert re.fullmatch(r"GRANT SELECT ON pub_[a-z_]+\.[a-z_]+ TO kbj_public_export", g), g
        granted = {re.search(r"ON ([\w.]+) TO", g).group(1) for g in grants}  # type: ignore[union-attr]
        assert granted == {t for t in _tables(body) if t.startswith("pub_")}, name


def test_p5_value_tables_carry_source_quality_and_loader() -> None:
    for table, cols in _all_p5_tables().items():
        if table.endswith("series_meta"):
            assert {"source", "loaded_by", "updated_at"} <= set(cols), table
            continue
        assert {"source", "quality", "loaded_by"} <= set(cols), table
        assert {"received_at", "computed_at"} & set(cols), table


def test_p5_keys_hypertables_and_retention_match_the_design() -> None:
    body = "\n".join(_body(n) for n in P5_FILES)

    def pk(table: str) -> str:
        m = re.search(rf"CREATE TABLE IF NOT EXISTS {re.escape(table)} \((.*?)\n\);", body, re.S)
        assert m is not None, table
        inline = re.search(r"^\s*(\w+)\s+\w+ PRIMARY KEY", m.group(1), re.M)
        if inline:
            return inline.group(1)
        found = re.search(r"PRIMARY KEY \(([^)]*)\)", m.group(1))
        assert found is not None, table
        return found.group(1)

    assert pk("pub_macro.series") == "series_id, period"
    assert pk("prv_macro.series") == "series_id, period"
    assert pk("pub_macro.calendar") == "event_id"
    assert pk("prv_market.us_universe") == "symbol, as_of"
    for t in P5_EXPECT["0016_market_stats.sql"]:
        assert pk(t) == "stat_date, item", t
    assert pk("prv_flows.program_market_daily") == "trade_date, market, venue, kind, side"
    assert pk("prv_flows.program_market_intraday") == "ts, market, venue, kind, side"
    assert pk("prv_flows.program_investor_daily") == "trade_date, market, venue, investor, kind"
    assert pk("prv_flows.program_stock_intraday") == "ts, code, venue"
    assert pk("prv_flows.program_stock_daily") == "trade_date, code, venue"
    hyper = re.findall(
        r"create_hypertable\(\s*'([\w.]+)', by_range\('(\w+)', INTERVAL '([^']+)'", body
    )
    assert hyper == [
        ("prv_flows.program_market_intraday", "ts", "1 day"),
        ("prv_flows.program_stock_intraday", "ts", "1 day"),
        ("prv_flows.program_stock_daily", "trade_date", "365 days"),
    ]
    retention = re.findall(r"add_retention_policy\(\s*'([\w.]+)', INTERVAL '([^']+)'", body)
    assert retention == [
        ("prv_flows.program_market_intraday", "90 days"),
        ("prv_flows.program_stock_intraday", "90 days"),
    ]
    for kind_col in ("kind", "side"):
        found = set(
            re.findall(rf"\b{kind_col}\s+text NOT NULL CHECK \({kind_col} IN \(([^)]*)\)\)", body)
        )
        assert len(found) == 1, kind_col  # 다섯 표가 같은 목록


def test_p5_enum_checks_match_rows() -> None:
    """DB CHECK 의 값 목록 = kbj.core.p5_rows 의 열거(같은 말을 쓴다)."""
    from kbj.core import p5_rows as p5

    body = "\n".join(_body(n) for n in P5_FILES)

    def values(col: str) -> set[str]:
        found = re.findall(rf"\b{col}\s+text NOT NULL CHECK \({col} IN \(([^)]*)\)\)", body)
        assert found, col
        return {v for f in found for v in re.findall(r"'([\w]+)'", f)}

    assert values("kind") == set(p5.PROGRAM_KINDS)
    assert values("side") == set(p5.PROGRAM_SIDES)
    assert values("scope") == set(p5.PROGRAM_SCOPES)
    assert values("market") == set(p5.PROGRAM_MARKETS)
    assert values("freq") == set(p5.MACRO_FREQS)
    m = re.search(r"source\s+text NOT NULL CHECK \(source IN \('bok'([^)]*)\)\)", body)
    assert m is not None
    assert {"bok", *re.findall(r"'(\w+)'", m.group(1))} == set(p5.PUBLIC_CALENDAR_SOURCES)
    m = re.search(r"CHECK \(source IN \('ECOS'([^)]*)\)\)", body)
    assert m is not None
    assert {"ECOS", *re.findall(r"'(\w+)'", m.group(1))} == set(p5.PUBLIC_MACRO_SOURCES)
    assert "'us_summary', 'us_brief'" in body and set(p5.US_ARTIFACTS) == {"us_summary", "us_brief"}


# ── P6 마이그레이션 0019(docs/p6_design.md §3.3, D-P6-3 — 공개 pub_trade) ──

P6_FILE = "0019_trade.sql"
P6_TABLES = {
    "pub_trade.ten_day", "pub_trade.ten_day_columns", "pub_trade.item_monthly",
    "pub_trade.country_monthly", "pub_trade.item_country_monthly", "pub_trade.sigungu_monthly",
    "pub_trade.hs_dim", "pub_trade.surge", "pub_trade.item_group_monthly",
    "pub_trade.stock_map", "pub_trade.stock_export",
}  # fmt: skip


def test_p6_migration_is_0019_after_idx() -> None:
    names = sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    assert "0018_index.sql" in names
    assert names[names.index("0018_index.sql") + 1] == P6_FILE


def test_p6_statements_are_idempotent_and_only_create_or_grant() -> None:
    body = _body(P6_FILE)
    assert not re.search(r"CREATE (SCHEMA|TABLE|EXTENSION)(?! IF NOT EXISTS)", body)
    assert not re.search(r"CREATE (UNIQUE )?INDEX(?! IF NOT EXISTS)", body)
    starts = {s.strip().split(maxsplit=1)[0].upper() for s in body.split(";") if s.strip()}
    assert starts <= {"CREATE", "GRANT"}, starts  # ALTER·DROP·DO 없음
    assert "create_hypertable" not in body  # 월 단위 — #57 뒤 다시 본다
    assert not re.search(r"\bprv_|\bpublic\.", body)  # 공개 스키마에만


def test_p6_tables_match_rows_and_carry_source_quality() -> None:
    from kbj.core import trade_rows as tr

    tables = _tables(_body(P6_FILE))
    assert set(tables) == P6_TABLES == set(tr.TRADE_TABLES)
    earlier = (
        set(_all_p2_tables()) | set(_all_p3_tables()) | set(_all_p4_tables())
        | set(_all_p5_tables())
    )  # fmt: skip
    assert not set(tables) & earlier
    for table, cols in tables.items():
        assert not LOGIN_ONLY_COLS & set(cols), (table, LOGIN_ONLY_COLS & set(cols))
        assert "loaded_by" in cols, table
        if table != "pub_trade.ten_day_columns":
            assert {"source", "as_of", "quality"} <= set(cols), table
            assert {"received_at", "computed_at"} & set(cols), table
    # 시세·관심종목 흔적 칸 없음(공개 — D-P6-4·14)
    assert "close" not in tables["pub_trade.stock_export"]
    assert not {"is_watch", "watchlist"} & set(tables["pub_trade.stock_map"])


def test_p6_keys_and_grants_match_the_design() -> None:
    body = _body(P6_FILE)

    def pk(table: str) -> str:
        m = re.search(rf"CREATE TABLE IF NOT EXISTS {re.escape(table)} \((.*?)\n\);", body, re.S)
        assert m is not None, table
        inline = re.search(r"^\s*(\w+)\s+\w+ PRIMARY KEY", m.group(1), re.M)
        if inline:
            return inline.group(1)
        found = re.search(r"PRIMARY KEY \(([^)]*)\)", m.group(1))
        assert found is not None, table
        return found.group(1)

    assert pk("pub_trade.ten_day") == "kind, ym, seg, col_no"
    assert pk("pub_trade.ten_day_columns") == "kind, col_no, valid_from"
    assert pk("pub_trade.item_monthly") == "ym, hs"
    assert pk("pub_trade.country_monthly") == "ym, country"
    assert pk("pub_trade.item_country_monthly") == "ym, country, hs"
    assert pk("pub_trade.sigungu_monthly") == "ym, sido_cd, sgg_nm, hs6"
    assert pk("pub_trade.hs_dim") == "hs"
    assert pk("pub_trade.surge") == "ym, level, key, flow"
    assert pk("pub_trade.item_group_monthly") == "ym, group_id"
    assert pk("pub_trade.stock_map") == "code, hs, flow, region_id"
    assert pk("pub_trade.stock_export") == "code, period"
    stmts = [s.strip() for s in body.split(";")]
    grants = [s for s in stmts if "kbj_public_export" in s]
    for g in grants:
        assert re.fullmatch(r"GRANT SELECT ON pub_trade\.[a-z_]+ TO kbj_public_export", g), g
    granted = {re.search(r"ON ([\w.]+) TO", g).group(1) for g in grants}  # type: ignore[union-attr]
    assert granted == P6_TABLES


def test_p6_enum_checks_match_rows_and_values_are_null_not_zero() -> None:
    from kbj.core import trade_rows as tr

    body = _body(P6_FILE)

    def values(col: str) -> set[str]:
        found = re.findall(rf"\b{col}\s+text NOT NULL CHECK \({col} IN \(([^)]*)\)\)", body)
        assert found, col
        return {v for f in found for v in re.findall(r"'([\w]+)'", f)}

    assert values("kind") == set(tr.TEN_DAY_KINDS)
    assert values("flow") == set(tr.FLOWS)
    assert values("level") == set(tr.SURGE_LEVELS)
    assert values("rule") == set(tr.SURGE_RULES)
    assert values("origin") == set(tr.MAP_ORIGINS)
    assert values("status") == set(tr.STOCK_EXPORT_STATUSES)
    assert values("mode") == set(tr.ITEM_GROUP_MODES)
    assert "DEFAULT 0" not in body
    # 금액 칸은 NULL 허용(NOT NULL 없음)
    assert not re.search(r"\b(usd|exp_usd|imp_usd|export_usd)\s+numeric\(20, 0\) NOT NULL", body)
    assert body.count("OR quality <> 'ok')") >= 7


# ── P7 마이그레이션 0020(docs/p7_design.md §3.3 — 공개 pub_diag·로그인 prv_diag) ──

P7_FILE = "0020_diag.sql"


def test_p7_migration_is_0020_after_p6() -> None:
    names = sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    assert names[names.index(P6_FILE) + 1] == P7_FILE


def test_p7_statements_are_idempotent_and_never_alter_gx_tables() -> None:
    body = _body(P7_FILE)
    assert not re.search(r"CREATE (SCHEMA|TABLE|EXTENSION)(?! IF NOT EXISTS)", body)
    starts = {s.strip().split(maxsplit=1)[0].upper() for s in body.split(";") if s.strip()}
    assert starts <= {"CREATE", "GRANT", "REVOKE", "ALTER", "COMMENT"}, starts
    # ALTER 는 기본 권한 한 줄뿐 — prv_gex 표 정의는 0006 그대로(체크섬)
    alters = [s.strip() for s in body.split(";") if s.strip().upper().startswith("ALTER")]
    assert alters == [
        "ALTER DEFAULT PRIVILEGES IN SCHEMA pub_diag GRANT SELECT ON TABLES TO kbj_public_export"
    ]
    assert "create_hypertable" not in body
    assert not re.search(r"\bDROP\b|\bINSERT\b|\bUPDATE\b", body)
    created = set(re.findall(r"CREATE SCHEMA IF NOT EXISTS (\w+)", body))
    assert created == {"pub_diag", "prv_diag"}
    assert re.search(r"REVOKE ALL ON SCHEMA pub_diag, prv_diag FROM PUBLIC", body)


def test_p7_tables_match_rows_and_tiers() -> None:
    from kbj.core import p7_rows as pr

    tables = _tables(_body(P7_FILE))
    assert set(tables) == set(pr.DIAG_PUB_TABLES) | set(pr.DIAG_PRV_TABLES)
    earlier = (
        set(_all_p2_tables()) | set(_all_p3_tables()) | set(_all_p4_tables())
        | set(_all_p5_tables()) | set(_tables(_body(P6_FILE)))
    )  # fmt: skip
    assert not set(tables) & earlier
    for table, cols in tables.items():
        assert {"source", "as_of", "quality", "loaded_by"} <= set(cols), table
        assert {"received_at", "computed_at"} & set(cols), table
        if table.startswith("pub_"):
            assert not LOGIN_ONLY_COLS & set(cols), table
    # 공개 지표·로그인 지표는 같은 모양(§3.3)
    assert tables["pub_diag.indicator"] == tables["prv_diag.indicator"]


def test_p7_grants_and_enum_checks_match_rows() -> None:
    from kbj.core import p7_rows as pr

    body = _body(P7_FILE)
    stmts = [s.strip() for s in body.split(";") if "kbj_public_export" in s]
    selects = {
        m.group(1)
        for s in stmts
        if (m := re.fullmatch(r"GRANT SELECT ON ([\w.]+) TO kbj_public_export", s))
    }
    assert selects == set(pr.DIAG_PUB_TABLES)
    for s in stmts:
        assert "prv_" not in s, s
        assert not re.search(r"INSERT|UPDATE|DELETE|ALL PRIVILEGES", s), s

    def values(col: str) -> set[str]:
        found = re.findall(rf"\b{col}\s+text (?:NOT NULL )?CHECK \({col} IN \(([^)]*)\)\)", body)
        assert found, col
        return {v for f in found for v in re.findall(r"'([\w]+)'", f)}

    assert values("state") == set(pr.DIAG_STATES)
    assert values("band") == set(pr.FEAR_GREED_BANDS)
    assert values("label") == set(pr.VERDICT_LABELS)
    assert values("kind") == set(pr.EVENT_KINDS)
    assert "DEFAULT 0" not in body
    assert body.count("OR quality <> 'ok')") >= 6


def test_p7_owner_comments_cover_every_gex_engine_table() -> None:
    raw = (MIGRATIONS / P7_FILE).read_text(encoding="utf-8")
    gx = {t for t in _tables(_body("0006_gex.sql")) if t.startswith("prv_gex.")}
    commented = set(re.findall(r"COMMENT ON TABLE ([\w.]+) IS", raw))
    # investor_flow 는 0017 이 적었다
    assert gx - {"prv_gex.investor_flow"} <= commented
    assert "prv_gex.investor_flow" not in commented


# ── P8 마이그레이션 0021(docs/p8_design.md §3.3 — 규칙 알림·백테스트·저널, 전부 로그인) ──

P8_FILE = "0021_rules_journal.sql"


def test_p8_migration_is_0021_after_p7() -> None:
    names = sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    assert names[names.index(P7_FILE) + 1] == P8_FILE


def test_p8_statements_are_idempotent_and_never_alter_or_write() -> None:
    body = _body(P8_FILE)
    assert not re.search(r"CREATE (SCHEMA|TABLE|INDEX|EXTENSION)(?! IF NOT EXISTS)", body)
    assert not re.search(r"CREATE VIEW", body)  # 뷰는 CREATE OR REPLACE
    starts = {s.strip().split(maxsplit=1)[0].upper() for s in body.split(";") if s.strip()}
    assert starts <= {"CREATE", "SELECT"}, starts
    assert not re.search(r"\bALTER\b|\bDROP\b|\bINSERT\b|\bUPDATE\b|\bGRANT\b", body)
    # 새 스키마 없음 — prv_alerts(0001)·prv_journal(0013)
    assert "CREATE SCHEMA" not in body
    assert "create_hypertable('prv_alerts.watch_quote_slot'" in body
    assert re.search(
        r"add_retention_policy\('prv_alerts.watch_quote_slot', INTERVAL '730 days'", body
    )
    assert "if_not_exists => TRUE" in body
    assert "DEFAULT 0" not in body


def test_p8_tables_match_rows_writers_and_tiers() -> None:
    from kbj.core import rules_rows as rr

    tables = _tables(_body(P8_FILE))
    assert set(tables) == set(rr.P8_TABLE_WRITERS)
    assert all(t.startswith(("prv_alerts.", "prv_journal.")) for t in tables)
    earlier = (
        set(_all_p2_tables()) | set(_all_p3_tables()) | set(_all_p4_tables())
        | set(_all_p5_tables()) | set(_tables(_body(P6_FILE))) | set(_tables(_body(P7_FILE)))
    )  # fmt: skip
    assert not set(tables) & earlier
    # 수치 행은 source·quality(절대 규칙 1)
    for t in (
        "prv_alerts.watch_quote_slot", "prv_alerts.signal_intraday", "prv_alerts.signal_close",
        "prv_alerts.signal_outcome", "prv_alerts.backtest_run", "prv_alerts.match_check",
    ):  # fmt: skip
        assert {"source", "quality"} <= set(tables[t]), t
    # 장중·마감 신호 표는 같은 모양(뷰 UNION ALL)
    assert tables["prv_alerts.signal_intraday"] == tables["prv_alerts.signal_close"]
    raw = (MIGRATIONS / P8_FILE).read_text(encoding="utf-8")
    assert "CREATE OR REPLACE VIEW prv_alerts.signal AS" in raw
    assert "kbj_public_export" not in _body(P8_FILE)


def test_p8_enum_checks_match_rows() -> None:
    from kbj.core import rules_rows as rr

    body = _body(P8_FILE)

    def values(col: str) -> set[str]:
        found = re.findall(rf"\b{col}\s+text (?:NOT NULL )?CHECK \({col} IN \(([^)]*)\)\)", body)
        assert found, col
        return {v for f in found for v in re.findall(r"'([\w:]+)'", f)}

    assert values("status") == set(rr.SIGNAL_STATUSES)
    assert values("skip_reason") == set(rr.SKIP_REASONS)
    assert values("reason") == set(rr.OUTCOME_REASONS)
    assert values("mode") == set(rr.BACKTEST_MODES)
    assert values("side") == set(rr.TRADE_SIDES)
    sources = re.findall(r"\bsource\s+text NOT NULL CHECK \(source IN \(([^)]*)\)\)", body)
    got = [set(re.findall(r"'([\w:]+)'", s)) for s in sources]
    assert set(rr.RULE_SOURCES) in got and set(rr.JOURNAL_SOURCES) in got
    assert body.count("OR quality <> 'ok')") >= 2
