-- 0015_us_board — 미국 유니버스와 미국 보드 산출 이름 (docs/p5_design.md §3.3·§4.6, D-P5-18, metrics §23)
--
-- 규칙
-- - 미국 출처(Nasdaq 스크리너·history, Stooq CSV, Yahoo chart)는 약관 미확인이라 로그인(DATA_TIERS §1,
--   ADR 0001 Q10). 일봉은 새 표를 만들지 않고 prv_market.daily_bar(market='US' — 0003 이 이미 허용)에
--   쓴다(행 source 에 실제 출처 nasdaq·stooq·yahoo). 이 파일은 유니버스 표와 산출 이름만.
-- - 유니버스: 스크리너 한 번(us.universe — 주 1회)이 as_of 날짜의 한 판이다. 펀드(ETF·ETN·CEF)는 is_fund.
--   market_cap 은 달러(스크리너 값 그대로 — 단위 [확인 필요 #47]). 없는 값은 NULL.
-- - prv_board.artifact 의 name 제약(0007 — 국장 5종)에 미국 산출 us_summary·us_brief 를 더한다.
--   0007 은 고칠 수 없으므로(체크섬) 0007 이 만든 자동 이름의 제약을 찾아 바꾼다 — 자동 이름은
--   'artifact_name_check' 로 추정 [확인 필요]이라 이름에 기대지 않고 pg_constraint 에서 정의로 찾는다
--   (DO 블록). us_* 이름은 market='US' 행에만(두 번째 제약).
-- - 멱등: CREATE … IF NOT EXISTS, DO 블록은 새 제약이 이미 있으면 아무것도 하지 않는다.

-- ── 미국 유니버스 (NASDAQ:screener — ET board/us/pipeline.py:universe) ────────────────────────
CREATE TABLE IF NOT EXISTS prv_market.us_universe (
    symbol       text NOT NULL CHECK (symbol ~ '^[A-Z0-9][A-Z0-9.^=/-]*$'),
    as_of        date NOT NULL,
    name         text,
    exchange     text,
    sector       text,
    industry     text,
    market_cap   numeric CHECK (market_cap IS NULL OR market_cap >= 0),
    is_fund      boolean NOT NULL DEFAULT false,
    source       text NOT NULL CHECK (source <> ''),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (symbol, as_of)
);
CREATE INDEX IF NOT EXISTS us_universe_asof_idx ON prv_market.us_universe (as_of);

-- ── 보드 산출 이름 넓히기 (0007 prv_board.artifact.name) ─────────────────────────────────────
DO $$
DECLARE
    old_name text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint
        WHERE conrelid = 'prv_board.artifact'::regclass AND conname = 'artifact_name_p5_check'
    ) THEN
        FOR old_name IN
            SELECT conname FROM pg_catalog.pg_constraint
            WHERE conrelid = 'prv_board.artifact'::regclass
              AND contype = 'c'
              AND pg_catalog.pg_get_constraintdef(oid) LIKE '%universe_meta%'
        LOOP
            EXECUTE format('ALTER TABLE prv_board.artifact DROP CONSTRAINT %I', old_name);
        END LOOP;
        ALTER TABLE prv_board.artifact ADD CONSTRAINT artifact_name_p5_check CHECK (
            name IN ('universe_meta', 'newhigh', 'sectors', 'events', 'rankings',
                     'us_summary', 'us_brief')
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint
        WHERE conrelid = 'prv_board.artifact'::regclass AND conname = 'artifact_us_names_check'
    ) THEN
        ALTER TABLE prv_board.artifact ADD CONSTRAINT artifact_us_names_check CHECK (
            name NOT IN ('us_summary', 'us_brief') OR market = 'US'
        );
    END IF;
END
$$;
