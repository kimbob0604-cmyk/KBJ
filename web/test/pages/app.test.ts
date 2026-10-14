// 셸 + 실제 W2 모듈(빌드 때 찾는 src/pages/p*_*.ts·src/app/ribbon.ts) 통합 — 두 등급에서 페이지 1·2·4·10 과 띠가
// 오류 없이 붙고, 공개판은 로그인 API 를 한 번도 부르지 않으며, axe-core 심각 위반 0(§6.8).
import axe from 'axe-core';
import { describe, expect, it, vi } from 'vitest';
import { mountApp } from '../../src/app/app';
import { P3_PAGES, P4_PAGES, P5_PAGES, P6_PAGES, P7_PAGES, P8_PAGES, P9_PAGES, pageLoaders } from '../../src/app/pages';
import { appRoot, fixedClock } from '../helpers';
import { apiHarness, publicHarness } from './support';

// P5 페이지 10 패널(ETF 맵·동시 편입)이 부르는 응답 — 합성 p5_world 픽스처(test/fixtures/api/etf/ — ETF 묶음)
const P5_ETF_FX = import.meta.glob<unknown>('../fixtures/api/etf/*.json', { eager: true, import: 'default' });
const P5_ETF = {
  etf_map: P5_ETF_FX['../fixtures/api/etf/map_type_1m.json'] as object,
  'etf_active-consensus': P5_ETF_FX['../fixtures/api/etf/active_consensus.json'] as object,
};

// P5 묶음 F2 — 페이지 1 장중 투자자 수급·프로그램매매 패널이 부르는 응답(test/fixtures/api/program/ — 합성 p5_world)
const P5_F2_FX = import.meta.glob<unknown>('../fixtures/api/program/*.json', { eager: true, import: 'default' });
const P5_F2 = {
  'flows_market-minute': P5_F2_FX['../fixtures/api/program/minute_k200_fut.json'] as object,
  program_daily: P5_F2_FX['../fixtures/api/program/daily_K.json'] as object,
  program_top: P5_F2_FX['../fixtures/api/program/top_K_buy_intraday.json'] as object,
};

// P5 묶음 US — 페이지 2 '간밤 미국 신고가' 패널이 부르는 응답(test/fixtures/api/us/ — 합성 p5_world, WRAP 이 붙였다)
const P5_US_FX = import.meta.glob<unknown>('../fixtures/api/us/*.json', { eager: true, import: 'default' });
const P5_US = { board_us: P5_US_FX['../fixtures/api/us/board_high.json'] as object };

async function serious(root: Element): Promise<string[]> {
  const res = await axe.run(root, { resultTypes: ['violations'], rules: { 'color-contrast': { enabled: false } } });
  return res.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
}

describe('셸 + W2 모듈', () => {
  it('빌드가 페이지 1·2·4·10(P3)·5·8(P4)·6·9(P5 — 자리표시부터)·7(P6)·3(P7)·11·12(P8)·13(P9) 모듈을 모두 찾는다', () => {
    const want = [...P3_PAGES, ...P4_PAGES, ...P5_PAGES, ...P6_PAGES, ...P7_PAGES, ...P8_PAGES, ...P9_PAGES].sort(
      (a, b) => a - b,
    );
    expect([...pageLoaders().keys()].sort((a, b) => a - b)).toEqual(want);
  });

  it('로그인: 띠·네 페이지가 오류 없이 붙고 axe 심각 위반 0', async () => {
    const err = vi.spyOn(console, 'error');
    const hz = apiHarness({ ...P5_ETF, ...P5_F2, ...P5_US });
    const root = appRoot();
    const app = mountApp(root, {
      tier: 'login',
      source: hz.source,
      now: fixedClock(),
      storage: null,
      onLogout: () => undefined,
      user: 'me',
    });
    for (const n of P3_PAGES) app.pager.go(n, { smooth: false });
    await app.settled();
    expect(root.querySelectorAll('.ribbon .chip')).toHaveLength(13);
    for (const n of P3_PAGES) {
      const sec = root.querySelector(`#page-${n}`);
      expect(sec?.querySelectorAll('section.panel').length).toBeGreaterThan(1);
      expect(sec?.querySelector('.is-pending .pending-box')?.textContent ?? '').not.toContain('위젯 연결 전');
    }
    expect(root.querySelectorAll('.msg.err')).toHaveLength(0);
    expect(err).not.toHaveBeenCalled();
    expect(await serious(document.body)).toEqual([]);
    app.destroy();
  }, 20_000); // axe 를 문서 전체에 돌린다 — 전체 시험을 병렬로 돌리면 5초를 넘길 수 있다

  it('공개: 로그인 API 0회, 공개 파일만, 로그인 위젯은 자물쇠, axe 심각 위반 0', async () => {
    const hz = publicHarness();
    const root = appRoot();
    const tv = vi.fn(() => () => undefined);
    const app = mountApp(root, {
      tier: 'public',
      source: hz.source,
      now: fixedClock(),
      storage: null,
      tradingview: tv,
    });
    for (const n of P3_PAGES) app.pager.go(n, { smooth: false });
    await app.settled();
    expect(hz.paths().every((u) => u.startsWith('./data/'))).toBe(true);
    expect(hz.paths().some((u) => u.includes('api'))).toBe(false);
    expect(root.querySelectorAll('#page-2 .panel.locked, #page-4 .panel.locked, #page-10 .panel.locked').length).toBe(
      7 + 4 + 4 + 3 + 1, // + P5 페이지 10(ETF 맵·보유 탐색·액티브 동시 편입) + P5 2차 IDX(지수 정기변경)
    );
    expect(tv).toHaveBeenCalled();
    expect(root.querySelectorAll('.ribbon .chip.locked').length).toBeGreaterThan(0);
    expect(await serious(document.body)).toEqual([]);
    app.destroy();
  }, 20_000);
});
