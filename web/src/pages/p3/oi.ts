// 페이지 3 ⑤ 코스피200 선물 미결제약정(OI) 패널(metrics §33, ADR 0043 — 로그인 전용, KRX·KIS 원천).
//
// - 패널 둘(전체 폭): '선물 미결제약정' — 최근 날(잠정이 있으면 잠정) 칸 + OI 합계·근월물 선 + OI 증감 막대 + 일별 표,
//   '옵션 투자자 일별' — 같은 응답의 월물·위클리 콜/풋 외국인·기관·개인 순매수(최근 날).
// - 데이터 키 'gex/futures-oi'(days=60) 한 번 — 두 패널이 나눈다. 404 는 '.msg'(작업 이름), 그 밖 실패만 '.msg.err'.
// - 잠정(KRX 확정 전 — 전광판)·15:45 전 투자자 행은 '잠정' 꼬리표(estimated). invalid 행은 표·차트에서 빼고 수를 notes 에.
// - 라벨은 규칙 결과(가격·OI 방향) — 누가 만든 포지션인지 단정하지 않는다(정의 줄에 적는다).
// - 공개 빌드에서는 이 모듈 본문이 번들에서 빠진다(p3_diag 의 LOGIN_MOUNTS) — 틀(ioPanels)만 자물쇠로.
import type { components } from '../../api/types.gen';
import type { PageContext } from '../../app/types';
import { DataError, type Envelope } from '../../data/source';
import { barsChart, type BarItem } from '../../ui/bars';
import { h, replace } from '../../ui/dom';
import { DASH, eok, eokNum, num, pct, shortDate, signClass, type Num } from '../../ui/fmt';
import { kv, type KvItem } from '../../ui/kv';
import { lineChart } from '../../ui/line';
import { errorText, type Panel, panel } from '../../ui/panel';
import { qValue } from '../../ui/quality';
import { table, type Column } from '../../ui/table';

type S = components['schemas'];
export type FuturesOi = S['FuturesOi'];
export type FutOiDay = S['FutOiDay'];
export type FutOiOption = S['FutOiOption'];
type Investor = FutOiOption['investor'];
type Label = FutOiDay['label'];

export const OI_KEY = 'gex/futures-oi';
export const OI_DAYS = '60';
/** 다시 읽는 주기 — 잠정 행(전광판)은 장중에 바뀐다. 확정 행은 하루 한 번 */
export const OI_REFRESH_MS = 60_000;
export const OI_SOURCE = 'KRX 선물 일별 + KIS 선물 전광판·투자자별';
export const OI_HINT = 'gex.krx_derivatives 평일 08:05';
export const OI_TITLES = { oi: '선물 미결제약정(OI)', opt: '옵션 투자자 일별(콜·풋)' } as const;
export const INVESTOR_KO: Readonly<Record<Investor, string>> = {
  foreign: '외국인',
  institution: '기관',
  individual: '개인',
};
/** 라벨 색 — 신규 매수·숏 커버는 상승색, 신규 매도·롱 청산은 하락색, 보합·판단 불가는 흐림 */
export const LABEL_TONE: Readonly<Record<Label, 'up' | 'down' | 'dim'>> = {
  new_long: 'up',
  short_cover: 'up',
  new_short: 'down',
  long_liquidation: 'down',
  flat: 'dim',
  unknown: 'dim',
};
export const OI_DEF =
  '코스피200 선물 전 월물 미결제약정 합계(계약)와 전일 대비 증감 — 만기 다음 날 사라지는 만기 종목(최종결제분)은 증감에서 뺀다. ' +
  '라벨: 가격↑·OI↑ 신규 매수 우위, 가격↓·OI↑ 신규 매도 우위, 가격↑·OI↓ 숏 커버 우위, 가격↓·OI↓ 롱 청산 우위(가격 = 근월물 등락률). ' +
  '규칙 결과일 뿐 누가 포지션을 만들었는지 단정하지 않는다 — 외국인 순매수와 겹쳐 읽는다. 확정은 KRX 일별(다음 날 08:05), ' +
  '그 전 오늘은 선물 전광판 잠정.';
export const OPT_DEF =
  '코스피200 옵션 투자자별 순매수 대금(억원) — 월물·위클리(월)·위클리(목) 콜/풋, 장중 투자자 수집의 그날 마지막 행. ' +
  '15:45 마감 전 행이면 잠정.';

const SERIES_COLS: readonly { series: FutOiOption['series']; cp: FutOiOption['cp']; label: string }[] = [
  { series: 'monthly', cp: 'C', label: '월물 콜' },
  { series: 'monthly', cp: 'P', label: '월물 풋' },
  { series: 'weekly_mon', cp: 'C', label: '위클리(월) 콜' },
  { series: 'weekly_mon', cp: 'P', label: '위클리(월) 풋' },
  { series: 'weekly_thu', cp: 'C', label: '위클리(목) 콜' },
  { series: 'weekly_thu', cp: 'P', label: '위클리(목) 풋' },
];

function finite(v: Num): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/** 계약 수(부호) — '+3,451계약' */
export function contracts(v: Num, signed = true): string {
  return finite(v) ? `${num(v, 0, signed)}계약` : DASH;
}

/** 외국인 연속일 — '3일 연속 매수'·'2일 연속 매도'·'—' */
export function streakText(n: Num): string {
  if (!finite(n) || n === 0) return DASH;
  return `${String(Math.abs(n))}일 연속 ${n > 0 ? '매수' : '매도'}`;
}

/** 표·칸에 보일 날(잠정 → 확정 최근순). invalid 는 뺀다 */
export function rowsOf(d: FuturesOi): FutOiDay[] {
  const all = [...(d.provisional ? [d.provisional] : []), ...[...d.days].reverse()];
  return all.filter((r) => r.quality !== 'invalid');
}

function flow(r: FutOiDay, who: Investor): S['FutOiInvestor'] | undefined {
  return (r.flows ?? []).find((f) => f.investor === who);
}

/** 라벨 꼬리표 */
export function labelChip(r: Pick<FutOiDay, 'label' | 'label_text' | 'reasons'>): HTMLElement {
  return h(
    'span',
    { class: `oi-label ${LABEL_TONE[r.label]}`, data: { label: r.label }, attrs: { title: (r.reasons ?? []).join(' · ') } },
    r.label_text,
  );
}

/** 맨 위 칸 — 그날 합계·증감·근월물·라벨·외국인 선물 */
export function headItems(r: FutOiDay, streak: Num): KvItem[] {
  const q = r.provisional ? r.quality : null;
  const f = flow(r, 'foreign');
  return [
    { label: '기준', value: `${shortDate(r.trade_date)}${r.provisional ? ' (잠정)' : ''}`, quality: q },
    { label: 'OI 합계', value: contracts(r.total_oi, false), quality: q, hint: '전 월물(계약)' },
    {
      label: 'OI 증감',
      value: finite(r.oi_change) ? `${contracts(r.oi_change)} (${pct(r.oi_change_pct, 2)})` : null,
      cls: signClass(r.oi_change),
      quality: q,
      hint: '만기 종목 제외',
    },
    {
      label: '근월물',
      value: finite(r.near_close) ? `${num(r.near_close, 2)} (${pct(r.near_change_pct, 2)})` : null,
      cls: signClass(r.near_change),
      quality: q,
      hint: r.near_expiry ? `${r.near_expiry.slice(0, 4)}.${r.near_expiry.slice(4)}물` : undefined,
    },
    { label: '해석', value: labelChip(r) },
    {
      label: '외국인 선물',
      value: f ? `${contracts(f.net_qty)} · ${eok(f.net_value_won, 0, true)}` : null,
      cls: signClass(f?.net_qty),
      quality: f?.quality ?? null,
      hint: streakText(streak),
    },
  ];
}

function cellQ(text: string, q: S['FutOiDay']['quality'] | undefined, cls?: string): Node | string {
  if (!q || q === 'ok') return text;
  return qValue(text, q, null, cls) ?? text;
}

function oiColumns(): Column<FutOiDay>[] {
  const inv = (who: Investor): Column<FutOiDay> => ({
    key: who,
    label: `${INVESTOR_KO[who]}(계약)`,
    cell: (r) => {
      const f = flow(r, who);
      return f ? cellQ(num(f.net_qty, 0, true), f.quality) : null;
    },
    cls: (r) => signClass(flow(r, who)?.net_qty),
  });
  return [
    { key: 'date', label: '날짜', align: 'l', cell: (r) => cellQ(shortDate(r.trade_date), r.provisional ? r.quality : 'ok') },
    { key: 'close', label: '근월물', cell: (r) => (finite(r.near_close) ? num(r.near_close, 2) : null) },
    { key: 'chg', label: '등락률', cell: (r) => pct(r.near_change_pct, 2), cls: (r) => signClass(r.near_change_pct) },
    { key: 'oi', label: 'OI 합계', cell: (r) => (finite(r.total_oi) ? num(r.total_oi) : null) },
    { key: 'doi', label: 'OI 증감', cell: (r) => (finite(r.oi_change) ? num(r.oi_change, 0, true) : null), cls: (r) => signClass(r.oi_change) },
    { key: 'doip', label: '증감률', cell: (r) => pct(r.oi_change_pct, 2), cls: (r) => signClass(r.oi_change_pct) },
    inv('foreign'),
    inv('institution'),
    inv('individual'),
    { key: 'label', label: '해석', align: 'l', cell: (r) => labelChip(r) },
  ];
}

function drawOi(p: Panel, env: Envelope<FuturesOi>): void {
  const d = env.data;
  const invalid = [...d.days, ...(d.provisional ? [d.provisional] : [])].filter((r) => r.quality === 'invalid').length;
  p.setSource(env, invalid);
  const rows = rowsOf(d);
  const head = rows[0];
  if (!head) {
    p.showMessage('보일 날이 없음(모두 검산 실패)');
    return;
  }
  const chrono = [...rows].reverse();
  const x = chrono.map((r) => shortDate(r.trade_date));
  const line = lineChart({
    label: '코스피200 선물 미결제약정(계약)',
    x,
    series: [
      { label: 'OI 합계', values: chrono.map((r) => r.total_oi) },
      { label: '근월물 OI', values: chrono.map((r) => r.near_oi) },
    ],
    format: (v) => num(v),
  });
  const bars: BarItem[] = chrono.map((r) => {
    const f = flow(r, 'foreign');
    return {
      label: shortDate(r.trade_date),
      value: r.oi_change,
      detail: `${r.label_text}${f ? ` · 외국인 ${contracts(f.net_qty)}` : ''}${r.provisional ? ' · 잠정' : ''}`,
    };
  });
  const tbl = table<FutOiDay>({
    caption: '일별 OI·근월물·투자자 순매수(최근순)',
    columns: oiColumns(),
    rows,
    rowKey: (r) => `${r.trade_date}${r.provisional ? 'p' : ''}`,
    pageSize: 10,
  });
  replace(
    p.body,
    kv(headItems(head, d.foreign_streak)),
    h('div', { class: 'oi-charts' }, line.el, barsChart({ label: 'OI 증감(만기 종목 제외, 계약)', items: bars, format: (v) => num(v, 0, true) })),
    tbl.el,
    h(
      'p',
      { class: 'dim' },
      `보합 기준: 가격 ±${String(d.thresholds.price_flat_pct)}% · OI ±${String(d.thresholds.oi_flat_pct)}% 안 [확인 필요 — 기본값]`,
    ),
  );
}

/** 옵션 표 — 최근 날 하나, 행 = 투자자, 열 = 시리즈·콜풋(억원) */
export function optionRows(d: FuturesOi): { day: string | null; rows: { investor: Investor; cells: (FutOiOption | undefined)[] }[] } {
  const day = d.options.reduce<string | null>((a, o) => (a === null || o.trade_date > a ? o.trade_date : a), null);
  const same = d.options.filter((o) => o.trade_date === day);
  const order: Investor[] = ['foreign', 'institution', 'individual'];
  return {
    day,
    rows: order.map((investor) => ({
      investor,
      cells: SERIES_COLS.map((c) => same.find((o) => o.investor === investor && o.series === c.series && o.cp === c.cp)),
    })),
  };
}

function drawOpt(p: Panel, env: Envelope<FuturesOi>): void {
  p.setSource(env);
  const { day, rows } = optionRows(env.data);
  if (day === null) {
    p.showMessage('옵션 투자자 행 없음 — flows.market_minute(정규장 매분)');
    return;
  }
  type Row = (typeof rows)[number];
  const cols: Column<Row>[] = [
    { key: 'who', label: '투자자', align: 'l', cell: (r) => INVESTOR_KO[r.investor] },
    ...SERIES_COLS.map((c, i): Column<Row> => ({
      key: `${c.series}-${c.cp}`,
      label: `${c.label}(억)`,
      cell: (r) => {
        const o = r.cells[i];
        if (!o || o.quality === 'invalid') return null;
        const el = h('span', { attrs: { title: `${contracts(o.net_qty)}${o.reason ? ` · ${o.reason}` : ''}` } });
        el.append(cellQ(eokNum(o.net_value_won, 0, true), o.quality));
        return el;
      },
      cls: (r) => signClass(r.cells[i]?.net_value_won),
    })),
  ];
  replace(
    p.body,
    h('p', { class: 'dim' }, `${shortDate(day)} 기준 — 순매수 대금(억원), 수량은 칸에 마우스`),
    table<Row>({ caption: `옵션 투자자별 순매수(${day})`, columns: cols, rows, rowKey: (r) => r.investor }).el,
  );
}

function fail(p: Panel, err: unknown): void {
  if (err instanceof DataError && err.status === 404) p.showMessage(`${errorText(err)} — ${OI_HINT}`);
  else p.showError(err);
}

/** ⑤ 패널 둘(틀만 — 공개 빌드는 이 틀만 쓰고 본문 mountOi 는 번들에서 빠진다) */
export function oiPanels(ctx: PageContext): { oi: Panel; opt: Panel } {
  const common = { tier: 'login' as const, lock: 'investor', src: OI_SOURCE, span: 12 as const };
  const oi = panel(ctx.tier, { ...common, title: OI_TITLES.oi, def: OI_DEF });
  const opt = panel(ctx.tier, { ...common, title: OI_TITLES.opt, def: OPT_DEF });
  ctx.root.append(oi.el, opt.el);
  return { oi, opt };
}

export async function mountOi(ctx: PageContext): Promise<void> {
  const { oi, opt } = oiPanels(ctx);
  if (oi.locked) return; // 공개판 — 자물쇠만, 요청하지 않는다

  async function load(): Promise<void> {
    let env: Envelope<FuturesOi>;
    try {
      env = await ctx.source.get<FuturesOi>(OI_KEY, { days: OI_DAYS });
    } catch (err) {
      fail(oi, err);
      fail(opt, err);
      return;
    }
    try {
      drawOi(oi, env);
    } catch (err) {
      oi.showError(err);
    }
    try {
      drawOpt(opt, env);
    } catch (err) {
      opt.showError(err);
    }
  }

  await load();
  if (!ctx.root.isConnected) return;
  let busy = false;
  ctx.every(OI_REFRESH_MS, () => {
    if (busy) return;
    busy = true;
    void load().finally(() => {
      busy = false;
    });
  });
}
