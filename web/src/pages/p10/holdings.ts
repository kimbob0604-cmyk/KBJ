// 페이지 10 보유 탐색(docs/p5_design.md §4.4 — 로그인): ETF → 구성종목(마지막 PDF 기준일·비중·수량·변동),
// 종목 → 담은 ETF(비중 상위·액티브 여부). 운용사 PDF 기준일이 다르면 그대로 보인다(섞지 않는다).
// 페이지 8 '담은 ETF' 모듈(p8/etf_holders.ts)도 drawHolders 를 같이 쓴다.
import type { components } from '../../api/types.gen';
import type { Envelope } from '../../data/source';
import { h } from '../../ui/dom';
import { eok, num, pct, shortDate } from '../../ui/fmt';
import type { Panel } from '../../ui/panel';
import type { Option } from '../../ui/seg';
import { type Column, table } from '../../ui/table';
import { nameCell } from '../p1_market';

type S = components['schemas'];
export type Holdings = S['EtfHoldings'];
export type Holders = S['EtfHolders'];
type HoldRow = S['EtfHoldingView'];
type HolderRow = S['EtfHolderView'];
export type Mode = 'fund' | 'stock';

export const MODES: readonly Option<Mode>[] = [
  { value: 'fund', label: 'ETF → 구성종목' },
  { value: 'stock', label: '종목 → 담은 ETF' },
];
/** 상장 코드·종목 코드(6자리 영숫자 — API 경로 규칙과 같다) */
export const CODE_RE = /^[0-9A-Z]{6}$/;

export const CHANGE_LABEL: Readonly<Record<string, string>> = {
  NEW: '신규 편입',
  IN10: 'TOP10 진입',
  ADD: '수량 확대',
  CUT: '수량 축소',
};

export function normalizeCode(v: string): string | null {
  const c = v.trim().toUpperCase();
  return CODE_RE.test(c) ? c : null;
}

function changeCell(r: HoldRow): HTMLElement | string | null {
  if (!r.change) return null;
  return h('span', { class: `tag ${r.change === 'CUT' ? 'down' : 'up'}` }, CHANGE_LABEL[r.change] ?? r.change);
}

export const HOLDING_COLUMNS: readonly Column<HoldRow>[] = [
  { key: 'stock', label: '종목', align: 'l', cls: () => 'nm', cell: (r) => nameCell(r.name, r.code) },
  { key: 'wt', label: '비중', cell: (r) => pct(r.wt, 2, false) },
  { key: 'qty', label: '수량', cell: (r) => num(r.qty) },
  { key: 'val', label: '평가금액', cell: (r) => eok(r.val) },
  { key: 'chg', label: '변동', align: 'l', cell: changeCell },
];

export const HOLDER_COLUMNS: readonly Column<HolderRow>[] = [
  {
    key: 'etf',
    label: 'ETF',
    align: 'l',
    cls: () => 'nm',
    cell: (r) =>
      h(
        'span',
        null,
        r.etf_name ?? r.etf_code ?? r.fund_id,
        r.is_active ? h('span', { class: 'tag' }, '액티브') : null,
        r.etf_code ? h('small', null, r.etf_code) : null,
      ),
  },
  { key: 'wt', label: '비중', cell: (r) => pct(r.wt, 2, false) },
  { key: 'qty', label: '수량', cell: (r) => num(r.qty) },
  { key: 'asof', label: 'PDF 기준일', cell: (r) => shortDate(r.asof) },
];

export function drawHoldings(p: Panel, env: Envelope<Holdings>): number {
  const d = env.data;
  const changed = d.rows.filter((r) => r.change).length;
  p.body.append(
    table<HoldRow>({
      caption: `${d.etf_name ?? d.fund_id}${d.etf_code ? ` (${d.etf_code})` : ''} · PDF ${shortDate(d.asof)} · ${num(d.rows.length)}종목 · 변동 ${num(changed)}`,
      columns: HOLDING_COLUMNS,
      rows: d.rows,
      rowKey: (r) => r.code,
      pageSize: 30,
      empty: '구성종목 없음',
    }).el,
  );
  return 0;
}

export function drawHolders(p: Panel, env: Envelope<Holders>): number {
  const d = env.data;
  const active = d.rows.filter((r) => r.is_active).length;
  p.body.append(
    table<HolderRow>({
      caption: `${d.name ?? d.code} (${d.code}) · 담은 ETF ${num(d.rows.length)}개 · 액티브 ${num(active)}`,
      columns: HOLDER_COLUMNS,
      rows: d.rows,
      rowKey: (r) => r.fund_id,
      pageSize: 20,
      empty: '이 종목을 담은 ETF 없음(추적 펀드 기준)',
    }).el,
  );
  return 0;
}
