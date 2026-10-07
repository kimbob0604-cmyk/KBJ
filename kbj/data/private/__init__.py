"""로그인 등급 수집기 — 본인만 보는 출처(정본: docs/DATA_TIERS.md §1·§3).

README
======
이 폴더가 받은 데이터는 DB `prv_*` 스키마에만 쌓이고 VM 로그인 API 로만 나간다. 공개 내보내기
(`kbj_public_export` 역할, docs/adr/0002-tier-schemas.md)는 이 폴더와 `prv_*` 스키마를 볼 수 없다.
원본 응답과 그것으로 만든 fixture 는 레포에 넣지 않는다 — 테스트는 `fixtures/synthetic/`
합성 데이터로.

출처(DATA_TIERS §1 "로그인") — 모듈 이름은 DATA_TIERS §3 수집기 위치를 따른다
--------------------------------------------------------------------------
- ``kis``              KIS 실시간 시세·호가·체결·투자자·옵션.
                       토큰은 services.auth 만 발급(ADR 0001 U1)
- ``krx``              KRX OpenAPI 일별 시세·업종·선물옵션(하루 호출 한도 공유)
- ``fsc_stock_price``  금융위 주식시세정보(공공누리 4유형 — 제3자 제공·재배포 금지)
- ``fsc_index_price``  금융위 지수시세정보(같음)
- ``yahoo``            Yahoo 미국 지수·종목
- ``naver_search``     네이버 검색 API(뉴스) — 표시 조건이 있다
- ``etf_issuers``      ETF 운용사 구성종목
- ``consensus``        컨센서스(FnGuide 등, 유료·재배포 금지)
- ECOS 타기관 작성 표(802Y001 한국거래소·731Y001 서울외국환중개, 결정 전까지 901Y056·901Y009)
- FRED 저작권 시리즈(S&P·ICE·다우 등)

쓰지 않는 것: 네이버 금융 스크래핑(약관상 크롤링 금지, ADR 0001 U4), KOFIA 사이트 직접 수집.
"""
