// 페이지 7 '종목' 탭 — 종목별 무역 흐름(P10 단계 1 — docs/p10_trade_stocks_plan.md, 나쁜양파 '종목별 흐름' 같은 단위).
// 랭킹 표(p7/stocks.ts)에서 종목을 누르거나 위 상자에서 고르면 `/api/trade/stock/<코드>/flows` 를 그린다.
//
// - 흐름 카드: 국가·방향, 시군구(사업장), 추적 품목, 상대국, 구분(회사 출하·업황 혼합·관계사 혼입), 최근 기준월, 근거(접기).
// - 흐름 차트: 품목별 누적 막대(달러) + 전년동월비 선. 보기 바꾸기: 금액 · 단가 · 중량 · 일평균.
// - 월 표 + CSV. 금액은 통관 합(가중치 미적용, 달러 — 원화 환산 없음). 시군구 흐름은 중량·단가 없음.
// - 로그인 전용(공개 파일 없음 — 공개판은 자물쇠). 매핑 없는 종목은 안내 한 줄.
// - 맨 위에 실적 프리뷰 카드(p7/preview.ts — 단계 3). 프리뷰가 실패해도 흐름은 그린다.
import type { components } from '../../api/types.gen';
import { h } from '../../ui/dom';
import { toCsv } from '../../ui/csv';
import { num, signClass } from '../../ui/fmt';
import { kv } from '../../ui/kv';
import { lineChart } from '../../ui/line';
import { panel } from '../../ui/panel';
import { seg } from '../../ui/seg';
import { stackBar } from '../../ui/stackbar';
import { type Column, table } from '../../ui/table';
import {
  csvMeta,
  failNote,
  perKg,
  ratio,
  tableHead,
  type TabCtx,
  TRADE_HINT,
  TRADE_SOURCE,
  type TradeStocks,
  usd,
} from './common';
import { previewCard, type TradePreview } from './preview';

type S = components['schemas'];
export type TradeStockFlows = S['TradeStockFlows'];
export type TradeFlow = TradeStockFlows['flows'][number];
type Point = TradeFlow['points'][number];

export const TITLE = '종목별 무역 흐름';
export type View = 'usd' | 'price' | 'kg' | 'daily';
export const VIEWS: readonly { value: View; label: string }[] = [
  { value: 'usd', label: '금액' },
  { value: 'price', label: '단가' },
  { value: 'kg', label: '중량' },
  { value: 'daily', label: '일평균' },
];

const DIR: Readonly<Record<string, string>> = { exp: '수출', imp: '수입' };

function shortYm(ym: string): string {
  return `${ym.slice(2, 4)}.${ym.slice(5, 7)}`;
}

/** 보기마다 (값, YoY) — 단위 글자 포함 */
export function pick(p: Point, view: View): { v: number | null; yoy: number | null } {
  switch (view) {
    case 'price':
      return { v: p.unit_price, yoy: p.price_yoy };
    case 'kg':
      return { v: p.kg, yoy: p.kg_yoy };
    case 'daily':
      return { v: p.daily_usd, yoy: p.daily_yoy };
    default:
      return { v: p.usd, yoy: p.usd_yoy };
  }
}

function fmtView(v: number | null, view: View): string {
  if (view === 'price') return perKg(v);
  if (view === 'kg') return v === null ? '—' : `${num(v / 1000)} t`;
  return usd(v);
}

/** 흐름 카드(머리) — 나쁜양파 흐름 카드와 같은 칸 */
export function flowCard(f: TradeFlow): HTMLElement {
  const where =
    f.kind === 'sigungu' ? `시군구 (HS6) ${DIR[f.direction] ?? ''} 흐름` : `전국 품목 ${DIR[f.direction] ?? ''} 흐름`;
  const items = f.products.map((p) => `${p.name ?? p.hs} (${p.hs})`).join(' · ');
  const rows: [string, string][] = [
    ['국가', `한국 → ${DIR[f.direction] ?? f.direction}`],
    ...(f.kind === 'sigungu' ? ([['시군구', f.regions.join(', ')]] as [string, string][]) : []),
    ['추적 품목', items],
    ['상대국', f.partners.length ? f.partners.join(', ') : '전세계'],
    ['구분', f.nature_label],
    ['최근 기준월', f.last_ym ?? '자료 없음'],
  ];
  return h(
    'div',
    { class: 'flow-card', data: { flow: f.id } },
    h('h5', null, f.name, h('span', { class: 'tag dim' }, where)),
    kv(rows),
    f.basis ? h('details', { class: 'flow-basis' }, h('summary', null, '근거'), h('p', null, f.basis)) : null,
    ...f.notes.map((n) => h('p', { class: 'msg' }, n)),
  );
}

/** 흐름 차트 — 금액 보기는 품목별 누적, 나머지는 선 하나 + YoY 선 */
export function flowChart(f: TradeFlow, view: View): HTMLElement {
  const pts = f.points;
  const cats = pts.map((p) => shortYm(p.ym));
  const label = VIEWS.find((x) => x.value === view)?.label ?? '';
  const main =
    view === 'usd'
      ? stackBar({
          label: `${f.name} — 품목별 금액(달러)`,
          categories: cats,
          series: f.products.map((p) => ({
            label: p.name ?? p.hs,
            values: pts.map((x) => x.products[p.hs] ?? null),
          })),
          format: (v) => usd(v),
          h: 220,
        })
      : lineChart({
          label: `${f.name} — ${label}`,
          x: cats,
          series: [{ label, values: pts.map((p) => pick(p, view).v) }],
          format: (v) => fmtView(v, view),
          h: 180,
        }).el;
  const yoy = lineChart({
    label: `${f.name} — ${label} 전년동월비(%)`,
    x: cats,
    series: [
      {
        label: `${label} YoY`,
        values: pts.map((p) => {
          const y = pick(p, view).yoy;
          return y === null ? null : y * 100;
        }),
      },
    ],
    zero: true,
    format: (v) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`,
    h: 120,
  }).el;
  return h('div', { class: 'flow-chart' }, main, yoy);
}

function cols(f: TradeFlow): Column<Point>[] {
  const out: Column<Point>[] = [
    { key: 'ym', label: '기간', align: 'l', cell: (r) => r.ym },
    { key: 'usd', label: '금액', cell: (r) => usd(r.usd) },
    {
      key: 'usd_yoy',
      label: '금액 YoY',
      cell: (r) => ratio(r.usd_yoy),
      cls: (r) => signClass(r.usd_yoy),
    },
  ];
  if (f.has_kg) {
    out.push(
      { key: 'kg', label: '중량', cell: (r) => fmtView(r.kg, 'kg') },
      {
        key: 'kg_yoy',
        label: '중량 YoY',
        cell: (r) => ratio(r.kg_yoy),
        cls: (r) => signClass(r.kg_yoy),
      },
      { key: 'price', label: '단가', cell: (r) => perKg(r.unit_price) },
      {
        key: 'price_yoy',
        label: '단가 YoY',
        cell: (r) => ratio(r.price_yoy),
        cls: (r) => signClass(r.price_yoy),
      },
    );
  }
  out.push(
    { key: 'daily', label: '일평균', cell: (r) => usd(r.daily_usd) },
    {
      key: 'daily_yoy',
      label: '일평균 YoY',
      cell: (r) => ratio(r.daily_yoy),
      cls: (r) => signClass(r.daily_yoy),
    },
  );
  return out;
}

export function flowsCsv(d: TradeStockFlows, f: TradeFlow, basis: string): string {
  return toCsv<Point>(
    [...f.points].reverse(),
    [
      { label: '기간', value: 'ym' },
      { label: '금액(달러)', value: 'usd' },
      { label: '금액 YoY', value: 'usd_yoy' },
      { label: '중량(kg)', value: 'kg' },
      { label: '중량 YoY', value: 'kg_yoy' },
      { label: '단가(달러/kg)', value: 'unit_price' },
      { label: '단가 YoY', value: 'price_yoy' },
      { label: '영업일', value: 'days' },
      { label: '일평균(달러)', value: 'daily_usd' },
      { label: '일평균 YoY', value: 'daily_yoy' },
    ],
    {
      meta: [...csvMeta(basis), ['종목', `${d.name} (${d.code})`], ['흐름', f.name]],
    },
  );
}

/** 한 흐름 덩어리 — 카드 + 보기 바꾸기 + 차트 + 표 */
export function flowBlock(d: TradeStockFlows, f: TradeFlow, basis: string): HTMLElement {
  const views = VIEWS.filter((v) => f.has_kg || (v.value !== 'price' && v.value !== 'kg'));
  const chart = h('div', null, flowChart(f, 'usd'));
  const sw = seg<View>({
    label: `${f.name} 보기`,
    options: views,
    value: 'usd',
    onChange: (v) => chart.replaceChildren(flowChart(f, v)),
  });
  return h(
    'section',
    { class: 'flow-block', attrs: { 'aria-label': f.name } },
    flowCard(f),
    sw.el,
    chart,
    tableHead(`${f.name} · 월별`, `trade_flow_${d.code}_${f.id}`, () => flowsCsv(d, f, basis)),
    table<Point>({
      columns: cols(f),
      rows: [...f.points].reverse(),
      rowKey: (r) => r.ym,
      pageSize: 12,
      empty: '자료 없음',
    }).el,
  );
}

export interface FlowsPanel {
  readonly el: HTMLElement;
  show(code: string): Promise<void>;
}

/** 패널 — 종목 고르기 상자 + 흐름들. 랭킹 표가 show(code) 를 부른다 */
export function flowsPanel(t: TabCtx, stocks: TradeStocks['rows'] = []): FlowsPanel {
  const p = panel(t.tier, {
    title: TITLE,
    tier: 'login',
    span: 12,
    src: TRADE_SOURCE,
  });
  p.setDef(
    '종목마다 매핑한 무역 흐름(전국 품목·사업장 시군구 × HS6). 금액은 통관 합(가중치 미적용, 달러). 일평균 = 금액 ÷ 영업일.',
  );
  const out = h('div', { class: 'flows' });
  const select = h(
    'select',
    {
      attrs: { 'aria-label': '종목 고르기' },
      on: { change: (e) => void show((e.target as HTMLSelectElement).value) },
    },
    h('option', { attrs: { value: '' } }, '종목 고르기'),
    ...stocks.map((r) => h('option', { attrs: { value: r.code } }, `${r.name} (${r.code})`)),
  );
  if (!p.locked) {
    p.clearBody();
    p.body.append(select, out);
  }
  async function show(code: string): Promise<void> {
    if (p.locked || !code) return;
    select.value = code;
    out.replaceChildren(h('p', { class: 'msg' }, '불러오는 중…'));
    const pv = t.source
      .get<TradePreview>(`trade/stock/${code}/preview`)
      .then((e) => previewCard(e.data, e.notes))
      .catch(() => h('p', { class: 'msg' }, '실적 프리뷰 없음(지수·매출 부족)'));
    try {
      const env = await t.source.get<TradeStockFlows>(`trade/stock/${code}/flows`);
      const d = env.data;
      const basis = env.as_of.slice(0, 10);
      out.replaceChildren(
        h(
          'p',
          { class: 'flows-head' },
          `${d.name} (${d.code}) · 흐름 ${String(d.flows.length)}개`,
          d.reviewed ? null : h('span', { class: 'tag warn' }, '검토 전'),
        ),
        await pv,
        ...d.flows.map((f) => flowBlock(d, f, basis)),
      );
      p.setSource(env);
    } catch (err) {
      out.replaceChildren(failNote(err, TRADE_HINT.stocks));
    }
  }
  return { el: p.el, show };
}
