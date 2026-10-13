// 페이지 2 '간밤 미국 신고가' 패널(P5 묶음 US) — 미국 거래일·출처·보류 수 표시, 종가·고가 기준 토글, 신고가·섹터 표,
// 404(아직 없음) 안내, 공개판 자물쇠. 픽스처 = test/fixtures/api/us/*.json(합성 p5_world — tests/unit/api/web_fixtures_p5/us.py).
import { describe, expect, it } from 'vitest';
import page from '../../src/pages/p2_board';
import { BASES, US_TITLE, usd, usDay } from '../../src/pages/p2/us';
import { apiHarness, buttonByText, click, flush, notFound, type Override, pageCtx, panelByTitle, publicHarness, text } from './support';

const US = import.meta.glob<unknown>('../fixtures/api/us/*.json', { eager: true, import: 'default' });

// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- 시험이 픽스처 모양을 적어 받는다
function us<T = Record<string, unknown>>(name: string): T {
  const v = US[`../fixtures/api/us/${name}.json`];
  if (v === undefined) throw new Error(`픽스처 없음: us/${name}`);
  return structuredClone(v) as T;
}

interface UsFx {
  source: string;
  notes: string[];
  data: {
    us_trade_date: string;
    basis: string;
    pending: number;
    summary_lines: string[];
    newhigh: { symbol: string; label: string | null }[];
    sectors: { sector: string; newhigh: number }[];
  };
}

const byBasis: Override = (url: string) =>
  new Response(JSON.stringify(us(url.includes('basis=high') ? 'board_high' : 'board_close')));

async function mountLogin(over: Record<string, Override> = {}) {
  const hz = apiHarness({ board_us: byBasis, ...over });
  const { ctx, root, every } = pageCtx(2, 'login', hz.source);
  await page.mount(ctx);
  await flush();
  return { hz, root, every, p: panelByTitle(root, US_TITLE) };
}

describe('간밤 미국 신고가 — 로그인', () => {
  it('미국 거래일(뉴욕 날짜)·출처·보류 수·한눈에 문장을 그린다', async () => {
    const fx = us<UsFx>('board_close');
    const { hz, p } = await mountLogin();
    expect(hz.paths()).toContain('board/us?basis=close');
    expect(text(p.querySelector('.us-head'))).toContain(`미국 ${usDay(fx.data.us_trade_date)} 마감`);
    expect(text(p.querySelector('.us-head'))).toContain('뉴욕 날짜');
    expect(text(p.querySelector('.us-head'))).toContain(`이력 부족 보류 ${String(fx.data.pending)}종목`);
    expect(text(p.querySelector('h3 .src'))).toContain(fx.source); // 출처
    const lis = [...p.querySelectorAll('.us-summary li')].map((li) => text(li));
    expect(lis).toEqual(fx.data.summary_lines);
    expect(text(p.querySelector('.notes'))).toContain('보류'); // 빠진 데이터를 숨기지 않는다
    expect(text(p.querySelector(':scope > .def'))).toContain('252거래일');
  });

  it('신고가 표 — 52주 먼저, 섹터 표는 신고가 있는 섹터만', async () => {
    const fx = us<UsFx>('board_close');
    const { p } = await mountLogin();
    const tables = p.querySelectorAll('table');
    const nh = tables[0];
    if (!nh) throw new Error('표 없음');
    expect(nh.querySelectorAll('tbody tr')).toHaveLength(fx.data.newhigh.length);
    expect(text(nh.querySelector('caption'))).toContain(`신고가 ${String(fx.data.newhigh.length)}종목`);
    expect(text(nh.querySelector('tbody tr'))).toContain(fx.data.newhigh[0]?.symbol ?? '?');
    const secs = fx.data.sectors.filter((s) => s.newhigh > 0);
    expect(tables[1]?.querySelectorAll('tbody tr')).toHaveLength(secs.length);
  });

  it('고가 기준 토글 → 쿼리·다른 해석', async () => {
    const { hz, p } = await mountLogin();
    click(buttonByText(p, '고가 기준'));
    await flush();
    expect(hz.paths()).toContain('board/us?basis=high');
    expect(text(p.querySelector('.us-head'))).toContain('고가 기준');
    const hi = us<UsFx>('board_high');
    const w52 = hi.data.newhigh.filter((r) => r.label === '52주').length;
    const cells = [...p.querySelectorAll('table')][0]?.querySelectorAll('tbody tr td:nth-child(2)') ?? [];
    expect([...cells].filter((c) => text(c) === '52주')).toHaveLength(w52);
  });

  it('아직 없으면 404 안내(작업·예정 시각)', async () => {
    const { p, root } = await mountLogin({ board_us: () => notFound('아직 없음 — us.eod 06:10(미국 마감 16:10 ET)') });
    expect(text(p.querySelector('.msg'))).toContain('us.eod');
    expect(panelByTitle(root, '신고가 종목').querySelectorAll('tbody tr').length).toBeGreaterThan(0); // 다른 패널은 그대로
  });
});

describe('간밤 미국 신고가 — 도우미·공개', () => {
  it('달러 금액·뉴욕 날짜 표기', () => {
    expect(usd(3.45e12)).toBe('$3.5T');
    expect(usd(2.5e11)).toBe('$250B');
    expect(usd(7.26e8)).toBe('$726M');
    expect(usd(null)).toBe('—');
    expect(usDay('2026-11-16')).toBe('11/16(월)');
    expect(BASES.map((b) => b.value)).toEqual(['close', 'high']);
  });

  it('공개판은 자물쇠만 — 요청 없음', async () => {
    const hz = publicHarness();
    const { ctx, root } = pageCtx(2, 'public', hz.source);
    await page.mount(ctx);
    expect(hz.calls).toHaveLength(0);
    const p = panelByTitle(root, US_TITLE);
    expect(p.classList.contains('locked')).toBe(true);
    expect(p.querySelector('table')).toBeNull();
  });
});
