// 페이지 11 ② 편집기(docs/p8_design.md §6, 묶음 EVL) — 조건 행(지표 → 파라미터·칸·연산자·값/참조), 묶음 AND/OR,
// 우니버스·모드·쿨다운·우선순위, JSON 보기(같은 스키마 검사 — rule.schema.json), 예시 불러오기, '오늘 데이터로 시험'
// (POST rules/validate — 저장 없음, 조건별 ✔/✖ 값), 저장(검사 통과 뒤 POST rules · 편집 중이면 PUT rules/<id> = 새 판).
//
// - 폼은 한 겹 묶음(all 또는 any)의 조건 행만 다룬다. 중첩 묶음 규칙은 JSON 으로만 고친다(안내 문구).
// - 서버 검사 결과는 위치·사유만(입력값 없음 — formErrors). 쓰기 채널이 없으면 시험·저장 단추를 끄고 안내만.
// - 숫자 칸은 빈칸·잘못된 값을 0 으로 바꾸지 않는다 — 그런 행은 DSL 에 넣지 않고 오류로 보인다.
import type { PageContext } from '../../app/types';
import { h } from '../../ui/dom';
import { choiceField, formErrors, formRow, numberField, rows, textField } from '../../ui/form';
import { num } from '../../ui/fmt';
import { errorText, type Panel } from '../../ui/panel';
import { type Option, seg, select } from '../../ui/seg';
import { type Column, table } from '../../ui/table';
import {
  busOf,
  type Dsl,
  INDICATORS,
  mark,
  MODE_LABEL,
  NO_WRITER,
  OP_LABEL,
  type Op,
  OPS,
  type RuleExamples,
  type RuleTrial,
  type RuleWriteResult,
  ruleWriter,
  schemaIssues,
  type SchemaIssue,
  SKIP_LABEL,
  type ValidateResult,
} from './rules_api';

type Mode = 'close' | 'intraday' | 'both';
type UniKind = 'watchlist' | 'codes' | 'portfolio' | 'market';
type Logic = 'all' | 'any';

export interface CondRow {
  ind: string;
  field: string | null;
  p: Record<string, number | string>;
  op: Op;
  ref: string | null; // 참조 지표(없으면 값)
  refN: number | null;
  scale: number;
  value: number | null;
  value2: number | null;
}

export interface Model {
  name: string;
  mode: Mode;
  uni: UniKind;
  codes: string;
  watchlist: string;
  market: 'KOSPI' | 'KOSDAQ' | 'ALL';
  logic: Logic;
  cooldown: number | null;
  priority: 1 | 2 | 3;
  conds: CondRow[];
}

export const EDITOR_NOTE_NESTED = '중첩 묶음 규칙 — JSON 으로 고친다';

export function newCond(ind = 'rsi'): CondRow {
  const def = INDICATORS[ind];
  const p: Record<string, number | string> = {};
  for (const x of def?.params ?? []) p[x.key] = x.def;
  return { ind, field: def?.fields?.[0] ?? null, p, op: '<', ref: null, refN: null, scale: 1, value: 30, value2: null };
}

export function blankModel(): Model {
  return {
    name: '새 규칙',
    mode: 'close',
    uni: 'watchlist',
    codes: '',
    watchlist: 'main',
    market: 'ALL',
    logic: 'all',
    cooldown: 1440,
    priority: 2,
    conds: [newCond()],
  };
}

const TURN_OPS: readonly Op[] = ['turns_positive', 'turns_negative'];

/** 폼 → DSL. 칸이 비었거나 잘못된 조건은 DSL 에 넣지 않고 문제로 돌려준다. */
export function toDsl(m: Model): { dsl: Dsl; issues: SchemaIssue[] } {
  const issues: SchemaIssue[] = [];
  const leaves: Dsl[] = [];
  m.conds.forEach((c, i) => {
    const at = `/when/${m.logic}/${String(i)}`;
    const leaf: Dsl = { ind: c.ind, op: c.op };
    if (Object.keys(c.p).length) leaf.p = { ...c.p };
    if (c.field) leaf.field = c.field;
    if (TURN_OPS.includes(c.op)) {
      leaves.push(leaf);
      return;
    }
    if (c.op === 'between') {
      if (c.value === null || c.value2 === null) issues.push({ pointer: `${at}/value`, reason: 'required' });
      else leaf.value = [c.value, c.value2];
    } else if (c.ref) {
      const ref: Dsl = { ind: c.ref };
      if (c.refN !== null) ref.p = { n: c.refN };
      if (c.scale !== 1) ref.scale = c.scale;
      leaf.ref = ref;
    } else if (c.value === null) {
      issues.push({ pointer: `${at}/value`, reason: 'required' });
    } else {
      leaf.value = c.value;
    }
    leaves.push(leaf);
  });
  let universe: Dsl;
  if (m.uni === 'codes') universe = { codes: m.codes.split(/[\s,]+/).filter(Boolean) };
  else if (m.uni === 'portfolio') universe = { portfolio: true };
  else if (m.uni === 'market') universe = { market: m.market };
  else universe = { watchlist: m.watchlist || 'main' };
  const alert: Dsl = { priority: m.priority };
  if (m.cooldown !== null) alert.cooldown_min = m.cooldown;
  else issues.push({ pointer: '/alert/cooldown_min', reason: 'required' });
  const dsl: Dsl = { v: 1, name: m.name, universe, mode: m.mode, when: { [m.logic]: leaves }, alert };
  return { dsl, issues };
}

function numOr(v: unknown, d: number | null): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : d;
}

/** DSL → 폼(한 겹 묶음만). 중첩이면 null. */
export function fromDsl(dsl: Dsl): Model | null {
  const when = (dsl.when ?? {}) as Record<string, unknown>;
  const logic: Logic = Array.isArray(when.any) ? 'any' : 'all';
  const leaves = (when[logic] ?? []) as Record<string, unknown>[];
  if (!Array.isArray(leaves) || leaves.some((x) => 'all' in x || 'any' in x)) return null;
  const u = (dsl.universe ?? {}) as Record<string, unknown>;
  const alert = (dsl.alert ?? {}) as Record<string, unknown>;
  const m = blankModel();
  m.name = typeof dsl.name === 'string' ? dsl.name : m.name;
  m.mode = (['close', 'intraday', 'both'] as const).find((x) => x === dsl.mode) ?? 'close';
  if (Array.isArray(u.codes)) {
    m.uni = 'codes';
    m.codes = (u.codes as unknown[]).map(String).join(', ');
  } else if (u.portfolio === true) m.uni = 'portfolio';
  else if (typeof u.market === 'string') {
    m.uni = 'market';
    m.market = (['KOSPI', 'KOSDAQ', 'ALL'] as const).find((x) => x === u.market) ?? 'ALL';
  } else m.watchlist = typeof u.watchlist === 'string' ? u.watchlist : 'main';
  m.logic = logic;
  m.cooldown = numOr(alert.cooldown_min, 1440);
  m.priority = alert.priority === 1 || alert.priority === 3 ? alert.priority : 2;
  m.conds = leaves.map((x) => {
    const c = newCond(typeof x.ind === 'string' ? x.ind : 'close');
    c.op = OPS.find((o) => o === x.op) ?? '<';
    c.field = typeof x.field === 'string' ? x.field : c.field;
    if (typeof x.p === 'object' && x.p !== null) c.p = { ...c.p, ...(x.p as Record<string, number | string>) };
    const ref = x.ref as Record<string, unknown> | null | undefined;
    if (ref && typeof ref.ind === 'string') {
      c.ref = ref.ind;
      c.refN = numOr((ref.p as Record<string, unknown> | undefined)?.n, null);
      c.scale = numOr(ref.scale, 1) ?? 1;
      c.value = null;
    } else if (Array.isArray(x.value)) {
      c.value = numOr(x.value[0], null);
      c.value2 = numOr(x.value[1], null);
    } else c.value = numOr(x.value, null);
    return c;
  });
  return m;
}

const INDS: Option<string>[] = Object.entries(INDICATORS).map(([k, v]) => ({ value: k, label: v.label }));
const OP_OPTS: Option<Op>[] = OPS.map((o) => ({ value: o, label: OP_LABEL[o] }));
const REF_OPTS: Option<string>[] = [
  { value: '', label: '값' },
  ...['close', 'sma', 'ema', 'high_n', 'low_n'].map((k) => ({ value: k, label: INDICATORS[k]?.label ?? k })),
];

function condRow(c: CondRow, update: (next: CondRow) => void, redraw: () => void): HTMLElement {
  const def = INDICATORS[c.ind];
  const set = (patch: Partial<CondRow>): void => {
    Object.assign(c, patch);
    update({ ...c });
  };
  const parts: HTMLElement[] = [
    select({
      label: '지표',
      options: INDS,
      value: c.ind,
      onChange: (v) => {
        Object.assign(c, { ...newCond(v), op: c.op, value: c.value });
        update({ ...c });
        redraw();
      },
    }).el,
  ];
  if (def?.fields) {
    parts.push(
      select({
        label: '칸',
        options: def.fields.map((f) => ({ value: f, label: f })),
        value: c.field ?? def.fields[0] ?? '',
        onChange: (v) => {
          set({ field: v });
        },
      }).el,
    );
  }
  for (const prm of def?.params ?? []) {
    if (prm.choices) {
      parts.push(
        select({
          label: prm.key,
          options: prm.choices.map((x) => ({ value: x, label: x })),
          value: String(c.p[prm.key] ?? prm.def),
          onChange: (v) => {
            set({ p: { ...c.p, [prm.key]: v } });
          },
        }).el,
      );
    } else {
      const cur = c.p[prm.key];
      parts.push(
        numberField({
          label: prm.key,
          value: typeof cur === 'number' ? cur : null,
          min: prm.min,
          max: prm.max,
          integer: prm.key !== 'k',
          required: true,
          onChange: (v) => {
            if (v !== null) set({ p: { ...c.p, [prm.key]: v } });
          },
        }).el,
      );
    }
  }
  parts.push(
    select({
      label: '연산',
      options: OP_OPTS,
      value: c.op,
      onChange: (v) => {
        set({ op: v });
        redraw();
      },
    }).el,
  );
  if (!TURN_OPS.includes(c.op)) {
    if (c.op !== 'between') {
      parts.push(
        select({
          label: '기준',
          options: REF_OPTS,
          value: c.ref ?? '',
          onChange: (v) => {
            set({ ref: v || null, refN: v && v !== 'close' ? 20 : null });
            redraw();
          },
        }).el,
      );
    }
    if (c.ref && c.op !== 'between') {
      if (c.refN !== null) {
        parts.push(
          numberField({
            label: 'n',
            value: c.refN,
            min: 2,
            max: 250,
            integer: true,
            onChange: (v) => {
              set({ refN: v });
            },
          }).el,
        );
      }
      parts.push(
        numberField({
          label: '배수',
          value: c.scale,
          min: 0.01,
          max: 10,
          onChange: (v) => {
            if (v !== null) set({ scale: v });
          },
        }).el,
      );
    } else {
      parts.push(
        numberField({
          label: c.op === 'between' ? '하한' : '값',
          value: c.value,
          onChange: (v) => {
            set({ value: v });
          },
        }).el,
      );
      if (c.op === 'between')
        parts.push(
          numberField({
            label: '상한',
            value: c.value2,
            onChange: (v) => {
              set({ value2: v });
            },
          }).el,
        );
    }
  }
  return formRow(...parts);
}

function trialTable(trial: readonly RuleTrial[]): HTMLElement {
  const columns: Column<RuleTrial>[] = [
    { key: 'code', label: '종목', align: 'l', cell: (t) => (t.name ? `${t.name} ${t.code}` : t.code) },
    {
      key: 'state',
      label: '판정',
      align: 'l',
      cell: (t) =>
        t.state === 'skipped'
          ? `건너뜀 · ${SKIP_LABEL[t.skipped_reason ?? ''] ?? t.skipped_reason ?? ''}`
          : t.state === 'true'
            ? '참'
            : t.state === 'false'
              ? '거짓'
              : '판단 불가',
      cls: (t) => (t.state === 'true' ? 'up' : undefined),
    },
    {
      key: 'conds',
      label: '조건',
      align: 'l',
      cell: (t) =>
        t.conds.map((c) => `${mark(c.ok)} ${c.ind}${c.field ? `.${c.field}` : ''} ${num(c.value, 2)}`).join(' · ') ||
        null,
    },
  ];
  const sorted = [...trial].sort(
    (a, b) => Number(b.state === 'true') - Number(a.state === 'true') || a.code.localeCompare(b.code),
  );
  return table({ columns, rows: sorted, rowKey: (t) => t.code, pageSize: 10, empty: '평가할 종목 없음' }).el;
}

export async function fillEditor(ctx: PageContext, p: Panel): Promise<void> {
  const bus = busOf(ctx);
  const writer = ruleWriter(ctx.source);
  let model = blankModel();
  let editing: string | null = null;
  const form = h('div');
  const out = h('div');
  const errs = h('div');
  const json = h('textarea', { attrs: { rows: 10, spellcheck: false, 'aria-label': '규칙 JSON' } });
  const jsonBox = h(
    'label',
    { class: 'form-field', attrs: { hidden: true } },
    h('span', { class: 'form-label' }, 'JSON'),
    json,
  );
  const head = h('p', { class: 'dim' });

  function draw(): void {
    head.textContent = editing ? `편집 중: ${editing} — 저장하면 새 판` : '새 규칙';
    const conds = rows<CondRow>({
      label: '조건',
      items: model.conds,
      min: 1,
      max: 12,
      make: () => newCond(),
      render: (c, _i, update) =>
        condRow(c, update, () => {
          model.conds = conds.items();
          draw();
        }),
      onChange: (items) => {
        model.conds = items;
      },
    });
    const uniField = (): HTMLElement => {
      if (model.uni === 'codes')
        return textField({
          label: '종목코드',
          value: model.codes,
          placeholder: '005930, 000660',
          onChange: (v) => {
            model.codes = v;
          },
        }).el;
      if (model.uni === 'market') {
        return choiceField({
          label: '시장',
          options: [
            { value: 'ALL', label: '전체' },
            { value: 'KOSPI', label: '코스피' },
            { value: 'KOSDAQ', label: '코스닥' },
          ],
          value: model.market,
          onChange: (v) => {
            model.market = v;
          },
        }).el;
      }
      if (model.uni === 'watchlist')
        return textField({
          label: '관심 목록',
          value: model.watchlist,
          onChange: (v) => {
            model.watchlist = v;
          },
        }).el;
      return h('span', { class: 'dim' }, '보유 종목(매매 기록)');
    };
    form.replaceChildren(
      head,
      formRow(
        textField({
          label: '이름',
          value: model.name,
          maxLength: 80,
          onChange: (v) => {
            model.name = v;
          },
        }).el,
        choiceField<Mode>({
          label: '모드',
          options: (['close', 'intraday', 'both'] as const).map((m) => ({ value: m, label: MODE_LABEL[m] ?? m })),
          value: model.mode,
          onChange: (v) => {
            model.mode = v;
          },
        }).el,
        choiceField<'1' | '2' | '3'>({
          label: '우선순위',
          options: [
            { value: '1', label: '1' },
            { value: '2', label: '2' },
            { value: '3', label: '3' },
          ],
          value: String(model.priority) as '1' | '2' | '3',
          onChange: (v) => {
            model.priority = Number(v) as 1 | 2 | 3;
          },
        }).el,
      ),
      formRow(
        choiceField<UniKind>({
          label: '대상',
          options: [
            { value: 'watchlist', label: '관심 목록' },
            { value: 'codes', label: '종목' },
            { value: 'portfolio', label: '보유' },
            { value: 'market', label: '시장(마감만)' },
          ],
          value: model.uni,
          onChange: (v) => {
            model.uni = v;
            draw();
          },
        }).el,
        uniField(),
        numberField({
          label: '쿨다운(분)',
          value: model.cooldown,
          min: 60,
          max: 43200,
          integer: true,
          required: true,
          onChange: (v) => {
            model.cooldown = v;
          },
        }).el,
      ),
      seg<Logic>({
        label: '묶음',
        options: [
          { value: 'all', label: '모두(AND)' },
          { value: 'any', label: '하나 이상(OR)' },
        ],
        value: model.logic,
        onChange: (v) => {
          model.logic = v;
        },
      }).el,
      conds.el,
    );
  }

  function load(dsl: Dsl, ruleId: string | null): void {
    const m = fromDsl(dsl);
    editing = ruleId;
    json.value = JSON.stringify(dsl, null, 2);
    if (m) {
      model = m;
      jsonBox.hidden = true;
      formErrors(errs, []);
    } else {
      jsonBox.hidden = false;
      formErrors(errs, [{ pointer: '/when', reason: EDITOR_NOTE_NESTED }]);
    }
    draw();
  }

  /** 지금 편집 중인 DSL — JSON 칸이 열려 있으면 그것(스키마 검사), 아니면 폼 */
  function current(): Dsl | null {
    if (!jsonBox.hidden) {
      let raw: unknown;
      try {
        raw = JSON.parse(json.value) as unknown;
      } catch {
        formErrors(errs, [{ pointer: '/', reason: 'json_syntax' }]);
        return null;
      }
      const issues = schemaIssues(raw);
      formErrors(errs, issues);
      return issues.length ? null : (raw as Dsl);
    }
    const { dsl, issues } = toDsl(model);
    const all = [...issues, ...schemaIssues(dsl)];
    formErrors(errs, all);
    return all.length ? null : dsl;
  }

  async function validate(dsl: Dsl): Promise<ValidateResult | null> {
    if (!writer) return null;
    const env = await writer.post<ValidateResult>('rules/validate', { dsl });
    const r = env.data;
    formErrors(errs, r.issues);
    out.replaceChildren();
    if (r.ok) {
      out.append(
        h(
          'p',
          { class: 'dim' },
          `검사 통과 · 오늘 원장 ${String(r.trial.filter((t) => t.state === 'true').length)}/${String(r.trial.length)}종목 참`,
        ),
      );
      for (const n of env.notes) out.append(h('p', { class: 'dim' }, n));
      out.append(trialTable(r.trial));
    }
    return r;
  }

  async function run(save: boolean): Promise<void> {
    const dsl = current();
    if (!dsl || !writer) return;
    try {
      const r = await validate(dsl);
      if (!save || !r?.ok) return;
      const body = { dsl, enabled: false };
      const env =
        editing && writer.put
          ? await writer.put<RuleWriteResult>(`rules/${editing}`, body)
          : await writer.post<RuleWriteResult>('rules', body);
      editing = env.data.rule_id;
      out.prepend(
        h(
          'p',
          { class: 'msg', attrs: { role: 'status' } },
          `저장 — ${env.data.rule_id} ${String(env.data.version)}판(꺼진 채 — 목록에서 켠다)`,
        ),
      );
      head.textContent = `편집 중: ${editing} — 저장하면 새 판`;
      for (const f of bus.changed) f();
    } catch (err) {
      out.replaceChildren(h('p', { class: 'msg err' }, errorText(err)));
    }
  }

  const exampleSel = h('select', { attrs: { 'aria-label': '예시' } });
  let examples: RuleExamples['examples'] = [];
  const btn = (label: string, onClick: () => void, disabled = false): HTMLButtonElement =>
    h('button', { class: 'btn', attrs: { type: 'button', disabled }, on: { click: onClick } }, label);
  const tools = formRow(
    h('label', { class: 'form-field' }, h('span', { class: 'form-label' }, '예시'), exampleSel),
    btn('예시 불러오기', () => {
      const ex = examples.find((e) => e.id === exampleSel.value);
      if (ex) load(ex.dsl, null);
    }),
    btn('JSON 보기', () => {
      if (jsonBox.hidden) json.value = JSON.stringify(toDsl(model).dsl, null, 2);
      jsonBox.hidden = !jsonBox.hidden;
    }),
    btn('새 규칙', () => {
      load(toDsl(blankModel()).dsl, null);
    }),
  );
  const acts = formRow(
    btn('오늘 데이터로 시험', () => void run(false), writer === null),
    btn('저장', () => void run(true), writer === null),
  );
  bus.edit.push((id, dsl) => {
    load(dsl, id);
  });
  draw();
  p.clearBody();
  p.body.append(tools, form, jsonBox, acts, errs, out);
  if (!writer) out.append(h('p', { class: 'msg' }, NO_WRITER));
  try {
    const env = await ctx.source.get<RuleExamples>('rules/examples');
    examples = env.data.examples;
    exampleSel.replaceChildren(...examples.map((e) => h('option', { attrs: { value: e.id } }, `${e.id} ${e.name}`)));
    p.setSource(env);
  } catch (err) {
    exampleSel.replaceChildren(h('option', { attrs: { value: '' } }, `예시 없음 — ${errorText(err)}`));
  }
}
