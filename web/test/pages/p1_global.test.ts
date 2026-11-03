// 페이지 1 '글로벌 프록시' — 주식봇식 묶음(소제목·설명·카드 — 큰 숫자 가격 + 색 % 줄), 5분 갱신, 공개판 자물쇠.
import { describe, expect, it } from 'vitest';
import page from '../../src/pages/p1_market';
import {
  GLOBAL_REFRESH_MS,
  GLOBAL_TITLE,
  type GlobalProxy,
  price,
  proxySections,
} from '../../src/pages/p1/global_proxy';
import { apiHarness, fixture, flush, pageCtx, panelByTitle, publicHarness, text } from './support';

describe('글로벌 프록시 — 로그인', () => {
  it('묶음 다섯(선물·해외 상장·지수선물·매크로·ETF)과 카드, 5분마다 다시 부른다', async () => {
    const hz = apiHarness();
    const { ctx, root, every } = pageCtx(1, 'login', hz.source);
    await page.mount(ctx);
    await flush();
    const p = panelByTitle(root, GLOBAL_TITLE);
    const secs = [...p.querySelectorAll('.stat-sec > h4')].map(text);
    expect(secs).toEqual([
      '24시간 선물 · Hyperliquid',
      '해외 상장 주식 · 괴리율',
      '미국 지수선물',
      '매크로',
      '관련 미국 ETF',
    ]);
    expect(p.querySelectorAll('.stat')).toHaveLength(16);
    const smsn = p.querySelector('.stat')!;
    expect(text(smsn)).toContain('삼성전자');
    expect(text(smsn)).toContain('$52.00'); // 큰 숫자 = 가격
    expect(text(smsn)).toContain('24시간');
    expect(text(smsn)).toContain('펀딩');
    expect(text(p.querySelectorAll('.stat')[1])).toContain('받은 값 없음');
    expect(text(p)).toContain('원/달러 1,400.0');
    expect(text(p)).not.toMatch(/NaN|undefined|null/);
    expect(hz.paths().filter((x) => x.startsWith('market/global-proxy'))).toHaveLength(1);
    const tick = every.find((e) => e.ms === GLOBAL_REFRESH_MS);
    expect(tick).toBeDefined();
    tick!.fn();
    await flush();
    expect(hz.paths().filter((x) => x.startsWith('market/global-proxy'))).toHaveLength(2);
  });

  it('국내 종가가 있으면 종가 대비·괴리율 줄에 부호 색, 증서 비율·환산값 꼬리', () => {
    const d = fixture<{ data: GlobalProxy }>('market_global-proxy').data;
    d.perps[0]!.vs_close_pct = -1.5;
    d.perps[0]!.vs_label = '국내 종가(달러 환산)';
    d.listings[0]!.premium_pct = 3.2;
    d.listings[0]!.fair = 29.1;
    d.listings[0]!.ratio = 0.1;
    const [perps, listings] = proxySections(d);
    expect(perps!.cards[0]!.lines![0]).toEqual({ value: '-1.50%', label: 'vs 국내 종가(달러 환산)', tone: 'down' });
    const adr = listings!.cards[0]!;
    expect(adr.lines!.find((l) => l.label === '국내 대비 괴리')).toEqual({
      value: '+3.20%',
      label: '국내 대비 괴리',
      tone: 'up',
    });
    expect(adr.foot).toBe('1증서 = 보통주 0.1주 · 환산 $29.10');
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

describe('글로벌 프록시 — 지연', () => {
  it('마지막 시세가 3시간 넘게 지났으면 머리줄에 지연 꼬리표', async () => {
    const env = fixture<{ data: GlobalProxy }>('market_global-proxy');
    const hz = apiHarness({ 'market_global-proxy': env });
    const at = new Date(new Date(env.data.ts!).getTime() + 10 * 3_600_000);
    const { ctx, root } = pageCtx(1, 'login', hz.source, { now: () => at });
    await page.mount(ctx);
    await flush();
    const p = panelByTitle(root, GLOBAL_TITLE);
    expect(text(p.querySelector('.stat-desc'))).toContain('10시간 지연');
  });
});
