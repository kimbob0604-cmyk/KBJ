# ADR 0002 — 등급별 DB 스키마(pub_·prv_)와 공개 내보내기 역할 (2026-10-06)

상태: 확정(P1). 근거: `docs/DATA_TIERS.md` §3·§4, `docs/conflict_map.md` §1.5, `docs/PLAN.md` §2(DB 행).
구현: `kbj/store/migrations/0001_schemas.sql`, `docker-compose.yml`, 시험 `tests/test_store_layout.py`.

## 1. 맥락

- DATA_TIERS §3 은 공개/로그인을 실행 중 검사가 아니라 **구조로** 나누라고 한다: 수집기 폴더(`kbj/data/public` 대 `kbj/data/private`), DB(`public` 대 `private` 스키마), 배포(Pages 대 VM API).
- PLAN §2 는 DB 를 PostgreSQL + TimescaleDB 하나로 합치고 도메인별 스키마(`market`·`flows`·`filings`·`fin`·`trade`·`gex`·`board`·`alerts`·`ops`)를 두자고 한다.
- 두 안을 그대로 합치면 문제가 둘이다. (1) DATA_TIERS 원문의 `public` 은 Postgres 기본 스키마 이름이라 확장·`search_path` 기본값과 겹친다. (2) 한 도메인 안에 두 등급이 섞인다 — 재무(`fin`)는 DART 만으로 만든 값(공개)과 시세가 들어간 PER·PBR·컨센서스(로그인)가 같이 있다.

## 2. 결정

### 2.1 스키마 이름 = 등급 접두사 + 도메인

| 접두사 | 등급 | 스키마 (0001) |
|---|---|---|
| `pub_` | 공개 (DATA_TIERS §1 "공개") | `pub_filings`(DART 공시·잠정실적·오버행·corp_code) · `pub_fin`(DART 재무만으로 만든 값) · `pub_trade`(관세청) · `pub_macro`(ECOS 한은 작성 표·KOSIS·FRED 정부·한은 보고서) · `pub_market_stats`(금투협 종합통계: 신용잔고·예탁금) · `pub_themes`(자체 분류 사전) |
| `prv_` | 로그인 | `prv_market` · `prv_flows` · `prv_board` · `prv_gex` · `prv_fin` · `prv_themes` · `prv_macro` · `prv_etf` · `prv_journal` · `prv_alerts` |
| (없음) | 운영 | `ops`(작업 실행 기록·health·수집 공백·`ops.schema_migrations`) |

- 표를 어느 스키마에 둘지는 **그 표에 들어가는 값 중 가장 높은 등급**으로 정한다. 공개 출처라도 로그인 출처 값이 한 열이라도 섞이면 `prv_` 다(예: 시세가 들어간 재무비율 → `prv_fin.ratio`).
- 알림(규칙·발송 이력·큐)은 개인 데이터라 `prv_alerts` 다. 작업 지시의 `alerts` 는 접두사 규칙에 따라 이 이름으로 둔다(conflict_map §1.5 와 같다).
- `ops` 는 데이터 출처가 아니라 접두사가 없지만 공개 내보내기에서 보이지 않는다(§2.2).
- 표별 이관 대상(원본 → `스키마.표`)은 conflict_map §1.5 표를 따른다. 표는 P2 부터 `0002_…` 로 만든다.

### 2.2 공개 내보내기는 `kbj_public_export` 역할로만

- `kbj_public_export` 는 로그인하지 않는 그룹 역할(NOLOGIN)이고 **`pub_*` 에만 `USAGE`·`SELECT`** 가 있다. `prv_*`·`ops` 에는 아무 권한이 없다. 쓰기(INSERT·UPDATE·DELETE)·생성 권한도 없다.
- 0001 을 적용한 역할(앱 소유자)이 앞으로 `pub_*` 에 만드는 표에도 `ALTER DEFAULT PRIVILEGES` 로 `SELECT` 만 자동으로 붙는다. 다른 역할이 `pub_*` 에 표를 만들면 붙지 않는다 — 마이그레이션은 앱 소유자 하나로만 돌린다.
- scheduler 의 공개 JSON 내보내기 작업(DATA_TIERS §3 "데이터 전달")은 이 역할의 구성원인 **별도 로그인 역할**로 접속한다. 그 로그인 역할과 비밀번호 변수는 내보내기 작업을 만드는 단계에서 정한다 [확인 필요: P2~P3]. 로그인 역할에는 `default_transaction_read_only = on` 을 건다.
- 코드 쪽 짝: 공개 내보내기 코드는 `kbj/data/public/` 만 import 한다 — import-linter 계약 ①(`pyproject.toml`). DB 권한과 import 규칙 두 겹으로 막는다.

### 2.3 마이그레이션 0001

- 스키마·역할·권한·`ops.schema_migrations`(GEXLAB `db/migrate.py` 와 같은 열)만 만든다. 표는 없다.
- **멱등**이다(`IF NOT EXISTS`, 역할 존재 검사, `GRANT`·`REVOKE` 반복 안전). 로컬 compose 는 빈 볼륨 첫 기동 때 initdb 로 적용하고, P2 적용기(GEXLAB `db/migrate.py` 승계 — 번호·체크섬·advisory lock)가 다시 적용해도 깨지지 않는다.
- 파일 이름은 GEXLAB 적용기 규칙(`NNN_소문자.sql`, 3자리 이상)을 따른다(`tests/test_store_layout.py` 가 고정).

## 3. 확인한 것 (2026-10-06)

- 로컬 PostgreSQL 16(TimescaleDB 없이 — `CREATE EXTENSION` 줄만 빼고)에 0001 을 두 번 적용: 두 번째도 오류 없음(멱등).
- `SET ROLE kbj_public_export` 후: `pub_trade` 표 읽기 성공, `prv_market`·`ops` 표 읽기는 `permission denied for schema`, `pub_trade` 표 쓰기·표 만들기도 거부. 0001 적용 뒤 소유자가 만든 `pub_*` 표도 읽힘(기본 권한).
- Docker 데몬이 없는 환경이라 Timescale 이미지로 실제 기동은 하지 않았다. `docker compose config -q` 문법 검사만 통과 [확인 필요: 첫 `docker compose up` 에서 initdb 순서(이미지의 `000_*`·`001_*` 다음 `100_kbj_*`) 확인].

## 4. 결과

- 공개 사이트로 나갈 데이터는 "어느 스키마에 있느냐"만 보면 된다. 실수로 `prv_` 표를 내보내려 하면 DB 가 거부한다.
- 대가: 같은 도메인이 스키마 두 개로 갈린다(`pub_fin`/`prv_fin`). 화면이 둘을 합쳐 보여 줄 때는 로그인 API 가 둘 다 읽는다(로그인 API 역할은 전부 읽는다).
- DATA_TIERS §3 표의 "`public` 스키마"·"`private` 스키마"는 각각 `pub_*`·`prv_*` 묶음을 뜻하는 것으로 읽는다.
