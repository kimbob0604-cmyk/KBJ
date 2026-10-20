// 누적 막대 — 비용 구성(연간 비용구조 = 금액 누적, 분기 비용구조 = 100% 누적; docs/p4_design.md §1.2·§6.2 '리포트' 탭).
//
// - 'abs'(금액): 양수는 0 위로, 음수는 0 아래로 쌓는다(기타 손익처럼 음수가 섞일 때 합이 맞게).
// - 'pct'(100%): 범주마다 양수 값의 합을 100 으로. 음수·없는 값은 비율에서 빼고 그 수를 excluded 로 센다
//   (그림 아래 줄에 '음수 n칸 제외' — 숨기지 않는다). 양수 합이 0 이면 그 범주는 비운다.
// - 없는 값(null·NaN)은 그 조각만 비운다(0 으로 그리지 않는다). 색은 스킨 토큰만(8색을 넘으면 되돌아 쓴다).
// - 범례는 HTML(글자 대비·줄바꿈) — 색 칸은 CSSOM(css 옵션)이라 CSP 안전.
import { h } from './dom';
import { DASH, joEok, type Num, pct } from './fmt';
import { s, svgBox } from './svg';

export interface StackSeries {
  label: string;
  /** 범주(기간)마다 값 — categories 와 같은 길이 */
  values: readonly Num[];
}

export type StackMode = 'abs' | 'pct';

export interface StackSeg {
  series: number;
  category: number;
  /** 값 공간(abs = 원, pct = %)의 아래·위 */
  y0: number;
  y1: number;
  /** 원래 값(pct 에서도 금액) */
  value: number;
}

export interface StackLayout {
  segs: StackSeg[];
  /** 축 범위(값 공간) */
  lo: number;
  hi: number;
  /** pct 에서 뺀 음수 칸 수 */
  excluded: number;
}

export const STACK_COLORS: readonly string[] = [
  'var(--accent)',
  'var(--down)',
  'var(--up)',
  'var(--warn)',
  'var(--est)',
  'var(--dim)',
  'var(--lock)',
  'var(--fg)',
];

function ok(v: Num): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/** 조각 위치(순수 계산 — 시험용). */
export function stackLayout(nCategories: number, series: readonly StackSeries[], mode: StackMode): StackLayout {
  const segs: StackSeg[] = [];
  let lo = 0;
  let hi = mode === 'pct' ? 100 : 0;
  let excluded = 0;
  for (let j = 0; j < nCategories; j++) {
    if (mode === 'pct') {
      let total = 0;
      for (const sr of series) {
        const v = sr.values[j];
        if (ok(v) && v > 0) total += v;
        else if (ok(v) && v < 0) excluded += 1;
      }
      if (total <= 0) continue;
      let acc = 0;
      series.forEach((sr, k) => {
        const v = sr.values[j];
        if (!ok(v) || v <= 0) return;
        const share = (v / total) * 100;
        segs.push({ series: k, category: j, y0: acc, y1: acc + share, value: v });
        acc += share;
      });
    } else {
      let up = 0;
      let down = 0;
      series.forEach((sr, k) => {
        const v = sr.values[j];
        if (!ok(v) || v === 0) return;
        if (v > 0) {
          segs.push({ series: k, category: j, y0: up, y1: up + v, value: v });
          up += v;
        } else {
          segs.push({ series: k, category: j, y0: down + v, y1: down, value: v });
          down += v;
        }
      });
      hi = Math.max(hi, up);
      lo = Math.min(lo, down);
    }
  }
  return { segs, lo, hi, excluded };
}

export interface StackBarOptions {
  /** 접근 이름 — 예 '연간 비용 구성' */
  label: string;
  /** 범주(기간) 이름 — 예 ['2023', '2024', …] 또는 ['1Q25', …] */
  categories: readonly string[];
  series: readonly StackSeries[];
  /** 기본 'abs' */
  mode?: StackMode;
  /** abs 의 값 글자(기본 원 → 억·조) — 조각 title 에 쓴다 */
  format?: (v: number) => string;
  w?: number;
  h?: number;
}

export function stackBar(o: StackBarOptions): HTMLDivElement {
  const mode = o.mode ?? 'abs';
  const W = o.w ?? 640;
  const H = o.h ?? 220;
  const top = 8;
  const bottom = 22;
  const left = 8;
  const right = 8;
  const fmt = o.format ?? ((v: number) => joEok(v));
  const n = o.categories.length;
  const lay = stackLayout(n, o.series, mode);
  const box = svgBox(`0 0 ${W} ${H}`, o.label, 'stackbar');
  const wrap = h('div', { class: 'stackbar', data: { mode } }, box);

  if (n === 0 || lay.segs.length === 0) {
    s('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle' }, box, '데이터 없음');
    box.dataset.empty = 'true';
  } else {
    const plotH = H - top - bottom;
    const span = lay.hi - lay.lo || 1;
    const y = (v: number): number => top + (1 - (v - lay.lo) / span) * plotH;
    const step = (W - left - right) / n;
    // 0 기준선(음수가 있을 때 보이게)
    if (lay.lo < 0) s('line', { x1: left, x2: W - right, y1: y(0), y2: y(0), stroke: 'var(--dim)' }, box);
    for (const sg of lay.segs) {
      const yTop = y(sg.y1);
      const hgt = Math.max(y(sg.y0) - yTop, 0.5);
      const rect = s(
        'rect',
        {
          x: left + sg.category * step + step * 0.18,
          y: yTop,
          width: step * 0.64,
          height: hgt,
          fill: STACK_COLORS[sg.series % STACK_COLORS.length] ?? 'var(--accent)',
          'data-series': sg.series,
          'data-category': sg.category,
        },
        box,
      );
      const name = o.series[sg.series]?.label ?? '';
      const amount = mode === 'pct' ? `${pct(sg.y1 - sg.y0, 1, false)} (${fmt(sg.value)})` : fmt(sg.value);
      s('title', {}, rect, `${o.categories[sg.category] ?? DASH} · ${name} ${amount}`);
    }
    // 범주가 많으면(월별 3년 등) 이름이 붙어 읽히지 않는다 — 12개 안팎만 적는다(마지막은 늘)
    const every = Math.max(1, Math.ceil(n / 12));
    o.categories.forEach((c, j) => {
      if (j % every !== (n - 1) % every) return;
      s(
        'text',
        { x: left + j * step + step / 2, y: H - 6, 'text-anchor': 'middle' },
        box,
        c,
      );
    });
  }

  const legend = h(
    'ul',
    { class: 'legend' },
    o.series.map((sr, k) =>
      h(
        'li',
        null,
        h('span', {
          class: 'sw',
          attrs: { 'aria-hidden': 'true' },
          css: { background: STACK_COLORS[k % STACK_COLORS.length] ?? 'var(--accent)' },
        }),
        sr.label,
      ),
    ),
  );
  wrap.append(legend);
  if (lay.excluded > 0) wrap.append(h('p', { class: 'def' }, `음수 ${lay.excluded}칸은 100% 비율에서 제외`));
  return wrap;
}
