# KBJ P3 설계서 — MD6형 웹 + 시장·신고가·수급·ETF 수급

- 작성: 2026-10-07. 상태: **설계(구현 전)**. 대상 단계: `docs/PLAN.md` §8 P3 — 사용자 요청(2026-10-07)으로 수급 스크리닝(페이지 4)·ETF 수급(페이지 10)이 들어갔고 ETF 표 마이그레이션(원래 0013·P5)을 당겼다.
- 기준 문서: 계획 `docs/PLAN.md` §4·§8, 등급 `docs/DATA_TIERS.md`(정본), **지표 정본 `docs/metrics.md`**, P2 정본 모듈 `docs/p2_design.md`·`docs/adr/0004~0007`, 정본 지정 `docs/conflict_map.md`(§1.7 신고가·§1.10 수급·§1.13 네이버·Q1~Q13 기본값은 ADR 0001), 인벤토리 `docs/inventory.md`, 비밀 이름표 `docs/secrets.md`, 화면 구성 `docs/design/preview_synthetic.html`(사용자가 승인한 합성 미리보기 — 페이지 1·2·3·4·5·7·10, 상단 띠, 스킨 5종, 공개/로그인 전환).
- 원본 줄 번호: 이 레포 `legacy/` (브랜치 `claude/build`, `f959a9c`) 를 직접 열어 확인했다. 표기 `ET:` = `legacy/etf_traker/`, `SD:` = `legacy/stock_dashboard/`, `GX:` = `legacy/gexlab/`. kbj 쪽은 레포 경로 그대로.
- 표기: **[추정]** = 코드로 끝까지 확인하지 못함, **[확인 필요]** = 기본값으로 정한 것, **[실측 필요]** = 키를 받은 뒤 실제 응답으로 확인, **[결정 필요]** = 사용자가 정할 것, **[제안]** = 이 설계의 권고, ⚠ = 충돌·위험.
- 키·토큰·계좌 값은 이 문서 어디에도 없다. `.env`·토큰 캐시 파일은 열지 않았다. 실데이터 키가 아직 없으므로 P3 의 모든 증명은 합성 데이터·가짜 서버로 하고, 실데이터 검산은 키를 받은 뒤 `docs/probe_results.md` 에 남긴다.

---

## 0. 요약

### 0.1 결정 요약

| # | 결정 | 이유·근거 |
|---|---|---|
| D-P3-1 | 프런트는 **Vite + TypeScript + 바닐라 DOM**(UI 프레임워크 없음, 런타임 의존성 0). 차트는 손으로 그리는 SVG | 승인된 미리보기가 바닐라 JS 한 파일(위젯 20여 개)로 이미 동작한다. 공개 레포의 공급망 면적·lock 크기·번들 크기를 최소로. 위젯이 표·막대·스파크라인·히트맵뿐이라 프레임워크 이득이 작다(§6.1) |
| D-P3-2 | **같은 SPA, 빌드 두 벌**: `VITE_KBJ_TIER=public` 은 `./data/*.json`(pub_* 내보내기)만 읽고 로그인 위젯은 자물쇠, `login` 은 같은 출처 `/api` 를 읽는다. 공개 번들에 `/api` 문자열 0 | DATA_TIERS §3 "같은 SPA… 공개 사이트에는 로그인 API 주소조차 넣지 않는다". 빌드 시점 상수로 갈라 죽은 코드 제거 + 번들 검사(§6.6) |
| D-P3-3 | 로그인 서버는 **FastAPI + uvicorn 한 프로세스**(compose 서비스 `api`): `/api/*`·`POST /telegram/webhook`·로그인 SPA 정적 파일을 **같은 출처**로. API 는 외부 출처를 부르지 않는다(DB·Redis 읽기만 — import-linter 계약 ⑩) | P2 결정 D4(FastAPI 는 P3). 같은 출처라 쿠키·CSRF 가 단순하다. 화면 요청이 KIS 한도를 쓰지 않게 |
| D-P3-4 | 로그인은 **사용자 1명**: `KBJ_WEB_USER` + `KBJ_WEB_PASSWORD_HASH`(표준 라이브러리 `hashlib.scrypt`, 새 의존성 없음), Redis 서버 세션, 쿠키 `__Host-kbj_session`(HttpOnly·Secure·SameSite=Strict), 세션 결합 CSRF 토큰(헤더 `X-KBJ-CSRF`) + Origin 검사. **기본 사용자·비밀번호 없음** — 해시가 없으면 로그인은 503 | 공개 레포라 기본값을 둘 수 없다(§5.4) |
| D-P3-5 | 계층을 나눈다: **순수 계산** `kbj/engines/<영역>/`(I/O import 금지 — 계약 ⑨), **작업 실행** `kbj/services/engine/`·`kbj/services/collectors/`, **SQL** `kbj/store/repos/`, **행 자료형** `kbj/core/rows.py`(엔진과 저장소가 같이 쓰는 frozen dataclass — 계약 ③·⑦ 을 지키며 공유). 그래서 등록부의 `board.daily` owner 를 `kbj.services.engine.board:daily` 로 바꾼다 | 계약 ⑦(store 는 engines 를 모른다)과 골든 시험(엔진은 입력만으로 결정) |
| D-P3-6 | 마이그레이션 번호: **0007 board · 0008 market·flows 추가 · 0009 etf**(P2 계획의 0013 에서 당김). P2 설계 §8.2 의 0008~0013 번호 계획은 버리고 이후 단계도 적용 순서대로 다음 번호를 쓴다 | 적용기 `kbj/store/migrate.py:discover`·`pending` 은 번호 순서로 미적용분을 고른다 — 번호를 비워 두면 뒤 단계 파일이 앞 번호를 쓰게 돼 읽기 어렵다 |
| D-P3-7 | **잠정 → 확정 원장 규칙**: 한 (종목, 날짜, 투자자/지표) 의 화면 값은 **`krx`(ok) > `kis`(ok, 마감 확정) > `kis`(estimated, 장중)** 우선순위로 하나만 고른다. 장중 잠정 행은 마감 수집이 같은 키를 **덮어쓰고**, 덮기 전 값과 차이는 `prv_flows.investor_revision`·`prv_market.eod_reconcile` 에 남긴다 | metrics §0("장중 estimated → 마감 ok → D+1 대조 차이면 invalid")·§2. 페이지끼리 숫자가 어긋나지 않게 원장 하나(§3.5) |
| D-P3-8 | **KRX D+1 대조**: `krx.daily`(08:05) 가 전날 KIS 마감값과 종목별로 대조하고, 새 작업 **`board.confirm`**(08:40) 이 전날 보드를 KRX 확정 일봉으로 다시 계산해 덮는다 | ADR 0001 Q1 기본값("16:40 KIS, 다음 영업일 08:05 KRX 확정치로 대조·정정"), conflict_map E1(KRX 는 D+1 08:00) |
| D-P3-9 | **거래소 구분**: 실측 전에는 KIS 를 `config/markets.yaml` `kis.venues: [KRX]` 하나로만 부른다 [확인 필요]. KRX OpenAPI 일별은 `venues=(KRX,)`(카탈로그 그대로). 합계(`TOTAL`)는 **계산하지 않는다** — 화면에 "NXT 미포함" 꼬리표 | metrics §1, `kbj/data/spec.py:Venue` 머리말("실측 전에는 받은 그대로의 구분으로 키를 만들고 합계는 계산하지 않는다"). venue 를 늘리면 KIS 호출이 배로 는다(§3.7) |
| D-P3-10 | **board 엔진 승격 순서**: ① legacy 엔진으로 합성 입력의 **골든을 먼저 캡처**해 커밋 → ② `kbj/engines/board` 로 순수 이식(이름·반환 dict 키 유지) → ③ 골든 비교(허용오차) → ④ legacy `board/engine/*.py` 를 kbj 를 다시 내보내는 shim 으로(legacy `build.run` 은 sqlite 읽기 → kbj `compute_day` → state 파일 쓰기만) | PLAN 완료 기준 "기존 board 산출과 같다". shim 뒤에 골든을 만들면 자기 자신과 비교하게 된다. 두 벌 금지(CLAUDE §3, P2 §1.11) |
| D-P3-11 | **역사적 신고가 깊이**: KRX 백필(R11) 이 상장일부터 덮지 못하는 종목은 `hist` 를 계산하지 않는다(`refs.hist=None`, 사유 "이력이 상장일 전에서 끊김") — 지어내지 않는다. ET `board.db` 의 `alltime` 스칼라 이관은 [결정 필요](네이버 일봉에서 나온 값 — U4) | board 원칙 "계산되지 않는 값은 None"(ET:`board/engine/newhigh.py` 머리말), conflict_map §1.5 "네이버 행은 버린다" |
| D-P3-12 | **ETF 를 P3 로 당긴다**: 표 `prv_etf.*`(일별·장중·메타·분할·구성종목·변동) + 작업 `etf.collect` P5 → P3 + 운용사 어댑터 9곳(ET `etf_tracker_v9/collectors.py` 4곳 + `adapters/` 5곳) 승격. **네이버 TOP10 폴백(`collectors.py:NaverTop10`:226)은 버린다**(U4 — 대체 후보 KIS `FHKST121600C0` [실측 필요]) | 사용자 요청: 페이지 10 '구성종목 변동' |
| D-P3-13 | **ETF 유형 7분류**(국내 대표지수·국내 테마·해외주식·레버리지·인버스·채권·현금·원자재)는 새 함수 `kbj.engines.etf.types.etf_type` 가 정한다. 테마 태그는 `classify`(ET `etf_tracker_v9/themes.py:classify`:96, ADR 0001 Q8) 그대로 | `classify` 는 레버리지·채권을 `None` 으로 빼고(`EXCLUDE`:6) 해외·원자재 구분이 없다 — metrics §4 유형과 1:1 이 아니다(§4.3) |
| D-P3-14 | 장중 단위는 **10분 슬롯**(`slot10m`). 지수·업종을 받는 새 작업 **`market.intraday`**. `flows.intraday`·`market.intraday` 는 **슬롯 안에서 재시도**(`retry {max: 2, backoff_s: [20, 40]}`, `deadline_min: 9`) | 완료 기준 "장중 1시간 무결측" — 지금 `flows.intraday` 는 `retry: {max: 0}` 이라 한 번 실패가 곧 결측이다 |
| D-P3-15 | **공개판 P3 데이터** = `calendar.json`(세션·휴장·만기·지연 개장 — 코드 계산)·`events.json`(금통위 일정 — 수기 설정)·`manifest.json` 뿐. 금투협·ECOS·관세청 칩·패널은 '준비 중(P5·P6)'. 시세 패널 자리는 **TradingView 위젯** | 공개 등급 수집 작업(`market_stats.kofia`·`macro.*`·`trade.*`)은 P5·P6 이다. DATA_TIERS §2 "공개판에서 시세는 TradingView 위젯으로만" |
| D-P3-16 | **Pages 배포**: `.github/workflows/pages.yml` 은 `workflow_dispatch` 만(예약 없음). 데이터는 VM 의 `public.export` 작업이 **고아 브랜치 `public-data`**(커밋 1개, 강제 푸시)에 올리고, 워크플로가 SPA 공개 빌드 + 그 브랜치를 합쳐 배포한다. Pages 활성·배포 키·VM 의 디스패치 토큰은 **[사용자 승인 필요]** — P3 는 코드·워크플로·로컬 빌드까지 | CI 머리말 "예약(schedule) 실행은 두지 않는다", 공개 레포 Actions 무료(DATA_TIERS §5) |
| D-P3-17 | 등록부에서 P3 로 표시돼 있지만 **이번에 켜지 않는 작업**: `brief.closing`(본문)·`flows.report` → P5, `us.universe`·`us.eod`(간밤 미국 신고가) → P5, `market.fsc_daily`(금융위 시세 — 대조 보조) → P5. `brief.closing` 단계는 **[결정 필요]**(기본값 P5 — 아침 브리핑과 같은 브리핑 묶음) | 이번 P3 범위 목록에 없다. 범위를 넓히면 6~8일 안에 완료 기준을 못 채운다 |
| D-P3-18 | **legacy 이전 범위**: P3 는 **파일 단위로 대체가 끝난** KR 수집 경로(네이버·KRX 웹 스크랩·옛 환경변수 관문 포함)만 지운다. SD `server.py` 함수 단위 삭제(32줄 네이버·35줄 스크랩)는 종목 상세가 대체하는 P4 로(§1.10) | 기준선은 "줄어들기만" 이면 된다(ADR 0007). 1.6만 줄 파일의 함수 단위 수술과 SD 검사 스크립트(AST 추출) 충돌 위험을 P3 에 싣지 않는다 |

### 0.2 만드는 것 / 만들지 않는 것 (P3 경계)

| 만든다 (P3) | 만들지 않는다 |
|---|---|
| SPA 셸: 상단 바·상단 띠·탭·미니맵·좌우 스와이프·키보드·해시 라우팅·스킨 5종·공개/로그인 빌드·PWA(앱 셸만 캐시) | P4 이후 페이지(3 진단·5 공시·6 매크로·7 수출입·8 종목 상세·9 테마·11 알림·12 백테스트·13 운영) — **빈 자리 패널 + '준비 중(Pn)' + 등급 자물쇠만**(§6.5) |
| 페이지 1 시장(지수 3종·시장 거래대금·업종 히트맵·시장폭·시장 투자자 수급), 2 신고가 보드(달성·근접·섹터·테마 히트맵·탐지·랭킹), 4 수급·스크리닝(6기준·투자자별·기관 7구분·종목 상세·장중 잠정), 10 ETF 수급(순유입·투자자별·유형별·괴리율·구성종목 변동), 상단 띠(P3 에 원천이 있는 칩) | 페이지 1 의 K200 야간선물·VKOSPI(GX — P7)·환율(ECOS — P5)·신용잔고(P5), 페이지 2 의 간밤 미국 신고가(P5), 상단 띠의 신용스프레드·신용잔고·수출 속보·한국 대 글로벌·GEX Flip(P5~P7) — 칩 자리에 '준비 중' |
| FastAPI `/api/market`·`/api/board`·`/api/flows`·`/api/etf`·`/api/auth`·`/api/health` + `POST /telegram/webhook`(P2 `WebhookHandler.handle` 를 감싼다) | 공개 API 경로(공개는 정적 사이트뿐), WebSocket 실시간(P7 ws-gateway), 쓰기 API(관심종목·알림 규칙 — P8) |
| 수집 처리기: `krx.daily`·`market.backfill`·`market.close_collect`·`flows.intraday`·**`market.intraday`(신규)**·`etf.collect`(P5 → P3) | KIS 웹소켓·GEX(P7), 금융위 시세 대조(`market.fsc_daily` — P5), 미국장(P5) |
| 엔진: `kbj/engines/board`(신고가 승격 — 골든), `kbj/engines/flows`(원장·검산 ①②·연속·스크리닝), `kbj/engines/market`(시장 거래대금·시장폭·업종·상단 띠), `kbj/engines/etf`(순유입·가격효과·검산 ③·분할·유형·괴리율·구성종목 변동) | board 의 LLM 서술(`ET:board/writer`·`engine/facts.py`)·백테스트·시그널(`systems`·`signals`·`backtest`·`search`·`ledger` — P8), 이벤트 스터디(flowlab — P8) |
| DB 0007~0009, 저장소 `kbj/store/repos/*`(Pg + 메모리 구현) | `pub_themes.*` 분류 사전 표(P5 — P3 은 `config/knowledge/*.yaml` 을 읽는다) |
| 공개 내보내기 `kbj/services/public_export`(pub_* 만), `pages.yml`(수동), 공개 빌드 스크립트 | Pages 실제 활성·배포 키(사용자) |
| 시험: 골든(board), 검산 ①②③(합성 원장), 장중 1시간 무결측 시뮬레이션, API·프런트 시험, CI 잡 | 실데이터 검산(키 받은 뒤 — CLI 만 만든다, §8.7) |

### 0.3 완료 기준과 증명

| PLAN §8 P3 완료 기준 | 증명 | 묶음 |
|---|---|---|
| **장중 1시간 무결측** | `tests/sim/test_intraday_hour.py`: 가짜 시계 2026-10-07(수) 09:55~11:05 KST, `flows.intraday`·`market.intraday` 실제 처리기 + 가짜 KIS. 슬롯 10:00~10:50 (6개) × 데이터셋 × venue 의 데이터 키가 **각각 `done` 1회**, 슬롯마다 저장 행 수 = 기대 행 수(결측 0), 슬롯 끝 전에 완료, 어떤 1초 창에도 KIS ≤ 4건, auth 밖 발급 0. 변형 4종(HTTP 500 1회·EGW00201 폭주·Redis 끊김 5초·스케줄러 재기동)에서도 결측 0(§8.3) | S(+C) |
| **신고가 보드 = 기존 board 산출**(골든) | `tests/golden/test_board_golden.py`: legacy 엔진으로 캡처한 합성 30거래일 골든(`tests/golden/board/`)과 `kbj.engines.board.build.compute_day` 결과를 필드 단위로 비교(실수 상대 1e-9·절대 1e-6, 순서·키 집합은 정확히). + 독립 오라클(flowlab `verify.check_newhigh`:132 이식) 속성 시험 "엔진 라벨 ⊆ 단순 루프"(§8.1) | E1 |
| **스크리닝·ETF 수급 검산 ①②③ 0 차이(합성)** | 합성 원장 생성기(`tests/fixtures/synthetic/ledger_gen.py`, 시드 고정) + hypothesis: ① 4구분 합 = 0, ② 기관 7구분 합 = 기관 합계, ③ 순자산 변화 = 순유입 + 가격효과 가 **잔차 0**. 깨진 행을 심으면 `invalid` 로 표시되고 집계에서 빠진다. metrics §5 표의 시험 전부(§8.2) | E2·E3 |
| (같은 기준 — 실데이터) | 키를 받은 뒤 `python -m kbj.services.engine.verify --date <D>` 가 실 DB 원장에 ①②③ 을 돌려 `docs/probe_results.md` 에 결과를 적는다(값은 적지 않고 건수·잔차 분포만) — **P3 완료는 합성으로, 실데이터는 키 수령 후 체크리스트 #20**(§8.7) | S |
| 프런트 eslint·tsc·vitest + 백엔드 ruff·pyright·pytest·lint-imports·check_canonical·check_public_safety 녹색, CI 녹색 | CI 잡 `web` 신설, `lint`·`test-kbj`·`sim`·`canonical`·`public-safety` 그대로 + 새 시험(§8.6) | S |

### 0.4 등록부(`config/jobs.yaml`)에서 바뀌는 것

| 작업 | 지금 | P3 뒤 |
|---|---|---|
| `krx.daily` | P3, 꺼짐 | **켜짐**. `writes` 에 `prv_market.stock_snapshot`·`prv_etf.etf_daily`·`prv_etf.meta`·`prv_market.eod_reconcile` |
| `market.backfill` | P3, 꺼짐(manual) | **켜짐**(수동). 날짜 범위 실행 CLI(§3.8) |
| `market.close_collect` | P3, 꺼짐 | **켜짐**. `collects` 에 `KIS:etf_investor_daily @ trade_date`(신규) |
| `flows.intraday` | P3, 꺼짐, `retry {max: 0}`, `deadline_min: 10` | **켜짐**, `retry {max: 2, backoff_s: [20, 40]}`, `deadline_min: 9`, `writes` 에 `prv_flows.investor_intraday`·`prv_market.turnover_rank_intraday` |
| `market.intraday` | (없음) | **신규·켜짐**: `equity {start: open, end: close, every_min: 10}`, `KIS:index_quote_intraday`·`KIS:sector_quote_intraday @ slot10m` |
| `board.daily` | owner `kbj.engines.board:daily`, `writes [prv_board.daily_state]` | **켜짐**, owner `kbj.services.engine.board:daily`, `writes [prv_board.alltime, prv_board.label, prv_board.split_check, prv_board.stock_day, prv_board.artifact]` |
| `board.confirm` | (없음) | **신규·켜짐**: `cron "40 8 * * 1-5"`, `trading_day`, `depends_on krx.daily(hard)`, 전 거래일 보드를 KRX 확정 일봉으로 다시 계산(D-P3-8) |
| `etf.collect` | P5 | **P3·켜짐**, owner `kbj.services.collectors.etf_holdings:run`, `writes [prv_etf.fund, prv_etf.holding, prv_etf.change_log]`, `notify` 는 지운다(ETF 리포트 발송은 P5) |
| `public.export` | (없음) | **신규·켜짐**: `cron "30 5 * * *"`, `always`, owner `kbj.services.public_export:run`, `collects` 없음(파일만 쓴다) |
| `brief.closing`·`flows.report`·`us.universe`·`us.eod`·`market.fsc_daily` | P3 | **P5 로**(꺼짐 유지, D-P3-17) |

등록부 검증(`tests/unit/scheduler/test_registry_validation.py`)의 "P2 에 켜진 작업 목록" 단언은 "단계별 켜진 작업 목록"(P2 셋 + 위 P3 9개)으로 바꾼다(S).

---

## 1. 모듈 목록

열: **경로 | 승격 원본(legacy 파일:함수:줄) | 공개 API | 시험(원본 옮김 / 새로)**. 원본 시험은 import 경로만 바꿔 옮기는 것이 원칙이고(P2 §1.11), 로그인 등급 입력은 합성(`tests/fixtures/synthetic/`)이다. 묶음 기호는 §9.

### 1.1 M — DB·저장소·행 자료형·설정·카탈로그 (웨이브 1)

| 경로 | 승격 원본 | 공개 API | 시험 |
|---|---|---|---|
| `kbj/core/rows.py` | 신규(ET `board/engine/db.py` 의 `px`·`snap`·`alltime` 행 모양:28~60, ET `etf_tracker_v9/tracker.py` DDL:71~84) | frozen dataclass: `Bar(code, date, open, high, low, close, volume, turnover, source, venue, quality)`, `Snap(code, date, name, market, kind, close, chg_pct, volume, turnover, turnover_is_estimate, mktcap, shares, status_flags, source, venue, quality)`, `InvestorDay(code, date, investor, net_value, net_qty, source, venue, quality)`, `IndexBar`, `EtfDay(code, date, name, close, nav, list_shrs, net_asset, turnover, volume, mktcap, base_index, source, venue, quality)`, `EtfQuote`, `AllTime`(ET 열 그대로), `Label`, `HoldingRow`, `Change` · `Investor`(StrEnum — 0003 주석의 12개 이름) · `INST7: tuple[Investor, ...]` | `tests/unit/core/test_rows.py`(frozen, naive 시각 거부, 금액 int) |
| `kbj/store/migrations/0007_board.sql` | ET `board/engine/db.py:DDL`:28(alltime·label·split_check) | §3.4 | `tests/test_store_layout.py` 확장(이름·멱등·접두사·hypertable 문장) |
| `kbj/store/migrations/0008_market_flows_p3.sql` | 신규 | §3.4 | 같음 |
| `kbj/store/migrations/0009_etf.sql` | ET `etf_tracker_v9/tracker.py` DDL:71~84(fund·holding·change_log) + P2 설계 §8.2 0013 계획 | §3.4 | 같음, 통합 `tests/integration/test_migrations_pg.py`(0001~0009 두 번 적용, `kbj_public_export` 가 새 `prv_*` 를 못 읽음) |
| `kbj/store/repos/{market,flows,board,etf}.py` | ET `board/engine/db.py`(`iter_series`:158·`series_for`:176·`all_series`:195·`trading_days`:204·`snapshot`:214·`sector_of`:233·`split_cleared`:113·`split_unknown`:123·`put_split_check`:133) — SQLite → Postgres | Protocol + `Pg*Repo(conn_factory)` + 메모리 구현(`kbj/store/repos/memory.py`). 예: `MarketRepo.upsert_daily_bars(rows: Sequence[Bar], *, loaded_by) -> int`, `.series(codes \| None, upto: date, n: int) -> dict[str, list[Bar]]`, `.snapshot(asof) -> tuple[dict[str, Snap], date \| None]`, `.universe(asof) -> list[UniverseRow]`; `FlowsRepo.upsert_investor_days(rows, *, revise: bool) -> RevisionReport`(덮어쓴 차이를 `investor_revision` 에 같은 트랜잭션으로), `.window(codes, end, n)`; `BoardRepo.alltime()`, `.put_alltime(...)`, `.labels(asof, basis)`, `.put_day(BoardDay)`, `.artifact(asof, name)`; `EtfRepo.etf_days(end, n)`, `.split_events()`, `.holdings(fund_id, asof)`, `.put_changes(...)` | `tests/unit/store/test_repos_memory.py`(메모리 구현의 규칙 — 우선순위·덮어쓰기·차이 기록), 통합 `tests/integration/test_repos_pg.py`(같은 시험을 Pg 로 — `@pytest.mark.integration`) |
| `kbj/store/redis_keys.py`(추가) | — | `web_session_key(digest)`, `web_login_fail_key(ip_digest)`, `WEB_LOGIN_LOCK`, `api_data_version_key(domain)` | `tests/unit/store/test_redis_keys.py` 확장 |
| `kbj/config/settings.py`(추가) | — | `web_user: str \| None`, `web_password_hash: SecretStr \| None`, `web_session_ttl_h: int = 12`, `web_cookie_secure: bool = True`, `web_dist_dir: Path = "web/dist-login"`, `public_export_database_url: SecretStr \| None`, `public_push_enabled: bool = False` | `tests/test_settings.py` 확장(repr 에 값 없음, 기본값) |
| `kbj/data/private/kis/datasets.py`(추가) | ET `board/ingest/kis.py:ENDPOINTS`:47, conflict_map §1.13 표(지수 `FHPUP02100000`·업종 `FHPUP02140000` [추정 TR]) | 데이터셋 3개 추가(§3.3) | `tests/unit/kis/test_kis_datasets.py` 확장 |
| `kbj/data/private/etf_issuers/__init__.py`·`datasets.py` | `kbj/data/catalog.py:PLANNED` 의 `ETF_ISSUERS:pdf` 를 옮긴다 | `DATASETS`(메타데이터만 — 클라이언트는 E3) | 카탈로그 겹침 시험 |
| `config/jobs.yaml`, `config/limits.yaml`(`etf_issuers: {rate: 1.0}` — 운용사 호스트별 scoped 리미터 [제안]), **`config/markets.yaml`**(신규 — §3.9), `config/calendar_events.yaml`(신규 — 금통위 날짜 [확인 필요]) | — | — | 등록부 검증·`python -m kbj.services.scheduler validate` |
| `pyproject.toml`·`uv.lock` | — | 런타임 의존성 `fastapi`·`uvicorn`(extras 없음) 추가. **계약 ⑧~⑩ 은 S 가 웨이브 3 에 넣는다**(소스 모듈이 생긴 뒤 — P2 R14) | `tests/test_import_contracts.py` 의 `RUNTIME_IMPORTS` 에 `fastapi`·`starlette`·`uvicorn` |
| `docs/metrics.md` §6~§8(신규 절), `docs/secrets.md`(새 이름 5개 — §5.4·§7.1), `.env.example` | metrics 는 "먼저 쓰고 구현"(CLAUDE §4) | §4.4 의 시장 지표, §4.1 의 신고가 정의 요약, §4.3 의 ETF 유형·분할 규칙을 이 절에 정본으로 | 문서 |

### 1.2 C — 수집 처리기·KIS 파서·KRX 백필 (웨이브 2)

| 경로 | 승격 원본 | 공개 API | 시험 |
|---|---|---|---|
| `kbj/data/private/kis/investors.py` | ET `board/ingest/kis.py`(`FIELD`:71, `_f`:165, `market_flows`:170, `stock_flows`:210, `top_flows`:238 — 단위 **백만원**, 셋 다 0 이면 질의 불성립 판정 :201~208), ET `monitor/flow/kissrc.py:fetch_daily`:48 | `parse_stock_investor(body, code) -> list[InvestorDay]`(FHKST01010900, 금액 백만원 → **원**(×1,000,000) [실측 필요: 단위]), `parse_market_investor(body, market) -> list[InvestorDay]`(FHPTJ04040000 `output1`), `parse_inst_foreign_total(body) -> list[InvestorDay]`(FHPTJ04400000, quality=estimated), `params_*()` · 셋 다 0 → `KisEmpty`(0 을 사실로 내보내지 않는다) | 옮김: ET `test_market_flows`(21)·`test_flows_parse`(14)·`test_stockflows` 의 KIS 부분 [추정 약 30], `monitor/flow/tests/test_kis`(14). 새로: 단위 변환, 4구분 중 빠진 구분은 행을 만들지 않음 |
| `kbj/data/private/kis/quotes.py` | SD `kis_api.py`(현재가), ET `board/ingest/kis.py:FIELD` | `parse_quote(body, code) -> Snap`(FHKST01010100 — 종가·등락·거래량·`acml_tr_pbmn`·시총·상태구분 `iscd_stat_cls_code` [실측 필요]) | 새로(합성) |
| `kbj/data/private/kis/ranks.py` | ET `board/ingest/kis.py:top_flows`:238 의 행 해석 방식 | `parse_volume_rank(body, ts) -> list[RankRow]`(FHPST01710000 — 거래대금 정렬 파라미터 [실측 필요]) | 새로 |
| `kbj/data/private/kis/etf.py` | — | `parse_etf_quote(body, code, ts) -> EtfQuote`(FHPST02400000 — `nav`·괴리율 필드 [실측 필요]) | 새로 |
| `kbj/data/private/kis/index.py` | SD `server.py:_fetch_kr_indices_live`:877 의 대체(conflict_map §1.13) | `parse_index_quote(body, code, ts)`(FHPUP02100000 [추정 TR]), `parse_sector_quotes(body, market, ts)`(FHPUP02140000 [추정 TR]) | 새로 |
| `kbj/services/collectors/krx_daily.py` | ET `board/ingest/pipeline.py`(`krx_regular_day`:67, `_apply_krx_snapshot`:121, `_apply_krx_bar`:153, `sync_universe`:190, `coverage_drop`:265 — 커버리지 하한 `COVERAGE_FLOOR`:254·`MIN_STOCKS`:262), ET `board/engine/kinds.py:of`:36 | `run(ctx) -> JobResult`(`krx.daily`): 선점한 키마다 `KrxClient` 호출 → `prv_market.daily_bar`·`stock_snapshot`(source `krx`)·`universe`·`prv_etf.etf_daily`·`prv_etf.meta` 쓰기, 빈 응답이면 `not_ready`, 커버리지 급감이면 실패(조용히 덮지 않음) → `reconcile_prev(ctx)`(§3.6) · `backfill(ctx)`(`market.backfill` — 같은 키·백필 예산·우선순위 P4) | 옮김: ET `test_close_source`(25) 중 KRX 확정 덮기 규칙 [추정 약 15], `test_snapshot_align`(6) [추정]. 새로: 가짜 KRX(`tests/fakes/krx_server.py`) — 공표 전 `not_ready`, 커버리지 가드, 대조 결과 |
| `kbj/services/collectors/reconcile.py` | ET `board/engine/build.py:close_provenance`:38(`CLOSE_CONFIRM_MIN` 0.9), `pipeline.py:CLOSE_DIFF_PCT`:64 | 순수 `reconcile(kis: Mapping[str, Snap], krx: Mapping[str, Snap], *, tol) -> list[ReconcileRow]` + 기록 | 새로: 같음·불일치·한쪽만 |
| `kbj/services/collectors/market_close.py` | ET `board/ingest/stockflows.py`(KIS 주 경로), SD `server.py:_refresh_flow_batch`:3045(대체) | `run(ctx)`(`market.close_collect`): 유니버스(전 거래일 KRX 기본정보 + 당일 신규)마다 FHKST01010100·FHKST01010900, 시장 2개 FHPTJ04040000, 가집계 FHPTJ04400000, ETF 목록 FHKST01010900(`KIS:etf_investor_daily`) — 우선순위 `close_collect`(P3). 장중 잠정 행을 **덮어쓰고 차이 기록**(D-P3-7) | 새로: 덮어쓰기·차이, 일부 종목 실패 시 그 키만 미완(재시도), 호출 수 = 기대 |
| `kbj/services/collectors/market_intraday.py` | — | `flows(ctx)`(`flows.intraday`), `market(ctx)`(`market.intraday`) — 슬롯마다 받은 행을 `*_intraday` 이력 표에 쌓고 일별 원장의 오늘 행(estimated)을 갱신 | 새로 |
| `tests/fakes/kis_server.py`(확장) | GX `tests/fakes/kis_server.py`(P2 승격본 — `synthetic_output`:149) | TR 추가: FHPTJ04400000·FHPST01710000·FHPST02400000·FHPUP02100000·FHPUP02140000 합성 출력(시드 = TR·파라미터·날짜·슬롯), `inject` 로 500·EGW00201 심기 | `tests/unit/fakes/` 확장 |
| `tests/fixtures/synthetic/kis/*.json` | — | 위 TR 응답 합성본 | — |

### 1.3 E1 — 신고가 엔진 승격 (웨이브 2)

| 경로 | 승격 원본 | 공개 API | 시험 |
|---|---|---|---|
| `kbj/engines/board/newhigh.py` | ET `board/engine/newhigh.py` 전체 410줄: `BASES`:24, `kinds`:30, `rank_of`:34, `displayable`:38, `split_guard`:50, `_key`·`_win`·`_max`·`_avg_vol`·`_gap`·`_refs_at`·`_resistance`·`_bucket`:77~173, **`evaluate`:181**, **`roll_alltime`:318**, `hist_ref_for`:362, `continuity`:381, `proximity_kind`:394 | 같은 이름·같은 반환 dict 키. `cfg` 는 `BoardConfig`(dict 처럼 읽히는 `Mapping` 호환 — 골든 비교용) | 옮김: ET `test_newhigh`(35 전부) |
| `kbj/engines/board/aggregate.py` | ET `board/engine/aggregate.py`: `_wavg`:17, `breadth`:32, `rollup`:46, `by_sector`:80, `by_theme`:90, `heatmap`:113, `detect`:149 | 같은 이름 | 옮김: 해당 시험 없음 → 새로 `test_aggregate.py`(미지 값 unknown, 히트맵 위아래 반씩) |
| `kbj/engines/board/rankings.py` | ET `board/engine/rankings.py`: `SECTOR_PERIODS`:38, `STOCK_BOARDS`:44, `sector_board`:107, `stock_board`:149, `mark_rank_delta`:167, `mark_cross`:199, `build`:217(state 파일 읽기) | 같은 이름 + **`build_rankings(universe_rows, prev_rankings, cfg, taxonomy) -> dict`**(파일 I/O 를 인자로) | 옮김: ET `test_rankings`(37) 중 엔진 부분 [추정 약 30 — `web.render` 를 부르는 나머지는 legacy 에 남긴다] |
| `kbj/engines/board/build.py` | ET `board/engine/build.py`: `close_provenance`:38, `_ret`:110, `_chg`:133, `_chg_src`:149, `unit_sanity`:166, `_turnover`:181, `_vol_ratio`:202, `_turnover_avg20`:223, `consistency_notes`:283, `by_mktcap`:377, `achieved_rows`:400, **`run`:425~727 의 본문** | **`compute_day(inp: BoardInputs, cfg: BoardConfig) -> BoardDay`** — `run` 에서 DB·파일 I/O(`DB.connect`·`write`·`read`·`conn.executemany`)만 뺀 같은 계산. `BoardDay(universe, newhigh, sectors, events, rankings, labels, notes, diagnostics)` 의 payload 는 `universe.json`·`newhigh.json`·`sectors.json`·`events.json`·`rankings.json` 과 같은 키 | 옮김: ET `test_turnover`(7), `test_consistency`(22), `test_mktcap_floor`(28) 중 엔진 [추정 약 20], `test_basis`(12) 중 엔진 [추정 약 8]. 새로: 골든(§8.1) |
| `kbj/engines/board/kinds.py` | ET `board/engine/kinds.py`: `of`:36, `common_name`:49, `counts`:59 | 같음 | 옮김: ET `test_kinds`(22) 중 render 제외 [추정 약 18] |
| `kbj/engines/board/themes.py` | ET `board/engine/themes.py`: `norm`:17, `display_index`:29, `near_names`:43, `build`:73, `primary`:142 | 같음(입력 yaml 은 `config/knowledge/themes.yaml`) | 옮김: ET `test_seeds`(13) |
| `kbj/engines/board/config.py` + `config/board.yaml` | ET `board/engine/config.py:load`:13·`themes`:19 + ET `board/config/settings.yaml` 의 `newhigh`·`proximity`·`volume`·`resistance`·`giveback`·`themes`·`display`·`integrity`·`detect`·`rankings` 절 | `BoardConfig.load(path=None) -> BoardConfig` | 새로: 모르는 키 거부. **숫자는 한 곳**: legacy `settings.yaml` 에서 이 절들을 지우고 legacy `config.load` 가 `config/board.yaml` 을 합친다 |
| `config/knowledge/{sectors,sector_map,themes}.yaml` | ET `board/knowledge/` 에서 `git mv`(자체 사전 — board48·62 테마, conflict_map §1.8) | (데이터) | legacy `test_sector_map`(12)·`test_seeds` 는 새 경로를 읽게 경로만 고친다 |
| `kbj/services/engine/board.py` | ET `board/ingest/pipeline.py:sync_px`:306 의 alltime 갱신 순서(`ALLTIME_SQL`:300 — 수집 후 `roll_alltime`), ET `board/engine/build.py:sync_label_kinds`:236·`_prev_labels`:264 | `daily(ctx) -> JobResult`(`board.daily` — KIS 마감 스냅으로 잠정 보드, quality estimated), `confirm(ctx)`(`board.confirm` — KRX 확정 일봉으로 전 거래일 다시 계산, 바뀐 라벨 수를 `detail` 에) | 새로: 메모리 저장소로 하루 → 다음 날 확정, 같은 as_of 재실행 멱등 |
| `tests/golden/board/`(`make_golden.py`, `inputs/*.json`, `expected/*.json`, `META.json`) | ET `board/tests/demo.py`(`_series`:41 합성 시세, `main`:143 — 시드 20260826) 방식 | §8.1 | §8.1 |
| `tests/oracles/newhigh_loop.py` | ET `flowlab/verify.py:check_newhigh`:132(단순 루프 독립 재현) | `labels_by_loop(series, asof, lookback) -> dict` | 속성 시험 |
| legacy shim | ET `board/engine/{newhigh,aggregate,rankings,kinds,themes}.py` → `from kbj.engines.board.<m> import *` 한 줄 + `__all__`. `build.py` 는 `run(db_path, …)` 만 남겨 sqlite 읽기 → `compute_day` → `write` | — | legacy board 시험(엔진 승격분 제외) 통과 + 다리 시험 1개 |

### 1.4 E2 — 시장·수급·스크리닝 엔진 (웨이브 2)

| 경로 | 승격 원본 | 공개 API | 시험 |
|---|---|---|---|
| `kbj/engines/flows/ledger.py` | metrics §0(일별 원장 하나), ET `monitor/flow/analyze.py:_sum`:57(없는 날은 0 으로 세지 않는다) | `build_ledger(snaps, bars, investors, universe, trading_days) -> Ledger` — 원천 우선순위(D-P3-7)로 (종목, 날짜)마다 한 행 `LedgerRow(code, date, market, kind, close, chg_pct, turnover, mktcap, foreign, inst, other_corp, indiv, inst7 \| None, traded: bool, flags, quality, sources)` · `Ledger.window(code, end, n) -> WindowAgg(sum_turnover, avg_turnover, n_traded, sums_by_investor)` | 새로: 1·5·20일 합 = 일별 합(휴장·거래정지 낀 가짜 캘린더) |
| `kbj/engines/flows/checks.py` | metrics §2 검산 ①②, ET `monitor/flow/analyze.py:reconcile`:116 | `check1(row) -> int \| None`(4구분이 모두 있을 때만 잔차, 하나라도 없으면 `None` = 검산 불가 — 지어내지 않는다), `check2(row) -> int \| None`, `apply_checks(ledger) -> tuple[Ledger, list[CheckFailure]]`(실패 행 `invalid`) | 옮김: ET `monitor/flow/tests/test_analyze`(17) 중 reconcile·buy_days [추정 약 8 — 오라클로]. 새로: 속성 시험(§8.2) |
| `kbj/engines/flows/streak.py` | metrics §2 "연속 순매수"(정본). 대체되는 3벌: SD `server.py:_flow_streak`:14918, ET `monitor/kr/flows.py:windows`:84(종목 휴장일을 건너뜀), ET `monitor/flow/analyze.py:buy_days`:68 | `streak(rows_desc: Sequence[LedgerRow], who) -> int` — 0원·순매도·거래정지일에서 끊긴다, 오늘 정지면 0 | 새로: metrics §5 '연속일' |
| `kbj/engines/flows/screen.py` | metrics §3 표, SD `server.py:_analyze_flow_signals`:14931(쌍끌이·연속 — 기능 참고) | `screen(ledger, *, mode: Literal["value","foreign","inst","both","streak","spike"], market: Literal["all","KOSPI","KOSDAQ"], period: Literal[1,5,20], min_avg_turnover: int, include_flagged=False, newhigh: Mapping[str, str] \| None = None, limit=100) -> ScreenResult` | 새로: 각 기준 정렬·필터, 관리·정지·정리매매 기본 제외, 우선주 별도, 급증 10일 미만 계산 안 함 |
| `kbj/engines/market/turnover.py` | metrics §1·§6 | `market_turnover(ledger, day) -> Sourced[int]`(코스피+코스닥 주식만), `intraday_market_turnover(index_quotes) -> Sourced[int]`(estimated), `turnover_series(ledger, end, n=20)` | 새로 |
| `kbj/engines/market/breadth.py` | ET `board/engine/aggregate.py:breadth`:32(재사용 — import) | `market_breadth(ledger, day, labels) -> Breadth(up, flat, down, unknown, above_ma20_pct, newhigh_n, limit_up, limit_down)` | 새로 |
| `kbj/engines/market/sectors.py` | SD `server.py:_scrape_naver_sectors`:3232 의 대체(KRX 업종지수) | `sector_heat(index_bars, intraday, codes, period) -> list[SectorCell]` | 새로 |
| `kbj/engines/market/ribbon.py` | v0.2 `docs/reference/plan_kr_v0.2.md`:60~64(반도체 쏠림·경기민감 대 방어) | `ribbon(inputs, now, cal) -> list[Chip]`(칩마다 `Sourced`) | 새로 |
| `tests/fixtures/synthetic/ledger_gen.py` | metrics §5 "합성 원장 생성기는 `tests/fixtures/synthetic/`" | `generate(seed, *, days, n_stocks, n_etfs, halts, splits, distributions, listings) -> SyntheticMarket` | 자기 시험(결정성·불변식) |

### 1.5 E3 — ETF 수급 엔진·운용사 어댑터 (웨이브 2)

| 경로 | 승격 원본 | 공개 API | 시험 |
|---|---|---|---|
| `kbj/engines/etf/flows.py` | metrics §4 | `daily_flow(prev: EtfDay \| None, cur: EtfDay, split: SplitEvent \| None) -> EtfFlow(net_inflow, price_effect, net_asset_chg, residual, status: Literal["ok","new","split_adjusted","invalid"])` · `window_flows(days, n)` · `by_type(flows, meta) -> list[TypeRollup]` · `check3(flow, tol) -> bool` | 새로: metrics §4 오류 1~5번 기대값 |
| `kbj/engines/etf/splits.py` | metrics §4 오류 1번 | `detect_split(prev, cur, *, tol=0.02, ratios=(2,3,4,5,10,20,50,100)) -> SplitCandidate \| None`, `adjust(prev, ratio) -> EtfDay` | 새로: 1:10 분할일 순유입 0, 병합(1/k), 오탐(큰 설정) |
| `kbj/engines/etf/types.py` | ET `etf_tracker_v9/themes.py`: `EXCLUDE`:6, `RULES`:13, `BRANDS`·`ISSUER_OF_BRAND`, `strip_brand`:70, `brand_of`:77, `issuer_of`:84, `is_excluded`:88, `is_active`:92, **`classify`:96**, `tag`:109 | 같은 이름 + **`etf_type(name, base_index) -> EtfType`**(§4.3) | 새로: Q8 비교 시험(ET `monitor/kr/etf.py:classify`:109 와의 차이는 기록만), 유형 규칙 표 |
| `kbj/engines/etf/premium.py` | metrics §4 괴리율 | `premium_pct(price, nav) -> float`, `alerts(rows, thresholds) -> list[PremiumRow]` | 새로 |
| `kbj/engines/etf/holdings.py` | ET `etf_tracker_v9/tracker.py`: `QTY_FLOOR`:63(100)·`ACTION_PP`:64(2.0 — 옛 환경변수 `_envnum` → `config/markets.yaml`), **`fund_pairs`:251**(펀드별 최근 두 스냅, 공백 14일 상한), **`analyze`:274**(신규·제외·TOP10 진입/이탈·CU 재산정 보정 중앙값 후 ±2%p) | `fund_pairs(dates_by_fund, max_gap=14)`, `analyze(cur, prev, *, depth, etf_codes, qty_floor, action_pp) -> list[Change]` | 새로(원본 시험 0 — conflict_map §1.16): CU 보정, 5종목 미만 보정 없음, TOP10 깊이 |
| `kbj/data/private/etf_issuers/{base,kodex,tiger,timefolio,sol,ace,hanaro,koact,plus,rise}.py` | ET `etf_tracker_v9/collectors.py`: `_session`:19, `_rows_from_table`:38, `isin_to_code`:59, `Kodex`:74, `Tiger`:111, `TimeFolio`:154, `Sol`:186 / `adapters/ace.py:Ace`:92, `hanaro.py:Hanaro`:71, `koact.py:KoAct`:23, `plus.py:Plus`:31, `rise.py:Rise`:27. **제외**: `NaverTop10`:226 | `IssuerAdapter`(Protocol: `KEY`, `NAME`, `DEPTH`, `universe() -> list[FundRef]`, `holdings(fund_key, date) -> tuple[dict[str, HoldingRow], date]`) · httpx + `kbj.data.http`(시간 제한·크기 상한) + scoped 리미터 `etf_issuers` | 새로: 운용사마다 합성 응답(HTML 조각·JSON) 파서 시험 |
| `kbj/services/collectors/etf_holdings.py` | ET `etf_tracker_v9/tracker.py`: `build_universe`:109, `snapshot`:188(빈 응답 연속 `empty_streak`) | `run(ctx)`(`etf.collect`) — 어댑터마다 격리(한 운용사 실패가 다른 운용사를 멈추지 않음), 끝에 `analyze` → `prv_etf.change_log` | 새로: 가짜 운용사 서버 |
| legacy shim | ET `etf_tracker_v9/themes.py` → `from kbj.engines.etf.types import *`, `tracker.py` 의 `fund_pairs`·`analyze` → kbj 호출, `collectors.py` 의 운용사 클래스 → kbj 다시 내보내기(나머지 render·report·dash 는 P5 까지 legacy) | — | `scripts/test_legacy.sh etf`(모듈 13개 import) |

### 1.6 A — API·로그인·웹훅 (웨이브 2)

| 경로 | 승격 원본 | 공개 API | 시험 |
|---|---|---|---|
| `kbj/services/api/app.py` | GX `docs/phase4_design.md`(FastAPI 설계 — 참고), SD `server.py` 라우트(옮기지 않음 — 인증 없는 쓰기 API·정적 catch-all `/<path:filename>`:4573 제외, conflict_map §1.16) | `create_app(settings, *, redis, repos, now) -> FastAPI` — 라우터·미들웨어(보안 헤더·Origin·CSRF)·정적 파일(`settings.web_dist_dir`, `html=True`) | `tests/unit/api/test_app.py` |
| `kbj/services/api/auth.py` | kbj `kbj/services/notifier/webhook.py:secret_ok`:92 의 상수 시간 비교 방식(SD 원본 웹훅은 P2 에 삭제 — `server.py`:5811 주석) | `hash_password(pw, *, n=2**15, r=8, p=1) -> str`(`scrypt$…` 형식), `verify_password(pw, encoded) -> bool`(상수 시간), `SessionStore(redis, ttl, now)` `.create(user) -> (sid, csrf)`, `.get(sid)`, `.drop(sid)` · 의존성 `require_session` | `test_auth.py`(§5.4 표 전부) |
| `kbj/services/api/routes/{market,board,flows,etf,auth,health,webhook}.py` | §5.3 | 라우트 | `test_routes_*.py` — 세션 없이 401, 봉투 필드, 쿼리 검증 |
| `kbj/services/api/readers/{market,board,flows,etf}.py` | — | 저장소 → 엔진(순수) → 응답 모델. 예: `flows_screen(repos, q: ScreenQuery, now) -> Envelope[ScreenResponse]` | 메모리 저장소 + 합성 원장 |
| `kbj/services/api/models/*.py` | `kbj/core/quality.py:Sourced`·`Quality` | `Envelope[T](source, as_of, quality, notes, generated_at, data: T)` + 영역별 모델(§5.2) | `test_models.py`(모든 응답 모델에 source·as_of·quality) |
| `kbj/services/api/cache.py` | — | `DataVersionCache(redis, ttl_s)` — 키 `(경로, 쿼리, data_version)` | 새로 |
| `kbj/services/api/__main__.py` | — | `python -m kbj.services.api [serve \| openapi [--check] \| hash-password]` | `test_cli.py`(`hash-password` 가 stdout 외에 값을 남기지 않음) |
| `web/src/api/openapi.json`, `web/test/fixtures/api/*.json` | — | A 가 만들어 커밋(합성 예시 응답). W2 가 TS 타입을 생성 | `openapi --check` 가 낡았으면 실패 |
| `kbj/services/notifier/commands.py`(작은 변경) | P2 §5.7 '준비 중(P3)' | `/신고가`·`/수급 <종목>` 을 같은 readers 로 [제안 — 늦으면 P4] | `tests/unit/notifier/test_commands.py` 확장 |

### 1.7 X — 공개 내보내기·Pages (웨이브 2)

| 경로 | 승격 원본 | 공개 API | 시험 |
|---|---|---|---|
| `kbj/services/public_export/{__init__,__main__,export,calendar_json,events_json,manifest,push}.py` | DATA_TIERS §3, ADR 0002 §2.2 | `run(ctx) -> JobResult`(`public.export`), `export_all(conn_pub, out_dir, *, now, cal) -> Manifest`, `push_public_data(out_dir, *, enabled, remote) -> PushResult`(기본 꺼짐) · `python -m kbj.services.public_export build --out <dir>` | §7.4 |
| `.github/workflows/pages.yml` | — | §7.3 | 워크플로 YAML 정적 시험(`tests/test_workflows.py` — 예약 없음·권한 최소) |
| `scripts/build_public_site.sh` | — | 공개 빌드 + 데이터 합치기 + 번들 검사 | CI 에서 `--dry-run` |

### 1.8 W·W2 — 프런트

§6 에 구조·위젯·시험을 적는다. W(웨이브 1)는 셸·디자인 토큰·UI 부품·데이터 소스 계층·빈 자리 페이지, W2(웨이브 3)는 페이지 1·2·4·10 위젯과 상단 띠.

### 1.9 S — 시뮬레이션·CI·legacy 정리·통합 (웨이브 3)

| 경로 | 내용 |
|---|---|
| `tests/sim/harness.py`(확장), `tests/sim/test_intraday_hour.py`, `test_intraday_day.py`(느림 표시), `test_one_day_p3.py` | §8.3 |
| `config/jobs.yaml` | P3 작업 `enabled: true` 로 뒤집기(처리기 import 가능 검증) |
| `pyproject.toml` | import-linter 계약 ⑧·⑨·⑩(§7.4·D-P3-5) |
| `.github/workflows/ci.yml` | 잡 `web` 신설, `test-kbj` 에 골든·API·openapi 검사 포함(§8.6) |
| `Dockerfile`, `docker-compose.yml` | node 빌드 단계(`web/dist-login` 을 이미지에), 서비스 `api`(127.0.0.1:8000), `scheduler` 에 KIS·KRX 키(ADR 0004 "P3 에 scheduler 에도") |
| `scripts/canonical_baseline.txt`, `scripts/check_canonical.py` | §1.10 삭제분만큼 줄인다. 새 그룹 `etf_issuers`(운용사 호스트 — kbj 안은 `kbj/data/private/etf_issuers/**` 만 허용, legacy 는 기준선) |
| legacy 삭제·고침 | §1.10 |
| `docs/PLAN.md` P3 결과, `docs/probe_results.md` §7 #20~#27, ADR 0008~0012 확정본 | 문서 |

### 1.10 legacy 이전 범위 — 네이버·KRX 스크랩·옛 환경변수 관문 (D-P3-18)

기준: **P3 의 kbj 작업이 그 기능을 대체했고, 파일 단위로 떼어낼 수 있는 것만** 지운다. 지울 파일의 시험도 함께 지우되, KIS 부분처럼 의미가 남는 시험은 kbj 로 옮긴다(§1.2·§1.11). legacy board `run.py` 는 ingest 를 **함수 안에서 늦게 import** 한다(ET `board/run.py`:64) — 지운 모듈을 부르는 명령(`cmd_init`:158·`cmd_daily`:253 의 수집부·`cmd_stockflows`:361·`cmd_kis_probe`:1446)만 같이 지운다.

| legacy 기능 (파일:함수) | 네이버·스크랩·옛 관문 | P3 kbj 대체 | P3 처리 | 기준선 변화 [추정] |
|---|---|---|---|---|
| ET board KR 수집: `ingest/naver.py`(15), `flows.py`(네이버 6·스크랩 2), `stockflows.py`(네이버 1, 관문 `creds.has('KIS_APP_KEY')`:258), `funds.py`(2), `pipeline.py`(네이버 1·스크랩 1, 관문 `creds.has('KRX_API_KEY')`:78·KIS :477), `datago.py`(datago 1), `kis.py`·`krx.py`(P2 브리지본) | 네이버·스크랩·옛 관문 3곳 | `krx.daily`·`market.backfill`·`market.close_collect`·`flows.intraday`·`board.daily` | **삭제** + `run.py` 의 해당 명령 삭제, `config/settings.yaml` 의 네이버 줄(1) | naver −26, krx_scrape −3, datago −1 |
| ET board 엔진 | — | `kbj/engines/board` | **shim**(§1.3) | — |
| ET board `run.py:cmd_kis_probe`:1446(관문 `creds.has('KIS_APP_KEY')`:1457) | 옛 관문 | (실측은 `scripts/` 의 kbj 실측 도구 — P2 설계 §7 체크리스트) | **삭제** | — |
| ET `etf_tracker_v9/market.py`(네이버 3 — ETF 목록·시세 `fetch_list`:44·`fetch_hist`:104), `collectors.py:NaverTop10`(4), `verify.py`(4), `tracker.py:naver_names`:101(2) | 네이버, 옛 환경변수 `QTY_FLOOR`·`ACTION_PP`(`_envnum`:55) | KRX `etp/etf_bydd_trd`(`krx.daily`), `etf.collect`, `config/markets.yaml` | **삭제**(market·verify·NaverTop10·naver_names), 운용사 클래스·themes·analyze 는 shim | naver −13 |
| ET `flowlab/naver.py`(네이버 4·스크랩 1), `krx.py`(스크랩 5, 옛 `KRX_ID`·`KRX_PW`:27), `probe_*.py` 7개(네이버 43·datago 1), `config.py`(네이버 3) | 네이버·스크랩·옛 환경변수 | `market.backfill`·`market.close_collect`. `verify.check_newhigh` 는 kbj 오라클(§1.3) | 수집·진단 파일 **삭제**. `eventstudy.py` 와 selftest(합성)는 P8 까지 legacy [추정 — selftest 44 중 수집 의존분은 S 가 확인] | naver −50, krx_scrape −6, datago −1 |
| ET `monitor/flow/krx.py`(스크랩 5, 옛 `KRX_ID`·`KRX_PW`:104), `capture.py`, `kissrc.py` | 스크랩·옛 환경변수 | `market.close_collect`(KIS — Q7) | **삭제**(`tests/test_krx` 삭제, `test_kis` 는 kbj 로 옮김). `analyze`·`chart`·`narrative`·`telegram` 은 `flows.report`(P5) 까지 legacy | krx_scrape −5 |
| SD `ohlcv_autofill.py`(네이버 2·스크랩 8), `ohlcv_5y_collector.py`(스크랩 2), `data_fetcher.py`(네이버 1·스크랩 12 — 폐지 작업 `market_update`·맥 `data_fetcher`), `scripts/probe_naver_*.py` 3개·`probe_ohlcv_*.py` 2개(네이버 35·스크랩 6), `scripts/check_ohlcv_autofill.py` | 네이버·pykrx | `market.backfill`·`krx.daily` | **삭제**(SD 검사 래퍼 13 → 12 [추정]) | naver −38, krx_scrape −28 |
| SD `server.py`(네이버 32·스크랩 35: 신고가 `_kr_new_highs_from_charts`:10708 등, 수급 `_fetch_naver_trend`:2812·`api_flow`:3144, 업종 `_scrape_naver_sectors`:3232, 지수 `_fetch_kr_indices_live`:877, 시세 `_fetch_naver_live_prices`:816), `data_freshness.py`(1), `krx_api.py`(1) | 네이버·pykrx, 텔레그램 cron 관문 `TELEGRAM_BOT_TOKEN` | 페이지 1·2·4 | **P4 로**(종목 상세가 SD 화면을 대체할 때 함수 단위 삭제 — SD 검사 스크립트 `check_new_high_logic`·`check_newhigh_full_list`·`check_newhigh_flow` 도 그때 퇴역) | — |
| ET board `ingest/triggers.py`(네이버 2, 관문 `creds.has('DART_API_KEY')`:337), `news.py`(1), `web/*`(2), SD `consensus_*`·`theme_crawler`·`valuechain`·`agents` | 네이버 검색 API·옛 관문 | — | **P4·P5**(공시 트리거·뉴스·테마 사전·컨센서스) | — |
| 합계 | | | | naver 176 → 약 49, krx_scrape 81 → 약 39(SD `server.py` 35·`data_freshness` 1·`krx_api` 1·`pyproject` 2), datago 2 → 0 |

### 1.11 옮기는 원본 시험 수 [추정 — 함수 단위로 나눌 때 바뀐다]

| 묶음 | 옮김(kbj 로) | 삭제(legacy — 대체됨) | 남김(legacy, shim 통해) |
|---|---|---|---|
| E1 | `test_newhigh` 35, `test_turnover` 7, `test_consistency` 22, `test_seeds` 13, `test_rankings` 약 30, `test_kinds` 약 18, `test_mktcap_floor` 약 20, `test_basis` 약 8 ≈ **153** | — | render·db 부분(`test_live_board` 27·`test_site` 14·`test_banner` 7·`test_series_read` 10·`test_sector_map` 12 등) |
| C | `test_market_flows` 21, `test_flows_parse` 14, `test_stockflows` 약 30, `test_close_source` 약 15, `monitor/flow test_kis` 14 ≈ **94** | `test_stale_px` 26, `test_snapshot_align` 6, `test_data_version` 10, `test_units` 8, `test_universe` 25, `test_naver_sectors` 13, `test_ingest` 11, `test_kis_call` 32(호출 규칙은 P2 `KisRestClient` 시험이 덮는다), `test_stockflows`·`test_close_source` 나머지, `monitor/flow test_krx` | — |
| E2 | `monitor/flow test_analyze` 중 약 8(오라클) | — | 나머지 analyze 시험(P5) |
| E3 | 0(원본 시험 없음) | — | — |

legacy board 는 1,267 → 약 900 [추정](S 가 실측해 PLAN P3 결과에 적는다).

---

## 2. 공개/로그인 배치

| 층 | 공개(정적 Pages) | 로그인(VM) |
|---|---|---|
| 데이터 | `pub_*` 만 — P3 는 표를 읽지 않고 캘린더(코드)·`config/calendar_events.yaml`(금통위 날짜 — 한국은행 공표 일정, 공개) | `prv_*` 전부 + `pub_*` |
| 서버 | 없음 — `public-data` 브랜치 JSON + SPA 공개 빌드 | `api` 서비스(FastAPI) |
| 코드 | `kbj/services/public_export/**` — 계약 ⑧: `kbj.data.private`·`kbj.engines`·`kbj.services.{api,collectors,engine}`·`kbj.store.repos` import 금지, SQL 은 `pub_` 스키마만(정적 시험) | `kbj/services/api/**` — 계약 ⑩: `kbj.data.private`·`kbj.services.collectors` import 금지(외부 호출 없음) |
| DB 역할 | `kbj_public_export` 구성원 로그인 역할(읽기 전용) — `KBJ_PUBLIC_EXPORT_DATABASE_URL` | 앱 역할 |
| 프런트 | `VITE_KBJ_TIER=public` — 로그인 위젯은 자물쇠, 시세 자리는 TradingView 위젯 | `VITE_KBJ_TIER=login` |

P3 의 새 표는 모두 `prv_*` 다(원천 KIS·KRX·운용사). 시세로 계산한 지표(시장폭·쏠림)도 등급은 로그인이다(DATA_TIERS §2 — 시세 기반 기능 전부 로그인). v0.2 의 "계산된 지표는 공개 가능 [확인 필요]" 는 P3 에서 열지 않는다(애매하면 로그인).

---

## 3. 데이터 흐름

### 3.1 한눈에

```
(KST)  05:30 public.export ─────────────────────────────────────────► public-data 브랜치 → Pages(수동)
       08:05 krx.daily ── KRX sto/idx/etp @ 전 거래일 ──► prv_market.daily_bar·stock_snapshot(krx)·universe
                    │                                     prv_etf.etf_daily·meta
                    └─ reconcile(전날 KIS 마감 vs KRX) ──► prv_market.eod_reconcile, KIS 행 quality
       08:40 board.confirm ── KRX 확정 일봉으로 전 거래일 보드 다시 ─► prv_board.*(quality ok)
       08:00 etf.collect ── 운용사 PDF ─► prv_etf.fund·holding ─► engines.etf.holdings ─► prv_etf.change_log
 09:00~15:20 market.intraday (10분) ── KIS 지수·업종 ─► prv_market.index_intraday·sector_intraday
 09:00~15:20 flows.intraday  (10분) ── KIS 가집계·거래대금 순위·ETF 현재가
                    ─► prv_flows.investor_intraday·prv_market.turnover_rank_intraday·prv_etf.quote_intraday (이력)
                    ─► prv_flows.stock_investor_daily·prv_market.stock_snapshot 오늘 행 (estimated)
       15:35 market.close_collect ── KIS 종목 현재가·투자자·시장 투자자·가집계·ETF 투자자
                    ─► 같은 오늘 행을 덮어씀(ok) + prv_flows.investor_revision(차이)
       16:20 board.daily ── 일봉(어제까지 krx + 오늘 kis) ─► engines.board.compute_day ─► prv_board.*(estimated)
  (요청 때) api readers ── repos ─► engines.flows/market/etf(순수) ─► Envelope[...] ─► SPA 위젯
```

### 3.2 작업 표 (P3 에 켜는 것)

| 작업(등록부) | 트리거·조건 | 수집 데이터 키(source:dataset @ as_of [venue]) | 쓰는 표 | 재시도·마감 | 소유 모듈 |
|---|---|---|---|---|---|
| `krx.daily` | `5 8 * * 1-5`, T, 10분마다 10:00 까지(그대로) | `KRX:sto/stk_bydd_trd`·`ksq_bydd_trd`·`knx_bydd_trd`[KRX], `sto/stk_isu_base_info`·`ksq_isu_base_info`, `idx/kospi_dd_trd`·`kosdaq_dd_trd`·`krx_dd_trd`, `etp/etf_bydd_trd`[KRX]·`etn_bydd_trd`[KRX] @ prev_trading_day | `prv_market.daily_bar`·`stock_snapshot`·`universe`·`eod_reconcile`, `prv_etf.etf_daily`·`meta` | every 600 s until 10:00 | `kbj.services.collectors.krx_daily:run` |
| `market.backfill` | manual(`run-once`·범위 CLI) | 같은 키 @ 과거 날짜(`backfill_of: [krx.daily]`) | 같음 | 2×600 s, 예산 `krx.backfill_cap` | `…krx_daily:backfill` |
| `board.confirm` (신규) | `40 8 * * 1-5`, T, `depends_on krx.daily(hard)` | — (DB 만) | `prv_board.*` | 2×600 s | `kbj.services.engine.board:confirm` |
| `etf.collect` (P5→P3) | `0 8 * * 1-5`, T | `ETF_ISSUERS:pdf @ trade_date` | `prv_etf.fund`·`holding`·`change_log` | 3×900 s | `kbj.services.collectors.etf_holdings:run` |
| `market.intraday` (신규) | `equity {open~close, every_min: 10}`, T | `KIS:index_quote_intraday`·`KIS:sector_quote_intraday @ slot10m` | `prv_market.index_intraday`·`sector_intraday` | 2×(20·40 s), 마감 9분 | `kbj.services.collectors.market_intraday:market` |
| `flows.intraday` | `equity {open~close, every_min: 10}`, T | `KIS:inst_foreign_intraday`[venue]·`turnover_rank_intraday`[venue]·`etf_quote_intraday @ slot10m` | `prv_flows.investor_intraday`·`stock_investor_daily`, `prv_market.turnover_rank_intraday`·`stock_snapshot`, `prv_etf.quote_intraday` | 같음 | `…market_intraday:flows` |
| `market.close_collect` | `equity {start: close+5}`, T | `KIS:stock_quote_eod`[venue]·`stock_investor_daily`[venue]·`market_investor_daily`[venue]·`inst_foreign_top`[venue]·**`etf_investor_daily`[venue]** @ trade_date | `prv_market.stock_snapshot`, `prv_flows.stock_investor_daily`·`market_investor_daily`·`investor_revision` | 5×300 s | `kbj.services.collectors.market_close:run` |
| `board.daily` | `20 16 * * 1-5`, T, `depends_on market.close_collect(hard)` | — | `prv_board.*` | 2×600 s | `kbj.services.engine.board:daily` |
| `public.export` (신규) | `30 5 * * *`, always | — | (파일) | 1×600 s | `kbj.services.public_export:run` |

[venue] = `config/markets.yaml` `kis.venues`(기본 `[KRX]` — D-P3-9)로 `kbj.data.catalog.keys_for(spec, as_of, venues)` 가 펼친다.

### 3.3 새·바뀐 데이터셋 (카탈로그)

| id | 출처 TR·엔드포인트 | as_of | 저장 표 | venues | 비고 |
|---|---|---|---|---|---|
| `KIS:index_quote_intraday` (신규) | FHPUP02100000 국내업종 현재지수 [추정 TR — conflict_map §1.13] | slot10m | `prv_market.index_intraday` | — | 코스피(0001)·코스닥(1001)·코스피200(2001) [실측 필요: 코드]. 누적거래대금 필드로 장중 시장 거래대금(§4.4) |
| `KIS:sector_quote_intraday` (신규) | FHPUP02140000 업종 구분별 전체시세 [추정 TR] | slot10m | `prv_market.sector_intraday` | — | 시장 2회 호출로 전 업종 |
| `KIS:etf_investor_daily` (신규) | FHKST01010900 을 ETF 코드로 [실측 필요: ETF 에 되는지] | trade_date | `prv_flows.stock_investor_daily` | KRX·NXT·TOTAL | 순자산 하한(`config/markets.yaml` `etf.investor_min_net_asset` 1,000억 [확인 필요]) 이상 ETF 만 |
| `ETF_ISSUERS:pdf` | 운용사 9곳 | trade_date | `prv_etf.holding` | — | `PLANNED` → `kbj/data/private/etf_issuers/datasets.py` |
| `KIS:etf_quote_intraday` (바뀜) | FHPST02400000 | slot10m | `prv_etf.quote_intraday` | — | 대상 = 순자산 상위 `etf.watch_top_n` 50 [확인 필요](전 ETF 는 슬롯당 900여 건 — §3.7) |
| `KIS:inst_foreign_intraday`·`inst_foreign_top` (바뀜) | FHPTJ04400000 | slot10m·trade_date | `prv_flows.investor_intraday`(장중 이력)·`stock_investor_daily`(일별 원장, source `kis.prelim`) | 그대로 | 표 확정(카탈로그 notes 의 '[확인 필요] 저장 표 P3') |

### 3.4 마이그레이션 0007~0009 (DDL 요약)

공통 규칙(0003 과 같다): 모든 값 행에 `source`·`quality`(ok·stale·estimated·invalid)·`received_at`·`loaded_by`, 종목코드 `text`, KR 금액 **원 단위 정수**(`bigint` 또는 `numeric`), `venue` 열(`'' \| KRX \| NXT \| TOTAL`), `CREATE … IF NOT EXISTS`·`create_hypertable(if_not_exists)`, 적용 뒤 고치지 않음. 0001 의 `ALTER DEFAULT PRIVILEGES` 는 `pub_*` 에만 걸려 있어 `prv_*` 새 표는 `kbj_public_export` 가 못 읽는다(통합 시험으로 확인).

| 파일 | 표 | 키 | 주요 열 | hypertable·보존 |
|---|---|---|---|---|
| **0007_board** | `prv_board.alltime` | `(market, code)` | `hi`, `hi_date`, `cl`, `cl_date`, `prev_hi`, `prev_cl`, `first_date`, `last_date`, `n_days`, `suspect`, `suspect_date`, `suspect_note`, `history_from date`(이력 시작 — D-P3-11), `source`, `quality`, `updated_at`, `loaded_by` (ET `db.py` DDL :46~55 열 그대로 + 2) | 아님 |
| | `prv_board.label` | `(market, code, trade_date, basis)` | `kind`(hist·w52·d60·w52_low — US 용), `rank smallint`, `quality`, `source` | `trade_date` 365일 |
| | `prv_board.split_check` | `(market, code)` | `jump_date`, `verdict`(action·none·unknown), `note`, `updated_at` | 아님 |
| | `prv_board.stock_day` | `(market, code, trade_date)` | ET `build.run` 의 universe 행 핵심 열(`close`·`chg_pct`·`turnover`·`turnover_is_estimate`·`mktcap`·`label`·`near_kind`·`near_gap`·`status`·`suspect`·`sector`·`theme`·`ret_*`·`vol_mult`) + `extra jsonb`(나머지 키), `quality`, `source`(`kis`·`krx`) | `trade_date` 365일, 압축 30일 뒤 [제안] |
| | `prv_board.artifact` | `(market, trade_date, name)` | `name` ∈ universe_meta·newhigh·sectors·events·rankings, `payload jsonb`, `engine_version`, `input_digest`, `as_of timestamptz`, `quality`, `source`, `generated_at` | 아님(하루 5행) |
| **0008_market_flows_p3** | `prv_market.index_intraday` | `(index_code, ts, source)` | `name`, `value`, `chg_pct`, `turnover`, `volume`, `quality`(estimated) | `ts` 1일, 보존 90일 [제안] |
| | `prv_market.sector_intraday` | `(market, sector_code, ts, source)` | `name`, `value`, `chg_pct`, `turnover` | 같음 |
| | `prv_market.turnover_rank_intraday` | `(market, ts, rank, venue)` | `code`, `name`, `turnover`, `chg_pct`, `source`, `quality` | 같음 |
| | `prv_market.eod_reconcile` | `(trade_date, code, field)` | `kis_value`, `krx_value`, `diff_pct`, `verdict`(ok·mismatch·missing_kis·missing_krx), `checked_at` | `trade_date` 365일 |
| | `prv_flows.investor_intraday` | `(code, ts, investor, venue)` | `net_qty`, `net_value`, `rank`, `source`(`kis.prelim`), `quality`(estimated) | `ts` 1일, 보존 90일 |
| | `prv_flows.investor_revision` | `(code, trade_date, investor, venue)` | `est_value`, `est_ts`, `final_value`, `final_source`, `diff`, `revised_at` | `trade_date` 365일 |
| | `prv_flows.ledger_check` | `(domain, trade_date, code, check_id)` | `domain`(stock·etf), `check_id`(c1·c2·c3), `residual`, `detail jsonb`, `checked_at` | 아님 |
| **0009_etf** (P2 계획 0013 당김) | `prv_etf.etf_daily` | `(code, trade_date, source, venue)` | `name`, `close`, `nav`, `list_shrs bigint`, `net_asset`, `turnover`, `volume`, `mktcap`, `base_index`, `quality` | `trade_date` 365일 |
| | `prv_etf.quote_intraday` | `(code, ts, source)` | `price`, `inav`, `premium_pct`, `turnover`, `volume`, `quality`(estimated) | `ts` 1일, 보존 30일 |
| | `prv_etf.meta` | `(code)` | `name`, `issuer`, `brand`, `theme`(classify), `etf_type`, `leverage numeric`, `base_index`, `listed_on`, `delisted_on`, `source`, `updated_at` | 아님 |
| | `prv_etf.split_event` | `(code, effective_date)` | `ratio numeric`, `origin`(detected·manual), `note`, `quality` | 아님 |
| | `prv_etf.fund` | `(fund_id)` | ET DDL:71 열(`issuer`, `fund_key`, `ticker`, `name`, `theme`, `is_active`, `depth`, `track`, `empty_streak`) + `updated_at` | 아님 |
| | `prv_etf.holding` | `(fund_id, asof, code)` | `name`, `qty`, `wt`, `val`, `source`, `received_at`, `loaded_by` | `asof` 365일 |
| | `prv_etf.change_log` | `(run_date, fund_id, code, kind)` | ET DDL:79 열(`name`, `asof`, `prev_asof`, `gap_days`, `prev_qty`, `cur_qty`, `prev_wt`, `cur_wt`, `qty_pct`, `qty_pct_adj`) + `engine_version` | 아님 |

P2 설계 §8.2 의 0007 계획에 있던 `pub_themes.sector_map` 은 P5(분류 사전 통합)로 미룬다 — P3 은 `config/knowledge/sector_map.yaml` 을 읽는다. ET 의 `etf_ticker`·`etf_meta`·`etf_aum` 은 `prv_etf.meta`·`etf_daily` 로 흡수(`etf_live` 폐기 — P2 §8.4).

### 3.5 잠정 → 확정 덮어쓰기

| 단계 | 시각 | 원천 | 원장 행(`stock_investor_daily`·`stock_snapshot`) | quality | 기록 |
|---|---|---|---|---|---|
| 장중 잠정 | 09:00~15:20, 10분 | KIS 가집계(FHPTJ04400000 — 상위 목록만), 거래대금 순위(상위 N) | 목록에 든 종목만 오늘 행 upsert, `source='kis.prelim'` | estimated | 슬롯마다 `*_intraday` 이력 표 |
| 마감 확정 | 15:35~ | KIS 종목별(FHKST01010900·FHKST01010100 — 전 종목) | 오늘 행을 **덮어쓴다**, `source='kis'` | ok | 덮기 전 값이 있으면 `prv_flows.investor_revision`(`est_value`·`final_value`·`diff`) — 같은 트랜잭션 |
| D+1 대조 | 다음 영업일 08:05~ | KRX 일별(`stk_bydd_trd` — 종가·거래대금·시총; **투자자별은 KRX OpenAPI 에 없음**, conflict_map §1.11) | `source='krx'` 행을 **따로** 넣는다(PK 의 source 가 다름) — 원장 우선순위가 krx 를 고른다 | ok | `eod_reconcile`: KIS 종가·거래대금이 KRX 와 `tol`(종가 0, 거래대금 0.5% [확인 필요]) 밖이면 KIS 행 `invalid` + 사유 |
| 보드 확정 | 08:40 | 위 KRX 일봉 | `prv_board.*` 의 그날 행 덮어씀 | ok | `artifact.payload.confirm` = 바뀐 라벨 목록(신규 생김·사라짐) |

투자자별 순매수는 KRX 대조 원천이 없어 KIS 마감값이 최종(ok)이다. 원장은 `kbj.engines.flows.ledger.build_ledger` 하나가 우선순위로 고르고, 화면·띠·보드가 모두 그 원장을 쓴다(metrics §0).

### 3.6 KRX D+1 대조 (`krx.daily` 끝 단계)

| 항목 | 규칙 |
|---|---|
| 대상 | 전 거래일 `stock_snapshot(source='kis', quality=ok)` 전 종목 대 같은 날 `source='krx'` |
| 필드 | `close`(정확히 같아야 — KIS 가 NXT·시간외를 섞으면 다름, ET `flowlab/verify.py` 주석 "D-080 이후 종가의 원천 상이는 정상"), `turnover`(KRX 정규장만인지 [실측 필요] — 차이 허용 0.5% [확인 필요]), `mktcap` |
| 결과 | `prv_market.eod_reconcile` 행, 불일치 KIS 행 `invalid`, 요약(불일치 수·최대 차이)을 `ops.job_run.detail` 과 health(`reconcile_mismatch` — 1% 이상 종목이면 경고) |
| 근거 | ADR 0001 Q1, conflict_map Q1 의 비교 방법(5거래일 대조)이 이 기록으로 자동으로 쌓인다 |

### 3.7 KIS 호출량 (초당 4건 — `config/limits.yaml` `kis.rate`)

| 작업 | 호출 수 | 시간(4/s) | 비고 |
|---|---|---|---|
| `market.intraday` 슬롯 | 지수 3 + 업종 2 = **5** | 2초 | |
| `flows.intraday` 슬롯 | 가집계 시장×구분 4 [실측 필요] + 순위 2 + ETF 50 = **약 56** | 14초 | ETF 전체(900여)면 225초 — 감시 상위 N 으로 제한(D-P3-14) |
| 장중 합계 | 38슬롯 × 61 ≈ **2,300** | — | GX poller(P7)와 같은 버킷 — P3 에는 legacy GX 가 VM 에서 돌지 않는다 [확인 필요] |
| `market.close_collect` | 현재가 2,700 + 투자자 2,700 + 시장 2 + 가집계 4 + ETF 투자자 약 300 = **약 5,700** | **약 24분**(15:35 → 16:00 전후) | venue 를 2개로 늘리면 약 48분 → `board.daily` 16:20 이 `depends_on` 으로 기다린다. 멀티종목 시세 TR(FHKST11300006, 30종목/건 [추정 — conflict_map §1.13])로 현재가를 90건으로 줄일 수 있는지 [실측 필요] |
| 하루 합계 | 약 8,000 | — | KIS 일 한도는 공표 없음(`limits.yaml` 주석) |

### 3.8 일봉 이력 KRX 백필 (P2 R11)

| 항목 | 규칙 |
|---|---|
| 대상 | `KRX:sto/stk_bydd_trd`·`ksq_bydd_trd`(종목), `idx/*`(지수), `etp/etf_bydd_trd`(ETF) — 날짜별 전 종목 1회 |
| 양 | 5년 ≈ 1,240 거래일 × (주식 2 + 지수 2 + ETF 1) ≈ **6,200회**. 주식만이면 2,500회(PLAN 숫자) |
| 나누기 | `krx.backfill_cap` 5,000/일(`limits.yaml`) 안에서 **최근 날짜부터 과거로**, 하루 최대 1,000일치 [제안] — 이틀 |
| 실행 | `python -m kbj.services.scheduler backfill market.backfill --from 2021-10-01 --to 2026-10-06` (신규 하위 명령 — 거래일을 펼쳐 `run-once` 를 반복, 이미 `done` 인 키는 건너뜀). 우선순위 P4(`limits.yaml priorities.backfill`) |
| board 연결 | 백필이 끝난 종목은 `prv_board.alltime` 을 처음부터 다시 쌓는다(`roll_alltime(None, rows)` — ET `--init` 와 같은 뜻, `newhigh.py:roll_alltime` 머리말 "과거 구간을 뒤늦게 채우려면 다시 쌓아야 한다"). `history_from` = 받은 첫 날. 상장일(`universe.listed_on`) < `history_from` 이면 hist 계산 안 함(D-P3-11) |
| 제공 시작일 | KRX OpenAPI 일별이 몇 년 전부터 있는지 [실측 필요 — 체크리스트 #21] |

### 3.9 `config/markets.yaml` (신규 — 숫자는 한 곳)

| 키 | 기본값 | 근거 |
|---|---|---|
| `kis.venues` | `[KRX]` [확인 필요] | D-P3-9 |
| `screen.min_avg_turnover_krw` | `30_000_000_000`(300억) [확인 필요] | metrics §3 |
| `screen.spike_min`·`spike_min_prior_days` | 1.5 · 10 | metrics §1·§3 |
| `screen.streak_min` | 3 | metrics §3 |
| `etf.premium_warn_pct` | `{default: 0.5, overseas: 0.5, bond: 0.5}` [확인 필요 — 해외·채권 기준] | metrics §4 |
| `etf.watch_top_n`·`investor_min_net_asset_krw` | 50 · 100,000,000,000 [확인 필요] | §3.7 |
| `etf.holdings.qty_floor`·`action_pp`·`max_gap_days` | 100 · 2.0 · 14 | ET `tracker.py`:63·64·`fund_pairs`:251(옛 환경변수 `QTY_FLOOR`·`ACTION_PP` 대체) |
| `etf.split_tol` | 0.02 | §4.3 |
| `reconcile.turnover_tol_pct` | 0.5 [확인 필요] | §3.6 |
| `market.sector_indices` | KRX 업종지수 코드 목록 [실측 필요] | §4.4 |
| `ribbon.cyclical`·`ribbon.defensive` | 지수 코드 [확인 필요] | §4.4 |

---

## 4. 엔진 명세

### 4.1 신고가 (board 엔진 승격)

**정의는 ET `board/engine/newhigh.py` 머리말 그대로**(60일 = 직전 60영업일·52주 = 직전 252영업일·역사적 = 상장 이후, 당일 제외 `cur > ref` 엄격 — ADR 0001 Q2, 종가·고가 두 기준, 기본 기준 종가 `default_basis: close`, 라벨 우선순위 hist > w52 > d60, 신규/이어감, 갭, 5일 축소폭, 저항두께, 거래량 배수, 재료 반납, `split_guard` ±31%). metrics.md §7 에는 이 정의를 요약하고 정본은 엔진 문서화 문자열이라고 적는다(M).

**함수 매핑과 바뀌는 것**

| legacy | kbj | 바뀌는 것 |
|---|---|---|
| `newhigh.evaluate(rows, asof, cfg, hist_ref, hist_days, split_cleared)`:181 | `kbj.engines.board.newhigh.evaluate`(같은 시그니처) | `rows` 는 `Sequence[Mapping]`(dict 그대로 — 골든 입력) 또는 `Bar` 를 받는다(`_as_row` 한 함수로 맞춤). 반환 dict 키 그대로 |
| `newhigh.roll_alltime`:318, `hist_ref_for`:362 | 같은 이름 | 입력에 `history_from` 이 있으면 D-P3-11 사유를 돌려준다(새 사유 문자열 1개 — 골든에는 없음) |
| `build.run(db_path, asof, cfg, log)`:425 | `build.compute_day(inp, cfg) -> BoardDay` | `DB.trading_days`·`snapshot`·`all_series`·`sector_of`·`alltime`·`split_cleared`·`split_unknown`·`missing`·`note` 결과를 `BoardInputs` 로 받는다. `write(...)` 대신 payload 반환. `log` 는 `notes`·`diagnostics` 로(배너 대 진단 나눔 :466~474 그대로) |
| `build.close_provenance`:38 | 같음 | `source == 'krx'` 판정 → KRX 확정(`board.confirm`) 여부와 같다. 네이버 문구(:578~582 "네이버 16:07 값")는 "KIS 마감값(잠정)"으로 바꾼다 — **골든 비교에서 이 한 문장은 정규화 표로 맞춘다** |
| `rankings.build(asof, cfg, log)`:217 | `build_rankings(universe_rows, prev_rankings, cfg, taxonomy)` | state 파일 읽기 → 인자 |
| `db.*`(sqlite) | `kbj.store.repos.board`·`market` | §1.1 |
| state JSON 6종 | `prv_board.stock_day`·`artifact` | `market.json` 은 쓰지 않는다(시장 화면은 `kbj.engines.market`) |

**입력·출력**

| 이름 | 내용 |
|---|---|
| `BoardInputs` | `asof`, `prev_asof`, `series: Mapping[code, Sequence[row]]`(오름차순, `asof` 까지), `snapshot: Mapping[code, Snap]`·`snap_asof`, `alltime: Mapping[code, AllTime]`(그날 `roll_alltime` 반영 뒤 — ET 순서: 수집 → `sync_px` 의 `ALLTIME_SQL` → `build.run`), `sectors: Mapping[code, str]`(board48), `taxonomy_counts`, `prev_ranks: Mapping[code, int]`, `split_cleared: set[str]`, `split_unknown: list[(code, note)]`, `prev_near: Mapping[code, (kind, gap)]`, `collect_notes: list[(step, note)]`, `themes_yaml`, `prev_rankings` |
| `BoardDay` | `universe`(행 목록 + 메타), `newhigh`·`sectors`·`events`·`rankings`(ET JSON 과 같은 키), `labels: list[Label]`, `notes`, `diagnostics`, `quality`(KIS 마감 = estimated, KRX 확정 = ok) |

**골든 비교 방법**(§8.1 에 시험)

| 단계 | 내용 |
|---|---|
| 1 입력 | `tests/golden/board/make_golden.py --seed 20260826 --days 30 --stocks 300`: ET `board/tests/demo.py:_series`:41 방식의 합성 일봉(돌파·근접·분할 계단·거래정지 공백·상장 60일 미만·시총 하한 미달·종가/고가 기준 차이 사례를 일부러 심는다) + 합성 스냅·sector_map·themes. 결과 `inputs/day_XX.json` |
| 2 legacy 실행 | 같은 스크립트가 legacy 루트 venv 로 임시 sqlite 를 만들어 ET `pipeline` 의 alltime 갱신 순서대로 `roll_alltime` → `build.run(db, asof)` 를 30일 연속 돌리고 `state/<날짜>/{universe,newhigh,sectors,events,rankings}.json` 을 `expected/day_XX/` 로 복사. `META.json` 에 legacy 커밋·시드·생성 시각 |
| 3 커밋 | 합성 데이터라 공개 레포에 넣어도 된다(U3). **shim(D-P3-10 ④) 전에** 커밋 |
| 4 비교 | kbj `compute_day` 를 같은 입력으로 30일 → 필드 단위 비교: 키 집합·목록 순서·문자열은 정확히, 실수는 `math.isclose(rel_tol=1e-9, abs_tol=1e-6)`(CLAUDE §4 — 바이트 비교 금지). 무시: `generated_at`. 정규화: 네이버 잠정 문구 1개, `source` 대소문자 |
| 5 재생성 금지 | `make_golden.py --check` 는 shim 뒤에는 legacy 가 kbj 를 부르므로 의미가 없다 — 골든 파일은 그 뒤 **고치지 않는다**(바꿀 일은 새 골든 세트 + ADR) |
| 6 독립 오라클 | ET `flowlab/verify.py:check_newhigh`:132 의 단순 루프(종가·고가 각 기준, 라벨 창) 이식 — hypothesis 로 만든 시계열에서 "엔진 라벨 ⊆ 루프 라벨, hist 는 w52 필요조건" |

### 4.2 수급·스크리닝 (metrics §1~§3 그대로)

| 지표 | 정의(metrics) | 함수 | 품질·예외 |
|---|---|---|---|
| 거래대금(일) | 정규장+시간외 합 [확인 필요], KRX·NXT 나눠 저장 | `ledger` 원천 그대로 | venue=KRX 만이면 꼬리표 "NXT 미포함" |
| 기간 거래대금 | 기간 영업일 합 | `Ledger.window(...).sum_turnover` | 휴장일 행 없음 |
| 일평균 거래대금 | 기간 합 ÷ **실제 거래된** 영업일 수(정지일 제외) | `.avg_turnover` | `n_traded == 0` → `None` |
| 회전율(%) | 일평균 ÷ 기간 마지막 날 시총 × 100 | `screen` | 시총 없으면 `None` |
| 급증 배수 | 오늘 ÷ 직전 19영업일 평균, 직전 거래일 < 10 이면 계산 안 함 | `screen(mode="spike")` | `invalid`·표시 없음 |
| 시장 거래대금 | 코스피+코스닥 **주식**(kind ∈ common·pref·spac [확인 필요 — 스팩]) 합, ETF·ETN·리츠 제외 | `market.turnover.market_turnover` | |
| 순매수(원) | 매수 − 매도 금액 | 원장 | KIS 백만원 → 원(파서) |
| 투자자 구분 | 외국인(+기타외국인)·기관(7구분 합)·기타법인·개인 | `Investor` | 기타법인이 응답에 없으면 그 구분 행 없음 |
| 검산 ① | 4구분 합 = 0 | `checks.check1` | **4구분이 다 있을 때만**. 하나라도 없으면 `None`(검산 불가 — 화면에 "검산 불가: 기타법인 미제공") [실측 필요 — FHKST01010900 은 개인·외국인·기관 3구분(ET `board/ingest/kis.py:FIELD`:71)] |
| 검산 ② | 7구분 합 = 기관 | `checks.check2` | 7구분이 없으면 패널을 숨긴다(빈 값을 0 으로 그리지 않음 — metrics §2) |
| 연속 순매수 | 오늘부터 거꾸로 > 0 연속, 0원·순매도·정지일에서 끊김 | `streak.streak` | 오늘 정지 → 0 |
| 동반 | 기간 외국인 > 0 이고 기관 > 0 | `screen(mode="both")` | |

**스크리닝 표**(metrics §3): `value`=기간 거래대금 내림차순 · `foreign`=기간 외국인 순매수 · `inst`=기간 기관 순매수 · `both`=외국인+기관 합(둘 다 > 0) · `streak`=max(외국인 연속, 기관 연속), 같으면 순매수 합(3일 이상) · `spike`=급증 배수(1.5배 이상, 기간 무관). 공통 필터: 시장, 기간 1·5·20, 일평균 하한(기본 300억), **관리종목·거래정지·정리매매 기본 제외**(플래그 원천 KIS `iscd_stat_cls_code` [실측 필요] — 모르면 제외하지 못한 수를 `n_status_unknown` 으로 함께 낸다), 우선주는 보통주와 따로. 결과 행에 board 라벨(신고가 여부)·급증 배수. `invalid` 행은 집계에서 빠지고 `n_excluded.invalid` 로 센다.

**장중 화면**: 장중에는 전 종목 원장이 없으므로(가집계·순위는 상위 목록뿐) 스크리너는 **전 거래일 확정 원장**으로 돌고, 따로 "장중 잠정" 위젯(가집계 상위·거래대금 순위)을 `estimated` 로 보여 준다. 15:35 마감 수집이 끝나면 스크리너가 오늘로 넘어간다(`as_of` 로 표시).

### 4.3 ETF 수급 (metrics §4)

| 지표 | 공식 | 함수 | 예외 처리 |
|---|---|---|---|
| 순유입 | Σ (Sₜ − Sₜ₋₁) × NAVₜ | `flows.daily_flow` | 전날 행 없음(신규 상장) → `status=new`, 순유입 `None`, 첫날 순자산은 '신규' 목록에 따로 |
| 가격효과 | Σ Sₜ₋₁ × (NAVₜ − NAVₜ₋₁) | 같음 | |
| 검산 ③ | 순자산 변화 = 순유입 + 가격효과 | `check3` | 계산 순자산(S×NAV)으로는 항등식이라 잔차 0. **보고 순자산**(KRX `INVSTASST_NETASST_TOTAMT`)과도 대조: 허용오차 = 0.005원 × (Sₜ + Sₜ₋₁) + 순자산 공표 단위 [실측 필요 — 원·백만원] — 넘으면 그 날 행 `invalid` + `ledger_check(c3)` |
| 분할·병합 | Sₜ/Sₜ₋₁ ≈ k 이고 NAVₜ₋₁/NAVₜ ≈ k (k ∈ 2·3·4·5·10·20·50·100 또는 1/k, 상대오차 `split_tol` 2%) | `splits.detect_split` → `adjust(prev, k)`: Sₜ₋₁·k, NAVₜ₋₁/k 로 맞춘 뒤 계산 | `status=split_adjusted`, `prv_etf.split_event(origin=detected, quality=estimated)` 기록. 수동 표(`origin=manual`)가 있으면 그것이 우선. 시험: 1:10 분할일 순유입 = 0 |
| 분배금 | 좌수 공식이라 자동 — NAV 하락분은 가격효과 | (별도 처리 없음) | 시험으로 고정: 좌수 불변·NAV −D → 순유입 0, 가격효과 −Sₜ₋₁·D |
| 상장폐지 | 마지막 행까지 계산, 그 뒤 없음 | — | `meta.delisted_on` |
| NAV 시점 | 마감 NAV(KRX 일별)만 | — | 장중 iNAV(KIS)는 괴리율 경고에만 |
| 투자자별 ETF 순매수 | 장내 매매 투자자별(KIS — `KIS:etf_investor_daily`) | 원장(종목과 같은 `stock_investor_daily`) | **순유입과 합치지 않고 따로**(LP 상대) |
| 괴리율(%) | (가격 ÷ NAV − 1) × 100 | `premium.premium_pct` | 마감: 종가·NAV(ok). 장중: 현재가·iNAV(estimated). 경고 `|괴리율| ≥ premium_warn_pct` |
| 유형별 순유입 | 유형 7개 합 | `flows.by_type` | 아래 규칙 |
| 레버리지·인버스 | 거래대금 상위를 덮음 → 유형 필터 기본 제공, 시장 거래대금에 넣지 않음 | — | metrics §4-5 |

**유형 규칙** `etf_type(name, base_index)` — 위에서 먼저 맞는 것(metrics §8 에 정본으로 적는다, [확인 필요]):

| 순서 | 유형 | 규칙 |
|---|---|---|
| 1 | 레버리지·인버스 | 이름에 `레버리지`·`인버스`·`2X`·`숏`·`곱버스` 또는 기초지수 이름에 배수 표시(ET `EXCLUDE`:6 의 앞 네 낱말 포함) |
| 2 | 채권·현금 | `채권`·`국고채`·`통안채`·`회사채`·`은행채`·`금리`·`머니마켓`·`CD`·`KOFR`·`단기자금`(ET `EXCLUDE` 채권 낱말 포함) |
| 3 | 원자재 | `골드`·`금현물`·`은`·`원유`·`WTI`·`구리`·`농산물`·`원자재` |
| 4 | 해외주식 | `미국`·`S&P`·`나스닥`·`차이나`·`중국`·`일본`·`인도`·`베트남`·`유럽`·`글로벌`·`MSCI ACWI`·`필라델피아` 등 해외 지수 낱말(기초지수 이름 우선) |
| 5 | 국내 대표지수 | `classify` 결과가 `시장대표`·`코스닥`·`팩터`(ET `RULES` 의 해당 줄) |
| 6 | 국내 테마 | 그 밖의 `classify` 테마 |
| — | (분류 불가) | 위에 안 걸리고 `classify` 도 `None` → `기타`(화면 유형 필터에 함께 표시, 수 0 이 아닌지 시험) |

**구성종목 변동**: `holdings.fund_pairs` → `analyze`(ET `tracker.py:analyze`:274 — 펀드 단위 최근 두 스냅, 신규 `NEW`·제외 `DROP`·TOP10 진입/이탈 `IN10`/`OUT10`·CU 재산정 보정(유의미 수량 종목 변동률 중앙값, 5종목 미만이면 보정 0) 후 ±`action_pp` 넘으면 `ADD`/`CUT`, ETF 가 ETF 를 담은 것은 제외). 결과 `prv_etf.change_log`. 화면 정렬: 액티브 우선·패시브 테마(`PASSIVE_THEMES` — ET `tracker.py` 의 `{'시장대표','코스닥','팩터','ESG','기타'}`) 후순위.

### 4.4 시장 — 시장 거래대금·시장폭·업종 히트맵·상단 띠 (metrics §6 신설)

| 값 | 계산 | 원천·as_of | quality |
|---|---|---|---|
| 시장 거래대금(확정) | §4.2 정의, 원장(krx > kis) | 전 거래일 KRX(08:05 뒤) 또는 오늘 KIS 마감(15:35 뒤) | ok |
| 시장 거래대금(장중) | 코스피(0001)+코스닥(1001) 지수 누적거래대금 | KIS 지수 현재가 슬롯 | estimated [실측 필요 — 지수 거래대금에 ETF 가 드는지. 들면 확정값과 정의가 달라 '장중(지수 기준)' 으로 따로 표시] |
| 20일 평균 대비 | 오늘 ÷ 직전 20영업일 평균 | 원장 | 원천 따름 |
| 시장폭 | 상승·보합·하락·미지 수(ET `aggregate.breadth`:32 그대로 — 모르는 값은 unknown), 20일선 위 비율(종가 > SMA20, 20봉 미만 제외), 신고가 수·비율(보드 종가 기준 라벨), 상한가·하한가(등락률 ≥ 29.5 / ≤ −29.5 [추정 — 가격제한폭 30%와 호가 단위]) | 원장 + `prv_board.label` | 보드 quality 따름. 신저가는 KR 엔진에 없어 P3 에 없다 |
| 업종 히트맵 | KRX 업종지수(`markets.yaml` `sector_indices`) 셀: 색 = 등락률(1·5·20일), 크기 = 지수 거래대금 | 장중 `sector_intraday`, 마감 뒤 `daily_bar(asset=index)` | estimated / ok |
| 섹터 집계(페이지 2) | board48(`by_sector`·`heatmap`) — 업종지수 히트맵과 다른 축(ADR 0001 Q6: 공식 업종 코드가 기본 축, board48 은 사용자 분류 층) | `prv_board.artifact(sectors)` | 보드 따름 |
| 반도체 쏠림 | r_semi = 삼성전자·SK하이닉스 시총가중 수익률, r_ex = (R_코스피·M_코스피 − r_semi·M_semi) ÷ (M_코스피 − M_semi), 쏠림 = r_semi − r_ex (%p, 1일·5일) — 가중치는 전일 시총 | 원장 + 코스피 지수 | 원천 따름 |
| 경기민감 대 방어 | 경기민감 업종지수 수익률 − 방어 업종지수 수익률(%p) — 지수 코드는 `ribbon.cyclical`·`defensive` [확인 필요] | KRX/KIS 업종지수 | 원천 따름 |
| 외국인·기관 현물 | `market_investor_daily` 코스피+코스닥 | 마지막 확정일(장중 값은 GX poller — P7) | ok, 칩에 '마감' |
| 신고가 수 | 보드 종가 기준 52주 이상 라벨 수 | 마지막 보드 | estimated(당일)·ok(확정) |
| 세션·야간 카운트다운 | `kbj.core.calendar`(`state_at`·`session_bounds`·`equity_bounds`·`night_session_opens`) | 코드 | ok |
| 금통위 D-n | `config/calendar_events.yaml` | 수기(한국은행 공표 일정) | ok(공개) |
| 신용스프레드·신용잔고·수출 속보·한국 대 글로벌·GEX Flip | — | P5·P6·P7 | '준비 중' 칩 |

---

## 5. API

### 5.1 구성

| 항목 | 값 |
|---|---|
| 프로세스 | compose 서비스 `api`: `python -m kbj.services.api serve` → `uvicorn.run(create_app(...), host=KBJ_API_HOST(127.0.0.1), port=KBJ_API_PORT(8000))`. TLS 는 VM 역방향 프록시(레포 밖 — [확인 필요: Caddy·nginx]) |
| 주입 환경변수 | `KBJ_SERVICE=api`, `KBJ_DATABASE_URL`, `KBJ_REDIS_URL`, `KBJ_WEB_USER`, `KBJ_WEB_PASSWORD_HASH`, `KBJ_TELEGRAM_WEBHOOK_SECRET`, `KBJ_PUBLIC_BASE_URL`. **KIS·KRX 키는 넣지 않는다**(계약 ⑩ + 컨테이너 범위) |
| 정적 파일 | `web/dist-login`(이미지 빌드 단계에서) — `/` 에 `StaticFiles(html=True)`, `/api`·`/telegram` 이 먼저 |
| DB | 앱 역할, 요청마다 풀 연결(psycopg pool 은 새 의존성 — 쓰지 않고 `kbj.store.db.connect` 를 작은 큐로 [제안]) |
| 시계 | `now` 주입(시험은 가짜 시계) |

### 5.2 응답 봉투·모델

모든 데이터 응답은 `Envelope[T]`:

| 필드 | 형 | 규칙 |
|---|---|---|
| `source` | str (필수) | 원천 묶음 — 예 `"KRX"`, `"KIS"`, `"KRX+KIS"`, `"KIS(잠정)"` |
| `as_of` | AwareDatetime (필수) | 데이터가 가리키는 시각(마감 = 그날 15:30 KST, 슬롯 = 슬롯 끝) |
| `quality` | `Quality` (필수) | 구성 값 중 가장 나쁜 것(ok < stale < estimated; invalid 행은 빠지고 `notes` 에 수) |
| `notes` | list[str] | "NXT 미포함", "검산 불가: 기타법인 미제공", "invalid 3행 제외" 등 |
| `generated_at` | AwareDatetime | 응답을 만든 시각 |
| `data` | T | 아래 모델. 행마다 값의 원천·품질이 다르면 행에도 `quality`·`source` |

| 모델 | 주요 필드 |
|---|---|
| `MarketSummary` | `indices: list[IndexTile(code, name, value, chg_pct, spark: list[float], source, as_of, quality)]`, `turnover: TurnoverPanel(today: Sourced[int], avg20: int, ratio: float, series: list[DayValue])`, `breadth: Breadth`, `investors: InvestorTotals` |
| `SectorHeat` | `period`, `cells: list[SectorCell(code, name, market, chg_pct, turnover, quality)]` |
| `Ribbon` | `chips: list[Chip(key, label, value: Sourced[float \| int \| str] \| None, tier, phase_pending: str \| None)]` |
| `BoardNewhigh` | ET `newhigh.json` 키(`basis`, `counts_close`, `counts_high`, `achieved`, `proximity`, `min_mktcap_eok`, `min_turnover_eok`, `n_below_mktcap`, `n_suspect`, `n_hist_not_evaluated`, `thresholds`, `labels`, `priority`) + 행에 원장 외국인·기관(기간 1일) |
| `BoardSectors`·`BoardEvents`·`BoardRankings` | ET `sectors.json`·`events.json`·`rankings.json` 키 그대로 |
| `ScreenResponse` | `mode`, `market`, `period`, `min_avg_turnover`, `n_total`, `n_excluded{flagged, invalid, below_min, status_unknown}`, `rows: list[ScreenRow(rank, code, name, market, sector, kind, chg_pct, turnover_sum, turnover_avg, turnover_rate_pct, foreign, inst, other_corp, indiv, streak_foreign, streak_inst, spike_mult, newhigh_label, flags, quality)]` |
| `InvestorTotals` | `date`, `by_investor{foreign, institution, other_corp, individual}`, `inst7 \| None`, `check1_residual \| None`, `check2_residual \| None` |
| `StockFlowDetail` | `code`, `name`, `days: list[{date, turnover, foreign, inst, other_corp, indiv, quality}]`, `cumulative`, `checks` |
| `IntradayFlows` | `slot`, `top_foreign`, `top_inst`, `turnover_rank` (모두 estimated) |
| `EtfFlows` | `mode`, `type`, `period`, `rows: list[EtfFlowRow(code, name, etf_type, theme, issuer, net_asset, net_inflow, price_effect, ret_pct, turnover, indiv, foreign, inst, status, quality)]`, `new_listings` |
| `EtfTypes` | `period`, `rows: list[TypeRollup(etf_type, net_inflow, n)]`, `check3{net_asset_chg, inflow, price_effect, residual}` |
| `EtfPremium` | `basis: "nav" \| "inav"`, `rows: list[PremiumRow(code, name, price, nav, premium_pct, threshold)]` |
| `EtfHoldingChanges` | `run_date`, `rows: list[Change(fund_id, etf_code, fund_name, issuer, is_active, code, name, kind, prev_qty, cur_qty, qty_pct_adj, asof, prev_asof, gap_days)]` |

### 5.3 라우트

모든 `/api/*` 데이터 라우트는 **로그인 등급**(세션 필수 — 없으면 401). 공개 라우트는 없다(공개는 정적 사이트).

| 메서드·경로 | 쿼리 | 응답 | 캐시(서버 TTL / `Cache-Control`) |
|---|---|---|---|
| `GET /api/health` | — | `{ok, commit}`(데이터 없음, 세션 불필요) | 없음 / `no-store` |
| `POST /api/auth/login` | 본문 `{username, password}` | `{csrf_token}` + 쿠키 | — / `no-store` |
| `POST /api/auth/logout` | (CSRF) | 204 | — |
| `GET /api/auth/me` | — | `{user, csrf_token}` | — / `no-store` |
| `GET /api/market/summary` | `date?` | `Envelope[MarketSummary]` | 장중 30 s · 그 밖 300 s / `private, no-cache` + ETag |
| `GET /api/market/sectors` | `date?`, `period=1\|5\|20` | `Envelope[SectorHeat]` | 같음 |
| `GET /api/market/ribbon` | — | `Envelope[Ribbon]` | 30 s |
| `GET /api/board/newhigh` | `date?`, `basis=close\|high`, `kind?`, `min_turnover_eok?` | `Envelope[BoardNewhigh]` | 300 s(데이터 버전 키) |
| `GET /api/board/sectors` | `date?` | `Envelope[BoardSectors]` | 같음 |
| `GET /api/board/events` | `date?` | `Envelope[BoardEvents]` | 같음 |
| `GET /api/board/rankings` | `date?` | `Envelope[BoardRankings]` | 같음 |
| `GET /api/flows/screen` | `mode`, `market=all\|KOSPI\|KOSDAQ`, `period=1\|5\|20`, `min_avg_turnover=30000000000`, `include_flagged=false`, `limit≤200` | `Envelope[ScreenResponse]` | 300 s |
| `GET /api/flows/investors` | `date?` | `Envelope[InvestorTotals]` | 300 s |
| `GET /api/flows/stock/{code}` | `days=20(≤60)` | `Envelope[StockFlowDetail]` | 300 s |
| `GET /api/flows/intraday` | — | `Envelope[IntradayFlows]` | 30 s |
| `GET /api/etf/flows` | `mode=in\|out\|value\|indiv\|foreign\|inst`, `type?`, `period=1\|5\|20`, `limit≤200` | `Envelope[EtfFlows]` | 300 s |
| `GET /api/etf/types` | `period` | `Envelope[EtfTypes]` | 300 s |
| `GET /api/etf/premium` | `basis=nav\|inav` | `Envelope[EtfPremium]` | 장중 30 s |
| `GET /api/etf/holdings/changes` | `date?`, `kind?`, `issuer?` | `Envelope[EtfHoldingChanges]` | 600 s |
| `POST /telegram/webhook` | 헤더 `X-Telegram-Bot-Api-Secret-Token` | `WebhookResult.as_response()` | — |

쿼리는 pydantic 으로 검증(모르는 값 422). `code` 는 `^[0-9A-Z]{6}$`. 서버 캐시 키 = (경로, 정렬된 쿼리, 데이터 버전) — 데이터 버전 = 관련 데이터셋의 `ops.data_claim` 최근 `done_at`(15초 캐시, `api_data_version_key`). 응답은 사용자 1명이지만 로그인 데이터라 `private`.

### 5.4 로그인 (사용자 1명)

| 항목 | 규칙 |
|---|---|
| 자격 | `KBJ_WEB_USER`(평문 이름), `KBJ_WEB_PASSWORD_HASH`(`scrypt$n=32768$r=8$p=1$<salt b64>$<dk b64>` — 솔트 16바이트, 키 64바이트). 생성: `python -m kbj.services.api hash-password`(`getpass` 로 두 번 입력, stdout 에 해시만 — 로그·파일 없음). **기본값 없음**: 둘 중 하나라도 없으면 `POST /api/auth/login` 은 503 `login_not_configured` |
| 검증 | 이름은 `hmac.compare_digest`, 비밀번호는 `hashlib.scrypt` 재계산 후 `hmac.compare_digest`. 이름이 틀려도 같은 scrypt 비용을 쓴다(시간 차 없음) |
| 무차별 대입 | Redis `web_login_fail_key(sha256(ip)[:16])` 15분 창 5회 → 그 IP 15분 잠금, 전체 1시간 20회 → `WEB_LOGIN_LOCK` 30분(전체 잠금). 응답은 실패·잠금 모두 같은 401 문구 + `Retry-After`(잠금 때) |
| 세션 | `sid = secrets.token_urlsafe(32)`, Redis `web_session_key(sha256(sid))` = `{user, csrf, created, last_seen}` TTL `web_session_ttl_h`(12시간, 활동 시 연장 없음 — 고정 만료 [제안]). 로그인 때마다 새 sid(고정 방지), 로그아웃 = 삭제 |
| 쿠키 | `__Host-kbj_session=<sid>; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=…`. 개발(`web_cookie_secure=false`, `api_host` 가 루프백일 때만 허용)은 이름 `kbj_session` |
| CSRF | 상태를 바꾸는 요청(POST·PUT·DELETE — P3 는 로그아웃뿐)은 헤더 `X-KBJ-CSRF` == 세션 `csrf`(상수 시간) **그리고** `Origin` == `KBJ_PUBLIC_BASE_URL`. 로그인 POST 는 세션 전이라 Origin 만 + `Content-Type: application/json` 강제(단순 폼 CSRF 차단). GET 은 부작용 없음 |
| 보안 헤더 | `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy: ()` |
| 로그 | 사용자 이름·IP·쿠키·비밀번호·해시를 남기지 않는다 — 실패 수와 IP 해시 앞 8자만(`kbj.core.masking`) |
| 시험 | 해시 없음 → 503 · 틀림 → 401 + 카운터 · 6번째 → 잠금 · 성공 → 쿠키 속성 · 세션 없이 데이터 → 401 · CSRF 없음·Origin 다름 → 403 · 로그아웃 뒤 세션 무효 · 응답·로그에 비밀번호·해시 없음(캡처 로그 검사) · 시험용 해시는 낮은 비용(n=2**10)으로 픽스처에서 만든다(레포에 해시 상수 없음) |

### 5.5 텔레그램 웹훅

| 항목 | 규칙 |
|---|---|
| 경로 | `POST /telegram/webhook`(`kbj.services.notifier.webhook.WEBHOOK_PATH`) — 세션·CSRF·Origin 면제 |
| 처리 | 본문 1 MiB 상한으로 읽어 `WebhookHandler(secret=settings.telegram_webhook_secret, redis, now).handle({"method","path","headers","body"})` → `WebhookResult.as_response()` 그대로(본문 `{"ok": bool}` — 사유 없음) |
| 검증 | 헤더 `X-Telegram-Bot-Api-Secret-Token` 을 `secret_ok`(상수 시간 — `webhook.py`:92)로. 비밀이 설정 안 됐으면 늘 403(인증 없는 수신 없음) |
| 그 뒤 | P2 그대로 — `update_id` 중복 제거·`notify:inbound` XADD 를 Lua 한 번에, 명령·인박스 분배는 notifier 서비스 |
| 전환 | `setup-webhook` 은 사용자 작업(P2 §5.11 순서) — P3 은 받을 서버만 연다 |
| 시험 | 틀린 비밀 403·XADD 0, 맞으면 200·XADD 1, 같은 `update_id` 두 번 → 한 번, 쿠키 없이도 동작, 큰 본문 413 |

---

## 6. 프런트

### 6.1 기술 선택과 근거

| 선택 | 근거 |
|---|---|
| Vite 6 + TypeScript 5(strict) + 바닐라 DOM | 미리보기(`docs/design/preview_synthetic.html`)가 프레임워크 없이 위젯 20여 개를 그린다 — 같은 구조를 모듈로 나누기만 하면 된다. 런타임 의존성 0 → 공개 레포 공급망·라이선스 점검이 dev 의존성뿐 |
| 차트 = 손 SVG(`ui/svg.ts`: 막대·선·스파크라인·히트맵 셀) | 미리보기와 같다. 차트 라이브러리(수십~수백 KB) 불필요 |
| dev 의존성(목록이 lock 의 전부) | `vite`, `typescript`, `vitest`, `jsdom`, `eslint`, `@eslint/js`, `typescript-eslint`, `axe-core`(접근성 시험), `openapi-typescript`(API 타입 생성) — 9개 [확인 필요: 버전은 W 가 고정] |
| 노드 | 22 LTS(`web/.nvmrc`, `package.json engines`). 이 환경 `node v22.22.2` 확인. CI `npm ci --ignore-scripts` |
| 글꼴 | MD6 글꼴 파일을 가져오지 않는다. `font-family: "IBM Plex Mono", "Nanum Gothic Coding", ui-monospace, monospace` 시스템 우선 — 자체 호스팅(OFL 글꼴 + 라이선스 파일)은 [제안 — P9] |

### 6.2 `web/` 구조

```
web/
  package.json  package-lock.json  .nvmrc  tsconfig.json  vite.config.ts  eslint.config.js  index.html
  public/          manifest.webmanifest  icon.svg(직접 그린 KBJ 글자 아이콘)
  scripts/         check-bundle.mjs(공개 번들 금지 문자열) · merge-public-data.mjs
  src/
    main.ts                 부팅: TIER = import.meta.env.VITE_KBJ_TIER ('public'|'login')
    app/  shell.ts          상단 바(브랜드·KST 시계·등급 배지·스킨 버튼)
          pager.ts          탭·미니맵·scroll-snap 스와이프·←/→·해시 #/p/<n>
          ribbon.ts         상단 띠(칩 렌더)
          pages.ts          페이지 등록부(번호·제목·단계·등급·위젯 목록)
    design/ tokens.css      스킨 5종 변수 · base.css · components.css
    ui/   panel.ts table.ts seg.ts lock.ts quality.ts svg.ts fmt.ts(억·조·부호·KST) dom.ts(h())
    data/ source.ts         DataSource 인터페이스(get<T>(key, params) → Envelope<T>)
          static.ts         공개: ./data/*.json
          api.ts            로그인: fetch('/api/…', {credentials:'same-origin'}), 401 → 로그인 화면, CSRF 헤더
          auth.ts           로그인 폼·me·로그아웃(login 빌드에만)
    api/  openapi.json(A) · types.gen.ts(W2 생성)
    pages/ p1_market.ts p2_board.ts p4_flows.ts p10_etf.ts placeholder.ts tradingview.ts(공개 빌드에만)
    pwa/  sw.ts register.ts
  test/   *.test.ts · fixtures/api/*.json(A) · fixtures/public/*.json
```

### 6.3 디자인 토큰·스킨 5종

토큰 값은 **KBJ 미리보기(직접 만든 것 — 사용자 승인)의 `:root`·`[data-skin]` 값**을 옮긴다. MD6 의 CSS·JS·로고·이미지는 쓰지 않는다(CLAUDE §2).

| 토큰 | 뜻 | 스킨 |
|---|---|---|
| `--bg` `--panel` `--line` `--fg` `--dim` `--accent` | 바탕·패널·선·글자·흐린 글자·강조 | `amber`(기본) · `phosphor` · `uv` · `dark` · `white` (미리보기 `.app[data-skin=…]` 다섯 벌) |
| `--up` `--down` | 상승 적색·하락 청색(국내 관례 — ET board CLAUDE 10장) | 스킨마다 |
| `--warn` `--lock` `--sel` | 경고·자물쇠·선택 | 스킨마다 |
| `--est` | 잠정(estimated) 표시 — 점선 밑줄 색 | 신규(스킨마다 `--dim` 계열) |
| `--mono` `--kr` | 글꼴 | 공통 |

스킨은 `data-skin` 속성 + `localStorage('kbj-skin')`(try/catch — 미리보기와 같음). 시험: 스킨마다 `--fg`/`--bg`·`--dim`/`--panel`·`--up`·`--down` 대비 WCAG AA(4.5:1, 큰 숫자 3:1) — `tokens.css` 를 읽어 계산.

### 6.4 셸

| 부품 | 동작 | 접근성 |
|---|---|---|
| 상단 바 | 브랜드 `KBJ`, KST 시계(1초), 등급 배지(공개/로그인), 스킨 선택(`role=group`, `aria-pressed`), 로그인 빌드는 로그아웃 | 버튼 `:focus-visible` |
| 상단 띠 | 칩 가로 스크롤, 칩 = 이름·값·꼬리표(잠정·마감·준비 중·자물쇠). 1분마다 새로 고침(로그인), 공개는 캘린더로 계산 | `aria-live="off"`(잦은 낭독 방지), 칩에 `title` 로 원천·시각 |
| 탭·미니맵 | 13개 페이지 전부 탭에 나온다. 구현된 페이지(1·2·4·10)는 채운 칸, 준비 중은 점선 칸(미리보기 `.minimap i.todo`) | 탭 `aria-current`, 미니맵은 장식(`aria-hidden`) |
| 스와이프 | CSS `scroll-snap-type: x mandatory`(미리보기 `.pages`), 보이는 페이지 감지는 `IntersectionObserver`, 해시 `#/p/4` 와 동기 | ←/→ 키(입력 칸 밖), `prefers-reduced-motion` 이면 스크롤 애니메이션 없음 |
| PWA | `manifest.webmanifest` + `sw.ts`: **앱 셸(HTML·JS·CSS·아이콘)만** 캐시. `/api/*` 는 절대 캐시하지 않는다(로그인 데이터가 기기에 남지 않게). 공개 빌드의 `data/*.json` 은 네트워크 우선 | — |

### 6.5 페이지·위젯

| 페이지 | 위젯 | API(로그인) | 공개판 | 비고 |
|---|---|---|---|---|
| 상단 띠 | 세션·야간 카운트다운, 금통위 D-n | `/api/market/ribbon` | `calendar.json`·`events.json` | 공개 |
| | 시장 거래대금, 외국인·기관 현물, 신고가 수, 반도체 쏠림, 경기민감 대 방어 | 같음 | 자물쇠 칩 | 로그인 |
| | 신용스프레드·신용잔고·수출 속보 / 한국 대 글로벌 / GEX Flip | — | '준비 중(P5·P6)' | '준비 중(P5·P7)' |
| 1 시장 | 지수 타일(코스피·코스닥·코스피200 — 값·등락·스파크라인) | `/api/market/summary` `.indices` | TradingView 위젯(티커 띠·미니 차트) | K200 야간선물·VKOSPI 'P7', 원/달러 'P5' 타일 |
| | 시장 거래대금(오늘·20일 막대·평균선·비율) | `.turnover` | 자물쇠 | |
| | 업종 히트맵(기간 1·5·20) | `/api/market/sectors` | TradingView 히트맵 위젯 [확인 필요 — KRX 지원] 또는 자물쇠 | |
| | 시장폭 | `.breadth` | 자물쇠 | |
| | 투자자 수급(시장, 오늘·5일) | `/api/flows/investors` | 자물쇠 | |
| | 신용잔고·예탁금·펀드 자금(공개) | — | '준비 중(P5)' | |
| 2 신고가 | 신고가 종목 표(기준 종가/고가, 라벨, 거래대금 하한 토글, 신규/이어감, 갭, 거래량 배수, 외국인·기관) | `/api/board/newhigh` | 자물쇠 | 결손 배너 = `notes` |
| | 근접 표(5일 축소폭) | 같음 `.proximity` | 자물쇠 | |
| | 섹터 집계·테마 히트맵 | `/api/board/sectors` | 자물쇠 | |
| | 탐지 이벤트 | `/api/board/events` | 자물쇠 | |
| | 랭킹(섹터 1d·7d, 종목 1w·거래량 3d/1m) | `/api/board/rankings` | 자물쇠 | |
| | 간밤 미국 신고가 | — | — | '준비 중(P5)' |
| 4 수급 | 스크리너(6기준·시장·기간·하한·관리종목 포함 토글) | `/api/flows/screen` | 자물쇠 | 행 선택 → 종목 상세 |
| | 투자자별 순매수 + 기관 7구분 + 검산 ①② 줄 | `/api/flows/investors` | 자물쇠 | 7구분 없으면 패널 숨김 |
| | 종목 상세(20일 거래대금 막대 + 누적 순매수 선) | `/api/flows/stock/{code}` | 자물쇠 | |
| | 장중 잠정(가집계 상위·거래대금 순위) | `/api/flows/intraday` | 자물쇠 | 장중만, '잠정' |
| 10 ETF | ETF 자금 흐름 표(6정렬·유형·기간) | `/api/etf/flows` | 자물쇠 | 신규 상장은 따로 |
| | 유형별 순유입 + 검산 ③ 줄 | `/api/etf/types` | 자물쇠 | |
| | 괴리율 경고 | `/api/etf/premium` | 자물쇠 | 장중 iNAV(잠정) / 마감 NAV |
| | 구성종목 변동 | `/api/etf/holdings/changes` | 자물쇠 | |
| 3·5·6·7·8·9·11·12·13 | 빈 자리 패널 1개: 제목·'준비 중(Pn)'·등급(DATA_TIERS §2 — 5·7 은 공개 표시, 나머지 로그인 자물쇠) | — | 같음 | P4~P8 |

### 6.6 공개 빌드 대 로그인 빌드

| 항목 | 공개(`npm run build:public` → `web/dist-public`) | 로그인(`npm run build:login` → `web/dist-login`) |
|---|---|---|
| 데이터 소스 | `data/static.ts` — `./data/<name>.json`(Pages 에 합쳐 넣음) | `data/api.ts` — 같은 출처 `/api` |
| 로그인 위젯 | `ui/lock.ts` 자물쇠(사유 문구는 미리보기 `.lockmsg` 와 같은 뜻: "KRX 원시세 재배포 금지" 등) | 실제 위젯 |
| TradingView | `pages/tradingview.ts` 로 시세 자리에 위젯(외부 스크립트 — 공개 빌드 CSP 에만 `s3.tradingview.com` 허용) | 넣지 않는다 |
| 갈라짐 | `if (import.meta.env.VITE_KBJ_TIER === 'login') { await import('./data/api') }` — Vite 가 공개 빌드에서 이 분기를 지운다 | — |
| 검사 | `scripts/check-bundle.mjs dist-public`: `/api/`·`/telegram`·`auth/login`·`X-KBJ-CSRF` 문자열 0, `KBJ_` 0, 로그인 API 주소 0 → 하나라도 있으면 실패 | `dist-login` 에 TradingView 주소 0 |

### 6.7 상태·오류 표시

| 상태 | 표시 |
|---|---|
| `quality=estimated` | 값 옆 '잠정' 배지 + 점선 밑줄(`--est`), 표 머리에 "장중 잠정 — 15:35 마감 뒤 확정" |
| `quality=stale` | 흐린 글자 + "기준 HH:MM" + 시계 아이콘, 패널 머리 경고색 |
| `invalid` 행 | 그리지 않는다. `notes` 의 "검산 실패 n행 제외"를 패널 아래 `.def` 줄로 |
| 검산 불가 | "검산 불가: <사유>" — 0 으로 그리지 않는다 |
| 빈 데이터 | "아직 없음 — <작업 이름> <예정 시각>"(예: KRX 확정은 08:05) |
| 401 | 로그인 화면(로그인 빌드), 그 밖 오류는 패널 안 오류 줄(다른 패널은 계속 — 격리, 절대 규칙 4). 오류 문구에 응답 본문을 싣지 않는다 |
| 원천·시각 | 패널 머리 `.src` 에 `source · as_of`(미리보기 `h3 .src`) |

### 6.8 시험 (vitest + jsdom)

| 시험 | 내용 |
|---|---|
| `fmt.test.ts` | 원 → 억·조 반올림은 화면에서만, 부호, KST 표시(UTC 입력) |
| `quality.test.ts` | 네 품질의 표시, invalid 미표시·수 표시 |
| `tier.test.ts` | 공개 모드: `fetch` 가 `/api` 를 한 번도 부르지 않음, 로그인 위젯 자물쇠 / 로그인 모드: 401 → 로그인 화면 |
| `pager.test.ts` | 탭·해시·←/→·미니맵, 13페이지 등록 |
| `pages/*.test.ts`(W2) | A 가 만든 합성 응답 픽스처로 각 위젯 렌더, 정렬·필터 토글, 검산 줄 문구 |
| `a11y.test.ts` | 페이지마다 `axe-core` 심각(serious·critical) 위반 0, 표 `th scope`, 버튼 이름 |
| `tokens.test.ts` | 스킨 5종 대비(§6.3) |
| `bundle` 검사 | §6.6(빌드 뒤 `node scripts/check-bundle.mjs`) |
| `types` 신선도 | `openapi-typescript src/api/openapi.json` 결과가 커밋된 `types.gen.ts` 와 같은지 |

### 6.9 성능 예산 [제안]

| 항목 | 예산 |
|---|---|
| 로그인 번들 JS | ≤ 90 KB gzip, CSS ≤ 20 KB |
| 공개 번들 JS | ≤ 60 KB gzip(TradingView 제외) |
| 첫 화면(페이지 1, 픽스처) 렌더 | jsdom 시험에서 ≤ 200 ms [추정 기준] |
| API 응답 | 캐시 적중 p95 ≤ 50 ms, 미적중 ≤ 300 ms(스크리너 2,700종목 × 20일) [목표 — §8.4 에서 메모리 저장소로 잰다] |
| 표 행 | 서버 `limit ≤ 200`, 화면 기본 50행 + '더 보기' |
| 새로 고침 | 장중 띠 1분, 장중 위젯 10분 슬롯 + 30초 여유, 탭이 안 보이면(`document.hidden`) 멈춤 |

---

## 7. 공개 정적 사이트 내보내기

### 7.1 `kbj_public_export` 역할

| 항목 | 규칙 |
|---|---|
| 역할 | 0001 의 `kbj_public_export`(NOLOGIN, `pub_*` SELECT 만)의 구성원인 **로그인 역할**을 운영 배포에서 만든다(ADR 0002 §2.2) — `default_transaction_read_only = on` |
| 접속 | `KBJ_PUBLIC_EXPORT_DATABASE_URL`(신규 — secrets.md 운영 등급). 내보내기 코드는 앱 DSN(`KBJ_DATABASE_URL`)을 쓰지 않는다 |
| P3 에서 읽는 표 | 없음(공개 수집은 P5·P6). 연결 확인만 하고, 표가 생기면 같은 모듈에 내보내기 함수를 더한다 |

### 7.2 내보내기 모듈과 산출 파일

| 파일 | 내용 | 원천 |
|---|---|---|
| `calendar.json` | 오늘부터 60일: 거래일·휴장·지연 개장(`late_open`)·월물/위클리 만기·야간 세션 여부, 세션 경계(KST) | `kbj.core.calendar`(코드 계산 — 데이터 아님) |
| `events.json` | 금통위 날짜(D-n 칩) | `config/calendar_events.yaml` |
| `manifest.json` | 파일 목록·`generated_at`·각 파일의 `source`·`as_of`·`quality`·스키마 버전 | — |
| (P5~) `credit.json`·`spread.json`·`exports.json` … | — | `pub_market_stats`·`pub_macro`·`pub_trade` |

`run(ctx)`: `export_all` → `out_dir`(`KBJ_DATA_DIR/public`) → `public_push_enabled` 이면 `push_public_data`(고아 브랜치 `public-data` 에 한 커밋 강제 푸시 — 배포 키는 VM 에만, 이름 `KBJ_PUBLIC_DEPLOY_KEY_PATH` [사용자 승인 필요]) → 성공하면 `pages.yml` 디스패치(토큰 `KBJ_GITHUB_DISPATCH_TOKEN`, actions:write 만 [사용자 승인 필요]). 둘 다 기본 꺼짐 — P3 는 파일 생성까지 시험한다.

### 7.3 Pages 배포 워크플로 (`.github/workflows/pages.yml`)

| 항목 | 값 |
|---|---|
| 트리거 | `workflow_dispatch` 만(**예약 없음** — CI 머리말 규칙. VM 이 디스패치하거나 사람이 누른다) |
| 권한 | `contents: read`, `pages: write`, `id-token: write`(배포 잡만) |
| 단계 | checkout(main) → node 22 `npm ci --ignore-scripts` → `npm run build:public` → checkout(`ref: public-data`, 경로 `public-data/`) → `node scripts/merge-public-data.mjs`(JSON 만, 이름 허용 목록 — `manifest.json` 에 적힌 것만) → `check-bundle.mjs` → `actions/upload-pages-artifact` → `actions/deploy-pages` |
| 비밀 | 쓰지 않는다(데이터는 공개 브랜치에서) |
| 승인 | 레포 설정에서 Pages 소스 = GitHub Actions 로 켜는 것, 배포 키·디스패치 토큰 발급은 **[사용자 승인 필요]** |

### 7.4 섞이지 않게 하는 겹

| 겹 | 무엇 |
|---|---|
| DB 권한 | `kbj_public_export` 는 `prv_*`·`ops` 를 못 읽는다(0001, 통합 시험) |
| import-linter 계약 ⑧ | `kbj.services.public_export` → `kbj.data.private`·`kbj.engines`·`kbj.services.api`·`kbj.services.collectors`·`kbj.services.engine`·`kbj.store.repos` 금지 |
| 정적 시험 | `tests/unit/public_export/test_sql_schemas.py`: 패키지 안 SQL 문자열의 스키마 이름이 전부 `pub_` 로 시작 |
| 내용 검사 | 내보낸 JSON 에 로그인 등급 출처 이름(`KIS`·`KRX`·`ETF_ISSUERS`·`YAHOO`)이 `source` 로 나오면 실패 |
| 번들 검사 | §6.6 |
| 공개 안전 검사 | `scripts/check_public_safety.py` 그대로(레포 전체) |

---

## 8. 시험·검증

### 8.1 골든 — 신고가 보드

| 시험 | 내용 |
|---|---|
| `tests/golden/test_board_golden.py` | §4.1 의 비교(30일 × 5개 payload). 실패 메시지는 (날짜, 파일, JSON 경로, legacy 값, kbj 값) |
| `tests/golden/test_board_golden_meta.py` | `META.json` 의 legacy 커밋이 shim 이전 커밋인지(문자열 고정), 골든 파일 체크섬 고정 |
| `tests/property/test_newhigh_oracle.py` | hypothesis 시계열 → 엔진 라벨 ⊆ 단순 루프(§4.1 6) |
| `tests/unit/engines/board/test_confirm.py` | 같은 날 KIS(잠정) → KRX(확정) 다시 계산 — 바뀐 라벨 목록이 기대와 같다 |
| legacy 다리 | `legacy/etf_traker/board/tests/test_kbj_engine_shim.py` — `board.engine.newhigh.evaluate is kbj.engines.board.newhigh.evaluate` |

### 8.2 검산 (metrics §2·§4·§5)

| 시험 | 내용 |
|---|---|
| 합성 원장 생성기 | `generate(seed)` — 종목: 4구분 합 0·7구분 합 = 기관으로 **만든다**, 휴장일(가짜 캘린더)·거래정지·신규 상장·관리종목 / ETF: Sₜ·NAVₜ 무작위 + 분할(1:10)·병합(5:1)·분배금·신규 상장·상장폐지 |
| `tests/property/test_ledger_checks.py` | 임의 시드에서 ①②③ 잔차 0, 기간 합 = 일별 합, 하나를 깨면 그 행만 `invalid` 이고 집계에서 빠짐 |
| `tests/unit/engines/flows/test_*.py` | metrics §5 표: 기간 집계·연속일(0원·순매도·정지·오늘 정지)·급증(10일 미만)·스크리닝 6기준·관리종목 제외·우선주 별도·장중 → 확정 덮어쓰기와 차이 기록 |
| `tests/unit/engines/etf/test_*.py` | 분할일 순유입 0, 분배금일 순유입 0, 신규 상장 제외·'신규' 표시, 상장폐지 전날까지, 유형 규칙 표, 괴리율 경계(0.5% 정확히) |
| `tests/unit/engines/market/test_*.py` | 시장 거래대금에 ETF·ETN·리츠 없음, 시장폭 unknown, 쏠림 공식 손계산 1건 |

### 8.3 장중 1시간 무결측 시뮬레이션 (P2 `tests/sim` 확장)

| 항목 | 내용 |
|---|---|
| 하네스 | `tests/sim/harness.py:SimDay` 에 `SimOptions(p3=True, window=…)` — P3 처리기(실제 `kbj.services.collectors.market_intraday`)를 `_sim_handler`(P2 의 계획 작업 흉내 — `harness.py`:575) 대신 붙이고, 저장소는 `kbj.store.repos.memory`, KIS 는 확장한 `FakeKisServer`, 토큰은 auth(같은 시계) |
| 창 | 2026-10-07(수) 09:55 ~ 11:05 KST, 보폭은 P2 와 같이 사건 사이 건너뛰기(`_step_after`:961) |
| 단언 ① 키 | 슬롯 10:00·10:10·…·10:50 × {`KIS:index_quote_intraday`, `sector_quote_intraday`, `inst_foreign_intraday`[KRX], `turnover_rank_intraday`[KRX], `etf_quote_intraday`} 의 데이터 키가 **각각 `done` 1회**(선점 장부), `failed` 로 남은 키 0 |
| 단언 ② 행 | 슬롯마다 `index_intraday` 3행, `sector_intraday` = 가짜 업종 수, `turnover_rank_intraday` = 순위 N × 시장 2, `investor_intraday` = 가짜 가집계 행, `quote_intraday` = 감시 ETF 50 — **모자란 슬롯 0**, 모든 행 `ts` = 슬롯 시작, quality=estimated |
| 단언 ③ 시간 | 슬롯 작업 완료 시각 < 슬롯 + 9분(`deadline_min`) |
| 단언 ④ 한도·토큰 | `FakeKisServer.max_in_window(1.0) ≤ 4`, 토큰 발급은 auth 에서만, auth 밖 발급 0 |
| 단언 ⑤ 원장 | 일별 원장 오늘 행이 슬롯마다 갱신되고(estimated) 이력 표와 마지막 슬롯 값이 같다 |
| 변형 | (a) 10:20 FHPST02400000 HTTP 500 1회 → 슬롯 안 재시도로 완료, (b) 10:30 EGW00201 3연속 → 리미터 감속에도 완료, (c) 10:40 Redis 5초 끊김(P2 `test_redis_blip` 방식), (d) 10:31 스케줄러 재기동(P2 `test_restart` 방식) — 10:30 슬롯 중복 0·결측 0 |
| 하루 판 | `test_intraday_day.py`(`@pytest.mark.slow`): 09:00~15:30 38슬롯, 수능일 지연 개장(2026-11-19 — `late_open`) 판 |
| 하루 운영 확장 | `test_one_day_p3.py`: P2 24시간 창에 P3 실제 처리기(`krx.daily`·`market.close_collect`·`board.daily`·`board.confirm`·`etf.collect`(가짜 운용사)·`public.export`) — 중복 수집 0, KIS 초당 ≤ 4, 원장 quality 전이 estimated → ok → (다음 날) krx, `eod_reconcile` 행 존재, 보드 artifact 5종 존재. 기대 키 수는 등록부에서 계산(하드코딩 금지) |

### 8.4 API 시험

| 시험 | 내용 |
|---|---|
| `tests/unit/api/test_auth.py` | §5.4 표 |
| `test_routes_*.py` | 라우트마다: 세션 없음 401, 정상 200 + 봉투(source·as_of·quality 필수 — 모델 검증), 쿼리 422, 메모리 저장소 + 합성 원장 결과가 엔진 직접 호출과 같다 |
| `test_no_external.py` | 앱 전체를 `httpx` 전송이 호출되면 실패하는 가드 아래에서 모든 라우트 호출 → 외부 호출 0(D-P3-3) |
| `test_webhook_route.py` | §5.5 |
| `test_headers.py` | 보안 헤더·`Cache-Control: private`·`no-store`(auth) |
| `test_openapi_fresh.py` | `python -m kbj.services.api openapi --check` |
| `test_perf.py` | 스크리너(2,700종목 × 20일 합성) 미적중 응답 시간 측정 — 실패 기준은 넉넉히(3초) [제안] |

### 8.5 프런트 시험

§6.8.

### 8.6 CI 잡 (`.github/workflows/ci.yml`)

| 잡 | 바뀌는 것 |
|---|---|
| `lint` | 그대로(ruff·format·pyright·lint-imports) — 계약 ⑧~⑩ 포함 |
| `test-kbj` | 골든·API·public_export·엔진 시험이 `tests/` 안이라 그대로 돈다(`-m "not sim and not integration"`) |
| `kbj-integration` | 0007~0009 적용·저장소 Pg 시험 포함 |
| `sim` | `tests/sim -m sim`(새 장중 시험 포함, `slow` 제외) |
| `canonical` | 줄어든 기준선, 새 그룹 `etf_issuers` |
| `public-safety` | 그대로 |
| **`web`(신규)** | `actions/setup-node`(22, 캐시 npm) → `cd web && npm ci --ignore-scripts` → `npm run lint` → `npm run typecheck` → `npm test` → `npm run build:public && npm run build:login` → `node scripts/check-bundle.mjs` → 타입 신선도 |
| `legacy` | 줄어든 board·etf·flow 시험(§1.11) |
| `pages.yml`(별도) | 수동 — CI 아님 |

### 8.7 실데이터 검증 (키를 받은 뒤)

| # | 할 일 | 남길 곳 |
|---|---|---|
| 20 | `python -m kbj.services.engine.verify --date <D>`: 원장 ①②③ 잔차 분포·검산 불가 수, ETF 분할 감지 목록 | `docs/probe_results.md` §7 #20(건수·분포만, 값 없음) |
| 21 | KRX OpenAPI 일별 제공 시작일, 주식·ETP 공표 시각, ETF 필드·순자산 단위 | #21 |
| 22 | KIS FHKST01010900 금액 단위·기타법인·7구분, ETF 코드로 되는지 | #22 |
| 23 | FHPTJ04400000 회차·상위 개수, FHPST01710000 거래대금 정렬·개수 | #23 |
| 24 | FHPUP02100000·FHPUP02140000(지수·업종) 존재·코드·거래대금 필드 | #24 |
| 25 | 거래소 구분 `J`·`NX`·`UN`, 응답이 통합인지 | #25(체크리스트 #14·#19 이어서) |
| 26 | Q1 비교(5거래일 KIS 마감 대 KRX) — `eod_reconcile` 집계 | #26 |
| 27 | 운용사 9곳 어댑터 실응답(약관 확인 포함) | #27 |

---

## 9. 구현 순서와 병렬화

### 9.1 웨이브

| 웨이브 | 묶음(동시) | 앞 웨이브에서 필요한 것 | 예상 |
|---|---|---|---|
| **1** | **M**(DB·저장소·행 자료형·설정·카탈로그·등록부·metrics 절), **W**(프런트 셸·디자인·부품·데이터 소스) | 없음 | 1.5일 |
| **2** | **C**(수집 처리기·KIS 파서·백필), **E1**(신고가 승격·골든), **E2**(시장·수급·스크리닝 엔진), **E3**(ETF 엔진·운용사 어댑터·구성종목), **A**(API·로그인·웹훅), **X**(공개 내보내기·Pages) | M 의 `kbj/core/rows.py`·`kbj/store/repos/*`·마이그레이션·설정·카탈로그. A 는 엔진 시그니처(§1.3~§1.5 — 이 문서)에 맞춰 짜고, 엔진이 늦으면 readers 시험만 메모리 가짜로 먼저 | 3일 |
| **3** | **W2**(페이지 1·2·4·10 위젯·띠·타입 생성), **S**(시뮬레이션·CI·legacy 정리·통합·enabled 뒤집기·문서) | W2: W 셸 + A 의 `openapi.json`·픽스처. S: 웨이브 2 전부 | 2.5일 |

합계 약 7일(PLAN 6~8일). 묶음 10개.

### 9.2 묶음별 파일 (겹치지 않는다)

| 묶음 | 손대는 파일 |
|---|---|
| M | `kbj/core/rows.py`, `kbj/store/migrations/0007_board.sql`·`0008_market_flows_p3.sql`·`0009_etf.sql`, `kbj/store/repos/**`, `kbj/store/redis_keys.py`, `kbj/config/settings.py`, `kbj/data/private/kis/datasets.py`, `kbj/data/private/etf_issuers/{__init__,datasets}.py`, `kbj/data/catalog.py`, `config/jobs.yaml`(웨이브 1), `config/limits.yaml`, `config/markets.yaml`, `config/calendar_events.yaml`, `pyproject.toml`·`uv.lock`(웨이브 1 — 의존성), `docs/metrics.md`, `docs/secrets.md`, `.env.example`, `tests/test_store_layout.py`, `tests/test_settings.py`, `tests/test_import_contracts.py`(`RUNTIME_IMPORTS` 만), `tests/unit/core/test_rows.py`, `tests/unit/store/**`, `tests/unit/data/test_catalog*.py`, `tests/unit/kis/test_kis_datasets.py`, `tests/unit/scheduler/test_registry_validation.py`(P3 작업 행), `tests/integration/test_migrations_pg.py`, `tests/integration/test_repos_pg.py` |
| W | `web/**` 중 `src/pages/p1_market.ts`·`p2_board.ts`·`p4_flows.ts`·`p10_etf.ts`·`src/app/ribbon.ts`·`src/api/**`·`test/fixtures/api/**`·`test/pages/**` **를 뺀 전부**(`package.json`·`package-lock.json` 포함) |
| C | `kbj/data/private/kis/{investors,quotes,ranks,etf,index}.py`, `kbj/services/collectors/{krx_daily,reconcile,market_close,market_intraday}.py`, `kbj/services/scheduler/__main__.py`(`backfill` 하위 명령 — 이 파일은 C 만), `tests/fakes/kis_server.py`·`krx_server.py`, `tests/fixtures/synthetic/kis/**`·`krx/**`(추가), `tests/unit/kis/test_parse_*.py`, `tests/unit/collectors/test_{krx_daily,reconcile,market_close,market_intraday}.py` |
| E1 | `kbj/engines/board/**`, `kbj/services/engine/{__init__,board}.py`, `config/board.yaml`, `config/knowledge/**`(git mv), `tests/unit/engines/board/**`, `tests/golden/**`, `tests/oracles/**`, `tests/property/test_newhigh_oracle.py`, `legacy/etf_traker/board/engine/**`, `legacy/etf_traker/board/config/settings.yaml`, `legacy/etf_traker/board/knowledge/**`(이동), legacy board 의 승격 시험 삭제·`test_kbj_engine_shim.py` |
| E2 | `kbj/engines/flows/**`, `kbj/engines/market/**`, `kbj/services/engine/verify.py`(실데이터 검산 CLI), `tests/unit/engines/flows/**`·`market/**`, `tests/property/test_ledger_checks.py`, `tests/fixtures/synthetic/ledger_gen.py` |
| E3 | `kbj/engines/etf/**`, `kbj/data/private/etf_issuers/` 중 `datasets.py`·`__init__.py` 를 뺀 전부, `kbj/services/collectors/etf_holdings.py`, `tests/unit/engines/etf/**`, `tests/unit/etf_issuers/**`, `tests/fakes/etf_issuer_server.py`, `tests/fixtures/synthetic/etf_issuers/**`, `tests/unit/collectors/test_etf_holdings.py`, `legacy/etf_traker/etf_tracker_v9/{themes,tracker,collectors}.py`(shim)·`adapters/**`(shim) |
| A | `kbj/services/api/**`, `kbj/services/notifier/commands.py`(선택), `tests/unit/api/**`, `tests/unit/notifier/test_commands.py`(선택), `web/src/api/openapi.json`, `web/test/fixtures/api/**` |
| X | `kbj/services/public_export/**`, `.github/workflows/pages.yml`, `scripts/build_public_site.sh`, `tests/unit/public_export/**`, `tests/test_workflows.py` |
| W2 | `web/src/pages/{p1_market,p2_board,p4_flows,p10_etf}.ts`, `web/src/app/ribbon.ts`, `web/src/api/types.gen.ts`, `web/test/pages/**` |
| S | `tests/sim/**`, `config/jobs.yaml`(웨이브 3 — enabled 뒤집기), `pyproject.toml`(웨이브 3 — 계약 ⑧~⑩), `.github/workflows/ci.yml`, `Dockerfile`, `docker-compose.yml`, `scripts/canonical_baseline.txt`, `scripts/check_canonical.py`, `tests/test_check_canonical.py`, §1.10 의 legacy 삭제 파일(board ingest·etf `market`·`verify`·flowlab 수집·monitor/flow 수집·SD 파일)과 그 시험, `scripts/test_legacy.sh`(시험 목록), `tests/unit/scheduler/legacy_jobs.txt`(바뀌면), `docs/PLAN.md`, `docs/probe_results.md`, `docs/adr/0008~0012`(각 묶음 초안을 확정) |

### 9.3 공유 파일 — 누가 언제

| 파일 | 웨이브 1 | 웨이브 2 | 웨이브 3 |
|---|---|---|---|
| `pyproject.toml`·`uv.lock` | **M**(fastapi·uvicorn 추가 후 `uv lock`) | 아무도 안 고침 — 필요하면 M 담당에게 요청 목록으로 | **S**(계약 ⑧~⑩; 의존성 추가가 있으면 `uv lock`) |
| `config/jobs.yaml` | **M**(P3 행 전부 `enabled: false`, owner·collects·writes 확정) | 안 고침 | **S**(enabled 뒤집기·검증) |
| `config/limits.yaml`·`markets.yaml`·`calendar_events.yaml` | **M** | 값 조정 요청만 | S(필요 시) |
| `web/package.json`·`package-lock.json` | **W** | 안 고침 | W2 는 고치지 않는다(필요 의존성은 W 가 웨이브 1 에 미리 — §6.1 목록) |
| `docs/metrics.md` | **M**(§6~§8 정본) | 안 고침(틀린 곳은 S 에 메모) | S |
| `tests/fakes/kis_server.py` | — | **C** | S 는 쓰기만 |
| `kbj/services/scheduler/__main__.py` | — | **C**(`backfill`) | — |
| `.github/workflows/ci.yml` | — | — | **S** |
| `scripts/canonical_baseline.txt` | — | 줄어드는 legacy 파일이 생기면 S 에 넘김 | **S** |
| ADR | — | 각 묶음이 초안(`0008` A·`0009` E1·`0010` E3·`0011` X·`0012` M — 잠정→확정 원장) | S 가 번호·상태 확정 |

### 9.4 묶음별 완료 확인 명령

| 묶음 | 명령(모두 종료 코드 0) |
|---|---|
| M | `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest tests/test_store_layout.py tests/test_settings.py tests/test_import_contracts.py tests/unit/core tests/unit/store tests/unit/data tests/unit/kis tests/unit/scheduler -q && uv run python -m kbj.services.scheduler validate config/jobs.yaml && uv run lint-imports` (+ Docker 있으면 `uv run pytest -m integration tests/integration/test_migrations_pg.py tests/integration/test_repos_pg.py`) |
| W | `cd web && npm ci --ignore-scripts && npm run lint && npm run typecheck && npm test && npm run build:public && npm run build:login && node scripts/check-bundle.mjs` |
| C | `uv run ruff check && uv run pyright && uv run pytest tests/unit/kis tests/unit/collectors tests/unit/fakes -q && uv run python -m kbj.services.scheduler validate config/jobs.yaml` |
| E1 | `uv run pytest tests/unit/engines/board tests/golden tests/property/test_newhigh_oracle.py -q && uv run pyright && bash scripts/test_legacy.sh board` |
| E2 | `uv run pytest tests/unit/engines/flows tests/unit/engines/market tests/property/test_ledger_checks.py -q && uv run pyright` |
| E3 | `uv run pytest tests/unit/engines/etf tests/unit/etf_issuers tests/unit/collectors/test_etf_holdings.py -q && uv run pyright && bash scripts/test_legacy.sh etf` |
| A | `uv run pytest tests/unit/api tests/unit/notifier -q && uv run pyright && uv run python -m kbj.services.api openapi --check` |
| X | `uv run pytest tests/unit/public_export tests/test_workflows.py -q && bash scripts/build_public_site.sh --dry-run` |
| W2 | W 의 명령 + `cd web && npm run gen:types -- --check` |
| S | `uv run ruff check && uv run ruff format --check && uv run pyright && uv run lint-imports && uv run pytest -q && uv run pytest tests/sim -m sim && uv run python scripts/check_canonical.py && uv run python scripts/check_public_safety.py && bash scripts/test_legacy.sh all` + W 명령 + CI 녹색(푸시 뒤 확인) |

---

## 10. 위험·[확인 필요]

| # | 위험·미정 | 영향 | 대응·기본값 |
|---|---|---|---|
| R1 | KIS 투자자 TR 필드·**단위**(ET 는 백만원으로 읽음 — `board/ingest/kis.py`:188·225) 미실측 | 금액이 10⁶ 배 틀림 | 파서가 단위를 상수 하나로, 합성 시험 + 체크리스트 #22. 시장 수급 "셋 다 0 → 질의 불성립" 규칙 유지 |
| R2 | ⚠ FHKST01010900 이 **3구분**(개인·외국인·기관)이면 기타법인이 없어 **검산 ①을 실데이터에서 할 수 없다** | 완료 기준 "실데이터 ①②③ 0 차이" 를 못 채움 | 검산 불가를 0 으로 바꾸지 않는다(§4.2). 4구분 TR(`FHPTJ04160001` [추정 — conflict_map Q7])을 실측해 데이터셋 추가 [실측 필요]. 기관 7구분 없으면 ② 도 불가 → PLAN 기준을 "가능한 검산 0 차이 + 불가 사유 기록"으로 해석할지 **[결정 필요]** |
| R3 | NXT 거래소 구분(KIS `J`·`NX`·`UN`, KRX OpenAPI 의 NXT 포함 여부) | 거래대금·순매수 과소, venue 마다 호출 배수 | D-P3-9 기본 KRX 만 + 꼬리표, 체크리스트 #25 |
| R4 | 마감 수집 약 5,700건·24분(§3.7) | `board.daily` 지연, venue 늘리면 48분 | 멀티종목 TR 실측, 유니버스 하한(Q11 — 시총·거래대금)으로 줄이기 [제안], `board.daily` 는 의존으로 기다림 |
| R5 | 가집계·순위 TR 회차·상위 개수 미실측 | 장중 위젯 행 수 | 가짜 서버는 설정값, 체크리스트 #23 |
| R6 | 지수·업종 TR(FHPUP02100000·FHPUP02140000)은 [추정 TR] | 페이지 1 장중 지수·히트맵 | 없으면 장중 타일은 '전일 확정'만(quality 표시), 체크리스트 #24 |
| R7 | KRX 공표 시각·백필 제공 시작일·일 한도(8,000 [확인 필요]) | 확정 시각, 역사적 신고가 깊이 | D-P3-11, 체크리스트 #21·#12 |
| R8 | 역사적 신고가: board.db `alltime` 이관(네이버 파생) 여부 | 오래된 종목의 hist 공백 | 기본: 이관 안 함 + hist None 사유 **[결정 필요]** |
| R9 | ETF 분할 감지 오탐·누락 | 가짜 순유입 | 감지는 `estimated`, 수동 표 우선, 큰 설정과 겹치면 감지 안 함(비율 + NAV 동시 조건). 실데이터 감지 목록 검토(#20) |
| R10 | ETF 순자산 공표 단위·반올림 | 검산 ③ 허용오차 | §4.3 허용오차식, #21 |
| R11 | 운용사 사이트 구조 변경·약관 | 구성종목 결측 | 운용사마다 격리·`empty_streak`, 등급 로그인, 실측 #27. 네이버 폴백 없음(U4) |
| R12 | 프런트 의존성 lock(공개 레포 공급망) | 악성 패키지·라이선스 | dev 의존성 9개만, `package-lock.json` 커밋, `npm ci --ignore-scripts`, 버전 고정. 자동 갱신 봇은 예약이 필요해 두지 않는다 [확인 필요] |
| R13 | Pages 배포 승인(Pages 활성·배포 키·디스패치 토큰) | 공개판 미배포 | P3 는 코드·워크플로·로컬 빌드까지, 활성은 **[사용자 승인 필요]** |
| R14 | TradingView 위젯의 KRX 심볼·히트맵 지원·약관 범위 | 공개 페이지 1 빈 칸 | 안 되면 자물쇠 + 안내 [확인 필요] |
| R15 | 로그인은 TLS 프록시 전제(`__Host-` 쿠키) | 평문 노출 | 프록시 설정은 레포 밖(VM) **[확인 필요]**, 개발 모드는 루프백에서만 비보안 쿠키 |
| R16 | board 순수 분리 때 계산 순서·반올림 차이 | 골든 불일치 | 함수 본문을 그대로 옮기고 I/O 만 뺀다, 허용오차 비교, 정규화 표는 문자열 1개뿐 |
| R17 | legacy 삭제(§1.10)가 남는 legacy 시험(run.py 명령·SD 검사 래퍼·flowlab selftest)에 걸림 | legacy CI 실패 | 삭제 전 import 그래프 확인(S), 걸리는 시험은 대체된 기능의 시험이면 함께 삭제·기록, 아니면 삭제를 P4 로 |
| R18 | `brief.closing` 을 P5 로 미루면 마감 요약이 없는 기간이 생긴다 | U2 운영 공백 | **[결정 필요]** — P3 에 넣으면 +1일(엔진 값으로 템플릿만, LLM 없음) |
| R19 | 장중 시장 외국인·기관(띠)은 GX poller(P7) — P3 에는 '마감'값뿐 | 띠의 장중 값 공백 | 칩에 '마감' 꼬리표 |
| R20 | import-linter 계약을 소스 모듈보다 먼저 넣으면 실패(P2 R14) | CI | 계약 ⑧~⑩ 은 S 가 웨이브 3 에 |
| R21 | Timescale 보존·압축 정책(장중 표 90·30일)과 디스크 | VM 디스크 | 0008·0009 에 정책 [제안], `ops.nightly` 가 크기 health |
| R22 | 관리·정지·정리매매 플래그 필드 미실측 | 스크리너 기본 제외 불가 | 모르면 제외하지 못한 수(`n_status_unknown`)를 함께 표시 |
| R23 | 장중 시장 거래대금(지수 누적)과 확정(종목 합) 정의 차이 | 숫자 튐 | 장중 값은 '지수 기준·잠정' 따로 표시, 마감 뒤 확정으로 교체 |
| R24 | legacy GX 가 P3 동안 VM 에서 도는지(KIS 버킷 공유) | 장중 호출 여유 | P2 §6.9 그대로 legacy 는 VM 에서 돌리지 않는다 [확인 필요] |
| R25 | 금통위 일정 수기 설정(`calendar_events.yaml`) | 날짜 오류 | 출처(한국은행 공표) 주석 + 연 1회 갱신, P5 경제 캘린더가 대체 |

### 이 설계로 남길 ADR (초안 → S 가 확정)

| ADR | 내용 | 초안 |
|---|---|---|
| 0008 | 로그인 API·단일 사용자 로그인·웹훅 서버·API 는 외부 호출 없음(D-P3-3·4) | A |
| 0009 | board 엔진 승격 — 골든 먼저, 순수/IO 분리, legacy shim, 역사적 신고가 깊이(D-P3-10·11) | E1 |
| 0010 | ETF 표·작업 당김, 운용사 어댑터 승격, 유형 규칙, 분할 감지(D-P3-12·13) | E3 |
| 0011 | 공개 정적 사이트·고아 브랜치·수동 Pages(D-P3-15·16) | X |
| 0012 | 잠정 → 확정 원장 우선순위·차이 기록·KRX D+1 대조·거래소 구분 기본값(D-P3-7·8·9) | M |

---

## 부록 A. 이 설계가 읽은 근거

| 구분 | 경로 |
|---|---|
| KBJ 문서 | `CLAUDE.md`, `docs/PLAN.md` §2·§4·§8, `docs/DATA_TIERS.md`, `docs/metrics.md`, `docs/p2_design.md`(§0·§1·§2·§5.6·§5.7·§6·§8·§12), `docs/adr/0001~0007`, `docs/conflict_map.md`(§1.6~§1.16, §4 Q1~Q13), `docs/secrets.md`, `docs/probe_results.md` §7, `docs/reference/plan_kr_v0.2.md`:32·60~64, `docs/design/preview_synthetic.html`(스타일 토큰·페이지·위젯·상단 띠 칩·공개/로그인 전환 스크립트) |
| KBJ 코드 | `kbj/data/catalog.py`, `kbj/data/spec.py`, `kbj/data/private/kis/datasets.py`, `kbj/data/private/krx/{client,datasets,models,stocks}.py`, `kbj/core/quality.py`, `kbj/config/settings.py`, `kbj/store/migrate.py`, `kbj/store/migrations/0001~0006`, `kbj/services/scheduler/handlers.py`, `kbj/services/collectors/corp_code.py`, `kbj/services/notifier/webhook.py`, `config/jobs.yaml`, `config/limits.yaml`, `config/notify.yaml`, `pyproject.toml`(의존성·import-linter 계약 ①~⑦), `.github/workflows/ci.yml`, `docker-compose.yml`, `.gitignore`, `scripts/check_public_safety.py`, `scripts/canonical_baseline.txt`, `tests/fakes/kis_server.py`, `tests/sim/harness.py`, `tests/test_store_layout.py`, `tests/test_import_contracts.py` |
| ET board | `board/engine/{newhigh,aggregate,rankings,build,db,kinds,themes,config}.py`, `board/config/settings.yaml`, `board/ingest/{kis,krx,pipeline,stockflows}.py`(함수 목록·관문), `board/run.py`(명령·늦은 import :64), `board/tests/`(시험 파일별 수·import), `board/tests/{demo,synthetic_fixtures}.py` |
| ET 그 밖 | `etf_tracker_v9/{themes,tracker,collectors}.py`·`adapters/*.py`(클래스·DDL·`fund_pairs`·`analyze`·옛 환경변수), `monitor/flow/analyze.py`, `monitor/kr/flows.py:windows`, `flowlab/verify.py:check_newhigh` |
| SD | `server.py`(라우트 목록, `_flow_streak`:14918, `_analyze_flow_signals`:14931, `build_market_summary`:13457, `_scrape_naver_sectors`:3232), `scripts/check_*.py` 목록 |
| 실행 확인 | `node v22.22.2`(`/opt/node22/bin/node`) |
