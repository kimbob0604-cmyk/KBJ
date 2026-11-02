// 페이지 3 '상대 시총·연도별 수익률' — 요약 카드(쌍마다 비율·기간 위치) + 쌍 차트 + 연도별 표, 공개판 자물쇠.
import { describe, expect, it } from 'vitest';
import { mountRelcap, RELCAP_TITLE, type RelCap, relcapCards } from '../../src/pages/p3/relcap';
import { apiHarness, buttonByText, click, fixture, flush, pageCtx, panelByTitle, publicHarness, text } from './support';

describe('상대 시총 — 로그인', () => {
  it('쌍마다 작은 차트 카드 7·연도별 히트맵, 기간 바꾸기', async () => {
    const hz = apiHarness();
    const { ctx, root } = pageCtx(3, 'login', hz.source);
    await mountRelcap(ctx);
    await flush();
    const p = panelByTitle(root, RELCAP_TITLE);
    const minis = p.querySelectorAll('.mini');
    expect(minis).toHaveLength(7);
    const avgo = [...minis].find((c) => text(c).includes('브로드컴 / 엔비디아'))!;
    expect(text(avgo)).toContain('50.00%');
    expect(avgo.querySelector('svg')).not.toBeNull();
    expect(text(minis[1])).toContain('현재 시총 없음');
    const heads = [...p.querySelectorAll('.heat thead th')].map(text);
    expect(heads).toEqual(['연도', 'S&P 500', 'NASDAQ', 'KOSPI', 'KOSDAQ']);
    const first = p.querySelector('.heat tbody tr')!;
    expect(text(first)).toContain('2026 YTD');
    expect(text(first)).toContain('+10.0%');
    expect((first.querySelector('td.hc') as HTMLElement).style.background).not.toBe('');
    expect(text(p)).not.toMatch(/NaN|undefined|null/);
    expect(hz.paths()).toEqual(['market/relcap?period=1Y']);
    click(buttonByText(p, '2Y'));
    await flush();
    expect(hz.paths()[1]).toBe('market/relcap?period=2Y');
  });

  it('기간 위치 꼬리표 — 상단 up, 하단 down', () => {
    const d = fixture<{ data: RelCap }>('market_relcap').data;
    d.pairs[0]!.pct_of_range = 90;
    d.pairs[0]!.latest = 120;
    d.pairs[2]!.pct_of_range = 5;
    d.pairs[2]!.latest = 80;
    const c = relcapCards(d);
    expect(c[0]!.zone).toBe('기간 상단');
    expect(c[0]!.tone).toBe('up');
    expect(c[2]!.zone).toBe('기간 하단');
  });
});

describe('상대 시총 — 공개', () => {
  it('자물쇠만, 요청하지 않는다', async () => {
    const hz = publicHarness();
    const { ctx, root } = pageCtx(3, 'public', hz.source);
    await mountRelcap(ctx);
    expect(panelByTitle(root, RELCAP_TITLE).classList.contains('locked')).toBe(true);
    expect(hz.paths()).toEqual([]);
  });
});
