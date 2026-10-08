# legacy/etf_traker/board — P1 이식 기록

KBJ P1(`docs/PLAN.md` §8, `docs/conflict_map.md` §3)에서 ETF-Traker 의 `board/`(미국장 `board/us/` 포함)를
옮긴 기록이다. 원칙은 **경로·import 만 고친다**이고, 예외는 ADR 0001 U3 의 합성 교체와 개인정보·운영정보
제거뿐이다. 바꾼 파일은 전부 아래 2장에 파일별로 적었다. 적지 않은 파일은 원본과 바이트까지 같다.

- 원본: ETF-Traker 스냅샷 `0014f57`(2026-10-06) — P0 작업 환경의 읽기 전용 사본(레포 밖)
- 이식 위치: `legacy/etf_traker/board/`, 시험이 읽는 워크플로 5개는 `legacy/etf_traker/.github/workflows/`
  (레포 루트 `.github` 가 아니다 — 실행되지 않는다. ADR 0003 2장 6번)
- 작업일: 2026-10-06

## 0. 시험 결과

명령은 원본과 같다. 작업 디렉터리만 `legacy/etf_traker` 다(레포 루트에서 `board.*` 로 import 하는 구조라 그대로 둔다).

```
cd legacy/etf_traker
python -m unittest discover -s board/tests -t .
```

| 어디서 | 결과 | 비고 |
|---|---|---|
| 원본 사본(`/tmp/p1/src-etf-board`, ET 레포 전체) | **1,319 OK**, skip 0 | 원본 `docs/`(발행된 보드 사이트 26.8MB)가 있어 보관 날짜 시험이 돈다 |
| 원본 사본에서 `docs/` 만 치운 것 | **1,319 OK (skipped=1)** | 아래 skip 과 같은 것임을 확인하려고 돌렸다 |
| 이식 뒤 `legacy/etf_traker` | **1,319 OK (skipped=1)** | 실패·오류 0 |

skip 1건은 `test_demo_isolation.DemoSiteTest.test_published_archive_keeps_only_recent_dates`('아직 발행된 사이트가
없다')다. 원본 시험에 원래 있는 `skipTest` 이고, ET `docs/`(생성물 — 로그인 등급 시세 포함)는 conflict_map §3.1 에
따라 옮기지 않았다. 시험을 고치지 않았다.

- Python 3.12. 시험이 실제로 쓰는 외부 패키지는 `requests`·`PyYAML`·`anthropic`·`openpyxl` 넷뿐이다 — 이 넷만 깐
  전용 venv(`/tmp/p1/venv-etf-board`: requests 2.34.2·PyYAML 6.0.3·anthropic 1.11.0·openpyxl 3.1.5)와 P0 통합
  환경(`/tmp/p0test/venv-312`) 둘 다에서 같은 결과다. `yfinance` 는 `tools/dashboard_brief_preview.py` 만 쓰고
  (없으면 그 도구가 사유를 찍는다) 시험에는 필요 없다.
- 네트워크 없이 돈다(원본과 같다).
- 시험이 끝나면 `board/state_us/20260828/`(미국장 시험이 쓰는 합성 산출)이 생긴다. 원본에서도 같고, `board/.gitignore`
  의 `state_us/*/` 가 막아 커밋되지 않는다. 최종 점검에서 KBJ 루트 `.gitignore` 에도 `legacy/**/state_us/` 를 넣었다(폴더 바로 아래 파일까지 막는다 — `tests/test_gitignore.py`).

## 1. 옮기지 않은 것

| 원본 경로 | 이유 |
|---|---|
| `board/state/xdigest/`(5일치 30파일, 커밋돼 있던 것) | 블루스카이 게시물 원문·발송본 — 개인 데이터(U3). 시험용으로는 합성 state 를 만들었다(2장) |
| `board/state/guru/`(13파일, `cost.json` 포함) | 구루 조사 결과·월 비용 — 개인 데이터(U3) |
| `board/state/` 의 나머지(토큰 캐시 `.kis_token.json`·텔레그램 인박스 `inbox.json` 등) | 스냅샷에 없었다. 있었어도 산출물·비밀이라 옮기지 않는다. KBJ `.gitignore` 의 `legacy/**/state/` 가 막는다 |
| `*.db`·`*.db-wal`·`*.db-shm` | 스냅샷에 없었다. 같은 이유로 제외 규칙만 걸었다 |
| `board/us/ci-out/`(6파일) | Nasdaq 실데이터 CI 결과 — 로그인 등급 |
| `board/notes/2026-09-21.yaml` | 사용자가 쓴 일일 노트 — 개인 데이터(U3). 시험용 합성 노트를 만들었다(2장) |
| `board/docs/reference-comment.md` | 사용자 원고 — 개인 데이터(U3) |
| `.github/workflows/` 중 11개(`artifacts-gc`·`bok`·`daily`·`dashboard-brief-preview`·`flow`·`frgn-probe`·`kis-futures-probe`·`kr`·`pages`·`preview`·`report`) | `board.run` 을 부르지 않아 board 시험이 읽을 것이 없다. `test_workflow_modes.test_모든_워크플로를_훑는다` 는 폴더의 yml 을 전부 읽는데, 이 11개에서는 플래그가 하나도 안 나온다 — 원본 16개에서 모은 플래그 42개와 옮긴 5개에서 모은 플래그 42개가 같다(직접 대조) |
| `__pycache__/` | 생성물 |

## 2. 파일별 변경

### 2.1 합성 교체 (U3) — 생성기 `tests/synthetic_fixtures.py`

합성 데이터는 전부 `board/tests/synthetic_fixtures.py` 가 결정론적으로(시드 고정) 만든다. 기대 텍스트는 손으로
맞추지 않았다 — board 렌더러(`xdigest.render.compose`)와 랭킹 엔진(`engine.rankings`)이 만든 값이다.

```
cd legacy/etf_traker
python -m board.tests.synthetic_fixtures            # 다시 만든다
python -m board.tests.synthetic_fixtures --check    # 디스크와 같은지만 본다(다르면 종료 코드 1)
python -m board.tests.synthetic_fixtures --doc      # docs/XDIGEST.md 9장 예시 블록도 다시 쓴다
python -m board.tests.synthetic_fixtures --measure  # 예시·분할 길이(XDIGEST.md 1장 숫자)를 찍는다
```

`--check` 는 `PYTHONHASHSEED` 0·1·12345 와 두 venv 에서 모두 '같다' 였다. 계정 이름은 전부 지어낸 것이고(`fx` 가
들어간다), 회사는 '가상 ○○사'·`가상NNN…` 으로 적었다. 실명은 **시험 코드가 이름으로 찾는 종목만** 남겼다
(삼성중공업·네오이뮨텍·제닉 — `test_telegram`, 한미반도체 — `test_xdigest_render.NAMES`, 소프트캠프·선도전기·
KBI메탈 — `test_note`). 그 이름에 붙은 값·문장은 전부 합성이다.

| 파일 | 원본 | 바꾼 것 | 시험 영향 |
|---|---|---|---|
| `tests/fixtures/rankings_sample.json` | 보드 산출 모양의 랭킹(실제 종목명·수치) — 로그인 등급 | 합성 유니버스 626종목(13섹터, 시드 20260826)을 `engine.rankings.sector_board`·`stock_board`·`mark_cross` 로 계산. 원본과 같은 계약(키·자료형): 섹터표 2(1d·7d)×13섹터, 종목표 2×15행, 교집합 7, null 셀 1행, `source: synthetic` | `test_telegram` 그대로 통과 |
| `tests/fixtures/rankings_tg_sample.json` | 경계 조건 샘플(실제 종목명 섞임) | 같은 경계 조건을 손 설계로 다시 만들고 값은 난수(시드 20260827): 섹터 1등 종목 등락률 null(삼성중공업), cross 행 null 셀(네오이뮨텍), 원값 null·표시 `0.00%`(제닉), 마크다운 문자가 든 이름(`가상ETF 2차전지_소재[합성]*`). 등락률 문자열은 끝자리가 0 이 아니게 둔다(`+41.20%` 는 '0%' 를 품어 `test_null_cell_is_dash_not_zero` 의 뜻이 흐려진다) | 그대로 통과 |
| `tests/fixtures/xdigest_render_posts.json` | 블루스카이 실제 계정 핸들 12개·표시이름(본문은 원래도 `[픽스처] …` 자리표시) | 가상 계정 12개로. 게시물 156건, 번호·시각·질의·언어·창 규칙은 원본과 같다(인용 45건 + 채움 111건, 120~125·130~133 번은 채움) | 그대로 통과 |
| `tests/fixtures/xdigest_render_facts.json` | 실제 다이제스트(제3자 게시물 요약·인용) | `synthetic_fixtures.EXAMPLE`(9장 합성 예시)에 원본과 같은 허용 차이를 적용해 만든다: 소주제 8·사실 32·단독 12·인사이트 5, 검증 탈락 2줄 제외, 평가 문구 잘라냄, 종목코드 제거, 소주제·단독 순서 뒤집기, `counts.analyzed` 68 | 그대로 통과 |
| `tests/fixtures/xdigest_render_expected.txt` | 렌더 결과(위 실제 다이제스트) | `xdigest.render.compose(facts, posts, {}, names={'한미반도체': '042700'})` 출력. 2통(3,062자 / 1,792자) | `TestExample`·`TestSplit` 등 그대로 통과 |
| `tests/fixtures/xdigest_render_sections_expected.txt` | 같음(별도 구획) | 시험 파일의 `SEC_POSTS`·`SEC_FACTS`·`_sec_fixture` 를 그대로 떼어 실행해(ast) 렌더. 2통(3,077자 / 2,249자) | `TestSections` 그대로 통과 |
| `tests/fixtures/note_2026-09-21.yaml` (신규) | `board/notes/2026-09-21.yaml`(개인 노트) | 같은 스키마(date·verdict·index·macro·themes 6·picks w52 3/watch 5/surge 3)의 합성 노트 | `test_note` 그대로 통과(경로만 바뀜 — 2.2) |
| `tests/fixtures/xdigest_state/20260921/{posts,facts}.json·digest.txt·sent.json` (신규) | `board/state/xdigest/`(실제 게시물·발송본) | 위 합성 게시물·facts·렌더와, `xdigest.send.send_parts` 로 만든 발송 기록(시각은 고정값) | `test_xdigest_preview` 가 '실제 state' 자리로 쓴다(2.2) |
| `tests/synthetic_fixtures.py` (신규) | — | 위 전부의 생성기. `unittest discover` 의 `test*.py` 패턴에 안 걸려 시험 수는 그대로 1,319 | 없음 |
| `docs/XDIGEST.md` | 9장 예시 = 실제 다이제스트 원문(제3자 게시물 요약·인용, 실제 X 핸들), 본문 곳곳의 실제 핸들(계정 목록·실측표·허용 차이 설명) | ① 실제 핸들 12개를 가상 이름으로 일괄 치환(X 표기·블루스카이 표기 모두, 넷째 구획 표시명 포함) ② 9장 예시 블록을 생성기 출력으로 교체, 제목의 '원문 그대로' 를 '합성 예시' 로 ③ 1장 '4,096자 분할' 의 측정값을 합성 예시 기준으로 다시 잼(5,137→4,905자, 2통 2,903/2,233→3,062/1,792자, 구획 픽스처 3,139/2,390→3,077/2,249자 — 원래 값도 괄호에 남김) ④ 머리에 이식 주석. 계정별 실측 값(팔로워 수 등)은 원문 그대로라 가상 이름에 붙어 있다 | `test_xdigest_render.test_example_diff_is_zero` 가 9장을 직접 읽는다 — 합성 예시와 합성 픽스처로 그대로 통과 |
| `docs/NOTE.md` | 1장 입력 예시·3장 렌더 예시가 실제 노트 발췌(사용자 판단 문장) | 합성 노트와 같은 내용의 합성 예시로 바꾸고 표시. D8(노트 커밋) 아래에 KBJ 에서는 옮기지 않았다는 주석 | 없음(시험이 안 읽는다) |
| `docs/COVERAGE.md` (검증 단계에서 고침) | 표 첫 열 "레퍼런스의 문장" 35행이 사용자 원고(`docs/reference-comment.md`, 옮기지 않음)의 문장을 그대로 인용 — 원고 본문의 약 28% | 첫 열을 "레퍼런스 문장의 유형"으로 바꾸고 각 행에 인용 대신 문장 유형만 적었다(예: "지수 시가→장중 저점→고점→종가(등락률) 한 줄"). 둘째·셋째 열(필요한 것·상태)과 결론 절은 원본 그대로. 머리에 이식 주석 | 없음(시험이 안 읽는다) |
| `web/prototype.html` (검증 단계에서 고침) | '일간 코멘트' 탭 본문(장 흐름·원전·전일 코멘트 검증·내일 관전 단락)과 '섹터 뉴스' 탭 요약 10줄이 사용자 원고 문장을 거의 그대로 옮긴 것 — 원고 본문의 약 25% | 그 단락·요약을 `[합성 예시]` 로 시작하는 자리표시 문장으로 바꿨다(무엇을 적는 자리인지만). 탐지 `cite` 줄·표·CSS·스크립트는 그대로. 본문 머리에 HTML 주석 | 없음(시험이 안 읽는다. `web/render.py` 는 이 파일을 디자인 참고로만 언급한다) |

### 2.2 시험 파일 (경로·합성 교체만 — assert 는 하나도 바꾸지 않았다)

| 파일 | 바꾼 것 | 왜 | 시험 영향 |
|---|---|---|---|
| `tests/test_note.py` | `SAMPLE` 경로 `board/notes/2026-09-21.yaml` → `board/tests/fixtures/note_2026-09-21.yaml`, 머리 docstring 에 두 줄 | 개인 노트를 옮기지 않았다(1장) | 같은 시험 21개가 그대로 돈다(노트 파일을 읽는 것은 그중 7개) |
| `tests/test_xdigest_preview.py` | `setUp` 이 합성 state(`fixtures/xdigest_state`)를 임시 디렉터리에 복사해 `BS.XSTATE`·`XR.STATE` 로 두고 그것을 '실제 state' 로 기억한다. `tearDown` 은 원래 값으로 되돌리고 임시 디렉터리를 지운다. `import shutil, tempfile` | 원본은 레포에 커밋된 `board/state/xdigest`(개인 데이터)를 '실제 state' 로 봤다. 그 디렉터리가 없으면 '전후 목록이 같다' 가 빈 목록끼리 비교가 된다 — 합성 state 를 깔아 비교 대상이 있게 했다 | 두 시험의 assert 그대로. 첫 시험의 전후 비교가 빈 목록이 아니라 실제 날짜 디렉터리로 돈다 |
| `tests/test_xdigest_render.py` | 9장 넷째 구획 제목 리터럴 1개를 합성 예시의 표시명 `'■ FXKAN의 24시간 인사이트'` 로 | 9장 예시의 넷째 구획 제목이 실제 계정에서 온 표시명이었다. 합성 예시의 가상 표시명으로 맞춤 | 없음(같은 자리에서 예시를 자른다) |
| `tests/test_xsource.py` | 인라인 RSS 픽스처의 계정 이름(`<title>`·링크 2곳) → `ChipWireFx` | 실제 X 계정 핸들(U3) | 없음 — assert 는 상태 id·본문만 본다 |

### 2.3 개인정보·운영정보 제거

| 파일 | 바꾼 것 | 왜 |
|---|---|---|
| `ingest/xsource.py` | 실제 X 계정 핸들 튜플(`SAMPLE` 2개·`DIGEST_ACCOUNTS` 12개)을 지우고 환경변수 `XSOURCE_SAMPLE_ACCOUNTS`·`XSOURCE_DIGEST_ACCOUNTS`(쉼표 구분)에서 읽게 했다(값은 예시에도 넣지 않았다). 비어 있으면 X 경로 실측 1~3번을 건너뛰고 '기록' 줄 하나를 남긴다(`SAMPLE[0]` 을 부르는 nitter·RSSHub 반복 두 곳에 `if SAMPLE` 조건, `probe_table` 머리에 안내 줄). 계정 실측(`probe_bsky` 2번)은 빈 목록이면 원래 코드대로 0건으로 지나간다 | 계정 핸들은 개인 데이터(U3). 이 함수들은 실측 도구(`--x-probe`·`--bsky-probe`, 원본 `board.yml` 의 `mode=x-probe`·`bsky-probe`)라 시험이 부르지 않는다 — 시험 영향 없음. 실측을 다시 돌리려면 두 환경변수를 넣는다 |
| `docs/GITHUB.md` | 개인 이메일 → `<볼 사람 이메일>`, Pages 주소·저장소 주소·`repository_dispatch` API 주소의 GitHub 계정 → `<owner>` | 개인 이메일·운영 주소 |
| `docs/LOCAL.md`, `docs/DASHBOARD.md` | `git clone` 주소의 GitHub 계정 → `<owner>` | 같음 |
| `docs/DECISIONS.md` | `git clone` 한 줄의 GitHub 계정 → `<owner>`, 대상 디렉터리(작업 머신의 절대 경로) → `~/ETF-Traker` | 절대 로컬 경로·계정 |
| `.env.example` | `KIS_ACCOUNT=   # 계좌번호 8자리-2자리 …` 의 줄 끝 주석을 윗줄 주석으로 옮김(값은 원래도 비어 있다) | KBJ `scripts/check_public_safety.py` 의 `env_secret_value` 규칙이 줄 끝 주석을 값으로 읽어 걸렸다. 허용 목록(레포 루트)을 건드리지 않고 이 파일에서 풀었다 |
| `../.github/workflows/flowlab.yml` | `flowlab verify` 의 `--ref 003490@…:inst5=…,frgn5=…`·`--ref-event …` 두 줄(실측 수급 수치)을 지우고 그 자리에 주석 | 로그인 등급 실데이터. 이 파일은 실행되지 않고, board 시험은 `board.run --financials --live` 플래그만 읽는다(`--financials` 는 이 파일에만 있어 같이 옮겼다) |

`board.yml`·`xdigest.yml`·`us-board.yml`·`us-search.yml` 은 원본 그대로다(키는 `secrets.*` 이름만 있고 값·호스트명·
개인 정보가 없다).

## 3. 그대로 둔 것 — 검토했지만 바꾸지 않았다 [확인 필요]

P1 은 경로만 고치는 단계라 아래는 손대지 않고 적어만 둔다.

1. 사용자 원고(`docs/reference-comment.md`, 옮기지 않음)를 대량 인용하던 `docs/COVERAGE.md`·`web/prototype.html` 은
   검증 단계에서 고쳤다(2.1). 남은 것은 짧은 조각이다 — 코드 docstring(`ingest/flows.py` 등)·프롬프트 예시·시험 입력·
   `knowledge/notes.yaml`·`events.yaml` 주석·`docs/DECISIONS.md` 에 원고 문장 한두 개씩(합치면 원고 본문의 약 23%,
   파일마다 0.5~5%). 시험 입력(`test_verify`·`test_telegram`)이 들어 있어 P1 원칙상 두었다. 지울지는 메인 결정이 필요하다.
   `README.md:128`·`CLAUDE.md:24`·`docs/DECISIONS.md`·`docs/COVERAGE.md` 의 그 파일 링크는 끊긴다.
   `web/prototype.html` 의 표·히트맵 값(2026-08-26 실제 종목명과 등락률·거래대금 등 화면 원안용 숫자)도 그대로 두었다.
2. `CLAUDE.md` 1장에 사용자 직무 한 단어("버이사이드 리서치")가 있다.
3. 시험·코드의 인라인 예시값: 코스피 종가(`6,808.21` 등 — `test_verify`·`test_telegram`·`writer/verify.py` 외), 무역협회
   HBM 수출단가(`$73.39`) 같은 공개 기사 수치, `test_xdigest_analyze`·`test_xdigest_verify` 의 줄인 가상 핸들
   (`pequity.bsky.social` 등 — 실제 핸들 아님). U3 대상 목록(픽스처 파일)에 없어 그대로 뒀다.
4. `docs/XDIGEST.md` 3-3 절·`xdigest/verify.py` docstring 의 인명 두 개(게시물에 나온 애널리스트·교수 이름 — 검증 규칙
   설명용 예).
5. `config/guru.yaml` — 구루 명단(공인)과 첫 실행 비용 추정 한 줄(주석). 조사 결과·비용 파일(`state/guru`)은 옮기지 않았다.
6. `knowledge/`·`us/knowledge/` — 자체 분류 사전(공개 등급, 지시대로 그대로).

## 4. 레포 규칙과의 관계

- `.gitignore`(루트): `legacy/**/state/`·`*.db`·`*.xlsx` 등에 걸리는 파일은 이식본에 없다. 합성 state 는
  `tests/fixtures/xdigest_state/` 라 `state/` 규칙에 안 걸린다(`git status --ignored` 로 확인 — 이식한 217파일(이 문서 포함) 모두 추적 대상).
- `scripts/check_public_safety.py`: 이식 뒤 레포 전체 통과(발견 0건, 허용 0건).
- 별도 grep(키·토큰 형태 — 텔레그램 봇 토큰·sk-ant·GitHub 토큰·AWS 키·JWT·PEM·KIS 앱키/시크릿, 맥·윈도우 사용자 홈 경로,
  리눅스 작업 머신 홈 경로, Render 호스트명, 계좌번호 8-2, 개인 이메일·GitHub 계정, 실제 핸들 12개): 0건(이 파일 포함).
- ruff·pyright 는 ADR 0003 에 따라 `legacy/` 를 보지 않는다. 생성기도 board 의 원래 문체(단일 따옴표·한글 주석)를 따랐다.

## 추가 정리 (메인, P1 커밋 직전)

| 파일 | 무엇을 | 왜 | 시험 영향 |
|---|---|---|---|
| `board/web/prototype.html` | 이식본에서 **삭제** | 화면 원안 표·히트맵에 2026-08-26 실제 종목명과 등락률·거래대금 숫자(로그인 등급 시세로 볼 수 있음)와 사용자 원고 조각이 남아 있었다. 시험은 이 파일을 읽지 않는다. 화면 디자인은 KBJ P3 의 MD6형 SPA 가 대신한다 | 없음 |

`render.py`·`README.md` 의 prototype.html 언급은 원본 설명이라 그대로 둔다(파일은 원본 레포에만 있다).

## P2. KBJ 정본으로 재배선 (묶음 H, 2026-10-07)

설계 `docs/p2_design.md` §1.9·§3.7·§3.8(K2·K3·K6)·§5.8~§5.10·§7.2. KIS·KRX·DART 는 KBJ 논리 URL 브리지
(`kbj.data.legacy_bridge`)로만, 텔레그램은 KBJ notifier 대기열로만, 인박스는 notifier 웹훅이 쌓은 것을
읽기만 한다. 키는 KBJ 설정(`KBJ_*`)에서 브리지·notifier 가 넣는다 — 옛 이름(`KIS_APP_KEY`·`KRX_API_KEY`·
`DART_API_KEY`·`TELEGRAM_*`)은 읽지 않는다(DART 는 옛 이름이 있어도 키 값은 버린다).

### 파일별

| 파일 | 바꾼 것 |
|---|---|
| `ingest/kis.py` (K3) | `REAL`·`VTS`·`TOKEN_CACHE`·`_cached_token`·`_save_token`·발급 POST 삭제. `base()` = `"kis:"`, `session` = 브리지 세션, `token()` 은 KBJ auth 토큰 읽기만(없으면 `Fetch('… auth 대기')`), `_headers` 는 `tr_id`·`custtype`·우선순위(P3)만 — 토큰·앱키는 브리지가. `probe()` 는 토큰 앞자리도 찍지 않는다 |
| `ingest/krx.py` | `BASE = "krx:"`, 세션 = 브리지(인증키는 브리지), `_isu_to_code` → KBJ `kbj.data.private.krx.stocks.isu_to_code` 다시 내보내기, `probe()` 키 확인 `KBJ_KRX_API_KEY` |
| `ingest/dart.py` | `BASE = "dart:"`, 세션 = 브리지, `_key()` 는 '설정됨' 표시값(키 원문 아님 — 브리지가 버리고 KBJ 키를 넣는다), `has_key()` 새로. `STATUS_KO`·`KINDS`·`kind_of` → KBJ `kbj.data.public.dart` 다시 내보내기. `disclosures_for` 등 나머지 함수 본문은 그대로(아래 '남긴 시험') |
| `ingest/financials.py` | `fnlttMultiAcnt` 세션 → `dart.session()`(브리지), opendart 리퍼러 삭제 |
| `ingest/http.py` | `scrub`·`why`·`SECRET_PARAMS`·`BODY_*` → KBJ `kbj.data.http` 다시 내보내기(가린 자리는 `***`). `get`·`Fetch`·`session`·`gather`·`pick`·`num` 은 그대로(ET 예외 계층) |
| `ingest/tg_inbox.py` (#33) | getUpdates·getWebhookInfo·offset 파일·봇 토큰 읽기 삭제. `drain(chat_ids, inbox_path, *, store=None, …)` 은 KBJ `kbj.services.notifier.inbox.read_items`(notifier 가 `prv_alerts.tg_inbox` 에 쌓은 것)를 읽어 `state/inbox.json` 계약 그대로 쓴다. 파싱 함수는 KBJ 정본 다시 내보내기. 대화방은 `KBJ_TELEGRAM_INBOX_CHAT_IDS`·`KBJ_TELEGRAM_CHAT_ID` |
| `ingest/triggers.py` | DART 사용 가능 판정 `_dart_ready()`(옛 이름 또는 `KBJ_DART_API_KEY`), getWebhookInfo(#46) → `webhook_status()` |
| `report/telegram.py` (#22) | `send` → `legacy_send(…, source="et.board", parse_mode=…, kind=…)`(기본 parse_mode 'Markdown' 그대로, `kind` 인자 추가), `send_document` → `legacy_send_document(…, kind='board.files')`, `check` → notifier 웹훅·하트비트 상태(getMe·getChat 은 notifier), `_split` → `kbj.services.notifier.format.split_text`. `token`·`chat_id` 인자는 받고 무시(경고 로그). `_cred`·`_why`·`_safe_err`·텔레그램 주소 상수 삭제 |
| `run.py` | `cmd_send` 가 종류를 넘긴다: rankings·draft·signals·backtest/search/screen(`board.manual`)·note·files(#23~#28). `cmd_us_send` 의 본문 발송은 `legacy_kinds`(cmd_us_send → `board.us` — notifier.yaml 요청)에 맡기고 첨부만 `kind='board.us'`(발송 대역 시험이 옛 호출 모양을 고정한다). `_send_files(only_fresh)` 는 '기준일 ≠ 오늘이면 휴장 추정' 을 KBJ 캘린더로 명시(휴장일이면 그렇다고 적고, 거래일인데 기준일이 다르면 '오늘 보드가 아직 없다'). `cmd_inbox` 는 `KBJ_DATABASE_URL` 이 있을 때만 인박스를 읽는다(없으면 0 으로 건너뜀) |
| `engine/build.py`·`engine/db.py`·`xdigest/analyze.py` 의 `now_kst` | 본문 → `kbj.core.time.now_kst().isoformat(timespec='seconds')`(벽시계 한 곳 — 설계 §7.2. 문자열은 같다: `+09:00`) |
| `tools/dashboard_brief_preview.py` (K2)·`tools/probe_kis_futures.py` (K6) | **삭제**. 워크플로 사본(`dashboard-brief-preview.yml`·`kis-futures-probe.yml`)은 P1 에서 옮기지 않았다 — `test_workflow_modes` 영향 없음 |

### 시험 — 승격으로 지운 것(kbj 쪽에서 같은 단언이 돈다)

| 지운 시험 | 수 | kbj 쪽 |
|---|---|---|
| `tests/test_tg_inbox.py` 옛 39개 | 39 | 27 승격(Links 9·Whitelist 4·DedupExpiry 4·OEmbed 7·Other 3 → `tests/unit/notifier/test_inbox.py`). 12 는 옛 getUpdates 경로 모양이라 그대로는 지웠다(Paging 4·Webhook 3·CmdInboxExit 2·ChatIdsFromCreds 1·파일 손상 2) — 이 중 legacy 에 남은 동작(손상 inbox·`cmd_inbox`·대화방)은 아래 '고쳐 둔 것' 에서 다시 썼다 |
| `tests/test_telegram.py` — TestSplit·TestSend·TestCheckDestination·TestPlainTextMode | 12 (2·5·3·2) | `tests/unit/notifier/test_format.py`(2)·`test_telegram_api.py`(10) |
| `tests/test_send_files.py` — SendDocumentTest | 2 | `tests/unit/notifier/test_telegram_api.py` |
| `tests/test_http_reason.py` — WhyTest·NoBacktrackTest | 10 (6·4) | `tests/unit/data/test_http.py` |
| 합계 | **63** | |

남긴 시험: `test_http_reason.py` 의 GetTest 4(ET `get` 은 그대로)·DatagoKeyFormTest 5(ET `datago.py` 는
P2 범위 밖 — 묶음 C 의 `kbj.data.datago` 로 바꾸는 것은 data.go.kr 기준선 정리 때), `test_dart_for.py` 7
(ET `disclosures_for` 본문을 그대로 두었다 — 브리지로만 바꿈. kbj 에도 같은 단언 7개가 있다).

새로: `tests/test_kbj_bridge.py`(+1 — `kis.token()` 이 발급하지 않는다, 주소는 논리 URL, 브리지는 직접
주소·POST 를 받지 않는다).

고쳐 둔 것(독립 검증에서 더함): `tests/test_tg_inbox.py` 를 **legacy 에 남은 인박스 코드** 시험 10개로 다시
썼다(+10). 승격한 27개는 kbj notifier 정본을 시험하고, legacy `drain`·`merge`·`read_inbox`·`inbox_chat_ids`·
`run.cmd_inbox` 는 그대로 돌므로 그 시험까지 지우면 legacy 쪽 단언이 0 이 된다. 옛 단언은 그대로 두고 갱신을
주는 자리만 저장소 대역(`FakeStore` — `read_items` 가 부르는 `items(since)`)으로, 자격 읽기는 `Settings`
대역으로 바꿨다: Drain 2(계약 모양·저장소에 묻는 기간, 대화방 없음 → Fetch — 옛 Whitelist 1 의 단언),
DedupExpiry 5(중복·보존 기간·나중 실행 만료·`keep_days`·손상 파일 `.bad` — 옛 단언 그대로), CmdInboxExit 2
(설정 없음 → 0, 수집 실패 → 1), ChatIdsFromCreds 1(목록이 하나짜리보다 우선). 그래서 옛 39개 중 진짜로
없어진 것은 getUpdates·offset 에 묶인 8개(Paging 4·Webhook 3·손상 offset 1)뿐이다.

결과: `scripts/test_legacy.sh board` **1,267 실행**(P1 1,319 − 63 + 1 + 10), 1 건너뜀(그대로).

## P3. 신고가 엔진 승격 — kbj 정본으로 (묶음 E1, 2026-10-07)

설계 `docs/p3_design.md` §1.3·§4.1·§8.1, D-P3-10·11, ADR 0009(초안). 순서: ① shim 전 이 엔진으로 합성 골든을
캡처(`tests/golden/board/`, legacy 커밋 `4ea5b7f`) → ② `kbj/engines/board` 로 함수 본문 그대로 이식 → ③ 골든 비교
30일 통과(허용오차) → ④ 이 폴더의 엔진을 shim 으로.

### 파일별

| 파일 | 바꾼 것 |
|---|---|
| `engine/{newhigh,aggregate,kinds,themes}.py` | 본문 삭제 → `from kbj.engines.board.<m> import *` + `__all__`(같은 함수 객체) |
| `engine/rankings.py` | 다시 내보내기 + `load_taxonomy()`(레포 루트 `config/knowledge/sectors.yaml`)·`build(asof, cfg, log)`(state 파일을 읽어 kbj `build_rankings` 에 넘긴다 — 시각은 `kbj.core.time.now_kst`). `_fmt` = kbj 공개 별칭 `format_cell` |
| `engine/build.py` | 계산 본문 삭제. `run(db_path, asof, cfg, log)` 은 sqlite 에서 엔진이 읽던 것(스냅·일봉·스칼라·섹터·전일 라벨·공시 대조·run_log 실패·state 의 전일 newhigh/rankings·market.json)을 모아 kbj `compute_day` 를 부르고, label 표·run_log(`BoardDay.steps`)·state 파일 5개를 쓴다. `state_dir`·`read`·`write`·`sync_label_kinds`·`_prev_labels` 는 그대로. 잠정 종가 문구는 "네이버 16:07 값" → "KIS 마감값(잠정)" |
| `engine/config.py` | `load()` = `config/settings.yaml` + 레포 루트 `config/board.yaml`(엔진 절 — 파일에 같은 절이 있으면 파일이 이긴다). `themes()`·`KNOWLEDGE` 는 `config/knowledge` |
| `engine/facts.py` | `eok` → kbj `rankings.eok` 다시 내보내기(같은 규칙 하나) |
| `config/settings.yaml` | 엔진 절 10개(newhigh·proximity·volume·resistance·giveback·themes·display·integrity·detect·rankings)를 `config/board.yaml` 로 옮기고 지웠다(주석 포함 그대로 옮김) |
| `knowledge/{themes,sectors,sector_map}.yaml` | 레포 루트 `config/knowledge/` 로 옮겼다(자체 사전 — 공개 등급). `events`·`notes`·`xdigest_names` 는 그대로 |
| `classify/sectors.py`·`writer/prompts.py` | 사전 경로 `ROOT/knowledge` → `engine.config.KNOWLEDGE` |

### 시험 — 승격(kbj `tests/unit/engines/board/`, import 경로만)

| legacy 파일 | 옮긴 것 | 수 | legacy 에 남긴 것 |
|---|---|---|---|
| `test_newhigh.py` | 전부(파일 삭제) | 35 | — |
| `test_turnover.py` | TurnoverTest | 6 | NaverUniverseTest 1 |
| `test_consistency.py` | ConsistencyTest·HistRefContainsWindowsTest | 20 | LabelKindSyncTest 2(sqlite 라벨 표 — shim) |
| `test_seeds.py` | Renames·Unresolved·RealFile | 10 | BannerWording 3(web.render) |
| `test_rankings.py` | TestSectorBoard·TestStockBoard·TestCross·TestFormat·TestThinSectors·RankDelta·TestTaxonomy 2 | 30 | TestTaxonomy(classify 후보) 2·RankDeltaRender 5 |
| `test_kinds.py` | Classify·Counts | 13 | RenderTag·ProximityRowsAreNotHidden 9 |
| `test_mktcap_floor.py` | Floor·Labels(5)·Detector6 | 18 | Banner·TurnoverToggle·Labels 화면 꼬리표 9 |
| `test_basis.py` | AchievedRows | 6 | BasisCell 6 |
| `test_universe.py` | TestTurnoverFallback·TestVolRatio | 7 | funds 판정 시험 |
| 소스 검사 8개(`test_close_source` 1·`test_reader_voice` 5·`test_mktcap_floor` 1·`test_stale_px` 1) | 계산 본문이 kbj 로 가서 함께 옮김 — 따옴표·보는 함수 이름만 kbj 코드에 맞춤(`test_build_source.py`) | 8 | 나머지 |
| 합계 | | **153** | |

다리 시험 `tests/test_kbj_engine_shim.py` +1. legacy board 시험 **1,267 → 1,115**(`scripts/test_legacy.sh board`).
