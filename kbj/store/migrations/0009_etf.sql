-- 0009_etf — ETF 일별·장중·메타·분할·운용사 구성종목·변동 (docs/p3_design.md §3.4·§4.3, D-P3-12·13)
--
-- P2 설계 §8.2 의 0013 계획을 P3 로 당겼다(사용자 요청 2026-10-07 — 페이지 10 ETF 수급). ET 의
-- etf_ticker·etf_meta·etf_aum 은 meta·etf_daily 로 흡수, etf_live 는 폐기(P2 §8.4).
--
-- 규칙(0003 과 같다)
-- - 로그인 등급(KRX·KIS 시세, 운용사 PDF — 약관이 달라 로그인)이라 prv_etf 에만 둔다(ADR 0002).
-- - 값 행은 source·quality·received_at·loaded_by. 금액(순자산·거래대금·시총)은 원 단위 정수, 좌수 bigint.
-- - 순유입 = Σ(Sₜ − Sₜ₋₁)×NAVₜ 는 저장하지 않고 원장(etf_daily)에서 계산한다(metrics §4 — 일별 원장 하나).
-- - 멱등: CREATE … IF NOT EXISTS, create_hypertable·add_retention_policy(if_not_exists).

-- ── ETF 일별 (KRX:etp/etf_bydd_trd — 마감 NAV·상장좌수·보고 순자산) ─────────────────────────
-- nav = 마감 NAV(확정 — 순유입 계산은 이것만), list_shrs = 상장좌수, net_asset = 보고 순자산
-- (INVSTASST_NETASST_TOTAMT — 공표 단위 [실측 필요], 원으로 바꿔 넣는다). base_index = 기초지수 이름.
CREATE TABLE IF NOT EXISTS prv_etf.etf_daily (
    code         text NOT NULL CHECK (code <> '' AND code !~ '\s'),
    trade_date   date NOT NULL,
    source       text NOT NULL CHECK (source <> ''),
    venue        text NOT NULL DEFAULT '' CHECK (venue IN ('', 'KRX', 'NXT', 'TOTAL')),
    name         text,
    close        numeric,
    nav          numeric,
    list_shrs    bigint CHECK (list_shrs IS NULL OR list_shrs >= 0),
    net_asset    bigint CHECK (net_asset IS NULL OR net_asset >= 0),
    turnover     bigint CHECK (turnover IS NULL OR turnover >= 0),
    volume       bigint CHECK (volume IS NULL OR volume >= 0),
    mktcap       bigint CHECK (mktcap IS NULL OR mktcap >= 0),
    base_index   text,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (code, trade_date, source, venue)
);
SELECT create_hypertable(
    'prv_etf.etf_daily', by_range('trade_date', INTERVAL '365 days'), if_not_exists => TRUE
);
CREATE INDEX IF NOT EXISTS etf_daily_date_idx ON prv_etf.etf_daily (trade_date);

-- ── 장중 ETF 현재가·iNAV (KIS:etf_quote_intraday — FHPST02400000, 괴리율 경고에만) ──────────────
-- 대상 = 순자산 상위 config/markets.yaml etf.watch_top_n. quality=estimated. 보존 30일 [제안].
CREATE TABLE IF NOT EXISTS prv_etf.quote_intraday (
    code         text NOT NULL CHECK (code <> '' AND code !~ '\s'),
    ts           timestamptz NOT NULL,
    source       text NOT NULL CHECK (source <> ''),
    price        numeric,
    inav         numeric,
    premium_pct  numeric,
    turnover     bigint CHECK (turnover IS NULL OR turnover >= 0),
    volume       bigint CHECK (volume IS NULL OR volume >= 0),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (code, ts, source)
);
SELECT create_hypertable('prv_etf.quote_intraday', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE);
SELECT add_retention_policy('prv_etf.quote_intraday', INTERVAL '30 days', if_not_exists => TRUE);

-- ── ETF 메타 (KRX 기본정보 + 이름 규칙 분류) ────────────────────────────────────────────────
-- theme = ET etf_tracker_v9/themes.classify(ADR 0001 Q8), etf_type = metrics §8 유형(kbj.core.rows
-- EtfType 값). leverage = 배수(2·−1·−2 …, 모르면 NULL). delisted_on = 상장폐지일(그 전날까지 계산).
CREATE TABLE IF NOT EXISTS prv_etf.meta (
    code         text PRIMARY KEY CHECK (code <> '' AND code !~ '\s'),
    name         text,
    issuer       text,
    brand        text,
    theme        text,
    etf_type     text CHECK (etf_type IN ('kr_index', 'kr_theme', 'overseas', 'leveraged_inverse',
                                          'bond_cash', 'commodity', 'other')),
    leverage     numeric,
    base_index   text,
    listed_on    date,
    delisted_on  date,
    source       text NOT NULL CHECK (source <> ''),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    updated_at   timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> '')
);

-- ── 분할·병합 이벤트 (metrics §4 오류 1번) ──────────────────────────────────────────────────
-- ratio = 새 좌수 ÷ 옛 좌수(1:10 분할 10, 5:1 병합 0.2). origin: detected(좌수·NAV 동시 계단 — estimated)
-- · manual(수기 — 우선). 저장소는 detected 로 manual 을 덮지 않는다.
CREATE TABLE IF NOT EXISTS prv_etf.split_event (
    code            text NOT NULL CHECK (code <> '' AND code !~ '\s'),
    effective_date  date NOT NULL,
    ratio           numeric NOT NULL CHECK (ratio > 0),
    origin          text NOT NULL CHECK (origin IN ('detected', 'manual')),
    note            text NOT NULL DEFAULT '',
    source          text NOT NULL CHECK (source <> ''),
    quality         text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    updated_at      timestamptz NOT NULL,
    loaded_by       text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (code, effective_date)
);

-- ── 운용사 PDF 추적 펀드 (ET etf_tracker_v9/tracker.py DDL:71 fund 열 — mktcap 은 네이버 출처라 뺐다) ──
-- depth: full(전 종목) · top10(상위 10만 — 변동 종류가 IN10·OUT10). empty_streak = 국내 종목 0개 연속 수.
CREATE TABLE IF NOT EXISTS prv_etf.fund (
    fund_id       text PRIMARY KEY CHECK (fund_id <> '' AND fund_id !~ '\s'),
    issuer        text NOT NULL CHECK (issuer <> ''),
    fund_key      text NOT NULL CHECK (fund_key <> ''),
    ticker        text,
    name          text,
    theme         text,
    is_active     boolean NOT NULL DEFAULT false,
    depth         text NOT NULL DEFAULT 'full' CHECK (depth IN ('full', 'top10')),
    track         boolean NOT NULL DEFAULT true,
    empty_streak  integer NOT NULL DEFAULT 0 CHECK (empty_streak >= 0),
    source        text NOT NULL CHECK (source <> ''),
    updated_at    timestamptz NOT NULL,
    loaded_by     text NOT NULL CHECK (loaded_by <> '')
);

-- ── 구성종목 스냅 (ET DDL holding 열 + source·quality·received_at·loaded_by) ─────────────────
-- asof = 운용사가 밝힌 기준일. val = 평가금액(원), wt = 비중(%), qty = 수량(주 — 소수 가능).
CREATE TABLE IF NOT EXISTS prv_etf.holding (
    fund_id      text NOT NULL CHECK (fund_id <> '' AND fund_id !~ '\s'),
    asof         date NOT NULL,
    code         text NOT NULL CHECK (code <> '' AND code !~ '\s'),
    name         text,
    qty          numeric,
    wt           numeric,
    val          numeric,
    source       text NOT NULL CHECK (source <> ''),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (fund_id, asof, code)
);
SELECT create_hypertable('prv_etf.holding', by_range('asof', INTERVAL '365 days'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS holding_fund_idx ON prv_etf.holding (fund_id, asof DESC);

-- ── 구성종목 변동 (ET DDL:79 change_log 열 + engine_version — kbj.engines.etf.holdings.analyze) ──
-- kind: NEW(신규)·DROP(제외)·IN10·OUT10(TOP10 진입·이탈)·ADD·CUT(CU 보정 뒤 ±action_pp).
CREATE TABLE IF NOT EXISTS prv_etf.change_log (
    run_date        date NOT NULL,
    fund_id         text NOT NULL CHECK (fund_id <> '' AND fund_id !~ '\s'),
    code            text NOT NULL CHECK (code <> '' AND code !~ '\s'),
    kind            text NOT NULL CHECK (kind IN ('NEW', 'DROP', 'IN10', 'OUT10', 'ADD', 'CUT')),
    name            text,
    asof            date NOT NULL,
    prev_asof       date NOT NULL,
    gap_days        integer NOT NULL CHECK (gap_days >= 0),
    prev_qty        numeric,
    cur_qty         numeric,
    prev_wt         numeric,
    cur_wt          numeric,
    qty_pct         numeric,
    qty_pct_adj     numeric,
    engine_version  text NOT NULL CHECK (engine_version <> ''),
    computed_at     timestamptz NOT NULL,
    PRIMARY KEY (run_date, fund_id, code, kind)
);
CREATE INDEX IF NOT EXISTS change_log_run_idx ON prv_etf.change_log (run_date);
