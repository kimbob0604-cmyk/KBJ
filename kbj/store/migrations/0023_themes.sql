-- 0023_themes — 테마 마스터(페이지 9 테마·밸류체인): stock-dashboard data/master/ 를 받아 쓴다
-- (ADR 0039 — 사용자 결정 2026-10-15, docs/DATA_TIERS.md §2 9 행, docs/metrics.md §31)
--
-- 규칙
-- - 등급은 표(스키마)로 나눈다(ADR 0002): 대테마 분류(사용자가 만든 id·이름·섹션·밸류체인)만 pub_themes,
--   네이버에서 온 것(네이버 테마 이름·설명·편입 종목·편입 사유·종목 마스터 업종)과 적재 기록은 prv_themes.
--   스키마는 0001 이 만들었고 권한은 0001 의 pub 기본 권한 + 아래 GRANT SELECT(0019 와 같은 꼴).
-- - 쓰기 주인은 작업 theme.master_sync 하나 — 판 단위로 **한 트랜잭션에서 통째로** 바꾼다(지우고 넣기 —
--   kbj/store/repos/theme.py replace_master). 검증에 걸린 판은 쓰지 않는다(옛 판 유지).
-- - 모든 값 행에 source·as_of·quality(절대 규칙 1). as_of = 원천 meta.json built_at.
--   pub 행 source = 'stock-dashboard:data/master', prv 행 = 'stock-dashboard:data/master+naver(stock-dashboard 수집)'.
-- - 테마 강도(시세 기반)는 표를 두지 않는다 — 로그인 API 가 읽을 때 보드 종목 하루(prv_board.stock_day)로
--   계산한다(metrics §31, ADR 0039 §4).
-- - 멱등: CREATE … IF NOT EXISTS. 적용된 뒤엔 고치지 않는다 — 바꿀 일은 새 번호로.

-- ── 대테마(공개) ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pub_themes.theme (
    id           text PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9_]{0,63}$'),
    name         text NOT NULL CHECK (name <> ''),
    section      text NOT NULL CHECK (section <> ''),
    valuechain   text CHECK (valuechain ~ '^[A-Za-z0-9_]{1,64}$'),
    ord          integer NOT NULL CHECK (ord >= 0),
    source       text NOT NULL CHECK (source <> ''),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> '')
);

-- ── 적재 기록(로그인) — built_at 이 바뀐 판만 적재(바뀜 감지 키) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS prv_themes.master_load (
    built_at     timestamptz PRIMARY KEY,
    digest       text NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
    counts       jsonb NOT NULL,
    warnings     jsonb NOT NULL,
    source       text NOT NULL CHECK (source <> ''),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> '')
);

-- ── 네이버 테마(로그인) ───────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS prv_themes.naver_theme (
    no           integer PRIMARY KEY CHECK (no > 0),
    name         text NOT NULL CHECK (name <> ''),
    description  text,
    count        integer CHECK (count >= 0),
    in_master    integer CHECK (in_master >= 0),
    source       text NOT NULL CHECK (source <> ''),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> '')
);

-- 대테마 ↔ 네이버 테마(한 네이버 테마가 대테마 둘에 걸릴 수 있다). naver_name = themes.json 에 적힌 이름.
-- 대테마 id 는 pub_themes.theme 를 가리키지만 외래 키는 두지 않는다(스키마 등급을 가로지르는 제약 없음 —
-- 같은 트랜잭션에서 함께 바뀐다).
CREATE TABLE IF NOT EXISTS prv_themes.theme_link (
    theme_id     text NOT NULL CHECK (theme_id ~ '^[a-z0-9][a-z0-9_]{0,63}$'),
    naver_no     integer NOT NULL CHECK (naver_no > 0),
    naver_name   text NOT NULL CHECK (naver_name <> ''),
    ord          integer NOT NULL CHECK (ord >= 0),
    source       text NOT NULL CHECK (source <> ''),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (theme_id, naver_no)
);

-- 대테마로 옮기지 않은 네이버 테마(이벤트·계절·일회성)
CREATE TABLE IF NOT EXISTS prv_themes.naver_excluded (
    naver_no     integer PRIMARY KEY CHECK (naver_no > 0),
    naver_name   text NOT NULL CHECK (naver_name <> ''),
    source       text NOT NULL CHECK (source <> ''),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> '')
);

-- 네이버 테마 × 종목 편입(사유 포함)
CREATE TABLE IF NOT EXISTS prv_themes.member (
    naver_no     integer NOT NULL CHECK (naver_no > 0),
    code         text NOT NULL CHECK (code ~ '^[0-9A-Z]{6}$'),
    reason       text,
    source       text NOT NULL CHECK (source <> ''),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (naver_no, code)
);
CREATE INDEX IF NOT EXISTS member_code_idx ON prv_themes.member (code);

-- 종목 마스터(시장·종류·티어·업종 — 업종 이름이 네이버 표기라 로그인)
CREATE TABLE IF NOT EXISTS prv_themes.stock (
    code            text PRIMARY KEY CHECK (code ~ '^[0-9A-Z]{6}$'),
    name            text NOT NULL CHECK (name <> ''),
    market          text NOT NULL CHECK (market <> ''),
    type            text NOT NULL CHECK (type <> ''),
    parent_code     text CHECK (parent_code ~ '^[0-9A-Z]{6}$'),
    industry        text,
    industry_group  text,
    tier            text,
    former_names    text,
    source          text NOT NULL CHECK (source <> ''),
    as_of           timestamptz NOT NULL,
    quality         text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at     timestamptz NOT NULL,
    loaded_by       text NOT NULL CHECK (loaded_by <> '')
);

-- 업종 대분류(id → 이름·업종 목록)
CREATE TABLE IF NOT EXISTS prv_themes.industry_group (
    id           text PRIMARY KEY CHECK (id <> ''),
    name         text NOT NULL CHECK (name <> ''),
    industries   jsonb NOT NULL,
    source       text NOT NULL CHECK (source <> ''),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> '')
);

-- 종목 종류 손 지정(원천 type_overrides.csv)
CREATE TABLE IF NOT EXISTS prv_themes.type_override (
    code         text PRIMARY KEY CHECK (code ~ '^[0-9A-Z]{6}$'),
    type         text NOT NULL CHECK (type <> ''),
    reason       text,
    source       text NOT NULL CHECK (source <> ''),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> '')
);

GRANT SELECT ON pub_themes.theme TO kbj_public_export;
