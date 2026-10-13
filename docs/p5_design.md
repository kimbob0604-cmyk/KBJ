# KBJ P5 설계서 — 매크로·테마·ETF·브리핑 + 장중 수급·프로그램매매(F2)

- 작성: 2026-10-13. 상태: **구현 완료(웨이브 3 WRAP, 2026-10-13)** — 사용자 결정(2026-10-13): Q1 테마·밸류체인 사전 통합(페이지 9·THM 묶음)은 사용자가 따로 진행해 **P5 에서 뺐다**(페이지 9 는 P3 자리표시, 테마 전용 기반 코드·`0015_themes` 마이그레이션 삭제 — 뒤 번호를 당겨 `0014`~`0017` 이 P5 표), Q4 구루 X 다이제스트는 **P5·P8 모두에서 뺐다**(원본 ET xdigest 는 건드리지 않음 — 등록부 `retired`). 대상 단계: `docs/PLAN.md` §8 P5 — "monitor/bok(페이지 6), 분류 사전 통합(테마·체인, 페이지 9), ETF(페이지 10), 아침 브리핑 하나로 통합" + 묶음 F2(`docs/p5_flow_program_prompt.md`) + 앞 단계가 P5 로 미룬 것(`brief.closing`·`flows.report` — P3 R18·D-P3-17, `us.*`·`market.fsc_daily` — D-P3-17, 경제 캘린더 — D-P4-15, legacy 기준선 naver·krx_scrape 의 P5 몫).
- 기준 문서: 계획 `docs/PLAN.md` §2·§4·§8, 등급 `docs/DATA_TIERS.md`(정본 — 페이지 6 공개 = ECOS 한은 표·KOSIS·FRED 정부 시리즈, 페이지 9 공개 = 우리가 만든 분류 사전·로그인 = 테마 강도, 페이지 10 = 로그인 전부), 지표 `docs/metrics.md`(P5 지표는 부록 A 를 기반 묶음이 §16~§23 으로 옮겨 정본으로 만든다), P3·P4 설계 `docs/p3_design.md`·`docs/p4_design.md`(구조·관례), ADR 0001(U2 하루 1회·U3 제3자 코드·U4 네이버 금지·Q5 테마 사전·Q8 ETF 분류·Q9 브리핑 매크로 출처·Q10 미국 신고가), ADR 0005(notifier outbox), 0006(등록부·선점), 0007(legacy 기준선), 0010(ETF), 0017(신고가 3축), 0018(마감 뒤 16:00), 작업 등록부 `config/jobs.yaml`, 묶음 F2 지시 `docs/p5_flow_program_prompt.md`(**그대로 따른다** — 이 문서는 넣는 자리·공유 파일·보정만 적는다).
- 원본 위치: 이 레포 `legacy/` 를 직접 열어 확인했다(HEAD `3cb2d5a`). 표기 `ET:` = `legacy/etf_traker/`, `SD:` = `legacy/stock_dashboard/`, `GX:` = `legacy/gexlab/`. kbj 쪽은 레포 경로 그대로.
- 표기: **[추정]** = 코드로 끝까지 확인하지 못함, **[확인 필요]** = 기본값으로 정한 것, **[실측 필요]** = 키를 받은 뒤 실제 응답으로 확인, **[결정 필요]** = 사용자가 정할 것, **[제안]** = 이 설계의 권고, ⚠ = 충돌·위험.
- 키·토큰·계좌 값은 이 문서 어디에도 없다. `.env`·토큰 캐시는 열지 않았다. 네트워크 호출은 하지 않았다. 레포 코드에 없는 응답 칸·코드·단위는 전부 [확인 필요]/[실측 필요]이고, 그 수집 작업은 `enabled: false` 로 둔다(키 수령 뒤 `docs/probe_results.md` §7 #39~ 를 채우고 켬). 모든 증명은 합성 데이터·가짜 서버로 한다(KIS·KRX·금융위 시세·Yahoo·Nasdaq·운용사 원자료와 그것으로 만든 픽스처는 레포에 넣지 않는다 — 고정 시드 생성기만).
- **코드로 확인한 사실 중 계획과 다른 것(이 설계의 출발점)**:
  1. `monitor/bok` 은 **레포에 없다**. 제3자 `rsm0kk/bok-monitor` 이식이라 P1 에서 옮기지 않았다(ADR 0001 U3, `legacy/etf_traker/MIGRATION.md`:28 "P5 에서 직접 다시 만든다"). 그래서 페이지 6 은 **옮길 코드가 없고** `docs/inventory.md`:21·117·400~402 의 기능 목록을 기준으로 kbj 어댑터(ECOS·KOSIS 는 P2 정본)로 새로 만든다.
  2. `monitor/kr` 의 테마 198·체인 23 사전도 **레포에 없다**. `legacy/etf_traker/monitor/kr/knowledge/themes.json` 은 같은 모양의 **합성** 사전이다(`monitor/kr/README.md` 머리말, U3). ADR 0001 Q5 가 이미 "board `themes.yaml` + SD `valuechain_map`(자체), monitor/kr 198·23 은 제3자"로 정했다.
  3. SD `themes_mapping.json`(네이버 테마 크롤 26 테마 — `theme_crawler.py`)도 **레포에 없다**(제외 등급 — `legacy/stock_dashboard/MIGRATION.md`:46, U4).
  4. 레포에 실제로 있는 자체 사전: `config/knowledge/themes.yaml`(테마 **62**, 축·단계별 seeds — 종목명), `sectors.yaml`(board48)·`sector_map.yaml`(**3,924** 종목 1:1 — claude 3,921·human-review 3), SD `data/valuechain_map.json`(v2.0, 테마 **5** × 레이어 × 세그먼트 **52**, 국내 코드 **93** 줄(고유 75 — 세그먼트 사이 중복 15종목), 미국 티커 62).
  5. ETF 분류 정본은 이미 kbj 에 있다(`kbj/engines/etf/types.py` — ET `etf_tracker_v9/themes.py` 승격, P3). 두 번째 사본 `ET:monitor/kr/etf.py:classify`:109 가 남아 있다.
  6. `gex.poller`(P7, external) 가 `KIS:market_investor_intraday` 를 등록부에서 collects 로 갖고 있다 — F2 의 새 작업과 **정적 겹침**이 된다(§0.4 에서 옮긴다).
  7. `prv_gex.investor_flow`(0006)는 **대금 백만원**(GX 단위 — `0006_gex.sql` 주석·뷰 `prv_flows.market_investor_intraday` 주석)이다. metrics §0 의 "원 단위 저장" 과 다르다(D-P5-21).
  8. 등록부의 P5 작업 owner 중 `kbj.engines.themes:monitor` 는 계약 ⑨(엔진은 순수 — 저장소 금지)와 맞지 않고, `kbj.services.collectors.macro:{morning,morning_restricted}` 는 공개·로그인 수집이 한 모듈이라 공개 작업 경계 계약(⑬ 방식)을 걸 수 없다(D-P5-7).

---

## 0. 요약

### 0.1 결정 요약

| # | 결정 | 이유·근거 |
|---|---|---|
| D-P5-1 | **P4 가 끝난 뒤 시작한다 — 충족**(P4 커밋·CI 12/12 녹색, HEAD `3cb2d5a`). P5 가 고치는 P3·P4 파일: `config/{jobs,notify,limits,markets}.yaml`, `kbj/data/catalog.py`, `kbj/data/private/kis/datasets.py`(F2 데이터셋 행), `kbj/store/repos/{__init__,memory}.py`, `kbj/services/api/{app,cache}.py`·`readers/_common.py`·`routes/{flows,etf,board,market}.py`, `kbj/services/public_export/{export,manifest}.py`, `kbj/engines/board/config.py`(사전 로더 한 곳 — D-P5-11), `web/src/app/{pages,ribbon}.ts`, `web/src/data/static.ts`, `web/src/pages/{p1_market,p2_board,p5_filings,p8_stock,p10_etf}.ts`, `tests/sim/harness.py`, `tests/fakes/kis_server.py`, `tests/unit/api/make_web_fixtures.py`, `tests/unit/scheduler/test_registry_validation.py`, `pyproject.toml`·`uv.lock`, `Dockerfile`, `.github/workflows/ci.yml` — 누가 언제 고치는지는 §1.10 | 묶음끼리 같은 파일을 같은 웨이브에 고치지 않는다 |
| D-P5-2 | **구현 = 공통 기반 1(FND, 웨이브 1) + 병렬 묶음 6(MAC·THM·ETF·BRF·US·F2, 웨이브 2) + 마무리 1(WRAP, 웨이브 3)**. 여러 묶음이 쓰는 마이그레이션·행 자료형·저장소·카탈로그·등록부 행·설정·합성 세계·공유 가짜 서버(Yahoo)·API 모델과 라우트(빈 리더)·openapi·TS 타입·공개 내보내기 고리·웹 셸·공용 차트 부품·지표 문서는 **FND 가 먼저** 만든다 | P4 의 M·W 웨이브 1 관례. 웨이브 2 묶음은 서로의 코드가 아니라 FND 가 고정한 시그니처(저장소 Protocol·API 모델·행 자료형)로만 맞춘다 |
| D-P5-3 | 마이그레이션 번호: **0014_macro · 0015_themes · 0016_us_board · 0017_market_stats · 0018_program**(§3.3). 적용된 0001~0013 은 고치지 않는다(체크섬). F2 의 현물·파생 1분 수급은 새 표를 만들지 않고 `prv_gex.investor_flow` 에 쓴다(F2 §2) — 0018 은 그 표에 손대지 않고 `COMMENT ON` 으로 쓰기 주인만 적는다 | `kbj/store/migrate.py` 는 번호 순서로 미적용분을 고른다. 공개(0014·0015·0017 의 pub_)·로그인 표를 파일 안 절로 나누고 권한 시험(`kbj_public_export` 가 pub_ 만 읽음)을 넓힌다 |
| D-P5-4 | **API 모델·라우트는 FND 가 고정**(웨이브 1 — 리더는 `NoData` 를 내는 빈 함수), 리더·모델 본문은 그 도메인 묶음. `web/src/api/openapi.json`·`types.gen.ts` 는 **생성 전용 공유 파일**: 손으로 고치지 않고, 모델을 바꾼 묶음은 생성 명령만 다시 돌린다(작업 트리에서 결정적). WRAP 이 `--check` 로 닫는다 | P4 는 API 묶음 하나(A)가 맡았지만 P5 는 도메인이 6개라 리더를 나눈다. 모델을 웨이브 1 에 고정하면 웨이브 2 의 웹 타입이 흔들리지 않는다 |
| D-P5-5 | **페이지 6 은 bok 을 옮기지 않고 다시 만든다**(U3 — 레포에 코드가 없다). 기능 기준은 `docs/inventory.md` 의 bok 8탭·아침 매크로 브리핑과 SD 글로벌매크로(`SD:server.py:api_macro`:8004·`api_global_macro`:13126·`_MACRO_TICKERS`:7988). **옮기는 것·버리는 것 표 = §4.1**. 통화신용정책보고서 110시트 원본 xlsx 파싱은 P5 에서 하지 않는다 — 보고서가 인용하는 ECOS 시리즈 + 원문 링크로 대신한다(**[결정 필요]** 기본값 — §10 Q2) | 원본 xlsx·그림(39.8 MB)은 제3자 저장소에 있었고 출처·구조가 확인되지 않았다(`docs/conflict_map.md`:269). ECOS 는 P2 정본 어댑터가 있다 |
| D-P5-6 | **매크로 등급은 데이터셋 단위**: 한 데이터셋에 공개·로그인 시리즈를 섞지 않는다. FRED 는 `FRED:gov_series`(정부 시리즈 — 공개, `pub_macro.series`)와 **`FRED:restricted_series`(새 — S&P·ICE·CBOE 등 저작권 시리즈, 로그인, `prv_macro.series`)** 로 나누고, `config/macro.yaml`(새)의 시리즈마다 `tier` 를 적어 시험이 데이터셋 tier 와 대조한다 | DATA_TIERS §1 "FRED 공개(미국 정부 시리즈만)". 공개 내보내기는 `pub_*` 만 읽으므로(계약 ⑧) 표가 등급을 지킨다 |
| D-P5-7 | **owner 정정(FND 가 등록부에서)**: 매크로 수집을 공개 `kbj.services.collectors.macro:{morning,monthly,evening}` 와 로그인 `kbj.services.collectors.macro_prv:{morning,monthly,evening}` 두 모듈로 나눈다. 테마는 순수 계산 `kbj.engines.themes`(사전·충돌·강도) + 실행 `kbj.services.engine.themes:{dictionary,monitor}` 로 나눈다(P3 board·P4 valuation 과 같은 틀) | 계약 ⑨(엔진에 저장소 금지)과, 공개 작업 경계 계약 ⑭(WRAP)를 걸려면 모듈이 갈라져 있어야 한다 |
| D-P5-8 | **경제 캘린더(페이지 5 일정 패널 + 아침 브리핑 ⑤)**: 공개 = 수기 공식 일정(`config/calendar_events.yaml` 의 금통위에 FOMC·한국 주요 공표일을 더함 — [확인 필요: 원 일정 대조]) + FRED `releases/dates`(미국 정부 지표 발표일 — 공개 [실측 필요]) + 발표 결과는 수집된 시리즈 값. 로그인 = ForexFactory 비공식 피드의 예상치(`FF:calendar`) — **꺼 둔다(사용자 결정 전 — §10 Q3)**. SD Finnhub 일정(`SD:server.py:api_calendar_economic`:8422)은 버린다 | 비공식 피드의 약관 미확인. 지어낸 예상치 금지(절대 규칙 2) |
| D-P5-9 | **분류 사전 = 레포에 있는 자체 사전만 합친다**: `config/knowledge/themes.yaml`(62 테마·단계 seeds) + **`config/knowledge/valuechains.yaml`(새 — SD `valuechain_map.json` 5 테마 × 52 세그먼트를 변환, 원본은 `git mv` 로 옮긴 뒤 변환본만 남김)** + `sectors.yaml`·`sector_map.yaml`(board48 1:1 — 테마와 다른 축) + **`aliases.yaml`(출처 테마 → 정본 테마 id)·`overrides.yaml`(사람이 고정한 포함·제외·대표·단계)·`theme_codes.yaml`(seeds 종목명 → 코드 확정본, DART `corp_code` 로 만든 공개 자료)**. 빌더 `kbj.engines.themes.dictionary.build` 하나가 `pub_themes.*` 를 만든다. monitor/kr 198·23(제3자)·SD themes_mapping(네이버)은 넣지 않는다(§10 Q1 — 원본을 로그인 참고층으로 넣을지) | ADR 0001 Q5·U3·U4, `docs/conflict_map.md`:141("공개에는 자체 사전만"). DART 는 공개 등급이라 이름→코드 확정본을 커밋할 수 있다 — 그래서 **완료 시험을 실제 사전으로 CI 에서** 돌릴 수 있다 |
| D-P5-10 | **테마 충돌 정의·해소 규칙 = metrics §17(부록 A)**. 충돌 7종(K1 별칭 누락·중복, K2 이름→코드 모호, K3 단일 단계 축의 단계 불일치, K4 대표 테마 미결, K5 고정 포함·제외 모순, K6 섹터 1:1 위반, K7 표시 이름 중복). 우선순위 `overrides(locked) > themes.yaml > valuechains.yaml > 파생` 으로 해소되는 것은 충돌이 아니다(낮은 쪽은 근거로만 남는다). **완료 기준 "한 종목의 테마 충돌 0" = 커밋된 실사전 빌드의 미해소 충돌 0 + 소비자 일치**(board 엔진·페이지 9·브리핑·종목 상세가 같은 대표 테마) | PLAN §8 P5 완료 기준. 사전은 다대다(`themes.yaml` meta)라 '여러 테마 소속' 자체는 충돌이 아니다 |
| D-P5-11 | **board 엔진도 사전 로더 하나로 읽는다**: `kbj/engines/board/config.py:load_themes` 는 `kbj.engines.themes.dictionary.load_sources`(같은 `themes.yaml`)를 다시 내보내는 얇은 함수가 된다. board 의 테마 매핑 결과는 **바뀌지 않아야** 한다 — 신고가 골든(`tests/golden/board`)과 `test_seeds` 가 그대로 녹색 | 정본 하나(CLAUDE §3). 골든이 '사전 통합이 board 산출을 바꾸지 않았다'를 증명한다 |
| D-P5-12 | **테마 강도(로그인) = `ET:monitor/kr/engine.py` 계산부 승격**(자작 코드 — 역산한 식 RS `0.2·1M+0.2·3M+0.2·6M+0.4·1Y` 백분위, 섹터 rs = 구성 중앙값, rsTop, 급증 = 오늘 거래대금 ÷ 20일 중앙값 — `monitor/kr/README.md`). 입력은 KRX 일봉 원장(`prv_market.daily_bar`)·사전 매핑. 템플릿·사전·골든(제3자 유래·합성)과 `build.py`·`industries.py`(Q6 — 업종 축은 KRX·board48)·`financials.py`(pub_fin 이 대체)·`flows.py`(prv_flows 가 대체)는 **폐지** — 그 시험은 대체 정본 시험을 MIGRATION 에 적고 지운다(§1.9) | 계산식은 시험 28개(`test_engine.py`)가 지킨다. 화면은 SPA 페이지 9 |
| D-P5-13 | **ETF 남은 것(페이지 10)**: ① ETF 맵(유형·테마 × 순자산 크기 × 기간 수익률 색 — 분류는 정본 `kbj.engines.etf.types` 하나, `ET:monitor/kr/etf.py` 의 분류는 폐지·묶음 계산은 승격) ② 보유 탐색(ETF → 구성종목, 종목 → 담은 ETF·비중) ③ 액티브 동시 편입(여러 액티브 ETF 가 같이 담은·늘린 종목 — `ET:etf_tracker_v9/report.py:_consensus`:124) ④ **ETF 리포트 텔레그램 `etf.report`(새 작업, 평일 08:20, `notify etf.report` 하루 1회)** — `ET:etf_tracker_v9/{tracker.build_report:296,report.build:222}` 승격, 원천은 `prv_etf.*`(P3)·`prv_market`. SD `api_etf_map`(`SD:server.py`:5151 — 네이버 스크랩·고정 목록)은 버린다 | ETF 수급·구성종목 변동은 P3 에 이미 있다(D-P3-12). 아침 발송이지만 브리핑과 다른 종류(`docs/inventory.md`:184 "ETF는 브리핑이 아니라 별도 1회") — U2 의 '아침 브리핑 1회'에 세지 않는다 |
| D-P5-14 | **아침 브리핑 하나 = `brief.morning`**(평일·간밤 미국 거래일 08:10, 캐치업 09:00, `dedup: daily`). 섹션 ①간밤 미국 ②금리·달러·원자재 한 표 ③한국 매크로(전일 이후 새 공표) ④야간선물·GEX(**P7 전에는 싣지 않는다**) ⑤오늘·이번 주 일정과 발표 결과 (⑥구루 — 2026-10-13 Q4 로 삭제) ⑦데이터 품질 줄 + 오늘 국내 실적 일정(P4 `pub_filings.earnings_schedule`)·금통위 D-n. 재료가 무른 의존 시한까지 없으면 그 섹션은 **'미준비(작업·예정 시각)'** 로 적는다. **LLM 을 쓰지 않는다**(템플릿 — 절대 규칙 3) | `docs/inventory.md`:186 의 통합안 그대로, 단계 밖 재료는 지어내지 않고 빼고 이유를 적는다 |
| D-P5-15 | **마감 요약 `brief.closing`·수급 리포트 `flows.report` = 16:00(ADR 0018)**. 마감 요약 섹션 ①지수·환율·시장 수급(16:00 은 KIS 마감 수집 값 — **기준일·원천·quality 를 적는다**, ADR 0018 §2-5) ②업종 상위·하위·특징주 ③신고가(board 3축 랭킹) ④수급 동향 ⑤주요 공시(P4 중요도) — GEX(P7)·스윙 시그널·코멘트 초안·발굴·추천(P8)·K200 선물 줄(P7)은 싣지 않는다. **F2 프로그램 줄은 넣지 않는다**(F2 §3.7) | U2 하루 1회, ADR 0018 |
| D-P5-16 | **`flows.report` = `ET:monitor/flow` 승격**(선정·본문·차트 4장 — `run.py`·`analyze.py`·`narrative.py`·`returns.py`·`chart.py`, 시험 129). 원천을 KRX 스크랩(`monitor/flow/krx.py` — 기준선 krx_scrape 5)·board ingest(`kissrc.py`) 대신 kbj 원장(`prv_flows.stock_investor_daily`·`prv_market.daily_bar`·`prv_board.artifact`)으로. 신고가 대상은 board **3축**(ADR 0017) — 원본의 'd60' 시험은 'd120' 으로 옮긴다(P3 E1 이 board 시험을 옮긴 방식 그대로, 시험 수 유지). 차트용 **`matplotlib` 을 런타임 의존성으로**(지금 legacy 그룹), 앱 이미지·CI kbj 잡에 `fonts-nanum`(legacy 잡에는 이미 있다 — `.github/workflows/ci.yml`:156) | 원본 시험을 지우거나 약하게 하지 않는다. 차트 시험("한글 폰트를 실제로 확인")은 폰트가 있어야 돈다 |
| D-P5-17 | **'브리핑 발송은 하루 1회' 증명**: ①등록부 — `notify.kind brief.morning`·`brief.closing` 을 보내는 작업은 각 1개(기존 시험), ②notify 정책 `dedup: daily`(기존), ③**하루 운영 시뮬레이션(발송 켬)에서 `ops.notify_log` 의 그날 `brief.morning` 접수 1건·텔레그램 가짜 서버 수신 1통(본문이 길어 나뉘면 같은 dedup 키의 부분들)**, ④변형 — 08:12 scheduler 재기동·캐치업 09:00·legacy shim 경로(`alert_morning_briefing`·`alert_overnight_prediction`·`send_us_market_summary_telegram` → `brief.morning`)로 두 번째 접수 시도 → `duplicate`·국내 휴장 + 미국 거래일(브리핑 1통)·둘 다 휴장(0통) | 완료 기준을 '작업 하나'가 아니라 '실제 발송 1'로 증명한다 |
| D-P5-18 | **미국장 = `ET:board/us` 승격**(엔진·브리프·섹터 — 시험 80 [추정: `def test` 수]) → `kbj/engines/us/`. 신고가 정의는 board/us 자체 설정(`lookback` 직전 252봉 — ADR 0001 Q10) 그대로(ADR 0017 은 국장만 — ADR 0017:74). 출처 Nasdaq(screener·history — 로그인 [확인 필요: 약관])·Yahoo chart(로그인)·Stooq CSV(폴백 — 로그인 [확인 필요]) → `kbj/data/private/{nasdaq,stooq}`(US)·`kbj/data/private/yahoo`(FND — MAC 과 같이 쓴다). 결과: 일봉 `prv_market.daily_bar`(market='US' — 0003 이 이미 허용), 유니버스 `prv_market.us_universe`(0016), 산출 `prv_board.artifact`(market='US', 0016 이 `name` 제약을 넓힌다 — `us_summary`·`us_brief`). 등록부 `us.eod.writes` 의 `prv_market.us_daily_bar` 는 `prv_market.daily_bar` 로 고친다 | ADR 0001 Q10. 브리핑 ①은 US 가 써 둔 `us_brief` 산출을 읽기만 한다(BRF 와 코드로 엮이지 않는다) |
| D-P5-19 | **`market.fsc_daily`**: 금융위 주식·지수 시세(공공데이터 15094808·15094807 — P2 어댑터 `kbj/data/private/fsc_{stock,index}_price`)를 `prv_market.daily_bar`(source=`fsc`)에 쌓고, 같은 날 KRX 행과 종가 대조 — 불일치 비율이 `markets.yaml reconcile.warn_mismatch_pct` 이상이면 health 경고. 화면·브리핑에는 쓰지 않는다(KRX 확정이 정본) | D-P3-17 '대조 보조'. 공공누리 4유형 — 로그인 |
| D-P5-20 | **`market_stats.kofia`(금투협 종합통계 — 공개)·상단 띠 칩 셋**: 신용잔고·예탁금(`pub_market_stats`, 0017)·신용스프레드 AA-(ECOS 817Y002 — `pub_macro`)는 공개 칩(공개 내보내기 `credit.json`), '한국 대 글로벌'(KRX 지수 대 Yahoo 지수 — 로그인)은 로그인 칩. MAC 이 맡는다 | `web/src/app/ribbon.ts`:28·29·37 의 `phase: 'P5'` 자리, metrics §6.5 |
| D-P5-21 | **F2 = `docs/p5_flow_program_prompt.md` 그대로 + 보정 셋**: ① 등록부의 `KIS:market_investor_intraday` collects 를 `gex.poller`(P7 external)에서 **`flows.market_minute` 으로 옮긴다**(FND — 정적 겹침 방지, ADR 0019). P7 이 GX poller 를 옮길 때 poller 는 이 TR 을 부르지 않고 표를 읽는다. GX poller 의 위클리(WKM·WKI)·야간 조합은 `config/markets.yaml flows.minute.pairs` 에 **꺼진 채** 적어 두고 P7 이 켠다 ② `prv_gex.investor_flow` 의 **대금 단위는 백만원 그대로**(GX 계약·뷰 주석). API 가 원으로 바꿔 낸다 — metrics §21 에 예외로 적는다 ③ 장중 1분 작업은 실행기 `equity {start: open, end: close+10, every_min: 1}`(15:40 까지 — F2 §3.4) | F2 §2 '쓰는 주인 한 명'. 적용된 0006 은 고칠 수 없고, P7 GX 엔진이 백만원을 기대한다 |
| D-P5-22 | **뉴스(RSS 헤드라인)는 P5 범위 밖 → P6 [제안]**. 페이지 5 '뉴스' 자리는 '준비 중' 그대로 | P5 완료 기준과 무관, 범위를 늘리면 4~6일을 넘는다 |
| D-P5-23 | **켜는 기준(WRAP — `ENABLED_BY_PHASE["P5"]`)**: 처리기가 있고 + 외부 호출이 없거나, 응답 칸·코드·단위가 실측 또는 legacy 운영으로 확인된 작업만 켠다. 기본: 켬 = `themes.dictionary`·`themes.monitor`·`etf.report`·`brief.morning`·`brief.closing`·`flows.report`·`us.universe`·`us.eod`(ET us-board 운영에서 확인된 Nasdaq·Yahoo 파서)·`flows.market_minute`(GX 가 2026-09-28 실측한 K2I 3조합만 — 현물은 #39 전까지 꺼진 조합). 꺼짐(키 수령·#39~ 뒤) = `macro.*`(ECOS 항목 코드·FRED·재무부·뉴욕연은 [실측 필요])·`market_stats.kofia`·`market.fsc_daily`(`probe_results` §3.1·§3.3 은 기술문서 확인뿐 — 실응답 미확인, #48)·`program.*` 다섯. 꺼진 작업도 하루 운영 시뮬레이션은 **시험 안의 등록부 사본에서 켜서** 돈다(P4 `consensus.snapshot` 관례) | 계산 단정 금지(절대 규칙 2), 실측 전 수집 금지(이번 지시) |
| D-P5-24 | **legacy 정리 = 옮긴 만큼만, 파일 단위**(D-P3-18·D-P4-19 와 같은 규칙): 지우는 파일은 ① 그 기능을 kbj 가 대체했고 ② 남은 legacy 명령·검사 스크립트가 import 하지 않으며(R14·R17) ③ `scripts/test_legacy.sh all` 이 그대로 녹색일 때만. 원본 시험은 **승격(같은 단언, import 경로만)** 이거나 **폐지(대체 정본 시험을 MIGRATION.md 에 적음)** 둘 중 하나 — 조용히 줄지 않는다. 범위 표 §1.9 | ADR 0007 '줄어들기만 하는 기준선' |
| D-P5-25 | **새 import-linter 계약 ⑭~⑯(WRAP)**: ⑭ 공개 매크로·통계 작업(`collectors.macro`·`collectors.kofia`)과 공개 원천(`kbj.data.public.{fred,treasury,nyfed}`)은 로그인 원천·로그인 저장소를 모른다, ⑮ 분류 사전(`kbj.engines.themes.dictionary`·`conflicts`·`services.engine.themes:dictionary` 의 모듈)은 시세·로그인 저장소를 모른다(강도 `strength` 는 로그인 — 예외 모듈), ⑯ 브리핑·리포트(`kbj.reports.briefs`·`kbj.reports.flows`·`kbj.reports.etf`)는 어댑터(`kbj.data`)·수집기·API 를 모른다 — **발송 시각에 외부 호출 0**(DB 만 읽는다) | P3 ⑧~⑩·P4 ⑪~⑬ 관례. `tests/unit/test_public_writers.py` 를 공개 새 작업으로 넓힌다 |

### 0.2 만드는 것 / 만들지 않는 것 (P5 경계)

| 만든다 (P5) | 만들지 않는다 |
|---|---|
| 페이지 6 매크로 — 탭 7(금리·통화 / 물가 / 성장·경기 / 고용·가계 / 대외·환율 / 미국 / 시장(로그인)) + 경제 캘린더, 공개판(정부 시리즈) | bok 이식(코드 없음 — U3), 통화신용정책보고서 110시트 xlsx 파싱(§10 Q2), 수출 104품목(P6 — 관세청), 국장 공포탐욕(P7 진단) |
| ECOS·KOSIS 수집 작업 처리기, FRED·미 재무부·뉴욕연은(공개)·Yahoo(로그인) 어댑터, ForexFactory 어댑터(꺼짐) | Finnhub 일정, CNN 공포탐욕·alternative.me 스크랩, yfinance 옵션 PCR(→ P7), 네이버 지수 |
| 분류 사전 하나(`pub_themes`) + 충돌 검사 + 테마 강도(`prv_themes`) + 페이지 9(사전·체인 맵·강도) | monitor/kr 198·23 사전(제3자 — §10 Q1), 네이버 테마 크롤, LLM 업종 재분류(ET `board classify` — P8 까지 legacy) |
| 페이지 10 남은 것 — ETF 맵·보유 탐색·액티브 동시 편입·ETF 리포트(텔레그램 08:20) | SD ETF 맵(네이버), ETF 장중 iNAV 확대(P3 감시 상위 N 그대로) |
| 아침 브리핑 1회·마감 요약 1회·수급 리포트(차트 4장) — 본문·발송·중복 방지 | 구루 X 다이제스트(§10 Q4 — 삭제), GEX·야간선물 줄(P7), AI 추천·스윙 시그널·코멘트 초안(P8), LLM 서술 |
| 간밤 미국 신고가(us.universe·us.eod·미국 엔진·페이지 2 패널·브리핑 ①), 금융위 일별 대조 | 미국 종목 컨센서스·옵션 시그널 |
| F2 — 페이지 1 장중 투자자 1분 선·프로그램 일별 60일·프로그램 상위 20(범위 표시), 수집 5작업(프로그램은 꺼짐) | 웹소켓 프로그램(`H0STPGM0` 등 — P7 이후), 다른 증권사 순위 API, 16:00 리포트의 프로그램 줄 |
| 상단 띠 칩 셋(신용잔고·신용스프레드 — 공개, 한국 대 글로벌 — 로그인) | GEX Flip 칩(P7), 수출 속보 칩(P6) |
| legacy 정리(§1.9)·기준선 축소·새 계약 ⑭~⑯ | 뉴스 RSS(D-P5-22 → P6), 운영 VM 전환(사용자) |

### 0.3 완료 기준과 증명

| PLAN §8 P5 완료 기준 | 증명 | 묶음 |
|---|---|---|
| **테마 사전에서 한 종목의 테마 충돌이 0이다** | `tests/acceptance/test_theme_conflicts.py`(WRAP): **커밋된 실사전**(`config/knowledge/*` — 공개·자체)을 `kbj.engines.themes.dictionary.build` 에 넣어 ① 미해소 충돌(K1~K7) **0** — 실패하면 충돌 목록(종목·테마·출처·규칙)을 그대로 출력 ② 상장 보통주(`theme_codes.yaml` 기준일의 DART 상장 목록)마다 board48 섹터 정확히 1 ③ **소비자 일치**: 같은 입력으로 board 엔진(`kbj.engines.board.themes.build`·`primary`)·페이지 9 리더·브리핑 섹터 줄·종목 상세 테마 칩이 내는 종목별 (테마 집합, 대표 테마)가 사전과 같다 ④ 충돌을 하나씩 심은 변형 사전(K1~K7 각 1)에서 정확히 그 충돌 1건이 잡히고 빌드가 실패(종료 코드 1). 운영판: 사전 빌드 작업 `themes.dictionary` 가 충돌 ≥ 1 이면 실패(옛 사전 유지)·`ops.job_failed` | THM(엔진·시험 부품), WRAP(완료 시험) |
| **브리핑 발송은 하루 1회다** | D-P5-17 ①~④: `tests/unit/scheduler/test_registry_validation.py`(종류별 작업 1), `tests/sim/test_one_day_p5.py`(WRAP — 2026-11-17(화) 05:00 ~ 11-18 05:00, 발송 켬: `brief.morning` 08:10 접수 1·수신 1, `brief.closing`·`flows.report` 16:00 각 1, `etf.report` 08:20 1 — 브리핑에는 세지 않음), 변형 `test_brief_once.py`(재기동·캐치업·legacy shim 이중 접수·휴장 2종) | BRF(본문), WRAP(시뮬레이션) |
| **F2: 합성 데이터로 위젯이 그려지고, 하루 운영 시뮬레이션에서 정규장 1분 수집 빈 분 0·검산 ①~④ 0 차이, 순위 화면이 범위를 표시한다** | F2 §4·§5 그대로: 파서·가짜 KIS·검산(합성 원장 — `tests/property/test_program_checks.py`)·라우트·vitest(카드·토글·끊긴 선·빈/실측 전/휴장·순위 꼬리표), `tests/sim/test_one_day_p5.py` 의 F2 단언(09:00~15:30 391분 × 켠 조합 빈 분 0, 실패 변형에서 빈 분 = 실패한 분 수), 공개 번들·공개 내보내기에 F2 값 0 | F2, WRAP |
| (PLAN 기능) 페이지 6·9·10 이 합성 데이터로 그려진다 | `web/test/acceptance/p5_pages.test.ts`(WRAP — 6·9·10·1·2 패널, `console.error` 0, DOM 의 `NaN`·`undefined`·`Infinity` 0, 공개 빌드는 공개 패널만) | MAC·THM·ETF·US·F2, WRAP |
| 백엔드 ruff·pyright·pytest·lint-imports(**16 kept**)·check_canonical·check_public_safety, 프런트 eslint·tsc·vitest·빌드·번들 검사, CI 녹색 | §8.7 | WRAP |

### 0.4 등록부(`config/jobs.yaml`)에서 바뀌는 것 (FND 가 웨이브 1 에 모양을 확정 — 전부 `enabled: false`, 켜기는 WRAP)

| 작업 | 지금(HEAD `3cb2d5a`) | P5 |
|---|---|---|
| `macro.morning` | owner `collectors.macro:morning`, FRED·TREASURY·NYFED → `pub_macro.series` | owner 그대로(공개 모듈). collects 에 **`FRED:release_dates`**(새 — 미국 지표 발표일, 공개) 더함, writes 에 `pub_macro.calendar` |
| `macro.morning_prv` | owner `collectors.macro:morning_restricted`, YAHOO·FF | owner **`collectors.macro_prv:morning`**(D-P5-7). collects 에 **`FRED:restricted_series`**(새 — 로그인) 더함. `FF:calendar` 는 남기되 처리기가 `config/macro.yaml ff.enabled: false`(§10 Q3)면 부르지 않고 키를 `skipped` 로 |
| `macro.monthly`·`macro.evening` | owner `collectors.macro:{monthly,evening}` | 그대로(공개 모듈) |
| `macro.monthly_prv`·`macro.evening_prv` | owner `collectors.macro:{monthly,evening}_restricted` | owner **`collectors.macro_prv:{monthly,evening}`** |
| `market_stats.kofia` | P5, 처리기 없음 | 처리기 `kbj.services.collectors.kofia:run`(MAC) — writes 그대로(0017 이 표를 만든다) |
| **`themes.dictionary`(새)** | — | owner `kbj.services.engine.themes:dictionary`, `cron "30 7 * * *"`, `when: always`, 수집 없음, writes `pub_themes.{theme,stage,membership,alias,build}`. 충돌 ≥ 1 이면 실패(옛 판 유지) |
| `themes.monitor` | owner `kbj.engines.themes:monitor`(⚠ 계약 ⑨), 16:50 | owner **`kbj.services.engine.themes:monitor`**. 시각·의존 그대로(16:50 — 텔레그램 아님, ADR 0018 대상 아님), writes `prv_themes.strength` |
| **`etf.report`(새)** | — | owner `kbj.reports.etf:report`, `cron "20 8 * * 1-5"`, `when: trading_day`, 굳은 의존 `etf.collect`, 무른 의존 `{job: krx.daily, hard: false, wait_min: 20}`, `notify: {kind: etf.report}`, writes 없음. absorbs 는 `tests/unit/scheduler/legacy_jobs.txt` 의 ET daily.yml 리포트 줄 [확인 필요: 줄 이름 — `etf.collect` 가 `ET:daily.yml` 을 이미 흡수했으면 노트만] |
| `brief.morning`·`brief.closing`·`flows.report` | P5, 처리기 없음 | 처리기 `kbj.reports.briefs:{morning,closing}`·`kbj.reports.flows:daily`(BRF). 시각·의존·catch-up 그대로(ADR 0018). `brief.morning` 의 무른 의존에 `etf`·`themes` 는 더하지 않는다 |
| `us.universe`·`us.eod` | writes `prv_market.us_universe`·`prv_market.us_daily_bar` | `us.eod.writes` → **`prv_market.daily_bar`**(market='US')·`prv_board.artifact`(D-P5-18). `NASDAQ:daily` 는 Stooq 폴백을 포함한 논리 데이터셋(행 `source` 에 실제 출처) [제안] |
| `market.fsc_daily` | P5, 처리기 없음 | 처리기 `kbj.services.collectors.fsc_daily:run`(US 묶음). writes `prv_market.daily_bar`(source `fsc`) — 대조 결과는 health 경고만 |
| `guru.research` | P5 | **삭제**(2026-10-13 Q4 — 등록부에서 지움, ET xdigest 는 `retired`) |
| `gex.poller`(service, P7 external) | collects `KIS:option_chain_live`·`KIS:market_investor_intraday` | **`KIS:market_investor_intraday` 를 뺀다** — 노트 "투자자별은 flows.market_minute 이 유일하게 쓴다(ADR 0019)" |
| **`flows.market_minute`(새, F2)** | — | owner `kbj.services.collectors.flows_minute:run`, `equity {start: open, end: close+10, every_min: 1}`, `when: trading_day`, `as_of: minute`(명시), collects `KIS:market_investor_intraday`(venues 없음 — 거래소 입력 없는 TR, F2 §1.3), writes `prv_gex.investor_flow`. 조합은 `config/markets.yaml flows.minute.pairs`(켠 것만) |
| **`program.intraday`(새, F2)** | — | owner `kbj.services.collectors.program:intraday`, `equity {start: open, end: close+10, every_min: 1}`, `as_of: minute`, collects `KIS:program_market_intraday`(venues = `kis.venues`), writes `prv_flows.program_market_intraday` |
| **`program.stock_scan`(새, F2)** | — | owner `…program:scan`, `equity {start: open+5, end: close-5, every_min: 10}`(P3 10분 슬롯 경계 09:00·09:10… 와 5분 엇갈림 — §3.4), collects `KIS:program_stock_intraday`, writes `prv_flows.program_stock_intraday` |
| **`program.daily`(새, F2)** | — | owner `…program:daily`, `equity {start: close+20}`, collects `KIS:program_market_daily`·`KIS:program_investor_today`, writes `prv_flows.program_market_daily`·`prv_flows.program_investor_daily`. 처음 60거래일은 백필 작업 **`program.backfill`(새 — `manual: true`, `backfill_of: [program.daily]`, 처리기 `…program:backfill` — F2)** 을 `python -m kbj.services.scheduler backfill program.backfill --from <60거래일 전> --to <전 거래일>` 로(P3 백필 하위 명령 — `registry.py`:14·319, `docs/p3_design.md` §3.8) |
| **`program.stock_sweep`(새, F2)** | — | owner `…program:sweep`, `cron "10 18 * * 1-5"`, `when: trading_day`, 굳은 의존 `market.close_collect`, 종목 단위 키는 처리기가 자원 `claims` 로 직접 선점(P4 D-P4-25 ② — 끊기면 이어서), writes `prv_flows.program_stock_daily`. 18:10 = 16:00 발송 묶음·컨센서스(16:00~16:05) 뒤 |

등록부 시험(FND 웨이브 1 몫, `test_registry_validation.py`): 위 행의 owner·collects·writes·as_of, F2 다섯 작업의 하루 KIS 호출 합 ≤ `config/limits.yaml kis.daily_soft_cap`(새 — §3.4), 16:00 발송 셋이 ADR 0018 그대로(새 발송 `etf.report` 는 08:20 — 15:30 이후 발화 없음), `program.stock_sweep` 이 16:00 묶음과 겹치지 않음, 휴장일 미실행(`when: trading_day`), `gex.poller` 에 `market_investor_intraday` 없음. `ENABLED_BY_PHASE["P5"]` 는 WRAP.

---

## 1. 모듈 묶음·웨이브·파일 소유

### 1.0 웨이브

| 웨이브 | 묶음(동시) | 시작 조건 | 예상 |
|---|---|---|---|
| **1** | **FND** — 공통 기반(마이그레이션·행 자료형·저장소·카탈로그·등록부·설정·지표 문서·합성 세계·Yahoo 어댑터와 가짜·API 모델/라우트/빈 리더·openapi·TS 타입·공개 내보내기 고리·웹 셸·공용 차트 부품·웹 픽스처 생성기 틀·의존성) | P4 커밋 — **충족(`3cb2d5a`)** | 1.5일 |
| **2** | **MAC**(매크로·경제 캘린더·상단 띠 칩 — 페이지 6·5), **THM**(분류 사전·충돌·테마 강도 — 페이지 9), **ETF**(ETF 맵·보유 탐색·리포트 — 페이지 10), **BRF**(아침 브리핑·마감 요약·수급 리포트), **US**(간밤 미국 신고가·금융위 일별 — 페이지 2), **F2**(장중 투자자 수급·프로그램매매 — 페이지 1) | FND 커밋. 서로는 FND 가 고정한 저장소 Protocol·행 자료형·API 모델로만 맞춘다(서로의 모듈을 import 하지 않는다 — BRF 는 다른 묶음의 **저장된 산출**을 저장소로 읽는다) | 2.5~3일 |
| **3** | **WRAP** — 작업 켜기·완료 시험·하루 운영 시뮬레이션·페이지 8 고리·CI·계약 ⑭~⑯·legacy 정리·기준선·문서·ADR 확정 | 웨이브 2 전부 | 1.5일 |

합계 약 5.5~6일(PLAN 4~6일). 묶음 8개(FND 1 + 병렬 6 + WRAP 1). 묶음마다 빌드 에이전트 + 검증 에이전트(§1.11 명령). **도커 통합·legacy 전체·sim 전체는 묶음이 돌리지 않는다** — 메인이 WRAP 끝에 병렬 스크립트로 한 번.

### 1.1 FND — 공통 기반 (웨이브 1)

| 경로 | 내용 | 시험 |
|---|---|---|
| `kbj/core/p5_rows.py`(새 — P3 `rows.py`·P4 `fin_rows.py` 는 건드리지 않는다) | frozen dataclass: `MacroSeriesRow(series_id, period, value: Decimal\|None, unit, source, as_of, quality, released_at)`, `MacroSeriesMeta(series_id, tab, title, unit, freq, tier, source, dataset)`, `CalendarEvent(kind, date, time_kst\|None, title, country, importance, source, quality, actual\|None, forecast\|None)`, `ThemeRow(theme_id, name, axis, color, single_stage: bool)`, `StageRow(theme_id, stage_id, name, order)`, `MembershipRow(theme_id, code, stage_id\|None, primary: bool, source, evidence, confidence, locked)`, `AliasRow(source, source_theme, theme_id)`, `Conflict(kind: K1~K7, code\|None, theme_id\|None, detail, sources)`, `DictBuild(version, built_at, input_digest, n_themes, n_members, n_conflicts, quality)`, `ThemeStrength(theme_id, trade_date, period, ret, rs, rs_top_pct, breadth, surge, n, quality)`, `UsDailyRow`(= P3 `Bar` 재사용 가능하면 재사용 — [확인 필요]), `UsUniverseRow`, `MarketStatRow(stat, date, value, unit, source, quality)`, `InvestorMinute(ts, trade_date, session, market_code, sector_code, investor, sell/buy/net × qty/value, quality)`(investor_flow 한 행 — 대금 백만원), `ProgramMarketRow`·`ProgramInvestorRow`·`ProgramStockRow`(F2 §3.3 칸 그대로), `EtfMapCell`·`EtfHolder`·`BriefSection(kind, key, title, lines, status: ok\|not_ready\|omitted, source, as_of, quality)` | `tests/unit/core/test_p5_rows.py`(불변·직렬화·quality 값) |
| `kbj/store/migrations/0014_macro.sql`·`0015_themes.sql`·`0016_us_board.sql`·`0017_market_stats.sql`·`0018_program.sql` | §3.3 | `tests/test_store_layout.py` 확장(파일 순서·스키마·hypertable·권한), 통합 `tests/integration/test_migrations_pg.py` 확장(0001~0018 두 번 적용·`kbj_public_export` 가 0014·0015·0017 의 pub_ 만 읽음) — Docker 는 메인이 |
| `kbj/store/repos/{macro,themes,us,market_stats,program}.py`(새), `kbj/store/repos/flows.py`(추가 — `put_investor_minutes`·`investor_minutes(trade_date, pairs)` — `prv_gex.investor_flow` 읽기·쓰기, 쓰기 함수는 F2 작업만 부른다), `etf.py`(추가 — `holdings_of(fund_or_etf, date)`·`holders_of(code, date)`·`map_cells(date, period)` 입력 읽기), `board.py`(추가 — `artifact(market='US', name)` 읽기·쓰기 허용 값 확장), `public.py`(추가 — `PublicRepos` 에 `macro`·`themes`·`market_stats`), `__init__.py`·`memory.py`(`Repos`·`MemoryRepos` 에 새 속성) | Protocol + `Pg*Repo` + 메모리. 공개 저장소(`MacroPublicRepo`·`ThemesPublicRepo`·`MarketStatsRepo`)와 로그인 저장소(`MacroPrivateRepo`·`ThemeStrengthRepo`·`UsRepo`·`ProgramRepo`)를 **클래스 단위로 나눈다**(계약 ⑭·⑮ 의 import 모양) | `tests/unit/store/test_repos_p5.py`(메모리 = Pg SQL 모양 — P4 관례), 통합 `tests/integration/test_repos_p5_pg.py` |
| `kbj/data/private/yahoo/{__init__,client,datasets}.py`(새) | `ET:board/us/sources.py:parse_yahoo`:264·`yahoo_daily`:292 승격 — `YahooClient(limiter, transport=None).chart(symbol, range) -> list[Bar]`, 데이터셋 `YAHOO:us_index_daily`·`YAHOO:macro`(PLANNED 에서 옮김). 원본 `test_us_sources` 의 Yahoo 파싱 시험만 승격(나머지는 US) | `tests/unit/data/private/test_yahoo.py`(승격분 + 리미터·오류 사유) |
| `kbj/data/catalog.py`(PLANNED 정리), `kbj/data/private/kis/datasets.py`(F2 데이터셋 다섯 행 — TR id·경로는 F2 §1.4 표, 확인 안 된 칸은 `[확인 필요 #NN]`. **파서는 F2**), `kbj/data/limits.py`(새 리미터 이름 읽기만 — 필요하면) | 새 데이터셋: `FRED:release_dates`·`FRED:restricted_series`(로그인)·`STOOQ` 는 만들지 않음(D-P5-18 — NASDAQ:daily 폴백), `KIS:program_market_daily`·`program_market_intraday`·`program_investor_today`·`program_stock_intraday`·`program_stock_daily` | `tests/unit/data/test_catalog.py`·`tests/unit/kis/test_kis_datasets.py` 확장 |
| `config/jobs.yaml`(§0.4), `config/notify.yaml`(`etf.report` 노트 08:20·`board.us` 노트 "P5 — brief.morning ① 로 대체, legacy 송신 정리는 WRAP"), `config/limits.yaml`(리미터 `fred`·`treasury`·`nyfed`·`yahoo`·`ff`·`nasdaq`·`stooq` [제안 값 — §3.5], `kis.priorities` 에 `market_minute: P2`·`program_intraday: P2`·`program_scan: P3`·`program_sweep: P4`, `kis.daily_soft_cap`(새 — 등록부 시험용 [제안 30,000])), `config/markets.yaml`(`flows.minute.pairs`(K2I 3개 켬·현물·위클리·야간 꺼짐), `program.scan_top_n`(기본 30 [확인 필요])·`program.sweep_markets`) | 등록부 검증·`python -m kbj.services.scheduler validate config/jobs.yaml` |
| `kbj/config/{macro,themes_cfg,briefs}.py`(새) + `config/macro.yaml`·`config/dictionary.yaml`·`config/briefs.yaml`(새 — 숫자·목록은 한 곳, `kbj/config/markets.py` 방식 — 모르는 키 오류) | `macro.yaml`: 탭·시리즈(id·데이터셋·항목 코드·단위·tier·주기·파생식) — §4.1 표가 원본. `dictionary.yaml`: 출처 우선순위·축별 `single_stage`·대표 테마 규칙·버전. `briefs.yaml`: 섹션 순서·제목·빈 섹션 규칙(SD `SKIP_TITLES` 승계)·'미준비' 문구·시각 기준 | `tests/unit/config/test_p5_config.py`(시리즈 tier ↔ 데이터셋 tier 대조 — D-P5-6, 모르는 키 오류) |
| `kbj/services/api/models/{macro,themes,program,us}.py`(새)·`models/{etf,flows,board,market}.py`(추가 — 새 응답 모델만), `routes/{macro,themes,program}.py`(새)·`routes/{flows,etf,board}.py`(라우트 추가), `readers/{macro,themes,program,flows_minute,etf_p5,board_us}.py`(새 — **빈 리더: 언제나 `NoData`(작업 이름·예정 시각)**), `readers/_common.py`(`ApiRepos` 에 새 저장소), `app.py`·`cache.py`(라우터 등록·데이터 버전 키) | §5 의 라우트·모델 전부 | `tests/unit/api/test_routes_p5_shape.py`(라우트가 있고 빈 리더면 404 `no_data`·봉투 모양), `python -m kbj.services.api openapi --check` |
| `web/src/api/openapi.json`·`types.gen.ts`(생성) | `python -m kbj.services.api openapi --write && (cd web && npm run gen:types)` | `--check` |
| `kbj/services/public_export/{export,manifest}.py`(고리) + 빈 모듈 `macro_json.py`·`themes_json.py`·`credit_json.py`(빈 목록을 내는 함수 — 본문은 MAC·THM) | `export.py`:367 의 'P5~' 자리에 고리, `manifest` 크기 상한에 새 폴더(`macro/`·`themes/`) | `tests/unit/public_export/test_export.py`·`test_manifest.py` 확장(빈 고리면 파일 0, 공개 안전) |
| `web/scripts/merge-public-data.{mjs,d.mts}`(새 파일 이름 목록), `web/src/data/static.ts`(공개 JSON 읽기 `macro/*`·`themes/*`·`credit.json`) | — | `web/test/scripts.test.ts`·`source.test.ts` 확장 |
| `web/src/app/pages.ts`(6·9 → `tier: 'mixed'`·`phase: 'P5'`·plan 문구, 10 plan 문구), `web/src/pages/{p6_macro,p9_themes}.ts`(새 — 자리표시 모듈: '준비 중' 패널, 본문은 MAC·THM) | — | `web/test/pager.test.ts`·`tier.test.ts` 확장 |
| `web/src/ui/{line,bars,treemap}.ts`(새 공용 부품) + `web/src/design/components.css`(부품 클래스) | `line`: 여러 선·끊긴 구간(null 은 잇지 않음)·끝점 이름표·0 기준선·토글(F2·MAC). `bars`: 양·음 막대·막대 위 상세(F2 프로그램 60일·MAC). `treemap`: 정사각 분할(squarified)·크기·색 범위·라벨 줄임(THM 페이지 9·ETF 맵). 전부 SVG, 외부 라이브러리 없음, 모바일 폭 가로 넘침 없음 | `web/test/ui_p5.test.ts`(null 끊김·토글·면적 비례·색 범위·빈 입력) |
| `tests/fixtures/synthetic/p5_world.py`(새) + `p5_expect.yaml` | 고정 시드 `build_world(seed=20261113)`: 매크로 시리즈(공개·로그인 tier 섞임, 공표 지연·결측), 캘린더, 사전 원본의 축소판 + 심은 충돌 7종 변형, KRX 일봉(테마 강도 입력), ETF 구성종목·순자산, 미국 일봉·유니버스·US 산출, 금투협 통계, F2 1분 누적(시장 5 × 12구분)·프로그램 일별·장중·종목, 브리핑 재료가 일부 늦거나 없는 날. 종목 코드는 P3·P4 가짜 대역과 겹치지 않는 번호 [확인 필요: `tests/fakes/kis_server.py:SYMBOLS`·`p4_world` 와 대조] | `tests/unit/fixtures/test_p5_world.py`(결정성·`_source: SYNTHETIC`·검산 ①~④ 합성 원장 0 차이) |
| `tests/fakes/yahoo_server.py`(새) | chart 엔드포인트(일봉·장중 봉 섞임·없는 심볼·429) | `tests/unit/fakes/test_yahoo_server.py` |
| `tests/unit/api/make_web_fixtures.py`(틀 — `render_p5()` 와 `--only <도메인>` 인자) + 빈 모듈 `tests/unit/api/web_fixtures_p5/{macro,themes,etf,us,program}.py` | 도메인 묶음이 자기 모듈만 채우고 `web/test/fixtures/api/<도메인>/**` 만 쓴다 | `tests/unit/api/test_web_fixtures.py` 확장(도메인별 최신) |
| `tests/unit/scheduler/test_registry_validation.py`(§0.4 단언 — `ENABLED_BY_PHASE` 는 WRAP) | — | 등록부 검증 |
| `pyproject.toml`·`uv.lock`(`matplotlib>=3.8` 를 legacy 그룹에서 **런타임으로** — D-P5-16), `tests/test_import_contracts.py`(`RUNTIME_IMPORTS` 에 `matplotlib`), `Dockerfile`(`fonts-nanum` 한 줄) | — | `tests/test_import_contracts.py` |
| `docs/metrics.md` §16~§23(부록 A 를 옮김), `docs/probe_results.md` §7 #39~#48(부록 B — F2 줄 #39~#42 는 F2 가 채울 칸만 두고 FND 가 번호를 잡는다), `docs/secrets.md`(새 이름 없음 확인 — FRED 는 `KBJ_FRED_KEY` 이미 있음, Treasury·NYFed·Yahoo·Nasdaq·Stooq 는 키 없음 [확인 필요]) | CLAUDE §4 "먼저 쓰고 구현" | 문서 |
| `scripts/check_public_safety.py` 설정(필요할 때만 — 새 공개 폴더) | — | `tests/test_public_safety.py` |

FND 가 하지 않는 것: 파서·처리기·엔진 본문·리더 본문·페이지 본문(웨이브 2 묶음), `kis_server.py`(F2), FRED·재무부·뉴욕연은·FF 가짜 서버(MAC), Nasdaq 가짜 서버(US), sim 시나리오(WRAP).

### 1.2 MAC — 매크로·경제 캘린더·상단 띠 칩 (웨이브 2, 페이지 6·5)

| 경로 | 내용 | 시험 |
|---|---|---|
| `kbj/data/public/{fred,treasury,nyfed}/{__init__,client,datasets}.py`(새) | FRED `series/observations`·`releases/dates`(키 `KBJ_FRED_KEY` — 공용 마스킹), 재무부 수익률곡선(일별 CSV/JSON [실측 필요]), 뉴욕연은 SOFR·EFFR [실측 필요]. 리미터·오류 사유(P2 ECOS 클라이언트 모양). `FRED:gov_series` 는 `config/macro.yaml` 의 공개 시리즈만, 저작권 시리즈 요청은 `TierError`(ECOS `require_public` 와 같은 방식) | `tests/unit/data/public/test_{fred,treasury,nyfed}.py` |
| `kbj/data/private/{forexfactory,fred_restricted}.py`(새) | FF 주간 일정(비공식 — 꺼짐), FRED 저작권 시리즈(같은 FRED 클라이언트를 로그인 쪽에서 감싼다 — 공개 모듈은 로그인을 모른다, 계약 ①) | `tests/unit/data/private/test_ff.py`·`test_fred_restricted.py` |
| `kbj/services/collectors/{macro,macro_prv,kofia}.py`(새) | 공개 `morning·monthly·evening`(ECOS 한은 표·KOSIS·FRED 정부·재무부·뉴욕연은 → `pub_macro.series`·`pub_macro.calendar`), 로그인 `morning·monthly·evening`(Yahoo 12종·FRED 저작권·ECOS 타기관 802Y001·731Y001·901Y056·901Y009 → `prv_macro.*`), `kofia:run`(금투협 4종 → `pub_market_stats.*`). 공표 전이면 키 미완료(다음 실행에 다시), 값 없는 칸은 `None`(0 금지) | `tests/unit/collectors/test_macro{,_prv}.py`·`test_kofia.py`(가짜 서버·시계 주입·공표 지연·부분 실패 격리) |
| `kbj/engines/macro/{derive,calendar,ribbon}.py`(새 — 순수) | 파생: 장단기 금리차(10Y−2Y·10Y−3M), 신용스프레드(AA-·BBB- − 국고3년 — 817Y002), 전년동월비·전월비, 실질금리(정책금리 − CPI YoY), 3개월 연율; 캘린더 합치기(수기 + FRED 발표일 + FF(로그인))와 '발표 결과' 붙이기(같은 시리즈의 공표 값); 상단 띠 칩 셋 계산 | `tests/unit/engines/macro/**`(손계산 표·결측·단위·tier 섞임 금지) |
| `kbj/services/api/readers/macro.py`·`models/macro.py`(웨이브 2 본문), `readers/market.py`(상단 띠 칩 셋 부분만 — P3 파일, MAC 만 고친다) | `/api/macro/*` 와 띠 칩 | `tests/unit/api/test_routes_macro.py` |
| `kbj/services/public_export/{macro_json,credit_json}.py`(본문) | 공개 시리즈·캘린더·신용 칩 JSON(공개 표만 — 계약 ⑧·`test_sql_schemas`) | `tests/unit/public_export/test_macro_json.py` |
| `web/src/pages/p6_macro.ts`·`web/src/pages/p6/**`(본문), `web/src/pages/p5/calendar.ts`(새)·`web/src/pages/p5_filings.ts`(캘린더 패널 고리만), `web/src/app/ribbon.ts`(칩 셋의 `phase` 지움) | 탭 7 + 캘린더, 시리즈 카드(최신값·변화·as_of·출처·quality)·선 차트(FND `line`)·공개판은 공개 시리즈만, 로그인 탭은 자물쇠 | `web/test/pages/p6_macro.test.ts`·`p5_calendar.test.ts`·`ribbon.test.ts` 확장 |
| `tests/fakes/{fred,treasury,nyfed,ff}_server.py`(새), `tests/fixtures/synthetic/macro/**`(로그인 출처 합성), `tests/fixtures/public/macro/**`(ECOS·FRED 정부 실응답은 공개 등급이라 키 수령 뒤 커밋 가능 — 지금은 합성), `tests/unit/api/web_fixtures_p5/macro.py`·`web/test/fixtures/api/macro/**` | — | `tests/unit/fakes/test_macro_fakes.py` |
| `docs/adr/0021-macro-sources-tiers.md`(초안) | D-P5-5·6·7·8 | — |

### 1.3 THM — 분류 사전·충돌·테마 강도 (웨이브 2, 페이지 9)

| 경로 | 내용 | 시험 |
|---|---|---|
| `config/knowledge/valuechains.yaml`(새 — `SD:data/valuechain_map.json` 을 `git mv` 해 온 뒤 YAML 로 변환, 원본 JSON 은 변환 커밋에서 지움), `config/knowledge/{aliases,overrides,theme_codes}.yaml`(새) | 변환 규칙: 테마 5 → 정본 테마 id(`aliases` — 같은 개념이 `themes.yaml` 에 있으면 그 id), 레이어 → 단계, 세그먼트 → 하위 단계(이름·키워드·병목 여부 보존), `stocks_kr` → 멤버(출처 `sd_valuechain`), `stocks_us` → `us_peers`(참고 — 국장 충돌 계산에 넣지 않는다), `stocks_metadata` 보존. `theme_codes.yaml` = seeds 종목명 → 코드(DART `corp_code` 기준일 기록, 비상장·미해결 목록 포함) | `tests/unit/engines/themes/test_sources.py`(변환 보존 — 세그먼트 52·국내 줄 93·US 62 개수, 원본 대비 왕복) |
| `kbj/engines/themes/{__init__,dictionary,conflicts,strength,chain}.py`(새 — 순수) | `dictionary.load_sources(paths) -> Sources`, `build(sources, listed: Mapping[code, name]) -> DictResult(themes, stages, members, aliases, conflicts, digest)`, `primary(result, code)`; `conflicts.detect(...) -> list[Conflict]`(metrics §17 K1~K7·해소 규칙); `strength`(ET `monitor/kr/engine.py` 승격 — `cumulative_index`:69·`period_return`:95·`drawdown`:134·`percentile_ranks`:170·`relative_strength`:197·`sector_stats`:221·`leaders`:267·`daily_rates`:349·`momentum`:363·`rotation`:379·`dd_stats`:414·`surge_stats`:431·`sector_rs`:444 — 함수 본문 그대로, '섹터' 대신 테마 멤버로 묶음); `chain`(체인 맵 — 단계 순서·병목·확산 표시용 자료) | 승격 `tests/unit/engines/themes/test_strength.py`(`monitor/kr/tests/test_engine.py` 28 — import 경로만), 새 `test_dictionary.py`·`test_conflicts.py`(K1~K7 하나씩 심은 변형·해소 규칙·결정성)·속성 시험 `tests/property/test_dictionary_props.py`(입력 순서를 섞어도 같은 결과·대표 테마 유일) |
| `kbj/engines/board/config.py`(`load_themes` 만 — 사전 로더를 다시 내보냄, D-P5-11) | board 의 테마 매핑 결과 불변 | 기존 `tests/unit/engines/board/**`·`tests/golden/board/**` 그대로 녹색 |
| `kbj/services/engine/themes.py`(새) | `dictionary`(파일 → 빌드 → `pub_themes.*` 한 트랜잭션, 충돌 ≥ 1 이면 실패·옛 판 유지, 이름→코드는 `pub_filings.corp_code` 로 새 상장 반영 시도 — 확정본과 다르면 경고만) · `monitor`(KRX 일봉 + 사전 → `prv_themes.strength` — 오늘 행은 KIS 마감 잠정 `estimated`) · CLI `python -m kbj.services.engine.themes {build --check, resolve --write config/knowledge/theme_codes.yaml, conflicts}` | `tests/unit/engines/themes/test_service_themes.py` |
| `kbj/services/api/readers/themes.py`·`models/themes.py`(본문) | `/api/themes/*` | `tests/unit/api/test_routes_themes.py` |
| `kbj/services/public_export/themes_json.py`(본문) | 사전(테마·단계·멤버·근거·출처)만 — 강도·시세 0 | `tests/unit/public_export/test_themes_json.py` |
| `web/src/pages/p9_themes.ts`·`web/src/pages/p9/**`(본문), `web/src/pages/p8/themes.ts`(새 — 종목 상세의 테마 칩 모듈, 고리는 WRAP) | 테마 목록(강도 정렬 — 로그인)·체인 맵(단계 열 × 종목, 병목 표시)·테마 상세(멤버·근거·출처·신뢰도)·트리맵(FND `treemap` — 로그인)·공개판은 사전만 | `web/test/pages/p9_themes.test.ts`·`p8_themes.test.ts` |
| `tests/unit/api/web_fixtures_p5/themes.py`·`web/test/fixtures/api/themes/**`, `docs/adr/0020-theme-dictionary.md`(초안) | — | — |

### 1.4 ETF — ETF 맵·보유 탐색·리포트 (웨이브 2, 페이지 10)

| 경로 | 내용 | 시험 |
|---|---|---|
| `kbj/engines/etf/{map,holders,report}.py`(새 — 순수) | `map.cells(etfs, daily, period) -> list[EtfMapCell]`(분류 = `types.classify`·`etf_type` 정본, `ET:monitor/kr/etf.py` 의 `build_items`:128·`filter_universe`:168·`build_groups`:183 묶음 계산 승격 — 분류 함수는 쓰지 않는다), `holders.of_stock(holdings, code)`·`active_consensus(changes, min_funds)`(`ET:etf_tracker_v9/report.py:_consensus`:124), `report.build(D) -> list[str]`(`report.py` 의 `_head`:63·`_new_holdings`:77·`_weight_moves`:99·`_consensus`·`_dropped`:138·`_movers`:158·`_periods`:178·`_flows`:189·`_misc`:207·`build`:222 본문 그대로 — 입력 D 를 kbj 저장소 행으로 만든다) | 승격 `tests/unit/engines/etf/test_map.py`(monitor/kr `test_etf.py` 의 묶음·기간 단언 — 분류 단언은 폐지 목록), 새 `test_holders.py`·`test_report.py`(섹션 순서·HTML 이스케이프·4,096자 나눔 전 길이) |
| `kbj/reports/etf.py`(새 — 작업 처리기 `report`) | `prv_etf.change_log`·`holding`·`fund`·`etf_daily` → 본문 → `notify(kind='etf.report', subject=<기준일>)`. 그날 변동 0 이면 "변동 없음" 한 통(조용히 끝내지 않는다 — monitor/flow '대상 없는 날' 원칙) | `tests/unit/reports/test_etf_report.py` |
| `kbj/services/api/readers/etf_p5.py`·`models/etf.py`(새 모델 본문), `routes/etf.py`(FND 가 단 라우트 그대로 — 바꿀 때만) | `/api/etf/map`·`/api/etf/fund/{code}/holdings`·`/api/etf/holders/{code}`·`/api/etf/active-consensus` | `tests/unit/api/test_routes_etf_p5.py` |
| `web/src/pages/p10_etf.ts`(패널 고리)·`web/src/pages/p10/**`(새 — map·holdings·consensus), `web/src/pages/p8/etf_holders.ts`(새 — 종목 상세 '담은 ETF' 모듈, 고리는 WRAP) | ETF 맵(FND `treemap` — 유형·테마 묶음, 크기 = 순자산, 색 = 기간 수익률), 보유 탐색(검색 → 구성종목·비중·변동), 액티브 동시 편입 표 | `web/test/pages/p10_etf_p5.test.ts`·`p8_etf_holders.test.ts` |
| `tests/unit/api/web_fixtures_p5/etf.py`·`web/test/fixtures/api/etf/**`(P5 파일만), `docs/adr/0024-etf-map-report.md`(초안) | — | — |

### 1.5 BRF — 아침 브리핑·마감 요약·수급 리포트 (웨이브 2)

| 경로 | 내용 | 시험 |
|---|---|---|
| `kbj/reports/briefs/{__init__,morning,closing,sections,render,quality}.py`(새) | 작업 처리기 `morning`·`closing`(등록부 owner `kbj.reports.briefs:{morning,closing}`). 섹션 함수는 **저장소만 읽는다**(계약 ⑯): ① US 산출 `prv_board.artifact(US, us_brief)` ② `pub_macro`·`prv_macro`(`config/briefs.yaml` 의 표 시리즈) ③ 전일 이후 새 공표 ⑤ `pub_macro.calendar`·`pub_filings.earnings_schedule`·금통위 ⑦ 재료마다 as_of·quality 로 품질 줄. 마감: 지수·환율(`prv_market`)·시장 수급(`prv_flows.market_investor_daily` — 기준일 명시)·업종·특징주·신고가(`prv_board`)·수급 동향·주요 공시(`pub_filings`, 중요도 ≤ 2). 렌더는 HTML(notifier 기본)·4,096자 나눔은 notifier 에 맡김. `config/briefs.yaml` 의 빈 섹션 규칙(SD `SKIP_TITLES` 승계)·'미준비' 문구·`omitted`(단계 밖 섹션). 미리보기 CLI `python -m kbj.reports.briefs preview --kind morning\|closing --at <ISO>`(발송 없음) | 새 `tests/unit/reports/briefs/**`; **승격** — SD `scripts/check_brief_sections.py`(본문 조립 규칙 — 빈 섹션 경고·SKIP 제목)·`check_closing_brief.py`(규칙 1~5 중 kbj 에 해당하는 것: 16:00 cron·하루 1회·캐치업·재기동 캐치업 — 등록부·notifier 단언으로)·`check_futures_us_index.py` 규칙 1~3(미국 지수 줄에 미국 거래일·장중 봉을 '종가'로 적지 않음·못 받으면 옛 값 대신 '못 받음') → `tests/unit/reports/briefs/test_sd_rules.py`. 규칙 4~5(K200 선물 근월·원월)는 **P7**(D-P5-15 — 원본 스크립트는 legacy 에 남는다) |
| `kbj/reports/flows/{__init__,select,analyze,narrative,returns,chart,run}.py`(새) | `ET:monitor/flow/{analyze,narrative,returns,chart,run}.py` 승격(본문 그대로, 입력만 kbj 원장 — `krx.py`·`kissrc.py` 는 옮기지 않고 저장소 읽기로 대체), 처리기 `daily`(등록부 owner `kbj.reports.flows:daily`): 대상 = 보드 `w52`(3축 — ADR 0017) ∧ 거래량 배수, 차트 4장 → `notify_media`, 본문 → `notify(kind='flows.report')`, 기준일 줄(ADR 0018 §2-5). 대상 없는 날 = 한 줄 | **승격** `tests/unit/reports/flows/**` = `monitor/flow/tests/test_{analyze,narrative,returns,run}.py` 104(17+16+17+54) — 'd60' 단언은 'd120' 으로(ADR 0017, 시험 수 유지), `test_kis.py`·`test_krx.py` 25(14+11)는 원천 파서 시험이라 **폐지**(대체: `tests/unit/kis/test_parse_investors.py`·`tests/unit/collectors/test_market_close.py` — MIGRATION 에 적음) |
| `kbj/reports/__init__.py`(고치지 않음), `tests/fixtures/synthetic/flows_report/**`(차트 입력 합성) | — | — |
| `docs/adr/0022-briefs-unified.md`(초안) | D-P5-14·15·16·17 | — |

BRF 가 하지 않는 것: 다른 묶음의 코드 import(US 산출·매크로 시리즈·사전은 **저장된 행**만 읽는다 — FND `p5_world` 로 시험), 등록부 시각 변경(ADR 0018), 하루 운영 시뮬레이션(WRAP).

### 1.6 US — 간밤 미국 신고가·금융위 일별 (웨이브 2, 페이지 2)

| 경로 | 내용 | 시험 |
|---|---|---|
| `kbj/data/private/{nasdaq,stooq}/{__init__,client,datasets}.py`(새) | `ET:board/us/sources.py` 승격 — `parse_screener`:65·`screener`:103·`nasdaq_symbol(_forms)`:133·143·`parse_nasdaq_history`:166·`nasdaq_daily`:195·`stooq_symbol`:161·`parse_stooq`:225·`stooq_daily`:243·`history`:301(폴백 순서)·`adjust_probe`:319. 리미터 `nasdaq`·`stooq`(FND 가 limits 에) | 승격 `tests/unit/data/private/test_us_sources.py`(`test_us_sources.py` 21 중 Yahoo 몫을 뺀 나머지 — 몫 나눔은 FND 와 표로 맞춘다) |
| `kbj/engines/us/{__init__,engine,sectors,brief,search}.py`(새 — 순수) | `ET:board/us/{engine,sectors,brief,btsearch}.py` 본문 그대로(252봉·자체 설정 `config/us.yaml` 로 — `board/config/us.yaml` 을 `git mv`), `us/knowledge/{us_sectors,us_overrides}.yaml` → `config/knowledge/us/` 로 `git mv`(자체 사전 — 공개) | 승격 `tests/unit/engines/us/**` = `test_us_{engine,brief,sectors,search}.py` 50(20+17+10+3) |
| `kbj/services/collectors/{us,fsc_daily}.py`(새) | `us:universe`(주 1회)·`us:eod`(일봉 → `prv_market.daily_bar` US → 엔진 → `prv_board.artifact` `us_summary`·`us_brief`) — `ET:board/us/pipeline.py`(`universe`:22·`fetch_history`:58·`daily`:128) 승격. `fsc_daily:run`(D-P5-19) | 승격 `test_us_pipeline.py` 9 → `tests/unit/collectors/test_us.py`, 새 `test_fsc_daily.py` |
| `kbj/services/api/readers/board_us.py`·`models/us.py`(본문) | `/api/board/us`(요약·신고가·섹터·메가 무버) | `tests/unit/api/test_routes_board_us.py` |
| `web/src/pages/p2_board.ts`(패널 고리)·`web/src/pages/p2/us.ts`(새) | '간밤 미국' 패널(로그인) — 미국 거래일·출처 표시 | `web/test/pages/p2_us.test.ts` |
| `tests/fakes/nasdaq_server.py`(새 — screener·history·Stooq CSV), `tests/fixtures/synthetic/us/**`(합성 — ET `board/us/demo.py` 방식), `tests/unit/api/web_fixtures_p5/us.py`·`web/test/fixtures/api/us/**`, `docs/adr/0023-us-board.md`(초안) | — | `tests/unit/fakes/test_nasdaq_server.py` |

### 1.7 F2 — 장중 투자자 수급·프로그램매매 (웨이브 2, 페이지 1)

**`docs/p5_flow_program_prompt.md` 를 그대로 한 묶음으로 한다** — §3 순서(1 체크리스트 → 2 데이터셋·파서 → 3 저장 → 4 수집 작업 → 5 API → 6 웹 → 7 텔레그램 없음), §4 시험, §5 완료 기준. 이 설계가 정한 넣는 자리와 보정만 적는다:

| F2 § | 이 설계에서 |
|---|---|
| §3.1 체크리스트 | `docs/probe_results.md` §7 **#39~#42**(번호는 FND 가 잡는다 — 부록 B). F2 는 칸·단위·코드·반영 시각만 채운다(값 없음) |
| §3.2 데이터셋·파서 | 데이터셋 행은 **FND**(카탈로그·등록부 검증이 웨이브 1 에 닫히게). F2 는 `kbj/data/private/kis/{investors_minute,program}.py`(새 — 파서)와 `datasets.py` 의 노트·`[확인 필요]` 갱신만. 12구분 → 7카드 매핑은 FND 가 metrics §21 에 쓴 정의를 따르고, 바꿀 것은 요청 |
| §3.3 저장 | 표·저장소는 **FND**(0018·`repos/program.py`·`flows.py` 의 investor_flow 함수). 대금: investor_flow 는 **백만원**(D-P5-21 ②), 프로그램 표는 원 |
| §3.4 수집 작업 | 등록부 행은 FND(§0.4), 처리기 `kbj/services/collectors/{flows_minute,program}.py` 는 F2. 같은 분 재실행 멱등(UNIQUE `(ts, market_code, sector_code, investor)`), 휴장일은 `when: trading_day` |
| §3.5 API | 라우트·모델은 FND(§5), 리더 `readers/{flows_minute,program}.py`·모델 본문 `models/program.py` 는 F2 |
| §3.6 웹 | `web/src/pages/p1_market.ts`(패널 고리 — **이 파일은 F2 만 고친다**, P3 의 `fill`·`filters`·`nameCell`·`REFRESH_MS` 내보내기는 그대로 — `p10_etf.ts` 가 쓴다)·`web/src/pages/p1/{flows_minute,program}.ts`(새). 차트는 FND `line`·`bars` |
| §4 시험 | 가짜 KIS 는 **F2 가 `tests/fakes/kis_server.py` 를 고친다**(P5 에서 이 파일을 고치는 묶음은 F2 하나). `tests/sim/harness.py` 는 **`KIS_SPECS` 줄만**(P4 K 관례), 하루 운영 시뮬레이션 단언은 WRAP |
| ADR | `docs/adr/0019-investor-flow-single-writer.md`(초안 — F2 §2 + 보정 ①②) |

### 1.8 WRAP — 마무리 (웨이브 3)

1. **작업 켜기**: `config/jobs.yaml` `enabled: true` 를 D-P5-23 목록대로, `tests/unit/scheduler/test_registry_validation.py` 에 `ENABLED_BY_PHASE["P5"]`. 꺼진 채 남는 작업마다 노트에 막는 실측 번호(#39~#48).
2. **완료 시험**: `tests/acceptance/test_theme_conflicts.py`(§0.3 ①~④), `tests/sim/test_one_day_p5.py`·`test_brief_once.py`(D-P5-17), F2 §5(`test_one_day_p5.py` 의 F2 단언 + `tests/sim/test_intraday_minute.py` 실패 변형), `web/test/acceptance/p5_pages.test.ts`.
3. **하루 운영 시뮬레이션**(`tests/sim/test_one_day_p5.py`, 2026-11-17(화) 05:00 ~ 11-18 05:00 — 서머타임 끝(11-01) 뒤라 `us.eod` 06:10 KST): 켜진 P3·P4·P5 작업 실제 처리기 + 꺼진 P5 작업(`macro.*`·`program.*`·`market.fsc_daily`)은 등록부 사본에서 켬. 단언: 데이터 키 각 done 1·선점 거절은 이미 받은 키뿐·KIS 어떤 1초에도 ≤ 4·발급은 auth 만·정규장 1분 빈 분 0·`program.stock_sweep` 18:10 시작·16:00 발송 셋이 보드 뒤·브리핑 1통씩·`etf.report` 1통·공개 산출물(`macro/`·`themes/`·`credit.json`) 생성과 공개 안전 검사 통과·로그인 출처 이름 0. `tests/sim/harness.py`(P5 자원·`p5` 플래그·새 출처 가짜 연결 — `OFFLINE_SOURCES` 에서 FRED·TREASURY·NYFED·YAHOO·NASDAQ 을 빼고 가짜 서버로).
4. **페이지 8 고리**: `web/src/pages/p8_stock.ts` 에 THM `p8/themes.ts`·ETF `p8/etf_holders.ts` 를 단다(두 묶음이 같은 P4 파일을 고치지 않게).
5. **CI**(`.github/workflows/ci.yml`): `test (kbj)` 잡에 `fonts-nanum`, sim 잡 경로, 새 시험 경로. legacy 매트릭스에서 지운 부분(`kr`·`flow` — §1.9) 빼기.
6. **import-linter 계약 ⑭~⑯**(D-P5-25) + `tests/test_import_contracts.py`·`tests/unit/test_public_writers.py`(공개 새 작업 `macro.*`·`market_stats.kofia`·`themes.dictionary` 의 SQL 스키마는 `pub_` 만) → **16 kept, 0 broken**.
7. **legacy 정리**(§1.9 — 옮긴 만큼만, `scripts/test_legacy.sh` 기준 근거): 지운 파일·옮긴/폐지 시험 수를 `legacy/*/MIGRATION.md` 'P5' 절에, `scripts/test_legacy.sh` 의 기준 수·부분 목록·머리말을 고친다.
8. **`check_canonical` 기준선**: 지운 파일의 줄을 `--update-baseline`(줄이는 방향만)으로, 머리말에 P5 줄. 새 그룹 없음(Yahoo·Nasdaq·Stooq·FRED 호스트를 그룹으로 만들지는 [제안] — 만들면 kbj 안은 어댑터만, legacy 0 이 목표).
9. **문서·ADR**: ADR 0019~0024 상태 확정('채택' + WRAP 확정 메모), `docs/metrics.md` §16~§23 확정(구현 묶음 주석 반영 — ⚠ 남은 결정 표시), `docs/PLAN.md` §8 P5 '결과' 절(P3·P4 형식 — 실측 수치), `docs/probe_results.md` #39~#48, `README`(필요하면), `docs/p5_design.md` 상태 '구현 완료'.
10. 메인이 마지막에 도커 통합·legacy 전체·sim 전체를 병렬 스크립트로 한 번(묶음은 돌리지 않는다).

### 1.9 legacy 이전 범위 (D-P5-24 — 옮긴 만큼만, 파일 단위)

규칙: **승격한 모듈은 legacy 에서 kbj 를 다시 내보내는 shim**(P3 board·P4 dart-report 와 같은 방식 — legacy 시험이 shim 으로 계속 돈다, 다리 시험 +1) 또는 **삭제**(남은 legacy 명령·검사 스크립트가 import 하지 않을 때만 — WRAP 이 `grep -rn` 과 `scripts/test_legacy.sh <부분>` 으로 확인). 원본 시험은 **승격**(같은 단언 — import 경로만, ADR 0017 처럼 정의가 이미 바뀐 단언은 그 ADR 대로 옮기고 수 유지) 또는 **폐지**(대체 정본 시험을 `MIGRATION.md` 에 1:1 로 적음). 숫자는 [추정] — WRAP 이 실측으로 확정한다.

| legacy | P5 처리 | 옮김(kbj) | 폐지 | legacy 에 남김 | 기준선 | 묶음 |
|---|---|---|---|---|---|---|
| `ET:monitor/flow/` 전체(`run`·`analyze`·`narrative`·`returns`·`chart`·`krx`·`kissrc`·`telegram`) | `kbj/reports/flows` 로 승격 후 **디렉터리 삭제**(flows.report 가 대체 — ET `flow.yml:report` 는 등록부 absorbs) | 104(`test_{analyze,narrative,returns,run}`) | 25(`test_kis` 14·`test_krx` 11 — KIS·KRX 원천 파서 시험, 대체 `tests/unit/kis/test_parse_investors.py`·`tests/unit/collectors/test_market_close.py`) | 0 — `test_legacy.sh` 의 `flow` 부분 삭제 | krx_scrape −5(`monitor/flow/krx.py`) | BRF(승격), WRAP(삭제) |
| `ET:monitor/kr/engine.py` | `kbj/engines/themes/strength.py` 로 승격, legacy 는 shim | 28(`test_engine`) | — | `build`·`etf`·`financials`·`flows`·`industries`·`ingest`·`template.html`·합성 사전 — 그 시험 66 + 다리 1(kr **94 → 67**). 전부 P9 `legacy/` 비우기 때(화면은 SPA 페이지 9 가 대체, ETF 분류 두 번째 사본 `etf.classify` 는 실행되지 않는 legacy 로만 남는다 — ⚠ 정본은 `kbj.engines.etf.types`) | 0 | THM |
| `ET:board/us/`(`sources`·`engine`·`sectors`·`brief`·`btsearch`·`pipeline`·`build`·`render`·`db`·`demo`, `knowledge/`) | `kbj/data/private/{yahoo,nasdaq,stooq}`·`kbj/engines/us`·`kbj/services/collectors/us` 로 승격, legacy 는 shim(`run.py cmd_us_*` 가 계속 import), `knowledge/*.yaml`·`config/us.yaml` 은 `git mv` → `config/knowledge/us/`·`config/us.yaml` | 80(`test_us_{sources,engine,brief,sectors,pipeline,search}`) | — | `build`·`render`·`db`(정적 사이트·sqlite — 화면은 페이지 2) shim 뒤에 그대로, board **1,115 → 1,036**(−80 + 다리 1) | 0(Nasdaq·Stooq·Yahoo 는 그룹 없음) | US·FND(Yahoo 몫) |
| `ET:etf_tracker_v9/{tracker,report,market,render,dash,live_update}.py` | `kbj.reports.etf`(리포트)·P3 `etf.collect`(수집)·페이지 10 이 대체 → **삭제**(import 하는 legacy 가 남지 않을 때 — `collectors`·`themes`·`adapters/*` 는 P3 shim 그대로) | 0(원본 시험 없음 — import 스모크뿐) | — | import 스모크 **13 → 7 모듈**(`collectors, themes, adapters.{ace,hanaro,koact,plus,rise}`) | naver −4(`market.py` 3·`render.py` 1) | ETF(대체), WRAP(삭제) |
| `SD:server.py` 함수 — 아침 계열(`alert_morning_briefing`:4589·`alert_overnight_prediction`:4835·`send_us_market_summary_telegram`·`_refresh_briefing_data`·`_refresh_global_data_periodic`:4979), 매크로(`_MACRO_TICKERS`:7988·`api_macro`:8004·`api_global_macro`:13126), ETF 맵(`_scrape_etf_price`:5098·`api_etf_map`:5151), 테마(`/api/themes`:1025·1036·`generate_themes_mapping`), 밸류체인 라우트(`/api/valuechain*`:13013~13757), 수급 시그널(`alert_flow_signals`), 미국 일봉(`_daily_us_market_build`·`sync_us_market_to_db_from_cache`·`refresh_us_universe_if_stale`) + 이들의 `add_job` | **함수 단위 삭제**(D-P4-19 방식) — 검사 스크립트가 소스에서 떼어 쓰는 함수(`send_market_summary_telegram`·`send_closing_market_summary`·`closing_brief_catchup`·마감 시황 섹션·워치독·신고가·`_build_data_json` 계열)는 **남긴다**(R14 — `check_{brief_sections,closing_brief,futures_us_index,ohlcv_autofill}` 이 본다. 선물 줄은 P7) | SD 규칙 승격: `check_brief_sections`·`check_closing_brief`·`check_futures_us_index`(1~3) → `tests/unit/reports/briefs/test_sd_rules.py`(검사 스크립트는 그대로 남아 legacy 도 계속 본다) | — | SD **13 그대로** | naver·krx_scrape 은 지운 함수만큼 [추정 — WRAP 실측] | BRF(규칙 승격), WRAP(삭제) |
| `SD:theme_crawler.py`, `SD:scripts/{fix_valuechain_map,fix_valuechain_step2,fix_valuechain_v2,validate_valuechain_external,validate_valuechain_llm}.py` | 네이버 크롤(U4)·사전 수동 정비 도구 — 사전이 `config/knowledge` 로 갔으므로 **삭제** | 0(시험 없음) | — | — | naver −3(`theme_crawler`)·−1(`validate_valuechain_external`) | WRAP |
| `SD:data/valuechain_map.json`·`SD:valuechain.py` | JSON 은 THM 이 `git mv` → 변환(`config/knowledge/valuechains.yaml`). `valuechain.py` 는 **남긴다**(`analysis_journal_helper.py`:64·195 — 분석 일지 P8 가 쓴다) — 읽는 곳 `load_valuechain_map` 은 kbj `kbj.engines.themes.sources.valuechain_map_v2()`(원본 v2.0 모양을 돌려주는 함수 — 왕복 골든으로 같음 증명)를 부르게 한 줄 바꾼다 | 0 | — | `valuechain.py`(P8) | naver 그대로(1) | THM |
| GX poller 투자자별(`GX:services/poller/planner.py:_investors`:347) | **바꾸지 않는다**(P7). 등록부에서 collects 만 F2 로(§0.4). ⚠ legacy GX poller 를 VM 에서 kbj 와 함께 돌리면 같은 TR 을 두 곳이 부른다 — ADR 0019 에 운영 조건("GX poller 를 켜면 `investor_period_s` 를 끄는 설정이 필요 — 지금 없음 [확인 필요]") | — | — | — | — | F2(ADR) |

### 1.10 묶음별 파일 소유 (겹치지 않는다)

| 묶음 | 손대는 파일 |
|---|---|
| FND | `kbj/core/p5_rows.py`, `kbj/store/migrations/0014~0018_*.sql`, `kbj/store/repos/{macro,themes,us,market_stats,program}.py`·`flows.py`·`etf.py`·`board.py`·`public.py`·`__init__.py`·`memory.py`(웨이브 1), `kbj/data/private/yahoo/**`, `kbj/data/catalog.py`, `kbj/data/private/kis/datasets.py`(웨이브 1 — F2 데이터셋 행), `kbj/config/{macro,themes_cfg,briefs}.py`, `config/{jobs,notify,limits,markets,macro,dictionary,briefs}.yaml`(웨이브 1), `kbj/services/api/models/{macro,themes,program,us}.py`·`routes/{macro,themes,program}.py`·`readers/{macro,themes,program,flows_minute,etf_p5,board_us}.py`(웨이브 1 — 빈 리더)·`routes/{flows,etf,board}.py`·`models/{etf,flows,board,market}.py`(새 모델만)·`readers/_common.py`·`app.py`·`cache.py`, `kbj/services/public_export/{export,manifest}.py`·`{macro_json,themes_json,credit_json}.py`(빈 고리), `web/src/api/{openapi.json,types.gen.ts}`(생성), `web/src/app/pages.ts`, `web/src/data/static.ts`, `web/scripts/merge-public-data.{mjs,d.mts}`, `web/src/pages/{p6_macro,p9_themes}.ts`(자리표시), `web/src/ui/{line,bars,treemap}.ts`, `web/src/design/components.css`, `tests/fixtures/synthetic/{p5_world.py,p5_expect.yaml}`, `tests/fakes/yahoo_server.py`, `tests/unit/api/make_web_fixtures.py`·`web_fixtures_p5/__init__.py`·`web_fixtures_p5/*.py`(빈 모듈), `tests/unit/scheduler/test_registry_validation.py`(웨이브 1 몫), `tests/sim/harness.py`(웨이브 1 — `_gx_poll` 의 투자자별 줄 삭제만)·`tests/sim/test_one_day*.py`·`test_intraday_hour.py`(키 수 상수만 — R16), `tests/test_store_layout.py`, `tests/test_import_contracts.py`(웨이브 1 — `RUNTIME_IMPORTS`), `pyproject.toml`·`uv.lock`(웨이브 1 — matplotlib), `Dockerfile`, `docs/metrics.md`(§16~§23), `docs/probe_results.md`(#39~#48 칸), FND 시험 파일(§1.1) |
| MAC | `kbj/data/public/{fred,treasury,nyfed}/**`, `kbj/data/private/{forexfactory,fred_restricted}.py`, `kbj/services/collectors/{macro,macro_prv,kofia}.py`, `kbj/engines/macro/**`, `kbj/services/api/readers/macro.py`·`models/macro.py`(웨이브 2)·`readers/market.py`(띠 칩 부분), `kbj/services/public_export/{macro_json,credit_json}.py`(본문), `web/src/pages/p6_macro.ts`·`p6/**`·`p5/calendar.ts`·`p5_filings.ts`(고리), `web/src/app/ribbon.ts`, `tests/fakes/{fred,treasury,nyfed,ff}_server.py`, `tests/fixtures/synthetic/macro/**`, `tests/unit/api/web_fixtures_p5/macro.py`, `web/test/fixtures/api/macro/**`, MAC 시험(§1.2), `docs/adr/0021`(초안) |
| THM | `config/knowledge/{valuechains,aliases,overrides,theme_codes}.yaml`, `legacy/stock_dashboard/data/valuechain_map.json`(`git mv`)·`legacy/stock_dashboard/valuechain.py`(한 줄), `kbj/engines/themes/**`, `kbj/engines/board/config.py`(`load_themes` 만), `kbj/services/engine/themes.py`, `kbj/services/api/readers/themes.py`·`models/themes.py`(웨이브 2), `kbj/services/public_export/themes_json.py`(본문), `web/src/pages/p9_themes.ts`·`p9/**`·`p8/themes.ts`, `legacy/etf_traker/monitor/kr/engine.py`(shim)·`monitor/kr/tests/test_kbj_bridge.py`(새 다리), `tests/unit/api/web_fixtures_p5/themes.py`, `web/test/fixtures/api/themes/**`, THM 시험(§1.3), `docs/adr/0020`(초안) |
| ETF | `kbj/engines/etf/{map,holders,report}.py`, `kbj/reports/etf.py`, `kbj/services/api/readers/etf_p5.py`·`models/etf.py`(웨이브 2 — P5 모델 본문)·`routes/etf.py`(웨이브 2 — 필요할 때만), `web/src/pages/p10_etf.ts`·`p10/**`·`p8/etf_holders.ts`, `tests/unit/api/web_fixtures_p5/etf.py`, `web/test/fixtures/api/etf/**`(P5 파일), ETF 시험(§1.4), `docs/adr/0024`(초안) |
| BRF | `kbj/reports/briefs/**`, `kbj/reports/flows/**`, `tests/fixtures/synthetic/flows_report/**`, `tests/unit/reports/{briefs,flows}/**`, `docs/adr/0022`(초안) |
| US | `kbj/data/private/{nasdaq,stooq}/**`, `kbj/engines/us/**`, `kbj/services/collectors/{us,fsc_daily}.py`, `kbj/services/api/readers/board_us.py`·`models/us.py`(웨이브 2), `web/src/pages/p2_board.ts`(고리)·`p2/us.ts`, `config/us.yaml`·`config/knowledge/us/**`(`git mv`), `legacy/etf_traker/board/us/**`(shim)·`board/tests/test_us_*.py`(옮김 — 삭제)·`board/tests/test_kbj_us_shim.py`(새 다리), `tests/fakes/nasdaq_server.py`, `tests/fixtures/synthetic/us/**`, `tests/unit/api/web_fixtures_p5/us.py`, `web/test/fixtures/api/us/**`, US 시험(§1.6), `docs/adr/0023`(초안) |
| F2 | `kbj/data/private/kis/{investors_minute,program}.py`, `kbj/data/private/kis/datasets.py`(웨이브 2 — 노트·`[확인 필요]` 만), `kbj/services/collectors/{flows_minute,program}.py`, `kbj/engines/flows/{minute,program}.py`(새 — 순수: 7카드·검산 ①~④·순위·범위 꼬리표), `kbj/services/api/readers/{flows_minute,program}.py`·`models/program.py`(웨이브 2), `kbj/services/engine/verify.py`(`--program` 하위 명령만 — P3 파일), `tests/fakes/kis_server.py`, `tests/sim/harness.py`(웨이브 2 — `KIS_SPECS` 줄만), `tests/fixtures/synthetic/kis/p5_*`, `web/src/pages/p1_market.ts`(고리)·`p1/**`, `tests/unit/api/web_fixtures_p5/program.py`, `web/test/fixtures/api/program/**`, `docs/probe_results.md`(#39~#42 칸 채우기만), F2 시험(F2 §4 — `tests/unit/kis/test_parse_{investors_minute,program}.py`·`tests/unit/collectors/test_{flows_minute,program}.py`·`tests/unit/engines/flows/test_{minute,program}.py`·`tests/property/test_program_checks.py`·`tests/unit/api/test_routes_program.py`·`web/test/pages/p1_{flows_minute,program}.test.ts`), `docs/adr/0019`(초안) |
| WRAP | `config/jobs.yaml`(웨이브 3 — enabled), `tests/unit/scheduler/test_registry_validation.py`(웨이브 3 — `ENABLED_BY_PHASE`), `tests/acceptance/test_theme_conflicts.py`, `tests/sim/{test_one_day_p5,test_brief_once,test_intraday_minute}.py`·`harness.py`(웨이브 3)·`p5.py`, `web/test/acceptance/p5_pages.test.ts`, `web/src/pages/p8_stock.ts`(고리 둘), `pyproject.toml`·`uv.lock`(웨이브 3 — 계약 ⑭~⑯), `tests/test_import_contracts.py`(웨이브 3), `tests/unit/test_public_writers.py`, `.github/workflows/ci.yml`, `scripts/{canonical_baseline.txt,test_legacy.sh}`, `tests/test_check_canonical.py`(필요할 때), §1.9 의 삭제 파일, `legacy/*/MIGRATION.md`, `docs/{PLAN,metrics,probe_results}.md`(웨이브 3), `docs/adr/0019~0024`(확정), `docs/p5_design.md`(상태) |

**공유 파일 — 누가 언제**(한 칸에 한 묶음)

| 파일 | 웨이브 1 | 웨이브 2 | 웨이브 3 |
|---|---|---|---|
| `config/jobs.yaml` | **FND**(§0.4 모양, 전부 false) | 안 고침 — 바꿀 것은 WRAP 에 요청 | **WRAP**(enabled) |
| `config/{limits,markets,notify}.yaml`·`config/{macro,dictionary,briefs}.yaml` | **FND** | 안 고침(값 변경은 요청 — 단, `macro.yaml` 시리즈 항목 코드의 `[실측 필요]` 표시는 MAC, `dictionary.yaml` 규칙 값은 THM, `briefs.yaml` 문구는 BRF 가 고친다 — 각 파일 한 묶음) | WRAP |
| `kbj/data/private/kis/datasets.py` | **FND**(F2 행) | **F2**(노트만) | — |
| `kbj/store/repos/*`·`memory.py`·`__init__.py` | **FND** | 안 고침(모자라면 FND 담당에게 요청 — 웨이브 2 동안 대기) | — |
| `kbj/services/api/models/*`·`routes/*`·`readers/*` | **FND**(모양·빈 리더) | 도메인 묶음이 **자기 리더·모델 파일만**(MAC macro·market 띠 / THM themes / ETF etf_p5·etf / US board_us·us / F2 flows_minute·program) | — |
| `web/src/api/openapi.json`·`types.gen.ts` | **FND**(생성) | 생성 전용 — 모델을 바꾼 묶음이 생성 명령만 | **WRAP**(`--check`) |
| `kbj/services/public_export/{export,manifest}.py` | **FND**(고리) | 안 고침 | — |
| `web/src/app/*`·`web/src/ui/*`·`web/src/data/*`·`web/src/design/*`·`web/package.json` | **FND** | 안 고침 — 단 `web/src/app/ribbon.ts` 는 MAC | — |
| `web/src/pages/p8_stock.ts` | — | 안 고침(THM·ETF 는 하위 모듈만) | **WRAP**(고리) |
| `tests/fakes/kis_server.py` | — | **F2** | WRAP 은 쓰기만 |
| `tests/sim/harness.py` | **FND**(`_gx_poll` 투자자별 줄 삭제만 — R16) | **F2**(`KIS_SPECS` 줄만) | **WRAP** |
| `tests/fixtures/synthetic/p5_world.py`·`p5_expect.yaml` | **FND** | 안 고침(필요한 사례는 FND 담당에게 요청) | WRAP(완료 시험 사례 보강 시) |
| `tests/unit/api/make_web_fixtures.py` | **FND**(틀·`--only`) | 도메인 묶음은 `web_fixtures_p5/<도메인>.py`·`web/test/fixtures/api/<도메인>/**` 만 | — |
| `tests/unit/scheduler/test_registry_validation.py` | **FND**(P5 행 단언) | 안 고침 | **WRAP**(`ENABLED_BY_PHASE["P5"]`) |
| `pyproject.toml`·`uv.lock`·`tests/test_import_contracts.py` | **FND**(matplotlib) | 안 고침 | **WRAP**(계약 ⑭~⑯) |
| `docs/metrics.md` | **FND**(§16~§23) | 안 고침(정의 변경은 요청) | **WRAP** |
| `docs/probe_results.md` | **FND**(#39~#48 칸) | **F2**(#39~#42 칸 채우기만 — 다른 묶음은 요청) | **WRAP** |
| ADR | — | 초안: 0019 F2·0020 THM·0021 MAC·0022 BRF·0023 US·0024 ETF | **WRAP** 가 상태 확정 |

### 1.11 묶음별 완료 확인 명령 (모두 종료 코드 0)

> 같은 작업 트리에서 여러 묶음이 동시에 일한다 — 묶음은 **자기 경로 시험 + 빠른 공통 명령**만 돌리고 실패는 자기 파일 것만 고친다(남의 미완성 때문에 깨진 공통 시험은 그 묶음에 알린다). **도커 통합(`-m integration`)·legacy 전체(`test_legacy.sh all`)·sim 전체는 돌리지 않는다**(Docker 충돌) — 메인이 WRAP 뒤 병렬 스크립트로 한 번. 시험은 `-q` 로, 실패만 본다. 시험이 읽는 파일이 `.gitignore` 에 걸리지 않았는지 `git status --ignored` 로 본다.

**공통(모든 백엔드 묶음)**: `uv run ruff check && uv run ruff format --check && uv run pyright && uv run lint-imports && uv run python scripts/check_canonical.py && uv run python scripts/check_public_safety.py --exclude legacy && uv run pytest -m "not sim and not integration and not legacy" -q` — 웹을 고친 묶음은 `cd web && npm run lint && npm run typecheck && npx vitest run <내 시험>` 도.

| 묶음 | 묶음 명령(공통에 더해) |
|---|---|
| FND | `uv run pytest tests/test_store_layout.py tests/test_import_contracts.py tests/unit/core tests/unit/store tests/unit/data tests/unit/config tests/unit/scheduler tests/unit/fixtures/test_p5_world.py tests/unit/fakes/test_yahoo_server.py tests/unit/api/test_routes_p5_shape.py tests/unit/api/test_web_fixtures.py tests/unit/public_export -q && uv run python -m kbj.services.scheduler validate config/jobs.yaml && uv run python -m kbj.services.api openapi --check && (cd web && npm run gen:types -- --check && npm run lint && npm run typecheck && npx vitest run test/ui_p5.test.ts test/pager.test.ts test/tier.test.ts test/scripts.test.ts test/source.test.ts)` |
| MAC | `uv run pytest tests/unit/data/public/test_fred.py tests/unit/data/public/test_treasury.py tests/unit/data/public/test_nyfed.py tests/unit/data/private/test_ff.py tests/unit/data/private/test_fred_restricted.py tests/unit/collectors/test_macro.py tests/unit/collectors/test_macro_prv.py tests/unit/collectors/test_kofia.py tests/unit/engines/macro tests/unit/api/test_routes_macro.py tests/unit/public_export/test_macro_json.py tests/unit/fakes/test_macro_fakes.py -q && uv run python -m tests.unit.api.make_web_fixtures --only macro && uv run pytest tests/unit/api/test_web_fixtures.py -q -k macro && (cd web && npx vitest run test/pages/p6_macro.test.ts test/pages/p5_calendar.test.ts test/pages/ribbon.test.ts)` |
| THM | `uv run pytest tests/unit/engines/themes tests/property/test_dictionary_props.py tests/unit/engines/board tests/golden/test_board_golden.py tests/unit/api/test_routes_themes.py tests/unit/public_export/test_themes_json.py -q && uv run python -m kbj.services.engine.themes build --check && uv run python -m tests.unit.api.make_web_fixtures --only themes && uv run pytest tests/unit/api/test_web_fixtures.py -q -k themes && (cd web && npx vitest run test/pages/p9_themes.test.ts test/pages/p8_themes.test.ts) && uv run bash scripts/test_legacy.sh kr` |
| ETF | `uv run pytest tests/unit/engines/etf tests/unit/reports/test_etf_report.py tests/unit/api/test_routes_etf_p5.py -q && uv run python -m tests.unit.api.make_web_fixtures --only etf && uv run pytest tests/unit/api/test_web_fixtures.py -q -k etf && (cd web && npx vitest run test/pages/p10_etf.test.ts test/pages/p10_etf_p5.test.ts test/pages/p8_etf_holders.test.ts)` |
| BRF | `uv run pytest tests/unit/reports/briefs tests/unit/reports/flows -q` (차트 시험은 한글 폰트가 있어야 한다 — 없으면 `apt-get install fonts-nanum` 뒤. 건너뛰기로 바꾸지 않는다) |
| US | `uv run pytest tests/unit/data/private/test_us_sources.py tests/unit/engines/us tests/unit/collectors/test_us.py tests/unit/collectors/test_fsc_daily.py tests/unit/api/test_routes_board_us.py tests/unit/fakes/test_nasdaq_server.py -q && uv run python -m tests.unit.api.make_web_fixtures --only us && uv run pytest tests/unit/api/test_web_fixtures.py -q -k us && (cd web && npx vitest run test/pages/p2_board.test.ts test/pages/p2_us.test.ts) && uv run bash scripts/test_legacy.sh board` |
| F2 | `uv run pytest tests/unit/kis tests/unit/collectors/test_flows_minute.py tests/unit/collectors/test_program.py tests/unit/engines/flows tests/property/test_program_checks.py tests/unit/api/test_routes_program.py tests/unit/fakes -q && uv run python -m kbj.services.scheduler validate config/jobs.yaml && uv run python -m tests.unit.api.make_web_fixtures --only program && uv run pytest tests/unit/api/test_web_fixtures.py -q -k program && (cd web && npx vitest run test/pages/p1_market.test.ts test/pages/p1_flows_minute.test.ts test/pages/p1_program.test.ts)` |
| WRAP | 공통 + `uv run pytest tests/acceptance -q && uv run pytest tests/sim -m "sim and not slow" -q -k "p5 or brief_once or intraday_minute" && uv run python -m kbj.services.scheduler validate config/jobs.yaml && uv run python -m kbj.services.api openapi --check && uv run python scripts/check_public_safety.py && bash scripts/build_public_site.sh --dry-run` + `cd web && npm run gen:types -- --check && npm run lint && npm run typecheck && npm test && npm run build:public && npm run build:login && node scripts/check-bundle.mjs` + 메인: Docker 통합·`test_legacy.sh all`·sim 전체·CI 녹색(푸시 뒤) |

### 1.12 옮기는 원본 시험 수 [추정 — WRAP 이 실측]

| 묶음 | 옮김(kbj 로) | 폐지(대체 정본 시험 기록) | legacy 기준 수 변화 |
|---|---|---|---|
| BRF | monitor/flow 104 + SD 검사 규칙(`check_brief_sections`·`check_closing_brief` 해당분·`check_futures_us_index` 1~3 → 새 시험 약 15 — 원본 스크립트는 legacy 에 남음) | monitor/flow 25 | flow 129 → 0(부분 삭제) |
| THM | monitor/kr `test_engine` 28 | — | kr 94 → 67(다리 +1) |
| US·FND | board `test_us_*` 80(Yahoo 파싱 몫은 FND) | — | board 1,115 → 1,036(다리 +1) |
| ETF | 0(etf_tracker_v9 는 import 스모크뿐) | — | etf_tracker_v9 13 → 7 모듈 |
| WRAP | — | — | stock_dashboard 13 그대로 |

kbj 시험은 약 +210(승격) + 새 시험. GEXLAB·dart-report·flowlab 은 바뀌지 않는다.

---

## 2. 공개/로그인 배치

| 페이지·기능 | 공개(정적 사이트 — `pub_*` 만) | 로그인(VM API) | 구조로 막는 곳 |
|---|---|---|---|
| 6 매크로 | ECOS 한은 작성 표·KOSIS·FRED 정부 시리즈·미 재무부·뉴욕연은([확인 필요] — DATA_TIERS §1 에 행 추가 요청, `docs/inventory.md`:52)·경제 캘린더(공식 일정) | + 시장 탭(Yahoo 12종·FRED 저작권·ECOS 타기관 802Y001·731Y001·901Y056·901Y009)·FF 예상치 | 데이터셋 tier = 표(`pub_macro` 대 `prv_macro`), 수집 모듈 분리(D-P5-7), 계약 ⑭, `test_p5_config`(시리즈 tier ↔ 데이터셋) |
| 상단 띠 | 신용스프레드 AA-(817Y002)·신용잔고·예탁금(금투협) | + 한국 대 글로벌 | 공개 칩은 `credit.json`(공개 내보내기) |
| 9 테마·밸류체인 | 분류 사전(테마·단계·멤버·근거·출처·신뢰도)·체인 맵 | + 테마 강도·트리맵(KRX 시세) | `pub_themes` 대 `prv_themes`, 계약 ⑮, 공개 `themes/*.json` 에 시세 칸 0(시험) |
| 10 ETF | — | 전부(ETF 맵·보유 탐색·동시 편입·리포트) | 운용사·KRX 원천 — 로그인 |
| 2 간밤 미국 | — | 전부 | Nasdaq·Yahoo·Stooq 로그인 |
| 1 F2 | — | 전부(장중 수급·프로그램) | KIS — 로그인 빌드 전용, 공개 번들 검사 |
| 5 일정 | 공식 일정·발표 결과(공개 시리즈 값) | + FF 예상치 | 캘린더 행 `source` 별 tier(`pub_macro.calendar` 에는 공개 출처만 — 시험) |
| 텔레그램(브리핑·리포트) | — | 전부(본인 채팅) | 계약 ⑯(발송 코드는 어댑터 없음) |

---

## 3. 데이터 흐름

### 3.1 한눈에

```
[아침]  us.eod(06:10, Nasdaq·Yahoo·Stooq) ─► prv_market.daily_bar(US) ─► kbj.engines.us ─► prv_board.artifact(US: us_summary·us_brief)
        macro.morning(06:20, FRED 정부·재무부·뉴욕연은·FRED 발표일) ─► pub_macro.series·pub_macro.calendar
        macro.morning_prv(06:20, Yahoo 12종·FRED 저작권·FF[꺼짐]) ─► prv_macro.series·prv_macro.calendar
        themes.dictionary(07:30, config/knowledge/* + pub_filings.corp_code) ─► pub_themes.*(충돌 0 일 때만 현재판)
        etf.collect(08:00, P3) ─► prv_etf.*        krx.daily(08:05, P3) ─► prv_market·prv_etf.etf_daily
        brief.morning(08:10) ◄─ 저장된 행만(us_brief·macro·calendar·earnings_schedule·금통위) ─► notifier(brief.morning, dedup daily)
        etf.report(08:20) ◄─ prv_etf.change_log·holding·etf_daily ─► notifier(etf.report)
[장중]  flows.market_minute(1분) ─► prv_gex.investor_flow(유일한 쓰기 주인)    program.intraday(1분) ─► prv_flows.program_market_intraday
        program.stock_scan(10분, +5분 엇갈림) ─► prv_flows.program_stock_intraday
        market_stats.kofia(13:30, D+1)·market.fsc_daily(13:10, D+1, 꺼짐) ─► pub_market_stats·prv_market.daily_bar(fsc)
[마감]  market.close_collect(15:35, P3) ─► board.daily(16:00, P3) ─┬─► brief.closing(16:00, wait_min 20) ─► notifier
                                                                   └─► flows.report(16:00, wait_min 20) ─► notifier(텍스트 + 차트 4장)
        program.daily(15:50) ─► prv_flows.program_market_daily·program_investor_daily
        themes.monitor(16:50) ─► prv_themes.strength      macro.evening(_prv)(17:00·17:05) ─► pub/prv_macro
        program.stock_sweep(18:10, 전 종목, 이어 받기) ─► prv_flows.program_stock_daily
[화면]  API(로그인 — DB 만) · public.export(05:30 — pub_ 만: macro/·themes/·credit.json)
```

### 3.2 작업 표 (P5 에 켜는 것·만드는 것 — 시각 KST)

| 작업 | 시각·트리거 | 의존 | 수집(키) | 쓰는 표 | 발송 | 켬(WRAP) |
|---|---|---|---|---|---|---|
| `us.universe` | 일 04:00 | — | `NASDAQ:screener @run_date` | `prv_market.us_universe` | — | ✓ |
| `us.eod` | 16:10 ET(06:10 KST, 11월~) | 무른 `us.universe` | `NASDAQ:daily`·`YAHOO:us_index_daily @us_trade_date` | `prv_market.daily_bar`·`prv_board.artifact` | — | ✓ |
| `macro.morning` / `_prv` | 06:20 | — | §0.4 | `pub_macro.*` / `prv_macro.*` | — | ✗(#43~#46) |
| `themes.dictionary` | 07:30 | — | — | `pub_themes.*` | — | ✓ |
| `brief.morning` | 08:10(캐치업 09:00) | 무른 `us.eod`·`macro.morning`·`macro.morning_prv`·`gex.night_minutes` | — | — | `brief.morning` | ✓ |
| `etf.report` | 08:20 | 굳은 `etf.collect`, 무른 `krx.daily`(wait 20) | — | — | `etf.report` | ✓ |
| `macro.monthly` / `_prv` | 09:10 / 09:15 | — | ECOS·KOSIS @month·quarter | `pub_macro` / `prv_macro` | — | ✗ |
| `flows.market_minute` | 09:00~15:40 매분 | — | `KIS:market_investor_intraday @minute`(켠 조합) | `prv_gex.investor_flow` | — | ✓(K2I 3조합) |
| `program.intraday` | 09:00~15:40 매분 | — | `KIS:program_market_intraday` | `prv_flows.program_market_intraday` | — | ✗(#40) |
| `program.stock_scan` | 09:05~15:25 10분 | — | `KIS:program_stock_intraday` | `prv_flows.program_stock_intraday` | — | ✗(#41) |
| `market.fsc_daily` | 13:10 | — | `DATAGO:15094808·15094807 @prev_trading_day` | `prv_market.daily_bar`(fsc) | — | ✗(#48) |
| `market_stats.kofia` | 13:30 | — | `DATAGO:15094809/* @prev_trading_day` | `pub_market_stats.*` | — | ✗(#48) |
| `program.daily` | 15:50(close+20) | — | `KIS:program_market_daily`·`program_investor_today` | `prv_flows.program_*_daily` | — | ✗(#40) |
| `brief.closing` | 16:00(캐치업 20:30) | 굳은 `market.close_collect`, 무른 `board.daily`(wait 20) | — | — | `brief.closing` | ✓ |
| `flows.report` | 16:00 | 위와 같음 | — | — | `flows.report` | ✓ |
| `themes.monitor` | 16:50 | 굳은 `market.close_collect` | — | `prv_themes.strength` | — | ✓ |
| `macro.evening` / `_prv` | 17:00 / 17:05 | — | ECOS @trade_date | `pub_macro` / `prv_macro` | — | ✗ |
| `program.stock_sweep` | 18:10 | 굳은 `market.close_collect` | `KIS:program_stock_daily`(종목 단위 — 처리기 claims) | `prv_flows.program_stock_daily` | — | ✗(#41) |

### 3.3 마이그레이션 0014~0018 (DDL 요약 — FND)

| 파일 | 표 | 키·주요 칸 | 비고 |
|---|---|---|---|
| `0014_macro.sql` | `pub_macro.series_meta`·`series`·`calendar`, `prv_macro.series_meta`·`series`·`calendar` | `series`: PK `(series_id, period)`, `value numeric NULL`(없으면 NULL — 0 금지), `unit`, `source`, `as_of timestamptz`, `released_at`, `quality` CHECK, `loaded_by`. `calendar`: PK `event_id`(=`<country>:<kind>:<date>:<code>`), `date`·`time_kst NULL`·`importance`·`actual NULL`·`actual_series NULL`·`forecast NULL`(**prv 에만** — pub 에는 칸이 없다) | 공개 `pub_macro.calendar.source` CHECK 는 공개 출처 목록만(`bok`·`fomc`·`fred`·`kosis`·`ecos`·`manual`) |
| `0015_themes.sql` | `pub_themes.build`·`theme`·`stage`·`membership`·`alias`·`sector`, `prv_themes.strength` | 판(version) 단위: `build(version PK, built_at, input_digest, n_themes, n_members, n_conflicts CHECK (n_conflicts = 0), is_current)` — **충돌 있는 판은 넣을 수 없다**(DB 가 완료 기준을 한 번 더 지킨다). `membership(version, theme_id, code, stage_id, is_primary, source, evidence, confidence, locked)`, 종목당 `is_primary` 한 줄 부분 유일 색인, `sector(version, code PK…, sector, source, why, confidence)`. `strength`: PK `(theme_id, trade_date, period)`, hypertable 365일, `dict_version` | 판은 최근 30개만 남김 [제안] — `ops.nightly` |
| `0016_us_board.sql` | `prv_market.us_universe` + `prv_board.artifact` 의 `name` 제약 확장(`us_summary`·`us_brief`) | `us_universe` PK `(symbol, as_of)`, `exchange`·`sector`·`industry`·`market_cap`·`is_fund`·`source`·`quality` | 제약 이름은 `DO` 블록에서 `pg_constraint` 로 찾아 바꾼다 [확인 필요: 0007 의 자동 이름] |
| `0017_market_stats.sql` | `pub_market_stats.credit_balance`·`market_capital`·`fund_nav`·`cma` | PK `(date, item)`, `value numeric NULL`, `unit`, `source`, `as_of`, `quality` | 칸 이름은 `kbj/data/public/fsc_kofia_stats/datasets.py` 와 맞춘다 [확인 필요] |
| `0018_program.sql` | `prv_flows.program_market_daily`·`program_market_intraday`(hypertable 1일)·`program_investor_daily`·`program_stock_intraday`(hypertable 1일)·`program_stock_daily` + `COMMENT ON TABLE prv_gex.investor_flow`(쓰기 주인 `flows.market_minute` — ADR 0019, 대금 백만원) | F2 §3.3 그대로: 날짜(·시각)·시장(K·Q)·거래소·`kind`(arbt·nabt·whol)·`side`(entm·onsl·smtn)·매도/매수/순매수 × 수량/대금(원)·`source`·`quality`. 종목 표는 `scope`(scan·sweep)·현재가·등락 | UNIQUE 로 같은 분 재실행 멱등 |

### 3.4 KIS 호출량 (초당 4건 — `config/limits.yaml kis.rate`, F2 §3.7 예산)

| 작업 | 호출 수 | 하루 | 비고 |
|---|---|---|---|
| `flows.market_minute` | 켠 조합 수/분 × 401분 | 1,203(K2I 3) → 2,005(+현물 2, #39 뒤) | 위클리·야간은 P7 |
| `program.intraday` | 시장 2 × 거래소 수/분 × 401 | 802(KRX) | 거래소를 늘리면 배 |
| `program.stock_scan` | (`scan_top_n` 30 + 관심종목 ≤ 20) × 38슬롯 | 약 1,900 | P3 슬롯(:00·:10…)과 5분 엇갈림 |
| `program.daily` | 4 | 4(+ 백필 [실측 필요]) | |
| `program.stock_sweep` | 전 종목 약 2,700 × 거래소 수 | 2,700(약 11~12분) | 18:10 — 마감 수집·컨센서스 뒤 |
| P3·P4 기존 | 장중 약 2,300 + 마감 약 5,700 + 컨센서스 약 640 | 약 8,640 | `docs/p3_design.md` §3.7·ADR 0015 |
| **하루 합** | | **약 17,300**(KRX 하나) | `kis.daily_soft_cap` 30,000 [제안] — 등록부 시험이 막는다 |

초 단위: 10분 경계(P3 슬롯 약 61건)와 매분 7건이 겹쳐도 약 17초 — 1분 수집은 그 분 안에 끝난다(시뮬레이션이 단언). 행의 `ts` 는 **슬롯 분**(호출 시각이 아님 — F2 §1.1 '오늘 누적 한 줄'), 슬롯 + 45초를 넘겨 받은 값은 `quality=estimated` + 사유 [제안].

### 3.5 다른 출처 호출량 [제안 — 리미터 값은 FND 가 `limits.yaml` 에, 실측 #43~#48 로 확정]

| 리미터 | 율 | 하루 | 근거 |
|---|---|---|---|
| `fred` | 2/s | 시리즈 약 40 + 발표일 1 + 저작권 약 6 | FRED API 공표 한도 분당 120 [확인 필요] |
| `treasury`·`nyfed` | 1/s | 각 1~3 | — |
| `yahoo` | 1/s | 매크로 12 + 미국 지수 약 6 + 상단 띠 | 약관상 로그인 |
| `nasdaq` | 2/s | 스크리너 1 + 일봉 약 1,700(ET 유니버스 실측 1,719 — `board/us/pipeline.py`:137 주석) | ET `config/us.yaml workers: 4` 보다 낮게 [제안] |
| `stooq` | 1/s | Nasdaq 실패 종목만 | — |
| `ff` | 0.2/s | 1(꺼짐) | — |
| `ecos`·`kosis`·`datago` | P2 값 그대로 | 매크로 약 25·금투협 4·금융위 2 | `limits.yaml` 기존 |

### 3.6 설정 파일(FND — 숫자·목록은 한 곳)

| 파일 | 내용 |
|---|---|
| `config/macro.yaml`(새) | `tabs`(7 — 순서·제목·공개 여부), `series`(id·데이터셋·항목 코드·단위·주기·tier·파생식·브리핑 표 포함 여부), `ff.enabled: false`, `calendar.manual`(FOMC·한국 주요 공표일 — 수기 [확인 필요]) |
| `config/dictionary.yaml`(새) | 출처 우선순위(`overrides > themes_yaml > valuechains > derived`), 축별 `single_stage`(예: `project_chain`·`supply_chain` = true [확인 필요]), 대표 테마 규칙 순서, 판 보존 수, 신뢰도 하한(0.7 — `themes.yaml meta.confidence_field`) |
| `config/briefs.yaml`(새) | 아침·마감 섹션 순서·제목·`omitted` 섹션(P7·P8)·빈 섹션 규칙(SD `SKIP_TITLES` 승계)·'미준비' 문구·표 시리즈 목록·품질 줄 형식 |
| `config/markets.yaml`(추가) | `flows.minute.pairs`(시장·업종 코드 쌍 — 켬/끔·설명), `program.scan_top_n`·`program.sweep_markets`, `reconcile.fsc_*`(대조 허용) |
| `config/limits.yaml`(추가) | §3.5 리미터, `kis.priorities`(분 수집 P2·스캔 P3·스윕 P4), `kis.daily_soft_cap` |
| `config/us.yaml`(`git mv` — US) | board/us 설정 그대로(lookback·유니버스 하한·브리프) |

---

## 4. 엔진 명세

> 순수 계산은 `kbj/engines/<영역>`(계약 ⑨ — 입력 행만으로 결정, 시계·설정은 인자), 실행은 `kbj/services/{collectors,engine}` 또는 `kbj/reports`(처리기). 지표 정의는 부록 A(→ metrics §16~§23).

### 4.1 매크로 — 옮기는 것·버리는 것 (D-P5-5~8)

**bok(제3자 — 코드 없음, 기능만 기준).** 원본 기능은 `docs/inventory.md`:21(대시보드 8탭·아침 브리핑)·117(06:40 발송)·400~402(지표 영역).

| bok 기능 | P5 처리 | kbj 위치 | 등급 |
|---|---|---|---|
| ECOS 8계열(`fetch-ecos.js` — 기준금리·시장금리·환율·물가·통화·국제수지·심리·GDP) | **다시 만든다** — P2 ECOS 정본 + 카탈로그 11표(`817Y002`·`722Y001`·`721Y001`·`404Y014`·`402Y014`·`401Y015`·`161Y005`·`301Y013`·`513Y001`·`200Y102`·`731Y003`) | `collectors.macro` → `pub_macro.series`, 탭 금리·통화/물가/성장·경기/대외·환율 | 공개(한은 작성) |
| ECOS 타기관 표(802Y001 한국거래소·731Y001 서울외국환중개·901Y056 금투협·901Y009) | 다시 만든다(로그인) | `collectors.macro_prv` → `prv_macro` | 로그인(DATA_TIERS §1) |
| KOSIS(경기종합·산업생산·고용·CPI) | 다시 만든다 — 카탈로그 6표 | `collectors.macro` | 공개 |
| FRED 74계열(`fetch-fred.js`) | **정부 시리즈만 공개 데이터셋**(약 40 [제안 목록 — `config/macro.yaml`]: CPI·근원 PCE·실업률·비농업 고용·연방기금·DGS2·DGS10·T10Y2Y·DGS3MO·실질 GDP 등), 저작권 시리즈(S&P500·VIXCLS·ICE BofA 스프레드 등)는 `FRED:restricted_series`(로그인) | `kbj/data/public/fred` / `kbj/data/private/fred_restricted.py` | 공개 / 로그인 |
| 미 재무부 수익률곡선·뉴욕연은 SOFR·EFFR(`fetch-rates.js`) | 다시 만든다 | `kbj/data/public/{treasury,nyfed}` | 공개 [확인 필요 — DATA_TIERS 행 추가] |
| Yahoo(`fetch-yahoo.js` — 달러·원자재·지수) | SD `_MACRO_TICKERS` 12종과 합쳐 하나로(아래) | `kbj/data/private/yahoo`(FND) | 로그인 |
| ForexFactory 컨센서스(`fetch-consensus.js`) | 어댑터만 만들고 **꺼 둔다**(§10 Q3) | `kbj/data/private/forexfactory.py` | 로그인 |
| 통화신용정책보고서 110시트·그림 188장(`data/`) | **옮기지 않는다**(원본 xlsx 출처·구조 미확인 — §10 Q2). 보고서가 인용하는 ECOS 시리즈를 '보고서 지표' 탭 묶음으로 보이고 원문 링크 | `config/macro.yaml` 묶음 | 공개 |
| 수출 104품목(`extract.py`) | **P6**(관세청 — 페이지 7) | — | — |
| 대시보드 8탭 HTML·`template.html`·`usmacro.js` | 버림 — SPA 페이지 6(탭 7 + 캘린더) | `web/src/pages/p6*` | — |
| 아침 매크로 브리핑(`send-telegram.js --morning-only`) | `brief.morning` ②③⑤ 로 흡수(U2) | BRF | 로그인(텔레그램) |
| 발송 기록 `cache/telegram-sent.json` | 버림 — `ops.notify_log` | — | — |

**SD 글로벌매크로**(`SD:server.py:api_macro`:8004·`api_global_macro`:13126 — 30분 파일 캐시).

| SD 항목 | P5 처리 | 비고 |
|---|---|---|
| `_MACRO_TICKERS`:7988 12종(USD/KRW·USD/JPY·EUR/USD·USD/CNY·WTI·금·은·구리·천연가스·美10년 `^TNX`·VIX·BTC) | **옮긴다** — `YAHOO:macro`(로그인) 시리즈 12, 단위 그대로. 美10년은 공개판에서 FRED `DGS10` 이 대신 | 로그인 탭 '시장' |
| 글로벌 지수(KOSPI·KOSDAQ = `data.json` 네이버, S&P·NASDAQ·DOW = yfinance) | 국내 = KRX 지수(P3 `prv_market`), 미국 = `YAHOO:us_index_daily`(US 묶음이 받음) — 네이버 경로 버림 | 상단 띠 '한국 대 글로벌' |
| 채권·금리 + 장단기 금리차 자동 계산 | 옮긴다 — 엔진 `macro.derive.spread`(FRED DGS10−DGS2·DGS10−DGS3MO, 공개) | 공개 |
| 통화·원자재 섹션 | 옮긴다(Yahoo, 로그인) | — |
| 변동성·심리: VIX | 옮긴다(Yahoo, 로그인) | — |
| CNN 공포탐욕·alternative.me(크립토 F&G) 스크랩 | **버린다**(스크랩·약관 미확인) — 국장 공포탐욕은 P7 진단 | — |
| PCR(yfinance 옵션)·코스피200 야간선물 줄 | **버린다 → P7**(GEX·야간 분봉) | — |
| 디지털 자산 BTC | Yahoo 로그인 시리즈로만(화면 '시장' 탭 맨 아래) | — |
| 30분 파일 캐시(`cache/global_macro.json`·`macro_data.json`) | 버린다 — 수집은 등록부, 화면은 API 캐시(P3 `cache.py`) | — |
| SD 4시간 `_refresh_global_data_periodic`:4979·05:00 `tg_briefing_refresh` | 버린다 — `macro.morning(_prv)` 06:20 | 등록부 absorbs 이미 있음 |

**파생 지표(엔진 `kbj/engines/macro/derive.py` — metrics §16)**: 금리차, 신용스프레드(817Y002 회사채 AA-·BBB- − 국고 3년), 전년동월비·전월비·3개월 연율, 실질 정책금리(기준금리 − CPI YoY), 최신값의 변화(전 공표 대비). 입력 시리즈 중 하나라도 `invalid`·없음이면 결과 `None` + 사유(0 으로 채우지 않는다). 공개 파생은 공개 입력만(시험 — tier 섞임 금지).

**캘린더(`kbj/engines/macro/calendar.py`)**: 수기 공식 일정 + FRED 발표일 → 공개 행. '발표 결과' = 같은 시리즈가 그 날짜 이후 공표된 값(`released_at`) — 지어내지 않는다. FF 예상치(로그인)는 `prv_macro.calendar` 에만, 화면에서 공개 행에 덧붙여 보인다(로그인 빌드만).

### 4.2 분류 사전 (D-P5-9~11 — 정의는 metrics §17)

- **입력(전부 `config/knowledge/` — 공개·자체)**: `themes.yaml`(62 — `axes`·`unlisted`·`renames`·`themes[].seeds{단계: [종목명]}`·`krx_hint`·`external_index`·`open_items`), `valuechains.yaml`(SD 5 테마 변환 — 레이어 → 단계, 세그먼트 → 하위 단계·키워드·병목·`us_peers`), `sectors.yaml`·`sector_map.yaml`(board48 1:1), `aliases.yaml`, `overrides.yaml`, `theme_codes.yaml`.
- **빌드 순서**: ① 출처별 정규화(이름 → `norm` — `kbj.engines.board.themes.norm` 을 사전으로 옮기고 board 가 다시 내보냄) ② 별칭으로 정본 테마 id(K1) ③ 종목명 → 코드(`theme_codes.yaml`; 미상장·미해결은 목록으로 — 조용히 버리지 않는다, K2) ④ (테마, 종목)별 단계 모으기 → 우선순위로 해소(K3) ⑤ 고정 포함·제외 적용(K5) ⑥ 대표 테마(K4 — 규칙: overrides `primary` > `themes.yaml` seeds 에 처음 나온 테마(= board `primary` 와 같은 결과) > valuechains > 테마 id 사전순) ⑦ 섹터 1:1(K6) ⑧ 표시 이름 유일(K7) ⑨ 입력 sha256 → `digest`.
- **결정성**: 같은 입력 → 같은 결과·같은 digest(속성 시험 — 입력 순서 섞기). 판 번호 = `digest` 앞 12자 + 날짜.
- **board 와의 관계**: board 의 `themes.build(themes_yaml, universe, cfg)`·`primary` 는 사전 빌드의 '`themes.yaml` 출처만' 부분과 **같은 결과**여야 한다(소비자 일치 시험). board 는 사전 DB 를 읽지 않고 파일 로더를 공유한다(신고가 골든의 입력이 그대로 — D-P5-11).
- **운영**: 새 상장사는 `themes.dictionary` 가 `pub_filings.corp_code` 로 이름 해석을 다시 시도하고, 확정본(`theme_codes.yaml`)과 다르면 경고만(사전은 커밋된 확정본 기준 — 사람이 `resolve --write` 후 커밋). LLM 재분류는 쓰지 않는다(ET `board classify` 는 legacy 에 P8 까지).

### 4.3 테마 강도 (로그인 — metrics §18)

`ET:monitor/kr/engine.py` 함수 본문 그대로(D-P5-12). 묶음 단위가 '섹터' → '테마(사전 판의 멤버)'. 기간 1W·1M·3M·6M·1Y(영업일 5·21·63·126·252 — `kbj.core.calendar`), 등락은 KRX 일봉(`prv_market.daily_bar` — 오늘 행은 KIS 마감 잠정이면 `estimated`), RS = 기간 수익률 백분위 가중(0.2·0.2·0.2·0.4), 테마 rs = 멤버 rs 중앙값, rsTop = rs ≥ 80 비율(%), 폭(breadth) = 상승 멤버 비율, 급증 = 오늘 거래대금 ÷ 20일 중앙값(≥ 2 급증), 낙폭(drawdown) = 52주 고점 대비. 멤버가 5 미만인 테마는 `quality=estimated` + '표본 작음' [확인 필요].

### 4.4 ETF 맵·보유 탐색·리포트 (로그인 — metrics §19)

- **ETF 맵**: 셀 = ETF 하나(크기 = 순자산 `prv_etf.etf_daily` 마지막 확정일, 색 = 기간 수익률 1D·1W·1M·3M — 분할 보정 원장 P3), 묶음 = 유형(metrics §8.1 7종) → 테마(`classify`). 레버리지·인버스는 기본 숨김(토글). 신규 상장(기간보다 짧음)은 회색 + 꼬리표.
- **보유 탐색**: ETF → 구성종목(마지막 PDF 기준일·비중·수량·전일 대비 변동 — P3 `change_log`), 종목 → 담은 ETF(비중 상위·액티브 여부). 운용사 PDF 기준일이 다르면 그대로 보인다(섞지 않는다).
- **액티브 동시 편입**: 같은 날 2개 이상 액티브 ETF 가 새로 담았거나(NEW·IN10) 늘린(ADD) 종목 — `report.py:_consensus` 정의 그대로.
- **리포트(`etf.report` 08:20)**: ET `report.build` 섹션 그대로(머리·신규 편입·비중 변화·동시 편입·편출·움직임·기간·자금 흐름·기타). 기준일 = 직전 완료 영업일(ET `market.py` 머리말 원칙 — 장중 값을 그날 수치로 쓰지 않는다). 자금 흐름은 P3 metrics §4(순유입 = 좌수 변화 × NAV).

### 4.5 브리핑 (로그인 — metrics §20)

| 섹션 | 아침(08:10) | 마감(16:00) | 재료(저장소) | 없으면 |
|---|---|---|---|---|
| ① | 간밤 미국 — 지수·섹터·메가 무버·신고가·등락 온도(US `us_brief` 산출 텍스트) | 지수·환율·시장 수급(코스피·코스닥 투자자 — **기준일·원천·quality**) | `prv_board.artifact(US)` / `prv_market`·`prv_flows.market_investor_daily` | '미준비 — us.eod(06:10)' / 굳은 의존이라 작업 실패 |
| ② | 금리·달러·원자재 한 표(FRED·재무부·뉴욕연은 공개 + Yahoo 로그인 — 중복 제거: 美10년은 DGS10 하나) | 업종 상위 5·하위 3·특징주 | `pub_macro`·`prv_macro` / `prv_market.sector_*`·보드 | 칸마다 '—' + 품질 줄에 사유 |
| ③ | 한국 매크로 — 전일 08:10 이후 새로 공표된 시리즈만 | 신고가(board 3축 랭킹 — 120일·52주·역사적) | `pub_macro.series.released_at` / `prv_board.artifact(KR)` | 섹션 생략(새 공표 없음) / '미준비 — board.daily(16:00~16:05)' |
| ④ | 야간선물·GEX — **P7 전 생략** | 수급 동향(외국인·기관 순매수 상위·연속) | — / `prv_flows.stock_investor_daily` | — |
| ⑤ | 오늘·이번 주 일정 + 발표 결과, 오늘 실적 일정, 금통위 D-n | 주요 공시(중요도 ≤ 2 — P4) | `pub_macro.calendar`·`pub_filings.earnings_schedule`·`calendar_events.yaml` / `pub_filings.disclosure` | '일정 없음' |
| ⑥ | 구루 — **삭제**(§10 Q4) | GEX·시그널·초안·추천 — **P7·P8 생략** | — | — |
| ⑦ | 데이터 품질 줄(재료별 as_of·quality·미준비 목록) | 같음 | 위 전부 | — |

- **하루 1회**: 발송은 notifier outbox(ADR 0005) — `notify(kind, subject=<기준일>)`, 정책 `dedup: daily`. 같은 날 두 번째 접수는 `duplicate`(작업 성공으로 기록 — 재시도 금지), 캐치업은 아직 안 보낸 날만(등록부 `catch_up_until`).
- **수급 리포트(`flows.report`)**: ET monitor/flow 규칙 그대로 — 대상 = 보드 `w52`(3축의 52주 — 역사적 포함) ∧ 그날 거래량 ≥ 20일 평균 × `vol_mult`(기본 문턱 없음 — 원본 `test_기본은_문턱이_없다`), 많이 는 순서 5, 1개월 투자자별 수급 차트 4장(주체 색 고정·캔들 오르면 빨강) + 표(코드블록), 기준일이 다르면 그 줄, 대상 없는 날은 한 줄. 16:00 수급은 잠정일 수 있고 당일 행이 없으면 전 거래일 — 본문이 기준일을 말한다(ADR 0018 §2-5).
- **LLM 없음**: 문장은 템플릿. 수치는 저장소 값 그대로(반올림은 렌더에서만 — metrics §0).

### 4.6 간밤 미국 신고가·금융위 일별 (로그인 — metrics §23)

- `kbj/engines/us` = `ET:board/us` 엔진 본문 그대로: `hits_on`(종가·고가 기준, 252봉 lookback — `config/us.yaml`), `streak_and_fresh`, `evaluate_all`, `sector_table`·`industry_groups`·`mega_movers`·`leaders`·`streaks`·`fresh52`·`summary`, 브리프 문장(`brief.py` — 퍼센트·금액·요일 라벨). 이력이 252봉 미만인 종목은 보류(숨기지 않고 수를 적는다 — `pipeline.daily` 머리말).
- 일봉 출처 순서: Nasdaq history → (실패) Stooq → (지수) Yahoo. 행 `source` 에 실제 출처, 수정주가 여부 `adjusted`(`adjust_probe`).
- `market.fsc_daily`: 금융위 주식·지수 일별(D+1 13시 개방) → `daily_bar(source='fsc')`, 같은 날 KRX 행과 종가 비교 → 불일치 비율 경고(health). 화면·브리핑 미사용.

### 4.7 장중 투자자 수급·프로그램매매 (F2 — metrics §21·§22)

F2 §0·§1·§4 그대로. 이 설계가 더하는 것만: 12구분 → 7카드 매핑(개인 `prsn` · 외국인 `frgn` · 기관 합계 `orgn` · 금융투자 `scrt` · 투신 `ivtr`(**사모 `pe_fund` 제외** — 기관 세부 선에서 따로) · 연기금 `fund` · 기타법인 `etc_corp`), 검산 ①(현물만: `prsn + frgn + orgn + etc_corp + etc_orgt` 순매수 대금 ≈ 0 — 허용 오차 [확인 필요: 백만원 반올림 × 구분 수]), ②(`orgn = scrt + ivtr + pe_fund + bank + insu + mrbn + fund` — `etc_orgt` 를 기관에 넣는지 [실측 필요 #39]), ③(마감 뒤 마지막 1분 값 대 `market_investor_daily` — 단위 환산 뒤 비교, 차이는 기록·quality), ④(전체 = 차익 + 비차익, 위탁 + 자기 = 합계), ⑤(순위 범위 꼬리표). 빈 분은 행이 없다(보간 금지) — API 가 분 축의 빈칸을 `null` 로 보낸다.

---

## 5. API (로그인 — DB·Redis 만 읽는다, 계약 ⑩)

상태 규칙은 P4 §5.2 그대로: `200` + 봉투(`source`·`as_of`·`quality`), 적용 불가는 `200` + `na_reason`, 아직 수집 안 됨은 `404 no_data`(작업 이름·예정 시각 — 꺼진 작업이면 "확인 전(#NN)"), 모르는 코드는 `404 unknown_code`. **500 은 언제나 실패.** 모델·라우트는 FND(웨이브 1), 리더 본문은 표의 묶음.

| 경로 | 쿼리 | 응답 모델 | 리더(묶음) | 비고 |
|---|---|---|---|---|
| `GET /api/macro/tabs` | — | `MacroTabs`(탭·시리즈 메타·tier) | `readers/macro.py`(MAC) | 공개판은 정적 JSON |
| `GET /api/macro/series` | `tab`, `from` | `MacroSeriesSet`(시리즈별 점·최신값·변화·파생) | MAC | 로그인 탭 포함 |
| `GET /api/macro/series/{series_id}` | `from`, `to` | `MacroSeries` | MAC | 모르는 id → 404 |
| `GET /api/macro/calendar` | `from`, `to` | `EconCalendar`(행 + 발표 결과·예상치(로그인)) | MAC | |
| `GET /api/market/ribbon`(P3) | — | `Ribbon`(칩 셋 값 채움) | `readers/market.py` 띠 부분(MAC) | |
| `GET /api/themes` | `period`, `sort` | `ThemeList`(사전 판·테마·멤버 수·강도) | `readers/themes.py`(THM) | 강도 없으면 사전만 + `na_reason` |
| `GET /api/themes/{theme_id}` | `period` | `ThemeDetail`(단계·멤버·근거·출처·신뢰도·강도·체인 맵) | THM | |
| `GET /api/themes/stock/{code}` | — | `StockThemes`(테마 집합·대표 테마·섹터) | THM | 페이지 8 칩(WRAP 고리) |
| `GET /api/themes/build` | — | `DictBuildInfo`(판·digest·충돌 수 — 언제나 0) | THM | 운영 확인용 |
| `GET /api/etf/map` | `period`(1D·1W·1M·3M), `group`(type·theme), `lev`(bool) | `EtfMap` | `readers/etf_p5.py`(ETF) | |
| `GET /api/etf/fund/{code}/holdings` | — | `EtfHoldings` | ETF | |
| `GET /api/etf/holders/{code}` | — | `EtfHolders` | ETF | 페이지 8 '담은 ETF'(WRAP 고리) |
| `GET /api/etf/active-consensus` | `days` | `ActiveConsensus` | ETF | |
| `GET /api/board/us` | `basis` | `UsBoard`(요약·신고가·섹터·메가 무버·보류 수·미국 거래일) | `readers/board_us.py`(US) | |
| `GET /api/flows/market-minute` | `market`, `venue` | `MarketMinute`(분 축 — 빈 분 `null`·카드 7·quality·수집 시각·`venue_locked`) | `readers/flows_minute.py`(F2) | F2 §3.5 |
| `GET /api/program/daily` | `market`, `days`(≤ 60) | `ProgramDaily` | `readers/program.py`(F2) | |
| `GET /api/program/top` | `market`, `side`(buy·sell), `scope`(intraday·close) | `ProgramTop`(행 + **`scope_label`**: '거래대금 상위 N + 관심종목' / '전 종목(마감 확정)') | F2 | 범위 꼬리표 필수(F2 §1.4) |

`openapi.json`·`types.gen.ts` 재생성(`--check` 녹색). 웹 픽스처는 `tests/unit/api/web_fixtures_p5/<도메인>.py` → `web/test/fixtures/api/<도메인>/**`.

---

## 6. 프런트

| 페이지 | 만드는 것 | 묶음 | 상태 표시 |
|---|---|---|---|
| 1 시장 | F2 §0 위젯 셋(시장 탭·카드 7·선 토글·끊긴 선·끝점 이름표·0 기준선·`수집 HH:MM · 60초 갱신 · 잠정`·거래소 표시(분 선은 `KRX` 잠금)·프로그램 60일 막대·상위 20 표(범위 꼬리표)) — 60초 폴링, 탭·토글은 URL 쿼리 | F2 | 데이터 없음·실측 전(꺼진 조합 — '확인 전')·장 시작 전·휴장일 각각 다르게. 공개판 자물쇠 |
| 2 신고가 보드 | '간밤 미국' 패널(요약 줄·신고가 표·섹터·메가 무버·보류 수) | US | '미준비 — us.eod 06:10' |
| 5 공시·일정 | 경제 캘린더 패널(오늘·이번 주, 발표 결과, 중요도, 출처) — 공개판도 | MAC | 예상치는 로그인만 |
| 6 매크로 | 탭 7(금리·통화 / 물가 / 성장·경기 / 고용·가계 / 대외·환율 / 미국 / 시장 — 마지막은 로그인) + '보고서 지표' 묶음·카드(최신값·변화·as_of·출처·quality)·선 차트(`ui/line`) | MAC | 확인 전 시리즈는 '확인 전(#43)' — 값 지어내지 않음 |
| 9 테마·밸류체인 | 테마 목록(강도 정렬 — 로그인)·체인 맵(단계 열 × 종목, 병목·확산)·테마 상세(멤버·근거·출처·신뢰도)·트리맵(`ui/treemap` — 로그인)·판 정보(충돌 0) | THM | 공개판은 사전만, 강도 자리는 자물쇠 |
| 10 ETF | ETF 맵(`ui/treemap`)·보유 탐색(검색)·액티브 동시 편입 | ETF | P3 패널 그대로 |
| 8 종목 상세 | 테마 칩(THM 모듈)·담은 ETF(ETF 모듈) — 고리는 WRAP | THM·ETF·WRAP | 섹션 격리(P4) |
| 상단 띠 | 신용스프레드·신용잔고(공개)·한국 대 글로벌(로그인) 칩 | MAC | P3 '준비 중' → 값 |

시험(vitest + jsdom): 각 묶음 `web/test/pages/<페이지>_*.test.ts`, 공용 부품 `web/test/ui_p5.test.ts`(FND), 완료 `web/test/acceptance/p5_pages.test.ts`(WRAP — 모바일 폭 가로 넘침 0 포함). 공개 번들에 `/api`·로그인 위젯 코드 0(`scripts/check-bundle.mjs`).

---

## 7. 공개 정적 사이트 내보내기 영향

| 파일 | 원천(pub_ 만) | 크기 상한 [제안] | 묶음 |
|---|---|---|---|
| `macro/tabs.json`·`macro/<tab>.json` | `pub_macro.series_meta`·`series`(공개 시리즈 — 월·분기 10년, 일별 2년) | 합계 3 MB | MAC |
| `macro/calendar.json` | `pub_macro.calendar`(오늘 −7일 ~ +30일) | 0.2 MB | MAC |
| `credit.json` | `pub_market_stats.credit_balance`·`market_capital`, `pub_macro`(817Y002 스프레드) | 0.1 MB | MAC |
| `themes/index.json`·`themes/<theme_id>.json` | `pub_themes.*` 현재판(시세·강도 칸 없음) | 합계 2 MB | THM |

공개 안전: `scripts/check_public_safety.py` 레포·산출물 0건, `tests/unit/public_export/test_sql_schemas.py`(SQL 스키마 `pub_` 만), 로그인 출처 이름(Yahoo·KIS·KRX·Nasdaq·운용사) 0, 관심종목 흔적 0. 계약 ⑧ 그대로(`kbj.services.public_export` 는 로그인 코드를 모른다).

---

## 8. 시험·검증

### 8.1 완료 시험 (WRAP)

| 시험 | 단언 |
|---|---|
| `tests/acceptance/test_theme_conflicts.py` | §0.3 ①~④ — **실사전** 미해소 충돌 0, 섹터 1:1, 소비자 일치(board·페이지 9 리더·브리핑·종목 상세), 심은 충돌 7종이 하나씩 잡힘 |
| `tests/sim/test_one_day_p5.py` | §1.8 3 — 데이터 키 각 1·KIS ≤ 4/s·발급 auth 만·1분 빈 분 0·브리핑 각 1통·`etf.report` 1통·16:00 발송이 보드 뒤·`program.stock_sweep` 18:10·공개 산출물·로그인 이름 0 |
| `tests/sim/test_brief_once.py` | D-P5-17 ④ 변형 넷(재기동·캐치업·legacy shim 이중 접수·휴장 2종) |
| `tests/sim/test_intraday_minute.py` | F2: 정상 = 391분 × 켠 조합 빈 분 0, 실패(429·타임아웃 — 10:20·10:21) = 빈 분이 정확히 그 분, 장전·휴장 = 0행 |
| `web/test/acceptance/p5_pages.test.ts` | 페이지 1·2·5·6·9·10 패널 — `console.error` 0·DOM 의 `NaN`·`undefined`·`Infinity` 0·공개 빌드는 공개 패널만·390px 가로 넘침 0 |

### 8.2 파서·수집

합성 응답(실제 칸 이름, 가짜 값 — 로그인 출처) / 공개 출처는 키 수령 뒤 실응답 픽스처 커밋 가능(FRED 정부·ECOS 한은·KOSIS·금투협). 공표 지연(키 미완료 → 다음 실행), 부분 실패 격리(한 시리즈·한 운용사·한 종목 실패가 다른 것을 멈추지 않음), 429·타임아웃·빈 응답, 단위 변환(F2 백만원 → 원은 API 에서), 시계 주입.

### 8.3 엔진

- 손계산 표(매크로 파생·테마 강도·ETF 맵 셀·프로그램 검산), 속성 시험(사전 결정성·대표 테마 유일·검산 ①~④ 합성 원장 0 차이 — `tests/property/test_{dictionary_props,program_checks}.py`).
- **골든**: 신고가 골든(`tests/golden/test_board_golden.py`) **그대로 녹색**(사전 로더 공유가 board 산출을 바꾸지 않음), SD `valuechain_map.json` 왕복 골든(변환 전 원본을 THM 이 먼저 캡처 — `valuechain_map_v2()` == 원본, P3·P4 '먼저 캡처' 관례).
- 승격 시험은 import 경로만 바꾼다(§1.12).

### 8.4 하루 운영 시뮬레이션 (WRAP — `tests/sim`)

`tests/sim/harness.py` 에 `p5` 플래그(합성 세계 `p5_world` 를 저장소에 심고, 켜진·사본에서 켠 P5 작업을 실제 처리기로, 새 출처는 가짜 서버로). 창 2026-11-17(화) 05:00 ~ 11-18 05:00. 기존 P2·P3·P4 시뮬레이션은 그대로 녹색(등록부 변경이 그 단언을 깨지 않는다 — `gex.poller` collects 이동은 P2 시뮬레이션의 데이터 키 수를 바꾸므로 기대값을 같은 커밋에서 고친다 [확인 필요: `tests/sim/test_one_day.py` 의 키 수 상수]).

### 8.5 API·공개 내보내기

라우트 시험(묶음별), `test_routes_p5_shape.py`(FND — 빈 리더 404), `openapi --check`, 공개 JSON 모양·크기·공개 안전.

### 8.6 웹

§6 시험 + `npm run build:public && npm run build:login && node scripts/check-bundle.mjs`(WRAP).

### 8.7 CI 잡 (`.github/workflows/ci.yml` — WRAP)

`test (kbj)` 에 `fonts-nanum`(차트 시험), `sim` 잡은 `tests/sim -m "sim and not slow"` 그대로(새 파일 자동 포함), `web` 잡 그대로, legacy 매트릭스에서 `flow` 부분 제거·`kr`·`board`·`etf_tracker_v9` 기준 수 갱신. 도커 통합 잡은 0014~0018 적용·P5 저장소 Pg 시험을 자동으로 모은다.

### 8.8 실데이터 검증 (키를 받은 뒤 — `docs/probe_results.md` §7 #39~#48)

부록 B. 실측이 끝난 항목만 WRAP 뒤 메인이 켠다(`enabled: true` + `ENABLED_BY_PHASE` 갱신은 별도 커밋). F2 실데이터 검산은 `python -m kbj.services.engine.verify --program --date <D>`(건수만 출력 — 값·종목코드 없음, F2 묶음이 `verify.py` 에 하위 명령을 더한다 [제안 — 파일은 P3 것이라 F2 가 고친다: §1.10 F2 줄에 포함]).

---

## 9. 위험·미정

| # | 위험 | 영향 | 대응 |
|---|---|---|---|
| R1 | FND 범위가 커서 웨이브 1 이 늦다 | 웨이브 2 전체 지연 | FND 안을 백엔드(마이그레이션·저장소·카탈로그·설정·API·합성 세계)와 웹(셸·부품·정적 읽기·생성) 두 에이전트로 — 파일이 겹치지 않는다. API 모델은 §5 표를 그대로 |
| R2 | 웨이브 2 중 API 모델 변경 | 다른 묶음 타입 흔들림 | 자기 모델 파일만·생성 명령만(D-P5-4). 다른 도메인 모델 변경은 요청 |
| R3 | 차트 시험이 폰트 없는 환경에서 실패 | BRF·CI | `fonts-nanum`(Docker·CI). 건너뛰기로 바꾸지 않는다 |
| R4 | 사전 로더 공유가 board 테마 매핑을 바꿈 | 신고가 골든 깨짐 | 골든이 지킨다 — 깨지면 로더를 고친다(사전 정의를 board 에 맞춘다) |
| R5 | F2 매분 호출 + P3 10분 슬롯 + 스캔이 같은 KIS 버킷 | 1분 수집 지연 | 스캔 5분 엇갈림, 우선순위(분 P2·스캔 P3·스윕 P4), 슬롯 분 `ts`, 45초 넘으면 `estimated` |
| R6 | legacy GX poller 를 VM 에서 같이 돌리면 같은 TR 두 곳 | 호출 낭비·행 경합 | ADR 0019 운영 조건. P7 이 poller 의 투자자 폴링을 지운다 |
| R7 | 실측 전이라 `macro.*`·`program.*` 가 꺼진 채 끝남 | 페이지 6·1 일부가 '확인 전' | 완료 기준은 합성으로 증명(P4 컨센서스와 같은 관례), 체크리스트 #39~#48 |
| R8 | `theme_codes.yaml` 의 이름이 DART 회사명과 다름(약칭·영문) | K2 미해결 증가 | `themes.yaml renames` + `overrides` — 미해결은 목록(충돌 아님), 비율 경고 |
| R9 | Nasdaq·Stooq·Yahoo 약관 | 운영 위험 | 로그인(본인만), §10 Q6 |
| R10 | FRED 시리즈 저작권 오분류 | 공개판에 로그인 값 | 시리즈 목록 `tier` + 시험(데이터셋 tier 대조), 애매하면 로그인(DATA_TIERS 원칙) |
| R11 | legacy shim 순환 import(ET board → kbj → …) | legacy 시험 실패 | P3 E1 shim 방식(kbj 는 legacy 를 모른다 — 계약 ②) |
| R12 | 브리핑 본문 4,096자 초과 | 여러 통처럼 보임 | notifier 가 같은 dedup 키로 나눔 — '1회' = 접수 1건(D-P5-17 ③) |
| R13 | flows.report 대상의 'd60' → 'd120' | 원본과 의미 차이 | ADR 0017 이 이미 바꾼 정의 — 시험 수 유지, MIGRATION 에 적음 |
| R14 | ETF 분류 두 번째 사본(`monitor/kr/etf.py`)이 legacy 에 남음 | 정본 혼동 | 실행되지 않는 legacy — P9 에 지움, `MIGRATION.md` 에 표시 |
| R15 | F2 1분 작업의 운영 기록 행(`ops.job_run` 약 800/일·`ops.data_claim` 약 2,800/일) | 표 증가 | `ops.nightly` 보존 기간(분 단위 작업 7일 [제안]) — WRAP 확인 |
| R16 | 등록부 `gex.poller` 변경 — 시뮬레이션의 GX poller 흉내(`tests/sim/harness.py:_gx_poll`:1137 이 10분마다 `KIS:market_investor_intraday` 키를 `gex.poller` 로 선점)와 P2·P3 시뮬레이션의 키 수 상수가 바뀜 | 기존 sim 실패·F2 작업과 선점 다툼 | **FND 가 같은 커밋에서** `_gx_poll` 의 투자자별 줄을 빼고 상수를 고친다(웨이브 1 — `tests/sim` 의 이 두 곳만. F2 는 웨이브 2 에 `KIS_SPECS` 줄만, WRAP 은 웨이브 3) |

### 9.1 P3·P4 에서 기대는 것 — 실제 코드 확인(2026-10-13, HEAD `3cb2d5a`)

| 기대 | 확인 |
|---|---|
| 마지막 마이그레이션 `0013_journal_watchlist.sql` | ✓ — P5 는 0014~ |
| `pub_macro`·`pub_themes`·`prv_themes`·`prv_macro`·`pub_market_stats` 스키마는 있고 표는 없음 | ✓ `0001_schemas.sql`:15~32 |
| `prv_gex.investor_flow`(UNIQUE `(ts, market_code, sector_code, investor)`, 대금 백만원) + 뷰 `prv_flows.market_investor_intraday` | ✓ `0006_gex.sql`:160·471 |
| `prv_market.daily_bar` 가 `market='US'`·`source` 키를 받음 | ✓ `0003_market_flows.sql`:49 |
| `prv_board.artifact` 의 `name` 은 5개로 제한 | ✓ `0007_board.sql`:117 — 0016 이 넓힌다 |
| 카탈로그 PLANNED 에 NASDAQ·YAHOO·FRED·TREASURY·NYFED·FF·ANTHROPIC | ✓ `kbj/data/catalog.py`:77 |
| 시뮬레이션 `OFFLINE_SOURCES` 가 위 출처를 오프라인 처리 | ✓ `tests/sim/harness.py`:198 |
| 등록부 P5 작업 15개(owner 처리기 없음) | ✓ `config/jobs.yaml` — `us.universe`·`us.eod`·`macro.*` 6·`brief.morning`·`market.fsc_daily`·`market_stats.kofia`·`brief.closing`·`flows.report`·`themes.monitor` |
| 무른 의존 `wait_min`(ADR 0018) | ✓ `kbj/services/scheduler/registry.py`:251~268 |
| notify 종류 `brief.morning`·`brief.closing`·`flows.report`·`etf.report`(dedup daily) | ✓ `config/notify.yaml`:23~39 |
| 처리기 알림 자원 `ctx.resources["notify"]`(`notify`·`notify_media`) | ✓ `kbj/services/engine/earnings.py`:557, `kbj/services/notifier/client.py`:189·270 |
| ETF 분류 정본 `kbj/engines/etf/types.py`(ET themes 승격) | ✓ |
| board 테마 매핑 `kbj/engines/board/themes.py`·로더 `config.py:load_themes`:226 | ✓ |
| 상단 띠 P5 칩 자리 셋 | ✓ `web/src/app/ribbon.ts`:28·29·37 |
| 공개 내보내기 P5 자리 | ✓ `kbj/services/public_export/export.py`:367 |
| `matplotlib` 은 legacy 그룹, CI legacy 잡에 `fonts-nanum` | ✓ `pyproject.toml`:69, `.github/workflows/ci.yml`:156 |
| 실측 체크리스트 마지막 번호 #38 | ✓ `docs/probe_results.md`:390 — P5 는 #39~ |
| ADR 마지막 0018 | ✓ — P5 는 0019~0024 |
| import-linter 13 kept | ✓ `pyproject.toml` ①~⑬ — P5 는 ⑭~⑯ |

### 9.2 이 설계로 남길 ADR (초안 → WRAP 이 확정)

| ADR | 내용 | 초안 |
|---|---|---|
| 0019 | 장중 투자자 수급의 쓰기 주인 하나(`flows.market_minute` — 등록부 이동·P7 겹침·GX poller 운영 조건·대금 백만원 유지)·프로그램 상위 표의 범위 표시 | F2 |
| 0020 | 분류 사전 통합(입력 파일·우선순위·충돌 K1~K7·판·board 로더 공유·monitor/kr·네이버 제외) | THM |
| 0021 | 매크로 출처·등급(데이터셋 단위 tier·수집 모듈 분리·bok 재작성 범위·SD 글로벌매크로에서 버린 것·FF·캘린더) | MAC |
| 0022 | 브리핑 통합(아침·마감 섹션·미준비·생략·LLM 없음·하루 1회 증명·flows.report 승격·matplotlib) | BRF |
| 0023 | 미국장(board/us 승격·252봉 유지·Nasdaq·Stooq·Yahoo 로그인·US 산출 저장) + 금융위 일별 대조 | US |
| 0024 | ETF 맵·보유 탐색·리포트(분류 정본 하나·리포트 08:20·SD ETF 맵 폐기) | ETF |

---

## 10. 사용자만 할 수 있는 것

| # | 질문·작업 | 기본값(답이 없을 때) |
|---|---|---|
| Q1 | monitor/kr 의 테마 198·체인 23 원본(제3자 `rsm0kk/kr-sector` 배포본 추출)을 **로그인 참고층**으로라도 넣을지 — 넣으면 원본 파일을 VM 에 직접 올려야 한다(레포에 넣지 않음) | 넣지 않는다(U3 — 자체 사전만) |
| Q2 | 한은 통화신용정책보고서 원본(110시트 xlsx)을 파싱해 '보고서 지표'로 넣을지 — 넣는다면 원본 파일 출처 확인 | ECOS 시리즈 + 원문 링크 |
| Q3 | ForexFactory 비공식 피드의 경제지표 **예상치**를 로그인 화면·브리핑에 쓸지(약관 미확인) | 꺼 둔다(발표 결과만) |
| Q4 | 구루 X 다이제스트(ET xdigest — Anthropic 키·X 원천)를 아침 브리핑 ⑥ 에 P5 부터 넣을지 | **답(2026-10-13): 빼기** — P5·P8 어디에도 넣지 않는다 |
| Q5 | ETF 리포트를 아침 브리핑과 따로 08:20 한 통으로 둘지, 브리핑 안의 한 섹션으로 합칠지 | 따로(`docs/inventory.md`:184) |
| Q6 | Nasdaq·Stooq·Yahoo 약관 확인 전에 간밤 미국 수집을 켤지(본인 화면·텔레그램만) | 켠다(ET us-board 운영과 같음) |
| Q7 | F2 '투신' 카드에 사모를 넣을지(KRX 표기는 따로) | 넣지 않는다(사모는 세부 선) |
| Q8 | 미 재무부·뉴욕연은 자료를 **공개** 등급으로 확정할지(DATA_TIERS §1 에 행 추가) | 공개(미국 정부 공표 자료) [확인 필요] |
| — | 키 발급·실측(#39~#48), 운영 VM 의 `matplotlib`·`fonts-nanum` 이미지 갱신, legacy GX poller 를 VM 에서 끄기(또는 P7 까지 돌리지 않기), Pages·VM 전환(P3·P4 에서 남은 것) | — |

---

## 부록 A. `docs/metrics.md` 에 넣을 지표 정의 (FND 가 §16~§23 으로 옮긴다 — 공식·입력·단위·예외·품질·시험)

### §16 매크로 파생 (공개 입력 → 공개, 로그인 입력이 하나라도 있으면 로그인)

| 지표 | 공식 | 입력·단위 | 예외·품질 | 시험 |
|---|---|---|---|---|
| 장단기 금리차 | `DGS10 − DGS2`, `DGS10 − DGS3MO`(%p) | FRED 정부(공개), 같은 날짜끼리만 | 한쪽 없음 → `None` + 사유, 날짜 다르면 계산 안 함 | 손계산 3일 |
| 신용스프레드 | `회사채 AA-(3년) − 국고 3년`, `BBB-` 도(%p) | ECOS 817Y002 [항목 코드 실측 #43] | 공표 지연 → 전일 값 `stale` | 손계산 |
| 전년동월비·전월비 | `(x_t / x_{t−12} − 1) × 100`, `(x_t / x_{t−1} − 1) × 100` | 지수형 시리즈(CPI·PPI·수출입물가) | 기준 연도 바뀜(2020=100 → 다음) 은 같은 기준끼리만 [확인 필요] | 경계(1월) |
| 3개월 연율 | `((x_t / x_{t−3})^4 − 1) × 100` | 계절조정 시리즈만 | 원계열이면 계산 안 함 | — |
| 실질 정책금리 | `기준금리(722Y001) − CPI YoY(KOSIS DT_1J22003)` | 같은 달 | — | — |
| 최신값 변화 | 직전 공표 대비 차·변화율 | `released_at` 순서 | 개정(revision) 값은 덮어쓰고 이전 값 보존 [확인 필요] | — |

### §17 분류 사전 — 충돌 정의와 해소 규칙 (공개, PLAN P5 완료 기준)

- **단위**: 테마 `theme_id`, 단계 `stage_id`(테마 안), 종목 `code`(상장 보통주 — 우선주는 보통주 코드로 모으지 않는다 [확인 필요]), 매핑 = `(theme_id, code, stage_id?, source, evidence, confidence, locked)`. 다대다 — 한 종목이 여러 테마·여러 단계에 속하는 것은 **충돌이 아니다**.
- **출처 우선순위**: `overrides`(사람 — `locked`) > `themes.yaml`(board 자체) > `valuechains.yaml`(SD 자체) > 파생(별칭 전파). 섹터 축(board48)은 테마와 별개 — `sector_map.yaml` 만.

| 충돌 | 정의 | 해소 규칙(해소되면 충돌 아님) | 남으면 |
|---|---|---|---|
| K1 별칭 | 출처 테마가 정본 `theme_id` 로 이어지지 않음(별칭 없음) 또는 둘 이상으로 이어짐 | 없음 — `aliases.yaml` 을 고친다 | 충돌 |
| K2 이름→코드 | seeds 종목명이 상장 코드 둘 이상으로 풀림(같은 이름·우선주·변경 전 이름) | `renames`·`overrides` 로 하나를 고정 | 충돌. 0개(비상장·미해결)는 충돌이 아니라 **미해결 목록**(수·이름을 보인다) |
| K3 단계 | `single_stage: true` 축의 테마에서 한 종목에 둘 이상의 단계가 서로 다른 출처로 붙음 | 우선순위가 높은 출처의 단계, 낮은 쪽은 `evidence` 로만. 같은 우선순위에서 다르면 미해소 | 충돌 |
| K4 대표 테마 | 종목의 대표 테마가 하나로 정해지지 않음 | 규칙 순서: `overrides.primary` > `themes.yaml` seeds 에 처음 나온 테마(board `primary` 와 같음) > `valuechains` 첫 세그먼트 > `theme_id` 사전순 — 결정적이라 `overrides.primary` 가 둘일 때만 남는다 | 충돌 |
| K5 포함·제외 | `overrides` 의 제외와 다른 출처의 포함이 겹침 | 제외가 이긴다. `locked` 포함과 `locked` 제외가 함께면 미해소 | 충돌 |
| K6 섹터 1:1 | 상장 보통주가 board48 섹터 0개 또는 2개 이상 | 없음 — `sector_map.yaml` 을 고친다(`locked` 우선) | 충돌 |
| K7 표시 이름 | 서로 다른 `theme_id` 가 같은 표시 이름 | 없음 — 이름을 바꾼다 | 충돌 |

- **완료 기준 "한 종목의 테마 충돌 0"** = 현재판 빌드에서 K1~K7 미해소 0(종목 단위로 세어 모든 종목 0) + 소비자 일치(board·페이지 9·브리핑·종목 상세가 같은 테마 집합·대표 테마). DB 제약 `pub_themes.build.n_conflicts = 0`.
- 품질: 판 `ok`, 미해결 이름 비율 ≥ 5% [제안] 이면 `estimated` + 경고. 신뢰도 < 0.7 매핑은 브리핑 서술에서 뺀다(`themes.yaml meta`).
- 시험: 실사전 0(완료 시험), 심은 충돌 7종 각 1, 결정성(입력 순서 섞기), 소비자 일치.

### §18 테마 강도 (로그인 — 원천 KRX·KIS 시세)

ET monitor/kr 의 확정 식(`monitor/kr/README.md` '무엇을 되살렸나'): 기간 수익률(영업일 5·21·63·126·252), 종목 RS = `0.2·P(1M) + 0.2·P(3M) + 0.2·P(6M) + 0.4·P(1Y)`(P = 유니버스 안 백분위), 테마 rs = 멤버 rs **중앙값**, rsTop = rs ≥ 80 멤버 비율(%), 폭 = 상승·하락·보합 수와 비율, vsKospi·vsKosdaq = 테마 평균 − 지수, 급증 배수 = 오늘 거래대금 ÷ 20일 **중앙값**(≥ 2 급증), 낙폭 = 52주 고점 대비. 예외: 기간보다 짧은 이력(신규상장)은 그 기간 제외, 거래정지 행 제외, 멤버 < 5 `estimated`. 품질: 오늘 행이 KIS 마감 잠정이면 `estimated`, 다음 날 KRX 확정 뒤 `ok`. 시험: 승격 28 + 테마 묶음 손계산.

### §19 ETF 맵·보유 탐색·동시 편입 (로그인)

맵 셀 크기 = 순자산(원 — `prv_etf.etf_daily` 마지막 확정일), 색 = 기간 수익률(분할 보정 종가 — metrics §8.2), 묶음 = 유형(§8.1) → 테마(`classify`). 보유 = 마지막 PDF 기준일의 비중·수량, 변동 = P3 `change_log` 종류(NEW·DROP·IN10·OUT10·ADD·CUT). 동시 편입 = 같은 날 액티브 ETF ≥ 2 개가 NEW·IN10·ADD(ET `report._consensus`). 예외: PDF 기준일이 운용사마다 다르면 그대로 표시, 신규 상장 회색. 시험: 손계산 셀·묶음, 동시 편입 경계(1개는 제외).

### §20 브리핑 — 구성·미준비·품질 줄 (로그인)

섹션 표는 §4.5. 규칙: ① 수치는 저장소 값 그대로(반올림은 렌더), ② 재료의 `as_of` 가 기대 기준일과 다르면 그 줄에 실제 기준일을 적는다(ADR 0018 §2-5), ③ 무른 의존 시한까지 재료가 없으면 '미준비 — <작업>(<예정 시각>)', ④ 단계 밖 섹션(P7·P8)은 싣지 않는다(`omitted` — 품질 줄에도 적지 않는다), ⑤ 품질 줄 = 재료별 `stale`·`estimated`·`미준비` 목록(모두 ok 면 '자료 이상 없음'), ⑥ 빈 섹션은 제목 없이 빼고(SD `SKIP_TITLES` 규칙) 경고 로그. 하루 1회 = 그날 dedup 키 접수 1건. 시험: 섹션별 손 입력, SD 규칙 승격(§1.5), 하루 1회 시뮬레이션.

### §21 장중 투자자 수급 (로그인 — F2 §1.1·§4)

| 항목 | 정의 |
|---|---|
| 원천 | KIS `FHPTJ04030000`(호출 시점 오늘 누적 한 줄) → `prv_gex.investor_flow`(**대금 백만원·수량 계약/주** — 0006 계약, metrics §0 '원 저장'의 예외). API 가 원으로 바꿔 낸다 |
| 시각 | 행 `ts` = 수집 슬롯 분(KST). 빈 분 = 행 없음 — 화면은 선을 끊는다(보간 금지) |
| 7카드 | 개인 `prsn`·외국인 `frgn`·기관 합계 `orgn`·금융투자 `scrt`·투신 `ivtr`(사모 `pe_fund` 제외 [확인 필요 — §10 Q7])·연기금 `fund`·기타법인 `etc_corp` |
| 선 토글 | 기본 개인·외국인·기관, 세부 금융투자·투신·사모·은행 `bank`·보험 `insu`·종금 `mrbn`·연기금·기타단체 `etc_orgt`, 기타법인(기본 끔) |
| 검산 ① | 현물: `prsn + frgn + orgn + etc_corp + etc_orgt` 순매수 대금 = 0 ± (백만원 반올림 × 5) [확인 필요]. 선물·옵션은 정의 별도(실측 뒤 — #39) |
| 검산 ② | `orgn = scrt + ivtr + pe_fund + bank + insu + mrbn + fund`(`etc_orgt` 포함 여부 [실측 필요 #39]) |
| 검산 ③ | 마감 뒤 마지막 분 값 대 `prv_flows.market_investor_daily`(원 — 단위 환산 뒤). 차이는 기록·`quality` |
| 품질 | 장중 `estimated`, 슬롯 + 45초 넘어 받은 값 `estimated` + 사유, 검산 실패 행 `invalid`(집계 제외 — 수는 보인다) |

### §22 프로그램매매 (로그인 — F2 §1.4·§4)

| 항목 | 정의 |
|---|---|
| 원천 | `FHPPG04600001`(일별)·`FHPPG04600101`(장중)·`HHPPG046600C1`(투자자별 당일)·`FHPPG04650101`(종목 장중)·`FHPPG04650201`(종목 일별) — 칸 이름은 공식 예제 기준, 단위·반영 시각 [실측 필요 #40·#41]. 저장은 **원** |
| 일별 막대 | 순매수 = 전체(`whol`) 합계(`smtn`) 순매수 대금. 막대 위 상세 = 차익(`arbt`)·비차익(`nabt`) × 위탁(`entm`)·자기(`onsl`) |
| 검산 ④ | 전체 = 차익 + 비차익, 위탁 + 자기 = 합계(대금·수량 각각, 허용 0 — 합성 원장) |
| 상위 20 | 순매수(또는 순매도) 대금 순. **범위**: 장중 = `거래대금 상위 N + 관심종목`(N = `markets.yaml program.scan_top_n`), 마감 = `전 종목(마감 확정)` — 응답 `scope_label` 필수(검산 ⑤ — 꼬리표가 범위를 말하는지 시험) |
| 품질 | 장중 `estimated`, 마감 훑기 `ok`, 훑기가 끝나지 않았으면(이어 받는 중) `estimated` + '훑은 종목 n/N' |

### §23 간밤 미국 신고가·금융위 대조 (로그인)

미국 신고가 = board/us 정의 그대로(ADR 0001 Q10): 직전 252봉(`config/us.yaml lookback.w52`) 종가·고가 기준 `>`, 이력 < 252 + 추이 일수 + 10 이면 보류(수를 적는다), 유니버스 = Nasdaq 스크리너 − 펀드·하한 미달(`config/us.yaml universe`). 기준일 = 미국 거래일(브리핑·화면에 표시). 금융위 대조 = 같은 날 KRX 종가 대 금융위 종가 불일치 비율(정확히 같아야 — `reconcile.close_tol_pct` 0), 경고 문턱 `reconcile.warn_mismatch_pct`. 시험: 승격 80 + 대조 손계산.

---

## 부록 B. `docs/probe_results.md` §7 에 더할 줄 (#39~#48 — 값은 적지 않고 칸·단위·코드·반영 시각만)

| # | 대상 | 확인할 것 | 막는 작업 |
|---|---|---|---|
| 39 | KIS `FHPTJ04030000` 현물·주식선물 (F2 §1.1·§1.3) | 코스피·코스닥 현물 코드 쌍(예제 `999`·`S001` 의 뜻), 주식선물 코드, 금액 단위(백만원 가정), 장전(08:00~) 값 유무, KRX 만인지 통합인지, `etc_orgt` 가 기관 합계에 드는지, 응답 반영 주기 | `flows.market_minute` 의 현물 조합 |
| 40 | KIS 프로그램 시장 TR `FHPPG04600001`·`FHPPG04600101`·`HHPPG046600C1` | 응답 칸·단위·반영 시각, 거래소 `J`·`NX`·`UN` 동작, 일별 날짜 구간 상한(60일 백필 호출 수), 장중 구간·시각 입력 뜻 | `program.intraday`·`program.daily` |
| 41 | KIS 종목 프로그램 `FHPPG04650101`·`FHPPG04650201` + 거래대금 상위 한 번 개수 | 칸·단위·반영 시각, 상위 TR 이 한 번에 몇 종목(N 기본값), 전 종목 훑기 소요(4/s) | `program.stock_scan`·`program.stock_sweep` |
| 42 | F2 실데이터 검산 ①~④ | `python -m kbj.services.engine.verify --program --date <D>` 의 건수(값·종목코드 없음) | — |
| 43 | ECOS 매크로 표 | `721Y001`·`301Y013`·`513Y001`·`731Y003` 등 `[실측 필요]` 항목 코드·주기·반영 시각, 개정 값 처리 | `macro.monthly`·`macro.evening` |
| 44 | KOSIS 매크로 표 | `DT_1C8015`·`DT_1JH20201`·`DT_1DA7001S` 항목 ID·계절조정 여부 | `macro.monthly` |
| 45 | FRED·미 재무부·뉴욕연은 | FRED 시리즈 목록의 저작권 표시(시리즈별 notes)·`releases/dates` 응답·한도, 재무부 수익률곡선 형식·시각, 뉴욕연은 SOFR·EFFR 엔드포인트·시각 | `macro.morning`·`macro.morning_prv` |
| 46 | Yahoo chart | 매크로 12종·미국 지수 심볼 응답, 장중 봉 구분(`check_futures_us_index` 규칙 2), 한도·429 형태 | `macro.morning_prv`(us.eod 지수는 ET 운영 확인분) |
| 47 | Nasdaq·Stooq | 약관(§10 Q6), 스크리너 유니버스 크기·필드, history 수정주가 여부, Stooq 폴백 빈도 | — (켬 — ET 운영 확인) |
| 48 | 금융위 15094808·15094807·금투협 15094809 | 기술문서로 적은 P0 §3.1·§3.3 의 오퍼레이션·응답 칸을 실응답으로 확인(대소문자·띄어쓰기 경로 포함)·0017 칸 이름 대조, KRX 대조 허용(`reconcile.fsc_*`) | `market_stats.kofia`·`market.fsc_daily` |

---

## 부록 B2. 묶음 지시 요약 (오케스트레이터가 묶음 프롬프트에 넣는다)

모든 묶음 공통: CLAUDE.md 절대 규칙(source·as_of·quality, 에러 삼키지 않기, 키·토큰 마스킹, 실주문 금지, MD6 파일 복사 금지), 네이버 스크래핑 금지, KIS 는 auth + 리미터로만, DART 는 정본 클라이언트로만(`check_canonical`), `.env`·토큰 캐시 열지 않기, 시계 주입, 원본 시험 승격은 그대로(import 경로만), 자기 묶음 파일만(§1.10) — 공유 파일은 §1.10 표대로, 필요하면 요청. git commit/push·stash·checkout·reset 금지(메인이 한다). 도커 통합·legacy 전체·sim 전체는 돌리지 않는다. 끝날 때 §1.11 의 공통 + 묶음 명령. 보고는 짧게, 한국어.

---

## 부록 C. 이 설계가 읽은 근거

- 계획·등급·지표: `docs/PLAN.md` §1·§2·§4·§5·§8(P3·P4 결과·P5), `docs/DATA_TIERS.md` §1·§2, `docs/metrics.md` §0·§2·§6.5·§8, `docs/p5_flow_program_prompt.md` 전부.
- 이전 설계: `docs/p3_design.md` §0.1(D-P3-12·17)·§3.7·R18, `docs/p4_design.md` §0~§1·§1.13~§1.15·§9, ADR 0001(U2·U3·U4·Q5·Q8·Q9·Q10)·0003·0005·0017(:40·:74)·0018 전부.
- 목록: `docs/inventory.md`:21·86·107·109·117·125·132·133·155·165·176~194(통합 시간표·아침 브리핑 통합안)·303·400~402, `docs/conflict_map.md`:115·141·268~271·276·294.
- 코드(HEAD `3cb2d5a`): `config/jobs.yaml`(P5 작업 15·`gex.poller`), `config/notify.yaml`:21~75, `config/limits.yaml`:9~91, `config/markets.yaml`, `config/knowledge/{themes,sectors,sector_map}.yaml`, `config/calendar_events.yaml`, `kbj/data/catalog.py`:51~190, `kbj/data/private/kis/datasets.py`:119~317, `kbj/data/public/ecos/datasets.py`, `kbj/data/public/kosis/datasets.py`, `kbj/data/private/ecos_restricted.py`, `kbj/store/migrations/0001~0013`(특히 `0003`:49·`0006`:160·471·`0007`:117), `kbj/services/scheduler/registry.py`:140~268·370·586, `kbj/engines/board/{themes,config}.py`, `kbj/engines/etf/types.py`, `kbj/services/api/{app.py,readers/_common.py}`·`routes/*`, `kbj/services/public_export/export.py`:367, `kbj/services/engine/earnings.py`:557, `kbj/services/notifier/client.py`, `web/src/app/{pages,ribbon}.ts`, `web/src/pages/p10_etf.ts`, `tests/sim/harness.py`:198·885·1137, `tests/unit/api/make_web_fixtures.py`, `tests/unit/scheduler/test_registry_validation.py`, `pyproject.toml`(계약 ①~⑬·의존성), `.github/workflows/ci.yml`:156, `scripts/{canonical_baseline.txt,test_legacy.sh,check_canonical.py}`.
- legacy: `ET:monitor/kr/{README.md,engine.py,etf.py,tests/*}`, `ET:monitor/flow/{README.md,*.py,tests/*}`(시험 129 = 17·14·11·16·17·54), `ET:board/us/*.py`(2,250줄)·`board/tests/test_us_*.py`(80)·`board/config/us.yaml`, `ET:etf_tracker_v9/{market,report,tracker}.py`, `ET:MIGRATION.md`:28, `SD:server.py`(16,032줄 — :1025·1036·1479·4589·4835·4979·5015·5098·5151·5545~5835·7988·8004·8422·13013~13757·13126), `SD:valuechain.py`(1,099줄)·`data/valuechain_map.json`(v2.0)·`theme_crawler.py`·`analysis_journal_helper.py`:64·195·`scripts/check_*.py`·`tests/test_legacy_checks.py`, `SD:MIGRATION.md`:46, `GX:data/kis/models.py`:270~331·`scripts/probe_investor.py`:10~16·`services/poller/{config,planner}.py`.
