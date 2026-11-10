// 페이지 8 — 공개 사이트(정적 스냅샷 소스, 등급 login): 전 종목 검색의 '상세 없음' 꼬리표, 상세 없는 종목은 가벼운 머리
// (이름·시장·종류·안내·별 — 탭·헛요청 없음), 이 브라우저 관심종목(칩·별·빈 목록 안내). 로그인판 동작은 p8_stock.test.ts.
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { route } from '../../src/app/route';
import { createSnapshotSource, WATCH_EMPTY, WATCH_STORAGE_KEY } from '../../src/data/snapshot';
import { NO_DETAIL } from '../../src/data/source';
import { STAR_OFF, STAR_ON } from '../../src/pages/p8/header';
import page, { settled } from '../../src/pages/p8_stock';
import { envelope, fakeFetch, fixedClock, json, memoryStorage } from '../helpers';
import { p4PageCtx } from '../acceptance/support_p4';
import { click, flush, panelByTitle, text } from './support';

const ITEMS = [
  { code: '994010', name: '모의전자', market: 'KOSPI', kind: 'common', corp_code: '99400010', is_watch: false, detail: true },
  { code: '994015', name: '모의전자우', market: 'KOSPI', kind: 'pref', corp_code: '99400010', is_watch: false, detail: false },
  { code: '99K170', name: '모의케이', market: 'KOSDAQ', kind: 'etf', corp_code: null, is_watch: false, detail: true },
];
const FILES: Record<string, unknown> = {
  'stock/search-index.json': envelope({ q: '', n_total: ITEMS.length, complete: true, items: ITEMS }, { source: 'KRX+DART' }),
  'stock/994010/header.json': envelope(
    {
      code: '994010', name: '모의전자', market: 'KOSPI', kind: 'common', common_code: '994010', corp_code: '99400010',
      corp_name: '모의전자', status_flags: null, listed_on: '2001-01-02', acc_mt: 12, induty_code: null, last: null,
      is_watch: false, detail_tier: true, newhigh: null,
    },
    { as_of: '2026-10-06T15:30:00+09:00' },
  ), // prettier-ignore
};

function site(storage: Storage = memoryStorage()) {
  const { fetch, calls } = fakeFetch((url) => {
    const name = url.replace('./data/', '');
    if (name in FILES) return json(FILES[name]);
    // 상세 종목(994010)의 파일은 내보내기가 다 만든다(여기서는 내용 없는 no_data 본문) — 그 밖은 없음
    if (name.includes('994010')) return json({ code: 'no_data', message: '아직 없음 — 시험' });
    return json({}, 404);
  });
  const source = createSnapshotSource({ fetch, storage, now: fixedClock() });
  return { source, storage, files: () => calls.map((c) => c.url.replace('./data/', '')) };
}

async function mount8(code: string | null, s = site()) {
  if (code) route.setCode(code);
  const { ctx, root } = p4PageCtx(8, 'login', s.source);
  const cleanup = (await page.mount(ctx)) as () => void;
  return { root, cleanup, ...s };
}

/** 검색 상자에 쳐서 후보 목록이 그려질 때까지(상자는 150ms 뒤에 찾는다) */
async function typeSearch(root: HTMLElement, q: string): Promise<HTMLElement[]> {
  const input = root.querySelector<HTMLInputElement>('input[role="combobox"]');
  if (!input) throw new Error('검색 상자 없음');
  input.value = q;
  input.dispatchEvent(new Event('input'));
  await new Promise((r) => setTimeout(r, 200));
  await flush();
  return [...root.querySelectorAll<HTMLElement>('li[role="option"]')];
}

beforeEach(() => {
  route.reset();
});
afterEach(() => {
  route.reset();
});

describe('페이지 8 — 공개 사이트(스냅샷)', () => {
  it('관심종목이 없으면 이 브라우저 안내(☆ 관심 추가) — 가져오기(watchlist import) 안내가 아니다', async () => {
    const { root, files } = await mount8(null);
    const chips = text(root.querySelector('[data-section="watchlist"]'));
    expect(chips).toBe(WATCH_EMPTY);
    expect(chips).not.toContain('watchlist import');
    expect(text(panelByTitle(root, '종목'))).toContain('종목을 검색하세요');
    expect(files()).toEqual([]); // 빈 목록은 요청 없음
  });

  it("검색 목록: 상세 없는 종목은 '상세 없음' 꼬리표(종류 꼬리표보다 먼저), 상세가 있으면 종류 꼬리표 그대로", async () => {
    const { root } = await mount8(null);
    const opts = await typeSearch(root, '모의');
    const tags = opts.map((li) => [li.dataset.code, text(li.querySelector('.tag'))]);
    expect(tags).toEqual([
      ['994010', ''],
      ['994015', '상세 없음'],
      ['99K170', 'ETF'],
    ]);
  });

  it('상세 없는 종목을 검색에서 고르면: 요청 없이 가벼운 머리(이름·시장·종류·안내·별)만 — 탭을 지운다', async () => {
    const { root, files } = await mount8(null);
    const opts = await typeSearch(root, '모의전자우');
    // 후보는 mousedown 으로 고른다(입력 칸 blur 보다 먼저 — ui/search.ts)
    opts.find((li) => li.dataset.code === '994015')?.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
    await flush();
    await settled(root);
    expect(route.getCode()).toBe('994015');
    const head = panelByTitle(root, '종목');
    expect(text(head)).toContain('모의전자우');
    expect(text(head)).toContain('994015');
    expect(text(head)).toContain('우선주');
    expect(text(head.querySelector('[data-section="no-detail"]'))).toBe(NO_DETAIL);
    expect(head.querySelector('.watch-star button')?.textContent).toBe(STAR_OFF);
    expect(root.querySelector('[role="tablist"]')).toBeNull();
    expect(root.querySelectorAll('.msg.err')).toHaveLength(0);
    expect(files()).toEqual(['stock/search-index.json']); // 그 종목 파일은 하나도 요청하지 않는다
  });

  it('상세 없는 종목을 주소로 바로 열면: 첫 404 뒤에 목록으로 판정하고 기억한다 — 다시 열면 요청 없음', async () => {
    const { root, files } = await mount8('994015');
    await settled(root);
    expect(text(panelByTitle(root, '종목'))).toContain(NO_DETAIL);
    expect(root.querySelector('[role="tablist"]')).toBeNull();
    expect(files().filter((f) => f === 'stock/search-index.json')).toHaveLength(1);
    const n = files().length;
    route.setCode('994010');
    await settled(root);
    route.setCode('994015');
    await settled(root);
    expect(text(panelByTitle(root, '종목'))).toContain(NO_DETAIL);
    expect(files().slice(n).filter((f) => f.includes('994015'))).toEqual([]);
  });

  it('상세 없는 종목도 별로 이 브라우저에 담는다 — 다시 열면 관심종목 칩', async () => {
    const s = site();
    const { root, cleanup } = await mount8('994015', s);
    await settled(root);
    click(root.querySelector('.watch-star button'));
    await flush();
    expect(root.querySelector('.watch-star button')?.textContent).toBe(STAR_ON);
    const saved = JSON.parse(s.storage.getItem(WATCH_STORAGE_KEY) ?? 'null') as { main: { code: string; name: string }[] };
    expect(saved.main.map((i) => [i.code, i.name])).toEqual([['994015', '모의전자우']]);
    cleanup();
    route.reset();
    const again = await mount8(null, s);
    expect(route.getCode()).toBe('994015'); // 코드 없이 열면 첫 관심종목
    const chip = again.root.querySelector('[data-section="watchlist"] button');
    expect(chip?.textContent).toBe('모의전자우');
  });

  it('상세가 있는 종목: 머리·탭 그대로, 별 처음 상태·관심 배지는 이 브라우저 목록 — 목록 파일은 받지 않는다', async () => {
    const storage = memoryStorage({ [WATCH_STORAGE_KEY]: JSON.stringify({ v: 1, main: [{ code: '994010', name: '모의전자' }] }) });
    const { root, files } = await mount8('994010', site(storage));
    await settled(root);
    const head = panelByTitle(root, '종목');
    expect(head.querySelector('.watch-star button')?.textContent).toBe(STAR_ON);
    expect(text(head)).toContain('관심');
    expect(root.querySelector('[role="tablist"]')).not.toBeNull();
    expect(files()).not.toContain('stock/search-index.json');
  });
});
