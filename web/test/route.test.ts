// 선택 종목 상태(app/route.ts) — 코드 형식·구독·openStock·정리 (p4_design §1.2·§6.5 route.test)
import { describe, expect, it, vi } from 'vitest';
import {
  CODE_RE,
  createRoute,
  getCode,
  isStockCode,
  onCode,
  openStock,
  route,
  setCode,
  STOCK_PAGE,
  stockHash,
} from '../src/app/route';

describe('종목 코드 형식', () => {
  it('^[0-9A-Z]{6}$ 만(P3·P4 API 의 {code} 와 같다)', () => {
    for (const good of ['005930', '000660', 'Q00001', '99A001', 'ABCDEF']) expect(isStockCode(good), good).toBe(true);
    for (const bad of ['', '00593', '0059300', 'q00001', '00593 ', '../x', '00-593', null, undefined, 5930]) {
      expect(isStockCode(bad), String(bad)).toBe(false);
    }
    expect(CODE_RE.source).toBe('^[0-9A-Z]{6}$');
    expect(STOCK_PAGE).toBe(8);
  });

  it('종목 상세 해시', () => {
    expect(stockHash('005930')).toBe('#/p/8/005930');
    expect(stockHash('bad')).toBeNull();
  });
});

describe('createRoute', () => {
  it('처음엔 종목 없음, setCode 로 바꾸고 바뀔 때만 알린다', () => {
    const r = createRoute();
    const seen: (string | null)[] = [];
    r.onCode((c) => seen.push(c));
    expect(r.getCode()).toBeNull();
    expect(r.setCode('005930')).toBe(true);
    expect(r.setCode('005930')).toBe(true); // 같은 값 — 알리지 않음
    expect(r.setCode('000660')).toBe(true);
    expect(r.setCode(null)).toBe(true);
    expect(seen).toEqual(['005930', '000660', null]);
  });

  it('잘못된 코드는 상태를 바꾸지 않고 false', () => {
    const r = createRoute();
    r.setCode('005930');
    const fn = vi.fn();
    r.onCode(fn);
    expect(r.setCode('00593')).toBe(false);
    expect(r.setCode('q00001')).toBe(false);
    expect(r.getCode()).toBe('005930');
    expect(fn).not.toHaveBeenCalled();
  });

  it('구독 끊기', () => {
    const r = createRoute();
    const fn = vi.fn();
    const off = r.onCode(fn);
    r.setCode('005930');
    off();
    r.setCode('000660');
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('openStock: 종목을 넣고 페이지 8 로 이동(이동 담당이 있을 때)', () => {
    const r = createRoute();
    const go = vi.fn();
    const unbind = r.bind({ go });
    expect(r.openStock('005930')).toBe(true);
    expect(go).toHaveBeenCalledWith(8);
    expect(r.getCode()).toBe('005930');
    expect(r.openStock('bad')).toBe(false);
    expect(go).toHaveBeenCalledTimes(1);
    unbind();
    expect(r.openStock('000660')).toBe(true); // 이동 담당이 없으면 종목만
    expect(go).toHaveBeenCalledTimes(1);
    expect(r.getCode()).toBe('000660');
  });

  it('다른 이동 담당이 붙은 뒤 앞 담당의 unbind 는 뒤 담당을 떼지 않는다', () => {
    const r = createRoute();
    const a = vi.fn();
    const b = vi.fn();
    const offA = r.bind({ go: a });
    r.bind({ go: b });
    offA();
    r.openStock('005930');
    expect(a).not.toHaveBeenCalled();
    expect(b).toHaveBeenCalledWith(8);
  });

  it('한 구독자의 오류가 다른 구독자를 멈추지 않는다(콘솔에는 남긴다)', () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const r = createRoute();
    const after = vi.fn();
    r.onCode(() => {
      throw new Error('구독 고장');
    });
    r.onCode(after);
    expect(r.setCode('005930')).toBe(true);
    expect(after).toHaveBeenCalledWith('005930');
    expect(err).toHaveBeenCalled();
  });

  it('reset: 종목·구독·이동 담당을 비운다', () => {
    const r = createRoute();
    const fn = vi.fn();
    const go = vi.fn();
    r.onCode(fn);
    r.bind({ go });
    r.setCode('005930');
    r.reset();
    expect(r.getCode()).toBeNull();
    r.openStock('000660');
    expect(fn).toHaveBeenCalledTimes(1);
    expect(go).not.toHaveBeenCalled();
  });
});

describe('앱의 선택 종목 상태(모듈 함수)', () => {
  it('getCode·onCode·setCode·openStock 은 같은 상태 하나를 쓴다', () => {
    route.reset();
    const seen: (string | null)[] = [];
    const off = onCode((c) => seen.push(c));
    expect(setCode('005930')).toBe(true);
    expect(getCode()).toBe('005930');
    const go = vi.fn();
    route.bind({ go });
    expect(openStock('000660')).toBe(true);
    expect(go).toHaveBeenCalledWith(8);
    expect(route.getCode()).toBe('000660');
    off();
    expect(seen).toEqual(['005930', '000660']);
    route.reset();
  });
});
