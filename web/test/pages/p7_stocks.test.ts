// 페이지 7 '종목' 탭 매핑 종목 랭킹 표(MAP — docs/p6_design.md §4.6·§6, metrics §28). 로그인은 API(공개·local 매핑),
// 공개는 공개 파일(공개 매핑만 — 로그인 API 0회). 합성 픽스처(p6_world)만.
import { describe, expect, it } from 'vitest';
import type { TradeStocks } from '../../src/pages/p7/common';
import { filterRows, mount, sectors, STATUS_LABEL, stocksCsv, TITLE } from '../../src/pages/p7/stocks';
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

describe('맵핑 트렌드 — 필터·정렬(P10 단계 2)', () => {
  const rows = [
    { code: '000002', name: 'B', yoy_3m: 0.1, yoy_1m: 0.5, status: 'ok', reviewed: false, origin: 'public', quality: 'ok', sector: '반도체', spark: [] },
    { code: '000001', name: 'A', yoy_3m: 0.05, yoy_1m: -0.4, status: 'ok', reviewed: false, origin: 'public', quality: 'ok', sector: '조선', spark: [] },
    { code: '000003', name: 'C', yoy_3m: 0.01, yoy_1m: 0.02, status: 'ok', reviewed: false, origin: 'public', quality: 'ok', sector: '반도체', spark: [] },
  ] as TradeStocks['rows'];

  it('산업·급등락·정렬', () => {
    expect(filterRows(rows, { sector: '', swing: false, sort: 'yoy_3m' }).map((r) => r.code)).toEqual(['000002', '000001', '000003']);
    expect(filterRows(rows, { sector: '반도체', swing: false, sort: 'yoy_3m' }).map((r) => r.code)).toEqual(['000002', '000003']);
    expect(filterRows(rows, { sector: '', swing: true, sort: 'yoy_3m' }).map((r) => r.code)).toEqual(['000002', '000001']);
    expect(filterRows(rows, { sector: '', swing: false, sort: 'yoy_1m' }).map((r) => r.code)).toEqual(['000002', '000003', '000001']);
    expect(filterRows(rows, { sector: '', swing: false, sort: 'code' }).map((r) => r.code)).toEqual(['000001', '000002', '000003']);
  });

  it('산업 칩 — 많은 순, 첫 칩은 전체', () => {
    expect(sectors(rows).map((s) => s.label)).toEqual(['전체(3)', '반도체(2)', '조선(1)']);
  });
});
