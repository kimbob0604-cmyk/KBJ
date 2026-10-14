# legacy/gexlab — P1 이식 기록

> **P9 WRAP(2026-10-14) 에 `legacy/gexlab` 을 레포에서 지웠다**(ADR 0038). 이 파일은 옛 `legacy/gexlab/MIGRATION.md` 를
> `git mv` 한 것이다 — 아래 경로는 지우기 전 기준이다. 지우기 직전 상태는 **커밋 `f6aeebd`**(legacy 를 지우기 직전 — 태그
> `legacy-final` 은 사용자가 GitHub 에서 달 수 있다), 원본은 GEXLAB 레포. 마지막 절 'P9 WRAP' 이 지울 때의 시험 수·사유다.

KBJ P1(`docs/PLAN.md` §8, `docs/conflict_map.md` §3.1·§3.5 2단계)에서 GEXLAB 전체를 옮긴 기록이다. 원칙은
**경로·import 만 고친다**이고, 예외는 ADR 0001 U3 의 합성 교체와 개인정보·운영정보 제거뿐이다. 바꾼 파일은 전부
아래 3장에 파일별로 적었다. 적지 않은 파일은 원본과 바이트까지 같다.

- 원본: GEXLAB 스냅샷 `43a9ed1`(2026-09-30 커밋, P0 작업 환경의 읽기 전용 사본 — 레포 밖)
- 이식 위치: `legacy/gexlab/` (원래 레포 루트 = 이 폴더. `pythonpath = ["."]` 그대로)
- `pyproject.toml`·`uv.lock` 은 원본 그대로 둔다. 통합 단계에서 루트 lock 의 `legacy` 의존성 그룹으로 합쳤다 — **KBJ 에서는 루트 `uv.lock` 이 정본**이다(6장)
- 작업일: 2026-10-06~07

## 0. 시험 결과

명령은 원본 CI 와 같다. 작업 디렉터리만 `legacy/gexlab` 이다.

```
cd legacy/gexlab
uv sync --frozen --python 3.12
uv run pytest -m "not network"
```

| 어디서 | 환경 | 결과 |
|---|---|---|
| 원본 사본(레포 밖 임시 사본, 원본 fixture) | 이 폴더 `uv.lock` 그대로(pandas 3.0.6) | **3,227 수집 / 3,178 통과 / 49 건너뜀**, 실패 0 |
| 이식 뒤 `legacy/gexlab`(합성 fixture) | 같은 lock(pandas 3.0.6) | **3,178 통과 / 49 건너뜀**, 실패 0 |
| 이식 뒤 `legacy/gexlab`(합성 fixture) | P0 통합 해석 환경(pandas 2.3.3, conflict_map §3.3) | **3,178 통과 / 49 건너뜀**, 실패 0(경고 9 — exchange_calendars 의 numpy 폐기 예고, 시험과 무관) |
| 원본 사본, `tests/integration` 만, Docker 데몬 켬 | 이 폴더 lock | 49 통과 / 1 건너뜀 |
| 이식 뒤 `legacy/gexlab`, `tests/integration` 만, Docker 데몬 켬 | 이 폴더 lock | **49 통과 / 1 건너뜀** — 원본과 같다(4장) |

- 건너뛴 49개는 원본과 같은 시험이다: Docker 데몬이 없을 때 `tests/conftest.py` 가 건너뛰는 통합 시험
  (`tests/integration/*` — compose 시험 스택의 TimescaleDB·Redis 필요). 사유 문구도 원본과 같다('Docker 없음 —
  docker 데몬에 붙지 못했다').
- 네트워크 없이 돈다(`-m "not network"`). `.env` 가 없어도 된다 — `.env.example`(빈 자리표시자만)은 시험 2개
  (`test_repo_rules`·`test_compose_file`)가 읽어서 옮겼다.
- 시험이 끝나면 `.hypothesis/`(hypothesis 예제 DB)가 생긴다. 원본에서도 같고 `.gitignore` 가 막는다.
- 원본 CI 의 나머지 검사도 이 폴더에서 원본과 같다: `uv run ruff check .`·`uv run ruff format --check .` 통과,
  `uv run pyright` 0 errors(원본 사본도 0). KBJ 루트의 ruff·pyright 는 `legacy/` 를 보지 않는다(ADR 0003) — 이것은 원래
  프로젝트 규칙으로 따로 확인한 것이다.

## 1. 옮기지 않은 것

| 원본 경로 | 이유 |
|---|---|
| `state/`·`probe_out/`·`.venv/` | 스냅샷에 없었다(원본에서도 gitignore). 실행 산출물·토큰 캐시·실측 원본 자리라 KBJ `.gitignore` 의 `legacy/**/state/`·`legacy/**/probe_out/` 가 막는다 |
| `.env` | 스냅샷에 없었다. 있었어도 열지도 옮기지도 않는다 |
| `.github/workflows/ci.yml`·`probe.yml` | 공개 레포에서 실행되면 안 된다(ADR 0003 2장 6번). 시험이 읽지 않아 legacy 안에도 두지 않았다. `probe.yml` 은 실 API 를 부르는 probe 워크플로다 |
| `.claude/`(`settings.json`·`hooks/post_edit.sh`) | 원래 작업 환경의 에이전트 설정이다. 코드·시험이 읽지 않는다 |
| `tests/fixtures/validation/chain_snapshot_20260928_{1427,1452}_small.json` | KIS 실측 체인 발췌(로그인 등급) → 합성 파일로 바꾸고 이름을 바꿨다(2장) |
| `tests/golden/core/chain_20260928_small.json` | 위 실측 체인의 코어 파이프라인 출력 → 합성 입력으로 다시 만들고 이름을 바꿨다(2장) |

## 2. 합성 교체 (U3 — conflict_map §2 의 GEXLAB 15개)

### 2.1 생성기 — `scripts/make_synthetic_fixtures.py` (새 파일)

KIS·KRX 실측 발췌 fixture 를 **실측을 읽지 않고** 시드 고정 난수(`SEED = "kbj-p1-gexlab-synthetic-v1"`, fixture
마다 따로 시드)와 Black-76 모형만으로 다시 만든다. 키·필드·자료형·행 수·행 순서는 원래 fixture 와 같고 값은 합성이다.
JSON 파일에는 머리 칸 `_source`(원래도 있던 칸)에 `SYNTHETIC — …` 를 적었다. 작은 체인 fixture 두 개는
`chain-subset` 규칙과 바이트까지 같아야 해서(`tests/golden/test_chain_subset.py`) 표시 칸 대신 파일 이름에 `synthetic` 을 넣었다.

```
cd legacy/gexlab
uv run python -m scripts.make_synthetic_fixtures            # 커밋된 fixture 와 비교만(다르면 종료 코드 1)
uv run python -m scripts.make_synthetic_fixtures --write    # 다시 쓴다
uv run python -m scripts.make_synthetic_fixtures --explain  # 시험 기대값 독립 계산
```

- 합성 체인: 월물 202610·202611(행사가 745.0~1595.0 341개), 위클리 WKM 260904(0DTE)·WKM 261001. 행 가격은
  Black-76(스마일 포함), KIS 칸(`gama`·`delta_val`·`hts_ints_vltl`·`hist_vltl` …)은 `docs/validation_greeks.md`·
  `docs/metrics.md` §1.7 에서 GEXLAB 이 실측으로 확인한 KIS 관례(월물 Δ·Γ = Black(σ = hist_vltl, S = KIS 이론가 패리티
  선도, T = 달력일/365), KIS IV = Black 역산(T = max(달력일, 0.5)/365), 위클리 무거래 행은 전 세션 수준·기초 HV,
  0DTE 는 0.5일)를 모형으로 써서 만든다. 그래서 KIS 관례 재현 검사 시험이 합성 데이터에서도 같은 판정을 낸다.
- 시험이 확인하는 행 성질(전 세션 가격 행, 내재가치 밑 last 로 KIS IV 폴백이 되는 1102.5 풋, KIS IV 0 행, 전일 종가
  없는 행, 표기 없는 코스닥 위클리 중복 행, 거래 없는 깊은 ITM 행 등)은 원래 fixture 와 같은 자리에 일부러 둔다.
- 남긴 시나리오 뼈대(손으로 쓴 시험·가짜 서버가 입력으로 쓰는 값): 날짜·조회 시각, 상품·만기·행사가 격자, 근월물
  선물가(14:27 `1095.10`·14:52 `1092.50`, 호가 1095.00/1095.10), 원월물 1085.00, 전광판 ATM 표시 1125.0,
  월물리스트(상장 규칙). 이 값들은 원본 시험 코드(`test_kis_ws`·`test_engine_service`·`test_full_day` 등 수십 곳)에
  이미 손으로 적혀 있는 시나리오 입력이라 바꾸면 '경로·import 만' 원칙을 깨게 된다. 행 단위 값(가격·호가·거래량·
  OI·IV·그릭스·투자자 수급·KRX 일별)과 수준(합성 F·KIS 이론 선도·현물·전 세션 수준·HV)은 모두 생성기 매개변수에서 나온다.
- 종목코드: 옵션 단축코드의 행사가 자리 3글자를 합성 규칙(`Z` + 행사가/2.5 의 36진 두 자리, 예 `B01610ZCI`)으로,
  표준코드(ISIN)는 합성 본문 + ISIN 검사 숫자로 만든다. 선물·스프레드 코드(A01612 등)는 상품·결제월 규칙 그대로다.
- `--explain` 은 시험이 값으로 확인하는 기대값을 생성기 안의 독립 계산(math 만 쓰는 Black-76·역산·중앙값·패리티 —
  `core`·`scripts` 의 계산 코드를 부르지 않는다)으로 찍는다. 3.2 의 바뀐 기대값은 손으로 맞춘 것이 아니라 여기서
  나온 값이거나 합성 fixture 의 해당 칸을 그대로 읽은 값이다.

### 2.2 파일별

| 파일 | 원래 | 지금 | 쓰는 시험 |
|---|---|---|---|
| `tests/fixtures/kis/callput_202610.json` | KIS 실측 전광판 콜·풋(2026-09 probe 발췌, 앞 10행 + 뒤 10행) | 합성 14:27 스냅샷 월물 202610 전광판의 같은 자리 | `test_kis_master`·`test_kis_models`·`fakes/kis_server.py` 경유 다수 |
| `tests/fixtures/kis/callput_wkm_260904.json` | KIS 실측(위클리 WKM 260904) | 합성 스냅샷의 같은 자리(인덱스 46~65) | `golden/synthetic.py`·`test_kis_master`·`test_kis_models`·`fakes/kis_server.py` |
| `tests/fixtures/kis/futures_board.json` | KIS 실측 선물 전광판 | 합성(선물 앞 2행, 근월물 가격은 뼈대) | `golden/synthetic.py`·`test_kis_models`·`fakes/kis_server.py` |
| `tests/fixtures/kis/investor.json` | KIS 실측 투자자별(7조합) | 합성(투자자 12종, 기관계 = 일곱의 합 규칙 유지) | `golden/synthetic.py`·`test_kis_models`·`fakes/kis_server.py`·`scripts/metric_report.py` |
| `tests/fixtures/kis/master_lines.json` | KIS 지수선물옵션 마스터 zip 발췌 110줄 | 합성 110줄(마스터 줄 형식 `data/kis/master.py` 그대로, 코드·ISIN 합성 규칙, ATM 구분은 합성 전 세션 수준 기준) | `integration/test_store`·`test_store_sql`·`test_kis_master` |
| `tests/fixtures/kis/minute_day.json` | KIS 실측 분봉(14:01 조회) | 합성 분봉(output1·output2 같은 칸, KIS basis = 이론 베이시스 관례) | `golden/synthetic.py`·`integration/test_store`·`test_store_sql`·`test_kis_models`·`test_futures_metrics`·`test_engine_futures`·`fakes/kis_server.py` |
| `tests/fixtures/kis/option_list.json` | KIS 실측 월물리스트 | 상장 규칙으로 만든 목록(같은 행 수) | `golden/synthetic.py`·`test_kis_models`·`fakes/kis_server.py` |
| `tests/fixtures/kis/price_options.json` | KIS 실측 옵션 단건 현재가 6종목 | 합성 스냅샷 단건 6종목 | `golden/synthetic.py`·`test_kis_models`·`fakes/kis_server.py` |
| `tests/fixtures/krx/fut_daily.json` | KRX OpenAPI 발췌(미니 선물 3행) | 합성 3행 | `integration/test_store`·`test_store_sql`·`test_krx_models`·`fakes/krx_server.py` |
| `tests/fixtures/krx/opt_daily.json` | KRX OpenAPI 발췌(이름 형식마다 한두 행) | 합성(같은 이름 형식·행 수, 2026-09-23·2010-01-04) | `integration/test_engine_store`·`test_store`·`test_krx_client`·`test_store_sql`·`test_krx_models`·`fakes/krx_server.py` |
| `tests/fixtures/validation/chain_snapshot_20260928_1427_small.json` → **`chain_snapshot_synthetic_20260928_1427_small.json`** | KIS 실측 체인 스냅(2026-09-28 14:27) 발췌 | 합성 전체 스냅샷을 프로젝트 규칙 `scripts.make_golden.chain_subset` + `dump_chain` 으로 자른 것 | `golden/test_chain_subset`·`test_metric_report`·`test_validate_greeks`, `make_golden.CORE_FIXTURES` 경유 `golden/test_core_golden`·`test_engine_extended`·`test_engine_evaluate`·`test_shadow_report`·`integration/test_shadow_report` |
| `tests/fixtures/validation/chain_snapshot_20260928_1452_small.json` → **`chain_snapshot_synthetic_20260928_1452_small.json`** | 같음(14:52) | 같음 | 같음 |
| `tests/golden/core/chain_20260928_small.json` → **`chain_synthetic_20260928_small.json`** | 위 실측 체인의 코어 파이프라인 출력 | 합성 입력으로 프로젝트 골든 생성기를 다시 돌린 출력: `uv run python -m scripts.make_golden core --write` | `golden/test_core_golden`·`test_fixtures_clean` |
| `tests/golden/raw/synthetic_20260928.jsonl`·`.parsed.json` | 합성 녹화(REST 본문 몇 행은 실측 fixture 에서 옮김) | 같은 합성 녹화, REST 본문은 위 합성 fixture 에서 옮김. 다시 만든 명령은 `tests/golden/synthetic.py` 머리말 그대로(`python -m tests.golden.synthetic <x.jsonl>` → `make_golden raw --from-jsonl … --name synthetic_20260928 --write`) | `golden/test_raw_golden`·`integration/test_make_golden_db`·`test_fixtures_clean` |

파일 이름의 `20260928` 은 시나리오 날짜(시험 코드·캘린더 계산이 쓰는 날짜)라 남기고, 앞에 `synthetic_` 을 붙여 합성임을
드러냈다. `raw/synthetic_20260928.*` 은 원래부터 합성 이름이라 그대로다.

재현 확인(이식 뒤 `legacy/gexlab` 에서): `make_synthetic_fixtures`(비교) 12개 모두 '같다', `make_golden core`(비교) '같다(허용오차 안)',
`make_golden raw … --name synthetic_20260928`(비교) jsonl '같다'·parsed '같다(허용오차 안)'.

### 2.3 `config/kis_ws_fields.yaml`·`config/kis_ws.yaml` — 유지(판단)

conflict_map §2 는 '제3자 코드: KIS 공식 샘플 `koreainvestment/open-trading-api` 의 필드 순서, 라이선스 확인 필요'로
적었다. 판단: 이 두 파일은 샘플 **코드**를 옮긴 것이 아니라 KIS Open API 의 실시간 TR 응답 필드 이름·순서, 웹소켓
접속 주소·구독 메시지 형식 같은 **인터페이스 사실**이다(API 를 쓰려면 누구나 같은 순서를 써야 한다). 그래서 내용은 그대로
두고 머리 주석의 출처를 'KIS Open API 문서'로 바꿨다. 원래 대조에 쓴 샘플 저장소 커밋·URL(`source`·`url` 칸)은 남겼다 —
시험(`test_kis_ws`·`test_ws_client`)이 그 칸을 읽고, 어디서 확인했는지의 기록이기도 하다. 라이선스 최종 확인은 메인 판단에
맡긴다(아래 5장).

## 3. 바꾼 파일 (무엇을·왜·시험 영향)

### 3.1 시험 — 합성 교체로 기대값이 바뀐 것

모두 **값만** 바꿨다. assert 개수·비교 연산·허용오차는 원본과 같다(약하게 한 것 없음, skip·xfail 없음). 새 값의 출처는
합성 fixture 칸(파싱 시험) 또는 `--explain` 독립 계산(계산 시험)이다.

| 파일 | 함수 | 바뀐 것 |
|---|---|---|
| `tests/unit/test_kis_models.py` | `test_monthly_board_parses_top_and_bottom_rows`, `test_futures_board`, `test_option_single_price`, `test_investor_net_qty_naming_irregularity`, `test_day_minute_bars` | 첫 행 코드·OI·Γ·Δ·IV·거래대금, 선물 OI·거래량, 단건 가격, 투자자 순매수, 분봉 OHLC·거래량 — 합성 fixture 값. 모듈 docstring |
| `tests/unit/test_kis_master.py` | `test_futures_line`, `test_monthly_series_and_padded_strike`, `test_monthly_atm_class_line_is_a_call_not_dropped`, `test_series_for_market_class` | 합성 종목코드(`B01610ZCI` 등)·표준코드 `KR4A16120003`. 모듈 docstring |
| `tests/unit/test_krx_models.py` | `test_kospi200_monthly_day_row`, `test_night_row_iv_placeholder_is_none`, `test_untraded_padded_strike_row`, `test_kosdaq_weekly_duplicate_rows_without_session`, `test_2010_rows`, `test_futures_rows_session_from_mkt_nm` | KRX 행 IV·종가·거래량·OI·코드·정산가 — 합성 fixture 값. 모듈 docstring |
| `tests/unit/test_store_sql.py` | `test_day_minute_bar_from_fixture`, `test_krx_option_rows_keep_kospi200_family_and_null_night_iv`, `test_krx_futures_rows_null_night_settlement`, `test_master_rows_from_fixture` | 분봉 OHLC·거래량, KRX 코드·IV·거래대금, 정산가, 마스터 ATM 코드 |
| `tests/unit/test_poller_fake_kis.py` | `test_single_price_by_code_and_market` | 단건 가격 |
| `tests/unit/test_futures_metrics.py` | `test_measured_response_kis_basis_is_the_theoretical_basis` | KIS basis 5.85·시장 베이시스 0.43·이론가(합성 분봉 output1). docstring |
| `tests/unit/test_engine_futures.py` | `test_the_measured_output1_is_validated_into_the_core_quote`, `test_five_rows_carry_the_kis_values_and_the_cross_check`, `test_the_cross_check_health`, `test_the_service_turns_new_recorded_responses_into_rows_every_minute` | 같은 분봉 output1 값과 교차검증 경계(이론 + 0.05 = 5.90), 어긋난 basis 0.43·차 5.42 |
| `tests/unit/test_metric_report.py` | `test_krx_rows_are_parsed_independently`, `test_krx_fixture_pcr_and_max_pain_hand_values`, `test_krx_daily_atm_iv_rank_and_hv_hand_values`, `test_futures_basis_hand_values_and_probe_outputs`, `test_investor_fixture_consistency_and_dealer_check`, `test_report_on_the_committed_small_snapshots` | PCR 1,824/2,656 = 0.6867·거래량 비, 202812 OI, ATM F 1119.40·IV 37.50%, 베이시스 행, 딜러 점검 합 −12,842/−790(`--explain` 손 계산 칸), 작은 스냅샷 파일 이름 |
| `tests/unit/test_validate_greeks.py` | `test_series_rows_merges_board_and_fills`, `test_quotes_carry_volume_and_skip_prev_session_in_forward`, `test_0dte_kis_iv_fallback_gamma_is_rescaled`, `test_kis_monthly_gamma_is_black_with_hist_vol`(docstring), `test_fit_delta_surface_on_kis_monthly`, `test_kis_weekly_iv_uses_stale_underlying`, `test_solve_delta_gamma_shows_stale_weekly_underlying`, `test_analyze_and_report_smoke`, `test_report_isolates_series_failures`, `test_board_spot_from_intrinsic_value`, `test_snapshot_hist`, `test_monthly_reproduction_passes`, `test_monthly_202611_needs_kis_forward_not_futures`, `test_0dte_reproduction_uses_half_day`, `test_weekly_untraded_rows_use_hv_and_previous_level`, `test_evaluate_passes_forward_basis`, `test_summarize_carries_confirmed_basis`, `test_fallback_counts_rescaled_and_dropped`, `test_fallback_gex_vs_kis_compares_like_rows`, `test_main_summary_smoke` | 보강 행 칸, 합성 F 1089.55, 폴백 행(IV 11.9151·Γ 0.0575·Δ −0.8599), 패리티 선도 1095.16, 재현 판정 칸 수(전체 Δ 26/26·Γ 22/26, 반올림 원인 4), 편향 +100.7, 현물 S, HV 81.05, 전 세션 수준 1124.2 등 — 전부 `--explain` 값. 모듈 docstring(fixture 출처 설명) |
| `tests/golden/test_chain_subset.py` | `test_both_small_fixtures_exist` | 파일 이름 두 개 |
| `tests/unit/test_fixtures_clean.py` | `test_golden_data_exists` | 코어 골든 파일 이름 |
| `tests/golden/synthetic.py` | `synthetic_export` | REST 본문의 지수 수준 1094.47(합성). 머리말·docstring 의 '실측 fixture' → '합성 fixture' |
| `tests/integration/test_store.py` | `test_krx_daily_night_iv_stays_null_and_reload_updates`, `test_master_snapshots_per_session` | KRX 코드·IV·정산가, 마스터 코드 (Docker 시험 — 4장) |
| `tests/integration/test_engine_store.py` | `test_daily_metric_inputs_read_what_the_engine_and_the_krx_loader_wrote` | KRX 일별 행 세 개(종가·IV·거래량) (Docker 시험 — 4장) |
| `tests/integration/test_scheduler_krx.py` | `test_loading_twice_and_after_a_restart_writes_each_row_once` | 선물 정산가 1120.35 (Docker 시험 — 4장) |

의미가 달라진 곳 하나: `test_quotes_carry_volume_and_skip_prev_session_in_forward` 는 원래 '전 세션 가격 행 1097.5 를
빼면 합성 F 가 1089.95 → 1089.30 으로 바뀐다'를 주석으로 보였다. 합성 스냅샷에서는 나머지 행사가 패리티가 같아 빼기 전·뒤
F 가 모두 1089.55 다. 시험이 확인하는 것(빠진 행사가 `(1097.50,)`, 쓴 행사가 네 개, F 값, `prev_session_last` 사유)은 그대로다.

### 3.2 시험 — 문서·주석만 (동작 영향 없음)

'실측 작은 스냅샷'·'probe 실측 발췌' 처럼 fixture 를 실측이라고 설명한 docstring·주석을 '합성'으로 고쳤다:
`tests/fakes/kis_server.py`(머리말), `tests/fakes/engine_inputs.py`(머리말·절 제목), `tests/golden/test_core_golden.py`(머리말),
`tests/unit/test_engine_evaluate.py`(머리말), `tests/unit/test_engine_extended.py`(머리말·`test_measured_snapshot_gives_sane_extended_metrics`
docstring), `tests/unit/test_shadow_report.py`(머리말·함수 docstring), `tests/integration/test_shadow_report.py`(머리말).
함수 이름(`test_measured_…`)은 바꾸지 않았다. 고친 docstring·주석이 100자를 넘은 곳은 줄만 나눴다(ruff E501).
`test_metric_report.test_report_on_the_committed_small_snapshots` 의 파일 이름 assert 하나는 이름이 길어져 괄호로 두 줄에
나눴다(같은 `and` 조건).

### 3.3 코드·설정

| 파일 | 무엇을 | 왜 | 시험 영향 |
|---|---|---|---|
| `scripts/make_synthetic_fixtures.py` | 새 파일(2.1) | U3 합성 데이터의 결정론적 생성기 | 시험이 import 하지 않는다 |
| `scripts/make_golden.py` | `CORE_FIXTURES`·`CORE_GOLDEN` 경로를 새 파일 이름으로, 머리말·`Derived` docstring(합성 설명, 줄 나눔) | 파일 이름 변경(2.2) | `test_core_golden`·`test_engine_*`·`test_metric_report` 가 이 경로로 읽는다 |
| `scripts/probe_common.py` | 주석에서 실측한 다른 프로젝트 이름을 뺐다('(2026 개편 후 실측)') | 다른 프로젝트 이름 제거 | 없음 |
| `scripts/probe_rest_limit.py` | docstring 에서 같은 앱키를 쓰는 다른 프로젝트 이름을 뺐다 | 같음 | 없음 |
| `config/kis_ws_fields.yaml`·`config/kis_ws.yaml` | 머리 주석(출처) | 2.3 | 없음(`source`·`url` 칸은 그대로) |

### 3.4 문서

| 파일 | 무엇을 | 왜 |
|---|---|---|
| `README.md` | '(비공개, 자기 투자 목적)' → KBJ 로 옮긴 legacy 원본이라는 설명 + 이 파일 링크 | 공개 레포 |
| `docs/runbook.md` | crontab 예시의 맥 홈 아래 체크아웃 절대경로(사용자 이름 포함)·Homebrew uv 절대경로 → `<GEXLAB 체크아웃 경로>`·`<uv 절대경로>` | 사용자 이름이 든 로컬 경로 |
| `docs/probe_results.md` | 동시 작업 줄의 다른 개인 프로젝트 이름·일정(수집 잡 2개의 이름과 시각) → '같은 앱키를 쓰는 다른 수집 작업' | 다른 개인 프로젝트 이름·일정 |
| `docs/phase1_design.md` | 같은 이유로 다른 프로젝트 이름·launchd 일정 제거(2곳), 코어 골든·녹화 골든 설명을 합성 fixture 로(2곳) | 개인 정보 + 파일 이름 변경 |
| `docs/metrics.md` | 머리 골든 문단의 파일 이름, §1.7 테스트 줄의 '실측 fixture' | 파일 이름 변경 |
| `docs/metric_validation.md` | 자동 생성 리포트를 합성 fixture 로 다시 만들었다(`uv run python -m scripts.metric_report --write`, probe_out 없음 → git 의 작은 발췌 사용). 원래는 로컬 실측 전체 스냅샷과 probe_out 분봉으로 만든 값(지표·레벨 수치)이었다 | 로그인 등급 원천으로 계산한 산출물. 파일 머리가 '자동 생성 — 손으로 고치지 않는다'라 생성기로 다시 만들었다 |

남긴 문서(판단 — 5장): `docs/validation_greeks.md`·`docs/metrics.md` §1~§3 의 '실측(2026-09-28 두 스냅샷 …)' 문단,
`docs/probe_results.md` 의 probe 결과 표. GEXLAB 이 KIS 관례를 알아낸 분석 기록(통과 행 수·오차 통계·몇몇 수준 값)이고
원본 응답이나 fixture 가 아니다. 합성 생성기가 이 결론을 모형으로 쓴다.

## 4. Docker 통합 시험(49개)

기준 명령(0장)은 Docker 데몬이 없는 조건이라 원본·이식 뒤 모두 49개가 건너뛰어진다. 3.1 표의 통합 시험 3개
(`test_store`·`test_engine_store`·`test_scheduler_krx`)는 기대값을 합성 fixture 값으로 바꿨으므로(새 값은 fixture 칸을 그대로
읽은 것 — 예: `B016AZC8` 정규 IV 35.20, `301E3Z2O` IV 20.80, `A056A000` 정규 정산가 1120.35, 마스터 ATM `B01610ZCI`) 따로
Docker 데몬을 켜고(`redis:7-alpine`·`timescale/timescaledb:latest-pg16`) `pytest tests/integration -m "not network"` 를
원본 사본과 이식 뒤에서 각각 돌렸다: 둘 다 **49 통과 / 1 건너뜀**. 건너뛴 1개는 둘 다 `test_compose.py:149` '앱 이미지를
만들지 못했다' — 이 작업 환경의 컨테이너 빌드가 네트워크(패키지·베이스 이미지 받기)에 막힌 것으로, fixture 와 무관하다.
즉 Docker 가 있는 CI 에서는 원본과 같이 49개가 더 돈다.

## 5. 메인 확인 필요

- `config/kis_ws*.yaml` 을 인터페이스 사실로 보고 유지한 판단(2.3)
- 분석 문서(`docs/validation_greeks.md` 등)의 실측 기반 통계·수준 값을 남긴 판단(3.4 끝)
- 합성 생성기의 시나리오 뼈대 값(근월물 선물가 1095.10/1092.50 등, 2.1)이 원래 실측 시각의 선물가와 같다 — 원본 시험 코드에
  손으로 적힌 입력이라 남겼다
- `tests/unit/test_metric_report.py`(`test_report_on_the_committed_small_snapshots` 끝 assert)에 맥 홈 접두사 문자열 하나가
  있다 — 리포트에 홈 경로가 **없음**을 확인하는 원본 assert 라 개인 경로가 아니다(사용자 이름 없음, KBJ
  `scripts/check_public_safety.py` 의 `user_home_path` 규칙에도 걸리지 않는다). 시험이라 고치지 않았다

## 6. KBJ 통합 실행 (P1 통합 단계, 2026-10-07)

**정본 lock**: 레포 루트 `pyproject.toml` 의 `legacy` 의존성 그룹과 루트 `uv.lock` 이 정본이다. 이 폴더의
`pyproject.toml`·`uv.lock` 은 원본 기록으로 남긴 것이고 KBJ 의 시험·CI 는 쓰지 않는다. 의존성을 바꿀 때는 루트만 고치고
`uv lock` 한다. (P1 때는 통합 시험 `tests/integration/test_compose.py` 의 앱 이미지가 이 폴더 `uv.lock` 을 읽어서 남겨
뒀다 — P2 에서 지웠다. 아래 '6.1 P2 — 이미지 빌드' 참고.)

### 6.1 P2 — 이미지 빌드를 레포 루트 맥락으로 (2026-10-07, 메인)

P2 재배선 뒤 legacy GX 는 kbj 정본을 다시 내보낸다(`config/settings.py` → `kbj.data.private.kis`,
`core/calendar.py` → `kbj.core.calendar`, `data/spool.py` → `kbj.store.spool` 등). 그런데 이 폴더만 맥락으로 만든 이미지에는
kbj 패키지가 없어 `migrate` 컨테이너가 import 에서 종료 1 로 끝났다(GitHub CI `gexlab-integration` 의
`test_stack_records_raw_messages_and_survives_a_db_outage` 실패, 커밋 17698d5). 고친 것:

| 파일 | 무엇을 | 시험 영향 |
|---|---|---|
| `Dockerfile` | 맥락 = 레포 루트. 루트 `pyproject.toml`·`uv.lock`(legacy 그룹)·`kbj/`·`config/` 를 `/opt/kbj` 에 편집 가능 설치하고 GX 코드는 `/app` 에. `KBJ_CONFIG_DIR=/opt/kbj/config` | 없음(이미지 내용) |
| `Dockerfile.dockerignore` (새) | 이 Dockerfile 전용 맥락 필터 — 필요한 것만 넣고 비밀·산출물·다른 legacy·시험은 뺀다(루트 `.dockerignore` 는 legacy 를 통째로 뺀다) | 없음 |
| `docker-compose.yml` | `build: .` 3곳 → `build: {context: ../.., dockerfile: legacy/gexlab/Dockerfile}` | `config -q` 통과 |
| `tests/integration/test_compose.py` | 이미지 빌드 명령의 맥락·`-f` 만 바꿈(경로만, 단언 그대로) | 로컬 Docker 로 2/2 통과 확인 |
| `uv.lock` | **지움** — 쓰는 곳이 없다. lock 은 루트 하나 | 없음 |
| `tests/unit/test_compose_file.py` | compose `build` 기대값을 `"."` → `{context: ../.., dockerfile: legacy/gexlab/Dockerfile}`(같은 엄밀함, 경로만) | 1개 단언의 기대값 |


| 항목 | 원본 lock(이 폴더) | KBJ 루트 lock |
|---|---|---|
| pandas | 3.0.6 | **2.3.3**(pykrx 가 `<3.0` 을 요구 — conflict_map §3.3, E2) |
| 그 밖의 GEXLAB 의존성 | — | 같은 하한으로 `legacy` 그룹에 넣음(exchange-calendars·vollib·websockets·pandas-stubs·pre-commit, httpx·psycopg·pydantic·redis 등은 루트 기본 의존성) |

**실행**: 레포 루트에서 `uv sync --all-groups && uv run bash scripts/test_legacy.sh gexlab gexlab-integration`.
스크립트는 작업 디렉터리를 이 폴더로 두고 `PYTHONPATH` 를 이 폴더 하나로만 잡는다(다른 legacy 의 `db`·`scripts`·`data`
와 섞이지 않게 — conflict_map §3.4). pytest 설정은 이 폴더 `pyproject.toml` 의 것이 잡힌다. 원본의
`pytest -m "not network"` 하나를 CI 에서 둘로 나눴다(시험·assert 변경 없음, 마크 고르기만):

| 부분 | 마크 | 루트 `.venv`(pandas 2.3.3) 결과 |
|---|---|---|
| `gexlab` | `not network and not integration` | **3,177 통과**, 50 제외 |
| `gexlab-integration` | `integration and not network` | Docker 없음: 1 통과(`test_compose_config_is_valid_without_an_env_file` — `docker compose config` 만 씀)·49 건너뜀. Docker 있음(로컬 dockerd): **49 통과·1 건너뜀**(`test_compose.py:149` 앱 이미지 빌드가 이 작업 환경의 네트워크에서 실패 — 4장과 같음) |
| 합계 | `not network` | 3,178 통과 + 통합 49 — 원본 기준(0장)과 같다 |

CI(`.github/workflows/ci.yml`)의 `gexlab-integration` 잡은 원본 GEXLAB CI 처럼 러너의 Docker 를 그대로 쓴다. 시험이 docker
CLI 로 TimescaleDB·Redis 컨테이너를 직접 띄우고 지우므로 job `services` 는 쓰지 않고 이미지만 미리 받는다.
`KBJ_REQUIRE_DOCKER=1` 이면 'Docker 없음' 으로 건너뛴 시험이 있거나 통과가 49개보다 적을 때 실패로 본다.

## P2. KBJ 정본으로 재배선 (묶음 H, 2026-10-07)

설계 `docs/p2_design.md` §1.9·§3.7·§3.8·§7.2, ADR 0004. 원칙: legacy 의 KIS 토큰 **발급**·KIS REST·
KRX 직접 호출을 0 으로(PLAN P2 완료 기준), 승격한 코드는 KBJ 를 다시 내보내는 한 줄로(두 벌 금지).
웹소켓 연결(`services/ws_gateway`, `config/kis_ws.yaml`)은 P7 까지 여기 남는다(메인 결정 D2) — 접속키는
KBJ auth 가 Redis 에 둔 것을 `reader(…, "ws_key")` 로 읽기만 한다.

### P2.1 다시 내보내기(정본 → 이 파일)

| 파일 | 정본 | 비고 |
|---|---|---|
| `core/calendar.py` | `kbj.core.calendar` | 공개 이름 전부. 휴장 덮어쓰기 정본은 레포 루트 `config/holidays_override.yaml`(이 폴더 `config/` 사본은 읽히지 않는다) |
| `services/poller/context.py:session_tag` | `kbj.core.calendar.session_tag` | 함수 본문 삭제, import 로 |
| `services/runtime.py:tagger_for` | `kbj.services.runtime.tagger_for` | 나머지(하트비트·싱크·백오프)는 GX `services.bus` 모델에 묶여 있어 그대로(P7) |
| `data/kis/auth_client.py` | `kbj.data.private.kis.token`·`credentials` | **읽기 쪽만**: `TokenRecord`·`RedisTokenCache`·`CachedTokenProvider`·`token_owner`(같은 소유 해시)·`TokenUnavailable`·`utcnow` 등 |
| `data/kis/ratelimit.py` | `kbj.data.ratelimit` | 같은 Redis 키 `rl:kis:<앱키 해시>` |
| `data/kis/master.py` | `kbj.data.private.kis.master` | `http_master_downloader`·`download_fo_master` 포함 — 배포 주소는 KBJ 모듈에만 |
| `data/kis/rest.py` | `kbj.data.private.kis.rest.KisRestClient` | `KisClient(settings, …)` 는 같은 호출 모양의 생성 함수. **provider 를 안 주면 토큰 없는 제공자**(`NoTokenProvider` → `TokenUnavailable`, 발급 없음 — K7) |
| `data/krx/eod.py` | `kbj.data.private.krx.client` | `KrxClient` 는 하위 클래스: `from_settings` 가 GX 설정을 받고, 실패 문구는 GX 모양(`KrxError.__str__` = 가린 사유). `fetch_daily`(probe)도 같은 클라이언트로 |
| `data/krx/models.py` | `kbj.data.private.krx.models` | |
| `data/spool.py` | `kbj.store.spool` | 묶음 G 요청(같은 코드 두 벌이었다) |
| `services/auth/health.py` | `kbj.services.runtime.health` | detail 을 한 번 더 형태로 가린다 |
| `services/auth/service.py` | — | 발급 코드 전부 삭제. `TOKEN_KEY`·`WS_KEY_KEY`·`reader(redis, settings, name, *, now)`(GX 설정 → KBJ `reader`)·`redact`(KBJ masking)만. `main()` 은 안내 후 2 로 끝난다(`python -m kbj.services.auth` 로 띄운다) |
| `services/scheduler/service.py:http_master_downloader` | `kbj.data.private.kis.master` | `data.kis.master` 를 거쳐 import |

### P2.2 지운 것·바꾼 것

- 발급: `KisTokenIssuer`·`KisApprovalKeyIssuer`·`AuthService`·`build_auth_service`·`serve`·발급 경로 상수
  (`TOKEN_PATH`·`APPROVAL_PATH`) → KBJ `kbj/services/auth/issuer.py`·`service.py`(K8·K9).
- 파일 토큰 캐시: `FileTokenCache`·`FallbackTokenCache`·`default_token_provider`, 설정 `kis_token_cache_path`
  — 토큰은 Redis 에만(docs/secrets.md, `KIS_TOKEN_CACHE_PATH` 삭제).
- `config/settings.py`: `KIS_REAL_BASE`·`KIS_VTS_BASE`·`KRX_BASE` 문자열 삭제. `kis_base` 는 KBJ
  `credentials.base_url(env)`, 새 `kis_credentials()` 가 KBJ `KisCredentials` 를 만든다.
  `telegram_bot_token`·`telegram_chat_id` 필드 삭제(설계 §5.10 #42 — 읽는 곳이 없었다. 발송·수신은 KBJ
  notifier. `extra="ignore"` 라 `.env` 에 옛 이름이 있어도 무시된다).
- `scripts/probe_common.py`(`MASTER_URL` 삭제)·`probe_chain_fill.py` → `download_fo_master()`.
  `scripts/probe_all.py` → `KisClient(settings, token_provider=reader(…))`(Redis 가 없으면 토큰 없음).
- 동작 차이: KIS 가 토큰을 거절하면 GX 처럼 캐시를 지우지 않고 거절을 신고만 한다(`kis:token:rejected`
  — 새 값은 auth 가, ADR 0004 §4.3). KBJ 캘린더는 주식 정규장 지연 개장(그해 첫 거래일 10:00·수능일
  `late_open`)을 안다 — GX 파생 세션 상태 머신 시험은 그대로 통과했다.

### P2.3 시험 — 승격으로 지운 것(kbj 쪽에서 같은 단언이 돈다)

pytest 수집 항목(매개변수 펼친 수) 기준. 함수 수는 괄호.

| 지운 시험 | 항목(함수) | kbj 쪽 |
|---|---|---|
| `tests/unit/test_calendar.py` | 137 (36) | `tests/unit/core/test_calendar.py` 137 |
| `tests/property/test_calendar_properties.py` | 7 (7) | `tests/property/test_calendar_properties.py` 7 |
| `tests/unit/test_dependencies.py::test_xkrx_knows_2026_holidays` | 7 (1) | `tests/unit/core/test_xkrx_dependency.py` 7 |
| `tests/unit/test_ratelimit.py` | 44 (24) | `tests/unit/data/test_ratelimit.py` 44 |
| `tests/property/test_ratelimit_properties.py` | 3 (3) | `tests/property/test_ratelimit_properties.py` 3 |
| `tests/unit/test_auth_client.py` | 49 (28) | 20 함수 승격(`tests/unit/kis/test_token_cache.py`·`tests/unit/auth/test_issuer.py`). 8 함수(파일 캐시 5·기본 발급 제공자/폴백 3)는 기능과 함께 폐지(설계 §1.2) |
| `tests/unit/test_auth_service.py` | 63 (52) | `tests/unit/auth/test_auth_service.py` 63(49 함수) + health 3 함수 `tests/unit/runtime/test_runtime.py` |
| `tests/unit/test_kis_rest.py` | 18 (13) | 11 함수 `tests/unit/kis/test_kis_rest.py`. 2 함수(토큰 파일 권한·파일 캐시 폴백)는 폐지 |
| `tests/unit/test_kis_master.py` | 39 (17) | `tests/unit/kis/test_kis_master.py` |
| `tests/unit/test_krx_client.py` | 11 (7) | `tests/unit/data/private/test_krx_client.py` 11 |
| `tests/unit/test_krx_models.py` | 38 (16) | `tests/unit/data/private/test_krx_models.py` 38 |
| `tests/unit/test_spool.py` DiskSpool·인코딩 18 함수 | 36 (18) | `tests/unit/store/test_spool.py` 36 |
| `tests/unit/test_scheduler_service.py` 마스터 내려받기 3 함수 | 3 (3) | `tests/unit/kis/test_master_download.py` |
| `tests/unit/test_services_runtime.py::test_tagger_follows_the_session_state_machine` | 6 (1) | `tests/unit/runtime/test_runtime.py` 6 |
| 합계 | **461** | |

그대로 남긴 것(승격됐지만 GX 코드가 아직 legacy 라 지우지 않았다): `test_services_runtime.py` 의 하트비트·
healthcheck·log_event·sink·backoff 7개(GX `services.runtime` 은 GX `services.bus` 모델을 쓴다),
`test_scheduler_service.py` 의 상태 발행 7개(GX `Scheduler` 는 P7 까지), `test_scheduler_krx.py` 의 예산
4개(GX `KrxCallBudget` 그대로).

### P2.4 시험 — 고친 것·더한 것

- `tests/unit/test_spool.py::test_every_service_gives_its_sink_the_calendar_tagger` — 매개변수 `auth` 를
  뺐다(−1 항목). auth 진입점이 KBJ 로 옮겨 `services/auth/service.py` 에 `PostgresSink` 가 없다.
- `tests/unit/test_compose_file.py::test_heartbeat_names_match_the_services` — auth 하트비트 이름을 KBJ
  `kbj/services/auth/service.py`(`SERVICE = "auth"`, `Heartbeater(redis, SERVICE…)`) 소스에서 본다.
- `tests/unit/test_nogap_report.py:182` — 패치 대상 `core.calendar._default_calendar` →
  `kbj.core.calendar._default_calendar`(정본 위치, 묶음 B 요청).
- `tests/fakes/kis_server.py` — 발급 경로 상수 `TOKEN_PATH` 를 가짜 서버 안에 둔다(legacy 코드에는 없다).
- 새로: `tests/unit/test_kbj_bridge.py`(+1) — 제공자 없는 `KisClient` 가 요청 0건으로 `TokenUnavailable`,
  `reader` 가 빈 Redis 에서 `TokenUnavailable`, 발급 이름이 없다, 다시 내보낸 객체가 KBJ 와 같다.

### P2.5 결과

`scripts/test_legacy.sh gexlab`: **2,716 통과**(P1 3,177 − 승격·폐지 461 − 매개변수 1 + 다리 1).
`gexlab-integration`: 50(compose 1 + Docker 49) — 실행 결과는 묶음 H 보고에 적는다.

## P7. GEX 엔진·웹소켓·녹화 KBJ 이식 (묶음 FND-B·FND-C·ENG·ING, 확정 WRAP, 2026-10-13)

근거: `docs/p7_design.md` §1.8·§1.11, ADR 0029(GEX 이식 경계). 옮긴 만큼만 지웠다(D-P7-9). 남은 GX
수집 경로(poller·scheduler·gaps·store·db)는 legacy 외부 서비스로 KBJ DB(`search_path=prv_gex,ops`)에
붙는다(§10 Q1 — 이식은 P9).

### P7.1 다시 내보내기(shim — 정본 → 이 파일)

| legacy | KBJ 정본 | 묶음 |
|---|---|---|
| `core/{black76,chain,forward,gex,greeks,iv,levels,preprocess,specs}.py`·`core/metrics/*` | `kbj.core.gex.<모듈>`(같은 이름) | FND-C |
| `scripts/validate_greeks.py` 스냅샷 입력 모델 | `kbj.engines.gex.snapshot`(KIS 그릭스 필드가 있어 core 밖) | FND-C |
| `scripts/make_golden.py` core·chain-subset | `python -m kbj.services.engine.gex_golden core` | FND-C |
| `services/engine/{daily,evaluate,extended,flow,futures,registry}.py` | `kbj.engines.gex.<모듈>` | ENG |
| `services/engine/{service,publish,context,__main__}.py` | `kbj.services.engine.gex`(한 모듈) | ENG |
| `core/features.py` | `kbj.config.gex`(플래그 로더 — 값은 `config/gex.yaml features`) | ENG |
| `services/ws_gateway/**`·`services/recorder/**` | `kbj.services.ws_gateway`·`kbj.services.recorder` | ING |
| `data/kis/{ws,models}.py` | `kbj.data.private.kis.ws`·`kbj.data.private.kis.derivatives` | ING |
| `scripts/make_golden.py` raw | `python -m kbj.services.recorder.golden raw-parse` | ING |
| `services/{bus,chain_feed}.py`·레코드 모델 | `kbj.services.runtime.{bus,chain_feed}`·`kbj.core.gex.records` | FND-B |

`config/{kis_ws,kis_ws_fields,ws_budget}.yaml` 은 레포 루트 `config/` 로 `git mv`(ING). `config/features.yaml`
은 GX compose 마운트·`test_compose` 가 읽어 남겼다(값이 KBJ `config/gex.yaml features` 와 같은지 다리 시험이
본다 — compose 를 KBJ 로 옮기면 지운다).

### P7.2 지운 것·바꾼 것

- poller 투자자별 폴링 삭제(D-P7-5): `FHPTJ04030000`·`_investors` 계획 0 — `KIS:market_investor_intraday`
  는 KBJ `flows.market_minute` 만 부른다(ADR 0019). `collector._on_investor` 는 부르는 계획이 없는 죽은
  코드로 남았다(P9 정리). `services/scheduler/service.py` 1줄(`night_investor` 필드 삭제 대응),
  `db/migrate.py`(KBJ DB 일 때 GX 마이그레이션 건너뜀).
- `tests/fakes/engine_inputs.py` 는 KBJ 로 `git mv` 하고 legacy 자리에 `snapshot_cycle` 얇은 shim(남은
  `shadow_report` 시험용).

### P7.3 시험 — 승격으로 지운 것(KBJ 쪽에서 같은 단언이 돈다)

pytest 수집 항목(매개변수 펼친 수, 2026-10-13 `--collect-only`).

| 지운 시험 | 항목 | KBJ 쪽 |
|---|---|---|
| 단위 `black76` 260·`greeks` 497·`forward` 119·`iv` 117·`gex` 109·`specs` 80·`levels` 75·`chain` 63·`flow` 46·`preprocess` 38·`vol` 23·`futures_metrics` 16·`exposure` 14 | 1,457 | `tests/unit/core/gex/` |
| 속성 9파일(`probe_properties` 2 는 남김) | 46 | `tests/property/gex/` |
| `tests/test_sign_conventions.py` | 9 | `tests/unit/core/gex/` |
| 골든 `test_core_golden` 22·`test_chain_subset` 14 | 36 | `tests/golden/gex/` |
| FND-C 소계 | **1,548** | |
| `test_engine_{service 29,evaluate 25,ticks 22,flow 14,investor 11,daily 10,extended 8,futures 6}`·`test_features` 21 | 146 | `tests/unit/engines/gex/` |
| `test_store_engine.py` 32 중 30(2 남김 — GX 001/002 마이그레이션·shadow_report 읽기) | 30 | `tests/unit/store/test_gex_repo_engine.py` |
| ENG 소계 | **176** | |
| `test_kis_ws` 38·`test_kis_models` 39 | 77 | `tests/unit/kis/test_{kis_ws,derivatives}.py` |
| `test_ws_{client 50,gateway_service 20,watch 20,subscriptions 10,backoff 6,budget 3}` | 109 | `tests/unit/ws_gateway/` |
| `test_recorder_envelope` 8·`test_recorder_service` 7 중 6(PostgresSink 스풀 1 은 `test_recorder_postgres_spool.py` 로 남김) | 14 | `tests/unit/recorder/` |
| 골든 `test_raw_golden` | 32 | `tests/golden/gex/test_raw_golden.py` |
| ING 소계 | **232** | |
| 합계 | **1,956** | |

통합(`gexlab-integration`): `test_engine_store` 9 중 8 → `tests/integration/test_gex_engine_pg.py`(1 남김 —
shadow_report), `test_make_golden_db` 3 → `tests/integration/test_recorder_golden_pg.py`. 50 → **39**.

### P7.4 시험 — 고친 것·더한 것

- `test_fixtures_clean.py` 매개변수 −3: 옮긴 코어 골든 데이터 1(FND-C)·raw 녹화 2(ING) — 그 파일의 비밀
  검사는 KBJ `scripts/check_public_safety.py` 가 본다.
- `test_poller_{collector,planner,service}.py`: 투자자별 단언을 '부르지 않는다' 단언으로 바꿨다(항목 수 유지).
- `test_metric_report.py` 2줄·`test_spool.py`(이동 반영)·`tests/integration/test_engine_store.py`(남은 1개).
- 새 다리 시험 +3: `test_kbj_gex_core_bridge.py`(FND-C)·`test_kbj_gex_engine_bridge.py`(ENG)·
  `test_kbj_gex_ingest_bridge.py`(ING) — 다시 내보낸 객체가 KBJ 와 같은지, 남긴 합성 fixture 가 KBJ 사본과
  바이트까지 같은지, `config/features.yaml` 값이 KBJ 와 같은지.

### P7.5 결과

`scripts/test_legacy.sh gexlab`: **760**(2,716 − 1,956 − 매개변수 3 + 다리 3). `gexlab-integration`: **39**
(compose 2·Docker 37 — CI 는 38 이상). 기준 수는 `scripts/test_legacy.sh` 머리말·`.github/workflows/ci.yml`.

## P9. GX 수집 경로 KBJ 이식 (묶음 GXI, 2026-10-14 — WRAP 확정 전 초안)

근거: `docs/p9_design.md` D-P9-7·§7.2 GXI, ADR 0036(초안). 기준 시점: 커밋 f6aeebd(legacy 를 지우기 직전 — 태그
legacy-final 은 사용자가 GitHub 에서 달 수 있다). 이 폴더에서는 시험을 지우고 `scripts/metric_report.py` `MOVED_TESTS` 에
`test_shadow_report` 의 KBJ 자리 한 줄을 더했다(P7 과 같은 방식 — 명세 시험 실행·읽기 경로). 폴더 삭제는 WRAP.

### P9.1 옮긴 것(GX → KBJ)

| GX | KBJ | 메모 |
|---|---|---|
| `services/poller/*`(config·context·endpoints·planner·quality·ready·sink·collector·service) | `kbj/services/gex_poller/*` | 본문 그대로. 투자자별(`_investors`·`_on_investor`·`investor()` 요청·`write_investor`)은 옮기지 않았다(ADR 0029 §3-6, D-P7-5) |
| `services/scheduler/minute.py`·`gaps.py`(GapDaily)·`open_check.py`·`krx.py`·`master_check.py` | `kbj/services/collectors/gex_{minutes,gap_daily,open_check,krx,master_check}.py` | 구동 클래스(MinuteDaily·GapDaily·OpenCheck·KrxDaily)는 본문 그대로, 등록부 처리기 `run_*` 가 한 실행에 한 step(제출은 그 자리 — `inline_submit`) |
| `services/gaps.py` | `kbj/services/collectors/gex_gaps.py` | 무결측 판정 규칙 그대로 |
| `services/scheduler/service.py` `_master_step`·`_apply_master` | `kbj/services/collectors/kis_master.py`(`apply_master`·`run`·`run_night`) | 등록부 `kis.master`(PRE_DAY)·`kis.master_night`(PRE_NIGHT) |
| `services/scheduler/service.py` `Scheduler`(세션 상태 발행·단계 구동) | **옮기지 않음** | 상태 발행은 KBJ scheduler 서비스, 단계 구동은 등록부(작업 7개 — `config/jobs.yaml`) |
| `data/store.py` `PostgresSink` 쓰기(수집 표) + 읽기 | `kbj/services/gex_poller/store.py:GexIngestStore`(P7 `SpooledGexStore` 상속 — 스풀 한 벌) + `kbj/store/repos/gex_ingest.py`(표·행 함수·`PgGexIngestRepo`) | 연결 관리(dsn·재연결·stats)는 `kbj.store.db.connect` |
| `scripts/nogap_report.py`·`shadow_report.py` | `python -m kbj.services.engine.gex_report {nogap,shadow}` | probe #61·#62 명령 갱신은 WRAP |
| `scripts/make_synthetic_fixtures.py` | `tests/fixtures/synthetic/gex/make_synthetic_fixtures.py` | 출력 자리만 KBJ 사본(`tests/fixtures/synthetic/{kis,krx}`·`gex/validation`). GX 사본은 남은 GX 시험(`test_kbj_gex_core_bridge`·`test_validate_greeks`)이 읽어 남겼다 — 폴더와 함께 삭제 |
| `tests/fakes/kis_server.py`·`krx_server.py`·`gap_inputs.py`·`pg.py` | `tests/unit/gex_ingest/fakes/*` | KBJ 설정·클라이언트로 import 만. GX 사본은 남은 GX 시험이 쓴다 |

fixture(`tests/fixtures/{kis,krx,validation}`)는 `git mv` 하지 않았다: KBJ 에 이미 바이트까지 같은 사본이 있고(P2·P7),
남은 GX 시험 277 이 이 폴더 사본을 읽는다. 폴더 삭제(WRAP) 때 함께 사라진다.

### P9.2 시험 — 승격(같은 단언, import 경로만) 397

| GX `tests/unit/` | GX 항목 | KBJ `tests/unit/gex_ingest/` | 승격 | 폐지 |
|---|---|---|---|---|
| `test_gaps` | 25 | `test_gaps` | 25 | 0 |
| `test_master_check` | 6 | `test_master_check` | 6 | 0 |
| `test_nogap_report` | 10 | `test_nogap_report` | 10 | 0 |
| `test_poller_{collector 40,fake_kis 9,planner 32,quality 6,ready 18,service 11}` | 116 | 같은 이름 | 116 | 0 |
| `test_scheduler_gaps` | 14 | `test_scheduler_gaps` | 13 | 1 |
| `test_scheduler_krx` | 42 | `test_scheduler_krx` | 41 | 1 |
| `test_scheduler_minute` | 55 | `test_scheduler_minute` | 55 | 0 |
| `test_scheduler_open_check` | 10 | `test_scheduler_open_check` | 9 | 1 |
| `test_shadow_report` | 63 | `test_shadow_report` | 63 | 0 |
| `test_spool` | 23 | `test_spool` | 6 | 17 |
| `test_store_engine`(P7 에 남긴 2) | 2 | `test_store_engine` | 1 | 1 |
| `test_store_sql` | 117 | `test_store_sql` | 52 | 65 |
| 합계(파일 17개 삭제) | **483** | | **397** | **86** |

고친 것(단언은 그대로): logger 이름(`services.scheduler.*` → `kbj.services.collectors.*`·`kbj.services.gex_poller.*`),
표 이름에 스키마(`prv_gex.`·`ops.`), `replace_gap_report` 의 트랜잭션 모양(KBJ `with conn` 한 번).

### P9.3 시험 — 폐지 86(사유)

- `test_scheduler_gaps::test_scheduler_runs_the_report_and_isolates_its_errors`·`test_scheduler_open_check::test_scheduler_runs_the_check_and_isolates_its_errors`(2):
  GX `Scheduler` 를 옮기지 않았다 — 단계 격리는 등록부 실행기(작업마다, `tests/unit/scheduler/test_runner.py`).
- `test_scheduler_krx::test_the_cap_setting_defaults_to_200_and_must_be_positive`(1): GX 설정 `KRX_DAILY_CALL_CAP` 이 KBJ 에
  없다 — 하루 상한은 `config/limits.yaml` krx(공용 `krx:calls` 예산)가 정본, 작업은 프로세스 셈만(`PROCESS_CALL_CAP`).
- `test_spool` 14함수 17항목(통계·연결 재시도·자체 health 행·`from_settings`·서비스 소스 검사): GX `PostgresSink` 스풀
  경로 — KBJ 는 P7 ING 의 `SpooledGexStore` 한 벌(`tests/unit/recorder/test_recorder_store.py`·`tests/unit/store/test_spool.py`,
  남은 6 이 수집 표 스풀·재생).
- `test_store_engine::test_applied_migrations_are_left_alone`(1)·`test_store_sql` GX 마이그레이션 DDL·순서 15함수 51항목:
  KBJ 는 `0006_gex.sql`·`kbj.store.migrate` 가 같은 표를 만들고 `tests/test_store_layout.py`·`tests/unit/store/test_migrate_unit.py` 가 본다.
- `test_store_sql` `PostgresSink` 연결 관리 12함수 14항목(dsn 해석·재연결·stats·접속 오류 문구·설정 `DATABASE_URL`): KBJ
  `kbj.store.db.connect`(StoreError 가림 — `tests/unit/store`)·`SpooledGexStore` 가 맡는다.

옮기지 않고 GX 에 남긴 수집 시험(WRAP 이 폴더와 함께 지운다 — KBJ 쪽 대체는 sim·통합 시험): `test_scheduler_service`(GX
`Scheduler` — 23), `test_full_day_harness`(3), `test_services_runtime`·`test_recorder_postgres_spool`, 통합
`test_full_day`·`test_store`·`test_scheduler_*`·`test_spool_db`·`test_nogap_report`·`test_shadow_report`.

### P9.4 KBJ 쪽

- 새 시험 `tests/unit/gex_ingest/`: `test_jobs`(처리기 7개 — 분봉·무결측·KRX·개장 확인·마스터) 20, `test_cli` 5,
  `test_compose_poller` 2, `test_migration_0022` 3. 등록부 `test_p9_gex_ingest_is_registered_with_kbj_owners_and_off` 1.
- 지운 KBJ 항목 1: `tests/unit/auth/test_owner_compat.py::test_gexlab_reader_code_reads_what_kbj_auth_stored`(GX 코드
  하위 프로세스 — 없으면 조용히 skip 되던 것. GX reader 는 KBJ 로 옮겼다). `tests/unit/kis/test_kis_rest.py` 의 GX 프로브
  하위 프로세스 대조는 f6aeebd 에서 잡은 값으로 고정(항목 수 그대로, skip 없음).
- `tests/unit/kis/test_kis_master.py`·`tests/unit/data/private/test_private_synthetic_fixtures.py`: GX fixture 경로 대신
  옮긴 생성기의 출력과 대조.

### P9.5 결과

`scripts/test_legacy.sh gexlab`: **277**(760 − 483). `gexlab-integration`: **39**(그대로).

## P9 WRAP. 폴더 삭제 (2026-10-14 — ADR 0038)

GXI(위 'P9')가 수집 경로를 KBJ 로 옮긴 뒤 폴더를 지웠다(`git rm -r legacy/gexlab`, 이 파일만 `docs/migration/gexlab.md` 로).
지우기 전 `scripts/test_legacy.sh gexlab` **277**·`gexlab-integration` **39** 였다. 조용한 skip 이 되지 않게 전부 아래로
갈랐다(KBJ 쪽 수집 수는 `docs/p9_design.md` 'WRAP 구현 기록').

### P9W.1 통합 39 → KBJ 통합 9 로 옮김, 30 폐지

| GX 통합 | 항목 | 처리 | 사유 |
|---|---|---|---|
| `test_scheduler_krx` | 1 | **옮김** → `tests/integration/test_gex_ingest_pg.py` | 같은 단언(대상 `GexIngestStore`·`kbj.store.migrate`·표 이름 `prv_gex.`) |
| `test_store`(poller 왕복·값·격리/health/raw·다시 쓰기) | 4 | **옮김** | 같음. 다시 쓰기는 GX 싱크 통계(`pg.stats`) 대신 행 수만 — KBJ 저장소의 통계 모양이 다르다 |
| `test_store`(분봉·마스터·리포트 교체) | 3 | **옮김** | 같음 |
| `test_nogap_report` | 1 | **옮김** | `python -m kbj.services.engine.gex_report nogap` 의 `main` |
| `test_store` 마이그레이션 5(두 번 적용·hypertable·압축 정책·autocommit·동시 실행) | 5 | 폐지 | KBJ 마이그레이션은 `tests/integration/test_migrations_pg.py`(0001~0022 — 두 번 적용·hypertable·동시 실행)가 본다 |
| `test_store` 웹소켓 원문·NUL·auth health·재연결·오류 가림·압축 청크 중복·체결 2·세션 기록·공백 재판정·무결측 입력·ws 이벤트 | 13 | 폐지 | 원문·체결·세션 기록·health 는 P7 ING 의 `SpooledGexStore`(`tests/integration/test_recorder_golden_pg.py`·`tests/unit/recorder`), 재연결·오류 가림은 `kbj.store.db`(`tests/unit/store`), 무결측 입력 읽기는 단위 `tests/unit/gex_ingest/test_store_sql.py`(52)가 같은 SQL 을 본다 |
| `test_store::test_krx_daily_night_iv…`·`test_krx_loaded_and_option_listing…` | 2 | 폐지 | 옮긴 `test_loading_twice_and_after_a_restart…` 가 같은 표·같은 읽기(야간 IV NULL·상장 목록)를 단언한다 |
| `test_scheduler_minute` 2·`test_scheduler_gaps` 1 | 3 | 폐지 | GX `Scheduler` 조립(스풀 싱크 경유)·서비스가 쓴 하루 — KBJ 는 등록부 작업(`tests/unit/gex_ingest/test_jobs.py`)과 하루 운영 시뮬레이션(`tests/sim/test_one_day*.py` — 등록부 GX 작업이 실행기로 돈다)이 본다 |
| `test_full_day` | 1 | 폐지 | GX 프로세스 하루(compose) — 같은 자리는 하루 운영 시뮬레이션 + 메인의 VM 전환 probe #68~#70 |
| `test_spool_db` | 1 | 폐지 | GX `PostgresSink` 스풀 — KBJ 스풀은 한 벌(`kbj.store.spool` — `tests/unit/store/test_spool.py`, P7 ING `SpooledGexStore`) |
| `test_shadow_report`·`test_engine_store` 의 shadow 읽기 | 2 | 폐지 | `gex_report shadow` 의 읽기는 단위 `tests/unit/gex_ingest/test_shadow_report.py`(63)와 엔진 저장 통합 `tests/integration/test_gex_engine_pg.py` 가 본다 |
| `test_compose` 2·`test_docker_cleanup` 1 | 3 | 폐지 | GX 자기 compose·시험 컨테이너 청소 — KBJ 는 `tests/test_store_layout.py`(compose)·`tests/integration/conftest.py`(같은 청소 방식) |

### P9W.2 단위·속성 277 → 폐지(이미 KBJ 에 승격된 것은 'P9.2'·'P7.3')

| GX 파일 | 함수 | 사유 |
|---|---|---|
| `test_scheduler_service`(23 항목) | 20 | GX `Scheduler` 클래스는 옮기지 않았다(ADR 0036 §2) — 단계 격리·재시도는 등록부 실행기(`tests/unit/scheduler/test_runner.py`), 세션 상태 발행은 `tests/unit/scheduler`, 하루 흐름은 `tests/sim` |
| `test_full_day_harness` | 3 | GX 하루 하네스(위 `test_full_day` 의 도구) |
| `test_services_runtime`·`test_recorder_postgres_spool` | 13 | GX 서비스 런타임·`PostgresSink` 스풀 — KBJ `kbj.services.runtime`(`tests/unit/runtime`)·`SpooledGexStore`(`tests/unit/recorder/test_recorder_store.py`) |
| `test_kbj_bridge`·`test_kbj_gex_{core,engine,ingest}_bridge` | 4 | GX → KBJ 다시 내보내기(shim) 다리 — shim 이 폴더와 함께 사라졌다. 바이트 동일 fixture 대조는 생성기 비교(`tests/unit/kis/test_kis_master.py`·`tests/unit/data/private/test_private_synthetic_fixtures.py`)가 잇는다 |
| `test_metric_report`·`test_validate_greeks`·`test_probe_helpers`·`property/test_probe_properties` | 108 | 옮기지 않은 GX 운영·실측 도구(`metric_report`·`validate_greeks`·`probe_*` — D-P9-7). 원본 GEXLAB 레포에 남는다 |
| `test_compose_file`·`test_dependencies`·`test_fixtures_clean`·`test_pytest_marks`·`test_repo_rules` | 27 | GX 레포 규칙(자기 compose·의존성·fixture 청결·마커·레포 규칙) — KBJ 는 `tests/test_store_layout.py`·`tests/test_public_outputs.py`(fixture 출처)·`pyproject.toml --strict-markers`·`tests/test_import_contracts.py` 가 같은 일을 한다 |

(함수 수는 파일의 `def test_` 줄 — 매개변수화로 수집 항목은 277·39 다.)

