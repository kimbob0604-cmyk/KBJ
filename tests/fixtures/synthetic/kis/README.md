# KIS 합성 fixture (로그인 등급 — 실데이터 아님)

KIS 응답은 로그인 등급이라 실측 원본과 그것으로 만든 fixture 를 레포에 넣지 않는다(CLAUDE.md §2,
DATA_TIERS §4, ADR 0001 U3). 이 폴더의 JSON 은 모두 **고정 시드 생성기가 만든 합성 데이터**다.

| 파일 | 내용 | 쓰는 시험 |
|---|---|---|
| `master_lines.json` | 지수선물옵션 마스터(`fo_idx_code_mts.mst`) 줄 형식 110줄 — 옵션 단축코드·표준코드는 합성 규칙 | `tests/unit/kis/test_kis_master.py`·`test_master_download.py` |
| `callput_202610.json` | 전광판 콜/풋(FHPIF05030100) 월물 202610 응답 모양 | `test_kis_master.py`(마스터와 코드·행사가 대조) |
| `callput_wkm_260904.json` | 전광판 콜/풋 위클리(월) 260904 응답 모양 | 같음 |
| `p3_responses.json` | P3 TR 8개(현재가·종목/시장 투자자·가집계·거래대금 순위·ETF 현재가·지수·업종 [추정 TR]) 응답 모양 — 생성기 `make_p3.py`(가짜 KIS 서버 `synthetic_output`, sha256 고정 시드) | `tests/unit/kis/test_parse_*.py`(묶음 C) |

- 생성기: `legacy/gexlab/scripts/make_synthetic_fixtures.py`(시드 `kbj-p1-gexlab-synthetic-v1`, fixture 마다
  `random.Random(f"{SEED}:{이름}")`). P1 이식 때 GEXLAB 실측 fixture 를 바꾼 것이다(`legacy/gexlab/MIGRATION.md` §2).
- 이 폴더의 파일은 생성기 출력(`legacy/gexlab/tests/fixtures/kis/` 의 같은 이름 파일)과 **바이트까지 같은
  복사본**이다(P2 묶음 A, 2026-10-07). 다시 만들 때: `cd legacy/gexlab && uv run python -m
  scripts.make_synthetic_fixtures --write` 뒤 같은 파일을 여기로 복사한다. 같은지는
  `tests/unit/kis/test_kis_master.py::test_synthetic_fixtures_match_the_generator_output` 가 본다
  (legacy 가 지워지면 그 시험은 건너뛴다 — 그때 생성기를 `tests/fixtures/synthetic/` 로 옮긴다).
- 모든 JSON 은 머리 칸 `_source` 가 `SYNTHETIC` 으로 시작한다(같은 시험이 확인).
- 하루 운영 시뮬레이션의 가짜 KIS 서버가 쓰는 합성 응답(묶음 I)도 이 폴더에 둔다.
- `p3_responses.json` 은 `uv run python tests/fixtures/synthetic/kis/make_p3.py --write` 로 다시 만든다. 커밋본이
  생성기 출력과 같은지는 `tests/unit/kis/test_parse_fixtures.py` 가 본다(P3 묶음 C).
