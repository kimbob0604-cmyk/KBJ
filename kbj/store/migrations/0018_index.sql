-- 0018_index — 패시브 지수 정기변경(편입·편출) 예측(P5 2차 묶음 IDX — docs/p5_index_rebalance_prompt.md §3-3,
-- metrics §24). 행 자료형 kbj/core/index_rows.py, 저장소 kbj/store/repos/index_review.py.
--
-- 규칙
-- - 전부 로그인 등급(구성종목은 MSCI·KRX 독점 자료, 예측은 KRX·KIS 시세로 계산) — 새 스키마 prv_index 에만.
--   공개 내보내기 역할(kbj_public_export)은 이 스키마에 권한이 없다(0001 과 같은 방식 — PUBLIC 권한도 닫는다).
-- - 금액은 원 단위 bigint. 비율은 numeric(소수 — 0.124 = 12.4%).
-- - constituents.source: user(사용자가 VM 에 올린 목록) · press(보도자료 리뷰 결과를 사람이 반영) ·
--   synthetic(합성 — 시험·데모). 레포에는 합성 목록만 둔다.
-- - prediction_run.status 가 ok 가 아니면(no_constituents — 목록 없음, no_data — 시세 없음) 후보 수는 0
--   이고 quality 는 ok 일 수 없다(지어내지 않음 — DB 가 한 번 더 지킨다).
-- - 멱등: CREATE … IF NOT EXISTS. hypertable 은 쓰지 않는다(하루 한 번 수백 행).

CREATE SCHEMA IF NOT EXISTS prv_index;           -- 지수 구성종목·리뷰 일정·편입/편출 예측·패시브 규모
REVOKE ALL ON SCHEMA prv_index FROM PUBLIC;

-- ── 구성종목(기간) ──────────────────────────────────────────────────────────────────────────
-- valid_to = 마지막 날(포함), NULL = 지금도 구성종목. fif = 지수 운용사가 적용한 유동비율(목록에 있으면).
CREATE TABLE IF NOT EXISTS prv_index.constituents (
    index_id     text NOT NULL CHECK (index_id ~ '^[a-z][a-z0-9_]*$'),
    code         text NOT NULL CHECK (code ~ '^[0-9A-Z]{6}$'),
    valid_from   date NOT NULL,
    valid_to     date CHECK (valid_to IS NULL OR valid_to >= valid_from),
    name         text,
    fif          numeric CHECK (fif IS NULL OR (fif > 0 AND fif <= 1)),
    source       text NOT NULL CHECK (source IN ('user', 'press', 'synthetic')),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (index_id, code, valid_from)
);
CREATE INDEX IF NOT EXISTS constituents_active_idx ON prv_index.constituents (index_id, valid_to);

-- ── 리뷰 일정(설정 config/index_reviews.yaml → index.calendar) ──────────────────────────────────
-- confirmed = 공표 일정표와 대조했나(아니면 quality estimated). user_cutoff_won = 사용자가 넣은 증권사
-- 컷오프 추정치(있으면 그것으로도 같이 계산 — prediction_run.cutoff_kind 'user').
CREATE TABLE IF NOT EXISTS prv_index.review_calendar (
    index_id         text NOT NULL CHECK (index_id ~ '^[a-z][a-z0-9_]*$'),
    review_id        text NOT NULL CHECK (review_id ~ '^[0-9]{4}-[0-9]{2}$'),
    index_title      text NOT NULL CHECK (index_title <> ''),
    display_order    integer NOT NULL CHECK (display_order >= 1),
    base_date        date NOT NULL,
    announce_date    date NOT NULL,
    trade_date       date NOT NULL,
    effective_date   date NOT NULL,
    confirmed        boolean NOT NULL,
    user_cutoff_won  bigint CHECK (user_cutoff_won IS NULL OR user_cutoff_won > 0),
    source           text NOT NULL CHECK (source <> ''),
    quality          text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    updated_at       timestamptz NOT NULL,
    loaded_by        text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (index_id, review_id),
    CHECK (base_date <= announce_date AND announce_date <= trade_date AND trade_date < effective_date),
    CHECK (confirmed OR quality <> 'ok')
);

-- ── 예측 실행 요약(상태·컷오프 추정·건수) ───────────────────────────────────────────────────
-- base_date = 계산에 쓴 시세 날짜(리뷰 기준일 전엔 그날 최신, 기준일 뒤엔 기준일로 고정).
CREATE TABLE IF NOT EXISTS prv_index.prediction_run (
    index_id         text NOT NULL CHECK (index_id ~ '^[a-z][a-z0-9_]*$'),
    review_id        text NOT NULL CHECK (review_id ~ '^[0-9]{4}-[0-9]{2}$'),
    base_date        date NOT NULL,
    cutoff_kind      text NOT NULL CHECK (cutoff_kind IN ('model', 'user')),
    status           text NOT NULL CHECK (status IN ('ok', 'no_constituents', 'no_data')),
    cutoff_won       bigint CHECK (cutoff_won IS NULL OR cutoff_won > 0),
    cutoff_low_won   bigint,
    cutoff_high_won  bigint,
    cutoff_method    text,
    n_members        integer NOT NULL CHECK (n_members >= 0),
    n_add            integer NOT NULL CHECK (n_add >= 0),
    n_delete         integer NOT NULL CHECK (n_delete >= 0),
    n_borderline     integer NOT NULL CHECK (n_borderline >= 0),
    aum_won          bigint CHECK (aum_won IS NULL OR aum_won >= 0),
    data_as_of       timestamptz NOT NULL,
    notes            jsonb NOT NULL DEFAULT '[]'::jsonb,
    source           text NOT NULL CHECK (source <> ''),
    quality          text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    computed_at      timestamptz NOT NULL,
    loaded_by        text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (index_id, review_id, base_date, cutoff_kind),
    CHECK (status = 'ok' OR (n_add = 0 AND n_delete = 0 AND n_borderline = 0 AND quality <> 'ok'))
);

-- ── 종목 판정 ───────────────────────────────────────────────────────────────────────────────
-- verdict: add(편입 후보)·delete(편출 후보)·borderline(경계)·keep(유지). 후보 아닌 비구성종목은 넣지 않는다.
-- evidence = 근거 수치 JSON(키는 kbj.core.index_rows.EVIDENCE_KEYS — 시총·유동시총·유동비율·괴리율·
-- 조건 충족·필요 상승률·예상 패시브 매매·거래대금 배수·순위·사유).
CREATE TABLE IF NOT EXISTS prv_index.predictions (
    index_id     text NOT NULL CHECK (index_id ~ '^[a-z][a-z0-9_]*$'),
    review_id    text NOT NULL CHECK (review_id ~ '^[0-9]{4}-[0-9]{2}$'),
    base_date    date NOT NULL,
    cutoff_kind  text NOT NULL CHECK (cutoff_kind IN ('model', 'user')),
    code         text NOT NULL CHECK (code ~ '^[0-9A-Z]{6}$'),
    name         text,
    verdict      text NOT NULL CHECK (verdict IN ('add', 'delete', 'borderline', 'keep')),
    member       boolean NOT NULL,
    evidence     jsonb NOT NULL,
    cutoff_won   bigint,
    data_as_of   timestamptz NOT NULL,
    source       text NOT NULL CHECK (source <> ''),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    computed_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (index_id, review_id, base_date, cutoff_kind, code)
);
CREATE INDEX IF NOT EXISTS predictions_code_idx ON prv_index.predictions (code, base_date);

-- ── 지수 추종 패시브 자금 ──────────────────────────────────────────────────────────────────
-- kind: etf(P3 ETF 순자산 중 추적지수가 그 지수인 것의 합) · non_etf(설정 추정) · user(사용자 전체 추정).
CREATE TABLE IF NOT EXISTS prv_index.passive_aum (
    index_id     text NOT NULL CHECK (index_id ~ '^[a-z][a-z0-9_]*$'),
    as_of        date NOT NULL,
    kind         text NOT NULL CHECK (kind IN ('etf', 'non_etf', 'user')),
    aum_won      bigint NOT NULL CHECK (aum_won >= 0),
    n_funds      integer CHECK (n_funds IS NULL OR n_funds >= 0),
    detail       jsonb NOT NULL DEFAULT '{}'::jsonb,
    source       text NOT NULL CHECK (source <> ''),
    quality      text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    computed_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (index_id, as_of, kind)
);
