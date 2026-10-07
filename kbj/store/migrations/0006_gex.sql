-- 0006_gex — GEXLAB 표 골격을 KBJ 스키마로 (docs/p2_design.md §8.1·§8.2, conflict_map §1.5)
--
-- - GEXLAB db/migrations/001~004 의 표를 **이름·열 그대로** 옮긴다: 시장 데이터·engine 산출 16표는
--   prv_gex(로그인 등급 — KIS·KRX), 운영 기록 collection_gaps·quarantine·collection_reports 는 ops.
--   session_log·health_events 는 0002 가 ops 에 만들었다(kbj 서비스도 같은 표에 쓴다).
-- - 004_flags 의 ALTER TABLE … ADD COLUMN flag 는 levels·oi_changes 정의에 합쳤다(기본값 그대로 —
--   levels 'visible', oi_changes 'shadow'). 열 순서도 GX 적용 뒤와 같다(flag 가 끝).
-- - P2 는 골격만이다 — 이 표에 쓰는 GX 서비스(poller·recorder·ws-gateway·engine·scheduler)는 P7 까지
--   legacy/gexlab 에서 GX 자기 DB 에 쓴다. P7 에 GX 코드를 search_path(prv_gex, ops)로 붙인다
--   (GX 의 표 이름은 스키마 없이 쓴다). 운영 DB 의 녹화 데이터 이관은 P7(pg_dump --data-only) [확인 필요].
-- - 시장 투자자 장중 수급은 GX investor_flow 하나다. KBJ 이름 prv_flows.market_investor_intraday 는
--   그 표를 읽는 뷰다(conflict_map §1.5 — GX 코드를 고치지 않고 이름만 맞춘다).
-- - 시각은 timestamptz(UTC), 전부 hypertable. 멱등: CREATE … IF NOT EXISTS,
--   create_hypertable·add_compression_policy(if_not_exists), CREATE OR REPLACE VIEW.
--   적용된 뒤엔 고치지 않는다(kbj/store/migrate.py 체크섬).
--
-- 아래 표 머리 주석은 GX 원문이다(설계 §·metrics § 는 GEXLAB 문서 번호).

-- ── 원본 녹화 (recorder) ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS prv_gex.raw_messages (
    ts            timestamptz NOT NULL,  -- 수신 시각 = RawEnvelope.received_at
    trade_date    date,                  -- 장 밖 수신(IDLE 등)은 NULL
    session       text CHECK (session IN ('day', 'night')),
    source        text NOT NULL CHECK (source IN ('kis_ws', 'kis_rest', 'krx')),
    tr_id         text NOT NULL,
    key           text NOT NULL DEFAULT '',  -- 종목·파라미터
    payload       jsonb,                 -- dict 원문 (REST·KRX 응답)
    payload_text  text,                  -- 문자열 원문 (웹소켓 프레임)
    lossy         boolean NOT NULL DEFAULT false,  -- NUL·NaN 처럼 jsonb/text 가 못 받는 값을 바꿔 넣었다
    digest        bytea NOT NULL,        -- sha256(source·tr_id·key·원문) — 재적재 중복 방지
    CHECK ((payload IS NULL) <> (payload_text IS NULL)),
    CHECK ((trade_date IS NULL) = (session IS NULL)),
    UNIQUE (ts, digest)
);
SELECT create_hypertable('prv_gex.raw_messages', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS raw_messages_trade_date_idx ON prv_gex.raw_messages (trade_date, session, ts);
CREATE INDEX IF NOT EXISTS raw_messages_tr_id_idx ON prv_gex.raw_messages (tr_id, ts DESC);
-- 3일 뒤 압축. digest 는 압축 청크에서도 ON CONFLICT 가 중복을 찾도록 orderby 에 둔다
ALTER TABLE prv_gex.raw_messages SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'source, tr_id',
    timescaledb.compress_orderby = 'ts, digest'
);
SELECT add_compression_policy('prv_gex.raw_messages', INTERVAL '3 days', if_not_exists => TRUE);
-- 30일 지난 청크는 Parquet 로 내보낸 뒤 지운다(GX PLAN §4.5·§11.4 — KBJ 에서는 P7 에 정한다).
-- 내보내기 확인 전에 지우면 원본을 잃으므로 retention 정책은 여기서 걸지 않는다.

-- ── 웹소켓 체결 (ws-gateway) ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS prv_gex.fut_ticks (
    ts            timestamptz NOT NULL,  -- 체결 시각 (KIS bsop_hour → UTC)
    trade_date    date NOT NULL,
    session       text NOT NULL CHECK (session IN ('day', 'night')),
    received_at   timestamptz NOT NULL,
    tr_id         text NOT NULL,         -- H0IFCNT0 · H0MFCNT0
    code          text NOT NULL,
    seq           bigint NOT NULL,       -- ws-gateway 수신 순번 (같은 초 여러 체결 구분)
    price         numeric NOT NULL,
    qty           bigint,
    cum_vol       bigint,
    cum_value     bigint,
    cum_buy_qty   bigint,                -- 누적 매수 체결수량 (HIRO-lite, PLAN §5.5)
    cum_sell_qty  bigint,                -- 누적 매도 체결수량
    oi            bigint,
    oi_chg        bigint,
    bid           numeric,
    ask           numeric,
    UNIQUE (ts, code, seq)
);
SELECT create_hypertable('prv_gex.fut_ticks', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS fut_ticks_trade_date_idx ON prv_gex.fut_ticks (trade_date, session, code, ts);

CREATE TABLE IF NOT EXISTS prv_gex.opt_ticks (
    ts            timestamptz NOT NULL,
    trade_date    date NOT NULL,
    session       text NOT NULL CHECK (session IN ('day', 'night')),
    received_at   timestamptz NOT NULL,
    tr_id         text NOT NULL,         -- H0IOCNT0 · H0EUCNT0
    code          text NOT NULL,
    seq           bigint NOT NULL,
    mrkt_cls      text,                  -- '' 월물 · WKM · WKI (위클리 두 종류는 6자리 만기가 겹칠 수 있다)
    expiry        text CHECK (expiry ~ '^[0-9]{6}$'),
    strike        numeric(8, 2),
    cp            text CHECK (cp IN ('C', 'P')),
    price         numeric NOT NULL,
    qty           bigint,
    cum_vol       bigint,
    cum_value     bigint,
    cum_buy_qty   bigint,
    cum_sell_qty  bigint,
    oi            bigint,
    oi_chg        bigint,
    bid           numeric,
    ask           numeric,
    delta         numeric,
    gamma         numeric,
    vega          numeric,
    theta         numeric,
    rho           numeric,
    iv            numeric,               -- % (KIS 0 은 값 없음 → NULL)
    UNIQUE (ts, code, seq)
);
SELECT create_hypertable('prv_gex.opt_ticks', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS opt_ticks_trade_date_idx ON prv_gex.opt_ticks (trade_date, session, ts);
CREATE INDEX IF NOT EXISTS opt_ticks_series_idx ON prv_gex.opt_ticks (expiry, strike, cp, ts);

-- ── REST 수집 (poller) — ts 는 응답 수신 시각 ───────────────────────────────

CREATE TABLE IF NOT EXISTS prv_gex.chain_snapshots (
    ts            timestamptz NOT NULL,
    trade_date    date NOT NULL,
    session       text NOT NULL CHECK (session IN ('day', 'night')),
    mrkt_cls      text NOT NULL,         -- '' 월물 · WKM · WKI (WKM·WKI 261001 처럼 만기값이 겹친다)
    expiry        text NOT NULL CHECK (expiry ~ '^[0-9]{6}$'),  -- 월물리스트 6자리
    strike        numeric(8, 2) NOT NULL,
    cp            text NOT NULL CHECK (cp IN ('C', 'P')),
    source        text NOT NULL CHECK (source IN ('board', 'fill')),  -- 전광판 · 단건 보강
    code          text,
    last          numeric,
    bid           numeric,               -- board 만 (단건엔 호가가 없다)
    ask           numeric,
    oi            bigint,
    oi_chg        bigint,
    volume        bigint,
    iv_kis        numeric,               -- %
    delta         numeric,
    gamma         numeric,
    theta         numeric,
    vega          numeric,
    rho           numeric,
    quality       text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    UNIQUE (ts, mrkt_cls, expiry, strike, cp, source)
);
SELECT create_hypertable('prv_gex.chain_snapshots', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS chain_snapshots_trade_date_idx ON prv_gex.chain_snapshots (trade_date, session, ts);
CREATE INDEX IF NOT EXISTS chain_snapshots_series_idx ON prv_gex.chain_snapshots (mrkt_cls, expiry, strike, cp, ts DESC);

CREATE TABLE IF NOT EXISTS prv_gex.fut_board (
    ts              timestamptz NOT NULL,
    trade_date      date NOT NULL,
    session         text NOT NULL CHECK (session IN ('day', 'night')),
    market          text NOT NULL,       -- F · CM
    code            text NOT NULL,
    source          text NOT NULL CHECK (source IN ('board', 'single')),  -- 전광판 · 단건 현재가
    name            text,
    price           numeric,
    bid             numeric,
    ask             numeric,
    volume          bigint,
    oi              bigint,
    remaining_days  integer,
    quality         text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    UNIQUE (ts, market, code, source)
);
SELECT create_hypertable('prv_gex.fut_board', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS fut_board_trade_date_idx ON prv_gex.fut_board (trade_date, session, code, ts);

-- 긴 형식: 7조합 × 투자자 12종 = 84행/분. 수량 계약, 대금 백만원
CREATE TABLE IF NOT EXISTS prv_gex.investor_flow (
    ts            timestamptz NOT NULL,
    trade_date    date NOT NULL,
    session       text NOT NULL CHECK (session IN ('day', 'night')),
    market_code   text NOT NULL,         -- K2I · WKM · WKI
    sector_code   text NOT NULL,         -- F001 · OC01 · OP01 · OC05 · OP05 · OC04 · OP04
    investor      text NOT NULL,
    sell_qty      bigint,
    buy_qty       bigint,
    net_qty       bigint,
    sell_value    bigint,
    buy_value     bigint,
    net_value     bigint,
    quality       text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    UNIQUE (ts, market_code, sector_code, investor)
);
SELECT create_hypertable('prv_gex.investor_flow', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS investor_flow_trade_date_idx ON prv_gex.investor_flow (trade_date, session, market_code, sector_code, ts);

-- 시리즈 최종거래일 (KIS 단건 futs_last_tr_date, 없으면 캘린더 계산값 — PLAN §2.3·§6.2)
CREATE TABLE IF NOT EXISTS prv_gex.series_expiries (
    ts               timestamptz NOT NULL,
    trade_date       date NOT NULL,
    session          text NOT NULL CHECK (session IN ('day', 'night')),
    mrkt_cls         text NOT NULL,
    expiry           text NOT NULL CHECK (expiry ~ '^[0-9]{6}$'),
    source           text NOT NULL CHECK (source IN ('kis', 'calendar')),
    last_trade_date  date NOT NULL,
    calendar_date    date,               -- 교차검증용 계산값
    matches          boolean,
    code             text,
    quality          text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    UNIQUE (ts, mrkt_cls, expiry, source)
);
SELECT create_hypertable('prv_gex.series_expiries', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS series_expiries_series_idx ON prv_gex.series_expiries (mrkt_cls, expiry, ts DESC);

-- ── 일별 적재 (scheduler) ───────────────────────────────────────────────────

-- KIS 분봉. ts = 봉 시각(UTC). 야간 CM 봉의 24~30시 표기는 core.calendar.night_bar_time 으로 푼다
CREATE TABLE IF NOT EXISTS prv_gex.minute_bars (
    ts            timestamptz NOT NULL,
    trade_date    date NOT NULL,
    session       text NOT NULL CHECK (session IN ('day', 'night')),
    received_at   timestamptz NOT NULL,
    code          text NOT NULL,
    market        text NOT NULL CHECK (market IN ('F', 'CM')),  -- 주간 F · 야간 CM
    open          numeric NOT NULL,
    high          numeric NOT NULL,
    low           numeric NOT NULL,
    close         numeric NOT NULL,
    volume        bigint,                -- 봉 체결량
    cum_value     bigint,                -- 누적 거래대금(천원, KIS acml_tr_pbmn)
    CHECK ((market = 'F') = (session = 'day')),
    UNIQUE (code, market, ts)
);
SELECT create_hypertable('prv_gex.minute_bars', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS minute_bars_trade_date_idx ON prv_gex.minute_bars (trade_date, session, code, ts);

-- KRX 일별 — 시각이 없는 일 단위 자료라 파티션은 trade_date(= BAS_DD). ts 는 조회(수신) 시각.
-- 하루 한 번 적재하는 일 단위 자료라 청크는 1년(2026-09-29 사용자 결정 — 1일이면 거래일마다 청크)
-- 세션: 선물은 MKT_NM, 옵션은 ISU_NM 끝 표기. 코스피200 계열만 적재한다(설계 §8)
CREATE TABLE IF NOT EXISTS prv_gex.krx_fut_daily (
    trade_date      date NOT NULL,       -- BAS_DD
    session         text NOT NULL CHECK (session IN ('day', 'night')),
    isu_cd          text NOT NULL,
    ts              timestamptz NOT NULL,
    isu_nm          text NOT NULL,
    prod_nm         text NOT NULL,
    family          text NOT NULL,
    expiry          text CHECK (expiry ~ '^[0-9]{6}$'),
    open            numeric,
    high            numeric,
    low             numeric,
    close           numeric,
    cmp_prev        numeric,
    setl_prc        numeric,             -- 정산가 (야간 행은 빈 값) — IV−HV 입력(PLAN §5.4)
    spot_prc        numeric,
    acc_trdvol      bigint,
    acc_trdval      bigint,              -- 원
    acc_opnint_qty  bigint,
    UNIQUE (trade_date, isu_cd, session)
);
SELECT create_hypertable('prv_gex.krx_fut_daily', by_range('trade_date', INTERVAL '365 days'), if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS prv_gex.krx_opt_daily (
    trade_date      date NOT NULL,       -- BAS_DD
    session         text NOT NULL CHECK (session IN ('day', 'night')),
    isu_cd          text NOT NULL,
    ts              timestamptz NOT NULL,
    isu_nm          text NOT NULL,
    prod_nm         text NOT NULL,
    family          text NOT NULL,
    expiry          text NOT NULL CHECK (expiry ~ '^[0-9]{6}$'),  -- KIS 6자리 (위클리 2609W4 → 260904)
    expiry_token    text NOT NULL,       -- 이름 표기 ('202610', '2609W4')
    strike          numeric(8, 2) NOT NULL,
    cp              text NOT NULL CHECK (cp IN ('C', 'P')),
    open            numeric,
    high            numeric,
    low             numeric,
    close           numeric,
    cmp_prev        numeric,
    imp_volt        numeric,             -- %. 야간 행 '0.00' 은 값 없음 표시 → NULL (#15)
    nxtdd_bas_prc   numeric,
    acc_trdvol      bigint,
    acc_trdval      bigint,
    acc_opnint_qty  bigint,
    UNIQUE (trade_date, isu_cd, session)
);
SELECT create_hypertable('prv_gex.krx_opt_daily', by_range('trade_date', INTERVAL '365 days'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS krx_opt_daily_series_idx ON prv_gex.krx_opt_daily (family, expiry, strike, cp, trade_date);

-- KIS 지수선물옵션 마스터 (PRE_DAY·PRE_NIGHT). 날짜·세션별 상장 행사가 이력 — 백테스트용.
-- ts = 내려받은 시각. atm_cls 는 다섯째 필드(1 ATM · 2 ITM · 3 OTM, 마스터 작성 시점). 청크 1년(KRX 일별과 같다)
CREATE TABLE IF NOT EXISTS prv_gex.master_snapshots (
    trade_date    date NOT NULL,
    session       text NOT NULL CHECK (session IN ('day', 'night')),
    code          text NOT NULL,
    ts            timestamptz NOT NULL,
    kind          text NOT NULL,
    isin          text NOT NULL,
    name          text NOT NULL,
    cp            text CHECK (cp IN ('C', 'P')),  -- 옵션만
    strike        numeric(8, 2),                  -- 옵션만
    atm_cls       text CHECK (atm_cls IN ('1', '2', '3')),
    family        text NOT NULL,
    series        text NOT NULL,
    expiry_token  text,
    expiry        text CHECK (expiry ~ '^[0-9]{6}$'),
    underlying    text NOT NULL,
    UNIQUE (trade_date, session, code)
);
SELECT create_hypertable('prv_gex.master_snapshots', by_range('trade_date', INTERVAL '365 days'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS master_snapshots_series_idx ON prv_gex.master_snapshots (family, expiry, cp, strike, trade_date);

-- ── 운영 기록 ───────────────────────────────────────────────────────────────

-- 무결측 판정 공백 (GX 설계 §9). 같은 공백을 다시 판정하면 끝·기대·실수신을 고친다
CREATE TABLE IF NOT EXISTS ops.collection_gaps (
    start_ts      timestamptz NOT NULL,
    stream        text NOT NULL,
    end_ts        timestamptz NOT NULL,
    trade_date    date NOT NULL,
    session       text NOT NULL CHECK (session IN ('day', 'night')),
    expected      integer,
    received      integer,
    detected_at   timestamptz NOT NULL,
    detail        jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (end_ts >= start_ts),
    UNIQUE (stream, start_ts)
);
SELECT create_hypertable('ops.collection_gaps', by_range('start_ts', INTERVAL '1 day'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS collection_gaps_trade_date_idx ON ops.collection_gaps (trade_date, session, stream);

-- 검증 실패 레코드 (PLAN §6.1). 해당 산출은 quality=invalid
CREATE TABLE IF NOT EXISTS ops.quarantine (
    ts            timestamptz NOT NULL,
    trade_date    date,
    session       text CHECK (session IN ('day', 'night')),
    source        text NOT NULL CHECK (source IN ('kis_ws', 'kis_rest', 'krx')),
    tr_id         text NOT NULL,
    key           text NOT NULL DEFAULT '',
    payload       jsonb NOT NULL,
    lossy         boolean NOT NULL DEFAULT false,
    error         text NOT NULL,
    digest        bytea NOT NULL,
    CHECK ((trade_date IS NULL) = (session IS NULL)),
    UNIQUE (ts, digest)
);
SELECT create_hypertable('ops.quarantine', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS quarantine_trade_date_idx ON ops.quarantine (trade_date, session, tr_id, ts);

-- 무결측 판정 리포트 (GX 002_collection_reports). 세션마다 스트림별 한 행 — 공백 자체는 collection_gaps.
-- 같은 세션을 다시 판정하면 그 세션의 gaps·reports 를 트랜잭션 하나에서 지우고 새로 쓴다(GX data/store.py)
CREATE TABLE IF NOT EXISTS ops.collection_reports (
    trade_date    date NOT NULL,         -- 귀속 거래일 (야간은 T+1)
    session       text NOT NULL CHECK (session IN ('day', 'night')),
    stream        text NOT NULL,         -- board:<시리즈> · fut_board · investor:<시장>/<업종> · fill1:<시리즈> · ws_connection · fut_trades
    evaluated_at  timestamptz NOT NULL,
    span_start    timestamptz NOT NULL,  -- 이 스트림을 기대한 구간
    span_end      timestamptz NOT NULL,
    required      boolean NOT NULL,      -- 무결측 판정에 드는 필수 스트림인가
    status        text NOT NULL CHECK (status IN ('ok', 'gaps', 'unverified')),
    expected      integer,               -- 주기 칸 수 · 대조한 봉 수 · 초 (스트림마다 detail.unit)
    received      integer,
    gaps          integer NOT NULL DEFAULT 0,
    max_gap_s     double precision,
    detail        jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (span_end >= span_start),
    UNIQUE (trade_date, session, stream)
);
SELECT create_hypertable('ops.collection_reports', by_range('trade_date', INTERVAL '1 day'), if_not_exists => TRUE);

-- ── engine 산출 (GX 003_engine + 004_flags — flag 열을 표 정의에 합쳤다) ─────────────────────

-- 범위(all·nearest·0dte)별 핵심 레벨 (GX metrics §3). 값이 없으면 NULL
CREATE TABLE IF NOT EXISTS prv_gex.levels (
    ts          timestamptz NOT NULL,
    trade_date  date NOT NULL,
    session     text NOT NULL CHECK (session IN ('day', 'night')),
    scope       text NOT NULL CHECK (scope IN ('all', 'nearest', '0dte')),
    name        text NOT NULL,
    value       numeric,
    detail      jsonb NOT NULL DEFAULT '{}'::jsonb,
    quality     text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    reasons     jsonb NOT NULL DEFAULT '[]'::jsonb,  -- 품질을 떨어뜨린 사유 목록
    flag        text NOT NULL DEFAULT 'visible' CHECK (flag IN ('off', 'shadow', 'visible')),  -- GX 004
    UNIQUE (ts, scope, name)
);
SELECT create_hypertable('prv_gex.levels', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS levels_trade_date_idx ON prv_gex.levels (trade_date, session, ts);
CREATE INDEX IF NOT EXISTS levels_name_idx ON prv_gex.levels (scope, name, ts DESC);

-- 지표 값 (metrics §2·§3·§4~§7). scope: all·nearest·0dte(범위 합산), series(만기 하나 — key 가
-- 시리즈 라벨 'WKM:261001'). flag: 계산 당시 기능 플래그(설계 §4 — shadow 는 저장만, 발행 안 함)
CREATE TABLE IF NOT EXISTS prv_gex.metrics (
    ts          timestamptz NOT NULL,
    trade_date  date NOT NULL,
    session     text NOT NULL CHECK (session IN ('day', 'night')),
    metric      text NOT NULL,
    scope       text NOT NULL CHECK (scope IN ('all', 'nearest', '0dte', 'series')),
    key         text NOT NULL DEFAULT '',
    value       numeric,
    payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
    quality     text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    flag        text NOT NULL CHECK (flag IN ('off', 'shadow', 'visible')),
    UNIQUE (ts, metric, scope, key)
);
SELECT create_hypertable('prv_gex.metrics', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS metrics_trade_date_idx ON prv_gex.metrics (trade_date, session, ts);
CREATE INDEX IF NOT EXISTS metrics_metric_idx ON prv_gex.metrics (metric, scope, key, ts DESC);

-- 행사가별 GEX (metrics §2.1, 원/1% — 대시보드 막대). 만기 하나·행사가 하나. 제외 종목은 0.
-- quality·excluded_oi_ratio 는 그 만기 표의 것, forward 는 그 만기 F(pt)
CREATE TABLE IF NOT EXISTS prv_gex.strike_gex (
    ts                 timestamptz NOT NULL,
    trade_date         date NOT NULL,
    session            text NOT NULL CHECK (session IN ('day', 'night')),
    mrkt_cls           text NOT NULL,       -- '' 월물 · WKM · WKI (WKM·WKI 261001 처럼 만기값이 겹친다)
    expiry             text NOT NULL CHECK (expiry ~ '^[0-9]{6}$'),
    strike             numeric(8, 2) NOT NULL,
    gex_call           numeric NOT NULL,
    gex_put            numeric NOT NULL,
    gex                numeric NOT NULL,
    forward            numeric,
    excluded_oi_ratio  numeric NOT NULL CHECK (excluded_oi_ratio >= 0 AND excluded_oi_ratio <= 1),
    quality            text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    UNIQUE (ts, mrkt_cls, expiry, strike)
);
SELECT create_hypertable('prv_gex.strike_gex', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS strike_gex_trade_date_idx ON prv_gex.strike_gex (trade_date, session, ts);

-- 종목별 자체 IV (metrics §1.1~§1.6 — 스마일·HIRO 입력). 검증 수정 3: 옵션별 IV 를 처음 저장하는
-- 곳이 source(self 자체 역산 · kis 폴백)·rescaled·t_kis 를 함께 둔다(KIS 폴백 σ 는 자체 T 로 옮긴 값).
-- delta·gamma 는 자체 그릭스(GEX 에 든 종목만) — KIS 그릭스가 아니다
CREATE TABLE IF NOT EXISTS prv_gex.option_iv (
    ts            timestamptz NOT NULL,
    trade_date    date NOT NULL,
    session       text NOT NULL CHECK (session IN ('day', 'night')),
    mrkt_cls      text NOT NULL,
    expiry        text NOT NULL CHECK (expiry ~ '^[0-9]{6}$'),
    strike        numeric(8, 2) NOT NULL,
    cp            text NOT NULL CHECK (cp IN ('C', 'P')),
    quote_source  text NOT NULL CHECK (quote_source IN ('board', 'fill')),  -- 쓴 체인 행
    price         numeric,               -- §1.1 고른 가격(pt)
    price_kind    text CHECK (price_kind IN ('mid', 'last')),
    prev_session  boolean NOT NULL DEFAULT false,  -- last 인데 당일 거래량 0 (전 세션 가격)
    oi            bigint NOT NULL,
    iv            numeric,               -- 쓴 σ(연율 소수). 없으면 NULL
    source        text CHECK (source IN ('self', 'kis')),
    rescaled      boolean NOT NULL DEFAULT false,  -- KIS σ 를 T 환산하며 값이 바뀌었나
    t_kis         numeric,               -- 옮길 때 쓴 T_KIS(년, metrics §1.7)
    reason        text,                  -- core.iv.IvResult.reason (자체 역산이면 NULL)
    excluded      text CHECK (excluded IN ('no_forward', 'no_price', 'below_min_premium', 'iv_invalid')),
    delta         numeric,
    gamma         numeric,
    forward       numeric,               -- 그 만기 F(pt)
    t_years       numeric NOT NULL,      -- 자체 T(년, metrics §1.4)
    quality       text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    CHECK ((iv IS NULL) = (source IS NULL)),
    CHECK (NOT rescaled OR t_kis IS NOT NULL),
    UNIQUE (ts, mrkt_cls, expiry, strike, cp)
);
SELECT create_hypertable('prv_gex.option_iv', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS option_iv_trade_date_idx ON prv_gex.option_iv (trade_date, session, ts);
CREATE INDEX IF NOT EXISTS option_iv_series_idx ON prv_gex.option_iv (mrkt_cls, expiry, strike, cp, ts DESC);

-- 스냅샷 사이 OI 증감 (metrics §6.7 — 히트맵). outlier: 줄었다가 다음 스냅샷에 90% 이상 복구된
-- 두 칸(이상치 격리 — 표시 제외, health)
CREATE TABLE IF NOT EXISTS prv_gex.oi_changes (
    ts          timestamptz NOT NULL,
    trade_date  date NOT NULL,
    session     text NOT NULL CHECK (session IN ('day', 'night')),
    mrkt_cls    text NOT NULL,
    expiry      text NOT NULL CHECK (expiry ~ '^[0-9]{6}$'),
    strike      numeric(8, 2) NOT NULL,
    cp          text NOT NULL CHECK (cp IN ('C', 'P')),
    oi          bigint NOT NULL,
    prev_ts     timestamptz,
    prev_oi     bigint,
    change      bigint,
    outlier     boolean NOT NULL DEFAULT false,
    quality     text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    flag        text NOT NULL DEFAULT 'shadow' CHECK (flag IN ('off', 'shadow', 'visible')),  -- GX 004
    UNIQUE (ts, mrkt_cls, expiry, strike, cp)
);
SELECT create_hypertable('prv_gex.oi_changes', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS oi_changes_trade_date_idx ON prv_gex.oi_changes (trade_date, session, ts);

-- ── KBJ 이름: 시장 투자자 장중 수급 = GX investor_flow (conflict_map §1.5) ──────────────────────
-- 수량 계약, 대금 백만원(GX 단위 그대로 — prv_flows.market_investor_daily 의 원 단위와 다르다).
CREATE OR REPLACE VIEW prv_flows.market_investor_intraday AS
SELECT
    ts,
    trade_date,
    session,
    market_code,
    sector_code,
    investor,
    sell_qty,
    buy_qty,
    net_qty,
    sell_value,
    buy_value,
    net_value,
    quality,
    'KIS'::text AS source
FROM prv_gex.investor_flow;
