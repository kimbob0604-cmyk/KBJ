// 페이지 7 '지역(시군구)' 탭(EXP — docs/p6_design.md §1.4·§6, metrics §26). 매핑 HS6 의 시군구별 수출(관세청 15134343 —
// 시군구 이름만 주어 통합 지역 ID 로 2026-07 시도 개편을 넘어 전년과 비교). HS6 를 고르면 그 시군구 표.
// 로그인: 'trade/regions'(전부 한 번 — HS6 는 화면에서 거른다), 공개: 'trade/hs2' 의 regions 색인 → 'trade/region/<hs6>'.
import type { Envelope } from '../../data/source';
import { toCsv } from '../../ui/csv';
import { h, replace } from '../../ui/dom';
import { signClass } from '../../ui/fmt';
import { select } from '../../ui/seg';
import { type Column, table } from '../../ui/table';
import {
  failNote,
  csvMeta,
  type PubTree,
  ratio,
  type TabCtx,
  tableHead,
  TRADE_HINT,
  type TradeRegions,
  tradePanel,
  usd,
} from './common';

export const TITLE = '지역(시군구)';

type Row = TradeRegions['rows'][number];

const COLUMNS: readonly Column<Row>[] = [
  { key: 'sido', label: '시도', align: 'l', cell: (r) => r.sido_cd },
  { key: 'sgg', label: '시군구', align: 'l', cell: (r) => r.sgg_nm },
  { key: 'exp', label: '수출', cell: (r) => usd(r.exp_usd) },
  {
    key: 'yoy',
    label: '전년 대비',
    cell: (r) => ratio(r.exp_yoy),
    cls: (r) => signClass(r.exp_yoy),
  },
  { key: 'share', label: '비중', cell: (r) => ratio(r.share).replace('+', '') },
];

export function regionsCsv(rows: readonly Row[], ym: string): string {
  return toCsv(
    rows,
    [
      { label: 'HS6', value: 'hs6' },
      { label: '시도', value: 'sido_cd' },
      { label: '시군구', value: 'sgg_nm' },
      { label: '통합 지역 ID', value: 'region_id' },
      { label: '수출(달러)', value: 'exp_usd' },
      { label: '전년 대비', value: 'exp_yoy' },
      { label: '비중', value: 'share' },
    ],
    { meta: csvMeta(ym) },
  );
}

export async function mount(t: TabCtx, el: HTMLElement): Promise<void> {
  const p = tradePanel(t, { title: TITLE });
  p.setDef(
    '매핑표에 걸린 HS6 만 수집한다. 비중 = 같은 HS6 안 시군구 수출 비중. 시도 개편(2026-07)은 통합 지역 ID 로 잇는다.',
  );
  el.append(h('div', { class: 'grid' }, p.el));
  const body = h('div', { class: 'regions-body' });
  let all: Envelope<TradeRegions> | null = null;
  const show = async (hs6: string): Promise<void> => {
    try {
      let env: Envelope<TradeRegions>;
      if (all)
        env = {
          ...all,
          data: {
            ...all.data,
            hs6,
            rows: all.data.rows.filter((r) => r.hs6 === hs6),
          },
        };
      else env = await t.source.get<TradeRegions>(`trade/region/${hs6}`);
      p.setSource(env);
      const d = env.data;
      replace(
        body,
        tableHead(`HS ${hs6} · ${d.ym}`, `trade_region_${hs6}_${d.ym}`, () => regionsCsv(d.rows, d.ym)),
        table<Row>({
          caption: `HS ${hs6} 시군구 ${d.ym}`,
          columns: COLUMNS,
          rows: d.rows,
          rowKey: (r) => `${r.sido_cd}:${r.sgg_nm}`,
        }).el,
      );
    } catch (err) {
      replace(body, failNote(err, TRADE_HINT.monthly));
    }
  };
  try {
    let list: { value: string; label: string }[];
    if (t.tier === 'login') {
      all = await t.source.get<TradeRegions>('trade/regions');
      list = [...new Set(all.data.rows.map((r) => r.hs6))].sort().map((x) => ({ value: x, label: x }));
    } else {
      const idx = (await t.source.get<PubTree>('trade/hs2')).data.regions ?? [];
      list = idx.map((x) => ({
        value: x.hs6,
        label: x.name ? `${x.hs6} ${x.name}` : x.hs6,
      }));
    }
    const first = list[0];
    if (!first) {
      p.body.append(h('p', { class: 'msg' }, `아직 없음 — ${TRADE_HINT.monthly}`));
      return;
    }
    const sel = select<string>({
      label: 'HS6',
      options: list,
      value: first.value,
      onChange: (v) => void show(v),
    });
    p.body.append(sel.el, body);
    await show(first.value);
  } catch (err) {
    p.body.append(failNote(err, TRADE_HINT.monthly));
  }
}
