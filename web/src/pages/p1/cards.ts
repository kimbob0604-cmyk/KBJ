// 페이지 1 투자자 요약 카드(2026-11-02 가시성 개선 — 주식봇식). 로그인 본문만 쓴다(공개 번들에서 빠진다).
import type { components } from '../../api/types.gen';
import { joEok, shortDate } from '../../ui/fmt';
import type { StatCard } from '../../ui/stat';
import { INVESTOR_LABEL, INVESTORS, sumRecent } from '../p1_market';

type S = components['schemas'];

/** 투자자 요약 카드(주식봇식 — 그날 순매수 큰 숫자 + 5일 합, 부호 색). 값이 없으면 '—'와 사유 */
export function investorCards(d: S['InvestorTotals']): StatCard[] {
  return INVESTORS.map((k): StatCard => {
    const v = d.by_investor[k] ?? null;
    const r5 = sumRecent(d.recent, k, 5);
    const tone = (x: number | null) => (x === null || x === 0 ? null : x > 0 ? ('up' as const) : ('down' as const));
    return {
      label: INVESTOR_LABEL[k] ?? k,
      asof: shortDate(d.date),
      value: v === null ? null : joEok(v, true),
      tone: tone(v),
      zone: v === null ? null : v > 0 ? '순매수' : v < 0 ? '순매도' : null,
      sub: v === null ? '미제공' : r5 === null ? null : `5일 합 ${joEok(r5, true)}`,
      subTone: v === null ? 'dim' : tone(r5),
    };
  });
}
