# KBJ 충돌 지도 — 파일 단위 확정 (P0)

- 작성: 2026-10-06. PLAN §2(충돌 지도 초안)를 실제 파일·함수 단위로 확정한다. 근거 자료와 표기는 `docs/inventory.md` 머리말과 같다(SD=stock-dashboard `f46178c`, ET=etf-traker `0014f57`, GX=gexlab `43a9ed1`).
- 키·토큰·계좌 값은 적지 않았다. 환경변수는 이름만. **[추정]** = 코드로 끝까지 확인하지 못함. **[제안]** = 이 문서의 권고. ⚠ = 충돌·대체 없음.
- 완성도 점검(2026-10-06): §1·§2·§3의 테스트 수는 원본에서 `def test` 를 직접 세고(board 1,319·kr 94·flow 129 정적 개수 일치, GX 파일별 개수 일치) flowlab selftest(44/44)·SD 검사 스크립트(검사 20·35·19)를 다시 돌려 모두 맞았다. 고친 것: KIS 발급 지점(K1·K3·K7·단계 8 — GX `probe.yml` 추가), 줄 번호 6곳, 텔레그램 지점 수(42→46), §2 크기 1곳·등급 1곳, 공개 스키마로 가면 안 되는 사전·재무비율 2곳.
- 확정된 사용자 결정 **U1~U4**(2026-10-06)는 질문으로 다시 남기지 않는다. 정본이 애매한 곳은 마지막 §4 "사용자 확인 필요"에 질문으로만 남겼다.

## 0. 이번 조사에서 새로 확인한 사실 (계획에 영향)

| # | 사실 | 근거 | 영향 |
|---|---|---|---|
| E1 | **KRX OpenAPI 일별 시세는 다음 영업일 08:00에 나온다.** board가 16:10에 덮으려던 KRX 확정 종가는 `close_source` 필드가 있는 보관본 4개 전부(9/21·9/22·9/23·10/2 — 9/17 보관본은 필드 이전 형식)에서 **한 번도 적용되지 않았다**(`close_source: naver`, `close_confirmed: false`). 10/5 17:13 실행 기록: "KRX 최신 공표일이 2026-10-01 다(기준일 2026-10-02) — 정규장 확정치가 아직 안 나왔다" | ET:`docs/api/d/2026-09-2{1,2,3}.json`·`2026-10-02.json`, `docs/api/latest.json` `notices.miss`, GX:`docs/PLAN.md:105·142`(#16 "다음 영업일 08:00 갱신") | U4로 네이버를 빼면 **당일 마감 요약에 쓸 종가 출처가 KIS뿐**이다 → §4 Q1 |
| E2 | GX 테스트는 **pandas 2.3.3**(pykrx가 `pandas<3.0` 을 요구해 단일 lock이 내려감)에서도 3,178개 통과 | `/tmp/p0test/venv-312` 실행 | 단일 uv lock 가능 |
| E3 | 세 프로젝트 테스트 전부가 **Python 3.12에서 통과**(board 1,319·kr 94·flow 129·flowlab 44·SD 검사 9/10·GX 3,178) | 같은 venv | Python 3.12 단일화 가능 |
| E4 | SD `scripts/check_futures_us_index.py` 는 스냅샷 그대로 **실패**한다(2절 `NameError: _yf`). 스크립트가 `server.py` 에서 AST로 꺼내는 함수 목록에 `_yf` 가 빠졌다 | 3.11·3.12 둘 다 같은 오류 | P1 "기존 테스트 전부 통과" 기준에서 이 1건은 '원래 실패'로 기록 |
| E5 | pykrx 1.2.x 는 import 할 때 환경변수 `KRX_ID`·`KRX_PW` 로 KRX 정보데이터시스템 자동 로그인을 시도한다 — ET `monitor/flow`·`flowlab` 의 같은 이름과 겹친다 | `pykrx/website/comm/auth.py:176` | 환경변수 정리(`inventory.md` (f)) |
| E6 | GX `KisClient` 는 `token_provider` 를 안 넘기면 `default_token_provider` 로 **직접 발급할 수 있다**(probe가 이 경로) | GX:`data/kis/rest.py:120-122`, `data/kis/auth_client.py:539` | "발급은 auth 한 곳" 이행 대상에 포함 |

---

## 1. 영역별 충돌 지도

열 정의: **지금 구현들**(레포:파일:함수) | **정본**(레포:파일) | **근거**(테스트 수·실측·범위) | **나머지 처리** | **단계**. 테스트 수는 `def test_` 정적 개수(GX는 파라미터 확장 전) 또는 실행 결과.

### 1.1 KIS 인증·토큰 (U1: 발급은 auth 한 곳)

#### 발급 지점 전부

| # | 지점 | 발급 종류 | 캐시 | 실행되는 곳 | 근거 |
|---|---|---|---|---|---|
| K1 | SD:`kis_api.py:_get_token`:50 → `POST /oauth2/tokenP`:64 | 접근토큰 | 메모리 + `cache/kis_token.json`(만료 300초 전 재발급, 락은 프로세스 내) | Render(재시작마다 캐시 소실 [추정]), 맥에서 `server.py` 를 직접 띄울 때 [추정]. 맥 crontab 18:00(`daily_macbook_cron.sh`)의 6개 스크립트는 `kis_api` 를 import 하지 않아 발급 경로가 아니다 | sweep A-1, `grep -l kis_api *.py` = `kis_api.py`·`server.py`·`data_freshness.py`(이름 문자열만) |
| K2 | SD 코드를 ET에서 실행: ET:`board/tools/dashboard_brief_preview.py:build` 가 SD `server.py` 함수를 `exec` → K1 | 접근토큰 | 러너마다 없음 | Actions `dashboard-brief-preview.yml`(수동) | etf-board 인벤토리 §10-4 |
| K3 | ET:`board/ingest/kis.py:token`:103 → `POST /oauth2/tokenP`:112 | 접근토큰 | `board/state/.kis_token.json`(만료 10분 전, 원자적 쓰기) | 맥 launchd 16:10, Actions `board.yml`(state 캐시 복원. 예약은 2026-09-29에 꺼졌고 `repository_dispatch` `board-daily`·`board-send`·수동 `kis-probe`·`check` 모드만 남음 — `board.yml` 머리 주석, `run.py:1442`) | `kis.py:90-130` |
| K4 | ET:`monitor/kr/flows.py:collect` → `board.ingest.kis.stock_flows` → K3 | 접근토큰 | `kr.yml` 은 `board/state` 를 캐시하지 않음 → **회차마다 새로 발급** [추정] | Actions `kr.yml` 09:30·15:30 | etf-rest 인벤토리 §10 |
| K5 | ET:`monitor/flow/kissrc.py:fetch_daily`:48·`probe`:102(`K.token()`:106 명시 호출) → K3 | 접근토큰 | board 캐시 복원본에 있으면 재사용 | Actions `flow.yml` 18:17 | 같음 |
| K6 | ET:`board/tools/probe_kis_futures.py` → `K._headers` → K3 | 접근토큰 | 없음 | Actions `kis-futures-probe.yml`(수동) | sweep B-1 |
| K7 | GX:`data/kis/auth_client.py:KisTokenIssuer`:165(`issue`:186) ← `default_token_provider`:539 ← `data/kis/rest.py:KisClient._provider`:120 (`token_provider` 없을 때) | 접근토큰 | Redis `kis:token` 우선, 실패 시 `state/kis.token.json` | `scripts/probe_all.py:55`(`KisClient(settings)` — provider 없이 만드는 유일한 곳) 로컬 실행 **+ Actions `probe.yml`(수동, 시크릿 `KIS_APP_KEY`·`KIS_APP_SECRET`, `probe.yml:53-57`)** | E6 |
| K8 | GX:`services/auth/service.py:AuthService`:186 (`build_auth_service`:531 → `KisTokenIssuer`) | 접근토큰 | Redis `kis:token`, 61초 간격 공유(`claim_issue`) | compose `auth` 상주 | **정본** |
| K9 | GX:`services/auth/service.py:KisApprovalKeyIssuer`:465(`issue`:490) (`/oauth2/Approval`) | WS 접속키 | Redis `kis:ws_key` | compose `auth` | **정본**(미실측 표시) |
| K10 | (레포 밖) `kospi-dislocation` 평일 08:55 | 접근토큰 [추정] | 모름 | 맥 [추정] | GX:`docs/phase1_design.md:92` |

재확인(2026-10-06 원본 grep `oauth2/(tokenP|Approval)`): 발급 HTTP 호출 문자열은 SD `kis_api.py:64`, ET `board/ingest/kis.py:112`, GX `data/kis/auth_client.py:46`(`TOKEN_PATH`)·`services/auth/service.py:91`(`APPROVAL_PATH`) 4곳뿐이다. K2·K4~K6은 ET `kis.py` 를, K7·K8은 GX `TOKEN_PATH` 를 거친다. GX `poller`(`services/poller/service.py:227`)·`scheduler` 분봉(`services/scheduler/minute.py:342`)·`ws_gateway`(`services/ws_gateway/client.py:250`)는 `reader()` 로 읽기만 한다. 실행 경로 집계는 `inventory.md` (c) 겹침 요약 ⑤.

| 지금 구현들 | 정본 | 근거 | 나머지 처리 | 단계 |
|---|---|---|---|---|
| K1~K10 | **GX `services/auth/service.py` + `data/kis/auth_client.py`** (읽기는 `services/auth/service.py:reader`:564) | `test_auth_service` 52 + `test_auth_client` 28. 61초 발급 간격을 Redis Lua로 인스턴스끼리 공유, `EGW00133` 실측, 토큰·키 가림(`_redact`), 만료 60분 전 갱신. SD·ET는 테스트가 SD 0, ET `test_kis_call` 32(토큰 경합 시험 없음) | 아래 이행 방법 | **P2** |

#### 이행 방법 — "발급은 auth 한 곳"

| 순서 | 할 일 | 대상 파일 | 확인 방법 |
|---|---|---|---|
| 1 | KBJ `services/auth` 를 GX에서 옮겨 띄운다. 앱키는 `KBJ_KIS_APP_KEY`·`KBJ_KIS_APP_SECRET`, **auth 컨테이너에만** 주입 | GX `services/auth/*`, `data/kis/auth_client.py` → `kbj/services/auth`, `kbj/data/private/kis/auth_client.py` | compose 환경변수 검사(다른 서비스에 앱키 없음) |
| 2 | 읽기 전용 함수 하나를 공용으로 둔다: `kbj.data.private.kis.token()` = `reader(redis, settings).get()`. 없으면 `TokenUnavailable` 을 올리고 **발급하지 않는다** | `services/auth/service.py:reader`:564 재사용 | 단위 시험: Redis 비었을 때 예외, HTTP 호출 0회 |
| 3 | GX `KisClient` 의 기본 발급 경로를 없앤다: `token_provider` 를 필수 인자로 바꾸고 `default_token_provider` 는 삭제(K7). probe도 reader 사용 | GX `data/kis/rest.py:120`, `auth_client.py:539`, `scripts/probe_all.py:55` | `KisClient()` 를 provider 없이 만들면 TypeError |
| 4 | legacy SD: `kis_api._get_token` 본문을 2번 함수 호출로 바꾸고 `_load_token_from_disk`·`_save_token_to_disk`·`cache/kis_token.json` 삭제(K1). K2(`dashboard_brief_preview.py`)는 삭제 | SD `kis_api.py:26-79`, ET `board/tools/dashboard_brief_preview.py` | grep `oauth2/tokenP` 가 legacy에서 0건 |
| 5 | legacy ET: `board/ingest/kis.py:token()` 을 2번 호출로, `force=True` 는 `invalidate()` 후 auth 재발급을 기다리게. `TOKEN_CACHE`·`_save_token` 삭제 → K3~K6 한꺼번에 해소 | ET `board/ingest/kis.py:44,90-140`, `monitor/flow/kissrc.py:103` | 같음 |
| 6 | 모든 KIS REST 호출이 앱키당 Redis 레이트리미터를 거치게 한다(§1.2) | 위 클라이언트 전부 | 속성 시험(어떤 1초 창도 4건 이하) |
| 7 | 금지 규칙: `oauth2/tokenP`·`oauth2/Approval` 문자열과 `KisTokenIssuer` import 를 `kbj/services/auth` 밖에서 금지(import-linter + grep CI) | CI | PLAN P2 완료 기준 |
| 8 | 옛 발급자 정지(전환일에 동시에): Render 환경변수에서 KIS 키 제거, ET 워크플로 시크릿 `KIS_APP_KEY` 사용 5곳(`board.yml`·`kr.yml`·`flow.yml`·`kis-futures-probe.yml`·`dashboard-brief-preview.yml`) + **GX `probe.yml`**(K7) 제거, 맥 launchd board 정지, K10 처리(§4 Q13) | 각 레포 설정 | 가짜 시계 하루 시뮬레이션에서 발급 1회(PLAN P2 기준) |

### 1.2 KIS 호출 한도

| 지금 구현들 | 정본 | 근거 | 나머지 처리 | 단계 |
|---|---|---|---|---|
| SD:`kis_api.py:_rate_limit`:100(프로세스 내 초당 18건) · ET:`board/ingest/kis.py:call`:167(재시도만, probe만 0.4초 sleep), `monitor/kr/flows.py:collect`(4스레드, `MAX_CALLS=1200`, 한도 없음) · GX:`data/kis/ratelimit.py`(Redis GCRA, 초당 4건, 우선순위 P0~P4, `EGW00201` 감속) | **GX `data/kis/ratelimit.py` `RedisRateLimiter`** | `test_ratelimit` 24 + 속성 3("어떤 1초 창도 4건 이하"). 초당 4건은 실측(GX `docs/probe_results.md`). SD 18건은 공식값 기준이라 실측과 다르다 | SD `_rate_limit` 삭제, ET 스레드 수는 그대로 두되 리미터 통과. 우선순위 [제안]: GX 실시간 P0~P1, 장마감 수집 P3, 백필 P4 | P2 |

### 1.3 텔레그램 발송·수신 (U1: 발송은 notifier 한 곳, 수신은 웹훅 하나)

발송·수신 지점 46개 전체 표는 `inventory.md` (d-1)에 있다(2026-10-06 점검에서 HTTP 발송 경로 3개·진단 호출 1개 추가). 여기서는 구현 단위로 정리한다.

| 지금 구현들 | 정본 | 근거 | 나머지 처리 | 단계 |
|---|---|---|---|---|
| 발송기 7벌: SD:`server.py:send_telegram`:5205·`send_telegram_long`:5273, SD:`earnings_telegram_sender.py:send_telegram_message`:51, ET:`board/report/telegram.py:send`:779·`send_document`:822, ET:`monitor/flow/telegram.py:send_photos`:36(sendMediaGroup), ET:`etf_tracker_v9/tracker.py:send_telegram`:395, ET:`monitor/bok/send-telegram.js:main`:44(Node), curl 2곳(`daily.yml`·`preview.yml`) | **ET `board/report/telegram.py`** → `kbj/services/notifier/send.py` | `test_telegram` 73 + `test_send_files` 12. 4,096자 줄 경계 분할, 캡션 상한, `_safe_err` 토큰 가림, (ok, 사유) 반환 | 흡수: sendMediaGroup(flow), 429 `retry_after` 4회(bok), 다중 채팅·토픽 `message_thread_id`(신규), HTML 기본(SD·bok·ETF), 끄기 스위치(SD `TELEGRAM_ENABLED`), 발송 기록 `ops.notify_log`. 삭제: 나머지 6벌·curl 2곳 | P2(서비스), P3~P5(각 메시지 이전) |
| 수신 2벌: SD:`_telegram_setup_webhook`:5882 + `/api/telegram/webhook`:5860 → `_handle_telegram_command`:5823 / ET:`board/ingest/tg_inbox.py:drain`:436(getUpdates) | **notifier 웹훅 1개**(새로 작성) + SD 명령 처리기 이식 + ET `tg_inbox` 의 `parse_update`·`oembed`·`merge` 재사용 | 같은 봇에서 웹훅과 getUpdates는 공존 불가(ET 스스로 409 검사 `tg_inbox.py:153-155`). SD 명령 처리기는 소유자 chat_id + 시크릿 헤더 검증이 있다. ET 인박스 시험 `test_tg_inbox` 39 | 이행 순서는 `inventory.md` (d-2). SD 시크릿 파생(`_telegram_secret`:5731, 봇 토큰 해시) → 독립 비밀 `KBJ_TELEGRAM_WEBHOOK_SECRET`. 테스트 엔드포인트 3개 + 발송을 일으키는 HTTP 3개(`POST /api/ops/brief/closing`:8493·`/api/agent/run`:12489·`/api/ops/cron/trigger/<job_id>`:18370, 모두 인증 없음) 삭제. ET `ingest/triggers.py:812` 의 getWebhookInfo 진단도 notifier 헬스체크로 옮긴다 | P2 |
| 중복 방지 6벌: SD `ops_state`(`send_closing_market_summary`), SD `_alert_cooldown_ok`:6102, ET `docs/api/sent.json`·`guru-sent.json`·`xdigest-sent.json`, bok `cache/telegram-sent.json`, Actions 캐시 `etf-sent-*` | `ops.notify_log` + 작업 등록부 규칙(하루 1회·쿨다운) | — | 파일 표식 전부 삭제 | P2 |
| 브리핑 중복(U2): 아침 SD 05:30·06:10·08:30, ET 06:40·07:00, GX 계획 06:05·08:30 / 마감 SD 15:40·15:45·16:00, ET 16:10, GX 계획 15:50 | **아침 브리핑 08:10 1회, 마감 요약 16:40 1회** [제안 시각] | — | 섹션 구성·흡수 목록은 `inventory.md` (c-3) | 마감 P3, 아침 P5 |

### 1.4 스케줄러 3종 통합

| 지금 구현들 | 정본 | 근거 | 나머지 처리 | 단계 |
|---|---|---|---|---|
| ① 프로세스 내 APScheduler: SD:`server.py:_startup`:6919 안 `add_job` 43회(7072~7517행, 직접 셈), 서버 TZ(Asia/Seoul)에 기댐 + 부팅마다 도는 1회성 스레드 묶음(Gist 복원·setWebhook·일봉 채움·시황 캐치업 등, `inventory.md` (c-1)) + HTTP 수동 실행 `POST /api/ops/cron/trigger/<job_id>`:18370(인증 없음) ② 외부 cron: SD `wake.yml`, ET Actions 정기 8개(`bok`·`daily`·`kr`·`flow`·`us-board`·`xdigest`·`report`·`artifacts-gc`, `board` 크론은 꺼짐), 맥 crontab(SD 18:00), 맥 launchd(ET 16:10), cron-job.org dispatch(`board-daily`·`board-send`·`xdigest-*`·`etf-daily`), 레포 밖 Claude 루틴 17:09 ③ GX 상주 스케줄러: GX:`services/scheduler/service.py:422`(1초 상태 머신, `KrxDaily`·`MinuteDaily`·`GapDaily`·`OpenCheck`·`MasterKrxReport`) | **GX `services/scheduler`** + 새 **작업 등록부** `config/jobs.yaml`(이름·시각 또는 세션 트리거·거래일 조건·의존·재시도·마감·받는 데이터 키·발송 토픽) | GX 스케줄러 시험: `test_scheduler_service` 23, `_krx` 37, `_minute` 46, `_gaps` 14, `_open_check` 10 + 통합. 캘린더로 거래일·야간·만기를 판정하고 가짜 시계로 시험한다. SD APScheduler·ET cron은 휴장 판정이 각자이고 시험 0 | APScheduler·Actions cron·launchd·crontab·외부 dispatch 전부 폐기. '받는 데이터 키'가 같은 작업은 등록부 검사에서 실패시킨다(PLAN §2 "같은 데이터를 두 작업이 받지 않게"). 통합 후 시간표 초안은 `inventory.md` (c-3) | P2(등록부·엔진), 작업 이전은 각 영역 단계 |

### 1.5 DB 이관 (SQLite·JSON → PostgreSQL + TimescaleDB 하나)

**스키마 이름 [제안]**: DATA_TIERS §3의 `public`/`private` 와 PLAN §2의 도메인 스키마를 합쳐 `pub_<도메인>`·`prv_<도메인>`·`ops` 로 둔다. ⚠ DATA_TIERS 원문대로 `public` 이라 이름 붙이면 Postgres 기본 스키마 `public`(확장·`search_path` 기본값)과 겹친다. PLAN §2 목록에 없는 도메인 `macro`·`themes`·`etf`·`journal` 은 추가 제안이다.

| 원본(레포:DB) | 표 | → KBJ 스키마.표 | 변환 | 단계 |
|---|---|---|---|---|
| SD `dashboard.db` `stocks` + ET `board.db` `snap` + `us_board.db` `snap` | 당일 스냅 | `prv_market.stock_snapshot`(market, code, trade_date, source, quality) | 날짜 TEXT→date, 코드 앞자리 0 유지(text) | P2 |
| SD `ohlcv` + ET `px`(board·us·backtest) + ETF `etf_px` + kr `cache/daily` + flowlab `cache/prices` | 일봉 | `prv_market.daily_bar`(hypertable, asset=stock·etf, adjusted 여부, source) | 네이버 출처 행은 이관하지 않고 KRX·KIS로 다시 받는다(U4) | P2 |
| SD `chart_cache`, `misc_cache`, `yinfo_cache`, `discover_results` | 캐시 | 이관 안 함(Redis·재계산) | — | — |
| SD `financial` | 네이버 재무비율 | 이관 안 함(U4) → `pub_fin.ratio`(DART 재무만으로 계산한 값) / 시세가 들어가는 PER·PBR·배당수익률과 KIS 응답 값은 `prv_fin.ratio`(DATA_TIERS: KIS 로그인) | — | P4 |
| SD `flow_cache` + ET `state/*/stockflows.json`·`flows.json`, kr `cache/flows.json` | 종목 수급 | `prv_flows.stock_investor_daily`(code, trade_date, investor, net_qty, net_value, unit, source) | 네이버 행은 `quality=estimated` 로만 보관하거나 버림 | P2 |
| GX `investor_flow` | 시장 투자자(주기 수집) | `prv_flows.market_investor_intraday`(GX 열 그대로) | — | P2 |
| SD `alert_rules` + `cache/alert_rules.json` + localStorage | 알림 규칙 3곳 | `prv_alerts.rule`(JSON DSL) | 셋 중 최신 우선 병합 | P8 |
| SD `alert_history`, `alert_history_v2` | 발송 이력 2벌 | `prv_alerts.event` | 두 표 합침 | P8 |
| SD `dart_corp_map` + ET `state/.dart_corp.json` + dart-report `.cache/corp*` | corp_code 3벌 | `pub_filings.corp_code` | 원본 corpCode.xml로 다시 받음 | P2 |
| SD `disclosure_history`, `dart_disclosure_overhang`, `earnings_actual` | 공시·오버행·잠정실적 | `pub_filings.disclosure`, `.overhang`, `.earnings_actual` | — | P4 |
| SD `financial_quarterly` + ET `state/financials.json` + dart-report 출력 | 분기 재무 3벌 | `pub_fin.quarterly` | 환산식 3벌 차이는 §4 Q4 | P4 |
| SD `consensus_snapshot`·`revision_alerts`(⚠ `schema.sql` 과 `migrations/008` 에 같은 정의 2번), `consensus_estimate`, `consensus_quarterly` | 컨센서스·리비전 | `prv_fin.consensus_snapshot`, `.consensus_quarterly`, `.revision` | 정의 하나로. `source='naver'` 행은 표시만 남기고 새 출처로 이어 쌓기 | P4 |
| SD `valuation_band`, `earnings_surprise` | 밴드·서프라이즈 | `prv_fin.valuation_band`, `.earnings_surprise` | — | P4 |
| SD `earnings_alert_queue` | 어닝 알림 큐 | `prv_alerts.queue` | — | P4 |
| SD `index_universe` + `naver_universe_*` + ET universe.json + kr 필터 | 유니버스 4벌 | `prv_market.universe`(code, 기준일, 플래그별 열) | §4 Q11 | P2 |
| SD `analysis_journal` + ET `notes/*.yaml` | 분석 일지·노트 | `prv_journal.analysis` | — | P8 |
| SD `recommendation_history`, `trade_journal` + `server_portfolio.json`·`server_watchlist.json`·localStorage | 추천·매매·포트폴리오·관심종목 | `prv_journal.recommendation`, `.trade`, `.portfolio`, `.watchlist` | — | P8 |
| SD `ops_state`, `fetch_progress` + ET `meta`·`run_log` | 운영 | `ops.kv`, `ops.job_run` | — | P2 |
| SD `llm_cache.db` | LLM 캐시 | 폐기 | — | — |
| ET `alltime`, `label`(+us `label`·`lowlabel`), `split_check` | 신고가 상태 | `prv_board.alltime`, `.label`(market 열로 KR·US 합침, kind에 w52_low 포함), `.split_check` | ⚠ 같은 이름 표가 KR·US 두 DB에 있던 것을 market 열로 해소 | P3 |
| ET `sector_map` + `knowledge/*.yaml` | 분류 사전 | `pub_themes.*`(§1.8) | 자체 사전만. monitor/kr `themes.json`·`industries.json`(제3자 추출, 라이선스 미확인)은 확인 전까지 `prv_themes.*` | P5 |
| ET `state/<날짜>/*.json` | 단계 산출 | `prv_board.artifact`(trade_date, kind, payload jsonb) | — | P3 |
| ETF `fund`, `holding`, `etf_ticker`, `change_log`, `etf_meta`, `etf_aum` / `etf_live` | ETF | `prv_etf.*` / `etf_live` 폐기(쓰는 곳 없음) | — | P5 |
| ET bok `cache/*.json` | 매크로 시계열 | `pub_macro.series`(ECOS 한은 작성·FRED 정부·재무부[DATA_TIERS 미기재 — §1 행 추가 필요]) / `prv_macro.series`(Yahoo·FF·타기관 ECOS 802Y001·731Y001·901Y056) | 시리즈 단위 등급 | P5 |
| GX `raw_messages`~`oi_changes`(21표) | GEX | `prv_gex.*`(이름 그대로); `session_log`·`health_events`·`collection_gaps`·`quarantine`·`collection_reports` 는 `ops.*` | 이미 Postgres, 스키마 이동만 | P2(골격)·P7 |
| GX `schema_migrations` | 마이그레이션 기록 | `ops.schema_migrations` | GX `db/migrate.py` 방식(번호·체크섬) 승계 | P1 |

이관 스크립트 [제안]: `kbj/store/migrate_legacy.py` 가 legacy SQLite를 읽기 전용으로 열고 표마다 행 수·키 합계를 대조한 뒤 COPY한다. 다시 돌려도 같은 결과(멱등). 원본 사본 선택은 §4 Q12.

| 지금 구현들 | 정본 | 근거 | 나머지 처리 | 단계 |
|---|---|---|---|---|
| SQLite 5개(SD `dashboard.db`·`llm_cache.db`, ET `board.db`·`us_board.db`·`backtest.db`·`etf.db`), JSON state 다수, GX Postgres+Timescale, 백업 3벌(SD Gist·ET git push+Actions 캐시·GX 계획 pg_dump) | **GX `data/store.py`·`data/spool.py`·`db/migrate.py`**(Postgres+Timescale, 디스크 스풀) | `test_store_sql` 66 + `test_spool` 38 + 통합 `test_store` 27(Docker). 모든 표에 `quality` 열, 장애 시 스풀 | Gist 백업(`db_backup.py`) 폐기, Actions 캐시·아티팩트 DB 폐기, 백업은 `pg_dump` 하나 | P2 |

### 1.6 거래 캘린더

| 지금 구현들 | 정본 | 근거 | 나머지 처리 | 단계 |
|---|---|---|---|---|
| SD:`server.py:_KR_HOLIDAYS_2026`:17971(**2026년만** 하드코딩)·`_is_kr_holiday`:17987·`is_market_hours`:174, SD:`kis_api.py:_is_kr_market_hours`:146 · ET:`board/engine/db.py:trading_days`:202(일봉 날짜 합집합), `board/us/db.py:156`, `etf_tracker_v9/tracker.py:prev_trading_day`:566(DB 날짜), `monitor/flow/narrative.py:prev_trading_day`:73(데이터 날짜), `board/run.py` `only_fresh` 휴장 추정(:1086), bok `send-telegram.js --morning-only`(평일 판정), guru `run_weekdays` · GX:`core/calendar.py`(`TradingCalendar`, `state_at`, `expiry_at`, `night_session_opens`) + `config/holidays_override.yaml` | **GX `core/calendar.py`** | `test_calendar` 36 + 속성 7 + 스케줄러 시험. XKRX(exchange_calendars) + 덮어쓰기, 야간 T+1 귀속·만기·대체공휴일 실측(2026 추석·10/5 대체공휴일 `test_dependencies.py`) | SD 하드코딩 3곳 삭제(2027년 누락 위험). ET의 데이터 기반 함수(`trading_days` 등)는 '데이터가 있는 날' 판정으로 남기되 휴장 판정은 캘린더로. 미국 일정은 XNYS 캘린더 추가 [제안]. `now_kst` 6벌(sweep G-2) → `kbj/core/time.py` | P2 |

### 1.7 52주·60일·역사적 신고가

| 지금 구현들 | 정본 | 근거 | 나머지 처리 | 단계 |
|---|---|---|---|---|
| SD:`server.py:build_market_summary`:13630 안 SQL(:13912, 종가만, `c >= h`, DB 일봉 범위 안의 '역사적', 1,000원 미만 제외), `_kr_new_highs_from_charts`:10835, `_prewarm_new_highs`:10944, `_newhigh_flow`:13315, `_us_new_highs_from_yinfo`:10972(95% 근접), `data_fetcher.fetch_new_highs`:422 · ET:`board/engine/newhigh.py:evaluate`:181(`cur > ref` 엄격, 종가·고가, `alltime` 상장 이후, `split_guard` ±31% + DART 대조), `board/us/engine.py`(엔진 공유) · ET:`flowlab/eventstudy.py:_events_for`:70(자체 재검출), `flowlab/verify.py:check_newhigh`:132(독립 재현) | **ET `board/engine/newhigh.py`** (+`aggregate.py`·`rankings.py`·`build.py`) | `test_newhigh` 35·`test_basis` 12·`test_stale_px` 26·`test_mktcap_floor` 28·`test_close_source` 25·`test_init_rebuild` 4 + flowlab 독립 재현 누락 0(`flowlab/ci-out/verify.txt` PASS). SD는 검사 스크립트 3개(`check_new_high_logic` 20·`check_newhigh_full_list` 35·`check_newhigh_flow` 19 검사) | SD 신고가 SQL·`_kr_new_highs_from_charts`·`_prewarm_new_highs`·`fetch_new_highs` 삭제(마감 요약 '신고가' 섹션은 board로). `_newhigh_flow` 는 수급 엔진으로. flowlab `verify.check_newhigh` 는 **시험 오라클로 유지**, `eventstudy` 의 재검출은 엔진 호출로 교체. SD `_us_new_highs_from_yinfo` 는 board/us로 대체(§4 Q10). 동률·역사적 정의 차이는 §4 Q2 | P3(골든: 기존 board 산출과 같음) |

### 1.8 테마·밸류체인·업종 분류 사전

| 지금 구현들 | 정본 | 근거 | 나머지 처리 | 단계 |
|---|---|---|---|---|
| SD:`themes_mapping.json`(26 테마, 네이버 크롤 `theme_crawler.py`), SD:`data/valuechain_map.json`(5 테마 × 레이어 × 세그먼트, 키워드, v2.0) + `valuechain.py:load_valuechain_map`, `universe_manager.sync_valuechain_to_universe` · ET:`board/knowledge/sectors.yaml`(board48 체계, 1:1 커버리지) + `sector_map.yaml`(3,924종목: LLM 3,921·사람 3) + `themes.yaml`(62 테마, 사슬 역할별 seeds) + `engine/themes.py`·`kinds.py` · ET:`monitor/kr/knowledge/themes.json`(테마 198·밸류체인 23, `rsm0kk/kr-sector` 배포본 추출) + `industries.json`(업종 58·배정 639) + `industries.learn_ksic` · ET:`etf_tracker_v9/themes.py:classify`:96, `monitor/kr/etf.py:classify`:109(ETF 이름 분류 2벌) | **새 분류 사전 `kbj/engines/themes`**(테마·체인·종목·근거·출처 한 스키마, `pub_themes`). 섹터 1:1은 **ET board48**(`sectors.yaml`·`sector_map.yaml`) | board48은 전 종목 정확히 하나씩(커버리지 보장), 근거(`why`)·출처(`source`)·신뢰도 열이 있고 시험 `test_sector_map` 12·`test_seeds` 13·`test_kinds` 22. 테마 다:다 기준은 애매 → §4 Q5·Q6 | SD `themes_mapping.json`·`theme_crawler.py` 폐기(U4 네이버). SD `valuechain_map` 의 레이어·세그먼트·키워드는 체인 상세로 흡수. ⚠ 공개(`pub_themes`)에는 자체 사전(board48·board `themes.yaml`·SD `valuechain_map`)만 올린다 — monitor/kr 198·23 사전은 제3자 추출이라 라이선스 확인 전까지 로그인(`prv_themes`, DATA_TIERS '애매하면 로그인'). ETF 분류는 §4 Q8. 같은 종목이 서로 다른 테마로 나오는 충돌은 사전 단위 시험으로 0을 강제(PLAN P5 기준) | P5 |

### 1.9 기술적 지표

| 지금 구현들 | 정본 | 근거 | 나머지 처리 | 단계 |
|---|---|---|---|---|
| SD:`server.py:_calc_rsi_macd`:368(RSI 와일더, 워밍업 0.0, EMA 첫 값 시드), `_detect_divergences`:424, `_calc_adx`:486, `_calc_bollinger`:542(정수 반올림), `_calc_fibonacci`:557, `_find_best_trendline`:571, `_calc_trendlines`:596, `api_volume_profile`:5293, `_generate_macd_tags`:11733, `_bt_calc_rsi`:12572(**단순평균 RSI — 같은 파일 안 2벌**) · ET:`board/engine/systems.py:sma_arr`:64·`ema_arr`:75(SMA 시드)·`rsi_arr`:86(와일더, 워밍업 None)·`adx_arr`:106·`turtle_n`:138, `board/engine/signals.py`(`sma`·`pstdev`·`true_range`·`bb_width`·`squeeze_on`·`percentile_ranks`) · ET:`monitor/kr/engine.py`(`relative_strength`·`drawdown`·`momentum`) · GX:`core/metrics/vol.py:realized_vol` | **새 `kbj/core/indicators`**(순수 함수 + 단위·속성 시험). 시드 구현은 ET `systems.py`·`signals.py` [제안] | ET: `test_signals` 25·`test_systems` 15·`test_backtest` 13. SD: 시험 0. 관례 차이(EMA 시드·워밍업·반올림)로 값이 다르다 → §4 Q3 | SD 지표 함수는 `kbj.core.indicators` 호출로 교체 후 삭제. 피보나치·추세선·볼륨 프로파일·다이버전스는 SD에만 있어 그대로 옮겨 시험을 붙인다. kr `engine.py` 는 섹터 모니터 엔진으로 유지(RS 정의는 지표 모듈에 등록) | P4(차트), P8(알림·백테스트 같은 함수) |

### 1.10 수급

| 지금 구현들 | 정본 | 근거 | 나머지 처리 | 단계 |
|---|---|---|---|---|
| 수집: SD:`server.py:_fetch_naver_trend`:2797·`_fetch_and_save_flow`:2920·`_refresh_flow_batch`:3030·`api_flow`:3129(네이버), SD:`kis_api.get_investor_trading`:256(KIS) · ET:`board/ingest/kis.py:stock_flows`:254·`market_flows`:214·`top_flows`:282(KIS), `ingest/flows.py:fetch_market`:177·`fetch_stock`:236(네이버 폴백), `ingest/stockflows.py`(KIS 주·네이버 폴백) · ET:`monitor/kr/flows.py:collect`:137(board KIS), `monitor/flow/krx.py:fetch_daily`:196(KRX 정보데이터시스템 7구분, 러너 차단)·`kissrc.py:fetch_daily`:48(KIS 3구분 폴백) · ET:`flowlab/naver.py:investor_flows`:150 · GX:`services/poller`(FHPTJ04030000 60초) → `investor_flow` | 수집 **ET `board/ingest/kis.py`**(종목·시장) + **GX poller**(시장·파생 장중). 분석 **ET `monitor/flow/analyze.py`**(누적·매수일수·기여율·KRX 원 단위 대조), 이벤트 스터디 **ET `flowlab/eventstudy.py`**(분석 모듈) | `test_kis_call` 32·`test_market_flows` 21·`test_stockflows` 58·`test_flows_parse` 14, monitor/flow `test_analyze` 17(골든)·`test_kis` 14(단위 검산 `unit_check`), GX `test_engine_investor` 11. KIS 금액은 실제 거래대금, 네이버는 주수×종가 추정(`is_estimate`) | 네이버 경로 전부 삭제(U4: SD 4곳, ET `flows.py`·`stockflows.naver_flows`·`flowlab/naver.py`). KRX 정보데이터시스템 스크랩(`monitor/flow/krx.py`·`capture.py`, `flowlab/krx.py`)은 KIS로 교체(§4 Q7). SD `_flow_streak`:15091·`_analyze_flow_signals`:15104(쌍끌이·연속·반전)는 규칙 엔진 내장 규칙으로 | P3(수집·페이지 4), P8(규칙) |

### 1.11 KRX OpenAPI 클라이언트

| 지금 구현들 | 정본 | 근거 | 나머지 처리 | 단계 |
|---|---|---|---|---|
| SD:`krx_api.py:krx_api_call`:59(`2**attempt` 재시도, 0.2초 스로틀, 일 캐시) + 엔드포인트 9개(`sto/stk·ksq·knx_bydd_trd`, `*_isu_base_info`, `idx/kospi·kosdaq·krx_dd_trd`, `etp/etf_bydd_trd`) · ET:`board/ingest/krx.py:fetch_day`·`fetch_index`(`stk_bydd_trd`·`ksq_bydd_trd`·`krx_dd_trd` 파서), `ingest/pipeline.py:_apply_krx_snapshot`·`krx_regular_day`(D-080) · ET:`flowlab/probe_market_official.py`(투자자 경로 추측 진단) · GX:`data/krx/eod.py:KrxClient`(`drv/fut·opt_bydd_trd`, 64MiB 상한), `services/scheduler/krx.py:KrxCallBudget`(Redis `krx:calls:<날짜>`, 일 상한 200) | **GX `data/krx/eod.py` 클라이언트 + 호출 예산** + **ET `board/ingest/krx.py` 주식 파서** + SD 엔드포인트 목록을 메서드로 추가 | GX `test_krx_client` 7·`test_krx_models` 16·`test_scheduler_krx` 37. ET `test_close_source` 25(확정치 덮기). SD 시험 0. 키 하나의 일 10,000회 한도를 GX 예산이 Redis로 센다 | SD `krx_api.py` 삭제. flowlab probe는 기록만. KRX OpenAPI에 투자자별 데이터가 없다는 점은 GX PLAN #16 '투자자별 데이터 미제공'과 일치 → 수급은 KIS. 공표 시각 D+1 08:00(E1) | P2 |

### 1.12 DART 클라이언트

| 지금 구현들 | 정본 | 근거 | 나머지 처리 | 단계 |
|---|---|---|---|---|
| SD:`server.py:poll_dart_disclosures`:10294(매분)·`_load_dart_corp_code_map`:10453·`init_dart_corp_map_db`:10267·`_fetch_dart_quarter`:10552·`_try_dart_segment_revenue`:10581·`_fetch_kr_earnings`:11174·`score_disclosure`:10102, SD:`dart_collector.py`(aiohttp+tenacity, **requirements 누락**), `earnings_parser.py`, `overhang_parser.py` · ET:`board/ingest/dart.py`(`corp_codes`:75·`company`·`disclosures`·`disclosures_for`·`stock_actions`), `ingest/financials.py`(`fnlttMultiAcnt`, 계정 동의어는 dart-report 사본이라고 주석), `ingest/triggers.py:_dart`, `run.py:_dart_ksic` · ET:`dart-report/dartreport/client.py:DartClient`:43(디스크 캐시·0.12초 스로틀·013 처리·일 20,000 보호·키 길이 검사), `statements.py`·`costs.py`·`bridge.py`·`orders.py`·`excel.py` | 전송층 **ET `dart-report/dartreport/client.py`** + 도메인 함수 **ET `board/ingest/dart.py`** + 리포트 모듈 **dart-report**(엑셀 8시트) + 공시 피드·잠정실적·오버행 파서 **SD**(`poll_dart_disclosures`·`earnings_parser`·`overhang_parser` — 다른 곳에 없음) | `DartClient` 는 캐시·한도·013을 가장 꼼꼼히 다룬다(시험은 스모크뿐). board 함수는 `test_dart_for` 7·`test_split_actions` 7·`test_financials` 12. corp_code 캐시 3벌 → 1벌 | SD `dart_collector.py`(5년 분기) 폐기 → dart-report `statements` 로. corp_code는 `pub_filings.corp_code` 하나. 누적→분기 환산 3벌은 §4 Q4. dart-report `app.py:106` 오류 화면 키 노출 제거(Streamlit은 KBJ 웹으로 대체) | P2(클라이언트), P4(공시·재무·리포트) |

### 1.13 네이버 (U4: 로그인 전용, 데이터는 KRX·KIS로 교체, 스크래핑 미사용)

정본 없음 — 네이버 금융 스크래핑 코드는 전부 legacy에서 지운다. 기능별 대체 출처는 아래와 같다. TR·엔드포인트 가운데 지금 코드에 없는 것은 **[추정: 실측 필요]**.

| 기능 | 지금 구현(레포:파일:함수) | 대체 출처(KRX OpenAPI / KIS TR) | 비고 |
|---|---|---|---|
| 장중 전 종목 시세 폴링 | SD:`server.py:_fetch_naver_live_prices`:801·`_refresh_prices_from_naver`:8139·`_price_broadcaster`:6867, `data_fetcher.py` | 관심종목만 KIS `FHKST11300006`(관심종목 멀티 시세)[추정] / 단건 `FHKST01010100` | ⚠ 전 종목 장중 실시간은 대체 없음: KIS WS 41건은 GX 옵션이 이미 다 쓰고(GX `docs/probe_results.md` #11a), REST 초당 4건이면 2,700종목 1바퀴에 약 11분 |
| 장중 지수 | SD:`_fetch_kr_indices_live`:862 | KIS `FHPUP02100000`(국내업종 현재지수)[추정] | — |
| 당일 전 종목 종가·등락·거래대금 | SD:`_refresh_prices_from_naver`(15:35), ET:`board/ingest/naver.py:fetch_universe` | 당일: KIS `FHKST01010100` 종목별 / 익일 확정: KRX `/sto/stk_bydd_trd`·`/sto/ksq_bydd_trd`(D+1 08:00, E1) | §4 Q1 |
| 일봉 이력·백필 | SD:`ohlcv_autofill._fetch_naver`, ET:`board/ingest/naver.py:fetch_ohlcv`, `etf_tracker_v9/market.py:fetch_hist`:104, `flowlab/naver.py:daily_ohlcv`:85, `monitor/flow/run._ohlcv`:268 | KRX `/sto/stk_bydd_trd`·`/sto/ksq_bydd_trd` 날짜별(시장당 하루 1회, 1년 ≈ 500회) + KIS `FHKST03010100`(기간별 시세, 수정주가 선택)[추정] | ⚠ KRX 일별은 비수정 가격 — board `split_guard`·DART 대조를 그대로 쓴다 |
| 업종 지수 | SD:`_scrape_naver_sectors`:3217, ET:`naver.fetch_sector_index` | KRX `/idx/kospi_dd_trd`·`/idx/kosdaq_dd_trd`(업종지수 포함, SD `krx_api.py:15-16` 주석) + 장중 KIS `FHPUP02140000`(업종 구분별 전체시세)[추정] | — |
| 업종 구성종목 | SD:`_scrape_naver_sector_detail`:3303, ET:`naver.fetch_sector_members` | ⚠ KRX OpenAPI에 없음 → KIS 종목마스터(`kospi_code.mst` 업종 분류 코드)[추정] 또는 KBJ 분류 사전 board48 | — |
| 지수 일봉 | ET:`naver.fetch_index`, kr `ingest.collect_indices`:132 | KRX `/idx/kospi_dd_trd`·`/idx/kosdaq_dd_trd`·`/idx/krx_dd_trd` | — |
| 환율 | ET:`naver.fetch_fx` | ECOS 731Y003(한국은행 작성 — 공개)[실측 필요: 항목 코드, probe F6] | ⚠ 장중 환율은 대체 미확인(KIS 해외 TR [추정]) |
| 종목 투자자 수급 | SD:`_fetch_naver_trend`:2797·`api_flow`:3129, ET:`flows.fetch_stock`:236·`stockflows.naver_flows`, `flowlab/naver.py:investor_flows`:150 | KIS `FHKST01010900`(이미 board 주 소스, 최근 30일) + 기간·기관 세부 KIS `FHPTJ04160001`(종목별 투자자 일별)[추정] | §4 Q7 |
| 시장 투자자 수급 | ET:`flows.fetch_market`:177 | KIS `FHPTJ04040000`(이미 board 주 소스) | — |
| ETF·ETN 목록, ETF 시세·NAV·시총·좌수 | ET:`board/ingest/funds.py:fetch_etf_codes`, `etf_tracker_v9/market.py:fetch_list`:44·`sync_meta_aum`:61, SD:`_scrape_etf_price`:6573 | KRX `/etp/etf_bydd_trd`·`/etp/etn_bydd_trd`(일별) + 장중 KIS `FHPST02400000`(ETF/ETN 현재가)[추정] | — |
| ETF 구성종목 TOP10 폴백 | ET:`etf_tracker_v9/collectors.py:NaverTop10` | KIS `FHKST121600C0`(ETF 구성종목 시세)[추정] | 주 경로(운용사 어댑터)는 유지 |
| 종목명·유니버스 | SD:`_build_naver_universe_background`, `data/naver_universe_seed.json`, ET:`tracker.naver_names`:99 | KRX `/sto/stk_isu_base_info`·`/sto/ksq_isu_base_info` + KIS 종목마스터 | — |
| 분봉 | SD:`_fetch_naver_minute_candles`:4163 | KIS `FHKST03010200`(이미 SD `kis_api.get_minute_chart`) | — |
| 시간외 단일가 | SD:`price_sync_afterhours`(16~17시) | KIS `FHPST02300000`(시간외 현재가)[추정] | — |
| 재무비율(PER·EPS·PBR·BPS·배당·외인비율) | SD:`api_financial`:3854 | KIS `FHKST01010100` 응답의 PER·PBR·EPS·BPS[추정: 필드] + DART 재무 계산 + KIS `FHKST66430300`(재무비율)[추정] | — |
| 컨센서스(Fwd EPS·목표가·분기 컨센) | SD:`consensus_collector.fetch_naver_consensus`, `consensus_quarterly_collector.fetch_naver_main`, `consensus_snapshot_collector.run_daily_snapshot` | KIS `FHKST668300C0`(종목 추정실적) + `FHKST663300C0`(종목 투자의견·목표가)[추정] | ⚠ 커버리지·분기 단위 미확인. 확인 전에는 리비전 기능을 '출처 미확보'로 비워 둔다(추정치로 채우지 않음) |
| 증권사 리포트 목록 | SD:`_crawl_naver_research`:2351·`_scrape_naver_research_list`:9230·`alert_new_reports`:6282 | — | ⚠ **대체 없음** → 기능 폐지 |
| 종목토론실 감성 | SD:`_fetch_naver_board_titles`:15419·`_score_sentiment_titles`:15455 | — | ⚠ **대체 없음** → 기능 폐지 |
| 테마-종목 매핑 | SD:`theme_crawler.py` | — (외부 출처 없음) | ⚠ 외부 대체 없음 → KBJ 분류 사전(§1.8)으로 |
| 종목 뉴스·트리거 | ET:`board/ingest/triggers.py`(네이버 종목 뉴스 URL), 네이버 검색 API 4곳(SD `api_news`:2270·`valuechain._fetch_naver_news`·`agents/pipeline.agent1_news`, ET `ingest/news.py`) | KIS `FHKST01011800`(종합 시황·공시 제목)[추정] + DART 공시 | 네이버 **검색 API**는 스크래핑이 아닌 공식 API로 DATA_TIERS상 로그인 등급이다. U4대로 로그인 전용. ⚠ 테마 키워드 뉴스 검색은 KIS로 대체 불가 |

pykrx·KRX 정보데이터시스템 스크랩(네이버는 아니지만 같은 성격): SD `_pykrx_call`:248 외, `ohlcv_5y_collector.py`, ET `monitor/flow/krx.py`, `flowlab/krx.py:shorts`(공매도) → 위 KRX OpenAPI·KIS로 교체 [제안]. 공매도는 ⚠ KRX OpenAPI에 없음 → KIS 공매도 일별 TR [추정].

### 1.14 ECOS·FRED·매크로

| 지금 구현들 | 정본 | 근거 | 나머지 처리 | 단계 |
|---|---|---|---|---|
| ECOS: ET `monitor/bok/fetch-ecos.js`(8계열, 키가 URL 경로) — 유일 구현. FRED: `fetch-fred.js`(74계열). 재무부·뉴욕연은: `fetch-rates.js`. FF 컨센서스: `fetch-consensus.js`. Yahoo: `fetch-yahoo.js`. SD 매크로: `server.py:api_macro`:9982·`_refresh_global_data_periodic`:6454(yfinance: USD/KRW·WTI·VIX·美10년) | **ET `monitor/bok/fetch-*.js` 로직을 Python 어댑터로 이식**(`kbj/data/public/ecos.py`·`fred_gov.py`·`treasury.py`, `kbj/data/private/yahoo.py`·`ff.py`) | ECOS·FRED 구현은 bok뿐. 실패 시 지난 캐시 유지·INFO-200 처리가 있다. 시리즈별 등급은 `probe_results.md` §4.3(한은 작성 표만 공개, 802Y001 로그인) | Node 런타임 제거 [제안]. SD yfinance 매크로는 §4 Q9 결정 후 하나로. 신용스프레드는 ECOS 817Y002(소매채권 폐지, F1) | P5 |

### 1.15 공공데이터포털

| 지금 구현들 | 정본 | 근거 | 나머지 처리 | 단계 |
|---|---|---|---|---|
| ET `board/ingest/datago.py`(옛 V1 경로, Encoding/Decoding 키 판별 `key_forms`), `monitor/kr/ingest.py`(경유), `flowlab/probe_market_*.py` | **ET `board/ingest/datago.py`** 의 키 판별·호출부 + V2 경로(F7) + 관세청·금투협 XML 파서(신규) | 키 형태 판별이 이미 해결돼 있다(`probe_results.md` §1). 금융위 주식시세는 로그인 등급(4유형) | 주식시세 어댑터는 `data/private`, 관세청·금투협은 `data/public` 으로 폴더 분리(DATA_TIERS §3) | P2, P6 |

### 1.16 그 밖의 영역

| 영역 | 지금 구현들 | 정본 | 근거 | 나머지 처리 | 단계 |
|---|---|---|---|---|---|
| 웹 서버·화면 | SD Flask `server.py`(라우트 156) + SPA(`static/js/*`), ET board 정적 사이트(`web/*`)·로컬 서버(`web/serve.py`), bok·kr·ETF 정적 HTML, dart-report Streamlit, GX FastAPI 설계(`docs/phase4_design.md`) | **FastAPI 1개 + MD6형 SPA 1개**(PLAN §2) | GX 설계가 API·WS·kill 스위치까지 정의 | SD 정적 catch-all(`server.py:4557`)·인증 없는 쓰기 API는 옮기지 않는다 | P3 |
| 설정·비밀·마스킹 | SD `.env` 로더 7곳, ET `board/ingest/creds.py`(`mask` 가 **앞 4자 노출**), bok `readKey`, GX `config/settings.py`(pydantic, `SecretStr`, `_redact`) | **GX `config/settings.py`** 방식 + `KBJ_` 접두사 | 절대 규칙 5와 맞는 것은 GX뿐 | 환경변수 대응표는 `inventory.md` (f) | P1 |
| HTTP 공용층 | ET `board/ingest/http.py`(`get` 재시도·`gather` 8 worker·`scrub`·`why`), GX httpx 클라이언트(타임아웃·크기 상한), SD requests 직접 | **GX httpx** + ET `scrub`·`why`(오류 사유 문구) | ET `test_http_reason` 19 | — | P2 |
| LLM 문장화 | ET `board/writer/*`(+`verify.check`), `classify/sectors.py`(Batch), `guru/*`, `xdigest/*`, SD `earnings_alert_writer.py`(Ollama) | **ET `board/writer` + `verify`** | `test_verify` 45, 수치·종목명 기계 대조(절대 규칙 3) | SD Ollama 폐기(템플릿 유지), xdigest 폐기 [제안] | P3 |
| 백테스트 | SD `api_backtest`:12742(태그 기반, RSI 따로 구현), ET `engine/backtest.py`·`systems.py`·`search.py`(봉인 구간)·`ledger.py` | **ET board 엔진** | `test_backtest` 13·`test_systems` 15·`test_search` 6·`test_ledger` 4, 미래참조·생존편향 규칙 주석 | SD 백테스트 폐기, 화면은 페이지 12 | P8 |
| ETF | ET `etf_tracker_v9`(PDF·변동·리포트), ET `monitor/kr/etf.py`(ETF 탭), SD `/api/etf_map`·`mark_etf_stocks`:7574(이름 패턴), ET `board/ingest/funds.py`(ETF·ETN 제외) | 구성종목 **ET `etf_tracker_v9`**, 목록·시세 **KRX `etp/etf_bydd_trd`**, 판정 **KRX 목록** | ETF 트래커만 PDF를 다룸(시험 0 → P5에서 시험 추가 필요) | SD 이름 패턴·board `funds` 는 KRX 목록으로 교체. 분류 2벌은 §4 Q8 | P5 |
| 종목 종류·유니버스 필터 | SD `stocks` 필터(시총 1,000억·ETF 제외·1,000원 이상), ET `engine/kinds.py`(보통주·우선주·스팩·리츠), `newhigh` 시총·거래대금 하한, kr `engine.filter_universe`:289 | 종류 **ET `engine/kinds.py`** | `test_kinds` 22·`test_mktcap_floor` 28·`test_universe` 25 | 필터 하한은 §4 Q11 | P2 |

---

## 2. fixture·제3자 코드 (U3 — 처리 방침은 메인 대화에서 확정, 여기서는 표만)

등급은 DATA_TIERS 기준. 크기는 바이트(디렉터리는 합계). "쓰는 테스트"는 파일을 직접 읽는 시험과 그 fixture를 감싼 가짜 서버를 거쳐 쓰는 시험을 함께 적었다.

| 경로 | 출처 | DATA_TIERS 등급 | 크기 | 쓰는 테스트 |
|---|---|---|---|---|
| GX `tests/fixtures/kis/callput_202610.json` | KIS 실측 전광판 콜·풋(2026-09 probe 발췌) | 로그인 | 37,845 | `unit/test_kis_master`, `unit/test_kis_models`, `fakes/kis_server.py` 경유 시험 다수(`test_poller_*`·`test_scheduler_*`·`integration/test_full_day` 등) |
| GX `tests/fixtures/kis/callput_wkm_260904.json` | KIS 실측(위클리) | 로그인 | 38,133 | `golden/synthetic.py`, `unit/test_kis_master`, `unit/test_kis_models`, `fakes/kis_server.py` |
| GX `tests/fixtures/kis/futures_board.json` | KIS 실측 선물 전광판 | 로그인 | 1,267 | `golden/synthetic.py`, `unit/test_kis_models`, `fakes/kis_server.py` |
| GX `tests/fixtures/kis/investor.json` | KIS 실측 투자자 | 로그인 | 13,818 | `golden/synthetic.py`, `unit/test_kis_models`, `fakes/kis_server.py`, `scripts/metric_report.py` |
| GX `tests/fixtures/kis/master_lines.json` | KIS 마스터 zip 발췌(공개 다운로드) | 로그인[애매] | 8,909 | `integration/test_store`, `unit/test_store_sql`, `unit/test_kis_master` |
| GX `tests/fixtures/kis/minute_day.json` | KIS 실측 분봉 | 로그인 | 1,534 | `golden/synthetic.py`, `integration/test_store`, `unit/test_store_sql`·`test_kis_models`·`test_futures_metrics`·`test_engine_futures`, `fakes/kis_server.py` |
| GX `tests/fixtures/kis/option_list.json` | KIS 실측 월물리스트 | 로그인 | 1,475 | `golden/synthetic.py`, `unit/test_kis_models`, `fakes/kis_server.py` |
| GX `tests/fixtures/kis/price_options.json` | KIS 실측 옵션 단건 현재가 | 로그인 | 5,581 | `golden/synthetic.py`, `unit/test_kis_models`, `fakes/kis_server.py` |
| GX `tests/fixtures/krx/fut_daily.json` | KRX OpenAPI 발췌 | 로그인 | 1,343 | `integration/test_store`, `unit/test_store_sql`·`test_krx_models`, `fakes/krx_server.py`(→ `test_scheduler_krx` 단위·통합) |
| GX `tests/fixtures/krx/opt_daily.json` | KRX OpenAPI 발췌 | 로그인 | 8,120 | `integration/test_engine_store`·`test_store`, `unit/test_krx_client`·`test_store_sql`·`test_krx_models`, `fakes/krx_server.py` |
| GX `tests/fixtures/validation/chain_snapshot_20260928_1427_small.json` | KIS 실측 체인 스냅(2026-09-28) | 로그인 | 15,337 | `golden/test_chain_subset`, `unit/test_metric_report`·`test_validate_greeks`, `scripts/make_golden.CORE_FIXTURES` 경유 `golden/test_core_golden`·`unit/test_engine_extended`·`test_engine_evaluate`·`test_shadow_report`·`integration/test_shadow_report` |
| GX `tests/fixtures/validation/chain_snapshot_20260928_1452_small.json` | 같음 | 로그인 | 15,337 | 같음 |
| GX `tests/golden/core/chain_20260928_small.json` | 위 실측 체인으로 만든 골든 출력 | 로그인[추정: 입력 시세 포함 여부 확인 필요] | 133,004 | `golden/test_core_golden`, `unit/test_fixtures_clean` |
| GX `tests/golden/raw/synthetic_20260928.jsonl`, `.parsed.json` | 합성(REST 본문 몇 행은 실측 fixture에서 옮김 — `golden/synthetic.py` 머리말) | 로그인[실측 행 포함] | 14,417 + 20,271 | `golden/test_raw_golden`, `integration/test_make_golden_db`, `unit/test_fixtures_clean` |
| GX `config/kis_ws_fields.yaml`, `config/kis_ws.yaml` | **제3자 코드**: KIS 공식 샘플 `koreainvestment/open-trading-api`(커밋 b4e6249)의 필드 순서 | 라이선스 확인 필요 | 7,049 + 6,028 | `unit/test_kis_ws`·`test_ws_client`·`test_store_sql`, `integration/test_store`·`test_full_day`, `fakes/ws_server.py` |
| ET `board/tests/fixtures/rankings_sample.json` | 보드 산출(실제 종목명·날짜·수치)[추정] | 로그인 | 45,276 | `test_telegram` |
| ET `board/tests/fixtures/rankings_tg_sample.json` | 같음 | 로그인 | 8,283 | `test_telegram` |
| ET `board/tests/fixtures/xdigest_render_posts.json` | 블루스카이 실제 계정 핸들·게시물 원문 | 로그인(제3자 게시물) | 68,759 | `test_xdigest_render`, `test_xdigest_preview` |
| ET `board/tests/fixtures/xdigest_render_facts.json` | 위 게시물의 LLM 분석 | 로그인 | 13,028 | `test_xdigest_render` |
| ET `board/tests/fixtures/xdigest_render_expected.txt`, `xdigest_render_sections_expected.txt` | 렌더 기대값(게시물 인용) | 로그인 | 8,867 + 9,622 | `test_xdigest_render` |
| ET `board/state/xdigest/`(커밋) | 블루스카이 게시물 원문·다이제스트 | 로그인 | 3,128,165 | `test_xdigest_preview`(건드리지 않는지 확인) |
| ET `board/state/guru/`(커밋) | LLM 웹 검색 조사 결과·월 비용 | 로그인[애매] | 45,560 | — |
| ET `board/notes/2026-09-21.yaml` | 사용자 판단 노트(개인) | 로그인(개인) | 5,305 | `test_note` |
| ET `board/knowledge/`(`sector_map.yaml` 등) | 자체(LLM 분류 + 사람 검수) | 공개(분류 사전, DATA_TIERS §2-9) | 606,899 | `test_sector_map`, `test_seeds` |
| ET `board/us/ci-out/` | Nasdaq 실데이터 CI 결과(파일 6개) | 로그인 | 16,163 | — |
| ET `board/us/knowledge/us_sectors.yaml`, `us_overrides.yaml` | 자체(나스닥 스크리너 industry → 18섹터 결정론 규칙 + 손수정) | 공개(분류 사전, DATA_TIERS §2-9) | 11,327 + 4,190 | — |
| ET `monitor/kr/tests/fixtures/golden.json` | **제3자** `rsm0kk/kr-sector` 배포본(2026-09-14) 값(시세 기반 집계) | 로그인 + 라이선스 미확인 | 207,998 | `test_engine`, `test_flows` |
| ET `monitor/flow/tests/fixtures/golden.json` | KRX 정보데이터시스템 워크북 실데이터(엑시콘 092870) | 로그인 | 17,846 | `test_analyze`, `test_narrative`, `test_run` |
| ET `monitor/kr/template.html` | **제3자** `rsm0kk/kr-sector` 빌드 산출물 | 라이선스 미확인 | 352KB(2,555줄) | `test_build`, `test_page` |
| ET `monitor/kr/knowledge/themes.json`, `industries.json` | **제3자에서 추출**(`rsm0kk/kr-sector`) + 학습 표 | 라이선스 미확인 | 93,321 | `test_industries`, `test_build` |
| ET `monitor/bok/*.js`, `template.html`, `usmacro.js` | **제3자** `rsm0kk/bok-monitor` 이식 | 라이선스 미확인 | JS 1,286줄 + 782줄 | — (시험 0) |
| ET `monitor/bok/data/` | 한은 보고서 원본 xlsx·md·그림 188장(공개) + 수출 104품목 xlsx(출처 불명확) | 공개(한은)/불명확 | 39,793,503 | — |
| ET `monitor/bok/cache/`(커밋) | ECOS·FRED(939KB)·Yahoo·재무부·FF·발송 기록 | 혼재(공개: 한은 작성 ECOS·정부 FRED·재무부 / 로그인: Yahoo·FF·저작권 FRED) | 1,144,434 | — |
| ET `docs/`(커밋) | 보드·bok·kr·미국 생성물, 랭킹 xlsx | 로그인(시세 기반) 혼재 | 26,767,695 | — |
| ET `flowlab/ci-out/`(커밋) | 네이버 수급·DART 재무·이벤트 스터디 결과 | 제외(네이버)/공개(DART) 혼재 | 3,314,336 | — |
| ET `etf_tracker_v9/docs/base.json` | 네이버 ETF 스냅샷 | 제외(네이버) | 약 254KB | — (`live_update.py` 가 읽음) |
| SD `data.json` | 시세·테마 스냅샷(pykrx·네이버) | 제외/로그인 | 107,438 | `scripts/test_data_json.py`(네트워크 필요) |
| SD `data/naver_universe_seed.json` | 네이버 수집 4,063종목 | 제외(네이버) | 403,173 | `scripts/check_etf_marking.py`, `scripts/check_ohlcv_autofill.py` |
| SD `themes_mapping.json` | 네이버 테마 크롤 | 제외(네이버) | 15,851 | `scripts/test_data_json.py`(네트워크 필요) |
| SD `data/valuechain_map.json` | 자체 작성 | 공개(분류 사전) | 55,907 | — |
| SD `data/valuechain_external_validation.json` | 네이버 coinfo '기업개요' 본문 발췌(`profile_excerpt`)를 담은 검증 결과(`scripts/validate_valuechain_external.py`) | **제외(네이버)** | 19,159 | — |
| SD `data/valuechain_llm_validation.json` | 로컬 Ollama 판정 결과(`scripts/validate_valuechain_llm.py`) | 로그인[애매] | 17,314 | — |
| SD `scripts/check_futures_us_index.py` 안의 KIS 응답 | 2026-09-23 러너 실측을 그대로 붙임(본문 주석) | 로그인 | (스크립트 안) | 자기 자신 |

---

## 3. P1 이식 순서

### 3.1 legacy 로 옮길 디렉터리

U3가 정해지기 전이므로 "U3 대기" 표시가 있는 파일은 옮길지 말지 메인 대화의 결정을 따른다. 공개 레포라면 DATA_TIERS §4로 처음부터 제외해야 하는 것도 같이 표시했다.

| KBJ 경로 | 원본 | 옮길 것 | 옮기지 않을 것(사유) | 비고 |
|---|---|---|---|---|
| `legacy/gexlab/` | GX 전체 | `core/` `data/` `db/` `services/` `scripts/` `config/` `tests/`(fixture 제외분) `docs/` `api/` `ui/` `live/` `exec/` `strategy/` `backtest/` `pyproject.toml`(→ 루트 lock으로 합침) `docker-compose.yml`·`Dockerfile`(참고) | `state/`·`probe_out/`(gitignore) | fixture 15개는 **U3 대기**(§2). P2에서 auth·scheduler·calendar·ratelimit·krx·store가 `kbj/` 로 승격된다 |
| `legacy/etf_traker/board/` | ET `board/` | 코드·`config/`·`knowledge/`·`tests/`·`docs/`(설계 문서) | `state/` 산출(gitignore 대상), `*.db` | `state/xdigest`·`state/guru`·`notes/`·`tests/fixtures/xdigest_*`·`rankings_*` **U3 대기** |
| `legacy/etf_traker/monitor/` | ET `monitor/` | `kr/` `flow/` `bok/` 코드·시험 | `bok/data/`·`bok/cache/`·`kr/cache`·`flow/out` | `kr/template.html`·`kr/knowledge/`·`bok/` 전체(제3자)·두 `golden.json` **U3 대기** |
| `legacy/etf_traker/flowlab/` | ET `flowlab/` | 코드·`tests.py` | `cache/`, `out/`, `ci-out/` | `ci-out/` **U3 대기** |
| `legacy/etf_traker/etf_tracker_v9/` | ET 같은 경로 | 코드·`adapters/` | `etf.db`, `docs/index.html` | `docs/base.json` **U3 대기** |
| `legacy/etf_traker/dart-report/` | ET 같은 경로 | `dartreport/`·`config/`·`run.py`·`app.py`·`tests_smoke.py` | `.cache/`, `out/` | 디렉터리 이름의 `-` 는 import 대상이 아니라 그대로 둔다(`run.py` 가 자기 폴더 기준 import) |
| `legacy/etf_traker/.github/workflows/` | ET 워크플로 16개 | 시험이 읽는 yml(`test_workflow_modes`·`test_xdigest_schedule`·`test_cli_help` 가 레포 루트 기준 경로로 읽음) | — | 루트 `.github` 가 아니라 실행되지 않는다. P1 "경로만 고친다" 원칙 |
| `legacy/stock_dashboard/` | SD 전체 | `*.py`, `agents/`, `db/`(코드·`schema.sql`), `migrations/`, `scripts/`, `static/`, `index.html`, `requirements.txt` | `cache/`, `db/*.db`, `.github/workflows/`(Render 전용) | `data.json`·`data/*.json`·`themes_mapping.json` **U3 대기**(`check_etf_marking`·`check_ohlcv_autofill` 이 `naver_universe_seed.json` 을 읽는다) |
| (옮기지 않음) | ET `docs/`(생성물 26.8MB), SD `render.yaml` | — | 생성물·배포 설정 | 보관만 |

### 3.2 프로젝트별 테스트 실행 명령과 지금 테스트 수 (2026-10-06 직접 실행)

실행 위치: `/tmp/p0test/<레포>` 사본(원본 `/home/user/p0src` 는 읽기만). Python 3.11 venv와 3.12 단일 lock venv 둘 다에서 돌렸다. SD 검사 스크립트 7개는 `/home/user/stock-dashboard/server.py` 절대경로를 하드코딩하고 있어 **사본 안에서만** 경로를 바꿔 돌렸다(이 세션의 작업 디렉터리 `/home/user/stock-dashboard` 는 스냅샷과 다른 커밋이다).

| 프로젝트 | 명령(legacy 루트 기준) | 결과 | 소요 | 조건 |
|---|---|---|---|---|
| GX | `uv sync --frozen --python 3.12 && uv run pytest -m "not network"` | **3,227 수집 / 3,178 통과 / 49 건너뜀**(Docker 없는 통합 시험), 실패 0 (`.env.example` 이 있어야 2개가 돈다) | 약 2분 25초 | Docker 있으면 49개도 실행 |
| ET board | `python -m unittest discover -s board/tests -t .` | **1,319 OK** | 약 18초 | 네트워크 없음, 레포 루트 `.github/workflows` 필요 |
| ET monitor/kr | `python -m unittest discover -s monitor/kr -p 'test_*.py' -t .` | **94 OK**(skip 1, Playwright 없음) | 2~30초 | board 패키지 import |
| ET monitor/flow | `python -m unittest discover -s monitor/flow -p 'test_*.py' -t .` | **129 OK** | 약 1초 | matplotlib |
| ET flowlab | `python -m flowlab selftest` | **44/44 통과** | 수 초 | pandas |
| ET dart-report | `cd dart-report && python tests_smoke.py` | "built. quarters=14 annual=4 bridge steps=9"(assert 0) | 수 초 | — |
| ET etf_tracker_v9 | — | 자동 시험 **0** | — | `verify.py` 는 실데이터 점검이라 CI 제외 |
| ET monitor/bok | `node monitor/bok/fetch-consensus.js --test` | 판정 출력만(집계 없음) | 수 초 | Node 22 |
| SD | `SERVER_NO_STARTUP=1 python scripts/check_<이름>.py` × 10 | **9 통과 / 1 실패**(`check_futures_us_index.py`, E4). 통과: `check_brief_sections`·`check_closing_brief`·`check_etf_marking`·`check_new_high_logic`·`check_newhigh_flow`·`check_newhigh_full_list`·`check_ohlcv_autofill`·`check_watchdog_alerts`·`check_watchdog_gating` | 각 수 초 | 네트워크 차단 상태로 실행. `test_collectors.py`·`test_data_json.py` 는 네이버·yfinance 실호출이라 **제외**(U4) |
| **합계** | — | 통과 **4,763개**(GX 3,178 + board 1,319 + kr 93 + flow 129 + flowlab 44) + SD 검사 스크립트 9 통과·1 실패 | — | P1 완료 기준을 "GX 3,178·board 1,319·kr 94·flow 129·flowlab 44 녹색, SD 검사 9/10(1건 원래 실패)"로 고칠 것 [제안] |

### 3.3 파이썬 버전·의존성 충돌

전 프로젝트 의존성을 합쳐 `uv pip compile --python-version 3.12` 로 풀었다(`/tmp/p0test/resolve/union.txt`). 해석은 성공했고 그 환경에서 전 시험이 위와 같은 결과로 통과했다.

| 패키지 | SD | ET | GX(`uv.lock`) | 합친 해석(3.12) | 충돌·조치 |
|---|---|---|---|---|---|
| Python | 3.11.9(`render.yaml`) | 3.11(board·daily·flowlab·us-board·xdigest 워크플로) / 3.12(bok·flow·kr·report) | `>=3.12,<3.13` | **3.12** | 3.12에서 전부 통과(E3) |
| pandas | `>=2.0` + **pykrx가 `<3.0,>=2.2.0`** | dart-report `>=2.0`, flowlab 무제한 | **3.0.6**(exchange-calendars 경유) | **2.3.3** | ⚠ GX lock과 다름. GX 시험은 2.3.3에서 통과(E2). pandas-stubs `3.0.5` 와 pandas 2.3의 pyright 결과 차이는 미확인[추정] |
| numpy | pandas 경유 | 무제한 | 2.5.3 | 2.5.3 | 없음 |
| websockets | (yfinance `>=13`) | — | 17.1 | 17.2 | 소폭 차이, GX 시험 통과 |
| requests | `>=2.31` | `>=2.31.0` | (httpx 사용) | 2.34.2 | 없음. HTTP 라이브러리가 requests·httpx 두 개 → KBJ 신규 코드는 httpx [제안] |
| lxml / beautifulsoup4 | `>=5.0` / `>=4.12` | dart-report 같음 | — | 6.1.3 / 4.15.0 | 없음 |
| PyYAML | — | `>=6.0` | 6.0.3 | 6.0.3 | 없음 |
| anthropic | — | `>=0.40.0` | — | **1.11.0**(메이저 1.x) | board 시험 통과(오프라인). 실호출은 미확인[추정] |
| openpyxl | — | `>=3.1` | — | 3.1.5 | 없음 |
| matplotlib | (pykrx `>=3.8`) | flow 무제한 | — | 3.11.2 | 없음 |
| streamlit | — | dart-report `>=1.36` | — | 1.65.0(+pyarrow·protobuf 7) | 무거운 의존성 — KBJ 웹으로 대체하면 제거 [제안] |
| aiohttp, tenacity | **requirements 누락**(`dart_collector.py:21`) | — | — | 3.14.4, 9.1.4 | ⚠ 누락 — 단일 lock에 추가하거나 `dart_collector` 폐기(§1.12) |
| pykrx | `>=1.0.51` → 1.2.9 | — | — | 1.2.9 | ⚠ import 때 `KRX_ID`·`KRX_PW` 로 자동 로그인(E5). U4·§1.13에 따라 제거 대상 |
| Flask·APScheduler·gunicorn·flask-socketio·simple-websocket | SD | — | — | 해석됨 | FastAPI 전환 뒤 제거 |
| yfinance | `>=0.2.40` → 1.7.0 | (preview 워크플로만) | — | 1.7.0 | 로그인 등급 어댑터 하나로 |
| playwright | — | kr·flow 시험·`capture.py` | — | 해석됨 | 브라우저 바이너리 별도 설치 필요(없으면 kr `test_page` skip) |
| exchange-calendars, vollib, psycopg, redis, pydantic(-settings), httpx | — | — | 고정 | 같음 | 없음 |
| Node 22 | — | monitor/bok | — | (별도 런타임) | Python 이식 시 제거 [제안] |
| 개발 도구 | 없음 | 없음 | pytest·hypothesis·fakeredis[lua]·ruff·pyright·pre-commit | — | ET는 unittest, SD는 스크립트 — pytest가 unittest를 수집하므로 단일 러너 가능[추정: SD 스크립트는 래퍼 필요] |

### 3.4 패키지 이름 충돌 (같은 최상위 모듈 이름)

legacy 프로젝트마다 import 루트가 다르다(GX `pythonpath=["."]`, ET는 레포 루트에서 `board.*`·`monitor.*`·`flowlab`, SD는 평면 모듈). 한 프로세스(한 pytest 실행)에 여러 루트를 올리면 아래가 부딪힌다.

| 최상위 이름 | SD | ET | GX | 영향 | 조치 [제안] |
|---|---|---|---|---|---|
| `db` | 패키지(`db/database.py`) | — | 패키지(`db/migrate.py`) | ⚠ **실제 충돌**: 두 루트를 함께 올리면 먼저 잡힌 쪽만 import | 프로젝트별로 따로 시험 실행(루트·`PYTHONPATH` 분리) |
| `scripts` | 디렉터리(파일로 실행, import 안 함) | `board/scripts`(board 안) | 패키지(시험이 `scripts.make_golden` import) | ⚠ SD 루트가 앞에 오면 GX 시험이 깨질 수 있음 | 같음 |
| `data` | 디렉터리(JSON만, 네임스페이스 패키지로 잡힐 수 있음) | — | 패키지 | 낮음 | 같음 |
| `config` | — | `dart-report/config/`(YAML), `board/config/`(board 안) | 패키지 | 낮음 | 같음 |
| `tests` | — | `board/tests`·`monitor/*/tests`(패키지 안) | 패키지 | 낮음 | 같음 |
| `market`, `report`, `render`, `themes`, `verify`, `dash`, `tracker`, `collectors` | — | `etf_tracker_v9/*.py` 평면 모듈(`monitor/kr/etf.py:264` 가 `sys.path` 에 끼워 import) | — | `report`·`verify`·`render` 는 ET `flowlab/report.py`·`verify.py`, board `web/render.py` 와 이름이 같다(패키지 안이라 지금은 안전). `dash` 는 PyPI Plotly Dash 이름 | `etf_tracker_v9` 를 패키지로 감싸 상대 import로 바꾸는 것은 P5 |
| `run`, `app` | — | `dart-report/run.py`·`app.py` 평면 | — | 낮음 | 같음 |
| `agents` | 패키지 | — | — | PyPI `openai-agents` 의 최상위 이름과 같음(설치 안 됨) | 낮음 |
| `telegram` | — | `monitor/flow/telegram.py`(패키지 안) | — | python-telegram-bot을 들이면 그 최상위 이름 `telegram` | notifier를 직접 HTTP로 유지하면 문제 없음 |
| `exec` | — | — | 패키지 | 내장 함수 이름(키워드 아님) | 그대로 |
| `sys.path` 조작 | 검사 스크립트·`test_*` | `monitor/kr/*.py`·`monitor/flow/*.py` 9곳(`'..', '..'` 삽입), `flowlab/probe_frgn.py:31` | — | legacy 위치가 바뀌어도 상대 경로라 동작 | 그대로(P1은 경로만) |

### 3.5 P1 진행 순서 [제안]

| 순서 | 할 일 | 완료 확인 |
|---|---|---|
| 1 | 레포 뼈대: 루트 `pyproject.toml`(Python 3.12, 단일 `uv.lock` = §3.3 해석 결과), `.gitignore`(DATA_TIERS §4), `config/` 와 `KBJ_` 설정(GX `config/settings.py` 방식), Compose(Postgres+Timescale·Redis) | `uv sync --frozen` |
| 2 | `legacy/gexlab/` 이동 → GX CI 명령 그대로 | 3,178 통과·49 건너뜀(Docker 있으면 통합 포함) |
| 3 | `legacy/etf_traker/board/` + `.github/workflows` 사본 이동 | 1,319 OK |
| 4 | `legacy/etf_traker/monitor/`·`flowlab/` 이동(board에 의존) | kr 94·flow 129·flowlab 44 |
| 5 | `legacy/etf_traker/etf_tracker_v9/`·`dart-report/` 이동 | 스모크 "built" |
| 6 | `legacy/stock_dashboard/` 이동, 검사 스크립트의 `/home/user/stock-dashboard` 절대경로(7개 파일)를 상대경로로(경로만) | 9/10(E4 1건은 원래 실패로 기록) |
| 7 | CI: 프로젝트별 잡 6개(GX·board·monitor+flowlab·dart-report·SD·ruff/pyright 신규 코드만), 금지 규칙 초안(`oauth2/tokenP`·`api.telegram.org`·네이버 호스트 문자열을 신규 `kbj/` 에서 금지) | CI 녹색 |

---

## 4. 사용자 확인 필요

정본을 추정으로 정하지 않고 질문으로 남긴다. 각 항목: 후보 A/B, 차이, 실데이터로 비교하는 방법.

### Q1. 당일 종가 출처와 마감 요약 시각
- **후보 A** — KIS 당일 종가: 15:35~16:00에 KIS `FHKST01010100`(종목별 현재가)로 약 2,700종목을 받는다. 마감 요약 16:40.
- **후보 B** — KRX 확정치: KRX `/sto/stk_bydd_trd`·`ksq_bydd_trd` 는 **D+1 08:00 공표**(E1). 신고가 보드 확정판은 이튿날 08:05에 만들고, 당일 마감 요약은 A로 내되 '잠정'을 붙인다(board D-080은 '재판정 경로를 만들지 않는다'로 정했었다 — 이 결정을 바꿔야 한다).
- **차이**: KIS 값이 KRX 정규장 종가와 같은지(NXT·시간외 단일가가 섞이는지) 미실측. A는 KIS 초당 4건을 공유하므로 약 11분 이상 걸리고 GX 16:00 분봉 적재와 겹친다.
- **비교 방법**: 5거래일 동안 15:40·16:10에 KIS로 전 종목 종가를 받아 저장 → 다음 날 08:05 KRX `TDD_CLSPRC` 와 종목별 대조. 불일치 종목 수, |차| 분포, 종가 기준 52주·역사적 라벨이 바뀌는 종목 수, 수집 소요 시간을 표로 낸다.

### Q2. 신고가 동률·'역사적' 정의
- **후보 A** — ET board(`newhigh.evaluate`): `종가 > 직전 최고`(동률 제외), 역사적 = 상장 이후 `alltime`, 종가·고가 두 기준, 분할 가드.
- **후보 B** — SD(`server.py:13912` SQL): `종가 >= 직전 최고`(동률 포함), 역사적 = DB에 있는 일봉 범위(최대 약 5년), 종가만, 1,000원 미만 제외.
- **차이**: 동률일 때 신고가 여부, 5년보다 오래된 고점이 있는 종목의 '역사적' 판정, 저가주 포함 여부.
- **비교 방법**: 같은 KRX 확정 일봉(최근 20거래일 각각)을 두 판정에 넣어(SD SQL은 `scripts/check_new_high_logic.py` 가 뽑는 방식 그대로) 날짜별 라벨 차이 목록(종목·A 라벨·B 라벨·사유: 동률/기간/가격 하한)을 만든다.

### Q3. 기술적 지표 계산 관례
- **후보 A** — ET `board/engine/systems.py`·`signals.py`: EMA 시드 = 첫 n개 SMA, RSI 워밍업 = None, 볼린저 실수값.
- **후보 B** — SD `server.py`: EMA 시드 = 첫 값, RSI 워밍업 = 0.0(과매도로 오인될 수 있음), 볼린저 정수 반올림, 백테스트용 RSI는 단순평균(`_bt_calc_rsi`:12572).
- **차이**: MACD·RSI 초기 구간 값, 저가주 볼린저 밴드 폭, 백테스트와 화면의 RSI가 다름.
- **비교 방법**: 관심종목 20개(저가주 3개 포함)의 2년 KRX 일봉으로 RSI14·MACD(12,26,9)·BB20·ADX14를 두 방식으로 계산 → 마지막 250봉의 최대 절대 차, 신호 뒤집힘 수(RSI<30 진입, MACD 골든크로스, BB 상단 돌파 날짜 차이)를 표로.

### Q4. DART 누적→분기 환산 (3벌)
- **후보 A** — ET `dart-report/dartreport/statements.py:to_quarterly`:166(`fnlttSinglAcntAll` 전체 재무제표).
- **후보 B** — ET `board/ingest/financials.py:_cum`·`quarters_vs_dart`(`fnlttMultiAcnt` 주요계정, A의 계정 동의어 사본).
- **후보 C** — SD `dart_collector.py:split_cumulative_to_quarterly`·`calculate_derived_metrics`(EBITDA·순부채).
- **차이**: 엔드포인트(전체 vs 주요계정), 연결·별도 폴백, 계정 이름 대응, Q4 = 연간 − 3Q누적 처리, 비12월 결산.
- **비교 방법**: 20종목(12월·비12월 결산, 지주·금융·제조 섞어서)의 최근 12분기 매출·영업이익·순이익을 세 방식으로 뽑아 셀 단위로 대조 → 불일치 셀 수와 사유.

### Q5. 테마·밸류체인 사전의 기준
- **후보 A** — ET `board/knowledge/themes.yaml`(62 테마, 자체, 사슬 역할별 seeds).
- **후보 B** — ET `monitor/kr/knowledge/themes.json`(테마 198·체인 23, `rsm0kk/kr-sector` 에서 추출 — 제3자, U3와 얽힘). PLAN §4 페이지 9는 이 숫자를 쓴다.
- **후보 C** — SD `data/valuechain_map.json`(5 테마 × 레이어 × 세그먼트, 키워드, 자체).
- **차이**: 같은 종목이 사전마다 다른 테마·체인에 들어간다. 구조(평면 테마 vs 레이어)도 다르다.
- **비교 방법**: 세 사전을 종목코드 기준으로 펼쳐 종목별 (테마 집합 A·B·C)를 만들고, 시총 상위 300종목에서 한쪽에만 있는 소속·서로 모순되는 소속(예: 체인 단계가 다름) 목록과 개수를 낸다.

### Q6. 업종(섹터) 1:1 분류
- **후보 A** — ET board48(`sectors.yaml`·`sector_map.yaml`, 3,924종목, LLM + 사람 검수).
- **후보 B** — ET `monitor/kr/knowledge/industries.json`(업종 58, 배정 639 + KSIC 학습 `industries.learn_ksic`).
- **후보 C** — KRX 업종지수 소속(KIS 종목마스터 업종 코드)[추정].
- **차이**: 분류 체계와 단위가 달라 섹터 순위·히트맵 결과가 달라진다.
- **비교 방법**: B의 배정 639종목에 대해 A 섹터·B 업종·C 업종 대응표를 만들어 다대일 불일치 비율과 대표 사례를 표로.

### Q7. 수급 — 기관 세부 출처와 '연속 순매수' 정의
- **후보 A** — KIS: `FHKST01010900`(3구분, 금액 실제) + 기관 세부는 `FHPTJ04160001`[추정: 세부 항목 제공 여부 실측 필요].
- **후보 B** — KRX 정보데이터시스템 스크랩(`monitor/flow/krx.py`, 기관 7구분, 로그인·러너 차단).
- **연속 정의 3벌**: SD `_flow_streak`:15091(값>0 연속), kr `flows.windows`:84(외국인 금액>0, 종목 휴장일 제외), flow `analyze.buy_days`(보합 제외).
- **비교 방법**: monitor/flow 골든(엑시콘 092870, KRX 워크북)과 같은 기간을 KIS로 받아 `analyze.reconcile` 로 원 단위 대조. 연속 정의는 50종목 20일 KIS 데이터에 세 함수를 돌려 값이 다른 종목 수를 낸다.

### Q8. ETF 테마 분류 (2벌)
- **후보 A** — ET `etf_tracker_v9/themes.py:classify`:96.
- **후보 B** — ET `monitor/kr/etf.py:classify`:109.
- **차이**: 같은 ETF가 다른 테마로 나올 수 있다(둘 다 이름 키워드 규칙).
- **비교 방법**: KRX `etp/etf_bydd_trd` 의 전 ETF 이름을 두 함수에 넣어 불일치 목록과 개수를 낸다.

### Q9. 매크로 수치 출처 (아침 브리핑 ②)
- **후보 A** — SD `api_macro`·`macro_data.json`(yfinance: USD/KRW `KRW=X`, WTI, VIX, 美10년).
- **후보 B** — ET bok(ECOS 한은 작성 환율·금리, FRED 정부 시리즈, 재무부 수익률곡선, Yahoo 일부).
- **차이**: 같은 지표라도 기준 시각·값이 다르다(예: 원/달러 매매기준율 vs 역외 시세, 美10년 재무부 고시 vs 야후 장중).
- **비교 방법**: 10영업일 동안 07:50에 두 출처 값을 나란히 저장 → 지표별 차이·기준 시각·결측 일수 표. 공개 등급이 필요한 칸은 B만 쓸 수 있다는 점도 함께 표기.

### Q10. 미국 신고가 판정
- **후보 A** — ET `board/us/engine.py`(Nasdaq 일봉으로 252일 직접 계산, 국장 엔진 공유, CI PASS).
- **후보 B** — SD `_us_new_highs_from_yinfo`:10972(yfinance `info` 52주 고가 필드, 95% 근접, S&P 유니버스).
- **차이**: 유니버스(전 상장 vs S&P), 기준(일봉 계산 vs 공급자 필드), 근접 기준.
- **비교 방법**: 같은 미국 거래일 5일에 두 방식 결과를 뽑아 S&P500 교집합에서 신고가 판정이 다른 종목 목록.

### Q11. 유니버스 필터 (3벌)
- **후보 A** — 공통 유니버스 하나(KRX 종목기본정보 + `engine/kinds.py`)에 기능별 하한만 다르게.
- **후보 B** — 지금처럼 기능별 필터 유지: SD(시총 1,000억·ETF 제외·1,000원 이상), board(시총 1,000억·거래대금 1억), kr(20일 중앙 거래대금 10억, 코스닥 시총 1,000억 또는 상위 200, 우선주·스팩·코넥스 제외).
- **차이**: 같은 화면 묶음(신고가·섹터·수급)에서 모집단 수가 다르다.
- **비교 방법**: 같은 날 KRX 확정 스냅에 세 필터를 적용해 종목 수와 차집합(한 필터에만 있는 종목) 목록.

### Q12. DB 이관 원본 사본
- **후보 A** — SD는 맥북 로컬 `db/dashboard.db`(맥 cron 산출 포함), board는 맥 로컬 `board/board.db`(2026-09-28부터 주 경로), ETF는 최신 Actions 아티팩트 `etf-db-*`.
- **후보 B** — SD는 Gist 백업(`CORE_TABLES` 17개만), board는 Actions 캐시 `board-db-v*`(9/29 이후 갱신 없음 [추정]).
- **차이**: 표 범위(B는 일부 표만)와 최신성.
- **비교 방법**: 두 사본을 읽기 전용으로 열어 표별 행 수·최신 날짜·키 합계를 나란히 비교.

### Q13. (정본 외) 레포 밖 KIS 발급자
- `kospi-dislocation`(평일 08:55, 같은 앱키 기록 — GX `docs/phase1_design.md:92`)을 KBJ 전환일에 멈출지, KBJ auth의 Redis 토큰을 읽게 바꿀지. 비교할 것 없음 — 결정만 필요.
