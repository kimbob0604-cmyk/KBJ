-- 003_engine — engine 산출 저장 (docs/phase3_design.md §2, PLAN §5.2~§5.6)
--
-- engine(services/engine)이 사이클마다(chain.ready ≈ 30초, 없으면 10초 따라잡기) 쓴다.
-- - ts = 사이클 기준 시각(as_of — chain.ready 의 ts, 곧 그 사이클 입력 체인 행의 가장 늦은 수신 시각).
--   trade_date·session 은 그 사이클의 귀속 거래일·세션(야간은 T+1)
-- - 같은 사이클을 다시 계산하면(재기동 뒤 따라잡기 등) 같은 키 — DO UPDATE 로 행이 늘지 않는다
--   (data/store.py 의 Table 상수가 이 파일과 같다는 것을 단위 테스트가 고정한다)
-- - 금액은 원(GEX 는 원/기초 1% — 표시 억원), 가격·행사가·F 는 pt, IV 는 연율 소수, T 는 년
-- - 모든 행에 quality(ok|stale|estimated|invalid). jsonb 는 정규화 JSON(키 정렬)
-- - 전부 hypertable(ts, 청크 1일)
--
-- 001·002 는 적용된 뒤라 고치지 않는다(db/migrate.py 체크섬).

-- 범위(all·nearest·0dte)별 핵심 레벨 (metrics §3): call_wall·put_wall·abs_gamma(행사가 pt) ·
-- flip(pt, detail 에 교차 목록·multi_cross) · flip_distance(%) · expected_move_calendar·
-- expected_move_trading(±1σ pt) · top_levels(레벨 수, detail 에 목록). 값이 없으면 NULL
CREATE TABLE levels (
    ts          timestamptz NOT NULL,
    trade_date  date NOT NULL,
    session     text NOT NULL CHECK (session IN ('day', 'night')),
    scope       text NOT NULL CHECK (scope IN ('all', 'nearest', '0dte')),
    name        text NOT NULL,
    value       numeric,
    detail      jsonb NOT NULL DEFAULT '{}'::jsonb,
    quality     text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    reasons     jsonb NOT NULL DEFAULT '[]'::jsonb,  -- 품질을 떨어뜨린 사유 목록
    UNIQUE (ts, scope, name)
);
SELECT create_hypertable('levels', by_range('ts', INTERVAL '1 day'));
CREATE INDEX levels_trade_date_idx ON levels (trade_date, session, ts);
CREATE INDEX levels_name_idx ON levels (scope, name, ts DESC);

-- 지표 값 (metrics §2·§3·§4~§7). scope: all·nearest·0dte(범위 합산), series(만기 하나 — key 가
-- 시리즈 라벨 'WKM:261001'). flag: 계산 당시 기능 플래그(설계 §4 — shadow 는 저장만, 발행 안 함)
CREATE TABLE metrics (
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
SELECT create_hypertable('metrics', by_range('ts', INTERVAL '1 day'));
CREATE INDEX metrics_trade_date_idx ON metrics (trade_date, session, ts);
CREATE INDEX metrics_metric_idx ON metrics (metric, scope, key, ts DESC);

-- 행사가별 GEX (metrics §2.1, 원/1% — 대시보드 막대). 만기 하나·행사가 하나. 제외 종목은 0.
-- quality·excluded_oi_ratio 는 그 만기 표의 것, forward 는 그 만기 F(pt)
CREATE TABLE strike_gex (
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
SELECT create_hypertable('strike_gex', by_range('ts', INTERVAL '1 day'));
CREATE INDEX strike_gex_trade_date_idx ON strike_gex (trade_date, session, ts);

-- 종목별 자체 IV (metrics §1.1~§1.6 — 스마일·HIRO 입력). 검증 수정 3: 옵션별 IV 를 처음 저장하는
-- 곳이 source(self 자체 역산 · kis 폴백)·rescaled·t_kis 를 함께 둔다(KIS 폴백 σ 는 자체 T 로 옮긴 값).
-- delta·gamma 는 자체 그릭스(GEX 에 든 종목만) — KIS 그릭스가 아니다
CREATE TABLE option_iv (
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
SELECT create_hypertable('option_iv', by_range('ts', INTERVAL '1 day'));
CREATE INDEX option_iv_trade_date_idx ON option_iv (trade_date, session, ts);
CREATE INDEX option_iv_series_idx ON option_iv (mrkt_cls, expiry, strike, cp, ts DESC);

-- 스냅샷 사이 OI 증감 (metrics §6.7 — 히트맵). outlier: 줄었다가 다음 스냅샷에 90% 이상 복구된
-- 두 칸(이상치 격리 — 표시 제외, health)
CREATE TABLE oi_changes (
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
    UNIQUE (ts, mrkt_cls, expiry, strike, cp)
);
SELECT create_hypertable('oi_changes', by_range('ts', INTERVAL '1 day'));
CREATE INDEX oi_changes_trade_date_idx ON oi_changes (trade_date, session, ts);
