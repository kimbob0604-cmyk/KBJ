// 페이지 2 신고가 보드(docs/p3_design.md §4.1·§6.5) — 신고가 종목·근접·섹터 집계·테마 히트맵·탐지·랭킹.
//
// - 로그인 등급 전부(공개판은 자물쇠 — 시세 원천이 모두 KRX 계열). 공개 빌드는 아무것도 요청하지 않는다.
// - 보드 응답의 금액(turnover·mktcap·*_eok)은 **억원**(ET 산출 그대로 — API notes 에도 적혀 있다). 원장 외국인·기관
//   (`flows`)만 원 단위다.
// - 신고가 3축(ADR 0017): 역사적 = 상장 이후 전체 · 52주 = 달력 52주 · 120일 = 120거래일. 역사적을 특정일 '이후'로
//   내세우지 않는다 — 이력이 상장일에 닿지 않은 종목 수·원천 바닥 기준 수만 품질 메모(`hist_notes`)로 적는다.
// - KIS 마감값은 잠정(estimated) — 다음 영업일 08:40 KRX 확정으로 덮인다(board.confirm).
import type { components } from '../api/types.gen';
import type { PageContext, PageModule } from '../app/types';
import type { Envelope } from '../data/source';
import { clear, h } from '../ui/dom';
import { DASH, eokNum, joEok, num, pct, signClass, times, WON_PER_EOK } from '../ui/fmt';
import { type Panel, panel } from '../ui/panel';
import { seg, select } from '../ui/seg';
import { heatColor, level } from '../ui/svg';
import { type Column, table } from '../ui/table';
import { failed, fill, filters, nameCell, REFRESH_MS } from './p1_market';

type S = components['schemas'];
type Newhigh = S['BoardNewhigh'];

/** 보드 행(엔진 dict — 쓰는 키만) */
export interface BoardRow {
  code: string;
  name?: string | null;
  market?: string | null;
  sector?: string | null;
  chg_pct?: number | null;
  turnover?: number | null;
  mktcap?: number | null;
  vol_mult?: number | null;
  label?: string | null;
  status?: string | null;
  suspect?: boolean;
  gap?: Record<string, number | null>;
  near_kind?: string | null;
  near_gap?: number | null;
  near_narrow5?: number | null;
  resistance_label?: Record<string, string | null>;
  /** 두 기준을 이름으로(엔진 build — label·gap 은 엔진 기본 기준(`basis`)의 값) */
  close_basis?: BasisView | null;
  high_basis?: BasisView | null;
}

/** 한 기준의 라벨·갭 */
export interface BasisView {
  label?: string | null;
  gap?: Record<string, number | null> | null;
}

const DEFAULT_LABELS: Record<string, string> = { hist: '역사적', w52: '52주', d120: '120일' };

export const EVENT_LABEL: Record<string, string> = {
  material_giveback: '재료 반납',
  volume_anomaly: '거래량 이상',
  multi_label_high: '복수 기준 신고가',
  proximity_cluster: '근접 클러스터',
  breakout_fail: '돌파 실패',
};

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** 엔진 dict 목록 → 코드가 있는 행만(모양이 다르면 버린다 — 지어내지 않는다) */
export function rowsOf<T extends { code: string }>(list: unknown): T[] {
  return Array.isArray(list) ? list.filter((r): r is T => isObj(r) && typeof r.code === 'string') : [];
}

const n = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);

/** 억원 값 → '1,234억'/'1.2조' */
const eokVal = (v: number | null | undefined): string => (n(v) === null ? DASH : joEok((v as number) * WON_PER_EOK));

// ── 신고가 표·근접 표 ──────────────────────────────────────

type Basis = 'close' | 'high';
type Kind = 'all' | 'hist' | 'w52' | 'd120';
type MinTurn = '0' | '50' | '100' | '300';

/**
 * 행의 그 기준(종가·고가) 라벨·갭. 엔진 행의 `label`·`gap` 은 엔진 기본 기준 값이라, 고가 기준을 고르면
 * `high_basis` 를 읽어야 한다(API 는 그 기준 라벨로 거르기만 하고 행을 바꾸지 않는다). 기준 칸이 없으면 기본 값.
 */
export function basisView(r: BoardRow, basis?: Basis): BasisView {
  const b = basis ? r[`${basis}_basis`] : null;
  return isObj(b) ? { label: str(b.label), gap: isObj(b.gap) ? (b.gap as BasisView['gap']) : null } : r;
}

/** 돌파폭(%) = 기준 최고가 대비 넘어선 폭 = −갭(엔진 갭은 이미 넘겼으면 음수). 모르면 null */
export function breakout(r: BoardRow, basis?: Basis): number | null {
  const v = basisView(r, basis);
  const g = v.label ? n(v.gap?.[v.label]) : null;
  return g === null ? null : -g;
}

export function achievedColumns(d: Newhigh): Column<BoardRow>[] {
  const labels = d.labels ?? DEFAULT_LABELS;
  const flows = d.flows ?? {};
  // 응답이 거른 기준(filter.basis). 신규·이어감은 엔진 기본 기준(d.basis)으로만 셌다 — 다른 기준이면 보이지 않는다
  const basis: Basis = d.filter?.basis === 'high' ? 'high' : 'close';
  const statusOk = (d.basis ?? 'close') === basis;
  const labelOf = (r: BoardRow): string | null => basisView(r, basis).label ?? null;
  return [
    { key: 'name', label: '종목', align: 'l', cell: (r) => nameCell(r.name, r.code), cls: () => 'nm' },
    { key: 'market', label: '시장', align: 'l', cell: (r) => r.market ?? null },
    {
      key: 'label',
      label: '기준',
      align: 'l',
      cell: (r) => {
        const l = labelOf(r);
        return l ? (labels[l] ?? l) : null;
      },
    },
    {
      key: 'status',
      label: '상태',
      align: 'l',
      cell: (r) =>
        h(
          'span',
          statusOk ? null : { attrs: { title: `신규·이어감은 ${d.basis === 'high' ? '고가' : '종가'} 기준으로만 센다` } },
          (statusOk ? r.status : null) ?? DASH,
          r.suspect ? h('span', { class: 'tag warn' }, '분할 의심') : null,
        ),
    },
    { key: 'chg', label: '등락', cell: (r) => pct(r.chg_pct), cls: (r) => signClass(r.chg_pct) },
    { key: 'gap', label: '돌파폭', cell: (r) => pct(breakout(r, basis)) },
    { key: 'turnover', label: '거래대금(억)', cell: (r) => num(r.turnover) },
    { key: 'vol', label: '거래량 배수', cell: (r) => times(r.vol_mult) },
    {
      key: 'foreign',
      label: '외국인(억)',
      cell: (r) => eokNum(flows[r.code]?.foreign, 0, true),
      cls: (r) => signClass(flows[r.code]?.foreign),
    },
    {
      key: 'inst',
      label: '기관(억)',
      cell: (r) => eokNum(flows[r.code]?.inst, 0, true),
      cls: (r) => signClass(flows[r.code]?.inst),
    },
  ];
}

export function proximityColumns(d: Newhigh): Column<BoardRow>[] {
  const labels = d.labels ?? DEFAULT_LABELS;
  return [
    { key: 'name', label: '종목', align: 'l', cell: (r) => nameCell(r.name, r.code), cls: () => 'nm' },
    {
      key: 'kind',
      label: '기준',
      align: 'l',
      cell: (r) => (r.near_kind ? (labels[r.near_kind] ?? r.near_kind) : null),
    },
    { key: 'gap', label: '남은 폭', cell: (r) => pct(r.near_gap, 2, false) },
    {
      key: 'narrow',
      label: '5일 변화',
      cell: (r) => pct(r.near_narrow5),
      cls: (r) => signClass(-(r.near_narrow5 ?? 0)),
    },
    {
      key: 'res',
      label: '저항',
      align: 'l',
      cell: (r) => (r.near_kind ? (r.resistance_label?.[r.near_kind] ?? null) : null),
    },
  ];
}

/** 신고가 개수 줄: '역사적 12 · 52주 22 · 120일 32' */
export function countsText(d: Newhigh, basis: Basis): string {
  const labels = d.labels ?? DEFAULT_LABELS;
  const counts = (basis === 'high' ? d.counts_high : d.counts_close) ?? {};
  const order = d.priority?.length ? d.priority : Object.keys(labels);
  return order.map((k) => `${labels[k] ?? k} ${num(counts[k])}`).join(' · ');
}

/** 축 기준 한 줄(ADR 0017) — 특정일 표기 없이 */
export const AXES_DEF =
  '역사적 = 상장 이후, 52주 = 달력 52주, 120일 = 120거래일 — 판정일 당일 제외 최고가를 초과(엄격)';

/** 역사적 신고가 품질 메모(이력이 상장일에 닿지 않은 종목·원천 바닥 기준 종목). 없으면 빈 목록 */
export function histNotes(d: Newhigh): string[] {
  return (d.hist_notes ?? []).filter((x): x is string => typeof x === 'string' && x.length > 0);
}

/** 기준 설명 — 축 기준 한 줄 + 역사적 품질 메모(있을 때만) */
export function newhighDef(d: Newhigh): string {
  const below = n(d.n_below_mktcap);
  const parts = [
    AXES_DEF,
    ...histNotes(d),
    d.min_mktcap_eok ? `시총 ${num(d.min_mktcap_eok)}억 미만${below === null ? '' : ` ${num(below)}종목`} 제외` : null,
    n(d.n_suspect) ? `분할 의심 ${num(d.n_suspect)}종목` : null,
  ];
  return parts.filter(Boolean).join('. ');
}

// ── 섹터 집계·히트맵 ───────────────────────────────────────

interface Agg {
  name: string;
  n_newhigh: number | null;
  n_near: number | null;
  chg_pct: number | null;
}

export function drawSectorCounts(p: Panel, env: Envelope<S['BoardSectors']>): number {
  const rows: Agg[] = (env.data.sectors ?? []).filter(isObj).map((s) => ({
    name: str(s.name) ?? DASH,
    n_newhigh: n(s.n_newhigh),
    n_near: n(s.n_near),
    chg_pct: n(s.chg_pct),
  }));
  rows.sort((a, b) => (b.n_newhigh ?? -1) - (a.n_newhigh ?? -1) || (b.n_near ?? -1) - (a.n_near ?? -1));
  const top = rows.slice(0, 15);
  const max = Math.max(1, ...top.map((r) => r.n_newhigh ?? 0));
  if (top.length === 0) {
    p.showMessage('섹터 집계 없음');
    return 0;
  }
  p.body.append(
    h(
      'div',
      { class: 'levels' },
      top.map((r) =>
        level(
          r.name,
          ((r.n_newhigh ?? 0) / max) * 100,
          `${num(r.n_newhigh)} · 근접 ${num(r.n_near)} · ${pct(r.chg_pct)}`,
        ),
      ),
    ),
  );
  return 0;
}

type HeatAxis = 'theme' | 'sector';

export function drawHeat(
  p: Panel,
  env: Envelope<S['BoardSectors']>,
  axis: HeatAxis,
  labels: Record<string, string>,
): number {
  const groups = (axis === 'theme' ? env.data.heatmap_theme : env.data.heatmap_sector) ?? [];
  const shown = groups.filter(isObj).slice(0, 8);
  if (shown.length === 0) {
    p.showMessage(axis === 'theme' ? '테마 히트맵 없음 — config/knowledge 테마 사전' : '섹터 히트맵 없음');
    return 0;
  }
  for (const g of shown) {
    const cells = Array.isArray(g.cells) ? g.cells.filter(isObj).slice(0, 12) : [];
    const name = str(g.group) ?? str(g.key) ?? DASH;
    p.body.append(
      h(
        'p',
        { class: 'def' },
        h('b', null, name),
        ` ${pct(n(g.chg_pct))} · ${eokVal(n(g.turnover))} · ${num(n(g.n_total))}종목`,
      ),
      h(
        'div',
        { class: 'heat', attrs: { role: 'list', 'aria-label': `${name} 종목 등락률` } },
        cells.map((c) => {
          const nh = str(c.newhigh);
          return h(
            'div',
            {
              class: 'cell',
              css: { background: heatColor(n(c.chg_pct)) },
              attrs: { role: 'listitem' },
              data: { code: str(c.code) ?? '' },
            },
            str(c.name) ?? str(c.code) ?? DASH,
            h('b', null, pct(n(c.chg_pct))),
            h('span', null, nh ? `신고가 ${labels[nh] ?? nh}` : eokVal(n(c.turnover))),
          );
        }),
      ),
    );
  }
  return 0;
}

// ── 탐지 ───────────────────────────────────────────────────

interface Ev {
  type: string;
  theme: string | null;
  tickers: string[];
  severity: number | null;
  evidence: Record<string, unknown>;
}

export function evidenceText(e: Ev, labels: Record<string, string>): string {
  const x = e.evidence;
  switch (e.type) {
    case 'material_giveback':
      return `고가 대비 반납 ${num(n(x.giveback_pp), 2)}%p (고가 ${pct(n(x.high_chg_pct))} → 종가 ${pct(n(x.chg_pct))})`;
    case 'proximity_cluster':
      return `근접 ${num(n(x.n))}종목`;
    case 'breakout_fail': {
      const k = str(x.kind);
      return `${k ? (labels[k] ?? k) : ''} 갭 ${pct(n(x.gap_prev), 2, false)} → ${pct(n(x.gap_now), 2, false)}`.trim();
    }
    case 'multi_label_high':
      return Array.isArray(x.labels)
        ? x.labels.map((l) => (typeof l === 'string' ? (labels[l] ?? l) : '')).join('·')
        : DASH;
    default:
      return Object.entries(x)
        .filter(([, v]) => typeof v === 'number' || typeof v === 'string')
        .map(([k, v]) => `${k} ${typeof v === 'number' ? num(v, 2) : String(v)}`)
        .join(' · ');
  }
}

export function eventsOf(env: Envelope<S['BoardEvents']>): Ev[] {
  return (env.data.events ?? []).filter(isObj).map((e) => ({
    type: str(e.type) ?? '?',
    theme: str(e.theme),
    tickers: Array.isArray(e.tickers) ? e.tickers.filter((t): t is string => typeof t === 'string') : [],
    severity: n(e.severity),
    evidence: isObj(e.evidence) ? e.evidence : {},
  }));
}

// ── 랭킹 ───────────────────────────────────────────────────

interface Board {
  key: string;
  label: string;
  kind: 'sector' | 'stock';
  raw: Record<string, unknown>;
}

export function boardsOf(d: S['BoardRankings']): Board[] {
  const out: Board[] = [];
  for (const b of (d.sector_boards ?? []).filter(isObj)) {
    out.push({
      key: `s:${str(b.key) ?? ''}`,
      label: `섹터 ${str(b.label) ?? str(b.key) ?? ''}`,
      kind: 'sector',
      raw: b,
    });
  }
  for (const b of (d.stock_boards ?? []).filter(isObj)) {
    out.push({ key: `t:${str(b.key) ?? ''}`, label: str(b.title) ?? str(b.key) ?? '', kind: 'stock', raw: b });
  }
  return out;
}

export function rankingTable(b: Board): HTMLElement {
  if (b.kind === 'sector') {
    const rows = (Array.isArray(b.raw.sectors) ? b.raw.sectors : []).filter(isObj);
    return table<Record<string, unknown>>({
      caption: str(b.raw.ret_label) ?? b.label,
      columns: [
        { key: 'rank', label: '순위', cell: (r) => num(n(r.rank)) },
        { key: 'name', label: '섹터', align: 'l', cell: (r) => str(r.name) },
        { key: 'ret', label: '상승률', cell: (r) => pct(n(r.ret_raw)), cls: (r) => signClass(n(r.ret_raw)) },
        { key: 'n', label: '종목 수', cell: (r) => num(n(r.n)) },
        {
          key: 'delta',
          label: '순위 변화',
          cell: (r) => num(n(r.rank_delta), 0, true),
          cls: (r) => signClass(n(r.rank_delta)),
        },
        {
          key: 'top',
          label: '대표 종목',
          align: 'l',
          cell: (r) => {
            const t = Array.isArray(r.top) ? r.top.find(isObj) : undefined;
            return t ? `${str(t.name) ?? ''} ${str(t.ret) ?? ''}`.trim() : null;
          },
        },
      ],
      rows,
      pageSize: 20,
    }).el;
  }
  const cols = (Array.isArray(b.raw.columns) ? b.raw.columns : []).filter(isObj);
  const rows = (Array.isArray(b.raw.rows) ? b.raw.rows : []).filter(isObj);
  return table<Record<string, unknown>>({
    caption: b.label,
    columns: [
      { key: 'rank', label: '순위', cell: (r) => num(n(r.rank)) },
      {
        key: 'name',
        label: '종목',
        align: 'l',
        cell: (r) => nameCell(str(r.name), str(r.code) ?? ''),
        cls: () => 'nm',
      },
      ...cols.map((c) => {
        const key = str(c.key) ?? '';
        return {
          key,
          label: str(c.label) ?? key,
          // 엔진이 형식화한 문구(억·조·%)를 그대로 — 부호 색은 원값으로
          cell: (r: Record<string, unknown>) => (isObj(r.cells) ? str(r.cells[key]) : null),
          cls: (r: Record<string, unknown>) =>
            c.kind === 'pct' && isObj(r.cells_raw) ? signClass(n(r.cells_raw[key])) : undefined,
        };
      }),
    ],
    rows,
    pageSize: 20,
  }).el;
}

// ── 페이지 ─────────────────────────────────────────────────

async function mount(ctx: PageContext): Promise<void> {
  const { tier, root } = ctx;
  const lock = 'board';
  const pNh = panel(tier, { title: '신고가 종목', tier: 'login', span: 8, lock });
  const pNear = panel(tier, { title: '신고가 근접', tier: 'login', span: 4, lock });
  const pSec = panel(tier, { title: '섹터 집계', tier: 'login', span: 4, lock });
  const pHeat = panel(tier, { title: '테마 히트맵', tier: 'login', span: 8, lock });
  const pEv = panel(tier, { title: '탐지 이벤트', tier: 'login', span: 6, lock });
  const pRank = panel(tier, { title: '랭킹', tier: 'login', span: 6, lock });
  const pUs = panel(tier, {
    title: '간밤 미국 신고가',
    tier: 'login',
    span: 12,
    lock,
    pending: 'P5',
    pendingNote: 'us.universe·us.eod',
  });
  root.append(pNh.el, pNear.el, pSec.el, pHeat.el, pEv.el, pRank.el, pUs.el);
  if (pNh.locked) return; // 공개판: 자물쇠만, 요청 없음

  pNear.setDef('남은 폭 = (기준 최고가 − 현재가) ÷ 기준 최고가. 5일 변화가 음수면 좁혀지는 중.');
  const hint = 'board.daily 16:20';
  const q = { basis: 'close' as Basis, kind: 'all' as Kind, min: '0' as MinTurn };
  let labels: Record<string, string> = DEFAULT_LABELS;
  const names = new Map<string, string>();
  const themeNames = new Map<string, string>();

  const count = h('span', { class: 'count', attrs: { 'aria-live': 'polite' } });
  const loadNewhigh = (): Promise<void> =>
    fill(
      pNh,
      () =>
        ctx.source.get<Newhigh>('board/newhigh', {
          basis: q.basis,
          kind: q.kind === 'all' ? null : q.kind,
          min_turnover_eok: q.min === '0' ? null : q.min,
        }),
      (env) => {
        const d = env.data;
        labels = d.labels ?? DEFAULT_LABELS;
        const achieved = rowsOf<BoardRow>(d.achieved);
        const near = rowsOf<BoardRow>(d.proximity).sort((a, b) => (a.near_gap ?? Infinity) - (b.near_gap ?? Infinity));
        for (const r of [...achieved, ...near]) if (r.name) names.set(r.code, r.name);
        count.textContent = countsText(d, q.basis);
        pNh.body.append(
          table({
            caption: `${q.basis === 'high' ? '고가' : '종가'} 기준 · 거래대금 순`,
            columns: achievedColumns(d),
            rows: achieved,
            rowKey: (r) => r.code,
            empty: '조건에 맞는 신고가 종목 없음',
          }).el,
        );
        pNh.setDef(newhighDef(d));
        pNear.clearBody();
        pNear.body.append(
          table({ columns: proximityColumns(d), rows: near, pageSize: 20, empty: '근접 종목 없음' }).el,
        );
        pNear.setSource(env);
        return 0;
      },
      hint,
      [pNear], // 근접 표도 같은 응답 — 실패하면 두 패널 모두에 오류 줄(직전 표를 그대로 두지 않는다)
    );

  filters(
    pNh,
    seg<Basis>({
      label: '신고가 기준',
      options: [
        { value: 'close', label: '종가 기준' },
        { value: 'high', label: '고가 기준' },
      ],
      value: q.basis,
      onChange: (v) => {
        q.basis = v;
        void loadNewhigh();
      },
    }).el,
    select<Kind>({
      label: '라벨',
      options: [
        { value: 'all', label: '전체' },
        { value: 'hist', label: '역사적' },
        { value: 'w52', label: '52주' },
        { value: 'd120', label: '120일' },
      ],
      value: q.kind,
      onChange: (v) => {
        q.kind = v;
        void loadNewhigh();
      },
    }).el,
    select<MinTurn>({
      label: '거래대금 ≥',
      options: [
        { value: '0', label: '보드 기본' },
        { value: '50', label: '50억' },
        { value: '100', label: '100억' },
        { value: '300', label: '300억' },
      ],
      value: q.min,
      onChange: (v) => {
        q.min = v;
        void loadNewhigh();
      },
    }).el,
    count,
  );

  let sectorsEnv: Envelope<S['BoardSectors']> | null = null;
  let axis: HeatAxis = 'theme';
  const drawHeatPanel = (): void => {
    if (!sectorsEnv) return;
    pHeat.clearBody();
    drawHeat(pHeat, sectorsEnv, axis, labels);
    pHeat.setSource(sectorsEnv);
  };
  const loadSectors = async (): Promise<void> => {
    try {
      const env = await ctx.source.get<S['BoardSectors']>('board/sectors');
      sectorsEnv = env;
      for (const t of (env.data.themes ?? []).filter(isObj)) {
        const k = str(t.theme);
        const nm = str(t.name);
        if (k && nm) themeNames.set(k, nm);
      }
      pSec.clearBody();
      drawSectorCounts(pSec, env);
      pSec.setSource(env);
      drawHeatPanel();
    } catch (err) {
      // 두 패널이 한 응답을 쓴다 — 각 패널 안에 같은 오류 줄
      failed(pSec, err, hint);
      failed(pHeat, err, hint);
    }
  };
  filters(
    pHeat,
    seg<HeatAxis>({
      label: '히트맵 축',
      options: [
        { value: 'theme', label: '테마' },
        { value: 'sector', label: '섹터(board48)' },
      ],
      value: axis,
      onChange: (v) => {
        axis = v;
        drawHeatPanel();
      },
    }).el,
  );

  const loadEvents = (): Promise<void> =>
    fill(
      pEv,
      () => ctx.source.get<S['BoardEvents']>('board/events'),
      (env) => {
        const evs = eventsOf(env).sort((a, b) => (b.severity ?? 0) - (a.severity ?? 0));
        pEv.body.append(
          table<Ev>({
            columns: [
              { key: 'type', label: '유형', align: 'l', cell: (e) => EVENT_LABEL[e.type] ?? e.type },
              {
                key: 'who',
                label: '종목·테마',
                align: 'l',
                cell: (e) => {
                  const who = e.tickers.slice(0, 3).map((c) => names.get(c) ?? c);
                  const more = e.tickers.length > 3 ? ` 외 ${e.tickers.length - 3}` : '';
                  const theme = e.theme ? `[${themeNames.get(e.theme) ?? e.theme}] ` : '';
                  return `${theme}${who.join(', ')}${more}`;
                },
              },
              { key: 'sev', label: '강도', cell: (e) => num(e.severity, 2) },
              { key: 'ev', label: '근거', align: 'l', cell: (e) => evidenceText(e, labels) },
            ],
            rows: evs,
            pageSize: 20,
            empty: '탐지 없음',
          }).el,
        );
        pEv.setDef(env.data.note ?? null);
        return 0;
      },
      hint,
    );

  const rankBox = h('div');
  let boards: Board[] = [];
  const rankSel = h('div');
  const showBoard = (key: string): void => {
    const b = boards.find((x) => x.key === key);
    clear(rankBox);
    if (b) rankBox.append(rankingTable(b));
  };
  filters(pRank, rankSel);
  const loadRankings = (): Promise<void> =>
    fill(
      pRank,
      () => ctx.source.get<S['BoardRankings']>('board/rankings'),
      (env) => {
        boards = boardsOf(env.data);
        const first = boards[0];
        if (!first) {
          pRank.showMessage('랭킹 없음');
          return 0;
        }
        clear(rankSel);
        rankSel.append(
          select<string>({
            label: '랭킹',
            options: boards.map((b) => ({ value: b.key, label: b.label })),
            value: first.key,
            onChange: showBoard,
          }).el,
        );
        pRank.body.append(rankBox);
        showBoard(first.key);
        return 0;
      },
      hint,
    );

  // 탐지는 종목·테마 이름을 쓰려고 보드·섹터 뒤에
  const all = async (): Promise<void> => {
    await Promise.all([loadNewhigh(), loadSectors(), loadRankings()]);
    await loadEvents();
  };
  await all();
  // 첫 불러오기 중에 앱이 내려갔으면(격자가 문서에서 빠짐) 주기 작업을 걸지 않는다
  if (root.isConnected) ctx.every(REFRESH_MS, () => void all());
}

const page: PageModule = { mount };
export default page;
