# ADR 0033 — 저널·포트폴리오·관심종목 쓰기: 쓰기 모듈 하나·이동평균 단가·읽을 때 계산·SD 이관 경로 (2026-10-14)

상태: **확정**(2026-10-14 P8 WRAP — P8 묶음 JNL 초안, 끝 'WRAP 확정 메모'). 설계서의 'ADR D'(docs/p8_design.md §9.2). 번호: ADR A(EVL)가 0031,
B(BKT)가 0032 라 0033(C — SWG 는 사용자 결정으로 없음).
근거: docs/p8_design.md D-P8-15·16·19, §4.1.5·§4.7·§5·§6, ADR 0008(로그인 API·CSRF), D-P4-13(관심종목 개인 데이터).

## 1. 맥락

SD 는 분석 일지·수익률 저널·추천 이력을 SQLite 에, 알림 규칙·포지션을 JSON 캐시에 두고 Flask 라우트가 직접 썼다
(포지션은 브라우저 localStorage 사본). KBJ 는 표마다 쓰는 쪽이 하나여야 하고(D-P8-15), 개인 쓰기 API 는 처음이다
(D-P8-16). KIS 잔고·주문은 쓰지 않는다(절대 규칙 6, §10 Q6 — 잔고 동기화 안 함).

## 2. 결정

1. **쓰기 모듈 하나**: 일지·매매는 `kbj.services.personal.journal`, 관심종목은 `kbj.services.watchlist`(P4 가져오기
   모듈에 `add`·`remove`·`reorder` 추가 — 출처 `api`). API 라우트(`/api/journal/*`·`/api/watchlist/{list}/{code}`·
   `/order`)와 CLI(`python -m kbj.services.personal journal …`)가 같은 함수를 부른다. 관심종목 쓰기는 저장소의
   `replace_watchlist`(그 목록을 읽어 고친 뒤 통째로 — 한 트랜잭션)를 그대로 쓴다(저장소를 늘리지 않음).
2. **문지기**: 쓰기는 전부 `WriteSession`(세션 → CSRF·Origin 403 → 64 KiB 413). 거절은 사유 코드만 —
   404 `unknown_analysis`·`unknown_trade`·`not_in_list`·`unknown_code`, 422 `negative_holding`·`invalid_row`·
   `bad_order`·`bad_code`. 입력값(금액·수량·메모)을 응답·로그에 되돌리지 않는다(시험이 확인).
3. **포지션(이동평균 단가, 읽을 때 계산 — 표 없음)** `kbj.engines.journal.portfolio`:
   - 매수: 보유 원가 += 가격 × 수량 + 수수료 + 세금(매수 비용을 원가에 넣는다 **[확인 필요 — SD 는 비용을 따로 뺐다]**).
   - 매도: 실현 = (가격 − 평균 단가) × 수량 − 수수료 − 세금. 보유보다 많이 팔면 `NegativeHolding` → 422. 매수를 지워
     뒤 매도가 보유보다 많아지는 삭제도 거절. 순서는 (매매일, id).
   - 평가 = (최신 확정 종가 − 평균 단가) × 수량(최근 5 거래일 안 마지막 종가 — 기준일·품질 표시). 종가가 없으면 None.
   - 월별 수익률 = (그 달 실현 + 평가 변화) ÷ (월초 보유 원가 + 그 달 매수 원가) × 100 **[확인 필요 — 현금 원장 없음]**.
   - 돈은 Decimal(원) — 저장(numeric·jsonb 문자열)·계산 모두. 응답은 원 단위 정수(반올림 0.5 올림)·소수 둘째 가격.
4. **상관** `kbj.engines.journal.correlation`: 관심종목(main) 또는 보유, 앞 50종목. 기준 달력 마지막 60·120 거래일의
   일간 수익률(바로 앞 거래일 종가가 있을 때만 — 정지 뒤 첫날을 여러 날 수익률로 잇지 않는다), 쌍마다 겹치는 날 < 40
   이거나 분산 0 이면 None. 2종목 미만이면 404.
5. **분석 일지 prefill** `kbj.engines.journal.prefill`: P4 PIT 배수(가격·시총·Fwd PER·EV/EBITDA)·PER 밴드 백분위·
   시나리오 적정가(bear·base·bull — PER 방식이면 PER 칸)·컨센서스(추정 영업이익률, 시나리오가 없을 때만 목표가 평균 →
   tp.base)·일봉 52주 수익률(250 거래일, 유효 240 미만 None **[확인 필요]**). 칸마다 `value_sources`(kbj·consensus).
   값이 없으면 빈칸(채울 것이 하나도 없으면 404). 화면에서 prefill 값을 고친 칸은 출처가 `manual` 로 바뀐다. 밸류체인
   칸은 사전 통합 전까지 빈칸.
6. **추천 성과**: SD `recommendation_history` 이관분만(새 추천 생성기 없음). 사후 수익 D+k = 추천일 뒤 k 번째 거래일
   확정 종가 ÷ 추천가 − 1(%) — 추천가가 없으면 추천일 종가. 없으면 None.
7. **SD 이관 경로** — COPY 매핑(`MAPPINGS`)이 아니라 쓰기 모듈 경로(`P8_SD`):
   일지·매매 표는 id 가 GENERATED 라 키 대조 COPY 가 맞지 않고, 규칙은 DSL 검사를 거쳐야 한다. 그래서 읽기·변환은
   `kbj.store.legacy_import.sd_p8`(계약 ⑦ — 저장소 층은 서비스·엔진을 모른다), 쓰기는
   `python -m kbj.services.personal journal import-sd --db … --rules … --portfolio …`.
   - 일지·매매는 `import:sd` 행이 이미 있으면 다시 넣지 않는다(멱등), 추천은 키 upsert, 규칙은 같은 이름이면 건너뜀.
   - SD 규칙 → DSL 은 `kbj.engines.rules.legacy_sd`(§4.1.5 표 그대로, mode both, 하루 1번 → `cooldown_min: 1440`).
     미국 종목(`not_kr`)·SD 평가기가 판정하지 않던 종류(bb_upper·bb_lower·volume_spike → `unsupported_type`)는 목록으로
     보고만. 이관 규칙은 **꺼진 채**(사용자가 페이지 11 에서 보고 켠다 — 이관 직후 텔레그램이 쏟아지지 않게).
   - 포지션(`server_portfolio.json`)은 기초 매수 한 줄 **[확인 필요 — SD 화면 저장 형식]** — SD 매매 기록에 있는
     종목은 두 번 세지 않게 뺀다(`covered_by_trades`). 종목 매매가 음수 보유가 되면 그 종목 매매를 통째로 뺀다.
   - SD 의 `created_at`·`updated_at`(오프셋 없는 서버 지역 시각)은 쓰지 않고 이관 시각을 넣는다(지어내지 않는다).
     `market_cap` 은 백만원 → 원. 옛 발송 이력(`alert_history`·`_v2`)은 옮기지 않는다(`NEVER` — 신호 표와 뜻이 다르다).
8. **화면**: 페이지 12 ④ 분석 일지(6)·⑤ 포트폴리오(6 — 월별 막대는 반폭 안)·⑥ 상관(6 — 히트맵)·⑦ 추천 성과(6).
   쓰기 단추는 데이터 소스에 쓰기 채널(`post`·`patch`·`put`·`del`)이 있을 때만 켠다 — 없으면 안내만(요청 없음).
   페이지 8 머리 별 토글(`put`·`del watchlist/main/<코드>`)도 같은 약속. 404·로그인 등급 안내는 `.msg`, 그 밖만 `.msg.err`.

## 3. 결과

- 매매 CSV 가져오기: `journal trades import --csv <파일>` — 머리 줄 필수(`date,code,side,price,qty` + 선택
  `fee,tax,strategy,memo,name`), 거절(줄 번호·사유만)이 있으면 아무것도 쓰지 않는다(`--skip-rejects`·`--dry-run`).
- 지표 정의(포지션·월별·상관·prefill·추천 사후 수익)는 `docs/metrics.md` P8 절에 옮겨야 한다(WRAP — 공유 파일).
- 열린 것: 매수 비용의 원가 포함 여부, 월별 수익률 분모(현금 원장 도입 시 바꾼다), 공용 데이터 소스의 쓰기 채널
  (`post`·`patch`·`put`·`del` — CSRF 헤더) 구현은 WRAP.

## 4. WRAP 확정 메모(2026-10-14)

- 쓰기 채널: 로그인 데이터 소스에 `post·put·patch·del`(CSRF) — 페이지 12 ④⑤ 저장·매매 넣기와 페이지 8 별 토글이 켜진다.
- import-linter 계약 ⑳: `kbj.services.personal` 은 원천 어댑터·수집기·발송·API·작업 처리기를 모른다(journal 이
  `personal.rules`·`engines.rules.legacy_sd`·`store.legacy_import.sd_p8` 를 쓰는 것은 허용 — 저장소·엔진·이관 읽기).
- CLI: `python -m kbj.services.personal journal …` — `--help`·사용법 오류는 DB 없이 끝난다(저장소는 처음 쓸 때 만든다).
- 정의: 포지션·월별 수익률·상관(겹침 40)·prefill(52주 = 250거래일, 유효 240)·추천 사후 수익 D+k 를 `docs/metrics.md` §30
  P8 절로 옮겼다(§2 의 정의 그대로 — 단가 이동평균·매수 비용 원가 포함, 월 분모는 [확인 필요]).
- SD 이관: 옛 발송 이력(`alert_history`·`_v2`)은 옮기지 않는다(`mappings.NEVER` — 신호 표와 뜻이 다르다) [확인 필요 —
  사용자 확인]. SD 서버의 매매·분석 일지·상관·추천 성과·백테스트 라우트는 지웠다(legacy/stock_dashboard/MIGRATION.md 'P8').
