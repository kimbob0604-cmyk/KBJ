// MAC 페이지 시험 도우미 — 합성 픽스처(tests/unit/api/web_fixtures_p5/macro.py 가 만든 이 폴더의 JSON)를 실제 데이터 소스에
// 가짜 fetch 로 물린다. 로그인: /api/macro/*·/api/market/ribbon, 공개: ./data/macro/*.json·./data/credit.json.
import { createApiSource } from '../../../../src/data/api';
import { createStaticSource } from '../../../../src/data/static';
import { type FakeCall, fakeFetch, json } from '../../../helpers';

const MAC = import.meta.glob<unknown>('./**/*.json', { eager: true, import: 'default' });

/** 이 폴더 기준 상대 이름(확장자 없이) — 깊은 복사 */
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- 시험이 픽스처 모양을 적어 받는다
export function macroFixture<T = Record<string, unknown>>(name: string): T {
  const v = MAC[`./${name}.json`];
  if (v === undefined) throw new Error(`MAC 픽스처 없음: ${name}`);
  return structuredClone(v) as T;
}

/** 시험 시계 — 합성 세계 CLOCK(2026-11-17 16:30 KST) */
export const MAC_NOW = new Date('2026-11-17T07:30:00Z');

export interface MacHarness {
  source: ReturnType<typeof createApiSource>;
  calls: FakeCall[];
  paths: () => string[];
}

type Over = Record<string, (() => Response) | undefined>;

/** 로그인 API — 경로(쿼리 포함, '/api/' 뺀 것) → 픽스처. over 로 응답을 바꾼다 */
export function macLoginHarness(over: Over = {}): MacHarness {
  const { fetch, calls } = fakeFetch((url) => {
    const path = url.replace(/^\/api\//, '');
    const o = over[path] ?? over[path.split('?')[0] ?? ''];
    if (o) return o();
    if (path === 'macro/tabs') return json(macroFixture('tabs'));
    if (path === 'macro/calendar') return json(macroFixture('calendar'));
    if (path === 'market/ribbon') return json(macroFixture('ribbon'));
    const m = /^macro\/series\?tab=([a-z]+)$/.exec(path);
    if (m?.[1]) {
      const tab = m[1];
      if (tab === 'labor') return json(macroFixture('series_labor_nodata'), 404);
      if (`./series_${tab}.json` in MAC) return json(macroFixture(`series_${tab}`));
      return json({ code: 'no_data', message: '아직 없음 — macro.morning 06:20' }, 404);
    }
    return json({ code: 'no_data', message: '아직 없음' }, 404);
  });
  return { source: createApiSource({ fetch }), calls, paths: () => calls.map((c) => c.url.replace(/^\/api\//, '')) };
}

/** 공개 파일 — ./data/<이름>.json → public/<이름>.json(없으면 404) */
export function macPublicHarness(over: Over = {}) {
  const { fetch, calls } = fakeFetch((url) => {
    const name = url.replace(/^\.\/data\//, '').replace(/\.json$/, '');
    const o = over[name];
    if (o) return o();
    const key = `./public/${name}.json`;
    return key in MAC ? json(structuredClone(MAC[key])) : json({}, 404);
  });
  return { source: createStaticSource({ fetch }), calls, paths: () => calls.map((c) => c.url) };
}
