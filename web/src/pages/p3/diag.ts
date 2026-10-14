// 페이지 3 진단 패널(docs/p7_design.md §6.1 ①②③⑤·§6.2 — 묶음 DIAG, metrics §D0~§D9).
//
// - 서명은 하나: `mount(ctx)` — 페이지 격자(ctx.root)에 자기 패널을 span 칸으로 붙인다. 차트는 반폭(span-6)으로 값 칸 옆에
//   나란히(전체 폭 칸에 차트 하나면 viewBox 가 커져 글자가 커진다). 이벤트 달력은 차트 없이 줄 목록(span-12).
// - 등급: 공공지표 진단·이벤트는 공개(공개판은 공개 파일 diag/* — 공개 지표·게이지·이벤트만), 판정 띠·시장 진단은 로그인
//   (공개판은 자물쇠 — 요청하지 않는다). 데이터 없음(404)은 '.msg' 안내, 그 밖 실패만 '.msg.err'.
// - 데이터 키: 로그인 = API 'diag/summary'·'diag/history'(key·days)·'diag/events', 공개 = 공개 파일 'diag/summary'·
//   'diag/history'(공개 지표 전부)·'diag/events'. 요약은 페이지 한 번에 한 번만 부른다(패널끼리 나눈다).
// - 값이 없으면 '—'와 사유(0 으로 그리지 않는다). 상태 점수 +1 은 up, −1 은 down 색.
import type { components } from '../../api/types.gen';
import type { PageContext } from '../../app/types';
import { DataError, type Envelope } from '../../data/source';
import { h, replace } from '../../ui/dom';
import { DASH, num } from '../../ui/fmt';
import { gauge, type GaugeBand } from '../../ui/gauge';
import { kv, type KvItem } from '../../ui/kv';
import { lineChart } from '../../ui/line';
import { errorText, type Panel, panel } from '../../ui/panel';
import { seg } from '../../ui/seg';

type S = components['schemas'];
export type DiagSummary = S['DiagSummary'];
export type DiagHistory = S['DiagHistory'];
export type DiagEvents = S['DiagEvents'];
type Indicator = DiagSummary['indicators'][number];
type DiagEvent = DiagSummary['events'][number];
/** 공개 파일 summary — 로그인 판정 칸(verdict·fear_greed)이 키조차 없다 */
export type PublicSummary = Pick<DiagSummary, 'trade_date' | 'gauge' | 'indicators' | 'events'>;
/** 공개 파일 history — 공개 지표 전부 */
export interface PublicHistory {
  series: Record<string, Pick<DiagHistory, 'unit' | 'points'>>;
}

export const DIAG_SOURCE = 'KBJ 진단(공공: 한국은행 ECOS·금투협·관세청 / 로그인: KRX·KIS)';
/** 빈 리더·수집 전 안내(작업·예정 시각) */
export const DIAG_HINT = {
  public: 'diag.public 평일 08:40',
  daily: 'diag.daily 거래일 16:00',
} as const;
export const TITLES = {
  verdict: '종합 판정',
  fear: '국장 공포탐욕',
  public: '공공지표 진단',
  publicHist: '공공지표 추이',
  market: '시장 진단',
  marketHist: '시장 진단 추이',
  events: '이벤트 달력',
} as const;

/** 지표 키 → 화면 이름 */
export const NAMES: Readonly<Record<string, string>> = {
  credit_spread: '신용스프레드',
  credit_balance: '신용잔고 추세',
  deposits: '예탁금 추세',
  export_momentum: '수출 모멘텀',
  rate_direction: '금리 방향',
  semis_concentration: '반도체 쏠림',
  cyclical_defensive: '경기민감 대 방어',
  breadth_ma20: '시장폭(20일선 위)',
  breadth_new_high: '신고가 비율',
  kr_vs_global: '한국 대 글로벌',
  foreign_flow: '수급(외국인 20일)',
  fear_greed: '국장 공포탐욕',
};
export const STATES: Readonly<Record<string, string>> = {
  extreme_low: '극단 낮음',
  low: '낮음',
  normal: '보통',
  high: '높음',
  extreme_high: '극단 높음',
  narrow: '좁음',
  wide: '넓음',
};
export const VERDICTS: Readonly<Record<string, string>> = {
  risk_on: '위험 선호',
  neutral: '중립',
  risk_off: '위험 회피',
  hold: '판정 보류',
};
export const EVENT_KINDS: Readonly<Record<string, string>> = {
  expiry: '만기',
  mpc: '금통위',
  export_flash: '수출 속보',
  earnings_cluster: '실적 집중',
};
const FLAGS: Readonly<Record<string, string>> = { overheat: '과열', fragile: '취약' };
/** 공포탐욕 다섯 구간(metrics §D7 — 경계 25·45·56·76) */
export const FEAR_BANDS: readonly GaugeBand[] = [
  { label: '극단적 공포', tone: 'down' },
  { label: '공포', tone: 'warn' },
  { label: '중립', tone: 'dim' },
  { label: '탐욕', tone: 'accent' },
  { label: '극단적 탐욕', tone: 'up' },
];
const FEAR_EDGES = [25, 45, 56, 76] as const;
const COMPONENTS: Readonly<Record<string, string>> = {
  vol: '변동성(뒤집음)',
  breadth: '시장폭',
  credit_balance: '신용잔고 변화',
  foreign: '외국인 수급',
  credit_spread: '신용스프레드(뒤집음)',
};

function finite(v: number | null | undefined): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/** 지표 값 글자 — 원은 억, 그 밖은 단위 붙여 소수 둘째. 값 없으면 '—' */
export function valueText(v: number | null | undefined, unit: string): string {
  if (!finite(v)) return DASH;
  if (unit === '원') return `${num(v / 1e8, 0, true)}억`;
  return `${num(v, 2, unit !== '%p')}${unit}`;
}

export function scoreCls(score: number | null | undefined): string | undefined {
  if (score === 1) return 'up';
  if (score === -1) return 'down';
  return score === 0 ? 'dim' : undefined;
}

function labelCls(label: string): string {
  return label === 'risk_on' ? 'up' : label === 'risk_off' ? 'down' : 'dim';
}

/** 지표 카드 한 칸 — 값·구간·백분위(값 없으면 사유) */
export function indicatorItem(c: Indicator): KvItem {
  const name = NAMES[c.key] ?? c.key;
  if (!finite(c.value)) return { label: name, na: c.reasons?.[0] ?? '값 없음' };
  const state = c.state ? (STATES[c.state] ?? c.state) : '구간 없음';
  const pct = finite(c.pct_rank) ? ` · ${num(c.pct_rank, 0)}%ile` : '';
  const flags = (c.flags ?? []).map((f) => FLAGS[f] ?? f).join('·');
  const cls = scoreCls(c.score);
  const hint = c.state ? undefined : c.reasons?.find((r) => r.startsWith('이력 부족'));
  return {
    label: name,
    value: `${valueText(c.value, c.unit)} · ${state}${pct}${flags ? ` · ${flags}` : ''}`,
    ...(cls ? { cls } : {}),
    ...(hint ? { hint } : {}),
    quality: c.quality,
  };
}

function hasLeft(e: DiagEvent): e is DiagEvent & { days_left: number } {
  return typeof e.days_left === 'number';
}

export function eventText(e: DiagEvent): string {
  const kind = EVENT_KINDS[e.kind] ?? e.kind;
  if (!hasLeft(e)) return kind;
  return `${kind} · ${e.days_left === 0 ? '오늘' : `D-${String(e.days_left)}`}`;
}

function notFound(err: unknown): boolean {
  return err instanceof DataError && err.status === 404;
}

function fail(p: Panel, err: unknown, hint: string): void {
  if (notFound(err)) p.showMessage(`${errorText(err)} — ${hint}`);
  else p.showError(err);
}

// 요약은 페이지 한 번에 한 번(패널끼리 나눈다)
const summaries = new WeakMap<PageContext, Promise<Envelope<DiagSummary | PublicSummary>>>();
function summary(ctx: PageContext): Promise<Envelope<DiagSummary | PublicSummary>> {
  let p = summaries.get(ctx);
  if (!p) {
    p = ctx.source.get<DiagSummary | PublicSummary>('diag/summary');
    summaries.set(ctx, p);
  }
  return p;
}

function lvl(label: string, value: string, cls?: string): HTMLElement {
  return h('div', { class: 'lvl' }, h('span', null, label), h('span', { class: 'dim' }), h('span', cls ? { class: cls } : null, value));
}

function historyChart(label: string, unit: string, pts: DiagHistory['points']): HTMLElement {
  if (!pts.some((p) => finite(p.value))) return h('p', { class: 'msg' }, '이력 없음');
  return lineChart({
    label,
    x: pts.map((p) => p.trade_date),
    series: [{ label, values: pts.map((p) => p.value ?? null) }],
    format: (v) => valueText(v, unit),
    h: 180,
  }).el;
}

/** ① 판정 띠 패널 둘(틀만 — 공개 빌드는 이 틀만 쓰고 본문 mountVerdict 는 번들에서 빠진다, P8 WRAP) */
export function verdictPanels(ctx: PageContext): { vp: Panel; fp: Panel } {
  const common = { tier: 'login' as const, lock: 'quote_based', span: 6 as const, src: DIAG_SOURCE };
  const vp = panel(ctx.tier, {
    ...common,
    title: TITLES.verdict,
    def: '공포탐욕·신용스프레드·시장폭·한국 대 글로벌·수급·GEX 체제 점수 합 — ≥2 위험 선호, ≤−2 위험 회피',
  });
  const fp = panel(ctx.tier, { ...common, title: TITLES.fear, def: '다섯 성분 250거래일 백분위 평균(0~100) — 성분 4개 미만이면 값 없음' });
  ctx.root.append(vp.el, fp.el);
  return { vp, fp };
}

/** ① 판정 띠 — 종합 판정·국장 공포탐욕(gauge)·이벤트 꼬리표(로그인) */
export async function mountVerdict(ctx: PageContext): Promise<void> {
  const { vp, fp } = verdictPanels(ctx);
  if (vp.locked) return; // 공개판 — 자물쇠만, 요청하지 않는다
  let env: Envelope<DiagSummary>;
  try {
    env = (await summary(ctx)) as Envelope<DiagSummary>;
  } catch (err) {
    fail(vp, err, DIAG_HINT.daily);
    fail(fp, err, DIAG_HINT.daily);
    return;
  }
  const d = env.data;
  vp.setSource(env);
  fp.setSource(env);
  const v = d.verdict;
  if (!v) vp.showMessage(`판정 아직 없음 — ${DIAG_HINT.daily}`);
  else {
    const tags = (v.event_flags ?? []).map((k) => h('span', { class: 'chip warn' }, EVENT_KINDS[k] ?? k));
    replace(
      vp.body,
      h('p', { class: 'verdict' }, h('b', { class: labelCls(v.label) }, VERDICTS[v.label] ?? v.label), finite(v.score) ? ` 점수 ${num(v.score, 0, true)}` : ''),
      kv(
        (v.top ?? []).map((t) => ({ label: '근거', value: t })),
        { layout: 'rows' },
      ),
      d.caution ? h('p', { class: 'warn' }, '이벤트 주의 ', tags) : null,
      v.reasons?.length ? h('p', { class: 'dim' }, v.reasons.join(' · ')) : null,
    );
  }
  const f = d.fear_greed;
  if (!f) fp.showMessage(`공포탐욕 아직 없음 — ${DIAG_HINT.daily}`);
  else {
    const comps = Object.entries(f.components).map(([k, x]) => ({ label: COMPONENTS[k] ?? k, value: finite(x) ? num(x, 0) : null }));
    replace(
      fp.body,
      gauge({ label: '국장 공포탐욕(0~100)', value: f.score ?? null, edges: FEAR_EDGES, bands: FEAR_BANDS }),
      kv(comps, { layout: 'rows' }),
      f.reasons?.length ? h('p', { class: 'dim' }, f.reasons.join(' · ')) : null,
    );
  }
}

/** ② 공공지표 진단 — .lvl 행 다섯 + 매크로 위험 게이지(공개) + 추이(반폭) */
export async function mountPublic(ctx: PageContext): Promise<void> {
  const common = { tier: 'public' as const, span: 6 as const, src: DIAG_SOURCE };
  const p = panel(ctx.tier, { ...common, title: TITLES.public, def: '공공데이터만으로 만든 판정 — 신용스프레드·신용잔고·예탁금·수출 모멘텀·금리 방향' });
  const hp = panel(ctx.tier, { ...common, title: TITLES.publicHist });
  ctx.root.append(p.el, hp.el);
  let env: Envelope<DiagSummary | PublicSummary>;
  try {
    env = await summary(ctx);
  } catch (err) {
    fail(p, err, DIAG_HINT.public);
    fail(hp, err, DIAG_HINT.public);
    return;
  }
  const d = env.data;
  p.setSource(env);
  const cards = d.indicators.filter((c) => c.tier === 'public');
  const g = d.gauge;
  const rows = cards.map((c) => {
    const it = indicatorItem(c);
    return lvl(it.label, it.na ?? (typeof it.value === 'string' ? it.value : DASH), it.cls);
  });
  replace(
    p.body,
    g
      ? h('p', { class: 'verdict' }, '매크로 위험 게이지 ', h('b', { class: labelCls(g.label) }, VERDICTS[g.label] ?? g.label), finite(g.score) ? ` 점수 ${num(g.score, 0, true)}` : '')
      : h('p', { class: 'msg' }, '게이지 아직 없음'),
    h('div', { class: 'levels' }, rows),
  );
  const keys = cards.map((c) => c.key);
  const first = keys[0];
  if (!first) {
    hp.showMessage('공공지표 없음');
    return;
  }
  const load = async (key: string): Promise<Pick<DiagHistory, 'unit' | 'points'> | undefined> => {
    if (ctx.tier === 'public') {
      const e = await ctx.source.get<PublicHistory>('diag/history');
      hp.setSource(e);
      return e.data.series[key];
    }
    const e = await ctx.source.get<DiagHistory>('diag/history', { key, days: '250' });
    hp.setSource(e);
    return e.data;
  };
  const draw = async (key: string): Promise<void> => {
    try {
      const pts = await load(key);
      replace(hp.body, pick.el, pts ? historyChart(NAMES[key] ?? key, pts.unit, pts.points) : h('p', { class: 'msg' }, '이력 없음'));
    } catch (err) {
      fail(hp, err, DIAG_HINT.public);
      hp.body.prepend(pick.el);
    }
  };
  const options = keys.map((k) => ({ value: k, label: NAMES[k] ?? k }));
  const pick = seg<string>({ label: '공공지표', options, value: first, onChange: (k) => void draw(k) });
  await draw(first);
}

/** ③ 시장 진단 — 지표 카드(로그인) + 250일 추이(반폭) */
/** ③ 시장 진단 패널 둘(틀만 — verdictPanels 와 같은 까닭) */
export function marketPanels(ctx: PageContext): { p: Panel; hp: Panel } {
  const common = { tier: 'login' as const, lock: 'quote_based', span: 6 as const, src: DIAG_SOURCE };
  const p = panel(ctx.tier, { ...common, title: TITLES.market, def: '250거래일 백분위 다섯 칸 — 반도체 쏠림 극단 높음은 취약 꼬리표(점수 밖)' });
  const hp = panel(ctx.tier, { ...common, title: TITLES.marketHist });
  ctx.root.append(p.el, hp.el);
  return { p, hp };
}

export async function mountMarket(ctx: PageContext): Promise<void> {
  const { p, hp } = marketPanels(ctx);
  if (p.locked) return;
  let env: Envelope<DiagSummary>;
  try {
    env = (await summary(ctx)) as Envelope<DiagSummary>;
  } catch (err) {
    fail(p, err, DIAG_HINT.daily);
    fail(hp, err, DIAG_HINT.daily);
    return;
  }
  p.setSource(env);
  const cards = env.data.indicators.filter((c) => c.tier === 'login');
  if (!cards.length) {
    p.showMessage(`시장 진단 아직 없음 — ${DIAG_HINT.daily}`);
    hp.showMessage(`시장 진단 아직 없음 — ${DIAG_HINT.daily}`);
    return;
  }
  replace(p.body, kv(cards.map(indicatorItem)));
  const keys = [...cards.map((c) => c.key), 'fear_greed'];
  const draw = async (key: string): Promise<void> => {
    try {
      const e = await ctx.source.get<DiagHistory>('diag/history', { key, days: '250' });
      hp.setSource(e);
      replace(hp.body, pick.el, historyChart(NAMES[key] ?? key, e.data.unit, e.data.points));
    } catch (err) {
      fail(hp, err, DIAG_HINT.daily);
      hp.body.prepend(pick.el);
    }
  };
  const start = keys.includes('breadth_ma20') ? 'breadth_ma20' : 'fear_greed';
  const options = keys.map((k) => ({ value: k, label: NAMES[k] ?? k }));
  const pick = seg<string>({ label: '시장 지표', options, value: start, onChange: (k) => void draw(k) });
  await draw(start);
}

/** ⑤ 이벤트 달력 — 만기·금통위·수출 속보·실적 집중 30일(공개) — 줄 목록(차트 없음) */
export async function mountEvents(ctx: PageContext): Promise<void> {
  const p = panel(ctx.tier, {
    title: TITLES.events,
    tier: 'public',
    span: 12,
    src: DIAG_SOURCE,
    def: '만기(캘린더 정본)·금통위(한국은행 공표 일정)·수출 속보 공표일(1·11·21일)·실적 집중(공시 예고 10곳 이상)',
  });
  ctx.root.append(p.el);
  try {
    const env = await ctx.source.get<DiagEvents>('diag/events');
    p.setSource(env);
    const ahead = env.data.events.filter(hasLeft);
    if (!ahead.length) {
      p.showMessage('30일 안 이벤트 없음');
      return;
    }
    replace(p.body, h('div', { class: 'levels' }, ahead.map((e) => lvl(e.date, `${e.title} · ${eventText(e)}`, e.days_left <= 1 ? 'warn' : undefined))));
  } catch (err) {
    fail(p, err, DIAG_HINT.public);
  }
}
