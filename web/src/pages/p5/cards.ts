// 페이지 5 공시 피드 요약 카드(2026-11-02 가시성 개선 — 주식봇식). 로그인 본문만 그린다(공개 번들에서 빠진다).
import type { StatCard } from '../../ui/stat';
import { num } from '../../ui/fmt';
import { kindLabel } from '../p8/common';
import { type FeedRow, levelOf } from './feed';

export function feedCards(rows: readonly FeedRow[], day: string | null): StatCard[] {
  const asof = day ? `${day.slice(5, 7)}/${day.slice(8, 10)}` : null;
  const important = rows.filter((r) => ['critical', 'high'].includes(levelOf(r)));
  const watch = rows.filter((r) => r.is_watch);
  const counts = new Map<string, number>();
  for (const r of rows) counts.set(r.kind, (counts.get(r.kind) ?? 0) + 1);
  const top = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 2);
  return [
    {
      label: '공시',
      asof,
      value: `${num(rows.length)}건`,
      sub: `정정 ${num(rows.filter((r) => r.is_correction).length)}건`,
    },
    {
      label: '중요 이상',
      asof,
      value: `${num(important.length)}건`,
      tone: important.length ? 'warn' : null,
      sub: important[0] ? `${important[0].corp_name} · ${important[0].title.slice(0, 18)}` : null,
    },
    {
      label: '관심종목',
      asof,
      value: `${num(watch.length)}건`,
      tone: null,
      sub: watch[0] ? `${watch[0].corp_name} · ${watch[0].title.slice(0, 18)}` : null,
    },
    ...top.map(([k, n]): StatCard => ({ label: kindLabel(k), asof, value: `${num(n)}건`, sub: '많은 유형' })),
  ];
}
