# 옛 SQLite·JSON 이관 원본 (합성 — 실데이터 아님)

옛 DB(SD `dashboard.db`, ET `board.db`·`us_board.db`·`backtest.db`·`us_backtest.db`)와 JSON(ET `state/inbox.json`·
`state/<날짜>/stockflows.json`, monitor/kr `cache/flows.json`)에는 로그인 등급 시세·수급과 개인 대화가 섞여 있어
레포에 넣지 않는다(CLAUDE.md §2, ADR 0001 U3). 이 폴더에는 **DB·JSON 파일이 없다** — 시험이 실행 중에 만든다
(`*.db` 는 `.gitignore`).

| 파일 | 내용 |
|---|---|
| `make_legacy_db.py` | 생성기(시드 `kbj-p2-legacy-sqlite-v1`). 원본 DDL 을 **글자 그대로** 써서 빈 DB 를 만들고 합성 값과 손으로 정한 경우만 넣는다: 네이버 행·출처 빈 행·정수가 아닌 거래량·잘못된 날짜·숫자로 저장돼 앞자리 0 이 빠진 코드·board 와 backtest 가 겹치는 날·같은 run_log 두 줄·비밀처럼 보이는 키·배열 길이가 어긋난 flow_cache·update_id 없는 인박스 항목 |

- DDL 출처(원본 스냅샷 — ET `0014f57`, SD `f46178c`): ET `board/engine/db.py:DDL`, `board/us/db.py:DDL`, SD
  `db/schema.sql`(ops_state·flow_cache), `migrations/004_step4_schema.py`(fetch_progress),
  `migrations/005_step4_7_earnings.py`(index_universe).
- 종목·이름은 가짜(코드 `99xxxx`, `합성…`, 미국 티커 `ZZ…`), 텔레그램 대화 id 도 합성이다.
- 쓰는 시험: `tests/unit/store/test_import_sqlite.py`(메모리 대상), `tests/integration/test_import_pg.py`(Docker).
- 손으로 만들어 보기(레포 밖 폴더에):

```
uv run python tests/fixtures/synthetic/sqlite/make_legacy_db.py --out /tmp/kbj-legacy
uv run python -m kbj.store.legacy_import --dry-run --phase P2 --source board=/tmp/kbj-legacy/board.db
```

실제 이관은 사용자가 고른 원본 사본(ADR 0001 Q12 — 맥 로컬 사본)으로 운영 DB 에 한다. 원본은 읽기 전용으로만 연다.
