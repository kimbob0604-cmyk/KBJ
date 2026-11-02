// 페이지 2 신고가 요약 카드(2026-11-02 가시성 개선 — 주식봇식). 로그인 본문만 쓴다(공개 번들에서 빠진다).
import type { components } from '../../api/types.gen';
import { num, pct } from '../../ui/fmt';
import type { StatCard } from '../../ui/stat';
import { type BoardRow, DEFAULT_LABELS } from '../p2_board';

type Newhigh = components['schemas']['BoardNewhigh'];
type Basis = 'close' | 'high';

/** 신고가 요약 카드(주식봇식) — 라벨별 종목 수 + 거래대금 1위 종목 */
export function newhighCards(d: Newhigh, basis: Basis, achieved: readonly BoardRow[]): StatCard[] {
  const labels = d.labels ?? DEFAULT_LABELS;
  const counts = (basis === 'high' ? d.counts_high : d.counts_close) ?? {};
  const order = d.priority?.length ? d.priority : Object.keys(labels);
  const cards: StatCard[] = order.map((k) => {
    const c = counts[k];
    return {
      label: `${labels[k] ?? k} 신고가`,
      value: c === undefined ? null : `${num(c)}종목`,
      tone: c ? 'up' : null,
      sub: basis === 'high' ? '고가 기준' : '종가 기준',
    };
  });
  const top = [...achieved].sort((a, b) => (b.turnover ?? 0) - (a.turnover ?? 0))[0];
  cards.push({
    label: '거래대금 1위',
    value: top ? (top.name ?? top.code) : null,
    tone: top && (top.chg_pct ?? 0) > 0 ? 'up' : top && (top.chg_pct ?? 0) < 0 ? 'down' : null,
    sub: top ? `${pct(top.chg_pct ?? null)} · ${num(top.turnover ?? null)}억` : null,
  });
  return cards;
}
