-- 0008_market_flows_p3 — 장중 이력(지수·업종·거래대금 순위·가집계)·D+1 대조·잠정→확정 차이·검산
-- (docs/p3_design.md §3.4~§3.6, D-P3-7·8·14, docs/metrics.md §0~§2·§6)
--
-- 규칙(0003 과 같다)
-- - 로그인 등급(KIS·KRX 시세)이라 prv_market·prv_flows 에만 둔다(ADR 0002). kbj_public_export 는
--   못 읽는다(0001 의 기본 권한은 pub_* 뿐).
-- - 값 행은 source·quality·received_at·loaded_by 를 갖는다. 장중 값은 증권사 추정·잠정이라
--   quality=estimated(metrics §2). 시각은 timestamptz(UTC 로 저장, 화면에서 KST) — ts = 10분 슬롯
--   시작(kbj.data.spec AsOfKind slot10m).
-- - KR 금액은 원 단위 정수(bigint), 수량 bigint. venue = 거래소 구분('' · KRX · NXT · TOTAL).
-- - 장중 이력 표는 hypertable(청크 1일) + 보존 정책(90일 [제안] — R21, ops.nightly 가 크기를 본다).
--   일별 원장의 오늘 행은 0003 의 stock_investor_daily·stock_snapshot 에 쓴다(이 파일은 이력만).
-- - 기록 표(eod_reconcile·investor_revision·ledger_check)는 값이 아니라 대조·차이·검산 결과다 —
--   출처는 열 이름(kis_value·krx_value·final_source)과 checked_at·revised_at 이 말한다.
-- - 멱등: CREATE … IF NOT EXISTS, create_hypertable·add_retention_policy(if_not_exists).

-- ── 장중 지수 (KIS:index_quote_intraday — FHPUP02100000 [추정 TR]) ──────────────────────────
-- index_code = KIS 업종 코드(코스피 0001 · 코스닥 1001 · 코스피200 2001 [실측 필요]). turnover =
-- 누적 거래대금(원) — 장중 시장 거래대금(지수 기준, estimated)의 원천(§4.4, R23).
CREATE TABLE IF NOT EXISTS prv_market.index_intraday (
    index_code   text NOT NULL CHECK (index_code <> '' AND index_code !~ '\s'),
    ts           timestamptz NOT NULL,
    source       text NOT NULL CHECK (source <> ''),
    name         text,
    value        numeric,
    chg_pct      numeric,
    turnover     bigint CHECK (turnover IS NULL OR turnover >= 0),
    volume       bigint CHECK (volume IS NULL OR volume >= 0),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (index_code, ts, source)
);
SELECT create_hypertable('prv_market.index_intraday', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE);
SELECT add_retention_policy('prv_market.index_intraday', INTERVAL '90 days', if_not_exists => TRUE);

-- ── 장중 업종 지수 (KIS:sector_quote_intraday — FHPUP02140000 [추정 TR]) ─────────────────────
-- market = KOSPI·KOSDAQ(시장마다 1회 호출로 전 업종). sector_code = KIS 업종 코드.
CREATE TABLE IF NOT EXISTS prv_market.sector_intraday (
    market       text NOT NULL CHECK (market <> ''),
    sector_code  text NOT NULL CHECK (sector_code <> '' AND sector_code !~ '\s'),
    ts           timestamptz NOT NULL,
    source       text NOT NULL CHECK (source <> ''),
    name         text,
    value        numeric,
    chg_pct      numeric,
    turnover     bigint CHECK (turnover IS NULL OR turnover >= 0),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (market, sector_code, ts, source)
);
SELECT create_hypertable('prv_market.sector_intraday', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE);
SELECT add_retention_policy('prv_market.sector_intraday', INTERVAL '90 days', if_not_exists => TRUE);

-- ── 장중 거래대금 순위 (KIS:turnover_rank_intraday — FHPST01710000) ─────────────────────────
-- 순위 목록(상위 N — [실측 필요])이라 키는 (시장, 슬롯, 순위, 거래소). 한 슬롯 안 같은 종목은 한 번.
CREATE TABLE IF NOT EXISTS prv_market.turnover_rank_intraday (
    market       text NOT NULL CHECK (market <> ''),
    ts           timestamptz NOT NULL,
    rank         integer NOT NULL CHECK (rank >= 1),
    venue        text NOT NULL DEFAULT '' CHECK (venue IN ('', 'KRX', 'NXT', 'TOTAL')),
    code         text NOT NULL CHECK (code <> '' AND code !~ '\s'),
    name         text,
    turnover     bigint CHECK (turnover IS NULL OR turnover >= 0),
    chg_pct      numeric,
    source       text NOT NULL CHECK (source <> ''),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (market, ts, rank, venue)
);
SELECT create_hypertable(
    'prv_market.turnover_rank_intraday', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE
);
SELECT add_retention_policy(
    'prv_market.turnover_rank_intraday', INTERVAL '90 days', if_not_exists => TRUE
);

-- ── KIS 마감값 대 KRX 확정값 D+1 대조 (§3.6 — ADR 0001 Q1, D-P3-8) ──────────────────────────
-- field: close(정확히 같아야)·turnover(허용 config/markets.yaml reconcile.turnover_tol_pct)·mktcap.
-- verdict: ok · mismatch(허용 밖 — KIS 행을 invalid 로) · missing_kis · missing_krx.
CREATE TABLE IF NOT EXISTS prv_market.eod_reconcile (
    trade_date  date NOT NULL,
    code        text NOT NULL CHECK (code <> '' AND code !~ '\s'),
    field       text NOT NULL CHECK (field IN ('close', 'turnover', 'mktcap')),
    kis_value   numeric,
    krx_value   numeric,
    diff_pct    numeric,
    verdict     text NOT NULL CHECK (verdict IN ('ok', 'mismatch', 'missing_kis', 'missing_krx')),
    checked_at  timestamptz NOT NULL,
    PRIMARY KEY (trade_date, code, field)
);
SELECT create_hypertable(
    'prv_market.eod_reconcile', by_range('trade_date', INTERVAL '365 days'), if_not_exists => TRUE
);
CREATE INDEX IF NOT EXISTS eod_reconcile_verdict_idx
    ON prv_market.eod_reconcile (trade_date, verdict);

-- ── 장중 가집계 이력 (KIS:inst_foreign_intraday — FHPTJ04400000, 증권사 추정치) ─────────────────
-- 상위 목록에 든 종목만 슬롯마다 한 줄씩. investor 는 0003 과 같은 소문자 이름(foreign·institution …).
-- rank = 목록 안 순위(모르면 NULL). source 는 'kis.prelim'(원장 우선순위 — D-P3-7).
CREATE TABLE IF NOT EXISTS prv_flows.investor_intraday (
    code         text NOT NULL CHECK (code <> '' AND code !~ '\s'),
    ts           timestamptz NOT NULL,
    investor     text NOT NULL CHECK (investor ~ '^[a-z][a-z_]*$'),
    venue        text NOT NULL DEFAULT '' CHECK (venue IN ('', 'KRX', 'NXT', 'TOTAL')),
    net_qty      bigint,
    net_value    bigint,
    rank         integer CHECK (rank IS NULL OR rank >= 1),
    source       text NOT NULL CHECK (source <> ''),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    CHECK (net_qty IS NOT NULL OR net_value IS NOT NULL),
    PRIMARY KEY (code, ts, investor, venue)
);
SELECT create_hypertable('prv_flows.investor_intraday', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE);
SELECT add_retention_policy('prv_flows.investor_intraday', INTERVAL '90 days', if_not_exists => TRUE);

-- ── 잠정 → 확정 덮어쓰기 차이 (§3.5, D-P3-7 — 마감 수집이 같은 트랜잭션으로 남긴다) ───────────────
-- est_* = 덮기 전 잠정 행(kis.prelim·estimated), final_* = 마감 확정 행. diff = final − est(원).
CREATE TABLE IF NOT EXISTS prv_flows.investor_revision (
    code          text NOT NULL CHECK (code <> '' AND code !~ '\s'),
    trade_date    date NOT NULL,
    investor      text NOT NULL CHECK (investor ~ '^[a-z][a-z_]*$'),
    venue         text NOT NULL DEFAULT '' CHECK (venue IN ('', 'KRX', 'NXT', 'TOTAL')),
    est_value     bigint,
    est_ts        timestamptz,
    final_value   bigint,
    final_source  text NOT NULL CHECK (final_source <> ''),
    diff          bigint,
    revised_at    timestamptz NOT NULL,
    PRIMARY KEY (code, trade_date, investor, venue)
);
SELECT create_hypertable(
    'prv_flows.investor_revision', by_range('trade_date', INTERVAL '365 days'), if_not_exists => TRUE
);

-- ── 검산 실패 기록 (metrics §2 ①② · §4 ③ — 실패 행은 원장에서 invalid, 여기에 잔차) ──────────────
-- domain: stock(종목 원장) · etf(ETF 순유입). check_id: c1 4구분 합 = 0 · c2 7구분 합 = 기관 ·
-- c3 순자산 변화 = 순유입 + 가격효과. 검산 불가(구분 미제공)는 행을 만들지 않고 화면 notes 로.
CREATE TABLE IF NOT EXISTS prv_flows.ledger_check (
    domain      text NOT NULL CHECK (domain IN ('stock', 'etf')),
    trade_date  date NOT NULL,
    code        text NOT NULL CHECK (code <> '' AND code !~ '\s'),
    check_id    text NOT NULL CHECK (check_id IN ('c1', 'c2', 'c3')),
    residual    numeric,
    detail      jsonb NOT NULL DEFAULT '{}'::jsonb,
    checked_at  timestamptz NOT NULL,
    PRIMARY KEY (domain, trade_date, code, check_id)
);
CREATE INDEX IF NOT EXISTS ledger_check_date_idx ON prv_flows.ledger_check (trade_date, domain);
