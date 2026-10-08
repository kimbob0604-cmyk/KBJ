// 손으로 그리는 SVG — 스파크라인·막대·선·히트맵 색·수평 막대(미리보기와 같은 방식, 차트 라이브러리 없음 — §6.1).
// 색은 CSS 변수(var(--accent) 등)를 SVG 표현 속성으로 넣는다(style 속성이 아니라 CSP 안전).
import { h } from './dom';

export const SVG_NS = 'http://www.w3.org/2000/svg';

export function s<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attrs: Record<string, string | number> = {},
  parent?: Element,
  text?: string | number,
): SVGElementTagNameMap[K] {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'style') throw new Error('style 속성은 쓰지 않는다');
    el.setAttribute(k, String(v));
  }
  if (text !== undefined) el.textContent = String(text);
  if (parent) parent.appendChild(el);
  return el;
}

/** role=img + 접근 이름이 있는 svg 틀. */
export function svgBox(viewBox: string, label: string, cls?: string): SVGSVGElement {
  const el = s('svg', { viewBox, role: 'img', 'aria-label': label });
  if (cls) el.setAttribute('class', cls);
  return el;
}

function finite(values: readonly (number | null | undefined)[]): number[] {
  return values.filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
}

/** 값 → 0..1 비율(범위가 0 이면 0.5). */
export function scale(values: readonly (number | null | undefined)[]): (v: number) => number {
  const xs = finite(values);
  if (xs.length === 0) return () => 0.5;
  const mn = Math.min(...xs);
  const mx = Math.max(...xs);
  return mx === mn ? () => 0.5 : (v) => (v - mn) / (mx - mn);
}

/** 스파크라인(지수 타일). 빈 값은 건너뛴다(0 으로 그리지 않는다). */
export function sparkline(
  values: readonly (number | null | undefined)[],
  opts: { label: string; w?: number; h?: number; stroke?: string } = { label: '추이' },
): SVGSVGElement {
  const w = opts.w ?? 100;
  const hh = opts.h ?? 32;
  const box = svgBox(`0 0 ${w} ${hh}`, opts.label);
  box.setAttribute('preserveAspectRatio', 'none');
  const f = scale(values);
  const n = values.length;
  const pts: string[] = [];
  values.forEach((v, i) => {
    if (typeof v !== 'number' || !Number.isFinite(v)) return;
    const x = n <= 1 ? w / 2 : (i / (n - 1)) * w;
    const y = hh - 2 - f(v) * (hh - 4);
    pts.push(`${x.toFixed(2)},${y.toFixed(2)}`);
  });
  if (pts.length > 0) {
    s(
      'polyline',
      {
        points: pts.join(' '),
        fill: 'none',
        stroke: opts.stroke ?? 'var(--accent)',
        'stroke-width': 1.2,
        'vector-effect': 'non-scaling-stroke',
      },
      box,
    );
  }
  return box;
}

/** 세로 막대 + (선택) 기준선. 값이 없으면 그 자리를 비운다. */
export function barChart(
  values: readonly (number | null | undefined)[],
  opts: { label: string; w?: number; h?: number; ref?: number | null; fill?: string; caption?: string },
): SVGSVGElement {
  const w = opts.w ?? 320;
  const hh = opts.h ?? 130;
  const box = svgBox(`0 0 ${w} ${hh}`, opts.label);
  const xs = finite(values);
  const mx = Math.max(...xs, opts.ref ?? 0, 0) * 1.1 || 1;
  const n = Math.max(values.length, 1);
  const left = 10;
  const plotW = w - 20;
  const base = hh - 20;
  const step = plotW / n;
  values.forEach((v, i) => {
    if (typeof v !== 'number' || !Number.isFinite(v) || v < 0) return;
    const bh = (v / mx) * (base - 10);
    s(
      'rect',
      { x: left + i * step + step * 0.15, y: base - bh, width: step * 0.7, height: bh, fill: opts.fill ?? 'var(--accent)' },
      box,
    );
  });
  if (typeof opts.ref === 'number' && Number.isFinite(opts.ref)) {
    const y = base - (opts.ref / mx) * (base - 10);
    s('line', { x1: left, x2: left + plotW, y1: y, y2: y, stroke: 'var(--warn)', 'stroke-dasharray': '3 3' }, box);
  }
  if (opts.caption) s('text', { x: left, y: hh - 4 }, box, opts.caption);
  return box;
}

/** 수평 막대 한 줄(미리보기 .lvl): 이름 · 막대(0~100%) · 값 */
export function level(name: string, pct: number, valueText: string, valueCls?: string): HTMLDivElement {
  const p = Math.max(0, Math.min(100, Number.isFinite(pct) ? pct : 0));
  return h(
    'div',
    { class: 'lvl' },
    h('span', null, name),
    h('div', { class: 'bar', attrs: { 'aria-hidden': 'true' } }, h('span', { css: { left: '0', width: `${p}%` } })),
    h('span', valueCls ? { class: valueCls } : null, valueText),
  );
}

/** 0 을 가운데 둔 수평 막대(순매수처럼 부호가 있는 값). max 는 절댓값 최대. */
export function centerLevel(name: string, v: number, max: number, valueText: string): HTMLDivElement {
  const half = max > 0 ? (Math.min(Math.abs(v), max) / max) * 50 : 0;
  const neg = v < 0;
  return h(
    'div',
    { class: 'lvl' },
    h('span', null, name),
    h(
      'div',
      { class: 'bar c', attrs: { 'aria-hidden': 'true' } },
      h('span', { class: neg ? 'neg' : undefined, css: { left: `${neg ? 50 - half : 50}%`, width: `${half}%` } }),
    ),
    h('span', { class: v > 0 ? 'up' : v < 0 ? 'down' : 'dim' }, valueText),
  );
}

/**
 * 히트맵 칸 바탕색 — 상승 --up, 하락 --down 을 패널색과 섞는다(글자는 --fg 그대로 — 대비 유지).
 * |v| ≥ full 이면 가장 진하게(기본 3%).
 */
export function heatColor(v: number | null | undefined, full = 3): string {
  if (typeof v !== 'number' || !Number.isFinite(v) || v === 0) return 'var(--panel)';
  const a = Math.round(15 + Math.min(Math.abs(v) / full, 1) * 45);
  return `color-mix(in srgb, var(${v > 0 ? '--up' : '--down'}) ${a}%, var(--panel))`;
}
