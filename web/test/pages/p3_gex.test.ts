// 페이지 3 GEX 레벨 패널(묶음 ENG — docs/p7_design.md §6.1 ④·§6.2): 프로필(행사가 막대·현재가·Flip 선)·레벨(사다리·.lvl
// 행)·범위 seg·확장 지표(섀도면 '섀도 검증 중'). 로그인만 — 공개판은 자물쇠, 요청 0. 지연·장 밖 머리 표시.
// 404 는 '.msg', 그 밖 실패만 '.msg.err'. 패널은 반폭(span-6) 둘 + 전체 폭 값 칸 하나.
import { describe, expect, it } from 'vitest';
import {
  barsOf,
  extItem,
  headState,
  levelText,
  mountGex,
  rungsOf,
  SHADOW_TEXT,
  TITLES,
  type GexLevels,
  type GexMetricSeries,
  type GexProfile,
} from '../../src/pages/p3/gex';
import { BRIEF_TITLE } from '../../src/pages/p3/gex_brief';
import { P3_SECTIONS } from '../../src/pages/p3_diag';
import { json } from '../helpers';
import { GEX_NOW, gexFixture, gexHarness } from '../fixtures/api/gex/harness';
import { buttonByText, flush, pageCtx, panelByTitle, text } from './support';

interface Env<T> {
  source: string;
  as_of: string;
  quality: string;
  notes: string[];
  data: T;
}

async function mount(tier: 'login' | 'public', over: Record<string, (() => Response) | undefined> = {}) {
  const hz = gexHarness(over);
  const { ctx, root } = pageCtx(3, tier, hz.source, { now: () => GEX_NOW });
  await mountGex(ctx);
  await flush();
  return { hz, root };
}

describe('페이지 3 GEX — 로그인', () => {
  it('브리핑(전체 폭) 아래 프로필·레벨 반폭 둘 나란히, 확장 지표는 전체 폭 — 순서는 진입 모듈이 고정', async () => {
    const { root } = await mount('login');
    const spans = [...root.querySelectorAll('section.panel')].map((p) => [text(p.querySelector('h3 > span')), p.className]);
    expect(spans.map((s) => s[0])).toEqual([BRIEF_TITLE, TITLES.profile, TITLES.levels, TITLES.ext]);
    expect(spans[0]?.[1]).toContain('span-12');
    expect(spans[1]?.[1]).toContain('span-6');
    expect(spans[2]?.[1]).toContain('span-6');
    expect(spans[3]?.[1]).toContain('span-12');
    expect(P3_SECTIONS).toContain('gex');
  });

  it('프로필 막대(행사가 17개)·현재가·Flip 선, 레벨 사다리와 .lvl 행', async () => {
    const { hz, root } = await mount('login');
    const prof = panelByTitle(root, TITLES.profile);
    expect(prof.querySelectorAll('svg rect').length).toBe(17);
    expect(text(prof)).toContain('Flip 350.50');
    const lv = panelByTitle(root, TITLES.levels);
    expect(lv.querySelector('svg')).not.toBeNull();
    const rows = [...lv.querySelectorAll('.lvl')].map((r) => text(r));
    expect(rows.some((r) => r.startsWith('콜월') && r.endsWith('360.00'))).toBe(true);
    expect(rows.some((r) => r.startsWith('풋월') && r.endsWith('340.00'))).toBe(true);
    expect(rows.some((r) => r.startsWith('기대변동폭') && r.includes('±4.20pt'))).toBe(true);
    expect(text(root)).not.toMatch(/NaN|undefined/);
    expect(hz.paths().filter((p) => p.startsWith('gex/levels'))).toEqual(['gex/levels?scope=all']);
  });

  it('장 중 마지막 사이클이 오래되면 머리에 지연(봉투 stale)', async () => {
    const { root } = await mount('login');
    const lv = panelByTitle(root, TITLES.levels);
    expect(text(lv.querySelector('.gex-state'))).toBe('지연');
    expect(lv.classList.contains('stale')).toBe(true);
  });

  it('장 밖이면 머리에 마지막 세션 — <시각>', async () => {
    const closed = gexFixture<Env<GexLevels>>('levels_closed');
    const { root } = await mount('login', { 'gex/levels': () => json(closed) });
    expect(text(panelByTitle(root, TITLES.levels).querySelector('.gex-state'))).toContain('마지막 세션 —');
  });

  it('범위를 바꾸면 그 범위만 한 번 부른다(다시 고르면 캐시)', async () => {
    const { hz, root } = await mount('login');
    const lv = panelByTitle(root, TITLES.levels);
    buttonByText(lv, '0DTE').click();
    await flush();
    buttonByText(lv, '전체').click();
    await flush();
    buttonByText(lv, '0DTE').click();
    await flush();
    expect(hz.paths().filter((p) => p.startsWith('gex/levels'))).toEqual(['gex/levels?scope=all', 'gex/levels?scope=0dte']);
    expect(hz.paths().filter((p) => p.startsWith('gex/profile'))).toHaveLength(2);
  });

  it('확장 지표는 섀도면 섀도 검증 중 — 값을 그리지 않는다', async () => {
    const { root } = await mount('login');
    const ext = panelByTitle(root, TITLES.ext);
    expect(text(ext)).toContain(SHADOW_TEXT);
    expect(text(ext)).toContain('VEX');
    expect(text(ext)).toContain('HIRO-lite');
    expect(text(ext)).not.toContain('21억'); // vex 2.1e9 를 그리지 않았다
  });

  it('아직 없음(404)은 패널 안 .msg — 다른 패널은 그대로', async () => {
    const nd = () => json({ code: 'no_data', message: '아직 없음 — gex.engine' }, 404);
    const { root } = await mount('login', { 'gex/levels': nd, 'gex/profile': nd });
    const lv = panelByTitle(root, TITLES.levels);
    expect(text(lv.querySelector('.msg:not(.err)'))).toContain('아직 없음');
    expect(lv.querySelector('.msg.err')).toBeNull();
    expect(text(panelByTitle(root, TITLES.ext))).toContain('VEX');
  });

  it('404 말고 실패(500)는 오류 줄로 남는다 — 삼키지 않는다', async () => {
    const { root } = await mount('login', { 'gex/profile': () => json({ code: 'internal', message: '오류' }, 500) });
    expect(panelByTitle(root, TITLES.profile).querySelector('.msg.err')).not.toBeNull();
    expect(panelByTitle(root, TITLES.levels).querySelector('.msg.err')).toBeNull();
  });
});

describe('페이지 3 GEX — 공개', () => {
  it('공개판은 자물쇠만 — GEX API 0회', async () => {
    const { hz, root } = await mount('public');
    expect(hz.paths()).toHaveLength(0);
    for (const t of Object.values(TITLES)) expect(panelByTitle(root, t).classList.contains('locked')).toBe(true);
  });
});

describe('GEX 순수 도우미', () => {
  it('레벨 글자 — 섀도·없음·거리 %·기대변동폭 ±pt', () => {
    const base = { quality: 'ok' as const, reasons: [], detail: {} };
    expect(levelText({ ...base, name: 'vex', value: null, flag: 'shadow' })).toBe(SHADOW_TEXT);
    expect(levelText({ ...base, name: 'flip', value: null, flag: 'visible' })).toBe('—');
    expect(levelText({ ...base, name: 'flip_distance', value: 0.27, flag: 'visible' })).toBe('+0.27%');
    expect(levelText({ ...base, name: 'expected_move_calendar', value: 4.2, flag: 'visible' })).toBe('±4.20pt');
    expect(levelText(undefined)).toBe('—');
  });

  it('사다리 ±1σ 는 현재가 ± 기대변동폭, 섀도 레벨은 단이 비어 있다', () => {
    const d = gexFixture<Env<GexLevels>>('levels_all').data;
    const r = rungsOf(d);
    const price = d.price ?? 0;
    expect(r.find((x) => x.name === '+1σ')?.value).toBeCloseTo(price + 4.2);
    expect(r.find((x) => x.name === '−1σ')?.value).toBeCloseTo(price - 4.2);
    const shadow: GexLevels = { ...d, levels: d.levels.map((l) => (l.name === 'flip' ? { ...l, flag: 'shadow', value: null } : l)) };
    expect(rungsOf(shadow).find((x) => x.name === 'Flip')?.value).toBeNull();
  });

  it('막대는 행사가마다 시리즈 합(오름차순)', () => {
    const p = gexFixture<Env<GexProfile>>('profile_all').data;
    const twice: GexProfile = { ...p, bars: [...p.bars, ...p.bars] };
    const one = barsOf(p);
    const two = barsOf(twice);
    expect(two.map((b) => b.strike)).toEqual(one.map((b) => b.strike));
    expect(two[0]?.value).toBeCloseTo((one[0]?.value ?? 0) * 2);
    expect(one.map((b) => b.strike)).toEqual([...one.map((b) => b.strike)].sort((a, b) => a - b));
  });

  it('머리 상태 — 장 밖이 지연보다 먼저', () => {
    const at = '2026-12-10T04:30:00Z';
    expect(headState({ stale: true, stale_reason: 'x', session_open: false, cycle_at: at })).toContain('마지막 세션');
    expect(headState({ stale: true, stale_reason: 'x', session_open: true, cycle_at: at })).toBe('지연');
    expect(headState({ stale: false, stale_reason: null, session_open: true, cycle_at: at })).toBeNull();
  });

  it('확장 지표 칸 — visible 은 마지막 값, 섀도는 na', () => {
    const vex = gexFixture<Env<GexMetricSeries>>('metrics_vex').data;
    expect(extItem('VEX', 'h', vex).na).toBe(SHADOW_TEXT);
    const net = gexFixture<Env<GexMetricSeries>>('metrics_net_gex').data;
    expect(extItem('순GEX', 'h', net).value).not.toBeNull();
    expect(extItem('X', 'h', null).value).toBeNull();
  });
});
