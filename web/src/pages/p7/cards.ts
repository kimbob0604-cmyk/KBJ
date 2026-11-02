// 페이지 7 수출입 속보 요약 카드(2026-11-02 가시성 개선 — 주식봇식). 로그인 본문만 그린다(공개 번들에서 빠진다).
// 합계(전년 대비) + 금액이 큰 항목 4개(전년 대비·기여).
import type { StatCard } from '../../ui/stat';
import { type FlashRow, ratioPct, usdEok } from './flash';

const tone = (v: number | null): 'up' | 'down' | null => (v === null || v === 0 ? null : v > 0 ? 'up' : 'down');

export function flashCards(rows: readonly FlashRow[], label: string): StatCard[] {
  const tot = rows.find((r) => r.col_no === 0);
  const items = rows
    .filter((r) => r.col_no !== 0 && r.usd !== null)
    .sort((a, b) => (b.usd ?? 0) - (a.usd ?? 0))
    .slice(0, 4);
  return [
    {
      label: `${label} 합계`,
      value: usdEok(tot?.usd),
      tone: tone(tot?.yoy ?? null),
      zone: tot?.yoy === null || tot?.yoy === undefined ? null : tot.yoy >= 0 ? '증가' : '감소',
      sub: `전년 대비 ${ratioPct(tot?.yoy)}`,
      subTone: tone(tot?.yoy ?? null),
    },
    ...items.map(
      (r): StatCard => ({
        label: r.name,
        value: ratioPct(r.yoy),
        tone: tone(r.yoy),
        sub: `${usdEok(r.usd)}${r.contribution === null ? '' : ` · 기여 ${ratioPct(r.contribution)}p`}`,
        title: '전년 대비 증감률 · 금액 · 합계 증감률 기여',
      }),
    ),
  ];
}
