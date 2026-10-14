// 페이지 12 ⑦ 추천 성과 — SD 추천 이력 이관분의 사후 수익 D+1~D+7(확정 종가, %)과 기간별 평균(docs/p8_design.md §6, 묶음 JNL).
//
// - 이관분이 없으면(404) '.msg' 안내 한 줄. 새로 쌓지 않는다(이관 전용 — notes).
// - 값이 없는 칸(기간 미도래·종가 없음)은 '—', 평균은 값 있는 것만(n 표시). 공개 빌드는 이 모듈을 번들에서 뺀다.
import type { components } from '../../api/types.gen';
import type { PageContext } from '../../app/types';
import type { Envelope } from '../../data/source';
import { h } from '../../ui/dom';
import { num, pct, signClass } from '../../ui/fmt';
import { kv } from '../../ui/kv';
import type { Panel } from '../../ui/panel';
import { type Column, table } from '../../ui/table';
import { failPanel, nil } from './journal';

export type PickList = components['schemas']['PickList'];
export type PickOut = components['schemas']['PickOut'];

export const PICKS_HINT = '아직 없음 — 추천 이력 이관분 없음';
export const HORIZONS = ['d1', 'd3', 'd5', 'd7'] as const;

function retCell(v: number | null | undefined): HTMLElement {
  return h('span', { class: signClass(v) }, pct(v));
}

const COLUMNS: readonly Column<PickOut>[] = [
  { key: 'date', label: '날짜', align: 'l', cell: (r) => r.date },
  { key: 'src', label: '출처', align: 'l', cell: (r) => r.source },
  {
    key: 'code',
    label: '종목',
    align: 'l',
    cell: (r) => (r.name ? `${r.name} ${r.code}` : r.code),
  },
  {
    key: 'px',
    label: '추천가',
    cell: (r) => (nil(r.price_at_rec) ? null : num(r.price_at_rec)),
  },
  ...HORIZONS.map((k) => ({
    key: k,
    label: `D+${k.slice(1)}`,
    cell: (r: PickOut) => retCell(r.rets[k]),
  })),
];

/** 기간별 평균(값 있는 것만)과 n */
export function averages(
  items: readonly PickOut[],
): Record<(typeof HORIZONS)[number], { mean: number | null; n: number }> {
  const out = {} as Record<(typeof HORIZONS)[number], { mean: number | null; n: number }>;
  for (const k of HORIZONS) {
    const vs = items.map((r) => r.rets[k]).filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
    out[k] = {
      mean: vs.length ? vs.reduce((a, b) => a + b, 0) / vs.length : null,
      n: vs.length,
    };
  }
  return out;
}

export async function fillPicks(ctx: PageContext, p: Panel): Promise<void> {
  let env: Envelope<PickList>;
  try {
    env = await ctx.source.get<PickList>('journal/picks');
  } catch (err) {
    failPanel(p, err, PICKS_HINT);
    return;
  }
  const items = env.data.items;
  const avg = averages(items);
  p.clearBody();
  p.setSource(env);
  p.body.append(
    kv(
      HORIZONS.map((k) => ({
        label: `평균 D+${k.slice(1)}`,
        value: `${pct(avg[k].mean)} (n=${avg[k].n})`,
        cls: signClass(avg[k].mean),
      })),
    ),
    table<PickOut>({
      caption: '추천 이력(이관분)',
      columns: COLUMNS,
      rows: items,
      rowKey: (r) => `${r.date}:${r.source}:${r.code}`,
    }).el,
  );
}
