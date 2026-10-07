-- 0001_schemas — 등급별 스키마·공개 내보내기 역할 (docs/adr/0002-tier-schemas.md)
--
-- 규칙
-- - 공개 등급 데이터(DATA_TIERS §1 "공개")는 pub_<도메인>, 로그인 등급은 prv_<도메인>, 운영은 ops.
--   Postgres 기본 스키마 이름 `public` 은 쓰지 않는다(확장·search_path 기본값과 겹친다 — conflict_map §1.5).
-- - 공개 내보내기(Pages 용 JSON)는 kbj_public_export 역할로만 읽는다. 이 역할은 pub_* 에만
--   USAGE·SELECT 가 있다 — prv_*·ops 는 권한이 없어 읽을 수 없다(실행 중 검사가 아니라 권한으로 막는다).
-- - 표는 만들지 않는다(P2 부터 0002_… 로). 시각은 timestamptz, 시장 데이터 표는 hypertable.
-- - 여러 번 돌려도 같은 결과(멱등): compose initdb 로 한 번, P2 적용기(GEXLAB db/migrate.py 승계)로
--   다시 적용돼도 깨지지 않는다. 적용된 뒤엔 고치지 않는다 — 바꿀 일은 새 번호 파일로.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ── 공개 등급 (DATA_TIERS §1 "공개") ──────────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS pub_filings;       -- DART 공시·잠정실적·오버행·내부자·자사주·corp_code
CREATE SCHEMA IF NOT EXISTS pub_fin;           -- DART 재무만으로 만든 분기 재무·비율
CREATE SCHEMA IF NOT EXISTS pub_trade;         -- 관세청 수출입(월간·10일 잠정·시군구)
CREATE SCHEMA IF NOT EXISTS pub_macro;         -- ECOS 한은 작성 표·KOSIS·FRED 정부 시리즈·한은 보고서
CREATE SCHEMA IF NOT EXISTS pub_market_stats;  -- 금투협 종합통계(신용공여 잔고·예탁금·펀드·CMA)
CREATE SCHEMA IF NOT EXISTS pub_themes;        -- 자체 분류 사전(테마·밸류체인·종목·근거)

-- ── 로그인 등급 (DATA_TIERS §1 "로그인") ──────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS prv_market;        -- KIS·KRX·금융위 시세, 스냅·일봉·유니버스
CREATE SCHEMA IF NOT EXISTS prv_flows;         -- 투자자 수급(종목·시장)
CREATE SCHEMA IF NOT EXISTS prv_board;         -- 신고가 상태·라벨·단계 산출
CREATE SCHEMA IF NOT EXISTS prv_gex;           -- 옵션 체인·GEX 지표(GEXLAB 21표)
CREATE SCHEMA IF NOT EXISTS prv_fin;           -- 컨센서스·리비전·밸류에이션 밴드·시세가 들어간 비율
CREATE SCHEMA IF NOT EXISTS prv_themes;        -- 라이선스 미확인 분류·시세 기반 테마 강도
CREATE SCHEMA IF NOT EXISTS prv_macro;         -- Yahoo·ForexFactory·ECOS 타기관 표·FRED 저작권 시리즈
CREATE SCHEMA IF NOT EXISTS prv_etf;           -- ETF 구성종목·운용사 데이터
CREATE SCHEMA IF NOT EXISTS prv_journal;       -- 분석 일지·추천·매매·포트폴리오·관심종목(개인)
CREATE SCHEMA IF NOT EXISTS prv_alerts;        -- 알림 규칙·발송 이력·큐(개인)

-- ── 운영 ───────────────────────────────────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS ops;               -- 작업 실행 기록·health·수집 공백·마이그레이션 기록

-- 마이그레이션 기록 — GEXLAB db/migrate.py 의 schema_migrations 와 같은 열(conflict_map §1.5)
CREATE TABLE IF NOT EXISTS ops.schema_migrations (
    version     text PRIMARY KEY,
    name        text NOT NULL,
    checksum    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

-- ── 공개 내보내기 역할 ──────────────────────────────────────────────────────────────────
-- 로그인하지 않는 그룹 역할이다. 비밀번호는 여기 두지 않는다 — 내보내기 작업이 접속할 로그인 역할은
-- 운영 배포에서 이 역할의 구성원으로 만든다(ADR 0002 §3).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'kbj_public_export') THEN
        CREATE ROLE kbj_public_export NOLOGIN;
    END IF;
END
$$;

-- 새 스키마에는 PUBLIC 권한이 없지만, 누가 바꿔 놓았어도 다시 닫는다
REVOKE ALL ON SCHEMA
    pub_filings, pub_fin, pub_trade, pub_macro, pub_market_stats, pub_themes,
    prv_market, prv_flows, prv_board, prv_gex, prv_fin, prv_themes, prv_macro, prv_etf,
    prv_journal, prv_alerts, ops
    FROM PUBLIC;

-- kbj_public_export 는 pub_* 에만 USAGE·SELECT
GRANT USAGE ON SCHEMA
    pub_filings, pub_fin, pub_trade, pub_macro, pub_market_stats, pub_themes
    TO kbj_public_export;
GRANT SELECT ON ALL TABLES IN SCHEMA
    pub_filings, pub_fin, pub_trade, pub_macro, pub_market_stats, pub_themes
    TO kbj_public_export;
-- 앞으로 이 마이그레이션을 돌린 역할(앱 소유자)이 pub_* 에 만드는 표도 SELECT 만
ALTER DEFAULT PRIVILEGES IN SCHEMA
    pub_filings, pub_fin, pub_trade, pub_macro, pub_market_stats, pub_themes
    GRANT SELECT ON TABLES TO kbj_public_export;
