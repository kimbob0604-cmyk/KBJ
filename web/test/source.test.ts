// 데이터 소스 계층 — 봉투 검사, 공개 manifest 보충, API 오류 처리(본문 미표시)
import { describe, expect, it, vi } from 'vitest';
import { API_DOWNLOAD, API_PATH, createApiSource } from '../src/data/api';
import { asEnvelope, AuthRequiredError, DataError, hrefOf, LockedError, queryString, reportXlsxKey } from '../src/data/source';
import { createStaticSource, isPublicKey, parseManifest, PUBLIC_DOWNLOAD, PUBLIC_SUBDIR } from '../src/data/static';
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
    expect(API_PATH.test('index-review')).toBe(true);
    expect(API_PATH.test('index-review/stock/005930')).toBe(true);
    for (const bad of ['../x', '/api/x', 'http://evil/x', 'market/summary?x=1', 'a//b', '', '-x', 'a-/b', 'a--b']) {
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

describe('P4 공개 하위 폴더(fin/<코드>·reports/<코드>) — p4_design §1.2·§7', () => {
  const finEnv = envelope({ code: 'Q00001', quarters: [] }, { source: 'DART', as_of: '2026-10-07T05:30:00+09:00' });

  it('허용 키는 하위 폴더 두 개 + 코드 형식만', () => {
    for (const ok of ['calendar', 'fin/Q00001', 'fin/005930', 'reports/005930']) expect(isPublicKey(ok), ok).toBe(true);
    for (const bad of [
      'fin/q00001',
      'fin/00593',
      'fin/0059300',
      'fin/Q00001/x',
      'reports/005930.xlsx',
      'stock/005930/header',
      'filings/feed',
      'flows/stock/005930',
      'fin/../manifest',
      'FIN/005930',
    ]) {
      expect(isPublicKey(bad), bad).toBe(false);
    }
    expect(PUBLIC_SUBDIR.test('fin/Q00001')).toBe(true);
  });

  it('P5 하위 폴더(macro/<이름>)와 credit — p5_design §7(테마 themes/ 는 P5 제외)', () => {
    for (const ok of ['macro/tabs', 'macro/rates', 'macro/calendar', 'macro/us_2', 'credit']) {
      expect(isPublicKey(ok), ok).toBe(true);
    }
    for (const bad of ['macro/Rates', 'macro/x/y', 'macro/a-b', 'themes/index', 'macro/../manifest', 'MACRO/tabs', 'macro/tabs.json']) {
      expect(isPublicKey(bad), bad).toBe(false);
    }
  });

  it('P6 하위 폴더(trade/<이름>·trade/(hs|country|region|stock)/<코드>) — p6_design §7', () => {
    for (const ok of [
      'trade/flash', 'trade/hs2', 'trade/hs/85', 'trade/hs/8542', 'trade/country/US',
      'trade/region/854232', 'trade/stock/000660', 'trade/items104',
    ]) {
      expect(isPublicKey(ok), ok).toBe(true);
    }
    for (const bad of [
      'trade/Flash', 'trade/hs/8', 'trade/hs/85/x', 'trade/x/85', 'trade/stock/00066!',
      'trade/../manifest', 'trade/flash.json', 'trade/stock/00066012345',
    ]) {
      expect(isPublicKey(bad), bad).toBe(false);
    }
  });

  it('P5 하위 폴더 파일도 봉투 그대로 — 봉투가 아니면 manifest 로 보충하지 않는다', async () => {
    const env = envelope({ tabs: [] }, { source: 'ECOS', as_of: '2026-11-13T05:30:00+09:00' });
    const { fetch, calls } = fakeFetch(() => json(env));
    const got = await createStaticSource({ fetch }).get<{ tabs: unknown[] }>('macro/tabs');
    expect(got.source).toBe('ECOS');
    expect(calls[0]?.url).toBe('./data/macro/tabs.json');
    const bare = fakeFetch(() => json({ tabs: [] }));
    await expect(createStaticSource({ fetch: bare.fetch }).get('macro/rates')).rejects.toThrow('원천·시각·품질 표시 없음');
  });

  it('하위 폴더 파일은 ./data/<폴더>/<코드>.json 을 읽고, 봉투 그대로', async () => {
    const { fetch, calls } = fakeFetch(() => json(finEnv));
    const src = createStaticSource({ fetch });
    const env = await src.get<{ code: string }>('fin/Q00001');
    expect(env.source).toBe('DART');
    expect(env.data.code).toBe('Q00001');
    expect(calls.map((c) => c.url)).toEqual(['./data/fin/Q00001.json']);
    expect(calls[0]?.init?.credentials).toBe('omit');
    await src.get('reports/005930');
    expect(calls[1]?.url).toBe('./data/reports/005930.json');
  });

  it('하위 폴더 파일이 봉투가 아니면 manifest 로 보충하지 않고 오류(manifest 도 읽지 않는다)', async () => {
    const { fetch, calls } = fakeFetch((url) => (url.endsWith('manifest.json') ? json(manifest) : json({ quarters: [] })));
    await expect(createStaticSource({ fetch }).get('fin/Q00001')).rejects.toThrow('원천·시각·품질 표시 없음');
    expect(calls.map((c) => c.url)).toEqual(['./data/fin/Q00001.json']);
  });

  it('없는 종목 파일은 404 "아직 없음"(공개판 — "공시 재무 없음" 은 페이지가 정한다)', async () => {
    const { fetch } = fakeFetch(() => json({}, 404));
    const err = (await createStaticSource({ fetch })
      .get('fin/Q99999')
      .catch((e: unknown) => e)) as DataError;
    expect(err).toBeInstanceOf(DataError);
    expect(err.status).toBe(404);
    expect(err.message).toBe('아직 없음');
  });

  it('그 밖의 슬래시 키는 요청 없이 LockedError(로그인 API 경로를 모른다)', async () => {
    const { fetch, calls } = fakeFetch(() => json(finEnv));
    const src = createStaticSource({ fetch });
    for (const key of ['stock/005930/header', 'stock/005930/consensus', 'watchlist/main', 'fin/q00001', 'reports/005930.xlsx']) {
      await expect(src.get(key), key).rejects.toBeInstanceOf(LockedError);
    }
    expect(calls).toHaveLength(0);
  });
});

describe('P4 내려받기 주소(href) — 리포트 엑셀', () => {
  it('키: 로그인 = API 경로(stock/<코드>/report.xlsx), 공개 = reports/<코드>.xlsx', () => {
    expect(reportXlsxKey('login', '005930')).toBe('stock/005930/report.xlsx');
    expect(reportXlsxKey('public', '005930')).toBe('reports/005930.xlsx');
  });

  it('공개: reports/<코드>.xlsx 만 주소를 만든다(요청하지 않음), 그 밖은 null', () => {
    const { fetch, calls } = fakeFetch(() => json({}, 500));
    const src = createStaticSource({ fetch });
    expect(hrefOf(src, 'reports/005930.xlsx')).toBe('./data/reports/005930.xlsx');
    expect(createStaticSource({ fetch, base: '/kbj/data/' }).href?.('reports/Q00001.xlsx')).toBe('/kbj/data/reports/Q00001.xlsx');
    for (const bad of ['stock/005930/report.xlsx', 'reports/005930', 'reports/q00001.xlsx', 'fin/005930.json', '../x.xlsx']) {
      expect(hrefOf(src, bad), bad).toBeNull();
    }
    expect(PUBLIC_DOWNLOAD.test('reports/005930.xlsx')).toBe(true);
    expect(calls).toHaveLength(0);
  });

  it('로그인: 같은 출처 API 주소(쿠키 세션으로 브라우저가 받는다), 형식이 틀리면 null', () => {
    const { fetch, calls } = fakeFetch(() => json({}, 500));
    const src = createApiSource({ fetch });
    expect(hrefOf(src, reportXlsxKey('login', '005930'))).toBe('/api/stock/005930/report.xlsx');
    for (const bad of ['stock/005930/report', '../auth/logout.xlsx', 'stock/005930/report.csv', '/api/x.xlsx', 'a//b.xlsx']) {
      expect(hrefOf(src, bad), bad).toBeNull();
    }
    expect(API_DOWNLOAD.test('stock/Q00001/report.xlsx')).toBe(true);
    expect(calls).toHaveLength(0);
  });

  it('href 가 없는 소스(시험 가짜)는 null', () => {
    expect(hrefOf({}, 'reports/005930.xlsx')).toBeNull();
  });
});
