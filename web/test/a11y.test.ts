// 접근성 — axe-core 심각(serious·critical) 위반 0, 표 th scope, 버튼 이름(§6.8 a11y.test).
// 색 대비(color-contrast)는 jsdom 에 화면 계산이 없어 tokens.test.ts 가 맡는다.
import axe from 'axe-core';
import { describe, expect, it } from 'vitest';
import { mountApp } from '../src/app/app';
import { PAGES } from '../src/app/pages';
import type { PageContext } from '../src/app/types';
import { renderLoginForm, createAuth } from '../src/data/auth';
import type { DataSource, Tier } from '../src/data/source';
import { chip } from '../src/ui/chip';
import { panel } from '../src/ui/panel';
import { seg, select } from '../src/ui/seg';
import { level, sparkline } from '../src/ui/svg';
import { table } from '../src/ui/table';
import { appRoot, fakeFetch, fakeLoaders, fixedClock, json } from './helpers';

const nullSource = (tier: Tier): DataSource => ({ tier, get: () => Promise.reject(new Error('부르지 않는다')) });

async function seriousViolations(root: Element): Promise<string[]> {
  const res = await axe.run(root, {
    resultTypes: ['violations'],
    rules: { 'color-contrast': { enabled: false } },
  });
  return res.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
}

/** 부품을 두루 쓰는 가짜 페이지(W2 위젯이 쓸 부품 — 표·선택·막대·스파크라인) */
function widgetPage(ctx: PageContext): void {
  const p = panel(ctx.tier, { title: '종목 스크리너', tier: 'login', span: 8, lock: 'investor' });
  ctx.root.append(p.el);
  if (p.locked) return;
  const mode = seg({
    label: '스크리닝 기준',
    options: [
      { value: 'value', label: '거래대금 상위' },
      { value: 'foreign', label: '외국인 순매수' },
    ],
    value: 'value',
  });
  const mkt = select({
    label: '시장',
    options: [
      { value: 'all', label: '전체' },
      { value: 'KOSPI', label: '코스피' },
    ],
    value: 'all',
  });
  const t = table({
    caption: '스크리닝 결과',
    columns: [
      { key: 'name', label: '종목', align: 'l', cell: (r: { name: string; v: string }) => r.name },
      { key: 'v', label: '거래대금', cell: (r) => r.v },
    ],
    rows: [
      { name: '가상전자', v: '1,234' },
      { name: '예시조선', v: '987' },
    ],
    rowKey: (r) => r.name,
    onSelect: () => undefined,
  });
  p.body.append(mode.el, mkt.el, t.el, level('상승 비율', 55, '55:45'), sparkline([1, 2, 3], { label: '코스피 추이' }));
}

describe('axe-core', () => {
  for (const tier of ['public', 'login'] as const) {
    it(`${tier}: 13페이지 모두 심각 위반 0`, async () => {
      const root = appRoot();
      const app = mountApp(root, {
        tier,
        source: nullSource(tier),
        now: fixedClock(),
        loaders: fakeLoaders({ 4: widgetPage }),
        ribbon: null,
        user: tier === 'login' ? 'me' : undefined,
        onLogout: tier === 'login' ? () => undefined : undefined,
      });
      for (const p of PAGES) app.pager.go(p.n, { smooth: false });
      await app.settled();
      root.prepend(chip(tier, { key: 'mkt', label: '시장 거래대금', value: '21.4조', tier: 'login' }));
      expect(await seriousViolations(document.body)).toEqual([]);
      app.destroy();
    });
  }

  it('로그인 화면 심각 위반 0', async () => {
    const root = appRoot();
    const { fetch } = fakeFetch(() => json({}, 401));
    renderLoginForm(root, createAuth({ fetch }), { onSuccess: () => undefined });
    expect(await seriousViolations(document.body)).toEqual([]);
  });
});

describe('구조 규칙', () => {
  it('표 머리는 th scope=col, 버튼은 모두 이름이 있다', async () => {
    const root = appRoot();
    const app = mountApp(root, {
      tier: 'login',
      source: nullSource('login'),
      now: fixedClock(),
      loaders: fakeLoaders({ 1: widgetPage }),
      ribbon: null,
      onLogout: () => undefined,
    });
    await app.settled();
    const ths = root.querySelectorAll('th');
    expect(ths.length).toBeGreaterThan(0);
    for (const th of ths) expect(th.getAttribute('scope')).toBe('col');
    for (const b of root.querySelectorAll('button')) expect(b.textContent.trim().length).toBeGreaterThan(0);
    for (const g of root.querySelectorAll('[role="group"]')) expect(g.getAttribute('aria-label')).toBeTruthy();
    for (const svg of root.querySelectorAll('svg[role="img"]')) expect(svg.getAttribute('aria-label')).toBeTruthy();
    app.destroy();
  });
});
