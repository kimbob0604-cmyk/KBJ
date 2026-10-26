-- ── 주식 5분봉 (KIS:stock_minute_history — FHKST03010230 주식일별분봉조회, 1분봉을 5분으로 묶어 저장) ──
-- cis 30분봉 백테스트(kbj/engines/screen/intraday.py)의 입력. 전 종목·전 기간이 아니라 일봉 신호 종목의
-- 신호일 D ~ D+창 만 받는다(kbj/services/collectors/stock_minutes.py — 앱키 리미터 P4). KIS 는 분봉을 최대
-- 1년 보관해 그보다 옛날은 받을 수 없다. ts = 봉 시작(KST 09:00, 09:05, …, 15:30 — 동시호가 15:30 봉 하나),
-- minutes = 그 5분 안 1분봉 수(거래 없는 분은 KIS 가 봉을 주지 않는다 [실측 필요]). 거래소 = KRX(J).
CREATE TABLE IF NOT EXISTS prv_market.stock_bar_5m (
    code         text NOT NULL CHECK (code ~ '^[0-9A-Z]{6}$'),
    ts           timestamptz NOT NULL,
    trade_date   date NOT NULL,
    open         numeric NOT NULL CHECK (open > 0),
    high         numeric NOT NULL CHECK (high > 0),
    low          numeric NOT NULL CHECK (low > 0),
    close        numeric NOT NULL CHECK (close > 0),
    volume       bigint NOT NULL CHECK (volume >= 0),
    minutes      smallint NOT NULL CHECK (minutes BETWEEN 1 AND 5),
    source       text NOT NULL DEFAULT 'kis' CHECK (source = 'kis'),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (code, ts)
);
SELECT create_hypertable('prv_market.stock_bar_5m', by_range('ts', INTERVAL '30 days'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS stock_bar_5m_day_idx ON prv_market.stock_bar_5m (trade_date, code);
