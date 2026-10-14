// 상관 행렬 히트맵 — −1~1 발산 색, 라벨 줄임, 칸 누르면 상세(docs/p8_design.md D-P8-23 — 페이지 12 상관관계).
//
// - 값이 없으면(null·NaN — 겹치는 날 부족) 칸을 비우고 '—'(0 으로 칠하지 않는다 — 절대 규칙 2).
// - 범위 밖 값(|v| > 1)은 자르지 않는다 — 오류(던진다). 행렬은 정사각이고 라벨 수와 같아야 한다.
// - SVG 는 viewBox 만(폭은 CSS 100%) — 좁은 폭에서 가로 넘침 없음. 색은 기존 토큰(--up 양·--down 음).
// - 키보드: 칸마다 tabindex=0, Enter·Space·초점으로 상세 줄을 바꾼다(색만으로 값을 전하지 않는다).
import { h } from './dom';
import { DASH, num, type Num } from './fmt';
import { s, svgBox } from './svg';

export interface HeatCell {
  row: number;
  col: number;
  x: number;
  y: number;
  size: number;
  value: number | null;
  fill: string;
}

export interface HeatLayout {
  cells: HeatCell[];
  /** 왼쪽·위 라벨 칸 폭 */
  gutter: number;
  width: number;
  height: number;
}

/** 라벨 줄임 — max 글자 넘으면 끝을 '…' 로(코드·이름 모두). */
export function shortLabel(text: string, max = 6): string {
  const seg = new Intl.Segmenter('ko', { granularity: 'grapheme' });
  const chars = Array.from(seg.segment(text), (x) => x.segment);
  return chars.length <= max ? text : `${chars.slice(0, Math.max(1, max - 1)).join('')}…`;
}

/** −1~1 발산 색. 0 은 바탕, |v| 가 클수록 진하다. 값 없음은 바탕. */
export function corrColor(v: Num): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return 'var(--panel)';
  if (v > 1 || v < -1) throw new RangeError('상관 값은 −1~1');
  if (v === 0) return 'var(--panel)';
  const a = Math.round(12 + Math.abs(v) * 68);
  return `color-mix(in srgb, var(${v > 0 ? '--up' : '--down'}) ${a}%, var(--panel))`;
}

export function heatmapLayout(labels: readonly string[], matrix: readonly (readonly Num[])[], cell = 22): HeatLayout {
  const n = labels.length;
  if (matrix.length !== n || matrix.some((r) => r.length !== n)) {
    throw new RangeError('행렬은 라벨 수와 같은 정사각이어야 한다');
  }
  const gutter = 44;
  const cells: HeatCell[] = [];
  for (let r = 0; r < n; r++) {
    for (let c = 0; c < n; c++) {
      const raw = matrix[r]?.[c];
      const value = typeof raw === 'number' && Number.isFinite(raw) ? raw : null;
      cells.push({ row: r, col: c, x: gutter + c * cell, y: gutter + r * cell, size: cell, value, fill: corrColor(value) });
    }
  }
  return { cells, gutter, width: gutter + n * cell, height: gutter + n * cell };
}

export interface HeatmapOptions {
  label: string;
  labels: readonly string[];
  /** 라벨 옆 긴 이름(상세 줄) — 없으면 라벨 */
  names?: readonly (string | null | undefined)[];
  matrix: readonly (readonly Num[])[];
  onCell?: (row: number, col: number, value: number | null) => void;
}

export function heatmap(o: HeatmapOptions): HTMLDivElement {
  const lay = heatmapLayout(o.labels, o.matrix);
  const svg = svgBox(`0 0 ${lay.width} ${lay.height}`, o.label);
  const detail = h('p', { class: 'heatmap-detail', attrs: { 'aria-live': 'polite' } }, '칸을 고르면 값이 나온다');
  const nameOf = (i: number): string => o.names?.[i] ?? o.labels[i] ?? '';
  o.labels.forEach((lab, i) => {
    const short = shortLabel(lab);
    s('text', { x: lay.gutter - 4, y: lay.gutter + i * 22 + 15, 'text-anchor': 'end' }, svg, short);
    s('text', { x: lay.gutter + i * 22 + 11, y: lay.gutter - 6, 'text-anchor': 'middle' }, svg, short);
  });
  for (const c of lay.cells) {
    const g = s('g', { class: 'heat-cell', tabindex: 0, role: 'button' }, svg);
    const text = `${nameOf(c.row)} × ${nameOf(c.col)}: ${c.value === null ? DASH : num(c.value, 2)}`;
    g.setAttribute('aria-label', text);
    s('rect', { x: c.x, y: c.y, width: c.size - 1, height: c.size - 1, fill: c.fill }, g);
    if (c.value === null) s('text', { x: c.x + c.size / 2, y: c.y + 15, 'text-anchor': 'middle' }, g, DASH);
    const pick = (): void => {
      detail.textContent = text;
      o.onCell?.(c.row, c.col, c.value);
    };
    g.addEventListener('click', pick);
    g.addEventListener('focus', pick);
    g.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') pick();
    });
  }
  return h('div', { class: 'heatmap' }, svg, detail);
}
