// 로그인 빌드 데이터 소스 — 같은 출처 API(docs/p3_design.md §5·§6.6). **로그인 빌드에만** 들어간다:
// main.ts 의 빌드 상수 분기 → boot/login.ts 만 이 파일을 import 한다(eslint no-restricted-imports 가 막고,
// scripts/check-bundle.mjs 가 공개 번들에서 API 경로 문자열을 찾으면 실패시킨다).
//
// - 쿠키 세션(`credentials: 'same-origin'`). 읽기는 GET. 개인 쓰기(P8 — 규칙·백테스트·저널·관심종목)는
//   post·put·patch·del: 세션 CSRF 토큰(auth.ts 가 메모리에만 든 값)을 `X-KBJ-CSRF` 헤더로, 본문은 JSON.
//   토큰이 없으면 요청하지 않고 실패(서버 403 을 기다리지 않는다). 거절(4xx)은 서버가 만든 짧은 사유 문구만 싣는다
//   (입력값을 되풀이하지 않는다 — ErrorBody `{code, message}`).
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
/** 경로 형식: 'market/summary', 'flows/stock/005930', 'index-review/stock/005930'(첫 마디 하이픈은 낱말 사이만 — P5 2차 IDX)
 *  — 상위 경로(..)·절대 URL 금지 */
export const API_PATH = /^[a-z0-9_]+(?:-[a-z0-9_]+)*(?:\/[A-Za-z0-9_-]+)*$/;
/** 내려받기 경로(P4): API 경로 + 정해진 확장자 하나 — 'stock/005930/report.xlsx' */
export const API_DOWNLOAD = /^[a-z0-9_]+(?:\/[A-Za-z0-9_-]+)*\.xlsx$/;

export interface ApiSourceOptions {
  fetch?: FetchLike;
  base?: string;
  /** 401 을 받았을 때 — 로그인 화면으로 */
  onUnauthorized?: () => void;
  /** 쓰기 요청 헤더(CSRF) — auth.writeHeaders. 없으면 쓰기 채널을 만들지 않는다(읽기 전용 소스) */
  writeHeaders?: () => Record<string, string> | null;
}

/** 로그인 소스의 쓰기 채널(P8 — 페이지 11·12·8 의 쓰기 패널이 `post` 유무로 찾는다) */
export interface WriteChannel {
  post<T>(key: string, body: unknown): Promise<Envelope<T>>;
  put<T>(key: string, body?: unknown): Promise<Envelope<T>>;
  patch<T>(key: string, body: unknown): Promise<Envelope<T>>;
  del<T>(key: string): Promise<Envelope<T>>;
}

/** 서버 문구 길이 상한 — 짧은 안내만 받는다(그 밖 본문은 그리지 않는다) */
const NO_DATA_MAX = 200;

/**
 * 404 본문이 `{code: 'no_data', message}` 이면 서버가 만든 안내 문구(예정 작업·준비 중 사유)를 돌려준다.
 * 그 밖의 404 본문(라우트 없음 등)은 null — 화면은 패널마다 정한 문구를 쓴다.
 */
async function noDataMessage(res: Response): Promise<string | null> {
  return serverMessage(res, (code) => code === 'no_data');
}

/** 오류 본문 `{code, message}` 의 짧은 서버 문구(코드 조건을 만족할 때만). 그 밖은 null */
async function serverMessage(res: Response, accept: (code: unknown) => boolean): Promise<string | null> {
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    return null;
  }
  if (typeof body !== 'object' || body === null) return null;
  const { code, message } = body as { code?: unknown; message?: unknown };
  if (!accept(code) || typeof message !== 'string') return null;
  const text = message.trim();
  return text && text.length <= NO_DATA_MAX ? text : null;
}

export function createApiSource(opts: ApiSourceOptions = {}): DataSource & Partial<WriteChannel> {
  const doFetch = opts.fetch ?? globalFetch;
  const base = opts.base ?? API_BASE;

  async function write<T>(method: 'POST' | 'PUT' | 'PATCH' | 'DELETE', key: string, body?: unknown): Promise<Envelope<T>> {
    if (!API_PATH.test(key)) throw new DataError('잘못된 API 경로');
    const auth = opts.writeHeaders?.() ?? null;
    if (!auth) throw new DataError('쓰기 권한 없음 — 다시 로그인하세요', 403);
    const headers: Record<string, string> = { Accept: 'application/json', ...auth };
    const init: RequestInit = { method, credentials: 'same-origin', cache: 'no-store', headers };
    if (body !== undefined && body !== null) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    const res = await doFetch(`${base}${key}`, init);
    if (res.status === 401) {
      opts.onUnauthorized?.();
      throw new AuthRequiredError();
    }
    if (!res.ok) {
      const msg = await serverMessage(res, (code) => typeof code === 'string');
      throw new DataError(msg ?? `저장하지 못함 (HTTP ${res.status})`, res.status, msg);
    }
    let raw: unknown;
    try {
      raw = await res.json();
    } catch {
      throw new DataError('JSON 형식 오류', res.status);
    }
    const env = asEnvelope<T>(raw);
    if (!env) throw new DataError('원천·시각·품질 표시 없음', res.status);
    return env;
  }

  const channel: Partial<WriteChannel> = opts.writeHeaders
    ? {
        post: <T>(key: string, body: unknown) => write<T>('POST', key, body),
        put: <T>(key: string, body?: unknown) => write<T>('PUT', key, body),
        patch: <T>(key: string, body: unknown) => write<T>('PATCH', key, body),
        del: <T>(key: string) => write<T>('DELETE', key),
      }
    : {};
  return {
    ...channel,
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
    href(key: string): string | null {
      // 요청은 하지 않는다 — 브라우저가 링크로 받는다(쿠키 세션 그대로, 같은 출처)
      return API_DOWNLOAD.test(key) ? `${base}${key}` : null;
    },
  };
}
