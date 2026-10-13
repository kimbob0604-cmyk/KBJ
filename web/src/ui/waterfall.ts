// 워터폴(이익 브릿지) — 시작 합계 → 항목별 증감 → 끝 합계, 잔차 행 강조(docs/p4_design.md §1.2·§6.2 '리포트' 탭).
//
// 가로 막대 한 줄 = 한 단계(이름 · 막대 · 값). 단계가 9개쯤이라 세로 막대보다 이름이 덜 겹친다(좁은 화면에서도).
// - total: 0 에서 값까지(그 값이 다음 단계의 출발점), delta: 지난 누계에서 누계 + 값까지.
// - 값이 없으면(null·NaN) 막대를 그리지 않고 값 칸 '—' — 누계는 그대로 둔다(0 으로 셈하지 않는다 — 대신 끊김 표시).
// - residual(잔차 — 설명되지 않은 차이)은 경고색 + 점선 테두리 + '잔차' 꼬리표.
// 색은 스킨 토큰만(증가 --up·감소 --down — 국내 관례, 합계 --accent, 잔차 --warn).
import { DASH, joEok, type Num } from './fmt';
import { s, svgBox } from './svg';

export interface WaterfallStep {
  label: string;
  value: Num;
  /** 기본 delta */
  kind?: 'total' | 'delta';
  /** 잔차(설명 안 된 차이) 행 — 강조 */
  residual?: boolean;
}

export interface WaterfallBar {
  /** 막대 시작·끝(값 공간). 값이 없으면 null */
  from: number | null;
  to: number | null;
  /** 이 단계 뒤 누계(값이 없던 단계 뒤로는 앞 누계 그대로) */
  running: number;
  /** 앞 단계 값이 없어 누계를 믿을 수 없다 */
  broken: boolean;
}

function ok(v: Num): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/** 단계 → 막대 위치(순수 계산 — 시험용으로 따로 둔다). */
export function waterfallBars(steps: readonly WaterfallStep[]): WaterfallBar[] {
  let running = 0;
  let broken = false;
  return steps.map((st) => {
    const v = st.value;
    if (!ok(v)) {
      // total 이 비면 그 뒤 누계는 모른다, delta 가 비어도 마찬가지 — 다음 total 이 다시 잡을 때까지 broken
      broken = true;
      return { from: null, to: null, running, broken };
    }
    if (st.kind === 'total') {
      running = v;
      broken = false;
      return { from: 0, to: v, running, broken };
    }
    const from = running;
    running += v;
    return { from, to: running, running, broken };
  });
}

export interface WaterfallOptions {
  /** 접근 이름 — 예 '영업이익 브릿지 2025 → 2026' */
  label: string;
  steps: readonly WaterfallStep[];
  /** 값 글자(기본 원 → 억·조 — 합계는 부호 없이, 증감은 +/-) */
  format?: (v: number, step: WaterfallStep) => string;
  w?: number;
  rowH?: number;
}

export function waterfall(o: WaterfallOptions): SVGSVGElement {
  const W = o.w ?? 640;
  const rowH = o.rowH ?? 24;
  const labelW = 150;
  const valueW = 96;
  const pad = 8;
  const n = o.steps.length;
  const H = Math.max(1, n) * rowH + pad * 2;
  const box = svgBox(`0 0 ${W} ${H}`, o.label, 'waterfall');
  const fmt = o.format ?? ((v: number, st: WaterfallStep) => joEok(v, st.kind !== 'total'));
  const bars = waterfallBars(o.steps);

  if (n === 0) {
    s('text', { x: W / 2, y: H / 2 + 4, 'text-anchor': 'middle' }, box, '데이터 없음');
    box.dataset.empty = 'true';
    return box;
  }

  const xs: number[] = [0];
  for (const b of bars) {
    if (b.from !== null) xs.push(b.from);
    if (b.to !== null) xs.push(b.to);
  }
  const lo = Math.min(...xs);
  const hi = Math.max(...xs);
  const plotL = labelW + pad;
  const plotW = W - labelW - valueW - pad * 2;
  const x = (v: number): number => plotL + (hi === lo ? 0.5 : (v - lo) / (hi - lo)) * plotW;

  // 0 기준선
  const x0 = x(0);
  s('line', { x1: x0, x2: x0, y1: pad / 2, y2: H - pad / 2, stroke: 'var(--line)' }, box);

  o.steps.forEach((st, i) => {
    const b = bars[i];
    const y = pad + i * rowH;
    const g = s('g', { 'data-step': i, 'data-kind': st.kind ?? 'delta' }, box);
    if (st.residual) g.setAttribute('data-residual', 'true');
    // 글자색은 CSS 클래스로(svg text 기본 --dim — base.css)
    s(
      'text',
      { x: labelW, y: y + rowH * 0.66, 'text-anchor': 'end', class: st.residual ? 'warn' : 'fg' },
      g,
      st.residual ? `${st.label} (잔차)` : st.label,
    );
    if (b && b.from !== null && b.to !== null) {
      const a = x(Math.min(b.from, b.to));
      const z = x(Math.max(b.from, b.to));
      const v = ok(st.value) ? st.value : 0;
      const fill = st.residual ? 'var(--warn)' : st.kind === 'total' ? 'var(--accent)' : v >= 0 ? 'var(--up)' : 'var(--down)';
      const attrs: Record<string, string | number> = {
        x: a,
        y: y + rowH * 0.2,
        width: Math.max(z - a, 1),
        height: rowH * 0.6,
        fill,
      };
      if (st.residual) {
        attrs['fill-opacity'] = 0.5;
        attrs.stroke = 'var(--warn)';
        attrs['stroke-dasharray'] = '3 2';
      }
      s('rect', attrs, g);
      // 다음 단계로 잇는 점선(누계 위치)
      const next = bars[i + 1];
      if (next && next.from !== null && o.steps[i + 1]?.kind !== 'total') {
        const xr = x(b.running);
        s('line', { x1: xr, x2: xr, y1: y + rowH * 0.8, y2: y + rowH * 1.2, stroke: 'var(--dim)', 'stroke-dasharray': '2 2' }, g);
      }
    }
    const txt = ok(st.value) ? fmt(st.value, st) : DASH;
    const cls = !ok(st.value) || b?.broken ? 'dim' : st.residual ? 'warn' : 'fg';
    s('text', { x: W - pad, y: y + rowH * 0.66, 'text-anchor': 'end', class: cls }, g, txt);
  });
  return box;
}
