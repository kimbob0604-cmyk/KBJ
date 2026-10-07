"""KRX OpenAPI 어댑터(로그인 등급 — DATA_TIERS §1, 원천 데이터 재배포 금지).

- `client` `KrxClient` — 일별 엔드포인트 호출(인증키 `AUTH_KEY` 헤더, 크기·시간 상한, 리미터
  `rl:krx:<키 해시>`·일 예산 `krx:calls:<YYYYMMDD>`)
- `models` 응답 행 모델 — 파생(GX 승격: `KrxFuturesDaily`·`KrxOptionDaily`), 주식·지수·ETF·ETN
  일별(거래대금·NAV·상장좌수·순자산 — docs/metrics.md §1·§4), 종목기본정보
- `stocks` 주식 일별 행을 저장 모양(`KrxStockRow`)으로 — ET `board/ingest/krx.py` 승격
- `datasets` KRX 논리 데이터셋(`DATASETS`)

하위 모듈을 바로 import 한다(`from kbj.data.private.krx.client import KrxClient`). 여기서 다시
내보내지 않는다 — 패키지 import 만으로 httpx·Redis 를 끌어오지 않게.
"""
