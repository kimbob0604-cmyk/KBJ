"""시장 엔진 — 시장 거래대금·시장폭·업종 히트맵·상단 띠(docs/metrics.md §6, docs/p3_design.md §4.4).

순수 계산이다(I/O 없음 — 계약 ⑨). 원장은 `kbj.engines.flows.ledger` 하나를 쓴다(metrics §0).
튜닝값은 `config/markets.yaml`(`kbj.config.markets.load_markets()`)을 부르는 쪽이 읽어 넘긴다.
"""
