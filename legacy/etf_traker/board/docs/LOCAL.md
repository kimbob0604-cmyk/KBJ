# 로컬에서 돌리기

서버도, 배포도, 요금제도 없다. 내 컴퓨터에서 만들고 내 컴퓨터에서 본다.
데이터가 밖으로 나가지 않는다.

---

## 0. 필요한 것

- **Python 3.9 이상.** 확인: `python3 -V` (Windows 는 `python -V`)
  없으면 https://python.org — Windows 설치 시 **"Add python.exe to PATH"** 를 반드시 켠다.
- 인증키. `board/.env` 에 넣는다. 없어도 돌아가고, 없는 만큼 화면에 "빠진 데이터"로 나온다.

---

## 1. 처음 한 번

**macOS · Linux**

```bash
git clone https://github.com/<owner>/ETF-Traker.git
cd ETF-Traker
./board/local.sh setup
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/<owner>/ETF-Traker.git
cd ETF-Traker
.\board\local.ps1 setup
```

> 스크립트 실행이 막히면 그 창에서 한 번만:
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

`setup` 이 하는 일 — 가상환경(`.venv`) 생성 → 의존성 설치 → `board/.env` 틀 복사
→ 테스트 144건 실행. 마지막에 `OK` 가 나와야 한다.

### 인증키 채우기

`board/.env` 를 열어 값을 넣는다. **이 파일은 `.gitignore` 에 걸려 있어 커밋되지 않는다.**

| 이름 | 없으면 |
|---|---|
| `KIS_APP_KEY` / `KIS_APP_SECRET` | 투자자별 수급 없음 |
| `KRX_API_KEY` | 네이버 폴백 (느리다) |
| `DART_API_KEY` | 공시 없음 |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | 뉴스 없음 |
| `ANTHROPIC_API_KEY` | 일간 코멘트 없음 |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | 텔레그램 발송 없음 |

---

## 2. 실행 순서

**첫날은 하나씩** 돌린다. 바로 `daily` 로 가지 마라.

| 순서 | 명령 | 무엇을 | 시간 |
|---|---|---|---|
| 1 | `./board/local.sh -- --check` | 소스가 실제로 응답하는지 | 2분 |
| 2 | `./board/local.sh init` | 전 종목 상장 이후 일봉 적재 | **30~60분** |
| 3 | `./board/local.sh -- --classify` | 종목 → 섹터 48개 배정 | 10분~1시간 |
| 4 | `./board/local.sh daily` | 수집 → 집계 → 랭킹 → 엑셀 → 화면 | 5~15분 |
| 5 | `./board/local.sh open` | 브라우저로 본다 | — |

Windows 는 `./board/local.sh` 를 `.\board\local.ps1` 로 바꾸면 똑같다.

**1번에서 KIS·KRX·DART 가 통과하는지부터 보라.** 여기서 깨지면 이후가 무의미하다.
2번은 처음 한 번이고, 3번도 1회성이다(안 하면 섹터가 KRX 업종 14개로 성기게 나온다).

이후 매일은 이거 하나다:

```bash
./board/local.sh          # daily 돌리고 바로 브라우저를 연다
```

---

## 3. 화면 보기

```bash
./board/local.sh open
```

브라우저가 열린다. 주소는 **http://127.0.0.1:8787/** 이고, 끄려면 그 터미널에서 `Ctrl+C`.

- **이 컴퓨터에서만 열린다.** 기본 바인딩이 `127.0.0.1` 이라 공유기 안의 다른 기기에서도 안 보인다.
- 같은 집·사무실의 다른 기기(태블릿 등)에서도 보려면 `--host 0.0.0.0` 을 준다.
  그 순간 같은 네트워크의 누구나 볼 수 있으니 알고 써야 한다.
- 포트가 물려 있으면 8788, 8789… 로 알아서 옮겨 간다. 뜰 때 찍히는 주소를 보면 된다.

**왜 `docs/index.html` 을 더블클릭하면 안 되나.** 화면은 뜬다. 그런데 헤더의
날짜 선택기가 `d/index.json` 을 fetch 하는데 `file://` 에서는 브라우저가 막는다(CORS).
과거 날짜로 못 넘어간다. 그래서 로컬 서버를 쓴다.

### 만들어지는 것

```
docs/index.html          최신 보드
docs/d/2026-08-27.html   날짜별 보관본 (90일치)
docs/x/rankings-*.xlsx   랭킹 엑셀 — 화면 우측 상단 "엑셀" 링크
board/state/<날짜>/      단계별 산출 JSON, 코멘트 초안
```

인터넷이 끊겨 있어도 화면은 뜬다. 글꼴 CDN 이 안 잡히면 시스템 글꼴로 떨어질 뿐이다.

---

## 4. 매일 자동으로 (선택)

컴퓨터가 켜져 있어야 돈다. 노트북을 닫아 두면 그날은 안 돈다.

**macOS · Linux — cron**

```bash
crontab -e
```

평일 16:10 에 한 줄 추가한다. `/path/to/ETF-Traker` 는 실제 경로로 바꾼다.

```
10 16 * * 1-5 cd /path/to/ETF-Traker && ./board/local.sh daily >> board/state/cron.log 2>&1
```

> macOS 는 절전 중이면 안 돈다. `caffeinate` 를 쓰거나 시스템 설정에서 예약 기상을 켠다.

**Windows — 작업 스케줄러**

1. `작업 스케줄러` → **작업 만들기**
2. 트리거: 매일, 16:10, **요일 월~금**
3. 동작: 프로그램 시작
   - 프로그램: `powershell.exe`
   - 인수: `-ExecutionPolicy Bypass -File "C:\경로\ETF-Traker\board\local.ps1" daily`
   - 시작 위치: `C:\경로\ETF-Traker`

돌고 나면 `./board/local.sh open` 으로 보면 된다.

---

## 5. 안 될 때

| 증상 | 원인 | 할 것 |
|---|---|---|
| `가상환경이 없다` | setup 안 함 | `./board/local.sh setup` |
| `python3: command not found` | PATH 에 없음 | 재설치 시 "Add to PATH" 체크 |
| PowerShell 이 스크립트를 막음 | 실행 정책 | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| `--check` 가 전부 FAIL | 키 미입력 또는 소스 개편 | `board/.env` 확인 → 응답 내용 공유 |
| `init` 이 너무 느림 | 네이버 폴백으로 2,800회 호출 | `KRX_API_KEY` 를 넣으면 호출이 1/100 |
| 화면에 "빠진 데이터"가 많음 | 정상. 없는 걸 없다고 적는 것 | 필요한 키를 채운다 |
| 날짜 선택기가 안 먹음 | `file://` 로 열었다 | `./board/local.sh open` |
| 어제 화면이 계속 보임 | 브라우저 캐시 | 서버가 `no-store` 를 보낸다. 강력 새로고침 |
| 섹터가 14개뿐 | `--classify` 미실행 | 한 번 돌린다 |

---

## 6. 남에게 보여줘야 한다면

로컬은 나만 본다. 링크를 남에게 주려면 어딘가에 올려야 하고, 그때
`docs/` 를 그대로 올리면 된다(빌드 없음). 선택지는 `GITHUB.md` 5장에 있다 —
요약하면 GitHub Pages 는 이 레포가 Private 이라 Pro 가 필요하고 **그래도 URL 은
공개**이며, 진짜 비공개가 필요하면 Cloudflare Pages + Access 다.
