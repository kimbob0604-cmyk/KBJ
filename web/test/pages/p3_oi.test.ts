// 페이지 3 ⑤ 코스피200 선물 미결제약정(OI) 패널(metrics §33, ADR 0043): 최근 날 칸(잠정이면 '잠정' 꼬리표)·OI 선·증감
// 막대·일별 표·옵션 투자자 표. 응답 한 번을 두 패널이 나눈다. 로그인만 — 공개판은 자물쇠, 요청 0. 404 는 '.msg'.
// 픽스처는 합성 OI 세계(tests/fixtures/synthetic/oi_world.py → tests/unit/api/web_fixtures_p7/gex.py).
import { describe, expect, it } from 'vitest';
import {
  contracts,
  headItems,
  labelChip,
  mountOi,
  OI_REFRESH_MS,
  OI_TITLES,
  optionRows,
  rowsOf,
  streakText,
  type FuturesOi,
} from '../../src/pages/p3/oi';
import { json } from '../helpers';
import { gexFixture, gexHarness } from '../fixtures/api/gex/harness';
import { flush, pageCtx, panelByTitle, text } from './support';

interface Env<T> {
  source: string;
  as_of: string;
  quality: string;
  notes: string[];
  data: T;
}

const NOW = new Date('2026-11-04T09:00:00Z'); // 18:00 KST — 합성 OI 세계 시계

async function mount(tier: 'login' | 'public', over: Record<string, (() => Response) | undefined> = {}) {
  const hz = gexHarness(over);
  const { ctx, root, every } = pageCtx(3, tier, hz.source, { now: () => NOW });
  await mountOi(ctx);
  await flush();
  return { hz, root, every };
}

describe('페이지 3 선물 미결제약정 — 로그인', () => {
  it('패널 둘(전체 폭) — 응답은 한 번(days=60), 60초마다 다시 읽는다', async () => {
    const { hz, root, every } = await mount('login');
    const titles = [...root.querySelectorAll('section.panel')].map((p) => [text(p.querySelector('h3 > span')), p.className]);
    expect(titles.map((t) => t[0])).toEqual([OI_TITLES.oi, OI_TITLES.opt]);
    for (const [, cls] of titles) expect(cls).toContain('span-12');
    expect(hz.paths()).toEqual(['gex/futures-oi?days=60']);
    expect(every.map((e) => e.ms)).toEqual([OI_REFRESH_MS]);
    every[0]?.fn();
    await flush(10);
    expect(hz.paths()).toHaveLength(2);
    expect(text(root)).not.toMatch(/NaN|undefined|null/);
  });

  it('맨 위 칸은 잠정 오늘(11/04) — 합계·증감·근월물·라벨·외국인 선물·연속일, 잠정 꼬리표', async () => {
    const { root } = await mount('login');
    const p = panelByTitle(root, OI_TITLES.oi);
    const head = text(p.querySelector('dl'));
    expect(head).toContain('11/04 (잠정)');
    expect(head).toContain('289,041계약');
    expect(head).toContain('+3,050계약 (+1.07%)');
    expect(head).toContain('558.06 (+0.55%)');
    expect(head).toContain('신규 매수 우위');
    expect(head).toContain('+1,800계약 · +2,520억');
    expect(head).toContain('3일 연속 매수');
    expect(p.querySelectorAll('dl .q-badge').length).toBeGreaterThan(0); // 잠정
    expect(p.querySelector('.oi-label.up')).not.toBeNull();
    expect(p.querySelector('.linechart svg')).not.toBeNull();
  });

  it('일별 표 — 잠정 오늘이 맨 위, 확정 11/03 다음(최근순, 10행씩)', async () => {
    const { root } = await mount('login');
    const p = panelByTitle(root, OI_TITLES.oi);
    const rows = [...p.querySelectorAll('tbody tr')].map((r) => text(r));
    expect(rows).toHaveLength(10);
    expect(rows[0]).toContain('11/04');
    expect(rows[1]).toContain('11/03');
    expect(rows[1]).toContain('285,991');
    expect(rows[1]).toContain('숏 커버 우위');
  });

  it('옵션 표 — 최근 날(11/04) 투자자 셋 × 월물·위클리 콜/풋(억원), 15:40 행은 잠정', async () => {
    const { root } = await mount('login');
    const p = panelByTitle(root, OI_TITLES.opt);
    expect(text(p)).toContain('11/04 기준');
    const rows = [...p.querySelectorAll('tbody tr')].map((r) => text(r));
    expect([...p.querySelectorAll('tbody tr')].map((r) => text(r.querySelector('td')))).toEqual(['외국인', '기관', '개인']);
    expect(rows[0]).toContain('-69'); // 외국인 월물 콜 −68.79억
    expect(p.querySelectorAll('tbody .q-badge').length).toBe(18);
  });

  it('아직 없음(404)은 두 패널 모두 .msg(작업 이름) — 오류 줄 아님', async () => {
    const nd = () => json({ code: 'no_data', message: '아직 없음 — gex.krx_derivatives(평일 08:05 — 전 거래일 확정)' }, 404);
    const { root } = await mount('login', { 'gex/futures-oi': nd });
    for (const t of Object.values(OI_TITLES)) {
      const p = panelByTitle(root, t);
      expect(text(p.querySelector('.msg:not(.err)'))).toContain('gex.krx_derivatives');
      expect(p.querySelector('.msg.err')).toBeNull();
    }
  });

  it('404 말고 실패(500)는 오류 줄 — 삼키지 않는다', async () => {
    const { root } = await mount('login', { 'gex/futures-oi': () => json({ code: 'internal', message: '오류' }, 500) });
    expect(panelByTitle(root, OI_TITLES.oi).querySelector('.msg.err')).not.toBeNull();
  });
});

describe('페이지 3 선물 미결제약정 — 공개', () => {
  it('공개판은 자물쇠만 — API 0회, 주기 작업도 걸지 않는다', async () => {
    const { hz, root, every } = await mount('public');
    expect(hz.paths()).toHaveLength(0);
    expect(every).toHaveLength(0);
    for (const t of Object.values(OI_TITLES)) expect(panelByTitle(root, t).classList.contains('locked')).toBe(true);
  });
});

describe('선물 OI 순수 도우미', () => {
  const d = gexFixture<Env<FuturesOi>>('futures_oi').data;

  it('보일 날 — 잠정 먼저, 확정 최근순, invalid 뺌', () => {
    const rows = rowsOf(d);
    expect(rows[0]?.provisional).toBe(true);
    expect(rows[1]?.trade_date).toBe('2026-11-03');
    const bad = { ...d, provisional: d.provisional ? { ...d.provisional, quality: 'invalid' as const } : null };
    expect(rowsOf(bad)[0]?.trade_date).toBe('2026-11-03');
  });

  it('글자 — 계약·연속일·라벨 꼬리표', () => {
    expect(contracts(3451)).toBe('+3,451계약');
    expect(contracts(null)).toBe('—');
    expect(streakText(3)).toBe('3일 연속 매수');
    expect(streakText(-2)).toBe('2일 연속 매도');
    expect(streakText(0)).toBe('—');
    const el = labelChip({ label: 'long_liquidation', label_text: '롱 청산 우위', reasons: ['가격↓·OI↓'] });
    expect(el.className).toContain('down');
    expect(el.getAttribute('title')).toBe('가격↓·OI↓');
  });

  it('맨 위 칸 — 확정 날은 잠정 꼬리표 없이, 값 없는 칸은 비운다', () => {
    const last = d.days.at(-1);
    if (!last) throw new Error('픽스처에 확정 행이 없다');
    const items = headItems({ ...last, oi_change: null, flows: [] }, null);
    expect(items.find((i) => i.label === '기준')?.quality).toBeNull();
    expect(items.find((i) => i.label === 'OI 증감')?.value).toBeNull();
    expect(items.find((i) => i.label === '외국인 선물')?.value).toBeNull();
  });

  it('옵션 행 — 최근 날만, 없는 칸은 undefined', () => {
    const o = optionRows(d);
    expect(o.day).toBe('2026-11-04');
    expect(o.rows[0]?.cells[0]?.net_value_won).toBe(-6_879_000_000);
    expect(optionRows({ ...d, options: [] }).day).toBeNull();
  });
});
