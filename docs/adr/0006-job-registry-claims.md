# ADR 0006 — 작업 등록부·논리 데이터셋·데이터 키 선점으로 중복 수집 금지, `runner: external` (2026-10-07)

상태: 확정(P2 — 묶음 F 구현이 근거, `docs/p2_design.md` §1.7·§6, D-P2-5, PLAN §2 "스케줄은 등록부 하나",
§8 P2 완료 기준 "중복 수집 0건").
구현: `config/jobs.yaml`(등록부) · `kbj/data/catalog.py`(논리 데이터셋) · `kbj/data/spec.py`(`DataKey`·`DatasetSpec`) ·
`kbj/services/scheduler/{registry,conditions,claims,runner,handlers,service,__main__}.py` · `kbj/core/cron.py` ·
`kbj/store/migrations/0002_ops_core.sql`(`ops.data_claim`·`ops.job_run`) · 시험 `tests/unit/scheduler/`·
`tests/property/test_cron_properties.py`·하루 시뮬레이션 `tests/sim/`.

## 1. 맥락

- 정기 작업이 세 곳에 흩어져 있었다: SD APScheduler·맥 crontab, ET GitHub Actions·launchd, GX 스케줄러 서비스.
  같은 데이터(KRX 일별 시세·KIS 투자자별 수급·DART 공시 목록)를 두세 곳이 따로 받았고, 하루 한도(KRX 일 호출·
  DART 20,000건·KIS 초당 4건)를 서로 모르고 나눠 썼다(inventory (c) 겹침 표).
- PLAN §8 P2 완료 기준은 "하루 운영 시뮬레이션에서 중복 수집 0건"이다. 같은 작업이 두 번 도는 것뿐 아니라 **다른
  작업이 같은 데이터를 받는 것**도 막아야 한다.
- GEX 단계(분봉·KRX 파생 일별·마스터·무결측)는 GX `Scheduler.step` 안에 얽혀 있어 P7 까지 legacy GX 가 돌린다.

## 2. 결정

### 2.1 등록부 하나 — `config/jobs.yaml`

- 모든 정기 작업·상시 서비스·폐지한 legacy 작업(`retired:` — 사유와 함께)을 한 파일에 둔다. 지금 작업 38·서비스 7·
  폐지 27. P2 에 실제로 도는 작업은 `ops.nightly`·`filings.corp_code`·`ops.watchdog` 셋뿐이고(§6.9), 나머지는
  등록만 한다 — 실행기는 처리기가 없는 작업을 `ops.job_run` 에 `skipped`·`reason=planned` 로 남긴다(legacy 를
  부르지 않는다).
- 트리거는 하나만: `cron`(작업 `tz` 벽시계, 기본 KST) · `state_enter`(세션 상태 진입) · `equity`(주식 정규장 기준 —
  `TradingCalendar.equity_bounds` 라 연초·수능일 지연 개장을 따른다, R24) · `manual`.
- 검증(`python -m kbj.services.scheduler validate`, CI 시험 `tests/unit/scheduler/test_registry_validation.py`):
  모르는 키 금지, 이름 규칙, 처리기(owner)가 풀리는지(켜진 작업), 의존이 있는 작업인지, 데이터셋이 카탈로그에
  있는지, 같은 `(source, dataset)` 을 두 곳이 `collects` 에 두지 않는지, 로그인 데이터셋을 받는 작업이 `pub_*` 표에
  쓰지 않는지(켜진 작업은 표가 마이그레이션에 있는지도), 트리거가 하나인지, 알림 종류가 `notify.yaml` 에 있는지,
  일 예산 합이 `limits.yaml` 상한 안인지. 시험은 여기에 legacy 정기 작업 목록(`legacy_jobs.txt`)의 줄마다
  `absorbs`·`retired` 에 정확히 한 번과 P2 에 켜진 작업 목록을 더 본다.

### 2.2 논리 데이터셋 — `kbj/data/catalog.py`

- 데이터는 출처 API 가 아니라 **논리 데이터셋**(`DatasetSpec` — id `SOURCE:dataset`, 등급, 리미터·예산, 공표 시각,
  as_of 종류, 적재 표)으로 부른다. 각 어댑터의 `datasets.py`(또는 모듈 안 `DATASETS`)를 명시적으로 모은다(79개).
  id 가 겹치면 import 할 때 실패한다.
- 어댑터가 아직 없는 출처(NASDAQ·Yahoo·FRED·미 재무부·뉴욕연은·ForexFactory·Anthropic·ETF 운용사)는 `PLANNED` 로
  카탈로그에 두어 P2 부터 키를 등록한다. 어댑터가 생기면 그 `datasets.py` 로 옮긴다(시험이 겹침을 잡는다).
- 결정 D7: 거래대금·투자자별 순매수·ETF 수급용 데이터셋(KRX `stk_bydd_trd`·`ksq_bydd_trd`·`etf_bydd_trd`, KIS
  `FHKST01010900`·`FHPTJ04400000`·`FHPST01710000`·`FHPST02400000`)을 등록했다. TR·필드는 [실측 필요]
  (`docs/probe_results.md` §7 #14). 거래소 구분(KRX·NXT·TOTAL — `Venue`)이 있는 데이터셋은 데이터 키에 venue 가
  **필수**다(`DatasetSpec.key` 가 venue 없이 부르면 `ValueError`). 수집 작업 자체는 P3.

### 2.3 같은 데이터는 한 작업만 — 두 겹

1. **정적**: 등록부 검증 — 같은 `(source, dataset)` 은 등록부 전체(작업 + 외부 서비스)에서 한 곳만 `collects`.
   venue 가 있어도 데이터셋 단위로 한 곳이다. 백필 작업은 `backfill_of` 로 원래 작업의 데이터 키를 같은 선점 규칙으로
   쓴다(정적 겹침이 아니다).
2. **동적**: 데이터 키 선점 `ops.data_claim` — 키는 `(source, dataset, as_of, venue)`, 살아 있는 줄(`claimed`·`done`)은
   부분 유일 인덱스로 하나만. `claim` 은 이미 `done` 이거나 다른 작업이 쥐고 있으면 거절(→ `skipped(duplicate)`),
   **같은 작업**이 쥐고 있으면 이어 쓴다(재시도·재기동). 처리기가 돌려준 키만 `done`, 못 돌려준 키는 재시도가 이어
   쓰거나 끝내 실패면 `failed` 로 놓아 준다.
- 같은 `(작업, as_of)` 는 두 번 돌지 않는다(진행 중이거나 `ok`·`skipped` 면 새 발화 무시, 실패면 시도 번호를 이어
  다시). `run_id = <작업>:<as_of>:<시도>`.

### 2.4 `runner: external` (D-P2-5)

- P7 까지 legacy GX 가 돌리는 서비스·단계(`gex.poller`·`gex.ws_gateway`·`gex.recorder`·`gex.engine`, 작업
  `gex.day_minutes`·`gex.night_minutes`·`gex.krx_derivatives`·`kis.master`)는 `runner: external` 로 **데이터 키만**
  등록한다. KBJ 실행기는 돌리지 않고 기록도 하지 않는다 — 다른 작업이 같은 데이터를 받는 것을 P2 부터 막는 것이
  목적이다. 하루 시뮬레이션은 이 단계들을 같은 선점 장부·같은 리미터·같은 토큰 읽기로 흉내 낸다.

### 2.5 실행기 규칙(격리·재시도·마감)

- `JobRunner.tick(now)` 를 서비스가 1초마다 부른다(시험·시뮬레이션은 가짜 시계). 처리기는 `submit`(데몬 스레드)으로
  돌아 상태 루프를 막지 않는다. 처리기 예외·저장소 오류는 그 작업만 실패시킨다(절대 규칙 4). 실패가 확정되면 health
  `job_failed` + 알림 `ops.job_failed`(subject `<작업>:<as_of>` — 작업·as_of 마다 1회). 사유 문구는 가린다.
- 첫 tick 은 지금 분의 발화만 본다(재기동이 지난 시각을 몰아서 돌리지 않는다). 하루 1회 작업은 `catch_up_until`
  까지만 따라잡는다.

## 3. 설계(§1.7·§6.7)와 다르게 정한 것

| # | 무엇 | 왜 |
|---|---|---|
| 1 | `macro.morning`·`macro.evening`·`macro.monthly` 를 공개/로그인 작업으로 나눴다(`*_prv` 3개 — Yahoo·FF·ECOS 타기관 표) | 로그인 출처를 받는 작업이 `pub_*` 에 쓰지 않게(등급 검증) |
| 2 | 작업 추가: `flows.intraday`(P3 — D7 장중), `market.fsc_daily`(P3 — 금융위 시세), `filings.company_profile`(P4 — `DART:company`) | 카탈로그 데이터셋마다 받는 작업이 있어야 검증을 통과한다 |
| 3 | `ECOS:200Y102`(분기)는 `macro.monthly`, `ECOS:731Y003` 은 `macro.evening` | 묶음 C 질문의 답 — 두 표가 등록부 검증에서 빠지지 않게 |
| 4 | ET `kr.yml` 공공데이터 일봉은 `krx.daily` 가 아니라 `market.fsc_daily` 가 흡수 | 출처(금융위)가 다르다 |
| 5 | 작업 이름 정규식은 영역에 `_` 를 허용(`market_stats.kofia`) | 영역 이름이 둘 이상의 낱말 |
| 6 | `schedule` 에 `equity` 트리거, 작업에 `triggered_by`·`backfill_of`·`as_of` 필드 | 지연 개장(R24)·백필·실행 as_of 를 선언으로 |
| 7 | 실행 as_of 는 수집 키 as_of 가 한 종류면 그것(`krx.daily` 의 run_id 는 `krx.daily:<전 거래일>:n`) | 같은 데이터 키를 다시 받는 재실행을 run_id 로도 알아보게 |
| 8 | `market.close_collect` 는 cron 15:35 대신 `equity` close+5(수능일 16:35) | 지연 개장일에 장 마감 전에 받지 않게 |
| 9 | `ops.watchdog` cron `0,30 8-20 * * 1-5` = 08:00~20:30(26회) | 설계 '08:00~20:00' 을 단일 cron 으로 정확히 표현할 수 없다 — 30분 더 감시 |
| 10 | API: `JobRunner` 는 `now` 대신 `tick(now)`, `runs`·`catalog` 인자 / `JobContext(run_id, attempt, keys, resources)` / `ClaimStore.complete·fail(…, now)` | 시계 주입·시도 번호·자원 주입을 명시 |

## 4. 결과

- 하루 시뮬레이션(24시간 창, `tests/sim/test_one_day.py`): 데이터 키 1,385개가 각각 `done` 1회, 선점 거절 0, 66개
  데이터셋마다 받는 작업 하나, KRX `(엔드포인트, basDd)` 12쌍 각 1회, KIS 초당 최대 4건.
- 남은 일·[확인 필요]: 처리기 없는 작업의 '계획됨' 기록이 `filings.dart_feed` 만으로 평일 하루 약 780행 —
  `(작업, 날짜)` 당 1건으로 줄일지 결정. `ops.nightly` 백업 위치·보존(R23). PLANNED 데이터셋의 리미터 이름(nasdaq·
  yahoo·fred·treasury·nyfed·ff·anthropic·etf_issuers)은 그 어댑터가 생기는 단계(P3~P5)에 `limits.yaml` 에 더한다.
  ETF 일별·장중 표(`prv_etf.*`)는 P5 마이그레이션 0013 예정 — D7 ETF 수급을 P3 에 받으려면 당겨야 한다.
- 경미: 처리기가 `failed` 를 돌려줬지만 키는 모두 받은 경우 다음 시도는 `skipped(reason=duplicate)` 로 끝난다(사유
  이름이 오해를 부를 수 있다). 기록 저장소가 오래 끊기면 같은 실행의 시도 번호가 30초마다 늘 수 있다. 둘 다 동작상
  해는 없다.
