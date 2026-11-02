// 페이지 10 ETF 수급 요약 카드(2026-11-02 가시성 개선 — 주식봇식). 로그인 본문만 쓴다(공개 번들에서 빠진다).
import type { components } from '../../api/types.gen';
import { joEok, num, pct, shortDate } from '../../ui/fmt';
import type { StatCard } from '../../ui/stat';

type S = components['schemas'];
type Flows = S['EtfFlows'];
type FlowRow = S['EtfFlowRow'];
type StockFlows = S['EtfStockFlows'];
type StockRow = S['StockEtfFlowRow'];

const sumOf = <T>(rows: readonly T[], get: (r: T) => number | null): number | null => {
  const vals = rows.map(get).filter((v): v is number => v !== null);
  return vals.length ? vals.reduce((a, b) => a + b, 0) : null;
};
const toneOf = (v: number | null): 'up' | 'down' | null => (v === null || v === 0 ? null : v > 0 ? 'up' : 'down');

/** ETF 수급 요약 카드(주식봇식) — 표에 보이는 ETF 합·가장 많이 들어온/나간 ETF. 검산 실패 행은 이미 뺐다 */
export function flowCards(rows: readonly FlowRow[], d: Pick<Flows, 'start' | 'end'>): StatCard[] {
  const asof = `${shortDate(d.start)}~${shortDate(d.end)}`;
  const total = sumOf(rows, (r) => r.net_inflow);
  const withIn = rows.filter((r) => r.net_inflow !== null);
  const top = withIn.reduce<FlowRow | null>(
    (a, r) => (a === null || (r.net_inflow ?? 0) > (a.net_inflow ?? 0) ? r : a),
    null,
  );
  const bottom = withIn.reduce<FlowRow | null>(
    (a, r) => (a === null || (r.net_inflow ?? 0) < (a.net_inflow ?? 0) ? r : a),
    null,
  );
  const who = (k: 'indiv' | 'foreign' | 'inst', label: string): StatCard => {
    const v = sumOf(rows, (r) => r[k]);
    return { label: `${label} 순매수`, asof, value: v === null ? null : joEok(v, true), tone: toneOf(v) };
  };
  return [
    {
      label: `순유입 합(${num(rows.length)}개)`,
      asof,
      value: total === null ? null : joEok(total, true),
      tone: toneOf(total),
      zone: total === null ? null : total >= 0 ? '자금 유입' : '자금 유출',
      title: '표에 보이는 ETF 의 기간 순유입(설정 − 환매 추정) 합',
    },
    {
      label: '최대 유입',
      asof,
      value: top && (top.net_inflow ?? 0) > 0 ? joEok(top.net_inflow, true) : null,
      tone: 'up',
      sub: top && (top.net_inflow ?? 0) > 0 ? top.name : null,
    },
    {
      label: '최대 유출',
      asof,
      value: bottom && (bottom.net_inflow ?? 0) < 0 ? joEok(bottom.net_inflow, true) : null,
      tone: 'down',
      sub: bottom && (bottom.net_inflow ?? 0) < 0 ? bottom.name : null,
    },
    who('indiv', '개인'),
    who('foreign', '외국인'),
    who('inst', '기관'),
  ];
}

/** 종목별 ETF 수급 요약 카드 — ETF 를 통해 가장 많이 들어간/빠진 종목 */
export function stockFlowCards(d: StockFlows): StatCard[] {
  const asof = `${shortDate(d.start)}~${shortDate(d.end)}`;
  const withIn = d.rows.filter((r) => r.inflow !== null);
  const top = [...withIn].sort((a, b) => (b.inflow ?? 0) - (a.inflow ?? 0))[0];
  const bottom = [...withIn].sort((a, b) => (a.inflow ?? 0) - (b.inflow ?? 0))[0];
  const card = (label: string, r: StockRow | undefined, sign: 1 | -1): StatCard => {
    const ok = r !== undefined && (r.inflow ?? 0) * sign > 0;
    return {
      label,
      asof,
      value: ok ? joEok(r.inflow, true) : null,
      tone: sign > 0 ? 'up' : 'down',
      sub: ok ? `${r.name ?? r.code} · 거래대금 대비 ${pct(r.inflow_pct)}` : null,
    };
  };
  return [
    card('ETF 통해 가장 많이 산 종목', top, 1),
    card('ETF 통해 가장 많이 판 종목', bottom, -1),
    {
      label: '덮은 비율',
      asof,
      value: d.coverage_pct === null ? null : pct(d.coverage_pct, 0, false),
      sub: `ETF ${num(d.n_etfs_known)}개 구성종목 · ${num(d.n_total)}종목`,
      title: '전체 ETF 순유입 중 구성종목을 아는 ETF 가 차지하는 몫',
    },
  ];
}
