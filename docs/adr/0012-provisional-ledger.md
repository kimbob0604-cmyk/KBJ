# ADR 0012 — 잠정 → 확정 원장 우선순위·차이 기록·KRX D+1 대조·거래소 구분 기본값 (2026-10-07)

상태: **확정**(2026-10-07 — 초안 P3 묶음 M, 번호·상태는 웨이브 3 묶음 S 가 확정 — 아래 'S 확정 메모', `docs/p3_design.md` §9.3).
근거: `docs/p3_design.md` D-P3-7·8·9, §3.5·§3.6, `docs/metrics.md` §0·§2·§5·§6.6, ADR 0001 Q1, conflict_map E1·§1.11.
구현(웨이브 1): `kbj/core/rows.py`(`ledger_rank`·`row_rank`·`pick_best`), `kbj/store/repos/flows.py`
(`plan_investor_upsert` — Pg·메모리 공용), 마이그레이션 `0008_market_flows_p3.sql`(`investor_revision`·
`eod_reconcile`·`ledger_check`), `config/markets.yaml`(`kis.venues`·`reconcile.*`), 시험
`tests/unit/store/repo_cases.py`(메모리·Pg 같은 묶음), `tests/unit/core/test_rows.py`.

## 1. 맥락

- 같은 (종목, 날짜) 의 값이 세 번 들어온다: 장중 KIS 가집계·순위(증권사 추정 — 상위 목록만), 15:35 KIS 마감 확정
  (전 종목), 다음 영업일 08:05 KRX 일별 확정(종가·거래대금·시총 — 투자자별은 KRX OpenAPI 에 없다).
- 화면마다 따로 받은 숫자를 섞으면 페이지끼리 숫자가 어긋난다(metrics §0 "일별 원장 하나").
- 장중 잠정 값이 확정으로 바뀔 때 얼마나 달랐는지 남기지 않으면, 장중 화면을 믿어도 되는지 판단할 근거가 없다.
- KIS·KRX 응답이 넥스트레이드(NXT)를 포함하는지 아직 모른다([실측 필요] — 체크리스트 #25).

## 2. 결정

1. **원천 우선순위**: 한 키의 값은 `krx(ok) > kis(ok) > kis(estimated) > kis.prelim(estimated) > 그 밖` 중 하나만
   쓴다. invalid 는 원천과 무관하게 맨 뒤 — 다른 행이 없을 때만 남아 엔진이 집계에서 빼고 수를 센다. 같은
   원천·품질이면 거래소 `KRX > '' > TOTAL > NXT`. 고르는 함수는 `kbj.core.rows` 하나(엔진·저장소 공용).
2. **덮어쓰기와 차이 기록**: 마감 확정 수집(`revise=True`)은 같은 (종목, 날짜, 투자자, 거래소) 의 뒤처진 원천
   행(장중 잠정)을 **지우고** 확정 행을 쓰며, 지우기 전 값·받은 시각과 확정 값·차이를 `prv_flows.investor_revision`
   에 **같은 트랜잭션**으로 남긴다(차이 기록이 실패하면 덮어쓰기도 되돌린다 — 통합 시험).
3. **늦은 잠정 거부**: 앞선 원천이 이미 있는 키에 잠정이 오면 쓰지 않는다(`skipped`).
4. **KRX D+1 대조**: `krx.daily` 끝 단계가 전 거래일 KIS 마감 스냅을 KRX 확정치와 대조해 `prv_market.eod_reconcile`
   에 남기고, 허용(종가 0·거래대금 0.5%·시총 0.5% — [확인 필요])을 넘으면 KIS 행을 `invalid` + 사유로 바꾼다.
   KRX 행은 따로(기본 키의 source 가 다르다) 들어가 원장이 krx 를 고른다. 08:40 `board.confirm` 이 KRX 확정
   일봉으로 전 거래일 보드를 다시 계산한다.
5. **거래소 구분 기본값**: 실측 전에는 KIS 를 `config/markets.yaml` `kis.venues: [KRX]` 하나로만 부르고, 합계(TOTAL)는
   계산하지 않는다. 화면에 "NXT 미포함" 꼬리표.
6. **검산 불가**(메인 결정 R2): 투자자 4구분 중 하나라도 응답에 없으면 검산 ①은 `None`(검산 불가) — 0 으로 바꾸지
   않고 사유를 notes·`docs/probe_results.md` 에. 합성 원장에서는 ①②③ 전부 강제.

## 3. 결과

- 장중 값(`estimated`) → 마감(`ok`) → 다음 날 대조(`ok` 또는 `invalid`) 전이가 표와 시험으로 고정된다.
- 투자자별 순매수는 대조 원천이 없어 KIS 마감값이 최종이다 — 그 한계를 문서·화면에 적는다.
- 같은 고르기 규칙을 엔진(원장)·저장소(시계열 읽기)·API 가 함께 쓴다(두 벌 금지).
- venue 를 늘리면(실측 뒤) KIS 호출이 배로 는다(마감 수집 약 24분 → 48분 — 설계 §3.7). 그때 이 ADR 을 고친다.

## 4. 대안

- 잠정·확정을 다른 표에 두기: 원장이 두 벌이 되어 페이지끼리 어긋난다 — 버림.
- 잠정 행을 지우지 않고 남기기(우선순위로만 고르기): 차이 기록 시점이 모호해지고 표가 커진다 — 덮어쓰기 + 차이 표로.
- KRX 확정치로 KIS 행을 고쳐 쓰기: 원래 받은 값을 잃는다 — KRX 행을 따로 넣고 KIS 행은 품질만 바꾼다.

## S 확정 메모 (웨이브 3, 2026-10-07)

- 번호 0012·상태 **확정**. 등록부 KIS 수집 키의 `venues` 를 `config/markets.yaml kis.venues`(기본 `[KRX]`)와 맞췄고
  (`tests/unit/scheduler/test_registry_validation.py::test_kis_venues_match_markets_config`) 선점 장부에 NXT·TOTAL 키가 생기지
  않는다(P2 24시간 시뮬레이션의 거래소 단언도 이 설정을 따른다).
- 하루 운영 확장 시뮬레이션이 원장 전이를 실제 처리기로 본다: 장중 `kis.prelim`(estimated) → 마감 `kis`(ok, 덮기 전 값과
  차이는 `investor_revision`) → 다음 날 `krx`(ok). KRX 대조에서 어긋난 KIS 행은 `invalid` + `eod_reconcile` 행.
- 메인 결정 R2: 검산 ①(투자자 합 0)은 KIS 가 기타법인을 주지 않으면 실데이터에서 **불가** — 가능한 검산은 0 차이,
  불가한 검산은 `None` + 사유(quality·notes·`docs/probe_results.md` §7 #20)로 남긴다. 합성 원장에서는 ①②③ 모두 강제한다
  (`tests/property/test_ledger_checks.py`).
