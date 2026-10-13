# ADR 0027 — 종목↔HS 매핑표와 수출 겹쳐 보기(자체 큐레이션·근거 필수·공개/local 분리·검증 두 층) (2026-10-13)

상태: **채택**(2026-10-13 — 초안 P6 묶음 MAP, 번호·상태는 웨이브 3 WRAP 이 확정 — 아래 'WRAP 확정 메모').
근거: `docs/p6_design.md` §1.5·§4.5·§4.6·§5·§6·§7, D-P6-14, 부록 A §28, 사용자 결정 2026-10-13(매핑 초안은 `reviewed: false` 로
출시 — Q7, 원화 환산 없음 — Q8, 104품목은 자체 고정 HS4 — Q2), ADR 0011(공개 정적 사이트), DATA_TIERS §1(관세청 = 공개).
구현: `config/knowledge/trade_map.yaml`, `kbj/engines/trade/{mapping,overlay}.py`, `kbj/services/engine/{trade_map,verify_trade}.py`,
`kbj/services/api/readers/trade_map.py`, `kbj/services/public_export/trade_stock_json.py`, `web/src/pages/p8/trade_overlay{,_price}.ts`,
`web/src/pages/p7/stocks.ts`.

## 1. 맥락

- 관세청 품목(HS)·국가·시군구 수출은 공개 자료지만 "어느 상장사의 매출이 어느 HS 인가"는 어디에도 표로 없다. 사업보고서의
  매출 구성 문단을 사람이 읽고 정해야 한다.
- 관심종목은 개인 데이터다(`prv_journal.watchlist`). 공개 산출물에 관심종목 흔적이 남으면 안 된다(P4 수용 시험 ⑥).
- 관세청 조회코드표 원본·실집계는 main 브랜치·픽스처에 넣지 않는다(합성만 — 사용자 결정 2026-10-13).

## 2. 결정

1. **매핑표 = 자체 큐레이션 YAML**(`config/knowledge/trade_map.yaml` — 공개). 한 종목 = 품목 줄(HS 접두 2~10 짝수 자리·흐름·
   가중치·선택 국가 목록) + 지역 줄(통합 지역 ID × HS6) + **근거 1개 이상**(종류·자체 문장 요약·선택 DART 접수번호 14자리 —
   원문 복사 금지). 종목별 가중치 합 ≤ 1(정규화하지 않는다 — YoY 는 크기와 무관). 첫 판 61종목(반도체·자동차·타이어·조선·전력
   기기·전선·화장품·라면·방산·이차전지·석유화학·정유·철강·비철·전자부품·바이오·담배·건설기계)은 **업종 대표 HS4 와 초안 가중치**
   이고 전부 `reviewed: false` — 화면에 '검토 전' 꼬리표. 접수번호는 사람이 검토할 때 채운다 [확인 필요].
2. **공개·local 분리**: 관심종목 전용 매핑은 VM local 파일(`config/trade.yaml map.local_file`, 상대 경로는 `KBJ_DATA_DIR` 기준 —
   레포 밖)로, 같은 스키마. `trade.map_load` 가 `origin = public`·`local` 판을 **따로 통째로** 바꾼다. 설정이 없으면 local 판을
   비우고, 설정했는데 파일이 없으면 실패(조용히 비우지 않는다). 같은 종목이 둘 다에 있으면 **local 우선 — 그 종목의 공개 줄은
   통째로 버린다**(줄 단위로 섞으면 가중치 합 ≤ 1 이 깨진다) + 경고 건수. 공개 내보내기는 SQL `WHERE origin = 'public'` +
   읽은 행 검사 두 번, local 우선 병합은 로그인 API 만. 작업 기록에는 local 의 **건수만**(종목코드 0).
3. **검증 두 층**: ① CI(`tests/unit/config/test_trade_map.py`·`verify_trade --map-only`) — 해석기(형식·짝수 자리·가중치·중복·
   근거)와 파일 사이 대조(HS2 장이 `hs_chapters.yaml` 에, 지역 ID 가 `trade_regions.yaml` 에). `trade.map_load` 도 같은 대조를
   하고 하나라도 걸리면 **실패(쓰지 않는다 — 옛 판 유지)**. ② VM(`verify_trade --watchlist main --json`) — 종목코드가 상장사
   고유번호 표(`pub_filings.corp_code`)에, HS 접두가 수집된 `pub_trade.hs_dim` 에 있는가(그 접두로 시작하는 코드가 하나라도
   있으면 있음), 겹쳐 보기 상태별 수 — **건수만**(관심종목 코드·값 0) [실측 필요 #56].
4. **판 이름**: `v<version>-<해석된 내용 sha256 앞 10자리>`(local 은 `local-` 앞붙임) — 같은 내용이면 같은 판, 시계와 무관.
5. **종목 수출 지수 `E_t = Σ wᵢ·Xᵢ,t`**(metrics §28): 품목 줄 = 품목 월간 접두 값(가장 깊은 행만 더해 이중 합산 0 —
   `explore.ItemIndex`), 국가 목록이 있으면 품목×국가 표의 그 국가 합. **국가 표에 그 접두가 한 달도 없으면**(국가 표는 HS4
   까지라 더 깊은 접두) 전 국가 값으로 대신하고 `estimated` + 메모(국가 몫을 나눠 짐작하지 않는다). 지역 줄 = 시군구×HS6 의
   통합 지역 ID 값(개편 전후 합). 한 달에 자료 있는 줄 하나라도 값이 없으면 그 달 E = None(0 채움 금지). 자료가 한 달도
   없는 줄은 빼고 `estimated`, 모두 없으면 `no_trade`.
6. **겹쳐 보기**: 분기 = 달력 분기 세 달 합(하나라도 None → None), YoY = 전년 같은 분기 대비. 매출 YoY = `pub_fin.quarterly`
   매출 — **CFS 매출이 하나라도 있으면 CFS 만, 아니면 OFS**(기준을 섞지 않는다), 분기 이름은 분기말의 달력 분기(결산월 무관).
   종목 → 회사는 공개 고유번호 표만(우선주 등 표에 없는 종목은 `no_revenue` — 종류주식 표는 로그인 저장소라 공개 작업이 읽지
   않는다. 우선주 매핑은 보통주 코드로 적는다). 시차 상관 = 피어슨(E 분기 YoY 의 k 분기 전, 매출 YoY), k ∈ {0,1,2}, 짝이 8개
   미만이거나 분산 0 이면 None. 상태 우선순위 `no_trade > no_revenue > short_overlap(시차 0 짝 < 8 — 선은 그린다) > ok`.
7. **랭킹**: 지수가 있는 마지막 달까지 최근 3개월 E 합 / 전년 같은 달 합 − 1 내림차순(None 은 뒤, 동률 코드 순). 공개 패키지는
   엔진을 import 하지 않으므로(계약 ⑧) 같은 정의를 `trade_stock_json.py` 에 다시 적고 시험이 로그인 API 와 대조한다.
8. **저장**: `pub_trade.stock_export` 에 월 행(`YYYY-MM` — lag_corr 빈칸)과 분기 행(`YYYYQn` — 매출 YoY·lag_corr)을 쓴다.
   금액은 달러 정수(가중치 곱은 반올림), 시세 칸 없음.
9. **화면**: 페이지 8 '재무' 탭 '수출 겹쳐 보기'(공개) — 두 선·시차 상관·매핑 표·근거·'검토 전'·'환율 미조정(달러)'·CSV.
   매핑 없는 종목은 오류가 아니라 '매핑 없음 — 매핑표에 없는 종목'. 로그인 한 줄 '월말 종가'는 기존 `stock/<코드>/chart`
   (최대 1,240일)의 달마다 마지막 종가와 지수를 **처음 겹친 달 = 100** 으로 맞춰 겹친다(새 API 없음, 공개 번들에서 뺀다 —
   고리는 WRAP). 페이지 7 '품목' 탭 아래 매핑 종목 랭킹 표(로그인판은 local 종목에 '로그인 전용' 꼬리표).

## 3. 결과·한계

- 첫 판 가중치는 사람이 검토하기 전 초안이다 — 상관·랭킹은 '검토 전' 꼬리표와 함께만 보인다.
- `upsert` 라 매핑표에서 빠진 종목의 옛 `stock_export` 행은 남는다(리더·공개 내보내기는 현재 매핑 종목만 읽는다) —
  저장소에 `replace_stock_export` 가 생기면 바꾼다.
- 합성 세계(`p6_world`)의 매핑에는 심은 상태 no_revenue(994015)·short_overlap(992059) 종목이 없어 MAP 시험·픽스처가 그
  두 종목의 공개 줄을 덧붙인다(`tests/unit/api/web_fixtures_p6/map.py EXTRA`). 세계의 관심목록 50 중 매핑된 종목은 23개
  (공개 겹침 20 + local 3) — 수용 시험의 '매핑 50/50' 은 세계 확장(FND/WRAP) 뒤에 맞출 수 있다.

## WRAP 확정 메모(2026-10-13)

- 번호 0027 그대로 채택. 사용자 결정: 매핑 초안은 `reviewed: false` 로 출시(Q7), 원화 환산 없음(Q8), 104품목은 자체 고정
  HS4 목록(Q2).
- 국가 표 대체 규칙(§2-5 — 국가 목록이 있는데 품목×국가 표에 그 접두가 없으면 전 국가 값으로 대신하고 `estimated`)을
  `docs/metrics.md` §28 에 옮겼다.
- `trade.stock_index` 는 실행마다 `pub_trade.stock_export` 를 통째로 바꾼다(`TradeRepo.replace_stock_export` — 매핑표에서
  빠진 종목의 옛 행이 남지 않게, WRAP 이 저장소에 더했다).
- 페이지 8 고리: '재무' 탭에 공개 패널(`p8/trade_overlay.ts`)과 로그인 줄(`p8/trade_overlay_price.ts` — `LOGIN_TRADE`, 공개
  빌드에서는 번들에서 뺀다 — `check-bundle.mjs` 공개 금지 문자열 '월말 종가'). 개요 탭의 'P6 준비 중' 자리표시는 지웠다.
- 계약 ⑰(import-linter): 수출입 엔진·작업(`collectors.customs`·`engine.trade`·`engine.trade_map`)·공개 내보내기 trade
  모듈은 로그인 원천·로그인 저장소·`verify_trade` 를 import 하지 않는다. `verify_trade`(VM CLI — 관심종목을 읽는다)는 일부러
  계약 밖.
- 증명: `tests/acceptance/test_trade_map_watchlist50.py` — 합성 관심목록 50종목에 실제 처리기(map_load → customs_monthly
  (가짜 관세청) → derive → stock_index)를 돌린 뒤 `/api/trade/stock/{code}` × 50: 500 = 0, 매핑 50/50(공개 + VM local —
  공개 매핑에 없는 관심종목은 **local 매핑 파일**로 매핑한다, D-P6-14), 상태 행렬(no_trade·no_revenue·short_overlap·ok),
  NaN·Infinity 0, 공개 `trade/stock/*.json` 은 public 종목만·local 흔적 0. 웹은 `web/test/acceptance/p6_pages.test.ts`.
- 남은 것: 첫 판 61종목의 가중치·근거 검토(사용자), 실데이터 대조 #56.

