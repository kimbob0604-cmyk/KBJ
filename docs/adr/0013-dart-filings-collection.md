# ADR 0013 — DART 공시 수집: 증분 피드·구조화 API 우선·원문 폴백·DB 대기 폴링·공시 알림 정책 (2026-10-07)

상태: **제안**(P4 설계 초안 — 묶음 F 가 구현하며 고치고, 묶음 S 가 웨이브 3 에 번호·상태를 확정한다, `docs/p4_design.md` §1.0·§9.2).
근거: `docs/p4_design.md` D-P4-3·4·14·18, §3.2·§3.3·§3.5·§4.1~§4.4, `docs/DATA_TIERS.md` §1(DART 공개 ✅확인), ADR 0005(알림 outbox),
ADR 0006(작업 등록부·선점), `docs/conflict_map.md` §1.12, SD `server.py:poll_dart_disclosures`:10166, SD `earnings_parser.py`·
`overhang_parser.py`, ET `board/ingest/dart.py`.
구현 예정: `kbj/data/public/dart/{feed,structured,ownership,prelim_doc,overhang_doc,company,shares}.py`,
`kbj/services/collectors/{dart_feed,dart_company}.py`, `kbj/services/engine/{filings,earnings}.py`, 마이그레이션
`0010_filings.sql`, `config/{jobs,notify,fin}.yaml`.

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
   건만 `parse_state=failed`, 다음 실행들이 3회까지 다시 — 다른 건을 멈추지 않는다(절대 규칙 4).
4. **연쇄는 DB 대기 상태 폴링**: 이벤트 버스를 만들지 않는다. 피드는 `pub_filings.*` 에 쓰기만 하고, `earnings.alerts`(5분)·
   `fin.quarterly`(30분)·`filings.derive`(30분)가 대기 상태(`parse_state`·`fin_loaded_at`·알림 행 없음)를 읽는다. 등록부의
   `fin.quarterly.triggered_by` 는 지운다.
5. **공개/로그인 분리**: 공시·잠정실적·오버행(주식 수·비율)·내부자(주식 수)·키워드 중요도는 `pub_filings.*`(공개). 관심종목·
   시총 가산 중요도, 오버행 금액·ITM, 내부자 금액, 컨센서스 대비 서프라이즈는 **읽을 때 계산**하고 `pub_*` 에 쓰지 않는다.
   공개 작업 모듈은 로그인 모듈을 import 하지 않는다(import-linter 계약 ⑬ + SQL 스키마 정적 시험).
6. **알림**: 실시간 push 는 `alert.earnings` 하나 — 관심종목의 잠정실적 중 YoY 분류 우선순위 ≤ 2. 대상(subject) =
   `<corp_code>:<회계기간>:<연결/별도>`(정정공시가 다시 울리지 않게 — 접수번호가 아님, ADR 0005 `subject` 규칙 30일).
   본문은 템플릿(숫자는 모두 저장 행에서 — LLM 없음, SD Ollama 작성기 폐기). 일반 공시는 push 하지 않는다. 공시 요약은
   P5 아침 브리핑(U2).
7. **보호예수**: 출처가 없다. 법정 전매제한에서 계산한 **추정 해제일**만(`quality=estimated`), 예탁결제원 출처는 확인 뒤
   데이터셋을 더한다. KRX KIND 스크랩은 하지 않는다(`krx_scrape` 그룹).

## 3. 결과

- 평일 DART 호출 약 1,400(시즌 약 4,200) — 예산 안(`docs/p4_design.md` §3.5). SD 의 '매분 처음부터 10쪽'이 사라진다.
- 오버행은 결정 공시뿐 아니라 전환가 조정·청구 행사를 반영한다(못 읽으면 상한값 + `estimated`).
- SD 의 공시·어닝·오버행 모듈과 `server.py` 공시 함수는 P4 묶음 S 가 지운다.
- 남은 [확인 필요]: 목록 정렬(#28), 구조화 엔드포인트·필드(#31~#33), 잠정실적 원문의 기간 표기(#30), 정정 제목 머리 종류,
  휴장일 접수(12/31·근로자의 날 — 그래서 `when: always`).
