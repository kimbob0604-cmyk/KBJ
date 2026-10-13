// 페이지 7 '속보' 탭(docs/p6_design.md §1.3·§6, metrics §25 — 묶음 FLASH). 관세청 10일 단위 잠정치 4종(수출·수입 × 품목·국가).
//
// - 구간 = 그 달 1일부터의 누계(1~10일·1~20일·1~말일). YoY = 전년 같은 구간 대비(열 이름이 전년과 다르면 없음 + 사유).
// - 표 = 최신 구간의 열 00~10(전체·10대): 최신 금액·발표 당시 금액·YoY·10대 기여도(Δ항목 ÷ Δ전체). 정정된 칸은 '정정' 꼬리표.
// - 막대 = 구간별 전체 YoY(시간 순), 선 = 구간 종류(1~10일·1~20일·월 전체)별 전체 YoY. 금액은 억 달러(원화 환산 없음).
// - 데이터 키: 로그인 'trade/flash' + {kind, months}(API), 공개 'trade/flash'(공개 파일 — 4종을 한 파일에, 최근 6개월).
// - 실패·'아직 없음'은 이 탭 안 한 줄(다른 탭은 계속 — 절대 규칙 4). CSV 는 지금 보이는 표 그대로(ui/csv.ts).
import type { Envelope } from '../../data/source';
import { barsChart } from '../../ui/bars';
import { type CsvColumn, downloadCsv, toCsv } from '../../ui/csv';
import { h, replace } from '../../ui/dom';
import { DASH, num, pct, signClass } from '../../ui/fmt';
import { lineChart } from '../../ui/line';
import { seg } from '../../ui/seg';
import { type Column, table } from '../../ui/table';
import { failNote, TRADE_HINT, TRADE_SOURCE, type TabCtx, type TradeFlash, tradePanel } from './common';

export const TITLE = '수출 속보';
/** 로그인 API 기간(개월) — 막대·선 */
export const LOGIN_MONTHS = 24;

export type FlashKind = TradeFlash['kind'];
type Segment = TradeFlash['segments'][number];
type Cell = Segment['cells'][number];

export const KINDS: readonly { value: FlashKind; label: string }[] = [
  { value: 'exp_item', label: '수출 품목' },
  { value: 'exp_country', label: '수출 국가' },
  { value: 'imp_item', label: '수입 품목' },
  { value: 'imp_country', label: '수입 국가' },
];

/** 공개 파일 `trade/flash.json` 의 data(4종 — 각 TradeFlash 와 같은 모양) */
export interface PublicFlash {
  kinds: TradeFlash[];
}

/** '01~20' → '1~20일' */
export function spanLabel(span: string): string {
  const [a, b] = span.split('~');
  const x = Number(a);
  const y = Number(b);
  return a && b && Number.isFinite(x) && Number.isFinite(y) ? `${String(x)}~${String(y)}일` : span;
}

/** 'YYYY-MM' + 구간 → '2026-12 1~10일' */
export function segLabel(s: Pick<Segment, 'ym' | 'span_text'>): string {
  return `${s.ym} ${spanLabel(s.span_text)}`;
}

/** 달러 → 억 달러(소수 1자리). 없으면 '—' */
export function usdEok(v: number | null | undefined): string {
  return typeof v === 'number' && Number.isFinite(v) ? `${num(v / 1e8, 1)}억 달러` : DASH;
}

/** 비율(0.123) → '+12.3%' */
export function ratioPct(v: number | null | undefined, digits = 1): string {
  return typeof v === 'number' && Number.isFinite(v) ? pct(v * 100, digits, true) : DASH;
}

/** 그 종류의 속보 봉투 — 등급마다 키만 다르다(공개판은 로그인 API 를 부르지 않는다). 공개 파일은 4종이 한 파일이라
 *  `file` 로 한 번 받은 것을 다시 쓴다 */
export async function loadFlash(
  t: TabCtx,
  kind: FlashKind,
  file: () => Promise<Envelope<PublicFlash>> = () => t.source.get<PublicFlash>('trade/flash'),
): Promise<Envelope<TradeFlash>> {
  if (t.tier === 'login')
    return t.source.get<TradeFlash>('trade/flash', {
      kind,
      months: LOGIN_MONTHS,
    });
  const env = await file();
  const one = Array.isArray(env.data.kinds) ? env.data.kinds.find((k) => k.kind === kind) : undefined;
  if (!one) throw new Error(`공개 파일에 ${kind} 없음`);
  return { ...env, data: one };
}

const total = (s: Segment): Cell | undefined => s.cells.find((c) => c.col_no === 0);

const yoyPct = (c: Cell | undefined): number | null => (c?.yoy === null || c?.yoy === undefined ? null : c.yoy * 100);

/** 표 행(CSV 와 같은 행) */
export interface FlashRow {
  col_no: number;
  name: string;
  usd: number | null;
  first_usd: number | null;
  yoy: number | null;
  contribution: number | null;
  note: string;
}

export function rowsOf(s: Segment): FlashRow[] {
  return [...s.cells]
    .sort((a, b) => a.col_no - b.col_no)
    .map((c) => ({
      col_no: c.col_no,
      name: c.name,
      usd: c.usd,
      first_usd: c.first_usd,
      yoy: c.yoy,
      contribution: c.col_no === 0 ? null : (c.contribution ?? null),
      note: [c.first_usd !== c.usd ? '정정' : '', c.yoy_reason ?? ''].filter(Boolean).join(' · '),
    }));
}

const COLUMNS: readonly Column<FlashRow>[] = [
  {
    key: 'name',
    label: '열',
    align: 'l',
    cell: (r) => (r.col_no === 0 ? `${r.name}(합계)` : r.name),
  },
  { key: 'usd', label: '최신', cell: (r) => usdEok(r.usd) },
  { key: 'first', label: '발표 당시', cell: (r) => usdEok(r.first_usd) },
  {
    key: 'yoy',
    label: '전년 대비',
    cell: (r) => ratioPct(r.yoy),
    cls: (r) => signClass(r.yoy),
  },
  {
    key: 'contrib',
    label: '기여도',
    cell: (r) => (r.col_no === 0 ? null : ratioPct(r.contribution)),
  },
  { key: 'note', label: '비고', align: 'l', cell: (r) => r.note || null },
];

export const CSV_COLUMNS: readonly CsvColumn<FlashRow>[] = [
  { label: '열 번호', value: 'col_no' },
  { label: '이름', value: 'name' },
  { label: '최신(달러)', value: 'usd' },
  { label: '발표 당시(달러)', value: 'first_usd' },
  { label: '전년 대비', value: 'yoy' },
  { label: '기여도', value: 'contribution' },
  { label: '비고', value: 'note' },
];

/** 구간 종류별(1~10일·1~20일·월 전체) 전체 YoY(%) 선 — 달(오래된 것부터) × 3 */
export function yoyLines(segs: readonly Segment[]): {
  x: string[];
  series: { label: string; values: (number | null)[] }[];
} {
  const months = [...new Set(segs.map((s) => s.ym))].sort();
  const pick = (ym: string, n: number): number | null => {
    const s = segs.find((x) => x.ym === ym && x.seg === n);
    return s ? yoyPct(total(s)) : null;
  };
  return {
    x: months,
    series: [
      { label: '1~10일', values: months.map((m) => pick(m, 1)) },
      { label: '1~20일', values: months.map((m) => pick(m, 2)) },
      { label: '월 전체', values: months.map((m) => pick(m, 3)) },
    ],
  };
}

/** CSV 글자(BOM·이스케이프는 ui/csv.ts) — 지금 보이는 표 그대로 */
export function flashCsv(env: Envelope<TradeFlash>, kindLabel: string): string | null {
  const last = env.data.segments[0];
  if (!last) return null;
  return toCsv(rowsOf(last), CSV_COLUMNS, {
    meta: [
      ['출처', TRADE_SOURCE],
      ['구간', segLabel(last)],
      ['종류', kindLabel],
      ['단위', '미화 달러(잠정)'],
    ],
  });
}

function draw(t: TabCtx, body: HTMLElement, env: Envelope<TradeFlash>, kindLabel: string): void {
  const segs = env.data.segments;
  const last = segs[0];
  if (!last) {
    replace(body, h('p', { class: 'msg' }, `아직 없음 — ${TRADE_HINT.flash}`));
    return;
  }
  const tot = total(last);
  const rows = rowsOf(last);
  const chrono = [...segs].reverse();
  const csvBtn = h(
    'button',
    {
      class: 'btn csv',
      attrs: { type: 'button' },
      on: {
        click: () => {
          const text = flashCsv(env, kindLabel);
          if (text !== null) downloadCsv(`trade_flash_${env.data.kind}_${last.ym}_${String(last.seg)}`, text);
        },
      },
    },
    'CSV',
  );
  const lines = yoyLines(segs);
  replace(
    body,
    h(
      'p',
      { class: 'flash-head' },
      h('b', {}, `${kindLabel} ${segLabel(last)}`),
      ` 전체 ${usdEok(tot?.usd)} · 전년 대비 `,
      h('span', { class: signClass(tot?.yoy) }, ratioPct(tot?.yoy)),
      tot && tot.first_usd !== tot.usd ? ` (발표 당시 ${usdEok(tot.first_usd)})` : '',
      tot?.yoy_reason ? ` — ${tot.yoy_reason}` : '',
    ),
    // 두 차트는 반씩 나란히(휴대폰 폭에서는 위아래) — 전체 폭이면 viewBox 가 커져 글자·높이가 지나치게 커진다
    h(
      'div',
      { class: 'grid' },
      h(
        'div',
        { class: 'span-6' },
        barsChart({
          label: `${kindLabel} 구간별 전체 전년 대비(%)`,
          items: chrono.map((s) => {
            const c = total(s);
            return { label: segLabel(s), value: yoyPct(c), detail: usdEok(c?.usd) };
          }),
          format: (v) => pct(v, 1, true),
        }),
      ),
      h(
        'div',
        { class: 'span-6' },
        lineChart({
          label: `${kindLabel} 구간 종류별 전체 전년 대비(%)`,
          x: lines.x,
          series: lines.series,
          zero: true,
          format: (v) => pct(v, 1, true),
        }).el,
      ),
    ),
    h('div', { class: 'tbl-head' }, h('span', {}, `${segLabel(last)} 열 00~10`), csvBtn),
    table<FlashRow>({
      caption: `${kindLabel} ${segLabel(last)}`,
      columns: COLUMNS,
      rows,
      rowKey: (r) => String(r.col_no),
    }).el,
    t.tier === 'public' ? h('p', { class: 'def' }, '공개판은 최근 6개월 — 더 긴 기간은 로그인판') : null,
  );
}

export async function mount(t: TabCtx, el: HTMLElement): Promise<void> {
  const p = tradePanel(t, { title: TITLE });
  p.setDef('관세청 10일 단위 잠정치 — 그 달 1일부터의 누계. 전년 대비 = 전년 같은 구간, 기여도 = Δ항목 ÷ Δ전체.');
  el.append(h('div', { class: 'grid' }, p.el));
  const cache = new Map<FlashKind, Envelope<TradeFlash>>();
  let pub: Promise<Envelope<PublicFlash>> | null = null;
  const file = (): Promise<Envelope<PublicFlash>> => {
    pub ??= t.source.get<PublicFlash>('trade/flash');
    pub.catch(() => {
      pub = null; // 실패는 다시 고를 때 다시 받는다
    });
    return pub;
  };
  const body = h('div', { class: 'flash-body' });
  const show = async (kind: FlashKind): Promise<void> => {
    const label = KINDS.find((k) => k.value === kind)?.label ?? kind;
    try {
      let env = cache.get(kind);
      if (!env) {
        env = await loadFlash(t, kind, file);
        cache.set(kind, env);
      }
      p.setSource(env);
      draw(t, body, env, label);
    } catch (err) {
      replace(body, failNote(err, TRADE_HINT.flash));
    }
  };
  const pick = seg<FlashKind>({
    label: '속보 종류',
    options: KINDS,
    value: 'exp_item',
    onChange: (v) => void show(v),
  });
  p.body.append(pick.el, body);
  await show('exp_item');
}
