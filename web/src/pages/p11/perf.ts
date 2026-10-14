// 페이지 11 ④ 알림 후 성과(규칙 × 기간 표 — n<20 흐림)·⑤ 성과 분포(막대 — 반폭)(docs/p8_design.md §4.5·§6, 묶음 BKT).
//
// - 데이터: GET rules/stats(로그인 — rules.outcome 08:30 이 쌓은 성과를 읽을 때 계산). 두 패널이 한 번만 부른다.
// - 값 단위 %(서버가 ×100). 수익을 약속하는 문구 없음 — 봉투 notes("과거 신호의 사후 수익 — 비용·체결 미반영")를
//   원천 줄에 그대로.
// - 데이터 없음(404)은 '.msg' 안내(서버 문구 — 예정 작업), 그 밖 실패만 '.msg.err'.
// - 공개 빌드는 이 모듈을 번들에서 뺀다(진입 파일 LOGIN_FILLS) — 모듈 최상위에 부수 효과를 두지 않는다.
import type { components } from '../../api/types.gen';
import type { PageContext } from '../../app/types';
import { DataError, type Envelope, LockedError } from '../../data/source';
import { barsChart } from '../../ui/bars';
import { h } from '../../ui/dom';
import { DASH, num, pct, signClass } from '../../ui/fmt';
import { errorText, type Panel } from '../../ui/panel';
import { select } from '../../ui/seg';
import { type Column, table } from '../../ui/table';

export type RuleStats = components['schemas']['RuleStats'];
export type StatsCell = components['schemas']['StatsCell'];
type StatsRow = components['schemas']['RuleStatsRow'];

export const STATS_HINT = '아직 없음 — rules.outcome 08:30';
export const SMALL_TEXT = '표본 적음';

/** 404(no_data)인가 — 안내 문구로 그린다 */
export function isNoData(err: unknown): err is DataError {
  return err instanceof DataError && err.status === 404;
}

/** 오류가 아닌 안내 문구 — 데이터 없음(404, 서버 문구)·이 소스엔 없는 로그인 등급 데이터(LockedError —
 *  정적 소스로 로그인 화면을 띄운 경우). 안내가 아니면 null(P8 WRAP — 페이지 12 저널의 isNotice 와 같은 기준) */
export function noticeText(err: unknown, hint: string): string | null {
  if (isNoData(err)) return err.serverMessage ?? `${errorText(err)} — ${hint}`;
  if (err instanceof LockedError) return errorText(err);
  return null;
}

/** 실패 → 404·로그인 등급 안내는 '.msg', 그 밖은 '.msg.err' */
export function failPanel(p: Panel, err: unknown, hint: string): void {
  const notice = noticeText(err, hint);
  if (notice !== null) p.showMessage(notice);
  else p.showError(err);
}

// 페이지 문맥마다 한 번(④·⑤ 와 페이지 12 '백테스트 대 실제' 가 나눈다)
let statsCache: WeakMap<PageContext, Promise<Envelope<RuleStats>>> | undefined;
export function loadStats(ctx: PageContext): Promise<Envelope<RuleStats>> {
  statsCache ??= new WeakMap();
  let got = statsCache.get(ctx);
  if (!got) {
    got = ctx.source.get<RuleStats>('rules/stats');
    statsCache.set(ctx, got);
  }
  return got;
}

export interface PerfRow {
  rule: string;
  name: string;
  first: boolean; // 규칙의 첫 줄(이름을 적는다)
  cell: StatsCell;
}

export function perfRows(data: RuleStats): PerfRow[] {
  const out: PerfRow[] = [];
  for (const r of data.rows) {
    r.cells.forEach((cell, i) => {
      out.push({
        rule: r.rule_id,
        name: r.name ?? r.rule_id,
        first: i === 0,
        cell,
      });
    });
  }
  return out;
}

const dimIf = (r: PerfRow): string | undefined => (r.cell.small ? 'dim' : undefined);

export function pctCell(v: number | null | undefined, signed = true): HTMLElement {
  return h('span', { class: signed ? signClass(v) : undefined }, pct(v, 2, signed));
}

const PERF_COLUMNS: readonly Column<PerfRow>[] = [
  {
    key: 'rule',
    label: '규칙',
    align: 'l',
    cell: (r) => (r.first ? r.name : ''),
    cls: dimIf,
  },
  {
    key: 'h',
    label: '기간',
    cell: (r) => `${String(r.cell.horizon)}일`,
    cls: dimIf,
  },
  {
    key: 'n',
    label: 'n',
    cell: (r) => (r.cell.small ? `${num(r.cell.n)} · ${SMALL_TEXT}` : num(r.cell.n)),
    cls: dimIf,
  },
  {
    key: 'win',
    label: '승률',
    cell: (r) => pct(r.cell.win_rate, 1, false),
    cls: dimIf,
  },
  { key: 'mean', label: '평균', cell: (r) => pctCell(r.cell.mean), cls: dimIf },
  {
    key: 'median',
    label: '중앙',
    cell: (r) => pctCell(r.cell.median),
    cls: dimIf,
  },
  {
    key: 'excess',
    label: '지수 초과',
    cell: (r) => pctCell(r.cell.excess_mean),
    cls: dimIf,
  },
  {
    key: 'excl',
    label: '뺀 수',
    cell: (r) => (r.cell.excluded > 0 ? num(r.cell.excluded) : DASH),
    cls: dimIf,
  },
];

export async function fillPerf(ctx: PageContext, p: Panel): Promise<void> {
  let env: Envelope<RuleStats>;
  try {
    env = await loadStats(ctx);
  } catch (err) {
    failPanel(p, err, STATS_HINT);
    return;
  }
  p.setSource(env);
  const rows = perfRows(env.data);
  if (rows.length === 0) {
    p.showMessage('알림 후 성과 없음');
    return;
  }
  p.clearBody();
  p.body.append(
    table<PerfRow>({
      caption: '규칙 × 기간 — 알림 후 수익(%) · 흐린 줄은 표본 적음',
      columns: PERF_COLUMNS,
      rows,
      rowKey: (r) => `${r.rule}:${String(r.cell.horizon)}`,
    }).el,
  );
}

function distChart(row: StatsRow, horizon: string): HTMLElement {
  if (row.dist.length === 0) return h('p', { class: 'msg', attrs: { role: 'status' } }, '분포 없음');
  return barsChart({
    label: `${row.name ?? row.rule_id} — ${horizon} 수익 분포(신호 수)`,
    items: row.dist.map((b) => ({
      label: `${pct(b.lo, 0)}~${pct(b.hi, 0)}`,
      value: b.n,
      detail: `${pct(b.lo, 1)} ~ ${pct(b.hi, 1)} · ${num(b.n)}건`,
    })),
    format: (v) => num(v),
  });
}

/** 봉투 notes 의 '분포 = N거래일 수익' 에서 기간 글자 */
function distHorizon(notes: readonly string[]): string {
  const n = notes.find((x) => x.startsWith('분포 = '));
  return n ? n.replace('분포 = ', '').replace(' 수익', '') : '5거래일';
}

export async function fillPerfDist(ctx: PageContext, p: Panel): Promise<void> {
  let env: Envelope<RuleStats>;
  try {
    env = await loadStats(ctx);
  } catch (err) {
    failPanel(p, err, STATS_HINT);
    return;
  }
  p.setSource(env);
  const rows = env.data.rows;
  const first = rows[0];
  if (!first) {
    p.showMessage('알림 후 성과 없음');
    return;
  }
  const horizon = distHorizon(env.notes);
  p.clearBody();
  const holder = h('div', { class: 'dist' }, distChart(first, horizon));
  const pick = select<string>({
    label: '규칙',
    options: rows.map((r) => ({
      value: r.rule_id,
      label: r.name ?? r.rule_id,
    })),
    value: first.rule_id,
    onChange: (v) => {
      const row = rows.find((r) => r.rule_id === v);
      if (row) holder.replaceChildren(distChart(row, horizon));
    },
  });
  p.body.append(h('div', { class: 'filters' }, pick.el), holder);
}
