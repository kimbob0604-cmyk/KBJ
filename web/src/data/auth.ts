// 로그인(사용자 1명) — 폼·me·로그아웃. **로그인 빌드에만** 들어간다(docs/p3_design.md §5.4·§6.2).
//
// - 세션은 HttpOnly 쿠키(서버가 붙인다). 이 코드는 쿠키를 읽지 않는다.
// - CSRF 토큰은 메모리에만 둔다(localStorage 금지 — 기기에 남기지 않는다). 상태를 바꾸는 요청(로그아웃)에 헤더로.
// - 비밀번호는 폼 → 요청 본문으로만 간다. 로그·화면·저장소에 남기지 않는다(절대 규칙 5). 실패 문구에 응답 본문을 싣지 않는다.
import { h, replace } from '../ui/dom';
import { type FetchLike, globalFetch } from './source';

export const AUTH_BASE = '/api/auth/';
export const CSRF_HEADER = 'X-KBJ-CSRF';

export interface Session {
  user: string;
}

export type LoginResult =
  | { ok: true; session: Session }
  | { ok: false; reason: 'invalid' | 'locked' | 'not_configured' | 'error'; retryAfterS?: number };

export interface Auth {
  /** 세션 확인 — 없으면 null. 네트워크·서버 오류는 던진다(삼키지 않는다). */
  me(): Promise<Session | null>;
  login(username: string, password: string): Promise<LoginResult>;
  logout(): Promise<void>;
  /** 지금 들고 있는 CSRF 토큰(시험·상태 표시용 — 값을 화면에 그리지 않는다) */
  hasCsrf(): boolean;
}

export interface AuthOptions {
  fetch?: FetchLike;
  base?: string;
}

function csrfFrom(raw: unknown): string | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const v = (raw as Record<string, unknown>).csrf_token;
  return typeof v === 'string' && v.length > 0 ? v : null;
}

async function jsonOrNull(res: Response): Promise<unknown> {
  try {
    return (await res.json()) as unknown;
  } catch {
    return null;
  }
}

export function createAuth(opts: AuthOptions = {}): Auth {
  const doFetch = opts.fetch ?? globalFetch;
  const base = opts.base ?? AUTH_BASE;
  let csrf: string | null = null;

  return {
    async me() {
      const res = await doFetch(`${base}me`, {
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      });
      if (res.status === 401) {
        csrf = null;
        return null;
      }
      if (!res.ok) throw new Error(`세션 확인 실패 (HTTP ${res.status})`);
      const body = await jsonOrNull(res);
      const user = typeof body === 'object' && body !== null ? (body as Record<string, unknown>).user : null;
      csrf = csrfFrom(body);
      if (typeof user !== 'string' || !csrf) throw new Error('세션 응답 형식 오류');
      return { user };
    },

    async login(username, password) {
      const res = await doFetch(`${base}login`, {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      if (res.ok) {
        const token = csrfFrom(await jsonOrNull(res));
        if (!token) return { ok: false, reason: 'error' };
        csrf = token;
        return { ok: true, session: { user: username } };
      }
      const retry = Number(res.headers.get('Retry-After'));
      if (Number.isFinite(retry) && retry > 0) return { ok: false, reason: 'locked', retryAfterS: retry };
      if (res.status === 401) return { ok: false, reason: 'invalid' };
      if (res.status === 503) return { ok: false, reason: 'not_configured' };
      return { ok: false, reason: 'error' };
    },

    async logout() {
      const headers: Record<string, string> = { Accept: 'application/json' };
      if (csrf) headers[CSRF_HEADER] = csrf;
      const res = await doFetch(`${base}logout`, {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        headers,
      });
      csrf = null;
      if (!res.ok && res.status !== 401) throw new Error(`로그아웃 실패 (HTTP ${res.status})`);
    },

    hasCsrf: () => csrf !== null,
  };
}

export function loginMessage(r: Exclude<LoginResult, { ok: true }>): string {
  switch (r.reason) {
    case 'invalid':
      return '이름 또는 비밀번호가 맞지 않습니다.';
    case 'locked':
      return `시도가 너무 많습니다. ${Math.ceil((r.retryAfterS ?? 60) / 60)}분 뒤에 다시 하세요.`;
    case 'not_configured':
      return '서버에 로그인이 설정되지 않았습니다(관리자: KBJ_WEB_USER·KBJ_WEB_PASSWORD_HASH).';
    case 'error':
      return '로그인하지 못했습니다. 잠시 뒤 다시 하세요.';
  }
}

export interface LoginFormOptions {
  onSuccess: (s: Session) => void;
  /** 폼 위에 보일 안내(예: 세션 만료) */
  notice?: string;
}

/** 로그인 화면. root 의 내용을 바꾼다. */
export function renderLoginForm(root: HTMLElement, auth: Auth, opts: LoginFormOptions): void {
  const user = h('input', { attrs: { type: 'text', name: 'username', autocomplete: 'username', required: true } });
  const pass = h('input', {
    attrs: { type: 'password', name: 'password', autocomplete: 'current-password', required: true },
  });
  const msg = h('p', { class: 'msg', attrs: { role: 'alert', 'aria-live': 'assertive' } }, opts.notice ?? '');
  const submit = h('button', { attrs: { type: 'submit' } }, '로그인');
  const form = h(
    'form',
    {
      attrs: { novalidate: true },
      on: {
        submit: (ev) => {
          ev.preventDefault();
          if (!user.value || !pass.value) {
            msg.textContent = '이름과 비밀번호를 넣으세요.';
            return;
          }
          submit.disabled = true;
          msg.textContent = '';
          auth
            .login(user.value, pass.value)
            .then((r) => {
              pass.value = '';
              if (r.ok) opts.onSuccess(r.session);
              else msg.textContent = loginMessage(r);
            })
            .catch((err: unknown) => {
              pass.value = '';
              console.error('[kbj] 로그인 요청 실패', err instanceof Error ? err.name : 'unknown');
              msg.textContent = '서버에 연결하지 못했습니다.';
            })
            .finally(() => {
              submit.disabled = false;
            });
        },
      },
    },
    h('label', null, '이름', user),
    h('label', null, '비밀번호', pass),
    msg,
    submit,
  );
  replace(root, h('main', { class: 'login' }, h('h1', null, 'KBJ'), form));
  user.focus();
}
