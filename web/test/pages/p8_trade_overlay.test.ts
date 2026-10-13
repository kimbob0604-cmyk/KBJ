// 페이지 8 '수출 겹쳐 보기'(MAP — docs/p6_design.md §4.6·§6, metrics §28): 공개 패널(수출 지수 YoY 대 분기 매출 YoY·
// 시차 상관·매핑 표·근거·'검토 전'·'환율 미조정')과 로그인 월말 종가 줄. 합성 픽스처(p6_world)만 — 고리(p8_stock.ts)는 WRAP.
import { describe, expect, it } from 'vitest';
import type { Tier } from '../../src/data/source';
import { memoGet, type TabCtx } from '../../src/pages/p8/common';
import {
  DRAFT_TAG,
  lagText,
  mount,
  NOT_MAPPED,
  overlayCsv,
  STATUS_TEXT,
  TITLE,
  type TradeStock,
} from '../../src/pages/p8/trade_overlay';
import { indexed, monthEnd, mount as mountPrice, TITLE as PRICE_TITLE } from '../../src/pages/p8/trade_overlay_price';
import { BOM } from '../../src/ui/csv';
import { json } from '../helpers';
import { MAP_NOW, mapFixture, mapLoginHarness, mapPublicHarness, type Over } from '../fixtures/api/trade_map/harness';
import { flush, panelByTitle, text } from './support';

function tabCtx(tier: Tier, code: string, source: TabCtx['source']): TabCtx {
  return {
    tier,
    code,
    source,
    now: () => MAP_NOW,
    get: memoGet(source),
    pub: () => Promise.resolve(null),
    corp: () => Promise.resolve(null),
    open: () => undefined,
  };
}

async function run(tier: Tier, code: string, over: Over = {}, price = false) {
  const hz = tier === 'login' ? mapLoginHarness(over) : mapPublicHarness(over);
  const el = document.createElement('div');
  document.body.append(el);
  const t = tabCtx(tier, code, hz.source);
  await (price ? mountPrice(t, el) : mount(t, el));
  await flush();
  return { hz, el, p: panelByTitle(el, price ? PRICE_TITLE : TITLE) };
}

const clean = (el: Element): void => {
  expect(text(el)).not.toMatch(/NaN|undefined|Infinity/);
};

describe('수출 겹쳐 보기 — 공개 패널', () => {
  it('로그인: 두 선·시차 상관·매핑 표·근거·꼬리표', async () => {
    const { hz, p } = await run('login', '992004');
    expect(hz.paths()).toEqual(['trade/stock/992004']);
    expect(p.querySelector('svg')).not.toBeNull();
    const t = text(p);
    expect(t).toContain(DRAFT_TAG);
    expect(t).toContain('환율 미조정(달러)');
    expect(t).toContain('시차 상관(수출이 앞섬)');
    expect(t).toContain('합성 근거(실측 아님)');
    expect(p.querySelectorAll('tbody tr').length).toBeGreaterThan(0);
    expect(text(p.querySelector('.src'))).toContain('관세청');
    clean(p);
  });

  it('공개: 같은 모양을 공개 파일로(로그인 API 0회)', async () => {
    const { hz, p } = await run('public', '992004');
    expect(hz.paths()).toEqual(['./data/trade/stock/992004.json']);
    expect(p.querySelector('svg')).not.toBeNull();
    expect(text(p)).toContain(DRAFT_TAG);
    expect(text(p)).not.toContain('로그인 전용');
    clean(p);
  });

  it.each([
    ['992060', 'no_trade'],
    ['994015', 'no_revenue'],
    ['992059', 'short_overlap'],
  ])('%s 상태 %s 는 안내 줄', async (code, status) => {
    const { p } = await run('login', code);
    expect(text(p)).toContain(STATUS_TEXT[status] ?? '');
    clean(p);
  });

  it('매핑 없는 종목은 오류가 아니라 안내 한 줄(로그인·공개)', async () => {
    const a = await run('login', '994010');
    expect(text(a.p)).toContain(NOT_MAPPED);
    expect(a.p.querySelector('.err')).toBeNull();
    const b = await run('public', '994010');
    expect(text(b.p)).toContain(NOT_MAPPED);
  });

  it('local 매핑 종목은 로그인판에 꼬리표', async () => {
    const { p } = await run('login', '992011');
    expect(text(p)).toContain('로그인 전용 매핑');
  });

  it('CSV — BOM·분기 행·출처 줄', () => {
    const env = mapFixture<{ data: TradeStock }>('stock_992004');
    const csv = overlayCsv(env.data);
    expect(csv.startsWith(BOM)).toBe(true);
    expect(csv).toContain('분기,수출 지수(달러),수출 지수 YoY,매출 YoY,품질');
    expect(csv.split('\r\n').length).toBeGreaterThan(env.data.quarterly.length);
    expect(csv).toContain('출처,관세청(공공데이터포털)+DART');
  });

  it('시차 상관 글자', () => {
    expect(lagText({ '0': 0.31, '1': null, '2': -0.2 })).toBe(
      '시차 상관(수출이 앞섬) 0분기 +0.31 · 1분기 — · 2분기 -0.20',
    );
    expect(lagText({})).toBe('시차 상관 없음');
  });
});

describe('수출 겹쳐 보기 — 로그인 월말 종가', () => {
  const bars = (): object[] => {
    const out: object[] = [];
    for (let y = 2022; y <= 2026; y++)
      for (let m = 1; m <= 12; m++) {
        if (y === 2026 && m > 11) break;
        const d = `${y}-${String(m).padStart(2, '0')}`;
        out.push({ date: `${d}-10`, o: 1, h: 1, l: 1, c: 900, v: 1, turnover: 1 });
        out.push({ date: `${d}-27`, o: 1, h: 1, l: 1, c: 1000 + m, v: 1, turnover: 1 });
      }
    return out;
  };
  const chart = (): Response =>
    json({
      source: 'KRX',
      as_of: '2026-12-10T15:30:00+09:00',
      quality: 'ok',
      notes: [],
      generated_at: '2026-12-11T11:30:00+09:00',
      data: {
        code: '992004',
        days: 1240,
        adjusted: false,
        bars: bars(),
        sma: {},
        events: [],
        last_trade: '2026-11-27',
      },
    });

  it('공개판은 자물쇠 — 요청 0', async () => {
    const { hz, p } = await run('public', '992004', {}, true);
    expect(hz.calls).toHaveLength(0);
    expect(p.querySelector('.lockmsg')).not.toBeNull();
  });

  it('로그인: 지수·월말 종가를 처음 겹친 달 = 100 으로', async () => {
    const { hz, p } = await run('login', '992004', { 'stock/992004/chart': chart }, true);
    expect(hz.paths()).toEqual(['trade/stock/992004', 'stock/992004/chart?days=1240']);
    expect(p.querySelector('svg')).not.toBeNull();
    expect(text(p.querySelector('.src'))).toContain('KRX');
    clean(p);
  });

  it('월말 종가·지수 맞추기', () => {
    const me = monthEnd([
      { date: '2026-01-05', o: 1, h: 1, l: 1, c: 10, v: 1, turnover: 1 },
      { date: '2026-01-30', o: 1, h: 1, l: 1, c: 12, v: 1, turnover: 1 },
      { date: '2026-02-27', o: 1, h: 1, l: 1, c: null, v: 0, turnover: 0 },
    ]);
    expect([...me]).toEqual([['2026-01', 12]]);
    const ix = indexed(
      ['2025-12', '2026-01', '2026-02'],
      new Map([
        ['2026-01', 50],
        ['2026-02', 100],
      ]),
      new Map([
        ['2025-12', 5],
        ['2026-01', 10],
        ['2026-02', 15],
      ]),
    );
    expect(ix).toEqual({ x: ['2026-01', '2026-02'], a: [100, 200], b: [100, 150] });
  });
});
