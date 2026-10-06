# 인벤토리 — gexlab

- 대상: `/home/user/p0src/gexlab` (브랜치 `claude/phase1` 스냅샷, HEAD `43a9ed1`)
- 작성: 2026-10-06, 읽기 전용 조사. 키·토큰·계좌 값은 적지 않았다(환경변수 이름만).
- 표기: `파일:줄` 은 정의 위치. **[추정]** 은 코드를 끝까지 읽지 않고 문서·이름으로 판단한 것.
- 참고: `services/scheduler/service.py` 본문은 이번 조사에서 직접 열지 못했다(도구 권한 거부). 스케줄러 관련 내용은 `docs/phase1_design.md` §2·§8·§9와 하위 모듈(`services/scheduler/*.py`) 클래스 정의로 채웠다.

---

## 1. 개요

| 항목 | 내용 | 근거 |
|---|---|---|
| 무엇 | 코스피200 옵션 **GEX(감마 익스포저) 실시간 트래커** → 백테스트 → 시스템 트레이딩(선물) 계획 중 **Phase 0~3 까지 구현**. 지금은 KIS·KRX 수집·녹화와 지표 계산 엔진만 있고, 대시보드·알림·백테스트·주문은 빈 패키지 | `README.md`, `docs/PLAN.md` §12, `docs/phase3_design.md` 머리 |
| 진행 상태 | Phase 0(probe 실측) 완료, Phase 1(수집·녹화) 구현 완료·3거래일 소크 대기, Phase 2(코어 엔진) 판정 완료([확인 필요] 표시), Phase 3(확장 지표·기능 플래그) 구현 완료·1주 섀도 운영 대기. Phase 4~8은 설계 문서만 | `docs/PLAN.md` §12 하단, `docs/phase4_design.md`, `docs/phase5_8_design.md` |
| 파일 수 | 280개(.git 제외). Python 226개(테스트 외 115개 + 테스트 111개), SQL 마이그레이션 4개, YAML 설정 5개, 문서 10개 | `find` 결과 |
| 줄 수 | Python 전체 70,353줄. 테스트 37,277줄 / 그 외 33,076줄(core 4,789 · data 5,407 · services 14,468 · scripts 8,194 · db 153 · config 65). SQL 534줄, docs 3,015줄, config YAML 420줄 | `wc -l` |
| 테스트 수 | **`def test_` 1,574개**(정적 집계, 파라미터 확장 전). unit 68파일 1,404개 · property 12파일 58개 · golden 3파일 53개 · integration 12파일 50개 · `tests/test_sign_conventions.py` 9개. `@pytest.mark.parametrize` 211곳이라 실제 수집 건수는 더 많다. 이 환경에는 의존성이 설치돼 있지 않아 `pytest --collect-only` 는 돌리지 않았다 | `grep -c 'def test_'` |
| 언어 | Python 3.12 고정(`>=3.12,<3.13`), 패키지 관리 `uv`(uv.lock), 린트 `ruff`, 타입 `pyright`(core·backtest·exec는 strict) | `pyproject.toml` |
| 주요 의존성 | `httpx`(REST), `websockets`(KIS WS), `psycopg[binary]`(PostgreSQL/TimescaleDB), `redis`(pub/sub·토큰 캐시·레이트리미터), `pydantic`/`pydantic-settings`(입력 검증·설정), `vollib`(Black-76·IV), `exchange-calendars`(XKRX 휴장일), `pyyaml`. 개발: pytest, hypothesis, fakeredis[lua], pre-commit, pandas-stubs | `pyproject.toml` |
| 실행 방식 | **서버(Docker Compose 상주 서비스)**: redis, db(timescaledb pg16), migrate(1회), auth, scheduler, recorder, poller, ws-gateway, engine. 공개 포트 없음, `restart: always`, 하트비트 healthcheck. 각 서비스 `python -m services.<이름>` | `docker-compose.yml`, `Dockerfile` |
| CLI | `python -m scripts.probe_all`(KIS·KRX 실측), `scripts.metric_report`, `scripts.shadow_report`, `scripts.nogap_report`, `scripts.make_golden`, `scripts.validate_greeks`, `db.migrate` | `scripts/*.py`, `db/migrate.py` |
| GitHub Actions | `ci.yml`(push main·PR: ruff·format·pyright·pytest `-m "not network"`·Docker 빌드), `probe.yml`(수동 `workflow_dispatch` 전용 KIS·KRX probe) | `.github/workflows/` |
| 운영 환경 | 개발 맥북(arm64), 운영 서울 리전 리눅스 VM(amd64) 계획. 같은 이미지 | `Dockerfile` 주석, PLAN §0-9 |

---

## 2. 모듈 표

| 경로 | 역할 | 주요 함수·클래스 | 의존 외부 출처 |
|---|---|---|---|
| `config/settings.py` | 환경 설정(pydantic-settings, 비밀은 `SecretStr`). KIS 실전/모의 URL, KRX URL 상수 | `Settings`, `KIS_REAL_BASE`, `KIS_VTS_BASE`, `KRX_BASE` | — |
| `config/*.yaml` | 기능 플래그(`features.yaml`), 휴장일 덮어쓰기(`holidays_override.yaml`), KIS WS URL·응답 문구(`kis_ws.yaml`), WS TR 필드 순서(`kis_ws_fields.yaml`), WS 41건 예산(`ws_budget.yaml`) | — | KIS 공식 샘플에서 옮긴 필드 |
| `core/specs.py` | 상품 명세 단일 정의(승수·호가단위), Decimal 틱 반올림 | `Product`, `spec`, `round_to_tick`, `option_tick`, `ticks_between` | — |
| `core/calendar.py` | 거래일·휴장일·세션 상태 머신(PRE_DAY/DAY/POST_DAY/PRE_NIGHT/NIGHT/IDLE), 야간 귀속 T+1, 만기일, 분봉 시각 변환 | `TradingCalendar`, `state_at`, `night_session_opens`, `expiry_at`, `night_bar_time`, `monthly_expiry`, `weeklies_listed` | exchange_calendars XKRX |
| `core/chain.py` | 선물가 기준 ATM 행사가·창 | `atm_strike`, `atm_window`, `covers`, `by_distance` | — |
| `core/preprocess.py` | IV 역산용 가격 선택(mid/last), 품질 합성 | `select_price`, `worst`, `excluded_oi_ratio` | — |
| `core/forward.py` | 합성선물 F(C−P+K 중앙값), 선물 기준가·베이시스 확정·이월, 잔존기간 T | `synthetic_forward`, `futures_reference`, `confirm_basis`, `basis_age`, `select_s_ref`, `time_to_expiry`, `kis_time_to_expiry` | — |
| `core/black76.py`, `core/iv.py`, `core/greeks.py` | Black-76 가격, IV 역산(KIS IV 폴백), 그릭스·Vanna·Charm | `price`, `implied_vol`, `greeks`, `gamma`, `vanna`, `charm` | vollib |
| `core/gex.py` | 행사가별·만기별 GEX/DEX, 범위 선택(all·nearest·0dte) | `OptionQuote`, `evaluate_expiry`, `option_gex`, `option_dex`, `strike_gex`, `select_scope`, `to_eok` | — |
| `core/levels.py` | 콜월·풋월·절대감마·Flip·전환점 거리·0DTE 레벨·ATM IV·기대변동폭 | `call_wall`, `put_wall`, `abs_gamma_strike`, `gamma_flip`, `flip_distance_pct`, `zero_dte_levels`, `atm_iv` | — |
| `core/metrics/exposure.py` | VEX·CEX·GEX P/C | `vex`, `cex`, `gex_put_call_ratio` | — |
| `core/metrics/vol.py` | IV 기간구조·25Δ 스큐·IV 랭크/퍼센타일·실현변동성·IV−HV, KRX ATM IV | `term_structure`, `skew_25d`, `iv_rank`, `realized_vol`, `iv_minus_hv`, `krx_atm_iv` | — |
| `core/metrics/flow.py` | PCR, 맥스페인, OI 증감, HIRO-lite, 딜러 가정 점검 | `pcr`, `max_pain`, `oi_change_cells`, `hiro_step`, `dealer_check`, `mismatch_streak` | — |
| `core/metrics/futures.py` | 선물 베이시스·괴리율·OI 증감·체결강도 | `futures_metrics` | — |
| `core/features.py` | 기능 플래그 로더(off·shadow·visible), 파일 감시 재로드 | `CATALOG`, `load_features`, `FeatureWatcher`, `changed_flags` | — |
| `data/kis/auth_client.py` | KIS 접근토큰 발급·캐시(Redis/파일/폴백) | `KisTokenIssuer`(:165), `CachedTokenProvider`(:436), `RedisTokenCache`, `FileTokenCache`, `default_token_provider`(:539) | KIS `/oauth2/tokenP` |
| `data/kis/rest.py` | KIS REST 공용 클라이언트(레이트리미터·토큰 거절 재시도·raw 발행) | `KisClient`(:98), `KisResponse`, `minute_chart_params`, `redact` | KIS REST |
| `data/kis/ratelimit.py` | 앱키당 전역 레이트리미터(Redis Lua GCRA, 우선순위 P0~P4, EGW00201 감속) | `RedisRateLimiter`, `LocalRateLimiter`, `RateLimitConfig`, `Priority` | Redis |
| `data/kis/models.py` | KIS 응답 pydantic 모델(전광판·단건·투자자·분봉) | `CallPutRow`, `FuturesBoardRow`, `PriceOutput`, `InvestorRow`, `MinuteBar`, `parse_rows` | — |
| `data/kis/master.py` | 지수선물옵션 마스터(`fo_idx_code_mts.mst`) 파서 | `parse_master_zip`, `parse_master_line`, `rows_by_series` | KIS 마스터 zip |
| `data/kis/ws.py` | KIS WS 프레임 파서·체결 틱 모델 | `parse_frame`, `ticks_from`, `FuturesTick`, `OptionTick` | — |
| `data/krx/eod.py`, `data/krx/models.py` | KRX Open API 선물·옵션 일별 클라이언트·모델 | `KrxClient`, `fetch_daily`, `KrxFuturesDaily`, `KrxOptionDaily`, `parse_option_name` | KRX `/drv/fut_bydd_trd`, `/drv/opt_bydd_trd` |
| `data/store.py` | PostgreSQL 저장(표별 열 정의, 행 변환, DO UPDATE, engine 입력 읽기, 섀도 집계) | `Table`, `PostgresSink`(:1212), `raw_row`, `krx_option_row`, `MinuteBarRecord` | PostgreSQL |
| `data/spool.py` | DB 장애 시 로컬 디스크 큐(jsonl, fsync) 후 재적재 | `DiskSpool`, `encode_batch`, `decode_batch` | 로컬 디스크 |
| `db/migrate.py`, `db/migrations/*.sql` | 번호순 마이그레이션(체크섬), TimescaleDB hypertable | `migrate`, `apply`, `discover` | PostgreSQL |
| `services/bus.py` | Redis 채널·키 계약, 메시지 모델 | `ChainReady`, `EngineLatest`, `EngineLevels`, `SessionState`, `heartbeat_key` | Redis |
| `services/auth/` | 토큰·WS 접속키 발급/갱신 유일 주체 | `AuthService`(:186), `KisApprovalKeyIssuer`(:465), `build_auth_service`, `reader`(:564) | KIS `/oauth2/tokenP`, `/oauth2/Approval` |
| `services/scheduler/` | 상태 머신 구동, 마스터 다운로드, KRX 전일 적재, 분봉 적재, 개장 확인, 무결측 판정 | `KrxDaily`·`KrxLoader`·`KrxCallBudget`(krx.py), `MinuteDaily`·`MinuteLoader`(minute.py), `GapDaily`(gaps.py), `OpenCheck`(open_check.py), `MasterKrxReport`(master_check.py) | KIS 마스터 zip, KIS 분봉 REST, KRX |
| `services/poller/` | KIS REST 주기 수집(전광판·선물·투자자·월물리스트·단건 보강), `chain.ready` 발행 | `PollerService`, `Collector`, `plan`(planner.py), `endpoints.*`, `ChainContext`, `ReadyNotifier`, `ReadyTap` | KIS REST |
| `services/ws_gateway/` | KIS WS 단일 세션, 41건 예산, ATM 회전, 세션 전환, 닫힌 세션 감시 | `KisWsClient`, `SubscriptionController`, `derive`(budget.py), `ClosedWatch`, `GatewayWorker`, `run_gateway` | KIS WS `ws://ops.koreainvestment.com:21000` |
| `services/recorder/` | 원문(raw) 무가공 녹화 | `Recorder`, `RawEnvelope`, `Batcher` | Redis·DB |
| `services/engine/` | 지표 계산 사이클·일별·플로우·선물, 플래그·섀도, 결과 저장·발행 | `EngineService`, `evaluate_cycle`, `evaluate_daily`, `MetricPlugin`, `TickFlow`, `OiTracker`, `EnginePublisher` | DB·Redis만(KIS 안 부름) |
| `services/gaps.py`, `services/healthcheck.py`, `services/runtime.py`, `services/chain_feed.py` | 공백 계산(순수), compose 하트비트 검사, 공용 런타임, 마스터·체인 문맥 공유 | `MasterWatcher`, `ContextPublisher` | Redis |
| `scripts/probe_*.py` | Phase 0 실측(REST 한도, 전광판, 월물리스트, 야간, 분봉, 투자자, KRX) | `probe_all.main`, `Ctx`, `MASTER_URL` | KIS, KRX |
| `scripts/metric_report.py`, `shadow_report.py`, `nogap_report.py`, `validate_greeks.py`, `make_golden.py` | 검증 리포트·섀도 점검·무결측 판정·그릭스 재현 검사·골든 fixture 생성 | `main`, `parse_junit`, `summarize`, `judge`, `chain_subset` | 로컬 fixture, DB |
| `api/`, `ui/`, `live/`, `exec/`, `strategy/`, `backtest/`, `services/notifier/` | **빈 자리**(`__init__.py`·`.gitkeep`만). Phase 4~8 예정 | — | — |

---

## 3. 외부 데이터 클라이언트 표

| 출처 | 파일 | 인증 방식(환경변수 이름) | 호출 한도·재시도·캐시 | KIS 토큰 직접 발급? |
|---|---|---|---|---|
| KIS REST(국내선물옵션 시세: 전광판 콜풋 `FHPIF05030100`, 선물 전광판 `FHPIF05030200`, 기초자산 `FHPIF05030000`, 단건 현재가 `FHMIF10000000`, 분봉, 월물리스트, 투자자별 `FHPTJ04030000`) | `data/kis/rest.py` `KisClient`, `services/poller/endpoints.py`, `services/scheduler/minute.py` | Bearer 토큰 + `KIS_APP_KEY`·`KIS_APP_SECRET` 헤더, `KIS_ENV`(real/vts) | Redis 레이트리미터 **4.0건/초·버킷 1**(앱키당 하나, `data/kis/ratelimit.py`), 우선순위 P0~P4, `EGW00201` 시 1회 재시도 + 60초 절반 감속 후 0.5/s씩 회복. 토큰 거절 `EGW00121`·`EGW00123` 시 토큰 무효화 후 1회 재시도. 전광판 TR 최소 간격 1초 | 아니오(토큰은 `TokenProvider` 로 받음). 단 `default_token_provider` 는 캐시 비면 발급 가능 — probe 로컬 실행 한정 |
| KIS 토큰 `/oauth2/tokenP` | `data/kis/auth_client.py` `KisTokenIssuer`(:165), `CachedTokenProvider`(:436) | `KIS_APP_KEY`·`KIS_APP_SECRET` | 캐시 우선(Redis 키 `kis:token`, 없으면 파일 `state/kis.token.json` = `KIS_TOKEN_CACHE_PATH`), 만료 60분 전 갱신, 발급 61초에 1회 이하(다음 가능 시각을 캐시에 공유), 오류 문구 가림(`_redact`) | **예** — `KisTokenIssuer`, 운영은 `services/auth/service.py` `AuthService` 만 |
| KIS WS 접속키 `/oauth2/Approval` | `services/auth/service.py` `KisApprovalKeyIssuer`(:465) | `KIS_APP_KEY`·`KIS_APP_SECRET` | Redis `kis:ws_key`, 61초 간격, 만료 미제공이라 `issued_at + life` (미실측 [확인 필요]) | **예** — 접속키 발급 |
| KIS WebSocket(`H0IFCNT0`·`H0IOCNT0`·`H0MFCNT0`·`H0EUCNT0` 등) | `services/ws_gateway/client.py` `KisWsClient`, `config/kis_ws.yaml` | 접속키(`approval_key`, Redis `kis:ws_key` 읽기만 — `redis_key_source`) | 41건 예산(주간 옵션 38·야간 34), 등록·해지 0.1초 간격 → 오류 시 ×2(상한 1초), 재연결 지수 백오프 1→60초, 수신 큐 20만 건 | 아니오 |
| KIS 마스터 파일(`fo_idx_code_mts.mst.zip`) | `scripts/probe_common.py:199` `MASTER_URL`, `services/scheduler/service.py:115` 에서 import | 없음(공개 다운로드) | 접속 5·읽기 15·전체 30초, 실패 시 60초 뒤 재시도, Redis `kis:master`+sha 캐시 | 아니오 |
| KRX Open API(`/drv/fut_bydd_trd`, `/drv/opt_bydd_trd`, `data-dbg.krx.co.kr`) | `data/krx/eod.py` `KrxClient`, `services/scheduler/krx.py` | `AUTH_KEY` 헤더 ← `KRX_API_KEY` | KRX 10,000/일 중 자체 상한 `KRX_DAILY_CALL_CAP`(기본 200, Redis `krx:calls:<YYYYMMDD>` TTL 3일), 갱신 전이면 10분 뒤 재시도·10:00 마감, 접속 5·읽기 30·전체 120초·64MiB | 아니오 |
| Redis | `services/bus.py`, `data/kis/ratelimit.py` | `REDIS_URL` | — | — |
| PostgreSQL/TimescaleDB | `data/store.py`, `db/migrate.py` | `DATABASE_URL`(compose 는 `POSTGRES_PASSWORD`) | 장애 시 `data/spool.py` 디스크 큐, 백오프 1→30초 | — |
| 네이버·DART·공공데이터포털·ECOS·KOSIS·KOFIA·FRED·Yahoo·Finnhub·RSS·운용사 | 없음 | — | — | — |
| 텔레그램 Bot API | 없음(설정 필드만) | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | — | — |
| Healthchecks.io 등 외부 하트비트 | 없음(Phase 5 설계) | `HEALTHCHECK_URL` [계획] | — | — |

---

## 4. 정기 실행 표

| 시각(KST)·주기 | 작업 | 정의 위치 | 받는 데이터 | 산출물 |
|---|---|---|---|---|
| 1초마다 | 세션 상태 평가, 바뀔 때만 발행 | `services/scheduler/service.py`(상주 루프) + `core/calendar.py` `state_at` | 시계·캘린더 | Redis `session:state`, 채널 `session.events`, `session_log` |
| 10초마다(TTL 60초) | 서비스 하트비트 | 각 서비스, `services/bus.py` `heartbeat_key` | — | Redis `health:heartbeat:<서비스>`, compose healthcheck(30초) |
| 만료 60분 전 / 실패 시 61초 | KIS 토큰·WS 접속키 갱신 | `services/auth/service.py` `AuthService` | KIS oauth2 | Redis `kis:token`, `kis:ws_key`, `health_events` |
| 08:00(PRE_DAY)·17:50(PRE_NIGHT) | KIS 마스터 다운로드·파싱 | `services/scheduler/service.py` (데몬 스레드) | 마스터 zip | Redis `kis:master`(+sha), `master_snapshots` |
| 거래일 08:05~10:00, 10분 재시도 | KRX 전 거래일 선물·옵션 일별 적재 | `services/scheduler/krx.py` `KrxDaily` | KRX 일별 | `krx_fut_daily`, `krx_opt_daily`, health `krx_daily_*` |
| 마스터·KRX 둘 다 있을 때(마스터 sha마다 1회, 실패 시 10분) | 마스터 ⊇ KRX 행사가 대조 | `services/scheduler/master_check.py` | Redis 마스터, `krx_opt_daily` | health `master_krx_missing` / 로그 |
| 08:48·18:03(+15초) | 개장 이중 확인(개장 3분 체결 유무) | `services/scheduler/open_check.py` `OpenCheck` | `fut_ticks` | health `calendar_mismatch`, `session_log` |
| 휴장일 08:44~08:55, 닫힌 밤 17:59~18:10 | 닫힌 세션 확인 구독(선물 체결 1건) | `services/ws_gateway/watch.py` `ClosedWatch` | KIS WS | health `ws_unexpected_open`·`ws_closed_watch_*`, `session_log` |
| 30초 | 전광판 콜/풋 3만기, 선물 전광판·기초자산 | `services/poller/planner.py` `plan`, `endpoints.py` | KIS REST | `chain_snapshots`(board), `fut_board`, `raw_messages` |
| 60초 | 투자자별 7조합, 보강 1(월물 ATM±20 단건 82건) | 같은 곳 | KIS REST | `investor_flow`, `chain_snapshots`(fill) |
| 1초 | 보강 2(나머지 행사가 순환) | 같은 곳 | KIS REST | `chain_snapshots`(fill) |
| 세션 시작 + 1시간 | 월물리스트(만기 목록) | 같은 곳 | KIS REST | `series_expiries`, Redis `poller:chain_context` |
| 야간(분기 B) | 단건 `EU` 옵션·`CM` 선물로 체인 구성(분당 약 194건) | `services/poller/planner.py` `night_mode`, `_night_futures` | KIS REST | 위와 같은 표(session=night) |
| 1분 | WS ATM 재계산·2행사가 이상 차이 시 구독 회전 | `services/ws_gateway/session.py` `SubscriptionController`, `subscriptions.py` `should_rotate` | 선물 체결가 | WS 구독 변경 |
| 실시간 | WS 체결 수신·발행·저장(500건/0.5초 묶음) | `services/ws_gateway/service.py` `GatewayWorker` | KIS WS | `fut_ticks`, `opt_ticks`, 채널 `ws.raw`·`ticks.fut`·`ticks.opt` |
| 실시간(최대 1초·500건 묶음) | 원문 녹화 | `services/recorder/service.py` `Recorder` | 채널 `ws.raw`·`rest.raw` | `raw_messages` |
| `chain.ready` 마다(약 30초), 놓치면 10초 따라잡기 | 지표 사이클(GEX·레벨·확장 지표) | `services/engine/service.py` `EngineService`, `evaluate.py` `evaluate_cycle` | DB 체인·선물, Redis | `levels`, `metrics`, `strike_gex`, `option_iv`, `oi_changes`, Redis `engine:latest`, 채널 `engine.levels`·`engine.metrics` |
| 2분 | Charm 익스포저 | `services/engine/extended.py` `compute_cex` [추정: 주기는 metrics §4.2] | 사이클 결과 | `metrics` |
| POST_DAY 1회 | 일별 지표(IV 랭크·HV·딜러 점검) | `services/engine/daily.py` `evaluate_daily` | KRX 일별, `metrics` 이력, `investor_flow` | `metrics` |
| 30초 | 기능 플래그 파일 재로드(mtime) | `core/features.py` `FeatureWatcher` | `config/features.yaml` | 플래그 갱신, health `engine_flags_invalid` |
| 거래일 16:00~17:50(주간), 06:10~08:00(야간) | KIS 분봉 적재(주간 `F` 5회·야간 `CM` 8회 상한, 실패 시 5분 간격 12회) | `services/scheduler/minute.py` `MinuteDaily`·`MinuteLoader` | KIS 분봉 REST | `minute_bars`, `raw_messages`, `quarantine` |
| 분봉 적재 뒤(대체 17:20·07:30) | 무결측 판정 | `services/scheduler/gaps.py` `GapDaily`, `services/gaps.py` | DB 전체 수집 표 | `collection_gaps`, `collection_reports`, health `nogap_session_ok` 등 |
| push(main)·PR | CI(ruff·format·pyright·pytest·Docker 빌드) | `.github/workflows/ci.yml` | — | Actions 결과 |
| 수동만(`workflow_dispatch`) | KIS·KRX probe | `.github/workflows/probe.yml` (스케줄 분기 코드는 남았지만 `schedule` 트리거 없음) | KIS·KRX | 아티팩트 `probe_out/`, 런 요약 |
| (기록용, 삭제됨) | 2026-09-28 맥 crontab 1회 야간 probe | `docs/runbook.md` | KIS | `probe_out/runs/` |
| **계획만** 08:30·12:00·15:50·06:05 | 텔레그램 정기 브리핑 | `docs/phase4_design.md` §4 (코드 없음) | engine 산출 | — |
| **계획만** POST_DAY / 주 1회 | Parquet 내보내기, `pg_dump` 백업, 5분 unhealthy 재시작 timer | `docs/phase5_8_design.md` (코드 없음) | DB | — |

---

## 5. 텔레그램 표

**구현 없음.** `services/notifier/__init__.py` 는 빈 파일이고, 저장소 안에서 텔레그램을 부르는 코드가 없다. 설정 필드와 설계만 있다.

| 발송 함수 | 메시지 종류 | 발송 시각·조건 | 봇/채팅 환경변수 이름 | 양방향 명령 |
|---|---|---|---|---|
| 없음 (계획: `services/notifier/rules.py` 순수 규칙 + 발송기) | 정기 브리핑: 레짐, 순GEX, 콜월·풋월·Flip, 기대범위, 전 세션 대비 변화, 투자자별 선물 순매수, 데이터 품질 | 08:30·12:00·15:50·06:05 KST (scheduler 트리거) — 계획 | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (`config/settings.py:29-30`, `SecretStr`) | 계획: `/status`, `/levels`, `/kill`(확인 단계 → trader), `/resume`, 허용 chat_id 외 무시 |
| 없음 | 이벤트: Flip 돌파, 순GEX 부호 전환, 콜월·풋월 이동, 기대범위 이탈, 대량 체결, 시스템 장애 | ±0.1% 버퍼 + 연속 2스냅샷, 쿨다운 10분, 품질 ok 아니면 보류 — 계획 | 위와 같음 | — |

근거: `docs/PLAN.md` §8, `docs/phase4_design.md` §4.

---

## 6. 저장 표

DB: PostgreSQL 16 + TimescaleDB(컨테이너 `db`, 볼륨 `pgdata`). 모든 표 hypertable(대부분 `ts` 1일 청크, 일 단위 표는 365일), 공통 `trade_date`·`session(day|night)`·`quality(ok|stale|estimated|invalid)`.

| DB·파일 | 표 | 주요 컬럼 | 쓰는 쪽 → 읽는 쪽 |
|---|---|---|---|
| PG `001_init.sql` | `raw_messages` | ts, source(kis_ws·kis_rest·krx), tr_id, key, payload jsonb, digest | recorder(발행자 직접 쓰기 폴백 포함) → make_golden, gaps(`underlying`·`fill1` 집계). 3일 뒤 압축 |
| 〃 | `fut_ticks` / `opt_ticks` | code, seq, price, qty, cum_vol, cum_buy_qty, cum_sell_qty, oi, oi_chg, (옵션) expiry, strike, cp, delta, gamma, vega, theta, iv | ws-gateway → engine(HIRO·플로우), open_check, gaps |
| 〃 | `chain_snapshots` | expiry, strike, cp, last, bid, ask, oi, oi_chg, volume, iv_kis, KIS 그릭스, source(board·fill) | poller → engine |
| 〃 | `fut_board` | code, price, volume, oi, bid, ask | poller → engine(S_ref·선물 지표), gaps |
| 〃 | `investor_flow` | market_code, sector_code, investor(12종), buy/sell/net × qty/value | poller → engine(투자자·딜러 점검), gaps |
| 〃 | `series_expiries` | mrkt_cls, expiry, source(kis·calendar), last_trade_date, calendar_date, matches, code | poller → engine, gaps |
| 〃 | `minute_bars` | code, market(F·CM), OHLC, volume, value | scheduler(minute) → gaps(체결 공백), engine 선물 [추정] |
| 〃 | `krx_fut_daily` / `krx_opt_daily` | bas_dd, session, isu_cd, prod_nm, expiry, (옵션) strike·cp·imp_volt, OHLC, setl_prc, spot_prc, acc_opnint_qty | scheduler(krx) → engine daily(HV·IV 랭크), master_check |
| 〃 | `master_snapshots` | asof, kind, code, name, cp, strike, atm_cls, series, underlying | scheduler → (백테스트용 이력) |
| 〃 | `session_log`, `health_events`, `collection_gaps`, `quarantine` | 상태 전이·경고·공백·검증 실패 원문 | 전 서비스 → nogap_report, shadow_report |
| PG `002` | `collection_reports` | trade_date, session, stream, 기대·실수신·공백 수·최대 공백·판정 | scheduler(gaps) → `scripts/nogap_report.py` |
| PG `003`·`004` | `levels` | ts, scope, name, value, detail jsonb, quality, reasons, flag | engine → (api 예정), metric_report |
| 〃 | `metrics` | ts, metric, scope, key, value, payload jsonb, quality, flag | engine → shadow_report, engine daily(ATM IV 이력) |
| 〃 | `strike_gex` | ts, mrkt_cls, expiry, strike, 콜·풋·순 GEX | engine → (대시보드 예정) |
| 〃 | `option_iv` | ts, mrkt_cls, expiry, strike, cp, iv, source, rescaled, t_kis, delta, gamma | engine → (스마일·HIRO) |
| 〃 | `oi_changes` | ts, mrkt_cls, expiry, strike, cp, OI 증감, flag | engine → (히트맵 예정) |
| PG | `schema_migrations` | 번호, 파일명, 체크섬, 적용 시각 | `db/migrate.py` |
| Redis | `kis:token`, `kis:ws_key` | 토큰·접속키·만료·다음 발급 가능 시각 | auth → 모든 KIS 호출자(읽기만) |
| Redis | `rl:kis:<앱키 해시>` | 레이트리미터 상태 | 모든 KIS 호출자 |
| Redis | `kis:master`(+`:sha`), `poller:chain_context`, `session:state`, `engine:basis`, `engine:latest`, `krx:calls:<YYYYMMDD>`, `health:heartbeat:<서비스>` | 마스터 원문, 체인 문맥, 세션 상태, 확정 베이시스, 최신 산출, KRX 호출 수, 하트비트 | `services/bus.py:44-60` |
| Redis 채널 | `ws.raw`, `rest.raw`, `ticks.fut`, `ticks.opt`, `chain.ready`, `session.events`, `engine.levels`, `engine.metrics` | pydantic JSON | 발행자 → recorder·engine |
| 파일 | `state/kis.token.json` | 토큰 캐시(Redis 없을 때) | probe·auth_client (gitignore) |
| 파일 | `state/spool/<서비스>/<표>/*.jsonl`, `_dead/<표>.jsonl` | DB 장애 중 쓰기 묶음 | 각 서비스(볼륨 `spool`) |
| 파일 | `probe_out/*.json`, `summary.md` | probe 결과 | probe → 사람이 `docs/probe_results.md` 로 옮김 (gitignore) |

---

## 7. 환경변수 이름 목록

| 이름 | 용도 | 사용 파일 |
|---|---|---|
| `KIS_APP_KEY`, `KIS_APP_SECRET` | KIS 시세 전용 실전 앱키 | `config/settings.py`, `data/kis/auth_client.py`, `services/auth/service.py`, probe, `probe.yml` secrets |
| `KIS_ENV` | `real` / `vts` 선택 | `config/settings.py` `kis_base` |
| `KIS_DEMO_APP_KEY`, `KIS_DEMO_APP_SECRET`, `KIS_DEMO_ACCOUNT` | KIS 모의 주문(Phase 8) | `.env.example` 에만 있음 — **코드에서 안 씀** |
| `KRX_API_KEY` | KRX Open API 인증키 | `config/settings.py`, `data/krx/eod.py`, `services/scheduler/krx.py`, `probe.yml` secrets |
| `KRX_DAILY_CALL_CAP` | KRX 하루 호출 상한(기본 200) | `config/settings.py`, `services/scheduler/krx.py` |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | 텔레그램(Phase 4) | `config/settings.py` 에만(미사용) |
| `REDIS_URL` | Redis 접속 | `config/settings.py`, compose(`redis://redis:6379/0` 덮어씀) |
| `DATABASE_URL` | PostgreSQL 접속 | `config/settings.py`, `db/migrate.py`, scripts(nogap·shadow·make_golden) |
| `POSTGRES_PASSWORD` | db 컨테이너 비밀번호 | `docker-compose.yml` |
| `SPOOL_DIR`, `SPOOL_MAX_MB` | 디스크 큐 경로·상한 | `config/settings.py`, `data/spool.py`, compose |
| `KIS_TOKEN_CACHE_PATH` | 파일 토큰 캐시 경로(기본 `state/kis.token.json`) | `config/settings.py` (pydantic 필드 이름에서 유도) |
| `LIVE_TRADING` | 실전 주문 스위치(기본 false) | `config/settings.py` (주문 코드 없음) |
| `PROBE_OUT_DIR` | probe 출력 폴더 | `scripts/probe_common.py:24` |
| `GEXLAB_TEST_TIMESCALE_IMAGE` | 통합 시험 DB 이미지 | `tests/integration/conftest.py` |
| `TZ` | 컨테이너 시간대 UTC | `docker-compose.yml`, `Dockerfile` |
| `HEALTHCHECK_URL` | 외부 하트비트(계획) | `docs/phase5_8_design.md` 만 |

---

## 8. 기능 목록 (사용자에게 보이는 것)

현재 **웹 화면·API 엔드포인트·엑셀·텔레그램은 없다**. 보이는 것은 아래뿐이다.

| 종류 | 기능 | 근거 |
|---|---|---|
| 운영 스택 | `docker compose up` 으로 수집·녹화·지표 계산 24시간 가동, `docker compose ps` 하트비트 상태 | `docker-compose.yml`, `docs/runbook.md` |
| Redis 조회 | `session:state`(세션 상태), `engine:latest`(최신 레벨·지표·품질 요약) — `redis-cli` 로 직접 | `services/bus.py`, runbook |
| DB 조회 | 위 저장 표(levels·metrics·strike_gex 등)를 `psql` 로 직접 | runbook |
| 리포트(마크다운) | `docs/metric_validation.md` — 지표별 검증 리포트(`scripts/metric_report.py --write`) | `docs/phase3_design.md` §5 |
| 리포트(콘솔) | 무결측 판정(`scripts/nogap_report.py`, 종료 코드 0/1/2), 섀도 운영 점검(`scripts/shadow_report.py`) | 각 스크립트 docstring |
| 검증 도구 | KIS 그릭스 관례 재현 검사(`scripts/validate_greeks.py` → `docs/validation_greeks.md`), 골든 fixture 생성·비교(`scripts/make_golden.py`) | 같음 |
| 실측 도구 | `scripts/probe_all.py` (로컬·Actions 수동) → `probe_out/summary.md` | `.github/workflows/probe.yml` |
| 계산 산출(표시 대상 예정) | 순GEX·DEX·콜월·풋월·절대감마·Flip·전환점 거리·기대변동폭(달력·거래)·상위 레벨·ATM IV·만기별 감마 소멸액(visible) / VEX·CEX·GEX P/C·기간구조·25Δ 스큐·IV 랭크·IV−HV·HIRO-lite·PCR·맥스페인·딜러 점검·투자자별·OI 증감·선물 지표(shadow) | `config/features.yaml`, `core/features.py` |
| **계획(Phase 4)** | FastAPI `GET /api/state`, `/api/levels`, `/api/gex/strikes`, `/api/series/{name}`, `/api/vol/smile`, `/api/flow/*`, `/api/health`, `WS /ws`, `POST /api/kill`·`/api/resume`; UI 탭 Live·Volatility·Flow·Night·Stats·Backtest·Trading·Health | `docs/phase4_design.md` §2·§3 |

---

## 9. 계산 엔진·지표

모든 공식의 정본은 `docs/metrics.md`. 이 레포는 **코스피200 파생(옵션 GEX) 전용**이라 주식 기술적 지표·신고가·밸류에이션은 없다.

| 범주 | 핵심 함수 | 테스트 |
|---|---|---|
| 상품 명세·틱 | `core/specs.py` `round_to_tick`, `option_tick` | `tests/unit/test_specs.py`, `tests/property/test_specs_properties.py` |
| 캘린더·세션·만기 | `core/calendar.py` `state_at`, `night_session_opens`, `expiry_at`, `weeklies_listed` | `test_calendar.py`, `test_calendar_properties.py` |
| 가격 선택·전처리 | `core/preprocess.py` `select_price` | `test_preprocess.py` |
| 합성선물 F·베이시스 | `core/forward.py` `synthetic_forward`, `confirm_basis`, `basis_age`, `select_s_ref` | `test_forward.py`, `test_forward_properties.py` |
| Black-76·IV·그릭스 | `core/black76.py`, `core/iv.py` `implied_vol`, `core/greeks.py` `vanna`·`charm` | `test_black76.py`, `test_iv.py`, `test_greeks.py`, `test_pricing_properties.py` |
| GEX·DEX(부호 고정) | `core/gex.py` `evaluate_expiry`, `option_gex`, `option_dex`, `strike_gex` | `test_gex.py`, `test_gex_properties.py`, `tests/test_sign_conventions.py` |
| 레벨 | `core/levels.py` `call_wall`, `put_wall`, `gamma_flip`, `zero_dte_levels`, `atm_iv` | `test_levels.py`, `test_levels_properties.py` |
| 익스포저 | `core/metrics/exposure.py` `vex`, `cex`, `gex_put_call_ratio` | `test_exposure.py`, `test_exposure_properties.py` |
| 변동성 | `core/metrics/vol.py` `term_structure`, `skew_25d`, `iv_rank`, `realized_vol`, `iv_minus_hv` | `test_vol.py` |
| 플로우·수급(투자자 합계, 행사가별 아님) | `core/metrics/flow.py` `hiro_step`, `pcr`, `max_pain`, `oi_change_cells`, `dealer_check`; `services/engine/flow.py` `investor_record`, `dealer_record`, `TickFlow` | `test_flow.py`, `test_flow_properties.py`, `test_engine_flow.py`, `test_engine_investor.py` |
| 선물 | `core/metrics/futures.py` `futures_metrics` | `test_futures_metrics.py`, `test_futures_properties.py`, `test_engine_futures.py` |
| ATM·체인 창 | `core/chain.py` | `test_chain.py`, `test_chain_properties.py` |
| 사이클 통합 | `services/engine/evaluate.py` `evaluate_cycle`, `daily.py` `evaluate_daily` | `test_engine_evaluate.py`, `test_engine_daily.py`, `tests/golden/test_core_golden.py`, `tests/integration/test_engine_store.py` |
| 레이트리미터 | `data/kis/ratelimit.py` | `test_ratelimit.py`, `test_ratelimit_properties.py`(어떤 1초 창도 4건 이하) |
| 무결측 판정 | `services/gaps.py` | `test_gaps.py`, `tests/integration/test_scheduler_gaps.py` |

모든 핵심 계산 모듈에 단위 + 속성(hypothesis) 테스트가 있고, 골든 테스트가 2026-09-28 두 스냅샷의 파이프라인 출력을 고정한다.

---

## 10. 품질·위험 메모

| 구분 | 내용 | 근거 |
|---|---|---|
| 빈 레이어 | `api`·`ui`·`live`·`exec`·`strategy`·`backtest`·`services/notifier` 가 빈 패키지. 대시보드·알림·백테스트는 설계 문서만 있음 → KBJ 통합 시 화면·알림은 새로 만들어야 함 | 각 `__init__.py` 0줄 |
| 운영 완료 기준 대기 | Phase 1 3거래일 무결측, Phase 3 1주 섀도 모두 미달 — **전용 KIS 앱키가 없어 라이브 녹화가 없다**. 라이브 raw 녹화 골든도 없음(합성 한 벌뿐) | PLAN §12, phase1_design §10 |
| 앱키 공유 경합 | 같은 KIS 앱키를 `stock-dashboard`(launchd 매일 18:00)·`etf-traker`·`kospi-dislocation`(평일 08:55)이 함께 쓴다고 기록. 토큰 1분 1회·REST 한도를 나눠 써 18:00 야간 전환과 겹침 → KBJ에서 **토큰 발급 단일화**가 필요 | `docs/phase1_design.md:92`, `docs/probe_results.md:88`, `scripts/probe_rest_limit.py:6` |
| 토큰 발급 지점 둘 | 운영은 `auth` 서비스만 발급하지만, `data/kis/auth_client.py` `default_token_provider` 는 캐시가 비면 probe 로컬 실행에서 직접 발급(파일 캐시 `state/kis.token.json`) | `data/kis/auth_client.py:539` |
| 계층 위반·하드코딩 | 운영 서비스가 `scripts.probe_common.MASTER_URL` 을 import(마스터 URL 하드코딩이 scripts에) | `services/scheduler/service.py:115` |
| 중복 | 마스터 파서가 `data/kis/master.py` 와 `scripts/probe_chain_fill.py` `MasterRow`/`parse_master_line` 에 둘(probe 쪽은 다섯째 필드를 콜/풋으로 잘못 읽던 옛 버전), `_nth_weekday` 가 `core/calendar.py:385` 와 `services/poller/context.py:88` 에 둘 | 해당 파일 |
| 평문 WS | KIS WS 공식 주소가 `ws://`(평문) — 접속키가 평문으로 감(KIS 방식 그대로) | `config/kis_ws.yaml:32` |
| 미사용 설정 | `KIS_DEMO_*` 는 `.env.example` 에만, `TELEGRAM_*`·`LIVE_TRADING` 는 설정 필드만 있고 소비 코드 없음 | `.env.example`, `config/settings.py` |
| [확인 필요] 다수 | 기본값 다수가 미실측: WS 접속키 응답 형식, WS 등록 속도 한도, 기초자산 조회 파라미터(`underlying`), 분봉 첫 봉 표기, KRX 갱신 시각, 베이시스 이월 2거래일 등 | `docs/phase1_design.md` §12, `docs/phase3_design.md` |
| 비밀 노출 방지(양호) | `SecretStr`·`_redact`·`hide_cut_tail` 로 키 가림, `.gitignore` 에 `.env`·`state/`·`*.token.json`·`probe_out/`, 골든 생성기가 approval_key·암호문·HTS ID를 `REDACTED` 로 가리고 남으면 쓰기 거부, `tests/unit/test_fixtures_clean.py` 가 fixture를 훑음 | `.gitignore`, `scripts/make_golden.py:915`, phase1_design §10 |
| **공개 레포 문제 ① 실데이터 fixture** | `tests/fixtures/kis/*.json`(전광판 콜풋 202610·WKM 260904, 투자자, 선물 전광판, 분봉, 단건 현재가 — 각 1~38KB)과 `tests/fixtures/validation/chain_snapshot_20260928_*_small.json`, `tests/golden/core/chain_20260928_small.json` 이 **2026-09-28 실측 KIS 시세 원본 발췌**. CLAUDE.md 스스로 "KIS 시세 제3자 제공 불가 → 대시보드 비공개"라 적음 → 공개 레포로 옮기면 KIS 이용 조건 위반 소지. KRX fixture(`tests/fixtures/krx/*.json`)도 원본 발췌라 출처 표기 조건 확인 필요. **공개 전 합성 데이터로 교체 권장** | `CLAUDE.md` API 제약, `tests/fixtures/` |
| 공개 레포 문제 ② 개인 정보·환경 | `docs/runbook.md:35` 에 개인 맥 경로(사용자 이름 포함 `/Users/...`), 다른 개인 프로젝트 이름·launchd 일정 기록 | runbook, phase1_design |
| 공개 레포 문제 ③ 목적·용도 | README "비공개, 자기 투자 목적", 자동매매(Phase 9) 계획 포함 → 공개 시 실전 주문 경로·리스크 문서 분리 필요 | `README.md`, PLAN §10 |
| 스크래핑 | 웹 스크래핑 없음. KIS 공식 API·공식 마스터 zip·KRX Open API만 사용 | §3 표 |
| Actions 의존 | probe 는 클라우드 개발 환경에서 KIS·KRX가 프록시에 막혀 Actions/맥 로컬에서만 실행. private 레포 Actions 무료 분 아끼려 수동 전용 | `.github/workflows/probe.yml` 주석 |
| 규모 대비 복잡도 | 테스트 비중이 크고(전체 줄의 53%) 문서·주석이 매우 상세 — 이식 시 설계 문서(`docs/*.md`)를 함께 가져가야 기본값의 근거가 유지됨 | §1 |
