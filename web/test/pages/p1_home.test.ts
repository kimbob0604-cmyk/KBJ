// 페이지 1 첫 화면 요약 카드(`home` 묶음 — ADR 0043) — 합성 픽스처 home.json(일부 no_data)·home_full.json(모든 카드 값)
// ·home_live.json(장중)으로 배치·카드 상태(ok·no_data·error)·격리·관심종목·탭·종목 → 페이지 8·공개 자물쇠.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { route } from '../../src/app/route';
import type { Envelope } from '../../src/data/source';
import page from '../../src/pages/p1_market';
import { marketStampText, stampText } from '../../src/pages/p1/home';
import { SIGNAL_CARDS } from '../../src/pages/p1/home_defs';
import { cardErrorText, type Home } from '../../src/pages/p1/home_ui';
import watchMain from '../fixtures/api/watchlist/main.json';
import { json } from '../helpers';
import { apiHarness, buttonByText, click, fixture, flush, pageCtx, panelByTitle, publicHarness, text } from './support';

const F2_FX = import.meta.glob<unknown>('../fixtures/api/program/*.json', { eager: true, import: 'default' });
const F2 = {
  'flows_market-minute': F2_FX['../fixtures/api/program/minute_k200_fut.json'] as object,
  program_daily: F2_FX['../fixtures/api/program/daily_K.json'] as object,
  program_top: F2_FX['../fixtures/api/program/top_K_buy_intraday.json'] as object,
};

type HomeFx = Envelope<Home> & { generated_at: string };
type CardFx = { status: string; error?: string | null; message?: string | null; data: unknown };

async function mountLogin(over: Parameters<typeof apiHarness>[0] = {}) {
  const hz = apiHarness({ ...F2, home: fixture('home_full'), ...over });
  const { ctx, root, every } = pageCtx(1, 'login', hz.source);
  await page.mount(ctx);
  await flush();
  return { hz, root, every };
}

function card(root: ParentNode, key: string): HTMLElement {
  const el = root.querySelector<HTMLElement>(`.hcard[data-card="${key}"]`);
  if (!el) throw new Error(`카드 없음: ${key}`);
  return el;
}

beforeEach(() => {
  route.reset();
});
afterEach(() => {
  route.reset();
});

describe('첫 화면 배치', () => {
  it('시장(지수 카드 3 + 띠 → 업종 지도) → 오늘의 신호(카드 13) → 자세히(원래 패널) 순서', async () => {
    const { root } = await mountLogin();
    const order = [...root.children].map((el) =>
      el.classList.contains('hsec')
        ? `#${el.getAttribute('data-section') ?? ''}`
        : el.classList.contains('hgrid')
          ? `[${el.classList.contains('hmarket') ? 'market' : 'signals'}]`
          : (el.querySelector('h3 > span')?.textContent ?? '?'),
    );
    expect(order.slice(0, 6)).toEqual(['#시장', '[market]', '업종 히트맵', '#오늘의 신호', '[signals]', '#자세히']);
    // 자세히 구역 — 원래 패널이 모두 남는다(기능을 지우지 않는다)
    for (const t of ['지수', '시장폭', '시장 거래대금', '시장 투자자 수급', '장중 투자자 수급', '프로그램 순매수(일별)', '프로그램 순매수 상위']) {
      expect(order.slice(6)).toContain(t);
    }
    const mk = [...root.querySelectorAll<HTMLElement>('.hmarket > .hcard')].map((c) => c.dataset.card);
    expect(mk).toEqual(['kospi', 'kosdaq', 'futures', 'band']);
    const sig = [...root.querySelectorAll<HTMLElement>('.hsignals > .hcard')].map((c) => c.dataset.card);
    expect(sig).toEqual(SIGNAL_CARDS.map((c) => c.key));
    expect(card(root, 'filings').classList.contains('wide')).toBe(true);
    expect(root.querySelector('.hsignals')?.getAttribute('aria-label')).toBe('오늘의 신호 카드');
  });

  it("카드마다 '더 보기 ›'(#/p/N) — 페이지 1 카드는 같은 쪽 패널로 내려가는 '자세히 ›' 단추", async () => {
    const { root } = await mountLogin();
    for (const c of SIGNAL_CARDS) {
      const more = card(root, c.key).querySelector('.hmore');
      if (c.page === 1) {
        expect(more?.tagName).toBe('BUTTON');
        expect(more?.textContent).toBe('자세히 ›');
      } else {
        expect(more?.getAttribute('href')).toBe(`#/p/${c.page}`);
        expect(more?.textContent).toBe('더 보기 ›');
      }
    }
    // 시장 카드: 지수·띠는 같은 쪽 '지수'·'시장폭' 패널로, 선물은 페이지 3(GEX)
    expect(card(root, 'kospi').querySelector('.hmore')?.textContent).toBe('자세히 ›');
    expect(card(root, 'band').querySelector('.hmore')?.textContent).toBe('자세히 ›');
    expect(card(root, 'futures').querySelector('.hmore')?.getAttribute('href')).toBe('#/p/3');
    // jsdom 에는 scrollIntoView 가 없다 — 눌러도 오류가 나지 않는다
    click(card(root, 'breadth').querySelector('.hmore'));
  });
});

describe('첫 화면 카드 — 모든 카드 값(home_full)', () => {
  it('요청은 묶음 한 번 + 관심종목 + 업종 지도(카드마다 따로 부르지 않는다)', async () => {
    const { hz } = await mountLogin();
    const p = hz.paths();
    expect(p.filter((x) => x === 'home')).toHaveLength(1);
    expect(p).toContain('watchlist');
    expect(p).not.toContain('market/movers');
    expect(p.filter((x) => x.startsWith('flows/screen') || x.startsWith('board/') || x.startsWith('gex'))).toEqual([]);
  });

  it('카드 오류 0 · NaN/undefined 없음 · 구역 도장', async () => {
    const { root } = await mountLogin();
    const cards = root.querySelectorAll('.hcard');
    expect(cards).toHaveLength(4 + SIGNAL_CARDS.length);
    expect(root.querySelectorAll('.hcard .msg.err')).toHaveLength(0);
    for (const c of cards) expect(text(c)).not.toMatch(/NaN|undefined|Infinity/);
    expect(text(root.querySelector('[data-section="오늘의 신호"] .hstamp'))).toContain('카드 14/14');
    expect(text(root.querySelector('[data-section="시장"] .hstamp'))).toContain('지수 기준');
  });

  it('지수 카드: 큰 값·등락률 알약·마감 기준·당일 차트·시장별 상승·하락·투자자 칸(개인·외국인·기관·프로그램)', async () => {
    const { root } = await mountLogin();
    const k = card(root, 'kospi');
    expect(text(k.querySelector('h3 > span'))).toBe('코스피');
    expect(text(k.querySelector('.hbig .hv'))).toBe('20,050.76');
    expect(k.querySelector('.hbig .pill.down')).not.toBeNull();
    expect(text(k.querySelector('.hmeta'))).toBe('09/15 마감');
    // 당일 = 묶음의 장중 10분 점(기본 기간)
    expect(k.querySelector('.hchart svg[role="img"]')?.getAttribute('aria-label')).toBe('코스피 당일 10분 추이');
    expect(text(k.querySelector('.hchart + .seg + .hsub'))).toMatch(/^당일 10분 · 11\/17 09:00~/);
    expect(k.querySelector('.seg [aria-pressed="true"]')?.textContent).toBe('당일');
    // 당일 점이 없는 코스닥은 20거래일 추이로 대신하고 그렇게 적는다
    const q = card(root, 'kosdaq');
    expect(q.querySelector('.hchart svg')?.getAttribute('aria-label')).toBe('코스닥 20거래일 추이');
    expect(text(q.querySelector('.hchart + .seg + .hsub'))).toContain('당일 10분 값 없음');
    // 그 시장 상승·보합·하락(마감)
    expect(text(k.querySelector('[data-updown]'))).toBe('상승 7 · 보합 1 · 하락 11 · 09/15 마감');
    expect(k.querySelector('[data-updown] .up')).not.toBeNull();
    const inv = [...k.querySelectorAll('.hinv dt')].map((x) => x.textContent);
    expect(inv).toEqual(['개인', '외국인', '기관', '프로그램']);
    expect(text(k.querySelector('.hinv-cap'))).toContain('장중 누적');
    // 장중 누적(잠정) 값에는 잠정 배지
    expect(k.querySelector('.hinv .q-badge')).not.toBeNull();
    // 상승·보합·하락 메모는 띠 카드로
    expect(text(k.querySelector('.notes'))).not.toContain('상승·보합·하락');
    expect(text(card(root, 'band').querySelector('.notes'))).toContain('상승·보합·하락');
  });

  it('선물 카드: 값·세션·전일 정산가 대비 등락률, 정산가가 없으면 — (지어내지 않는다)', async () => {
    const { root } = await mountLogin();
    const f = card(root, 'futures');
    expect(text(f.querySelector('.hbig .hv'))).toBe('351.46잠정'); // 엔진 F_ref 없음 — 잠정 배지
    const pill = f.querySelector<HTMLElement>('.hbig .pill');
    expect(text(pill)).toBe('+0.70%잠정');
    expect(pill?.classList.contains('up')).toBe(true);
    expect(pill?.title).toBe('전일(12/09) 정산가 349.00pt 대비');
    expect(text(f.querySelector('.hmeta'))).toContain('주간');
    expect(text(f.querySelector('.hmeta'))).toContain('갱신 지연');
    expect(text(f.querySelector('.notes'))).toContain('KRX 정산가 대비');

    const fx = fixture<HomeFx>('home_full');
    const fut = (fx.data.cards as unknown as Record<string, CardFx>).futures;
    Object.assign(fut?.data ?? {}, { chg_pct: null, prev_settle: null, prev_settle_date: null });
    const { root: r2 } = await mountLogin({ home: fx });
    const p2 = card(r2, 'futures').querySelector<HTMLElement>('.hbig .pill');
    expect(text(p2)).toBe('—');
    expect(p2?.title).toContain('정산가가 아직 없다');
  });

  it('상승·보합·하락 띠: 종목 수 그대로, 색 막대 + 글자', async () => {
    const { root } = await mountLogin();
    const b = card(root, 'band');
    expect(b.classList.contains('wide')).toBe(true);
    expect(b.querySelector('.hband')?.getAttribute('role')).toBe('img');
    expect(text(b.querySelector('.hband-txt'))).toMatch(/^상승 \d+ · 보합 \d+ · 하락 \d+/);
  });

  it('공시 카드: 건수·잠정실적 서프/쇼크 줄 + 키워드 상위 줄, 종목 이름 단추', async () => {
    const { root } = await mountLogin();
    const c = card(root, 'filings');
    expect(text(c.querySelector('.hmeta'))).toContain('18건');
    expect(c.querySelectorAll('.hfil li[data-side]').length).toBeGreaterThan(0);
    expect(c.querySelectorAll('.hfil li[data-rcept]').length).toBeGreaterThan(c.querySelectorAll('li[data-side]').length);
    expect(c.querySelector('.hfil button[data-open]')).not.toBeNull();
    expect(c.querySelector('.hfil a[href^="https://dart.fss.or.kr/"]')).not.toBeNull();
  });

  it('급등·급락 탭: 상승이 먼저, 하락 탭을 누르면 하락 순위 — 고른 탭은 갱신 뒤에도 남는다', async () => {
    const { root, every } = await mountLogin();
    const m = card(root, 'movers');
    expect(text(m.querySelector('.hmeta'))).toContain('시총 3,000억 이상');
    expect(m.querySelector('.hlist')?.getAttribute('aria-label')).toBe('급등 순위');
    expect(m.querySelectorAll('.hlist li')).toHaveLength(9); // 합성 세계: 오른 종목 9(나머지는 내림·보합)
    expect(text(m.querySelector('.hlist li .rk'))).toBe('1');
    click(buttonByText(m, '하락 10'));
    expect(m.querySelector('.hlist')?.getAttribute('aria-label')).toBe('급락 순위');
    expect(m.querySelector('.hlist .pill.down')).not.toBeNull();
    // 슬롯 갱신(다시 그림) 뒤에도 하락 탭
    every[0]?.fn();
    await flush();
    expect(card(root, 'movers').querySelector('.hlist')?.getAttribute('aria-label')).toBe('급락 순위');
  });

  it('종목 이름 → 페이지 8(openStock)', async () => {
    const go = vi.fn();
    route.bind({ go });
    const { root } = await mountLogin();
    const btn = card(root, 'spike').querySelector<HTMLButtonElement>('.hlist button[data-open]');
    expect(btn?.dataset.open).toBe('Q00000');
    click(btn);
    expect(go).toHaveBeenCalledWith(8);
    expect(route.getCode()).toBe('Q00000');
  });

  it('외국인 + 기관 탭, 거래대금 급증 배수, GEX·진단·ETF·수출·테마·프로그램 값', async () => {
    const { root } = await mountLogin();
    const fi = card(root, 'foreign_inst');
    expect(fi.querySelector('.hlist')?.getAttribute('aria-label')).toBe('외국인 순매수 순위');
    click(buttonByText(fi, '기관'));
    expect(fi.querySelector('.hlist')?.getAttribute('aria-label')).toBe('기관 순매수 순위');
    expect(text(card(root, 'spike').querySelector('.mult'))).toBe('3.0배');
    expect(text(card(root, 'gex').querySelector('.hbig'))).toContain('Long γ');
    expect(text(card(root, 'gex').querySelector('.hkv'))).toContain('±4.20pt');
    expect(text(card(root, 'diag').querySelector('.hkv'))).toContain('54.7');
    expect(card(root, 'etf').querySelectorAll('.hlist').length).toBeGreaterThan(0);
    expect(text(card(root, 'export').querySelector('.hmeta'))).toContain('2026-12 1~10일');
    expect(card(root, 'themes').querySelectorAll('.hlist li').length).toBeGreaterThan(0);
    const pg = card(root, 'program');
    expect([...pg.querySelectorAll('.hlist li')].map((li) => li.getAttribute('data-market'))).toEqual(['코스피', '코스닥']);
    expect(text(pg.querySelector('.hmeta'))).toBe('11/17 마감 · 시장 순매수');
    expect([...pg.querySelectorAll('.hlist li')].map((li) => li.getAttribute('data-basis'))).toEqual(['daily', 'daily']);
  });

  it('시장 폭: 합산 20일선 + 시장별 20·60·120일선 칸(분모 0 은 —)', async () => {
    const { root } = await mountLogin();
    const b = card(root, 'breadth');
    expect(text(b.querySelector('.hbig small'))).toContain('코스피+코스닥 20일선 위');
    const k = b.querySelector('[data-market="KOSPI"]');
    expect([...(k?.querySelectorAll('dt') ?? [])].map((x) => x.textContent)).toEqual(['20일선', '60일선', '120일선']);
    expect([...(k?.querySelectorAll('dd') ?? [])].map((x) => x.textContent)).toEqual(['55.6%', '—', '—']);
    expect(k?.querySelector('[data-ma="20"]')?.getAttribute('title')).toBe('10 / 18종목');
    expect(text(b.querySelector('[data-market="KOSDAQ"] .hsub'))).toContain('코스닥');
  });

  it('지수 차트 3개월·1년: 처음 누를 때 index-chart 한 번(250일) — 3개월은 마지막 63일, 고른 기간은 갱신 뒤에도 남는다', async () => {
    const chart = fixture<Envelope<{ points: { date: string; close: number }[] }>>('market_index-chart');
    const hz = apiHarness({ ...F2, home: fixture('home_full'), 'market_index-chart': chart });
    let t = new Date('2026-11-10T01:00:00Z');
    const { ctx, root, every } = pageCtx(1, 'login', hz.source, { now: () => t });
    await page.mount(ctx);
    await flush();
    const k = card(root, 'kospi');
    expect(hz.paths().filter((x) => x.startsWith('market/index-chart'))).toEqual([]);
    click(buttonByText(k, '3개월'));
    await flush();
    expect(hz.paths().filter((x) => x.startsWith('market/index-chart'))).toEqual(['market/index-chart?code=0001&days=250']);
    const n = Math.min(63, chart.data.points.length);
    expect(k.querySelector('.hchart svg')?.getAttribute('aria-label')).toBe(`코스피 ${String(n)}거래일 추이`);
    expect(text(k.querySelector('.hchart + .seg + .hsub'))).toMatch(new RegExp(`^3개월 · ${String(n)}거래일 · `));
    click(buttonByText(k, '1년'));
    await flush();
    const all = chart.data.points.length;
    expect(k.querySelector('.hchart svg')?.getAttribute('aria-label')).toBe(`코스피 ${String(all)}거래일 추이`);
    expect(hz.paths().filter((x) => x.startsWith('market/index-chart'))).toHaveLength(1); // 지수마다 한 번
    // 갱신(묶음 다시 그림) 뒤에도 고른 기간 그대로 — 차트는 다시 부르지 않는다
    const before = k.querySelector('.hchart svg');
    t = new Date(t.getTime() + 10 * 60_000); // 슬롯 경계를 넘긴다
    for (const e of every) e.fn();
    await flush();
    const k2 = card(root, 'kospi');
    expect(k2.querySelector('.hchart svg')).not.toBe(before); // 다시 그렸다
    expect(k2.querySelector('.seg [aria-pressed="true"]')?.textContent).toBe('1년');
    expect(k2.querySelector('.hchart svg')?.getAttribute('aria-label')).toBe(`코스피 ${String(all)}거래일 추이`);
    expect(hz.paths().filter((x) => x.startsWith('market/index-chart'))).toHaveLength(1);
  });

  it('지수 차트 요청이 실패하면 차트 자리에만 문구 — 카드 값은 그대로', async () => {
    const { root } = await mountLogin({ 'market_index-chart': () => json({ code: 'internal', message: '서버 오류' }, 500) });
    const k = card(root, 'kospi');
    click(buttonByText(k, '1년'));
    await flush();
    expect(text(k.querySelector('.hchart + .seg + .hsub'))).toContain('차트를 불러오지 못함');
    expect(text(k.querySelector('.hbig .hv'))).toBe('20,050.76');
    expect(k.querySelector('.hinv')).not.toBeNull();
  });

  it('프로그램: 장중 누적이 새 날이면 장중 누적(잠정 배지) — 지수 카드 프로그램 칸도 같은 값', async () => {
    const fx = fixture<HomeFx>('home_full');
    const pg = (fx.data.cards as unknown as Record<string, CardFx>).program;
    const data = (pg?.data ?? {}) as Record<string, unknown>;
    data.kospi_now = {
      basis: 'intraday', date: '2026-11-18', as_of: '2026-11-18T13:05:00+09:00', net: 120_000_000_000,
      arbt_net: 20_000_000_000, nabt_net: 100_000_000_000, quality: 'estimated',
    };
    const { root } = await mountLogin({ home: fx });
    const c = card(root, 'program');
    expect(text(c.querySelector('.hmeta'))).toBe('장중 누적 13:05 · 시장 순매수');
    const li = c.querySelector('.hlist li[data-market="코스피"]');
    expect(li?.getAttribute('data-basis')).toBe('intraday');
    expect(li?.querySelector('.v .q-badge')).not.toBeNull();
    expect(text(c.querySelector('.hlist li[data-market="코스닥"] small'))).toContain('11/17 마감');
    const cell = card(root, 'kospi').querySelector('.hinv [data-investor="프로그램"]');
    expect(cell?.getAttribute('title')).toBe('프로그램 순매수 장중 누적 13:05');
    expect(text(cell?.querySelector('dd'))).toContain('+1,200억');
  });

  it('색은 등락에만 — 이름·순위에는 .up/.down 이 없다', async () => {
    const { root } = await mountLogin();
    expect(root.querySelectorAll('.hcard .nm-t.up, .hcard .nm-t.down, .hcard .rk.up, .hcard .rk.down')).toHaveLength(0);
  });
});

describe('첫 화면 카드 — 상태와 격리', () => {
  it('no_data 카드는 서버 안내 문구, 값 있는 카드는 계속(home.json)', async () => {
    const { root } = await mountLogin({ home: fixture('home') });
    expect(text(card(root, 'gex').querySelector('.msg'))).toContain('아직 없음 — gex.engine');
    expect(card(root, 'gex').querySelector('.msg.err')).toBeNull();
    expect(card(root, 'movers').querySelectorAll('.hlist li').length).toBeGreaterThan(0);
    expect(text(root.querySelector('[data-section="오늘의 신호"] .hstamp'))).toContain('카드 7/14');
  });

  it('한 카드의 error 는 그 카드에만(코드 문구 — 내부 문구 없이)', async () => {
    const fx = fixture<HomeFx>('home_full');
    const cards = fx.data.cards as unknown as Record<string, CardFx>;
    Object.assign(cards.spike ?? {}, { status: 'error', error: 'unavailable', data: null, message: null });
    Object.assign(fx.data, { n_ok: 13, n_error: 1 });
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const { root } = await mountLogin({ home: fx });
    expect(text(card(root, 'spike').querySelector('.msg.err'))).toBe(cardErrorText('unavailable'));
    expect(root.querySelectorAll('.hcard .msg.err')).toHaveLength(1);
    expect(card(root, 'movers').querySelectorAll('.hlist li')).toHaveLength(9);
    expect(text(root.querySelector('[data-section="오늘의 신호"] .hstamp'))).toContain('오류 1');
    spy.mockRestore();
  });

  it('예상 밖 모양(그리다 던짐)도 그 카드만 오류', async () => {
    const fx = fixture<HomeFx>('home_full');
    const cards = fx.data.cards as unknown as Record<string, CardFx>;
    Object.assign(cards.newhigh ?? {}, { data: { date: '2026-09-15' } }); // counts·rows 없음
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const { root } = await mountLogin({ home: fx });
    expect(card(root, 'newhigh').querySelector('.msg.err')).not.toBeNull();
    expect(root.querySelectorAll('.hcard .msg.err')).toHaveLength(1);
    expect(card(root, 'themes').querySelectorAll('.hlist li').length).toBeGreaterThan(0);
    spy.mockRestore();
  });

  it('묶음 요청이 실패하면 묶음 카드에 오류 줄 — 관심종목·업종 지도·자세히 패널은 계속', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const { root } = await mountLogin({ home: () => json({ secret: 'body-text' }, 500) });
    expect(text(card(root, 'movers').querySelector('.msg.err'))).toBe('불러오지 못함 (HTTP 500)');
    expect(text(card(root, 'kospi').querySelector('.msg.err'))).toBe('불러오지 못함 (HTTP 500)');
    expect(text(root)).not.toContain('body-text');
    expect(card(root, 'watchlist').querySelectorAll('.hlist li').length).toBeGreaterThan(0);
    expect(panelByTitle(root, '업종 히트맵').querySelectorAll('.cell')).toHaveLength(2);
    expect(panelByTitle(root, '시장 투자자 수급').querySelectorAll('tbody tr')).toHaveLength(4);
    expect(text(root.querySelector('[data-section="오늘의 신호"] .hstamp'))).toBe('');
    spy.mockRestore();
  });

  it('묶음 404 는 서버 안내 문구', async () => {
    const msg = '아직 없음 — 첫 화면 묶음';
    const { root } = await mountLogin({ home: () => json({ code: 'no_data', message: msg }, 404) });
    expect(text(card(root, 'etf').querySelector('.msg'))).toBe(msg);
    expect(card(root, 'etf').querySelector('.msg.err')).toBeNull();
  });

  it('장중 응답(home_live): 외국인 + 기관은 장중 가집계(잠정) 배지', async () => {
    const { root } = await mountLogin({ home: fixture('home_live') });
    const fi = card(root, 'foreign_inst');
    expect(text(fi.querySelector('.hmeta'))).toContain('장중 가집계');
    expect(fi.querySelector('.hlist .q-badge')).not.toBeNull();
  });
});

describe('관심종목 카드', () => {
  it('서버 목록(최대 10) — 이름 단추·등락률 알약·외 N종목', async () => {
    const { root } = await mountLogin();
    const w = card(root, 'watchlist');
    expect(w.querySelectorAll('.hlist li')).toHaveLength(10);
    expect(w.querySelector('.hlist button[data-open]')).not.toBeNull();
    expect(text(w.querySelector('.hmore-n'))).toMatch(/^외 \d+종목/);
    expect(w.querySelector('.hmore')?.getAttribute('href')).toBe('#/p/8');
  });

  it('비면 안내 문구(오류가 아니다)', async () => {
    const empty = structuredClone(watchMain) as { data: { items: unknown[]; n: number } };
    empty.data.items = [];
    empty.data.n = 0;
    const { root } = await mountLogin({ watchlist: empty });
    const w = card(root, 'watchlist');
    expect(text(w.querySelector('.msg'))).toContain('관심종목이 비어 있다');
    expect(w.querySelector('.msg.err')).toBeNull();
  });

  it('관심종목 실패는 그 카드만', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const { root } = await mountLogin({ watchlist: () => json({}, 500) });
    expect(card(root, 'watchlist').querySelector('.msg.err')).not.toBeNull();
    expect(root.querySelectorAll('.hcard .msg.err')).toHaveLength(1);
    spy.mockRestore();
  });
});

describe('도장 글자', () => {
  it('기준·품질·카드 수·오류 수·만든 시각', () => {
    const fx = fixture<HomeFx>('home');
    expect(stampText(fx)).toBe('기준 09/15 15:30 · 잠정 · 카드 7/14 · 만든 시각 18:00');
    expect(marketStampText(fx)).toBe('지수 기준 09/15 15:30 · 잠정');
    const cards = fx.data.cards as unknown as Record<string, CardFx>;
    Object.assign(cards.indices ?? {}, { status: 'no_data' });
    expect(marketStampText(fx)).toBe('');
  });
});

describe('공개 빌드', () => {
  it('묶음·관심종목을 부르지 않고, 신호 카드는 자물쇠 + 더 보기 링크', async () => {
    const hz = publicHarness();
    const { ctx, root } = pageCtx(1, 'public', hz.source, { tradingview: vi.fn(() => () => undefined) });
    await page.mount(ctx);
    expect(hz.paths()).toEqual(['./data/credit.json']);
    const locked = root.querySelectorAll('.hsignals .hcard.locked');
    expect(locked).toHaveLength(SIGNAL_CARDS.length);
    expect(root.querySelectorAll('.hsignals .hmeta')).toHaveLength(0);
    expect(card(root, 'etf').querySelector('a.hmore')?.getAttribute('href')).toBe('#/p/10');
  });
});
