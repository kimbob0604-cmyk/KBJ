// 트리맵 — 정사각에 가깝게 나누기(squarified)·크기·색 범위·라벨 줄임(docs/p5_design.md §1.1·§6 — THM 페이지 9·ETF 맵).
//
// - 면적은 크기(size)에 비례(squarified — Bruls·Huizing·van Wijk 2000). 크기가 없거나 0 이하인 칸은 그리지 않고
//   omitted 로 센다(아래 줄에 적는다 — 숨기지 않는다).
// - 색: 값(color)의 부호로 --up/--down, 세기는 |값| / 범위(range, 기본 = |값| 최대)로 진하기(fill-opacity 0.25~1).
//   범위를 넘는 값은 끝 색으로 자른다. 값이 없으면 회색(--dim). 색은 스킨 토큰만(CSP 안전 — 표현 속성).
// - 라벨: 칸 폭·높이에 맞춰 줄이고(…), 너무 작은 칸은 라벨 없이 title 만.
// - SVG 는 viewBox 만(폭은 CSS 100%) — 모바일 폭에서 가로 넘침 없음.
import { h } from './dom';
import { DASH, num, type Num } from './fmt';
import { s, svgBox } from './svg';

export interface TreeItem {
  id: string;
  label: string;
  /** 면적 — 예 순자산·시가총액 */
  size: Num;
  /** 색 값 — 예 기간 수익률(%) */
  color: Num;
}

export interface TreemapOptions {
  label: string;
  items: readonly TreeItem[];
  /** 색 범위(±) — 기본은 |color| 최대 */
  range?: number;
  /** 크기·색 값 글자(title) */
  formatSize?: (v: number) => string;
  formatColor?: (v: number) => string;
  w?: number;
  h?: number;
}

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

function ok(v: Num): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

function worst(row: readonly number[], side: number): number {
  const sum = row.reduce((a, b) => a + b, 0);
  const mx = Math.max(...row);
  const mn = Math.min(...row);
  const s2 = side * side;
  return Math.max((s2 * mx) / (sum * sum), (sum * sum) / (s2 * mn));
}

/**
 * squarified 나누기(순수 계산 — 시험용). 입력 순서대로 칸을 돌려준다 — 크기가 없거나 0 이하면 null.
 * 면적 합 = 상자 면적(크기 비례).
 */
export function squarify(sizes: readonly Num[], box: Rect): (Rect | null)[] {
  const out: (Rect | null)[] = sizes.map(() => null);
  const idx = sizes
    .map((v, i) => ({ i, v: ok(v) && v > 0 ? v : 0 }))
    .filter((x) => x.v > 0)
    .sort((a, b) => b.v - a.v || a.i - b.i);
  const total = idx.reduce((a, b) => a + b.v, 0);
  if (total <= 0 || box.w <= 0 || box.h <= 0) return out;
  const scaleArea = (box.w * box.h) / total;
  const areas = idx.map((x) => ({ i: x.i, a: x.v * scaleArea }));
  const free: Rect = { ...box };
  let row: { i: number; a: number }[] = [];

  const layRow = (items: { i: number; a: number }[], last: boolean): void => {
    const sum = items.reduce((t, x) => t + x.a, 0);
    if (free.w >= free.h) {
      const t = last ? free.w : sum / free.h; // 세로 한 줄(왼쪽)
      let y = free.y;
      for (const x of items) {
        const hh = x.a / t;
        out[x.i] = { x: free.x, y, w: t, h: hh };
        y += hh;
      }
      free.x += t;
      free.w -= t;
    } else {
      const t = last ? free.h : sum / free.w; // 가로 한 줄(위)
      let x0 = free.x;
      for (const x of items) {
        const ww = x.a / t;
        out[x.i] = { x: x0, y: free.y, w: ww, h: t };
        x0 += ww;
      }
      free.y += t;
      free.h -= t;
    }
  };

  for (const item of areas) {
    const side = Math.min(free.w, free.h);
    const next = [...row, item];
    if (row.length === 0 || worst(next.map((x) => x.a), side) <= worst(row.map((x) => x.a), side)) {
      row = next;
    } else {
      layRow(row, false);
      row = [item];
    }
  }
  if (row.length) layRow(row, true);
  return out;
}

/** 색 값 → (채움, 진하기). 범위를 넘으면 끝 색. 값이 없으면 회색. */
export function treeColor(v: Num, range: number): { fill: string; opacity: number } {
  if (!ok(v)) return { fill: 'var(--dim)', opacity: 0.35 };
  if (v === 0 || !(range > 0)) return { fill: 'var(--dim)', opacity: 0.5 };
  const t = Math.min(Math.abs(v) / range, 1);
  return { fill: v > 0 ? 'var(--up)' : 'var(--down)', opacity: Number((0.25 + 0.75 * t).toFixed(3)) };
}

/** 글자 폭 어림(px) — 한글·전각은 글자 크기, 그 밖은 0.6배. */
export function textWidth(text: string, fontPx: number): number {
  let w = 0;
  for (const ch of text) w += /[\u1100-\u11ff\u3000-\u9fff\uac00-\ud7af\uff00-\uffef]/.test(ch) ? fontPx : fontPx * 0.6;
  return w;
}

/** 폭에 맞게 줄인 라벨(…). 한 글자도 못 들어가면 ''. */
export function fitLabel(text: string, widthPx: number, fontPx: number): string {
  if (textWidth(text, fontPx) <= widthPx) return text;
  const chars = Array.from(text);
  while (chars.length > 0 && textWidth(`${chars.join('')}…`, fontPx) > widthPx) chars.pop();
  return chars.length ? `${chars.join('')}…` : '';
}

export function treemap(o: TreemapOptions): HTMLDivElement {
  const W = o.w ?? 640;
  const H = o.h ?? 360;
  const font = 11;
  const box = svgBox(`0 0 ${W} ${H}`, o.label, 'treemap');
  const wrap = h('div', { class: 'treemap' }, box);
  const rects = squarify(
    o.items.map((it) => it.size),
    { x: 0, y: 0, w: W, h: H },
  );
  const colors = o.items.map((it) => it.color).filter(ok);
  const range = o.range ?? (colors.length ? Math.max(...colors.map(Math.abs)) : 0);
  const fs = o.formatSize ?? ((v: number) => num(v));
  const fc = o.formatColor ?? ((v: number) => num(v, 2, true));
  let drawn = 0;
  o.items.forEach((it, i) => {
    const r = rects[i];
    if (!r) return;
    drawn += 1;
    const c = treeColor(it.color, range);
    const g = s('g', { 'data-id': it.id }, box);
    const rect = s(
      'rect',
      {
        x: r.x.toFixed(2),
        y: r.y.toFixed(2),
        width: Math.max(r.w - 1, 0.5).toFixed(2),
        height: Math.max(r.h - 1, 0.5).toFixed(2),
        fill: c.fill,
        'fill-opacity': c.opacity,
      },
      g,
    );
    const size = ok(it.size) ? fs(it.size) : DASH;
    const col = ok(it.color) ? fc(it.color) : DASH;
    s('title', {}, rect, `${it.label} · ${size} · ${col}`);
    if (r.h >= font + 6) {
      const text = fitLabel(it.label, r.w - 6, font);
      if (text) s('text', { class: 'fg', x: (r.x + 3).toFixed(2), y: (r.y + font + 2).toFixed(2) }, g, text);
    }
  });
  if (drawn === 0) {
    s('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle' }, box, '데이터 없음');
    box.dataset.empty = 'true';
  }
  const omitted = o.items.length - drawn;
  if (omitted > 0 && drawn > 0) wrap.append(h('p', { class: 'def' }, `크기 없는 ${omitted}칸은 그리지 않음`));
  return wrap;
}
