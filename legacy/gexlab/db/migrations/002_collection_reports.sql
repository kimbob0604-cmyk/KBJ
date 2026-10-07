-- 002_collection_reports — 무결측 판정 리포트 (docs/phase1_design.md §9)
--
-- scheduler 가 세션마다(주간은 POST_DAY, 야간은 06:10 분봉 적재 뒤 IDLE) 스트림별로 한 행 —
-- 기대·실수신·공백 수·최대 공백·판정(ok·gaps·unverified)·필수 여부. 공백 자체는 001 의
-- collection_gaps. scripts/nogap_report.py 가 이 표로 '3거래일 연속 무결측'을 판정한다 — 리포트가
-- 없는 세션은 판정 전(무결측이 아니다)이다.
--
-- 같은 세션을 다시 판정하면 그 세션의 collection_gaps·collection_reports 를 트랜잭션 하나에서 지우고
-- 새로 쓴다(data/store.py replace_gap_report) — 늦게 들어온 데이터로 공백이 줄어도 옛 행이 남지
-- 않는다. 유니크 키는 그 교체 안의 중복 방지용이다.

CREATE TABLE collection_reports (
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
SELECT create_hypertable('collection_reports', by_range('trade_date', INTERVAL '1 day'));
