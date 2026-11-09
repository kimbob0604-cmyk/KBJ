// 공개 사이트 데이터 소스 — Beom's Dashboard 방식(사용자 결정 2026-11-09, docs/adr/0042-site-snapshot.md).
//
// 맥이 로그인 화면용 API 응답을 정적 JSON 으로 만들어(kbj/services/api/site.py) GitHub Pages(kbj-site)에 올린다.
// 이 소스는 로그인 빌드와 **같은 키**(API 경로 + 인자)를 받아 `./data/<경로>[@<인자>].json` 을 읽는다.
// - 파일 이름 siteFile 은 site.py site_file 과 같은 규칙: 인자 키 정렬·빈 값 생략, `<키>-<값>` 을 '.' 로 잇고
//   [A-Za-z0-9_-] 밖 문자는 '_'.
// - API 기본값과 같은 인자는 뺀다(`_defaults.json` — 내보내기는 인자 없이 불렀다. 화면은 기본값을 적어 부르기도 한다).
// - 쓰기 채널이 없다(읽기 전용) — 쓰기 패널은 post 가 없으면 그리지 않는다.
// - 파일이 없으면(404): 날짜 인자(YYYY-MM-DD)를 빼고 한 번 더(화면이 오늘 날짜로 부르는 기본 범위). 그래도 없으면
//   '공개 사이트에 없는 보기'. 다른 인자는 빼지 않는다 — 다른 보기의 숫자를 그 보기인 척 그리지 않는다.
// - 파일이 404 `no_data` 본문이면 로그인판과 같은 서버 안내 문구(serverMessage).
// - 'stock/search' 는 `stock/search-index.json`(상세가 있는 종목)을 화면에서 거른다.
import {
  asEnvelope,
  DataError,
  type DataSource,
  type Envelope,
  type FetchLike,
  globalFetch,
  type Params,
} from './source';

export const SEARCH_KEY = 'stock/search';
export const SEARCH_INDEX = 'stock/search-index';
export const MISSING = '공개 사이트에 없는 보기';
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const UNSAFE = /[^A-Za-z0-9_-]/g;
/** 키 형식(로그인 API 경로와 같다) — 상위 경로·절대 주소 금지 */
const KEY = /^[a-z0-9_]+(?:-[a-z0-9_]+)*(?:\/[A-Za-z0-9_-]+)*$/;

/** API 경로 + 인자 → 사이트 파일 이름(site.py site_file 과 같은 규칙). */
export function siteFile(key: string, params?: Params): string {
  const parts: string[] = [];
  for (const k of Object.keys(params ?? {}).sort()) {
    const v = params?.[k];
    if (v === undefined || v === null || v === '') continue;
    parts.push(`${k.replace(UNSAFE, '_')}-${String(v).replace(UNSAFE, '_')}`);
  }
  return parts.length ? `${key}@${parts.join('.')}.json` : `${key}.json`;
}

/** 경로 틀('stock/{code}/valuation') → 기본값이 있는 쿼리 인자(내보내기의 `_defaults.json`) */
export type QueryDefaults = Readonly<Record<string, Readonly<Record<string, unknown>>>>;

function templateRe(t: string): RegExp {
  const body = t
    .split('/')
    .map((seg) => (/^\{[A-Za-z_]+\}$/.test(seg) ? '[^/]+' : seg.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
    .join('/');
  return new RegExp(`^${body}$`);
}

/** 기본값과 같은 인자를 뺀다 — 내보내기는 인자 없이(기본값으로) 불렀다 */
export function dropDefaults(key: string, params: Params | undefined, defaults: QueryDefaults): Params | undefined {
  if (!params) return params;
  let d: Readonly<Record<string, unknown>> | undefined = defaults[key];
  if (!d) {
    for (const [t, v] of Object.entries(defaults)) {
      if (t.includes('{') && templateRe(t).test(key)) {
        d = v;
        break;
      }
    }
  }
  if (!d) return params;
  const out: Record<string, Params[string]> = {};
  for (const [k, v] of Object.entries(params)) {
    if (k in d && v !== undefined && v !== null && String(v) === String(d[k])) continue;
    out[k] = v;
  }
  return out;
}

/** 날짜 인자를 뺀 인자 — 하나도 안 뺐으면 null */
function withoutDates(params: Params | undefined): Params | null {
  if (!params) return null;
  const out: Record<string, Params[string]> = {};
  let dropped = false;
  for (const [k, v] of Object.entries(params)) {
    if (typeof v === 'string' && DATE.test(v)) dropped = true;
    else out[k] = v;
  }
  return dropped ? out : null;
}

interface SearchItem {
  code: string;
  name: string | null;
  [k: string]: unknown;
}

export interface SnapshotSourceOptions {
  fetch?: FetchLike;
  /** 데이터 디렉터리(끝에 /). 기본 './data/' — Pages 하위 경로에서도 맞게 상대 경로 */
  base?: string;
}

export function createSnapshotSource(opts: SnapshotSourceOptions = {}): DataSource {
  const doFetch = opts.fetch ?? globalFetch;
  const base = opts.base ?? './data/';
  let index: Promise<Envelope<{ items: SearchItem[] }>> | null = null;
  let defaults: Promise<QueryDefaults> | null = null;

  /** 파일 하나 — 없으면 null(404), 그 밖 오류는 던진다 */
  async function read(file: string): Promise<unknown> {
    const res = await doFetch(`${base}${file}`, { cache: 'no-cache', credentials: 'omit' });
    if (res.status === 404) return null;
    if (!res.ok) throw new DataError(`불러오지 못함 (HTTP ${res.status})`, res.status);
    try {
      return (await res.json()) as unknown;
    } catch {
      throw new DataError('JSON 형식 오류', res.status);
    }
  }

  /** 기본값 표(없으면 빈 표 — 그때는 인자를 그대로 쓴다) */
  function loadDefaults(): Promise<QueryDefaults> {
    defaults ??= read('_defaults.json').then((raw) =>
      typeof raw === 'object' && raw !== null && !Array.isArray(raw) ? (raw as QueryDefaults) : {},
    );
    defaults.catch(() => {
      defaults = null; // 실패하면 다음에 다시
    });
    return defaults;
  }

  async function load<T>(key: string, given?: Params): Promise<Envelope<T>> {
    if (!KEY.test(key)) throw new DataError('잘못된 경로');
    const params = given ? dropDefaults(key, given, await loadDefaults()) : given;
    let raw = await read(siteFile(key, params));
    if (raw === null) {
      const rest = withoutDates(params);
      if (rest) raw = await read(siteFile(key, rest));
    }
    if (raw === null) throw new DataError(MISSING, 404, MISSING);
    const env = asEnvelope<T>(raw);
    if (env) return env;
    const r = raw as Record<string, unknown>;
    if (r.code === 'no_data' && typeof r.message === 'string') {
      const msg = r.message.trim().slice(0, 200) || '아직 없음';
      throw new DataError(msg, 404, msg);
    }
    throw new DataError('원천·시각·품질 표시 없음');
  }

  async function search<T>(params?: Params): Promise<Envelope<T>> {
    index ??= load<{ items: SearchItem[] }>(SEARCH_INDEX);
    index.catch(() => {
      index = null; // 실패하면 다음에 다시
    });
    const env = await index;
    const q = String(params?.q ?? '').trim().toLowerCase();
    const limit = Number(params?.limit ?? 20) || 20;
    const hits = q
      ? env.data.items.filter((it) => it.code.toLowerCase().startsWith(q) || (it.name ?? '').toLowerCase().includes(q))
      : [];
    const data = { q, n_total: hits.length, items: hits.slice(0, limit) };
    return { ...env, data: data as T };
  }

  return {
    tier: 'login',
    get<T>(key: string, params?: Params): Promise<Envelope<T>> {
      return key === SEARCH_KEY ? search<T>(params) : load<T>(key, params);
    },
    href(): string | null {
      return null; // 내려받기(엑셀)는 공개 사이트에 없다
    },
  };
}
