// 로그인(로그인 빌드) — me·login·logout, CSRF 헤더, 실패 문구, 비밀번호가 남지 않음
import { describe, expect, it, vi } from 'vitest';
import { CSRF_HEADER, createAuth, loginMessage, renderLoginForm } from '../src/data/auth';
import { appRoot, fakeFetch, json } from './helpers';

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('auth', () => {
  it('me: 401 → null, 200 → 이름 + CSRF 보관(값은 밖에 내지 않는다)', async () => {
    const a = fakeFetch(() => json({}, 401));
    expect(await createAuth({ fetch: a.fetch }).me()).toBeNull();
    const b = fakeFetch(() => json({ user: 'me', csrf_token: 'tok' }));
    const auth = createAuth({ fetch: b.fetch });
    expect(await auth.me()).toEqual({ user: 'me' });
    expect(auth.hasCsrf()).toBe(true);
    expect(b.calls[0]?.init?.credentials).toBe('same-origin');
  });

  it('me: 서버 오류는 던진다(삼키지 않는다)', async () => {
    const { fetch } = fakeFetch(() => json({}, 502));
    await expect(createAuth({ fetch }).me()).rejects.toThrow('HTTP 502');
  });

  it('login: JSON 본문 POST, 성공하면 CSRF 를 받아 둔다', async () => {
    const { fetch, calls } = fakeFetch(() => json({ csrf_token: 'tok' }));
    const auth = createAuth({ fetch });
    const r = await auth.login('me', 'pw-123');
    expect(r).toEqual({ ok: true, session: { user: 'me' } });
    const init = calls[0]?.init;
    expect(calls[0]?.url).toBe('/api/auth/login');
    expect(init?.method).toBe('POST');
    expect((init?.headers as Record<string, string>)['Content-Type']).toBe('application/json');
    expect(JSON.parse(init?.body as string)).toEqual({ username: 'me', password: 'pw-123' });
    expect(auth.hasCsrf()).toBe(true);
  });

  it('login 실패 사유: 401·잠금(Retry-After)·503', async () => {
    const mk = (status: number, headers: Record<string, string> = {}) =>
      createAuth({ fetch: fakeFetch(() => json({}, status, headers)).fetch });
    expect(await mk(401).login('a', 'b')).toEqual({ ok: false, reason: 'invalid' });
    expect(await mk(401, { 'Retry-After': '900' }).login('a', 'b')).toEqual({
      ok: false,
      reason: 'locked',
      retryAfterS: 900,
    });
    expect(await mk(503).login('a', 'b')).toEqual({ ok: false, reason: 'not_configured' });
    expect(await mk(500).login('a', 'b')).toEqual({ ok: false, reason: 'error' });
    expect(loginMessage({ ok: false, reason: 'locked', retryAfterS: 900 })).toContain('15분');
  });

  it('logout: CSRF 헤더를 붙이고, 뒤에는 토큰을 버린다', async () => {
    const { fetch, calls } = fakeFetch((url) =>
      url.endsWith('/me') ? json({ user: 'me', csrf_token: 'tok' }) : new Response(null, { status: 204 }),
    );
    const auth = createAuth({ fetch });
    await auth.me();
    await auth.logout();
    const headers = calls[1]?.init?.headers as Record<string, string>;
    expect(calls[1]?.url).toBe('/api/auth/logout');
    expect(calls[1]?.init?.method).toBe('POST');
    expect(headers[CSRF_HEADER]).toBe('tok');
    expect(auth.hasCsrf()).toBe(false);
  });
});

describe('로그인 폼', () => {
  it('성공하면 onSuccess, 비밀번호 칸을 비운다', async () => {
    const root = appRoot();
    const { fetch } = fakeFetch(() => json({ csrf_token: 'tok' }));
    const onSuccess = vi.fn();
    renderLoginForm(root, createAuth({ fetch }), { onSuccess });
    const [user, pass] = [...root.querySelectorAll('input')];
    user!.value = 'me';
    pass!.value = 'pw-123';
    root.querySelector('form')?.dispatchEvent(new Event('submit', { cancelable: true }));
    await flush();
    await flush();
    expect(onSuccess).toHaveBeenCalledWith({ user: 'me' });
    expect(pass!.value).toBe('');
  });

  it('실패 문구는 고정 문장(응답 본문 없음), 비밀번호는 어디에도 남지 않는다', async () => {
    const root = appRoot();
    const logs: unknown[] = [];
    vi.spyOn(console, 'error').mockImplementation((...a: unknown[]) => logs.push(...a));
    vi.spyOn(console, 'warn').mockImplementation((...a: unknown[]) => logs.push(...a));
    const { fetch } = fakeFetch(() => json({ detail: '서버 내부 사유' }, 401));
    renderLoginForm(root, createAuth({ fetch }), { onSuccess: () => undefined });
    const [user, pass] = [...root.querySelectorAll('input')];
    user!.value = 'me';
    pass!.value = 'pw-secret-9';
    root.querySelector('form')?.dispatchEvent(new Event('submit', { cancelable: true }));
    await flush();
    await flush();
    const msg = root.querySelector('.msg')?.textContent ?? '';
    expect(msg).toBe('이름 또는 비밀번호가 맞지 않습니다.');
    const markup = new XMLSerializer().serializeToString(root);
    expect(markup).not.toContain('pw-secret-9');
    expect(markup).not.toContain('서버 내부 사유');
    expect(JSON.stringify(logs)).not.toContain('pw-secret-9');
  });

  it('네트워크 실패도 알리고 비밀번호를 콘솔에 남기지 않는다', async () => {
    const root = appRoot();
    const logs: unknown[] = [];
    vi.spyOn(console, 'error').mockImplementation((...a: unknown[]) => logs.push(...a));
    const fetch = () => Promise.reject(new TypeError('network down'));
    renderLoginForm(root, createAuth({ fetch }), { onSuccess: () => undefined });
    const [user, pass] = [...root.querySelectorAll('input')];
    user!.value = 'me';
    pass!.value = 'pw-secret-9';
    root.querySelector('form')?.dispatchEvent(new Event('submit', { cancelable: true }));
    await flush();
    await flush();
    expect(root.querySelector('.msg')?.textContent).toBe('서버에 연결하지 못했습니다.');
    expect(logs.length).toBeGreaterThan(0);
    expect(JSON.stringify(logs)).not.toContain('pw-secret-9');
  });

  it('빈 칸이면 요청하지 않는다', () => {
    const root = appRoot();
    const { fetch, calls } = fakeFetch(() => json({}));
    renderLoginForm(root, createAuth({ fetch }), { onSuccess: () => undefined });
    root.querySelector('form')?.dispatchEvent(new Event('submit', { cancelable: true }));
    expect(calls).toHaveLength(0);
    expect(root.querySelector('.msg')?.textContent).toBe('이름과 비밀번호를 넣으세요.');
  });
});
