-- ── KIS 종목추정실적 EPS·비율 칸 단위 바로잡기(10배 — kbj/data/private/kis/consensus.py RATIO_SCALE) ──────
-- 2026-11-10 맥 실측: 저장된 EPS × 상장주식수 ÷ 순이익 중앙값 10.00(89종목), 저장된 PER × EPS ÷ 그날 종가
-- 중앙값 100(98종목). KIS 는 금액(억원) 아닌 칸(EPS·증감율·PER·EV/EBITDA·ROE·부채비율·이자보상배율)을
-- 10배로 준다('66610.0' 꼴). 그 전 파서는 그대로 읽었다 — EPS 10배, 그 EPS 로 만든 선행 PER 1/10.
-- 파서는 이제 RATIO_SCALE 로 나눈다. 마이그레이션은 새 코드의 수집보다 먼저 한 번 돈다(migrate 컨테이너 —
-- 0028 과 같은 전제) — 그때 있는 행은 모두 옛 파서가 쓴 것이다. 그대로 두면 새 스냅과 옛 기준선을 비교하는
-- EPS 리비전이 −90% 로 나와 리비전 알림이 잘못 간다.

-- 1) 스냅: EPS 와 extra 의 비율 칸(숫자일 때만)
UPDATE prv_fin.consensus_snapshot
   SET eps = eps / 10,
       extra = extra || COALESCE((
           SELECT jsonb_object_agg(k, to_jsonb(((extra ->> k)::numeric / 10)::float8))
             FROM unnest(ARRAY['revenue_growth_pct', 'op_growth_pct', 'ni_growth_pct',
                               'eps_growth_pct', 'per', 'ev_ebitda', 'roe_pct',
                               'debt_ratio_pct', 'interest_coverage']) AS k
            WHERE jsonb_typeof(extra -> k) = 'number'
       ), '{}'::jsonb)
 WHERE source = 'KIS';

-- 2) EPS 리비전의 현재·기준 값(변화율 pct 는 비율이라 그대로)
UPDATE prv_fin.revision
   SET current_value = current_value / 10,
       baseline = baseline / 10
 WHERE metric = 'eps';

-- 3) 선행 PER — NTM 순이익을 EPS × 주식 수로 만들었다(KIS 는 지배순익을 주지 않는다 — ni_owner 없음).
--    EPS 가 10배였으니 PER 은 1/10 이었다. 컨센서스 수집은 2026-10-26 부터다.
UPDATE prv_fin.valuation_daily
   SET per_fwd = per_fwd * 10
 WHERE per_fwd IS NOT NULL
   AND trade_date >= DATE '2026-10-01';
