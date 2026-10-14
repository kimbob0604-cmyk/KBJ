// 페이지 11 ① 규칙 목록(docs/p8_design.md §6, 묶음 EVL) — 이름·모드·상태(켜짐·꺼짐·보관)·대상·쿨다운·30일 신호 수·
// 최근 신호·판. 행의 '편집' 은 ② 편집기에 현재 판 DSL 을 싣는다(같은 페이지 문맥의 알림 — rules_api.busOf).
//
// - 켜기/끄기·보관은 쓰기 채널(데이터 소스 `post` — 로그인 CSRF)로. 채널이 없으면 단추를 끄고 안내만(요청하지 않는다).
// - 데이터 없음(404)은 '.msg'(서버 안내), 그 밖 실패만 '.msg.err'. 쓰기 실패는 표 아래 한 줄(목록은 그대로).
import type { PageContext } from '../../app/types';
import { h } from '../../ui/dom';
import { kstStamp } from '../../ui/fmt';
import { errorText, type Panel } from '../../ui/panel';
import { type Column, table } from '../../ui/table';
import { failPanel } from './perf';
import {
  busOf,
  cooldownText,
  MODE_LABEL,
  NO_WRITER,
  type RuleDetail,
  type RuleList,
  type RuleSummary,
  type RuleWriteResult,
  RULES_HINT,
  ruleWriter,
  universeText,
} from './rules_api';

type Action = 'enable' | 'disable' | 'archive';

function stateText(r: RuleSummary): string {
  if (r.archived) return '보관';
  return r.enabled ? '켜짐' : '꺼짐';
}

export async function fillRules(ctx: PageContext, p: Panel): Promise<void> {
  const bus = busOf(ctx);
  const writer = ruleWriter(ctx.source);
  const status = h('p', { class: 'msg', attrs: { role: 'status' } });
  let busy = false;

  function fail(name: string, err: unknown): void {
    status.className = 'msg err';
    status.textContent = `${name}: ${errorText(err)}`;
  }

  async function act(r: RuleSummary, action: Action): Promise<void> {
    if (!writer || busy) return;
    busy = true;
    try {
      await writer.post<RuleWriteResult>(`rules/${r.rule_id}/${action}`, null);
      for (const f of bus.changed) f();
    } catch (err) {
      fail(r.name, err);
    } finally {
      busy = false;
    }
  }

  async function edit(r: RuleSummary): Promise<void> {
    try {
      const env = await ctx.source.get<RuleDetail>(`rules/${r.rule_id}`);
      const cur = env.data.versions.find((v) => v.version === env.data.rule.current_version);
      if (cur) for (const f of bus.edit) f(r.rule_id, cur.dsl);
    } catch (err) {
      fail(r.name, err);
    }
  }

  function button(label: string, onClick: () => void, disabled: boolean): HTMLButtonElement {
    return h('button', { class: 'btn', attrs: { type: 'button', disabled }, on: { click: onClick } }, label);
  }

  function actions(r: RuleSummary): HTMLElement {
    const off = writer === null || r.archived;
    const flip: Action = r.enabled ? 'disable' : 'enable';
    return h(
      'span',
      null,
      button(r.enabled ? '끄기' : '켜기', () => void act(r, flip), off),
      button('편집', () => void edit(r), r.archived),
      button('보관', () => void act(r, 'archive'), off),
    );
  }

  const columns: Column<RuleSummary>[] = [
    { key: 'name', label: '이름', align: 'l', cell: (r) => r.name },
    { key: 'mode', label: '모드', align: 'l', cell: (r) => MODE_LABEL[r.mode] ?? r.mode },
    { key: 'state', label: '상태', align: 'l', cell: stateText, cls: (r) => (r.enabled ? undefined : 'dim') },
    { key: 'universe', label: '대상', align: 'l', cell: (r) => universeText(r.universe) },
    { key: 'cooldown', label: '쿨다운', cell: (r) => cooldownText(r.cooldown_min) },
    { key: 'n30', label: '30일 신호', cell: (r) => String(r.signals_30d) },
    { key: 'last', label: '최근 신호', cell: (r) => (r.last_signal_at ? kstStamp(r.last_signal_at) : null) },
    { key: 'ver', label: '판', cell: (r) => `${String(r.current_version)}판` },
    { key: 'act', label: '', align: 'l', cell: actions },
  ];

  async function load(): Promise<void> {
    try {
      const env = await ctx.source.get<RuleList>('rules');
      p.setSource(env);
      p.clearBody();
      const rows = [...env.data.rules].sort(
        (a, b) => Number(a.archived) - Number(b.archived) || a.name.localeCompare(b.name, 'ko'),
      );
      status.className = 'msg';
      status.textContent = writer ? '' : NO_WRITER;
      p.body.append(table({ columns, rows, rowKey: (r) => r.rule_id, empty: RULES_HINT }).el, status);
    } catch (err) {
      failPanel(p, err, RULES_HINT);
    }
  }

  bus.changed.push(() => void load());
  await load();
}
