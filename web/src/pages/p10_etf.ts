// 페이지 10 ETF 수급(docs/p3_design.md §4.3·§6.5, metrics §4) — 자금 흐름(6정렬·유형·기간)·유형별 순유입 + 검산 ③·
// 괴리율 경고·구성종목 변동.
//
// - 로그인 등급 전부(공개판은 자물쇠, 요청 없음).
// - 순유입(설정−환매)과 투자자별 장내 순매수는 합치지 않고 따로 보인다(LP 상대 — metrics §4).
// - 분할 보정 행은 잠정(감지값)으로, 신규 상장은 순유입 없이 따로 적는다. invalid 행은 그리지 않고 수만 적는다.
// - 검산 ③(순자산 변화 = 순유입 + 가격효과)은 잔차 그대로. 보고 순자산이 없으면 '검산 불가: 사유'.
import type { components } from '../api/types.gen';
import type { PageContext, PageModule } from '../app/types';
import type { Envelope } from '../data/source';
import { clear, h } from '../ui/dom';
import { eok, eokNum, joEok, num, pct, shortDate, signClass } from '../ui/fmt';
import { type Panel, panel } from '../ui/panel';
import { checkLine, qValue, visibleRows } from '../ui/quality';
import { type Option, seg, select } from '../ui/seg';
import { centerLevel } from '../ui/svg';
import { type Column, table } from '../ui/table';
import { fill, filters, nameCell, REFRESH_MS } from './p1_market';

type S = components['schemas'];
type Flows = S['EtfFlows'];
type FlowRow = S['EtfFlowRow'];
type Mode = Flows['mode'];
type EtfType = S['EtfType'];
type Period = '1' | '5' | '20';
type ChangeKind = S['ChangeRow']['kind'];

export const MODES: readonly Option<Mode>[] = [
  { value: 'in', label: '순유입 상위' },
  { value: 'out', label: '순유출 상위' },
  { value: 'value', label: '거래대금 상위' },
  { value: 'indiv', label: '개인 순매수' },
  { value: 'foreign', label: '외국인 순매수' },
  { value: 'inst', label: '기관 순매수' },
];

/** 유형 7분류 화면 이름(metrics §8 — 응답 행의 etf_type_label 이 정본, 이 표는 필터 목록용) */
export const TYPE_LABEL: Record<EtfType, string> = {
  kr_index: '국내 대표지수',
  kr_theme: '국내 테마',
  overseas: '해외주식',
  leveraged_inverse: '레버리지·인버스',
  bond_cash: '채권·현금',
  commodity: '원자재',
  other: '기타',
};

export const CHANGE_KINDS: readonly Option<'all' | ChangeKind>[] = [
  { value: 'all', label: '전체' },
  { value: 'NEW', label: '신규편입' },
  { value: 'DROP', label: '편출' },
  { value: 'IN10', label: 'TOP10 진입' },
  { value: 'OUT10', label: 'TOP10 이탈' },
  { value: 'ADD', label: '비중확대' },
  { value: 'CUT', label: '비중축소' },
];

/** 패시브 테마 — 구성종목 변동에서 뒤로(ET tracker.py PASSIVE_THEMES) */
export const PASSIVE_THEMES: ReadonlySet<string> = new Set(['시장대표', '코스닥', '팩터', 'ESG', '기타']);

const STATUS_TAG: Record<FlowRow['status'], string | null> = {
  ok: null,
  new: '신규',
  split_adjusted: '분할 보정',
  invalid: '검산 실패',
};

function eokCol(key: string, label: string, get: (r: FlowRow) => number | null): Column<FlowRow> {
  return { key, label, cell: (r) => eokNum(get(r), 0, true), cls: (r) => signClass(get(r)) };
}

export function flowColumns(): Column<FlowRow>[] {
  return [
    {
      key: 'name',
      label: 'ETF',
      align: 'l',
      cls: () => 'nm',
      cell: (r) => {
        const tag = STATUS_TAG[r.status];
        return h(
          'span',
          null,
          nameCell(r.name, r.code),
          tag ? h('span', { class: r.status === 'split_adjusted' ? 'tag q-est' : 'tag' }, tag) : null,
          r.n_invalid > 0 ? h('span', { class: 'tag warn' }, `검산 실패 ${r.n_invalid}일 제외`) : null,
        );
      },
    },
    { key: 'type', label: '유형', align: 'l', cell: (r) => r.etf_type_label },
    { key: 'asset', label: '순자산', cell: (r) => joEok(r.net_asset) },
    {
      key: 'inflow',
      label: '순유입(억)',
      cell: (r) => qValue(eokNum(r.net_inflow, 0, true), r.quality),
      cls: (r) => signClass(r.net_inflow),
    },
    eokCol('price', '가격효과(억)', (r) => r.price_effect),
    { key: 'ret', label: '수익률', cell: (r) => pct(r.ret_pct), cls: (r) => signClass(r.ret_pct) },
    { key: 'turnover', label: '거래대금(억)', cell: (r) => eokNum(r.turnover) },
    eokCol('indiv', '개인(억)', (r) => r.indiv),
    eokCol('foreign', '외국인(억)', (r) => r.foreign),
    eokCol('inst', '기관(억)', (r) => r.inst),
  ];
}

/**
 * 검산 ③ 줄. 판정은 ETF·날짜 행마다 허용오차(metrics §8.3 — 0.005원 × 좌수 + 공표 단위)로 하므로, 합계 잔차가
 * 0 이 아니어도 실패 행이 없으면 정상이다(잔차는 그대로 보인다). 보고 순자산이 없으면 '검산 불가: 사유'.
 */
export function check3Line(c: S['Check3']): HTMLElement {
  const label = '검산 ③ 순자산 변화 − (순유입 + 가격효과)';
  if (c.residual === null) {
    return checkLine(label, null, { reason: c.net_asset_chg === null ? '보고 순자산 없음' : '입력 부족' });
  }
  const bad = c.n_failed > 0;
  return h(
    'div',
    { class: bad ? 'check fail' : 'check', data: { check: bad ? 'fail' : 'ok' } },
    `${label} 차이 `,
    h('b', null, num(c.residual, 0, true)),
    '원',
    bad ? ` (허용오차 밖 ${num(c.n_failed)}건 — 합계에서 뺐다)` : c.residual !== 0 ? ' (행마다 허용오차 안)' : '',
  );
}

export function drawTypes(p: Panel, env: Envelope<S['EtfTypes']>): number {
  const d = env.data;
  const max = Math.max(1, ...d.rows.map((r) => Math.abs(r.net_inflow)));
  const c = d.check3;
  p.body.append(
    h('p', { class: 'dim' }, `${shortDate(d.start)} ~ ${shortDate(d.end)} · 억`),
    h(
      'div',
      { class: 'levels' },
      d.rows.map((r) => centerLevel(`${r.label} (${num(r.n_etfs)})`, r.net_inflow, max, eok(r.net_inflow, 0, true))),
    ),
    check3Line(c),
    h('p', { class: 'dim' }, `검사 ${num(c.n_checked)}행 · 실패 ${num(c.n_failed)} · 불가 ${num(c.n_unavailable)}`),
  );
  const invalid = d.rows.reduce((a, r) => a + r.n_invalid, 0);
  return invalid;
}

export function drawPremium(p: Panel, env: Envelope<S['EtfPremium']>): number {
  const d = env.data;
  const { rows, invalid } = visibleRows(d.rows);
  const sorted = [...rows].sort(
    (a, b) => Number(b.warn) - Number(a.warn) || Math.abs(b.premium_pct) - Math.abs(a.premium_pct),
  );
  const navLabel = d.basis === 'inav' ? 'iNAV' : 'NAV';
  p.body.append(
    table<S['PremiumRow']>({
      caption: `${d.basis === 'inav' ? '장중 iNAV(잠정)' : '마감 NAV'} 기준 · 검사 ${num(d.n_checked)}개`,
      columns: [
        { key: 'name', label: 'ETF', align: 'l', cls: () => 'nm', cell: (r) => nameCell(r.name, r.code) },
        { key: 'price', label: '시장가', cell: (r) => num(r.price) },
        { key: 'nav', label: navLabel, cell: (r) => num(r.nav, 2) },
        {
          key: 'prem',
          label: '괴리율',
          cell: (r) => qValue(pct(r.premium_pct), r.quality, r.as_of),
          cls: (r) => (r.warn ? 'warn' : undefined),
        },
        { key: 'th', label: '경고 기준', cell: (r) => `±${num(r.threshold, 2)}%` },
      ],
      rows: sorted,
      pageSize: 20,
      empty: '괴리율 경고 없음',
    }).el,
  );
  return invalid;
}

/** 액티브 우선 · 패시브 테마 후순위(안정 정렬) */
export function sortChanges(rows: readonly S['ChangeRow'][]): S['ChangeRow'][] {
  const rank = (r: S['ChangeRow']): number => (r.is_active ? 0 : r.theme && PASSIVE_THEMES.has(r.theme) ? 2 : 1);
  return rows
    .map((r, i) => ({ r, i }))
    .sort((a, b) => rank(a.r) - rank(b.r) || a.i - b.i)
    .map((x) => x.r);
}

export function changeText(r: S['ChangeRow']): string {
  switch (r.kind) {
    case 'ADD':
    case 'CUT':
      return `수량 ${pct(r.qty_pct_adj)}(CU 보정)`;
    case 'NEW':
      return r.cur_wt === null ? '신규' : `비중 ${num(r.cur_wt, 2)}%`;
    case 'DROP':
      return r.prev_wt === null ? '제외' : `직전 비중 ${num(r.prev_wt, 2)}%`;
    default:
      return r.prev_wt === null && r.cur_wt === null
        ? r.kind_label
        : `비중 ${num(r.prev_wt, 2)} → ${num(r.cur_wt, 2)}%`;
  }
}

export function drawChanges(p: Panel, env: Envelope<S['EtfHoldingChanges']>): number {
  const rows = sortChanges(env.data.rows);
  p.body.append(
    table<S['ChangeRow']>({
      caption: `${shortDate(env.data.run_date)} 실행 · 펀드마다 최근 두 스냅 비교`,
      columns: [
        { key: 'date', label: '기준일', align: 'l', cell: (r) => `${shortDate(r.prev_asof)}→${shortDate(r.asof)}` },
        {
          key: 'fund',
          label: 'ETF',
          align: 'l',
          cls: () => 'nm',
          cell: (r) =>
            h(
              'span',
              null,
              r.fund_name ?? r.fund_id,
              r.is_active ? h('span', { class: 'tag' }, '액티브') : null,
              r.issuer ? h('small', null, r.issuer) : null,
            ),
        },
        {
          key: 'kind',
          label: '변동',
          align: 'l',
          cell: (r) => r.kind_label,
          cls: (r) => (r.kind === 'NEW' || r.kind === 'ADD' || r.kind === 'IN10' ? 'up' : 'down'),
        },
        { key: 'stock', label: '종목', align: 'l', cls: () => 'nm', cell: (r) => nameCell(r.name, r.code) },
        { key: 'chg', label: '내용', align: 'l', cell: changeText },
      ],
      rows,
      pageSize: 30,
      empty: '변동 없음',
    }).el,
  );
  return 0;
}

// ── 페이지 ─────────────────────────────────────────────────

async function mount(ctx: PageContext): Promise<void> {
  const { tier, root } = ctx;
  const pFlow = panel(tier, { title: 'ETF 자금 흐름', tier: 'login', span: 8, lock: 'etf' });
  const pType = panel(tier, { title: '유형별 순유입', tier: 'login', span: 4, lock: 'etf' });
  const pPrem = panel(tier, { title: '괴리율 경고', tier: 'login', span: 6, lock: 'etf' });
  const pHold = panel(tier, { title: '구성종목 변동', tier: 'login', span: 6, lock: 'etf_issuers' });
  root.append(pFlow.el, pType.el, pPrem.el, pHold.el);
  if (pFlow.locked) return; // 공개판: 자물쇠만, 요청 없음

  pFlow.setDef(
    '순유입(설정−환매) = Σ(상장좌수 변화 × 그날 NAV). 가격 변동으로 늘어난 순자산은 넣지 않는다(가격효과로 따로). 투자자별 순매수는 장내 매매(대부분 LP 상대)라 순유입과 다를 수 있어 따로 보인다.',
  );
  pPrem.setDef('괴리율 = (시장가 ÷ NAV − 1) × 100. 장중은 iNAV(추정) 기준이라 잠정.');

  const q = {
    mode: 'in' as Mode,
    type: 'all' as 'all' | EtfType,
    period: '5' as Period,
    basis: 'nav' as 'nav' | 'inav',
  };
  const hold = { kind: 'all' as 'all' | ChangeKind, issuer: 'all' };
  const count = h('span', { class: 'count', attrs: { 'aria-live': 'polite' } });

  const loadFlows = (): Promise<void> =>
    fill(
      pFlow,
      () =>
        ctx.source.get<Flows>('etf/flows', {
          mode: q.mode,
          type: q.type === 'all' ? null : q.type,
          period: q.period,
        }),
      (env) => {
        const d = env.data;
        const { rows, invalid } = visibleRows(d.rows);
        count.textContent = `${num(d.n_total)}개 · ${shortDate(d.start)} ~ ${shortDate(d.end)}`;
        pFlow.body.append(table({ columns: flowColumns(), rows, rowKey: (r) => r.code, empty: '해당 ETF 없음' }).el);
        if (d.new_listings.length) {
          pFlow.body.append(
            h(
              'p',
              { class: 'def', data: { section: 'new-listings' } },
              '신규 상장(순유입 없음 — 첫날 순자산): ',
              d.new_listings
                .map((n) => `${n.name ?? n.code}(${n.code}) ${shortDate(n.listed_on)} ${joEok(n.net_asset)}`)
                .join(' · '),
            ),
          );
        }
        return invalid;
      },
      'krx.daily 08:05',
    );
  const loadTypes = (): Promise<void> =>
    fill(
      pType,
      () => ctx.source.get<S['EtfTypes']>('etf/types', { period: q.period }),
      (env) => drawTypes(pType, env),
      'krx.daily 08:05',
    );
  const loadPremium = (): Promise<void> =>
    fill(
      pPrem,
      () => ctx.source.get<S['EtfPremium']>('etf/premium', { basis: q.basis }),
      (env) => drawPremium(pPrem, env),
      q.basis === 'inav' ? 'market.intraday 장중' : 'krx.daily 08:05',
    );

  const issuerBox = h('span');
  const issuers = new Set<string>();
  const drawIssuerSelect = (): void => {
    clear(issuerBox);
    issuerBox.append(
      select<string>({
        label: '운용사',
        options: [{ value: 'all', label: '전체' }, ...[...issuers].sort().map((i) => ({ value: i, label: i }))],
        value: hold.issuer,
        onChange: (v) => {
          hold.issuer = v;
          void loadChanges();
        },
      }).el,
    );
  };
  const loadChanges = (): Promise<void> =>
    fill(
      pHold,
      () =>
        ctx.source.get<S['EtfHoldingChanges']>('etf/holdings/changes', {
          kind: hold.kind === 'all' ? null : hold.kind,
          issuer: hold.issuer === 'all' ? null : hold.issuer,
        }),
      (env) => {
        const before = issuers.size;
        for (const r of env.data.rows) if (r.issuer) issuers.add(r.issuer);
        if (issuers.size !== before) drawIssuerSelect();
        return drawChanges(pHold, env);
      },
      'etf.collect 08:00',
    );

  filters(
    pFlow,
    seg<Mode>({
      label: 'ETF 정렬',
      options: MODES,
      value: q.mode,
      onChange: (v) => {
        q.mode = v;
        void loadFlows();
      },
    }).el,
    select<'all' | EtfType>({
      label: '유형',
      options: [
        { value: 'all', label: '전체' },
        ...(Object.keys(TYPE_LABEL) as EtfType[]).map((t) => ({ value: t, label: TYPE_LABEL[t] })),
      ],
      value: q.type,
      onChange: (v) => {
        q.type = v;
        void loadFlows();
      },
    }).el,
    select<Period>({
      label: '기간',
      options: [
        { value: '1', label: '1일' },
        { value: '5', label: '5일' },
        { value: '20', label: '20일' },
      ],
      value: q.period,
      onChange: (v) => {
        q.period = v;
        void loadFlows();
        void loadTypes();
      },
    }).el,
    count,
  );
  filters(
    pPrem,
    seg<'nav' | 'inav'>({
      label: '괴리율 기준',
      options: [
        { value: 'nav', label: '마감 NAV' },
        { value: 'inav', label: '장중 iNAV' },
      ],
      value: q.basis,
      onChange: (v) => {
        q.basis = v;
        void loadPremium();
      },
    }).el,
  );
  filters(
    pHold,
    select<'all' | ChangeKind>({
      label: '변동',
      options: CHANGE_KINDS,
      value: hold.kind,
      onChange: (v) => {
        hold.kind = v;
        void loadChanges();
      },
    }).el,
    issuerBox,
  );
  drawIssuerSelect();

  await Promise.all([loadFlows(), loadTypes(), loadPremium(), loadChanges()]);
  // 첫 불러오기 중에 앱이 내려갔으면(격자가 문서에서 빠짐) 주기 작업을 걸지 않는다
  if (root.isConnected) {
    ctx.every(REFRESH_MS, () => {
      void loadFlows();
      void loadTypes();
      void loadPremium();
      void loadChanges();
    });
  }
}

const page: PageModule = { mount };
export default page;
