# MIGRATION — etf_traker 나머지(monitor/kr·monitor/flow·flowlab·etf_tracker_v9·dart-report·루트 문서)

- 작업: KBJ P1 legacy 이식, 2026-10-07. 원본 스냅샷 p0src/etf-traker(ET `0014f57`, 읽기 전용).
- 범위: 이 문서는 **board 를 뺀** etf_traker 이식만 적는다. board 는 `board/MIGRATION.md`.
- 원칙(PLAN §8 P1, ADR 0001 U3): 경로·import 만 고친다. 예외는 U3 합성 교체와 개인·운영 정보 제거뿐이다.
  테스트를 지우거나 skip·xfail 로 바꾸거나 assert 를 약하게 하지 않았다. 테스트 수는 전부 원본과 같다.

## 1. 결과 요약

| 대상 | 명령(legacy/etf_traker 에서) | 원본 사본 | KBJ 사본 |
|---|---|---|---|
| monitor/kr | `python -m unittest discover -s monitor/kr -p "test_*.py" -t .` | 94 OK | 94 OK (크로미움 있으면 skip 0, Playwright 없으면 skip 1) |
| monitor/flow | `python -m unittest discover -s monitor/flow -p "test_*.py" -t .` | 129 OK | 129 OK |
| flowlab | `python -m flowlab selftest` | 44/44 | 44/44 |
| dart-report | `cd dart-report && python tests_smoke.py` | `built. quarters= 14 annual= 4 bridge steps= 9` | 같은 출력 |
| etf_tracker_v9 | import 스모크(아래 §5) | 13개 모듈 import 성공 | 13개 모듈 import 성공 |

- 실행 환경: Python 3.12.3, `/tmp/p0test/venv-312`(pandas 2.3.3). 이 환경에는 `/opt/pw-browsers` 크로미움이 있어
  원본·KBJ 모두 `test_page` 가 실제로 돌았다(skip 0). Playwright 를 막고 돌리면 94 OK(skip 1) — 원본 기준과 같다.
- monitor/kr·monitor/flow·flowlab 는 `board` 패키지를 import 한다. 검증은 `/tmp/p1/etf-rest` 에
  (이 이식 결과물 + 원본 board 사본)을 조립해 돌렸고, KBJ 위치(`legacy/etf_traker`, 다른 작업이 옮긴 board 포함)에서도 같은 결과였다.
- 합성 데이터 생성기 3개는 모두 시드 고정이다. 두 번 돌려 바이트 단위로 같은 파일이 나오는 것을 확인했다.

## 2. 옮기지 않은 것

| 원본 경로 | 이유 |
|---|---|
| `monitor/bok/` 전체 | 제3자 `rsm0kk/bok-monitor` 이식(U3). P5 에서 직접 다시 만든다 |
| `monitor/kr/template.html` | 제3자 `rsm0kk/kr-sector` 빌드 산출물(U3) → 직접 쓴 최소 템플릿으로 대체(§3) |
| `monitor/kr/knowledge/themes.json`·`industries.json` | 제3자 배포본에서 추출(U3) → 합성 사전으로 대체(§3) |
| `monitor/kr/tests/fixtures/golden.json` | 제3자 배포본 값(시세 기반, U3) → 합성 회귀 골든으로 대체(§3) |
| `monitor/flow/tests/fixtures/golden.json` | KRX 정보데이터시스템 워크북 실데이터(로그인 등급) → 합성으로 대체(§4) |
| `monitor/kr/cache`·`monitor/flow/out`·`cache` | 실행 산출물(원본에도 없음) |
| `flowlab/ci-out/` | 네이버 수급·DART·이벤트 스터디 CI 결과(로그인/제외 등급 혼재, 3.3MB) |
| `flowlab/cache/`·`flowlab/out/` | 실행 산출물(원본에 없음. `config.py` 가 import 때 만든다) |
| `etf_tracker_v9/docs/base.json` | 네이버 ETF 스냅샷(제외 등급) |
| `etf_tracker_v9/etf.db`·`docs/index.html` | 실행 산출물(원본에 없음). 빈 `docs/` 폴더도 두지 않았다 — `tracker.py --run` 이 만든다 |
| `dart-report/.cache/`·`dart-report/out/`(`.gitkeep` 포함) | 실행 산출물. KBJ `.gitignore` 가 `legacy/**/out/` 을 무시한다 → `tests_smoke.py` 가 폴더를 만들게 했다(§6) |
| `__pycache__/` | 생성물 |
| 루트 `docs/`(발행된 보드·bok·kr 사이트, `api/`·`artifact.html` 등) | GitHub Pages 생성물 — 로그인 등급 시세 포함(conflict_map §3.1). board 시험 1건이 원래의 skipTest 로 건너뛴다(`board/MIGRATION.md` 0장) |
| 루트 `.gitignore` | 원본 레포 루트 무시 규칙. KBJ 루트 `.gitignore`(`legacy/**/state/`·`out/`·`cache/` 등)가 대신한다. `board/.gitignore` 는 옮겼다 |
| 원본 `.github/workflows/` | 이 범위의 시험은 워크플로 파일을 읽지 않는다. 루트 `.github` 에도, legacy 안에도 추가하지 않았다(board 작업이 둔 `legacy/etf_traker/.github/` 는 건드리지 않음) |

`.env`·`*.key`·토큰 캐시는 이 범위에 없었고 열지도 복사하지도 않았다.

## 3. monitor/kr — 파일별 변경

시험 의미가 바뀐 곳: **원본 골든 대조 10건은 '남의 배포본과 숫자가 같은가'(독립 구현 대조)였고, 이제는
'우리 엔진의 출력이 바뀌지 않았는가'(회귀)다.** 원본에서 배포본과 대조해 확정했다는 사실(README 의 표)은
그대로 남겼지만, 그 대조를 KBJ 공개 레포에서 다시 돌릴 수는 없다. 해당 시험:
`test_engine.골든대조` 9건(섹터집계·상대강도 가중치·sg·모멘텀·로테이션·섹터 RS 중앙값·급증집계·낙폭요약·1D=등락률),
`test_flows.골든대조.test_섹터합이_배포본과_같다` 1건. 시험 이름의 '배포본' 은 바꾸지 않았다(이름 변경도 변경이라 피함).

| 파일 | 무엇을 | 왜 | 시험 영향 |
|---|---|---|---|
| `template.html` | **새로 작성.** state 계약(`window.__STATE__`, 자리표시자 1개)만 따라 쓴 최소 화면: 지수 카드·묶음/기간 버튼·히트맵(`#heatmap`)·로테이션 산점도(`#rotChart`, 인라인 작은 `Chart` 클래스)·상하위 표(종목명 `data-tip` 에 시가총액·재무)·수급 표(`#flowTable`)·ETF 표(`#etfTable`)·`#asofLag .badge.prov`. 외부 스크립트·글꼴 없음 | 원본은 제3자 빌드 산출물(U3) | `test_build`(크기 300KB 초과·자리표시자 없음)·`test_page`(브라우저로 열어 모든 단추를 눌러도 오류 0, 히트맵·산점도·카드·수급·ETF·업종·잠정 배지) 그대로 통과 |
| `knowledge/themes.json` | 합성 사전: 테마 198칸(상위 33 + 하위 165, 배정 1,676건) + 밸류체인 23칸(구성 없음 — 원본과 같은 모양). 키(`source·groups[key,title,sectors[name,label,level,parent,children,declared,members,note]]`) 동일. 가상 이름(`가상테마NN › 하위N`)·가상 코드(`8xxxx0`) | U3 | `test_build`·`test_page` 가 여기서 종목을 뽑는다. 통과 |
| `knowledge/industries.json` | 합성 사전: 가상 업종 58개, 배정 555건, 중복 없음. 키 동일 | U3 | `test_industries` 의 `58개`·`500건 초과`·`한 업종에만` 단정 그대로 통과 |
| `tests/fixtures/golden.json` | 합성 회귀 골든: 합성 일봉(700종목·400거래일)·합성 지수·합성 수급을 `build.build_state` 에 넣어 나온 값 중 테마 하위 섹터 12개(구성 최다순)와 그 구성종목 160개. 키(`note·periods·stocks·sectors·sectorExtra`)와 종목 필드(`c,n,m,p,f,v,k,r,sec,rs,rsp,t,tl,sg,dd,fl`) 동일 | U3. 기대값을 손으로 맞추지 않고 엔진으로 다시 만들었다 | 위 10건(의미 변경). 엔진을 일부러 바꾸면(섹터 RS 를 평균으로, nearHigh 경계 −25) 해당 시험이 실패하는 것을 확인 |
| `tests/fixtures/make_synthetic.py` | **신규.** 위 세 JSON 을 만드는 결정론적 생성기(seed 20261007). 실행: `python monitor/kr/tests/fixtures/make_synthetic.py` | U3 합성 데이터는 생성 스크립트와 함께 둔다 | 시험이 직접 부르지 않음 |
| `tests/test_engine.py` | 모듈·클래스 docstring 만 수정(골든이 합성 회귀 골든임을 적음) | 사실과 맞추기 | 없음 |
| `tests/test_flows.py`·`tests/test_industries.py` | docstring 만 수정 | 같음 | 없음 |
| `build.py`·`industries.py` | docstring 만 수정(템플릿·사전이 합성/직접 작성으로 바뀜) | 같음 | 없음 |
| `README.md` | 맨 위에 KBJ 이식 메모 추가. 본문은 원본 그대로 | 같음 | 없음 |
| 그 밖의 코드(`engine.py`·`etf.py`·`financials.py`·`flows.py`·`ingest.py`, 나머지 시험) | 변경 없음 | — | — |

남는 것: 실사용 테마·업종 사전과 실제 화면은 P5 에서 직접 다시 만든다(U3). 합성 사전으로 `build.py` 를 실행하면
가상 테마로 묶인 화면이 나온다 — 운영 데이터로 쓰면 안 된다.

## 4. monitor/flow — 파일별 변경

| 파일 | 무엇을 | 왜 | 시험 영향 |
|---|---|---|---|
| `tests/fixtures/golden.json` | 합성: 가상 종목 `가상전자 999990`, 같은 20거래일(2026-08-18~09-14, 기준일 08-14), 13구분 일별 금액·수량(원·주 정수, 전체=0), 구분별 기간합계 6칸(순매수 = 일별 합, 원 단위까지 일치), 종가. `expect.main`·`expect.inst`·`expect.narrative` 에 더해 문장 숫자용 `expect.burst`·`top2`·`recent_driver`·`texts` | 원본은 KRX 워크북 실데이터(로그인 등급) | 아래 시험들 |
| `tests/fixtures/make_synthetic.py` | **신규.** 결정론적 생성기(seed 20261007). 기대값은 `analyze`·`narrative` 를 import 하지 않고 **워크북 정의로 따로 계산**한다(원본 골든이 워크북 = 독립 계산이었던 것과 같은 성격). 합성 수급이 원본과 같은 이야기 구조(9/1~9/4 기관 집중·주가 동행, 이후 조정, 최근 5일 기관 매도·외국인 매수 교차, 상위 둘 투신·사모, 최근 주동 사모)를 갖는지 스스로 확인하고 아니면 멈춘다 | U3 | — |
| `tests/test_analyze.py` | 모듈 docstring 만 수정(실종목 이름 제거, 합성·독립 계산 설명) | 개인·실데이터 언급 제거 | 없음. 골든 대조 9건은 새 `expect`(독립 계산)와 대조 |
| `tests/test_narrative.py` | 숫자 리터럴 → `golden['expect']` 에서 읽음: 집중 금액(→`burst.amt`), 구간 가격(→`burst.price_pct`), 문장 숫자 9개(→`texts`, 개수 9 단정 추가), 상위 둘 합(→`top2.share`), 최근 주동 비중(→`recent_driver.share`). 허용오차(places)는 원본과 같다. 날짜(`20260901`~`20260904`, 직전일 `20260831`)·구분 이름(`투신`·`사모`)·교차·전환·'동행' 단정은 그대로. docstring 의 실제 종가 제거 | 원본 리터럴은 실데이터 워크북 숫자 | 단정 수·강도 동일. `RECENT_DAYS`·`BURST_WINDOW` 를 바꾸면 실패하는 것을 확인 |
| `tests/test_run.py` | 실종목 이름·코드 `assertIn` 2건 → 합성 종목 `'가상전자'`·`'999990'` | 골든 종목이 합성으로 바뀜 | 단정 수 동일 |
| `README.md` | 골든 설명을 합성으로 고치고 실제 종목명·코드·종가와 구간 수익률 제거 | 실데이터·개인 자료 언급 제거 | 없음 |
| `narrative.py` | docstring 의 워크북 문장 숫자와 실제 종가를 `NNN.N` 자리표시로 바꿈 | 같음 | 없음(코드 변경 없음) |
| 그 밖(`analyze.py`·`chart.py`·`capture.py`·`kissrc.py`·`krx.py`·`returns.py`·`run.py`·`telegram.py`, `test_kis.py`·`test_krx.py`·`test_returns.py`) | 변경 없음. `test_kis.py` 의 종목 라벨(실종목 이름·코드)은 손으로 만든 3구분 표본의 라벨일 뿐 실데이터가 아니라 그대로 뒀다 | P1 은 경로·import 만 | — |

## 5. flowlab — 파일별 변경

| 파일 | 무엇을 | 왜 | 시험 영향 |
|---|---|---|---|
| `tests.py` | 박혀 있던 네이버 trend API 원문 4행(실종목, 러너 실측)과 siseJson 2행을 `fixtures/synthetic/naver_samples.json` 로드로 바꿈. 숫자 리터럴 8개(소진율·거래량·기관/외인 순매수·보유율·등락률 2개)와 금액 환산식의 수량·종가를 생성기가 문자열로 바꾸기 **전** 숫자(`_X`/`_R`)로 바꿈. 날짜 리터럴(`2026-09-03`·`2026-09-16`·`2026-09-15`)과 행 수(2·4·3)는 그대로 | 네이버 = 로그인/제외 등급(U3·U4) | 44건 그대로. 정성 조건(1행 기관·외인 순매수, 4행 기관 순매도, 1행 하락·3행 상승)은 생성기가 보장 |
| `fixtures/synthetic/make_synthetic.py` | **신규.** 결정론적 생성기(seed 20261007, 가상 종목 999990). 원 응답과 같은 키·문자열 형식("+85,620"·"40,550"·"31.13%"·중첩 `compareToPreviousPrice`) | U3 | — |
| `fixtures/synthetic/naver_samples.json` | **신규.** 위 생성 결과 | U3 | — |
| `config.py`·`naver.py`·`probe_trend_pages.py`·`probe_market_unit.py` | 주석·docstring 에 붙어 있던 네이버 응답 원문 한 줄(값)을 같은 모양의 합성 값으로 바꾸고 `(KBJ P1: 모양만 원문, 값은 합성)` 표시 | 실데이터 발췌 제거 | 없음(주석) |
| `verify.py`·`__main__.py`·`README.md` | 수기 기준값 예시(실종목 코드와 실제 5일 기관·외인 금액, 후행 초과수익)를 가상 값 `999990:inst5=12.3,frgn5=45.6`, `999990:2026-06-15:exc5=-1.23` 으로 | 같음 | 없음(도움말·예시) |
| 그 밖 | 변경 없음 | — | — |

데모 DB: flowlab 안에는 데모 DB 가 없다. `demo.py`·`backfill.py` 는 `board/board.db.demo` 를 읽는데 그 파일은 board 쪽(원본 스냅샷에도 없음, 실행 때 생성)이라 이 범위에서 손대지 않았다.

## 6. etf_tracker_v9·dart-report·루트 문서

| 파일 | 무엇을 | 왜 | 시험 영향 |
|---|---|---|---|
| `etf_tracker_v9/*` | 변경 없음(`docs/base.json`·`etf.db`·`docs/index.html` 제외만) | — | 자동 시험 0. import 스모크: `cd etf_tracker_v9 && python -c "import collectors, dash, market, render, report, themes, tracker, live_update, adapters.ace, adapters.hanaro, adapters.koact, adapters.plus, adapters.rise"` 성공. `verify.py` 는 import 하는 순간 `etf.db` 를 열고 네이버를 부르는 스크립트라 스모크에서 뺐다 |
| `dart-report/tests_smoke.py` | 맨 앞에 `os.makedirs('out', exist_ok=True)` 한 줄 | KBJ `.gitignore`(`legacy/**/out/`)로 새 클론에는 `out/` 이 없어 `wb.save('out/_smoke.xlsx')` 가 실패한다. 경로 준비만 추가 | 출력 `built. quarters= 14 annual= 4 bridge steps= 9` 원본과 같음. 스모크 데이터는 원래 합성이라 DART 실데이터 fixture 는 없다 |
| `dart-report/` 나머지(`dartreport/`·`config/mapping.yaml`·`run.py`·`app.py`·문서) | 변경 없음 | DART = 공개 등급 | — |
| `기능목록.md`·`운영가이드.md` | 변경 없음(개인 경로·호스트·키 없음 확인) | — | — |

## 7. 개인·운영 정보 점검

옮긴 파일 전체(board 제외)에 대해 grep — 결과 0건(IFRS 태그 이름 1건은 오탐):
홈 디렉터리 절대경로(macOS·리눅스)·Render 호스트명, 텔레그램 봇 토큰 형태(`숫자:문자 30+`)·JWT·`sk-ant`·`AKIA`·KIS 앱키 형태(`PS…`)·
40자리 hex·긴 base64, 계좌번호 형태(`8자리-2자리`, `CANO=`·`ACNT_PRDT_CD=`), 이메일 주소, `.env*`·`*.key`·`*token*.json`·`*.db` 파일.
`운영가이드.md` 의 `1234567890:AAHxxxxxxxxxxxxx`·`Id: 123456789` 는 원본에 있던 설명용 자리표시다.

## 8. 필요한 파이썬 패키지

pandas(2.3.3 에서 확인, 3.x 는 미확인), numpy, requests, lxml, beautifulsoup4, PyYAML, openpyxl, matplotlib, fonttools
(`monitor/flow/chart.py` 가 직접 import), streamlit(`dart-report/app.py` 만), playwright(선택 — kr `test_page`·flow `capture.py`,
없으면 kr 시험 1건 skip), anthropic(board 패키지 의존. monitor·flowlab 시험이 board 를 import).

## P2. KBJ 정본으로 재배선 (묶음 H, 2026-10-07)

board 는 `board/MIGRATION.md` 'P2' 절. 그 밖:

| 파일 | 바꾼 것 |
|---|---|
| `etf_tracker_v9/tracker.py` | `send_telegram` → `legacy_send(…, source="et.etf", kind="etf.report")`(#34 — 분할·429 대기는 notifier), `send_telegram_file` **삭제**(#35 — 부르는 곳 없음), `doctor` 의 getMe·시험 발송 → notifier `webhook_status()`(#36 — 메시지를 보내지 않는다), `prev_trading_day` 는 KBJ 캘린더의 직전 거래일과 DB 마지막 스냅샷을 대조해 다르면 알린다(비교 대상은 DB 스냅샷 그대로) |
| `etf_tracker_v9/dash.py:kst_now` | `kbj.core.time.now_kst()` 를 같은 고정 오프셋 KST 로(벽시계 한 곳 — 설계 §7.2) |
| `monitor/flow/telegram.py` | `send_photos` → `legacy_send_media(…, source="et.flow", kind="flows.report")`(#39), `send_report` 본문도 `kind="flows.report"`, 봇 토큰·주소 상수 삭제 |
| `monitor/flow/kissrc.py` | 메시지만: '발급/캐시 확인' → '토큰 읽기 확인(KBJ auth)'(K5 — 토큰은 board `kis.token()` 이 읽기만) |
| `monitor/flow/tests/test_run.py::전송.test_토큰이_없으면_실패다` | 단언 하나를 바꿨다: 봇 토큰은 notifier 만 가지므로 `send_photos` 의 관문은 토큰이 아니라 파일이다 — `'TELEGRAM_BOT_TOKEN' in why` → `'없다' in why`(실패 반환 단언은 그대로). 시험 수 129 그대로 |
| `dart-report/dartreport/client.py` | KBJ `kbj.data.public.dart.client.DartClient` 다시 내보내기 + 옛 생성자 호환(`DartClient(api_key=None, cache_dir='.cache', throttle=0.12, timeout=30)` — throttle 은 공용 리미터가 대신). 키는 인자 또는 `KBJ_DART_API_KEY`. 차이: `DartError.status` 는 HTTP 상태(DART 코드는 `.code`), `disclosures` 는 20쪽을 넘으면 자르지 않고 `DartError` |
| `flowlab/probe_market_official.py` | **삭제**(KRX OpenAPI 직접 조회 진단 — 설계 §9.4 [제안]. selftest 44 영향 없음) |

남긴 것: `monitor/flow/narrative.py:prev_trading_day` — 리포트 데이터 안의 직전 날짜(데이터가 있는 날)라
캘린더로 바꾸지 않았다(설계 §7.2 의 ET `trading_days` 와 같은 이유, 시험이 합성 날짜로 고정한다).
`monitor/kr`(K4)는 board `kis` 를 타므로 코드 변경 없음.

시험: kr 94·flow 129·flowlab 44/44·dart-report built(14·4·9)·etf_tracker_v9 13 모듈 import — P1 과 같다.
