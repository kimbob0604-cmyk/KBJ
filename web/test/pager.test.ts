// 탭·해시·←/→·미니맵, 13페이지 등록 (§6.8 pager.test)
import { describe, expect, it, vi } from 'vitest';
import { mountApp } from '../src/app/app';
import { pageLoaders, PAGES, P3_PAGES } from '../src/app/pages';
import { isMostlyVisible, parseHash } from '../src/app/pager';
import type { DataSource } from '../src/data/source';
import { appRoot, fakeLoaders, fixedClock } from './helpers';

const nullSource: DataSource = { tier: 'public', get: () => Promise.reject(new Error('부르지 않는다')) };

function mount(loaders = new Map(), hash = '') {
  if (hash) window.history.replaceState(null, '', hash);
  const root = appRoot();
  const app = mountApp(root, { tier: 'public', source: nullSource, now: fixedClock(), loaders, ribbon: null });
  return { root, app };
}

const current = (root: HTMLElement): string | null =>
  root.querySelector('.tabs button[aria-current="page"]')?.textContent ?? null;

describe('페이지 등록부', () => {
  it('13페이지, 번호 1~13, P3 위젯 페이지는 1·2·4·10', () => {
    expect(PAGES.map((p) => p.n)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]);
    expect(P3_PAGES).toEqual([1, 2, 4, 10]);
    // DATA_TIERS §2: 5·7 은 공개 표시, 1 은 공개·로그인 섞임, 나머지는 로그인
    expect(PAGES.filter((p) => p.tier === 'public').map((p) => p.n)).toEqual([5, 7]);
    expect(PAGES.find((p) => p.n === 1)?.tier).toBe('mixed');
  });

  it('위젯 모듈 찾기: 파일 이름의 번호로', () => {
    const noop = () => Promise.resolve({ mount: () => undefined });
    const m = pageLoaders({ '../pages/p1_market.ts': noop, '../pages/p10_etf.ts': noop, '../pages/placeholder.ts': noop });
    expect([...m.keys()].sort((a, b) => a - b)).toEqual([1, 10]);
  });

  it('해시 해석', () => {
    expect(parseHash('#/p/4', PAGES)).toBe(4);
    expect(parseHash('#/p/14', PAGES)).toBeNull();
    expect(parseHash('#/x/4', PAGES)).toBeNull();
    expect(parseHash('', PAGES)).toBeNull();
  });
});

describe('보이는 페이지 판단', () => {
  const rect = (x: number, width: number, height: number) => ({ x, y: 0, width, height, top: 0, left: x, right: x + width, bottom: height }) as DOMRectReadOnly;
  const root = rect(76, 1288, 900);

  it('아직 그리지 않은(높이 0) 옆 페이지는 붙어 있어도 현재가 아니다', () => {
    // 크로미움은 넓이 0 인 대상이 루트 가장자리에 붙으면 isIntersecting·ratio 1 로 알린다
    expect(isMostlyVisible({ isIntersecting: true, intersectionRect: rect(1364, 0, 0), rootBounds: root }, 1288)).toBe(false);
  });

  it('루트 폭의 60% 이상 보이면 현재', () => {
    expect(isMostlyVisible({ isIntersecting: true, intersectionRect: rect(76, 1288, 500), rootBounds: root }, 1288)).toBe(true);
    expect(isMostlyVisible({ isIntersecting: true, intersectionRect: rect(76, 700, 500), rootBounds: root }, 1288)).toBe(false);
    expect(isMostlyVisible({ isIntersecting: false, intersectionRect: rect(76, 1288, 500), rootBounds: root }, 1288)).toBe(false);
    expect(isMostlyVisible({ isIntersecting: true, intersectionRect: rect(0, 800, 10), rootBounds: null }, 1000)).toBe(true);
    expect(isMostlyVisible({ isIntersecting: true, intersectionRect: rect(0, 800, 10), rootBounds: null }, 0)).toBe(false);
  });
});

describe('탭·미니맵', () => {
  it('탭 13개·미니맵 13칸, 위젯 없는 페이지는 점선(todo)', () => {
    const { root, app } = mount(fakeLoaders({ 1: () => undefined, 4: () => undefined }));
    expect(root.querySelectorAll('.tabs button')).toHaveLength(13);
    const marks = root.querySelectorAll('.minimap i');
    expect(marks).toHaveLength(13);
    expect(root.querySelector('.minimap')?.getAttribute('aria-hidden')).toBe('true');
    const todo = [...marks].filter((i) => i.classList.contains('todo')).map((i) => (i as HTMLElement).dataset.page);
    expect(todo).toEqual(['2', '3', '5', '6', '7', '8', '9', '10', '11', '12', '13']);
    expect(current(root)).toBe('1 시장');
    expect(root.querySelector('.minimap i.on')?.getAttribute('data-page')).toBe('1');
    app.destroy();
  });

  it('탭을 누르면 그 페이지 + 해시 #/p/n', () => {
    const { root, app } = mount();
    root.querySelector<HTMLButtonElement>('.tabs button[data-page="10"]')?.click();
    expect(current(root)).toBe('10 ETF 수급');
    expect(window.location.hash).toBe('#/p/10');
    expect(root.querySelector('.minimap i.on')?.getAttribute('data-page')).toBe('10');
    app.destroy();
  });

  it('처음 해시가 #/p/4 면 4쪽으로 연다', () => {
    const { root, app } = mount(new Map(), '#/p/4');
    expect(current(root)).toBe('4 수급·스크리닝');
    expect(app.pager.current()).toBe(4);
    app.destroy();
  });

  it('hashchange 를 따라간다', () => {
    const { root, app } = mount();
    window.history.replaceState(null, '', '#/p/7');
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    expect(current(root)).toBe('7 수출입');
    app.destroy();
  });
});

describe('키보드 ←/→', () => {
  it('→ 다음, ← 이전, 끝에서 멈춤', () => {
    const { root, app } = mount();
    const key = (k: string, target: EventTarget = document.body) =>
      target.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true }));
    key('ArrowRight');
    expect(app.pager.current()).toBe(2);
    key('ArrowRight');
    key('ArrowLeft');
    expect(app.pager.current()).toBe(2);
    key('ArrowLeft');
    key('ArrowLeft');
    expect(app.pager.current()).toBe(1);
    app.pager.go(13, { smooth: false });
    key('ArrowRight');
    expect(app.pager.current()).toBe(13);
    expect(current(root)).toBe('13 운영');
    app.destroy();
  });

  it('입력 칸·수정 키에서는 가로채지 않는다', () => {
    const { root, app } = mount();
    const input = document.createElement('input');
    root.append(input);
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    expect(app.pager.current()).toBe(1);
    document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', altKey: true, bubbles: true }));
    expect(app.pager.current()).toBe(1);
    app.destroy();
  });
});

describe('페이지 모듈은 처음 보일 때 한 번', () => {
  it('보이기 전에는 붙이지 않고, 다시 와도 두 번 붙이지 않는다', async () => {
    const mounted: number[] = [];
    const loaders = fakeLoaders({
      1: (ctx) => mounted.push(ctx.page.n),
      2: (ctx) => mounted.push(ctx.page.n),
    });
    const { app } = mount(loaders);
    await app.settled();
    expect(mounted).toEqual([1]);
    app.pager.go(2, { smooth: false });
    app.pager.go(1, { smooth: false });
    app.pager.go(2, { smooth: false });
    await app.settled();
    expect(mounted).toEqual([1, 2]);
    app.destroy();
  });

  it('한 페이지의 오류는 그 페이지에만(다른 페이지·셸은 계속)', async () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const loaders = fakeLoaders({
      1: () => {
        throw new Error('위젯 고장');
      },
      2: (ctx) => {
        ctx.root.append(document.createTextNode('ok2'));
      },
    });
    const { root, app } = mount(loaders);
    await app.settled();
    expect(root.querySelector('#page-1 .msg')?.textContent).toContain('그리지 못했습니다');
    app.pager.go(2, { smooth: false });
    await app.settled();
    expect(root.querySelector('#page-2')?.textContent).toContain('ok2');
    expect(err).toHaveBeenCalled();
    app.destroy();
  });

  it('정리 함수는 앱을 내릴 때 부른다', async () => {
    const cleaned: string[] = [];
    const { app } = mount(fakeLoaders({ 1: () => () => cleaned.push('p1') }));
    await app.settled();
    app.destroy();
    expect(cleaned).toEqual(['p1']);
  });
});
