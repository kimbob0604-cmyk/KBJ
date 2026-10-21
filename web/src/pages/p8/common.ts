// 페이지 8(종목 상세)·5(공시·일정)가 함께 쓰는 것 — 탭 문맥·요청 묶음·사유 문구·이름표·서식(docs/p4_design.md §5·§6).
//
// - 데이터는 DataSource.get(key) 만(페이지 모듈에 로그인 API 경로 문자열을 쓰지 않는다 — check-bundle.mjs api_path).
//   로그인 키는 'stock/<코드>/<섹션>'·'filings/<이름>'·'watchlist', 공개 키는 'fin/<코드>'·'reports/<코드>'·'<이름>'.
// - 적용 불가(applicable=false)는 사유 문구(NA_TEXT), 아직 없음(404 no_data)은 서버 문구 또는 HINT 의 작업·시각.
// - 금액은 원(외국 통화 회사는 그 통화) 정수 → 화면에서만 억·조. 없는 값은 '—'(0 으로 그리지 않는다).
// - 공개 파일(X 묶음 — fin/<코드>.json·corp_index.json)은 표를 columns + rows 로 싣는다 — unpack() 으로 객체로 되돌린다.
import type { components } from '../../api/types.gen';
import type { DataSource, Envelope, Params, Quality, Tier } from '../../data/source';
import { DataError, queryString } from '../../data/source';
import { h } from '../../ui/dom';
import { DASH, joEok, kstDate, num, type Num, pct, shortDate, signClass } from '../../ui/fmt';
import { errorText, type Panel, panel, type PanelOptions } from '../../ui/panel';
import { EST_LABEL, qValue } from '../../ui/quality';
import { s, svgBox } from '../../ui/svg';
import type { Column } from '../../ui/table';
import type { AppContext } from '../../app/types';

export type S = components['schemas'];
export type NaReason = S['NaReason'];

// ── 적용 불가 사유(§6.3) ──────────────────────────────────

/** 사유 → 문구(§6.3). what = 어느 칸의 사유인지(적자·자본잠식·매출 없음·금융업은 칸 이름을 넣는다) */
const NA: Readonly<Record<string, string>> = {
  loss: '적자 — @ 산출 안 함',
  capital_impairment: '자본잠식 — @ 산출 안 함',
  no_revenue: '매출 없음 — @ 산출 안 함',
  financial_sector: '금융업 — @ 해당 없음',
  short_history: '상장 1년 미만 — 5년 밴드 없음',
  not_detail_tier: '상세 수집 대상 아님 — 관심종목에 넣으면 다음 수집(16:00·21:00)',
  not_in_consensus_universe: '컨센서스 수집 대상 아님',
  no_filings: '정기보고서 없음 — 재무 미제출',
  spac: '스팩 — 실적 지표 해당 없음',
  konex_no_consensus: '코넥스 — 컨센서스 없음',
  currency_mismatch: '재무 통화가 원화가 아님 — 환산하지 않는다',
  missing_input: '필요한 공시 항목 없음',
};
const NA_WHAT: Readonly<Record<string, string>> = { loss: 'PER', capital_impairment: 'PBR', no_revenue: 'PSR', financial_sector: 'EV/EBITDA' };

export function naText(r: NaReason | null | undefined, what?: string): string {
  return r ? (NA[r] ?? r).replace('@', what ?? NA_WHAT[r] ?? '') : '해당 없음';
}

/** 404 no_data 의 서버 문구가 없을 때 쓰는 예정 작업·시각(A 의 readers *_ETA 와 같은 말) */
export const HINT = {
  market: 'krx.daily 08:05',
  fin: 'fin.quarterly',
  report: 'reports.dart_excel 21:00',
  consensus: 'consensus.snapshot 16:00',
  valuation: 'fin.valuation_band 08:50',
  derive: 'filings.derive',
  feed: 'filings.dart_feed',
  earnings: 'earnings.alerts',
  flows: 'market.close_collect 15:35',
  watchlist: 'watchlist import',
  public: 'public.export 05:30',
} as const;

/** 컨센서스 교체(네이버 → KIS)로 잃은 것 — 화면에 늘 적는다(D-P4-10) */
export const LOST_DEFAULT: readonly string[] = ['분기 컨센서스', '분기 어닝 서프라이즈', '컨센서스 최고·최저'];
export function lostLine(lost: readonly string[] = LOST_DEFAULT): string {
  return `출처 없음 — KIS 교체로 잃음: ${(lost.length ? lost : LOST_DEFAULT).join('·')}`;
}

// ── 이름표 ────────────────────────────────────────────────

/** 공시 종류(피드 feed_kind — F 묶음 FEED_KINDS·KINDS) */
export const KIND_LABEL: Readonly<Record<string, string>> = {
  pre_announce: '실적 예고',
  prelim: '잠정실적',
  overhang: '오버행 결정',
  overhang_exercise: '전환·행사 청구',
  price_adjust: '가액 조정',
  buyback: '자기주식',
  insider: '임원·주요주주',
  major_holder: '5% 대량보유',
  periodic: '정기보고서',
  earnings: '실적',
  inquiry: '조회공시',
  capital: '자본 변동',
  owner: '지분·증권',
  clinical: '임상·허가',
  contract: '계약',
  supply: '공급계약',
  other: '기타',
};
export const kindLabel = (k: string | null | undefined): string => (k ? (KIND_LABEL[k] ?? k) : DASH);

/** 화면 필터에 내놓는 종류(이름표 순서) */
export const KIND_OPTIONS: readonly string[] = Object.keys(KIND_LABEL);

export type Importance = 'critical' | 'high' | 'medium' | 'low';
export const IMPORTANCE_LABEL: Readonly<Record<string, string>> = {
  critical: '매우 중요',
  high: '중요',
  medium: '보통',
  low: '낮음',
};
export function importanceCell(v: string | null | undefined): HTMLSpanElement {
  const k = v ?? 'low';
  const cls = k === 'critical' || k === 'high' ? 'warn' : k === 'low' ? 'dim' : undefined;
  return h('span', cls ? { class: cls } : null, IMPORTANCE_LABEL[k] ?? DASH);
}

export const INSTRUMENT_LABEL: Readonly<Record<string, string>> = {
  cb: 'CB',
  bw: 'BW',
  eb: 'EB',
  paid_in: '유상증자',
  bonus: '무상증자',
};
export const instrumentLabel = (k: string | null | undefined): string => (k ? (INSTRUMENT_LABEL[k] ?? k) : DASH);

export const SCOPE_LABEL: Readonly<Record<string, string>> = {
  consolidated: '연결',
  standalone: '별도',
  subsidiary: '자회사',
};

/** 잠정실적 분류(E 엔진 classify_prelim — 값은 이름 그대로 오고 화면에서만 바꾼다) */
export const SIGNAL_LABEL: Readonly<Record<string, string>> = {
  TURNAROUND: '흑자전환',
  SHOCK: '적자전환',
  TURNAROUND_PARTIAL: '적자 축소',
  YOY_BIG_UP: '큰 폭 증가',
  YOY_UP: '증가',
  YOY_BIG_DOWN: '큰 폭 감소',
  YOY_DOWN: '감소',
  YOY_INLINE: '전년 수준',
  NO_DATA: '비교 불가',
};
// 컨센서스 대비 분류(BEAT·MISS …)는 분기 컨센서스가 없어(KIS 교체 — D-P4-10) 오지 않는다 — 와도 이름 그대로
const SIGNAL_UP = /^(TURNAROUND|YOY_BIG_UP|YOY_UP|BEAT)/;
const SIGNAL_DOWN = /^(SHOCK|YOY_BIG_DOWN|YOY_DOWN|MISS)/;

/** 분류 칸: 이름 + 색(상승 적색·하락 청색 — 국내 관례), 우선순위 1 은 굵게 */
export function signalCell(signal: string | null | undefined, priority?: number | null): HTMLElement | null {
  if (!signal) return null;
  const cls = SIGNAL_UP.test(signal) ? 'up' : SIGNAL_DOWN.test(signal) ? 'down' : 'dim';
  const label = SIGNAL_LABEL[signal] ?? signal;
  return h('span', { class: cls, data: { signal } }, priority === 1 ? h('b', null, label) : label);
}

/** 리비전 신호(K 엔진 revision) */
export const REVISION_LABEL: Readonly<Record<string, string>> = {
  STRONG_UP: '크게 상향',
  UP: '상향',
  NEUTRAL: '유지',
  DOWN: '하향',
  STRONG_DOWN: '크게 하향',
  TURN: '부호 전환',
};
export function revisionCell(sig: string | null | undefined): HTMLElement | null {
  if (!sig) return null;
  const cls = sig.endsWith('UP') ? 'up' : sig.endsWith('DOWN') ? 'down' : sig === 'TURN' ? 'warn' : 'dim';
  return h('span', { class: cls }, REVISION_LABEL[sig] ?? sig);
}

export const METRIC_LABEL: Readonly<Record<string, string>> = {
  revenue: '매출',
  op: '영업이익',
  ni: '순이익',
  eps: 'EPS',
  ebitda: 'EBITDA',
  target_mean: '목표가 평균',
};

export const MARKET_LABEL: Readonly<Record<string, string>> = { KOSPI: '코스피', KOSDAQ: '코스닥', KONEX: '코넥스' };
export const marketLabel = (m: string | null | undefined): string => (m ? (MARKET_LABEL[m] ?? m) : DASH);

// ── 서식 ──────────────────────────────────────────────────

function finite(v: Num): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/** 통화가 원이 아니면 단위에 통화를 붙인다(환산하지 않는다 — §8.4 #19) */
export function isWon(cur: string | null | undefined): boolean {
  return !cur || cur === 'KRW';
}

/** 외국 통화 금액의 화면 단위 — 백만(억 단위면 작은 회사 값이 0.0 으로 보여 0 과 헷갈린다) */
const FX_UNIT = 1e6;

/** 금액 한 칸(원 → 억·조, 외국 통화는 '백만 USD') */
export function money(v: Num, cur?: string | null, signed = false): string {
  if (isWon(cur)) return joEok(v, signed);
  return finite(v) ? `${num(v / FX_UNIT, 1, signed)}백만 ${cur ?? ''}`.trim() : DASH;
}

/** 표 칸(단위는 머리에) — 원 → 억 숫자, 외국 통화 → 백만 숫자 */
export function moneyNum(v: Num, cur?: string | null, signed = false): string {
  if (!finite(v)) return DASH;
  return isWon(cur) ? num(v / 1e8, 0, signed) : num(v / FX_UNIT, 1, signed);
}

/** 표 머리 단위: '억' 또는 '백만 USD' */
export function moneyUnit(cur?: string | null): string {
  return isWon(cur) ? '억' : `백만 ${cur ?? ''}`.trim();
}

/** dart-report 블록 값(이미 억원 실수) */
export function eok1(v: Num, signed = false): string {
  return finite(v) ? num(v, 1, signed) : DASH;
}

/** 주식 수: 1,234,567주 */
export function shares(v: Num, signed = false): string {
  return finite(v) ? `${num(v, 0, signed)}주` : DASH;
}

/** 원/주 가격: 12,345원 */
export function won(v: Num): string {
  return finite(v) ? `${num(v, 0)}원` : DASH;
}

// ── 링크·종목 칸 ──────────────────────────────────────────

/** DART 뷰어 주소만 링크로 그린다(데이터에 다른 주소가 와도 링크로 만들지 않는다) */
export const DART_VIEWER = 'https://dart.fss.or.kr/';

export function dartLink(href: string | null | undefined, text: string): HTMLElement {
  if (typeof href !== 'string' || !href.startsWith(DART_VIEWER)) return h('span', null, text);
  return h('a', { attrs: { href, target: '_blank', rel: 'noopener noreferrer' } }, text);
}

/** 종목 이름 칸 — 코드가 있으면 눌러서 페이지 8 로(onOpen) */
export function stockLink(
  name: string | null | undefined,
  code: string | null | undefined,
  onOpen?: (code: string) => void,
): HTMLElement {
  const label = name ?? code ?? DASH;
  if (!code) return h('span', null, label);
  if (!onOpen) return h('span', null, label, h('small', null, code));
  return h(
    'span',
    null,
    h(
      'button',
      {
        class: 'btn link',
        attrs: { type: 'button', title: '종목 상세' },
        data: { open: code },
        on: {
          click: (e) => {
            e.stopPropagation();
            onOpen(code);
          },
        },
      },
      label,
    ),
    h('small', null, code),
  );
}

/** 작은 꼬리표(관심·정정·추정 등) */
export function tag(text: string, cls?: string, title?: string): HTMLSpanElement {
  return h('span', { class: cls ? `tag ${cls}` : 'tag', attrs: { title } }, text);
}

/** 표 칸 줄임(key = 이름표 — 표 부품은 key 를 그리지 않는다). l = 왼쪽 정렬 */
export function col<T>(label: string, cell: Column<T>['cell'], l?: boolean, cls?: Column<T>['cls']): Column<T> {
  return { key: label, label, cell, align: l ? 'l' : 'r', cls };
}

// ── 공개 파일(X 묶음 모양) ────────────────────────────────

/** columns + rows(배열의 배열) — X 의 공개 표 모양 */
export interface Packed {
  columns: string[];
  rows: unknown[][];
}

/** 배열이 아니면 빈 배열(공개 파일은 형 검사가 없다 — 모양이 틀리면 '없음'으로 그린다) */
export function arr<T>(v: readonly T[] | null | undefined): readonly T[] {
  return Array.isArray(v) ? (v as readonly T[]) : [];
}

/** 표 → 객체 목록. 모양이 틀리면 빈 목록(그리는 쪽이 '없음'으로) */
export function unpack<T>(p: unknown): T[] {
  if (typeof p !== 'object' || p === null) return [];
  const { columns, rows } = p as Partial<Packed>;
  if (!Array.isArray(columns) || !Array.isArray(rows)) return [];
  const out: T[] = [];
  for (const r of rows) {
    if (!Array.isArray(r)) continue;
    const o: Record<string, unknown> = {};
    columns.forEach((c, i) => {
      o[c] = r[i] ?? null;
    });
    out.push(o as T);
  }
  return out;
}

/** corp_index.json 한 줄 */
export interface CorpRow {
  code: string;
  name: string;
  market: string | null;
  acc_mt: number | null;
  /** fin/<코드>.json 이 있다 */
  fin: boolean;
  /** reports/<코드>.json 이 있다(공개 데모) */
  report: boolean;
}

export interface PubWindow {
  days: number;
  net_shares: number | null;
  buy_shares: number | null;
  sell_shares: number | null;
  n_reports: number | null;
  n_reporters: number | null;
  ratio_pct: number | null;
  reason_known_pct: number | null;
  quality: Quality;
}

export interface PubSchedule {
  corp_code: string;
  period_end: string;
  kind: 'pre_announce' | 'statutory';
  planned_date: string;
  fiscal_year: number;
  fiscal_q: number;
  rcept_no: string | null;
  note: string | null;
  quality: Quality;
  stock_code?: string | null;
  corp_name?: string | null;
  market?: string | null;
}

/** fin/<코드>.json 의 data(X — 공개 칸만) */
export interface PubFin {
  code: string;
  corp_code: string;
  corp_name: string | null;
  market: string | null;
  acc_mt: number | null;
  induty_code: string | null;
  financials: null | {
    as_of: string;
    quality: Quality;
    notes: string[];
    fs_div: 'CFS' | 'OFS' | null;
    currency: string | null;
    tier: 'main' | 'full' | null;
    quarters: Packed;
    annual: Packed;
    ratios: S['RatioView'] | null;
    checks: S['FinChecks'] | null;
  };
  segments: null | { year: number; rcept_no: string | null; rows: S['SegmentView'][]; quality: Quality; notes: string[] };
  /** 요약의 공개 칸(로그인 칸 — 상장주식수 비율·금액·ITM — 은 없다). timeline 은 X 가 싣게 되면 그린다 */
  overhang: null | (Omit<S['OverhangSummaryView'], 'listed_shares' | 'login_ratio_pct' | 'amount' | 'itm_shares'> & { timeline?: S['TimelineItem'][] });
  insider: null | { label: string; as_of: string; windows: PubWindow[] };
  filings: Packed;
  dart_viewer: string;
  schedule?: PubSchedule[] | null;
  report: boolean;
}

/** fin/<코드>.json 의 공시 한 줄 */
export interface PubFiling {
  rcept_no: string;
  rcept_dt: string;
  title: string;
  kind: string;
  is_correction: boolean;
  kw_score: number | null;
  quality: Quality;
}

// ── 탭 문맥 ───────────────────────────────────────────────

/** 탭 모듈이 받는 것(종목 하나) */
export interface TabCtx {
  readonly tier: Tier;
  readonly code: string;
  readonly source: DataSource;
  readonly now: () => Date;
  readonly tradingview?: AppContext['tradingview'];
  /** 같은 종목 안에서 같은 요청(키 + 쿼리)은 한 번 — 탭끼리 나눠 쓴다. 실패는 기억하지 않는다 */
  get<T>(key: string, params?: Params): Promise<Envelope<T>>;
  /** 공개판: 이 종목의 공개 재무 파일(없으면 null — '공시 재무 없음'). 로그인판은 늘 null(요청 없음) */
  pub(): Promise<Envelope<PubFin> | null>;
  /** 공개판: corp_index 의 이 종목 줄(없으면 null) */
  corp(): Promise<CorpRow | null>;
  /** 종목을 바꾼다(페이지 8 안 — 예: 우선주 → 보통주) */
  open(code: string): void;
}

/** 요청 묶음 — 같은 키·쿼리는 같은 약속을 돌려준다(실패하면 지워서 다음에 다시) */
export function memoGet(source: DataSource): TabCtx['get'] {
  const m = new Map<string, Promise<Envelope<unknown>>>();
  return <T>(key: string, params?: Params): Promise<Envelope<T>> => {
    const k = key + queryString(params);
    let p = m.get(k);
    if (!p) {
      p = source.get<unknown>(key, params);
      m.set(k, p);
      p.catch(() => {
        if (m.get(k) === p) m.delete(k);
      });
    }
    return p as Promise<Envelope<T>>;
  };
}

/** 패널 안 한 줄 오류 문구 — 404 는 서버 안내(no_data) 또는 '아직 없음 — <작업>', 그 밖은 상태 코드만(본문 없이) */
export function errLine(err: unknown, hint: string): string {
  if (err instanceof DataError && err.status === 404) return err.serverMessage ?? `아직 없음 — ${hint}`;
  return errorText(err);
}

/** 404(파일 없음·no_data)인가 */
export function isNotFound(err: unknown): boolean {
  return err instanceof DataError && err.status === 404;
}

// ── 탭 안 격자·패널 ───────────────────────────────────────

/** 탭 패널 안에 12칸 격자를 붙이고 패널들을 넣는다 */
export function tabGrid(el: HTMLElement, ...panels: Panel[]): HTMLDivElement {
  const g = h('div', { class: 'grid' }, panels.map((p) => p.el));
  el.append(g);
  return g;
}

/** 공개판: 공시 재무 파일이 없을 때 패널 문구 */
export const NO_PUB_FIN = '공시 재무 없음(공개 재무 파일 없음)';

/** 공개 재무 파일이 없을 때의 오류 — fill() 이 404 안내 줄(NO_PUB_FIN)로 그린다(오류 줄이 아니다) */
export function noPubFin(): DataError {
  return new DataError(NO_PUB_FIN, 404, NO_PUB_FIN);
}

/** 패널 만들기(등급은 tier 그대로) */
export function mkPanel(t: Pick<TabCtx, 'tier'>, o: PanelOptions): Panel {
  return panel(t.tier, o);
}

// ── 콤보 차트(막대 + 선) — 분기 실적(재무 탭·리포트 탭) ───────

export interface ComboOptions {
  label: string;
  categories: readonly string[];
  /** 막대(금액 — 양수만 그림, 음수·없는 값은 비움) */
  bars: readonly Num[];
  /** 보조축 선(% — 음수 가능) */
  line: readonly Num[];
  barName: string;
}

/** 막대 최대 폭(viewBox 단위) */
const BAR_MAX = 40;

/** 막대 + 보조축 선(OPM %). 값이 없으면 그 자리를 비운다(0 으로 그리지 않는다). 칸은 categories 전부 —
 *  값이 없는 분기(차분 불가)도 자리를 두고, 아래 범위 글자는 첫 칸 ~ 끝 칸이다. */
export function comboChart(o: ComboOptions): SVGSVGElement {
  const W = 640;
  const plotH = 144;
  const box = svgBox(`0 0 ${W} 180`, o.label);
  // 칸 폭은 분기 수로 나누되 막대는 BAR_MAX 를 넘지 않는다 — 값이 하나뿐이면 막대 하나가 그림 전체를
  // 덮어 빈 상자처럼 보였다(2026-10-21). 맨 위 1/10 은 비운다(꼭대기 % 글자와 겹치지 않게)
  const step = (W - 48) / Math.max(o.categories.length, 1);
  const cx = (i: number): number => 8 + i * step + step / 2;
  const bw = Math.min(step * 0.64, BAR_MAX);
  const bMax = Math.max(1, ...o.bars.filter(finite)) * 1.1;
  s('line', { x1: 8, x2: W - 40, y1: 14 + plotH, y2: 14 + plotH, stroke: 'var(--line)', 'stroke-width': 1 }, box);
  o.bars.forEach((v, i) => {
    if (finite(v) && v > 0) s('rect', { x: cx(i) - bw / 2, y: 14 + plotH * (1 - v / bMax), width: bw, height: (v / bMax) * plotH, fill: 'var(--line)' }, box);
  });
  const lv = o.line.filter(finite);
  const lo = Math.min(0, ...lv);
  const hi = Math.max(0, ...lv);
  const y = (v: number): number => 14 + (1 - (v - lo) / (hi - lo || 1)) * plotH;
  let pts: string[] = [];
  const out = (): void => {
    if (pts.length) s('polyline', { points: pts.join(' '), fill: 'none', stroke: 'var(--accent)', 'stroke-width': 1.6 }, box);
    pts = [];
  };
  o.line.forEach((v, i) => (finite(v) ? pts.push(`${cx(i).toFixed(1)},${y(v).toFixed(1)}`) : out()));
  out();
  // 점도 찍는다 — 앞뒤가 비어 혼자인 값은 선이 그려지지 않는다
  o.line.forEach((v, i) => {
    if (finite(v)) s('circle', { cx: cx(i).toFixed(1), cy: y(v).toFixed(1), r: 2.4, fill: 'var(--accent)' }, box);
  });
  if (lv.length) s('text', { x: W - 36, y: y(hi) + 4 }, box, `${num(hi, 1)}%`);
  if (lo < 0) s('text', { x: W - 36, y: y(lo) + 4 }, box, `${num(lo, 1)}%`);
  s('text', { x: 8, y: 10 }, box, `막대 = ${o.barName}, 선 = OPM(%)`);
  s('text', { x: 8, y: 174 }, box, `${o.categories[0] ?? ''} ~ ${o.categories.at(-1) ?? ''}`);
  return box;
}

// ── 잠정실적·실적 일정 표(페이지 8 '공시·지분'·'개요', 페이지 5) ─────

/** 종목 칸(페이지 5 — 누르면 페이지 8, 관심종목 꼬리표) */
export function stockCol<T extends { corp_name?: string | null; stock_code?: string | null; is_watch?: boolean }>(
  onOpen: (code: string) => void,
): Column<T> {
  return col('종목', (r) => h('span', null, stockLink(r.corp_name, r.stock_code, onOpen), r.is_watch ? tag('관심', 'up') : null), true, () => 'nm');
}

/** 잠정실적 칸. withSignal = 분류(YoY) 칸(로그인 — 공개 파일에는 분류가 없다), onOpen 이 있으면 종목 칸(페이지 5) */
export function prelimColumns(withSignal: boolean, onOpen?: (code: string) => void): Column<S['PrelimView']>[] {
  type P = S['PrelimView'];
  const yoy = (label: string, get: (r: P) => number | null): Column<P> => col(label, (r) => pct(get(r), 1), false, (r) => signClass(get(r)));
  return [
    col<P>('접수일', (r) => shortDate(r.rcept_dt)),
    ...(onOpen ? [stockCol<P>(onOpen)] : []),
    col<P>(
      '기간',
      (r) =>
        h(
          'span',
          null,
          dartLink(r.link, r.label),
          ` ${SCOPE_LABEL[r.scope] ?? r.scope}`,
          r.is_correction ? tag('정정', 'warn') : null,
          r.period_inferred ? tag('기간 추정', 'dim') : null,
        ),
      true,
    ),
    col<P>('매출', (r) => money(r.revenue_cur, r.currency)),
    yoy('매출 YoY', (r) => r.rev_yoy),
    col<P>('영업이익', (r) => money(r.op_cur, r.currency, true), false, (r) => signClass(r.op_cur)),
    yoy('영업익 YoY', (r) => r.op_yoy),
    yoy('영업익 QoQ', (r) => r.op_qoq),
    ...(withSignal ? [col<P>('분류(YoY)', (r) => signalCell(r.signal, r.priority), true)] : []),
  ];
}

/** 실적 일정 한 줄(로그인 ScheduleView·공개 파일 모양 공통) */
export type ScheduleLike = Pick<PubSchedule, 'period_end' | 'kind' | 'planned_date' | 'fiscal_year' | 'fiscal_q' | 'rcept_no' | 'note' | 'quality'> & {
  stock_code?: string | null;
  corp_name?: string | null;
  is_watch?: boolean;
};

export function scheduleColumns(onOpen?: (code: string) => void): Column<ScheduleLike>[] {
  return [
    col<ScheduleLike>('예정일', (r) => qValue(kstDate(r.planned_date), r.quality, null, undefined, EST_LABEL)),
    ...(onOpen ? [stockCol<ScheduleLike>(onOpen)] : []),
    col<ScheduleLike>('기간', (r) => `${r.fiscal_year} ${r.fiscal_q}Q · ${kstDate(r.period_end)} 결산`, true),
    col<ScheduleLike>(
      '구분',
      (r) => (r.kind === 'pre_announce' ? dartLink(r.rcept_no ? `${DART_VIEWER}dsaf001/main.do?rcpNo=${r.rcept_no}` : null, '실적 예고') : '법정 기한(추정)'),
      true,
    ),
    col<ScheduleLike>('비고', (r) => r.note, true),
  ];
}

// ── 공시 줄(로그인 DisclosureView·공개 파일 줄 공통) ────────

export interface DiscRow {
  rcept_no: string;
  rcept_dt: string;
  title: string;
  kind: string;
  is_correction: boolean;
  kw_score: number | null;
  link: string;
  /** 로그인만(키워드 + 관심종목 + 시총 가산) */
  importance?: string | null;
}

/** 공개 fin/<코드>.json 의 공시 → 공통 줄(링크 = 뷰어 + 접수번호) */
export function pubDiscRows(d: PubFin): DiscRow[] {
  return unpack<PubFiling>(d.filings).map((r) => ({ ...r, link: `${d.dart_viewer}${r.rcept_no}` }));
}

export function discColumns(login: boolean): Column<DiscRow>[] {
  return [
    col<DiscRow>('접수일', (r) => kstDate(r.rcept_dt)),
    col<DiscRow>('제목', (r) => h('span', null, dartLink(r.link, r.title), r.is_correction ? tag('정정', 'warn') : null), true),
    col<DiscRow>('종류', (r) => kindLabel(r.kind), true),
    login ? col<DiscRow>('중요도', (r) => importanceCell(r.importance), true) : col<DiscRow>('키워드 점수', (r) => num(r.kw_score)),
  ];
}

/**
 * 같은 대상(회사·기간·연결/별도)의 잠정실적은 **가장 늦은 접수번호**만(설계 §4.1 — 정정공시는 원 공시를 대체).
 * 줄 순서는 그대로, 대체된 줄 수를 함께 돌려준다(표 제목에 적는다).
 */
export function latestPrelims<T extends Pick<S['PrelimView'], 'rcept_no' | 'corp_code' | 'period' | 'scope'>>(
  rows: readonly T[],
): { rows: T[]; superseded: number } {
  const key = (r: T): string => `${r.corp_code}|${r.period}|${r.scope}`;
  const last = new Map<string, string>();
  for (const r of rows) {
    const k = key(r);
    const cur = last.get(k);
    if (cur === undefined || r.rcept_no > cur) last.set(k, r.rcept_no);
  }
  const kept = rows.filter((r) => last.get(key(r)) === r.rcept_no);
  return { rows: kept, superseded: rows.length - kept.length };
}

/** 표 제목 꼬리: 정정으로 대체된 원 공시 수 */
export function supersededNote(n: number): string {
  return n > 0 ? ` · 정정 전 원 공시 ${num(n)}건 숨김` : '';
}

/** 공개: earnings_prelim.json(최근 14일, 전 종목)에서 이 종목 잠정실적 */
export async function pubPrelims(t: Pick<TabCtx, 'get' | 'code'>): Promise<S['PrelimView'][]> {
  const env = await t.get<{ rows?: S['PrelimView'][] }>('earnings_prelim');
  return arr(env.data.rows).filter((r) => r.stock_code === t.code);
}
