// 페이지 6 매크로(MAC — docs/p5_design.md §6): 탭 6(공개)·시장 탭(로그인 — 공개판 자물쇠)·경제 캘린더.
// 시리즈 카드(최신값·변화·출처·quality)·확인 전 시리즈는 '—' + 실측 번호·탭마다 오류 격리·공개판은 로그인 API 0회.
import { describe, expect, it } from 'vitest';
import page, { P6_TITLES } from '../../src/pages/p6_macro';
import { changeText, krwCompact, seriesCard, seriesKey } from '../../src/pages/p6/common';
import { json } from '../helpers';
import { MAC_NOW, macLoginHarness, macPublicHarness } from '../fixtures/api/macro/harness';
import { flush, pageCtx, panelByTitle, text } from './support';

async function mount(tier: 'login' | 'public', over: Record<string, (() => Response) | undefined> = {}) {
  const hz = tier === 'login' ? macLoginHarness(over) : macPublicHarness(over);
  const { ctx, root } = pageCtx(6, tier, hz.source, { now: () => MAC_NOW });
  await page.mount(ctx);
  await flush();
  return { hz, root };
}

const card = (root: ParentNode, id: string): HTMLElement => {
  const c = root.querySelector<HTMLElement>(`.mcard[data-series="${id}"]`);
  if (!c) throw new Error(`카드 없음: ${id}`);
  return c;
};

const tabButton = (root: ParentNode, label: string): HTMLButtonElement => {
  const b = [...root.querySelectorAll<HTMLButtonElement>('[role="tab"]')].find((x) => text(x) === label);
  if (!b) throw new Error(`탭 없음: ${label}`);
  return b;
};

describe('페이지 6 — 로그인', () => {
  it('탭 목록·첫 탭·캘린더·시장 탭을 부르고 카드로 그린다', async () => {
    const { hz, root } = await mount('login');
    expect(hz.paths().sort()).toEqual(
      ['macro/tabs', 'macro/series?tab=rates', 'macro/calendar', 'macro/series?tab=market'].sort(),
    );
    const series = panelByTitle(root, P6_TITLES.series);
    expect([...series.querySelectorAll('[role="tab"]')].map((b) => text(b))).toEqual([
      '금리·통화',
      '물가',
      '성장·경기',
      '고용·가계',
      '대외·환율',
      '미국',
    ]);
    const spread = card(series, 'DERIVED:credit_aa');
    expect(text(spread.querySelector('.p .v'))).toBe('0.53%p');
    expect(spread.classList.contains('stale')).toBe(false);
    expect(text(spread)).toContain('파생');
    const unprobed = card(series, 'ECOS:817Y002:ktb10y');
    expect(text(unprobed.querySelector('.p .v'))).toBe('—'); // 0 이 아니다
    expect(text(unprobed.querySelector('.na'))).toBe('아직 없음 — macro.morning·monthly·evening'); // 항목 코드를 채운 뒤(0d7ab8a) — 값이 없으면 수집 안내
    expect(series.querySelector('a[href*="bok.or.kr"]')).not.toBeNull(); // 보고서 지표 원문 링크(Q2)
    const market = panelByTitle(root, P6_TITLES.market);
    expect(market.classList.contains('locked')).toBe(false);
    expect(text(card(market, 'YAHOO:KRW=X'))).toContain('로그인 등급');
    const cal = panelByTitle(root, P6_TITLES.calendar);
    expect([...cal.querySelectorAll('th')].map((t) => text(t))).toContain('예상치');
  });

  it('탭은 처음 볼 때 한 번 — 값 없는 탭은 그 탭 안 한 줄(다른 탭은 그대로)', async () => {
    const { hz, root } = await mount('login');
    const series = panelByTitle(root, P6_TITLES.series);
    tabButton(series, '고용·가계').click();
    await flush();
    expect(hz.paths()).toContain('macro/series?tab=labor');
    const labor = series.querySelector('[role="tabpanel"]:not([hidden])');
    expect(text(labor?.querySelector('.msg.err'))).toContain('아직 없음');
    tabButton(series, '미국').click();
    await flush();
    const us = series.querySelector('[role="tabpanel"]:not([hidden])');
    expect(us?.querySelector('.mcard[data-series="FRED:DGS10"]')).not.toBeNull();
    tabButton(series, '금리·통화').click();
    await flush();
    expect(hz.paths().filter((p) => p === 'macro/series?tab=rates')).toHaveLength(1);
  });

  it('탭 목록 실패는 시리즈 패널만 — 캘린더·시장 탭은 계속', async () => {
    const { root } = await mount('login', { 'macro/tabs': () => json({ detail: 'secret' }, 500) });
    expect(text(panelByTitle(root, P6_TITLES.series))).toContain('HTTP 500');
    expect(text(panelByTitle(root, P6_TITLES.series))).not.toContain('secret');
    expect(panelByTitle(root, P6_TITLES.calendar).querySelector('table')).not.toBeNull();
    expect(panelByTitle(root, P6_TITLES.market).querySelector('.mcard')).not.toBeNull();
  });
});

describe('페이지 6 — 공개판', () => {
  it('공개 파일만(로그인 API 0회), 시장 탭은 자물쇠·요청 없음, 예상치 열 없음', async () => {
    const { hz, root } = await mount('public');
    expect(hz.paths().sort()).toEqual(['./data/macro/calendar.json', './data/macro/rates.json', './data/macro/tabs.json']);
    expect(panelByTitle(root, P6_TITLES.market).classList.contains('locked')).toBe(true);
    const series = panelByTitle(root, P6_TITLES.series);
    expect(series.querySelector('[role="tab"]')).not.toBeNull();
    expect(text(card(series, 'DERIVED:credit_aa').querySelector('.p .v'))).toBe('0.53%p');
    expect(text(root)).not.toContain('YAHOO');
    const cal = panelByTitle(root, P6_TITLES.calendar);
    expect([...cal.querySelectorAll('th')].map((t) => text(t))).not.toContain('예상치');
    tabButton(series, '성장·경기').click();
    await flush();
    expect(text(series.querySelector('[role="tabpanel"]:not([hidden]) .msg.err'))).toContain('아직 없음');
  });
});

describe('도우미', () => {
  it('탭 키 — 로그인 API 와 공개 파일', () => {
    expect(seriesKey('login', 'us')).toEqual({ key: 'macro/series', params: { tab: 'us' } });
    expect(seriesKey('public', 'us')).toEqual({ key: 'macro/us' });
  });
  it('변화 문구 — %·%p 는 %p, 그 밖은 차와 변화율, 없으면 —', () => {
    expect(changeText({ change: 0.05, change_pct: 1.2, unit: '%' })).toBe('+0.05%p');
    expect(changeText({ change: -1200, change_pct: -0.8, unit: '천명' })).toBe('-1,200 (-0.80%)');
    expect(changeText({ change: null, change_pct: null, unit: '%' })).toBe('—');
  });
});

describe('페이지 6 카드 숫자 — 카드를 넘치지 않게', () => {
  it('큰 원화는 조·억, 지수 기준(2020=100)은 값 옆이 아니라 출처 줄', () => {
    expect(krwCompact(104_664_400_150_814)).toBe('104.66조 원');
    expect(krwCompact(4_959_968_638_979, true)).toBe('+4.96조 원');
    expect(krwCompact(49_600_000_000)).toBe('496억 원');
    expect(krwCompact(1_332)).toBe('1,332 원');
    const base = { series_id: 'X', title: 'CPI', unit: '2020=100', freq: 'M', source: 'ECOS', tier: 'public', derived: false, quality: 'ok', as_of: null, na_reason: null, change: 0.38, change_pct: 0.32, points: [], latest: { period: '2026-09', value: 120.43 } };
    const card = seriesCard(base as unknown as Parameters<typeof seriesCard>[0]);
    expect(card.querySelector('.v')?.textContent).toBe('120.43');
    expect(card.querySelector('.src')?.textContent).toContain('지수 2020=100');
  });
});
