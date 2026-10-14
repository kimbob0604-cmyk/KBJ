// 페이지 8 '재무' 탭 — 분기 12·연간 4 표(매출·영업익·순익·OPM·YoY), 막대 + OPM 선, 3개월값 대조 경고, 비율, 부문 매출
// (docs/p4_design.md §4.5·§6.2). 원천 DART(공개) — 로그인은 'stock/<코드>/financials'·'segments', 공개는 fin/<코드>.json.
//
// - 분기 값은 누적 차분(Q4 = 연간 − 3분기 누적). 차분할 직전 누적이 없으면 그 행은 값 없이 '차분 불가'(derived).
// - 보고서 3개월값과 차분값이 1% 넘게 다르면 그 행은 추정(checks.diff3m_max_pct) — 경고 줄.
// - 외국 통화 회사는 그 통화 그대로(환산하지 않는다 — 단위에 통화).
// - 공개 파일은 로그인 응답과 같은 모양으로 바꿔(분기·연간 표 columns → 행) 같은 그리기 코드를 쓴다.
import type { Envelope } from '../../data/source';
import { append, h } from '../../ui/dom';
import { DASH, kstDate, num, pct, signClass } from '../../ui/fmt';
import { kv, type KvItem } from '../../ui/kv';
import type { Panel } from '../../ui/panel';
import { EST_LABEL, qValue } from '../../ui/quality';
import { seg } from '../../ui/seg';
import { type Column, table } from '../../ui/table';
import { failed, fill, filters } from '../p1_market';
import {
  col,
  comboChart,
  HINT,
  mkPanel,
  moneyNum,
  moneyUnit,
  naText,
  NO_PUB_FIN,
  noPubFin,
  type PubFin,
  type S,
  tabGrid,
  type TabCtx,
  tag,
  unpack,
  won,
} from './common';

type FinRow = S['FinRow'];
type Fin = S['Financials'];
type Basis = 'q' | 'a';

/** 재무 표 칸 */
export function finColumns(cur: string | null): Column<FinRow>[] {
  const u = moneyUnit(cur);
  const m = (label: string, get: (r: FinRow) => number | null): Column<FinRow> =>
    col(`${label}(${u})`, (r) => moneyNum(get(r), cur), false, (r) => signClass(get(r)));
  const p = (label: string, get: (r: FinRow) => number | null, signed: boolean): Column<FinRow> =>
    col(label, (r) => pct(get(r), 1, signed), false, signed ? (r) => signClass(get(r)) : undefined);
  return [
    col(
      '기간',
      (r) =>
        h(
          'span',
          { attrs: { title: r.notes.join(' · ') || undefined } },
          qValue(r.label, r.quality, null, undefined, EST_LABEL) ?? r.label,
          r.derived ? tag('차분 불가', 'dim') : null,
        ),
      true,
    ),
    m('매출', (r) => r.revenue),
    m('영업이익', (r) => r.op),
    m('순이익', (r) => r.ni),
    m('지배순익', (r) => r.ni_owner),
    p('OPM', (r) => r.opm, false),
    p('매출 YoY', (r) => r.rev_yoy, true),
    p('영업익 YoY', (r) => r.op_yoy, true),
    col('접수일', (r) => (r.rcept_dt ? kstDate(r.rcept_dt) : null)),
  ];
}

/** 3개월값 대조·차분·추정 줄 */
export function checksLine(c: S['FinChecks'] | null | undefined): HTMLElement | null {
  if (!c) return null;
  const d = c.diff3m_max_pct;
  const parts = [d === null ? '3개월값 대조 불가' : `3개월값 대조 최대 차이 ${num(d, 2)}%`];
  if (c.n_derived) parts.push(`차분 불가 ${c.n_derived}행`);
  if (c.n_estimated) parts.push(`추정 ${c.n_estimated}행`);
  const bad = (d ?? 0) > 1 || c.n_estimated > 0;
  return h('div', { class: bad ? 'check fail' : 'check', data: { check: bad ? 'fail' : 'ok' } }, parts.join(' · '));
}

/** 재무 표 + 차트(공개·로그인 공통 — 로그인 응답 모양) */
export function drawFin(p: Panel, d: Fin): void {
  if (!d.applicable) {
    p.showMessage(naText(d.na_reason));
    return;
  }
  const cur = d.currency;
  const rows = d.rows.filter((r) => !r.derived);
  append(p.body, [
    comboChart({
      label: `${d.basis === 'q' ? '분기' : '연간'} 매출과 영업이익률`,
      categories: rows.map((r) => r.label),
      bars: rows.map((r) => r.revenue),
      line: rows.map((r) => r.opm),
      barName: `매출(${moneyUnit(cur)})`,
    }),
    table({
      caption: `${d.basis === 'q' ? '분기(누적 차분)' : '연간'} · ${d.fs_div === 'OFS' ? '별도' : '연결'}${cur && cur !== 'KRW' ? ` · ${cur}(환산 안 함)` : ''}`,
      columns: finColumns(cur),
      rows: [...d.rows].reverse(),
      empty: '재무 행 없음',
    }).el,
    checksLine(d.checks),
    d.tier === 'main' ? h('p', { class: 'def' }, '주요계정 층 — 전체 재무제표 수집 대상 아님') : null,
  ]);
}

type RatioKey = Exclude<keyof S['RatioView'], 'period' | 'fs_div' | 'na_reasons' | 'quality' | 'notes'>;

/** 비율 칸 */
export function ratioItems(r: S['RatioView'], cur: string | null): KvItem[] {
  const item = (label: string, key: RatioKey, text: (v: number) => string): KvItem => {
    const na = r.na_reasons[key];
    const v = r[key];
    return na ? { label, na: naText(na, label) } : { label, value: v === null ? null : text(v), quality: r.quality };
  };
  const p1 = (v: number): string => pct(v, 1, false);
  const ps = (v: number): string => pct(v, 1);
  const mo = (v: number): string => `${moneyNum(v, cur)}${moneyUnit(cur)}`;
  return [
    item('ROE', 'roe', p1),
    item('ROA', 'roa', p1),
    item('OPM(TTM)', 'opm', p1),
    item('순이익률(TTM)', 'npm', p1),
    item('부채비율', 'debt_ratio', p1),
    item('순차입금비율', 'net_debt_ratio', p1),
    item('매출 YoY', 'rev_yoy', ps),
    item('영업익 YoY', 'op_yoy', ps),
    item('EPS(TTM)', 'eps_ttm', won),
    item('BPS', 'bps', won),
    item('TTM 매출', 'ttm_revenue', mo),
    item('TTM 영업이익', 'ttm_op', mo),
    item('TTM 지배순익', 'ttm_ni_owner', mo),
  ];
}

function drawRatios(p: Panel, d: Fin): void {
  const r = d.ratios;
  if (!d.applicable || !r) {
    p.showMessage(d.applicable ? '비율 없음' : naText(d.na_reason));
    return;
  }
  append(p.body, [
    h('p', { class: 'dim' }, `기준 ${r.period} · ${r.fs_div === 'OFS' ? '별도' : '연결'} · 주식 수 ${r.shares_basis === null ? DASH : num(r.shares_basis)}`),
    kv(ratioItems(r, d.currency), { layout: 'rows' }),
    r.notes.length ? h('p', { class: 'def' }, r.notes.join(' · ')) : null,
  ]);
}

/** 부문 매출(언제나 추정 — 원문 표 best-effort, D-P4-22) */
export function drawSegments(p: Panel, d: { year: number; rows: S['SegmentView'][] }): void {
  const keys = [...new Set(d.rows.flatMap((r) => Object.keys(r.amounts)))];
  p.body.append(
    h('p', { class: 'dim' }, `${d.year} 사업보고서 원문 표 `, tag('추정')),
    table<S['SegmentView']>({
      caption: '부문 매출(억)',
      columns: [col('부문', (r) => r.segment, true), ...keys.map((k) => col<S['SegmentView']>(k, (r) => moneyNum(r.amounts[k] ?? null)))],
      rows: d.rows,
      empty: '표 없음',
    }).el,
  );
}

/** 공개 파일 → 로그인 응답 모양(분기·연간 표를 행으로) */
export function pubFinancials(d: PubFin, basis: Basis): Fin | null {
  const f = d.financials;
  if (!f) return null;
  return {
    applicable: true,
    code: d.code,
    corp_code: d.corp_code,
    basis,
    fs_div: f.fs_div,
    currency: f.currency,
    tier: f.tier,
    rows: unpack<FinRow>(basis === 'q' ? f.quarters : f.annual),
    ratios: f.ratios,
    checks: f.checks ?? { diff3m_max_pct: null, n_derived: 0, n_estimated: 0 },
  };
}

export async function mount(t: TabCtx, el: HTMLElement): Promise<void> {
  const pFin = mkPanel(t, { title: '분기·연간 재무', tier: 'public', span: 12 });
  const pRatio = mkPanel(t, { title: '재무 비율', tier: 'public', span: 6 });
  const pSeg = mkPanel(t, { title: '부문 매출', tier: 'public', span: 6 });
  tabGrid(el, pFin, pRatio, pSeg);
  pFin.setDef('분기 = 누적 차분(4분기 = 연간 − 3분기 누적). 3개월값 대조 차이 1% 초과는 추정.');
  const code = t.code;
  let basis: Basis = 'q';
  let pub: Envelope<PubFin> | null = null;

  /** 그 기준의 재무 — 로그인 API 또는 공개 파일(요청 없이 다시 그림) */
  const load = (b: Basis): Promise<Envelope<Fin>> => {
    if (t.tier === 'login') return t.get<Fin>(`stock/${code}/financials`, { basis: b, n: b === 'q' ? 12 : 4 });
    const f = pub && pubFinancials(pub.data, b);
    return f && pub ? Promise.resolve({ ...pub, quality: pub.data.financials?.quality ?? pub.quality, data: f }) : Promise.reject(noPubFin());
  };
  const loadFin = (): Promise<void> =>
    fill(
      pFin,
      () => load(basis),
      (env) => {
        drawFin(pFin, env.data);
        return 0;
      },
      HINT.fin,
    );
  filters(
    pFin,
    seg<Basis>({
      label: '재무 기준',
      options: [
        { value: 'q', label: '분기 12' },
        { value: 'a', label: '연간 4' },
      ],
      value: 'q',
      onChange: (v) => {
        basis = v;
        void loadFin();
      },
    }).el,
  );

  if (t.tier === 'public') {
    try {
      pub = await t.pub();
    } catch (err) {
      for (const p of [pFin, pRatio, pSeg]) failed(p, err, HINT.public); // 404 는 안내, 그 밖은 오류 줄
      return;
    }
    const d = pub?.data;
    if (!pub || !d?.financials) {
      for (const p of [pFin, pRatio, pSeg]) p.showMessage(NO_PUB_FIN);
      return;
    }
    await loadFin();
    pRatio.clearBody();
    drawRatios(pRatio, pubFinancials(d, 'q') as Fin);
    pRatio.setSource({ ...pub, notes: [] });
    pSeg.clearBody();
    if (d.segments) {
      drawSegments(pSeg, d.segments);
      pSeg.setSource({ ...pub, quality: d.segments.quality, notes: d.segments.notes });
    } else pSeg.showMessage('표 없음');
    return;
  }
  await Promise.all([
    loadFin(),
    fill(
      pRatio,
      () => load('q'),
      (env) => {
        drawRatios(pRatio, env.data);
        return 0;
      },
      HINT.fin,
    ),
    fill(
      pSeg,
      () => t.get<S['Segments']>(`stock/${code}/segments`),
      (env) => {
        drawSegments(pSeg, env.data);
        return 0;
      },
      HINT.report,
    ),
  ]);
}
