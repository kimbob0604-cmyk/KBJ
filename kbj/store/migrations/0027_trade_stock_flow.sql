-- ── 종목 매핑 '흐름'(P10 — docs/p10_trade_stocks_plan.md 단계 1) ─────────────────────────────────
-- 매핑 줄마다 그 줄이 속한 흐름(나쁜양파 '종목별 흐름'과 같은 단위 — 국가·시군구·품목 묶음·상대국·구분·근거).
-- flow_id = 종목 안에서 유일한 흐름 id('' = 판 1 적재분), flow_meta = {name, kind, direction, nature, partners,
-- regions, products[{hs, name}], basis}. 같은 흐름의 줄은 같은 meta 를 갖는다(매핑표 해석기가 만든다).
ALTER TABLE pub_trade.stock_map ADD COLUMN IF NOT EXISTS flow_id text NOT NULL DEFAULT '';
ALTER TABLE pub_trade.stock_map ADD COLUMN IF NOT EXISTS flow_meta jsonb NOT NULL DEFAULT '{}'::jsonb;
