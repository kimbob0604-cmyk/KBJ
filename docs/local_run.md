# KBJ 내 컴퓨터(Mac)에서 혼자 쓰기 — 로컬 실행 안내서

공유·도메인·서버 없이, 내 맥 한 대에서만 대시보드를 돌린다. 접속 주소는 **http://127.0.0.1:8000** (내 맥에서만 열린다 —
같은 와이파이의 다른 기기에서도 안 열린다). 비용 0원. ADR 0041.

> **먼저 알아둘 것**
> - 수집은 **맥이 켜져 있고 깨어 있을 때만** 돈다. 맥이 잠든 동안(밤·덮개 닫힘)은 그 시간이 비어 있다. 장중에 쓰려면 전원을 꽂고
>   `awake on` 을 켜 둔다(⑥).
> - 텔레그램은 **보내기(알림)만** 된다. 텔레그램에서 명령(`/시그널` 같은 것)을 보내 받는 기능은 공개 주소가 있어야 해서 로컬에선 안 된다.
> - 키(KIS 등)는 **채팅에 붙이지 않는다.** 내 맥의 터미널에서 직접 입력한다(화면에 안 보인다).

## ① 준비물 설치 (처음 한 번)

1. **Docker Desktop** 설치: https://www.docker.com/products/docker-desktop/ → Mac(Apple chip) 받아 설치 → 앱을 열고 약관 동의.
2. Docker Desktop → 톱니바퀴(Settings) → **Resources** → Memory 를 **6GB 이상**으로 → Apply. 같은 창의 General 에서
   "Start Docker Desktop when you sign in" 을 켜 두면 편하다.
3. 터미널(Cmd+Space → "터미널")에서 `git --version` — 설치 안내 창이 뜨면 "설치"를 누른다(몇 분).

## ② 코드 받기 (처음 한 번)

```
git clone --branch claude/build https://github.com/kimbob0604-cmyk/KBJ.git ~/kbj
cd ~/kbj
```

## ③ 키와 로그인 정하기 (처음 한 번)

```
bash scripts/local/kbj.sh keys
```

차례로 묻는다. **KIS 앱키·앱시크릿은 꼭**, 나머지(KRX·DART·공공데이터·ECOS·FRED·KOSIS·텔레그램 토큰)는 없으면 Enter 로 건너뛴다.
나중에 `bash scripts/local/kbj.sh keys KBJ_DART_API_KEY` 처럼 하나만 다시 넣어도 된다. 화면엔 글자 수만 나온다.

```
bash scripts/local/kbj.sh password
```

대시보드 로그인 아이디와 비밀번호(12자 이상)를 정한다. 처음엔 이미지를 만드느라 **10~20분** 걸린다(맥이 깨어 있어야 한다).

## ④ 시작

```
bash scripts/local/kbj.sh start
```

마지막에 `✔ http://127.0.0.1:8000 열림` 이 나오면 브라우저에서 그 주소를 열고 ③에서 정한 아이디·비밀번호로 로그인한다.
(`bash scripts/local/kbj.sh open` 으로도 열린다.) 이후로는 Docker Desktop 만 켜져 있으면 `start` 한 줄이면 된다.

## ⑤ 텔레그램 알림 (선택)

1. 텔레그램 `@BotFather` → `/newbot` → 받은 토큰을 `bash scripts/local/kbj.sh keys KBJ_TELEGRAM_BOT_TOKEN` 으로 넣는다.
2. 만든 봇을 내가 쓸 대화방(나 혼자 있는 그룹)에 넣고 그 방에서 `/start` 를 보낸다.
3. `bash scripts/local/kbj.sh start`(키 반영) → `bash scripts/local/kbj.sh telegram find-chat` → 방 번호 선택.
4. `bash scripts/local/kbj.sh telegram enable-send` — 이때부터 알림이 간다(끄기는 `disable-send`). 장 마감 요약은 정확히 16:00(한국 시각)이다.

## ⑥ 매일 쓰기

| 하고 싶은 일 | 명령 |
|---|---|
| **평일 아침 자동 — 한 번만 켜 두면 매일 손댈 일 없음**(아래) | `bash scripts/local/kbj.sh auto on` / `auto off` / `auto status` |
| 맥 잠자기 방지 켜기/끄기(전원 꽂고, 덮개는 열어 둠) | `bash scripts/local/kbj.sh awake on` / `awake off` |
| 상태 점검(컨테이너·접속·운영 점검·백업) | `bash scripts/local/kbj.sh status` |
| 멈추기 / 다시 시작 | `bash scripts/local/kbj.sh stop` / `start` |
| 백업 지금 | `bash scripts/local/kbj.sh backup --now` (`~/kbj-backup` 에 7일 보관) |
| 새 버전 받기 | `bash scripts/local/kbj.sh update` (먼저 자동 백업) |
| 지난 3주 시장 일봉 채우기(KRX 키 필요, 처음 한 번) | `bash scripts/local/kbj.sh backfill` (기본 21일. 더 길게는 `backfill 120`) |
| 나머지 빈 칸(매크로·미국·신용·테마·진단) 한 번에 채우기 | `bash scripts/local/kbj.sh fill-all` (키가 없는 작업은 실패로 나오니 끝에 나오는 목록을 본다. 미국 일봉은 맨 끝 — 처음엔 약 1,700종목을 받느라 15~60분 조용하다) |
| 통계청(KOSIS) 항목 코드표 보기 | `bash scripts/local/kbj.sh probe-kosis` (키는 안 보인다 — 출력을 그대로 보내 주면 매크로 KOSIS 칸을 켠다) |
| 호출 예산을 누가 썼나 | `bash scripts/local/kbj.sh budget` — 오늘 DART·공공데이터 등 하루 예산의 쓴 수·닫힘 사유와 작업별 셈. 같은 사유의 작업 실패 알림은 하루 한 통만 온다 |
| 작업 오류가 많다 | `bash scripts/local/kbj.sh errors --since 7d` — 작업마다 실패·시간 초과·건너뜀(공표 대기·의존 막힘)·마지막 사유(값·키 없음). 원인 분류와 고친 것은 `docs/ops_error_catalog.md` |
| 화면 데이터 전체 점검 | `bash scripts/local/kbj.sh audit` — 화면 API 전부의 상태·기준 시각·빈 칸(로그인 없이). 한 화면 값은 `audit --show /api/market/summary` |
| Claude 에게 맡기기 | 맥 터미널에서 `cd ~/kbj && claude remote-control` — 세션이 Claude 앱에 뜬다. 점검 절차는 `.claude/skills/local-verify/SKILL.md`, `.env` 읽기는 `.claude/settings.json` 에서 막는다 |
| 종목 상세(8번) 재무·공시 채우기(DART 키 필요, 처음 한 번 + 며칠) | `bash scripts/local/kbj.sh fill-dart` |
| 수출입 7번 '종목' 탭 채우기(공공데이터 키 필요 — 매핑표 다시 싣기·시군구 5년 백필·지수 재계산) | `bash scripts/local/kbj.sh fill-trade` |
| 작업 하나 지금 돌리기 | `bash scripts/local/kbj.sh job market.close_collect` (`--as-of 2026-10-16` 로 날짜 지정) |
| 문제가 있는 서비스 로그 | `bash scripts/local/kbj.sh status --logs api` |

- 데이터는 도커 볼륨에 남는다. `stop` 이나 `--down` 으로는 지워지지 않는다(어느 명령도 데이터를 지우지 않는다).
- 매일 새벽 3시의 자동 백업은 맥이 깨어 있어야 돈다. 일주일에 한 번 `backup --now` 를 해 두는 것을 권한다.

### 평일 아침 자동 (`auto on`)

한 번 켜면(맥 비밀번호를 한 번 묻는다) 평일마다:

- **06:00** 맥이 스스로 깬다(`pmset`), **06:02** 도커가 꺼져 있으면 켜고 컨테이너를 올린다(로그인할 때도 한 번),
  **06:05~22:05** 잠들지 않는다. 그동안 등록부 작업이 알아서 돈다 — 06:20 미국장·매크로, 07:00~ 공시, **08:05 KRX 전일 확정 일봉**
  (지수·업종 포함), 08:50 밸류에이션, 15:35 장 마감 수집, 21:00 리포트.
- 조건: **전원 어댑터를 꽂아 두고**, 맥은 **종료가 아니라 잠자기**로 둔다(덮개는 닫아도 된다). Docker Desktop 의
  "Start Docker Desktop when you sign in" 도 켜 두면 더 안전하다.
- 주말·밤에는 하지 않는다(필요하면 `awake on`). 끄려면 `auto off`.

## ⑦ 아직 안 되는 것 · 한계 (정직하게)

- **맥이 잠든 동안의 빈 구간:** 장중 분 단위 수집은 그 시간만큼 비는 구간이 생기고 `status` 의 운영 점검에 표시된다. 일 단위 수집(장 마감 후
  확정 데이터)은 맥이 깨어 있는 때 다시 돌려 메운다.
- **배터리로 덮개를 닫으면 잠든다:** `auto`·`awake` 의 잠자기 방지(`caffeinate -s`)는 전원 어댑터가 꽂혀 있을 때만 듣는다.
  2026-10-22 은 16:19 에 배터리로 덮개를 닫아 15:35 마감 수집이 절반에서 멈췄고, 16:00 작업(보드·진단·마감 요약 등)이 모두
  실패했다. **15:30~16:30 에는 전원을 꽂아 두거나 덮개를 열어 둔다.** 그래도 잠들면, 스케줄러가 잠든 시간을 하루 단위 작업의
  시한에 세지 않으므로(`KBJ_SCHEDULER_CATCH_UP_H` 를 켠 로컬만 — 1초 주기가 2분 넘게 끊기면 잠든 것으로 본다) 맥이 다시 깨어 있을 때 마감 수집이 받은
  곳부터 이어 돌고 16:00 작업이 뒤따른다(깬 뒤 1분 기다림 — 잠깐 깨는 순간엔 네트워크가 없다).
- **실데이터로 아직 확인하지 못한 수집 작업은 꺼져 있다:** 관세청 수출입(`trade.customs_*`), 컨센서스 스냅샷, 지수 정기변경(`index.*`),
  VKOSPI. 키를 넣고 실제로 한 번 받아 보며 확인한 뒤 켜는 일이 남아 있다(`docs/probe_results.md` §7).
- **옵션 감마(GEX) 수집:** KBJ 쪽 수집기(`gex.poller`·`kis.master` 등)는 2026-10-20 부터 등록부에서 켜져 있다 — 같은 KIS
  앱키로 옛 GEXLAB 수집을 같이 돌리지 않는다(한도 초과). `start` 는 GEX 옵션 마스터(Redis `kis:master`)가 없으면 한 번
  받는다(장중·주말에 처음 띄워도 `gex-poller` 가 체인을 부를 수 있게). 페이지 3 GEX 패널은 30초마다 다시 읽는다.
- **외부 공개 사이트(Pages)·공유 링크:** 로컬 모드에서는 쓰지 않는다.
- **실주문은 없다:** `KBJ_LIVE_TRADING` 은 항상 false 다.

## ⑧ 문제가 생기면

| 증상 | 확인 |
|---|---|
| `Docker Desktop 이 꺼져 있다` | 앱을 열고 고래 아이콘이 가만히 있을 때 다시 |
| 빌드가 중간에 멈춤 | 메모리를 6GB 이상으로(①-2), 인터넷 연결 확인 후 `start` 를 다시(이어서 빌드한다) |
| `127.0.0.1:8000` 이 안 열림 | `bash scripts/local/kbj.sh status` → 이상한 서비스 이름을 `status --logs <이름>` |
| 로그인이 안 됨 | `bash scripts/local/kbj.sh password` 로 다시 정한 뒤 `start` |
| 8000 포트 사용 중 | 다른 프로그램이 쓰는 중 — 그 프로그램을 끄거나 알려 주세요 |
