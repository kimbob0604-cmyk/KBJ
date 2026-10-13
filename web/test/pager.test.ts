// 탭·해시·←/→·미니맵, 13페이지 등록 (§6.8 pager.test) + P4 종목 해시 #/p/8/<코드> (p4_design §1.2·§6.5)
import { describe, expect, it, vi } from 'vitest';
import { mountApp } from '../src/app/app';
import { pageLoaders, PAGES, P3_PAGES, P4_PAGES } from '../src/app/pages';
import { formatHash, HASH_RE, isMostlyVisible, parseHash, parseRoute } from '../src/app/pager';
import { createRoute, route as appRoute } from '../src/app/route';
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
  it('13페이지, 번호 1~13, P3 위젯 페이지는 1·2·4·10, P4 는 5·8', () => {
    expect(PAGES.map((p) => p.n)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]);
    expect(P3_PAGES).toEqual([1, 2, 4, 10]);
    expect(P4_PAGES).toEqual([5, 8]);
    // DATA_TIERS §2: 7 은 공개 표시, 1·5·8 은 공개·로그인 섞임(D-P4-15), 나머지는 로그인
    expect(PAGES.filter((p) => p.tier === 'public').map((p) => p.n)).toEqual([7]);
    expect(PAGES.filter((p) => p.tier === 'mixed').map((p) => p.n)).toEqual([1, 5, 8]);
    // 섞인 페이지는 페이지 자물쇠가 없다(패널마다 정한다)
    expect(PAGES.find((p) => p.n === 8)?.lock).toBeUndefined();
  });

  it("화면 문구에 'TAM' 을 쓰지 않는다 — 시나리오 적정가(D-P4-12)", () => {
    const p8 = PAGES.find((p) => p.n === 8);
    expect(p8?.plan).toContain('시나리오 적정가');
    expect(PAGES.some((p) => /TAM/.test(p.plan))).toBe(false);
  });

  it('위젯 모듈 찾기: 파일 이름의 번호로(진입 파일만 — 하위 폴더는 진입 파일이 import)', () => {
    const noop = () => Promise.resolve({ mount: () => undefined });
    const m = pageLoaders({
      '../pages/p1_market.ts': noop,
      '../pages/p10_etf.ts': noop,
      '../pages/p5_filings.ts': noop,
      '../pages/p8_stock.ts': noop,
      '../pages/placeholder.ts': noop,
    });
    expect([...m.keys()].sort((a, b) => a - b)).toEqual([1, 5, 8, 10]);
  });

  it('빌드 glob 이 페이지 5·8 진입 파일도 찾는다(p{1,2,4,5,8,10}_*.ts)', async () => {
    const src = (await import('../src/app/pages.ts?raw')).default;
    expect(src).toContain("'../pages/p{1,2,4,5,8,10}_*.ts'");
  });

  it('해시 해석', () => {
    expect(parseHash('#/p/4', PAGES)).toBe(4);
    expect(parseHash('#/p/14', PAGES)).toBeNull();
    expect(parseHash('#/x/4', PAGES)).toBeNull();
    expect(parseHash('', PAGES)).toBeNull();
  });

  it('종목 해시: 페이지 8 에만 올바른 코드(^[0-9A-Z]{6}$)가 붙는다', () => {
    expect(parseRoute('#/p/8/005930', PAGES)).toEqual({ n: 8, code: '005930' });
    expect(parseRoute('#/p/8/Q00001', PAGES)).toEqual({ n: 8, code: 'Q00001' });
    expect(parseHash('#/p/8/005930', PAGES)).toBe(8);
    // 잘못된 코드는 무시(페이지는 따른다)
    for (const bad of ['#/p/8/abc', '#/p/8/00593', '#/p/8/0059300', '#/p/8/q00001', '#/p/8/00 593']) {
      expect(parseRoute(bad, PAGES), bad).toEqual({ n: 8, code: null });
    }
    // 다른 페이지에 붙은 코드는 무시
    expect(parseRoute('#/p/4/005930', PAGES)).toEqual({ n: 4, code: null });
    // 경로 조각이 둘 이상·질의 문자열은 해시로 보지 않는다
    expect(parseRoute('#/p/8/005930/x', PAGES)).toBeNull();
    expect(parseRoute('#/p/8/005930?x=1', PAGES)).toBeNull();
    expect(parseRoute('#/p/14/005930', PAGES)).toBeNull();
    expect(HASH_RE.test('#/p/8/')).toBe(false);
  });

  it('해시 만들기: 페이지 8 + 올바른 코드만 붙인다', () => {
    expect(formatHash(8, '005930')).toBe('#/p/8/005930');
    expect(formatHash(8, null)).toBe('#/p/8');
    expect(formatHash(8, 'bad')).toBe('#/p/8');
    expect(formatHash(4, '005930')).toBe('#/p/4');
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

describe('선택 종목(P4) — 해시·openStock·페이지 8', () => {
  function mountWith(hash = '', loaders = new Map()) {
    if (hash) window.history.replaceState(null, '', hash);
    const route = createRoute();
    const root = appRoot();
    const app = mountApp(root, { tier: 'public', source: nullSource, now: fixedClock(), loaders, ribbon: null, route });
    return { root, app, route };
  }

  it('처음 해시 #/p/8/005930 이면 8쪽 + 그 종목(페이지 모듈이 붙을 때 이미 들어 있다)', async () => {
    const seen: (string | null)[] = [];
    const route = createRoute();
    window.history.replaceState(null, '', '#/p/8/005930');
    const root = appRoot();
    const app = mountApp(root, {
      tier: 'public',
      source: nullSource,
      now: fixedClock(),
      ribbon: null,
      route,
      loaders: fakeLoaders({ 8: () => seen.push(route.getCode()) }),
    });
    await app.settled();
    expect(app.pager.current()).toBe(8);
    expect(current(root)).toBe('8 종목 상세');
    expect(seen).toEqual(['005930']);
    expect(window.location.hash).toBe('#/p/8/005930');
    app.destroy();
  });

  it('잘못된 코드는 무시하고 8쪽만 연다', () => {
    const { app, route } = mountWith('#/p/8/bad!');
    expect(app.pager.current()).toBe(8);
    expect(route.getCode()).toBeNull();
    app.destroy();
  });

  it('openStock(code) → 8쪽 + 해시 #/p/8/<코드>, 잘못된 코드는 이동 없음', () => {
    const { root, app, route } = mountWith('#/p/5');
    expect(app.pager.current()).toBe(5);
    expect(route.openStock('00593')).toBe(false);
    expect(app.pager.current()).toBe(5);
    expect(route.openStock('000660')).toBe(true);
    expect(app.pager.current()).toBe(8);
    expect(current(root)).toBe('8 종목 상세');
    expect(window.location.hash).toBe('#/p/8/000660');
    app.destroy();
  });

  it('8쪽에서 종목만 바꾸면 해시도(replaceState), 다른 쪽으로 가면 코드를 떼고 돌아오면 다시 붙인다', () => {
    const { app, route } = mountWith('#/p/8/005930');
    const push = vi.spyOn(window.history, 'pushState');
    route.setCode('Q00001');
    expect(window.location.hash).toBe('#/p/8/Q00001');
    app.pager.go(4, { smooth: false });
    expect(window.location.hash).toBe('#/p/4');
    route.setCode('000660'); // 다른 페이지에 있을 때는 해시를 건드리지 않는다
    expect(window.location.hash).toBe('#/p/4');
    app.pager.go(8, { smooth: false });
    expect(window.location.hash).toBe('#/p/8/000660');
    expect(push).not.toHaveBeenCalled();
    app.destroy();
  });

  it('hashchange #/p/8/<코드> 를 따라간다(같은 페이지에서 종목만 바뀌어도)', () => {
    const { app, route } = mountWith('#/p/1');
    const codes: (string | null)[] = [];
    route.onCode((c) => codes.push(c));
    window.history.replaceState(null, '', '#/p/8/005930');
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    expect(app.pager.current()).toBe(8);
    window.history.replaceState(null, '', '#/p/8/000660');
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    expect(route.getCode()).toBe('000660');
    expect(codes).toEqual(['005930', '000660']);
    app.destroy();
  });

  it('앱을 내리면 선택 종목·이동 담당을 비운다(다음 앱이 물려받지 않는다)', () => {
    const { app, route } = mountWith('#/p/8/005930');
    expect(route.getCode()).toBe('005930');
    app.destroy();
    expect(route.getCode()).toBeNull();
    // 이동 담당이 없으니 종목만 바뀐다
    expect(route.openStock('000660')).toBe(true);
    expect(route.getCode()).toBe('000660');
  });

  it('route 를 주지 않으면 앱의 선택 종목 상태(페이지 모듈이 import 하는 것)를 쓴다', () => {
    window.history.replaceState(null, '', '#/p/8/005930');
    const root = appRoot();
    const app = mountApp(root, { tier: 'public', source: nullSource, now: fixedClock(), loaders: new Map(), ribbon: null });
    expect(appRoute.getCode()).toBe('005930');
    appRoute.openStock('000660');
    expect(window.location.hash).toBe('#/p/8/000660');
    app.destroy();
    expect(appRoute.getCode()).toBeNull();
  });
});
