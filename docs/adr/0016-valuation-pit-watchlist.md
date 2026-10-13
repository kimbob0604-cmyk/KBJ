# ADR 0016 — 밸류에이션 시점 일관(PIT)·회사 시총·EV 정의, 시나리오 적정가(구 TAM), 관심종목 원천과 20종목 완료 기준 (2026-10-07)

상태: **확정**(2026-10-13 — 초안 P4 묶음 V, 구현 V·A·S, 번호·상태는 웨이브 3 묶음 S 가 확정 — 아래 'S 확정 메모'). **2026-10-08 메인 결정으로 채택된 항목**: PIT 밴드(legacy 와 값이 달라지는 것은 §3 표), 'TAM' → '시나리오 적정가'(U-P4-4), 관심종목 원천(SD `server_watchlist.json` 은 사용자가 VM 에 올림 — 레포에는 합성 20종목만, U-P4-5).
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
   공개 JSON 에는 관심종목의 흔적이 없다. SD `cache/server_watchlist.json` 은 **사용자가 VM 에 올린다 — 레포에 넣지 않는다**
   (`.gitignore` `/cache/`). 시험·완료 증명은 합성 20종목 세계(`tests/fixtures/synthetic/p4_world.py`)뿐.
6. **'오류 없이'의 정의**: 페이지 8 라우트 × 관심종목 각각에서 HTTP 500 = 0, 모든 200 이 봉투 검증(source·as_of·quality) 통과,
   섹션 상태가 기대 행렬(200 / 200 + `applicable=false`·`na_reason` / 404 `no_data`)과 같고, 화면 DOM 에 `NaN`·`undefined`·`null`·
   `Infinity` 0, `console.error` 0, 한 패널 실패가 다른 패널을 멈추지 않는다. 증명은 합성 20종목(신규상장·거래정지·우선주·스팩·
   리츠·비12월 결산·적자·재무 미제출·KONEX·금융업·OFS·자본잠식·오버행·내부자·소형·분할·영숫자 코드·외국기업·정리매매)과, 키 뒤
   실 관심종목(`verify_stock` — 값은 기록하지 않음).

## 3. 결과

- legacy 화면과 값이 다르다(밴드 위치·백분위·EV/EBITDA). 차이 표를 페이지 도움말과 `docs/metrics.md` §11 에 둔다.
  **legacy 대비 차이 표**(SD `valuation_calculator.py` — 2026-10-08 원본 확인. 방향은 정성 — 실데이터 크기는 키 뒤 #36·#37 에서):

  | 항목 | legacy SD | KBJ | 값이 달라지는 곳 |
  |---|---|---|---|
  | TTM 시점 | 분기말 날짜에 그 분기로 끝나는 TTM(`calculate_per_band`:122 — 아직 공시 안 된 실적을 씀) | 정기보고서 접수일 ≤ t 인 마지막 연속 4분기 | 분기말~접수일(최대 45·90일) 구간은 한 분기 앞 TTM — 실적이 크게 바뀐 종목의 그 구간 배수 |
  | TTM 결측 | 4행이 있으면 `None` 을 0 으로 합산(`get_ttm_metrics`:111 `or 0`) | 하나라도 None 이면 TTM None | 결측 분기가 있던 종목의 배수가 사라지거나(KBJ) 부풀었던 값(legacy)이 빠짐 |
  | 표본 | 분기말 점, 8분기 이상(:134) | 매 거래일 5년(1,240일), 유효 250일 이상 | p10·p90 폭, 신규상장 밴드 유무 |
  | 주식 수 | 오늘의 발행주식수(`financial_quarterly` → 네이버 `financial` 폴백 — `get_shares_outstanding`:52) × 과거 가격 | 그날 상장주식수 × 그날 종가(KRX 원장), 보통주 + 우선주 | 증자·분할·소각 종목의 과거 시총, 우선주 있는 회사의 배수 |
  | PER 분모 | 당기순이익(`net_income`) | 지배주주 순이익(없으면 당기순이익 + estimated) | 비지배지분이 큰 지주·자회사 보유 회사 |
  | EV | 시총 + 순차입금 + 오늘의 오버행 가치(모든 과거 분기에 — `calculate_ev_ebitda_band`:194) | 시총 + 순차입금 + 비지배지분, 오버행 제외 | CB·BW 발행사의 EV/EBITDA(이중 계산·미래 참조 제거로 낮아짐) |
  | 현재 백분위 | 조각 선형 근사(`percentile_rank`:334) | 경험적 순위 100 × #{x ≤ 현재} ÷ n | 몇 %p |

- 신규상장·자본잠식·적자·스팩·금융업에서 '0 으로 채운 배수'가 사라지고 사유가 보인다.
- 밸류 일별 시계열은 KRX 일별 백필(P3 R11)이 끝난 뒤에만 5년이 찬다 — 그전에는 `short_history`.
- 남은 [확인 필요]: 자기주식 차감, 리스부채의 순차입금 포함, 외국기업 통화, 금융업 KSIC 경계, 우선주→보통주 매핑 예외.

## 4. 구현 메모 (묶음 V — 2026-10-08, 초안 — S 가 확정)

코드: `kbj/engines/valuation/{multiples,pit,band,percentile,scenario,classes}.py`(순수 — 계약 ⑨),
`kbj/services/engine/valuation.py`(`fin.valuation_band` 처리기 `band`·재계산 `rebuild`). 시험:
`tests/unit/engines/valuation/**`(합성 20종목 세계에서 p4_expect.yaml 의 valuation·scenario 칸 대조 포함),
`tests/property/test_valuation_pit.py`(미래 참조 0·분할 불변).

1. **정본 재사용(두 벌 금지)**: TTM 창은 재무 엔진 `kbj.engines.financials.period.ttm_window`(R), NTM 은
   컨센서스 엔진 `kbj.engines.consensus.forward.ntm`(K), 컨센서스 대상 사유는 `consensus.universe.consensus_na`,
   우선주 이름 규칙은 `kbj.engines.board.kinds.common_name`, 백분위는 순수 함수(numpy 'linear' 와 같은 값).
2. **사유 하나·우선순위**(docs/metrics.md §11 끝 [제안] 그대로 구현 — `multiples.NA_PRIORITY`):
   currency_mismatch > no_filings > spac > financial_sector > not_detail_tier > short_history >
   loss·capital_impairment·no_revenue > missing_input. 컨센서스 사유(konex_no_consensus·
   not_in_consensus_universe)는 forward 칸에만. 판정 근거:
   - no_filings = 그날까지 알려진 정기보고서 분기가 하나도 없다, currency_mismatch = 알려진 마지막 분기의 통화 ≠ KRW.
   - not_detail_tier = TTM(없으면 최근 분기) 행에 주요계정 층(`tier='main'`)이 섞였다 — 상세 대상 목록이 아니라 **데이터 층**으로
     본다(PIT 와 같은 기준).
   - TTM 이 없을 때: 상장 뒤 처음 알려진 분기가 4개 미만 → short_history(신규상장), 그 밖(반기·연간 보고만 — 리츠·코넥스) →
     missing_input.
   - EV/EBITDA: TTM EBITDA ≤ 0 → loss(EV 유무보다 먼저), EBITDA 없음·EV 없음 → missing_input.
   - 밴드 사유: 유효 일 < 250 이면 {as_of 행의 그 칸 사유, (창의 행 < 250 이면) short_history} 중 순위 앞의 것, 둘 다 없으면
     창에서 가장 많은 사유, 그마저 없으면(거름값 밖뿐) short_history.
3. **회사 시총**: 스냅의 `mktcap`(KRX 원장)을 쓰고 없으면 종가 × 상장주식수. 우선주가 빠지면 빼고 estimated + notes.
   **보통주가 빠지면 회사 시총을 만들지 않는다**(그날 우선주 행은 missing_input + notes) — 우선주만의 합으로 배수를 내면 몇 배
   틀린 값이 밴드 표본에 들어간다(2026-10-08 독립 검증에서 고침, §4.8 이 빼도 된다고 한 것은 우선주뿐).
4. **EV**: 비지배지분 줄이 없는 연결 재무(R 정규화가 None 으로 둔 칸)는 0 으로 보고 estimated + notes [확인 필요 — 실측 #35].
5. **주당 값은 그날의 주식 수로**(§4.8 분할·병합): 분모 = DART 주식총수(보통주 발행 − 자기주식, PIT)가 그날 KRX 보통주
   상장주식수와 30% 안이면 그것, 아니면 KRX 상장주식수(estimated). 보고된 주당 값(분기 기본주당이익 합·KIS 추정 EPS)이
   함의하는 주식 수가 그 분모와 30% 넘게 다르면(분할 직후 — 분할 전 EPS 가 남음) 총액 ÷ 분모로 다시 세고 근거 문자열을
   `TTM_NI_PER_SHARE`·`CONSENSUS_NI_PER_SHARE` 로 적는다(합성 #17 액면분할 5:1 — 이 규칙 없이는 업사이드가 +500%).
   주요계정 층(기본주당이익 없음)의 TTM EPS 도 `TTM_NI_PER_SHARE`.
6. **forward**: 품질은 언제나 estimated(KIS 단일 추정 [확인 필요 #29]), PER_fwd 분모 = NTM 지배순익(없으면 NTM EPS × 분모).
   추정이 없는 것(수집 꺼짐·KIS 빈 응답)은 사유 없는 None('자료 없음' — '적용 불가'와 구분).
7. **밴드·시나리오는 그날 행이 있는 전 종목**(주요계정 층 포함) — 등록부 노트('상세 대상만')와 다르다. p4_expect #16 이
   밴드·시나리오를 기대하고, 계산은 종목당 일별 행 1,240줄 읽기라 가볍다 [메인에 노트 수정 요청].
8. **우선주 시나리오 = 회사(보통주) 기준**: EPS·BPS·밴드는 회사 것, 가격·업사이드는 보통주 종가. 우선주 배수 행은 회사 배수
   (보통주 행과 같은 값), 가격 칸만 그 종목 종가.
9. **종류주식 표**(`prv_market.share_class`)는 이 작업이 매일 쓴다 — 우선주 짝은 코드(끝자리 → 0)·이름(접미 제거) 둘 다 맞을
   때만, 못 맞추면 그 우선주의 배수 칸은 missing_input + notes(R13).
10. **재계산**: 일별 행 `flags.fin_digest` = 그날까지 알려진 재무(분기·주식총수 값 칸)의 지문. 다음 실행에서 마지막 행의 지문이
    지금 저장소로 만든 지문과 다르면(백필·정정으로 과거 재무가 바뀜) 그 종목은 5년 창 전부를 다시 계산한다. 새 공시(접수일 >
    마지막 날)는 지문을 바꾸지 않는다. 수동 재계산은 `rebuild(repos, codes, as_of, …)` 와 그 CLI
    `python -m kbj.services.engine.valuation rebuild --as-of <전 거래일> [--code C …]`(건수만 출력 — 값·종목별 결과 없음,
    실패한 종목만 앞 5건의 코드·가린 사유. 종료 코드 0 ok·1 not_ready/failed·2 저장소 오류). 처리기 `detail` 도 같다(종목
    하나의 실패는 사유를 남기고 계속 — P4 `filings`·`earnings` 처리기와 같은 방식). KRX 일별 백필로 시세가 더 과거로 늘어난 경우는 지문이 잡지 못한다 —
    백필 뒤 이 CLI 를 한 번 돌린다 [S·메인: 운영 절차(`docs/PLAN.md`·백필 안내)에 한 줄].
11. **p4_expect 와 다른 칸 하나**: #8 994070 시나리오 — FY1 지배 EPS 추정이 11-13 에 양수(합성 컨센서스의 부호 전환이 70일에
    끝남)라 §4.9 표대로 Base = CONSENSUS → PER 밴드 → `ok:PER`(기대 행렬은 `ok:PBR`). 규칙을 바꾸지 않고 M 에게 기대 칸 수정을
    요청했다(시험 `KNOWN_DIFF`).
12. **[확인 필요 — S 가 metrics §11·§15 확정 때 결정] 시나리오 주당 값과 회사 배수의 기준 어긋남**: 배수는 회사 시총(보통 +
    우선, 자기주식 포함 — §4.8 '자기주식 차감 안 함')으로 세는데, 시나리오의 BPS·`*_NI_PER_SHARE` 분모는 보통주 발행 − 자기주식,
    TTM EPS 는 DART 기본주당이익(자기주식 뺀 가중평균)이다. 그래서 **현재 배수를 그대로 곱해도 업사이드가 0 이 아니다**: 합성
    #1(우선주 6천만·자기주식 560만/보통주 4억) PBR 폴백·`*_NI_PER_SHARE` 경로 +14.3%(TTM_EPS 경로 −0.9%), 우선주 없이 자기주식
    20% 인 회사는 모든 경로 약 +25%. 밴드가(`band.band_lines`)는 주당 기준값 = 종가 ÷ 그날 배수라 어긋나지 않는다. 고치는 길(택
    하나): ① 시나리오 주당 값 = 총액 × 보통주 종가 ÷ 회사 시총(= 밴드가와 같은 기준 — 분할 규칙 5 도 필요 없어진다), ② 회사
    시총에서 자기주식을 빼고 우선주는 따로. 규칙(§4.9·metrics §9 BPS)을 바꾸는 일이라 이 묶음은 고치지 않았다.

## 5. S 확정 메모 (웨이브 3, 2026-10-13)

1. 번호 0016·상태 **확정**. 등록부에서 `fin.valuation_band` 를 켰다(08:50, `krx.daily` 굳은 의존 — 하루 시뮬레이션이 `krx.daily`
   끝난 뒤 시작을 확인). 노트는 '밴드·시나리오는 그날 행이 있는 전 종목'(§4-7). §4 의 구현 규칙(주당 값 분모·분할 직후 보고 EPS·
   비지배지분 0·회사 시총의 보통주 결측)과 사유 우선순위는 `docs/metrics.md` §11 에 옮겨 확정했다. 운영 절차(KRX 일별 백필 뒤
   `python -m kbj.services.engine.valuation rebuild --as-of <전 거래일>` 한 번)는 `docs/PLAN.md` P4 결과에 적었다.
2. **'오류 없이'(2-6) 증명 — PLAN §8 P4 완료 기준 ②**: `tests/acceptance/test_stock_detail_watchlist20.py` — 합성 20종목 세계에
   P4 **실제 처리기**를 등록부 cron 대로(2026-11-12 22:00 씨앗 → 11-13 00:00 ~ 11-16 21:30) 돌린 뒤 페이지 8 라우트 14개 × 20종목
   (280칸): 500 = 0, 모든 200 봉투 검증, 섹션 상태 = `p4_expect.yaml` 행렬, NaN·Infinity 0, 처리기·라우트 traceback 0, 공개
   내보내기(`fin/<코드>.json` — 공개 안전 검사·출처 DART·관심종목 흔적 0), 손계산 칸(#14 오버행·#15 내부자), 그날 알림. 실데이터는
   같은 판정을 하는 CLI `python -m kbj.services.engine.verify_stock --watchlist main`(건수만 출력 — 값·종목코드 없음, 체크리스트
   #37) — 시험 `tests/acceptance/test_verify_stock.py` 가 같은 세계에서 실패 칸 0 을 본다. 프런트 쪽(`console.error` 0·DOM 의
   `NaN`·`undefined`·`null`·`Infinity` 0·패널 격리)은 묶음 W2 의 `web/test/acceptance/p8_watchlist20.test.ts`.
3. **알려진 결함 S-R1(완료 시험이 찾았다 — 280칸 중 27칸 + 뿌리·리비전 알림 시험 2개, 모두 xfail(strict)로 고정, 요청)**: P3 `krx.daily` 유니버스의 `kind=None` 을 P4
   소비자(컨센서스 대상·밸류 `classes`·서비스의 스팩·우선주·API `readers.stock.ref`)가 분류하지 않아, 처리기를 거친 세계에서 컨센서스
   대상 0(#1·#3·#4·… `consensus` 칸), 우선주 #2 의 회사 섹션 404, 스팩 #5 의 PER 적용이 된다. 500 은 아니고(섹션 격리 — 404·200),
   단위 시험·하루 시뮬레이션은 참값 종류를 넣어 가렸다. 고치면 그 칸들이 XPASS 로 실패하니 표에서 지운다. **이 결함을 고치기 전에는
   실데이터 수용(#37)에서 같은 칸이 404·적용 불가로 나온다.**
4. **[결정 필요 — 메인] 정정 보고서의 PIT 접수일**(R 의 질문): 정정 보고서는 원시 재무를 덮고 그 분기의 접수일이 정정일로 밀린다
   → 원 공시일~정정일 사이 그 분기를 포함한 TTM 이 None(밴드에 구멍). 원 공시일을 쓰면 정정된 값을 정정 전 날짜에 쓰는 미래 참조다.
   [권고] P4 는 엄격 PIT(구멍 — 보수적) 그대로, 정정 전 원시 행을 따로 남기는 저장(새 마이그레이션)은 실측 #35(정정 빈도) 뒤에.
5. **[결정 필요 — 메인·사용자] 시나리오 주당 값과 회사 배수의 기준 어긋남**(§4-12): `docs/metrics.md` §15 ⚠ 에 옮겼다. [권고] ①
   시나리오 주당 값 = 총액 × 보통주 종가 ÷ 회사 시총(밴드가와 같은 기준). 정해지면 V 가 `scenario`·시험을 고친다.
6. `p4_expect.yaml` #8 994070 의 시나리오 기대를 `ok:PER` 로 고쳤고(§4-11 — metrics §15 Base 규칙), A·V 시험의 `KNOWN_DIFF` 를
   지웠다. API 쪽 사실(A): API 는 작업이 저장한 `na_reason` 을 그대로 싣고, 밸류 행이 없을 때만 회사 단위 사유(`currency_mismatch`·
   `no_filings`)를 정한다 — 그 밖은 404 `no_data`('fin.valuation_band 08:50').
7. 남은 요청: API `readers.stock` 의 `_na_of`·`_cell` 이 엔진 `multiples.cells_of` 와 같은 일을 한다(두 벌 — A), 우선주 시나리오의
   '보통주 기준' 화면 표기(W2), 컨센서스 스냅 5년 창 읽기 성능(키 뒤 Pg 실측 — 넘으면 '그날 이하 마지막 스냅' 저장소 메서드, M).
   남은 [확인 필요]: 자기주식 차감, 리스부채, 외국기업 통화, 금융업 KSIC 경계, 우선주 매핑 예외.

