# ADR 0013 — DART 공시 수집: 증분 피드·구조화 API 우선·원문 폴백·DB 대기 폴링·공시 알림 정책 (2026-10-07)

상태: **확정**(2026-10-13 — 초안 P4 묶음 F, 구현 F·E, 번호·상태는 웨이브 3 묶음 S 가 확정 — 아래 'S 확정 메모', `docs/p4_design.md` §1.0·§9.2).
**2026-10-08 메인 결정으로 채택**: 알림 정책(6 — 관심종목 잠정실적만 사건 시점 push, 일반 공시 push 없음), 보호예수 추정 해제일만(7).
근거: `docs/p4_design.md` D-P4-3·4·14·18, §3.2·§3.3·§3.5·§4.1~§4.4, `docs/DATA_TIERS.md` §1(DART 공개 ✅확인), ADR 0005(알림 outbox),
ADR 0006(작업 등록부·선점), `docs/conflict_map.md` §1.12, SD `server.py:poll_dart_disclosures`:10166, SD `earnings_parser.py`·
`overhang_parser.py`, ET `board/ingest/dart.py`.
구현: 묶음 F(2026-10-08) — `kbj/data/public/dart/{feed,structured,ownership,prelim_doc,overhang_doc,company,shares,doctext}.py`,
`disclosures.py`(`FEED_KINDS`·`feed_kind`), `kbj/services/collectors/{dart_feed,dart_company}.py`, 가짜 서버
`tests/fakes/dart_server.py`(`world=`), 합성 원문 `tests/fixtures/synthetic/dart/`. 남은 구현: `kbj/services/engine/{filings,earnings}.py`(묶음 E).
이미 있음(묶음 M): 마이그레이션 `0010_filings.sql`, `config/{jobs,notify,fin}.yaml`.

## 1. 맥락

- legacy 공시 수집은 세 벌이다: SD 매분 폴링(`list.json` 을 매번 1쪽부터 최대 10쪽 — 하루 최대 7,800호출), SD 오버행·잠정실적
  파서(정규식·원문 HTML), ET board 의 종목별 당일 공시. 정본 클라이언트는 P2 에 하나로 모였다(`kbj.data.public.dart.client`,
  `check_canonical` 그룹 `dart` 0).
- DART 일 한도 20,000건(예산 18,000)을 공시 피드·재무·리포트·기업개황이 같이 쓴다.
- DART 는 주요사항보고서(CB·BW·EB·유상·무상증자·자기주식)와 지분공시(임원·주요주주 소유보고, 5% 대량보유)를 **구조화
  응답**으로도 준다 [확인 필요 — 엔드포인트 이름·필드는 레포에 없다, 실측 #31~#33]. 잠정실적·전환청구·전환가 조정은 원문뿐이다.
- 작업 등록부의 `triggered_by`(이벤트 연쇄)는 선언만 있고 실행기에 구현이 없다(`kbj/services/scheduler/registry.py`:168).
- SD 에서 사용자는 일반 공시의 텔레그램 발송을 막았다(`server.py:poll_dart_disclosures` 주석 "사용자 요청으로 텔레 발송 차단").
  U2 로 브리핑은 아침·마감 하루 1회씩이다.

## 2. 결정

1. **증분 피드**: `filings.dart_feed`(평일 07:00~19:59 매분)가 그날 `list.json` 을 1쪽부터 읽다 **이미 본 접수번호가 나온 쪽에서
   멈춘다**(정렬 = 접수 내림차순 가정 [확인 필요 #28] — 다르면 전 쪽 읽기로 바꾸고 예산을 다시 잰다). 07:00 첫 실행은 전 영업일
   전체를 한 번 더 훑어 19:59 뒤 접수분을 채운다. 접수 시각은 응답에 없으므로 `first_seen_at`(처음 본 시각)만 남긴다.
2. **구조화 우선, 원문 폴백**: 새 접수번호 중 오버행·자기주식·내부자·대량보유는 구조화 엔드포인트로, 잠정실적·전환청구·
   전환가 조정·결산실적공시예고는 원문(`document.xml`)으로 파싱한다. 구조화 응답의 빈 칸만 SD 정규식(승격)으로 채운다.
   **원문 자체는 저장하지 않는다** — 파싱 결과와 접수번호(공개 뷰어 링크)만.
3. **데이터 키**: 접수번호마다 `DART:<dataset> @ event(rcept_no)` 로 선점한다(같은 공시를 두 번 받지 않음). 한 건 실패는 그
   건만 `parse_state=failed`, 다음 실행들이 3회까지 다시 — 다른 건을 멈추지 않는다(절대 규칙 4). P2 실행기는 `event` 키를 미리
   잡지 않으므로(`kbj/services/scheduler/runner.py:_keys`) **처리기가 자원 `claims`(선점 장부 — 실행기와 같은 저장소)로 `claim →
   complete/fail` 하고 `JobResult.collected` 에는 넣지 않는다**. 실행 키는 `DART:list @ minute` 하나(`as_of: minute`).
4. **연쇄는 DB 대기 상태 폴링**: 이벤트 버스를 만들지 않는다. 피드는 `pub_filings.*` 에 쓰기만 하고, `earnings.alerts`(5분)·
   `fin.quarterly`(30분)·`filings.derive`(30분)가 대기 상태(`parse_state`·`fin_loaded_at`·알림 행 없음)를 읽는다. 등록부의
   `fin.quarterly.triggered_by` 는 지운다.
5. **공개/로그인 분리**: 공시·잠정실적·오버행(주식 수·비율)·내부자(주식 수)·키워드 중요도는 `pub_filings.*`(공개). 관심종목·
   시총 가산 중요도, 오버행 금액·ITM, 내부자 금액, 컨센서스 대비 서프라이즈는 **읽을 때 계산**하고 `pub_*` 에 쓰지 않는다.
   공개 작업 모듈은 로그인 모듈을 import 하지 않는다(import-linter 계약 ⑬ + SQL 스키마 정적 시험).
6. **알림**: 실시간 push 는 `alert.earnings` 하나 — 관심종목의 잠정실적 중 YoY 분류 우선순위 ≤ 2. 대상(subject) =
   `<corp_code>:<회계기간>:<연결/별도>`(정정공시가 다시 울리지 않게 — 접수번호가 아님, ADR 0005 `subject` 규칙 30일).
   본문은 템플릿(숫자는 모두 저장 행에서 — LLM 없음, SD Ollama 작성기 폐기). 일반 공시는 push 하지 않는다. 공시 요약은
   P5 아침 브리핑(U2). 발송은 notifier shim(`notify(…, kind='alert.earnings', subject=…)` — outbox·dedup·쿨다운, ADR 0005)만.
   `alert.earnings` 는 **사건 시점**(작업 `earnings.alerts` 5분 — 장 마감 뒤 사건도 바로)이고, 시각을 정한 장 마감 뒤 발송은
   ADR 0018 대로 전부 16:00(P4 에서는 리비전 `alert.revision` 하나). 잠정실적 일괄 요약을 만들게 되면 16:00.
7. **보호예수**: 출처가 없다. 법정 전매제한에서 계산한 **추정 해제일**만(`quality=estimated`, 화면 '추정' 배지), 예탁결제원 출처는 확인 뒤
   데이터셋을 더한다. KRX KIND 스크랩은 하지 않는다(`krx_scrape` 그룹).

## 3. 결과

- 평일 DART 호출 약 1,400(시즌 약 4,200) — 예산 안(`docs/p4_design.md` §3.5). SD 의 '매분 처음부터 10쪽'이 사라진다.
- 오버행은 결정 공시뿐 아니라 전환가 조정·청구 행사를 반영한다(못 읽으면 상한값 + `estimated`).
- SD 의 공시·어닝·오버행 모듈과 `server.py` 공시 함수는 P4 묶음 S 가 지운다.
- 남은 [확인 필요]: 목록 정렬(#28), 구조화 엔드포인트·필드(#31~#33), 잠정실적 원문의 기간 표기(#30), 정정 제목 머리 종류,
  휴장일 접수(12/31·근로자의 날 — 그래서 `when: always`).

## 4. 구현 메모 (묶음 F — 2026-10-08, S 가 확정할 때 함께 본다)

1. **피드 종류는 `KINDS` 를 고치지 않고 `FEED_KINDS`·`feed_kind()` 로 따로** 두었다. 설계(§1.3)는 새 종류를
   `KINDS` 앞에 넣으라 했지만 그러면 `kind_of()` 결과가 바뀌어 P2 승격 원본 시험(ET `test_dart_for.py::test_mapping`
   — 잠정실적 → earnings, 소유보고 → owner)이 깨지고 legacy board 표시가 바뀐다. `pub_filings.disclosure.kind` =
   `feed_kind()`(잘게 나눈 종류 `pre_announce`·`prelim`·`overhang_exercise`·`price_adjust`·`overhang`·`buyback`·
   `insider`·`major_holder`·`periodic` 먼저, 안 걸리면 P2 종류). '실적등에대한전망'은 `earnings`(잠정실적 아님).
2. **선점 키**: 실행 키는 `DART:list @ <분>`(실행기). 접수번호 키는 처리기가 `claims` 자원으로 `DART:<데이터셋> @
   <접수번호>` 를 잡는다(잠정·예고·행사·조정 = `document`, 결정 = 구조화 데이터셋, 소유보고 = `elestock`, 대량보유 =
   `majorstock`). 원문 보충·연결 폴백·소유보고 사유처럼 같은 공시를 처리하며 부르는 다른 호출은 그 키 안의 일이다.
   07:00 다시 훑기는 `DART:list @ <전 거래일>~<어제>` 키로 하루 한 번(같은 날 두 번 훑지 않는다). run-once 가 미리
   잡은 키(`<dataset>@<as_of>`)는 `JobResult.collected` 로 그대로 돌려준다.
3. **실패 구분**: 해석 실패(표 없음·형식)·DART 의 그 건 오류(014·100 등) = 그 건 `failed`(시도 + 1, 3회면 `na` +
   사유). 전송 오류·5xx·점검(800)·900 = 그 실행의 파싱을 멈추고 실행 실패 — **시도 수를 쓰지 않는다**(다음 분 다시).
   `020`·예산 소진 = 그날 파싱 중단, 실행 실패(사유). 키·IP 오류 = 그대로 올린다(critical 알림).
4. **결산실적공시예고**는 표를 쓰지 않고 해석 결과를 `parse_note`(`planned=YYYY-MM-DD;time=HH:MM;target=…` —
   `prelim_doc.encode_pre_announce`)에 남긴다. 실적 일정 표(`earnings_schedule`)는 `filings.derive`(묶음 E)가
   `decode_pre_announce` 로 읽어 만든다 — 두 작업이 한 표를 쓰지 않게(설계 §1.3 의 '각 표'에 예고의 표가 없었다).
5. **결정 공시**: 구조화 응답에 같은 접수번호 행이 있으면 그것(`parse_source=api`), 필수 칸이 비면 원문으로 그 칸만
   채우고 `notes` 에 '원문 보충: …'(유상증자의 발행가·신주 상장 예정일은 구조화 응답에 없다 [확인 필요 #31]). 행이
   없으면 원문만으로(`parse_source=document`). 둘 다 비면 그 칸은 None + `estimated`.
6. **조정·행사의 대상 잇기**: 같은 회사·상품·회차 결정 공시 중 가장 늦은 접수번호. 저장소에 없으면(피드가 생기기 전
   발행) 그 상품의 구조화 API 를 접수일 7년 전부터 불러 옛 결정을 넣고 다시 잇는다. 그래도 없으면 `quality=invalid` +
   사유(행은 남긴다).
7. **소유보고·대량보유**: 회사 보고 전체 중 저장소에 없는 접수번호를 모두 넣는다(과거 보고도 — 창 합계의 재료).
   그날 공시 행에만 원문 '변동사유'를 붙인다(구조화 응답에 사유 칸이 없다고 본다 [확인 필요 #32]). 응답에 그 접수번호가
   없으면(반영 지연) 그 건 실패 — 3회면 `na`(과거 행은 다음 소유보고 때 들어온다).
8. **비상장 공시는 저장만** 하고 파싱하지 않는다(상장사만 — 예산). 한 실행의 파싱 상한 200건, 다시 훑기 상한
   100쪽 [제안].
9. ~~알려진 한계: 증분 훑기가 30쪽에서 끊기면 그날 남은 목록은 다음 날 07:00 다시 훑기가 채운다~~ — 검증에서 고침(아래
   17).
10. **주식총수**: 발행 주식 수가 있는 행의 자기주식 `-` 는 0 으로 읽는다(None 이면 유통 주식 수·BPS 분모를 모른다 —
    [확인 필요 #33]).

### 4.1 독립 검증에서 고친 것 (묶음 F 검증 — 2026-10-08)

11. **같은 작업의 동시 실행**: 실행기는 as_of(분)가 다른 실행을 동시에 돌리고, 선점 장부는 **같은 작업이면** 잡힌 키를
    다시 잡게 해 준다(run_id 를 바꿔 — 재기동이 이어 쓰게). 그래서 앞 분 실행이 아직 도는 동안 다음 분 실행이 같은
    접수번호를 또 받고(호출 두 번), 먼저 끝난 쪽이 `done` 으로 만든 키를 늦은 쪽이 `complete` 하다 `StoreError` 로 실행이
    실패했다. 처리기가 `claim` 전에 `live` 를 보고 **같은 작업의 다른 run_id 가 `CLAIM_LIVE_MIN`(10분 [제안]) 안에 잡은
    키는 바쁨**으로 건너뛴다(그보다 오래된 선점은 죽은 실행 — 이어 잡는다). 07:00 다시 훑기 키도 같은 규칙(전에는
    `claimed` 로 남은 키가 그날 다시 훑기를 영영 막았다).
12. **예상 밖 오류도 그 건만**: 해석기의 예외가 `ParseError`·`ValueError`·`TypeError`·`KeyError` 가 아니면(예: `decimal`
    의 `InvalidOperation` — ArithmeticError, 해석기 버그) 그 실행 전체가 멈추고, 선점은 `claimed` 로 남고, 시도 수가
    오르지 않아 같은 공시가 매분 실행을 실패시켰다. 이제 그 건만 `failed`(traceback 로그 + `parse_note` 사유, 3회면
    `na`). `doctext.parse_int`·`parse_float` 는 NaN·무한·bigint 밖 값을 None(엄격이면 ValueError)으로, 잠정실적 금액
    환산은 정밀도·bigint 초과를 `ParseError` 로 바꾼다.
13. **다시 훑기 실패의 격리**: 전 영업일 다시 훑기가 실패하면(전송·DART 오류) 그 실행을 실패(사유)로 남기되 그날 증분·
    파싱은 계속 돈다(전에는 예외가 그 실행을 끝내 증분이 멈췄다). 키는 실패로 놓아 다음 분 실행이 다시 훑는다. 일 한도·
    예산(증분도 같은 한도)·키 오류(critical)는 그대로 올린다.
14. **분기말을 넘긴 정정 잠정실적의 기간**: 본문에 기간이 없어 접수일로 추정한 정정 공시는 원 공시(같은 회사·범위의 앞
    잠정실적 중 가장 늦은 것, 92일 안 [제안])의 기간을 따른다(`notes` 에 남김). 접수일 추정은 '접수일 직전에 끝난
    분기'라 2분기 실적을 10월에 정정하면 3분기가 되어 알림 대상(`회사:기간:범위`)이 갈라지고 같은 실적이 두 번 울렸다.
15. **행사·청구 내역 표**: 회차 칸이 숫자가 아닌 행(합계·소계)에서 내역이 끝난다 — 합계 행을 한 번 더 더하지 않는다(전에는
    청구 금액이 두 배가 되었다). 한 공시에 회차가 둘 이상이면 **첫 회차 행만** 합해 그 결정 공시에 잇는다(조정 표는
    접수번호마다 한 행 — 다른 회차의 청구를 첫 회차에 더하면 첫 회차 잔여가 실제보다 줄어든다). 다른 회차는
    `AdjustParse.other_seqs`·notes 로 남기고 행은 `estimated`(그 사채의 잔여는 상한 — §4.3 '청구분 미반영'과 같은 방향).
    가액 조정의 다른 회차도 같다. 회차 표기('3'·'03'·'제3회'·'3회차')는 숫자로 맞춰 잇는다. 다회차 공시를 회차마다
    저장하려면 `overhang_adjust` 키가 `(rcept_no, target_rcept_no)` 여야 한다 — 0010 변경(묶음 M·S 판단).
16. **잠정실적 표의 빈 흑자적자전환 칸**: SD 정규식의 전환 칸 `(\S+)` 는 칸이 비어(태그를 지우면 사라진다) 다음 열(전년동기
    숫자)을 먹어 전년동기·증감율이 한 칸 밀려 읽혔다(예 `100 50 100.0 80 25.0 -` → 전년동기 25.0). 전환 칸은 글자 또는
    `-` 만 받고, 전환 칸이 빠진 5개 값 행도 6열 폼으로 읽는다(SD 는 2열로 읽어 전년동기를 버렸다). 실측 폼(#30)으로 다시
    본다.
17. **증분이 `max_pages` 에서 끊기면 같은 실행에서 이어 읽는다**: 다음 실행의 증분은 1쪽에서 이미 본 접수번호를 만나
    멈추므로, 끊긴 뒤쪽(그날 앞선 접수분 — 장애 뒤 첫 실행·실적 시즌 마감일)은 다음 날 07:00 다시 훑기까지 빠졌다(그
    사이 관심종목 잠정실적이 하루 늦게 울린다). 이제 끊기면 같은 실행에서 그날 목록 전체를 `full=True`(이미 넣은 것은
    거름, 상한 `RESCAN_MAX_PAGES` 100쪽)로 이어 읽는다(설계 §4.1 '다음 분에 이어서'). 하루 1만 건을 넘는 날만 다음 날
    다시 훑기로 넘긴다(경고).

## S 확정 메모 (웨이브 3, 2026-10-13)

- 번호 0013·상태 **확정**. 등록부에서 `filings.dart_feed`·`filings.company_profile`·`filings.derive`·`earnings.alerts`·
  `earnings.backfill` 을 켰다(`ENABLED_BY_PHASE["P4"]` — `tests/unit/scheduler/test_registry_validation.py`). 공개 작업의 경계는
  import-linter 계약 ⑬(공개 작업 → 로그인 원천·엔진·저장소 금지)과 정적 시험 `tests/unit/test_public_writers.py`(공개 작업과 그
  저장소의 SQL 스키마는 `pub_` 만 — 관심종목을 읽는 dart-report 작업의 `prv_journal` 하나만 예외)가 지킨다.
- **하루 운영 증명**(`tests/sim/test_one_day_p4.py` — 2026-11-16 05:00 ~ 11-17 05:00, 3분기 보고 마감일, 실제 처리기 + 합성
  20종목 세계): `filings.dart_feed` 780회 모두 ok, DART 데이터 키(분 780·다시 훑기 1·접수번호)마다 done 1회·선점 거절 0, DART
  어떤 1초에도 ≤ 8, 하루 DART 1,151건(2026-10-13 다시 잼 — 합성 20종목 세계라 실제 시즌 값이 아니다. 예산 카운터 = 실제 호출,
  18,000 안), 관심종목 잠정실적 `alert.earnings` 1통(16:20 정정 공시
  뒤에도 1통 — 처리기의 subject 기록), 일반 공시 push 0. 변형: 14:00 `020` → 그날 DART 호출 0·이후 피드 실행 실패(사유),
  다음 날 07:00 다시 훑기가 그날 공시를 빠짐 0 으로 채운다.
- **§4-1 확정**: 피드 종류는 `KINDS` 를 고치지 않고 `FEED_KINDS`·`feed_kind()` 로 따로 둔다(설계 §1.3 의 'KINDS 앞에 새 종류'
  문구를 대체) — P2 승격 원본 시험(ET `test_dart_for.py::test_mapping`)과 legacy board 표시를 지키는 쪽이 맞다.
- **처리기 상수는 모듈 상수 그대로**: `CLAIM_LIVE_MIN`(10분)·`CORRECTION_LOOKBACK_DAYS`(92일)·`RESCAN_MAX_PAGES`(100쪽)·
  `PARSE_LIMIT`(200건)·E 의 `PRELIM_LOOKBACK_DAYS`(30일)·`ALERT_FRESH_TRADING_DAYS`(1)·`SCORE_LOOKBACK_DAYS`(7일)·
  `CHANGE_WINDOW`(90분)·`OVERHANG_GRACE_DAYS`(30일)는 사용자가 고를 값이 아니라 처리기의 안전 장치라 `config/fin.yaml` 로 옮기지
  않는다(전부 [제안] — 실측 #28·#30 뒤 다시 본다). 사용자가 고를 값(알림 우선순위 상한·경계·창)은 이미 `config/fin.yaml`.
- **알림 기간 규칙·NO_DATA**(묶음 E): 미처리 잠정실적은 최근 30일에서 찾고, 접수일이 실행일의 1거래일 앞보다 이른 옛 공시는
  '사건 시점' 알림으로 보내지 않고 기록만 한다. 전년동기 값이 없는 잠정실적(2열 폼)은 NO_DATA(5 — 알림 대상 아님).
  `docs/metrics.md` §14 에 옮겼다.
- **[결정 필요 — 사용자] YoY 분류 역전**: SD 순서를 그대로 승격해 적자 축소 20~49% 는 YOY_BIG_UP(1), 50% 이상은
  TURNAROUND_PARTIAL(2)다(더 크게 개선됐는데 우선순위가 낮아짐 — metrics §14 ⚠). 관심종목 알림 대상이 바뀌므로 규칙을 바꾸려면
  새 ADR + 분류 시험. 그 전까지 legacy 동작 그대로.
- **다회차 조정 공시(§4-15)**: `overhang_adjust` 키는 접수번호 하나 그대로 둔다 — 첫 회차만 잇고 다른 회차는 estimated + notes.
  실측 #31 에서 다회차 공시가 흔하면 새 마이그레이션(0014 —)으로 키를 `(rcept_no, target_rcept_no)` 로 바꾼다(결정 보류). 그 사이
  E 의 사채 상태가 notes 의 다른 회차를 '청구분 미반영' estimated 로 보게 하는 보강은 요청으로 남겼다.
- **공개 잠정실적 파일의 YoY 분류(설계 §7)**: 공개 `earnings_prelim.json` 에는 아직 신호·등급이 없다(분류 규칙이 공개 엔진
  `kbj.engines.filings.earnings` 에 있는데 계약 ⑧ 이 공개 내보내기의 엔진 import 를 막는다). **[결정 필요 — 메인]** ① [권고]
  ⑧ 의 금지 목록을 로그인 엔진으로 좁혀 순수·공개 엔진 `kbj.engines.filings`·`financials`(⑫ 가 로그인 import 를 막는다)를 허용 —
  읽는 표는 그대로 `pub_*` 만, 분류 구현은 한 벌 ② E 가 `pub_filings.earnings_prelim` 에 분류 열을 쓰는 마이그레이션. 고르는 쪽이
  X 의 `tests/unit/public_export/test_import_boundary.py` 를 같이 고친다.
- 남은 [확인 필요]: 목록 정렬(#28), 잠정실적 원문 폼·기간·4분기 '당해실적'(#30), 구조화 엔드포인트·필드·7년 조회(#31),
  `elestock`·`majorstock` 필드·사유 칸(#32), 주식총수 '-' 의 뜻(#33), 비12월 결산 회계연도 이름(#34). 체크리스트는
  `docs/probe_results.md` §7.

