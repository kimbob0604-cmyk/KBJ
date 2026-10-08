// 서비스 워커 등록 — 빌드 결과에서만(개발 서버 제외). 실패는 콘솔에 남긴다(화면 기능에는 영향 없음).
export function registerServiceWorker(
  nav: Navigator = navigator,
  enabled: boolean = import.meta.env.PROD,
): Promise<ServiceWorkerRegistration | null> {
  if (!enabled || !('serviceWorker' in nav)) return Promise.resolve(null);
  return nav.serviceWorker.register('./sw.js', { scope: './' }).catch((err: unknown) => {
    console.warn('[kbj] 서비스 워커 등록 실패 — 오프라인 캐시 없이 동작', err);
    return null;
  });
}
