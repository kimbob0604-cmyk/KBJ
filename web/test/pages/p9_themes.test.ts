// 페이지 9 테마·밸류체인(ADR 0039). 합성 픽스처(tests/unit/api/web_fixtures_p9/themes.py 가 만든
// test/fixtures/api/themes/*.json — 합성 테마 마스터, 실제 stock-dashboard 내용 아님).
// 공개판: 대테마 분류·밸류체인(공개 파일 themes.json 만) + 로그인 패널 넷 자물쇠(요청 0).
// 로그인판: 강도 순위 표·히트맵·밸류체인 강도·편입 종목(종목은 #/p/8/<코드>). 404 는 '.msg', 그 밖 실패만 '.msg.err'.
import { afterEach, describe, expect, it } from 'vitest';
import { route } from '../../src/app/route';
import { createApiSource } from '../../src/data/api';
import { createStaticSource } from '../../src/data/static';
import type { DataSource } from '../../src/data/source';
import p9, { P9_LOGIN_PANELS, P9_SECTIONS } from '../../src/pages/p9_themes';
import { CATALOG_PANELS } from '../../src/pages/p9/catalog';
import { type FakeCall, fakeFetch, json } from '../helpers';
import { flush, pageCtx, text } from './support';

const FX = import.meta.glob<unknown>('../fixtures/api/themes/*.json', { eager: true, import: 'default' });

// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- 시험이 픽스처 모양을 적어 받는다
function fx<T = Record<string, unknown>>(name: string): T {
  const v = FX[`../fixtures/api/themes/${name}.json`];
  if (v === undefined) throw new Error(`테마 픽스처 없음: ${name}`);
  return structuredClone(v) as T;
}

type Over = Record<string, (() => Response) | undefined>;

function loginHarness(over: Over = {}): { source: DataSource; calls: FakeCall[]; paths: () => string[] } {
  const { fetch, calls } = fakeFetch((url) => {
    const [p = '', q = ''] = url.replace(/^\/api\//, '').split('?');
    const o = over[p];
    if (o) return o();
    if (p === 'themes') return json(fx('catalog'));
    if (p === 'themes/strength') return json(fx(`strength_${new URLSearchParams(q).get('period') ?? '1'}`));
    if (p.startsWith('themes/')) return json(fx(p.endsWith('_1_1') ? 'detail' : 'detail_plain'));
    return json(fx('none'), 404);
  });
  return { source: createApiSource({ fetch }), calls, paths: () => calls.map((c) => c.url.replace(/^\/api\//, '')) };
}

function publicHarness(): { source: DataSource; calls: FakeCall[] } {
  const { fetch, calls } = fakeFetch((url) => (url === './data/themes.json' ? json(fx('public')) : json({}, 404)));
  return { source: createStaticSource({ fetch }), calls };
}

const NOW = () => new Date('2026-09-15T09:00:00Z');
const titles = (root: HTMLElement): string[] =>
  [...root.querySelectorAll('section.panel')].map((p) => p.querySelector('h3 > span')?.textContent ?? '');
const err = (root: ParentNode): number => root.querySelectorAll('.msg.err').length;

afterEach(() => {
  route.reset();
});

describe('페이지 9 — 배치', () => {
  it('패널 여섯 순서·칸 폭(분류 8·밸류체인 4·강도 12·히트맵 6·체인 강도 6·편입 12)', async () => {
    const hz = loginHarness();
    const { ctx, root } = pageCtx(9, 'login', hz.source, { now: NOW });
    await p9.mount(ctx);
    await flush();
    expect(P9_SECTIONS).toHaveLength(6);
    const panels = [...root.querySelectorAll('section.panel')];
    expect(panels.map((p) => /span-(\d+)/.exec(p.className)?.[1])).toEqual(['8', '4', '12', '6', '6', '12']);
    expect(titles(root).slice(0, 5)).toEqual([
      CATALOG_PANELS.sections.title,
      CATALOG_PANELS.chains.title,
      P9_LOGIN_PANELS.rank.title,
      P9_LOGIN_PANELS.heat.title,
      P9_LOGIN_PANELS.chain_strength.title,
    ]);
    // 차트(히트맵)는 반폭 이하
    expect(root.querySelector('#p9-heat')?.className).toContain('span-6');
    expect(root.querySelector('#p9-heat svg')).not.toBeNull();
    expect(err(root)).toBe(0);
    expect(text(root)).not.toMatch(/NaN|undefined|Infinity/);
  });
});

describe('페이지 9 — 공개판', () => {
  it('공개 파일 themes.json 만 — 로그인 패널 넷은 자물쇠(요청 0)', async () => {
    const hz = publicHarness();
    const { ctx, root } = pageCtx(9, 'public', hz.source, { now: NOW });
    await p9.mount(ctx);
    await flush();
    expect(hz.calls.map((c) => c.url)).toEqual(['./data/themes.json']);
    expect(root.querySelectorAll('.panel.locked')).toHaveLength(4);
    const pub = fx<{ data: { n_themes: number } }>('public');
    expect(root.querySelectorAll('.theme-section .tag').length).toBe(pub.data.n_themes);
    expect(root.querySelector('.theme-section button')).toBeNull(); // 공개판은 고르기 없음
    expect(text(root)).not.toContain('원본 테마');
    expect(err(root)).toBe(0);
  });
});

describe('페이지 9 — 로그인판', () => {
  it('분류·강도(당일)·1위 대테마 편입 종목을 부르고, 기간 토글·대테마 고르기가 다시 부른다', async () => {
    const hz = loginHarness();
    const { ctx, root } = pageCtx(9, 'login', hz.source, { now: NOW });
    await p9.mount(ctx);
    await flush();
    const s1 = fx<{ data: { rows: { id: string; name: string }[] } }>('strength_1');
    const first = s1.data.rows[0]?.id ?? '?';
    expect(hz.paths()).toEqual(['themes', 'themes/strength?period=1', `themes/${first}`]);
    const rank = root.querySelector('#p9-rank');
    expect(rank?.querySelectorAll('tbody tr').length).toBe(s1.data.rows.length);
    expect(text(rank?.querySelector('tbody tr'))).toContain(s1.data.rows[0]?.name ?? '?');
    // 기간 5일
    rank?.querySelector<HTMLButtonElement>('button[data-value="5"]')?.click();
    await flush();
    expect(hz.paths()).toContain('themes/strength?period=5');
    // 분류 칩을 누르면 그 대테마 편입 종목
    root.querySelector<HTMLButtonElement>('.theme-section button[data-theme="syn_1_1"]')?.click();
    await flush();
    expect(hz.paths().at(-1)).toBe('themes/syn_1_1');
    const members = root.querySelector('#p9-members');
    const detail = fx<{ data: { name: string; members: { code: string }[] } }>('detail');
    expect(text(members?.querySelector('h3'))).toContain(detail.data.name);
    expect(members?.querySelectorAll('tbody tr').length).toBe(Math.min(50, detail.data.members.length));
    expect(text(members)).toContain('합성 편입 사유');
    // 종목은 #/p/8/<코드>
    const code = detail.data.members[0]?.code ?? '?';
    const a = members?.querySelector<HTMLAnchorElement>('tbody tr a');
    expect(a?.getAttribute('href')).toBe(`#/p/8/${code}`);
    a?.click();
    expect(route.getCode()).toBe(code);
    expect(err(root)).toBe(0);
  });

  it('강도 셋·편입 종목은 서버가 고른 시세 날(마지막 보드 날)을 보인다 — 오늘 보드 전이면 지난 거래일', async () => {
    const DAY = '2026-09-11'; // 주말·오늘 보드 전 — 서버가 지난 보드 날을 골랐다
    const strength = (): Response => {
      const b = fx<{ data: { trade_date: string } }>('strength_1');
      b.data.trade_date = DAY;
      return json(b);
    };
    const detail = (): Response => {
      const b = fx<{ data: { trade_date: string | null } }>('detail');
      b.data.trade_date = DAY;
      return json(b);
    };
    const s1 = fx<{ data: { rows: { id: string }[] } }>('strength_1');
    const first = s1.data.rows[0]?.id ?? '?';
    const hz = loginHarness({ 'themes/strength': strength, [`themes/${first}`]: detail });
    const { ctx, root } = pageCtx(9, 'login', hz.source, { now: NOW });
    await p9.mount(ctx);
    await flush();
    for (const id of ['rank', 'heat', 'chain_strength']) {
      expect(text(root.querySelector(`#p9-${id} [data-section="asof"]`))).toContain(`시세 ${DAY} 마감`);
    }
    expect(text(root.querySelector('#p9-members [data-section="members-head"]'))).toContain(`시세 ${DAY} 마감`);
    expect(err(root)).toBe(0);
    // 상세에 시세가 없으면(보드 전) 그렇게 적는다
    const plain = fx<{ data: { trade_date: string | null } }>('detail_plain');
    plain.data.trade_date = null;
    const hz2 = loginHarness({ [`themes/${first}`]: () => json(plain) });
    const c2 = pageCtx(9, 'login', hz2.source, { now: NOW });
    await p9.mount(c2.ctx);
    await flush();
    expect(text(c2.root.querySelector('#p9-members [data-section="members-head"]'))).toContain('시세 없음');
  });

  it('수집 전 404 는 안내 줄(.msg — 작업 이름), 진짜 실패만 .msg.err', async () => {
    const none = (): Response => json(fx('none'), 404);
    const hz = loginHarness({ themes: none, 'themes/strength': () => json(fx('strength_none'), 404) });
    const { ctx, root } = pageCtx(9, 'login', hz.source, { now: NOW });
    await p9.mount(ctx);
    await flush();
    expect(err(root)).toBe(0);
    expect(text(root)).toContain('theme.classes_sync');
    expect(text(root.querySelector('#p9-rank'))).toContain('board.daily');
    expect(text(root.querySelector('#p9-members'))).toContain('board.daily');

    const hz2 = loginHarness({ 'themes/strength': () => json({ code: 'internal', message: 'x' }, 500) });
    const c2 = pageCtx(9, 'login', hz2.source, { now: NOW });
    await p9.mount(c2.ctx);
    await flush();
    expect(c2.root.querySelector('#p9-rank .msg.err')).not.toBeNull();
    expect(c2.root.querySelector('.theme-section')).not.toBeNull(); // 분류 패널은 계속(격리)
  });
});
