# 인벤토리 — stock-dashboard

- 대상: `/home/user/p0src/stock-dashboard` (읽기 전용 스냅샷, HEAD `f46178c`, 마지막 커밋 2026-09-29)
- 작성: 2026-10-06, P0 인벤토리. 키·토큰 값은 적지 않았다(환경변수는 이름만). `.env`·토큰 캐시 파일은 열지 않았다.
- 줄 번호는 이 스냅샷 기준이다. `server.py:NNNN` 는 함수 정의 줄이다.
- 표기: [추정] = 코드만 보고 짐작한 것, 실측하지 않음.

---

## 1. 개요

| 항목 | 내용 |
|---|---|
| 무엇 | 한국(+미국) 주식 대시보드. 시세·테마·밸류체인·수급·신고가·공시·컨센서스·어닝 서프라이즈·밸류에이션 밴드·TAM 모델·분석 일지·포트폴리오를 웹 화면으로 보여 주고, 텔레그램으로 시황·알림을 보낸다 (`REBUILD_BRIEF.md` §1~3) |
| 규모(실측) | 추적 파일 89개. Python 59개 36,987줄 · JS 8개 13,119줄 · CSS 1개 7,046줄 · HTML 1개 368줄 · 워크플로 YAML 8개 563줄 · SQL 1개 238줄 · sh 1개 104줄 · JSON 6개(데이터) |
| 거대 파일 | `server.py` **19,112줄**(Flask 라우트 156개, 최상위 함수·클래스 401개, `add_job` 43회), `static/js/pages.js` 9,152줄, `static/js/chart.js` 2,204줄 |
| 테스트(실측) | pytest 없음. `scripts/check_*.py` 10개 + `scripts/test_*.py` 2개 = **검사 스크립트 12개**, 검사 호출(`assert`/`want(`/`check(`) **328개** (grep 집계: check_ohlcv_autofill 93, check_newhigh_full_list 35, check_closing_brief 31, check_futures_us_index 27, test_data_json 25, test_collectors 24, check_new_high_logic 20, check_watchdog_gating 20, check_newhigh_flow 19, check_watchdog_alerts 13, check_etf_marking 11, check_brief_sections 10). `revision_calculator._self_test` 별도 |
| 언어 | Python 3.11.9(`render.yaml`), 바닐라 JS(SPA), SQLite |
| 의존성(`requirements.txt`) | Flask, pykrx, pandas, APScheduler, gunicorn, yfinance, requests, beautifulsoup4, lxml, flask-socketio, simple-websocket |
| 누락 의존성 | `aiohttp`·`tenacity`(`dart_collector.py:21`) — requirements 에 없다(맥북 전용 CLI라 Render에서 안 돌아서 드러나지 않음). `numpy` 는 pandas 경유 |
| 실행 방식 | **서버**: Render 무료 플랜 web 서비스, `gunicorn --workers 1 --threads 4 --timeout 180 server:app`(`render.yaml`), TZ=Asia/Seoul, autoDeploy. 서버 안 APScheduler가 정기 작업을 돈다. **CLI**: 각 수집기 모듈이 `python3 xxx.py all` 형태. **맥북 cron**: `scripts/daily_macbook_cron.sh`(매일 18:00, 수집 후 `data.json` git push). **GitHub Actions**: 8개(깨우기·진단·검증, §4) |
| 영속성 | Render 디스크 비영속 → 재시작마다 `db/dashboard.db`·`cache/` 소실. 핵심 테이블만 GitHub **비공개 Gist** 백업/복원(`db_backup.py`), 종목 유니버스는 git 시드(`data/naver_universe_seed.json`) |
| 부팅 | `server.py:19096` `SERVER_NO_STARTUP!=1` 이면 `_startup()`(server.py:6919): DB init·Gist 복원·일봉 채움 스레드·data.json 스레드·yfinance 워밍·텔레그램 webhook 등록·스케줄러·WebSocket 브로드캐스터 |

---

## 2. 모듈 표

| 경로 | 역할 | 주요 함수·클래스 | 의존하는 외부 출처 |
|---|---|---|---|
| `server.py` | Flask 앱 전체(라우트 156, 스케줄러, 텔레그램, 지표, 시황, 스크리너, 발굴, 백테스트, 워치독) | `_startup`:6919, `send_telegram`:5205, `build_market_summary`:13630, `_refresh_prices_from_naver`:8139, `_fill_ohlcv_job`:8428, `_build_data_json`:7893, `_market_watchdog`:14998, `check_alert_rules`:5579 | 네이버(polling·m.stock·api.stock·finance), KRX OpenAPI(`krx_api`), pykrx, yfinance, DART, Finnhub, 네이버 검색 API, KIS(`kis_api`), alternative.me, CNN F&G, esignal·investing.com 스크랩, Wikipedia, Telegram, GitHub Gist |
| `kis_api.py` | KIS REST 조회 전용(분봉·호가·투자자·현재가·코스피200 선물) | `_get_token`:50, `_headers`:82, `_rate_limit`:100, `get_minute_chart`:155, `get_orderbook`:210, `get_investor_trading`:256, `get_price_detail`:295, `get_kospi200_futures`:396 | KIS OpenAPI, KIS 종목마스터 zip(`new.real.download.dws.co.kr`) |
| `krx_api.py` | KRX OpenAPI(전종목 일별·지수·종목마스터) | `krx_api_call`:59, `krx_all_stocks_kospi/kosdaq`:125/130, `krx_kospi_series`:135, `krx_stock_master_*`:145/150, `probe_subscriptions`:158 | KRX OpenAPI(`data-dbg.krx.co.kr`) |
| `dart_collector.py` | DART 5년 분기 재무 비동기 수집(CLI) | `get_corp_code`, `parse_dart_items`, `split_cumulative_to_quarterly`, `calculate_derived_metrics`, `save_quarterly_to_db`, `fetch_all_kr_stocks_sync` | DART OpenAPI (aiohttp+tenacity) |
| `earnings_parser.py` | DART 잠정실적 본문 파서 → `earnings_actual` | `classify_disclosure_type`, `fetch_dart_document`, `parse_preliminary_earnings`, `process_disclosure`, `collect_universe_recent` | DART |
| `earnings_signal_classifier.py` | 컨센서스 sanity check + 서프라이즈 12개 시그널 분류 → `earnings_surprise` | `sanity_check_consensus`, `classify_signal`, `process_earnings_announcement` | (DB만) |
| `earnings_telegram_sender.py` | 어닝 알림 발송·성과 백필 | `send_telegram_message`:51, `send_earnings_alert`:108, `send_pending_alerts`:201, `backfill_alert_performance`:257 | Telegram |
| `earnings_alert_writer.py` | 어닝 알림 본문을 로컬 LLM으로 작성(맥북 전용), 실패 시 템플릿 | `cache_get/set`, LLM 호출(Ollama `qwen3:14b`) | `localhost:11434`(Ollama) |
| `overhang_parser.py` | DART CB/BW/유증/EB 본문 파싱 → `dart_disclosure_overhang` | `classify_overhang_type`, `parse_cb_bw_content`, `parse_paid_in_content` | DART |
| `consensus_collector.py` | 컨센서스(Fwd EPS·목표가) 수집 → `consensus_estimate` | `fetch_naver_consensus`, `fetch_yfinance_consensus`, `collect_consensus_all` | 네이버 금융 스크랩, yfinance |
| `consensus_quarterly_collector.py` | 분기 컨센서스(`main.naver` tb_type1) → `consensus_quarterly` | `fetch_naver_main`, `parse_tb_type1_table`, `collect_all` | 네이버 금융 스크랩 |
| `consensus_snapshot_collector.py` | 350종목 컨센서스 일별 스냅샷 → `consensus_snapshot` | `get_extended_universe`, `run_daily_snapshot` | 네이버 금융(consensus_quarterly_collector 재사용) [추정] |
| `revision_calculator.py` | 스냅샷 시계열 리비전 % → `revision_alerts` | `classify_signal`, `compute_revisions_for_stock`, `compute_all`, `_self_test` | (DB만) |
| `valuation_calculator.py` | 5년 PER/EV-EBITDA/PBR 밴드 + 오버행 보정 → `valuation_band` | `calculate_per_band`, `calculate_ev_ebitda_band`, `calculate_pbr_band`, `percentile_rank`, `calculate_all_bands` | (DB만: ohlcv·financial_quarterly) |
| `tam_modeler.py` | Bear/Base/Bull EPS×PER 목표가 | `calc_base_eps`, `calc_bear_eps`, `calc_bull_eps`, `calc_per_scenarios`, `build_auto_tam` | yfinance(US 폴백) |
| `valuechain.py` | 밸류체인 맵 + 뉴스 heat + 반영도 점수 | `load_valuechain_map`, `calculate_layer_heat`, `calculate_reflection_score`, `_calculate_segment_heat_v2` | 네이버 검색 API, Finnhub 뉴스, yfinance(US) |
| `universe_manager.py` | 동적 유니버스(`index_universe`) | `sync_valuechain_to_universe`, `sync_top_market_cap_kospi/kosdaq` | (DB·JSON) |
| `ohlcv_autofill.py` | 일봉 자동 채움(신고가 입력), 시총 1,000억 이상 비ETF | `select_universe`, `plan`, `fill`, `status`, `_fetch_naver`, `_fetch_pykrx` | 네이버 `api.finance.naver.com` 일봉, pykrx 폴백 |
| `ohlcv_5y_collector.py` | 5년 일봉(밸류에이션 밴드용, CLI) | `fetch_5y_ohlcv`, `fetch_all_kr` | pykrx |
| `data_fetcher.py` | 테마 시세 → `data.json`(맥북 CLI, 서버판은 `_build_data_json`) | `fetch_ticker_data`, `fetch_indices`, `fetch_new_highs`, `fetch_market_overview`, `build_stock_master`, `apply_rank_changes` | pykrx, 네이버 polling |
| `theme_crawler.py` | 네이버 테마-종목 매핑 → `themes_mapping.json` | `parse_theme_list`, `parse_theme_stocks`, `fetch_all_theme_stocks` | 네이버 금융 스크랩 |
| `data_freshness.py` | 데이터 신선도 메타(`DATA_SOURCE_CONFIG`) | `get_freshness`, `get_all_sources_status`, `get_freshness_summary` | (DB·파일 mtime) |
| `db_backup.py` | 핵심 테이블·JSON → 비공개 Gist 백업/복원 | `backup_db`:180, `restore_db`:298, `_build_mini_db`:122 | GitHub API |
| `db/database.py`, `db/schema.sql` | SQLite 헬퍼·스키마 | `init_db`, `get_db`, `bulk_upsert`, `read_chart_db`, `read_flow_db` | — |
| `db/migrate_from_json.py`, `migrations/004~008` | JSON→SQLite, 스키마 마이그레이션 | `migrate_*` | — |
| `analysis_journal_api.py`, `analysis_journal_helper.py` | 분석 일지 CRUD·초안 자동 채움 | `create_journal`, `import_review_session_analysis`, `export_to_markdown`, `build_prefill_data` | — |
| `review_validator.py` | 수동 검토 vs 자동 산출 비교 | `compare_review_vs_auto`, `verify_step25_constraints` | — |
| `agents/pipeline.py` | 규칙 기반 5단계 추천 에이전트(LLM 없음) | `agent1_news`~`agent5_fundamental`, `run_pipeline`:897, `send_agent_telegram`:989 | 네이버 검색 API, Finnhub 뉴스 |
| `static/js/*.js`, `index.html`, `static/css/style.css` | SPA(사이드바 23페이지), D3 차트, socket.io | `app.js`(라우팅·관심종목), `pages.js`(페이지 렌더), `chart.js`, `websocket.js`, `freshness.js`, `marketContext.js`, `trading.js`(포지션 사이징) | 자체 API만 |
| `scripts/` | 검사·진단·배포 확인·맥북 cron·밸류체인 정리 | `verify_deploy.py`, `probe_*.py`, `check_*.py`, `validate_valuechain_*.py`, `fix_valuechain_*.py` | 네이버·Render(진단용) |

---

## 3. 외부 데이터 클라이언트 표

| 출처 | 파일(함수) | 인증(환경변수 이름) | 호출 한도·재시도·캐시 | KIS 토큰 직접 발급? |
|---|---|---|---|---|
| **KIS** 한국투자증권 REST | `kis_api.py` (`_get_token`:50, `get_*`), 호출처 `server.py` `api_kis_*`:16081~16133, `_kospi200_futures_section`:13599 | `KIS_APP_KEY`, `KIS_APP_SECRET` | 자체 레이트리미트 **초당 18회**(`_rate_limit`:100, 공식 20회 기준). 재시도 없음. 메모리+파일 캐시(`cache/kis_{key}.json`, TTL 장중/장외 차등). 매매 없음(조회 전용) | **예** — `kis_api._get_token`:50 이 `POST /oauth2/tokenP` 로 직접 발급, 메모리 + **`cache/kis_token.json` 파일 캐시**(만료 300초 전 재발급, 락은 프로세스 내 `threading.Lock`만). WebSocket 접속키(approval) 발급은 없음 |
| **KRX OpenAPI** | `krx_api.py` (`krx_api_call`:59), 호출처 `server.py` `_get_krx_all_stocks_cached`:1083, `api_krx_status`:3750 | `KRX_API_KEY`(헤더 `AUTH_KEY`), `KRX_API_BASE`(기본값 있음) | 호출 간 0.2초 스로틀, 지수 백오프 재시도(`2**attempt`). 일 캐시 `cache/krx_all_stocks_{today}.json` | 아니오 |
| **pykrx**(KRX 웹 스크랩) | `server.py` `_pykrx_call`:248(풀 5), `api_chart`:4404, `api_compare`:3767, `api_price`:4067, `_run_stage2_kr`:11856, `_fetch_night_futures`:4696 등; `ohlcv_5y_collector.py`, `data_fetcher.py`, `ohlcv_autofill._fetch_pykrx` | 없음 | 스레드풀 5. Render(데이터센터 IP)에서 차단되는 경우가 있어 주석상 네이버 우선 [추정: `ohlcv-probe.yml` 진단 존재]. `api_kr_options`:4889 는 "data.krx.co.kr 차단 상태라 전부 unavailable" | 아니오 |
| **DART OpenAPI** | `server.py` `poll_dart_disclosures`:10294, `_load_dart_corp_code_map`:10453, `_fetch_dart_quarter`:10552, `_try_dart_segment_revenue`:10581, `_fetch_kr_earnings`:11174; `dart_collector.py`, `earnings_parser.py`, `overhang_parser.py` | `DART_API_KEY` | 공시 폴링 1분 간격(평일 08~17시). `dart_collector`: `RATE_LIMIT_DELAY=0.15`초 + Semaphore + tenacity 지수 재시도, 상태 `013`(데이터 없음) 처리. corp_code 매핑은 DB `dart_corp_map`(매일 03:00 갱신) | 아니오 |
| **네이버 금융**(polling 실시간) | `server.py` `_fetch_naver_live_prices`:801, `_fetch_kr_indices_live`:862, `_append_today_candle_kr`:4352, `_price_broadcaster`:6867, `_refresh_prices_from_naver`:8139; `data_fetcher.py` | 없음(UA·Referer 위장) | 재시도 거의 없음, 실패는 링버퍼(`_note_collect_error`, `/api/ops/diag/collect_errors`) | 아니오 |
| **네이버 모바일 JSON**(`m.stock`·`api.stock`) | `server.py` `_naver_json`:3204, `_fetch_naver_trend`:2797(수급), `_scrape_naver_sectors`:3217, `_scrape_naver_sector_detail`:3303, `_fetch_naver_minute_candles`:4163 | 없음 | 재시도 없음, 실패 링버퍼. 파일 캐시 `flow_{code}.json`, `sectors_naver_landing.json` 등 | 아니오 |
| **네이버 금융 HTML 스크랩** | `server.py` `_crawl_naver_research`:2351, `_extract_report_html`:2406, `api_flow`:3129(`frgn.naver`), `api_financial`:3854, `_scrape_etf_price`:6573, `_scrape_naver_research_list`:9230, `_fetch_naver_board_titles`:15419(종목토론실); `consensus_collector.py`, `consensus_quarterly_collector.py`, `theme_crawler.py`(0.3초 간격) | 없음 | 모듈별 고정 sleep(`NAVER_DELAY`, `REQUEST_DELAY`, 0.3초). 재시도 거의 없음 | 아니오 |
| **네이버 일봉 API** | `ohlcv_autofill.py` `_fetch_naver`(`api.finance.naver.com`) | 없음 | 동시 4개(ThreadPool), 호출 간 gap sleep(:394), 증분 수집 | 아니오 |
| **네이버 검색 API**(뉴스) | `server.py` `api_news`:2270; `valuechain._fetch_naver_news`; `agents/pipeline.agent1_news` | `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | 파일 캐시 `news_{code}.json` | 아니오 |
| **Yahoo(yfinance)** | `server.py` `_fetch_us_market_data`:1464, `api_us_*`:1844~2216, `api_macro`:9982, `_compute_options_signal`:4947, `_us_new_highs_from_yinfo`:10972, `_run_stage2_us`:12169, `_fetch_night_futures`:4696; `consensus_collector.py`, `tam_modeler.py`, `valuechain.py` | 없음 | `_yf()` 재시도 3회·1.5초(server.py:7786), 부팅 시 `_warm_yfinance` 선적재. 캐시 `us_market_{today}.json`, `us_yinfo_{sym}.json`, DB `yinfo_cache`, `macro_data.json` | 아니오 |
| **Finnhub** | `server.py` `api_calendar_economic`:11059, `_fetch_us_earnings`:11132, `api_research_us_recommend`:9536; `valuechain._fetch_finnhub_news`; `agents/pipeline.agent1_us_news` | `FINNHUB_API_KEY` | 캐시 `economic_calendar_*.json`, `earnings_calendar_*.json` | 아니오 |
| **Wikipedia** | `server.py` `_sp500_tickers`:1251 (S&P500/400/600 구성) | 없음 | 캐시 `sp500_tickers.json` | 아니오 |
| **Fear & Greed** | `server.py` `api_fear_greed`:15997 (`api.alternative.me/fng`, CNN `production.dataviz.cnn.io`) | 없음 | 캐시 `fear_greed.json` | 아니오 |
| **야간선물 스크랩** | `server.py` `_fetch_night_futures`:4696 (esignal.co.kr, kr.investing.com; 1순위 yfinance/pykrx [추정]) | 없음 | 티어 폴백, 캐시 `night_futures.json` | 아니오 |
| **Telegram Bot API** | `server.py` `send_telegram`:5205, `_telegram_setup_webhook`:5882; `earnings_telegram_sender.send_telegram_message`:51 | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_ENABLED` | 재시도 없음, timeout 10초. 긴 본문은 `send_telegram_long`:5273(3,900자 줄 단위 분할) | 아니오 |
| **GitHub Gist** | `db_backup.py` `_gh_request`:102 | `GITHUB_TOKEN`, `BACKUP_GIST_ID` | 매시 30분 백업, 부팅 시 복원. 비공개 Gist(`public=False`:231) | 아니오 |
| **Ollama(로컬 LLM)** | `earnings_alert_writer.py` (`localhost:11434`, `qwen3:14b`) | 없음 | `db/llm_cache.db` 영구 캐시 | 아니오 |
| 미사용 | 공공데이터포털, ECOS, KOSIS, KOFIA, FRED, RSS, ETF 운용사 | — | 이 레포에는 호출 코드가 없다(grep 확인) | — |

---

## 4. 정기 실행 표

APScheduler 는 모두 `server.py` `_startup()` 안(7072~7517행), 스케줄러 TZ 는 서버 TZ(Asia/Seoul, `render.yaml`)이다. "텔레그램 조건부" = `TELEGRAM_BOT_TOKEN`·`TELEGRAM_CHAT_ID` 가 있을 때만 등록(7078).

| 시각(KST)·주기 | 작업(job id → 함수) | 정의 위치 | 받는 데이터 | 산출물 |
|---|---|---|---|---|
| 장중 N분 간격 | `market_update` → `trigger_fetch` | APScheduler :7072 (**Render 에선 미등록**, `DISABLE_AUTO_FETCH`) | pykrx 등 | data.json(로컬) |
| 평일 08:30 | `tg_morning` → `alert_morning_briefing`:6064 | APScheduler, 텔레그램 조건부 | `macro_data.json` 등 캐시 | 텔레그램 아침 브리핑 |
| 평일 18:30 | `tg_revision_signals` → `alert_revision_signals`:5948 | 〃 | `consensus_snapshot` | `revision_alerts`, 텔레그램 |
| 평일 09~14시 :00·:30 | `tg_watchlist` → `alert_watchlist_price`:6248 | 〃 | 네이버 유니버스, `server_watchlist.json` | 텔레그램 |
| 평일 10:00 | `tg_reports` → `alert_new_reports`:6282 | 〃 | 네이버 리서치 | 텔레그램 |
| 평일 15:40 | `tg_closing` → `alert_closing_summary`:6490 | 〃 | 네이버 지수 라이브 | 텔레그램 "장 마감 요약" |
| 평일 15:40 | `flow_batch` → `_refresh_flow_batch(top_n=200)` | 〃 | 네이버 수급(m.stock) | `flow_cache` |
| 평일 16:00 | `tg_closing_summary` → `send_closing_market_summary`:14696 (`_CLOSING_BRIEF_HHMM=(16,0)`:14802) | 〃, 하루 1회 제한 `ops_state` | stocks·ohlcv·flow_cache·data.json·US 지수·KIS 선물 | 텔레그램 장마감 시황 |
| 평일 16:05~20:35 :05·:35 | `closing_brief_catchup`:14805 (+부팅 직후 1회) | 〃 | 위와 같음 | 밀린 시황 발송(마감 20:35 `_CLOSING_BRIEF_DEADLINE_HHMM`) |
| 평일 19:30 | `tg_flow_signals` → `alert_flow_signals`:15196 | 〃 | `flow_cache` | 텔레그램 수급 시그널 |
| 평일 15:48 | `prewarm_new_highs` → `_prewarm_new_highs`:10944 | 〃 | ohlcv·stocks | `new_highs_kr_{today}.json` |
| 평일 08~20시 :00·:30 | `data_watchdog` → `_market_watchdog`:14998 | 〃 | stocks·flow_cache 상태 | 자동 재갱신 + 관리자 알림(`WATCHDOG_TELEGRAM=1`일 때) |
| 화~토 06:10 | `tg_us_market_summary` → `send_us_market_summary_telegram`:14554 | 〃 | yfinance US | 텔레그램 미국장 시황 |
| 화~토 05:00 | `tg_briefing_refresh` → `_refresh_briefing_data` | 〃 | 매크로·옵션·선물 | 캐시 |
| 화~토 05:30 | `tg_overnight` → `alert_overnight_prediction`:6310 | 〃 | SPY/QQQ 옵션, 야간선물, 매크로 | 텔레그램 익일 예측 |
| 평일 22:00 | `refresh_options` → `refresh_us_options_signal` | 〃 | yfinance 옵션 | `options_signal_*.json` |
| 평일 09~15시 10분마다 | `tg_custom_alerts` → `check_alert_rules`:5579 | 〃 | `cache/alert_rules.json`, 유니버스, `/api/chart`, `/api/flow` | 텔레그램 맞춤 알림 |
| 평일 09~15시 :00·:30 | `tg_trailing` → `_check_trailing_stops`:5438 | 〃 | `server_portfolio.json`, 시세 | 텔레그램 트레일링 스톱 |
| 평일 09~15시 :05·:35 | `price_sync_intraday` → `_refresh_prices_from_naver`:8139 | APScheduler :7149 | 네이버 polling | `stocks`, 유니버스 |
| 평일 15:35 | `price_sync_close` → 〃 | 〃 | 〃 | 〃 |
| 평일 16~17시 5분마다 | `price_sync_afterhours` → 〃 | 〃 | 시간외 단일가 | `stocks.after_hours_*` |
| 평일 15:45, 16:20 | `data_json_close`/`data_json_evening` → `_refresh_data_json_job`:8096 | 〃 | stocks·ohlcv·테마 | `data.json`(서버 생성) |
| 평일 16:10 | `ohlcv_autofill` → `_fill_ohlcv_job`:8428 (+부팅 스레드) | 〃 | 네이버 일봉(pykrx 폴백) | `ohlcv` |
| 평일 09~15시 5분마다 | `stage2_realtime_kr` → `_run_stage2_kr` | 〃 | SQLite | `discover_results`, `discover_kr_stage2.json` |
| 평일 08:00, 16:00 | `stage2_auto` → `_stage2_scoring_worker("kr")` | 〃 | 〃 | 〃 + `alert_discovery_new_entries` 텔레그램 |
| 평일 08:45, 15:45 | `agent_pipeline` → `run_pipeline`+`send_agent_telegram` | 〃 | 네이버 뉴스, Finnhub, DB | `cache/agent_result_latest.json`, 텔레그램 |
| 매일 03:10 | `mark_etf_stocks`:7574 | 〃 | stocks | `stocks.is_etf` |
| 일 03:20 | `gen_themes_mapping`:7588 | 〃 | 유니버스 | `themes_mapping.json` |
| 일 04:00 | `refresh_us_universe` → `refresh_us_universe_if_stale`:7639 | 〃 | Wikipedia S&P | US 유니버스 |
| 평일 08~17시 매분 | `dart_poll` → `poll_dart_disclosures`:10294 (`DART_API_KEY` 있을 때) | 〃 | DART list.json | `disclosure_history` |
| 매일 03:00 | `dart_corp_map_update` → `init_dart_corp_map_db` | 〃 | DART corpCode.xml | `dart_corp_map` |
| 4분 간격 | `self_keepalive` → `_self_keep_alive`:4577 | 〃 | 자기 `/api/health` | 슬립 방지 |
| 60분 간격 | `us_db_sync` → `sync_us_market_to_db_from_cache` | 〃 | US 캐시 | DB |
| 4시간 간격 | `global_data_refresh` → `_refresh_global_data_periodic`:6454 | 〃 | 매크로·야간선물·옵션·F&G·밸류체인 heat | 캐시 |
| 매일 08:00 | `kr_universe_daily` → `_build_naver_universe_background` | 〃 | 네이버 | `naver_universe_{today}.json` |
| 화~토 05:50 | `us_market_daily` → `_fetch_us_market_data(force)` | 〃 | yfinance | `us_market_{today}.json`, DB |
| 매시 :30 | `db_backup_hourly` → `db_backup.backup_db` | 〃 | DB 핵심 테이블 | 비공개 Gist |
| 매월 1일 03:00 | `universe_sync_monthly` → `sync_valuechain_to_universe` | 〃 | valuechain_map | `index_universe`, 변동 시 텔레그램 |
| 월 06:00 | `consensus_quarterly_weekly` → `consensus_quarterly_collector.collect_all` | 〃 | 네이버 main | `consensus_quarterly` |
| 5분 간격 | `earnings_pipeline_5min` → 파싱→분류→발송 | 〃 | `disclosure_history`, DART 본문 | `earnings_actual`·`earnings_surprise`, 텔레그램 |
| 매일 06:30 | `earnings_backfill_daily` → `backfill_alert_performance` | 〃 | ohlcv | `earnings_surprise` 성과 |
| 평일 18:00 | `consensus_snapshot_daily` → `run_daily_snapshot(350)` | 〃 | 네이버 | `consensus_snapshot` |
| 상시 스레드 | `_price_broadcaster`:6867 | WebSocket 스레드 | 네이버 polling | socket.io `price_update` |
| 매일 18:00 | 맥북 cron: DART retry → OHLCV 5y → data_fetcher → 밸류에이션 밴드 → 컨센서스 → TAM → Gist 백업 → `data.json` git push | `scripts/daily_macbook_cron.sh`(crontab, 로컬 경로 하드코딩) | DART·pykrx·네이버·yfinance | DB, data.json(git) |
| 평일 15:50·15:55·16:00~16:20 5분 | `wake.yml` — Render 깨우기(`/api/health`) | GitHub Actions cron `50,55 6 * * 1-5`, `0,5,10,15,20 7 * * 1-5`(UTC) | — | 부팅 캐치업 유도 |
| push 시 | `collector-test.yml` — `test_collectors.py`·`test_data_json.py` | GitHub Actions(server.py 등 변경) | 네이버 실호출 | 검증 로그 |
| 수동 | `brief-kick.yml`(시황 강제 발송), `collect-diag.yml`, `verify-deploy.yml`, `summary-probe.yml`, `naver-probe.yml`, `ohlcv-probe.yml` | GitHub Actions `workflow_dispatch`(일부 특정 브랜치 push) | Render API·네이버 | 로그·요약 |

정리: APScheduler `add_job` 43회(Render 실등록 약 42개), GitHub Actions 정기 1개(wake) + push 트리거 3개 + 수동 4개, 맥북 crontab 1개.

---

## 5. 텔레그램 표

공통 발송: `server.py` `send_telegram`:5205(HTML, `TELEGRAM_ENABLED=0` 이면 생략), 긴 본문 `send_telegram_long`:5273. 어닝만 별도 구현 `earnings_telegram_sender.send_telegram_message`:51(중복 구현).

| 발송 함수 | 메시지 종류 | 발송 시각·조건 | 봇/채팅 환경변수 | 양방향 |
|---|---|---|---|---|
| `alert_morning_briefing`:6064 | 오늘의 시황 브리핑(USD/KRW·WTI·VIX 등) | 평일 08:30 | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | — |
| `alert_overnight_prediction`:6310 | 익일 예측(SPY/QQQ 옵션·야간선물·매크로·일정) | 화~토 05:30 | 〃 | — |
| `send_us_market_summary_telegram`:14554 | 미국 장마감 시황 | 화~토 06:10 | 〃 | — |
| `alert_watchlist_price`:6248 | 관심종목 가격 | 평일 09~14시 30분 간격 | 〃 | — |
| `alert_new_reports`:6282 | 관심종목 신규 증권사 리포트 | 평일 10:00 | 〃 | — |
| `alert_closing_summary`:6490 | 장 마감 요약(지수 등) | 평일 15:40 | 〃 | — |
| `send_closing_market_summary`:14696 → `send_market_summary_telegram`:14583 | 장마감 확정 시황(특징주·신고가·지수·선물) | 평일 16:00, 캐치업 16:05~20:35, 하루 1회(`ops_state`) | 〃 | `/시황` |
| `alert_revision_signals`:5948 | 컨센서스 리비전 STRONG_UP/DOWN | 평일 18:30 | 〃 | — |
| `alert_flow_signals`:15196 | 수급 시그널(쌍끌이·외국인 연속·반전) | 평일 19:30 | 〃 | `/시그널` |
| `check_alert_rules`:5579 | 맞춤 알림(가격·등락률·RSI·MACD 크로스·외국인 연속/누적) | 평일 09~15시 10분, 규칙당 하루 1회 | 〃 | — |
| `_check_trailing_stops`:5438 | 트레일링 스톱 갱신·이탈 | 평일 09~15시 30분 | 〃 | — |
| `alert_discovery_new_entries`:6143 | 종목발굴 신규 진입·스코어 급상승(쿨다운 60분 `_alert_cooldown_ok`:6102) | Stage2 스캔 완료 후(:12162) | 〃 | — |
| `agents/pipeline.send_agent_telegram`:989 | AI(규칙) 추천 종목 | 평일 08:45, 15:45 | 〃(server.send_telegram 재사용) | — |
| `earnings_telegram_sender.send_pending_alerts`:201 | 어닝 서프라이즈(priority 1~3) | 5분 파이프라인에서 새 파싱/분류 있을 때 | 〃 + `TELEGRAM_ENABLED` | — |
| `_scheduled_universe_sync` 내부 | 유니버스 갱신(추가/제거 수) | 매월 1일 03:00, 변동 시 | 〃 | — |
| `_watchdog_notify`:14989 | 데이터 공백·정체·복구 | 워치독 30분, `WATCHDOG_TELEGRAM=1` 일 때만 | 〃 + `WATCHDOG_TELEGRAM` | — |
| `api_test_telegram_get`:4644, `api_telegram_test`:5708, `api_telegram_briefing_test`(:5718 라우트) | 서버 정상 확인·브리핑 테스트 | HTTP 호출 시(인증 없음) | 〃 | — |
| `_tg_reply`:5766 ← `_handle_telegram_command`:5823 | 봇 응답 | webhook 수신 시 | 〃 | `/도움`(help·start·명령), `/시황`(summary), `/시그널`(signals), `/수급 <종목>`(flow), `/가격 <종목>`(시세·price) |
| `_telegram_setup_webhook`:5882 | setWebhook 등록 | 부팅 시(`RENDER_EXTERNAL_URL` 있을 때), `POST /api/telegram/setup_webhook` | 시크릿 = `sha256("wh:"+봇토큰)[:32]`(`_telegram_secret`:5731) | 수신 `POST /api/telegram/webhook`:5860, 소유자 chat_id 만 처리 |

---

## 6. 저장 표

| DB 종류·파일 | 테이블(또는 파일) | 주요 컬럼 | 쓰는 쪽 → 읽는 쪽 |
|---|---|---|---|
| SQLite `db/dashboard.db`(Render 휘발, ~230MB 주석) | `stocks` | code, name, market, sector, market_cap, close, change_pct, volume_mn, after_hours_*, is_etf | `_refresh_prices_from_naver`, `mark_etf_stocks` → 시황·스크리너·신고가·화면 전반 |
| 〃 | `ohlcv` | code, date, OHLCV | `ohlcv_autofill.fill`, `ohlcv_5y_collector` → 신고가(`build_market_summary`:13912), 밸류에이션 밴드, 상관, 스파크라인, 어닝 백필 |
| 〃 | `chart_cache` | code, days, cache_date, *_json(OHLCV·bollinger·fibonacci·trendlines·analysis·rsi_macd·adx) | `api_chart` → 차트 화면, 알림 규칙 |
| 〃 | `financial` | code, per, eps, estimate_per/eps, pbr, bps, dividend_yield, market_cap, foreign_ratio, annual_json | `api_financial`(네이버) → 종목 상세·스코어 |
| 〃 | `flow_cache` | code, dates/close/foreign_*/inst_*_json, foreign_sum_20, inst_sum_20, source | `_fetch_and_save_flow`, `flow_batch` → 수급 시그널·시황·봇 `/수급`·알림 |
| 〃 | `yinfo_cache` | symbol, info_json | yfinance → US 화면·신고가 |
| 〃 | `discover_results` | stage, market, items_json | Stage2 스캔 → 종목발굴 |
| 〃 | `alert_rules` | id, code, rule_type, value, enabled, triggered_at | 동기화 API → (실제 평가는 `cache/alert_rules.json`을 읽음) |
| 〃 | `alert_history`, `alert_history_v2` | code, alert_type, alerted_at / payload, price_at_alert, performance_7d/30d | 쿨다운 → 성과 추적(v2 는 [추정] 거의 미사용) |
| 〃 | `dart_corp_map`, `disclosure_history` | stock_code↔corp_code / rcept_no, title, importance, score, alerted | DART 폴링 → 공시 화면·어닝 파이프라인 |
| 〃 | `misc_cache`, `ops_state` | cache_key, data_json / key, value | 각종 → 시황 1일 1회 제한·워치독 중복 방지 |
| 〃 | `consensus_snapshot`, `revision_alerts` | stock_code, period_*, snapshot_date, *_consensus / metric, revision_pct, window_days, signal, alert_sent | 스냅샷 수집·`revision_calculator` → 리비전 화면·알림 |
| 〃 | `financial_quarterly`, `valuation_band`, `consensus_estimate`, `dart_disclosure_overhang`, `fetch_progress` (migration 004) | 분기 재무·EBITDA·순부채 / PER·EV·PBR 백분위 / Fwd EPS·목표가 / CB·BW 희석 / 수집 진행 | 맥북 cron CLI → Render 는 Gist 복원본 read-only |
| 〃 | `analysis_journal` (004) | 종목·테마·밸류체인 위치, 밴드%, Bear/Base/Bull EPS·PER·TP, reflection_score… | 검증 시트·분석 일지 API ↔ 화면 |
| 〃 | `index_universe`, `consensus_quarterly`, `earnings_actual`, `earnings_surprise`, `earnings_alert_queue` (005) | 유니버스 / 분기 컨센 / 실적 / 서프라이즈·signal·priority·alert_sent / D-3·D-1 큐 | 어닝 파이프라인 ↔ 어닝 화면·텔레그램 |
| 〃 | `recommendation_history`(server.py:13052), `trade_journal`(server.py:15495) | date, source, rank, code, score, price_at_rec / action, price, qty, fee, tax, realized_pnl | 추천 스냅샷·수익률 저널 → 추천성과·저널 화면 |
| SQLite `db/llm_cache.db` | `llm_cache` | cache_key, model, response, hit_count | `earnings_alert_writer`(맥북) |
| JSON `cache/`(휘발, 웹 접근 403) | 사용자 상태: `server_watchlist.json`, `server_portfolio.json`, `alert_rules.json`; 토큰: `kis_token.json`; 시장 캐시 60여 종(`naver_universe_*`, `us_market_*`, `macro_data`, `night_futures`, `options_signal_*`, `fear_greed`, `flow_{code}`, `chart_*`, `new_highs_*`, `discover_*_stage2`, `agent_result_latest` 등) | — | `/api/*/sync` POST·수집기 → 알림·화면. 사용자 상태 3개는 Gist 백업 대상 |
| git 추적 JSON | `data.json`(테마·지수·신고가 섹터, 갱신 2026-09-15), `data/naver_universe_seed.json`(4,063종목), `data/valuechain_map.json`, `data/valuechain_*_validation.json`, `themes_mapping.json` | — | 맥북 cron·서버 → 화면·유니버스 시드 |
| GitHub Gist(비공개) | `db_backup.CORE_TABLES` 18개 + 위 사용자 JSON | — | 매시 백업 ↔ 부팅 복원 |
| 브라우저 localStorage | 관심종목(`_WATCHLIST_KEY`), 포트폴리오(`_PF_KEY`), 저널(`_JN_KEY`), `alert_rules`, `split_plans`, 포지션 사이징 자본·위험 | — | 프론트 → `/api/watchlist|portfolio|alerts/sync` 로 서버 복사 |

---

## 7. 환경변수 이름 목록

| 이름 | 용도 | 사용 파일 |
|---|---|---|
| `KIS_APP_KEY`, `KIS_APP_SECRET` | KIS 토큰 발급·요청 헤더 | `kis_api.py` |
| `KRX_API_KEY` | KRX OpenAPI 인증 헤더 | `krx_api.py` |
| `KRX_API_BASE` | KRX API 기본 URL(기본값 있음) | `krx_api.py` |
| `DART_API_KEY` | OpenDART, 공시 폴링 등록 조건 | `server.py`, `dart_collector.py`, `earnings_parser.py`, `overhang_parser.py` |
| `FINNHUB_API_KEY` | 경제·실적 캘린더, US 뉴스·목표가 | `server.py`, `valuechain.py`, `agents/pipeline.py` |
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | 네이버 검색 API(뉴스) | `server.py`, `valuechain.py`, `agents/pipeline.py` |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | 텔레그램 발송·봇·webhook 시크릿 파생, 알림 스케줄 등록 조건 | `server.py`, `earnings_telegram_sender.py` |
| `TELEGRAM_ENABLED` | 발송 전체 on/off(기본 1) | `server.py`, `earnings_telegram_sender.py` |
| `WATCHDOG_TELEGRAM` | 워치독 알림 on(기본 0) | `server.py`, `scripts/check_watchdog_alerts.py` |
| `GITHUB_TOKEN`, `BACKUP_GIST_ID` | Gist 백업/복원 | `db_backup.py` |
| `USE_SQLITE` | SQLite 사용(기본 1) | `server.py`, 테스트 스크립트 |
| `DISABLE_AUTO_FETCH` | 장중 자동 fetch 끄기 | `server.py` |
| `SERVER_NO_STARTUP` | import 시 부팅 루틴 생략(테스트용) | `server.py`, `scripts/test_*.py` |
| `PORT`, `HOST` | 바인딩 | `server.py` |
| `RENDER`, `RENDER_EXTERNAL_URL` | Render 감지, 자가 핑·webhook URL | `server.py` |
| `RENDER_GIT_COMMIT`, `RENDER_GIT_BRANCH`, `GIT_COMMIT` | `/api/health` 배포 버전 | `server.py` |
| `PYTHON_VERSION`, `TZ` | Render 런타임 설정 | `render.yaml` |
| (Actions 저장소 변수) `DASHBOARD_URL` | 워크플로 대상 URL(기본값 Render 주소 하드코딩) | `.github/workflows/*.yml` |

`.env` 로더가 6곳에 따로 있다(`server.py`:35, `data_fetcher.py`:44, `dart_collector.py`:33, `earnings_parser.py`:33, `overhang_parser.py`:30, `earnings_telegram_sender.py`:31, `agents/pipeline.py`:25).

---

## 8. 기능 목록

**화면(SPA 사이드바 23개, `index.html`)**: 대시보드 · 테마맵(트리맵) · 밸류체인 · 시황분석 · 글로벌 매크로 · 공시 · 종목 상세 · 검증 시트 · 분석 일지 · 종목발굴 · AI추천 · 관심종목 · 백테스트 · 추천성과 · 상관관계 · 데이터 신선도 · Cron 모니터 · 헬스 대시보드 · 포트폴리오 · 수익률 저널 · (숨김) 리서치 · ETF맵 · 배당. SPA 경로 `/verification[/<code>]`, `/journal[/<id>]`, `/ops/<page>`, `/freshness-demo`.

**API 엔드포인트(156개, 도메인별 요약)**

| 도메인 | 엔드포인트 |
|---|---|
| 시장·지수·테마 | `/data.json`, `/api/index_kr`, `/api/status`, `/api/refresh`, `/api/refresh_all`, `/api/refresh_prices`, `/api/interval/<n>`, `/api/rank_change/<n>`, `/api/themes`(GET/POST), `/api/sectors`, `/api/sector/<no>`, `/api/sector_rotation`, `/api/sector_rotation/phase`, `/api/etf_map`, `/api/dividend`, `/api/market/context` |
| 종목 | `/api/stock_search`, `/api/price/<code>`, `/api/after_hours/<code>`, `/api/chart/<code>`, `/api/chart_intraday/<code>`, `/api/ohlcv/<code>`, `/api/volume_profile/<code>`, `/api/financial/<code>`, `/api/dart_financial/<code>`, `/api/flow/<code>`, `/api/news/<code>`, `/api/reports/<code>`, `/api/sentiment/<code>`, `/api/peers/<code>`, `/api/compare`, `/api/screener` |
| KIS | `/api/kis/minute/<code>`, `/api/kis/orderbook/<code>`, `/api/kis/investor/<code>`, `/api/kis/price/<code>` |
| 수급·신고가·시황 | `/api/flow/refresh-batch`, `/api/flow/signals`, `/api/new_highs`, `/api/us/new_highs`, `/api/market_summary`, `/api/market_summary/us` |
| 파생·매크로 | `/api/night_futures`, `/api/kr_options`, `/api/options_signal`, `/api/macro`, `/api/global_macro`, `/api/fear_greed`, `/api/calendar/economic`, `/api/calendar/earnings` |
| 미국 | `/api/us/market`, `/api/us/search`, `/api/us/extended/<s>`, `/api/us/chart/<s>`, `/api/us/news/<s>`, `/api/us/financial/<s>`, `/api/us/screener`, `/api/us/price/<s>`, `/api/us/peers/<s>`, `/api/us/add_stocks`, `/api/us/sync_db` |
| 공시 | `/api/disclosures`, `/api/disclosures/recalc`, `/api/disclosure_bonus/<code>`, `/api/disclosure_events/<code>` |
| 리서치·추천·발굴 | `/api/research/sectors`, `/api/research/companies`, `/api/research/recommend`, `/api/research/us_recommend`, `/api/discover`, `/api/discover/scan`, `/api/discover/progress`, `/api/discover/reset`, `/api/agent/run`, `/api/agent/result`, `/api/agent/status`, `/api/recommendation/performance`, `/api/recommendation/migrate` |
| 백테스트·상관 | `/api/backtest`(POST), `/api/backtest/available_tags`, `/api/correlation` |
| 밸류체인·TAM | `/api/valuechain`, `/api/valuechain2/themes`, `/layers/<theme>`, `/segment/<t>/<l>/<s>`(+`/manage`), `/stock/<code>`(+`/tam`, `/full`), `/refresh`, `/review/compare` |
| 어닝·컨센·리비전 | `/api/earnings/universe`(+`/sync`), `/api/earnings/consensus/<code>`, `/api/earnings/consensus/collect`, `/api/revision/<code>`, `/api/revision/screener`, `/api/revision/divergence`, `/api/revision/history/<code>` |
| 검증 시트·분석 일지 | `/api/verification/<code>/prefill`, `/composite`, `/gap-analysis`, `/api/verification/stock/<code>`, `/api/journal`(CRUD, recent, stock, import-review, prefill, stats) |
| 사용자 상태 | `/api/watchlist/sync`, `/api/portfolio/sync`, `/api/alerts/sync`, `/api/alerts/list`, `/api/journal/add|list|summary|delete`(수익률 저널) |
| 텔레그램 | `/api/test_telegram`, `/api/telegram/test`, `/api/telegram/briefing_test`, `/api/telegram/webhook`, `/api/telegram/setup_webhook` |
| 운영 | `/api/health`, `/api/krx_status`, `/api/db/backup`, `/api/db/restore`, `/api/freshness/source/<k>`, `/api/freshness/all`, `/api/freshness/categories`, `/api/ops/watchdog`, `/api/ops/cron/jobs`, `/api/ops/cron/trigger/<id>`, `/api/ops/data_json/rebuild|status`, `/api/ops/ohlcv/fill|status`, `/api/ops/brief/closing`, `/api/ops/diag/collect_errors|sources|stocks_schema|kr_universe`, `/api/ops/recover/kr_stocks`, `/api/ops/health` |
| 실시간 | socket.io `connect`/`subscribe`/`unsubscribe` → `price_update`(server.py:6824~6909) |

**리포트·산출물**: 텔레그램 push(§5), `data.json`, 분석 일지 마크다운 export(`analysis_journal_api.export_to_markdown`). **엑셀/CSV 출력 없음**(grep: openpyxl·xlsx·csv 없음).

---

## 9. 계산 엔진·지표

| 영역 | 함수(위치) | 내용 | 테스트 |
|---|---|---|---|
| RSI·MACD | `_calc_rsi_macd`:368 | RSI(14), MACD(12,26,9) 순수 Python | 없음 |
| 다이버전스 | `_detect_divergences`:424 | 가격 vs RSI/MACD 강세·약세 다이버전스 | 없음 |
| ADX | `_calc_adx`:486 | ADX(14) | 없음 |
| 볼린저 | `_calc_bollinger`:542 | 20일 ±2σ | 없음 |
| 피보나치·추세선 | `_calc_fibonacci`:557, `_find_best_trendline`:571, `_calc_trendlines`:596 | 120일 고저 되돌림, 피벗 추세선 | 없음 |
| 코멘트 생성 | `_generate_analysis`:613 | 위 지표로 신호 문구·태그(과매도·신고가 근접·거래량 급증 등) | 없음 |
| 볼륨 프로파일 | `api_volume_profile`:5293 | POC·Value Area·VWAP·지지/저항 | 없음 |
| 백테스트 RSI(중복) | `_bt_calc_rsi`:12572, `api_backtest`:12742 | 태그 기반 백테스트, RSI 를 따로 다시 구현 | 없음 |
| 상관관계 | `_compute_correlation`:12945 | 종가 상관 행렬 | 없음 |
| **52주·60일·역사적 신고가** | `build_market_summary`:13630 내부 SQL(:13912, 60·252일 창, 시총 하한 `ohlcv_autofill.MIN_MARKET_CAP_WON=1,000억`), `_kr_new_highs_from_charts`:10835, `_us_new_highs_from_yinfo`:10972(95% 근접), `data_fetcher.fetch_new_highs` | 신고가 판정·섹터 집계(`_build_new_high_sectors`:7860)·신고가 종목 수급(`_newhigh_flow`:13315) | **있음**: `check_new_high_logic.py`(SQL을 server.py 에서 정규식으로 뽑아 합성 DB 실행), `check_newhigh_full_list.py`, `check_newhigh_flow.py` |
| 수급 시그널 | `_flow_streak`:15091, `_analyze_flow_signals`:15104(50억·3일) | 쌍끌이·외국인 연속·반전 | 없음(간접: `check_brief_sections`) |
| 스코어(발굴) | `_calc_momentum_score_kr`:8598, `_calc_sector_score_kr`:8697, `_calc_flow_score_kr`:11461, `_calc_valuation_score_kr`:11546, `_calc_technical_score_kr`:11643, `_calc_undervalued_bonus`:11679, `_generate_macd_tags`:11733 (+US 판 8765~8927) | 종목발굴 Stage1/2 점수 | 없음 |
| 시장 국면 | `_detect_market_phase`:15260 | 섹터 로테이션 국면 | 없음 |
| 공시 점수 | `_classify_disclosure`:10068, `score_disclosure`:10102 | 공시 제목 키워드 중요도 | 없음 |
| 감성 | `_score_sentiment_titles`:15455 | 종목토론실 제목 사전 점수 | 없음 |
| 옵션 시그널 | `_compute_options_signal`:4947 | SPY/QQQ PCR 등 | 없음 |
| 밸류에이션 밴드 | `valuation_calculator.calculate_per_band`·`calculate_ev_ebitda_band`·`calculate_pbr_band`·`percentile_rank` | 5년 밴드, 오버행 희석 보정 | 없음 |
| TAM 목표가 | `tam_modeler.calc_*_eps`, `calc_per_scenarios`, `calc_pbr_based_tp` | Bear/Base/Bull | 없음 |
| 반영도 | `valuechain.calculate_reflection_score`, `_calculate_segment_heat_v2` | 52주 수익률·PER·EV 점수 | 없음 |
| 검증 시트 점수 | `_classify_auto_reflection`:16768, `_score_valuation`:17186, `_score_earnings`:17196, `_score_technical`:17202, `_score_review`:17211 | 종합 점수 | 없음 |
| 어닝 서프라이즈 | `earnings_signal_classifier.classify_signal` | 12 시그널 + 우선순위 | 없음 |
| 리비전 | `revision_calculator.classify_signal`, `compute_revisions_for_stock` | N일 리비전 % | `_self_test` 내장 |
| DART 분기 분리 | `dart_collector.split_cumulative_to_quarterly`, `calculate_derived_metrics` | 누적→분기, EBITDA·순부채 | 없음 |
| 트레일링 스톱 | `_check_trailing_stops`:5438 | fixed_pct·atr(근사)·chandelier(근사), 최대 -7% | 없음 |
| 맞춤 알림 평가 | `check_alert_rules`:5579 | price/change/RSI/MACD/외국인 | 없음 |
| ETF 판정 | `_is_etf_name`:7563, `ETF_PATTERNS` | 이름 패턴 | **있음** `check_etf_marking.py`, `summary-probe.yml` |
| 워치독 | `_check_market_data_health`:14905, `_watchdog_checks_due`:14852 | 공백·정체 판정 | **있음** `check_watchdog_alerts.py`, `check_watchdog_gating.py` |
| 일봉 채움 | `ohlcv_autofill.plan`·`fill` | 증분·소스 폴백 | **있음** `check_ohlcv_autofill.py`(93 검사) |
| 시황 조립 | `build_market_summary`, `_brief_data_ready`:14648 | 섹션·준비 판정 | **있음** `check_closing_brief.py`, `check_brief_sections.py`, `check_futures_us_index.py` |

---

## 10. 품질·위험 메모

| 구분 | 내용 | 근거 |
|---|---|---|
| **KIS 토큰 충돌** | `kis_api._get_token` 이 자체 발급하고 `cache/kis_token.json` 에 둔다. 같은 앱키를 다른 프로젝트(board·GEXLAB)가 쓰면 서로 토큰을 무효화하거나 1분 1회 발급 제한에 걸린다. 레이트리미트도 초당 18회로 잡혀 있어 PLAN §2 실측(초당 4건)과 다르다 | `kis_api.py:5,50~79,100` |
| **인증 없는 쓰기·발송 엔드포인트** | 공개 Render URL에서 누구나 `GET /api/test_telegram`(텔레그램 발송), `GET/POST /api/db/backup`, `POST /api/db/restore`, `POST /api/ops/*`(시황 강제 발송·일봉 채움·cron 트리거·복구), `/api/journal` CRUD, `/api/*/sync`(관심종목·포트폴리오 덮어쓰기)를 호출할 수 있다 | `server.py:4623~4660, 8493, 18369, 5420, 5911` |
| **정적 파일 catch-all 노출** | `/<path:filename>` 이 `BASE_DIR` 아래 아무 파일이나 보낸다(`cache/`만 403). `db/dashboard.db`(trade_journal·analysis_journal 포함), `db/llm_cache.db`, 로컬 실행 시 `.env` 가 그대로 내려받아질 수 있다. `.git/` 노출은 [추정](Render 빌드 디렉터리에 있을 경우) | `server.py:4557~4565` |
| 키 노출 위험(낮음) | 하드코딩 키는 grep 상 없다. 단 KIS 토큰을 평문 파일로 캐시, 텔레그램 URL에 봇 토큰이 들어가 예외 로그(`r.text`)에 섞일 여지 [추정], webhook 시크릿을 봇 토큰 해시로 파생(토큰 바뀌면 시크릿도 바뀜) | `kis_api.py:27,42`, `server.py:5216,5731` |
| 거대 단일 파일 | `server.py` 19,112줄(브리프의 1.6만 줄보다 커짐), `pages.js` 9,152줄 | §1 |
| 중복 구현 | 텔레그램 발송 2벌(`server.send_telegram`, `earnings_telegram_sender.send_telegram_message`), `.env` 로더 7벌, RSI 2벌(`_calc_rsi_macd`, `_bt_calc_rsi`), 장마감 메시지 2개가 15:40(`alert_closing_summary`)·16:00(`send_closing_market_summary`)에 연달아 나감, data.json 생성기 2벌(`data_fetcher.py` 맥북, `_build_data_json` 서버), 관심종목·알림 규칙이 localStorage·`cache/*.json`·DB `alert_rules` 3곳 | 각 줄 번호 §2·§5 |
| 깨진·죽은 부분 | `_format_rule_label` 에 `bb_upper`·`bb_lower`·`volume_spike` 라벨은 있으나 `check_alert_rules` 평가 분기가 없다(조용히 미발동). `*_legacy_unused` 함수 3개(네이버 PC 페이지 SPA 전환으로 사용 중단). `api_kr_options` 는 KRX 차단으로 항상 unavailable. `alert_history_v2` 는 [추정] 거의 미사용. `REBUILD_BRIEF.md` 의 "알림은 가격 기준뿐"은 현재 코드(RSI·MACD·외국인 조건 있음)와 다르다 | `server.py:5560~5576, 5600~5690, 2930, 3254, 3343, 4889` |
| 의존성 누락 | `aiohttp`, `tenacity` 가 requirements 에 없다 | `dart_collector.py:21` |
| 하드코딩 | 로컬 절대경로 `/Users/<사용자>/stock-dashboard`(`daily_macbook_cron.sh`), `/home/user/stock-dashboard`(check 스크립트 7개 — 다른 경로에서 실행 불가). Render 주소 `<Render 서비스 주소>`(워크플로·`verify_deploy.py`). 시각·임계값(시총 1,000억, 수급 50억·3일, 트레일링 -7%, 컨센 350종목)이 코드 상수 | `scripts/*.py`, `.github/workflows/*.yml` |
| 운영 취약 | Render 무료 플랜 슬립 → GitHub Actions `wake.yml`·자가 핑·캐치업으로 땜질. 재시작마다 DB 휘발 → Gist 복원·부팅 일봉 채움(~7분 [추정, 주석 인용]). 맥북 cron 이 멈추면 밸류에이션·컨센 등 맥북 산출물이 굳는다(2026-09-15 실제 정지 주석). LLM 알림 작성기는 맥북 Ollama 전용 | `server.py:7040~`, `.github/workflows/wake.yml` |
| 에러 삼킴 | `except Exception: pass`/`log.debug` 다수 — 실패가 화면에 안 드러나는 곳이 많다(PLAN 절대 규칙 4 위반 소지) | `server.py` 전반 |
| 테스트 체계 | pytest·린트·타입검사 없음. 검사 스크립트가 server.py 소스를 정규식으로 잘라 SQL을 재실행하는 방식이라 리팩터링에 약하다. 지표 계산(RSI·MACD·볼린저·ADX) 단위 테스트 0 | `scripts/check_new_high_logic.py:13~30` |
| **공개 레포 이전 시 문제** | ① **네이버 금융 스크래핑 의존이 핵심 경로 전부**(시세·수급·업종·일봉·컨센서스·리서치·테마·종목토론실) — DATA_TIERS 상 "제외" 등급이라 대체 필요(KRX·KIS 로그인, 공공데이터포털 공개). ② 실데이터 fixture 커밋됨: `data.json`(시세·테마 스냅샷 107KB), `data/naver_universe_seed.json`(네이버 수집 4,063종목 403KB), `themes_mapping.json`(네이버 테마 크롤), `data/valuechain_*` (외부·LLM 검증 결과). ③ pykrx(KRX 웹 스크랩)·yfinance·Finnhub·CNN·investing.com·esignal 은 재배포 금지/로그인 등급 또는 스크랩. ④ 개인 데이터: 포트폴리오·매매 저널·분석 일지가 Gist·DB 에 있고 catch-all 로 DB 파일 노출 가능. ⑤ 커밋 이력·워크플로에 Render 서비스 주소·로컬 사용자 경로가 남아 있다 | §3, §6, `.gitignore` |
| 재사용 가치 | 지표 함수(§9 상단), 신고가 SQL·테스트, 워치독·시황 1일 1회 제한(`ops_state`) 패턴, 어닝 서프라이즈 분류, 리비전 계산, 밸류에이션 밴드, DART 누적→분기 분리는 PLAN §2 정본 후보로 옮길 가치가 있다 | `REBUILD_BRIEF.md` §4 |
