// 페이지 7 수출입 공통(docs/p6_design.md §6 — 웨이브 1 FND 가 모양을 고정, 본문은 FLASH·EXP·MAP).
//
// - 탭 모듈 서명은 하나: `mount(t: TabCtx, el: HTMLElement): Promise<void>` — p7_trade.ts 가 탭이 처음 보일 때 부른다.
//   웨이브 2 는 p7_trade.ts 를 고치지 않는다(탭 틀·import 는 FND 몫).
// - 데이터 키: 로그인 = API 경로('trade/flash'·'trade/items' + 쿼리 …), 공개 = 공개 파일('trade/flash'·'trade/hs2'·
//   'trade/hs/<장>'·'trade/country/<ISO2>'·'trade/region/<hs6>'·'trade/stock/<코드>' — public.export, 같은 모양).
//   공개판은 로그인 API 를 부르지 않는다(키만 다르다 — 이 파일에 API 경로 문자열 없음).
// - 패널마다 출처 '관세청(공공데이터포털)'·기준 월·품질 칩·CSV 버튼(ui/csv.ts). 금액은 달러(원화 환산 없음 — Q8).
// - 페이지 7 은 공개 등급 전부(DATA_TIERS §1) — 자물쇠 없음. HS10 단계는 로그인만('로그인판에서 HS10' 안내 한 줄).
import type { components } from '../../api/types.gen';
import type { DataSource, Tier } from '../../data/source';
import { downloadCsv } from '../../ui/csv';
import { h } from '../../ui/dom';
import { DASH, num, pct } from '../../ui/fmt';
import { failText, isNoDataError, panel, type Panel, type PanelOptions } from '../../ui/panel';

type S = components['schemas'];
export type TradeFlash = S['TradeFlash'];
export type TradeItems = S['TradeItems'];
export type TradeItemSeries = S['TradeItemSeries'];
export type TradeItemCountries = S['TradeItemCountries'];
export type TradeCountries = S['TradeCountries'];
export type TradeCountryItems = S['TradeCountryItems'];
export type TradeRegions = S['TradeRegions'];
export type TradeSurges = S['TradeSurges'];
export type TradeItems104 = S['TradeItems104'];
export type TradeStocks = S['TradeStocks'];
export type TradeMonthly = S['TradeMonthly'];

/** 탭 모듈 문맥(페이지 문맥에서 필요한 것만) */
export interface TabCtx {
  readonly tier: Tier;
  readonly source: DataSource;
  readonly now: () => Date;
}

/** 탭 모듈 서명(웨이브 2 묶음이 지킨다) */
export type TabMount = (t: TabCtx, el: HTMLElement) => Promise<void>;

export const TRADE_SOURCE = '관세청(공공데이터포털)';
/** 빈 리더·수집 전 안내(작업·예정 시각 — 확인 전 실측 번호) */
export const TRADE_HINT = {
  flash: 'trade.customs_tenday 1·11·21일(+2일) 09:00~17:40(확인 전 #6·#17·#55)',
  monthly: 'trade.customs_monthly 14~20일 10:10·15:10(확인 전 #1~#5)',
  derived: 'trade.derive 14~20일 10:40·15:40',
  stocks: 'trade.stock_index 21:00',
} as const;

/** 페이지 7 패널(공개 등급 — 자물쇠 없음). 웨이브 2 전 껍데기는 pending 'P6' 로 그린다 */
/** 불러오기 실패 한 줄 — 404(아직 수집 전)는 안내(msg), 그 밖은 오류(msg err). 삼키지 않는다. */
export function failNote(err: unknown, hint: string): HTMLElement {
  return h('p', { class: isNoDataError(err) ? 'msg' : 'msg err' }, failText(err, hint));
}

export function tradePanel(t: TabCtx, opts: Omit<PanelOptions, 'tier'>): Panel {
  return panel(t.tier, {
    tier: 'public',
    span: 12,
    src: TRADE_SOURCE,
    ...opts,
  });
}

/** 웨이브 1 껍데기 — '준비 중(P6)' 패널 하나를 붙인다(요청 없음) */
export function pendingTab(t: TabCtx, el: HTMLElement, title: string, note: string): Promise<void> {
  el.append(h('div', { class: 'grid' }, tradePanel(t, { title, pending: 'P6', pendingNote: note }).el));
  return Promise.resolve();
}

// ── 탐색 탭 공통(EXP — 품목·국가·지역·급등·104품목) ──────────────────────────────────────────────

export type TradeItemNode = TradeItems['rows'][number];
export type Flow = 'exp' | 'imp';
export const FLOWS: readonly { value: Flow; label: string }[] = [
  { value: 'exp', label: '수출' },
  { value: 'imp', label: '수입' },
];

const fin = (v: number | null | undefined): v is number => typeof v === 'number' && Number.isFinite(v);

/** 달러 → 1억 달러 이상은 '12.3억 달러', 그 밑은 '4,567만 달러'(원화 환산 없음). 없으면 '—' */
export function usd(v: number | null | undefined): string {
  if (!fin(v)) return DASH;
  return Math.abs(v) >= 1e8 ? `${num(v / 1e8, 1)}억 달러` : `${num(v / 1e4)}만 달러`;
}

/** 비율(0.123) → '+12.3%' */
export function ratio(v: number | null | undefined, digits = 1): string {
  return fin(v) ? pct(v * 100, digits, true) : DASH;
}

/** 단가(달러/kg) */
export function perKg(v: number | null | undefined): string {
  return fin(v) ? `${num(v, 2)}$/kg` : DASH;
}

/** 표 머리 줄 — 이름 + CSV 버튼(지금 보이는 표 그대로, ui/csv.ts) */
export function tableHead(label: string, file: string, make: () => string | null): HTMLElement {
  const btn = h(
    'button',
    {
      class: 'btn csv',
      attrs: { type: 'button' },
      on: {
        click: () => {
          const text = make();
          if (text !== null) downloadCsv(file, text);
        },
      },
    },
    'CSV',
  );
  return h('div', { class: 'tbl-head' }, h('span', {}, label), btn);
}

/** CSV 메타 줄(출처·기준·단위) */
export function csvMeta(basis: string): readonly (readonly [string, string])[] {
  return [
    ['출처', TRADE_SOURCE],
    ['기준', basis],
    ['단위', '미화 달러(원화 환산 없음)'],
  ];
}

/** 품목 트리 공개 파일 한 줄(trade/hs2.json·trade/hs/<장>.json — 24개월 배열) */
export interface PubSeries {
  usd: (number | null)[];
  kg: (number | null)[];
  yoy: (number | null)[];
}
export interface PubCode {
  hs: string;
  name: string | null;
  exp: PubSeries;
  imp: PubSeries;
  has_children: boolean;
}
export interface PubTree {
  months: string[];
  hs10_login_only: boolean;
  /** 중량 기준 부적합 HS 접두(config/trade.yaml price_volume.weight_unfit) */
  weight_unfit?: string[];
  codes: PubCode[];
  chapter?: string;
  countries?: Record<string, TradeItemCountries['rows']>;
  regions?: { hs6: string; name: string | null; ym: string; rows: number }[];
}
