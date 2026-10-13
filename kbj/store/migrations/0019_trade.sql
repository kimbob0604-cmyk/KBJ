-- 0019_trade — 수출입(관세청 10일 잠정치·월간 4종)·HS 차원·급등·104품목·종목↔HS 매핑·종목 수출 지수
-- (docs/p6_design.md §3.3, D-P6-3, metrics §25~§29)
--
-- 규칙
-- - 관세청 자료는 공개(DATA_TIERS §1, 이용허락 "제한 없음") — pub_trade 에만. 스키마는 0001 이 만들었고
--   권한은 0001 의 pub 기본 권한(ALTER DEFAULT PRIVILEGES) + 아래 GRANT SELECT(0014·0016 과 같은 꼴).
-- - 금액은 **미화 달러 정수** numeric(20,0)(10일 잠정치는 천 달러 × 1,000 — 파서가 바꾼다). 중량 kg numeric,
--   건수 bigint. 비율(yoy 등)은 소수 double precision(0.5 = +50%).
-- - ym 은 'YYYY-MM' text(기존 데이터 키와 같은 모양). 모든 값 행에 source·as_of·quality(절대 규칙 1).
--   관세청에서 받은 행의 source = 'DATAGO:<포털 ID>', 파생 행 = 계산한 작업('KBJ:<작업>').
-- - 값 칸은 NULL 허용(0 으로 채우지 않는다) — 값이 모두 없으면 quality 가 ok 일 수 없다(CHECK, 절대 규칙 2).
-- - 10일 잠정치: first_usd·first_seen_at = 처음 본 값(발표 당시 — 저장소가 덮지 않는다), usd·revised_at =
--   최신(정정). 열 번호 → 이름은 ten_day_columns(열 구성이 바뀌면 수집이 실패 — TenDayColumnsChanged).
-- - stock_map 의 origin = 'local' 행(VM 전용 — 관심종목 매핑)이 pub 스키마에 있어도 공개 내보내기는
--   origin = 'public' 만 읽는다(시험 — D-P6-14, §10 Q3 [제안: 지금은 한 표, 필터 시험]). 품목 행의
--   region_id 는 ''(기본 키에 NULL 을 두지 않는다).
-- - hypertable 은 쓰지 않는다(월 단위 — 예상 행 수 [실측 필요 #57] 뒤 다시 본다). 보존 정책 없음.
-- - 멱등: CREATE … IF NOT EXISTS. 적용된 뒤엔 고치지 않는다 — 바꿀 일은 새 번호로.

-- ── 10일 잠정치(DATAGO 15157908·941·901·909 — trade.customs_tenday) ──────────────────────────
-- kind = exp_item·exp_country·imp_item·imp_country, seg 1(1~10일)·2(1~20일)·3(1~말일), col_no 0~10(0 = 전체).
CREATE TABLE IF NOT EXISTS pub_trade.ten_day (
    kind           text NOT NULL CHECK (kind IN ('exp_item', 'exp_country', 'imp_item', 'imp_country')),
    ym             text NOT NULL CHECK (ym ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    seg            smallint NOT NULL CHECK (seg BETWEEN 1 AND 3),
    col_no         smallint NOT NULL CHECK (col_no BETWEEN 0 AND 10),
    name           text NOT NULL CHECK (name <> ''),
    usd            numeric(20, 0) CHECK (usd >= 0),
    first_usd      numeric(20, 0) CHECK (first_usd >= 0),
    first_seen_at  timestamptz NOT NULL,
    span_text      text NOT NULL CHECK (span_text ~ '^01~(10|20|2[89]|3[01])$'),
    source         text NOT NULL CHECK (source ~ '^DATAGO:[0-9]+$'),
    as_of          timestamptz NOT NULL,
    quality        text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    revised_at     timestamptz,
    received_at    timestamptz NOT NULL,
    loaded_by      text NOT NULL CHECK (loaded_by <> ''),
    CHECK (usd IS NOT NULL OR quality <> 'ok'),
    PRIMARY KEY (kind, ym, seg, col_no)
);

-- 열 번호 → 이름 이력. valid_from = 이 이름이 처음 쓰인 기준 월.
CREATE TABLE IF NOT EXISTS pub_trade.ten_day_columns (
    kind        text NOT NULL CHECK (kind IN ('exp_item', 'exp_country', 'imp_item', 'imp_country')),
    col_no      smallint NOT NULL CHECK (col_no BETWEEN 0 AND 10),
    valid_from  text NOT NULL CHECK (valid_from ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    name        text NOT NULL CHECK (name <> ''),
    seen_at     timestamptz NOT NULL,
    loaded_by   text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (kind, col_no, valid_from)
);

-- ── 월간 품목(DATAGO 15101609 — trade.customs_monthly) ───────────────────────────────────────
-- hs = 응답 그대로의 자릿수(hs_len 2·4·6·8·10) — 접두 합산은 가장 깊은 행만(metrics §26).
CREATE TABLE IF NOT EXISTS pub_trade.item_monthly (
    ym           text NOT NULL CHECK (ym ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    hs           text NOT NULL CHECK (hs ~ '^[0-9]{2}([0-9]{2}){0,4}$'),
    hs_len       smallint NOT NULL CHECK (hs_len IN (2, 4, 6, 8, 10)),
    exp_usd      numeric(20, 0) CHECK (exp_usd >= 0),
    exp_kg       numeric CHECK (exp_kg >= 0),
    imp_usd      numeric(20, 0) CHECK (imp_usd >= 0),
    imp_kg       numeric CHECK (imp_kg >= 0),
    bal_usd      numeric(20, 0),
    source       text NOT NULL CHECK (source ~ '^DATAGO:[0-9]+$'),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    CHECK (hs_len = length(hs)),
    CHECK (exp_usd IS NOT NULL OR imp_usd IS NOT NULL OR quality <> 'ok'),
    PRIMARY KEY (ym, hs)
);
CREATE INDEX IF NOT EXISTS item_monthly_hs_idx ON pub_trade.item_monthly (hs text_pattern_ops, ym);

-- ── 월간 국가(DATAGO 15101612) — 중량 없음·건수 있음 ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pub_trade.country_monthly (
    ym            text NOT NULL CHECK (ym ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    country       text NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
    country_name  text NOT NULL CHECK (country_name <> ''),
    exp_cnt       bigint CHECK (exp_cnt >= 0),
    exp_usd       numeric(20, 0) CHECK (exp_usd >= 0),
    imp_cnt       bigint CHECK (imp_cnt >= 0),
    imp_usd       numeric(20, 0) CHECK (imp_usd >= 0),
    bal_usd       numeric(20, 0),
    source        text NOT NULL CHECK (source ~ '^DATAGO:[0-9]+$'),
    as_of         timestamptz NOT NULL,
    quality       text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at   timestamptz NOT NULL,
    loaded_by     text NOT NULL CHECK (loaded_by <> ''),
    CHECK (exp_usd IS NOT NULL OR imp_usd IS NOT NULL OR quality <> 'ok'),
    PRIMARY KEY (ym, country)
);

-- ── 월간 품목×국가(DATAGO 15100475) ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pub_trade.item_country_monthly (
    ym           text NOT NULL CHECK (ym ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    country      text NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
    hs           text NOT NULL CHECK (hs ~ '^[0-9]{2}([0-9]{2}){0,4}$'),
    hs_len       smallint NOT NULL CHECK (hs_len IN (2, 4, 6, 8, 10)),
    exp_usd      numeric(20, 0) CHECK (exp_usd >= 0),
    exp_kg       numeric CHECK (exp_kg >= 0),
    imp_usd      numeric(20, 0) CHECK (imp_usd >= 0),
    imp_kg       numeric CHECK (imp_kg >= 0),
    bal_usd      numeric(20, 0),
    source       text NOT NULL CHECK (source ~ '^DATAGO:[0-9]+$'),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    CHECK (hs_len = length(hs)),
    CHECK (exp_usd IS NOT NULL OR imp_usd IS NOT NULL OR quality <> 'ok'),
    PRIMARY KEY (ym, country, hs)
);
CREATE INDEX IF NOT EXISTS item_country_monthly_hs_idx
    ON pub_trade.item_country_monthly (hs text_pattern_ops, ym);

-- ── 월간 시군구×HS6(DATAGO 15134343) — 응답은 시군구 이름만, region_id 는 trade_regions.yaml 통합 ID ──
CREATE TABLE IF NOT EXISTS pub_trade.sigungu_monthly (
    ym           text NOT NULL CHECK (ym ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    sido_cd      text NOT NULL CHECK (sido_cd ~ '^[0-9]{2}$'),
    sgg_nm       text NOT NULL CHECK (sgg_nm <> ''),
    hs6          text NOT NULL CHECK (hs6 ~ '^[0-9]{6}$'),
    region_id    text CHECK (region_id ~ '^KR-[0-9]{2}-[0-9a-z_]+$'),
    exp_usd      numeric(20, 0) CHECK (exp_usd >= 0),
    exp_cnt      bigint CHECK (exp_cnt >= 0),
    imp_usd      numeric(20, 0) CHECK (imp_usd >= 0),
    imp_cnt      bigint CHECK (imp_cnt >= 0),
    bal_usd      numeric(20, 0),
    source       text NOT NULL CHECK (source ~ '^DATAGO:[0-9]+$'),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    CHECK (exp_usd IS NOT NULL OR imp_usd IS NOT NULL OR quality <> 'ok'),
    PRIMARY KEY (ym, sido_cd, sgg_nm, hs6)
);
CREATE INDEX IF NOT EXISTS sigungu_monthly_hs6_idx ON pub_trade.sigungu_monthly (hs6, ym);

-- ── HS 차원(응답에서 본 코드·한글 품목명) ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pub_trade.hs_dim (
    hs           text PRIMARY KEY CHECK (hs ~ '^[0-9]{2}([0-9]{2}){0,4}$'),
    hs_len       smallint NOT NULL CHECK (hs_len IN (2, 4, 6, 8, 10)),
    name_ko      text NOT NULL CHECK (name_ko <> ''),
    first_ym     text NOT NULL CHECK (first_ym ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    last_ym      text NOT NULL CHECK (last_ym ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    source       text NOT NULL CHECK (source ~ '^DATAGO:[0-9]+$'),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    CHECK (hs_len = length(hs)),
    CHECK (first_ym <= last_ym)
);

-- ── 급등·급락(trade.derive — metrics §26) ─────────────────────────────────────────────────────
-- level = hs2·hs4·hs6·hs10·country, key = HS 접두 또는 국가 ISO2, rule = surge·plunge.
CREATE TABLE IF NOT EXISTS pub_trade.surge (
    ym           text NOT NULL CHECK (ym ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    level        text NOT NULL CHECK (level IN ('hs2', 'hs4', 'hs6', 'hs10', 'country')),
    key          text NOT NULL CHECK (key ~ '^([0-9]{2}([0-9]{2}){0,4}|[A-Z]{2})$'),
    flow         text NOT NULL CHECK (flow IN ('exp', 'imp')),
    rule         text NOT NULL CHECK (rule IN ('surge', 'plunge')),
    yoy          double precision,
    yoy_3m       double precision,
    usd          numeric(20, 0) CHECK (usd >= 0),
    rank         integer NOT NULL CHECK (rank >= 1),
    source       text NOT NULL CHECK (source <> ''),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    computed_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    CHECK (yoy IS NOT NULL OR usd IS NOT NULL OR quality <> 'ok'),
    PRIMARY KEY (ym, level, key, flow)
);

-- ── 104품목 묶음 월간(trade.derive — metrics §29) ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pub_trade.item_group_monthly (
    ym           text NOT NULL CHECK (ym ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    group_id     text NOT NULL CHECK (group_id ~ '^[a-z0-9][a-z0-9_]*$'),
    exp_usd      numeric(20, 0) CHECK (exp_usd >= 0),
    exp_kg       numeric CHECK (exp_kg >= 0),
    imp_usd      numeric(20, 0) CHECK (imp_usd >= 0),
    imp_kg       numeric CHECK (imp_kg >= 0),
    yoy          double precision,
    unit_price   double precision CHECK (unit_price >= 0),
    mode         text NOT NULL CHECK (mode IN ('fixed', 'top_n')),
    source       text NOT NULL CHECK (source <> ''),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    computed_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    CHECK (exp_usd IS NOT NULL OR imp_usd IS NOT NULL OR quality <> 'ok'),
    PRIMARY KEY (ym, group_id)
);

-- ── 종목↔HS 매핑표(trade.map_load — config/knowledge/trade_map.yaml + VM local, metrics §28) ─────
-- 품목 행 region_id = '', 지역 행 region_id = 통합 ID·hs = HS6·flow = exp. origin = public·local.
-- 공개 내보내기는 origin = 'public' 만 읽는다(D-P6-14 — 시험).
CREATE TABLE IF NOT EXISTS pub_trade.stock_map (
    code         text NOT NULL CHECK (code ~ '^[0-9A-Z]{6}$'),
    hs           text NOT NULL CHECK (hs ~ '^[0-9]{2}([0-9]{2}){0,4}$'),
    flow         text NOT NULL CHECK (flow IN ('exp', 'imp')),
    region_id    text NOT NULL CHECK (region_id = '' OR region_id ~ '^KR-[0-9]{2}-[0-9a-z_]+$'),
    name         text NOT NULL CHECK (name <> ''),
    weight       double precision NOT NULL CHECK (weight >= 0 AND weight <= 1),
    origin       text NOT NULL CHECK (origin IN ('public', 'local')),
    reviewed     boolean NOT NULL,
    map_version  text NOT NULL CHECK (map_version <> ''),
    source       text NOT NULL CHECK (source <> ''),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    countries    text[] NOT NULL,
    evidence     jsonb NOT NULL,
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    CHECK (region_id = '' OR (length(hs) = 6 AND flow = 'exp')),
    PRIMARY KEY (code, hs, flow, region_id)
);

-- ── 종목 수출 지수·겹쳐 보기(trade.stock_index — metrics §28) ──────────────────────────────────
-- period = 'YYYY-MM'(월 지수)·'YYYYQn'(분기 — 매출 YoY·시차 상관). 시세 칸은 없다(공개).
CREATE TABLE IF NOT EXISTS pub_trade.stock_export (
    code         text NOT NULL CHECK (code ~ '^[0-9A-Z]{6}$'),
    period       text NOT NULL CHECK (period ~ '^[0-9]{4}(-(0[1-9]|1[0-2])|Q[1-4])$'),
    export_usd   numeric(20, 0) CHECK (export_usd >= 0),
    yoy          double precision,
    revenue_yoy  double precision,
    status       text NOT NULL CHECK (status IN ('ok', 'no_revenue', 'short_overlap', 'no_trade')),
    map_version  text NOT NULL CHECK (map_version <> ''),
    source       text NOT NULL CHECK (source <> ''),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    lag_corr     jsonb NOT NULL,
    computed_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (code, period)
);

GRANT SELECT ON pub_trade.ten_day TO kbj_public_export;
GRANT SELECT ON pub_trade.ten_day_columns TO kbj_public_export;
GRANT SELECT ON pub_trade.item_monthly TO kbj_public_export;
GRANT SELECT ON pub_trade.country_monthly TO kbj_public_export;
GRANT SELECT ON pub_trade.item_country_monthly TO kbj_public_export;
GRANT SELECT ON pub_trade.sigungu_monthly TO kbj_public_export;
GRANT SELECT ON pub_trade.hs_dim TO kbj_public_export;
GRANT SELECT ON pub_trade.surge TO kbj_public_export;
GRANT SELECT ON pub_trade.item_group_monthly TO kbj_public_export;
GRANT SELECT ON pub_trade.stock_map TO kbj_public_export;
GRANT SELECT ON pub_trade.stock_export TO kbj_public_export;
