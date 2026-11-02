// 페이지 10 ETF 수급(docs/p3_design.md §4.3·§6.5, metrics §4) — 자금 흐름(6정렬·유형·기간)·유형별 순유입 + 검산 ③·
// 괴리율 경고·구성종목 변동. P5(docs/p5_design.md §6): ETF 맵·보유 탐색·액티브 동시 편입은 ./p10/ 모듈(mountP5).
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
import { fill, filters, nameCell, slotEvery } from './p1_market';
import { mountP5, type P5Panels, TITLES as P5_TITLES } from './p10/index';
import { INDEX_LOCK, INDEX_TITLE, type IndexReviewWidget, mountIndexReview } from './p10/index_review';

// 공개 빌드에서는 P5 로그인 모듈(ETF 맵·보유 탐색·동시 편입)을 번들에서 빼고 같은 제목의 자물쇠 패널만 그린다
// (p8_stock LOGIN_VALUE 와 같은 방식 — 공개 등급은 요청 없음, 공개 JS gzip 예산 72 KB(ADR 0025). WRAP)
const LOGIN_P5 = import.meta.env.MODE === 'public' ? null : mountP5;

function lockedP5(ctx: PageContext): P5Panels {
  const { tier, root } = ctx;
  const map = panel(tier, {
    title: P5_TITLES.map,
    tier: 'login',
    span: 12,
    lock: 'etf',
  });
  const hold = panel(tier, {
    title: P5_TITLES.hold,
    tier: 'login',
    span: 6,
    lock: 'etf_issuers',
  });
  const cons = panel(tier, {
    title: P5_TITLES.cons,
    tier: 'login',
    span: 6,
    lock: 'etf_issuers',
  });
  root.append(map.el, hold.el, cons.el);
  const done = Promise.resolve();
  return { map, hold, cons, refresh: () => done, ready: done };
}

// P5 2차(묶음 IDX) '지수 정기변경' — 구성종목·컷오프 추정이라 로그인 전용. 공개 빌드는 모듈을 빼고 자물쇠만(요청 없음)
const LOGIN_INDEX = import.meta.env.MODE === 'public' ? null : mountIndexReview;

function lockedIndex(ctx: PageContext): IndexReviewWidget {
  const p = panel(ctx.tier, { title: INDEX_TITLE, tier: 'login', span: 12, lock: INDEX_LOCK });
  ctx.root.append(p.el);
  return { panel: p, load: () => Promise.resolve() };
}

type S = components['schemas'];
type Flows = S['EtfFlows'];
type FlowRow = S['EtfFlowRow'];
type Mode = Flows['mode'];
type EtfType = S['EtfType'];
type Period = '1' | '5' | '20';
type ChangeKind = S['ChangeRow']['kind'];
type StockFlows = S['EtfStockFlows'];
type StockRow = S['StockEtfFlowRow'];
type StockSort = StockFlows['sort'];

export const MODES: readonly Option<Mode>[] = [
  { value: 'in', label: '순유입 상위' },
  { value: 'out', label: '순유출 상위' },
  { value: 'value', label: '거래대금 상위' },
  { value: 'indiv', label: '개인 순매수' },
  { value: 'foreign', label: '외국인 순매수' },
  { value: 'inst', label: '기관 순매수' },
];

export const STOCK_SORTS: readonly Option<StockSort>[] = [
  { value: 'in', label: '순유입 몫 상위' },
  { value: 'out', label: '순유출 몫 상위' },
  { value: 'pct', label: '거래대금 대비' },
  { value: 'value', label: 'ETF 거래대금 몫' },
  { value: 'indiv', label: '개인' },
  { value: 'foreign', label: '외국인' },
  { value: 'inst', label: '기관' },
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
  return {
    key,
    label,
    cell: (r) => eokNum(get(r), 0, true),
    cls: (r) => signClass(get(r)),
  };
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
    {
      key: 'ret',
      label: '수익률',
      cell: (r) => pct(r.ret_pct),
      cls: (r) => signClass(r.ret_pct),
    },
    { key: 'turnover', label: '거래대금(억)', cell: (r) => eokNum(r.turnover) },
    eokCol('indiv', '개인(억)', (r) => r.indiv),
    eokCol('foreign', '외국인(억)', (r) => r.foreign),
    eokCol('inst', '기관(억)', (r) => r.inst),
  ];
}

function stockEok(key: string, label: string, get: (r: StockRow) => number | null): Column<StockRow> {
  return { key, label, cell: (r) => eokNum(get(r), 0, true), cls: (r) => signClass(get(r)) };
}

/** 종목별 ETF 수급 — ETF 기간 값 × 구성종목 비중의 종목별 합(금액은 억, 주식 수는 끝 종가로 나눈 추정) */
export function stockColumns(): Column<StockRow>[] {
  return [
    { key: 'name', label: '종목', align: 'l', cls: () => 'nm', cell: (r) => nameCell(r.name, r.code) },
    { key: 'n', label: 'ETF 수', cell: (r) => num(r.n_etfs) },
    stockEok('inflow', '순유입 몫(억)', (r) => r.inflow),
    { key: 'shares', label: '추정 주식 수', cell: (r) => num(r.inflow_shares, 0, true), cls: (r) => signClass(r.inflow_shares) },
    { key: 'pct', label: '거래대금 대비', cell: (r) => pct(r.inflow_pct), cls: (r) => signClass(r.inflow_pct) },
    { key: 'eturn', label: 'ETF 거래대금 몫(억)', cell: (r) => eokNum(r.etf_turnover) },
    { key: 'evol', label: 'ETF 거래량 몫(주)', cell: (r) => num(r.etf_volume) },
    { key: 'tpct', label: '종목 거래대금 대비', cell: (r) => pct(r.turnover_pct, 1, false) },
    { key: 'sturn', label: '종목 거래대금(억)', cell: (r) => eokNum(r.stock_turnover) },
    stockEok('indiv', '개인(억)', (r) => r.indiv),
    stockEok('foreign', '외국인(억)', (r) => r.foreign),
    stockEok('inst', '기관(억)', (r) => r.inst),
    {
      key: 'top',
      label: '주요 ETF',
      align: 'l',
      cell: (r) => r.top_etfs.map((e) => `${e.name ?? e.code} ${eok(e.inflow, 0, true)}`).join(' · '),
    },
  ];
}

/**
 * 검산 ③ 줄. 판정은 ETF·날짜 행마다 허용오차(metrics §8.3 — 0.005원 × 좌수 + 공표 단위)로 하므로, 합계 잔차가
 * 0 이 아니어도 실패 행이 없으면 정상이다(잔차는 그대로 보인다). 보고 순자산이 없으면 '검산 불가: 사유'.
 */
export function check3Line(c: S['Check3']): HTMLElement {
  const label = '검산 ③ 순자산 변화 − (순유입 + 가격효과)';
  if (c.residual === null) {
    return checkLine(label, null, {
      reason: c.net_asset_chg === null ? '보고 순자산 없음' : '입력 부족',
    });
  }
  const bad = c.n_failed > 0;
  return h(
    'div',
    {
      class: bad ? 'check fail' : 'check',
      data: { check: bad ? 'fail' : 'ok' },
    },
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
        {
          key: 'name',
          label: 'ETF',
          align: 'l',
          cls: () => 'nm',
          cell: (r) => nameCell(r.name, r.code),
        },
        { key: 'price', label: '시장가', cell: (r) => num(r.price) },
        { key: 'nav', label: navLabel, cell: (r) => num(r.nav, 2) },
        {
          key: 'prem',
          label: '괴리율',
          cell: (r) => qValue(pct(r.premium_pct), r.quality, r.as_of),
          cls: (r) => (r.warn ? 'warn' : undefined),
        },
        {
          key: 'th',
          label: '경고 기준',
          cell: (r) => `±${num(r.threshold, 2)}%`,
        },
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
        {
          key: 'date',
          label: '기준일',
          align: 'l',
          cell: (r) => `${shortDate(r.prev_asof)}→${shortDate(r.asof)}`,
        },
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
        {
          key: 'stock',
          label: '종목',
          align: 'l',
          cls: () => 'nm',
          cell: (r) => nameCell(r.name, r.code),
        },
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
  const pFlow = panel(tier, {
    title: 'ETF 자금 흐름',
    tier: 'login',
    span: 8,
    lock: 'etf',
  });
  const pType = panel(tier, {
    title: '유형별 순유입',
    tier: 'login',
    span: 4,
    lock: 'etf',
  });
  const pStock = panel(tier, {
    title: '종목별 ETF 수급',
    tier: 'login',
    span: 12,
    lock: 'etf_issuers',
  });
  const pPrem = panel(tier, {
    title: '괴리율 경고',
    tier: 'login',
    span: 6,
    lock: 'etf',
  });
  const pHold = panel(tier, {
    title: '구성종목 변동',
    tier: 'login',
    span: 6,
    lock: 'etf_issuers',
  });
  root.append(pFlow.el, pType.el, pStock.el, pPrem.el, pHold.el);
  // P5 패널(ETF 맵·보유 탐색·액티브 동시 편입 — docs/p5_design.md §6). 공개판은 자물쇠만
  const p5 = LOGIN_P5 ? LOGIN_P5(ctx) : lockedP5(ctx);
  const idx = LOGIN_INDEX ? LOGIN_INDEX(ctx) : lockedIndex(ctx);
  if (pFlow.locked || !LOGIN_ETF_P3) return; // 공개판: 자물쇠만, 요청 없음(공개 빌드는 아래 본문을 번들에서 뺀다)
  await LOGIN_ETF_P3(ctx, { pFlow, pType, pStock, pPrem, pHold, p5, idx });
}

// 로그인 본문 — 공개 빌드에서는 번들에서 뺀다(p8_stock LOGIN_VALUE 와 같은 방식, 공개 JS gzip 예산 72 KB(ADR 0025) — P5 WRAP).
// 공개 등급은 위에서 자물쇠로 돌아가므로 실행 동작은 같다.
interface EtfPanels {
  pFlow: Panel;
  pType: Panel;
  pStock: Panel;
  pPrem: Panel;
  pHold: Panel;
  p5: P5Panels;
  idx: IndexReviewWidget;
}
const LOGIN_ETF_P3 = import.meta.env.MODE === 'public' ? null : mountLogin;

async function mountLogin(ctx: PageContext, { pFlow, pType, pStock, pPrem, pHold, p5, idx }: EtfPanels): Promise<void> {
  const { root } = ctx;

  pFlow.setDef(
    '순유입(설정−환매) = Σ(상장좌수 변화 × 그날 NAV). 가격 변동으로 늘어난 순자산은 넣지 않는다(가격효과로 따로). 투자자별 순매수는 장내 매매(대부분 LP 상대)라 순유입과 다를 수 있어 따로 보인다.',
  );
  pStock.setDef(
    'ETF 기간 순유입·거래대금·투자자별 장내 순매수에 구성종목 비중(운용사 PDF)을 곱해 종목마다 더했다. 순유입 몫 = 설정·환매로 생긴 바스켓 매매 추정, 거래대금 대비 = 순유입 몫 ÷ 그 종목 기간 거래대금. 주식 수는 기간 끝 종가로 나눈 추정. 구성종목을 모르는 ETF·선물·스왑은 빠진다.',
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
  const stockQ = { sort: 'in' as StockSort };
  const stockCount = h('span', { class: 'count', attrs: { 'aria-live': 'polite' } });

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
        pFlow.body.append(
          table({
            columns: flowColumns(),
            rows,
            rowKey: (r) => r.code,
            empty: '해당 ETF 없음',
          }).el,
        );
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
  const loadStocks = (): Promise<void> =>
    fill(
      pStock,
      () => ctx.source.get<StockFlows>('etf/stocks', { sort: stockQ.sort, period: q.period }),
      (env) => {
        const d = env.data;
        const cov = d.coverage_pct === null ? '' : ` · 순유입 ${pct(d.coverage_pct, 0, false)} 덮음`;
        stockCount.textContent = `${num(d.n_total)}종목 · ETF ${num(d.n_etfs_known)}개${cov} · ${shortDate(d.start)} ~ ${shortDate(d.end)}`;
        pStock.body.append(
          table({ columns: stockColumns(), rows: d.rows, rowKey: (r) => r.code, pageSize: 30, empty: '해당 종목 없음' }).el,
        );
        return 0;
      },
      'etf.collect 08:00 · krx.daily 08:05',
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
        ...(Object.keys(TYPE_LABEL) as EtfType[]).map((t) => ({
          value: t,
          label: TYPE_LABEL[t],
        })),
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
        void loadStocks();
      },
    }).el,
    count,
  );
  filters(
    pStock,
    seg<StockSort>({
      label: '종목 정렬',
      options: STOCK_SORTS,
      value: stockQ.sort,
      onChange: (v) => {
        stockQ.sort = v;
        void loadStocks();
      },
    }).el,
    stockCount,
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

  await Promise.all([loadFlows(), loadTypes(), loadStocks(), loadPremium(), loadChanges(), p5.ready, idx.load()]);
  // 첫 불러오기 중에 앱이 내려갔으면(격자가 문서에서 빠짐) 주기 작업을 걸지 않는다
  if (root.isConnected) {
    slotEvery(ctx, () => {
      void loadFlows();
      void loadTypes();
      void loadStocks();
      void loadPremium();
      void loadChanges();
      void p5.refresh();
      void idx.load();
    });
  }
}

const page: PageModule = { mount };
export default page;
