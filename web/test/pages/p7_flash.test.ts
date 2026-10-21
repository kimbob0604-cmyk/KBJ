// 페이지 7 '속보' 탭(FLASH — docs/p6_design.md §1.3·§6, metrics §25): 관세청 10일 잠정치 4종 — 구간 표·발표 당시/최신·
// YoY·기여도·CSV. 로그인은 API(종류마다), 공개는 공개 파일 한 개(로그인 API 0회). 실패는 탭 안 한 줄.
import { describe, expect, it } from 'vitest';
import page from '../../src/pages/p7_trade';
import { flashCsv, rowsOf, segLabel, spanLabel, TITLE, usdEok, yoyLines } from '../../src/pages/p7/flash';
import type { TradeFlash } from '../../src/pages/p7/common';
import { BOM } from '../../src/ui/csv';
import { json } from '../helpers';
import { FLASH_NOW, flashFixture, flashLoginHarness, flashPublicHarness } from '../fixtures/api/trade_flash/harness';
import { buttonByText, flush, pageCtx, panelByTitle, text } from './support';

interface Env<T> {
  source: string;
  as_of: string;
  quality: 'ok' | 'stale' | 'estimated' | 'invalid';
  notes: string[];
  generated_at: string;
  data: T;
}

async function mount(tier: 'login' | 'public', over: Record<string, (() => Response) | undefined> = {}) {
  const hz = tier === 'login' ? flashLoginHarness(over) : flashPublicHarness(over);
  const { ctx, root } = pageCtx(7, tier, hz.source, { now: () => FLASH_NOW });
  await page.mount(ctx);
  await flush();
  // 첫 탭은 '종목'(P10 — 2026-10-21) — 속보 탭을 열고, 경로는 속보 것만 센다
  const tab = [...root.querySelectorAll<HTMLElement>('[role="tab"]')].find((b) => b.textContent === '속보');
  tab?.click();
  await flush();
  const paths = (): string[] => hz.paths().filter((x) => x.includes('flash'));
  return { hz: { ...hz, paths }, root };
}

const bodyRows = (root: ParentNode): HTMLTableRowElement[] => [...root.querySelectorAll<HTMLTableRowElement>('tbody tr')];

describe('페이지 7 속보 — 로그인', () => {
  it('수출 품목을 먼저 불러 최신 구간 표(열 00~10)·막대·선을 그린다', async () => {
    const { hz, root } = await mount('login');
    expect(hz.paths()).toHaveLength(1);
    expect(hz.paths()[0]).toMatch(/^trade\/flash\?/);
    expect(hz.paths()[0]).toContain('kind=exp_item');
    const p = panelByTitle(root, TITLE);
    expect(text(p.querySelector('.flash-head'))).toContain('수출 품목 2026-12 1~10일');
    expect(text(p.querySelector('.flash-head'))).toContain('+5.5%');
    expect(bodyRows(p)).toHaveLength(11);
    expect(text(bodyRows(p)[0])).toContain('전체(합계)');
    expect(p.querySelector('svg')).not.toBeNull();
    expect(text(p)).not.toMatch(/NaN|undefined/);
    expect(text(p.querySelector('.src'))).toContain('관세청');
  });

  it('종류를 바꾸면 그 종류만 한 번 부른다(다시 고르면 캐시)', async () => {
    const { hz, root } = await mount('login');
    const p = panelByTitle(root, TITLE);
    buttonByText(p, '수출 국가').click();
    await flush();
    expect(hz.paths().filter((x) => x.includes('kind=exp_country'))).toHaveLength(1);
    expect(text(p.querySelector('.flash-head'))).toContain('수출 국가');
    buttonByText(p, '수출 품목').click();
    await flush();
    buttonByText(p, '수출 국가').click();
    await flush();
    expect(hz.paths()).toHaveLength(2);
  });

  it('아직 없음(404)은 탭 안 한 줄 — 다른 탭은 그대로', async () => {
    const { root } = await mount('login', {
      'trade/flash': () => json({ code: 'no_data', message: '아직 없음 — trade.customs_tenday 1·11·21일' }, 404),
    });
    const p = panelByTitle(root, TITLE);
    expect(text(p.querySelector('.msg:not(.err)'))).toContain('아직 없음');
    expect(root.querySelectorAll('[role="tab"]')).toHaveLength(9);
  });

  it('404 말고 실패(500)는 오류 줄로 남는다 — 삼키지 않는다', async () => {
    const { root } = await mount('login', {
      'trade/flash': () => json({ code: 'internal', message: '저장소 오류' }, 500),
    });
    expect(panelByTitle(root, TITLE).querySelector('.msg.err')).not.toBeNull();
  });
});

describe('페이지 7 속보 — 공개', () => {
  it('공개 파일 하나(trade/flash)만 — 로그인 API 0, 종류를 바꿔도 다시 받지 않는다', async () => {
    const { hz, root } = await mount('public');
    expect(hz.paths()).toEqual(['./data/trade/flash.json']);
    const p = panelByTitle(root, TITLE);
    expect(text(p.querySelector('.flash-head'))).toContain('수출 품목 2026-12 1~10일');
    buttonByText(p, '수입 국가').click();
    await flush();
    expect(hz.paths()).toEqual(['./data/trade/flash.json']);
    expect(text(p.querySelector('.flash-head'))).toContain('수입 국가');
    expect(text(p)).toContain('공개판은 최근 6개월');
    expect(text(p)).not.toMatch(/NaN|undefined/);
  });
});

describe('속보 표·CSV(순수 함수)', () => {
  const item = flashFixture<Env<TradeFlash>>('flash_exp_item');
  const country = flashFixture<Env<TradeFlash>>('flash_exp_country');

  it('정정된 칸은 발표 당시 ≠ 최신 + 비고 정정', () => {
    const s = item.data.segments.find((x) => x.ym === '2026-11' && x.seg === 3);
    if (!s) throw new Error('2026-11 월 전체 구간 없음');
    const r = rowsOf(s)[0];
    expect(r?.usd).not.toBe(r?.first_usd);
    expect((r?.usd ?? 0) - (r?.first_usd ?? 0)).toBe(120_000_000);
    expect(r?.note).toContain('정정');
  });

  it('열 이름이 바뀐 해는 YoY 없음 + 사유(0 으로 그리지 않는다)', () => {
    const s = country.data.segments.find((x) => x.ym === '2025-12' && x.seg === 3);
    if (!s) throw new Error('2025-12 구간 없음');
    const r = rowsOf(s).find((x) => x.col_no === 10);
    expect(r?.yoy).toBeNull();
    expect(r?.note).toContain('열 이름 바뀜');
  });

  it('CSV 는 BOM·출처 줄·이스케이프된 본문', () => {
    const csv = flashCsv(item, '수출 품목');
    expect(csv?.startsWith(BOM)).toBe(true);
    expect(csv).toContain('열 번호,이름,최신(달러)');
    expect(csv).toContain('출처,관세청(공공데이터포털)');
    expect(csv?.split('\r\n')[1]).toMatch(/^0,전체,\d+,\d+,/);
  });

  it('구간 이름·금액·선 모양', () => {
    expect(spanLabel('01~31')).toBe('1~31일');
    expect(segLabel({ ym: '2026-12', span_text: '01~10' })).toBe('2026-12 1~10일');
    expect(usdEok(22_622_970_000)).toBe('226.2억 달러');
    expect(usdEok(null)).toBe('—');
    const l = yoyLines(item.data.segments);
    expect(l.series.map((x) => x.label)).toEqual(['1~10일', '1~20일', '월 전체']);
    expect(l.x).toEqual([...l.x].sort());
    expect(l.series[1]?.values.at(-1)).toBeNull(); // 2026-12 1~20일은 아직 없음 — 0 이 아니다
  });
});
