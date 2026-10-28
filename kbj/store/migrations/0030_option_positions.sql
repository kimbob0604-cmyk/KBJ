-- 0030_option_positions — 옵션 행사가별 투자자 추정 포지션(metrics §34, ADR 0044 — 로그인 prv_gex).
--
-- - 거래소 값이 아니라 키움 방식 추정치다(투자자별 합계 매매 → 평균단가 → 가까운 두 행사가 2:1).
--   원천은 prv_gex.investor_flow(1분)·chain_snapshots(시세)·option_iv(델타) — 이 표는 그 **하루 배분 결과**만
--   둔다. 누적 보유·OI 검산·벽·합성 델타는 API 리더가 읽을 때 계산한다(KRX 확정 OI 가 다음 날 와도 다시
--   돌릴 작업이 없다).
-- - 쓰기 주인: gex.option_positions(kbj.services.engine.option_positions — 평일 16:05). 그날 행 교체(한
--   트랜잭션 — 같은 날 다시 돌려도 행이 늘지 않는다).
-- - 수량은 소수 계약(2:1 배분 — 합이 원 수량과 같게), 하루 단위 자료라 청크 1년(krx_*_daily 와 같다).

CREATE TABLE IF NOT EXISTS prv_gex.opt_pos_strike (
    trade_date    date NOT NULL,
    investor      text NOT NULL CHECK (investor IN ('frgn', 'prsn', 'orgn')),
    mrkt_cls      text NOT NULL CHECK (mrkt_cls IN ('', 'WKM', 'WKI')),
    expiry        text NOT NULL CHECK (expiry ~ '^[0-9]{6}$'),
    strike        numeric(8, 2) NOT NULL,
    cp            text NOT NULL CHECK (cp IN ('C', 'P')),
    buy_qty       double precision NOT NULL CHECK (buy_qty >= 0),   -- 그날 배분된 매수(계약)
    sell_qty      double precision NOT NULL CHECK (sell_qty >= 0),  -- 그날 배분된 매도(계약)
    close_price   numeric,                 -- 그날 마지막 시세(pt — 표시용)
    board_oi      bigint,                  -- 그날 마지막 전광판 OI(KRX 확정 전 검산 대용)
    delta         double precision,        -- 그날 마지막 델타(option_iv 자체 → 없으면 체인 KIS)
    delta_source  text CHECK (delta_source IN ('self', 'kis')),
    quality       text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    computed_at   timestamptz NOT NULL,
    loaded_by     text NOT NULL CHECK (loaded_by <> ''),
    CHECK ((delta IS NULL) = (delta_source IS NULL)),
    PRIMARY KEY (trade_date, investor, mrkt_cls, expiry, strike, cp)
);
SELECT create_hypertable('prv_gex.opt_pos_strike', by_range('trade_date', INTERVAL '365 days'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS opt_pos_strike_series_idx ON prv_gex.opt_pos_strike (investor, mrkt_cls, expiry, trade_date);

-- 묶음(시장·업종) 하루 요약 — 옵션 6묶음 + 선물 F001(순매수·근월물 가격). 사유는 문장이 아니라 품질 사유 목록
CREATE TABLE IF NOT EXISTS prv_gex.opt_pos_day (
    trade_date        date NOT NULL,
    investor          text NOT NULL CHECK (investor IN ('frgn', 'prsn', 'orgn')),
    market_code       text NOT NULL,       -- K2I · WKM · WKI
    sector_code       text NOT NULL,       -- OC01 · OP01 · OC05 · OP05 · OC04 · OP04 · F001
    minutes           integer NOT NULL CHECK (minutes >= 0),
    gap_minutes       integer NOT NULL CHECK (gap_minutes >= 0),
    first_ts          timestamptz,
    last_ts           timestamptz,
    total_buy         bigint,              -- 마지막 누적 매수 수량(옵션) = 배분 + 미배분
    total_sell        bigint,
    unallocated_buy   double precision NOT NULL DEFAULT 0,
    unallocated_sell  double precision NOT NULL DEFAULT 0,
    imprecise_qty     double precision NOT NULL DEFAULT 0,
    single_qty        double precision NOT NULL DEFAULT 0,
    reverted          integer NOT NULL DEFAULT 0,
    skipped           integer NOT NULL DEFAULT 0,
    futures_net       bigint,              -- F001 만 — 그날 마지막 행 순매수 수량
    fut_price         numeric,             -- F001 만 — 근월물 마지막 가격(fut_board)
    reasons           jsonb NOT NULL DEFAULT '[]'::jsonb,
    quality           text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    computed_at       timestamptz NOT NULL,
    loaded_by         text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (trade_date, investor, market_code, sector_code)
);
SELECT create_hypertable('prv_gex.opt_pos_day', by_range('trade_date', INTERVAL '365 days'), if_not_exists => TRUE);

COMMENT ON TABLE prv_gex.opt_pos_strike IS
    '쓰기 주인: gex.option_positions(kbj.services.engine.option_positions — ADR 0044). 키움 방식 추정치.';
COMMENT ON TABLE prv_gex.opt_pos_day IS
    '쓰기 주인: gex.option_positions(kbj.services.engine.option_positions — ADR 0044).';
