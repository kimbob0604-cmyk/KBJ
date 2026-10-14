-- 0021_rules_journal — 규칙 알림·백테스트·저널(docs/p8_design.md §3.3, D-P8-9·14·15·16·19, metrics §30).
-- 행 자료형 kbj/core/rules_rows.py, 저장소 kbj/store/repos/rules.py·personal.py·journal.py.
--
-- 규칙
-- - 개인 기능(DATA_TIERS §2 — 페이지 11·12 전부 로그인)이라 prv_alerts·prv_journal 에만 둔다. 공개 내보내기
--   역할(kbj_public_export)에는 아무 권한도 주지 않는다(통합 시험 — 새 표를 못 읽는다). 새 표는 앱(kbj_app)만.
-- - 표 하나에 쓰는 주체 하나(D-P8-15 — P8_TABLE_WRITERS): 장중 신호(rules.intraday)와 마감 신호(rules.close)를
--   두 표로 나누고 읽기는 뷰 prv_alerts.signal. 규칙·일지·매매는 쓰기 모듈 하나(kbj.services.personal.*).
-- - 수치 행은 source·as_of(또는 그에 해당하는 시각 칸)·quality(절대 규칙 1). 값 칸은 NULL 허용(0 으로 채우지
--   않는다) — 값이 없으면 quality 가 ok 일 수 없다(CHECK, 절대 규칙 2).
-- - 규칙 판(rule_version)은 바꾸지 않는다(새 판만 — 과거 신호가 어느 판에서 났는지 남는다, D-P8-16).
-- - 장중 슬롯 시세(watch_quote_slot)는 hypertable + 보존 2년 [제안 — config/rules.yaml retention]. 장중 재생
--   백테스트가 같은 입력을 다시 읽는다(D-P8-9).
-- - 멱등: CREATE … IF NOT EXISTS, CREATE OR REPLACE VIEW, create_hypertable·add_retention_policy(if_not_exists).
--   적용된 뒤엔 고치지 않는다(체크섬).

-- ── 규칙 ─────────────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS prv_alerts.rule (
    rule_id          text PRIMARY KEY CHECK (rule_id ~ '^r_[0-9a-z]{12}$'),
    name             text NOT NULL CHECK (name <> ''),
    current_version  integer NOT NULL CHECK (current_version >= 1),
    enabled          boolean NOT NULL DEFAULT false,
    archived_at      timestamptz,
    source           text NOT NULL CHECK (source IN ('api', 'cli', 'import:sd', 'example')),
    created_at       timestamptz NOT NULL,
    updated_at       timestamptz NOT NULL,
    loaded_by        text NOT NULL CHECK (loaded_by <> '')
);

CREATE TABLE IF NOT EXISTS prv_alerts.rule_version (
    rule_id     text NOT NULL REFERENCES prv_alerts.rule (rule_id),
    version     integer NOT NULL CHECK (version >= 1),
    dsl         jsonb NOT NULL,
    dsl_hash    text NOT NULL CHECK (dsl_hash ~ '^[0-9a-f]{64}$'),
    created_at  timestamptz NOT NULL,
    PRIMARY KEY (rule_id, version)
);

-- ── 장중 슬롯 시세(관심종목 10분 — KIS:watch_quotes_intraday, 부분 봉) ─────────────────────────
CREATE TABLE IF NOT EXISTS prv_alerts.watch_quote_slot (
    slot_ts      timestamptz NOT NULL,
    code         text NOT NULL CHECK (code ~ '^[0-9A-Z]{6}$'),
    open         double precision CHECK (open > 0),
    high         double precision CHECK (high > 0),
    low          double precision CHECK (low > 0),
    price        double precision CHECK (price > 0),
    volume       bigint CHECK (volume >= 0),
    turnover     bigint CHECK (turnover >= 0),
    change_pct   double precision,
    flags        text[] NOT NULL DEFAULT '{}',
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    source       text NOT NULL CHECK (source <> ''),
    received_at  timestamptz,
    CHECK (price IS NOT NULL OR quality <> 'ok'),
    PRIMARY KEY (slot_ts, code)
);
SELECT create_hypertable('prv_alerts.watch_quote_slot', by_range('slot_ts', INTERVAL '7 days'), if_not_exists => TRUE);
SELECT add_retention_policy('prv_alerts.watch_quote_slot', INTERVAL '730 days', if_not_exists => TRUE);

-- ── 신호(장중·마감 — 쓰는 작업이 다르므로 두 표) ────────────────────────────────────────────────
-- status: sent · cooldown(쿨다운으로 막힘 — 기록) · suppressed(notifier 문지기) · dry(발송 꺼짐) · skipped(사유).
CREATE TABLE IF NOT EXISTS prv_alerts.signal_intraday (
    signal_id    text PRIMARY KEY CHECK (signal_id ~ '^si_[0-9A-HJKMNP-TV-Z]{26}$'),
    rule_id      text NOT NULL REFERENCES prv_alerts.rule (rule_id),
    version      integer NOT NULL CHECK (version >= 1),
    dsl_hash     text NOT NULL CHECK (dsl_hash ~ '^[0-9a-f]{64}$'),
    code         text NOT NULL CHECK (code ~ '^[0-9A-Z]{6}$'),
    eval_ts      timestamptz NOT NULL,
    trade_date   date NOT NULL,
    entry_price  double precision CHECK (entry_price > 0),
    evidence     jsonb NOT NULL DEFAULT '{}'::jsonb,
    input_fp     text NOT NULL CHECK (input_fp ~ '^[0-9a-f]{16}$'),
    status       text NOT NULL CHECK (status IN ('sent', 'cooldown', 'suppressed', 'dry', 'skipped')),
    skip_reason  text CHECK (skip_reason IN ('split_suspect', 'halted', 'no_live_flow', 'truncated', 'no_data', 'error')),
    ticket       text,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    source       text NOT NULL CHECK (source <> ''),
    CHECK ((status = 'skipped') = (skip_reason IS NOT NULL)),
    UNIQUE (rule_id, version, code, eval_ts)
);
CREATE INDEX IF NOT EXISTS signal_intraday_rule_code_idx ON prv_alerts.signal_intraday (rule_id, code, eval_ts DESC);
CREATE INDEX IF NOT EXISTS signal_intraday_date_idx ON prv_alerts.signal_intraday (trade_date);

CREATE TABLE IF NOT EXISTS prv_alerts.signal_close (
    signal_id    text PRIMARY KEY CHECK (signal_id ~ '^sc_[0-9A-HJKMNP-TV-Z]{26}$'),
    rule_id      text NOT NULL REFERENCES prv_alerts.rule (rule_id),
    version      integer NOT NULL CHECK (version >= 1),
    dsl_hash     text NOT NULL CHECK (dsl_hash ~ '^[0-9a-f]{64}$'),
    code         text NOT NULL CHECK (code ~ '^[0-9A-Z]{6}$'),
    eval_ts      timestamptz NOT NULL,
    trade_date   date NOT NULL,
    entry_price  double precision CHECK (entry_price > 0),
    evidence     jsonb NOT NULL DEFAULT '{}'::jsonb,
    input_fp     text NOT NULL CHECK (input_fp ~ '^[0-9a-f]{16}$'),
    status       text NOT NULL CHECK (status IN ('sent', 'cooldown', 'suppressed', 'dry', 'skipped')),
    skip_reason  text CHECK (skip_reason IN ('split_suspect', 'halted', 'no_live_flow', 'truncated', 'no_data', 'error')),
    ticket       text,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    source       text NOT NULL CHECK (source <> ''),
    CHECK ((status = 'skipped') = (skip_reason IS NOT NULL)),
    UNIQUE (rule_id, version, code, eval_ts)
);
CREATE INDEX IF NOT EXISTS signal_close_rule_code_idx ON prv_alerts.signal_close (rule_id, code, eval_ts DESC);
CREATE INDEX IF NOT EXISTS signal_close_date_idx ON prv_alerts.signal_close (trade_date);

-- 읽기 전용 뷰(두 표 UNION ALL + mode)
CREATE OR REPLACE VIEW prv_alerts.signal AS
    SELECT 'intraday'::text AS mode, signal_id, rule_id, version, dsl_hash, code, eval_ts, trade_date,
           entry_price, evidence, input_fp, status, skip_reason, ticket, quality, source
      FROM prv_alerts.signal_intraday
    UNION ALL
    SELECT 'close'::text AS mode, signal_id, rule_id, version, dsl_hash, code, eval_ts, trade_date,
           entry_price, evidence, input_fp, status, skip_reason, ticket, quality, source
      FROM prv_alerts.signal_close;

-- ── 알림 후 성과(rules.outcome — 확정 일봉) ──────────────────────────────────────────────────
-- 신호 id 는 두 표 중 하나라 외래키를 두지 않는다(접두 si_/sc_ 로 가린다). 값이 없으면 reason.
CREATE TABLE IF NOT EXISTS prv_alerts.signal_outcome (
    signal_id    text NOT NULL CHECK (signal_id ~ '^(si|sc)_[0-9A-HJKMNP-TV-Z]{26}$'),
    horizon      integer NOT NULL CHECK (horizon >= 1),
    due_date     date NOT NULL,
    close_at     double precision CHECK (close_at > 0),
    ret          double precision,
    index_code   text,
    excess       double precision,
    mfe          double precision,
    mae          double precision,
    reason       text CHECK (reason IN ('halted', 'delisted', 'not_due', 'split_suspect', 'no_index')),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    computed_at  timestamptz NOT NULL,
    source       text NOT NULL CHECK (source <> ''),
    CHECK (ret IS NOT NULL OR reason IS NOT NULL),
    CHECK (ret IS NOT NULL OR quality <> 'ok'),
    PRIMARY KEY (signal_id, horizon)
);

-- ── 백테스트·일치 감시(rules.backtest_nightly) ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS prv_alerts.backtest_run (
    run_id        text PRIMARY KEY CHECK (run_id ~ '^bt_[0-9A-HJKMNP-TV-Z]{26}$'),
    rule_id       text NOT NULL REFERENCES prv_alerts.rule (rule_id),
    version       integer NOT NULL CHECK (version >= 1),
    dsl_hash      text NOT NULL CHECK (dsl_hash ~ '^[0-9a-f]{64}$'),
    mode          text NOT NULL CHECK (mode IN ('close', 'intraday_replay')),
    start_date    date NOT NULL,
    end_date      date NOT NULL,
    params        jsonb NOT NULL DEFAULT '{}'::jsonb,
    ledger_as_of  timestamptz NOT NULL,
    summary       jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL,
    quality       text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    source        text NOT NULL CHECK (source <> ''),
    CHECK (start_date <= end_date)
);
CREATE INDEX IF NOT EXISTS backtest_run_rule_idx ON prv_alerts.backtest_run (rule_id, created_at DESC);

CREATE TABLE IF NOT EXISTS prv_alerts.backtest_signal (
    run_id       text NOT NULL REFERENCES prv_alerts.backtest_run (run_id) ON DELETE CASCADE,
    code         text NOT NULL CHECK (code ~ '^[0-9A-Z]{6}$'),
    trade_date   date NOT NULL,
    eval_ts      timestamptz NOT NULL,
    mode         text NOT NULL CHECK (mode IN ('close', 'intraday_replay')),
    entry_price  double precision CHECK (entry_price > 0),
    rets         jsonb NOT NULL DEFAULT '{}'::jsonb,
    status       text NOT NULL CHECK (status IN ('sent', 'cooldown', 'suppressed', 'dry', 'skipped')),
    input_fp     text NOT NULL CHECK (input_fp ~ '^[0-9a-f]{16}$'),
    PRIMARY KEY (run_id, code, eval_ts)
);

-- diffs = [{code, eval_ts, side(live_only·backtest_only), reason(data_revised·logic)}]
CREATE TABLE IF NOT EXISTS prv_alerts.match_check (
    trade_date   date NOT NULL,
    rule_id      text NOT NULL REFERENCES prv_alerts.rule (rule_id),
    version      integer NOT NULL CHECK (version >= 1),
    live_n       integer NOT NULL CHECK (live_n >= 0),
    backtest_n   integer NOT NULL CHECK (backtest_n >= 0),
    diffs        jsonb NOT NULL DEFAULT '[]'::jsonb,
    logic_n      integer NOT NULL CHECK (logic_n >= 0),
    computed_at  timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    source       text NOT NULL CHECK (source <> ''),
    PRIMARY KEY (trade_date, rule_id)
);

-- ── 분석 일지(SD analysis_journal 칸 + 출처 — JNL) ─────────────────────────────────────────────
-- 밸류체인 칸 셋은 빈칸 허용(사전은 사용자 별도). eps·per·tp 는 {bear, base, bull}. value_sources = {칸: 출처}.
CREATE TABLE IF NOT EXISTS prv_journal.analysis (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code           text NOT NULL CHECK (code ~ '^[0-9A-Z]{6}$'),
    analyzed_on    date NOT NULL,
    chain_id_1     text,
    chain_id_2     text,
    chain_id_3     text,
    price          numeric(20, 2) CHECK (price >= 0),
    ret_52w        double precision,
    fwd_per        double precision,
    band_pos       double precision,
    ev_ebitda      double precision,
    mktcap         numeric(24, 0) CHECK (mktcap >= 0),
    opm            double precision,
    eps            jsonb NOT NULL DEFAULT '{}'::jsonb,
    per            jsonb NOT NULL DEFAULT '{}'::jsonb,
    tp             jsonb NOT NULL DEFAULT '{}'::jsonb,
    tp_source      text,
    conviction     text,
    memo           text,
    tags           text[] NOT NULL DEFAULT '{}',
    conclusion     text,
    value_sources  jsonb NOT NULL DEFAULT '{}'::jsonb,
    source         text NOT NULL CHECK (source IN ('api', 'cli', 'import:sd', 'import:csv')),
    updated_at     timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS analysis_code_idx ON prv_journal.analysis (code, analyzed_on DESC);

-- ── 매매 기록(SD trade_journal + signal_id — JNL). 포지션·손익은 읽을 때 계산(표 없음) ───────────
CREATE TABLE IF NOT EXISTS prv_journal.trade (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code           text NOT NULL CHECK (code ~ '^[0-9A-Z]{6}$'),
    name_at_trade  text,
    side           text NOT NULL CHECK (side IN ('buy', 'sell')),
    trade_date     date NOT NULL,
    price          numeric(20, 2) NOT NULL CHECK (price > 0),
    qty            bigint NOT NULL CHECK (qty > 0),
    fee            numeric(20, 2) NOT NULL CHECK (fee >= 0),
    tax            numeric(20, 2) NOT NULL CHECK (tax >= 0),
    strategy       text,
    memo           text,
    signal_id      text CHECK (signal_id ~ '^(si|sc)_[0-9A-HJKMNP-TV-Z]{26}$'),
    source         text NOT NULL CHECK (source IN ('api', 'cli', 'import:sd', 'import:csv')),
    created_at     timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS trade_code_date_idx ON prv_journal.trade (code, trade_date, id);

-- ── 추천 이력(SD recommendation_history 그대로 — 이관 전용) ────────────────────────────────────
-- source 는 추천 출처 이름(SD 칸 그대로). 새 추천 생성기는 없다(D-P8-19).
CREATE TABLE IF NOT EXISTS prv_journal.pick (
    date          date NOT NULL,
    source        text NOT NULL CHECK (source <> ''),
    rank          integer CHECK (rank >= 1),
    code          text NOT NULL CHECK (code ~ '^[0-9A-Z]{6}$'),
    name          text,
    market        text,
    score         double precision,
    price_at_rec  numeric(20, 2) CHECK (price_at_rec >= 0),
    tags          jsonb NOT NULL DEFAULT '{}'::jsonb,
    loaded_by     text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (date, source, code)
);
