// P7 완료 시험(docs/p7_design.md §0.3·§1.7-2·§6 — 웨이브 3 WRAP): 페이지 3(진단·GEX) 전체를 진입 모듈(p3_diag)로
// 공개·로그인 두 등급에서 그린다. 데이터는 합성(p7_world 로 만든 웹 픽스처 — test/fixtures/api/{diag,gex}/**, 실데이터 아님).
// - 두 등급: console.error 0, 오류 줄(.msg.err) 0, DOM 글자에 NaN·undefined·Infinity 0.
// - 공개: 로그인 API 0회(공개 파일 ./data/diag/* 만), 로그인 패널(판정 띠·공포탐욕·시장 진단·GEX)은 자물쇠, 화면에
//   로그인 값(공포탐욕 점수·판정 근거·GEX 레벨 값) 0. 공개 파일에도 로그인 판정 키(fear_greed·verdict·gex*) 0.
// - 로그인: 다섯 구역이 다 그려지고 GEX 레벨 값이 보인다.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DataSource } from '../../src/data/source';
import p3, { P3_SECTIONS } from '../../src/pages/p3_diag';
import { TITLES } from '../../src/pages/p3/diag';
import { DIAG_NOW, diagFixture, diagLoginHarness, diagPublicHarness } from '../fixtures/api/diag/harness';
import { gexFixture, gexHarness } from '../fixtures/api/gex/harness';
import { flush, pageCtx, panelByTitle, text } from '../pages/support';

const PUBLIC_FILES = import.meta.glob<string>('../fixtures/api/diag/public/**/*.json', {
  eager: true,
  query: '?raw',
  import: 'default',
});

interface Summary {
  verdict: { top: string[] } | null;
  fear_greed: { score: number } | null;
}
interface Levels {
  levels: { name: string; value: number | null }[];
}

function badWords(el: Element): string[] {
  return el.textContent.match(/\b(NaN|undefined|-?Infinity)\b/g) ?? [];
}

function loginSource(): { source: DataSource; paths: () => string[] } {
  const dz = diagLoginHarness();
  const gz = gexHarness();
  const source: DataSource = {
    ...dz.source,
    get: <T,>(key: string, params?: Record<string, string>) =>
      key.startsWith('gex/') ? gz.source.get<T>(key, params) : dz.source.get<T>(key, params),
  };
  return { source, paths: () => [...dz.paths(), ...gz.paths()] };
}

async function mountPage(tier: 'login' | 'public') {
  const hz = tier === 'login' ? loginSource() : diagPublicHarness();
  const { ctx, root } = pageCtx(3, tier, hz.source, { now: () => DIAG_NOW });
  await p3.mount(ctx);
  await flush();
  return { root, paths: hz.paths };
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

describe('P7 완료 — 페이지 3 두 등급', () => {
  for (const tier of ['login', 'public'] as const) {
    it(`${tier}: 오류 0·이상 글자 0`, async () => {
      const { root } = await mountPage(tier);
      expect(errors).toEqual([]);
      expect(root.querySelectorAll('.msg.err')).toHaveLength(0);
      expect(badWords(root)).toEqual([]);
      expect(root.querySelectorAll('section.panel').length).toBeGreaterThanOrEqual(P3_SECTIONS.length);
    });
  }

  it('공개: 로그인 API 0회, 로그인 패널은 자물쇠, 로그인 값이 화면에 없다', async () => {
    const { root, paths } = await mountPage('public');
    expect(paths().length).toBeGreaterThan(0);
    expect(paths().every((p) => p.startsWith('./data/diag/'))).toBe(true);
    for (const t of [TITLES.verdict, TITLES.fear, TITLES.market]) {
      expect(panelByTitle(root, t).classList.contains('locked')).toBe(true);
    }
    // 공개판이 그리는 것은 공공지표·이벤트뿐(§6 — 페이지 3 mixed)
    expect(panelByTitle(root, TITLES.public).classList.contains('locked')).toBe(false);
    expect(panelByTitle(root, TITLES.events).classList.contains('locked')).toBe(false);
    const body = text(root);
    const s = diagFixture<{ data: Summary }>('summary').data;
    expect(body).not.toContain((s.fear_greed?.score ?? 0).toFixed(1));
    for (const t of s.verdict?.top ?? []) expect(body).not.toContain(t);
    const lv = gexFixture<{ data: Levels }>('levels_all').data.levels;
    const flip = lv.find((l) => l.name === 'flip')?.value;
    expect(flip).not.toBeNull();
    expect(body).not.toContain((flip ?? 0).toFixed(2));
    expect(body).not.toMatch(/공포탐욕 \d|위험 선호 점수|콜월 \d|Flip \d/);
  });

  it('공개 파일(diag/*)에 로그인 판정 키가 없다', () => {
    const files = Object.entries(PUBLIC_FILES);
    expect(files.length).toBeGreaterThan(0);
    for (const [name, raw] of files) {
      expect(raw, name).not.toMatch(/"(fear_greed|verdict|gex[a-z_]*)"\s*:/);
    }
  });

  it('로그인: 다섯 구역이 그려지고 GEX 레벨 값이 보인다', async () => {
    const { root, paths } = await mountPage('login');
    expect(paths().some((p) => p.startsWith('gex/levels'))).toBe(true);
    expect(paths().some((p) => p.startsWith('diag/summary'))).toBe(true);
    expect(root.querySelectorAll('section.panel.locked')).toHaveLength(0);
    const lv = gexFixture<{ data: Levels }>('levels_all').data.levels;
    const wall = lv.find((l) => l.name === 'call_wall')?.value ?? 0;
    expect(text(root)).toContain(wall.toFixed(2));
  });
});
