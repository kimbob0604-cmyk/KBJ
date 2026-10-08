// 데이터 소스 계층 — 위젯은 이 인터페이스만 안다(docs/p3_design.md §6.2·§6.6).
//
// - 공개 빌드: data/static.ts — `./data/<이름>.json`(public.export 산출물)만 읽는다. 로그인 키를 물으면 요청 없이 LockedError.
// - 로그인 빌드: data/api.ts — 같은 출처 API. 401 이면 AuthRequiredError + 로그인 화면.
// 어느 쪽이든 돌려주는 값은 봉투(Envelope)다: 모든 수치에 source·as_of·quality(절대 규칙 1). 봉투가 아니면 오류다.

export type Tier = 'public' | 'login';
export type Quality = 'ok' | 'stale' | 'estimated' | 'invalid';
export const QUALITIES: readonly Quality[] = ['ok', 'stale', 'estimated', 'invalid'];

/** API·공개 JSON 의 공통 봉투(§5.2). */
export interface Envelope<T> {
  source: string;
  /** 데이터가 가리키는 시각(ISO 8601, 시간대 포함) */
  as_of: string;
  quality: Quality;
  notes: string[];
  /** 응답을 만든 시각 */
  generated_at: string;
  data: T;
}

export type ParamValue = string | number | boolean | null | undefined;
export type Params = Readonly<Record<string, ParamValue>>;

export interface DataSource {
  readonly tier: Tier;
  /**
   * 공개: key = 공개 파일 이름(예 'calendar' → ./data/calendar.json).
   * 로그인: key = API 경로(예 'market/summary' → 같은 출처 API).
   */
  get<T>(key: string, params?: Params): Promise<Envelope<T>>;
}

/** 로그인이 필요하다(로그인 빌드의 401). */
export class AuthRequiredError extends Error {
  override readonly name = 'AuthRequiredError';
  constructor() {
    super('로그인이 필요합니다');
  }
}

/** 이 등급에서 볼 수 없는 데이터(공개 빌드에서 로그인 키를 물었다). 네트워크 요청은 하지 않았다. */
export class LockedError extends Error {
  override readonly name = 'LockedError';
  constructor(readonly key: string) {
    super('로그인 등급 데이터');
  }
}

/** 응답 오류. 응답 본문은 싣지 않는다(§6.7 — 오류 문구에 본문 금지). */
export class DataError extends Error {
  override readonly name = 'DataError';
  constructor(
    message: string,
    readonly status: number | null = null,
    /** 404 `no_data` 본문의 서버 문구(예정 작업 안내 — 데이터·입력값 없음). 그 밖 오류는 null */
    readonly serverMessage: string | null = null,
  ) {
    super(message);
  }
}

function isQuality(v: unknown): v is Quality {
  return typeof v === 'string' && (QUALITIES as readonly string[]).includes(v);
}

function isAwareIso(v: unknown): v is string {
  return typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T[\d:.]+(Z|[+-]\d{2}:?\d{2})$/.test(v);
}

/** 봉투 모양 검사 — source·as_of(시간대 포함)·quality 가 있어야 한다. notes·generated_at 은 없으면 채운다. */
export function asEnvelope<T>(raw: unknown): Envelope<T> | null {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.source !== 'string' || r.source.length === 0) return null;
  if (!isAwareIso(r.as_of) || !isQuality(r.quality) || !('data' in r)) return null;
  const notes = Array.isArray(r.notes) ? r.notes.filter((n): n is string => typeof n === 'string') : [];
  const generated = isAwareIso(r.generated_at) ? r.generated_at : r.as_of;
  return { source: r.source, as_of: r.as_of, quality: r.quality, notes, generated_at: generated, data: r.data as T };
}

/** 품질 순서: ok < stale < estimated < invalid (봉투 quality = 가장 나쁜 것 — §5.2). */
export function worstQuality(qs: readonly Quality[]): Quality {
  const rank: Record<Quality, number> = { ok: 0, stale: 1, estimated: 2, invalid: 3 };
  let worst: Quality = 'ok';
  for (const q of qs) if (rank[q] > rank[worst]) worst = q;
  return worst;
}

/** 쿼리 문자열 — 키 정렬(서버 캐시 키와 같은 순서), 빈 값 생략. */
export function queryString(params: Params | undefined): string {
  if (!params) return '';
  const parts: string[] = [];
  for (const k of Object.keys(params).sort()) {
    const v = params[k];
    if (v === undefined || v === null || v === '') continue;
    parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  }
  return parts.length ? `?${parts.join('&')}` : '';
}

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

/** 전역 fetch 를 지연 바인딩(시험에서 vi.stubGlobal 로 바꿀 수 있게). */
export const globalFetch: FetchLike = (input, init) => fetch(input, init);
