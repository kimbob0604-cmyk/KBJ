// 페이지 11 규칙 패널 공용(묶음 EVL — docs/p8_design.md §5·§6): 응답 형·쓰기 채널·패널 사이 알림·DSL 지표 목록·
// 스키마 검사(rule.schema.json — 서버 pydantic 에서 생성한 같은 스키마)·표시 문구.
//
// - 쓰기 채널: 데이터 소스의 선택 기능 `post`·`put`(로그인 CSRF — 페이지 12 백테스트와 같은 약속). 없으면 쓰기 단추를
//   끄고 안내만(요청하지 않는다). 이 모듈은 API 경로 문자열을 만들지 않는다(키만 — 'rules', 'rules/<id>/enable').
// - 스키마 검사는 모양만(키·형·목록·범위). 의미 검사(파라미터 범위·모드별 지표·단위)는 서버 `POST rules/validate`.
// - 공개 빌드는 이 모듈을 번들에서 뺀다(모듈 최상위 부수 효과 없음 — 진입 파일의 LOGIN_FILLS).
import type { components } from '../../api/types.gen';
import schemaJson from '../../api/rule.schema.json';
import type { PageContext } from '../../app/types';
import type { Envelope } from '../../data/source';

type S = components['schemas'];
export type RuleList = S['RuleList'];
export type RuleSummary = S['RuleSummary'];
export type RuleDetail = S['RuleDetail'];
export type RuleExamples = S['RuleExamples'];
export type RuleWriteResult = S['RuleWriteResult'];
export type ValidateResult = S['ValidateResult'];
export type RuleTrial = S['RuleTrial'];
export type SignalList = S['SignalList'];
export type SignalOut = S['SignalOut'];
export type Dsl = Record<string, unknown>;

export const RULES_HINT = '아직 없음 — 편집기에서 규칙을 만든다';
export const NO_WRITER = '쓰기 채널(로그인 CSRF) 준비 뒤 — 읽기만';

// ── 쓰기 채널 ─────────────────────────────────────────────────────────────────────────────────

export interface RuleWriter {
  post<T>(key: string, body: unknown): Promise<Envelope<T>>;
  put?<T>(key: string, body: unknown): Promise<Envelope<T>>;
}

export function ruleWriter(source: unknown): RuleWriter | null {
  const post = (source as Partial<RuleWriter> | null)?.post;
  return typeof post === 'function' ? (source as RuleWriter) : null;
}

// ── 패널 사이 알림(페이지 문맥마다) ───────────────────────────────────────────────────────────

export interface RuleBus {
  /** 규칙 목록이 바뀌었다(만들기·새 판·상태) — ① 다시 읽기 */
  changed: (() => void)[];
  /** ① 에서 '편집' — ② 에 판 DSL 을 싣는다 */
  edit: ((ruleId: string, dsl: Dsl) => void)[];
}

let buses: WeakMap<PageContext, RuleBus> | undefined;
export function busOf(ctx: PageContext): RuleBus {
  buses ??= new WeakMap();
  let b = buses.get(ctx);
  if (!b) {
    b = { changed: [], edit: [] };
    buses.set(ctx, b);
  }
  return b;
}

// ── DSL 지표 목록(kbj.engines.rules.dsl.INDICATORS 의 화면판 — 검사는 서버) ─────────────────────────

export interface ParamDef {
  key: string;
  def: number | string;
  min?: number;
  max?: number;
  choices?: readonly string[];
}

export interface IndDef {
  label: string;
  params: readonly ParamDef[];
  fields?: readonly string[];
  /** 장중 평가 가능(신고가는 마감 전용) */
  intraday: boolean;
  flow?: boolean;
}

const n = (def: number, min: number, max: number): ParamDef => ({ key: 'n', def, min, max });
const side: ParamDef = { key: 'side', def: 'buy', choices: ['buy', 'sell'] };

export const INDICATORS: Readonly<Record<string, IndDef>> = {
  close: { label: '종가(현재가)', params: [], intraday: true },
  open: { label: '시가', params: [], intraday: true },
  high: { label: '고가', params: [], intraday: true },
  low: { label: '저가', params: [], intraday: true },
  change_pct: { label: '등락률 %', params: [], intraday: true },
  sma: { label: 'SMA', params: [n(5, 2, 250)], intraday: true },
  ema: { label: 'EMA', params: [n(5, 2, 250)], intraday: true },
  rsi: { label: 'RSI', params: [n(14, 2, 50)], intraday: true },
  macd: {
    label: 'MACD',
    params: [
      { key: 'fast', def: 12, min: 2, max: 50 },
      { key: 'slow', def: 26, min: 3, max: 100 },
      { key: 'signal', def: 9, min: 2, max: 50 },
    ],
    fields: ['line', 'signal', 'hist'],
    intraday: true,
  },
  bb: {
    label: '볼린저',
    params: [n(20, 5, 100), { key: 'k', def: 2, min: 0.5, max: 4 }],
    fields: ['upper', 'mid', 'lower', 'pctb', 'width'],
    intraday: true,
  },
  bb_width_rank: {
    label: '밴드폭 백분위',
    params: [n(20, 5, 100), { key: 'k', def: 2, min: 0.5, max: 4 }, { key: 'lookback', def: 120, min: 20, max: 500 }],
    intraday: true,
  },
  adx: { label: 'ADX', params: [n(14, 2, 50)], fields: ['adx', 'pdi', 'mdi'], intraday: true },
  atr: { label: 'ATR', params: [n(14, 2, 50)], intraday: true },
  vol_ratio: { label: '거래량 배수', params: [n(20, 2, 120)], intraday: true },
  high_n: { label: 'N일 최고', params: [n(20, 2, 250)], intraday: true },
  low_n: { label: 'N일 최저', params: [n(20, 2, 250)], intraday: true },
  new_high: {
    label: '신고가(마감)',
    params: [{ key: 'axis', def: '52w', choices: ['d120', '52w', 'ath'] }],
    intraday: false,
  },
  foreign_streak: { label: '외국인 연속', params: [side], intraday: true, flow: true },
  inst_streak: { label: '기관 연속', params: [side], intraday: true, flow: true },
  foreign_sum: { label: '외국인 N일 합(원)', params: [n(5, 1, 60)], intraday: true, flow: true },
  inst_sum: { label: '기관 N일 합(원)', params: [n(5, 1, 60)], intraday: true, flow: true },
  trail_from_peak: { label: '보유 고점 대비 %', params: [], intraday: true },
};

export const OPS = [
  '<',
  '<=',
  '>',
  '>=',
  '==',
  'between',
  'cross_above',
  'cross_below',
  'turns_positive',
  'turns_negative',
] as const;
export type Op = (typeof OPS)[number];
export const OP_LABEL: Readonly<Record<Op, string>> = {
  '<': '<',
  '<=': '≤',
  '>': '>',
  '>=': '≥',
  '==': '=',
  between: '사이',
  cross_above: '상향 돌파',
  cross_below: '하향 돌파',
  turns_positive: '양전환',
  turns_negative: '음전환',
};

// ── 표시 문구 ─────────────────────────────────────────────────────────────────────────────────

export const MODE_LABEL: Readonly<Record<string, string>> = { close: '마감', intraday: '장중', both: '장중+마감' };
export const STATUS_LABEL: Readonly<Record<string, string>> = {
  sent: '보냄',
  cooldown: '쿨다운',
  suppressed: '막힘(중복)',
  dry: '발송 꺼짐',
  skipped: '건너뜀',
};
export const SKIP_LABEL: Readonly<Record<string, string>> = {
  split_suspect: '분할 의심',
  halted: '거래정지',
  no_live_flow: '판단 불가(장중 가집계 없음)',
  truncated: '대상 상한으로 잘림',
  no_data: '데이터 없음',
  error: '오류',
};
export const REASON_LABEL: Readonly<Record<string, string>> = {
  insufficient: '값 없음(워밍업 부족)',
  missing: '값 없음(결측)',
  halted: '값 없음(거래정지)',
  no_live_flow: '판단 불가(장중 가집계 없음)',
};

export function universeText(u: Record<string, unknown>): string {
  if (Array.isArray(u.codes)) return `종목 ${String(u.codes.length)}개`;
  if (typeof u.watchlist === 'string') return `관심 ${u.watchlist}`;
  if (u.portfolio === true) return '보유 종목';
  if (typeof u.market === 'string') return `시장 ${u.market}`;
  return '—';
}

export function cooldownText(min: number): string {
  if (min % 1440 === 0) return `${String((min / 1440) * 24)}시간`;
  if (min % 60 === 0) return `${String(min / 60)}시간`;
  return `${String(min)}분`;
}

export function mark(ok: boolean | null | undefined): string {
  return ok === true ? '✔' : ok === false ? '✖' : '?';
}

// ── 스키마 검사(모양만 — JSON Schema 일부: $ref·type·properties·required·additionalProperties·enum·const·
//    minimum·maximum·exclusiveMinimum·minLength·maxLength·pattern·items·prefixItems·minItems·maxItems·anyOf·oneOf) ──

export interface SchemaIssue {
  pointer: string;
  reason: string;
}

type Js = Record<string, unknown>;
const ROOT = schemaJson as unknown as Js;

function deref(s: Js): Js {
  const ref = s.$ref;
  if (typeof ref !== 'string') return s;
  const name = ref.replace('#/$defs/', '');
  const defs = ROOT.$defs as Record<string, Js>;
  const got = defs[name];
  if (!got) throw new Error(`스키마 참조 없음: ${ref}`);
  return got;
}

function typeOk(t: string, v: unknown): boolean {
  switch (t) {
    case 'object':
      return typeof v === 'object' && v !== null && !Array.isArray(v);
    case 'array':
      return Array.isArray(v);
    case 'string':
      return typeof v === 'string';
    case 'integer':
      return typeof v === 'number' && Number.isInteger(v);
    case 'number':
      return typeof v === 'number' && Number.isFinite(v);
    case 'boolean':
      return typeof v === 'boolean';
    case 'null':
      return v === null;
    default:
      return false;
  }
}

function check(s0: Js, v: unknown, at: string, out: SchemaIssue[]): void {
  const s = deref(s0);
  for (const key of ['anyOf', 'oneOf'] as const) {
    const alts = s[key];
    if (Array.isArray(alts)) {
      const okCount = (alts as Js[]).filter((a) => {
        const sub: SchemaIssue[] = [];
        check(a, v, at, sub);
        return sub.length === 0;
      }).length;
      if (key === 'anyOf' ? okCount === 0 : okCount !== 1) out.push({ pointer: at, reason: 'no_match' });
      return;
    }
  }
  if (typeof s.type === 'string' && !typeOk(s.type, v)) {
    out.push({ pointer: at, reason: `type_${s.type}` });
    return;
  }
  if ('const' in s && v !== s.const) out.push({ pointer: at, reason: 'const' });
  if (Array.isArray(s.enum) && !(s.enum as unknown[]).includes(v)) out.push({ pointer: at, reason: 'enum' });
  if (typeof v === 'number') {
    if (typeof s.minimum === 'number' && v < s.minimum) out.push({ pointer: at, reason: 'minimum' });
    if (typeof s.maximum === 'number' && v > s.maximum) out.push({ pointer: at, reason: 'maximum' });
    if (typeof s.exclusiveMinimum === 'number' && v <= s.exclusiveMinimum) out.push({ pointer: at, reason: 'minimum' });
  }
  if (typeof v === 'string') {
    if (typeof s.minLength === 'number' && v.length < s.minLength) out.push({ pointer: at, reason: 'min_length' });
    if (typeof s.maxLength === 'number' && v.length > s.maxLength) out.push({ pointer: at, reason: 'max_length' });
    if (typeof s.pattern === 'string' && !new RegExp(s.pattern).test(v)) out.push({ pointer: at, reason: 'pattern' });
  }
  if (Array.isArray(v)) {
    if (typeof s.minItems === 'number' && v.length < s.minItems) out.push({ pointer: at, reason: 'min_items' });
    if (typeof s.maxItems === 'number' && v.length > s.maxItems) out.push({ pointer: at, reason: 'max_items' });
    const pre = Array.isArray(s.prefixItems) ? (s.prefixItems as Js[]) : [];
    v.forEach((item, i) => {
      const sub = pre[i] ?? (typeof s.items === 'object' && s.items !== null ? (s.items as Js) : null);
      if (sub) check(sub, item, `${at}/${String(i)}`, out);
    });
  }
  if (typeOk('object', v)) {
    const obj = v as Record<string, unknown>;
    const props = (s.properties ?? {}) as Record<string, Js>;
    for (const r of (s.required ?? []) as string[])
      if (!(r in obj)) out.push({ pointer: `${at}/${r}`, reason: 'missing' });
    for (const [k, val] of Object.entries(obj)) {
      const ps = props[k];
      if (ps) check(ps, val, `${at}/${k}`, out);
      else if (s.additionalProperties === false) out.push({ pointer: `${at}/${k}`, reason: 'extra_forbidden' });
      else if (typeof s.additionalProperties === 'object' && s.additionalProperties !== null)
        check(s.additionalProperties as Js, val, `${at}/${k}`, out);
    }
  }
}

/** DSL 모양 검사 — 문제(위치·사유)가 없으면 빈 목록. 입력값은 싣지 않는다. */
export function schemaIssues(dsl: unknown): SchemaIssue[] {
  const out: SchemaIssue[] = [];
  check(ROOT, dsl, '', out);
  return out;
}
