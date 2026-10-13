// 밸류에이션 밴드 차트 — 가격선 + 배수 밴드 다섯 줄(가격 공간, docs/p4_design.md §1.2·§4.8 '밴드 차트').
//
//   밴드가(t) = 배수_p × 그날 주당 기준값(EPS_TTM·BPS) — 화면에서만 계산하고 저장하지 않는다(§4.8).
//   bandPrices() 가 그 곱을 만들고, bandChart() 가 그린다(차트 라이브러리 없이 손으로 그린 SVG — P3 svg.ts 방식).
//
// 규칙: 값이 없거나(null·NaN·Infinity) 기준값이 0 이하인 날은 그 점을 비운다(선이 끊긴다 — 0 으로 그리지 않는다).
// 축 범위는 유한한 값만으로. 그릴 값이 하나도 없으면 빈 틀 + '데이터 없음'. 색은 스킨 토큰만.
import { DASH, kstDate, num, type Num } from './fmt';
import { s, svgBox } from './svg';

export interface BandLine {
  /** 'P10'·'P50' 처럼 짧게 */
  label: string;
  /** 날짜마다 가격 공간 값(원) */
  values: readonly Num[];
  /** 배수(범례 꼬리표 — 예 12.3 → '12.3x'). 없으면 꼬리표 없음 */
  multiple?: Num;
}

export interface BandChartOptions {
  /** 접근 이름 — 예 '005930 PER 밴드 5년' */
  label: string;
  /** 날짜('YYYY-MM-DD') — price·bands 의 values 와 같은 길이 */
  dates: readonly string[];
  /** 종가(원) */
  price: readonly Num[];
  /** 아래(P10)부터 위(P90) 순서 — 보통 다섯 줄 */
  bands: readonly BandLine[];
  w?: number;
  h?: number;
}

function ok(v: Num): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/**
 * 배수 → 가격 공간 선. 결과[k][i] = multiples[k] × basis[i] — 기준값이 없거나 0 이하(적자 EPS·자본잠식 BPS)면 null,
 * 배수가 없으면 그 줄 전체가 null.
 */
export function bandPrices(basis: readonly Num[], multiples: readonly Num[]): (number | null)[][] {
  return multiples.map((m) => basis.map((b) => (ok(m) && ok(b) && b > 0 ? m * b : null)));
}

/** 밴드 줄 색·모양(아래에서 위로) — 가운데(중앙값)가 가장 진하다. 스킨 토큰만 */
const BAND_STYLE: readonly { stroke: string; dash?: string }[] = [
  { stroke: 'var(--dim)', dash: '2 3' },
  { stroke: 'var(--est)', dash: '4 3' },
  { stroke: 'var(--accent)' },
  { stroke: 'var(--est)', dash: '4 3' },
  { stroke: 'var(--dim)', dash: '2 3' },
];

function styleOf(i: number, n: number): { stroke: string; dash?: string } {
  if (n === BAND_STYLE.length) return BAND_STYLE[i] ?? { stroke: 'var(--dim)' };
  return { stroke: 'var(--est)', dash: '4 3' };
}

export function bandChart(o: BandChartOptions): SVGSVGElement {
  const W = o.w ?? 900;
  const H = o.h ?? 260;
  const left = 8;
  const right = 92; // 범례 꼬리표 자리
  const top = 10;
  const bottom = 22;
  const box = svgBox(`0 0 ${W} ${H}`, o.label, 'band');
  const n = o.dates.length;
  const all: number[] = [];
  for (const v of o.price) if (ok(v)) all.push(v);
  for (const b of o.bands) for (const v of b.values) if (ok(v)) all.push(v);

  if (n === 0 || all.length === 0) {
    s('rect', { x: 0.5, y: 0.5, width: W - 1, height: H - 1, fill: 'none', stroke: 'var(--line)' }, box);
    s('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle' }, box, '데이터 없음');
    box.dataset.empty = 'true';
    return box;
  }

  const mn = Math.min(...all);
  const mx = Math.max(...all);
  const pad = mx === mn ? Math.max(Math.abs(mx) * 0.05, 1) : (mx - mn) * 0.05;
  const lo = mn - pad;
  const hi = mx + pad;
  const plotW = W - left - right;
  const plotH = H - top - bottom;
  const x = (i: number): number => left + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const y = (v: number): number => top + (1 - (v - lo) / (hi - lo)) * plotH;

  /** 값 배열 → 끊긴 구간마다 polyline 하나 */
  function segments(values: readonly Num[]): string[] {
    const out: string[] = [];
    let cur: string[] = [];
    for (let i = 0; i < n; i++) {
      const v = values[i];
      if (ok(v)) cur.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`);
      else if (cur.length) {
        out.push(cur.join(' '));
        cur = [];
      }
    }
    if (cur.length) out.push(cur.join(' '));
    return out;
  }

  /** 마지막 유효 값(꼬리표 자리) */
  function lastOk(values: readonly Num[]): { i: number; v: number } | null {
    for (let i = Math.min(n, values.length) - 1; i >= 0; i--) {
      const v = values[i];
      if (ok(v)) return { i, v };
    }
    return null;
  }

  // 밴드 줄
  o.bands.forEach((b, k) => {
    const st = styleOf(k, o.bands.length);
    const g = s('g', { 'data-band': b.label }, box);
    for (const pts of segments(b.values)) {
      const attrs: Record<string, string | number> = {
        points: pts,
        fill: 'none',
        stroke: st.stroke,
        'stroke-width': 1,
        'vector-effect': 'non-scaling-stroke',
      };
      if (st.dash) attrs['stroke-dasharray'] = st.dash;
      s('polyline', attrs, g);
    }
    const end = lastOk(b.values);
    if (end) {
      const tag = ok(b.multiple) ? `${b.label} ${num(b.multiple, 1)}x` : b.label;
      // 글자색은 CSS(svg text — 기본 --dim, .fg 는 --fg)가 정한다 — 가운데 줄만 진하게
      const t = s('text', { x: W - right + 4, y: y(end.v) + 3 }, g, tag);
      if (st.stroke === 'var(--accent)') t.setAttribute('class', 'fg');
    }
  });

  // 가격선(맨 위에)
  const pg = s('g', { 'data-band': 'price' }, box);
  for (const pts of segments(o.price)) {
    s(
      'polyline',
      { points: pts, fill: 'none', stroke: 'var(--fg)', 'stroke-width': 1.6, 'vector-effect': 'non-scaling-stroke' },
      pg,
    );
  }

  // 기간 줄
  const first = o.dates[0];
  const last = o.dates[n - 1];
  s(
    'text',
    { x: left, y: H - 6 },
    box,
    `${first ? kstDate(first) : DASH} ~ ${last ? kstDate(last) : DASH} · 굵은 선 = 종가, 가는 선 = 배수 밴드`,
  );
  return box;
}
