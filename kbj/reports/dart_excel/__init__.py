"""dart-report 승격 — DART 재무 엑셀(6블록 + RAW)과 화면용 블록 JSON(docs/p4_design.md §1.5·§4.6).

- `excel`: 엑셀 빌더(ET `dart-report/dartreport/excel.py` 그대로 — 생성 시각만 주입).
- `pipeline`: 종목 → 리포트 입력(`collect_inputs` — legacy `run.main` 순서·경고 그대로) → 엑셀 +
  블록 (`render`).
- `blocks`: 화면용 블록(엑셀 칸과 같은 값).
- `job`: 등록부 작업 `reports.dart_excel`(매일 21:00 — 관심종목 ∪ 공개 데모 중 재무가 바뀐 종목).
- `__main__`: `python -m kbj.reports.dart_excel --stock 006110 …`(legacy CLI 옵션 그대로).

공개 등급(원천 DART) 산출이다. 로그인 원천 어댑터·밸류·컨센서스 엔진·API 를 import 하지 않는다
(import-linter ⑪ — 묶음 S 가 계약으로 넣는다).
"""
