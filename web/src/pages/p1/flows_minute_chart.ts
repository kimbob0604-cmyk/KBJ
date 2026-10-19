// 페이지 1 '장중 투자자 수급' 차트 — HTS 시장별 시간동향처럼 한 장에 투자자 누적 순매수 선 + 지수 선.
//
// - 선은 끊지 않는다: 값이 있는 분끼리는 실선, 못 받은 분(null)을 건너 잇는 구간은 옅은 점선(값을 지어내지
//   않았다는 표시 — 서버는 그 분을 null 로 준다). 첫 값 앞·마지막 값 뒤(아직 오지 않은 분)는 그리지 않는다.
//   점 하나짜리 조각을 따로 그리지 않는다.
// - 오른쪽 세로축 = 억원 눈금(천 단위 쉼표, 0 기준선). 지수는 따로 정규화한 축(왼쪽 눈금)이라 값 눈금을
//   섞지 않는다. 지수 값은 장중 10분 값이라 10분 간격 점을 실선으로 잇고, 그보다 큰 빈칸만 점선.
// - 오른쪽 끝에 선마다 현재값 꼬리표(색 바탕) — 겹치면 위아래로 비켜 놓는다(nudgeLabels).
// - 아래 시간축 = 분 축 전체(09:00~15:40) 정시 눈금 + 날짜 + 마지막 갱신 시각.
// - 범례(위 한 줄)가 켜고 끄는 버튼 — 기본 개인·외국인·기관계·지수, 기관 세부는 숨김.
// - 마우스를 올리면 세로선과 그 분 값(없는 분은 —).
// - SVG 는 viewBox 만(폭 100%) — 모바일 가로 넘침 없음. 색은 CSS 변수(없으면 HTS 관례 색).
import { h } from '../../ui/dom';
import { DASH } from '../../ui/fmt';
import { s, svgBox } from '../../ui/svg';

export type Pt = readonly [number, number];

export interface MinuteSeries {
  id: string;
  label: string;
  color: string;
  /** 분 축과 같은 길이 — 억원, 없는 분 null */
  values: readonly (number | null)[];
}

export interface MinuteIndexLine {
  label: string;
  values: readonly (number | null)[];
  last: number | null;
  chgPct: number | null;
}

export interface MinuteChartOptions {
  label: string;
  /** 'HH:MM' 분 축 */
  axis: readonly string[];
  series: readonly MinuteSeries[];
  /** 처음에 숨길 id(지수는 'index') */
  hidden?: readonly string[];
  index?: MinuteIndexLine | null;
  /** 'MM/DD' */
  date: string;
  /** 마지막 갱신 'HH:MM' */
  updated: string | null;
}

export const INDEX_ID = 'index';
const INDEX_COLOR = 'var(--fg)';
const INDEX_STEP = 10; // 지수는 10분 값 — 이 간격까지는 실선

const W = 640;
const H = 260;
const TOP = 10;
const BOTTOM = 30;
const LEFT = 58;
const RIGHT = 112;
const TAG_H = 13;

function finite(v: number | null | undefined): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/**
 * 값 → (실선 조각들, 점선 다리들). 값이 있는 분끼리 maxStep 칸 이내면 같은 실선 조각, 더 벌어지면 그 사이를
 * 점선 다리로 잇는다. 첫 값 앞·마지막 값 뒤는 비운다(순수 계산 — 시험용).
 */
export function bridgeSegments(
  values: readonly (number | null)[],
  maxStep = 1,
): { solid: Pt[][]; gaps: [Pt, Pt][] } {
  const pts: Pt[] = [];
  values.forEach((v, i) => {
    if (finite(v)) pts.push([i, v]);
  });
  const solid: Pt[][] = [];
  const gaps: [Pt, Pt][] = [];
  let cur: Pt[] = [];
  pts.forEach((p, k) => {
    const prev = pts[k - 1];
    if (prev && p[0] - prev[0] > maxStep) {
      if (cur.length > 1) solid.push(cur);
      gaps.push([prev, p]);
      cur = [];
    }
    cur.push(p);
  });
  if (cur.length > 1) solid.push(cur);
  return { solid, gaps };
}

/**
 * 꼬리표 y(가운데)들을 최소 간격 gap 이상 벌린다 — 원래 순서를 지키며 위에서 아래로 밀고, 아래 끝(hi)을
 * 넘치면 위로 되민다. 반환은 입력 순서 그대로(순수 계산 — 시험용).
 */
export function nudgeLabels(ys: readonly number[], gap: number, lo: number, hi: number): number[] {
  const order = ys.map((y, i) => ({ y, i })).sort((a, b) => a.y - b.y || a.i - b.i);
  const out = order.map((o) => Math.min(Math.max(o.y, lo), hi));
  for (let k = 1; k < out.length; k++) {
    const prev = out[k - 1] ?? lo;
    if ((out[k] ?? 0) - prev < gap) out[k] = prev + gap;
  }
  const last = out.length - 1;
  if (last >= 0 && (out[last] ?? 0) > hi) {
    out[last] = hi;
    for (let k = last - 1; k >= 0; k--) {
      const next = out[k + 1] ?? hi;
      if (next - (out[k] ?? 0) < gap) out[k] = next - gap;
    }
  }
  const res = new Array<number>(ys.length);
  order.forEach((o, k) => {
    res[o.i] = out[k] ?? o.y;
  });
  return res;
}

/** 범위 → 보기 좋은 눈금 간격(1·2·2.5·5 × 10^k, 눈금 4~6개). */
export function niceStep(span: number, target = 4): number {
  if (!(span > 0)) return 1;
  const raw = span / target;
  const mag = 10 ** Math.floor(Math.log10(raw));
  for (const m of [1, 2, 2.5, 5, 10]) {
    if (raw <= m * mag) return m * mag;
  }
  return 10 * mag;
}

/** 억원 눈금 글자 — 천 단위 쉼표(예: -2,500). */
export function eokTick(v: number): string {
  const r = Math.round(v * 100) / 100;
  return (Object.is(r, -0) ? 0 : r).toLocaleString('ko-KR', { maximumFractionDigits: 2 });
}

/** 현재값 꼬리표 글자 — '+564억'. */
export function eokTag(v: number): string {
  const r = Math.round(v);
  return `${r > 0 ? '+' : ''}${r.toLocaleString('ko-KR')}억`;
}

function indexText(v: number): string {
  return v.toLocaleString('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function pctText(v: number | null): string {
  if (!finite(v)) return '';
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`;
}

/** 글자 폭 어림(px, font-size 10) — 한글 10, 그 밖 6. */
function textW(t: string): number {
  let w = 0;
  for (const ch of t) w += (ch.codePointAt(0) ?? 0) > 0x1100 ? 10 : 6;
  return w;
}

function range(vals: Iterable<number>, withZero: boolean): { lo: number; hi: number } | null {
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of vals) {
    lo = Math.min(lo, v);
    hi = Math.max(hi, v);
  }
  if (lo > hi) return null;
  if (withZero) {
    lo = Math.min(lo, 0);
    hi = Math.max(hi, 0);
  }
  if (lo === hi) {
    const pad = lo === 0 ? 1 : Math.abs(lo) * 0.05;
    return { lo: lo - pad, hi: hi + pad };
  }
  const pad = (hi - lo) * 0.06;
  return { lo: lo - pad, hi: hi + pad };
}

function pathOf(pts: readonly Pt[], x: (i: number) => number, y: (v: number) => number): string {
  return pts.map(([i, v], k) => `${k === 0 ? 'M' : 'L'}${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(' ');
}

interface Drawn {
  x: (i: number) => number;
  iAt: (px: number) => number;
}

function drawLine(
  g: SVGGElement,
  values: readonly (number | null)[],
  color: string,
  maxStep: number,
  x: (i: number) => number,
  y: (v: number) => number,
  width: number,
): void {
  const { solid, gaps } = bridgeSegments(values, maxStep);
  for (const run of solid) {
    s('path', { class: 'run', d: pathOf(run, x, y), fill: 'none', stroke: color, 'stroke-width': width, 'stroke-linejoin': 'round' }, g);
  }
  for (const [a, b] of gaps) {
    s(
      'path',
      { class: 'gap', d: pathOf([a, b], x, y), fill: 'none', stroke: color, 'stroke-width': width, 'stroke-dasharray': '3 3', opacity: 0.5 },
      g,
    );
  }
}

function tag(box: SVGSVGElement, x: number, yMid: number, text: string, color: string, anchor: 'start' | 'end', id: string): void {
  const w = textW(text) + 6;
  const x0 = anchor === 'start' ? x : x - w;
  const g = s('g', { class: 'f2-tag-v', 'data-tag': id }, box);
  s('rect', { x: x0, y: yMid - TAG_H / 2, width: w, height: TAG_H, rx: 2, fill: color }, g);
  s('text', { x: x0 + 3, y: yMid + 3.5, fill: 'var(--panel)', 'font-size': 10 }, g, text);
}

function draw(box: SVGSVGElement, o: MinuteChartOptions, on: ReadonlySet<string>): Drawn {
  while (box.firstChild) box.removeChild(box.firstChild);
  const n = o.axis.length;
  const plotW = W - LEFT - RIGHT;
  const plotH = H - TOP - BOTTOM;
  const x = (i: number): number => LEFT + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const iAt = (px: number): number => (n <= 1 ? 0 : Math.max(0, Math.min(n - 1, Math.round(((px - LEFT) / plotW) * (n - 1)))));
  const shown = o.series.filter((sr) => on.has(sr.id));
  const yr = range(
    shown.flatMap((sr) => sr.values.filter(finite)),
    true,
  );
  const idx = o.index && on.has(INDEX_ID) ? o.index : null;
  const ir = idx ? range(idx.values.filter(finite), false) : null;
  if (n === 0 || (yr === null && ir === null)) {
    s('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle' }, box, '데이터 없음');
    box.dataset.empty = 'true';
    return { x, iAt };
  }
  delete box.dataset.empty;
  const bottom = TOP + plotH;
  const right = LEFT + plotW;
  // 시간 눈금(정시) + 날짜·갱신
  const grid = s('g', { class: 'f2-grid' }, box);
  o.axis.forEach((t, i) => {
    if (!t.endsWith(':00')) return;
    s('line', { x1: x(i), x2: x(i), y1: TOP, y2: bottom, stroke: 'var(--line)', 'stroke-width': 1 }, grid);
    s('text', { class: 'f2-hour', x: x(i), y: bottom + 12, 'text-anchor': 'middle', fill: 'var(--dim)', 'font-size': 10 }, grid, t.slice(0, 2));
  });
  s('text', { class: 'f2-date', x: LEFT, y: H - 3, 'text-anchor': 'start', fill: 'var(--dim)', 'font-size': 10 }, box, o.date);
  if (o.updated)
    s('text', { class: 'f2-upd', x: right, y: H - 3, 'text-anchor': 'end', fill: 'var(--dim)', 'font-size': 10 }, box, `갱신 ${o.updated}`);
  const tags: { id: string; y: number; text: string; color: string }[] = [];
  // 억원 축(오른쪽)
  if (yr) {
    const y = (v: number): number => TOP + (1 - (v - yr.lo) / (yr.hi - yr.lo)) * plotH;
    const step = niceStep(yr.hi - yr.lo);
    const ticks = s('g', { class: 'f2-yaxis' }, box);
    for (let v = Math.ceil(yr.lo / step) * step; v <= yr.hi + 1e-9; v += step) {
      s('line', { x1: LEFT, x2: right, y1: y(v), y2: y(v), stroke: 'var(--line)', 'stroke-width': 0.5 }, ticks);
      s('text', { class: 'f2-ytick', x: right + 4, y: y(v) + 3, fill: 'var(--dim)', 'font-size': 10 }, ticks, eokTick(v));
    }
    s('text', { x: right + 4, y: TOP - 1, fill: 'var(--dim)', 'font-size': 9 }, ticks, '억원');
    s('line', { class: 'zero', x1: LEFT, x2: right, y1: y(0), y2: y(0), stroke: 'var(--dim)', 'stroke-width': 1 }, box);
    for (const sr of shown) {
      const g = s('g', { 'data-series': sr.id }, box);
      s('title', {}, g, sr.label);
      drawLine(g, sr.values, sr.color, 1, x, y, 1.6);
      const last = [...sr.values].reverse().find(finite);
      if (finite(last)) tags.push({ id: sr.id, y: y(last), text: `${sr.label} ${eokTag(last)}`, color: sr.color });
    }
  }
  // 지수(왼쪽 축 — 값 눈금을 억원과 섞지 않는다)
  if (idx && ir) {
    const y = (v: number): number => TOP + (1 - (v - ir.lo) / (ir.hi - ir.lo)) * plotH;
    const g = s('g', { 'data-series': INDEX_ID }, box);
    s('title', {}, g, idx.label);
    drawLine(g, idx.values, INDEX_COLOR, INDEX_STEP, x, y, 1.2);
    const step = niceStep(ir.hi - ir.lo, 3);
    const ticks = s('g', { class: 'f2-iaxis' }, box);
    for (let v = Math.ceil(ir.lo / step) * step; v <= ir.hi + 1e-9; v += step) {
      s('text', { class: 'f2-itick', x: LEFT - 4, y: y(v) + 3, 'text-anchor': 'end', fill: 'var(--dim)', 'font-size': 10 }, ticks, indexText(v));
    }
    const last = finite(idx.last) ? idx.last : [...idx.values].reverse().find(finite);
    if (finite(last)) {
      const yy = Math.min(Math.max(y(last), TOP + TAG_H / 2), bottom - TAG_H / 2);
      tag(box, LEFT - 2, yy, indexText(last), INDEX_COLOR, 'end', INDEX_ID);
    }
  }
  // 현재값 꼬리표(오른쪽) — 겹치면 비켜 놓는다
  const ys = nudgeLabels(
    tags.map((t) => t.y),
    TAG_H + 1,
    TOP + TAG_H / 2,
    bottom - TAG_H / 2,
  );
  tags.forEach((t, k) => {
    tag(box, right + 2, ys[k] ?? t.y, t.text, t.color, 'start', t.id);
  });
  return { x, iAt };
}

export interface MinuteChart {
  readonly el: HTMLDivElement;
  /** 선 켜고 끄기(범례 버튼과 같다) — 바뀐 뒤 켜져 있으면 true */
  toggle(id: string): boolean;
  /** 켜진 id(series 순서, 지수는 맨 뒤) */
  visible(): string[];
}

export function minuteChart(o: MinuteChartOptions): MinuteChart {
  const ids = [...o.series.map((sr) => sr.id), ...(o.index ? [INDEX_ID] : [])];
  const hidden = new Set(o.hidden ?? []);
  const on = new Set(ids.filter((id) => !hidden.has(id)));
  const box = svgBox(`0 0 ${String(W)} ${String(H)}`, o.label, 'f2-chart');
  const el = h('div', { class: 'linechart f2-minute' });
  const buttons = new Map<string, HTMLButtonElement>();
  let drawn = draw(box, o, on);
  const cross = h('p', { class: 'f2-cross dim', css: { 'min-height': '1.4em', margin: '2px 0 0', 'font-size': '11px' } }, '');
  const flip = (id: string): boolean => {
    if (on.has(id)) on.delete(id);
    else on.add(id);
    buttons.get(id)?.setAttribute('aria-pressed', String(on.has(id)));
    drawn = draw(box, o, on);
    return on.has(id);
  };
  const item = (id: string, label: string, color: string, extra?: string): HTMLElement => {
    const sw = h('span', { class: 'sw', attrs: { 'aria-hidden': 'true' }, css: { background: color } });
    const b = h('button', { attrs: { type: 'button', 'aria-pressed': String(on.has(id)) }, data: { id } }, sw, label);
    if (extra) b.append(h('span', { class: 'f2-ix' }, ` ${extra}`));
    b.addEventListener('click', () => flip(id));
    buttons.set(id, b);
    return h('li', null, b);
  };
  const legend = h('ul', { class: 'legend' }, [
    ...o.series.map((sr) => item(sr.id, sr.label, sr.color)),
    ...(o.index
      ? [
          item(
            INDEX_ID,
            o.index.label,
            INDEX_COLOR,
            [finite(o.index.last) ? indexText(o.index.last) : DASH, pctText(o.index.chgPct)].filter(Boolean).join(' '),
          ),
        ]
      : []),
  ]);
  // 마우스 세로선 — 그 분의 실제 값만(없는 분은 —)
  box.addEventListener('pointermove', (ev: PointerEvent) => {
    const r = box.getBoundingClientRect();
    if (!(r.width > 0)) return;
    const i = drawn.iAt(((ev.clientX - r.left) / r.width) * W);
    box.querySelector('.f2-hair')?.remove();
    s('line', { class: 'f2-hair', x1: drawn.x(i), x2: drawn.x(i), y1: TOP, y2: H - BOTTOM, stroke: 'var(--dim)', 'stroke-dasharray': '2 2' }, box);
    const parts = o.series
      .filter((sr) => on.has(sr.id))
      .map((sr) => {
        const v = sr.values[i];
        return `${sr.label} ${finite(v) ? eokTag(v) : DASH}`;
      });
    if (o.index && on.has(INDEX_ID)) {
      const v = o.index.values[i];
      parts.push(`${o.index.label} ${finite(v) ? indexText(v) : DASH}`);
    }
    cross.textContent = `${o.axis[i] ?? DASH} · ${parts.join(' · ')}`;
  });
  box.addEventListener('pointerleave', () => {
    box.querySelector('.f2-hair')?.remove();
    cross.textContent = '';
  });
  el.append(legend, box, cross);
  return {
    el,
    toggle: flip,
    visible: () => ids.filter((id) => on.has(id)),
  };
}
