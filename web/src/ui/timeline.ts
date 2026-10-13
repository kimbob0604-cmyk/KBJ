// 타임라인 — 오버행 청구(행사) 기간·추정 보호예수 해제일처럼 날짜 구간 여러 줄(docs/p4_design.md §1.2·§6.2 '공시·지분').
//
// - 한 줄 = 한 항목: 시작~끝 막대. 날짜가 하나뿐이면 점(마름모), 둘 다 없으면 그 줄은 '날짜 없음' 글자만.
// - estimated(추정 — 예: 보호예수 출처 없음 → 법정 전매제한으로 계산한 해제일, D-P4-18)는 점선 막대 + '추정' 꼬리표.
// - today 를 주면 세로 점선(오늘). 시계는 주입받는다(벽시계 금지 — 호출자가 ctx.now() 로).
// - 날짜는 'YYYY-MM-DD'(달력일) — 잘못된 날짜는 없는 것으로 본다. 색은 스킨 토큰만.
import { DASH, kstDate } from './fmt';
import { s, svgBox } from './svg';

export interface TimelineItem {
  label: string;
  start: string | null;
  end: string | null;
  /** 추정 날짜(출처가 직접 주지 않음) */
  estimated?: boolean;
  /** 종류(CB·BW·EB·유상·보호예수 …) — data-kind 로 남긴다 */
  kind?: string;
}

export interface TimelineOptions {
  /** 접근 이름 — 예 'CB·BW 청구 기간' */
  label: string;
  items: readonly TimelineItem[];
  /** 오늘(KST 'YYYY-MM-DD' 또는 Date) */
  today?: string | Date | null;
  w?: number;
  rowH?: number;
}

const DAY_MS = 86_400_000;

/** 'YYYY-MM-DD' → 1970-01-01 부터 센 날 수. 형식·달력이 틀리면 null */
export function dayNumber(d: string | null | undefined): number | null {
  if (!d) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d);
  if (!m) return null;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const da = Number(m[3]);
  const t = Date.UTC(y, mo - 1, da);
  const back = new Date(t);
  if (back.getUTCFullYear() !== y || back.getUTCMonth() !== mo - 1 || back.getUTCDate() !== da) return null;
  return Math.round(t / DAY_MS);
}

export function timeline(o: TimelineOptions): SVGSVGElement {
  const W = o.w ?? 640;
  const rowH = o.rowH ?? 22;
  const labelW = 150;
  const pad = 8;
  const axisH = 18;
  const n = o.items.length;
  const H = Math.max(1, n) * rowH + pad * 2 + axisH;
  const box = svgBox(`0 0 ${W} ${H}`, o.label, 'timeline');

  const spans = o.items.map((it) => {
    let a = dayNumber(it.start);
    let b = dayNumber(it.end);
    if (a !== null && b !== null && b < a) [a, b] = [b, a];
    return { a, b };
  });
  const todayStr = o.today instanceof Date ? kstDate(o.today) : (o.today ?? null);
  const today = dayNumber(todayStr);
  const days: number[] = [];
  for (const sp of spans) {
    if (sp.a !== null) days.push(sp.a);
    if (sp.b !== null) days.push(sp.b);
  }

  if (n === 0 || days.length === 0) {
    s('text', { x: W / 2, y: H / 2 + 4, 'text-anchor': 'middle' }, box, n === 0 ? '항목 없음' : '날짜 없음');
    box.dataset.empty = 'true';
    return box;
  }

  if (today !== null) days.push(today);
  let lo = Math.min(...days);
  let hi = Math.max(...days);
  if (hi === lo) {
    lo -= 15;
    hi += 15;
  }
  const plotL = labelW + pad;
  const plotW = W - plotL - pad;
  const x = (d: number): number => plotL + ((d - lo) / (hi - lo)) * plotW;
  const toIso = (d: number): string => new Date(d * DAY_MS).toISOString().slice(0, 10);

  o.items.forEach((it, i) => {
    const sp = spans[i] ?? { a: null, b: null };
    const y = pad + i * rowH;
    const mid = y + rowH / 2;
    const g = s('g', { 'data-item': i }, box);
    if (it.kind) g.setAttribute('data-kind', it.kind);
    if (it.estimated) g.setAttribute('data-estimated', 'true');
    // 글자색은 CSS 클래스로(svg text 기본 --dim — base.css)
    s('text', { x: labelW, y: mid + 4, 'text-anchor': 'end', class: 'fg' }, g, it.label);
    const stroke = it.estimated ? 'var(--est)' : 'var(--accent)';
    if (sp.a !== null && sp.b !== null) {
      const attrs: Record<string, string | number> = {
        x: x(sp.a),
        y: y + rowH * 0.25,
        width: Math.max(x(sp.b) - x(sp.a), 2),
        height: rowH * 0.5,
        fill: it.estimated ? 'none' : 'var(--accent)',
        'fill-opacity': it.estimated ? 1 : 0.45,
        stroke,
      };
      if (it.estimated) attrs['stroke-dasharray'] = '3 2';
      s('rect', attrs, g);
      s('title', {}, g, `${it.label} ${toIso(sp.a)} ~ ${toIso(sp.b)}${it.estimated ? ' (추정)' : ''}`);
    } else if (sp.a !== null || sp.b !== null) {
      const d = sp.a ?? sp.b ?? lo;
      const cx = x(d);
      const r = rowH * 0.28;
      const attrs: Record<string, string | number> = {
        points: `${cx},${mid - r} ${cx + r},${mid} ${cx},${mid + r} ${cx - r},${mid}`,
        fill: it.estimated ? 'none' : 'var(--accent)',
        stroke,
      };
      if (it.estimated) attrs['stroke-dasharray'] = '2 2';
      s('polygon', attrs, g);
      s('title', {}, g, `${it.label} ${toIso(d)}${it.estimated ? ' (추정)' : ''}`);
    } else {
      s('text', { x: plotL, y: mid + 4 }, g, '날짜 없음');
    }
    if (it.estimated && (sp.a !== null || sp.b !== null)) {
      const endX = x(sp.b ?? sp.a ?? lo);
      const tx = Math.min(endX + 6, W - pad - 24);
      s('text', { x: tx, y: mid + 4 }, g, '추정');
    }
  });

  // 축: 처음·끝 날짜 + 오늘
  const axisY = H - axisH + 12;
  s('text', { x: plotL, y: axisY }, box, toIso(lo));
  s('text', { x: W - pad, y: axisY, 'text-anchor': 'end' }, box, toIso(hi));
  if (today !== null) {
    const tx = x(today);
    s('line', { x1: tx, x2: tx, y1: pad / 2, y2: H - axisH, stroke: 'var(--warn)', 'stroke-dasharray': '3 3', 'data-today': todayStr ?? DASH }, box);
  }
  return box;
}
