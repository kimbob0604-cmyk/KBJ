# GitHub 에서 돌리는 법

서버 없이 GitHub Actions 로 매 영업일 자동 실행한다. 무료다.

> **내 컴퓨터에서만 돌리고 싶으면 `LOCAL.md` 를 보라.** 배포도 요금제도 없고
> 데이터가 밖으로 안 나간다. 이 문서는 클라우드에서 자동으로 돌릴 때의 이야기다.

---

## 0. 먼저 알아야 할 것 — 워크플로가 `main` 에 있어야 한다

GitHub 는 **기본 브랜치(`main`)에 있는 워크플로만** Actions 탭에 띄운다.
지금 `board.yml` 은 작업 브랜치에만 있어서 **실행 버튼이 안 보인다.**

그래서 1단계가 머지다. 머지 전에는 예약 실행(cron)도 동작하지 않는다.

---

## 1. 브랜치를 `main` 에 머지

**웹에서:**

1. https://github.com/<owner>/ETF-Traker 로 간다
2. 노란 배너의 **Compare & pull request** 를 누른다
   (안 보이면 상단 **Pull requests → New pull request**,
    base `main` ← compare `claude/52-week-high-dashboard-9bedkz`)
3. **Create pull request** → **Merge pull request**

**터미널에서:**

```bash
git checkout main
git pull
git merge claude/52-week-high-dashboard-9bedkz
git push
```

기존 ETF 트래커(`daily.yml`)는 그대로 둔다. 시간대가 다르다 —
ETF 는 09:00 KST, 신고가 보드는 16:10 KST 라 겹치지 않는다.

---

## 2. 자격증명 등록

**Settings → Secrets and variables → Actions → New repository secret**

`board/.env` 에 넣은 것과 **이름을 똑같이** 맞춘다. 값은 그 파일에서 복사한다.

| 이름 | 없으면 |
|---|---|
| `KIS_APP_KEY` | 투자자별 수급 없음 |
| `KIS_APP_SECRET` | 〃 |
| `KRX_API_KEY` | 네이버 폴백으로 돌아감 (느리고 깨질 수 있음) |
| `DART_API_KEY` | 공시 없음 |
| `NAVER_CLIENT_ID` | 뉴스 없음 |
| `NAVER_CLIENT_SECRET` | 〃 (2026-09-22 `--check` PASS — 들어 있다) |
| `ANTHROPIC_API_KEY` | 일간 코멘트 없음 |
| `TELEGRAM_BOT_TOKEN` | 발송 안 됨 |
| `TELEGRAM_CHAT_ID` | 〃 |
| `TRIGGER_INBOX_CHAT_IDS` | **선택.** 인박스(5-3)가 읽을 대화방, 쉼표 구분. 없으면 `TELEGRAM_CHAT_ID` 를 읽는다 |

하나도 없어도 워크플로는 돌아간다. 없는 만큼 화면에 "빠진 데이터"로 나온다.

> **한투 시크릿은 재발급 권장.** 대화로 오간 값이라 노출 이력이 있고,
> 이 앱키는 주문 API 에도 쓰인다.

---

## 3. 실행

**Actions 탭 → 왼쪽에서 `신고가 보드` → 오른쪽 `Run workflow`**
→ `mode` 를 고르고 초록 버튼.

**첫날은 이 순서로 하나씩** 돌린다. 한 번에 `daily` 로 가지 마라.

| 순서 | mode | 무엇을 하나 | 걸리는 시간 |
|---|---|---|---|
| 1 | `test` | 정의 단위 검증만. 네트워크를 안 탄다 | 1분 |
| 2 | `check` | 소스가 실제로 응답하는지 | 2분 |
| 3 | `init` | 전 종목 상장 이후 전체 일봉 적재 | **30~60분** |
| 4 | `classify` | 종목 → 섹터 48개 배정 (Batch API) | 10분~1시간 |
| 5 | `daily` | 수집 → 집계 → 랭킹 → 엑셀 → 화면 | 5~15분 |

**2번(`check`)에서 KIS·KRX·DART 가 통과하는지부터 보라.** 여기서 깨지면
3번 이후는 의미가 없다. 실패하면 응답 내용을 그대로 알려주면 파서를 맞춘다.

4번(`classify`)은 1회성이다. 안 돌리면 섹터가 KRX 업종 14개로 성기게 나온다.

이후에는 **평일 16:10 KST 에 `daily` 가 자동으로** 돈다. 손댈 것 없다.

---

## 4. 결과 확인 — 세 군데

### ① 런 페이지 요약

Actions → 해당 런을 클릭하면 **맨 위에 표로** 나온다.
로그를 뒤질 필요 없다.

- 라벨별 신고가 종목수, 근접, 수정주가 의심
- 섹터 상위 5개와 각 1등 종목 (금일·7거래일)
- 교집합 종목
- **빠진 데이터** — 무엇이 없는 채로 보고 있는지

### ② Artifacts (내려받기)

같은 런 페이지 아래 **Artifacts → `board-<번호>`** 를 받으면 안에 있다.

```
docs/index.html                대시보드 (브라우저로 열면 됨)
docs/x/rankings-*.xlsx         랭킹 엑셀
board/state/<날짜>/*.json      단계별 산출 (universe·newhigh·sectors·rankings·events)
board/state/<날짜>/draft.md    일간 코멘트 초안
```

30일 보관된다.

### ③ 레포에 커밋된 결과 = 그대로 사이트

`daily` 가 성공하면 `docs/` 아래를 통째로 자동 커밋한다. 이게 곧 대시보드 사이트다.

```
docs/index.html          ← 최신 보드. 링크는 이 주소 하나
docs/d/2026-08-27.html      날짜별 보관본 (90일치)
docs/d/index.json           보관 목록 — 화면 상단 날짜 선택기가 읽는다
docs/x/rankings-*.xlsx      랭킹 엑셀 (화면 우측 상단 "엑셀" 링크)
docs/board/index.html       옛 주소용 리다이렉트
```

날짜 목록을 페이지에 박지 않고 `index.json` 을 런타임에 읽는다. 그래서 어제
만든 보관본을 열어도 선택기에 오늘 날짜가 나온다.

---

## 5. 링크 하나로 접속하게 만들기

> **이미 되고 있다.** 매 영업일 `docs/artifact.html` 이 Claude 아티팩트 주소
> 하나에 자동으로 다시 올라간다. 설정할 것도, 요금제도 없다 — `DASHBOARD.md` 를 보라.
> 아래는 그 대신 **정적 사이트를 직접 호스팅**하고 싶을 때의 이야기다.
> (사이트에만 있는 것: 지난 90일 날짜 선택기, 랭킹 엑셀 내려받기)

`docs/` 는 그대로 올리면 되는 정적 사이트다. 빌드 과정이 없다. 셋 중 하나를 고른다.

> **지금 고른 것은 방법 C 다** (2026-09-20). 보드를 공개하지 않기로 했고, 이 레포는
> Private + Free 라 방법 A 는 애초에 되지도 않는다(아홉 번 실행해 아홉 번 다
> `Get Pages site failed ... Not Found` 로 실패했다). `pages.yml` 은 자동 실행을
> 꺼 둔 채 남겨 뒀다 — 되살리는 절차는 그 파일 머리에 적혀 있다.
>
> **텔레그램 발송은 이것과 무관하게 계속 간다.** 보내는 쪽은 `board`·`us-board`·
> `bok`·`flow`·`daily` 워크플로가 각자 직접 쏘는 것이라 Pages 에 기대지 않는다.
> 화면이 필요하면 Actions 아티팩트(위 ②)로 받는다.

### 방법 A — GitHub Pages (제일 간단, 대신 주소가 공개)

`.github/workflows/pages.yml` 을 넣어 뒀다. `main` 에 머지되면 `docs/` 가 바뀔
때마다 자동 배포된다. 한 번만 켜 주면 된다.

**Settings → Pages → Source 를 `GitHub Actions` 로** (`Deploy from a branch` 아님)

몇 분 뒤:

```
https://<owner>.github.io/ETF-Traker/
```

**먼저 알아야 할 것 — 이 레포는 지금 Private 이다.**

| | 되나 | 주소는 |
|---|---|---|
| Private + Free | **안 됨** | — |
| Private + Pro($4/월) 이상 | 됨 | **여전히 누구나 접속 가능** |
| Public 으로 전환 | 됨 (무료) | 누구나 접속 가능 + 레포 코드도 전부 공개 |

Pages 사이트 자체에 접근 제어(로그인 요구)를 거는 기능은 **Enterprise 전용**이다.
Pro 를 결제해도 URL 을 아는 사람은 다 본다. 주소가 길어서 검색에 잘 안 걸릴 뿐,
비공개가 아니다. 종목·수치가 그대로 있으니 이 점을 감수할지 먼저 정해라.

### 방법 B — Cloudflare Pages + Access (진짜 비공개, 무료)

레포를 Private 으로 둔 채 **로그인한 사람만** 볼 수 있게 하는 유일한 무료 경로다.

1. https://dash.cloudflare.com → **Workers & Pages → Create → Pages → Connect to Git**
2. 이 레포를 고르고
   - Production branch: `main`
   - Build command: **비움**
   - Build output directory: `docs`
3. 배포되면 `https://<프로젝트명>.pages.dev` 가 나온다
4. **Zero Trust → Access → Applications → Add an application → Self-hosted**
   - 도메인에 그 주소를 넣고
   - Policy: `Emails` → `<볼 사람 이메일>` (원하는 사람 추가)

이제 그 링크는 이메일 인증을 통과한 사람에게만 열린다. 무료 플랜 50명까지.

### 방법 C — 안 올린다

Actions 의 **Artifacts** 로만 받는다(위 ②). 아무 데도 노출되지 않는다.
대신 매번 zip 을 받아 풀어야 한다.

---

## 5-1. 어느 걸 고를까

- **혼자 본다 / 새어나가면 곤란하다** → **B**. 손이 10분 더 갈 뿐 결과가 제일 낫다.
- **팀에 링크를 뿌린다, 공개돼도 상관없다** → **A** + 레포 Public 전환
- **아직 수치를 못 믿겠다** → **C** 로 며칠 보고, 검증되면 옮긴다

지금 `docs/index.html` 에 들어 있는 건 **합성 데이터**다(화면 상단에 그렇게 적혀
있다). 실행 전에 링크를 열면 그 화면이 보인다. `daily` 한 번 돌면 실데이터로 바뀐다.

---

## 5-2. 예약 실행이 다섯 시간씩 밀릴 때 — 밖에서 깨운다

GitHub 예약 실행(cron)은 **약속이 아니라 큐다.** 이 계정에서 실측한 지연이다
(2026-09-21).

| 워크플로 | 크론 | 예정 | 실제 | 지연 |
|---|---|---|---|--:|
| `daily` | `0 7` | 07:00 | 12:15 | 5.2시간 |
| `kr` | `30 6` | 06:30 | 11:40 | 5.2시간 |
| `board`(옛) | `10 7` | 07:10 | 12:34 | 5.4시간 |

**분을 옮기는 것으로는 안 고쳐진다.** :00 · :10 · :30 이 전부 같은 다섯 시간이다.
16:00 에 온다고 적힌 ETF 리포트가 실제로는 21:15 에 왔다.

정확한 시각이 필요하면 밖에서 깨운다. 무료 스케줄러(cron-job.org 등)가
`repository_dispatch` 를 부르면 분 단위로 정확히 돈다.

**1. 토큰을 만든다** — https://github.com/settings/personal-access-tokens
Fine-grained token, 이 레포만, 권한은 **Contents: Read and write** 하나면 된다.

**2. 스케줄러에 등록한다.** cron-job.org 기준:

- URL `https://api.github.com/repos/<owner>/ETF-Traker/dispatches`
- Method `POST`
- Headers
  - `Authorization: Bearer <토큰>`
  - `Accept: application/vnd.github+json`
- Body `{"event_type":"etf-daily"}`
- 시각 — 16:00 KST 평일

보내는 이름은 워크플로마다 다르다.

| 워크플로 | `event_type` | 하는 일 |
|---|---|---|
| `daily.yml` (ETF 리포트) | `etf-daily` | 수집 → 리포트 발송 |
| `board.yml` (신고가 보드) | `board-daily` | 보드 생성 + 발송 |
| 〃 | `board-send` | 발송만 (못 보낸 날 재시도) |
| `xdigest.yml` (X 다이제스트) | `xdigest-daily` | 수집 → 분석 → 렌더 → 발송. **매일 07:45 KST**(22:45 UTC) |
| 〃 | `xdigest-collect` | 수집만 (발송 없음). 텔레그램·수집 갱신 24시간 보관 대비. **매일 19:45 KST**(10:45 UTC) |
| 〃 | `xdigest-send` | 발송만 (못 보낸 통부터 재시도). **매일 08:15 KST**(23:15 UTC) |

**3. 크론은 지우지 않는다.** 스케줄러가 죽었을 때를 위한 뒤받침이다 — 늦게라도
오는 편이 안 오는 것보다 낫다.

같은 날 둘 다 깨워도 **리포트는 한 번만 간다.** `daily.yml` 이 기준일을 키로 한
표시를 남기고, 이미 보낸 날이면 수집만 하고 발송을 건너뛴다. 손으로 돌릴 때
(`Run workflow`)는 막지 않는다 — 일부러 다시 보내려는 것이라서다.

**확인하는 법** — 잘 도는지는 Actions 목록의 `Event` 칸을 본다.
`repository_dispatch` 로 찍히면 스케줄러가 깨운 것이고, `schedule` 이면 크론이
먼저 온 것이다.

---

## 5-3. X 게시물을 보드에 붙이기 — 봇에 공유한다

X 를 자동으로 검색하는 길은 없다(공식 API 유료, 스크래핑은 규약 위반). 대신
**X 앱에서 게시물 → 공유 → Telegram → 봇 대화**로 넘기면 된다. 다음 실행이
그 대화를 읽어 `board/state/inbox.json` 에 남기고, 같은 실행의 트리거 단계가 본문에서
종목명·코드를 찾아 **그 종목이 그날 52주 이상 신고가면** 랭킹 요약의 종목 줄에
`↳ X: <본문 60자> (@계정 · M/D)` 로 붙인다(D-085·D-086). 신고가가 아닌 종목의
게시물은 붙을 자리가 없다 — 실패가 아니다. 들어왔는지는 `board/state/inbox.json`
이나 실행 로그의 '인박스 새 N건' 줄로 본다.
캡션을 함께 적으면 그 글이 본문이 되고, 링크만 보내면 공식 oEmbed 로
게시물 본문과 작성자를 채운다(못 채우면 링크만 남고 지어내지 않는다).

텔레그램은 봇에 온 메시지를 **24시간**만 서버에 두므로 그 안에 실행이 한 번은
있어야 한다. 평일은 보드 실행과 파일 발송 재시도가 맨 앞에서 읽고, 주말엔 인박스
크론(토·일 09:23·20:23 KST, `mode=inbox`)이 읽기만 하고 아무것도 보내지 않는다.
어느 실행 사이도 13시간 이하지만(금 20:23 → 토 09:23 → … → 월 16:07) 크론은
제때를 보장하지 않는다 — 실행이 큐에 밀린 만큼은 잃을 수 있다.

기본은 `TELEGRAM_CHAT_ID`(보통 사용자와 봇의 1:1 대화)라 추가 설정이 없다. 단체방을
쓰려면 봇을 그 방에 넣고 `TRIGGER_INBOX_CHAT_IDS` 에 방 id 를 넣는다 — 숫자 id 가
기본이고 공개 방은 `@username` 도 된다. 화이트리스트 밖 대화는 버리고 로그에
chat_id 만 남긴다. **단체방은 봇 프라이버시 모드를 꺼야 한다** — 기본값(켜짐)에서는
봇에게 명령·답장만 가고 방에 공유한 링크는 안 온다. BotFather `/setprivacy` →
Disable 로 두고(또는 봇을 방 관리자로) **봇을 방에서 뺐다 다시 넣어야** 적용된다.
봇에 웹훅을 걸어 두면 읽기가 409 로 거부되니 `mode=check` 의 "텔레그램 인박스"
항목으로 먼저 확인한다.

---

## 5-4. Actions 무료 분 — 어디에 쓰이나 (2026-09-28 추정)

private 레포라 무료 분(월 2,000분, 리눅스 러너)이 걸린다. 2026-09-27 소진돼 모든 워크플로가
3초 만에 실패했다. 아래는 **추정**이다 — 최근 성공 런의 소요 시간(분 단위 올림 과금)에
크론 횟수를 곱했다. 수동 실행(backtest·search·us-search 등)은 빠져 있다.

| 워크플로 | 크론 (UTC) | 월 실행 | 1회 | 월 추정 |
|---|---|--:|--:|--:|
| `kr.yml` 섹터 모니터 | 매일 00:30 · 06:30 (**주말 포함**) | ~60 | 6~11분 | **~500** |
| `us-board.yml` 미국장 보드 | 평일 22:00 + 작업 브랜치 push | ~22+ | ~17분 | **~375+** |
| `xdigest.yml` X 다이제스트 | 매일 23:23 · 01:23 | ~60 | 1~9분 | ~300 |
| `bok.yml` 정책·수출 모니터 | 주 24회 (00:30·07:30 **매일**) | ~103 | 2~3분 | ~260 |
| `board.yml` 신고가 보드 | ~~평일 07:07 daily~~(주석) · 재시도 09:23·11:23 · 주말 인박스 | ~60 | 1~4분 | ~150 → ~60 |
| `daily.yml` ETF 일일 리포트 | 일~목 23:00 · 평일 07:00 | ~44 | ~3분 | ~130 |
| `flow.yml` 수급 리포트 | 평일 09:17 | ~22 | ~6분 | ~130 |
| `artifacts-gc.yml` | 일 03:00 | ~4 | ~1분 | ~4 |
| `report.yml` | 분기 1회 | — | — | ~0 |

합계 ~1,850분 + 수동 실행 → 한도(2,000)를 넘었다.

### 2026-09-29 적용한 절약

| 무엇 | 바꾼 것 | 월 절약(추정) |
|---|---|--:|
| `kr.yml` | 크론 평일만 · 예약 실행은 크로미움(`--with-deps`, 1~2분) 설치 생략 | ~200 |
| `bok.yml` | 00:30 · 07:30 크론 평일만 | ~90 |
| `board.yml` | 예약 전부 끔(재시도·주말 인박스 — 로컬 daily 뒤로 빈손이었다) · `board-daily` dispatch 도 발송만 | ~60 |
| `daily.yml` | 예약 실행이 '오늘 이미 보냄' 이면 설치·DB 복원·수집 없이 끝 | ~50 |
| `xdigest.yml` | 뒤받침 크론 두 개 → 하나(10:23 KST) | ~30~150 |
| `us-board.yml` | 작업 브랜치 push 트리거 제거(고칠 때마다 ~17분) | 고친 횟수 × 17 |

합쳐 월 ~450분 이상 → 예약만으로 ~1,400분. 수동 실행(backtest·search·us-search 는
회당 4~36분)은 이 여유 안에서 쓴다.

남은 후보(안 건드렸다): `us-board.yml` 평일 1회 ~375분은 미국장 보드 자체라 그대로 둔다.
크게 줄이려면 레포를 public 으로(무료 분 제한 없음) — 키는 Secrets 라 노출되지 않지만
코드·`docs/` 산출물이 공개된다.

**외부 스케줄러(cron-job.org)** 에 `board-daily` 가 등록돼 있다면 지워도 된다 — 이제
발송만 하므로, 로컬이 발송을 켠 뒤로는 매일 1분씩 빈손으로 쓴다.

## 6. 실패했을 때

| 증상 | 원인 | 할 것 |
|---|---|---|
| Actions 에 `신고가 보드` 가 안 보임 | `main` 에 머지 안 됨 | 1단계 |
| `check` 가 전부 FAIL | 인증키 미등록 또는 소스 개편 | Secrets 확인 → 응답 내용 공유 |
| `init` 이 타임아웃 | 네이버 폴백으로 2,800회 호출 중 | `KRX_API_KEY` 등록 (호출이 1/100 로 준다) |
| `daily` 는 성공인데 섹터가 14개 | `classify` 미실행 | mode `classify` 한 번 |
| 화면 상단에 "빠진 데이터"가 많음 | 정상. 없는 걸 없다고 적는 것 | 필요한 키를 채운다 |
| 커밋이 안 올라감 | 브랜치 보호 규칙 | Settings → Branches 확인 |
| Pages 배포가 실패 | Source 가 `Deploy from a branch` | Source 를 `GitHub Actions` 로 |
| Pages 가 아예 안 켜짐 | Private + Free 플랜 | 5장 방법 B 또는 Public 전환 |
| 사이트는 열리는데 옛날 날짜 | 배포 캐시 | 강력 새로고침 (Ctrl+Shift+R) |

Actions 사용량: **Public 레포는 무료 무제한.** Private 이면 무료 2,000분/월이라
`init`(40분) 한 번 + `daily`(10분) × 22영업일 ≈ 260분으로 여유가 있다.
