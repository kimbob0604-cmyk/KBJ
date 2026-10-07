# 링크 하나로 보드 보기

보는 길이 둘이다. **사이트**는 열 때마다 최신 데이터를 받아 그리고, **아티팩트**는
그날 화면을 통째로 구워 올린 사본이다.

---

## 1. 사이트 — 링크를 열면 그 자리에서 최신

```
docs/
  index.html          데이터가 0 인 고정 셸. 매일 같은 파일이라 커밋에 안 올라온다
  api/latest.json     최신 보드 데이터
  api/d/<날짜>.json   날짜별 보관본 (90일)
  api/index.json      보관 목록 + 최신 생성 시각 (수백 바이트)
```

셸은 열릴 때 `api/latest.json` 을 받아 그리고, **열어 둔 채로** 60초마다
`api/index.json` 만 다시 받아 `generated_at` 이 바뀌었는지 본다. 바뀌었으면 그
자리에서 갈아 끼운다 — 보던 탭과 스크롤 위치를 그대로 둔 채다. 머리말의 표시등이
지금 무엇을 보고 있는지 적는다(최신 · 받는 중 · 갱신 실패 · 보관본).

왜 나눴나. 예전에는 데이터와 화면을 함께 구워 `index.html` 하나를 매일 새로
커밋했다. 그러면 링크를 열었을 때 보이는 것은 **만들어진 시점의 화면**이다 —
장중에 다시 열어도 아침에 구운 그 화면이고, 화면 코드를 한 줄 고쳐도 보드를
다시 돌려야 반영됐다. 나누면 화면은 고정되고 데이터만 움직인다.

**브라우저가 네이버·KRX 를 직접 부르지는 못한다.** 두 곳 다 CORS 를 열어 주지
않아서, 정적 호스팅에서 브라우저가 직접 시세를 받는 길은 없다. 그래서 데이터는
Actions 가 갱신하고 화면은 그것을 받는다 — 갱신 주기는 보드가 도는 주기다.

옛 주소(`d/<날짜>.html`)는 그대로 둔다. 이미 공유한 링크가 깨지면 안 된다.

---

## 2. 아티팩트 — 데이터가 박힌 사본

주소를 누르면 그날 보드가 그대로 뜬다. Actions 탭에 들어가거나 zip 을 받을 일이 없다.

**https://claude.ai/code/artifact/08baf50b-525c-474a-833e-e657486e7cfb**

주소는 고정이다. 매 영업일 이 자리의 내용만 바뀐다. 기본은 비공개(본인만)이고,
페이지 우측 상단 공유 메뉴에서 사람을 더할 수 있다.

---

## 어떻게 갱신되나

```
평일 16:10 KST   맥 로컬 — launchd → board/scripts/local_daily.sh   (2026-09-28 부터)
                 board.yml daily 와 같은 순서: 검증 → 수집 → 집계 → 랭킹 → 렌더
                 → 게시 전 점검 → docs/api/*.json · docs/artifact.html 을 main 에 커밋
                          │
평일 17:09 KST   루틴 "국장 신고가 보드 — 아티팩트 갱신"
                 레포에서 docs/index.html → docs/artifact.html 을 만들어
                 위 주소에 다시 올린다
```

예전에는 16:07 에 GitHub Actions(`board.yml` daily)가 만들었다. private 레포의 Actions
무료 분이 2026-09-27 에 소진돼 러너가 뜨지 않으므로(모든 워크플로가 3초 만에 실패)
만드는 쪽을 맥으로 옮겼다. `board.yml` 의 daily 크론은 주석 처리했다 — 10월에 무료 분이
돌아와도 같은 날 두 번 커밋·발송되지 않게. 아래 '로컬 실행' 을 본다.

보드를 만드는 쪽(GitHub)과 보여 주는 쪽(아티팩트)이 분리돼 있다. 사람이 끼는
단계는 없다.

### 루틴은 새 세션이 아니라 **기존 대화**로 들어간다

처음엔 매 실행마다 새 세션을 띄우게 만들었는데 그 세션에는 **레포가 없었다.**
`git clone` 을 하려다 권한 프롬프트에서 멈췄고, 아무도 안 보는 자동 실행이라
그대로 멈춰 있었다. 오류도 안 났다 — 그냥 아무 일도 안 일어난다.

그래서 루틴을 이 작업 대화에 묶었다(`persistent_session_id`). 그 대화에는
레포가 이미 있고, 아티팩트를 게시한 이력도 있어서 `read` 없이 바로 올릴 수 있다.
새 세션은 아티팩트를 처음 보는 것이라 첫 `publish` 가 한 번 거부되는데, 그 단계도
통째로 없어진다.

### 왜 `index.html` 을 그대로 안 올리나

아티팩트는 올린 파일을 자기 껍데기(`<!doctype>…<head>…</head><body>`) 안에
넣는다. 완성 문서를 그대로 올리면 문서가 문서를 감싸고 `<head>` 내용이 본문으로
새어 나온다. 그리고 CSP 가 걸려 있어 `fonts.googleapis.com` 이 아닌 스타일시트는
**에러 없이** 막힌다 — jsdelivr 에서 받던 Pretendard 가 조용히 사라져 한글이
시스템 기본 글꼴로 떨어진다.

`board/web/artifact.py` 가 이 둘을 처리해 `docs/artifact.html` 을 만든다.
껍데기 태그를 벗기고, 막히는 스타일시트를 빼고, Pretendard 자리를 구글 폰트의
`IBM Plex Sans KR` 로 바꾼다. 아티팩트에서 동작하지 않는 컨트롤(날짜 선택기,
엑셀 내려받기 — 둘 다 상대 경로가 필요하다)도 뗀다. 그 둘은 사이트 쪽에만 있다.

`site.publish()` 안에서 매번 함께 만들어지므로 따로 돌릴 명령이 없다.
검증은 `board/tests/test_artifact.py` 7건.

---

## 로컬 실행 (맥)

### 처음 한 번

```bash
git clone https://github.com/<owner>/ETF-Traker ~/ETF-Traker   # 없으면
cd ~/ETF-Traker && git checkout main && git pull

# 키 — 값이 화면에 안 나오는 복사. stock-dashboard/.env 에 있는 셋을 옮긴다
touch board/.env && chmod 600 board/.env
grep -E '^(KIS_APP_KEY|KIS_APP_SECRET|KRX_API_KEY)=' ~/stock-dashboard/.env >> board/.env
cut -d= -f1 board/.env            # 이름만 확인
```

나머지 키는 GitHub Secrets 에 있지만 **Secrets 는 되읽을 수 없다.** 발급처에서 다시 본다.
없어도 보드는 나온다 — 해당 부분이 '빠진 데이터' 배너로 간다.

| 키 | 어디서 | 없으면 |
|---|---|---|
| `DART_API_KEY` | opendart.fss.or.kr → 인증키 관리 | 공시·재무 카드 빠짐 |
| `NAVER_CLIENT_ID` · `_SECRET` | developers.naver.com → 내 애플리케이션 | 섹터 뉴스·종목 재료 빠짐 |
| `DATAGO_KEY` | data.go.kr → 마이페이지 → 일반 인증키(**Decoding**) | 교차검증(`--verify-adjust`)만 빠짐 — daily 는 안 쓴다 |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys (새로 발급) | 일간 코멘트 빠짐 |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 @BotFather → /mybots → API Token | 발송 불가 |
| `TELEGRAM_CHAT_ID` | 봇에 말을 건 뒤 `api.telegram.org/bot<토큰>/getUpdates` 의 chat.id | 발송 불가 |
| `KIS_ENV` | 값이 아니라 `real` 한 줄 | 기본 real |

```bash
board/scripts/local_daily.sh test     # 정의 단위 검증만
board/scripts/local_daily.sh          # daily 한 번 — board.db 가 없으면 --init 부터라 첫 회는 오래 걸린다
```

로그는 `board/state/local-logs/날짜-모드.log`. 첫 로컬 실행은 Actions 캐시의 `board.db`·
`board/state` 를 못 받으므로 이력을 새로 쌓는다 — 첫날 '신규/이어감' 과 페이퍼 장부는
그날부터 다시 시작한다.

### 텔레그램 발송

**기본 꺼짐.** 중복 발송을 막으려고 첫 실행은 보드만 만든다. 결과를 눈으로 본 뒤
그날 한 번만 손으로 보내고, 예약에서 켠다:

```bash
.venv/bin/python -m board.run --send files --only-fresh --once --no-inbox   # 오늘 것 한 번
board/scripts/install_launchd.sh --send                                    # 이후 매일 켬
```

`--once` 가 `docs/api/sent.json` 에 보낸 기준일을 적으므로, 같은 기준일은 다시 나가지 않는다.

### 예약 — launchd

```bash
board/scripts/install_launchd.sh          # 평일 16:10(맥 로컬 시각 = KST), 발송 꺼짐
board/scripts/install_launchd.sh --remove # 해제
```

**맥이 잠들어 있으면 16:10 에 돌지 않는다.** launchd 는 깨어난 직후에 놓친 한 번을 늦게
돌리고, 전원이 꺼져 있었으면 그날은 건너뛴다. 17:09 루틴 전에 끝나지 않으면 루틴은 어제
보드를 둔다. 정시에 깨우려면 `sudo pmset repeat wakeorpoweron MTWRF 16:05:00`.
돌아가는 동안은 스크립트가 `caffeinate` 로 잠자기를 막는다.

### 게시 전 점검

`python3 -m board.tools.artifact_check` — 17:09 루틴의 3번 검사(종목 수 2,000+ · 섹터
미배정 300 미만 · 투자자 수급 · '덜 받았습니다' 없음)를 코드로 옮긴 것. 로컬 실행이 커밋
전에 돌린다. 투자자 수급은 D-082 이후 **기타법인이 소스에 없어 세 값**이다 — '기타법인
빠져 있습니다' 안내가 붙어 있으면 통과로 본다(루틴도 그 상태의 보드를 게시해 왔다).

---

## 아티팩트에 없는 것

| | 아티팩트 | 정적 사이트(`docs/`) |
|---|---|---|
| 신고가·섹터랭킹·종목랭킹·히트맵·뉴스·코멘트 | 있다 | 있다 |
| 날짜 선택기 (지난 90일 보관본) | **없다** | 있다 |
| 랭킹 엑셀 내려받기 | **없다** | 있다 |
| 접근 제어 | 기본 비공개 | 호스팅에 따라 다름 |

지난 날짜와 엑셀이 필요하면 사이트를 띄운다. 내 컴퓨터에서는
`python3 -m board.run --serve`, 인터넷에 올리려면 `GITHUB.md` 5장.

---

## 손이 필요할 때

**하루 건너뛴 것 같다.** 루틴이 실패했거나 그날 `daily` 가 실패한 것이다.
Actions 에서 `신고가 보드` 의 마지막 런을 본다. 런이 성공했는데 링크가 옛날
날짜면 루틴만 다시 돌리면 된다.

**직접 지금 갱신하고 싶다.** 레포에서:

```bash
python3 -m board.run --daily          # 또는 --render (state 가 이미 있으면)
# docs/artifact.html 이 새로 만들어진다
```

그 파일을 위 주소에 올리면 끝이다.

**주소를 바꾸고 싶다 / 새로 만들고 싶다.** 새 주소로 올린 뒤 이 문서와
루틴 프롬프트의 주소를 함께 고친다. 두 곳뿐이다.
