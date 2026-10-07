-- 0005_alerts_inbox — 텔레그램 인박스와 발송 본문 (docs/p2_design.md §5.4·§5.8·§8.3)
--
-- - 개인 데이터(대화·본문 — 본문에는 시세·포트폴리오가 섞인다)라 prv_alerts 에 둔다(ADR 0002
--   "가장 높은 등급"). 운영 메타(본문 없음)는 ops.notify_log(0002).
-- - 멱등: CREATE … IF NOT EXISTS. 적용된 뒤엔 고치지 않는다.

-- ── 인박스 (ET board/ingest/tg_inbox.py 의 state/inbox.json 항목 모양 그대로 — §5.8) ─────────
-- kind: x(x.com·twitter.com·t.co 링크가 있다) · other. text_via: message(본문) · oembed(oEmbed 로
-- 채움) · NULL(본문이 URL 뿐이고 아직 못 채움 — 지어내지 않는다). date 는 메시지 시각(텔레그램),
-- received_at 은 받은 시각(이관분은 모르면 NULL). 보존 14일 — ops.nightly 가 지운다(묶음 F).
CREATE TABLE IF NOT EXISTS prv_alerts.tg_inbox (
    update_id    bigint PRIMARY KEY,
    chat_id      bigint,
    date         timestamptz,
    text         text NOT NULL DEFAULT '',
    urls         text[] NOT NULL DEFAULT '{}',
    x_ids        text[] NOT NULL DEFAULT '{}',
    author       text,
    kind         text NOT NULL CHECK (kind IN ('x', 'other')),
    text_via     text CHECK (text_via IN ('message', 'oembed')),
    received_at  timestamptz
);
CREATE INDEX IF NOT EXISTS tg_inbox_date_idx ON prv_alerts.tg_inbox (date DESC);

-- ── 발송 본문 (§5.4 — 30일 보존 [제안]) ─────────────────────────────────────────────────────
-- dedup_key 로 ops.notify_log 한 줄과 짝을 이룬다. 같은 트랜잭션 안에서는 어느 쪽을 먼저 넣어도
-- 되게 외래키 검사를 커밋 때로 미룬다. 발송 기록을 지우면 본문도 지운다.
CREATE TABLE IF NOT EXISTS prv_alerts.notify_message (
    dedup_key   text PRIMARY KEY
                REFERENCES ops.notify_log (dedup_key) ON DELETE CASCADE
                DEFERRABLE INITIALLY DEFERRED,
    body        text NOT NULL,
    parse_mode  text,                    -- HTML · Markdown … · NULL(평문)
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS notify_message_created_idx ON prv_alerts.notify_message (created_at);
