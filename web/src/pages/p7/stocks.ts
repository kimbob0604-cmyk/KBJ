// 페이지 7 '품목' 탭 아래 '매핑 종목 랭킹' 표(docs/p6_design.md §4.6·§6, metrics §28 — 묶음 MAP). 종목↔HS 매핑표 종목의
// 최근 3개월 수출 지수 YoY 내림차순(값 없음은 뒤, 동률은 코드 순 — 서버가 정렬해 준다). 서명은 p7/common.ts TabMount.
//
// - 키 'trade/stocks' — 로그인은 API(공개·local 매핑), 공개는 공개 파일(공개 매핑만 — origin 칸 없음).
// - 줄마다 상태(겹침 부족·매출 없음·자료 없음)·'검토 전' 꼬리표. 금액·비율은 달러 기준(원화 환산 없음).
import type { Envelope } from '../../data/source';
import { h } from '../../ui/dom';
import { toCsv } from '../../ui/csv';
import { type Column, table } from '../../ui/table';
import { failed } from '../p1_market';
import { csvMeta, ratio, tableHead, type TabCtx, TRADE_HINT, tradePanel, type TradeStocks } from './common';

export const TITLE = '매핑 종목 랭킹';
type Row = TradeStocks['rows'][number] & { origin?: 'public' | 'local' };
type Ranked = Row & { __rank?: number };

export const STATUS_LABEL: Readonly<Record<string, string>> = {
  ok: '',
  no_trade: '자료 없음',
  no_revenue: '매출 없음',
  short_overlap: '겹침 부족',
};

const tagEl = (text: string, cls = 'dim'): HTMLSpanElement => h('span', { class: `tag ${cls}` }, text);

export function stocksCsv(rows: readonly Row[], basis: string): string {
  return toCsv<Row>(
    rows,
    [
      { label: '순위', value: (r) => rows.indexOf(r) + 1 },
      { label: '종목코드', value: 'code' },
      { label: '종목', value: 'name' },
      { label: '3개월 수출 지수 YoY', value: 'yoy_3m' },
      { label: '상태', value: (r) => STATUS_LABEL[r.status] || '정상' },
      { label: '검토', value: (r) => (r.reviewed ? '검토' : '검토 전') },
    ],
    { meta: [...csvMeta(basis), ['매핑', '자체 큐레이션(관세청+DART 매출)']] },
  );
}

function columns(): Column<Ranked>[] {
  return [
    { key: 'rank', label: '순위', cell: (r) => String(r.__rank ?? '') },
    {
      key: 'name',
      label: '종목',
      align: 'l',
      cell: (r) =>
        h(
          'span',
          {},
          `${r.name} (${r.code})`,
          r.reviewed ? null : tagEl('검토 전', 'warn'),
          r.origin === 'local' ? tagEl('로그인 전용') : null,
        ),
    },
    {
      key: 'yoy',
      label: '3개월 YoY',
      cell: (r) => ratio(r.yoy_3m),
      cls: (r) => (r.yoy_3m === null ? 'dim' : r.yoy_3m >= 0 ? 'up' : 'down'),
    },
    {
      key: 'status',
      label: '상태',
      align: 'l',
      cell: (r) => (STATUS_LABEL[r.status] ? tagEl(STATUS_LABEL[r.status] ?? '') : '정상'),
    },
  ];
}

export async function mount(t: TabCtx, el: HTMLElement): Promise<void> {
  const p = tradePanel(t, {
    title: TITLE,
    def: '매핑 품목 수출의 가중합(종목 수출 지수) 최근 3개월 합의 전년 대비',
  });
  el.append(h('div', { class: 'grid' }, p.el));
  let env: Envelope<TradeStocks>;
  try {
    env = await t.source.get<TradeStocks>('trade/stocks');
  } catch (err) {
    failed(p, err, TRADE_HINT.stocks);
    return;
  }
  const rows: Ranked[] = env.data.rows.map((r, i) => ({ ...r, __rank: i + 1 }));
  const basis = env.notes.find((n) => n.startsWith('랭킹')) ?? env.as_of.slice(0, 10);
  p.clearBody();
  p.body.append(
    tableHead(`${rows.length}종목 · 매핑판 ${env.data.map_version}`, 'trade_stocks', () => stocksCsv(rows, basis)),
    table<Ranked>({
      columns: columns(),
      rows,
      rowKey: (r) => r.code,
      empty: '매핑 종목 없음',
    }).el,
  );
  p.setSource(env);
}
