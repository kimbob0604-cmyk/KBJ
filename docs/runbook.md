# KBJ 운영 런북

P9 묶음 OPS(docs/p9_design.md §6.3, ADR 0037). 서울 VM 하나에서 KBJ 를 띄우고, 매일 점검하고, 1주 무중단을 판정하는
순서다. **값(키·토큰·접속 문자열·계좌)은 이 문서에 적지 않는다** — 이름표만(`docs/secrets.md`). 명령은 레포 루트에서
돈다. `[사용자]` 표시는 사용자만 할 수 있는 일(VM·키·텔레그램·GitHub 설정)이다.

목차: ① VM 준비 ② `.env` 이름표 ③ 첫 기동 ④ GX → KBJ 전환 ⑤ 매일 점검 ⑥ 장애별 대응 ⑦ 공개 사이트 켜기
⑧ 백업·복구 리허설 ⑨ 원문 녹화 보존 ⑩ 1주 판정

---

## ① VM 준비 [사용자]

- **오라클 클라우드 무료 VM**(Ampere A1 ARM64 4 OCPU·24GB, Ubuntu 24.04, 홈 리전 서울·춘천 — ADR 0040). 만들기·포트 열기는
  [`docs/deploy_oracle.md`](deploy_oracle.md) ①~③(콘솔 클릭), VM 준비는 그 ④ 의 한 줄 `scripts/vm/install.sh` 가 한다:
  Docker Engine + Compose v2(공식 저장소), 시간대 Asia/Seoul·NTP, (메모리 8GB 미만이면) 스왑, 방화벽 80·443(오라클
  iptables 기본 규칙 앞), 레포 clone(`~/kbj`), `.env` 템플릿(권한 600). 여러 번 돌려도 안전하다.
- 시각: 호스트 NTP 동기화(`timedatectl status` → `System clock synchronized: yes`). 컨테이너는 `TZ=UTC` 로 돌고 KST 변환은
  코드가 한다 — 호스트 시간대(설치가 Asia/Seoul 로 둔다)는 cron·로그 읽기 편의일 뿐이다.
- 디스크: Postgres 볼륨(`pgdata`)·상태 폴더(`state` — 백업·스풀)가 같은 디스크면 여유 20% 이상을 유지한다(부트 볼륨 100GB
  권장 — 안내서 ②). 크기 판단은 ⑤ 의 `ops check` 가 보여 주는 표 크기 상위 10·DB 크기로 한다.
- 열린 포트: API 는 루프백(127.0.0.1:8000)에만 묶인다. 바깥은 80·443 만 — TLS 역방향 프록시는 `docker-compose.vm.yml` 의
  Caddy(호스트 네트워크, `deploy/Caddyfile`, ADR 0040 — ADR 0008 의 '레포 밖 프록시' 를 대체하는 선택 오버레이). 주소는
  `KBJ_DOMAIN` 또는 `<공인IP 대시>.sslip.io`(Let's Encrypt 자동). 오라클 보안 목록 수신 규칙 80·443 [사용자 — 안내서 ③].

## ② `.env` 이름표

`docs/secrets.md` 의 표가 정본이다(이름·쓰는 서비스·없을 때 동작). 값은 `.env` 에만 두고 레포·이슈·채팅에 붙이지 않는다.
필수(`docker-compose.yml` 이 `:?` 로 막는다): DB·Redis 접속, 로그인 사용자·비밀번호 해시(`python -m kbj.services.api
hash-password`), 텔레그램 봇·웹훅 비밀, KIS 앱키 묶음. 선택: KRX·DART·관세청·ECOS·FRED 키(없으면 그 작업이
`skipped`/키 없음 사유로 남는다).

## ③ 첫 기동

VM 에서는 스크립트로 한다(안내서 ⑤·⑥ — 키 넣기 `scripts/vm/keys.sh`·`telegram.sh find-chat`·`set-password.sh`):

```bash
bash scripts/vm/start.sh     # .env 필수 이름 검사(이름만) → 주소 → 빌드 → db·redis → migrate → 공개 내보내기 역할 → validate → 전부 + Caddy
bash scripts/vm/status.sh    # 컨테이너·healthcheck·HTTPS·ops check·디스크·백업
```

start.sh 가 하는 일을 손으로 하면(오버레이 없이 — 다른 호스팅·자기 프록시):

```bash
docker compose --profile app up -d --build db redis
docker compose --profile app run --rm migrate          # 마이그레이션(ops.schema_migrations 에 체크섬)
docker compose --profile app run --rm scheduler python -m kbj.services.scheduler validate   # 등록부 검증
docker compose --profile app up -d --build             # auth·notifier·scheduler·ws-gateway·recorder·gex-engine·api
docker compose --profile app ps                        # 모두 healthy(하트비트 healthcheck)
```

- 공개 내보내기(`public.export`)는 `KBJ_PUBLIC_EXPORT_DATABASE_URL` 의 로그인 역할(`kbj_public_export` 구성원·
  `default_transaction_read_only=on` — ADR 0002 §2.2)이 있어야 한다. start.sh 가 `kbj_public_reader` 로 만들고 맞춘다
  (손으로 할 때는 같은 역할을 만든다).
- VM 에서 runbook 의 `docker compose --profile app …` 명령을 칠 때는 `-f docker-compose.yml -f docker-compose.vm.yml` 을
  붙인다(caddy 포함). 한 번짜리 명령(`exec`·`run`)은 붙이지 않아도 같다.
- `gex-poller` 와 GX 수집 작업 다섯(`gex.day_minutes`·`gex.night_minutes`·`gex.krx_derivatives`·`kis.master`·
  `kis.master_night`·개장 확인 둘)은 등록부에서 **꺼져 있다**(§11 Q2 — 옛 GX 와 동시 실행 금지, §9 R2). ④ 에서 켠다.
- 첫 기동 뒤 바로 `python -m kbj.services.ops check`(⑤)로 하트비트·작업 기록을 본다.

## ④ GX → KBJ 전환 [사용자 — 주말, 장이 닫힌 시간]

GXI(ADR 0036)가 GX 수집 경로를 KBJ 로 옮겼다. 전환은 한 번, 주말에 한다.

1. KBJ 스택 최신: `docker compose --profile app up -d --build migrate` 후 나머지 서비스.
2. 옛 GX 의 poller·scheduler 를 **멈춘다**(GX compose 에서 stop). 이 뒤로 GX 는 KBJ DB 에 쓰지 않는다. 옛 GX 는 이 레포가
   아니라 원본 GEXLAB 레포(또는 KBJ 커밋 `f6aeebd` 의 `legacy/gexlab`)의 compose 로 돈다 — KBJ 레포의 `legacy/` 는 P9 에 비웠다.
3. 녹화·산출 이관이 아직이면 `scripts/gex_migrate_data.sh --count` → `--run --yes` → `--count`(P7 #60 — 표 이름·건수만
   출력).
4. 등록부(`config/jobs.yaml`)에서 서비스 `gex.poller` 와 GX 작업들의 `enabled: true` 로 바꾸는 커밋을 올리고(PR —
   WRAP 이 켜지 않는다), `docker compose --profile app up -d --build gex-poller scheduler`.
5. 기동 직후 마스터를 한 번 받는다: `docker compose --profile app exec scheduler python -m kbj.services.scheduler
   run-once kis.master` (state_enter 작업은 기동 때 발화하지 않는다).
6. 다음 거래일: `python -m kbj.services.engine.gex_report nogap --days 1 --need 1`(probe #68 — 종료 코드 0) 과
   `ops check` 로 `gex-poller` 하트비트(#69)·`gex.krx_derivatives` 10:00 전 ok(#70)를 본다.
7. 되돌리기: KBJ `gex-poller` stop + 등록부 GX 작업 `enabled: false` → GX poller·scheduler start(위 2 의 옛 GX compose).
   같은 행은 `ON CONFLICT` 로 한 번만 남아 해가 없다.
8. 장중에 scheduler 를 다시 띄웠는데 Redis `kis:master` 가 없으면(재기동은 `state_enter` 를 다시 발화하지 않는다) 5 의
   `run-once kis.master` 를 한 번 더 한다.

## ⑤ 매일 점검

```bash
docker compose --profile app exec api python -m kbj.services.ops check            # 표 — 종료 코드 0(OK·WARN) / 1(FAIL)
docker compose --profile app exec api python -m kbj.services.ops check --json     # 같은 내용 JSON
docker compose --profile app exec api python -m kbj.services.ops check --since 3d # 주말 뒤 월요일
```

- 언제: **장 시작 전(08:30 전후)** 과 **마감 브리핑 뒤(16:30 이후)**. 화면으로는 로그인 페이지 13 '운영'(같은 함수 —
  1분마다 다시 읽는다).
- 판정 항목(종료 코드에 든다): 하트비트(지금 — 기준 안 OK, 10분 안 WARN, 넘거나 없으면 FAIL), 하트비트 끊김 >10분
  기록, 켜진 작업 최종 실패·마감 초과, 거래일 아침·마감 브리핑 1통씩(마감 09:00·20:30 이 지난 날만).
- 참고 항목(WARN 만 — 종료 코드를 바꾸지 않는다): 수집 결측·격리 수, 마지막 야간 백업 시각(26시간 넘으면 WARN),
  디스크 여유(10% 아래 WARN), 표 크기 상위 10·DB 크기(⑨·압축 판단).
- 출력에는 이름·상태·개수·시각만 있다(시세·키·접속 문자열 없음). 그대로 메모에 옮겨도 된다.

## ⑥ 장애별 대응

로그는 `docker compose --profile app logs --since 2h <서비스>`(구조화 JSON 한 줄 — 키·토큰은 가려져 있다). 재시작은
`docker compose --profile app restart <서비스>`. 한 서비스의 실패는 다른 서비스를 멈추지 않는다(절대 규칙 4).

| 증상 | 어디서 보나 | 대응 |
|---|---|---|
| KIS 토큰 발급 실패 | `auth` 로그 `issue_failed`·`ops check` 하트비트, 텔레그램 `ops.job_failed` | 1분 1회 제한(ADR 0004) — 연달아 재시작하지 않는다. 앱키 만료면 [사용자] 재발급 → `.env` → `restart auth` |
| KIS 429(초당 한도) | 작업 detail 의 사유, `kis:` 리미터 로그 | 리미터가 초당 4건으로 막는다. 계속 나면 동시에 도는 작업(`ops check` 작업 표) 확인 — 같은 앱키를 쓰는 다른 프로세스(옛 GX)가 살아 있지 않은지 |
| KRX 지연(08:05 자료 없음) | `krx.daily`·`gex.krx_derivatives` `not_ready` 재시도 | 등록부 재시도가 기다린다(마감 10:00). 마감을 넘기면 다음 날 `run-once --as-of <날짜>` |
| DB 디스크 부족 | `ops check` 디스크 WARN, `db` 로그 `No space left` | 백업 보존(7일) 확인, ⑨ 녹화 보존, 큰 표(표 크기 상위 10) — 지우기 전에 사용자 결정 |
| 텔레그램 웹훅 실패 | `notifier` 로그 `webhook_check`(10분 점검), 발송 기록 `failed` | 웹훅 주소·TLS 프록시 확인 [사용자]. 발송은 outbox 에 남아 다시 보낸다 |
| 하트비트 끊김 | `ops check` FAIL, 워치독 알림 `heartbeat:<서비스>` | `ps` 로 컨테이너 상태 → 로그 → `restart`. 10분 넘게 끊겼으면 1주 판정에서 그날 실패 |
| 브리핑 0통·2통 | `ops check` 브리핑 항목, 페이지 13 발송 기록 | 0통: `brief.*` 작업 실패 사유 → 캐치업(마감 09:00·20:30) 안이면 `run-once`. 2통: notifier 중복 키 확인 |

## ⑦ 공개 사이트 켜기 [사용자 — 운영 1주 뒤(§11 Q7)]

docs/p9_design.md §5 순서: 저장소 Settings → Pages 소스 'GitHub Actions' → 공개 내보내기 작업(`public.export`) ok 확인 →
`bash scripts/build_public_site.sh --dry-run` → 공개 산출물 검사(`uv run python scripts/check_public_outputs.py`) 통과 뒤
배포 잡을 켠다. 공개 사이트에는 합성 체험판을 올리지 않는다(§11 Q8).

## ⑧ 백업·복구 리허설 [사용자 — R23]

- `ops.nightly`(03:00)가 `pg_dump --format=custom` 을 `state/backup/kbj-<KST 날짜>.dump` 로 남기고 7일 보존한다
  [확인 필요 — 외부 보관 위치는 사용자 결정 전]. `ops check` 의 '마지막 야간 백업' 이 26시간 안이어야 한다.
- 복구 리허설(한 달에 한 번 권장): 빈 DB 컨테이너를 따로 띄워 `pg_restore --clean --if-exists -d <빈 DB> <파일>` →
  그 DB 를 `KBJ_DATABASE_URL` 로 두고 `python -m kbj.store.migrate`(적용된 마이그레이션 체크섬이 맞는지 보고 새로
  적용할 것 0 — `migration_done applied=0`) → 표 행 수 몇 개만 비교. 운영 DB 에 바로
  복원하지 않는다.

## ⑨ 원문 녹화 보존 메모(§0.4 #14·#15)

- `prv_gex.raw_messages`(웹소켓 원문)·틱 표가 가장 빨리 큰다. P7 R9 의 '원문 30일 Parquet 내보내기' 는 코드로
  만들지 않았다 — 운영 1주 뒤 `ops check` 의 표 크기 상위 10·DB 크기로 하루 증가량을 보고, 보존 기간(예 30일)과
  압축(Timescale 압축 정책 — `prv_board.*` 포함, `0007_board.sql` 메모)을 사용자와 정한다 [확인 필요].
- 정하기 전에는 지우지 않는다. 디스크가 먼저 차면 ⑥ 'DB 디스크 부족' 대로.

## ⑩ 1주 판정

```bash
docker compose --profile app exec api python -m kbj.services.ops week --end 2026-MM-DD   # 마지막 날(기본 어제 KST)
```

- 기준(§11 Q6 — ADR 0037): **7일 동안(거래일 5일 이상 포함)** ① 켜진 상시 서비스 하트비트 끊김 중 10분 넘는 것 0,
  ② 켜진 작업 최종 실패 0(재시도로 성공한 것은 '재시도 성공' 으로 셈만, 시간 초과는 실패), ③ 거래일마다 아침·마감
  브리핑 1통씩·발송 실패 0. 거래일이 5일 미만인 주(연휴)는 '판정 불가' — 다음 주로 다시.
- 끊김 기록은 `ops.health_events` 의 `heartbeat_gap` — 워치독(`ops.watchdog`, P9 WRAP 부터 **5분마다·매일 24시간**)이
  돌 때마다 지금 끊긴 상시 서비스를 남긴다. 5분 간격이라 10분 넘는 끊김은 놓치지 않는다(더 짧은 끊김은 보이지 않을 수
  있다 — ADR 0037 §3·WRAP 메모). 같은 공백 알림은 30분에 한 번(notify.yaml cooldown).
- 결과는 **통과/실패와 개수만** `docs/PLAN.md` P9 결과 줄에 옮긴다(시세·키 없음). 실패면 실패한 날의 `ops check
  --since` 와 로그로 원인을 찾고, 고친 뒤 7일을 처음부터 다시 센다.
