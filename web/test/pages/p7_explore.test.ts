// 페이지 7 탐색 탭(EXP — docs/p6_design.md §1.4·§6, metrics §26·§27·§29): 품목(HS2→4→6, HS10 로그인)·국가·지역·급등·
// 104품목. 로그인은 API, 공개는 공개 파일(로그인 API 0회) — 같은 모양으로 그린다. 합성 픽스처(p6_world)만.
import { describe, expect, it } from 'vitest';
import page from '../../src/pages/p7_trade';
import type { PubTree, TradeItems, TradeItemSeries } from '../../src/pages/p7/common';
import { TITLE as COUNTRIES_TITLE, ITEMS_TITLE, countriesCsv } from '../../src/pages/p7/countries';
import { HS10_NOTE, itemsCsv, pubNodes, pubPoints, SERIES_TITLE, TITLE as ITEMS } from '../../src/pages/p7/items';
import { AUTO_TAG, TITLE as ITEMS104 } from '../../src/pages/p7/items104';
import { TITLE as REGIONS } from '../../src/pages/p7/regions';
import { surgeRows, TITLE as SURGE } from '../../src/pages/p7/surge';
import { BOM } from '../../src/ui/csv';
import { json } from '../helpers';
import {
  EXPLORE_NOW,
  exploreFixture,
  exploreLoginHarness,
  explorePublicHarness,
  loginFixtureName,
} from '../fixtures/api/trade_explore/harness';
import { buttonByText, flush, pageCtx, panelByTitle, text } from './support';

type Over = Record<string, (() => Response) | undefined>;

async function mount(tier: 'login' | 'public', over: Over = {}) {
  const hz = tier === 'login' ? exploreLoginHarness(over) : explorePublicHarness(over);
  const { ctx, root } = pageCtx(7, tier, hz.source, { now: () => EXPLORE_NOW });
  await page.mount(ctx);
  await flush();
  return { hz, root };
}

async function openTab(root: HTMLElement, label: string): Promise<void> {
  const tab = [...root.querySelectorAll<HTMLElement>('[role="tab"]')].find((x) => x.textContent.trim() === label);
  if (!tab) throw new Error(`탭 없음: ${label}`);
  tab.click();
  await flush(10);
}

const rows = (el: ParentNode): HTMLTableRowElement[] => [...el.querySelectorAll<HTMLTableRowElement>('tbody tr')];
const first = (r: HTMLTableRowElement): string => text(r.cells[0]);
function pick(el: ParentNode, hs: string): void {
  const r = rows(el).find((x) => first(x) === hs);
  if (!r) throw new Error(`행 없음: ${hs}`);
  r.click();
}

function clean(el: Element): void {
  expect(text(el)).not.toMatch(/NaN|undefined|null/);
}

interface Env<T> {
  quality: 'ok' | 'stale' | 'estimated' | 'invalid';
  data: T;
}

describe('픽스처 이름', () => {
  it('API 경로 → 파일', () => {
    expect(loginFixtureName('trade/items?flow=exp&level=2')).toBe('items_l2_exp');
    expect(loginFixtureName('trade/items?flow=exp&level=4&parent=85')).toBe('items_l4_85');
    expect(loginFixtureName('trade/items/8502/series?flow=exp')).toBe('series_8502');
    expect(loginFixtureName('trade/countries/US/items?flow=exp')).toBe('country_items_US');
    expect(loginFixtureName('trade/nope')).toBeNull();
  });
});

describe('페이지 7 품목 — 로그인', () => {
  it('HS2 한 층(트리맵·표 12행) → 행을 고르면 추이와 HS4 로 내려간다 → HS10 까지', async () => {
    const { hz, root } = await mount('login');
    await openTab(root, '품목');
    expect(hz.paths()).toContain('trade/items?flow=exp&level=2');
    const p = panelByTitle(root, ITEMS);
    expect(p.querySelector('.treemap, svg')).not.toBeNull();
    expect(rows(p)).toHaveLength(12);
    expect(text(p.querySelector('.src'))).toContain('관세청');
    clean(p);
    pick(p, '85');
    await flush(10);
    expect(hz.paths()).toContain('trade/items/85/series?flow=exp');
    expect(hz.paths()).toContain('trade/items?flow=exp&level=4&parent=85');
    expect(rows(p).map(first)).toEqual(['8501', '8502']);
    const sp = panelByTitle(root, SERIES_TITLE);
    expect(text(sp.querySelector('.flash-head'))).toContain('85');
    expect(sp.querySelectorAll('svg').length).toBeGreaterThanOrEqual(2);
    clean(sp);
    rows(p)[0]?.click(); // 8501 → HS6
    await flush(10);
    expect(hz.paths()).toContain('trade/items?flow=exp&level=6&parent=8501');
    pick(p, '850110');
    await flush(10);
    expect(hz.paths()).toContain('trade/items?flow=exp&level=10&parent=850110');
    expect(rows(p)).toHaveLength(2);
    expect(text(p)).not.toContain(HS10_NOTE);
    buttonByText(p, '전체').click();
    await flush(10);
    expect(rows(p)).toHaveLength(12);
  });

  it('수입으로 바꾸면 수입 HS2 를 부른다', async () => {
    const { hz, root } = await mount('login');
    await openTab(root, '품목');
    const p = panelByTitle(root, ITEMS);
    buttonByText(p, '수입').click();
    await flush(10);
    expect(hz.paths()).toContain('trade/items?flow=imp&level=2');
  });

  it('중량 기준 부적합 꼬리표와 수출 상위 국가', async () => {
    const { root } = await mount('login');
    await openTab(root, '품목');
    const p = panelByTitle(root, ITEMS);
    pick(p, '89');
    await flush(10);
    pick(p, '8901');
    await flush(10);
    expect(text(panelByTitle(root, SERIES_TITLE))).toContain('중량 기준 부적합');
  });

  it('아직 없음(404)은 탭 안 한 줄 — 다른 탭은 그대로', async () => {
    const { root } = await mount('login', {
      'trade/items': () => json({ code: 'no_data', message: '아직 없음 — trade.customs_monthly' }, 404),
    });
    await openTab(root, '품목');
    expect(text(panelByTitle(root, ITEMS).querySelector('.msg:not(.err)'))).toContain('아직 없음');
    await openTab(root, '104품목');
    expect(rows(panelByTitle(root, ITEMS104)).length).toBeGreaterThan(0);
  });

  it('CSV 는 BOM·출처 줄·머리', () => {
    const d = exploreFixture<Env<TradeItems>>('items_l2_exp').data;
    const csv = itemsCsv(d.rows, d.ym_to);
    expect(csv.startsWith(BOM)).toBe(true);
    expect(csv).toContain('HS,품목,금액(달러),중량(kg),전년 대비,비중,단가(달러/kg)');
    expect(csv).toContain('출처,관세청(공공데이터포털)');
  });
});

describe('페이지 7 국가·지역·급등·104품목 — 로그인', () => {
  it('국가 총괄 40행 → 국가를 고르면 그 국가 수출 품목', async () => {
    const { hz, root } = await mount('login');
    await openTab(root, '국가');
    const p = panelByTitle(root, COUNTRIES_TITLE);
    expect(rows(p)).toHaveLength(40);
    clean(p);
    rows(p).find((r) => text(r).includes('(US)'))?.click();
    await flush(10);
    expect(hz.paths()).toContain('trade/countries/US/items?flow=exp');
    expect(rows(panelByTitle(root, ITEMS_TITLE))).toHaveLength(24);
    const csv = countriesCsv(exploreFixture<Env<{ ym: string; rows: never[] }>>('countries').data.rows, '2026-10');
    expect(csv.startsWith(BOM)).toBe(true);
  });

  it('지역은 한 번 받아 HS6 를 화면에서 거른다', async () => {
    const { hz, root } = await mount('login');
    await openTab(root, '지역');
    const p = panelByTitle(root, REGIONS);
    expect(hz.paths().filter((x) => x.startsWith('trade/regions'))).toEqual(['trade/regions']);
    const n = rows(p).length;
    expect(n).toBeGreaterThan(0);
    const sel = p.querySelector('select');
    expect(sel?.options.length).toBeGreaterThan(1);
    clean(p);
  });

  it('급등·급락 — 층·흐름 거르기(요청 1회)', async () => {
    const { hz, root } = await mount('login');
    await openTab(root, '급등');
    const p = panelByTitle(root, SURGE);
    expect(text(p)).toContain('8502');
    buttonByText(p, 'HS2').click();
    await flush();
    expect(text(p)).not.toContain('HS4 8502');
    expect(hz.paths().filter((x) => x.startsWith('trade/surge'))).toHaveLength(1);
    const all = exploreFixture<Env<{ rows: Parameters<typeof surgeRows>[0] }>>('surges').data.rows;
    const exp = surgeRows(all, 'hs4', 'exp');
    expect(exp[0]?.rule).toBe('surge');
    expect(exp.some((r) => r.rule === 'plunge' && r.key === '2701')).toBe(true);
  });

  it('104품목 — 트리맵·표, 고정 목록이면 자동 선택 꼬리표 없음', async () => {
    const { root } = await mount('login');
    await openTab(root, '104품목');
    const p = panelByTitle(root, ITEMS104);
    expect(rows(p).length).toBeGreaterThan(0);
    expect(text(p)).not.toContain(AUTO_TAG);
    clean(p);
  });
});

describe('페이지 7 탐색 — 공개', () => {
  it('품목은 공개 파일만(hs2 → hs/85) — 로그인 API 0, HS6 에서 HS10 안내', async () => {
    const { hz, root } = await mount('public');
    await openTab(root, '품목');
    const p = panelByTitle(root, ITEMS);
    expect(rows(p)).toHaveLength(12);
    pick(p, '85');
    await flush(10);
    expect(rows(p).map(first)).toEqual(['8501', '8502']);
    pick(p, '8502');
    await flush(10);
    expect(text(p)).toContain(HS10_NOTE);
    expect(rows(panelByTitle(root, SERIES_TITLE)).length).toBe(10); // 상위 국가
    buttonByText(p, '전체').click();
    await flush(10);
    pick(p, '89');
    await flush(10);
    pick(p, '8901');
    await flush(10);
    expect(text(panelByTitle(root, SERIES_TITLE))).toContain('중량 기준 부적합');
    expect(hz.paths().every((x) => x.startsWith('./data/'))).toBe(true);
    expect(hz.paths()).toContain('./data/trade/hs/85.json');
    expect(hz.paths().filter((x) => x === './data/trade/hs/85.json')).toHaveLength(1);
    expect(hz.paths()).toContain('./data/trade/hs/89.json');
    clean(p);
  });

  it('공개 노드·추이는 로그인 API 와 같은 값', () => {
    const pub = exploreFixture<Env<PubTree>>('public/trade/hs2');
    const api = exploreFixture<Env<TradeItems>>('items_l2_exp').data.rows;
    const nodes = pubNodes(pub.data, 2, null, 'exp', pub.quality);
    expect(nodes.map((n) => n.hs)).toEqual(api.map((n) => n.hs));
    nodes.forEach((n, i) => {
      const a = api[i];
      expect(n.usd).toBe(a?.usd);
      expect(n.yoy).toBeCloseTo(a?.yoy ?? NaN, 3); // 공개 비율은 4자리 반올림(RATIO_DIGITS)
      expect(n.share).toBeCloseTo(a?.share ?? NaN, 9);
    });
    const ch = exploreFixture<Env<PubTree>>('public/trade/hs/85').data;
    const code = ch.codes.find((c) => c.hs === '8502');
    if (!code) throw new Error('8502 없음');
    const pts = pubPoints(code, ch.months, 'exp', 'ok');
    const series = exploreFixture<Env<TradeItemSeries>>('series_8502').data.points;
    for (const p of pts.slice(-12)) {
      const s = series.find((x) => x.ym === p.ym);
      expect(p.usd).toBe(s?.usd);
      expect(p.ln_price).toBeCloseTo(s?.ln_price ?? NaN, 9);
      expect(p.ln_volume).toBeCloseTo(s?.ln_volume ?? NaN, 9);
    }
  });

  it('국가·지역·급등·104품목도 공개 파일로 그린다', async () => {
    const { hz, root } = await mount('public');
    for (const [tab, title] of [
      ['국가', COUNTRIES_TITLE],
      ['지역', REGIONS],
      ['급등', SURGE],
      ['104품목', ITEMS104],
    ] as const) {
      await openTab(root, tab);
      const p = panelByTitle(root, title);
      expect(rows(p).length, tab).toBeGreaterThan(0);
      clean(p);
    }
    rows(panelByTitle(root, COUNTRIES_TITLE)).find((r) => text(r).includes('(US)'))?.click();
    await flush(10);
    expect(hz.paths()).toContain('./data/trade/country/US.json');
    expect(hz.paths()).toContain('./data/trade/surge.json');
    expect(hz.paths().some((x) => x.startsWith('./data/trade/region/'))).toBe(true);
    expect(hz.paths().every((x) => x.startsWith('./data/'))).toBe(true);
  });
});
