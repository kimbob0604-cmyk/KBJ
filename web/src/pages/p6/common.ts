// 페이지 6 매크로 공통 — 시리즈 카드(최신값·변화·as_of·출처·quality)·선 차트·탭 키(docs/p5_design.md §6, D-P5-5).
//
// - 로그인: API 'macro/tabs'·'macro/series?tab=…'. 공개: 공개 파일 'macro/tabs'·'macro/<탭>'(public.export — 같은 모양).
// - 값이 없거나 '확인 전(#43)'인 시리즈는 0 이 아니라 '—' + 사유(na_reason). 결측 점은 선을 끊는다(ui/line).
// - 반올림은 화면에서만(metrics §0).
import type { components } from '../../api/types.gen';
import type { DataSource, Tier } from '../../data/source';
import { h } from '../../ui/dom';
import { DASH, kstStamp, num, signClass } from '../../ui/fmt';
import { lineChart } from '../../ui/line';
import { QUALITY_LABEL } from '../../ui/quality';

type S = components['schemas'];
export type MacroTabs = S['MacroTabs'];
export type MacroTab = S['MacroTab'];
export type MacroSeries = S['MacroSeries'];
export type MacroSeriesSet = S['MacroSeriesSet'];

export interface P6Ctx {
  readonly tier: Tier;
  readonly source: DataSource;
  readonly now: () => Date;
}

/** 탭 시리즈 묶음의 키 — 로그인 API 와 공개 파일이 다르다 */
export function seriesKey(tier: Tier, tab: string): { key: string; params?: { tab: string } } {
  return tier === 'login' ? { key: 'macro/series', params: { tab } } : { key: `macro/${tab}` };
}

export const MACRO_HINT = 'macro.morning 06:20 · macro.monthly 09:10 · macro.evening 17:00(확인 전 #43~#46)';
/** 카드 차트의 최근 점 수(일별 약 1년, 월·분기는 전부) */
export const CHART_POINTS: Record<string, number> = { D: 260, W: 104, M: 120, Q: 40, A: 20 };

function unitText(v: number, unit: string, digits: number): string {
  if (unit === '%' || unit === '%p') return `${num(v, digits)}${unit}`;
  return `${num(v, digits)} ${unit}`;
}

function digitsOf(unit: string, v: number): number {
  if (unit === '%' || unit === '%p') return 2;
  return Math.abs(v) >= 1000 ? 0 : 2;
}

/** 변화 문구 — %·%p 단위는 %p(bp 아님), 그 밖은 차와 변화율 */
export function changeText(s: Pick<MacroSeries, 'change' | 'change_pct' | 'unit'>): string {
  const c = s.change;
  if (typeof c !== 'number' || !Number.isFinite(c)) return DASH;
  if (s.unit === '%' || s.unit === '%p') return `${num(c, 2, true)}%p`;
  const pct = s.change_pct;
  const head = num(c, digitsOf(s.unit, c), true);
  return typeof pct === 'number' && Number.isFinite(pct) ? `${head} (${num(pct, 2, true)}%)` : head;
}

/** 시리즈 카드 하나 */
export function seriesCard(s: MacroSeries): HTMLElement {
  const v = s.latest?.value;
  const has = typeof v === 'number' && Number.isFinite(v);
  const value = has ? unitText(v, s.unit, digitsOf(s.unit, v)) : DASH;
  const meta = [
    s.source,
    s.latest ? `기준 ${s.latest.period}` : null,
    s.as_of ? `받음 ${kstStamp(s.as_of)}` : null,
    s.quality && s.quality !== 'ok' ? QUALITY_LABEL[s.quality] : null,
    s.tier === 'private' ? '로그인 등급' : null,
    s.derived ? '파생' : null,
  ].filter((x): x is string => !!x);
  const pts = s.points.slice(-(CHART_POINTS[s.freq] ?? 120));
  const card = h(
    'article',
    { class: `tile mcard${s.quality === 'stale' ? ' stale' : ''}`, data: { series: s.series_id } },
    h('span', { class: 'n' }, s.title),
    h(
      'span',
      { class: 'p' },
      h('span', { class: has ? 'v' : 'v dim' }, value),
      ' ',
      h('span', { class: `chg ${signClass(s.change ?? null)}` }, changeText(s)),
    ),
    s.na_reason ? h('span', { class: 'n na' }, s.na_reason) : null,
    h('span', { class: 'n src' }, meta.join(' · ')),
  );
  if (pts.some((p) => typeof p.value === 'number')) {
    card.append(
      lineChart({
        label: `${s.title} 추이`,
        x: pts.map((p) => p.period),
        series: [{ label: s.title, values: pts.map((p) => p.value ?? null) }],
        endLabels: false,
        format: (x) => num(x, digitsOf(s.unit, x)),
        h: 90,
      }).el,
    );
  }
  return card;
}

/** 시리즈 카드 묶음(격자) */
export function seriesGrid(series: readonly MacroSeries[]): HTMLElement {
  return h('div', { class: 'idx mgrid' }, series.map(seriesCard));
}
