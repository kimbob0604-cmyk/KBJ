-- 0003_market_flows — 로그인 등급 시세(스냅·일봉·유니버스)와 투자자 수급(종목·시장)
-- (docs/p2_design.md §8.3·§8.4·§8.5, docs/metrics.md §0~§2, conflict_map §1.5)
--
-- 규칙
-- - 로그인 등급(DATA_TIERS §1 — KIS·KRX·금융위 시세·Nasdaq)이라 prv_* 에만 둔다(ADR 0002).
-- - 모든 값 행에 source·quality(ok·stale·estimated·invalid)를 둔다(CLAUDE.md 절대 규칙 1).
--   as_of 는 trade_date(그 값이 속한 거래일)다 — 받은 시각은 received_at.
-- - 종목코드는 text(앞자리 0 유지). KR 금액은 원 단위(metrics §0 — 억·조 변환은 화면에서만),
--   US 금액은 달러. 수량은 bigint.
-- - venue = 거래소 구분(kbj/data/spec.py Venue — KRX·NXT·TOTAL). 거래소를 나누지 않거나 모르는
--   행(이관분 등)은 ''. metrics §1: KRX 와 넥스트레이드(NXT)를 나눠 저장하고 합계를 따로 둔다.
-- - loaded_by = 이 행을 쓴 수집 작업 이름 또는 이관 원본('legacy:board.px' 처럼). 이관 검증
--   (kbj/store/legacy_import/verify.py)이 이 열로 '이 원본에서 온 행'을 센다.
-- - 이관분은 원본에 받은 시각이 없어 received_at 이 NULL 일 수 있다(지어내지 않는다).
-- - 멱등: CREATE … IF NOT EXISTS, create_hypertable(if_not_exists). 적용된 뒤엔 고치지 않는다.

-- ── 종목 일자 스냅 (ET board.db·us_board.db snap, SD stocks 는 이관 안 함 — §8.4) ──────────
-- KR 스냅의 segment 는 KOSPI·KOSDAQ·KONEX, US 는 상장 거래소. extra 는 출처에만 있는 칸
-- (US sector_raw·industry_raw 등)을 그대로 담는다.
CREATE TABLE IF NOT EXISTS prv_market.stock_snapshot (
    market                text NOT NULL CHECK (market IN ('KR', 'US')),
    code                  text NOT NULL CHECK (code <> '' AND code !~ '\s'),
    trade_date            date NOT NULL,
    source                text NOT NULL CHECK (source <> ''),
    venue                 text NOT NULL DEFAULT '' CHECK (venue IN ('', 'KRX', 'NXT', 'TOTAL')),
    name                  text,
    segment               text,
    sector                text,
    industry              text,
    close                 numeric,
    chg_pct               numeric,             -- %
    volume                bigint CHECK (volume IS NULL OR volume >= 0),
    turnover              numeric CHECK (turnover IS NULL OR turnover >= 0),  -- 거래대금
    turnover_is_estimate  boolean NOT NULL DEFAULT false,  -- 출처가 거래대금을 안 줘서 비웠다/추정했다
    mktcap                numeric CHECK (mktcap IS NULL OR mktcap >= 0),
    shares                bigint CHECK (shares IS NULL OR shares >= 0),
    extra                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    quality               text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at           timestamptz,
    loaded_by             text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (market, code, trade_date, source, venue)
);
CREATE INDEX IF NOT EXISTS stock_snapshot_date_idx ON prv_market.stock_snapshot (trade_date, market);

-- ── 일봉 (ET px·backtest px·us px, KRX·금융위 일별 — §8.4) ─────────────────────────────────
-- asset = stock·etf·etn·index. KRX OpenAPI 일별은 비수정 가격이라 adjusted=false
-- (conflict_map §1.13 ⚠). 수정 여부를 모르는 출처(이관분 일부)는 NULL — 지어내지 않는다.
-- 하루 한 번 쌓는 일 단위 자료라 hypertable 청크는 365일(GX krx_*_daily 와 같다).
CREATE TABLE IF NOT EXISTS prv_market.daily_bar (
    market       text NOT NULL CHECK (market IN ('KR', 'US')),
    asset        text NOT NULL CHECK (asset IN ('stock', 'etf', 'etn', 'index')),
    code         text NOT NULL CHECK (code <> '' AND code !~ '\s'),
    trade_date   date NOT NULL,
    source       text NOT NULL CHECK (source <> ''),
    venue        text NOT NULL DEFAULT '' CHECK (venue IN ('', 'KRX', 'NXT', 'TOTAL')),
    open         numeric,
    high         numeric,
    low          numeric,
    close        numeric,
    volume       bigint CHECK (volume IS NULL OR volume >= 0),
    turnover     numeric CHECK (turnover IS NULL OR turnover >= 0),
    adjusted     boolean,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (market, asset, code, trade_date, source, venue)
);
SELECT create_hypertable(
    'prv_market.daily_bar', by_range('trade_date', INTERVAL '365 days'), if_not_exists => TRUE
);
CREATE INDEX IF NOT EXISTS daily_bar_code_idx ON prv_market.daily_bar (market, code, trade_date DESC);

-- ── 유니버스 (SD index_universe·KRX 종목기본정보 …) ────────────────────────────────────────
-- 유니버스가 여러 벌(conflict_map §1.5 — Q11 결정 전)이라 source 를 키에 넣는다. 기능별 하한·
-- 출처에만 있는 칸(순위·시총·편출일 등)은 flags 에. kind 는 ET engine/kinds.py 이름 + ETF·ETN.
CREATE TABLE IF NOT EXISTS prv_market.universe (
    market      text NOT NULL CHECK (market IN ('KR', 'US')),
    code        text NOT NULL CHECK (code <> '' AND code !~ '\s'),
    as_of       date NOT NULL,
    source      text NOT NULL CHECK (source <> ''),
    name        text,
    kind        text CHECK (kind IN ('common', 'pref', 'spac', 'reit', 'etf', 'etn')),
    listed_on   date,
    flags       jsonb NOT NULL DEFAULT '{}'::jsonb,
    quality     text NOT NULL DEFAULT 'ok' CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at timestamptz,
    loaded_by   text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (market, code, as_of, source)
);
CREATE INDEX IF NOT EXISTS universe_source_idx ON prv_market.universe (source, as_of DESC);

-- ── 종목별 투자자 수급 (KIS FHKST01010900 — metrics §2, SD flow_cache·ET stockflows 이관) ────
-- investor 는 소문자 이름: foreign(외국인) · foreign_other(기타외국인) · institution(기관 합계) ·
-- fin_invest(금융투자) · trust(투신) · private_fund(사모) · insurance(보험) · bank(은행) ·
-- pension(연기금) · other_fin(기타금융) · other_corp(기타법인) · individual(개인).
-- 금액은 원(KIS 금액 = 실거래대금). SD flow_cache 금액은 '순매매량 × 종가' 추정이라 quality=estimated.
-- unit 은 net_value 의 통화(지금은 krw 뿐).
CREATE TABLE IF NOT EXISTS prv_flows.stock_investor_daily (
    code         text NOT NULL CHECK (code <> '' AND code !~ '\s'),
    trade_date   date NOT NULL,
    investor     text NOT NULL CHECK (investor ~ '^[a-z][a-z_]*$'),
    source       text NOT NULL CHECK (source <> ''),
    venue        text NOT NULL DEFAULT '' CHECK (venue IN ('', 'KRX', 'NXT', 'TOTAL')),
    net_qty      bigint,
    net_value    bigint,
    unit         text NOT NULL DEFAULT 'krw' CHECK (unit IN ('krw')),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    CHECK (net_qty IS NOT NULL OR net_value IS NOT NULL),
    PRIMARY KEY (code, trade_date, investor, source, venue)
);
SELECT create_hypertable(
    'prv_flows.stock_investor_daily', by_range('trade_date', INTERVAL '365 days'),
    if_not_exists => TRUE
);
CREATE INDEX IF NOT EXISTS stock_investor_daily_date_idx
    ON prv_flows.stock_investor_daily (trade_date, investor);

-- ── 시장별 투자자 수급 일별 (KIS FHPTJ04040000) ───────────────────────────────────────────
-- market_code 는 KIS 시장 코드(0001 코스피 · 1001 코스닥 …). 장중 주기 수집은 GX investor_flow
-- (prv_gex, 0006) — 같은 이름의 뷰 prv_flows.market_investor_intraday 로 읽는다.
CREATE TABLE IF NOT EXISTS prv_flows.market_investor_daily (
    market_code  text NOT NULL CHECK (market_code <> ''),
    trade_date   date NOT NULL,
    investor     text NOT NULL CHECK (investor ~ '^[a-z][a-z_]*$'),
    source       text NOT NULL CHECK (source <> ''),
    venue        text NOT NULL DEFAULT '' CHECK (venue IN ('', 'KRX', 'NXT', 'TOTAL')),
    net_qty      bigint,
    net_value    bigint,
    unit         text NOT NULL DEFAULT 'krw' CHECK (unit IN ('krw')),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    CHECK (net_qty IS NOT NULL OR net_value IS NOT NULL),
    PRIMARY KEY (market_code, trade_date, investor, source, venue)
);
CREATE INDEX IF NOT EXISTS market_investor_daily_date_idx
    ON prv_flows.market_investor_daily (trade_date, market_code);
