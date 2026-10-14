# ADR 0031 — 규칙 알림: DSL v1·평가 시점 둘·쿨다운 두 겹·근거 템플릿·장중 시세 저장·장중 수급 라이브 (2026-10-14)

상태: **확정**(2026-10-14 P8 WRAP — P8 묶음 EVL 초안, 끝 'WRAP 확정 메모'). 설계서의 'ADR A'(docs/p8_design.md §9.2). 번호: P6 가 0025~0027,
IDX 가 0028, P7 이 0029~0030 이라 0031(BKT 의 ADR B 는 0032).
근거: docs/p8_design.md D-P8-4~11·15·16·20, §3.1~§3.4, §4.1~§4.4, 사용자 결정(2026-10-13 — 장중 수급 라이브), metrics
§30, ADR 0005(notifier 쿨다운), ADR 0008(로그인 API·CSRF), ADR 0018(마감 뒤 발송은 16:00).

## 1. 맥락

SD 의 규칙 알림(`tg_custom_alerts`·`tg_watchlist`·`tg_trailing` — 가격·RSI·MACD·외국인 조건)은 규칙 형식이 코드에 박혀
있고, 같은 조건을 백테스트로 다시 볼 수 없으며, 왜 울렸는지 근거가 남지 않는다. PLAN §8 P8 완료 기준은 "규칙 예시
10개가 백테스트와 실시간 평가에서 같은 신호를 낸다" 이다.

## 2. 결정

1. **규칙 = JSON DSL v1**(`kbj.engines.rules.dsl` — pydantic 모델, JSON 스키마는 생성 전용 `config/schemas/
   rule_v1.schema.json`·`web/src/api/rule.schema.json`). 문자열 식·사용자 코드 실행 없음. 수정은 **새 판**(판은 바꾸지
   않는다 — 과거 신호가 어느 판에서 났는지 남는다), 삭제는 보관. 쓰기 모듈 하나(`kbj.services.personal.rules`)를 API
   (`POST·PUT /api/rules*` — 세션·CSRF·64 KiB, 422 는 위치·사유만)와 CLI(`python -m kbj.services.personal rules …`)가
   같이 부른다.
2. **평가 시점 둘**:
   - `rules.intraday`(정규장 10분, 사건 시점 발송 `alert.rule`, subject `<rule_id>:<code>`) — 대상 = 장중 규칙
     우니버스의 합집합(관심 목록·보유·코드), 상한 `intraday_max_codes`(60), 넘으면 (규칙 우선순위, 우니버스 안 순서)로
     자르고 잘린 종목을 `skipped/truncated` 로 기록. KIS FHKST01010100(리미터 P2) → 부분 봉(시가·지금까지 고가·저가·
     현재가·누적 거래량, estimated) → **`prv_alerts.watch_quote_slot` 에 저장**(장중 재생 백테스트가 같은 입력을 읽는다).
   - `rules.close`(16:00, 굳은 의존 `market.close_collect`, 캐치업 20:30) — 원장만(KIS 호출 없음, 상한 없음), 허락된
     신호를 **16:00 한 통 묶음**(`alert.rule_close`, `dedup: daily`, 0건이면 보내지 않음). 마감 신호의 평가 시각은
     그날 15:30 KST(`live.close_eval_ts` — 16:00·캐치업 어느 때 돌아도 같은 키).
   - **ADR 0018 보충**: 15:30 이후 첫 발화 발송 작업은 `brief.closing`·`flows.report`·`consensus.snapshot`·
     `rules.close` 넷, 전부 16:00(등록부 시험 — FND).
3. **장중 수급은 라이브**(사용자 결정 2026-10-13 — 설계서 Q12 기본값 '전일 확정' 을 버림): 수급 조건의 오늘 값 = 같은
   회차 KIS 가집계(`prv_flows.investor_intraday` ts = 슬롯 — 쓰기 주인 `flows.intraday`, 등록부 무른 의존 5분). 근거에
   '장중 잠정'. 가집계가 없는 종목·회차는 그 조건을 **판단 불가**(3값 논리 — 평가 상태 `unknown`)로 두고 그 규칙은 그
   종목을 보내지 않는다(`skipped/no_live_flow`, 지어내지 않음). 마감 평가는 원장 행으로 다시 판정하고, 잠정 → 확정
   차이는 일치 감시가 `data_revised` 로 분류한다(ADR 0032).
4. **같은 신호의 정의**: 실시간·백테스트·규칙 검증 API 는 같은 입력 고르기(`kbj.services.engine.rules_input.
   load_frames` — 원장 우선순위 일봉, 거래일 캘린더 칸, 확정 수급 원장 **언제나**(입력 지문이 수급 값을 포함), board
   `split_guard` 문턱), 같은 프레임(`build_frame`)·평가기(`evaluate`)·쿨다운 상태기(`allow`)를 부른다.
5. **쿨다운 두 겹**: 엔진 상태기(규칙 `cooldown_min` — 기본 1,440·하한 60, 같은 (규칙, 종목)의 마지막 `sent`·
   `suppressed`·`dry` 로부터 미끄러지는 창, 판 무관, `both` 는 장중·마감 공유) + notifier `alert.rule` 60분 안전망.
   막힌 시점도 `status='cooldown'` 으로 기록한다. 같은 키(규칙·판·종목·평가 시각)를 다시 돌리면 이미 판정된 행은
   그대로 두고 다시 보내지 않는다(멱등). notify 결과 → 상태: 넣음 `sent`, 중복·쿨다운 `suppressed`, 발송 꺼짐(또는
   notify 자원 없음) `dry`, 넣기 실패 → `skipped/error` + 실행 failed(다음 실행이 다시 판정 — 오류는 쿨다운에 세지
   않는다).
6. **근거 = 템플릿**(`kbj.engines.rules.evidence` — LLM 없음): 신호 표 `evidence` jsonb 에 조건마다 지표·파라미터·값·
   기준값·전 칸 값·사유·수급 기준(장중 잠정/확정), 시세 출처·품질을 싣고, 알림 본문·마감 묶음·`/시그널` 답·페이지 11
   이력이 같은 자료로 그린다. 상세 링크는 `KBJ_PUBLIC_BASE_URL` + `#/8/<코드>`(주소가 없으면 뺀다).
7. **분할 의심**: 의심 칸부터 260칸 동안 `skipped/split_suspect`(수정주가는 만들지 않는다). `prv_board.split_check`
   는 의심 날짜가 없어(종목·메모만) 넘기지 않는다 [확인 필요 — 날짜가 생기면 `split_dates` 로].

## 3. 결과

- 규칙을 화면(페이지 11 ①②③)·CLI·API 로 다루고, 모든 신호(건너뜀·쿨다운 포함)가 근거와 함께 표에 남는다.
- KIS 호출: 장중 대상 ≤ 60 × 40 슬롯 = 하루 ≤ 2,400(P2).
- 장중 수급 조건은 가집계가 받는 종목(`flows.intraday` — 시장별 상위 목록)에만 값이 있다 — 관심종목·보유가 빠지면 그
  종목의 수급 조건은 언제나 판단 불가(probe #66). 범위를 넓히려면 등록부 변경(WRAP).

## 4. 남은 것(WRAP)

- `rules.intraday`·`rules.close` 켜기(probe #65 FHKST01010100 장중 칸·#66 가집계 범위 실측 전에는 장중 판단),
  `/시그널` 읽기 함수를 notifier 서비스에 연결(`default_registry(signals=kbj.services.engine.rules.command_reader(…))`),
  데이터 소스 쓰기 채널(`post`·`put` — CSRF), `rules_perf.frame_inputs` 와 `rules_input.load_frames` 하나로 합치기.

## 5. WRAP 확정 메모(2026-10-14)

- 켜기: `rules.close`(16:00)는 켰다(원장만 — 외부 호출 없음). `rules.intraday` 는 **꺼진 채**로 둔다(→ §6 에서 사용자 요청으로 켬) — 장중 FHKST01010100
  칸·반영 지연(#65)과 가집계 대상 범위(#66)를 실데이터로 확인하기 전이다(실데이터로 확인 안 된 수집 작업은 꺼 둔다 — P7
  `krx.vkospi` 와 같은 기준). 꺼진 동안 장중 규칙은 저장만 되고 평가·발송이 없다(화면 '발송 이력' 은 마감 신호만).
  켜는 일은 #65·#66 실측 뒤 등록부 한 줄 + `ENABLED_BY_PHASE["P8"]` 한 줄.
- `/시그널`: notifier 진입점이 `command_reader(pg_repos(...), utcnow)` 를 넘긴다(`build_service(signals=…)` — 시험
  `tests/unit/notifier/test_signals_wiring.py`).
- 쓰기 채널: 로그인 데이터 소스 `createApiSource({writeHeaders})` 의 `post·put·patch·del`(CSRF 헤더 `X-KBJ-CSRF` — 토큰
  없으면 요청 0, 거절은 서버 짧은 사유만). 시험 `web/test/api_write.test.ts`.
- 입력 고르기 하나: `rules_perf.frame_inputs` 를 `kbj.services.engine.rules_input.frame_inputs` 로 옮겨 실시간
  `load_frames` 가 그것을 거친다(같은 `build_frame` 인자 — 두 벌 없음). `NEW_HIGH_BASIS`·`calendar_days`·신고가 라벨도 한 곳.
- `split_dates`(prv_board.split_check 의심 날짜) 전달은 **[확인 필요]** 그대로 — 의심 표에 날짜가 없다. 분할 의심은
  `split_guard` 문턱 판정(정본)만으로 막는다(완료 시험 ⑤ — 분할 종목 허락 신호 0).
- 지표 백분위 두 구현(`kbj.core.indicators.pct_rank` float · `kbj.engines.diagnosis.bands.pct_rank` Decimal 양자화)은 같은
  §D0 규칙이다. engines 는 core 를 import 할 수 있으므로 P9 에서 diagnosis 쪽을 core 호출 + 양자화로 바꿔 하나로 한다
  [제안 — 진단 골든(P7) 재확인이 필요해 P8 에서는 손대지 않는다].
- 완료 증명: `tests/acceptance/test_rules_parity.py`(마감 7·장중 3 — 실제 처리기 대 백테스트 집합 같음, 쿨다운 같음,
  KRX 정정 → data_revised·logic 0, 분할 종목 0), `tests/sim/test_rules_days.py`(25 거래일 — 성과 누적·멱등·16:00 한 통).

## 6. `rules.intraday` 켬 — 사용자 요청(2026-10-14)

§5 의 '꺼진 채' 를 바꾼다. 사용자가 장중 수급 라이브 판정을 바로 켜 달라고 요청했고, 공식 KIS 예제
(koreainvestment/open-trading-api, 2026-10-14 확인)로 다음을 확인했다: FHKST01010100 응답 칸 이름은 레포와 같다(#65).
FHPTJ04400000 가집계는 **순위 목록**(순매수 상위·순매도 상위)이고 증권사 입력값의 단순 누계라 **하루 4회**(외국인
09:30·11:20·13:20·14:30 / 기관 10:00·11:20·13:20·14:30, ±10분)만 바뀐다(#66). 그래서 켜면서 세 가지를 바꿨다.
① `flows.intraday` 가 순매도 상위(`FID_RANK_SORT_CLS_CODE=1`)도 받아 같은 회차·대상으로 합친다(슬롯 4 → 8건, 하루
+160건·재시도 최악 +480 — `daily_soft_cap` 30,000 안, 등록부 시험). ② 장중 평가의 수급 조건은 구분마다 **확실히
들어온 가장 최근 회차**(회차 + 10분 ≤ 슬롯)의 목록만 쓰고, 다음 회차 ±10분 창에서 받은 목록은 회차를 단정할 수 없어
쓰지 않는다(`kbj/engines/flows/prelim.py` — 실시간·장중 재생 공용). 근거 문구에 회차 시각('장중 잠정 13:20 회차').
③ 첫 회차 전이거나 두 목록 어디에도 없는 종목·구분은 '판단 불가'(0 으로 세지 않음 — 발송 없음, `skipped/no_live_flow`).
가격 조건은 FHKST01010100 슬롯 시세 그대로. **한계**: 목록 길이 N 이 문서에 없어 관심종목·보유가 목록 밖이면 그 종목의
수급 조건은 늘 판단 불가다(종목 단위 TR HHPTJ04160200 은 수량만 있고 레포에 없음 — 범위를 넓히려면 별도 결정). 회차
시각은 '장 사정에 따라 바뀔 수 있다' — 늦게 들어오면 그 회차 동안 앞 회차 값이나 판단 불가가 된다. 남은 확인은 장중 하루
실측: #65 단위·반영 지연, #66 목록 길이·금액 단위·회차 실제 편차(`docs/probe_results.md`).
