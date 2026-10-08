// 셸 — KST 시계(주입 시계)·스킨(저장소 막힘 포함)·주기 실행기(탭 숨김 멈춤)·TradingView(공개 전용)
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountApp } from '../src/app/app';
import { createScheduler } from '../src/app/refresh';
import { applySkin, DEFAULT_SKIN, isSkin, loadSkin, saveSkin, SKIN_STORAGE_KEY, SKINS } from '../src/app/skin';
import type { DataSource } from '../src/data/source';
import { mountTradingView, TV_SCRIPT_BASE } from '../src/pages/tradingview';
import { registerServiceWorker } from '../src/pwa/register';
import { appRoot, fixedClock, memoryStorage } from './helpers';

const nullSource: DataSource = { tier: 'public', get: () => Promise.reject(new Error('x')) };

describe('KST 시계', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('주입한 시계로 1초마다, 앱을 내리면 멈춘다', () => {
    let t = Date.UTC(2026, 9, 7, 0, 59, 58); // KST 09:59:58
    const root = appRoot();
    const app = mountApp(root, { tier: 'public', source: nullSource, now: () => new Date(t), loaders: new Map(), ribbon: null });
    const clock = () => root.querySelector('.clock')?.textContent;
    expect(clock()).toBe('KST 09:59:58');
    t += 2000;
    vi.advanceTimersByTime(1000);
    expect(clock()).toBe('KST 10:00:00');
    app.destroy();
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('주기 실행기', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('탭이 숨으면 멈추고, 다시 보이면 한 번 바로', () => {
    let hidden = false;
    const doc = document;
    const spy = vi.spyOn(doc, 'hidden', 'get').mockImplementation(() => hidden);
    const s = createScheduler(window, doc);
    const fn = vi.fn();
    s.every(1000, fn, { immediate: true });
    expect(fn).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(2000);
    expect(fn).toHaveBeenCalledTimes(3);
    hidden = true;
    vi.advanceTimersByTime(5000);
    expect(fn).toHaveBeenCalledTimes(3);
    hidden = false;
    doc.dispatchEvent(new Event('visibilitychange'));
    expect(fn).toHaveBeenCalledTimes(4);
    s.stop();
    vi.advanceTimersByTime(5000);
    expect(fn).toHaveBeenCalledTimes(4);
    spy.mockRestore();
  });

  it('stop 뒤에 건 주기 작업은 받지 않는다(늦게 끝난 mount 의 타이머가 새지 않게)', () => {
    const s = createScheduler(window, document);
    s.stop();
    const fn = vi.fn();
    const cleanup = s.every(1000, fn, { immediate: true });
    vi.advanceTimersByTime(5000);
    expect(fn).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
    cleanup();
  });

  it('한 작업의 오류가 다른 작업을 멈추지 않는다', () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const s = createScheduler(window, document);
    const ok = vi.fn();
    s.every(1000, () => {
      throw new Error('고장');
    });
    s.every(1000, ok);
    vi.advanceTimersByTime(1000);
    expect(ok).toHaveBeenCalledOnce();
    expect(err).toHaveBeenCalled();
    s.stop();
  });
});

describe('스킨', () => {
  it('다섯 벌, 기본 amber', () => {
    expect(SKINS.map((s) => s.id)).toEqual(['amber', 'phosphor', 'uv', 'dark', 'white']);
    expect(DEFAULT_SKIN).toBe('amber');
    expect(isSkin('uv')).toBe(true);
    expect(isSkin('md6')).toBe(false);
  });

  it('저장·읽기, 모르는 값은 기본', () => {
    const st = memoryStorage();
    saveSkin('white', st);
    expect(st.getItem(SKIN_STORAGE_KEY)).toBe('white');
    expect(loadSkin(st)).toBe('white');
    expect(loadSkin(memoryStorage({ [SKIN_STORAGE_KEY]: '<x>' }))).toBe('amber');
    expect(loadSkin(null)).toBe('amber');
  });

  it('저장소가 막혀도(던져도) 화면은 뜬다', () => {
    const broken = {
      getItem: () => {
        throw new Error('SecurityError');
      },
      setItem: () => {
        throw new Error('QuotaExceeded');
      },
    };
    expect(loadSkin(broken)).toBe('amber');
    expect(() => saveSkin('uv', broken)).not.toThrow();
  });

  it('스킨 버튼 → <html data-skin> + 저장', () => {
    const st = memoryStorage({ [SKIN_STORAGE_KEY]: 'dark' });
    const root = appRoot();
    const app = mountApp(root, { tier: 'public', source: nullSource, now: fixedClock(), loaders: new Map(), ribbon: null, storage: st });
    expect(document.documentElement.dataset.skin).toBe('dark');
    const group = root.querySelector('[role="group"][aria-label="스킨"]');
    expect(group?.querySelector('[aria-pressed="true"]')?.textContent).toBe('Dark');
    group?.querySelector<HTMLButtonElement>('[data-value="phosphor"]')?.click();
    expect(document.documentElement.dataset.skin).toBe('phosphor');
    expect(st.getItem(SKIN_STORAGE_KEY)).toBe('phosphor');
    applySkin('amber');
    expect(document.documentElement.dataset.skin).toBe('amber');
    app.destroy();
  });

  it('저장소를 주지 않으면 브라우저 localStorage 에 스킨을 저장한다(운영 부팅 경로)', () => {
    localStorage.setItem(SKIN_STORAGE_KEY, 'uv');
    const root = appRoot();
    const app = mountApp(root, { tier: 'public', source: nullSource, now: fixedClock(), loaders: new Map(), ribbon: null });
    expect(document.documentElement.dataset.skin).toBe('uv');
    root.querySelector<HTMLButtonElement>('[role="group"][aria-label="스킨"] [data-value="white"]')?.click();
    expect(localStorage.getItem(SKIN_STORAGE_KEY)).toBe('white');
    localStorage.removeItem(SKIN_STORAGE_KEY);
    applySkin('amber');
    app.destroy();
  });

  it('로그인 빌드만 로그아웃 버튼', () => {
    const onLogout = vi.fn();
    const root = appRoot();
    const app = mountApp(root, {
      tier: 'login',
      source: { ...nullSource, tier: 'login' },
      now: fixedClock(),
      loaders: new Map(),
      ribbon: null,
      onLogout,
      user: 'me',
    });
    const btn = [...root.querySelectorAll('button')].find((b) => b.textContent === '로그아웃');
    btn?.click();
    expect(onLogout).toHaveBeenCalledOnce();
    app.destroy();
    const pub = mountApp(root, { tier: 'public', source: nullSource, now: fixedClock(), loaders: new Map(), ribbon: null, onLogout });
    expect([...root.querySelectorAll('button')].some((b) => b.textContent === '로그아웃')).toBe(false);
    pub.destroy();
  });
});

describe('상단 띠 자리', () => {
  it('띠 모듈이 없으면 준비 중 칩, 있으면 그 모듈이 그린다', async () => {
    const root = appRoot();
    const a = mountApp(root, { tier: 'public', source: nullSource, now: fixedClock(), loaders: new Map(), ribbon: null });
    expect(root.querySelector('.ribbon .chip .v')?.textContent).toBe('준비 중(P3)');
    a.destroy();
    const b = mountApp(root, {
      tier: 'public',
      source: nullSource,
      now: fixedClock(),
      loaders: new Map(),
      ribbon: () =>
        Promise.resolve({
          mount: (el: HTMLElement) => {
            el.append('띠');
          },
        }),
    });
    await b.settled();
    expect(root.querySelector('.ribbon')?.textContent).toBe('띠');
    b.destroy();
  });

  it('띠 모듈 오류는 띠에만', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const root = appRoot();
    const app = mountApp(root, {
      tier: 'public',
      source: nullSource,
      now: fixedClock(),
      loaders: new Map(),
      ribbon: () => Promise.reject(new Error('고장')),
    });
    await app.settled();
    expect(root.querySelector('.ribbon .chip .v')?.textContent).toBe('불러오지 못함');
    expect(root.querySelectorAll('.tabs button')).toHaveLength(13);
    app.destroy();
  });
});

describe('TradingView(공개 전용)', () => {
  it('임베드 스크립트 + 설정 JSON + 출처 표시, 정리하면 사라진다', () => {
    document.documentElement.dataset.skin = 'white';
    const box = document.createElement('div');
    document.body.append(box);
    const off = mountTradingView(box, 'mini-symbol-overview', { symbol: 'KRX:KOSPI' });
    const script = box.querySelector('script');
    expect(script?.getAttribute('src')).toBe(`${TV_SCRIPT_BASE}mini-symbol-overview.js`);
    expect(JSON.parse(script?.textContent ?? '{}')).toMatchObject({ symbol: 'KRX:KOSPI', colorTheme: 'light', locale: 'kr' });
    expect(box.querySelector('.tradingview-widget-copyright a')?.getAttribute('rel')).toContain('noopener');
    off();
    expect(box.childNodes).toHaveLength(0);
  });
});

describe('서비스 워커 등록', () => {
  it('개발 모드·미지원이면 등록하지 않는다', async () => {
    const register = vi.fn(() => Promise.resolve({} as ServiceWorkerRegistration));
    const nav = { serviceWorker: { register } } as unknown as Navigator;
    expect(await registerServiceWorker(nav, false)).toBeNull();
    expect(await registerServiceWorker({} as Navigator, true)).toBeNull();
    await registerServiceWorker(nav, true);
    expect(register).toHaveBeenCalledWith('./sw.js', { scope: './' });
  });

  it('등록 실패는 경고로 남기고 앱은 계속', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const nav = { serviceWorker: { register: () => Promise.reject(new Error('no')) } } as unknown as Navigator;
    expect(await registerServiceWorker(nav, true)).toBeNull();
    expect(warn).toHaveBeenCalled();
  });
});
