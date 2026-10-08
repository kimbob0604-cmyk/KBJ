# ADR 0016 — 밸류에이션 시점 일관(PIT)·회사 시총·EV 정의, 시나리오 적정가(구 TAM), 관심종목 원천과 20종목 완료 기준 (2026-10-07)

상태: **제안**(P4 설계 초안 — 묶음 V·A·S 가 구현하며 고치고, 묶음 S 가 확정한다). 'TAM' 이름 바꿈은 **[결정 필요 — U-P4-4]**.
근거: `docs/p4_design.md` D-P4-11·12·13·16, §4.8·§4.9·§5.2·§8.4, 부록 A §11·§15, SD `valuation_calculator.py`(`get_ttm_metrics`:82,
`calculate_ev_ebitda_band`:194, `percentile_rank`:334), SD `tam_modeler.py`, SD `server.py:api_watchlist_sync`:5821, `docs/DATA_TIERS.md` §2.
구현 예정: `kbj/engines/valuation/**`, `kbj/services/engine/valuation.py`, `kbj/services/api/{routes,readers}/stock.py`,
`kbj/services/watchlist/**`, 마이그레이션 `0012_fin_prv.sql`·`0013_journal_watchlist.sql`, `tests/acceptance/test_stock_detail_watchlist20.py`.

## 1. 맥락

- SD 밸류에이션 밴드는 분기말 종가 × (오늘의) 발행주식수 ÷ 그 분기로 끝나는 TTM 이다 — 분기말에는 아직 공시되지 않은 실적을
  쓰고(미래 참조), 주식 수는 네이버 재무비율 표(`financial`)에서 왔다(U4). EV 에 오늘의 오버행 가치(잠재 주식 × 전환가)를
  모든 과거 분기에 더했고, CB 는 이미 사채(순차입금)에 있어 **이중 계산**이다. 백분위는 조각 선형 근사다.
- SD 'TAM'(`tam_modeler.py`)은 시장 규모가 아니라 EPS × PER 목표가 시나리오다.
- PLAN P4 완료 기준 "종목 상세가 관심 종목 20개에서 오류 없이"는 '오류'의 정의가 없다. 관심종목은 SD 브라우저 localStorage 를
  서버 파일(`cache/server_watchlist.json`)로 동기화한 것뿐이다.

## 2. 결정

1. **PIT**: 날짜 t 의 TTM·자본은 **t 까지 공시된**(정기보고서 접수일 ≤ t) 마지막 연속 4개 분기·최근 분기말만 쓴다. 잠정실적은
   쓰지 않는다. 일별 시계열(`prv_fin.valuation_daily`)을 5년(1,240거래일) 쌓고, 밴드는 유효 값의 p10·25·50·75·90(선형 보간),
   현재 백분위는 경험적 순위(100 × #{x ≤ 현재} ÷ n). 유효 일 < 250 이면 밴드 없음(`short_history`).
2. **회사 시가총액** = 상장된 보통주 + 우선주(종가 × 상장주식수, 원장 krx > kis). 우선주 페이지도 회사 배수를 보인다.
   자기주식 차감 안 함 [확인 필요].
3. **PER 분모는 지배주주 순이익**(없으면 당기순이익 + estimated). **EV = 시총 + 순차입금 + 비지배지분**, 오버행 가치는 더하지 않는다.
   EBITDA 는 영업이익 + 감가상각비 + 무형자산상각비(없으면 None — 영업이익으로 바꾸지 않음). 분모 ≤ 0·업종 부적합은 None +
   `na_reason`.
4. **시나리오 적정가(구 TAM)**: Base = (FY1 추정 EPS → TTM EPS) × PER p50, Bear = EPS × 0.7 × p25, Bull = (컨센 최고 → Base EPS × 1.2)
   × (피어 평균 × 1.1 → p75), PBR 폴백, 근거 문자열 필수. 시장 규모 TAM 은 만들지 않는다.
5. **관심종목 원천** = `prv_journal.watchlist`(로그인·개인). P4 는 읽기 API + 가져오기 CLI(SD JSON·CSV)만, 쓰기는 P8. 공개판·
   공개 JSON 에는 관심종목의 흔적이 없다.
6. **'오류 없이'의 정의**: 페이지 8 라우트 × 관심종목 각각에서 HTTP 500 = 0, 모든 200 이 봉투 검증(source·as_of·quality) 통과,
   섹션 상태가 기대 행렬(200 / 200 + `applicable=false`·`na_reason` / 404 `no_data`)과 같고, 화면 DOM 에 `NaN`·`undefined`·`null`·
   `Infinity` 0, `console.error` 0, 한 패널 실패가 다른 패널을 멈추지 않는다. 증명은 합성 20종목(신규상장·거래정지·우선주·스팩·
   리츠·비12월 결산·적자·재무 미제출·KONEX·금융업·OFS·자본잠식·오버행·내부자·소형·분할·영숫자 코드·외국기업·정리매매)과, 키 뒤
   실 관심종목(`verify_stock` — 값은 기록하지 않음).

## 3. 결과

- legacy 화면과 값이 다르다(밴드 위치·백분위·EV/EBITDA). 차이 표를 페이지 도움말과 `docs/metrics.md` §11 에 둔다.
- 신규상장·자본잠식·적자·스팩·금융업에서 '0 으로 채운 배수'가 사라지고 사유가 보인다.
- 밸류 일별 시계열은 KRX 일별 백필(P3 R11)이 끝난 뒤에만 5년이 찬다 — 그전에는 `short_history`.
- 남은 [확인 필요]: 자기주식 차감, 리스부채의 순차입금 포함, 외국기업 통화, 금융업 KSIC 경계, 우선주→보통주 매핑 예외.
