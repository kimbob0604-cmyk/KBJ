// 페이지 시험 도우미(W2) — A 의 합성 응답 픽스처(test/fixtures/api/*.json)를 실제 데이터 소스(createApiSource·
// createStaticSource)에 가짜 fetch 로 물린다. 벽시계·네트워크에 기대지 않는다(CLAUDE.md §4).
import { pageDef } from '../../src/app/pages';
import type { PageContext } from '../../src/app/types';
import { createApiSource } from '../../src/data/api';
import type { DataSource, Tier } from '../../src/data/source';
import { createStaticSource } from '../../src/data/static';
import { type FakeCall, fakeFetch, fixedClock, json } from '../helpers';
import publicCalendar from './fixtures/public_calendar.json';
import publicEvents from './fixtures/public_events.json';
// 페이지 1 '신용잔고 · 예탁금 · 펀드 자금'(market/credit) — MAC 합성 세계 픽스처(tests/unit/api/web_fixtures_p5/macro.py)
import marketCredit from '../fixtures/api/macro/market_credit.json';

const API = import.meta.glob<unknown>('../fixtures/api/*.json', { eager: true, import: 'default' });
const IDX = import.meta.glob<unknown>('../fixtures/api/index_review/*.json', { eager: true, import: 'default' });

/** 지수 정기변경 픽스처(test/fixtures/api/index_review — tests/unit/api/web_fixtures_p5/index_review.py) */
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- 시험이 픽스처 모양을 적어 받는다
export function idxFixture<T = Record<string, unknown>>(name: string): T {
  const v = IDX[`../fixtures/api/index_review/${name}.json`];
  if (v === undefined) throw new Error(`픽스처 없음: index_review/${name}`);
  return structuredClone(v) as T;
}

/** '/api/index-review…' 기본 응답(P5 2차 IDX) — 페이지 5·8·10 시험이 따로 바꿔 끼우지 않으면 이것 */
export function indexReviewDefault(url: string): unknown {
  const [path = '', qs = ''] = url.replace(/^\/api\//, '').split('?');
  if (path.startsWith('index-review/stock/')) {
    const code = path.slice('index-review/stock/'.length);
    return `../fixtures/api/index_review/stock_${code}.json` in IDX ? idxFixture(`stock_${code}`) : idxFixture('stock_none');
  }
  const q = new URLSearchParams(qs);
  const index = q.get('index') ?? 'msci_korea';
  if (index === 'msci_korea' && q.get('review') === '2026-05') return idxFixture('review_msci_2026-05');
  return idxFixture(index === 'msci_korea' ? 'review_msci' : `review_${index}`);
}

/** 픽스처 이름(경로의 / 를 _ 로 — A 의 규칙)으로 꺼낸다. 깊은 복사라 시험이 고쳐도 다른 시험에 새지 않는다. */
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- 시험이 픽스처 모양을 적어 받는다
export function fixture<T = Record<string, unknown>>(name: string): T {
  const v = API[`../fixtures/api/${name}.json`];
  if (v === undefined) throw new Error(`픽스처 없음: ${name}`);
  return structuredClone(v) as T;
}

/** '/api/flows/stock/Q00010?days=20' → 'flows_stock' */
export function fixtureName(url: string): string {
  const path = url.replace(/^\/api\//, '').split('?')[0] ?? '';
  if (path.startsWith('flows/stock/')) return 'flows_stock';
  return path.replace(/\//g, '_');
}

/** 픽스처 대신 줄 응답 본문, 또는 Response 를 만드는 함수 */
export type OverrideFn = (url: string) => Response | Promise<Response>;
export type Override = object | OverrideFn;

function isFn(o: Override | undefined): o is OverrideFn {
  return typeof o === 'function';
}

export interface Harness {
  source: DataSource;
  calls: FakeCall[];
  /** 부른 API 경로(쿼리 포함, '/api/' 뺀 것) */
  paths(): string[];
}

/** 로그인 데이터 소스 — 경로별 픽스처, overrides 로 바꿔 끼운다(Response 를 주는 함수면 그대로). */
export function apiHarness(overrides: Record<string, Override> = {}): Harness {
  const { fetch, calls } = fakeFetch((url) => {
    const name = fixtureName(url);
    const o = overrides[name];
    if (isFn(o)) return o(url);
    if (o !== undefined) return json(o);
    if (name.startsWith('index-review')) return json(indexReviewDefault(url));
    if (name === 'market_credit') return json(structuredClone(marketCredit));
    return json(fixture(name));
  });
  return {
    source: createApiSource({ fetch }),
    calls,
    paths: () => calls.map((c) => c.url.replace(/^\/api\//, '')),
  };
}

/** 공개 데이터 소스 — public.export 산출 모양의 calendar·events. */
export function publicHarness(over: { calendar?: unknown; events?: unknown } = {}): Harness {
  const { fetch, calls } = fakeFetch((url) => {
    if (url.endsWith('/calendar.json')) return json(over.calendar ?? structuredClone(publicCalendar));
    if (url.endsWith('/events.json')) return json(over.events ?? structuredClone(publicEvents));
    return json({}, 404);
  });
  return { source: createStaticSource({ fetch }), calls, paths: () => calls.map((c) => c.url) };
}

export interface EveryCall {
  ms: number;
  fn: () => void;
}

/** 페이지 문맥 — 격자를 문서에 붙이고, 주기 실행은 기록만 한다(시험이 직접 부른다). */
export function pageCtx(
  n: number,
  tier: Tier,
  source: DataSource,
  extra: Partial<PageContext> = {},
): { ctx: PageContext; root: HTMLElement; every: EveryCall[] } {
  const def = pageDef(n);
  if (!def) throw new Error(`페이지 ${n} 없음`);
  const root = document.createElement('div');
  root.className = 'grid';
  document.body.append(root);
  const every: EveryCall[] = [];
  const ctx: PageContext = {
    tier,
    source,
    now: fixedClock(),
    every: (ms, fn) => {
      every.push({ ms, fn });
      return () => undefined;
    },
    page: def,
    root,
    ...extra,
  };
  return { ctx, root, every };
}

/** 패널 찾기(제목으로) */
export function panelByTitle(root: ParentNode, title: string): HTMLElement {
  for (const p of root.querySelectorAll<HTMLElement>('section.panel')) {
    if (p.querySelector('h3 > span')?.textContent === title) return p;
  }
  throw new Error(`패널 없음: ${title}`);
}

export function text(el: Element | null | undefined): string {
  return (el?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

/** 비동기 처리(가짜 fetch → json → 그리기)가 끝날 때까지 */
export async function flush(times = 5): Promise<void> {
  for (let i = 0; i < times; i++) await new Promise((r) => setTimeout(r, 0));
}

export function click(el: Element | null | undefined): void {
  if (!(el instanceof HTMLElement)) throw new Error('누를 요소 없음');
  el.click();
}

export function choose(sel: HTMLSelectElement | null, value: string): void {
  if (!sel) throw new Error('선택 상자 없음');
  sel.value = value;
  sel.dispatchEvent(new Event('change'));
}

export function buttonByText(root: ParentNode, label: string): HTMLButtonElement {
  const b = [...root.querySelectorAll('button')].find((x) => x.textContent.trim() === label);
  if (!b) throw new Error(`버튼 없음: ${label}`);
  return b;
}

export function selectByLabel(root: ParentNode, label: string): HTMLSelectElement {
  for (const l of root.querySelectorAll('label')) {
    if (l.firstChild?.textContent?.trim() === label) {
      const s = l.querySelector('select');
      if (s) return s;
    }
  }
  throw new Error(`선택 상자 없음: ${label}`);
}

export function notFound(message = '아직 없음 — board.daily 16:00~16:05'): Response {
  return json({ code: 'no_data', message }, 404);
}
