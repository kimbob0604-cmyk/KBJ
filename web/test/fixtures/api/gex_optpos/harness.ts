// 옵션 행사가별 추정 포지션 시험 도우미 — 합성 픽스처(tests/unit/api/optpos_fixtures.py 가 만든 이 폴더의 JSON)를
// 실제 데이터 소스에 가짜 fetch 로 물린다. 로그인: /api/gex/option-positions?investor=….
import { createApiSource } from '../../../../src/data/api';
import type { DataSource } from '../../../../src/data/source';
import { type FakeCall, fakeFetch, json } from '../../../helpers';

const FX = import.meta.glob<unknown>('./*.json', { eager: true, import: 'default' });

// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- 시험이 픽스처 모양을 적어 받는다
export function optposFixture<T = Record<string, unknown>>(name: string): T {
  const v = FX[`./${name}.json`];
  if (v === undefined) throw new Error(`옵션 포지션 픽스처 없음: ${name}`);
  return structuredClone(v) as T;
}

/** 시험 시계 — 합성 세계 CLOCK(2026-11-03 18:00 KST) */
export const OPTPOS_NOW = new Date('2026-11-03T09:00:00Z');

export interface OptposHarness {
  source: DataSource;
  calls: FakeCall[];
  paths: () => string[];
}

export function optposHarness(over: Record<string, (() => Response) | undefined> = {}): OptposHarness {
  const { fetch, calls } = fakeFetch((url) => {
    const path = url.replace(/^\/api\//, '');
    const base = path.split('?')[0] ?? '';
    const o = over[path] ?? over[base];
    if (o) return o();
    if (base === 'gex/option-positions') return json(optposFixture('option_positions'));
    const empty = optposFixture<{ status: number }>('option_positions_empty');
    return json(empty, empty.status);
  });
  return { source: createApiSource({ fetch }), calls, paths: () => calls.map((c) => c.url.replace(/^\/api\//, '')) };
}
