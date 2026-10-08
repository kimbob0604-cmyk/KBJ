# KBJ 비밀·환경변수 이름표

- 작성: 2026-10-06(P1). 고침: 2026-10-07(P2 S0 — `KBJ_SERVICE`·`KBJ_CONFIG_DIR` 추가, KIS 앱키 주입 범위를 ADR 0004 로 / P2 최종 점검 — 서비스별 주입 표, legacy 옛 이름 현황 / P3 묶음 M — 로그인 웹·공개 내보내기 이름 5개와 설정 4개, `docs/p3_design.md` §5.4·§7). 근거: `docs/inventory.md` (f) 환경변수 통합 표, `docs/DATA_TIERS.md` §1, `docs/adr/0001-p0-decisions.md` U1, `docs/adr/0004-kis-issuance.md`.
- **이름만 적는다. 값은 어디에도 적지 않는다**(레포·문서·이슈·로그·텔레그램). 값은 로컬 `.env`(git 제외)와 운영 VM 환경에만 둔다.
- 코드는 `kbj/config/settings.py` 의 `Settings` 하나로만 읽는다(`KBJ_` 접두사, 비밀은 `SecretStr`). 로그·오류 문구에 내보낼 때는 `kbj/core/masking.py` 를 거친다(절대 규칙 5).
- 빈 값은 '설정 안 함'(기본값)으로 읽는다. 비밀이 아닌 튜닝값(호출 상한·주기·발송 규칙)은 환경변수가 아니라 `config/*.yaml` 로 둔다.
- 등급: **공개** = 공개 등급 데이터를 받는 키(DATA_TIERS §1), **로그인** = 로그인 등급 데이터를 받는 키, **운영** = 데이터 출처가 아닌 운영 비밀, **설정** = 비밀 아님.

## 1. 이름표

| 이름 | 용도 | 발급처 | 등급 | 읽는 곳 |
|---|---|---|---|---|
| `KBJ_KIS_APP_KEY` | KIS 앱키 | 한국투자증권 KIS Developers | 로그인 | **발급: services.auth 만**(U1) / 요청 헤더: kbj.data.private.kis(KIS REST 를 부르는 프로세스 — §4, ADR 0004) |
| `KBJ_KIS_APP_SECRET` | KIS 앱시크릿 | 같음 | 로그인 | 같음(발급: services.auth 만 / 요청 헤더: kbj.data.private.kis) |
| `KBJ_KIS_ENV` | `real` / `vts`(모의) | — | 설정 | kbj.data.private.kis.credentials(auth·REST 호출 프로세스) |
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
| `KBJ_REDIS_URL` | Redis 접속 문자열(비밀번호 포함) | — | 운영 | 토큰 캐시·레이트리미터·일 예산·발송 outbox·하트비트·pub/sub(키 이름은 `kbj/store/redis_keys.py`) |
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
| `KBJ_SERVICE` | 이 프로세스의 서비스 이름(`auth`·`scheduler`·`notifier` …, 소문자). compose 가 서비스마다 넣는다. KIS 발급자(`kbj/services/auth/issuer.py`)는 `auth` 일 때만 만들어진다(ADR 0004 런타임 가드) | — | 설정 | kbj.config.settings, services.auth |
| `KBJ_CONFIG_DIR` | `config/*.yaml`(작업 등록부·호출 상한·발송 규칙·휴장 덮어쓰기) 폴더. 기본 `config` — 상대 경로는 레포 루트 기준, 설치 이미지에서는 절대 경로 | — | 설정 | kbj.config.files |
| `KBJ_WEB_USER` | 로그인 웹 사용자 이름(1명). 로그·화면에 남기지 않는다. **기본값 없음** — 비면 로그인 503(`login_not_configured`) | 직접 정함 | 운영 | services.api(P3) |
| `KBJ_WEB_PASSWORD_HASH` | 로그인 비밀번호의 scrypt 해시(`scrypt$n=…$r=…$p=…$<salt>$<dk>`). `python -m kbj.services.api hash-password` 가 stdout 에만 낸다 — 비밀번호 원문은 어디에도 두지 않는다. **기본값 없음** | 직접 생성 | 운영 | services.api(P3) |
| `KBJ_WEB_SESSION_TTL_H` | 세션 고정 만료(시간, 기본 12) | — | 설정 | services.api |
| `KBJ_WEB_COOKIE_SECURE` | 쿠키 Secure·`__Host-` 접두사(기본 true — TLS 역방향 프록시 뒤). false 는 `KBJ_API_HOST` 가 루프백일 때만(개발) | — | 설정 | services.api |
| `KBJ_WEB_DIST_DIR` | 로그인 SPA 빌드 폴더(기본 `web/dist-login`) — api 가 같은 출처로 내보낸다 | — | 설정 | services.api |
| `KBJ_PUBLIC_EXPORT_DATABASE_URL` | 공개 내보내기 전용 접속 문자열 — `kbj_public_export`(pub_* SELECT 만) 구성원 로그인 역할, `default_transaction_read_only=on`(ADR 0002 §2.2). 앱 DSN(`KBJ_DATABASE_URL`)은 쓰지 않는다 | 운영 배포에서 역할 생성 | 운영 | services.public_export(P3) |
| `KBJ_PUBLIC_PUSH_ENABLED` | public-data 브랜치 푸시·Pages 디스패치 켜기(기본 false). **[사용자 승인 필요]** | — | 설정 | services.public_export |
| `KBJ_PUBLIC_DEPLOY_KEY_PATH` | public-data 브랜치에 강제 푸시할 배포 키 **파일 경로**(키는 VM 에만, 레포·이미지에 없음). **[사용자 승인 필요]** — 발급·등록 전엔 비워 둔다 | GitHub 레포 Deploy keys(쓰기) | 운영 | services.public_export |
| `KBJ_GITHUB_DISPATCH_TOKEN` | `pages.yml` 수동 실행(workflow_dispatch) 토큰 — 이 레포 `actions:write` 만(세분 토큰). **[사용자 승인 필요]** | GitHub fine-grained PAT | 운영 | services.public_export |
| `KBJ_LIVE_TRADING` | 실전 주문 스위치. **기본 false, 사용자 승인 전엔 바꾸지 않는다**(절대 규칙 6) | — | 설정 | — (주문 코드 없음) |

## 2. 옛 이름 → 새 이름 (`docs/inventory.md` (f))

| 옛 이름 | 쓰던 곳 | 새 이름 | 비고 |
|---|---|---|---|
| `KIS_APP_KEY`, `KIS_APP_SECRET` | SD·ET·GX | `KBJ_KIS_APP_KEY`, `KBJ_KIS_APP_SECRET` | 발급은 auth 만, 요청 헤더는 KIS REST 를 부르는 프로세스(§4, ADR 0004). 옛 레포 시크릿(ET 워크플로 5곳 + GX 1곳)은 지운다 |
| `KIS_ENV` | ET·GX | `KBJ_KIS_ENV` | — |
| `KIS_ACCOUNT` | ET `.env.example` 만 | 삭제 | 코드 미사용 |
| `KIS_DEMO_APP_KEY`, `KIS_DEMO_APP_SECRET`, `KIS_DEMO_ACCOUNT` | GX `.env.example` 만 | 보류 | 주문 코드는 승인 전 만들지 않는다 |
| `KIS_TOKEN_CACHE_PATH` | GX | 삭제 | 토큰은 Redis 에만 |
| `KRX_API_KEY` | SD·ET·GX | `KBJ_KRX_API_KEY` | — |
| `KRX_API_BASE`, `KRX_DAILY_CALL_CAP` | SD·GX | 주소: `kbj.data.private.krx` 의 비공개 상수 / 하루 상한: `config/limits.yaml` 의 `krx.daily_cap`(기본 8,000 [확인 필요]) | 비밀 아님 |
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

**P2 뒤(2026-10-07)**: legacy 의 KIS·KRX·DART 호출은 논리 URL 브리지(`kbj/data/legacy_bridge.py`, ADR 0007)가, 텔레그램 발송·수신은 notifier shim(ADR 0005)이 `KBJ_*` 값으로 한다 — 호출자가 넘긴 옛 이름의 값은 버린다. legacy GX 설정의 `telegram_*`·`kis_token_cache_path` 필드는 지웠다. 남은 것: ET board ingest 의 단계 관문 몇 곳(`creds.has('KRX_API_KEY')`·`'KIS_APP_KEY'`·`'DART_API_KEY'`)과 SD `server.py` 의 텔레그램 cron 등록 조건(`TELEGRAM_BOT_TOKEN`)은 아직 옛 이름의 **유무**만 본다 — KBJ 환경에서는 그 단계를 건너뛴다(값은 읽지 않는다). P3 이전 때 KBJ 설정 확인으로 바꾼다.

P1 이식에서 개인·운영 정보를 코드에서 빼며 legacy 에 새로 생긴 이름(값은 레포 어디에도 적지 않는다 — 각 `legacy/<프로젝트>/MIGRATION.md`):

| 이름 | 쓰는 곳 | 무엇 | 등급 |
|---|---|---|---|
| `JOURNAL_ANALYST` | SD `analysis_journal_helper.py` | 분석 일지 작성자 기본값(사용자 이름을 코드에서 뺐다) | 설정 |
| `XSOURCE_SAMPLE_ACCOUNTS`, `XSOURCE_DIGEST_ACCOUNTS` | ET `board/ingest/xsource.py` | X 실측 도구가 볼 계정 목록(쉼표 구분, 계정 핸들을 코드에서 뺐다) | 설정(개인 데이터) |
| `DASHBOARD_URL` | SD `scripts/verify_deploy.py` | 배포 확인 대상 주소(Render 호스트명을 코드에서 뺐다 — 위 표의 옛 이름과 같다) | 운영 |
| `KBJ_REQUIRE_DOCKER` | `scripts/test_legacy.sh`(CI `gexlab-integration` 잡) | `1` 이면 Docker 를 못 써서 건너뛴 통합 시험을 실패로 본다 | 설정 |

## 3. 키가 샜을 때

1. 그 키를 발급처에서 바로 폐기·재발급한다(KIS 는 앱키 재발급, 텔레그램은 BotFather `/revoke`).
2. 커밋에 들어갔다면 이력을 고치기 전에 **먼저 재발급**한다 — 공개 레포는 이미 복제됐다고 본다.
3. `scripts/check_public_safety.py` 가 잡지 못한 형태였다면 `kbj/core/masking.py` 의 `SECRET_SHAPES` 에 형태를 더한다(검사와 마스킹이 같은 목록을 쓴다).

## 4. KIS 앱키 주입 범위 (ADR 0004 — 2026-10-07 메인 결정 D1·D2)

KIS REST 는 **모든 요청 헤더에** `appkey`·`appsecret` 을 요구한다(GX `data/kis/rest.py:_send`, ET `board/ingest/kis.py:_headers`, SD `kis_api.py:_headers`). 그래서 P1 이 적었던 "앱키는 services.auth 만 읽는다"는 그대로 지킬 수 없다. 결정(안 A):

| 무엇 | 어디에 |
|---|---|
| `KBJ_KIS_APP_KEY`·`KBJ_KIS_APP_SECRET`·`KBJ_KIS_ENV` 주입 | KIS REST 를 부르는 프로세스: `auth`, scheduler 실행기·collectors, legacy 브리지를 쓰는 legacy 실행, P7 까지 legacy GX poller·ws-gateway. **그 밖 서비스(notifier·api·migrate 등)에는 넣지 않는다**(compose — 묶음 I) |
| 접근토큰·웹소켓 접속키 **발급** | `services.auth` 한 곳. 나머지는 Redis `kis:token`·`kis:ws_key` 를 읽기만 한다 |
| 발급 차단 세 겹 | ① 발급 클래스는 `kbj/services/auth/issuer.py` 에만, import-linter 계약으로 auth 밖 import 금지 ② `oauth2/tokenP`·`oauth2/Approval` 문자열 검사(`scripts/check_canonical.py`) ③ 런타임 가드 — `KBJ_SERVICE` 가 `auth` 가 아니면 발급자 생성 거부 |
| 웹소켓 연결 코드 | P7 까지 legacy GX(`check_canonical` 기준선 그룹 `kis_ws`). 접속키 발급은 지금부터 auth 만 |

서비스별 주입(`docker-compose.yml` profile `app` — `.env` 를 통째로 넣지 않는다, `tests/test_store_layout.py` 가 고정):

| 서비스 | 받는 비밀 |
|---|---|
| `migrate` | `KBJ_DATABASE_URL` |
| `auth` | `KBJ_REDIS_URL`, `KBJ_KIS_APP_KEY`·`KBJ_KIS_APP_SECRET`·`KBJ_KIS_ENV` (P2 에 KIS 를 부르는 서비스는 auth 하나 — KIS 수집 작업이 켜지는 P3 에 scheduler 에도 더한다) |
| `scheduler` | `KBJ_REDIS_URL`, `KBJ_DATABASE_URL`, `KBJ_DART_API_KEY`(`filings.corp_code`) |
| `notifier` | `KBJ_REDIS_URL`, `KBJ_DATABASE_URL`, `KBJ_TELEGRAM_BOT_TOKEN`·`KBJ_TELEGRAM_CHAT_ID`·`KBJ_TELEGRAM_INBOX_CHAT_IDS`·`KBJ_TELEGRAM_WEBHOOK_SECRET`, `KBJ_PUBLIC_BASE_URL` |

접속 문자열(`KBJ_DATABASE_URL`·`KBJ_REDIS_URL`)은 compose 가 `KBJ_POSTGRES_PASSWORD`·`KBJ_REDIS_PASSWORD` 로 만든다(기본값 없음 — 없으면 compose 가 멈춘다).

토큰 값 자체는 어느 환경변수에도 두지 않는다(Redis 에만 — `KIS_TOKEN_CACHE_PATH` 삭제).

## 5. P3 에 더한 이름 (docs/p3_design.md §5.4·§7, 묶음 M)

- 새 비밀 이름 5개: `KBJ_WEB_USER`·`KBJ_WEB_PASSWORD_HASH`(로그인 웹 — 사용자 1명), `KBJ_PUBLIC_EXPORT_DATABASE_URL`(공개 내보내기 — pub_* 만 읽는 역할), `KBJ_PUBLIC_DEPLOY_KEY_PATH`·`KBJ_GITHUB_DISPATCH_TOKEN`(public-data 푸시·Pages 디스패치). 설정 4개: `KBJ_WEB_SESSION_TTL_H`·`KBJ_WEB_COOKIE_SECURE`·`KBJ_WEB_DIST_DIR`·`KBJ_PUBLIC_PUSH_ENABLED`.
- **기본 사용자·비밀번호는 없다**(공개 레포). 레포·시험 어디에도 실제 해시를 두지 않는다 — 시험용 해시는 시험 픽스처가 낮은 비용으로 그때 만든다.
- **켜지 않는 것(사용자 승인 사항)**: GitHub Pages 활성(소스 = Actions), 배포 키 발급·등록, 디스패치 토큰 발급, VM TLS(역방향 프록시). P3 는 이름만 문서화하고 `pages.yml` 은 `workflow_dispatch` 만 둔다. `KBJ_PUBLIC_PUSH_ENABLED` 기본 false.
- 주입 범위(compose 반영 완료 — 웨이브 3 묶음 S, §5.1): `api` 서비스는 `KBJ_DATABASE_URL`·`KBJ_REDIS_URL`·`KBJ_WEB_USER`·`KBJ_WEB_PASSWORD_HASH`·`KBJ_TELEGRAM_WEBHOOK_SECRET`·`KBJ_PUBLIC_BASE_URL` 만(KIS·KRX 키 없음 — 계약 ⑩). `public.export` 작업은 `KBJ_PUBLIC_EXPORT_DATABASE_URL`(+ 승인 뒤 배포 키 경로·디스패치 토큰). KIS 수집 작업이 켜지는 P3 에 scheduler 에도 `KBJ_KIS_APP_KEY`·`KBJ_KIS_APP_SECRET`·`KBJ_KIS_ENV`·`KBJ_KRX_API_KEY`(ADR 0004 "P3 에 scheduler 에도").
- compose `api` 는 **호스트 네트워크**(`network_mode: host`, `127.0.0.1:8000` — TLS 역방향 프록시가 실제 접속 IP 를 넘겨 로그인 잠금이 IP 별로 걸리게)라서 DB·Redis 주소가 서비스 이름이 아니라 `127.0.0.1`(compose 의 `*_host` 접속 문자열)이다.
- `KBJ_WEB_PASSWORD_HASH` 값에는 `$` 가 들어 있다 — `.env` 에 **작은따옴표**로 넣는다(`KBJ_WEB_PASSWORD_HASH='scrypt$…'`). 따옴표가 없으면 compose 의 변수 치환이 `$n`·`$r` 등을 지워 값이 깨지고 로그인이 503 이 된다.

