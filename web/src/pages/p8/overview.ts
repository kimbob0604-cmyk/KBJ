// 페이지 8 '개요' 탭 — 가격 차트(SMA 20·60·120), 핵심 지표 카드, 수급 20일, 최근 공시·잠정실적, 빈 자리(docs/p4_design.md §6.2).
//
// - 로그인: chart·valuation·financials·overhang·insiders·consensus·flows/stock·disclosures·earnings — 같은 요청은 탭끼리
//   나눠 쓴다(TabCtx.get). 핵심 지표 카드는 원천마다 따로 받아(allSettled) 하나가 실패해도 나머지 칸을 그린다.
// - 공개: 차트는 TradingView 위젯, 카드는 DART 칸(ROE·OPM·오버행 비율·내부자 주식 수)만 — 나머지 칸은 자물쇠.
// - 기술적 지표는 SMA 20·60·120 만(D-P4-21). 가격은 비수정(분할은 events 로 표시).
import type { Envelope } from '../../data/source';
import { h } from '../../ui/dom';
import { kstDate, num, pct, shortDate, signClass } from '../../ui/fmt';
import { kv, type KvItem } from '../../ui/kv';
import type { Panel } from '../../ui/panel';
import { s, svgBox } from '../../ui/svg';
import { table } from '../../ui/table';
import { fill } from '../p1_market';
import { drawDetail } from '../p4_flows';
import {
  discColumns,
  type DiscRow,
  errLine,
  HINT,
  latestPrelims,
  mkPanel,
  naText,
  NO_PUB_FIN,
  prelimColumns,
  pubDiscRows,
  pubPrelims,
  type S,
  shares,
  tabGrid,
  type TabCtx,
} from './common';

const SMA: readonly [string, string, string?][] = [
  ['20', 'var(--accent)'],
  ['60', 'var(--warn)', '4 3'],
  ['120', 'var(--est)', '2 3'],
];

const ok = (v: number | null | undefined): v is number => typeof v === 'number' && Number.isFinite(v);

/** 종가 선 + SMA 20·60·120 + 분할 표시 — 값이 없는 날은 선을 끊는다 */
export function priceChart(d: S['PriceChart']): SVGSVGElement {
  const W = 900;
  const H = 260;
  const top = 16;
  const plotW = W - 72;
  const plotH = H - 44;
  const vBase = H - 22;
  const n = d.bars.length;
  const box = svgBox(`0 0 ${W} ${H}`, `${d.code} 일봉 ${n}일과 이동평균`);
  const closes = d.bars.map((b) => b.c);
  const all = [...closes, ...SMA.flatMap(([k]) => d.sma[k] ?? [])].filter(ok);
  if (!all.length) {
    s('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle' }, box, '데이터 없음');
    return box;
  }
  const lo = Math.min(...all);
  const span = Math.max(...all) - lo || 1;
  const x = (i: number): number => 8 + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const y = (v: number): number => top + (1 - (v - lo) / span) * plotH;
  const poly = (vals: readonly (number | null)[], stroke: string, w: number, dash?: string): void => {
    let cur: string[] = [];
    const out = (): void => {
      if (cur.length > 1) s('polyline', { points: cur.join(' '), fill: 'none', stroke, 'stroke-width': w, 'stroke-dasharray': dash ?? 'none' }, box);
      cur = [];
    };
    vals.forEach((v, i) => (ok(v) ? cur.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`) : out()));
    out();
  };
  for (const [k, stroke, dash] of SMA) poly(d.sma[k] ?? [], stroke, 1, dash);
  poly(closes, 'var(--fg)', 1.6);
  for (const ev of d.events) {
    const i = d.bars.findIndex((b) => b.date >= ev.date);
    if (i < 0) continue;
    s('line', { x1: x(i), x2: x(i), y1: top, y2: vBase, stroke: 'var(--warn)', 'stroke-dasharray': '3 3' }, box);
    s('text', { x: x(i) + 3, y: top + 10, class: 'warn' }, box, `분할 ×${num(ev.ratio, 0)}`);
  }
  s('text', { x: 8, y: 11, class: 'fg' }, box, '종가 · SMA20 · SMA60 · SMA120(실선·긴 점선·짧은 점선)');
  s('text', { x: W - 60, y: top + 4 }, box, num(lo + span, 0));
  s('text', { x: W - 60, y: top + plotH }, box, num(lo, 0));
  s('text', { x: 8, y: H - 6 }, box, `${kstDate(d.bars[0]?.date)} ~ ${kstDate(d.bars[n - 1]?.date)} · 비수정 가격`);
  return box;
}

// ── 핵심 지표 카드 ────────────────────────────────────────

/** 칸 원천 줄 — 받았으면 원천·시각, 못 받았으면 그 까닭(404 no_data 는 서버 안내 '아직 없음 — …' — §6.3) */
const hint = (env: Envelope<unknown> | null, why?: string | null): string => (env ? `${env.source} · ${shortDate(env.as_of)}` : (why ?? '불러오지 못함'));

/** 카드 원천마다 못 받은 까닭(errLine — 404 는 서버 안내, 그 밖은 상태 코드만) */
export type CardWhy = Readonly<Partial<Record<'v' | 'f' | 'o' | 'i' | 'c', string>>>;

/** 비율 칸(ROE·OPM) — 적용 불가는 사유 */
function ratioItem(label: string, r: S['RatioView'] | null | undefined, key: 'roe' | 'opm', h0?: string): KvItem {
  const na = r?.na_reasons[key];
  return na ? { label, na: naText(na, label), hint: h0 } : { label, value: r ? pct(r[key], 1, false) : null, quality: r?.quality, hint: h0 };
}

function multipleItem(label: string, env: Envelope<S['Valuation']> | null, key: 'per' | 'pbr', why?: string): KvItem {
  const d = env?.data;
  const c = d?.current?.[key];
  if (d && !d.applicable) return { label, na: naText(d.na_reason, label), hint: hint(env) };
  if (c && !c.applicable) return { label, na: naText(c.na_reason, label), hint: hint(env) };
  return { label, value: ok(c?.value) ? `${num(c.value, 1)}x` : null, quality: d?.current?.quality, hint: hint(env, why) };
}

export function metricsItems(
  v: Envelope<S['Valuation']> | null,
  f: Envelope<S['Financials']> | null,
  o: Envelope<S['Overhang']> | null,
  i: Envelope<S['Insiders']> | null,
  c: Envelope<S['Consensus']> | null,
  why: CardWhy = {},
): KvItem[] {
  const sum = o?.data.summary;
  const w90 = i?.data.windows.find((w) => w.days === 90);
  const tg = c?.data.target;
  const fin = f && !f.data.applicable ? null : f;
  return [
    multipleItem('PER', v, 'per', why.v),
    multipleItem('PBR', v, 'pbr', why.v),
    f && !fin ? { label: 'ROE', na: naText(f.data.na_reason) } : ratioItem('ROE', fin?.data.ratios, 'roe', hint(f, why.f)),
    f && !fin ? { label: 'OPM', na: naText(f.data.na_reason) } : ratioItem('OPM', fin?.data.ratios, 'opm', hint(f, why.f)),
    { label: '오버행 비율', value: o ? (sum ? pct(sum.ratio_pct, 2, false) : '없음') : null, quality: sum?.quality, hint: hint(o, why.o) },
    {
      label: '내부자 90일',
      value: i ? (w90 ? shares(w90.net_shares, true) : '보고 없음') : null,
      cls: signClass(w90?.net_shares),
      quality: w90?.quality,
      hint: `보고 기준 순증감 · ${hint(i, why.i)}`,
    },
    c && !c.data.applicable
      ? { label: '목표가 업사이드', na: naText(c.data.na_reason), hint: hint(c) }
      : { label: '목표가 업사이드', value: tg ? pct(tg.upside_pct, 1) : (why.c ?? null), cls: signClass(tg?.upside_pct), quality: tg?.quality, hint: c ? `${c.data.label} · ${hint(c)}` : undefined },
  ];
}

// ── 탭 ─────────────────────────────────────────────────────

function recent(p: Panel, disc: DiscRow[] | string, prelims: S['PrelimView'][] | string, login: boolean): void {
  p.clearBody();
  p.body.append(
    typeof disc === 'string'
      ? h('p', { class: 'msg' }, disc)
      : table({ caption: '최근 공시 5건', columns: discColumns(login), rows: disc.slice(0, 5), empty: '최근 공시 없음' }).el,
    typeof prelims === 'string'
      ? h('p', { class: 'msg' }, `잠정실적: ${prelims}`)
      : table({ caption: '최근 잠정실적', columns: prelimColumns(login), rows: latestPrelims(prelims).rows.slice(0, 3), empty: '잠정실적 공시 없음' }).el,
  );
}

export async function mount(t: TabCtx, el: HTMLElement): Promise<void> {
  const pub = t.tier === 'public';
  const pChart = mkPanel(t, { title: '가격 차트', tier: 'public', span: 8, src: pub ? 'TradingView 제공' : '' });
  const pCard = mkPanel(t, { title: '핵심 지표', tier: 'public', span: 4 });
  const pFlow = mkPanel(t, { title: '수급 20일', tier: 'login', span: 6, lock: 'investor' });
  const pRecent = mkPanel(t, { title: '최근 공시·잠정실적', tier: 'public', span: 6 });
  const pend = (title: string, phase: string): Panel => mkPanel(t, { title, tier: 'public', span: 4, pending: phase });
  tabGrid(el, pChart, pCard, pFlow, pRecent, pend('피어', 'P5'), pend('검증 시트', 'P8'), pend('수출 겹쳐 보기', 'P6'));
  const code = t.code;

  if (pub) {
    const tv = t.tradingview;
    if (tv) {
      const cell = h('div', { attrs: { role: 'figure', 'aria-label': `${code} 차트(TradingView)` } });
      pChart.body.append(cell);
      // KRX 종목 심볼 [확인 필요 — R14]
      tv(cell, 'symbol-overview', { symbols: [[code, `KRX:${code}|12M`]], width: '100%', height: 280, chartType: 'line' });
    } else pChart.showMessage('TradingView 위젯을 불러오지 못함');
    try {
      const env = await t.pub();
      if (!env) {
        pCard.showMessage(NO_PUB_FIN);
        pRecent.showMessage(NO_PUB_FIN);
        return;
      }
      const d = env.data;
      const r = d.financials?.ratios;
      const w90 = d.insider?.windows.find((w) => w.days === 90);
      pCard.clearBody();
      pCard.body.append(
        kv(
          [
            { label: 'PER', locked: true },
            { label: 'PBR', locked: true },
            ratioItem('ROE', r, 'roe'),
            ratioItem('OPM', r, 'opm'),
            { label: '오버행 비율', value: d.overhang ? pct(d.overhang.ratio_pct, 2, false) : '없음', quality: d.overhang?.quality },
            { label: '내부자 90일', value: w90 ? shares(w90.net_shares, true) : '보고 없음', cls: signClass(w90?.net_shares), quality: w90?.quality, hint: '보고 기준 순증감' },
            { label: '목표가 업사이드', locked: true },
          ],
          { layout: 'rows' },
        ),
      );
      pCard.setSource({ ...env, notes: [] });
      const prelims = await pubPrelims(t).catch((err: unknown) => errLine(err, HINT.public));
      recent(pRecent, pubDiscRows(d), prelims, false);
      pRecent.setSource({ ...env, notes: [] });
    } catch (err) {
      pCard.showMessage(errLine(err, HINT.public));
      pRecent.showMessage(errLine(err, HINT.public));
    }
    return;
  }

  pChart.setDef('굵은 선 = 종가(비수정), 가는 선 = 단순이동평균 20·60·120일. 다른 기술적 지표는 P8.');
  pCard.setSrcText('칸마다 원천·시각(이름 아래)');
  const card = Promise.allSettled([
    t.get<S['Valuation']>(`stock/${code}/valuation`, { metric: 'per' }),
    t.get<S['Financials']>(`stock/${code}/financials`, { basis: 'q', n: 12 }),
    t.get<S['Overhang']>(`stock/${code}/overhang`),
    t.get<S['Insiders']>(`stock/${code}/insiders`, { days: 90 }),
    t.get<S['Consensus']>(`stock/${code}/consensus`),
  ]).then((rs) => {
    const [v, f, o, i, c] = rs.map((r) => (r.status === 'fulfilled' ? r.value : null));
    // 못 받은 원천은 그 까닭을 칸에 — 404 no_data 는 '아직 없음 — <작업>'(실패가 아니다), 그 밖은 상태 코드만
    const keys = ['v', 'f', 'o', 'i', 'c'] as const;
    const hints = [HINT.valuation, HINT.fin, HINT.derive, HINT.derive, HINT.consensus] as const;
    const why: Partial<Record<(typeof keys)[number], string>> = {};
    rs.forEach((r, k) => {
      const key = keys[k];
      if (r.status === 'rejected' && key) why[key] = errLine(r.reason, hints[k] ?? HINT.market);
    });
    pCard.clearBody();
    pCard.body.append(
      kv(
        metricsItems(
          v as Envelope<S['Valuation']> | null,
          f as Envelope<S['Financials']> | null,
          o as Envelope<S['Overhang']> | null,
          i as Envelope<S['Insiders']> | null,
          c as Envelope<S['Consensus']> | null,
          why,
        ),
        { layout: 'rows' },
      ),
    );
  });
  const rec = Promise.allSettled([
    t.get<S['Disclosures']>(`stock/${code}/disclosures`, { days: 30 }),
    t.get<S['Earnings']>(`stock/${code}/earnings`),
  ]).then(([d, e]) => {
    recent(
      pRecent,
      d.status === 'fulfilled' ? d.value.data.rows : errLine(d.reason, HINT.feed),
      e.status === 'fulfilled' ? e.value.data.prelims : errLine(e.reason, HINT.earnings),
      true,
    );
    const src = d.status === 'fulfilled' ? d.value : e.status === 'fulfilled' ? e.value : null;
    if (src) pRecent.setSource({ ...src, notes: [] });
  });
  await Promise.all([
    fill(
      pChart,
      () => t.get<S['PriceChart']>(`stock/${code}/chart`, { days: 250 }),
      (env) => {
        pChart.body.append(priceChart(env.data));
        return 0;
      },
      HINT.market,
    ),
    fill(pFlow, () => t.get<S['StockFlowDetail']>(`flows/stock/${code}`, { days: 20 }), (env) => drawDetail(pFlow, env), HINT.flows),
    card,
    rec,
  ]);
}
