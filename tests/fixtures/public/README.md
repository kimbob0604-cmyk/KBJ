# 공개 등급 실데이터 fixture (자리만 — 키 받은 뒤 채운다)

공개 등급 출처(DART·ECOS 한국은행 작성 표·KOSIS·관세청·금투협 종합통계)의 **실호출 응답**은 레포에 넣어도
된다(CLAUDE.md §2, DATA_TIERS §4). 2026-10-07 현재 키가 없어 비어 있다 — 묶음 C 시험은 공식 문서 모양의
합성 응답(`tests/fixtures/synthetic/datago/`, 시험 안의 작은 JSON)으로 돈다(설계 R21).

키를 받은 뒤 넣을 것(폴더 = 출처):

| 폴더 | 무엇 | 바꿀 시험 |
|---|---|---|
| `dart/` | `corpCode.xml` 발췌(상장 몇 개·비상장 몇 개를 다시 zip), `list.json`·`company.json` 한 쪽 | `tests/unit/data/public/test_dart_*.py` |
| `ecos/` | **한국은행 작성 표만**(PUBLIC_TABLES — 817Y002·722Y001 등) `StatisticSearch`·`StatisticItemList` 응답. 타기관 표(802Y001·731Y001·901Y056·901Y009)는 넣지 않는다 | `test_ecos.py` |
| `kosis/` | 국내 통계 표(`DT_1C8015` 등) 통계자료 응답 | `test_kosis.py` |
| `datago/` | 관세청 8종·금투협 4종 응답(합성 fixture 를 대체) | `test_customs.py`·`test_kofia_stats.py` |

- 넣기 전에 `uv run python scripts/check_public_safety.py` 로 키·토큰이 응답에 섞이지 않았는지 본다
  (요청 URL 에 키가 들어가는 ECOS·DART 는 특히 — 응답 본문만 저장한다).
- 로그인 등급(KIS·KRX·금융위 주식·지수 시세·ECOS 타기관 표)은 여기에 넣지 않는다 — `fixtures/synthetic/`.
