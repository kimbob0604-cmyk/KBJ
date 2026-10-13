// 페이지 7 '급등' 탭(EXP — docs/p6_design.md §1.4·§6, metrics §26). 마지막 달 급등·급락(수준 문턱 이상 ∧ 전년 대비 ∧
// 3개월 전년 대비 — trade.derive 가 쓴 pub_trade.surge). 층(HS2·4·6·국가)·흐름으로 거른다(화면에서 — 요청 1회).
// 로그인: 'trade/surges'(API), 공개: 'trade/surge'(같은 모양). 금액은 달러.
import { toCsv } from '../../ui/csv';
import { h, replace } from '../../ui/dom';
import { num, signClass } from '../../ui/fmt';
import { seg } from '../../ui/seg';
import { type Column, table } from '../../ui/table';
import {
  failNote,
  csvMeta,
  type Flow,
  FLOWS,
  ratio,
  type TabCtx,
  tableHead,
  TRADE_HINT,
  type TradeSurges,
  tradePanel,
  usd,
} from './common';

export const TITLE = '급등·급락';

type Row = TradeSurges['rows'][number];
type Level = Row['level'] | 'all';

const LEVELS: readonly { value: Level; label: string }[] = [
  { value: 'all', label: '전체' },
  { value: 'hs2', label: 'HS2' },
  { value: 'hs4', label: 'HS4' },
  { value: 'hs6', label: 'HS6' },
  { value: 'country', label: '국가' },
];

const COLUMNS: readonly Column<Row>[] = [
  {
    key: 'rule',
    label: '구분',
    align: 'l',
    cell: (r) => (r.rule === 'surge' ? `급등 ${String(r.rank)}` : `급락 ${String(r.rank)}`),
    cls: (r) => (r.rule === 'surge' ? 'up' : 'down'),
  },
  {
    key: 'key',
    label: '코드',
    align: 'l',
    cell: (r) => `${r.level.toUpperCase()} ${r.key}`,
  },
  { key: 'name', label: '이름', align: 'l', cell: (r) => r.name },
  { key: 'usd', label: '금액', cell: (r) => usd(r.usd) },
  {
    key: 'yoy',
    label: '전년 대비',
    cell: (r) => ratio(r.yoy),
    cls: (r) => signClass(r.yoy),
  },
  {
    key: 'yoy3',
    label: '3개월 전년 대비',
    cell: (r) => ratio(r.yoy_3m),
    cls: (r) => signClass(r.yoy_3m),
  },
];

/** 고른 층·흐름의 행(급등 먼저, 순위 순) */
export function surgeRows(rows: readonly Row[], level: Level, flow: Flow): Row[] {
  return rows
    .filter((r) => (level === 'all' || r.level === level) && r.flow === flow)
    .sort((a, b) => (a.rule === b.rule ? a.rank - b.rank : a.rule === 'surge' ? -1 : 1));
}

export function surgeCsv(rows: readonly Row[], ym: string): string {
  return toCsv(
    rows,
    [
      { label: '구분', value: 'rule' },
      { label: '순위', value: 'rank' },
      { label: '층', value: 'level' },
      { label: '코드', value: 'key' },
      { label: '이름', value: 'name' },
      { label: '흐름', value: 'flow' },
      { label: '금액(달러)', value: 'usd' },
      { label: '전년 대비', value: 'yoy' },
      { label: '3개월 전년 대비', value: 'yoy_3m' },
    ],
    { meta: csvMeta(ym) },
  );
}

export async function mount(t: TabCtx, el: HTMLElement): Promise<void> {
  const p = tradePanel(t, { title: TITLE });
  el.append(h('div', { class: 'grid' }, p.el));
  try {
    const env = await t.source.get<TradeSurges>(t.tier === 'login' ? 'trade/surges' : 'trade/surge');
    p.setSource(env);
    const d = env.data;
    p.setDef(
      `급등 = 금액 ≥ ${num(d.min_usd / 1e6)}백만 달러 ∧ 전년 대비·3개월 전년 대비가 문턱 이상, 급락 = 전년 금액이 문턱 이상이면서 크게 줄어든 것.`,
    );
    let level: Level = 'all';
    let flow: Flow = 'exp';
    const body = h('div', { class: 'surge-body' });
    const draw = (): void => {
      const rows = surgeRows(d.rows, level, flow);
      replace(
        body,
        tableHead(`${d.ym} · ${String(rows.length)}건`, `trade_surge_${d.ym}_${flow}`, () => surgeCsv(rows, d.ym)),
        table<Row>({
          caption: `급등·급락 ${d.ym}`,
          columns: COLUMNS,
          rows,
          rowKey: (r) => `${r.level}:${r.key}:${r.rule}`,
          empty: '해당 없음',
        }).el,
      );
    };
    const lv = seg<Level>({
      label: '층',
      options: LEVELS,
      value: level,
      onChange: (v) => ((level = v), draw()),
    });
    const fl = seg<Flow>({
      label: '흐름',
      options: FLOWS,
      value: flow,
      onChange: (v) => ((flow = v), draw()),
    });
    p.body.append(lv.el, fl.el, body);
    draw();
  } catch (err) {
    p.body.append(failNote(err, TRADE_HINT.derived));
  }
}
