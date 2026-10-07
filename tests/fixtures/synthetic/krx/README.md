# KRX·금융위 시세 합성 fixture (로그인 등급 — 실데이터 아님)

KRX OpenAPI 와 금융위 주식·지수 시세(공공누리 4유형, 원천 KRX)는 로그인 등급이라 실측 원본과 그것으로
만든 fixture 를 레포에 넣지 않는다(CLAUDE.md §2, DATA_TIERS §4, ADR 0001 U3). 이 폴더의 JSON 은 모두
**고정 시드 생성기가 만든 합성 데이터**이고, 머리 칸 `_source` 가 `SYNTHETIC` 으로 시작한다.

| 파일 | 내용 | 생성기 | 쓰는 시험 |
|---|---|---|---|
| `opt_daily.json`·`fut_daily.json` | KRX 파생 일별(`drv/opt_bydd_trd`·`fut_bydd_trd`) 응답 행 모양 | legacy GX `scripts/make_synthetic_fixtures.py`(시드 `kbj-p1-gexlab-synthetic-v1`) — 그 출력(`legacy/gexlab/tests/fixtures/krx/`)과 **바이트까지 같은 복사본** | `tests/unit/data/private/test_krx_models.py`·`test_krx_client.py`(GX 승격) |
| `stock_daily.json` | 주식 일별(`sto/stk_bydd_trd`·`ksq_bydd_trd`) 두 거래일 | `make_synthetic.py`(시드 `kbj-p2-krx-synthetic-v1`) | `test_krx_cash_models.py`·`test_krx_stocks.py` |
| `etf_daily.json` | ETF 일별(`etp/etf_bydd_trd`) 두 거래일 — 순자산 = 좌수 × NAV, 둘째 날 신규 상장 | 같음 | `test_krx_cash_models.py` |
| `etn_daily.json`·`index_daily.json`·`base_info.json` | ETN·지수·종목기본정보 | 같음 | 같음 |
| `fsc_stock_price.json` | 금융위 주식시세 `item`(15094808) — `stock_daily.json` 같은 날과 같은 값 | 같음 | `test_fsc_stock_price.py` |
| `fsc_index_price.json` | 금융위 지수시세 `item` 두 거래일 | 같음 | `test_fsc_index_price.py` |

- 필드 이름은 KRX OpenAPI 카탈로그·금융위 V1 문서 기준이라 그 자체가 **[실측 필요]**다
  (`docs/probe_results.md` §7 #8·#14). 키를 받아 실측한 뒤에도 원본을 여기에 넣지 않는다 — 다른 이름이면
  생성기의 필드 이름만 고쳐 다시 만든다.
- 다시 만들기: `uv run python tests/fixtures/synthetic/krx/make_synthetic.py --write`. 커밋본과 같은지는
  `tests/unit/data/private/test_private_synthetic_fixtures.py` 가 본다(생성기 출력과 같아야 한다).
- GX 복사본 대조는 legacy GX 가 지워지면(P9) 건너뛴다 — 그때 GX 생성기의 KRX 부분을 이 폴더로 옮긴다.
