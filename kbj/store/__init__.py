"""저장 — PostgreSQL + TimescaleDB 하나(PLAN §2), 스키마·마이그레이션·Redis 키를 한 곳에 둔다.

- 스키마 이름: 공개 등급 `pub_<도메인>`, 로그인 등급 `prv_<도메인>`, 운영 `ops`
  (docs/adr/0002-tier-schemas.md, docs/conflict_map.md §1.5).
- 마이그레이션: `migrations/NNNN_이름.sql`. 0001 은 스키마·역할만 만든다(표는 P2 부터).
  적용기는 P2 에 GEXLAB `db/migrate.py`(번호·체크섬·advisory lock)를 승계해
  `ops.schema_migrations` 에 기록한다. 로컬 compose 는 0001 을 initdb 로 한 번 적용한다
  (docker-compose.yml).
"""
