# ADR 0029 — GEX 이식 경계: 옮긴 것·남긴 것·DB 연결·쓰기 주인·웹소켓 D2 마감·알림 시각 (2026-10-13)

상태: **확정**(2026-10-13 P7 WRAP — 엔진 절은 묶음 ENG, 웹소켓·녹화·raw·poller 절은 묶음 ING 초안, §4 WRAP 확정 메모).
설계서의 'ADR-A'(docs/p7_design.md D-P7-21). 번호: P6 가 0025~0027, IDX 가 0028 을 써서 0029.
근거: docs/p7_design.md D-P7-3·4·5·7·8·10·16, §1.3·§3.4, ADR 0004 D2(웹소켓 P7)·0005(outbox)·0006(등록부)·0018(마감 뒤
16:00)·0019(investor_flow 쓰기 주인).

## 1. 맥락

GEXLAB(GX)은 P2 에서 캘린더·KIS 토큰 읽기·리미터·REST·마스터·KRX·스풀·health 를 KBJ 로 넘겼고, P7 에서 계산(core)·
엔진 서비스·웹소켓·녹화를 옮긴다. 수집 경로(poller·scheduler 작업)는 실행기에 없는 트리거(`state_enter` 분봉·초 단위
전광판) 때문에 이번에 옮기지 않는다(§10 Q1 — P9).

## 2. 결정 — 엔진(묶음 ENG)

1. **순수 계산과 실행부를 나눈다**: `GX:services/engine/{registry,evaluate,extended,flow,futures,daily}.py` → `kbj/engines/gex/`
   (`git mv`, 계산 그대로·import 경로만 — 계약 ⑨: 어댑터·서비스·저장소·Redis 를 모른다). Redis·스레드·DB 를 쓰는
   `service.py`·`publish.py`·`context.py` 는 한 파일 `kbj/services/engine/gex.py`(진입 `python -m kbj.services.engine.gex`)로.
   - 엔진이 쓰던 버스 자료형(`MrktCls`·`SeriesKey`·`series_label`·`BasisEntry`·`BasisBook`)과 캘린더 최종거래일 계산은 순수
     모듈 `kbj/engines/gex/series.py` 가 정본 — `kbj.services.runtime.bus`·`chain_feed` 가 다시 내보낸다(두 벌 금지).
   - KIS 분봉 output1 검증(`quote_of` — 어댑터 모델 `MinuteQuote`)은 서비스에 둔다. 엔진의 분봉 TR 상수는 값으로 적고
     어댑터 상수와 같은지 시험이 본다(`tests/unit/engines/gex/test_engine_kbj.py`).
   - 스냅샷 → 사이클 입력 변환(`snapshot_cycle`)은 `kbj/engines/gex/snapshot_cycle.py` 하나 — legacy `metric_report`·시험이 쓴다.
2. **기능 플래그 = `kbj.config.gex`**(GX `core/features.py` 승계 — 카탈로그·기본값·`FeatureWatcher` 30초 다시 읽기). 값은
   `config/gex.yaml` 의 `features:`(GX 값 그대로 — 섀도는 섀도). 로더는 GX 플래그 문서와 gex.yaml 전체를 다 받는다.
   카탈로그 밖 이름의 `Features.flag` 는 None(파일에 쓸 수 없는 이름) — 엔진 기본값은 `default_flag`.
3. **저장 = `kbj.store.repos.gex.GexRepo`**(D-P7-8 — GX 싱크 메서드 이름 그대로, legacy `PostgresSink` 도 같은 모양이라
   GX 통합 시험이 shim 으로 돈다). 엔진 읽기에 `ws_connection_events`(HIRO 리셋 사유 — `ops.health_events` 의 ws-gateway
   연결 사건)를 더했다. 서비스 health 는 `kbj.store.db.PgHealthStore`.
4. **쓰기 주인**: `prv_gex.levels·metrics·strike_gex·option_iv·oi_changes` = `gex.engine`(KBJ) 하나. `prv_gex.investor_flow` 는
   **읽기만**(쓰기 주인 `flows.market_minute` — ADR 0019). 엔진은 텔레그램을 보내지 않는다 — 장중 레벨 알림은 등록부 작업
   `gex.alert`(09:00~15:30, 쿨다운 600초 — ADR 0018·§10 Q5).
5. **스풀**: KBJ `PgGexRepo` 는 실패를 올린다(에러를 삼키지 않는다). 엔진 서비스는 GX 와 같이 표 하나의 쓰기 실패를
   health(`engine_write_failed`)로만 남기고 나머지 쓰기·발행을 계속한다. 디스크 스풀 감싸기(GX `PostgresSink(spool=True)`
   자리)는 녹화 싱크(ING `kbj/services/recorder/store.py`)와 합쳐 WRAP 이 정한다 [확인 필요].
6. **legacy = 옮긴 만큼만**: `legacy/gexlab/services/engine/*`·`core/features.py` 는 KBJ 를 다시 내보내는 shim, GX compose 의
   `python -m services.engine` 은 KBJ 서비스를 띄운다. 원본 시험은 승격(같은 단언, import 경로만) — 엔진 9파일 146 항목
   (`tests/unit/engines/gex/`), 저장 `test_store_engine` 32 중 30(`tests/unit/store/test_gex_repo_engine.py`), 통합
   `test_engine_store` 9 중 8(`tests/integration/test_gex_engine_pg.py`). legacy 에 남긴 3 항목(GX 자기 마이그레이션 1·
   shadow_report 읽기 2)은 legacy 도구 몫(D-P7-4). 다리 시험 +1(`test_kbj_gex_engine_bridge.py`).
   - legacy `config/features.yaml` 은 GX compose 마운트가 읽어 남겼다 — 값이 KBJ 와 같은지 다리 시험이 본다(WRAP 이
     compose 를 옮기면 지운다).
7. **API(로그인 — DB 만, 계약 ⑩)**: 섀도 지표는 값·detail 을 비우고 플래그만, off 는 싣지 않는다(§10 Q3). invalid 는 빼고
   notes 에 수. 장 중 마지막 사이클이 2분보다 오래면 stale [확인 필요], 장 밖이면 `session_open=false`. 현재가는 Flip detail
   의 `f_ref`, 없으면 선물 전광판 근월물(estimated) [확인 필요]. 투자자별 대금은 원(백만원 × 10⁶).

## 3. 결정 — 웹소켓·녹화·raw·poller (묶음 ING)

1. **웹소켓은 KBJ 로(ADR 0004 D2 마감)**: `GX:services/ws_gateway/**` → `kbj/services/ws_gateway/`, `GX:data/kis/ws.py` →
   `kbj/data/private/kis/ws.py`, REST 파생 응답 모델 `GX:data/kis/models.py` → `kbj/data/private/kis/derivatives.py`(TR 번호
   `TR_*` 정본도 여기 — GX `scripts/probe_common.py` 와 같은 값). 모두 `git mv` + import 경로만. 진입점
   `python -m kbj.services.ws_gateway`. 접속키는 auth 가 발급하고 ws-gateway 는 `kis:ws_key` 를 `reader(…, "ws_key")` 로
   읽기만 한다.
   - **호스트는 코드 상수 하나**: `kbj/services/ws_gateway/endpoints.py`(`REAL_URL`·`VTS_URL`). `config/{kis_ws,kis_ws_fields,
     ws_budget}.yaml` 은 `git mv` 로 KBJ `config/` 에 옮기며 `kis_ws.yaml` 의 `url.real`·`url.vts` 줄을 뺐다
     (`WsConfig.url_for(env)` 는 `endpoints.url_for` 로). check_canonical 기준선 `kis_ws` 4 → 0(그룹을 '목표 0'으로 바꾸는
     것은 WRAP).
   - 웹소켓 라이브러리 import 를 `kbj.services.ws_gateway` 안으로 묶는 계약(D-P7-20 나)은 WRAP. 지금 KBJ 에서
     `websockets` 를 쓰는 곳은 이 패키지와 시험 가짜 서버(`tests/fakes/ws_server.py`)뿐이다.
2. **녹화(recorder)도 KBJ 로**: `GX:services/recorder/{service,envelope}.py` → `kbj/services/recorder/`(봉투 모델 정본은
   FND-B 의 `kbj.core.gex.records`, `wrap`·`Batcher` 는 `envelope.py`). 진입점 `python -m kbj.services.recorder`.
3. **쓰기 싱크 = `kbj.services.recorder.store.SpooledGexStore`**(D-P7-8): `kbj.store.repos.gex` 의 표 정의·행 함수
   (`RAW_MESSAGES`·`FUT_TICKS`·`OPT_TICKS`·`SESSION_LOG`·`HEALTH_EVENTS`·`raw_row`·`attr_row`·`session_log_row`)를 그대로
   쓰고(같은 원문은 같은 digest), GX `PostgresSink(spool=True)` 의 '쓰기 + 디스크 스풀' 규칙을 다시 세웠다 — 연결 오류면
   `kbj.store.spool.DiskSpool` 에 쌓고 백오프 뒤 `flush_spool()` 이 재적재, 데이터 오류는 `StoreError` 로 올린다(호출자가
   한 건씩 다시 쓴다). 메서드 이름은 GX 싱크 그대로라 ws-gateway `GatewayStore`·recorder `RawStore` 프로토콜과 legacy
   `PostgresSink` 가 서로 바꿔 끼워진다. health 는 서비스 이벤트를 `kbj.store.db.health_row` 로(detail 빈 객체).
   엔진 쓰기에도 이 싱크를 쓸지는 위 2-5 와 함께 WRAP 이 정한다.
4. **쓰기 주인과 raw 예외(D-P7-7)**: `prv_gex.fut_ticks·opt_ticks` = ws-gateway, `prv_gex.raw_messages` = recorder. **예외 하나**
   — ws-gateway 가 원문을 `ws.raw` 로 발행했는데 받는 쪽이 없거나 Redis 오류면 직접 `write_raw` 한다(GX `services.bus` 규칙,
   녹화를 잃지 않으려는 장치). 원문은 덧붙이기 전용이고 `(ts, digest)` UNIQUE 로 멱등이라 두 주인의 경합이 행을 바꾸지
   않는다. 등록부 `writes` 에는 recorder 만 적는다.
5. **raw 녹화 골든 = `kbj.services.recorder.golden`**(GX `make_golden.py` 의 `raw`·`raw-parse` — 파일 I/O 도우미는 FND-C 의
   `kbj.services.engine.gex_golden` 것). 녹화는 `tests/golden/gex/raw/`(합성 한 벌 — 실제 녹화 아님), DB 입력은 KBJ
   `prv_gex.raw_messages`(`KBJ_DATABASE_URL`). 커밋된 파서 스냅샷은 메타 문자열 2줄(`_about`·`_regenerate`)만 KBJ 경로로
   바뀌고 값은 그대로다. 라이브 녹화는 로그인 등급 원문이라 공개 레포에 넣지 않는다 — 운영 VM 에서만 잘라 비교 [확인 필요].
6. **poller 투자자별 폴링 삭제(D-P7-5 — ADR 0019 §2-2 해소)**: `GX:services/poller/planner.py:_investors` 와
   `PollerConfig.night_investor·investor_period_s·investor_offset_s` 를 지웠다. `prv_gex.investor_flow` 는 KBJ
   `flows.market_minute` 만 쓴다(주간만 — D-P7-6 으로 야간 투자자 행은 P7 뒤 끊긴다). GX scheduler 의 무결측 점검은
   야간 투자자를 기대하지 않는다(`GapConfig(night_investor=False)`). 투자자 행 처리기(`collector._on_investor`)는 부르는
   계획이 없어 죽은 코드로 남는다 — poller 이식(P9) 때 지운다.
7. **legacy 를 KBJ DB 에(D-P7-4)**: GX 설정 `db_search_path`(환경변수 `DB_SEARCH_PATH`, 운영 값 `prv_gex,ops`)가 있으면 접속
   문자열에 `options=-csearch_path=…` 를 붙인다 — GX SQL 의 스키마 없는 표 이름이 KBJ 0006 표로 간다. 이때 GX
   `db.migrate` 는 아무것도 하지 않고 끝난다(스키마 정본은 KBJ). 비우면(통합 시험의 자기 db) 지금처럼 돈다.
   [확인 필요: 운영 VM 의 환경변수 이름]
8. **legacy = 옮긴 만큼만**: `legacy/gexlab/services/{ws_gateway,recorder}/*`·`data/kis/{ws,models}.py` 는 KBJ 를 다시
   내보내는 shim(`python -m services.ws_gateway|recorder` 는 KBJ 서비스를 띄운다 — `KBJ_*` 환경변수), `scripts/make_golden.py`
   는 모드를 KBJ CLI 로 넘긴다. 원본 시험 233 항목 승격(같은 단언, import 경로만 — 가짜 서버는 GX 판을 `tests/fakes/ws_server.py`
   의 `AsyncKisWsServer` 로 옮겨 `as FakeKisWsServer` 로 import), 그중 recorder 의 `PostgresSink` 스풀 1 항목은 GX 싱크
   시험이라 legacy `test_recorder_postgres_spool.py` 에 남기고 KBJ 싱크의 같은 시나리오를 새로 썼다
   (`tests/unit/recorder/test_recorder_store.py`). 다리 시험 +1(`test_kbj_gex_ingest_bridge.py`).

## 4. WRAP 확정 메모(2026-10-13)

1. **켜기**: 등록부 서비스 `gex.engine`·`gex.ws_gateway`·`gex.recorder` 를 `enabled: true`(GX 운영으로 데이터 확인됨).
   쓰기 표는 등록부 `ServiceSpec.writes`(WRAP 이 더함 — 작업 writes 와 같은 형식·마이그레이션 검사, 표 하나에 작업·서비스
   통틀어 쓰기 주인 하나, 서비스는 공개 표에 쓰지 않는다): engine = levels·metrics·strike_gex·option_iv·oi_changes,
   ws_gateway = fut_ticks·opt_ticks, recorder = raw_messages. raw 예외(버스 발행 실패 때 발행자가 직접 쓴다 — digest
   UNIQUE 로 멱등)는 writes 에 적지 않는다(이 ADR §3·0020 주석).
2. **하트비트 이름**: 세 프로세스는 GX 이름 그대로(`engine`·`ws-gateway`·`recorder`) 하트비트를 남긴다. 등록부 이름(`gex.*`)과
   달라 `ServiceSpec.heartbeat_key` 를 두고 워치독(`ops.watchdog`)이 그 이름으로 읽는다 — 없으면 켜자마자 '하트비트 끊김'
   알림이 매번 나갔다(WRAP 이 sim 으로 찾음). compose healthcheck 도 같은 이름.
3. **compose**: 루트 `docker-compose.yml` 에 `ws-gateway`·`recorder`·`gex-engine`(KBJ 이미지, profile app). 앱키는
   ws-gateway 만(접속키 캐시 이름 — 시크릿 없음, 발급은 auth), 녹화·엔진은 KIS 키 없음. 스풀은 `/data/spool/<서비스>`.
   legacy GX compose 의 같은 셋은 통합 시험용으로 남고 운영 VM 에서는 §10 Q7 순서(WRAP 뒤 첫 주말)로 끈다 —
   런북은 `scripts/gex_migrate_data.sh` 머리말, 녹화·산출 이관은 같은 스크립트(실행은 사용자 — #P7-3).
4. **계약**: import-linter ⑲ — `websockets` 는 `kbj.services.ws_gateway` 안에서만 직접 import(계약 ⑤ 방식). GEX 엔진
   (`kbj.engines.gex`)은 계약 ⑨(순수)에 든다(시험 사례로 확인). 직접 호출 검사 `kis_ws` 그룹은 목표 0(legacy 기준선 4 → 0).
5. **`/gex` 봇 명령**: notifier 진입점이 `command_reader(PgGexRepo(...), utcnow)` 를 명령표에 넘긴다(OUT 이 만든 읽기
   함수 — 부를 때마다 짧게 연결). 운영에서도 '준비 중(P7)' 대신 최신 레벨을 답한다.
6. **엔진 쓰기 스풀**: 엔진은 표별 health(`engine_write_failed`)만 남기고 디스크 스풀은 붙이지 않았다(위 §2). 녹화 싱크
   (`kbj/services/recorder/store.py` `SpooledGexStore`)를 공용으로 옮기는 일은 다시 볼 때로 남긴다 [확인 필요].
7. **legacy 기준**: `scripts/test_legacy.sh gexlab` 2,716 → **760**(실측), `gexlab-integration` 50 → **39** — 근거
   `legacy/gexlab/MIGRATION.md` 'P7' 절(옮긴 1,956 항목 1:1).
8. **알림 지연 기준**: `gex.alert` 의 `STALE_AFTER`(3분 [제안])는 API·띠 칩(2분)과 다르다 — 알림은 사이클 지연 1분을
   더 참는다(분 단위 작업이 사이클 직전에 돌 수 있다). 하나로 맞추는 일은 실데이터 하루(#P7-4) 뒤 [확인 필요].

## 5. 다시 볼 때

- 디스크 스풀을 엔진 쓰기에 붙일 때(위 2-5) — 녹화 싱크와 한 모듈로.
- 섀도 → visible 전환(사용자 `shadow_report` 1주 확인 — §10 Q3): `config/gex.yaml` 값만 바꾼다(코드 변경 없음).
- 엔진 stale 기준(2분)·현재가 대체 규칙은 실데이터 하루(`#P7-4`) 뒤 다시 정한다.
