"""재무 엔진 — dart-report 승격 + 분기 정규화·비율·부문(docs/p4_design.md §1.5·§4.5·§4.6).

D-P4-5~8, ADR 0014.

- `statements`·`costs`·`bridge`·`orders`: ET `dart-report/dartreport/*.py` 함수 본문 그대로(골든
  G1·G2 가 legacy 와 같은지 지킨다 — `tests/golden/dart_report`). 바뀐 것은 입출력 경계뿐:
  원문·재무제표·공시 목록은 Protocol 인자(`StatementSource`·`DocumentSource`)로 받고,
  `print`(verbose)는 `log` 인자로.
- `period`·`normalize`·`ratios`·`segments`: P4 새 계산(공개 `pub_fin.quarterly`·`ratio`·`segment`).
- 엔진은 순수 계산이다(import-linter ⑨ — `kbj.data`·`kbj.services`·`kbj.store` 를 모른다).
  보고서 코드 `REPRT` 는 `kbj.core.fin_rows` 에서 가져온다.
"""
