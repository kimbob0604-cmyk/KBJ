// 공개 사이트(맥이 올린 정적 스냅샷) — 파일 이름 규칙·없는 보기·날짜 대체·서버 안내·전 종목 검색·상세 없음·
// 이 브라우저 관심종목(put·del 만)·페이지 11~13 없음
import { describe, expect, it } from 'vitest';
import { bootSite, SITE_PAGES } from '../src/boot/site';
import {
  createSnapshotSource,
  MISSING,
  parseWatch,
  READ_ONLY,
  siteFile,
  WATCH_BROKEN,
  WATCH_EMPTY,
  WATCH_MAX,
  WATCH_SAVE_FAILED,
  WATCH_STORAGE_KEY,
} from '../src/data/snapshot';
import { DataError, type Envelope, NO_DETAIL, type Params } from '../src/data/source';
import { ruleWriter } from '../src/pages/p11/rules_api';
import { writerOf as backtestWriter } from '../src/pages/p12/backtest';
import { journalWriter } from '../src/pages/p12/journal';
import { watchWriter } from '../src/pages/p8/header';
import { appRoot, envelope, fakeFetch, fakeLoaders, FIXED_NOW, fixedClock, json, memoryStorage } from './helpers';

// tests/unit/api/test_site.py NAME_CASES 와 같은 표 — 두 쪽이 어긋나면 화면이 파일을 못 찾는다
const NAME_CASES: [string, Params | undefined, string][] = [
  ['market/summary', undefined, 'market/summary.json'],
  ['etf/flows', { period: 5, mode: 'out' }, 'etf/flows@mode-out.period-5.json'],
  ['x', { b: true, a: '', c: null }, 'x@b-true.json'],
  ['diag/history', { key: 'credit/spread', from: '2026-11-09' }, 'diag/history@from-2026-11-09.key-credit_spread.json'],
  ['stock/005930/valuation', { metric: 'per' }, 'stock/005930/valuation@metric-per.json'],
  ['filings/feed', { date: '2026-11-10', kind: 'prelim', limit: 1000 }, 'filings/feed@date-2026-11-10.kind-prelim.limit-1000.json'],
  ['filings/insiders', { days: 30, min_ratio_pct: 0.1 }, 'filings/insiders@days-30.min_ratio_pct-0_1.json'],
];

/** 합성 전 종목 목록(시총 순) — 상세가 있는 종목(detail)은 994010·994020 뿐 */
const INDEX_ITEMS = [
  { code: '994010', name: '모의전자', market: 'KOSPI', kind: 'common', corp_code: '99400010', is_watch: false, detail: true },
  { code: '994020', name: '모의바이오', market: 'KOSDAQ', kind: 'common', corp_code: '99400020', is_watch: false, detail: true },
  { code: '994015', name: '모의전자우', market: 'KOSPI', kind: 'pref', corp_code: '99400010', is_watch: false, detail: false },
  { code: '99K170', name: '케이모의', market: 'KOSDAQ', kind: null, corp_code: null, is_watch: false, detail: false },
  { code: '994030', name: '한모의전자', market: 'KOSPI', kind: 'common', corp_code: '99400030', is_watch: false, detail: false },
];
const INDEX = envelope(
  { q: '', n_total: INDEX_ITEMS.length, complete: true, items: INDEX_ITEMS },
  { source: 'KRX+DART', notes: ['전 종목 5개 — 상세는 2종목만(화면에 자주 나온 순)'] },
);

function header(code: string, name: string, over: Record<string, unknown> = {}): Envelope<Record<string, unknown>> {
  return envelope(
    {
      code, name, market: 'KOSPI', kind: 'common', common_code: code, corp_code: null, corp_name: name, status_flags: null,
      listed_on: '2001-01-02', acc_mt: 12, induty_code: null, is_watch: false, detail_tier: true, newhigh: null,
      last: {
        date: '2026-10-06', close: 71000, chg_pct: 1.2, volume: 10, turnover: 100, mktcap: 1000, last_trade: '2026-10-06',
        stale: false, source: 'KRX', quality: 'ok',
      },
      ...over,
    },
    { as_of: '2026-10-06T15:30:00+09:00' },
  ); // prettier-ignore
}

/** './data/<이름>' 파일 표로 답하는 가짜 Pages — 없으면 404, status 로 다른 상태 */
function pages(files: Record<string, unknown>, status: Record<string, number> = {}) {
  return fakeFetch((url) => {
    const name = url.replace('./data/', '');
    const st = status[name];
    if (st) return json({}, st);
    return name in files ? json(files[name]) : json({}, 404);
  });
}

const SITE_FILES: Record<string, unknown> = {
  'stock/search-index.json': INDEX,
  'stock/994010/header.json': header('994010', '모의전자'),
  'stock/994020/header.json': header('994020', '모의바이오', {
    last: { date: '2026-10-07', close: 5000, chg_pct: -2, volume: 1, turnover: 1, mktcap: 1, last_trade: '2026-10-07', stale: false, source: 'KIS', quality: 'estimated' },
  }), // prettier-ignore
};

const files = (calls: { url: string }[]): string[] => calls.map((c) => c.url.replace('./data/', ''));

async function failure(p: Promise<unknown>): Promise<DataError> {
  const err = await p.catch((e: unknown) => e);
  if (!(err instanceof DataError)) throw new Error('DataError 가 아니다');
  return err;
}

describe('snapshot source', () => {
  it.each(NAME_CASES)('siteFile(%s)', (key, params, want) => {
    expect(siteFile(key, params)).toBe(want);
  });

  it('API 경로 키로 ./data/ 파일을 읽는다', async () => {
    const { fetch, calls } = fakeFetch(() => json(envelope({ v: 1 })));
    const src = createSnapshotSource({ fetch });
    const env = await src.get<{ v: number }>('etf/flows', { mode: 'out', period: 5 });
    expect(env.data.v).toBe(1);
    // 인자가 있으면 기본값 표를 한 번 먼저 읽는다
    expect(calls.map((c) => c.url)).toEqual(['./data/_defaults.json', './data/etf/flows@mode-out.period-5.json']);
    expect(calls[1]?.init?.credentials).toBe('omit');
    expect(src.tier).toBe('login');
    expect(src.href?.('stock/005930/report.xlsx')).toBeNull();
  });

  it('쓰기는 관심종목 put·del 만 — post·patch 가 없어 페이지 11·12 쓰기 패널(규칙·저널·백테스트)이 켜지지 않는다', () => {
    const src = createSnapshotSource({ fetch: fakeFetch(() => json({}, 404)).fetch, storage: memoryStorage() });
    expect('post' in src).toBe(false);
    expect('patch' in src).toBe(false);
    expect(ruleWriter(src)).toBeNull();
    expect(journalWriter(src)).toBeNull();
    expect(backtestWriter(src)).toBeNull();
    expect(watchWriter(src)).not.toBeNull(); // 페이지 8 머리의 ☆ 관심 별 하나
  });

  it('없는 보기: 날짜를 빼고 찾은 파일은 요청한 날짜가 그 파일을 만든 날(KST)일 때만 — 어제를 오늘 파일로 그리지 않는다', async () => {
    // 날짜 없이 만든 파일 — generated_at 2026-10-07 16:00 KST
    const made = envelope({ rows: [] });
    const { fetch, calls } = pages({ 'filings/feed@limit-1000.json': made, 'filings/earnings.json': made });
    const src = createSnapshotSource({ fetch });
    // 오늘(만든 날)
    await expect(src.get('filings/feed', { date: '2026-10-07', limit: 1000 })).resolves.toBeTruthy();
    expect(files(calls).slice(-2)).toEqual(['filings/feed@date-2026-10-07.limit-1000.json', 'filings/feed@limit-1000.json']);
    // 어제 — 날짜 없는 파일(오늘 것)을 읽지만 쓰지 않는다
    const yday = await failure(src.get('filings/feed', { date: '2026-10-06', limit: 1000 }));
    expect(yday.message).toBe(MISSING);
    expect(yday.serverMessage).toBe(MISSING);
    // from·to 는 to 가 요청한 날짜
    await expect(src.get('filings/earnings', { from: '2026-10-01', to: '2026-10-07' })).resolves.toBeTruthy();
    expect((await failure(src.get('filings/earnings', { from: '2026-09-30', to: '2026-10-06' }))).message).toBe(MISSING);
    // date·to 가 없으면(from 만) 날짜 없는 파일을 찾지도 않는다
    const before = calls.length;
    expect((await failure(src.get('filings/earnings', { from: '2026-10-01' }))).message).toBe(MISSING);
    expect(files(calls.slice(before))).toEqual(['filings/earnings@from-2026-10-01.json']);
  });

  it('날짜 대체: 만든 날은 KST 로 본다(UTC 저녁 = 다음 날 KST), no_data 본문은 날짜를 몰라 쓰지 않는다', async () => {
    const utc = envelope({ rows: [] }, { generated_at: '2026-10-06T16:30:00Z' }); // KST 2026-10-07 01:30
    const { fetch } = pages({ 'filings/feed.json': utc, 'board/newhigh.json': { code: 'no_data', message: '아직 없음' } });
    const src = createSnapshotSource({ fetch });
    await expect(src.get('filings/feed', { date: '2026-10-07' })).resolves.toBeTruthy();
    expect((await failure(src.get('filings/feed', { date: '2026-10-06' }))).message).toBe(MISSING);
    expect((await failure(src.get('board/newhigh', { date: '2026-10-07' }))).message).toBe(MISSING);
  });

  it('없는 보기: 날짜 밖 인자는 빼지 않는다', async () => {
    const { fetch, calls } = pages({ 'etf/flows.json': envelope([]) });
    const err = await failure(createSnapshotSource({ fetch }).get('etf/flows', { mode: 'out' }));
    expect(err.message).toBe(MISSING);
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
    const err = await failure(createSnapshotSource({ fetch }).get('board/us'));
    expect(err.serverMessage).toBe('아직 없음 — rules.close 16:00');
  });

  it('봉투가 아니면 그리지 않는다(원천 없는 숫자 금지)', async () => {
    const { fetch } = fakeFetch(() => json({ v: 1 }));
    await expect(createSnapshotSource({ fetch }).get('market/summary')).rejects.toThrow('원천');
  });

  it('잘못된 경로는 요청하지 않는다', async () => {
    const { fetch, calls } = fakeFetch(() => json(envelope(1)));
    await expect(createSnapshotSource({ fetch }).get('../secret')).rejects.toThrow('잘못된 경로');
    expect(calls).toHaveLength(0);
  });
});

describe('snapshot source — 전 종목 검색·상세 없음', () => {
  it('검색: 전 종목 목록을 한 번 받아 서버와 같은 순위(코드 일치 > 코드 앞부분 > 이름 앞부분 > 이름 포함, 같으면 목록 순)', async () => {
    const { fetch, calls } = pages(SITE_FILES);
    const src = createSnapshotSource({ fetch });
    type Hit = { q: string; n_total: number; items: { code: string; detail?: boolean }[] };
    const codes = async (q: string, limit?: number): Promise<string[]> =>
      (await src.get<Hit>('stock/search', { q, ...(limit ? { limit } : {}) })).data.items.map((i) => i.code);
    // 이름 앞부분 셋(목록 = 시총 순) → 이름 포함 둘(목록 순)
    expect(await codes('모의')).toEqual(['994010', '994020', '994015', '99K170', '994030']);
    expect(await codes('모의전자')).toEqual(['994010', '994015', '994030']); // 앞부분 둘 → 포함 하나
    expect(await codes('99401')).toEqual(['994010', '994015']); // 코드 앞부분
    expect(await codes('994015')).toEqual(['994015']); // 코드 일치
    expect(await codes('99k')).toEqual(['99K170']); // 영문 대소문자 무시
    expect(await codes('모의', 1)).toEqual(['994010']);
    const all = await src.get<Hit>('stock/search', { q: ' 모의 ', limit: 1 });
    expect(all.data.q).toBe('모의'); // 서버처럼 앞뒤 공백만 뺀 입력
    expect(all.data.n_total).toBe(5); // 잘리기 전 맞은 수
    expect(all.data.items[0]?.detail).toBe(true);
    expect(all.source).toBe('KRX+DART');
    expect(files(calls)).toEqual(['stock/search-index.json']);
  });

  it('상세 없는 종목: 목록이 있으면 요청 없이 NO_DETAIL, 전 종목에 없는 코드는 serverMessage 없는 404(모르는 코드)', async () => {
    const { fetch, calls } = pages(SITE_FILES);
    const src = createSnapshotSource({ fetch });
    await src.get('stock/search', { q: '모의' });
    const n = calls.length;
    for (const key of ['stock/994015/header', 'stock/994015/chart', 'flows/stock/994015', 'etf/holders/994015', 'trade/stock/994015/flows']) {
      const err = await failure(src.get(key));
      expect(err.serverMessage, key).toBe(NO_DETAIL);
      expect(err.status).toBe(404);
    }
    const unknown = await failure(src.get('stock/123456/header'));
    expect(unknown.status).toBe(404);
    expect(unknown.serverMessage).toBeNull();
    expect(calls).toHaveLength(n); // 네트워크 없음
  });

  it('상세 없는 종목을 주소로 바로 열면: 첫 404 뒤에 목록을 받아 판정하고 기억한다. 상세가 있는 종목은 목록을 받지 않는다', async () => {
    const { fetch, calls } = pages(SITE_FILES);
    const src = createSnapshotSource({ fetch });
    await src.get('stock/994010/header');
    expect(files(calls)).toEqual(['stock/994010/header.json']);
    const err = await failure(src.get('stock/994015/header'));
    expect(err.serverMessage).toBe(NO_DETAIL);
    expect(files(calls)).toEqual(['stock/994010/header.json', 'stock/994015/header.json', 'stock/search-index.json']);
    await failure(src.get('stock/994015/valuation'));
    expect(calls).toHaveLength(3);
  });

  it('옛 목록(detail·complete 없음 — 상세 종목만)이면 판정하지 않고 없는 보기', async () => {
    const old = envelope({ q: '', n_total: 1, items: [{ code: '994010', name: '모의전자', is_watch: false }] });
    const { fetch } = pages({ 'stock/search-index.json': old });
    const src = createSnapshotSource({ fetch });
    await src.get('stock/search', { q: '모의' });
    const err = await failure(src.get('stock/994015/header'));
    expect(err.message).toBe(MISSING);
  });
});

describe('snapshot source — 관심종목(이 브라우저 localStorage)', () => {
  const mk = (init: Record<string, string> = {}, st: Record<string, number> = {}) => {
    const storage = memoryStorage(init);
    const { fetch, calls } = pages(SITE_FILES, st);
    const src = createSnapshotSource({ fetch, storage, now: fixedClock() });
    return { storage, calls, src };
  };
  type WL = { list_name: string; n: number; items: { code: string; name: string | null; close: number | null; quality: string | null; source: string; sort: number; added_at: string | null; kind: string | null }[] };

  it('빈 목록: 요청 없이 빈 봉투 + 안내(이 브라우저 · 지금)', async () => {
    const { src, calls } = mk();
    const env = await src.get<WL>('watchlist');
    expect(env.data).toEqual({ list_name: 'main', n: 0, items: [] });
    expect(env.notes).toEqual([WATCH_EMPTY]);
    expect(env.source).toBe('이 브라우저');
    expect(env.quality).toBe('ok');
    expect(env.as_of).toBe(FIXED_NOW.toISOString());
    expect(env.generated_at).toBe(FIXED_NOW.toISOString());
    expect(calls).toHaveLength(0);
    await expect(src.get('watchlist', { list: 'other' })).rejects.toThrow(MISSING);
  });

  it('put: 목록 끝에 더하고(이름·시장·종류는 전 종목 목록에서) 같은 종목은 한 번만 — 시세는 올라간 머리 파일 그대로', async () => {
    const { src, storage } = mk({ watch: '[{"c":"1"}]', theme: 'dark' });
    await src.put('watchlist/main/994020');
    await src.put('watchlist/main/994010');
    const env = await src.put<WL>('watchlist/main/994020'); // 멱등
    const saved = JSON.parse(storage.getItem(WATCH_STORAGE_KEY) ?? 'null') as { v: number; main: { code: string; name: string; market: string; kind: string; added_at: string }[] };
    expect(saved.v).toBe(1);
    expect(saved.main.map((i) => i.code)).toEqual(['994020', '994010']);
    expect(saved.main[0]).toEqual({ code: '994020', name: '모의바이오', market: 'KOSDAQ', kind: 'common', added_at: FIXED_NOW.toISOString() });
    // Beom's Dashboard(같은 출처)의 키는 건드리지 않는다
    expect(storage.getItem('watch')).toBe('[{"c":"1"}]');
    expect(storage.getItem('theme')).toBe('dark');
    // 돌려주는 목록 = GET 과 같은 모양 — 시세는 머리 파일(원천·품질 그대로), 봉투 품질은 가장 나쁜 것
    expect(env.data.items.map((i) => [i.code, i.sort, i.close, i.quality, i.source])).toEqual([
      ['994020', 0, 5000, 'estimated', 'browser'],
      ['994010', 1, 71000, 'ok', 'browser'],
    ]);
    expect(env.source).toBe('이 브라우저 · KIS+KRX');
    expect(env.quality).toBe('estimated');
    expect(env.as_of).toBe('2026-10-06T15:30:00+09:00'); // 머리 봉투 as_of 중 가장 늦은 것
    expect(env.notes).toEqual([]);
  });

  it('del: 목록에서 빼고, 없는 종목은 서버처럼 거절(404)', async () => {
    const { src, storage } = mk();
    await src.put('watchlist/main/994010');
    const env = await src.del<WL>('watchlist/main/994010');
    expect(env.data.items).toEqual([]);
    expect(JSON.parse(storage.getItem(WATCH_STORAGE_KEY) ?? 'null')).toEqual({ v: 1, main: [] });
    const err = await failure(src.del('watchlist/main/994010'));
    expect(err.status).toBe(404);
    expect(err.message).toBe('관심종목 쓰기 거절');
  });

  it('관심종목 main 한 종목 밖의 쓰기 키는 저장소·네트워크를 건드리지 않고 거절(405)', async () => {
    const { src, storage, calls } = mk();
    for (const key of ['rules/r1', 'journal/trades', 'backtest/run', 'watchlist/other/994010', 'watchlist/main/../x', 'watchlist/main', 'watchlist/main/994010/x']) {
      const a = await failure(src.put(key));
      const b = await failure(src.del(key));
      expect([a.status, b.status], key).toEqual([405, 405]);
      expect(a.message).toBe(READ_ONLY);
    }
    expect(storage.length).toBe(0);
    expect(calls).toHaveLength(0);
  });

  it('머리·검색의 is_watch 를 이 브라우저 목록으로 덧씌운다(파일은 늘 false)', async () => {
    const { src } = mk();
    type H = { is_watch: boolean };
    expect((await src.get<H>('stock/994010/header')).data.is_watch).toBe(false);
    await src.put('watchlist/main/994010');
    expect((await src.get<H>('stock/994010/header')).data.is_watch).toBe(true);
    expect((await src.get<H>('stock/994020/header')).data.is_watch).toBe(false);
    const hits = await src.get<{ items: { code: string; is_watch: boolean }[] }>('stock/search', { q: '모의', limit: 3 });
    expect(hits.data.items.map((i) => [i.code, i.is_watch])).toEqual([
      ['994010', true],
      ['994020', false],
      ['994015', false],
    ]);
  });

  it('상세 없는 종목도 담는다 — 시세 없음(머리 요청 없음)은 notes 에 수', async () => {
    const { src, calls } = mk();
    await src.get('stock/search', { q: '모의' });
    const env = await src.put<WL>('watchlist/main/994015');
    expect(env.data.items[0]).toMatchObject({ code: '994015', name: '모의전자우', kind: 'pref', close: null, quality: null });
    expect(env.notes).toEqual(['시세 없는 종목 1개(공개 사이트에 상세가 없거나 시세 없음)']);
    expect(env.source).toBe('이 브라우저');
    expect(files(calls)).not.toContain('stock/994015/header.json');
    // 전 종목에 없는 코드는 서버(unknown_code)처럼 거절
    expect((await failure(src.put('watchlist/main/123456'))).status).toBe(404);
  });

  it('넣을 때 종목을 확인하지 못하면(목록·머리 모두 못 읽음) 넣지 않고 사유를 던진다 — 머리가 있으면 그 이름으로', async () => {
    const { src, storage } = mk({}, { 'stock/search-index.json': 503 });
    const err = await failure(src.put('watchlist/main/994015')); // 상세 없는 종목 — 머리 파일도 없다
    expect(err.status).toBe(503);
    expect(storage.getItem(WATCH_STORAGE_KEY)).toBeNull();
    await src.put('watchlist/main/994010');
    const saved = JSON.parse(storage.getItem(WATCH_STORAGE_KEY) ?? 'null') as { main: { code: string; name: string }[] };
    expect(saved.main).toEqual([expect.objectContaining({ code: '994010', name: '모의전자', market: 'KOSPI' })]);
  });

  it('머리 파일을 못 읽으면(404 밖) 삼키지 않고 notes·품질(stale)에 남긴다', async () => {
    const { src } = mk({ [WATCH_STORAGE_KEY]: JSON.stringify({ v: 1, main: [{ code: '994010', name: '모의전자' }, { code: '994020' }] }) }, { 'stock/994020/header.json': 503 });
    const env = await src.get<WL>('watchlist');
    expect(env.data.items.map((i) => i.close)).toEqual([71000, null]);
    expect(env.notes).toEqual(['1개 시세를 읽지 못함']);
    expect(env.quality).toBe('stale');
  });

  it('저장값이 깨졌으면 빈 목록 + 안내, 덮어쓰지 않는다(409)', async () => {
    for (const bad of ['{', '[]', '{"v":2,"main":[]}', '{"v":1,"main":[{"code":"5930"}]}', '{"v":1,"main":"x"}']) {
      const { src, storage } = mk({ [WATCH_STORAGE_KEY]: bad });
      const env = await src.get<WL>('watchlist');
      expect(env.data.items, bad).toEqual([]);
      expect(env.notes).toEqual([WATCH_BROKEN]);
      const err = await failure(src.put('watchlist/main/994010'));
      expect(err.status).toBe(409);
      expect(storage.getItem(WATCH_STORAGE_KEY)).toBe(bad);
    }
  });

  it('읽기: 같은 코드는 처음 것만, 상한을 넘은 저장값은 깨진 것으로', () => {
    expect(parseWatch('{"v":1,"main":[{"code":"994010"},{"code":"994010","name":"x"}]}')?.map((i) => i.name)).toEqual([null]);
    const many = { v: 1, main: Array.from({ length: WATCH_MAX + 1 }, (_, i) => ({ code: String(100000 + i) })) };
    expect(parseWatch(JSON.stringify(many))).toBeNull();
  });

  it('상한(WATCH_MAX)을 넘겨 더하지 않는다(422)', async () => {
    const full = { v: 1, main: Array.from({ length: WATCH_MAX }, (_, i) => ({ code: String(100000 + i) })) };
    const { src } = mk({ [WATCH_STORAGE_KEY]: JSON.stringify(full) });
    expect((await failure(src.put('watchlist/main/994010'))).status).toBe(422);
  });

  it('저장소가 막혔으면(setItem 실패·저장소 없음) 별 옆 사유 — 이 브라우저에 저장하지 못함', async () => {
    const blocked: Pick<Storage, 'getItem' | 'setItem'> = {
      getItem: () => null,
      setItem: () => {
        throw new Error('QuotaExceededError');
      },
    };
    const { fetch } = pages(SITE_FILES);
    const a = createSnapshotSource({ fetch, storage: blocked, now: fixedClock() });
    expect((await failure(a.put('watchlist/main/994010'))).message).toBe(WATCH_SAVE_FAILED);
    const b = createSnapshotSource({ fetch, storage: null });
    expect((await failure(b.put('watchlist/main/994010'))).message).toBe(WATCH_SAVE_FAILED);
    expect((await b.get<WL>('watchlist')).notes).toEqual([WATCH_EMPTY]);
    const throwing: Pick<Storage, 'getItem' | 'setItem'> = {
      getItem: () => {
        throw new Error('SecurityError');
      },
      setItem: () => undefined,
    };
    const c = createSnapshotSource({ fetch, storage: throwing });
    expect((await c.get<WL>('watchlist')).notes).toEqual([WATCH_BROKEN]);
  });
});

describe('bootSite', () => {
  it('페이지 1~10 만 붙이고 로그아웃·로그인 화면 없이, 배지는 스냅샷', () => {
    const root = appRoot();
    const { fetch } = fakeFetch(() => json(envelope({})));
    const app = bootSite(root, { fetch, now: fixedClock(), ribbon: null, loaders: fakeLoaders({}), storage: memoryStorage() });
    const ids = [...root.querySelectorAll('section.page')].map((s) => s.id);
    expect(ids).toEqual(SITE_PAGES.map((n) => `page-${n}`));
    expect(root.querySelector('form input[type="password"]')).toBeNull();
    expect([...root.querySelectorAll('button')].map((b) => b.textContent)).not.toContain('로그아웃');
    expect(root.querySelector('.badge')?.textContent).toBe('스냅샷');
    expect(app.ctx.tier).toBe('login');
    app.destroy();
  });

  it('관심종목은 넘긴 저장소(스킨과 같은 것)와 시계로 — 다시 열어도 그대로', async () => {
    const storage = memoryStorage();
    const { fetch } = pages(SITE_FILES);
    const a = bootSite(appRoot(), { fetch, now: fixedClock(), ribbon: null, loaders: fakeLoaders({}), storage });
    await watchWriter(a.ctx.source)?.put('watchlist/main/994010');
    a.destroy();
    const b = bootSite(appRoot(), { fetch, now: fixedClock(), ribbon: null, loaders: fakeLoaders({}), storage });
    const env = await b.ctx.source.get<{ items: { code: string; added_at: string | null }[] }>('watchlist');
    expect(env.data.items).toEqual([expect.objectContaining({ code: '994010', added_at: FIXED_NOW.toISOString() })]);
    b.destroy();
  });
});
