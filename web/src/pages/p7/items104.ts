// 페이지 7 '104품목' 탭(EXP — docs/p6_design.md §1.4·§4.4, D-P6-9, metrics §29). 자체 고정 HS4 묶음
// (config/knowledge/trade_items.yaml — 사용자 결정 2026-10-13) 마지막 달 수출 — 트리맵(크기 = 금액, 색 = 전년 대비)·표·CSV.
// 묶음 파일이 비어 엔진이 상위 HS4 를 고른 달은 '자동 선택' 꼬리표. 로그인: 'trade/items104'(API), 공개: 같은 이름 파일.
import { toCsv } from '../../ui/csv';
import { h } from '../../ui/dom';
import { pct, signClass } from '../../ui/fmt';
import { type Column, table } from '../../ui/table';
import { treemap } from '../../ui/treemap';
import {
  failNote,
  csvMeta,
  perKg,
  ratio,
  type TabCtx,
  tableHead,
  TRADE_HINT,
  type TradeItems104,
  tradePanel,
  usd,
} from './common';

export const TITLE = '104품목';
export const AUTO_TAG = '자동 선택';

type Row = TradeItems104['rows'][number];

const COLUMNS: readonly Column<Row>[] = [
  { key: 'name', label: '품목', align: 'l', cell: (r) => r.name },
  { key: 'usd', label: '수출', cell: (r) => usd(r.exp_usd) },
  {
    key: 'yoy',
    label: '전년 대비',
    cell: (r) => ratio(r.yoy),
    cls: (r) => signClass(r.yoy),
  },
  { key: 'share', label: '비중', cell: (r) => ratio(r.share).replace('+', '') },
  { key: 'price', label: '단가', cell: (r) => perKg(r.unit_price) },
];

export function items104Csv(rows: readonly Row[], ym: string): string {
  return toCsv(
    rows,
    [
      { label: '묶음', value: 'group_id' },
      { label: '품목', value: 'name' },
      { label: '수출(달러)', value: 'exp_usd' },
      { label: '전년 대비', value: 'yoy' },
      { label: '비중', value: 'share' },
      { label: '단가(달러/kg)', value: 'unit_price' },
    ],
    { meta: csvMeta(ym) },
  );
}

export async function mount(t: TabCtx, el: HTMLElement): Promise<void> {
  const p = tradePanel(t, { title: TITLE });
  p.setDef('자체 고정 HS4 목록의 마지막 달 수출. 비중 = 104품목 합 대비. 원화 환산 없음.');
  el.append(h('div', { class: 'grid' }, p.el));
  try {
    const env = await t.source.get<TradeItems104>('trade/items104');
    p.setSource(env);
    const d = env.data;
    if (d.auto_selected) {
      p.body.append(
        h(
          'p',
          { class: 'def' },
          h('span', { class: 'tag' }, AUTO_TAG),
          ' 직전 12개월 수출 상위 HS4 — 매달 바뀔 수 있다',
        ),
      );
    }
    p.body.append(
      treemap({
        label: '104품목 트리맵',
        items: d.rows.map((r) => ({
          id: r.group_id,
          label: r.name,
          size: r.exp_usd,
          color: r.yoy === null ? null : r.yoy * 100,
        })),
        formatSize: usd,
        formatColor: (x) => pct(x, 1, true),
      }),
      tableHead(`${d.ym} · ${String(d.rows.length)}개`, `trade_items104_${d.ym}`, () => items104Csv(d.rows, d.ym)),
      table<Row>({
        caption: `104품목 ${d.ym}`,
        columns: COLUMNS,
        rows: d.rows,
        rowKey: (r) => r.group_id,
        pageSize: 104,
      }).el,
    );
  } catch (err) {
    p.body.append(failNote(err, TRADE_HINT.derived));
  }
}
