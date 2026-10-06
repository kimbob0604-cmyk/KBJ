# P0 전 레포 grep 스윕 (독립 교차검증용)

- 작성: 2026-10-06, 방법: `rg`/`grep` 키워드 스윕 + 정의 위치 역추적(인벤토리 문서와 독립)
- 대상(읽기 전용 스냅샷): `/home/user/p0src/{stock-dashboard, etf-traker, gexlab}`
- 제외: `.env*`, `*.key`, `*secret*`, `*token*` 파일명, `node_modules`, `*.csv/*.parquet`. 대부분의 표는 `tests/`, `*.md`, 생성물(`etf-traker/docs/**`, `state/**`, `*.html/*.json`)을 빼고 **코드만** 센다.
- 표기: `레포:파일:줄 — 무엇`. 레포 약어 **SD**=stock-dashboard, **ET**=etf-traker, **GX**=gexlab. 값(키·토큰·계좌)은 적지 않는다. 추정은 [추정].

---

## A. KIS 토큰·접속키 발급/사용

### A-1. 발급 지점 (`/oauth2/tokenP`, `/oauth2/Approval`)

| 지점 | 무엇 | 캐시 |
|---|---|---|
| SD:kis_api.py:50 `_get_token()` → :64 `POST {KIS_BASE}/oauth2/tokenP` | 접근토큰 발급. 앱키 `KIS_APP_KEY`/`KIS_APP_SECRET`(:58-59). 만료 5분 전 갱신 | 메모리 `_token_cache`(:26) + 디스크 `cache/kis_token.json`(:27 `_TOKEN_FILE`, `_load_token_from_disk` :30, `_save_token_to_disk` :42) |
| SD:kis_api.py:82 `_headers(tr_id)` | 토큰+appkey/appsecret 헤더 구성 | — |
| ET:board/ingest/kis.py:103 `token(force=False)` → :112 `POST {base()}/oauth2/tokenP` | 접근토큰 발급. 1분 1회 제한 대비 캐시 | `board/state/.kis_token.json`(:44 `TOKEN_CACHE`, `_cached_token` :92, `_save_token` :124 원자적 쓰기 0600). `.gitignore`(board/.gitignore:14) |
| ET:board/ingest/kis.py:146 `_headers(tr_id)`, :167 `call()` | 헤더·공용 호출 | — |
| GX:data/kis/auth_client.py:165 `KisTokenIssuer.issue()` (:46 `TOKEN_PATH="/oauth2/tokenP"`) | 접근토큰 발급 | `FileTokenCache`(:245, 기본 `state/kis.token.json` :51) / `RedisTokenCache`(:326, 키 `kis:token`) / `FallbackTokenCache`(:391) |
| GX:data/kis/auth_client.py:436 `CachedTokenProvider`, :539 `default_token_provider()` | 캐시 우선·발급 간격(`claim_issue`) 직렬화 | Redis 우선, 실패 시 파일 [추정: :550 `Redis.from_url`] |
| GX:services/auth/service.py:465 `KisApprovalKeyIssuer.issue()` (:91 `APPROVAL_PATH="/oauth2/Approval"`) | **웹소켓 접속키** 발급(미실측 표시, :23) | Redis 키 `kis:ws_key`(:90) |
| GX:services/auth/service.py:186 `AuthService`, :364 `run_forever()` (30초 step), :531 `build_auth_service()` | 토큰·접속키 상시 갱신 서비스(진입점 `main` :652) | Redis `kis:token`, `kis:ws_key` |
| GX:services/auth/service.py:564 `reader()` | 다른 서비스는 **읽기만**(poller :227, scheduler/minute :342) | Redis |
| GX:config/settings.py:50 `kis_token_cache_path` | 파일 캐시 경로 설정 필드 | `state/kis.token.json` |

### A-2. `access_token` / `approval_key` 사용

| 지점 | 무엇 |
|---|---|
| SD:kis_api.py:69 | 응답에서 `access_token` 추출 |
| ET:board/ingest/kis.py:97, :117, :137 | 캐시 읽기·응답 추출·캐시 쓰기 |
| ET:board/ingest/http.py:39 | 로그 마스킹 키 목록(`access_token`, `appkey`, `appsecret` …) |
| GX:data/kis/auth_client.py:102-113, :159-162, :216-225 | `SecretStr` 모델·만료(`access_token_token_expired`) 파싱 |
| GX:services/ws_gateway/client.py:201 `build_message(cfg, approval_key, …)` | WS 등록/해제 메시지 헤더에 접속키 |
| GX:scripts/make_golden.py:914-915 | 골든 파일에서 토큰·접속키 패턴 제거 |

### A-3. 토큰 캐시 파일·키 정리

| 레포 | 경로/키 | 범위 |
|---|---|---|
| SD | `stock-dashboard/cache/kis_token.json` | Render 인스턴스·맥 각각 [추정: 재배포 시 소실] |
| ET | `etf-traker/board/state/.kis_token.json` | 로컬(launchd)·GitHub Actions 러너 각각(러너는 매 실행 새로 발급 [추정]) |
| GX | Redis `kis:token`, `kis:ws_key` / 파일 `gexlab/state/kis.token.json` | compose 내 공유 |

> 위험: 세 레포가 **같은 KIS 앱키**를 쓰면 발급 1분 1회 제한·초당 한도를 나눠 쓴다. GX:docs/phase1_design.md:92 에 "stock-dashboard(launchd 매일 18:00 잡)와 같은 키로 보인다"는 기록이 있다. 앱키는 SD(Render env), ET(GitHub Secrets `KIS_APP_KEY` 5개 워크플로 + `board/.env`), GX(`.env`, GitHub Secrets) 세 곳에 따로 있다.

---

## B. 외부 데이터 출처별 지점

### B-1. KIS 호출 지점 (파일별)

| 파일 | KIS 관련 줄 수* | 엔드포인트/TR |
|---|---|---|
| SD:kis_api.py | 8 | `inquire-time-itemchartprice`(분봉, `get_minute_chart` :155), `inquire-asking-price-exp-ccn`(호가 :210), `inquire-investor`(투자자 :256), `inquire-price`(현재가 :295), `domestic-futureoption/inquire-price`(K200 선물 :396). TR: FHKST01010100/0200/0900, FHKST03010200, FHMIF10000000. 자체 레이트리밋 18회/초(:100) |
| SD:server.py:13603, 16088, 16105, 16121, 16133 | 호출부 5 | `from kis_api import get_kospi200_futures / get_minute_chart / get_orderbook / get_investor_trading / get_price_detail` |
| SD:scripts/check_futures_us_index.py:137 | 1 | 점검 스크립트 |
| ET:board/ingest/kis.py | 15 | `inquire-investor-daily-by-market`(`market_flows` :214), `inquire-investor`(`stock_flows` :254), `foreign-institution-total`(`top_flows` :282). TR: FHPTJ04040000, FHKST01010900, FHPTJ04400000 |
| ET 호출부 | — | board/ingest/pipeline.py:479 `kis.market_flows`, board/ingest/stockflows.py:275 `kis.stock_flows`, monitor/kr/flows.py:150, monitor/flow/kissrc.py:27, board/tools/probe_kis_futures.py(FHKIF03020100, FHMIF10000000) |
| GX:data/kis/rest.py:98 `KisClient` | 8 | `inquire-time-fuopchartprice`(분봉 FHKIF03020200) |
| GX:services/poller/endpoints.py | 3 | FHPIF05030000 등 전광판 [추정: display-board 계열] |
| GX:services/poller/collector.py | 6 | 폴링 수집(KisClient) |
| GX:services/scheduler/minute.py | 2 | 분봉 일괄 적재(`MinuteLoader`) |
| GX:data/kis/ws.py, services/ws_gateway/*.py | 20+18 | 웹소켓 H0IFCNT0·H0MFCNT0·H0IOCNT0·H0EUCNT0(체결), `ws://ops.koreainvestment.com:21000`(config/kis_ws.yaml:32) |
| GX:data/kis/ratelimit.py | 17 | Redis 레이트리미터(앱키당 하나) |
| GX:scripts/probe_*.py | 다수 | display-board-callput/futures/option-list/top, inquire-investor-time-by-market 등(실측용) |

*`koreainvestment.com|/uapi/|tr_id|TR코드` 매치 줄 수(rg -c).

KIS 종목 마스터 zip(`new.real.download.dws.co.kr/.../fo_idx_code_mts.mst.zip`): SD:kis_api.py:361, ET:board/tools/probe_kis_futures.py:22, GX:scripts/probe_common.py:199 + GX scheduler 마스터 다운로더.

### B-2. 기타 출처 (코드 파일만, 괄호=매치 줄 수)

| 출처 | SD | ET | GX |
|---|---|---|---|
| **KRX OpenAPI** (`data-dbg.krx.co.kr`, `AUTH_KEY`) | krx_api.py(12), server.py(2) | board/ingest/krx.py(6), board/ingest/http.py, creds.py, pipeline.py(2), flowlab/probe_market_official.py(5), board/scripts/local_daily.sh | data/krx/eod.py(8), config/settings.py(3), services/scheduler/service.py(2), scripts/probe_all.py(2) |
| KRX 정보데이터시스템(`data.krx.co.kr` 크롤) | server.py(3) | flowlab/krx.py(4), monitor/flow/krx.py(6, 로그인 probe 포함), monitor/flow/capture.py, board/ingest/pipeline.py | — |
| **DART** (`opendart`) | dart_collector.py(7), overhang_parser.py(7), earnings_parser.py(3), server.py(20) | board/ingest/dart.py(13), financials.py(2), triggers.py(6), http.py(2), run.py(2), dart-report/dartreport/client.py(8), dart-report/app.py(4), monitor/kr/financials.py, monitor/kr/build.py | — |
| **네이버 금융** (`finance.naver`, `m.stock.naver`, `api.stock.naver`, `polling.finance.naver`) | server.py(31), consensus_collector.py, consensus_quarterly_collector.py, ohlcv_autofill.py, data_fetcher.py, theme_crawler.py(3), scripts/probe_naver_*.py | board/ingest/naver.py(15), flows.py(8), funds.py, stockflows.py, triggers.py, pipeline.py, etf_tracker_v9/{collectors,market,tracker,verify,render}.py, flowlab/naver.py + probe_*.py(다수) | — |
| 네이버 검색 OpenAPI (`openapi.naver`) | server.py(7), valuechain.py(5), agents/pipeline.py(5) | board/ingest/news.py(7), triggers.py(6), creds.py | — |
| 공공데이터포털 (`apis.data.go.kr`) | server.py(1, 문서성 [추정]) | board/ingest/datago.py, flowlab/probe_market_official.py, probe_market_unit2.py, monitor/kr/ingest.py | — |
| ECOS | — | monitor/bok/fetch-ecos.js(5), build.js | — |
| KOSIS | — | monitor/bok/audit.js(언급만) | — |
| KOFIA | **없음** (코드·문서 0건) | **없음** | **없음** |
| FRED | — | monitor/bok/fetch-fred.js(5), build.js | — |
| Yahoo/yfinance | server.py(78), data_fetcher.py(8), consensus_collector.py(9), tam_modeler.py(7), data_freshness.py, db_backup.py, scripts/check_futures_us_index.py | board/us/sources.py, board/tools/dashboard_brief_preview.py, monitor/bok/fetch-yahoo.js | — |
| Finnhub | server.py(23), valuechain.py(8), agents/pipeline.py(5) | — | — |
| pykrx | server.py(47), data_fetcher.py(14), ohlcv_autofill.py(9), ohlcv_5y_collector.py(3), krx_api.py, data_freshness.py | board/ingest/flows.py(2), flowlab/krx.py | — |
| 그 외 | `api.alternative.me`, CNN F&G(`production.dataviz.cnn.io`), `kr.investing.com`, `esignal.co.kr`, Wikipedia(server.py) | 운용사 ETF 사이트(etf_tracker_v9/adapters/*: ace·hanaro·koact·plus·rise 등), Google News(board/ingest/gnews.py), X/Bluesky(board/ingest/xsource.py·bsky.py), nasdaq·treasury·newyorkfed·faireconomy·stooq(board/us/sources.py, monitor/bok/fetch-rates.js·fetch-consensus.js), Anthropic(board/writer/claude.py 등) | GitHub(문서 링크만) |

---

## C. 텔레그램

### C-1. 발송 함수(Bot API 직접 호출)

| 지점 | 메서드 | 무엇 |
|---|---|---|
| SD:server.py:5205 `send_telegram()` (:5216) | sendMessage | 공용 발송(HTML). `TELEGRAM_ENABLED` 끄기 스위치(:5207) |
| SD:server.py:5273 `send_telegram_long()` | sendMessage(분할) | `_split_telegram_lines` :5236 |
| SD:server.py:5882 `_telegram_setup_webhook()` (:5891) | **setWebhook** | 부팅 시 자동 호출(:7024-7025, 토큰 있으면) |
| SD:earnings_telegram_sender.py:51 `send_telegram_message()` (:61) | sendMessage | 실적 알림 큐 발송(`send_pending_alerts` :201) |
| ET:board/report/telegram.py:779 `send()`, :822 `send_document()` | sendMessage, sendDocument | ET board 계열 공용 발송기(`_cred` :761) |
| ET:monitor/flow/telegram.py:36 `send_photos()` | sendMediaGroup | 수급 리포트 차트 4장 + 본문은 `TG.send` |
| ET:etf_tracker_v9/tracker.py:395 `send_telegram()`, :425 `send_telegram_file()`, :534 테스트 | sendMessage, sendDocument, getMe | ETF 일일 리포트(독립 구현) |
| ET:monitor/bok/send-telegram.js:34, :68 | sendMessage | 정책·수출 모니터 다이제스트(Node) |
| ET:board/ingest/tg_inbox.py:436 `drain()` (:131 `_call`, :153 `webhook_url`) | **getUpdates**, getWebhookInfo | 봇 대화 인박스 수집(트리거 소스) |
| GX:config/settings.py:29-30 | — | `telegram_bot_token`·`telegram_chat_id` 필드만. 발송 코드 없음(`services/notifier/__init__.py` 0줄) |

### C-2. 무엇을 언제 보내나 (호출부)

| 지점 | 내용 | 시점 |
|---|---|---|
| SD:server.py:6064 `alert_morning_briefing` (:6096) | 아침 브리핑 | 평일 08:30 |
| SD:server.py:5948 `alert_revision_signals` (:6046) | 컨센서스 리비전 | 평일 18:30 |
| SD:server.py:6248 `alert_watchlist_price` (:6276) | 관심종목 가격 | 평일 09-14시 :00/:30 |
| SD:server.py:6282 `alert_new_reports` (:6304) | 신규 리포트 | 평일 10:00 |
| SD:server.py:6490 `alert_closing_summary` (:6527) | 장마감 요약 | 평일 15:40 |
| SD:server.py:14696 `send_closing_market_summary` → :14583 `send_market_summary_telegram` (:14634, long) | 장마감 시황(신고가·수급 포함) | 평일 16:00 + `closing_brief_catchup` 16-20시 :05/:35 |
| SD:server.py:15196 `alert_flow_signals` (:15250) | 수급 시그널 | 평일 19:30 |
| SD:server.py:14554 `send_us_market_summary_telegram` (:14580) | 미국장 요약 | 화-토 06:10 |
| SD:server.py:6310 `alert_overnight_prediction` (:6396) | 야간 예측 | 화-토 05:30 |
| SD:server.py:6143 `alert_discovery_new_entries` (:6203, :6233) | 발굴 신규 진입 | [추정: stage2 스캔 후] |
| SD:server.py:5579 `check_alert_rules` (:5696), :5438 `_check_trailing_stops` (:5520) | 사용자 알림 규칙·트레일링 스톱 | 평일 9-15시 10분/30분 |
| SD:server.py:14989 `_watchdog_notify` (:14992) | 데이터 워치독 | 평일 8-20시 :00/:30 (`WATCHDOG_TELEGRAM`) |
| SD:server.py:7381 `_scheduled_universe_sync` (:7397) | 유니버스 동기화 결과 | 매월 1일 03:00 |
| SD:agents/pipeline.py:992, :1032 | 에이전트 결과 | 평일 08:45/15:45 [추정: `_auto_agent_run`] |
| SD:earnings_telegram_sender.py:148 | 실적 서프라이즈 알림 | 5분 간격(`_scheduled_earnings_pipeline` server.py:7485) |
| SD:server.py:4644, :5708, :5718 | 테스트 엔드포인트 `/api/test_telegram`, `/api/telegram/test`, `/api/telegram/briefing_test` | 수동 |
| ET:board/run.py:881 `cmd_send` (:930-992) | 시그널·백테스트·시스템조합 등 | board.yml/로컬 `--send` |
| ET:board/run.py:1000 `_send_note` (:1034), :1083 `_send_files` (:1122, :1126) | 신고가 보드 HTML·랭킹 엑셀 문서 | 로컬 launchd 16:10 → `--send files/note/rankings` |
| ET:board/run.py:1864 `cmd_us_send` (:1900, :1906) | 미국장 신고가 보드 | us-board.yml 07:00 KST |
| ET:board/run.py:1305 `cmd_xdigest_preview` (:1343), board/xdigest/send.py:159 `send_parts`, board/guru/pipeline.py:153 | X 다이제스트·구루 | xdigest.yml 10:23 KST (`XDIGEST_CHAT_ID`) |
| ET:monitor/flow/run.py:332, :354 | 수급 리포트(차트+본문) | flow.yml 18:17 KST |
| ET:etf_tracker_v9/tracker.py:417, :681, :697 | ETF 일일 리포트 | daily.yml 08:00/16:00 KST |
| ET:monitor/bok/send-telegram.js | 정책·수출 다이제스트(`--morning-only`) | bok.yml |
| ET:board/tools/dashboard_brief_preview.py:78 | 장마감 시황 섹션 미리보기 | 수동 워크플로 |

### C-3. 봇 명령 핸들러

| 지점 | 무엇 |
|---|---|
| SD:server.py:5860 `/api/telegram/webhook` → :5823 `_handle_telegram_command` | 명령: `도움/help/start/명령`, `시황/summary`, `시그널/수급시그널/signals`, `수급/flow`(`_tg_cmd_flow` :5787), `가격/시세/price`(`_tg_cmd_price` :5770). 소유자 chat_id + 시크릿 헤더 검증(:5731 `_telegram_secret`, :5865) |
| SD:server.py:5904 `/api/telegram/setup_webhook` | 웹훅 수동 재설정 |
| ET:board/ingest/tg_inbox.py | 명령이 아니라 **봇에 공유된 메시지 수집**(getUpdates, 화이트리스트 `TRIGGER_INBOX_CHAT_IDS`) |
| python-telegram-bot | **세 레포 모두 미사용**(전부 `requests`/`fetch` 직접 호출) |

> 충돌 [추정]: SD 는 부팅마다 `setWebhook` 을 건다. ET `tg_inbox` 는 웹훅이 걸려 있으면 getUpdates 가 409 로 거부된다고 스스로 검사한다(tg_inbox.py:153-155, :455). **두 레포가 같은 `TELEGRAM_BOT_TOKEN` 을 쓰면 ET 인박스가 막힌다.** 토큰 동일 여부는 값 비공개라 미확인.

---

## D. 스케줄

### D-1. SD APScheduler (server.py, `BackgroundScheduler` :70/:7069, Render `TZ=Asia/Seoul` render.yaml:11 → KST)

| 줄 | 잡 | 시각(KST) |
|---|---|---|
| 7072 | `_scheduled_update` (장중 갱신) | interval `interval_minutes`(설정값), `DISABLE_AUTO_FETCH` 면 끔 |
| 7079-7169 | 텔레그램 알림 잡(토큰 있을 때만, :7078) | 위 C-2 참조. `_refresh_flow_batch(200)` 평일 15:40, `_prewarm_new_highs` 15:48, `refresh_us_options_signal` 22:00, `_refresh_briefing_data` 화-토 05:00 |
| 7188-7196 | `_refresh_prices_from_naver` | 평일 9-15시 :05/:35, 15:35, 16-17시 5분 |
| 7208, 7211 | `_refresh_data_json_job` | 평일 15:45, 16:20 |
| 7226 | `_fill_ohlcv_job` | 평일 16:10 |
| 7254 | `_stage2_realtime_kr` | 평일 9-15시 5분 |
| 7272 | `_auto_stage2_scan` | 평일 08:00, 16:00 |
| 7294 | `_auto_agent_run` | 평일 08:45, 15:45 |
| 7300 | `mark_etf_stocks` | 매일 03:10 |
| 7303 | `generate_themes_mapping` | 일 03:20 |
| 7306 | `refresh_us_universe_if_stale` | 일 04:00 |
| 7314 | `poll_dart_disclosures` | 평일 8-17시 **매분** (DART 키 있을 때) |
| 7318 | `init_dart_corp_map_db` | 매일 03:00 |
| 7324 | `_self_keep_alive` | 4분 간격 |
| 7329 | `sync_us_market_to_db_from_cache` | 60분 간격 |
| 7335 | `_refresh_global_data_periodic` | 4시간 간격 |
| 7345 | `_daily_naver_universe_build` | 매일 08:00 |
| 7359 | `_daily_us_market_build` | 화-토 05:50 |
| 7376 | `_hourly_db_backup` (Gist) | 매시 :30 |
| 7402 | `_scheduled_universe_sync` | 매월 1일 03:00 |
| 7416 | `_scheduled_consensus_quarterly` | 월 06:00 |
| 7485 | `_scheduled_earnings_pipeline` | 5분 간격 |
| 7498 | `_scheduled_earnings_backfill` | 매일 06:30 |
| 7515 | `_scheduled_consensus_snapshot` | 평일 18:00 |
| 6867 `_price_broadcaster` | `while True` + sleep 3/10초 | 상시(가격 푸시) |

### D-2. GitHub Actions cron (UTC → KST 환산)

| 레포:워크플로 | cron(UTC) | KST | 무엇 |
|---|---|---|---|
| SD:wake.yml:21,23 | `50,55 6 * * 1-5`, `0,5,10,15,20 7 * * 1-5` | 평일 15:50~16:20 | Render 깨우기(장마감 시황 전) |
| SD: 나머지 7개 | 없음 | — | 수동(brief-kick, collect-diag, collector-test, naver/ohlcv/summary-probe, verify-deploy) |
| ET:board.yml:29-43 | **전부 주석 처리** | — | 보드는 로컬 launchd 16:10 이 만들고 Actions 는 `repository_dispatch`(board-daily/board-send)로 발송만 |
| ET:bok.yml:14-22 | `40 21 * * 0-4`, `30 0 * * 1-5`, `30 7 * * 1-5`, `0 13 * * 1-5` | 평일 06:40, 09:30, 16:30, 22:00 | 정책·수출 모니터(ECOS/FRED/Yahoo) + 텔레그램 |
| ET:daily.yml:19-20 | `0 23 * * 0-4`, `0 7 * * 1-5` | 평일 08:00, 16:00 | etf_tracker_v9 일일 리포트 |
| ET:flow.yml:17 | `17 9 * * 1-5` | 평일 18:17 | 수급 리포트(monitor.flow) |
| ET:kr.yml:20-22 | `30 0 * * 1-5`, `30 6 * * 1-5` | 평일 09:30, 15:30 | 섹터 모니터(monitor.kr) |
| ET:us-board.yml:15 | `0 22 * * 1-5` | 화-토 07:00 | 미국장 신고가 보드 |
| ET:xdigest.yml:38 | `23 1 * * *` | 매일 10:23 | X 다이제스트·구루 |
| ET:report.yml:6 | `0 0 16 2,5,8,11 *` | 2·5·8·11월 16일 09:00 | DART 재무 리포트 |
| ET:artifacts-gc.yml:14 | `0 3 * * 0` | 일 12:00 | 아티팩트 정리 |
| ET: 나머지 | 없음 | — | dashboard-brief-preview, flowlab, frgn-probe, kis-futures-probe, pages, preview, us-search |
| GX:ci.yml, probe.yml | 없음 | — | CI / 수동 probe |

### D-3. 로컬 cron·launchd·상시 루프

| 지점 | 시각(KST) | 무엇 |
|---|---|---|
| SD:scripts/daily_macbook_cron.sh:9 | 매일 18:00 (crontab 문서) | 맥 무거운 수집 → Gist 백업(:66) → `git push`(Render 배포 트리거) |
| ET:board/scripts/install_launchd.sh, local_daily.sh | 평일 16:10 (launchd) | 보드 생성 → `git commit/push` → 발송(`--send`) |
| ET:etf_tracker_v9/README.md:87, 운영가이드.md:271 | 08:00 / 09:10 (문서 예시) | etf_tracker run.sh crontab 예시 [추정: 현재는 daily.yml 이 대체] |
| ET:board/docs/LOCAL.md:123 | 16:10 (문서 예시) | `board/local.sh daily` crontab 예시 |
| GX:docs/runbook.md:25-41 | 일회성 | probe 예약 crontab(2026-09-29 전부 삭제됨) |
| GX:services/scheduler/service.py:422 | 1초 루프 | 세션 상태머신, PRE_DAY/PRE_NIGHT 마스터 갱신, 08:05 KRX 전일 적재, 16:00/06:10 분봉 적재, 08:48/18:03 개장 확인, 무결측 판정 |
| GX:services/auth/service.py:380 | 30초 루프 | 토큰·WS 접속키 갱신 |
| GX:services/poller/service.py:177, ws_gateway/service.py:596, client.py:396, recorder/service.py:166, engine/service.py:961 | 상시 | 폴링·WS·기록·엔진 루프(`while not stop.is_set()`) |
| GX:docs/phase5_8_design.md:15 | 5분 | systemd `gexlab-heal.timer`(설계) |
| `schedule` 라이브러리 | — | **세 레포 모두 미사용** |

---

## E. 저장

### E-1. DB 연결

| 레포 | 엔진 | 위치 |
|---|---|---|
| SD | SQLite `db/dashboard.db` | db/database.py:10 `DB_PATH`, :43/:55/:69 + 개별 모듈 직접 연결 다수(earnings_alert_writer.py:139, universe_manager.py:21, ohlcv_autofill.py:147, revision_calculator.py:90, valuechain.py:662, server.py:16885/17319/17809 등 **sqlite3.connect 총 54곳(scripts·migrations 포함)**) |
| SD | SQLite `db/llm_cache.db` | earnings_alert_writer.py:29 `CACHE_DB_PATH`, server.py:18801 |
| ET | SQLite `board/board.db` | board/run.py:47 (`BOARD_DB`), board/engine/db.py:88 `connect` |
| ET | SQLite `board/backtest.db`, `board/us_board.db` | board/run.py:529 (`BOARD_BACKTEST_DB`), :1750 (`US_DB`), board/us/db.py:57 |
| ET | SQLite `etf_tracker_v9/etf.db` | etf_tracker_v9/tracker.py:31 (Actions 캐시로 회차 간 보존 daily.yml:110) |
| ET | SQLite `flowlab/cache/backfill.db.demo`, `board/board.db.demo` | flowlab/backfill.py:20, demo.py:24 |
| GX | PostgreSQL+TimescaleDB (psycopg 3) | data/store.py:1196 `_connect_dsn`, db/migrate.py:119, `database_url`(settings.py:54) |
| GX | Redis | `redis_url`(settings.py:49), auth_client.py:550, auth/service.py:618 |
| SQLAlchemy `create_engine` | — | **세 레포 모두 미사용** |

### E-2. CREATE TABLE 이름 전부

| 레포:파일 | 테이블 |
|---|---|
| SD:db/schema.sql | alert_history, alert_rules, chart_cache, consensus_snapshot, dart_corp_map, disclosure_history, discover_results, financial, flow_cache, misc_cache, ohlcv, ops_state, revision_alerts, stocks, yinfo_cache |
| SD:migrations/004_step4_schema.py | alert_history_v, analysis_journal, consensus_estimate, dart_disclosure_overhang, fetch_progress, financial_quarterly, valuation_band |
| SD:migrations/005_step4_7_earnings.py | consensus_quarterly, earnings_actual, earnings_alert_queue, earnings_surprise, index_universe |
| SD:migrations/008_consensus_revision.py | consensus_snapshot, revision_alerts (schema.sql 과 중복 정의) |
| SD:server.py | recommendation_history, trade_journal |
| SD:earnings_alert_writer.py | llm_cache (llm_cache.db) |
| ET:board/engine/db.py | alltime, label, meta, px, run_log, sector_map, snap, split_check |
| ET:board/us/db.py | label, lowlabel, meta, px, run_log, snap |
| ET:etf_tracker_v9/tracker.py | change_log, etf_ticker, fund, holding |
| ET:etf_tracker_v9/market.py | etf_aum, etf_live, etf_meta, etf_px |
| GX:db/migrations/001_init.sql | raw_messages, fut_ticks, opt_ticks, chain_snapshots, fut_board, investor_flow, series_expiries, minute_bars, krx_fut_daily, krx_opt_daily, master_snapshots, session_log, health_events, collection_gaps, quarantine (전부 hypertable) |
| GX:db/migrations/002_collection_reports.sql | collection_reports |
| GX:db/migrations/003_engine.sql | levels, metrics, strike_gex, option_iv, oi_changes |
| GX:db/migrate.py | schema_migrations |

### E-3. JSON 상태 파일 쓰기 (대표 경로)

| 레포 | 경로(코드 근거) |
|---|---|
| SD | `data.json`, `cache/*.json`(server.py 63곳: `new_highs_{kr,us}_{날짜}`, `flow_{code}`, `naver_universe_*`, `us_market_*`, `macro_data`, `themes_mapping` …), `cache/kis_{key}.json`(kis_api.py:115 `_cache_path`), `server_portfolio.json`·`server_watchlist.json`·`alert_rules.json`(db_backup.py), `valuechain_map.json`(valuechain.py), `agent_result_*.json`(agents/pipeline.py) |
| ET | `board/state/<날짜>/{newhigh,universe,rankings,sectors,market,events,stockflows,flows,triggers,news,claims,facts,…}.json`(board/run.py, engine/build.py), `board/state/{signal_ledger,financials,inbox,.inbox_offset,.dart_corp,.kis_token}.json`, `xdigest-sent.json`·`guru-sent.json`, `etf_tracker_v9/base.json`, `monitor/bok/{data,ecos,fred,yahoo,rates,consensus,telegram-sent}.json`, `monitor/kr/cache/{flows,financials,themes,indices}.json`, `flowlab/*` |
| GX | `state/kis.token.json`, `state/spool/`(settings.py:57), scripts 산출물만 |

### E-4. 백업·영속화

| 지점 | 무엇 |
|---|---|
| SD:db_backup.py:180 `backup_db()`, :298 `restore_db()`, :245 `_find_backup_gist` | **Gist 백업/복원**(`api.github.com/gists`, 파일명 `dashboard_core.db.gz.b64` :55). 대상 `CORE_TABLES`(:57): trade_journal, recommendation_history, disclosure_history, alert_rules, analysis_journal, financial_quarterly, valuation_band, consensus_estimate, dart_disclosure_overhang, index_universe, consensus_quarterly, earnings_actual, earnings_surprise, earnings_alert_queue, consensus_snapshot, revision_alerts, ops_state. env `GITHUB_TOKEN`, `BACKUP_GIST_ID` |
| SD:server.py:4623 `/api/db/backup`, :4633 `/api/db/restore`, :6934 부팅 복원, :7376 매시 :30 | 호출 지점 |
| SD:scripts/daily_macbook_cron.sh:66 | 맥 cron 의 Gist 백업 |
| ET: 워크플로 `git push`(board, bok, flowlab, kr, us-board, xdigest), `actions/cache`(kr, report), `upload-artifact` | 상태를 레포 커밋·Actions 캐시로 보존 |

---

## F. 환경변수 이름 (값 없음)

| 레포 | 코드에서 읽는 이름 | 배포 설정에서만 보이는 이름 |
|---|---|---|
| SD | BACKUP_GIST_ID, DART_API_KEY, DISABLE_AUTO_FETCH, FINNHUB_API_KEY, GITHUB_TOKEN, GIT_COMMIT, HOST, KIS_APP_KEY, KIS_APP_SECRET, KRX_API_BASE, KRX_API_KEY, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET, PORT, RENDER, RENDER_EXTERNAL_URL, RENDER_GIT_BRANCH, RENDER_GIT_COMMIT, SERVER_NO_STARTUP, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_ENABLED, USE_SQLITE, WATCHDOG_TELEGRAM | render.yaml: PYTHON_VERSION, TZ / 워크플로 vars: DASHBOARD_URL |
| ET | ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, BOARD_BACKTEST_DB, BOARD_DB, BOARD_DEMO_SITE, BOARD_SITE, BSKY_APP_PASSWORD, BSKY_HANDLE, BSKY_PROBE_SPEC, DART_API_KEY, DASH_URL, DATAGO_KEY, ECOS_API_KEY, FRED_API_KEY, KIS_APP_KEY, KIS_APP_SECRET, KIS_ENV, KRX_API_KEY, KRX_ID, KRX_PW, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET, SD_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TRIGGER_INBOX_CHAT_IDS, US_BACKTEST_DB, US_DB, US_OUT, XDIGEST_CHAT_ID (로더: board/ingest/creds.py:57 `get`, `board/.env`) | 워크플로 vars: ACTION_PP, DART_STOCKS, KEEP_DAYS, KIS_ENV, MIN_FUNDS |
| GX | pydantic `Settings`(config/settings.py:16, `.env`): kis_app_key, kis_app_secret, kis_env, krx_api_key, krx_daily_call_cap, telegram_bot_token, telegram_chat_id, redis_url, kis_token_cache_path, database_url, spool_dir, spool_max_mb, live_trading → 환경변수 KIS_APP_KEY … LIVE_TRADING. 그 외 PROBE_OUT_DIR | docker-compose: POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, REDIS_URL, SPOOL_DIR, TZ / 워크플로 secrets: KIS_APP_KEY, KIS_APP_SECRET, KRX_API_KEY |

공통 이름(세 레포 이상 또는 두 레포): **KIS_APP_KEY, KIS_APP_SECRET, KRX_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID**(3레포), DART_API_KEY, NAVER_CLIENT_ID/SECRET(SD·ET), KIS_ENV(ET·GX).

---

## G. 레포 간 같은 이름 모듈·함수

### G-1. 모듈 파일명 겹침

| 이름 | 위치 |
|---|---|
| krx | ET:board/ingest/krx.py, ET:flowlab/krx.py, ET:monitor/flow/krx.py, GX:services/scheduler/krx.py, GX:data/krx/ (+ SD:krx_api.py) |
| kis | SD:kis_api.py, ET:board/ingest/kis.py, ET:monitor/flow/kissrc.py, GX:data/kis/ |
| flows / flow | ET:board/ingest/flows.py, ET:board/ingest/stockflows.py, ET:flowlab/flows.py, ET:monitor/kr/flows.py, GX:core/metrics/flow.py, GX:services/engine/flow.py (+ SD:server.py 내부 flow 함수군) |
| dart | SD:dart_collector.py, ET:board/ingest/dart.py, ET:dart-report/dartreport/client.py |
| telegram | ET:board/report/telegram.py, ET:monitor/flow/telegram.py, ET:monitor/bok/send-telegram.js, SD:earnings_telegram_sender.py (+ SD:server.py 내부) |
| pipeline | SD:agents/pipeline.py, ET:board/ingest/pipeline.py, ET:board/guru/pipeline.py, ET:board/us/pipeline.py |
| client | ET:dart-report/dartreport/client.py, GX:services/ws_gateway/client.py (이름만 같음) |
| config | ET:flowlab/config.py, ET:board/engine/config.py, GX:services/poller/config.py (이름만 같음) |
| financials | ET:board/ingest/financials.py, ET:monitor/kr/financials.py |
| calendar | GX:core/calendar.py 만 (다른 레포는 함수 수준) |

### G-2. 함수 이름 겹침(의미 있는 것)

| 함수 | 위치 |
|---|---|
| `now_kst` | SD:server.py:57, SD:data_fetcher.py:54, ET:board/engine/db.py:16, ET:board/engine/build.py:67, ET:board/xdigest/analyze.py:62, GX:scripts/probe_common.py:46 |
| `_headers`(KIS) | SD:kis_api.py:82, ET:board/ingest/kis.py:146 |
| `token` | ET:board/ingest/kis.py:103, GX:data/kis/rest.py:125, GX:data/kis/auth_client.py:112 |
| `send_telegram` | SD:server.py:5205, ET:etf_tracker_v9/tracker.py:395 |
| `_parse_sise`(네이버 시세) | SD:scripts/probe_ohlcv_sources.py:68, ET:board/ingest/naver.py:122 |
| `fetch_daily` | GX:data/krx/eod.py:38, ET:monitor/flow/krx.py:196, ET:monitor/flow/kissrc.py:48 |
| `prev_trading_day` / `trading_days` / 휴장 | GX:core/calendar.py:183(`exchange_calendars` XKRX + holidays_override.yaml), ET:etf_tracker_v9/tracker.py:566, ET:monitor/flow/narrative.py:73, ET:board/engine/db.py:202(px 날짜 합집합), ET:board/us/db.py:156, ET:etf_tracker_v9/market.py:147, GX:scripts/nogap_report.py:68, SD:server.py:17971 `_KR_HOLIDAYS_2026` 하드코딩 + :17987 `_is_kr_holiday` |
| 장중 판정 | SD:server.py:174 `is_market_hours`, SD:kis_api.py:146 `_is_kr_market_hours`, GX:core/calendar.py:279 `state_at` |
| 투자자 수급 | SD:kis_api.py:256 `get_investor_trading`, SD:server.py:2920 `_fetch_and_save_flow`·:3030 `_refresh_flow_batch`, ET:board/ingest/kis.py:214/254 `market_flows`/`stock_flows`, ET:board/ingest/flows.py:177/236 `fetch_market`/`fetch_stock`(네이버), ET:flowlab/naver.py:150 `investor_flows`, ET:monitor/kr/flows.py:137 `collect` |
| 신고가 | SD:server.py:10835 `_kr_new_highs_from_charts`·:10915·:10944 `_prewarm_new_highs`·:13315 `_newhigh_flow`, SD:data_fetcher.py:422 `fetch_new_highs`, ET:board/engine/newhigh.py(판정 엔진), ET:board/report/telegram.py:478-532 `_newhigh_*`, ET:flowlab/verify.py:132 `check_newhigh`, ET:monitor/flow/run.py:79 `latest_newhigh` |
| RSI/MACD/이평 | SD:server.py:368 `_calc_rsi_macd`, :542 `_calc_bollinger`, :11733 `_generate_macd_tags`, :12572 `_bt_calc_rsi`; ET:board/engine/systems.py:86 `rsi_arr`, ET:board/engine/signals.py:48 `sma` |
| DART 고유번호 | SD:dart_collector.py `get_corp_code`, SD:server.py:10267 `init_dart_corp_map_db`·:10453 `_load_dart_corp_code_map`, ET:board/ingest/dart.py:75 `corp_codes`, ET:dart-report/dartreport/client.py:129 `corp_code` |
| KRX OpenAPI 일별 | SD:krx_api.py `krx_api_call`·`krx_all_stocks_*`, ET:board/ingest/krx.py `fetch_day`·`fetch_index`, GX:data/krx/eod.py `fetch_daily`·`daily` |
| `read_cache`/`write_cache` | SD:data_fetcher.py:119/127, ET:flowlab/prices.py:34/48 |

---

## 레포 간 중복 후보

정본 지정 판단 재료. "정본 후보"는 grep 근거로 본 구현 성숙도 기준이며 최종 결정은 PLAN §2 충돌 지도에서 한다.

| # | 기능 | 중복 구현 | 충돌·위험 | 정본 후보 |
|---|---|---|---|---|
| 1 | **KIS 토큰 발급·캐시** | SD:kis_api.py `_get_token` / ET:board/ingest/kis.py `token` / GX:data/kis/auth_client.py + services/auth | 같은 앱키면 1분 1회 발급 제한·초당 한도 공유. 캐시 3벌(파일 2, Redis 1) | GX auth 서비스(Redis 공유 캐시·발급 직렬화·레이트리미터) [추정] |
| 2 | KIS 레이트리밋 | SD:kis_api.py:100(프로세스 내 18/초), GX:data/kis/ratelimit.py(Redis), ET 는 재시도만 [추정] | 프로세스 간 합산 안 됨 | GX RedisRateLimiter |
| 3 | KIS 투자자 수급(`inquire-investor`) | SD:kis_api.py:256, ET:board/ingest/kis.py:254 (+ 네이버 수급 SD:server.py:2920, ET:board/ingest/flows.py, flowlab/naver.py) | 같은 데이터 5경로, 단위·기준 차이 가능 | ET board/ingest 계열 [추정] |
| 4 | K200 선물 시세·마스터 zip | SD:kis_api.py:361/396, ET:board/tools/probe_kis_futures.py, GX scheduler 마스터 | 마스터 다운로드 3벌 | GX |
| 5 | **텔레그램 발송기** | SD:server.py `send_telegram`·earnings_telegram_sender.py, ET:board/report/telegram.py, ET:monitor/flow/telegram.py, ET:etf_tracker_v9/tracker.py, ET:monitor/bok/send-telegram.js (총 6벌) | 분할·이스케이프·끄기 스위치 규칙이 제각각 | ET:board/report/telegram.py(+sendMediaGroup 확장) [추정] |
| 6 | **텔레그램 수신(웹훅 vs getUpdates)** | SD:server.py:5882 setWebhook(부팅마다), ET:board/ingest/tg_inbox.py getUpdates | 같은 봇 토큰이면 상호 배타 → ET 인박스 실패 | 봇 분리 또는 한쪽으로 통일 필요 |
| 7 | 장마감 시황·신고가 브리핑 | SD:server.py:14696 `send_closing_market_summary`(16:00), ET:board/run.py `--send files/note`(16:10 로컬), ET:board/tools/dashboard_brief_preview.py | 같은 시간대 두 번 발송 가능 | ET board(신고가 엔진) + SD 시황 섹션 병합 [추정] |
| 8 | 신고가 판정 | SD:server.py:10835 등, SD:data_fetcher.py:422, ET:board/engine/newhigh.py | 판정 기준(종가/고가·기간) 차이 가능 | ET:board/engine/newhigh.py |
| 9 | 거래일·휴장 캘린더 | SD:server.py:17971 하드코딩(2026만), ET 각자(px 날짜·요일), GX:core/calendar.py(XKRX+override) | 2027년 SD 휴장 누락 위험 | GX:core/calendar.py |
| 10 | `now_kst` | 6곳 | 사소, 공용 util 로 | 공용 모듈 |
| 11 | KRX OpenAPI 클라이언트 | SD:krx_api.py, ET:board/ingest/krx.py, GX:data/krx/eod.py (+ 데이터포털 크롤 ET:flowlab/krx.py, monitor/flow/krx.py) | 같은 `KRX_API_KEY` 일일 호출 한도 공유(GX `krx_daily_call_cap`=200) | GX:data/krx/eod.py 또는 ET board [추정] |
| 12 | DART 고유번호·공시·재무 | SD:dart_collector.py·server.py `poll_dart_disclosures`(매분), ET:board/ingest/dart.py·financials.py, ET:dart-report, ET:monitor/kr/financials.py | 같은 `DART_API_KEY` 일 한도 공유, corp_code 캐시 3벌(SD DB `dart_corp_map`, ET `.dart_corp.json`, dart-report 캐시) | ET:board/ingest/dart.py [추정] |
| 13 | 네이버 시세·유니버스 | SD:server.py `_refresh_prices_from_naver`·`_daily_naver_universe_build`, ET:board/ingest/naver.py, ET:etf_tracker_v9, flowlab/naver.py | 크롤 부하·차단 위험 합산 | 하나로 |
| 14 | 기술지표(RSI/MACD/SMA) | SD:server.py:368·12572, ET:board/engine/systems.py:86·signals.py:48 | 계산식 차이 가능 | 공용 지표 모듈 |
| 15 | SQLite 테이블 `px`/`label`/`meta`/`snap`/`run_log` | ET:board/engine/db.py vs ET:board/us/db.py | 같은 레포 내 KR/US 스키마 쌍둥이 | 통합 시 시장 컬럼으로 [추정] |
| 16 | 상태 영속화 방식 | SD Gist+git push, ET git push+Actions 캐시, GX Postgres/Redis | 백업 경로 3벌 | PLAN 결정 사항 |
| 17 | 맥 로컬 정기 실행 | SD crontab 18:00, ET launchd 16:10, GX(문서상 systemd/compose) | 맥 잠자기 시 둘 다 누락, 18:00 은 GX 야간 전환 시각과 겹침(GX:docs/phase1_design.md:92) | 단일 스케줄러 |

### 미확인·후속 확인 필요
- 세 레포의 `KIS_APP_KEY`·`TELEGRAM_BOT_TOKEN` 이 실제로 같은 값인지(값 비공개, 소유자 확인 필요).
- SD Render 인스턴스의 `cache/kis_token.json` 재배포 후 소실 여부와 실제 발급 빈도.
- GX `services/poller/endpoints.py` 의 실제 사용 TR 전체(FHPIF05030000 외)는 코드 상세 확인 필요.
- KOFIA 는 세 레포 어디에도 없음 → PLAN P0 "KOFIA 실측"은 신규 작업.
