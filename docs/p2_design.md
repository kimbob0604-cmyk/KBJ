# KBJ P2 설계서 — 공용 기반 일원화

- 작성: 2026-10-06. 상태: **구현 완료(P2, 2026-10-07 — 로컬 통과, CI 확인 대기)**. 대상 단계: `docs/PLAN.md` §8 P2(결과 수치는 그 절).
- **메인 결정(2026-10-07)으로 닫은 것**: D1 앱키는 KIS REST 를 부르는 프로세스에도, 발급은 세 겹으로 auth 에만(§3.6·R1 — ADR 0004) · D2 KIS 웹소켓 연결은 P7 까지 legacy GX 기준선 그룹, 접속키 발급은 auth 만(D-P2-4·R2) · D3 하루 시뮬레이션은 **24시간 창**, 단언은 "어떤 23시간 구간에도 접근토큰 발급 ≤ 1"(D-P2-3·R17·§10.3) · D4 FastAPI·uvicorn 은 P3, P2 웹훅은 순수 처리 함수(R8) · D5 KRX 일 상한 8,000 [확인 필요] + 실측 체크리스트(R9) · D6 네이버 어댑터 없음(D-P2-1) · D7 거래대금·투자자별 순매수·ETF 수급 논리 데이터셋 등록(`docs/metrics.md`) · 지연 개장 `late_open`(R24 — §7.1). 아래 본문에서 각 자리에 **[결정 Dn]** 으로 표시했다. 구현이 설계와 다르게 정한 것은 ADR 0004~0007 과 각 절의 '구현 메모'에 있다.
- 기준 문서: 정본 지정 `docs/conflict_map.md`(이 설계의 기준), 등급 `docs/DATA_TIERS.md`, 결정 `docs/adr/0001~0003`, 일정·발송·DB·환경변수 표 `docs/inventory.md` (c)(d)(e)(f), 공공데이터 명세 `docs/probe_results.md`, 비밀 이름표 `docs/secrets.md`, 뼈대(`kbj/config/settings.py`, `kbj/core/quality.py`·`masking.py`, `kbj/store/migrations/0001_schemas.sql`, `pyproject.toml` 의 `[tool.importlinter]`).
- 원본 줄 번호: 읽기 전용 스냅샷 `/home/user/p0src` 기준 — SD=stock-dashboard `f46178c`, ET=etf-traker `0014f57`, GX=gexlab `43a9ed1`. 이 설계를 쓰는 동안 `legacy/` 이식(P1)이 진행 중이었다(그때 `legacy/` 에는 `gexlab` 만 있었다). legacy 경로는 이식 후 경로(`legacy/stock_dashboard/…`, `legacy/etf_traker/board/…`, `legacy/gexlab/…`)로 적었다. P1 은 경로·import 만 고치므로 줄 번호는 같거나 몇 줄 밀린다 **[추정]** — H 묶음이 시작할 때 다시 찾는다.
- 표기: **[추정]** = 코드로 끝까지 확인하지 못함, **[확인 필요]** = 실측·결정 전의 기본값, **[제안]** = 이 설계의 권고, ⚠ = 충돌·위험.
- 키·토큰·계좌 값은 이 문서 어디에도 없다. `.env`·토큰 캐시 파일은 열지 않았다.
- 사용자 결정 반영(ADR 0001): **U1** 앱키·봇 공유 → 발급은 auth 한 곳(1분 1회), 앱키당 리미터 하나(초당 4건), 발송은 notifier 한 곳, 수신은 웹훅 하나(getUpdates 폐지). **U2** 아침 브리핑 1회·마감 요약 1회. **U3** 로그인 등급 실데이터는 합성으로. **U4** 네이버는 로그인 전용 기능으로, 데이터는 KRX·KIS 로 바꾸고 **네이버 어댑터는 만들지 않는다**.

---

## 0. 요약

### 0.1 P2 에서 만드는 것 / 만들지 않는 것

| 만든다 (P2) | 만들지 않는다 (경계) |
|---|---|
| `kbj/services/auth` — KIS 접근토큰·웹소켓 접속키의 유일한 발급자(GX 승격) | 실제 legacy 작업을 KBJ 스케줄러에서 **실행**하는 것 → P3~P5(영역별) |
| 앱키당 Redis 레이트리미터 + 출처별 리미터·일 예산(GX `ratelimit` 일반화) | 브리핑·마감 요약 **본문** 만들기 → 마감 P3, 아침 P5 |
| 어댑터: KIS REST(+마스터 내려받기), KRX, DART, ECOS, KOSIS, 공공데이터포털(관세청·금투협 종합통계·금융위 시세) | 네이버 어댑터(U4 — PLAN §8 P2 목록에서 뺀다), FRED·Yahoo·RSS·ETF 운용사(P5) |
| `kbj/core/calendar`(GX `core.calendar` 승격 + XNYS·주식 정규장) | GEX 엔진·poller·ws-gateway·recorder 승격(P7 — 그때까지 legacy GX) |
| `kbj/services/notifier` — 발송(outbox)·웹훅 수신·명령 분배·인박스·발송 기록 | 수집 결과를 쌓는 collectors 대부분(P3~P6) — P2 는 `filings.corp_code`·`ops.watchdog` 두 개만 |
| `kbj/services/scheduler` — 세션 상태 머신 + **작업 등록부** `config/jobs.yaml` + 실행기 + 데이터 키 선점(claim) | FastAPI 로그인 API·웹훅 HTTP 서버(P3) — **[결정 D4]** P2 웹훅은 순수 처리 함수만(요청 dict → 응답), `fastapi`·`uvicorn` 의존성 없음 |
| DB: 0002~0006 마이그레이션(P2 표), 적용기(GX `db/migrate.py` 승격), SQLite·JSON → Postgres 이관 스크립트(멱등·검증) | P3 이후 표(보드·공시·재무·매크로·무역·ETF·알림 규칙·저널) — 목록과 매핑만 정해 둔다 |
| legacy 재배선: KIS·KRX·DART 직접 호출 0, 텔레그램 발송 7벌 → shim 하나, legacy 휴장 판정 → 캘린더 | legacy 텔레그램·ECOS·data.go.kr·네이버 호출의 **완전 제거**(기준선 파일로 줄여 간다) |
| `scripts/check_canonical.py` + import-linter 계약 추가 + 하루 운영 시뮬레이션 `tests/sim` | 운영 VM 전환(옛 발급자 정지·setWebhook 이전) — 사용자 작업(§3.8 K10, §5.11) |

### 0.2 완료 기준과 증명

| PLAN §8 P2 완료 기준 | 증명 방법 | 담당 묶음 |
|---|---|---|
| legacy 가 **KIS** 를 직접 부르면 검사가 실패한다 | `scripts/check_canonical.py` 그룹 `kis_oauth`·`kis_rest`·`kis_master` 가 legacy 에서 0건(기준선 없음, 한 건이라도 생기면 종료 코드 1). import-linter 계약 ④(발급 모듈은 auth 밖 import 금지) | H, I |
| legacy 가 **KRX**·**DART** 를 직접 부르면 실패한다 | 같은 검사 그룹 `krx_api`·`dart` 0건 | H, I |
| (+텔레그램·ECOS·data.go.kr) | 그룹 `telegram`·`ecos`·`datago`·`kosis`·`naver`·`krx_scrape`·`kis_ws` 는 **줄어들기만 하는 기준선**(`scripts/canonical_baseline.txt`) — 새 위반·건수 증가는 실패 | I |
| 하루 운영 시뮬레이션(가짜 시계)에서 **토큰 발급 1회** | **[결정 D3]** `tests/sim/test_one_day.py`(창 2026-10-06 05:00 ~ 10-07 05:00 KST, 24시간 — §10.3): 모든 발급은 auth 에서만, auth 밖 소비자의 발급 0, **어떤 23시간 구간에도 접근토큰 발급 ≤ 1**(창 안 발급은 05:00 첫 발급과 다음 날 04:00 만료 60분 전 갱신 2회 — 간격 23시간) | I |
| **중복 수집 0건** | 같은 시험: 데이터 키 `(source, dataset, as_of)` 마다 성공 수집 1회(`ops.data_claim` 충돌 0, 가짜 서버 요청 목록 대조), 등록부 정적 검사(같은 데이터셋을 두 작업이 갖지 않음) | F, I |

### 0.3 PLAN·앞 문서와 달라지는 점 (이 설계가 정한 것)

| # | 내용 | 이유 |
|---|---|---|
| D-P2-1 | P2 어댑터에서 **네이버 제외** | U4. PLAN §8 P2 문구("어댑터: KRX, DART, 네이버…")보다 ADR 0001 이 뒤에 확정됐다 |
| D-P2-2 | KIS **앱키·시크릿은 KIS REST 를 부르는 프로세스에도 필요**하다(모든 REST 요청 헤더에 `appkey`·`appsecret`) → "앱키는 auth 컨테이너에만"(conflict_map §1.1 이행 1번·secrets.md)은 그대로 지킬 수 없다 | GX:`data/kis/rest.py:_send`(헤더), ET:`board/ingest/kis.py:_headers`:146, SD:`kis_api.py:_headers`:82. 발급 차단은 **코드 구조**로 한다(§3.6) — **[결정 D1] 안 A 확정**(ADR 0004, secrets.md §4) |
| D-P2-3 | ~~시뮬레이션의 "하루"는 23시간 창~~ → **[결정 D3] 24시간 창**(05:00 → 다음 날 05:00 KST). 단언은 "어떤 23시간 구간에도 접근토큰 발급 ≤ 1" — 23시간 창 시험은 따로 만들지 않는다 | 토큰 수명 24시간·만료 60분 전 갱신이라 24시간 창에는 발급이 2회(05:00·다음 날 04:00) 걸린다. 갱신이 정상 동작한다는 것도 같은 시험이 보인다(§10.3) |
| D-P2-4 | KIS **웹소켓 호스트**(`ops.koreainvestment.com`)는 P7 까지 기준선 그룹 | ws-gateway 는 GX legacy 로 남는다(P7 승격). 접속키는 이미 auth 의 Redis 값을 읽기만 한다(GX `services/ws_gateway/client.py:250`) — **[결정 D2] 확정**: 연결 코드는 P7 까지 legacy GX(`check_canonical` 기준선 그룹 `kis_ws`, 지금 `legacy/gexlab/config/kis_ws.yaml` 4줄), 접속키 **발급**은 지금부터 auth 만 |
| D-P2-5 | GX 스케줄러의 GEX 단계(마스터·KRX 파생 일별·분봉·무결측·개장 확인)는 legacy GX 에 두고, 등록부에는 `runner: external` 로 **데이터 키만** 등록 | 그 단계들은 GX `Scheduler.step`(GX:`services/scheduler/service.py:187`) 안에 얽혀 있다. 키만 먼저 등록해 두면 다른 작업이 같은 데이터를 받는 것을 P2 부터 막는다 |
| D-P2-6 | legacy 가 KBJ 어댑터를 쓰는 길은 **논리 URL 브리지**(`kbj/data/legacy_bridge.py`) 하나 | legacy 파일마다 호출 구조를 바꾸지 않고 "주소 상수 + 세션/`requests` 이름" 몇 줄만 고친다(§3.7) |

---

## 1. 모듈 목록

열: **kbj 최종 경로 | 승격 원본(레포:파일:줄) | 공개 API(시그니처) | 의존 | 테스트(옮겨 올 원본 / 새로 쓸 것)**. 원본 시험은 **import 경로만 바꿔** 그대로 옮기는 것이 원칙이다. 로그인 등급 fixture(KIS·KRX)는 `tests/fixtures/synthetic/` 의 합성본을 쓴다(U3 — P1 이식이 만든 합성본을 옮긴다 **[확인 필요: P1 산출 경로]**). 묶음 기호(S0·A~I)는 §11.

### 1.1 S0 — 공유 기반 (먼저, 한 사람)

| kbj 최종 경로 | 승격 원본 | 공개 API | 의존 | 테스트 |
|---|---|---|---|---|
| `kbj/data/ratelimit.py` | GX:`data/kis/ratelimit.py` 전체(489줄: `Priority`:42, `RateLimitConfig`:96, `limiter_key`:151, `RedisRateLimiter`:332, `LocalRateLimiter`:421) | `Priority`(IntEnum P0~P4) · `RateLimitConfig(rate=4.0, capacity=1, floor_rate=1.0, hold_s=60.0, step_rate=0.5, step_s=60.0, debounce_s=1.0, tr_min_interval_s={"FHPIF05030100": 1.0}, …)` · `RateLimiter`(Protocol): `acquire(priority, tr_id, timeout: float \| None = None) -> None`, `on_rate_limited() -> float` · `RedisRateLimiter(redis, app_key: str, config=None, clock=None)`(GX 그대로) · **신규** `RedisRateLimiter.scoped(redis, source: str, scope: str, config=None, clock=None)` · `LocalRateLimiter(config=None, clock=None)` · `limiter_key(app_key) -> "rl:kis:<sha256 16자>"`(GX 와 같은 키 — 전환 기간 legacy GX 와 버킷 공유) · **신규** `scoped_key(source, scope) -> "rl:<source>:<sha256 16자>"` · `RateLimitTimeout`, `Clock`, `SystemClock` | redis, `kbj.store.redis_keys` | 옮김: GX `tests/unit/test_ratelimit.py`(24), `tests/property/test_ratelimit_properties.py`(3). 새로: `test_scoped_limiter.py`(출처별 키 분리, 같은 scope 공유), 설정별 속성 시험(어떤 1초 창도 `ceil(rate)` 이하 — §4.2 의 data.go.kr·KOSIS·DART·ECOS 값) |
| `kbj/data/budget.py` | GX:`services/scheduler/krx.py:KrxCallBudget`:87~160 일반화 | `DailyBudget(redis: Redis \| None, name: str, cap: int, *, key_fn: Callable[[date], str] \| None = None, ttl_s: int = 3*86400)` · `.used(at) -> int` · `.take(at) -> int`(상한이면 `BudgetExhausted`, 세지 않음) · **신규** `.exhaust(at, reason)`(공공데이터포털 GW `22`, DART `020` 을 받으면 그날 닫기) · `krx_budget(redis, cap) -> DailyBudget`(키 `krx:calls:<YYYYMMDD>` — GX `services/bus.py:krx_calls_key`:67 과 같다) | redis | 옮김: GX `test_scheduler_krx.py` 의 예산 시험 4개(:75·:89·:101·:127). 새로: `exhaust`, KST 자정 경계, 두 인스턴스 공유 |
| `kbj/data/http.py` | ET:`board/ingest/http.py`(`SECRET_PARAMS`:38, `scrub`:42, `why`:84) + GX:`data/krx/eod.py`(시간 제한·크기 상한 :31~34, `_get`:130) | `make_client(base_url: str, *, connect_s=5.0, read_s=30.0, transport=None) -> httpx.Client` · `get_capped(client, path, *, params, headers, max_bytes, total_s, clock) -> tuple[int, bytes]` · `scrub(msg: str, params: Mapping \| None = None) -> str`(안에서 `kbj.core.masking.mask_text`) · `why(resp) -> str` · `FetchError(source, dataset, status, reason)` | httpx, `kbj.core.masking` | 옮김: ET `board/tests/test_http_reason.py`(19 — unittest → pytest). 새로: 크기 상한·전체 시간 초과·오류 문구에 키 없음 |
| `kbj/data/spec.py` | 신규 | `Tier`(StrEnum `public`·`private`) · `AsOfKind`(Literal: `trade_date`·`prev_trading_day`·`us_trade_date`·`run_date`·`minute`·`slot10m`·`ten_day`·`month`·`quarter`·`event`) · `DatasetSpec(id: str, source: str, dataset: str, tier: Tier, limiter: str, budget: str \| None, published: str, as_of_kind: AsOfKind, store: str, notes: str = "")` · `DataKey(NamedTuple: source, dataset, as_of: str)` | pydantic | `test_spec.py`: id 형식 `SOURCE:dataset`, `tier=public` ⇔ `store` 가 `pub_*` |
| `kbj/store/redis_keys.py` | GX:`services/bus.py`:44~71 키 상수 + GX auth·ratelimit 키 | 상수·함수: `KIS_TOKEN="kis:token"`, `KIS_WS_KEY="kis:ws_key"`, `issue_blocked(key)`, `KIS_TOKEN_REJECTED`, `SESSION_STATE="session:state"`, `SESSION_EVENTS="session.events"`, `heartbeat_key(service)`, `krx_calls_key(day)`, `budget_key(source, scope, day)`, `NOTIFY_OUTBOX="notify:outbox"`, `notify_dedup_key(key)`, `NOTIFY_WEBHOOK_INFO`, `tg_update_key(update_id)`, `sched_run_lock(job, as_of)` | — | `test_redis_keys.py`(GX `test_services_runtime.py:40` "키 이름이 계약" 방식으로 고정) |
| `kbj/services/runtime/` (`health.py`·`log.py`·`heartbeat.py`·`backoff.py`·`threads.py`·`healthcheck.py`) | GX:`services/auth/health.py`(102줄), `services/runtime.py`(`setup_logging`:47, `log_event`:53, `install_stop`:73, `connect_redis`:79, `run_in_thread`:88, `tagger_for`:105, `Heartbeater`:115, `ServiceHealthSink`:173, `Backoff`:208), `services/healthcheck.py`(`check`:23, `main`:37) | `HealthEvent(kind, detail, at, severity, service)` · `HealthSink.emit(event)` · `MemoryHealthSink` · `LogHealthSink(logger=None, tagger=None)` · `log_event(logger, level, service, event, tag=None, **fields)` · `Heartbeater(redis, service, *, ttl_s=60, every_s=10.0, now)` `.beat(*, force=False, **status) -> bool` · `connect_redis(url: SecretStr) -> Redis` · `Backoff(initial=1.0, maximum=30.0)` · `healthcheck.main(argv) -> int` | redis, `kbj.core.calendar`(`tagger_for` — B 뒤에 붙인다) | 옮김: GX `tests/unit/test_services_runtime.py` 13개 중 하트비트·`log_event`·health sink·backoff·healthcheck 8개 **[추정 — 나머지 5개는 GEX 채널·봉투 시험이라 P7]** |
| `kbj/config/files.py` | 신규 | `config_path(name: str) -> Path`(`Settings.config_dir` 기준) · `load_yaml(name: str) -> object` | `kbj.config.settings` | `test_config_files.py` |
| `tests/fakes/clock.py` | GX:`tests/fakes/kis_server.py:FakeClock`:108 | `FakeClock(start)` — `.set(when)`, `.now()`, `.now_us()`, `.sleep(s)`(시각만 옮김) | — | (다른 시험이 쓴다) |

### 1.2 A — auth · KIS

| kbj 최종 경로 | 승격 원본 | 공개 API | 의존 | 테스트 |
|---|---|---|---|---|
| `kbj/data/private/kis/credentials.py` | GX:`config/settings.py:kis_base`:64 + `auth_client.token_owner`:82 | `KisCredentials(app_key: SecretStr, app_secret: SecretStr, env: Literal["real","vts"])`(frozen) · `.base_url`(비공개 상수 `_REAL_BASE`·`_VTS_BASE` 에서) · `.owner`(= `token_owner(base_url, app_key)`) · `KisCredentials.from_settings(settings) -> KisCredentials`(없으면 `TokenUnavailable`) · `token_owner(base_url, app_key) -> str`(GX 와 같은 해시 16자 — 전환 기간 legacy GX 와 같은 `owner`) | `kbj.config.settings` | 새로: `test_kis_credentials.py`(owner 가 GX 식과 같다, repr 에 값 없음) |
| `kbj/data/private/kis/token.py` | GX:`data/kis/auth_client.py`(`TokenError`:57, `TokenUnavailable`:61, `TokenRecord`:97, `IssuedToken`:117, `TokenProvider`:123, `TokenIssuer`:133, `TokenCache`:137, `_CLAIM_LUA`:318, `RedisTokenCache`:326, `CachedTokenProvider`:436) + GX:`services/auth/service.py:reader`:564 | `TokenRecord`, `IssuedToken`, `TokenProvider`·`TokenIssuer`·`TokenCache`(Protocol) · `RedisTokenCache(redis, key=KIS_TOKEN)` `.load()/.store(rec, now)/.discard(token)/.claim_issue(now, min_gap)/.next_issue_at(now)` · `CachedTokenProvider(cache, owner, issuer=None, *, refresh_margin=60분, min_issue_gap=61초, min_valid=1분, on_throttle="raise", max_wait=130초, now, sleep)` `.get() -> str`, `.invalidate()` · **`reader(redis, creds: KisCredentials, name: Literal["token","ws_key"]="token", *, now=utcnow) -> CachedTokenProvider`**(발급자 없음) · `access_token(*, settings=None, redis=None) -> str`(모듈 수준 편의) · `ws_approval_key(...) -> str` · `TokenUnavailable` · **삭제**: `FileTokenCache`:245, `FallbackTokenCache`:391, `default_token_provider`:539(토큰은 Redis 에만 — secrets.md `KIS_TOKEN_CACHE_PATH` 삭제) | redis, pydantic, `kbj.store.redis_keys` | 옮김: GX `tests/unit/test_auth_client.py` 28개 중 캐시·제공자 13개(파일 캐시 5개 :238~294·기본 제공자/폴백 3개 :423~475 는 버린다; `caches` 매개변수에서 파일 캐시를 뺀다). 새로: `test_reader_never_issues.py`(Redis 가 비면 `TokenUnavailable`, HTTP 0회), `test_invalidate_guard.py`(§3.4) |
| `kbj/data/private/kis/rest.py` | GX:`data/kis/rest.py`(`KisResponse`:69, `KisClient`:98, `minute_chart_params`, `redact`) + GX:`services/scheduler/minute.py:reader_kis_client`:323 | `KisResponse(status, body, elapsed_ms, tr_cont)` `.ok/.rt_cd/.msg_cd/.rate_limited/.token_rejected` · **`KisRestClient(creds: KisCredentials, token_provider: TokenProvider, rate_limiter: RateLimiter \| None, *, timeout=20.0, transport=None)`** — `token_provider` 필수(K7: GX `_provider`:120 의 기본 발급 경로 삭제) · `.get(path, tr_id, params, tr_cont="", *, priority=Priority.P2, timeout=None) -> KisResponse`(EGW00201 → 감속 후 1회 재시도, EGW00121·123 → `invalidate` 후 1회) · `.secrets()`, `.close()` · `KisRestClient.for_service(settings, redis, *, clock=None, now=None, transport=None)`(앱키 리미터 + reader) · `minute_chart_params(...)`, `redact(text, client)` | httpx, `kbj.data.ratelimit`, `.token` | 옮김: GX `tests/unit/test_kis_rest.py` 13개 중 11개(:113 토큰 파일 권한·:235 Redis 없을 때 파일 캐시 폴백 2개는 버린다). 새로: provider 없이 만들면 `TypeError` |
| `kbj/data/private/kis/master.py` | GX:`data/kis/master.py`(`MasterRow`:90, `parse_master_line`:137, `parse_master`:165, `master_text_from_zip`:179, `parse_master_zip`:191, …) + GX:`services/scheduler/service.py:http_master_downloader`:104 | 위 이름 그대로 + `download_fo_master(*, transport=None, connect_s=5, read_s=15, total_s=30, max_bytes=32MiB, clock) -> bytes`(배포 URL 은 비공개 상수 — legacy 는 브리지 `kis-master:` 로만) | httpx | 옮김: GX `test_kis_master.py`(17 — 합성 `master_lines.json`), `test_scheduler_service.py` 의 내려받기 시험 3개(:487·:498·:511) |
| `kbj/data/private/kis/datasets.py` | 신규(TR 목록은 ET:`board/ingest/kis.py:ENDPOINTS`:49, SD:`kis_api.py`, GX:`services/poller/endpoints.py`) | `DATASETS: tuple[DatasetSpec, ...]` — §6.7 의 `KIS:*` 데이터셋 | `kbj.data.spec` | 등록부 검증 시험이 쓴다 |
| `kbj/services/auth/issuer.py` | GX:`data/kis/auth_client.py`(`TOKEN_PATH`:46, `ISSUE_THROTTLED_CODE`:47, `TokenIssueError`:65, `TokenIssueThrottled`:69, `_TokenResponse`:156, `KisTokenIssuer`:165, `_expiry`:220) + GX:`services/auth/service.py`(`APPROVAL_PATH`:91, `KisApprovalKeyIssuer`:465) | `KisTokenIssuer(http: httpx.Client, app_key: SecretStr, app_secret: SecretStr, now=utcnow)` `.issue() -> IssuedToken` · `KisApprovalKeyIssuer(http, app_key, app_secret, *, life=12시간, now)` `.issue() -> IssuedToken` · `TokenIssueError`, `TokenIssueThrottled` · **이 파일만** `oauth2/tokenP`·`oauth2/Approval` 문자열을 갖는다 | httpx, `kbj.data.private.kis.token` | 옮김: GX `test_auth_client.py` 발급자 시험 7개(:332~:415), `test_auth_service.py` 의 접속키 시험(:632~) |
| `kbj/services/auth/service.py`, `__main__.py` | GX:`services/auth/service.py`(`Credential`:119, `CredentialStatus`:129, `AuthStatus`:145, `next_wait`:161, `AuthService`:186 — `step`:233, `run_forever`:364; `build_auth_service`:531, `calendar_tagger`:587, `serve`:598, `heartbeat_status`:637, `main`:652) | `AuthService(credentials, sink, *, refresh_margin, retry_gap, expiring_margin, min_valid, secrets, limiter, permit_timeout, now)` `.step(now) -> AuthStatus`, `.run_forever(stop, *, interval=30.0, on_status=None) -> int` · `build_auth_service(settings, redis, http, sink, *, ws_key=True, ws_key_life=12h, limiter=None, now) -> AuthService` · `serve(settings, stop, *, redis=None, http=None, sink=None, interval=30.0, ws_key=True, limiter=None, on_status=None) -> int` · `heartbeat_status(status) -> dict` · 진입점 `python -m kbj.services.auth` | `.issuer`, `kbj.data.private.kis.token`, `kbj.data.ratelimit`, `kbj.services.runtime`, `kbj.core.calendar`(태거만) | 옮김: GX `tests/unit/test_auth_service.py`(52). 새로: `test_auth_guard.py`(§3.6 런타임 가드), `test_rejected_storm.py`(§3.4) |

### 1.3 B — 캘린더

| kbj 최종 경로 | 승격 원본 | 공개 API | 의존 | 테스트 |
|---|---|---|---|---|
| `kbj/core/calendar.py` | GX:`core/calendar.py` 전체(448줄: `KST`:38, `OVERRIDE_PATH`:42, 세션 경계 :47~53, `HolidayOverride`:70, `load_override`:92, `_xkrx_sessions`:101, `TradingCalendar`:117, `night_session_opens`:241, `State`:255, `SessionInfo`:265, `state_at`:279, `session_bounds`:313, `expiry_at`:326, `night_bar_time`:352, `day_bar_time`:366, `monthly_expiry`:397, `weekly_*`:403·412, `weeklies_listed`:436) + GX:`services/poller/context.py:session_tag`:49 | GX 이름 전부 그대로 · `TradingCalendar(*, extra_closed=(), extra_open=(), start, end, exchange: str = "XKRX")` — **신규 `exchange` 인자**(기본값이 XKRX 라 GX 시험은 그대로 통과) · **신규** `us_calendar() -> TradingCalendar`(XNYS, 덮어쓰기 없음) · **신규** `equity_session_bounds(d) -> tuple[datetime, datetime]`(주식 정규장 09:00~15:30 KST) · `is_equity_regular_hours(ts, cal) -> bool` · `session_tag(ts, cal) -> tuple[date, Session] \| None`(GX poller 에서 옮김) · `OVERRIDE_PATH` = 레포 루트 `config/holidays_override.yaml` **[제안]** | exchange_calendars(지연 import — GX :105), yaml, pydantic | 옮김: GX `tests/unit/test_calendar.py`(36), `tests/property/test_calendar_properties.py`(7), `tests/unit/test_dependencies.py` 의 XKRX 시험 1개(7경우). 새로: `test_xnys_calendar.py`, `test_equity_hours.py`, `test_legacy_holiday_parity.py`(§7.3) |
| `kbj/core/time.py` | ET `now_kst` 6벌(`board/engine/build.py:67`·`engine/db.py:16`·`xdigest/analyze.py:62`·`etf_tracker_v9/dash.py:17`), SD 4벌(`server.py:57`·`kis_api.py:365`·`data_fetcher.py:54`·`data_freshness.py:316`) | `KST` · `utcnow() -> datetime`(벽시계를 읽는 유일한 함수 — 서비스 진입점에서만) · `to_kst(ts) -> datetime`(naive 거부) · `kst_date(ts) -> date` | — | 새로: `test_time.py` |
| `kbj/core/calendar_compat.py` | SD:`server.py:_is_kr_holiday`:17987·`_next_trading_open_kst`:17994·`is_market_hours`:174, SD:`kis_api.py:_is_kr_market_hours`:146 | legacy 대체용: `is_kr_holiday(ts) -> bool`, `next_trading_open(ts) -> datetime`, `is_kr_regular_hours(ts) -> bool` — 안에서 `TradingCalendar.default()`·`equity_session_bounds` 만 부른다 | `.calendar` | 새로: `test_calendar_compat.py` |
| `config/holidays_override.yaml` | GX:`config/holidays_override.yaml`(2026-06-03 지방선거) | (데이터) | — | GX 시험이 읽는다 |

### 1.4 C — 공개 어댑터 (`kbj/data/public`) + 공공데이터포털 공통

| kbj 최종 경로 | 승격 원본 | 공개 API | 의존 | 테스트 |
|---|---|---|---|---|
| `kbj/data/datago.py`(공개·로그인 공용 전송) | ET:`board/ingest/datago.py`(`key_forms`:46, `call`:67, `_rows`:85) + `probe_results.md` §1(GW 오류 코드) | `DatagoTransport(service_key: SecretStr, *, limiters: Mapping[str, RateLimiter], budgets: Mapping[str, DailyBudget], transport=None, timeout=20.0)` `.call(dataset_id: str, path: str, params: dict, *, want: Literal["json","xml"]) -> DatagoResponse` · `key_forms(raw: str) -> list[tuple[str, str]]`(디코딩/그대로 — 통한 형태 기억) · `parse_gw_error(text) -> GwError \| None`(`22` 일 한도 → 예산 닫기, `23` 초당 → 감속, `30`·`31` 키, `12` 폐기, `05` 시간 초과) · `unwrap_items(js) -> tuple[list[dict], int]` | `kbj.data.http`, `.ratelimit`, `.budget`, defusedxml **[제안]** | 새로: `test_datago_transport.py`(키 형태 두 벌, GW 오류 XML 봉투, 예산 닫기) — ET 시험은 없음 |
| `kbj/data/public/dart/client.py` | ET:`dart-report/dartreport/client.py`(`BASE`:21, `REPRT`:24, `EMPTY_STATUSES`:32, `DartError`:35, `DartClient`:43 — `_cache_path`:66, `_wait`:73, `_get`:79, `get_json`:89, `get_zip`:107, `corp_code`:129, `financials`:151, `disclosures`:169, `document_texts`:196) | `DartClient(api_key: SecretStr \| str \| None = None, *, cache_dir: Path \| None = None, limiter: RateLimiter \| None = None, budget: DailyBudget \| None = None, transport=None, timeout=30.0)` — 위 메서드 그대로 + `get_raw(endpoint, params) -> bytes`(document.xml·corpCode.xml) · `DartClient.from_settings(settings, redis) -> DartClient` · `DartError(status, message, endpoint)` · 키 길이 40자 검사(경고), `013` 빈 결과, `020`(일 20,000건 초과 — ET:`board/ingest/dart.py:43`) → 예산 닫기 | `kbj.data.http`, `.ratelimit`, `.budget` | 새로: `test_dart_client.py`(캐시 키에 `crtfc_key` 없음, 013·020, 스로틀). dart-report `tests_smoke.py` 는 리포트 모듈 시험이라 P4 |
| `kbj/data/public/dart/corp_code.py` | ET:`board/ingest/dart.py:corp_codes`:75 | `CorpCode(corp_code, stock_code \| None, corp_name, modify_date)` · `parse_corp_codes(zip_bytes: bytes) -> list[CorpCode]` | defusedxml | 새로: 실데이터 corpCode 발췌 fixture(공개 등급 — `tests/fixtures/public/dart/`) |
| `kbj/data/public/dart/disclosures.py` | ET:`board/ingest/dart.py`(`STATUS_KO`:37, `_decode_status`:55, `_check`:67, `company`:105, `disclosures`:116, `stock_actions`:152, `KINDS`:183, `kind_of`:196, `disclosures_for`:210) | `decode_status(text) -> tuple[str, str] \| None`, `list_disclosures(client, bgn, end=None, page=1, count=100) -> DisclosurePage`, `disclosures_for(client, code, asof, *, timeout=None, retries=None)`, `stock_actions(client, code, bgn, end)`, `kind_of(title) -> str`, `company(client, code)` | `.client` | 옮김: ET `board/tests/test_dart_for.py`(7). ET `test_split_actions.py`(7)·`test_financials.py`(12)는 P4 |
| `kbj/data/public/ecos/client.py` | ET:`monitor/bok/fetch-ecos.js`(:21 BASE, :23~32 8계열, :55 URL 형식, :62~63 오류) — 로직만 Python 으로(bok 은 이식하지 않음, U3) + `probe_results.md` §4 | `EcosClient(api_key: SecretStr, *, limiter: RateLimiter, transport=None, timeout=60.0)` `.search(stat_code, cycle, start, end, items: tuple[str, ...] = ()) -> list[EcosRow]`(StatisticSearch), `.table_list()`, `.item_list(stat_code)` · `EcosError(code, message)` · `INFO-200` → 빈 목록, `602` → `limiter.on_rate_limited()` + `EcosThrottled` · `PUBLIC_TABLES`(722Y001·817Y002·404Y014·402Y014·401Y015·161Y005·301Y013·200Y102·513Y001·731Y003·721Y001) — 그 밖의 표는 `TierError` | `kbj.data.http`, `.ratelimit` | 새로: `test_ecos.py`(공개 예시 키로 받은 실데이터 fixture는 공개 등급 표만 — `probe_results.md` §4.3) |
| `kbj/data/public/kosis/client.py` | 신규(`probe_results.md` §5) | `KosisClient(api_key: SecretStr, *, limiter, transport=None)` `.parameter_data(org_id, tbl_id, obj_l1, itm_id, prd_se, *, start=None, end=None, newest=None) -> list[KosisRow]`, `.meta(org_id, tbl_id)` · HTTPS 만 | 같음 | 새로: `test_kosis.py`(합성 — 실데이터는 키 받은 뒤 공개 fixture 로 교체 **[확인 필요]**) |
| `kbj/data/public/customs/client.py`, `models.py` | 신규(`probe_results.md` §2) | `CustomsClient(datago: DatagoTransport)` · `.item_country(start_ym, end_ym, country, hs=None)`(15100475, 1년 한도 검사) · `.items(start_ym, end_ym, hs=None)`(15101609) · `.countries(start_ym, end_ym, country=None)`(15101612) · `.sigungu(start_ym, end_ym, sido, hs6)`(15134343) · `.ten_day(kind: Literal["exp_item","exp_country","imp_item","imp_country"], start_ym, end_ym)`(15157908·941·901·909 — **열 번호→이름 대응을 저장하고 바뀌면 실패**, `probe_results.md` §2.6 주의) · `SIDO_CODES`(2026-07 개편 반영, §2.5) · 금액 문자열(쉼표·앞 공백) 정규화 | `kbj.data.datago`, defusedxml | 새로: `test_customs.py`(XML 파서, `hsCode` 앞자리 0, 10일 잠정치 열 대응) |
| `kbj/data/public/fsc_kofia_stats/client.py` | 신규(`probe_results.md` §3.1) | `KofiaStatsClient(datago)` `.credit_balance(begin, end)`, `.market_capital(begin, end)`, `.fund_nav(bas_dt, ctg, tst_mthd_ctg)`, `.cma(begin, end)` — `endBasDt` 가 '미만'이라 하루 더해 부른다 **[확인 필요]** | 같음 | 새로: `test_kofia_stats.py` |
| `kbj/data/public/*/datasets.py` | 신규 | 출처별 `DATASETS` | `kbj.data.spec` | 등록부 검증 |

### 1.5 D — 로그인 어댑터 (`kbj/data/private`, KIS 제외)

| kbj 최종 경로 | 승격 원본 | 공개 API | 의존 | 테스트 |
|---|---|---|---|---|
| `kbj/data/private/krx/client.py` | GX:`data/krx/eod.py`(`FUT_DAILY`:28, `OPT_DAILY`:29, `KrxError`:51, `KrxDailyResponse`:59, `KrxClient`:68 — `from_settings`:96, `daily`:104, `_get`:130, `_redact`:150) + SD:`krx_api.py` 엔드포인트 9개(:9~19) | `KrxClient(api_key: SecretStr, *, base_url=_KRX_BASE, connect_s=5.0, read_s=30.0, total_s=120.0, max_bytes=64MiB, transport=None, clock=time.monotonic, limiter: RateLimiter \| None = None, budget: DailyBudget \| None = None)` `.daily(endpoint, bas_dd: date) -> list[dict]`(GX 그대로 — `OutBlock_1` 없으면 오류, 빈 날 = `[]`) · 이름 붙은 메서드: `stock_daily(market: Literal["kospi","kosdaq","konex"], bas_dd)`, `stock_base_info(market, bas_dd)`, `index_daily(series: Literal["kospi","kosdaq","krx"], bas_dd)`, `etf_daily(bas_dd)`, `etn_daily(bas_dd)`, `futures_daily(bas_dd)`, `options_daily(bas_dd)` · 호출마다 `budget.take` | `kbj.data.http`, `.budget` | 옮김: GX `test_krx_client.py`(7), `test_krx_models.py`(16) — 합성 `opt_daily.json`·`fut_daily.json`. 새로: 주식·지수·ETP 엔드포인트 합성 시험 |
| `kbj/data/private/krx/stocks.py` | ET:`board/ingest/krx.py`(`PATHS`:29, `FIELD`:36, `_isu_to_code`:83, `_norm`:103, `fetch_day`:123, `fetch_index`:136, `sector_map`:153) | `KrxStockRow`, `normalize_stock_row(r) -> KrxStockRow`, `isu_to_code(raw) -> str`, `parse_index_rows(rows)`, `sector_map(rows) -> dict[str, str]` | — | 새로: 합성 행 파서 시험(ET `test_close_source`(25)는 파이프라인 시험이라 P3) |
| `kbj/data/private/krx/models.py` | GX:`data/krx/models.py` | `KrxOptionDaily`, `KrxFuturesDaily` | pydantic | (위 GX 시험) |
| `kbj/data/private/fsc_stock_price/client.py` | ET:`board/ingest/datago.py`(`PRICE`:29, `_norm`:104, `fetch_day`:127, `fetch_ohlcv`:148) — **V2 경로로**(`probe_results.md` F7) | `FscStockPriceClient(datago)` `.day(bas_dt) -> list[FscPriceRow]`, `.ohlcv(code, start, end)` · `numOfRows` 최대 1만 | `kbj.data.datago` | 새로: 합성 시험 |
| `kbj/data/private/fsc_index_price/client.py` | ET:`board/ingest/datago.py`(`INDEX`:30, `fetch_index`:169) | `FscIndexPriceClient(datago)` `.index(name, start, end)` | 같음 | 새로: 합성 시험 · 데이터셋 ID **[확인 필요]**(DATA_TIERS 에 지수시세정보 ID 없음) |
| `kbj/data/private/ecos_restricted.py` | — | ECOS 타기관 표(802Y001·731Y001·901Y056·901Y009) 전용 래퍼 — `kbj.data.public.ecos` 전송층을 재사용(로그인 → 공개 import 는 허용) | `kbj.data.public.ecos` | 새로: 공개 클라이언트가 이 표를 거절하는지 |
| `kbj/data/private/*/datasets.py` | 신규 | `DATASETS` | `kbj.data.spec` | 등록부 검증 |

### 1.6 E — notifier (`kbj/services/notifier`)

| kbj 최종 경로 | 승격 원본 | 공개 API | 의존 | 테스트 |
|---|---|---|---|---|
| `telegram_api.py` | ET:`board/report/telegram.py`(`API`:50, `_safe_err`:53, `_why`:770, `send`:779, `send_document`:822, `check`:857), ET:`monitor/flow/telegram.py:send_photos`:36(sendMediaGroup), bok `send-telegram.js:34`(429 `retry_after` 4회) | `TelegramApi(token: SecretStr, *, transport=None, timeout=20.0, sleep=time.sleep)` · `.send_message(chat_id, text, *, thread_id=None, parse_mode="HTML", silent=False) -> SendResult` · `.send_document(chat_id, path, caption, *, thread_id=None)` · `.send_media_group(chat_id, paths, caption, *, thread_id=None)` · `.set_webhook(url, secret, allowed_updates, *, drop_pending=False)` · `.get_webhook_info()` · `.get_me()` · `.get_chat(chat_id)` · **`api.telegram.org` 는 이 파일에만** | httpx, `kbj.core.masking` | 옮김: ET `test_telegram.py` 의 `TestSend`(5)·`TestCheckDestination`(3)·`TestPlainTextMode`(2), `test_send_files.py:SendDocumentTest`(2). 새로: 429 재시도, 오류 문구에 토큰 없음 |
| `format.py` | ET:`board/report/telegram.py:_split`:237(`TG_LIMIT` 4096, `TG_CAPTION_LIMIT` 1024), SD:`server.py:_split_telegram_lines`·`send_telegram_long`:5273(`(i/n)` 머리) | `split_text(text, limit=4096) -> list[str]`, `with_part_headers(chunks, parse_mode) -> list[str]`, `cap_caption(text, limit=1024) -> str` | — | 옮김: ET `TestSplit`(2). 새로: HTML 태그가 조각을 넘지 않음 |
| `policy.py` | inventory (d-2) 토픽 4개, SD `_alert_cooldown_ok`:6102(60분), GX 설계 쿨다운 10분 | `Topic`(StrEnum 시장·신고가·알림·운영) · `KindPolicy(kind, topic, once_per_day, cooldown_s, dedup, parse_mode, silent, max_parts, catch_up_until)` · `load_policies() -> dict[str, KindPolicy]`(`config/notify.yaml`) · `dedup_key(policy, *, as_of, subject, body, now) -> str` | `kbj.config.files` | 새로: `test_policy.py` |
| `outbox.py` | 신규 | `OutboundMessage(kind, text \| None, document: Path \| None, media: tuple[Path, ...], as_of, subject, chat \| None, thread_id \| None, parse_mode, source)` · `Outbox(redis, *, now)` `.put(msg) -> NotifyTicket`(중복 키 `SET NX` + `XADD notify:outbox`), `.read(consumer, n) -> list`, `.ack(msg_id)` | redis | 새로: `test_outbox.py`(fakeredis — 같은 키 두 번 → 두 번째 `duplicate`) |
| `client.py`(공용 shim) | SD:`server.py:send_telegram`:5205·`send_telegram_long`:5273, SD:`earnings_telegram_sender.py:send_telegram_message`:51, ET:`board/report/telegram.py:send`:779·`send_document`:822, ET:`etf_tracker_v9/tracker.py:send_telegram`:395, ET:`monitor/flow/telegram.py:send_photos`:36 — **7벌의 대체** | `notify(text, *, kind, as_of=None, subject=None, parse_mode="HTML", silent=False, source) -> NotifyTicket` · `notify_document(path, caption="", *, kind, ...)` · `notify_media(paths, caption="", *, kind, ...)` · **`legacy_send(text, *, source, parse_mode="HTML", kind=None, numbered=False) -> tuple[bool, str]`**(ET 식 반환) · `legacy_send_document(path, caption="", *, source, kind=None)` · `legacy_send_media(paths, caption="", *, source, kind=None)` · `webhook_status() -> dict` · `NotifyTicket(ok, id, reason, duplicate)` | `.outbox`, `.policy` | 새로: `test_client_shim.py`(legacy 반환 모양 그대로, 꺼짐 모드 사유, Redis 없음 → `(False, 사유)`) |
| `service.py`, `__main__.py` | 신규 | `NotifierService(outbox, api, policies, store, settings, *, now, limiter)` `.drain_once() -> int`, `.health() -> dict` · `run(service, stop, *, step_s=1.0, clock, wait)` · `python -m kbj.services.notifier [run \| setup-webhook \| webhook-info]` | `.telegram_api`, `.store`, `kbj.data.ratelimit` | 새로: `test_service.py`(꺼짐 → `suppressed`, 켜짐 → 가짜 텔레그램 1회, 실패 → 재시도·`failed`) |
| `webhook.py` | SD:`server.py:api_telegram_webhook`:5860(시크릿 헤더·소유자 chat), `_telegram_setup_webhook`:5882 | **[결정 D4]** `WebhookHandler.handle(요청 dict) -> WebhookResult`(순수 처리 — 시크릿·`update_id` 중복·`notify:inbound` XADD 를 Lua 한 번에). `router`·`build_app`(FastAPI)은 P3 API 가 이 함수를 감싼다 | — (fastapi 넣지 않음) | 새로: `test_webhook.py`(시크릿 `hmac.compare_digest`, 허용 chat, `update_id` 중복, 명령/인박스 갈래) |
| `commands.py` | SD:`server.py:_handle_telegram_command`:5823·`_tg_help`·`_tg_cmd_price`·`_tg_cmd_flow`·`_resolve_kr_code` | `CommandRegistry.register(name, aliases, handler, *, phase)` · `.dispatch(text, chat_id, thread_id) -> Reply` · 내장: `/도움`(help·start·명령) — P2 동작, 나머지는 '준비 중(Pn)' 답(§5.7) | `.client` | 새로: `test_commands.py`(`@봇이름` 접미사, 모르는 명령, 인자 없음) |
| `inbox.py`, `store.py` | ET:`board/ingest/tg_inbox.py`(`_norm_ids`:87, `_allow_sets`:102, `_chat_allowed`:117, `extract_urls`:173, `is_x_url`:204, `x_ids_and_author`:218, `_forward_name`:232, `parse_update`:266, `oembed`:308, `fill_oembed`:333, `merge`:410) | `parse_update(up) -> InboxItem \| None`(ET 계약 그대로) · `extract_urls(text, entities)` · `x_ids_and_author(urls)` · `fill_oembed(item, client)` · `InboxStore.upsert(items)` · **`read_items(chat_ids, since) -> dict`**(ET `state/inbox.json` 모양 그대로 — legacy ET 가 읽는다) · `NotifyLogStore.record(...)` | psycopg, httpx(oEmbed) | 옮김: ET `test_tg_inbox.py` 39개 중 27개(Links 9·Whitelist 4·DedupExpiry 4·OEmbed 7·Other 3). 버림 12개(Paging 4·Webhook 3·CmdInboxExit 2·ChatIdsFromCreds 1·파일 손상 2 — getUpdates·파일 저장 폐지) |

### 1.7 F — scheduler + 작업 등록부

> 구현 메모: API 가 아래 표와 조금 다르다 — `JobRunner.tick(now)`(+`runs`·`catalog` 인자), `JobContext(run_id, attempt, keys, resources)`, `ClaimStore.complete·fail(…, now)`. 등록부가 §6.7 과 다르게 정한 것은 ADR 0006 §3.

| kbj 최종 경로 | 승격 원본 | 공개 API | 의존 | 테스트 |
|---|---|---|---|---|
| `kbj/core/cron.py` | 신규(외부 라이브러리 대신 5필드 부분집합 — 목록·범위·간격) **[제안]** | `CronSpec.parse(expr) -> CronSpec`, `.matches(local: datetime) -> bool`, `.next_after(local) -> datetime` | — | 새로: `test_cron.py` + 속성 시험(next_after 는 matches 이고 그 사이에 matches 없음) |
| `kbj/services/scheduler/registry.py` | 신규(conflict_map §1.4 필드: 이름·시각·세션 트리거·거래일 조건·의존·재시도·마감·데이터 키·토픽) | `JobSpec`, `ScheduleSpec`, `RetrySpec`, `CollectSpec(source, dataset, as_of: AsOfKind)`, `ServiceSpec` · `Registry.load(path) -> Registry` · `.validate(catalog) -> list[str]` · `.jobs`, `.by_name(name)` | pydantic, `kbj.core.cron`, `kbj.data.spec` | 새로: §6.6 |
| `kbj/services/scheduler/conditions.py` | GX `core.calendar` 위에 신규 | `holds(cond, local_date, kr: TradingCalendar, us: TradingCalendar) -> bool` · `resolve_as_of(kind, now, kr, us) -> str` | `kbj.core.calendar` | 새로: `test_conditions.py`(10-05 대체공휴일, 10-02 금요일 밤, 미국 서머타임 끝 11-01) |
| `kbj/services/scheduler/claims.py` | 신규 | `ClaimStore`(Protocol) `.claim(key: DataKey, job, run_id, now) -> bool`, `.complete(key, run_id, rows)`, `.fail(key, run_id, reason)` · `MemoryClaimStore` · `PgClaimStore(conn)`(`ops.data_claim`) | psycopg | 새로: `test_claims.py`(같은 키 두 번째 claim 거절, 실패 뒤 재claim 허용) |
| `kbj/services/scheduler/runner.py`, `handlers.py` | 신규 | `JobRunner(registry, handlers, claims, kr, us, *, now, submit, health, notify=None, run_planned=False)` `.tick(now) -> list[RunEvent]` · `JobContext(job, as_of, now, claims, settings, redis)` · `JobResult(status, collected: list[DataKey], detail)` · `resolve(owner: str) -> Callable[[JobContext], JobResult]` | `.registry`, `.conditions`, `.claims` | 새로: `test_runner.py`(가짜 시계: 재시도 간격·마감, 의존, 캐치업, 같은 (job, as_of) 두 번 안 돎) |
| `kbj/services/scheduler/service.py`, `__main__.py` | GX:`services/scheduler/service.py`(`Scheduler`:143 의 `step`:187·`_transition`:200·`_publish`:225 — 세션 상태 발행 부분만, `run`:408) | `SchedulerService(redis, runner, kr, store, *, now, health, state_refresh_s=30)` `.step() -> SessionInfo` · `run(service, stop, *, heartbeat=None, step_s=1.0, clock, wait)` · `python -m kbj.services.scheduler [run \| validate \| run-once <job> --as-of …]` | `kbj.core.calendar`, `kbj.services.runtime` | 옮김: GX `test_scheduler_service.py` 23개 중 상태 발행 7개(:184·:229·:242·:303·:321·:330·:338). 마스터·KRX·분봉 시험(16)은 P7 까지 legacy |
| `kbj/data/catalog.py` | 신규 | `all_datasets() -> dict[str, DatasetSpec]`(각 어댑터 `datasets.py` 를 명시적으로 모은다), `get(id) -> DatasetSpec` | `kbj.data.public.*.datasets`, `kbj.data.private.*.datasets` | `test_catalog.py`(id 유일, 등급·저장 스키마 일치) |
| `kbj/services/collectors/corp_code.py` | SD:`server.py:init_dart_corp_map_db`:10267·`_load_dart_corp_code_map`:10453, ET:`board/ingest/dart.py:corp_codes`:75 | `run(ctx: JobContext) -> JobResult` — `DART:corpCode` → `pub_filings.corp_code` 갈아 넣기 | C, G | 새로: 가짜 DART + 메모리 저장 |
| `kbj/services/collectors/ops_watchdog.py` | SD:`server.py:_market_watchdog`:14998·`_watchdog_notify`:14989(내용만 참고) | `run(ctx) -> JobResult` — 등록부의 마감 지난 실행·하트비트 끊긴 서비스 → `ops.watchdog` 알림 | E, G | 새로 |
| `config/jobs.yaml` | inventory (c) 전부 | §6.2 형식 | — | §6.6 |

### 1.8 G — DB

| kbj 최종 경로 | 승격 원본 | 공개 API | 의존 | 테스트 |
|---|---|---|---|---|
| `kbj/store/migrate.py` | GX:`db/migrate.py`(152줄: `MIGRATIONS_DIR`:28, `LOCK_KEY`:30, `_FILE`:31, `BOOTSTRAP`:33, `Migration`:50, `discover`:60, `pending`:74, `apply`:92, `migrate`:118, `main`:129) | 같은 이름 · 기록 표는 **`ops.schema_migrations`**(0001 이 만든 표 — `BOOTSTRAP` 을 `CREATE SCHEMA IF NOT EXISTS ops; CREATE TABLE IF NOT EXISTS ops.schema_migrations …` 로) · `LOCK_KEY` 는 KBJ 고유 상수 · `python -m kbj.store.migrate` | psycopg | 새로: `test_migrate_unit.py`(discover·pending·체크섬 불일치), 통합 `tests/integration/test_migrations_pg.py`(0001~0006 두 번 적용, `kbj_public_export` 는 `pub_*` 만) |
| `kbj/store/migrations/0002~0006_*.sql` | §8.2 | (DDL) | — | `tests/test_store_layout.py` 확장(파일명·멱등·스키마 접두사) |
| `kbj/store/db.py` | GX:`data/store.py`(연결·재시도 부분) | `connect(settings, *, autocommit=False, search_path: Sequence[str] \| None = None) -> psycopg.Connection` | psycopg | 통합 |
| `kbj/store/spool.py` | GX:`data/spool.py`(538줄: `DiskSpool`:237, `encode_batch`:137, `decode_batch`:155) | GX 그대로 | — | 옮김: GX `test_spool.py` 38개 중 DiskSpool 자체 시험 **[추정 20여 개 — GX 레코드(poller·recorder)를 쓰는 나머지는 P7]** |
| `kbj/store/legacy_import/` (`__main__.py`·`mappings.py`·`transforms.py`·`sources.py`·`verify.py`) | conflict_map §1.5 표, inventory (e) | `Mapping(source_db, source_table, target, select_sql, transform, key_cols, sum_cols, phase, drop)` · `MAPPINGS: tuple[Mapping, ...]` · `run(sources: dict[str, Path], conn, *, phase: str, dry_run: bool) -> ImportReport` · `verify(...) -> VerifyReport` · `open_sqlite_ro(path) -> sqlite3.Connection` · `file_sha256(path)` · 진입점 `python -m kbj.store.legacy_import --source sd=… --source board=… --phase P2 [--dry-run \| --verify-only]` | psycopg, sqlite3 | 새로: `test_import_transforms.py`(순수), `test_import_sqlite.py`(옛 DDL 로 만든 합성 SQLite → 메모리 대상), 통합 `test_import_pg.py`(두 번 돌려 같은 결과) |

### 1.9 H — legacy 재배선

| kbj 최종 경로 | 승격 원본 | 공개 API | 의존 | 테스트 |
|---|---|---|---|---|
| `kbj/data/legacy_bridge.py` | 신규(§3.7) | `get(url, params=None, headers=None, timeout=None, **kw) -> BridgeResponse`(`requests.get` 호환) · `session() -> BridgeSession`(`.headers`, `.get(url, params=None, timeout=None, **kw)`, `.close()`) · `BridgeResponse(status_code, text, content, headers)` `.json()`, `.raise_for_status()` · `access_token_or_none() -> str \| None` · 논리 URL `kis:`·`kis-master:`·`krx:`·`dart:` 만 받고 `http(s)://` 는 `ValueError` | A·C·D | 새로: `test_legacy_bridge.py`(호출자 키 파라미터를 지우고 KBJ 키를 넣음, 토큰 없음 → 503 봉투, 리미터 우선순위 헤더) |
| legacy 파일 패치 | §3.8·§5.10·§7.2 | — | — | legacy 프로젝트별 기존 시험 + 프로젝트마다 다리 시험 1개(예: `legacy/etf_traker/board/tests/test_kbj_bridge.py` — `kis.token()` 이 발급 POST 를 하지 않는다) |

### 1.10 I — 검사·시뮬레이션·통합

| kbj 최종 경로 | 승격 원본 | 공개 API | 의존 | 테스트 |
|---|---|---|---|---|
| `scripts/check_canonical.py`, `scripts/canonical_baseline.txt` | `scripts/check_public_safety.py` 형식(출력·종료 코드·허용 목록) | §9 | — | `tests/test_check_canonical.py` |
| `tests/fakes/kis_server.py`·`krx_server.py`·`ws_server.py` | GX:`tests/fakes/kis_server.py`(`FakeKisServer`:384 — `token_posts`:398, `max_in_window`:489, `handle`:503; `StaticTokenProvider`:707, `seed_cached_token`:729, `make_client`:743), `krx_server.py`(`FakeKrx`:56), `ws_server.py` | GX 그대로 + 주식 TR(FHKST01010100·FHKST01010900·FHPTJ04040000·FHPTJ04400000) 합성 응답, `/oauth2/Approval` 경로와 `approval_posts`, KRX 주식·지수·ETP 엔드포인트와 `publish_at(day, when)` | — | 시뮬레이션이 쓴다 |
| `tests/fakes/dart_server.py`·`datago_server.py`·`ecos_server.py`·`kosis_server.py`·`telegram_server.py` | 신규 | `httpx.MockTransport` 라우터 + 요청 목록 | — | 같음 |
| `tests/sim/` (`conftest.py`·`test_one_day.py`·`test_holiday.py`·`test_notify_on.py`) | GX:`tests/integration/test_full_day.py`(구조 — 가짜 시계 하나·창별 보폭) | §10 | 전부 | §10.4 |

### 1.11 테스트 배치 규칙

- 위치: `tests/unit/<영역>/test_*.py`, `tests/property/`, `tests/integration/`(Docker — `@pytest.mark.integration`), `tests/sim/`. 파일 이름은 레포 전체에서 유일하게 짓고, `pyproject` `addopts` 에 `--import-mode=importlib` 을 더한다 **[제안 — 지금 `tests/` 에 `__init__.py` 가 없어 같은 이름 두 개면 수집이 깨진다]**.
- GX 시험의 `Settings(...)` 생성은 KBJ `Settings` 로 바꾸고 KIS 생성자 차이(`KisCredentials`)는 `tests/unit/kis/conftest.py` 헬퍼 한 곳에서 흡수한다 — 시험 본문은 바꾸지 않는다.
- ET 시험(unittest, 상대 import)은 pytest 함수로 옮기되 단언 내용은 그대로 둔다.
- 승격한 함수의 원본은 legacy 에서 **kbj 를 다시 내보내는 한 줄**로 바꾼다(두 벌 금지 — CLAUDE.md §3). 예: ET `board/ingest/http.py` 의 `scrub`·`why` → `from kbj.data.http import scrub, why`.

### 1.12 옮겨 오는 원본 시험 수

| 묶음 | 원본 시험 | 수 |
|---|---|---|
| S0 | GX ratelimit 24 + 속성 3 + 예산 4, ET http_reason 19, GX runtime 8 [추정] | 58 |
| A | GX auth_client 20(캐시·제공자 13 + 발급자 7), auth_service 52, kis_rest 11, kis_master 17, 마스터 내려받기 3 | 103 |
| B | GX calendar 36 + 속성 7 + XKRX 1 | 44 |
| C | ET test_dart_for 7 | 7 |
| D | GX krx_client 7 + krx_models 16 | 23 |
| E | ET telegram 12 + send_files 2 + tg_inbox 27 | 41 |
| F | GX scheduler_service 7 | 7 |
| G | GX spool ≈20 [추정] | ≈20 |
| 합계 | | ≈303 |

---

## 2. 공개/로그인 배치

원칙은 DATA_TIERS §3: 등급은 **폴더·스키마로** 나눈다. 표 하나에 로그인 출처 값이 한 열이라도 섞이면 `prv_`(ADR 0002 §2.1).

| 어댑터(kbj 경로) | 등급 | 근거 | P2 데이터셋(카탈로그 id) | 저장 스키마.표 | 표 생성 |
|---|---|---|---|---|---|
| `kbj/data/public/dart` | 공개 | DATA_TIERS §1 ✅ | `DART:corpCode`, `DART:list`, `DART:document`, `DART:company`, `DART:fnlttSinglAcntAll`, `DART:fnlttMultiAcnt` | `pub_filings.corp_code`(P2) · `pub_filings.disclosure`·`overhang`·`earnings_actual`(P4) · `pub_fin.quarterly`·`pub_fin.ratio`(P4) | 0004(P2), 0008·0009(P4) |
| `kbj/data/public/ecos` | 공개(**한은 작성 표만**) | DATA_TIERS §1, `probe_results.md` F6·§4.3 | `ECOS:817Y002`(일), `ECOS:722Y001`, `ECOS:404Y014`·`402Y014`·`401Y015`·`161Y005`·`301Y013`·`200Y102`·`513Y001`·`731Y003`·`721Y001` | `pub_macro.series` | 0011(P5) |
| `kbj/data/private/ecos_restricted` | 로그인 | 802Y001 한국거래소·731Y001 서울외국환중개, 901Y056·901Y009 결정 전 | `ECOS:802Y001`, `ECOS:731Y001`, `ECOS:901Y056`, `ECOS:901Y009` | `prv_macro.series` | 0011(P5) |
| `kbj/data/public/kosis` | 공개(국내 통계) | DATA_TIERS §1, `probe_results.md` §5.3(국제통계 표는 로그인 [제안]) | `KOSIS:DT_1C8015`, `KOSIS:DT_1JH20201`, `KOSIS:DT_1DA7001S`, `KOSIS:DT_1J22003` … | `pub_macro.series` | 0011(P5) |
| `kbj/data/public/customs` | 공개 | 다섯 데이터셋 이용허락 "제한 없음" | `DATAGO:15100475`, `:15101609`, `:15101612`, `:15134343`, `:15157908`, `:15157941`, `:15157901`, `:15157909` | `pub_trade.item_country_monthly`·`item_monthly`·`country_monthly`·`sigungu_monthly`·`ten_day` | 0012(P6) |
| `kbj/data/public/fsc_kofia_stats` | 공개 | 15094809 "제한 없음"(공공누리 1유형) | `DATAGO:15094809/credit`, `/capital`, `/fund`, `/cma` | `pub_market_stats.credit_balance`·`market_capital`·`fund_nav`·`cma` | 0011(P5) |
| `kbj/data/private/fsc_stock_price`·`fsc_index_price` | 로그인 | 공공누리 4유형, 원천 KRX(DATA_TIERS §1 ❌) | `DATAGO:15094808`(주식, V2), 지수 ID **[확인 필요]** | `prv_market.daily_bar`(`source='fsc'` — 교차검증용) | 0003(P2) |
| `kbj/data/datago.py`(공용 전송) | — (데이터를 담지 않는 전송층) | 공개·로그인이 같은 `serviceKey` 를 쓴다(secrets.md `KBJ_DATAGO_KEY`) | — | — | — |
| `kbj/data/private/krx` | 로그인 | DATA_TIERS §1 | `KRX:sto/stk_bydd_trd`, `sto/ksq_bydd_trd`, `sto/knx_bydd_trd`, `sto/stk_isu_base_info`, `sto/ksq_isu_base_info`, `idx/kospi_dd_trd`, `idx/kosdaq_dd_trd`, `idx/krx_dd_trd`, `etp/etf_bydd_trd`, `etp/etn_bydd_trd`, `drv/fut_bydd_trd`, `drv/opt_bydd_trd` | `prv_market.stock_snapshot`·`daily_bar`·`universe`, `prv_gex.krx_fut_daily`·`krx_opt_daily` | 0003(P2), 0006(P2 골격) |
| `kbj/data/private/kis` | 로그인 | DATA_TIERS §1 | `KIS:stock_quote_eod`, `KIS:stock_investor_daily`, `KIS:market_investor_daily`, `KIS:inst_foreign_top`, `KIS:watch_quotes_intraday`, `KIS:fo_master`, `KIS:fut_minute_day`·`_night`, `KIS:option_chain_live`, `KIS:market_investor_intraday`, `KIS:consensus_estimate`[추정 TR], `KIS:quote_on_demand`(명령 응답 — 선점 대상 아님) | `prv_market.*`, `prv_flows.*`, `prv_gex.*` | 0003·0006(P2) |
| `kbj/data/legacy_bridge.py` | — (legacy 전용 다리, P9 에 삭제) | 공개·로그인 어댑터를 둘 다 부르므로 `kbj.data` 바로 아래(계약 ①은 public→private 만 막는다) | — | — | — |
| `kbj/data/catalog.py` | — | 메타데이터만(문자열) | — | — | — |
| (만들지 않음) 네이버 | 제외 | U4 | — | — | — |

공개 내보내기(Pages JSON, P3~)는 `kbj.data.public` 과 `pub_*` 만 쓴다 — DB 역할 `kbj_public_export`(ADR 0002 §2.2)와 import-linter 계약 ①이 두 겹으로 막는다.

---

## 3. auth — KIS 토큰 발급 한 곳

### 3.1 구성

```
                       ┌───────────────────────────── Redis ─────────────────────────────┐
 kbj/services/auth ──▶ │ kis:token (TTL=남은 수명) · kis:token:issue_blocked_until (61초)   │ ◀── reader() 읽기만
 (30초 step, 유일한     │ kis:ws_key · kis:ws_key:issue_blocked_until                       │      · kbj KisRestClient
  KisTokenIssuer·       │ rl:kis:<앱키 해시> (+:wait)  ← 발급 요청도 P0 허가                │      · legacy_bridge(SD·ET)
  KisApprovalKeyIssuer) │ health:heartbeat:auth                                             │      · legacy GX poller·ws(P7까지)
                       └──────────────────────────────────────────────────────────────────┘
```

### 3.2 Redis 키

| 키 | 값 | 수명 | 쓰는 곳 | 읽는 곳 | 근거 |
|---|---|---|---|---|---|
| `kis:token` | `TokenRecord` JSON(`access_token`·`expires_at`·`issued_at`·`owner`) | 남은 수명(PX) | auth | 모든 KIS REST 호출자 | GX:`auth_client.py:RedisTokenCache.store`:347, `service.py:TOKEN_KEY`:89 |
| `kis:token:issue_blocked_until` | 다음 발급 가능 시각(에포크 µs) | 61초 | auth(`_CLAIM_LUA`) | auth | GX:`auth_client.py`:318·371 |
| `kis:ws_key`, `kis:ws_key:issue_blocked_until` | 웹소켓 접속키 기록, 간격 | 12시간 가정 / 61초 | auth | ws-gateway(legacy GX → P7) | GX:`service.py`:90·94 |
| `rl:kis:<sha256(앱키)[:16]>`, `…:wait` | GCRA 상태 hash, 대기 zset | 900초 | 모든 KIS 호출자(발급 요청 포함, P0) | — | GX:`ratelimit.py:limiter_key`:151 |
| `health:heartbeat:auth` | 자격별 동작·만료 시각(값 없음) | 180초 | auth | healthcheck, `ops.watchdog` | GX:`service.py:heartbeat_status`:637·`main`:652(하트비트 :671) |
| `kis:token:rejected` (신규) | `{token_sha16, at, by}` | 1시간 | 읽는 쪽 `invalidate()` | auth | [제안] §3.4 |

키 이름은 GX 와 **같게** 둔다 — P2~P7 동안 legacy GX(poller·ws-gateway·scheduler 분봉)가 같은 Redis 에서 같은 토큰을 읽는다.

### 3.3 발급·갱신 규칙

| 규칙 | 값 | 근거 |
|---|---|---|
| 발급 간격 | 자격마다 **61초에 한 번 이하**. Redis Lua(`SET … PX` 를 '지금 ≥ 기존 값'일 때만)로 인스턴스끼리 공유 + 인스턴스 자체 직전 시도 시각 | GX:`auth_client.py:ISSUE_MIN_GAP`:48·`_CLAIM_LUA`:318, KIS `EGW00133` 실측 |
| 갱신 시점 | 만료 **60분 전** | `REFRESH_MARGIN`:49 |
| 최소 유효 | 1분 미만 남은 토큰은 없는 것으로 본다 | `MIN_VALID`:50 |
| 만료 계산 | 응답의 `access_token_token_expired` 와 `issued_at + expires_in` 중 이른 것 | `_expiry`:220, 시험 `test_issuer_posts_credentials_and_takes_earlier_expiry` |
| step | 30초. 갱신 못 한 자격이 있으면 다음 시도 시각(61초 뒤)에 맞춰 깬다 | GX:`service.py:STEP_INTERVAL_S`:96·`next_wait`:161 |
| 발급 요청 허가 | 앱키 리미터 **P0**, 5초 안에 못 받으면 이번 시도 실패 | `PERMIT_TIMEOUT_S`:97 |
| 접속키 | `POST /oauth2/Approval`, 응답에 만료 없음 → **12시간 가정**(11시간마다 갱신) **[확인 필요: 미실측]** | GX:`service.py`:22~24·94 |
| 소유 확인 | `owner = sha256(base_url + "\n" + app_key)[:16]` — 다른 앱키·환경(real/vts) 토큰은 무시 | `token_owner`:82 |
| 복제 수 | auth 는 **한 컨테이너**(compose `restart: unless-stopped`, replicas 1). 둘이 떠도 Lua 간격이 막는다 | — |

### 3.4 실패 시 동작

| 상황 | auth | 읽는 쪽 | 알림 |
|---|---|---|---|
| Redis 접속 불가 | 발급하지 않는다(받아도 둘 곳이 없다) — `serve` 기동 실패·재시작 | `TokenUnavailable` → 그 호출 실패, 다음 실행에 다시 | health `auth_redis_down`, 하트비트 끊김 → `ops.watchdog`(운영 토픽) |
| KIS 5xx·연결 오류 | 기존 토큰이 살아 있으면 둔다. 61초 뒤 다시 | 기존 토큰 그대로 | 남은 시간 < 10분이고 실패 중이면 `token_expiring`(1분에 한 번 이하) |
| `EGW00133`(1분 1회 초과 — 다른 발급자가 방금 발급) | 한 간격 기다린 뒤에도 못 채우면 경고 | 기존 토큰 | `token_throttled` — 레포 밖 발급자(K10) 의심 |
| 앱키·시크릿 오류(4xx) | 61초 간격으로만 다시(폭주 없음) | `TokenUnavailable` | critical `auth_credentials_rejected` |
| 토큰 만료·없음 | 즉시 발급 시도 | 만료 1분 전까지 쓰고, 그 뒤 `TokenUnavailable` — **발급하지 않는다** | — |
| KIS 가 토큰 거절(`EGW00121`·`EGW00123`) | 아래 가드 | `invalidate()` 후 1회 재시도(GX:`rest.py:get`) | — |
| ⚠ 거절 폭주 | **가드(구현 — ADR 0004 §4.3: '한 번 처리하면 닫는' 대신 '값이 바뀔 때까지 걸려 있는' 방식)**: `invalidate()` 는 키를 지우지 않고 `kis:token:rejected` 에 신고만 한다. auth 는 같은 토큰에 대한 신고를 한 번만 처리하고, 발급 10분 안에 거절 신고가 오면 다시 발급하지 않고 critical `token_rejected_after_issue` | 신고 후 다음 step(≤30초+61초) 뒤 새 토큰 | GX 는 `discard`(:352)로 키를 지운다 — 고장 난 호출자 하나가 하루 1,400번 발급을 일으킬 수 있다 **[추정 상한 = 86,400 ÷ 61]** |

### 3.5 읽기 전용 클라이언트 API (발급하지 않는다)

| 함수·클래스 | 하는 일 | 없을 때 |
|---|---|---|
| `kbj.data.private.kis.token.reader(redis, creds, name="token", *, now)` | `CachedTokenProvider(issuer=None)` — 메모리 → Redis, 만료 임박이어도 만료 1분 전까지 돌려준다 | `TokenUnavailable` |
| `kbj.data.private.kis.token.access_token(*, settings=None, redis=None) -> str` | 프로세스당 reader 하나를 만들어 둔 편의 함수 | 같음 |
| `kbj.data.private.kis.token.ws_approval_key(...) -> str` | `name="ws_key"` reader | 같음 |
| `KisRestClient.for_service(settings, redis, …)` | 앱키 리미터 + reader 를 끼운 REST 클라이언트(GX `reader_kis_client`:323) | 호출이 실패(`token_rejected` 아님) |
| `kbj.data.legacy_bridge.access_token_or_none()` | legacy 용 — 예외 대신 `None` | `None` |

발급 경로가 없다는 것은 세 겹으로 지킨다: ① 발급자 클래스는 `kbj/services/auth/issuer.py` 에만 있고 import-linter 계약 ④가 auth 밖 import 를 막는다(§9.6) ② `oauth2/tokenP`·`oauth2/Approval` 문자열은 `kbj/services/auth/**` 밖에서 `check_canonical` 이 실패시킨다 ③ 런타임 가드 — `Settings.service`(신규, `KBJ_SERVICE`)가 `auth` 가 아니면 `KisTokenIssuer.__init__` 이 `RuntimeError` **[제안]**.

### 3.6 앱키 주입 범위 — [결정 D1] 안 A 확정 (ADR 0004)

KIS REST 는 **모든 요청 헤더에 `appkey`·`appsecret`** 이 들어간다(GX:`data/kis/rest.py:_send` 헤더, ET:`board/ingest/kis.py:_headers`:146, SD:`kis_api.py:_headers`:82). 그래서 "`KBJ_KIS_APP_KEY` 는 services.auth 만 읽는다"(`docs/secrets.md` 표, conflict_map §1.1 이행 1번 "auth 컨테이너에만 주입")는 그대로 지킬 수 없다. GX 도 compose 의 모든 앱 서비스에 `.env` 를 통째로 넣는다(GX:`docker-compose.yml:24` `env_file`).

| 안 | 내용 | 장단점 |
|---|---|---|
| **A(채택 — 결정 D1)** | 앱키·시크릿을 KIS REST 를 부르는 프로세스(auth, scheduler 실행기·collectors, legacy GX poller·ws-gateway)에 주입. **발급** 차단은 §3.5 세 겹으로 | 지금 구조 그대로. secrets.md "읽는 곳" 칸은 "발급: services.auth 만 / 요청 헤더: kbj.data.private.kis" 로 고쳤다(secrets.md §1·§4). compose 는 P2 에 KIS 를 부르는 서비스가 auth 하나라 앱키를 auth 에만 넣었다 — KIS 수집 작업이 켜지는 P3 에 scheduler 에도 더한다 |
| B | auth 가 KIS REST 프록시까지 맡아(내부 HTTP) 앱키를 auth 에만 둔다 | 지연·장애 단일점, 레이트리미터·재시도·EGW00201 처리를 auth 로 모아야 한다 — P7 이후 재검토 |
| C | 앱키 2개(발급용·조회용) | 토큰은 앱키별이라 조회용 앱키 토큰을 따로 발급해야 한다 — "발급 1곳" 은 지키지만 앱키 공유(U1)와 어긋난다 |

### 3.7 legacy 연결 — 논리 URL 브리지 (`kbj/data/legacy_bridge.py`)

legacy 는 "주소 상수 + `requests`/세션"으로 부른다. 주소 상수를 **논리 URL** 로 바꾸고 `requests`·세션 자리에 브리지를 끼우면 나머지 코드(파라미터 채우기·파서·재시도)는 그대로다.

| 스킴 | 예 | 브리지가 하는 일 | 넣는 비밀(호출자가 넘긴 값은 지운다) | 한도 |
|---|---|---|---|---|
| `kis:` | `kis:/uapi/domestic-stock/v1/quotations/inquire-investor` | `KisRestClient.get(path, tr_id=headers["tr_id"], params, tr_cont)` | `authorization`(reader 토큰)·`appkey`·`appsecret` | `rl:kis` — 기본 P3, 헤더 `x-kbj-priority` 로 바꿈 |
| `kis-master:` | `kis-master:fo_idx_code_mts.mst.zip` | `master.download_fo_master()` | — | — |
| `krx:` | `krx:/sto/stk_bydd_trd` | `KrxClient.daily(endpoint, basDd)` | `AUTH_KEY` | `krx:calls:<날짜>` 예산 |
| `dart:` | `dart:/list.json` | `DartClient.get_raw/get_json` | `crtfc_key` | DART 리미터·예산 |
| `http(s)://…` | — | **`ValueError`** — 남은 직접 호출이 조용히 새지 않게 | — | — |

- 응답은 `requests.Response` 모양(`status_code`·`text`·`content`·`headers`·`json()`·`raise_for_status()`). ET `http.get`(ET:`board/ingest/http.py:get`:111)이 그대로 돈다.
- 토큰이 없으면 HTTP 503 + `{"rt_cd": "1", "msg_cd": "KBJ_TOKEN_UNAVAILABLE", "msg1": "토큰 없음 — auth 대기"}` — legacy 의 기존 오류 경로가 사유를 문자열로 남긴다.
- legacy 는 옛 환경변수 이름(`DART_API_KEY`·`KRX_API_KEY`)을 읽는다(secrets.md §2 끝). KBJ 환경에는 그 이름이 없으니 빈 값이 오고, 브리지는 호출자 값을 지우고 KBJ 키를 넣는다.

### 3.8 발급 지점 K1~K10 교체 (conflict_map §1.1)

| # | 지점(원본 줄) | 교체 방법(legacy 파일·함수) | 확인 |
|---|---|---|---|
| K1 | SD:`kis_api.py:_get_token`:50(`POST …/oauth2/tokenP`:64), 캐시 `_TOKEN_FILE`:28·`_load_token_from_disk`:30·`_save_token_to_disk`:42 | `legacy/stock_dashboard/kis_api.py`: `import requests`(:16) → `from kbj.data import legacy_bridge as requests`; `KIS_BASE`(:20) → `"kis:"`; `_get_token` 본문 → `return requests.access_token_or_none()`; :24~48 토큰 캐시 코드 삭제; `_rate_limit`:100 과 호출 5곳(:164·:219·:264·:304·:423) 삭제(리미터는 브리지); `FO_MASTER_URL`:361 → `"kis-master:fo_idx_code_mts.mst.zip"`. `_headers`:82 는 토큰이 없으면 `None`(호출자가 빈 결과로 끝내는 기존 동작 유지) | `grep oauth2/tokenP` 0, SD 검사 스크립트 9/10 그대로 |
| K2 | ET:`board/tools/dashboard_brief_preview.py:build`(SD `server.py` 함수를 `exec`) | **파일 삭제**(conflict_map §1.1 이행 4번). 워크플로 사본 `dashboard-brief-preview.yml` 은 P1 에서 옮기지 않았다(board MIGRATION — `test_workflow_modes` 영향 없음, 확인됨) | ET 1,319 녹색 |
| K3 | ET:`board/ingest/kis.py:token`:103(`POST`:112), `_cached_token`:92, `_save_token`:124, `TOKEN_CACHE`:44, `REAL`·`VTS`:42~43, `base`:87, `_headers`:146, `call`:167 | `legacy/etf_traker/board/ingest/kis.py`: `REAL`·`VTS`·`TOKEN_CACHE` 삭제; `base()` → `"kis:"`; `token(force=False)` → `force` 면 `kbj…token.reader(...).invalidate()` 후 읽기, 아니면 `access_token()`(발급 없음); `_cached_token`·`_save_token` 삭제; `_headers(tr_id)` → `{"tr_id": tr_id, "custtype": "P", "content-type": "application/json", "x-kbj-priority": "P3"}`(토큰·앱키 없음); `call` 의 `session()` → `kbj.data.legacy_bridge.session()`. `probe()`:546 의 `creds.mask(t)`(앞 4자 노출 — ET:`creds.py:mask`:72)는 `'토큰 있음'` 으로 | ET `test_kis_call`(32)이 `kis._headers`·`kis.get`·`kis.session` 을 바꿔 끼우므로 그대로 통과 **[추정]** |
| K4 | ET:`monitor/kr/flows.py:collect` → K3 | 코드 변경 없음(K3 를 탄다). 회차마다 새로 발급하던 문제(러너 캐시 없음)가 사라진다 | kr 94 녹색 |
| K5 | ET:`monitor/flow/kissrc.py:fetch_daily`:48, `probe`:102(`K.token()`:106) | 변경 없음(K3). `probe` 의 메시지 '발급/캐시 확인' → '토큰 읽기 확인' | flow 129 녹색 |
| K6 | ET:`board/tools/probe_kis_futures.py`(`K._headers`:27, `MASTER`:22) | **파일 삭제**(수동 진단, 선물은 GX 정본). 워크플로 사본 `kis-futures-probe.yml` 은 P1 에서 옮기지 않았다(K2 와 같음 — 확인됨) | — |
| K7 | GX:`data/kis/auth_client.py:default_token_provider`:539 ← `data/kis/rest.py:KisClient._provider`:120 ← `scripts/probe_all.py:55`(`KisClient(settings)`) | 승격 후 `legacy/gexlab/data/kis/{auth_client,rest,ratelimit,master}.py` 는 kbj 를 다시 내보내는 얇은 모듈(파일 캐시·기본 발급 경로 없음). `probe_all.py:55` → `KisRestClient.for_service(settings, redis)`. `scripts/probe_common.py:199`(`MASTER_URL`)·:230, `probe_chain_fill.py:117` → `kbj…master.download_fo_master()` | `KisRestClient()` provider 없이 → `TypeError` |
| K8 | GX:`services/auth/service.py:AuthService`:186 (정본) | `kbj/services/auth/service.py` 로 승격. `legacy/gexlab/services/auth/` 는 `reader`·`TOKEN_KEY`·`WS_KEY_KEY`·health 이름만 kbj 에서 다시 내보낸다(발급 코드 없음) | GX 남은 시험 녹색 |
| K9 | GX:`services/auth/service.py:KisApprovalKeyIssuer`:465 (정본) | `kbj/services/auth/issuer.py` | 같음 |
| K10 | 레포 밖 `kospi-dislocation`(평일 08:55, GX:`docs/phase1_design.md:92`) | **사용자 작업**(ADR 0001 Q13): 전환일에 멈추거나 Redis `kis:token` 을 읽게 바꾼다. 모르고 두면 auth 에 `EGW00133`·`token_throttled` 가 매일 08:55 근처에 찍힌다 — 이것이 감지 신호 | 운영 1주 health 기록 |

GX 설정 상수도 바꾼다: `legacy/gexlab/config/settings.py:11~13`(`KIS_REAL_BASE`·`KIS_VTS_BASE`·`KRX_BASE`) → 문자열을 지우고 `kbj.data.private.kis.credentials`·`kbj.data.private.krx.client` 의 공개 함수로 값을 얻는다(legacy GX 의 `settings.kis_base` 를 쓰는 곳이 남아 있으므로 속성은 둔다). 그 뒤 GX 에서 KIS·KRX 를 직접 부르는 코드는 승격 모듈 안에만 있다.

### 3.9 auth 테스트 요약

옮김 GX 103(§1.12 — auth_client 20·auth_service 52·kis_rest 11·kis_master 17·내려받기 3) + 새로: `test_reader_never_issues`, `test_invalidate_guard`, `test_auth_guard`(비-auth 프로세스에서 발급자 생성 금지), `test_owner_compat`(GX `token_owner` 와 같은 값 — legacy GX 가 KBJ 토큰을 읽는다), 시뮬레이션(§10).

---

## 4. 레이트리미터·일 예산

### 4.1 구성

| 부품 | 원본 | 규칙 |
|---|---|---|
| `RedisRateLimiter`(GCRA, Lua 원자 갱신) | GX:`data/kis/ratelimit.py:332` | 버킷 1 → 허가 간격 ≥ 1/rate. 우선순위 P0~P4(높은 등급이 기다리면 낮은 등급은 못 받음), TR 별 최소 간격, 한도초과 신호 → 반감(하한)·유지·단계 회복. 시각은 호출자가 넘긴다(가짜 시계 시험) |
| `LocalRateLimiter` | GX:`ratelimit.py:421` | 같은 규칙, 프로세스 안(시험·Redis 없는 legacy 시험) |
| `DailyBudget` | GX:`services/scheduler/krx.py:KrxCallBudget`:87 | KST 날짜별 Redis 카운터(`INCR`+`EXPIRE` 3일), Redis 장애 시 프로세스 셈과 큰 값. 상한이면 부르지 않는다(세지 않음). 실패한 호출도 센다 |
| 설정 | `config/limits.yaml`(비밀 아님 — inventory (f) 원칙) | 출처·데이터셋별 rate·capacity·우선순위 기본값·일 상한 |

### 4.2 키별 규칙

| 출처(키) | 초당 리미터 | 일 예산 | 오류 → 동작 | 근거 |
|---|---|---|---|---|
| **KIS 앱키** `rl:kis:<해시>` | **4.0/s**, 버킷 1, P0~P4, `FHPIF05030100` 1초 간격 | 없음(KIS 일 한도 미공표) | `EGW00201` → 반감(하한 1.0)·60초 유지·60초마다 +0.5 | GX `ratelimit.py` 머리말, 실측 무오류 5/s 의 80%(GX `docs/probe_results.md` #10) |
| KIS 우선순위 배정 [제안] | P0 발급·WS 재연결 스냅샷, P1 GX 전광판, P2 GX 투자자·명령 응답(`/가격`), **P3 장마감 수집(`market.close_collect`)·legacy 브리지 기본**, P4 백필·분봉 | — | P3·P4 는 `timeout` 짧게 → 다음 주기·다음 실행 | conflict_map §1.2 [제안] |
| KIS 발급 `kis:token:issue_blocked_until` | 61초 1회 | — | `EGW00133` → 한 간격 대기 | §3.3 |
| **KRX** `rl:krx:<해시>` + `krx:calls:<YYYYMMDD>` | 2/s [추정 — 공표 없음] | **8,000/일**(KRX 10,000/일의 80%, 백필은 그 안에서 최대 5,000) **[확인 필요]** — GX 기본 200(GX:`config/settings.py:27`)은 파생 2건 기준이라 주식·지수·ETP(하루 10건 × 재시도)에 모자란다 | 401 "Unauthorized API Call" = 엔드포인트 미구독(SD:`krx_api.py` 머리말 :20~23) → 그 엔드포인트만 끄고 health | GX `data/krx/eod.py` 머리말(#16 10,000회/일) |
| **DART** `rl:dart:<해시>` + `budget:dart:<날짜>` | 8/s(dart-report 0.12초 스로틀 — ET:`dart-report/dartreport/client.py:46`) | **18,000/일**(20,000의 90%) | `020` 요청 제한 초과(ET:`board/ingest/dart.py:43`) → 예산 닫기, `013` → 빈 결과 | dart-report `client.py:4`, README:24 |
| **data.go.kr** `rl:datago:<데이터셋 ID>` + `budget:datago:<ID>:<날짜>` | **25/s**(문서 30 tps, 데이터셋별) | **9,500/일 데이터셋별**(개발계정 10,000) | GW `22` → 그 데이터셋 그날 닫기, `23` → 감속, `30`·`31` → critical(키), `05` → 재시도 | `probe_results.md` §1 |
| **KOSIS** `rl:kosis:<해시>` | **3.0/s**(= 180/분 < 200/분) | 미공표 **[확인 필요]** | HTTP 오류·한도 메시지 → 감속 | `probe_results.md` F8·§5.3 |
| **ECOS** `rl:ecos:<해시>` | 2/s [추정 — 한도 미공표] | 없음 | **`602` → 반감·10분 유지, 같은 날 3회면 그날 닫기** [제안], `400`(60초 타임아웃) → 기간 나눠 다시, `INFO-200` → 빈 결과(오류 아님) | `probe_results.md` §4.1 |
| **Telegram**(notifier) `rl:telegram:<봇 해시>` | 전체 25/s, 대화당 1/s(그룹 20/분) | — | 429 `retry_after` 따라 최대 4회(bok `send-telegram.js:34`) | Telegram Bot FAQ [추정 — 레포 근거 없음] |

### 4.3 `config/limits.yaml` 예

```yaml
version: 1
kis:      {rate: 4.0, capacity: 1, floor_rate: 1.0, hold_s: 60, step_rate: 0.5, step_s: 60,
           tr_min_interval_s: {FHPIF05030100: 1.0}}
krx:      {rate: 2.0, daily_cap: 8000, backfill_cap: 5000}          # [확인 필요]
dart:     {rate: 8.0, daily_cap: 18000}
datago:   {rate: 25.0, daily_cap_per_dataset: 9500}
kosis:    {rate: 3.0}
ecos:     {rate: 2.0, throttle_hold_s: 600, close_after_throttles: 3}
telegram: {rate: 25.0, per_chat_rate: 1.0, retry_after_max: 4}
```

### 4.4 시험

- GX 시험 27개를 그대로 옮긴다(fakeredis[lua] — 이미 dev 의존성).
- 속성 시험을 출처별 설정마다 돌린다: 가짜 시계에서 무작위 요청 열 → 어떤 반열린 1초 창에도 `ceil(rate)` 이하, 일 예산 초과 0.
- 두 리미터 인스턴스(서로 다른 "프로세스")가 같은 Redis 키를 나눠 쓰는 시험 — KBJ 수집기와 legacy 브리지가 같은 버킷을 쓰는지.

---

## 5. notifier — 발송·수신 한 곳

### 5.1 구성

```
 kbj 작업·legacy shim ── notify()/legacy_send() ──▶ Redis  notify:dedup:<키> (SET NX)
                                                          notify:outbox (Stream)
                                                              │
                         kbj/services/notifier (한 컨테이너)  ▼
                         NotifierService ── 정책(토픽·하루 1회·쿨다운) ── KBJ_NOTIFY_ENABLED?
                               │ 켜짐: TelegramApi(유일한 api.telegram.org) → 슈퍼그룹 토픽
                               │ 꺼짐: 보내지 않고 status=suppressed
                               ▼
                         ops.notify_log(메타) + prv_alerts.notify_message(본문)

 Telegram ── POST /telegram/webhook (secret_token 검증) ──▶ webhook.handle_update
                         ├─ '/명령' + 허용 대화 → CommandRegistry → notify(kind=cmd.reply, 받은 대화·스레드)
                         └─ 허용된 인박스 대화의 링크·전달 → inbox.parse_update → prv_alerts.tg_inbox
```

봇 토큰(`KBJ_TELEGRAM_BOT_TOKEN`)은 notifier 컨테이너에만 넣는다 — 발송·수신이 모두 이 프로세스라 가능하다(secrets.md 그대로).

### 5.2 토픽·메시지 종류 (`config/notify.yaml`)

| 종류 | 토픽 | 규칙 | 보내는 작업 | 흡수하는 legacy(inventory d-1 #) |
|---|---|---|---|---|
| `brief.morning` | 시장 | **하루 1회**(U2) | `brief.morning` 08:10 | #3·#4·#5·#15(오전)·#29·#30·#38, GX 계획 06:05·08:30 |
| `brief.closing` | 시장 | **하루 1회**(U2), 캐치업 20:30 까지 | `brief.closing` 16:40 | #8·#9·#15(오후)·#23·#24·#25(엑셀 첨부)·#26, GX 계획 15:50 |
| `flows.report` | 시장 | 하루 1회 | `flows.report` 18:20 | #11·#39·#40 |
| `etf.report` | 시장 | 하루 1회 | `etf.collect` 08:00 | #34 |
| `board.note` | 신고가 | 수동 | (명령·운영 화면) | #27 |
| `board.manual` | 운영 | 수동 | 백테스트·탐색 요약 | #28 |
| `alert.rule` | 알림 | 규칙·종목마다 쿨다운 60분 | `rules.intraday`(P8) | #6·#12·#13·#14 |
| `alert.earnings` | 알림 | 접수번호마다 1회 | `filings.dart_feed`(P4) | #16 |
| `alert.revision` | 알림 | 하루 1회 | `consensus.snapshot`(P4) | #10 |
| `alert.gex_level` | 알림 | 쿨다운 10분(GX 설계) | GEX 엔진(P7) | #42 계획 |
| `ops.watchdog` | 운영 | 같은 공백 30분 쿨다운 | `ops.watchdog` | #18, GX health |
| `ops.job_failed` | 운영 | 작업·as_of 마다 1회 | 실행기 | #37 |
| `ops.universe` | 운영 | 하루 1회 | `krx.daily` | #17 |
| `cmd.reply` | (받은 대화·스레드) | 없음 | 명령 처리기 | #21 |

토픽 id(`message_thread_id`)는 슈퍼그룹을 만든 뒤 `config/notify.yaml` 에 적는다 **[확인 필요]**. chat id 는 비밀 취급(`KBJ_TELEGRAM_CHAT_ID`).

**전환 기간 legacy 규칙(ADR 0005 §2.2 — 구현)**: 위 '규칙' 칸은 kbj 작업(origin 없이 부른다) 기준이다 — 종류당 하루 1회(U2)가 그대로 선다. legacy 발송은 ① kind 를 넘기지 않아 `legacy_kinds`(§5.9)로 종류를 찾은 함수는 **부른 함수(origin)마다 하루 1회** — SD 아침 함수 셋(#3·#4·#5)이 모두 `brief.morning` 이어도 함수마다 따로 센다(본문 합치기는 마감 P3·아침 P5. 그전에 legacy 내용을 조용히 버리지 않는다) ② kind 를 넘긴 legacy 호출(ET ETF 리포트 여러 통 `etf.report`·수급 종목별 차트 `flows.report`)은 한 번에 여러 통을 보내므로 origin 에 내용 해시를 붙여 **내용마다** 센다(같은 내용의 재실행만 막는다 — 안 그러면 둘째 통부터 `duplicate` 인데 legacy 는 보낸 줄 안다).

### 5.3 중복 방지 키·쿨다운

| 정책 | 문지기 키(Redis `notify:dedup:<키>`) | 발송 기록 키(`ops.notify_log.dedup_key`) | 수명 |
|---|---|---|---|
| 하루 1회 | `{kind}:{as_of:%Y%m%d}`(+`:{subject}`)(+`:{legacy origin}`) | 문지기 키와 같다 | 36시간 |
| 쿨다운 | `{kind}:{subject 또는 본문 해시}` — **미끄러지는 창**(마지막 발송부터 `cooldown_s`) | `{kind}:{subject}:{floor(now / cooldown_s)}` | `cooldown_s` |
| 대상별 1회 | `{kind}:{subject 또는 본문 해시}` | 문지기 키와 같다 | 30일 |
| 그 밖 | `{kind}:{as_of}:{sha256(본문)[:16]}` | 문지기 키와 같다 | 24시간 |
| 강제 재발송(운영 화면, P3 — SD `?force=1`(#43) 대체) | 위 키 + `:force:<n>` + 감사 기록 | — |

1차는 Redis `SET NX`(enqueue 순간 — 호출자가 즉시 `duplicate` 를 안다), 2차는 `ops.notify_log.dedup_key` UNIQUE. **구현 메모(ADR 0005 §2.2)**: `floor` 붙은 키를 문지기로 쓰면 창 경계(10:59·11:01)에서 2분 만에 두 번 나간다 — 그래서 문지기는 미끄러지는 창, `floor` 키는 기록 키로만 쓴다(받아들인 두 발송은 늘 `cooldown_s` 이상 떨어져 기록 키가 겹치지 않는다). 문지기 만료는 주입한 시계로 판정한다(가짜 시계 시험·시뮬레이션에서도 풀린다). subject 가 없으면 본문 해시가 대상이다. 거절(`duplicate`·`cooldown`) 기록은 `<원래 키>#<상태>#<대기열 항목 id>` 로 한 줄씩 남긴다(`dedup_key` 가 `NOT NULL UNIQUE`). SD `ops_state` 하루 1회 표식, ET `docs/api/sent.json`·`guru-sent.json`·`xdigest-sent.json`, bok `telegram-sent.json`, Actions `etf-sent-*`, SD `_alert_cooldown_ok`:6102 은 이 한 곳으로 대체되고 메시지 이전 단계(P3~P5)에서 삭제한다.

### 5.4 발송 기록

| 표 | 열 | 이유 |
|---|---|---|
| `ops.notify_log` | `id`, `kind`, `topic`, `as_of date`, `subject`, `dedup_key UNIQUE`, `body_sha256`, `body_chars`, `parts`, `status`(`queued`·`sent`·`failed`·`suppressed`·`duplicate`·`cooldown`), `reason`, `message_ids bigint[]`, `source`(legacy 호출 지점 이름), `requested_at`, `sent_at` | 운영 메타(본문 없음) — 운영 화면(13)·워치독이 읽는다. inventory (d-2) 의 `ops.notify_log` 와 같다 |
| `prv_alerts.notify_message` | `dedup_key PK`(→ `ops.notify_log`), `body text`, `parse_mode`, `created_at` · 30일 보존 [제안] | 본문에는 시세·포트폴리오가 섞인다(로그인 등급) — ADR 0002 "가장 높은 등급" |

### 5.5 발송 금지 모드 (`KBJ_NOTIFY_ENABLED=false` 기본)

- notifier 는 outbox 를 그대로 소비하고 정책·중복 판정까지 다 하지만 텔레그램을 부르지 않는다 → `status=suppressed`, `reason="KBJ_NOTIFY_ENABLED=false"`.
- shim 반환: `legacy_send` 는 `(False, "발송 꺼짐(KBJ_NOTIFY_ENABLED=false) — 기록만")` — SD `send_telegram` 의 `TELEGRAM_ENABLED` 꺼짐 반환(False)과 같은 뜻을 유지한다. kbj `notify()` 는 `NotifyTicket(ok=True, reason="suppressed")`.
- 명령 응답(`cmd.reply`)도 같은 스위치를 따른다 [제안 — 운영 VM 에서만 켠다].
- 시뮬레이션은 꺼짐을 기본으로 돌고(가짜 텔레그램 요청 0), 켬 변형을 따로 돈다(§10.5).

### 5.6 웹훅 수신

| 단계 | 처리 | 근거 |
|---|---|---|
| 경로 | `POST /telegram/webhook` — **[결정 D4]** HTTP 서버는 P3 API 가 열고(VM 역방향 프록시가 `KBJ_PUBLIC_BASE_URL` 로 노출 **[확인 필요]**) P2 의 순수 처리 함수를 부른다. 받을 서버가 없는 P2 에는 `setup-webhook` 이 `--confirm` 없이 걸지 않는다 | SD:`server.py:5860` |
| 인증 | 헤더 `X-Telegram-Bot-Api-Secret-Token` 을 `KBJ_TELEGRAM_WEBHOOK_SECRET` 과 `hmac.compare_digest` 로 비교, 다르면 403 | SD `_telegram_secret`:5731(봇 토큰 해시 파생·`!=` 비교)을 독립 비밀·상수 시간 비교로 |
| 응답 | 검증 뒤 **바로 200** — 처리는 `notify:inbound` 스트림으로 넘긴다(텔레그램은 실패 응답이면 재전송한다) | — |
| 중복 | `update_id` 를 `SET NX tg:update:<id>`(48시간) | ET `merge`:410 의 update_id 중복 제거와 같은 뜻 |
| 갈래 ① 명령 | 텍스트가 `/` 로 시작 + 대화가 허용 목록(`KBJ_TELEGRAM_CHAT_ID` + `KBJ_TELEGRAM_INBOX_CHAT_IDS`) → `CommandRegistry.dispatch` | SD `api_telegram_webhook`(소유자 chat 만) |
| 갈래 ② 인박스 | 명령이 아니고 인박스 허용 대화(숫자 id 또는 `@username` — ET `_chat_allowed`:117) → `parse_update` → `prv_alerts.tg_inbox` | ET `tg_inbox.py` 계약(머리말 :1~40) |
| 그 밖 | 버리고 건수만(chat id 는 가려서) 센다 | ET 머리말 "화이트리스트 밖" |
| 허용 업데이트 | `message`, `edited_message`, `channel_post` | SD `_telegram_setup_webhook` 의 setWebhook 본문 :5891~5894, ET `ALLOWED`:53 |
| 등록 | `python -m kbj.services.notifier setup-webhook`(수동, `drop_pending_updates=false`) | inventory (d-2) 이행 ③ |
| 점검 | 10분마다 `getWebhookInfo` → Redis `notify:webhook_info`·health(`webhook_missing`·`pending_update_count` 급증) | ET `triggers.py:812`(#46) 대체 |

### 5.7 명령

| 명령(별칭) | 하는 일 | P2 | 연결 단계 |
|---|---|---|---|
| `/도움`(help·start·명령) | 명령 목록 | **동작** | P2 |
| `/시황`(summary) | 오늘 `brief.closing` 본문(`prv_alerts.notify_message`) — 없으면 '아직 없음' | '준비 중(P3)' | P3 |
| `/수급 <종목>`(flow) | `prv_flows.stock_investor_daily` 최근 5일 + 20일 누적 | '준비 중(P3)' | P3 |
| `/가격 <종목>`(시세·price) | KIS 단건 현재가(P2 우선순위, `KIS:quote_on_demand` — 선점 대상 아님) | '준비 중(P3)' | P3 |
| `/신고가`(newhigh) | 오늘 보드 52주·역사적 신고가 요약 | '준비 중(P3)' | P3 |
| `/gex` | 최신 GEX 레벨(Flip·콜월·풋월·기대변동폭) | '준비 중(P7)' | P7 |
| `/시그널`(수급시그널·signals) | 쌍끌이·연속·반전 | '준비 중(P8)' | P8 |

종목명 → 코드 해석은 SD `_resolve_kr_code`(네이버 유니버스)가 아니라 `prv_market.universe`(KRX 종목기본정보)로 한다(U4).

### 5.8 인박스 (ET `tg_inbox` 대체)

- `prv_alerts.tg_inbox(update_id PK, chat_id, date timestamptz, text, urls text[], x_ids text[], author, kind('x'·'other'), text_via, received_at)` — ET `state/inbox.json` 항목 모양 그대로.
- oEmbed 채우기(ET `fill_oembed`:333, `publish.twitter.com/oembed`)는 inbound 작업 스레드에서, 실패하면 URL 그대로 두고 `text_via=null`(지어내지 않음).
- 보존 14일(ET `KEEP_DAYS`:54) — 매일 `ops.nightly` 가 지운다.
- legacy ET `ingest/triggers.py` 는 `state/inbox.json` 대신 `kbj.services.notifier.inbox.read_items(chat_ids, since)` 를 읽는다(같은 dict 계약).
- getUpdates·offset 파일 코드(ET `drain`:436, `_call`:131, `webhook_url`:153, `read_offset`·`write_offset`)는 지운다.

### 5.9 공용 shim — legacy 발송 7벌을 한 함수로

| legacy 발송기(원본 줄) | 바꾼 본문 | 반환 |
|---|---|---|
| SD `server.py:send_telegram(message, parse_mode="HTML") -> bool`:5205 | `return legacy_send(message, source="sd.send_telegram", parse_mode=parse_mode)[0]` — `TELEGRAM_ENABLED`·토큰 읽기 삭제 | bool 그대로 |
| SD `server.py:send_telegram_long`:5273 | `return legacy_send(message, source="sd.send_telegram_long", parse_mode=parse_mode, numbered=True)[0]` — 분할·`(i/n)` 머리는 notifier `format` 이 | bool |
| SD `earnings_telegram_sender.py:send_telegram_message`:51 | `legacy_send(text, source="sd.earnings", parse_mode=…, kind="alert.earnings")` | 원래 반환 모양으로 감싼다 |
| ET `board/report/telegram.py:send(text, token=None, chat_id=None, silent=False, parse_mode='Markdown')`:779 | `return legacy_send(text, source="et.board", parse_mode=parse_mode, kind=kind)` — **`kind=None` 인자 추가**(호출자 `cmd_send` 가 `board.<what>` 를 넘긴다 [제안]). `token`·`chat_id` 인자는 받되 무시(경고 로그) | `(ok, 사유)` 그대로 |
| ET `board/report/telegram.py:send_document`:822 | `legacy_send_document(path, caption, source="et.board.files")` | `(ok, 사유)` |
| ET `board/report/telegram.py:check`:857 | `kbj.services.notifier.client.webhook_status()` 와 notifier 하트비트로 (getMe·getChat 은 notifier 가 한다) | `(ok, 사유)` |
| ET `etf_tracker_v9/tracker.py:send_telegram(text, parse='HTML')`:395 | `legacy_send(text, source="et.etf", parse_mode=parse, kind="etf.report")` — `try` 없이 토큰 URL 이 예외에 실리던 문제 해소 | 원래대로 |
| ET `monitor/flow/telegram.py:send_photos`:36 | `legacy_send_media(paths, caption, source="et.flow", kind="flows.report")` | `(ok, 사유)` |

`kind` 를 넘기지 않은 legacy 호출(SD 발송 함수 대부분, ET `cmd_us_send`·flow `send_text`)은 `config/notify.yaml` 의 `legacy_kinds:`(부른 함수 이름 → 종류)로 정한다 — shim 이 호출 스택을 거슬러 올라가며 처음 만나는 이름을 쓰고, 없으면 `legacy.other`(운영 토픽, 본문 해시 규칙). **세는 단위(ADR 0005 §2.2)**: `legacy_kinds` 로 찾은 함수는 **함수마다 하루 1회**(그 함수는 한 번 돌 때 한 통을 보낸다), kind 를 넘긴 legacy 호출은 **내용마다**(재실행만 막는다) — §5.2 끝 '전환 기간 legacy 규칙'. 전환 기간 한정이고, 메시지를 kbj 작업으로 옮길 때(P3~P5) 그 함수와 함께 지운다.

### 5.10 발송·수신 지점 46곳 교체 (inventory d-1)

| # | 지점 | P2 에서 할 일 | 종류·토픽 | 메시지 자체를 kbj 로 옮기는 단계 |
|---|---|---|---|---|
| 1 | SD `send_telegram`:5205 | shim(§5.9) | legacy_kinds | — |
| 2 | SD `send_telegram_long`:5273 | shim | legacy_kinds | — |
| 3 | SD `alert_morning_briefing`:6064 | 없음(#1 경유) | brief.morning·시장 | P5 |
| 4 | SD `alert_overnight_prediction`:6310 | 없음 | brief.morning | P5 |
| 5 | SD `send_us_market_summary_telegram`:14554 | 없음(#2) | brief.morning | P5 |
| 6 | SD `alert_watchlist_price`:6248 | 없음 | alert.rule·알림 | P8 |
| 7 | SD `alert_new_reports`:6282 | 없음(실행 안 함 — 등록부에 없음) | — | 폐지(네이버, 대체 없음) — 코드 삭제 P3 |
| 8 | SD `alert_closing_summary`:6490 | 없음 | brief.closing | P3 |
| 9 | SD `send_closing_market_summary`:14696 | 없음(#2). `ops_state` 하루 1회 표식은 그대로 두되 notifier 중복 키가 우선 | brief.closing | P3 |
| 10 | SD `alert_revision_signals`:5948 | 없음 | alert.revision | P4 |
| 11 | SD `alert_flow_signals`:15196 | 없음 | flows.report | P3/P5 |
| 12 | SD `check_alert_rules`:5579 | 없음 | alert.rule | P8 |
| 13 | SD `_check_trailing_stops`:5438 | 없음 | alert.rule | P8 |
| 14 | SD `alert_discovery_new_entries`:6143 | 없음(`_alert_cooldown_ok`:6102 → notifier 쿨다운) | alert.rule | P8 |
| 15 | SD `agents/pipeline.send_agent_telegram`:989 | 없음 | — | 폐지 → 마감 요약 ⑨(P3) |
| 16 | SD `earnings_telegram_sender.send_telegram_message`:51 / `send_pending_alerts`:201 | shim | alert.earnings | P4 |
| 17 | SD `_scheduled_universe_sync` 안 | 없음 | ops.universe·운영 | P3 |
| 18 | SD `_watchdog_notify`:14989 | 없음(SD 워치독은 돌지 않는다 — `ops.watchdog` 이 대신) | ops.watchdog | P2(kbj 쪽) |
| 19 | SD 시험 엔드포인트 `:4644`·`:5708`·`:5718` | **라우트 삭제**(인증 없는 발송) | — | — |
| 20 | SD `_telegram_setup_webhook`:5882, `POST /api/telegram/setup_webhook`:5904, 부팅 호출 | **삭제** — setWebhook 은 notifier 만 | — | — |
| 21 | SD `_handle_telegram_command`:5823 ← `/api/telegram/webhook`:5860, `_tg_reply`:5766 | **라우트 삭제**, 명령 로직은 notifier `commands`(§5.7) | cmd.reply | P3 |
| 22 | ET `report/telegram.py:send`:779·`send_document`:822·`check`:857 | shim(§5.9), `_split`:237 → `kbj…format.split_text` 다시 내보내기 | 인자 `kind` | — |
| 23 | ET `cmd_send('rankings')` | `kind="board.rankings"` 넘김 | brief.closing ③ | P3 |
| 24 | ET `cmd_send('draft')` | `kind="board.draft"` | brief.closing ⑧ | P3 |
| 25 | ET `run.py:_send_files`:1083 | `legacy_send_document`. `docs/api/sent.json` 은 P3 에서 삭제 | brief.closing 첨부 | P3 |
| 26 | ET `cmd_send('signals')` | `kind="board.signals"` | brief.closing ⑦ | P3 |
| 27 | ET `run.py:_send_note`:1000 | `kind="board.note"` | board.note·신고가 | P3 |
| 28 | ET `cmd_send('backtest'/'screen'/'search')`:930~961 | `kind="board.manual"` | 운영 | P8 |
| 29 | ET `run.py:cmd_us_send`:1864 | `kind="board.us"` | brief.morning ① | P5 |
| 30 | ET `guru/pipeline.send`:143(:153) | 없음(#22 경유) | brief.morning ⑥ | P5 |
| 31 | ET `xdigest/send.send_parts`:159(:168) | 없음 | — | 폐지 [제안] |
| 32 | ET `tools/dashboard_brief_preview.main` | **파일 삭제**(K2) | — | — |
| 33 | ET `ingest/tg_inbox.drain`:436(`_call`:131, `webhook_url`:153) | `read_items` 로 교체(§5.8), getUpdates 삭제 | — | P2 |
| 34 | ET `etf_tracker_v9/tracker.py:send_report`:413 → `send_telegram`:395 | shim | etf.report | P5 |
| 35 | ET `tracker.py:send_telegram_file`:425 | **삭제**(호출 없음) | — | — |
| 36 | ET `tracker.py:doctor`:513 | getMe·sendMessage(:541·:544) → `webhook_status()`·notifier 하트비트 | — | P2 |
| 37 | ET `daily.yml` 실패 단계 curl(:295, legacy 사본) | 실행되지 않는 사본(ADR 0003-6) — **기준선**, 대체는 `ops.job_failed` | ops.job_failed | P5 에 사본 삭제 |
| 38 | ET `monitor/bok/send-telegram.js:main`:44 | 해당 없음 — bok 은 이식하지 않음(U3) | brief.morning ②③⑤ | P5 |
| 39 | ET `monitor/flow/telegram.py:send_photos`:36 | shim(media) | flows.report | P3/P5 |
| 40 | ET `monitor/flow/telegram.py:send_text`:103 | 없음(#22 경유) | flows.report | — |
| 41 | ET `preview.yml` curl(:43, 사본) | 기준선 | — | P5 에 사본 삭제 |
| 42 | GX `config/settings.py:29~30`(필드만) | 필드 삭제(H) | — | — |
| 43 | SD `api_ops_brief_closing`:8493 | **라우트 삭제** — 재발송은 P3 운영 화면(인증·강제 키) | — | — |
| 44 | SD `api_agent_run`:12489 | **라우트 삭제** | — | — |
| 45 | SD `api_ops_cron_trigger`:18370 | **라우트 삭제** — 수동 실행은 `python -m kbj.services.scheduler run-once <job> --as-of …`(`ops.job_run.source='manual'`) | — | — |
| 46 | ET `ingest/triggers.py:812` getWebhookInfo | `webhook_status()` 읽기 | — | P2 |

SD 라우트 삭제가 SD 검사 스크립트(AST 로 함수를 꺼냄)에 걸리지 않는지 확인한다 **[확인 필요]**.

### 5.11 수신 전환 순서 (inventory d-2 를 P2 에 맞춤)

1. notifier 배포(VM), 발송 꺼짐 상태로 웹훅 경로만 연다.
2. **SD Render 의 부팅 setWebhook 을 끈다**(옛 서비스 중지 또는 `RENDER_EXTERNAL_URL` 제거 — 사용자 작업). 안 끄면 SD 가 재시작할 때마다 웹훅을 자기 주소로 되돌린다.
3. `setup-webhook` 실행 → `webhook-info` 로 URL·대기 건수 확인.
4. legacy ET `tg_inbox.drain` 은 이미 `read_items` 로 바뀌어 있다(H) — getUpdates 호출 코드 없음.
5. `check_canonical` 의 `telegram` 기준선이 사본 워크플로 3줄만 남는지 확인.

### 5.12 notifier 시험

옮김 41(§1.6) + 새로: 정책(하루 1회·쿨다운·대상별), 꺼짐 모드, 웹훅(시크릿·허용 대화·update_id·갈래), 명령 분배, 인박스 저장·`read_items` 계약, outbox 장애(Redis 없음 → shim `(False, 사유)`, 조용히 버리지 않음), 46지점 표의 shim 대상 7곳이 전부 `legacy_send*` 를 부르는지(정적 검사).

---

## 6. scheduler + 작업 등록부

### 6.1 구성

| 부품 | 하는 일 | 원본 |
|---|---|---|
| 세션 상태 머신 | 1초마다 `state_at` → 바뀌면 Redis `session:state` + `session.events` 발행, `ops.session_log` 기록, 30초마다 키 재기록 | GX:`services/scheduler/service.py:step`:187·`_transition`:200·`_publish`:225 |
| 실행기 `JobRunner.tick(now)` | 등록부의 작업마다 cron(KST) 일치 + 캘린더 조건 + 의존 충족이면 실행 요청. 작업은 스레드 풀에서 돌고 상태 루프를 막지 않는다(GX `run_in_thread` 방식) | 신규 |
| 선점(claim) | 수집 작업은 데이터 키마다 `ops.data_claim` 을 먼저 잡는다 — 잡지 못하면 받지 않는다 | 신규 |
| 기록 | `ops.job_run(run_id = "<job>:<as_of>:<attempt>")`, 실패·마감 초과 → health + `ops.job_failed` 알림 | SD `run_log`, ET `run_log` 대체 |
| 외부 실행자 | `runner: external` 작업(legacy GX 스케줄러 안 단계)은 KBJ 가 실행하지 않고 **데이터 키만 등록** — P7 에 kbj 작업으로 바뀐다 | D-P2-5 |

### 6.2 `config/jobs.yaml` 형식

```yaml
version: 1
defaults:
  tz: Asia/Seoul               # cron 은 KST. 컨테이너 TZ=UTC(secrets.md) — 변환은 코드에서
  retry: {max: 3, backoff_s: [60, 300, 900]}
  deadline_min: 120            # 시작 시각 + 이 시간 안에 끝나지 않으면 실패
  enabled: false               # P2: kbj 구현이 있는 작업만 true

services:                      # 상시 프로세스(작업이 아님) — 하트비트로만 감시
  - {name: auth,      owner: "kbj.services.auth",      phase: P2, heartbeat_s: 180}
  - {name: notifier,  owner: "kbj.services.notifier",  phase: P2, heartbeat_s: 60}
  - {name: scheduler, owner: "kbj.services.scheduler", phase: P2, heartbeat_s: 60}
  - {name: gex.poller, owner: "legacy:gexlab/services/poller", phase: P7, runner: external}

jobs:
  - name: filings.corp_code
    phase: P2
    enabled: true
    owner: "kbj.services.collectors.corp_code:run"
    schedule: {cron: "5 3 * * *", when: always}
    retry: {max: 3, backoff_s: [600, 600, 600]}
    collects:
      - {source: DART, dataset: corpCode, as_of: run_date}
    writes: [pub_filings.corp_code]
    absorbs: ["SD:server.py:init_dart_corp_map_db", "ET:board/ingest/dart.py:corp_codes", "ET:dart-report .cache/corp*"]

  - name: krx.daily
    phase: P3
    owner: "kbj.services.collectors.krx_daily:run"
    schedule: {cron: "5 8 * * 1-5", when: trading_day}
    retry: {every_s: 600, until: "10:00"}      # KRX 는 D+1 08:00 공표(conflict_map E1)
    collects:
      - {source: KRX, dataset: sto/stk_bydd_trd, as_of: prev_trading_day}
      - {source: KRX, dataset: sto/ksq_bydd_trd, as_of: prev_trading_day}
      # … (§6.7)
    budget: krx
    writes: [prv_market.stock_snapshot, prv_market.daily_bar, prv_market.universe]
    notify: {kind: ops.universe, when: changed}

  - name: brief.closing
    phase: P3
    owner: "kbj.reports.briefs:closing"
    schedule: {cron: "40 16 * * 1-5", when: trading_day, catch_up_until: "20:30"}
    depends_on: [{job: market.close_collect, as_of: same, hard: true},
                 {job: board.daily, as_of: same, hard: false}]
    collects: []
    notify: {kind: brief.closing}

  - name: gex.krx_derivatives
    phase: P7
    runner: external                           # legacy GX KrxDaily(services/scheduler/krx.py:401)
    owner: "legacy:gexlab/services/scheduler/krx.py:KrxDaily"
    schedule: {cron: "5 8 * * 1-5", when: trading_day}
    collects:
      - {source: KRX, dataset: drv/fut_bydd_trd, as_of: prev_trading_day}
      - {source: KRX, dataset: drv/opt_bydd_trd, as_of: prev_trading_day}
    budget: krx
```

### 6.3 필드

| 필드 | 뜻 | 검증 |
|---|---|---|
| `name` | `영역.작업` 소문자 | 유일, `^[a-z]+(\.[a-z0-9_]+)+$` |
| `phase` | 실행을 kbj 로 연결하는 단계 | `P2`~`P9` |
| `enabled` | 이 단계에서 실제로 돈다 | `true` 면 `owner` 가 import 가능해야 한다 |
| `owner` | `모듈:함수` 또는 `legacy:<경로>` | `runner: external` 이면 `legacy:` 만 |
| `runner` | `kbj`(기본)·`external` | — |
| `schedule.cron` | 5필드, `tz` 기준(기본 KST) | `kbj.core.cron` 파싱 |
| `schedule.tz` | `Asia/Seoul`·`America/New_York` | IANA |
| `schedule.when` | 캘린더 조건(§6.4) | 목록 안 |
| `schedule.state_enter` | 세션 상태 진입 트리거(`PRE_DAY`·`PRE_NIGHT`·`POST_DAY`·`IDLE`) — cron 대신 | GX 마스터·분봉 방식 |
| `schedule.catch_up_until` | 놓친 실행을 이 시각까지 따라잡는다 | 하루 1회 종류와 함께 |
| `retry` | `max`·`backoff_s` 또는 `every_s`+`until` | `until` > 시작 |
| `deadline_min` | 마감 | > 0 |
| `depends_on` | `{job, as_of: same \| prev, hard}` | 존재·비순환 |
| `collects` | 데이터 키 규칙 `{source, dataset, as_of}` | **카탈로그에 있어야 하고, 같은 `(source, dataset)` 은 등록부 전체에서 한 작업만** |
| `budget` | 일 예산 이름 | `limits.yaml` 에 있음 |
| `writes` | 쓰는 `스키마.표` | 수집 등급과 스키마 접두사 일치(공개 출처만 → `pub_*` 가능) |
| `notify` | 보내는 메시지 종류 | `notify.yaml` 에 있음 |
| `absorbs` | 흡수하는 legacy 작업(근거) | inventory (c) 의 모든 정기 작업이 어느 `absorbs` 나 `retired` 에 한 번 나온다 |
| `retired:`(최상위) | 폐지하는 legacy 작업과 사유 | 같음 |

### 6.4 캘린더 조건·as_of 규칙

| `when` | 참일 때 | 근거 |
|---|---|---|
| `always` | 매일 | — |
| `weekday` | 월~금 | — |
| `trading_day` | `TradingCalendar.default().is_trading_day(KST 날짜)` | GX `core.calendar` |
| `us_trading_day` | XNYS 거래일(그 지역 날짜) | §7.1 |
| `after_us_session` | KST 날짜의 간밤에 끝난 미국 정규장이 있다(XNYS 전날) | 아침 브리핑 화~토 |
| `kr_or_after_us` | `trading_day` 또는 `after_us_session` | inventory c-3 아침 브리핑 조건 |
| `night_session` | `night_session_opens(D)` | GX :241 |
| `month_days: [1, 11, 21]` | 그 날짜(관세청 10일 잠정치 — `probe_results.md` §2.6) | — |

| `as_of` | 값 |
|---|---|
| `trade_date` | 그날(KST) — 거래일이 아니면 실행 안 함 |
| `prev_trading_day` | `prev_trading_day(그날)` — 예: 2026-10-06(화) → 2026-10-02(금, 10-05 대체공휴일) |
| `us_trade_date` | 끝난 미국 세션 날짜(뉴욕 날짜) |
| `run_date` | 실행 날짜(KST) |
| `minute`·`slot10m` | 분·10분 구간 시작(KST) |
| `ten_day` | `YYYYMM-1`·`-2`·`-3`(1~10·~20·말일) |
| `month`·`quarter` | 공표 기준 월·분기 |
| `event` | 이벤트 id(DART 접수번호 등) |

### 6.5 같은 데이터는 한 작업만 — 두 겹

| 겹 | 언제 | 무엇 |
|---|---|---|
| 정적(등록부 검증) | CI(`test_registry_validation.py`)·`python -m kbj.services.scheduler validate` | 같은 `(source, dataset)` 을 두 작업이 `collects` 에 두면 실패. 데이터셋은 카탈로그의 **논리 데이터셋**이라 같은 TR 이라도 쓰임이 다르면 다른 id 다(예: `KIS:stock_quote_eod` 대 `KIS:watch_quotes_intraday`) |
| 실행 시 선점 | 작업 실행 | `ops.data_claim` 에 `(source, dataset, as_of)` 을 `claimed` 로 넣는다(부분 유일 인덱스: `status IN ('claimed','done')`). 이미 있으면 받지 않고 `skipped(duplicate)`. 실패하면 `failed` 로 바꿔 재시도가 다시 잡을 수 있다. 같은 작업의 재시도(아직 공표 전 등)는 같은 선점을 이어 쓴다 |

명령 응답처럼 쌓지 않는 즉석 조회(`KIS:quote_on_demand`)는 선점 대상이 아니지만 리미터는 탄다.

### 6.6 등록부 검증 시험 (`tests/unit/scheduler/test_registry_validation.py`)

1. YAML 스키마(모르는 키 금지), 이름 유일·형식.
2. cron 파싱, `tz` 유효, `when`·`as_of` 값이 목록 안.
3. `depends_on` 대상 존재·비순환.
4. **`(source, dataset)` 유일** — 위반 시 두 작업 이름을 메시지에.
5. `collects` 가 전부 카탈로그에 있다. 카탈로그 등급과 `writes` 스키마 접두사가 맞다(로그인 출처를 `pub_*` 에 쓰지 않는다).
6. `enabled: true` 면 `owner` import 가능, `runner: external` 이면 `enabled: false`.
7. **U2**: `notify.kind` 가 `brief.morning` 인 작업 정확히 1개, `brief.closing` 1개, 두 종류의 정책이 `once_per_day`.
8. **inventory (c) 전부 반영**: `tests/unit/scheduler/legacy_jobs.txt`(SD `add_job` 43개 id + ET 워크플로·launchd·cron-job.org·레포 밖 루틴 + GX 스케줄러 단계 + 부팅 루틴)의 모든 줄이 어느 작업의 `absorbs` 나 `retired` 에 정확히 한 번 나온다.
9. `budget` 이 `limits.yaml` 에 있고, 한 예산을 쓰는 작업들의 하루 최대 호출 추정(데이터셋 수 × 재시도 횟수) 합이 상한 안.
10. P2 에 `enabled: true` 인 작업은 `filings.corp_code`·`ops.watchdog`·`ops.nightly` 뿐.

### 6.7 통합 시간표 → 등록부 (inventory c-3 반영, U2 적용)

시각은 KST, 조건 약어: T = `trading_day`, U = `after_us_session`, K∨U = `kr_or_after_us`. "단계" 는 kbj 실행을 연결하는 단계(그 전에는 `enabled: false` 로 등록만).

| 작업 | cron(KST)·트리거 | 조건 | 의존 | 재시도 | 수집 데이터 키(source:dataset @ as_of) | 소유 모듈 | 단계 | 흡수하는 legacy |
|---|---|---|---|---|---|---|---|---|
| `ops.nightly` | 03:00 | always | — | 1회 | — (pg_dump·인박스 14일 정리·`prv_alerts.notify_message` 30일 정리) | `kbj.services.ops.nightly:run` | **P2** (백업 대상 경로 [확인 필요]) | SD `db_backup_hourly`(매시 :30 Gist) — 폐지, ET Actions 캐시 DB |
| `filings.corp_code` | 03:05 | always | — | 3×10분 | `DART:corpCode @ run_date` | `kbj.services.collectors.corp_code:run` | **P2** | SD `dart_corp_map_update` 03:00, ET `.dart_corp.json`, dart-report `.cache/corp*` |
| `us.universe` | 일 04:00 | always | — | 2 | `NASDAQ:screener @ run_date` | `kbj.services.collectors.us:universe` | P3 | SD `refresh_us_universe`(Wikipedia) |
| `us.eod` | 16:10 **America/New_York** (서머타임 끝 11-01 전 05:10 KST, 뒤 06:10 KST) | `us_trading_day` | us.universe(soft) | 3×10분 | `NASDAQ:daily @ us_trade_date`, `YAHOO:us_index_daily @ us_trade_date` | `kbj.services.collectors.us:eod` | P3 | SD `us_market_daily` 05:50·`us_db_sync`(60분), ET `us-board.yml` 07:00 수집부 |
| `macro.morning` | 06:20 | K∨U | — | 3×10분 | `FRED:gov_series @ us_trade_date`, `TREASURY:yield_curve @ us_trade_date`, `NYFED:rates @ us_trade_date`, `FF:calendar @ run_date`, `YAHOO:macro @ us_trade_date` | `kbj.services.collectors.macro:morning` | P5 | ET bok 06:40·09:30 수집, SD `tg_briefing_refresh` 05:00, SD `global_data_refresh`(4시간) 매크로부 |
| `gex.night_minutes` | `state_enter: IDLE` 06:10~08:00 | night_session(전날) | — | GX 규칙 | `KIS:fut_minute_night @ trade_date` | `legacy:gexlab/services/scheduler/minute.py` (external) | P7 | GX `MinuteDaily`·`GapDaily` 야간 |
| `guru.research` | 07:30 | U | — | 2 | `ANTHROPIC:guru @ run_date` | `kbj.services.collectors.guru:run` | P5 | ET `xdigest.yml` 07:45·08:15·10:23 dispatch |
| `etf.collect` | 08:00 | T | — | 3×15분 | `ETF_ISSUERS:pdf @ trade_date` | `kbj.services.collectors.etf:run` | P5 | ET `daily.yml` 08:00·16:00 |
| `kis.master` | `state_enter: PRE_DAY`·`PRE_NIGHT` | T | — | 60초 | `KIS:fo_master @ trade_date` | `legacy:gexlab/services/scheduler/service.py` (external) | P7 | GX `Scheduler._master_step` |
| `krx.daily` | 08:05, 10분마다 10:00 까지 | T | — | every 600 s until 10:00 | `KRX:sto/stk_bydd_trd`·`sto/ksq_bydd_trd`·`sto/knx_bydd_trd`·`sto/stk_isu_base_info`·`sto/ksq_isu_base_info`·`idx/kospi_dd_trd`·`idx/kosdaq_dd_trd`·`idx/krx_dd_trd`·`etp/etf_bydd_trd`·`etp/etn_bydd_trd` **@ prev_trading_day** | `kbj.services.collectors.krx_daily:run` | P3 | SD `krx_api.*`, ET board KRX 확정치(`pipeline._apply_krx_snapshot`·`krx_regular_day`), ET kr 공공데이터 일봉, SD `mark_etf_stocks` 03:10, SD `kr_universe_daily` 08:00, SD `universe_sync_monthly` — + ADR 0001 Q1: 전날 KIS 잠정 종가와 대조·정정 |
| `gex.krx_derivatives` | 08:05~10:00 | T | — | GX 규칙 | `KRX:drv/fut_bydd_trd`·`drv/opt_bydd_trd @ prev_trading_day` | `legacy:gexlab/services/scheduler/krx.py:KrxDaily` (external) | P7 | GX `KrxDaily` |
| **`brief.morning`** | **08:10** | K∨U | us.eod(soft)·macro.morning(soft)·guru.research(soft)·gex.night_minutes(soft) | 캐치업 09:00 | — | `kbj.reports.briefs:morning` | P5 | SD `tg_morning` 08:30·`tg_overnight` 05:30·`tg_us_market_summary` 06:10·`agent_pipeline` 08:45, ET bok 06:40(발송)·us-board 07:00(발송)·guru 발송, GX 계획 06:05·08:30 |
| `market_stats.kofia` | 13:30 | T | — | 3×30분 | `DATAGO:15094809/credit`·`/capital`·`/fund`·`/cma @ prev_trading_day` (D+1 13시 개방 — `probe_results.md` §3.1) | `kbj.services.collectors.kofia:run` | P5 | (신규 — 상단 띠 신용잔고·예탁금) |
| `filings.dart_feed` | 매분 07:00~19:59 | T | — | 다음 분 | `DART:list @ minute` | `kbj.services.collectors.dart_feed:run` | P4 | SD `dart_poll`(매분 08~17시)·`earnings_pipeline_5min`(이벤트로), ET `ingest/dart.disclosures`·`triggers._dart` |
| `fin.quarterly` | 이벤트(정기보고서 접수 — dart_feed 가 일으킴) + 매일 07:30 점검 | always | — | 3 | `DART:fnlttSinglAcntAll @ quarter`, `DART:fnlttMultiAcnt @ quarter` | `kbj.services.collectors.fin:quarterly` | P4 | SD `dart_collector`(맥 18:00), ET `ingest/financials`, dart-report 수집부 |
| `rules.intraday` | 10분마다 09:00~15:30 | T | — | 없음 | `KIS:watch_quotes_intraday @ slot10m` | `kbj.engines.rules:intraday` | P8 | SD `tg_custom_alerts`·`tg_watchlist`·`tg_trailing`·`price_sync_intraday`·`stage2_realtime_kr` |
| `market.close_collect` | 15:35, 16:00 까지 | T | — | 5분×5 | `KIS:stock_quote_eod`·`KIS:stock_investor_daily`·`KIS:market_investor_daily`·`KIS:inst_foreign_top` **@ trade_date** (Q1 결정 전 기본값 — ADR 0001) | `kbj.services.collectors.market_close:run` | P3 | SD `price_sync_close`·`flow_batch`·`ohlcv_autofill`·`price_sync_afterhours`, ET board 16:10 수집부(네이버 일봉 2,800회 대체), ET kr 09:30·15:30 KIS 수급, ET flow 18:17 수집부 |
| `gex.day_minutes` | `state_enter: POST_DAY` 16:00~17:50 | T | — | GX 규칙 | `KIS:fut_minute_day @ trade_date` | `legacy:gexlab/services/scheduler/minute.py` (external) | P7 | GX `MinuteDaily`·`GapDaily`·`OpenCheck` |
| `board.daily` | 16:20 | T | market.close_collect(hard) | 2 | — (DB 만) | `kbj.engines.board:daily` | P3 | ET board 16:10 계산·랭킹·탐지·LLM 초안, SD `prewarm_new_highs` 15:48·`stage2_auto` 16:00, SD `data_json_close`·`data_json_evening`(폐지) |
| **`brief.closing`** | **16:40**, 캐치업 20:30 | T | market.close_collect(hard)·board.daily(soft) | 하루 1회 | — | `kbj.reports.briefs:closing` | P3 | SD `tg_closing` 15:40·`tg_closing_summary` 16:00·`closing_brief_catchup`·`agent_pipeline` 15:45, ET board 16:10 발송 4종, GX 계획 15:50 |
| `themes.monitor` | 16:50 | T | market.close_collect | 1 | — | `kbj.engines.themes:monitor` | P5 | ET `kr.yml` 09:30·15:30(장중판 폐지 [제안]) |
| `macro.evening` | 17:00 | T | — | 3×30분 | `ECOS:817Y002 @ trade_date`, `ECOS:722Y001 @ trade_date` | `kbj.services.collectors.macro:evening` | P5 | ET bok 16:30·22:00 |
| `flows.report` | 18:20 | T | board.daily·market.close_collect | 하루 1회 | — | `kbj.reports.flows:daily` | P3(수집)·P5(발송) [제안 — U2 범위 밖] | ET `flow.yml` 18:17, SD `tg_flow_signals` 19:30 |
| `consensus.snapshot` | 18:30 | T | market.close_collect | 2 | `KIS:consensus_estimate @ trade_date` [추정 TR] | `kbj.services.collectors.consensus:run` | P4 | SD `consensus_snapshot_daily` 18:00·`tg_revision_signals` 18:30·`consensus_quarterly_weekly`(월 06:00) — ⚠ 대체 출처 미확인(conflict_map §1.13) |
| `fin.valuation_band` | 18:40 | T | market.close_collect | 1 | — | `kbj.engines.valuation:band` | P4 | SD 맥 crontab `valuation_calculator` |
| `earnings.backfill` | 06:30 | always | — | 1 | — | `kbj.engines.earnings:backfill` | P4 | SD `earnings_backfill_daily` |
| `trade.customs_tenday` | 10:00 매월 1·11·21일 | `month_days` | — | 1시간×6 | `DATAGO:15157908`·`15157941`·`15157901`·`15157909 @ ten_day` | `kbj.services.collectors.customs:tenday` | P6 | (신규 — bok 수출 워크북 수동 입력 대체) |
| `trade.customs_monthly` | 10:00 매월 15~17일 | `month_days` | — | 1일 | `DATAGO:15101609`·`15101612`·`15100475`·`15134343 @ month` | `kbj.services.collectors.customs:monthly` | P6 | (신규) |
| `macro.monthly` | 09:10 | always | — | 1 | `ECOS:<월간 공개 표> @ month`, `KOSIS:<표> @ month` | `kbj.services.collectors.macro:monthly` | P5 | (신규) |
| `reports.dart_excel` | 09:00 2·5·8·11월 16일 | always | fin.quarterly | 1 | — | `kbj.reports.dart_excel:run` | P4 | ET `report.yml` |
| `market.backfill` | 수동(`run-once`) | — | — | — | `KRX:sto/*_bydd_trd @ <과거 날짜>`(백필 예산) | `kbj.services.collectors.krx_daily:backfill` | P3 | SD `ohlcv_5y_collector`·`ohlcv_autofill` 백필, ET 네이버 일봉 백필(U4 — 네이버 행 대신 KRX 로 다시 받기) |
| `ops.watchdog` | 30분마다 08:00~20:00 | T | — | 없음 | — | `kbj.services.collectors.ops_watchdog:run` | **P2** | SD `data_watchdog`, ET `daily.yml` 실패 curl(#37), GX health |
| (상시) `auth` | 30초 step | — | — | — | `KIS:token`(발급 — 데이터 키 아님) | `kbj.services.auth` | **P2** | K1~K9 |
| (상시) `notifier` | outbox 소비·웹훅·10분 웹훅 점검 | — | — | — | — | `kbj.services.notifier` | **P2** | #1~#46 |
| (상시) GX poller·ws-gateway·recorder·engine | 연속 | 세션 | — | — | `KIS:option_chain_live`·`KIS:market_investor_intraday`·`KIS:ws_ticks` | legacy GX (external) | P7 | — |

### 6.8 폐지하는 legacy 정기 작업 (`retired:`)

| legacy 작업 | 사유 |
|---|---|
| SD `market_update`(interval, `DISABLE_AUTO_FETCH`) | pykrx·data.json — 쓰지 않음(U4) |
| SD `self_keepalive`(4분), SD `.github/workflows/wake.yml` | Render 전용(D2) |
| SD `tg_reports`(`alert_new_reports` 10:00) | 네이버 리서치 — 대체 없음(conflict_map §1.13) |
| SD `gen_themes_mapping`(일 03:20) | 네이버 테마 크롤(U4) → P5 분류 사전 |
| SD `refresh_options`(22:00, yfinance) | 쓰는 화면 재검토 전 폐지 [제안] |
| SD `data_json_close`·`data_json_evening` | data.json 정적 산출 폐지 |
| SD `db_backup_hourly`(Gist) | `ops.nightly` pg_dump |
| SD `stage2_auto` 08:00 회차 | 발굴은 장마감 뒤 1회(P8) |
| SD 맥 crontab 18:00 의 Gist·git push 단계 | DB 일원화 |
| ET `artifacts-gc.yml`, cron-job.org dispatch(`board-daily`·`board-send`·`xdigest-*`·`etf-daily`), 맥 launchd board 16:10, 레포 밖 Claude 루틴 17:09, `board.yml` dispatch | 스케줄러 하나(PLAN §2) |
| ET `etf_tracker_v9/live_update.py`(예약 없음) | 죽은 진입점 |
| ET `xdigest` 구모드 | 폐기 [제안](inventory (b)) |
| SD 부팅 1회성 묶음(Gist 복원·setWebhook·일봉 채움·캐치업 발송) | 부팅이 일을 일으키지 않는다 — 등록부 작업과 `catch_up_until` 로 |
| SD HTTP 수동 실행 `POST /api/ops/cron/trigger/<job_id>`:18370 | `run-once`(인증된 운영 화면은 P3) |

SD `add_job` 43개 전부의 행선지: `market_update`·`self_keepalive`·`tg_reports`·`gen_themes_mapping`·`refresh_options`·`data_json_close`·`data_json_evening`·`db_backup_hourly`(→ops.nightly)·`us_db_sync` = 폐지 9 / `tg_morning`·`tg_overnight`·`tg_us_market_summary`·`tg_briefing_refresh`(→macro.morning·us.eod)·`global_data_refresh`(→macro.morning) → 아침 계열 / `tg_closing`·`tg_closing_summary`·`closing_brief_catchup`·`agent_pipeline` → brief.closing / `flow_batch`·`price_sync_close`·`price_sync_afterhours`·`ohlcv_autofill` → market.close_collect / `tg_flow_signals` → flows.report / `prewarm_new_highs`·`stage2_auto` → board.daily(P3)·발굴(P8) / `tg_watchlist`·`tg_custom_alerts`·`tg_trailing`·`price_sync_intraday`·`stage2_realtime_kr` → rules.intraday / `tg_revision_signals`·`consensus_snapshot_daily`·`consensus_quarterly_weekly` → consensus.snapshot / `dart_poll`·`earnings_pipeline_5min` → filings.dart_feed / `dart_corp_map_update` → filings.corp_code / `mark_etf_stocks`·`kr_universe_daily`·`universe_sync_monthly` → krx.daily / `refresh_us_universe` → us.universe / `us_market_daily` → us.eod / `data_watchdog` → ops.watchdog / `earnings_backfill_daily` → earnings.backfill. 합계 43(SD:`server.py`:7072~7517). 이 대응은 §6.6-8 시험 입력 파일로 고정한다.

### 6.9 P2 경계

- P2 의 등록부는 **전부 들어 있지만** `enabled: true` 는 `filings.corp_code`·`ops.watchdog`·`ops.nightly` 와 상시 서비스 셋뿐이다.
- legacy 작업을 KBJ 스케줄러가 부르는 일은 없다. legacy 코드는 P2 동안 시험에서만 돌고, 운영 VM 에서도 돌리지 않는다 **[확인 필요: P2 기간 SD Render·ET launchd 를 그대로 둘지 — 두면 옛 발급자(K1·K3)가 산다. 전환일에 함께 멈추는 것이 conflict_map §1.1 이행 8번]**.
- 각 작업을 kbj 로 옮기는 일(소유 모듈 구현·`enabled: true`·legacy 삭제)은 그 작업 행의 단계에서 한다.

---

## 7. 캘린더

### 7.1 승격·확장

| 항목 | 내용 |
|---|---|
| 승격 | GX:`core/calendar.py` 를 `kbj/core/calendar.py` 로(이름·동작 그대로). pyright strict 는 GX 에서도 `core` 가 strict 였다(GX `pyproject.toml:50`) — 그대로 통과 **[추정]** |
| 덮어쓰기 파일 | 레포 루트 `config/holidays_override.yaml`(GX 파일 그대로 — 2026-06-03 지방선거 + `late_open`). `TradingCalendar.default()`·`calendar_compat` 는 늘 `OVERRIDE_PATH`(레포 루트)를 읽는다. 서비스 진입점(scheduler `__main__._build`)은 `TradingCalendar.from_override(load_override(config_path(...)))` 로 `Settings.config_dir` 의 파일을 쓴다(구현 메모) |
| 신규 `exchange` 인자 | `TradingCalendar(exchange="XNYS")` → `us_calendar()`. 미국 일정(`us.eod`·아침 브리핑 조건)을 XNYS 로 판정(conflict_map §1.6 [제안]) |
| 신규 주식 시간 | GX 상태 머신은 **파생** 세션(08:45~15:45)이다. 주식 정규장 09:00~15:30 은 `equity_session_bounds`·`is_equity_regular_hours` 로 따로. 수능일·연초 지연 개장은 GX 도 반영하지 않았다(GX `calendar.py` 머리말) → **[결정 R24 — 구현]** `TradingCalendar.equity_bounds(d)`: override `late_open:`(수능일 10:00~16:30 — XKRX 4.13.2 는 모른다, 해마다 KRX 공지로 더한다 [확인 필요: 2026-11-19 공지]) > 그해 첫 거래일 10:00 개장(규칙 — XKRX 도 안다) > 평소 09:00~15:30. 작업 등록부 `equity` 트리거(`market.close_collect` 등)가 이것을 따른다(수능일 마감 수집 16:35). 파생 세션 상태 머신(`state_at`)은 수능일 지연을 아직 반영하지 않는다 [확인 필요]. compat `is_kr_regular_hours` 는 [09:00, 15:30) 반열림(SD 는 15:30 분 전체를 장중으로 봤다 — 의도한 변경) |
| 의존성 | `exchange-calendars`(GX `pyproject.toml:7` `>=4.13.2`)를 KBJ 런타임 의존성에 더한다(S0). pandas 는 P0 단일 해석 2.3.3(conflict_map §3.3) |

### 7.2 legacy 휴장 판정 대체

| legacy(원본 줄) | 지금 | 바꾸는 방법(H) | 남는 것 |
|---|---|---|---|
| SD:`server.py:_KR_HOLIDAYS_2026`:17971 | 2026년만 하드코딩 | **삭제** | — |
| SD:`server.py:_is_kr_holiday`:17987(쓰는 곳 :14868·:17999·:18020) | 주말 + 하드코딩 | 본문 → `kbj.core.calendar_compat.is_kr_holiday(dt)` | 함수 이름(호출부 유지) |
| SD:`server.py:_next_trading_open_kst`:17994 | 09:00 + 하드코딩 | → `calendar_compat.next_trading_open(now)` | 같음 |
| SD:`server.py:is_market_hours`:174 | 평일 09:00~15:30(휴장 모름) | → `calendar_compat.is_kr_regular_hours(now_kst())` | 같음 |
| SD:`kis_api.py:_is_kr_market_hours`:146 | 같음 | 같음 | 같음 |
| SD `now_kst` 4벌(`server.py:57` 등) | `datetime.now(KST)` | `kbj.core.time.utcnow()` 기반으로(벽시계 한 곳) | 함수 이름 |
| ET:`board/engine/db.py:trading_days`:202, `board/us/db.py`:156 | 일봉 날짜 합집합 | **그대로**(데이터가 있는 날 판정) — 휴장 판정에 쓰는 호출부만 캘린더로 | 함수 |
| ET:`etf_tracker_v9/tracker.py:prev_trading_day`:566 | DB 날짜 | → `TradingCalendar.default().prev_trading_day` | 함수 이름 |
| ET:`monitor/flow/narrative.py:prev_trading_day`:73 | 데이터 날짜 | → 캘린더 | 같음 |
| ET:`board/run.py:_send_files` only_fresh(:1086 근처) | "기준일 ≠ 오늘이면 휴장 추정" | → `is_trading_day(today)` 로 명시 | — |
| ET `now_kst` 4벌(`board/engine/build.py:67`·`engine/db.py:16`·`xdigest/analyze.py:62`·`etf_tracker_v9/dash.py:17`) | 각자 | `kbj.core.time` 다시 내보내기 | — |
| ET bok `send-telegram.js --morning-only`, guru `run_weekdays` | 평일 판정 | 이식 안 함 / 등록부 `when` | — |
| GX `core/calendar.py` | 정본 | legacy GX 쪽은 `from kbj.core.calendar import *` 다시 내보내기 | — |

### 7.3 SD 2026 하드코딩과 캘린더의 차이 (실측)

`exchange_calendars` XKRX + GX 덮어쓰기와 SD `_KR_HOLIDAYS_2026` 을 2026년 평일 전부에 대어 본 결과(P0 시험 venv `/tmp/p0test/venv-312`, 2026-10-06):

| 날짜 | XKRX+덮어쓰기 | SD | 뜻 |
|---|---|---|---|
| 2026-05-01(금) | 휴장 | 개장 | 근로자의 날 — SD 누락 |
| 2026-06-03(수) | 휴장 | 개장 | 지방선거(덮어쓰기) — SD 누락 |
| 2026-08-17(월) | 휴장 | 개장 | 광복절 대체공휴일 — SD 누락 |
| 2026-09-28(월) | **개장** | 휴장 | SD 가 추석 대체로 잘못 넣음(GX `test_dependencies.py` 도 개장으로 고정) |
| 2026-10-05(월) | 휴장 | 개장 | 개천절 대체공휴일 — SD 누락(conflict_map E1 의 10/5 기록과 맞다) |

이 다섯 날을 `test_legacy_holiday_parity.py` 의 기대값으로 고정한다(SD 함수를 바꾼 뒤 legacy 쪽 결과가 캘린더와 같아지는지).

---

## 8. DB

### 8.1 도메인별 스키마 (0001 그대로 + P2 표)

| 스키마 | 등급 | 내용 | P2 에 만드는 표 |
|---|---|---|---|
| `ops` | 운영 | 실행·선점·발송 메타·health·세션·이관 기록 | `schema_migrations`(0001), `job_run`, `data_claim`, `kv`, `notify_log`, `session_log`, `health_events`, `legacy_import`, `collection_gaps`·`quarantine`·`collection_reports`(GX) |
| `prv_market` | 로그인 | 스냅·일봉·유니버스 | `stock_snapshot`, `daily_bar`, `universe` |
| `prv_flows` | 로그인 | 투자자 수급 | `stock_investor_daily`, `market_investor_daily`, 뷰 `market_investor_intraday` |
| `prv_gex` | 로그인 | GX 표(이름 그대로) | GX 001~004 의 16표(골격 — P7 에 씀) |
| `prv_alerts` | 로그인(개인) | 인박스·발송 본문 (규칙·이력은 P8) | `tg_inbox`, `notify_message` |
| `pub_filings` | 공개 | corp_code (공시는 P4) | `corp_code` |
| `pub_fin`·`pub_trade`·`pub_macro`·`pub_market_stats`·`pub_themes`·`prv_board`·`prv_fin`·`prv_themes`·`prv_macro`·`prv_etf`·`prv_journal` | — | P3 이후 | (없음) |

### 8.2 마이그레이션 목록

파일 이름은 `NNNN_소문자.sql`(GX 적용기 규칙, `tests/test_store_layout.py`). 모두 `IF NOT EXISTS`·멱등, 적용 뒤에는 고치지 않는다(체크섬).

| 파일 | 단계 | 내용 |
|---|---|---|
| `0001_schemas.sql` | P1(있음) | 스키마·역할·권한·`ops.schema_migrations` |
| `0002_ops_core.sql` | **P2** | `ops.job_run`, `ops.data_claim`(+부분 유일 인덱스), `ops.kv`, `ops.notify_log`, `ops.session_log`·`ops.health_events`(GX:`db/migrations/001_init.sql`:296~325 열 그대로, hypertable), `ops.legacy_import` |
| `0003_market_flows.sql` | **P2** | `prv_market.stock_snapshot`, `prv_market.daily_bar`(hypertable `trade_date`, 365일 청크), `prv_market.universe`, `prv_flows.stock_investor_daily`, `prv_flows.market_investor_daily` |
| `0004_filings_corp.sql` | **P2** | `pub_filings.corp_code` |
| `0005_alerts_inbox.sql` | **P2** | `prv_alerts.tg_inbox`, `prv_alerts.notify_message` |
| `0006_gex.sql` | **P2(골격)** | GX 001~004 를 `prv_gex`(raw_messages·fut_ticks·opt_ticks·chain_snapshots·fut_board·investor_flow·series_expiries·minute_bars·krx_fut_daily·krx_opt_daily·master_snapshots·levels·metrics·strike_gex·option_iv·oi_changes — `flag` 열 포함)와 `ops`(collection_gaps·quarantine·collection_reports)로. 뷰 `prv_flows.market_investor_intraday AS SELECT … FROM prv_gex.investor_flow`(conflict_map §1.5 — GX 코드를 고치지 않고 이름만 맞춤) |
| `0007_board.sql` | P3 | `prv_board.alltime`·`label`(market 열 KR·US, kind 에 w52_low)·`split_check`·`artifact`, `pub_themes.sector_map`(board48 — 자체 사전) |
| `0008_filings.sql` | P4 | `pub_filings.disclosure`·`overhang`·`earnings_actual` |
| `0009_fin.sql` | P4 | `pub_fin.quarterly`·`ratio`, `prv_fin.consensus_snapshot`·`consensus_quarterly`·`revision`·`valuation_band`·`earnings_surprise`·`ratio`, `prv_alerts.queue` |
| `0010_journal_rules.sql` | P8 | `prv_alerts.rule`·`event`, `prv_journal.analysis`·`recommendation`·`trade`·`portfolio`·`watchlist` |
| `0011_macro_stats.sql` | P5 | `pub_macro.series`·`prv_macro.series`(시리즈 단위 등급), `pub_market_stats.*` |
| `0012_trade.sql` | P6 | `pub_trade.*`(월간·10일·시군구, 열 대응 버전) |
| `0013_themes_etf.sql` | P5 | `pub_themes.*`·`prv_themes.*`, `prv_etf.fund`·`holding`·`etf_ticker`·`change_log`·`etf_meta`·`etf_aum` |

### 8.3 P2 표 요약

| 표 | 키 | 주요 열 | 비고 |
|---|---|---|---|
| `ops.job_run` | `run_id` | `job`, `as_of`, `attempt`, `status`(queued·running·ok·failed·skipped·timeout), `started_at`, `finished_at`, `detail`, `source`(scheduler·manual·legacy_import) | SD `run_log`·ET `run_log` 흡수 |
| `ops.data_claim` | `id` + 부분 유일 `(source, dataset, as_of) WHERE status IN ('claimed','done')` | `job`, `run_id`, `status`, `claimed_at`, `done_at`, `rows`, `detail` | §6.5 |
| `ops.kv` | `(namespace, key)` | `value jsonb`, `updated_at` | SD `ops_state`(`server.py:_ops_get`:14876), ET `meta` |
| `ops.notify_log` | `id`, `dedup_key` 유일 | §5.4 | — |
| `ops.legacy_import` | `batch_id` | `source_name`, `source_sha256`, `source_table`, `target_table`, `phase`, `rows_read`, `rows_dropped`, `rows_written`, `key_digest_src`, `key_digest_dst`, `sums jsonb`, `status`, 시각 | §8.6 |
| `prv_market.stock_snapshot` | `(market, code, trade_date, source)` | `name`, `segment`, `close`, `chg_pct`, `volume`, `turnover`, `turnover_is_estimate`, `mktcap`, `shares`, `quality`, `received_at` | 코드 text(앞자리 0 유지) |
| `prv_market.daily_bar` | `(market, asset, code, trade_date, source)` | `open`·`high`·`low`·`close`·`volume`·`turnover`, `adjusted bool`, `quality`, `received_at` | `asset` = stock·etf·etn·index. KRX 일별은 비수정 가격 — `adjusted=false`(conflict_map §1.13 ⚠) |
| `prv_market.universe` | `(market, code, as_of)` | `name`, `kind`(보통주·우선주·스팩·리츠·ETF·ETN — ET `engine/kinds.py`), `listed_on`, `flags jsonb`, `source` | Q11 결정 전에는 플래그 열로 기능별 하한 |
| `prv_flows.stock_investor_daily` | `(code, trade_date, investor, source)` | `net_qty`, `net_value`, `unit`, `quality` | KIS 금액 = 실거래대금 |
| `prv_flows.market_investor_daily` | `(market_code, trade_date, investor, source)` | `net_value`, `unit`, `quality` | KIS `FHPTJ04040000` |
| `pub_filings.corp_code` | `corp_code` | `stock_code`, `corp_name`, `modify_date`, `source='DART'`, `received_at` | 매일 갈아 넣기 |
| `prv_alerts.tg_inbox` | `update_id` | §5.8 | — |
| `prv_alerts.notify_message` | `dedup_key` | `body`, `parse_mode`, `created_at` | 30일 |

모든 값 표에 `source`·`quality`(ok·stale·estimated·invalid) 열을 둔다(CLAUDE.md 절대 규칙 1, `kbj/core/quality.py`). 시각은 `timestamptz`, 날짜는 `date`.

**구현 메모(묶음 G — 위 표와 달라진 점)**: universe 키에 `source` 추가 · 값 표(`prv_market`·`prv_flows`) 키에 `venue`(KRX·NXT·TOTAL — 결정 D7) 추가 · `loaded_by`(적재한 작업 이름, NOT NULL — 이관 검증용 원본 표시) 열 · `prv_flows.stock_investor_daily` 도 hypertable(365일) · 이관분은 원본에 시각이 없으면 `received_at`·`ops.kv.updated_at` 이 NULL(지어내지 않는다) · `ops.legacy_import` 는 `id` + UNIQUE(`batch_id`, `mapping`) · `ops.data_claim` 은 `venue` 열(기본 '')을 키에 포함, `done` 이면 `done_at` 필수. 적용기·이관 API(§1.8)는 `Mapping(source, …)`(`source_db` → `source`, 버리기는 transform `Drop`), `run(sources, target, *, phase, now, dry_run, verify_only, batch_id)` — 시계 주입, `PgTarget`·`MemoryTarget` 대상 추상. ETF 일별·장중 표(`prv_etf.etf_daily`·`quote_intraday`)는 0013(P5) 예정 — D7 ETF 수급을 P3 에 받으려면 당겨야 한다 [결정 필요].

### 8.4 SQLite·JSON → Postgres 매핑

| 원본(DB·표) | → KBJ | 변환·버림 규칙 | 실행 단계 |
|---|---|---|---|
| SD `dashboard.db` `ops_state` | `ops.kv(namespace='sd')` | — | **P2** |
| SD `fetch_progress` | `ops.kv(namespace='sd.fetch_progress')` | 행 → JSON | **P2** |
| ET `board.db` `meta` / `us_board.db` `meta` | `ops.kv(namespace='board.kr' / 'board.us')` | 이름 충돌 해소 | **P2** |
| ET `board.db`·`us_board.db` `run_log` | `ops.job_run(job='legacy.board.kr' / '.us', source='legacy_import')` | `run_id = job:asof:ts` | **P2** |
| SD `index_universe` | `prv_market.universe(source='sd.valuechain_v2')` | 자체 목록(네이버 아님) | **P2** |
| ET `board.db` `snap` | `prv_market.stock_snapshot(market='KR')` | **`source='naver'` 행은 버린다**(U4, conflict_map §1.5) — 나머지만 | **P2** |
| ET `us_board.db` `snap` | `prv_market.stock_snapshot(market='US')` | Nasdaq 출처(로그인) | **P2** |
| ET `board.db` `px`, `backtest.db` `px` | `prv_market.daily_bar(market='KR', asset='stock')` | 네이버 행 버림, `(code, asof, source)` 중복은 board.db 우선 | **P2** |
| ET `us_board.db` `px`, `us_backtest.db` `px` | `prv_market.daily_bar(market='US')` | 같음 | **P2** |
| SD `stocks` | (이관 안 함) | 네이버 동기화 스냅 — 다시 받는다 | — |
| SD `ohlcv` | (이관 안 함) | 출처 열이 없어 네이버·pykrx 행을 가를 수 없다 → `market.backfill` 로 KRX 에서 다시 받는다 **[제안]** | P3 백필 |
| ETF `etf.db` `etf_px` | (이관 안 함) | 네이버 출처(`etf_tracker_v9/market.py`) → KRX `etp/etf_bydd_trd` 로 다시 | P3 |
| SD `flow_cache` | `prv_flows.stock_investor_daily` | JSON 배열을 행으로 펼친다. `source='kis'` 만, 네이버는 버림 | **P2** |
| ET `state/*/stockflows.json`·`flows.json`, kr `cache/flows.json` | `prv_flows.*` | KIS 행만 | **P2**(파일이 있으면) |
| GX `investor_flow` | `prv_gex.investor_flow`(뷰로 `prv_flows`) | 이미 Postgres — `pg_dump --data-only` + `search_path` | P7 |
| SD `dart_corp_map`, ET `.dart_corp.json`, dart-report `.cache/corp*` | `pub_filings.corp_code` | **이관 안 함 — corpCode.xml 로 다시 받는다**(conflict_map §1.5) | P2(작업으로) |
| ET `state/inbox.json` | `prv_alerts.tg_inbox` | 항목 그대로 | **P2** |
| SD `cache/kis_token.json`, ET `state/.kis_token.json`, GX `state/kis.token.json` | **절대 이관·열람 안 함** — 전환 뒤 삭제 | — | — |
| SD `chart_cache`·`misc_cache`·`yinfo_cache`·`discover_results`, `llm_cache.db` | 이관 안 함(캐시) | — | — |
| SD `financial` | 이관 안 함(네이버) | — | — |
| SD `alert_rules`+`cache/alert_rules.json`+localStorage, `alert_history`·`alert_history_v2` | `prv_alerts.rule`·`event` | 최신 우선 병합, 두 이력 합침 | P8 |
| SD `disclosure_history`·`dart_disclosure_overhang`·`earnings_actual` | `pub_filings.*` | — | P4 |
| SD `financial_quarterly`, ET `state/financials.json` | `pub_fin.quarterly` | Q4 결정 뒤 | P4 |
| SD `consensus_snapshot`·`revision_alerts`·`consensus_estimate`·`consensus_quarterly`, `valuation_band`·`earnings_surprise`·`earnings_alert_queue` | `prv_fin.*`·`prv_alerts.queue` | `source='naver'` 는 표시만 남기고 이어 쌓기(conflict_map) | P4 |
| ET `alltime`·`label`(+us `label`·`lowlabel`)·`split_check`, `state/<날짜>/*.json` | `prv_board.*` | market 열 | P3 |
| ET `sector_map`·`knowledge/*.yaml` | `pub_themes.*` | 자체 사전만 | P5 |
| ETF `fund`·`holding`·`etf_ticker`·`change_log`·`etf_meta`·`etf_aum` | `prv_etf.*` | `etf_live` 폐기 | P5 |
| SD `analysis_journal`·`recommendation_history`·`trade_journal`, `server_*.json`, ET `notes/*.yaml`·`signal_ledger.json` | `prv_journal.*` | — | P8 |

### 8.5 이름 충돌 해결 (inventory (e) 끝 정리)

| 충돌 | 해결 |
|---|---|
| 같은 이름 다른 DB: `px`·`snap`·`label`·`meta`·`run_log`(board.db·us_board.db·backtest.db) | `market` 열(KR·US)로 한 표에, `meta` → `ops.kv` namespace, `run_log` → `ops.job_run` job 이름 |
| 같은 DB 이중 정의: SD `consensus_snapshot`·`revision_alerts`(`db/schema.sql` 대 `migrations/008`) | KBJ 정의 하나(P4). 이관 때 실제 열을 읽어 맞춘다 |
| 같은 데이터 다른 이름: 일봉(`ohlcv`·`px`·`etf_px`·`minute_bars`), 스냅(`stocks`·`snap`), 수급(`flow_cache`·`investor_flow`·JSON 4종), corp_code 3벌, 유니버스 4벌 | `daily_bar`(asset 열)·`stock_snapshot`·`stock_investor_daily`·`corp_code`·`universe` 하나씩. 분봉(`minute_bars`)은 GX 그대로 `prv_gex` |
| Postgres `public` 스키마 | 쓰지 않는다(ADR 0002) |
| GX 운영 표 `session_log`·`health_events` 와 KBJ 운영 표 | GX 열 그대로 `ops` 에 하나 — kbj 서비스도 같은 표에 쓴다 |

### 8.6 이관 스크립트 (`python -m kbj.store.legacy_import`)

| 단계 | 처리 |
|---|---|
| 입력 | `--source sd=<dashboard.db> --source board=<board.db> --source us=<us_board.db> --source backtest=… --source etf=<etf.db> --json inbox=<inbox.json> …`, `--phase P2`(그 단계 매핑만), `--dry-run`, `--verify-only`. 원본 사본 선택은 ADR 0001 Q12(맥 로컬 사본 — 사용자 제공) |
| 원본 열기 | `sqlite3.connect("file:<경로>?mode=ro&immutable=1", uri=True)` — 읽기만. 파일 sha256 을 `ops.legacy_import.source_sha256` 에 |
| 변환 | 매핑마다 순수 함수 `transform(row) -> TargetRow \| Drop(reason)`(날짜 TEXT→date, 코드 6자리 text, 네이버 행 버림 사유) — 단위 시험 |
| 쓰기 | 임시 표로 `COPY` → `INSERT … ON CONFLICT (키) DO NOTHING`(이력) / `DO UPDATE`(나중에 고쳐질 수 있는 값) — 표마다 한 트랜잭션 |
| 기록 | `ops.legacy_import` 한 줄(배치·원본·대상·행 수·버림 수·요약값) |
| 출력 | `원본.표 → 스키마.표: 읽음 N · 버림 M(사유별) · 씀 K · 키 다이제스트 일치/불일치` — 값·키는 찍지 않는다 |

### 8.7 검증·멱등

| 검증 | 방법 |
|---|---|
| 행 수 | `읽음 − 버림 = 대상에서 이 배치 원본 표에 해당하는 행 수`(대상 행에 `source`·원본 이름을 남겨 센다) |
| 키 합계 | 양쪽에서 `md5(string_agg(키 열 연결, ',' ORDER BY 키))` — SQLite 쪽은 파이썬으로, Postgres 쪽은 SQL 로 같은 정규화(코드 6자리, 날짜 ISO) 뒤 비교 |
| 값 합계 | 숫자 열 합(`close`·`volume`·`net_value` 등)을 허용오차로(CLAUDE.md §4 — 바이트 비교 금지) |
| 멱등 | 같은 원본으로 두 번 → 두 번째 `씀 0`(DO NOTHING) 또는 바뀐 행 0(DO UPDATE 는 `IS DISTINCT FROM` 조건), 다이제스트 같음 |
| 실패 | 불일치면 종료 코드 1, 트랜잭션 되돌림. 원본은 건드리지 않는다 |

### 8.8 DB 시험

`test_store_layout.py` 확장(0002~0006: 파일명·멱등 문장·스키마 접두사·`pub_*` 표에 로그인 출처 열 없음), `test_migrate_unit.py`, `test_import_transforms.py`, `test_import_sqlite.py`(옛 DDL — ET `engine/db.py:30~83`, SD `db/schema.sql` — 로 만든 **합성** SQLite), 통합 `test_migrations_pg.py`·`test_import_pg.py`(Docker, `@pytest.mark.integration`).

---

## 9. 직접 호출 금지 검사 (`scripts/check_canonical.py`)

### 9.1 규칙 그룹

| 그룹 | 패턴(정규식) | 허용 위치(kbj) | legacy 목표 |
|---|---|---|---|
| `kis_oauth` | `oauth2/(?:tokenP\|Approval)` | `kbj/services/auth/**` | **0 (P2)** |
| `kis_rest` | `openapi(?:vts)?\.koreainvestment\.com` | `kbj/data/private/kis/**` | **0 (P2)** |
| `kis_master` | `download\.dws\.co\.kr` | `kbj/data/private/kis/**` | **0 (P2)** |
| `krx_api` | `data-dbg\.krx\.co\.kr` | `kbj/data/private/krx/**` | **0 (P2)** |
| `dart` | `https?://opendart\.fss\.or\.kr` (스킴이 있어야 — 엑셀 출처 표기 `dart-report/excel.py:108`·도움말 `app.py:42` 는 걸리지 않는다) | `kbj/data/public/dart/**` | **0 (P2)** |
| `telegram` | `api\.telegram\.org` | `kbj/services/notifier/telegram_api.py` | 기준선(사본 워크플로만 남김) |
| `ecos` | `ecos\.bok\.or\.kr` | `kbj/data/public/ecos/**` | 기준선(지금 0 — bok 미이식) |
| `datago` | `apis\.data\.go\.kr` | `kbj/data/datago.py`, `kbj/data/public/{customs,fsc_kofia_stats}/**`, `kbj/data/private/fsc_*/**` | 기준선 |
| `kosis` | `kosis\.kr/openapi` | `kbj/data/public/kosis/**` | 기준선(지금 0) |
| `kis_ws` | `\bops\.koreainvestment\.com` | `kbj/services/ws_gateway/**`(P7) | 기준선 → P7 에 0 (D-P2-4) |
| `naver` | `(?:finance\|m\.stock\|api\.stock\|polling\.finance\|fchart\.stock\|openapi\|search)\.naver\.com\|navercomp\.wisereport\.co\.kr` | **없음**(kbj 어디에도 금지 — U4) | 기준선 → P3~P5 에 0 |
| `krx_scrape` | `(?<![-\w])data\.krx\.co\.kr\|\bpykrx\b` | 없음 | 기준선 → P3 에 0 |

### 9.2 검사 범위

- 대상: `git ls-files --cached --others --exclude-standard`(check_public_safety 와 같은 방식) 중 `.py .js .mjs .sh .yml .yaml .toml .cfg .ini .html`.
- 제외: `*.md`·`docs/**`, 시험(`**/tests/**`, `**/test_*.py`, `**/*_test.py`, `**/tests_smoke.py`, `**/fakes/**`, `**/fixtures/**`) — 시험은 가짜 오류 문구에 호스트를 담는다(예: ET `board/tests/test_stockflows.py:28~34`, `test_tg_inbox.py:370`). 검사 스크립트 자신(패턴은 조각을 이어 만든다).
- `kbj/**` 는 허용 위치 밖이면 **어느 그룹이든** 실패(기준선 없음). `legacy/**` 는 목표 0 그룹은 실패, 기준선 그룹은 §9.3.

### 9.3 기준선 — 줄어들기만

- 파일 `scripts/canonical_baseline.txt`, 줄 형식 `그룹 | 경로 | 건수 | 정리 단계·사유`(사유 필수 — 없으면 종료 코드 2).
- 실패 조건: ① 기준선에 없는 파일에서 기준선 그룹 위반 ② 건수가 기준선보다 많음 ③ **건수가 기준선보다 적은데 기준선을 안 줄였음**(줄어든 만큼 즉시 고정 — 다시 늘 수 없게) ④ 목표 0 그룹이 기준선에 있음 ⑤ `kbj/**` 경로가 기준선에 있음.
- `--update-baseline` 은 **줄이는 방향으로만** 다시 쓴다(늘리려 하면 거부).
- 0 으로 할지 기준선으로 둘지: PLAN 완료 기준이 명시한 KIS·KRX·DART 는 **0**(P2 에 legacy 수정 범위가 작다 — §9.4 표 기준 15파일). 텔레그램은 P2 에 shim 으로 발송기 7벌을 다 바꾸므로 실행되는 코드에서는 0 이 되고, 실행되지 않는 워크플로 사본 3줄만 기준선에 남는다. 나머지(ECOS·data.go.kr·KOSIS·네이버·KRX 스크랩·KIS WS)는 해당 영역 단계에서 0.

### 9.4 지금 위반 (원본 스냅샷 스캔, 시험·문서 제외, 2026-10-06)

legacy 로 옮길 범위(SD 전체, ET `board`·`monitor/kr`·`monitor/flow`·`flowlab`·`etf_tracker_v9`·`dart-report`·워크플로 사본, GX 전체)를 §9.1 패턴으로 센 값 **[이식 후 경로로 다시 셀 것]**:

| 그룹 | 파일 | 줄 | 파일 목록 |
|---|---|---|---|
| `kis_oauth` | 4 | 7 | ET `board/ingest/kis.py`, GX `data/kis/auth_client.py`(승격), GX `services/auth/service.py`(승격), SD `kis_api.py` |
| `kis_rest` | 3 | 5 | ET `board/ingest/kis.py`, GX `config/settings.py`, SD `kis_api.py` |
| `kis_master` | 3 | 3 | ET `board/tools/probe_kis_futures.py`(삭제), GX `scripts/probe_common.py`, SD `kis_api.py` |
| `krx_api` | 4 | 5 | ET `board/ingest/krx.py`, ET `flowlab/probe_market_official.py`, GX `config/settings.py`, SD `krx_api.py` |
| `dart` | 7 | 13 | ET `board/ingest/dart.py`, ET `board/ingest/financials.py`(:216 referer), ET `dart-report/dartreport/client.py`, SD `dart_collector.py`, SD `earnings_parser.py`, SD `overhang_parser.py`(2), SD `server.py`(6: :10310·:10472·:10559·:10590·:10609·:11182) |
| `telegram` | 10 | 14 | ET 워크플로 사본 `daily.yml`·`pages.yml`(주석)·`preview.yml`, ET `board/ingest/tg_inbox.py`·`triggers.py`·`report/telegram.py`, ET `etf_tracker_v9/tracker.py`(4), ET `monitor/flow/telegram.py`, SD `earnings_telegram_sender.py`, SD `server.py`(2) |
| `datago` | 3 | 4 | ET `board/ingest/datago.py`, `flowlab/probe_market_official.py`(2), `flowlab/probe_market_unit2.py` |
| `ecos`·`kosis` | 0 | 0 | (bok 미이식) |
| `kis_ws` | 1 | 4 | GX `config/kis_ws.yaml` |
| `naver` | 41 | 179 | SD 15파일(`server.py` 32줄 등), ET 26파일 |
| `krx_scrape` | 13 | 84 | SD `server.py`(35)·`data_fetcher.py`(12) 등, ET `monitor/flow/krx.py`·`flowlab/krx.py` 등 |

목표 0 그룹을 0 으로 만드는 legacy 수정(H): SD `kis_api.py`·`krx_api.py`·`dart_collector.py`(`DART_BASE_URL`:42, `_fetch_dart_json`:154 → `await asyncio.to_thread(dart_bridge…)`)·`earnings_parser.py`:81·`overhang_parser.py`:95·137·`server.py`(DART 6곳), ET `board/ingest/kis.py`·`krx.py`(`BASE`:26 → `"krx:"`, 세션 → 브리지)·`dart.py`(`BASE`:27 → `"dart:"`)·`financials.py`(:216 referer 삭제, `fetch_batch`:165 는 `dart.BASE` 를 따라감)·`dart-report/dartreport/client.py`(kbj `DartClient` 다시 내보내기 + 옛 생성자 호환 — `run.py:58`·`app.py:69`), ET `flowlab/probe_market_official.py`(KRX 조회 진단 — 삭제 [제안]), ET `board/tools/probe_kis_futures.py`·`dashboard_brief_preview.py` 삭제, GX `config/settings.py`·`scripts/probe_common.py`·`probe_chain_fill.py`·`probe_all.py`·`services/scheduler/service.py:104~130`·`services/scheduler/minute.py:323` + 승격 모듈 자리의 다시 내보내기.

### 9.5 AST 규칙 (문자열 우회 방지)

| 규칙 | 대상 | 이유 |
|---|---|---|
| legacy 는 `kbj.services.auth`(발급자)를 import 하지 않는다 | `legacy/**/*.py` | 계약 ④는 kbj 안만 본다(legacy 는 import-linter 루트가 아니다) |
| legacy 는 kbj 의 `_` 로 시작하는 이름(비공개 주소 상수 `_REAL_BASE`·`_KRX_BASE` 등)을 import 하지 않는다 | 같음 | 상수를 가져와 `requests.get(상수 + 경로)` 로 문자열 검사를 피하는 것을 막는다 |
| legacy 가 import 할 수 있는 kbj 모듈 허용 목록: `kbj.data.legacy_bridge`, `kbj.services.notifier.client`, `kbj.services.notifier.inbox`(read_items), `kbj.core.*`, `kbj.config.settings`, `kbj.data.private.kis.{token,rest,master,credentials}`·`kbj.data.private.krx.{client,models,stocks}`·`kbj.data.public.dart.*`·`kbj.data.ratelimit`·`kbj.data.budget`·`kbj.data.http`(GX·ET 다시 내보내기 자리), `kbj.services.runtime.*`, `kbj.store.spool` | 같음 | 새 직접 경로가 생기지 않게 |

### 9.6 import-linter 계약 추가 (`pyproject.toml`)

| # | 이름 | 종류 | 내용 |
|---|---|---|---|
| ① | (있음) 공개 수집기는 로그인 수집기를 import 하지 않는다 | forbidden | 그대로 |
| ② | (있음) kbj 는 legacy 를 import 하지 않는다 | forbidden | 그대로 |
| ③ | (있음) core 는 순수 계산 | forbidden | 그대로(calendar 의 yaml·exchange_calendars 는 허용 목록 밖이 아니다) |
| ④ | **KIS 발급(kbj.services.auth)은 다른 모듈이 import 하지 않는다** | forbidden | source: `kbj.core`, `kbj.config`, `kbj.data`, `kbj.store`, `kbj.engines`, `kbj.reports`, `kbj.services.api`, `kbj.services.collectors`, `kbj.services.engine`, `kbj.services.notifier`, `kbj.services.scheduler`, `kbj.services.ws_gateway`, `kbj.services.runtime` → forbidden: `kbj.services.auth` |
| ⑤ | **텔레그램 HTTP 는 notifier 안에서만** | forbidden | source: ④와 같되 `kbj.services.notifier` 대신 `kbj.services.auth` → forbidden: `kbj.services.notifier.telegram_api` |
| ⑥ | **어댑터는 서비스·엔진을 모른다** | forbidden | source: `kbj.data` → forbidden: `kbj.services`, `kbj.engines`, `kbj.reports` |
| ⑦ | **저장소는 어댑터·서비스를 모른다** | forbidden | source: `kbj.store` → forbidden: `kbj.data`, `kbj.services`, `kbj.engines` |

- `tests/test_import_contracts.py` 의 위반 심기 시험은 지금 `"Contracts: 2 kept, 1 broken."`(계약 3개)을 기대한다 — 계약이 7개가 되면 `"6 kept, 1 broken."` 으로 고치고 ④~⑦ 각각에 위반 하나씩 심은 경우를 더한다(I 가 계약을 더할 때 같이).
- `RUNTIME_IMPORTS` 에 새 런타임 의존성(`exchange_calendars`, `defusedxml`)을 더한다(S0). `fastapi`·`uvicorn` 은 P3(결정 D4).
- 계약 소스 모듈이 아직 없으면 import-linter 가 실패할 수 있다 → ④~⑦ 은 A·E·F 가 들어온 뒤 I 가 한꺼번에 넣었다(R14 닫힘 — 7 kept). **구현 메모**: ⑤ 는 간접 import 도 잡는다(`allow_indirect_imports` 없음) — 예외는 `Attachment` 형 import 두 줄(`notifier.client`·`notifier.outbox` → `telegram_api`, `ignore_imports`). 묶음 E 가 `Attachment` 를 HTTP 없는 모듈로 옮기면 두 줄을 지운다. 위반 심기 시험은 19개(`tests/test_import_contracts.py`).

### 9.7 출력·종료 코드·CI

- 출력: `경로:줄: 그룹 — 설명`(찾은 문자열은 찍지 않는다 — 주소에 키가 섞일 수 있다), 끝에 그룹별 건수 표.
- 종료 코드: 0 통과, 1 위반, 2 사용법·기준선 형식 오류.
- 옵션: `--groups kis_oauth,kis_rest,…`, `--update-baseline`, `--list`.
- CI: 루트 잡(ruff·pyright·pytest·lint-imports)과 같은 잡에서 `uv run python scripts/check_canonical.py`.

### 9.8 시험 (`tests/test_check_canonical.py`)

임시 트리에 위반을 심어: 목표 0 그룹 legacy 위반 → 1, kbj 허용 위치 밖 → 1, 허용 위치 안 → 0, 기준선 건수 증가 → 1, 감소했는데 기준선 그대로 → 1, `--update-baseline` 이 늘리지 않음, 시험·문서 제외, AST 규칙(비공개 이름 import, auth import), 출력에 매칭 문자열 없음.

---

## 10. 하루 운영 시뮬레이션 (`tests/sim`)

### 10.1 구성

| 부품 | 무엇 | 출처 |
|---|---|---|
| 시계 | `FakeClock` 하나 — 리미터·auth·스케줄러·notifier·가짜 서버가 모두 본다 | GX `tests/fakes/kis_server.py:108` |
| Redis | fakeredis(Lua) 서버 하나 | GX 시험 방식 |
| DB | `MemoryClaimStore`·메모리 기록 저장소(단위 실행) / 통합 변형은 Timescale 컨테이너(`@pytest.mark.integration`) | 신규 |
| auth | `AuthService.step` 을 30초마다(가짜 시계) | §1.2 |
| scheduler | `SchedulerService.step` — 등록부 전체, `run_planned=True`: 아직 구현이 없는 작업은 **시뮬레이션 처리기**가 맡는다(선언한 데이터 키마다 카탈로그의 어댑터 호출을 한 번 — KIS·KRX·DART·ECOS·KOSIS·DATAGO 는 실제 kbj 어댑터 + 가짜 서버, 그 밖 출처(NASDAQ·YAHOO·FRED·ETF·ANTHROPIC)는 호출을 기록만 하는 가짜) | §6 |
| notifier | outbox 소비 + 가짜 텔레그램 | §5 |
| external 작업 | GX 스케줄러 단계(분봉·KRX 파생·마스터)는 legacy 를 부르지 않고 시뮬레이션 처리기로 같은 데이터 키·같은 리미터·같은 토큰 읽기를 흉내 | D-P2-5 |
| 웹소켓 | `FakeWsServer`(GX `tests/fakes/ws_server.py`)에 접속키를 읽어 등록·해지 한 번(주간 08:44·야간 17:59) — 접속키 발급이 auth 밖에서 일어나지 않는지 | GX |

### 10.2 가짜 서버

| 가짜 | 바탕 | 더할 것 |
|---|---|---|
| `FakeKisServer` | GX(`token_posts`:398, `bearers`, `calls`, `max_in_window`:489, 오류 주입 `EGW00201`·`EGW00123`) | 주식 TR 4종 합성 응답(합성 종목 20개), `/oauth2/Approval` 경로·`approval_posts` |
| `FakeKrx` | GX(`publish`, `requested()`, `stale`, `fail`) | 주식·지수·ETP 엔드포인트, `publish_at(day, when)`(D+1 08:00 공표 — 시계 기준) |
| `FakeWsServer` | GX 그대로 | — |
| `FakeDart` | 신규 | `list.json`(분마다 합성 공시 0~2건), `corpCode.xml` 합성 zip, `020` 주입 |
| `FakeDatago` | 신규 | 관세청 XML·금투협 JSON, GW `22`·`23` 주입 |
| `FakeEcos`·`FakeKosis` | 신규 | `INFO-200`, `602` 주입 |
| `FakeTelegram` | 신규 | sendMessage·sendDocument·sendMediaGroup 기록, 429 주입 |

### 10.3 시계 진행

- **창: 2026-10-06(화) 05:00 KST ~ 2026-10-07(수) 05:00 KST (24시간 — [결정 D3])**. 토큰 수명 24시간·만료 60분 전 갱신이라, 05:00 에 빈 Redis 에서 시작하면 첫 발급 05:00, 다음 갱신은 다음 날 04:00 이다 — 창 안 발급은 2회(간격 23시간)이고, 단언은 "모든 발급은 auth 에서만·auth 밖 발급 0·**어떤 23시간 구간에도 접근토큰 발급 ≤ 1**"이다. 23시간 창 시험은 따로 두지 않는다(설계 원안 D-P2-3 의 23시간 창은 결정 D3 로 바뀌었다). 이 날을 고른 이유: 전날 10-05 가 대체공휴일이라 `prev_trading_day` = 10-02(금), KRX 는 10-02 분을 10-06 08:00 에 공표(conflict_map E1 의 10/5 기록과 맞다), 간밤 미국(10-05 월) 장 마감 16:10 ET = 10-06 05:10 KST 가 창 안이다.
- 보폭(구현): 세션 전이 둘레 ±2분은 1초, 그 밖은 **30초**(auth step 과 같은 간격 — 설계 원안의 '작업 창 ±2분 1초·그 밖 60초' 대신). 작업 발화는 실행기가 `(지난 tick, now]` 구간의 시각을 모두 보므로 30초 보폭에서도 빠지지 않고, DART 매분 폴링도 분마다 한 번 돈다(`tests/sim/harness.py` `STEP_S`·`FINE_S`·`FINE_AROUND_S`).

### 10.4 단언

| # | 단언 | 확인 방법 |
|---|---|---|
| 1 | **접근토큰 발급은 auth 에서만, 어떤 23시간 구간에도 ≤ 1**([결정 D3]) | `FakeKisServer.posts(TOKEN_PATH)` = auth 의 05:00:00·다음 날 04:00:00 두 건(간격 ≥ 23시간), auth 밖 소비자(scheduler·gx·legacy)의 발급 요청 0 |
| 2 | 접속키 발급은 11시간 규칙대로 3회(05:00·16:00·03:00) | `approval_posts == 3` [가정: 12시간 수명] |
| 3 | 모든 KIS REST 요청이 그 토큰 하나를 실었다 | `set(bearers) == {"Bearer <가짜 토큰>"}` |
| 4 | 앱키 리미터: 어떤 1초 창에도 KIS 요청 4건 이하 | `max_in_window(1.0) <= 4` |
| 5 | **같은 데이터 키 성공 수집 1회** | `ops.data_claim` 에서 `(source, dataset, as_of)` 마다 `done` 1, 선점 충돌 0 |
| 6 | 가짜 서버 요청 목록과 대조 | KRX `requested()` 의 `(엔드포인트, basDd)` 각 1회(공표 지연 변형에서는 '빈 응답 재시도'만 추가), DART `list.json` 은 07:00~19:59 분마다 1회, KIS 종목 TR 은 `(tr_id, 종목, 날짜)` 각 1회 |
| 7 | 한 데이터셋을 두 작업이 받지 않았다 | `data_claim` 의 `(source, dataset)` 별 `job` 이 하나 |
| 8 | 일 예산 안 | KRX `krx:calls:20261006` ≤ 상한, DART·DATAGO 예산 ≤ 상한 |
| 9 | **U2** | `ops.notify_log` 에 `brief.morning` 1건(08:10), `brief.closing` 1건(16:40), 둘 다 `suppressed`(꺼짐 기본) |
| 10 | 꺼짐 모드 | `FakeTelegram` 요청 0 |
| 11 | 세션 전이 | `session.events` 가 IDLE→PRE_DAY→DAY→POST_DAY→PRE_NIGHT→NIGHT 순서, NIGHT 귀속 10-07(GX `test_scheduler_service.py:184` 와 같은 기대) |
| 12 | 실행 기록 | `enabled`·시뮬레이션 작업마다 `ops.job_run` ok, 마감 초과 0 |

### 10.5 변형

| 시험 | 내용 | 기대 |
|---|---|---|
| `test_holiday.py` | 창 2026-10-05(월, 대체공휴일) 05:00 ~ 10-06 05:00(24시간 — 결정 D3) | 거래일 작업 0회, `brief.morning`·`brief.closing` 0건(전날 밤 미국 = 일요일 → `after_us_session` 거짓), 야간장 없음, 토큰 발급은 auth 에서만(05:00·다음 날 04:00 — 23시간 구간마다 ≤ 1) |
| `test_notify_on.py` | `KBJ_NOTIFY_ENABLED=true` | 텔레그램 sendMessage 가 브리핑 2건 + 알림 종류별 건수와 같다, 429 주입 시 재시도 후 1회 |
| `test_krx_late.py` | KRX 공표 08:20 | 08:05·08:15 빈 응답 → 08:25 성공, 성공 수집 1회, 예산 = 3 × 엔드포인트 수 |
| `test_token_rejected.py` | 14:00 에 KIS 가 `EGW00123` 한 번 | 재발급 1회(신고 처리 1번만), 그 뒤 발급 10분 안 거절 반복 → 추가 발급 0 + critical `token_rejected_after_issue`(§3.4 가드). 지난 토큰의 늦은 신고가 현재 값의 신고를 덮지 않는다(최종 점검에서 고친 결함 — ADR 0004 §4.3-5) |
| `test_redis_blip.py` | 12:00~12:01 Redis 접속 실패 | auth 발급 안 함, 수집 작업은 실패 기록 후 재시도, 데이터 키 중복 0 |
| `test_restart.py` | 15:50 스케줄러 재시작 | 같은 `(job, as_of)` 재실행 없음(`ops.job_run`·선점이 막음), `brief.closing` 1건 |

---

## 11. 구현 순서와 병렬화

### 11.1 웨이브

```
웨이브 0 (순차, 1명)   S0 공유 기반 ── 끝나야 모두 시작
                         │
웨이브 1 (병렬)        A auth·KIS ─┐   B 캘린더 ─┐   C 공개 어댑터 ─┐   D 로그인 어댑터(KRX 먼저, fsc 는 C 의 datago.py 뒤)
                       E notifier ─┤   G DB ─────┤                  │
                                   │             │                  │
웨이브 2 (병렬)        F scheduler·등록부(B·C·G 뒤) ──┐   H legacy 재배선(A·C·D·E 뒤 + P1 이식 완료 뒤)
                                                     │
웨이브 3 (순차)        I 검사·계약·시뮬레이션·compose (전부 뒤)
```

### 11.2 묶음별 파일 (겹치지 않는다)

| 묶음 | 만드는·고치는 파일 | 읽기만(의존) |
|---|---|---|
| **S0** 공유 기반 | `pyproject.toml`(의존성 `exchange-calendars>=4.13.2`·`defusedxml` — `fastapi`·`uvicorn` 은 결정 D4 로 P3, pytest 마커 `integration`·`network`·`sim`, `addopts` `--import-mode=importlib`), `uv.lock`, `kbj/config/settings.py`(`config_dir: Path = Path("config")`, `service: str \| None = None`), `kbj/config/files.py`, `.env.example`(`KBJ_SERVICE`·`KBJ_CONFIG_DIR`), `docs/secrets.md`(새 이름 + §3.6 결정 반영), `config/limits.yaml`, `config/notify.yaml`(틀), `config/jobs.yaml`(틀 — `version`·`defaults` 만), `kbj/store/redis_keys.py`, `kbj/data/ratelimit.py`, `kbj/data/budget.py`, `kbj/data/http.py`, `kbj/data/spec.py`, `kbj/services/runtime/*.py`, `tests/conftest.py`(마커 별칭 — GX 방식), `tests/fakes/__init__.py`, `tests/fakes/clock.py`, `tests/test_import_contracts.py`(`RUNTIME_IMPORTS` 만), `tests/unit/data/test_ratelimit.py`, `tests/property/test_ratelimit_properties.py`, `tests/unit/data/test_budget.py`, `tests/unit/data/test_http.py`, `tests/unit/data/test_spec.py`, `tests/unit/store/test_redis_keys.py`, `tests/unit/runtime/test_runtime.py`, `tests/unit/config/test_config_files.py` | GX `data/kis/ratelimit.py`, `services/runtime.py`, `services/auth/health.py`, ET `board/ingest/http.py` |
| **A** auth·KIS | `kbj/data/private/kis/{__init__,credentials,token,rest,master,datasets}.py`, `kbj/services/auth/{__init__,issuer,service,__main__}.py`, `tests/unit/kis/{conftest,test_kis_credentials,test_token_cache,test_reader_never_issues,test_invalidate_guard,test_kis_rest,test_kis_master,test_master_download}.py`, `tests/unit/auth/{test_issuer,test_auth_service,test_auth_guard,test_owner_compat}.py`, `tests/fixtures/synthetic/kis/master_lines.json` | S0, B(`calendar_tagger` — `__main__` 만) |
| **B** 캘린더 | `kbj/core/calendar.py`, `kbj/core/time.py`, `kbj/core/calendar_compat.py`, `config/holidays_override.yaml`, `tests/unit/core/{test_calendar,test_xkrx_dependency,test_xnys_calendar,test_equity_hours,test_calendar_compat,test_legacy_holiday_parity,test_time}.py`, `tests/property/test_calendar_properties.py` | S0 |
| **C** 공개 어댑터 | `kbj/data/datago.py`, `kbj/data/public/dart/{__init__,client,corp_code,disclosures,datasets}.py`, `kbj/data/public/ecos/{__init__,client,datasets}.py`, `kbj/data/public/kosis/{__init__,client,datasets}.py`, `kbj/data/public/customs/{__init__,client,models,datasets}.py`, `kbj/data/public/fsc_kofia_stats/{__init__,client,datasets}.py`, `tests/unit/data/public/test_*.py`, `tests/unit/data/test_datago_transport.py`, `tests/fixtures/public/{dart,ecos}/…`(공개 등급 실데이터 — 키가 생긴 뒤), `tests/fixtures/synthetic/datago/…` | S0 |
| **D** 로그인 어댑터 | `kbj/data/private/krx/{__init__,client,stocks,models,datasets}.py`, `kbj/data/private/fsc_stock_price/{__init__,client,datasets}.py`, `kbj/data/private/fsc_index_price/{__init__,client,datasets}.py`, `kbj/data/private/ecos_restricted.py`, `tests/unit/data/private/test_*.py`, `tests/fixtures/synthetic/krx/*.json` | S0, C(`kbj/data/datago.py`·`kbj/data/public/ecos`) |
| **E** notifier | `kbj/services/notifier/{__init__,telegram_api,format,policy,outbox,client,service,webhook,commands,inbox,store,__main__}.py`, `config/notify.yaml`(내용 — S0 틀 위에), `tests/unit/notifier/test_*.py` | S0, G(DDL — 표 이름만 맞추면 병렬 가능) |
| **G** DB | `kbj/store/migrate.py`, `kbj/store/db.py`, `kbj/store/spool.py`, `kbj/store/migrations/0002_ops_core.sql`~`0006_gex.sql`, `kbj/store/legacy_import/{__init__,__main__,mappings,transforms,sources,verify}.py`, `tests/test_store_layout.py`(확장), `tests/unit/store/{test_migrate_unit,test_spool,test_import_transforms,test_import_sqlite}.py`, `tests/integration/{test_migrations_pg,test_import_pg}.py`, `tests/fixtures/synthetic/sqlite/README`(합성 DB 는 시험이 만든다 — `*.db` 는 gitignore) | S0 |
| **F** scheduler·등록부 | `kbj/core/cron.py`, `kbj/services/scheduler/{__init__,registry,conditions,claims,runner,handlers,service,__main__}.py`, `kbj/data/catalog.py`, `kbj/services/collectors/{__init__,corp_code,ops_watchdog}.py`, `kbj/services/ops/{__init__,nightly}.py`, `config/jobs.yaml`(전체), `tests/unit/scheduler/{test_cron,test_registry_validation,test_conditions,test_claims,test_runner,test_session_publish,legacy_jobs.txt}.py·txt`, `tests/property/test_cron_properties.py`, `tests/unit/collectors/test_corp_code.py` | S0, B, C(`dart`·datasets), D(datasets), A(datasets), G(`ops.*` 표), E(`client.notify`) |
| **H** legacy 재배선 | `kbj/data/legacy_bridge.py`, `tests/unit/data/test_legacy_bridge.py` + legacy: `legacy/stock_dashboard/{kis_api,krx_api,dart_collector,earnings_parser,overhang_parser,earnings_telegram_sender,server}.py`, `legacy/etf_traker/board/ingest/{kis,krx,dart,financials,http,tg_inbox,triggers}.py`, `legacy/etf_traker/board/report/telegram.py`, `legacy/etf_traker/board/run.py`(`cmd_send` 의 `kind`), `legacy/etf_traker/board/tools/{dashboard_brief_preview,probe_kis_futures}.py`(삭제), 워크플로 사본 2개(삭제 — 확인 뒤), `legacy/etf_traker/etf_tracker_v9/tracker.py`, `legacy/etf_traker/monitor/flow/{telegram,kissrc,narrative}.py`, `legacy/etf_traker/dart-report/dartreport/client.py`, `legacy/etf_traker/flowlab/probe_market_official.py`(삭제 [제안]), `legacy/gexlab/config/settings.py`, `legacy/gexlab/core/calendar.py`, `legacy/gexlab/data/kis/{auth_client,rest,ratelimit,master}.py`, `legacy/gexlab/data/krx/{eod,models}.py`, `legacy/gexlab/services/auth/{__init__,service,health,__main__}.py`, `legacy/gexlab/services/runtime.py`(일부), `legacy/gexlab/services/scheduler/{service,minute}.py`(일부), `legacy/gexlab/scripts/{probe_common,probe_chain_fill,probe_all}.py`, legacy 시험 중 승격분 삭제(`legacy/gexlab/tests/unit/test_{calendar,ratelimit,auth_client,auth_service,kis_rest,kis_master,krx_client,krx_models}.py`, `tests/property/test_{calendar,ratelimit}_properties.py`, ET `board/tests/test_tg_inbox.py` 일부, `test_telegram.py`·`test_send_files.py`·`test_http_reason.py` 일부), 프로젝트별 다리 시험 1개씩 | A, B, C, D, E, P1 이식 완료 |
| **I** 검사·통합 | `scripts/check_canonical.py`, `scripts/canonical_baseline.txt`, `tests/test_check_canonical.py`, `pyproject.toml`(**import-linter 계약 ④~⑦ 만**), `tests/test_import_contracts.py`(계약 수·위반 심기 경우), `docker-compose.yml`(`migrate`·`auth`·`scheduler`·`notifier` 서비스 — 앱키는 §3.6 결정대로), `tests/test_store_layout.py` 의 compose 시험(서비스 추가분 — G 가 끝난 뒤), `tests/fakes/{kis_server,krx_server,ws_server,dart_server,datago_server,ecos_server,kosis_server,telegram_server}.py`, `tests/fixtures/synthetic/kis/*.json`(가짜 서버용), `tests/sim/{conftest,test_one_day,test_holiday,test_notify_on,test_krx_late,test_token_rejected,test_redis_blip,test_restart}.py`, `README.md` 상태 줄 | 전부 |

### 11.3 공유 파일 — 누가 언제

| 파일 | 고치는 묶음·시점 | 다른 묶음 |
|---|---|---|
| `pyproject.toml` | S0(의존성·마커·addopts) → I(import-linter ④~⑦) | 그 사이에는 아무도 고치지 않는다. 새 의존성이 필요하면 S0 담당에게 요청해 한 커밋으로 |
| `uv.lock` | S0 만(I 가 의존성을 바꾸지 않으므로) | — |
| `kbj/config/settings.py` | S0(`config_dir`·`service`) | 그 뒤 필드가 더 필요하면 S0 담당이 한 번에 |
| `.env.example`·`docs/secrets.md` | S0 | — |
| `config/jobs.yaml` | S0(틀) → F(전체) | 다른 묶음은 자기 작업 행을 F 담당에게 PR 설명으로 넘긴다 |
| `config/notify.yaml` | S0(틀) → E(내용) | H 의 `legacy_kinds` 는 E 가 받아 적는다 |
| `config/limits.yaml` | S0(§4.3 값) | A·C·D 는 읽기만 |
| `config/holidays_override.yaml` | B | — |
| `kbj/store/redis_keys.py` | S0(모든 키 미리) | 키가 더 필요하면 S0 담당에게 |
| `tests/test_import_contracts.py` | S0(`RUNTIME_IMPORTS`) → I(계약 수) | — |
| `tests/test_store_layout.py` | G(마이그레이션) → I(compose) | — |
| `docker-compose.yml` | I | — |
| `tests/conftest.py`·`tests/fakes/__init__.py`·`tests/fakes/clock.py` | S0 | — |
| `kbj/data/catalog.py` | F(각 어댑터 `datasets.py` 를 모은다) | A·C·D 는 자기 `datasets.py` 만 |

### 11.4 묶음별 완료 확인 명령

공통(모든 묶음 끝): `uv run ruff check && uv run ruff format --check && uv run pyright && uv run lint-imports && uv run python scripts/check_public_safety.py --exclude legacy`

| 묶음 | 추가 확인 |
|---|---|
| S0 | `uv lock && uv sync --frozen && uv run pytest -q` (기존 시험 + `tests/unit/data`·`property`·`runtime`·`config` 녹색) |
| A | `uv run pytest tests/unit/kis tests/unit/auth -q` · `grep -rnE "oauth2/(tokenP\|Approval)" kbj --include=*.py \| grep -v "^kbj/services/auth/"` 가 빈 결과 |
| B | `uv run pytest tests/unit/core tests/property/test_calendar_properties.py -q` · `uv run pyright kbj/core`(strict) |
| C | `uv run pytest tests/unit/data/public tests/unit/data/test_datago_transport.py -q` · `uv run lint-imports`(계약 ①) |
| D | `uv run pytest tests/unit/data/private -q` |
| E | `uv run pytest tests/unit/notifier -q` · `grep -rn "api.telegram.org" kbj \| grep -v telegram_api.py` 빈 결과 |
| G | `uv run pytest tests/unit/store tests/test_store_layout.py -q` · (Docker) `uv run pytest -m integration tests/integration/test_migrations_pg.py tests/integration/test_import_pg.py` · `uv run python -m kbj.store.legacy_import --dry-run --phase P2 --source board=<합성 board.db>` |
| F | `uv run pytest tests/unit/scheduler tests/unit/collectors tests/property/test_cron_properties.py -q` · `uv run python -m kbj.services.scheduler validate config/jobs.yaml` 종료 0 |
| H | legacy 프로젝트별 원래 명령(ADR 0003-2, conflict_map §3.2): GX `pytest -m "not network"`, ET board `python -m unittest discover -s board/tests -t .`, kr·flow·flowlab, dart-report smoke, SD 검사 스크립트 — 각 프로젝트 루트에서 루트 venv 로(`kbj` 가 설치돼 있어야 한다) · `uv run python scripts/check_canonical.py --groups kis_oauth,kis_rest,kis_master,krx_api,dart` 0건(I 전이면 같은 패턴 grep) |
| I | `uv run python scripts/check_canonical.py` 종료 0 · `uv run pytest tests/sim -q` · `uv run pytest -m "not integration and not network" -q` · (Docker) `uv run pytest -m integration` · `docker compose --env-file <임시 값 파일> config -q` |

### 11.5 legacy 시험 수 변화 (예상)

| 프로젝트 | P1 기준(conflict_map §3.2) | P2 뒤 legacy(**실측 2026-10-07**) | 빠진 것(시험 ID 대조 — 최종 점검) |
|---|---|---|---|
| GX(`not network and not integration`) | 3,177 | **2,716** | 462 항목 빠짐 + 다리 시험 1. 빠진 462 중 439 는 같은 이름으로 kbj 에 있다(calendar 137·속성 7·XKRX 7·ratelimit 44·속성 3·auth_client·auth_service·kis_rest·kis_master 39·krx 11+38·spool 36·마스터 내려받기 3·태거 6). 22 는 기능과 함께 폐지(파일 토큰 캐시·폴백 — 함수 10 + `file` 매개변수 12, 설계 §1.2·K7), 1 은 매개변수(`auth` 싱크 — auth 진입점이 KBJ 로). `legacy/gexlab/MIGRATION.md` P2.3 |
| ET board | 1,319 | **1,267**(건너뜀 1 그대로) | 55 ID 빠짐(telegram 12·send_files 2·http_reason 10·tg_inbox 31) + 3 더함(다리 1·인박스 Drain 2). 빠진 55 중 47 은 kbj 에 있고 8 은 getUpdates·offset 경로(§5.8 폐지). ET `test_dart_for` 7 은 legacy 에 남겼다(ET `disclosures_for` 본문 유지 — kbj 쪽 승격본과 별개). `legacy/etf_traker/board/MIGRATION.md` P2 |
| ET kr·flow·flowlab | 94·129·44 | 94·129·44/44 | — |
| SD 검사 | 12(래퍼) | **13** | 다리 시험 +1 |

kbj 쪽 시험은 1,826 수집(1,804 통과·22 건너뜀 — 통합 21 은 Docker 필요) — 승격분 + 새 시험.

---

## 12. 위험과 [확인 필요]

| # | 위험·미정 | 영향 | 대응·기본값 |
|---|---|---|---|
| R1 | ⚠ 앱키가 REST 헤더에 필요 — "auth 에만 주입"과 충돌(§3.6) | 비밀 노출 면적 | **닫힘 — 결정 D1(안 A, ADR 0004)**: 앱키는 KIS REST 를 부르는 프로세스에도, 발급은 세 겹(issuer.py 에만 + 계약 ④ / `oauth2` 경로 문자열 검사 / `KBJ_SERVICE != auth` 면 발급자 생성 거부). secrets.md §1·§4 고침. compose 는 P2 에 auth 에만 넣었다(P3 에 scheduler 추가) |
| R2 | KIS 웹소켓은 P7 까지 legacy(D-P2-4) | PLAN 완료 기준 해석 | **닫힘 — 결정 D2**: 연결 코드는 P7 까지 legacy GX, `check_canonical` 기준선 그룹 `kis_ws`(지금 4줄 — `legacy/gexlab/config/kis_ws.yaml`). 접속키 발급은 auth 만(시뮬레이션: 05:00·16:00·03:00 모두 auth) |
| R3 | 거절 신고 폭주 → 재발급 반복(GX `discard`) | 1분 1회 제한·다른 발급자 무효화 | §3.4 가드 [제안] |
| R4 | 접근토큰·접속키가 KIS 의 1분 1회 제한을 함께 쓰는지 미실측 | 기동 직후 접속키 1회 지연 | GX 가 이미 정상으로 처리(서비스 머리말). 실측 후 기록 |
| R5 | 레포 밖 발급자 K10 | 매일 토큰 무효화 | 전환일 사용자 작업(Q13), `token_throttled` health 로 감지 |
| R6 | P1 이식 미완 — legacy 경로·줄 번호가 바뀔 수 있음 | H 일정 | H 는 P1 완료 뒤, 줄 번호는 함수 이름으로 다시 찾는다 |
| R7 | GX fixture 합성본(U3)이 늦으면 A·D·I 시험이 막힌다 | 일정 | **닫힘**: P1 합성본을 `tests/fixtures/synthetic/{kis,krx,datago}` 로 옮겨 썼다(A·D·I) |
| R8 | `exchange-calendars`(pandas)·FastAPI 를 런타임 의존성에 더함 | 이미지 크기, pandas 2.3.3 고정 | **닫힘 — 결정 D4**: FastAPI·uvicorn 은 P3 로 미룬다(의존성 없음). P2 웹훅은 순수 처리 함수 `WebhookHandler.handle(요청 dict)` 로 만들고 시험했다(`tests/unit/notifier/test_webhook.py`). `exchange-calendars` 는 S0 에서 lock |
| R9 | KRX 일 상한(GX 기본 200) | 주식·지수·ETP 추가로 모자람 | **결정 D5**: `config/limits.yaml` `krx.daily_cap` 기본 8,000 **[확인 필요]**(백필 5,000 분리), 실측 체크리스트 `probe_results.md` §7 #12 |
| R10 | DART 20,000/일·ECOS·KOSIS 일 한도 미확정 | 상한 값 | §4.2 기본값, 실측 체크리스트(`probe_results.md` §7)에 더함 |
| R11 | 네이버 출처 행을 버리면 일봉 이력이 대부분 사라짐(ET 일봉 주 경로가 네이버) | 신고가 보드 이력·백테스트 | `market.backfill` 로 KRX 재수집(5년 ≈ 2,500회, 며칠에 나눠) **[확인 필요: Q12 원본 사본·수집 일정]** |
| R12 | ET 워크플로 사본·도구 삭제가 ET 시험(`test_workflow_modes` 등)에 걸릴 수 있음 | ET 1,319 | **닫힘**: K2·K6 의 워크플로 사본은 P1 에서 옮기지 않았고(board MIGRATION), 도구 두 파일 삭제 뒤 board 1,267 통과(승격분만 빠짐) |
| R13 | SD 라우트 삭제가 SD 검사 스크립트(AST 로 함수 추출)에 걸릴 수 있음 | SD 9/10 | **닫힘**: SD 검사 래퍼 13/13 통과(P1 12 + 다리 시험 1) |
| R14 | import-linter 가 없는 소스 모듈을 어떻게 다루는지 | I 실패 | **닫힘**: 계약 ④~⑦ 을 I 가 마지막에 넣었다 — 7 kept, 0 broken. 위반 심기 시험 19개(`tests/test_import_contracts.py`) |
| R15 | 웹훅 전환 순서를 어기면(SD 부팅 setWebhook 이 살아 있음) 수신이 SD 로 되돌아감 | 명령·인박스 유실 | §5.11 순서, `webhook-info` 점검 |
| R16 | 토픽 thread id·슈퍼그룹 미생성 | 발송 위치 | `notify.yaml` 비워 두면 일반 대화로 보내고 경고 |
| R17 | 시뮬레이션 23시간 창(D-P2-3)을 PLAN 의 "하루"로 읽어도 되는지 | 완료 기준 해석 | **닫힘 — 결정 D3**: 24시간 창(10-06 05:00 ~ 10-07 05:00). 단언: 모든 발급은 auth 에서만, auth 밖 발급 0, 어떤 23시간 구간에도 접근토큰 발급 ≤ 1(05:00·다음 날 04:00). 23시간 창 시험은 따로 만들지 않는다 |
| R18 | Q1(당일 종가 KIS 대 KRX) 미결 | `market.close_collect` 데이터 키 | ADR 0001 기본값(KIS 16:40, 다음 날 KRX 대조)으로 등록 |
| R19 | legacy 가 kbj 를 import 하려면 legacy 시험을 루트 venv(kbj 설치)로 돌려야 함 | CI 잡 구성 | ADR 0003 의 프로젝트별 잡에서 `uv run --project <루트>` 로 [추정] |
| R20 | KIS 투자의견·추정실적 TR(`FHKST668300C0`·`FHKST663300C0`) 미실측 | 컨센서스 작업 | 등록만, P4 실측 뒤 데이터셋 확정(추정치로 채우지 않음) |
| R21 | 공개 실데이터 fixture(DART·ECOS·관세청)는 키가 있어야 녹화 가능 | C 시험이 합성으로 시작 | 키 발급 뒤 공개 fixture 로 바꾼다 |
| R22 | GX 운영 DB 에 보관할 녹화 데이터 존재 여부 | 0006 이관 필요성 | P7 에 `pg_dump --data-only` + `search_path` **[확인 필요]** |
| R23 | `ops.nightly` 백업 위치(VM 디스크·외부) 미정 | 백업 | P2 는 VM 로컬 + 보존 7일 [확인 필요] |
| R24 | 주식 지연 개장일(수능·연초) | 장중 작업 시각 | **닫힘 — 지연 개장 결정**: `holidays_override.yaml` `late_open:`(수능일 10:00~16:30) + 그해 첫 거래일 10:00 규칙 → `TradingCalendar.equity_bounds`, 등록부 `equity` 트리거가 따른다(§7.1). 남은 [확인 필요]: 2026-11-19 KRX 공지, 파생 세션 상태 머신의 수능일 지연 |

### 이 설계로 남긴 ADR (확정 — 2026-10-07)

- [ADR 0004](adr/0004-kis-issuance.md) — KIS 앱키 주입 범위와 발급 차단 세 겹(§3.5·§3.6, 결정 D1~D3).
- [ADR 0005](adr/0005-notifier-outbox.md) — 발송 outbox·중복 키·발송 기록 분리(`ops.notify_log` 메타 / `prv_alerts.notify_message` 본문), 쿨다운 미끄러지는 창·legacy 규칙(§5.2·§5.3·§5.9), 결정 D4.
- [ADR 0006](adr/0006-job-registry-claims.md) — 작업 등록부·논리 데이터셋·선점(claim)으로 중복 수집 금지, `runner: external`, §6.7 과 달라진 점.
- [ADR 0007](adr/0007-legacy-bridge-baseline.md) — legacy 논리 URL 브리지와 직접 호출 금지 검사의 기준선 정책.

---

## 부록 A. 이 설계가 읽은 근거

| 구분 | 경로 |
|---|---|
| KBJ 문서 | `CLAUDE.md`, `docs/PLAN.md` §2·§3·§8, `docs/DATA_TIERS.md`, `docs/conflict_map.md` §0·§1·§3·§4, `docs/inventory.md` (a)~(f), `docs/probe_results.md`, `docs/adr/0001~0003`, `docs/secrets.md` |
| KBJ 뼈대 | `pyproject.toml`, `kbj/config/settings.py`, `kbj/core/quality.py`, `kbj/core/masking.py`, `kbj/store/migrations/0001_schemas.sql`, `kbj/*/__init__.py`, `tests/test_import_contracts.py`, `tests/test_store_layout.py`, `tests/test_settings.py`, `scripts/check_public_safety.py`, `docker-compose.yml`, `.env.example`, `.gitignore` |
| GX | `services/auth/service.py`, `data/kis/{auth_client,rest,ratelimit,master}.py`, `core/calendar.py`, `config/{settings.py,holidays_override.yaml}`, `data/krx/eod.py`, `services/scheduler/{service,krx,minute}.py`, `services/{runtime,bus,healthcheck}.py`, `services/auth/health.py`, `services/poller/context.py`, `db/migrate.py`, `db/migrations/001~004`, `data/spool.py`, `data/store.py`(머리말), `scripts/{probe_all,probe_common}.py`, `docker-compose.yml`, `pyproject.toml`, `tests/fakes/{kis_server,krx_server,ws_server}.py`, `tests/conftest.py`, `tests/integration/test_full_day.py`, `tests/unit/test_{calendar,dependencies,ratelimit,auth_client,auth_service,kis_rest,kis_master,krx_client,krx_models,scheduler_service,scheduler_krx,services_runtime,repo_rules}.py` |
| ET | `board/ingest/{kis,krx,dart,datago,http,creds,tg_inbox,triggers,financials}.py`, `board/report/telegram.py`, `board/engine/db.py`, `board/us/db.py`, `board/run.py`(발송 함수), `board/guru/pipeline.py`, `board/xdigest/send.py`, `board/tests/{test_kis_call,test_telegram,test_send_files,test_tg_inbox,test_http_reason,test_dart_for}.py`, `etf_tracker_v9/{tracker,market}.py`, `monitor/flow/{telegram,kissrc}.py`, `dart-report/dartreport/client.py`, `dart-report/{tests_smoke,run,app}.py`, `.github/workflows/` 목록 |
| SD | `kis_api.py`, `krx_api.py`, `server.py`(발송·웹훅 :5205~5910, APScheduler :7072~7517, DART :10294~11182, 휴장 :17971~18030, 장중 :174), `earnings_telegram_sender.py`, `dart_collector.py`, `db/schema.sql`, `migrations/004·005` |
| 실측 | `/tmp/p0test/venv-312` 에서 XKRX 2026 세션과 SD 하드코딩 대조(§7.3), 원본 스냅샷 금지 문자열 스캔(§9.4) |
