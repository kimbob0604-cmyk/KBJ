"""DB 스키마 마이그레이션 0001 과 docker-compose.yml 의 구조 검사(정적 — DB·데몬 없이).

ADR 0002: 공개 등급은 pub_*, 로그인 등급은 prv_*, 운영은 ops. kbj_public_export 는 pub_* 에만
USAGE·SELECT. compose 는 비밀번호 기본값이 없고 포트는 127.0.0.1 에만 연다.
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
