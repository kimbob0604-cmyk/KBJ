// 로그인 빌드 데이터 소스 — 같은 출처 API(docs/p3_design.md §5·§6.6). **로그인 빌드에만** 들어간다:
// main.ts 의 빌드 상수 분기 → boot/login.ts 만 이 파일을 import 한다(eslint no-restricted-imports 가 막고,
// scripts/check-bundle.mjs 가 공개 번들에서 API 경로 문자열을 찾으면 실패시킨다).
//
// - 쿠키 세션(`credentials: 'same-origin'`). GET 만 쓴다(P3 의 쓰기는 로그아웃뿐 — auth.ts).
// - 401 → onUnauthorized() 를 부르고 AuthRequiredError. 404 `no_data` 는 서버 안내 문구(serverMessage)를 싣고,
//   그 밖의 오류는 상태 코드만 담은 DataError(본문 미표시).
// - 응답은 봉투여야 한다(source·as_of·quality). 아니면 DataError — 원천 없는 숫자를 그리지 않는다.
import {
  asEnvelope,
  AuthRequiredError,
  DataError,
  type DataSource,
  type Envelope,
  type FetchLike,
  globalFetch,
  type Params,
  queryString,
} from './source';

export const API_BASE = '/api/';
/** 경로 형식: 'market/summary', 'flows/stock/005930' — 상위 경로(..)·절대 URL 금지 */
export const API_PATH = /^[a-z0-9_]+(?:\/[A-Za-z0-9_-]+)*$/;

export interface ApiSourceOptions {
  fetch?: FetchLike;
  base?: string;
  /** 401 을 받았을 때 — 로그인 화면으로 */
  onUnauthorized?: () => void;
}

/** 서버 문구 길이 상한 — 짧은 안내만 받는다(그 밖 본문은 그리지 않는다) */
const NO_DATA_MAX = 200;

/**
 * 404 본문이 `{code: 'no_data', message}` 이면 서버가 만든 안내 문구(예정 작업·준비 중 사유)를 돌려준다.
 * 그 밖의 404 본문(라우트 없음 등)은 null — 화면은 패널마다 정한 문구를 쓴다.
 */
async function noDataMessage(res: Response): Promise<string | null> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    return null;
  }
  if (typeof body !== 'object' || body === null) return null;
  const { code, message } = body as { code?: unknown; message?: unknown };
  if (code !== 'no_data' || typeof message !== 'string') return null;
  const text = message.trim();
  return text && text.length <= NO_DATA_MAX ? text : null;
}

export function createApiSource(opts: ApiSourceOptions = {}): DataSource {
  const doFetch = opts.fetch ?? globalFetch;
  const base = opts.base ?? API_BASE;
  return {
    tier: 'login',
    async get<T>(key: string, params?: Params): Promise<Envelope<T>> {
      if (!API_PATH.test(key)) throw new DataError('잘못된 API 경로');
      const res = await doFetch(`${base}${key}${queryString(params)}`, {
        method: 'GET',
        credentials: 'same-origin',
        cache: 'no-cache',
        headers: { Accept: 'application/json' },
      });
      if (res.status === 401) {
        opts.onUnauthorized?.();
        throw new AuthRequiredError();
      }
      if (res.status === 404) {
        const msg = await noDataMessage(res);
        throw new DataError(msg ?? '불러오지 못함 (HTTP 404)', 404, msg);
      }
      if (!res.ok) throw new DataError(`불러오지 못함 (HTTP ${res.status})`, res.status);
      let raw: unknown;
      try {
        raw = await res.json();
      } catch {
        throw new DataError('JSON 형식 오류', res.status);
      }
      const env = asEnvelope<T>(raw);
      if (!env) throw new DataError('원천·시각·품질 표시 없음', res.status);
      return env;
    },
  };
}
