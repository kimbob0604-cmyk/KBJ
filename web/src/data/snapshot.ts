// 공개 사이트 데이터 소스 — Beom's Dashboard 방식(사용자 결정 2026-11-09, docs/adr/0042-site-snapshot.md).
//
// 맥이 로그인 화면용 API 응답을 정적 JSON 으로 만들어(kbj/services/api/site.py) GitHub Pages(kbj-site)에 올린다.
// 이 소스는 로그인 빌드와 **같은 키**(API 경로 + 인자)를 받아 `./data/<경로>[@<인자>].json` 을 읽는다.
// - 파일 이름 siteFile 은 site.py site_file 과 같은 규칙: 인자 키 정렬·빈 값 생략, `<키>-<값>` 을 '.' 로 잇고
//   [A-Za-z0-9_-] 밖 문자는 '_'.
// - API 기본값과 같은 인자는 뺀다(`_defaults.json` — 내보내기도 빼고 불렀다. 화면은 기본값을 적어 부르기도 한다).
// - 파일이 없으면(404): 날짜 인자(YYYY-MM-DD)를 빼고 한 번 더 찾되, 요청한 날짜(date, 없으면 to)가 그 파일을 만든 날
//   (generated_at 의 KST 날짜)과 같을 때만 쓴다 — 서버 기본값(그날)으로 만든 파일이 그 보기와 같을 때만. 아니면('어제'를
//   눌렀는데 오늘 만든 파일뿐 등) '공개 사이트에 없는 보기'. 다른 인자는 빼지 않는다 — 다른 보기의 숫자를 그 보기인 척
//   그리지 않는다.
// - 파일이 404 `no_data` 본문이면 로그인판과 같은 서버 안내 문구(serverMessage).
// - 'stock/search' 는 전 종목 목록 `stock/search-index.json` 을 한 번 받아 서버 검색과 같은 순위로 거른다(ui/search.ts
//   matchItems — 목록이 시총 순이라 같은 순위 안 순서도 서버와 같다). 목록이 상세 없음(detail=false)이라 한 종목의
//   'stock/<코드>/…' 는 요청 없이 NO_DETAIL, 전 종목 목록(complete)에 없는 코드는 serverMessage 없는 404(서버
//   unknown_code 와 같은 길). 목록을 아직 받지 않았으면 첫 404 뒤에 받아 판정한다(상세가 있는 종목은 받지 않는다).
// - 쓰기는 관심종목(main) 하나 — 이 브라우저 localStorage('kbj-watch')에만 둔다(내보내기·서버·kbj-site 저장소에는
//   없다). put·del 만 연다 — post·patch 가 없어 페이지 11·12 쓰기 패널은 켜지지 않는다. 'watchlist' 읽기와 종목 머리·
//   검색의 is_watch 는 이 목록으로 채운다(값은 이 브라우저 안에서만 — 파일·서버로 나가지 않는다). 시세는 이미 올라간
//   종목 머리 파일 값 그대로(지어내지 않는다).
import type { components } from '../api/types.gen';
import { kstDate } from '../ui/fmt';
import { matchItems } from '../ui/search';
import {
  asEnvelope,
  DataError,
  type DataSource,
  type Envelope,
  type FetchLike,
  globalFetch,
  NO_DETAIL,
  type Params,
  type Quality,
  worstQuality,
} from './source';

type Schemas = components['schemas'];

export const SEARCH_KEY = 'stock/search';
export const SEARCH_INDEX = 'stock/search-index';
export const WATCH_KEY = 'watchlist';
/** 관심종목 저장 키 — Beom's Dashboard 와 같은 출처(github.io)라 localStorage 를 함께 쓴다: 'kbj-' 접두(스킨 'kbj-skin' 과 같은 꼴) */
export const WATCH_STORAGE_KEY = 'kbj-watch';
/** 이 브라우저 관심종목 상한 [확인 필요 — 기본값으로 정함] */
export const WATCH_MAX = 100;
export const MISSING = '공개 사이트에 없는 보기';
export const READ_ONLY = '공개 사이트에서는 저장할 수 없음';
export const WATCH_EMPTY = '관심종목 없음 — 종목 머리의 ☆ 관심 추가(이 브라우저에만 저장)';
export const WATCH_BROKEN = '저장된 관심종목을 읽지 못함';
export const WATCH_SAVE_FAILED = '이 브라우저에 저장하지 못함';
const WATCH_REJECT = '관심종목 쓰기 거절';
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const CODE = /^[0-9A-Z]{6}$/;
const UNSAFE = /[^A-Za-z0-9_-]/g;
/** 키 형식(로그인 API 경로와 같다) — 상위 경로·절대 주소 금지 */
const KEY = /^[a-z0-9_]+(?:-[a-z0-9_]+)*(?:\/[A-Za-z0-9_-]+)*$/;
/** 종목 상세 키(내보내기 code_templates — 종목마다 만드는 파일) → 종목 코드 */
const STOCK_KEY = /^(?:stock|flows\/stock|etf\/holders|trade\/stock|index-review\/stock)\/([0-9A-Z]{6})(?:\/|$)/;
const HEADER_KEY = /^stock\/([0-9A-Z]{6})\/header$/;
/** 받는 쓰기 키 — 관심종목 main 목록 한 종목뿐 */
const WATCH_WRITE = /^watchlist\/main\/([0-9A-Z]{6})$/;
const TEXT_MAX = 100;

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

/** 기본값과 같은 인자를 뺀다 — 내보내기도 빼고 불렀다 */
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

/** 날짜 인자를 뺀 인자와 요청한 날짜(date, 없으면 to) — 날짜 인자가 하나도 없으면 null */
function withoutDates(params: Params | undefined): { rest: Params; day: string | null } | null {
  if (!params) return null;
  const rest: Record<string, Params[string]> = {};
  let dropped = false;
  for (const [k, v] of Object.entries(params)) {
    if (typeof v === 'string' && DATE.test(v)) dropped = true;
    else rest[k] = v;
  }
  if (!dropped) return null;
  const day = (v: unknown): string | null => (typeof v === 'string' && DATE.test(v) ? v : null);
  return { rest, day: day(params.date) ?? day(params.to) };
}

/** 전 종목 목록 한 줄(site.py — readers.stock.stock_index). 옛 목록(상세 종목만)에는 detail·complete 가 없다 */
interface IndexItem {
  code: string;
  name: string | null;
  market?: string | null;
  kind?: string | null;
  is_watch?: boolean;
  /** 공개 사이트에 상세 파일이 있는가 */
  detail?: boolean;
  [k: string]: unknown;
}

interface SearchIndex {
  q?: string;
  n_total?: number;
  /** 전 종목인가 — 참이면 목록에 없는 코드는 모르는 코드 */
  complete?: boolean;
  items: IndexItem[];
}

/** 이 브라우저에 둔 관심종목 한 줄 — {"v":1,"main":[…]} */
interface WatchItem {
  code: string;
  name: string | null;
  market: string | null;
  kind: string | null;
  added_at: string | null;
}

type Kind = Schemas['WatchRow']['kind'];
const KINDS: readonly string[] = ['common', 'pref', 'spac', 'reit', 'etf', 'etn'];

function text(v: unknown): string | null {
  return typeof v === 'string' && v.length > 0 && v.length <= TEXT_MAX ? v : null;
}

/** 목록 한 줄 검사 — 코드가 맞지 않으면 뺀다, 이름은 문자열만(검색이 이름을 소문자로 바꾼다) */
function indexItem(v: unknown): IndexItem[] {
  if (typeof v !== 'object' || v === null) return [];
  const o = v as Record<string, unknown>;
  if (typeof o.code !== 'string' || !CODE.test(o.code)) return [];
  return [{ ...o, code: o.code, name: typeof o.name === 'string' ? o.name : null }];
}

function kindOf(v: unknown): Kind {
  return typeof v === 'string' && KINDS.includes(v) ? (v as Kind) : null;
}

/** 저장 문자열 → 목록. 형식이 어긋나면 null(덮어쓰지 않는다). 같은 코드가 겹치면 처음 것만 */
export function parseWatch(raw: string): WatchItem[] | null {
  let v: unknown;
  try {
    v = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof v !== 'object' || v === null) return null;
  const r = v as { v?: unknown; main?: unknown };
  if (r.v !== 1 || !Array.isArray(r.main) || r.main.length > WATCH_MAX) return null;
  const out: WatchItem[] = [];
  const seen = new Set<string>();
  for (const x of r.main as unknown[]) {
    if (typeof x !== 'object' || x === null) return null;
    const o = x as Record<string, unknown>;
    if (typeof o.code !== 'string' || !CODE.test(o.code)) return null;
    if (seen.has(o.code)) continue;
    seen.add(o.code);
    out.push({ code: o.code, name: text(o.name), market: text(o.market), kind: text(o.kind), added_at: text(o.added_at) });
  }
  return out;
}

export interface SnapshotSourceOptions {
  fetch?: FetchLike;
  /** 데이터 디렉터리(끝에 /). 기본 './data/' — Pages 하위 경로에서도 맞게 상대 경로 */
  base?: string;
  /** 관심종목 저장소(boot/site.ts 가 스킨과 같은 브라우저 localStorage 를 넘긴다). 생략·null 이면 저장하지 않는다 */
  storage?: Pick<Storage, 'getItem' | 'setItem'> | null;
  /** 시계(관심종목 추가 시각·봉투 generated_at) — 시험은 고정 시계 */
  now?: () => Date;
}

/** 공개 사이트 소스 — 읽기 + 관심종목 쓰기(put·del 만 — 이 브라우저). post·patch 는 없다 */
export interface SnapshotSource extends DataSource {
  put<T>(key: string, body?: unknown): Promise<Envelope<T>>;
  del<T>(key: string): Promise<Envelope<T>>;
  /** 관심종목이 이 브라우저에 있다는 표시(페이지 8 빈 목록 안내가 소스의 notes 를 쓴다) */
  readonly watchStore: 'browser';
}

export function createSnapshotSource(opts: SnapshotSourceOptions = {}): SnapshotSource {
  const doFetch = opts.fetch ?? globalFetch;
  const base = opts.base ?? './data/';
  const storage = opts.storage ?? null;
  const clock = opts.now ?? ((): Date => new Date());
  let index: Promise<Envelope<SearchIndex>> | null = null;
  /** 받은 전 종목 목록(코드 → 줄) — 받기 전에는 null */
  let known: Map<string, IndexItem> | null = null;
  let complete = false;
  /** 머리 파일을 읽은 종목 — 상세가 있다(그 종목의 다른 파일이 없어도 목록을 받아 판정하지 않는다) */
  const headed = new Set<string>();
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

  /** 전 종목 목록 — 한 번 받는다(실패하면 다음에 다시) */
  function loadIndex(): Promise<Envelope<SearchIndex>> {
    if (!index) {
      const mine = load<unknown>(SEARCH_INDEX).then((env): Envelope<SearchIndex> => {
        const d = (typeof env.data === 'object' && env.data !== null ? env.data : {}) as Partial<Record<string, unknown>>;
        const items = (Array.isArray(d.items) ? (d.items as unknown[]) : []).flatMap(indexItem);
        known = new Map(items.map((it) => [it.code, it]));
        complete = d.complete === true;
        return { ...env, data: { ...d, items, complete } };
      });
      index = mine;
      mine.catch(() => {
        if (index === mine) index = null;
      });
    }
    return index;
  }

  /** 이미 받은 목록으로 본 상세 — 'yes'·'no'(NO_DETAIL)·'unknown'(전 종목에 없음)·null(목록 없음·옛 목록이라 모름) */
  function detailOf(code: string): 'yes' | 'no' | 'unknown' | null {
    if (!known) return null;
    const it = known.get(code);
    if (!it) return complete ? 'unknown' : null;
    return it.detail === false ? 'no' : it.detail === true ? 'yes' : null;
  }

  /** 상세가 없다고 아는 종목은 요청 없이 던진다 */
  function gate(code: string): void {
    const d = detailOf(code);
    if (d === 'no') throw new DataError(NO_DETAIL, 404, NO_DETAIL);
    if (d === 'unknown') throw new DataError('모르는 종목 코드', 404);
  }

  /** 날짜를 빼고 찾은 파일 — 요청한 날짜가 그 파일을 만든 날(KST)과 같을 때만(아니면 null) */
  async function sameDay(key: string, params: Params | undefined): Promise<unknown> {
    const alt = withoutDates(params);
    if (!alt?.day) return null;
    const raw = await read(siteFile(key, alt.rest));
    const env = raw === null ? null : asEnvelope(raw);
    return env && kstDate(env.generated_at) === alt.day ? raw : null;
  }

  async function load<T>(key: string, given?: Params): Promise<Envelope<T>> {
    if (!KEY.test(key)) throw new DataError('잘못된 경로');
    const code = STOCK_KEY.exec(key)?.[1] ?? null;
    if (code) gate(code);
    const params = given ? dropDefaults(key, given, await loadDefaults()) : given;
    let raw = await read(siteFile(key, params));
    if (raw === null) raw = await sameDay(key, params);
    if (raw === null && code && !known && !headed.has(code)) {
      // 주소로 바로 들어온 종목 — 첫 404 뒤에 목록을 받아 판정한다. 목록을 못 받으면 판정 없이 아래 '없는 보기'
      // (이 요청의 실패는 그대로 보인다 — 목록은 문구를 고르는 데만 쓴다)
      await loadIndex().catch(() => undefined);
      gate(code);
    }
    if (raw === null) throw new DataError(MISSING, 404, MISSING);
    const env = asEnvelope<T>(raw);
    if (env && code && HEADER_KEY.test(key)) {
      headed.add(code);
      return withWatch(env, code);
    }
    if (env) return env;
    const r = raw as Record<string, unknown>;
    if (r.code === 'no_data' && typeof r.message === 'string') {
      const msg = r.message.trim().slice(0, 200) || '아직 없음';
      throw new DataError(msg, 404, msg);
    }
    throw new DataError('원천·시각·품질 표시 없음');
  }

  // ── 관심종목(이 브라우저) ─────────────────────────────

  function readWatch(): { items: WatchItem[]; problem: string | null } {
    if (!storage) return { items: [], problem: null };
    let raw: string | null;
    try {
      raw = storage.getItem(WATCH_STORAGE_KEY);
    } catch {
      return { items: [], problem: WATCH_BROKEN }; // 저장소 막힘 — 목록 notes 에 남긴다
    }
    if (raw === null) return { items: [], problem: null };
    const items = parseWatch(raw);
    return items ? { items, problem: null } : { items: [], problem: WATCH_BROKEN };
  }

  function writeWatch(items: readonly WatchItem[]): void {
    if (!storage) throw new DataError(WATCH_SAVE_FAILED);
    try {
      storage.setItem(WATCH_STORAGE_KEY, JSON.stringify({ v: 1, main: items }));
    } catch {
      throw new DataError(WATCH_SAVE_FAILED); // 용량 초과·사생활 보호 모드 — 별 옆에 사유
    }
  }

  function watched(): Set<string> {
    return new Set(readWatch().items.map((it) => it.code));
  }

  /** 머리·검색 줄의 관심종목 표시를 이 브라우저 목록으로 */
  function withWatch<T>(env: Envelope<T>, code: string): Envelope<T> {
    const d: unknown = env.data;
    if (typeof d !== 'object' || d === null || Array.isArray(d)) return env;
    return { ...env, data: { ...(d as Record<string, unknown>), is_watch: watched().has(code) } as T };
  }

  /** 종목 머리 파일(이미 올라간 것) — 없으면(상세 없음·no_data) null. 목록 판정은 하지 않는다(목록을 받지 않게) */
  async function headerOf(code: string): Promise<Envelope<Schemas['StockHeader']> | null> {
    const d = detailOf(code);
    if (d === 'no' || d === 'unknown') return null;
    const raw = await read(siteFile(`stock/${code}/header`));
    if (raw === null) return null;
    const env = asEnvelope<Schemas['StockHeader']>(raw);
    if (env) return env;
    if ((raw as Record<string, unknown>).code === 'no_data') return null;
    throw new DataError('원천·시각·품질 표시 없음');
  }

  /** 서버 readers/watchlist.py 와 같은 규칙 — 이름·시장·종류는 머리(없으면 넣을 때 값), 시세는 머리의 마지막 시세 */
  async function watchGet<T>(params?: Params): Promise<Envelope<T>> {
    const list = params?.list;
    if (list !== undefined && list !== null && list !== '' && list !== 'main') throw new DataError(MISSING, 404, MISSING);
    const st = readWatch();
    const heads = await Promise.allSettled(st.items.map((it) => headerOf(it.code)));
    const rows: Schemas['WatchRow'][] = [];
    const sources = new Set<string>();
    let asOf: string | null = null;
    let failed = 0;
    for (const [i, it] of st.items.entries()) {
      const r = heads[i];
      const env = r?.status === 'fulfilled' ? r.value : null;
      if (r?.status === 'rejected') failed += 1;
      const h = env?.data ?? null;
      const last = h?.last ?? null;
      if (env && last) {
        sources.add(last.source);
        if (asOf === null || Date.parse(env.as_of) > Date.parse(asOf)) asOf = env.as_of;
      }
      rows.push({
        code: it.code,
        name: h?.name ?? it.name,
        sort: i,
        added_at: it.added_at,
        note: null,
        source: 'browser',
        market: h?.market ?? it.market,
        kind: h?.kind ?? kindOf(it.kind),
        date: last?.date ?? null,
        close: last?.close ?? null,
        chg_pct: last?.chg_pct ?? null,
        mktcap: last?.mktcap ?? null,
        quality: last?.quality ?? null,
      });
    }
    const notes: string[] = [];
    if (st.problem) notes.push(st.problem);
    else if (!rows.length) notes.push(WATCH_EMPTY);
    const noPrice = rows.filter((r) => r.date === null).length - failed;
    if (noPrice > 0) notes.push(`시세 없는 종목 ${noPrice}개(공개 사이트에 상세가 없거나 시세 없음)`);
    if (failed > 0) notes.push(`${failed}개 시세를 읽지 못함`);
    const qs = rows.map((r) => r.quality).filter((q): q is Quality => q !== null);
    const now = clock().toISOString();
    const data: Schemas['Watchlist'] = { list_name: 'main', n: rows.length, items: rows };
    return {
      source: sources.size ? `이 브라우저 · ${[...sources].sort().join('+')}` : '이 브라우저',
      as_of: asOf ?? now,
      quality: worstQuality(failed > 0 ? [...qs, 'stale'] : qs),
      notes,
      generated_at: now,
      data: data as T,
    };
  }

  /**
   * 넣을 때 이름·시장·종류(서버 name_at_add 구실) — 전 종목 목록, 없으면 머리 파일. 목록도 머리도 읽지 못해 종목을 확인할 수
   * 없으면 넣지 않고 그 사유를 던진다(별 옆에 보인다). 옛 목록(상세 종목만)에 없는 코드는 이름 없이 넣는다.
   */
  async function metaOf(code: string): Promise<Pick<WatchItem, 'name' | 'market' | 'kind'>> {
    let indexErr: Error | null = null;
    try {
      await loadIndex();
    } catch (err) {
      // 머리 파일로 확인해 본다 — 그것도 없으면 아래에서 이 사유로 실패
      indexErr = err instanceof Error ? err : new DataError('종목 목록을 읽지 못함');
    }
    if (detailOf(code) === 'unknown') throw new DataError(WATCH_REJECT, 404, WATCH_REJECT); // 서버 unknown_code 와 같다
    const it = known?.get(code);
    if (it) return { name: text(it.name), market: text(it.market), kind: text(it.kind) };
    const h = await headerOf(code);
    if (h) return { name: text(h.data.name), market: text(h.data.market), kind: text(h.data.kind) };
    if (indexErr) throw indexErr;
    return { name: null, market: null, kind: null };
  }

  async function watchWrite<T>(key: string, add: boolean): Promise<Envelope<T>> {
    const code = WATCH_WRITE.exec(key)?.[1];
    if (!code) throw new DataError(READ_ONLY, 405); // 관심종목 main 밖 — 저장소·네트워크를 건드리지 않는다
    if (!storage) throw new DataError(WATCH_SAVE_FAILED);
    const st = readWatch();
    if (st.problem) throw new DataError(`${st.problem} — 덮어쓰지 않음`, 409);
    const has = st.items.some((it) => it.code === code);
    if (add && !has) {
      if (st.items.length >= WATCH_MAX) throw new DataError(`관심종목은 이 브라우저에 ${WATCH_MAX}개까지`, 422);
      const meta = await metaOf(code);
      const fresh = readWatch(); // 이름을 찾는 사이 다른 탭이 바꿨을 수 있다 — 다시 읽고 더한다
      if (fresh.problem) throw new DataError(`${fresh.problem} — 덮어쓰지 않음`, 409);
      if (!fresh.items.some((it) => it.code === code)) {
        if (fresh.items.length >= WATCH_MAX) throw new DataError(`관심종목은 이 브라우저에 ${WATCH_MAX}개까지`, 422);
        writeWatch([...fresh.items, { code, ...meta, added_at: clock().toISOString() }]);
      }
    } else if (!add) {
      if (!has) throw new DataError(WATCH_REJECT, 404, WATCH_REJECT); // 서버 not_in_list 와 같다
      writeWatch(st.items.filter((it) => it.code !== code));
    }
    return watchGet<T>();
  }

  async function search<T>(params?: Params): Promise<Envelope<T>> {
    const env = await loadIndex();
    const q = String(params?.q ?? '').trim();
    const limit = Number(params?.limit ?? 20) || 20;
    // 서버 stock_search 와 같은 순위(코드 일치 > 코드 앞부분 > 이름 앞부분 > 이름 포함), 같은 순위는 목록 순서(시총 순)
    const hits = q ? matchItems(env.data.items, q, Number.POSITIVE_INFINITY) : [];
    const mine = watched();
    const items = hits.slice(0, limit).map((it) => ({ ...it, is_watch: mine.has(it.code) }));
    return { ...env, data: { q, n_total: hits.length, items } as T };
  }

  return {
    tier: 'login',
    watchStore: 'browser',
    get<T>(key: string, params?: Params): Promise<Envelope<T>> {
      if (key === SEARCH_KEY) return search<T>(params);
      if (key === WATCH_KEY) return watchGet<T>(params);
      return load<T>(key, params);
    },
    put<T>(key: string): Promise<Envelope<T>> {
      return watchWrite<T>(key, true);
    },
    del<T>(key: string): Promise<Envelope<T>> {
      return watchWrite<T>(key, false);
    },
    href(): string | null {
      return null; // 내려받기(엑셀)는 공개 사이트에 없다
    },
  };
}
