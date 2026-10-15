"""테마 엔진(순수 — 계약 ⑨): 테마 마스터 해석·검증(`master`)과 테마 강도·밸류체인 묶음(`strength`).

ADR 0039, docs/metrics.md §31. 입력은 받은 파일 바이트·행 자료형(`kbj.core.theme_rows`·
`kbj.core.rows.StockDay`)·설정 모델(`kbj.config.theme`)뿐 — 어댑터·저장소·서비스를 모른다. 강도
집계는 보드 엔진의 롤업(`kbj.engines.board.aggregate.rollup` — 시총 가중)을 그대로 쓴다(두 벌 금지).
"""
