"""패시브 지수 정기변경(편입·편출) 예측 엔진 — 순수 계산(P5 2차 묶음 IDX, metrics §24).

근거: docs/p5_index_rebalance_prompt.md §0~§3. 규칙 숫자는 `config/index_reviews.yaml`(해석기
`kbj.config.index_reviews`) 한 곳에서 받는다 — 이 패키지에 숫자 상수를 두지 않는다.

- `fif`: 유동비율 반올림·완충(리뷰 날짜로 고른 규칙)·FOL 조정
- `required`: 조건 문턱·괴리율·충족 판정·필요 주가 상승률(정확 십진 — 역산 검증)
- `cutoff`: 컷오프 추정(시장 커버리지 방식)
- `passive`: 예상 패시브 매매·일평균 거래대금 대비 배수
- `msci`: 컷오프 배수 규칙(MSCI·FTSE 형), `krx`: 시총 순위·완충 규칙(KOSPI200·KOSDAQ150·KRX300 형)
- `backtest`: 예측 대 실제(적중·놓침·오탐)

입출력은 `types`. 시계는 쓰지 않는다(기준일을 인자로 받는다). I/O 없음(계약 ⑨).
"""
