-- 0016_market_stats — 금투협 종합통계(신용공여 잔고·증시자금·펀드 순자산·CMA)
-- (docs/p5_design.md §3.3, D-P5-20, kbj/data/public/fsc_kofia_stats/datasets.py)
--
-- 규칙
-- - 공개 등급(공공데이터포털 15094809 — 공공누리 1유형, DATA_TIERS §1)이라 pub_market_stats 에 둔다.
--   상단 띠 신용잔고·예탁금 칩과 공개 내보내기 credit.json 의 원천(D-P5-20).
-- - 네 표가 같은 모양: 하루(stat_date) × 항목(item) 한 값. item = 응답의 항목 칸을 소문자 식별자로
--   (예 credit_total·deposit·margin_sale) — 칸 이름·단위는 실응답으로 확인 [확인 필요 #48]. 금액 단위는
--   unit 열(원·백만원 — 출처 공표 단위 그대로, 바꾸지 않는다). 없는 값은 NULL(0 으로 채우지 않는다).
-- - source 는 'DATAGO'(카탈로그 출처 이름)만. 로그인 출처 값은 한 칸도 없다.
-- - 멱등: CREATE … IF NOT EXISTS. 적용된 뒤엔 고치지 않는다.

-- 신용공여 잔고 추이(getGrantingOfCreditBalanceInfo — 15094809/credit)
CREATE TABLE IF NOT EXISTS pub_market_stats.credit_balance (
    stat_date    date NOT NULL,
    item         text NOT NULL CHECK (item ~ '^[a-z][a-z0-9_]*$'),
    value        numeric,
    unit         text NOT NULL CHECK (unit <> ''),
    source       text NOT NULL CHECK (source = 'DATAGO'),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    CHECK (value IS NOT NULL OR quality <> 'ok'),
    PRIMARY KEY (stat_date, item)
);

-- 증시자금 추이 — 투자자예탁금·반대매매(getSecuritiesMarketTotalCapitalInfo — 15094809/capital)
CREATE TABLE IF NOT EXISTS pub_market_stats.market_capital (
    stat_date    date NOT NULL,
    item         text NOT NULL CHECK (item ~ '^[a-z][a-z0-9_]*$'),
    value        numeric,
    unit         text NOT NULL CHECK (unit <> ''),
    source       text NOT NULL CHECK (source = 'DATAGO'),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    CHECK (value IS NOT NULL OR quality <> 'ok'),
    PRIMARY KEY (stat_date, item)
);

-- 펀드 순자산 총액(getFundTotalNetEssetInfo — 15094809/fund)
CREATE TABLE IF NOT EXISTS pub_market_stats.fund_nav (
    stat_date    date NOT NULL,
    item         text NOT NULL CHECK (item ~ '^[a-z][a-z0-9_]*$'),
    value        numeric,
    unit         text NOT NULL CHECK (unit <> ''),
    source       text NOT NULL CHECK (source = 'DATAGO'),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    CHECK (value IS NOT NULL OR quality <> 'ok'),
    PRIMARY KEY (stat_date, item)
);

-- 일자별 CMA 현황(getCMAStatus — 15094809/cma)
CREATE TABLE IF NOT EXISTS pub_market_stats.cma (
    stat_date    date NOT NULL,
    item         text NOT NULL CHECK (item ~ '^[a-z][a-z0-9_]*$'),
    value        numeric,
    unit         text NOT NULL CHECK (unit <> ''),
    source       text NOT NULL CHECK (source = 'DATAGO'),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    CHECK (value IS NOT NULL OR quality <> 'ok'),
    PRIMARY KEY (stat_date, item)
);

GRANT SELECT ON pub_market_stats.credit_balance TO kbj_public_export;
GRANT SELECT ON pub_market_stats.market_capital TO kbj_public_export;
GRANT SELECT ON pub_market_stats.fund_nav TO kbj_public_export;
GRANT SELECT ON pub_market_stats.cma TO kbj_public_export;
