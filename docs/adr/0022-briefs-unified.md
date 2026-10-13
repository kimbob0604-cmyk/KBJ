# ADR 0022 — 브리핑 통합: 아침 브리핑·마감 요약 섹션, 미준비·생략, LLM 없음, 하루 1회, 수급 리포트 승격 (2026-10-13)

상태: **채택**(2026-10-13 — 초안 P5 묶음 BRF, 번호·상태는 웨이브 3 WRAP 이 확정 — 아래 'WRAP 확정 메모').
근거: `docs/p5_design.md` D-P5-14·15·16·17, §1.5·§4.5, 부록 A §20(→ `docs/metrics.md` §20), ADR 0005(notifier
outbox·dedup), ADR 0017(신고가 3축), ADR 0018(장 마감 뒤 발송 16:00), 사용자 결정 2026-10-13(Q4 구루 X 다이제스트
완전 제외, Q5 ETF 리포트 08:20 따로, Q6 간밤 미국 수집 켬, Q8 미 재무부·뉴욕연은 공개).
구현(묶음 BRF): `kbj/reports/briefs/{__init__,__main__,sections,morning_brief,closing_brief,quality,render,job}.py`,
`kbj/reports/flows/{__init__,__main__,select,analyze,narrative,returns,chart,compose,run}.py`,
시험 `tests/unit/reports/{briefs,flows}/**`, 합성 입력 `tests/fixtures/synthetic/flows_report/**`.

## 1. 맥락

- 아침·마감 텔레그램이 세 프로젝트에 흩어져 있었다 — SD `alert_morning_briefing`·`alert_overnight_prediction`·
  `send_us_market_summary_telegram`·`send_closing_market_summary`, ET `bok.yml`·`us-board.yml`·`board@16:10`,
  GX `plan:brief_*`. U2 는 아침 1회·마감 1회다(등록부·notify 정책은 P2 에 이미 하루 1회로 묶였다).
- ET `monitor/flow`(수급 리포트)는 KRX 정보데이터시스템을 긁거나(`krx.py`) KIS 를 직접 불렀다(`kissrc.py`), 가격은
  네이버 일봉 — 모두 kbj 원장에 이미 있는 값이다.

## 2. 결정

1. **처리기 둘** — `kbj.reports.briefs:morning`(08:10, 캐치업 09:00)·`:closing`(16:00, 캐치업 20:30). 섹션 구성·순서·
   제목·문구는 `config/briefs.yaml` 한 곳(해석기 `kbj/config/briefs.py`). 모듈 이름은 `morning_brief`·`closing_brief`
   (처리기 이름 `morning`·`closing` 과 같은 이름의 하위 모듈을 두면 import 순서에 따라 owner 해석이 모듈을 가리킨다).
2. **섹션**(metrics §20): 아침 ①간밤 미국(미국 지수 줄 + US 묶음의 `us_brief` 산출 그대로) ②금리·달러·원자재 한 표
   (`table_series` 순서, 美10년은 DGS10 하나, 전일 대비 = 같은 시리즈의 앞 기간 값과의 차 — `prev_value`(개정 전 값)를
   쓰지 않는다) ③한국 매크로(전일 08:10 이후 새로 공표된 월·분기 시리즈) ⑤일정·어제 결과·예상치(로그인)·오늘 실적
   일정·금통위 D-n ⑦품질 줄. 마감 ①지수(장중 마지막 값)·환율·시장 수급(기준일·원천·quality) ②업종 상위 5·하위 3·특징주
   (보드 events 거래량 이상) ③신고가 3축(종가 기준, 기준 표기 한 줄) ④수급 동향(외국인·기관 순매수 상위·연속 일수)
   ⑤주요 공시(키워드 중요도 '높음' 이상) ⑦품질 줄.
3. **싣지 않는 것**: 아침 ④야간선물·GEX(P7)·⑥구루(P8 — 단 X 다이제스트는 사용자 결정 Q4 로 **어느 단계에도 없다**),
   마감 GEX·시그널·초안·추천(P7·P8)·**프로그램매매 줄(F2)**·K200 선물 줄(P7). `omitted` 섹션은 본문에도 품질 줄에도
   적지 않는다.
4. **재료는 저장소에서만**(계약 ⑯ — 어댑터·수집기·API·다른 묶음 코드 import 없음, 발송 시각 외부 호출 0). 재료가 무른
   의존 시한까지 없으면 '미준비 — <작업>(<예정 시각>)' 한 줄. 재료의 기준일이 기대(아침: 직전 미국·한국 거래일, 마감:
   실행일)보다 이르면 그 줄에 실제 기준일을 적고(ADR 0018 §2-5) 품질 줄 '지연'에 남긴다. 한 섹션의 예외는 그 섹션만
   '미준비'로 바꾸고 로그(`brief_section_failed`)·품질 줄에 사유를 남긴다(격리 — 삼키지 않는다).
5. **빈 섹션**(SD `SKIP_TITLES` 승계): `empty: none_line` 이면 '일정 없음' 한 줄, 그 밖은 제목째 뺀다 — `skip_titles`
   (한국 매크로)·`empty: skip` 이면 경고 로그만, 아니면 품질 줄 '빈 섹션'. '⚠️ 데이터 없음 (점검 필요)' 을 내보내지 않는다.
6. **LLM 없음** — 문장은 템플릿, 수치는 저장소 값 그대로(반올림은 글자로 바꿀 때만).
7. **하루 1회** — `notify(kind='brief.<종류>', as_of=<실행일>)` 한 통(subject 없음 → dedup 키 `brief.<종류>:<날짜>`).
   같은 날 두 번째 접수는 notifier 가 `duplicate` 로 돌려주고 작업은 성공(재시도 0). 4,096자 나눔은 notifier.
8. **수급 리포트 `flows.report` = ET `monitor/flow` 승격** — 선정(`select.py` — 보드 newhigh 산출의 `achieved`, 종가
   기준, 기본 **52주**(역사적 포함), 60일 → 120일(ADR 0017), 거래량 문턱 기본 없음, 상한 12)·분석·문장·수익률·차트 본문
   그대로. 원천은 kbj 원장(`FlowsRepo.window`·`MarketRepo.series`·`BoardRepo.artifact`). KRX 기간합계가 없으므로
   `verified=False`, 대신 ET KIS 경로의 금액 자릿수 대조(수량 x 종가)를 값이 충분할 때만 건다. 일봉은 비수정(KRX)이라
   차트 표기를 그렇게 적는다 [확인 필요 — 분할 구간의 수익률은 보드 분할 가드와 묶기 전까지 틀릴 수 있다].
   발송: 종목마다 차트(최대 4장 — `notify_media`, subject `<코드>:charts`) → 본문(`notify`, Markdown, subject `<코드>`).
   그림이 실패하면 본문을 보내지 않고 작업 실패. 대상 없는 날·모두 건너뛴 날은 한 줄(subject `none`·`skipped`).
   보드 산출이 없거나 달성 0 이면 작업 실패(ET `SystemExit` → `FlowReportError`). 본문에 수급 원장의 실제 기준일·원천·
   quality 한 줄(16:00 은 KIS 마감 값 — 잠정일 수 있다).
9. **시험**: ET `monitor/flow` 시험 104(analyze 17·narrative 16·returns 17·run 54) 승격 — 수 유지, `d60` 단언은 `d120`,
   텔레그램 함수는 notifier 대기열 함수로. `test_kis.py` 14·`test_krx.py` 11(원천 파서)은 폐지 — 대체 시험은
   `tests/unit/kis/test_parse_investors.py`·`tests/unit/collectors/test_market_close.py`(MIGRATION 에 적는다 — WRAP).
   SD `check_brief_sections`·`check_closing_brief`(kbj 해당분)·`check_futures_us_index` 1~3 → `test_sd_rules.py`
   (원본 스크립트는 legacy 에 남는다). 차트 시험은 한글 폰트가 있어야 돈다(건너뛰기로 바꾸지 않는다 — R3).

## 3. 결과

- 아침·마감 본문이 한 곳(config + 섹션 함수)에서 나오고, 단계가 바뀌면 `status: omitted → include` 와 섹션 함수 하나로
  늘어난다(P7 GEX·P8 구루).
- 미리보기 `python -m kbj.reports.briefs preview --kind morning|closing --at <ISO>`(DB 가 없으면 빈 메모리 저장소 —
  모양만, 지어낸 값 없음). 수급 리포트 미리보기 `python -m kbj.reports.flows --at <날짜>`(DB 필요, 보내지 않음).
- 남은 일(WRAP·다른 묶음 요청): 실행기 자원 `notify_media`(지금은 `notify` 의 짝을 찾는다), 하루 운영 시뮬레이션의
  `flows.report` 접수 수(대상 종목 수 x 2 — 하루 1회 규칙은 브리핑만), 미국 지수 목록·공시 '없음' 문구의 설정 칸,
  `briefs.yaml` 의 `guru` omitted 줄 정리(Q4), 등록부 `brief.morning` 의 `ET:xdigest.yml:send` 흡수 표기(Q4 — 폐기).

## WRAP 확정 메모(2026-10-13 — 웨이브 3)

1. 상태 **채택**. 등록부: `brief.morning`·`brief.closing`·`flows.report` 켬. `guru.research` 작업·`ANTHROPIC:guru` 데이터셋·브리핑
   ⑥ 자리표시는 지웠다(사용자 결정 Q4 — P5·P8 모두 아님, absorbs `ET:xdigest.yml:{collect,send}` 는 retired).
2. `config/briefs.yaml` 에 간밤 미국 지수 목록(`us_indexes` — `config/us.yaml indexes` 와 같다·`morning_brief.US_INDEXES` 는 설정
   없는 호출의 기본값)과 섹션별 '없음' 문구(`none_text` — 공시 '오늘 중요 공시 없음')를 더했다. `notify.yaml flows.report`
   `parse_mode: Markdown`. 실행기 자원 `notify_media`(scheduler `__main__`·sim 하네스).
3. 계약 ⑯(briefs·flows·etf 리포트 → 어댑터 원천·HTTP·카탈로그·수집기·API 금지 — `kbj.data.spec` 자료형만 허용).
4. 하루 1회 증명: `tests/sim/test_one_day_p5.py`(아침 접수 1·수신 1, 마감 1, 수급 리포트 16:00 — 대상 종목마다 차트 + 본문,
   `etf.report` 08:20 1 — 브리핑에 세지 않음)·`tests/sim/test_brief_once.py`(08:12 재기동·08:30 캐치업·legacy shim 이중 접수 →
   duplicate·국내 휴장 + 간밤 미국 거래일 → 아침만·둘 다 휴장 → 0). legacy SD 아침 계열 함수는 WRAP 이 지웠다.
5. 하루 운영 시뮬레이션에서 `board.daily` 는 시뮬레이션 처리기라 그날 보드 산출을 창 앞에 합성으로 둔다(하네스 머리말).
   원장 일봉이 비수정이라 분할 낀 구간의 수익률 [확인 필요]는 그대로.
