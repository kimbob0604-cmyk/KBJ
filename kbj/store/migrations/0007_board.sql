-- 0007_board — 신고가 보드 상태·라벨·산출 (docs/p3_design.md §3.4·§4.1, D-P3-10·11)
--
-- 규칙
-- - 로그인 등급(원천 KIS·KRX 시세 — DATA_TIERS §1)이라 prv_board 에만 둔다(ADR 0002). 0001 의
--   ALTER DEFAULT PRIVILEGES 는 pub_* 에만 걸려 있어 kbj_public_export 는 이 표들을 못 읽는다.
-- - 값 행은 source·quality(ok·stale·estimated·invalid)를 갖는다(CLAUDE.md 절대 규칙 1). 보드는
--   KIS 마감 스냅으로 만든 날은 estimated, KRX 확정 일봉으로 다시 만든 날(board.confirm)은 ok.
-- - market = 'KR'·'US'(0003 과 같다 — P3 은 KR 만 쓴다). 종목코드는 text. 금액은 원 단위.
-- - ET board/engine/db.py 의 sqlite 표(alltime·label·split_check)를 열 이름 그대로 옮기고, ET
--   state/<날짜>/*.json 산출은 stock_day(종목 행)·artifact(JSON 5종)로 둔다. ET 의 px·snap 은
--   prv_market.daily_bar·stock_snapshot(0003), sector_map 은 config/knowledge(P5 에 pub_themes),
--   meta·run_log 는 ops.job_run(0002)이 대신한다.
-- - 멱등: CREATE … IF NOT EXISTS, create_hypertable(if_not_exists). 적용된 뒤엔 고치지 않는다.

-- ── 역사적 최고가 스칼라 (ET db.py DDL alltime 열 그대로 + history_from·source·quality) ───────
-- prev_hi·prev_cl = 직전 처리일까지의 최고가(당일 갱신 판정의 기준 — ET newhigh.roll_alltime).
-- history_from = 받은 일봉 이력의 첫 날. 상장일이 그보다 앞이면 hist 를 계산하지 않는다(D-P3-11 —
-- 네이버 출처 옛 최고가는 옮기지 않는다, 메인 결정 R8).
CREATE TABLE IF NOT EXISTS prv_board.alltime (
    market        text NOT NULL CHECK (market IN ('KR', 'US')),
    code          text NOT NULL CHECK (code <> '' AND code !~ '\s'),
    hi            numeric,
    hi_date       date,
    cl            numeric,
    cl_date       date,
    prev_hi       numeric,
    prev_cl       numeric,
    first_date    date,
    last_date     date,
    n_days        integer NOT NULL DEFAULT 0 CHECK (n_days >= 0),
    suspect       boolean NOT NULL DEFAULT false,   -- 수정주가 미반영 의심(split_guard)
    suspect_date  date,
    suspect_note  text,
    history_from  date,
    source        text NOT NULL CHECK (source <> ''),
    quality       text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    updated_at    timestamptz NOT NULL,
    loaded_by     text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (market, code)
);

-- ── 일자별 신고가 라벨 ('신규 / 이어감' 판정이 전일 행을 읽는다 — ET db.py label) ─────────────
-- basis: close(종가 기준 — 기본)·high(고가 기준). kind: hist·w52·d60(KR), w52_low(US 보드).
-- rank: 0=hist 1=w52 2=d60 — 작을수록 상위(ET 그대로).
CREATE TABLE IF NOT EXISTS prv_board.label (
    market       text NOT NULL CHECK (market IN ('KR', 'US')),
    code         text NOT NULL CHECK (code <> '' AND code !~ '\s'),
    trade_date   date NOT NULL,
    basis        text NOT NULL CHECK (basis IN ('close', 'high')),
    kind         text NOT NULL CHECK (kind IN ('hist', 'w52', 'd60', 'w52_low')),
    rank         smallint NOT NULL CHECK (rank >= 0),
    source       text NOT NULL CHECK (source <> ''),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    computed_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (market, code, trade_date, basis)
);
SELECT create_hypertable(
    'prv_board.label', by_range('trade_date', INTERVAL '365 days'), if_not_exists => TRUE
);
CREATE INDEX IF NOT EXISTS label_date_idx ON prv_board.label (market, trade_date, basis);

-- ── 분할 의심 종목의 공시 대조 결과 (ET db.py split_check — D-056) ─────────────────────────────
-- action = 분할·병합·감자·무상증자 공시가 있다(가드 유지) · none = 없다(가드 해제) ·
-- unknown = 조회 실패(가드를 풀지 않는다 — 못 본 것을 없다고 적지 않는다).
CREATE TABLE IF NOT EXISTS prv_board.split_check (
    market      text NOT NULL CHECK (market IN ('KR', 'US')),
    code        text NOT NULL CHECK (code <> '' AND code !~ '\s'),
    jump_date   date,
    verdict     text NOT NULL CHECK (verdict IN ('action', 'none', 'unknown')),
    note        text NOT NULL DEFAULT '',
    source      text NOT NULL CHECK (source <> ''),
    updated_at  timestamptz NOT NULL,
    PRIMARY KEY (market, code)
);

-- ── 보드의 종목 하루 (ET build.run 의 universe 행 핵심 열 + extra) ──────────────────────────────
-- turnover·mktcap 은 원(보드 엔진 안의 억원 값은 저장할 때 원으로). turnover_is_estimate =
-- 종가×거래량 추정. label = 기본 기준(default_basis)의 라벨, near_* = 근접 라벨·간격(%).
-- ret_5d·ret_21d = 5·21영업일 수익률(%), vol_mult = 거래량 배수. 나머지 키는 extra 에 그대로.
-- 압축은 걸지 않는다 — board.confirm·백필 재적재가 지난 날짜를 지우고 다시 쓴다(크기는 P9 에 본다).
CREATE TABLE IF NOT EXISTS prv_board.stock_day (
    market                text NOT NULL CHECK (market IN ('KR', 'US')),
    code                  text NOT NULL CHECK (code <> '' AND code !~ '\s'),
    trade_date            date NOT NULL,
    name                  text,
    close                 numeric,
    chg_pct               numeric,
    turnover              numeric CHECK (turnover IS NULL OR turnover >= 0),
    turnover_is_estimate  boolean NOT NULL DEFAULT false,
    mktcap                numeric CHECK (mktcap IS NULL OR mktcap >= 0),
    label                 text CHECK (label IN ('hist', 'w52', 'd60', 'w52_low')),
    near_kind             text,
    near_gap              numeric,
    status                text,
    suspect               boolean NOT NULL DEFAULT false,
    sector                text,
    theme                 text,
    ret_5d                numeric,
    ret_21d               numeric,
    vol_mult              numeric,
    extra                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    source                text NOT NULL CHECK (source <> ''),
    quality               text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    computed_at           timestamptz NOT NULL,
    loaded_by             text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (market, code, trade_date)
);
SELECT create_hypertable(
    'prv_board.stock_day', by_range('trade_date', INTERVAL '365 days'), if_not_exists => TRUE
);
CREATE INDEX IF NOT EXISTS stock_day_date_idx ON prv_board.stock_day (market, trade_date);

-- ── 보드 산출 JSON (ET state/<날짜>/{universe,newhigh,sectors,events,rankings}.json 키 그대로) ──
-- universe_meta = universe.json 에서 종목 행(stock_day)을 뺀 나머지. 하루 5행이라 hypertable 아님.
-- input_digest = 입력 묶음의 sha256(같은 입력·같은 엔진 버전이면 같은 산출 — 재실행 멱등 확인).
CREATE TABLE IF NOT EXISTS prv_board.artifact (
    market          text NOT NULL CHECK (market IN ('KR', 'US')),
    trade_date      date NOT NULL,
    name            text NOT NULL
                    CHECK (name IN ('universe_meta', 'newhigh', 'sectors', 'events', 'rankings')),
    payload         jsonb NOT NULL,
    engine_version  text NOT NULL CHECK (engine_version <> ''),
    input_digest    text NOT NULL CHECK (input_digest <> ''),
    as_of           timestamptz NOT NULL,
    source          text NOT NULL CHECK (source <> ''),
    quality         text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    generated_at    timestamptz NOT NULL,
    loaded_by       text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (market, trade_date, name)
);
