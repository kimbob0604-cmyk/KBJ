// 페이지 9 로그인 본문(ADR 0039, metrics §31) — 테마 강도 순위 표·강도 히트맵(트리맵)·밸류체인 강도·편입 종목 표.
// **공개 빌드에서는 번들에서 뺀다**(p9_themes.ts 의 LOGIN_FILLS — p8_stock LOGIN_VALUE 방식, 공개 JS 예산 ADR 0025).
//
// - 키: 'themes/strength'(+ period 1·5·20), 'themes/<id>'(편입 종목). 404 는 '.msg'(작업·시각), 그 밖만 '.msg.err'.
// - 강도 = 편입 종목의 당일·5일·20일 등락 동일가중 평균(metrics §31 — 시총 가중이면 대형주 하나가 테마를 덮었다), 상승 비율, 신고가 수(보드 라벨). 시세는 보드 종목
//   하루(KRX/KIS 정본) — 서버가 계산한다(화면은 반올림·색만).
// - 편입 종목 표의 종목은 #/p/8/<코드>(종목 상세)로 연다(app/route.ts openStock).
import { openStock } from '../../app/route';
import type { components } from '../../api/types.gen';
import type { Envelope } from '../../data/source';
import { h } from '../../ui/dom';
import { DASH, joEok, num, pct, signClass } from '../../ui/fmt';
import type { Panel } from '../../ui/panel';
import { seg } from '../../ui/seg';
import { type Column, table } from '../../ui/table';
import { treemap } from '../../ui/treemap';
import { failed } from '../p1_market';
import type { P9Ctx } from './catalog';

type S = components['schemas'];
export type ThemeStrength = S['ThemeStrength'];
export type ThemeDetail = S['ThemeDetail'];
type Row = ThemeStrength['rows'][number];
type ChainRow = ThemeStrength['chains'][number];
type Stats = S['ThemeStats'];
type Member = ThemeDetail['members'][number];
export type Period = 1 | 5 | 20;

export const HINT_STRENGTH = 'theme.master_sync 일 12:40 · board.daily 16:00~16:05';
export const PERIODS: readonly { value: `${Period}`; label: string }[] = [
  { value: '1', label: '당일' },
  { value: '5', label: '5일' },
  { value: '20', label: '20일' },
];
const LABEL_KO: Readonly<Record<string, string>> = { hist: '역사적', w52: '52주', d120: '120일' };

export function periodValue(s: Stats, p: Period): number | null {
  return p === 1 ? s.chg_pct : p === 5 ? s.ret_5d : s.ret_20d;
}

const pctCell = (v: number | null | undefined): string => (typeof v === 'number' ? pct(v, 2) : DASH);
const ratio = (v: number | null | undefined): string => (typeof v === 'number' ? `${num(v * 100, 0)}%` : DASH);

function strengthColumns(): Column<Row>[] {
  return [
    { key: 'rank', label: '순위', cell: (r) => String(r.rank) },
    { key: 'name', label: '대테마', align: 'l', cell: (r) => h('span', {}, r.name, h('small', { class: 'dim' }, ` ${r.section}`)) },
    { key: 'd1', label: '당일', cell: (r) => pctCell(r.stats.chg_pct), cls: (r) => signClass(r.stats.chg_pct) },
    { key: 'd5', label: '5일', cell: (r) => pctCell(r.stats.ret_5d), cls: (r) => signClass(r.stats.ret_5d) },
    { key: 'd20', label: '20일', cell: (r) => pctCell(r.stats.ret_20d), cls: (r) => signClass(r.stats.ret_20d) },
    { key: 'up', label: '상승 비율', cell: (r) => ratio(r.stats.up_ratio) },
    { key: 'nh', label: '신고가', cell: (r) => num(r.stats.n_newhigh) },
    { key: 'n', label: '종목', cell: (r) => `${num(r.stats.n_priced)}/${num(r.stats.n_members)}` },
    { key: 'lead', label: '거래대금 1위', align: 'l', cell: (r) => r.stats.leader?.name ?? DASH },
  ];
}

function chainColumns(): Column<ChainRow>[] {
  return [
    { key: 'key', label: '밸류체인', align: 'l', cell: (r) => r.key },
    { key: 'd1', label: '당일', cell: (r) => pctCell(r.stats.chg_pct), cls: (r) => signClass(r.stats.chg_pct) },
    { key: 'd5', label: '5일', cell: (r) => pctCell(r.stats.ret_5d), cls: (r) => signClass(r.stats.ret_5d) },
    { key: 'd20', label: '20일', cell: (r) => pctCell(r.stats.ret_20d), cls: (r) => signClass(r.stats.ret_20d) },
    { key: 'up', label: '상승 비율', cell: (r) => ratio(r.stats.up_ratio) },
    { key: 'n', label: '대테마', cell: (r) => num(r.themes.length) },
  ];
}

function stockLink(code: string, name: string): HTMLElement {
  return h(
    'a',
    {
      attrs: { href: `#/p/8/${code}` },
      on: {
        click: (ev: Event) => {
          ev.preventDefault();
          openStock(code);
        },
      },
    },
    `${name} (${code})`,
  );
}

function memberColumns(naver: ReadonlyMap<number, string>): Column<Member>[] {
  return [
    { key: 'name', label: '종목', align: 'l', cell: (r) => stockLink(r.code, r.name) },
    { key: 'grp', label: '업종', align: 'l', cell: (r) => r.industry_group ?? DASH },
    { key: 'd1', label: '당일', cell: (r) => pctCell(r.chg_pct), cls: (r) => signClass(r.chg_pct) },
    { key: 'd5', label: '5일', cell: (r) => pctCell(r.ret_5d), cls: (r) => signClass(r.ret_5d) },
    { key: 'd20', label: '20일', cell: (r) => pctCell(r.ret_20d), cls: (r) => signClass(r.ret_20d) },
    { key: 'cap', label: '시총', cell: (r) => (typeof r.mktcap === 'number' ? joEok(r.mktcap) : DASH) },
    { key: 'nh', label: '신고가', align: 'l', cell: (r) => (r.label ? (LABEL_KO[r.label] ?? r.label) : '') },
    {
      key: 'why',
      label: '편입 사유',
      align: 'l',
      cell: (r) =>
        h(
          'span',
          { class: 'dim' },
          r.reasons.map((x) => `[${naver.get(x.naver_no) ?? `#${x.naver_no}`}] ${x.reason ?? DASH}`).join(' · '),
        ),
    },
  ];
}

export interface StrengthPanels {
  rank: Panel;
  heat: Panel;
  chains: Panel;
  members: Panel;
}

/** 편입 종목 패널을 그 대테마로 채운다 */
export async function fillMembers(t: P9Ctx, p: Panel, id: string): Promise<void> {
  let env: Envelope<ThemeDetail>;
  try {
    env = await t.source.get<ThemeDetail>(`themes/${id}`);
  } catch (err) {
    failed(p, err, HINT_STRENGTH);
    return;
  }
  const d = env.data;
  const naver = new Map(d.naver.map((n) => [n.no, n.name] as const));
  p.clearBody();
  p.head.querySelector('span')?.replaceChildren(`편입 종목 — ${d.name}`);
  p.body.append(
    h(
      'p',
      { class: 'dim', data: { section: 'members-head' } },
      `${d.section}${d.valuechain ? ` · ${d.valuechain}` : ''} · 원본 테마 ${num(d.naver.length)}개 · 종목 ${num(d.members.length)} · ${asOfDay(d.trade_date)}`,
    ),
    h('div', { class: 'theme-chips' }, ...d.naver.map((n) => h('span', { class: 'tag dim' }, `${n.name} ${num(n.n_members)}`))),
    table<Member>({ columns: memberColumns(naver), rows: d.members, rowKey: (r) => r.code, empty: '편입 종목 없음' }).el,
  );
  p.setSource(env);
}

/** 시세 기준 날 한 줄 — 서버가 고른 마지막 보드 날(오늘 보드 전이면 지난 거래일) */
export function asOfDay(day: string | null | undefined): string {
  return day ? `시세 ${day} 마감` : '시세 없음(보드 전)';
}

function drawHeat(p: Panel, rows: readonly Row[], period: Period, day: string): void {
  p.clearBody();
  p.body.append(h('p', { class: 'dim', data: { section: 'asof' } }, asOfDay(day)));
  // 크기 = 시세 있는 편입 종목 수 — 시총 합이면 여러 테마에 든 대형주 시총이 테마마다 겹쳐 더해졌다(2026-10-20)
  const items = rows.map((r) => ({ id: r.id, label: r.name, size: r.stats.n_priced, color: periodValue(r.stats, period) }));
  p.body.append(
    treemap({
      label: `대테마 강도 히트맵 — 크기 종목 수, 색 ${PERIODS.find((x) => x.value === String(period))?.label ?? ''} 등락`,
      items,
      formatSize: (v) => `${num(v)}종목`,
      formatColor: (v) => pct(v, 2),
      w: 480,
      h: 300,
    }),
  );
}

/**
 * 강도 셋(순위·히트맵·밸류체인)을 기간 토글과 함께 채운다. 대테마를 고르면 편입 종목 패널을 그 테마로 다시 그린다.
 * 처음 편입 종목 패널은 1위 대테마.
 */
export async function fillStrength(t: P9Ctx, p: StrengthPanels, pick: (fn: (id: string) => void) => void): Promise<void> {
  let period: Period = 1;
  let selected: string | null = null;
  let tb: ReturnType<typeof table<Row>> | null = null;
  let members: Promise<void> = Promise.resolve();
  const choose = (id: string): void => {
    selected = id;
    tb?.select(id);
    members = fillMembers(t, p.members, id);
  };
  pick(choose);
  const toggle = seg<`${Period}`>({
    label: '기간',
    options: PERIODS,
    value: '1',
    onChange: (v) => {
      period = Number(v) as Period;
      void load();
    },
  });
  const load = async (): Promise<void> => {
    let env: Envelope<ThemeStrength>;
    try {
      env = await t.source.get<ThemeStrength>('themes/strength', { period });
    } catch (err) {
      failed(p.rank, err, HINT_STRENGTH);
      failed(p.heat, err, HINT_STRENGTH);
      failed(p.chains, err, HINT_STRENGTH);
      if (selected === null) failed(p.members, err, HINT_STRENGTH);
      return;
    }
    const d = env.data;
    p.rank.clearBody();
    tb = table<Row>({
      columns: strengthColumns(),
      rows: d.rows,
      rowKey: (r) => r.id,
      onSelect: (r) => choose(r.id),
      selected,
      empty: '대테마 없음',
    });
    p.rank.body.append(toggle.el, h('p', { class: 'dim', data: { section: 'asof' } }, `${asOfDay(d.trade_date)} · 대테마 ${num(d.rows.length)}`), tb.el);
    p.rank.setSource(env);
    drawHeat(p.heat, d.rows, period, d.trade_date);
    p.heat.setSource(env);
    p.chains.clearBody();
    p.chains.body.append(h('p', { class: 'dim', data: { section: 'asof' } }, asOfDay(d.trade_date)), table<ChainRow>({ columns: chainColumns(), rows: d.chains, rowKey: (r) => r.key, empty: '밸류체인 없음' }).el);
    p.chains.setSource(env);
    if (selected === null && d.rows[0]) choose(d.rows[0].id);
  };
  await load();
  await members; // 첫 편입 종목 요청이 끝날 때까지(시험·데모 캡처가 기다린다)
}
