// 로그인 빌드 부팅 — 세션 확인 → (없으면) 로그인 화면 → 앱. API 가 401 을 주면 앱을 내리고 로그인 화면으로.
// TradingView 를 import 하지 않는다(로그인판은 KIS·KRX 원천 위젯을 그린다).
import { type AppHandle, type MountOptions, mountApp } from '../app/app';
import { createApiSource } from '../data/api';
import { type Auth, createAuth, renderLoginForm, type Session } from '../data/auth';
import type { FetchLike } from '../data/source';
import { h, replace } from '../ui/dom';

export interface BootLoginOptions extends Partial<Omit<MountOptions, 'tier' | 'source' | 'user' | 'onLogout'>> {
  fetch?: FetchLike;
}

export interface LoginBoot {
  readonly auth: Auth;
  /** 지금 떠 있는 앱(로그인 화면이면 null) */
  app(): AppHandle | null;
}

export async function bootLogin(root: HTMLElement, o: BootLoginOptions = {}): Promise<LoginBoot> {
  const { fetch, ...mountRest } = o;
  const auth = createAuth(fetch ? { fetch } : {});
  let app: AppHandle | null = null;

  const showLogin = (notice?: string): void => {
    app?.destroy();
    app = null;
    renderLoginForm(root, auth, { onSuccess: start, ...(notice ? { notice } : {}) });
  };

  function start(session: Session): void {
    const source = createApiSource({
      ...(fetch ? { fetch } : {}),
      onUnauthorized: () => {
        if (app) showLogin('세션이 끝났습니다. 다시 로그인하세요.');
      },
    });
    app?.destroy();
    app = mountApp(root, {
      ...mountRest,
      tier: 'login',
      source,
      user: session.user,
      onLogout: () => {
        auth
          .logout()
          .catch((err: unknown) => console.error('[kbj] 로그아웃 오류', err instanceof Error ? err.message : err))
          .finally(() => showLogin('로그아웃했습니다.'));
      },
    });
  }

  try {
    const session = await auth.me();
    if (session) start(session);
    else showLogin();
  } catch (err) {
    // 서버에 닿지 못함 — 감추지 않고 화면에 알린다(로그인 폼 대신 오류)
    console.error('[kbj] 세션 확인 실패', err instanceof Error ? err.message : err);
    replace(
      root,
      h(
        'main',
        { class: 'login' },
        h('h1', null, 'KBJ'),
        h('p', { class: 'msg', attrs: { role: 'alert' } }, '서버에 연결하지 못했습니다. 잠시 뒤 새로 고침하세요.'),
      ),
    );
  }
  return { auth, app: () => app };
}
