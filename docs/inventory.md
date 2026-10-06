# KBJ 통합 인벤토리 (P0)

- 작성: 2026-10-06. 입력: `docs/p0/inventory_{stock-dashboard,etf-board,etf-rest,gexlab}.md`, `docs/p0/sweep_grep.md`, `docs/PLAN.md` §2·§3·§4, `docs/DATA_TIERS.md`, `docs/probe_results.md`. 빈 곳은 `/home/user/p0src` 원본을 직접 읽어 채웠다.
- 스냅샷: SD=stock-dashboard `f46178c`, ET=etf-traker `0014f57`, GX=gexlab `43a9ed1`(브랜치 `claude/phase1`). 소스는 고치지 않았다. 테스트는 `/tmp/p0test` 사본에서 돌렸다(키 파일 제외 복사).
- 키·토큰·계좌 값은 어디에도 적지 않았다. 환경변수는 이름만 적었다. `.env`·토큰 캐시 파일은 열지 않았다.
- 표기: `레포:파일:줄` 또는 `레포:파일:함수`. **[추정]** = 코드로 끝까지 확인하지 못한 판단. **[제안]** = 이 문서의 권고. ⚠ = 충돌·겹침.
- 확정된 사용자 결정(2026-10-06): **U1** KIS 앱키·텔레그램 봇 공유 → 발급은 auth 한 곳, 발송은 notifier 한 곳, 수신은 notifier 웹훅 하나. **U2** 아침 브리핑 1회·마감 요약 1회. **U3** fixture·제3자 코드는 표만(`conflict_map.md` §2). **U4** 네이버 의존 기능은 로그인 전용, 데이터는 KRX·KIS로 교체, 네이버 스크래핑 미사용.
- 파일 단위 정본 지정·이식 순서·사용자 질문은 `docs/conflict_map.md` 에 있다.
- 완성도 점검(2026-10-06, 원본 재grep): KIS 발급 문자열·`api.telegram.org`·`send_telegram(` 호출부·`add_job(` 43개·`CREATE TABLE`·환경변수 이름·SD 라우트 156개를 원본에서 다시 셌다. 빠진 것(부팅 작업, HTTP 발송 경로 3개, GX `probe.yml` 발급 경로, GX engine 일별 지표, 기능 14건 등)과 DATA_TIERS 와 어긋나거나 애매하게 적힌 등급 8곳을 이 문서에 고쳤다. 테스트 수(GX·board·kr·flow·flowlab·SD 검사 스크립트)는 원본 정적 개수·재실행으로 모두 일치했다.

---

## (a) 프로젝트 한눈 표

| 프로젝트 | 하는 일 | 코드 규모 | 언어·런타임 | 실행 방식 | 저장 | 테스트 (2026-10-06 직접 실행) | 통합 시 주요 위험 |
|---|---|---|---|---|---|---|---|
| **SD** stock-dashboard | 국장(+미장) 대시보드: 시세·테마맵·밸류체인·시황·공시·컨센서스·어닝·밸류에이션·TAM·발굴·백테스트·저널, 텔레그램 push 16종 + 양방향 봇 | Py 59파일 36,987줄(`server.py` 19,112줄·라우트 156·`add_job` 43), JS 13,119줄 | Py 3.11.9(`render.yaml`), Flask·APScheduler·SQLite, 바닐라 JS | Render 무료 web(디스크 휘발) + 맥 crontab 18:00(`scripts/daily_macbook_cron.sh`) + Actions `wake.yml` | SQLite `db/dashboard.db`·`db/llm_cache.db`, `cache/*.json`, 비공개 Gist 백업 | 검사 스크립트 12개 중 오프라인 10개 실행: **9 통과, 1 실패**(`scripts/check_futures_us_index.py` 2절 `NameError: _yf` — 스크립트가 꺼내는 함수 목록이 `server.py` 와 어긋남). 네트워크 필요 2개(`test_collectors.py`·`test_data_json.py`, 네이버 실호출)는 돌리지 않았다. 정적 검사 호출 328개 | 네이버 스크래핑이 시세·수급·일봉·컨센서스의 주 경로(U4로 전면 교체), KIS 토큰 자체 발급, 인증 없는 쓰기·발송 API, 정적 catch-all 로 DB 파일 노출(`server.py:4557`) |
| **ET board** (+`us/`·`guru/`·`xdigest/`) | 국장 60일·52주·역사적 신고가(종가·고가), 섹터·종목 랭킹, 히트맵, 탐지기, LLM 장마감 코멘트, 스윙 시그널, 엑셀, 미국장 보드, 구루 브리핑 | 운영 83파일 24,593줄 + 테스트 73파일 15,807줄, `knowledge/` YAML 20,520줄 | Py 3.11 | 맥 launchd 평일 16:10(`board/scripts/local_daily.sh`) + Actions(`us-board.yml`·`xdigest.yml`) + 외부 cron-job.org dispatch | SQLite `board.db`·`us_board.db`·`backtest.db`, `state/<날짜>/*.json`, 정적 `docs/` | `python -m unittest discover -s board/tests -t .` → **1,319 OK** (3.11·3.12 둘 다) | 네이버 일봉 하루 약 2,800회, KIS 토큰 자체 발급, `getUpdates` 인박스(웹훅과 공존 불가) |
| **ET etf_tracker_v9** | ETF 구성종목(PDF) 편입·편출·비중, ETF 시세·자금흐름 리포트 | 15파일 3,347줄 | Py 3.11, requests만 | Actions `daily.yml` 평일 08:00/16:00(+dispatch `etf-daily`) | SQLite `etf.db`(Actions 캐시·아티팩트) | 자동 테스트 **0** (`verify.py` 는 실데이터 10항목 점검) | 운용사 9곳·네이버 스크래핑, 대시보드 HTML 전달 경로 없음 |
| **ET dart-report** | 종목코드 → DART 재무 엑셀 8시트, Streamlit UI | 10파일 2,325줄 | Py 3.12 | `report.yml` 분기(2·5·8·11월 16일) + 수동 | `.cache/`, `out/*.xlsx` | `python tests_smoke.py` → "built" (assert 없음) | 오류 화면에 키 포함 URL 노출(`app.py:106`) |
| **ET monitor/bok** | 한은 통화신용정책보고서 110시트·수출 104품목·ECOS·FRED·Yahoo·재무부·ForexFactory 대시보드, 매크로 아침 브리핑 | JS 12파일 1,286줄 + Py 2파일 300줄 | Node 22 + Py 3.12 | `bok.yml` 평일 06:40·09:30·16:30·22:00 | `cache/*.json`(커밋), `data/` 39.8MB(커밋) | 자동 0. `node fetch-consensus.js --test` 자가검사는 판정 출력만(통과·실패 집계 없음) | 제3자 코드(`rsm0kk/bok-monitor`), 원본 자료 39.8MB |
| **ET monitor/kr** | 테마 198·밸류체인 23 섹터 모니터(RS·낙폭·로테이션·ETF 탭) | 16파일 3,192줄 + `template.html` 2,555줄 | Py 3.12 | `kr.yml` 평일 09:30·15:30 | Actions 캐시 → `docs/kr/index.html` | `unittest discover -s monitor/kr` → **94 OK**(skip 1, Playwright 화면 시험) | `board.ingest.*` 의존, 제3자 template·사전 |
| **ET monitor/flow** | 신고가+거래량 상위 5종목 투자자별 수급 차트·숫자표 | 17파일 3,461줄 | Py 3.12 | `flow.yml` 평일 18:17 | `out/`(아티팩트 7일) | `unittest discover -s monitor/flow` → **129 OK** | KRX 정보데이터시스템이 러너 차단, 낡은 보드 state 를 읽을 수 있음 |
| **ET flowlab** | 수급 레이어·신고가 이벤트 스터디(실험·검증) | 23파일 4,012줄 | Py 3.11 | `flowlab.yml`(push·수동) | `cache/`, `ci-out/`(커밋 3.3MB) | `python -m flowlab selftest` → **44/44** | `board/state/` 에 직접 씀, 네이버 수급 |
| **GX** gexlab | 코스피200 옵션 GEX 수집·녹화·지표 엔진(Phase 0~3), KIS auth·레이트리미터·WS, KRX 일별, 거래 캘린더 | Py 226파일 70,353줄(테스트 37,277줄) | Py 3.12 고정, uv, httpx·pydantic·psycopg·redis | Docker Compose 상주(redis·db·migrate·auth·scheduler·recorder·poller·ws-gateway·engine) | PostgreSQL16+TimescaleDB, Redis | `uv run pytest -m "not network"` → **3,227 수집, 3,178 통과, 49 건너뜀**(Docker 없는 통합 시험) | 실측 KIS·KRX fixture(U3), 화면·알림·API 없음 |

> 참고: PLAN P1 완료 기준의 "board 테스트 507개"는 `기능목록.md`(09-04) 숫자다. 지금은 board 1,319 + monitor 223 + flowlab 44 이다.

---

## (b) 외부 출처 × 프로젝트 매트릭스 (파일 경로)

등급은 DATA_TIERS §1 + `probe_results.md` F1·F6·F7 반영. "애매하면 로그인" 원칙을 따랐다.

| 출처 | SD | ET board | ET 나머지 | GX | 등급 | KBJ 처리 |
|---|---|---|---|---|---|---|
| KIS REST 국내주식 | `kis_api.py` `get_minute_chart`:155(FHKST03010200)·`get_orderbook`:210(FHKST01010200)·`get_investor_trading`:256(FHKST01010900)·`get_price_detail`:295(FHKST01010100) | `ingest/kis.py` `stock_flows`:254(FHKST01010900)·`market_flows`:214(FHPTJ04040000)·`top_flows`:282(FHPTJ04400000), `ingest/stockflows.py` | `monitor/kr/flows.py:collect`, `monitor/flow/kissrc.py:fetch_daily` (둘 다 board 경유) | — | 로그인 | `kbj/data/private/kis` 하나 |
| KIS REST 지수선물옵션 | `kis_api.get_kospi200_futures`:396(FHMIF10000000) | `tools/probe_kis_futures.py`(FHKIF03020100·FHMIF10000000) | — | `data/kis/rest.py` `KisClient`:98, `services/poller/endpoints.py`(FHPIF05030000/0100/0200·FHMIF10000000·FHPTJ04030000·FHPIO056104C0), `services/scheduler/minute.py`(FHKIF03020200) | 로그인 | GX 정본 |
| KIS 토큰·WS 접속키 | `kis_api._get_token`:50 | `ingest/kis.token`:103 | (board 경유) | `data/kis/auth_client.py:165`(`KisTokenIssuer`, probe 기본 경로 `default_token_provider`:539 ← `scripts/probe_all.py:55`, 로컬·Actions `probe.yml`), `services/auth/service.py:186·465` | — | **auth 한 곳(U1)** |
| KIS WebSocket | — | — | — | `services/ws_gateway/*`(H0IFCNT0·H0IOCNT0·H0MFCNT0·H0EUCNT0), 41건 예산 | 로그인 | GX 정본 |
| KIS 마스터 zip | `kis_api.py:361` | `tools/probe_kis_futures.py:22` | — | `scripts/probe_common.py:199`, `services/scheduler/service.py:115` | 로그인[애매] | GX 정본 |
| KRX OpenAPI | `krx_api.py` `krx_api_call`:59(sto/stk·ksq·knx_bydd_trd, *_isu_base_info, idx/kospi·kosdaq·krx_dd_trd, etp/etf_bydd_trd) | `ingest/krx.py`(sto/stk·ksq_bydd_trd, idx/krx_dd_trd), `ingest/pipeline.py:_apply_krx_snapshot` | `flowlab/probe_market_official.py`(진단) | `data/krx/eod.py`(drv/fut·opt_bydd_trd), `services/scheduler/krx.py` | 로그인 | GX 클라이언트 + board 파서 |
| KRX 정보데이터시스템 스크랩 (`data.krx.co.kr`, pykrx) | `server.py:_pykrx_call`:248 외 47곳, `ohlcv_5y_collector.py`, `data_fetcher.py`, `ohlcv_autofill._fetch_pykrx` | — | `monitor/flow/krx.py`·`capture.py`, `flowlab/krx.py` | — | 로그인(스크래핑) | [제안] 쓰지 않는다 → KRX OpenAPI·KIS |
| DART OpenAPI | `server.py` `poll_dart_disclosures`:10294·`_fetch_dart_quarter`:10552 등, `dart_collector.py`, `earnings_parser.py`, `overhang_parser.py` | `ingest/dart.py`, `ingest/financials.py`, `ingest/triggers.py:_dart`, `run.py:_dart_ksic` | `dart-report/dartreport/client.py`, `monitor/kr/financials.py`·`industries.py`(board 경유) | — | **공개** | 클라이언트 1개 |
| 공공데이터포털 금융위 주식시세 | — | `ingest/datago.py`(옛 V1 경로, F7) | `monitor/kr/ingest.py`(board 경유), `flowlab/probe_market_*.py` | — | 로그인(공공누리 4유형) | V2 경로 어댑터 |
| **네이버 금융 스크래핑** | `server.py` 31곳(`_fetch_naver_live_prices`:801, `_naver_json`:3204, `_fetch_naver_trend`:2797, `_crawl_naver_research`:2351 …), `ohlcv_autofill._fetch_naver`, `consensus_*collector.py`, `theme_crawler.py`, `data_fetcher.py` | `ingest/naver.py`, `flows.py`, `funds.py`, `stockflows.naver_flows`, `triggers.py` | `etf_tracker_v9/market.py`·`collectors.NaverTop10`·`tracker.naver_names`·`verify.py`, `monitor/kr/ingest.py`·`etf.py`, `monitor/flow/run._ohlcv`, `flowlab/naver.py` | — | **제외** | U4: 전부 KRX·KIS로 교체(`conflict_map.md` §1.13) |
| 네이버 검색 API(뉴스) | `server.py:api_news`:2270, `valuechain._fetch_naver_news`, `agents/pipeline.agent1_news` | `ingest/news.py`, `ingest/triggers.py:_naver` | — | — | 로그인 | U4: 로그인 전용, KIS 뉴스 제목으로 교체 검토 |
| ECOS | — | — | `monitor/bok/fetch-ecos.js` | — | 공개(한국은행 작성 표만. 802Y001·731Y001 등 타기관 표는 로그인, F6) | Python 어댑터로 이식 |
| FRED | — | — | `monitor/bok/fetch-fred.js` | — | 공개(정부 시리즈)/로그인(저작권 시리즈) | 시리즈별 등급 |
| Yahoo(yfinance·chart) | `server.py` 78곳(`_fetch_us_market_data`:1464, `api_macro`:9982 …), `consensus_collector`, `tam_modeler`, `valuechain`, `data_fetcher` | `us/sources.yahoo_daily`, `tools/dashboard_brief_preview.py` | `monitor/bok/fetch-yahoo.js` | — | 로그인 | 어댑터 1개 |
| Finnhub | `server.py` `api_calendar_economic`:11059·`_fetch_us_earnings`:11132, `valuechain`, `agents/pipeline` | — | — | — | 로그인[DATA_TIERS 미기재] | 경제 캘린더 출처 재검토 |
| 미 재무부·뉴욕연은 | — | — | `monitor/bok/fetch-rates.js` | — | 공개(정부)[DATA_TIERS 미기재 — §1에 행 추가 필요] | 매크로 어댑터 |
| ForexFactory(비공식) | — | — | `monitor/bok/fetch-consensus.js` | — | 로그인 | 매크로 어댑터 |
| Nasdaq·stooq | — | `us/sources.py` `screener`·`nasdaq_daily`·`stooq_daily` | — | — | 로그인[애매] | 미국 어댑터 |
| ETF 운용사 9곳 | — | — | `etf_tracker_v9/collectors.py`, `adapters/*.py` | — | 로그인 | ETF 어댑터 |
| Google News RSS | — | `ingest/gnews.py` | — | — | 로그인[애매: DATA_TIERS '뉴스 RSS = 공개(헤드라인·링크)'에 해당하나 Google News 는 비공식 RSS] | RSS 어댑터 |
| Bluesky·X | — | `ingest/bsky.py`, `ingest/xsource.py`, `tg_inbox.oembed` | — | — | 로그인 / `xsource` 는 스스로 약관 위반 소지를 적음 | [제안] xsource·xdigest 폐기 |
| Anthropic | — | `writer/claude.py`, `classify/sectors.py`, `guru/*`, `xdigest/*` | — | — | (데이터 아님) | LLM 문장화만(D7) |
| Ollama(로컬) | `earnings_alert_writer.py` | — | — | — | — | [제안] 폐기(템플릿 폴백 유지) |
| Telegram Bot API | `server.py:send_telegram`:5205·`_telegram_setup_webhook`:5882, `earnings_telegram_sender.py:51` | `report/telegram.py:779·822`, `ingest/tg_inbox.py:436`, `guru/pipeline.py:153`, `xdigest/send.py:168` | `etf_tracker_v9/tracker.py:395·425`, `monitor/bok/send-telegram.js`, `monitor/flow/telegram.py:36`, `daily.yml`·`preview.yml` curl | `config/settings.py:29-30`(필드만) | — | **notifier 한 곳(U1)** |
| GitHub Gist/API | `db_backup.py:102` | — | `daily.yml`, `artifacts-gc.yml` | — | — | 폐기(pg_dump) |
| Wikipedia·CNN F&G·alternative.me·investing·esignal | `server.py:_sp500_tickers`:1251·`api_fear_greed`:15997·`_fetch_night_futures`:4696 | — | — | — | 로그인/스크래핑 | [제안] 폐기(야간선물은 GX KIS) |
| KOSIS·KOFIA·관세청 | 없음 | 없음 | 없음(`audit.js:19` 키 이름 감사 목록에만) | 없음 | 공개(KOSIS·관세청, 금투협 종합통계는 **공공데이터포털 15094809 경유만**). KOFIA 사이트 직접 수집(FreeSIS·채권정보센터)은 로그인 — 수집하지 않음(DATA_TIERS §1) | 신규(P5·P6) |

---

## (c) 통합 시간표 — 지금 돌고 있는 정기 작업 전부 (KST 시각순)

- 표기: ⚠중복 = 중복 브리핑, ⚠같은데이터 = 다른 작업과 같은 데이터를 받음, ⚠동시각 = 같은 시각(±5분)에 무거운 작업이 겹침, ⚠발급 = KIS 토큰을 따로 새로 발급할 수 있음.
- GitHub Actions 예약은 이 계정에서 **최대 약 5시간 밀린다**고 실측돼 있다(`ET:.github/workflows/daily.yml` 4~16행 주석). 미국 정규장 마감은 서머타임(2026-11-01까지) 05:00 KST, 그 뒤 06:00 KST.

### c-1. 상시·주기 작업

| 주기 | 작업 | 정의 위치 | 받는 데이터 | 산출 | 표시 |
|---|---|---|---|---|---|
| 1초 | 세션 상태 머신, 마스터·KRX·분봉·개장확인·무결측 트리거 | GX:`services/scheduler/service.py:422` + `core/calendar.state_at` | 시계 | Redis `session:state`, `session_log` | — |
| 30초 step(만료 60분 전 갱신) | KIS 토큰·WS 접속키 갱신 | GX:`services/auth/service.py:364` | KIS oauth2 | Redis `kis:token`·`kis:ws_key` | 유일 발급자 후보 |
| 1초/30초/60초 | 옵션 체인 보강·전광판·투자자 7조합 | GX:`services/poller/planner.py` | KIS REST | `chain_snapshots`·`fut_board`·`investor_flow` | ⚠같은데이터(투자자: ET board `market_flows`) |
| 실시간 | WS 체결·원문 녹화·지표 사이클(약 30초)·CEX(2분) | GX:`services/ws_gateway`·`recorder`·`engine` | KIS WS·DB | `fut_ticks`·`opt_ticks`·`levels`·`metrics` | — |
| 10초 | 하트비트 | GX:`services/bus.py` | — | Redis `health:heartbeat:*` | — |
| 3·10초 | 가격 브로드캐스터 | SD:`server.py:_price_broadcaster`:6867 | 네이버 polling | socket.io | ⚠같은데이터(SD price_sync) |
| 4분 | 자가 핑 | SD:`server.py:7324` | 자기 `/api/health` | — | Render 전용 |
| 5분 | 어닝 파이프라인(파싱→분류→발송) | SD:`server.py:7485` | `disclosure_history`, DART 본문 | `earnings_*`, 텔레그램 | — |
| 60분 | US 캐시→DB | SD:`server.py:7329` | US 캐시 | DB | — |
| 매시 :30 | DB → 비공개 Gist 백업 | SD:`server.py:7376` → `db_backup.backup_db`:180 | DB | Gist | — |
| 4시간 | 매크로·야간선물·옵션·F&G·밸류체인 heat | SD:`server.py:_refresh_global_data_periodic`:6454 | yfinance·스크랩·네이버 검색 | 캐시 | ⚠같은데이터(ET bok 매크로) |
| 평일 08~17시 매분 | DART 공시 폴링 | SD:`server.py:7314` `poll_dart_disclosures`:10294 | DART `list.json` | `disclosure_history` | ⚠같은데이터(ET board `ingest/dart.disclosures` 16:10) |
| 평일 08~20시 :00·:30 | 데이터 워치독 | SD:`server.py:_market_watchdog`:14998 | DB 상태 | 재갱신, 운영 알림 | — |
| 평일 09~14시 :00·:30 | 관심종목 가격 알림 | SD:`server.py:alert_watchlist_price`:6248 | 네이버 유니버스 | 텔레그램 | — |
| 평일 09~15시 :05·:35 | 장중 시세 동기화 | SD:`server.py:7188` `_refresh_prices_from_naver`:8139 | 네이버 polling | `stocks` | — |
| 평일 09~15시 5분 | 발굴 Stage2 실시간 | SD:`server.py:7254` | SQLite | `discover_results` | — |
| 평일 09~15시 10분 | 맞춤 알림 규칙 | SD:`server.py:check_alert_rules`:5579 | `cache/alert_rules.json`, 차트·수급 | 텔레그램 | — |
| 평일 09~15시 :00·:30 | 트레일링 스톱 | SD:`server.py:_check_trailing_stops`:5438 | `server_portfolio.json` | 텔레그램 | — |
| 평일 16~17시 5분 | 시간외 시세 | SD:`server.py:7195` | 네이버 | `stocks.after_hours_*` | — |
| 평일 16:05~20:35 :05·:35 | 장마감 시황 캐치업 | SD:`server.py:closing_brief_catchup`:14805 | 시황 입력 | 밀린 시황 발송 | — |
| (Render 미등록) | 장중 data.json 갱신 | SD:`server.py:7072` | pykrx | — | `DISABLE_AUTO_FETCH` |
| **부팅마다**(재배포·재시작·Render가 잠에서 깰 때 — `wake.yml`·4분 자가 핑과 맞물림) | SD 기동 루틴: Gist에서 DB 복원(`db_backup.restore_db`), 글로벌 데이터·국장 갱신 스레드, 네이버 유니버스 동기화, **setWebhook**(d-1 #20), 미국장 빌드, 일봉 채움(`_startup_ohlcv_fill`:3550), data.json(`_startup_data_json`:8104), yfinance 예열, **장마감 시황 캐치업 → 발송 가능**(`server.py:7178`) | SD:`server.py:_startup`:6919 | Gist·네이버·yfinance·pykrx | DB·캐시·텔레그램 | 정해진 시각이 없다 — 깨어난 시각에 무거운 수집이 몰린다 |
| (예약 없음) | ETF 대시보드 장중 갱신 — README 는 "장중 15분마다"라고 적지만 어느 워크플로·크론에도 등록돼 있지 않다 | ET:`etf_tracker_v9/live_update.py`, `README.md:242` | 네이버 ETF 목록 + `docs/base.json` | `docs/index.html` | 죽은 진입점 [제안] 폐기 |

### c-2. 시각 지정 작업

| KST | 요일 | 작업 | 정의 위치 | 받는 데이터 | 산출 | 표시 |
|---|---|---|---|---|---|---|
| 03:00 | 매일 | DART corp_code 갱신 | SD:`server.py:7318` `init_dart_corp_map_db` | DART corpCode.xml | `dart_corp_map` | ⚠같은데이터(ET `state/.dart_corp.json`, dart-report `.cache`) |
| 03:00 | 매월 1일 | 유니버스 동기화(+텔레그램) | SD:`server.py:7402` | `valuechain_map` | `index_universe` | — |
| 03:10 | 매일 | ETF 종목 표시 | SD:`server.py:mark_etf_stocks`:7574 | `stocks` 이름 패턴 | `stocks.is_etf` | ⚠같은데이터(ETF 목록: ET board `funds`, ETF 트래커) |
| 03:20 | 일 | 테마 매핑 생성 | SD:`server.py:7303` | 유니버스 | `themes_mapping.json` | — |
| 04:00 | 일 | US 유니버스(S&P) | SD:`server.py:7306` | Wikipedia | US 유니버스 | — |
| 05:00 | 화~토 | 브리핑 재료 갱신 | SD:`tg_briefing_refresh` | 매크로·옵션·선물(yfinance·스크랩) | 캐시 | ⚠같은데이터(ET bok 06:40) |
| 05:30 | 화~토 | 익일 예측 브리핑 | SD:`alert_overnight_prediction`:6310 | SPY/QQQ 옵션·야간선물·매크로 | 텔레그램 | **⚠중복**(아침) |
| 05:50 | 화~토 | 미국장 일봉 | SD:`server.py:7359` | yfinance | `us_market_*.json` | ⚠같은데이터(ET us-board 07:00) |
| 06:00 | 월 | 분기 컨센서스 | SD:`server.py:7416` | 네이버 main | `consensus_quarterly` | 네이버(U4) |
| 06:05 | (계획) | GEX 야간 마감 브리핑 | GX:`docs/phase4_design.md` §4 | engine | — | ⚠중복(계획) |
| 06:10 | 화~토 | 미국장 시황 | SD:`send_us_market_summary_telegram`:14554 | yfinance | 텔레그램 | **⚠중복**(ET us-board 07:00) |
| 06:10~08:00 | 거래일 | 야간 선물 분봉 적재, 무결측 판정(대체 07:30) | GX:`services/scheduler/minute.py:98`, `gaps.py:62` | KIS 분봉 | `minute_bars`, `collection_gaps` | — |
| 06:30 | 매일 | 어닝 알림 성과 백필 | SD:`server.py:7498` | ohlcv | `earnings_surprise` | — |
| 06:40 | 평일 | 정책·수출 모니터 + **매크로 아침 브리핑** | ET:`bok.yml` `40 21 * * 0-4` → `monitor/bok/daily.js`, `send-telegram.js --morning-only` | ECOS·FRED·Yahoo·재무부·FF | `docs/bok`, 텔레그램 | **⚠중복**(SD 08:30), ⚠같은데이터(SD 05:00) |
| 07:00 | 화~토 | 미국장 보드 + **미국장 브리프** | ET:`us-board.yml` → `board.run --us-daily/--us-send` | Nasdaq | `docs/us`, 텔레그램(+HTML) | **⚠중복**(SD 06:10) |
| 07:45 / 08:15 | 화~토 | 구루 브리핑(외부 dispatch) | ET:`xdigest.yml` + cron-job.org [추정] | Anthropic web_search | 텔레그램 | 아침 브리핑과 별개 발송 |
| 08:00 | 평일 | ETF 리포트(1회차) | ET:`daily.yml` `0 23 * * 0-4` → `tracker.py --run` | 운용사 PDF·네이버 ETF | 텔레그램(당일 1회) | ⚠동시각(SD 08:00 ×2, GX 08:00), ⚠같은데이터(ETF 목록) |
| 08:00 | 평일 | 발굴 Stage2 스캔(+텔레그램) | SD:`server.py:7272` | SQLite | 발굴 결과 | ⚠동시각 |
| 08:00 | 매일 | 국장 유니버스 | SD:`server.py:7345` | 네이버 | `naver_universe_*.json` | ⚠같은데이터(ET board `sync_universe`) |
| 08:00 | 거래일 | KIS 마스터(PRE_DAY) | GX:`services/scheduler/service.py` | 마스터 zip | Redis `kis:master` | ⚠동시각 |
| 08:05~10:00 | 거래일 | KRX 전일 선물·옵션 적재(10분 재시도) | GX:`services/scheduler/krx.py:75` | KRX OpenAPI(D+1 08:00 갱신) | `krx_*_daily` | — |
| 08:30 | 평일 | **아침 시황 브리핑** | SD:`alert_morning_briefing`:6064 | `macro_data.json`(USD/KRW·WTI·VIX·美10년), Finnhub 일정 | 텔레그램 | **⚠중복**(ET bok 06:40) |
| 08:30 | (계획) | GEX 장전 브리핑 | GX:`docs/phase4_design.md` §4 | engine | — | ⚠중복(계획) |
| 08:44~08:55 | 휴장일 | 닫힌 세션 감시 | GX:`services/ws_gateway/watch.py` | KIS WS | health | — |
| 08:45 | 평일 | AI(규칙) 추천 | SD:`server.py:7294` → `agents/pipeline.run_pipeline`:897 | 네이버 검색·Finnhub | 텔레그램 | ⚠중복(마감 'AI 추천 요약'과 같은 내용) |
| 08:48 | 거래일 | 개장 확인(주간) | GX:`services/scheduler/open_check.py` | `fut_ticks` | health | — |
| 08:55 | 평일(레포 밖) | `kospi-dislocation`(레포 밖) — 같은 KIS 앱키 사용 기록 | GX:`docs/phase1_design.md:92` | KIS | — | **⚠발급**(레포 밖) |
| 09:00 | 2·5·8·11월 16일 | DART 재무 리포트 | ET:`report.yml` | DART | 아티팩트 xlsx | — |
| 09:30 | 평일 | bok 2회차(발송 없음) | ET:`bok.yml` | 위와 같음 | `docs/bok` | ⚠같은데이터(06:40) |
| 09:30 | 평일 | 섹터 모니터 | ET:`kr.yml` → `monitor.kr.build` | 공공데이터 일봉·네이버 지수·DART·**KIS 1,200회**·네이버 ETF | `docs/kr` | ⚠동시각(bok), **⚠발급**(러너에 토큰 캐시 없음) |
| 10:00 | 평일 | 신규 증권사 리포트 | SD:`alert_new_reports`:6282 | 네이버 리서치 | 텔레그램 | 네이버(U4, 대체 없음) |
| 10:23 | 매일 | 구루 브리핑 뒤받침 | ET:`xdigest.yml` `23 1 * * *` | 위와 같음 | 텔레그램 | — |
| 12:00 | (계획) | GEX 정오 브리핑 | GX:`docs/phase4_design.md` §4 | — | — | — |
| 12:00 | 일 | 아티팩트 정리 | ET:`artifacts-gc.yml` | GitHub API | 삭제 | — |
| 15:30 | 평일 | 섹터 모니터 2회차 | ET:`kr.yml` | 위와 같음(KIS 1,200회) | `docs/kr` | **⚠발급**, ⚠같은데이터(수급: SD 15:40·board 16:10) |
| 15:35 | 평일 | 마감 시세 동기화 | SD:`server.py:7191` | 네이버 | `stocks` | ⚠같은데이터 |
| 15:40 | 평일 | **장 마감 요약**(지수·발굴 TOP5) | SD:`alert_closing_summary`:6490 | 네이버 지수 | 텔레그램 | **⚠중복**(16:00·16:10) |
| 15:40 | 평일 | 수급 배치 200종목 | SD:`_refresh_flow_batch` | 네이버 수급 | `flow_cache` | ⚠같은데이터(board stockflows·kr flows) |
| 15:45 | 평일 | data.json(마감) | SD:`server.py:7208` | DB | `data.json` | — |
| 15:45~17:50 첫 사이클 | 거래일 | GX engine 일별 지표(IV 랭크·퍼센타일·IV−HV, `POST_DAY` 1회) | GX:`services/engine/daily.py`, `services/engine/service.py:25` | 그날 `metrics`(ATM IV) | `metrics` | ⚠동시각(SD 15:45~16:10) |
| 15:45 | 평일 | AI(규칙) 추천 | SD:`server.py:7294` | 위와 같음 | 텔레그램 | **⚠중복** |
| 15:48 | 평일 | 신고가 사전계산 | SD:`_prewarm_new_highs`:10944 | ohlcv | `new_highs_kr_*.json` | ⚠같은데이터(board 신고가) |
| 15:50~16:20 | 평일 | Render 깨우기 | SD:`.github/workflows/wake.yml` | — | — | — |
| 15:50 | (계획) | GEX 마감 브리핑 | GX:`docs/phase4_design.md` §4 | — | — | ⚠중복(계획) |
| 16:00 | 평일 | **장마감 시황**(지수·매크로·섹터·특징주·신고가·수급·공시·AI 추천·K200 선물·미국 지수), 하루 1회 | SD:`send_closing_market_summary`:14696 | stocks·ohlcv·flow_cache·KIS 선물·yfinance | 텔레그램 | **⚠중복**, ⚠동시각 |
| 16:00 | 평일 | 발굴 Stage2 스캔 | SD:`server.py:7272` | SQLite | 발굴 결과 | ⚠동시각 |
| 16:00 | 평일 | ETF 리포트 2회차(당일 이미 보냈으면 수집만) | ET:`daily.yml` `0 7 * * 1-5` | 위와 같음 | `etf.db` | ⚠동시각 |
| 16:00~17:50 | 거래일 | 주간 선물 분봉 적재·무결측 판정(대체 17:20) | GX:`minute.py:95`, `gaps.py:59` | KIS 분봉 | `minute_bars` | ⚠동시각(KIS 한도 공유) |
| 16:10 | 평일 | **신고가 보드 daily** → 랭킹·코멘트 초안·HTML·엑셀·스윙 시그널 발송(`BOARD_SEND=1`) | ET:`board/scripts/local_daily.sh`(launchd) | **네이버 일봉 약 2,800회**·KRX·KIS·DART·네이버 검색·Google News·Anthropic | `board.db`, `state/`, `docs/`, 텔레그램 | **⚠중복**, **⚠동시각**(SD 16:10), ⚠같은데이터, ⚠발급 |
| 16:10 | 평일 | 일봉 자동 채움 | SD:`server.py:7226` `_fill_ohlcv_job`:8428 | 네이버 일봉(pykrx 폴백) | `ohlcv` | **⚠동시각·같은데이터**(board 16:10 네이버 일봉) |
| 16:20 | 평일 | data.json(저녁) | SD:`server.py:7211` | DB | `data.json` | — |
| 16:30 | 평일 | bok 3회차(발송 없음) | ET:`bok.yml` | ECOS 당일치 등 | `docs/bok` | — |
| 17:09 | 평일 | 보드 아티팩트 갱신(레포 밖 Claude 루틴) | ET:`board/tools/artifact_check.py` 주석 [추정] | `docs/api/latest.json` | 아티팩트 | — |
| 17:50 | 거래일 | KIS 마스터(PRE_NIGHT) | GX:`services/scheduler/service.py` | 마스터 zip | Redis | ⚠동시각(SD 18:00) |
| 17:59~18:10 | 거래일 | 닫힌 밤 감시·야간 개장 확인 18:03 | GX:`ws_gateway/watch.py`, `open_check.py` | KIS WS | health | — |
| 18:00 | 매일 | 맥 무거운 수집(DART retry→5년 일봉→data_fetcher→밸류에이션 밴드→컨센서스→TAM→Gist→git push) | SD:`scripts/daily_macbook_cron.sh` | DART·pykrx·네이버·yfinance | DB, data.json(git) | **⚠동시각**(GX 야간 전환, SD 18:00). KIS 발급 없음 — 48~63행이 부르는 6개 스크립트(`dart_collector`·`ohlcv_5y_collector`·`data_fetcher`·`valuation_calculator`·`consensus_collector`·`tam_modeler`)는 `kis_api` 를 import 하지 않는다 |
| 18:00 | 평일 | 컨센서스 스냅샷 350종목 | SD:`server.py:7515` | 네이버 | `consensus_snapshot` | ⚠동시각, 네이버(U4) |
| 18:17 | 평일 | **수급 리포트**(차트 4장+표) | ET:`flow.yml` → `monitor.flow.run` | 보드 `newhigh.json`, KRX→KIS, 네이버 일봉 | 텔레그램 | ⚠같은데이터(SD 19:30), ⚠발급 |
| 18:30 | 평일 | 컨센서스 리비전 알림 | SD:`alert_revision_signals`:5948 | `consensus_snapshot` | 텔레그램 | — |
| 19:30 | 평일 | **수급 시그널**(쌍끌이·연속·반전) | SD:`alert_flow_signals`:15196 | `flow_cache` | 텔레그램 | ⚠같은데이터(ET 18:17) |
| 22:00 | 평일 | US 옵션 시그널 | SD:`server.py refresh_options` | yfinance | `options_signal_*.json` | ⚠동시각(bok 22:00) |
| 22:00 | 평일 | bok 4회차 | ET:`bok.yml` | 매크로 | `docs/bok` | — |
| 수동·push | — | SD 7개(`brief-kick`·`collect-diag`·`collector-test`·`verify-deploy`·`summary-probe`·`naver-probe`·`ohlcv-probe`), ET 8개(`board`·`dashboard-brief-preview`·`flowlab`·`frgn-probe`·`kis-futures-probe`·`pages`·`preview`·`us-search`), GX 2개(`ci`·`probe`) | 각 워크플로 | — | — | ⚠발급: ET `dashboard-brief-preview`·`kis-futures-probe`·`board`(예약은 2026-09-29 꺼짐, `repository_dispatch` `board-daily`·`board-send` 통로와 수동 `kis-probe`·`check` 모드는 남음 — `board.yml` 머리 주석), **GX `probe`**(시크릿 `KIS_APP_KEY` → `scripts.probe_all` → `default_token_provider`, `probe.yml:53-57`) |
| 수동(HTTP) | — | SD 인증 없는 실행·발송 API: `POST /api/ops/cron/trigger/<job_id>`(`api_ops_cron_trigger`:18370 — 등록된 잡 43개 아무거나 즉시 실행, 발송 잡 포함, 운영 화면 `static/js/pages.js:8835`), `POST /api/ops/brief/closing`(`api_ops_brief_closing`:8493, `brief-kick.yml`), `POST /api/agent/run`(`api_agent_run`:12489), `GET·POST /api/db/backup`(:4624)·`POST /api/db/restore`(:4634), 텔레그램 테스트 3개(d-1 #19) | `server.py` | — | 텔레그램·Gist | 폐지(인증 뒤 운영 화면으로) |

**겹침 요약**: ① 아침 브리핑성 발송 6건(SD 05:30·06:10·08:30, ET 06:40·07:00, GX 계획 06:05·08:30) ② 마감 요약성 발송 5건(SD 15:40·15:45·16:00, ET 16:10, GX 계획 15:50) ③ 16:10 에 SD·ET가 같은 네이버 일봉을 전 종목 동시에 받는다 ④ 수급을 받는 작업 6개(SD 15:40, ET 09:30·15:30·16:10·18:17, GX 60초) ⑤ KIS 토큰을 GX `auth` 밖에서 따로 발급할 수 있는 실행 경로 **9개** — 정기 5(SD Render 서버 K1, ET 맥 launchd board 16:10 K3, `kr.yml` K4, `flow.yml` K5, 레포 밖 `kospi-dislocation` K10) + 수동 4(ET `board.yml` dispatch·수동 K3, `dashboard-brief-preview` K2, `kis-futures-probe` K6, GX `probe.yml`·로컬 `probe_all` K7). SD 맥 crontab 18:00 은 KIS를 부르지 않는다(발급 지점 번호는 `conflict_map.md` §1.1).

### c-3. 통합 후 시간표 (KBJ 작업 등록부 초안) [제안]

U2에 따라 아침 브리핑과 마감 요약은 하루 1회씩이다. 모든 작업은 GX 캘린더로 거래일·세션을 판정하고, 같은 데이터는 한 수집기만 받는다.

| KST | 요일·조건 | KBJ 작업 | 흡수하는 legacy 작업 |
|---|---|---|---|
| 상시 | — | `auth`(토큰·WS 접속키 유일 발급), `poller`·`ws-gateway`·`recorder`·`engine`(GX 그대로) | SD `kis_api._get_token`, ET `ingest/kis.token` |
| 평일 07:00~19:00 1분 | 거래일 | `filings.dart_feed`(공시 폴링, 잠정실적·오버행 파싱 트리거) | SD `dart_poll`(매분)·`earnings_pipeline_5min`, ET board `ingest/dart.disclosures`·`triggers._dart` |
| 03:00 | 매일 | `ops.nightly`(DART corp_code 1벌, 유니버스·종목 종류, pg_dump 백업) | SD 03:00·03:10·03:20·매월 1일·매시 :30 Gist, ET `.dart_corp.json` |
| 미국 마감+10분(05:10 / 11월부터 06:10) | 화~토 | `us.eod`(미국 일봉·신고가 엔진) | SD 05:50 `us_market_daily`, ET us-board 07:00 수집부 |
| 06:20 | 평일 | `macro.morning`(ECOS·FRED·재무부·뉴욕연은·FF·Yahoo) | ET bok 06:40·09:30 수집, SD 05:00 `tg_briefing_refresh`, SD 4시간 `global_data_refresh` 매크로부 |
| 06:10~08:00 | 거래일 | GX 야간 분봉·무결측(그대로) | — |
| 07:30 | 화~토 | `guru.research`(LLM) | ET xdigest 07:45·08:15·10:23 dispatch |
| 08:00 | 거래일 | `etf.collect` + **ETF 리포트**(ETF는 브리핑이 아니라 별도 1회 push) | ET `daily.yml` 08:00·16:00 |
| 08:05~10:00 | 거래일 | `krx.daily`(전일 주식·지수·ETF·선물·옵션 일별, D+1 08:00 공표) | GX `KrxDaily`, ET board KRX 확정치 덮기, SD `krx_api`, ET kr 공공데이터 일봉 |
| **08:10** | 거래일 또는 간밤 미국 거래일(화~토) | **아침 브리핑(1회)** — ① 간밤 미국: 지수·섹터·Mag7(SD 06:10) + 신고가·등락 온도 ①~⑧ 요약(ET us-board 07:00) ② 금리·달러·원자재(ET bok 06:40 '미국채'·'달러·원자재' + SD 08:30 USD/KRW·WTI·VIX·美10년 → 한 표, 중복 제거) ③ 한국 매크로(ET bok 'ECOS') ④ 야간선물·GEX: 야간 마감 레벨·Flip·기대변동폭(SD 05:30 야간선물 + GX 계획 06:05·08:30) ⑤ 오늘·이번 주 일정과 발표 결과(ET bok '발표 결과'·'이번 주' + SD 08:30 Finnhub 고영향 일정) ⑥ 구루 한 줄(ET guru — 준비 안 됐으면 '미준비'로 적고 생략) ⑦ 데이터 품질 줄(결측·stale). SD 08:45 AI 추천 push 는 폐지하고 화면(추천)으로만 | SD 05:30·06:10·08:30·08:45, ET 06:40·07:00(메시지)·07:45, GX 계획 06:05·08:30 |
| 09:00~15:30 10분 | 거래일 | `rules.intraday`(규칙 알림·관심종목·트레일링) | SD `tg_custom_alerts`·`tg_watchlist`·`tg_trailing`·`price_sync_intraday`·`stage2_realtime_kr` |
| 15:35~16:00 | 거래일 | `market.close_collect`(당일 종가·수급 — 출처는 `conflict_map.md` Q1 결정 대기) | SD 15:35·15:40 수급·16:10 일봉, ET board 16:10 수집부, ET kr 15:30 KIS 수급 |
| 16:00~17:50 | 거래일 | GX 주간 분봉·무결측(그대로) | — |
| **16:40** (캐치업 ~20:30, 하루 1회) | 거래일 | **마감 요약(1회)** — ① 지수·K200 선물·환율·시장 수급(SD 15:40·16:00 지수/선물, ET board 헤더) ② 섹터 상위·하위, 특징주(SD 16:00 '섹터 등락'·'특징주' + ET board 섹터 상위5·하위3·교집합) ③ 신고가(ET board 랭킹: 52주 이상 신고가 + 종목별 수급·재료 — SD 16:00 '신고가' 섹션은 board로 대체) ④ 수급 동향(SD 16:00 '수급 동향') ⑤ 주요 공시(SD 16:00) ⑥ GEX 마감 레벨(GX 계획 15:50) ⑦ 스윙 시그널 요약(ET board signals) ⑧ 코멘트 초안(ET board draft, 검증 통과분만) ⑨ 발굴·추천 TOP5(SD 15:40 발굴, 15:45·16:00 AI 추천) ⑩ 엑셀 첨부(ET board files), HTML 보관본은 웹 링크로 | SD 15:40·15:45·16:00(+캐치업), ET board 16:10 발송 4종, GX 계획 15:50 |
| 17:00 | 거래일 | `macro.evening`(ECOS 당일 금리 등) | ET bok 16:30·22:00 |
| 18:20 | 거래일 | 수급 리포트 1회 [제안 — U2 범위 밖이라 통합 여부는 별도] | ET flow 18:17, SD 19:30 수급 시그널 |
| 18:30 | 거래일 | `consensus.snapshot`·리비전 알림 | SD 18:00 스냅샷·18:30 리비전 |
| 분기 16일 09:00 + 요청 시 | — | `reports.dart_excel` | ET `report.yml` |

---

## (d) 텔레그램 발송 전체 표와 통합안

### d-1. 발송·수신 지점 전부

| # | 레포:파일:함수 | API | 메시지 | 시각·조건 | 채팅 env | 형식·분할 | 중복 방지 | KBJ 토픽 [제안] | 처리 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | SD:`server.py:send_telegram`:5205 | sendMessage | 공용 발송 | — | `TELEGRAM_BOT_TOKEN`·`TELEGRAM_CHAT_ID`, 끄기 `TELEGRAM_ENABLED` | HTML, 재시도 없음 | — | — | notifier로 교체 |
| 2 | SD:`server.py:send_telegram_long`:5273 | sendMessage | 긴 본문 | — | 같음 | 3,900자 줄 단위 분할 | — | — | notifier로 교체 |
| 3 | SD:`alert_morning_briefing`:6064 | ↑1 | 아침 시황 | 평일 08:30 | 같음 | HTML | — | 시장 | 아침 브리핑에 흡수 |
| 4 | SD:`alert_overnight_prediction`:6310 | ↑1 | 익일 예측 | 화~토 05:30 | 같음 | HTML | — | 시장 | 아침 브리핑에 흡수 |
| 5 | SD:`send_us_market_summary_telegram`:14554 | ↑2 | 미국장 시황 | 화~토 06:10 | 같음 | HTML | — | 시장 | 아침 브리핑에 흡수 |
| 6 | SD:`alert_watchlist_price`:6248 | ↑1 | 관심종목 가격 | 평일 09~14시 30분 | 같음 | HTML | — | 알림 | 규칙 엔진(P8) |
| 7 | SD:`alert_new_reports`:6282 | ↑1 | 증권사 리포트 | 평일 10:00 | 같음 | HTML | — | 알림 | ⚠ 네이버 리서치 — 대체 없음, 폐지 |
| 8 | SD:`alert_closing_summary`:6490 | ↑1 | 장 마감 요약 | 평일 15:40 | 같음 | HTML | — | 시장 | 마감 요약에 흡수 |
| 9 | SD:`send_closing_market_summary`:14696 → `send_market_summary_telegram`:14583 | ↑2 | 장마감 시황 | 평일 16:00 + 캐치업(16~20시 :05·:35) + **부팅 직후 1회**(`server.py:7178`) + 수동 HTTP(#43) + 봇 `/시황`(#21, `server.py:5839`) | 같음 | HTML 분할 | `ops_state` 하루 1회(`/시황`·`?force=1` 은 우회) | 시장 | 마감 요약에 흡수(패턴 재사용) |
| 10 | SD:`alert_revision_signals`:5948 | ↑1 | 리비전 | 평일 18:30 | 같음 | HTML | `revision_alerts.alert_sent` | 알림 | 유지(P4) |
| 11 | SD:`alert_flow_signals`:15196 | ↑1 | 수급 시그널 | 평일 19:30 | 같음 | HTML | — | 시장 | 수급 리포트로 통합 [제안] |
| 12 | SD:`check_alert_rules`:5579 | ↑1 | 맞춤 알림 | 평일 09~15시 10분 | 같음 | HTML | 규칙당 하루 1회 | 알림 | 규칙 엔진(P8) |
| 13 | SD:`_check_trailing_stops`:5438 | ↑1 | 트레일링 스톱 | 평일 09~15시 30분 | 같음 | HTML | — | 알림 | 규칙 엔진(P8) |
| 14 | SD:`alert_discovery_new_entries`:6143 | ↑1 | 발굴 신규 진입 | Stage2 후 | 같음 | HTML | 쿨다운 60분 `_alert_cooldown_ok`:6102 | 알림 | 마감 요약 ⑨ + 화면 |
| 15 | SD:`agents/pipeline.send_agent_telegram`:989 | ↑1 | AI(규칙) 추천 | 평일 08:45·15:45 | 같음 | HTML | — | 시장 | 폐지 → 마감 요약 ⑨ |
| 16 | SD:`earnings_telegram_sender.send_telegram_message`:51 / `send_pending_alerts`:201 | sendMessage(별도 구현) | 어닝 서프라이즈 | 5분 파이프라인 | 같음 | 독자 구현 | `alert_sent` | 알림 | notifier로 교체(P4) |
| 17 | SD:`_scheduled_universe_sync` 내부 | ↑1 | 유니버스 변동 | 매월 1일 03:00 | 같음 | HTML | — | 운영 | 유지 |
| 18 | SD:`_watchdog_notify`:14989 | ↑1 | 데이터 공백·복구 | 30분, `WATCHDOG_TELEGRAM=1` | 같음 | HTML | `ops_state` | 운영 | 유지 |
| 19 | SD:`api_test_telegram_get`:4644, `api_telegram_test`:5708, `/api/telegram/briefing_test`:5718 | ↑1 | 테스트 | HTTP 호출(**인증 없음**) | 같음 | — | — | 운영 | 폐지(인증 뒤 운영 화면 버튼) |
| 20 | SD:`_telegram_setup_webhook`:5882 | **setWebhook** | 웹훅 등록 | 부팅마다(`RENDER_EXTERNAL_URL` 있을 때), `POST /api/telegram/setup_webhook`:5904 | 시크릿 = 토큰 해시 파생(`_telegram_secret`:5731) | — | — | — | notifier 웹훅으로 이전 |
| 21 | SD:`_handle_telegram_command`:5823 ← `/api/telegram/webhook`:5860, 응답 `_tg_reply`:5766 | 수신(웹훅) | `/도움` `/시황` `/시그널` `/수급 <종목>` `/가격 <종목>` | 수신 시, 소유자 chat_id만 | 같음 | HTML | — | (명령 응답은 받은 대화로) | notifier 명령 분배기로 이식 |
| 22 | ET:`board/report/telegram.py:send`:779, `send_document`:822, `check`(getMe·getChat) | sendMessage·sendDocument | board 계열 공용 | — | `TELEGRAM_BOT_TOKEN`·`TELEGRAM_CHAT_ID`(`_cred`:761) | Markdown 기본, 4,096자 분할, 캡션 상한, `_safe_err` 토큰 가림 | — | — | **notifier 정본 후보**(테스트 73+12) |
| 23 | ET:`run.py:cmd_send('rankings')` → `rankings_message` | ↑22 | 신고가 랭킹 요약 | 평일 16:10 로컬(`BOARD_SEND=1`) | 같음 | Markdown | — | 신고가 | 마감 요약 ③에 흡수 |
| 24 | ET:`cmd_send('draft')` → `draft_message` | ↑22 | LLM 코멘트 초안 | 16:10 | 같음 | Markdown | — | 신고가 | 마감 요약 ⑧에 흡수 |
| 25 | ET:`run.py:_send_files`:1083 | sendDocument | HTML 보관본·엑셀 | 16:10, `--only-fresh --once` | 같음 | — | `docs/api/sent.json` | 신고가 | 엑셀만 마감 요약 첨부, HTML은 웹 링크 |
| 26 | ET:`cmd_send('signals')` → `report/signals_tg.message` | ↑22 | 스윙 시그널 | 16:10 끝 | 같음 | Markdown | — | 신고가 | 마감 요약 ⑦ 요약 + 규칙 알림 |
| 27 | ET:`run.py:_send_note`:1000 → `report/note.py` | ↑22 | 사람이 쓴 일일 노트 | 수동 | 같음 | Markdown | 이름 미해결 시 전부 보류 | 신고가 | 유지(수동) |
| 28 | ET:`cmd_send('backtest'/'screen'/'search')`:930~961 | ↑22 | 백테스트·탐색 요약 | 수동 | 같음 | Markdown | — | 운영 | 유지(수동, P8) |
| 29 | ET:`run.py:cmd_us_send`:1864 → `us/brief.telegram_chunks` + `send_document(docs/us/index.html)` | ↑22 | 미국장 브리프 ①~⑧ + HTML | 화~토 07:00 | 같음 | HTML | 기준일 3일 초과 생략 | 시장 | 아침 브리핑 ①에 흡수, HTML은 웹 |
| 30 | ET:`guru/pipeline.send`:143(발송 :153) | ↑22 | 구루 브리핑 | 화~토 07:45·08:15·10:23 | `XDIGEST_CHAT_ID`(없으면 기본) | 평문 | `docs/api/guru-sent.json` | 시장 | 아침 브리핑 ⑥ |
| 31 | ET:`xdigest/send.send_parts`:159(발송 :168) ← `run`:208, `run.py:cmd_xdigest_preview`:1305 | ↑22 | 구 X 다이제스트 | 수동 | `XDIGEST_CHAT_ID` | 평문 | `docs/api/xdigest-sent.json` | — | [제안] 폐기 |
| 32 | ET:`tools/dashboard_brief_preview.main` | ↑22 | SD 시황 미리보기(SD 코드 exec) | 수동 | 같음 | HTML | — | — | 폐기(원격 코드 실행·KIS 발급) |
| 33 | ET:`ingest/tg_inbox.drain`:436 (`_call`:131, `webhook_url`:153) | **getUpdates**·getWebhookInfo | 수신: 공유된 X 링크 → `state/inbox.json` | daily 앞, **기본 꺼짐**(`settings.yaml triggers.inbox_enabled: false`) | `TRIGGER_INBOX_CHAT_IDS` | — | `.inbox_offset.json` | (인박스 저장) | notifier 웹훅 → 인박스로 분배(U1) |
| 34 | ET:`etf_tracker_v9/tracker.py:send_report`:413 → `send_telegram`:395 | sendMessage(독자) | ETF 리포트 여러 통 | 평일 08:00(당일 1회) | 같음 | HTML 3,900자 묶음, 0.4초 간격, **try 없음**(예외에 토큰 URL) | Actions 캐시 `etf-sent-{날짜}` | 시장(또는 ETF) | notifier로 교체 |
| 35 | ET:`tracker.py:send_telegram_file`:425 | sendDocument | 대시보드 HTML | **호출 없음** | — | — | — | — | 폐기 |
| 36 | ET:`tracker.py:doctor`:513 | getMe·sendMessage | 연결 테스트 | `--check` | 같음 | — | — | 운영 | notifier 헬스체크 |
| 37 | ET:`.github/workflows/daily.yml` 실패 단계 | curl sendMessage | "ETF 리포트 실행 실패" | 잡 실패 | Secrets | 평문 | — | 운영 | scheduler 실패 알림으로 |
| 38 | ET:`monitor/bok/send-telegram.js:main`:44 | sendMessage(Node) | 매크로 아침 브리핑 | 평일 06:40 회차만 `--morning-only` | `TELEGRAM_CHAT_ID`(**쉼표로 여러 개**) | HTML ≤4,096, **429 `retry_after` 준수 4회** | `cache/telegram-sent.json` | 시장 | 아침 브리핑 ②③⑤ |
| 39 | ET:`monitor/flow/telegram.py:send_photos`:36 + `TG.send` | **sendMediaGroup** + sendMessage | 수급 리포트(차트 4장+표) | 평일 18:17 | board `_cred` | 캡션 1,024자 | — | 시장 | 수급 리포트 |
| 40 | ET:`monitor/flow/telegram.py:send_text`:103 | ↑22 | "대상 없음" | 문턱 미달 | 같음 | — | — | 시장 | 유지 |
| 41 | ET:`.github/workflows/preview.yml` | curl sendMessage | 임의 HTML 예시본 | 수동 | Secrets | HTML | — | 운영 | 폐기 |
| 42 | GX:`config/settings.py:29-30` | — | 없음(설계: 정기 브리핑 4회·이벤트·`/status` `/levels` `/kill` `/resume`) | 계획 | `TELEGRAM_*` | — | 설계: 쿨다운 10분 | 알림·운영 | 브리핑은 U2 통합, 이벤트는 규칙 알림 |
| 43 | SD:`api_ops_brief_closing`:8493 `POST /api/ops/brief/closing` → #9 | ↑2 | 장마감 시황 수동 재발송(`?force=1` 이면 하루 1회 표식을 지우고 다시) | HTTP(**인증 없음**), `brief-kick.yml:86-87` 수동 | 같음 | HTML 분할 | `ops_state`(force 시 무시) | 운영 | 폐지 → 운영 화면 '재발송' 버튼(인증 뒤) |
| 44 | SD:`api_agent_run`:12489 `POST /api/agent/run` → `send_agent_telegram`(#15) | ↑1 | AI(규칙) 추천 즉시 실행·발송(kr·us·all, all이면 2통) | HTTP(**인증 없음**) | 같음 | HTML | — | 시장 | 폐지(#15와 같이) |
| 45 | SD:`api_ops_cron_trigger`:18370 `POST /api/ops/cron/trigger/<job_id>` | (잡에 따라) | **등록된 APScheduler 잡 43개 아무거나 즉시 실행** — `tg_*`·`agent_pipeline`·`stage2_auto` 등 발송 잡 포함 | HTTP(**인증 없음**), 운영 화면 버튼(`static/js/pages.js:8835`) | 같음 | — | 잡 자체 규칙만 | 운영 | 폐지 → KBJ 작업 등록부 수동 실행(인증·감사 로그) |
| 46 | ET:`board/ingest/triggers.py:812` | **getWebhookInfo**(진단) | 수신 경로 점검(웹훅 걸림 여부 기록) | 트리거 점검 실행 시 | `TELEGRAM_BOT_TOKEN` | — | — | — | notifier 헬스체크로 흡수(`api.telegram.org` 직접 호출 금지 대상) |

(#43~#46은 2026-10-06 완성도 점검에서 원본 grep으로 추가 — `api.telegram.org`·`send_telegram(` 호출부와 그 호출부를 부르는 HTTP 라우트 전수.)

**웹훅 대 getUpdates 충돌 (U1)**: SD는 부팅마다 `setWebhook`(#20)을 건다. ET `tg_inbox`(#33)는 웹훅이 걸려 있으면 409로 거부된다고 스스로 검사한다(`tg_inbox.py:153-155`). 같은 봇을 쓰므로(U1) 둘은 공존할 수 없다.

### d-2. 통합안 — 봇 1개, 슈퍼그룹 토픽 4개 [제안, D5]

| 항목 | 지금 | KBJ |
|---|---|---|
| 봇·대화방 | 봇 1개를 SD·ET 전부가 공유(U1), 채팅은 `TELEGRAM_CHAT_ID`·`XDIGEST_CHAT_ID`·`TRIGGER_INBOX_CHAT_IDS`·bok 쉼표 다중 | 봇 1개 + **포럼 슈퍼그룹 1개**, 토픽(`message_thread_id`) 4개: **시장**(아침 브리핑·마감 요약·수급 리포트·ETF 리포트), **신고가**(보드 엑셀·노트), **알림**(규칙·관심종목·트레일링·어닝·리비전·공시·GEX 레벨 변화), **운영**(워치독·작업 실패·헬스·유니버스 변동). 토픽 id는 `config/notify.yaml` |
| 발송 구현 | 7벌(#1·#16·#22·#34·#38·#39·curl) | `kbj/services/notifier` 하나. 정본 = ET `report/telegram.py`(분할·캡션 상한·토큰 가림·테스트 73) + sendMediaGroup(#39) + 429 `retry_after`(#38) + HTML 기본(SD·bok·ETF) + 끄기 스위치(SD `TELEGRAM_ENABLED`) |
| 중복 방지·발송 기록 | `ops_state`, `docs/api/sent.json`·`guru-sent.json`·`xdigest-sent.json`, bok `telegram-sent.json`, Actions 캐시 `etf-sent-*`, `_alert_cooldown_ok` | `ops.notify_log`(종류·기준일·토픽·본문 해시·결과) 한 곳. 하루 1회·쿨다운은 등록부 규칙 |
| 수신 | SD 웹훅(명령) / ET getUpdates(인박스) | **notifier 웹훅 1개**(`POST /telegram/webhook`, setWebhook `secret_token` = 독립 비밀 `KBJ_TELEGRAM_WEBHOOK_SECRET`). 업데이트를 ① `/`로 시작 → 명령 분배기(`/도움` `/시황` `/시그널` `/수급` `/가격` + PLAN `/신고가` `/gex`) ② 허용 대화의 링크·전달 메시지 → 인박스 표(`prv_alerts.tg_inbox`)로. getUpdates 호출 코드는 없앤다 |
| 이행 순서 | — | ① notifier 웹훅 배포(P2) ② SD Render의 부팅 setWebhook 끄기(legacy 쪽 `RENDER_EXTERNAL_URL` 제거 또는 서비스 중지) ③ notifier가 setWebhook(`drop_pending_updates=false`) ④ ET `tg_inbox.drain` 대신 인박스 표를 읽게 교체(`parse_update`·`oembed`·`merge` 재사용) ⑤ import-linter·grep으로 `api.telegram.org` 를 notifier 밖에서 금지 |

---

## (e) DB·state 전체 표

### e-1. 테이블

| 저장소 | 테이블 | 주요 내용 | 쓰는 쪽 → 읽는 쪽 | 충돌·겹침 |
|---|---|---|---|---|
| SD SQLite `db/dashboard.db` (`db/schema.sql`) | `stocks` | 종목 당일 스냅(종가·등락·시총·시간외·is_etf) | 네이버 동기화 → 화면·시황·신고가 | ⚠같은데이터: ET `snap` |
| 〃 | `ohlcv` | 일봉 | `ohlcv_autofill`·`ohlcv_5y_collector` → 신고가·밴드·상관 | ⚠같은데이터: ET `px`, ETF `etf_px` |
| 〃 | `chart_cache` | 지표 JSON 캐시 | `api_chart` | 캐시(이관 안 함) |
| 〃 | `financial` | 네이버 PER·EPS·PBR 등 | `api_financial` | 네이버(U4), ⚠이름 유사: ET `state/financials.json` |
| 〃 | `flow_cache` | 수급 20일 JSON | 네이버 수급 → 시그널·봇 | ⚠같은데이터: GX `investor_flow`, ET `stockflows.json`·kr `flows.json`·flowlab `flows.json` |
| 〃 | `yinfo_cache`, `discover_results`, `misc_cache` | yfinance 정보 / 발굴 / 잡캐시 | — | 캐시 |
| 〃 | `alert_rules`, `alert_history` | 규칙 / 발송 이력 | 동기화 API(실제 평가는 `cache/alert_rules.json`) | ⚠규칙 저장 3곳(localStorage·JSON·DB) |
| 〃 | `dart_corp_map`, `disclosure_history` | corp_code / 공시 | DART 폴링 | ⚠같은데이터: ET `.dart_corp.json`, dart-report `.cache` |
| 〃 | `ops_state` | 1일 1회·워치독 표식 | 각 작업 | 발송 기록과 통합 |
| 〃 | `consensus_snapshot`, `revision_alerts` | 컨센서스 스냅·리비전 | 스냅 수집 → 리비전 | ⚠**같은 DB에 두 곳에서 중복 정의**(`db/schema.sql` 과 `migrations/008_consensus_revision.py` — 둘 다 `CREATE TABLE IF NOT EXISTS`, 먼저 돈 쪽 정의가 남는다) |
| 〃 (`migrations/004`) | `financial_quarterly`, `valuation_band`, `consensus_estimate`, `dart_disclosure_overhang`, `fetch_progress`, `analysis_journal`, `alert_history_v2` | 분기 재무·밴드·Fwd EPS·오버행·진행·일지·이력 v2 | 맥 CLI → Render는 Gist 복원본 | `alert_history` 와 v2 두 벌 |
| 〃 (`migrations/005`) | `index_universe`, `consensus_quarterly`, `earnings_actual`, `earnings_surprise`, `earnings_alert_queue` | 유니버스·분기 컨센·실적·서프라이즈·큐 | 어닝 파이프라인 | ⚠유니버스 4벌(SD `index_universe`·`naver_universe_*`, ET board universe, kr 필터) |
| 〃 (`server.py:13052`, `:15495`) | `recommendation_history`, `trade_journal` | 추천 스냅·매매 저널 | 화면 | 개인 데이터 |
| SD SQLite `db/llm_cache.db` | `llm_cache` | Ollama 응답 캐시 | `earnings_alert_writer` | 폐기 |
| ET SQLite `board/board.db` (`engine/db.py`) | `px`, `snap`, `alltime`, `label`, `sector_map`, `split_check`, `meta`, `run_log` | 일봉 / 당일 스냅 / 역사적 최고 / 라벨 / 섹터 / 분할 판정 / k-v / 실행 로그 | ingest → engine | ⚠**같은 이름**: `us_board.db` 의 `px`·`snap`·`label`·`meta`·`run_log` |
| ET SQLite `board/us_board.db` (`us/db.py`) | `px`, `snap`, `label`, `lowlabel`, `meta`, `run_log` | 미국장 | us 파이프라인 | ⚠위와 이름 충돌 |
| ET SQLite `board/backtest.db`, `us_backtest.db` | board와 같은 스키마 [추정] | 긴 일봉 | 백테스트 | ⚠이름 충돌 |
| ET SQLite `board/board.db.demo`, `flowlab/cache/backfill.db.demo` | board 스키마 사본 | flowlab 데모·백필 작업본 | `flowlab/demo.py:24`, `flowlab/backfill.py:20` | 이관 안 함(재생성 가능) |
| ET SQLite `etf_tracker_v9/etf.db` | `fund`, `holding`, `etf_ticker`, `change_log`, `etf_meta`, `etf_px`, `etf_aum`, `etf_live`(쓰는 곳 없음) | ETF PDF·변동·시세 | tracker·market → dash·report | ⚠`etf_px`: KRX `etf_bydd_trd` 와 같은 데이터 |
| GX Postgres `001_init.sql` | `raw_messages`, `fut_ticks`, `opt_ticks`, `chain_snapshots`, `fut_board`, `investor_flow`, `series_expiries`, `minute_bars`, `krx_fut_daily`, `krx_opt_daily`, `master_snapshots`, `session_log`, `health_events`, `collection_gaps`, `quarantine` (전부 hypertable) | 녹화·틱·체인·선물·투자자·만기·분봉·KRX 일별·마스터·운영 | poller·ws·recorder·scheduler → engine | `investor_flow` ⚠같은데이터(시장 수급) |
| GX `002`·`003` | `collection_reports`, `levels`, `metrics`, `strike_gex`, `option_iv`, `oi_changes` | 무결측 판정·GEX 산출 | engine | — |
| GX `db/migrate.py` | `schema_migrations` | 마이그레이션 기록 | — | KBJ 마이그레이션 표로 승계 |

**이름 충돌 정리**: ① 같은 이름 다른 DB: `px`·`snap`·`label`·`meta`·`run_log`(board.db·us_board.db·backtest.db) ② 같은 DB 안 이중 정의: `consensus_snapshot`·`revision_alerts`(SD) ③ 같은 데이터 다른 이름: 일봉(`ohlcv`·`px`·`etf_px`·`minute_bars`), 당일 스냅(`stocks`·`snap`), 수급(`flow_cache`·`investor_flow`·JSON 4종), corp_code 3벌 ④ DATA_TIERS의 `public` 스키마 이름은 Postgres 기본 스키마 `public`(TimescaleDB 확장이 기본으로 놓이는 곳)과 겹친다 → `conflict_map.md` §1.5에서 `pub_*`/`prv_*` 로 제안.

### e-2. 파일·Redis·외부 state

| 저장소 | 경로·키 | 내용 | 비고 |
|---|---|---|---|
| SD `cache/`(휘발, 웹 403) | `server_watchlist.json`, `server_portfolio.json`, `alert_rules.json`, `kis_token.json`, 시장 캐시 60여 종 | 사용자 상태·**KIS 토큰**·캐시 | 토큰 파일은 auth 이전 후 삭제 |
| SD git 추적 | `data.json`, `data/naver_universe_seed.json`, `data/valuechain_*.json`, `themes_mapping.json` | 실데이터 스냅·사전 | U3 표(`conflict_map.md` §2) |
| SD Gist | `CORE_TABLES` 17개 + 사용자 JSON | 백업 | pg_dump로 대체 |
| SD 브라우저 localStorage | 관심종목·포트폴리오·저널·알림 규칙·분할 계획·사이징 | 사용자 상태 | DB로 일원화 [제안] |
| ET `board/state/<날짜>/` | `market`·`universe`·`sectors`·`newhigh`·`events`·`rankings`·`news`·`stockflows`·`flows`(flowlab이 씀)·`triggers`·`index_hist`·`signal_flows`·`signals`.json, `draft.md`, `claims.json` | 단계 산출(모든 수치 source·as_of) | `newhigh.json` 을 monitor/flow·flowlab이 읽는다 |
| ET `board/state/` 루트 | `signal_ledger`, `inbox`, `.inbox_offset`, `financials`, `.dart_corp`, **`.kis_token`**, `backtest`·`search`·`screen`.json | 장부·인박스·캐시·**토큰** | 디렉터리 전체가 Actions 캐시로 저장된다(토큰 포함) |
| ET `board/state/guru/`, `state/xdigest/`(커밋) | 조사 결과·비용·게시물 원문 | — | U3 |
| ET `docs/`(커밋) | 보드 `api/*.json`·`d/*.html`·`x/*.xlsx`·`us/`, `bok/`, `kr/` | 생성물 | 실데이터(로그인 등급 포함) |
| ET `monitor/bok/cache/`(커밋), `data/`(커밋 39.8MB) | ECOS·FRED·Yahoo·rates·FF·`telegram-sent` / 원본 xlsx·그림 | — | U3 |
| ET `monitor/kr/cache/`(Actions 캐시) | `daily/*.json`, `indices`, `financials`, `flows`, `ksic`, `etf/` | — | — |
| ET `flowlab/cache/`, `ci-out/`(커밋 3.3MB) | 일봉 캐시, 이벤트 스터디 | — | U3 |
| ET Actions 캐시·아티팩트 | `board-db-v*`, `kr-cache-*`, `dart-cache-*`, `etf-db-*`, `etf-sent-*` | — | 폐기(DB 일원화) |
| GX Redis | `kis:token`, `kis:ws_key`, `rl:kis:<앱키 해시>`, `kis:master`(+sha), `poller:chain_context`, `session:state`, `engine:basis`, `engine:latest`, `krx:calls:<YYYYMMDD>`, `health:heartbeat:*` + 채널 8개 | 토큰·리미터·문맥 | **KBJ Redis 키 정본**(`kbj/store/redis_keys.py`) |
| GX 파일 | `state/kis.token.json`(Redis 없을 때), `state/spool/`, `probe_out/` | — | 토큰 파일 경로는 폐지 |

---

## (f) 환경변수 통합 표 — 지금 이름 → KBJ 이름 [제안]

원칙: 비밀은 `KBJ_` + 출처 + 종류. 비밀이 아닌 튜닝값은 환경변수가 아니라 `config/*.yaml`. 이름표는 `docs/secrets.md`(값 없이)로 옮긴다.

| 지금 이름 | 쓰는 곳 | KBJ 이름 | 비고 |
|---|---|---|---|
| `KIS_APP_KEY`, `KIS_APP_SECRET` | SD `kis_api.py`, ET `ingest/kis.py`·워크플로 5개(`board`·`kr`·`flow`·`kis-futures-probe`·`dashboard-brief-preview`), GX `config/settings.py`·워크플로 `probe.yml` | `KBJ_KIS_APP_KEY`, `KBJ_KIS_APP_SECRET` | **auth 서비스만 읽는다**(U1). 레포 시크릿은 ET 5곳 + GX 1곳에서 지운다 |
| `KIS_ENV` | ET `ingest/kis.base`, GX settings | `KBJ_KIS_ENV` | real/vts |
| `KIS_ACCOUNT` | ET `.env.example` 만 | 삭제 | 코드 미사용 |
| `KIS_DEMO_APP_KEY`, `KIS_DEMO_APP_SECRET`, `KIS_DEMO_ACCOUNT` | GX `.env.example` 만 | 보류 | 주문 코드는 승인 전 만들지 않음 |
| `KIS_TOKEN_CACHE_PATH` | GX settings | 삭제 | 토큰은 Redis만 |
| `KRX_API_KEY` | SD, ET, GX | `KBJ_KRX_API_KEY` | 일 10,000회 공유 |
| `KRX_API_BASE`, `KRX_DAILY_CALL_CAP` | SD / GX | `config/krx.yaml` | 비밀 아님 |
| `KRX_ID`, `KRX_PW` | ET `monitor/flow/krx.py:104`, `flowlab/krx.py:27` | 삭제 | ⚠ **pykrx 1.2.x 가 같은 이름을 import 때 읽어 자동 로그인한다**(`pykrx/website/comm/auth.py:176`). 정보데이터시스템 스크래핑을 끝내면 둘 다 없앤다 |
| `DART_API_KEY` | SD, ET | `KBJ_DART_API_KEY` | 공개 등급 |
| `DATAGO_KEY` | ET | `KBJ_DATAGO_KEY` | 관세청·금투협 통계도 같은 키 |
| `DATA_GO_KR_KEY`, `KOSIS_API_KEY` | ET `monitor/bok/audit.js:19`(산출 HTML에 키 이름이 새지 않았는지 보는 감사 목록에만, 읽는 코드 없음) | `KBJ_DATAGO_KEY`(통합) / `KBJ_KOSIS_KEY`(신규, `probe_results.md` §5.3) | 공공데이터포털 키 이름이 두 벌 |
| `ECOS_API_KEY` | ET bok | `KBJ_ECOS_KEY` | `probe_results.md` §4.1 제안과 같음 |
| `FRED_API_KEY` | ET bok | `KBJ_FRED_KEY` | — |
| `FINNHUB_API_KEY` | SD | `KBJ_FINNHUB_KEY` | 로그인 |
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | SD, ET | `KBJ_NAVER_SEARCH_ID`, `KBJ_NAVER_SEARCH_SECRET` | U4: 로그인 전용, 대체 후 삭제 후보 |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` | ET | `KBJ_ANTHROPIC_API_KEY` | SDK는 기본으로 `ANTHROPIC_API_KEY` 를 읽으므로 코드에서 명시 전달 |
| `BSKY_HANDLE`, `BSKY_APP_PASSWORD`, `BSKY_PROBE_SPEC` | ET | 삭제 [제안] | xdigest 구모드·진단 |
| `TELEGRAM_BOT_TOKEN` | SD, ET, GX | `KBJ_TELEGRAM_BOT_TOKEN` | **notifier만 읽는다**(U1) |
| `TELEGRAM_CHAT_ID` | SD, ET, GX | `KBJ_TELEGRAM_CHAT_ID` | 슈퍼그룹 1개. ⚠ 지금 bok은 쉼표 다중, 나머지는 단일 |
| `XDIGEST_CHAT_ID` | ET | 삭제 | 토픽으로 흡수 |
| `TRIGGER_INBOX_CHAT_IDS` | ET | `KBJ_TELEGRAM_INBOX_CHAT_IDS` | 인박스 허용 대화 |
| (신규) | — | `KBJ_TELEGRAM_WEBHOOK_SECRET` | SD의 '봇 토큰 해시 파생 시크릿' 대체 |
| `TELEGRAM_ENABLED`, `WATCHDOG_TELEGRAM`, `BOARD_SEND` | SD / SD / ET | `KBJ_NOTIFY_ENABLED` + `config/notify.yaml` | — |
| `REDIS_URL`, `DATABASE_URL`, `POSTGRES_PASSWORD` | GX | `KBJ_REDIS_URL`, `KBJ_DATABASE_URL`, `KBJ_POSTGRES_PASSWORD` | — |
| `SPOOL_DIR`, `SPOOL_MAX_MB` | GX | `KBJ_SPOOL_DIR`, `KBJ_SPOOL_MAX_MB` | — |
| `LIVE_TRADING` | GX | `KBJ_LIVE_TRADING` | 기본 false(절대 규칙 6) |
| `PROBE_OUT_DIR`, `GEXLAB_TEST_TIMESCALE_IMAGE`, `HEALTHCHECK_URL` | GX(`HEALTHCHECK_URL` 은 `docs/phase5_8_design.md` 설계에만 — 코드·`.env.example` 에 없음) | `KBJ_PROBE_OUT_DIR`, `KBJ_TEST_TIMESCALE_IMAGE`, `KBJ_HEALTHCHECK_URL` | — |
| `GITHUB_TOKEN`, `BACKUP_GIST_ID` | SD `db_backup.py` | 삭제 | ⚠ `GITHUB_TOKEN` 은 Actions가 자동 주입하는 이름과 겹친다 |
| `GH_TOKEN`, `REPO`, `DRY_RUN`, `ALREADY_SENT`, `MODE` | ET 워크플로 | 삭제(CI 내부) | ⚠ `REPO` 가 `local_daily.sh:19`(레포 경로)와 `artifacts-gc.yml:38`(owner/repo)에서 뜻이 다르다 |
| `USE_SQLITE`, `DISABLE_AUTO_FETCH`, `SERVER_NO_STARTUP` | SD | 삭제(legacy 테스트에서만 `SERVER_NO_STARTUP`) | — |
| `PORT`, `HOST` | SD | `KBJ_API_PORT`, `KBJ_API_HOST` | — |
| `RENDER`, `RENDER_EXTERNAL_URL`, `RENDER_GIT_COMMIT`, `RENDER_GIT_BRANCH`, `GIT_COMMIT`, `PYTHON_VERSION`, `DASHBOARD_URL` | SD | `KBJ_PUBLIC_BASE_URL`(웹훅 주소), `KBJ_GIT_COMMIT`, 나머지 삭제 | — |
| `TZ` | SD Render `Asia/Seoul`, GX compose `UTC` | `TZ=UTC` 고정 + 코드에서 KST 변환 | ⚠ SD APScheduler는 서버 TZ에 기대어 KST로 돈다 |
| `BOARD_DB`, `BOARD_BACKTEST_DB`, `BOARD_SITE`, `BOARD_DEMO_SITE`, `US_DB`, `US_OUT`, `US_BACKTEST_DB`, `SD_DIR` | ET | 삭제(`KBJ_DATA_DIR` 하나) | — |
| `DASH_URL`, `PYTHON_BIN`, `PYTHONIOENCODING` | ET | 삭제 | `DASH_URL` 은 워크플로가 안 넘겨 죽은 값 |
| `MIN_FUNDS`, `ACTION_PP`, `KEEP_DAYS`, `WORKERS`, `QTY_FLOOR`, `RETRIES`, `EMPTY_LIMIT` | ET ETF | `config/etf.yaml` | 비밀 아님 |
| `STOCKS`, `YEAR_FROM`, `FS_DIV`, `DIAGNOSE`, `DART_STOCKS` | ET `report.yml` | CLI 인자·`config/reports.yaml` | — |
| `NOTION_TOKEN`, `NOTION_DATABASE_ID` | ET `.env.example` 만 | 삭제 | 코드 없음 |

`.env` 로더도 하나로 줄인다: 지금 SD 7곳(`server.py:35` 등), ET 4곳 이상(`board/ingest/creds.py`, `tracker.py:_load_env`, bok `readKey` 2곳), GX pydantic `Settings` 1곳 → GX `Settings` 방식(SecretStr) 하나.

---

## (g)·(h) 기능 전체 목록 → PLAN §4 페이지, 공개/로그인 등급

등급은 DATA_TIERS §2 기준(로그인하면 전부 보인다. "공개"는 로그인 없이 보이는 것). U4: 네이버 의존 기능은 전부 로그인이며 출처를 바꾼다(대체 출처는 `conflict_map.md` §1.13).

| 기능 | 출처 자산(레포:위치) | PLAN §4 페이지 | 등급 | 비고 |
|---|---|---|---|---|
| 세션·야간 카운트다운, 휴장·만기 | GX `core/calendar.py`, SD `api_market_context`:18081(사이드바 장 상태 배지 — 지수 실시간값은 로그인) | 상단 띠 | 공개(시계·캘린더만) | SD 배지는 GX 캘린더로 대체 |
| 지수·환율·시장 수급 헤더 | ET `web/render.py:_banner`, SD `/api/index_kr` | 상단 띠 / 1 | 로그인 | 네이버 → KIS·KRX |
| 대시보드(지수·테마 등락) | SD `data.json`·`_build_data_json`:7893 | 1 | 로그인 | — |
| 테마맵(트리맵), 업종 히트맵 | SD 테마맵, ET board 히트맵 탭, kr 섹터 히트맵 | 1 / 9 | 로그인 | ⚠ 히트맵 3벌 |
| 코스피200 선물(주야간), 야간선물 | SD `_kospi200_futures_section`:13599·`_fetch_night_futures`:4696, GX poller | 1 / 3 | 로그인 | 스크랩 → GX KIS |
| 섹터 로테이션 국면 | SD `/api/sector_rotation/phase`, kr `engine.rotation` | 9 | 로그인 | ⚠ 2벌 |
| 시황 분석(국장·미장) | SD `build_market_summary`:13630 | 1 + 텔레그램 | 로그인 | 마감 요약으로 |
| 60일·52주·역사적 신고가, 근접, 갭, 저항, 거래량 배수 | ET `engine/newhigh.py` | 2 | 로그인 | 정본 |
| 섹터·종목 랭킹, 교집합, 탐지기 5종 | ET `engine/rankings.py`, `aggregate.detect` | 2 | 로그인 | 미구현 탐지기 5종은 추가 제안 |
| 일간 코멘트(LLM), 섹터 뉴스 | ET `writer/`, `ingest/news.py` | 2 / 5 | 로그인 | — |
| 보관본 날짜 선택, 랭킹 엑셀 | ET `web/site.py`, `export/excel.py` | 2 | 로그인 | — |
| 간밤 미국 신고가·신저가·연속·첫 진입 | ET `us/engine.py` | 2 | 로그인 | Nasdaq·Yahoo |
| 미국 종목 화면·스크리너·Mag7·피어 | SD `/api/us/*` | **추가 제안**(2의 미국 탭, 또는 보류) | 로그인 | 국장 대상 밖 |
| GEX 레벨(Flip·월·기대변동폭·VEX·CEX·스큐·HIRO-lite·PCR·맥스페인) | GX `core/*`, `services/engine` | 3, 상단 띠 | 로그인 | — |
| 국장 공포탐욕·종합 판정 | (신규, SD CNN F&G 참고) | 3 | **로그인**(DATA_TIERS §2-3이 명시) | 공공지표만 쓰는 '매크로 위험 게이지'·신용·수출 모멘텀은 별도 공개 위젯 |
| SPY/QQQ 옵션 시그널 | SD `_compute_options_signal`:4947 | **추가 제안**(3 '글로벌' 보조) 또는 폐기 | 로그인 | Yahoo |
| 투자자 수급(시장·종목), 외국인·기관 연속, 반전, 쌍끌이 | SD `_analyze_flow_signals`:15104, ET `ingest/kis.py`·kr `flows.windows` | 4 | 로그인 | 네이버 → KIS |
| 신고가 상위 수급 차트(기관 7구분), 수익률 한 줄 | ET `monitor/flow` | 4 | 로그인 | KRX 스크랩 → KIS |
| 수급 레이어·이벤트 스터디 | ET `flowlab` | 4 | 로그인 | — |
| 경제 캘린더(한국·미국) | SD `api_calendar_economic`(Finnhub), ET bok `fetch-fred` 발표 캘린더·FF 컨센서스 | 5 | 공개(정부 일정)/로그인(Finnhub·FF) | — |
| DART 실시간 공시·중요도 점수 | SD `poll_dart_disclosures`·`score_disclosure`:10102 | 5 | 공개 | — |
| 잠정실적 → 실적 일정·서프라이즈 | SD `earnings_parser`, `earnings_signal_classifier` | 5 / 8 | 공개(실적)/로그인(서프라이즈: 컨센서스 필요) | — |
| 오버행(CB·BW·유증·EB) | SD `overhang_parser` | 5 / 8 | 공개 | — |
| 뉴스 헤드라인 | SD `api_news`(네이버 검색), ET `gnews`, RSS(신규) | 5 | 공개(RSS)/로그인(네이버 검색) | — |
| 증권사 리포트 목록 | SD `alert_new_reports`, `_crawl_naver_research`:2351 | 5 | 로그인 | ⚠ 네이버, 대체 없음 |
| 종목토론실 감성 | SD `_score_sentiment_titles`:15455 | — | — | ⚠ 네이버, 대체 없음 → 폐지 |
| BOK 통화신용정책보고서 지표, 성장·물가, 주택·가계, AI 투자 | ET `monitor/bok` 8탭 | 6 | 공개(한은 작성)/로그인(타기관 표) | — |
| ECOS·FRED·재무부 금리, 달러·원자재 | ET bok, SD `api_macro`:9982(yfinance) | 6 | 공개(정부)/로그인(Yahoo) | ⚠ 2벌 |
| 수출 104품목 | ET bok `extract.py` | 7 | 지금 원본(xlsx, 출처 불명확)은 **로그인[애매]** / 관세청 15101609 월간으로 바꾸면 공개 | 원본 xlsx 출처 확인 |
| 관세청 월간·10일·시군구, 급등, 단가 대 물량 | (신규, v0.2) | 7 | 공개 | — |
| 종목 시세·차트(기술적 지표 겹쳐 보기)·분봉·호가 | SD `api_chart`:4404, `kis_api` | 8 | 로그인 | — |
| 볼륨 프로파일, 피보나치, 추세선, 다이버전스 | SD `api_volume_profile`:5293, `_calc_fibonacci`:557 등 | 8 | 로그인 | — |
| 재무(DART 6~8블록 엑셀) | ET `dart-report` | 8 | 공개 | — |
| 재무 카드(연간·분기), 부문 매출 | ET kr `financials.py`, SD `_try_dart_segment_revenue`:10581 | 8 | 공개 | — |
| 컨센서스·리비전·서프라이즈 | SD `consensus_*`, `revision_calculator` | 8 | 로그인 | 네이버 → KIS [추정] |
| 밸류에이션 밴드(PER·EV/EBITDA·PBR) | SD `valuation_calculator` | 8 | 로그인 | 시세 사용 |
| TAM Bear/Base/Bull | SD `tam_modeler` | 8 | 로그인 | — |
| 검증 시트·갭 분석 | SD `/api/verification/*` | 8 | 로그인 | — |
| 피어 | SD `/api/peers` | 8 | 로그인 | — |
| 배당 | SD `api_dividend`:6726(배당수익률 상위 — `/api/financial` 캐시, 없으면 **네이버 main.naver 스크랩**) | **추가 제안**(8 배당 칸) | 지금 구현은 네이버라 U4 대상. DART 배당 공시로 다시 만들면 배당금은 공개, 시세 대비 수익률은 로그인 | DART 배당 엔드포인트 [추정: 실측 필요] |
| 종목↔HS 매핑 수출 겹쳐 보기 | (신규, v0.2) | 8 | 공개 | — |
| 테마 198·체인 23 모니터, 상대강도·낙폭·모멘텀 | ET `monitor/kr` | 9 | **로그인**(강도는 시세, 198·23 사전은 제3자 `rsm0kk/kr-sector` 추출·라이선스 미확인 → DATA_TIERS §2-9 '우리가 만든 사전'이 아님) | 사전 출처는 U3. 라이선스 확인 전 공개 금지 |
| 밸류체인 맵·heat·반영도 | SD `valuechain.py` | 9 | 공개(사전)/로그인(heat) | — |
| 48섹터 분류·종목→섹터 | ET `knowledge/sector_map.yaml` | 9 | 공개 | — |
| ETF 구성종목 변동·자금흐름·거래급증·신규상장 | ET `etf_tracker_v9`(대시보드 HTML `dash.py`, 장중 경량 갱신 `live_update.py` — 예약 없음) | 10 | 로그인 | — |
| ETF 맵 | SD `/api/etf_map` | 10 | 로그인 | — |
| ETF 탭(섹터 모니터 엔진) | ET kr `etf.py` | 10 | 로그인 | ⚠ ETF 분류 2벌 |
| 맞춤 알림(가격·등락·RSI·MACD·외국인) | SD `check_alert_rules`:5579 | 11 | 로그인 | `bb_upper`·`bb_lower`·`volume_spike` 라벨만 있고 평가 없음 |
| 스윙 시그널(돌파 확인·감시·시장 게이트) | ET `engine/signals.py` | 11 | 로그인 | **추가 제안**: 기본 규칙 세트 |
| 트레일링 스톱 | SD `_check_trailing_stops`:5438 | 11 | 로그인 | **추가 제안**(PLAN에 명시 없음) |
| 백테스트(태그·시그널·시스템·조합 탐색), 페이퍼 장부 | SD `api_backtest`:12742, ET `engine/backtest.py`·`systems.py`·`search.py`·`ledger.py`, 미국판 조합 탐색 `board/us/btsearch.py`(`us_backtest.db`) | 12 | 로그인 | ⚠ 2벌 |
| 분석 일지, 일일 노트 | SD `analysis_journal_api`, ET `report/note.py` | 12 | 로그인(개인) | ⚠ 2벌 |
| 포트폴리오·수익률 저널, 상관관계 | SD | 12 | 로그인(개인) | — |
| 종목 발굴(Stage1/2 점수), AI(규칙) 추천, 추천 성과 | SD `_calc_*_score_kr`, `agents/pipeline.py` | **추가 제안**(12 '발굴·추천' 탭) | 로그인 | PLAN §4에 페이지 없음 |
| 포지션 사이징, 분할 매수 계획 | SD `static/js/trading.js`, localStorage `split_plans` | **추가 제안**(12) | 로그인(개인) | — |
| 데이터 신선도, Cron 모니터, 헬스, 워치독 | SD `data_freshness.py`, `/api/ops/*`, GX health | 13 | 로그인 | — |
| 무결측·섀도·지표 검증 리포트 | GX `scripts/nogap_report.py`·`shadow_report.py`·`metric_report.py` | 13 | 로그인 | — |
| 진단 CLI(`--check` 등) | ET `run.py` | 13 | 로그인 | `--check` 가 키 앞 4자를 찍는다(절대 규칙 5와 충돌) |
| 관심종목 | SD `app.js` | 커스텀 | 로그인 | — |
| 실시간 가격 푸시 | SD socket.io `price_update` | 커스텀 / 8 | 로그인 | 네이버 → KIS REST [추정] |
| 텔레그램 push·봇 | §d | 텔레그램 | 로그인 | U1·U2 |
| 구루 브리핑(글로벌 투자자 발언) | ET `guru/` | **추가 제안**(아침 브리핑 ⑥ + 5 뉴스 칸) | 로그인[애매: DATA_TIERS 미기재, 공개 후보는 출처 링크만] | — |
| X(블루스카이) 다이제스트 | ET `xdigest/` | — | — | [제안] 폐기 |
| Claude 아티팩트 조각 | ET `web/artifact.py` | — | — | 폐기(KBJ 웹) |
| 데모 모드 | ET `--demo`·`--us-demo`(합성), GX golden | P9 데모 | 공개(합성만) | — |
| 분석 일지 마크다운 export | SD `export_to_markdown` | 12 | 로그인 | — |
| 분석 일지 초안 자동 채움 | SD `analysis_journal_helper.build_prefill_data`:33 ← `/api/journal/prefill/<code>`(`api_analysis_journal_prefill`:16715)·`/api/verification/<code>/prefill`(`api_verification_prefill`:16874) | 12 | 로그인(개인) | 2026-10-06 점검에서 추가 |
| 검토 세션 수동 분석 대 자동 산출 비교 | SD `review_validator.py`(328줄, CLI) | 13 | 로그인 | 검증 도구. KBJ 지표 시험으로 대체 [제안] |
| 증권사 리포트 기반 종목 추천, 기업·산업 리포트 목록 | SD `api_research_recommend`:9384(목표가·괴리율 점수), `api_research_companies`:9326, `api_research_sectors`:9297 | 5 / 8 | 로그인 | ⚠ 네이버 리서치 — 대체 없음 → 폐지(U4) |
| 미국 애널리스트 추천·목표가 | SD `api_research_us_recommend`:9536(Finnhub, 상위 50) | 보류(국장 대상 밖) | 로그인 | Finnhub |
| 국장 스크리너 | SD `api_screener`:2605(data.json 테마 종목 + KRX OpenAPI 전 종목) | **추가 제안**(2 또는 8) | 로그인 | KRX 일별로 재구현 |
| 두 종목 비교 | SD `api_compare`:3767 | 8 | 로그인 | — |
| 순위 변동(N분 전 대비) | SD `api_rank_change`:979(`cache/ranking_history.json`) | 1 | 로그인 | 네이버 시세 기반 → KIS |
| 시간외 단일가 | SD `api_after_hours`:8540, `price_sync_afterhours` | 8 | 로그인 | 네이버 → KIS [추정] (`conflict_map.md` §1.13) |
| 코스피200 옵션 PCR | SD `api_kr_options`:4889(`data.krx.co.kr` 차단으로 늘 unavailable) | 3 | 로그인 | 폐기 → GX PCR |
| 동적 유니버스 관리 | SD `universe_manager.py`(`sync_valuechain_to_universe`:30 → `index_universe`) | 13(운영) | 로그인 | 유니버스 4벌 정리(`conflict_map.md` §4 Q11) |
| DB 백업·복원 API | SD `api_db_backup`:4624(GET·POST)·`api_db_restore`:4634, `db_backup.py` | 13 | 로그인 | **인증 없음** → 폐기(pg_dump) |
| 밸류체인 맵 보정·검증 스크립트 | SD `scripts/fix_valuechain_{map,step2,v2}.py`(1회용), `validate_valuechain_external.py`(네이버 기업개요), `validate_valuechain_llm.py`(로컬 Ollama) | — | — | 이식 안 함(U4·Ollama 폐기). 결과 JSON은 `conflict_map.md` §2 |
| 배포 실측·네이버 진단 | SD `scripts/verify_deploy.py`(Render 주소 하드코딩), `probe_naver_{fields,json_api,sources}.py`·`probe_ohlcv_{fill,sources}.py`(네이버 엔드포인트 생존 점검) | 13 | — | 배포 스모크는 KBJ 헬스로, 네이버 진단은 폐기(U4) |
| 실행 요약 | ET `board/tools/summary.py`(Actions 런 요약 마크다운) | 13 | 로그인 | 작업 등록부 실행 기록으로 |
