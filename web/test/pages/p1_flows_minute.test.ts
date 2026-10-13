// 페이지 1 '장중 투자자 수급'(P5 묶음 F2) — 카드 7(사모 제외)·선 토글(기본 개인·외국인·기관)·끊긴 선(빈 분)·머리 꼬리표
// (수집 HH:MM · 60초 갱신 · 잠정)·거래소 잠금·확인 전/장 시작 전/휴장/아직 없음 화면·탭과 토글의 URL 쿼리·60초 갱신·공개판 자물쇠.
// 픽스처 = test/fixtures/api/program/minute_*.json(합성 p5_world — tests/unit/api/web_fixtures_p5/program.py).
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import page from '../../src/pages/p1_market';
import { DEFAULT_TAB, MINUTE_REFRESH_MS, MINUTE_TITLE, minuteTag } from '../../src/pages/p1/flows_minute';
import { eok } from '../../src/ui/fmt';
import { apiHarness, buttonByText, click, flush, notFound, type Override, pageCtx, panelByTitle, publicHarness, text } from './support';

const FX = import.meta.glob<unknown>('../fixtures/api/program/*.json', { eager: true, import: 'default' });

// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- 시험이 픽스처 모양을 적어 받는다
function fx<T = Record<string, unknown>>(name: string): T {
  const v = FX[`../fixtures/api/program/${name}.json`];
  if (v === undefined) throw new Error(`픽스처 없음: program/${name}`);
  return structuredClone(v) as T;
}

interface MinuteFx {
  notes: string[];
  data: {
    status: string;
    venue: string;
    axis: string[];
    collected_at: string | null;
    refresh_s: number;
    provisional: boolean;
    trade_date: string;
    lines: { investor: string; label: string; default_on: boolean; values: (number | null)[] }[];
    cards: { investor: string; label: string; net_value: number | null }[];
  };
}

const MINUTE_BY_TAB: Record<string, string> = {
  kospi: 'minute_kospi',
  kosdaq: 'minute_kosdaq_off',
  k200_fut: 'minute_k200_fut',
  k200_call: 'minute_k200_fut',
  k200_put: 'minute_k200_fut',
};

function param(url: string, key: string): string | null {
  return new URL(url, 'https://x').searchParams.get(key);
}

const minute = (override?: string): Override => (url: string) =>
  new Response(JSON.stringify(fx(override ?? MINUTE_BY_TAB[param(url, 'market') ?? ''] ?? 'minute_kosdaq_off')));
const program: Record<string, Override> = {
  program_daily: (url: string) => new Response(JSON.stringify(fx(`daily_${param(url, 'market') ?? 'K'}`))),
  program_top: () => new Response(JSON.stringify(fx('top_K_buy_intraday'))),
};

async function mountLogin(over: Record<string, Override> = {}) {
  const hz = apiHarness({ 'flows_market-minute': minute(), ...program, ...over });
  const { ctx, root, every } = pageCtx(1, 'login', hz.source);
  await page.mount(ctx);
  await flush();
  return { hz, root, every, p: panelByTitle(root, MINUTE_TITLE) };
}

beforeEach(() => {
  window.history.replaceState(null, '', '/');
});
afterEach(() => {
  window.history.replaceState(null, '', '/');
});

describe('장중 투자자 수급 — 로그인', () => {
  it('기본 탭(코스피200 선물)을 부르고 카드 7·선 12·머리 꼬리표를 그린다', async () => {
    const f = fx<MinuteFx>('minute_k200_fut');
    const { hz, p } = await mountLogin();
    expect(DEFAULT_TAB).toBe('k200_fut');
    expect(hz.paths()).toContain('flows/market-minute?market=k200_fut');
    const cards = [...p.querySelectorAll('.f2-card')];
    expect(cards.map((c) => c.querySelector('span')?.textContent)).toEqual([
      '개인',
      '외국인',
      '기관 합계',
      '금융투자',
      '투신',
      '연기금',
      '기타법인',
    ]);
    expect(cards.map((c) => (c as HTMLElement).dataset.investor)).not.toContain('pe_fund'); // 투신은 사모 제외
    const frgn = f.data.cards.find((c) => c.investor === 'frgn');
    expect(text(cards[1])).toContain(eok(frgn?.net_value, 0, true));
    const head = text(p.querySelector('.f2-head'));
    expect(head).toContain(minuteTag(f.data as never));
    expect(head).toContain('수집 13:00 · 60초 갱신 · 잠정');
    expect(head).toContain('KRX(잠금)');
    const legend = [...p.querySelectorAll('.legend button')];
    expect(legend).toHaveLength(12);
    const on = legend.filter((b) => b.getAttribute('aria-pressed') === 'true').map((b) => text(b));
    expect(on).toEqual(['개인', '외국인', '기관']); // 기관 세부·기타법인은 기본 끔
    expect(p.querySelectorAll('svg [data-series]')).toHaveLength(3);
    expect(text(p.querySelector('h3 .src'))).toContain('KIS');
    expect(p.querySelector('.msg.err')).toBeNull();
  });

  it('빈 분은 선을 끊는다(잇지 않음) — 코스피 10:00~10:04', async () => {
    window.history.replaceState(null, '', '/?f2m=kospi');
    const f = fx<MinuteFx>('minute_kospi');
    const { hz, p } = await mountLogin();
    expect(hz.paths()).toContain('flows/market-minute?market=kospi');
    const frgn = f.data.lines.find((l) => l.investor === 'frgn');
    const i = f.data.axis.indexOf('10:00');
    expect(frgn?.values.slice(i, i + 5)).toEqual([null, null, null, null, null]);
    const g = p.querySelector('svg [data-series="1"]');
    // 09:00~09:59 · 10:05~13:00 두 구간 → 선 조각 2(보간이면 1)
    expect(g?.querySelectorAll('path[data-run]').length).toBe(2);
    expect(text(p.querySelector('.notes'))).toContain('빈 분 5개');
  });

  it('선 토글은 URL 쿼리에 남고, 다시 열면 그 선이 켜진다', async () => {
    const { p } = await mountLogin();
    click(buttonByText(p, '금융투자'));
    expect(new URLSearchParams(window.location.search).get('f2l')).toBe('prsn,frgn,orgn,scrt');
    expect(p.querySelectorAll('svg [data-series]')).toHaveLength(4);
    const again = await mountLogin();
    const on = [...again.p.querySelectorAll('.legend button')]
      .filter((b) => b.getAttribute('aria-pressed') === 'true')
      .map((b) => text(b));
    expect(on).toEqual(['개인', '외국인', '기관', '금융투자']);
  });

  it('시장 탭 → 쿼리·URL', async () => {
    const { hz, p } = await mountLogin();
    click(buttonByText(p, '콜옵션'));
    await flush();
    expect(hz.paths()).toContain('flows/market-minute?market=k200_call');
    expect(new URLSearchParams(window.location.search).get('f2m')).toBe('k200_call');
  });

  it('확인 전(꺼진 조합)·장 시작 전·휴장일·아직 없음 화면이 각각 다르다', async () => {
    const off = await mountLogin({ 'flows_market-minute': minute('minute_kosdaq_off') });
    expect(off.p.querySelector('.f2-state')?.getAttribute('data-status')).toBe('not_confirmed');
    expect(text(off.p)).toContain('확인 전');
    expect(off.p.querySelector('.linechart')).toBeNull(); // 값을 지어내지 않는다

    const pre = await mountLogin({ 'flows_market-minute': minute('minute_before_open') });
    expect(pre.p.querySelector('.f2-state')?.getAttribute('data-status')).toBe('before_open');
    expect(text(pre.p)).toContain('장 시작 전');
    expect(text(pre.p.querySelector('.f2-head'))).not.toContain('잠정');
    expect(pre.p.querySelector('svg')?.getAttribute('data-empty')).toBe('true');

    const hol = await mountLogin({ 'flows_market-minute': minute('minute_holiday') });
    expect(hol.p.querySelector('.f2-state')?.getAttribute('data-status')).toBe('holiday');
    expect(text(hol.p)).toContain('휴장일');

    const none = await mountLogin({ 'flows_market-minute': () => notFound('아직 없음 — flows.market_minute 09:00~15:40 매분') });
    expect(text(none.p)).toContain('아직 없음 — flows.market_minute');
  });

  it('60초 갱신을 따로 건다(웹소켓 아님)', async () => {
    const { hz, every } = await mountLogin();
    expect(every.map((e) => e.ms)).toContain(MINUTE_REFRESH_MS);
    const before = hz.paths().filter((x) => x.startsWith('flows/market-minute')).length;
    every.find((e) => e.ms === MINUTE_REFRESH_MS)?.fn();
    await flush();
    expect(hz.paths().filter((x) => x.startsWith('flows/market-minute')).length).toBe(before + 1);
  });
});

describe('장중 투자자 수급 — 공개', () => {
  it('요청하지 않고 자물쇠', async () => {
    const hz = publicHarness();
    const { ctx, root } = pageCtx(1, 'public', hz.source);
    await page.mount(ctx);
    expect(hz.paths().some((x) => x.includes('market-minute'))).toBe(false);
    expect(panelByTitle(root, MINUTE_TITLE).classList.contains('locked')).toBe(true);
  });
});
