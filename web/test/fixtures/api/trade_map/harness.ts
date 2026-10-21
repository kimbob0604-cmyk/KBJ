// MAP 시험 도우미 — 합성 픽스처(tests/unit/api/web_fixtures_p6/map.py 가 만든 이 폴더의 JSON — p6_world, 실데이터 아님)를
// 실제 데이터 소스에 가짜 fetch 로 물린다. 로그인: /api/trade/stocks·/api/trade/stock/<코드>(/flows·/preview), 공개: ./data/trade/… .
import { createApiSource } from '../../../../src/data/api';
import type { DataSource } from '../../../../src/data/source';
import { createStaticSource } from '../../../../src/data/static';
import { type FakeCall, fakeFetch, json } from '../../../helpers';

const FX = import.meta.glob<unknown>('./**/*.json', { eager: true, import: 'default' });

/** 이 폴더 기준 상대 이름(확장자 없이) — 깊은 복사 */
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- 시험이 픽스처 모양을 적어 받는다
export function mapFixture<T = Record<string, unknown>>(name: string): T {
  const v = FX[`./${name}.json`];
  if (v === undefined) throw new Error(`MAP 픽스처 없음: ${name}`);
  return structuredClone(v) as T;
}

/** 시험 시계 — 합성 세계 p6_world CLOCK(2026-12-11 11:30 KST) */
export const MAP_NOW = new Date('2026-12-11T02:30:00Z');

export interface MapHarness {
  source: DataSource;
  calls: FakeCall[];
  paths: () => string[];
}

export type Over = Record<string, (() => Response) | undefined>;

/** API 경로('/api/' 뺀 것) → 픽스처 이름. 매핑 없는 종목은 no_map 픽스처(404) */
export function loginFixtureName(path: string): string | null {
  const p = path.split('?')[0] ?? '';
  if (p === 'trade/stocks') return 'stocks';
  const f = /^trade\/stock\/([0-9A-Z]{6})\/(flows|preview)$/.exec(p);
  if (f) return `${f[2] ?? ''}_${f[1] ?? ''}`;
  const m = /^trade\/stock\/([0-9A-Z]{6})$/.exec(p);
  return m ? `stock_${m[1] ?? ''}` : null;
}

export function mapLoginHarness(over: Over = {}): MapHarness {
  const { fetch, calls } = fakeFetch((url) => {
    const path = url.replace(/^\/api\//, '');
    const o = over[path] ?? over[path.split('?')[0] ?? ''];
    if (o) return o();
    const name = loginFixtureName(path);
    if (name !== null && `./${name}.json` in FX) return json(mapFixture(name));
    if (name !== null && /^(stock|flows|preview)_/.test(name)) return json(mapFixture('no_map_994010'), 404);
    return json({ code: 'no_data', message: '아직 없음 — trade.stock_index 21:00' }, 404);
  });
  return { source: createApiSource({ fetch }), calls, paths: () => calls.map((c) => c.url.replace(/^\/api\//, '')) };
}

/** 공개 파일 — ./data/<이름>.json → public/<이름>.json(없으면 404) */
export function mapPublicHarness(over: Over = {}): MapHarness {
  const { fetch, calls } = fakeFetch((url) => {
    const name = url.replace(/^\.\/data\//, '').replace(/\.json$/, '');
    const o = over[name];
    if (o) return o();
    const key = `./public/${name}.json`;
    return key in FX ? json(structuredClone(FX[key])) : json({}, 404);
  });
  return { source: createStaticSource({ fetch }), calls, paths: () => calls.map((c) => c.url) };
}
