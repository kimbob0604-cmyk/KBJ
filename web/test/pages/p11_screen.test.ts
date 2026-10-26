// 페이지 11 스크리너(cis 순추세). 합성 픽스처(tests/unit/api/web_fixtures_p8/screen.py — 통과 1·윗꼬리 1). 404 는 '.msg'.
import { describe, expect, it } from 'vitest';
import { createApiSource } from '../../src/data/api';
import { fillScreen, PASS_TEXT, paramsText, regimeText, type ScreenCis, viewRows } from '../../src/pages/p11/screen';
import { panel } from '../../src/ui/panel';
import fx from '../fixtures/api/screen/cis.json';
import { fakeFetch, json } from '../helpers';
import { pageCtx, text } from './support';

interface Env<T> {
  data: T;
  notes: string[];
}

const body = (): Env<ScreenCis> => structuredClone(fx) as unknown as Env<ScreenCis>;

function source(respond: () => Response) {
  const { fetch, calls } = fakeFetch(() => respond());
  return { source: createApiSource({ fetch }), calls };
}

function mkPanel() {
  const p = panel('login', { title: '스크리너', tier: 'login', lock: 'personal', span: 12 });
  document.body.append(p.el);
  return p;
}

describe('스크리너 — cis 순추세', () => {
  it('요약·문턱·통과 표(기본) → 전체 보기에 까닭', async () => {
    const { source: src, calls } = source(() => json(body()));
    const p = mkPanel();
    await fillScreen(pageCtx(11, 'login', src).ctx, p);
    expect(calls.map((c) => c.url)).toEqual(['/api/screen/cis']);
    const t = text(p.body);
    expect(t).toContain('거래량 급증 2');
    expect(t).toContain(`${PASS_TEXT} 1`);
    expect(t).toContain('코스피 강세');
    expect(t).toContain('손절 −2%');
    let rows = [...p.body.querySelectorAll('tbody tr')].map((tr) => text(tr));
    expect(rows).toHaveLength(1);
    expect(rows[0]).toContain('합성돌파');
    expect(rows[0]).toContain('6.0x');
    const all = [...p.body.querySelectorAll<HTMLButtonElement>('.seg button')].find((b) => b.textContent === '거래량 급증 전체');
    all?.click();
    rows = [...p.body.querySelectorAll('tbody tr')].map((tr) => text(tr));
    expect(rows).toHaveLength(2);
    expect(rows[1]).toContain('윗꼬리');
    expect(text(p.el)).not.toMatch(/NaN|undefined/);
  });

  it('도우미 — 국면·문턱·보기', () => {
    const d = body().data;
    expect(regimeText(d.regimes)).toBe('코스피 강세 · 코스닥 약세');
    expect(paramsText(d.params)).toContain('거래량 ≥ 20일 평균×3');
    expect(viewRows(d, 'pass').every((r) => r.passed)).toBe(true);
    expect(viewRows(d, 'all')).toHaveLength(d.rows.length);
  });

  it('404 는 안내 문구', async () => {
    const { source: src } = source(() => json({ code: 'no_data', message: '아직 없음 — 일봉' }, 404));
    const p = mkPanel();
    await fillScreen(pageCtx(11, 'login', src).ctx, p);
    expect(p.body.querySelector('.msg.err')).toBeNull();
    expect(text(p.body)).toContain('아직 없음');
  });
});
