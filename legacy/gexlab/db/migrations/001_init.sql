-- 001_init — Phase 1 저장 스키마 (PLAN §4.5, docs/phase1_design.md §8)
--
-- 공통 규칙
-- - 시각은 전부 timestamptz(UTC 로 넣는다). naive 시각 금지
-- - 시장 데이터 행은 trade_date(귀속 거래일)·session('day'|'night')을 따로 둔다. 야간분은 T+1 귀속
-- - 가격·그릭스·IV 는 numeric, 행사가는 numeric(8,2), 수량·대금은 bigint
-- - 전부 TimescaleDB hypertable(청크 1일). 유니크 키에는 파티션 열이 들어간다
-- - 유니크 키는 재적재(같은 묶음을 다시 씀) 멱등용이다: 시세 스냅샷·이벤트는 ON CONFLICT DO NOTHING,
--   나중에 값이 고쳐질 수 있는 적재(분봉·KRX 일별·마스터·공백 판정)는 DO UPDATE (data/store.py)
-- - 행 → 열 매핑은 data/store.py 의 Table 상수가 이 파일과 같다는 것을 단위 테스트가 고정한다
--
-- 이 파일은 db/migrate.py 가 트랜잭션 하나로 적용하고 schema_migrations 에 기록한다. 적용된 뒤엔
-- 고치지 않는다(체크섬 검사) — 바꿀 일은 002_… 로 새로 쓴다.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ── 원본 녹화 (recorder) ─────────────────────────────────────────────────────

CREATE TABLE raw_messages (
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
SELECT create_hypertable('raw_messages', by_range('ts', INTERVAL '1 day'));
CREATE INDEX raw_messages_trade_date_idx ON raw_messages (trade_date, session, ts);
CREATE INDEX raw_messages_tr_id_idx ON raw_messages (tr_id, ts DESC);
-- 3일 뒤 압축. digest 는 압축 청크에서도 ON CONFLICT 가 중복을 찾도록 orderby 에 둔다
ALTER TABLE raw_messages SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'source, tr_id',
    timescaledb.compress_orderby = 'ts, digest'
);
SELECT add_compression_policy('raw_messages', INTERVAL '3 days');
-- TODO(Phase 1 scheduler): 30일 지난 청크는 Parquet 로 내보낸 뒤 지운다(PLAN §4.5·§11.4).
--   내보내기 확인 전에 지우면 원본을 잃으므로 retention 정책은 여기서 걸지 않는다.

-- ── 웹소켓 체결 (ws-gateway) ────────────────────────────────────────────────

CREATE TABLE fut_ticks (
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
SELECT create_hypertable('fut_ticks', by_range('ts', INTERVAL '1 day'));
CREATE INDEX fut_ticks_trade_date_idx ON fut_ticks (trade_date, session, code, ts);

CREATE TABLE opt_ticks (
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
SELECT create_hypertable('opt_ticks', by_range('ts', INTERVAL '1 day'));
CREATE INDEX opt_ticks_trade_date_idx ON opt_ticks (trade_date, session, ts);
CREATE INDEX opt_ticks_series_idx ON opt_ticks (expiry, strike, cp, ts);

-- ── REST 수집 (poller) — ts 는 응답 수신 시각 ───────────────────────────────

CREATE TABLE chain_snapshots (
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
SELECT create_hypertable('chain_snapshots', by_range('ts', INTERVAL '1 day'));
CREATE INDEX chain_snapshots_trade_date_idx ON chain_snapshots (trade_date, session, ts);
CREATE INDEX chain_snapshots_series_idx ON chain_snapshots (mrkt_cls, expiry, strike, cp, ts DESC);

CREATE TABLE fut_board (
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
SELECT create_hypertable('fut_board', by_range('ts', INTERVAL '1 day'));
CREATE INDEX fut_board_trade_date_idx ON fut_board (trade_date, session, code, ts);

-- 긴 형식: 7조합 × 투자자 12종 = 84행/분. 수량 계약, 대금 백만원
CREATE TABLE investor_flow (
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
SELECT create_hypertable('investor_flow', by_range('ts', INTERVAL '1 day'));
CREATE INDEX investor_flow_trade_date_idx ON investor_flow (trade_date, session, market_code, sector_code, ts);

-- 시리즈 최종거래일 (KIS 단건 futs_last_tr_date, 없으면 캘린더 계산값 — PLAN §2.3·§6.2)
CREATE TABLE series_expiries (
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
SELECT create_hypertable('series_expiries', by_range('ts', INTERVAL '1 day'));
CREATE INDEX series_expiries_series_idx ON series_expiries (mrkt_cls, expiry, ts DESC);

-- ── 일별 적재 (scheduler) ───────────────────────────────────────────────────

-- KIS 분봉. ts = 봉 시각(UTC). 야간 CM 봉의 24~30시 표기는 core.calendar.night_bar_time 으로 푼다
CREATE TABLE minute_bars (
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
SELECT create_hypertable('minute_bars', by_range('ts', INTERVAL '1 day'));
CREATE INDEX minute_bars_trade_date_idx ON minute_bars (trade_date, session, code, ts);

-- KRX 일별 — 시각이 없는 일 단위 자료라 파티션은 trade_date(= BAS_DD). ts 는 조회(수신) 시각.
-- 하루 한 번 적재하는 일 단위 자료라 청크는 1년(2026-09-29 사용자 결정 — 1일이면 거래일마다 청크)
-- 세션: 선물은 MKT_NM, 옵션은 ISU_NM 끝 표기. 코스피200 계열만 적재한다(설계 §8)
CREATE TABLE krx_fut_daily (
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
SELECT create_hypertable('krx_fut_daily', by_range('trade_date', INTERVAL '365 days'));

CREATE TABLE krx_opt_daily (
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
SELECT create_hypertable('krx_opt_daily', by_range('trade_date', INTERVAL '365 days'));
CREATE INDEX krx_opt_daily_series_idx ON krx_opt_daily (family, expiry, strike, cp, trade_date);

-- KIS 지수선물옵션 마스터 (PRE_DAY·PRE_NIGHT). 날짜·세션별 상장 행사가 이력 — 백테스트용.
-- ts = 내려받은 시각. atm_cls 는 다섯째 필드(1 ATM · 2 ITM · 3 OTM, 마스터 작성 시점). 청크 1년(KRX 일별과 같다)
CREATE TABLE master_snapshots (
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
SELECT create_hypertable('master_snapshots', by_range('trade_date', INTERVAL '365 days'));
CREATE INDEX master_snapshots_series_idx ON master_snapshots (family, expiry, cp, strike, trade_date);

-- ── 운영 기록 ───────────────────────────────────────────────────────────────

-- 세션 상태 전이·캘린더/데이터 불일치('예상 밖 개장' 등) (설계 §6)
CREATE TABLE session_log (
    ts            timestamptz NOT NULL,
    trade_date    date,                  -- DAY·NIGHT 밖 상태는 NULL
    session       text CHECK (session IN ('day', 'night')),
    service       text NOT NULL,
    kind          text NOT NULL,         -- transition · calendar_mismatch · unexpected_open …
    state         text CHECK (state IN ('PRE_DAY', 'DAY', 'POST_DAY', 'PRE_NIGHT', 'NIGHT', 'IDLE')),
    prev_state    text CHECK (prev_state IN ('PRE_DAY', 'DAY', 'POST_DAY', 'PRE_NIGHT', 'NIGHT', 'IDLE')),
    detail        jsonb NOT NULL DEFAULT '{}'::jsonb,
    digest        bytea NOT NULL,
    CHECK ((trade_date IS NULL) = (session IS NULL)),
    UNIQUE (ts, digest)
);
SELECT create_hypertable('session_log', by_range('ts', INTERVAL '1 day'));
CREATE INDEX session_log_trade_date_idx ON session_log (trade_date, session, ts);

CREATE TABLE health_events (
    ts            timestamptz NOT NULL,
    trade_date    date,
    session       text CHECK (session IN ('day', 'night')),
    service       text NOT NULL,
    kind          text NOT NULL,
    level         text NOT NULL CHECK (level IN ('info', 'warning', 'error', 'critical')),
    message       text NOT NULL,
    detail        jsonb NOT NULL DEFAULT '{}'::jsonb,
    digest        bytea NOT NULL,
    CHECK ((trade_date IS NULL) = (session IS NULL)),
    UNIQUE (ts, digest)
);
SELECT create_hypertable('health_events', by_range('ts', INTERVAL '1 day'));
CREATE INDEX health_events_kind_idx ON health_events (service, kind, ts DESC);
CREATE INDEX health_events_trade_date_idx ON health_events (trade_date, session, ts);

-- 무결측 판정 공백 (설계 §9). 같은 공백을 다시 판정하면 끝·기대·실수신을 고친다
CREATE TABLE collection_gaps (
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
SELECT create_hypertable('collection_gaps', by_range('start_ts', INTERVAL '1 day'));
CREATE INDEX collection_gaps_trade_date_idx ON collection_gaps (trade_date, session, stream);

-- 검증 실패 레코드 (PLAN §6.1). 해당 산출은 quality=invalid
CREATE TABLE quarantine (
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
SELECT create_hypertable('quarantine', by_range('ts', INTERVAL '1 day'));
CREATE INDEX quarantine_trade_date_idx ON quarantine (trade_date, session, tr_id, ts);
