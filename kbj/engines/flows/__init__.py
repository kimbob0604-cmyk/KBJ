"""수급 엔진 — 일별 원장·검산 ①②·연속 순매수·스크리닝.

근거: docs/metrics.md §0~§3, docs/p3_design.md §4.2.

순수 계산이다(I/O 없음 — 계약 ⑨). 입력은 `kbj.core.rows` 의 행, 출력은 이 패키지의 frozen dataclass.
화면·띠·보드는 모두 `ledger.build_ledger` 가 만든 원장 하나에서 계산한다(metrics §0).

- `ledger`: (종목, 날짜) 마다 한 행 — 원천 우선순위(D-P3-7, `kbj.core.rows.row_rank`)로 고른다
- `checks`: 검산 ①(4구분 합 0)·②(7구분 합 = 기관). 불가하면 None + 사유(메인 결정 R2)
- `streak`: 연속 순매수(0원·순매도·거래정지에서 끊김)
- `screen`: 스크리닝 6기준(metrics §3)
- `totals`: 시장 투자자 합계·종목 상세
"""
