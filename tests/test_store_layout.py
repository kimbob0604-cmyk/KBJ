"""DB 스키마 마이그레이션 0001~0006 과 docker-compose.yml 의 구조 검사(정적 — DB·데몬 없이).

ADR 0002: 공개 등급은 pub_*, 로그인 등급은 prv_*, 운영은 ops. kbj_public_export 는 pub_* 에만
USAGE·SELECT. compose 는 비밀번호 기본값이 없고 포트는 127.0.0.1 에만 연다.
P2(docs/p2_design.md §8.2·§8.8): 0002~0006 은 파일 이름·멱등 문장·스키마 접두사를 지키고,
pub_* 표에는 로그인 등급 출처의 값 열이 없으며, GX 표(0002 의 session_log·health_events, 0006)는
GEXLAB db/migrations 001~004 의 열을 그대로 갖는다.
실제 적용은 tests/integration/test_migrations_pg.py.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

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
    for svc in services.values():
        for port in svc.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), port
        assert "healthcheck" in svc
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
