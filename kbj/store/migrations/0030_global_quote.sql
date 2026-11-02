-- ── 글로벌 프록시 시세(2026-11-02 — stock-dashboard 화면을 옮김) ──────────────────────────────────
-- 한국 장이 닫힌 동안 한국 주식을 가늠하는 해외 가격: Hyperliquid 무기한 선물(xyz dex — 삼성전자·
-- SK하이닉스·SK하이닉스 ADR·현대차·코스피200), 야후 해외 상장(SKHY·SMSN.IL·HY9H.F)·선물(NQ=F·ES=F)·
-- 매크로(^TNX·CL=F·KRW=X·EURUSD=X)·미국 ETF(DRAM·EWY·SOXX). 작업 global.proxy 가 5분마다 쓴다(유일한 주인).
-- ts = 받은 시각(분 시작, KST 기준 분). prev = 비교 기준(야후 직전 세션 종가·Hyperliquid 24시간 전).
CREATE TABLE IF NOT EXISTS prv_market.global_quote (
    symbol       text NOT NULL CHECK (symbol <> '' AND symbol !~ '\s'),
    ts           timestamptz NOT NULL,
    price        numeric NOT NULL CHECK (price > 0),
    prev         numeric,
    funding_1h   numeric,
    vol_24h      numeric,
    currency     text,
    source       text NOT NULL CHECK (source IN ('hyperliquid', 'yahoo')),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (symbol, ts)
);
SELECT create_hypertable('prv_market.global_quote', by_range('ts', INTERVAL '30 days'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS global_quote_ts_idx ON prv_market.global_quote (ts DESC);
