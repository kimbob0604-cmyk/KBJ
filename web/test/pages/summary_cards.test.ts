// 가시성 개선(2026-11-02 — 주식봇식 요약 카드): 페이지 1 투자자·페이지 10 ETF 수급·종목별 ETF 수급·페이지 2 신고가.
// 카드가 그려지고, 값이 없으면 '—'(0 으로 그리지 않는다), 부호 색이 맞는다.
import { describe, expect, it } from 'vitest';
import page1 from '../../src/pages/p1_market';
import { investorCards } from '../../src/pages/p1/cards';
import page2 from '../../src/pages/p2_board';
import { newhighCards } from '../../src/pages/p2/cards';
import page10 from '../../src/pages/p10_etf';
import { flowCards, stockFlowCards } from '../../src/pages/p10/cards';
import { apiHarness, fixture, flush, pageCtx, panelByTitle, text } from './support';

type Any = Record<string, never> & { data: never };

describe('요약 카드 — 순수 함수', () => {
  it('투자자: 4구분 카드, 순매수 up·순매도 down, 미제공은 —', () => {
    const d = fixture<Any>('flows_investors').data as unknown as Parameters<typeof investorCards>[0];
    d.by_investor = { foreign: 1.5e11, institution: -2e10, other_corp: null, individual: 0 };
    const c = investorCards(d);
    expect(c.map((x) => x.label)).toEqual(['외국인', '기관', '기타법인', '개인']);
    expect(c[0]!.value).toBe('+1,500억');
    expect(c[0]!.tone).toBe('up');
    expect(c[0]!.zone).toBe('순매수');
    expect(c[1]!.tone).toBe('down');
    expect(c[2]!.value).toBeNull();
    expect(c[2]!.sub).toBe('미제공');
    expect(c[3]!.tone).toBeNull();
  });

  it('ETF 수급: 순유입 합·최대 유입/유출·주체별 합', () => {
    const d = fixture<Any>('etf_flows').data as unknown as Parameters<typeof flowCards>[1] & {
      rows: Parameters<typeof flowCards>[0];
    };
    const rows = d.rows;
    const c = flowCards(rows, d);
    expect(c).toHaveLength(6);
    const total = rows.reduce((a, r) => a + (r.net_inflow ?? 0), 0);
    expect(c[0]!.tone).toBe(total > 0 ? 'up' : total < 0 ? 'down' : null);
    const best = Math.max(...rows.map((r) => r.net_inflow ?? -Infinity));
    if (best > 0) expect(c[1]!.sub).toBe(rows.find((r) => r.net_inflow === best)!.name);
    expect(flowCards([], d)[0]!.value).toBeNull();
  });

  it('종목별 ETF 수급: 가장 많이 산/판 종목·덮은 비율', () => {
    const d = fixture<Any>('etf_stocks').data as unknown as Parameters<typeof stockFlowCards>[0];
    const c = stockFlowCards(d);
    expect(c).toHaveLength(3);
    expect(c[0]!.tone).toBe('up');
    expect(c[1]!.tone).toBe('down');
    expect(c[2]!.sub).toContain('종목');
  });

  it('신고가: 라벨별 종목 수 + 거래대금 1위', () => {
    const d = fixture<Any>('board_newhigh').data as unknown as Parameters<typeof newhighCards>[0];
    const c = newhighCards(d, 'close', [
      { code: 'A', name: '가', turnover: 10, chg_pct: 3 },
      { code: 'B', name: '나', turnover: 99, chg_pct: -1 },
    ]);
    expect(c.at(-1)!.value).toBe('나');
    expect(c.at(-1)!.tone).toBe('down');
    expect(c.slice(0, -1).every((x) => x.label.endsWith('신고가'))).toBe(true);
    expect(newhighCards(d, 'close', []).at(-1)!.value).toBeNull();
  });
});

describe('요약 카드 — 페이지에 붙는다', () => {
  it('페이지 1 투자자·페이지 2 신고가·페이지 10 ETF 패널 맨 위에 카드', async () => {
    for (const [n, page, title] of [
      [1, page1, '시장 투자자 수급'],
      [2, page2, '신고가'],
      [10, page10, 'ETF 자금 흐름'],
      [10, page10, '종목별 ETF 수급'],
    ] as const) {
      const hz = apiHarness();
      const { ctx, root } = pageCtx(n, 'login', hz.source);
      await page.mount(ctx);
      await flush();
      const titles = [...root.querySelectorAll('section.panel h3 > span:first-child')].map((e) => e.textContent);
      const t = titles.find((x) => x.startsWith(title));
      expect(t, `${n} ${title} — ${titles.join('|')}`).toBeDefined();
      const p = panelByTitle(root, t!);
      expect(p.querySelectorAll('.stat').length, title).toBeGreaterThan(0);
      expect(text(p)).not.toMatch(/NaN|undefined/);
    }
  });
});
