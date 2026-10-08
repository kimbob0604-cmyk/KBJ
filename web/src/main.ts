// 부팅 — 등급 상수(VITE_KBJ_TIER)를 읽는 유일한 곳(docs/p3_design.md §6.2·§6.6).
//
// `import.meta.env.VITE_KBJ_TIER` 는 빌드 때 문자열로 바뀌어 아래 if 가 상수로 접힌다. 그래서
//   공개 빌드 번들에는 boot/login(→ data/api·data/auth) 청크가 아예 생기지 않고,
//   로그인 빌드 번들에는 boot/public(→ TradingView) 청크가 생기지 않는다.
// scripts/check-bundle.mjs 가 빌드 결과로 이를 확인한다.
import './design/tokens.css';
import './design/base.css';
import './design/components.css';
import { registerServiceWorker } from './pwa/register';

async function main(): Promise<void> {
  const root = document.getElementById('app');
  if (!root) throw new Error('#app 요소가 없다');
  if (import.meta.env.VITE_KBJ_TIER === 'login') {
    const { bootLogin } = await import('./boot/login');
    await bootLogin(root);
  } else {
    const { bootPublic } = await import('./boot/public');
    bootPublic(root);
  }
  void registerServiceWorker();
}

void main().catch((err: unknown) => {
  console.error('[kbj] 부팅 실패', err);
  const root = document.getElementById('app');
  if (root) root.textContent = '화면을 열지 못했습니다. 새로 고침하세요.';
});
