# 국장 신고가 보드

매 영업일 장 마감 후 전 종목의 **60일 / 52주 / 역사적 신고가**를 판정하고,
신고가에 근접한 종목까지 묶어 한 화면으로 낸다.

사양은 `CLAUDE.md`, 화면 원안은 `web/prototype.html`, 필요한 외부 API 는 `docs/APIS.md`.

---

## 지금 상태

| 화면 | 상태 |
|---|---|
| 신고가 (달성 · 근접) | **동작** — 실데이터만 붙이면 됨 |
| 섹터 랭킹 (48섹터 순위 + 1~5등) | **동작** — 단, 섹터 배정을 안 돌리면 KRX 업종 14개로 성기게 나옵니다 |
| 종목 랭킹 (수익률 순 / 거래량 순 + 교집합) | **동작** |
| 엑셀 출력 | **동작** — `--excel` |
| 히트맵 | **동작** — 크기·색상·그룹 컨트롤 |
| 섹터 뉴스 | 미구현 — 네이버 검색 API 시크릿 대기 |
| 일간 코멘트 | **동작** — `--write` 가 Claude 를 불러 초안 생성. ANTHROPIC_API_KEY 필요 |
| 헤더 투자자별 수급 | 한국투자증권 KIS 로 결정 · 실호출 검증 남음 |

> **실데이터로 한 번도 안 돌려봤다.** 개발 환경에서 네이버·KRX 로 나가는 접속이
> 막혀 있었다. 코드는 다 붙어 있고 합성 데이터로 전 구간이 통과하지만,
> 엔드포인트 응답 형태는 `run.py --check` 로 먼저 확인해야 한다.

---

## 미국장 보드

간밤 미국장의 신고가·등락 흐름을 브리프 한 장과 화면 하나로 낸다. **키가 필요 없다** —
나스닥 스크리너와 stooq 로 받는다. 정의는 국장과 같은 코드를 쓰고 데이터·화면만 나뉜다.

```bash
python3 -m board.run --us-demo     # 합성 데이터로 끝까지 (네트워크 불필요)
python3 -m board.run --us-check    # 소스 점검
python3 -m board.run --us-init     # 최초 1회 (20~40분)
python3 -m board.run --us-daily    # 매일 — 수집 → 집계 → 브리프 → docs/us/index.html
```

사양·정의·막혔을 때의 대응은 `docs/US.md`, 결정 근거는 `docs/DECISIONS.md` D-076.
**실데이터로는 아직 안 돌렸다** — 개발 환경에서 소스 세 곳이 모두 막혀 있다.

---

## 로컬에서 돌리기 (권장)

내 컴퓨터에서 만들고 내 컴퓨터에서 본다. 데이터가 밖으로 안 나간다.
**`docs/LOCAL.md` 에 단계별로 적어 뒀다.**

```bash
./board/local.sh setup     # 처음 한 번 — 가상환경 + 의존성 + .env 틀
./board/local.sh init      # 전 종목 과거 일봉 (30~60분, 처음 한 번)
./board/local.sh           # 매일 — daily 돌리고 브라우저를 연다
```

Windows 는 `.\board\local.ps1` 로 같다. 화면은 http://127.0.0.1:8787/ 에 뜨고
**이 컴퓨터에서만** 열린다.

---

## GitHub Actions 로 돌리기

서버 없이 매 영업일 자동 실행. **`docs/GITHUB.md` 에 단계별로 적어 뒀다.**

먼저 알아야 할 것 하나: 워크플로는 **`main` 에 있어야** Actions 탭에 뜬다.
작업 브랜치에만 있으면 실행 버튼이 안 보인다.

첫날은 `test` → `check` → `init` → `classify` → `daily` 순으로 하나씩.
결과는 런 페이지 상단 요약과 Artifacts 에서 본다.

---

## 설치

```bash
pip install -r board/requirements.txt     # requests, PyYAML, anthropic
cp board/.env.example board/.env          # 자격증명을 채운다 (.env 는 커밋되지 않는다)
```

필요한 키와 발급처는 `board/.env.example` 과 `docs/APIS.md` 에 있다.
`python3 -m board.run --check` 가 무엇이 있고 무엇이 되는지 한 번에 보여 준다.

## 실행

```bash
# 0) 소스가 살아 있는지부터
python3 -m board.run --check

# 1) 최초 1회 — 전 종목 과거 2년 일봉 (10~20분)
python3 -m board.run --init

# 2) 매 영업일 — 수집 → 집계 → 렌더
python3 -m board.run --daily
```

화면은 `docs/board/index.html` 에 나온다. GitHub Pages 를 켜면 그대로 웹에서 열린다.

### 그 밖에

```bash
python3 -m board.run --engine     # DB 는 그대로, 집계만 다시
python3 -m board.run --render     # state JSON 만 읽어 HTML 만 다시
python3 -m board.run --test       # 신고가 정의 단위 검증 (네트워크 불필요)
python3 -m board.run --demo       # 합성 데이터로 화면까지 (네트워크 불필요)
python3 -m board.run --verify-adjust 005930 051910
                                  # 수정주가 교차 검증 (CLAUDE.md 9장 1번)

python3 -m board.run --write      # 서술 생성 (Claude 호출 → draft.md + claims.json)
python3 -m board.run --write --dry-run
                                  # API 를 부르지 않고 프롬프트만 뽑아 본다
```

### 서술 생성이 하는 일

1. `engine/facts.py` 가 state JSON 에서 **사실 팩**을 만든다.
   수치는 미리 문자열로 렌더한다 — `"+13.63%"`, `"3,240억"`, `"8.2배"`.
   LLM 이 계산할 것도, 반올림할 것도 남기지 않는다 (CLAUDE.md 2장 3번).
2. 장 흐름 1회 + 테마별 N회(병렬) + 캘린더 1회를 호출한다.
   문체 규칙과 테마 사전은 매일 같은 접두사라 프롬프트 캐시에 올린다.
3. **각 섹션을 검증한다** (`writer/verify.py`). 초안에 등장한 종목명이 그 섹션의
   사실 팩에 있었는지, 모든 수치 토큰이 사실 팩의 문자열과 정확히 일치하는지.
   `+13.63%` 를 `약 14%` 로 바꾸면 여기서 걸린다.
4. 걸리면 위반 내역을 붙여 다시 요청한다. 재시도를 넘기면 **그 섹션을 빼고**
   사유를 문서 상단에 적는다. 지어낸 수치가 섞이느니 짧은 리포트가 낫다.
5. 통과한 초안에서 내일 검증할 주장을 뽑아 `claims.json` 에 남긴다 (탐지기 7).

레퍼런스 산출물은 `docs/reference-comment.md`, 그 문서의 어느 줄이 지금 되고 안 되는지는
`docs/COVERAGE.md`.

`--demo` 는 `themes.yaml` 의 시드 종목명으로 가짜 시세를 만들어 화면까지 그린다.
**숫자는 전부 가짜고 화면 상단에 그렇게 적힌다.** 배관 확인과 디자인 확인용이다.

### 환경변수

| 변수 | 기본값 | 용도 |
|---|---|---|
| `BOARD_DB` | `board/board.db` | sqlite 파일 위치 |
| `BOARD_OUT` | `docs/board/index.html` | 렌더 결과 |
| `DATAGO_KEY` | 없음 | 공공데이터포털 인증키 (`docs/APIS.md` A1) |

---

## 구조

```
run.py            진입점
config/           settings.yaml — 임계값. 코드에 숫자를 박지 않는다
knowledge/        themes.yaml — 2층 테마 사전
ingest/
  http.py         세션·재시도·병렬 수집
  naver.py        네이버 금융 (기본. 인증키 불필요)
  datago.py       공공데이터포털 (인증키. 교차검증·폴백)
  pipeline.py     수집 → DB
engine/
  db.py           sqlite 스키마
  newhigh.py      신고가 판정 — 이 프로젝트의 심장
  themes.py       themes.yaml → 종목코드 정규화
  aggregate.py    업종·테마 롤업, 히트맵, 탐지기
  build.py        DB → state/YYYYMMDD/*.json
web/
  render.py       state JSON → 단일 HTML
  board.css       prototype.html 의 디자인
  board.js        탭·필터·히트맵
state/YYYYMMDD/   universe.json newhigh.json sectors.json events.json market.json
docs/             APIS.md DECISIONS.md BACKLOG.md
```

단계 사이는 **JSON 파일로만** 넘어간다. `web/render.py` 는 DB 를 전혀 모른다.
재실행과 디버깅을 위해서다 (CLAUDE.md 3장).

---

## 이 보드가 지키는 것

CLAUDE.md 2장의 절대 규칙 중 데이터 단계에 해당하는 것들.

- **추정을 단정하지 않는다.** 계산되지 않은 값은 `None` 으로 두고 화면에 `–` 로 나간다.
  추정치인 필드에는 `is_estimate` 가 붙는다 (거래대금 20일 평균, 저항두께).
- **모든 수치는 출처를 갖는다.** 각 state JSON 최상단에 `source` 와 `as_of` 가 있다.
- **에러를 삼키지 않는다.** 수집 실패·테마 미매핑·수정주가 의심 종목 수가
  화면 맨 위 "빠진 데이터" 배너에 그대로 올라간다.

---

## 알아 둘 것

**신고가가 안 뜨는 종목이 있다.** 다음 경우 해당 라벨을 계산하지 않는다.

- 상장 후 영업일 수가 룩백보다 짧을 때 (60일 미만이면 60일 신고가 없음)
- 수정주가 미반영이 의심될 때 — 역사적 판정 제외, 룩백 창은 점프 이후로 절단
- 역사적 최고가 스칼라가 아직 직전일 값을 모를 때 (최초 적재 직후 하루)

빈칸이 아니라 **판정 보류**다. 그 수는 `newhigh.json` 의 `n_suspect`,
`n_hist_not_evaluated` 와 화면 배너에 나온다.

**백필은 `--init` 으로만 된다.** 역사적 최고가 스칼라가 증분 갱신 전용이라
과거 구간을 뒤늦게 끼워 넣을 수 없다 (`DECISIONS.md` D-008).
