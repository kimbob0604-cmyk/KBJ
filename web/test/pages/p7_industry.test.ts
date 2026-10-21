// 페이지 7 '산업' 탭·국가 품목 추이(P10 단계 4). 합성 픽스처(p6_world — tests/unit/api/web_fixtures_p6/map.py)만.
// 로그인: 'trade/sectors' 한 번 → 산업 표, 행을 고르면 종목·추적 품목. 공개: 자물쇠(요청 0).
// 국가 탭(로그인)은 국가를 고르면 'trade/countries/<cc>/trend' 도 불러 품목 추이 표(24개월·관련 종목)를 더 그린다.
import { describe, expect, it } from 'vitest';
import { mount as mountCountries, relatedText, type TradeCountryTrend } from '../../src/pages/p7/countries';
import { DETAIL_TITLE, mount, sectorsCsv, TITLE, type TradeSectors } from '../../src/pages/p7/industry';
import { BOM } from '../../src/ui/csv';
import { json } from '../helpers';
import { EXPLORE_NOW, exploreLoginHarness } from '../fixtures/api/trade_explore/harness';
import { MAP_NOW, mapFixture, mapLoginHarness, mapPublicHarness } from '../fixtures/api/trade_map/harness';
import { flush, panelByTitle, text } from './support';

async function setup(tier: 'login' | 'public') {
  const hz = tier === 'login' ? mapLoginHarness() : mapPublicHarness();
  const el = document.createElement('div');
  document.body.append(el);
  await mount({ tier, source: hz.source, now: () => MAP_NOW }, el);
  await flush();
  return { hz, el };
}

describe('산업 탭', () => {
  it('로그인: 산업 표 → 행을 고르면 종목·추적 품목', async () => {
    const { hz, el } = await setup('login');
    expect(hz.paths()).toEqual(['trade/sectors']);
    const env = mapFixture<{ data: TradeSectors }>('sectors');
    const p = panelByTitle(el, TITLE);
    expect(p.querySelectorAll('tbody tr').length).toBe(env.data.rows.length);
    const first = env.data.rows[0];
    if (!first) throw new Error('산업 없음');
    p.querySelector<HTMLElement>('tbody tr')?.click();
    await flush();
    const d = text(panelByTitle(el, DETAIL_TITLE));
    expect(d).toContain(`${first.sector} · 종목 ${String(first.n_stocks)}개`);
    expect(d).toContain('추적 품목');
    expect(text(el)).not.toMatch(/NaN|undefined|Infinity/);
  });

  it('공개: 자물쇠, 요청 0', async () => {
    const { hz, el } = await setup('public');
    expect(hz.paths()).toEqual([]);
    expect(el.querySelectorAll('.panel.locked').length).toBe(2);
  });

  it('CSV — BOM·열', () => {
    const env = mapFixture<{ data: TradeSectors }>('sectors');
    const csv = sectorsCsv(env.data.rows, env.data.last_ym);
    expect(csv.startsWith(BOM)).toBe(true);
    expect(csv).toContain('산업,종목 수,최신월 지수 합(달러),YoY,3개월 YoY');
  });
});

describe('국가 품목 추이', () => {
  it('로그인: 국가를 고르면 추이 표(24개월·관련 종목)', async () => {
    const trend = mapFixture<{ data: TradeCountryTrend }>('trend_US');
    const hz = exploreLoginHarness({ 'trade/countries/US/trend': () => json(trend) });
    const el = document.createElement('div');
    document.body.append(el);
    await mountCountries({ tier: 'login', source: hz.source, now: () => EXPLORE_NOW }, el);
    await flush();
    const row = [...el.querySelectorAll<HTMLElement>('tbody tr')].find((r) => text(r).includes('(US)'));
    row?.click();
    await flush();
    expect(hz.paths()).toContain('trade/countries/US/trend');
    const t = text(el);
    expect(t).toContain('품목 추이 · 24개월');
    const withStocks = trend.data.rows.find((r) => r.stocks.length > 0);
    if (withStocks) expect(t).toContain(relatedText(withStocks));
    expect(t).not.toMatch(/NaN|undefined|Infinity/);
  });

  it('관련 종목 글자 — 셋까지 + 외 N', () => {
    const base = { hs: '8542', usd: 1, mom: null, yoy: null, spark: [] };
    const s = (n: number) => Array.from({ length: n }, (_, i) => ({ code: `99000${String(i)}`, name: `가${String(i)}` }));
    expect(relatedText({ ...base, stocks: [] })).toBe('—');
    expect(relatedText({ ...base, stocks: s(2) })).toBe('가0, 가1');
    expect(relatedText({ ...base, stocks: s(5) })).toBe('가0, 가1, 가2 외 2');
  });
});
