-- 구성종목 대리 ETF(완전 복제 — config/index_reviews.yaml proxy_etf)의 구성종목 공시로 맞춘 목록(2026-10-20).
-- 목록을 올리지 않아 지수 정기변경 예측이 늘 '구성종목 목록 없음'이었다 — source 'etf' 를 받는다.
ALTER TABLE prv_index.constituents DROP CONSTRAINT IF EXISTS constituents_source_check;
ALTER TABLE prv_index.constituents
    ADD CONSTRAINT constituents_source_check CHECK (source IN ('user', 'press', 'synthetic', 'etf'));
