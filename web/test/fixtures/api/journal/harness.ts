// 저널·포트폴리오 시험 도우미(묶음 JNL) — 합성 픽스처(tests/unit/api/web_fixtures_p8/journal.py 가 만든 이 폴더의 JSON)를
// 실제 데이터 소스에 가짜 fetch 로 물린다. 쓰기(post·patch·put·del)는 데이터 소스의 선택 기능 — withJournalWriter 로 붙인다
// (쓰기 채널은 아직 공용 소스에 없다 — 백테스트 harness 와 같은 약속).
import { createApiSource } from '../../../../src/data/api';
import type { DataSource, Envelope } from '../../../../src/data/source';
import { type FakeCall, fakeFetch, json } from '../../../helpers';

const FX = import.meta.glob<unknown>('./*.json', { eager: true, import: 'default' });

/** 이 폴더 기준 이름(확장자 없이) — 깊은 복사 */
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- 시험이 픽스처 모양을 적어 받는다
export function jrFixture<T = Record<string, unknown>>(name: string): T {
  const v = FX[`./${name}.json`];
  if (v === undefined) throw new Error(`저널 픽스처 없음: ${name}`);
  return structuredClone(v) as T;
}

/** 시험 시계 — API 시험 세계(api_world) 마감 뒤 2026-09-15 18:00 KST */
export const JR_NOW = new Date('2026-09-15T09:00:00Z');

/** 경로(쿼리 포함 먼저, 없으면 쿼리 뺀 것) → 픽스처 */
const ROUTES: Readonly<Record<string, string>> = {
  'journal/analysis': 'analysis',
  'journal/analysis/1': 'analysis_1',
  'journal/analysis/1/markdown': 'analysis_1_markdown',
  'journal/analysis/prefill/990002': 'prefill_990002',
  'journal/trades': 'trades',
  'journal/picks': 'picks',
  portfolio: 'portfolio',
  'portfolio/correlation?n=60&set=watchlist': 'corr_watchlist_60',
  'portfolio/correlation?n=120&set=watchlist': 'corr_watchlist_120',
  'portfolio/correlation?n=60&set=holdings': 'corr_holdings_60',
  'watchlist?list=main': 'watchlist',
};

export const EMPTY_ROUTES: Readonly<Record<string, string>> = {
  'journal/analysis': 'analysis_empty',
  'journal/trades': 'trades_empty',
  'journal/picks': 'picks_empty',
  portfolio: 'portfolio_empty',
  'portfolio/correlation': 'corr_empty',
};

function respond(name: string): Response {
  const body = jrFixture(name);
  const st = typeof body.status === 'number' ? body.status : 200;
  return json(body, st);
}

export interface JrHarness {
  source: DataSource;
  calls: FakeCall[];
  paths: () => string[];
}

/** 로그인 API. empty = 빈 세계 응답, over 로 바꿔 끼운다(없는 경로는 404 no_data) */
export function jrHarness(opts: { empty?: boolean; over?: Record<string, () => Response> } = {}): JrHarness {
  const routes = opts.empty ? EMPTY_ROUTES : ROUTES;
  const { fetch, calls } = fakeFetch((url) => {
    const path = url.replace(/^\/api\//, '');
    const base = path.split('?')[0] ?? '';
    const o = opts.over?.[path] ?? opts.over?.[base];
    if (o) return o();
    const name = routes[path] ?? routes[base];
    return name ? respond(name) : json({ code: 'no_data', message: '아직 없음 — 시험 경로 없음' }, 404);
  });
  return { source: createApiSource({ fetch }), calls, paths: () => calls.map((c) => c.url.replace(/^\/api\//, '')) };
}

export interface WriteCall {
  method: 'post' | 'patch' | 'put' | 'del';
  key: string;
  body?: unknown;
}

type Writer = {
  post: <T>(key: string, body: unknown) => Promise<Envelope<T>>;
  patch: <T>(key: string, body: unknown) => Promise<Envelope<T>>;
  put: <T>(key: string, body?: unknown) => Promise<Envelope<T>>;
  del: <T>(key: string) => Promise<Envelope<T>>;
};

/** 쓰기 채널을 붙인 소스 — fail 이 Error 를 돌려주면 그 호출은 실패한다 */
export function withJournalWriter(
  source: DataSource,
  fail: (c: WriteCall) => Error | null = () => null,
): { source: DataSource & Writer; writes: WriteCall[] } {
  const writes: WriteCall[] = [];
  const run = <T>(c: WriteCall): Promise<Envelope<T>> => {
    writes.push(c);
    const err = fail(c);
    if (err) return Promise.reject(err);
    return Promise.resolve(jrFixture<Envelope<T>>('analysis_1'));
  };
  const writer: Writer = {
    post: (key, body) => run({ method: 'post', key, body }),
    patch: (key, body) => run({ method: 'patch', key, body }),
    put: (key, body) => run({ method: 'put', key, body }),
    del: (key) => run({ method: 'del', key }),
  };
  return { source: Object.assign(Object.create(source) as DataSource, source, writer), writes };
}
