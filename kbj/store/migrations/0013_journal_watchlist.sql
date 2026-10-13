-- 0013_journal_watchlist — 관심종목 (docs/p4_design.md §3.4·§3.8, D-P4-13)
--
-- 규칙
-- - 개인 데이터(DATA_TIERS §2 11~13 과 같은 등급)라 prv_journal 에만 둔다. 공개판·공개 내보내기는 관심종목을
--   모른다(kbj_public_export 는 prv_* 를 못 읽는다 — 통합 시험).
-- - 원천: 사용자가 VM 에 올린 SD cache/server_watchlist.json(레포에 넣지 않는다 — U-P4-5 채택)을
--   `python -m kbj.services.watchlist import --from sd-json` 또는 legacy 이관(매핑 sd.watchlist)으로 넣는다.
--   쓰기 API(별 토글)는 P8. source: import:sd · import:csv · cli · api.
-- - 멱등: CREATE … IF NOT EXISTS. 적용된 뒤엔 고치지 않는다.

CREATE TABLE IF NOT EXISTS prv_journal.watchlist (
    list_name    text NOT NULL CHECK (list_name ~ '^[a-z][a-z0-9_]*$'),
    code         text NOT NULL CHECK (code ~ '^[0-9A-Z]{6}$'),
    name_at_add  text,
    added_at     timestamptz,
    sort         smallint NOT NULL DEFAULT 0 CHECK (sort >= 0),
    note         text,
    source       text NOT NULL CHECK (source IN ('import:sd', 'import:csv', 'cli', 'api')),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (list_name, code)
);
CREATE INDEX IF NOT EXISTS watchlist_sort_idx ON prv_journal.watchlist (list_name, sort);
