// 공개 빌드 데이터 소스 — `./data/<이름>.json` 만 읽는다(docs/p3_design.md §6.6·§7.2).
//
// - 이름 허용 형식: 소문자·숫자·밑줄. 슬래시가 든 키(로그인 API 경로)는 **요청하지 않고** LockedError —
//   공개 사이트는 로그인 API 주소조차 모른다(DATA_TIERS §3). 이 파일에는 API 경로 문자열이 없다.
// - 파일이 봉투(source·as_of·quality·data)면 그대로, 아니면 manifest.json 의 같은 이름 항목으로 원천·시각·품질을
//   붙인다. 둘 다 없으면 DataError — 원천 없는 숫자를 그리지 않는다(절대 규칙 1).
import {
  asEnvelope,
  DataError,
  type DataSource,
  type Envelope,
  type FetchLike,
  globalFetch,
  LockedError,
  type Quality,
  QUALITIES,
} from './source';

export const PUBLIC_NAME = /^[a-z0-9_]+$/;

export interface ManifestEntry {
  name: string;
  source: string;
  as_of: string;
  quality: Quality;
}

export interface Manifest {
  generated_at: string | null;
  files: ManifestEntry[];
}

function entryFrom(name: string, v: unknown): ManifestEntry | null {
  if (typeof v !== 'object' || v === null) return null;
  const r = v as Record<string, unknown>;
  const n = typeof r.name === 'string' ? r.name : name;
  const q = r.quality;
  if (typeof r.source !== 'string' || typeof r.as_of !== 'string') return null;
  if (typeof q !== 'string' || !(QUALITIES as readonly string[]).includes(q)) return null;
  return { name: n.replace(/\.json$/, ''), source: r.source, as_of: r.as_of, quality: q as Quality };
}

/**
 * manifest.json 해석. 받는 모양 두 가지(내보내기 X 묶음과 맞춘다):
 *   { generated_at, files: [ {name, source, as_of, quality}, … ] }
 *   { generated_at, files: { "<name>": {source, as_of, quality}, … } }
 * 봉투로 싸여 있으면(data 안에) 벗긴다.
 */
export function parseManifest(raw: unknown): Manifest {
  const env = asEnvelope<unknown>(raw);
  const body = (env ? env.data : raw) as Record<string, unknown> | null;
  if (typeof body !== 'object' || body === null) throw new DataError('manifest 형식 오류');
  const files: ManifestEntry[] = [];
  const f = body.files;
  if (Array.isArray(f)) {
    for (const item of f) {
      const e = entryFrom('', item);
      if (e?.name) files.push(e);
    }
  } else if (typeof f === 'object' && f !== null) {
    for (const [name, item] of Object.entries(f)) {
      const e = entryFrom(name, item);
      if (e) files.push(e);
    }
  } else {
    throw new DataError('manifest 에 files 가 없음');
  }
  const generated = typeof body.generated_at === 'string' ? body.generated_at : (env?.generated_at ?? null);
  return { generated_at: generated, files };
}

export interface StaticSourceOptions {
  fetch?: FetchLike;
  /** 데이터 디렉터리(끝에 /). 기본 './data/' — Pages 하위 경로에서도 맞게 상대 경로 */
  base?: string;
}

export function createStaticSource(opts: StaticSourceOptions = {}): DataSource {
  const doFetch = opts.fetch ?? globalFetch;
  const base = opts.base ?? './data/';
  let manifest: Promise<Manifest> | null = null;

  async function fetchJson(name: string): Promise<unknown> {
    const res = await doFetch(`${base}${name}.json`, { cache: 'no-cache', credentials: 'omit' });
    if (!res.ok) throw new DataError(res.status === 404 ? '아직 없음' : `불러오지 못함 (HTTP ${res.status})`, res.status);
    try {
      return (await res.json()) as unknown;
    } catch {
      throw new DataError('JSON 형식 오류', res.status);
    }
  }

  function loadManifest(): Promise<Manifest> {
    if (!manifest) {
      manifest = fetchJson('manifest').then(parseManifest);
      // 실패하면 다음에 다시 시도(오류는 호출자에게 그대로 간다)
      manifest.catch(() => {
        manifest = null;
      });
    }
    return manifest;
  }

  return {
    tier: 'public',
    async get<T>(key: string): Promise<Envelope<T>> {
      if (!PUBLIC_NAME.test(key)) throw new LockedError(key);
      const raw = await fetchJson(key);
      const env = asEnvelope<T>(raw);
      if (env) return env;
      if (key === 'manifest') {
        const m = parseManifest(raw);
        throw new DataError(`manifest 는 봉투가 아님(파일 ${m.files.length}개)`);
      }
      const m = await loadManifest();
      const entry = m.files.find((e) => e.name === key);
      if (!entry || !asEnvelope({ ...entry, data: null })) throw new DataError('원천·시각·품질 표시 없음');
      return {
        source: entry.source,
        as_of: entry.as_of,
        quality: entry.quality,
        notes: [],
        generated_at: m.generated_at ?? entry.as_of,
        data: raw as T,
      };
    },
  };
}
