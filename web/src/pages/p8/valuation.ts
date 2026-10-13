// 페이지 8 '컨센·밸류' 탭의 밸류에이션 패널(로그인 — 시세 기반, docs/p4_design.md §4.8·§6.2, ADR 0016).
//
// - 시점 일관(PIT): 그날까지 공시된 정기보고서 분기만으로 만든 배수. 회사 시총(보통 + 우선) 기준 — 우선주도 같은 배수.
// - 밴드 차트: 가격선 + 배수 밴드 다섯 줄(P10·P25·P50·P75·P90). 밴드가 = 배수_p × 그날 주당 기준값(= 종가 ÷ 그날 배수)
//   — 화면에서만 계산(ui/band.ts bandPrices). 배수가 없는 날(적자 등)은 점을 비운다.
// - 칸마다 적용 불가 사유(적자 PER·자본잠식 PBR·금융업 EV/EBITDA·상장 1년 미만 밴드 …) — 0 으로 그리지 않는다.
// - 공개판: 자물쇠('시세 기반'), 요청 없음.
import { bandChart, bandPrices } from '../../ui/band';
import { append, h } from '../../ui/dom';
import { DASH, joEok, kstDate, num, pct } from '../../ui/fmt';
import { kv } from '../../ui/kv';
import type { Panel } from '../../ui/panel';
import { EST_LABEL, qValue } from '../../ui/quality';
import { type Option, select } from '../../ui/seg';
import { table } from '../../ui/table';
import { fill, filters } from '../p1_market';
import { col, HINT, mkPanel, naText, type S, shares, tabGrid, type TabCtx, won } from './common';

type V = S['Valuation'];
type Metric = V['metric'];
type Band = S['BandView'];

export const METRICS: readonly Option<Metric>[] = [
  { value: 'per', label: 'PER' },
  { value: 'pbr', label: 'PBR' },
  { value: 'psr', label: 'PSR' },
  { value: 'ev_ebitda', label: 'EV/EBITDA' },
];
const LABEL: Record<Metric, string> = { per: 'PER', pbr: 'PBR', psr: 'PSR', ev_ebitda: 'EV/EBITDA' };
const x2 = (v: number | null): string => (v === null ? DASH : `${num(v, 2)}x`);

/** 배수 칸 하나 — 적용 불가는 사유, 거름값 밖은 '범위 밖' 꼬리, 추정은 배지 */
export function cellText(c: S['MultipleCell'] | null | undefined, what: string, q: S['ValuationCurrent']['quality']): Node | string | null {
  if (!c) return null;
  if (!c.applicable) return h('span', { class: 'dim' }, naText(c.na_reason, what));
  const el = qValue(x2(c.value), q, null, undefined, EST_LABEL);
  if (el && c.out_of_cap) el.append(h('span', { class: 'tag warn' }, '범위 밖'));
  return el;
}

/** 밴드 차트(그 지표) — 밴드 적용 불가면 사유 줄 */
export function bandBlock(d: V): HTMLElement {
  const name = LABEL[d.metric];
  const b = d.bands.find((x) => x.metric === d.metric);
  if (!b?.applicable) return h('p', { class: 'msg', data: { na: b?.na_reason ?? '' } }, b ? naText(b.na_reason, name) : `${name} 밴드 없음`);
  // 그날 주당 기준값 = 종가 ÷ 그날 배수(배수가 없거나 0 이하면 빈 칸)
  const basis = d.series.map((p) => (p.price !== null && p.value !== null && p.value > 0 ? p.price / p.value : null));
  const ms = [b.p10, b.p25, b.p50, b.p75, b.p90];
  return h(
    'div',
    { data: { section: 'band' } },
    bandChart({
      label: `${d.code} ${name} 밴드 5년`,
      dates: d.series.map((p) => p.date),
      price: d.series.map((p) => p.price),
      bands: bandPrices(basis, ms).map((values, i) => ({ label: `P${[10, 25, 50, 75, 90][i] ?? ''}`, values, multiple: ms[i] ?? null })),
    }),
    h('p', { class: 'def' }, `${name} 5년 분포 · 유효 ${num(b.n_days)}일(제외 ${num(b.n_excluded)}일) · 현재 ${x2(b.current)} = 하위 ${pct(b.current_rank_pct, 0, false)}`),
  );
}

export function drawValuation(p: Panel, d: V): void {
  if (!d.applicable) {
    p.showMessage(naText(d.na_reason));
    return;
  }
  const cur = d.current;
  const q = cur?.quality ?? 'ok';
  const band = (m: Metric, f: (b: Band) => string): Node | string | null => {
    const b = d.bands.find((x) => x.metric === m);
    return !b ? null : b.applicable ? f(b) : h('span', { class: 'dim' }, naText(b.na_reason, LABEL[m]));
  };
  append(p.body, [
    bandBlock(d),
    table<Metric>({
      caption: `배수 — ${cur ? `${kstDate(cur.date)} 종가 ${won(cur.price)}` : '현재 배수 없음'}`,
      columns: [
        col('배수', (m) => LABEL[m], true),
        col('현재(TTM)', (m) => cellText(cur?.[m], LABEL[m], q)),
        col('선행(NTM)', (m) => (m === 'pbr' ? null : cellText(cur?.[`${m}_fwd`], `선행 ${LABEL[m]}`, q))),
        col('5년 P10', (m) => band(m, (b) => x2(b.p10))),
        col('P50', (m) => band(m, (b) => x2(b.p50))),
        col('P90', (m) => band(m, (b) => x2(b.p90))),
        col('현재 백분위', (m) => band(m, (b) => pct(b.current_rank_pct, 0, false))),
      ],
      rows: METRICS.map((m) => m.value),
    }).el,
    kv([
      { label: '회사 시총(보통 + 우선)', value: joEok(d.mktcap_company) },
      { label: 'EV', value: joEok(cur?.ev), hint: '오버행 미포함' },
      { label: 'TTM 기준 분기', value: cur?.ttm_ref, hint: '그날까지 공시(PIT)' },
    ]),
    d.classes.length > 1
      ? table<S['ClassCap']>({
          caption: '종류별 시총',
          columns: [
            col('코드', (r) => `${r.code} ${r.share_class === 'pref' ? '우선주' : '보통주'}`, true),
            col('종가', (r) => won(r.close)),
            col('상장주식수', (r) => shares(r.shares)),
            col('시총', (r) => joEok(r.mktcap)),
          ],
          rows: d.classes,
        }).el
      : null,
  ]);
}

export async function mount(t: TabCtx, el: HTMLElement): Promise<void> {
  const p = mkPanel(t, { title: '밸류에이션 밴드', tier: 'login', span: 12, lock: 'valuation' });
  tabGrid(el, p);
  if (p.locked) return;
  p.setDef('시점 일관(PIT) — 그날까지 공시된 분기만. 회사 시총 기준이라 우선주도 같은 배수. 오버행은 EV 에 넣지 않는다.');
  let metric: Metric = 'per';
  const load = (): Promise<void> =>
    fill(
      p,
      () => t.get<V>(`stock/${t.code}/valuation`, { metric }),
      (env) => {
        drawValuation(p, env.data);
        return 0;
      },
      HINT.valuation,
    );
  filters(
    p,
    select<Metric>({
      label: '밴드 지표',
      options: METRICS,
      value: metric,
      onChange: (v) => {
        metric = v;
        void load();
      },
    }).el,
  );
  await load();
}
