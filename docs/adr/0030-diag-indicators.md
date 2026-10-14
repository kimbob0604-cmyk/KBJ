# ADR 0030 — 페이지 3 진단 지표: 등급 배치·국장 공포탐욕·종합 판정·VKOSPI 대체·작업 둘 (2026-10-13)

상태: **확정**(2026-10-13 P7 WRAP — 묶음 DIAG 초안, §4 WRAP 확정 메모).
설계서의 'ADR-B'(docs/p7_design.md D-P7-21). 번호: P6 가 0025~0027, IDX 가 0028, ADR-A(GEX 이식 경계)가 0029 라 0030.
근거: docs/p7_design.md D-P7-12~15·§2·§3.2·§5·§6·§7, 부록 A → docs/metrics.md §D0~§D9, DATA_TIERS(페이지 3 = mixed),
§10 Q3·Q4·Q6, ADR 0018(마감 뒤 발송은 16:00).

## 1. 맥락

PLAN §4 페이지 3 은 '진단'(종합 판정·국장 공포탐욕·시장 진단·GEX 레벨)이다. DATA_TIERS 는 페이지 3 을 섞인 등급으로
정했다 — 공공데이터만으로 만든 지표(신용스프레드 구간·신용잔고·예탁금 추세·수출 모멘텀·금리 → 매크로 위험 게이지)와
이벤트 표시는 공개, 시장폭·수급·공포탐욕·종합 판정·GEX 는 로그인. 외부 공포탐욕 지수(CNN·alternative.me)를 긁지
않는다(U4 정신 — 원천을 모르는 값을 화면에 올리지 않는다).

## 2. 결정

1. **등급 배치(D-P7-12, §10 Q4)**: 공개 = `pub_diag.indicator`(§D1~§D5 다섯 줄)·`gauge`(§D4)·`event_marker`(§D9),
   로그인 = `prv_diag.indicator`(§D6 여섯 줄)·`fear_greed`(§D7)·`verdict`(§D8)·`vol_index`(VKOSPI). 공개 내보내기는
   `pub_diag` SQL 만 읽고(계약 ⑧), 파일에 로그인 판정 키(`fear_greed`·`verdict`·`gex*`)를 **키조차** 싣지 않는다
   (`manifest.diag_problems`·`merge-public-data.mjs`). 공개판 페이지 3 은 공공지표 진단·추이·이벤트 달력만 그리고 나머지
   자리는 자물쇠(요청 0).
2. **작업 둘 — 공개·로그인 모듈 분리(D-P7-15)**:
   - `diag.public`(`kbj.services.engine.diag_pub:run`, 평일 08:40) — 공개 표(`pub_macro` ECOS 국고 3년·회사채 AA-,
     `pub_market_stats` 금투협, `pub_trade` 수출 속보, `pub_filings` 실적 일정)만 읽고 `pub_diag` 만 쓴다. 이 모듈은
     로그인 원천·저장소·GEX 를 import 하지 않는다(새 계약 '가' 의 대상 — WRAP).
   - `diag.daily`(`kbj.services.engine.diag:run`, 거래일 16:00) — 그날 D 는 `estimated`, 직전 거래일 D−1 은 KRX 확정
     원장으로 **같은 작업이** 다시 계산해 덮는다(쓰기 주인 하나 — 확정용 두 번째 작업 없음). 발송하지 않는다 —
     `brief.closing`(16:00)이 무른 의존으로 기다린다(ADR 0018).
3. **정의는 metrics §D0~§D9 하나**(부록 A). 상태 구간은 250거래일 백분위(평균 순위, `(r−1)/(n−1)×100`) 다섯 칸,
   신용스프레드만 1년 평균 ± 1σ(모표준편차). 이력 < 60 이면 구간 없음 + `estimated` + '이력 부족'. 기존 정본
   공식은 **다시 정의하지 않고 불러 쓴다** — 반도체 쏠림·경기민감 대 방어 `kbj.engines.market.ribbon`(20거래일),
   시장폭 `kbj.engines.market.breadth`, 신용스프레드 `kbj.engines.macro.derive.spread`, 한국 대 글로벌
   `kbj.engines.macro.ribbon.kr_vs_global_chip`(20거래일 전 봉과 그날 봉 두 점), 수출 YoY `kbj.engines.trade.flash`.
   순수 계산은 `kbj/engines/diagnosis/`(계약 ⑨), 숫자 문턱은 `config/diag.yaml` 한 곳.
4. **국장 공포탐욕(§D7, §10 Q6)**: v0.2 다섯 성분(변동성·시장폭·신용잔고 변화·외국인 수급·신용스프레드) 백분위의
   **같은 가중** 평균(변동성·신용스프레드는 뒤집음). 성분 < 4 → 값 없음(`invalid` + 빠진 성분), 성분이 다 모이지 않았거나
   대체가 있으면 `estimated`.
5. **VKOSPI 대체(D-P7-14)**: `krx.vkospi`(KRX `idx/drvprod_dd_trd` — 경로·행 이름·칸 [실측 필요 #P7-1])는 **꺼진 채**.
   VKOSPI 이력이 60 미만이면 변동성 성분은 월물 ATM IV(`prv_gex.metrics atm_iv` 시리즈 'M:' 가장 가까운 만기, visible·
   invalid 아님)로 대신하고 사유 '대체: ATM IV' + `estimated`. 그날 ATM IV 가 없으면 성분 빠짐(지어내지 않는다).
6. **종합 판정(§D8)**: 공포탐욕 구간·신용스프레드 σ 칸·시장폭(20일선)·한국 대 글로벌·외국인 수급·GEX 체제(범위 all
   마지막 주간 사이클의 Flip 거리 부호 — **visible 일 때만**, §10 Q3) 점수 합. ≥2 위험 선호·≤−2 위험 회피·그 사이
   중립·성분 < 4 보류. 근거 셋은 저장하지 않고 화면이 성분 점수에서 다시 읽는다(`verdict.top_reasons` 정본).
   이벤트 주의 = §D9 이벤트가 오늘~다음 거래일.
7. **시점 입력(재계산 같음)**: 공개 작업은 공표·수신 시각이 실행 시각(cutoff) 이하인 행만 읽는다 — 같은 날짜를 같은
   cutoff 로 다시 돌리면 그 사이 새 자료가 와도 결과가 같다. 로그인 시장 지표의 백분위 이력은 원장으로 250일을 매일 다시
   만들지 않고 **앞서 저장한 `prv_diag.indicator`**(그날 앞)와 그날 값으로 — 처음엔 '이력 부족', `python -m
   kbj.services.engine.diag backfill --from --to` 가 과거 날을 오래된 날부터 채운다.

## 3. 결과

- 화면: 판정 띠(종합 판정·공포탐욕 gauge) · 공공지표 진단(.lvl 다섯 + 게이지) + 추이(반폭) · 시장 진단(카드) + 추이(반폭) ·
  GEX(ENG) · 이벤트 달력(줄 목록 — 차트 없음, span-12). 데이터 없음(404)은 '.msg', 그 밖 실패만 '.msg.err'.
- 실적 집중은 실적 공시 예고(`pre_announce`)의 회사 수만 센다 — 법정 기한(statutory)은 같은 날에 몰려 집중이 아니다.
  §D9 의 '시총 상위 200' 거르기는 공개 입력에 시총이 없어 하지 않는다 [확인 필요 — 공개 시총 원천이 생기면].
- 수출 속보 공표일(1·11·21일)의 휴일 규칙은 미상이라 달력일 그대로 `estimated`(P6 #55).
- 경기민감 대 방어는 업종 지수 코드가 비어(`config/markets.yaml ribbon.cyclical·defensive` [확인 필요]) 값 없음 + 사유.

## 4. WRAP 확정 메모(2026-10-13)

1. **켜기**: `diag.public`(08:40)·`diag.daily`(16:00) 를 켰다. `krx.vkospi` 는 실측(#P7-1) 전이라 꺼진 채 — 공포탐욕
   변동성 성분은 월물 ATM IV 대체(`estimated` + '대체: ATM IV').
2. **경계 계약**: import-linter ⑱ — 공개 진단(`kbj.engines.diagnosis.public`·`kbj.services.engine.diag_pub`·
   `kbj.services.public_export.diag_json`)은 로그인 원천·로그인 저장소(`diag_prv`·`gex` 등)·`kbj.core.gex`·`kbj.engines.gex`·
   공포탐욕·판정 엔진을 import 하지 않는다. 표 쪽 겹은 `tests/unit/test_public_writers.py` — `diag.public` 이 끌어오는
   저장소 SQL 은 `pub_` 만(행 모듈 `kbj.core.p7_rows` 의 로그인 표 이름 상수 `DIAG_PRV_TABLES` 는 SQL 이 아니라 그 상수
   정의만 허용 — 다른 곳에 로그인 표 이름이 생기면 실패).
3. **공개 키 금지 규칙**: 공개 파일의 로그인 판정 키(`fear_greed`·`verdict`·`gex*`)는 빌드 때 두 겹이 막는다 —
   `manifest.diag_problems`(공개 내보내기)와 `web/scripts/merge-public-data.mjs`. 레포 정적 검사
   (`scripts/check_public_safety.py`)에는 넣지 않는다 — 그 검사는 커밋된 파일의 비밀·원자료를 보고, 공개 산출물은
   커밋되지 않는다. 커밋된 공개 픽스처(`web/test/fixtures/api/diag/public/**`)는 웹 완료 시험
   (`web/test/acceptance/p7_pages.test.ts`)이 같은 키 0 을 본다.
4. **백분위 공식**: metrics §D0 에 확정 — 평균 순위 r 로 `(r−1)/(n−1)×100`(n=1 은 50), σ 는 모표준편차. §D9 실적
   집중은 `pre_announce` 회사 수만(법정 기한 제외), 시총 상위 200 거르기는 공개 입력에 시총이 없어 생략(위 §3).
5. **브리핑·봇 문구**: 16:00 마감 요약 ⑥ 과 `/gex` 는 `verdict.top_reasons`·판정 라벨·`fear_greed.band` 를 그대로 읽어
   화면과 같은 문구(OUT). 마감 뒤 새 발송 없음(ADR 0018) — sim `tests/sim/test_one_day_p7.py` 가 16:00 뒤 1통을 본다.
6. **16:00 사슬(시각 그대로)**: `diag.daily` 는 보드(16:00)·마감 수집(≈16:00)이 재료라 16:00 전으로 당길 수 없다 —
   발화 16:00·`brief.closing` 무른 의존 `wait_min` 10 그대로. 대신 실행기가 그 자리에서 끝난 시도를 같은 회차에 바로
   거두게 했다(`kbj/services/scheduler/runner.py` `_advance` → `_collect_one`) — 보드 → 진단 → 마감 요약 사슬이 홉마다
   `DEP_POLL_S`(30초)씩 밀려 마감 요약이 16:01 에 시작하던 것(sim)을 16:00 안으로. 실제 발송 시각은 ADR 0018 §2.6 그대로
   (재료 준비 뒤 — 평소 16:00~16:05).

## 5. 다시 볼 때

- VKOSPI 실측(#P7-1) 뒤 `krx.vkospi` 켜기 → 60거래일 쌓인 뒤 대체가 자동으로 끝난다.
- 섀도 지표가 visible 로 바뀌면(§10 Q3) GEX 체제 성분이 판정에 들어온다(코드 변경 없음 — 저장된 플래그를 본다).
- 공포탐욕 가중치를 바꿀 때는 `config/diag.yaml fear_greed.weights` 와 metrics §D7 을 같이(지금은 `equal` 만).
