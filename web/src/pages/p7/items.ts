// 페이지 7 '품목' 탭(EXP — docs/p6_design.md §1.4·§6, metrics §26·§27). HS2 → 4 → 6(→ 10 로그인) 접두 트리.
//
// - 한 층 = 트리맵(크기 = 금액, 색 = 전년 대비) + 표(CSV). 행을 고르면 그 품목의 월별 금액 선·단가 대 물량 분해 막대·
//   수출 상위 국가, 하위가 있으면 한 층 내려간다. 금액은 달러(원화 환산 없음).
// - 로그인: 'trade/items' {level, parent, flow}·'trade/items/<hs>/series'·'trade/items/<hs>/countries'(API).
//   공개: 'trade/hs2'(장 총괄)·'trade/hs/<장>'(그 장의 HS4·HS6 24개월 배열 + HS4 상위 국가) — 같은 모양으로 바꿔 그린다.
//   공개판은 HS10 이 없다(크기 때문 — 공개 등급이라 자물쇠가 아니라 '로그인판에서 HS10' 한 줄).
import type { Envelope } from '../../data/source';
import { toCsv } from '../../ui/csv';
import { h, replace } from '../../ui/dom';
import { pct, signClass } from '../../ui/fmt';
import { lineChart } from '../../ui/line';
import { seg } from '../../ui/seg';
import { stackBar } from '../../ui/stackbar';
import { type Column, table } from '../../ui/table';
import { treemap } from '../../ui/treemap';
import {
  failNote,
  csvMeta,
  type Flow,
  FLOWS,
  perKg,
  type PubCode,
  type PubTree,
  ratio,
  type TabCtx,
  tableHead,
  TRADE_HINT,
  type TradeItemCountries,
  type TradeItemNode,
  type TradeItems,
  type TradeItemSeries,
  tradePanel,
  usd,
} from './common';

export const TITLE = '품목 탐색';
export const SERIES_TITLE = '품목 추이 · 단가 대 물량';
export const HS10_NOTE = 'HS10 단계는 로그인판에서(공개판은 HS2·4·6 최근 24개월)';

type Point = TradeItemSeries['points'][number];
type CountryRow = TradeItemCountries['rows'][number];
type Level = 2 | 4 | 6 | 10;

const NEXT: Readonly<Record<number, Level | undefined>> = { 2: 4, 4: 6, 6: 10 };

/** 공개 트리 파일 → 한 층 노드(마지막 달 — 로그인 API 의 한 달 노드와 같은 뜻) */
export function pubNodes(
  tree: PubTree,
  level: Level,
  parent: string | null,
  flow: Flow,
  q: Point['quality'],
): TradeItemNode[] {
  const last = tree.months.length - 1;
  const picked = tree.codes.filter((c) => c.hs.length === level && (parent === null || c.hs.startsWith(parent)));
  const vals = picked.map((c) => c[flow].usd[last] ?? null);
  const total = vals.reduce<number>((a, v) => a + (v ?? 0), 0);
  const nodes = picked.map((c, i): TradeItemNode => {
    const u = vals[i] ?? null;
    const kg = c[flow].kg[last] ?? null;
    return {
      hs: c.hs,
      name: c.name,
      usd: u,
      kg,
      yoy: c[flow].yoy[last] ?? null,
      share: u !== null && total > 0 ? u / total : null,
      unit_price: u !== null && kg !== null && kg > 0 ? u / kg : null,
      has_children: c.has_children,
      quality: q,
    };
  });
  return nodes.sort((a, b) => (b.usd ?? -1) - (a.usd ?? -1) || a.hs.localeCompare(b.hs));
}

/** 공개 트리 한 줄 → 월별 점(단가·전년 대비·분해 — 24개월 배열이라 분해는 뒤 12개월만) */
export function pubPoints(code: PubCode, months: readonly string[], flow: Flow, q: Point['quality']): Point[] {
  const s = code[flow];
  const out: Point[] = [];
  months.forEach((ym, i) => {
    const u = s.usd[i] ?? null;
    if (u === null) return;
    const kg = s.kg[i] ?? null;
    const pu = i >= 12 ? (s.usd[i - 12] ?? null) : null;
    const pk = i >= 12 ? (s.kg[i - 12] ?? null) : null;
    const ok = u > 0 && kg !== null && kg > 0 && pu !== null && pu > 0 && pk !== null && pk > 0;
    const lnVol = ok ? Math.log(kg / pk) : null;
    out.push({
      ym,
      usd: u,
      kg,
      unit_price: kg !== null && kg > 0 ? u / kg : null,
      yoy: s.yoy[i] ?? null,
      yoy_3m: null,
      ln_price: ok && lnVol !== null ? Math.log(u / pu) - lnVol : null,
      ln_volume: lnVol,
      quality: q,
    });
  });
  return out;
}

const COLUMNS: readonly Column<TradeItemNode>[] = [
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
  { key: 'price', label: '단가', cell: (r) => perKg(r.unit_price) },
];

const COUNTRY_COLUMNS: readonly Column<CountryRow>[] = [
  {
    key: 'cc',
    label: '국가',
    align: 'l',
    cell: (r) => r.country_name ?? r.country,
  },
  { key: 'usd', label: '금액', cell: (r) => usd(r.usd) },
  {
    key: 'yoy',
    label: '전년 대비',
    cell: (r) => ratio(r.yoy),
    cls: (r) => signClass(r.yoy),
  },
  { key: 'share', label: '비중', cell: (r) => ratio(r.share).replace('+', '') },
];

/** 표 CSV(지금 보이는 층 그대로) */
export function itemsCsv(rows: readonly TradeItemNode[], basis: string): string {
  return toCsv(
    rows,
    [
      { label: 'HS', value: 'hs' },
      { label: '품목', value: 'name' },
      { label: '금액(달러)', value: 'usd' },
      { label: '중량(kg)', value: 'kg' },
      { label: '전년 대비', value: 'yoy' },
      { label: '비중', value: 'share' },
      { label: '단가(달러/kg)', value: 'unit_price' },
    ],
    { meta: csvMeta(basis) },
  );
}

interface View {
  level: Level;
  parent: string | null;
  basis: string;
  rows: TradeItemNode[];
  env: Envelope<unknown>;
  tree?: PubTree;
}

export async function mount(t: TabCtx, el: HTMLElement): Promise<void> {
  const p = tradePanel(t, { title: TITLE });
  p.setDef(
    '접두 값 = 그 HS 행, 없으면 바로 아래 HS 의 합. 크기 = 금액, 색 = 전년 대비. 행을 고르면 아래 추이·한 층 아래.',
  );
  const sp = tradePanel(t, { title: SERIES_TITLE });
  sp.setDef('ln(1+금액 YoY) = ln(1+단가 YoY) + ln(1+물량 YoY) — 막대는 두 기여(%)를 쌓는다. 단가 = 금액 ÷ 중량.');
  sp.showMessage('위 표에서 품목을 고르면 그린다');
  el.append(h('div', { class: 'grid' }, p.el, sp.el));
  let flow: Flow = 'exp';
  const path: string[] = [];
  const files = new Map<string, Promise<Envelope<PubTree>>>();
  const pubFile = (key: string): Promise<Envelope<PubTree>> => {
    let f = files.get(key);
    if (!f) {
      f = t.source.get<PubTree>(key);
      files.set(key, f);
      f.catch(() => files.delete(key));
    }
    return f;
  };
  const crumbs = h('div', { class: 'crumbs' });
  const body = h('div', { class: 'items-body' });

  const load = async (level: Level, parent: string | null): Promise<View> => {
    if (t.tier === 'login') {
      const env = await t.source.get<TradeItems>('trade/items', {
        level,
        parent,
        flow,
      });
      return { level, parent, basis: env.data.ym_to, rows: env.data.rows, env };
    }
    const env = await pubFile(parent === null ? 'trade/hs2' : `trade/hs/${parent.slice(0, 2)}`);
    const tree = env.data;
    return {
      level,
      parent,
      basis: tree.months.at(-1) ?? '',
      rows: pubNodes(tree, level, parent, flow, env.quality),
      env,
      tree,
    };
  };

  const showSeries = async (v: View, node: TradeItemNode): Promise<void> => {
    try {
      let points: Point[];
      let unfit = false;
      let countries: CountryRow[] = [];
      if (t.tier === 'login') {
        const env = await t.source.get<TradeItemSeries>(`trade/items/${node.hs}/series`, { flow });
        sp.setSource(env);
        points = env.data.points;
        unfit = env.data.weight_unfit;
        try {
          countries = (await t.source.get<TradeItemCountries>(`trade/items/${node.hs}/countries`, { flow })).data.rows;
        } catch {
          countries = []; // 품목×국가가 없는 자릿수(HS6·10)는 국가 표 없이 — 추이는 그대로
        }
      } else {
        const tree = v.tree;
        const code = tree?.codes.find((c) => c.hs === node.hs);
        if (!tree || !code) throw new Error('공개 파일에 그 품목 없음');
        sp.setSource(v.env);
        points = pubPoints(code, tree.months, flow, v.env.quality);
        unfit = (tree.weight_unfit ?? []).some((x) => node.hs.startsWith(x) || x.startsWith(node.hs));
        countries = flow === 'exp' ? (tree.countries?.[node.hs] ?? []) : [];
      }
      const months = points.map((x) => x.ym);
      const split = points.filter((x) => x.ln_price !== null && x.ln_volume !== null);
      replace(
        sp.body,
        h(
          'p',
          { class: 'flash-head' },
          h('b', {}, `${node.hs} ${node.name ?? ''}`),
          ` ${FLOWS.find((f) => f.value === flow)?.label ?? ''} ${usd(points.at(-1)?.usd)} · 전년 대비 `,
          h('span', { class: signClass(points.at(-1)?.yoy) }, ratio(points.at(-1)?.yoy)),
          unfit ? h('span', { class: 'tag' }, '중량 기준 부적합') : null,
        ),
        lineChart({
          label: `${node.hs} 월별 금액(억 달러)`,
          x: months,
          series: [
            {
              label: '금액(억 달러)',
              values: points.map((x) => (x.usd === null ? null : x.usd / 1e8)),
            },
          ],
          format: (x) => `${x.toFixed(1)}억`,
        }).el,
        split.length
          ? stackBar({
              label: `${node.hs} 전년 대비 분해(%)`,
              categories: split.map((x) => x.ym),
              series: [
                {
                  label: '단가',
                  values: split.map((x) => (x.ln_price ?? 0) * 100),
                },
                {
                  label: '물량',
                  values: split.map((x) => (x.ln_volume ?? 0) * 100),
                },
              ],
              format: (x) => pct(x, 1, true),
            })
          : h('p', { class: 'msg' }, '분해할 전년 값(금액·중량)이 없다'),
        countries.length
          ? table<CountryRow>({
              caption: `${node.hs} 수출 상위 국가`,
              columns: COUNTRY_COLUMNS,
              rows: countries,
              pageSize: 10,
            }).el
          : null,
      );
    } catch (err) {
      sp.showError(err);
    }
  };

  const draw = (v: View): void => {
    p.setSource(v.env);
    const items = v.rows.map((r) => ({
      id: r.hs,
      label: `${r.hs} ${r.name ?? ''}`,
      size: r.usd,
      color: r.yoy === null ? null : r.yoy * 100,
    }));
    const next = NEXT[v.level];
    const tbl = table<TradeItemNode>({
      caption: `HS${String(v.level)} ${v.parent ?? '전체'} ${v.basis}`,
      columns: COLUMNS,
      rows: v.rows,
      rowKey: (r) => r.hs,
      onSelect: (r) => {
        void showSeries(v, r);
        if (r.has_children && next !== undefined && (next !== 10 || t.tier === 'login')) {
          path.push(r.hs);
          void go();
        }
      },
    });
    replace(
      body,
      treemap({
        label: `HS${String(v.level)} 트리맵`,
        items,
        formatSize: usd,
        formatColor: (x) => pct(x, 1, true),
      }),
      tableHead(
        `HS${String(v.level)} ${v.parent ?? '전체'} · ${v.basis}`,
        `trade_items_${v.parent ?? 'all'}_${flow}`,
        () => itemsCsv(v.rows, v.basis),
      ),
      tbl.el,
      t.tier === 'public' && v.level === 6 ? h('p', { class: 'def' }, HS10_NOTE) : null,
    );
  };

  const go = async (): Promise<void> => {
    const parent = path.at(-1) ?? null;
    const level = (parent === null ? 2 : NEXT[parent.length]) ?? 2;
    replace(
      crumbs,
      [null, ...path].map((x, i) =>
        h(
          'button',
          {
            class: 'btn',
            attrs: {
              type: 'button',
              'aria-current': i === path.length ? 'true' : null,
            },
            on: {
              click: () => {
                path.length = i;
                void go();
              },
            },
          },
          x ?? '전체',
        ),
      ),
    );
    try {
      draw(await load(level, parent));
    } catch (err) {
      replace(body, failNote(err, TRADE_HINT.monthly));
    }
  };

  const flowSeg = seg<Flow>({
    label: '흐름',
    options: FLOWS,
    value: flow,
    onChange: (v) => {
      flow = v;
      void go();
    },
  });
  p.body.append(flowSeg.el, crumbs, body);
  await go();
}
