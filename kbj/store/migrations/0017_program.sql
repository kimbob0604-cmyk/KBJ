-- 0017_program — 프로그램매매(시장 일별·장중, 투자자별 당일, 종목 장중·일별) + 장중 투자자 수급의 쓰기 주인
-- (docs/p5_design.md §3.3·§4.7, docs/p5_flow_program_prompt.md §1.4·§3.3, D-P5-21, metrics §21·§22)
--
-- 규칙
-- - KIS 시세라 로그인(DATA_TIERS §1) — prv_flows 에만. 공개 내보내기는 이 표들을 모른다.
-- - 금액은 **원 단위 bigint**(metrics §0 — 파서가 원 정수로 바꿔 넘긴다). 수량은 주. 칸 이름은 KIS 공식
--   예제 기준, 단위·반영 시각은 [실측 필요 #40·#41] — 실측 전 수집 작업은 꺼져 있다(enabled: false).
-- - 긴 형식: kind = arbt(차익)·nabt(비차익)·whol(전체), side = entm(위탁)·onsl(자기)·smtn(합계) — 한
--   (날짜·시장·거래소) 에 최대 9행. 매도·매수·순매수 × 수량·대금. 검산 ④(전체 = 차익 + 비차익, 위탁 + 자기 =
--   합계)는 엔진(F2)이 하고 실패 행은 invalid.
-- - market = K(코스피)·Q(코스닥)(KIS FID_MRKT_CLS_CODE). venue = KRX·NXT·TOTAL(KIS J·NX·UN —
--   config/markets.yaml kis.venues, 기본 KRX).
-- - 장중 표는 슬롯 분(ts — 호출 시각이 아님) 기준 hypertable(청크 1일) + 보존 90일 [제안](0008 장중 표와
--   같다). 같은 분 재실행은 기본 키로 덮는다(멱등).
-- - 종목 표: scope = scan(장중 — 거래대금 상위 N + 관심종목 범위) · sweep(마감 뒤 전 종목 훑기). 화면은 범위를
--   꼬리표로 보인다(F2 §1.4 — 검산 ⑤).
-- - prv_gex.investor_flow(0006)는 손대지 않는다(체크섬). 쓰기 주인과 단위만 COMMENT 로 적는다(ADR 0019 —
--   P5 flows.market_minute 이 유일한 쓰기 주인, 대금은 GX 단위 백만원 그대로 — API 가 원으로 바꾼다).
-- - 멱등: CREATE … IF NOT EXISTS, create_hypertable·add_retention_policy(if_not_exists), COMMENT 반복 안전.

-- ── 시장 일별 (KIS:program_market_daily — FHPPG04600001, 60거래일 막대) ───────────────────────
-- index_close = 그날 지수 종가(응답에 있으면 — [확인 필요 #40]).
CREATE TABLE IF NOT EXISTS prv_flows.program_market_daily (
    trade_date   date NOT NULL,
    market       text NOT NULL CHECK (market IN ('K', 'Q')),
    venue        text NOT NULL CHECK (venue IN ('KRX', 'NXT', 'TOTAL')),
    kind         text NOT NULL CHECK (kind IN ('arbt', 'nabt', 'whol')),
    side         text NOT NULL CHECK (side IN ('entm', 'onsl', 'smtn')),
    sell_qty     bigint,
    buy_qty      bigint,
    net_qty      bigint,
    sell_value   bigint,
    buy_value    bigint,
    net_value    bigint,
    index_close  numeric,
    source       text NOT NULL CHECK (source <> ''),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (trade_date, market, venue, kind, side)
);

-- ── 시장 장중 (KIS:program_market_intraday — FHPPG04600101, 매분) ────────────────────────────
CREATE TABLE IF NOT EXISTS prv_flows.program_market_intraday (
    ts           timestamptz NOT NULL,
    trade_date   date NOT NULL,
    market       text NOT NULL CHECK (market IN ('K', 'Q')),
    venue        text NOT NULL CHECK (venue IN ('KRX', 'NXT', 'TOTAL')),
    kind         text NOT NULL CHECK (kind IN ('arbt', 'nabt', 'whol')),
    side         text NOT NULL CHECK (side IN ('entm', 'onsl', 'smtn')),
    sell_qty     bigint,
    buy_qty      bigint,
    net_qty      bigint,
    sell_value   bigint,
    buy_value    bigint,
    net_value    bigint,
    index_close  numeric,
    source       text NOT NULL CHECK (source <> ''),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (ts, market, venue, kind, side)
);
SELECT create_hypertable(
    'prv_flows.program_market_intraday', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE
);
SELECT add_retention_policy(
    'prv_flows.program_market_intraday', INTERVAL '90 days', if_not_exists => TRUE
);
CREATE INDEX IF NOT EXISTS program_market_intraday_day_idx
    ON prv_flows.program_market_intraday (trade_date, market, venue, ts);

-- ── 투자자별 프로그램 당일 (KIS:program_investor_today — HHPPG046600C1) ────────────────────────
-- investor = 소문자 식별자(metrics §21 의 12구분 이름과 같은 말 — 응답 구분 [확인 필요 #40]).
-- kind = 차익·비차익·전체(응답이 나누어 주는 만큼 — [확인 필요 #40]).
CREATE TABLE IF NOT EXISTS prv_flows.program_investor_daily (
    trade_date   date NOT NULL,
    market       text NOT NULL CHECK (market IN ('K', 'Q')),
    venue        text NOT NULL CHECK (venue IN ('KRX', 'NXT', 'TOTAL')),
    investor     text NOT NULL CHECK (investor ~ '^[a-z][a-z_]*$'),
    kind         text NOT NULL CHECK (kind IN ('arbt', 'nabt', 'whol')),
    sell_qty     bigint,
    buy_qty      bigint,
    net_qty      bigint,
    sell_value   bigint,
    buy_value    bigint,
    net_value    bigint,
    source       text NOT NULL CHECK (source <> ''),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (trade_date, market, venue, investor, kind)
);

-- ── 종목 장중 (KIS:program_stock_intraday — FHPPG04650101, 10분 — scope scan) ───────────────────
-- price = 현재가(원), chg_pct = 등락률(%). 순매수는 전체(whol) 합계 기준 [확인 필요 #41].
CREATE TABLE IF NOT EXISTS prv_flows.program_stock_intraday (
    ts           timestamptz NOT NULL,
    trade_date   date NOT NULL,
    code         text NOT NULL CHECK (code ~ '^[0-9A-Z]{6}$'),
    market       text NOT NULL CHECK (market IN ('K', 'Q')),
    venue        text NOT NULL CHECK (venue IN ('KRX', 'NXT', 'TOTAL')),
    scope        text NOT NULL CHECK (scope IN ('scan', 'sweep')),
    price        numeric,
    chg_pct      numeric,
    sell_qty     bigint,
    buy_qty      bigint,
    net_qty      bigint,
    sell_value   bigint,
    buy_value    bigint,
    net_value    bigint,
    source       text NOT NULL CHECK (source <> ''),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (ts, code, venue)
);
SELECT create_hypertable(
    'prv_flows.program_stock_intraday', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE
);
SELECT add_retention_policy(
    'prv_flows.program_stock_intraday', INTERVAL '90 days', if_not_exists => TRUE
);
CREATE INDEX IF NOT EXISTS program_stock_intraday_day_idx
    ON prv_flows.program_stock_intraday (trade_date, market, venue, ts);

-- ── 종목 일별 (KIS:program_stock_daily — FHPPG04650201, 18:10 전 종목 훑기 — scope sweep) ─────────
CREATE TABLE IF NOT EXISTS prv_flows.program_stock_daily (
    trade_date   date NOT NULL,
    code         text NOT NULL CHECK (code ~ '^[0-9A-Z]{6}$'),
    market       text NOT NULL CHECK (market IN ('K', 'Q')),
    venue        text NOT NULL CHECK (venue IN ('KRX', 'NXT', 'TOTAL')),
    scope        text NOT NULL CHECK (scope IN ('scan', 'sweep')),
    price        numeric,
    chg_pct      numeric,
    sell_qty     bigint,
    buy_qty      bigint,
    net_qty      bigint,
    sell_value   bigint,
    buy_value    bigint,
    net_value    bigint,
    source       text NOT NULL CHECK (source <> ''),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (trade_date, code, venue)
);
SELECT create_hypertable(
    'prv_flows.program_stock_daily', by_range('trade_date', INTERVAL '365 days'), if_not_exists => TRUE
);
CREATE INDEX IF NOT EXISTS program_stock_daily_rank_idx
    ON prv_flows.program_stock_daily (trade_date, market, venue, net_value);

-- ── 장중 투자자 수급(0006 GX 표)의 쓰기 주인·단위 기록 ───────────────────────────────────────
COMMENT ON TABLE prv_gex.investor_flow IS
    '장중 시장별 투자자 1분 누적(KIS FHPTJ04030000). 쓰기 주인은 등록부 작업 flows.market_minute 하나'
    '(ADR 0019 — P7 GX poller 는 이 TR 을 부르지 않고 이 표를 읽는다). 수량 계약·주, 대금 백만원'
    '(GX 단위 그대로 — metrics §21 예외, API 가 원으로 바꿔 낸다). 빈 분은 행이 없다(보간 금지).';
