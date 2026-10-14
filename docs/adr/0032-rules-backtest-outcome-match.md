# ADR 0032 — 규칙 백테스트·알림 후 성과·일치 감시: 같은 함수·anchor·d+1 체결·사유 분류 (2026-10-14)

상태: **확정**(2026-10-14 P8 WRAP — P8 묶음 BKT 초안, 끝 'WRAP 확정 메모'). 설계서의 'ADR B'(docs/p8_design.md §9.2). 번호: P7 이 0029·0030 을 썼고
ADR A(EVL — 규칙 알림)가 0031 이라 0032.
근거: docs/p8_design.md D-P8-6·7·12·13·14·17, §4.5·§4.6, 사용자 결정(2026-10-13 — 장중 수급 라이브), metrics §30.4,
ADR 0018(마감 뒤 발송은 16:00 — 성과·백테스트 작업은 발송하지 않는다).

## 1. 맥락

완료 기준은 "백테스트와 실시간이 같은 신호를 낸다"와 "알림 후 성과 표가 자동으로 쌓인다"다. legacy 에는 규칙 백테스트가
없고(SD `api_backtest` 는 발굴 스냅샷 태그 백테스트), 지표 정의는 SD·ET 가 서로 달랐다(EMA 시드·RSI 0 채움). 두 계산을
따로 만들면 임계값 근처에서 신호가 갈라지고, 갈라진 이유(데이터가 바뀌었나, 코드가 틀렸나)를 알 수 없다.

## 2. 결정

1. **같은 함수(D-P8-6)**: 백테스트(`kbj.engines.rules.backtest.run_backtest`)는 실시간과 같은 `FrameInputs`→
   `build_frame`→`evaluate(rule, frame, i)`→`CooldownBook` 을 시점마다 부른다. 벡터화 판은 없다. 마감 신호의 평가 시각은
   실시간과 같은 `live.close_eval_ts(d)`(그날 15:30 KST), 장중은 슬롯 시각 — 쿨다운·일치 감시 키가 같다. `both` 규칙은
   두 모드의 사건을 시각 순으로 섞어 **하나의** 상태기로 판정한다.
2. **입력 고르기 하나(서비스)**: `kbj.services.engine.rules_perf.frame_inputs`·`code_data` 가 야간 작업과 즉석 API 의
   유일한 입력 고르기다(정본 캘린더 칸, `MarketRepo.series`, 확정 수급 → `build_ledger`, board `split_guard` 문턱, 보유
   시작일 `live.holdings`, 신고가 라벨). 실시간 `kbj.services.engine.rules.load_frames` 와 같은 프레임을 낸다(시험
   `test_rules_perf.py` 가 날짜·종가·분할 칸·입력 지문을 대조). 이 모듈은 어댑터를 import 하지 않는다(API 가 부른다 —
   계약 ⑩). [제안 — WRAP] 둘을 한 모듈로 합친다.
3. **anchor(D-P8-7)**: 지표는 `max(config/rules.yaml anchor_date, 첫 봉)` 부터 한 번에 계산 — 실시간·백테스트·야간이
   같은 시작점. [확인 필요] anchor 2020-01-02.
4. **체결(D-P8-13, metrics §30.4)**: 마감 신호 d → d+1 첫 유효 시가(진입 칸 장중 고저도 손절·익절에 노출), 장중 신호 →
   슬롯 현재가(그날 슬롯 뒤 고저는 모르므로 다음 거래일부터 손절·익절). 같은 칸에 둘 다 닿으면 손절. 보유 N 유효 칸 뒤
   종가, 자료가 모자라면 `open_end`. 비용 = 수수료 양쪽 1.5bp + 매도 거래세 0.20% **[확인 필요 — 시행 연도 세율]** +
   슬리피지 0(`kbj.engines.backtest.costs` — FND 공용). 거래는 신호마다 하나(동시 보유 허용), 곡선은 청산일 순 거래 단위
   복리(표시용 — MDD 와 같은 정의).
5. **미래참조 없음**: 칸 d 의 판정·쿨다운은 d 뒤 봉에 무관하다 — 속성 시험 `tests/property/test_backtest_props.py`
   (뒤 칸을 아무렇게나 바꿔도 앞 칸 신호·상태·진입가·입력 지문이 같다). 비용 0 이면 체결 대칭·가격 척도 불변.
6. **알림 후 성과(D-P8-12)**: `kbj.engines.rules.outcome.signal_outcomes` 하나를 실제 신호(`rules.outcome` 08:30 —
   KRX 확정 일봉 뒤, 허락된 신호 sent·suppressed·dry)와 백테스트 신호('백테스트 기대' 칸)가 같이 쓴다. 기간 1·3·5·10·20
   거래일, 시장별 지수 초과, MFE/MAE. 만기 전 칸은 행을 만들지 않고, 정지·상폐·분할 의심은 사유를 적고 통계에서 뺀다
   (뺀 수를 보인다). 확정 값으로 매일 다시 계산해 덮는다(멱등). 통계(`kbj.engines.rules.stats`)는 읽을 때 계산 — 단위 %,
   n < 20 '표본 적음', 문구 "과거 신호의 사후 수익 — 비용·체결 미반영".
7. **일치 감시(D-P8-14)**: `rules.backtest_nightly`(08:45)가 켜진 규칙 판마다 3년(마감 모드 — 장중 재생은 저장 슬롯이
   있는 최근 60 거래일 [제안 — 속도])을 다시 돌려 `backtest_run`·`backtest_signal` 에, 최근 20 거래일을 실시간 신호와
   비교해 `match_check` 에 쓴다. 비교는 **규칙이 참이었던 시점**(실시간 sent·suppressed·dry·cooldown / 백테스트 dry·
   cooldown — 쿨다운 상태는 창 앞 이력에 따라 달라 비교하지 않는다). 키: 마감 = (종목, 거래일), 장중 = (종목, 슬롯).
   사유: 두 입력 지문을 알면 같음 → `logic`(버그 — health 오류, 실행 failed), 다름 → `data_revised`. 실시간 지문이 없는
   칸(실시간이 거짓이라 행이 없다)은 그 칸 입력이 실시간 평가 뒤 바뀌었을 수 있으면(마감 — 그날 봉이 지금 KRX 확정)
   `data_revised`, 아니면(장중 — 저장된 슬롯·가집계) `logic`. 장중 잠정 수급 → 확정은 지문이 달라 `data_revised`
   (사용자 결정 2026-10-13).
8. **즉석 백테스트(D-P8-17)**: `POST /api/backtest` 는 저장하지 않는다 — 같은 입력 고르기·같은 엔진, 상한(종목 100·5년
   → 413, 20초 → 503), Redis `rules:bt:<요청 해시>:<원장 기준일>` 1시간. 규칙은 `rule_id`(+판) 또는 `dsl` 하나, DSL
   오류 422 는 위치·사유만. POST 라 `WriteSession`(CSRF·본문 상한).

## 3. 결과

- 실시간과 백테스트가 다르면 이유가 표에 남는다(`data_revised`/`logic`). `logic` 은 운영 중 버그 신호다.
- 표본은 원장 보유 구간(상장폐지 종목은 원장에 있으면 포함 — 생존편향 문구). 수정주가는 만들지 않고 분할 의심 칸을
  건너뛴다(D-P8-8).
- 남은 일(WRAP): 실시간·백테스트 입력 고르기 한 모듈로 합치기, 웹 쓰기 채널(데이터 소스 `post` — CSRF)로 페이지 12 즉석
  실행 켜기, 완료 시험 `tests/acceptance/test_rules_parity.py`(실시간 작업 대 백테스트), 작업 켜기·계약.

## 4. WRAP 확정 메모(2026-10-14)

- 켜기: `rules.outcome`(08:30)·`rules.backtest_nightly`(08:45) 켰다(원장만, 발송 없음 — ADR 0018 영향 없음).
- 입력 고르기 하나(§2 의 [제안] 채택): `frame_inputs` 는 `kbj.services.engine.rules_input` 에 하나 — 실시간 `load_frames`·
  백테스트·야간 감시·즉석 백테스트 API 가 같은 함수로 고른다(`rules_perf` 는 다시 내보내기만).
- 비용: 매도세 0.20% 는 **[확인 필요]** 그대로(`config/rules.yaml` — 시행 연도 세율 확인 뒤 바꾼다). 장중 재생 창 60
  거래일·anchor 2020-01-02 도 [제안]/[확인 필요] 그대로.
- 즉석 백테스트(페이지 12)는 로그인 데이터 소스 쓰기 채널(`post` — CSRF)로 켜졌다.
- 완료 증명: `tests/acceptance/test_rules_parity.py`·`tests/sim/test_rules_days.py`(ADR 0031 §5 와 같은 시험).
