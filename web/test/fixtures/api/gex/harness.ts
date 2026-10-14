// GEX 시험 도우미 — 합성 픽스처(tests/unit/api/web_fixtures_p7/gex.py 가 만든 이 폴더의 JSON)를 실제 데이터 소스에
// 가짜 fetch 로 물린다. 로그인: /api/gex/levels?scope=…·/api/gex/profile?scope=…·/api/gex/metrics?name=…·/api/gex/flow.
import { createApiSource } from '../../../../src/data/api';
import type { DataSource } from '../../../../src/data/source';
import { type FakeCall, fakeFetch, json } from '../../../helpers';

const FX = import.meta.glob<unknown>('./*.json', { eager: true, import: 'default' });

/** 이 폴더 기준 이름(확장자 없이) — 깊은 복사 */
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- 시험이 픽스처 모양을 적어 받는다
export function gexFixture<T = Record<string, unknown>>(name: string): T {
  const v = FX[`./${name}.json`];
  if (v === undefined) throw new Error(`GEX 픽스처 없음: ${name}`);
  return structuredClone(v) as T;
}

/** 시험 시계 — 합성 세계 p7_world CLOCK(2026-12-10 14:00 KST) */
export const GEX_NOW = new Date('2026-12-10T05:00:00Z');

export interface GexHarness {
  source: DataSource;
  calls: FakeCall[];
  paths: () => string[];
}

type Over = Record<string, (() => Response) | undefined>;

const NO_DATA = { code: 'no_data', message: '아직 없음 — gex.engine(장중 사이클 — 켜기 전 [WRAP])' };

/** 경로(쿼리 포함, '/api/' 뺀 것) → 픽스처. levels·profile 은 범위마다(0dte 프로필은 all 것을 쓴다), metrics 는 이름마다 */
export function gexHarness(over: Over = {}): GexHarness {
  const { fetch, calls } = fakeFetch((url) => {
    const path = url.replace(/^\/api\//, '');
    const base = path.split('?')[0] ?? '';
    const o = over[path] ?? over[base];
    if (o) return o();
    const q = new URLSearchParams(path.split('?')[1] ?? '');
    if (base === 'gex/levels') {
      const name = `levels_${q.get('scope') ?? 'all'}`;
      return `./${name}.json` in FX ? json(gexFixture(name)) : json(gexFixture('levels_all'));
    }
    if (base === 'gex/profile') return json(gexFixture('profile_all'));
    if (base === 'gex/metrics') {
      const name = `metrics_${q.get('name') ?? ''}`;
      return `./${name}.json` in FX ? json(gexFixture(name)) : json(NO_DATA, 404);
    }
    if (base === 'gex/flow') return json(gexFixture('flow'));
    return json(NO_DATA, 404);
  });
  return { source: createApiSource({ fetch }), calls, paths: () => calls.map((c) => c.url.replace(/^\/api\//, '')) };
}

export { NO_DATA };
