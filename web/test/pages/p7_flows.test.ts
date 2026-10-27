// 페이지 7 '종목' 탭 — 종목별 무역 흐름(P10 단계 1). 합성 픽스처(p6_world — tests/unit/api/web_fixtures_p6/map.py)만.
// 로그인: 종목을 고르면 /api/trade/stock/<코드>/preview·/flows 한 번씩, 프리뷰 카드 + 흐름 카드·차트·표·CSV.
// 공개: 자물쇠(요청 0).
import { describe, expect, it } from 'vitest';
import type { DataSource, Envelope, Params } from '../../src/data/source';
import type { TradeStocks } from '../../src/pages/p7/common';
import { flowsCsv, flowsPanel, pick, TITLE, type TradeStockFlows } from '../../src/pages/p7/flows';
import { type TradePreview, verdictText } from '../../src/pages/p7/preview';
import { mount } from '../../src/pages/p7/stocks';
import { BOM } from '../../src/ui/csv';
import { MAP_NOW, mapFixture, mapLoginHarness, mapPublicHarness } from '../fixtures/api/trade_map/harness';
import { flush, panelByTitle, text } from './support';

function setup(tier: 'login' | 'public') {
  const hz = tier === 'login' ? mapLoginHarness() : mapPublicHarness();
  const fp = flowsPanel({ tier, source: hz.source, now: () => MAP_NOW });
  document.body.append(fp.el);
  return { hz, fp };
}

describe('종목별 무역 흐름', () => {
  it('로그인: 고른 종목의 흐름 카드·보기·표', async () => {
    const { hz, fp } = setup('login');
    await fp.show('992004');
    await flush();
    expect(hz.paths().sort()).toEqual(['trade/stock/992004/flows', 'trade/stock/992004/preview']);
    const env = mapFixture<{ data: TradeStockFlows }>('flows_992004');
    const blocks = fp.el.querySelectorAll('.flow-block:not(.preview)');
    expect(blocks.length).toBe(env.data.flows.length);
    const t = text(fp.el);
    expect(t).toContain('추적 품목');
    expect(t).toContain('최근 기준월');
    expect(t).toContain('구분');
    expect(t).not.toMatch(/NaN|undefined|Infinity/);
    const sig = env.data.flows.find((f) => f.kind === 'sigungu');
    if (sig) {
      const card = fp.el.querySelector(`[data-flow="${sig.id}"]`);
      expect(text(card)).toContain('시군구');
      // 시군구 흐름은 단가·중량 보기가 없다
      const block = card?.closest('.flow-block');
      const labels = [...(block?.querySelectorAll('.seg button') ?? [])].map((b) => b.textContent);
      expect(labels).toEqual(['금액', '일평균']);
    }
    expect(text(fp.el.querySelector('.src'))).toContain('관세청');
  });

  it('보기 바꾸기 — 일평균은 다른 선을 그린다', async () => {
    const { fp } = setup('login');
    await fp.show('992004');
    await flush();
    const block = fp.el.querySelector('.flow-block:not(.preview)');
    const before = block?.querySelector('.flow-chart')?.firstElementChild;
    const daily = [...(block?.querySelectorAll<HTMLButtonElement>('.seg button') ?? [])].find(
      (b) => b.textContent === '일평균',
    );
    daily?.click();
    const after = block?.querySelector('.flow-chart')?.firstElementChild;
    expect(after).toBeTruthy();
    expect(after).not.toBe(before);
    expect(after?.className).toContain('linechart');
  });

  it('실적 프리뷰 카드 — 분기·추정·신뢰도, 컨센서스 없으면 판정 없음', async () => {
    const { fp } = setup('login');
    await fp.show('992004');
    await flush();
    const env = mapFixture<{ data: TradePreview; notes: string[] }>('preview_992004');
    const card = fp.el.querySelector('.flow-block.preview');
    const t = text(card);
    expect(t).toContain(`실적 프리뷰 — ${env.data.quarter}`);
    expect(t).toContain('추정 매출');
    expect(t).toContain('신뢰도');
    expect(t).toContain('매매 권유가 아니다');
    expect(t).toContain(verdictText(env.data));
    expect(t).not.toMatch(/NaN|undefined|Infinity/);
  });

  it('프리뷰가 없어도(지수 없음) 흐름은 그린다', async () => {
    const { fp } = setup('login');
    await fp.show('992060');
    await flush();
    expect(text(fp.el)).toContain('실적 프리뷰 없음');
    expect(fp.el.querySelectorAll('.flow-block:not(.preview)').length).toBeGreaterThan(0);
  });

  it('판정 글자 — 지난달과 다르면 같이 적는다', () => {
    const env = mapFixture<{ data: TradePreview }>('preview_992004');
    const d = { ...env.data, reported: false, verdict: 'BEAT' as const, prev_verdict: 'MISS' as const };
    expect(verdictText(d)).toBe('BEAT (지난달 MISS)');
    expect(verdictText({ ...d, prev_verdict: 'BEAT' })).toBe('BEAT');
    expect(verdictText({ ...d, reported: true })).toBe('공시됨(사후 대조)');
    expect(verdictText({ ...d, verdict: null, revenue_est: null })).toBe('추정 없음');
  });

  it('매핑 없는 종목은 안내 한 줄', async () => {
    const { fp } = setup('login');
    await fp.show('994010');
    await flush();
    expect(text(fp.el)).toContain('매핑 없음');
    // 404 no_data 는 서버 문구 그대로 — 지수 작업 시각 힌트를 덧붙이지 않는다(매핑 없음과 무관한 안내였다)
    expect(fp.el.querySelector('.flows .msg.err')).toBeNull();
    expect(text(fp.el.querySelector('.flows'))).not.toContain('trade.stock_index');
  });

  it('종목을 빨리 바꾸면 마지막 종목만 — 먼저 고른 종목의 늦은 응답이 덮지 않는다', async () => {
    const hz = mapLoginHarness();
    let release: () => void = () => undefined;
    const gate = new Promise<void>((r) => {
      release = r;
    });
    // 992004 응답만 늦게 온다(데모 감사 2026-10-27 — 상자는 B 인데 흐름은 A 로 남았다)
    const slow: DataSource = {
      tier: 'login',
      get: async <T,>(key: string, params?: Params): Promise<Envelope<T>> => {
        if (key.includes('992004')) await gate;
        return hz.source.get<T>(key, params);
      },
    };
    const fp = flowsPanel({ tier: 'login', source: slow, now: () => MAP_NOW }, [
      { code: '992004', name: 'A' },
      { code: '992060', name: 'B' },
    ] as TradeStocks['rows']);
    document.body.append(fp.el);
    const first = fp.show('992004');
    await fp.show('992060');
    release();
    await first;
    await flush();
    const later = mapFixture<{ data: TradeStockFlows }>('flows_992060');
    expect(text(fp.el.querySelector('.flows-head'))).toContain(`(${later.data.code})`);
    expect(text(fp.el.querySelector('.flows-head'))).not.toContain('992004');
    expect(fp.el.querySelector<HTMLSelectElement>('select')?.value).toBe('992060');
  });

  it('공개: 자물쇠, 요청 0', async () => {
    const { hz, fp } = setup('public');
    await fp.show('992004');
    expect(hz.paths()).toEqual([]);
    expect(fp.el.querySelector('.flow-block')).toBeNull();
  });

  it('랭킹 표 종목 이름을 누르면 그 종목 흐름(로그인)', async () => {
    const hz = mapLoginHarness();
    const el = document.createElement('div');
    document.body.append(el);
    await mount({ tier: 'login', source: hz.source, now: () => MAP_NOW }, el);
    await flush();
    expect(panelByTitle(el, TITLE)).toBeTruthy();
    const btn = [...el.querySelectorAll<HTMLButtonElement>('button.link')].find((b) =>
      b.textContent.includes('992004'),
    );
    btn?.click();
    await flush();
    expect(hz.paths().sort()).toEqual(['trade/stock/992004/flows', 'trade/stock/992004/preview', 'trade/stocks']);
    expect(el.querySelectorAll('.flow-block').length).toBeGreaterThan(0);
  });

  it('CSV — BOM·열·종목·흐름', () => {
    const env = mapFixture<{ data: TradeStockFlows }>('flows_992004');
    const f = env.data.flows[0];
    if (!f) throw new Error('흐름 없음');
    const csv = flowsCsv(env.data, f, '2026-12-11');
    expect(csv.startsWith(BOM)).toBe(true);
    expect(csv).toContain('기간,금액(달러),금액 YoY,중량(kg)');
    expect(csv).toContain(f.name);
  });

  it('pick — 보기마다 값·YoY', () => {
    const p = {
      ym: '2026-09',
      usd: 10,
      kg: 2,
      unit_price: 5,
      usd_yoy: 0.1,
      kg_yoy: 0.2,
      price_yoy: 0.3,
      days: 20,
      daily_usd: 1,
      daily_yoy: 0.4,
      products: {},
      quality: 'ok' as const,
    };
    expect(pick(p, 'usd')).toEqual({ v: 10, yoy: 0.1 });
    expect(pick(p, 'price')).toEqual({ v: 5, yoy: 0.3 });
    expect(pick(p, 'kg')).toEqual({ v: 2, yoy: 0.2 });
    expect(pick(p, 'daily')).toEqual({ v: 1, yoy: 0.4 });
  });
});
