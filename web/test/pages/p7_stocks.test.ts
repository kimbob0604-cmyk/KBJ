// 페이지 7 '품목' 탭 아래 매핑 종목 랭킹 표(MAP — docs/p6_design.md §4.6·§6, metrics §28). 로그인은 API(공개·local 매핑),
// 공개는 공개 파일(공개 매핑만 — 로그인 API 0회). 합성 픽스처(p6_world)만.
import { describe, expect, it } from 'vitest';
import type { TradeStocks } from '../../src/pages/p7/common';
import { mount, STATUS_LABEL, stocksCsv, TITLE } from '../../src/pages/p7/stocks';
import { BOM } from '../../src/ui/csv';
import { json } from '../helpers';
import { MAP_NOW, mapFixture, mapLoginHarness, mapPublicHarness, type Over } from '../fixtures/api/trade_map/harness';
import { flush, panelByTitle, text } from './support';

async function run(tier: 'login' | 'public', over: Over = {}) {
  const hz = tier === 'login' ? mapLoginHarness(over) : mapPublicHarness(over);
  const el = document.createElement('div');
  document.body.append(el);
  await mount({ tier, source: hz.source, now: () => MAP_NOW }, el);
  await flush();
  return { hz, el, p: panelByTitle(el, TITLE) };
}

const body = (p: HTMLElement): HTMLTableRowElement[] => [...p.querySelectorAll<HTMLTableRowElement>('tbody tr')];

describe('매핑 종목 랭킹', () => {
  it('로그인: 서버 순서 그대로·상태·검토 전·로그인 전용 꼬리표', async () => {
    const { hz, p } = await run('login');
    expect(hz.paths()).toEqual(['trade/stocks']);
    const env = mapFixture<{ data: TradeStocks }>('stocks');
    const rows = body(p);
    expect(rows.length).toBe(Math.min(50, env.data.rows.length));
    expect(text(rows[0]?.cells[1])).toContain(env.data.rows[0]?.code ?? '?');
    expect(text(rows[0]?.cells[0])).toBe('1');
    const t = text(p);
    expect(t).toContain('검토 전');
    expect(t).toContain('로그인 전용');
    expect(t).toContain(STATUS_LABEL.no_trade ?? '');
    expect(t).not.toMatch(/NaN|undefined|Infinity/);
    expect(text(p.querySelector('.src'))).toContain('관세청');
  });

  it('공개: 공개 파일만(로그인 전용 줄 없음)', async () => {
    const { hz, p } = await run('public');
    expect(hz.paths()).toEqual(['./data/trade/stocks.json']);
    expect(text(p)).not.toContain('로그인 전용');
    expect(body(p).length).toBeGreaterThan(0);
  });

  it('아직 없음(404)은 작업 이름', async () => {
    const { p } = await run('login', {
      'trade/stocks': () =>
        json({ code: 'no_data', message: '아직 없음 — trade.stock_index 21:00(매핑 종목 수출 지수)' }, 404),
    });
    expect(text(p)).toContain('trade.stock_index');
  });

  it('CSV — BOM·순위·출처', () => {
    const env = mapFixture<{ data: TradeStocks }>('stocks');
    const csv = stocksCsv(env.data.rows, '2026-10');
    expect(csv.startsWith(BOM)).toBe(true);
    const lines = csv.split('\r\n');
    expect(lines[0]).toBe(BOM + '순위,종목코드,종목,3개월 수출 지수 YoY,상태,검토');
    expect(lines[1]?.startsWith('1,')).toBe(true);
    expect(csv).toContain('출처,관세청(공공데이터포털)');
  });
});
