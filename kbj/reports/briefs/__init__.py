"""아침 브리핑·마감 요약(U2 — 하루 1회씩, docs/p5_design.md §1.5·§4.5, metrics §20, ADR 0022 초안).

등록부 owner `kbj.reports.briefs:morning`(08:10)·`kbj.reports.briefs:closing`(16:00 — ADR 0018).
모듈: `sections`(공통 틀)·`morning_brief`·`closing_brief`(섹션 함수 — 저장소만 읽는다, 계약 ⑯)·
`quality`(품질 줄)·`render`(조립·HTML)·`job`(처리기·발송). 미리보기 `python -m kbj.reports.briefs
preview --kind morning|closing --at <ISO>`(발송 없음). LLM 없음 — 문장은 템플릿.
"""

from kbj.reports.briefs.job import closing, morning

__all__ = ["closing", "morning"]
