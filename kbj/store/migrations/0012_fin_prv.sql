-- 0012_fin_prv — 컨센서스·투자의견·목표가·리비전·밸류에이션 일별·밴드·시나리오 적정가·잠정실적 분류·
-- 종류주식 (docs/p4_design.md §3.4·§4.7~§4.9, D-P4-9~12·16)
--
-- 규칙
-- - 로그인 등급(KIS 컨센서스 — 제3자 유료·재배포 금지, 시세가 들어간 배수 — DATA_TIERS §1)이라 prv_fin·
--   prv_market 에만 둔다(ADR 0002). 0001 의 ALTER DEFAULT PRIVILEGES 는 pub_* 에만 걸려 있어
--   kbj_public_export 는 이 표들을 못 읽는다(통합 시험이 확인).
-- - 옛 SD 네이버 컨센서스·밴드·서프라이즈 행은 옮기지 않는다(U-P4-2 채택 — §3.8). 이 표들은 비어서 시작한다.
-- - 값 행은 source·quality·received_at(또는 computed_at)·loaded_by. 금액은 원 단위 bigint, 가격·배수·비율은
--   numeric. 종목코드 text(^[0-9A-Z]{6}$). 멱등: CREATE … IF NOT EXISTS, create_hypertable(if_not_exists).

-- ── 추정실적 스냅 (KIS:consensus_estimate — TR 은 kis/datasets.py CONSENSUS_TRS [확인 필요 #29]) ────────
-- fiscal_ym = 결산년월 YYYYMM, is_estimate = 추정(E)/실적(A). 금액은 원(KIS 억원이면 파서가 원으로).
-- extra = KIS 가 준 비율 등(계산에 쓰지 않는다). P4 동안 수집 작업은 꺼져 있다(D-P4-10).
CREATE TABLE IF NOT EXISTS prv_fin.consensus_snapshot (
    code         text NOT NULL CHECK (code ~ '^[0-9A-Z]{6}$'),
    snap_date    date NOT NULL,
    fiscal_ym    text NOT NULL CHECK (fiscal_ym ~ '^[0-9]{4}(0[1-9]|1[0-2])$'),
    source       text NOT NULL CHECK (source <> ''),
    is_estimate  boolean NOT NULL,
    revenue      bigint,
    op           bigint,
    ni           bigint,
    ni_owner     bigint,
    eps          numeric,
    ebitda       bigint,
    extra        jsonb NOT NULL DEFAULT '{}',
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (code, snap_date, fiscal_ym, source)
);
CREATE INDEX IF NOT EXISTS consensus_snapshot_day_idx ON prv_fin.consensus_snapshot (snap_date);

-- ── 증권사 투자의견 (KIS:invest_opinion — 증권사 이름은 원문 그대로) ───────────────────────────
CREATE TABLE IF NOT EXISTS prv_fin.invest_opinion (
    code          text NOT NULL CHECK (code ~ '^[0-9A-Z]{6}$'),
    broker        text NOT NULL CHECK (broker <> ''),
    opinion_date  date NOT NULL,
    source        text NOT NULL CHECK (source <> ''),
    opinion       text,
    opinion_prev  text,
    target_price  numeric,
    quality       text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at   timestamptz NOT NULL,
    loaded_by     text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (code, broker, opinion_date, source)
);

-- ── 목표가 컨센서스 (파생 — 90일 창 증권사별 마지막 행, n ≥ 3 ok · 1~2 estimated, §4.7) ──────────────
CREATE TABLE IF NOT EXISTS prv_fin.target_consensus (
    code         text NOT NULL CHECK (code ~ '^[0-9A-Z]{6}$'),
    as_of        date NOT NULL,
    mean         numeric NOT NULL CHECK (mean > 0),
    median       numeric NOT NULL CHECK (median > 0),
    high         numeric NOT NULL CHECK (high > 0),
    low          numeric NOT NULL CHECK (low > 0),
    n_brokers    integer NOT NULL CHECK (n_brokers >= 1),
    window_days  integer NOT NULL CHECK (window_days > 0),
    source       text NOT NULL CHECK (source <> ''),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    computed_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (code, as_of),
    CHECK (n_brokers >= 3 OR quality <> 'ok')
);

-- ── 리비전 (파생 — SD revision_alerts 정의 하나로, 기준선 = t − W 이전 가장 늦은 스냅, §4.7) ─────────
-- metric: revenue·op·ni·eps·target_mean(목표가 평균 — fiscal_ym '000000'). 부호 전환은 signal TURN(pct NULL).
CREATE TABLE IF NOT EXISTS prv_fin.revision (
    code           text NOT NULL CHECK (code ~ '^[0-9A-Z]{6}$'),
    as_of          date NOT NULL,
    metric         text NOT NULL CHECK (metric IN ('revenue', 'op', 'ni', 'eps', 'target_mean')),
    fiscal_ym      text NOT NULL CHECK (fiscal_ym ~ '^[0-9]{6}$'),
    window_days    smallint NOT NULL CHECK (window_days IN (7, 30)),
    current_value  numeric,
    baseline       numeric,
    baseline_date  date,
    pct            numeric,
    signal         text CHECK (signal IS NULL OR signal IN ('STRONG_UP', 'UP', 'NEUTRAL', 'DOWN', 'STRONG_DOWN', 'TURN')),
    source         text NOT NULL CHECK (source <> ''),
    quality        text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    computed_at    timestamptz NOT NULL,
    loaded_by      text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (code, as_of, metric, fiscal_ym, window_days),
    CHECK (signal IS DISTINCT FROM 'TURN' OR pct IS NULL)
);

-- ── 밸류에이션 일별 (PIT — t 에 공시된 분기만, 회사 시총 = 보통 + 우선, §4.8) ─────────────────────────
-- ttm_ref = 그날 쓴 마지막 분기('2026Q2:CFS'). flags = {'na': {지표: na_reason}, 'out_of_cap': [지표 …]}.
-- 보존 없음. 압축 90일 뒤는 [제안] — P4 에서는 켜지 않는다(행 수 약 2,700 × 250/년 — 작다).
CREATE TABLE IF NOT EXISTS prv_fin.valuation_daily (
    code            text NOT NULL CHECK (code ~ '^[0-9A-Z]{6}$'),
    trade_date      date NOT NULL,
    mktcap_company  bigint CHECK (mktcap_company IS NULL OR mktcap_company >= 0),
    price           numeric,
    ttm_ref         text,
    per             numeric,
    pbr             numeric,
    psr             numeric,
    ev              bigint,
    ev_ebitda       numeric,
    per_fwd         numeric,
    psr_fwd         numeric,
    ev_ebitda_fwd   numeric,
    flags           jsonb NOT NULL DEFAULT '{}',
    source          text NOT NULL CHECK (source <> ''),
    quality         text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    computed_at     timestamptz NOT NULL,
    loaded_by       text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (code, trade_date)
);
SELECT create_hypertable(
    'prv_fin.valuation_daily', by_range('trade_date', INTERVAL '365 days'), if_not_exists => TRUE
);
CREATE INDEX IF NOT EXISTS valuation_daily_date_idx ON prv_fin.valuation_daily (trade_date);

-- ── 배수 밴드 (최근 1,240거래일 유효 값의 선형 백분위 — 유효 < 250일이면 NULL + na_reason) ─────────────
-- current_rank_pct = 100 × #{x ≤ 현재} ÷ n(경험적 — SD 조각 선형과 다르다, ADR 0016).
CREATE TABLE IF NOT EXISTS prv_fin.valuation_band (
    code              text NOT NULL CHECK (code ~ '^[0-9A-Z]{6}$'),
    as_of             date NOT NULL,
    metric            text NOT NULL CHECK (metric IN ('per', 'pbr', 'psr', 'ev_ebitda')),
    p10               numeric,
    p25               numeric,
    p50               numeric,
    p75               numeric,
    p90               numeric,
    min_value         numeric,
    max_value         numeric,
    n_days            integer NOT NULL CHECK (n_days >= 0),
    n_excluded        integer NOT NULL CHECK (n_excluded >= 0),
    current_value     numeric,
    current_rank_pct  numeric CHECK (current_rank_pct IS NULL OR current_rank_pct BETWEEN 0 AND 100),
    window_from       date,
    na_reason         text,
    source            text NOT NULL CHECK (source <> ''),
    quality           text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    computed_at       timestamptz NOT NULL,
    loaded_by         text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (code, as_of, metric),
    CHECK (p50 IS NOT NULL OR na_reason IS NOT NULL)
);

-- ── 시나리오 적정가 (구 TAM — EPS × PER 시나리오, D-P4-12, §4.9) ────────────────────────────────
-- eps_source·multiple_source = SD 와 같은 문자열 체계(CONSENSUS·TTM_EPS·BASE_×0.7·5Y_P25 …).
CREATE TABLE IF NOT EXISTS prv_fin.scenario (
    code             text NOT NULL CHECK (code ~ '^[0-9A-Z]{6}$'),
    as_of            date NOT NULL,
    scenario_case    text NOT NULL CHECK (scenario_case IN ('bear', 'base', 'bull')),
    method           text NOT NULL CHECK (method IN ('PER', 'PBR', 'INSUFFICIENT')),
    eps              numeric,
    eps_source       text,
    multiple         numeric,
    multiple_source  text,
    tp               numeric,
    upside_pct       numeric,
    price            numeric,
    na_reason        text,
    source           text NOT NULL CHECK (source <> ''),
    quality          text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    computed_at      timestamptz NOT NULL,
    loaded_by        text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (code, as_of, scenario_case),
    CHECK (method <> 'INSUFFICIENT' OR (tp IS NULL AND na_reason IS NOT NULL))
);

-- ── 잠정실적 분류·알림 기록 (earnings.alerts — YoY 분류(공개 규칙) + 서프라이즈(분기 컨센서스 있을 때만)) ──
-- 행이 없으면 '미처리'(earnings.alerts 가 다음 5분에 본다 — D-P4-4). 성과 = 알림 직전 마감 종가 대비 3·5·7거래일.
CREATE TABLE IF NOT EXISTS prv_fin.earnings_surprise (
    rcept_no        text PRIMARY KEY CHECK (rcept_no ~ '^[0-9]{14}$'),
    code            text CHECK (code IS NULL OR code ~ '^[0-9A-Z]{6}$'),
    corp_code       text NOT NULL CHECK (corp_code ~ '^[0-9]{8}$'),
    period          text NOT NULL CHECK (period ~ '^[0-9]{4}(Q[1-4]|FY)$'),
    scope           text NOT NULL CHECK (scope IN ('consolidated', 'standalone', 'subsidiary')),
    signal          text NOT NULL CHECK (signal ~ '^[A-Z][A-Z_]*$'),
    priority        smallint NOT NULL CHECK (priority BETWEEN 1 AND 9),
    rev_yoy         numeric,
    op_yoy          numeric,
    rev_surprise    numeric,
    op_surprise     numeric,
    consensus_ref   text,
    alerted_at      timestamptz,
    notify_ticket   text,
    price_at_alert  numeric,
    perf_3d         numeric,
    perf_5d         numeric,
    perf_7d         numeric,
    perf_done       boolean NOT NULL DEFAULT false,
    notes           jsonb NOT NULL DEFAULT '[]',
    source          text NOT NULL CHECK (source <> ''),
    quality         text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    computed_at     timestamptz NOT NULL,
    loaded_by       text NOT NULL CHECK (loaded_by <> '')
);
CREATE INDEX IF NOT EXISTS earnings_surprise_code_idx ON prv_fin.earnings_surprise (code, period);
CREATE INDEX IF NOT EXISTS earnings_surprise_perf_idx
    ON prv_fin.earnings_surprise (alerted_at) WHERE alerted_at IS NOT NULL AND NOT perf_done;

-- ── 종류주식 → 회사 (KRX 기본정보 + 이름 규칙 — 둘 다 맞을 때만, R13) ──────────────────────────────
-- 우선주는 DART corp_code 가 없다 → common_code 의 회사 재무·공시를 쓴다(§4.5·§5.3).
CREATE TABLE IF NOT EXISTS prv_market.share_class (
    code         text PRIMARY KEY CHECK (code ~ '^[0-9A-Z]{6}$'),
    corp_code    text CHECK (corp_code IS NULL OR corp_code ~ '^[0-9]{8}$'),
    share_class  text NOT NULL CHECK (share_class IN ('common', 'pref')),
    common_code  text NOT NULL CHECK (common_code ~ '^[0-9A-Z]{6}$'),
    source       text NOT NULL CHECK (source <> ''),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    updated_at   timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    CHECK ((share_class = 'common') = (common_code = code))
);
CREATE INDEX IF NOT EXISTS share_class_common_idx ON prv_market.share_class (common_code);
