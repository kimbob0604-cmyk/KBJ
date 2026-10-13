# ADR 0021 — 매크로 출처·등급·수집 모듈 분리, 경제 캘린더(공식 일정 + 발표 결과 + 예상치), 상단 띠 P5 칩 (2026-10-13)

상태: **채택**(2026-10-13 — 초안 P5 묶음 MAC, 번호·상태는 웨이브 3 WRAP 이 확정 — 아래 'WRAP 확정 메모').
근거: `docs/p5_design.md` D-P5-5·6·7·8·20, §1.2·§2·§3·§4.1·§5·§6·§7, 부록 A §16, `docs/inventory.md`(bok 8탭·아침 매크로 — 코드 없음,
U3), SD `server.py:api_macro`·`api_global_macro`·`_MACRO_TICKERS`, `docs/DATA_TIERS.md` §1, 사용자 결정 2026-10-13(Q2·Q3·Q8).
구현: `kbj/data/public/{fred,treasury,nyfed}/**`, `kbj/data/private/{fred_restricted,forexfactory}.py`,
`kbj/services/collectors/{macro,macro_prv,kofia}.py`, `kbj/engines/macro/{derive,calendar,ribbon}.py`,
`kbj/services/api/readers/{macro,market}.py`(market 은 띠 부분만), `kbj/services/public_export/{macro_json,credit_json}.py`,
`web/src/pages/{p6_macro.ts,p6/**,p5/calendar.ts}`·`web/src/app/ribbon.ts`.

## 1. 맥락

- bok(제3자 rsm0kk — 레포에 코드 없음)는 ECOS 8계열·FRED 74계열·재무부·뉴욕연은·Yahoo·ForexFactory 를 한 대시보드(8탭)와 아침
  텔레그램에 섞었다. 출처마다 등급이 다르다: 한국은행 작성 표·미국 정부 시리즈는 공개 가능, ECOS 의 타기관 작성 표(한국거래소·
  서울외국환중개·금투협)·FRED 가 다시 내보내는 저작권 시리즈(S&P·CBOE·ICE BofA)·Yahoo·ForexFactory 는 아니다.
- SD 글로벌매크로는 12종 Yahoo 심볼 + 네이버 지수 + CNN 공포탐욕 스크랩 + PCR + 30분 파일 캐시였다.

## 2. 결정

1. **등급은 데이터셋 단위**(D-P5-6): `FRED:gov_series`(정부 시리즈 — `config/macro.yaml` 목록)는 공개, 같은 FRED 키라도 저작권
   시리즈는 `FRED:restricted_series`(로그인). 공개 어댑터 `FredClient` 는 정부 목록 밖 시리즈를 `TierError` 로 거절하고, 로그인
   `FredRestrictedClient` 는 공개 전송층(`FredTransport`)을 재사용하되 저작권 목록 밖을 거절한다(ECOS `require_public`·
   `require_restricted` 와 같은 모양). 미 재무부 수익률곡선·뉴욕연은 SOFR·EFFR 은 **공개**(사용자 결정 Q8 — DATA_TIERS §1 행 추가는
   WRAP·문서 몫).
2. **수집 모듈 분리**(D-P5-7): 공개 작업 `collectors.macro`(→ `pub_macro`)는 로그인 원천·저장소·P3 도우미(`_p3`)를 import 하지
   않는다. 로그인 작업 `collectors.macro_prv`(→ `prv_macro`)가 공개 모듈의 도우미(`run_keys`·`write_series` …)를 가져다 쓴다(로그인 →
   공개 방향만). 금투협 통계 `collectors.kofia`(→ `pub_market_stats`)는 공개. 계약 ⑭ 를 import-linter 에 넣는 일은 WRAP.
3. **실측 전에는 부르지 않는다**: `config/macro.yaml` 의 `item` 이 null 인 시리즈(ECOS·KOSIS 항목 코드 #43·#44, 재무부 만기 열·
   뉴욕연은 금리 코드 #45)는 수집하지 않고 그 키를 `skipped`(받은 척하지 않는다). item 이 있어도 단위·저작권·공표 시각을 실응답으로
   보기 전이면 `probe`(#45 FRED·#46 Yahoo)를 붙여 화면에 보인다. 공표 전(그 기간 값 없음)은 `not_ready`(받은 만큼은 쓴다), 값이 없는
   칸은 None + invalid(0 금지). `released_at` 은 출처가 주지 않아 **처음 본 시각**(같은 값이면 처음 시각을 지킨다)[제안].
4. **파생**(metrics §16): 엔진 `kbj.engines.macro.derive` 하나 — 금리차·신용스프레드(같은 기간끼리만, Decimal 차), 전년동월비·
   전월비·3개월 연율, 실질 정책금리(그달 마지막 정책금리 − CPI YoY). 공개 파생에 로그인 입력이 섞이면 `TierMixError`. 공개
   내보내기는 엔진을 import 할 수 없어(계약 ⑧) **같은 정의를 SQL 로**(spread·yoy) 계산하고 시험이 엔진 값과 맞춘다 — 실질 정책금리는
   공개판에 싣지 않고 사유만(로그인 화면).
5. **경제 캘린더**(D-P5-8): 공개 행 = 수기 공식 일정(`calendar.manual` — 원 일정 대조 전이라 지금 비어 있음) + FRED 발표일(이름 →
   종류 대응 `FRED_RELEASES` — 모르는 발표는 싣지 않음 [확인 필요 #45]). '발표 결과' = 같은 시리즈가 그 날짜 이후 처음 공표한 값
   (지어내지 않는다). **예상치는 ForexFactory**(사용자 결정 Q3 — 쓴다): `prv_macro.calendar` 에만, 로그인 API 가 같은 (나라·종류·날짜)
   공개 행에 덧붙이고 짝 없는 행은 따로 싣는다. 공개 표·공개 파일·레포 픽스처에는 예상치가 없다(합성 가짜 피드만). 피드 칸 이름·
   갱신 주기 [확인 필요 #49]. 스위치는 `config/macro.yaml ff.enabled`(지금 false — 켜기는 FND·WRAP 요청).
6. **상단 띠 P5 칩**(D-P5-20): 신용스프레드 AA-(817Y002 AA- − 국고 3년, 한쪽이 늦으면 stale)·신용잔고(신용거래융자 전체, 없으면
   유가+코스닥 — 전 거래일 값이 없으면 stale) = 공개(로그인 API + 공개 파일 `credit.json`), 한국 대 글로벌(코스피 최근 등락률 −
   간밤 S&P 500·나스닥 등락률 평균, %p — [제안] 정의) = 로그인. 입력이 없으면 서버가 '준비 중(P5)' 칩을 그대로 보낸다(0 금지).
7. **버린 것**: CNN 공포탐욕·alternative.me 스크랩, PCR·코스피200 야간선물(→ P7), 네이버 지수(→ KRX 지수), Finnhub, 30분 파일 캐시
   (→ 등록부·API 캐시), 통화신용정책보고서 xlsx 파싱(사용자 결정 Q2 — 인용 ECOS 시리즈 + 원문 링크로 대신).

## 3. 결과

- 공개판 페이지 6 은 공개 탭 6 + 경제 캘린더(공식 일정·발표 결과)만, '시장' 탭은 자물쇠. 로그인판은 시장 탭(Yahoo 12종·FRED 저작권·
  ECOS 타기관)과 예상치까지. 페이지 5 의 캘린더 패널은 같은 모듈.
- 실측 전이라 `macro.*`·`market_stats.kofia` 는 꺼진 채(#43~#46·#48·#49). 키를 받은 뒤 항목 코드를 채우고(`config/macro.yaml`),
  probe 를 지우고 WRAP 이 켠다.
- 알려진 중복: 공개 내보내기의 spread·yoy SQL 과 신용 칩 계산은 엔진 정의를 다시 적은 것이다(계약 ⑧ 때문) — 시험
  (`tests/unit/public_export/test_macro_json.py`)이 두 결과를 맞춘다.

## 4. 열린 것

- FRED 시리즈별 저작권 표시 판정(#45), FRED 발표 이름 대응 목록, 재무부 CSV 열·뉴욕연은 경로(#45), Yahoo 장중 봉(#46),
  금투협 열·단위(#48), ForexFactory 칸·주기·약관(#49), '한국 대 글로벌' 정의(metrics §6.5 보강 — WRAP).

## WRAP 확정 메모(2026-10-13 — 웨이브 3)

1. 상태 **채택**. 카탈로그: `FRED:gov_series`·`FRED:release_dates`·`FRED:restricted_series`·`TREASURY:yield_curve`·`NYFED:rates`·
   `FF:calendar` 를 PLANNED 에서 어댑터(`_ADAPTERS`)로 옮겼다 — PLANNED 는 비었다. 미 재무부·뉴욕연은은 **공개**(사용자 결정 Q8 —
   DATA_TIERS §1 행), ForexFactory 는 **로그인 — 예상치를 쓴다**(Q3: `config/macro.yaml ff.enabled: true`, 칸 이름·갱신 주기
   [확인 필요 #49]).
2. 등록부: `macro.*` 6·`market_stats.kofia` 는 실측 #43~#46·#48·#49 전이라 꺼짐(노트에 막는 번호). FF 어댑터는 켰지만 FF 를 부르는
   `macro.morning_prv` 가 FRED 저작권 시리즈·Yahoo 실측(#45·#46) 전이라 꺼져 있다 — 그 작업을 켤 때 함께 돈다.
3. 계약 ⑭(공개 매크로·통계 작업·공개 원천 → 로그인 원천·저장소·`collectors._p3` 금지)과 `tests/unit/test_public_writers.py`(macro·
   kofia SQL 은 pub_ 만)를 더했다. 공개 내보내기 `NO_DB_SKIP` 에 P5 파일(macro/·credit.json)을 적었다.
4. 하루 운영 시뮬레이션: 06:20 KST(= 16:20 ET 전날)에는 FRED 일별 값이 아직 없어 `macro.morning`·`macro.morning_prv` 가 재시도
   뒤 `not_ready` 로 끝난다(가짜 FRED 도 다음 뉴욕 날 공표). 실제 FRED 일별 공표 시각 [확인 필요 #45] — 늦으면 cron 을 옮기거나
   catch_up 을 둔다(아침 브리핑 ② 는 전 영업일 값 + 실제 기준일 표시로 이미 견딘다). 공개 산출물 `macro/`·`credit.json` 생성·
   공개 안전 검사 통과.
5. 합성 세계 금투협 항목 이름을 수집기 이름표(`loan_kospi`·`loan_kosdaq`·`investor_deposit`)로 맞췄다(웹 픽스처의 바꿔 싣기 삭제).
