// 페이지 1 '글로벌 프록시' — 요약 카드(24시간 선물 종가 대비·해외 상장 괴리) + 표, 5분 갱신, 공개판 자물쇠.
import { describe, expect, it } from 'vitest';
import page from '../../src/pages/p1_market';
import { GLOBAL_REFRESH_MS, GLOBAL_TITLE, type GlobalProxy, price, proxyCards } from '../../src/pages/p1/global_proxy';
import { apiHarness, fixture, flush, pageCtx, panelByTitle, publicHarness, text } from './support';

describe('글로벌 프록시 — 로그인', () => {
  it('카드(선물 5 + 해외 상장 3)와 표를 그리고 5분마다 다시 부른다', async () => {
    const hz = apiHarness();
    const { ctx, root, every } = pageCtx(1, 'login', hz.source);
    await page.mount(ctx);
    await flush();
    const p = panelByTitle(root, GLOBAL_TITLE);
    const cards = p.querySelectorAll('.stat');
    expect(cards).toHaveLength(8);
    expect(text(cards[0])).toContain('삼성전자 24h');
    expect(text(cards[0])).toContain('$52.00'); // 국내 종가가 없으면 가격을 큰 숫자로
    expect(text(cards[0])).toContain('펀딩 +0.010%/8h');
    expect(text(cards[1])).toContain('받은 값 없음');
    expect(p.querySelectorAll('tbody tr').length).toBe(8); // 지수선물 2 + 매크로 3 + 미국 ETF 3
    expect(text(p)).toContain('원/달러 1,400.0');
    expect(text(p)).not.toMatch(/NaN|undefined|null/);
    expect(hz.paths().filter((x) => x.startsWith('market/global-proxy'))).toHaveLength(1);
    const tick = every.find((e) => e.ms === GLOBAL_REFRESH_MS);
    expect(tick).toBeDefined();
    tick!.fn();
    await flush();
    expect(hz.paths().filter((x) => x.startsWith('market/global-proxy'))).toHaveLength(2);
  });

  it('국내 종가가 있으면 종가 대비 %·괴리율을 큰 숫자로, 부호 색', () => {
    const d = fixture<{ data: GlobalProxy }>('market_global-proxy').data;
    d.perps[0]!.vs_close_pct = -1.5;
    d.listings[0]!.premium_pct = 3.2;
    d.listings[0]!.fair = 29.1;
    const c = proxyCards(d);
    expect(c[0]!.value).toBe('-1.50%');
    expect(c[0]!.tone).toBe('down');
    expect(c[0]!.zone).toBe('종가 대비');
    expect(c[5]!.value).toBe('+3.20%');
    expect(c[5]!.zone).toBe('프리미엄');
    expect(c[5]!.sub).toContain('vs 환산 $29.10');
  });

  it('단위 — 달러는 앞, pt·원은 뒤, 1000 이상은 정수', () => {
    const base = fixture<{ data: GlobalProxy }>('market_global-proxy').data.futures[0]!;
    expect(price({ ...base, price: 21000, unit: 'pt' })).toBe('21,000 pt');
    expect(price({ ...base, price: 52, unit: '$' })).toBe('$52.00');
    expect(price({ ...base, price: null })).toBeNull();
  });
});

describe('글로벌 프록시 — 공개', () => {
  it('자물쇠만, 요청하지 않는다', async () => {
    const hz = publicHarness();
    const { ctx, root } = pageCtx(1, 'public', hz.source);
    await page.mount(ctx);
    const p = panelByTitle(root, GLOBAL_TITLE);
    expect(p.classList.contains('locked')).toBe(true);
    expect(hz.paths().some((x) => x.includes('global'))).toBe(false);
  });
});
