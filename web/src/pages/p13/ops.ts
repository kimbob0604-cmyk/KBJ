// 페이지 13 '운영' 최소판 본문(docs/p9_design.md §6.2, §11 Q5, ADR 0037) — 보기 전용 표 셋:
// ① 서비스 상태(하트비트 나이·기준·상태 + 점검 항목 한 줄씩) ② 오늘 작업(마지막 시도) ③ 오늘 발송 기록(본문 없음).
//
// - 데이터: GET ops/status·ops/jobs·ops/notify(로그인 — `python -m kbj.services.ops check` 와 같은 함수).
// - 404(no_data — 운영 기록 원천 없음)는 '.msg' 안내, 그 밖 실패만 '.msg.err'. 한 패널의 실패는 그 패널 안에서 끝난다.
// - 공개 빌드는 이 모듈을 번들에서 뺀다(진입 파일 LOGIN_FILLS) — 모듈 최상위에 부수 효과를 두지 않는다.
import type { components } from '../../api/types.gen';
import type { PageContext } from '../../app/types';
import { DataError, LockedError } from '../../data/source';
import { h } from '../../ui/dom';
import { toggle } from '../../ui/form';
import { kstClock, kstStamp, num } from '../../ui/fmt';
import { errorText, type Panel } from '../../ui/panel';
import { type Column, table } from '../../ui/table';

type S = components['schemas'];
export type OpsStatus = S['OpsStatus'];
export type OpsService = S['OpsService'];
export type OpsCheckItem = S['OpsCheckItem'];
export type OpsJobs = S['OpsJobs'];
export type OpsJobRow = S['OpsJobRow'];
export type OpsNotify = S['OpsNotify'];
export type OpsNotifyRow = S['OpsNotifyRow'];
type Level = OpsService['level'];

export const OPS_HINT = '운영 기록 없음 — API 가 DB 에 붙은 운영에서만 보인다';

export const LEVEL_LABEL: Readonly<Record<Level, string>> = { OK: '정상', WARN: '주의', FAIL: '실패' };

export const JOB_STATUS: Readonly<Record<string, string>> = {
  ok: '성공',
  failed: '실패',
  timeout: '시간 초과',
  skipped: '건너뜀',
  running: '도는 중',
  queued: '대기',
};

export const NOTIFY_STATUS: Readonly<Record<string, string>> = {
  sent: '보냄',
  failed: '실패',
  suppressed: '억제',
  duplicate: '중복',
  cooldown: '쿨다운',
  queued: '대기',
};

export function levelCls(level: Level): string | undefined {
  return level === 'FAIL' ? 'down' : level === 'WARN' ? 'warn' : undefined;
}

function badCls(status: string): string | undefined {
  return status === 'failed' || status === 'timeout' ? 'down' : status === 'skipped' ? 'dim' : undefined;
}

/** 404·로그인 등급 안내는 '.msg', 그 밖은 '.msg.err' */
export function failOps(p: Panel, err: unknown): void {
  if (err instanceof DataError && err.status === 404) p.showMessage(err.serverMessage ?? OPS_HINT);
  else if (err instanceof LockedError) p.showMessage(errorText(err));
  else p.showError(err);
}

export function ageText(s: OpsService): string {
  return s.age_s === null ? '없음' : `${num(s.age_s, 0)}초`;
}

export async function fillStatus(ctx: PageContext, p: Panel): Promise<void> {
  let env;
  try {
    env = await ctx.source.get<OpsStatus>('ops/status');
  } catch (err) {
    failOps(p, err);
    return;
  }
  p.setSource(env);
  const d = env.data;
  const columns: Column<OpsService>[] = [
    { key: 'name', label: '서비스', align: 'l', cell: (s) => s.name },
    { key: 'age', label: '하트비트', cell: ageText },
    { key: 'base', label: '기준', cell: (s) => `${num(s.heartbeat_s, 0)}초` },
    { key: 'level', label: '상태', align: 'l', cell: (s) => LEVEL_LABEL[s.level], cls: (s) => levelCls(s.level) },
  ];
  const summary = h(
    'p',
    { class: levelCls(d.level) ?? '' },
    h('b', null, `점검 ${LEVEL_LABEL[d.level]}`),
    ` · 기록 ${kstStamp(d.since)} 부터`,
  );
  const items = h(
    'ul',
    { class: 'ops-items' },
    d.items.map((i: OpsCheckItem) =>
      h('li', { class: levelCls(i.level) ?? '' }, `${LEVEL_LABEL[i.level]} · ${i.title} — ${i.value}`),
    ),
  );
  p.clearBody();
  p.body.append(
    summary,
    table({ columns, rows: d.services, rowKey: (s) => s.name, empty: '켜진 상시 서비스 없음' }).el,
    items,
  );
}

/** 손볼 것 — 마지막 시도가 실패·시간 초과·도는 중·대기, 또는 다시 해서 성공한 것 */
export function isProblem(r: OpsJobRow): boolean {
  return r.status !== 'ok' && r.status !== 'skipped' ? true : r.retried_ok;
}

const PROBLEM_ORDER: Readonly<Record<string, number>> = { failed: 0, timeout: 0, running: 1, queued: 1 };

/** 실패·시간 초과 → 도는 중 → 나머지(시각순) */
export function sortJobs(rows: readonly OpsJobRow[]): OpsJobRow[] {
  return [...rows].sort((a, b) => (PROBLEM_ORDER[a.status] ?? 2) - (PROBLEM_ORDER[b.status] ?? 2));
}

// 패널마다 '문제만 보기' 상태(1분마다 다시 그려도 유지)
const problemsOnly = new WeakMap<Panel, boolean>();

export async function fillJobs(ctx: PageContext, p: Panel): Promise<void> {
  let env;
  try {
    env = await ctx.source.get<OpsJobs>('ops/jobs'); // 날짜 없음 = 서버 시계의 KST 오늘
  } catch (err) {
    failOps(p, err);
    return;
  }
  p.setSource(env);
  const d = env.data;
  const columns: Column<OpsJobRow>[] = [
    { key: 'start', label: '시작', align: 'l', cell: (r) => (r.started_at ? kstClock(r.started_at) : '시작 못 함') },
    { key: 'job', label: '작업', align: 'l', cell: (r) => (r.as_of.length > 10 ? `${r.job} @ ${r.as_of.slice(11)}` : r.job) },
    {
      key: 'status',
      label: '상태',
      align: 'l',
      cell: (r) => `${JOB_STATUS[r.status] ?? r.status}${r.retried_ok ? ' (재시도)' : ''}${r.manual ? ' · 수동' : ''}`,
      cls: (r) => badCls(r.status),
    },
    { key: 'try', label: '시도', cell: (r) => num(r.attempts, 0) },
    { key: 'why', label: '사유', align: 'l', cell: (r) => (r.status === 'ok' ? null : (r.reason ?? null)) },
  ];
  const draw = (): void => {
    const only = problemsOnly.get(p) ?? true;
    const rows = only ? sortJobs(d.rows.filter(isProblem)) : sortJobs(d.rows);
    const empty = only && d.rows.length > 0 ? '손볼 작업 없음 — 모두 성공·건너뜀' : '이날 실행 기록 없음';
    const tg = toggle({
      label: '문제만 보기',
      value: only,
      onChange: (v) => {
        problemsOnly.set(p, v);
        draw();
      },
    });
    p.clearBody();
    p.body.append(
      h('p', { class: 'dim' }, `${d.date} · ${num(d.total, 0)}건 · 실패 ${num(d.failed, 0)} · 재시도 성공 ${num(d.retried_ok, 0)}`),
      tg.el,
      table({ columns, rows, rowKey: (r) => `${r.job}|${r.as_of}`, empty }).el,
    );
  };
  draw();
}

export interface NotifyGroup {
  kind: string;
  status: string;
  reason: string | null;
  n: number;
  first: string;
  last: string;
  parts: number;
}

const NOTIFY_ORDER: Readonly<Record<string, number>> = { failed: 0, sent: 1, queued: 2 };

/** 같은 (종류, 상태, 사유)를 한 줄로 — 쿨다운·억제가 수백 줄 되풀이되지 않게. 실패 → 보냄 → 나머지 */
export function groupNotify(rows: readonly OpsNotifyRow[]): NotifyGroup[] {
  const by = new Map<string, NotifyGroup>();
  for (const r of rows) {
    const key = `${r.kind}|${r.status}|${r.reason ?? ''}`;
    const g = by.get(key);
    if (g === undefined) {
      by.set(key, { kind: r.kind, status: r.status, reason: r.reason ?? null, n: 1, first: r.requested_at, last: r.requested_at, parts: r.parts ?? 0 });
    } else {
      g.n += 1;
      g.parts += r.parts ?? 0;
      if (r.requested_at < g.first) g.first = r.requested_at;
      if (r.requested_at > g.last) g.last = r.requested_at;
    }
  }
  return [...by.values()].sort(
    (a, b) => (NOTIFY_ORDER[a.status] ?? 3) - (NOTIFY_ORDER[b.status] ?? 3) || b.n - a.n || a.kind.localeCompare(b.kind),
  );
}

export async function fillNotify(ctx: PageContext, p: Panel): Promise<void> {
  let env;
  try {
    env = await ctx.source.get<OpsNotify>('ops/notify');
  } catch (err) {
    failOps(p, err);
    return;
  }
  p.setSource(env);
  const d = env.data;
  const columns: Column<NotifyGroup>[] = [
    { key: 'kind', label: '종류', align: 'l', cell: (g) => g.kind },
    {
      key: 'status',
      label: '상태',
      align: 'l',
      cell: (g) => `${NOTIFY_STATUS[g.status] ?? g.status}${g.reason ? ` · ${g.reason}` : ''}`,
      cls: (g) => badCls(g.status),
    },
    { key: 'n', label: '건수', cell: (g) => num(g.n, 0) },
    { key: 'when', label: '시각', align: 'l', cell: (g) => (g.n > 1 ? `${kstClock(g.first)}~${kstClock(g.last)}` : kstClock(g.first)) },
  ];
  const groups = groupNotify(d.rows);
  const held = d.rows.length - d.sent - d.failed;
  p.clearBody();
  p.body.append(
    h('p', { class: 'dim' }, `${d.date} · 보냄 ${num(d.sent, 0)} · 실패 ${num(d.failed, 0)} · 안 보냄(억제·쿨다운·중복) ${num(held, 0)}`),
    table({
      columns,
      rows: groups,
      rowKey: (g) => `${g.kind}|${g.status}|${g.reason ?? ''}`,
      empty: '이날 발송 기록 없음',
    }).el,
    h('p', { class: 'def' }, '같은 종류·상태·사유는 한 줄로 묶었다. 억제 = 발송 꺼짐(KBJ_NOTIFY_ENABLED), 쿨다운·중복 = 같은 알림을 이미 보냈거나 대기 중 — 실패가 아니다.'),
  );
}
