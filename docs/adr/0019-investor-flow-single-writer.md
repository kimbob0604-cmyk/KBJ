# ADR 0019 — 장중 투자자 수급의 쓰기 주인 하나·프로그램 상위 표의 범위 표시 (2026-10-13)

상태: **채택**(2026-10-13 — 초안 P5 묶음 F2, 번호·상태는 웨이브 3 WRAP 이 확정 — 아래 'WRAP 확정 메모').
근거: `docs/p5_flow_program_prompt.md` §1·§2·§4, `docs/p5_design.md` §0.4·§1.7·§3.4·§4.7(D-P5-21 ①②③), `docs/metrics.md`
§21·§22, ADR 0004 D2(웹소켓은 P7), ADR 0006(등록부·선점), ADR 0018(마감 뒤 텔레그램 16:00).
구현: `kbj/services/collectors/{flows_minute,program}.py`, `kbj/data/private/kis/{investors_minute,program}.py`,
`kbj/engines/flows/{minute,program}.py`, `kbj/services/api/readers/{flows_minute,program}.py`, `kbj/services/engine/verify.py`
(`--program`), `web/src/pages/p1/{flows_minute,program}.ts`, 등록부 `config/jobs.yaml`(FND — `flows.market_minute`·`program.*`),
마이그레이션 `0018_program.sql`(FND — `prv_gex.investor_flow` COMMENT).

## 1. 맥락

- KIS `FHPTJ04030000`(시장별 투자자매매동향 — 시세성)은 **호출 시점의 오늘 누적 한 줄**이다. 1분 선은 1분마다 불러 쌓아야
  생기고, 못 부른 분은 뒤에서 채울 수 없다.
- 같은 TR 을 GEXLAB poller 가 이미 부르고 있다(K2I 선물·콜·풋 — `legacy/gexlab`, P7 이식 대상). 저장 표
  `prv_gex.investor_flow`(0006 — UNIQUE `(ts, market_code, sector_code, investor)`, **대금 백만원**)와 뷰
  `prv_flows.market_investor_intraday` 도 GX 계약이다.
- P5 F2 가 페이지 1 '투자자 수급' 1분 선을 만든다. 두 곳(F2 작업·GX poller)이 같은 TR 을 1분마다 부르면 호출이 두 배가 되고
  같은 행을 두 작업이 다투어 쓴다.
- KIS 에는 '프로그램 순매수 상위' 순위 TR 이 없다(공식 예제의 순위 계열에 프로그램 정렬 없음). 스크린샷의 순위는 다른 증권사
  회원 집계다.

## 2. 결정

1. **쓰기 주인 하나**: `prv_gex.investor_flow` 의 유일한 쓰기 주인은 P5 작업 `flows.market_minute`(처리기
   `kbj.services.collectors.flows_minute:run`)이다. 등록부에서 `KIS:market_investor_intraday` 를 `gex.poller` 의 collects 에서
   빼고 이 작업으로 옮긴다(FND — 같은 데이터셋은 등록부 전체에서 한 작업만 받는다, ADR 0006). 저장소 쓰기 함수
   `FlowsRepo.put_investor_minutes` 는 이 작업만 부른다.
2. **P7 과의 겹침**: GX poller 를 옮길 때(P7) poller 는 이 TR 을 부르지 않고 표를 **읽는다**. 주인을 넘기려면 새 마이그레이션과
   ADR 로 한다. 그 전까지 VM 에서 legacy GX poller 를 같이 돌리면 투자자별 폴링을 끈다(운영 조건 — R6).
3. **단위 그대로**: investor_flow 대금은 GX 계약대로 **백만원**(수량 계약·주)으로 저장한다. 원으로 바꾸는 것은 API 다
   (`/api/flows/market-minute` — metrics §0 '원 저장'의 예외, metrics §21). 프로그램 표(0018)는 원으로 저장한다.
4. **분 행의 시각과 품질**: 매분 작업은 `equity {start: open, end: close+10, every_min: 1}`. 행 `ts` 는 **슬롯 분**(호출 시각이
   아님). 슬롯 + 45초 안에 받은 값은 `ok`, 늦게 받은 값은 `estimated`(그 분보다 뒤의 누적), 검산 ①(현물 합 0)·②(기관 = 7세부)가
   어긋난 분은 `invalid` 로 쓰고 화면·카드에서 뺀 뒤 수를 보인다. 같은 분 재실행은 같은 키를 덮는다(멱등). 빈 분은 행이 없고
   화면은 선을 끊는다(보간 금지). 화면의 '잠정' 꼬리표는 장중 내내 붙는다(값 품질과 별개).
5. **실측 전에는 GX 가 실측한 조합만**: `config/markets.yaml flows.minute.pairs` 에서 K2I `F001`·`OC01`·`OP01` 셋만 켠다.
   현물(코스피·코스닥)·주식선물 코드는 [확인 필요 #39] — 코드를 비우고 꺼 둔다(설정 검사가 코드 없는 조합을 켜지 못하게
   막는다). 꺼진 탭은 화면에 '확인 전'. 거래소 입력이 없는 TR 이라 분 선은 `KRX` 로 잠근다(통합 수치를 지어내지 않는다).
6. **프로그램 상위 표는 범위를 숨기지 않는다**: 순위는 종목 TR 로 범위를 훑어 만든다 — 장중 `program.stock_scan`(10분, 거래대금
   상위 `program.scan_top_n` + 관심종목), 마감 뒤 `program.stock_sweep`(18:10 전 종목 — 종목 단위 선점으로 끊기면 이어서).
   API `ProgramTop.scope_label` 은 비울 수 없고 화면은 표 위에 `범위: …` 를 단다(장중 '거래대금 상위 N + 관심종목', 마감
   '전 종목(마감 확정)' — 덜 끝났으면 '훑은 종목 n/N' + estimated). 다른 증권사 API(새 키·계정)는 쓰지 않는다(PLAN §7).
7. **켜는 순서**: `flows.market_minute` 은 K2I 셋으로 WRAP 이 켠다. `program.*` 다섯 작업은 TR·칸·단위가 전부 [확인 필요
   #40·#41] 이라 실측 전 꺼 둔다. 실측 뒤에는 파서 상수 표(`kbj/data/private/kis/program.py`)만 고치고 켠다.
8. **텔레그램 없음**: 16:00 수급 리포트에 프로그램 줄을 더하는 것은 사용자 요청이 있을 때(ADR 0018 규칙 유지).

## 3. 결과

- 같은 TR 을 1분마다 두 곳이 부르지 않는다(KIS 버킷 하나 — 초당 4건). 하루 F2 호출 합은 `config/limits.yaml
  kis.daily_soft_cap` 안이고 등록부 시험이 지킨다(FND).
- 하루 운영 시뮬레이션·단위 시험: 정상 정규장 391분 빈 분 0, 실패 주입(429·타임아웃)한 분만 정확히 빈다(재시도는 못 받은 조합만).
- 실데이터 검산은 `python -m kbj.services.engine.verify --program --date <D>`(건수만 — probe_results §7 #42).
- 남는 위험: 현물 코드·단위·장전 값(#39), 프로그램 TR 칸·단위(#40·#41)를 실측하기 전까지 페이지 1 일부가 '확인 전'.
  P7 poller 이식 때 이 ADR 2-2 를 지키지 않으면 쓰기 주인이 둘이 된다(계약 시험은 WRAP·P7 이 더한다).

## WRAP 확정 메모(2026-10-13 — 웨이브 3)

1. 상태 **채택**. 등록부: `flows.market_minute` 켬(K2I 3조합 — GX 2026-09-28 실측), `program.intraday`·`program.stock_scan`·
   `program.daily`·`program.stock_sweep`·`program.backfill` 은 실측 #40·#41 전이라 꺼짐(노트에 막는 번호). `ENABLED_BY_PHASE["P5"]`
   (`tests/unit/scheduler/test_registry_validation.py`).
2. 하루 운영 시뮬레이션(`tests/sim/test_one_day_p5.py` — 2026-11-17, 꺼진 F2 작업은 등록부 사본에서 켬): 정규장 391분 × K2I 3조합
   빈 분 0, `verify_program_day` 검산 ①~④ 실패 0, 순위 표 범위 꼬리표(장중 '거래대금 상위 N + 관심종목'·마감 '전 종목'),
   `program.stock_sweep` 18:10 시작, KIS 어떤 1초에도 ≤ 4·발급 auth 만. 실패 변형(`tests/sim/test_intraday_minute.py`): 09:30~09:32
   1분 TR 500 → 그 세 분만 실패·빈 분 = 실패한 분(0 으로 채우지 않는다).
3. metrics §21 품질 줄은 F2 구현대로 확정(슬롯 + 45초 안 ok, 늦으면 estimated, 검산 실패 분 invalid, '잠정' 꼬리표는 장중 내내).
   투신 카드는 사모 제외(사용자 결정 Q7 — KRX 표기, 사모는 기관 세부 선).
4. 남은 확인: 현물 조합 코드(#39)·프로그램 TR 칸·단위·투자자 코드(#40·#41)·검산 실데이터(#42). 장중 종목 훑기 행 ts = 슬롯 + 5분
   (`SCAN_OFFSET_MIN` — 등록부 `open+5` 와 같이 바꾼다).

## P7 확인 메모(2026-10-13 — P7 WRAP)

- §2-2 운영 조건 해소: GX poller 의 투자자별 폴링(`FHPTJ04030000`·`_investors` 계획)을 지웠다(묶음 ING — legacy poller
  소스 grep 0). `prv_gex.investor_flow` 의 쓰기 주인은 등록부에서 `flows.market_minute` 하나(작업·서비스 통틀어 —
  `ServiceSpec.writes` 검사, `tests/unit/scheduler/test_registry_validation.py`). GX 엔진은 이 표를 **읽기만** 한다
  (`EngineSink` 에 투자자별 쓰기 없음 — sim `tests/sim/test_one_day_p7.py`).
- 위클리 4조합(WKM OC05·OP05, WKI OC04·OP04)을 켰다(D-P7-6 — GX 실측 #13). `flows.market_minute` 은 정규장 391분 × 7조합
  = 하루 2,807 호출(KIS 일 상한 안 — 등록부 시험). 야간 조합은 꺼진 채(#P7-2) — 엔진 `investor_flow` 지표는 야간에 '수집 안 함'.
