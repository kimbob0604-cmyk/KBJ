// 데이터 소스 계층 — 봉투 검사, 공개 manifest 보충, API 오류 처리(본문 미표시)
import { describe, expect, it, vi } from 'vitest';
import { API_PATH, createApiSource } from '../src/data/api';
import { asEnvelope, AuthRequiredError, DataError, queryString } from '../src/data/source';
import { createStaticSource, parseManifest } from '../src/data/static';
import calendar from './fixtures/public/calendar.json';
import events from './fixtures/public/events.json';
import manifest from './fixtures/public/manifest.json';
import { envelope, fakeFetch, json } from './helpers';

describe('봉투', () => {
  it('source·as_of(시간대)·quality·data 가 있어야 한다', () => {
    expect(asEnvelope(envelope(1))).not.toBeNull();
    expect(asEnvelope({ ...envelope(1), source: '' })).toBeNull();
    expect(asEnvelope({ ...envelope(1), as_of: '2026-10-07T15:30:00' })).toBeNull(); // naive
    expect(asEnvelope({ ...envelope(1), quality: 'great' })).toBeNull();
    const noData: Record<string, unknown> = { ...envelope(1) };
    delete noData.data;
    expect(asEnvelope(noData)).toBeNull();
    expect(asEnvelope([1, 2])).toBeNull();
  });

  it('notes·generated_at 이 없으면 채운다', () => {
    const e = asEnvelope({ source: 'KRX', as_of: '2026-10-07T06:30:00Z', quality: 'ok', data: 1 });
    expect(e?.notes).toEqual([]);
    expect(e?.generated_at).toBe('2026-10-07T06:30:00Z');
  });

  it('쿼리 문자열: 키 정렬·빈 값 생략·인코딩', () => {
    expect(queryString({ b: 2, a: '가', c: null, d: '' })).toBe('?a=%EA%B0%80&b=2');
    expect(queryString({})).toBe('');
    expect(queryString(undefined)).toBe('');
  });
});

describe('공개 데이터 소스', () => {
  it('봉투 파일은 그대로', async () => {
    const { fetch, calls } = fakeFetch(() => json(calendar));
    const env = await createStaticSource({ fetch }).get('calendar');
    expect(env.source).toBe('KBJ 거래 캘린더(코드 계산)');
    expect(calls[0]?.url).toBe('./data/calendar.json');
    expect(calls[0]?.init?.credentials).toBe('omit');
  });

  it('봉투가 아닌 파일은 manifest 로 원천·시각·품질을 붙인다', async () => {
    const { fetch, calls } = fakeFetch((url) => (url.endsWith('manifest.json') ? json(manifest) : json(events.data)));
    const src = createStaticSource({ fetch });
    const env = await src.get<{ events: unknown[] }>('events');
    expect(env.source).toBe('한국은행 공표 일정(수기 설정)');
    expect(env.quality).toBe('ok');
    expect(env.data.events).toHaveLength(1);
    await src.get('events');
    expect(calls.filter((c) => c.url.endsWith('manifest.json'))).toHaveLength(1); // manifest 는 한 번만
  });

  it('manifest 에도 없으면 오류 — 원천 없는 숫자를 그리지 않는다', async () => {
    const { fetch } = fakeFetch((url) => (url.endsWith('manifest.json') ? json(manifest) : json({ x: 1 })));
    await expect(createStaticSource({ fetch }).get('credit')).rejects.toThrow('원천·시각·품질 표시 없음');
  });

  it('없는 파일은 "아직 없음", 그 밖은 상태 코드만', async () => {
    const { fetch } = fakeFetch((url) => (url.includes('a.json') ? json({}, 404) : json({ secret: 'body' }, 500)));
    const src = createStaticSource({ fetch });
    await expect(src.get('a')).rejects.toThrow('아직 없음');
    const err = await src.get('b').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(DataError);
    expect((err as DataError).message).toBe('불러오지 못함 (HTTP 500)');
    expect((err as DataError).message).not.toContain('secret');
  });

  it('manifest 두 모양(배열·객체)과 봉투 안 manifest', () => {
    expect(parseManifest(manifest).files.map((f) => f.name)).toEqual(['calendar', 'events']);
    const obj = parseManifest({
      generated_at: '2026-10-07T05:30:00+09:00',
      files: { 'calendar.json': { source: 'S', as_of: '2026-10-07T05:30:00+09:00', quality: 'ok' } },
    });
    expect(obj.files[0]?.name).toBe('calendar');
    const wrapped = parseManifest(envelope({ files: [] }, { source: 'KBJ' }));
    expect(wrapped.files).toEqual([]);
    expect(() => parseManifest({ nope: 1 })).toThrow('files');
    // 품질이 이상한 항목은 버린다
    expect(parseManifest({ files: [{ name: 'x', source: 'S', as_of: 'a', quality: 'great' }] }).files).toEqual([]);
  });
});

describe('로그인 API 소스', () => {
  it('경로 형식: 상위 경로·절대 URL·쿼리 섞기 금지', async () => {
    expect(API_PATH.test('market/summary')).toBe(true);
    expect(API_PATH.test('flows/stock/005930')).toBe(true);
    for (const bad of ['../x', '/api/x', 'http://evil/x', 'market/summary?x=1', 'a//b', '']) {
      expect(API_PATH.test(bad), bad).toBe(false);
    }
    const { fetch, calls } = fakeFetch(() => json(envelope(1)));
    await expect(createApiSource({ fetch }).get('../auth/logout')).rejects.toThrow('잘못된 API 경로');
    expect(calls).toHaveLength(0);
  });

  it('401 → onUnauthorized + AuthRequiredError', async () => {
    const onUnauthorized = vi.fn();
    const { fetch } = fakeFetch(() => json({}, 401));
    await expect(createApiSource({ fetch, onUnauthorized }).get('board/newhigh')).rejects.toBeInstanceOf(
      AuthRequiredError,
    );
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it('오류 문구에 응답 본문을 싣지 않는다', async () => {
    const { fetch } = fakeFetch(() => json({ detail: '내부 정보' }, 503));
    const err = await createApiSource({ fetch })
      .get('etf/flows')
      .catch((e: unknown) => e);
    expect((err as DataError).status).toBe(503);
    expect((err as DataError).message).not.toContain('내부 정보');
  });

  it('404 no_data 는 서버 안내 문구를 싣고, 그 밖의 404 본문은 싣지 않는다', async () => {
    const msg = '준비 중 — 업종지수 코드(market.sector_indices) [실측 필요]';
    const ok = fakeFetch(() => json({ code: 'no_data', message: msg }, 404));
    const err = (await createApiSource({ fetch: ok.fetch })
      .get('market/sectors')
      .catch((e: unknown) => e)) as DataError;
    expect(err.status).toBe(404);
    expect(err.serverMessage).toBe(msg);
    expect(err.message).toBe(msg);
    for (const body of [{ detail: 'Not Found' }, { code: 'other', message: '내부' }, { code: 'no_data', message: 'x'.repeat(201) }]) {
      const other = fakeFetch(() => json(body, 404));
      const e = (await createApiSource({ fetch: other.fetch })
        .get('market/sectors')
        .catch((x: unknown) => x)) as DataError;
      expect(e.status).toBe(404);
      expect(e.serverMessage).toBeNull();
      expect(e.message).toBe('불러오지 못함 (HTTP 404)');
    }
    const html = fakeFetch(() => new Response('<html>', { status: 404 }));
    const e2 = (await createApiSource({ fetch: html.fetch })
      .get('market/sectors')
      .catch((x: unknown) => x)) as DataError;
    expect(e2.serverMessage).toBeNull();
  });

  it('봉투가 아닌 응답은 오류', async () => {
    const { fetch } = fakeFetch(() => json({ rows: [] }));
    await expect(createApiSource({ fetch }).get('etf/flows')).rejects.toThrow('원천·시각·품질 표시 없음');
    const bad = fakeFetch(() => new Response('<html>', { status: 200 }));
    await expect(createApiSource({ fetch: bad.fetch }).get('etf/flows')).rejects.toThrow('JSON 형식 오류');
  });
});
