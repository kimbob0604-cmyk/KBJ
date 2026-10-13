// FLASH 시험 도우미 — 합성 픽스처(tests/unit/api/web_fixtures_p6/flash.py 가 만든 이 폴더의 JSON)를 실제 데이터 소스에
// 가짜 fetch 로 물린다. 로그인: /api/trade/flash?kind=…·/api/market/ribbon, 공개: ./data/trade/flash.json·./data/trade/ribbon.json.
import { createApiSource } from '../../../../src/data/api';
import type { DataSource } from '../../../../src/data/source';
import { createStaticSource } from '../../../../src/data/static';
import { type FakeCall, fakeFetch, json } from '../../../helpers';

const FL = import.meta.glob<unknown>('./**/*.json', { eager: true, import: 'default' });

/** 이 폴더 기준 상대 이름(확장자 없이) — 깊은 복사 */
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- 시험이 픽스처 모양을 적어 받는다
export function flashFixture<T = Record<string, unknown>>(name: string): T {
  const v = FL[`./${name}.json`];
  if (v === undefined) throw new Error(`FLASH 픽스처 없음: ${name}`);
  return structuredClone(v) as T;
}

/** 시험 시계 — 합성 세계 p6_world CLOCK(2026-12-11 11:30 KST) */
export const FLASH_NOW = new Date('2026-12-11T02:30:00Z');

export interface FlashHarness {
  source: DataSource;
  calls: FakeCall[];
  paths: () => string[];
}

type Over = Record<string, (() => Response) | undefined>;

/** 로그인 API — 경로(쿼리 포함, '/api/' 뺀 것) → 픽스처(기간과 무관하게 종류마다 한 파일). over 로 응답을 바꾼다 */
export function flashLoginHarness(over: Over = {}): FlashHarness {
  const { fetch, calls } = fakeFetch((url) => {
    const path = url.replace(/^\/api\//, '');
    const o = over[path] ?? over[path.split('?')[0] ?? ''];
    if (o) return o();
    if (path === 'market/ribbon') return json(flashFixture('market_ribbon'));
    const m = /^trade\/flash\?(?:.*&)?kind=([a-z_]+)/.exec(path);
    if (m?.[1] && `./flash_${m[1]}.json` in FL) return json(flashFixture(`flash_${m[1]}`));
    return json({ code: 'no_data', message: '아직 없음 — trade.customs_tenday' }, 404);
  });
  return { source: createApiSource({ fetch }), calls, paths: () => calls.map((c) => c.url.replace(/^\/api\//, '')) };
}

/** 공개 파일 — ./data/<이름>.json → public/<이름>.json(없으면 404) */
export function flashPublicHarness(over: Over = {}): FlashHarness {
  const { fetch, calls } = fakeFetch((url) => {
    const name = url.replace(/^\.\/data\//, '').replace(/\.json$/, '');
    const o = over[name];
    if (o) return o();
    const key = `./public/${name}.json`;
    return key in FL ? json(structuredClone(FL[key])) : json({}, 404);
  });
  return { source: createStaticSource({ fetch }), calls, paths: () => calls.map((c) => c.url) };
}
