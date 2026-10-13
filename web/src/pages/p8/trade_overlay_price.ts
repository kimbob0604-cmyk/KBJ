// 페이지 8 '수출 겹쳐 보기'의 로그인 한 줄(월말 종가 — 기존 'stock/<코드>/chart' 의 월말 값, docs/p6_design.md §4.6 — 묶음 MAP).
// **공개 빌드에서는 번들에서 뺀다**(p8_stock.ts LOGIN_VALUE 방식 — 고리는 WRAP). 공개판에서 불려도 자물쇠만(요청 0).
//
// - 수출 지수(월, 달러)와 월말 종가(원)를 같은 달 축에 두되 단위가 달라 둘 다 처음 겹친 달 = 100 으로 맞춘다.
// - 새 API 경로 없음 — 일봉 차트(최대 1,240일 — 약 5년)에서 달마다 마지막 거래일 종가만 쓴다(비수정 주가).
import type { Envelope } from '../../data/source';
import { num } from '../../ui/fmt';
import { lineChart } from '../../ui/line';
import { isNotFound, mkPanel, type S, tabGrid, type TabCtx } from './common';
import { notMappedText, stockKey, type TradeStock } from './trade_overlay';

export const TITLE = '수출 겹쳐 보기 — 월말 종가';
export const CHART_DAYS = 1240;
type Chart = S['PriceChart'];

/** 일봉 → {YYYY-MM: 그 달 마지막 종가} */
export function monthEnd(bars: Chart['bars']): Map<string, number> {
  const out = new Map<string, number>();
  for (const b of [...bars].sort((a, b2) => a.date.localeCompare(b2.date))) {
    if (typeof b.c === 'number' && Number.isFinite(b.c)) out.set(b.date.slice(0, 7), b.c);
  }
  return out;
}

/** 두 줄을 처음 같이 있는 달 = 100 으로. 그 전 달·빈 달은 null */
export function indexed(
  months: readonly string[],
  a: ReadonlyMap<string, number>,
  b: ReadonlyMap<string, number>,
): { x: string[]; a: (number | null)[]; b: (number | null)[] } {
  const base = months.find((m) => (a.get(m) ?? 0) > 0 && (b.get(m) ?? 0) > 0);
  if (base === undefined) return { x: [], a: [], b: [] };
  const a0 = a.get(base) ?? 1;
  const b0 = b.get(base) ?? 1;
  const x = months.filter((m) => m >= base);
  const f = (m: ReadonlyMap<string, number>, v0: number) => (k: string) => {
    const v = m.get(k);
    return v === undefined ? null : (v / v0) * 100;
  };
  return { x, a: x.map(f(a, a0)), b: x.map(f(b, b0)) };
}

export async function mount(t: TabCtx, el: HTMLElement): Promise<void> {
  const p = mkPanel(t, {
    title: TITLE,
    tier: 'login',
    span: 12,
    lock: 'quote_based',
  });
  tabGrid(el, p);
  if (p.locked) return;
  let trade: Envelope<TradeStock>;
  try {
    trade = await t.get<TradeStock>(stockKey(t.code));
  } catch (err) {
    if (isNotFound(err)) p.showMessage(notMappedText(err));
    else p.showError(err);
    return;
  }
  try {
    const chart = await t.get<Chart>(`stock/${t.code}/chart`, {
      days: CHART_DAYS,
    });
    const exp = new Map<string, number>();
    for (const m of trade.data.monthly) if (typeof m.export_usd === 'number') exp.set(m.period, m.export_usd);
    const px = monthEnd(chart.data.bars);
    const months = [...new Set([...exp.keys(), ...px.keys()])].sort();
    const ix = indexed(months, exp, px);
    p.clearBody();
    if (!ix.x.length) {
      p.showMessage('겹치는 달 없음 — 수출 지수와 종가가 같이 있는 달이 없다');
      return;
    }
    p.body.append(
      lineChart({
        label: `${trade.data.name} 수출 지수 대 월말 종가(처음 겹친 달 = 100)`,
        x: ix.x,
        series: [
          { label: '수출 지수', values: ix.a },
          { label: '월말 종가', values: ix.b },
        ],
        format: (v) => num(v, 0),
      }).el,
    );
    p.setSource({
      source: `${trade.source}+KRX`,
      as_of: chart.as_of > trade.as_of ? chart.as_of : trade.as_of,
      quality: chart.quality === 'ok' ? trade.quality : chart.quality,
      notes: ['종가는 비수정(액면분할 등 미조정)', '처음 겹친 달 = 100 으로 맞춘 지수'],
    });
  } catch (err) {
    p.showError(err);
  }
}
