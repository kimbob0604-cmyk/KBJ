"""수급 리포트(`flows.report` — 16:00 거래일, ADR 0018) — ET `monitor/flow` 승격
(docs/p5_design.md §1.5).

모듈: `select`(보드 신고가 ∧ 거래량 대상)·`analyze`(표·누적·검산)·`narrative`(규칙 문장)·`returns`
(수익률)·`chart`(차트 4장 — matplotlib·한글 폰트)·`compose`(본문)·`run`(원장 읽기·발송·처리기).
등록부 owner `kbj.reports.flows:daily`. 저장소만 읽는다(계약 ⑯ — 발송 시각 외부 호출 0).
"""

from kbj.reports.flows.run import daily

__all__ = ["daily"]
