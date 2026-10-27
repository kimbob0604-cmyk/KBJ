# 운영 오류 카탈로그 — 맥 로컬 운영(2026-10-27)

> 사용자 보고: "매번 오류가 많다". 맥 DB·로그는 이 작업에서 볼 수 없어 **코드·등록부(`config/jobs.yaml`)·
> 문서(`docs/probe_results.md`·ADR·데이터셋 notes)만으로** 만들었다. 근거가 코드에 없는 판단은 **[추정]**,
> 맥 기록으로만 가릴 수 있는 것은 **맥 확인 필요**와 확인 명령을 적었다.
>
> 실데이터로 고치는 법: 맥에서 `bash scripts/local/kbj.sh errors --since 7d`(새 명령 — 작업마다 실패·시간
> 초과·건너뜀(대기) 수·마지막 시각·사유, 값·키 없음)를 찍어 붙이면 아래 표의 '맥 확인 필요' 칸을 바꾼다.
> ETF(`etf.collect`·`etf.report`·`kbj/data/private/etf_issuers/`)는 다른 작업이 맡아 여기서 다루지 않는다.

## 0. 실패를 어떻게 세는가(이 카탈로그의 전제)

| 무엇 | 규칙 | 코드 |
|---|---|---|
| 처리기 결과 | `ok`·`not_ready`(공표 전 — 재시도)·`failed`(재시도)·`skipped`(할 일 없음) | `kbj/services/scheduler/handlers.py` |
| 최종 기록 | `ops.job_run.status` = ok·failed·timeout·skipped. 재시도 중간 시도도 `failed` 줄(detail `retry_at`) | `kbj/services/scheduler/runner.py` |
| 운영 점검·페이지 13 | (작업, as_of) 의 **마지막 시도**가 `failed`·`timeout` 이면 최종 실패. `skipped` 는 실패가 아니다 | `kbj/services/ops/check.py:job_outcomes` |
| 알림 | 최종 실패마다 `ops.job_failed` — 같은 작업·같은 사유(숫자 뺀 문장)는 KST 하루 1통 | `runner._alert` |
| 수 | 매분 작업은 as_of 가 분이라 **실패 하나가 (작업, 분) 하나** — 하루 최대 약 390건 | `conditions.resolve_as_of` |

## 1. 이번에 고친 것

| # | 고친 것 | 줄어드는 오류 | 실패를 숨기지 않는 장치 |
|---|---|---|---|
| F1 | **잠든 맥이 깬 tick**: 잠든 동안의 발화를 몰아 띄우지 않고 켜질 때 따라잡기(작업마다 가장 최근 회차, 장중 슬롯 제외)로 바꿨다 | 밤 8시간 잠듦마다 `ops.watchdog` 약 95건, `ops.nightly`(03:00)·`filings.corp_code`(03:05)·`public.export`(05:30)·`us.eod`(22:10 KST 회차)가 '마감을 넘겼다(시작하지 못함)'로 실패, 낮에 덮개를 닫으면 장중 슬롯 전부 실패 | 놓친 하루 작업은 깬 뒤 한 번 돈다. 장중 슬롯 공백은 무결측 판정·하트비트 끊김에 그대로 남는다. `runner.tick`·`_after_sleep`, 시험 `test_overnight_sleep_catches_up_once_…` |
| F2 | **굳은 의존 사슬**: 굳은 의존이 failed·timeout(또는 아래 F3 기다림)이면 의존 작업은 `skipped`(`detail.wait=blocked`, `blocked_by`)로 끝나고 알리지 않는다. 끝난 실행으로 치지 않아 원인을 고친 뒤 `kbj.sh job <작업>` 이 다시 돈다 | `market.close_collect` 실패 1번 → board.daily·brief.closing·flows.report·consensus.snapshot·program.stock_sweep·index.predict·rules.close·diag.daily 8건, `krx.daily` → board.confirm·rules.outcome·rules.backtest_nightly·fin.valuation_band 4건, `trade.customs_monthly` → trade.derive | 원인 작업의 실패·알림은 그대로. 마감 요약 미발송은 `ops check` 브리핑 항목이 여전히 FAIL. `runner._blocked` |
| F3 | **기간 수집의 공표 전**: as_of 가 월·분기·10일인 작업(`PERIOD_AS_OF`)의 `not_ready` 가 재시도를 다 쓰면 실패 대신 `skipped`(`detail.wait=not_ready`) — 다음 정시 발화가 같은 기간 키를 다시 본다, 알림 없음 | `macro.monthly`(161Y005 M2·301Y013 경상수지는 전월 값이 다음 달 중순에야 나온다 [추정] → 한 달 내내 매일 실패), `macro.monthly_prv`, `trade.customs_tenday`(20분 폴링마다), `trade.customs_monthly`, `trade.derive` | 출처 오류(`failed`)는 그대로 실패. 날짜 as_of 의 공표 전은 그대로 실패(다음 날 다시 보지 않으므로). 대기는 `kbj.sh errors` 의 '건너뜀(대기)'로 보인다 — 몇 주째 대기면 항목 코드 문제다. `runner._polls_again` |
| F4 | **금투협 포털 지연**(`market_stats.kofia`): 그날 행이 없어도 받은 최신 날이 3거래일 안이면 키를 `skipped`('공표 지연') — 지난 날은 매일 10일 창이 채운다 | 실측 2026-10-20: 포털이 2영업일 늦어 13:30 실행이 **거래일마다** 4회 시도 뒤 실패 | 3거래일보다 늦거나 창에 행이 없으면 `not_ready` → 실패(포털 멈춤). `kofia.LAG_OK_TRADING_DAYS` [추정 — 실측 2 + 1] |
| F5 | **출처 키 없음**: 매크로 계열(FRED·ECOS·KOSIS)·금투협(DATAGO)은 설정 키가 비면 클라이언트를 만들지 않고 그 키를 `skipped`('KBJ_… 가 비었다') | 키를 넣지 않은 출처가 날마다 실패 | 키 이름만 적고 값은 보지 않는다. 키를 넣고 다시 띄우면 다음 실행부터. `macro.client(need=…)`·`KeyNotSet` |
| F6 | **`python -m kbj.services.ops errors [--since 7d] [--json]`** + `kbj.sh errors` | — (진단 도구) | `check` 와 같은 마지막 시도 규칙, 계획됨 제외, 사유는 한 번 더 가림 |

등록부의 켬·끔(`enabled`)은 바꾸지 않았다. 사용자 결정(2026-10-20 "꺼 둔 것도 전부 활성화")을 뒤집을 근거 —
TR 이 추측뿐이고 실측 없이는 결코 돌 수 없다는 증거 — 가 코드에 없다: 프로그램 TR 은 공식 예제 경로이고
일별은 맥에서 실측했으며(probe_results §7.x 검산 ④), 컨센서스는 2026-10-26 실응답 모양 수정(7e1120b)이 있었다.
`krx.vkospi`·`market.fsc_daily` 는 아래 표대로 맥 기록을 보고 정한다.

## 2. 의심 작업 표(빈도 높은 순 [추정])

분류: **a** 실측 전 TR·엔드포인트 · **b** 키·설정 없음 · **c** 의존 사슬 · **d** 일정·공표 시각 · **e** 재시도·마감 설정 · **s** 맥 잠듦

| 작업 | 증상(예상) | 원인 근거 | 분류 | 수정 방안 | 이번에 고침 / 맥 확인 필요 |
|---|---|---|---|---|---|
| ops.watchdog (*/5 매일) | 아침마다 수십~수백 건 '마감을 넘겼다(시작하지 못함)' | 깬 tick 이 `(지난 tick, 지금]` 의 발화를 전부 띄웠다(`runner.tick`·`_fires`). 마감 10분이라 다 실패. 맥은 22:05~06:00 잠들 수 있다(`kbj.sh auto` — 06:05~22:05 만 깨어 둠) | s·e | 깬 tick = 켜질 때 따라잡기 | **고침(F1)**. 맥: `kbj.sh errors --since 7d` 에서 ops.watchdog 실패가 0 인지 |
| ops.nightly 03:00 · filings.corp_code 03:05 · public.export 05:30 | 잠든 밤마다 실패 1건씩 | 위와 같음 — 마감 120·120·30분이 06:00 깸보다 이르다 | s | 같음 — 깬 뒤 한 번 돈다(`scheduler_catch_up_h`=20 안) | **고침(F1)** |
| us.eod 09:10 ET(22:10 KST) 회차 | 매일 아침 실패 1건 | 22:05 뒤 잠들면 깬 뒤 발화 → 마감 180분(01:10) 지남 | s | 같음 | **고침(F1)** |
| 장중 슬롯 작업(flows.intraday·market.intraday·rules.intraday·program.*·flows.market_minute·gex.alert) | 낮에 덮개를 닫았다 열면 슬롯 수만큼 실패 | 위와 같음(마감 1~10분) | s | 깬 뒤 지난 슬롯은 띄우지 않는다(지난 슬롯을 지금 값으로 채우지 않는다) | **고침(F1)** — 공백은 무결측 판정에 남는다 |
| market_stats.kofia 13:30 | 거래일마다 4회 시도 뒤 실패 + 알림 | 포털이 D+1 이 아니라 2영업일 늦다(probe_results '금투협 … 공표 시차', 2026-10-20). as_of 전 거래일 행은 13:30~15:00 에 없다 | d | 허용 지연 안이면 건너뜀(지난 날은 10일 창이 채움) | **고침(F4)**. 맥: `kbj.sh errors` 에서 `skipped` 사유 '공표 지연 … 받은 최신' 의 날짜가 3거래일 안인지 |
| macro.monthly 09:10 · macro.monthly_prv 09:15 | 매일 1~2회 시도 뒤 실패(한 달 내내일 수 있음) | as_of 월 = 지난달(`resolve_as_of` — "[확인 필요 — 지표마다 공표 지연이 다르다]"). M2(161Y005)·경상수지(301Y013)는 지난달 값이 다음 달 중순 이후 [추정 — ECOS 공표 일정] | d·e | 기간 수집 공표 전 = 대기(다음 날 다시) | **고침(F3)**. 맥: `kbj.sh errors` 의 macro.monthly '건너뜀(대기)' 사유 키가 매달 중순 뒤에도 남으면 항목 코드(#43) 문제 |
| trade.customs_tenday(1·11·21일 ±2, 20분) · trade.customs_monthly(14~20일) | 공표 전 회차마다 실패 + 하루 1통 알림 | `retry: {max: 0}` — 폴링 회차가 재시도인데 not_ready 가 바로 최종 실패(D-P6-6). 공표 시각 #55 미실측 | d·e | 기간 수집 공표 전 = 대기 | **고침(F3)**. 맥: 공표일 기록에서 첫 ok 시각(#55) |
| trade.derive(14~20일 10:40·15:40) | customs_monthly 가 못 받은 날마다 실패 | 굳은 의존 customs_monthly 실패 → 의존 실패 | c | 의존 막힘 = 건너뜀 | **고침(F2·F3)** |
| market.close_collect 의 하류 8개(board.daily·brief.closing·flows.report·consensus.snapshot·program.stock_sweep·index.predict·rules.close·diag.daily) | 마감 수집 실패 1번에 실패 9건·알림 9통 | `runner._deps_ready` 가 의존 실패를 의존 작업의 실패로 기록·알림 | c | 원인 하나만 실패, 하류는 막힘(skipped) | **고침(F2)**. 원인(마감 수집) 실패는 그대로 — 맥: `kbj.sh errors` 의 market.close_collect 사유 |
| krx.daily 의 하류(board.confirm·rules.outcome→rules.backtest_nightly·fin.valuation_band) | KRX 공표가 10:00 넘게 늦거나 키·예산 문제면 실패 5건 | 같음 | c | 같음 | **고침(F2)**. 원인: 맥 `kbj.sh errors`·`kbj.sh budget` |
| program.intraday(매분) | TR 응답 칸이 가정과 다르면 **하루 약 390건** | `FHPPG04600101` 응답 칸·`FID_INPUT_HOUR_1` 등 입력 뜻 [확인 필요 #40](`kbj/data/private/kis/program.py`). 일별 `FHPPG04600001` 은 맥 실측(검산 ④ 허용 오차 — probe_results §7.x) | a | 칸이 틀렸으면 파서 상수 하나(`program.py`) 고침. 고칠 수 없으면 끔 | **맥 확인 필요**: `kbj.sh errors --since 1d` 의 program.intraday 실패 수·사유(KisShapeError·rt_cd 면 #40), 장중 `kbj.sh job program.intraday` |
| flows.market_minute · program.intraday · gex.alert(마감 1분) | 장 초반·10분 슬롯 경계에 `timeout`(마감 1분) [추정] | KIS 앱키 하나 4건/초(`config/limits.yaml kis`). poller 전광판이 P1 로 먼저, 10분 슬롯 경계엔 rules.intraday(≤60건)·flows.intraday·program.stock_scan(≤약 70건, :05)이 몰린다 | e | timeout 이 많으면 1분 작업 마감 2분·재시도 15초 유지 또는 슬롯 작업 우선순위 낮춤 | **맥 확인 필요**: `kbj.sh errors` 의 '시간초과' 열, `kbj.sh status --logs scheduler` 의 `rate_limited`·429 |
| program.stock_scan(10분) · program.stock_sweep(18:10) · program.daily | 칸 가정이 틀리면 실패 | #41 응답 칸·단위 가정(`STOCK_VALUE_UNIT_KRW`). 페이지 1 프로그램 순매수 상위가 장중 훑기를 보인다(2535134) — 돌고 있다는 정황 | a | 위와 같음 | **맥 확인 필요**: `kbj.sh errors` 의 세 작업, `python -m kbj.services.engine.verify --program --date <D>`(#42) |
| krx.vkospi 08:10~10:00 | 거래일마다 약 12회 시도 뒤 실패 1건 [추정] | 데이터셋 `idx/drvprod_dd_trd`·VKOSPI 행 이름·칸 [실측 필요 #P7-1]. KRX OpenAPI 는 서비스마다 이용 신청 — 파생상품지수를 신청하지 않았으면 매번 거절. 끄면 공포탐욕 변동성은 ATM IV 대체(metrics §D7) | a | 행을 못 찾는 것이면 `pick_vkospi` 이름표, 신청 문제면 KRX 에서 신청 또는 끔 | **맥 확인 필요**: `kbj.sh job krx.vkospi` 의 사유('행 없음' 대 401·403) |
| market.fsc_daily 13:10 | 거래일마다 4회 시도 뒤 실패 [추정] | 지수시세 포털 ID `15094807` 는 가정·V2 경로 미확인(#8·#48), 공공데이터포털은 데이터셋마다 활용신청(probe_results §1). 같은 포털의 금투협이 2영업일 늦다(위) — 주식시세도 늦으면 매일 not_ready | a·d | 늦음이면 F4 처럼 지연 허용, 신청·ID 문제면 지수 키만 끔. 화면·브리핑이 안 쓰는 대조 보조라 끄는 비용이 작다 | **맥 확인 필요**: `kbj.sh job market.fsc_daily` — 사유가 '행이 없다'(지연)인지 403·SERVICE_ACCESS_DENIED(신청)인지 |
| filings.dart_feed(매분 07~19) | DART 연결 순간 오류마다 그 분 실패(재시도 0) | `retry: {max: 0}`, `deadline_min: 5`. 다음 분이 같은 목록을 이어 보므로 놓친 것은 없다. 예산 소진은 2026-10-21 에 기다림으로 바꿨다(da746ed) | e | 순간 오류가 많으면 `retry: {max: 1, backoff_s: [20]}`(등록부 호출 추정 시험이 DART 예산을 다시 본다) | **맥 확인 필요**: `kbj.sh errors` 의 filings.dart_feed 실패 수·사유 |
| consensus.snapshot 16:00 | TR 이 틀렸으면 날마다 실패 | `CONSENSUS_TRS` 전부 [확인 필요 #29]. 2026-10-26 응답 머리 'A' 접두 수정(7e1120b)은 실응답을 받았다는 정황 | a | — | **맥 확인 필요**: `kbj.sh errors` 의 consensus.snapshot |
| macro.morning_prv 06:20 | Yahoo 한도(429)·FF 피드 변경이면 실패 | Yahoo chart #46, ForexFactory #49 [실측 필요] | a | — | **맥 확인 필요**: `kbj.sh errors` |
| macro.morning 06:20 | 날마다 `skipped`(실패 아님) | 재무부·뉴욕연은 item null(#45) → 그 키는 실측 전 건너뜀. FRED 일별 하루 늦음은 2026-10-20 고침(82d6bc6) | b | — | 정상(건너뜀 사유가 기록에 남는다) |
| macro.evening 17:00 · macro.evening_prv 17:05 | 17:00~18:30 안에 그날 값이 없으면 실패 | 817Y002·731Y003 은 그날 as_of, 722Y001·802Y001 은 다음 날 올라와 전 거래일로 바꿨다(f2db504). ECOS 그날 18:00 공표(diag_pub 일정) [추정] | d | 실패가 잦으면 그 둘도 전 거래일로 | **맥 확인 필요**: `kbj.sh errors` |
| us.universe(일 04:00) · filings.company_profile(토 04:00) | 주말 내내 잠든 맥이면 **돌지 않는다**(실패 기록도 없음) | `kbj.sh auto` 는 평일만 깨운다. 깬 뒤 따라잡기 창 `scheduler_catch_up_h`=20 시간 — 월 06:00 에서 일 04:00 은 26시간 전 | s·d | 주말에 `kbj.sh awake on` 이거나 수동 `kbj.sh job us.universe` · `kbj.sh job filings.company_profile`, 또는 평일 이른 아침으로 옮김(사용자 결정) | **맥 확인 필요**: 주말 잠듦 여부(`pmset -g log \| grep -E "Sleep\|Wake" \| tail -20`) |
| theme.master_sync · theme.classes_sync(일 12:40) | 일요일 잠듦이면 월 06:00 깬 뒤 따라잡기로 돈다(17시간 20분 전) | 위와 같음. classes_sync 는 굳은 의존 | s·c | — | F1 로 따라잡는다. master_sync 실패 시 classes_sync 는 막힘(F2) |
| gex.night_minutes 06:10 · kis.master(PRE_DAY) | 맥이 06:00 에 깨고 도커가 06:02 에 올라오면 첫 회차가 늦다 | 재시도 5분·60초(until 08:00·08:45)가 덮는다. `state_enter` 는 기동 때 발화하지 않는다(등록부 노트 — 91d61c1 자가 복구) | s | — | 낮음 — 맥 기록으로만 확인 |

## 3. 위험 낮음(켜진 나머지 작업)

외부 호출이 없거나(원장·DB 만) 실데이터로 이미 돌고 있는 작업 — 실패하면 거의 위 원인 작업의 결과다.

| 작업 | 근거 |
|---|---|
| board.daily·board.confirm·diag.daily·diag.public·rules.close·rules.outcome·rules.backtest_nightly·fin.valuation_band·filings.derive·earnings.alerts·earnings.backfill·index.calendar·index.predict·trade.map_load·trade.stock_index·trade.preview·brief.morning·brief.closing·flows.report·reports.dart_excel | DB·설정만 읽는다(노트 '외부 호출 없음'·'원장만'). 굳은 의존 실패는 이제 막힘(F2) |
| krx.daily·market.close_collect·flows.intraday·market.intraday·rules.intraday·fin.quarterly·filings.corp_code·gex.krx_derivatives·gex.day_minutes·gex.open_check_day·gex.open_check_night·kis.master_night | 2026-10-20~26 맥 운영 수정 기록이 있다(공표 시각·업종 코드·마감 잠듦 등 — git log). KRX 08:00 공표가 늦은 날은 10:00 까지 재시도 |
| fin.backfill·market.backfill·program.backfill | 수동 전용(`manual: true`) — 정시 실패 없음 |
| public.export | 2026-10-21·25 크기 상한 수정 뒤 — 05:30 회차는 F1 |

## 4. 맥에서 확인하는 순서

1. `bash scripts/local/kbj.sh update` 로 이 수정을 받고 하루 돌린다.
2. `bash scripts/local/kbj.sh errors --since 7d` — 작업마다 실패·시간 초과·건너뜀(대기)·재시도·마지막 사유. 출력은 값·키가 없으니 그대로 붙여도 된다(`--json` 도 같다).
3. 실패가 남은 작업만 `bash scripts/local/kbj.sh job <작업>` 한 번 — 첫 줄 사유로 위 표의 분류(a 칸·단위·TR, b 키, d 공표 시각)를 정한다.
4. 시간 초과가 많으면 `bash scripts/local/kbj.sh status --logs scheduler` 에서 KIS 한도(429·rate) 줄을 본다.
5. 결과로 이 표의 '맥 확인 필요' 칸을 고치고, 고칠 수 없는 실측 전 작업은 `config/jobs.yaml` 에서 날짜와 이 문서를 적어 끈다(`enabled: false` — 등록부 시험 `ENABLED_BY_PHASE` 도 같이).
