# ADR 0037 — 운영 점검·1주 무중단 기준·페이지 13 최소판 (2026-10-14)

상태: **확정**(2026-10-14 P9 WRAP — P9 묶음 OPS 초안, 끝 'WRAP 확정 메모'). 근거: docs/p9_design.md D-P9-12·§6·§11 Q5·Q6, ADR 0006(작업 등록부 —
이 ADR 이 '운영 확인' 쪽을 보충), ADR 0018(장 마감 뒤 발송 16:00), ADR 0025(번들 예산).

## 1. 맥락

PLAN P9 완료 기준의 하나가 '운영 1주 무중단'이다. 실제 VM·키·텔레그램이 필요한 운영은 사용자가 하고, 코드는 매일
확인하고 1주를 판정하는 **수단**만 만든다(D-P9-12). 판정 기준은 사용자가 §11 Q6 로 정했다. 운영 기록은 이미 `ops.*`
표(작업 실행·발송·health·결측·격리)와 Redis 하트비트에 있다 — 새 표를 만들지 않고 읽는다.

## 2. 결정

1. **판정 기준(§11 Q6)** — 7일(KST) 동안, 거래일 5일 이상 포함:
   ① 켜진 상시 서비스(등록부 `services` 중 enabled·heartbeat_s 있는 것) 하트비트 끊김 중 **600초 넘는 것 0**,
   ② 켜진 작업(등록부 enabled, 실행기 기록 `source='scheduler'`)의 **최종 실패 0** — (작업, as_of) 마다 시도 번호가 가장
   큰 줄이 `failed`·`timeout` 이면 실패, 재시도로 `ok` 면 '재시도 성공'(셈만),
   ③ **거래일마다 `brief.morning`·`brief.closing` 발송 `sent` 정확히 1통, `failed` 0**(기준일 = `notify_log.as_of`,
   없으면 요청 시각의 KST 날짜). 비거래일은 브리핑을 요구하지 않는다(실패만 센다).
   거래일 5일 미만인 주는 '판정 불가'(통과 아님). 결측·격리·백업·디스크·표 크기는 **참고(WARN)** 로만 보이고 판정에
   넣지 않는다(본문 §6.1 의 ④⑤ 는 사용자 결정에 없어 뺐다).
2. **점검 CLI** `python -m kbj.services.ops check [--since 24h] [--json]`·`week [--end 날짜] [--json]` — DB·Redis
   **읽기만**, 출력은 이름·상태·개수·시각(시세·키·접속 문자열 없음, 오류 문장은 형태 가림 + DSN 사용자·비밀번호 칸
   가림). 종료 코드: check 0(OK·WARN)/1(FAIL)/2(설정·접속 오류), week 0(통과)/1(실패·판정 불가)/2. check 의 하트비트는
   지금 나이로 OK(기준 안)·WARN(600초 안)·FAIL(넘거나 없음), 브리핑은 마감 시각(`catch_up_until` — 09:00·20:30)이 지난
   거래일만 본다.
3. **하트비트 끊김 기록** — Redis 하트비트는 마지막 값 하나라 지난 끊김을 모른다. 그래서 끊김 관측을
   `ops.health_events`(kind `heartbeat_gap`, detail `{"age_s": 초}`, ts = 관측 시각)로 남기고, 판정은 관측을 끊긴 시작
   (ts − age_s)이 120초 안인 것끼리 하나로 묶어 그 끊김에서 본 가장 긴 나이로 600초를 잰다(하트비트가 아예 없던
   관측은 길이 모름 → 넘은 것으로 친다). 날짜는 그 끊김의 마지막 관측일.
   쓰는 함수는 `kbj.services.ops.source.record_heartbeat_gaps`(등록부 서비스·하트비트 나이·시각·쓰기 대상을 받는다).
   **부르는 쪽은 워치독(`ops.watchdog`)이 맡는다 — WRAP 요청**(이 묶음은 `collectors/ops_watchdog.py`·`config/jobs.yaml`
   을 고치지 않는다). 관측 간격보다 짧은 끊김은 보이지 않는다(해상도 = 워치독 주기 — 지금 30분·평일 08~20시라
   WRAP 이 5분·매일 24시간으로 바꾸는 것을 권한다).
4. **기대값은 `config/jobs.yaml` 에서 필요한 칸만 직접 읽는다**(`load_expect` — 서비스 이름·하트비트 이름·기준 초,
   켜진 작업·마감 분, 브리핑 마감 시각). 등록부 모듈(`kbj.services.scheduler.registry`)은 `kbj.data.catalog` → 로그인
   어댑터를 import 하므로 로그인 API(계약 ⑩)가 쓸 수 없다. 두 벌 해석이 어긋나지 않게 `tests/unit/ops` 가 등록부와
   같은 값을 읽는지 대조한다.
5. **로그인 API `/api/ops/status`·`/api/ops/jobs?date=`·`/api/ops/notify?date=`** — 점검과 같은 함수(`run_check`·
   `job_outcomes`). 로그인 전용(`data_router`), 읽기만, 15초 프로세스 캐시 + ETag. 발송 기록은 종류·상태·시각·조각
   수·가린 사유만(본문·subject 없음). 운영 원천은 앱마다 한 번 만든다 — 시험·데모는 `app.state.kbj_ops` 에 메모리
   원천을 넣고, 운영은 `KBJ_DATABASE_URL` 로 `PgOpsSource`. DB 설정이 없는 실행은 404 `no_data`(화면 `.msg`).
6. **페이지 13 '운영' 최소판(§11 Q5)** — 보기 전용 표 셋: ① 서비스 상태(span-12 — 하트비트 나이·기준·상태 + 점검
   항목 줄) ② 오늘 작업(span-6) ③ 오늘 발송 기록(span-6). 1분마다 다시 읽는다. `tier: login`·`lock: personal`, 본문
   모듈은 공개 빌드에서 뺀다(`p11_rules` 방식) — 공개판은 자물쇠만·요청 0. `pages.ts` 13 → `phase: 'P9'`.

## 3. 결과·한계

- 1주 판정의 ① 은 워치독이 `heartbeat_gap` 을 쓰기 시작한 뒤부터 의미가 있다(§2-3 — WRAP 요청). 그 전에는 끊김
  기록이 비어 '끊김 0' 으로 보인다 — 런북 ⑤ 의 매일 `check`(지금 하트비트)로 보완한다.
- 표 크기는 일반 표 `pg_total_relation_size` + 하이퍼테이블 `hypertable_size`(청크 합)로 잰다(청크·시스템 스키마 제외).
- 번들(실측 2026-10-14): 로그인 JS gzip 123,458 B(예산 131 KB — 페이지 13 +2.1 KB), 공개 JS gzip 79,727 B(예산 88 KB —
  진입 모듈 자물쇠 껍데기만, 본문·`/api/ops` 문자열 0).
- 원문 녹화 30일 Parquet·`prv_board.*` 압축은 코드로 만들지 않는다 — `check` 가 표 크기 상위 10·DB 크기를 보이고
  결정은 운영 1주 데이터 뒤 사용자(런북 ⑨).

## WRAP 확정 메모 (2026-10-14)

- §2-3 요청 반영: `ops.watchdog` 이 자원 `ops_gaps`(운영 = `PgOpsSource(connect)` — scheduler `__main__`)가 있으면
  `record_heartbeat_gaps(등록부의 켜진 상시 서비스, heartbeat_age, now, ops_gaps)` 로 지금 끊긴 서비스를
  `ops.health_events` `heartbeat_gap` 에 남긴다. 기록 실패는 알림을 보낸 뒤 실행을 `failed`(사유 가림)로 남긴다(삼키지
  않음). 쓰기 Protocol 이름은 `GapWriter`(공개).
- 워치독 일정은 `*/5 * * * *`·`when: always`(5분·매일 24시간 — 10분 넘는 끊김을 놓치지 않는 해상도). 같은 공백 알림은
  notify.yaml `cooldown 1800` 그대로라 5분마다 알림이 쌓이지 않는다. 하루 운영 시뮬레이션은 창 첫 tick 앞에서 상시
  프로세스 하트비트를 한 번 남긴다(창 앞에서 이미 돌던 프로세스처럼 — 첫 발화가 '아직 안 뛴 프로세스' 를 끊김으로
  보지 않게).
- 페이지 13 은 데모에도 싣는다(ADR 0034 WRAP 메모). 웹 시험 기대값(빌드 glob·P9_PAGES·모듈 목록)을 갱신했다.
