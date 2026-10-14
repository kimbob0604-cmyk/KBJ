-- 0020_diag — 페이지 3 진단(공공지표 진단·매크로 위험 게이지·이벤트 / 시장 진단·국장 공포탐욕·종합 판정·
-- VKOSPI)과 prv_gex 표의 쓰기 주인 기록(docs/p7_design.md §3.3·§3.4, D-P7-7·12~15, metrics §D0~§D9).
-- 행 자료형 kbj/core/p7_rows.py, 저장소 kbj/store/repos/diag.py.
--
-- 규칙
-- - 공개 판정(공공데이터만 — ECOS·금투협·관세청·캘린더)은 새 스키마 pub_diag, 로그인 판정(KRX·KIS 원장·
--   GEX·VKOSPI)은 새 스키마 prv_diag. 권한은 ADR 0002 그대로: kbj_public_export 는 pub_diag 에 USAGE·SELECT
--   만, prv_diag 는 PUBLIC 권한을 닫는다(0018 과 같은 꼴).
-- - 모든 값 행에 source·as_of·quality(절대 규칙 1). 값 칸은 NULL 허용(0 으로 채우지 않는다) — 값이 없으면
--   quality 가 ok 일 수 없다(CHECK, 절대 규칙 2). 판정 'hold'(보류)는 점수가 없고 ok 가 아니다.
-- - 백분위 pct_rank 는 0~100. 공포탐욕 점수 0~100(소수 첫째). 상태 점수 parts 는 jsonb {성분: −1|0|1}.
-- - 일별 작은 표라 hypertable 이 아니다. 같은 날 재실행은 키를 덮는다(멱등 — 저장소 UPSERT).
-- - **prv_gex 표는 고치지 않는다**(0006 체크섬). COMMENT ON TABLE 로 쓰기 주인만 적는다(§3.4).
--   investor_flow 의 COMMENT 는 0017 그대로. ops 의 GX 표는 legacy GX 가 쓴다(코드 grep — 2026-10-13).
-- - 멱등: CREATE … IF NOT EXISTS, COMMENT 반복 안전.

CREATE SCHEMA IF NOT EXISTS pub_diag;            -- 공공지표 진단·매크로 위험 게이지·이벤트 표시(공개)
CREATE SCHEMA IF NOT EXISTS prv_diag;            -- 시장 진단·국장 공포탐욕·종합 판정·변동성 지수(로그인)
REVOKE ALL ON SCHEMA pub_diag, prv_diag FROM PUBLIC;
GRANT USAGE ON SCHEMA pub_diag TO kbj_public_export;
ALTER DEFAULT PRIVILEGES IN SCHEMA pub_diag GRANT SELECT ON TABLES TO kbj_public_export;

-- ── 공개 지표(§D1·§D2·§D3·§D5 — diag.public) ─────────────────────────────────────────────────
-- state: 백분위 다섯 칸(§D0) 또는 신용스프레드 σ 세 칸(§D1). 이력 < 60거래일이면 NULL(estimated).
CREATE TABLE IF NOT EXISTS pub_diag.indicator (
    trade_date   date NOT NULL,
    key          text NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]*$'),
    value        numeric,
    unit         text NOT NULL CHECK (unit <> ''),
    state        text CHECK (state IN ('extreme_low', 'low', 'normal', 'high', 'extreme_high', 'narrow', 'wide')),
    pct_rank     numeric CHECK (pct_rank >= 0 AND pct_rank <= 100),
    inputs       jsonb NOT NULL DEFAULT '{}'::jsonb,
    flags        jsonb NOT NULL DEFAULT '[]'::jsonb,
    source       text NOT NULL CHECK (source <> ''),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    reasons      jsonb NOT NULL DEFAULT '[]'::jsonb,
    computed_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    CHECK (value IS NOT NULL OR quality <> 'ok'),
    CHECK (value IS NOT NULL OR (state IS NULL AND pct_rank IS NULL)),
    PRIMARY KEY (trade_date, key)
);

-- 매크로 위험 게이지(§D4 — 공공 판정). parts = {성분: −1|0|1}, 성분 < 3 이면 hold.
CREATE TABLE IF NOT EXISTS pub_diag.gauge (
    trade_date   date PRIMARY KEY,
    label        text NOT NULL CHECK (label IN ('risk_on', 'neutral', 'risk_off', 'hold')),
    score        integer,
    parts        jsonb NOT NULL DEFAULT '{}'::jsonb,
    source       text NOT NULL CHECK (source <> ''),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    reasons      jsonb NOT NULL DEFAULT '[]'::jsonb,
    computed_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    CHECK ((label = 'hold') = (score IS NULL)),
    CHECK (label <> 'hold' OR quality <> 'ok')
);

-- 이벤트 표시(§D9 — 만기·금통위·수출 속보 공표일·실적 집중). title 이 기본 키에 든다(같은 날 같은 종류 둘).
CREATE TABLE IF NOT EXISTS pub_diag.event_marker (
    date         date NOT NULL,
    kind         text NOT NULL CHECK (kind IN ('expiry', 'mpc', 'export_flash', 'earnings_cluster')),
    title        text NOT NULL CHECK (title <> ''),
    detail       jsonb NOT NULL DEFAULT '{}'::jsonb,
    source       text NOT NULL CHECK (source <> ''),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    computed_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (date, kind, title)
);

-- ── 로그인 지표(§D6~§D8 — diag.daily, §D7 입력 VKOSPI — krx.vkospi) ──────────────────────────
CREATE TABLE IF NOT EXISTS prv_diag.indicator (
    trade_date   date NOT NULL,
    key          text NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]*$'),
    value        numeric,
    unit         text NOT NULL CHECK (unit <> ''),
    state        text CHECK (state IN ('extreme_low', 'low', 'normal', 'high', 'extreme_high', 'narrow', 'wide')),
    pct_rank     numeric CHECK (pct_rank >= 0 AND pct_rank <= 100),
    inputs       jsonb NOT NULL DEFAULT '{}'::jsonb,
    flags        jsonb NOT NULL DEFAULT '[]'::jsonb,
    source       text NOT NULL CHECK (source <> ''),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    reasons      jsonb NOT NULL DEFAULT '[]'::jsonb,
    computed_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    CHECK (value IS NOT NULL OR quality <> 'ok'),
    CHECK (value IS NOT NULL OR (state IS NULL AND pct_rank IS NULL)),
    PRIMARY KEY (trade_date, key)
);

-- 국장 공포탐욕(§D7). components = {성분: 0~100 | null}. 성분 < 4 → score NULL(invalid).
CREATE TABLE IF NOT EXISTS prv_diag.fear_greed (
    trade_date   date PRIMARY KEY,
    score        numeric CHECK (score >= 0 AND score <= 100),
    band         text CHECK (band IN ('extreme_fear', 'fear', 'neutral', 'greed', 'extreme_greed')),
    components   jsonb NOT NULL DEFAULT '{}'::jsonb,
    source       text NOT NULL CHECK (source <> ''),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    reasons      jsonb NOT NULL DEFAULT '[]'::jsonb,
    computed_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    CHECK ((score IS NULL) = (band IS NULL)),
    CHECK (score IS NOT NULL OR quality <> 'ok')
);

-- 종합 판정(§D8). event_flags = 이벤트 주의 꼬리표(§D9 kind 목록).
CREATE TABLE IF NOT EXISTS prv_diag.verdict (
    trade_date   date PRIMARY KEY,
    label        text NOT NULL CHECK (label IN ('risk_on', 'neutral', 'risk_off', 'hold')),
    score        integer,
    parts        jsonb NOT NULL DEFAULT '{}'::jsonb,
    event_flags  jsonb NOT NULL DEFAULT '[]'::jsonb,
    source       text NOT NULL CHECK (source <> ''),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    reasons      jsonb NOT NULL DEFAULT '[]'::jsonb,
    computed_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    CHECK ((label = 'hold') = (score IS NULL)),
    CHECK (label <> 'hold' OR quality <> 'ok')
);

-- 변동성 지수 일별(KRX:idx/drvprod_dd_trd — VKOSPI 행 [실측 필요 #P7-1]). 작업 krx.vkospi 는 꺼진 채.
CREATE TABLE IF NOT EXISTS prv_diag.vol_index (
    trade_date   date NOT NULL,
    index_id     text NOT NULL CHECK (index_id ~ '^[a-z][a-z0-9_]*$'),
    close        numeric CHECK (close >= 0),
    source       text NOT NULL CHECK (source <> ''),
    as_of        timestamptz NOT NULL,
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    CHECK (close IS NOT NULL OR quality <> 'ok'),
    PRIMARY KEY (trade_date, index_id)
);

GRANT SELECT ON pub_diag.indicator TO kbj_public_export;
GRANT SELECT ON pub_diag.gauge TO kbj_public_export;
GRANT SELECT ON pub_diag.event_marker TO kbj_public_export;

-- ── 쓰기 주인(§3.4 — 등록부 writes 와 같은 말. 표 정의는 0006 그대로) ──────────────────────────
COMMENT ON TABLE pub_diag.indicator IS '쓰기 주인: diag.public(kbj.services.engine.diag_pub). 공공데이터만.';
COMMENT ON TABLE pub_diag.gauge IS '쓰기 주인: diag.public. 매크로 위험 게이지(metrics §D4).';
COMMENT ON TABLE pub_diag.event_marker IS '쓰기 주인: diag.public. 이벤트 표시(metrics §D9).';
COMMENT ON TABLE prv_diag.indicator IS
    '쓰기 주인: diag.daily(kbj.services.engine.diag). 그날 D 는 estimated, D−1 은 KRX 확정으로 다시 계산해 덮는다.';
COMMENT ON TABLE prv_diag.fear_greed IS '쓰기 주인: diag.daily. 국장 공포탐욕(metrics §D7).';
COMMENT ON TABLE prv_diag.verdict IS '쓰기 주인: diag.daily. 종합 판정(metrics §D8).';
COMMENT ON TABLE prv_diag.vol_index IS '쓰기 주인: krx.vkospi(꺼짐 — 실측 #P7-1 전).';

COMMENT ON TABLE prv_gex.levels IS '쓰기 주인: gex.engine(KBJ kbj.services.engine.gex — P7).';
COMMENT ON TABLE prv_gex.metrics IS '쓰기 주인: gex.engine(KBJ — P7). flag 는 계산 당시 기능 플래그.';
COMMENT ON TABLE prv_gex.strike_gex IS '쓰기 주인: gex.engine(KBJ — P7).';
COMMENT ON TABLE prv_gex.option_iv IS '쓰기 주인: gex.engine(KBJ — P7).';
COMMENT ON TABLE prv_gex.oi_changes IS '쓰기 주인: gex.engine(KBJ — P7).';
COMMENT ON TABLE prv_gex.fut_ticks IS '쓰기 주인: gex.ws_gateway(KBJ kbj.services.ws_gateway — P7).';
COMMENT ON TABLE prv_gex.opt_ticks IS '쓰기 주인: gex.ws_gateway(KBJ — P7).';
COMMENT ON TABLE prv_gex.raw_messages IS
    '쓰기 주인: gex.recorder(KBJ kbj.services.recorder — P7). 예외: 버스 발행 실패 때 발행자가 직접 쓴다'
    '(digest UNIQUE 로 멱등 — 녹화를 잃지 않으려는 장치, ADR 0029).';
COMMENT ON TABLE prv_gex.chain_snapshots IS '쓰기 주인: gex.poller(legacy GX — KBJ DB search_path, P9 이식).';
COMMENT ON TABLE prv_gex.fut_board IS '쓰기 주인: gex.poller(legacy GX — P9 이식).';
COMMENT ON TABLE prv_gex.series_expiries IS '쓰기 주인: gex.poller(legacy GX — P9 이식).';
COMMENT ON TABLE prv_gex.minute_bars IS
    '쓰기 주인: GX MinuteDaily 처리기 하나(legacy — 등록부 gex.day_minutes·gex.night_minutes, 세션으로 행이 갈림).';
COMMENT ON TABLE prv_gex.krx_fut_daily IS '쓰기 주인: gex.krx_derivatives(legacy GX).';
COMMENT ON TABLE prv_gex.krx_opt_daily IS '쓰기 주인: gex.krx_derivatives(legacy GX).';
COMMENT ON TABLE prv_gex.master_snapshots IS '쓰기 주인: kis.master(legacy GX).';
COMMENT ON TABLE ops.collection_gaps IS '쓰기 주인: legacy GX scheduler 무결측 판정(GapDaily — replace_gap_report).';
COMMENT ON TABLE ops.collection_reports IS '쓰기 주인: legacy GX scheduler 무결측 판정(GapDaily — replace_gap_report).';
COMMENT ON TABLE ops.quarantine IS
    '덧붙이기 전용 사건 기록: legacy GX poller·scheduler 분봉이 격리 행을 남긴다(값 원장 아님 — 주인 규칙 밖).';
