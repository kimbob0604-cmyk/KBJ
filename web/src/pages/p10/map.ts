// 페이지 10 ETF 맵(docs/p5_design.md §4.4·§6, metrics §19 — 로그인).
//
// - 칸 = ETF 하나: 크기 = 순자산(마지막 확정일), 색 = 기간 수익률(1D·1W·1M·3M — 분할 보정). 묶음 = 유형(7종) 또는
//   테마(국내주식만). 레버리지·인버스는 기본 숨김(토글). 신규 상장(이력이 기간보다 짧음)은 수익률 없음 → 회색 + '신규'.
// - 색 범위는 모든 묶음에 같은 값(묶음끼리 진하기를 비교할 수 있게).
// - 칸을 누르면 그 ETF 의 구성종목을 보유 탐색에서 연다(onOpen).
import type { components } from '../../api/types.gen';
import type { Envelope } from '../../data/source';
import { h } from '../../ui/dom';
import { joEok, num, pct, shortDate } from '../../ui/fmt';
import type { Panel } from '../../ui/panel';
import type { Option } from '../../ui/seg';
import { type TreeItem, treemap } from '../../ui/treemap';

type S = components['schemas'];
export type MapData = S['EtfMap'];
export type MapPeriod = MapData['period'];
export type MapGroup = MapData['group'];
type Cell = S['EtfMapCellView'];

export const PERIODS: readonly Option<MapPeriod>[] = [
  { value: '1D', label: '1일' },
  { value: '1W', label: '1주' },
  { value: '1M', label: '1개월' },
  { value: '3M', label: '3개월' },
];
export const GROUPS: readonly Option<MapGroup>[] = [
  { value: 'type', label: '유형' },
  { value: 'theme', label: '테마' },
];
export const LEV: readonly Option<'hide' | 'show'>[] = [
  { value: 'hide', label: '레버리지·인버스 숨김' },
  { value: 'show', label: '보기' },
];
export const NEW_TAG = '신규';

/** 칸 이름표: 이름(없으면 코드) + 신규 상장 꼬리표 */
export function cellLabel(c: Cell): string {
  const base = c.name ?? c.code;
  return c.new_listing ? `${base} · ${NEW_TAG}` : base;
}

export function mapItems(cells: readonly Cell[]): TreeItem[] {
  return cells.map((c) => ({ id: c.code, label: cellLabel(c), size: c.net_asset, color: c.new_listing ? null : c.ret_pct }));
}

/** 공통 색 범위(모든 칸 |수익률| 최대 — 없으면 0) */
export function colorRange(d: MapData): number {
  let m = 0;
  for (const g of d.groups) for (const c of g.cells) if (c.ret_pct !== null && !c.new_listing) m = Math.max(m, Math.abs(c.ret_pct));
  return m;
}

export function drawMap(p: Panel, env: Envelope<MapData>, onOpen?: (code: string) => void): number {
  const d = env.data;
  const range = colorRange(d);
  const per = PERIODS.find((o) => o.value === d.period)?.label ?? d.period;
  const n = d.groups.reduce((a, g) => a + g.cells.length, 0);
  p.body.append(
    h(
      'p',
      { class: 'dim', data: { section: 'map-head' } },
      `${shortDate(d.asof)} 기준 · ${per} 수익률 · ETF ${num(n)}개 · 색 범위 ±${num(range, 1)}%`,
    ),
  );
  if (!d.groups.length) {
    p.showMessage('맵에 올릴 ETF 없음');
    return 0;
  }
  for (const g of d.groups) {
    const box = h(
      'div',
      { class: 'map-group', data: { group: g.key } },
      h('h4', null, `${g.label} `, h('small', null, `${num(g.cells.length)}개`)),
      treemap({
        label: `${g.label} ETF 맵 — 크기 순자산, 색 ${per} 수익률`,
        items: mapItems(g.cells),
        range,
        formatSize: (v) => joEok(v),
        formatColor: (v) => pct(v, 2),
        h: Math.max(140, Math.min(360, 60 * Math.ceil(Math.sqrt(g.cells.length)) + 80)),
      }),
    );
    if (onOpen) {
      box.addEventListener('click', (ev) => {
        const t = ev.target as Element | null;
        const id = t?.closest('g[data-id]')?.getAttribute('data-id');
        if (id) onOpen(id);
      });
    }
    p.body.append(box);
  }
  return 0;
}
