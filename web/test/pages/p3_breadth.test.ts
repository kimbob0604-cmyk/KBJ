// 페이지 3 '시장 체력 — ADR·이격도' — 요약 카드(값·구간 꼬리표·그날 등락 수) + 차트 둘 + 기간·이격도 쿼리, 공개판 자물쇠.
import { describe, expect, it } from 'vitest';
import { BREADTH_TITLE, breadthCards, mountBreadth } from '../../src/pages/p3/breadth';
import { apiHarness, buttonByText, click, fixture, flush, pageCtx, panelByTitle, publicHarness, text } from './support';

describe('시장 체력 — 로그인', () => {
  it('요약 카드 넷과 차트 둘, 기간·이격도 바꾸면 다시 부른다', async () => {
    const hz = apiHarness({ 'diag_breadth-risk': fixture('diag_breadth-risk') });
    const { ctx, root } = pageCtx(3, 'login', hz.source);
    await mountBreadth(ctx);
    await flush();
    const p = panelByTitle(root, BREADTH_TITLE);
    const cards = p.querySelectorAll('.stat');
    expect(cards).toHaveLength(4);
    expect(text(cards[0])).toContain('ADR 코스피');
    expect(text(cards[0])).toContain('101.7%');
    expect(text(cards[0])).toContain('중립');
    expect(text(cards[0])).toMatch(/그날 상승 \d+ · 하락 \d+/);
    expect(text(cards[2])).toContain('이격도 코스피 25일');
    expect(text(cards[2])).toContain('118.3');
    expect(text(cards[2])).toContain('% 위');
    expect(p.querySelectorAll('figure svg')).toHaveLength(2);
    expect(text(p)).not.toMatch(/NaN|undefined|null/);
    expect(hz.paths()).toEqual(['diag/breadth-risk?ma=50&period=6M']);
    click(buttonByText(p, '1Y'));
    click(buttonByText(p, '25일'));
    await flush();
    expect(hz.paths().slice(1)).toEqual(['diag/breadth-risk?ma=50&period=1Y', 'diag/breadth-risk?ma=25&period=1Y']);
  });

  it('구간 꼬리표 색 — 과열은 warn, 바닥권은 down, 값 없으면 —', () => {
    const d = fixture<{ data: Parameters<typeof breadthCards>[0] }>('diag_breadth-risk');
    d.data.adr[0]!.latest = 130;
    d.data.adr[0]!.zone = '과열';
    d.data.adr[1]!.latest = null;
    d.data.adr[1]!.zone = null;
    const c = breadthCards(d.data);
    expect(c[0]!.tone).toBe('warn');
    expect(c[1]!.value).toBeNull();
  });
});

describe('시장 체력 — 공개', () => {
  it('요청 없이 자물쇠', async () => {
    const hz = publicHarness();
    const { ctx, root } = pageCtx(3, 'public', hz.source);
    await mountBreadth(ctx);
    expect(hz.calls).toHaveLength(0);
    expect(panelByTitle(root, BREADTH_TITLE).classList.contains('locked')).toBe(true);
  });
});
