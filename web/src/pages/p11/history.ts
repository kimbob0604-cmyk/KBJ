// 페이지 11 ③ 발송 이력(docs/p8_design.md §6, 묶음 EVL) — 시각·규칙·종목·모드·상태(보냄·쿨다운·막힘·발송 꺼짐·
// 건너뜀+사유)·품질, 행을 고르면 근거(조건별 ✔/✖/? 값 — 신호 표 evidence)를 표 아래에 펼친다.
//
// - 기간: 최근 7일(서버 기본). 모드 거르개(전체·장중·마감)는 받은 목록을 거른다(요청 한 번).
// - 장중 신호의 수급 근거는 '장중 잠정'(가집계), 판단 불가는 '?'. 데이터 없음(404)은 '.msg', 그 밖 실패만 '.msg.err'.
import type { PageContext } from '../../app/types';
import { h } from '../../ui/dom';
import { kstStamp, num } from '../../ui/fmt';
import type { Panel } from '../../ui/panel';
import { seg } from '../../ui/seg';
import { type Column, table } from '../../ui/table';
import { failPanel } from './perf';
import { mark, MODE_LABEL, REASON_LABEL, type SignalList, type SignalOut, SKIP_LABEL, STATUS_LABEL } from './rules_api';

export const HISTORY_HINT = '아직 없음 — rules.intraday 장중 10분 · rules.close 16:00';
type ModeFilter = 'all' | 'intraday' | 'close';

interface EvCond {
  label?: string;
  ind?: string;
  ok?: boolean | null;
  value?: number | null;
  ref_value?: number | null;
  op?: string;
  reason?: string | null;
  flow?: boolean;
}

export function statusText(s: SignalOut): string {
  const base = STATUS_LABEL[s.status] ?? s.status;
  return s.skip_reason ? `${base} · ${SKIP_LABEL[s.skip_reason] ?? s.skip_reason}` : base;
}

/** 근거 한 줄 — 서버 근거 자료(evidence.conds)에서. 값이 없으면 사유. */
export function evidenceLine(c: EvCond, live: boolean): string {
  const name = c.label ?? c.ind ?? '?';
  const flow = c.flow ? (live ? ' (장중 잠정)' : ' (확정 원장)') : '';
  if (c.value === null || c.value === undefined || c.reason) {
    return `${mark(c.ok)} ${name} — ${REASON_LABEL[c.reason ?? ''] ?? '값 없음'}${flow}`;
  }
  const ref = c.ref_value === null || c.ref_value === undefined ? '' : ` ${c.op ?? ''} ${num(c.ref_value, 2)}`;
  return `${mark(c.ok)} ${name} ${num(c.value, 2)}${ref}${flow}`;
}

function evidenceBox(s: SignalOut): HTMLElement {
  const ev = s.evidence as { conds?: EvCond[]; partial?: boolean; logic?: string };
  const conds = Array.isArray(ev.conds) ? ev.conds : [];
  const live = ev.partial === true;
  const head = `${s.rule_name ?? s.rule_id} · ${s.name ? `${s.name} ` : ''}${s.code} · ${kstStamp(s.eval_ts)} ${live ? '장중(잠정)' : '마감'}`;
  return h(
    'div',
    { class: 'evidence' },
    h('p', null, h('b', null, head)),
    conds.length
      ? h(
          'ul',
          null,
          conds.map((c) => h('li', null, evidenceLine(c, live))),
        )
      : h('p', { class: 'dim' }, '근거 없음(평가 전 건너뜀)'),
    h(
      'p',
      { class: 'dim' },
      `${ev.logic === 'any' ? '하나 이상(OR)' : '모두(AND)'} · ${String(s.version)}판 · 품질 ${s.quality}`,
    ),
  );
}

export async function fillHistory(ctx: PageContext, p: Panel): Promise<void> {
  let env;
  try {
    env = await ctx.source.get<SignalList>('rules/signals');
  } catch (err) {
    failPanel(p, err, HISTORY_HINT);
    return;
  }
  p.setSource(env);
  const all = env.data.signals;
  const detail = h('div');
  const columns: Column<SignalOut>[] = [
    { key: 'ts', label: '시각', align: 'l', cell: (s) => kstStamp(s.eval_ts) },
    { key: 'rule', label: '규칙', align: 'l', cell: (s) => s.rule_name ?? s.rule_id },
    { key: 'code', label: '종목', align: 'l', cell: (s) => (s.name ? `${s.name} ${s.code}` : s.code) },
    { key: 'mode', label: '모드', align: 'l', cell: (s) => MODE_LABEL[s.mode] ?? s.mode },
    {
      key: 'status',
      label: '상태',
      align: 'l',
      cell: statusText,
      cls: (s) => (s.status === 'sent' ? 'up' : s.status === 'skipped' ? 'dim' : undefined),
    },
    { key: 'price', label: '가격', cell: (s) => num(s.entry_price, 0) },
    { key: 'q', label: '품질', align: 'l', cell: (s) => s.quality },
  ];
  const t = table({
    columns,
    rows: all,
    rowKey: (s) => s.signal_id,
    empty: '이 거르개에 해당하는 신호 없음',
    onSelect: (s) => {
      detail.replaceChildren(evidenceBox(s));
    },
  });
  const filter = seg<ModeFilter>({
    label: '모드',
    value: 'all',
    options: [
      { value: 'all', label: '전체' },
      { value: 'intraday', label: '장중' },
      { value: 'close', label: '마감' },
    ],
    onChange: (v) => {
      t.update(v === 'all' ? all : all.filter((s) => s.mode === v));
      detail.replaceChildren();
    },
  });
  p.clearBody();
  p.body.append(filter.el, t.el, detail);
}
