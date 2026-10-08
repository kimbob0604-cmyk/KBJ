// 시험 도우미 — 가짜 fetch·가짜 시계·가짜 저장소. 벽시계·네트워크에 기대지 않는다(CLAUDE.md §4).
import { vi } from 'vitest';
import type { PageLoader } from '../src/app/pages';
import type { PageModule } from '../src/app/types';
import type { Envelope } from '../src/data/source';

export const FIXED_NOW = new Date('2026-10-07T01:23:45Z'); // KST 10:23:45

export function fixedClock(t: Date = FIXED_NOW): () => Date {
  return () => new Date(t.getTime());
}

export interface FakeCall {
  url: string;
  init: RequestInit | undefined;
}

export type Responder = (url: string, init: RequestInit | undefined) => Response | Promise<Response>;

/** 경로별 응답을 주는 가짜 fetch. 부른 기록을 calls 에 남긴다. */
export function fakeFetch(responder: Responder): { fetch: (u: string, i?: RequestInit) => Promise<Response>; calls: FakeCall[] } {
  const calls: FakeCall[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return responder(url, init);
  });
  return { fetch: fn, calls };
}

export function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } });
}

export function envelope<T>(data: T, over: Partial<Envelope<T>> = {}): Envelope<T> {
  return {
    source: 'KRX',
    as_of: '2026-10-07T15:30:00+09:00',
    quality: 'ok',
    notes: [],
    generated_at: '2026-10-07T16:00:00+09:00',
    data,
    ...over,
  };
}

export function memoryStorage(init: Record<string, string> = {}): Storage {
  const m = new Map(Object.entries(init));
  return {
    get length() {
      return m.size;
    },
    clear: () => m.clear(),
    getItem: (k) => m.get(k) ?? null,
    key: (i) => [...m.keys()][i] ?? null,
    removeItem: (k) => m.delete(k),
    setItem: (k, v) => m.set(k, v),
  };
}

/** 페이지 모듈 가짜 — mount 될 때 기록하고 fn 을 부른다. */
export function fakeLoaders(
  pages: Record<number, (ctx: Parameters<PageModule['mount']>[0]) => unknown>,
): Map<number, PageLoader> {
  const out = new Map<number, PageLoader>();
  for (const [n, fn] of Object.entries(pages)) {
    out.set(Number(n), () => Promise.resolve({ mount: fn }));
  }
  return out;
}

export function appRoot(): HTMLElement {
  const root = document.createElement('div');
  root.id = 'app';
  document.body.append(root);
  return root;
}
