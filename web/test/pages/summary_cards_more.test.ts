// 가시성 개선 2차(2026-11-02 — 주식봇식 요약 카드): 페이지 4 스크리너·5 공시·7 수출 속보·9 테마 강도·13 운영.
import { describe, expect, it } from 'vitest';
import type { components } from '../../src/api/types.gen';
import { screenCards } from '../../src/pages/p4/cards';
import { feedCards } from '../../src/pages/p5/cards';
import type { FeedRow } from '../../src/pages/p5/feed';
import { flashCards } from '../../src/pages/p7/cards';
import type { FlashRow } from '../../src/pages/p7/flash';
import { strengthCards } from '../../src/pages/p9/cards';
import page13 from '../../src/pages/p13_ops';
import { apiHarness, fixture, flush, pageCtx, text } from './support';

type S = components['schemas'];
const THEMES = import.meta.glob<unknown>('../fixtures/api/themes/*.json', { eager: true, import: 'default' });

describe('요약 카드 2차', () => {
  it('스크리너: 외국인·기관·개인 순매수 1위와 거래대금 폭증 1위', () => {
    const rows = fixture<{ data: S['ScreenResponse'] }>('flows_screen').data.rows;
    const c = screenCards(rows, '5일');
    expect(c.map((x) => x.label)).toEqual([
      '외국인 순매수 1위',
      '기관 순매수 1위',
      '개인 순매수 1위',
      '거래대금 폭증 1위',
    ]);
    const best = [...rows].sort((a, b) => (b.foreign ?? 0) - (a.foreign ?? 0))[0]!;
    expect(c[0]!.value).toBe(best.name);
    expect(screenCards([], '5일')[0]!.value).toBeNull();
  });

  it('공시: 건수·중요 이상·관심종목·많은 유형', () => {
    const row = (over: Partial<FeedRow>): FeedRow => ({
      rcept_no: '1', rcept_dt: '2026-11-02', first_seen_at: '2026-11-02T01:00:00Z', stock_code: '005930',
      corp_name: '가', corp_cls: 'Y', title: '단일판매ㆍ공급계약체결', kind: 'contract', is_correction: false,
      kw_score: null, link: '', ...over,
    }); // prettier-ignore
    const c = feedCards(
      [
        row({ importance: 'high' }),
        row({ rcept_no: '2', is_watch: true, is_correction: true }),
        row({ rcept_no: '3', kind: 'earnings' }),
      ],
      '2026-11-02',
    );
    expect(c[0]!.value).toBe('3건');
    expect(c[0]!.sub).toBe('정정 1건');
    expect(c[1]!.value).toBe('1건');
    expect(c[1]!.tone).toBe('warn');
    expect(c[2]!.value).toBe('1건');
    expect(c).toHaveLength(5);
  });

  it('수출 속보: 합계 전년 대비 + 금액 큰 항목 4개', () => {
    const r = (col_no: number, name: string, usd: number, yoy: number): FlashRow => ({
      col_no, name, usd, first_usd: usd, yoy, contribution: col_no ? 0.01 : null, note: '',
    }); // prettier-ignore
    const c = flashCards(
      [
        r(0, '전체', 3e10, 0.12),
        r(1, '반도체', 1e10, 0.5),
        r(2, '승용차', 5e9, -0.1),
        r(3, '선박', 1e9, 0.2),
        r(4, 'a', 2e9, 0),
        r(5, 'b', 1e8, 0),
      ],
      '수출',
    );
    expect(c[0]!.value).toBe('300.0억 달러');
    expect(c[0]!.zone).toBe('증가');
    expect(c.slice(1).map((x) => x.label)).toEqual(['반도체', '승용차', 'a', '선박']);
    expect(c[2]!.tone).toBe('down');
  });

  it('테마 강도: 강한 3개 + 가장 약한 1개', () => {
    const d = structuredClone(THEMES['../fixtures/api/themes/strength_1.json'] as { data: S['ThemeStrength'] }).data;
    const c = strengthCards(d.rows, 1);
    const ok = d.rows.filter((r) => r.stats.chg_pct !== null);
    expect(c.length).toBe(ok.length > 3 ? 4 : Math.min(3, ok.length));
    const best = [...ok].sort((a, b) => (b.stats.chg_pct ?? 0) - (a.stats.chg_pct ?? 0))[0];
    if (best) expect(c[0]!.value).toBe(best.name);
  });

  it('운영: 점검·상시 서비스·손볼 항목·오늘 작업 카드', async () => {
    const hz = apiHarness();
    const { ctx, root } = pageCtx(13, 'login', hz.source);
    await page13.mount(ctx);
    await flush();
    const t = [...root.querySelectorAll('.stat')].map(text);
    expect(t.some((x) => x.includes('상시 서비스') && x.includes('2/3'))).toBe(true);
    expect(t.some((x) => x.includes('손볼 항목') && x.includes('2개'))).toBe(true);
    expect(t.some((x) => x.includes('오늘 작업') && x.includes('4건'))).toBe(true);
    expect(t.some((x) => x.includes('재시도 성공') && x.includes('1건'))).toBe(true);
  });
});

describe('요약 카드 3차 — 규칙', () => {
  it('켠 규칙·30일 신호·최근 신호(보관 규칙은 뺀다)', async () => {
    const { ruleCards } = await import('../../src/pages/p11/rules');
    const r = (id: string, enabled: boolean, n: number, last: string | null, archived = false) =>
      ({ rule_id: id, name: id, enabled, archived, signals_30d: n, last_signal_at: last }) as Parameters<
        typeof ruleCards
      >[0][number];
    const c = ruleCards([
      r('A', true, 3, '2026-11-02T01:00:00Z'),
      r('B', false, 5, '2026-11-03T02:00:00Z'),
      r('C', true, 9, '2026-11-03T05:00:00Z', true),
    ]);
    expect(c[0]!.value).toBe('1/2');
    expect(c[1]!.value).toBe('8건');
    expect(c[1]!.sub).toContain('B 5건');
    expect(c[2]!.sub).toBe('B');
  });
});
