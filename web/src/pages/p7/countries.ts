// 페이지 7 '국가' 탭(EXP — docs/p6_design.md §1.4·§6, metrics §26). 국가 총괄(마지막 달 수출·수입·수지·전년 대비·비중) +
// 고른 국가의 수출 품목(HS4). 로그인: 'trade/countries'·'trade/countries/<ISO2>/items'(API), 공개: 'trade/countries'·
// 'trade/country/<ISO2>'(같은 모양의 공개 파일). 금액은 달러(원화 환산 없음). 실패는 탭 안 한 줄.
// 로그인은 품목 추이(P10 단계 4 — 'trade/countries/<ISO2>/trend': 전월·전년 대비·24개월·관련 종목)를 아래에 더 그린다.
import type { components } from '../../api/types.gen';
import type { Envelope } from '../../data/source';
import { toCsv } from '../../ui/csv';
import { h, replace } from '../../ui/dom';
import { signClass } from '../../ui/fmt';
import { sparkline } from '../../ui/svg';
import { type Column, table } from '../../ui/table';
import {
  failNote,
  csvMeta,
  ratio,
  type TabCtx,
  tableHead,
  TRADE_HINT,
  type TradeCountries,
  type TradeCountryItems,
  type TradeItemNode,
  tradePanel,
  usd,
} from './common';

export const TITLE = '국가';
export const ITEMS_TITLE = '국가별 수출 품목';
export const TREND_MAX_STOCKS = 3;

export type TradeCountryTrend = components['schemas']['TradeCountryTrend'];
type TrendRow = TradeCountryTrend['rows'][number];

/** 관련 종목 글자 — 앞 TREND_MAX_STOCKS 개 + '외 N' */
export function relatedText(r: TrendRow): string {
  const head = r.stocks.slice(0, TREND_MAX_STOCKS).map((s) => s.name);
  const more = r.stocks.length - head.length;
  return head.length ? `${head.join(', ')}${more > 0 ? ` 외 ${String(more)}` : ''}` : '—';
}

function trendColumns(names: ReadonlyMap<string, string | null>): Column<TrendRow>[] {
  return [
    { key: 'hs', label: '품목', align: 'l', cell: (r) => `${names.get(r.hs) ?? ''} (${r.hs})` },
    { key: 'usd', label: '금액', cell: (r) => usd(r.usd) },
    { key: 'mom', label: '전월 대비', cell: (r) => ratio(r.mom), cls: (r) => signClass(r.mom) },
    { key: 'yoy', label: '전년 대비', cell: (r) => ratio(r.yoy), cls: (r) => signClass(r.yoy) },
    {
      key: 'spark',
      label: '24개월',
      cell: (r) => sparkline(r.spark, { label: `${r.hs} 24개월 수출`, w: 96, h: 20 }),
    },
    { key: 'stocks', label: '관련 종목', align: 'l', cell: (r) => relatedText(r) },
  ];
}

type Row = TradeCountries['rows'][number];

const COLUMNS: readonly Column<Row>[] = [
  {
    key: 'cc',
    label: '국가',
    align: 'l',
    cell: (r) => `${r.country_name} (${r.country})`,
  },
  { key: 'exp', label: '수출', cell: (r) => usd(r.exp_usd) },
  {
    key: 'exp_yoy',
    label: '수출 전년 대비',
    cell: (r) => ratio(r.exp_yoy),
    cls: (r) => signClass(r.exp_yoy),
  },
  {
    key: 'share',
    label: '수출 비중',
    cell: (r) => ratio(r.exp_share).replace('+', ''),
  },
  { key: 'imp', label: '수입', cell: (r) => usd(r.imp_usd) },
  {
    key: 'bal',
    label: '수지',
    cell: (r) => usd(r.bal_usd),
    cls: (r) => signClass(r.bal_usd),
  },
];

const ITEM_COLUMNS: readonly Column<TradeItemNode>[] = [
  { key: 'hs', label: 'HS', align: 'l', cell: (r) => r.hs },
  { key: 'name', label: '품목', align: 'l', cell: (r) => r.name },
  { key: 'usd', label: '금액', cell: (r) => usd(r.usd) },
  {
    key: 'yoy',
    label: '전년 대비',
    cell: (r) => ratio(r.yoy),
    cls: (r) => signClass(r.yoy),
  },
  { key: 'share', label: '비중', cell: (r) => ratio(r.share).replace('+', '') },
];

export function countriesCsv(rows: readonly Row[], ym: string): string {
  return toCsv(
    rows,
    [
      { label: '국가', value: 'country' },
      { label: '국가 이름', value: 'country_name' },
      { label: '수출(달러)', value: 'exp_usd' },
      { label: '수출 전년 대비', value: 'exp_yoy' },
      { label: '수출 비중', value: 'exp_share' },
      { label: '수입(달러)', value: 'imp_usd' },
      { label: '수입 전년 대비', value: 'imp_yoy' },
      { label: '수지(달러)', value: 'bal_usd' },
    ],
    { meta: csvMeta(ym) },
  );
}

function countryItems(t: TabCtx, cc: string): Promise<Envelope<TradeCountryItems>> {
  return t.tier === 'login'
    ? t.source.get<TradeCountryItems>(`trade/countries/${cc}/items`, {
        flow: 'exp',
      })
    : t.source.get<TradeCountryItems>(`trade/country/${cc}`);
}

export async function mount(t: TabCtx, el: HTMLElement): Promise<void> {
  const p = tradePanel(t, { title: TITLE });
  p.setDef('마지막 달 국가별 수출·수입. 비중 = 국가 수출 ÷ 국가 합. 행을 고르면 그 국가의 수출 품목(HS4).');
  const ip = tradePanel(t, { title: ITEMS_TITLE });
  ip.showMessage('위 표에서 국가를 고르면 그린다');
  el.append(h('div', { class: 'grid' }, p.el, ip.el));
  const pick = async (r: Row): Promise<void> => {
    const trend =
      t.tier === 'login'
        ? t.source.get<TradeCountryTrend>(`trade/countries/${r.country}/trend`).catch(() => null)
        : Promise.resolve(null);
    try {
      const env = await countryItems(t, r.country);
      ip.setSource(env);
      const d = env.data;
      replace(
        ip.body,
        tableHead(`${d.country_name ?? d.country} · ${d.ym}`, `trade_country_${d.country}_${d.ym}`, () =>
          toCsv(
            d.rows,
            [
              { label: 'HS', value: 'hs' },
              { label: '품목', value: 'name' },
              { label: '금액(달러)', value: 'usd' },
              { label: '전년 대비', value: 'yoy' },
              { label: '비중', value: 'share' },
            ],
            { meta: csvMeta(d.ym) },
          ),
        ),
        table<TradeItemNode>({
          caption: `${d.country} 수출 품목`,
          columns: ITEM_COLUMNS,
          rows: d.rows,
          rowKey: (x) => x.hs,
        }).el,
      );
      const tr = await trend;
      if (tr) {
        const names = new Map(d.rows.map((x) => [x.hs, x.name] as const));
        ip.body.append(
          h('h5', null, `품목 추이 · 24개월 (${tr.data.months[0] ?? ''}~${tr.data.ym})`),
          table<TrendRow>({
            caption: `${d.country} 수출 품목 추이`,
            columns: trendColumns(names),
            rows: tr.data.rows,
            rowKey: (x) => x.hs,
            pageSize: 20,
          }).el,
        );
      }
    } catch (err) {
      ip.showError(err);
    }
  };
  try {
    const env = await t.source.get<TradeCountries>('trade/countries');
    p.setSource(env);
    const d = env.data;
    p.body.append(
      tableHead(`국가 총괄 · ${d.ym}`, `trade_countries_${d.ym}`, () => countriesCsv(d.rows, d.ym)),
      table<Row>({
        caption: `국가 총괄 ${d.ym}`,
        columns: COLUMNS,
        rows: d.rows,
        rowKey: (r) => r.country,
        onSelect: (r) => void pick(r),
      }).el,
    );
  } catch (err) {
    p.body.append(failNote(err, TRADE_HINT.monthly));
  }
}
