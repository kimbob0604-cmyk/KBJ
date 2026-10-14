// 페이지 11 ① 규칙·② 편집기·③ 발송 이력(묶음 EVL — docs/p8_design.md §6). 합성 픽스처(tests/unit/api/web_fixtures_p8/
// rules.py). 칸 폭: 규칙 표만 전체 폭, 편집기·이력은 반폭. 404 는 '.msg', 그 밖 실패만 '.msg.err'. 쓰기 채널(데이터 소스
// `post`·`put`)이 없으면 쓰기 단추를 끄고 요청하지 않는다. 공개판은 자물쇠만(요청 0).
import { describe, expect, it } from 'vitest';
import p11, { P11_PANELS } from '../../src/pages/p11_rules';
import { blankModel, fillEditor, fromDsl, toDsl } from '../../src/pages/p11/editor';
import { evidenceLine, fillHistory, statusText } from '../../src/pages/p11/history';
import { fillRules } from '../../src/pages/p11/rules';
import {
  cooldownText,
  type Dsl,
  INDICATORS,
  NO_WRITER,
  schemaIssues,
  type SignalList,
  universeText,
} from '../../src/pages/p11/rules_api';
import schemaJson from '../../src/api/rule.schema.json';
import type { DataSource } from '../../src/data/source';
import { panel } from '../../src/ui/panel';
import { json } from '../helpers';
import { NO_DATA, RULES_NOW, rulesFixture, rulesHarness, withRuleWriter } from '../fixtures/api/rules/harness';
import { buttonByText, click, flush, pageCtx, panelByTitle, text } from './support';

interface Env<T> {
  data: T;
  notes: string[];
}

function mkPanel(title: string) {
  const p = panel('login', { title, tier: 'login', lock: 'personal', span: 6 });
  document.body.append(p.el);
  return p;
}

function ctxOf(source: DataSource) {
  return pageCtx(11, 'login', source, { now: () => RULES_NOW }).ctx;
}

const examples = (): { id: string; dsl: Dsl }[] =>
  rulesFixture<Env<{ examples: { id: string; dsl: Dsl }[] }>>('examples').data.examples;

describe('페이지 11 — 배치', () => {
  it('패널 순서·칸 폭 — 규칙 표만 전체 폭, 나머지 반폭(차트 반폭 규칙)', async () => {
    const hz = rulesHarness();
    const { ctx, root } = pageCtx(11, 'login', hz.source, { now: () => RULES_NOW });
    await p11.mount(ctx);
    await flush();
    const spans = [...root.querySelectorAll('section.panel')].map((p) => p.className);
    expect(spans[0]).toContain('span-12');
    for (const s of spans.slice(1)) expect(s).toContain('span-6');
    expect(text(panelByTitle(root, P11_PANELS.rules.title))).toContain('RSI 과매도');
    expect(text(root)).not.toMatch(/NaN|undefined/);
  });

  it('공개판은 자물쇠만 — 요청 0', async () => {
    const hz = rulesHarness();
    const { ctx, root } = pageCtx(11, 'public', hz.source, { now: () => RULES_NOW });
    await p11.mount(ctx);
    await flush();
    expect(hz.calls).toHaveLength(0);
    expect(root.querySelectorAll('section.panel.locked')).toHaveLength(Object.keys(P11_PANELS).length);
  });
});

describe('① 규칙 목록', () => {
  it('보관은 맨 아래·상태 글자·쿨다운·대상, 쓰기 채널이 없으면 단추 꺼짐 + 안내', async () => {
    const hz = rulesHarness();
    const p = mkPanel('규칙');
    await fillRules(ctxOf(hz.source), p);
    const rows = [...p.body.querySelectorAll('tbody tr')].map((tr) => text(tr));
    expect(rows).toHaveLength(5);
    expect(rows[4]).toContain('볼린저 하단 터치');
    expect(rows[4]).toContain('보관');
    expect(rows.find((r) => r.startsWith('RSI 과매도'))).toMatch(/마감켜짐관심 main24시간2.*2판/);
    expect(text(p.body)).toContain(NO_WRITER);
    const btns = [...p.body.querySelectorAll<HTMLButtonElement>('tbody button')];
    expect(btns.filter((b) => b.textContent === '켜기' || b.textContent === '끄기').every((b) => b.disabled)).toBe(
      true,
    );
    expect(text(p.el)).toContain('KBJ:rules');
  });

  it('끄기 → POST rules/<id>/disable 후 목록을 다시 읽는다', async () => {
    const hz = rulesHarness();
    const { source, writes } = withRuleWriter(hz.source);
    const p = mkPanel('규칙');
    await fillRules(ctxOf(source), p);
    const tr = [...p.body.querySelectorAll('tbody tr')].find((x) => text(x).startsWith('RSI 과매도'));
    click(tr?.querySelector('button'));
    await flush();
    expect(writes).toHaveLength(1);
    expect(writes[0]?.key).toMatch(/^rules\/r_[0-9a-z]{12}\/disable$/);
    expect(hz.paths().filter((x) => x === 'rules')).toHaveLength(2);
  });

  it('쓰기 실패는 표 아래 한 줄(.msg.err) — 목록은 그대로', async () => {
    const hz = rulesHarness();
    const { source } = withRuleWriter(hz.source, () => Promise.reject(new Error('boom')));
    const p = mkPanel('규칙');
    await fillRules(ctxOf(source), p);
    click(p.body.querySelector('tbody button'));
    await flush();
    expect(p.body.querySelector('p.msg.err')).not.toBeNull();
    expect(p.body.querySelectorAll('tbody tr')).toHaveLength(5);
  });

  it('404 는 .msg(서버 안내), 500 은 .msg.err', async () => {
    const empty = rulesHarness({ rules: () => json(NO_DATA, 404) });
    const a = mkPanel('규칙');
    await fillRules(ctxOf(empty.source), a);
    expect(a.body.querySelector('p.msg:not(.err)')?.textContent).toBe(NO_DATA.message);
    const broken = rulesHarness({ rules: () => json({}, 500) });
    const b = mkPanel('규칙');
    await fillRules(ctxOf(broken.source), b);
    expect(b.body.querySelector('p.msg.err')).not.toBeNull();
  });
});

describe('② 편집기', () => {
  it('예시 목록 10개, 쓰기 채널이 없으면 시험·저장 꺼짐', async () => {
    const hz = rulesHarness();
    const p = mkPanel('편집기');
    await fillEditor(ctxOf(hz.source), p);
    expect(p.body.querySelectorAll('option[value^="E"]')).toHaveLength(10);
    expect(buttonByText(p.body, '저장').disabled).toBe(true);
    expect(buttonByText(p.body, '오늘 데이터로 시험').disabled).toBe(true);
    expect(text(p.body)).toContain(NO_WRITER);
  });

  it('예시 불러오기 → 시험 → validate 한 번, 조건별 ✔/✖ 표', async () => {
    const hz = rulesHarness();
    const { source, writes } = withRuleWriter(hz.source);
    const p = mkPanel('편집기');
    await fillEditor(ctxOf(source), p);
    const sel = p.body.querySelector<HTMLSelectElement>('select[aria-label="예시"]');
    if (!sel) throw new Error('예시 선택 없음');
    sel.value = 'E9';
    click(buttonByText(p.body, '예시 불러오기'));
    const name = p.body.querySelector<HTMLInputElement>('input[type="text"]');
    expect(name?.value).toBe('장중 급등');
    click(buttonByText(p.body, '오늘 데이터로 시험'));
    await flush();
    expect(writes.map((w) => w.key)).toEqual(['rules/validate']);
    const dsl = (writes[0]?.body as { dsl: Dsl }).dsl;
    expect(dsl.mode).toBe('intraday');
    expect(schemaIssues(dsl)).toEqual([]);
    expect(text(p.body)).toContain('검사 통과');
    expect(p.body.querySelectorAll('tbody tr').length).toBeGreaterThan(0);
    expect(text(p.body)).toMatch(/[✔✖]/);
    expect(text(p.body)).not.toMatch(/NaN|undefined/);
  });

  it('저장 = 검사 통과 뒤 POST rules, 목록이 다시 읽힌다', async () => {
    const hz = rulesHarness();
    const { source, writes } = withRuleWriter(hz.source);
    const ctx = ctxOf(source);
    const list = mkPanel('규칙');
    const ed = mkPanel('편집기');
    await fillRules(ctx, list);
    await fillEditor(ctx, ed);
    click(buttonByText(ed.body, '저장'));
    await flush();
    expect(writes.map((w) => `${w.method} ${w.key}`)).toEqual(['POST rules/validate', 'POST rules']);
    expect((writes[1]?.body as { enabled: boolean }).enabled).toBe(false);
    expect(text(ed.body)).toContain('저장 — r_newrule00001 1판');
    expect(hz.paths().filter((x) => x === 'rules')).toHaveLength(2);
  });

  it('① 편집 → ② 에 현재 판이 실리고 저장은 PUT(새 판)', async () => {
    const hz = rulesHarness();
    const { source, writes } = withRuleWriter(hz.source);
    const ctx = ctxOf(source);
    const list = mkPanel('규칙');
    const ed = mkPanel('편집기');
    await fillRules(ctx, list);
    await fillEditor(ctx, ed);
    const tr = [...list.body.querySelectorAll('tbody tr')].find((x) => text(x).startsWith('RSI 과매도'));
    click([...(tr?.querySelectorAll('button') ?? [])].find((b) => b.textContent === '편집'));
    await flush();
    expect(text(ed.body)).toMatch(/편집 중: r_[0-9a-z]{12}/);
    expect(ed.body.querySelector<HTMLInputElement>('input[type="text"]')?.value).toBe('RSI 과매도');
    click(buttonByText(ed.body, '저장'));
    await flush();
    expect(writes.map((w) => w.method)).toEqual(['POST', 'PUT']);
    expect(writes[1]?.key).toMatch(/^rules\/r_[0-9a-z]{12}$/);
  });

  it('서버 검사 실패는 위치·사유만, 저장하지 않는다', async () => {
    const hz = rulesHarness();
    const { source, writes } = withRuleWriter(hz.source);
    const p = mkPanel('편집기');
    await fillEditor(ctxOf(source), p);
    click(buttonByText(p.body, 'JSON 보기'));
    const ta = p.body.querySelector('textarea');
    if (!ta) throw new Error('JSON 칸 없음');
    ta.value = JSON.stringify({
      v: 1,
      name: '검사 실패 예',
      universe: { watchlist: 'main' },
      mode: 'intraday',
      when: { all: [{ ind: 'new_high', op: '==', value: 1 }] },
    });
    click(buttonByText(p.body, '저장'));
    await flush();
    expect(writes.map((w) => w.key)).toEqual(['rules/validate']);
    const errs = text(p.body.querySelector('.form-errors'));
    expect(errs).toContain('/when/all/0/ind close_only');
    expect(errs).not.toContain('검사 실패 예');
  });

  it('JSON 칸 — 문법 오류·스키마 위반은 요청 없이 위치·사유만', async () => {
    const hz = rulesHarness();
    const { source, writes } = withRuleWriter(hz.source);
    const p = mkPanel('편집기');
    await fillEditor(ctxOf(source), p);
    click(buttonByText(p.body, 'JSON 보기'));
    const ta = p.body.querySelector('textarea');
    if (!ta) throw new Error('JSON 칸 없음');
    ta.value = '{ "v": 1,';
    click(buttonByText(p.body, '오늘 데이터로 시험'));
    await flush();
    expect(text(p.body.querySelector('.form-errors'))).toContain('json_syntax');
    ta.value = JSON.stringify({ v: 2, name: '', mode: 'weekly', extra: 1 });
    click(buttonByText(p.body, '오늘 데이터로 시험'));
    await flush();
    const errs = text(p.body.querySelector('.form-errors'));
    for (const want of ['/v const', '/mode enum', '/extra extra_forbidden', '/universe missing', '/name min_length']) {
      expect(errs).toContain(want);
    }
    expect(writes).toHaveLength(0);
  });
});

describe('편집기 모델·스키마', () => {
  it('예시 10개는 스키마를 지나고, 한 겹 예시는 폼 왕복 뒤에도 지난다', () => {
    for (const ex of examples()) {
      expect(schemaIssues(ex.dsl)).toEqual([]);
      const m = fromDsl(ex.dsl);
      if (m === null) continue;
      const back = toDsl(m);
      expect(back.issues).toEqual([]);
      expect(schemaIssues(back.dsl)).toEqual([]);
      expect(back.dsl.mode).toBe(ex.dsl.mode);
      expect(JSON.stringify(back.dsl.when)).toContain(
        JSON.stringify((ex.dsl.when as Record<string, Dsl[]>).all?.[0]?.ind ?? ''),
      );
    }
    expect(fromDsl(examples().find((e) => e.id === 'E10')?.dsl ?? {})).toBeNull(); // 중첩 any
  });

  it('빈 값은 0 이 아니라 문제(required)', () => {
    const m = blankModel();
    m.conds[0] = { ...m.conds[0]!, value: null };
    m.cooldown = null;
    const { issues } = toDsl(m);
    expect(issues.map((i) => i.pointer)).toEqual(['/when/all/0/value', '/alert/cooldown_min']);
  });

  it('지표 목록은 스키마의 지표 이름과 같다', () => {
    const cond = (schemaJson as { $defs: { Cond: { properties: { ind: { enum: string[] } } } } }).$defs.Cond;
    expect(Object.keys(INDICATORS).sort()).toEqual([...cond.properties.ind.enum].sort());
  });

  it('표시 문구', () => {
    expect(cooldownText(1440)).toBe('24시간');
    expect(cooldownText(90)).toBe('90분');
    expect(universeText({ codes: ['A', 'B'] })).toBe('종목 2개');
    expect(universeText({ portfolio: true })).toBe('보유 종목');
  });
});

describe('③ 발송 이력', () => {
  it('표·모드 거르개·근거 펼치기', async () => {
    const hz = rulesHarness();
    const p = mkPanel('발송 이력');
    await fillHistory(ctxOf(hz.source), p);
    const all = rulesFixture<Env<SignalList>>('signals').data.signals;
    expect(p.body.querySelectorAll('tbody tr')).toHaveLength(all.length);
    click(buttonByText(p.body, '마감'));
    expect(p.body.querySelectorAll('tbody tr')).toHaveLength(all.filter((s) => s.mode === 'close').length);
    click(buttonByText(p.body, '장중'));
    const sent = [...p.body.querySelectorAll('tbody tr')].find((tr) => text(tr).includes('보냄'));
    click(sent);
    const ev = text(p.body.querySelector('.evidence'));
    expect(ev).toContain('장중(잠정)');
    expect(ev).toMatch(/✔ /);
    expect(text(p.body)).toContain('건너뜀 · 분할 의심');
    expect(text(p.body)).not.toMatch(/NaN|undefined/);
  });

  it('근거 줄 — 판단 불가는 ?, 수급은 기준 표시', () => {
    expect(
      evidenceLine({ label: '외국인 연속 순매수', ok: null, value: null, reason: 'no_live_flow', flow: true }, true),
    ).toBe('? 외국인 연속 순매수 — 판단 불가(장중 가집계 없음) (장중 잠정)');
    expect(evidenceLine({ label: 'RSI(14)', ok: true, value: 28.44, ref_value: 30, op: '<' }, false)).toBe(
      '✔ RSI(14) 28.44 < 30.00',
    );
    const s = rulesFixture<Env<SignalList>>('signals').data.signals.find((x) => x.status === 'skipped');
    if (s) expect(statusText(s)).toMatch(/^건너뜀 · /);
  });

  it('404 는 .msg(서버 안내)', async () => {
    const hz = rulesHarness({ 'rules/signals': () => json(NO_DATA, 404) });
    const p = mkPanel('발송 이력');
    await fillHistory(ctxOf(hz.source), p);
    expect(p.body.querySelector('p.msg:not(.err)')?.textContent).toBe(NO_DATA.message);
  });
});
