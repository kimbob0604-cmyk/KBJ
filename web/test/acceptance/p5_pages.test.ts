// P5 완료 시험(docs/p5_design.md §0.3·§8.6 — 웨이브 3 WRAP): 셸 + 빌드가 찾은 실제 모듈로 페이지 1·2·5·6·9·10 이
// 합성 데이터(p5_world 로 만든 웹 픽스처 — test/fixtures/api/{macro,program,etf,us}/** + P3·P4 픽스처)로 그려진다.
// - 로그인: console.error 0, 오류 줄 0, DOM 글자에 NaN·undefined·Infinity 0, 390px 폭에서 가로 넘침 원인 0(고정 폭 SVG·
//   390px 넘는 인라인 폭 없음 — jsdom 은 배치를 계산하지 않으므로 구성으로 본다), axe 심각 위반 0.
// - 공개: 로그인 API 0회(공개 파일만), 로그인 패널은 자물쇠 — 공개 패널만 그린다.
// 페이지 9(테마·밸류체인)는 사전 통합을 사용자가 따로 진행(2026-10-13 Q1) — P3 자리표시 그대로인지 본다.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mountApp } from '../../src/app/app';
import { route } from '../../src/app/route';
import { appRoot, fakeFetch, fixedClock, json } from '../helpers';
import { createApiSource } from '../../src/data/api';
import { macroFixture } from '../fixtures/api/macro/harness';
import { apiPath, p4PublicHarness, p4Response, seriousA11y } from './support_p4';

const P5 = import.meta.glob<unknown>('../fixtures/api/{program,etf,us}/*.json', { eager: true, import: 'default' });
const PAGES = [1, 2, 5, 6, 9, 10] as const;
const NOW = new Date('2026-11-17T07:30:00Z'); // 합성 세계 CLOCK(2026-11-17 16:30 KST)

function p5(name: string): Response {
  const v = P5[`../fixtures/api/${name}.json`];
  if (v === undefined) throw new Error(`P5 픽스처 없음: ${name}`);
  return json(structuredClone(v));
}

/** 로그인 응답 — P5 경로는 P5 픽스처, 그 밖은 P4·P3 픽스처(support_p4.p4Response) */
function p5Response(url: string): Response {
  const path = apiPath(url);
  const q = new URLSearchParams(url.split('?')[1] ?? '');
  if (path === 'macro/tabs') return json(macroFixture('tabs'));
  if (path === 'market/ribbon') return json(macroFixture('ribbon'));
  if (path === 'macro/series') {
    const tab = q.get('tab') ?? '';
    if (['rates', 'prices', 'us', 'market'].includes(tab)) return json(macroFixture(`series_${tab}`));
    return json({ code: 'no_data', message: '아직 없음 — macro.monthly 09:10' }, 404);
  }
  if (path === 'flows/market-minute') return p5('program/minute_k200_fut');
  if (path === 'program/daily') return p5(q.get('market') === 'Q' ? 'program/daily_Q' : 'program/daily_K');
  if (path === 'program/top') return p5('program/top_K_buy_intraday');
  if (path === 'etf/map') return p5('etf/map_type_1m');
  if (path === 'etf/active-consensus') return p5('etf/active_consensus');
  if (path.startsWith('etf/holders/')) return p5('etf/holders_none');
  if (path.startsWith('etf/fund/')) return p5('etf/fund_holdings_semi');
  if (path === 'board/us') return p5(q.get('basis') === 'high' ? 'us/board_high' : 'us/board_close');
  return p4Response(url);
}

/** 화면 글자에 계산 실패 흔적(NaN·undefined·Infinity)이 없다 */
function badWords(el: Element): string[] {
  return el.textContent.match(/\b(NaN|undefined|-?Infinity)\b/g) ?? [];
}

/** 390px 폭 가로 넘침 원인(구성): 고정 폭 SVG, 390px 넘는 인라인 width·min-width */
function overflowCauses(el: Element): string[] {
  const out: string[] = [];
  for (const svg of el.querySelectorAll('svg')) {
    const w = Number(svg.getAttribute('width') ?? 'NaN');
    if (Number.isFinite(w) && w > 390) out.push(`svg width=${w}`);
  }
  for (const x of el.querySelectorAll<HTMLElement>('[style]')) {
    for (const prop of ['width', 'minWidth'] as const) {
      const m = /^(\d+(?:\.\d+)?)px$/.exec(x.style[prop]);
      if (m && Number(m[1]) > 390) out.push(`${x.tagName}.${prop}=${x.style[prop]}`);
    }
  }
  return out;
}

afterEach(() => {
  route.reset();
});

describe('P5 페이지 — 합성 데이터로 그려진다', () => {
  it('로그인: 1·2·5·6·9·10 이 오류·NaN·undefined·Infinity 0, 가로 넘침 원인 0, axe 심각 위반 0', async () => {
    window.history.replaceState(null, '', '#/p/1');
    const err = vi.spyOn(console, 'error');
    const { fetch, calls } = fakeFetch((url) => p5Response(url));
    const root = appRoot();
    const app = mountApp(root, {
      tier: 'login',
      source: createApiSource({ fetch }),
      now: fixedClock(NOW),
      storage: null,
      user: 'me',
      onLogout: () => undefined,
    });
    for (const n of PAGES) {
      app.pager.go(n, { smooth: false });
      await app.settled();
    }
    for (const n of PAGES) {
      const sec = root.querySelector(`#page-${n}`);
      if (!sec) throw new Error(`페이지 ${n} 없음`);
      expect(sec.querySelectorAll('section.panel').length, `페이지 ${n}`).toBeGreaterThan(0);
      expect(sec.querySelectorAll('.msg.err'), `페이지 ${n}`).toHaveLength(0);
      expect(badWords(sec), `페이지 ${n}`).toEqual([]);
      expect(overflowCauses(sec), `페이지 ${n}`).toEqual([]);
    }
    // P5 패널이 실제로 P5 경로를 불렀다(자리표시가 아니다)
    const paths = calls.map((c) => apiPath(c.url));
    for (const p of ['flows/market-minute', 'program/daily', 'program/top', 'board/us', 'macro/tabs', 'etf/map']) {
      expect(paths, p).toContain(p);
    }
    // 페이지 9 는 자리표시(테마 사전 통합은 사용자 별도) — 요청 없음
    expect(paths.some((p) => p.startsWith('themes'))).toBe(false);
    expect(root.querySelector('#page-9 .panel.is-pending')).not.toBeNull();
    expect(err).not.toHaveBeenCalled();
    expect(await seriousA11y(document.body)).toEqual([]);
    app.destroy();
  }, 60_000);

  it('공개: 로그인 API 0회, 공개 파일만, 로그인 패널은 자물쇠(공개 빌드는 공개 패널만)', async () => {
    window.history.replaceState(null, '', '#/p/1');
    const pub: Record<string, unknown> = { credit: macroFixture('public/credit') };
    for (const n of ['tabs', 'rates', 'prices', 'us', 'calendar']) pub[`macro/${n}`] = macroFixture(`public/macro/${n}`);
    const hz = p4PublicHarness(pub);
    const root = appRoot();
    const app = mountApp(root, {
      tier: 'public',
      source: hz.source,
      now: fixedClock(NOW),
      storage: null,
      tradingview: () => () => undefined,
    });
    for (const n of PAGES) {
      app.pager.go(n, { smooth: false });
      await app.settled();
    }
    expect(hz.paths().every((u) => u.startsWith('./data/'))).toBe(true);
    expect(hz.paths().some((u) => /api|program|flows|etf|board/.test(u))).toBe(false);
    for (const n of PAGES) {
      const sec = root.querySelector(`#page-${n}`);
      if (!sec) throw new Error(`페이지 ${n} 없음`);
      expect(badWords(sec), `페이지 ${n}`).toEqual([]);
      expect(overflowCauses(sec), `페이지 ${n}`).toEqual([]);
    }
    // 로그인 등급 P5 패널(1분 수급·프로그램·간밤 미국·ETF 맵·매크로 시장 탭)은 자물쇠
    for (const n of [1, 2, 6, 9, 10]) {
      expect(root.querySelectorAll(`#page-${n} .panel.locked`).length, `페이지 ${n}`).toBeGreaterThan(0);
    }
    const errs = [...root.querySelectorAll('.msg.err')].map((e) => e.closest('section.panel')?.querySelector('h3')?.textContent ?? '?');
    expect(errs).toEqual([]);
    app.destroy();
  }, 60_000);
});
