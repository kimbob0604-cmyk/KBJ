-- 관세청 국가 코드에 숫자가 섞인 특수 코드가 있다(예: 'Z1' — 실측 2026-10-20, 15101612·15100475 월간).
-- 영문 두 글자만 받아 9월 월간 국가·품목×국가 저장이 통째로 실패했다 — 영문·숫자 두 글자로 넓힌다.
ALTER TABLE pub_trade.country_monthly DROP CONSTRAINT IF EXISTS country_monthly_country_check;
ALTER TABLE pub_trade.country_monthly
    ADD CONSTRAINT country_monthly_country_check CHECK (country ~ '^[A-Z0-9]{2}$');
ALTER TABLE pub_trade.item_country_monthly DROP CONSTRAINT IF EXISTS item_country_monthly_country_check;
ALTER TABLE pub_trade.item_country_monthly
    ADD CONSTRAINT item_country_monthly_country_check CHECK (country ~ '^[A-Z0-9]{2}$');
