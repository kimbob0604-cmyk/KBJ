-- 0002_ops_core — 운영 표: 작업 실행·데이터 선점·키값·발송 기록·세션·health·이관 기록
-- (docs/p2_design.md §8.2·§8.3·§5.4·§6.5, conflict_map §1.5)
--
-- 규칙
-- - 모든 문장은 다시 돌려도 같은 결과(멱등): CREATE … IF NOT EXISTS, create_hypertable(if_not_exists).
--   적용기(kbj/store/migrate.py)는 한 번만 적용하고 체크섬을 남긴다 — 적용된 뒤엔 고치지 않는다.
-- - 시각은 timestamptz(UTC 로 넣는다), 날짜는 date. naive 시각 금지.
-- - ops 는 데이터 출처가 아니라 운영 기록이다. kbj_public_export 는 ops 를 읽지 못한다(0001).
-- - 값·키·토큰·계좌는 여기 남기지 않는다. detail·reason 은 쓰는 쪽이 가린 문장이다(절대 규칙 5).

-- ── 작업 실행 기록 (SD run_log·ET board/us run_log 흡수) ─────────────────────────────────
-- run_id 는 실행기가 정한다(예: '<job>:<as_of>:<attempt>'). as_of 는 AsOfKind 규칙으로 만든 문자열
-- (kbj/data/spec.py — 날짜·분·10일 구간·접수번호 …)이라 text 다.
CREATE TABLE IF NOT EXISTS ops.job_run (
    run_id       text PRIMARY KEY,
    job          text NOT NULL,
    as_of        text NOT NULL,
    attempt      integer NOT NULL DEFAULT 1 CHECK (attempt >= 1),
    status       text NOT NULL
                 CHECK (status IN ('queued', 'running', 'ok', 'failed', 'skipped', 'timeout')),
    started_at   timestamptz,
    finished_at  timestamptz,
    detail       jsonb NOT NULL DEFAULT '{}'::jsonb,
    source       text NOT NULL DEFAULT 'scheduler'
                 CHECK (source IN ('scheduler', 'manual', 'legacy_import')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
);
CREATE INDEX IF NOT EXISTS job_run_job_idx ON ops.job_run (job, as_of, attempt);
CREATE INDEX IF NOT EXISTS job_run_status_idx ON ops.job_run (status, created_at DESC);

-- ── 데이터 키 선점 (§6.5 — 같은 데이터는 한 작업만) ──────────────────────────────────────
-- 데이터 키 = (source, dataset, as_of, venue) — kbj/data/spec.py DataKey. venue 는 거래소를 나눠 받는
-- 데이터셋만 채운다(KRX·NXT·TOTAL), 나머지는 ''. claimed·done 은 키마다 하나만(부분 유일 인덱스),
-- failed 는 여러 줄이 남아도 된다(재시도가 다시 잡는다).
CREATE TABLE IF NOT EXISTS ops.data_claim (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source      text NOT NULL,
    dataset     text NOT NULL,
    as_of       text NOT NULL,
    venue       text NOT NULL DEFAULT '' CHECK (venue IN ('', 'KRX', 'NXT', 'TOTAL')),
    job         text NOT NULL,
    run_id      text,
    status      text NOT NULL CHECK (status IN ('claimed', 'done', 'failed')),
    claimed_at  timestamptz NOT NULL,
    done_at     timestamptz,             -- 끝난 시각(done 이면 반드시, failed 는 있으면)
    rows        integer CHECK (rows IS NULL OR rows >= 0),
    detail      jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (status <> 'done' OR done_at IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS data_claim_live_key
    ON ops.data_claim (source, dataset, as_of, venue)
    WHERE status IN ('claimed', 'done');
CREATE INDEX IF NOT EXISTS data_claim_job_idx ON ops.data_claim (job, claimed_at DESC);

-- ── 작은 운영 상태 키값 (SD ops_state·fetch_progress, ET board/us meta) ─────────────────────
-- namespace 로 출처를 가른다('sd', 'sd.fetch_progress', 'board.kr', 'board.us', kbj 서비스 이름 …).
-- 이관분은 원본에 시각이 없으면 updated_at 이 NULL 이다(지어내지 않는다).
CREATE TABLE IF NOT EXISTS ops.kv (
    namespace   text NOT NULL CHECK (namespace ~ '^[a-z][a-z0-9_.-]*$'),
    key         text NOT NULL CHECK (key <> ''),
    value       jsonb NOT NULL,
    updated_at  timestamptz,
    PRIMARY KEY (namespace, key)
);

-- ── 발송 기록 — 운영 메타만, 본문 없음 (§5.4) ─────────────────────────────────────────────
-- 본문은 prv_alerts.notify_message(0005 — 로그인 등급). dedup_key 는 notifier 정책(§5.3)이 만든
-- 중복 방지 키다(2차 방어 — 1차는 Redis SET NX).
CREATE TABLE IF NOT EXISTS ops.notify_log (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind          text NOT NULL,
    topic         text,
    as_of         date,
    subject       text,
    dedup_key     text NOT NULL UNIQUE,
    body_sha256   text CHECK (body_sha256 IS NULL OR body_sha256 ~ '^[0-9a-f]{64}$'),
    body_chars    integer CHECK (body_chars IS NULL OR body_chars >= 0),
    parts         integer CHECK (parts IS NULL OR parts >= 0),
    status        text NOT NULL
                  CHECK (status IN ('queued', 'sent', 'failed', 'suppressed', 'duplicate', 'cooldown')),
    reason        text,
    message_ids   bigint[] NOT NULL DEFAULT '{}',
    source        text,                  -- legacy 호출 지점 이름(sd.send_telegram 등) 또는 kbj 작업
    requested_at  timestamptz NOT NULL,
    sent_at       timestamptz
);
CREATE INDEX IF NOT EXISTS notify_log_kind_idx ON ops.notify_log (kind, requested_at DESC);
CREATE INDEX IF NOT EXISTS notify_log_status_idx ON ops.notify_log (status, requested_at DESC);

-- ── 세션 상태·health (GEXLAB db/migrations/001_init.sql 열 그대로 — kbj 서비스도 같은 표에 쓴다) ──
CREATE TABLE IF NOT EXISTS ops.session_log (
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
SELECT create_hypertable('ops.session_log', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS session_log_trade_date_idx ON ops.session_log (trade_date, session, ts);

CREATE TABLE IF NOT EXISTS ops.health_events (
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
SELECT create_hypertable('ops.health_events', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS health_events_kind_idx ON ops.health_events (service, kind, ts DESC);
CREATE INDEX IF NOT EXISTS health_events_trade_date_idx ON ops.health_events (trade_date, session, ts);

-- ── SQLite·JSON → Postgres 이관 기록 (§8.6·§8.7 — python -m kbj.store.legacy_import) ────────
-- 매핑 하나(원본 표 → 대상 표)가 한 줄. 값·키는 남기지 않는다 — 행 수·버림 사유별 수·키 다이제스트
-- (md5)·숫자 열 합만. source_sha256 은 원본 파일(읽기 전용으로 연) 전체의 sha256.
CREATE TABLE IF NOT EXISTS ops.legacy_import (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id        text NOT NULL,
    mapping         text NOT NULL,       -- 'board.px' 처럼 매핑 이름
    source_name     text NOT NULL,       -- --source 이름(sd·board·us·backtest …)
    source_sha256   text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    source_table    text NOT NULL,
    target_table    text NOT NULL,
    phase           text NOT NULL CHECK (phase ~ '^P[0-9]+$'),
    rows_read       integer NOT NULL CHECK (rows_read >= 0),
    rows_dropped    integer NOT NULL CHECK (rows_dropped >= 0),
    drops           jsonb NOT NULL DEFAULT '{}'::jsonb,   -- 버림 사유별 수
    rows_written    integer NOT NULL CHECK (rows_written >= 0),
    key_digest_src  text,
    key_digest_dst  text,
    sums            jsonb NOT NULL DEFAULT '{}'::jsonb,   -- {열: {src, dst}}
    status          text NOT NULL CHECK (status IN ('ok', 'mismatch', 'failed')),
    started_at      timestamptz NOT NULL,
    finished_at     timestamptz NOT NULL,
    UNIQUE (batch_id, mapping)
);
CREATE INDEX IF NOT EXISTS legacy_import_mapping_idx ON ops.legacy_import (mapping, finished_at DESC);
