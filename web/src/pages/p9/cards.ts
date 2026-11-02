// 페이지 9 테마 강도 요약 카드(2026-11-02 가시성 개선 — 주식봇식). 로그인 본문만 그린다(공개 번들에서 빠진다).
// 강한 대테마 3개 + 가장 약한 대테마 1개(기간 등락률 — 시총 가중이면 가중 평균).
import type { components } from '../../api/types.gen';
import { num, pct } from '../../ui/fmt';
import type { StatCard } from '../../ui/stat';
import { type Period, periodValue } from './strength';

type Row = components['schemas']['ThemeStrengthRow'];

export function strengthCards(rows: readonly Row[], period: Period): StatCard[] {
  const val = (r: Row): number | null => periodValue(r.stats, period);
  const ok = rows.filter((r) => val(r) !== null);
  const sorted = [...ok].sort((a, b) => (val(b) ?? 0) - (val(a) ?? 0));
  const card = (r: Row, label: string): StatCard => {
    const v = val(r);
    return {
      label,
      value: r.name,
      tone: v === null || v === 0 ? null : v > 0 ? 'up' : 'down',
      sub: `${pct(v)} · 상승 ${num(r.stats.up)}/${num(r.stats.n_priced)}${r.stats.n_newhigh ? ` · 신고가 ${num(r.stats.n_newhigh)}` : ''}${r.stats.leader ? ` · 대장 ${r.stats.leader.name}` : ''}`,
    };
  };
  const out = sorted.slice(0, 3).map((r, i) => card(r, `강한 테마 ${i + 1}위 · ${period}일`));
  const weakest = sorted.at(-1);
  if (weakest && sorted.length > 3) out.push(card(weakest, `가장 약한 테마 · ${period}일`));
  return out;
}
