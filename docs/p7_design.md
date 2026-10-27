# KBJ P7 설계서 — GEX 엔진 이식·페이지 3 진단

- 작성: 2026-10-13. 상태: **구현 완료**(2026-10-13 P7 WRAP — 아래 'WRAP 보정' 이 이 문서보다 우선). 대상 단계: `docs/PLAN.md` §4 페이지 3·§8 P7 — "GEXLAB engine 이식(이미 서비스형), 페이지 3 진단, 국장 공포탐욕·종합 판정". 완료 기준: **GEXLAB 골든·명세 시험이 KBJ 에서 녹색이고, 진단 지표 명세가 `docs/metrics.md` 에 있다.**
- 참고: 같은 시각 설계 중인 `docs/p6_design.md` 는 `0019_trade.sql`·ADR 0025~0027(초안)·metrics §25~§26·계약 ⑰ 을 쓴다 — P7 의 실제 번호는 그 뒤(FND 가 확정).
- 시작 조건: **IDX(P5 2차 — 지금 작업 트리에서 빌드 중, `0018_index.sql`) 커밋 → P6 커밋(마이그레이션 0019 부터) 뒤.** 이 문서의 번호는 전부 상대 번호다 — 마이그레이션 = **"P6 다음 번호"**, ADR = "IDX·P6 다음 번호", `docs/metrics.md` 절 = "P6 다음 절"(이 문서에서는 GEX 를 §G0~§G8, 진단을 §D1~§D9 로 부르고 FND 가 실제 번호로 바꾼다), import-linter 계약 = "⑯ 뒤 다음 번호", 실측 체크리스트 = `#P7-1~#P7-6`(FND 가 `docs/probe_results.md` §7 의 다음 번호로 바꾼다).
- 기준 문서: 계획 `docs/PLAN.md` §2·§4·§8, 등급 `docs/DATA_TIERS.md`(정본 — 페이지 3 공개 = 공공데이터만으로 만든 지표: 신용스프레드 구간·신용잔고·예탁금 추세·매크로 위험 게이지·수출 모멘텀, 로그인 = GEX 레벨 전부·시장폭·수급 진단·국장 공포탐욕·종합 판정 — :49), v0.2 지표 표 `docs/reference/plan_kr_v0.2.md`:55~78(반도체 쏠림·시장폭·신용스프레드·한국 대 글로벌·공포탐욕), 미리보기 `docs/design/preview_synthetic.html`:196~205·490~501(페이지 3 패널 배치: '공공지표 진단' + 'GEX 레벨' — `.lvl` 행·행사가 막대·현재가·Flip 선), P4·P5 설계(구조·관례), ADR 0002(등급 스키마)·0004 D2(웹소켓)·0005(outbox)·0006(등록부·선점)·0007(legacy 기준선)·0018(마감 뒤 16:00)·0019(investor_flow 쓰기 주인), 묶음 F2 지시 `docs/p5_flow_program_prompt.md` §2.
- 원본 위치: `GX:` = `legacy/gexlab/`. GX 지표 명세 `GX:docs/metrics.md` §0~§8, 이식 기록 `GX:MIGRATION.md` 'P2'.
- 표기: **[추정]** = 코드로 끝까지 확인하지 못함, **[확인 필요]** = 기본값으로 정한 것, **[실측 필요]** = 키를 받은 뒤 실제 응답으로 확인, **[결정 필요]** = 사용자가 정할 것(§10), **[제안]** = 이 설계의 권고, ⚠ = 충돌·위험.
- 키·토큰·계좌 값은 이 문서 어디에도 없다. `.env`·토큰 캐시는 열지 않았다. 네트워크 호출은 하지 않았다. 증명은 전부 **합성 데이터·가짜 서버**로 한다(GX 골든은 이미 합성 — `GX:tests/golden/core/chain_synthetic_20260928_small.json`·`raw/synthetic_20260928.*`, 생성기 `GX:scripts/make_synthetic_fixtures.py` 시드 고정). KIS·KRX 원자료와 그것으로 만든 픽스처는 레포에 넣지 않는다. 로그인 등급 산출은 공개 빌드에 들어가지 않는다.

**WRAP 보정(2026-10-13 — 구현이 이 문서와 다른 곳, 구현이 정본)**:
- 번호: 마이그레이션 `0020_diag.sql`, ADR-A = **0029**(GEX 이식 경계), ADR-B = **0030**(진단 지표), import-linter (가) = **⑱**·
  (나) = **⑲**, 실측 `#P7-1~#P7-6` = probe **#58~#63**(+ WRAP #64 `#P7-7`), metrics §G·§D 는 그 이름 그대로.
- §1.2: 스냅샷 입력 모델은 `kbj/engines/gex/snapshot.py`(KIS 그릭스 필드를 읽어 core 금지 단언 때문에 engines). 골든 입력은
  `tests/fixtures/synthetic/gex/validation/`, 재생성은 `uv run python -m kbj.services.engine.gex_golden core --write`.
- §1.3: 서비스 실행부(service·publish·context)는 `kbj/services/engine/gex.py` 한 파일(context 는 Redis 라 서비스 쪽). 버스 모델·
  캘린더 최종거래일 계산 일부는 계약 ⑨ 때문에 `kbj/engines/gex/series.py`. store_engine 은 32 중 30 승격.
- §1.10 OUT 명령의 `npx vitest run test/ribbon.test.ts` → `test/pages/ribbon.test.ts`.
- §1.11 실측: legacy `gexlab` 760, `gexlab-integration` 39(옮김 1,956 = FND-C 1,548 · ENG 176 · ING 232 — `GX:MIGRATION.md` 'P7').
- 등록부: `ServiceSpec` 에 `writes`(쓰기 주인)·`heartbeat_key`(프로세스 하트비트 이름 — engine·ws-gateway·recorder) 칸을 더했다.
- 공개 JS 예산: 88 KB·로그인 107 KB(ADR 0025 갱신 — 실측 + 10%).

**코드로 확인한 사실(2026-10-13, HEAD `138da8f` + IDX 미커밋 작업본) — 이 설계의 출발점**:

1. GX 는 이미 P2 에서 일부를 KBJ 로 넘겼다: 캘린더·KIS 토큰 읽기·리미터·REST·마스터·KRX 클라이언트·스풀·health(`GX:MIGRATION.md` P2.1). 시험 461(+매개변수 1)이 빠져 `scripts/test_legacy.sh gexlab` 기준이 **2,716**(수집 항목 수 — 이 문서 작성 때 `--collect-only` 로 같은 2,716 을 확인), `gexlab-integration` **50**(compose 1 + Docker 49).
2. 남은 GX 코드(약 32k 줄): 순수 계산 `core/{black76,greeks,iv,forward,chain,preprocess,gex,levels,specs,features}.py`·`core/metrics/{exposure,flow,futures,vol}.py`, 서비스 `services/{poller,ws_gateway,recorder,engine,scheduler}`·`bus.py`·`chain_feed.py`·`gaps.py`·`runtime.py`, 원천 `data/kis/{models,ws}.py`·`data/store.py`(2,334줄 — 표 정의·`PostgresSink`·읽기 함수), 도구 `scripts/{make_golden,validate_greeks,metric_report,shadow_report,nogap_report,probe_*}.py`. `api/`·`strategy/`·`exec/`·`live/`·`backtest/`·`ui/` 는 빈 자리(`__init__` 뿐).
3. KBJ 쪽 자리는 이미 있다: 표 `prv_gex.*` 16 + `ops.collection_*` 3(`0006_gex.sql` — "P7 에 GX 코드를 search_path(prv_gex, ops)로 붙인다", 녹화 이관 [확인 필요]), 등록부 `config/jobs.yaml` 의 `services: gex.poller·gex.ws_gateway·gex.recorder·gex.engine`(runner external, P7)과 작업 `gex.day_minutes·gex.night_minutes·gex.krx_derivatives·kis.master`(external, P7), 데이터셋 `kbj/data/private/kis/datasets.py:274~` `fo_master·fut_minute_day·fut_minute_night·option_chain_live·market_investor_intraday`·`:407 ws_ticks`, 빈 패키지 `kbj/services/ws_gateway/__init__.py`, 띠 칩 `gex_flip`(`kbj/engines/market/ribbon.py:70`·`web/src/app/ribbon.ts:41` — phase P7), 봇 명령 `/gex`(`kbj/services/notifier/commands.py:150` — phase P7), 알림 종류 `alert.gex_level`(`config/notify.yaml` — 쿨다운 600초), 브리핑 자리 `config/briefs.yaml:14 night_futures_gex`·`:25 gex_signals`(omitted, P7), 페이지 `web/src/app/pages.ts:30`(3 진단 — `tier: 'login'`, `lock: 'options'`).
4. **`prv_gex.investor_flow` 쓰기 주인은 P5 `flows.market_minute` 하나다**(ADR 0019, `0017_program.sql` COMMENT). GX poller 는 아직 이 TR(`FHPTJ04030000`)을 부른다(`GX:services/poller/planner.py:_investors`:347, `config.py` `night_investor: True`) — ADR 0019 §2-2 의 운영 조건. GX 엔진은 이 표를 **읽기만** 한다(`GX:services/engine/flow.py` §6.2 `InvestorFeed` — 30초마다 새 행). 대금은 **백만원**(GX 단위).
5. `flows.market_minute` 는 `equity {open → close+10, every_min: 1}` 하나다. 실행기는 **시각 트리거를 하나만** 받는다(`kbj/services/scheduler/registry.py`:8) — 야간 1분 창을 같은 작업에 더할 수 없다. 위클리 조합(WKM OC05/OP05·WKI OC04/OP04)은 `config/markets.yaml:66~69` 에 꺼진 채(“GX poller 조합 — P7”), **GX 실측 #13 에서 7조합 모두 정상 응답 확인**(`GX:docs/probe_results.md`:18). 야간 조합(`:70`)은 실측 없음.
6. **GX 확장 지표는 섀도다**: `GX:config/features.yaml` 에서 VEX·CEX·GEX P/C·IV 기간구조·25Δ 스큐·IV 랭크·IV−HV·HIRO-lite·투자자별·딜러 점검·대량 체결·PCR 이 `shadow`(저장만, 발행 안 함 — "1주 섀도 운영 전까지"). 페이지 3 이 요구하는 VEX·CEX·스큐·HIRO-lite 가 여기 든다.
7. `prv_gex.raw_messages` 는 GX 에서 **쓰는 곳이 넷**이다: recorder(정상 경로 — Redis `ws.raw`·`rest.raw` 구독), 발행 실패 때 직접 쓰는 poller·ws-gateway·scheduler 분봉(`services.bus` 규칙, digest UNIQUE 로 중복 없음).
8. `check_canonical` 기준선 `kis_ws | legacy/gexlab/config/kis_ws.yaml | 4`(P7 — ws-gateway 승격 때 0). 그룹 `kis_ws` 의 KBJ 허용 위치는 `kbj/services/ws_gateway/*` 뿐이다(`scripts/check_canonical.py`:170~176) — `config/` 로 옮긴 YAML 에 호스트가 있으면 위반이다.
9. GX `core` 는 `vollib`(legacy 의존성 그룹)과 `pyyaml` 을 쓴다. `websockets` 는 KBJ dev 그룹(가짜 웹소켓 서버용)에만 있다.
10. VKOSPI 는 KBJ 어디에서도 수집하지 않는다. 페이지 3 이벤트의 '수출 속보'와 공개 '수출 모멘텀'은 P6(관세청) 산출에 기댄다.

---

## 0. 요약

### 0.1 결정 요약

| # | 결정 | 이유·근거 |
|---|---|---|
| D-P7-1 | **IDX·P6 가 커밋된 뒤 시작**한다. FND 첫 일: 상대 번호(마이그레이션·ADR·metrics 절·계약·실측 #)를 실제 번호로 바꾸고, IDX·P6 가 고친 공유 파일(`app.py`·`cache.py`·`readers/_common.py`·`repos/{__init__,memory}.py`·`jobs.yaml`·`metrics.md`·`openapi.json`)을 다시 읽어 §1.9 표를 맞춘다 | 지금 IDX 가 같은 공유 파일을 고치는 중(`git status`), P6 는 0019 부터 마이그레이션을 쓴다 |
| D-P7-2 | **구현 = 기반 1(FND, 웨이브 1 — 두 갈래 FND-B 기반·FND-C 코어 이전, 커밋 하나) + 병렬 묶음 4(ENG·ING·DIAG·OUT, 웨이브 2) + 마무리 1(WRAP, 웨이브 3)**. 코어 이전을 기반에 넣는다 — 엔진·녹화·진단이 모두 `kbj.core.gex` 를 import 하고, 완료 기준(골든·명세 녹색)을 첫 웨이브에서 증명해 위험을 앞당긴다 | P5 방식(FND·묶음·WRAP). 웨이브 2 묶음끼리는 FND 가 고정한 행 자료형·저장소 Protocol·API 모델·버스 모델로만 맞춘다 |
| D-P7-3 | **KBJ 로 옮기는 것(P7)**: ① 순수 계산 `GX:core/**` → `kbj/core/gex/`(FND-C) ② GX 지표 명세 `GX:docs/metrics.md` §0~§8 → `docs/metrics.md` §G0~§G8(FND-B) ③ 골든 도구 `GX:scripts/make_golden.py` 의 `core`·`chain-subset`(FND-C)·`raw`·`raw-parse`(ING) ④ 엔진 서비스 `GX:services/engine/**` → `kbj/engines/gex/`(순수)·`kbj/services/engine/gex.py`(ENG) ⑤ 웹소켓 `GX:services/ws_gateway/**`·`data/kis/ws.py`·녹화 `services/recorder/**`(ING) ⑥ 버스 모델 `GX:services/bus.py`·체인 문맥 `chain_feed.py`·`poller/context.py:ChainContext`·레코드 모델(FND-B) ⑦ REST 파생 응답 모델 `GX:data/kis/models.py`(ING — raw 골든 파서) | 완료 기준(골든·명세)·ADR 0004 D2(웹소켓 P7)·페이지 3 이 쓰는 엔진 산출. 셋 다 자기 시험·골든이 있어 '같은 단언을 import 경로만 바꿔' 옮길 수 있다 |
| D-P7-4 | **legacy 서비스로 남기는 것(P7)**: GX poller(전광판·체인·선물·최종거래일 — `option_chain_live`), GX scheduler 작업(`gex.day_minutes`·`gex.night_minutes`·`gex.krx_derivatives`·`kis.master`·무결측·개장 확인), `data/store.py:PostgresSink`(legacy 서비스의 쓰기), 운영 도구 `scripts/{validate_greeks,metric_report,shadow_report,nogap_report,probe_*}.py`. 등록부 runner external·enabled false 그대로. **legacy 서비스는 KBJ DB 에 `search_path=prv_gex,ops` 로 붙인다**(GX 자기 마이그레이션 001~004 는 돌리지 않는다 — 스키마 정본은 0006). 이식은 §10 Q1(기본 P9 `legacy/` 비우기) | 3~5일 안에 수집 경로까지 옮기면 실행기에 없는 트리거(`state_enter` 분봉·초 단위 전광판)를 새로 만들어야 한다. 이 경로는 KIS 를 이미 KBJ 리미터·토큰 읽기로 부른다(P2) — 남겨도 `check_canonical` 목표 0 그룹은 깨지지 않는다. ⚠ PLAN D8('단계마다 해당 영역 legacy 0')의 예외 — ADR 에 적는다 |
| D-P7-5 | **GX poller 의 투자자별 폴링을 없앤다**(ING — `GX:services/poller/planner.py:_investors` 삭제·`PollerConfig.night_investor`·`investor_period_s`·`investor_offset_s` 삭제, 그 시험은 '폴링하지 않는다' 단언으로 바꿈). `prv_gex.investor_flow` 는 계속 `flows.market_minute` 만 쓴다. 엔진·진단은 이 표를 읽는다 | ADR 0019 §2-2(같은 TR 두 곳 금지 — 호출 2배·같은 행 경합). F2 §2 "P7 이 poller 를 옮길 때 이 TR 을 부르지 않고 표를 읽는다" |
| D-P7-6 | **위클리 4조합은 켠다, 야간은 켜지 않는다**: WRAP 이 `config/markets.yaml flows.minute.pairs` 의 `wkm_*`·`wki_*` 를 `enabled: true`(근거 GX 실측 #13 — 7조합 정상 응답). `k200_fut_night` 는 꺼진 채 — 실행기에 야간 분 트리거가 없고(`registry.py`:8) 야간 응답 실측도 없다(`#P7-2`). ⚠ GX 엔진이 받던 **야간 투자자 행은 P7 뒤 끊긴다**(GX `night_investor: True` 였음) — 엔진 `investor_flow` 지표는 야간에 '수집 안 함'으로 표시 | '확인 안 된 수집은 꺼진 채'·쓰기 주인 하나. 같은 작업에 시각 트리거 둘은 실행기 변경이 필요해 P7 범위 밖 |
| D-P7-7 | **표마다 쓰기 주인 하나**(§3.4 표). P7 이 KBJ 로 가져오는 주인: `levels·metrics·strike_gex·option_iv·oi_changes` = `gex.engine`, `fut_ticks·opt_ticks` = `gex.ws_gateway`, `raw_messages` = `gex.recorder`, `pub_diag.*` = `diag.public`, `prv_diag.*` = `diag.daily`, `prv_diag.vol_index` = `krx.vkospi`. **예외 하나**: `raw_messages` 의 '발행 실패 때 직접 쓰기'(GX `services.bus` 규칙 — 녹화를 잃지 않으려는 장치, digest UNIQUE 로 멱등)를 남기고 ADR 에 예외로 적는다. 등록부 `writes` 에는 recorder 만 적는다 | 단일 주인 규칙. 녹화 원문은 덧붙이기 전용·키 멱등이라 경합이 행을 바꾸지 않는다 — 없애면 Redis 장애 때 원문을 잃는다 |
| D-P7-8 | **KBJ 쪽 서비스의 저장은 GX 싱크 메서드 이름 그대로의 Protocol**(`write_levels·write_metrics·write_strike_gex·write_option_iv·write_oi_changes·write_fut_ticks·write_opt_ticks·write_raw·write_session_log·write_health` + 엔진 읽기 `chain_max_ts·chain_latest·futures_latest·expiry_dates·metric_last·metric_history·krx_option_days·krx_iv_rows·krx_futures_settles·investor_latest·opt_tick_days·opt_tick_history·kis_rest_outputs`)에 기댄다. KBJ 구현은 `kbj/store/repos/gex.py`(FND-B — `prv_gex.` 스키마 이름, `kbj.store.spool`), legacy `PostgresSink` 도 같은 Protocol 을 만족한다 | GX `data/store.py` 는 원천·서비스 모듈을 import 해서 KBJ 저장소 계약 ⑦ 을 깬다 — 통째로 옮길 수 없다. 이름을 같게 두면 legacy shim 으로 GX 통합 시험(`test_full_day`)이 그대로 돈다 |
| D-P7-9 | **legacy 정리 = 옮긴 만큼만**(D-P3-18·D-P4-19·D-P5-24 와 같은 규칙): 옮긴 모듈 자리에 KBJ 를 다시 내보내는 shim(서비스 진입점 `python -m services.{engine,ws_gateway,recorder}` 도 KBJ 서비스를 띄우는 한 줄 — GX compose·통합 시험이 그대로 돈다), 원본 시험은 **승격(같은 단언, import 경로만)** 또는 **폐지(대체 정본 시험 1:1 기록)**. `scripts/test_legacy.sh gexlab` 기준 수는 **옮긴 수만큼만** 줄고 단계마다 다리 시험 +1(§1.11) | P2 와 같은 방식. 기준 수는 최소 통과 수라 조용히 덜 모이면 실패로 잡힌다 |
| D-P7-10 | **웹소켓은 KBJ 로**(ADR 0004 D2 마감): 호스트는 `kbj/services/ws_gateway/endpoints.py` 상수 하나(그룹 `kis_ws` 허용 위치), `GX:config/{kis_ws,kis_ws_fields,ws_budget}.yaml` 은 `git mv` → `config/` 로 옮기며 **호스트 줄을 뺀다**. 기준선 `kis_ws` 4 → **0**, WRAP 이 그룹을 '목표 0'으로 바꾼다. 접속키는 auth 가 발급하고 ws-gateway 는 `kis:ws_key` 를 읽기만(D2 그대로). 웹소켓 라이브러리 import 는 `kbj.services.ws_gateway` 안에서만(새 계약) | ADR 0004 §2.3 "P7 에 0". 남은 KIS 직접 호스트는 이것 하나 |
| D-P7-11 | **기능 플래그는 GX 규칙 그대로**: `GX:config/features.yaml` → `config/gex.yaml` 의 `features:`(값 그대로 — 섀도는 섀도). off = 계산 안 함, shadow = 저장만(화면·알림·브리핑에 싣지 않음, 패널 자리에 '섀도 검증 중'), visible = 표시. 섀도 → visible 은 사용자가 `shadow_report` 1주 확인 뒤 바꾼다(§10 Q3) | GX 설계 §4·metrics §8. 검증 전 지표를 화면에 올리지 않는다 |
| D-P7-12 | **페이지 3 = mixed**(DATA_TIERS 정본 — `pages.ts` 의 `tier: 'login'` 을 고친다, §10 Q4). 공개 = '공공지표 진단'(신용스프레드 구간·신용잔고 추세·예탁금 추세·수출 모멘텀·금리 방향 → **매크로 위험 게이지** = 공공데이터만의 판정, `pub_diag`), 로그인 = 종합 판정·국장 공포탐욕·반도체 쏠림·시장폭·수급 진단·한국 대 글로벌·GEX 레벨 패널 전부(`prv_diag`·`prv_gex`). 공개 빌드는 로그인 패널 자리에 자물쇠만 | DATA_TIERS:49, 미리보기 '공공지표 진단 — 공공데이터만으로 만든 판정' |
| D-P7-13 | **진단 지표 정의 = 부록 A(→ metrics §D1~§D9)**: 상태 구간은 250거래일 백분위 다섯 칸(신용스프레드만 v0.2 대로 '1년 평균 ± 1σ' 위치), 국장 공포탐욕 = v0.2 다섯 성분(VKOSPI·시장폭·신용잔고 변화·외국인 수급·신용스프레드)의 백분위 평균(0~100, 성분 4개 미만이면 값 없음), 종합 판정 = 성분 상태 점수 합의 세 단계(위험 선호·중립·위험 회피) + '판정 보류' + 이벤트 주의 꼬리표. 기존 정본 공식(반도체 쏠림 metrics §6.4, 시장폭 §6.2, 신용스프레드 §16, 한국 대 글로벌 §6.5)은 **다시 정의하지 않고** 불러 쓴다 | CLAUDE '명세 먼저'. 정본 하나 — 띠 칩과 페이지 3 이 같은 함수 |
| D-P7-14 | **VKOSPI = 새 데이터셋 `KRX:idx/drvprod_dd_trd`**(파생상품지수 일별 — VKOSPI 행 [실측 필요 `#P7-1`]), 작업 `krx.vkospi`(08:10, **enabled: false** — 실측 전). 꺼져 있거나 값이 없으면 공포탐욕 변동성 성분은 **GX 월물 ATM IV**(`prv_gex.metrics atm_iv`, visible)로 대신하고 성분 품질 `estimated` + 사유 '대체: ATM IV' | 확인 안 된 수집은 꺼진 채. ATM IV 는 이미 우리가 계산하는 같은 성격의 값 |
| D-P7-15 | **진단 작업 둘 — 공개·로그인 모듈 분리**: `diag.public`(`kbj.services.engine.diag_pub:run`, 08:40 평일 — `macro.morning`·`market_stats.kofia` 뒤 무른 의존, `pub_diag` 만), `diag.daily`(`kbj.services.engine.diag:run`, **16:00** 거래일 — `market.close_collect` 굳은 의존·`board.daily` 무른 의존, `prv_diag` 만). `diag.daily` 는 그날 D 를 `estimated` 로, 직전 거래일 D−1 을 KRX 확정 원장으로 다시 계산해 `ok` 로 덮는다 — **확정용 두 번째 작업을 만들지 않는다**(쓰기 주인 하나) | 계약 ⑭ 방식(공개 작업은 로그인 원천·저장소를 모른다 — 새 계약). board.daily·confirm 처럼 둘로 나누면 같은 표에 주인이 둘 |
| D-P7-16 | **텔레그램(ADR 0018·U2)**: 새 발송 종류를 만들지 않는다. 진단·GEX 요약은 ① `brief.closing`(16:00 — `diag.daily` 를 무른 의존 `wait_min` 으로 기다림) ② `brief.morning` ④ 야간선물·GEX(08:10 — 간밤 야간 세션 마지막 엔진 산출)에 줄로 싣는다. 장중 `alert.gex_level` 은 새 작업 `gex.alert`(`equity {open → close, every_min: 1}` — **15:30 에 끝**, 쿨다운 600초)만 보낸다. 15:30 뒤·야간의 레벨 변화는 저장만 하고 보내지 않는다(§10 Q5) | ADR 0018 '마감 뒤 발송은 16:00' 과 등록부 시험("15:30 이후 첫 발화 발송 작업은 셋, 전부 16:00")을 그대로 지킨다. 엔진 서비스 안에서 보내지 않는다 — 발송 시각을 등록부가 통제 |
| D-P7-17 | **알림 조건 [제안]**: visible 플래그 지표만. ① 현재가가 Flip(범위 all)을 건넘(부호 체제 바뀜) ② 콜월·풋월 행사가가 바뀜 ③ 현재가가 기대변동폭 ±1σ(달력 기준) 밖으로 나감. 같은 조건은 `cooldown_s: 600` 안에 다시 보내지 않는다(notify 정책). 품질 `invalid`·`stale` 산출로는 보내지 않는다 | GX 에 알림 구현이 없었다(`GX:services/notifier` 빈 패키지) — notify.yaml 의 '설계 쿨다운 10분' 만 있음 |
| D-P7-18 | **화면은 MD6 13쪽 배치·기존 패널 모양 그대로**: 페이지 3 은 기존 `panel`·`kv`·`line`·`bars`·`seg`·`tabs`·`quality`·`lock` 과 미리보기의 `.lvl` 행(이미 `components.css`:477)으로 그린다. 새 공용 부품은 셋만 — `gauge`(0~100 가로 구간 막대 — 공포탐욕), `ladder`(가격 사다리 — 현재가·Flip·월·±1σ), `profile`(행사가별 GEX 세로 막대 + 현재가·Flip 선 — 미리보기 `#gex` 그림). 색·글꼴은 기존 토큰(`--up`·`--down`·`--warn`·`--accent`)만, 새 테마·전역 스타일 없음 | PLAN §4 'MD6 디자인 그대로', 미리보기 페이지 3 |
| D-P7-19 | **의존성**: `vollib`·`websockets` 를 legacy·dev 그룹에서 **런타임으로**(FND-B — `pyproject.toml`·`uv.lock`·`tests/test_import_contracts.py RUNTIME_IMPORTS`). `numpy` 는 GX core 가 쓰지 않으면 옮기지 않는다 [확인 필요 — FND-C grep] | 엔진·웹소켓이 KBJ 런타임에서 돈다 |
| D-P7-20 | **새 import-linter 계약(다음 번호, WRAP)**: (가) 공개 진단(`kbj.engines.diagnosis.public`·`kbj.services.engine.diag_pub`·`kbj.services.public_export.diag_json`)은 로그인 원천·로그인 저장소·`kbj.core.gex`·`kbj.engines.gex` 를 모른다 (나) 웹소켓 라이브러리(`websockets`)는 `kbj.services.ws_gateway` 밖에서 import 하지 않는다(계약 ⑤ 텔레그램 방식) (다) `kbj.engines.gex`·`kbj.engines.diagnosis` 는 계약 ⑨(순수)에 든다 — 새로 쓰지 않고 시험으로 확인 | 공개 빌드에 로그인 산출이 새지 않게, 웹소켓 연결 지점 하나 |
| D-P7-21 | **ADR 둘(초안은 묶음, 확정은 WRAP)**: ADR-A 'GEX 이식 경계'(D-P7-3·4·5·7·8·10·16 — 옮긴 것·남긴 것·DB 연결·쓰기 주인과 raw 예외·D2 마감·알림 시각), ADR-B '페이지 3 진단 지표'(D-P7-12~15 — 등급 배치·공포탐욕·종합 판정·VKOSPI 대체). ADR 0019 에 'P7 확인 메모'(poller 투자자별 삭제로 §2-2 운영 조건 해소) | 결정 기록 |
| D-P7-22 | **운영 DB 녹화 이관**은 WRAP 이 스크립트·런북(`scripts/gex_migrate_data.sh` — `pg_dump --data-only` 표 목록·행 수 대조 `#P7-3`)만 만든다. 실행은 사용자(VM, §10 Q2·Q7) | 0006 머리말 '[확인 필요]'. 운영 DB 는 레포 밖 |

### 0.2 만드는 것 / 만들지 않는 것 (P7 경계)

| 만든다 (P7) | 만들지 않는다 |
|---|---|
| `kbj/core/gex/`(GX core 이식 — 골든·명세·속성 시험 녹색), GEX 명세 metrics §G0~§G8 | GX 수식 변경(골든 기대값은 바뀌지 않아야 한다 — 바뀌면 이식 버그) |
| GEX 엔진 서비스(KBJ — `gex.engine`), 웹소켓·녹화 서비스(KBJ — `gex.ws_gateway`·`gex.recorder`), raw 골든 | GX poller·scheduler 수집 작업 이식(§10 Q1 — 기본 P9), 웹소켓 **주식** 실시간 시세(SD 가격 방송 대체 — P8 이후 [제안]) |
| 페이지 3 진단: 종합 판정·국장 공포탐욕·반도체 쏠림·시장폭·신용·한국 대 글로벌·수급 진단·GEX 레벨(Flip·월·기대변동폭·VEX·CEX·스큐·HIRO-lite)·이벤트 표시(만기·금통위·수출 속보·실적 집중), 공개판 매크로 위험 게이지 | 새 공포탐욕 외부 출처(CNN·alternative.me 스크랩 — U4 정신), 금통위 '시장 내재 경로'(v0.2 [확인 필요] — §9 R12) |
| `gex.alert`(장중 레벨 알림), `/gex` 봇 명령, 브리핑 GEX·진단 줄, 띠 칩 `gex_flip` | 마감 뒤 새 발송, 야간 알림, LLM 서술 |
| VKOSPI 어댑터·작업(꺼짐) | 야간 투자자별 1분(실행기 트리거 없음 — D-P7-6) |

### 0.3 완료 기준과 증명

| 기준 | 증명 |
|---|---|
| GEXLAB 골든 시험이 KBJ 에서 녹색 | `uv run pytest tests/golden/gex -q`(코어 골든 22·체인 부분집합 14·raw 골든 32 — 수집 항목 수) + `uv run python -m kbj.services.engine.gex_golden core` 와 `raw-parse` 가 종료 0(재생성 없이 기존 파일과 같음). **골든 JSON 은 `git mv` 로 옮기고 내용 해시가 같다**(WRAP 이 `git diff -M --stat` 으로 확인) |
| GEXLAB 명세 시험이 KBJ 에서 녹색 | `uv run pytest tests/unit/core/gex tests/property/gex tests/unit/core/gex/test_sign_conventions.py -q` — GX `docs/metrics.md` §0~§7 을 근거로 한 단위·속성·부호 시험(§1.11 FND-C 1,548 항목). metrics §G0~§G8 의 각 절이 시험 파일을 가리킨다 |
| 진단 지표 명세가 `docs/metrics.md` 에 | §D1~§D9(부록 A) — 지표마다 공식·입력·단위·상태 구간·품질 규칙·경계 시험·'과거 날짜로 다시 계산해도 같다'(시점 입력만 — `published_at ≤ as_of`). `tests/unit/engines/diagnosis/test_spec_*.py` 가 절 번호를 단다 |
| legacy 시험 수는 옮긴 만큼만 줄었다 | `scripts/test_legacy.sh gexlab` 기준 2,716 → **옮긴 수만큼 뺀 값 + 다리 3**(§1.11 — WRAP 실측), `gexlab-integration` 50 → 옮긴 통합 12 를 뺀 값. 근거 `GX:MIGRATION.md` 'P7' 절에 파일·항목 수 1:1 |
| 웹소켓 직접 호출 0 | `uv run python scripts/check_canonical.py` — 기준선 `kis_ws` 줄 없음, 그룹 목표 0 |
| 쓰기 주인 하나 | 등록부 시험: 같은 표를 `writes` 에 적은 작업·서비스는 하나(raw 예외는 ADR-A 와 `writes` 미기재), `prv_gex.investor_flow` 는 `flows.market_minute` 만. legacy poller 소스에 `FHPTJ04030000`·`investor` 계획 없음(ING 시험) |

### 0.4 등록부(`config/jobs.yaml`)에서 바뀌는 것 (FND-B 가 모양 확정 — 새 작업은 전부 `enabled: false`, 켜기는 WRAP)

| 항목 | 바뀜 |
|---|---|
| `services: gex.engine` | owner `legacy:gexlab/services/engine` → **`kbj.services.engine.gex`**, runner `kbj`, `heartbeat_s: 60`, `writes: [prv_gex.levels, prv_gex.metrics, prv_gex.strike_gex, prv_gex.option_iv, prv_gex.oi_changes]` |
| `services: gex.ws_gateway` | owner → **`kbj.services.ws_gateway`**, runner `kbj`, `heartbeat_s: 60`, `writes: [prv_gex.fut_ticks, prv_gex.opt_ticks]` |
| `services: gex.recorder` | owner → **`kbj.services.recorder`**, runner `kbj`, `heartbeat_s: 60`, `writes: [prv_gex.raw_messages]`(노트: 발행 실패 직접 쓰기 예외 — ADR-A) |
| `services: gex.poller` | 그대로 external. 노트에서 '⚠ 같은 TR 두 곳' 문구를 'P7 — 투자자별 폴링 삭제(D-P7-5)'로 |
| `gex.day_minutes`·`gex.night_minutes`·`gex.krx_derivatives`·`kis.master` | 그대로 external(§10 Q1). 노트에 'KBJ DB(search_path) — P7' |
| 새 `diag.public` | owner `kbj.services.engine.diag_pub:run`, `cron "40 8 * * 1-5"`, `when: trading_day`, 캐치업 10:00, `depends_on` `macro.morning`·`market_stats.kofia`(무른 — `wait_min` 20), `writes: [pub_diag.indicator, pub_diag.gauge, pub_diag.event_marker]` |
| 새 `diag.daily` | owner `kbj.services.engine.diag:run`, `cron "0 16 * * 1-5"`, `when: trading_day`, `depends_on` `market.close_collect`(굳은)·`board.daily`(무른 — `wait_min` 15), `writes: [prv_diag.indicator, prv_diag.fear_greed, prv_diag.verdict]`, notify 없음 |
| 새 `krx.vkospi` | owner `kbj.services.collectors.krx_vkospi:run`, `cron "10 8 * * 1-5"`, `retry {every_s: 600, until: "10:00"}`, `budget: krx`, collects `KRX:idx/drvprod_dd_trd`(as_of prev_trading_day), `writes: [prv_diag.vol_index]`, **enabled false 고정**(실측 `#P7-1` 전) |
| 새 `gex.alert` | owner `kbj.services.engine.gex_alert:run`, `equity {start: open, end: close, every_min: 1}`, `deadline_min: 1`, notify `{kind: alert.gex_level, when: changed}`, `writes: []`(상태는 Redis `gex:alert:last` — 표 아님) |
| `brief.closing` | `depends_on` 에 `diag.daily`(무른 — `wait_min` 10) 추가. 발화 시각 16:00 그대로(보드 → 진단 → 마감 요약 사슬이 16:00 안에 — 실행기가 그 자리에서 끝난 시도를 같은 회차에 거둔다, ADR 0030 §4-6) |
| `flows.market_minute` | 바뀌지 않음(쓰기 주인 그대로). 노트에 '위클리 4조합 P7 켬(GX #13)' — 값은 `markets.yaml` |

---

## 1. 모듈 묶음·웨이브·파일 소유

### 1.0 웨이브

| 웨이브 | 묶음(동시) | 시작 조건 | 예상 |
|---|---|---|---|
| **0** | — | IDX 커밋 + P6 커밋(마이그레이션 0019~, ADR·metrics 절 번호 확정) | — |
| **1** | **FND** = **FND-B**(기반: 마이그레이션·행 자료형·레코드·버스 모델·저장소·카탈로그·등록부·설정·지표 문서·합성 세계·API 모델/라우트/빈 리더·openapi·TS 타입·공개 고리·웹 셸·공용 부품·의존성) ∥ **FND-C**(GX core·골든 core·명세·속성 시험 이식). 두 갈래는 파일이 겹치지 않고, FND-C 가 `kbj/core/gex/` 공개 이름을 먼저 고정해 FND-B 의 레코드 모델이 그것을 import 한다(첫날 오전) | 웨이브 0 | 1.5일 |
| **2** | **ENG**(GEX 엔진 서비스·GEX API·GEX 패널), **ING**(웹소켓·녹화·raw 골든·legacy poller 투자자별 삭제), **DIAG**(진단 엔진·작업·VKOSPI·진단 API·공개 내보내기·페이지 3 본문), **OUT**(브리핑 줄·`gex.alert`·`/gex`·띠 칩) | FND 커밋. 서로의 모듈을 import 하지 않는다 — 저장된 산출을 FND 저장소로 읽는다 | 2~2.5일 |
| **3** | **WRAP** — 작업 켜기·완료 시험·하루 운영 시뮬레이션·CI·계약·legacy 정리·기준선·문서·ADR 확정·이관 런북 | 웨이브 2 전부 | 1일 |

합계 약 4.5~5일(PLAN 3~5일). 묶음마다 빌드 에이전트 + 검증 에이전트(§1.10 명령). **도커 통합·legacy 전체·sim 전체는 묶음이 돌리지 않는다** — 메인이 WRAP 끝에 한 번.

### 1.1 FND-B — 공통 기반 (웨이브 1)

| 경로 | 내용 | 시험 |
|---|---|---|
| `kbj/core/p7_rows.py`(새) | frozen dataclass: `DiagIndicatorRow(trade_date, key, value: Decimal\|None, unit, state, pct_rank\|None, inputs: Mapping, quality, reasons, as_of)`, `GaugeRow`(공개 판정), `FearGreedRow(trade_date, score\|None, band, components, quality, reasons)`, `VerdictRow(trade_date, label, score, parts, event_flags, quality, reasons)`, `EventMarker(date, kind, title, source, detail)`, `VolIndexRow(trade_date, index_id, close, quality)`, `GexLevelView`(API 읽기 모양 — 범위·이름·값·플래그·품질) | `tests/unit/core/test_p7_rows.py` |
| `kbj/core/gex/records.py`(새 — **모델 이동**) | GX 레코드 pydantic 모델을 한 곳으로: `services/engine/records.py`(Level·Metric·StrikeGex·OptionIv·OiChange), `services/poller/records.py`(Chain·Futures·Investor·Expiry·Quarantine), `services/recorder/envelope.py:RawEnvelope`, `data/store.py` 의 `FuturesTickRecord`·`OptionTickRecord`·`SessionLogRecord`·`MinuteBarRecord`. legacy 원래 자리는 다시 내보내기 | 원본 시험은 legacy 에서 shim 으로 계속(옮기지 않음) |
| `kbj/services/runtime/bus.py`·`chain_feed.py`(새 — **이동**) | `GX:services/bus.py`(스트림 이름·`publish_or_none`·인코딩)·`GX:services/chain_feed.py`·`GX:services/poller/context.py:ChainContext`(모델만). legacy shim. `GX:services/runtime.py` 의 하트비트·sink·backoff 7개는 KBJ 정본을 다시 내보낸다 [추정 — P2 가 남긴 이유(GX bus 모델)가 사라짐] | `GX:tests/unit/test_services_runtime.py` 13 은 legacy 에서 그대로(옮기지 않음 — 기준 수 영향 없음) |
| `kbj/store/migrations/<P6 다음>_diag.sql`(새) | §3.3 | `tests/test_store_layout.py`·`tests/integration/test_migrations_pg.py` 확장 |
| `kbj/store/repos/gex.py`(새), `diag.py`(새), `public.py`(추가 — `PublicRepos.diag`), `__init__.py`·`memory.py`(새 속성) | `GexRepo` = D-P7-8 Protocol(쓰기·엔진 읽기) + 화면 읽기(`levels_latest(scope)`·`strike_profile(ts)`·`metrics_series(name, from, to)`·`flags_of(ts)`) + `PgGexRepo`(SQL 은 `GX:data/store.py` 의 같은 함수를 `prv_gex.` 이름으로) + 메모리. `DiagRepo`·`DiagPublicRepo`(공개 저장소는 `pub_diag` 만) | `tests/unit/store/test_gex_repo.py`(메모리·SQL 문자열·0006 열 목록 대조), `test_diag_repo.py` |
| `kbj/data/catalog.py`·`kbj/data/private/kis/datasets.py`(노트 — `ws_ticks` 주인 KBJ·저장 표 셋, `market_investor_intraday` 위클리 켬 근거 #13·야간 `#P7-2`), `kbj/data/private/krx/datasets.py`(새 행 `idx/drvprod_dd_trd` — 칸 [실측 필요 `#P7-1`]) | — | `tests/unit/data/test_catalog.py`·`tests/unit/kis/test_kis_datasets.py` 확장 |
| `config/jobs.yaml`(§0.4), `config/notify.yaml`(`alert.gex_level` 노트: 작업 `gex.alert`·09:00~15:30), `config/limits.yaml`(`kis.priorities.gex_investor` 삭제 — 쓰는 곳 없음, `ws_reconnect_snapshot` 노트 'KBJ ws-gateway'), `config/markets.yaml`(`flows.minute.pairs` 노트 — 값은 WRAP), `config/briefs.yaml`(`night_futures_gex`·`gex_signals` 자리를 `gex_diag`(P7)로 나눔 — status 는 OUT) | — | 등록부 검증·`python -m kbj.services.scheduler validate config/jobs.yaml` |
| `config/gex.yaml`(새 — `GX:config/features.yaml` 값 그대로 + 엔진 주기·따라잡기·알림 조건 [제안]), `config/diag.yaml`(새 — 지표 키·창 250·구간 경계·성분·가중·판정 문턱·실적 집중 N), `kbj/config/{gex,diag}.py`(새 — 모르는 키 오류) | 숫자는 한 곳 | `tests/unit/config/test_p7_config.py` |
| `kbj/services/api/models/{gex,diag}.py`(새)·`routes/{gex,diag}.py`(새)·`readers/{gex,diag}.py`(새 — **빈 리더: 언제나 `NoData`**), `readers/_common.py`(`ApiRepos.gex`·`diag`), `app.py`·`cache.py`(라우터·데이터 버전 키 `gex:latest`·`diag:<날짜>`) | §5 라우트·모델 | `tests/unit/api/test_routes_p7_shape.py`, `python -m kbj.services.api openapi --check` |
| `web/src/api/openapi.json`·`types.gen.ts`(생성) | `python -m kbj.services.api openapi --write && (cd web && npm run gen:types)` | `--check` |
| `kbj/services/public_export/{export,manifest}.py`(고리) + 빈 `diag_json.py` | `diag/` 폴더 | `tests/unit/public_export/test_export.py`·`test_manifest.py` |
| `web/scripts/merge-public-data.{mjs,d.mts}`·`web/src/data/static.ts`(공개 `diag/*`) | — | `web/test/scripts.test.ts`·`source.test.ts` |
| `web/src/app/pages.ts`(3 → `tier: 'mixed'`·plan 문구, `FOUND` glob 에 3), `web/src/pages/p3_diag.ts`(새 — 자리표시 진입: 패널 순서·공개/로그인 갈림·하위 모듈 import 고정), `web/src/pages/p3/{gex,diag}.ts`(새 — 자리표시 '준비 중') | — | `web/test/pager.test.ts`·`tier.test.ts` |
| `web/src/ui/{gauge,ladder,profile}.ts`(새) + `web/src/design/components.css`(부품 클래스만 — 기존 토큰) | D-P7-18 | `web/test/ui_p7.test.ts`(빈 입력·null·음수 막대·좁은 폭 가로 넘침 없음) |
| `tests/fixtures/synthetic/p7_world.py`(새) + `p7_expect.yaml` | 고정 시드 `build_world(seed=20261201)`: GX 합성 체인(골든 생성기 방식 — 주간·야간·만기일·0DTE), 선물 전광판, 엔진 산출 하루치(visible·shadow 섞임), `investor_flow` 7조합(야간 없음), 진단 입력 300거래일(신용·예탁금·신용스프레드·시장폭·외국인·VKOSPI 일부 결측·지연 공표), 이벤트(만기·금통위·실적 몰림·수출 속보 — P6 표 없음 변형) | `tests/unit/fixtures/test_p7_world.py`(결정성·`_source: synthetic`) |
| `tests/unit/api/make_web_fixtures.py`(`render_p7()`·`--only gex\|diag`) + 빈 `tests/unit/api/web_fixtures_p7/{gex,diag}.py` | 도메인 묶음이 자기 모듈·`web/test/fixtures/api/<도메인>/**` 만 | `tests/unit/api/test_web_fixtures.py` |
| `tests/unit/scheduler/test_registry_validation.py`(P7 행 단언 — `ENABLED_BY_PHASE` 는 WRAP) | — | 등록부 검증 |
| `pyproject.toml`·`uv.lock`(D-P7-19), `tests/test_import_contracts.py`(`RUNTIME_IMPORTS` 에 `vollib`·`websockets`) | — | `tests/test_import_contracts.py` |
| `docs/metrics.md`(§G0~§G8 = `GX:docs/metrics.md` 옮김·경로만 KBJ 로, §D1~§D9 = 부록 A), `GX:docs/metrics.md`(머리에 '정본은 `docs/metrics.md` §G — P7' 한 줄), `docs/probe_results.md`(`#P7-1~#P7-6` 칸) | '명세 먼저' | 문서 |

FND-B 가 하지 않는 것: 엔진·웹소켓·진단 본문, 리더·페이지 본문, 가짜 KRX 의 VKOSPI 응답(DIAG), sim 시나리오(WRAP).

### 1.2 FND-C — GX core 이식 (웨이브 1, FND-B 와 동시)

| 경로 | 내용 | 시험 |
|---|---|---|
| `kbj/core/gex/{black76,greeks,iv,forward,chain,preprocess,gex,levels,specs}.py`·`kbj/core/gex/metrics/{exposure,flow,futures,vol}.py`(`git mv` + import 경로) | 수식·기본값 그대로. `core.calendar` → `kbj.core.calendar`(이미 정본). pyright strict(`kbj/core`)·ruff 통과 — 타입 보강만, 동작 변경 금지 | 아래 |
| `kbj/core/gex/snapshot.py`(새 — `GX:scripts/validate_greeks.py:Snapshot` 의 체인 스냅샷 읽기만 옮김) | 골든·체인 부분집합 입력 | `tests/golden/gex/test_chain_subset.py` |
| `kbj/engines/gex/golden.py`(순수 — `core_golden`·`compare_golden`·`tolerance`·`check_schema`·`chain_subset`), `kbj/services/engine/gex_golden.py`(CLI `core`·`chain-subset`·`[--write]`, 파일 읽기·쓰기) | `GX:scripts/make_golden.py` 의 core 부분. raw 부분은 ING | `tests/golden/gex/test_core_golden.py` |
| `tests/golden/gex/core/chain_synthetic_20260928_small.json`(`git mv`) | 내용 그대로 | 해시 같음 |
| `tests/unit/core/gex/**`·`tests/property/gex/**`(`git mv` + import) | GX 단위 `test_{black76,chain,forward,gex,greeks,iv,levels,preprocess,specs,exposure,flow,futures_metrics,vol}`·속성 9파일·`test_sign_conventions` | §1.11 |
| `legacy/gexlab/core/**`(shim — `from kbj.core.gex.<모듈> import *` 와 공개 이름), `legacy/gexlab/scripts/make_golden.py`(core 부분을 KBJ 로 다시 내보냄), `legacy/gexlab/tests/unit/test_kbj_gex_core_bridge.py`(새 다리 +1) | 남은 legacy(엔진 전·validate_greeks·metric_report)가 그대로 돈다 | `scripts/test_legacy.sh gexlab`(FND-C 가 이 부분만 돌린다 — 새 기준 수는 WRAP 이 고정) |

`core/features.py`(플래그 YAML 읽기·파일 감시 — I/O)는 FND-C 가 옮기지 않는다 → ENG(`kbj/config/gex.py` 의 플래그 감시).

### 1.3 ENG — GEX 엔진 서비스 (웨이브 2)

| 경로 | 내용 |
|---|---|
| `kbj/engines/gex/{evaluate,flow,extended,futures,daily,registry,context}.py` | `GX:services/engine/*` 의 계산부(순수 — Redis·DB·스레드 없음). 입력은 레코드 모델, 출력은 Level·Metric 레코드 |
| `kbj/services/engine/gex.py`(+ `__main__` 진입 `python -m kbj.services.engine.gex`) | `GX:services/engine/service.py`·`publish.py` 의 실행부 — `chain.ready` 구독·10초 따라잡기·POST_DAY 일별·틱 구독(HIRO-lite)·`investor_flow` 읽기(쓰지 않음)·발행(`engine.levels`·`engine.metrics`·`engine:latest`)·하트비트. 저장은 `GexRepo`(D-P7-8). 플래그 감시는 `kbj/config/gex.py` |
| `kbj/config/gex.py`(본문 — 플래그 감시 `FeatureWatcher`)·`config/gex.yaml`(값 — 엔진 노브만) | `GX:core/features.py` 승계 |
| `kbj/services/api/readers/gex.py`·`models/gex.py`(본문) | §5 GEX 라우트. shadow 는 응답에서 값 대신 `{state: 'shadow'}`, `invalid` 는 뺌 + 수 |
| `web/src/pages/p3/gex.ts` | GEX 패널(§6.2) |
| `tests/unit/api/web_fixtures_p7/gex.py`·`web/test/fixtures/api/gex/**`·`web/test/pages/p3_gex.test.ts` | — |
| `tests/unit/engines/gex/**`(승격 — `test_engine_{daily,evaluate,extended,flow,futures,investor,service,ticks}`·`test_features`), `tests/unit/store/test_gex_repo_engine.py`(GX `test_store_engine` 32 중 KBJ 저장소에 해당하는 단언 승격, 나머지는 폐지 — 대체 표기), `tests/integration/test_gex_engine_pg.py`(GX `test_engine_store` 9 승격) | §1.11 |
| `legacy/gexlab/services/engine/**`(shim·`__main__` 은 KBJ 서비스 실행), `legacy/gexlab/core/features.py`(shim), `legacy/gexlab/tests/unit/test_kbj_gex_engine_bridge.py`(+1), 옮긴 시험 파일 삭제 | — |
| ADR-A 초안(엔진·DB·쓰기 주인 부분) | — |

### 1.4 ING — 웹소켓·녹화·raw 골든·legacy 수집 연결 (웨이브 2)

| 경로 | 내용 |
|---|---|
| `kbj/services/ws_gateway/{endpoints,client,session,subscriptions,budget,watch,backoff,service}.py`(+ `__main__`) | `GX:services/ws_gateway/**` 이식. 호스트 상수는 `endpoints.py` 하나(D-P7-10). 접속키 `reader(…, "ws_key")` 읽기만. 체결 → `GexRepo.write_fut_ticks/opt_ticks`, 원문 → `ws.raw` 발행(실패 시 직접 `write_raw` — 예외) |
| `kbj/services/recorder/{service,golden}.py`(+ `__main__`) | `GX:services/recorder/service.py` + `make_golden.py` 의 `raw`·`raw-parse`(`sanitize`·`secret_hits`·`cut_raw`·`DbRawSource` — DB 읽기는 KBJ DSN) |
| `kbj/data/private/kis/ws.py`·`derivatives.py`(새 — `GX:data/kis/ws.py`·`models.py` 이식, 원천 어댑터 자리) | 웹소켓 프레임·체결 파서, REST 파생 응답 모델(전광판·선물·월물리스트·분봉·투자자) — legacy poller 는 shim 으로 계속 쓴다 |
| `config/{kis_ws,kis_ws_fields,ws_budget}.yaml`(`git mv` — `kis_ws.yaml` 의 `real`·`vts` 호스트 줄 삭제) | — |
| `tests/golden/gex/raw/**`(`git mv`)·`tests/golden/gex/test_raw_golden.py`·`synthetic.py` | raw 골든 |
| `tests/unit/ws_gateway/**`·`tests/unit/recorder/**`·`tests/unit/kis/test_{kis_ws,derivatives}.py`(승격), `tests/integration/test_recorder_golden_pg.py`(GX `test_make_golden_db` 3 승격), `tests/fakes/ws_server.py`(필요한 프레임만 추가) | §1.11 |
| `legacy/gexlab/services/{ws_gateway,recorder}/**`·`data/kis/{ws,models}.py`(shim), `legacy/gexlab/scripts/make_golden.py`(raw 부분 다시 내보냄 — FND-C 뒤 순서), `legacy/gexlab/config/kis_ws.yaml` 등(옮긴 뒤 삭제 — 읽는 곳이 KBJ 경로로 바뀌었는지 grep), `legacy/gexlab/tests/unit/test_kbj_gex_ingest_bridge.py`(+1) | — |
| `legacy/gexlab/services/poller/{planner,config}.py`(D-P7-5 — 투자자별 계획 삭제), `legacy/gexlab/tests/unit/test_poller_{planner,collector,service}.py`(투자자별 단언을 '부르지 않는다'로 — 항목 수 유지가 원칙, 줄면 MIGRATION 에 1:1) | ADR 0019 §2-2 |
| `legacy/gexlab/config/settings.py`(DSN 에 `options=-csearch_path=prv_gex,ops` — KBJ DB 연결 [확인 필요: GX 설정 이름]), `legacy/gexlab/docker-compose.yml`(`migrate` 서비스가 KBJ DB 에는 돌지 않게 — 통합 시험용 자기 DB 는 그대로) | D-P7-4 |
| ADR-A 초안(웹소켓·raw 예외·poller 부분) | — |

### 1.5 DIAG — 진단 지표·페이지 3 본문 (웨이브 2)

| 경로 | 내용 |
|---|---|
| `kbj/engines/diagnosis/{bands,indicators,fear_greed,verdict,events}.py`·`public.py` | 부록 A 순수 계산. 반도체 쏠림·경기민감 대 방어는 `kbj.engines.market.ribbon` 의 공식을 **불러 쓴다**(복사 금지), 시장폭은 `kbj.engines.market.breadth`, 신용스프레드는 `kbj.engines.macro.derive`. `public.py` 는 공개 입력만(계약 가) |
| `kbj/services/engine/{diag,diag_pub}.py` | 작업 처리기 — 저장소로 입력을 시점 기준(`published_at ≤ as_of`)으로 읽고 `prv_diag`·`pub_diag` 에 쓴다 |
| `kbj/data/private/krx/vol_index.py`(새 파서)·`kbj/services/collectors/krx_vkospi.py`, `tests/fakes/krx_server.py`(VKOSPI 엔드포인트만 추가) | D-P7-14 |
| `kbj/services/api/readers/diag.py`·`models/diag.py`(본문), `kbj/services/public_export/diag_json.py`(본문) | §5·§7 |
| `web/src/pages/p3_diag.ts`(본문)·`web/src/pages/p3/diag.ts` | §6 (GEX 패널 `p3/gex.ts` 는 ENG) |
| `tests/unit/engines/diagnosis/**`(명세·경계·재계산 같음), `tests/property/test_diag_props.py`(백분위 단조·성분 빠짐 규칙), `tests/unit/collectors/test_krx_vkospi.py`, `tests/unit/api/test_routes_diag.py`, `tests/unit/public_export/test_diag_json.py`, `tests/unit/api/web_fixtures_p7/diag.py`·`web/test/fixtures/api/diag/**`·`web/test/pages/p3_diag.test.ts` | — |
| ADR-B 초안 | — |

### 1.6 OUT — 브리핑·알림·봇·띠 칩 (웨이브 2)

| 경로 | 내용 |
|---|---|
| `kbj/reports/gex/{lines,alert}.py`(새 — 순수 문장·조건) | 브리핑 GEX 줄(Flip·월·±1σ·체제, visible 만), 진단 줄(종합 판정·공포탐욕 구간·변화), 알림 조건 D-P7-17 |
| `kbj/services/engine/gex_alert.py` | `gex.alert` 처리기 — `GexRepo.levels_latest` 읽고 직전 상태(Redis `gex:alert:last`)와 비교, notify outbox 에 `alert.gex_level` |
| `kbj/reports/briefs/{morning_brief,closing_brief}.py`(줄 자리만), `config/briefs.yaml`(`gex_diag` status) | 아침 ④·마감 GEX·진단 줄. 미준비면 기존 '미준비' 규칙 |
| `kbj/services/notifier/commands.py`(`/gex` 처리기 등록 — 요약 한 덩어리, 로그인 등급 채팅만) | — |
| `kbj/engines/market/ribbon.py`(`gex_flip` 칩 계산), `kbj/services/api/readers/market.py`(띠 입력에 GEX 최신), `web/src/app/ribbon.ts`(`gex_flip` 의 `phase: 'P7'` 제거) | metrics §6.5 'GEX Flip' 줄을 §G3.5 로 |
| `tests/unit/reports/gex/**`·`tests/unit/reports/briefs/test_*_p7.py`·`tests/unit/engines/market/test_ribbon_gex.py`·`tests/unit/notifier/test_cmd_gex.py`·`web/test/pages/ribbon.test.ts`(칩 하나) | — |

### 1.7 WRAP — 마무리 (웨이브 3)

1. **켜기**: `config/jobs.yaml` — `gex.engine`·`gex.ws_gateway`·`gex.recorder`(서비스 — 데이터는 GX 운영으로 확인됨), `diag.public`·`diag.daily`·`gex.alert` → `enabled: true`. `krx.vkospi` 는 꺼진 채(`#P7-1`). `config/markets.yaml` 위클리 4조합 `enabled: true`(D-P7-6). `ENABLED_BY_PHASE["P7"]`.
2. **완료 시험**: `tests/acceptance/test_p7_golden_spec.py`(§0.3 골든·명세 경로를 한 번에 모아 수를 단언 — 시험이 조용히 덜 모이면 실패), `tests/sim/test_one_day_p7.py`(2026-12-03(목) 05:00 ~ 12-04 05:00 — 위클리 목요일 만기일 포함): 엔진이 합성 체인으로 사이클·`diag.public` 08:40·`diag.daily` 16:00 뒤 `brief.closing` 1통(GEX·진단 줄)·`gex.alert` 는 15:30 뒤 0통·`investor_flow` 쓰기는 `flows.market_minute` 만·KIS 어떤 1초에도 ≤ 4, `web/test/acceptance/p7_pages.test.ts`(공개 빌드 페이지 3 에 로그인 값 0).
3. **계약**(D-P7-20) + `tests/test_import_contracts.py`·`tests/unit/test_public_writers.py`(`diag.public` SQL 은 `pub_` 만).
4. **legacy 정리**: `GX:MIGRATION.md` 'P7' 절(옮긴·폐지 시험 파일·항목 수 1:1, shim 목록, 남긴 것), `scripts/test_legacy.sh` 의 `gexlab`·`gexlab-integration` 기준 수·머리말(§1.11 실측), `.github/workflows/ci.yml`(KBJ 새 시험 경로·통합 경로·legacy 기준).
5. **기준선**: `scripts/canonical_baseline.txt` 의 `kis_ws` 줄 삭제(`--update-baseline`), `scripts/check_canonical.py` 의 `kis_ws` 그룹을 목표 0 으로, `tests/test_check_canonical.py`.
6. **루트 `docker-compose.yml`**: `gex-engine`·`ws-gateway`·`recorder` 서비스(KBJ 이미지, 하트비트 healthcheck). legacy GX compose 는 통합 시험용으로 남는다(운영 VM 에서는 §10 Q7 순서로 GX 쪽 셋을 끈다).
7. **이관 런북**: `scripts/gex_migrate_data.sh`(표 목록·`--data-only`·행 수 대조, 실행하지 않음 — `#P7-3`), `docs/secrets.md` 확인(새 이름 없음 — KIS 접속키는 auth 그대로).
8. **문서·ADR**: ADR-A·ADR-B 확정, ADR 0019 'P7 확인 메모', `docs/metrics.md` §G·§D 확정, `docs/DATA_TIERS.md` 페이지 3 행 확인(바뀐 것 없으면 그대로), `docs/PLAN.md` §8 P7 '결과' 절, `docs/probe_results.md` `#P7-*`, 이 문서 상태 '구현 완료'.
9. 메인이 마지막에 도커 통합(KBJ + `gexlab-integration`)·`test_legacy.sh all`·sim 전체를 한 번.

### 1.8 legacy 이전 범위 (D-P7-9 — 옮긴 만큼만)

| legacy | P7 처리 | 옮김(KBJ) | 폐지 | legacy 에 남김 | 기준선 | 묶음 |
|---|---|---|---|---|---|---|
| `GX:core/**`(features 빼고) | `kbj/core/gex` 로, legacy shim | 단위 13파일·속성 9파일·부호 1·골든 core·체인 부분집합 | — | shim | — | FND-C |
| `GX:services/engine/**`·`core/features.py` | `kbj/engines/gex`·`kbj/services/engine/gex`·`kbj/config/gex`, shim(`__main__` 포함) | 엔진 8파일·features·store_engine 해당분·통합 engine_store | store_engine 의 GX `PostgresSink` 전용 단언 [추정 일부] | shim | — | ENG |
| `GX:services/{ws_gateway,recorder}/**`·`data/kis/{ws,models}.py`·`config/kis_ws*.yaml`·`ws_budget.yaml` | KBJ 로, shim, YAML 은 `git mv` | ws 6파일·kis_ws·kis_models·recorder 2·raw 골든·통합 make_golden_db | — | shim | `kis_ws` −4 → 0 | ING |
| `GX:services/bus.py`·`chain_feed.py`·레코드 모델 | `kbj/services/runtime`·`kbj/core/gex/records`, shim | 0(시험은 legacy 에서 shim 으로) | — | shim | — | FND-B |
| `GX:services/poller/**` | **남김** — 투자자별 계획만 삭제 | — | 투자자별 단언 → '부르지 않음' 단언 | 전부 | — | ING |
| `GX:services/scheduler/**`·`services/gaps.py`·`data/store.py`·`db/**` | **남김**(§10 Q1) | — | — | 전부 | — | — |
| `GX:scripts/{validate_greeks,metric_report,shadow_report,nogap_report,probe_*}.py` | **남김**(운영 도구 — core 는 shim 으로) | — | — | 전부 | — | — |
| `GX:docs/metrics.md` | 정본을 `docs/metrics.md` §G 로, 원본에 한 줄 | — | — | 기록용 | — | FND-B |

### 1.9 묶음별 파일 소유 (겹치지 않는다)

| 묶음 | 손대는 파일 |
|---|---|
| FND-B | `kbj/core/p7_rows.py`, `kbj/core/gex/records.py`, `kbj/services/runtime/{bus,chain_feed}.py`, `kbj/store/migrations/<P6 다음>_diag.sql`, `kbj/store/repos/{gex,diag}.py`·`public.py`·`__init__.py`·`memory.py`, `kbj/data/catalog.py`, `kbj/data/private/kis/datasets.py`, `kbj/data/private/krx/datasets.py`, `config/{jobs,notify,limits,markets,briefs,gex,diag}.yaml`(웨이브 1), `kbj/config/{gex,diag}.py`(웨이브 1 — 읽기 틀), `kbj/services/api/models/{gex,diag}.py`·`routes/{gex,diag}.py`·`readers/{gex,diag}.py`(빈)·`readers/_common.py`·`app.py`·`cache.py`, `web/src/api/{openapi.json,types.gen.ts}`, `kbj/services/public_export/{export,manifest,diag_json}.py`(고리·빈), `web/scripts/merge-public-data.{mjs,d.mts}`, `web/src/data/static.ts`, `web/src/app/pages.ts`, `web/src/pages/p3_diag.ts`·`p3/{gex,diag}.ts`(자리표시), `web/src/ui/{gauge,ladder,profile}.ts`, `web/src/design/components.css`, `tests/fixtures/synthetic/{p7_world.py,p7_expect.yaml}`, `tests/unit/api/make_web_fixtures.py`·`web_fixtures_p7/__init__.py`, `tests/unit/scheduler/test_registry_validation.py`(P7 행), `tests/test_store_layout.py`, `tests/integration/test_migrations_pg.py`, `pyproject.toml`·`uv.lock`·`tests/test_import_contracts.py`(의존성), `docs/metrics.md`, `docs/probe_results.md`, `legacy/gexlab/docs/metrics.md`(한 줄), legacy shim: `legacy/gexlab/services/{bus,chain_feed}.py`·`services/engine/records.py`·`services/poller/{records,context}.py`(모델 부분)·`services/recorder/envelope.py`·`data/store.py`(레코드 클래스 정의를 import 로 바꾸는 부분만)·`services/runtime.py` |
| FND-C | `kbj/core/gex/**`(records 빼고), `kbj/engines/gex/golden.py`, `kbj/services/engine/gex_golden.py`, `tests/unit/core/gex/**`, `tests/property/gex/**`, `tests/golden/gex/{core/**,test_core_golden.py,test_chain_subset.py}`, `legacy/gexlab/core/**`(shim — `features.py` 빼고), `legacy/gexlab/scripts/make_golden.py`(core 부분), 옮긴 legacy 시험 파일 삭제, `legacy/gexlab/tests/unit/test_kbj_gex_core_bridge.py` |
| ENG | `kbj/engines/gex/**`(golden.py 빼고), `kbj/services/engine/gex.py`, `kbj/config/gex.py`(본문)·`config/gex.yaml`(값), `kbj/services/api/{readers,models}/gex.py`(본문), `web/src/pages/p3/gex.ts`, `tests/unit/engines/gex/**`, `tests/unit/store/test_gex_repo_engine.py`, `tests/integration/test_gex_engine_pg.py`, `tests/unit/api/{test_routes_gex.py,web_fixtures_p7/gex.py}`, `web/test/fixtures/api/gex/**`, `web/test/pages/p3_gex.test.ts`, `legacy/gexlab/services/engine/**`(shim)·`core/features.py`(shim)·옮긴 시험 삭제·`tests/unit/test_kbj_gex_engine_bridge.py`, ADR-A 초안(엔진 절) |
| ING | `kbj/services/ws_gateway/**`, `kbj/services/recorder/**`, `kbj/data/private/kis/{ws,derivatives}.py`, `config/{kis_ws,kis_ws_fields,ws_budget}.yaml`(`git mv`), `tests/golden/gex/{raw/**,test_raw_golden.py,synthetic.py}`, `tests/unit/{ws_gateway,recorder}/**`, `tests/unit/kis/test_{kis_ws,derivatives}.py`, `tests/integration/test_recorder_golden_pg.py`, `tests/fakes/ws_server.py`, `legacy/gexlab/services/{ws_gateway,recorder}/**`·`data/kis/{ws,models}.py`(shim)·`scripts/make_golden.py`(raw 부분 — FND-C 뒤)·`config/{kis_ws,kis_ws_fields,ws_budget}.yaml`(삭제)·`services/poller/{planner,config}.py`·`config/settings.py`·`docker-compose.yml`, 해당 legacy 시험 파일(삭제·수정)·`tests/unit/test_kbj_gex_ingest_bridge.py`, ADR-A 초안(웹소켓·raw·poller 절) |
| DIAG | `kbj/engines/diagnosis/**`, `kbj/services/engine/{diag,diag_pub}.py`, `kbj/data/private/krx/vol_index.py`, `kbj/services/collectors/krx_vkospi.py`, `config/diag.yaml`(값), `kbj/services/api/{readers,models}/diag.py`(본문), `kbj/services/public_export/diag_json.py`(본문), `web/src/pages/p3_diag.ts`(본문)·`p3/diag.ts`, `tests/fakes/krx_server.py`, `tests/unit/engines/diagnosis/**`, `tests/property/test_diag_props.py`, `tests/unit/collectors/test_krx_vkospi.py`, `tests/unit/api/{test_routes_diag.py,web_fixtures_p7/diag.py}`, `tests/unit/public_export/test_diag_json.py`, `web/test/fixtures/api/diag/**`, `web/test/pages/p3_diag.test.ts`, ADR-B 초안 |
| OUT | `kbj/reports/gex/**`, `kbj/services/engine/gex_alert.py`, `kbj/reports/briefs/{morning_brief,closing_brief}.py`, `config/briefs.yaml`(status), `kbj/services/notifier/commands.py`, `kbj/engines/market/ribbon.py`, `kbj/services/api/readers/market.py`, `web/src/app/ribbon.ts`, `tests/unit/reports/gex/**`, `tests/unit/reports/briefs/test_*_p7.py`, `tests/unit/engines/market/test_ribbon_gex.py`, `tests/unit/notifier/test_cmd_gex.py`, `web/test/pages/ribbon.test.ts` |
| WRAP | `config/jobs.yaml`·`config/markets.yaml`(웨이브 3 — enabled), `tests/unit/scheduler/test_registry_validation.py`(`ENABLED_BY_PHASE`), `tests/acceptance/test_p7_golden_spec.py`, `tests/sim/{test_one_day_p7.py,harness.py,p7.py}`, `web/test/acceptance/p7_pages.test.ts`, `pyproject.toml`·`uv.lock`(계약), `tests/test_import_contracts.py`, `tests/unit/test_public_writers.py`, `.github/workflows/ci.yml`, `scripts/{canonical_baseline.txt,check_canonical.py,test_legacy.sh,gex_migrate_data.sh}`, `tests/test_check_canonical.py`, `docker-compose.yml`(루트), `legacy/gexlab/MIGRATION.md`, `docs/{metrics,PLAN,probe_results,DATA_TIERS,secrets}.md`·`docs/adr/0019-*.md`·ADR-A·ADR-B·`docs/p7_design.md`(상태) |

**공유 파일 — 누가 언제**(한 칸에 한 묶음. P7 이 손대는 공유 파일 전부)

| 파일 | 웨이브 1 | 웨이브 2 | 웨이브 3 |
|---|---|---|---|
| `config/jobs.yaml` | **FND-B**(§0.4, 새 작업 false) | 안 고침 — 요청 | **WRAP**(enabled) |
| `config/{notify,limits}.yaml` | **FND-B** | 안 고침 | — |
| `config/markets.yaml` | **FND-B**(노트) | 안 고침 | **WRAP**(위클리 켬) |
| `config/briefs.yaml` | **FND-B**(자리) | **OUT**(status·문구) | — |
| `config/{gex,diag}.yaml`(새) | **FND-B**(모양) | **ENG** gex 값 / **DIAG** diag 값 | — |
| `kbj/data/catalog.py`·`kis/datasets.py`·`krx/datasets.py` | **FND-B** | 안 고침 | — |
| `kbj/store/repos/{__init__,memory,public}.py`·`gex.py`·`diag.py` | **FND-B** | 안 고침(모자라면 FND 담당에 요청 — 대기) | — |
| `kbj/services/api/{app,cache}.py`·`readers/_common.py` | **FND-B** | 안 고침 | — |
| `kbj/services/api/readers/market.py` | — | **OUT**(띠 GEX 입력) | — |
| `kbj/services/api/{models,readers,routes}/{gex,diag}.py` | **FND-B**(모양·빈) | **ENG** gex / **DIAG** diag | — |
| `web/src/api/{openapi.json,types.gen.ts}` | **FND-B**(생성) | 생성 전용 — 모델 바꾼 묶음이 명령만 | **WRAP**(`--check`) |
| `kbj/services/public_export/{export,manifest}.py` | **FND-B** | 안 고침 | — |
| `web/src/app/pages.ts`·`web/src/data/static.ts`·`web/scripts/merge-public-data.*`·`web/src/ui/*`·`web/src/design/components.css` | **FND-B** | 안 고침 | — |
| `web/src/app/ribbon.ts` | — | **OUT** | — |
| `web/src/pages/p3_diag.ts`·`p3/*` | **FND-B**(자리표시) | **DIAG** 진입·diag / **ENG** gex | — |
| `kbj/engines/market/ribbon.py` | — | **OUT** | — |
| `kbj/reports/briefs/{morning,closing}_brief.py` | — | **OUT** | — |
| `kbj/services/notifier/commands.py` | — | **OUT** | — |
| `tests/fakes/{ws_server,krx_server}.py` | — | **ING** ws / **DIAG** krx | WRAP 은 쓰기만 |
| `tests/sim/harness.py` | — | 안 고침 | **WRAP** |
| `tests/fixtures/synthetic/p7_world.py` | **FND-B** | 안 고침(요청) | WRAP(보강) |
| `tests/unit/api/make_web_fixtures.py` | **FND-B** | 도메인 모듈만 | — |
| `tests/unit/scheduler/test_registry_validation.py` | **FND-B**(P7 행) | 안 고침 | **WRAP**(`ENABLED_BY_PHASE`) |
| `tests/test_store_layout.py`·`tests/integration/test_migrations_pg.py` | **FND-B** | — | — |
| `pyproject.toml`·`uv.lock`·`tests/test_import_contracts.py` | **FND-B**(의존성) | 안 고침 | **WRAP**(계약) |
| `docs/metrics.md` | **FND-B**(§G·§D) | 안 고침(정의 변경은 요청) | **WRAP** |
| `docs/probe_results.md` | **FND-B**(`#P7-*` 칸) | 안 고침 | **WRAP** |
| `legacy/gexlab/scripts/make_golden.py` | **FND-C**(core) | **ING**(raw) | — |
| `legacy/gexlab/services/poller/**` | **FND-B**(`records`·`context` 모델 shim) | **ING**(`planner`·`config` 투자자별) | — |
| `legacy/gexlab/data/store.py` | **FND-B**(레코드 import) | 안 고침 | — |
| `legacy/gexlab/MIGRATION.md`·`scripts/test_legacy.sh`·`.github/workflows/ci.yml`·`scripts/{canonical_baseline.txt,check_canonical.py}`·`docker-compose.yml`(루트) | — | 안 고침(묶음은 수만 보고) | **WRAP** |
| `docs/adr/*` | — | 초안: ADR-A ENG·ING(절 나눔), ADR-B DIAG | **WRAP** 확정 + 0019 메모 |

### 1.10 묶음별 완료 확인 명령 (모두 종료 코드 0)

> 같은 작업 트리에서 여러 묶음이 동시에 일한다 — 자기 경로 시험 + 빠른 공통 명령만 돌리고, 실패는 자기 파일 것만 고친다. **도커 통합·`test_legacy.sh all`·sim 전체는 돌리지 않는다**(메인이 WRAP 뒤 한 번). `-q` 로 실패만 본다. 시험이 읽는 파일이 `.gitignore` 에 걸리지 않았는지 `git status --ignored`.

**공통(모든 백엔드 묶음)**: `uv run ruff check && uv run ruff format --check && uv run pyright && uv run lint-imports && uv run python scripts/check_canonical.py && uv run python scripts/check_public_safety.py --exclude legacy && uv run pytest -m "not sim and not integration and not legacy" -q` — 웹을 고친 묶음은 `cd web && npm run lint && npm run typecheck && npx vitest run <내 시험>`.

| 묶음 | 묶음 명령(공통에 더해) |
|---|---|
| FND-B | `uv run pytest tests/test_store_layout.py tests/test_import_contracts.py tests/unit/core/test_p7_rows.py tests/unit/store tests/unit/data tests/unit/config/test_p7_config.py tests/unit/scheduler tests/unit/fixtures/test_p7_world.py tests/unit/api/test_routes_p7_shape.py tests/unit/api/test_web_fixtures.py tests/unit/public_export -q && uv run python -m kbj.services.scheduler validate config/jobs.yaml && uv run python -m kbj.services.api openapi --check && (cd web && npm run gen:types -- --check && npx vitest run test/ui_p7.test.ts test/pager.test.ts test/tier.test.ts) && uv run bash scripts/test_legacy.sh gexlab` |
| FND-C | `uv run pytest tests/unit/core/gex tests/property/gex tests/golden/gex/test_core_golden.py tests/golden/gex/test_chain_subset.py -q && uv run python -m kbj.services.engine.gex_golden core && uv run pyright kbj/core/gex && uv run bash scripts/test_legacy.sh gexlab` |
| ENG | `uv run pytest tests/unit/engines/gex tests/unit/store/test_gex_repo_engine.py tests/unit/api/test_routes_gex.py tests/unit/config/test_p7_config.py -q && uv run python -m tests.unit.api.make_web_fixtures --only gex && uv run pytest tests/unit/api/test_web_fixtures.py -q -k gex && (cd web && npx vitest run test/pages/p3_gex.test.ts) && uv run bash scripts/test_legacy.sh gexlab` |
| ING | `uv run pytest tests/unit/ws_gateway tests/unit/recorder tests/unit/kis/test_kis_ws.py tests/unit/kis/test_derivatives.py tests/golden/gex/test_raw_golden.py tests/unit/fakes -q && uv run python -m kbj.services.recorder.golden raw-parse && uv run python scripts/check_canonical.py && grep -rn "FHPTJ04030000\|_investors" legacy/gexlab/services/poller; test $? -eq 1 && uv run bash scripts/test_legacy.sh gexlab` |
| DIAG | `uv run pytest tests/unit/engines/diagnosis tests/property/test_diag_props.py tests/unit/collectors/test_krx_vkospi.py tests/unit/api/test_routes_diag.py tests/unit/public_export/test_diag_json.py tests/unit/engines/market -q && uv run python -m tests.unit.api.make_web_fixtures --only diag && uv run pytest tests/unit/api/test_web_fixtures.py -q -k diag && (cd web && npx vitest run test/pages/p3_diag.test.ts) && uv run python scripts/check_public_safety.py` |
| OUT | `uv run pytest tests/unit/reports/gex tests/unit/reports/briefs tests/unit/engines/market tests/unit/notifier/test_cmd_gex.py tests/unit/api/test_routes_market.py -q && uv run python -m kbj.services.scheduler validate config/jobs.yaml && (cd web && npx vitest run test/ribbon.test.ts)` |
| WRAP | 공통 + `uv run pytest tests/acceptance -q && uv run pytest tests/sim -m "sim and not slow" -q -k "p7 or brief_once or intraday_minute" && uv run python -m kbj.services.scheduler validate config/jobs.yaml && uv run python -m kbj.services.api openapi --check && uv run python scripts/check_public_safety.py && bash scripts/build_public_site.sh --dry-run` + `cd web && npm run gen:types -- --check && npm run lint && npm run typecheck && npm test && npm run build:public && npm run build:login && node scripts/check-bundle.mjs` + 메인: Docker 통합(KBJ·`gexlab-integration`)·`test_legacy.sh all`·sim 전체 |

### 1.11 옮기는 원본 시험 수 (수집 항목 수 — 2026-10-13 `--collect-only` 로 센 값, WRAP 이 다시 실측)

| 단계 | 옮김(KBJ 로) | 항목 |
|---|---|---|
| FND-C | 단위 `black76` 260·`greeks` 497·`forward` 119·`iv` 117·`gex` 109·`specs` 80·`levels` 75·`chain` 63·`flow` 46·`preprocess` 38·`vol` 23·`futures_metrics` 16·`exposure` 14(= 1,457), 속성 9파일 46(`probe_properties` 2 는 남김), `test_sign_conventions` 9, 골든 `core` 22·`chain_subset` 14 | **1,548** |
| ENG | `engine_service` 29·`engine_evaluate` 25·`engine_ticks` 22·`features` 21·`engine_flow` 14·`engine_investor` 11·`engine_daily` 10·`engine_extended` 8·`engine_futures` 6(= 146) + `store_engine` 32(승격·폐지 합 — 폐지분은 대체 시험 기록) | **178** |
| ING | `kis_ws` 38·`kis_models` 39·`ws_client` 50·`ws_gateway_service` 20·`ws_watch` 20·`ws_subscriptions` 10·`ws_backoff` 6·`ws_budget` 3·`recorder_envelope` 8·`recorder_service` 7·골든 `raw` 32 | **233** |
| 합 | — | **1,959** |

- `scripts/test_legacy.sh gexlab` 기준: **2,716 − 1,959 + 다리 3 = 760 [추정]**. poller 투자자별 단언은 '부르지 않는다'로 바꿔 수를 유지한다(줄면 그 수를 MIGRATION 에 1:1 로 적고 기준에서 뺀다 — 옮긴 것 말고는 줄지 않는다는 규칙의 유일한 예외 경로, WRAP 이 확인).
- `gexlab-integration`: 50 − `test_engine_store` 9 − `test_make_golden_db` 3 = **38**(Docker 37 + compose 1) [추정]. `test_full_day`·`test_compose`·`test_store` 는 shim 으로 그대로 돈다.
- 남는 GX 단위(약 760): poller 116·scheduler 150·gaps 25·store_sql 117·validate_greeks 62·metric_report 78·shadow_report 63·probe 42·compose 19·fixtures_clean 25·services_runtime 13·spool 23·기타.
- KBJ 시험은 약 +1,959(승격) + 새 시험(진단·API·웹·sim).

---

## 2. 공개/로그인 배치

| 항목 | 등급 | 저장 | 원천 |
|---|---|---|---|
| 신용스프레드 구간·신용잔고 추세·예탁금 추세·금리 방향·수출 모멘텀 → 매크로 위험 게이지(공공 판정) | **공개** | `pub_diag.indicator`·`gauge` | ECOS 817Y002·722Y001·국고 3년(공개 시리즈), 금투협 15094809, 관세청(P6 `pub_trade.ten_day` — 없으면 성분 '준비 중') |
| 이벤트 표시(만기·금통위·수출 속보 공표일·실적 집중) | **공개** | `pub_diag.event_marker` | `kbj.core.calendar`(만기 — 코드), `config/calendar_events.yaml`, P6 공표 일정, `pub_filings.earnings_schedule` |
| 종합 판정·국장 공포탐욕·반도체 쏠림·경기민감 대 방어·시장폭·수급 진단·한국 대 글로벌 | 로그인 | `prv_diag.*` | KRX·KIS 원장, Yahoo(P5), `prv_gex.investor_flow`, VKOSPI(`prv_diag.vol_index`) |
| GEX 레벨 전부·HIRO-lite·투자자별 선물·옵션 | 로그인 | `prv_gex.*` | KIS 옵션 체인·웹소켓 |

공개 빌드 페이지 3: '공공지표 진단' 패널 + 이벤트 띠만, 나머지 자리는 자물쇠(`lock: 'options'` 사유 그대로). 공개 내보내기는 `pub_diag` 만 읽는다(계약 가·`check_public_safety`).

---

## 3. 데이터 흐름

### 3.1 한눈에

```
[legacy GX — external, KBJ DB 에 search_path]          [KBJ]
 poller(전광판·체인·선물·최종거래일) ──chain.ready──▶ gex.engine ──▶ prv_gex.levels·metrics·strike_gex·option_iv·oi_changes
   └▶ prv_gex.chain_snapshots·fut_board·series_expiries      ▲  ▲                       │
 scheduler(분봉·KRX 파생·마스터) ──▶ minute_bars·krx_*·master ┘  │                       ├─▶ /api/gex/* ─▶ 페이지 3 GEX 패널·띠 칩
                                                              │                       ├─▶ gex.alert(09:00~15:30) ─▶ notifier
 KIS 웹소켓 ◀── gex.ws_gateway(KBJ) ──ticks.fut/opt──────────┘                       └─▶ brief.morning ④·brief.closing
                  ├▶ prv_gex.fut_ticks·opt_ticks
                  └─ws.raw─▶ gex.recorder(KBJ) ─▶ prv_gex.raw_messages
 flows.market_minute(P5, 유일 주인) ─▶ prv_gex.investor_flow ─(읽기)─▶ gex.engine·diag.daily
 krx.daily·board·close_collect·kofia·macro·us·P6 ─(읽기)─▶ diag.public ─▶ pub_diag ─▶ 공개 내보내기 diag/
                                                       └▶ diag.daily(16:00) ─▶ prv_diag ─▶ /api/diag/*·brief.closing
 krx.vkospi(꺼짐) ─▶ prv_diag.vol_index
```

### 3.2 작업 표 (P7 에 켜는 것·만드는 것 — 시각 KST)

| 작업·서비스 | 시각 | 입력 | 출력 | 켜기 |
|---|---|---|---|---|
| `gex.engine`(서비스) | 상시 — `chain.ready`·10초 따라잡기·POST_DAY 일별 | `prv_gex` 수집 표·`investor_flow`·Redis 문맥 | 엔진 표 5 | WRAP |
| `gex.ws_gateway`(서비스) | 세션 중 | KIS 웹소켓(접속키 읽기) | 틱 표 2·`ws.raw` | WRAP |
| `gex.recorder`(서비스) | 상시 | `ws.raw`·`rest.raw` | `raw_messages` | WRAP |
| `diag.public` | 08:40 평일(캐치업 10:00) | 공개 표 | `pub_diag` | WRAP |
| `diag.daily` | 16:00 거래일 | 로그인 원장·GEX 일별·`investor_flow` | `prv_diag` | WRAP |
| `gex.alert` | 정규장 매분 09:00~15:30 | `levels` 최신 | notify `alert.gex_level` | WRAP |
| `krx.vkospi` | 08:10 평일 | KRX | `prv_diag.vol_index` | **꺼짐** `#P7-1` |
| `brief.closing`(기존) | 16:00 | + `prv_diag`·GEX 마감 | 줄 추가 | 이미 켬 |
| `brief.morning`(기존) | 08:10 | + 야간 GEX | ④ 켬 | 이미 켬 |

### 3.3 마이그레이션 `<P6 다음>_diag.sql` (DDL 요약 — FND-B)

- `CREATE SCHEMA IF NOT EXISTS pub_diag; prv_diag;` 권한은 ADR 0002 그대로(`kbj_public_export` 는 `pub_diag` 읽기만, `prv_diag` 는 `REVOKE ALL … FROM PUBLIC`).
- `pub_diag.indicator(trade_date date, key text, value numeric, unit text, state text, pct_rank numeric, inputs jsonb, quality text CHECK(…), reasons jsonb, as_of timestamptz, PRIMARY KEY(trade_date, key))`, `pub_diag.gauge(trade_date PK, label, score, parts jsonb, quality, reasons)`, `pub_diag.event_marker(date, kind CHECK IN ('expiry','mpc','export_flash','earnings_cluster'), title, source, detail jsonb, PRIMARY KEY(date, kind, title))`.
- `prv_diag.indicator`(공개와 같은 모양), `prv_diag.fear_greed(trade_date PK, score numeric, band text, components jsonb, quality, reasons)`, `prv_diag.verdict(trade_date PK, label CHECK IN ('risk_on','neutral','risk_off','hold'), score int, parts jsonb, event_flags jsonb, quality, reasons)`, `prv_diag.vol_index(trade_date, index_id, close numeric, quality, PRIMARY KEY(trade_date, index_id))`.
- 일별 작은 표라 hypertable 아님. 같은 날 재실행은 키를 덮는다(멱등).
- **`prv_gex` 표는 고치지 않는다**(0006 체크섬). `COMMENT ON TABLE` 만 — 표마다 쓰기 주인(§3.4)과 raw 예외. `investor_flow` 의 0017 COMMENT 는 그대로.

### 3.4 쓰기 주인 표 (P7 뒤)

| 표 | 주인 | 비고 |
|---|---|---|
| `prv_gex.investor_flow` | `flows.market_minute`(P5) | P7 은 읽기만. legacy poller 투자자별 삭제 |
| `prv_gex.chain_snapshots`·`fut_board`·`series_expiries` | `gex.poller`(legacy) | — |
| `prv_gex.minute_bars` | GX `MinuteDaily`(legacy — `gex.day_minutes`·`night_minutes` 같은 처리기, 세션으로 행이 갈림) | ⚠ 등록부에 작업 이름 둘 — 처리기 하나라 주인 하나로 본다(기존 그대로) |
| `prv_gex.krx_fut_daily`·`krx_opt_daily` | `gex.krx_derivatives`(legacy) | — |
| `prv_gex.master_snapshots` | `kis.master`(legacy) | — |
| `prv_gex.levels`·`metrics`·`strike_gex`·`option_iv`·`oi_changes` | `gex.engine`(KBJ) | — |
| `prv_gex.fut_ticks`·`opt_ticks` | `gex.ws_gateway`(KBJ) | — |
| `prv_gex.raw_messages` | `gex.recorder`(KBJ) | 예외: 발행 실패 직접 쓰기(ADR-A) |
| `ops.collection_gaps`·`quarantine`·`collection_reports` | legacy GX(gaps·poller·scheduler) [추정 — FND-B 가 grep 으로 확정해 COMMENT] | — |
| `pub_diag.*` / `prv_diag.indicator·fear_greed·verdict` / `prv_diag.vol_index` | `diag.public` / `diag.daily` / `krx.vkospi` | — |

### 3.5 호출량

- **KIS REST**: P7 이 새로 부르는 것 없음. legacy poller 는 투자자별을 빼서 오히려 줄어든다(7조합 × 분당 1 → 0 — `flows.market_minute` 은 이미 계산된 몫). 웹소켓은 앱키당 41구독(`config/ws_budget.yaml` 그대로), 재연결 스냅샷만 REST 리미터 P0.
- **KRX**: `krx.vkospi` 하루 1건(켜면). `limits.yaml krx.daily_cap` 8,000 안.
- **DB 쓰기**: 틱·원문 양은 GX 와 같다(같은 코드) — 압축 정책은 0006 그대로, 30일 원문 Parquet 내보내기는 0006 이 P7 로 미룬 것 → **이번에도 하지 않는다** ⚠ §9 R9.

### 3.6 설정 파일

| 파일 | 내용 |
|---|---|
| `config/gex.yaml`(새) | `features:`(GX 값 그대로), `engine: {catchup_s: 10, grace_s: 35, daily_retry_s: 60}`(GX 상수 — 값 바꾸지 않음), `alert: {conditions: [flip_cross, wall_move, outside_1sigma], scope: all}` |
| `config/diag.yaml`(새) | `window: 250`, `bands: [20, 40, 60, 80]`, `fear_greed: {components: [vol, breadth, credit_balance, foreign, credit_spread], min_components: 4, weights: equal}`, `verdict: {on: 2, off: -2}`, `events: {earnings_cluster_min: 10, caution_days: 1}`, `semis`·`cyclical`·`defensive` 는 **`config/markets.yaml ribbon` 을 읽는다**(두 벌 금지) |
| `config/{kis_ws,kis_ws_fields,ws_budget}.yaml` | GX 에서 `git mv`(호스트 줄 삭제) |

---

## 4. 엔진 명세

### 4.1 GEX (로그인 — metrics §G0~§G8 = GX 명세 그대로)

GX 의 규약·공식·단위·품질 규칙을 바꾸지 않는다: 합성선물 F·잔존기간 T(달력·거래시간 두 기준)·Black-76 IV·그릭스, GEX `±Γ·OI·250,000·F²·0.01`(원/1%, 표시 억원 — naive 딜러: 콜 롱·풋 숏), 콜월·풋월·절대감마·Flip(다중 교차·격자)·Flip 거리·0DTE·ATM IV·기대변동폭 ±1σ·상위 레벨·만기별 감마(§3), VEX·CEX·GEX P/C(§4), 기간구조·25Δ 스큐·IV 랭크·IV−HV(§5), HIRO-lite·투자자별·딜러 점검·대량 체결·PCR·맥스페인·OI 증감(§6), 선물 베이시스(§7), 플래그(§8). 페이지 3 이 쓰는 것과 플래그:

| 페이지 3 항목 | GX § | 지금 플래그 |
|---|---|---|
| Flip·Flip 거리·콜월·풋월·기대변동폭·상위 레벨·순GEX·행사가 GEX | §2·§3 | visible |
| VEX·CEX | §4.1·§4.2 | shadow |
| 25Δ 스큐·IV 기간구조·IV 랭크 | §5 | shadow |
| HIRO-lite | §6.1 | shadow |

### 4.2 진단 (부록 A → metrics §D1~§D9)

공통: 시점 입력만(`published_at ≤ as_of`, KRX 확정 전은 `estimated`), 품질은 입력 중 가장 나쁜 것, 값을 지어내지 않는다(입력 없으면 `None` + 사유). 재계산 같음 시험: 같은 날짜를 다른 날 다시 돌려도(그 사이 새 데이터가 와도) 결과가 같다.

---

## 5. API (로그인 — DB·Redis 만 읽는다, 계약 ⑩)

| 라우트 | 내용 |
|---|---|
| `GET /api/gex/levels?scope=all\|nearest\|0dte` | 최신 사이클 레벨(값·품질·플래그·as_of·세션) + 현재가 |
| `GET /api/gex/profile?scope=` | 행사가별 GEX(막대) + Flip·월 |
| `GET /api/gex/metrics?name=&from=&to=` | 지표 시계열(visible 만 값, shadow 는 상태만) |
| `GET /api/gex/flow` | HIRO-lite·투자자별(대금 **원**으로 — investor_flow 백만원 × 10⁶) |
| `GET /api/gex/futures-oi?days=20..120` | 코스피200 선물 미결제약정(전 월물 합)·증감(만기 종목 제외)·근월물·OI 해석 라벨·선물/옵션 투자자 일별 — 확정 KRX·잠정 전광판(2026-10-27 추가, metrics §33·ADR 0043) |
| `GET /api/diag/summary?date=` | 종합 판정·공포탐욕·지표 카드·이벤트 |
| `GET /api/diag/history?key=&days=` | 지표·공포탐욕 시계열과 구간 |
| `GET /api/diag/events?from=&to=` | 이벤트 표시 |

봉투·`NoData`(작업 이름·예정 시각)·데이터 버전 키는 P3~P5 규칙 그대로.

## 6. 프런트 — 페이지 3

### 6.1 배치(미리보기 따름 — 위에서 아래, 휴대폰은 한 줄)

1. **판정 띠**: 종합 판정(세 단계 + 보류) · 공포탐욕 `gauge`(0~100, 다섯 구간) · 이벤트 꼬리표(만기 D-n·금통위 D-n·'이벤트 주의').
2. **공공지표 진단**(공개): `.lvl` 행 다섯 + 매크로 위험 게이지 — 공개 빌드에도 나온다.
3. **시장 진단**(로그인): 반도체 쏠림·경기민감 대 방어·시장폭(상승/하락·20일선 위·신고가 수)·한국 대 글로벌·수급 진단(외국인 현물·K200 선물 20일 누적) — `kv` 카드 + 250일 `line` + 구간 표시.
4. **GEX 레벨**(로그인, ENG): `profile`(행사가 막대·현재가·Flip 선) + `ladder`(현재가·Flip·콜월·풋월·±1σ) + `.lvl` 행(콜월·풋월·기대변동폭) + 범위 `seg`(all·nearest·0dte) + 확장 지표 줄(VEX·CEX·스큐·HIRO-lite — shadow 면 '섀도 검증 중').
5. **이벤트 달력**: 30일 `timeline`(만기·금통위·수출 속보·실적 집중).

### 6.2 상태·오류

P3 §6.7 규칙(품질 꼬리표·원천·as_of). 엔진이 `stale` 이면 패널 머리에 '지연', 세션 밖이면 '마지막 세션 — <시각>'. 야간 투자자별은 '수집 안 함(야간)'(D-P7-6).

## 7. 공개 정적 사이트 내보내기 영향

`diag/summary.json`(게이지·공개 지표 카드)·`diag/history.json`(공개 지표 250일)·`diag/events.json`. `manifest` 크기 상한에 `diag/`. `check_public_safety` 가 `diag/` 에 로그인 키 이름(`fear_greed`·`verdict`·`gex`)이 없는지 본다.

---

## 8. 시험·검증

| 범위 | 시험 |
|---|---|
| 골든 | 코어 골든(허용오차 `TOLERANCES` 그대로)·체인 부분집합·raw 골든 — **기대값 파일 수정 금지**. 바꿔야 녹색이 되면 이식 버그로 본다 |
| 명세·속성 | GX 단위·속성 그대로(가설 `hypothesis` 설정 그대로), 부호 시험 |
| 엔진 | 승격 시험 + KBJ 저장소 메모리로 사이클 1회(합성 체인 → 레벨이 골든과 같은 값) |
| 웹소켓·녹화 | 가짜 서버(`tests/fakes/ws_server.py`)로 구독·재연결·순번 공백·닫힌 세션 확인, 녹화 멱등 |
| 진단 | 지표마다 손계산 1·경계(구간 끝값·성분 3개·결측·공표 지연)·재계산 같음, 판정 표 전 조합(속성) |
| 단일 주인 | 등록부 시험 + legacy poller 소스 검사(ING) |
| 하루 운영 | `tests/sim/test_one_day_p7.py`(WRAP — §1.7-2) |
| 실데이터 | `#P7-1` VKOSPI 칸·코드, `#P7-2` 야간 투자자별 응답, `#P7-3` GX DB 이관 행 수, `#P7-4` KBJ ws-gateway 하루 무결측(`nogap_report`), `#P7-5` 섀도 1주(`shadow_report`), `#P7-6` 위클리 4조합 KBJ 수집 하루 대조 |

---

## 9. 위험·미정

| # | 위험 | 대응 |
|---|---|---|
| R1 | legacy 기준 수 계산이 어긋남(매개변수 펼침·shim 으로 다시 모이는 시험) | 묶음마다 `test_legacy.sh gexlab` 만 돌려 '옮긴 수만큼만' 줄었는지 확인, WRAP 이 `--collect-only` 파일별 수로 MIGRATION 표 작성 |
| R2 | ⚠ VM 에서 legacy ws-gateway 와 KBJ ws-gateway 가 동시에 뜸 — 같은 앱키 웹소켓 세션·41구독 예산 다툼 | 전환은 한 번에(§10 Q7), 런북에 'GX compose 의 engine·ws-gateway·recorder 를 끈 뒤 KBJ 셋 기동'. 둘 다 같은 Redis 하트비트 이름 → health 로 겹침 감지 [제안] |
| R3 | legacy poller 투자자별 삭제가 배포되기 전까지 같은 TR 두 곳(ADR 0019 R6) | ING 변경과 KBJ 배포를 같은 전환에 |
| R4 | GX core 가 KBJ pyright strict·ruff 규칙에서 경고 — 타입 보강 중 동작이 바뀔 위험 | 동작 변경 금지·골든 기대값 고정이 안전망. FND-C 에 1.5일 |
| R5 | `vollib` 런타임 의존(이미지 크기·설치) | FND-B 가 Docker 빌드 확인(메인) |
| R6 | raw 직접 쓰기 예외가 '주인 하나' 규칙을 흐림 | ADR-A 에 범위 고정(발행 실패 때만·digest 멱등), 등록부 `writes` 에는 recorder 만 |
| R7 | 야간 투자자별 끊김(D-P7-6) — GX 엔진 §6.2 야간 지표 공백 | 화면 '수집 안 함(야간)'. 실행기 야간 분 트리거 + `#P7-2` 뒤 별도 작업 |
| R8 | P6 미완·지연 → 수출 속보·수출 모멘텀 없음 | 성분 '준비 중', 게이지는 남은 성분으로(최소 3) |
| R9 | 0006 이 P7 로 미룬 원문 30일 Parquet 내보내기·보존 정책 미정 → 디스크 증가 | P7 범위 밖으로 명시, ADR-A '다시 볼 때'에 적고 P9 운영 [제안] |
| R10 | legacy GX 를 KBJ DB 에 붙일 때 GX `migrate`(001~004)가 돌면 0006 과 충돌 | ING 가 compose 에서 KBJ DB 대상 migrate 를 막고, 통합 시험용 자기 DB 는 그대로 |
| R11 | KBJ 서비스 시그니처가 GX 와 달라지면 shim 으로 도는 GX `test_full_day`(Docker) 가 깨짐 | D-P7-8(같은 메서드 이름 Protocol), 메인 Docker 통합으로 확인 |
| R12 | v0.2 의 '금통위 시장 내재 경로'·'공매도 잔고'는 정의·원천 미확정 | P7 에 넣지 않음(§0.2). 금리 방향은 국고 3년 20일 변화로 대신(부록 A §D5) |
| R13 | IDX·P6 가 같은 공유 파일을 바꿈 — 번호·줄 위치 어긋남 | D-P7-1(FND 첫 일 재확인) |

### 9.1 앞 단계에서 기대는 것 — 코드 확인(2026-10-13)

`prv_gex` 21표·뷰(0006), `investor_flow` COMMENT(0017), `flows.market_minute`(enabled — K2I 3조합), `kbj.core.calendar`(만기·세션 — P2), `kbj.data.private.kis.{token,master,rest}`·`kbj.data.ratelimit`·`kbj.store.spool`(P2), `kbj.engines.market.{ribbon,breadth}`(P3), `kbj.engines.macro.derive`·`pub_market_stats`(P5), `tests/fakes/{ws_server,kis_server,krx_server}.py`(P2~P5), notify outbox(ADR 0005), 봇 명령 등록부(`commands.py`).

---

## 10. 사용자만 할 수 있는 것 (각각 기본값으로 진행)

| # | 질문 | 기본값 |
|---|---|---|
| Q1 | GX 수집 경로(poller·분봉·KRX 파생·마스터·무결측)를 P7 에 KBJ 로 옮길까? | **아니오** — P7 은 legacy 외부 서비스로 두고 KBJ DB 에 붙이기만, 이식은 P9 `legacy/` 비우기(PLAN D8 예외를 ADR-A 에 기록) |
| Q2 | GX 운영 DB 의 녹화·엔진 이력을 KBJ DB 로 옮길까? | **옮긴다** — WRAP 이 스크립트·런북만, 실행은 사용자(VM). 옮기기 전엔 IV 랭크 등 이력 지표가 '이력 부족' |
| Q3 | 섀도 지표(VEX·CEX·스큐·HIRO-lite·IV 랭크 등)를 페이지 3 에 보일까? | **GX 규칙대로 숨긴다** — '섀도 검증 중' 자리만, `shadow_report` 1주 확인 뒤 사용자가 `config/gex.yaml` 에서 visible |
| Q4 | 페이지 3 공개판을 만들까(DATA_TIERS 대로)? | **만든다** — 공공지표 진단(매크로 위험 게이지)·이벤트만 공개, 나머지 자물쇠 |
| Q5 | GEX 레벨 알림을 장 마감 뒤·야간에도 보낼까? | **보내지 않는다** — 정규장 09:00~15:30 즉시(쿨다운 10분), 그 밖은 16:00 마감 요약·다음 날 아침 브리핑 |
| Q6 | 국장 공포탐욕의 성분·가중을 v0.2 다섯·같은 가중으로 할까? | **그렇다** — VKOSPI(없으면 ATM IV)·시장폭·신용잔고 변화·외국인 수급·신용스프레드, 250거래일 백분위 평균 |
| Q7 | VM 전환(GX compose 의 engine·ws-gateway·recorder 정지 → KBJ 셋 기동·poller 새 버전)을 언제 할까? | **WRAP 뒤 첫 주말**(토요일 야간 세션 끝난 뒤) 한 번에, 병행 운영 없음 |

---

## 부록 A. `docs/metrics.md` 에 넣을 진단 지표 정의 (FND-B 가 §D1~§D9 로 옮긴다 — [제안] 값은 `config/diag.yaml`)

공통 구간(§D0): 연속 지표 x 의 250거래일 백분위 p(그날 포함, 동점은 평균 순위). 구간 `p < 20` 극단 낮음 · `20–40` 낮음 · `40–60` 보통 · `60–80` 높음 · `≥ 80` 극단 높음. 이력 < 60거래일이면 구간 없음(`estimated` + '이력 부족'). 상태 점수 s ∈ {−1, 0, +1}: 지표마다 '위험 선호 방향'을 정하고 높음·극단 = 그 방향 ±1, 보통 = 0.

| § | 지표 | 공식·입력 | 등급 | 상태 점수 방향 | 예외·품질 | 시험 |
|---|---|---|---|---|---|---|
| D1 | 신용스프레드 구간 | metrics §16 신용스프레드(AA- − 국고 3년, %p). 구간 = (x − 250일 평균) ÷ 250일 표준편차: `≤ −1σ` 좁음 · `±1σ` 보통 · `≥ +1σ` 넓음 | 공개 | 좁음 +1 · 넓음 −1 | 공표 지연 → 전일 값 `stale` | 손계산·σ 경계 |
| D2 | 신용잔고·예탁금 추세 | 금투협 신용공여 잔고(코스피+코스닥)·투자자예탁금의 20거래일 변화율(%) → 백분위 | 공개 | 증가 +1(신용은 극단 높음이면 과열 플래그만) | D+1 공표 — 늦으면 전 영업일 | 경계 |
| D3 | 수출 모멘텀 | P6 `pub_trade.ten_day` 최신 잠정 구간(10일·20일·월 누계) 전년동기비(%) → 백분위(P6 는 속보 YoY 까지만 만들고 '수출 모멘텀'은 P7 몫 — `docs/p6_design.md` §0.2) | 공개 | 높음 +1 | P6 표 없음 → 성분 '준비 중' | P6 뒤 |
| D4 | 매크로 위험 게이지(공공 판정) | D1·D2(신용·예탁금 각각)·D3·D5 점수 합 S: `S ≥ 2` 위험 선호 · `S ≤ −2` 위험 회피 · 그 사이 중립. 성분 3개 미만 → 보류 | 공개 | — | — | 판정 표 전 조합 |
| D5 | 금리 방향 | 국고 3년(ECOS) 20거래일 변화(bp) — 하락 = 완화 | 공개 | 하락 +1 | — | 손계산 |
| D6 | 시장 지표 | 반도체 쏠림(§6.4, 20거래일)·경기민감 대 방어(§6.4)·시장폭(§6.2 — 20일선 위 비율·신고가 비율)·한국 대 글로벌(§6.5, 20거래일)·수급 진단(외국인 현물 코스피+코스닥 20일 누적 원 + K200 선물 외국인 순매수 20일 누적 — `investor_flow` 마감 행, 백만원→원) → 각 백분위 구간 | 로그인 | 시장폭·한국 대 글로벌·외국인 +1 방향, 반도체 쏠림 극단 높음은 **취약 플래그**(점수 0) | 입력 품질 따름 | 정본 함수 재사용 확인(같은 값) |
| D7 | 국장 공포탐욕 | 성분 5: ① 변동성 = VKOSPI(없으면 월물 ATM IV — `estimated`) 백분위를 **뒤집음**(100 − p) ② 시장폭 = 20일선 위 비율 p ③ 신용잔고 변화 = D2 신용 p ④ 외국인 수급 = D6 수급 p ⑤ 신용스프레드 = 100 − p. 점수 = 있는 성분 평균(0~100, 소수 첫째). 성분 < 4 → 값 없음(`invalid` + 빠진 성분), 4 → `estimated`. 구간 `0–24` 극단적 공포 · `25–44` 공포 · `45–55` 중립 · `56–75` 탐욕 · `76–100` 극단적 탐욕 | 로그인 | 탐욕 +1·공포 −1(극단은 같은 ±1) | — | 손계산·성분 빠짐·대체 표시 |
| D8 | 종합 판정 | 점수 S = D7 + D1 + D6(시장폭·한국 대 글로벌·외국인) + GEX 체제(현재가 > Flip(all) +1, < −1, Flip 없음 0 — 마지막 주간 사이클, visible 일 때만). `S ≥ 2` 위험 선호 · `S ≤ −2` 위험 회피 · 그 사이 중립 · 성분 4개 미만 보류. 근거 = 기여 큰 셋(부호·이름). 이벤트 주의 꼬리표 = D9 이벤트가 오늘·다음 거래일 | 로그인 | — | 입력 중 가장 나쁜 품질 | 판정 표 속성 시험·재계산 같음 |
| D9 | 이벤트 표시 | 만기: `kbj.core.calendar` 월물(둘째 목요일)·위클리(WKM 월·WKI 목) 최종거래일. 금통위: `config/calendar_events.yaml`. 수출 속보: 관세청 10일 잠정 공표일(1·11·21일 — `docs/p6_design.md` D-P6-6, 휴일이면 P6 규칙), 실제 반영일은 `pub_trade.ten_day` 수집 시각(P6 없으면 생략). 실적 집중: 그날 `pub_filings.earnings_schedule` 의 시총 상위 200 [제안] 종목 수 ≥ `earnings_cluster_min`(10) | 공개 | — | 휴장일·대체공휴일은 캘린더 정본 | 만기일 경계(휴장 앞당김) |

GEX §G0~§G8 은 `GX:docs/metrics.md` 를 그대로 옮기고 '구현·시험' 칸을 KBJ 경로(`kbj/core/gex/*`·`tests/unit/core/gex/*`)로만 바꾼다. metrics §6.5 'GEX Flip' 줄은 '§G3.5 Flip 거리(%) = (현재가 − Flip) ÷ 현재가 × 100, visible 일 때만'으로 바꾼다(OUT 요청 → FND-B 반영).
