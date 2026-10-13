-- 0010_filings — DART 공시·기업개황·잠정실적·실적 일정·오버행·내부자·대량보유·자기주식·주식총수
-- (docs/p4_design.md §3.4, D-P4-2·3·4)
--
-- 규칙
-- - 공개 등급(DART — DATA_TIERS §1)이라 pub_filings 에 둔다. 이 파일의 표에는 로그인 등급 출처의 값이
--   한 칸도 들어가지 않는다(ADR 0002 §2.1 — source 는 'DART' 만, CHECK). 시세가 들어가는 혼합 값(오버행
--   금액·ITM·내부자 금액·관심종목·시총 가산 중요도·서프라이즈)은 읽을 때 계산하고 여기 쓰지 않는다(§2).
-- - 값 행은 source·quality(ok·stale·estimated·invalid)·received_at(받은 시각)·loaded_by(쓴 작업). 파생 표
--   (overhang_state·overhang_summary·insider_window·earnings_schedule)는 received_at 대신 computed_at.
-- - 종목코드 text(^[0-9A-Z]{6}$ — 영숫자 단축코드 포함), corp_code ^[0-9]{8}$, 접수번호 ^[0-9]{14}$.
--   금액은 원 단위 bigint(DART 원 정수 — 외국 통화 회사는 currency 열, 환산하지 않음). 가격·비율은 numeric.
-- - 원문(document.xml) 자체는 저장하지 않는다(파싱 결과 + 접수번호 링크만 — D-P4-3).
-- - 정정 공시는 새 접수번호의 새 행이다. 읽는 쪽(파생·화면)이 같은 대상의 가장 늦은 접수번호를 쓴다.
-- - 멱등: CREATE … IF NOT EXISTS, GRANT 반복은 안전. 적용된 뒤엔 고치지 않는다 — 바꿀 일은 새 번호로.

-- ── 공시 목록 (DART:list — 증분 피드 filings.dart_feed) ──────────────────────────────────────
-- first_seen_at = 우리가 처음 본 시각(DART 응답에는 접수 시각이 없다 — 날짜뿐, §4.1).
-- kind = 공시 종류(P2 kind_of + P4 새 종류 — 소문자 식별자). kw_score·kw_matched 는 filings.derive 가 채운다.
-- parse_state: none(파싱 대상 아님)·pending·done·failed·na(3회 실패 — parse_note 사유).
-- fin_loaded_at = 정기보고서 재무를 fin.quarterly 가 적재한 시각(대기 상태 폴링 — D-P4-4).
CREATE TABLE IF NOT EXISTS pub_filings.disclosure (
    rcept_no       text PRIMARY KEY CHECK (rcept_no ~ '^[0-9]{14}$'),
    corp_code      text NOT NULL CHECK (corp_code ~ '^[0-9]{8}$'),
    stock_code     text CHECK (stock_code IS NULL OR stock_code ~ '^[0-9A-Z]{6}$'),
    corp_name      text NOT NULL CHECK (corp_name <> ''),
    corp_cls       text CHECK (corp_cls IS NULL OR corp_cls IN ('Y', 'K', 'N', 'E')),
    title          text NOT NULL CHECK (title <> ''),
    base_title     text NOT NULL CHECK (base_title <> ''),
    filer          text,
    rcept_dt       date NOT NULL,
    remark         text,
    kind           text NOT NULL CHECK (kind ~ '^[a-z][a-z0-9_]*$'),
    is_correction  boolean NOT NULL DEFAULT false,
    kw_score       smallint CHECK (kw_score IS NULL OR kw_score >= 0),
    kw_matched     text[] NOT NULL DEFAULT '{}',
    first_seen_at  timestamptz NOT NULL,
    parse_state    text NOT NULL DEFAULT 'none' CHECK (parse_state IN ('none', 'pending', 'done', 'failed', 'na')),
    parse_tries    smallint NOT NULL DEFAULT 0 CHECK (parse_tries >= 0),
    parse_note     text,
    fin_loaded_at  timestamptz,
    source         text NOT NULL DEFAULT 'DART' CHECK (source = 'DART'),
    quality        text NOT NULL DEFAULT 'ok' CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at    timestamptz NOT NULL,
    loaded_by      text NOT NULL CHECK (loaded_by <> '')
);
CREATE INDEX IF NOT EXISTS disclosure_stock_idx ON pub_filings.disclosure (stock_code, rcept_dt DESC);
CREATE INDEX IF NOT EXISTS disclosure_day_kind_idx ON pub_filings.disclosure (rcept_dt, kind);
CREATE INDEX IF NOT EXISTS disclosure_corp_idx ON pub_filings.disclosure (corp_code, rcept_dt DESC);
CREATE INDEX IF NOT EXISTS disclosure_parse_idx
    ON pub_filings.disclosure (parse_state) WHERE parse_state IN ('pending', 'failed');

-- ── 기업개황 (DART:company — filings.company_profile, 주 1회) ─────────────────────────────────
-- acc_mt = 결산월(1~12 — DART 문자열 '12' → 12 [확인 필요 #34]). 대표자 이름 등 개인 칸은 받지 않는다.
CREATE TABLE IF NOT EXISTS pub_filings.corp_profile (
    corp_code    text PRIMARY KEY CHECK (corp_code ~ '^[0-9]{8}$'),
    stock_code   text CHECK (stock_code IS NULL OR stock_code ~ '^[0-9A-Z]{6}$'),
    corp_name    text,
    acc_mt       smallint CHECK (acc_mt IS NULL OR acc_mt BETWEEN 1 AND 12),
    induty_code  text CHECK (induty_code IS NULL OR induty_code ~ '^[0-9]{1,6}$'),
    corp_cls     text CHECK (corp_cls IS NULL OR corp_cls IN ('Y', 'K', 'N', 'E')),
    est_dt       date,
    source       text NOT NULL DEFAULT 'DART' CHECK (source = 'DART'),
    quality      text NOT NULL DEFAULT 'ok' CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at  timestamptz NOT NULL,
    loaded_by    text NOT NULL CHECK (loaded_by <> '')
);
CREATE INDEX IF NOT EXISTS corp_profile_stock_idx
    ON pub_filings.corp_profile (stock_code) WHERE stock_code IS NOT NULL;

-- ── 잠정실적 (DART:document 원문 파싱 — §4.2) ───────────────────────────────────────────────
-- 금액은 원(본문 단위 × unit_mult). _cur 당기 · _prev_q 전기(직전 분기) · _prev_y 전년동기 — 세 열 다 남긴다.
-- fiscal_q 4 + is_annual 은 연간 잠정. period_inferred = 본문 기간 표기 없이 결산월로 추정(estimated).
-- yoy_reported = 본문 '전년동기대비%' {지표: %}. notes = 본문 값과 우리 계산이 다른 경우 등.
CREATE TABLE IF NOT EXISTS pub_filings.earnings_prelim (
    rcept_no         text PRIMARY KEY CHECK (rcept_no ~ '^[0-9]{14}$'),
    corp_code        text NOT NULL CHECK (corp_code ~ '^[0-9]{8}$'),
    rcept_dt         date NOT NULL,
    scope            text NOT NULL CHECK (scope IN ('consolidated', 'standalone', 'subsidiary')),
    fiscal_year      smallint NOT NULL CHECK (fiscal_year BETWEEN 1990 AND 2100),
    fiscal_q         smallint NOT NULL CHECK (fiscal_q BETWEEN 1 AND 4),
    is_annual        boolean NOT NULL DEFAULT false,
    period_end       date NOT NULL,
    period_inferred  boolean NOT NULL DEFAULT false,
    unit_mult        bigint NOT NULL CHECK (unit_mult IN (1, 1000, 1000000, 100000000)),
    revenue_cur      bigint,
    revenue_prev_q   bigint,
    revenue_prev_y   bigint,
    op_cur           bigint,
    op_prev_q        bigint,
    op_prev_y        bigint,
    pretax_cur       bigint,
    pretax_prev_q    bigint,
    pretax_prev_y    bigint,
    ni_cur           bigint,
    ni_prev_q        bigint,
    ni_prev_y        bigint,
    ni_owner_cur     bigint,
    ni_owner_prev_q  bigint,
    ni_owner_prev_y  bigint,
    eps_cur          numeric,
    eps_prev_y       numeric,
    yoy_reported     jsonb NOT NULL DEFAULT '{}',
    currency         text NOT NULL DEFAULT 'KRW' CHECK (currency ~ '^[A-Z]{3}$'),
    notes            jsonb NOT NULL DEFAULT '[]',
    source           text NOT NULL DEFAULT 'DART' CHECK (source = 'DART'),
    quality          text NOT NULL DEFAULT 'ok' CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at      timestamptz NOT NULL,
    loaded_by        text NOT NULL CHECK (loaded_by <> ''),
    CHECK (NOT is_annual OR fiscal_q = 4)
);
CREATE INDEX IF NOT EXISTS earnings_prelim_corp_idx
    ON pub_filings.earnings_prelim (corp_code, period_end DESC);
CREATE INDEX IF NOT EXISTS earnings_prelim_day_idx ON pub_filings.earnings_prelim (rcept_dt);

-- ── 실적 일정 (파생 — filings.derive) ───────────────────────────────────────────────────────
-- kind: pre_announce(결산실적공시예고 본문 예정일 — ok) · statutory(법정 제출기한 — estimated).
CREATE TABLE IF NOT EXISTS pub_filings.earnings_schedule (
    corp_code     text NOT NULL CHECK (corp_code ~ '^[0-9]{8}$'),
    period_end    date NOT NULL,
    kind          text NOT NULL CHECK (kind IN ('pre_announce', 'statutory')),
    fiscal_year   smallint NOT NULL CHECK (fiscal_year BETWEEN 1990 AND 2100),
    fiscal_q      smallint NOT NULL CHECK (fiscal_q BETWEEN 1 AND 4),
    planned_date  date NOT NULL,
    rcept_no      text CHECK (rcept_no IS NULL OR rcept_no ~ '^[0-9]{14}$'),
    note          text,
    source        text NOT NULL DEFAULT 'DART' CHECK (source = 'DART'),
    quality       text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    computed_at   timestamptz NOT NULL,
    loaded_by     text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (corp_code, period_end, kind),
    CHECK (kind <> 'statutory' OR quality <> 'ok')
);
CREATE INDEX IF NOT EXISTS earnings_schedule_day_idx ON pub_filings.earnings_schedule (planned_date);

-- ── 오버행 결정 (CB·BW·EB·유상·무상증자 — 구조화 API 우선, 원문 폴백) ───────────────────────────
-- face_value = 권면총액(원 — 증자는 NULL), price = 전환·행사·교환가액(유증은 발행가 — 없으면 NULL),
-- shares = 발행 결정 시 잠재·신주 수(EB 는 교환 대상 주식 — 신주 아님), refix_floor = 리픽싱 하한(표시만),
-- alloc_method = 배정 방식 원문(제3자배정 → 추정 보호예수 — D-P4-18). parse_source: api·document.
CREATE TABLE IF NOT EXISTS pub_filings.overhang_event (
    rcept_no        text PRIMARY KEY CHECK (rcept_no ~ '^[0-9]{14}$'),
    corp_code       text NOT NULL CHECK (corp_code ~ '^[0-9]{8}$'),
    rcept_dt        date NOT NULL,
    instrument      text NOT NULL CHECK (instrument IN ('cb', 'bw', 'eb', 'paid_in', 'bonus')),
    seq             text,
    face_value      bigint CHECK (face_value IS NULL OR face_value >= 0),
    price           numeric CHECK (price IS NULL OR price > 0),
    shares          bigint CHECK (shares IS NULL OR shares >= 0),
    refix_floor     numeric CHECK (refix_floor IS NULL OR refix_floor > 0),
    exercise_start  date,
    exercise_end    date,
    maturity        date,
    listing_date    date,
    alloc_method    text,
    parse_source    text NOT NULL CHECK (parse_source IN ('api', 'document')),
    notes           jsonb NOT NULL DEFAULT '[]',
    source          text NOT NULL DEFAULT 'DART' CHECK (source = 'DART'),
    quality         text NOT NULL DEFAULT 'ok' CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at     timestamptz NOT NULL,
    loaded_by       text NOT NULL CHECK (loaded_by <> ''),
    CHECK (exercise_start IS NULL OR exercise_end IS NULL OR exercise_start <= exercise_end)
);
CREATE INDEX IF NOT EXISTS overhang_event_corp_idx ON pub_filings.overhang_event (corp_code, rcept_dt);

-- ── 오버행 조정 (전환가액 조정·전환·행사·교환 청구·만기 전 상환 — 원문 파싱) ──────────────────────
-- target_rcept_no = 연결된 결정 공시. 연결 실패면 NULL + quality invalid + note(사유 — §4.3).
-- amount = 청구·상환 권면 금액(원), shares = 청구로 발행된 주식 수, new_price = 조정 뒤 가격.
CREATE TABLE IF NOT EXISTS pub_filings.overhang_adjust (
    rcept_no         text PRIMARY KEY CHECK (rcept_no ~ '^[0-9]{14}$'),
    corp_code        text NOT NULL CHECK (corp_code ~ '^[0-9]{8}$'),
    rcept_dt         date NOT NULL,
    target_rcept_no  text CHECK (target_rcept_no IS NULL OR target_rcept_no ~ '^[0-9]{14}$'),
    kind             text NOT NULL CHECK (kind IN ('price_adjust', 'exercise', 'redemption')),
    new_price        numeric CHECK (new_price IS NULL OR new_price > 0),
    amount           bigint CHECK (amount IS NULL OR amount >= 0),
    shares           bigint CHECK (shares IS NULL OR shares >= 0),
    effective_date   date,
    note             text,
    source           text NOT NULL DEFAULT 'DART' CHECK (source = 'DART'),
    quality          text NOT NULL DEFAULT 'ok' CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at      timestamptz NOT NULL,
    loaded_by        text NOT NULL CHECK (loaded_by <> ''),
    CHECK (target_rcept_no IS NOT NULL OR (quality = 'invalid' AND note IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS overhang_adjust_target_idx ON pub_filings.overhang_adjust (target_rcept_no);
CREATE INDEX IF NOT EXISTS overhang_adjust_corp_idx ON pub_filings.overhang_adjust (corp_code, rcept_dt);

-- ── 오버행 상태 (파생 — t 기준 결정 공시마다, §4.3) ────────────────────────────────────────────
-- remaining_face = 권면 − Σ 청구·상환(≤ t), potential_shares = 잔여 ÷ 현재 가격(버림, EB 는 교환 대상).
-- lockup_end_est = 추정 보호예수 해제일(제3자배정 신주 상장일 + 12개월 [확인 필요] — estimated).
CREATE TABLE IF NOT EXISTS pub_filings.overhang_state (
    corp_code         text NOT NULL CHECK (corp_code ~ '^[0-9]{8}$'),
    as_of             date NOT NULL,
    rcept_no          text NOT NULL CHECK (rcept_no ~ '^[0-9]{14}$'),
    instrument        text NOT NULL CHECK (instrument IN ('cb', 'bw', 'eb', 'paid_in', 'bonus')),
    remaining_face    bigint CHECK (remaining_face IS NULL OR remaining_face >= 0),
    price             numeric CHECK (price IS NULL OR price > 0),
    potential_shares  bigint CHECK (potential_shares IS NULL OR potential_shares >= 0),
    active            boolean NOT NULL,
    exercisable       boolean NOT NULL,
    lockup_end_est    date,
    notes             jsonb NOT NULL DEFAULT '[]',
    source            text NOT NULL DEFAULT 'DART' CHECK (source = 'DART'),
    quality           text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    computed_at       timestamptz NOT NULL,
    loaded_by         text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (corp_code, as_of, rcept_no),
    CHECK (active OR NOT exercisable)
);

-- ── 오버행 요약 (파생 — 회사·날짜, 공개 칸만) ─────────────────────────────────────────────────
-- 분모(공개) = 최근 share_count.common_issued(denom_source 'dart_share_count'). 없으면 비율 NULL.
CREATE TABLE IF NOT EXISTS pub_filings.overhang_summary (
    corp_code              text NOT NULL CHECK (corp_code ~ '^[0-9]{8}$'),
    as_of                  date NOT NULL,
    potential_new_shares   bigint NOT NULL CHECK (potential_new_shares >= 0),
    exercisable_shares     bigint NOT NULL CHECK (exercisable_shares >= 0),
    exchange_shares        bigint NOT NULL CHECK (exchange_shares >= 0),
    denominator            bigint CHECK (denominator IS NULL OR denominator > 0),
    denom_source           text,
    ratio_pct              numeric,
    exercisable_ratio_pct  numeric,
    notes                  jsonb NOT NULL DEFAULT '[]',
    source                 text NOT NULL DEFAULT 'DART' CHECK (source = 'DART'),
    quality                text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    computed_at            timestamptz NOT NULL,
    loaded_by              text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (corp_code, as_of),
    CHECK ((denominator IS NULL) = (denom_source IS NULL)),
    CHECK (denominator IS NOT NULL OR (ratio_pct IS NULL AND exercisable_ratio_pct IS NULL))
);

-- ── 임원·주요주주 소유보고 (DART:elestock — 회사의 보고 전체가 온다, 새 접수번호만) ───────────────
-- 보고자 이름은 저장하되 로그·알림에는 싣지 않는다 [제안]. reason = 원문 변동 사유(파싱 성공 때만).
CREATE TABLE IF NOT EXISTS pub_filings.insider_report (
    rcept_no      text PRIMARY KEY CHECK (rcept_no ~ '^[0-9]{14}$'),
    corp_code     text NOT NULL CHECK (corp_code ~ '^[0-9]{8}$'),
    rcept_dt      date NOT NULL,
    reporter      text NOT NULL CHECK (reporter <> ''),
    position      text,
    registered    boolean,
    major_holder  boolean,
    shares_after  bigint CHECK (shares_after IS NULL OR shares_after >= 0),
    shares_delta  bigint,
    ratio_after   numeric,
    ratio_delta   numeric,
    reason        text,
    source        text NOT NULL DEFAULT 'DART' CHECK (source = 'DART'),
    quality       text NOT NULL DEFAULT 'ok' CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at   timestamptz NOT NULL,
    loaded_by     text NOT NULL CHECK (loaded_by <> '')
);
CREATE INDEX IF NOT EXISTS insider_report_corp_idx ON pub_filings.insider_report (corp_code, rcept_dt);

-- ── 주식등의 대량보유상황보고 (DART:majorstock — 5%) ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pub_filings.major_holder_report (
    rcept_no      text PRIMARY KEY CHECK (rcept_no ~ '^[0-9]{14}$'),
    corp_code     text NOT NULL CHECK (corp_code ~ '^[0-9]{8}$'),
    rcept_dt      date NOT NULL,
    reporter      text NOT NULL CHECK (reporter <> ''),
    report_type   text,
    shares        bigint CHECK (shares IS NULL OR shares >= 0),
    shares_delta  bigint,
    ratio         numeric,
    ratio_delta   numeric,
    reason        text,
    source        text NOT NULL DEFAULT 'DART' CHECK (source = 'DART'),
    quality       text NOT NULL DEFAULT 'ok' CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at   timestamptz NOT NULL,
    loaded_by     text NOT NULL CHECK (loaded_by <> '')
);
CREATE INDEX IF NOT EXISTS major_holder_report_corp_idx
    ON pub_filings.major_holder_report (corp_code, rcept_dt);

-- ── 내부자 창 (파생 — 최근 30·90·180 달력일 보고 기준 순증감, §4.4) ───────────────────────────────
-- 화면 이름은 "보고 기준 순증감(사유 미구분 포함)". 금액(로그인)은 읽을 때 계산하고 여기 쓰지 않는다.
CREATE TABLE IF NOT EXISTS pub_filings.insider_window (
    corp_code         text NOT NULL CHECK (corp_code ~ '^[0-9]{8}$'),
    as_of             date NOT NULL,
    days              smallint NOT NULL CHECK (days IN (30, 90, 180)),
    net_shares        bigint NOT NULL,
    buy_shares        bigint NOT NULL CHECK (buy_shares >= 0),
    sell_shares       bigint NOT NULL CHECK (sell_shares >= 0),
    n_reports         integer NOT NULL CHECK (n_reports >= 0),
    n_reporters       integer NOT NULL CHECK (n_reporters >= 0),
    ratio_pct         numeric,
    reason_known_pct  numeric CHECK (reason_known_pct IS NULL OR reason_known_pct BETWEEN 0 AND 100),
    source            text NOT NULL DEFAULT 'DART' CHECK (source = 'DART'),
    quality           text NOT NULL CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    computed_at       timestamptz NOT NULL,
    loaded_by         text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (corp_code, as_of, days),
    CHECK (net_shares = buy_shares - sell_shares)
);

-- ── 자기주식 취득·처분 결정 (DART:tsstkAqDecsn·tsstkDpDecsn) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS pub_filings.buyback_event (
    rcept_no        text PRIMARY KEY CHECK (rcept_no ~ '^[0-9]{14}$'),
    corp_code       text NOT NULL CHECK (corp_code ~ '^[0-9]{8}$'),
    rcept_dt        date NOT NULL,
    kind            text NOT NULL CHECK (kind IN ('acquire', 'dispose', 'trust_in', 'trust_out')),
    shares_planned  bigint CHECK (shares_planned IS NULL OR shares_planned >= 0),
    amount_planned  bigint CHECK (amount_planned IS NULL OR amount_planned >= 0),
    start_date      date,
    end_date        date,
    method          text,
    source          text NOT NULL DEFAULT 'DART' CHECK (source = 'DART'),
    quality         text NOT NULL DEFAULT 'ok' CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at     timestamptz NOT NULL,
    loaded_by       text NOT NULL CHECK (loaded_by <> '')
);
CREATE INDEX IF NOT EXISTS buyback_event_corp_idx ON pub_filings.buyback_event (corp_code, rcept_dt);

-- ── 주식의 총수 현황 (DART:stockTotqySttus — 정기보고서, 오버행·내부자 비율·BPS 의 공개 분모) ──────
-- common_issued = 보통주 발행주식의 총수(자기주식 포함). [확인 필요 #33 — 필드]
CREATE TABLE IF NOT EXISTS pub_filings.share_count (
    corp_code        text NOT NULL CHECK (corp_code ~ '^[0-9]{8}$'),
    period_end       date NOT NULL,
    rcept_no         text NOT NULL CHECK (rcept_no ~ '^[0-9]{14}$'),
    rcept_dt         date,
    common_issued    bigint CHECK (common_issued IS NULL OR common_issued >= 0),
    pref_issued      bigint CHECK (pref_issued IS NULL OR pref_issued >= 0),
    treasury_common  bigint CHECK (treasury_common IS NULL OR treasury_common >= 0),
    treasury_pref    bigint CHECK (treasury_pref IS NULL OR treasury_pref >= 0),
    source           text NOT NULL DEFAULT 'DART' CHECK (source = 'DART'),
    quality          text NOT NULL DEFAULT 'ok' CHECK (quality IN ('ok', 'stale', 'estimated', 'invalid')),
    received_at      timestamptz NOT NULL,
    loaded_by        text NOT NULL CHECK (loaded_by <> ''),
    PRIMARY KEY (corp_code, period_end, rcept_no)
);

-- 공개 내보내기 역할은 0001 의 기본 권한으로도 읽지만(같은 소유자가 만든 표), 다른 역할이 이
-- 파일을 돌린 경우에도 읽히게 명시한다 — SELECT 만(0004 방식).
GRANT SELECT ON pub_filings.disclosure TO kbj_public_export;
GRANT SELECT ON pub_filings.corp_profile TO kbj_public_export;
GRANT SELECT ON pub_filings.earnings_prelim TO kbj_public_export;
GRANT SELECT ON pub_filings.earnings_schedule TO kbj_public_export;
GRANT SELECT ON pub_filings.overhang_event TO kbj_public_export;
GRANT SELECT ON pub_filings.overhang_adjust TO kbj_public_export;
GRANT SELECT ON pub_filings.overhang_state TO kbj_public_export;
GRANT SELECT ON pub_filings.overhang_summary TO kbj_public_export;
GRANT SELECT ON pub_filings.insider_report TO kbj_public_export;
GRANT SELECT ON pub_filings.major_holder_report TO kbj_public_export;
GRANT SELECT ON pub_filings.insider_window TO kbj_public_export;
GRANT SELECT ON pub_filings.buyback_event TO kbj_public_export;
GRANT SELECT ON pub_filings.share_count TO kbj_public_export;
