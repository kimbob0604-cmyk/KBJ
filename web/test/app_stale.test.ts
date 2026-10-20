// 다시 빌드된 뒤 옛 화면 파일을 못 받으면 한 번만 새로 고친다(2026-10-20 — 페이지 5~13 '그리지 못했다').
import { describe, expect, it } from 'vitest';
import { reloadOnce, staleBuild } from '../src/app/app';

function memStore(): Pick<Storage, 'getItem' | 'setItem'> {
  const m = new Map<string, string>();
  return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => void m.set(k, v) };
}

describe('옛 빌드 파일 404 → 한 번 새로 고침', () => {
  it('브라우저별 동적 import 실패 문구를 알아본다', () => {
    expect(staleBuild(new TypeError('Failed to fetch dynamically imported module: http://x/assets/p8.js'))).toBe(true);
    expect(staleBuild(new TypeError('Importing a module script failed.'))).toBe(true);
    expect(staleBuild(new Error('panel 오류'))).toBe(false);
  });

  it('1분 안에 두 번은 새로 고치지 않는다(무한 새로 고침 방지)', () => {
    const store = memStore();
    let n = 0;
    expect(reloadOnce(store, 1_000_000, () => (n += 1))).toBe(true);
    expect(reloadOnce(store, 1_030_000, () => (n += 1))).toBe(false);
    expect(reloadOnce(store, 1_070_000, () => (n += 1))).toBe(true);
    expect(n).toBe(2);
  });
});
