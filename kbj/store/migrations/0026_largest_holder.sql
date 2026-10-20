-- ── 최대주주 현황 (DART:hyslrSttus — 정기보고서, 지수 유동비율 추정의 전략적 보유 — metrics §24.2) ──
-- 2026-10-21: 5% 대량보유 보고(2년 창)만으로는 최대주주 보고가 없는 회사(LG유플러스 등)의 유동비율이
-- 자기주식만 빠져 1 에 가깝게 나왔다. common_held·pref_held = 최대주주 + 특수관계인 기말 보유('계' 행).
CREATE TABLE IF NOT EXISTS pub_filings.largest_holder (
    corp_code    text NOT NULL CHECK (corp_code ~ '^[0-9]{8}$'),
    period_end   date NOT NULL,
    rcept_no     text NOT NULL CHECK (rcept_no ~ '^[0-9]{14}$'),
    common_held  bigint CHECK (common_held IS NULL OR common_held >= 0),
    pref_held    bigint CHECK (pref_held IS NULL OR pref_held >= 0),
    holders      jsonb NOT NULL DEFAULT '[]'::jsonb,
    rcept_dt     date,
    source       text NOT NULL DEFAULT 'DART' CHECK (source = 'DART'),
    quality      text NOT NULL DEFAULT 'ok' CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (corp_code, period_end, rcept_no)
);

GRANT SELECT ON pub_filings.largest_holder TO kbj_public_export;
