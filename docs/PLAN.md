# KBJ — 국장 통합 터미널 · 포트폴리오 완성본 계획서 (v0.3, 2026-10-06)

> **v0.3에서 바뀐 것**
> 1. 지금까지 만든 프로젝트를 **하나로 합친 포트폴리오 완성본**으로 만든다.
>    대상은 stock-dashboard, ETF-Traker(board·us·etf_tracker·dart-report·monitor·flowlab), GEXLAB이다.
> 2. 화면은 **MD6 디자인을 그대로 따른다**(개인용). 터미널 테마, 페이지 스와이프, 상단 띠.
> 3. 대상은 **국장**. MD6·BadOnion 기능을 국장판으로 옮긴 내용(v0.2)을 포함한다.
>
> 이 문서는 **다른 대화(통합 작업용)에 그대로 넘기는 설계서**다.
> **공개/로그인 등급은 `docs/DATA_TIERS.md`가 정본**이다. 이 문서의 §6(포트폴리오)·D3(공개 범위)과 다르면 DATA_TIERS를 따른다(2026-10-06 약관 확인 반영). 국장 기능 매핑 세부는 `docs/reference/plan_kr_v0.2.md`에 있다.
> **[결정 필요]**: 사용자가 정할 것. **[확인 필요]**: 실측 전의 가정.

---

## 0. 목표와 원칙

**목표:** 기존 자산을 **버리지 않고 모두** 한 레포, 한 서버, 한 화면에 모은다.
그러면서 **같은 일을 하는 코드가 두 벌 이상 남지 않게**(충돌 없음) 하고, 기능은 최대한 다양하고 쓸모 있게 만든다.

**절대 규칙** (board·GEXLAB의 CLAUDE.md를 합친 것 — 모든 모듈이 따른다)
1. 모든 수치에 `source`, `as_of`, `quality`(ok·stale·estimated·invalid)를 붙인다.
2. 추정을 단정하지 않는다.
3. LLM에 계산을 시키지 않는다. LLM이 지어낸 값이면 실패로 처리한다.
4. 에러를 삼키지 않는다. 한 모듈의 실패가 다른 모듈을 멈추지 않는다(격리).
5. 키·토큰은 어떤 로그·화면·커밋에도 남기지 않는다(마스킹 공용 함수).
6. 실전 주문은 하지 않는다. 주문 코드는 `LIVE_TRADING=false`가 기본이고, 사용자 승인 전에는 만들지 않는다.

---

## 1. 기존 자산 목록 (2026-10-06 레포 확인)

| 출처 | 주요 내용 | 기술 | 상태 |
|---|---|---|---|
| **stock-dashboard** | 시장·테마맵·밸류체인·시황·글로벌매크로·공시, 종목 상세·검증 시트·분석 일지·발굴·AI추천·관심종목·백테스트·상관, 컨센서스 스냅샷·분기·리비전, 어닝 파서·시그널·알림, 밸류에이션, TAM 모델, 오버행, 테마 크롤러, 포트폴리오·수익률 저널, 텔레그램 push 11종과 양방향 봇, 데이터 신선도·워치독, **규칙 기반 기술적 알림 엔진(재설계 목표, REBUILD_BRIEF §6)** | Flask 단일 파일 약 1.6만 줄, SQLite, 바닐라 JS, APScheduler(cron 39개), Render | 동작, 구조 산만 |
| **etf-traker/board** | 국장 전 종목 **신고가 엔진**(60일·52주·역사적, 종가·고가 기준), 섹터·테마 분류, 랭킹, 탐지기, 웹 보드·엑셀·텔레그램 | Python, sqlite, 단계별 state JSON | 동작, 테스트 다수 |
| **etf-traker/board/us** | 간밤 미국장 신고가·등락 브리프 | 같은 엔진 | 동작 |
| **etf-traker/etf_tracker_v9** | 국내 ETF 구성종목 일일 변동(운용사 어댑터) | Python | 동작 |
| **etf-traker/dart-report** | 종목코드 하나로 DART 재무 엑셀 6블록 | Python | 동작 |
| **etf-traker/monitor/bok** | 통화신용정책보고서 110시트, 수출 104품목, ECOS·FRED 최신치, 대시보드 8탭, 아침 브리핑 | Python | 동작 |
| **etf-traker/monitor/kr** | 전 종목을 테마 198·밸류체인 23으로 묶은 섹터 모니터 | Python | 동작 |
| **etf-traker/monitor/flow**, **flowlab** | 신고가 상위 종목 수급(기관 7구분), 이벤트 스터디, 백필 | Python | 동작·실험 |
| **GEXLAB** | 코스피200 옵션 GEX 엔진(F·IV·GEX·Flip·월·기대변동폭·확장 지표), KIS 인증·레이트리미터·웹소켓, KRX 일별, **KRX 거래 캘린더(야간·휴장·만기)**, TimescaleDB·스풀, 서비스 골격, 기능 플래그·섀도, 지표 명세·검증 리포트 | Python 3.12, uv, FastAPI 예정, Timescale, Redis, Docker | Phase 3까지 완료, 테스트 3천여 개 |
| **kis-ota** | KIS 공식 샘플, strategy_builder, backtester | 외부 코드 | **참고만**(그대로 넣지 않는다) |
| **참고 사이트** | MD6(디자인·대시보드 구성), BadOnion(수출입·종목 매핑) | — | 기능·디자인 참고 |

---

## 2. 충돌 지도 — 같은 일을 하는 코드와 정리 규칙

| 영역 | 지금 몇 벌인가 | 남길 것(정본) | 이유 |
|---|---|---|---|
| **KIS 인증·토큰** | stock-dashboard `kis_api.py`, board `ingest/kis.py`, GEXLAB `data/kis` + auth 서비스 | **GEXLAB auth 서비스** 하나가 발급하고 나머지는 Redis에서 **읽기만** | KIS는 **토큰 발급이 1분에 1회**이고 앱키를 공유한다. 여러 프로젝트가 따로 발급하면 서로 토큰을 무효화한다. **실제 충돌 1순위** |
| **KIS 호출 한도** | 각자 따로 | **앱키당 Redis 레이트리미터 하나**(실측 초당 4건) | 따로 호출하면 `EGW00201` 초과 |
| KRX OpenAPI | stock-dashboard `krx_api.py`, board `ingest/krx.py`, GEXLAB `data/krx`, flowlab `krx.py` | GEXLAB 클라이언트 + board의 `stk_bydd_trd` 파서 | 하루 호출 한도 공유 |
| DART | stock-dashboard `dart_collector.py`, board `ingest/dart.py`, dart-report | 클라이언트 1개 + corp_code 캐시 1개. dart-report는 그 위의 **리포트 모듈**로 | 하루 한도 공유, 파서 함정 지식은 dart-report에 있다 |
| 네이버 금융 | stock-dashboard, board `naver.py`, flowlab `naver.py` | 어댑터 1개(재시도·속도 제한) | 스크래핑 차단 위험 |
| 공공데이터포털 | board `datago.py` | 그대로 공용 어댑터로 | — |
| ECOS·FRED | stock-dashboard 매크로, monitor/bok | 어댑터 1개 | — |
| 텔레그램 발송 | stock-dashboard `send_telegram`(push 11종·봇), board, etf_tracker, monitor(bok·flow) | **notifier 서비스 1개**(채널·토픽·중복 방지·쿨다운·발송 기록) | 아침 브리핑이 두 군데서 겹친다(stock-dashboard 대 bok). 봇 토큰도 하나 |
| 스케줄 | APScheduler 39개, GitHub Actions 4개, GEXLAB scheduler | **scheduler 서비스 1개 + 작업 등록부**(이름·시각·의존·재시도) | 같은 데이터를 두 작업이 받지 않게 한다 |
| DB | SQLite 2벌(dashboard.db, board), Timescale(GEXLAB) | **PostgreSQL + TimescaleDB 하나**, 도메인별 스키마(`market`·`flows`·`filings`·`fin`·`trade`·`gex`·`board`·`alerts`·`ops`) | 테이블 이름 충돌 방지, 백업 일원화 |
| 거래 캘린더 | 각자 휴장 판정 | **GEXLAB `core.calendar`** 정본 | 야간·만기·대체공휴일·금요일 밤까지 실측으로 검증됐다 |
| 52주 신고가 | stock-dashboard 스캐너, board `newhigh.py` | **board 엔진** 정본 | 3종·종가·고가 기준·테스트 |
| 테마·밸류체인 | stock-dashboard `themes_mapping.json`·`valuechain.py`·`theme_crawler`, monitor/kr(테마 198·체인 23), board `knowledge/` | **하나의 분류 사전**(테마·체인·종목·근거·출처) | 같은 종목이 서로 다른 테마로 나오는 것 방지 |
| 기술적 지표 | stock-dashboard `_calc_rsi_macd`·볼린저·ADX·피보·추세선·볼륨프로파일 | `kbj.core.indicators`(순수 함수 + 테스트) | 알림 엔진과 화면이 같은 값을 쓴다 |
| 수급 | stock-dashboard `flow_cache`, board `flows.py`, monitor/flow, flowlab, GEXLAB 투자자 | 수집기 1개 + 이벤트 스터디(flowlab)는 분석 모듈로 | — |
| 웹 서버 | Flask(stock-dashboard), board web, monitor docs 정적 | **FastAPI 하나**(`/api/<도메인>/…`) + **MD6형 SPA 하나** | 라우트 충돌·이중 서버 방지 |
| 설정·환경변수 | 프로젝트마다 제각각 | `KBJ_` 접두사 + `config/*.yaml` 하나, 키 이름표(`docs/secrets.md`, 값 없이 이름만) | — |
| 파이썬·의존성 | 버전 제각각, Flask 대 FastAPI | Python 3.12, **uv 단일 lock** | 의존성 충돌 방지 |

**정리 방식(교살자 패턴):**
1. 기존 코드를 `kbj/legacy/<프로젝트>/`에 **그대로 옮겨** 테스트를 먼저 통과시킨다.
2. 위 표의 정본 모듈로 **한 영역씩** 갈아 끼우고, 갈아 끼운 legacy 코드는 지운다.
3. 단계마다 "같은 일을 하는 코드가 두 벌인가"를 CI에서 검사한다. 예: 금지 import 규칙(`import-linter`)으로 legacy가 KIS를 직접 호출하면 실패시킨다.

---

## 3. 모노레포 구조

```
kbj/
  core/          순수 계산 — calendar · indicators · quality · models · gex(GEXLAB core) · newhigh(board) · trade_math
  data/          외부 어댑터 — kis · krx · dart · naver · datago · ecos · kosis · kofia · customs(관세청) · fred · yahoo · rss · etf_issuers
  store/         DB 스키마·마이그레이션·저장(Timescale) · 스풀 · Redis 키 한 곳
  engines/       도메인 엔진 — market · flows · board(신고가·랭킹·탐지) · themes(분류 사전) · etf · filings · financials(dart-report)
                 · consensus · valuation · tam · trade(수출입·매핑) · macro(bok) · gex · diagnosis · rules(알림) · backtest · journal
  services/      auth · scheduler · collectors · ws-gateway · engine · api(FastAPI) · notifier(텔레그램 push·봇)
  web/           MD6형 SPA (Vite·TS) — 페이지 스와이프 · 상단 띠 · 테마 5종 · PWA
  reports/       엑셀·텍스트 산출(dart-report 6블록, ETF 리포트, 신고가 랭킹, BOK 브리핑)
  legacy/        옮겨 온 원본(정리되면 비운다)
  docs/          PLAN · metrics(지표 명세) · probe_results · runbook · ADR(결정 기록) · secrets(이름만)
  tests/         unit · property · golden · integration(docker)
```

---

## 4. 기능 목록 (합친 결과 — 화면별)

**화면은 MD6 디자인을 따른다.** 터미널 폰트, 테마 5종(Amber·Phosphor·Ultraviolet·Dark·White), 좌우 스와이프, 페이지 미니맵, 상단 띠, 환영 화면, 모바일·PC·PWA.

| 페이지 | 기능 | 출처 자산 |
|---|---|---|
| **상단 띠(공통)** | 세션·야간 카운트다운, 금통위, 반도체 쏠림, 경기민감 대 방어, 시장폭(신고가 수), 신용스프레드, 한국 대 글로벌, 외국인 현물·선물 순매수, GEX Flip까지 거리 | v0.2 · board · GEXLAB |
| **1 시장** | 지수·선물(주야간)·VKOSPI·환율·금리·원자재·미국 지수, 업종 히트맵, 코스피200·코스닥150 트리맵, 테마맵, 투자자 수급·프로그램 | stock-dashboard 대시보드·테마맵, MD6 |
| **2 신고가 보드** | 60일·52주·역사적 신고가, 섹터·테마 집계, 랭킹, 탐지(이벤트), 엑셀, 간밤 미국 신고가 브리프 | board, board/us |
| **3 진단** | 종합 판정·국장 공포탐욕, 반도체 쏠림·시장폭·신용·글로벌, **GEX 레벨**(Flip·월·기대변동폭·VEX·CEX·스큐·HIRO-lite), 이벤트 표시(만기·금통위·수출 속보·실적 집중) | MD6 진단, GEXLAB |
| **4 수급** | 신고가 상위 종목 수급(기관 7구분) 차트, 외국인·기관 연속 순매수, 수급 반전, 이벤트 스터디 | monitor/flow, flowlab |
| **5 일정·공시·뉴스** | 경제 캘린더(한국·미국·금통위·FOMC·만기), DART 실시간 공시, 잠정실적 → 실적 일정, 오버행(전환·보호예수), 내부자·자사주, 뉴스 헤드라인 | stock-dashboard 공시·오버행, MD6 |
| **6 매크로** | BOK 통화신용정책보고서 지표, ECOS·FRED, 한국 PPI·수출입물가, 글로벌 매크로 | monitor/bok, stock-dashboard 글로벌매크로 |
| **7 수출입** | 관세청 월간·10일·20일 속보, 품목·국가·지역 탐색, 급등, 단가 대 물량, 트리맵, CSV, 수출 104품목 | v0.2(BadOnion 국장판), monitor/bok 수출 |
| **8 종목 상세** | 시세·차트(기술적 지표 겹쳐 보기, 볼륨 프로파일), 재무(dart-report), 부문 매출, **컨센서스·리비전**, 밸류에이션 밴드, TAM, 오버행, 수급, 종목↔HS 매핑 수출 겹쳐 보기, 피어, 검증 시트 | stock-dashboard, dart-report, v0.2 |
| **9 테마·밸류체인** | 테마 198·체인 23 모니터, 밸류체인 맵, 테마별 강도 | monitor/kr, stock-dashboard 밸류체인 |
| **10 ETF** | ETF 구성종목 변동, ETF 맵, 자금 유입 | etf_tracker_v9, stock-dashboard ETF맵 |
| **11 알림·규칙** | **규칙 기반 기술적 알림**(JSON DSL, AND/OR, RSI·MACD·볼린저·이평·ADX·거래량·수급 연속), 쿨다운, 발송 이력, **알림 후 N일 성과 통계** | REBUILD_BRIEF §6 |
| **12 백테스트·저널** | 규칙·신호 백테스트, 추천 성과, 분석 일지, 포트폴리오·수익률 저널, 상관관계 | stock-dashboard |
| **13 운영** | 데이터 신선도, 작업 모니터(스케줄 등록부), 헬스, 워치독, 발송 기록 | stock-dashboard 운영, GEXLAB health |
| **커스텀** | MD6식 3×2 위젯 격자, 관심종목 | MD6, stock-dashboard 관심종목 |
| **텔레그램** | push(아침 브리핑 하나로 통합, 마감 보드, 수급 시그널, 미국장 브리프, ETF, 규칙 알림, GEX 레벨 변화), 양방향 봇(`/시황` `/수급` `/가격` `/신고가` `/gex` `/도움`) | stock-dashboard, board, monitor |

---

## 5. 데이터 출처 (정리)

| 종류 | 출처 |
|---|---|
| 시세·수급 | KIS(실시간 REST·웹소켓, 투자자), KRX OpenAPI(일별·선물옵션), 네이버(폴백), 공공데이터포털(교차검증) |
| 공시·재무 | DART OpenAPI |
| 매크로 | ECOS, KOSIS, KOFIA(채권·신용·예탁금), FRED, BOK 보고서 |
| 수출입 | 관세청(공공데이터포털) [확인 필요: 10일 품목·시군구] |
| 해외 | Yahoo(미국 지수·신고가 — board/us 방식), 해외 지수 지연 |
| 뉴스 | 경제지 RSS, 네이버 검색 API(헤드라인·링크만) |
| ETF | 운용사 어댑터 |

---

## 6. 포트폴리오로 보여 줄 것

- **README**: 한 장 요약, 아키텍처 그림, 화면 GIF, 모듈별 링크, 기술적 결정(ADR) 요약
- **품질 지표**: 테스트 수·커버리지 배지, CI(ruff·pyright·pytest·eslint·tsc·vitest·docker build), 골든 테스트
- **데모 모드**: 녹화된 실데이터 스냅샷(fixture)으로 서버 없이 돌아가는 화면. 키 없이도 실행된다.
  - 공개 레포나 데모에는 **KIS·KRX 원시세를 넣지 않는다**(약관: 제3자 제공·재배포 금지).
  - 공공데이터(DART·ECOS·관세청)와 계산 지표는 넣어도 된다.
  - 개인 운영은 실데이터로 돌린다.
- **문서**: 지표 명세(`metrics.md`), 실측 기록(`probe_results.md`), 런북. GEXLAB 방식 그대로다.

---

## 7. 결정할 것

| # | 항목 | 기본값 |
|---|---|---|
| D1 | 레포 | 새 비공개 레포 `KBJ`(모노레포). 기존 레포는 읽기 전용 보관 |
| D2 | 실행 환경 | 서울 VM 1대 + Docker Compose(Postgres·Timescale·Redis·서비스·웹). Render 무료플랜은 휘발 디스크라 쓰지 않는다 |
| D3 | 공개 범위 | 개인 운영은 비공개. 포트폴리오 공개는 **데모 모드만** |
| D4 | KIS 앱키 | KBJ 전용 앱키 1개. 모든 서비스가 auth 서비스 토큰을 공유한다 |
| D5 | 텔레그램 | 봇 1개. 채널·토픽으로 나눈다(시장·신고가·알림·운영) |
| D6 | 무역 데이터 | 1단계 한국만 |
| D7 | LLM 사용처 | 요약·문장화만(시황 초안·리포트 초안). 계산·수치 생성은 금지(절대 규칙 3) |
| D8 | legacy 정리 기한 | 단계마다 해당 영역 legacy를 0으로 만든다. 마지막 단계에서 `legacy/`를 비운다 |

---

## 8. 단계 (각 단계는 완료 기준을 모두 채워야 넘어간다)

**P0 — 목록·충돌 지도 확정 (2~3일)**
- 네 레포의 모듈·cron·텔레그램 발송·DB 테이블·환경변수를 표로 만든다(`docs/inventory.md`).
- §2 충돌 지도를 실제 파일 단위로 확정한다.
- 관세청·ECOS·KOFIA 실측(docs/reference/plan_kr_v0.2.md P0)
- **완료 기준:** 같은 일을 하는 모듈 목록과 정본 지정이 끝나 있다. cron 전체가 하나의 시간표에 올라가 있고 겹침이 표시돼 있다.

**P1 — 모노레포 뼈대 + legacy 이식 (3~5일)**
- uv 단일 lock, CI, Docker Compose, Postgres·Timescale·Redis
- 세 프로젝트(stock-dashboard·ETF-Traker·GEXLAB — kis-ota 는 참고만)를 `legacy/`로 옮기고 **기존 테스트를 전부 통과**시킨다(경로·import만 고친다. 예외: ADR 0001 U3 합성 교체·개인정보 제거 — `legacy/<프로젝트>/MIGRATION.md`).
- **완료 기준:** KBJ CI(`.github/workflows/ci.yml`)에서 아래가 모두 녹색이다(2026-10-07 로컬 실측 수, `docs/conflict_map.md` §3.2).
  legacy 는 루트 `.venv` 하나(단일 `uv.lock`, Python 3.12, pandas 2.3.3, `uv sync --all-groups`)로 `scripts/test_legacy.sh` 가 프로젝트별 import 루트를 나눠 돌린다.
  (예외: `legacy/gexlab/uv.lock` 은 통합 시험 `test_compose` 가 `legacy/gexlab/Dockerfile` 로 앱 이미지를 만들 때만 쓰여 남긴다 — `legacy/gexlab/MIGRATION.md` §6)
  - GEXLAB: `pytest -m "not network"` **3,178 통과** + Docker 통합 **49**(TimescaleDB pg16·Redis). CI 에서는 단위 3,177(`gexlab`)과 통합 50(`gexlab-integration` — compose 설정 검사 1 포함)으로 나눠 돈다
  - board **1,319**(skip 1 — 발행 사이트 생성물 `docs/` 를 옮기지 않아 원래 있던 skipTest), monitor/kr **94**(Playwright 브라우저가 없으면 skip 1), monitor/flow **129**, flowlab selftest **44/44**, dart-report 스모크 `built. quarters= 14 annual= 4 bridge steps= 9`, etf_tracker_v9 모듈 13개 import
  - stock_dashboard 검사 스크립트 **10/10**(pytest 래퍼 12건 — E4 는 검사 하네스 버그를 고쳐 통과, 원본은 9/10)
  - 신규 `kbj/`: ruff·ruff format·pyright·import-linter·pytest, 레포 전체(legacy 포함) 공개 안전 검사 발견 0건

**P2 — 공용 기반 일원화 (5~7일)**
- auth(토큰 1곳 발급) + 앱키 레이트리미터
- 어댑터: KRX, DART, ~~네이버~~(ADR 0001 U4 — 네이버 어댑터는 만들지 않는다), ECOS, 공공데이터(+ KIS REST·KOSIS·관세청·금투협·금융위 시세)
- 캘린더, notifier, scheduler(작업 등록부), DB 스키마 통합(SQLite → Postgres 이관 스크립트)
- **완료 기준:** legacy가 KIS·KRX·DART를 직접 부르면 import-linter가 실패한다. 하루 운영 시뮬레이션(가짜 시계)에서 토큰 발급 1회, 중복 수집 0건이다.
  (해석 — 설계 `docs/p2_design.md` §0.2·메인 결정 D1~D7: legacy 는 import-linter 루트가 아니라 `scripts/check_canonical.py`(문자열·AST)가, kbj 안은 import-linter 계약 ④~⑦ 이 실패시킨다. '하루'는 24시간 창이고 "토큰 발급 1회"는 "모든 발급은 auth 에서만·auth 밖 발급 0·어떤 23시간 구간에도 접근토큰 발급 ≤ 1"로 단언한다 — 결정 D3)
- **결과(2026-10-07 로컬 실측, 설계서·ADR 0004~0007):**
  - legacy 의 KIS·KRX·DART 직접 호출 **0**: `check_canonical` 목표 0 그룹(`kis_oauth`·`kis_rest`·`kis_master`·`krx_api`·`dart`) 0건(파일 493개). legacy 의 호출은 논리 URL 브리지(`kbj/data/legacy_bridge.py`)로, 발송 7벌은 notifier shim 하나로. 남은 직접 호출은 줄어들기만 하는 기준선 — `datago` 2·`kis_ws` 4(P7, 결정 D2)·`naver` 176·`krx_scrape` 81(P3~P5). kbj 는 import-linter 계약 **7 kept, 0 broken**
  - 하루 운영 시뮬레이션(`tests/sim`, 24시간 창 2026-10-06 05:00 ~ 10-07 05:00 KST): 접근토큰 발급은 auth 의 05:00·다음 날 04:00(만료 60분 전 갱신) 두 번뿐 — **어떤 23시간 구간에도 1회**, 접속키 3회도 auth, **auth 밖 발급 0**. 데이터 키 **1,385개 각각 수집 1회**(선점 거절 0, 데이터셋 66개마다 작업 하나), KRX (엔드포인트, 기준일) 12쌍 각 1회, KIS 초당 최대 4건, 아침·마감 브리핑 각 1건(U2 — 발송 꺼짐이라 `suppressed`, 텔레그램 요청 0). 시험 50개 통과(변형 6종 포함)
  - kbj: ruff·ruff format·pyright(0 errors)·import-linter·pytest **1,804 통과**·22 건너뜀(통합 21 — Docker 없음, legacy 휴장 대조 1)·xfail 0 · `scripts/check_public_safety.py` 레포 전체 0건 · 작업 등록부 검증 통과
  - legacy(`scripts/test_legacy.sh`, 기존 시험 — 승격한 원본 시험만 빠지고 프로젝트마다 다리 시험 +1): GEXLAB **2,716**(P1 3,177 − kbj 로 승격·폐지 462 + 1) + 통합 1(Docker 49 는 CI), board **1,267**(1,319 − 55 + 3), monitor/kr 94·flow 129·flowlab 44/44·dart-report 스모크·etf_tracker_v9 13 import, stock_dashboard **13**(12 + 1)
  - 남은 것: 운영 VM 전환(옛 발급자 정지·레포 밖 발급자 K10·setWebhook 이전 — 사용자 작업), GitHub CI 첫 녹색, Docker 통합 시험(kbj 21·GEXLAB 49 — 이 환경에 Docker 없음), 실측 체크리스트(`docs/probe_results.md` §7 #12~#19)

**P3 — MD6형 웹 + 시장·신고가·수급·ETF 수급 (6~8일)**
- SPA 셸(MD6 디자인, 테마 5종, 스와이프, PWA, 상단 띠). 화면 구성 예시는 미리보기 아티팩트(합성 데이터)
- 페이지 1(시장 — 시장 거래대금·시장폭 포함)·2(신고가 보드)·4(수급·스크리닝), FastAPI `/api/market`·`/api/board`·`/api/flows`·`/api/etf`, 텔레그램 웹훅 수신(P2 D4)
- **사용자 요청(2026-10-07) 반영:** 거래대금 상위·외국인/기관 순매수 상위·외인·기관 동반·연속 순매수·거래대금 급증 스크리닝(페이지 4), **ETF 수급**(페이지 10 — 순유입=좌수 변화×NAV, 투자자별 ETF 순매수, 유형별 자금 흐름, 괴리율, 구성종목 변동). 지표 정의는 `docs/metrics.md`(분할·분배금·신규상장·NXT 함정 포함)
- 그래서 ETF 표 마이그레이션(`prv_etf.etf_daily`·`quote_intraday`, 원래 0013·P5)을 **P3 으로 당긴다**. 수집 작업: KRX 주식·ETF 일별(거래소별 KRX/NXT/합계), KIS 투자자별·가집계·순위 TR(장중 잠정 → 마감 확정 덮어쓰기)
- 네이버·옛 환경변수에 묶인 legacy 단계(conflict_map §1.13, secrets.md P2 절)를 기능별로 kbj 작업으로 옮긴다. 일봉 이력은 KRX 로 다시 받는다(R11 — 약 2,500회를 며칠에 나눠)
- **완료 기준:** 장중 1시간 무결측. 신고가 보드 결과가 기존 board 산출과 같다(골든). 스크리닝·ETF 수급의 검산 ①②③(metrics.md §2·§4)이 합성·실데이터 모두 0 차이.
  (해석 — 설계 `docs/p3_design.md` §0.3·메인 결정 2026-10-07: R2 "가능한 검산은 0 차이, 불가한 검산은 사유를 quality·문서에 기록"(KIS 가 기타법인을 주지 않으면 ①은 실데이터에서 불가 — 합성 원장에서는 ①②③ 전부 강제), R8 네이버 출처 옛 역사적 최고가는 옮기지 않고 KRX 백필 범위(`history_from`)를 응답·화면에 표시, R18 마감 요약 `brief.closing` 은 P5. 실데이터 검산은 키를 받은 뒤 체크리스트 #20)
- **결과(2026-10-07 로컬 실측, 설계서 `docs/p3_design.md`·ADR 0008~0012 확정):**
  - **장중 1시간 무결측**(`tests/sim/test_intraday_hour.py`, 2026-10-07 09:55~11:05 KST, 실제 처리기 `market_intraday.flows`·`market` + 가짜 KIS + 메모리 저장소): 슬롯 10:00~10:50 6개 × 데이터 키 5개(지수·업종·가집계[KRX]·순위[KRX]·ETF 현재가) **각각 done 1회**, 슬롯마다 지수 3·업종 10·순위 20·가집계 40·감시 ETF 50행(기대값은 설정·가짜 서버 상수에서 계산) — 모자란 슬롯 0, 모든 행 ts = 슬롯 시작·estimated, 슬롯 마지막 KIS 호출 < 슬롯 + 9분, KIS 초당 최대 4건(창 안 scheduler 조회 462건), 발급은 auth 만. 일별 원장 오늘 행(kis.prelim)이 슬롯마다 마지막 이력 값으로 갱신. 변형 4종(10:20 HTTP 500·10:30 EGW00201 3연속·10:40 Redis 5초 끊김·10:31 scheduler 재기동) 모두 결측 0·중복 0. 하루 판(`test_intraday_day.py`, slow): 평일 09:00~15:30·수능일 10:00~16:30 각 40슬롯 무결측
  - **하루 운영 확장**(`tests/sim/test_one_day_p3.py`, 10-07 05:00 ~ 10-08 09:30 — 켜진 P3 작업 전부 실제 처리기): 데이터 키 1,522개 각 1회(데이터셋 70개, 선점 거절은 이미 받은 키뿐), KIS 초당 ≤ 4, 접근토큰 발급 auth 2회(05:00·다음 날 04:00), 원장 전이 장중 `kis.prelim`(estimated) → 마감 `kis`(ok, `investor_revision` 40행) → 다음 날 `krx`(ok)·어긋난 KIS 행 invalid(`eod_reconcile` 78행), 보드 artifact 5종 16:00(마감 수집 뒤 — ADR 0018, 원래 16:20) estimated → 08:40 확정 ok, `krx.daily` 끝 단계 검산 ③·분할 감지 기록, ETF 구성종목 변동, 공개 산출물 calendar·events·manifest(로그인 출처 이름 0)
  - **신고가 보드 = 기존 board 산출**(골든 — 묶음 E1): legacy 엔진으로 먼저 캡처한 합성 30거래일 × 5 payload 와 `kbj.engines.board.compute_day` 필드 단위 비교(상대 1e-9·절대 1e-6) + 독립 오라클 속성 시험. legacy board 는 shim
  - **검산 ①②③ 0 차이(합성)**: 고정 시드 합성 원장 + hypothesis — ①②③ 잔차 0, 깨진 행만 invalid·집계 제외(묶음 E2·E3). 실데이터는 `python -m kbj.services.engine.verify --date <D>`(키 수령 뒤 #20)
  - 작업 등록부: P3 작업 9개 켬(`krx.daily`·`market.backfill`·`market.close_collect`·`flows.intraday`·`market.intraday`·`board.daily`·`board.confirm`·`etf.collect`·`public.export`), `brief.closing`·`flows.report`·`us.*`·`market.fsc_daily` 는 P5. KIS 수집 키의 거래소는 `config/markets.yaml kis.venues`(기본 KRX)와 같다(등록부 시험)
  - kbj: ruff·ruff format·pyright(0 errors)·import-linter **10 kept, 0 broken**(계약 ⑧ 공개 내보내기·⑨ 엔진 순수·⑩ API 외부 호출 없음)·pytest **2,891 통과**(단위·시뮬레이션 2,845 + 통합 46 — Docker, 0001~0009·저장소 Pg)·1 건너뜀(legacy 휴장 대조 — 고정 사본이 기준)·시뮬레이션 70(`-m sim`) · `check_canonical` 통과 · 공개 안전 검사 레포 전체 0건 · `openapi --check` 최신 · 등록부 검증 통과
  - 프런트(`web/`): eslint·tsc·vitest **205 통과**(18 파일)·공개/로그인 빌드·번들 검사(공개 번들 /api·CSRF·KBJ_ 0, gzip 예산 안)·openapi → TS 타입 신선도·공개 사이트 조립(dry-run·실제). CI 에 `web` 잡 신설(node 22, `npm ci --ignore-scripts`, 예약·비밀 없음)
  - Docker: 앱 이미지에 로그인 SPA(node 1단계 → `/app/web/dist-login`), compose 서비스 `api`(호스트 네트워크·127.0.0.1:8000 — TLS 프록시 뒤 실제 IP 로 로그인 잠금), scheduler 에 KIS·KRX 키·공개 내보내기 DSN. 로컬 기동 확인: migrate 0001~0009 → health 200·SPA 200·세션 없음 401·로그인 200(`__Host-` 쿠키). redis 설정 파일 권한 문제(이미지 umask 077)를 고쳤다
  - legacy(`scripts/test_legacy.sh all`): GEXLAB 2,716 + 통합 49(Docker), board **1,115**(1,267 − 승격 153 + 다리 1 — E1), monitor/kr 94·flow 129·flowlab 44/44·dart-report 스모크·etf_tracker_v9 13 import, stock_dashboard 13. legacy 정리(§1.10·D-P3-18)는 **파일 단위로 뗄 수 있는 대체 완료분만** 지웠다 — flowlab 네이버 진단 7·KRX 스크랩 `krx.py`, SD 진단 5·`ohlcv_5y_collector.py`, etf_tracker_v9 `verify.py`·`NaverTop10`·`naver_names`. 기준선 naver 176 → **88**, krx_scrape 81 → **69**, datago 2 → **1**, 새 그룹 `etf_issuers` 0. ET board KR 수집 8파일·monitor/flow 수집 3파일·`etf_tracker_v9/market.py`·SD 수집 파일은 아직 켜진 legacy 명령이 import 해서(R17) 그 기능 대체 단계(P4·P5·P8)로 이월 — `legacy/etf_traker/MIGRATION.md`·`legacy/stock_dashboard/MIGRATION.md` 'P3'
  - **실사용 확인(최종 점검, `scripts/demo_p3.sh` — 합성 데이터만, 키·DB·Docker 불필요)**: 합성 세계(고정 시드 원장 + 신고가 골든 입력)를 메모리 저장소에 채운 API 서버 + 로그인 SPA 빌드 → Playwright(크로미움)로 로그인 → 페이지 1·2·4·10(+ 스크리너 하한 해제·종목 상세, 휴대폰 폭 390px) 스크린샷, 콘솔 오류·실패 요청·가로 넘침 0. 마감(18:00)·장중(10:05) 두 시계, 메모리 저장소와 `--db`(일회용 Timescale·Redis 컨테이너 — 마이그레이션 9개 → 같은 합성 데이터를 Pg 저장소로, 응답·화면 같음) 둘 다 확인. 이 점검에서 찾아 고친 것: ① 탭 강조가 한 칸 밀림(아직 그리지 않은 높이 0 페이지를 IntersectionObserver 가 ratio 1 로 알림 → 보이는 가로 폭으로 판단, 해시 이동은 즉시 스크롤), ② 문서 전체가 가로로 15,534px 넘침(페이지의 `.sr-only` 제목이 `.pages` 스크롤 밖 body 기준 — `.page { position: relative }`), ③ `Permissions-Policy: ()` 문법 오류(브라우저 콘솔 오류 — 기능별 `기능=()` 사전으로), ④ 404 `no_data` 의 서버 안내 문구를 화면에 싣기(`DataError.serverMessage`), ⑤ 앱을 내린 뒤 건 주기 작업 무시(`createScheduler`), ⑥ `market.backfill` 범위 실행이 끝나면 `board.rebuild_alltime`(history_from = 백필 첫날) — 실패는 종료 코드 1, ⑦ 데모 합성 세계의 금액을 실제 시장 규모로(거래대금 ×50·순매수 ×100 정수 배 — 검산 ①② 유지, 스크리너 기본 필터는 그대로 — `tests/test_demo_p3.py`), 스크리너 빈 결과에 '하한 미달 N종목, 하한을 낮춰 보세요' 안내. 최종 수치: pytest **2,893 통과**·1 건너뜀(Docker 통합 46 포함)·시뮬레이션 70, legacy `test_legacy.sh all` 모두 통과(GEXLAB 2,716 + 통합 49·board 1,115·kr 94·flow 129·flowlab 44/44·SD 13), vitest **208**
  - **신고가 3축으로 정의 변경(2026-10-08 사용자 요청 — ADR 0017)**: 역사적(상장 이후 전체)·52주(달력 364일)·120일(120 시장 거래일), 엄격 `>`, d60 없앰. 역사적은 이력이 상장일 또는 원천 바닥(`config/board.yaml newhigh.hist_source_floor` [실측 필요])에 닿아야 판정(못 닿으면 보류 + 품질 메모), 화면·API 의 "역사적 = YYYY-MM-DD 이후" 표기를 없앴다. 상장일까지 채우는 KRX 백필 `backfill market.backfill --to-listing`(여러 날 이어 받기). 골든은 같은 합성 입력을 새 엔진으로 **재캡처**(위 "기존 board 산출과 같다" 는 옛 정의 기준이었다 — 이제 정의는 독립 오라클 = 엔진 속성 시험·골든 30일 대조가 지킨다)
  - **장 마감 뒤 텔레그램은 전부 16:00(2026-10-08 사용자 결정 — ADR 0018)**: 원본 SD·ET 와 같게 등록부의 마감 뒤 발송 `brief.closing` 16:40·`flows.report` 18:20·`consensus.snapshot`(리비전) 18:30 → 전부 `0 16 * * 1-5`(마감 요약 캐치업 20:30 그대로). 재료인 `board.daily` 도 16:20 → 16:00 cron + `market.close_collect` 굳은 의존(마감 수집이 끝나는 대로). 실행기에 무른 의존 `wait_min` 을 더해 마감 요약·수급 리포트가 같은 16:00 보드를 최대 20분 기다린다(보드 실패는 발송을 막지 않는다). 실제 도착은 평소 16:00~16:05 [추정]. 16:00 수급은 KIS 마감 수집 값(잠정일 수 있음) 또는 전 거래일 값 — 리포트에 실제 기준일을 적는다(SD 2026-09-18 원칙). 아침·장중·공시 사건·운영 감시 발송은 그대로. 등록부 시험이 '장 마감 뒤 발송 = 16:00' 을 지킨다
  - 남은 것(사용자 승인·운영): Pages 활성·배포 키·디스패치 토큰·VM TLS·`known_hosts`(켜지 않음 — `pages.yml` 은 workflow_dispatch 만), 운영 DB 에 `kbj_public_export` 구성원 로그인 역할 + `KBJ_PUBLIC_EXPORT_DATABASE_URL`(없으면 `public.export` 가 매일 실패 알림), GitHub CI 첫 녹색(푸시 뒤), 실측 체크리스트 `docs/probe_results.md` §7 #20~#27, 스크리너 미적중 응답 약 13초(목표 3초 [제안] — 최적화 뒤 다시)

**P4 — 공시·재무·컨센서스·종목 상세 (5~7일)**
- DART 피드·잠정실적·오버행·내부자, dart-report 모듈화(엑셀 + 화면)
- 컨센서스·리비전, 밸류에이션, TAM, 종목 상세 페이지 8
- **완료 기준:** dart-report 엑셀이 이식 전과 같다(골든). 종목 상세가 관심 종목 20개에서 오류 없이 나온다.

**P5 — 매크로·테마·ETF (4~6일)**
- monitor/bok(페이지 6), 분류 사전 통합(테마·체인, 페이지 9), ETF(페이지 10), 아침 브리핑 하나로 통합
- **완료 기준:** 테마 사전에서 한 종목의 테마 충돌이 0이다. 브리핑 발송은 하루 1회다.

**P6 — 수출입·종목 매핑 (6~8일)** — docs/reference/plan_kr_v0.2.md 의 P5·P6
- **완료 기준:** 수출 속보일 당일 자동으로 반영된다. 관심 종목 50개의 매핑과 겹쳐 보기 차트가 나온다.

**P7 — GEX·진단 (3~5일)**
- GEXLAB engine 이식(이미 서비스형), 페이지 3 진단, 국장 공포탐욕·종합 판정
- **완료 기준:** GEXLAB 골든·명세 테스트가 KBJ에서 녹색이다. 진단 지표 명세가 `metrics.md`에 있다.

**P8 — 규칙 알림·백테스트·저널 (6~8일)** — REBUILD_BRIEF §6
- 규칙 DSL(JSON 스키마), 평가 엔진(장중 10분·마감), 쿨다운, 근거 메시지, **알림 후 성과 통계**
- 백테스트(같은 지표 함수), 저널·포트폴리오, 페이지 11·12
- **완료 기준:** 규칙 예시 10개가 백테스트와 실시간 평가에서 같은 신호를 낸다. 성과 표가 자동으로 쌓인다.

**P9 — 포트폴리오 마감 (3~4일)**
- 데모 모드(fixture), README·아키텍처 그림·GIF, ADR, `legacy/` 비우기, 운영 1주 무중단
- **완료 기준:** `docker compose up` 한 줄로 데모가 뜬다. 공개 산출물에 원시세와 키가 없는지 검사하는 스크립트가 통과한다.

---

## 9. 다른 대화(통합 작업)에 넘길 첫 지시

```
KBJ 레포의 docs/PLAN.md(v0.3)·docs/DATA_TIERS.md·CLAUDE.md 를 읽고 P0 부터 진행한다.
- 대상 레포: kimbob0604-cmyk/stock-dashboard, kimbob0604-cmyk/ETF-Traker, kimbob0604-cmyk/GEXLAB (kis-ota 는 참고만).
- P0: 네 레포의 모듈·cron·텔레그램 발송·DB 테이블·환경변수를 docs/inventory.md 표로 만들고, PLAN §2 충돌 지도를 파일 단위로 확정해 정본을 지정한다. 특히 KIS 토큰 발급 지점과 텔레그램 발송 지점을 전부 찾는다.
- 정본 선택이 애매한 곳(같은 기능, 다른 결과)은 두 결과를 실데이터로 비교해 표로 보여 주고 나에게 묻는다.
- 화면은 MD6(md6.today) 디자인을 따른다(스타일을 보고 직접 다시 만든다 — MD6 파일·로고·이미지는 복사하지 않는다).
- 공개/로그인 구분은 docs/DATA_TIERS.md 대로 처음부터 폴더·스키마·배포를 나눈다(실행 중 검사 대신 구조로 분리). KIS·KRX·금융위 시세 원본과 실데이터 fixture 는 레포에 넣지 않는다.
- 절대 규칙은 CLAUDE.md 에 있다. 키·토큰은 출력하지 않는다.
- 단계마다 완료 기준을 채우고, ruff·pyright·pytest·eslint·tsc·vitest 가 통과해야 커밋·push 한다.
```
