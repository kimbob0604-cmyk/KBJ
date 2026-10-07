# KBJ 비밀·환경변수 이름표

- 작성: 2026-10-06(P1). 근거: `docs/inventory.md` (f) 환경변수 통합 표, `docs/DATA_TIERS.md` §1, `docs/adr/0001-p0-decisions.md` U1.
- **이름만 적는다. 값은 어디에도 적지 않는다**(레포·문서·이슈·로그·텔레그램). 값은 로컬 `.env`(git 제외)와 운영 VM 환경에만 둔다.
- 코드는 `kbj/config/settings.py` 의 `Settings` 하나로만 읽는다(`KBJ_` 접두사, 비밀은 `SecretStr`). 로그·오류 문구에 내보낼 때는 `kbj/core/masking.py` 를 거친다(절대 규칙 5).
- 빈 값은 '설정 안 함'(기본값)으로 읽는다. 비밀이 아닌 튜닝값(호출 상한·주기·발송 규칙)은 환경변수가 아니라 `config/*.yaml` 로 둔다.
- 등급: **공개** = 공개 등급 데이터를 받는 키(DATA_TIERS §1), **로그인** = 로그인 등급 데이터를 받는 키, **운영** = 데이터 출처가 아닌 운영 비밀, **설정** = 비밀 아님.

## 1. 이름표

| 이름 | 용도 | 발급처 | 등급 | 읽는 곳 |
|---|---|---|---|---|
| `KBJ_KIS_APP_KEY` | KIS 앱키 | 한국투자증권 KIS Developers | 로그인 | services.auth **만**(U1) |
| `KBJ_KIS_APP_SECRET` | KIS 앱시크릿 | 같음 | 로그인 | services.auth **만** |
| `KBJ_KIS_ENV` | `real` / `vts`(모의) | — | 설정 | services.auth |
| `KBJ_KRX_API_KEY` | KRX OpenAPI 일별 시세·선물옵션(하루 한도 공유) | KRX 정보데이터시스템 OpenAPI | 로그인 | kbj.data.private.krx |
| `KBJ_DART_API_KEY` | DART 공시·재무 | OpenDART(금융감독원) — 1인 1키 | 공개 | kbj.data.public.dart |
| `KBJ_DATAGO_KEY` | 공공데이터포털: 관세청 수출입·금투협 종합통계(·금융위 시세는 로그인 데이터) | 공공데이터포털 활용신청(DATA_TIERS §5 목록) | 공개 | kbj.data.public.customs·fsc_kofia_stats, kbj.data.private.fsc_* |
| `KBJ_KOSIS_KEY` | 통계청 KOSIS | KOSIS 공유서비스 | 공개 | kbj.data.public.kosis |
| `KBJ_ECOS_KEY` | 한국은행 ECOS(이용기간 2년) | 한국은행 ECOS — 이용형태 '비영리' | 공개(한은 작성 표만) | kbj.data.public.ecos |
| `KBJ_FRED_KEY` | FRED | St. Louis Fed FRED | 공개(정부 시리즈)·로그인(저작권 시리즈) | kbj.data.public.fred_gov |
| `KBJ_FINNHUB_KEY` | Finnhub | Finnhub | 로그인 | kbj.data.private |
| `KBJ_NAVER_SEARCH_ID` | 네이버 검색 API(뉴스) 클라이언트 ID | 네이버 개발자센터 | 로그인 — 대체 후 삭제 후보(U4) | kbj.data.private.naver_search |
| `KBJ_NAVER_SEARCH_SECRET` | 같은 API 시크릿 | 같음 | 로그인 | 같음 |
| `KBJ_ANTHROPIC_API_KEY` | 요약·문장화(계산 금지 — 절대 규칙 3). SDK 에 명시 전달 | Anthropic Console | 운영 | 리포트·브리핑 초안 |
| `KBJ_TELEGRAM_BOT_TOKEN` | 텔레그램 봇 1개(D5) | BotFather | 운영 | services.notifier **만**(U1) |
| `KBJ_TELEGRAM_CHAT_ID` | 슈퍼그룹 1개(토픽으로 나눈다) | 텔레그램 | 운영 | services.notifier |
| `KBJ_TELEGRAM_INBOX_CHAT_IDS` | 인박스 명령을 받을 대화(쉼표 구분) | 텔레그램 | 운영 | services.notifier |
| `KBJ_TELEGRAM_WEBHOOK_SECRET` | `setWebhook` 의 secret_token(수신 웹훅 검증) | 직접 생성(무작위) | 운영 | services.notifier |
| `KBJ_NOTIFY_ENABLED` | 발송 켜기(기본 false) | — | 설정 | services.notifier |
| `KBJ_POSTGRES_PASSWORD` | db 컨테이너 비밀번호(compose 가 요구, 기본값 없음) | 직접 생성(영숫자) | 운영 | docker-compose.yml |
| `KBJ_REDIS_PASSWORD` | redis 컨테이너 비밀번호(compose 가 요구, 기본값 없음) | 직접 생성(영숫자) | 운영 | docker-compose.yml |
| `KBJ_DATABASE_URL` | Postgres 접속 문자열(비밀번호 포함) | — | 운영 | kbj.store |
| `KBJ_REDIS_URL` | Redis 접속 문자열(비밀번호 포함) | — | 운영 | 토큰 캐시·레이트리미터·pub/sub |
| `KBJ_DATA_DIR` | 실행 산출물 루트(기본 `state`) [확인 필요] | — | 설정 | — |
| `KBJ_SPOOL_DIR` | DB 장애 동안 쓰기 묶음 디스크 큐(기본 `state/spool`) | — | 설정 | kbj.store |
| `KBJ_SPOOL_MAX_MB` | 디스크 큐 상한(서비스 하나, 기본 1024) | — | 설정 | kbj.store |
| `KBJ_API_HOST` | API 바인딩 주소(기본 127.0.0.1) | — | 설정 | services.api |
| `KBJ_API_PORT` | API 포트(기본 8000) | — | 설정 | services.api |
| `KBJ_PUBLIC_BASE_URL` | 텔레그램 웹훅 주소의 앞부분(운영 VM 도메인) — 주소는 레포에 적지 않는다 | — | 운영 | services.notifier |
| `KBJ_GIT_COMMIT` | 배포 커밋 표시 | CI | 설정 | services.api |
| `KBJ_HEALTHCHECK_URL` | 외부 하트비트 주소(주소 자체가 비밀) [확인 필요: 서비스 미정] | 하트비트 서비스 | 운영 | scheduler |
| `KBJ_PROBE_OUT_DIR` | 실측 스크립트 출력 폴더(기본 `probe_out`, gitignore) | — | 설정 | scripts |
| `KBJ_TEST_TIMESCALE_IMAGE` | 통합 시험용 Timescale 이미지 | — | 설정 | tests(integration) |
| `KBJ_LIVE_TRADING` | 실전 주문 스위치. **기본 false, 사용자 승인 전엔 바꾸지 않는다**(절대 규칙 6) | — | 설정 | — (주문 코드 없음) |

## 2. 옛 이름 → 새 이름 (`docs/inventory.md` (f))

| 옛 이름 | 쓰던 곳 | 새 이름 | 비고 |
|---|---|---|---|
| `KIS_APP_KEY`, `KIS_APP_SECRET` | SD·ET·GX | `KBJ_KIS_APP_KEY`, `KBJ_KIS_APP_SECRET` | auth 만 읽는다. 옛 레포 시크릿(ET 워크플로 5곳 + GX 1곳)은 지운다 |
| `KIS_ENV` | ET·GX | `KBJ_KIS_ENV` | — |
| `KIS_ACCOUNT` | ET `.env.example` 만 | 삭제 | 코드 미사용 |
| `KIS_DEMO_APP_KEY`, `KIS_DEMO_APP_SECRET`, `KIS_DEMO_ACCOUNT` | GX `.env.example` 만 | 보류 | 주문 코드는 승인 전 만들지 않는다 |
| `KIS_TOKEN_CACHE_PATH` | GX | 삭제 | 토큰은 Redis 에만 |
| `KRX_API_KEY` | SD·ET·GX | `KBJ_KRX_API_KEY` | — |
| `KRX_API_BASE`, `KRX_DAILY_CALL_CAP` | SD·GX | `config/krx.yaml` | 비밀 아님 |
| `KRX_ID`, `KRX_PW` | ET monitor/flow·flowlab | 삭제 | pykrx 1.2.x 가 import 때 같은 이름으로 자동 로그인한다(conflict_map E5) |
| `DART_API_KEY` | SD·ET | `KBJ_DART_API_KEY` | — |
| `DATAGO_KEY`, `DATA_GO_KR_KEY` | ET | `KBJ_DATAGO_KEY` | 두 벌을 하나로 |
| `KOSIS_API_KEY` | ET(감사 목록에만) | `KBJ_KOSIS_KEY` | — |
| `ECOS_API_KEY` | ET bok | `KBJ_ECOS_KEY` | — |
| `FRED_API_KEY` | ET bok | `KBJ_FRED_KEY` | — |
| `FINNHUB_API_KEY` | SD | `KBJ_FINNHUB_KEY` | — |
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | SD·ET | `KBJ_NAVER_SEARCH_ID`, `KBJ_NAVER_SEARCH_SECRET` | U4 |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` | ET | `KBJ_ANTHROPIC_API_KEY` | — |
| `BSKY_HANDLE`, `BSKY_APP_PASSWORD`, `BSKY_PROBE_SPEC` | ET | 삭제 | — |
| `TELEGRAM_BOT_TOKEN` | SD·ET·GX | `KBJ_TELEGRAM_BOT_TOKEN` | notifier 만 읽는다 |
| `TELEGRAM_CHAT_ID` | SD·ET·GX | `KBJ_TELEGRAM_CHAT_ID` | 지금 bok 은 쉼표 다중, 나머지는 단일 → 슈퍼그룹 1개 |
| `XDIGEST_CHAT_ID` | ET | 삭제 | 토픽으로 흡수 |
| `TRIGGER_INBOX_CHAT_IDS` | ET | `KBJ_TELEGRAM_INBOX_CHAT_IDS` | — |
| (SD 봇 토큰 해시 파생 시크릿) | SD | `KBJ_TELEGRAM_WEBHOOK_SECRET` | 신규 |
| `TELEGRAM_ENABLED`, `WATCHDOG_TELEGRAM`, `BOARD_SEND` | SD·ET | `KBJ_NOTIFY_ENABLED` + `config/notify.yaml` | — |
| `POSTGRES_PASSWORD`, `DATABASE_URL`, `REDIS_URL` | GX | `KBJ_POSTGRES_PASSWORD`, `KBJ_DATABASE_URL`, `KBJ_REDIS_URL` | — |
| (없음 — GX redis 는 비밀번호 없이 내부망만) | — | `KBJ_REDIS_PASSWORD` | 신규(P1). compose 가 포트를 127.0.0.1 에 열기 때문 |
| `SPOOL_DIR`, `SPOOL_MAX_MB` | GX | `KBJ_SPOOL_DIR`, `KBJ_SPOOL_MAX_MB` | — |
| `LIVE_TRADING` | GX | `KBJ_LIVE_TRADING` | 기본 false |
| `PROBE_OUT_DIR`, `GEXLAB_TEST_TIMESCALE_IMAGE`, `HEALTHCHECK_URL` | GX | `KBJ_PROBE_OUT_DIR`, `KBJ_TEST_TIMESCALE_IMAGE`, `KBJ_HEALTHCHECK_URL` | — |
| `PORT`, `HOST` | SD | `KBJ_API_PORT`, `KBJ_API_HOST` | — |
| `RENDER_EXTERNAL_URL`, `DASHBOARD_URL` | SD | `KBJ_PUBLIC_BASE_URL` | Render 는 쓰지 않는다(D2) |
| `RENDER_GIT_COMMIT`, `GIT_COMMIT` | SD | `KBJ_GIT_COMMIT` | — |
| `RENDER`, `RENDER_GIT_BRANCH`, `PYTHON_VERSION` | SD | 삭제 | — |
| `BOARD_DB`, `BOARD_BACKTEST_DB`, `BOARD_SITE`, `BOARD_DEMO_SITE`, `US_DB`, `US_OUT`, `US_BACKTEST_DB`, `SD_DIR` | ET | `KBJ_DATA_DIR` 하나 | — |
| `GITHUB_TOKEN`, `BACKUP_GIST_ID` | SD `db_backup.py` | 삭제 | Gist 백업 폐기, `GITHUB_TOKEN` 은 Actions 자동 주입 이름과 겹친다 |
| `GH_TOKEN`, `REPO`, `DRY_RUN`, `ALREADY_SENT`, `MODE` | ET 워크플로 | 삭제(CI 내부) | — |
| `USE_SQLITE`, `DISABLE_AUTO_FETCH`, `SERVER_NO_STARTUP` | SD | 삭제(legacy 시험에서만 `SERVER_NO_STARTUP`) | — |
| `TZ` | SD·GX | `TZ=UTC` 고정 | 코드에서 KST 로 바꾼다 |
| `MIN_FUNDS`, `ACTION_PP`, `KEEP_DAYS`, `WORKERS`, `QTY_FLOOR`, `RETRIES`, `EMPTY_LIMIT` | ET ETF | `config/etf.yaml` | 비밀 아님 |
| `STOCKS`, `YEAR_FROM`, `FS_DIV`, `DIAGNOSE`, `DART_STOCKS` | ET `report.yml` | CLI 인자·`config/reports.yaml` | — |
| `DASH_URL`, `PYTHON_BIN`, `PYTHONIOENCODING`, `NOTION_TOKEN`, `NOTION_DATABASE_ID` | ET | 삭제 | — |

legacy 코드는 옮겨 온 그대로 옛 이름을 읽는다(P1 은 경로·import 만 고친다). 새 이름으로 바꾸는 것은 각 영역을 정본으로 갈아 끼우는 단계(P2~)에서 한다.

## 3. 키가 샜을 때

1. 그 키를 발급처에서 바로 폐기·재발급한다(KIS 는 앱키 재발급, 텔레그램은 BotFather `/revoke`).
2. 커밋에 들어갔다면 이력을 고치기 전에 **먼저 재발급**한다 — 공개 레포는 이미 복제됐다고 본다.
3. `scripts/check_public_safety.py` 가 잡지 못한 형태였다면 `kbj/core/masking.py` 의 `SECRET_SHAPES` 에 형태를 더한다(검사와 마스킹이 같은 목록을 쓴다).
