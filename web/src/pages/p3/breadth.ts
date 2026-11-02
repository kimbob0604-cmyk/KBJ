// 페이지 3 '시장 체력' — ADR(20거래일 등락비율)·지수 이격도(stock-dashboard 위험지표 화면을 옮김, 2026-11-02).
//
// - 로그인(KRX 일봉 기반 — 공개판은 자물쇠, 요청하지 않는다). API 'diag/breadth-risk'(period·ma).
// - 맨 위 요약 카드(지금 값·구간 꼬리표·그날 상승/하락 수) → 아래 차트 둘(ADR 시장 둘 + 과열·바닥 선, 이격도 시장 둘 +
//   100 선). 기간 3M·6M·1Y, 이격도 25·50일.
// - 값이 없으면 '—'와 사유(0 으로 그리지 않는다).
import type { components } from '../../api/types.gen';
import type { PageContext } from '../../app/types';
import type { Envelope } from '../../data/source';
import { h, replace } from '../../ui/dom';
import { num } from '../../ui/fmt';
import { lineChart } from '../../ui/line';
import { failText, type Panel, panel } from '../../ui/panel';
import { seg } from '../../ui/seg';
import { type StatCard, type StatTone, statGrid } from '../../ui/stat';

type S = components['schemas'];
export type BreadthRisk = S['BreadthRisk'];
type Period = BreadthRisk['period'];

export const BREADTH_TITLE = '시장 체력 — ADR·이격도';
export const BREADTH_HINT = 'krx.daily 08:05(종목·지수 일봉)';
const MARKET: Record<'KOSPI' | 'KOSDAQ', string> = {
  KOSPI: '코스피',
  KOSDAQ: '코스닥',
};

function zoneTone(z: string | null | undefined): StatTone {
  if (z === '과열') return 'warn';
  if (z === '바닥권') return 'down';
  return null;
}

function dispTone(v: number | null | undefined): StatTone {
  if (v === null || v === undefined) return null;
  if (v >= 105) return 'warn';
  if (v <= 95) return 'down';
  return null;
}

const md = (d: string | null | undefined): string | null => (d ? `${d.slice(5, 7)}/${d.slice(8, 10)}` : null);

export function breadthCards(d: BreadthRisk): StatCard[] {
  const adr = d.adr.map(
    (a): StatCard => ({
      label: `ADR ${MARKET[a.market]}`,
      asof: md(a.date),
      value: a.latest === null ? null : `${num(a.latest, 1)}%`,
      zone: a.zone ?? null,
      tone: zoneTone(a.zone),
      sub: a.up === null ? null : `그날 상승 ${num(a.up)} · 하락 ${num(a.down)} · 보합 ${num(a.flat)}`,
      title: `${d.window}거래일 상승 종목 수 합 ÷ 하락 종목 수 합 × 100 — ${num(d.hot)} 이상 과열, ${num(d.cold)} 이하 바닥권`,
    }),
  );
  const disp = d.disparity.map(
    (x): StatCard => ({
      label: `이격도 ${MARKET[x.market]} ${d.ma}일`,
      asof: md(x.date),
      value: x.latest === null ? null : num(x.latest, 1),
      tone: dispTone(x.latest),
      sub:
        x.latest === null
          ? null
          : `${d.ma}일 평균보다 ${num(Math.abs(x.latest - 100), 1)}% ${x.latest >= 100 ? '위' : '아래'}`,
      subTone: x.latest === null ? null : x.latest >= 100 ? 'up' : 'down',
      title: `지수 종가 ÷ ${d.ma}일 이동평균 × 100`,
    }),
  );
  return [...adr, ...disp];
}

function chart(label: string, series: { label: string; values: (number | null)[] }[], x: string[]): HTMLElement {
  const fig = h('figure', null, h('figcaption', null, label));
  if (!series.some((s) => s.values.some((v) => v !== null))) {
    fig.append(h('p', { class: 'msg' }, '이력 없음'));
    return fig;
  }
  fig.append(
    lineChart({
      label,
      x,
      series,
      format: (v) => num(v, 1),
      h: 200,
      toggle: true,
    }).el,
  );
  return fig;
}

export function breadthCharts(d: BreadthRisk): HTMLElement {
  const adrX = d.adr[0]?.series.map((p) => p.date) ?? [];
  const adrSeries = d.adr.map((a) => ({
    label: `ADR ${MARKET[a.market]}`,
    values: a.series.map((p) => p.value ?? null),
  }));
  const ref = (v: number, label: string) => ({
    label,
    values: adrX.map(() => v),
  });
  const dispX = d.disparity[0]?.series.map((p) => p.date) ?? [];
  const dispSeries = d.disparity.map((x) => ({
    label: MARKET[x.market],
    values: x.series.map((p) => p.value ?? null),
  }));
  return h(
    'div',
    { class: 'charts2' },
    chart(
      'ADR(%) — 과열·바닥 선',
      [...adrSeries, ref(d.hot, `과열 ${num(d.hot)}`), ref(d.cold, `바닥 ${num(d.cold)}`)],
      adrX,
    ),
    chart(`지수 이격도(${d.ma}일)`, [...dispSeries, { label: '100', values: dispX.map(() => 100) }], dispX),
  );
}

/** 틀만(공개 빌드는 이것만 쓴다 — 자물쇠, 요청 없음) */
export function breadthPanel(ctx: PageContext): Panel {
  const p = panel(ctx.tier, {
    title: BREADTH_TITLE,
    tier: 'login',
    lock: 'quote_based',
    span: 12,
    src: 'KRX 일봉',
    def: 'ADR(등락비율) = 최근 20거래일 동안 오른 종목 수 합 ÷ 내린 종목 수 합 × 100. 120 이상은 많은 종목이 함께 오른 과열, 80 이하는 바닥권으로 흔히 읽는다. 이격도 = 지수 종가 ÷ 이동평균 × 100 — 100 위면 평균보다 높게 거래 중. 그날 값은 다음 날 아침(KRX 확정)에 들어온다.',
  });
  ctx.root.append(p.el);
  return p;
}

export async function mountBreadth(ctx: PageContext): Promise<void> {
  const p = breadthPanel(ctx);
  if (p.locked) return;
  const q = { period: '6M' as Period, ma: '50' as '25' | '50' };
  const controls = h('div', { class: 'filters' });
  const load = async (): Promise<void> => {
    let env: Envelope<BreadthRisk>;
    try {
      env = await ctx.source.get<BreadthRisk>('diag/breadth-risk', {
        period: q.period,
        ma: q.ma,
      });
    } catch (err) {
      replace(p.body, controls, h('p', { class: 'msg' }, failText(err, BREADTH_HINT)));
      return;
    }
    p.setSource(env);
    replace(p.body, controls, statGrid(breadthCards(env.data)), breadthCharts(env.data));
  };
  controls.append(
    seg<Period>({
      label: '기간',
      options: [
        { value: '3M', label: '3M' },
        { value: '6M', label: '6M' },
        { value: '1Y', label: '1Y' },
      ],
      value: q.period,
      onChange: (v) => {
        q.period = v;
        void load();
      },
    }).el,
    seg<'25' | '50'>({
      label: '이격도',
      options: [
        { value: '25', label: '25일' },
        { value: '50', label: '50일' },
      ],
      value: q.ma,
      onChange: (v) => {
        q.ma = v;
        void load();
      },
    }).el,
  );
  await load();
}
