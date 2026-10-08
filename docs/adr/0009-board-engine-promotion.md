# ADR 0009 — 신고가 board 엔진 승격: 골든 먼저, 순수/IO 분리, legacy shim, 역사적 신고가 깊이 (2026-10-07)

상태: **확정**(2026-10-07 — 초안 P3 묶음 E1, 번호·상태는 웨이브 3 묶음 S 가 확정 — 아래 'S 확정 메모', `docs/p3_design.md` §9.3).

> **2026-10-08: 신고가 정의(축·창·역사적 깊이)는 [ADR 0017](0017-newhigh-three-axes.md) 이 대체했다**(사용자 요청 — d60·252봉 → d120 = 120 시장 거래일·w52 = 달력 364일·hist = 상장 이후 전체, 원천 바닥). 골든은 새 엔진으로 재캡처했고 정의는 독립 오라클이 지킨다. 아래 1~8 의 나머지(골든 먼저·순수/IO 분리·shim·잠정 → 확정)는 그대로다.
근거: `docs/p3_design.md` D-P3-5·8·10·11, §1.3·§3.8·§4.1·§8.1, 메인 결정 R8(2026-10-07), ADR 0001 Q2,
conflict_map §1.7·§1.8.
구현: `kbj/engines/board/{config,newhigh,aggregate,rankings,kinds,themes,build}.py`, `config/board.yaml`,
`config/knowledge/{themes,sectors,sector_map}.yaml`(ET `board/knowledge/` 에서 옮김),
`kbj/services/engine/board.py`(`board.daily`·`board.confirm`), legacy shim `legacy/etf_traker/board/engine/*.py`,
시험 `tests/golden/`·`tests/unit/engines/board/`·`tests/oracles/newhigh_loop.py`·`tests/property/test_newhigh_oracle.py`.

## 1. 맥락

- 신고가 정본은 ET board 엔진이다(CLAUDE.md 3장 — "신고가는 board 엔진"). P3 는 그 계산을 kbj 로 옮기되, PLAN P3
  완료 기준은 "신고가 보드 = 기존 board 산출" 이다.
- ET `build.run` 은 sqlite 읽기·계산·state 파일 쓰기가 한 함수(300줄)에 섞여 있어, kbj 의 저장소(Postgres)·작업
  등록부와 그대로 붙일 수 없다.
- 옮긴 뒤 legacy board 가 계속 돌아야 하고(legacy 시험 1,267), 같은 계산을 두 벌 두지 않는다.
- KRX 백필(R11)이 상장일까지 닿지 않는 종목이 있다. ET 의 역사적 최고가 스칼라에는 네이버 일봉에서 나온 값이
  섞여 있다(U4 — 옮기지 않는다).

## 2. 결정

1. **골든 먼저**(D-P3-10 ①): shim 전 legacy 엔진으로 합성 30거래일 × 300종목(시드 20260826)을 돌려 입력(그 순간의
   DB 상태 전부)과 산출 5종(universe·newhigh·sectors·events·rankings)을 `tests/golden/board/` 에 캡처했다(legacy
   커밋 `4ea5b7f`, 체크섬 고정 — `test_board_golden_meta.py`). 합성이라 공개 레포에 둔다(U3). 사례를 일부러 심었다:
   돌파·근접·분할 계단(action·none·unknown)·거래정지 공백·상장 60일 미만·시총 하한 미달/모름·종가/고가 기준 차이·
   거래대금 없음/0·KRX 확정/KIS 잠정/일부 확정·늦은 스냅·단위 뒤집힘·테마 시드 비상장/개명/오타·분류 혼재·거래량 이상.
2. **순수/IO 분리**(D-P3-5): `kbj.engines.board.build.compute_day(BoardInputs, cfg) -> BoardDay` 는 ET `run` 에서
   DB·파일 I/O 만 뺀 같은 계산이다(함수 본문 그대로, 반환 dict 키 그대로). 읽기·쓰기는 읽는 쪽이 한다 — kbj 는
   `kbj.services.engine.board`(저장소), legacy 는 `board/engine/build.run`(sqlite). run_log 에 남기던 실패 단계는
   `BoardDay.steps` 로 돌려준다.
3. **비교는 허용오차로**: 키 집합·목록 순서·문자열은 정확히, 실수는 `math.isclose(rel_tol=1e-9, abs_tol=1e-6)`.
   무시는 `generated_at` 하나, 정규화는 잠정 종가 문구 한 문장(ET "네이버 16:07 값" → kbj "KIS 마감값(잠정)")과
   `source` 대소문자뿐. 30일 전부 통과.
4. **legacy shim**(D-P3-10 ④): ET `engine/{newhigh,aggregate,rankings,kinds,themes}.py` 는 kbj 를 다시 내보내기만
   한다(같은 함수 객체 — 다리 시험 `test_kbj_engine_shim.py`). `rankings.build`·`build.run` 은 state 파일·sqlite 를
   읽어 kbj 함수에 넘긴다. legacy 는 kbj 비공개 이름을 가져오지 않는다(필요한 것은 kbj 공개 별칭 `format_cell`). ET `facts.eok`(억원 표기)도
   kbj `rankings.eok` 를 다시 내보낸다.
   `scripts/check_canonical.py` 의 legacy 허용 목록에 `kbj.engines.board` 를 더했다(S 확정).
5. **숫자는 한 곳**: 엔진 임계값 절은 `config/board.yaml` 하나. legacy `config.load()` 는 그 파일을 settings.yaml 에
   합친다(같은 절이 파일에 있으면 파일이 이긴다 — 시험용 임시 설정). 모르는 키·빠진 키는 `BoardConfig` 가 거부한다.
   자체 사전 셋은 `config/knowledge/` 로 옮겼다(공개 등급 — conflict_map §1.8).
6. **역사적 신고가 깊이**(D-P3-11, 메인 결정 R8): ET 의 네이버 파생 스칼라는 옮기지 않는다. kbj 스칼라는 받은
   일봉으로 처음부터 쌓고 `history_from`(받은 이력의 첫날)을 함께 둔다. 상장일이 그보다 앞이거나 상장일을 모르면
   역사적 신고가를 계산하지 않고 사유를 남긴다("이력이 상장일 전에서 끊김"·"상장일을 몰라 …"). 범위 시작일과
   막힌 수는 newhigh 산출의 `hist_scope` 에 싣는다(API·화면이 그대로 표시). KRX 백필이 과거를 채운 뒤에는
   `kbj.services.engine.board.rebuild_alltime` 으로 스칼라를 처음부터 다시 쌓는다(§3.8 — `python -m kbj.services.scheduler backfill market.backfill …` 가 범위를 다 받은 뒤 부른다). 골든 입력에는 `history_from` 이 없어
   ET 동작 그대로다.
7. **잠정 → 확정**(D-P3-8): `board.daily`(16:20) 는 KIS 마감 스냅으로 잠정 보드(`estimated`), `board.confirm`(08:40,
   `krx.daily` 뒤) 은 KRX 확정 스냅 비율이 0.9(ET `CLOSE_CONFIRM_MIN`) 이상일 때만 전 거래일을 다시 계산해 덮는다
   (`ok`, 아니면 쓰지 않고 `not_ready`). 그날 스칼라 몫은 확정 일봉으로 다시 쓴다(`restate_last_day` — 직전일까지의
   최고가는 그대로, 그날을 포함한 최고가만 다시. 잠정값이 최고였는데 확정값이 낮아지면 최고일은 None — 지어내지
   않는다). 바뀐 라벨(생김·사라짐·등급 변경)은 newhigh 산출 `confirm` 과 실행 detail `n_changed` 에.
   같은 as_of 재실행(`board.daily` 포함)도 그날 몫을 지금 일봉으로 다시 쓴다(값이 같으면 그대로 — 멱등).
   다음 거래일 몫까지 이미 굴린 스칼라가 있으면(다음 날 `board.daily` 뒤의 늦은 확정·수동 재실행) 그날의
   '직전일까지 최고가' 를 복원할 수 없으므로 보드를 덮지 않고 `failed`(사유 기록)로 끝낸다 — 그날 보드는
   잠정(`estimated`) 그대로 남는다(독립 검증에서 찾은 경우).
8. **독립 오라클**: ET flowlab `verify.check_newhigh` 의 단순 루프를 `tests/oracles/newhigh_loop.py` 로 이식해
   hypothesis 시계열에서 "엔진 라벨 ⊆ 루프 라벨, hist 는 w52 필요조건" 을 본다.

## 3. 결과

- 신고가 계산은 `kbj.engines.board` 한 곳이다. legacy board 는 같은 함수를 불러 같은 화면을 만든다.
- 골든 생성기를 shim 뒤에 `--allow-shim` 으로 돌린 산출(legacy sqlite → kbj `compute_day`)도 골든과 차이 0 이다 —
  legacy shim 의 입력 조립이 맞다는 확인. 생성기는 shim 을 감지하면 골든 폴더에 쓰지 않는다.
- 원본 시험 153개를 kbj 로 옮겼다(import 경로만 — 소스 검사 시험 8개는 따옴표·보는 함수 이름만 kbj 코드에
  맞췄다, `tests/unit/engines/board/test_build_source.py` 머리말). legacy board 시험 1,267 → 1,115(다리 시험 +1).
- 저장은 원 단위(엔진 안 억원 × 1e8 — `kbj.core.rows.StockDay`), 산출 JSON 은 ET 와 같은 키(억원)다.
- 골든 파일(약 5.6MB, gzip)은 다시 만들지 않는다 — 계산을 바꿀 일이 생기면 새 골든 세트와 ADR.

## 4. 남은 것 [확인 필요]

- 일봉 창 `service.series_days: 420`(ET px 보관일과 같게) — 역사적 기준의 저항두께가 이 창 전체를 본다.
- kbj 에는 ET run_log 같은 "수집 실패 사유" 표가 없다 — `collect_notes` 는 지금 대조 invalid 안내만 싣는다.
  수집 처리기의 실패 사유를 보드 배너로 올리는 길(작업 실행 기록 읽기)은 S·A 와 정한다.
- 섹터 분류 혼재 안내는 kbj 에서 사전 하나(board48)라 나오지 않는다. `pub_themes.sector_map`(P5)으로 옮길 때 다시 본다.

## S 확정 메모 (웨이브 3, 2026-10-07)

- 번호 0009·상태 **확정**. 계약 ⑨(엔진 I/O 금지 — `kbj.data`·`kbj.services`·`kbj.store`·`kbj.reports`·httpx·psycopg·
  redis·fastapi·starlette·uvicorn, 간접 포함)를 넣었다. `kbj/engines/board/config.py` 가 `config/board.yaml`·
  `config/knowledge/*.yaml` 을 읽는 것은 **입력 준비**로 허용한다(`kbj.config` 는 금지 목록에 없다) — 계산 함수
  (`compute_day` 등)는 설정을 인자로 받아 골든·속성 시험의 '입력만으로 결정' 이 깨지지 않는다(§4 의 [확인 필요] 하나를 닫음).
- `board.daily`·`board.confirm` 을 켰다(등록부 `enabled: true`). 하루 운영 확장 시뮬레이션(`tests/sim/test_one_day_p3.py`)이
  KIS 마감 → 16:20 잠정 보드(estimated) → 다음 날 08:05 KRX → 08:40 확정 보드(ok, `payload.confirm.previous_quality`)
  전이를 실제 처리기로 본다.
- R8 은 메인 결정대로: 네이버 출처 옛 역사적 최고가는 옮기지 않고, KRX 백필이 닿는 범위(`history_from`)에서만 계산해
  그 시작일을 응답에 싣는다. `market.backfill` 뒤 `rebuild_alltime` 연결은 최종 점검에서 넣었다 — 백필 CLI 가 범위를
  다 받으면(종료 코드 0) 오늘(KST)까지의 일봉으로 다시 쌓고, 다시 쌓기가 실패하면 종료 코드 1(삼키지 않음 — 시험
  `tests/unit/collectors/test_krx_daily.py`).
- legacy 시험 기준: board 1,267 → 1,115(승격 153 − 다리 +1) — `scripts/test_legacy.sh` 를 S 가 확정했다.
