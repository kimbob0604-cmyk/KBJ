// 페이지 8 '재무' — 분기 막대 + OPM 선(comboChart)과 재무 표(drawFin).
// 2026-10-21: 값이 있는 분기가 하나뿐이면 막대 하나가 그림 전체를 덮어 빈 상자처럼 보였고, 차분 불가 행을
// 걸러 범위 글자가 '2026 1Q ~ 2026 1Q' 였다. 막대 폭 상한·점·모든 행 칸을 본다.
import { describe, expect, it } from 'vitest';
import { comboChart, type S } from '../src/pages/p8/common';
import { drawFin } from '../src/pages/p8/financials';
import { panel } from '../src/ui/panel';

type FinRow = S['FinRow'];

function row(label: string, revenue: number | null, opm: number | null, derived: boolean): FinRow {
  return {
    period: label,
    label,
    fiscal_year: 2026,
    fiscal_q: 1,
    period_start: '2026-01-01',
    period_end: '2026-03-31',
    rcept_dt: null,
    revenue,
    op: revenue === null ? null : 1,
    ni: null,
    ni_owner: null,
    opm,
    npm: null,
    rev_yoy: null,
    op_yoy: null,
    derived,
    quality: 'ok',
    notes: [],
  };
}

const texts = (svg: SVGSVGElement): string[] => [...svg.querySelectorAll('text')].map((t) => t.textContent);

describe('comboChart — 값이 드문 분기', () => {
  it('값 하나뿐이어도 막대는 폭 상한 안(그림을 덮지 않는다) + 선 대신 점', () => {
    const svg = comboChart({ label: 't', categories: ['2026 1Q'], bars: [74e8], line: [1.9], barName: '매출(억)' });
    const rects = svg.querySelectorAll('rect');
    expect(rects).toHaveLength(1);
    expect(Number(rects[0]?.getAttribute('width'))).toBeLessThanOrEqual(40);
    expect(Number(rects[0]?.getAttribute('y'))).toBeGreaterThan(14); // 맨 위까지 채우지 않는다
    expect(svg.querySelectorAll('circle')).toHaveLength(1);
  });

  it('없는 값은 자리만 두고(0 으로 그리지 않는다) 범위 글자는 첫 칸 ~ 끝 칸', () => {
    const svg = comboChart({
      label: 't',
      categories: ['2025 2Q', '2025 3Q', '2026 1Q', '2026 2Q'],
      bars: [null, 10, 12, null],
      line: [null, 5, 6, null],
      barName: '매출(억)',
    });
    expect(svg.querySelectorAll('rect')).toHaveLength(2);
    expect(svg.querySelectorAll('circle')).toHaveLength(2);
    expect(texts(svg)).toContain('2025 2Q ~ 2026 2Q');
  });
});

describe('drawFin — 차분 불가 행도 차트 칸', () => {
  it('범위 글자는 표의 첫 행 ~ 끝 행(차분 불가 행을 거르지 않는다)', () => {
    const p = panel('login', { title: '분기·연간 재무', tier: 'public' });
    const fin = {
      applicable: true,
      code: '994010',
      corp_code: '99400010',
      basis: 'q',
      fs_div: 'OFS',
      currency: 'KRW',
      tier: 'full',
      rows: [row('2025 2Q', null, null, true), row('2026 1Q', 74e8, 1.9, false), row('2026 2Q', null, null, true)],
      ratios: null,
      checks: { diff3m_max_pct: 0, n_derived: 2, n_estimated: 0 },
    } as unknown as S['Financials'];
    drawFin(p, fin);
    const svg = p.body.querySelector('svg') as SVGSVGElement;
    expect(texts(svg)).toContain('2025 2Q ~ 2026 2Q');
    expect(svg.querySelectorAll('rect')).toHaveLength(1);
    expect(p.body.querySelectorAll('tbody tr')).toHaveLength(3);
  });
});
