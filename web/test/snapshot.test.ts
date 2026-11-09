// 공개 사이트(맥이 올린 정적 스냅샷) — 파일 이름 규칙·없는 보기·서버 안내·검색·페이지 11~13 없음
import { describe, expect, it } from 'vitest';
import { bootSite, SITE_PAGES } from '../src/boot/site';
import { createSnapshotSource, MISSING, siteFile } from '../src/data/snapshot';
import { DataError, type Params } from '../src/data/source';
import { appRoot, envelope, fakeFetch, fakeLoaders, fixedClock, json } from './helpers';

// tests/unit/api/test_site.py NAME_CASES 와 같은 표 — 두 쪽이 어긋나면 화면이 파일을 못 찾는다
const NAME_CASES: [string, Params | undefined, string][] = [
  ['market/summary', undefined, 'market/summary.json'],
  ['etf/flows', { period: 5, mode: 'out' }, 'etf/flows@mode-out.period-5.json'],
  ['x', { b: true, a: '', c: null }, 'x@b-true.json'],
  ['diag/history', { key: 'credit/spread', from: '2026-11-09' }, 'diag/history@from-2026-11-09.key-credit_spread.json'],
  ['stock/005930/valuation', { metric: 'per' }, 'stock/005930/valuation@metric-per.json'],
];

describe('snapshot source', () => {
  it.each(NAME_CASES)('siteFile(%s)', (key, params, want) => {
    expect(siteFile(key, params)).toBe(want);
  });

  it('API 경로 키로 ./data/ 파일을 읽고 쓰기 채널은 없다', async () => {
    const { fetch, calls } = fakeFetch(() => json(envelope({ v: 1 })));
    const src = createSnapshotSource({ fetch });
    const env = await src.get<{ v: number }>('etf/flows', { mode: 'out', period: 5 });
    expect(env.data.v).toBe(1);
    // 인자가 있으면 기본값 표를 한 번 먼저 읽는다
    expect(calls.map((c) => c.url)).toEqual(['./data/_defaults.json', './data/etf/flows@mode-out.period-5.json']);
    expect(calls[1]?.init?.credentials).toBe('omit');
    expect(src.tier).toBe('login');
    expect('post' in src).toBe(false);
    expect(src.href?.('stock/005930/report.xlsx')).toBeNull();
  });

  it('없는 보기: 날짜 인자만 빼고 한 번 더, 다른 인자는 빼지 않는다', async () => {
    const { fetch, calls } = fakeFetch((url) => (url === './data/filings/earnings.json' ? json(envelope([])) : json({}, 404)));
    const src = createSnapshotSource({ fetch });
    await expect(src.get('filings/earnings', { from: '2026-11-09' })).resolves.toBeTruthy();
    expect(calls.map((c) => c.url)).toEqual([
      './data/_defaults.json',
      './data/filings/earnings@from-2026-11-09.json',
      './data/filings/earnings.json',
    ]);
    const err = await src.get('etf/flows', { mode: 'out' }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(DataError);
    expect((err as DataError).message).toBe(MISSING);
    expect(calls.at(-1)?.url).toBe('./data/etf/flows@mode-out.json');
  });

  it('기본값과 같은 인자는 빼고 찾는다(경로 틀 포함), 다른 값은 그대로', async () => {
    const defaults = { 'stock/{code}/valuation': { metric: 'per' }, 'stock/{code}/chart': { days: 250 }, 'etf/flows': { period: 5 } };
    const { fetch, calls } = fakeFetch((url) => (url === './data/_defaults.json' ? json(defaults) : json(envelope(1))));
    const src = createSnapshotSource({ fetch });
    await src.get('stock/005930/valuation', { metric: 'per' });
    await src.get('stock/005930/valuation', { metric: 'pbr' });
    await src.get('stock/005930/chart', { days: '250' });
    await src.get('etf/flows', { period: 5, mode: 'out' });
    expect(calls.map((c) => c.url)).toEqual([
      './data/_defaults.json',
      './data/stock/005930/valuation.json',
      './data/stock/005930/valuation@metric-pbr.json',
      './data/stock/005930/chart.json',
      './data/etf/flows@mode-out.json',
    ]);
  });

  it('404 no_data 본문 파일은 서버 안내 문구 그대로', async () => {
    const { fetch } = fakeFetch(() => json({ code: 'no_data', message: '아직 없음 — rules.close 16:00' }));
    const err = await createSnapshotSource({ fetch })
      .get('board/us')
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(DataError);
    expect((err as DataError).serverMessage).toBe('아직 없음 — rules.close 16:00');
  });

  it('봉투가 아니면 그리지 않는다(원천 없는 숫자 금지)', async () => {
    const { fetch } = fakeFetch(() => json({ v: 1 }));
    await expect(createSnapshotSource({ fetch }).get('market/summary')).rejects.toThrow('원천');
  });

  it('검색: 종목 목록 한 번 받아 이름·코드로 거른다', async () => {
    const items = [
      { code: '005930', name: '삼성전자' },
      { code: '000660', name: 'SK하이닉스' },
    ];
    const { fetch, calls } = fakeFetch(() => json(envelope({ q: '', n_total: 2, items })));
    const src = createSnapshotSource({ fetch });
    const a = await src.get<{ items: { code: string }[] }>('stock/search', { q: '삼성', limit: 20 });
    expect(a.data.items.map((i) => i.code)).toEqual(['005930']);
    const b = await src.get<{ items: { code: string }[] }>('stock/search', { q: '0006' });
    expect(b.data.items.map((i) => i.code)).toEqual(['000660']);
    expect(calls.map((c) => c.url)).toEqual(['./data/stock/search-index.json']);
  });

  it('잘못된 경로는 요청하지 않는다', async () => {
    const { fetch, calls } = fakeFetch(() => json(envelope(1)));
    await expect(createSnapshotSource({ fetch }).get('../secret')).rejects.toThrow('잘못된 경로');
    expect(calls).toHaveLength(0);
  });
});

describe('bootSite', () => {
  it('페이지 1~10 만 붙이고 로그아웃·로그인 화면 없이, 배지는 스냅샷', () => {
    const root = appRoot();
    const { fetch } = fakeFetch(() => json(envelope({})));
    const app = bootSite(root, { fetch, now: fixedClock(), ribbon: null, loaders: fakeLoaders({}) });
    const ids = [...root.querySelectorAll('section.page')].map((s) => s.id);
    expect(ids).toEqual(SITE_PAGES.map((n) => `page-${n}`));
    expect(root.querySelector('form input[type="password"]')).toBeNull();
    expect([...root.querySelectorAll('button')].map((b) => b.textContent)).not.toContain('로그아웃');
    expect(root.querySelector('.badge')?.textContent).toBe('스냅샷');
    expect(app.ctx.tier).toBe('login');
    app.destroy();
  });
});
