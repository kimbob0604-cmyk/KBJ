"""ETF 운용사 구성종목(PDF) 어댑터 — 로그인 등급(운용사 약관이 달라 — DATA_TIERS §1, D-P3-12).

- `datasets` 논리 데이터셋(`DATASETS` — `ETF_ISSUERS:pdf`, 카탈로그 `PLANNED` 에서 옮겼다 — 묶음 M)
- 운용사 9곳 어댑터(`base`·`kodex`·`tiger`·`timefolio`·`sol`·`ace`·`hanaro`·`koact`·`plus`·`rise`)는
  묶음 E3 가 ET `etf_tracker_v9/collectors.py`·`adapters/` 에서 승격한다. 네이버 TOP10 폴백은 버린다
  (ADR 0001 U4).
- 호출은 운용사 호스트별 scoped 리미터 `etf_issuers`(config/limits.yaml)를 탄다.

하위 모듈을 바로 import 한다. 여기서 다시 내보내지 않는다 — 패키지 import 만으로 httpx 를
끌어오지 않게.
"""
