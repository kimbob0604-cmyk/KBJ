# ADR 0014 — dart-report 승격과 엑셀 골든(정규화 비교), 재무 2층·분기 환산 하나 (2026-10-07)

상태: **확정**(2026-10-13 — 초안·구현 P4 묶음 R, 번호·상태는 웨이브 3 묶음 S 가 확정 — 아래 'S 확정 메모').
근거: `docs/p4_design.md` D-P4-5~8, §4.5·§4.6·§8.1, ADR 0001 Q4(dart-report `statements.to_quarterly` 기본값), P3 D-P3-10
(board 골든 순서), `legacy/etf_traker/dart-report/CLAUDE.md`(깨뜨리면 안 되는 것·지뢰), CLAUDE.md §4(골든은 허용오차 — 바이트 비교 금지).
구현 예정: `kbj/engines/financials/**`, `kbj/reports/dart_excel/**`, `kbj/services/collectors/fin.py`, `config/dart_report/mapping.yaml`,
`tests/golden/dart_report/**`, `tests/oracles/{xlsx_normalize,formula_eval}.py`, 마이그레이션 `0011_fin.sql`.

## 1. 맥락

- PLAN P4 완료 기준: "dart-report 엑셀이 이식 전과 같다(골든)". legacy 시험은 스모크 하나(`tests_smoke.py` — assert 없음,
  출력 `built. quarters= 14 annual= 4 bridge steps= 9`)다.
- xlsx 는 생성 시각(`docProps`, 요약 시트 '생성 시각' — `excel.py`:107 벽시계)이 들어가 바이트로 같을 수 없다. openpyxl 은
  읽을 때 차트를 버린다 — dart-report 의 실제 버그(OPM 선이 영업이익 행을 그림·이중축)는 차트 XML 에 있다.
- 누적→분기 환산이 세 벌이다(dart-report 차분, ET board '3개월값 우선', SD `dart_collector`). ADR 0001 Q4 기본값은 dart-report.
- 전 상장사 전체 재무제표(`fnlttSinglAcntAll`)는 약 7만 호출로 일 예산 4~5일치다.

## 2. 결정

1. **순서**: ① legacy 원본으로 합성 골든을 먼저 캡처·커밋(G1 빌더 8사례, G2 합성 DART 응답 2개 회사의 전체 파이프라인)
   → ② 함수 본문을 그대로 `kbj/engines/financials`(재무제표·주석 비용·브릿지·수주 — 원문 접근은 Protocol 인자, `print` 는
   `log` 인자)와 `kbj/reports/dart_excel`(빌더·파이프라인·작업·CLI)로 → ③ 골든 비교 녹색 → ④ legacy `dartreport/*.py` 는
   kbj 를 다시 내보내는 shim, `config/mapping.yaml` 은 `config/dart_report/mapping.yaml` 로 `git mv`, Streamlit `app.py` 삭제
   (페이지 8 '리포트' 탭이 대체).
2. **골든 = 정규화 비교**: 시트 순서·크기, 셀 값(수식 문자열 정확히, 실수 `isclose(rel 1e-9, abs 1e-9)`), 글꼴·채움·테두리·정렬·
   서식, 열 너비·행 높이·병합·눈금선, **차트·드로잉 XML 은 zip 에서 꺼내 C14N 정규화**해 정확히. 제외: `docProps/*`, '생성 시각'
   값 칸(자리표시). 작은 수식 평가기로 모든 수식을 계산해 오류 0·IFERROR 밖 `#DIV/0!` 0·수식 개수 고정.
3. **실데이터 골든 G3** 는 키 수령 뒤 — DART 는 공개 등급이라 실응답 fixture 를 커밋할 수 있다. shim 뒤에도
   `make_golden.py --legacy-commit <shim 전 커밋>` 이 `git archive` 로 그 커밋의 dart-report 를 임시 폴더에 풀어 실행한다.
4. **legacy 동작은 버그까지 고정**한다(예: 누적값 0 을 `or` 로 당기값으로 바꾸는 `_pick`:116). 고칠 때는 새 골든 세트 + 새 ADR.
5. **재무 2층**: 주요계정(전 상장사, `fnlttMultiAcnt` 100개 묶음)과 전체 재무제표(상세 대상 = 관심종목 ∪ 컨센서스 유니버스 ∪
   공개 데모 — 원시 행 `pub_fin.statement_raw`). 리포트 파이프라인은 원시 행이 있으면 그것을 읽는다(같은 행이면 같은 결과 —
   G2 가 확인).
6. **분기 환산은 하나**(`to_quarterly`, Q4 기본값). ET 의 3개월값은 값으로 쓰지 않고 `pub_fin.quarterly` 의 대조 열로 남기며,
   1% 넘게 다르면 `quality=estimated` + 사유. Q4 의 20종목 대조(#36) 결과로 ADR 0001 Q4 를 확정한다.
7. 리포트 화면 payload 와 엑셀은 **같은 입력**에서 만든다(화면과 엑셀 숫자가 갈라지지 않게). 엑셀은 `pub_fin.report.xlsx`
   (≤ 2 MiB)에 저장하고 API 는 저장본만 내려준다(API 는 외부 호출 없음).
8. **경계(2026-10-08 — P3 코드 확인)**: 엔진은 `kbj.data` 를 import 못 한다(import-linter ⑨) — DR 원본의 `from .client import REPRT`
   는 `kbj.core.fin_rows.REPRT`(P2 `kbj/data/public/dart/client.py` 에서 옮기고 그 모듈은 다시 내보냄)로 바꾼다. 원문·재무제표 접근은
   Protocol 인자. 재무 수집 작업은 하루 여러 번 도므로 `as_of: minute`, 데이터 키(재무제표·주요계정 묶음·주식총수)는 전부 `event` 로
   처리기가 직접 선점한다(실행기는 event 키를 미리 잡지 않는다). 5년 백필은 `run-once fin.backfill` + `config/fin.yaml backfill`,
   DART 백필 상한은 `DartClient.from_settings(…, backfill=True)`(KRX 와 같은 모양) — `docs/p4_design.md` §0.4·D-P4-25.

## 3. 결과

- "이식 전과 같다"가 합성 골든으로 CI 에서 매번 증명되고, 실데이터는 체크리스트로 이어진다.
- 새 런타임 의존성: `openpyxl`·`beautifulsoup4`(`html.parser` — lxml 불필요).
- dart-report 의 '깨뜨리면 안 되는 것'(합계·비율은 수식, 파란 원천·검은 수식, 브릿지 잔차 플러그, 재계산 오류 0)이 시험이 된다.
- 남은 [확인 필요]: 비12월 결산의 `bsns_year` 뜻(#34), 지배순이익·감가상각·차입금 태그(#35), openpyxl 판 올림 때 골든 재캡처 절차.

## 4. 구현 기록 (묶음 R, 2026-10-08 — 초안, S 가 확정)

- **골든 캡처**: `tests/golden/dart_report/make_golden.py` — shim 전 커밋 `607b761` 의 dart-report 를 `git archive` 로 풀어
  돌린다(레포 상태를 바꾸지 않고, shim 뒤에도 같은 legacy 로 다시 캡처 가능). G1 빌더 8사례(`inputs/<사례>.json` — 스모크 그대로·
  OFS 혼재·직전 분기 없음·빈 블록·미매핑 0·잔차 큰 브릿지·음수/적자·3M/1H/9M 라벨) → `builder/<사례>.json.gz`(중간값 + 정규화 엑셀 +
  수식 수 — 스모크 197개는 DR CLAUDE.md 기준과 같다). G2 합성 세계 2회사(#1 994010 연결·별도·본문 주석 셋, #12 994110 OFS·천원) →
  `pipeline/<종목>.json.gz`(DART 호출 순서·로그 줄·`build_workbook` 입력·정규화 엑셀). `META.json` 에 legacy 커밋·openpyxl 판·매핑
  sha256·체크섬. 캡처는 결정적이고(두 번 같은 체크섬) lxml 유무(openpyxl 직렬화기)에 무관하다(`OPENPYXL_LXML=False` 로 확인).
- **정규화**(`tests/oracles/xlsx_normalize.py`): 차트·드로잉·시트 관계 XML 은 `defusedxml` 로 읽어 이름공간 URI 태그·정렬 속성·
  공백 제거 문자열로(C14N 과 같은 뜻 — 표준 `xml` 모듈을 쓰지 않는다). 수식 평가기(`formula_eval.py`)는 DR 수식 모양(사칙·
  `SUM`·`SUMIF`·`IFERROR`·셀/범위)만 — 모르는 모양은 `Unsupported`(0 으로 계산하지 않음).
- **legacy 동작 목록**(골든이 고정 — 고치려면 새 골든 + 새 ADR): `legacy/etf_traker/dart-report/MIGRATION.md` §3 의 11개(누적 0 의
  `or`, 부호 키워드 '손' 이 평가이익에 걸림, 법인세 수익의 `-abs`, 정정 수주 미제거, 미매핑 금액 중복 합산, 보고서별 CFS/OFS 혼재
  차분, 12월 결산 가정의 보고서 분기 판정, 연간 비용 차트 앵커 겹침, 벽시계 생성 시각, 단계 예외의 경고화 등).
- **의도된 차이 둘**: ① 생성 시각 주입(골든은 자리표시) ② 파이프라인은 수집 실패·예산 닫힘을 경고로 바꾸지 않고 올린다(절대
  규칙 4 — 일부가 빈 리포트를 정상처럼 저장하지 않게). 둘 다 G2 의 정상 경로에는 영향이 없다.
- **매핑 이동**: `config/dart_report/mapping.yaml`(내용 같음 — META `mapping_sha256` 시험). legacy 경로 `config/mapping.yaml` 은
  그 파일을 가리키는 심볼릭 링크(원본 `tests_smoke.py` 를 고치지 않으려고 — 한 벌).
- **재무 2층 정규화**(`kbj/engines/financials/normalize.py`): 연결·별도를 **따로** 환산한다(같은 `to_quarterly` — legacy 리포트의
  보고서별 폴백 혼재 차분을 `pub_fin.quarterly` 에 들이지 않는다). 주요계정 층은 원시 행을 저장하지 않으므로 직전 분기 누적을 저장한
  분기 단독 행의 합으로 되살려 같은 함수에 넣는다. PIT 접수일 = 그 분기 값을 만든 보고서 접수번호 앞 8자리 중 늦은 것.
- **재무 수집 데이터 키**: `DART:fnlttSinglAcntAll` 키에 촉발한 정기보고서 접수번호를 더했다(`<corp>:<연도>:<보고서>:<CFS|OFS>:<접수번호>`
  — 정정본이 새 키로 다시 받힌다) [제안 — 카탈로그 노트는 M]. 상세 대상(관심종목 ∪ 컨센서스 유니버스 ∪ 공개 데모)은 공개 작업이
  로그인 저장소를 import 하지 못하므로(계약 ⑬) 로그인 쪽이 스케줄러 자원 `detail_universe`(호출 가능 — KST 날짜 → 종목코드들)로
  넘긴다 [제안 — M·S]. 없으면 공개 데모만 상세 층(작업 detail `detail_source`).
- **리포트 작업**: 입력 요약(엔진 판·매핑·연도·분기 재무 행·공급계약 공시)이 같으면 DART 를 부르지 않고 건너뛴다. 재무제표는
  `pub_fin.statement_raw` 를 먼저 읽고(같은 행이면 legacy 골든과 같은 엑셀 — `tests/unit/reports/test_job.py`), 원문은 한 실행에서
  한 번만(주석 비용·브릿지가 같은 원문을 두 번 읽던 호출을 반으로). 사업보고서 원문의 부문 매출을 `pub_fin.segment` 에 함께 쓴다
  [제안 — 등록부 `writes` 는 S]. 재무 통화가 원화가 아닌 회사(§8.4 #19)는 만들지 않는다(엑셀이 억원 전제).
- **독립 검증 보강(2026-10-08)**: ① 주요계정 층(`fnlttMultiAcnt`)에는 차입금·단기금융상품·현금 줄이 없으므로 `total_debt`·
  `short_inv`·`net_debt` 를 None 으로 둔다('줄이 없으면 0' [확인 필요 #35] 은 전체 재무제표 층만 — 주요계정 층에 0 이 들어가던 것을
  고쳤다) ② 금융업이고 legacy 매출 이름이 비면 '보험수익'·'순영업수익'(ET `NAMES` 동의어)을 매출 자리에 쓴다(notes, [확인 필요 #35]) ③ 비율
  행에서 기준(CFS↔OFS)이 바뀐 분기는 quality estimated(metrics §9 '혼재 → estimated'). 남은 위험: 정정 보고서는 접수번호가 바뀌어
  그 분기의 PIT 접수일이 정정일로 밀린다(원 공시일 ~ 정정일 사이 TTM 창이 비게 됨 — V·ADR 0016 에서 정할 것), 재무제표 API 가
  아직 비어 있는 정기보고서도 처리 표시가 찍힌다(DART 반영 지연 실측 전 [확인 필요]).

## S 확정 메모 (웨이브 3, 2026-10-13)

- 번호 0014·상태 **확정**. **PLAN §8 P4 완료 기준 ① "dart-report 엑셀이 이식 전과 같다"** 는 `tests/golden/dart_report`(G1 빌더
  8사례·G2 파이프라인 2회사·수식 평가 오류 0·`#DIV/0!` 0·수식 수 고정·메타 — 52 통과, G3 실데이터 1 건너뜀 '키 수령 후 #30')와
  legacy 스모크(`scripts/test_legacy.sh dart-report` — shim 뒤에도 `built. quarters= 14 annual= 4 bridge steps= 9` + 다리
  `tests_bridge.py` 'bridge ok: 5 modules')로 증명한다. CI 의 `test-kbj`·`legacy(etf-rest)` 잡이 자동으로 모은다.
- 정본 경계: `scripts/check_canonical.py` 의 legacy 허용 목록(`LEGACY_KBJ_ALLOW`)에 `kbj.engines.financials`·
  `kbj.reports.dart_excel` 을 더했다(DR `dartreport/*` shim 이 다시 내보내는 모듈 — 그 밖의 kbj 모듈 import 는 여전히 실패).
  계약 ⑪(리포트 → 로그인 원천·컨센서스·밸류 엔진·API 금지)·⑫(공개 엔진 → 로그인 엔진·원천 금지)를 넣었다.
- 등록부: `fin.quarterly`·`fin.backfill`(수동)·`reports.dart_excel` 을 켰다. `reports.dart_excel.writes` 에 `pub_fin.segment`(부문
  매출 — §4 구현 기록). legacy 의존성 그룹에서 `streamlit`(DR `app.py` 만)을 지우고 `uv.lock` 을 다시 만들었다.
- **하루 운영 증명**(`tests/sim/test_one_day_p4.py`): 창 앞 정기보고서는 백필이 받고, 창의 `fin.quarterly` 는 그날 접수된 3분기
  보고서만 받으며(원시 재무 키의 접수번호가 전부 그날 공시), 21:00 `reports.dart_excel` 은 그 뒤에 돌아 그날 보고한 관심종목의
  리포트 마지막 분기가 `3Q26` 이다.
- **[중요 — 운영 공백, 요청] 상세 대상 자원 `detail_universe`**: 공개 작업 `fin.quarterly`·`fin.backfill` 은 계약 ⑬ 때문에
  관심종목·컨센서스 유니버스를 직접 못 읽어 스케줄러 자원 `detail_universe`(KST 날짜 → 보통주 코드)를 받는다. 완료 시험·하루
  시뮬레이션은 이 자원을 준다. **운영 스케줄러(`kbj/services/scheduler/__main__.py`)에는 아직 없다** — 그대로 켜면 상세 층(전체
  재무제표)은 공개 데모 목록만 받는다(작업 detail `detail_source=public_demo_only` — 실패는 아님, EV/EBITDA·밸류 밴드가
  `not_detail_tier`). 관심종목 ∪ 전 거래일 시총 상위 `consensus.top_n`(보통주) ∪ 공개 데모를 돌려주는 자원을 스케줄러에 더해야
  한다(소유 M/메인 — 계산 규칙은 API `readers.stock` 의 상세 대상과 한 벌로).
- **빈 재무 응답**: `fnlttSinglAcntAll` 이 CFS·OFS 모두 비면 그 키는 done(0)·공시에 처리 표시가 찍혀 다시 받지 않는다(키에
  접수번호가 있어 같은 키로는 다시 못 받는다). DART 반영 지연 실측(#30·#35) 전이라 '빈 응답은 N일 동안 다시' 규칙은 만들지
  않는다 [확인 필요 — 실측 뒤 결정].
- 정정 보고서의 PIT 접수일(원 공시일 ~ 정정일 TTM 공백)은 ADR 0016 S 메모에서 정한다. 리츠처럼 6개월 결산인 회사가 4분기 모델에
  맞는지 [확인 필요]. `ops.nightly` 가 `FinRepo.prune_reports`(종목마다 최근 4개)를 부르는 연결은 아직 없다(요청 — P2 작업 소유).
- **[결정 필요 — 메인·사용자] 공개판 재무 파일의 상세 층 흔적**: 공개 `fin/<코드>.json` 이 `tier`·D&A·EBITDA 를 실으면 상세 층
  (관심종목 ∪ 컨센서스 유니버스 ∪ 데모)인 회사가 드러난다 — 시총 상위 밖 관심종목이 공개판에서 추정될 수 있다(D-P4-13 '공개판은
  관심종목을 모른다'). [권고] 공개 데모 밖 회사의 공개 파일은 주요계정 층 칸만 싣고 `tier` 를 빼거나 전 회사 같은 값으로.
- 남은 [확인 필요]: 비12월 결산 `bsns_year`(#34), 지배순이익·감가상각·차입금 태그(#35), openpyxl 판 올림 때 골든 재캡처
  (`make_golden.py --legacy-commit 607b761`).

