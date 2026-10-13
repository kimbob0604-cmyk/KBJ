// 페이지 10 액티브 동시 편입(docs/p5_design.md §4.4, metrics §19 — 로그인): 같은 날 액티브 ETF 2개 이상이
// 새로 담았거나(NEW·IN10) 늘린(ADD) 종목. 1개만 담은 종목은 넣지 않는다(정의 — 서버가 거른다).
import type { components } from '../../api/types.gen';
import type { Envelope } from '../../data/source';
import { h } from '../../ui/dom';
import { num, shortDate } from '../../ui/fmt';
import type { Panel } from '../../ui/panel';
import type { Option } from '../../ui/seg';
import { type Column, table } from '../../ui/table';
import { nameCell } from '../p1_market';
import { CHANGE_LABEL } from './holdings';

type S = components['schemas'];
export type Consensus = S['ActiveConsensus'];
type Row = S['ConsensusStock'];
export type Days = '1' | '5' | '20';

export const DAYS: readonly Option<Days>[] = [
  { value: '1', label: '1일' },
  { value: '5', label: '5일' },
  { value: '20', label: '20일' },
];

export const CONSENSUS_COLUMNS: readonly Column<Row>[] = [
  { key: 'stock', label: '종목', align: 'l', cls: () => 'nm', cell: (r) => nameCell(r.name, r.code) },
  { key: 'n', label: 'ETF 수', cell: (r) => num(r.n_funds), cls: () => 'up' },
  { key: 'kinds', label: '변동', align: 'l', cell: (r) => r.kinds.map((k) => CHANGE_LABEL[k] ?? k).join(' · ') },
  { key: 'funds', label: '액티브 ETF', align: 'l', cell: (r) => h('small', null, r.funds.join(', ')) },
];

export function drawConsensus(p: Panel, env: Envelope<Consensus>): number {
  const d = env.data;
  p.body.append(
    table<Row>({
      caption: `${shortDate(d.asof)} 실행까지 ${d.days}회 · 액티브 ETF ${d.min_funds}개 이상`,
      columns: CONSENSUS_COLUMNS,
      rows: d.rows,
      rowKey: (r) => r.code,
      pageSize: 30,
      empty: '동시 편입 종목 없음',
    }).el,
  );
  return 0;
}
