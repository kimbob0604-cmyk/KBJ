// P6 완료 시험(docs/p6_design.md §0.3·§8 — 웨이브 3 WRAP): 페이지 7(수출입) 탭 6개 × 공개·로그인과 페이지 8 '수출 겹쳐 보기'
// 패널이 합성 데이터(p6_world 로 만든 웹 픽스처 — test/fixtures/api/trade_{flash,explore,map}/**, 실데이터 아님)로 그려진다.
// - 탭 6 × 등급 2: console.error 0, 오류 줄 0, DOM 글자에 NaN·undefined·Infinity 0. 공개는 로그인 API 0회(공개 파일만).
// - CSV 버튼: BOM·CRLF·RFC 4180 이스케이프(큰따옴표 짝)인 본문을 만든다.
// - 페이지 8: 관심종목 50 = 상태별 합성 픽스처 5개(ok 2·no_trade·no_revenue·short_overlap)를 종목코드만 바꿔 50번 그린다
//   (50종목의 실제 처리기 값·상태 행렬은 백엔드 완료 시험 tests/acceptance/test_trade_map_watchlist50.py 가 본다).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createApiSource } from '../../src/data/api';
import type { Tier } from '../../src/data/source';
import { createStaticSource } from '../../src/data/static';
import page, { TRADE_TABS } from '../../src/pages/p7_trade';
import { memoGet, type TabCtx } from '../../src/pages/p8/common';
import { mount as mountOverlay, TITLE as OVERLAY } from '../../src/pages/p8/trade_overlay';
import { mount as mountPrice, TITLE as PRICE } from '../../src/pages/p8/trade_overlay_price';
import { BOM } from '../../src/ui/csv';
import { fakeFetch, json } from '../helpers';
import { loginFixtureName as exploreName } from '../fixtures/api/trade_explore/harness';
import { flush, pageCtx, panelByTitle, text } from '../pages/support';

const FX = import.meta.glob<unknown>('../fixtures/api/trade_{flash,explore,map}/**/*.json', { eager: true, import: 'default' });
const NOW = new Date('2026-12-11T02:30:00Z'); // p6_world CLOCK(2026-12-11 11:30 KST)
const NO_DATA = (): Response => json({ code: 'no_data', message: '아직 없음' }, 404);

function fx(rel: string): unknown {
  const v = FX[`../fixtures/api/${rel}.json`];
  return v === undefined ? undefined : structuredClone(v);
}

/** 로그인 API — 속보(trade/flash·market/ribbon)·탐색(trade/items…)·매핑(trade/stocks·trade/stock/<코드>) 픽스처 */
function loginResponse(url: string, stockAs: (code: string) => string | null): Response {
  const path = url.replace(/^\/api\//, '');
  const p = path.split('?')[0] ?? '';
  if (p === 'market/ribbon') return json(fx('trade_flash/market_ribbon'));
  const fl = /^trade\/flash\?(?:.*&)?kind=([a-z_]+)/.exec(path);
  if (fl?.[1]) return json(fx(`trade_flash/flash_${fl[1]}`));
  if (p === 'trade/stocks') return json(fx('trade_map/stocks'));
  const st = /^trade\/stock\/([0-9A-Z]{6})$/.exec(p);
  if (st?.[1]) {
    const name = stockAs(st[1]);
    const body = name === null ? undefined : (fx(`trade_map/${name}`) as { data: { code: string } } | undefined);
    if (body === undefined) return json(fx('trade_map/no_map_994010'), 404);
    body.data.code = st[1];
    return json(body);
  }
  if (/^stock\/[0-9A-Z]{6}\/chart$/.test(p)) return NO_DATA(); // 월말 종가 줄 — 시세는 P3 픽스처 몫(여기서는 안내 줄)
  const name = exploreName(path);
  const v = name === null ? undefined : fx(`trade_explore/${name}`);
  return v === undefined ? NO_DATA() : json(v);
}

/** 공개 파일 — ./data/<이름>.json → trade_{flash,explore,map}/public/<이름>.json(없으면 404) */
function publicResponse(url: string): Response {
  const name = url.replace(/^\.\/data\//, '').replace(/\.json$/, '');
  for (const d of ['trade_flash', 'trade_explore', 'trade_map']) {
    const v = fx(`${d}/public/${name}`);
    if (v !== undefined) return json(v);
  }
  return json({}, 404);
}

function harness(tier: Tier, stockAs: (code: string) => string | null = () => null) {
  const { fetch, calls } = fakeFetch((url) => (tier === 'login' ? loginResponse(url, stockAs) : publicResponse(url)));
  const source = tier === 'login' ? createApiSource({ fetch }) : createStaticSource({ fetch });
  return { source, urls: () => calls.map((c) => c.url) };
}

function badWords(el: Element): string[] {
  return el.textContent.match(/\b(NaN|undefined|-?Infinity)\b/g) ?? [];
}

let errors: unknown[][] = [];
beforeEach(() => {
  errors = [];
  vi.spyOn(console, 'error').mockImplementation((...a: unknown[]) => {
    errors.push(a);
  });
});
afterEach(() => {
  vi.restoreAllMocks();
  document.body.replaceChildren();
});

/** jsdom Blob 에는 text() 가 없다 — FileReader 로(BOM 을 지우지 않게 바이트로 읽어 ignoreBOM 으로 푼다) */
function readBlob(b: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => {
      resolve(r.result instanceof ArrayBuffer ? new TextDecoder('utf-8', { ignoreBOM: true }).decode(r.result) : '');
    };
    r.onerror = () => {
      reject(new Error('CSV 읽기 실패'));
    };
    r.readAsArrayBuffer(b);
  });
}

async function mountP7(tier: Tier) {
  const hz = harness(tier);
  const { ctx, root } = pageCtx(7, tier, hz.source, { now: () => NOW });
  await page.mount(ctx);
  await flush(10);
  return { hz, root };
}

async function openTab(root: HTMLElement, label: string): Promise<void> {
  const tab = [...root.querySelectorAll<HTMLElement>('[role="tab"]')].find((x) => x.textContent.trim() === label);
  if (!tab) throw new Error(`탭 없음: ${label}`);
  tab.click();
  await flush(10);
}

describe('페이지 7 — 탭 6 × 등급 2', () => {
  for (const tier of ['login', 'public'] as const) {
    it(`${tier}: 탭마다 오류 0·NaN·undefined 0`, async () => {
      const { hz, root } = await mountP7(tier);
      expect(TRADE_TABS.map((t) => t.label)).toEqual(['월별 추이', '속보', '품목', '국가', '지역', '급등', '104품목']);
      for (const t of TRADE_TABS) {
        await openTab(root, t.label);
        const panel = root.querySelector('[role="tabpanel"]:not([hidden])') ?? root;
        expect(panel.querySelectorAll('section.panel').length, t.label).toBeGreaterThan(0);
        expect(root.querySelectorAll('.msg.err').length, t.label).toBe(0);
        expect(badWords(root), t.label).toEqual([]);
      }
      expect(errors).toEqual([]);
      if (tier === 'public') expect(hz.urls().every((u) => u.startsWith('./data/'))).toBe(true);
      else expect(hz.urls().every((u) => u.startsWith('/api/'))).toBe(true);
    });
  }

  it('CSV 버튼 — BOM·CRLF·큰따옴표 짝(이스케이프)', async () => {
    const blobs: Blob[] = [];
    const u = URL as unknown as { createObjectURL?: (b: Blob) => string; revokeObjectURL?: (s: string) => void };
    u.createObjectURL = (b: Blob) => {
      blobs.push(b);
      return 'blob:kbj-test';
    };
    u.revokeObjectURL = () => undefined;
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    const { root } = await mountP7('login');
    for (const t of TRADE_TABS) {
      await openTab(root, t.label);
      for (const b of root.querySelectorAll<HTMLButtonElement>('button')) {
        if (b.textContent.trim() === 'CSV') b.click();
      }
    }
    expect(blobs.length).toBeGreaterThanOrEqual(TRADE_TABS.length);
    for (const b of blobs) {
      const body = await readBlob(b);
      expect(body.startsWith(BOM)).toBe(true);
      expect(body).toContain('\r\n');
      for (const line of body.slice(1).split('\r\n')) expect((line.match(/"/g) ?? []).length % 2).toBe(0);
      expect(body).not.toMatch(/NaN|undefined|Infinity/);
    }
  });
});

// ── 페이지 8 '수출 겹쳐 보기' × 관심종목 50 ─────────────────────────────────────────────────────

/** 상태별 합성 픽스처(p6_world) — ok 2·관세청 자료 없음·매출 없음·겹침 부족 */
const TEMPLATES = ['stock_992004', 'stock_992011', 'stock_992060', 'stock_994015', 'stock_992059'] as const;
const WATCH50 = Array.from({ length: 50 }, (_, i) => `99${String(5001 + i)}`);

function tabCtx(tier: Tier, code: string, source: TabCtx['source']): TabCtx {
  return {
    tier,
    code,
    source,
    now: () => NOW,
    get: memoGet(source),
    pub: () => Promise.resolve(null),
    corp: () => Promise.resolve(null),
    open: () => undefined,
  };
}

describe('페이지 8 — 수출 겹쳐 보기 × 관심종목 50(로그인)', () => {
  it('50종목 모두 패널(매핑 50/50) — 오류 0·NaN 0·상태 안내', async () => {
    const as = (code: string): string => TEMPLATES[WATCH50.indexOf(code) % TEMPLATES.length] ?? 'stock_992004';
    const hz = harness('login', as);
    const seen = new Set<string>();
    for (const code of WATCH50) {
      const el = document.createElement('div');
      document.body.append(el);
      const t = tabCtx('login', code, hz.source);
      await mountOverlay(t, el);
      await mountPrice(t, el);
      await flush();
      const p = panelByTitle(el, OVERLAY);
      expect(text(p), code).not.toContain('매핑 없음');
      expect(p.querySelectorAll('.msg.err').length, code).toBe(0);
      expect(badWords(el), code).toEqual([]);
      expect(panelByTitle(el, PRICE)).toBeTruthy();
      seen.add(as(code));
    }
    expect(seen.size).toBe(TEMPLATES.length);
    expect(errors).toEqual([]);
    const stockCalls = hz.urls().filter((u) => u.startsWith('/api/trade/stock/'));
    expect(new Set(stockCalls).size).toBe(50);
  });
});
