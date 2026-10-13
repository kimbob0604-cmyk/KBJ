// 등급 — 공개: /api 를 한 번도 부르지 않고 로그인 위젯은 자물쇠 / 로그인: 401 → 로그인 화면 (§6.8 tier.test)
import { describe, expect, it } from 'vitest';
import { mountApp } from '../src/app/app';
import { PAGES } from '../src/app/pages';
import { createRoute } from '../src/app/route';
import type { PageContext } from '../src/app/types';
import { bootLogin } from '../src/boot/login';
import { bootPublic } from '../src/boot/public';
import { createApiSource } from '../src/data/api';
import { AuthRequiredError, hrefOf, LockedError, reportXlsxKey } from '../src/data/source';
import { createStaticSource } from '../src/data/static';
import { panel } from '../src/ui/panel';
import calendar from './fixtures/public/calendar.json';
import { appRoot, envelope, fakeFetch, fakeLoaders, fixedClock, json, memoryStorage } from './helpers';

/** 가짜 페이지: 로그인 위젯 하나 + 공개 위젯 하나. 잠기지 않았을 때만 요청한다(위젯 규칙). */
function twoPanelPage(log: string[]) {
  return async (ctx: PageContext) => {
    const loginP = panel(ctx.tier, { title: '시장 거래대금', tier: 'login', lock: 'krx_daily', span: 6 });
    const pubP = panel(ctx.tier, { title: '휴장 캘린더', tier: 'public', span: 6 });
    ctx.root.append(loginP.el, pubP.el);
    if (!loginP.locked) {
      const env = await ctx.source.get<{ v: number }>('market/summary');
      loginP.setSource(env);
      log.push(`login:${env.data.v}`);
    }
    const key = ctx.tier === 'public' ? 'calendar' : 'market/ribbon';
    const env = await ctx.source.get(key);
    pubP.setSource(env);
    log.push(`public:${key}`);
  };
}

describe('공개 빌드', () => {
  it('API 경로를 한 번도 부르지 않고, 로그인 위젯은 자물쇠', async () => {
    const root = appRoot();
    const { fetch, calls } = fakeFetch((url) => (url === './data/calendar.json' ? json(calendar) : json({}, 404)));
    const log: string[] = [];
    const app = bootPublic(root, {
      fetch,
      now: fixedClock(),
      loaders: fakeLoaders({ 1: twoPanelPage(log) }),
      ribbon: null,
      storage: memoryStorage(),
    });
    await app.settled();

    expect(calls.map((c) => c.url)).toEqual(['./data/calendar.json']);
    expect(calls.some((c) => c.url.includes('/api'))).toBe(false);
    expect(log).toEqual(['public:calendar']);
    const locked = root.querySelectorAll('.panel.locked');
    expect(locked.length).toBeGreaterThanOrEqual(1);
    expect(locked[0]?.querySelector('.lockmsg')?.textContent).toContain('LOGIN');
    expect(root.querySelector('.badge')?.textContent).toBe('공개판');
    app.destroy();
  });

  it('위젯이 로그인 키를 물어도 요청 없이 LockedError', async () => {
    const { fetch, calls } = fakeFetch(() => json({}, 500));
    const src = createStaticSource({ fetch });
    await expect(src.get('market/summary')).rejects.toBeInstanceOf(LockedError);
    await expect(src.get('flows/stock/005930')).rejects.toBeInstanceOf(LockedError);
    await expect(src.get('../secret')).rejects.toBeInstanceOf(LockedError);
    expect(calls).toHaveLength(0);
  });

  it('모든 페이지를 차례로 열어도 /api 요청 0, 로그인 등급 빈 자리는 자물쇠', async () => {
    const root = appRoot();
    const { fetch, calls } = fakeFetch(() => json({}, 404));
    const app = bootPublic(root, { fetch, now: fixedClock(), loaders: new Map(), ribbon: null });
    for (const p of PAGES) app.pager.go(p.n, { smooth: false });
    await app.settled();
    expect(calls.filter((c) => c.url.includes('/api'))).toHaveLength(0);
    for (const p of PAGES) {
      const sec = root.querySelector(`#page-${p.n}`);
      const lockedPanel = sec?.querySelector('.panel.locked');
      if (p.tier === 'login') expect(lockedPanel, `페이지 ${p.n}`).not.toBeNull();
      else expect(lockedPanel, `페이지 ${p.n}`).toBeNull();
      expect(sec?.querySelector('.panel h3 .src')?.textContent).toContain(`준비 중(${p.phase})`);
    }
    app.destroy();
  });
});

describe('로그인 빌드', () => {
  it('세션이 없으면(401) 로그인 화면', async () => {
    const root = appRoot();
    const { fetch, calls } = fakeFetch(() => json({ detail: 'x' }, 401));
    const boot = await bootLogin(root, { fetch, now: fixedClock(), ribbon: null, loaders: new Map() });
    expect(calls.map((c) => c.url)).toEqual(['/api/auth/me']);
    expect(root.querySelector('form input[type="password"]')).not.toBeNull();
    expect(boot.app()).toBeNull();
  });

  it('세션이 있으면 앱, 데이터 요청이 401 이면 앱을 내리고 로그인 화면', async () => {
    const root = appRoot();
    const { fetch, calls } = fakeFetch((url) => {
      if (url === '/api/auth/me') return json({ user: 'me', csrf_token: 't0k' });
      return json({}, 401);
    });
    const seen: unknown[] = [];
    const boot = await bootLogin(root, {
      fetch,
      now: fixedClock(),
      ribbon: null,
      loaders: fakeLoaders({
        1: async (ctx) => {
          try {
            await ctx.source.get('market/summary');
          } catch (e) {
            seen.push(e);
          }
        },
      }),
    });
    await boot.app()?.settled();
    expect(seen[0]).toBeInstanceOf(AuthRequiredError);
    expect(calls.map((c) => c.url)).toEqual(['/api/auth/me', '/api/market/summary']);
    expect(boot.app()).toBeNull();
    expect(root.querySelector('form input[type="password"]')).not.toBeNull();
    expect(root.querySelector('.login .msg')?.textContent).toContain('세션이 끝났습니다');
  });

  it('로그인 위젯을 그린다(자물쇠 없음)', async () => {
    const root = appRoot();
    const { fetch } = fakeFetch((url) => {
      if (url === '/api/auth/me') return json({ user: 'me', csrf_token: 't0k' });
      if (url === '/api/market/summary') return json(envelope({ v: 7 }));
      if (url === '/api/market/ribbon') return json(envelope({ chips: [] }));
      return json({}, 404);
    });
    const log: string[] = [];
    const boot = await bootLogin(root, { fetch, now: fixedClock(), ribbon: null, loaders: fakeLoaders({ 1: twoPanelPage(log) }) });
    await boot.app()?.settled();
    expect(log).toEqual(['login:7', 'public:market/ribbon']);
    expect(root.querySelector('#page-1 .panel.locked')).toBeNull();
    expect(root.querySelector('.badge')?.textContent).toBe('로그인 · me');
    expect(root.querySelector('#page-1 .panel h3 .src')?.textContent).toBe('KRX · 10/07 15:30');
  });

  it('API 소스는 쿠키 세션으로 같은 출처만, 쿼리는 정렬', async () => {
    const { fetch, calls } = fakeFetch(() => json(envelope([])));
    const src = createApiSource({ fetch });
    await src.get('flows/screen', { period: 5, mode: 'foreign', market: 'all', include_flagged: false, x: undefined });
    expect(calls[0]?.url).toBe('/api/flows/screen?include_flagged=false&market=all&mode=foreign&period=5');
    expect(calls[0]?.init?.credentials).toBe('same-origin');
    expect(calls[0]?.init?.method).toBe('GET');
  });
});

describe('셸은 등급을 인자로만 안다', () => {
  it('같은 mountApp 이 두 등급을 그린다', () => {
    for (const tier of ['public', 'login'] as const) {
      const root = appRoot();
      const app = mountApp(root, {
        tier,
        source: { tier, get: () => Promise.reject(new Error('부르지 않는다')) },
        now: fixedClock(),
        loaders: new Map(),
        ribbon: null,
      });
      expect(root.dataset.tier).toBe(tier);
      expect(root.querySelectorAll('.tabs button')).toHaveLength(13);
      app.destroy();
      root.remove();
    }
  });
});

/**
 * 가짜 페이지 8(섞인 등급 — D-P4-15): 공개 패널(재무)과 로그인 패널(컨센서스·밸류). 종목은 선택 종목 상태에서,
 * 키는 등급마다(공개 fin/<코드>, 로그인 stock/<코드>/…). 페이지 모듈은 '/api' 문자열을 쓰지 않는다.
 */
function stockPage(log: string[], code: () => string | null) {
  return async (ctx: PageContext) => {
    const c = code() ?? 'Q00001';
    const fin = panel(ctx.tier, { title: '재무', tier: 'public', span: 6 });
    const cons = panel(ctx.tier, { title: '컨센서스', tier: 'login', lock: 'consensus', span: 3 });
    const val = panel(ctx.tier, { title: '밸류 밴드', tier: 'login', lock: 'valuation', span: 3 });
    ctx.root.append(fin.el, cons.el, val.el);
    const env = await ctx.source.get(ctx.tier === 'public' ? `fin/${c}` : `stock/${c}/financials`);
    fin.setSource(env);
    log.push(`fin:${env.source}`);
    if (!cons.locked) log.push(`cons:${(await ctx.source.get(`stock/${c}/consensus`)).source}`);
    log.push(`xlsx:${hrefOf(ctx.source, reportXlsxKey(ctx.tier, c)) ?? 'none'}`);
  };
}

describe('P4 섞인 페이지(8) — 등급별 키·자물쇠', () => {
  const dart = envelope({ rows: [] }, { source: 'DART' });

  it('공개: fin/<코드>.json 만 읽고 컨센서스·밸류는 자물쇠, /api 요청 0', async () => {
    window.history.replaceState(null, '', '#/p/8/005930');
    const root = appRoot();
    const route = createRoute();
    const { fetch, calls } = fakeFetch((url) => (url === './data/fin/005930.json' ? json(dart) : json({}, 404)));
    const log: string[] = [];
    const app = bootPublic(root, {
      fetch,
      now: fixedClock(),
      route,
      loaders: fakeLoaders({ 8: stockPage(log, () => route.getCode()) }),
      ribbon: null,
      storage: memoryStorage(),
    });
    await app.settled();
    expect(calls.map((c) => c.url)).toEqual(['./data/fin/005930.json']);
    expect(log).toEqual(['fin:DART', 'xlsx:./data/reports/005930.xlsx']);
    const locked = [...root.querySelectorAll('#page-8 .panel.locked .lockmsg')].map((e) => e.textContent);
    expect(locked).toEqual(['LOGIN컨센서스 — 제3자 유료·재배포 금지', expect.stringContaining('시세 기반')]);
    app.destroy();
  });

  it('로그인: 같은 페이지가 API 경로로 읽고 자물쇠 없음, 엑셀은 같은 출처 API 주소', async () => {
    window.history.replaceState(null, '', '#/p/8/005930');
    const root = appRoot();
    const route = createRoute();
    const { fetch, calls } = fakeFetch((url) => {
      if (url === '/api/auth/me') return json({ user: 'me', csrf_token: 't0k' });
      if (url === '/api/stock/005930/financials') return json(dart);
      if (url === '/api/stock/005930/consensus') return json(envelope({}, { source: 'KIS' }));
      return json({}, 404);
    });
    const log: string[] = [];
    const boot = await bootLogin(root, {
      fetch,
      now: fixedClock(),
      ribbon: null,
      route,
      loaders: fakeLoaders({ 8: stockPage(log, () => route.getCode()) }),
    });
    await boot.app()?.settled();
    expect(calls.map((c) => c.url)).toEqual(['/api/auth/me', '/api/stock/005930/financials', '/api/stock/005930/consensus']);
    expect(log).toEqual(['fin:DART', 'cons:KIS', 'xlsx:/api/stock/005930/report.xlsx']);
    expect(root.querySelector('#page-8 .panel.locked')).toBeNull();
    boot.app()?.destroy();
  });
});
