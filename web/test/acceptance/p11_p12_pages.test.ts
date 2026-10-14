// P8 완료 시험(docs/p8_design.md §0.3·§1.6-2·§6 — 웨이브 3 WRAP): 페이지 11(규칙 알림)·12(백테스트·저널)를 진입 모듈로
// 공개·로그인 두 등급에서 그린다. 데이터는 합성(p8_world 로 만든 웹 픽스처 — test/fixtures/api/{rules,backtest,journal}/**,
// 실데이터 아님).
// - 두 등급: console.error 0, 오류 줄(.msg.err) 0, DOM 글자에 NaN·undefined·Infinity 0, 패널 수 고정.
// - 공개: 요청 0(개인 기능 — 자물쇠만), 화면에 규칙 이름·종목 코드·금액 0.
// - 로그인: 페이지 격자(ctx.root)의 span 칸 — 표·탭 묶음만 span-12, 차트 패널은 반폭(span-6) 이하(전체 폭 차트는 viewBox 가
//   커져 글자가 커진다 — 사용자 요구). 데이터 없음(404)은 '.msg' 안내.
// - 로그인: 쓰기 채널(CSRF 헤더 — createApiSource writeHeaders)이 있으면 쓰기 단추가 켜진다(요청은 하지 않는다).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createApiSource } from '../../src/data/api';
import type { DataSource } from '../../src/data/source';
import p11, { P11_PANELS, P11_SECTIONS } from '../../src/pages/p11_rules';
import p12, { P12_PANELS, P12_SECTIONS } from '../../src/pages/p12_journal';
import { btHarness } from '../fixtures/api/backtest/harness';
import { jrHarness } from '../fixtures/api/journal/harness';
import { RULES_NOW, rulesFixture, rulesHarness } from '../fixtures/api/rules/harness';
import { fakeFetch, json } from '../helpers';
import { buttonByText, flush, pageCtx, panelByTitle, text } from '../pages/support';

function badWords(el: Element): string[] {
  return el.textContent.match(/\b(NaN|undefined|-?Infinity)\b/g) ?? [];
}

/** 세 묶음 픽스처를 경로 첫 마디로 나눠 붙인 로그인 소스(읽기만) */
function loginSource(): { source: DataSource; paths: () => string[] } {
  const rz = rulesHarness();
  const bz = btHarness();
  const jz = jrHarness();
  const pick = (key: string): DataSource =>
    key === 'rules/stats' || key.startsWith('backtest') ? bz.source : key.startsWith('rules') ? rz.source : jz.source;
  const source: DataSource = {
    tier: 'login',
    get: <T,>(key: string, params?: Record<string, string>) => pick(key).get<T>(key, params),
  };
  return { source, paths: () => [...rz.paths(), ...bz.paths(), ...jz.paths()] };
}

async function mountBoth(tier: 'login' | 'public', source: DataSource) {
  const out: { n: number; root: HTMLElement }[] = [];
  for (const [n, mod] of [
    [11, p11],
    [12, p12],
  ] as const) {
    const { ctx, root } = pageCtx(n, tier, source, { now: () => RULES_NOW });
    await mod.mount(ctx);
    await flush();
    out.push({ n, root });
  }
  return out;
}

let errors: unknown[][] = [];
beforeEach(() => {
  errors = [];
  vi.spyOn(console, 'error').mockImplementation((...a: unknown[]) => {
    errors.push(a);
  });
});
afterEach(() => {
  vi.restoreAllMocks();
  document.body.replaceChildren();
});

const COUNTS: Record<number, number> = { 11: P11_SECTIONS.length, 12: P12_SECTIONS.length };
// 차트(막대·선)를 그리는 패널 — 반폭 이하여야 한다
const CHART_PANELS = [P11_PANELS.perf_dist.title, P12_PANELS.curve.title, P12_PANELS.corr.title];

describe('P8 완료 — 페이지 11·12 두 등급', () => {
  for (const tier of ['login', 'public'] as const) {
    it(`${tier}: 오류 0·이상 글자 0·패널 수 고정`, async () => {
      const hz = tier === 'login' ? loginSource() : { source: createApiSource({ fetch: fakeFetch(() => json({}, 500)).fetch }), paths: () => [] };
      const pages = await mountBoth(tier, hz.source);
      expect(errors).toEqual([]);
      for (const { n, root } of pages) {
        expect(root.querySelectorAll('section.panel'), `페이지 ${n}`).toHaveLength(COUNTS[n] ?? -1);
        expect(root.querySelectorAll('.msg.err'), `페이지 ${n}`).toHaveLength(0);
        expect(badWords(root)).toEqual([]);
      }
    });
  }

  it('공개: 요청 0, 전부 자물쇠, 개인 값(규칙 이름·종목 코드) 0', async () => {
    const { fetch, calls } = fakeFetch(() => json({}, 500));
    const pages = await mountBoth('public', createApiSource({ fetch }));
    expect(calls).toHaveLength(0);
    const ruleName = rulesFixture<{ data: { rules: { name: string }[] } }>('list').data.rules[0]?.name ?? '';
    expect(ruleName).not.toBe('');
    for (const { root } of pages) {
      expect(root.querySelectorAll('section.panel.locked')).toHaveLength(root.querySelectorAll('section.panel').length);
      expect(text(root)).not.toContain(ruleName);
      expect(text(root)).not.toMatch(/\b99\d{4}\b/);
    }
  });

  it('로그인: 규칙 목록·성과·백테스트 실행 목록·일지·포트폴리오가 그려진다(합성 값)', async () => {
    const hz = loginSource();
    const [pg11, pg12] = await mountBoth('login', hz.source);
    const ruleName = rulesFixture<{ data: { rules: { name: string }[] } }>('list').data.rules[0]?.name ?? '';
    expect(text(panelByTitle(pg11!.root, P11_PANELS.rules.title))).toContain(ruleName);
    expect(panelByTitle(pg11!.root, P11_PANELS.perf.title).querySelector('table')).not.toBeNull();
    expect(panelByTitle(pg12!.root, P12_PANELS.journal.title).querySelector('tbody tr')).not.toBeNull();
    expect(panelByTitle(pg12!.root, P12_PANELS.portfolio.title).querySelector('table')).not.toBeNull();
    // 쓰는 경로 없음 — 읽기만(GET)
    expect(hz.paths().length).toBeGreaterThan(0);
  });

  it('로그인: 레이아웃 — 페이지 격자의 span 칸, 차트 패널은 반폭 이하, 표·탭 묶음만 전체 폭', async () => {
    const pages = await mountBoth('login', loginSource().source);
    for (const { root } of pages) {
      expect(root.classList.contains('grid')).toBe(true);
      for (const p of root.querySelectorAll('section.panel')) {
        expect([...p.classList].some((c) => /^span-(3|4|6|8|12)$/.test(c))).toBe(true);
      }
    }
    for (const title of CHART_PANELS) {
      const owner = pages.find((pg) => pg.root.textContent.includes(title));
      const p = owner ? panelByTitle(owner.root, title) : null;
      expect(p, title).not.toBeNull();
      expect(p?.classList.contains('span-12'), title).toBe(false);
    }
    const wide = pages.flatMap(({ root }) =>
      [...root.querySelectorAll('section.panel.span-12')].map((p) => p.querySelector('h3 span')?.textContent ?? ''),
    );
    expect(wide.sort()).toEqual([P11_PANELS.rules.title, P12_PANELS.vs_live.title].sort());
  });

  it('로그인: 쓰기 채널(CSRF)이 있으면 쓰기 단추가 켜진다 — 그리기만으로는 쓰기 요청 0', async () => {
    const rz = rulesHarness();
    const { fetch, calls } = fakeFetch((_url, init) => {
      if (init?.method && init.method !== 'GET') return json({}, 500);
      return json({}, 404);
    });
    const writer = createApiSource({ fetch, writeHeaders: () => ({ 'X-KBJ-CSRF': 't' }) });
    const source = { ...writer, tier: 'login' as const, get: rz.source.get.bind(rz.source) };
    const { ctx, root } = pageCtx(11, 'login', source, { now: () => RULES_NOW });
    await p11.mount(ctx);
    await flush();
    const ed = panelByTitle(root, P11_PANELS.editor.title);
    expect(buttonByText(ed, '저장').disabled).toBe(false);
    expect(calls.filter((c) => c.init?.method && c.init.method !== 'GET')).toHaveLength(0);
  });
});
