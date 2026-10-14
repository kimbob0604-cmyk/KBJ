// 진단 시험 도우미 — 합성 픽스처(tests/unit/api/web_fixtures_p7/diag.py 가 만든 이 폴더의 JSON)를 실제 데이터 소스에
// 가짜 fetch 로 물린다. 로그인: /api/diag/summary·/api/diag/history?key=…·/api/diag/events, 공개: ./data/diag/*.json.
import { createApiSource } from '../../../../src/data/api';
import type { DataSource } from '../../../../src/data/source';
import { createStaticSource } from '../../../../src/data/static';
import { type FakeCall, fakeFetch, json } from '../../../helpers';

const FX = import.meta.glob<unknown>('./**/*.json', { eager: true, import: 'default' });

/** 이 폴더 기준 이름(확장자 없이) — 깊은 복사 */
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- 시험이 픽스처 모양을 적어 받는다
export function diagFixture<T = Record<string, unknown>>(name: string): T {
  const v = FX[`./${name}.json`];
  if (v === undefined) throw new Error(`진단 픽스처 없음: ${name}`);
  return structuredClone(v) as T;
}

/** 시험 시계 — 합성 세계 p7_world CLOCK(2026-12-10 14:00 KST) */
export const DIAG_NOW = new Date('2026-12-10T05:00:00Z');

export interface DiagHarness {
  source: DataSource;
  calls: FakeCall[];
  paths: () => string[];
}

type Over = Record<string, (() => Response) | undefined>;

export const NO_DATA = { code: 'no_data', message: '아직 없음 — diag.public 08:40 · diag.daily 16:00' };

/** 로그인 API — 경로(쿼리 포함, '/api/' 뺀 것) → 픽스처. history 는 key 마다(없으면 404) */
export function diagLoginHarness(over: Over = {}): DiagHarness {
  const { fetch, calls } = fakeFetch((url) => {
    const path = url.replace(/^\/api\//, '');
    const base = path.split('?')[0] ?? '';
    const o = over[path] ?? over[base];
    if (o) return o();
    const q = new URLSearchParams(path.split('?')[1] ?? '');
    if (base === 'diag/summary') return json(diagFixture('summary'));
    if (base === 'diag/events') return json(diagFixture('events'));
    if (base === 'diag/history') {
      const name = `history_${q.get('key') ?? ''}`;
      return `./${name}.json` in FX ? json(diagFixture(name)) : json(NO_DATA, 404);
    }
    return json(NO_DATA, 404);
  });
  return { source: createApiSource({ fetch }), calls, paths: () => calls.map((c) => c.url.replace(/^\/api\//, '')) };
}

/** 공개 파일 — ./data/diag/<이름>.json → public/diag/<이름>.json(없으면 404) */
export function diagPublicHarness(over: Over = {}): DiagHarness {
  const { fetch, calls } = fakeFetch((url) => {
    const name = url.replace(/^\.\/data\//, '').replace(/\.json$/, '');
    const o = over[name];
    if (o) return o();
    const key = `./public/${name}.json`;
    return key in FX ? json(structuredClone(FX[key])) : json({}, 404);
  });
  return { source: createStaticSource({ fetch }), calls, paths: () => calls.map((c) => c.url) };
}
