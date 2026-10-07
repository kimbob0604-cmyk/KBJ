# Phase 3 설계안 — engine 서비스·확장 지표·기능 플래그

작성 2026-09-29. PLAN §5.3~§5.6·§6.4·§12 Phase 3 을 구현 단위로 옮긴다. 공식은 `docs/metrics.md` §4~§8 이 정본이고 여기엔 없다(명세에 없는 공식 금지). 완료 기준(PLAN §12): **지표별 검증 리포트**, ⏱ **1주 섀도 운영 무오류**(라이브 녹화가 필요 — 전용 앱키 뒤, 운영 기간 기준이라 병행).

> 상태(2026-09-30): §7 구현 순서 1~4 끝. 지표별 검증 리포트 `docs/metric_validation.md` — 명세 테스트 실패 0·교차 확인 불일치 0(2026-09-28 두 스냅샷). ⏱ 1주 섀도 운영은 전용 앱키로 라이브 녹화가 쌓인 뒤 `scripts/shadow_report.py` 로 판정한다 — 그 전까지 Phase 3 는 '구현·검증 끝, 운영 기준 대기'. 새 지표는 모두 shadow

## 1. engine 서비스

Phase 1 수집물(DB·Redis)을 읽어 `core` 로 계산하고 산출물을 저장·발행한다. 계산은 모두 `core`(순수 함수), 서비스는 입출력만.

```
poller ──chain_snapshots·fut_board·series_expiries (DB)──┐
       └─ chain.ready (Redis 채널, 사이클 끝 알림) ───────┤
ws-gateway ─ ticks.fut·ticks.opt (Redis) ─────────────────┤──► engine ──► levels·metrics·strike_gex·option_iv (DB)
scheduler ─ session.events·krx_*_daily (DB) ──────────────┘          └──► engine.levels·engine.metrics (Redis 채널)
                                                                        + engine:latest (Redis 키, 마지막 값)
```

- **입력 계약**: poller 는 한 사이클(전광판 3만기 + 보강)을 DB 에 넘긴 뒤 `chain.ready` 에 `{ts, trade_date, session, series:[(mrkt_cls, expiry)]}` 를 발행한다(`services/bus.py` 에 추가). engine 은 알림을 받으면 그 `ts` 까지의 시리즈별 최신 행을 DB 에서 읽는다. 알림이 없어도 10초마다 `max(ts)` 를 보고 따라잡는다(알림은 지연 단축용일 뿐 — pub/sub 은 잃을 수 있다)
  - 구현(2026-09-29, `services/bus.py` `ChainReady`·`services/poller/ready.py`): poller 싱크를 `ReadyTap` 으로 감싸 안쪽 싱크가 예외 없이 받은 체인 행(스풀 포함)만 모은다. 수집 조각(1초)마다 사이클이 끝났는지 본다 — 추적 시리즈(최근접·차기·월물) 전부의 전광판 행이 모였으면, 아니면 마지막 발행 뒤 30초(= 전광판 주기, 야간 B 는 전광판이 없어 늘 이쪽) **[확인 필요]**. ts 는 모인 행 중 가장 늦은 것, series 는 행이 온 (시장분류, 만기) 쌍(정렬·중복 없음). 세션(귀속 거래일·세션)이 바뀌면 모인 것을 먼저 낸다(한 알림 = 한 세션). 발행 실패(Redis 오류)는 로그·통계만 남기고 모인 것은 버린다 — 수집은 그대로, engine 이 따라잡는다
- **시리즈 키**: `(mrkt_cls, expiry)` — WKM·WKI 가 같은 6자리 만기값(예 261001)을 쓰므로 만기값만으로는 겹친다(검증 수정 4 에서 확인). 만기일은 `series_expiries` 의 최종거래일
- **야간(분기 B)**: 야간엔 전광판이 주간 마감 스냅샷으로 멈춰 있으므로 `session = night` 행(단건 `fill`)만 쓴다. 주간 전광판 행을 야간 계산에 섞지 않는다
- **S_ref(근월물 선물가) 출처는 세션으로 고른다** (2026-09-29 사용자 결정, metrics §1.3): 주간 = `fut_board` 의 전광판 `F`(day·board), **야간 = 단건 `CM` 시세(`fut_board` 의 night·`CM`·single 행) — 전광판 `F` 는 야간에 주간 종가로 고정(#19)**. engine 은 사이클마다 `core.forward.select_s_ref(quotes, session=지금 세션, code=근월물, now)` 로 고르고, None 이면 그 사이클은 F 를 만들지 않고(직전 산출물 `stale`) health, `stale` 이면 산출 품질에 합성한다. 대체 출처 없음(야간에 주간 전광판 행을 쓰지 않는다)
- **평가 주기**: `chain.ready` 마다(≈ 30초). Charm 은 2분(metrics §4.2), 일별 지표(IV 랭크·HV·딜러 점검)는 `POST_DAY` 한 번
- **상태**: 확정 베이시스(metrics §1.3)를 시리즈별로 Redis `engine:basis` 에 (베이시스, 확정 귀속 거래일, 확정 세션)으로 둔다 — 재기동해도 이어 쓴다. 근월물이 바뀌면(롤) 비운다
  - **결정(2026-09-29 사용자 결정) — 이월은 최대 2거래일** **[확인 필요]**: engine 은 사이클마다 나이 = `core.forward.basis_age(cal, 확정 귀속 거래일, 확정 세션, 지금 귀속 거래일, 지금 세션)` 을 `evaluate_expiry(forward_basis_age=…)` 로 넘긴다. **주간에 확정한 베이시스를 바로 이어지는 야간에 쓰면 0 — 같은 저녁의 야간은 이월이 아니다, 다음 주간부터 1**(2026-09-29 사용자 결정: 월 주간 확정 → 월 야간 0 · 화 주간 1 · 수 주간 2 · 목 주간 3 만료). 나이 1~2 인 이어 받은 베이시스로 만든 선물 대체 F 는 사유에 `basis_carried`, 나이 3 이상이면 선물 대체 F 를 쓰지 않고 쓸 행사가 2 개 미만인 만기는 `invalid`(`basis_carry_expired`) — 2 개 이상이면 F 는 내고 교차 확인만 건너뛴다. 확정은 여전히 품질 ok F 로만(`confirm_basis`) — 첫 ok F 가 나오면 나이 0 으로 다시 시작
  - 이로써 검증 수정 4 열린 문제(WKM 261001·202611 처럼 ok F 가 안 나오는 시리즈)는 전 거래일 베이시스로 2거래일까지 선물 대체 F 를 받고, 그 뒤엔 invalid 다
- **격리**: 지표 하나의 예외는 그 지표만 `invalid` + health, 사이클은 계속(CLAUDE.md). 사이클 전체 실패는 직전 산출물을 `stale` 로 두고 health
- 구현(2026-09-29) — 사이클 평가 `services/engine/evaluate.py`(부작용 없음, core_golden 흐름 그대로 — 2026-09-28 두 스냅샷과 야간 파생의 core 골든 값을 그대로 낸다, `tests/unit/test_engine_evaluate.py`). 정한 기본값 **[확인 필요]**:
  - 만기 지난 시리즈: 그 시리즈 최신 행 시각에 만기 시각(15:20)이 지났으면 뺀다(core_golden). 사이클 시각엔 지났지만 최신 행은 그 전인 시리즈(15:20 을 걸친 사이클)는 이번 사이클에 행이 온 때(직전 사이클 as_of 뒤 행)만 한 번 평가하고 그 뒤엔 뺀다 — 옛 스냅샷으로 죽은 시리즈를 계속 내지 않게. 재기동 첫 사이클은 걸침 평가를 하지 않는다
  - 평가 시각 = 그 시리즈 최신 행 시각(core_golden 의 전광판 시각), ±1σ Δt 는 사이클 시각(as_of)
  - 같은 종목에 전광판·보강 행이 다 있으면 전광판 행(호가가 있다 — `series_rows` 와 같다). OI 없는 행은 0 으로 두고 입력 품질 estimated(`oi_missing`)
  - 옛 행(검토 수정 E1, metrics §0 — REST 90초 미갱신이면 stale): 행의 저장 품질은 받은 순간의 것이라 engine 이 사이클 시각으로 나이를 본다. 시리즈의 최신 전광판 행(30초 주기)이 as_of 보다 90초 넘게 앞이면 그 시리즈 입력 `stale`(`rows_stale`) — 먼 행사가 보강 행이 새로 와도 전광판(ATM 구간)이 끊겼으면 stale. 전광판 행이 없는 시리즈(야간 B)는 최신 보강 행으로 보고 한도 20분 — 보강 2 만 받는 차기 위클리는 순환의 월물 꼬리(위클리에 없는 먼 행사가 약 460건 ≈ 8분) 동안 행이 오지 않아 90초로는 밤새 거짓 stale 이다(순환 한 바퀴 약 960건 ≈ 16분 + 여유). 그 시리즈가 드는 범위·±1σ·만기별 지표와 그 시리즈의 `strike_gex`·`option_iv` 행이 stale 이상, health `engine_series_stale`. 평가 시각은 그대로 그 시리즈 최신 행(옛 스냅샷을 그 시각 값으로 — stale 표시)
  - 최종거래일(검토 수정 E2): `series_expiries` 에 없는 시리즈는 빼지 않고 캘린더 계산값(`estimate_last_trade_date` — poller 가 KIS 값을 못 받았을 때 쓰는 것과 같다, 원천 calendar)으로 평가 + health `engine_no_expiry`. 캘린더로도 못 세는 시리즈(만기값이 규칙 밖)는 평가하지 않고(`no_expiry`) 조용히 빠지지 않게 범위 all 은 `invalid`(그 OI 가 빠졌다), nearest·0dte 는 `estimated`(그 시리즈가 더 이르거나 오늘 만기일 수 있다) — 사유 `series_no_expiry` **[확인 필요]**. 전에는 행이 있는데 만기 행이 없는 시리즈가 모든 범위에서 빠지고 품질은 ok 였다(최근접 위클리면 nearest 가 조용히 다음 만기)
  - 산출 품질 = core 품질 ⊕ S_ref 품질 ⊕ 범위 입력 품질(행 품질, 실패한 시리즈가 그 범위에 들면 invalid `series_failed`, 최종거래일 모르는 시리즈 `series_no_expiry`). ±1σ 는 기준 만기(범위에서 F 있는 가장 이른 만기)의 입력만. 사이클 품질 = 범위 all 순GEX 품질(S_ref 포함)
  - 베이시스 확정은 F 품질 ok 이고 S_ref 품질도 ok 일 때만(stale S_ref 로 만든 베이시스는 틀린다). 만기 지난 시리즈의 베이시스는 지운다
    - 검토 수정 E1: 옛 행 시리즈는 확정하지 않고, F 에 쓴 행사가(`ForwardResult.strikes`)의 콜·풋 행이 모두 as_of 의 90초(REST) 안일 때만 확정한다 — 옛 옵션 가격으로 만든 F 와 지금 S_ref 의 차는 베이시스가 아니다(검토 재현: 09:10 행 F 1095 − 14:00 S_ref 1115 = −20pt 를 나이 0 으로 확정해 2거래일 동안 교차 확인·대체 F 에 썼다). 야간 B 월물(보강 120초 주기)·차기 위클리(보강 2 순환)는 그 사이클엔 확정을 건너뛸 수 있다 — 확정 베이시스는 남고 나이로 센다 **[확인 필요]**
  - 근월물 코드: poller 문맥(Redis `poller:chain_context` + 마스터 — `ChainContext.live_futures_codes` 첫 종목, ws-gateway 와 같은 규칙). 없으면 그 세션 출처(주간 전광판 F·야간 단건 CM) 행 중 잔존일수가 가장 짧은 종목(만기 지난 근월물을 못 가린다)
  - 레벨 이름(`levels.name`): call_wall·put_wall·abs_gamma(행사가)·flip(교차 목록·multi_cross)·flip_distance·expected_move_calendar·expected_move_trading(±1σ, detail 에 F·범위·ATM IV)·top_levels(레벨 수, detail 에 목록 — 달력 기준 ±1σ). 지표(`metrics`): net_gex·dex(범위), atm_iv·expiry_gamma(series — 만기 목록 품질은 캘린더 최종거래일이 있으면 estimated). Phase 2 산출은 모두 `flag = visible`
  - `option_iv` 는 IV 를 시도한 종목만(가격·F 없어 역산하지 않은 종목은 행이 없다 — 한 사이클 수천 행을 줄인다)
  - 확장 지표 등록부 `services/engine/registry.py`: `MetricPlugin(name, flag, compute)`, 플래그 off 는 부르지 않고 shadow 는 저장만(기본 — 새 지표), 예외는 그 지표만 invalid
- **하트비트**: `health:heartbeat:engine`, compose 에 `engine` 서비스(healthcheck·`restart: always`·multi-arch 이미지 공용)
- 구현(2026-09-29) — 서비스 `services/engine/service.py`(진입점 `python -m services.engine`)·`context.py`·`publish.py`, compose `engine`(앱 이미지 공용·`restart: always`·하트비트 healthcheck 60초, redis·migrate 만 기다린다 — KIS 를 부르지 않는다). 정한 기본값 **[확인 필요]**:
  - 사이클: `chain.ready` 를 1초 대기로 받아 그 ts 가 마지막 as_of 보다 늦으면 사이클. 10초마다 그 세션(`session_tag(지금)`, 장 밖이면 5분 전 세션 — 마감 직후 마지막 행) `chain_snapshots` 로 따라잡기 — 알림 여유(poller 최대 대기 30초 + 5초 = 35초) **[확인 필요]** 보다 오래된 행이 마지막 as_of 뒤에 있을 때만(그 알림을 놓쳤다) 그 세션 max(ts) 까지 한 사이클. 여유 안의 행은 알림이 오는 중이라 보지 않는다 — 따라잡기가 알림을 앞질러 전광판이 반쯤 온 사이클을 돌지 않게(하루 시험에서 여유 없이는 사이클 212번 중 157번이 따라잡기였다 — 평가 주기가 ≈30초가 아니라 10초, `option_iv` 행이 3배). 재기동하면 `engine:latest` 의 as_of 를 이어 받아 이미 계산한 사이클은 다시 하지 않는다
  - 입력 읽기·평가가 통째로 실패하면 as_of 를 올리지 않아 다음 따라잡기(10초 — 여유 없이)에서 다시 한다. 그동안 `engine:latest` 는 직전 값에 `stale`(`stale_reason` cycle_failed·no_s_ref, `stale_at`, 품질 전부 stale 이상 — 사이클·레벨·지표와 S_ref·시리즈 F·입력 품질까지, 검토 수정 E4: 전엔 S_ref·시리즈 품질이 ok 로 남았다)
  - 표 하나의 쓰기 실패는 health `engine_write_failed` 만(나머지 쓰기·발행은 한다). Redis 장애는 로그·통계만 — DB 저장은 계속
  - health(`engine_no_s_ref`·`engine_no_near_code`·`engine_series_failed`·`engine_series_stale`·`engine_level_failed`·`engine_metric_failed`·`engine_no_expiry`·`engine_basis_rolled`·`engine_basis_invalid`·`engine_rows_failed`·`engine_cycle_failed`·`engine_write_failed`·`engine_read_failed`·`engine_near_code_fallback`)는 같은 (종류, 대상)을 10분에 한 번, 로그는 매번. 사이클 실패의 대상은 (귀속 거래일, 세션, 예외 종류) — as_of 는 detail·로그에만(검토 수정 E3: 대상 없이 detail 로 묶어 as_of 마다 새 health 가 났다 — 실패가 이어지면 알림마다). 묶음 기록은 10분 지난 것을 버린다
  - `engine:latest`(`EngineLatest`): as_of·trade_date·session·quality(사이클)·computed_at·near_code·s_ref(값·품질)·시리즈 요약(F·품질·사유·기록 코드·베이시스 나이)·레벨·visible 지표. `engine.levels`(`EngineLevels`)·`engine.metrics`(`EngineMetrics`, visible 만) 발행

## 2. 산출 저장 (새 마이그레이션 파일 — 그때의 다음 번호)

| 표 | 키 | 내용 |
|---|---|---|
| `levels` | (ts, scope, name) | 콜월·풋월·절대감마·Flip(교차 목록 jsonb)·전환점 거리·기대변동폭(달력·거래 기준)·기대범위 상위 레벨 — `value`, `detail jsonb`, `quality`, `reasons` |
| `metrics` | (ts, metric, scope, key) | 순GEX·DEX·VEX·CEX·GEX P/C·기간구조·25Δ 스큐·IV 랭크/퍼센타일·IV−HV·HIRO-lite·PCR·맥스페인·딜러 점검·선물 필드 — `value numeric`, `payload jsonb`, `quality`, `flag`(off·shadow·visible 중 계산 당시 값) |
| `strike_gex` | (ts, mrkt_cls, expiry, strike) | 행사가별 GEX 콜·풋·순(대시보드 막대) |
| `option_iv` | (ts, mrkt_cls, expiry, strike, cp) | 자체 IV·출처(self·kis)·`rescaled`·`t_kis`·사유·델타·감마(스마일·HIRO 입력). 검증 수정 3 열린 문제: 옵션별 IV 를 처음 저장하는 곳이 세 필드를 함께 저장 |
| `oi_changes` | (ts, mrkt_cls, expiry, strike, cp) | OI 증감·이상치 격리 표시(metrics §6.7) |

- 모든 행에 `trade_date`·`session`·`quality`. hypertable, `ts` 1일 청크
- 구현(2026-09-29, `db/migrations/004_flags.sql`): `levels`·`oi_changes` 에도 계산 당시 `flag`(003 은 적용된 뒤라 열을 더한다 — 이미 있는 행은 그때 값: 레벨 visible, OI 증감 shadow). 같은 사이클 재계산이면 플래그도 최신
- Redis 발행 메시지는 같은 pydantic 모델(`services/bus.py`) — `quality`·`as_of` 필수, `shadow` 는 발행하지 않는다(§4)
- 구현(2026-09-29, `db/migrations/003_engine.sql`·`services/engine/records.py`·`data/store.py`): 다섯 표 모두 hypertable(ts 1일), ts = 사이클 기준 시각(as_of — `chain.ready` 의 ts), 키에 시장분류(WKM·WKI 261001). 같은 사이클을 다시 계산하면 DO UPDATE(멱등). `levels.value`·`metrics.value` 는 NULL 가능(해당 없음·invalid), `detail`·`payload`·`reasons` 는 jsonb. `metrics.scope` 는 all·nearest·0dte 와 `series`(만기 하나 — key 가 시리즈 라벨 `WKM:261001`). `option_iv` 는 쓴 σ(`iv`)·`source`(self·kis)·`rescaled`·`t_kis`·`reason`·자체 `delta`·`gamma`(GEX 에 든 종목만)·`excluded`·`forward`·`t_years` — DB 제약으로 iv·source 는 함께, rescaled 면 t_kis. engine 입력 읽기(`chain_latest`·`futures_latest`·`chain_max_ts`·`expiry_dates`)는 그 세션(귀속 거래일·세션) 행만, 검증 실패 행은 건너뛰어 그 앞의 성한 행

## 3. 지표 모듈 (`core/metrics/`, pyright strict)

| 모듈 | metrics.md | 입력 | 주기 |
|---|---|---|---|
| `exposure.py` | §4.1 Vanna, §4.2 Charm, §4.3 GEX P/C | `ExpiryEval`(자체 σ·T·F), OI | 사이클 / Charm 2분 |
| `vol.py` | §5.2 기간구조, §5.3 25Δ 스큐, §5.4 IV 랭크·퍼센타일, §5.5 IV−HV | `ExpiryEval`, 일별 ATM IV 이력(`metrics` 표 + KRX `IMP_VOLT` 백필), KRX 선물 정산가 | 사이클 / 일별 |
| `flow.py` | §6.1 HIRO-lite, §6.3 딜러 점검, §6.4 대량 체결, §6.5 PCR, §6.6 맥스페인, §6.7 OI 증감 | `ticks.opt`(cum_buy·cum_sell), 최근 자체 델타, `investor_flow`, 체인 OI·거래량 | 틱 / 사이클 / 일별 |
| `futures.py` | §7 베이시스·괴리·OI 증감·체결강도·교차검증 | `fut_board`·KIS 선물 필드 | 사이클 |

- 입력 품질이 `ok` 가 아니면 산출 품질에 합성(각 절의 품질 규칙). 레코드 부족·분모 0 은 명세대로 null
- HIRO-lite: 웹소켓 끊김·시퀀스 공백·세션 전환이면 누적 0 리셋(명세). 누적 수량 역행 틱은 버리고 health. 항상 `estimated`
- 대량 체결 p99 기준은 20거래일 녹화가 쌓여야 켜진다 — 그 전엔 비활성(명세)

## 4. 기능 플래그·섀도 (metrics §8, PLAN §6.4)

- `config/features.yaml`: 지표·위젯·알림·전략 이름 → `off | shadow | visible`. 새 지표 기본 `shadow`. Phase 2 핵심(순GEX·DEX·콜월·풋월·절대감마·Flip·전환점 거리·기대변동폭·ATM IV)은 `visible`
- `core/features.py`: pydantic 검증 로더(모르는 이름·값은 기동 실패), 30초마다 파일 mtime 으로 다시 읽기. `off` = 계산 안 함, `shadow` = 계산·저장(`flag = shadow`), 발행·알림 안 함, `visible` = 전부
- 섀도 운영 점검: `scripts/shadow_report.py` — 기간 동안 지표별 계산 횟수·`invalid` 비율·예외 수·null 비율. ⏱ 1주 무오류(예외 0, 명세 밖 null 0)가 완료 기준
- 구현(2026-09-29) — 플래그: `config/features.yaml`(구역마다 카탈로그 이름만 — `core.features.CATALOG`, 적지 않은 이름은 기본값), engine 은 기동 때 읽고(틀리면 — 모르는 이름·값, 중복 키 — 종료 코드 2) 곁일이 30초마다 파일 서명을 보고 다시 읽는다(틀리면 직전 플래그 + health `engine_flags_invalid`). Phase 2 핵심 레벨·지표도 플래그를 따른다(shadow 면 저장만 — `engine.levels`·`engine:latest` 에서 빠진다, off 는 로더가 막는다 **[확인 필요]**). 확장 지표(사이클 등록부·일별·플로우·선물·OI 증감)는 off 면 부르지 않는다(HIRO 는 대량 체결과 틱을 나눠 쓴다 — `hiro` off 면 순번·대량 체결만 보고 누적을 버려, 다시 켜면 처음부터). compose 는 플래그 파일을 engine 에 읽기 전용으로 마운트 — 제자리 편집만 보인다(편집기가 파일을 새로 만들면 engine 재기동)
- 구현(2026-09-30) — 점검: `uv run python -m scripts.shadow_report [--end YYYY-MM-DD] [--days 5] [--flag shadow|visible|off|all]`. 저장소 읽기 세 개(`data.store.PostgresSink.engine_output_counts`·`engine_null_groups`·`engine_failures` — 귀속 거래일 범위, null 은 행 플래그별로 묶는다), 명세 null 규칙은 metrics §8. 종료 코드 0 충족·1 미충족·2 설정·DB 오류. 사이클·시리즈·행 만들기 예외 health 도 무오류를 막는다 **[확인 필요]**

## 5. 지표별 검증 리포트 (완료 기준)

`scripts/metric_report.py` → `docs/metric_validation.md`: 지표마다 ① 명세 테스트(단위·속성) 결과, ② 2026-09-28 체인 스냅샷 두 장(로컬, git 제외)에 대한 값·품질, ③ 교차 확인(아래), ④ 남은 [확인 필요].

- Vanna·Charm: 수치 미분 대조(명세 테스트), 스냅샷 값의 부호·크기 상식 점검
- 25Δ 스큐·기간구조: 스냅샷 스마일에서 수동 계산 대조
- PCR·맥스페인: KRX 09-23 일별(fixture)로 독립 계산 대조
- IV 랭크·HV: KRX 일별 fixture 로 손계산 대조(252일 이력은 Phase 6 백필 뒤)
- 선물: KIS `basis` 대 자체 `선물가 − kospi200_nmix`
- 구현(2026-09-30): `uv run python -m scripts.metric_report --probe-dir <probe_out> --write` — ① pytest 를 따로 돌려 JUnit 으로 지표마다 센다(`ENTRIES` — 시험 파일·이름 패턴, 패턴이 시험을 잃으면 단위 테스트가 걸린다), ② 스냅샷을 engine 입력으로(`snapshot_cycle` — 시험 가짜와 같은 함수) engine 사이클 + 등록부에 시각 순으로, OI 증감은 두 장 사이. 로컬 스냅샷이 없으면 git 의 작은 발췌(CI), ③ 교차 확인은 구현과 따로 짠 계산: Vanna·Charm 은 vollib 델타의 중앙 차분(종목마다·범위 합)과 계약당 |CEX| 상식 점검, GEX P/C 는 행사가별 GEX 행으로, 스큐·기간구조는 손 보간, PCR·맥스페인은 체인 행과 KRX 일별 fixture(이름 문자열을 따로 풀어, 맥스페인은 식 그대로 전수), IV 랭크·HV 는 fixture(하루뿐 — null 확인)와 SYNTHETIC 이력 손계산, 투자자·딜러 점검은 KIS 투자자 fixture, 선물은 분봉 fixture + probe 분봉 응답(KIS basis = 이론 베이시스 — metrics §7 [확인 필요]), ④ metrics.md 그 절의 표시. 명세 테스트 실패(파일 수집 실패는 그 파일을 쓰는 지표마다 실패 — pytest 는 수집 실패에서 멈춘다)·명세 테스트 없음(시험을 돌렸는데 0 건인 지표·묶음)·교차 확인 불일치면 종료 코드 1

## 6. 테스트

- 단위·속성: metrics.md 각 절의 "테스트" 줄 전부(해석식 대 수치 미분, 부호, 상쇄, 구간 밖 null, 동률 규칙, 리셋 규칙)
- 서비스: 가짜 입력(DB 행·Redis 알림)으로 한 사이클 → 표·채널 산출, 지표 하나가 예외여도 나머지 산출, shadow 미발행, 야간 행만 사용
- 통합(integration·docker): 하루 시험(`tests/integration/test_full_day.py`)에 engine 을 붙여 사이클마다 `levels` 가 쌓이고 전이·15:20 만기 전환에서 끊기지 않는지
  - 구현(2026-09-29): poller 에 `ReadyTap`·`ReadyNotifier`, engine 은 run 루프 한 번(`on_message` + `tick`)을 걸음마다 가짜 시계로. 확인 — 사이클마다 levels 세 범위 × 여덟 레벨과 `engine.levels` 하나, 열린 세션 창마다 끊김 없이(알림 간격 35초 안, 자정 창은 알림을 버려 따라잡기만으로 50초 안), 창이 닫힌 뒤에도 마지막 행까지 따라잡기. 15:20 WKM 260904 는 걸친 사이클까지만 평가하고 그 뒤 0DTE 빔·nearest WKI 261001·all 에 새 추적 WKM 261001. 15:45~18:00 사이클 없음, 야간은 야간 단건 행만(`option_iv.quote_source = fill`), 마지막 `engine:latest` 는 야간 귀속·근월물 A01612·S_ref ok. 사이클 실패·S_ref 없음 0, engine health 는 옛 행(`engine_series_stale`)만 — 시계를 건너뛴 창의 첫머리(건너뛴 동안 poller 가 돌지 않아 직전 창 행이 묵는다 — 운영엔 없는 틈)에만. 가짜 체인은 모든 행사가가 같은 가격(fixture 한 행)이라 값의 의미는 보지 않는다 — 흐름·연속성만

## 7. 구현 순서 (커밋 = 기능 하나 + 테스트)

1. `chain.ready` 계약 + poller 발행 + engine 골격(입력 읽기·시리즈 키·야간 분기·평가·`levels`·`strike_gex`·`option_iv` 저장·발행·베이시스 상태·격리·하트비트·compose) — 구현 끝(2026-09-29, §1·§2·§6 구현 줄). 운영 기준(1주 섀도)은 전용 앱키 뒤
2. `exposure.py`·`vol.py` + engine 연결
3. `flow.py`·`futures.py` + engine 연결(틱 구독)
4. 기능 플래그·섀도 + `shadow_report.py`·`metric_report.py`·`docs/metric_validation.md` — 구현 끝(2026-09-30, §4·§5 구현 줄). 운영 기준(1주 섀도 무오류)은 전용 앱키 뒤(PLAN §12)
