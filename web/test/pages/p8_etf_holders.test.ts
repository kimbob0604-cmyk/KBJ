// 페이지 8 '담은 ETF' 모듈(p8/etf_holders.ts — 고리는 WRAP). 로그인: etf/holders/{코드} 를 불러 비중순 표,
// 액티브 꼬리표, 없으면 빈 줄, 404 는 작업 이름. 공개판: 자물쇠, 요청 없음.
import { describe, expect, it } from 'vitest';
import type { Tier } from '../../src/data/source';
import { HINT, mount, TITLE } from '../../src/pages/p8/etf_holders';
import { memoGet, type TabCtx } from '../../src/pages/p8/common';
import { fixedClock, json } from '../helpers';
import { apiHarness, flush, notFound, type Override, panelByTitle, publicHarness, text } from './support';

const P5 = import.meta.glob<unknown>('../fixtures/api/etf/*.json', { eager: true, import: 'default' });
const fx = (name: string): object => structuredClone(P5[`../fixtures/api/etf/${name}.json`]) as object;

function tabCtx(tier: Tier, code: string, source: TabCtx['source']): TabCtx {
  return {
    tier,
    code,
    source,
    now: fixedClock(),
    get: memoGet(source),
    pub: () => Promise.resolve(null),
    corp: () => Promise.resolve(null),
    open: () => undefined,
  };
}

async function run(tier: Tier, code: string, over: Record<string, Override> = {}) {
  const hz =
    tier === 'login'
      ? apiHarness({ etf_holders_997190: fx('holders_robot'), etf_holders_997240: fx('holders_none'), ...over })
      : publicHarness();
  const el = document.createElement('div');
  document.body.append(el);
  await mount(tabCtx(tier, code, hz.source), el);
  await flush();
  return { hz, el, p: panelByTitle(el, TITLE) };
}

describe('페이지 8 담은 ETF', () => {
  it('담은 ETF 를 비중순으로, 액티브 꼬리표', async () => {
    const { hz, p } = await run('login', '997190');
    expect(hz.paths()).toEqual(['etf/holders/997190']);
    const rows = p.querySelectorAll('tbody tr');
    expect(rows).toHaveLength(2);
    expect(text(rows[0])).toContain('합성 액티브B');
    expect(text(rows[0])).toContain('20.00%');
    expect(rows[0]?.querySelector('.tag')?.textContent).toBe('액티브');
    expect(text(p.querySelector('caption'))).toBe('합성로봇 (997190) · 담은 ETF 2개 · 액티브 2');
    expect(text(p.querySelector('.src'))).toContain('ETF_ISSUERS');
  });

  it('담은 ETF 가 없으면 빈 줄', async () => {
    const { p } = await run('login', '997240');
    expect(text(p)).toContain('이 종목을 담은 ETF 없음');
  });

  it('아직 없음(404)은 작업 이름', async () => {
    const { p } = await run('login', '997190', {
      etf_holders_997190: () => notFound(`아직 없음 — ${HINT}(운용사 구성종목)`),
    });
    expect(text(p)).toContain('아직 없음');
    expect(text(p)).toContain('etf.collect');
  });

  it('응답이 깨져도 다른 칸을 죽이지 않는다(오류 줄)', async () => {
    const { p } = await run('login', '997190', { etf_holders_997190: () => json({ code: 'oops' }, 500) });
    expect(p.querySelector('tbody')).toBeNull();
    expect(text(p).length).toBeGreaterThan(0);
  });

  it('공개판은 자물쇠, 요청 없음', async () => {
    const { hz, p } = await run('public', '997190');
    expect(p.classList.contains('locked')).toBe(true);
    expect(hz.calls).toHaveLength(0);
  });
});
