"""ops — 운영 작업과 운영 점검.

- 등록부 작업 `ops.nightly`(백업·보존 기간 정리 — 설계 §6.7).
- 운영 점검 `python -m kbj.services.ops check|week`(docs/p9_design.md §6.2, ADR 0037) — `check`·
  `source` 모듈은 로그인 API(`/api/ops/*`)도 같은 함수로 쓴다.
"""
