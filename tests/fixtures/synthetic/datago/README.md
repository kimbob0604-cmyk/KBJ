# 공공데이터포털 합성 fixture (공개 등급 출처 — 키 받기 전 임시)

관세청 수출입·금투협 종합통계(15094809)는 **공개 등급**(DATA_TIERS §1, 이용허락 "제한 없음")이라 실데이터
fixture 를 레포에 넣어도 된다. 다만 2026-10-07 현재 `KBJ_DATAGO_KEY` 가 없어 실호출을 못 했으므로, 응답
**모양**(봉투·열 이름·값 문자열 형식)은 공식 문서(포털 swagger·기술문서 — `docs/probe_results.md` §1~§3)대로,
값은 고정 시드 생성기가 만든 **가상 값**이다.

| 파일 | 포털 ID | 담은 함정 | 쓰는 시험 |
|---|---|---|---|
| `customs_item_country.xml` | 15100475 | `year` = `YYYY.MM` | `tests/unit/data/public/test_customs.py` |
| `customs_items.xml` | 15101609 | 숫자형 `hsCode` 의 앞자리 0 손실(`106191000`·`303`) | 같음 |
| `customs_countries.xml` | 15101612 | 중량 없음·건수 있음 | 같음 |
| `customs_sigungu.xml` | 15134343 | 금액·건수가 쉼표 붙은 문자열, 시군구는 이름만 | 같음 |
| `customs_ten_day_exp_item.xml` | 15157908 | 고정 11열 `itemUsdAmt00~10`, 금액 앞 공백·쉼표(`" 13,886,115"`), `priodDt` 말일 표기 | 같음 |
| `kofia_*.json` | 15094809 | 공통 JSON 봉투, 금액 문자열(원) | `tests/unit/data/public/test_kofia_stats.py` |

- 생성기: `make_fixtures.py`(시드 `kbj-p2-c-datago-synthetic-v1`, 파일마다 `random.Random(f"{SEED}:{이름}")`).
  다시 쓰기 `uv run python tests/fixtures/synthetic/datago/make_fixtures.py`, 같은지 확인 `--check`.
  `test_customs.py::test_synthetic_fixtures_match_the_generator` 가 파일과 생성기 출력이 같은지 본다.
- XML 은 첫 줄 뒤 주석, JSON 은 머리 칸 `_source` 가 `SYNTHETIC` 으로 시작한다.
- **키를 받은 뒤**: 실호출 응답을 `tests/fixtures/public/datago/`(공개 실데이터)로 넣고 시험을 그쪽으로 옮긴다.
  실측 체크리스트 `docs/probe_results.md` §7 #1~#7 의 답(행 상한·잘림, `hsSgn` 생략, 말일 표기, `endBasDt` 의미
  등)을 시험 기대값에 반영한다 [확인 필요 — 설계 R21].
