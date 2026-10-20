// 양·음 막대 — 0 기준 위아래 막대 + 막대 위 상세(docs/p5_design.md §1.1·§6 — F2 프로그램 매매 60일·MAC 시리즈 변화).
//
// - 양수는 0 위로(--up), 음수는 0 아래로(--down). 없는 값(null·NaN)은 막대를 그리지 않고 빈 칸으로 둔다(0 으로 그리지
//   않는다 — 그 수는 missing 으로 세어 아래 줄에 적는다).
// - 막대 위 상세: 막대마다 title(마우스) + 막대를 누르거나 초점을 주면 상세 줄(aria-live)에 '이름 값 [상세]'.
//   막대는 초점을 받는다(tabindex=0, 키보드로도 상세를 본다).
// - SVG 는 viewBox 만(폭은 CSS 100%) — 모바일 폭에서 가로 넘침 없음.
import { h } from './dom';
import { DASH, num, type Num } from './fmt';
import { s, svgBox } from './svg';

export interface BarItem {
  /** x 이름(날짜 등) */
  label: string;
  value: Num;
  /** 상세 줄에 덧붙일 글(예 '차익 +12억 · 비차익 -30억') */
  detail?: string;
}

export interface BarsOptions {
  label: string;
  items: readonly BarItem[];
  format?: (v: number) => string;
  w?: number;
  h?: number;
}

export interface BarRect {
  index: number;
  x: number;
  y: number;
  width: number;
  height: number;
  sign: 'up' | 'down' | 'flat';
}

function ok(v: Num): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/** 칸이 이보다 적어도 이만큼의 폭으로 나눠 그린다 — 하루치 막대가 폭 전체를 덮지 않게(2026-10-20). */
export const MIN_SLOTS = 20;

/** 막대 위치(순수 계산 — 시험용). 0 은 높이 0.5 의 평평한 막대, 없는 값은 빠진다.
 *  칸이 `MIN_SLOTS` 보다 적으면 그 폭으로 나누고 오른쪽(최근)에 붙인다. */
export function barsLayout(
  values: readonly Num[],
  box: { w: number; h: number; top?: number; bottom?: number; left?: number; right?: number },
): { rects: BarRect[]; zeroY: number; missing: number; step: number; offset: number } {
  const top = box.top ?? 8;
  const bottom = box.bottom ?? 20;
  const left = box.left ?? 8;
  const right = box.right ?? 8;
  const xs = values.filter(ok);
  const hi = Math.max(0, ...xs);
  const lo = Math.min(0, ...xs);
  const span = hi - lo || 1;
  const plotH = box.h - top - bottom;
  const y = (v: number): number => top + (1 - (v - lo) / span) * plotH;
  const n = values.length;
  const slots = Math.max(n, MIN_SLOTS);
  const step = n ? (box.w - left - right) / slots : 0;
  const offset = left + (slots - n) * step; // 칸이 적으면 오른쪽(최근)에 붙인다
  const rects: BarRect[] = [];
  let missing = 0;
  values.forEach((v, i) => {
    if (!ok(v)) {
      missing += 1;
      return;
    }
    const y0 = y(0);
    const y1 = y(v);
    rects.push({
      index: i,
      x: offset + i * step + step * 0.15,
      y: Math.min(y0, y1),
      width: Math.max(step * 0.7, 0.5),
      height: Math.max(Math.abs(y1 - y0), 0.5),
      sign: v > 0 ? 'up' : v < 0 ? 'down' : 'flat',
    });
  });
  return { rects, zeroY: y(0), missing, step, offset };
}

const FILL = { up: 'var(--up)', down: 'var(--down)', flat: 'var(--dim)' } as const;

export function barsChart(o: BarsOptions): HTMLDivElement {
  const W = o.w ?? 640;
  const H = o.h ?? 200;
  const fmt = o.format ?? ((v: number) => num(v, 0, true));
  const box = svgBox(`0 0 ${W} ${H}`, o.label, 'barschart');
  const detail = h('p', { class: 'bars-detail', attrs: { 'aria-live': 'polite' } }, '');
  const wrap = h('div', { class: 'barschart' }, box, detail);
  const lay = barsLayout(
    o.items.map((it) => it.value),
    { w: W, h: H },
  );
  if (lay.rects.length === 0) {
    s('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle' }, box, '데이터 없음');
    box.dataset.empty = 'true';
    detail.remove();
    return wrap;
  }
  s('line', { class: 'zero', x1: 8, x2: W - 8, y1: lay.zeroY, y2: lay.zeroY, stroke: 'var(--dim)' }, box);
  const text = (it: BarItem): string =>
    `${it.label} ${ok(it.value) ? fmt(it.value) : DASH}${it.detail ? ` · ${it.detail}` : ''}`;
  for (const r of lay.rects) {
    const it = o.items[r.index];
    if (!it) continue;
    const rect = s(
      'rect',
      {
        x: r.x,
        y: r.y,
        width: r.width,
        height: r.height,
        fill: FILL[r.sign],
        tabindex: 0,
        'data-index': r.index,
        'data-sign': r.sign,
      },
      box,
    );
    s('title', {}, rect, text(it));
    const show = (): void => {
      detail.textContent = text(it);
      for (const other of box.querySelectorAll('rect.on')) other.classList.remove('on');
      rect.classList.add('on');
    };
    rect.addEventListener('click', show);
    rect.addEventListener('focus', show);
  }
  const n = o.items.length;
  const { step, offset } = lay;
  for (const i of [...new Set([0, n - 1])]) {
    const anchor = n === 1 ? 'end' : i === 0 ? 'start' : 'end';
    const x = n === 1 ? W - 8 : i === 0 ? offset : offset + n * step;
    s('text', { x, y: H - 6, 'text-anchor': anchor }, box, o.items[i]?.label ?? '');
  }
  if (lay.missing > 0) wrap.append(h('p', { class: 'def' }, `값 없는 ${lay.missing}칸은 비움`));
  return wrap;
}
