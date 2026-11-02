// 페이지 3 '상대 시총·연도별 수익률'(stock-dashboard 화면을 옮김, 2026-11-02).
//
// - 로그인(야후·KIS 시총 — 공개판은 자물쇠, 요청하지 않는다). API 'market/relcap'(period 6M·1Y·2Y).
// - 맨 위 요약 카드(쌍마다 지금 비율·기간 고저·기간 안 위치 꼬리표) → 고른 쌍의 추이 차트 → 지수 연도별 수익률 표(부호 색).
// - 값이 없으면 '—'와 사유(0 으로 그리지 않는다).
import type { components } from '../../api/types.gen';
import type { PageContext } from '../../app/types';
import type { Envelope } from '../../data/source';
import { h, replace } from '../../ui/dom';
import { num, pct, signClass } from '../../ui/fmt';
import { lineChart } from '../../ui/line';
import { failText, type Panel, panel } from '../../ui/panel';
import { seg } from '../../ui/seg';
import { type StatCard, type StatTone, statGrid } from '../../ui/stat';
import { table } from '../../ui/table';

type S = components['schemas'];
export type RelCap = S['RelCap'];
type Period = RelCap['period'];
type Pair = S['RelCapPair'];
type AnnualRow = S['AnnualIndexRow'];

export const RELCAP_TITLE = '상대 시총·연도별 수익률';
export const RELCAP_HINT = 'global.daily 07:25(야후 일봉·월봉·시총)';

const md = (d: string | null | undefined): string | null => (d ? `${d.slice(5, 7)}/${d.slice(8, 10)}` : null);

function rangeZone(p: Pair): { zone: string | null; tone: StatTone } {
  const r = p.pct_of_range;
  if (r === null) return { zone: null, tone: null };
  if (r >= 80) return { zone: '기간 상단', tone: 'up' };
  if (r <= 20) return { zone: '기간 하단', tone: 'down' };
  return { zone: '기간 중간', tone: null };
}

export function relcapCards(d: RelCap): StatCard[] {
  return d.pairs.map((p): StatCard => {
    const z = rangeZone(p);
    return {
      label: p.name,
      asof: md(p.date),
      value: p.latest === null ? null : `${num(p.latest, 1)}%`,
      zone: z.zone,
      tone: z.tone,
      sub: p.error ? p.error : `${d.period} 고점 ${num(p.high ?? null, 1)}% · 저점 ${num(p.low ?? null, 1)}%`,
      subTone: p.error ? 'dim' : null,
      title: `${p.legs_a.join('+')} 시총 ÷ ${p.legs_b.join('+')} 시총(달러 환산) × 100`,
    };
  });
}

export function pairChart(p: Pair | undefined): HTMLElement {
  const fig = h('figure', null, h('figcaption', null, p ? `${p.name} — 시총 비율(%)` : '쌍 없음'));
  if (!p || p.series.length === 0) {
    fig.append(h('p', { class: 'msg' }, p?.error ?? '이력 없음'));
    return fig;
  }
  fig.append(
    lineChart({
      label: p.name,
      x: p.series.map((s) => s.date),
      series: [{ label: p.name, values: p.series.map((s) => s.value ?? null) }],
      format: (v) => `${num(v, 1)}%`,
      h: 220,
    }).el,
  );
  return fig;
}

export function annualTable(d: RelCap): HTMLElement {
  const years = d.years.slice(0, 10);
  const val = (r: AnnualRow, y: number): number | null => r.returns.find((x) => x.year === y)?.pct ?? null;
  return table<AnnualRow>({
    caption: `주요 지수 연도별 수익률(가격 지수 — 배당 제외, ${years[0] ?? ''} 은 연초 대비)`,
    rowKey: (r) => r.symbol,
    rows: d.annual,
    columns: [
      { key: 'name', label: '지수', align: 'l', cell: (r) => r.name },
      ...years.map((y) => ({
        key: String(y),
        label: String(y),
        cell: (r: AnnualRow) => {
          const v = val(r, y);
          return v === null ? (r.error ?? null) : pct(v, 1);
        },
        cls: (r: AnnualRow) => signClass(val(r, y)),
      })),
    ],
  }).el;
}

/** 틀만(공개 빌드는 이것만 쓴다 — 자물쇠, 요청 없음) */
export function relcapPanel(ctx: PageContext): Panel {
  const p = panel(ctx.tier, {
    title: RELCAP_TITLE,
    tier: 'login',
    lock: '해외 시세(야후)·KIS 시총 — 로그인 후 표시',
    span: 12,
    src: 'Yahoo·KIS',
    def: '상대 시총 = A 시가총액 합 ÷ B 시가총액 합(달러 환산) × 100. 100 위면 A 가 더 크다. 과거 값은 현재 주식 수가 그대로였다고 보고 그날 종가로 거슬러 계산한다(자사주 소각·증자 구간은 어긋남). 꼬리표 = 기간 저점~고점 중 지금 위치. 아래 표는 주요 지수의 연말 종가 대비 수익률.',
  });
  ctx.root.append(p.el);
  return p;
}

export async function mountRelcap(ctx: PageContext): Promise<void> {
  const p = relcapPanel(ctx);
  if (p.locked) return;
  const q = { period: '1Y' as Period, pair: 0 };
  const controls = h('div', { class: 'filters' });
  const pairBox = h('div', { class: 'filters' });
  let data: RelCap | null = null;
  const chartBox = h('div');
  const drawChart = (): void => {
    if (data) replace(chartBox, pairChart(data.pairs[q.pair]));
  };
  const load = async (): Promise<void> => {
    let env: Envelope<RelCap>;
    try {
      env = await ctx.source.get<RelCap>('market/relcap', { period: q.period });
    } catch (err) {
      replace(p.body, controls, h('p', { class: 'msg' }, failText(err, RELCAP_HINT)));
      return;
    }
    p.setSource(env);
    data = env.data;
    q.pair = Math.min(q.pair, Math.max(0, data.pairs.length - 1));
    replace(
      pairBox,
      seg<string>({
        label: '쌍',
        options: data.pairs.map((x, i) => ({ value: String(i), label: x.name })),
        value: String(q.pair),
        onChange: (v) => {
          q.pair = Number(v);
          drawChart();
        },
      }).el,
    );
    drawChart();
    replace(p.body, controls, statGrid(relcapCards(data)), pairBox, chartBox, annualTable(data));
  };
  controls.append(
    seg<Period>({
      label: '기간',
      options: [
        { value: '6M', label: '6M' },
        { value: '1Y', label: '1Y' },
        { value: '2Y', label: '2Y' },
      ],
      value: q.period,
      onChange: (v) => {
        q.period = v;
        void load();
      },
    }).el,
  );
  await load();
}
