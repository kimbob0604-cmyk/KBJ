// 페이지 7 '산업' 탭(P10 단계 4 — docs/p10_trade_stocks_plan.md). 산업 → 종목 → 추적 품목.
// `/api/trade/sectors`: 매핑표 종목의 산업마다 종목 수출 지수(매핑 가중) 합·YoY·3개월 YoY·24개월 스파크라인.
// 행을 고르면 그 산업의 종목 표 + 추적 품목(HS). 로그인 전용(공개판은 자물쇠). 금액은 달러(원화 환산 없음).
import type { components } from '../../api/types.gen';
import { toCsv } from '../../ui/csv';
import { h, replace } from '../../ui/dom';
import { signClass } from '../../ui/fmt';
import { panel } from '../../ui/panel';
import { sparkline } from '../../ui/svg';
import { type Column, table } from '../../ui/table';
import { csvMeta, failNote, ratio, type TabCtx, tableHead, TRADE_HINT, TRADE_SOURCE, usd } from './common';

export type TradeSectors = components['schemas']['TradeSectors'];
type Sector = TradeSectors['rows'][number];
type SectorStock = Sector['stocks'][number];

export const TITLE = '산업별 수출';
export const DETAIL_TITLE = '산업 구성';

const COLUMNS: readonly Column<Sector>[] = [
  { key: 'sector', label: '산업', align: 'l', cell: (r) => r.sector },
  { key: 'n', label: '종목', cell: (r) => String(r.n_stocks) },
  { key: 'usd', label: '최신월', cell: (r) => usd(r.usd) },
  { key: 'yoy', label: 'YoY', cell: (r) => ratio(r.yoy), cls: (r) => signClass(r.yoy) },
  { key: 'yoy3', label: '3개월 YoY', cell: (r) => ratio(r.yoy_3m), cls: (r) => signClass(r.yoy_3m) },
  {
    key: 'spark',
    label: '24개월',
    cell: (r) => sparkline(r.spark, { label: `${r.sector} 24개월 수출 지수`, w: 96, h: 20 }),
  },
];

const STOCK_COLUMNS: readonly Column<SectorStock>[] = [
  { key: 'name', label: '종목', align: 'l', cell: (r) => `${r.name} (${r.code})` },
  { key: 'usd', label: '최신월 지수', cell: (r) => usd(r.usd) },
  { key: 'yoy3', label: '3개월 YoY', cell: (r) => ratio(r.yoy_3m), cls: (r) => signClass(r.yoy_3m) },
];

export function sectorsCsv(rows: readonly Sector[], ym: string): string {
  return toCsv(
    rows,
    [
      { label: '산업', value: 'sector' },
      { label: '종목 수', value: 'n_stocks' },
      { label: '최신월 지수 합(달러)', value: 'usd' },
      { label: 'YoY', value: 'yoy' },
      { label: '3개월 YoY', value: 'yoy_3m' },
    ],
    { meta: csvMeta(ym) },
  );
}

/** 산업 구성 — 종목 표 + 추적 품목 칩 */
export function sectorDetail(s: Sector): HTMLElement {
  return h(
    'div',
    null,
    h('h5', null, `${s.sector} · 종목 ${String(s.n_stocks)}개`),
    table<SectorStock>({ caption: `${s.sector} 종목`, columns: STOCK_COLUMNS, rows: s.stocks, rowKey: (r) => r.code })
      .el,
    h('h5', null, `추적 품목 ${String(s.products.length)}개`),
    h(
      'div',
      { class: 'theme-chips' },
      ...s.products.map((p) => h('span', { class: 'tag dim' }, `${p.name ?? ''} (${p.hs})`)),
    ),
  );
}

export async function mount(t: TabCtx, el: HTMLElement): Promise<void> {
  const p = panel(t.tier, { title: TITLE, tier: 'login', span: 12, src: TRADE_SOURCE });
  p.setDef(
    '매핑 종목의 산업별 수출 지수 합(가중 — 통관 합이 아니다). YoY 는 두 기간 모두 값이 있는 종목만. 행을 고르면 구성.',
  );
  const dp = panel(t.tier, { title: DETAIL_TITLE, tier: 'login', span: 12, src: TRADE_SOURCE });
  el.append(h('div', { class: 'grid' }, p.el, dp.el));
  if (p.locked) return;
  dp.showMessage('위 표에서 산업을 고르면 그린다');
  try {
    const env = await t.source.get<TradeSectors>('trade/sectors');
    p.setSource(env);
    dp.setSource(env);
    const d = env.data;
    p.clearBody();
    p.body.append(
      tableHead(`산업 ${String(d.rows.length)}개 · ${d.last_ym}`, `trade_sectors_${d.last_ym}`, () =>
        sectorsCsv(d.rows, d.last_ym),
      ),
      table<Sector>({
        caption: `산업별 수출 ${d.last_ym}`,
        columns: COLUMNS,
        rows: d.rows,
        rowKey: (r) => r.sector,
        onSelect: (r) => replace(dp.body, sectorDetail(r)),
      }).el,
    );
  } catch (err) {
    p.clearBody();
    p.body.append(failNote(err, TRADE_HINT.stocks));
  }
}
