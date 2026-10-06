# 인벤토리 — etf-board

- 대상: `/home/user/p0src/etf-traker/board/` (하위 `board/us/` 포함) + `board` 코드를 실행하거나 board 캐시·state를 읽는 `.github/workflows/*.yml`
- 스냅샷 커밋: `0014f57` (2026-10-06). 읽기 전용으로 조사했고 소스는 수정하지 않았다.
- 표기: 경로는 `board/` 기준 상대경로(워크플로는 `.github/workflows/`). 코드를 직접 확인하지 못하고 추론한 내용은 **[추정]**으로 표시했다.
- `.env`, 토큰 캐시(`state/.kis_token.json`)는 열지 않았다. 환경변수는 이름만 적었다(`.env.example`도 변수 이름만 추출).

---

## 1. 개요

| 항목 | 내용 |
|---|---|
| 하는 일 | 국내 증시 장 마감 후 전 종목의 **60일 / 52주 / 역사적 신고가**와 신고가 근접 종목을 판정한다. 결과로 섹터·종목 랭킹, 히트맵, 종목별 수급·재료(트리거), LLM 장마감 코멘트 초안, 스윙 시그널을 만든다. 산출물은 정적 웹 보드(`docs/`), 엑셀, 텔레그램으로 나간다. 같은 신고가 엔진을 쓰는 **미국장 보드**(`board/us/`)와 아침 **글로벌 투자 구루 브리핑**(`guru/`, 구 X 다이제스트 `xdigest/`)이 함께 있다 (`CLAUDE.md` 1장, `README.md`) |
| 규모 (직접 셈) | 파일 256개(state 산출 43개 포함). Python 156개, 40,400줄. 이 가운데 운영 코드는 83개 파일 24,593줄이고 테스트는 73개 파일 15,807줄이다. 웹 정적 파일(js/css/html) 2,473줄, YAML 설정 956줄, `knowledge/` YAML 20,520줄(주로 `sector_map.yaml`), 문서 `docs/` 15개 6,696줄. `run.py` 하나가 2,345줄이다 |
| 미국장 부분 | `board/us/` Python 11개, 2,250줄. `us/ci-out/`에 CI 결과 txt/json 6개 |
| 테스트 | `tests/test_*.py` 71개 파일. **test 함수 1,319개**(AST로 셈). 스크래치 사본에서 `python3 -m unittest discover -s board/tests -t .`를 돌린 결과 **Ran 1319, OK (skipped=1)**, 약 18초. 네트워크 없이 돈다. 일부 테스트(`test_xdigest_schedule` 등)는 `.github/workflows/*.yml`을 읽으므로 레포 루트 구조가 필요하다 |
| 언어 | Python 3.11 이상(워크플로 3.11, 로컬 스크립트도 3.11 이상 강제). 화면은 바닐라 JS/CSS. 셸 스크립트 일부(bash, ps1) |
| 주요 의존성 | `board/requirements.txt`: requests, PyYAML, anthropic, openpyxl. 표준 라이브러리 sqlite3를 쓴다. 미리보기 워크플로만 yfinance를 쓴다 |
| 실행 방식 | **CLI**(`python3 -m board.run --<모드>`, 플래그 약 50개)가 중심이다. ① 맥 로컬 launchd가 평일 16:10에 `scripts/local_daily.sh`를 돈다. 2026-09-28부터 국장 daily의 주 경로다. ② GitHub Actions는 미국장, 구루 브리핑, 수동 모드를 맡는다. 국장 크론은 2026-09-29에 꺼졌다. ③ 로컬 HTTP 서버(`web/serve.py`, 127.0.0.1:8787)로 정적 사이트를 본다. 상시 서버는 없다 |
| 데이터 흐름 | 수집(`ingest/`) → sqlite(`board.db`) → 엔진(`engine/build.py`) → `state/YYYYMMDD/*.json` → 렌더(`web/`, `export/`, `report/`). 단계 사이는 JSON 파일로만 넘긴다 (`CLAUDE.md` 3장) |

---

## 2. 모듈 표

| 경로 | 역할 | 주요 함수·클래스 | 의존하는 외부 출처 |
|---|---|---|---|
| `run.py` | CLI 진입점. 모든 모드의 오케스트레이션을 맡는다(daily, init, send, us-*, xdigest-*, guru, probe 류) | `cmd_daily`, `cmd_init`, `cmd_send`, `_send_files`, `cmd_signals`, `cmd_backtest`, `cmd_screen`, `cmd_search`, `cmd_check`, `cmd_us_daily`, `cmd_us_send`, `cmd_verify_adjust`, `cmd_kis_probe`, `build_parser` | 모듈을 거쳐 전부 |
| `ingest/http.py` | 공통 HTTP 계층: 세션, 재시도, 병렬, 비밀값 스크럽 | `get(retries=3, backoff=0.8)`, `gather(workers=8)`, `scrub`, `why`, `Fetch` | — |
| `ingest/creds.py` | `.env`와 환경변수 로딩, 마스킹 | `load`, `get`, `has`, `mask`(앞 4자 노출), `status`, `KEYS` | — |
| `ingest/naver.py` | 기본 시세 소스: 전 종목, 일봉, 지수, 환율, 업종 | `fetch_universe`, `fetch_ohlcv`, `fetch_index`, `fetch_fx`, `fetch_sector_index`, `fetch_sector_members` | 네이버 금융(비공식 내부 API·HTML) |
| `ingest/krx.py` | KRX 오픈API 당일 확정 종가와 업종 | `fetch_day`, `fetch_index`, `sector_map`, `probe` | KRX 오픈API(`data-dbg.krx.co.kr`) |
| `ingest/datago.py` | 공공데이터포털 시세(교차검증용. `--check`, `--verify-adjust`에서만 쓴다) | `fetch_day`, `fetch_ohlcv`, `fetch_index` | 공공데이터포털 금융위 시세 |
| `ingest/kis.py` | KIS 투자자별 수급(시장, 종목, 기관·외국인 상위) | `token`, `_cached_token`, `_save_token`, `call`, `market_flows`, `stock_flows`, `top_flows`, `probe_market_params` | 한국투자증권 KIS Developers |
| `ingest/flows.py` | 네이버 투자자별 매매동향(KIS 폴백) | `fetch_market`, `fetch_stock`, `to_amount` | 네이버 증권 모바일 API |
| `ingest/stockflows.py` | 52주 이상 신고가 종목의 종목별 수급. KIS가 주 소스이고 네이버가 폴백이다(D-083) | `targets`, `collect`, `naver_flows`, `_kis_entry` | KIS, 네이버 |
| `ingest/funds.py` | ETF·ETN 판별과 제외 | `fetch_etf_codes`, `is_etn`, `split` | 네이버 etfItemList |
| `ingest/dart.py` | 공시, 기업개황, corp_code 매핑, 액면분할 공시 | `corp_codes`, `company`, `disclosures`, `disclosures_for`, `stock_actions` | DART OpenAPI |
| `ingest/financials.py` | DART 주요계정(히트맵 툴팁용 연간·분기 매출·영업이익) | `fetch_batch`, `collect`, `refresh`, `quarters_vs_dart` | DART `fnlttMultiAcnt` |
| `ingest/news.py` | 테마별 뉴스(제목·링크·요약만 저장) | `search`, `for_theme`, `collect`, `rank` | 네이버 검색 API |
| `ingest/gnews.py` | 종목 트리거의 2차 뉴스 소스 | `search`, `parse` | Google News RSS(비공식) |
| `ingest/triggers.py` | 신고가 종목별 재료(공시, 뉴스, X 인박스) | `collect`, `adopt`, `name_in`, `Budget`, `load`, `probe` | DART, 네이버 검색, Google News, 텔레그램 인박스 파일 |
| `ingest/tg_inbox.py` | 사용자가 봇에 공유한 X 링크를 `getUpdates`로 읽는다 | `drain`, `parse_update`, `oembed`, `merge`, `probe` | Telegram Bot API, publish.twitter.com oEmbed |
| `ingest/bsky.py` | 블루스카이 공개 검색(구 X 다이제스트 수집) | `collect`, `search`, `_login`, `call` | Bluesky AT Protocol |
| `ingest/xsource.py` | X 자동 수집 경로 실측 진단. 규약 위반 소지가 있어 수동으로만 돈다 | `probe_table`, `probe_bsky`, `parse_rss` | syndication.twitter.com, Nitter/RSSHub류, bsky |
| `ingest/pipeline.py` | 수집 → DB 적재(init, daily), 수정주가 의심 처리, 시장 데이터 | `init`, `daily`, `sync_universe`, `sync_px`, `sync_sectors`, `sync_market`, `_apply_krx_snapshot`, `confirm_suspects`, `repair_alltime`, `DATA_VERSION` | naver, krx, kis, flows, funds, dart |
| `engine/db.py` | sqlite 스키마와 접근 | `connect`, `series_for`, `snapshot`, `last_asof`, `log_step` | — |
| `engine/newhigh.py` | 신고가 판정 엔진(국장·미국장 공용) | `evaluate`, `split_guard`, `roll_alltime`, `continuity`, `proximity_kind`, `_gap`, `_resistance` | — |
| `engine/aggregate.py` | 업종·테마 롤업, 히트맵, 탐지기 | `rollup`, `by_sector`, `by_theme`, `heatmap`, `detect`, `breadth` | — |
| `engine/rankings.py` | 섹터·종목 랭킹(엑셀, 대시보드, 텔레그램이 공통으로 읽는 입력) | `sector_board`, `stock_board`, `mark_cross`, `build` | — |
| `engine/build.py` | DB → `state/YYYYMMDD/*.json` 오케스트레이션 | `run`, `achieved_rows`, `close_provenance`, `consistency_notes`, `unit_sanity` | — |
| `engine/facts.py` | LLM 입력용 사실 팩. 수치를 미리 문자열로 렌더한다 | `build`, `score_themes`, `pick_narrate` | — |
| `engine/themes.py`, `engine/kinds.py` | 테마 매핑, 보통주·우선주·스팩·리츠 구분 | `themes.build`, `near_names`, `kinds.of` | — |
| `engine/signals.py` | 스윙 시그널(돌파 확인·감시, 시장 게이트) | `build`, `levels`, `regime`, `squeeze_on`, `bb_width`, `true_range`, `percentile_ranks` | — |
| `engine/backtest.py`, `engine/systems.py`, `engine/search.py`, `engine/ledger.py` | 시그널 백테스트, 매매 시스템 점검, 조합 탐색(봉인 구간), 페이퍼 장부 | `simulate`, `stats`, `systems.run`, `rsi_arr`, `adx_arr`, `turtle_n`, `search.run`, `ledger.append/score` | (`--live` 때) 네이버 일봉 |
| `classify/sectors.py` | 종목 → 48섹터 배치 분류(LLM Batch) | `run`, `_run_batch`, `apply_to_db` | Anthropic API, DART(KSIC 근거) |
| `writer/` | 장마감 코멘트 생성과 검증 | `claude.ask/ask_many/ask_json`, `compose.run`, `prompts.system_blocks`, `verify.check` | Anthropic API |
| `report/telegram.py` | 텔레그램 메시지 조립과 발송 | `rankings_message`, `draft_message`, `send`, `send_document`, `check`, `_safe_err` | Telegram Bot API |
| `report/signals_tg.py`, `report/note.py` | 시그널 메시지, 사람이 쓴 일일 노트 메시지 | `message`, `note_messages`, `Resolver` | — |
| `export/excel.py` | `rankings.json` → xlsx | `build`, `from_state`, `_sector_sheet`, `_stock_sheet`, `_meta_sheet` | — |
| `web/app.py`, `payload.py`, `render.py`, `site.py`, `serve.py`, `artifact.py` | 고정 셸 + 데이터 JSON 구조, 날짜별 보관본, 로컬 서버, Claude 아티팩트 조각 | `app.build`, `payload.build`, `render.build`, `site.publish`, `serve.serve`, `artifact` 변환 | 폰트 CDN(jsdelivr Pretendard, Google Fonts) |
| `us/sources.py` | 미국 유니버스와 일봉 수집 | `screener`, `nasdaq_daily`, `stooq_daily`, `yahoo_daily`, `history`, `probe` | api.nasdaq.com, stooq, Yahoo chart |
| `us/pipeline.py`, `us/db.py`, `us/engine.py`, `us/build.py`, `us/brief.py`, `us/render.py`, `us/sectors.py`, `us/btsearch.py`, `us/demo.py` | 미국장 수집, DB, 엔진(신저가, 연속, 첫 진입), state 조립, 브리프, 화면, 규칙 섹터, 조합 탐색, 데모 | `us.engine.evaluate_all`, `streak_and_fresh`, `fresh52`, `brief.telegram_chunks`, `build.build` | 위 미국 소스 |
| `guru/` | 글로벌 구루 36명의 당일 발언 브리핑. Claude 웹 검색을 쓰고 코드가 출처를 대조한다 | `research.run`, `validate_people`, `synth.run`, `render.text`, `pipeline.build/send`, `over_budget` | Anthropic API(web_search) |
| `xdigest/` | 구 X(블루스카이) 24시간 다이제스트: 분석, 검증, 렌더, 발송 | `analyze.run`, `verify.check`, `render.compose`, `send.run` | Anthropic API, Bluesky |
| `tools/` | 실행 요약, 게시 전 점검, KIS 선물 측정, stock-dashboard 시황 미리보기 | `summary.render`, `artifact_check.check`, `probe_kis_futures`, `dashboard_brief_preview.build` | KIS, yfinance, stock-dashboard 레포 코드 |
| `scripts/` | 맥 로컬 daily와 launchd 설치 | `local_daily.sh`, `install_launchd.sh` | git push |
| `config/` | 임계값 YAML: `settings`, `score`, `signals`, `us`, `xdigest`, `guru` | — | — |
| `knowledge/` | 섹터 48개, 테마, 종목 → 섹터 맵, 이벤트, 노트, X 이름표 | — | — |

---

## 3. 외부 데이터 클라이언트 표

공통 사항: `ingest/http.py:get`이 기본 3회 재시도(선형 backoff 0.8초×i)와 timeout 25초를 적용하고, `gather`가 기본 8 worker로 병렬 수집한다. 예외 메시지에서는 `scrub`이 키를 지운다.

| 출처 | 파일 | 인증(환경변수 이름) | 호출 한도·재시도·캐시 처리 | KIS 토큰 직접 발급 |
|---|---|---|---|---|
| **KIS**(한국투자증권) | `ingest/kis.py`(`market_flows`, `stock_flows`, `top_flows`), `ingest/stockflows.py`, `tools/probe_kis_futures.py` | `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ENV`(real/vts) | 토큰은 `state/.kis_token.json`에 캐시한다(만료 10분 전까지 재사용, tmp 파일에 쓰고 원자적으로 교체, 권한 0600). `call(retries=2)`로 재시도하고, 빠진 필수 파라미터를 빈 값으로 채워 최대 6회 다시 부른다. `stockflows`는 첫 접속 실패에서 KIS 호출을 중단하고 네이버로 폴백한다(23시 전후 접속 불가, D-083). probe는 호출 사이에 0.4초 sleep | **예.** `ingest/kis.py:token()`이 `POST /oauth2/tokenP`로 발급하고 `_save_token`으로 저장한다. `tools/probe_kis_futures.py`는 `K._headers`를 거쳐 같은 함수를 쓴다 |
| **KRX** 오픈API | `ingest/krx.py`(`/sto/stk_bydd_trd`, `/sto/ksq_bydd_trd`, `/idx/krx_dd_trd`), `ingest/pipeline.py:_apply_krx_snapshot` | `KRX_API_KEY`(헤더 `AUTH_KEY`) | 하루치 전 종목을 1회 호출로 받는다. 당일 봉을 확정치로 덮어쓰고, 못 받으면 네이버 값에 '종가 잠정'을 붙인다(D-080). 공통 재시도 | 아니오 |
| **네이버 금융**(비공식) | `ingest/naver.py`, `ingest/flows.py`, `ingest/funds.py`, `ingest/stockflows.py:naver_flows`, `ingest/triggers.py`(종목 뉴스 URL) | 없음 | 일봉을 종목당 1회 부른다(하루 약 2,800회). `gather` 8 worker(업종 6, 재적재 4). 공통 재시도. 응답 키는 `pick()`으로 여러 후보를 조회한다 | 아니오 |
| **네이버 검색 API** | `ingest/news.py`, `ingest/triggers.py:_naver` | `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | 무료 일 25,000회(주석). 트리거는 `Budget`(budget_sec 240)과 호출당 timeout 8초, retries 1을 쓴다(`settings.yaml triggers`) | 아니오 |
| **DART** OpenAPI | `ingest/dart.py`, `ingest/financials.py`, `ingest/triggers.py:_dart`, `run.py:_dart_ksic`, `ingest/pipeline.py:confirm_suspects` | `DART_API_KEY`(쿼리 `crtfc_key`) | corpCode는 `state/.dart_corp.json`에 캐시한다. 재무는 `state/financials.json`에 종목별로 캐시하고 `stale_days`가 지나면 갱신하며, 100개 묶음을 3 worker로 받는다 | 아니오 |
| **공공데이터포털** | `ingest/datago.py` | `DATAGO_KEY`(쿼리 `serviceKey`) | `--check`, `--verify-adjust`의 교차검증에서만 쓴다. 일 트래픽 제한이 있다(주석) | 아니오 |
| **Google News RSS** | `ingest/gnews.py` | 없음 | 2차 소스다. 429나 consent 리다이렉트가 오면 `Fetch`를 올리고 그 소스를 접는다 | 아니오 |
| **Telegram Bot API** | `report/telegram.py`, `ingest/tg_inbox.py`, `xdigest/send.py`, `guru/pipeline.py` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `XDIGEST_CHAT_ID`, `TRIGGER_INBOX_CHAT_IDS` | 본문은 4,096자에서 분할하고 캡션 상한을 따로 둔다. 예외 문구는 `_safe_err`로 토큰을 스크럽한다. 인박스는 getUpdates 페이지마다 디스크에 먼저 저장하고 `.inbox_offset.json`을 남긴다 | 아니오 |
| **Anthropic**(Claude) | `writer/claude.py`, `classify/sectors.py`, `xdigest/analyze.py`, `guru/research.py`, `guru/synth.py` | `ANTHROPIC_API_KEY`(또는 `ANTHROPIC_AUTH_TOKEN`) | 프롬프트 캐시(system 접두사 `cache_control`), 분류는 Batch API. writer는 retries 2, max_tokens 16000. 구루 브리핑은 월 예산 `budget_usd_month`(기본 30달러)을 `state/guru/cost.json`으로 판정한다 | 아니오 |
| **Bluesky** | `ingest/bsky.py`, `ingest/xsource.py` | `BSKY_HANDLE`, `BSKY_APP_PASSWORD`(워크플로 주입. 코드는 `_login`) | 검색 호스트 두 곳(api/public)을 폴백한다. xsource는 후보당 1회, 순차, 1~5초 sleep, 429면 중단 | 아니오 |
| X 공개 경로(syndication, oEmbed) | `ingest/xsource.py`, `ingest/tg_inbox.py:oembed` | 없음 | 진단 전용이고 수동으로만 돈다. oEmbed는 인박스 본문을 보강하는 데만 쓴다 | 아니오 |
| **Nasdaq** 스크리너·일봉 | `us/sources.py:screener`, `nasdaq_daily` | 없음 | `config/us.yaml sources.history: nasdaq`, workers 4. 현재 주 소스다(`us/ci-out/check.txt`: PASS) | 아니오 |
| **stooq** | `us/sources.py:stooq_daily` | 없음 | 대체 어댑터. CI 실측 FAIL(0행, 데이터센터 IP 차단 [추정]) | 아니오 |
| **Yahoo** chart API / yfinance | `us/sources.py:yahoo_daily`, `tools/dashboard_brief_preview.py:raw_bars` | 없음 | 대체 어댑터. CI 실측 HTTP 429 | 아니오 |
| stock-dashboard 레포 코드 | `tools/dashboard_brief_preview.py` | KIS 키, 텔레그램 키(워크플로) | `server.py`에서 함수를 AST로 꺼내 `exec`한다. 그 안의 `kis_api`가 KIS를 부른다 | **[추정] 예** — stock-dashboard `kis_api`가 같은 앱키로 따로 토큰을 발급할 가능성이 있다(이 레포 밖 코드) |
| KIS 선물 마스터 zip | `tools/probe_kis_futures.py` | 없음(마스터 다운로드) | 일회성 측정 | (위 KIS 행과 같음) |
| ECOS, KOSIS, KOFIA, FRED, Finnhub, 운용사 | board 코드에는 **없음**. FRED와 ECOS는 `bok.yml`(monitor/bok, Node)에만 있어 이번 범위 밖이다 | — | — | — |

---

## 4. 정기 실행 표

| 시각(KST)·주기 | 작업 | 정의 위치 | 받는 데이터 | 산출물 |
|---|---|---|---|---|
| **평일 16:10** (맥이 깨어 있어야 돈다) | 국장 daily: `--test` → (DB 없으면 `--init`) → `--daily` → `--excel` → (`BOARD_SEND=1`이면 `--send draft`, `--send rankings`, `--send files --only-fresh --once --no-inbox`) → `--signals`(+`--send signals`) → `tools.artifact_check` → `docs` 커밋 후 `main`에 push | `scripts/install_launchd.sh`(launchd `com.etftraker.board-daily`), `scripts/local_daily.sh` | 네이버, KRX, KIS, DART, 네이버 검색, Google News, Anthropic, (인박스) | `board.db`, `state/YYYYMMDD/*.json`, `draft.md`, `docs/index.html`, `docs/api/latest.json`, `docs/d/<날짜>.html`, `docs/x/rankings-<날짜>.xlsx`, 텔레그램 |
| (비활성) 평일 16:07, 재시도 18:23·20:23, 주말 인박스 토·일 09:23·20:23 | 국장 daily와 발송 | `board.yml` `schedule` — **2026-09-29부터 전부 주석 처리** | — | — |
| 외부 스케줄러 호출 시(시각은 레포 밖 설정 [추정]) | `repository_dispatch` `board-daily`·`board-send` → **발송만** 한다(`--send files --only-fresh --once`) | `board.yml` + cron-job.org [추정] | `docs/` | 텔레그램 첨부 |
| 수동 | board 모드 약 28개(init, check, engine, render, write, news, stockflows, triggers, classify, reclassify-*, excel, signals, backtest, screen, search, inbox, verify-adjust, diagnose-banner, kis-probe, trigger-probe, x-probe, bsky-probe, send-note 등) | `board.yml` `workflow_dispatch` | 모드별 | Actions 캐시 `board-db-v<N>-<run_id>`(board.db + `board/state`), artifact `board-<run>`, docs 커밋 |
| (외부 루틴) **평일 17:09** | Claude 루틴 "국장 신고가 보드 — 아티팩트 갱신". 이 레포에 정의가 없고 `tools/artifact_check.py`와 `local_daily.sh` 주석에만 나온다 | 레포 밖 [추정] | `docs/api/latest.json` | Claude 아티팩트(`docs/artifact.html` 조각) |
| **화~토 07:00** (`0 22 * * 1-5` UTC) | 미국장 daily: `--us-check` → `--us-daily` → `--us-send files --only-fresh` | `.github/workflows/us-board.yml` | Nasdaq 스크리너와 일봉 | `us_board.db`(캐시), `state_us/YYYYMMDD/{universe,board}.json`, `docs/us/index.html`, `board/us/ci-out/*` 커밋, 텔레그램 브리프와 HTML 첨부 |
| 화~토 **07:45 / 08:15** (외부 dispatch `xdigest-daily`·`xdigest-send`) + 뒤받침 매일 **10:23** (`23 1 * * *` UTC) | 구루 브리핑: `--guru` → `--send guru --once`. 요일 판정 `run_weekdays: [1..5]`(화~토)은 코드가 한다 | `.github/workflows/xdigest.yml`, `config/guru.yaml` | Anthropic web_search | `state/guru/YYYYMMDD/{research,synth}.json`, `brief.txt`, `sent.json`, `docs/api/guru-sent.json`, 커밋, 텔레그램 |
| 평일 18:17 (`17 9 * * 1-5` UTC) | 수급 리포트(`monitor/flow`, board 범위 밖). board 캐시의 `newhigh.json`을 읽고 `DATA_VERSION`을 import한다 | `.github/workflows/flow.yml` | KRX(ID/PW), KIS(폴백) | 차트와 텔레그램 |
| 일 12:00 (`0 3 * * 0` UTC) | 아티팩트 정리(`board` 10개, `us-board` 20개 등만 남긴다) | `.github/workflows/artifacts-gc.yml` | GitHub API | — |
| push(특정 브랜치) 또는 수동 | flowlab 실데이터 검증과 `--financials --live`(DART) | `.github/workflows/flowlab.yml` | board 캐시, 네이버, DART | `flowlab/ci-out` 커밋 |
| 수동 전용 | `us-search.yml`(`--us-search`), `kis-futures-probe.yml`(`board.tools.probe_kis_futures`), `dashboard-brief-preview.yml`(stock-dashboard 체크아웃 → 시황 미리보기 텔레그램), `frgn-probe.yml`(board requirements만 설치하고 flowlab probe 실행) | 각 워크플로 | 각자 | 런 요약, 텔레그램 |
| (비활성) | GitHub Pages 배포. push 트리거가 주석 처리돼 있다(사용자 결정: 보드 비공개) | `.github/workflows/pages.yml` | — | — |

---

## 5. 텔레그램 표

모든 일반 발송은 `report/telegram.py:send`(sendMessage, 기본 `parse_mode=Markdown`)와 `send_document`(sendDocument)를 지난다. 연결 확인은 `check`(getMe, getChat)이다.

| 발송 함수 | 메시지 종류 | 발송 시각·조건 | 봇/채팅 환경변수 | 양방향 |
|---|---|---|---|---|
| `run.py:cmd_send('rankings')` → `TG.rankings_message` | 랭킹 요약: 결손 줄, 52주 이상 신고가와 종목별 수급·재료(트리거), 섹터 상위 5와 하위 3, 종목 교집합 | 평일 16:10 로컬 daily(`BOARD_SEND=1`), 수동 `send-text` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | — |
| `cmd_send('draft')` → `TG.draft_message` | LLM 장마감 코멘트 초안(`draft.md`, 검증 통과분만) | 위와 같은 시각. 실패해도 `|| true`로 넘어간다 | 같음 | — |
| `cmd_send('files')` → `_send_files` | 첨부: `신고가보드-<날짜>.html`(보관본), `rankings-<날짜>.xlsx`. 엑셀이 없으면 안내 문구를 보낸다 | daily 직후. `--only-fresh`면 기준일이 오늘일 때만 보낸다. `--once`면 `docs/api/sent.json` 표식이 있을 때 생략한다. 외부 dispatch로도 부른다 | 같음 | — |
| `cmd_send('signals')` → `report/signals_tg.message` | 스윙 시그널: 시장 게이트, 돌파 확인, 돌파 감시, 결손, 고지 | daily 끝(실패해도 경고만) | 같음 | — |
| `cmd_send('note')` → `report/note.py:note_messages` | 사람이 쓴 일일 노트(`notes/YYYY-MM-DD.yaml`) + 보드 수치. 이름을 하나라도 못 찾으면 전부 보내지 않는다 | 수동(`send-note`) | 같음 | — |
| `cmd_send('backtest' / 'screen' / 'search')` | 백테스트, 매매 시스템 점검, 조합 탐색 요약 | 수동 모드 | 같음 | — |
| `guru/pipeline.send` | 글로벌 투자 구루 브리핑(평문, 통 단위 재시도, 지문). `--once` 표식은 `docs/api/guru-sent.json` | 화~토 07:45 이후 / 08:15 / 뒤받침 10:23 | `TELEGRAM_BOT_TOKEN`, `XDIGEST_CHAT_ID`(없으면 `TELEGRAM_CHAT_ID`) | — |
| `xdigest/send.run`, `run.py:cmd_xdigest_preview(send=True)` | 구 X(블루스카이) 다이제스트, 미리보기 | 수동(구 모드만 남음) | 같음 | — |
| `run.py:cmd_us_send` → `us/brief.telegram_chunks` (HTML) + `send_document(docs/us/index.html)` | 간밤 미국장 브리프 ①~⑧와 화면 첨부 | 화~토 07:00 Actions. 기준일이 3일보다 오래됐으면 생략 | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | — |
| `tools/dashboard_brief_preview.main --send` | stock-dashboard 장마감 시황 일부(미국 지수, 코스피200 선물) 미리보기(HTML) | 수동 | 같음 | — |
| `ingest/tg_inbox.drain` (`run.py:cmd_inbox`) | **수신**: 사용자가 X 앱에서 봇 대화로 공유한 게시물을 `getUpdates`로 읽어 `state/inbox.json`에 14일치 쌓는다. 트리거 줄에 `↳ X:`로 붙인다 | daily 맨 앞과 `--send files` 앞. **기본 꺼짐**(`settings.yaml triggers.inbox_enabled: false`). 웹훅이 걸려 있으면 409로 실패한다 | `TELEGRAM_BOT_TOKEN`, `TRIGGER_INBOX_CHAT_IDS`(없으면 `TELEGRAM_CHAT_ID`) | **예(수신 전용)**. `/명령` 처리는 없다. 링크만 수집한다 |
| (같은 레포의 다른 프로젝트) | bok, flow, daily(etf_tracker_v9) 워크플로도 **같은 봇과 채팅 시크릿**으로 보낸다 | 범위 밖 | 같음 | — |

---

## 6. 저장 표

| DB 종류·파일 | 테이블 / 파일 | 주요 컬럼·내용 | 쓰는 쪽 → 읽는 쪽 |
|---|---|---|---|
| sqlite `board/board.db`(`BOARD_DB`). Actions 캐시로 옮기고 레포에는 넣지 않는다 | `px` | code, asof, OHLCV, source(PK code+asof). 최근 KEEP_DAYS(약 420일)만 남긴다 | `ingest/pipeline` → `engine/*`, `signals`, `backtest` |
| | `snap` | code, asof, name, market, close, chg_pct, volume, turnover, mktcap, turnover_is_estimate, source | pipeline → build, rankings |
| | `alltime` | code, hi/hi_date, cl/cl_date, prev_hi, prev_cl, first/last_date, n_days, suspect, suspect_date/note | pipeline(`roll_alltime`, `repair_alltime`) → newhigh |
| | `label` | code, asof, basis(high/close), kind(hist/w52/d60), rank | build → 신규/이어감 판정 |
| | `sector_map` | code, sector, taxonomy | classify, `restore_sectors` → aggregate, rankings |
| | `split_check` | code, jump_date, verdict(action/none/unknown), note | `confirm_suspects`(DART) → newhigh 가드 |
| | `meta`, `run_log` | DATA_VERSION 등 k/v. asof, step, ok, note, ts | 전 단계 → 결손 표시, `stale_reason` |
| sqlite `board/backtest.db`(`BOARD_BACKTEST_DB`) | `board.db`와 같은 스키마 [추정] | 긴 일봉 이력 | `--backtest/--screen/--search --live` |
| sqlite `board/us_board.db`(`US_DB`) | `px`(ticker), `snap`(+exchange, sector, industry, *_raw), `label`, `lowlabel`(52주 신저가), `meta`, `run_log` | 위와 같은 규약 | `us/pipeline` → `us/engine`, `us/build` |
| sqlite `us_backtest.db`(`US_BACKTEST_DB`) | 미국 탐색용 | | `us/btsearch` |
| JSON `state/YYYYMMDD/` (git 제외, 캐시로 옮긴다) | `market`, `universe`, `sectors`, `newhigh`, `events`, `rankings`, `news`, `stockflows`, `triggers`, `index_hist`, `signal_flows`, `signals`.json, `draft.md`, `claims.json` | 단계 산출물. 모든 수치에 source와 as_of를 붙인다 | engine, ingest → web, export, report, writer, flow, flowlab |
| JSON `state/` 루트 | `signal_ledger.json`, `inbox.json`, `.inbox_offset.json`, `financials.json`, `.dart_corp.json`, **`.kis_token.json`**, `backtest.json`, `search.json`, `screen.json` | 페이퍼 장부, 인박스, 캐시, 토큰 | 각 모듈. `board/state` 디렉터리 전체가 Actions 캐시로 저장된다 |
| JSON `state/guru/`, `state/xdigest/` (**커밋됨**) | `YYYYMMDD/research.json`, `synth.json`, `brief.txt`, `sent.json`, `cost.json` / `posts.json`, `themes.json`, `facts.json`, `digest.txt` | 조사 결과, 월 비용, 블루스카이 게시물 원문 | guru, xdigest → send |
| JSON `state_us/YYYYMMDD/` | `universe.json`, `board.json`, `search.json` | 미국장 단계 산출물 | `us/build` → `us/brief`, `us/render` |
| 정적 사이트 `docs/`(레포 루트, `BOARD_SITE`) | `index.html`(데이터가 0인 셸), `api/latest.json`, `api/d/<날짜>.json`, `api/index.json`, `d/<날짜>.html`, `d/index.json`, `x/rankings-<날짜>.xlsx`, `artifact.html`, `api/sent.json`, `api/guru-sent.json`, `api/xdigest-sent.json`, `us/index.html` | 화면, 보관본, 발송 표식 | `web/site.publish` → 브라우저, 텔레그램 첨부, Claude 아티팩트 |
| YAML `knowledge/` | `sector_map.yaml`(종목 → 섹터, **커밋됨**), `sectors.yaml`(48섹터), `themes.yaml`, `events.yaml`, `notes.yaml`, `xdigest_names.yaml`, `us/knowledge/us_sectors.yaml`, `us_overrides.yaml` | 분류 사전 | classify(쓰기) → engine, aggregate |
| YAML `notes/YYYY-MM-DD.yaml` | 사람이 쓴 일일 노트 | 제목, 사실, 판단 | 사용자 → `report/note.py` |

---

## 7. 환경변수 이름 목록

| 이름 | 용도 | 사용 파일 |
|---|---|---|
| `KIS_APP_KEY`, `KIS_APP_SECRET` | KIS 앱키, 시크릿(조회 전용 사용) | `ingest/kis.py`, `ingest/creds.py`, `tools/probe_kis_futures.py`, `board.yml`, `kis-futures-probe.yml`, `dashboard-brief-preview.yml` |
| `KIS_ENV` | real/vts 서버 선택 | `ingest/kis.py:base` |
| `KIS_ACCOUNT` | `.env.example`에만 있고 코드에서는 쓰지 않는다 | `.env.example` |
| `KRX_API_KEY` | KRX 오픈API(`AUTH_KEY` 헤더) | `ingest/krx.py`, `ingest/pipeline.py`, `frgn-probe.yml` |
| `DART_API_KEY` | DART OpenAPI | `ingest/dart.py`, `ingest/financials.py`, `flowlab.yml` |
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | 네이버 검색 API | `ingest/news.py`, `ingest/triggers.py` |
| `DATAGO_KEY` | 공공데이터포털 인증키(Decoding 키) | `ingest/datago.py`, `run.py` |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` | Claude 호출. 없으면 daily가 서술을 건너뛴다 | `writer/claude.py`, `run.py:cmd_daily`, `xdigest.yml` |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | 봇 토큰, 기본 대화방 | `report/telegram.py`, `ingest/tg_inbox.py`, `xdigest/send.py`, 각 워크플로 |
| `XDIGEST_CHAT_ID` | 구루·다이제스트 대화방 | `xdigest/send.py`, `guru/pipeline.py`, `xdigest.yml` |
| `TRIGGER_INBOX_CHAT_IDS` | 인박스로 읽을 대화방(쉼표 구분) | `ingest/tg_inbox.py` |
| `BSKY_HANDLE`, `BSKY_APP_PASSWORD` | 블루스카이 로그인(검색) | `ingest/bsky.py`, `board.yml`, `xdigest.yml` |
| `BSKY_PROBE_SPEC` | bsky-probe 후보 질의 | `ingest/xsource.py` |
| `NOTION_TOKEN`, `NOTION_DATABASE_ID` | `.env.example`에만 있다. **노션 발송은 코드에 없다**(미구현) | `.env.example` |
| `BOARD_DB`, `BOARD_BACKTEST_DB`, `BOARD_SITE`, `BOARD_DEMO_SITE` | 경로 오버라이드 | `run.py`, `xdigest/send.py` |
| `US_DB`, `US_OUT`, `US_BACKTEST_DB` | 미국장 경로 | `run.py`, `us/btsearch.py` |
| `BOARD_SEND` | 로컬 daily 텔레그램 발송 on/off(기본 0) | `scripts/local_daily.sh`, `install_launchd.sh` |
| `REPO` | 로컬 스크립트 레포 경로 | `scripts/local_daily.sh` |
| `SD_DIR` | stock-dashboard 체크아웃 경로 | `tools/dashboard_brief_preview.py` |
| (워크플로 전용, board 밖) `KRX_ID`, `KRX_PW`, `ECOS_API_KEY`, `FRED_API_KEY`, `MIN_FUNDS`, `ACTION_PP`, `KEEP_DAYS` | monitor/flow, bok, etf_tracker | `flow.yml`, `bok.yml`, `daily.yml` |

---

## 8. 기능 목록 (사용자에게 보이는 것)

| 구분 | 기능 | 근거 |
|---|---|---|
| 웹 화면(`docs/index.html` + `api/latest.json`) | 탭 6개: **신고가**(달성·근접, 종가/고가 기준 토글, 신규/이어감, 갭, 5일 축소폭, 저항두께, 거래량 배수, 종목 종류 필터, 종목별 수급 셀, 재료 태그), **섹터 랭킹**(48섹터 금일·7거래일 순위 + 1~5등), **종목 랭킹**(기간수익률 순, 거래량 순, 교집합), **히트맵**(크기·색상·그룹 컨트롤, 재무 툴팁), **섹터 뉴스**, **일간 코멘트** | `web/app.js:741` TABS, `web/render.py`, `web/payload.py` |
| 웹 화면 공통 | 헤더 지수·수급·환율, 결손 배너, '종가 잠정' 배너, 날짜 선택기(보관본), 엑셀 다운로드 링크 | `web/render.py:_banner/_downloads`, `web/site.py` |
| 미국장 화면 | `docs/us/index.html`: 52주 신고가·신저가, 연속 신고가, 첫 진입, 섹터 18개 표, 시총 구간, 메가캡 무버 | `us/render.py`, `us/engine.py` |
| 로컬 서버 | `--serve` → http://127.0.0.1:8787 | `web/serve.py`, `local.sh` |
| Claude 아티팩트 | `docs/artifact.html` 조각(CSP 대응 폰트 교체) | `web/artifact.py` |
| 엑셀 | `rankings-<날짜>.xlsx`: 섹터 시트(금일, 7일), 종목 시트, 메타 시트. 숫자 셀은 float이고 표시는 number_format으로 한다 | `export/excel.py` |
| 텔레그램 리포트 | 랭킹 요약, 코멘트 초안, HTML·엑셀 첨부, 스윙 시그널, 일일 노트, 백테스트·시스템·탐색 요약, 미국장 브리프, 구루 브리핑 | 5장 |
| LLM 코멘트 | 장 흐름 + 테마별(4~6개) + 캘린더 섹션. 수치·종목명 기계 검증을 통과한 섹션만 싣고, 내일 검증할 `claims.json`을 남긴다 | `writer/compose.py`, `writer/verify.py` |
| 운영 리포트 | GitHub Actions 런 요약(마크다운 표), 게시 전 점검 | `tools/summary.py`, `tools/artifact_check.py` |
| 진단 CLI | `--check`(소스·키 점검), `--verify-adjust`(수정주가 교차검증), `--diagnose-banner`, `--kis-probe`, `--trigger-probe`, `--x-probe`, `--bsky-probe`, `--xdigest-check`, `--us-check`, `--us-verify` | `run.py` |
| 데모 | `--demo`, `--us-demo`(합성 데이터. 화면 상단에 '가짜' 표기) | `tests/demo.py`, `us/demo.py` |
| HTTP API 엔드포인트 | **없음.** 정적 JSON 파일(`docs/api/*.json`)만 있다 | `web/site.py` |

---

## 9. 계산 엔진·지표

| 계산 | 함수 | 정의·비고 | 테스트 |
|---|---|---|---|
| 60일 / 52주 / 역사적 신고가(종가·고가 기준 각각) | `engine/newhigh.py:evaluate`, `roll_alltime`, `hist_ref_for` | 당일을 빼고 직전 60·252영업일. 역사적 신고가는 `alltime.prev_hi`와 비교한다. 라벨 우선순위는 hist > w52 > d60 | 있음 `test_newhigh.py`, `test_basis.py`, `test_init_rebuild.py`, `test_stale_px.py` |
| 수정주가 미반영 가드 | `split_guard`(±31% 점프), `pipeline.confirm_suspects`(DART 공시 대조) | 점프가 있으면 역사적 판정에서 빼고 룩백을 절단한다(D-001, D-056) | 있음 `test_newhigh.py`, `test_dart_for.py` |
| 근접 / 갭 / 5일 축소폭 | `newhigh._gap`, `proximity_kind`, `continuity` | 갭 5% 이내, 시총 1,000억 이상 | 있음 `test_newhigh.py`, `test_mktcap_floor.py` |
| 저항두께, 거래량 배수 | `newhigh._resistance`, `_avg_vol`, `build._vol_ratio`, `_turnover_avg20` | 누적거래량 ÷ 20일 평균. 20일 평균은 당일을 뺀다 | 있음 `test_newhigh.py`, `test_turnover.py` |
| 업종·테마 등락률(시총 가중), 브레드스, 히트맵 | `engine/aggregate.py:_wavg`, `rollup`, `breadth`, `heatmap` | D-004 | 일부 `test_mktcap_floor.py` |
| 탐지기 | `aggregate.detect` | **구현 5개**: material_giveback, volume_anomaly, multi_label_high, proximity_cluster, breakout_fail. **미구현 5개**: valuechain_diffusion, opposite_reaction, flow_alignment, claim_check, sector_rotation(`CLAUDE.md` 6장 대비) | 간접 |
| 섹터·종목 랭킹, 교집합, 순위 변동 | `engine/rankings.py` | 금일·7일 수익률, 거래량 3일·1달 순 | 있음 `test_rankings.py`, `test_banner.py` |
| 종가 출처·단위 검사 | `build.close_provenance`, `unit_sanity`, `consistency_notes` | KRX 확정치 / 네이버 잠정 | 있음 `test_close_source.py`, `test_units.py`, `test_consistency.py` |
| 수급(시장·종목) | `kis.market_flows/stock_flows`, `flows.fetch_market/fetch_stock`, `to_amount` | 네이버 종목 수급은 주수 × 종가라 `is_estimate`를 붙인다 | 있음 `test_kis_call.py`, `test_stockflows.py`, `test_market_flows.py`, `test_flows_parse.py` |
| 스윙 시그널 | `engine/signals.py`: `sma`, `pstdev`, `true_range`, `bb_width`, `squeeze_on`, `is_nr`, `is_inside`, `ma_stack`, `contraction_at`, `percentile_ranks`(RS), `regime`(시장 게이트), `levels`(돌파가·손절·목표·비중) | `config/signals.yaml`. 성과가 검증되지 않았다는 사실을 메시지에 명시한다 | 있음 `test_signals.py` |
| 백테스트, 시스템 점검, 조합 탐색, 페이퍼 장부 | `backtest.simulate/stats`, `systems`(`ema_arr`, `rsi_arr`, `adx_arr`, `turtle_n`), `search.run`(봉인 구간), `ledger.score` | 미래참조·생존편향 규칙을 주석에 적었다 | 있음 `test_backtest.py`, `test_systems.py`, `test_search.py`, `test_ledger.py` |
| 미국장: 52주 신저가, 연속 신고가, 첫 진입, 시총 구간, 섹터표 | `us/engine.py` | 신고가 판정은 국장 엔진을 공유한다 | 있음 `test_us_engine.py`, `test_us_sectors.py`, `test_us_brief.py`, `test_us_search.py` |
| 테마 스코어(서술 대상 선정) | `engine/facts.py:score_themes`, `pick_narrate` | z-score + 신고가 수 + 거래대금 증가율 등(`config/score.yaml`) | 간접 `test_news_narrative.py`, `test_reader_voice.py` |
| LLM 출력 검증 | `writer/verify.py:check`, `xdigest/verify.py`, `guru/research.validate_people`, `guru/synth.stray_numbers` | 종목명·수치 토큰을 정확히 대조하고, 출처 URL을 검색 결과와 대조한다 | 있음 `test_verify.py`, `test_xdigest_verify.py`, `test_guru.py` |
| 재무(분기 차분) | `ingest/financials.py:_cum`, `quarters_vs_dart` | Q2 = 반기 − 1Q 등 | 있음 `test_financials.py` |
| 밸류에이션(PER, PBR 등) | **없음** | — | — |
| 엑셀 출력 | `export/excel.py` | — | **없음**(직접 테스트하는 파일이 없다) |

---

## 10. 품질·위험 메모

| # | 구분 | 내용 | 근거 |
|---|---|---|---|
| 1 | 키 노출 위험 | `--check`가 각 키의 **앞 4자**를 출력한다(`creds.mask`). `board.yml`의 check 모드는 이 출력을 `/tmp/check.txt`와 런 요약에 남긴다. 공개 레포 Actions 로그에서는 일부 노출이 된다. 로컬 스크립트는 이 이유로 `--check`를 부르지 않는다 | `ingest/creds.py:mask`, `run.py:68`, `scripts/local_daily.sh` 주석 |
| 2 | 키 노출 위험 | `board.yml`이 `board/state` 디렉터리 전체를 Actions 캐시에 저장한다. 그 안에 **KIS 접근토큰 캐시 `state/.kis_token.json`**과 사용자 텔레그램 인박스(`inbox.json`)가 들어간다. 레포를 공개하면 캐시 접근 범위를 다시 검토해야 한다 [추정: 포크 PR 캐시 접근 가능성] | `board.yml` 'DB·state 저장', `ingest/kis.py:TOKEN_CACHE` |
| 3 | KIS 토큰 경합 | 같은 앱키로 토큰을 받는 곳이 여럿이다: `board/ingest/kis.py`, `monitor/flow`(flow.yml), `monitor/kr`(kr.yml), stock-dashboard `kis_api`(dashboard-brief-preview). KIS 발급은 1분에 1회로 제한된다. 캐시 파일도 각자 따로 둔다 [추정] | 각 워크플로 env, `ingest/kis.py` 주석 |
| 4 | 원격 코드 실행 | `tools/dashboard_brief_preview.py`가 다른 레포(stock-dashboard)의 `server.py` 일부를 AST로 꺼내 KIS·텔레그램 시크릿이 있는 러너에서 `exec`한다 | `tools/dashboard_brief_preview.py:build` |
| 5 | 스크래핑·약관 | 네이버 금융 내부 API·HTML(시세, 업종, 수급, ETF 목록), Google News RSS, X syndication/Nitter 진단(코드 주석이 스스로 **X 약관 스크래핑 금지 조항 위반**이라고 적었다), Yahoo chart. 공개 레포로 옮기면 비공식 엔드포인트와 우회 코드의 공개가 문제가 된다 | `ingest/naver.py`, `flows.py`, `funds.py`, `gnews.py`, `xsource.py` docstring |
| 6 | 실데이터 fixture·개인 데이터 | `tests/fixtures/xdigest_render_posts.json`과 커밋된 `state/xdigest/*/posts.json`에 **실제 블루스카이 계정 핸들과 게시물 원문**이 있다. `state/guru/*`(조사 결과·비용), `notes/2026-09-21.yaml`(사용자의 판단 노트), `docs/reference-comment.md`(사용자 원고), `tests/fixtures/rankings_sample.json`(실제 종목명과 날짜, 수치 진위 [추정])도 커밋돼 있다 | 해당 파일 |
| 7 | gitignore 예외 | `board/.gitignore`는 `state/*/`를 제외하면서 `!state/xdigest/`만 예외로 둔다. 그런데 `state/guru/`도 스냅샷에 있다. 워크플로가 강제로 add한 것으로 보인다 [추정]. 루트 `.gitignore`는 `*.db`나 토큰 캐시를 다루지 않고 `board/.gitignore`에만 의존한다 | `board/.gitignore`, `.gitignore` |
| 8 | 문서와 코드 불일치 | ① `CLAUDE.md`는 "18:00 발송, 17:15 초안"이라 적었지만 실제는 16:10 로컬 실행 후 즉시 발송이다. ② `engine/newhigh.py` docstring은 "리포트 기본값은 고가 기준"이지만 `CLAUDE.md`와 `settings.yaml`의 `default_basis`는 `close`다. ③ 탐지기 10개 가운데 5개만 구현돼 있다. ④ `config/guru.yaml send_at: '06:40'`이지만 실제 dispatch는 07:45와 08:15다. ⑤ README의 "섹터 뉴스 미구현" 문구가 아직 남아 있다 | 각 파일 |
| 9 | 운영 의존성 | 국장 daily가 **사용자 맥 launchd**에 의존한다. 맥이 잠들면 실행을 놓친다. Actions 크론은 무료 분이 소진돼 꺼져 있다. 발송 정시는 외부 cron-job.org가 맞추는데 그 설정은 레포 밖이다. 17:09 Claude 루틴도 레포 밖이다 | `board.yml` 주석, `scripts/install_launchd.sh` |
| 10 | 하드코딩 | 실행 브랜치 `claude/52-week-high-dashboard-9bedkz`(flowlab, frgn-probe 트리거), `git push origin HEAD:main`(로컬), 레포 이름 `kimbob0604-cmyk/stock-dashboard`, 유예 날짜 `UNTIL: '2026-10-01'`(board.yml), launchd 라벨 `com.etftraker.board-daily`, KIS tr_id·path 표(`ENDPOINTS`). 임계값은 대부분 `config/*.yaml`로 빠져 있어 양호하다 | 각 파일 |
| 11 | 거대 파일과 중복 | `run.py` 2,345줄(CLI 50개, 업무 로직 혼재). 렌더러가 둘이다(`web/render.py` 구운 HTML, `web/app.js` 셸 + JSON). 국장과 미국장이 db, build, render 구조를 사실상 복제했다. `financials.py`의 계정 동의어는 `dart-report/dartreport/statements.py`의 사본이라고 주석이 밝힌다 | 해당 파일 |
| 12 | 검증 상태 | KIS, KRX, 네이버 수급 파서의 docstring에 "실호출로 검증하지 못했다"가 남아 있다(이후 Actions에서 일부 실측. `us/ci-out`에 PASS/FAIL이 기록됨). 미국 일봉 대체 소스 stooq는 FAIL(0행), yahoo는 FAIL(429) | `ingest/kis.py`, `krx.py`, `flows.py` docstring, `us/ci-out/check.txt` |
| 13 | 테스트 공백 | `export/excel.py` 직접 테스트가 없다. openpyxl이 없는 환경에서도 테스트가 통과하므로 엑셀 경로는 실질적으로 검증되지 않는다. 탐지기 단위 테스트는 간접적이다 | `tests/` 매핑 결과 |
| 14 | 공개 레포 이전 시 | ① Pages 사이트는 공개다(주석: 사용자 결정은 비공개라 Pages를 껐다). ② `knowledge/sector_map.yaml`(LLM 분류 결과 약 2만 줄)과 `themes.yaml`은 자산이지만 공개해도 무방한지 판단이 필요하다. ③ 1~2번의 로그·캐시 노출. ④ 5~6번의 스크래핑 코드와 실데이터. ⑤ Anthropic 비용 예산이 `cost.json`에 커밋된다 | 위 근거 |
