// 페이지 1 시장 — A 의 합성 응답 픽스처로 위젯 렌더·기간 토글·품질 표시·검산 줄·공개판 자물쇠(§6.8 pages/*.test).
import { describe, expect, it, vi } from 'vitest';
import page, { PENDING_TILES, REFRESH_MS } from '../../src/pages/p1_market';
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

async function mountLogin(over: Parameters<typeof apiHarness>[0] = {}) {
  const hz = apiHarness(over);
  const { ctx, root, every } = pageCtx(1, 'login', hz.source);
  await page.mount(ctx);
  return { hz, root, every };
}

describe('페이지 1 시장 — 로그인', () => {
  it('요약·업종·투자자를 한 번씩 부르고 위젯을 그린다', async () => {
    const { hz, root } = await mountLogin();
    expect(hz.paths().sort()).toEqual(['flows/investors', 'market/sectors?period=1', 'market/summary']);

    const idx = panelByTitle(root, '지수');
    const tiles = idx.querySelectorAll('.tile');
    expect(tiles).toHaveLength(2 + PENDING_TILES.length);
    expect(text(tiles[0])).toContain('코스피');
    expect(text(tiles[0])).toContain('20,050.76');
    expect(tiles[0]?.querySelector('svg[role="img"]')?.getAttribute('aria-label')).toBe('코스피 추이');
    expect(text(idx)).toContain('준비 중(P7)');
    expect(text(idx)).toContain('원/달러');
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

    // 공개 등급 빈 자리
    expect(text(panelByTitle(root, '신용잔고 · 예탁금 · 펀드 자금'))).toContain('P5');
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
    expect(every.map((e) => e.ms)).toEqual([REFRESH_MS]);
    const before = hz.calls.length;
    every[0]?.fn();
    await flush();
    expect(hz.calls.length).toBe(before + 3);
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
    expect(hz.calls).toHaveLength(0);
    expect(tv).toHaveBeenCalledTimes(3);
    expect(tv.mock.calls.map((c) => (c as unknown[])[1])).toEqual([
      'mini-symbol-overview',
      'mini-symbol-overview',
      'mini-symbol-overview',
    ]);
    expect(root.querySelectorAll('.panel.locked')).toHaveLength(4);
    expect(text(panelByTitle(root, '시장 거래대금'))).toContain('LOGIN');
    expect(text(panelByTitle(root, '신용잔고 · 예탁금 · 펀드 자금'))).toContain('준비 중');
  });

  it('TradingView 가 없으면 패널 안 안내', async () => {
    const hz = publicHarness();
    const { ctx, root } = pageCtx(1, 'public', hz.source);
    await page.mount(ctx);
    expect(text(panelByTitle(root, '지수'))).toContain('TradingView 위젯을 불러오지 못함');
  });
});
