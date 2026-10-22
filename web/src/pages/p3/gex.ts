// 페이지 3 GEX 레벨 패널(docs/p7_design.md §6.1 ④·§6.2 — 묶음 ENG, metrics §G2·§G3·§G4·§G6).
//
// - 로그인 전용(KIS 옵션 체인 — 공개판은 자물쇠 'options', 요청하지 않는다).
// - 배치: 'GEX 프로필'(행사가 막대·현재가·Flip 선 — span-6) + 'GEX 레벨'(ladder + .lvl 행 — span-6) 나란히,
//   '확장 지표'(VEX·CEX·25Δ 스큐·HIRO-lite 값 칸 — span-12, 차트 없음). 범위 seg(all·nearest·0dte)는 레벨 패널에
//   있고 바꾸면 두 패널을 그 범위로 다시 그린다(범위마다 한 번만 부른다).
// - 섀도 지표(§10 Q3)는 값 대신 '섀도 검증 중'. 엔진 stale 이면 패널 머리 '지연'(+ 사유), 장 밖이면
//   '마지막 세션 — <시각>'. 야간 투자자별은 '수집 안 함(야간)'(D-P7-6).
// - 데이터 키: API 'gex/levels'·'gex/profile'(scope)·'gex/metrics'(name)·'gex/flow'. 아직 없음(404)은 '.msg',
//   그 밖 실패만 '.msg.err'(삼키지 않는다). 한 패널의 실패는 그 패널 안에서만(절대 규칙 4).
// - 실시간: GEX_REFRESH_MS(30초 — API 캐시 장중 TTL 과 같다)마다 지금 범위의 브리핑·레벨·프로필과 확장 지표를 다시
//   읽는다(범위 캐시는 그때 비운다). 탭이 안 보이면 주기 실행기가 멈추고, 다시 보이면 바로 한 번(app/refresh.ts).
//   2026-10-22 사용자 '3번 진단 페이지가 실시간 업데이트가 안 된다' — 전에는 처음 한 번만 읽었다.
import type { components } from '../../api/types.gen';
import type { PageContext } from '../../app/types';
import { DataError, type Envelope } from '../../data/source';
import { h, replace } from '../../ui/dom';
import { DASH, num, type Num } from '../../ui/fmt';
import { kv, type KvItem } from '../../ui/kv';
import { ladder, type Rung } from '../../ui/ladder';
import { errorText, type Panel, panel } from '../../ui/panel';
import { profile, type ProfileBar } from '../../ui/profile';
import { seg } from '../../ui/seg';
import { BRIEF_DEF, BRIEF_TITLE, briefView, type GexBriefing } from './gex_brief';

type S = components['schemas'];
export type GexLevels = S['GexLevels'];
export type GexProfile = S['GexProfile'];
export type GexMetricSeries = S['GexMetricSeries'];
export type GexFlow = S['GexFlow'];
export type GexScope = GexLevels['scope'];
type Level = GexLevels['levels'][number];

export const GEX_SOURCE = 'KBJ GEX 엔진(KIS 옵션 체인·웹소켓)';
/** 다시 읽는 주기 — 엔진 사이클(chain.ready·10초 따라잡기)보다 길고 API 캐시 장중 TTL(30초)과 같다 */
export const GEX_REFRESH_MS = 30_000;
export const GEX_HINT = 'gex.engine 장중 사이클';
/** 섀도 지표 자리 문구(§10 Q3) */
export const SHADOW_TEXT = '섀도 검증 중';
export const NIGHT_INVESTOR_TEXT = '수집 안 함(야간)';
export const TITLES = { profile: 'GEX 프로필', levels: 'GEX 레벨', ext: '확장 지표' } as const;
export const SCOPES: readonly { value: GexScope; label: string }[] = [
  { value: 'all', label: '전체' },
  { value: 'nearest', label: '최근월' },
  { value: '0dte', label: '0DTE' },
];
/** 확장 지표 칸(이름 → API 지표 이름). HIRO-lite 는 flow 에서 */
export const EXT_METRICS: readonly { name: string; label: string; hint: string }[] = [
  { name: 'vex', label: 'VEX', hint: 'Vanna 익스포저(원/1%·σ) — §G4.1' },
  { name: 'cex', label: 'CEX', hint: 'Charm 익스포저(원/일) — §G4.2' },
  { name: 'skew_25d', label: '25Δ 스큐', hint: '풋 25Δ IV − 콜 25Δ IV(연율) — §G5.3' },
];

/** 레벨 이름 → 화면 이름(.lvl 행 순서) */
export const LEVEL_ROWS: readonly { name: string; label: string }[] = [
  { name: 'call_wall', label: '콜월' },
  { name: 'put_wall', label: '풋월' },
  { name: 'flip', label: 'Flip' },
  { name: 'flip_distance', label: 'Flip 거리' },
  { name: 'expected_move_calendar', label: '기대변동폭 ±1σ' },
  { name: 'abs_gamma', label: '절대감마' },
];

const KST_FMT = new Intl.DateTimeFormat('ko-KR', {
  timeZone: 'Asia/Seoul',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

/** ISO 시각 → 'MM. DD. HH:MM'(KST) */
export function kstTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : KST_FMT.format(d);
}

function finite(v: Num): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/** 레벨 하나의 화면 값 — 섀도면 '섀도 검증 중', 값 없으면 '—'. Flip 거리는 %, 기대변동폭은 ±pt */
export function levelText(lv: Level | undefined): string {
  if (!lv) return DASH;
  if (lv.flag !== 'visible') return SHADOW_TEXT;
  if (!finite(lv.value)) return DASH;
  if (lv.name === 'flip_distance') return `${num(lv.value, 2, true)}%`;
  if (lv.name.startsWith('expected_move')) return `±${num(lv.value, 2)}pt`;
  return num(lv.value, 2);
}

/** 패널 머리 상태 — '지연'·'마지막 세션 — <시각>'·null */
export function headState(d: Pick<GexLevels, 'stale' | 'stale_reason' | 'session_open' | 'cycle_at'>): string | null {
  if (!d.session_open) return `마지막 세션 — ${kstTime(d.cycle_at)}`;
  if (d.stale) return '지연';
  return null;
}

/** 사다리 단(현재가·Flip·콜월·풋월·±1σ 위아래) */
export function rungsOf(d: GexLevels): Rung[] {
  const by = new Map(d.levels.map((l) => [l.name, l]));
  const val = (name: string): Num => {
    const l = by.get(name);
    return l && l.flag === 'visible' ? l.value : null;
  };
  const move = val('expected_move_calendar');
  const price = d.price;
  return [
    { name: '현재가', value: price, tone: 'accent' },
    { name: 'Flip', value: val('flip'), tone: 'warn' },
    { name: '콜월', value: val('call_wall'), tone: 'up' },
    { name: '풋월', value: val('put_wall'), tone: 'down' },
    { name: '+1σ', value: finite(price) && finite(move) ? price + move : null, tone: 'dim' },
    { name: '−1σ', value: finite(price) && finite(move) ? price - move : null, tone: 'dim' },
  ];
}

/** 행사가마다 GEX 합(시리즈를 더한다 — 원/1%) → 막대 */
export function barsOf(p: GexProfile): ProfileBar[] {
  const sum = new Map<number, number>();
  for (const b of p.bars) sum.set(b.strike, (sum.get(b.strike) ?? 0) + b.gex);
  return [...sum.entries()].sort((a, b) => a[0] - b[0]).map(([strike, value]) => ({ strike, value }));
}

/** 억원(원/1% → 억) */
export function eok(v: Num): string {
  return finite(v) ? `${num(v / 1e8, 0, true)}억` : DASH;
}

function notFound(err: unknown): boolean {
  return err instanceof DataError && err.status === 404;
}

function fail(p: Panel, err: unknown, hint: string): void {
  if (notFound(err)) p.showMessage(`${errorText(err)} — ${hint}`);
  else p.showError(err);
}

function setHead(p: Panel, state: string | null): void {
  p.el.querySelector('.gex-state')?.remove();
  if (state) p.head.append(h('span', { class: 'gex-state warn' }, state));
}

function lvlRow(label: string, value: string): HTMLElement {
  return h('div', { class: 'lvl' }, h('span', null, label), h('span', { class: 'dim' }), h('span', null, value));
}

function drawLevels(p: Panel, env: Envelope<GexLevels>, scopeEl: HTMLElement): void {
  const d = env.data;
  p.setSource(env);
  setHead(p, headState(d));
  const by = new Map(d.levels.map((l) => [l.name, l]));
  const rows = h(
    'div',
    { class: 'levels' },
    lvlRow('현재가', finite(d.price) ? num(d.price, 2) : DASH),
    LEVEL_ROWS.map((r) => lvlRow(r.label, levelText(by.get(r.name)))),
  );
  replace(p.body, scopeEl, ladder(`GEX 레벨 사다리(${d.scope})`, rungsOf(d)), rows);
}

function drawProfile(p: Panel, env: Envelope<GexProfile>): void {
  const d = env.data;
  p.setSource(env);
  const bars = barsOf(d);
  replace(
    p.body,
    bars.length
      ? profile({ label: `행사가별 GEX(${d.scope})`, bars, price: d.price, flip: d.flip })
      : h('p', { class: 'msg' }, '행사가 막대 없음'),
    h(
      'p',
      { class: 'dim' },
      `현재가 ${finite(d.price) ? num(d.price, 2) : DASH} · Flip ${finite(d.flip) ? num(d.flip, 2) : DASH} · 순GEX 합 ${eok(
        bars.reduce((a, b) => a + (finite(b.value) ? b.value : 0), 0),
      )}(원/1%)`,
    ),
  );
}

/** 확장 지표 칸 하나 — 섀도면 '섀도 검증 중', 없으면 '—' */
export function extItem(label: string, hint: string, s: GexMetricSeries | null): KvItem {
  if (!s) return { label, hint, value: null };
  if (s.flag !== 'visible') return { label, hint, na: SHADOW_TEXT };
  const last = s.points.at(-1);
  return { label, hint, value: last && finite(last.value) ? num(last.value, 4) : null, quality: last?.quality ?? null };
}

async function drawExt(ctx: PageContext, p: Panel): Promise<void> {
  const items: KvItem[] = [];
  const notes: string[] = [];
  let shown: Envelope<unknown> | null = null;
  for (const m of EXT_METRICS) {
    try {
      const env = await ctx.source.get<GexMetricSeries>('gex/metrics', { name: m.name });
      shown ??= env;
      items.push(extItem(m.label, m.hint, env.data));
    } catch (err) {
      if (!notFound(err)) throw err;
      items.push(extItem(m.label, m.hint, null));
    }
  }
  try {
    const flow = await ctx.source.get<GexFlow>('gex/flow');
    shown = flow;
    const last = flow.data.hiro.at(-1);
    items.push(
      flow.data.hiro_flag !== 'visible'
        ? { label: 'HIRO-lite', hint: '딜러 헤지 수요(원) — §G6.1', na: SHADOW_TEXT }
        : { label: 'HIRO-lite', hint: '딜러 헤지 수요(원) — §G6.1', value: last ? eok(last.value) : null, quality: last?.quality ?? null },
    );
    const night = flow.data.investors.length === 0 && !flow.data.night_investor_collected;
    items.push({
      label: '투자자별',
      hint: 'investor_flow — flows.market_minute(§G6.2)',
      ...(night ? { na: NIGHT_INVESTOR_TEXT } : { value: `${String(flow.data.investors.length)}행` }),
    });
  } catch (err) {
    if (!notFound(err)) throw err;
    items.push({ label: 'HIRO-lite', value: null }, { label: '투자자별', value: null });
    notes.push('플로 아직 없음 — gex.engine·flows.market_minute');
  }
  if (shown) p.setSource({ ...shown, notes: [...shown.notes, ...notes] });
  replace(p.body, kv(items));
}

// 패널 설명 — GEX 를 처음 보는 사람이 읽는 법(2026-10-20 사용자 '설명이 부족하다')
export const PROFILE_DEF =
  '코스피200 옵션 미결제약정으로 추정한 행사가별 감마 노출(GEX) — 지수가 1% 움직일 때 딜러가 헤지로 사고팔아야 할 금액(원/1%). ' +
  '+막대 구간에서는 딜러가 오르면 팔고 내리면 사서 움직임을 누르고(변동성 억제), −막대 구간에서는 따라 사고팔아 움직임을 키운다. ' +
  '세로선 = 현재가(선물)·Flip. 가정: 딜러가 콜을 들고 풋을 판 상태(naive) — 한국은 개인 콜 매수가 많아 이 가정이 틀릴 수 있고, 틀리면 콜 쪽 부호가 뒤집힌다.';
export const LEVELS_DEF =
  'Flip = 순GEX 부호가 바뀌는 지수(현재가가 위면 변동성 억제, 아래면 확대 구간). 콜월 = 콜 GEX 가 가장 큰 행사가(저항으로 자주 읽는다), ' +
  '풋월 = 풋 GEX 가 가장 큰 행사가(지지). 절대감마 = 감마가 가장 몰린 행사가(만기 무렵 가격이 붙기 쉽다). ' +
  '±1σ = ATM 내재변동성으로 본 이번 세션 기대 변동폭(약 68% 범위). 야간에는 직전 주간 값.';
export const EXT_DEF =
  `VEX = 변동성이 1%p 바뀔 때 딜러 헤지 변화, CEX = 하루 지날 때 헤지 변화, 25Δ 스큐 = 풋 IV − 콜 IV(높을수록 하락 대비 수요), ` +
  `HIRO = 오늘 옵션 체결로 생긴 딜러 헤지 수요. 값 검증(1주 섀도 운영) 뒤 표시 — 그 전엔 '${SHADOW_TEXT}'.`;

/** ④ GEX 레벨(로그인) */
/** ④ GEX 패널 셋(틀만 — 공개 빌드는 이 틀만 쓰고 본문 mountGex 는 번들에서 빠진다, P8 WRAP) */
export function gexPanels(ctx: PageContext): { brief: Panel; prof: Panel; lvls: Panel; ext: Panel } {
  const common = { tier: 'login' as const, lock: 'options', src: GEX_SOURCE };
  const brief = panel(ctx.tier, { ...common, title: BRIEF_TITLE, span: 12, def: BRIEF_DEF });
  const prof = panel(ctx.tier, { ...common, title: TITLES.profile, span: 6, def: PROFILE_DEF });
  const lvls = panel(ctx.tier, { ...common, title: TITLES.levels, span: 6, def: LEVELS_DEF });
  const ext = panel(ctx.tier, { ...common, title: TITLES.ext, span: 12, def: EXT_DEF });
  // 머리 원천 줄이 반폭 패널에서 제목과 붙지 않게 작은 글씨(components.css .panel.gex)
  for (const p of [brief, prof, lvls, ext]) p.el.classList.add('gex');
  ctx.root.append(brief.el, prof.el, lvls.el, ext.el);
  return { brief, prof, lvls, ext };
}

export async function mountGex(ctx: PageContext): Promise<void> {
  const { brief, prof, lvls, ext } = gexPanels(ctx);
  if (lvls.locked) return; // 공개판 — 자물쇠만, 요청하지 않는다

  const levelCache = new Map<GexScope, Envelope<GexLevels>>();
  const profileCache = new Map<GexScope, Envelope<GexProfile>>();
  const briefCache = new Map<GexScope, Envelope<GexBriefing>>();
  let current: GexScope = 'all';
  const scopes = seg<GexScope>({
    label: 'GEX 범위',
    options: SCOPES,
    value: current,
    onChange: (v) => {
      current = v;
      void show(v);
    },
  });

  async function show(scope: GexScope): Promise<void> {
    try {
      let env = briefCache.get(scope);
      if (!env) {
        env = await ctx.source.get<GexBriefing>('gex/briefing', { scope });
        briefCache.set(scope, env);
      }
      brief.setSource(env);
      replace(brief.body, briefView(env.data));
    } catch (err) {
      fail(brief, err, GEX_HINT);
    }
    try {
      let env = levelCache.get(scope);
      if (!env) {
        env = await ctx.source.get<GexLevels>('gex/levels', { scope });
        levelCache.set(scope, env);
      }
      drawLevels(lvls, env, scopes.el);
    } catch (err) {
      fail(lvls, err, GEX_HINT);
      lvls.body.prepend(scopes.el);
    }
    try {
      let env = profileCache.get(scope);
      if (!env) {
        env = await ctx.source.get<GexProfile>('gex/profile', { scope });
        profileCache.set(scope, env);
      }
      drawProfile(prof, env);
    } catch (err) {
      fail(prof, err, GEX_HINT);
    }
  }

  async function loadExt(): Promise<void> {
    try {
      await drawExt(ctx, ext);
    } catch (err) {
      ext.showError(err);
    }
  }

  await show(current);
  await loadExt();
  // 첫 불러오기 중에 앱이 내려갔으면(격자가 문서에서 빠짐) 주기 작업을 걸지 않는다
  if (!ctx.root.isConnected) return;
  let busy = false;
  ctx.every(GEX_REFRESH_MS, () => {
    if (busy) return; // 앞 회차가 아직이면 건너뛴다(느린 응답이 겹쳐 쌓이지 않게)
    busy = true;
    levelCache.clear();
    profileCache.clear();
    briefCache.clear();
    void show(current)
      .then(loadExt)
      .finally(() => {
        busy = false;
      });
  });
}
