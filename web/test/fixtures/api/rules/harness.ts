// 규칙 시험 도우미(묶음 EVL) — 합성 픽스처(tests/unit/api/web_fixtures_p8/rules.py 가 만든 이 폴더의 JSON)를 실제 데이터
// 소스에 가짜 fetch 로 물린다. 로그인: /api/rules·/api/rules/<id>·/api/rules/examples·/api/rules/signals.
// 쓰기(POST·PUT)는 데이터 소스의 선택 기능 `post`·`put` — withRuleWriter 로 붙인다(쓰기 채널은 아직 공용 소스에 없다).
import { createApiSource } from '../../../../src/data/api';
import type { DataSource, Envelope } from '../../../../src/data/source';
import { type FakeCall, fakeFetch, json } from '../../../helpers';

const FX = import.meta.glob<unknown>('./*.json', { eager: true, import: 'default' });

/** 이 폴더 기준 이름(확장자 없이) — 깊은 복사 */
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- 시험이 픽스처 모양을 적어 받는다
export function rulesFixture<T = Record<string, unknown>>(name: string): T {
  const v = FX[`./${name}.json`];
  if (v === undefined) throw new Error(`규칙 픽스처 없음: ${name}`);
  return structuredClone(v) as T;
}

/** 시험 시계 — 합성 세계 p8_world clock(2026-10-30 16:00 KST) */
export const RULES_NOW = new Date('2026-10-30T07:00:00Z');
export const NO_DATA = { code: 'no_data', message: '아직 없음 — rules.intraday 장중 10분 · rules.close 16:00' };

export interface RulesHarness {
  source: DataSource;
  calls: FakeCall[];
  paths: () => string[];
}

type Over = Record<string, (() => Response) | undefined>;

function detailId(): string {
  return rulesFixture<{ data: { rule: { rule_id: string } } }>('detail').data.rule.rule_id;
}

/** 로그인 API — 경로(쿼리 뺀 것) → 픽스처. over 로 바꿔 끼운다(없는 경로는 404) */
export function rulesHarness(over: Over = {}): RulesHarness {
  const routes: Record<string, string> = {
    rules: 'list',
    'rules/examples': 'examples',
    'rules/signals': 'signals',
    [`rules/${detailId()}`]: 'detail',
  };
  const { fetch, calls } = fakeFetch((url) => {
    const path = url.replace(/^\/api\//, '');
    const base = path.split('?')[0] ?? '';
    const o = over[path] ?? over[base];
    if (o) return o();
    const name = routes[base];
    return name ? json(rulesFixture(name)) : json(NO_DATA, 404);
  });
  return { source: createApiSource({ fetch }), calls, paths: () => calls.map((c) => c.url.replace(/^\/api\//, '')) };
}

export interface WriteCall {
  method: 'POST' | 'PUT';
  key: string;
  body: unknown;
}

function written(ruleId: string, version: number): Envelope<unknown> {
  return {
    source: 'KBJ:rules',
    as_of: '2026-10-30T16:00:00+09:00',
    quality: 'ok',
    notes: [],
    generated_at: '2026-10-30T16:00:00+09:00',
    data: { rule_id: ruleId, version, dsl_hash: '0'.repeat(64), enabled: false, archived: false },
  };
}

/** 기본 응답 — validate 는 이름이 '검사 실패 예' 면 validate_bad, 아니면 validate_ok. 만들기·새 판·상태는 쓰기 결과 */
export function defaultRespond(method: 'POST' | 'PUT', key: string, body: unknown): Promise<Envelope<unknown>> {
  if (key === 'rules/validate') {
    const name = (body as { dsl?: { name?: unknown } } | null)?.dsl?.name;
    return Promise.resolve(rulesFixture<Envelope<unknown>>(name === '검사 실패 예' ? 'validate_bad' : 'validate_ok'));
  }
  if (key === 'rules') return Promise.resolve(written('r_newrule00001', 1));
  const id = key.split('/')[1] ?? 'r_unknown0000';
  return Promise.resolve(written(id, method === 'PUT' ? 2 : 1));
}

export function withRuleWriter(
  source: DataSource,
  respond: (method: 'POST' | 'PUT', key: string, body: unknown) => Promise<Envelope<unknown>> = defaultRespond,
): { source: DataSource; writes: WriteCall[] } {
  const writes: WriteCall[] = [];
  const send =
    (method: 'POST' | 'PUT') =>
    async <T>(key: string, body: unknown): Promise<Envelope<T>> => {
      writes.push({ method, key, body });
      return (await respond(method, key, body)) as Envelope<T>;
    };
  return {
    source: Object.assign(Object.create(source) as DataSource, source, { post: send('POST'), put: send('PUT') }),
    writes,
  };
}
