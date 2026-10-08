// 서비스 워커 — 앱 셸만 캐시(routes.ts 의 허용 목록). 빌드 때 루트의 sw.js 로 나간다(vite.config.ts).
// 이 파일은 export 가 없어야 한다(클래식 워커 스크립트).
import { classify } from './routes';

const CACHE = 'kbj-shell-v1';

interface ExtendableEventLike extends Event {
  waitUntil(p: Promise<unknown>): void;
}
interface FetchEventLike extends ExtendableEventLike {
  readonly request: Request;
  respondWith(r: Promise<Response>): void;
}
interface SwScope {
  readonly registration: { readonly scope: string };
  skipWaiting(): Promise<void>;
  readonly clients: { claim(): Promise<void> };
  addEventListener(type: 'install' | 'activate', fn: (e: ExtendableEventLike) => void): void;
  addEventListener(type: 'fetch', fn: (e: FetchEventLike) => void): void;
}

const sw = self as unknown as SwScope;

sw.addEventListener('install', (e) => {
  e.waitUntil(sw.skipWaiting());
});

sw.addEventListener('activate', (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith('kbj-') && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => sw.clients.claim()),
  );
});

async function networkFirst(req: Request, fallback?: string): Promise<Response> {
  const cache = await caches.open(CACHE);
  try {
    const res = await fetch(req);
    if (res.ok) await cache.put(fallback ?? req, res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(fallback ?? req);
    if (hit) return hit;
    throw err;
  }
}

async function cacheFirst(req: Request): Promise<Response> {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) await cache.put(req, res.clone());
  return res;
}

sw.addEventListener('fetch', (e) => {
  const scope = new URL(sw.registration.scope);
  const route = classify(new URL(e.request.url), scope, e.request.method, e.request.mode);
  if (route === 'nav') e.respondWith(networkFirst(e.request, new URL('index.html', scope).href));
  else if (route === 'asset') e.respondWith(cacheFirst(e.request));
  else if (route === 'data') e.respondWith(networkFirst(e.request));
  // bypass: respondWith 를 부르지 않는다 — 브라우저가 그대로 처리
});
