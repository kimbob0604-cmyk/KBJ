// 공개 빌드 데이터 소스 — `./data/<이름>.json` 만 읽는다(docs/p3_design.md §6.6·§7.2, docs/p4_design.md §1.2·§7).
//
// - 이름 허용 형식: 소문자·숫자·밑줄. 슬래시가 든 키(로그인 API 경로)는 **요청하지 않고** LockedError —
//   공개 사이트는 로그인 API 주소조차 모른다(DATA_TIERS §3). 이 파일에는 API 경로 문자열이 없다.
// - 예외는 하위 폴더 세 개: P4 `fin/<코드>`(상장사 DART 재무)·`reports/<코드>`(dart-report 데모 종목, 코드는
//   `^[0-9A-Z]{6}$`), P5 `macro/<이름>`(공개 매크로 탭·캘린더, 이름은 소문자·숫자·밑줄 —
//   docs/p5_design.md §7), P6 `trade/<이름>`·`trade/(hs|country|region|stock)/<코드>`(관세청 수출입·매핑 종목 —
//   docs/p6_design.md §7). 이 파일들은 manifest 항목이 아니라 **파일 자체가 봉투**(source·as_of·quality)여야 한다
//   (공개 내보내기와의 약속) — 봉투가 아니면 manifest 로 보충하지 않고 DataError. P5 신용 칩 `credit` 은 평평한 이름.
// - 파일이 봉투(source·as_of·quality·data)면 그대로, 아니면 manifest.json 의 같은 이름 항목으로 원천·시각·품질을
//   붙인다. 둘 다 없으면 DataError — 원천 없는 숫자를 그리지 않는다(절대 규칙 1).
// - 내려받기(href): `reports/<코드>.xlsx` 만 주소를 만든다(요청하지 않음). 그 밖은 null.
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

/**
 * 하위 폴더 키 — 파일 자체가 봉투인 공개 파일(P4 종목마다 하나, P5 매크로 탭마다 하나, P6 `trade/<이름>`·
 * `trade/(hs|country|region|stock)/<코드 2~10자>` — docs/p6_design.md §7, manifest.py·merge-public-data.mjs 와 같은 규칙)
 */
export const PUBLIC_SUBDIR =
  /^(?:(?:fin|reports)\/[0-9A-Z]{6}|(?:macro)\/[a-z0-9_]+|(?:trade)\/(?:(?:hs|country|region|stock)\/[0-9A-Z]{2,10}|[a-z0-9_]+))$/;

/** 공개 내려받기 파일 — dart-report 엑셀(데모 종목만 있다) */
export const PUBLIC_DOWNLOAD = /^reports\/[0-9A-Z]{6}\.xlsx$/;

/** 공개판이 요청할 수 있는 키인가(그 밖은 요청 없이 LockedError). */
export function isPublicKey(key: string): boolean {
  return PUBLIC_NAME.test(key) || PUBLIC_SUBDIR.test(key);
}

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
      if (!isPublicKey(key)) throw new LockedError(key);
      const raw = await fetchJson(key);
      const env = asEnvelope<T>(raw);
      if (env) return env;
      // 하위 폴더 파일은 그 자체가 봉투여야 한다 — manifest 에 종목마다 항목을 두지 않는다
      if (PUBLIC_SUBDIR.test(key)) throw new DataError('원천·시각·품질 표시 없음');
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
    href(key: string): string | null {
      return PUBLIC_DOWNLOAD.test(key) ? `${base}${key}` : null;
    },
  };
}
