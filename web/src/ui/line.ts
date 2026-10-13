// 선 차트 — 여러 선·끊긴 구간·끝점 이름표·0 기준선·선 켜고 끄기(docs/p5_design.md §1.1·§6 — F2 분 단위 투자자 선·MAC 시리즈).
//
// - 없는 값(null·NaN)은 잇지 않는다: 값이 이어진 구간(run)마다 path 하나. 점 하나뿐인 구간은 작은 원으로 그린다
//   (0 으로 그리지 않고 건너뛰지도 않는다 — 절대 규칙 1·4).
// - 0 기준선: 값 범위가 0 을 가로지르거나 zero=true 면 0 을 범위에 넣고 기준선을 그린다.
// - 끝점 이름표: 선마다 마지막 값 자리에 '이름 값'. 켜진 선만.
// - 켜고 끄기(toggle=true): 범례가 버튼(aria-pressed). 끄면 그 선을 빼고 범위를 다시 잡아 다시 그린다.
// - SVG 는 viewBox 만(폭은 CSS 100%) — 모바일 폭에서 가로 넘침 없음. 색은 스킨 토큰(CSS 변수)만.
import { h } from './dom';
import { DASH, num, type Num } from './fmt';
import { s, svgBox } from './svg';

export interface LineSeries {
  label: string;
  /** x 와 같은 길이 — 없는 값은 null */
  values: readonly Num[];
  /** 선 색(CSS 변수) — 없으면 LINE_COLORS 차례 */
  color?: string;
}

export interface LineOptions {
  /** 접근 이름 — 예 '시장 투자자별 순매수(분)' */
  label: string;
  /** x 축 이름(시각·날짜) — 처음·가운데·끝만 적는다 */
  x: readonly string[];
  series: readonly LineSeries[];
  /** 0 을 범위에 넣고 기준선을 그린다(기본: 범위가 0 을 가로지를 때만) */
  zero?: boolean;
  /** 끝점 이름표(기본 true) */
  endLabels?: boolean;
  /** 범례 버튼으로 선 켜고 끄기(기본 false — 범례는 글자만) */
  toggle?: boolean;
  /** 처음에 꺼 둘 선 이름 */
  hidden?: readonly string[];
  /** 값 글자(끝점 이름표·title) — 기본 num(v) */
  format?: (v: number) => string;
  w?: number;
  h?: number;
}

export const LINE_COLORS: readonly string[] = [
  'var(--accent)',
  'var(--up)',
  'var(--down)',
  'var(--warn)',
  'var(--est)',
  'var(--fg)',
  'var(--dim)',
  'var(--lock)',
];

function ok(v: Num): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/** 값이 이어진 구간들 — [x 위치, 값] 목록의 목록(순수 계산 — 시험용). */
export function lineRuns(values: readonly Num[]): [number, number][][] {
  const runs: [number, number][][] = [];
  let cur: [number, number][] = [];
  values.forEach((v, i) => {
    if (ok(v)) cur.push([i, v]);
    else if (cur.length) {
      runs.push(cur);
      cur = [];
    }
  });
  if (cur.length) runs.push(cur);
  return runs;
}

/** 켜진 선들의 값 범위(zero 면 0 포함, 0 을 가로지르면 기준선). 값이 없으면 null. */
export function lineRange(
  series: readonly LineSeries[],
  zero = false,
): { lo: number; hi: number; baseline: boolean } | null {
  let lo = Infinity;
  let hi = -Infinity;
  for (const sr of series) {
    for (const v of sr.values) {
      if (!ok(v)) continue;
      lo = Math.min(lo, v);
      hi = Math.max(hi, v);
    }
  }
  if (lo > hi) return null;
  const crosses = lo < 0 && hi > 0;
  if (zero) {
    lo = Math.min(lo, 0);
    hi = Math.max(hi, 0);
  }
  if (lo === hi) {
    const pad = lo === 0 ? 1 : Math.abs(lo) * 0.05;
    lo -= pad;
    hi += pad;
  }
  return { lo, hi, baseline: zero || crosses };
}

function colorOf(sr: LineSeries, k: number): string {
  return sr.color ?? LINE_COLORS[k % LINE_COLORS.length] ?? 'var(--accent)';
}

function draw(box: SVGSVGElement, o: LineOptions, on: ReadonlySet<number>): void {
  while (box.firstChild) box.removeChild(box.firstChild);
  const W = o.w ?? 640;
  const H = o.h ?? 220;
  const top = 10;
  const bottom = 20;
  const left = 8;
  const right = (o.endLabels ?? true) ? 88 : 8;
  const fmt = o.format ?? ((v: number) => num(v));
  const shown = o.series.map((sr, k) => ({ sr, k })).filter(({ k }) => on.has(k));
  const range = lineRange(
    shown.map(({ sr }) => sr),
    o.zero,
  );
  const n = o.x.length;
  if (n === 0 || range === null) {
    s('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle' }, box, '데이터 없음');
    box.dataset.empty = 'true';
    return;
  }
  delete box.dataset.empty;
  const plotW = W - left - right;
  const plotH = H - top - bottom;
  const xOf = (i: number): number => left + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const yOf = (v: number): number => top + (1 - (v - range.lo) / (range.hi - range.lo)) * plotH;
  if (range.baseline) {
    s('line', { class: 'zero', x1: left, x2: left + plotW, y1: yOf(0), y2: yOf(0), stroke: 'var(--dim)' }, box);
  }
  for (const { sr, k } of shown) {
    const color = colorOf(sr, k);
    const g = s('g', { 'data-series': k }, box);
    lineRuns(sr.values).forEach((run, r) => {
      if (run.length === 1) {
        const [i, v] = run[0] ?? [0, 0];
        const dot = s('circle', { cx: xOf(i), cy: yOf(v), r: 2, fill: color, 'data-run': r }, g);
        s('title', {}, dot, `${sr.label} ${o.x[i] ?? DASH} ${fmt(v)}`);
        return;
      }
      const d = run.map(([i, v], j) => `${j === 0 ? 'M' : 'L'}${xOf(i).toFixed(2)},${yOf(v).toFixed(2)}`).join(' ');
      const path = s('path', { d, fill: 'none', stroke: color, 'stroke-width': 1.5, 'data-run': r }, g);
      s('title', {}, path, sr.label);
    });
    if (o.endLabels ?? true) {
      const lastRun = lineRuns(sr.values).at(-1);
      const last = lastRun?.at(-1);
      if (last) {
        const [i, v] = last;
        s('text', { class: 'end', x: xOf(i) + 4, y: yOf(v) + 3, fill: color }, g, `${sr.label} ${fmt(v)}`);
      }
    }
  }
  const ticks = n <= 1 ? [0] : [0, Math.floor((n - 1) / 2), n - 1];
  for (const i of [...new Set(ticks)]) {
    const anchor = i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle';
    s('text', { x: xOf(i), y: H - 6, 'text-anchor': n <= 1 ? 'middle' : anchor }, box, o.x[i] ?? '');
  }
}

export interface LineChart {
  readonly el: HTMLDivElement;
  /** 선 켜고 끄기(범례 버튼과 같은 동작) — 바뀐 뒤 켜져 있으면 true */
  toggle(index: number): boolean;
  /** 켜진 선 번호들 */
  visible(): number[];
}

export function lineChart(o: LineOptions): LineChart {
  const hidden = new Set(o.hidden ?? []);
  const on = new Set(o.series.map((_, k) => k).filter((k) => !hidden.has(o.series[k]?.label ?? '')));
  const box = svgBox(`0 0 ${o.w ?? 640} ${o.h ?? 220}`, o.label, 'linechart');
  const el = h('div', { class: 'linechart' }, box);
  const buttons: HTMLButtonElement[] = [];
  const flip = (k: number): boolean => {
    if (on.has(k)) on.delete(k);
    else on.add(k);
    buttons[k]?.setAttribute('aria-pressed', String(on.has(k)));
    draw(box, o, on);
    return on.has(k);
  };
  const legend = h(
    'ul',
    { class: 'legend' },
    o.series.map((sr, k) => {
      const sw = h('span', { class: 'sw', attrs: { 'aria-hidden': 'true' }, css: { background: colorOf(sr, k) } });
      if (!o.toggle) return h('li', null, sw, sr.label);
      const b = h('button', { attrs: { type: 'button', 'aria-pressed': String(on.has(k)) } }, sw, sr.label);
      b.addEventListener('click', () => flip(k));
      buttons[k] = b;
      return h('li', null, b);
    }),
  );
  el.append(legend);
  draw(box, o, on);
  return {
    el,
    toggle: flip,
    visible: () => [...on].sort((a, b) => a - b),
  };
}
