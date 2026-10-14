// 로그인 데이터 소스의 쓰기 채널(P8 WRAP — JNL·BKT·EVL 요청): post·put·patch·del 에 CSRF 헤더·JSON 본문,
// 토큰 없으면 요청 0, 거절은 서버 짧은 사유만, 401 은 로그인 화면으로. 쓰기 패널은 `post` 유무로 채널을 찾는다.
import { describe, expect, it, vi } from 'vitest';
import { createApiSource } from '../src/data/api';
import { createAuth } from '../src/data/auth';
import { AuthRequiredError, DataError } from '../src/data/source';
import { journalWriter } from '../src/pages/p12/journal';
import { ruleWriter } from '../src/pages/p11/rules_api';
import { writerOf } from '../src/pages/p12/backtest';
import { watchWriter } from '../src/pages/p8/header';
import { envelope, fakeFetch, json } from './helpers';

const CSRF = { 'X-KBJ-CSRF': 'tok-합성' };

describe('createApiSource 쓰기 채널', () => {
  it('writeHeaders 가 없으면 쓰기 채널이 없다(읽기 전용) — 쓰기 패널은 안내만', () => {
    const src = createApiSource({ fetch: fakeFetch(() => json({})).fetch });
    expect('post' in src).toBe(false);
    expect(journalWriter(src)).toBeNull();
    expect(ruleWriter(src)).toBeNull();
    expect(writerOf(src)).toBeNull();
    expect(watchWriter(src)).toBeNull();
  });

  it('있으면 넷 다 — 쓰기 패널 탐지 함수가 모두 찾는다', () => {
    const src = createApiSource({ fetch: fakeFetch(() => json({})).fetch, writeHeaders: () => CSRF });
    expect(journalWriter(src)).not.toBeNull();
    expect(ruleWriter(src)).not.toBeNull();
    expect(writerOf(src)).not.toBeNull();
    expect(watchWriter(src)).not.toBeNull();
  });

  it('POST: CSRF 헤더·JSON 본문·같은 출처 쿠키, 봉투를 돌려준다', async () => {
    const { fetch, calls } = fakeFetch(() => json(envelope({ rule_id: 'r_x', version: 1 }), 201));
    const src = createApiSource({ fetch, writeHeaders: () => CSRF });
    const env = await src.post?.<{ rule_id: string }>('rules', { name: '합성' });
    expect(env?.data.rule_id).toBe('r_x');
    const init = calls[0]?.init;
    expect(calls[0]?.url).toBe('/api/rules');
    expect(init?.method).toBe('POST');
    expect(init?.credentials).toBe('same-origin');
    const h = init?.headers as Record<string, string>;
    expect(h['X-KBJ-CSRF']).toBe('tok-합성');
    expect(h['Content-Type']).toBe('application/json');
    expect(init?.body).toBe(JSON.stringify({ name: '합성' }));
  });

  it('본문 없는 쓰기(켜기·관심 별·삭제)는 Content-Type·본문을 싣지 않는다', async () => {
    const { fetch, calls } = fakeFetch(() => json(envelope({})));
    const src = createApiSource({ fetch, writeHeaders: () => CSRF });
    await src.post?.('rules/r_x/enable', null);
    await src.put?.('watchlist/main/005930');
    await src.del?.('journal/trades/7');
    await src.patch?.('journal/analysis/3', { memo: 'a' });
    expect(calls.map((c) => c.init?.method)).toEqual(['POST', 'PUT', 'DELETE', 'PATCH']);
    for (const c of calls.slice(0, 3)) {
      expect(c.init?.body).toBeUndefined();
      expect((c.init?.headers as Record<string, string>)['Content-Type']).toBeUndefined();
    }
  });

  it('CSRF 토큰이 없으면 요청하지 않고 403 실패', async () => {
    const { fetch, calls } = fakeFetch(() => json(envelope({})));
    const src = createApiSource({ fetch, writeHeaders: () => null });
    const err = await src.post?.('rules', {}).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(DataError);
    expect((err as DataError).status).toBe(403);
    expect(calls).toHaveLength(0);
  });

  it('잘못된 경로는 요청 0', async () => {
    const { fetch, calls } = fakeFetch(() => json(envelope({})));
    const src = createApiSource({ fetch, writeHeaders: () => CSRF });
    await expect(src.post?.('../auth/logout', {})).rejects.toThrow('잘못된 API 경로');
    expect(calls).toHaveLength(0);
  });

  it('거절(4xx)은 서버 짧은 사유만 — 그 밖 본문은 싣지 않는다', async () => {
    const reject = fakeFetch(() => json({ code: 'invalid_rule', message: '조건이 비었다' }, 422));
    const e1 = (await createApiSource({ fetch: reject.fetch, writeHeaders: () => CSRF })
      .post?.('rules', {})
      .catch((e: unknown) => e)) as DataError;
    expect(e1.status).toBe(422);
    expect(e1.message).toBe('조건이 비었다');
    const leak = fakeFetch(() => json({ detail: '내부 정보' }, 500));
    const e2 = (await createApiSource({ fetch: leak.fetch, writeHeaders: () => CSRF })
      .post?.('rules', {})
      .catch((e: unknown) => e)) as DataError;
    expect(e2.message).not.toContain('내부 정보');
    expect(e2.message).toContain('HTTP 500');
  });

  it('401 → onUnauthorized + AuthRequiredError', async () => {
    const onUnauthorized = vi.fn();
    const { fetch } = fakeFetch(() => json({}, 401));
    const src = createApiSource({ fetch, writeHeaders: () => CSRF, onUnauthorized });
    await expect(src.del?.('journal/trades/1')).rejects.toBeInstanceOf(AuthRequiredError);
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it('봉투가 아닌 성공 응답은 거절(원천 없는 값)', async () => {
    const { fetch } = fakeFetch(() => json({ ok: true }));
    const src = createApiSource({ fetch, writeHeaders: () => CSRF });
    await expect(src.post?.('backtest', {})).rejects.toThrow('원천·시각·품질');
  });
});

describe('auth.writeHeaders', () => {
  it('세션 확인 뒤 토큰 헤더, 로그아웃 뒤 null', async () => {
    const { fetch } = fakeFetch((url) =>
      url.endsWith('/me') ? json({ user: 'me', csrf_token: 't1' }) : new Response(null, { status: 204 }),
    );
    const auth = createAuth({ fetch });
    expect(auth.writeHeaders()).toBeNull();
    await auth.me();
    expect(auth.writeHeaders()).toEqual({ 'X-KBJ-CSRF': 't1' });
    await auth.logout();
    expect(auth.writeHeaders()).toBeNull();
  });
});
