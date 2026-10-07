"""KIS 어댑터(로그인 등급 — DATA_TIERS §1) — 자격·토큰 읽기·REST·마스터·데이터셋(설계 §1.2·§3).

- `credentials` 앱키·시크릿·환경과 REST 주소(비공개 상수), GX 와 같은 토큰 `owner`
- `token` Redis 토큰 캐시와 **읽기 전용** `reader`(발급하지 않는다 — 발급은 `kbj.services.auth` 만,
  ADR 0004), 거절 신고판
- `rest` `KisRestClient` — 읽기 전용 토큰 + 앱키당 레이트리미터
- `master` 지수선물옵션 마스터 파서·내려받기
- `datasets` KIS 논리 데이터셋(`DATASETS`)

하위 모듈을 바로 import 한다(`from kbj.data.private.kis.token import reader`). 여기서 다시 내보내지
않는다 — 패키지를 import 하는 것만으로 Redis·httpx 를 끌어오지 않게.
"""
