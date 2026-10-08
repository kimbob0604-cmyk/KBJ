// 서비스 워커 요청 분류 — 앱 셸만 캐시한다(docs/p3_design.md §6.4 PWA).
//
// **허용 목록** 방식: 아래 셋에 들지 않는 요청은 가로채지 않는다(브라우저 기본 동작 그대로).
//   nav   — 페이지 이동(HTML): 네트워크 우선, 실패하면 캐시한 index.html
//   asset — assets/*(해시 이름)·icon.svg·manifest.webmanifest: 캐시 우선
//   data  — 공개 데이터 data/<이름>.json: 네트워크 우선, 실패하면 캐시(공개 등급이라 기기에 남아도 된다)
// 로그인 데이터(API 응답)는 이 목록에 없어 절대 캐시되지 않는다 — API 경로를 적지 않아도 된다(공개 번들 검사와도 맞다).
export type Route = 'nav' | 'asset' | 'data' | 'bypass';

const SHELL_FILES = new Set(['', 'index.html', 'icon.svg', 'manifest.webmanifest']);
const DATA_FILE = /^data\/[a-z0-9_]+\.json$/;
const ASSET_FILE = /^assets\/[A-Za-z0-9._-]+\.(?:js|css|svg|woff2?)$/;

export function classify(url: URL, scope: URL, method: string, mode: string): Route {
  if (method !== 'GET') return 'bypass';
  if (url.origin !== scope.origin) return 'bypass';
  if (!url.pathname.startsWith(scope.pathname)) return 'bypass';
  if (url.search !== '') return 'bypass';
  const rel = url.pathname.slice(scope.pathname.length);
  if (mode === 'navigate') return rel === '' || rel === 'index.html' ? 'nav' : 'bypass';
  if (ASSET_FILE.test(rel) || SHELL_FILES.has(rel)) return 'asset';
  if (DATA_FILE.test(rel)) return 'data';
  return 'bypass';
}
