# ADR 0004 — KIS 앱키 주입 범위와 발급 차단 세 겹 (2026-10-07)

상태: 확정(P2 — 메인 결정 D1·D2·D3, `docs/p2_design.md` §3.4~§3.6·D-P2-2·D-P2-4, 위험 R1·R2·R3·R17).
구현: `kbj/config/settings.py`(`service`)·`docs/secrets.md` §4·`.env.example`(S0) · `kbj/services/auth/issuer.py`·
`kbj/services/auth/service.py`·`kbj/data/private/kis/{credentials,token,rest}.py`(묶음 A) · import-linter
계약 ④·`scripts/check_canonical.py`·`docker-compose.yml` 앱 서비스·하루 시뮬레이션(묶음 I).

> 이 파일은 S0 가 먼저 쓴 `0004-kis-issuance-boundary.md` 의 결정(1~3장)을 그대로 옮기고 묶음 A 의
> 구현(4장)을 더한 것이다. ADR 0004 는 이 파일 하나다.

## 1. 맥락

- ADR 0001 U1: KIS 앱키를 세 프로젝트가 같이 쓴다 → 접근토큰 발급은 auth 서비스 한 곳(KIS 발급 1분 1회 제한 —
  다른 곳이 발급하면 서로의 토큰을 무효화한다).
- P1 의 `docs/secrets.md` 와 `docs/conflict_map.md` §1.1 이행 1번은 "앱키는 **auth 컨테이너에만** 주입"이라고 적었다.
- 그런데 KIS REST 는 **모든 요청 헤더에** `appkey`·`appsecret` 을 요구한다(GX `data/kis/rest.py:_send`,
  ET `board/ingest/kis.py:_headers`:146, SD `kis_api.py:_headers`:82). 앱키를 auth 에만 두면 다른 프로세스는
  KIS 를 부를 수 없다. GX 도 compose 의 모든 앱 서비스에 `.env` 를 통째로 넣는다(GX `docker-compose.yml:24`).
- 설계서 §3.6 은 세 안을 냈다: A(앱키는 REST 호출 프로세스에도, 발급은 코드 구조로 막는다), B(auth 가 KIS REST
  프록시까지 — 지연·장애 단일점), C(앱키 둘 — U1 과 어긋난다).

## 2. 결정

### 2.1 안 A — 앱키는 KIS REST 를 부르는 프로세스에도 준다 (D1)

| 무엇 | 어디에 |
|---|---|
| `KBJ_KIS_APP_KEY`·`KBJ_KIS_APP_SECRET`·`KBJ_KIS_ENV` | `auth`, scheduler 실행기·collectors(KIS 수집 작업), legacy 브리지(`kbj/data/legacy_bridge.py`)를 쓰는 legacy 실행, P7 까지 legacy GX poller·ws-gateway |
| 넣지 않는 곳 | notifier·api·migrate 등 KIS 를 부르지 않는 서비스(compose 에서 서비스별로 고른다 — 묶음 I) |
| 토큰 | 환경변수·파일에 두지 않는다. Redis `kis:token`·`kis:ws_key` 만(auth 가 쓰고 나머지는 `reader` 로 읽기만) |

### 2.2 발급은 auth 에만 — 세 겹

1. **위치 + import 계약**: 접근토큰·접속키 발급 클래스(`KisTokenIssuer`·`KisApprovalKeyIssuer`)는
   `kbj/services/auth/issuer.py` 에만 둔다. import-linter 계약 ④ "KIS 발급(kbj.services.auth)은 다른 모듈이
   import 하지 않는다"가 kbj 안의 다른 패키지에서 auth 를 import 하면 실패시킨다(설계 §9.6). legacy 는
   import-linter 루트가 아니므로 `check_canonical` 의 AST 규칙이 같은 것을 본다(§9.5).
2. **문자열 검사**: KIS 발급 경로 문자열(`oauth2/tokenP`·`oauth2/Approval`)은 `kbj/services/auth/**` 밖에서
   `scripts/check_canonical.py` 그룹 `kis_oauth` 가 실패시킨다(legacy 목표 0, 기준선 없음).
3. **런타임 가드**: `Settings.service`(`KBJ_SERVICE`)가 `"auth"` 가 아니면 발급 클래스의 생성자가
   `RuntimeError` 로 거부한다. 시험·스크립트처럼 `KBJ_SERVICE` 가 없는 프로세스도 발급할 수 없다(발급 시험은
   `service="auth"` 설정을 명시적으로 만든다).

### 2.3 웹소켓 (D2)

- KIS 웹소켓 **연결** 코드는 P7 까지 legacy GX(`legacy/gexlab/services/ws_gateway`)에 둔다. 웹소켓 호스트는
  `check_canonical` 의 **기준선 그룹** `kis_ws` 로 관리한다(줄어들기만 — P7 에 0).
- 웹소켓 **접속키**(approval key) **발급**은 지금부터 auth 만 한다. legacy GX ws-gateway 는 이미 auth 의 Redis
  `kis:ws_key` 를 읽기만 한다(GX `services/ws_gateway/client.py:250`).

### 2.4 하루 시뮬레이션의 '하루' (D3)

- 창은 **24시간**(2026-10-06 05:00 ~ 10-07 05:00 KST). 설계 D-P2-3 의 23시간 창 시험은 따로 만들지 않는다.
- 단언: 모든 발급은 auth 에서만, auth 밖 소비자의 발급 0, **어떤 23시간 구간에도 접근토큰 발급 ≤ 1**
  (창 안 발급은 05:00 첫 발급과 다음 날 04:00 — 만료 60분 전 — 갱신 2회, 간격 23시간).

## 3. 결과

- 비밀 노출 면적이 auth 하나에서 "KIS 를 부르는 프로세스"로 넓어진다. 대신 그 프로세스들은 발급 경로가 코드에
  없다 — 앱키가 있어도 토큰을 새로 만들 수 없다.
- PLAN §8 P2 완료 기준("legacy 가 KIS 를 직접 부르면 검사가 실패")과 하루 운영 시뮬레이션의 "모든 발급은
  auth 에서만, auth 밖 발급 0"은 위 세 겹과 가짜 KIS 서버의 발급 요청 수(`token_posts`·`approval_posts`)로
  증명한다(묶음 I).
- 레포 밖 발급자(K10 `kospi-dislocation`)는 이 ADR 이 막지 못한다 — 전환일 사용자 작업(ADR 0001 Q13), auth 의
  `token_throttled`·`token_rejected` health 로 감지한다.
- 다시 볼 때: P7 이후 auth 가 KIS REST 프록시를 맡는 안(B)을 재검토할 수 있다(지연·장애 단일점 비용과 비교).

## 4. 구현 (묶음 A — 2026-10-07)

### 4.1 발급 쪽 — `kbj/services/auth/`

| 무엇 | 내용 |
|---|---|
| `issuer.py` | 레포에서 KIS 발급 요청을 보내는 **유일한 파일**. `KisTokenIssuer`(토큰 경로)·`KisApprovalKeyIssuer`(접속키 경로, 수명 12시간 가정 — 미실측), `TOKEN_PATH`·`APPROVAL_PATH`·`ISSUE_THROTTLED_CODE`. 오류 문구는 앱키·시크릿을 가린 뒤 200자로 자르고, 토큰·접속키는 오류에 싣지 않는다 |
| 런타임 가드 | `require_issuer_process(settings=None)` — 두 발급자 생성자가 맨 먼저 부른다. 설정을 받지 않으면 이 프로세스의 `Settings()`(환경변수·`.env`)를 읽는다. `service == "auth"` 와 정확히 같을 때만 통과(대소문자·접두사 다른 값은 거부). `build_auth_service`·`serve` 는 받은 설정을 그대로 넘긴다 |
| `service.py` | GX `AuthService` 승격 — 30초 step, 만료 60분 전 갱신, 61초 발급 간격(Redis Lua 공유 + 인스턴스 자체 간격), 발급 요청도 앱키 리미터 **P0** 허가(5초), Redis 를 못 쓰면 발급하지 않음, 만료 임박 critical. `serve` 의 리미터는 `RedisRateLimiter.scoped(r, "kis", 앱키)`(= `rl:kis:<해시>`, `config/limits.yaml` `kis`) |
| 진입점 | `python -m kbj.services.auth` — `KBJ_SERVICE=auth`·`KBJ_REDIS_URL`·자격이 없으면 종료 코드 2. 하트비트 `health:heartbeat:auth`(180초), health 는 JSON 로그(거래일·세션 태그 — `kbj.core.calendar`). `ops.health_events` 적재는 저장소(묶음 G)가 생기면 `ServiceHealthSink` 에 끼운다 |

### 4.2 읽는 쪽 — `kbj/data/private/kis/`

| 무엇 | 내용 |
|---|---|
| `credentials.py` | `KisCredentials(app_key, app_secret, env)` — 주소는 비공개 상수(`base_url(env)` 로만), `owner` 는 GX 와 같은 식(sha256(주소 + "\n" + 앱키) 앞 16자) — 전환 기간 legacy GX 가 KBJ auth 의 토큰을 그대로 읽는다 |
| `token.py` | `RedisTokenCache`(GX 키 그대로)·`CachedTokenProvider`·**`reader(redis, creds)`**(발급자 없음)·`access_token()`·`ws_approval_key()`. GX 의 파일 캐시·폴백·기본 발급 제공자는 옮기지 않았다(토큰은 Redis 에만). Redis 를 못 읽으면 `TokenUnavailable`(메모리의 살아 있는 토큰은 만료 전까지 쓴다) |
| `rest.py` | `KisRestClient(creds, token_provider, rate_limiter)` — provider 필수(없으면 `TypeError`, K7). `for_service(settings, redis)` = 앱키 리미터 + `reader`. 발급 요청 코드 없음 |

### 4.3 거절 신고 가드 (설계 §3.4 [제안] → 구현, R3)

GX 의 `invalidate()` 는 `kis:token` 을 지웠다 — 고장 난 호출자 하나가 auth 재발급을 하루 1,400번까지 일으킬 수 있었다.

1. 읽는 쪽(`reader`)의 `invalidate()` 는 키를 지우지 않고 `kis:token:rejected`(hash — `token_sha16`·`at`·`by`,
   1시간)에 **신고만** 한다. 같은 토큰의 신고가 이미 있으면 덮어쓰지 않는다. 그 토큰은 이 프로세스에서 다시 쓰지
   않는다(auth 가 새 값을 넣을 때까지 `TokenUnavailable`). REST 클라이언트는 거절 응답 뒤 한 번만 다시 시도하고,
   새 토큰이 없으면 발급하지 않고 실패한다.
2. auth 는 캐시의 값에 걸린 신고(`pending_rejection`)를 본다. 신고는 그 값이 캐시에 있는 동안 걸려 있고, 다시
   발급해 값이 바뀌면 지난 신고가 된다 — 그래서 **같은 값은 한 번만** 다시 발급한다(재기동한 auth 도 걸린 신고를
   이어서 처리한다). 발급 간격(61초)·리미터 규칙은 그대로다.
3. 발급 **10분 안**에 온 신고는 다시 발급하지 않고 critical `token_rejected_after_issue` 로 **닫는다**
   (`close_rejection` — 처음 닫은 인스턴스만 알린다). 닫힌 신고는 같은 값의 새 신고로도 다시 열리지 않는다.
   앱키·환경(real/vts)·레포 밖 발급자를 사람이 확인한다. 그 밖은 warning `token_rejected` 뒤 다시 발급한다.
4. 결과: 늘 거절만 신고하는 호출자가 있어도 하루 발급은 '처음 1 + 재발급 1' 에서 멈춘다
   (`tests/unit/auth/test_rejected_storm.py`). 신고를 10분보다 드물게 내는 호출자는 값마다 한 번씩 재발급을
   일으킬 수 있다(최대 10분에 1회) — 이것이 남은 상한이다.
5. **지난 토큰의 늦은 신고는 쓰지 않는다**(최종 점검 2026-10-07 — 하루 시뮬레이션이 찾은 결함): 캐시의 값이 이미
   다른 토큰이면 `report_rejected` 는 False 로 끝난다. 전에는 '다른 토큰 것이면 바꾼다'(Lua)라서, 지난 값을 메모리에
   쥔 프로세스(legacy 브리지 등)의 신고가 현재 값의 신고를 덮어써 auth 가 현재 값의 거절을 몰랐다(재발급·알림 둘 다
   없음). 지금은 `kis:token`·신고 키를 WATCH 하는 트랜잭션으로 확인·쓰기를 한 번에 한다
   (`tests/unit/kis/test_invalidate_guard.py::test_a_late_report_for_a_replaced_token_does_not_mask_the_current_report`,
   `tests/sim/test_token_rejected.py::test_stale_token_report_does_not_mask_current_rejection`).

### 4.4 증명(시험)

| 시험 | 무엇을 |
|---|---|
| `tests/unit/auth/test_auth_guard.py` | 가드(`KBJ_SERVICE` 없음·다른 서비스·대문자 → `RuntimeError`, auth 만 통과), kbj 안 다른 패키지가 `kbj.services.auth` 를 import 하지 않음(AST — 계약 ④ 전에도), 발급 클래스·발급 경로 문자열이 `issuer.py` 에만 |
| `tests/unit/kis/test_reader_never_issues.py` | 빈 Redis·만료 직전·Redis 장애에서 읽는 쪽 HTTP 0회, `for_service` 가 토큰이 없으면 GET 도 안 보냄, `kbj/**` 에 auth 밖 발급 경로 문자열 0 |
| `tests/unit/kis/test_invalidate_guard.py`·`tests/unit/auth/test_rejected_storm.py` | 4.3 |
| `tests/unit/auth/test_owner_compat.py` | owner 가 GX 값과 같고, KBJ auth 가 넣은 값을 **GX 코드 그대로**(하위 프로세스) 읽는다 |
| `tests/unit/auth/test_day_window.py` | D3 의 24시간 창: 접근토큰 05:00·04:00(간격 23시간), 접속키 05:00·16:00·03:00, 그 밖의 발급 요청 0 |
| GX 승격 시험 | `test_token_cache`(13)·`test_issuer`(7)·`test_auth_service`(52)·`test_kis_rest`(11)·`test_kis_master`(17)·`test_master_download`(3) |

### 4.5 남은 일

- 묶음 I: import-linter 계약 ④(auth)·⑥(어댑터는 서비스를 모른다)를 pyproject 에 넣는다 — 지금 코드로 미리 돌려
  둘 다 KEPT 확인. `check_canonical` 그룹 `kis_oauth`·`kis_rest`·`kis_master`·`kis_ws`, compose 서비스별
  `KBJ_SERVICE`·앱키 주입(2.1 표), 하루 시뮬레이션(2.4).
- 묶음 H: legacy GX `data/kis/{auth_client,rest,master}.py`·`services/auth/*` 를 kbj 를 다시 내보내는 얇은 모듈로
  (발급 코드 없음 — legacy 쪽에서 KIS 발급 경로 문자열 0).
- 실측: 접속키 수명·접근토큰과 접속키가 1분 1회 제한을 함께 쓰는지(R4), 거절 코드 `EGW00121`·`EGW00123`(미실측).
