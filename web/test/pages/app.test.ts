// 셸 + 실제 W2 모듈(빌드 때 찾는 src/pages/p*_*.ts·src/app/ribbon.ts) 통합 — 두 등급에서 페이지 1·2·4·10 과 띠가
// 오류 없이 붙고, 공개판은 로그인 API 를 한 번도 부르지 않으며, axe-core 심각 위반 0(§6.8).
import axe from 'axe-core';
import { describe, expect, it, vi } from 'vitest';
import { mountApp } from '../../src/app/app';
import { P3_PAGES, pageLoaders } from '../../src/app/pages';
import { appRoot, fixedClock } from '../helpers';
import { apiHarness, publicHarness } from './support';

async function serious(root: Element): Promise<string[]> {
  const res = await axe.run(root, { resultTypes: ['violations'], rules: { 'color-contrast': { enabled: false } } });
  return res.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
}

describe('셸 + W2 모듈', () => {
  it('빌드가 페이지 1·2·4·10 모듈을 모두 찾는다', () => {
    expect([...pageLoaders().keys()].sort((a, b) => a - b)).toEqual([...P3_PAGES].sort((a, b) => a - b));
  });

  it('로그인: 띠·네 페이지가 오류 없이 붙고 axe 심각 위반 0', async () => {
    const err = vi.spyOn(console, 'error');
    const hz = apiHarness();
    const root = appRoot();
    const app = mountApp(root, {
      tier: 'login',
      source: hz.source,
      now: fixedClock(),
      storage: null,
      onLogout: () => undefined,
      user: 'me',
    });
    for (const n of P3_PAGES) app.pager.go(n, { smooth: false });
    await app.settled();
    expect(root.querySelectorAll('.ribbon .chip')).toHaveLength(13);
    for (const n of P3_PAGES) {
      const sec = root.querySelector(`#page-${n}`);
      expect(sec?.querySelectorAll('section.panel').length).toBeGreaterThan(1);
      expect(sec?.querySelector('.is-pending .pending-box')?.textContent ?? '').not.toContain('위젯 연결 전');
    }
    expect(root.querySelectorAll('.msg.err')).toHaveLength(0);
    expect(err).not.toHaveBeenCalled();
    expect(await serious(document.body)).toEqual([]);
    app.destroy();
  });

  it('공개: 로그인 API 0회, 공개 파일만, 로그인 위젯은 자물쇠, axe 심각 위반 0', async () => {
    const hz = publicHarness();
    const root = appRoot();
    const tv = vi.fn(() => () => undefined);
    const app = mountApp(root, {
      tier: 'public',
      source: hz.source,
      now: fixedClock(),
      storage: null,
      tradingview: tv,
    });
    for (const n of P3_PAGES) app.pager.go(n, { smooth: false });
    await app.settled();
    expect(hz.paths().every((u) => u.startsWith('./data/'))).toBe(true);
    expect(hz.paths().some((u) => u.includes('api'))).toBe(false);
    expect(root.querySelectorAll('#page-2 .panel.locked, #page-4 .panel.locked, #page-10 .panel.locked').length).toBe(
      7 + 4 + 4,
    );
    expect(tv).toHaveBeenCalled();
    expect(root.querySelectorAll('.ribbon .chip.locked').length).toBeGreaterThan(0);
    expect(await serious(document.body)).toEqual([]);
    app.destroy();
  });
});
