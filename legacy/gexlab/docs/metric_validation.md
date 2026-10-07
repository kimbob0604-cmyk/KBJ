# 지표별 검증 리포트 (Phase 3 완료 기준)

> 자동 생성 — `uv run python -m scripts.metric_report --probe-dir <probe_out> --write`. 손으로 고치지 않는다. 설계 `docs/phase3_design.md` §5, 명세 `docs/metrics.md` §4~§8, 기능 플래그 `config/features.yaml`.

- 스냅샷: git 에 있는 작은 발췌(tests/fixtures/validation — 로컬 전체 스냅샷이 없을 때) — `tests/fixtures/validation/chain_snapshot_synthetic_20260928_1427_small.json` (9578c6f28b12), `tests/fixtures/validation/chain_snapshot_synthetic_20260928_1452_small.json` (94eb3f2466a9)
  - engine 사이클(`services.engine.evaluate.evaluate_cycle` + engine 등록부)에 시각 순으로 — 확정 베이시스·OI 추적을 다음 장에 넘긴다. 두 장 모두 2026-09-28(월) 주간, 최근접·0DTE = WKM 260904(그날 15:20 만기)
- fixture: `tests/fixtures/krx/opt_daily.json`, `tests/fixtures/krx/fut_daily.json`, `tests/fixtures/kis/minute_day.json`, `tests/fixtures/kis/investor.json`
- 명세 테스트: pytest 1013건 — 지표마다 그 지표의 단위·속성·engine 시험(`scripts/metric_report.py` `ENTRIES`)
- 판정: 명세 테스트 실패 0 · 교차 확인 불일치 0
- 새 지표는 기본 `shadow`. `visible` 로 올리는 조건(metrics §8): 이 리포트의 교차 확인·육안 검토 통과 + ⏱ 1주 섀도 운영 무오류(`scripts.shadow_report` — 전용 앱키로 라이브 녹화가 쌓인 뒤, PLAN §12)

## 요약

| 플래그 | 지표 | metrics.md | 지금 | 명세 테스트 | 스냅샷 | 교차 확인 | 남은 [확인 필요] |
|---|---|---|---|---|---|---|---|
| vex | Vanna 익스포저 | §4.1 | shadow | 200 통과 | 있음 | 일치 | 0 |
| cex | Charm 익스포저 | §4.2 | shadow | 203 통과 | 있음 | 일치 | 1 |
| gex_pc | GEX P/C 비율 | §4.3 | shadow | 5 통과 | 있음 | 일치 | 0 |
| iv_term | IV 기간구조 | §5.2 | shadow | 10 통과 | 있음 | 일치 | 2 |
| skew_25d | 25Δ 스큐 | §5.3 | shadow | 11 통과 | 있음 | 일치 | 1 |
| atm_iv_daily | 일별 월물 ATM IV | §5.4 | shadow | 12 통과 | - | 일치 | 4 |
| iv_rank | IV 랭크 | §5.4 | shadow | 6 통과 | - | 일치 | 4 |
| iv_percentile | IV 퍼센타일 | §5.4 | shadow | 6 통과 | - | 일치 | 4 |
| iv_hv | IV − HV20 | §5.5 | shadow | 7 통과 | - | 일치 | 3 |
| hiro | HIRO-lite | §6.1 | shadow | 29 통과 | - | - | 3 |
| investor_flow | 투자자별 순매수 | §6.2 | shadow | 5 통과 | - | 일치 | 1 |
| dealer_check | 딜러 가정 점검 | §6.3 | shadow | 9 통과 | - | 일치 | 3 |
| block_trades | 대량 체결 | §6.4 | shadow | 10 통과 | - | - | 4 |
| pcr | PCR | §6.5 | shadow | 15 통과 | 있음 | 일치 | 2 |
| max_pain | 맥스페인 | §6.6 | shadow | 11 통과 | 있음 | 일치 | 2 |
| oi_changes | OI 증감 히트맵 | §6.7 | shadow | 22 통과 | 있음 | 일치 | 3 |
| futures | 선물 베이시스·괴리율·OI 증감·체결강도 | §7 | shadow | 24 통과 | - | 일치 | 6 |

## Phase 2 핵심 (visible) — 스냅샷 값

명세 테스트(부호 고정·GEX·레벨·골든·engine 평가): 248 통과 (tests/golden/test_core_golden.py, tests/property/test_gex_properties.py, tests/property/test_levels_properties.py, tests/test_sign_conventions.py, tests/unit/test_engine_evaluate.py, tests/unit/test_gex.py, tests/unit/test_levels.py)

| 산출 | 범위 | 키 | 14:27 | 14:52 |
|---|---|---|---|---|
| net_gex | 0dte | - | -1,400.1억 · estimated | -15,086.0억 · estimated |
| net_gex | all | - | -1,252.7억 · invalid | -14,874.7억 · invalid |
| net_gex | nearest | - | -1,400.1억 · estimated | -15,086.0억 · estimated |
| dex | 0dte | - | 19,441.6억 · estimated | 17,163.1억 · estimated |
| dex | all | - | 29,312.5억 · invalid | 27,988.1억 · invalid |
| dex | nearest | - | 19,441.6억 · estimated | 17,163.1억 · estimated |
| atm_iv | series | M:202610 | 41.48% · ok | 41.22% · ok |
| atm_iv | series | M:202611 | null · invalid (no_forward) | null · invalid (no_forward) |
| atm_iv | series | WKM:260904 | 51.07% · ok | 53.68% · ok |
| atm_iv | series | WKM:261001 | null · invalid (no_forward) | null · invalid (no_forward) |
| expiry_gamma | series | M:202610 | 147.3억 · estimated | 211.3억 · estimated |
| expiry_gamma | series | M:202611 | null · invalid | null · invalid |
| expiry_gamma | series | WKM:260904 | -1,400.1억 · estimated | -15,086.0억 · estimated |
| expiry_gamma | series | WKM:261001 | null · invalid | null · invalid |

| 레벨 | 범위 | 14:27 | 14:52 |
|---|---|---|---|
| call_wall | all | 1097.50 · invalid | 1092.50 · invalid |
| put_wall | all | 1097.50 · invalid | 1090.00 · invalid |
| abs_gamma | all | 1097.50 · invalid | 1090.00 · invalid |
| flip | all | 1088.74 · invalid | 1072.56 · invalid |
| flip_distance | all | +0.523% · invalid | +1.769% · invalid |
| expected_move_calendar | all | 6.79 · ok | 5.84 · ok |
| expected_move_trading | all | 15.14 · ok | 13.02 · ok |
| top_levels | all | 5 · invalid | 5 · invalid |
| call_wall | nearest | 1097.50 · estimated | 1092.50 · estimated |
| put_wall | nearest | 1097.50 · estimated | 1090.00 · estimated |
| abs_gamma | nearest | 1097.50 · estimated | 1090.00 · estimated |
| flip | nearest | 1086.86 · estimated | null · estimated |
| flip_distance | nearest | +0.695% · estimated | null · estimated |
| expected_move_calendar | nearest | 6.79 · ok | 5.84 · ok |
| expected_move_trading | nearest | 15.14 · ok | 13.02 · ok |
| top_levels | nearest | 5 · estimated | 5 · estimated |
| call_wall | 0dte | 1097.50 · estimated | 1092.50 · estimated |
| put_wall | 0dte | 1097.50 · estimated | 1090.00 · estimated |
| abs_gamma | 0dte | 1097.50 · estimated | 1090.00 · estimated |
| flip | 0dte | 1086.86 · estimated | null · estimated |
| flip_distance | 0dte | +0.695% · estimated | null · estimated |
| expected_move_calendar | 0dte | 6.79 · ok | 5.84 · ok |
| expected_move_trading | 0dte | 15.14 · ok | 13.02 · ok |
| top_levels | 0dte | 5 · estimated | 5 · estimated |

값 표기: 금액 억원(원 ÷ 1e8, GEX 는 원/1%), IV 연율 %, 스큐 %p, 가격·레벨 pt. 품질 뒤 괄호는 사유(둘까지). F 없는 시리즈(M:202611·WKM:261001 — 검증 수정 4 열린 문제, 거래 없는 행사가)가 범위 all 을 invalid 로 만든다

## 지표별

### vex — §4.1 Vanna 익스포저

- 플래그: 기본 `shadow`, 지금 `shadow` · 산출 `vex`
- 명세 테스트: 200 통과 — `tests/property/test_exposure_properties.py`, `tests/unit/test_engine_extended.py`, `tests/unit/test_exposure.py`, `tests/unit/test_greeks.py`

스냅샷 값:

| 산출 | 범위 | 키 | 14:27 | 14:52 |
|---|---|---|---|---|
| vex | 0dte | - | -18.5억 · estimated | -8.4억 · estimated |
| vex | all | - | -17.0억 · invalid | -4.8억 · invalid |
| vex | nearest | - | -18.5억 · estimated | -8.4억 · estimated |

**Vanna 해석식 대 수치 미분(∂Δ/∂σ)** — 일치

| 스냅샷 | 종목 | Vanna 최대 상대오차 | 허용 밖 | VEX all engine | VEX all 수치 미분 | 차 |
|---|---|---|---|---|---|---|
| 14:27 | 36 | 4.1e-09 | 0 | -17.0억 | -17.0억 | +2.64e-09억 |
| 14:52 | 36 | 8.4e-09 | 0 | -4.8억 | -4.8억 | +4.64e-09억 |

허용: 종목마다 |해석식 − 중앙 차분| ≤ 1e-05·|해석식| + 1e-9 (σ ± 1e-05, T ± T·0.0001), 범위 합 상대 1e-05. 수치 미분은 vollib 해석 델타(`core.greeks.greeks`)를 σ·T 로 흔든 것 — 해석식 `core.greeks.vanna·charm` 과 따로다

남은 [확인 필요]: 없음

### cex — §4.2 Charm 익스포저

- 플래그: 기본 `shadow`, 지금 `shadow` · 산출 `cex`
- 명세 테스트: 203 통과 — `tests/property/test_exposure_properties.py`, `tests/unit/test_engine_extended.py`, `tests/unit/test_exposure.py`, `tests/unit/test_greeks.py`

스냅샷 값:

| 산출 | 범위 | 키 | 14:27 | 14:52 |
|---|---|---|---|---|
| cex | 0dte | - | 14,091.3억 · estimated | 16,884.6억 · estimated |
| cex | all | - | 14,079.1억 · invalid | 16,869.5억 · invalid |
| cex | nearest | - | 14,091.3억 · estimated | 16,884.6억 · estimated |

**Charm 해석식 대 수치 미분(−∂Δ/∂T ÷ 365)** — 일치

| 스냅샷 | 종목 | Charm 최대 상대오차 | 허용 밖 | CEX all engine | CEX all 수치 미분 | 차 |
|---|---|---|---|---|---|---|
| 14:27 | 36 | 3.1e-08 | 0 | 14,079.1억 | 14,079.1억 | -4.00e-05억 |
| 14:52 | 36 | 6.3e-08 | 0 | 16,869.5억 | 16,869.5억 | -2.27e-05억 |

허용: 종목마다 |해석식 − 중앙 차분| ≤ 1e-05·|해석식| + 1e-9 (σ ± 1e-05, T ± T·0.0001), 범위 합 상대 1e-05. 수치 미분은 vollib 해석 델타(`core.greeks.greeks`)를 σ·T 로 흔든 것 — 해석식 `core.greeks.vanna·charm` 과 따로다

시리즈별 계약당 익스포저(상식 점검 — 만기가 가까울수록 |CEX|/OI 가 커야 한다, §4.2):

| 스냅샷 | 시리즈 | T(일) | 포함 OI | VEX/OI(원) | CEX/OI(원) |
|---|---|---|---|---|---|
| 14:27 | WKM:260904 | 0.04 | 13,054 | -141,734 | 107,946,257 |
| 14:27 | M:202610 | 10.04 | 14,782 | 10,122 | -82,716 |
| 14:52 | WKM:260904 | 0.02 | 11,869 | -70,982 | 142,257,883 |
| 14:52 | M:202610 | 10.02 | 13,068 | 27,615 | -115,445 |

- 14:27: 가장 가까운 만기 WKM:260904 의 계약당 CEX 크기가 가장 크다
- 14:52: 가장 가까운 만기 WKM:260904 의 계약당 CEX 크기가 가장 크다

남은 [확인 필요] (§4.2 — 1):
- …2분 주기는 사이클 시각(as_of)으로 센다 — 같은 세션에서 마지막 계산 뒤 120초 이상인 사이클, 세션이 바뀌면 바로 [확인 필요]

### gex_pc — §4.3 GEX P/C 비율

- 플래그: 기본 `shadow`, 지금 `shadow` · 산출 `gex_pc`
- 명세 테스트: 5 통과 — `tests/property/test_exposure_properties.py`, `tests/unit/test_engine_extended.py`, `tests/unit/test_exposure.py`

스냅샷 값:

| 산출 | 범위 | 키 | 14:27 | 14:52 |
|---|---|---|---|---|
| gex_pc | 0dte | - | 1.1419 · estimated | 7.2263 · estimated |
| gex_pc | all | - | 1.1188 · invalid | 5.6549 · invalid |
| gex_pc | nearest | - | 1.1419 · estimated | 7.2263 · estimated |

**GEX P/C 를 행사가별 GEX 행으로** — 일치

| 스냅샷 | 범위 | Σ 콜 GEX | Σ 풋 GEX | 다시 잰 비율 | engine | 일치 |
|---|---|---|---|---|---|---|
| 14:27 | all | 10,543.1억 | -11,795.9억 | 1.1188 | 1.1188 · invalid | 예 |
| 14:27 | nearest | 9,868.4억 | -11,268.5억 | 1.1419 | 1.1419 · estimated | 예 |
| 14:27 | 0dte | 9,868.4억 | -11,268.5억 | 1.1419 | 1.1419 · estimated | 예 |
| 14:52 | all | 3,195.5억 | -18,070.2억 | 5.6549 | 5.6549 · invalid | 예 |
| 14:52 | nearest | 2,423.0억 | -17,509.0억 | 7.2263 | 7.2263 · estimated | 예 |
| 14:52 | 0dte | 2,423.0억 | -17,509.0억 | 7.2263 | 7.2263 · estimated | 예 |

남은 [확인 필요]: 없음

### iv_term — §5.2 IV 기간구조

- 플래그: 기본 `shadow`, 지금 `shadow` · 산출 `iv_term`
- 명세 테스트: 10 통과 — `tests/unit/test_engine_extended.py`, `tests/unit/test_vol.py`

스냅샷 값:

| 산출 | 범위 | 키 | 14:27 | 14:52 |
|---|---|---|---|---|
| iv_term | all | 0dte | 51.07% · ok | 53.68% · ok |
| iv_term | all | monthly | 41.48% · ok | 41.22% · ok |
| iv_term | all | next_weekly | null · invalid (no_forward) | null · invalid (no_forward) |

**IV 기간구조 칸의 ATM IV 손계산** — 일치

| 스냅샷 | 칸 | 만기 | 손계산 ATM IV | engine | 일치 |
|---|---|---|---|---|---|
| 14:27 | 0dte | WKM:260904 | 51.07% | 51.07% · ok | 예 |
| 14:27 | next_weekly | WKM:261001 | null | null · invalid (no_forward) | 예 |
| 14:27 | monthly | M:202610 | 41.48% | 41.48% · ok | 예 |
| 14:52 | 0dte | WKM:260904 | 53.68% | 53.68% · ok | 예 |
| 14:52 | next_weekly | WKM:261001 | null | null · invalid (no_forward) | 예 |
| 14:52 | monthly | M:202610 | 41.22% | 41.22% · ok | 예 |

남은 [확인 필요] (§5.2 — 2):
- ….levels.atm_iv). 기본값 [확인 필요]: 차기 위클리 = 만기일이 귀속 거래일보다 뒤인 가장 이른 위클리(오늘 만기 위클리는 0DTE 칸), 월물 = 만기일이 귀속 거…
- …ated(series_no_expiry) — 빈 칸이어도 같고, 범위 지표(§2.2 engine 범위 입력 품질)와 같은 규칙 [확인 필요]

### skew_25d — §5.3 25Δ 스큐

- 플래그: 기본 `shadow`, 지금 `shadow` · 산출 `skew_25d`
- 명세 테스트: 11 통과 — `tests/unit/test_engine_extended.py`, `tests/unit/test_vol.py`

스냅샷 값:

| 산출 | 범위 | 키 | 14:27 | 14:52 |
|---|---|---|---|---|
| skew_25d | series | M:202610 | null · ok (put_out_of_range) | null · ok (put_out_of_range) |
| skew_25d | series | M:202611 | null · invalid (no_forward) | null · invalid (no_forward) |
| skew_25d | series | WKM:260904 | +1.13%p · ok | +1.62%p · ok |
| skew_25d | series | WKM:261001 | null · invalid (no_forward) | null · invalid (no_forward) |

**25Δ 스큐 손 보간** — 일치

| 스냅샷 | 시리즈 | 풋 σ(−25Δ) | 콜 σ(+25Δ) | 손 보간 스큐 | engine | 일치 |
|---|---|---|---|---|---|---|
| 14:27 | M:202610 | null | 49.82% | null | null · ok (put_out_of_range) | 예 |
| 14:27 | WKM:260904 | 52.76% | 51.63% | +1.13%p | +1.13%p · ok | 예 |
| 14:52 | M:202610 | null | 49.63% | null | null · ok (put_out_of_range) | 예 |
| 14:52 | WKM:260904 | 57.48% | 55.86% | +1.62%p | +1.62%p · ok | 예 |

남은 [확인 필요] (§5.3 — 1):
- …점이 있으면 그 IV 평균). 기본값 [확인 필요]: 구간 밖·한쪽 종목 없음 null 은 품질 ok(사유 put_out_of_range·call_out_of_range·no_pu…

### atm_iv_daily — §5.4 일별 월물 ATM IV

- 플래그: 기본 `shadow`, 지금 `shadow` · 산출 `atm_iv_daily`
- 명세 테스트: 12 통과 — `tests/unit/test_engine_daily.py`, `tests/unit/test_vol.py`

스냅샷 값: 없음 — POST_DAY 일별(그날 주간 마지막 사이클의 월물 ATM IV·KRX 일별 백필) — 아래 KRX fixture 손계산

**KRX 일별 ATM IV 손계산(§5.4 과거 값)** — 일치

| 거래일 | 만기 | 거래 있는 행 | ATM 기준 | 손계산 F | core F | 손계산 ATM IV | core | 일치 |
|---|---|---|---|---|---|---|---|---|
| 2026-09-23 | 202610 | 2 | 1120.35 | 1119.40 | 1119.40 · estimated | 37.50% | 37.50% · estimated (one_side) | 예 |

fixture 에 거래 있는 정규 행사가가 1100 하나뿐 — F = 1100 + C − P, F 가 그 위라 §3.7 한쪽 행사가만(`one_side` estimated). ATM 기준(§1.3 ATM 선정)은 fixture 의 미니 202610 주간 정산가(코스피200 선물 행이 fixture 에 없다 — 행사가 하나라 결과는 같다)

남은 [확인 필요] (§5.4 — 4):
- 예외: n < 252 면 있는 만큼으로 계산하고 estimated [확인 필요]
- n < 20 이면 null [확인 필요]
- …: KRX IMP_VOLT 와 자체 IV 는 산출 기준이 다를 수 있어(#18 보류) 두 원천을 섞은 창이면 estimated [확인 필요]
- …atm_iv 와 같은 규칙). 기본값 [확인 필요]: 창은 오늘을 포함한 252거래일(캘린더 거래일, 빠진 날은 n 이 준다), 품질은 오늘 값 품질에 위 두 규칙만(지난 값의 품…

### iv_rank — §5.4 IV 랭크

- 플래그: 기본 `shadow`, 지금 `shadow` · 산출 `iv_rank`
- 명세 테스트: 6 통과 — `tests/unit/test_engine_daily.py`, `tests/unit/test_vol.py`

스냅샷 값: 없음 — 일별 이력 20거래일 이상(252일 창) — Phase 6 백필 뒤. 아래 fixture·SYNTHETIC 손계산

**IV 랭크·퍼센타일 손계산** — 일치

- fixture(2026-09-23 KRX 값 하나): n = 1 → 랭크·퍼센타일 null·null (short_window, too_few_days) — 명세대로 null(n < 20)
- SYNTHETIC 25거래일(2026-08-20 ~ 2026-09-23, σ = 0.20 + 0.01·((7i) mod 11)): 손계산 min 0.20·max 0.30·오늘 0.23 → 랭크 0.300000·퍼센타일 0.280000 (7/25), core 0.3000·0.2800 · estimated (short_window) — 일치
- 252일 실측 이력 대조는 Phase 6 백필(KRX `IMP_VOLT`) 뒤

남은 [확인 필요] (§5.4 — 4):
- 예외: n < 252 면 있는 만큼으로 계산하고 estimated [확인 필요]
- n < 20 이면 null [확인 필요]
- …: KRX IMP_VOLT 와 자체 IV 는 산출 기준이 다를 수 있어(#18 보류) 두 원천을 섞은 창이면 estimated [확인 필요]
- …atm_iv 와 같은 규칙). 기본값 [확인 필요]: 창은 오늘을 포함한 252거래일(캘린더 거래일, 빠진 날은 n 이 준다), 품질은 오늘 값 품질에 위 두 규칙만(지난 값의 품…

### iv_percentile — §5.4 IV 퍼센타일

- 플래그: 기본 `shadow`, 지금 `shadow` · 산출 `iv_percentile`
- 명세 테스트: 6 통과 — `tests/unit/test_engine_daily.py`, `tests/unit/test_vol.py`

스냅샷 값: 없음 — iv_rank 와 같다

**IV 랭크·퍼센타일 손계산** — 일치

- fixture(2026-09-23 KRX 값 하나): n = 1 → 랭크·퍼센타일 null·null (short_window, too_few_days) — 명세대로 null(n < 20)
- SYNTHETIC 25거래일(2026-08-20 ~ 2026-09-23, σ = 0.20 + 0.01·((7i) mod 11)): 손계산 min 0.20·max 0.30·오늘 0.23 → 랭크 0.300000·퍼센타일 0.280000 (7/25), core 0.3000·0.2800 · estimated (short_window) — 일치
- 252일 실측 이력 대조는 Phase 6 백필(KRX `IMP_VOLT`) 뒤

남은 [확인 필요] (§5.4 — 4):
- 예외: n < 252 면 있는 만큼으로 계산하고 estimated [확인 필요]
- n < 20 이면 null [확인 필요]
- …: KRX IMP_VOLT 와 자체 IV 는 산출 기준이 다를 수 있어(#18 보류) 두 원천을 섞은 창이면 estimated [확인 필요]
- …atm_iv 와 같은 규칙). 기본값 [확인 필요]: 창은 오늘을 포함한 252거래일(캘린더 거래일, 빠진 날은 n 이 준다), 품질은 오늘 값 품질에 위 두 규칙만(지난 값의 품…

### iv_hv — §5.5 IV − HV20

- 플래그: 기본 `shadow`, 지금 `shadow` · 산출 `iv_hv`
- 명세 테스트: 7 통과 — `tests/unit/test_engine_daily.py`, `tests/unit/test_vol.py`

스냅샷 값: 없음 — KRX 선물 정산가 21거래일 — 아래 fixture·SYNTHETIC 손계산

**HV20 손계산** — 일치

- fixture(tests/fixtures/krx/fut_daily.json — 미니·야간 행뿐): HV20 null (no_data) — 명세대로 null(20일 안 됨)
- SYNTHETIC 21거래일 정산가(202612, ln 수익률 0.012·sin(1.7i)): 손계산 √(Σ(r − r̄)² / 19)·√252 = 13.856368%, core 13.86% — 일치

남은 [확인 필요] (§5.5 — 3):
- 공식: ATM IV − HV20, HV20 = stdev(ln(Pₜ/Pₜ₋₁), 최근 20거래일) × √252 [확인 필요: 연환산 √252]
- …act·iv_minus_hv. 기본값 [확인 필요]: stdev 는 표본표준편차(n − 1), 창 = 정산가가 있는 가장 늦은 거래일(as-of 이하 — POST_DAY 엔 KRX…
- …새 지표 기본 shadow). 기본값 [확인 필요]: 오늘 값 = 그날 주간 마지막 사이클의 월물 atm_iv 중 그날 가장 가까운 월물(최종거래일 — KIS 먼저, 없으면 캘린더…

### hiro — §6.1 HIRO-lite

- 플래그: 기본 `shadow`, 지금 `shadow` · 산출 `hiro`
- 명세 테스트: 29 통과 — `tests/property/test_flow_properties.py`, `tests/unit/test_engine_ticks.py`, `tests/unit/test_flow.py`

스냅샷 값: 없음 — ws-gateway 옵션 체결 틱(누적 매수·매도) 녹화 — 전용 앱키 뒤 라이브 재생. 식은 명세 테스트(흐름 = 순 signed × Δ × m × F 속성 포함)

남은 [확인 필요] (§6.1 — 3):
- 입력: 틱(cum_buy_qty·cum_sell_qty — 설계 §8), Δ 는 그 종목의 최근 자체 델타(§1.6) [확인 필요: 틱 시점이 아니라 최근 체인 평가 값]
- …품질은 늘 estimated. 기본값 [확인 필요]: 종목의 첫 틱(리셋 뒤 포함)은 기준만 잡고 흐름에 넣지 않는다(직전 누적이 없다 — 세션 누적 전체를 한 틱에 넣지 않게),…
- …(종목마다 10분에 한 번). 기본값 [확인 필요]: Δ·F 는 마지막 사이클의 option_iv 자체 델타·그 만기 F 로 그 사이클과 같은 세션(귀속 거래일·세션)의 틱에만(세…

### investor_flow — §6.2 투자자별 순매수

- 플래그: 기본 `shadow`, 지금 `shadow` · 산출 `investor_flow`
- 명세 테스트: 5 통과 — `tests/unit/test_engine_investor.py`

스냅샷 값: 없음 — poller investor_flow 행 — 아래 KIS fixture 로 원문 일관성

**투자자별 순매수 — KIS 원문 일관성** — 일치

| 조합 | 외국인 | 개인 | 기관계 | 증권 | 순매수 = 매수 − 매도 |
|---|---|---|---|---|---|
| K2I/F001 | -2,460 | -1,378 | +4,702 | +3,950 | 예 |
| K2I/OC01 | -2,769 | +1,333 | +1,323 | +1,278 | 예 |
| K2I/OP01 | -228 | -231 | +610 | +591 | 예 |
| WKI/OC04 | +358 | -361 | -5 | -5 | 예 |
| WKI/OP04 | +289 | -289 | +0 | +0 | 예 |
| WKM/OC05 | -626 | +14,225 | -14,252 | -14,115 | 예 |
| WKM/OP05 | +3,991 | -2,709 | -1,504 | -1,381 | 예 |

fixture: tests/fixtures/kis/investor.json(2026-09-28 13:25, 투자자 12종 중 보이는 넷). engine 은 값을 그대로 옮긴다

남은 [확인 필요] (§6.2 — 1):
- …·세션·품질은 그 행 그대로. 기본값 [확인 필요]: 주기 30초(poller 60초), 순매수 수량이 없는 행은 null·invalid(field_missing), 세션이 바뀌면…

### dealer_check — §6.3 딜러 가정 점검

- 플래그: 기본 `shadow`, 지금 `shadow` · 산출 `dealer_check`
- 명세 테스트: 9 통과 — `tests/unit/test_engine_investor.py`, `tests/unit/test_flow.py`

스냅샷 값: 없음 — 그날 주간 마지막 investor_flow 증권 행 — 아래 KIS fixture 손 판정

**딜러 가정 점검 손 판정** — 일치

- 증권(딜러 프록시) 콜 순매수 1278 + -14115 + -5 = -12,842, 풋 591 + -1381 + 0 = -790 → 손 판정 불일치, core False · ok (-) — 같다
- 장중 한 시점(13:25) 값이라 판정의 뜻은 없다 — engine 은 그날 주간 마지막 행으로 POST_DAY 에 한 번(§6.3)

남은 [확인 필요] (§6.3 — 3):
- 경고: 불일치가 연속 5거래일이면 대시보드 경고 배지 [확인 필요: 5일]
- …WARN_DAYS 5 이상). 기본값 [확인 필요]: 모르는 조합(None)은 빼고 estimated(pairs_missing), 한쪽을 통째로 모르면 판정 없음(None)·inv…
- …c_history)으로 센다. 기본값 [확인 필요]: 주간 행만(야간 투자자별은 미실측 #13 — 거래일 = 주간), 앞 20거래일까지 보고 행이 없는 날은 판정 없는 날로(연속을…

### block_trades — §6.4 대량 체결

- 플래그: 기본 `shadow`, 지금 `shadow` · 산출 `block_trades`, `block_trade`
- 명세 테스트: 10 통과 — `tests/property/test_flow_properties.py`, `tests/unit/test_engine_ticks.py`, `tests/unit/test_flow.py`

스냅샷 값: 없음 — opt_ticks 20거래일 — 그 전엔 비활성(명세). 기록 0거래일이면 `block_thresholds([])` 활성 = False

남은 [확인 필요] (§6.4 — 4):
- 머니니스 구간: K/F − 1 을 1% 단위로 자른 구간, 콜·풋 따로 [확인 필요]
- …기록 없는 구간은 None). 기본값 [확인 필요]: p99 는 nearest-rank(오름차순 ⌈0.99·n⌉ 번째 — 관측된 체결량), 창 = 기록이 있는 가장 최근 20거래일…
- …_tick_history — F = 그 틱 앞 10분 안의 같은 종목 engine F(option_iv), 못 찾은 틱은 뺀다 [확인 필요]
- …cope series, key 라벨:행사가:콜풋:수신 순번, 값 = 1틱 체결량, payload 에 기준·구간·F, 품질 ok [확인 필요]

### pcr — §6.5 PCR

- 플래그: 기본 `shadow`, 지금 `shadow` · 산출 `pcr_oi`, `pcr_volume`
- 명세 테스트: 15 통과 — `tests/property/test_flow_properties.py`, `tests/unit/test_engine_flow.py`, `tests/unit/test_flow.py`

스냅샷 값:

| 산출 | 범위 | 키 | 14:27 | 14:52 |
|---|---|---|---|---|
| pcr_oi | all | - | 0.5783 · ok | 1.2742 · ok |
| pcr_oi | series | M:202610 | 0.2576 · ok | 0.3287 · ok |
| pcr_oi | series | M:202611 | 0.0000 · ok | 0.0000 · ok |
| pcr_oi | series | WKM:260904 | 1.2472 · ok | 10.4235 · ok |
| pcr_oi | series | WKM:261001 | 0.0000 · ok | 0.0000 · ok |
| pcr_volume | all | - | 1.2812 · ok | 0.9403 · ok |
| pcr_volume | series | M:202610 | 0.1797 · ok | 0.1594 · ok |
| pcr_volume | series | M:202611 | 0.0000 · ok | 0.0000 · ok |
| pcr_volume | series | WKM:260904 | 1.3277 · ok | 0.9833 · ok |
| pcr_volume | series | WKM:261001 | 0.0000 · ok | 0.0000 · ok |

**PCR 을 체인 행으로** — 일치

| 스냅샷 | 시리즈 | 풋/콜 OI | 다시 잰 PCR(OI) | engine | 풋/콜 거래량 | 다시 잰 PCR(거래량) | engine | 일치 |
|---|---|---|---|---|---|---|---|---|
| 14:27 | M:202610 | 3,032/11,768 | 0.2576 | 0.2576 · ok | 516/2,871 | 0.1797 | 0.1797 · ok | 예 |
| 14:27 | M:202611 | 0/192 | 0.0000 | 0.0000 · ok | 0/6 | 0.0000 | 0.0000 · ok | 예 |
| 14:27 | WKM:260904 | 7,245/5,809 | 1.2472 | 1.2472 · ok | 90,599/68,239 | 1.3277 | 1.3277 · ok | 예 |
| 14:27 | WKM:261001 | 0/2 | 0.0000 | 0.0000 · ok | 0/2 | 0.0000 | 0.0000 · ok | 예 |
| 14:52 | M:202610 | 3,233/9,835 | 0.3287 | 0.3287 · ok | 565/3,545 | 0.1594 | 0.1594 · ok | 예 |
| 14:52 | M:202611 | 0/161 | 0.0000 | 0.0000 · ok | 0/6 | 0.0000 | 0.0000 · ok | 예 |
| 14:52 | WKM:260904 | 10,830/1,039 | 10.4235 | 10.4235 · ok | 63,455/64,530 | 0.9833 | 0.9833 · ok | 예 |
| 14:52 | WKM:261001 | 0/2 | 0.0000 | 0.0000 · ok | 0/2 | 0.0000 | 0.0000 · ok | 예 |

**PCR — KRX 일별 fixture 독립 재계산** — 일치

| 거래일 | 만기 | 풋/콜 OI | 손계산 PCR(OI) | core | 풋/콜 거래량 | 손계산 PCR(거래량) | core | 일치 |
|---|---|---|---|---|---|---|---|---|
| 20100104 | 201001 | 0/4,188 | 0.0000 | 0.0000 · ok | 0/5 | 0.0000 | 0.0000 · ok | 예 |
| 20100104 | 201003 | 978/0 | null | null · ok | 151/0 | null | null · ok | 예 |
| 20260923 | 202610 | 1,824/2,656 | 0.6867 | 0.6867 · ok | 887/72 | 12.3194 | 12.3194 · ok | 예 |
| 20260923 | 202812 | 256/0 | null | null · ok | 0/0 | null | null · ok | 예 |

fixture = KRX `/drv/opt_bydd_trd` 원본 발췌(tests/fixtures/krx/opt_daily.json — 행이 적다: 값의 뜻보다 원문 → 파서 → core 경로의 대조). 손계산은 `ISU_NM` 을 정규식으로 따로 풀고 합을 분수로, 맥스페인은 후보마다 식 그대로 — F 없음(동률이면 낮은 쪽)

남은 [확인 필요] (§6.5 — 2):
- …풋·OI·당일 누적 거래량). 기본값 [확인 필요]: 값을 모르는 종목(OI·거래량 None)은 합에서 빼고 그 비율을 estimated(oi_missing·volume_missi…
- …목의 OI·당일 누적 거래량. 기본값 [확인 필요]: F 를 쓰지 않아 품질에 S_ref 를 합성하지 않고 그 시리즈 체인 행 입력 품질(옛 행 stale·OI 없음 estimat…

### max_pain — §6.6 맥스페인

- 플래그: 기본 `shadow`, 지금 `shadow` · 산출 `max_pain`
- 명세 테스트: 11 통과 — `tests/property/test_flow_properties.py`, `tests/unit/test_engine_flow.py`, `tests/unit/test_flow.py`

스냅샷 값:

| 산출 | 범위 | 키 | 14:27 | 14:52 |
|---|---|---|---|---|
| max_pain | series | M:202610 | 1100.00 · ok | 1100.00 · ok |
| max_pain | series | M:202611 | 1347.50 · ok | 1347.50 · ok |
| max_pain | series | WKM:260904 | 1097.50 · ok | 1100.00 · ok |
| max_pain | series | WKM:261001 | 1092.50 · ok | 1090.00 · estimated (tie_without_forward) |

**맥스페인을 식 그대로(전수)** — 일치

| 스냅샷 | 시리즈 | 후보 | 다시 잰 맥스페인 | 동률 | engine | 일치 |
|---|---|---|---|---|---|---|
| 14:27 | M:202610 | 13 | 1100.00 | 1 | 1100.00 · ok | 예 |
| 14:27 | M:202611 | 3 | 1347.50 | 1 | 1347.50 · ok | 예 |
| 14:27 | WKM:260904 | 7 | 1097.50 | 1 | 1097.50 · ok | 예 |
| 14:27 | WKM:261001 | 2 | 1092.50 | 1 | 1092.50 · ok | 예 |
| 14:52 | M:202610 | 13 | 1100.00 | 1 | 1100.00 · ok | 예 |
| 14:52 | M:202611 | 3 | 1347.50 | 1 | 1347.50 · ok | 예 |
| 14:52 | WKM:260904 | 7 | 1100.00 | 1 | 1100.00 · ok | 예 |
| 14:52 | WKM:261001 | 2 | 1090.00 | 2 | 1090.00 · estimated (tie_without_forward) | 예 |

후보 = 체인 행이 온 행사가(스냅샷엔 마스터 상장 행사가가 없다 — engine 도 같은 입력), 동률은 그 만기 F 에 가까운 쪽·낮은 쪽. 합은 정수·분수로 정확히

**맥스페인 — KRX 일별 fixture 독립 재계산** — 일치

| 거래일 | 만기 | 후보 | 손계산 맥스페인 | core | 일치 |
|---|---|---|---|---|---|
| 20100104 | 201001 | 1 | 185.00 | 185.00 · ok | 예 |
| 20100104 | 201003 | 1 | 240.00 | 240.00 · ok | 예 |
| 20260923 | 202610 | 2 | 1100.00 | 1100.00 · ok | 예 |
| 20260923 | 202812 | 1 | 1000.00 | 1000.00 · ok | 예 |

남은 [확인 필요] (§6.6 — 2):
- …거리, 같으면 낮은 행사가). 기본값 [확인 필요]: 후보는 넘긴 상장 행사가(마스터 전 행사가 — 없으면 OI 가 온 행사가, OI 행이 없는 상장 행사가도 후보), F 를 모르…
- …동률의 F 는 그 만기 F. 기본값 [확인 필요]: 상장 행사가 중 체인 행이 한 번도 오지 않은 행사가가 있으면(세션 첫머리 — 보강 2 순환 전) 그 OI 를 몰라 estim…

### oi_changes — §6.7 OI 증감 히트맵

- 플래그: 기본 `shadow`, 지금 `shadow` · 산출 `oi_changes` (표 `oi_changes`)
- 명세 테스트: 22 통과 — `tests/property/test_flow_properties.py`, `tests/unit/test_engine_flow.py`, `tests/unit/test_flow.py`

**OI 증감 = 두 스냅샷 OI 차** — 일치

14:27 → 14:52: engine 칸 35(0 이 아닌 증감), 체인 행으로 센 바뀐 종목 35, 증감이 OI 차와 다른 칸 0, 이상치 0

| 시리즈 | 칸 | Σ증가 | Σ감소 | 가장 큰 증감 |
|---|---|---|---|---|
| M:202610 | 23 | +1,628 | -3,480 | 1595.00 C -2,459 |
| M:202611 | 2 | +0 | -31 | 1350.00 C -28 |
| WKM:260904 | 10 | +3,183 | -4,644 | 1097.50 C -1,616 |

남은 [확인 필요] (§6.7 — 3):
- …한 스냅샷에서 줄었다가 다음 스냅샷에 줄어든 양의 90% 이상 복구되면 두 칸 모두 이상치로 격리(표시 제외, health) [확인 필요: 90%]
- …l 0.9 — 정확한 비교). 기본값 [확인 필요]: 90% 는 경계 포함(넘친 복구도 이상치), 증감 0 인 스냅샷도 '다음 스냅샷'(복구 아님으로 확정), 첫 스냅샷은 증감 없…
- …직전보다 늦다)이면 한 칸. 기본값 [확인 필요]: 행 ts = 스냅샷 시각(체인 행 수신 시각 — 사이클 as_of 가 아니다: 히트맵의 시각 축이고, 이상치로 앞 칸을 고칠…

### futures — §7 선물 베이시스·괴리율·OI 증감·체결강도

- 플래그: 기본 `shadow`, 지금 `shadow` · 산출 `futures_basis`, `futures_theory_basis`, `futures_divergence`, `futures_oi_change`, `futures_strength`
- 명세 테스트: 24 통과 — `tests/property/test_futures_properties.py`, `tests/unit/test_engine_futures.py`, `tests/unit/test_futures_metrics.py`

스냅샷 값: 없음 — scheduler 가 세션 뒤 녹화한 분봉 조회 원문 — 아래 fixture·probe 응답

**선물 KIS basis 대 자체 베이시스** — 일치

| 응답 | 종목 | 선물가 | 지수 | 이론가 | KIS basis | 선물가 − 지수 | 이론가 − 지수 | KIS − 시장 | KIS − 이론 | core 교차검증(≤ 0.05) |
|---|---|---|---|---|---|---|---|---|---|---|
| tests/fixtures/kis/minute_day.json | A01612 | 1098.05 | 1097.62 | 1103.47 | 5.85 | 0.43 | 5.85 | +5.42 | +0.00 | 통과 |

응답 1건 중 KIS basis ≈ 이론 베이시스(±0.01) 1건, ≈ 시장 베이시스(±0.05) 0건 — 그래서 §7 교차검증은 이론 베이시스와 하고 시장 베이시스는 자체 계산으로 표시한다(2026-09-30, metrics §7 [확인 필요])

남은 [확인 필요] (§7 — 6):
- …sis 차이가 0.05pt 를 넘으면 health — KIS 필드 뜻이 바뀌었거나 한 응답 안의 값이 서로 맞지 않는다는 신호 [확인 필요: 0.05pt]
- 비교 대상을 이론 베이시스로 바꾼 것도 [확인 필요]
- …. 자체 이론 베이시스는 금리·배당 추정이 필요해 계산하지 않는다(PLAN §2.4 "배당·금리 추정 불필요" 와 같은 입장) [확인 필요]
- …— 0.05 정확히는 통과). 기본값 [확인 필요]: 빈 필드는 그 값만 null·invalid(field_missing), 지수·선물가·이론가가 0 이하면 없는 것으로(야간 응답…
- …(종목마다 10분에 한 번). 기본값 [확인 필요]: 주기 60초, 기동하면 하루 앞 응답부터(같은 키라 다시 써도 행이 늘지 않는다), 태그가 없거나 검증에 실패한 응답은 그것만…
- …행은 세션마다 그 조회 수만큼(주간 최대 5·야간 8, 같은 마감 값) — 장중 선물 필드 수집은 PLAN §4.4 에 없다 [확인 필요]

## 기능 플래그 (§8)

- 명세 테스트(로더·섀도 점검): 84 통과
- 지금 플래그: `net_gex` visible, `dex` visible, `atm_iv` visible, `expiry_gamma` visible, `call_wall` visible, `put_wall` visible, `abs_gamma` visible, `flip` visible, `flip_distance` visible, `expected_move` visible, `top_levels` visible, `vex` shadow, `cex` shadow, `gex_pc` shadow, `iv_term` shadow, `skew_25d` shadow, `atm_iv_daily` shadow, `iv_rank` shadow, `iv_percentile` shadow, `iv_hv` shadow, `hiro` shadow, `investor_flow` shadow, `dealer_check` shadow, `block_trades` shadow, `pcr` shadow, `max_pain` shadow, `oi_changes` shadow, `futures` shadow
- 남은 [확인 필요] (2):
  - …다, visible 은 발행. 기본값 [확인 필요]: Phase 2 핵심은 off 로 둘 수 없다(shadow 까지 — 사이클 품질·레벨 사이 의존의 바탕), 다시 읽다 틀리면 직…
  - …invalid 인 null. 기본값 [확인 필요]: 플래그 무오류 = 행이 있고 예외 0·명세 밖 null 0, 완료 기준 충족 = 고른 플래그(기본 shadow — 행 플래그와…
