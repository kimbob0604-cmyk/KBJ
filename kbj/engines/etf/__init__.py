"""ETF 수급 엔진 — 순수 계산(docs/p3_design.md §1.5·§4.3, docs/metrics.md §4·§8).

- `types`: 테마 분류 `classify`(ET `etf_tracker_v9/themes.py` 승격 — ADR 0001 Q8)·유형 7분류
  `etf_type`(metrics §8.1)·레버리지 배수
- `splits`: 분할·병합 감지(`detect_split`)와 전날 행 보정(`adjust`) — metrics §8.2
- `flows`: 순유입·가격효과·검산 ③(`daily_flow`·`check3`)·기간 창·유형별 합 — metrics §4
- `premium`: 괴리율·경고(metrics §8.3)
- `holdings`: 구성종목 변동(ET `tracker.py:fund_pairs`·`analyze` 승격)

I/O 를 하지 않는다(계약 ⑨ — `kbj.core` 만 쓴다). 튜닝값(`config/markets.yaml` `etf.*`)은 부르는
쪽(수집 처리기·API)이 `kbj.config.markets.load_markets()` 로 읽어 인자로 넘긴다.
"""
