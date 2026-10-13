// 페이지 2 '간밤 미국 신고가' 패널(docs/p5_design.md §1.6·§6, metrics §23 — 로그인).
//
// - 원천 Nasdaq·Stooq·Yahoo(약관 미확인 → 로그인). 공개판은 자물쇠만(요청 없음 — p2_board 가 막는다).
// - **미국 거래일(뉴욕 날짜)** 과 출처를 머리에 적는다. 한국 날짜로 바꾸지 않는다(간밤 = 전날 뉴욕 세션).
// - 신고가 = board/us 정의(252봉 — ADR 0001 Q10, 국장 3축(ADR 0017)과 다르다). 종가·고가 두 기준.
// - 이력이 252봉 미만이라 판정을 보류한 종목은 숨기지 않고 수를 적는다(`pending`).
// - 금액은 달러: 시총 $B·$M. 한눈에 문장은 서버 산출 그대로(아침 브리핑 ① 과 같은 문장 — 계산하지 않는다).
import type { components } from '../../api/types.gen';
import type { PageContext } from '../../app/types';
import type { Envelope } from '../../data/source';
import { h } from '../../ui/dom';
import { DASH, num, pct, signClass } from '../../ui/fmt';
import type { Panel } from '../../ui/panel';
import { type Option, seg } from '../../ui/seg';
import { type Column, table } from '../../ui/table';
import { fill, filters, nameCell } from '../p1_market';

type S = components['schemas'];
export type UsBoard = S['UsBoard'];
type Row = S['UsBoardRow'];
type Sector = S['UsSectorRow'];
export type UsBasis = 'close' | 'high';

export const US_TITLE = '간밤 미국 신고가';
export const US_HINT = 'us.eod 06:10(미국 마감 16:10 ET)';
export const BASES: readonly Option<UsBasis>[] = [
  { value: 'close', label: '종가 기준' },
  { value: 'high', label: '고가 기준' },
];

/** 달러 금액: $1.2T · $345B · $726M (없으면 —) */
export function usd(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH;
  const a = Math.abs(v);
  for (const [unit, div] of [
    ['T', 1e12],
    ['B', 1e9],
    ['M', 1e6],
  ] as const) {
    if (a >= div) return `$${num(v / div, a / div >= 100 ? 0 : 1)}${unit}`;
  }
  return `$${num(v, 0)}`;
}

/** 'YYYY-MM-DD' → 'M/D(요일)' — 뉴욕 날짜 그대로(시간대 변환 없음) */
export function usDay(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  if (!y || !m || !d) return iso;
  const wd = '일월화수목금토'[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
  return `${m}/${d}(${wd})`;
}

const chg = (r: Row): HTMLElement => h('span', { class: signClass(r.chg_pct) }, pct(r.chg_pct, 1));

export const NEWHIGH_COLUMNS: readonly Column<Row>[] = [
  { key: 'sym', label: '종목', align: 'l', cls: () => 'nm', cell: (r) => nameCell(r.name, r.symbol) },
  { key: 'label', label: '구분', align: 'l', cell: (r) => r.label ?? DASH },
  { key: 'sector', label: '섹터', align: 'l', cell: (r) => r.sector ?? DASH },
  { key: 'close', label: '종가($)', cell: (r) => num(r.close, 2) },
  { key: 'chg', label: '등락', cell: chg },
  { key: 'mcap', label: '시총', cell: (r) => usd(r.market_cap) },
];

export const SECTOR_COLUMNS: readonly Column<Sector>[] = [
  { key: 'sector', label: '섹터', align: 'l', cell: (s) => s.sector },
  { key: 'n', label: '종목 수', cell: (s) => num(s.n) },
  { key: 'nh', label: '신고가', cell: (s) => num(s.newhigh), cls: (s) => (s.newhigh > 0 ? 'up' : 'dim') },
  {
    key: 'ret',
    label: '중앙 등락',
    cell: (s) => h('span', { class: signClass(s.ret_pct) }, pct(s.ret_pct, 1)),
  },
];

/** 봉투 → 패널 본문. 돌려주는 값 = 뺀 행 수(없음 → 0). */
export function drawUs(p: Panel, env: Envelope<UsBoard>): number {
  const d = env.data;
  const basis = d.basis === 'high' ? '고가' : '종가';
  p.body.append(
    h(
      'p',
      { class: 'us-head' },
      h('b', null, `미국 ${usDay(d.us_trade_date)} 마감`),
      ` · ${basis} 기준 · 뉴욕 날짜`,
      d.pending > 0 ? h('span', { class: 'dim' }, ` · 이력 부족 보류 ${num(d.pending)}종목`) : null,
    ),
  );
  if (d.summary_lines.length > 0) {
    p.body.append(h('ul', { class: 'us-summary' }, d.summary_lines.map((s) => h('li', null, s))));
  }
  p.body.append(
    table<Row>({
      caption: `신고가 ${num(d.newhigh.length)}종목 · 52주 먼저, 거래대금 순`,
      columns: NEWHIGH_COLUMNS,
      rows: d.newhigh,
      rowKey: (r) => r.symbol,
      pageSize: 20,
      empty: '신고가 종목 없음',
    }).el,
  );
  const secs = d.sectors.filter((s) => s.newhigh > 0);
  p.body.append(
    table<Sector>({
      caption: '섹터별 신고가(신고가 있는 섹터)',
      columns: SECTOR_COLUMNS,
      rows: secs,
      rowKey: (s) => s.sector,
      empty: '신고가 섹터 없음',
    }).el,
  );
  if (d.mega_movers.length > 0) {
    p.body.append(
      table<Row>({
        caption: '대형주($100B+) 상승·하락',
        columns: NEWHIGH_COLUMNS.filter((c) => c.key !== 'label'),
        rows: d.mega_movers,
        rowKey: (r) => r.symbol,
      }).el,
    );
  }
  p.setDef('신고가 = 직전 252거래일(당일 제외) 최고가 갱신(52주) · 직전 60거래일(신고가). 국장 3축과 다른 정의.');
  return 0;
}

/** 패널에 기준 토글을 달고, 불러오기 함수를 돌려준다(p2_board 의 주기 갱신이 부른다). */
export function mountUs(ctx: PageContext, p: Panel): () => Promise<void> {
  const q = { basis: 'close' as UsBasis };
  const load = (): Promise<void> =>
    fill(p, () => ctx.source.get<UsBoard>('board/us', { basis: q.basis }), (env) => drawUs(p, env), US_HINT);
  filters(
    p,
    seg<UsBasis>({
      label: '미국 신고가 기준',
      options: BASES,
      value: q.basis,
      onChange: (v) => {
        q.basis = v;
        void load();
      },
    }).el,
  );
  return load;
}
