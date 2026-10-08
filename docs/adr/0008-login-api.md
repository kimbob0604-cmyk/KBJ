# ADR 0008 — 로그인 API·단일 사용자 로그인·웹훅 서버·API 는 외부 호출 없음 (2026-10-07)

상태: **확정**(2026-10-07 — 초안 P3 묶음 A, 번호·상태는 웨이브 3 묶음 S 가 확정 — 아래 'S 확정 메모', `docs/p3_design.md` §9.3).
근거: `docs/p3_design.md` D-P3-3·D-P3-4, §5(API)·§8.4(시험), `docs/DATA_TIERS.md` §3, P2 결정 D4(FastAPI 는 P3).
구현: `kbj/services/api/`(`app.py`·`auth.py`·`deps.py`·`cache.py`·`models/*`·`readers/*`·`routes/*`·`__main__.py`),
스키마 `web/src/api/openapi.json`, 합성 응답 픽스처 `web/test/fixtures/api/*.json`, 시험 `tests/unit/api/`.

## 1. 맥락

- 로그인 화면(페이지 1·2·4·10)은 로그인 등급 데이터(KIS·KRX·운용사 — `prv_*`)를 읽는다. 공개 사이트에는 로그인
  API 주소조차 넣지 않는다(DATA_TIERS §3) — 로그인은 VM 의 서버 하나가 맡는다.
- 사용자는 1명이다. 공개 레포라 기본 사용자·비밀번호를 둘 수 없다.
- 화면 요청이 KIS 호출 한도(초당 4건)를 쓰면 수집이 밀린다.
- 텔레그램 웹훅(P2 `WebhookHandler`)을 받을 HTTP 서버가 P2 에는 없었다.

## 2. 결정

1. **한 프로세스**: FastAPI + uvicorn(compose 서비스 `api`, 127.0.0.1:8000) 이 `/api/*`·`POST /telegram/webhook`·
   로그인 SPA 정적 파일(`web/dist-login`)을 **같은 출처**로 낸다(`/api`·`/telegram` 이 정적 파일보다 먼저). TLS 는 VM
   역방향 프록시(레포 밖 — [확인 필요]).
2. **외부 호출 없음**: API 는 DB(저장소 `kbj.store.repos`)·Redis 만 읽고 순수 엔진(`kbj.engines.*`)으로 계산한다.
   `kbj.data.private`·`kbj.services.collectors` 를 import 하지 않는다(import-linter 계약 ⑩ — 묶음 S 가 웨이브 3 에
   넣는다). 시험 `test_no_external.py` 가 httpx 전송·소켓 연결을 막은 채 모든 라우트를 부른다.
3. **응답 봉투**: 모든 데이터 응답은 `Envelope[T]`(`source`·시간대 있는 `as_of`·`quality`·`notes`·`generated_at`·
   `data`). 금액은 원 단위 정수(보드 산출 JSON 만 ET 그대로 억원 — notes 에 적는다). 데이터가 아직 없으면 404
   `{code: "no_data", message: "아직 없음 — <작업> <예정 시각>"}`. 검산 불가는 `None` + 사유(R2).
4. **로그인 1명**: `KBJ_WEB_USER` + `KBJ_WEB_PASSWORD_HASH`(`hashlib.scrypt`, `scrypt$n=32768$r=8$p=1$<salt>$<dk>` —
   새 의존성 없음, `python -m kbj.services.api hash-password` 가 stdout 에만 낸다). **기본값 없음** — 둘 중 하나라도
   없거나 해시 형식이 틀리면 로그인 503 `login_not_configured`(형식 검사는 설정 모델이 아니라 api 가 값 없이 한다 —
   pydantic 검증 오류 문구가 입력값을 싣기 때문, 묶음 M 요청).
5. **세션·CSRF**: Redis 서버 세션(`web:session:<sha256(sid) 32자>`, 12시간 고정 만료, 로그인마다 새 sid), 쿠키
   `__Host-kbj_session`(HttpOnly·Secure·SameSite=Strict·Path=/). 상태를 바꾸는 요청은 `X-KBJ-CSRF`(세션 결합 토큰,
   상수 시간 비교) **그리고** `Origin == KBJ_PUBLIC_BASE_URL`. 로그인 POST 는 Origin + `Content-Type: application/json`.
   개발(`KBJ_WEB_COOKIE_SECURE=false`)은 루프백에서만, 쿠키 이름 `kbj_session`.
6. **무차별 대입**: IP(해시 16자)별 15분 창 5회 → 15분 잠금, 전체 1시간 20회 → 30분 전체 잠금. 실패·잠금은 같은 401
   문구(잠금이면 `Retry-After`). 로그에는 이름·IP·쿠키·비밀번호·해시를 남기지 않는다(IP 해시 앞 8자·실패 수만).
   422 응답은 위치·종류만 싣는다(입력값 없음).
7. **보안 헤더**: CSP `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self';
   worker-src 'self'; manifest-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'`,
   `nosniff`·`no-referrer`·`Permissions-Policy: ()`. 데이터 응답 `Cache-Control: private, no-cache` + ETag(304),
   로그인·상태·오류 `no-store`. 운영 앱은 `/openapi.json`·`/docs` 를 내지 않는다(스키마는 CLI 로 레포에).
8. **캐시**: 키 = (경로, 정렬된 쿼리, 데이터 버전). 데이터 버전 = 영역 작업들의 마지막 완료 시각
   (`ops.data_claim.done_at`·`ops.job_run.finished_at`, Redis `api:data_version:<영역>` 15초). 응답 본문과 원장은
   **프로세스 메모리**에 둔다(로그인 데이터를 Redis 에 복사하지 않는다). 원장(60영업일 창)은 데이터 버전마다 한 번.
9. **웹훅**: `POST /telegram/webhook` 은 세션·CSRF·Origin 면제, 본문 1 MiB 상한(넘으면 413), 처리는 P2
   `WebhookHandler.handle` 그대로(비밀 헤더 상수 시간 비교). 비밀이 설정되지 않았으면 경로가 처리기를 부르지 않고
   늘 403(§5.5 — P2 처리기 자체는 이때 503 이지만 HTTP 경로는 설계대로 403).
10. **스키마**: `python -m kbj.services.api openapi` 가 `web/src/api/openapi.json` 을 쓰고 `--check` 가 신선도를
    본다(W2 의 `npm run gen:types` 입력).

## 3. 결과

- 화면 요청은 KIS 한도를 쓰지 않는다. 로그인 등급 데이터는 공개 번들·공개 브랜치로 새지 않는다(번들 검사·계약 ⑧).
- 비밀번호 해시가 없는 배포는 로그인이 닫힌 채로 뜬다(503) — 실수로 열린 기본 계정이 생기지 않는다.
- 남은 일·[확인 필요]: TLS 프록시 설정(VM — 사용자), 역방향 프록시 뒤 IP(현재 `request.client` — 프록시가 같은 IP 로
  보이면 IP 별 잠금이 전체 잠금처럼 동작한다; `uvicorn --proxy-headers`·`forwarded_allow_ips=127.0.0.1` 로 받는다),
  스크리너 미적중 응답 시간(2,700종목 × 25영업일 합성에서 약 13초 — 메모리 저장소 읽기·원장 고르기. 목표 3초는
  묶음 M·E2 의 최적화 뒤 다시 잰다), `/신고가`·`/수급` 텔레그램 명령(readers 재사용 — P4 로).

## S 확정 메모 (웨이브 3, 2026-10-07)

- 번호 0008·상태 **확정**. 계약 ⑩(`kbj.services.api` → `kbj.data.private`·`kbj.services.collectors` 금지, 간접 포함)을
  `pyproject.toml` 에 넣었고 `tests/test_import_contracts.py` 가 위반 2건(어댑터·수집기)을 심어 잡는 것을 본다.
- compose 서비스 `api`(Dockerfile 1단계 node 가 `npm run build:login` → `/app/web/dist-login`, `KBJ_WEB_DIST_DIR`).
  **호스트 네트워크** + `KBJ_API_HOST=127.0.0.1`·포트 8000 — VM TLS 프록시가 127.0.0.1 에서 들어오므로
  `forwarded_allow_ips=127.0.0.1` 이 맞고 로그인 잠금이 실제 클라이언트 IP 로 돈다(브리지 네트워크면 게이트웨이 IP 하나로
  보여 전체 잠금이 된다 — 위 §3 의 [확인 필요]를 이것으로 닫는다). 그래서 api 의 DB·Redis 주소는 127.0.0.1 포트다.
  주입은 `docs/secrets.md` §5 표 그대로(KIS·KRX·텔레그램 봇 토큰 없음 — `tests/test_compose_p3.py`).
- 로컬 Docker 로 실제 기동을 확인했다: migrate 0001~0009 적용 → `GET /api/health` 200, `/` 로그인 SPA 200(text/html),
  세션 없는 `/api/market/summary` 401, 로그인 200 + `__Host-kbj_session`(HttpOnly·Secure·SameSite=Strict), 틀린 비밀번호 401,
  `X-Forwarded-For` 가 IP 해시에 반영됨.
- 운영 메모: `KBJ_WEB_PASSWORD_HASH` 값에는 `$` 가 있어 `.env` 에 **작은따옴표로** 넣는다(compose 변수 치환 — docker-compose.yml
  머리말). TLS 프록시·도메인은 [사용자 승인 필요] 그대로.
