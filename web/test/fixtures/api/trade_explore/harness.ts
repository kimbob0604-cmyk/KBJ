// EXP 시험 도우미 — 합성 픽스처(tests/unit/api/web_fixtures_p6/explore.py 가 만든 이 폴더의 JSON)를 실제 데이터 소스에
// 가짜 fetch 로 물린다. 로그인: /api/trade/items…·countries…·regions·surges·items104, 공개: ./data/trade/… .
import { createApiSource } from '../../../../src/data/api';
import type { DataSource } from '../../../../src/data/source';
import { createStaticSource } from '../../../../src/data/static';
import { type FakeCall, fakeFetch, json } from '../../../helpers';

const FX = import.meta.glob<unknown>('./**/*.json', { eager: true, import: 'default' });

/** 이 폴더 기준 상대 이름(확장자 없이) — 깊은 복사 */
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- 시험이 픽스처 모양을 적어 받는다
export function exploreFixture<T = Record<string, unknown>>(name: string): T {
  const v = FX[`./${name}.json`];
  if (v === undefined) throw new Error(`EXP 픽스처 없음: ${name}`);
  return structuredClone(v) as T;
}

/** 시험 시계 — 합성 세계 p6_world CLOCK(2026-12-11 11:30 KST) */
export const EXPLORE_NOW = new Date('2026-12-11T02:30:00Z');

export interface ExploreHarness {
  source: DataSource;
  calls: FakeCall[];
  paths: () => string[];
}

type Over = Record<string, (() => Response) | undefined>;

const NO_DATA = (): Response => json({ code: 'no_data', message: '아직 없음 — trade.customs_monthly' }, 404);

/** API 경로(쿼리 포함, '/api/' 뺀 것) → 픽스처 이름. 모르는 경로는 null(404) */
export function loginFixtureName(path: string): string | null {
  const [p = '', q = ''] = path.split('?');
  const qs = new URLSearchParams(q);
  if (p === 'trade/items') {
    const level = qs.get('level') ?? '';
    const parent = qs.get('parent');
    return level === '2' ? `items_l2_${qs.get('flow') ?? 'exp'}` : parent ? `items_l${level}_${parent}` : null;
  }
  const m = /^trade\/items\/([0-9]+)\/(series|countries)$/.exec(p);
  if (m) return m[2] === 'series' ? `series_${m[1] ?? ''}` : `item_countries_${m[1] ?? ''}`;
  const c = /^trade\/countries\/([A-Z]{2})\/items$/.exec(p);
  if (c) return `country_items_${c[1] ?? ''}`;
  return { 'trade/countries': 'countries', 'trade/regions': 'regions', 'trade/surges': 'surges', 'trade/items104': 'items104' }[p] ?? null;
}

export function exploreLoginHarness(over: Over = {}): ExploreHarness {
  const { fetch, calls } = fakeFetch((url) => {
    const path = url.replace(/^\/api\//, '');
    const o = over[path] ?? over[path.split('?')[0] ?? ''];
    if (o) return o();
    const name = loginFixtureName(path);
    return name !== null && `./${name}.json` in FX ? json(exploreFixture(name)) : NO_DATA();
  });
  return { source: createApiSource({ fetch }), calls, paths: () => calls.map((c) => c.url.replace(/^\/api\//, '')) };
}

/** 공개 파일 — ./data/<이름>.json → public/<이름>.json(없으면 404) */
export function explorePublicHarness(over: Over = {}): ExploreHarness {
  const { fetch, calls } = fakeFetch((url) => {
    const name = url.replace(/^\.\/data\//, '').replace(/\.json$/, '');
    const o = over[name];
    if (o) return o();
    const key = `./public/${name}.json`;
    return key in FX ? json(structuredClone(FX[key])) : json({}, 404);
  });
  return { source: createStaticSource({ fetch }), calls, paths: () => calls.map((c) => c.url) };
}
