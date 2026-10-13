"""컨센서스 엔진 — 순수 계산(docs/p4_design.md §1.6·§4.7, docs/metrics.md §10, ADR 0015).

- `target`: 목표가 컨센서스(`target_consensus` — 창 안 증권사별 마지막 의견 하나, 미제시·목표가 ≤ 0
  제외)·투자의견 분포(`opinion_counts` — 매수·중립·매도 매핑 [확인 필요])
- `revision`: 리비전(`revisions` — FY1·FY2 의 매출·영업이익·순이익·EPS + 목표가 평균 × 창
  7·30일, 기준선 = t − W 이전 가장 늦은 스냅), 신호 분류(`classify` — SD
  `revision_calculator.classify_signal`:76 경계, 수치는 `config/fin.yaml consensus.signal_pct`),
  알림 대상 고르기(`alertable`)
- `forward`: 결산연도 FY1·FY2 고르기(`fiscal_years`)·12개월 선행 NTM(`ntm` — 시간 가중)
- `universe`: 수집 대상 규칙(`consensus_na` — 코넥스·보통주 아님 사유, API 와 처리기가 같이 쓴다)

I/O 를 하지 않는다(계약 ⑨ — `kbj.core` 와 설정 자료형만). 튜닝값은 부르는 쪽(수집 처리기
`kbj.services.collectors.consensus`·API)이 `kbj.config.fin.load_fin()` 으로 읽어 인자로 넘긴다.
출처 KIS 가 여러 증권사 평균인지 자체 추정인지는 [확인 필요 #29] — 이름은 부르는 쪽이 정한다.
"""

from typing import Final

__all__ = ["LABEL"]

# 화면·알림의 출처 이름 — KIS 추정실적이 시장 컨센서스(여러 증권사 평균)인지 한국투자증권
# 자체 추정인지 모른다(§4.7, ADR 0015 2-4 — [확인 필요 #29]). 확인 전에는 "컨센서스"라 부르지
# 않는다. API(묶음 A)·수집 처리기·화면이 이 한 곳을 쓴다(실측으로 시장 컨센서스면 바꾼다).
LABEL: Final = "KIS 추정"
