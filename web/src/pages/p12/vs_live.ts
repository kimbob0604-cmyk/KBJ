// 페이지 12 ③ 백테스트 대 실제(전체 폭 — 탭 묶음, 차트 없음)(docs/p8_design.md §4.5·§4.6·§6·D-P8-14, 묶음 BKT).
//
// - 탭 '기대 대 실제': 같은 규칙의 야간 백테스트 신호 성과(summary.outcome — 같은 함수)와 실제 알림 후 성과(rules/stats)를
//   같은 칸(n·승률·평균)으로 나란히. 실제 쪽이 아직 없으면 '—'.
// - 탭 '일치 감시': 최근 20 거래일(backtest/match) — 날마다 실시간 n·백테스트 n·차이(종목·쪽·사유). logic(같은 입력에
//   다른 결과)은 down 색 + 위에 경고 한 줄.
// - 데이터 없음(404)은 '.msg', 그 밖 실패만 '.msg.err'(탭 안 — 다른 탭은 계속).
import type { components } from '../../api/types.gen';
import type { PageContext } from '../../app/types';
import type { Envelope } from '../../data/source';
import { h } from '../../ui/dom';
import { DASH, num, pct, shortDate } from '../../ui/fmt';
import type { Panel } from '../../ui/panel';
import { type Column, table } from '../../ui/table';
import { tabs } from '../../ui/tabs';
import { failPanel, isNoData, loadStats, noticeText, pctCell, type RuleStats, type StatsCell } from '../p11/perf';
import { BT_HINT, type BacktestRun, latestRuns, loadRuns } from './backtest';

type BacktestMatch = components['schemas']['BacktestMatch'];
type MatchRow = components['schemas']['MatchRowOut'];

export const VS_TABS = [
  { id: 'vs', label: '기대 대 실제' },
  { id: 'match', label: '일치 감시' },
] as const;
type TabId = (typeof VS_TABS)[number]['id'];

const SIDE: Readonly<Record<string, string>> = {
  live_only: '실시간에만',
  backtest_only: '백테스트에만',
};
const REASON: Readonly<Record<string, string>> = {
  data_revised: '데이터 수정',
  logic: '로직',
};

/** 기대 칸(야간 summary.outcome) — 모양을 확인하고 모르는 값은 null */
export interface Cell {
  horizon: number;
  n: number | null;
  win_rate: number | null;
  mean: number | null;
}

function numOrNull(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

export function expectedCells(summary: Record<string, unknown>): Cell[] {
  const raw = summary.outcome;
  if (!Array.isArray(raw)) return [];
  const out: Cell[] = [];
  for (const x of raw as unknown[]) {
    if (x === null || typeof x !== 'object') continue;
    const o = x as Record<string, unknown>;
    const hz = numOrNull(o.horizon);
    if (hz === null) continue;
    out.push({
      horizon: hz,
      n: numOrNull(o.n),
      win_rate: numOrNull(o.win_rate),
      mean: numOrNull(o.mean),
    });
  }
  return out;
}

export interface VsRow {
  rule: string;
  name: string;
  first: boolean;
  horizon: number;
  bt: Cell | null;
  live: StatsCell | null;
}

export function vsRows(runs: readonly BacktestRun[], stats: RuleStats | null): VsRow[] {
  const out: VsRow[] = [];
  for (const r of latestRuns(runs)) {
    const srow = stats?.rows.find((x) => x.rule_id === r.rule_id) ?? null;
    const exp = expectedCells(r.summary);
    const horizons = [...new Set([...exp.map((c) => c.horizon), ...(srow?.cells.map((c) => c.horizon) ?? [])])].sort(
      (a, b) => a - b,
    );
    horizons.forEach((hz, i) => {
      out.push({
        rule: r.rule_id,
        name: srow?.name ?? r.rule_id,
        first: i === 0,
        horizon: hz,
        bt: exp.find((c) => c.horizon === hz) ?? null,
        live: srow?.cells.find((c) => c.horizon === hz) ?? null,
      });
    });
  }
  return out;
}

const VS_COLUMNS: readonly Column<VsRow>[] = [
  {
    key: 'rule',
    label: '규칙',
    align: 'l',
    cell: (r) => (r.first ? r.name : ''),
  },
  { key: 'h', label: '기간', cell: (r) => `${String(r.horizon)}일` },
  { key: 'bn', label: '기대 n', cell: (r) => num(r.bt?.n) },
  { key: 'bw', label: '기대 승률', cell: (r) => pct(r.bt?.win_rate, 1, false) },
  { key: 'bm', label: '기대 평균', cell: (r) => pctCell(r.bt?.mean) },
  {
    key: 'ln',
    label: '실제 n',
    cell: (r) => num(r.live?.n),
    cls: (r) => (r.live?.small ? 'dim' : undefined),
  },
  {
    key: 'lw',
    label: '실제 승률',
    cell: (r) => pct(r.live?.win_rate, 1, false),
    cls: (r) => (r.live?.small ? 'dim' : undefined),
  },
  {
    key: 'lm',
    label: '실제 평균',
    cell: (r) => pctCell(r.live?.mean),
    cls: (r) => (r.live?.small ? 'dim' : undefined),
  },
];

export function diffText(m: MatchRow): string {
  if (m.diffs.length === 0) return DASH;
  return m.diffs.map((d) => `${d.code} ${SIDE[d.side] ?? d.side}·${REASON[d.reason] ?? d.reason}`).join(', ');
}

const MATCH_COLUMNS: readonly Column<MatchRow>[] = [
  { key: 'd', label: '날짜', cell: (m) => shortDate(m.trade_date) },
  { key: 'rule', label: '규칙', align: 'l', cell: (m) => m.rule_id },
  { key: 'live', label: '실시간', cell: (m) => num(m.live_n) },
  { key: 'bt', label: '백테스트', cell: (m) => num(m.backtest_n) },
  {
    key: 'logic',
    label: 'logic',
    cell: (m) => num(m.logic_n),
    cls: (m) => (m.logic_n > 0 ? 'down' : undefined),
  },
  {
    key: 'diff',
    label: '차이',
    align: 'l',
    cell: diffText,
    cls: (m) => (m.logic_n > 0 ? 'down' : undefined),
  },
];

function msg(el: HTMLElement, text: string, err = false): void {
  el.replaceChildren(h('p', { class: err ? 'msg err' : 'msg', attrs: { role: 'status' } }, text));
}

async function fillVs(ctx: PageContext, el: HTMLElement): Promise<void> {
  let runs: Envelope<components['schemas']['BacktestRuns']>;
  try {
    runs = await loadRuns(ctx);
  } catch (err) {
    const notice = noticeText(err, BT_HINT);
    if (notice !== null) msg(el, isNoData(err) ? (err.serverMessage ?? BT_HINT) : notice);
    else msg(el, '불러오지 못함', true);
    return;
  }
  let stats: RuleStats | null = null;
  try {
    stats = (await loadStats(ctx)).data;
  } catch (err) {
    if (noticeText(err, BT_HINT) === null) throw err;
  }
  const rows = vsRows(runs.data.runs, stats);
  if (rows.length === 0) {
    msg(el, BT_HINT);
    return;
  }
  el.replaceChildren(
    h(
      'p',
      { class: 'dim' },
      '기대 = 야간 백테스트 신호의 사후 수익(같은 계산), 실제 = 알림 후 성과 · 단위 % · 비용 미반영',
    ),
    table<VsRow>({
      caption: '백테스트 기대 대 실제 알림 성과',
      columns: VS_COLUMNS,
      rows,
      rowKey: (r) => `${r.rule}:${String(r.horizon)}`,
    }).el,
  );
}

async function fillMatch(ctx: PageContext, el: HTMLElement): Promise<void> {
  let env: Envelope<BacktestMatch>;
  try {
    env = await ctx.source.get<BacktestMatch>('backtest/match');
  } catch (err) {
    const notice = noticeText(err, BT_HINT);
    if (notice !== null) msg(el, isNoData(err) ? (err.serverMessage ?? BT_HINT) : notice);
    else msg(el, '불러오지 못함', true);
    return;
  }
  const rows = env.data.rows;
  if (rows.length === 0) {
    msg(el, '일치 감시 없음');
    return;
  }
  const logic = rows.reduce((a, m) => a + m.logic_n, 0);
  el.replaceChildren(
    h(
      'p',
      { class: logic > 0 ? 'msg err' : 'dim', attrs: { role: 'status' } },
      logic > 0
        ? `logic 차이 ${num(logic)}건 — 같은 입력에 다른 결과(점검 필요)`
        : '최근 거래일 실시간과 백테스트 신호가 같다',
    ),
    table<MatchRow>({
      caption: '일치 감시 — 최근 20 거래일',
      columns: MATCH_COLUMNS,
      rows,
      rowKey: (m) => `${m.trade_date}:${m.rule_id}`,
      pageSize: 40,
    }).el,
  );
}

export async function fillVsLive(ctx: PageContext, p: Panel): Promise<void> {
  try {
    p.setSource(await loadRuns(ctx));
  } catch (err) {
    failPanel(p, err, BT_HINT);
    return;
  }
  p.clearBody();
  const t = tabs<TabId>({
    label: '백테스트 대 실제',
    tabs: VS_TABS,
    onFirstShow: (id, el) => (id === 'vs' ? fillVs(ctx, el) : fillMatch(ctx, el)),
  });
  p.body.append(t.el);
  await t.settled();
}
