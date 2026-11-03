-- ── 야후 환율 일봉 날짜 바로잡기(2026-11-03) ────────────────────────────────────────────────────
-- 야후 환율(KRW=X·JPY=X·EURUSD=X·CNY=X)의 일봉 시각은 런던 0시다. 수집(macro.morning_prv)이 그 시각을
-- UTC 날짜로 잘라, 영국 서머타임(BST — 3월 마지막 일요일 ~ 10월 마지막 일요일) 동안은 런던 0시 = 전날
-- 23:00 UTC 라 하루 앞 날짜에 적혔다(예: 7/15 값이 7/14 칸). 수집은 이제 거래소 현지 날짜로 적는다
-- (YahooClient local_dates). 여기서는 이미 적힌 행을 고친다: 그 날짜의 다음 날 런던 0시가 BST 였던 행만
-- 하루 뒤로 옮긴다. 겨울(GMT) 행은 원래 맞다. 옮기는 행끼리만 겹치므로(BST 시작·끝 경계에는 겹칠 맞는
-- 행이 없다) 지운 뒤 다시 넣는다. 한 번만 돈다(마이그레이션 기록) — 두 번 돌면 안 되는 고침이다.
CREATE TEMP TABLE fx_shift ON COMMIT DROP AS
SELECT *
FROM prv_macro.series
WHERE series_id IN ('YAHOO:KRW=X', 'YAHOO:JPY=X', 'YAHOO:EURUSD=X', 'YAHOO:CNY=X')
  AND source = 'YAHOO'
  AND period ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
  AND ((period::date + 1)::timestamp AT TIME ZONE 'Europe/London')
      <> ((period::date + 1)::timestamp AT TIME ZONE 'UTC');

DELETE FROM prv_macro.series s
USING fx_shift f
WHERE s.series_id = f.series_id AND s.period = f.period;

INSERT INTO prv_macro.series (series_id, period, value, prev_value, unit, source, as_of, released_at,
                              quality, received_at, loaded_by)
SELECT series_id, to_char(period::date + 1, 'YYYY-MM-DD'), value, prev_value, unit, source, as_of,
       released_at, quality, received_at, 'migration:0032'
FROM fx_shift
ON CONFLICT (series_id, period) DO NOTHING;
