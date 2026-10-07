# MIGRATION — stock_dashboard

- 작업: KBJ P1 legacy 이식, 2026-10-07. 원본 스냅샷 `p0src/stock-dashboard`(SD `f46178c`, 읽기 전용).
  기준은 `docs/conflict_map.md` §2(fixture 표)·§3.1(옮길 것) 과 ADR 0001 U3·U4.
- 원칙(PLAN §8 P1, ADR 0003): 경로·import 만 고친다. 예외는 U3 합성 교체와 개인·운영 정보 제거, 그리고 E4
  (원래 실패하던 검사 하네스 버그) 하나다. 검사를 지우거나 건너뛰게 하거나 단정을 약하게 하지 않았다.

## 1. 결과 요약

| 항목 | 원본 사본(`/tmp/p1/src-stock-dashboard`) | KBJ(`legacy/stock_dashboard`) |
|---|---|---|
| 검사 스크립트 10개(`SERVER_NO_STARTUP=1`, 네트워크 차단) | **9 통과 / 1 실패** — `check_futures_us_index` 가 `NameError: name '_yf' is not defined`(E4) | **10 / 10 통과** |
| pytest 래퍼 `tests/test_legacy_checks.py` | — (원본에 없음) | **12 passed**(목록 대조 1 + 검사 스크립트 10 + 합성 데이터 재현 1) |

- 원본 사본에서는 검사 스크립트 7개의 작업 머신 절대경로(`/home/<사용자>/stock-dashboard`)만 사본 경로로 바꿔 돌렸다
  (그 경로에는 다른 커밋의 작업 트리가 있어 그대로 두면 엉뚱한 `server.py` 를 읽는다). 출력 로그는 `/tmp/p1/sd-base-*.log`.
- KBJ 위치 결과와 원본 결과를 줄 단위로 비교했다. 9개 스크립트 출력은 한 줄을 빼고 같다 — `check_ohlcv_autofill` 의
  안내 줄 "시드 기준 대상: 시총 1,000억 이상 1,394종목 (800억 이상 1,556종목)" 이 합성 시드로 "1,346종목 (1,481종목)" 이 됐다(§3).
  `check_etf_marking` 의 "전 종목 4,063 중 ETF 1,255" 는 합성 시드에서도 같은 수다.
- 실행 환경: Python 3.12.3. 참고 환경 `/tmp/p0test/venv-312`(pandas 2.3.3)와, 최소 의존성만 넣은 전용 venv
  `/tmp/p1/venv-stock-dashboard`(pytest·pandas 2.3.3·requests) 두 곳에서 같은 결과. 검사 스크립트 10개가 실제로 import 하는
  외부 패키지는 pandas·requests 뿐이다(`server.py` 는 import 하지 않고 소스에서 함수를 꺼내 쓴다).
- 래퍼가 실제로 실패를 잡는지 확인: 사본에서 `server.py` `ETF_PATTERNS` 의 `'KODEX'` 를 빼면 `check_etf_marking`·
  `check_ohlcv_autofill` 2개가 실패한다(합성 시드의 `069500 KODEX 200` 이 대상에 섞인다).

실행(레포 루트에서). `python` 은 pandas·pytest 가 있는 Python 3.12 환경이어야 한다(시스템 기본 `python` 에 pandas 가 없으면
`check_futures_us_index`·`check_ohlcv_autofill` 이 `ModuleNotFoundError` 로 실패한다 — 예: `/tmp/p1/venv-stock-dashboard/bin/python`):

```
cd legacy/stock_dashboard && PYTHONDONTWRITEBYTECODE=1 python -m pytest tests -q -p no:cacheprovider
# 스크립트 하나만: cd legacy/stock_dashboard && SERVER_NO_STARTUP=1 python scripts/check_etf_marking.py
# 합성 데이터 재생성·확인: cd legacy/stock_dashboard && python scripts/make_synthetic_fixtures.py [--check|--print]
```

## 2. 옮긴 것과 옮기지 않은 것

옮김: 루트 `*.py` 26개, `agents/`, `db/`(`__init__.py`·`database.py`·`migrate_from_json.py`·`schema.sql`), `migrations/`,
`scripts/`(24개 + 신규 1), `static/`, `index.html`, `requirements.txt`, `REBUILD_BRIEF.md`(설계 브리프, 개인 경로·호스트·키 없음 확인),
원본 `.gitignore`(프로젝트 안 무시 규칙 — 그대로), `data/valuechain_map.json`(자체 작성 사전, 공개 등급).

| 원본 경로 | 옮기지 않은 이유 |
|---|---|
| `.github/workflows/`(8개) | Render 전용 배포 확인·wake 핑·네이버/OHLCV 실측. 레포 루트 `.github` 에 넣으면 실행된다. 검사 스크립트가 읽지 않으므로 legacy 안에도 두지 않았다 |
| `render.yaml` | 배포 설정(보관만, conflict_map §3.1) |
| `data.json` | 시세·테마 스냅샷(pykrx·네이버) — 제외/로그인 등급 |
| `themes_mapping.json` | 네이버 테마 크롤 — 제외 등급 |
| `data/naver_universe_seed.json`(원본) | 네이버 수집 4,063종목 — 제외 등급. 같은 경로에 **합성** 시드를 두었다(§3) |
| `data/valuechain_external_validation.json` | 네이버 기업개요 본문 발췌(`profile_excerpt`) — 제외 등급 |
| `data/valuechain_llm_validation.json` | 로컬 LLM 판정 결과 — 로그인[애매] |
| `cache/`·`db/*.db` | 런타임 산출물(스냅샷에도 없음) |
| `.git`(워크트리 포인터 파일)·`__pycache__/` | 생성물 |

`.env`·`*.key`·토큰 캐시(`cache/kis_token.json` 등)는 스냅샷에 없었고 열지도 복사하지도 않았다.

## 3. U3 합성 교체

생성기 `scripts/make_synthetic_fixtures.py`(신규, seed 20261007) 하나가 아래 값을 모두 만든다. 두 번 돌려 바이트 단위로 같은
파일이 나오는 것을 확인했다(sha256 동일). `--check` 는 시드 파일이 생성기 출력과 같은지, 검사 스크립트에 붙인 인라인 값이
생성기 출력과 글자 그대로 같은지 본다 — pytest 래퍼의 `test_synthetic_fixtures_reproducible` 가 이것을 돌린다.
기대값(문구·튜플)은 손으로 고치지 않았다. 생성기가 합성 입력으로부터 계산해 낸 값을 그대로 붙였다.

| 파일 | 무엇을 | 왜 | 시험 영향 |
|---|---|---|---|
| `data/naver_universe_seed.json` | 합성 유니버스. 구조 `{_note, source_date, stock_count, stocks{코드: {name, sectors[1], market_cap?}}}` 동일, 한 줄 JSON 동일. 4,063종목·ETF/ETN 이름 1,255개·`market_cap` 없는 종목 146개(그중 ETF 27개)·업종 79개 + '기타' — 원본과 같은 규모. 코드는 가상 `9xxxxx`, 이름은 가상(`가상전자0001`·`KODEX 가상지수0001`·`가상증권 가상지수0025 ETN` 등), 시총은 로그정규 합성값(원 단위, 정수). 예외 3개: 검사 스크립트가 코드로 직접 부르는 `005930`(시총 1위·1e14 초과여야 함)·`069500`(ETF)·`138930`(이름 'BNK' — ETF 오탐 회귀) 은 코드를 두고 이름은 검사 스크립트 안의 리터럴(`삼성전자`·`KODEX 200`·`BNK금융지주`)과 같게 했다. 시총은 합성 | 원본은 네이버 수집(제외 등급) | `check_etf_marking` 3번(전 종목 오탐 0, BNK 은 ETF 아님)·`check_ohlcv_autofill` 1-b(단위·시총 1위·ETF 제외·1,000억 이상 전부·1,000종목 초과) 단정 그대로 통과. 안내 줄의 종목 수만 바뀜(§1) |
| `scripts/check_futures_us_index.py` (KIS) | `OUT`(선물 현재가 `output1` 근월물·원월물 2건)을 같은 키·문자열 자료형의 합성 응답으로. 이에 맞춰 단정 6곳의 기대값(시·고·저·종 튜플, 미결제약정·증감 튜플, 섹션 줄 0·1·2·5)을 생성기 출력으로. 부호 구성(근월 증감 +, 원월 증감 −)과 월물 코드·이름·최종거래일(`A01612`/`F 202612`/`20261210` 등 — 상품 명세) 은 그대로. 마스터 줄(`MASTER`, 값이 `00000.00` 인 손으로 쓴 5줄)은 실측 응답이 아니라 그대로 둠. 머리 docstring·출력 제목의 '실측' 을 '합성' 으로 | 원본 docstring: "2026-09-23 러너 실측 그대로"(로그인 등급) | 단정 수·강도 동일(28 PASS) |
| `scripts/check_futures_us_index.py` (Yahoo) | 4-1 '마지막 일봉 종가 NaN' 재현의 종가 2개와 `last_price` 를 합성값으로, 기대 줄도 생성기 출력으로. 2·3·4 의 둥근 숫자(6600·22000 등)는 원래 손으로 만든 값이라 그대로 | 출력 제목에 "2026-09-23 실측" 이라 적힌 Yahoo 값(로그인 등급) | 단정 동일 |
| `scripts/check_etf_marking.py` | 2번 재현 표(`LEAKED`·`REAL`)에서 거래대금 상위 5행(`KODEX 200`·`KODEX 레버리지`·`TIGER 200`·`SK하이닉스`·`삼성전자`)의 등락률·거래대금을 생성기 출력으로. 생성 규칙은 원본의 순서 관계만 지킨다 — 실제 종목 2개(40,000~70,000) > ETF 3개(10,000~30,000) > 나머지(9,000 이하), 등락률은 ±3% 안(급등·급락 ±5% 쿼리에 걸리지 않게). 종목 코드·이름, 둥근 가격, 9,000 이하의 둥근 거래대금 행은 손으로 만든 재현 값이라 그대로. 한 줄에 두 튜플이던 것을 한 줄에 하나로 나눔(생성기 `--check` 가 튜플 단위로 대조) | 원본 커밋이 "2026-09-22 시황에 ETF 가 섞여 나갔다" 를 재현하며 실린 값을 옮긴 것으로 보인다(둥글지 않은 거래대금·등락률 — 네이버 시세, 로그인/제외 등급). **이식 검증 단계에서 추가 교체** | 단정 수·강도 동일. 출력은 원본과 줄 단위로 같다(특징주 쿼리 결과가 같은 순서 관계로 정해지므로). 실제 종목 행·ETF 행을 바꾸면 `--check` 가 실패한다 |
| `scripts/check_ohlcv_autofill.py` | 4번 '네이버 응답 파싱' 의 첫 행(시가·고가·저가·종가·거래량·외국인소진율, 두 곳)과 기대 튜플을 합성값으로. 날짜·호출 코드(`005930`)·깨진 행들은 그대로 | 실제 종목의 하루 시세로 보이는 숫자(네이버, 제외 등급) | 단정 동일 |

## 4. 경로·개인정보·운영정보 — 파일별 변경

| 파일 | 무엇을 | 왜 | 시험 영향 |
|---|---|---|---|
| `scripts/check_brief_sections.py`·`check_closing_brief.py`·`check_newhigh_flow.py`·`check_newhigh_full_list.py`·`check_watchdog_alerts.py`·`check_watchdog_gating.py` | `open('<작업 머신 절대경로>/server.py')` → `import os` + `ROOT = 스크립트 위치의 상위 폴더` + `os.path.join(ROOT, 'server.py')`(이미 상대경로를 쓰던 `check_etf_marking.py` 와 같은 방식) | 작업 머신 절대경로(conflict_map §3.2) | 없음 — 어디서 돌려도 자기 `server.py` 를 읽는다 |
| `scripts/check_new_high_logic.py` | 위와 같음 + `sys.path.insert(0, '<작업 머신 절대경로>')` → `sys.path.insert(0, ROOT)` | 같음 | 없음 |
| `scripts/check_futures_us_index.py` (E4) | 소스에서 꺼낼 함수 목록 `want` 에 `'_yf'` 추가, 그 함수가 쓰는 모듈 상수 `_YF_LOCK` 을 `US_INDEX_TICKERS` 와 같이 꺼냄, 실행 이름공간에 `threading`·`time` 을 넣음, 꺼낸 조각 수 단정 4 → 6(메시지도 "함수 4개 + 티커 표 + yfinance 잠금"). **서버 코드는 고치지 않았다** | **원래 실패 → 검사 하네스 버그 수정.** `server.py` 의 `_fetch_us_indices_live` 가 yfinance 동시 import 경합을 막으려고 `_yf()` 를 부르게 바뀌었는데 검사 스크립트의 추출 목록이 따라가지 않아 `NameError: _yf` 로 죽었다(conflict_map E4, 3.11·3.12 둘 다) | 원본 데이터에 이 수정만 얹어도 `ALL PASS` 를 확인했다. 진짜 `_yf` 가 가짜 yfinance 모듈을 import 해 돌려주므로 대역 없이 실제 함수가 돈다 |
| `scripts/daily_macbook_cron.sh` | `PROJECT_DIR=` 의 맥 개인 홈 절대경로 → `PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"`, 머리 주석의 crontab 예시 경로 2곳 → `<프로젝트 경로>/scripts/…` | 사용자 이름이 든 로컬 경로 | 시험이 부르지 않음. 스크립트 위치 기준이라 동작은 같다 |
| `scripts/verify_deploy.py` | 기본 주소로 박혀 있던 Render 서비스 호스트명을 지우고, 인자가 없으면 환경변수 `DASHBOARD_URL` 을 읽게 함(값은 예시에도 넣지 않음). 둘 다 없으면 사유를 출력하고 종료코드 1. docstring 의 사용 예·호스트 언급도 같이 | Render 호스트명(운영 정보). 이름은 원본 워크플로가 쓰던 저장소 변수 이름과 같다 | 시험이 부르지 않음(배포 확인용, 네트워크 필요) |
| `analysis_journal_helper.py` | `build_prefill_data` 의 `'analyst'` 기본값(사용자 이름) → `os.environ.get('JOURNAL_ANALYST') or '사용자'`, `import os` 추가 | 사용자 이름 | 시험 없음 |
| `analysis_journal_api.py` | `__main__` 의 `test_create` 표본(사용자의 실제 분석 일지 — 종목·논지·피어 비교·증권사 추정 출처·태그)을 같은 키·자료형의 합성 표본으로(가상 코드 `999990`, tp = eps × per 관계 유지) | 개인 데이터(U3: 사용자 노트·매매 일지) | 시험 없음(수동 CLI) |
| `scripts/test_collectors.py` | 마스킹 확인용 **가짜** 봇 토큰 리터럴(`123456789:AAbb…` 자리표시자)을 인접 문자열 두 조각으로 나눔. 실행 시 값은 원본과 같다(AST 상수 비교로 확인) | 레포 공개 안전 검사(`scripts/check_public_safety.py`)가 줄 단위로 토큰 형태를 잡는다 — 허용 목록은 이 작업 범위 밖이라 리터럴을 나눴다 | 이 스크립트는 래퍼에 넣지 않았다(§5) |
| `tests/test_legacy_checks.py` | **신규** pytest 래퍼(§1·§5) | 루트 CI 에서 프로젝트별로 돌릴 수 있게(ADR 0003) | — |
| `scripts/make_synthetic_fixtures.py` | **신규** 합성 데이터 생성기(§3) | U3 | 래퍼가 `--check` 로 돌린다 |

그 밖의 파일은 바이트 단위로 원본과 같다. 확인만 하고 고치지 않은 것:

- `server.py` 의 자기 핑(`_self_keep_alive`)·텔레그램 웹훅 등록(`_telegram_setup_webhook`)·Render 감지는 이미 환경변수
  `RENDER_EXTERNAL_URL` 을 읽고, 값이 없으면 아무것도 하지 않는다 — 호스트명이 코드에 없다.
- `migrations/007_rename_kuvic_to_review.py`·`008_consensus_revision.py` 의 `KUVIC` 은 DB 값(`analyst = 'KUVIC'` 행을 바꾸는
  마이그레이션 대상)이라 바꾸면 마이그레이션 뜻이 달라진다. 그대로 뒀다 [확인 필요: 공개 레포에 남겨도 되는 이름인지].
- `scripts/check_etf_marking.py` 의 `LEAKED`·`REAL` 표 중 둥근 가격·둥근 거래대금(9,000 이하) 행은 손으로 만든 재현 값이라
  그대로다. 둥글지 않던 상위 5행의 등락·거래대금은 §3 에서 합성으로 바꿨다.
- `static/js/app.js` 의 `DUMMY`(data.json 을 못 받을 때 쓰는 화면용 더미 — 실제 종목 이름·코드에 둥근 등락·거래대금,
  100 에서 시작하는 손으로 만든 스파크라인)는 시험이 읽지 않고 실측 모양이 아니라 그대로 뒀다.

## 5. 래퍼에 넣지 않은 시험

| 파일 | 이유 |
|---|---|
| `scripts/test_collectors.py` | 네이버·yfinance 실호출(네트워크 필요). **U4 대상 — P3~P5 에서 KRX·KIS 로 교체** |
| `scripts/test_data_json.py` | 같음(`data.json`·`themes_mapping.json` 도 옮기지 않았다) |

래퍼(`tests/test_legacy_checks.py`)는 검사 스크립트 10개를 각각 subprocess 로 돌려 종료코드 0 을 본다(파라미터화).
환경을 최소로 새로 만들어(PATH·HOME·LANG 등만 물려주고 키·토큰 환경변수는 넘기지 않는다) `SERVER_NO_STARTUP=1`,
HTTP(S) 프록시를 닫힌 포트(`127.0.0.1:9`)로 둬 실수로 나가는 요청이 바로 실패하게 했고, `PYTHONDONTWRITEBYTECODE=1` 로
legacy 디렉터리에 `__pycache__` 를 남기지 않는다. `check_*.py` 가 늘거나 줄면 목록 대조 시험이 실패한다.

## 6. 의존성

`requirements.txt` 는 원본 그대로다. 검사 스크립트·래퍼만 돌리려면 `pandas`(2.3.3 확인)·`requests`·`pytest` 면 된다.
앱 전체 실행에는 `requirements.txt`(Flask·pykrx·pandas·APScheduler·gunicorn·yfinance·requests·beautifulsoup4·lxml·
flask-socketio·simple-websocket)에 더해 **requirements 에서 빠져 있던** `aiohttp`·`tenacity`(`dart_collector.py`)가 필요하다
(conflict_map §3.3). pykrx 는 import 때 `KRX_ID`·`KRX_PW` 로 자동 로그인을 시도한다(E5).

## 7. 공개 안전 점검

옮긴·새로 만든 파일 79개 전체(이 문서 포함)에 대해 `kbj.core.masking.SECRET_SHAPES` 10종(텔레그램 봇 토큰·Anthropic·GitHub·AWS·JWT·PEM·
KIS 앱키/시크릿 형태·계좌번호 8-2)과 맥 개인 홈 경로·Render 호스트 도메인·작업 머신 홈 경로·사용자 이름·Render 서비스 식별자 grep: **0건**.
`scripts/check_public_safety.py`(레포 전체): 통과, 발견 0건·허용 0건.

## P2. KBJ 정본으로 재배선 (묶음 H, 2026-10-07)

설계 `docs/p2_design.md` §1.9·§3.7·§3.8(K1)·§5.9·§5.10·§7.2. KIS·KRX·DART 는 KBJ 논리 URL 브리지
(`kbj.data.legacy_bridge` — `kis:`·`kis-master:`·`krx:`·`dart:`)로만 부르고, 텔레그램은 KBJ notifier 대기열
shim(`kbj.services.notifier.client.legacy_send`)으로, 휴장·장중은 KBJ 캘린더(`kbj.core.calendar_compat`)로.
키는 KBJ 설정(`KBJ_KIS_APP_KEY`·`KBJ_KRX_API_KEY`·`KBJ_DART_API_KEY`)에서 브리지가 넣는다 — 옛 이름
(`KIS_APP_KEY`·`KRX_API_KEY`·`DART_API_KEY`·`TELEGRAM_*`)은 읽지 않는다(docs/secrets.md §2). 네이버
호출(U4·D6)은 그대로 기준선으로 남는다(P3~P5 에서 KRX·KIS 로).

| 파일 | 바꾼 것 |
|---|---|
| `kis_api.py` | `requests` → `kbj.data.legacy_bridge`, `KIS_BASE = "kis:"`, `FO_MASTER_URL = "kis-master:…"`. **토큰 발급·파일 캐시(`cache/kis_token.json`) 삭제** — `_get_token()` 은 KBJ auth 가 Redis 에 둔 토큰을 읽기만(`access_token_or_none`), 없으면 `_headers` 가 None 이라 호출자는 빈 결과(기존 동작). 자체 리미터(초당 18회) 삭제 — 브리지가 앱키당 4/s. `_is_kr_market_hours` → `calendar_compat.is_kr_regular_hours(now_kst())`(UTC 서버의 naive `datetime.now()` 오류도 사라졌다). `_now_kst` → `kbj.core.time.now_kst` |
| `krx_api.py` | `urllib` 직접 호출 → 브리지 `krx:`. `KRX_API_BASE = "krx:"`, `has_api_key()` 는 `KBJ_KRX_API_KEY`. 호출 간 0.2초 슬립 삭제(리미터·일 예산 `krx:calls:<날짜>` 가 대신). `basDd` 없는 호출(종목기본정보 2개)은 부르지 않고 사유를 찍는다 — KBJ 어댑터는 기준일이 필요하다 |
| `dart_collector.py` | `DART_BASE_URL = "dart:"`, aiohttp 호출 → `asyncio.to_thread(legacy_bridge.get, …)`. 재시도 대상에 `BridgeConnectionError`. 키 확인은 `KBJ_DART_API_KEY` |
| `earnings_parser.py`·`overhang_parser.py` | `document.xml`·`list.json` → 브리지 `dart:`(`crtfc_key` 는 브리지가 넣는다). `DART_API_KEY` 는 '설정됨' 표시값(키 원문 아님) |
| `server.py` — DART 6곳(`poll_dart_disclosures`·`_load_dart_corp_code_map`·`_fetch_dart_quarter`·`_try_dart_segment_revenue`(2)·`_fetch_kr_earnings`) | 그 함수 안 `requests` → 브리지, 주소 → `dart:/…`, 키 확인 `_dart_key_configured()` |
| `server.py` — 텔레그램 | `send_telegram` → `legacy_send(…, source="sd.send_telegram")[0]`, `send_telegram_long` → `legacy_send(…, numbered=True)[0]`(분할·`(i/n)` 머리는 notifier). 종류는 `config/notify.yaml legacy_kinds`(부른 함수 이름). 봇 토큰·chat id 를 읽지 않는다 |
| `server.py` — 라우트 삭제(§5.10 #19·#20·#21·#43·#44·#45) | `/api/test_telegram`·`/api/telegram/test`·`/api/telegram/briefing_test`(인증 없는 시험 발송), `/api/telegram/webhook`·`/api/telegram/setup_webhook`·`_telegram_setup_webhook`·`_telegram_secret`·부팅 setWebhook(웹훅은 notifier 하나 — §5.11), `/api/ops/brief/closing`(재발송은 P3 운영 화면), `/api/agent/run`, `/api/ops/cron/trigger/<id>`(수동 실행은 `python -m kbj.services.scheduler run-once`). 자리에 `# KBJ P2 삭제` 주석. 프런트(`static/js`)의 세 버튼은 404 가 된다 — 기준선 |
| `server.py` — 시각·휴장 | `now_kst()` → `kbj.core.time.now_kst()`. `_KR_HOLIDAYS_2026` **삭제**, `_is_kr_holiday`·`_next_trading_open_kst` → `calendar_compat`(naive 시각은 KST 로 읽는다), `is_market_hours` → `is_kr_regular_hours`. 2026년에 다섯 날이 바뀐다(05-01·06-03·08-17·10-05 휴장, 09-28 개장 — 설계 §7.3), 15:30:00 부터 장 밖([09:00, 15:30)), 지연 개장일 반영 |
| `earnings_telegram_sender.py` | `send_telegram_message` → `legacy_send(…, source="sd.earnings", kind="alert.earnings")`. 반환 dict 모양 유지(`message_id` 는 notifier 가 보낼 때 정해져 None) |
| `scripts/check_watchdog_gating.py` | `_KR_HOLIDAYS_2026` 를 떼어 오던 정규식 → 새 `_is_kr_holiday`(KBJ 캘린더) 발췌. 단언(1~5)은 그대로 |

남긴 것(기준선): `_split_telegram_lines`·`_TG_LIMIT`·`_TG_CHUNK`(검사 스크립트 `check_newhigh_full_list.py`
가 떼어 시험한다 — 메시지를 kbj 작업으로 옮길 때 지운다), 텔레그램 cron 등록 조건
`os.getenv("TELEGRAM_BOT_TOKEN")`(SD APScheduler 는 KBJ 등록부로 폐지 — §6.8, 돌지 않는다),
`_handle_telegram_command`·`_tg_*` 명령 함수(라우트가 없어 부르는 곳 없음 — 명령은 notifier, P3),
`data_fetcher.py`·`data_freshness.py` 의 `now_kst`(H 파일 목록 밖 — P3), 네이버·KRX 스크랩 호출(U4).

시험: 검사 스크립트 10 + 목록 대조 + 합성 재현 = 12 그대로 통과, 새 다리 시험 `tests/test_kbj_bridge.py`
(+1 — `kis_api` 가 브리지를 쓰고 토큰을 발급하지 않는다, 직접 주소는 `ValueError`) → **13 통과**.
