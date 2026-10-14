# ADR 0036 — GX 수집 경로 KBJ 이식: poller 서비스·분봉/KRX 파생/마스터/개장 확인 작업·쓰기 스풀·전환 순서 (2026-10-14)

상태: **확정**(2026-10-14 P9 WRAP — P9 묶음 GXI 초안, 끝 'WRAP 확정 메모'). 근거: docs/p9_design.md D-P9-7·§7.2 GXI·§9.1·§10 Q2, ADR 0029(GX 이식 경계 —
이 ADR 이 D-P7-4 의 '수집 경로는 legacy 외부 서비스' 예외를 끝낸다), ADR 0019(장중 투자자 1분은 flows.market_minute 하나),
ADR 0004(KIS 키 주입·발급은 auth 하나), ADR 0018(장 마감 뒤 발송 16:00).

## 1. 맥락

P7 은 GEX 엔진·웹소켓·녹화를 KBJ 로 옮기고, 수집 경로(poller·분봉·KRX 파생·마스터·무결측·개장 확인)는 legacy GX 가
KBJ DB(`search_path=prv_gex,ops`)에 붙어 돌게 남겼다(D-P7-4). 이것이 운영 중인 마지막 legacy 라 남으면 `legacy/` 를
비울 수 없다(PLAN D8). P7 이 미룬 이유(세션 상태 진입 트리거)는 등록부가 `state_enter` 를 이미 지원해 사라졌다.

## 2. 결정

1. **poller → 상시 서비스 `gex.poller`**(`kbj/services/gex_poller`, 하트비트 `gex-poller`, compose `gex-poller`).
   GX `services/poller` 본문 그대로 — 계획(전광판·체인·선물·최종거래일)·수집·품질·준비 판정. 투자자별
   (`_investors`·`_on_investor`)은 옮기지 않는다(죽은 코드 — ADR 0029 §3-6). KIS 는 `kbj.data.private.kis` +
   앱키 리미터(`config/limits.yaml`), 토큰은 auth 가 둔 Redis `kis:token` 읽기만. 쓰기 주인:
   `prv_gex.chain_snapshots`·`fut_board`·`series_expiries`.
2. **GX scheduler 단계 → 등록부 작업 7개**(runner kbj, phase P9). GX 의 구동 클래스(`MinuteDaily`·`GapDaily`·
   `OpenCheck`·`KrxDaily`)는 본문 그대로 옮기고, 처리기(`run_*`)가 등록부 실행 한 번에 GX scheduler 의 한 초(step)를
   돈다 — 작업 스레드는 실행기가 이미 띄웠으므로 제출은 그 자리에서(`inline_submit`). 재시도·마감은 클래스 안 타이머가
   아니라 등록부 `retry`(멱등 — 진행은 처음부터 다시):

   | 작업 | owner | 트리거 | 재시도 | 쓰기 |
   |---|---|---|---|---|
   | `gex.day_minutes` | `gex_minutes:run_day` | cron 16:00 거래일 | 5분, 17:50 까지 | `minute_bars`·`ops.collection_gaps`·`collection_reports` |
   | `gex.night_minutes` | `gex_minutes:run_night` | cron 06:10 거래일(그날로 귀속되는 밤) | 5분, 08:00 까지 | 같음 |
   | `gex.krx_derivatives` | `gex_krx:run` | cron 08:05 거래일 | 10분, 10:00 까지 | `krx_fut_daily`·`krx_opt_daily` |
   | `kis.master` | `kis_master:run` | PRE_DAY 진입 | 60초, 08:45 까지 | `master_snapshots` + Redis `kis:master` |
   | `kis.master_night` | `kis_master:run_night` | PRE_NIGHT 진입(야간장 날) | 60초, 18:00 까지 | 같음 |
   | `gex.open_check_day` | `gex_open_check:run_day` | cron 08:49 | 60초 | 없음(health·session_log 사건) |
   | `gex.open_check_night` | `gex_open_check:run_night` | cron 18:04(야간장 날) | 60초 | 없음 |

   - 야간 분봉은 cron 06:10 으로 둔다 — `state_enter: IDLE` + `after_night_session`(전날 밤)은 월요일에 금요일 밤을
     놓친다. 그 밤이 열렸는지는 처리기가 GX 규칙(`night_session_opens(prev_trading_day(T))`)으로 보고 아니면 skipped.
   - 데이터 키 `KIS:fo_master`(trade_date)는 `kis.master` 하나가 잡고 야간 갱신은 키 없이 돈다(같은 키 한 곳 — §6.5).
   - 개장 확인을 분봉 작업에서 떼어 냈다(GX 08:48·18:03 — 시각이 분봉 창과 다르다). absorbs 도 옮겼다.
   - 작업마다 owner 이름이 하나다(같은 본문을 `run_day`·`run_night` 로) — 실행기·시뮬레이션이 owner 로 처리기를 고른다.
   - GX `Scheduler` 클래스(세션 상태 발행 + 단계 격리)는 옮기지 않는다 — 상태 발행은 KBJ scheduler 서비스, 격리는 실행기.
3. **쓰기 = GX `PostgresSink` 의 수집 몫만** — `GexIngestStore`(`kbj/services/gex_poller/store.py`)가 P7 ING 의
   `SpooledGexStore`(디스크 스풀 한 벌 — `spool_dir/<서비스>`)를 상속해 수집 표를 더한다(두 벌 금지). 표·행 함수·읽기는
   `kbj/store/repos/gex_ingest.py`(`PgGexIngestRepo` — 무결측 판정 교체 `replace_gap_report` 는 한 트랜잭션). 연결 관리는
   `kbj.store.db.connect`(오류는 `StoreError` — 접속 정보 가림). 작업은 스풀을 쓰지 않는다(실패는 작업 실패 → 등록부
   재시도, 여러 작업 스레드가 같은 스풀 디렉터리를 쓰지 않게). poller 는 스풀을 쓴다(GX 규칙 그대로).
4. **마이그레이션 0022** — 표는 그대로(0006·0020), 0020 의 쓰기 주인 COMMENT(legacy GX)를 KBJ 주인으로 `COMMENT ON` 다시
   (적용된 파일은 고치지 않는다 — 체크섬).
5. **KRX 호출 예산** — `KrxClient` 가 공용 `krx:calls` 일 예산을 센다. 작업의 `KrxCallBudget` 은 프로세스 셈만(이중 계산
   금지 — GX `KRX_DAILY_CALL_CAP` 설정은 없앴다).
6. **운영 도구** `python -m kbj.services.engine.gex_report {nogap,shadow}`(GX `nogap_report`·`shadow_report` — probe #61·#62).
   나머지 GX 도구(`metric_report`·`validate_greeks`·`probe_*`)는 옮기지 않는다(원본 GEXLAB 레포에 있다).
7. **켜기는 WRAP** — 서비스·작업 전부 `enabled: false`(§9 R2). 전환 순서(런북 ④): 주말에 legacy GX poller·scheduler
   정지 → `scripts/gex_migrate_data.sh`(#60) → KBJ `gex-poller` 와 GX 작업 켜기 → 다음 거래일 `nogap`(#61)·하루 대조.

## 3. 결과

- `config/jobs.yaml` 에 runner external 이 없다. GX 시험 483 항목 중 397 을 `tests/unit/gex_ingest/` 로 같은 단언으로
  옮기고 86 을 사유와 함께 폐지(GX `Scheduler`·`PostgresSink` 연결 관리·GX 마이그레이션 — `legacy/gexlab/MIGRATION.md` P9).
  `test_legacy.sh gexlab` 760 → 277.
- ⚠ `state_enter` 는 scheduler 기동 때 발화하지 않는다 — 장중 재기동 뒤 Redis `kis:master` 가 없으면 poller·분봉이 근월물을
  모른다. 런북(WRAP/OPS)에 `python -m kbj.services.scheduler run-once kis.master` 를 적는다(조건 무시 한 번 실행).
- 하루 운영 시뮬레이션(`tests/sim`)의 GX 흉내 단계(`_gx_*` — external 작업 흉내)는 이제 등록부 작업과 겹친다 — WRAP 이
  sim 을 등록부 작업으로 바꾼다(GXI 는 `tests/sim/**` 를 고치지 않는다).

## 4. 실측 필요(probe 초안 — docs/p9_design.md 'GXI 구현 기록', 표는 WRAP)

- #68 전환 뒤 첫 거래일: 주간·야간 분봉 무결측(`gex_report nogap --days 1 --need 1`)·`ops.collection_reports` 행이 GX 운영과 같은 모양.
- #69 `gex-poller` 하트비트·체인 주기(장중 10초대)·KIS 초당 한도 안(poller + ws-gateway + scheduler 작업 합).
- #70 KRX 파생 일별 지연(08:05 첫 시도 → 공표 시각 분포)·`krx:calls` 예산 합계.

## WRAP 확정 메모 (2026-10-14)

- **켜지 않는다**(사용자 결정 §11 Q2): 서비스 `gex.poller` 와 작업 일곱(`gex.day_minutes`·`gex.night_minutes`·
  `gex.krx_derivatives`·`kis.master`·`kis.master_night`·`gex.open_check_day`·`gex.open_check_night`)은 `enabled: false`.
  `ENABLED_BY_PHASE["P9"] = ∅`(등록부 시험). 켜기는 사용자가 런북 ④ 전환 때 한 커밋으로(옛 GX 와 동시 실행 금지 — §9 R2).
- `legacy/gexlab` 을 지웠다(ADR 0038). 운영 VM 의 옛 GX 는 원본 GEXLAB 레포(또는 커밋 f6aeebd)의 compose 로 돌고 있으며
  전환 전까지 그대로다. 되돌리기도 그쪽을 다시 켠다(런북 ④-7).
- 하루 운영 시뮬레이션: harness 의 GX 흉내 단계 중 마스터·분봉·KRX 파생(`_gx_master`·`_gx_minutes`·
  `_gx_krx_derivatives`)을 지웠다 — 등록부 작업(runner kbj)이 실행기로 돈다(하네스는 꺼진 작업도 시뮬레이션
  처리기로 돌린다). 시뮬레이션 처리기가 `KIS:fo_master`(배포 zip)·`fut_minute_day/night`(분봉 1쪽)을 부르고,
  키 없는 `kis.master_night` 도 마스터를 받는다. 상시 서비스 흉내(poller 체인 회차·ws 구독)는 그대로 — `gx` 프로세스.
  `test_one_day`·`test_krx_late` 의 단언은 등록부 실행 기록으로 바꿨다(KRX `/drv/` 소비자 gx → scheduler, KRX 파생
  재시도 08:05·08:15 not_ready → 08:25 ok 는 같다).
- GX 통합 시험 중 KBJ 로 옮긴 코드를 보는 9 항목을 `tests/integration/test_gex_ingest_pg.py` 로 옮겼다(KRX 적재 멱등·poller
  왕복 4·분봉·마스터·무결측 리포트 교체·nogap 보고). Docker 가 필요해 이 세션에서는 돌리지 않았다 — 메인의 통합 실행으로
  확인한다. 옮기지 않은 GX 시험과 사유는 `docs/migration/gexlab.md` 'P9 WRAP'.
- probe #61·#62 명령은 `python -m kbj.services.engine.gex_report nogap|shadow`, #68~#70 은 `docs/probe_results.md` 표.
