// P4 페이지 시험 도우미(W2 — 페이지 5·8) — A 의 합성 응답(test/fixtures/api/{stock,filings,watchlist}/**)과 X 의 공개 파일
// (test/fixtures/public/**)을 실제 데이터 소스(createApiSource·createStaticSource)에 가짜 fetch 로 물린다.
// 벽시계·네트워크에 기대지 않는다(CLAUDE.md §4). P3 도우미(test/pages/support.ts)는 그대로 두고 그 위에 얹는다.
//
// - 로그인: '/api/stock/<코드>/<섹션>' → stock/<코드>/<섹션>.json, HTTP 상태는 stock/_index.json(A 가 처리기 출력으로 만든 표 —
//   404 이면 픽스처 본문이 {code:'no_data', message}). 표에 없는 코드는 404 {code:'unknown_code'}(§5.2).
//   '/api/flows/stock/<코드>' → stock/<코드>/flows.json, '/api/filings/<이름>' → filings/<이름>.json,
//   '/api/watchlist' → watchlist/main.json, '/api/stock/search' → stock/search.json. 그 밖(P3 경로)은 P3 픽스처.
// - 공개: './data/<이름>.json' → fixtures/public/<이름>.json(하위 폴더 fin/·reports/ 포함). 없으면 404.
import axe from 'axe-core';
import { pageDef } from '../../src/app/pages';
import type { PageContext } from '../../src/app/types';
import { createApiSource } from '../../src/data/api';
import type { DataSource, Tier } from '../../src/data/source';
import { createStaticSource } from '../../src/data/static';
import { type FakeCall, fakeFetch, fixedClock, json } from '../helpers';
import { fixture as p3Fixture, fixtureName as p3FixtureName, type EveryCall, type Harness } from '../pages/support';

/** P4 픽스처의 '지금' — A 의 합성 응답 generated_at(2026-11-16 21:30 KST)과 같은 날 */
export const P4_NOW = new Date('2026-11-16T12:30:00Z');

const API = import.meta.glob<unknown>('../fixtures/api/{stock,filings,watchlist}/**/*.json', { eager: true, import: 'default' });
const PUB = import.meta.glob<unknown>('../fixtures/public/**/*.json', { eager: true, import: 'default' });

/** 섹션 → HTTP 상태(+ report_xlsx) */
export type StatusIndex = Record<string, Record<string, number>>;

function apiFixture(rel: string): unknown {
  const v = API[`../fixtures/api/${rel}.json`];
  if (v === undefined) throw new Error(`픽스처 없음: ${rel}`);
  return structuredClone(v);
}

export const STATUS = apiFixture('stock/_index') as StatusIndex;

/** 합성 20종목(관심종목 19 + 관심 밖 1 — p4_world) */
export const CODES: readonly string[] = Object.keys(STATUS);

/** 페이지 8 섹션 라우트(머리 제외) — 픽스처 파일 이름과 같다 */
export const SECTIONS = [
  'chart',
  'financials',
  'report',
  'segments',
  'consensus',
  'valuation',
  'scenario',
  'overhang',
  'insiders',
  'disclosures',
  'earnings',
] as const;

/** 종목 섹션 픽스처 본문(봉투 또는 404 본문) */
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- 시험이 픽스처 모양을 적어 받는다
export function stockFixture<T = Record<string, unknown>>(code: string, section: string): T {
  return apiFixture(`stock/${code}/${section}`) as T;
}

// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- 시험이 픽스처 모양을 적어 받는다
export function p4Fixture<T = Record<string, unknown>>(rel: string): T {
  return apiFixture(rel) as T;
}

/** 공개 파일(이름 — 'filings_feed'·'fin/Q00001') */
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- 시험이 파일 모양을 적어 받는다
export function publicFixture<T = Record<string, unknown>>(name: string): T {
  const v = PUB[`../fixtures/public/${name}.json`];
  if (v === undefined) throw new Error(`공개 픽스처 없음: ${name}`);
  return structuredClone(v) as T;
}

/** '/api/stock/994010/valuation?metric=per' → 'stock/994010/valuation'(덮어쓰기 키) */
export function apiPath(url: string): string {
  return url.replace(/^\/api\//, '').split('?')[0] ?? '';
}

/** 로그인 응답 — P4 경로는 P4 픽스처(상태 표 그대로), 그 밖은 P3 픽스처 */
export function p4Response(url: string): Response {
  const path = apiPath(url);
  if (path === 'stock/search') return json(apiFixture('stock/search'));
  if (path === 'watchlist') return json(apiFixture('watchlist/main'));
  let m = /^filings\/([a-z_]+)$/.exec(path);
  if (m?.[1]) return json(apiFixture(`filings/${m[1]}`));
  m = /^flows\/stock\/([0-9A-Z]{6})$/.exec(path);
  const sm = /^stock\/([0-9A-Z]{6})\/([a-z_]+)$/.exec(path);
  const code = m?.[1] ?? sm?.[1];
  const section = m ? 'flows' : sm?.[2];
  if (code && section) {
    const st = STATUS[code];
    if (!st) return json({ code: 'unknown_code', message: '모르는 종목코드' }, 404);
    return json(apiFixture(`stock/${code}/${section}`), st[section] ?? 200);
  }
  return json(p3Fixture(p3FixtureName(url)));
}

/** 덮어쓰기: 경로(쿼리 뺀 것) → 본문 또는 Response 를 만드는 함수 */
export type P4OverrideFn = (url: string) => Response | Promise<Response>;
export type P4Override = object | P4OverrideFn;

function isFn(o: P4Override | undefined): o is P4OverrideFn {
  return typeof o === 'function';
}

/** 로그인 데이터 소스 — 경로별 P4 픽스처. overrides 의 키는 apiPath(예 'stock/994010/valuation') */
export function p4ApiHarness(overrides: Record<string, P4Override> = {}): Harness {
  const { fetch, calls } = fakeFetch((url) => {
    const o = overrides[apiPath(url)];
    if (isFn(o)) return o(url);
    if (o !== undefined) return json(o);
    return p4Response(url);
  });
  return { source: createApiSource({ fetch }), calls, paths: () => calls.map((c) => c.url.replace(/^\/api\//, '')) };
}

/** 공개 데이터 소스 — fixtures/public 의 모든 JSON(P3 calendar·events·manifest + P4 파일). overrides: 이름 → 본문(null 이면 404) */
export function p4PublicHarness(overrides: Record<string, unknown> = {}): Harness {
  const { fetch, calls } = fakeFetch((url) => {
    const name = url.replace(/^\.\/data\//, '').replace(/\.json$/, '');
    if (name in overrides) {
      const o = overrides[name];
      return o === null ? json({}, 404) : json(o);
    }
    const v = PUB[`../fixtures/public/${name}.json`];
    return v === undefined ? json({}, 404) : json(structuredClone(v));
  });
  return { source: createStaticSource({ fetch }), calls, paths: () => calls.map((c) => c.url) };
}

/** 페이지 문맥(P4 시계) — 격자를 문서에 붙이고, 주기 실행은 기록만 한다 */
export function p4PageCtx(
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
    now: fixedClock(P4_NOW),
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

/** 부른 요청 기록에서 로그인 API 경로만 */
export function apiCalls(calls: readonly FakeCall[]): string[] {
  return calls.map((c) => c.url).filter((u) => u.startsWith('/api/'));
}

// ── 완료 시험 단언(§0.3·§6.5) ─────────────────────────────

/** DOM 에 나오면 안 되는 낱말(값 서식 실패의 흔적) */
export const FORBIDDEN = /\b(NaN|undefined|Infinity|null)\b/;

/** 글자와 속성값에서 금지 낱말을 찾는다 — 찾은 곳(요소·속성) 목록 */
export function forbiddenHits(root: Element): string[] {
  const out: string[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = n.textContent ?? '';
    if (FORBIDDEN.test(t)) out.push(`text: ${t.slice(0, 80)}`);
  }
  for (const el of root.querySelectorAll('*')) {
    for (const a of el.attributes) {
      if (FORBIDDEN.test(a.value)) out.push(`${el.tagName.toLowerCase()}[${a.name}]: ${a.value.slice(0, 80)}`);
    }
  }
  return out;
}

/** axe-core 심각(serious·critical) 위반 — 색 대비는 jsdom 이 계산하지 못해 끈다(P3 와 같음) */
export async function seriousA11y(root: Element): Promise<string[]> {
  const res = await axe.run(root, { resultTypes: ['violations'], rules: { 'color-contrast': { enabled: false } } });
  return res.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
}
