-- ── 상대 시총·연도별 수익률(2026-11-02 — stock-dashboard 화면을 옮김) ──────────────────────────────
-- global_daily: 야후 일봉·월봉 종가(분할 반영). 상대 시총 종목(국내 .KS·미국·일본)·환율(KRW=X·JPY=X)은
--   일봉 2년, 주요 지수(^GSPC·^IXIC·^KS11·^KQ11)는 월봉 전 기간(interval 칸으로 구분). 작업 global.daily 가
--   하루 한 번 쓴다(유일한 주인).
-- global_cap: 해외 종목 현재 시총(현지 통화 — 야후 quote). 국내 시총은 스냅(stock_snapshot)을 쓴다.
CREATE TABLE IF NOT EXISTS prv_market.global_daily (
    symbol       text NOT NULL CHECK (symbol <> '' AND symbol !~ '\s'),
    interval     text NOT NULL CHECK (interval IN ('1d', '1mo')),
    trade_date   date NOT NULL,
    close        numeric NOT NULL CHECK (close > 0),
    currency     text,
    source       text NOT NULL CHECK (source IN ('yahoo')),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (symbol, interval, trade_date)
);

CREATE TABLE IF NOT EXISTS prv_market.global_cap (
    symbol       text NOT NULL CHECK (symbol <> '' AND symbol !~ '\s'),
    as_of        date NOT NULL,
    market_cap   numeric NOT NULL CHECK (market_cap > 0),
    currency     text,
    source       text NOT NULL CHECK (source IN ('yahoo')),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (symbol, as_of)
);
