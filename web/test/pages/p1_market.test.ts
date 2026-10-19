// 페이지 1 시장 — A 의 합성 응답 픽스처로 위젯 렌더·기간 토글·품질 표시·검산 줄·공개판 자물쇠(§6.8 pages/*.test).
import { describe, expect, it, vi } from 'vitest';
import page, { CREDIT_TITLE, PENDING_TILES, REFRESH_MS } from '../../src/pages/p1_market';
import { MINUTE_REFRESH_MS } from '../../src/pages/p1/flows_minute';
import { macPublicHarness, macroFixture } from '../fixtures/api/macro/harness';
import { json } from '../helpers';
import {
  apiHarness,
  buttonByText,
  click,
  fixture,
  flush,
  notFound,
  pageCtx,
  panelByTitle,
  publicHarness,
  text,
} from './support';

// P5 묶음 F2 패널(장중 투자자 수급·프로그램매매)이 부르는 응답 — 합성 p5_world 픽스처(test/fixtures/api/program/)
const F2_FX = import.meta.glob<unknown>('../fixtures/api/program/*.json', { eager: true, import: 'default' });
const F2 = {
  'flows_market-minute': F2_FX['../fixtures/api/program/minute_k200_fut.json'] as object,
  program_daily: F2_FX['../fixtures/api/program/daily_K.json'] as object,
  program_top: F2_FX['../fixtures/api/program/top_K_buy_intraday.json'] as object,
};

async function mountLogin(over: Parameters<typeof apiHarness>[0] = {}) {
  const hz = apiHarness({ ...F2, ...over });
  const { ctx, root, every } = pageCtx(1, 'login', hz.source);
  await page.mount(ctx);
  return { hz, root, every };
}

describe('페이지 1 시장 — 로그인', () => {
  it('보조 타일(원/달러·VKOSPI)은 서버가 보낼 때만 그린다 — 준비 중 자리는 없다', async () => {
    const summary = fixture<{ data: { indices: object[] } }>('market_summary');
    const base = summary.data.indices[0] as Record<string, unknown>;
    summary.data.indices.push({ ...base, code: 'USDKRW', name: '원/달러', value: 1424.1, live: false });
    const { root } = await mountLogin({ market_summary: summary });
    const idx = panelByTitle(root, '지수');
    expect(idx.querySelector('[data-code="USDKRW"]')).not.toBeNull();
    expect(idx.querySelectorAll('[data-pending]')).toHaveLength(0);
    expect(text(idx)).not.toContain('VKOSPI');
  });

  it('요약·업종·투자자를 한 번씩 부르고 위젯을 그린다', async () => {
    const { hz, root } = await mountLogin();
    expect(hz.paths().sort()).toEqual([
      'flows/investors',
      'flows/market-minute?market=k200_fut',
      'market/credit',
      'market/sectors?period=1',
      'market/summary',
      'program/daily?days=60&market=K',
      'program/top?market=K&scope=intraday&side=buy',
    ]);

    const idx = panelByTitle(root, '지수');
    const tiles = idx.querySelectorAll('.tile');
    expect(tiles).toHaveLength(2); // 원천이 없는 보조 타일(PENDING_TILES)은 그리지 않는다
    expect(text(tiles[0])).toContain('코스피');
    expect(text(tiles[0])).toContain('20,050.76');
    expect(tiles[0]?.querySelector('svg[role="img"]')?.getAttribute('aria-label')).toBe('코스피 추이');
    expect(text(idx)).not.toContain('준비 중');
    expect(PENDING_TILES.map((t) => t.code)).toEqual(['K200N', 'VKOSPI', 'USDKRW']);
    // 지수 2001 결측 메모는 지수 패널로
    expect(text(idx.querySelector('.notes'))).toContain('지수 2001 자료 없음');

    const turn = panelByTitle(root, '시장 거래대금');
    expect(text(turn)).toContain('4,611억'); // 461,070,878,400원 → 화면에서만 억
    expect(text(turn)).toContain('1.08x');
    expect(text(turn)).toContain('마감 확정');
    expect(turn.querySelectorAll('svg rect')).toHaveLength(20);
    expect(text(turn.querySelector('.notes'))).toContain('NXT 미포함');

    const br = panelByTitle(root, '시장폭');
    expect(text(br)).toContain('상승');
    expect(text(br)).toContain('47.1% (16/34)');
    expect(text(br.querySelector('.notes'))).toContain('신고가 비율 없음');
    expect(text(br.querySelector('h3 .src'))).toContain('잠정');

    const heat = panelByTitle(root, '업종 히트맵');
    expect(heat.querySelectorAll('.cell')).toHaveLength(2);
    expect(text(heat.querySelector('.cell'))).toContain('S0001'); // 이름이 없으면 코드

    const inv = panelByTitle(root, '시장 투자자 수급');
    expect(inv.querySelectorAll('tbody tr')).toHaveLength(4);
    expect(text(inv)).toContain('-11'); // 외국인 -1,143,000,000원 → -11억
    expect(inv.querySelector('[data-check]')?.getAttribute('data-check')).toBe('ok');

    // 금투협 신용 패널(합성 MAC 세계 — market/credit)
    expect(panelByTitle(root, CREDIT_TITLE).querySelectorAll('.tile')).toHaveLength(3);
    expect(root.querySelectorAll('.msg.err')).toHaveLength(0);
  });

  it('히트맵 기간 토글 → period 쿼리로 다시 부른다', async () => {
    const { hz, root } = await mountLogin();
    click(buttonByText(panelByTitle(root, '업종 히트맵'), '20일'));
    await flush();
    expect(hz.paths()).toContain('market/sectors?period=20');
    expect(buttonByText(root, '20일').getAttribute('aria-pressed')).toBe('true');
  });

  it('장중 응답: 잠정 배지·점선 밑줄, 지수 기준 표시', async () => {
    const { root } = await mountLogin({ market_summary: fixture('market_summary_live') });
    const turn = panelByTitle(root, '시장 거래대금');
    expect(text(turn)).toContain('장중(지수 기준)');
    expect(turn.querySelector('.q-est .q-badge')?.textContent).toBe('잠정');
    expect(panelByTitle(root, '지수').querySelector('.tile .q-est')).not.toBeNull();
    expect(text(panelByTitle(root, '지수').querySelector('.tile .n'))).toContain('장중');
  });

  it('요약 404: 세 패널에 서버 안내 문구, 다른 패널은 계속(격리)', async () => {
    const msg = '아직 없음 — krx.daily 08:05 · market.close_collect 15:35';
    const { root } = await mountLogin({ market_summary: () => notFound(msg) });
    for (const t of ['지수', '시장 거래대금', '시장폭']) {
      expect(text(panelByTitle(root, t).querySelector('.msg'))).toBe(msg);
    }
    expect(panelByTitle(root, '업종 히트맵').querySelectorAll('.cell')).toHaveLength(2);
    expect(panelByTitle(root, '시장 투자자 수급').querySelectorAll('tbody tr')).toHaveLength(4);
  });

  it('no_data 본문이 없는 404 는 패널의 예정 작업 문구', async () => {
    const { root } = await mountLogin({ market_summary: () => json({ detail: 'Not Found' }, 404) });
    expect(text(panelByTitle(root, '지수').querySelector('.msg'))).toBe('아직 없음 — market.close_collect 15:35');
  });

  it('서버 오류는 상태 코드만(본문 미표시)', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const { root } = await mountLogin({ flows_investors: () => json({ secret: 'body-text' }, 500) });
    const inv = panelByTitle(root, '시장 투자자 수급');
    expect(text(inv.querySelector('.msg.err'))).toBe('불러오지 못함 (HTTP 500)');
    expect(text(root)).not.toContain('body-text');
    spy.mockRestore();
  });

  it('업종 코드가 비면 준비 중 안내(0 으로 칠하지 않는다)', async () => {
    const sectors = fixture<{ data: { cells: unknown[] } }>('market_sectors');
    sectors.data.cells = [];
    const { root } = await mountLogin({ market_sectors: sectors });
    const heat = panelByTitle(root, '업종 히트맵');
    expect(heat.querySelectorAll('.cell')).toHaveLength(0);
    expect(text(heat)).toContain('업종 지수 코드 준비 중');
  });

  it('기타법인이 없으면 검산 ① 불가 사유(R2) — 0 으로 그리지 않는다', async () => {
    const inv = fixture<{ data: { by_investor: Record<string, number | null>; check1_residual: number | null } }>(
      'flows_investors',
    );
    inv.data.by_investor.other_corp = null;
    inv.data.check1_residual = null;
    const { root } = await mountLogin({ flows_investors: inv });
    const p = panelByTitle(root, '시장 투자자 수급');
    expect(text(p.querySelector('[data-check]'))).toBe('검산 불가: 기타법인 미제공');
  });

  it('invalid 지수는 그리지 않고 수를 적는다', async () => {
    const s = fixture<{ data: { indices: { quality: string }[] } }>('market_summary');
    const first = s.data.indices[0];
    if (first) first.quality = 'invalid';
    const { root } = await mountLogin({ market_summary: s });
    const idx = panelByTitle(root, '지수');
    expect(idx.querySelectorAll('.tile[data-code]')).toHaveLength(1);
    expect(text(idx.querySelector('.notes'))).toContain('검산 실패 1행 제외');
  });

  it('10분 슬롯 + 여유로 새로 고침을 건다', async () => {
    const { hz, every } = await mountLogin();
    expect(every.map((e) => e.ms)).toEqual([REFRESH_MS, MINUTE_REFRESH_MS]); // P3 10분 슬롯 + F2 60초
    const before = hz.calls.length;
    every[0]?.fn();
    await flush();
    expect(hz.calls.length).toBe(before + 4); // 요약·업종·투자자·신용
  });
});

describe('앱이 먼저 내려가면', () => {
  it('첫 불러오기 중에 격자가 빠지면 주기 작업을 걸지 않는다(정리 누수 방지)', async () => {
    const hz = apiHarness();
    const { ctx, root, every } = pageCtx(1, 'login', hz.source);
    const job = page.mount(ctx);
    root.remove();
    await job;
    expect(every).toHaveLength(0);
  });
});

describe('페이지 1 시장 — 공개', () => {
  it('데이터를 요청하지 않고, 지수 자리는 TradingView, 나머지는 자물쇠', async () => {
    const hz = publicHarness();
    const tv = vi.fn(() => () => undefined);
    const { ctx, root } = pageCtx(1, 'public', hz.source, { tradingview: tv });
    await page.mount(ctx);
    expect(hz.paths()).toEqual(['./data/credit.json']); // 공개 파일 하나(금투협 — 공개 등급), 로그인 API 0
    expect(tv).toHaveBeenCalledTimes(3);
    expect(tv.mock.calls.map((c) => (c as unknown[])[1])).toEqual([
      'mini-symbol-overview',
      'mini-symbol-overview',
      'mini-symbol-overview',
    ]);
    expect(root.querySelectorAll('.panel.locked')).toHaveLength(4 + 3); // + F2 투자자 수급·프로그램 2
    expect(text(panelByTitle(root, '시장 거래대금'))).toContain('LOGIN');
    // 공개 파일이 없으면(수집 전) 패널 안 안내 — '준비 중' 상자는 없다
    const credit = panelByTitle(root, CREDIT_TITLE);
    expect(credit.classList.contains('locked')).toBe(false);
    expect(text(credit.querySelector('.msg'))).toContain('아직 없음');
  });

  it('TradingView 가 없으면 패널 안 안내', async () => {
    const hz = publicHarness();
    const { ctx, root } = pageCtx(1, 'public', hz.source);
    await page.mount(ctx);
    expect(text(panelByTitle(root, '지수'))).toContain('TradingView 위젯을 불러오지 못함');
  });
});

// ── 신용잔고 · 예탁금 · 펀드 자금(금투협 종합통계 — 합성 MAC 세계 test/fixtures/api/macro/market_credit.json) ──

type CreditFx = { quality: string; data: { lines: Record<string, unknown>[] } };

function tileOf(root: HTMLElement, key: string): HTMLElement {
  const el = panelByTitle(root, CREDIT_TITLE).querySelector<HTMLElement>(`.tile[data-key="${key}"]`);
  if (!el) throw new Error(`신용 타일 없음: ${key}`);
  return el;
}

describe('페이지 1 신용잔고 · 예탁금 · 펀드 자금 — 로그인', () => {
  it('마지막 값(조·억)·전일 대비·20일 추이, 늦은 값은 지연 표시, 없는 값은 —', async () => {
    const { root } = await mountLogin();
    const p = panelByTitle(root, CREDIT_TITLE);
    expect(p.querySelector('[data-pending]')).toBeNull();
    expect(text(p)).not.toContain('준비 중');

    const cb = tileOf(root, 'credit_balance');
    expect(text(cb.querySelector('.n'))).toContain('신용잔고');
    expect(text(cb.querySelector('.p'))).toContain('17.5조'); // 17,534,979,000,000원 → 화면에서만 조
    expect(cb.querySelector('.p .q-stale')).not.toBeNull(); // 하루 늦게 공표(D+1) — stale
    expect(text(cb.querySelector('.down'))).toBe('전일 대비 -286억'); // -28,570,000,000원
    expect(cb.querySelector('svg')?.getAttribute('aria-label')).toBe('신용잔고 20영업일 추이');
    expect(cb.querySelectorAll('svg polyline')).toHaveLength(1);

    const dep = tileOf(root, 'investor_deposit');
    expect(text(dep.querySelector('.p'))).toBe('55.3조');
    expect(text(dep.querySelector('.up'))).toBe('전일 대비 +2,133억');

    const fund = tileOf(root, 'fund_nav');
    expect(text(fund.querySelector('.p'))).toBe('—'); // 수집 전 — 0 이 아니다
    expect(fund.querySelector('svg')).toBeNull();
    const notes = text(p.querySelector('.notes'));
    expect(notes).toContain('펀드 순자산: 아직 없음');
    expect(notes).toContain('신용잔고: 공표 지연');
    expect(text(p.querySelector('h3 .src'))).toContain('DATAGO');
    expect(text(p)).not.toMatch(/NaN|undefined/);
  });

  it('값·변화가 null 이면 0 대신 —, 추이의 빈 날은 건너뛴다', async () => {
    const fx = macroFixture<CreditFx>('market_credit');
    const dep = fx.data.lines[1] as { value: unknown; change: unknown; quality: unknown; spark: { value: unknown }[] };
    dep.value = null;
    dep.change = null;
    dep.quality = null;
    for (const pt of dep.spark) pt.value = null;
    const { root } = await mountLogin({ market_credit: fx });
    const t = tileOf(root, 'investor_deposit');
    expect(text(t.querySelector('.p'))).toBe('—');
    expect(text(t.querySelector('.dim'))).toBe('—');
    expect(t.querySelector('svg polyline')).toBeNull();
  });

  it('예탁금이 ECOS 월별로 대신 채워지면 전월 대비·개월 추이', async () => {
    const fx = macroFixture<CreditFx>('market_credit');
    Object.assign(fx.data.lines[1] ?? {}, { freq: 'M', source: 'ECOS:901Y056', note: '월별(ECOS 901Y056) — 금투협 일별 값 없음' });
    const { root } = await mountLogin({ market_credit: fx });
    const t = tileOf(root, 'investor_deposit');
    expect(text(t)).toContain('전월 대비');
    expect(t.querySelector('svg')?.getAttribute('aria-label')).toContain('개월 추이');
    expect(text(panelByTitle(root, CREDIT_TITLE).querySelector('.notes'))).toContain('월별(ECOS 901Y056)');
  });

  it('404(수집 전): 패널 안 서버 안내, 다른 패널은 계속(격리)', async () => {
    const msg = '아직 없음 — market_stats.kofia 13:30(D+1 개방)';
    const { root } = await mountLogin({ market_credit: () => notFound(msg) });
    expect(text(panelByTitle(root, CREDIT_TITLE).querySelector('.msg'))).toBe(msg);
    expect(panelByTitle(root, '시장 투자자 수급').querySelectorAll('tbody tr')).toHaveLength(4);
  });
});

describe('페이지 1 신용잔고 · 예탁금 — 공개(credit.json)', () => {
  async function mountPublic(over: Parameters<typeof macPublicHarness>[0] = {}) {
    const hz = macPublicHarness(over);
    const { ctx, root } = pageCtx(1, 'public', hz.source, { tradingview: vi.fn(() => () => undefined) });
    await page.mount(ctx);
    return { hz, root };
  }

  it('공개 칩(백만원) → 원으로 바꿔 조·억, 추이 없이 직전 대비만', async () => {
    const { hz, root } = await mountPublic();
    expect(hz.paths()).toEqual(['./data/credit.json']);
    const cb = tileOf(root, 'credit_balance');
    expect(text(cb.querySelector('.p'))).toContain('17.5조');
    expect(text(cb.querySelector('.down'))).toBe('전일 대비 -286억');
    expect(text(tileOf(root, 'investor_deposit').querySelector('.p'))).toBe('55.3조');
    const p = panelByTitle(root, CREDIT_TITLE);
    expect(p.querySelectorAll('.tile')).toHaveLength(2); // 펀드 순자산은 공개 파일에 없다
    expect(p.querySelector('svg')).toBeNull();
    expect(text(p.querySelector('.notes'))).toContain('20일 추이는 로그인');
  });

  it('모르는 단위는 값을 지어내지 않고 —', async () => {
    const credit = macroFixture<{ data: { chips: { key: string; detail: Record<string, unknown> }[] } }>('public/credit');
    for (const c of credit.data.chips) c.detail.unit = '좌';
    const { root } = await mountPublic({ credit: () => json(credit) });
    const cb = tileOf(root, 'credit_balance');
    expect(text(cb.querySelector('.p'))).toBe('—');
    expect(text(panelByTitle(root, CREDIT_TITLE).querySelector('.notes'))).toContain('단위 확인 필요');
  });
});
