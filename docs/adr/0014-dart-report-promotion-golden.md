# ADR 0014 — dart-report 승격과 엑셀 골든(정규화 비교), 재무 2층·분기 환산 하나 (2026-10-07)

상태: **제안**(P4 설계 초안 — 묶음 R 이 구현하며 고치고, 묶음 S 가 확정한다).
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

## 3. 결과

- "이식 전과 같다"가 합성 골든으로 CI 에서 매번 증명되고, 실데이터는 체크리스트로 이어진다.
- 새 런타임 의존성: `openpyxl`·`beautifulsoup4`(`html.parser` — lxml 불필요).
- dart-report 의 '깨뜨리면 안 되는 것'(합계·비율은 수식, 파란 원천·검은 수식, 브릿지 잔차 플러그, 재계산 오류 0)이 시험이 된다.
- 남은 [확인 필요]: 비12월 결산의 `bsns_year` 뜻(#34), 지배순이익·감가상각·차입금 태그(#35), openpyxl 판 올림 때 골든 재캡처 절차.
