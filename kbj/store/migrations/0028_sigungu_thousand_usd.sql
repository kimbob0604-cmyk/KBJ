-- ── 시군구 금액 단위 바로잡기(관세청 15134343 = 천 달러 — docs/probe_results.md §2.5) ─────────────────
-- 2026-10-26 맥 실측: 한 HS6 의 전 시도 시군구 합 ÷ 품목별(15101609) 전국 합 = 0.001. 그 전 클라이언트는
-- 천 달러를 달러로 저장했다(쌓인 행이 1/1000). 클라이언트는 이제 ×1000 해 달러로 쓴다(SIGUNGU_USD_PER_UNIT).
-- 마이그레이션은 새 코드의 수집보다 먼저 한 번 돈다(migrate 컨테이너) — 그때 있는 행은 모두 옛 코드가 쓴 것이다.
UPDATE pub_trade.sigungu_monthly
   SET exp_usd = exp_usd * 1000,
       imp_usd = imp_usd * 1000,
       bal_usd = bal_usd * 1000;
