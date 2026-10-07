-- 0004_filings_corp — DART 고유번호(corp_code) 표 (docs/p2_design.md §8.3, conflict_map §1.5)
--
-- - 공개 등급(DART — DATA_TIERS §1)이라 pub_filings 에 둔다. 이 표에는 로그인 등급 출처의 값이
--   한 칸도 들어가지 않는다(ADR 0002 §2.1 — source 는 DART 만).
-- - 작업 filings.corp_code(묶음 F)가 DART corpCode.xml 을 받아 매일 갈아 넣는다. 옛 corp_code 3벌
--   (SD dart_corp_map·ET .dart_corp.json·dart-report 캐시)은 이관하지 않고 다시 받는다(§8.4).
-- - 상장사가 아니면 stock_code 는 NULL. received_at = DART 에서 받은 시각.
-- - 멱등: CREATE … IF NOT EXISTS, GRANT 반복은 안전. 적용된 뒤엔 고치지 않는다.

CREATE TABLE IF NOT EXISTS pub_filings.corp_code (
    corp_code    text PRIMARY KEY CHECK (corp_code ~ '^[0-9]{8}$'),
    stock_code   text CHECK (stock_code IS NULL OR stock_code ~ '^[0-9A-Z]{6}$'),
    corp_name    text NOT NULL CHECK (corp_name <> ''),
    modify_date  date,
    source       text NOT NULL DEFAULT 'DART' CHECK (source = 'DART'),
    quality      text NOT NULL DEFAULT 'ok' CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS corp_code_stock_idx
    ON pub_filings.corp_code (stock_code) WHERE stock_code IS NOT NULL;

-- 공개 내보내기 역할은 0001 의 기본 권한으로도 읽지만(같은 소유자가 만든 표), 다른 역할이 이
-- 파일을 돌린 경우에도 읽히게 명시한다 — SELECT 만.
GRANT SELECT ON pub_filings.corp_code TO kbj_public_export;
