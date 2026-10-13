-- 0011_fin — DART 재무(원시 행·분기·비율)·dart-report 산출·부문 매출 (docs/p4_design.md §3.4·§4.5·§4.6)
--
-- 규칙
-- - 공개 등급(DART 재무만으로 만든 값 — DATA_TIERS §1·§2 8행)이라 pub_fin 에 둔다. source 는 'DART' 만
--   (CHECK). 시세가 들어가는 배수(PER·PBR·EV/EBITDA)·컨센서스는 prv_fin(0012)에 둔다(ADR 0002).
-- - 금액은 원 단위 bigint(DART 원 정수 — 외국 통화 회사는 currency 열, 환산하지 않음). 손익은 **분기
--   단독**(누적 차분 — Q4 = 연간 − 3분기 누적, ADR 0001 Q4 기본값·D-P4-5), 상태표는 분기말.
-- - quarterly.rcept_dt = 그 분기를 담은 정기보고서 접수일 — 밸류에이션 PIT 기준(D-P4-11).
-- - 값 행은 source·quality·received_at(또는 computed_at)·loaded_by. 멱등: CREATE … IF NOT EXISTS.

-- ── 단일회사 전체 재무제표 원시 행 (DART:fnlttSinglAcntAll — 상세 층만, 약 350사) ─────────────────
-- rows = 응답 list 그대로(jsonb). 같은 키를 다시 받으면 덮어쓴다(정정 보고서는 rcept_no 가 큰 쪽).
-- 크기 약 350사 × 20보고서 × 30KB(TOAST 압축 전) [추정 — R10].
CREATE TABLE IF NOT EXISTS pub_fin.statement_raw (
    corp_code    text NOT NULL CHECK (corp_code ~ '^[0-9]{8}$'),
    bsns_year    smallint NOT NULL CHECK (bsns_year BETWEEN 1990 AND 2100),
    reprt_code   text NOT NULL CHECK (reprt_code IN ('11013', '11012', '11014', '11011')),
    fs_div       text NOT NULL CHECK (fs_div IN ('CFS', 'OFS')),
    rcept_no     text CHECK (rcept_no IS NULL OR rcept_no ~ '^[0-9]{14}$'),
    rows         jsonb NOT NULL,
    n_rows       integer NOT NULL CHECK (n_rows >= 0),
    currency     text CHECK (currency IS NULL OR currency ~ '^[A-Z]{3}$'),
    source       text NOT NULL DEFAULT 'DART' CHECK (source = 'DART'),
    quality      text NOT NULL DEFAULT 'ok' CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (corp_code, bsns_year, reprt_code, fs_div)
);

-- ── 분기 재무 (정규화 — normalize(to_quarterly), §4.5) ─────────────────────────────────────────
-- tier: main(주요계정 — fnlttMultiAcnt, 전 상장사)·full(전체 재무제표 — 상세 층). derived = 직전 누적이
-- 없어 차분을 못 했다(손익 칸 NULL — 지어내지 않는다). reported_3m = 보고서의 3개월값(대조 열 — 값으로 쓰지
-- 않는다), diff3m_max_pct > 1 이면 quality estimated + notes. eps_basic = 분기 단독 기본주당이익(원/주).
CREATE TABLE IF NOT EXISTS pub_fin.quarterly (
    corp_code          text NOT NULL CHECK (corp_code ~ '^[0-9]{8}$'),
    fiscal_year        smallint NOT NULL CHECK (fiscal_year BETWEEN 1990 AND 2100),
    fiscal_q           smallint NOT NULL CHECK (fiscal_q BETWEEN 1 AND 4),
    fs_div             text NOT NULL CHECK (fs_div IN ('CFS', 'OFS')),
    period_start       date NOT NULL,
    period_end         date NOT NULL,
    rcept_dt           date,
    tier               text NOT NULL CHECK (tier IN ('main', 'full')),
    revenue            bigint,
    cogs               bigint,
    gross_profit       bigint,
    sgna               bigint,
    op                 bigint,
    pretax             bigint,
    tax                bigint,
    ni                 bigint,
    ni_owner           bigint,
    d_and_a            bigint,
    ebitda             bigint,
    total_assets       bigint,
    total_liabilities  bigint,
    total_equity       bigint,
    equity_owner       bigint,
    cash               bigint,
    short_inv          bigint,
    total_debt         bigint,
    net_debt           bigint,
    minority           bigint,
    eps_basic          numeric,
    derived            boolean NOT NULL DEFAULT false,
    reported_3m        jsonb NOT NULL DEFAULT '{}',
    diff3m_max_pct     numeric,
    currency           text NOT NULL DEFAULT 'KRW' CHECK (currency ~ '^[A-Z]{3}$'),
    notes              jsonb NOT NULL DEFAULT '[]',
    source             text NOT NULL DEFAULT 'DART' CHECK (source = 'DART'),
    quality            text NOT NULL DEFAULT 'ok' CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    computed_at        timestamptz NOT NULL,
    loaded_by          text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (corp_code, fiscal_year, fiscal_q, fs_div),
    CHECK (period_start < period_end),
    CHECK (rcept_dt IS NULL OR rcept_dt >= period_end)
);
CREATE INDEX IF NOT EXISTS quarterly_pit_idx ON pub_fin.quarterly (corp_code, rcept_dt);

-- ── DART 비율 (공개 — §4.5, metrics §9) ─────────────────────────────────────────────────────
-- 분모 ≤ 0 이면 값 NULL + na_reasons {지표: 사유}. fs_div = 근거 기준(CFS 우선). shares_basis = BPS 분모
-- (보통주 발행 − 보통주 자기주식, DART 주식총수).
CREATE TABLE IF NOT EXISTS pub_fin.ratio (
    corp_code       text NOT NULL CHECK (corp_code ~ '^[0-9]{8}$'),
    fiscal_year     smallint NOT NULL CHECK (fiscal_year BETWEEN 1990 AND 2100),
    fiscal_q        smallint NOT NULL CHECK (fiscal_q BETWEEN 1 AND 4),
    fs_div          text NOT NULL CHECK (fs_div IN ('CFS', 'OFS')),
    ttm_revenue     bigint,
    ttm_op          bigint,
    ttm_ni_owner    bigint,
    opm             numeric,
    npm             numeric,
    roe             numeric,
    roa             numeric,
    debt_ratio      numeric,
    net_debt_ratio  numeric,
    rev_yoy         numeric,
    op_yoy          numeric,
    eps_ttm         numeric,
    bps             numeric,
    shares_basis    bigint CHECK (shares_basis IS NULL OR shares_basis >= 0),
    na_reasons      jsonb NOT NULL DEFAULT '{}',
    notes           jsonb NOT NULL DEFAULT '[]',
    source          text NOT NULL DEFAULT 'DART' CHECK (source = 'DART'),
    quality         text NOT NULL DEFAULT 'ok' CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    computed_at     timestamptz NOT NULL,
    loaded_by       text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (corp_code, fiscal_year, fiscal_q)
);

-- ── dart-report 산출 (reports.dart_excel — 화면 블록 payload 와 엑셀이 같은 입력에서, §4.6) ──────────
-- xlsx ≤ 2 MiB. 종목마다 최근 4개만 남긴다(ops.nightly). input_digest 가 같으면 다시 만들지 않는다.
CREATE TABLE IF NOT EXISTS pub_fin.report (
    stock_code      text NOT NULL CHECK (stock_code ~ '^[0-9A-Z]{6}$'),
    built_on        date NOT NULL,
    corp_code       text NOT NULL CHECK (corp_code ~ '^[0-9]{8}$'),
    year_from       smallint NOT NULL CHECK (year_from BETWEEN 1990 AND 2100),
    year_to         smallint NOT NULL CHECK (year_to BETWEEN 1990 AND 2100),
    fs_div_used     text NOT NULL CHECK (fs_div_used IN ('CFS', 'OFS')),
    payload         jsonb NOT NULL,
    xlsx            bytea CHECK (xlsx IS NULL OR octet_length(xlsx) <= 2097152),
    xlsx_sha256     text CHECK (xlsx_sha256 IS NULL OR xlsx_sha256 ~ '^[0-9a-f]{64}$'),
    warnings        jsonb NOT NULL DEFAULT '[]',
    engine_version  text NOT NULL CHECK (engine_version <> ''),
    input_digest    text NOT NULL CHECK (input_digest <> ''),
    generated_at    timestamptz NOT NULL,
    source          text NOT NULL DEFAULT 'DART' CHECK (source = 'DART'),
    quality         text NOT NULL DEFAULT 'ok' CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    loaded_by       text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (stock_code, built_on),
    CHECK (year_from <= year_to),
    CHECK ((xlsx IS NULL) = (xlsx_sha256 IS NULL))
);

-- ── 부문 매출 (사업보고서 원문 표 best-effort — quality estimated, D-P4-22) ───────────────────────
CREATE TABLE IF NOT EXISTS pub_fin.segment (
    corp_code    text NOT NULL CHECK (corp_code ~ '^[0-9]{8}$'),
    bsns_year    smallint NOT NULL CHECK (bsns_year BETWEEN 1990 AND 2100),
    segment      text NOT NULL CHECK (segment <> ''),
    amounts      jsonb NOT NULL,
    rcept_no     text CHECK (rcept_no IS NULL OR rcept_no ~ '^[0-9]{14}$'),
    source       text NOT NULL DEFAULT 'DART' CHECK (source = 'DART'),
    quality      text NOT NULL DEFAULT 'estimated' CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (corp_code, bsns_year, segment)
);

-- 공개 내보내기 역할 — SELECT 만(0004 방식, 기본 권한과 같은 결과)
GRANT SELECT ON pub_fin.statement_raw TO kbj_public_export;
GRANT SELECT ON pub_fin.quarterly TO kbj_public_export;
GRANT SELECT ON pub_fin.ratio TO kbj_public_export;
GRANT SELECT ON pub_fin.report TO kbj_public_export;
GRANT SELECT ON pub_fin.segment TO kbj_public_export;
