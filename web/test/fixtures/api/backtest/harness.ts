// 백테스트·성과 시험 도우미 — 합성 픽스처(tests/unit/api/web_fixtures_p8/backtest.py 가 만든 이 폴더의 JSON)를 실제
// 데이터 소스에 가짜 fetch 로 물린다. 로그인: /api/rules/stats·/api/backtest/runs·/api/backtest/match.
// 즉석 백테스트(POST)는 데이터 소스의 선택 기능 `post` — withWriter 로 붙인다(쓰기 채널은 아직 공용 소스에 없다).
import { createApiSource } from '../../../../src/data/api';
import type { DataSource, Envelope } from '../../../../src/data/source';
import { type FakeCall, fakeFetch, json } from '../../../helpers';

const FX = import.meta.glob<unknown>('./*.json', {
  eager: true,
  import: 'default',
});

/** 이 폴더 기준 이름(확장자 없이) — 깊은 복사 */
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- 시험이 픽스처 모양을 적어 받는다
export function btFixture<T = Record<string, unknown>>(name: string): T {
  const v = FX[`./${name}.json`];
  if (v === undefined) throw new Error(`백테스트 픽스처 없음: ${name}`);
  return structuredClone(v) as T;
}

/** 시험 시계 — 합성 세계 p8_world clock(2026-10-30 16:00 KST) */
export const BT_NOW = new Date('2026-10-30T07:00:00Z');

export const NO_DATA = {
  code: 'no_data',
  message: '아직 없음 — rules.backtest_nightly 08:45',
};

export interface BtHarness {
  source: DataSource;
  calls: FakeCall[];
  paths: () => string[];
}

type Over = Record<string, (() => Response) | undefined>;

const ROUTES: Readonly<Record<string, string>> = {
  'rules/stats': 'stats',
  'backtest/runs': 'runs',
  'backtest/match': 'match',
};

/** 로그인 API — 경로(쿼리 뺀 것) → 픽스처. over 로 바꿔 끼운다(없는 경로는 404) */
export function btHarness(over: Over = {}): BtHarness {
  const { fetch, calls } = fakeFetch((url) => {
    const path = url.replace(/^\/api\//, '');
    const base = path.split('?')[0] ?? '';
    const o = over[path] ?? over[base];
    if (o) return o();
    const name = ROUTES[base];
    return name ? json(btFixture(name)) : json(NO_DATA, 404);
  });
  return {
    source: createApiSource({ fetch }),
    calls,
    paths: () => calls.map((c) => c.url.replace(/^\/api\//, '')),
  };
}

export interface PostCall {
  key: string;
  body: unknown;
}

/** 쓰기 채널을 붙인 소스 — post 는 respond 결과(기본 adhoc 픽스처)를 돌려준다 */
export function withWriter(
  source: DataSource,
  respond: (key: string, body: unknown) => Promise<Envelope<unknown>> = () =>
    Promise.resolve(btFixture<Envelope<unknown>>('adhoc')),
): {
  source: DataSource & {
    post: <T>(key: string, body: unknown) => Promise<Envelope<T>>;
  };
  posts: PostCall[];
} {
  const posts: PostCall[] = [];
  const post = async <T>(key: string, body: unknown): Promise<Envelope<T>> => {
    posts.push({ key, body });
    return (await respond(key, body)) as Envelope<T>;
  };
  return {
    source: Object.assign(Object.create(source) as DataSource, source, {
      post,
    }),
    posts,
  };
}
