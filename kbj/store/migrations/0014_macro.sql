-- 0014_macro — 매크로 시리즈 메타·관측값·경제 캘린더 (docs/p5_design.md §3.3, D-P5-6·8, metrics §16)
--
-- 규칙
-- - 등급은 데이터셋 단위(D-P5-6): 공개 데이터셋(ECOS 한은 작성 표·KOSIS·FRED 정부 시리즈·미 재무부·
--   뉴욕연은 — DATA_TIERS §1)의 시리즈는 pub_macro, 로그인 데이터셋(Yahoo·FRED 저작권 시리즈·ECOS 타기관
--   표·ForexFactory)의 시리즈는 prv_macro. 한 표에 두 등급을 섞지 않는다 — tier 열 CHECK 가 막는다.
--   시리즈 목록(어느 시리즈가 어느 등급인지)은 config/macro.yaml 하나(시험이 데이터셋 tier 와 대조).
-- - 값(value)은 없으면 NULL 이다. 0 으로 채우지 않는다(절대 규칙 2) — 공표 전·결측은 행을 만들지 않거나
--   NULL + quality(stale·estimated·invalid). 값이 NULL 인 행은 quality 가 ok 일 수 없다(CHECK).
-- - 개정(revision): 같은 (series_id, period) 를 덮어쓰고 덮기 전 값은 prev_value 에 남긴다 [확인 필요 —
--   metrics §16 '최신값 변화'].
-- - 경제 캘린더: 공개(pub_macro.calendar)는 공식 일정(수기 config/calendar_events.yaml·macro.yaml)과 FRED
--   발표일만 — source CHECK 가 공개 출처 목록으로 막는다. 예상치(forecast)는 **prv_macro.calendar 에만**
--   칸이 있다(ForexFactory — 로그인, 기본 꺼짐 §10 Q3). 발표 결과(actual)는 수집된 시리즈 값만(지어내지
--   않는다 — actual_series 가 그 시리즈).
-- - event_id = '<country>:<kind>:<YYYY-MM-DD>:<code>'(예 KR:meeting:2026-11-27:bok_mpc).
-- - 멱등: CREATE … IF NOT EXISTS. 적용된 뒤엔 고치지 않는다 — 바꿀 일은 새 번호로.

-- ── 공개: 시리즈 메타 ─────────────────────────────────────────────────────────────────────
-- tab = 페이지 6 탭 id(config/macro.yaml tabs). freq D·W·M·Q·A. dataset = 카탈로그 id(예 FRED:gov_series).
CREATE TABLE IF NOT EXISTS pub_macro.series_meta (
    series_id   text PRIMARY KEY CHECK (series_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:/-]*$'),
    tab         text NOT NULL CHECK (tab ~ '^[a-z][a-z0-9_]*$'),
    title       text NOT NULL CHECK (title <> ''),
    unit        text NOT NULL CHECK (unit <> ''),
    freq        text NOT NULL CHECK (freq IN ('D', 'W', 'M', 'Q', 'A')),
    tier        text NOT NULL CHECK (tier = 'public'),
    source      text NOT NULL CHECK (source IN ('ECOS', 'KOSIS', 'FRED', 'TREASURY', 'NYFED')),
    dataset     text NOT NULL CHECK (dataset ~ '^[A-Z][A-Z0-9_]*:[^\s]+$'
                                     AND dataset <> 'FRED:restricted_series'),
    updated_at  timestamptz NOT NULL,
    loaded_by   text NOT NULL CHECK (loaded_by <> '')
);

-- ── 공개: 관측값 ──────────────────────────────────────────────────────────────────────────
-- period = YYYY(연)·YYYY-MM(월)·YYYY-Qn(분기)·YYYY-MM-DD(일·주). as_of = 받은 값의 기준 시각(받은 시각),
-- released_at = 출처 공표 시각(모르면 NULL).
CREATE TABLE IF NOT EXISTS pub_macro.series (
    series_id    text NOT NULL CHECK (series_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:/-]*$'),
    period       text NOT NULL CHECK (period ~ '^[0-9]{4}(-[0-9]{2}(-[0-9]{2})?|-Q[1-4])?$'),
    value        numeric,
    prev_value   numeric,
    unit         text NOT NULL CHECK (unit <> ''),
    source       text NOT NULL CHECK (source IN ('ECOS', 'KOSIS', 'FRED', 'TREASURY', 'NYFED')),
    as_of        timestamptz NOT NULL,
    released_at  timestamptz,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    CHECK (value IS NOT NULL OR quality <> 'ok'),
    PRIMARY KEY (series_id, period)
);
CREATE INDEX IF NOT EXISTS pub_series_released_idx ON pub_macro.series (released_at);

-- ── 공개: 경제 캘린더(공식 일정 + FRED 발표일 + 발표 결과) ──────────────────────────────────
-- kind = release(지표 발표)·meeting(금통위·FOMC)·holiday·other. importance 1(높음)~3.
-- source 는 공개 출처만: bok(금통위)·fomc(연준 공표 일정)·fred(releases/dates)·kosis·ecos·manual.
CREATE TABLE IF NOT EXISTS pub_macro.calendar (
    event_id       text PRIMARY KEY CHECK (event_id ~ '^[A-Z]{2}:[a-z][a-z0-9_]*:[0-9]{4}-[0-9]{2}-[0-9]{2}:[^\s]+$'),
    kind           text NOT NULL CHECK (kind ~ '^[a-z][a-z0-9_]*$'),
    event_date     date NOT NULL,
    time_kst       time,
    title          text NOT NULL CHECK (title <> ''),
    country        text NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
    importance     smallint NOT NULL CHECK (importance BETWEEN 1 AND 3),
    actual         numeric,
    actual_series  text,
    unit           text,
    source         text NOT NULL CHECK (source IN ('bok', 'fomc', 'fred', 'kosis', 'ecos', 'manual')),
    quality        text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at    timestamptz NOT NULL,
    loaded_by      text NOT NULL CHECK (loaded_by <> '')
);
CREATE INDEX IF NOT EXISTS pub_calendar_date_idx ON pub_macro.calendar (event_date, importance);

GRANT SELECT ON pub_macro.series_meta TO kbj_public_export;
GRANT SELECT ON pub_macro.series TO kbj_public_export;
GRANT SELECT ON pub_macro.calendar TO kbj_public_export;

-- ── 로그인: 시리즈 메타 ───────────────────────────────────────────────────────────────────
-- Yahoo 12종(SD _MACRO_TICKERS)·FRED 저작권 시리즈(S&P·ICE·CBOE …)·ECOS 타기관 표(802Y001·731Y001·
-- 901Y056·901Y009). tier 는 private 만.
CREATE TABLE IF NOT EXISTS prv_macro.series_meta (
    series_id   text PRIMARY KEY CHECK (series_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:/^=-]*$'),
    tab         text NOT NULL CHECK (tab ~ '^[a-z][a-z0-9_]*$'),
    title       text NOT NULL CHECK (title <> ''),
    unit        text NOT NULL CHECK (unit <> ''),
    freq        text NOT NULL CHECK (freq IN ('D', 'W', 'M', 'Q', 'A')),
    tier        text NOT NULL CHECK (tier = 'private'),
    source      text NOT NULL CHECK (source <> ''),
    dataset     text NOT NULL CHECK (dataset ~ '^[A-Z][A-Z0-9_]*:[^\s]+$'),
    updated_at  timestamptz NOT NULL,
    loaded_by   text NOT NULL CHECK (loaded_by <> '')
);

-- ── 로그인: 관측값 ────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS prv_macro.series (
    series_id    text NOT NULL CHECK (series_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:/^=-]*$'),
    period       text NOT NULL CHECK (period ~ '^[0-9]{4}(-[0-9]{2}(-[0-9]{2})?|-Q[1-4])?$'),
    value        numeric,
    prev_value   numeric,
    unit         text NOT NULL CHECK (unit <> ''),
    source       text NOT NULL CHECK (source <> ''),
    as_of        timestamptz NOT NULL,
    released_at  timestamptz,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    CHECK (value IS NOT NULL OR quality <> 'ok'),
    PRIMARY KEY (series_id, period)
);
CREATE INDEX IF NOT EXISTS prv_series_released_idx ON prv_macro.series (released_at);

-- ── 로그인: 경제 캘린더(예상치 포함 — ForexFactory, 기본 꺼짐) ──────────────────────────────
CREATE TABLE IF NOT EXISTS prv_macro.calendar (
    event_id       text PRIMARY KEY CHECK (event_id ~ '^[A-Z]{2}:[a-z][a-z0-9_]*:[0-9]{4}-[0-9]{2}-[0-9]{2}:[^\s]+$'),
    kind           text NOT NULL CHECK (kind ~ '^[a-z][a-z0-9_]*$'),
    event_date     date NOT NULL,
    time_kst       time,
    title          text NOT NULL CHECK (title <> ''),
    country        text NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
    importance     smallint NOT NULL CHECK (importance BETWEEN 1 AND 3),
    actual         numeric,
    actual_series  text,
    unit           text,
    forecast       numeric,
    source         text NOT NULL CHECK (source <> ''),
    quality        text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at    timestamptz NOT NULL,
    loaded_by      text NOT NULL CHECK (loaded_by <> '')
);
CREATE INDEX IF NOT EXISTS prv_calendar_date_idx ON prv_macro.calendar (event_date, importance);
