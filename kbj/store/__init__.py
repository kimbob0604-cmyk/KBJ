"""저장 — PostgreSQL + TimescaleDB 하나(PLAN §2), 스키마·마이그레이션·Redis 키를 한 곳에 둔다.

- 스키마 이름: 공개 등급 `pub_<도메인>`, 로그인 등급 `prv_<도메인>`, 운영 `ops`
  (docs/adr/0002-tier-schemas.md, docs/conflict_map.md §1.5).
- 마이그레이션: `migrations/NNNN_이름.sql`. 0001 은 스키마·역할, 0002~0006 은 P2 표
  (docs/p2_design.md §8.2). 적용기 `python -m kbj.store.migrate`(GEXLAB `db/migrate.py` 승계 —
  번호·체크섬·advisory lock)가 `ops.schema_migrations` 에 기록한다. 로컬 compose 는 0001 을
  initdb 로 한 번 적용한다(docker-compose.yml) — 적용기가 다시 돌려도 멱등이다.
- `db.connect(settings)` — 접속(UTC·application_name·search_path, 오류 문구에 접속 정보 없음).
  `spool.DiskSpool` — DB 장애 동안 쓰기 묶음 디스크 큐(GEXLAB `data/spool.py` 그대로).
- 옛 SQLite·JSON 이관: `python -m kbj.store.legacy_import`(§8.6 — 읽기 전용·멱등·검증).
"""
