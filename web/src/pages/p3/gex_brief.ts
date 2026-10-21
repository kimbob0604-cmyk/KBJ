// 페이지 3 'GEX 브리핑'(사용자 요청 2026-10-21 — 아침 브리핑 화면처럼: 감마 국면·첫 시험대·레벨마다 뜻 +
// 행사가별 콜/풋/순감마 막대 + 옵션 포지셔닝 손익분기). 데이터는 `/api/gex/briefing`(scope) 하나 — 문장은
// 서버 규칙(엔진 `kbj.engines.gex.briefing`), 화면은 그리기만. 로그인 전용(공개판은 자물쇠 틀만).
import type { components } from '../../api/types.gen';
import { h } from '../../ui/dom';
import { DASH, kstTime, num, type Num } from '../../ui/fmt';
import { s, svgBox } from '../../ui/svg';

type S = components['schemas'];
export type GexBriefing = S['GexBriefing'];
type KeyLevel = GexBriefing['levels'][number];
type StrikeNet = GexBriefing['strikes'][number];
type Strategy = GexBriefing['strategies'][number];

export const BRIEF_TITLE = 'GEX 브리핑';
export const BRIEF_DEF =
  '감마 국면(현재가가 감마 플립 위면 롱감마 — 딜러 헤지가 움직임을 누르고, 아래면 숏감마 — 움직임을 키운다), 오늘 첫 시험대, ' +
  '레벨마다 막히면/뚫리면 어떻게 읽는지. 아래 막대는 행사가별 콜(노랑)·풋(보라) 감마와 순감마(초록=양, 분홍=음) — 원/지수 1%. ' +
  '옵션 포지셔닝은 가장 가까운 만기 시세로 계산한 만기 손익(pt·원, 수수료·증거금 제외)이며 매매 권유가 아니다.';

const JO = 1e12;
const EOK = 1e8;

function finite(v: Num): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/** 원 → '+5.6조'·'-5,857억' */
export function money(v: Num): string {
  if (!finite(v)) return DASH;
  const sign = v > 0 ? '+' : v < 0 ? '-' : '';
  const a = Math.abs(v);
  return a >= JO ? `${sign}${(a / JO).toFixed(1)}조` : `${sign}${Math.round(a / EOK).toLocaleString('ko-KR')}억`;
}

/** 레벨 값 — 2.5 단위 행사가는 소수 한 자리, 정수면 정수 */
export function pt(v: Num): string {
  if (!finite(v)) return DASH;
  return Number.isInteger(v) ? num(v, 0) : num(v, 1);
}

const ARROW: Record<KeyLevel['kind'], string> = {
  em_up: '↑',
  call_wall2: '↑',
  call_wall: '↑',
  flip: '↑',
  spot: '▸',
  put_wall: '↓',
  put_wall2: '↓',
  em_down: '↓',
};

function levelTone(kind: KeyLevel['kind']): string {
  if (kind === 'spot') return 'accent';
  if (kind === 'call_wall' || kind === 'call_wall2') return 'cw';
  if (kind === 'put_wall' || kind === 'put_wall2') return 'pw';
  if (kind === 'flip') return 'warn';
  return 'dim';
}

function levelRow(lv: KeyLevel): HTMLElement {
  const dist =
    lv.kind === 'spot'
      ? '현재가'
      : `${num(lv.dist_pt, Math.abs(lv.dist_pt) % 1 ? 1 : 0, true)}pt · ${num(lv.dist_pct, 1, true)}%`;
  return h(
    'div',
    { class: `gb-lv gb-${levelTone(lv.kind)}`, data: { kind: lv.kind } },
    h('span', { class: 'gb-arrow', attrs: { 'aria-hidden': 'true' } }, ARROW[lv.kind]),
    h('div', { class: 'gb-val' }, h('b', null, pt(lv.value)), h('small', null, dist)),
    h(
      'div',
      { class: 'gb-txt' },
      h(
        'p',
        { class: 'gb-label' },
        lv.label,
        lv.strength ? h('span', { class: `gb-badge gb-${lv.strength === '강' ? 'strong' : 'weak'}` }, lv.strength) : null,
        finite(lv.gex) ? h('span', { class: 'gb-gex' }, money(lv.gex)) : null,
      ),
      (lv.lines ?? []).map((t) => h('p', { class: 'gb-line' }, t)),
    ),
  );
}

/** 행사가별 콜·풋·순감마 수평 막대(높은 행사가가 위) + 현재가·콜월·풋월 표시 */
export function strikeBars(rows: readonly StrikeNet[], b: Pick<GexBriefing, 'price' | 'levels'>): SVGSVGElement {
  const W = 640;
  const ROW = 20;
  const LEFT = 64;
  const RIGHT = 96;
  const H = Math.max(rows.length, 1) * ROW + 24;
  const svg = svgBox(`0 0 ${String(W)} ${String(H)}`, '행사가별 콜·풋·순감마', 'gb-bars');
  const max = Math.max(1, ...rows.flatMap((r) => [Math.abs(r.call), Math.abs(r.put), Math.abs(r.net)]));
  const mid = LEFT + (W - LEFT - RIGHT) / 2;
  const half = (W - LEFT - RIGHT) / 2;
  const x = (v: number): number => mid + (v / max) * half;
  const wall = (k: KeyLevel['kind']): number | undefined => b.levels.find((l) => l.kind === k)?.value;
  const cw = wall('call_wall');
  const pw = wall('put_wall');
  s('line', { x1: mid, x2: mid, y1: 4, y2: H - 20, stroke: 'var(--line)' }, svg);
  rows.forEach((r, i) => {
    const y = 8 + i * ROW;
    s('text', { x: LEFT - 8, y: y + 11, 'text-anchor': 'end', class: 'gb-k', fill: 'var(--dim)' }, svg, pt(r.strike));
    const bar = (v: number, dy: number, hgt: number, cls: string): void => {
      const x0 = Math.min(mid, x(v));
      s('rect', { x: x0, y: y + dy, width: Math.max(Math.abs(x(v) - mid), 0.5), height: hgt, class: cls }, svg);
    };
    bar(r.put, 2, 6, 'gb-put');
    bar(r.call, 2, 6, 'gb-call');
    bar(r.net, 9, 6, r.net >= 0 ? 'gb-pos' : 'gb-neg');
    const tag = r.strike === cw ? 'CW' : r.strike === pw ? 'PW' : null;
    if (tag) s('text', { x: W - RIGHT + 8, y: y + 11, class: `gb-tag gb-t${tag.toLowerCase()}` }, svg, `${tag} ${pt(r.strike)}`);
  });
  // 현재가 — 행사가 사이를 선형으로(행사가가 내림차순)
  const price = b.price;
  if (finite(price) && rows.length > 1) {
    let yy: number | null = null;
    for (let i = 0; i + 1 < rows.length; i += 1) {
      const a = rows[i];
      const c = rows[i + 1];
      if (a && c && price <= a.strike && price >= c.strike) {
        yy = 8 + (i + (a.strike - price) / (a.strike - c.strike || 1)) * ROW + 7;
        break;
      }
    }
    if (yy !== null) {
      s('line', { x1: LEFT, x2: W - RIGHT, y1: yy, y2: yy, stroke: 'var(--accent)', 'stroke-dasharray': '4 3' }, svg);
      s('text', { x: W - RIGHT + 8, y: yy + 4, class: 'gb-tag gb-spot', fill: 'var(--accent)' }, svg, `현재 ${pt(price)}`);
    }
  }
  s('text', { x: mid, y: H - 4, 'text-anchor': 'middle', class: 'gb-axis', fill: 'var(--dim)' }, svg, `← 풋·음(−)  ${money(max)}  콜·양(+) →`);
  return svg;
}

/** 시나리오 점(등락 %, 만기 손익 pt) → 손익 곡선 svg */
export function payoffChart(st: Strategy): SVGSVGElement {
  const W = 240;
  const H = 90;
  const svg = svgBox(`0 0 ${String(W)} ${String(H)}`, `${st.name} 만기 손익`, 'gb-payoff');
  const pts = st.scenario;
  if (pts.length < 2) return svg;
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const lo = Math.min(0, ...ys);
  const hi = Math.max(0, ...ys);
  const sx = (v: number): number => 6 + ((v - x0) / (x1 - x0 || 1)) * (W - 12);
  const sy = (v: number): number => 6 + ((hi - v) / (hi - lo || 1)) * (H - 12);
  s('line', { x1: 6, x2: W - 6, y1: sy(0), y2: sy(0), stroke: 'var(--line)' }, svg);
  s('line', { x1: sx(0), x2: sx(0), y1: 4, y2: H - 4, stroke: 'var(--line)', 'stroke-dasharray': '2 3' }, svg);
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join(' ');
  s('path', { d, fill: 'none', stroke: 'var(--accent)', 'stroke-width': 2 }, svg);
  return svg;
}

function legText(g: Strategy['legs'][number]): string {
  return `${g.side === 'buy' ? '매수' : '매도'} ${g.cp === 'C' ? '콜' : '풋'} ${pt(g.strike)} @${num(g.price, 2)}`;
}

function wonOf(ptv: Num, mult: number): string {
  return finite(ptv) ? `${Math.round(ptv * mult).toLocaleString('ko-KR')}원` : DASH;
}

/** 카드 표는 5칸(−10·−5·0·+5·+10%) — 곡선은 전부 쓴다 */
export function scenCols(st: Strategy): Strategy['scenario'] {
  const keep = new Set([-10, -5, 0, 5, 10]);
  const picked = st.scenario.filter((p) => keep.has(p[0]));
  return picked.length ? picked : st.scenario;
}

function strategyCard(st: Strategy, mult: number, price: Num): HTMLElement {
  const prem = st.net_premium >= 0 ? `받음 ${num(st.net_premium, 2)}pt` : `냄 ${num(-st.net_premium, 2)}pt`;
  const be = st.breakevens.map((v) => {
    const d = finite(price) ? ` (${num(((v - price) / price) * 100, 1, true)}%)` : '';
    return `${pt(v)}${d}`;
  });
  return h(
    'article',
    { class: 'gb-card', data: { key: st.key } },
    h('h5', null, st.name),
    h('p', { class: 'gb-idea' }, st.idea),
    h('ul', { class: 'gb-legs' }, st.legs.map((g) => h('li', null, legText(g)))),
    h(
      'dl',
      { class: 'gb-nums' },
      h('dt', null, '프리미엄'),
      h('dd', null, `${prem} · ${wonOf(Math.abs(st.net_premium), mult)}`),
      h('dt', null, '손익분기'),
      h('dd', null, be.length ? be.join(' / ') : DASH),
      h('dt', null, '최대 이익'),
      h('dd', null, st.max_profit === null ? '제한 없음' : `${num(st.max_profit, 2)}pt · ${wonOf(st.max_profit, mult)}`),
      h('dt', null, '최대 손실'),
      h('dd', { class: st.max_loss === null ? 'up' : '' }, st.max_loss === null ? '제한 없음' : `${num(st.max_loss, 2)}pt · ${wonOf(st.max_loss, mult)}`),
    ),
    payoffChart(st),
    h(
      'table',
      { class: 'gb-scen' },
      h('tr', null, scenCols(st).map((p) => h('th', null, `${num(p[0], 0, true)}%`))),
      h('tr', null, scenCols(st).map((p) => h('td', { class: p[1] > 0 ? 'down' : p[1] < 0 ? 'up' : 'dim' }, num(p[1], 1, true)))),
    ),
    st.note ? h('p', { class: 'gb-note' }, st.note) : null,
  );
}

/** 브리핑 본문 */
export function briefView(b: GexBriefing): HTMLElement {
  const regimeCls = b.regime === 'short' ? 'gb-short' : b.regime === 'long' ? 'gb-long' : 'dim';
  const em = b.levels.find((l) => l.kind === 'em_up');
  const ed = b.levels.find((l) => l.kind === 'em_down');
  const sub = [
    b.session_open ? null : `장 마감 — 마지막 사이클 ${kstTime(b.cycle_at)}`,
    `현재 ${pt(b.price)}`,
    em && ed ? `1일 예상범위 ${pt(ed.value)}~${pt(em.value)}` : null,
    b.expiry ? `전략 만기 ${b.expiry}${b.days_to_expiry !== null ? ` D-${String(b.days_to_expiry)}` : ''}` : null,
  ].filter(Boolean);
  return h(
    'div',
    { class: 'gb' },
    h(
      'div',
      { class: 'gb-head' },
      b.regime_label ? h('span', { class: `gb-chip ${regimeCls}` }, b.regime_label) : null,
      h('span', { class: 'gb-net' }, `순감마 ${money(b.net_gex)}`, finite(b.net_vs_avg) ? ` · 평소의 ${num(b.net_vs_avg, 1)}배` : ''),
    ),
    b.headline ? h('h4', { class: 'gb-headline' }, b.headline) : null,
    h('p', { class: 'gb-sub dim' }, sub.join(' · ')),
    b.levels.length ? h('section', { class: 'gb-levels', attrs: { 'aria-label': '핵심 레벨' } }, h('h5', null, '핵심 레벨 + 요점'), b.levels.map(levelRow)) : null,
    b.strikes.length
      ? h(
          'section',
          { class: 'gb-structure', attrs: { 'aria-label': '오늘의 구조' } },
          h('h5', null, '오늘의 구조 — 행사가별 감마'),
          h('p', { class: 'gb-legend dim' }, '노랑 = 콜 감마 · 보라 = 풋 감마 · 아래 막대 = 순감마(초록 양 · 분홍 음)'),
          strikeBars(b.strikes, b),
        )
      : null,
    b.strategies.length
      ? h(
          'section',
          { class: 'gb-strats', attrs: { 'aria-label': '옵션 포지셔닝' } },
          h('h5', null, `옵션 포지셔닝 — 만기 손익(1계약 = 1pt × ${b.multiplier.toLocaleString('ko-KR')}원)`),
          h('div', { class: 'gb-cards' }, b.strategies.map((st) => strategyCard(st, b.multiplier, b.price))),
        )
      : null,
  );
}
