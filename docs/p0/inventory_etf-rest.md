# 인벤토리 — etf-rest

- 대상: `/home/user/p0src/etf-traker/` 중 `board/` 를 뺀 나머지. 범위는 `etf_tracker_v9/`, `dart-report/`, `monitor/bok·kr·flow/`, `flowlab/`, `.github/workflows/*.yml` 16개 전부, 루트 문서 `기능목록.md`·`운영가이드.md` 다. `docs/` 생성물(보드·bok·kr HTML, 랭킹 xlsx)은 코드가 아니어서 산출물 위치만 적었다.
- 스냅샷 커밋은 `0014f57`(2026-10-06)이다. 읽기 전용으로 조사했고 소스는 고치지 않았다. 테스트는 스크래치 사본과 별도 venv에서 돌렸다.
- `.env`, 토큰 캐시(`board/state/.kis_token.json`)는 열지 않았다. 환경변수는 이름만 적었다. 이 범위에는 `.env.example`이 하나도 없다.
- 표기: 경로는 레포 루트 기준이고 `파일:줄` 또는 `파일:함수`로 적었다. 코드로 확인하지 못하고 추론한 것은 **[추정]**으로 표시했다.

---

## 1. 개요

| 항목 | 내용 |
|---|---|
| 하는 일 | board 옆에 붙은 **독립 도구 5개**와 그 실행 워크플로다. ① `etf_tracker_v9` — 국내 ETF 구성종목(PDF)의 일일 편입·편출·비중 변화, ETF 시세·자금흐름을 텔레그램 리포트와 HTML로 낸다. ② `dart-report` — 종목코드 하나로 DART 재무 엑셀(8시트)을 만든다. CLI와 Streamlit을 함께 쓴다. ③ `monitor/bok` — 한은 통화신용정책보고서·수출 104품목·ECOS·FRED·야후·재무부·ForexFactory를 묶은 매크로 대시보드와 텔레그램 아침 브리핑. ④ `monitor/kr` — 전 종목 테마 198·밸류체인 23 섹터 모니터(상대강도·낙폭·로테이션·ETF 탭) 대시보드. ⑤ `monitor/flow` — 보드 52주 신고가 + 거래량 급증 종목 5개의 1~3개월 투자자별 수급 차트 4장과 숫자표를 텔레그램으로 보낸다. 여기에 ⑥ `flowlab` — 보드 state 위에 얹는 수급 레이어·신고가 이벤트 스터디(실험·검증용)가 있다 |
| 규모 (직접 셈) | **Python 83개 파일, 16,637줄**: etf_tracker_v9 15/3,347 · dart-report 10/2,325 · monitor/kr 16/3,192 · monitor/flow 17/3,461 · monitor/bok 2/300 · flowlab 23/4,012. **JS 12개, 1,286줄**(monitor/bok). HTML 템플릿 2개: `monitor/kr/template.html` 2,555줄(352KB), `monitor/bok/template.html` 782줄. 지식 JSON은 `monitor/kr/knowledge/themes.json` 3,641줄과 `industries.json` 993줄. **워크플로 16개, 2,738줄** |
| 테스트 (실행해서 셈) | `monitor/kr` unittest **94건 OK (skip 1** — Playwright 화면 시험`test_page`). `monitor/flow` unittest **129건 OK**(matplotlib 필요). `flowlab selftest` **44/44 PASS**(`flowlab/tests.py:cases`, 네트워크 없음). `dart-report/tests_smoke.py`는 assert 없는 합성 빌드 1회이고 "built" 출력만 확인한다. `etf_tracker_v9`와 `monitor/bok`은 **자동 테스트가 0건**이다(`etf_tracker_v9/verify.py`는 실데이터 10항목 점검, `monitor/bok/fetch-consensus.js --test`는 로직 자가검사) |
| 언어 | Python 3.11/3.12(워크플로마다 다름), Node 22(monitor/bok), 바닐라 JS/HTML 템플릿, bash |
| 주요 의존성 | etf_tracker_v9: `requests`만 쓴다(`requirements.txt`). dart-report: requests·lxml·bs4·PyYAML·openpyxl·pandas·streamlit. monitor/kr: requests·pyyaml·playwright(시험만), **`board.ingest.*`를 import한다**. monitor/flow: requests·matplotlib·fonttools·openpyxl·playwright·pyyaml, `board.ingest.*`·`board.report.telegram`을 import한다. monitor/bok: Node 내장 fetch, openpyxl(extract.py). flowlab: pandas·numpy·lxml·bs4·openpyxl, board 엔진을 import한다 |
| 실행 방식 | 상시 서버는 없다. 전부 **GitHub Actions 예약·수동 실행 + CLI**로 돈다. 외부 스케줄러(cron-job.org)가 `repository_dispatch`로 정시를 맞추는 것이 있다(`etf-daily`, board·xdigest 타입). 로컬 진입점: `python etf_tracker_v9/tracker.py --run`, `python dart-report/run.py --stock`, `streamlit run dart-report/app.py`, `node monitor/bok/daily.js`, `python -m monitor.kr.build`, `python -m monitor.flow.run`, `python3 -m flowlab <sub>`. 배포는 `docs/` 하위(bok·kr)에 커밋만 한다. `pages.yml` 자동 배포는 2026-09-20에 꺼졌다 |
| board 결합 | monitor/kr·flow와 flowlab은 board 코드에 의존한다(KIS·DART·datago·naver 클라이언트, 텔레그램, 엔진). 그래서 **board를 옮기지 않으면 함께 깨진다**. etf_tracker_v9·dart-report·monitor/bok은 독립적이다. 단 monitor/kr/etf.py가 `etf_tracker_v9/market.py`를 import한다 |

---

## 2. 모듈 표

| 경로 | 역할 | 주요 함수·클래스 | 의존하는 외부 출처 |
|---|---|---|---|
| `etf_tracker_v9/tracker.py` | CLI·DB·변동 산출·텔레그램 발송 본체 | `_load_env`(34), `db`(86), `naver_names`(99), `build_universe`(107), `snapshot`(186), `fund_pairs`(249), `analyze`(272), `build_report`(326), `send_telegram`(395), `send_report`(413), `send_telegram_file`(425, **미사용**), `research`(461), `prune`(499), `doctor`(513), `main`(571) | 네이버 etfItemList, 텔레그램 |
| `etf_tracker_v9/collectors.py` | 운용사 PDF 어댑터(내장 4개 + 네이버 TOP10 폴백), 플러그인 자동 등록 | `Kodex`, `Tiger`, `TimeFolio`, `Sol`, `NaverTop10`, `ADAPTERS`, `_load_plugins`(277) | samsungfund.com, investments.miraeasset.com, timeetf.co.kr, soletf.com, m.stock.naver.com `etfAnalysis` |
| `etf_tracker_v9/adapters/*.py` | 운용사 어댑터 플러그인 5개 | `Ace`(papi.aceetf.co.kr), `Hanaro`(hanaroetf.com), `KoAct`(samsungactive.co.kr, 클래스 단위 레이트리밋), `Plus`(plusetf.co.kr), `Rise`(riseetf.co.kr) | 각 운용사 웹(비공식 내부 API·HTML) |
| `etf_tracker_v9/market.py` | ETF 시세·NAV·시총·좌수, 일봉 | `fetch_list`(44), `sync_meta_aum`(60), `fetch_hist`(103), `sync_px`(119), `live_rows`(147), `DDL` | 네이버 `etfItemList.nhn`, `api.finance.naver.com/siseJson.naver` |
| `etf_tracker_v9/dash.py` | 대시보드 집계(등락·기간수익률·자금흐름·거래급증·편입편출) | `base_dates`, `movers`, `period_returns`, `flows`(118), `volume_spikes`(150), `new_listings`, `holdings_new/out`, `weight_moves`, `collect`(274), `export_base`(356), `live_view`(404) | 없음(DB만) |
| `etf_tracker_v9/report.py` | 텔레그램 전문 리포트(HTML, 3,900자 단위로 묶는다) | `build`(222), `_pack`, `_new_holdings`, `_weight_moves`, `_consensus`, `_dropped`, `_movers`, `_periods`, `_flows`, `_misc` | — |
| `etf_tracker_v9/render.py` | 단일 HTML 대시보드 | `build`(153), `telegram`(334, **미사용**) | — |
| `etf_tracker_v9/themes.py` | ETF명 키워드로 테마를 분류하고 파생을 제외 | `classify`(96), `is_active`, `is_excluded`, `issuer_of`, `tag` | — |
| `etf_tracker_v9/verify.py` | 실데이터 품질 점검 10항목(모듈 최상위 스크립트) | `chk` | 네이버 etfItemList, 운용사 |
| `etf_tracker_v9/live_update.py` | 장중 경량 갱신(base.json + 목록 API) | `main` | 네이버 |
| `etf_tracker_v9/run.sh` | 로컬 cron 래퍼(`--init`/`--run`) | — | — |
| `dart-report/dartreport/client.py` | DART OpenAPI 래퍼(디스크 캐시·스로틀·013 처리) | `DartClient`(43), `get_json`(89), `get_zip`(107), `corp_code`(129), `financials`(151), `disclosures`(169), `document_texts`(196) | opendart.fss.or.kr |
| `dart-report/dartreport/statements.py` | 재무제표 수집, 누적값을 분기로 환산 | `fetch_periods`(141), `to_quarterly`(166), `to_annual`(204), `Period` | DART `fnlttSinglAcntAll` |
| `dart-report/dartreport/costs.py` | 주석 '비용의 성격별 분류' 파싱, 7버킷 | `fetch_cost_notes`(212), `_parse_cost_table`(145), `bucketize`(295), `cost_timeseries`(331), `annual_cost_structure`(364) | DART 공시원문 ZIP |
| `dart-report/dartreport/bridge.py` | 영업이익→순이익 워터폴 | `fetch_bridge_details`(60), `build_bridge`(101), `waterfall_layout`(145) | DART 원문 |
| `dart-report/dartreport/orders.py` | 단일판매·공급계약 공시 파싱 | `fetch_orders`(122), `orders_table`(170, `NIPA` 키워드 하드코딩) | DART `list.json` + 원문 |
| `dart-report/dartreport/excel.py` | 8시트 엑셀 빌더(수식·차트) | `build_workbook`(588), `_sheet_*` 7개 | — |
| `dart-report/run.py` · `app.py` | CLI(진단 모드 포함) · Streamlit UI | `main`(52), `_diagnose`(263), `_diagnose_bridge`(219) | DART |
| `monitor/bok/extract.py` | 보고서 xlsx 110시트와 수출 104품목 xlsx를 `out/data.json`으로 변환 | `newest`, `num`, 시트 매핑 | 로컬 `data/*.xlsx` |
| `monitor/bok/fetch-ecos.js` | ECOS 8계열(기준금리·국고채3/10·회사채AA-·원달러·코스피·외국인순매수·CPI) | `readKey`(39), `call`(53), `main`(76) | ecos.bok.or.kr |
| `monitor/bok/fetch-fred.js` | FRED 74계열 + 발표 캘린더 | `readKey`(115), `get`(126), `main`(137) | api.stlouisfed.org |
| `monitor/bok/fetch-yahoo.js` | 유가·금·미국채·VIX·지수·DXY·KRW 15종 | `one`(38) | query1.finance.yahoo.com(비공식) |
| `monitor/bok/fetch-rates.js` | 재무부 명목·실질 곡선, SOFR·EFFR | `treasury`(51), `nyfed`(64) | home.treasury.gov CSV, markets.newyorkfed.org |
| `monitor/bok/fetch-consensus.js` | 주간 컨센서스를 누적 저장하고 FRED 실제치로 상회·하회 판정 | `resolve`(65), `baseline`(88), `main`(93) | nfs.faireconomy.media(ForexFactory, 비공식) |
| `monitor/bok/build.js` · `usmacro.js` · `template.html` | 대시보드 조립(8탭), US 매크로 렌더러, 키 잔존 검사 | build.js 61~72행 키 검사, usmacro 66행 '국면 읽기' | — |
| `monitor/bok/digest.js` · `send-telegram.js` | 아침 브리핑 생성과 발송 | `build`(63), `api`(30), `main`(44) | api.telegram.org |
| `monitor/bok/audit.js` · `publish.js` · `daily.js` · `check-data.py` | 외부 의존·키 감사 / `docs/bok` 복사 / 일괄 실행 / data.json 점검 | — | — |
| `monitor/kr/ingest.py` | 공공데이터 일봉 400영업일 캐시, 지수(네이버), 당일 잠정치(네이버) | `collect`(86), `collect_indices`(132), `collect_provisional`(183) | `board.ingest.datago`(공공데이터포털), `board.ingest.naver` |
| `monitor/kr/engine.py` | 누적지수·기간수익률·낙폭·백분위·RS·섹터 집계·모멘텀·로테이션(순수 계산) | `cumulative_index`(69), `period_return`(95), `drawdown`(134), `percentile_ranks`(170), `relative_strength`(197), `sector_stats`(221), `filter_universe`(289), `momentum`(363), `rotation`(379), `dd_stats`(414), `surge_stats`(431), `sector_rs`(444) | 없음 |
| `monitor/kr/financials.py` | 재무 카드(연간 3·최근 분기) | `collect`(136), `build`(64) | `board.ingest.financials`(DART `fnlttMultiAcnt`) |
| `monitor/kr/flows.py` | 1·5·20일 순매수 금액(KIS만) | `collect`(137, `MAX_CALLS=1200`, `WORKERS=4`), `windows`(84), `sector_flow`(117) | `board.ingest.kis.stock_flows` |
| `monitor/kr/etf.py` | ETF 탭(같은 엔진) | `collect`(261), `classify`(109), `build`(218) | `etf_tracker_v9/market.py`(네이버) |
| `monitor/kr/industries.py` | KSIC 업종 묶음(지식 + 학습 표) | `learn_ksic`(64), `assign`(85), `fetch_induty`(109), `refresh`(144) | `board.ingest.dart`(company.json) |
| `monitor/kr/build.py` | state 조립 → template 치환 → `docs/kr/index.html` | `build_state`(310), `render`(391), `main`(413) | 위 전부 |
| `monitor/flow/run.py` | 대상 선정(보드 newhigh.json) → 수집 → 분석 → 차트 → 발송 | `latest_newhigh`(79), `pick_targets`(125), `build_one`(196), `_ohlcv`(268), `run`(296), `main`(360) | 보드 state, KRX, KIS, 네이버 |
| `monitor/flow/krx.py` | KRX 정보데이터시스템 투자자별(기관 7구분). 익명 우선, 막히면 로그인 | `call`(131), `fetch_daily`(196), `fetch_total`(232), `find_isu`(271), `probe`(533) 외 probe 다수 | data.krx.co.kr(스크래핑, **러너 차단**) |
| `monitor/flow/kissrc.py` | KIS 3구분 폴백 + 금액 단위 자릿수 대조 | `fetch_daily`(49), `unit_check`(77), `probe`(103) | `board.ingest.kis` |
| `monitor/flow/analyze.py` · `returns.py` · `narrative.py` · `chart.py` | 누적·매수일수·기여율·검산 / 수익률 한 줄 / 규칙 기반 문장 / matplotlib 4장 | `analyze`(156), `reconcile`(116), `compute`(53), `lines`(138), `burst`(44) | — |
| `monitor/flow/telegram.py` | 사진 묶음 + 본문 발송 | `send_photos`(36), `send_report`(86), `send_text`(103), `compose`(210) | api.telegram.org(`board.report.telegram` 재사용) |
| `monitor/flow/capture.py` | Playwright로 KRX 실요청을 캡처(쿠키 제외) | — | data.krx.co.kr |
| `flowlab/flows.py` | 모듈 A 수급 레이어. **`board/state/{날짜}/flows.json`에 쓴다** | `metrics`(64), `_grade`(44), `aggregate`(109), `run`(135) | 네이버 `m.stock.naver.com/api/stock/{code}/trend` |
| `flowlab/eventstudy.py` · `prior.py` · `history.py` | 신고가 이벤트 스터디(3년), 승률 되먹임, 누적 조인 | `_events_for`(70), `run`(179), `history.run`(127) | 네이버 siseJson |
| `flowlab/naver.py` · `prices.py` · `krx.py` · `demo.py` · `backfill.py` | 수집기·일봉 캐시·공매도(KRX 계정)·합성 소스·데모 백필 | `daily_ohlcv`(85), `investor_flows`(150), `prices.load`(60), `krx.shorts`(48) | 네이버, data.krx.co.kr |
| `flowlab/report.py` · `verify.py` · `tests.py` · `__main__.py` | HTML/MD 리포트, 검증 4종, selftest, CLI(flows/study/backfill/history/selftest/sample/verify) | `relink_board`(41), `flows_html`(128) | — |
| `flowlab/probe_*.py` (8개) | 일회성 원자료 진단(frgn 표, 시장 수급, 공식 소스) | 각 `main` | 네이버, KRX OpenAPI, 공공데이터포털 |

---

## 3. 외부 데이터 클라이언트 표

| 출처 | 파일 | 인증(환경변수 이름만) | 한도·재시도·캐시 | KIS 토큰 직접 발급 |
|---|---|---|---|---|
| **KIS** 종목별 투자자(`stock_investor`) | `monitor/kr/flows.py:collect` → `board.ingest.kis.stock_flows`. `monitor/flow/kissrc.py:fetch_daily` → `K.call('stock_investor')` | `KIS_APP_KEY`, `KIS_APP_SECRET`, (`KIS_ENV`는 board `kis.base()`) | kr: `MAX_CALLS=1200`, 4스레드, 시총 큰 순. 실패는 종목 단위로 삼킨다. 재시도는 board `kis.call(retries=2)`. 결과 캐시 `monitor/kr/cache/flows.json` | **아니오.** 직접 발급하지 않고 `board/ingest/kis.py:token()`(103)이 `oauth2/tokenP`로 발급해 `board/state/.kis_token.json`에 캐시한다. `monitor/flow/kissrc.py:probe`(103)는 `K.token()`을 명시적으로 부른다. **kr.yml 러너에는 board/state 캐시가 없어 회차마다 새로 발급한다 [추정]** |
| **KRX 정보데이터시스템** | `monitor/flow/krx.py`, `flowlab/krx.py`, `monitor/flow/capture.py` | `KRX_ID`, `KRX_PW`(익명이 막힐 때만 로그인) | 세션·핸드셰이크. Actions 러너에서는 400 `LOGOUT`으로 막혀 있다(README 실측). 그래서 `--source auto`가 KIS로 내려간다 | 아니오 |
| **KRX OpenAPI** | `flowlab/probe_market_official.py`(진단) | `KRX_API_KEY` | 일회성 | 아니오 |
| **DART OpenAPI** | `dart-report/dartreport/client.py`, `monitor/kr/financials.py`·`industries.py`(board 모듈 경유), `flowlab.yml`(`board.run --financials`) | `DART_API_KEY`(키 길이 40 검사, `client.py:50`) | dart-report: 0.12초 스로틀, `.cache/` 디스크 캐시(키는 해시에서 제외), 013은 빈값으로 처리, 일 20,000건 보호. Actions 캐시 `dart-cache-*`. kr: `fetch_induty(max_calls=1500)` | 아니오 |
| **공공데이터포털**(금융위 주식시세) | `monitor/kr/ingest.py:collect` → `board.ingest.datago.fetch_day` | `DATAGO_KEY`(Decoding 키) | 최근 5일만 다시 받고(`RECHECK_DAYS`) 나머지는 날짜별 캐시. 콜드스타트 약 1,600 호출. 지수 API는 403(별도 활용신청 필요) | 아니오 |
| **네이버 금융** etfItemList·siseJson | `etf_tracker_v9/market.py`, `tracker.py:naver_names`, `collectors.NaverTop10`, `verify.py`, `monitor/kr/etf.py`(재사용) | 없음(UA·Referer 위장) | `fetch_list` 3회 재시도, `fetch_hist` 2회. `sync_px` 12스레드. 최초 400일, 이후 45일 | 아니오 |
| **네이버** 시총 목록·지수·일봉 | `monitor/kr/ingest.py:collect_indices`·`collect_provisional`, `monitor/flow/run.py:_ohlcv` (`board.ingest.naver`) | 없음 | 지수 실패 시 지난 캐시(`cache/indices.json`) | 아니오 |
| **네이버** 투자자 trend JSON | `flowlab/naver.py:investor_flows`(150), `flowlab/config.py:73` | 없음 | 3회 지수 백오프(`0.4·2^i`). 옛 `frgn.naver` HTML은 2026-09에 사라져 교체됐다 | 아니오 |
| **ETF 운용사 9곳** | `etf_tracker_v9/collectors.py`, `adapters/*.py` | 없음(스크래핑) | `RETRIES=3`(1.5·n초 백오프), `WORKERS=6`. KoAct는 35초당 20요청 창(`koact.py:26`). 국내종목 0개가 3회 연속이면 추적 제외 | 아니오 |
| **ECOS** | `monitor/bok/fetch-ecos.js` | `ECOS_API_KEY`(**URL 경로에 들어간다**). 환경변수가 없으면 `monitor/bok/.env`나 루트 `.env`를 읽는다 | 25초 타임아웃. 실패해도 exit 0이고 지난 캐시를 유지한다. INFO-200은 빈 결과 | 아니오 |
| **FRED** | `monitor/bok/fetch-fred.js` | `FRED_API_KEY`(쿼리스트링) | 25초 타임아웃, 실패해도 옛 캐시. `cache/fred.json` 939KB를 커밋한다 | 아니오 |
| **Yahoo Finance**(비공식) | `monitor/bok/fetch-yahoo.js` | 없음 | 20초. 실패하면 이 단계만 빠진다 | 아니오 |
| **미 재무부·뉴욕연은** | `monitor/bok/fetch-rates.js` | 없음 | 40초 | 아니오 |
| **ForexFactory**(비공식 피드) | `monitor/bok/fetch-consensus.js` | 없음 | 주간 스냅샷을 누적한다(소급 불가라 캐시 커밋이 필수) | 아니오 |
| **한은 보고서·수출통계 원본** | `monitor/bok/extract.py` | 없음(로컬 xlsx) | 수동 투입(연 2회·월 3회) | 아니오 |
| **텔레그램 Bot API** | 5장 참조 | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | bok: 429 `retry_after`를 지키고 4회 시도. ETF: 0.4초 간격 | — |
| **GitHub API**(아티팩트) | `daily.yml`(DB 복구), `artifacts-gc.yml` | `GH_TOKEN`(=`github.token`) | — | — |

---

## 4. 정기 실행 표

GitHub 예약 실행은 이 계정에서 **약 5시간까지 밀린다**고 실측돼 있다(`daily.yml` 4~16행 주석). 시각은 예약 기준이다.

| 시각(KST)·주기 | 작업 | 정의 위치 | 받는 데이터 | 산출물 |
|---|---|---|---|---|
| 평일 08:00, 16:00 (+외부 스케줄러 `repository_dispatch: etf-daily`) | ETF 일일 리포트 | `.github/workflows/daily.yml`(cron `0 23 * * 0-4`, `0 7 * * 1-5`) → `tracker.py --run` | 운용사 PDF, 네이버 ETF 목록·일봉 | 텔레그램 리포트 여러 통. `etf.db`(Actions 캐시와 아티팩트). 같은 날 중복 발송은 캐시 `etf-sent-{날짜}`가 막는다. 실패하면 텔레그램 경고 |
| 평일 06:40 / 09:30 / 16:30 / 22:00 | 정책·수출 모니터 | `bok.yml`(cron `40 21 * * 0-4`, `30 0 * * 1-5`, `30 7 * * 1-5`, `0 13 * * 1-5`) → extract·fetch-*·build·audit·publish | xlsx, ECOS, FRED, ForexFactory, Yahoo, 재무부·뉴욕연은 | `docs/bok/` + `monitor/bok/cache/*.json` 커밋. **06:40 회차만** 텔레그램 아침 브리핑 |
| 평일 09:30 / 15:30 | 섹터 모니터 | `kr.yml`(cron `30 0 * * 1-5`, `30 6 * * 1-5`) → `python -m monitor.kr.build` | 공공데이터 일봉, 네이버 지수·잠정치, DART, KIS, 네이버 ETF | `docs/kr/index.html` 커밋. 캐시 `kr-cache-*` |
| 평일 18:17 (README에는 18:10) | 수급 리포트 | `flow.yml`(cron `17 9 * * 1-5`) → `python -m monitor.flow.run` | 보드 `newhigh.json`(Actions 캐시 복원), KRX→KIS, 네이버 일봉 | 텔레그램 사진 4장 + 본문. 아티팩트 `flow-charts`(7일) |
| 2·5·8·11월 16일 09:00 | DART 재무 리포트 | `report.yml`(cron `0 0 16 2,5,8,11 *`) → `dart-report/run.py` | DART | 아티팩트 `dart-report-N`(xlsx, 90일). 텔레그램은 없다 |
| 매주 월 12:00 | 아티팩트 정리(종류별 최신 N개) | `artifacts-gc.yml`(cron `0 3 * * 0`) | GitHub API | 삭제만 한다 |
| 화~토 07:00 | 미국장 보드 (board 범위) | `us-board.yml`(cron `0 22 * * 1-5`) | 나스닥·stooq 등 | board 인벤토리 참조 |
| 매일 10:23 (뒤받침) + 외부 dispatch 07:45·08:15 | 구루 브리핑 (board 범위) | `xdigest.yml`(cron `23 1 * * *`) | — | board 인벤토리 참조 |
| (꺼짐) 평일 16:07 등 | 신고가 보드 | `board.yml` cron 전부 주석 처리(2026-09-29). daily는 맥 launchd 16:10(board 범위) | — | dispatch `board-daily`/`board-send`만 남아 있다 |
| push(브랜치 `claude/52-week-high-dashboard-9bedkz`) / 수동 | flowlab 실데이터 검증 | `flowlab.yml` | 보드 캐시, 네이버, DART | `flowlab/ci-out/` 커밋 |
| push(probe 파일) / 수동 | 투자자별 표 점검 | `frgn-probe.yml` | 네이버, KRX OpenAPI, 공공데이터 | 런 요약에만 남는다 |
| 수동만 | 대시보드 배포 / 예시본 발송 / 장마감 시황 미리보기 / KIS 선물 점검 / 미국장 조합 탐색 | `pages.yml`, `preview.yml`, `dashboard-brief-preview.yml`, `kis-futures-probe.yml`, `us-search.yml` | — | Pages(꺼짐) / 텔레그램 / 로그 |
| 로컬 선택 | ETF 트래커 cron 예시 평일 09:10 | `etf_tracker_v9/run.sh`, `운영가이드.md` | — | 문서상 예시다(현행 Actions는 08:00/16:00) |

---

## 5. 텔레그램 표

| 발송 함수 | 메시지 종류 | 발송 시각·조건 | 봇/채팅 환경변수 | 양방향 명령 |
|---|---|---|---|---|
| `etf_tracker_v9/tracker.py:send_report`(413) → `send_telegram`(395), 본문은 `report.py:build`(222) | ETF 전문 리포트(편입·비중변화·컨센서스·제외·등락·기간·자금·기타), HTML, 3,900자 단위로 여러 통 | `--run`일 때(평일 08:00·16:00). `--no-send`면 보내지 않는다. 같은 날 자동 경로에서는 1회만(daily.yml이 `MODE=run-nosend`로 바꾼다) | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | 없음 |
| `tracker.py:main` 폴백 → `send_telegram(build_report(...), parse=None)` | 대시보드 데이터가 없을 때 테마별 변동 요약 | 위와 같다 | 같음 | 없음 |
| `tracker.py:doctor`(513) | 연결 테스트(`getMe` + "ETF 트래커 연결 테스트 성공") | `--check` 수동 | 같음 | 없음 |
| `tracker.py:send_telegram_file`(425) | 대시보드 HTML 문서 첨부 | **호출하는 곳이 없다**(운영가이드에는 보낸다고 적혀 있다) | 같음 | — |
| `daily.yml` "실패 알림" 단계(curl `sendMessage`) | "⚠️ ETF 리포트 실행 실패 — 로그: URL" | 잡 실패 시 | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (Secrets) | 없음 |
| `monitor/bok/send-telegram.js:main`(44) → `api('sendMessage')`, 본문은 `digest.js:build`(63) | 매크로 아침 브리핑(간밤 미국장·미국채·달러원자재·한국 ECOS·발표 결과·이번 주 일정), HTML ≤4,096자 | `bok.yml` 06:40 cron 회차만, `--morning-only`(평일·KST 12시 전·당일 1회). 기록은 `cache/telegram-sent.json`. 새 데이터가 없으면 빈 파일이라 보내지 않는다 | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`(**쉼표로 여러 채팅**) | 없음 |
| `monitor/flow/telegram.py:send_report`(86) = `send_photos`(`sendMediaGroup`) + `board.report.telegram.send` | 수급 리포트: 차트 4장 + 숫자표·문장 | 평일 18:17. `dry_run`이면 보내지 않는다. 검산이 깨진 종목은 건너뛴다 | 같음(`board.report.telegram._cred`로 조달) | 없음 |
| `monitor/flow/telegram.py:send_text`(103) | "📉 수급 리포트 — 대상 없음" 한 줄 | 신고가는 있으나 문턱을 넘은 종목이 0일 때 (`run.py:332`) | 같음 | 없음 |
| `preview.yml`(curl `sendMessage`) | 입력으로 받은 임의 HTML 예시본 | 수동 | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | 없음 |
| board 범위 워크플로 `board.yml`·`us-board.yml`·`xdigest.yml`·`dashboard-brief-preview.yml` | 보드 코멘트·랭킹·구루 브리핑·미국장·시황 미리보기 | — | `TELEGRAM_*`, `XDIGEST_CHAT_ID`, `TRIGGER_INBOX_CHAT_IDS` | board 인박스는 board 인벤토리 참조. **이 범위의 코드에는 양방향 명령(getUpdates·웹훅)이 없다** |

---

## 6. 저장 표

| DB 종류·파일 | 테이블(또는 JSON/state 파일) | 주요 컬럼 | 쓰는 쪽 / 읽는 쪽 |
|---|---|---|---|
| SQLite `etf_tracker_v9/etf.db`(레포 밖이다. Actions 캐시 `etf-db-{run_id}`와 아티팩트 `etf-db-N` 4일·`etf-db-weekly-N` 21일) | `fund` | fund_id(PK), issuer, fund_key, ticker, name, theme, is_active, mktcap, depth, track, empty_streak | 쓰기 `tracker.build_universe`·`snapshot`. 읽기 `analyze`·`dash`·`verify` |
| 〃 | `holding` | (fund_id, asof, code) PK, name, qty, wt, val | 쓰기 `snapshot`. 읽기 `fund_pairs`·`analyze`·`dash` |
| 〃 | `etf_ticker` | code, name | 쓰기 `build_universe`. 읽기 `analyze`(ETF 담은 ETF 제외) |
| 〃 | `change_log` | (run_date, fund_id, code, kind) PK, asof, prev_asof, gap_days, prev/cur qty·wt, qty_pct, qty_pct_adj | 쓰기 `analyze`. 읽기 `build_report`·`dash` |
| 〃 (`market.py:DDL`) | `etf_meta`, `etf_px`, `etf_aum`, `etf_live`(**쓰는 곳 없음**) | code, asof, close, volume / nav, mktcap, units | 쓰기 `sync_meta_aum`·`sync_px`. 읽기 `dash.*`. 병합 대상은 daily.yml `merge_artifact`(holding·etf_aum·etf_px·change_log) |
| JSON `etf_tracker_v9/docs/base.json`(254KB, **레포에 커밋됨**), `docs/index.html`(러너에서만 생성) | 대시보드 기준 데이터 | etf, kpi … | 쓰기 `tracker --run`. 읽기 `--live`·`live_update.py`. daily.yml은 이것을 커밋하지도 업로드하지도 않는다 |
| 디스크 캐시 `dart-report/.cache/`(gitignore, Actions 캐시 `dart-cache-*`) | `{endpoint}_{sha1}.json/zip` | API 응답 원본 | `DartClient` 읽기·쓰기 |
| `dart-report/out/*.xlsx` | 시트 요약·연간비용구조·분기실적·이익브릿지·수주현황·분기비용구조·RAW_분기재무·RAW_미매핑계정 | — | 쓰기 `excel.build_workbook`. 아티팩트로만 나간다 |
| JSON `monitor/bok/cache/`(**커밋됨**) | `ecos.json`, `fred.json`(939KB), `yahoo.json`, `rates.json`, `consensus.json`, `telegram-sent.json` | series{id:[[date,v]]}, errors, keyFrom / events / date·sentAt·chats | 쓰기 fetch-*.js·send-telegram.js. 읽기 build.js·digest.js |
| `monitor/bok/out/`(gitignore) → `docs/bok/`(커밋) | data.json, index.html, digest.html/txt, fig/ | — | extract·build·digest / publish |
| `monitor/bok/data/`(커밋, 약 30MB) | 보고서 원본 xlsx 28.7MB, 보고서 md, 수출 xlsx, 그림 188장 | — | 사람이 넣는다 / extract.py |
| JSON 캐시 `monitor/kr/cache/`(gitignore, Actions 캐시 `kr-cache-*`) | `daily/YYYYMMDD.json`, `indices.json`, `financials.json`, `flows.json`, `ksic.json`, `etf/` | 종목 c·종가·등락률 fltRt·시총 k / by_code{날짜:{f,o,p}} | ingest·financials·flows·industries·etf / build |
| `monitor/kr/knowledge/*.json`(커밋) | themes.json(테마 198 + 밸류체인 23), industries.json(업종 58, 배정 639) | — | 사람과 역산 / build·industries |
| `docs/kr/index.html`(커밋) | `window.__STATE__` | meta(provisional, confirmedThrough, flowCoverage …), stocks, sectors, etf | build.py / 브라우저 |
| 보드 state(Actions 캐시 `board-db-v*`) | `board/state/{날짜}/newhigh.json` | achieved, hits, vol_mult, as_of | board 엔진 / **monitor/flow·flowlab이 읽는다** |
| `board/state/{날짜}/flows.json`, `board/state/financials.json` | 수급 레이어, DART 주요계정 | inst_/frgn_/net_{1,5,20}d_eok, flow_grade … | **flowlab이 board 폴더에 쓴다** / board 화면·flowlab history |
| `flowlab/cache/prices/naver/*.csv.gz`, `flowlab/out/*`(gitignore). `flowlab/ci-out/`(**커밋**, eventstudy_events.csv.gz 2.9MB 등) | 일봉 캐시, 이벤트 스터디, 누적 | — | flowlab / 개발 환경 |
| `monitor/flow/out/`(gitignore, 아티팩트 7일) | png 4장, json, `krx-request.json` | — | run·capture |
| Actions 캐시 마커 `etf_tracker_v9/.sent` | `etf-sent-{KST날짜}` | 시각 | daily.yml |

---

## 7. 환경변수 이름 목록

| 이름 | 용도 | 사용 파일 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | 봇 토큰 | `etf_tracker_v9/tracker.py:397,431,534`, `monitor/bok/send-telegram.js:24`, `monitor/flow/telegram.py:32`(board 경유), `daily.yml`, `bok.yml`, `flow.yml`, `preview.yml` |
| `TELEGRAM_CHAT_ID` | 받는 채팅. bok은 쉼표로 여러 개 | 위와 같음 |
| `DASH_URL` | 리포트 끝 대시보드 링크 | `tracker.py:690`. **워크플로가 넘기지 않아서 쓰이지 않는다.** 운영가이드의 `ENABLE_PAGES`도 연결된 곳이 없다 |
| `MIN_FUNDS`, `ACTION_PP`, `KEEP_DAYS` | 리포트 문턱, 비중변동 %p, 보관일 | `tracker.py:57~62`, `daily.yml`(vars) |
| `WORKERS`, `QTY_FLOOR`, `RETRIES`, `EMPTY_LIMIT` | 수집 동시성, 수량 노이즈 하한, 재시도, 빈 ETF 제외 | `tracker.py:57~61` |
| `ALREADY_SENT`, `MODE` | 중복 발송 차단, 실행 모드(워크플로 내부) | `daily.yml` |
| `PYTHON_BIN` | 로컬 래퍼 파이썬 | `etf_tracker_v9/run.sh` |
| `DART_API_KEY` | DART 인증 | `dart-report/dartreport/client.py:50`, `app.py`, `report.yml`, `kr.yml`, `flowlab.yml`(board 모듈 경유) |
| `STOCKS`, `YEAR_FROM`, `FS_DIV`, `DIAGNOSE` / vars `DART_STOCKS` | DART 리포트 입력 | `report.yml` |
| `ECOS_API_KEY` | ECOS 인증 | `monitor/bok/fetch-ecos.js:40`, `build.js:62`, `bok.yml` |
| `FRED_API_KEY` | FRED 인증 | `monitor/bok/fetch-fred.js:116`, `build.js:62`, `bok.yml` |
| `DATAGO_KEY` | 공공데이터포털 | `kr.yml`(→ `board.ingest.datago`), `flowlab/probe_market_official.py`, `probe_market_unit2.py`, `frgn-probe.yml` |
| `KIS_APP_KEY`, `KIS_APP_SECRET` | KIS 앱키 | `kr.yml`, `flow.yml`(→ `board.ingest.kis`) |
| `KIS_ENV` | 실전/모의(real/vts) | `board.ingest.kis.base`(vars. kr.yml·flow.yml은 넘기지 않아 기본값을 쓴다 [추정]) |
| `KRX_ID`, `KRX_PW` | KRX 로그인(폴백) | `monitor/flow/krx.py:104`, `flowlab/krx.py:27`, `flow.yml` |
| `KRX_API_KEY` | KRX OpenAPI(진단) | `flowlab/probe_market_official.py`, `frgn-probe.yml` |
| `GH_TOKEN`, `REPO`, `DRY_RUN` | 아티팩트 조회·삭제 | `daily.yml`, `artifacts-gc.yml` |
| `PYTHONIOENCODING` | 추출 단계 인코딩 | `bok.yml` |
| (board 범위 워크플로) `ANTHROPIC_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `BSKY_HANDLE`, `BSKY_APP_PASSWORD`, `XDIGEST_CHAT_ID`, `TRIGGER_INBOX_CHAT_IDS` | board 인벤토리 참조 | `board.yml`, `xdigest.yml` |

---

## 8. 기능 목록 (사용자에게 보이는 것)

| 분류 | 기능 | 근거 |
|---|---|---|
| 텔레그램 | ETF 구성종목 변동 리포트: 신규편입·전량제외·비중확대/축소(%p와 수량), 복수 ETF 동시 발생(컨센서스), 종목 사유 제외, 등락 TOP, 기간수익률, 자금 유입·이탈, 거래급증, 신규상장 | `etf_tracker_v9/report.py:build` |
| 텔레그램 | ETF 리포트 실패 경고 | `daily.yml` |
| 텔레그램 | 매크로 아침 브리핑 | `monitor/bok/digest.js` |
| 텔레그램 | 신고가 종목 수급 리포트(누적 순매수·일봉+거래량·외인기관 일별·기관 세부 차트, 투자자 표, 문장 6줄, 수익률 한 줄, 출처 kis/krx 표기) | `monitor/flow/` |
| 텔레그램 | 임의 HTML 예시본 발송 | `preview.yml` |
| HTML 대시보드 | ETF 대시보드 7섹션(당일 등락·기간별 수익률·자금 유입이탈·거래 급증·구성종목 변동·신규 상장·규모), 다크모드, 레버리지 숨김 토글. 장중 갱신 모드가 있다 | `etf_tracker_v9/render.py`, `live_update.py`. **현재 전달 경로가 없다**(10장) |
| HTML 대시보드 | 정책·수출 모니터 8탭(한눈에·US 매크로·통화정책·성장물가·수출 104품목·금융시장·주택가계·AI 투자·자료출처), CSV 버튼, 그림 갤러리 177개 | `monitor/bok/template.html`, `usmacro.js`, README |
| HTML 대시보드 | 섹터 모니터: 테마 198·밸류체인 23·업종·시장·규모 묶음, 기간별 수익률·RS·낙폭·급증·모멘텀·로테이션 사분면·섹터 히트맵·리더, 재무 카드 툴팁, 수급(1·5·20일), ETF 탭, 당일 잠정 배지, 섹터 편집 | `monitor/kr/template.html`, `build.py` |
| HTML 리포트 | flowlab 수급(`docs/flows-{날짜}.html`), 수급 누적(`flows-history.html`), 이벤트 스터디(`eventstudy.html`), 보드 헤더 링크 재연결 | `flowlab/report.py`, `relink_board` |
| 엑셀 | DART 재무 리포트 8시트(요약·연간비용구조·분기실적·이익브릿지·수주현황·분기비용구조·RAW 2개, 차트, 수식) | `dart-report/dartreport/excel.py` |
| 웹 앱 | Streamlit DART 리포트 생성기(키 입력, 미리보기, xlsx 다운로드) | `dart-report/app.py` |
| CLI | `tracker.py --init/--run/--live/--themes/--research/--backfill/--check/--verify`, `run.py --stock/--years/--fs-div/--diagnose-costs/--diagnose-bridge`, `monitor.kr.build --no-fetch/--days`, `monitor.flow.run --codes/--top/--days/--kind/--vol-mult/--source/--dry-run/--check`, `monitor.flow.capture`, `flowlab flows/study/verify/selftest/sample/backfill/history`, `node daily.js` 등 | 각 파일 `main` |
| API 엔드포인트 | **없다**(서버 없음) | — |

---

## 9. 계산 엔진·지표

| 계산 | 함수 | 정의 요지 | 테스트 |
|---|---|---|---|
| ETF 구성종목 변동 | `etf_tracker_v9/tracker.py:analyze`(272), `fund_pairs`(249) | 펀드별로 최근 두 스냅샷을 비교한다(공백 14일 초과는 제외). CU 재산정 보정 = 수량변동률 중앙값을 차감(유의 표본 5개 이상). `QTY_FLOOR=100`, `ACTION_PP=2.0%p` | **없음**(`verify.py` 실데이터 점검만) |
| ETF 설정·환매 추정 | `dash.py:flows`(118) | 좌수 = 시총÷NAV. (좌수 변화 × NAV)를 억원으로 환산. 시총 50억, 절대값 1억 하한 | 없음 |
| ETF 거래급증 | `dash.py:volume_spikes`(150) | 당일 거래량 ÷ 직전 20거래일 **중앙값**이 2배 이상, 중앙 거래대금 1억 하한 | 없음 |
| ETF 기간수익률·등락·신규상장 | `dash.py:period_returns`(94), `movers`(71), `new_listings`(177) | 기준일은 직전 완료 영업일. 1주~1년, YTD | 없음 |
| ETF 테마 분류 | `themes.py:classify`(96) | 이름 키워드 규칙, 파생 제외 | 없음 |
| 누적지수·기간수익률 | `monitor/kr/engine.py:cumulative_index`(69), `period_return`(95) | 등락률(fltRt)을 곱해 수정주가를 대신한다. 결측 20일 초과면 N/A | `monitor/kr/tests/test_engine.py`(골든) |
| 낙폭 | `engine.py:drawdown`(134), `dd_stats`(414) | curDD, nearHigh(≥-20%) 비율, deep(≤-50%) 비율 | 있음 |
| 상대강도 | `engine.py:relative_strength`(197), `sector_rs`(444) | 0.2·1M + 0.2·3M + 0.2·6M + 0.4·1Y 백분위. 섹터는 중앙값, rsTop은 80 이상 비율 | 있음 |
| 섹터 집계·모멘텀·로테이션·급증 | `engine.py:sector_stats`(221), `momentum`(363), `rotation`(379), `surge_stats`(431) | 평균·중앙·breadth·vsKOSPI, rr = 평균수익률/영업일, 사분면 4종, 거래대금 ÷ 20일 중앙값 | 있음 |
| 유니버스 필터 | `engine.py:filter_universe`(289), `etf.py:filter_universe`(168) | 우선주·스팩·코넥스 제외, 거래대금 10억(20일 중앙값), 코스닥 시총 1,000억 또는 상위 200. ETF는 순자산 50억·거래대금 1억 | 있음(`test_etf.py`) |
| 재무 카드 | `monitor/kr/financials.py:build`(64) | 연간 3개, 최근 분기 YoY·QoQ, 기준 분기 = 최빈 제출 분기 | `test_financials.py` |
| 수급 창(kr) | `monitor/kr/flows.py:windows`(84) | 1·5·20일 순매수 억원, 동반 비중(5일), streak | `test_flows.py` |
| 수급 분석(flow) | `monitor/flow/analyze.py:analyze`(156), `cumulative`(75), `buy_days`(68), `institution_share`(103), `reconcile`(116) | 직전 거래일을 0으로 놓고 누적. 보합은 매수일에서 뺀다. KRX 기간합계와 원 단위로 대조 | `test_analyze.py`, 골든(KRX 워크북) |
| KIS 단위 검산 | `monitor/flow/kissrc.py:unit_check`(77) | 금액 ÷ (수량×종가)가 0.2~5.0 범위인지 | `test_kis.py` |
| 수익률 한 줄 | `monitor/flow/returns.py:compute`(53) | 일간·장중고가·1개월(달력)·연초(작년 말 종가) | `test_returns.py` |
| 규칙 기반 문장 | `monitor/flow/narrative.py:lines`(138), `burst`(44) | 집중 구간·교차·전환·쏠림. LLM은 쓰지 않는다 | `test_narrative.py` |
| 대상 선정(신고가 + 거래량) | `monitor/flow/run.py:pick_targets`(125) | 보드 `hits['w52']`(종가·고가 중 하나), `vol_mult` 2배 이상, 상위 5. **신고가 자체는 계산하지 않고 board 결과를 쓴다** | `test_run.py` |
| 수급 레이어 | `flowlab/flows.py:metrics`(64), `_grade`(44) | 순매매량×종가로 억원, intensity bp, concentration, 외인 보유율 변화, 등급(쌍끌이 등), supported(강도 20bp 이상) | `flowlab/tests.py`(44 체크) |
| 신고가 이벤트 스터디 | `flowlab/eventstudy.py:_events_for`(70), `run`(179) | 52주·60일 신고가와 근접을 **자체 재검출**하고 지수 대비 후행 5·20일 초과수익을 축별로 집계 | selftest + `verify.py` |
| 신고가 독립 재현 | `flowlab/verify.py` | 단순 루프로 고가·종가 룩백을 재현해 엔진과 대조(누락 0) | CI 결과 PASS(`flowlab/ci-out/verify.txt`) |
| DART 누적→분기 | `dart-report/dartreport/statements.py:to_quarterly`(166) | Q2 = 반기 − 1Q, Q4 = 연간 − 3Q누적. 재무상태표는 차분하지 않는다 | 스모크만 |
| 비용 7버킷·브릿지 | `costs.py:bucketize`(295), `bridge.py:build_bridge`(101) | 주석 파싱, 기대값 ±5% 대조로 파일 선택, 잔차 플러그 | 스모크만 |
| 컨센서스 판정 | `monitor/bok/fetch-consensus.js:resolve`(65) | 예상치 대비 FRED 실제치로 상회·보합·하회 | `--test` 자가검사 |
| US 국면 읽기 | `monitor/bok/usmacro.js`(66~81) | 근원 PCE 3개월 연율 vs 전년비 ±0.3%p, HY OAS, 실질금리 방향 | 없음 |
| 밸류에이션 | **없다**(이 범위에는 PER·PBR 계산이 없다) | — | — |

---

## 10. 품질·위험 메모

| 구분 | 내용 | 근거 |
|---|---|---|
| **깨진 부분** | ETF 대시보드 HTML(`docs/index.html`)은 러너에서 만들어지지만 **커밋도, 아티팩트 업로드도, 텔레그램 첨부도 하지 않는다.** `send_telegram_file`은 호출하는 곳이 없다. 운영가이드는 "대시보드 HTML 파일이 온다"고 적고 있다 | `daily.yml`(etf.db만 업로드), `tracker.py:425` |
| 깨진 부분 | `DASH_URL`·`ENABLE_PAGES`는 문서에만 있고 워크플로가 넘기지 않는다. `.env.example`은 README·운영가이드가 언급하지만 없다 | `tracker.py:690`, `운영가이드.md` |
| 깨진 부분 [추정] | `flow.yml`은 보드 state를 Actions 캐시(`board-db-v*`)에서 복원한다. 그런데 보드 daily는 2026-09-28부터 맥 로컬로 옮겼고 `board.yml` cron은 꺼졌다. 그래서 **낡은 `newhigh.json`으로 대상을 고를 수 있다.** `run.py`는 날짜 차이를 본문에 적기만 하고 막지는 않는다 | `flow.yml` 96~104행, `board.yml` 4~30행, `monitor/flow/run.py:79,311` |
| 깨진 부분 | bok 아침 브리핑은 `if: github.event.schedule == '40 21 * * 0-4'`일 때만 보낸다. `send-telegram.js` 주석은 "09:30 회차가 대신 보낸다"고 하지만 워크플로가 그 회차에서는 발송 단계를 돌리지 않는다. 06:40 예약이 5시간 이상 밀려 KST 12시를 넘기면 그날 브리핑이 빠진다 | `bok.yml` 108~114행, `send-telegram.js:52,60` |
| 깨진 부분 | KRX 정보데이터시스템이 Actions 러너를 막고 있다. 그래서 monitor/flow는 KIS 3구분으로 내려가 기관 7구분 차트·표가 빠진다. KRX_ID/PW는 막힌 경로에만 쓰인다 | `monitor/flow/README.md` |
| 문서 불일치 | `기능목록.md`(09-04 기준)는 "워크플로 4개, 테스트 507/581건"이라 적었지만 현재 워크플로는 16개다. flow README는 18:10, cron은 18:17이다. 운영가이드는 09:10이라 적었지만 현행은 08:00/16:00이다 | 각 파일 |
| 죽은 코드 | `render.telegram`, `etf_live` 테이블, `build_report`(폴백만), 업로드 유예 가드(`UNTIL: '2026-10-01'`, 만료)가 4개 워크플로에 복붙돼 있다. `flowlab/probe_*.py` 8개는 일회성 진단이다 | `render.py:334`, `market.py:30`, daily·flow·flowlab·report.yml |
| 중복 | 텔레그램 발송 구현이 5개다(tracker.py, send-telegram.js, flow/telegram.py + board/report/telegram.py, curl 2곳). 네이버 siseJson 파서가 3개다(`market._parse_hist`, `flowlab/naver.parse_sise`, board naver). ETF 이름 분류가 2개다(`etf_tracker_v9/themes.py`, `monitor/kr/etf.py:classify`). KRX 클라이언트가 2개이고 로그인 URL이 다르다(`login.cmd` vs `loginProcess.cmd`). `.env` 로더가 4개 이상이다. git push 재시도 루프가 3개 워크플로에 복붙돼 있다. 신고가 검출이 board 엔진 외에 flowlab eventstudy·verify에 따로 있다 | 해당 파일 |
| 결합 | monitor/kr·flow·flowlab이 `board.ingest.*`를 `sys.path` 조작으로 import한다. flowlab은 **`board/state/`에 직접 쓴다.** monitor/kr/etf.py는 `etf_tracker_v9`를 path 주입으로 import한다 | `kr/flows.py:37`, `flow/kissrc.py:27`, `flowlab/flows.py` |
| KIS 토큰 [추정] | 발급 지점은 `board/ingest/kis.py:token()` 하나다. 캐시 파일이 `board/state/.kis_token.json`인데 `kr.yml`은 `monitor/kr/cache`만 캐시한다. 그래서 평일 2회 새로 발급한다. `flow.yml`은 board 캐시 복원본에 토큰이 있으면 재사용하고 없으면 발급한다. 로컬 board와 합치면 하루 여러 번 발급된다(KIS 발급 빈도 제한 위험) | `kr.yml` 65~75행, `board/ingest/kis.py:44,103` |
| 키 노출 위험 | DART 키가 쿼리스트링에 실린다. `requests`의 `HTTPError` 메시지에 URL 전체가 들어가는데 Streamlit `app.py:107`이 `st.exception(exc)`로 **화면에 그대로 띄운다.** Actions 로그는 시크릿을 마스킹하지만 로컬·Streamlit에서는 마스킹되지 않는다. `tracker.send_telegram`은 `requests.post`를 try로 감싸지 않아서 연결 예외 traceback에 `/bot<토큰>/` URL이 찍힐 수 있다(로컬). ECOS 키는 URL 경로에, FRED 키는 쿼리에 들어간다. 다만 build.js·audit.js가 산출물을 검사한다. bok `cache/*.json`의 `errors` 필드는 커밋되는데, 현재는 비어 있고 키 패턴도 0건이다 | `client.py:79~87`, `app.py:106`, `tracker.py:403` |
| 하드코딩 | `digest.js:21` 링크가 원저자 사이트 `rsm0kk.github.io/bok-monitor/`를 가리킨다. `orders_table(nipa_keyword="NIPA")`. 기본 종목 `006110`. `fetch-consensus.js SINCE='2026-09-14'`. `flowlab.yml` push 트리거 브랜치 `claude/52-week-high-dashboard-9bedkz`. 각종 문턱 상수(dash·engine·config) | 해당 행 |
| **공개 레포 이전 시 문제** | ① **실데이터 fixture**: `monitor/kr/tests/fixtures/golden.json`(208KB, 타인 배포본 값), `monitor/flow/tests/fixtures/golden.json`(KRX 워크북 실데이터, 엑시콘 092870). DATA_TIERS상 KRX 원본은 금지다. ② **커밋된 실데이터**: `flowlab/ci-out/`(네이버 수급·DART 재무·이벤트 2.9MB), `etf_tracker_v9/docs/base.json`(네이버 스냅샷), `monitor/bok/cache/`(Yahoo·ForexFactory 비공식 재배포, FRED 939KB). ③ **원본 자료**: `monitor/bok/data/` 약 30MB(한은 보고서 xlsx·md·그림 188장. 수출 104품목 xlsx는 출처·재배포권이 불명확하다 [추정]). `docs/bok/fig`에 같은 그림이 중복으로 있다. ④ **제3자 코드·화면**: `monitor/kr/template.html`(352KB)은 `rsm0kk/kr-sector` 빌드 산출물을 복사한 것이고, `monitor/bok` 전체는 `rsm0kk/bok-monitor`를 옮긴 것이다. 라이선스가 확인되지 않았다 [추정]. ⑤ **스크래핑**: 운용사 9곳 내부 API, 네이버 비공식 API 다수, KRX 정보데이터시스템(로그인), Yahoo, ForexFactory. User-Agent 위장도 있다 | 해당 경로 |
| 운영 비용 | private 레포의 Actions 무료 분이 9/27에 소진됐다. 아티팩트 한도도 9/20에 초과했다(etf-db 1,003MB). 대응으로 평일만 실행하고 GC 워크플로를 두었다. 공개로 전환하면 무료 분 제약은 풀린다 [추정] | `kr.yml` 14~17행, `artifacts-gc.yml` |
| 테스트 공백 | etf_tracker_v9(3,347줄)와 monitor/bok(JS 1,286줄 + py 300줄)에는 자동 테스트가 없다. dart-report는 assert 없는 스모크 1개뿐이다. kr 화면 시험은 Playwright가 없으면 건너뛴다 | 1장 |
