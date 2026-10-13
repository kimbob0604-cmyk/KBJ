// 페이지 1 '프로그램매매'(P5 묶음 F2) — 일별 60거래일 막대(상세·아래 줄)·상위 20 표(범위 꼬리표 — 장중/마감)·
// 방향·범위·시장 탭(쿼리·URL)·종목 → 페이지 8·아직 없음(확인 전 #40·#41)·공개판 자물쇠.
// 픽스처 = test/fixtures/api/program/{daily,top}_*.json(합성 p5_world).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { route } from '../../src/app/route';
import page from '../../src/pages/p1_market';
import { DAILY_TITLE, dayLine, TOP_TITLE } from '../../src/pages/p1/program';
import { apiHarness, buttonByText, click, flush, notFound, type Override, pageCtx, panelByTitle, publicHarness, text } from './support';

const FX = import.meta.glob<unknown>('../fixtures/api/program/*.json', { eager: true, import: 'default' });

// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- 시험이 픽스처 모양을 적어 받는다
function fx<T = Record<string, unknown>>(name: string): T {
  const v = FX[`../fixtures/api/program/${name}.json`];
  if (v === undefined) throw new Error(`픽스처 없음: program/${name}`);
  return structuredClone(v) as T;
}

interface DailyFx {
  data: { days: number; rows: { date: string; net: number | null; quality: string; index_close: number | null }[] };
}
interface TopFx {
  data: { scope_label: string; rows: { code: string; name: string | null }[] };
}

function param(url: string, key: string): string | null {
  return new URL(url, 'https://x').searchParams.get(key);
}

const topFx = (url: string): string => {
  const m = param(url, 'market') ?? 'K';
  const side = param(url, 'side') ?? 'buy';
  const scope = param(url, 'scope') ?? 'intraday';
  if (scope === 'close') return m === 'Q' ? 'top_Q_sell_close' : 'top_K_buy_close';
  return side === 'sell' ? 'top_K_sell_intraday' : 'top_K_buy_intraday';
};
const DEFAULTS: Record<string, Override> = {
  'flows_market-minute': () => new Response(JSON.stringify(fx('minute_k200_fut'))),
  program_daily: (url: string) => new Response(JSON.stringify(fx(`daily_${param(url, 'market') ?? 'K'}`))),
  program_top: (url: string) => new Response(JSON.stringify(fx(topFx(url)))),
};

async function mountLogin(over: Record<string, Override> = {}) {
  const hz = apiHarness({ ...DEFAULTS, ...over });
  const { ctx, root, every } = pageCtx(1, 'login', hz.source);
  await page.mount(ctx);
  await flush();
  return { hz, root, every, daily: panelByTitle(root, DAILY_TITLE), top: panelByTitle(root, TOP_TITLE) };
}

beforeEach(() => {
  window.history.replaceState(null, '', '/');
  route.reset();
});
afterEach(() => {
  window.history.replaceState(null, '', '/');
  route.reset();
});

describe('프로그램매매 — 로그인', () => {
  it('일별 60거래일 막대와 아래 줄(날짜·합계·비차익·차익·지수)', async () => {
    const f = fx<DailyFx>('daily_K');
    const { hz, daily } = await mountLogin();
    expect(hz.paths()).toContain('program/daily?days=60&market=K');
    expect(daily.querySelectorAll('svg rect')).toHaveLength(f.data.days);
    const last = f.data.rows.at(-1);
    expect(text(daily.querySelector('.f2-dayline'))).toBe(dayLine(last as never));
    expect(text(daily.querySelector('.f2-dayline'))).toMatch(/^2026-11-17 · 합계 .+ · 비차익 .+ · 차익 .+ · 지수 [\d,.]+$/);
    // 막대에 초점 → 차익·비차익·위탁·자기 상세
    const rect = daily.querySelector('svg rect');
    rect?.dispatchEvent(new Event('focus'));
    rect?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(text(daily.querySelector('.bars-detail'))).toMatch(/차익 .+ · 비차익 .+ · 위탁 .+ · 자기 /);
  });

  it('상위 표는 범위를 숨기지 않는다 — 장중 "거래대금 상위 N + 관심종목"', async () => {
    const f = fx<TopFx>('top_K_buy_intraday');
    const { hz, top } = await mountLogin();
    expect(hz.paths()).toContain('program/top?market=K&scope=intraday&side=buy');
    expect(text(top.querySelector('.f2-scope'))).toBe(`범위: ${f.data.scope_label}`);
    expect(f.data.scope_label).toMatch(/^거래대금 상위 \d+ \+ 관심종목$/);
    expect(top.querySelectorAll('tbody tr')).toHaveLength(f.data.rows.length);
    const heads = [...top.querySelectorAll('thead th')].map((th) => text(th));
    expect(heads).toEqual(['순위', '종목', '현재가', '등락', '순매수(억)', '매수(억)', '매도(억)']);
  });

  it('마감 범위 → "전 종목(마감 확정)", 방향 → 순매도', async () => {
    const { hz, top } = await mountLogin();
    click(buttonByText(top, '마감'));
    await flush();
    expect(hz.paths()).toContain('program/top?market=K&scope=close&side=buy');
    expect(text(top.querySelector('.f2-scope'))).toBe('범위: 전 종목(마감 확정)');
    expect(new URLSearchParams(window.location.search).get('pgc')).toBe('close');
    click(buttonByText(top, '순매도 상위'));
    await flush();
    expect(hz.paths()).toContain('program/top?market=K&scope=close&side=sell');
  });

  it('시장 탭은 두 패널이 함께 바꾼다', async () => {
    const { hz, daily, top } = await mountLogin();
    click(buttonByText(daily, '코스닥'));
    await flush();
    expect(hz.paths()).toContain('program/daily?days=60&market=Q');
    expect(hz.paths()).toContain('program/top?market=Q&scope=intraday&side=buy');
    expect(buttonByText(top, '코스닥').getAttribute('aria-pressed')).toBe('true');
    expect(new URLSearchParams(window.location.search).get('pgm')).toBe('Q');
  });

  it('URL 쿼리로 연 상태를 그대로 쓴다', async () => {
    window.history.replaceState(null, '', '/?pgm=Q&pgs=sell&pgc=close');
    const { hz } = await mountLogin();
    expect(hz.paths()).toContain('program/top?market=Q&scope=close&side=sell');
  });

  it('종목을 누르면 페이지 8', async () => {
    const go = vi.fn();
    route.bind({ go });
    const f = fx<TopFx>('top_K_buy_intraday');
    const { top } = await mountLogin();
    click(top.querySelector('tbody tr'));
    expect(route.getCode()).toBe(f.data.rows[0]?.code);
    expect(go).toHaveBeenCalledWith(8);
  });

  it('아직 없음(확인 전 #40·#41)은 패널 안내, 다른 패널은 계속', async () => {
    const { daily, top, root } = await mountLogin({
      program_daily: () => notFound('아직 없음 — program.daily 15:50(확인 전 #40)'),
    });
    expect(text(daily)).toContain('확인 전 #40');
    expect(top.querySelectorAll('tbody tr').length).toBeGreaterThan(0);
    expect(root.querySelectorAll('.msg.err')).toHaveLength(0);
  });
});

describe('프로그램매매 — 공개', () => {
  it('요청하지 않고 자물쇠', async () => {
    const hz = publicHarness();
    const { ctx, root } = pageCtx(1, 'public', hz.source);
    await page.mount(ctx);
    expect(hz.paths().some((x) => x.includes('program'))).toBe(false);
    expect(panelByTitle(root, DAILY_TITLE).classList.contains('locked')).toBe(true);
    expect(panelByTitle(root, TOP_TITLE).classList.contains('locked')).toBe(true);
  });
});
