// 셸 + P4 페이지(5·8) 통합 — 빌드가 찾은 실제 모듈로 두 등급에서 붙고, 해시 #/p/8/<코드> 로 바로 열리며, 페이지 5 의
// 행을 누르면 해시가 #/p/8/<코드> 로 바뀐다(docs/p4_design.md §6.1·§6.5). 공개판은 로그인 API 를 한 번도 부르지 않는다.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mountApp } from '../../src/app/app';
import { P4_PAGES, pageLoaders } from '../../src/app/pages';
import { route } from '../../src/app/route';
import { settled as p8Settled } from '../../src/pages/p8_stock';
import { appRoot, fixedClock } from '../helpers';
import { panelByTitle, text } from '../pages/support';
import { forbiddenHits, P4_NOW, p4ApiHarness, p4PublicHarness, seriousA11y } from './support_p4';

afterEach(() => {
  route.reset();
});

describe('셸 + P4 페이지', () => {
  it('빌드가 페이지 5·8 진입 모듈을 찾는다(하위 폴더 p5/·p8/ 는 glob 에 걸리지 않는다)', () => {
    const found = [...pageLoaders().keys()];
    for (const n of P4_PAGES) expect(found).toContain(n);
    expect(P4_PAGES).toEqual([5, 8]);
  });

  it('로그인: 해시 #/p/8/<코드> 로 바로 그 종목, 페이지 5 행 → 해시 #/p/8/<코드>, 오류·금지 낱말·axe 심각 위반 0', async () => {
    window.history.replaceState(null, '', '#/p/8/994020');
    const err = vi.spyOn(console, 'error');
    const hz = p4ApiHarness();
    const root = appRoot();
    const app = mountApp(root, { tier: 'login', source: hz.source, now: fixedClock(P4_NOW), storage: null, user: 'me', onLogout: () => undefined });
    await app.settled();
    const p8 = root.querySelector<HTMLElement>('#page-8');
    if (!p8) throw new Error('페이지 8 없음');
    expect(route.getCode()).toBe('994020');
    expect(text(panelByTitle(p8, '종목'))).toContain('모의바이오');
    // 해시에 종목이 있으면 관심종목(칩 줄은 그린다)의 첫 종목으로 바꾸지 않는다
    expect(hz.paths()).toContain('watchlist');

    app.pager.go(5, { smooth: false });
    await app.settled();
    const p5 = root.querySelector<HTMLElement>('#page-5');
    if (!p5) throw new Error('페이지 5 없음');
    const row = [...panelByTitle(p5, '공시 피드').querySelectorAll<HTMLTableRowElement>('tbody tr')].find((r) => text(r).includes('모의모빌리티'));
    row?.click();
    await app.settled();
    // 페이지 8 은 이미 붙어 있다 — 종목을 바꾼 뒤 그리기는 그 페이지가 기다린다(페이지 격자 = 첫 .grid)
    const grid = p8.querySelector<HTMLElement>('.grid');
    if (grid) await p8Settled(grid);
    expect(route.getCode()).toBe('994130');
    expect(window.location.hash).toBe('#/p/8/994130');
    expect(text(panelByTitle(p8, '종목'))).toContain('모의모빌리티');

    for (const n of [5, 8]) {
      const sec = root.querySelector(`#page-${n}`);
      expect(sec?.querySelectorAll('section.panel').length).toBeGreaterThan(3);
      expect(sec?.querySelectorAll('.msg.err')).toHaveLength(0);
      if (sec) expect(forbiddenHits(sec)).toEqual([]);
    }
    expect(err).not.toHaveBeenCalled();
    expect(await seriousA11y(document.body)).toEqual([]);
    app.destroy();
  }, 30_000);

  it('공개: 페이지 5·8 이 공개 파일만 읽고(로그인 API 0회) 로그인 패널은 자물쇠, axe 심각 위반 0', async () => {
    window.history.replaceState(null, '', '#/p/8/Q00001');
    const hz = p4PublicHarness();
    const root = appRoot();
    const tv = vi.fn(() => () => undefined);
    const app = mountApp(root, { tier: 'public', source: hz.source, now: fixedClock(P4_NOW), storage: null, tradingview: tv });
    await app.settled();
    app.pager.go(5, { smooth: false });
    await app.settled();
    expect(hz.paths().every((u) => u.startsWith('./data/'))).toBe(true);
    expect(hz.paths().some((u) => u.includes('api'))).toBe(false);
    expect(hz.paths()).toContain('./data/fin/Q00001.json');
    expect(hz.paths()).toContain('./data/filings_feed.json');
    expect(root.querySelectorAll('#page-8 .panel.locked').length).toBe(1); // 개요 탭의 수급(컨센·밸류 탭은 아직 안 열림)
    expect(tv).toHaveBeenCalled();
    expect(root.querySelectorAll('#page-5 .msg.err, #page-8 .msg.err')).toHaveLength(0);
    expect(await seriousA11y(document.body)).toEqual([]);
    app.destroy();
  }, 30_000);
});
