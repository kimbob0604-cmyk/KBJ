// 페이지 4 수급·스크리닝(docs/p3_design.md §4.2·§6.5, metrics §1~§3) — 스크리너 6기준·투자자별·기관 7구분·
// 종목 상세·장중 잠정.
//
// - 로그인 등급 전부(공개판은 자물쇠, 요청 없음).
// - 스크리너는 전 거래일 확정 원장으로 돈다(장중에는 전 종목 원장이 없다). 장중 값은 '장중 잠정' 위젯에만(estimated).
// - 검산 ①(4구분 합 0)·②(7구분 합 = 기관)은 잔차를 그대로 보인다. 입력이 없으면 '검산 불가: 사유'(0 으로 그리지 않는다 —
//   R2: KIS 가 기타법인을 주지 않으면 실데이터에서 ① 은 불가). 7구분이 없으면 그 칸을 숨긴다.
// - 금액은 원 단위 → 화면에서만 억.
import type { components } from '../api/types.gen';
import type { PageContext, PageModule } from '../app/types';
import type { Envelope, Quality } from '../data/source';
import { h } from '../ui/dom';
import { DASH, eok, eokNum, kstTime, num, pct, shortDate, signClass, times } from '../ui/fmt';
import { type Panel, panel } from '../ui/panel';
import { checkLine, qValue, visibleRows } from '../ui/quality';
import { type Option, seg, select } from '../ui/seg';
import { centerLevel, s, svgBox } from '../ui/svg';
import { type Column, table } from '../ui/table';
import { fill, filters, INVESTOR_LABEL, kv, nameCell, REFRESH_MS } from './p1_market';

type S = components['schemas'];
type Screen = S['ScreenResponse'];
type ScreenRow = S['ScreenRow'];
type Mode = Screen['mode'];
type Market = Screen['market'];
type Share = Screen['share_class'];
type Period = '1' | '5' | '20';
type MinAvg = '0' | '10000000000' | '30000000000' | '100000000000';

export const MODES: readonly Option<Mode>[] = [
  { value: 'value', label: '거래대금 상위' },
  { value: 'foreign', label: '외국인 순매수' },
  { value: 'inst', label: '기관 순매수' },
  { value: 'both', label: '외인·기관 동반' },
  { value: 'streak', label: '연속 순매수' },
  { value: 'spike', label: '거래대금 급증' },
];

/** 기준 설명(metrics §3) */
export const MODE_DEF: Record<Mode, string> = {
  value: '기간 거래대금 내림차순',
  foreign: '기간 외국인 순매수 내림차순',
  inst: '기간 기관 순매수 내림차순',
  both: '기간 외국인 > 0 이고 기관 > 0 — 둘의 합 순',
  streak: '외국인·기관 연속 순매수 일수 중 큰 값(3일 이상), 같으면 순매수 합 순 — 0원·순매도·정지일에서 끊김',
  spike: '오늘 거래대금 ÷ 직전 19영업일 평균 ≥ 1.5배(기간 무관). 직전 거래일이 10일 미만이면 계산하지 않음',
};

export const INST7_LABEL: Record<string, string> = {
  fin_invest: '금융투자',
  trust: '투신',
  private_fund: '사모',
  insurance: '보험',
  bank: '은행',
  pension: '연기금',
  other_fin: '기타금융',
};

const INVESTORS = ['foreign', 'institution', 'other_corp', 'individual'] as const;

/** 억 칸(부호·색) */
function eokCol(key: string, label: string, get: (r: ScreenRow) => number | null): Column<ScreenRow> {
  return { key, label, cell: (r) => eokNum(get(r), 0, true), cls: (r) => signClass(get(r)) };
}

export function screenColumns(mode: Mode): Column<ScreenRow>[] {
  return [
    { key: 'rank', label: '#', cell: (r) => num(r.rank) },
    {
      key: 'name',
      label: '종목',
      align: 'l',
      cls: () => 'nm',
      cell: (r) =>
        h(
          'span',
          null,
          nameCell(r.name, r.code),
          (r.flags ?? []).map((f) => h('span', { class: 'tag warn' }, f)),
          r.quality === 'estimated' ? h('span', { class: 'q-badge' }, '잠정') : null,
        ),
    },
    { key: 'market', label: '시장', align: 'l', cell: (r) => r.market },
    { key: 'chg', label: '등락', cell: (r) => pct(r.chg_pct), cls: (r) => signClass(r.chg_pct) },
    { key: 'sum', label: '거래대금(억)', cell: (r) => eokNum(r.turnover_sum) },
    { key: 'avg', label: '일평균(억)', cell: (r) => eokNum(r.turnover_avg) },
    { key: 'rate', label: '회전율', cell: (r) => pct(r.turnover_rate_pct, 2, false) },
    eokCol('foreign', '외국인(억)', (r) => r.foreign),
    eokCol('inst', '기관(억)', (r) => r.inst),
    eokCol('indiv', '개인(억)', (r) => r.indiv),
    {
      key: 'streak',
      label: '연속(외/기)',
      cell: (r) => `${num(r.streak_foreign)}/${num(r.streak_inst)}`,
      cls: () => (mode === 'streak' ? 'up' : undefined),
    },
    { key: 'spike', label: '급증', cell: (r) => times(r.spike_mult) },
    {
      key: 'nh',
      label: '신고가',
      align: 'l',
      cell: (r) => (r.newhigh_label ? (NH_LABEL[r.newhigh_label] ?? r.newhigh_label) : null),
    },
  ];
}

const NH_LABEL: Record<string, string> = { hist: '역사적', w52: '52주', d60: '60일' };

export function excludedText(d: Screen): string {
  const x = d.n_excluded;
  const parts = [`${num(d.n_total)}종목`];
  if (x.flagged) parts.push(`관리·정지 제외 ${num(x.flagged)}`);
  if (x.invalid) parts.push(`검산 실패 제외 ${num(x.invalid)}`);
  if (x.below_min) parts.push(`하한 미달 ${num(x.below_min)}`);
  if (x.status_unknown) parts.push(`상태 모름(제외 못함) ${num(x.status_unknown)}`);
  return parts.join(' · ');
}

/** 빈 결과 안내 — 하한·관리종목 제외로 빠진 수를 알려 무엇을 풀면 되는지 보인다(0 건을 조용히 그리지 않는다) */
export function emptyText(d: Screen): string {
  const x = d.n_excluded;
  if (x.below_min) return `조건에 맞는 종목 없음 — 일평균 거래대금 하한 미달 ${num(x.below_min)}종목, 하한을 낮춰 보세요`;
  if (x.flagged) return `조건에 맞는 종목 없음 — 관리·정지 제외 ${num(x.flagged)}종목('관리·정지 포함'으로 볼 수 있다)`;
  return '조건에 맞는 종목 없음';
}

// ── 투자자별 순매수 + 기관 7구분 + 검산 ①② ──────────────────

export function drawInvestorBlock(p: Panel, env: Envelope<S['InvestorTotals']>): number {
  const d = env.data;
  const vals = INVESTORS.map((k) => d.by_investor[k] ?? null);
  const max = Math.max(1, ...vals.map((v) => Math.abs(v ?? 0)));
  p.body.append(
    h('p', { class: 'dim' }, `${shortDate(d.date)} · 억`),
    h(
      'div',
      { class: 'levels' },
      INVESTORS.map((k, i) => {
        const v = vals[i] ?? null;
        return v === null
          ? h(
              'div',
              { class: 'lvl' },
              h('span', null, INVESTOR_LABEL[k]),
              h('span'),
              h('span', { class: 'dim' }, `${DASH} 미제공`),
            )
          : centerLevel(INVESTOR_LABEL[k] ?? k, v, max, eok(v, 0, true));
      }),
    ),
    checkLine('검산 ① 4구분 합', d.check1_residual, {
      reason: vals.includes(null)
        ? `${INVESTORS.filter((_, i) => vals[i] === null)
            .map((k) => INVESTOR_LABEL[k])
            .join('·')} 미제공`
        : '입력 부족',
      unit: '원',
    }),
  );
  if (d.inst7) {
    // 7구분이 있을 때만 그린다(없으면 숨김 — 빈 값을 0 으로 그리지 않는다, metrics §2)
    const entries = Object.entries(d.inst7);
    const m7 = Math.max(1, ...entries.map(([, v]) => Math.abs(v)));
    p.body.append(
      h(
        'div',
        { class: 'inst7', data: { section: 'inst7' } },
        h('p', { class: 'dim' }, '기관 세부 7구분 · 억'),
        h(
          'div',
          { class: 'levels' },
          entries.map(([k, v]) => centerLevel(INST7_LABEL[k] ?? k, v, m7, eok(v, 0, true))),
        ),
        checkLine('검산 ② 7구분 합 − 기관', d.check2_residual, { reason: '기관 합계 없음', unit: '원' }),
      ),
    );
  }
  p.setSource({ ...env, notes: [...new Set([...env.notes, ...d.notes])] });
  return 0;
}

// ── 종목 상세(20일 거래대금 막대 + 누적 순매수 선) ───────────

const LINES: readonly { key: 'foreign' | 'inst' | 'indiv'; label: string; stroke: string }[] = [
  { key: 'foreign', label: '외국인', stroke: 'var(--accent)' },
  { key: 'inst', label: '기관', stroke: 'var(--warn)' },
  { key: 'indiv', label: '개인', stroke: 'var(--dim)' },
];

/** 누적 합 — 값이 없는 날은 null(선이 끊긴다), 그 뒤는 이어서 더한다 */
export function cumulative(values: readonly (number | null)[]): (number | null)[] {
  let acc = 0;
  return values.map((v) => {
    if (v === null) return null;
    acc += v;
    return acc;
  });
}

export function detailChart(days: readonly S['DayFlow'][], name: string): SVGSVGElement {
  const W = 900;
  const H = 200;
  const top = 14;
  const base = H - 22;
  const box = svgBox(`0 0 ${W} ${H}`, `${name} ${days.length}일 거래대금과 누적 순매수`);
  const n = Math.max(days.length, 1);
  const step = (W - 40) / n;
  const tv = days.map((d) => d.turnover ?? 0);
  const tMax = Math.max(1, ...tv);
  days.forEach((d, i) => {
    if (d.turnover === null) return;
    const bh = (d.turnover / tMax) * (base - top);
    s(
      'rect',
      { x: 20 + i * step + step * 0.15, y: base - bh, width: step * 0.7, height: bh, fill: 'var(--line)' },
      box,
    );
  });
  const series = LINES.map((l) => ({ ...l, vals: cumulative(days.map((d) => d[l.key])) }));
  const cMax = Math.max(1, ...series.flatMap((x) => x.vals.map((v) => Math.abs(v ?? 0))));
  const mid = (top + base) / 2;
  const y = (v: number): number => mid - (v / cMax) * ((base - top) / 2);
  s('line', { x1: 20, x2: W - 20, y1: mid, y2: mid, stroke: 'var(--line)', 'stroke-dasharray': '2 3' }, box);
  series.forEach((ln, li) => {
    const pts = ln.vals
      .map((v, i) => (v === null ? null : `${(20 + i * step + step / 2).toFixed(1)},${y(v).toFixed(1)}`))
      .filter((x): x is string => x !== null);
    if (pts.length) s('polyline', { points: pts.join(' '), fill: 'none', stroke: ln.stroke, 'stroke-width': 1.6 }, box);
    s('text', { x: 24 + li * 70, y: 11, fill: ln.stroke, 'font-size': 11 }, box, ln.label);
  });
  const first = days[0];
  const last = days.at(-1);
  if (first && last)
    s(
      'text',
      { x: 20, y: H - 6, fill: 'var(--dim)', 'font-size': 10 },
      box,
      `${shortDate(first.date)} ~ ${shortDate(last.date)} · 막대 = 거래대금, 선 = 누적 순매수`,
    );
  return box;
}

export function drawDetail(p: Panel, env: Envelope<S['StockFlowDetail']>): number {
  const d = env.data;
  const { rows, invalid } = visibleRows(d.days);
  const name = d.name ?? d.code;
  const c = d.cumulative;
  const est = rows.some((r) => r.quality === 'estimated');
  const q: Quality = est ? 'estimated' : 'ok';
  p.body.append(
    kv([
      ['종목', `${name} ${d.code}`],
      ['기간', `${rows.length}일`],
      ...INVESTORS.map((k): [string, Node | string | null] => {
        const key = k === 'institution' ? 'inst' : k === 'individual' ? 'indiv' : k;
        const v = c[key] ?? null;
        return [`누적 ${INVESTOR_LABEL[k] ?? k}`, qValue(eok(v, 0, true), q, null, signClass(v))];
      }),
    ]),
    detailChart(rows, name),
  );
  const ck = d.checks;
  const total = rows.length;
  const line = (
    label: string,
    failedN: number | undefined,
    unavail: number | undefined,
    reason: string,
  ): HTMLElement => {
    if (total > 0 && (unavail ?? 0) >= total)
      return h('div', { class: 'check', data: { check: 'unavailable' } }, `${label} 검산 불가: ${reason}`);
    const bad = (failedN ?? 0) > 0;
    return h(
      'div',
      { class: bad ? 'check fail' : 'check', data: { check: bad ? 'fail' : 'ok' } },
      `${label} 실패 `,
      h('b', null, num(failedN ?? null)),
      `일 · 불가 ${num(unavail ?? null)}일`,
    );
  };
  p.body.append(
    line('검산 ①', ck.c1_failed, ck.c1_unavailable, '기타법인 미제공'),
    line('검산 ②', ck.c2_failed, ck.c2_unavailable, '기관 7구분 미제공'),
  );
  return invalid;
}

// ── 장중 잠정 ──────────────────────────────────────────────

export function drawIntraday(p: Panel, env: Envelope<S['IntradayFlows']>, names: Map<string, string>): number {
  const d = env.data;
  for (const r of d.turnover_rank) if (r.name) names.set(r.code, r.name);
  const est = (text: string): HTMLElement | null => qValue(text, 'estimated');
  const topCols = (label: string): Column<S['IntradayRow']>[] => [
    { key: 'rank', label: '#', cell: (r) => num(r.rank) },
    { key: 'name', label: '종목', align: 'l', cls: () => 'nm', cell: (r) => nameCell(names.get(r.code), r.code) },
    { key: 'v', label: `${label}(억)`, cell: (r) => est(eokNum(r.value, 0, true)), cls: (r) => signClass(r.value) },
  ];
  const box = (t: HTMLElement): HTMLElement => h('div', { class: 'span-4' }, t);
  p.body.append(
    h('p', { class: 'dim' }, `${kstTime(d.slot)} 슬롯 · 장중 잠정 — 15:35 마감 뒤 확정`),
    h(
      'div',
      { class: 'grid' },
      box(
        table({
          caption: '외국인 가집계 상위',
          columns: topCols('외국인'),
          rows: d.top_foreign,
          pageSize: 10,
          empty: '없음',
        }).el,
      ),
      box(
        table({ caption: '기관 가집계 상위', columns: topCols('기관'), rows: d.top_inst, pageSize: 10, empty: '없음' })
          .el,
      ),
      box(
        table<S['RankItem']>({
          caption: '거래대금 순위',
          columns: [
            { key: 'rank', label: '#', cell: (r) => num(r.rank) },
            { key: 'name', label: '종목', align: 'l', cls: () => 'nm', cell: (r) => nameCell(r.name, r.code) },
            { key: 'mkt', label: '시장', align: 'l', cell: (r) => r.market },
            { key: 't', label: '거래대금(억)', cell: (r) => est(eokNum(r.turnover)) },
            { key: 'c', label: '등락', cell: (r) => est(pct(r.chg_pct)), cls: (r) => signClass(r.chg_pct) },
          ],
          rows: d.turnover_rank,
          pageSize: 10,
          empty: '없음',
        }).el,
      ),
    ),
  );
  return 0;
}

// ── 페이지 ─────────────────────────────────────────────────

async function mount(ctx: PageContext): Promise<void> {
  const { tier, root } = ctx;
  const lock = 'investor';
  const pScr = panel(tier, { title: '종목 스크리너', tier: 'login', span: 9, lock });
  const pInv = panel(tier, { title: '투자자별 순매수', tier: 'login', span: 3, lock });
  const pDet = panel(tier, { title: '종목 상세', tier: 'login', span: 12, lock, src: '표에서 종목을 고르세요' });
  const pIntra = panel(tier, { title: '장중 잠정', tier: 'login', span: 12, lock });
  root.append(pScr.el, pInv.el, pDet.el, pIntra.el);
  if (pScr.locked) return; // 공개판: 자물쇠만, 요청 없음

  pInv.setDef(
    '기관 = 금융투자+투신+사모+보험+은행+연기금+기타금융. 세부 합은 기관 합계와 같아야 한다(검산 ②). 4구분 합은 0(검산 ①).',
  );
  pDet.setDef('막대 = 일별 거래대금, 선 = 기간 누적 순매수(외국인·기관·개인). 검산 실패(invalid) 날은 그리지 않는다.');
  pDet.showMessage('표에서 종목을 고르세요');

  const names = new Map<string, string>();
  const q = {
    mode: 'value' as Mode,
    market: 'all' as Market,
    period: '5' as Period,
    min: '30000000000' as MinAvg,
    share: 'common' as Share,
    flagged: false,
  };
  let selected: string | null = null;
  const count = h('span', { class: 'count', attrs: { 'aria-live': 'polite' } });

  const loadDetail = (code: string): Promise<void> =>
    fill(
      pDet,
      () => ctx.source.get<S['StockFlowDetail']>(`flows/stock/${code}`, { days: 20 }),
      (env) => drawDetail(pDet, env),
      'market.close_collect 15:35',
    );

  const loadScreen = (): Promise<void> =>
    fill(
      pScr,
      () =>
        ctx.source.get<Screen>('flows/screen', {
          mode: q.mode,
          market: q.market,
          period: q.period,
          min_avg_turnover: q.min,
          share_class: q.share,
          include_flagged: q.flagged,
        }),
      (env) => {
        const { rows, invalid } = visibleRows(env.data.rows);
        for (const r of rows) if (r.name) names.set(r.code, r.name);
        count.textContent = excludedText(env.data);
        pScr.body.append(
          table({
            caption: `${MODES.find((m) => m.value === q.mode)?.label ?? q.mode} · ${q.mode === 'spike' ? '오늘' : `${q.period}일`} · 기준일 ${shortDate(env.data.date)}`,
            columns: screenColumns(q.mode),
            rows,
            rowKey: (r) => r.code,
            selected,
            onSelect: (r) => {
              selected = r.code;
              void loadDetail(r.code);
            },
            empty: emptyText(env.data),
          }).el,
        );
        pScr.setDef(`${MODE_DEF[q.mode]}. 일평균 거래대금 하한·관리종목 제외는 필터로. 우선주는 보통주와 따로.`);
        return invalid;
      },
      'market.close_collect 15:35',
    );

  const flaggedBox = h('input', {
    attrs: { type: 'checkbox' },
    on: {
      change: () => {
        q.flagged = flaggedBox.checked;
        void loadScreen();
      },
    },
  });
  filters(
    pScr,
    seg<Mode>({
      label: '스크리닝 기준',
      options: MODES,
      value: q.mode,
      onChange: (v) => {
        q.mode = v;
        void loadScreen();
      },
    }).el,
    select<Market>({
      label: '시장',
      options: [
        { value: 'all', label: '전체' },
        { value: 'KOSPI', label: '코스피' },
        { value: 'KOSDAQ', label: '코스닥' },
      ],
      value: q.market,
      onChange: (v) => {
        q.market = v;
        void loadScreen();
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
        void loadScreen();
      },
    }).el,
    select<MinAvg>({
      label: '일평균 거래대금 ≥',
      options: [
        { value: '0', label: '제한 없음' },
        { value: '10000000000', label: '100억' },
        { value: '30000000000', label: '300억' },
        { value: '100000000000', label: '1,000억' },
      ],
      value: q.min,
      onChange: (v) => {
        q.min = v;
        void loadScreen();
      },
    }).el,
    select<Share>({
      label: '종류',
      options: [
        { value: 'common', label: '보통주' },
        { value: 'pref', label: '우선주' },
      ],
      value: q.share,
      onChange: (v) => {
        q.share = v;
        void loadScreen();
      },
    }).el,
    h('label', null, flaggedBox, '관리·정지 포함'),
    count,
  );

  const loadInvestors = (): Promise<void> =>
    fill(
      pInv,
      () => ctx.source.get<S['InvestorTotals']>('flows/investors'),
      (env) => drawInvestorBlock(pInv, env),
      'market.close_collect 15:35',
    );
  const loadIntraday = (): Promise<void> =>
    fill(
      pIntra,
      () => ctx.source.get<S['IntradayFlows']>('flows/intraday'),
      (env) => drawIntraday(pIntra, env, names),
      'flows.intraday 장중 10분',
    );

  await Promise.all([loadScreen(), loadInvestors()]);
  await loadIntraday(); // 종목 이름을 스크리너에서 빌려 쓴다
  // 첫 불러오기 중에 앱이 내려갔으면(격자가 문서에서 빠짐) 주기 작업을 걸지 않는다
  if (root.isConnected) {
    ctx.every(REFRESH_MS, () => {
      void loadScreen();
      void loadInvestors();
      void loadIntraday();
    });
  }
}

const page: PageModule = { mount };
export default page;
