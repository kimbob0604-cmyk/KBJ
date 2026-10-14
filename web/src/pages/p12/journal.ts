// 페이지 12 ④ 분석 일지 — 목록·상세(칸마다 값 출처)·작성(prefill)·마크다운 내보내기·지우기(docs/p8_design.md §4.7·§6,
// 묶음 JNL). ⑤ 포트폴리오·⑥ 상관·⑦ 추천 성과가 쓰는 쓰기 채널·실패 표시도 여기 둔다.
//
// - 쓰기 채널: 데이터 소스의 선택 기능 `post`·`patch`·`put`·`del`(로그인 CSRF — 페이지 11·12 백테스트와 같은 약속).
//   없으면 쓰기 단추를 끄고 안내만(요청하지 않는다). 이 모듈은 API 경로 문자열을 만들지 않는다(키만 — 'journal/analysis').
// - prefill 은 초안 — 칸마다 출처(KBJ 계산·컨센서스·수기)를 보이고, 저장 전에 사용자가 고친다(값을 지어내지 않는다).
//   prefill 값을 고친 칸은 출처가 '수기'로 바뀐다.
// - 데이터 없음(404)은 '.msg', 그 밖 실패만 '.msg.err'. 공개 빌드는 이 모듈을 번들에서 뺀다(모듈 최상위 부수 효과 없음).
import type { components } from '../../api/types.gen';
import type { PageContext } from '../../app/types';
import { DataError, type Envelope, LockedError } from '../../data/source';
import { h } from '../../ui/dom';
import { formRow, numberField, textField } from '../../ui/form';
import { DASH, num, pct, times } from '../../ui/fmt';
import { kv, type KvItem } from '../../ui/kv';
import { errorText, type Panel } from '../../ui/panel';
import { type Column, table } from '../../ui/table';

type S = components['schemas'];
export type AnalysisList = S['AnalysisList'];
export type AnalysisOut = S['AnalysisOut'];
export type AnalysisIn = S['AnalysisIn'];
export type AnalysisPrefill = S['AnalysisPrefill'];
export type AnalysisMarkdown = S['AnalysisMarkdown'];
type Scenario = S['Scenario3'];

export const JOURNAL_HINT = '아직 없음 — 분석 일지 없음(아래에서 작성)';
export const NO_WRITER = '쓰기 채널(로그인 CSRF) 준비 뒤 — 읽기만';
export const SOURCE_LABEL: Readonly<Record<string, string>> = {
  kbj: 'KBJ 계산',
  consensus: '컨센서스',
  manual: '수기',
};
export const CODE_RE = /^[0-9A-Z]{6}$/;
export const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

// ── 쓰기 채널·실패 표시(⑤⑥⑦ 공용) ─────────────────────────────────────────────────────────────────

export interface JournalWriter {
  post<T>(key: string, body: unknown): Promise<Envelope<T>>;
  patch?<T>(key: string, body: unknown): Promise<Envelope<T>>;
  put?<T>(key: string, body?: unknown): Promise<Envelope<T>>;
  del?<T>(key: string): Promise<Envelope<T>>;
}

export function journalWriter(source: unknown): JournalWriter | null {
  const post = (source as Partial<JournalWriter> | null)?.post;
  return typeof post === 'function' ? (source as JournalWriter) : null;
}

export function isNoData(err: unknown): err is DataError {
  return err instanceof DataError && err.status === 404;
}

/** 오류가 아닌 안내 — 데이터 없음(404)·이 소스엔 없는 로그인 등급 데이터 */
export function isNotice(err: unknown): boolean {
  return isNoData(err) || err instanceof LockedError;
}

/** 404·로그인 등급 안내는 '.msg', 그 밖은 '.msg.err' */
export function failPanel(p: Panel, err: unknown, hint: string): void {
  if (isNoData(err)) p.showMessage(err.serverMessage ?? hint);
  else if (err instanceof LockedError) p.showMessage(errorText(err));
  else p.showError(err);
}

/** 쓰기 결과 줄(성공·거절) — 거절은 사유만(입력값을 되풀이하지 않는다) */
export function status(el: HTMLElement, ok: boolean, text: string): void {
  el.className = ok ? 'msg' : 'msg err';
  el.textContent = text;
}

/** null·undefined(값 없음) */
export function nil(v: unknown): v is null | undefined {
  return v === null || v === undefined;
}

export function won(v: number | null | undefined): string {
  return v === null || v === undefined ? DASH : `${num(v)}원`;
}

/** 오늘(KST) YYYY-MM-DD — 입력 칸 기본값 */
export function kstToday(now: Date): string {
  return new Date(now.getTime() + 9 * 3600_000).toISOString().slice(0, 10);
}

// ── 목록·상세 ───────────────────────────────────────────────────────────────────────────────────

const COLUMNS: readonly Column<AnalysisOut>[] = [
  { key: 'date', label: '분석일', align: 'l', cell: (a) => a.analyzed_on },
  { key: 'code', label: '종목', align: 'l', cell: (a) => a.code },
  {
    key: 'conv',
    label: '반영도',
    align: 'l',
    cell: (a) => a.conviction ?? null,
  },
  {
    key: 'tp',
    label: '적정가(기본)',
    cell: (a) => (nil(a.tp.base) ? null : num(a.tp.base)),
  },
  { key: 'end', label: '결론', align: 'l', cell: (a) => a.conclusion ?? null },
];

function src(a: Pick<AnalysisOut, 'value_sources'>, key: string): string | undefined {
  const s = a.value_sources?.[key];
  return s ? (SOURCE_LABEL[s] ?? s) : undefined;
}

function three(m: Scenario | undefined, f: (v: number) => string): string {
  return [m?.bear, m?.base, m?.bull].map((v) => (nil(v) ? DASH : f(v))).join(' / ');
}

/** 상세 칸(값 출처는 hint 로 — 색만으로 전하지 않는다) */
export function detailItems(a: AnalysisOut): KvItem[] {
  return [
    { label: '현재가', value: won(a.price), hint: src(a, 'price') },
    {
      label: '52주 수익률',
      value: nil(a.ret_52w) ? null : pct(a.ret_52w, 1),
      hint: src(a, 'ret_52w'),
    },
    {
      label: 'Fwd PER',
      value: nil(a.fwd_per) ? null : times(a.fwd_per, 1),
      hint: src(a, 'fwd_per'),
    },
    {
      label: 'PER 밴드 위치',
      value: nil(a.band_pos) ? null : `${num(a.band_pos, 0)}%`,
      hint: src(a, 'band_pos'),
    },
    {
      label: 'EV/EBITDA',
      value: nil(a.ev_ebitda) ? null : times(a.ev_ebitda, 1),
      hint: src(a, 'ev_ebitda'),
    },
    {
      label: '영업이익률',
      value: nil(a.opm) ? null : pct(a.opm, 1, false),
      hint: src(a, 'opm'),
    },
    {
      label: 'EPS 약세/기본/강세',
      value: three(a.eps, (v) => num(v)),
      hint: src(a, 'eps'),
    },
    {
      label: '적정가 약세/기본/강세',
      value: three(a.tp, (v) => num(v)),
      hint: a.tp_source ?? src(a, 'tp'),
    },
    { label: '태그', value: a.tags?.length ? a.tags.join(', ') : null },
    { label: '기록 출처', value: a.source },
  ];
}

// ── 작성 칸 ─────────────────────────────────────────────────────────────────────────────────────

export interface Editor {
  el: HTMLElement;
  load(f: Partial<AnalysisIn>): void;
  /** 본문 또는 형식 오류 사유 */
  body(): AnalysisIn | string;
  code(): string;
}

export function editor(today: string): Editor {
  const code = textField({
    label: '종목코드',
    maxLength: 6,
    placeholder: '005930',
  });
  const date = textField({ label: '분석일', value: today, maxLength: 10 });
  const price = numberField({ label: '현재가', min: 0 });
  const fwd = numberField({ label: 'Fwd PER', min: 0 });
  const bear = numberField({ label: '적정가 약세', min: 0 });
  const base = numberField({ label: '적정가 기본', min: 0 });
  const bull = numberField({ label: '적정가 강세', min: 0 });
  const conv = textField({ label: '반영도', maxLength: 50 });
  const memo = textField({ label: '메모', maxLength: 2000 });
  const end = textField({ label: '결론', maxLength: 200 });
  let carried: Partial<AnalysisIn> = {};
  const el = h(
    'div',
    { class: 'jr-editor' },
    formRow(code.el, date.el, price.el, fwd.el),
    formRow(bear.el, base.el, bull.el),
    formRow(conv.el, memo.el, end.el),
  );
  return {
    el,
    code: () => code.value().toUpperCase(),
    load(f) {
      carried = { ...f };
      if (f.code) code.set(f.code);
      if (f.analyzed_on) date.set(f.analyzed_on);
      price.set(f.price ?? null);
      fwd.set(f.fwd_per ?? null);
      bear.set(f.tp?.bear ?? null);
      base.set(f.tp?.base ?? null);
      bull.set(f.tp?.bull ?? null);
      conv.set(f.conviction ?? '');
      memo.set(f.memo ?? '');
      end.set(f.conclusion ?? '');
    },
    body() {
      const c = code.value().toUpperCase();
      if (!CODE_RE.test(c)) return '종목코드 형식(6자리)';
      const d = date.value();
      if (!DATE_RE.test(d)) return '분석일 형식(YYYY-MM-DD)';
      const same = carried.code === c;
      const prev: Partial<AnalysisIn> = same ? carried : {};
      let sources: Record<string, 'kbj' | 'consensus' | 'manual'> = {
        ...(prev.value_sources ?? {}),
      };
      const tp = { bear: bear.value(), base: base.value(), bull: bull.value() };
      const touched: [string, unknown, unknown][] = [
        ['price', price.value(), prev.price ?? null],
        ['fwd_per', fwd.value(), prev.fwd_per ?? null],
        [
          'tp',
          JSON.stringify(tp),
          JSON.stringify({
            bear: prev.tp?.bear ?? null,
            base: prev.tp?.base ?? null,
            bull: prev.tp?.bull ?? null,
          }),
        ],
      ];
      for (const [k, now, before] of touched) {
        if (now !== before) {
          if (now === null || now === JSON.stringify({ bear: null, base: null, bull: null })) {
            sources = Object.fromEntries(Object.entries(sources).filter(([key]) => key !== k));
          } else sources[k] = 'manual';
        }
      }
      const kept = prev as Omit<AnalysisIn, 'code' | 'analyzed_on'>;
      return {
        ...kept,
        code: c,
        analyzed_on: d,
        price: price.value(),
        fwd_per: fwd.value(),
        tp,
        conviction: conv.value() || null,
        memo: memo.value() || null,
        conclusion: end.value() || null,
        value_sources: sources,
      };
    },
  };
}

// ── 패널 ────────────────────────────────────────────────────────────────────────────────────────

export async function fillJournal(ctx: PageContext, p: Panel): Promise<void> {
  const w = journalWriter(ctx.source);
  let env: Envelope<AnalysisList>;
  try {
    env = await ctx.source.get<AnalysisList>('journal/analysis');
  } catch (err) {
    failPanel(p, err, JOURNAL_HINT);
    return;
  }
  p.clearBody();
  p.setSource(env);
  const detail = h('div', { class: 'jr-detail' });
  const msg = h('p', { class: 'msg' }, w ? '' : NO_WRITER);
  const ed = editor(kstToday(ctx.now()));
  const reload = (): Promise<void> => fillJournal(ctx, p);
  const fail = (err: unknown): void => {
    status(msg, false, errorText(err));
  };

  const show = (a: AnalysisOut): void => {
    const md = h('pre', { class: 'jr-md', attrs: { hidden: true } });
    const mdBtn = h('button', { class: 'btn', attrs: { type: 'button' } }, '마크다운');
    mdBtn.addEventListener('click', () => {
      ctx.source
        .get<AnalysisMarkdown>(`journal/analysis/${a.id}/markdown`)
        .then((r) => {
          md.textContent = r.data.markdown;
          md.hidden = false;
        })
        .catch(fail);
    });
    const edit = h('button', { class: 'btn', attrs: { type: 'button' } }, '작성 칸으로');
    edit.addEventListener('click', () => {
      ed.load(a);
    });
    const del = h('button', { class: 'btn', attrs: { type: 'button', disabled: !w?.del } }, '지우기');
    del.addEventListener('click', () => {
      if (!w?.del) return;
      w.del<S['Deleted']>(`journal/analysis/${a.id}`).then(reload, fail);
    });
    detail.replaceChildren(
      h('p', { class: 'p8-title' }, h('b', null, a.code), ' ', h('small', null, a.analyzed_on)),
      kv(detailItems(a)),
      a.memo ? h('p', { class: 'def' }, a.memo) : '',
      formRow(mdBtn, edit, del),
      md,
    );
  };

  const list = table<AnalysisOut>({
    caption: '분석 일지',
    columns: COLUMNS,
    rows: env.data.items,
    rowKey: (a) => String(a.id),
    onSelect: show,
    empty: JOURNAL_HINT,
  });

  const prefill = h('button', { class: 'btn', attrs: { type: 'button' } }, '값 불러오기(prefill)');
  prefill.addEventListener('click', () => {
    const code = ed.code();
    if (!CODE_RE.test(code)) {
      status(msg, false, '종목코드 형식(6자리)');
      return;
    }
    ctx.source.get<AnalysisPrefill>(`journal/analysis/prefill/${code}`).then(
      (r) => {
        ed.load(r.data.fields);
        const n = Object.keys(r.data.fields.value_sources ?? {}).length;
        status(msg, true, `불러옴 — ${n}칸(출처 표시) · 저장 전에 확인`);
      },
      (err: unknown) => {
        if (isNoData(err)) status(msg, true, err.serverMessage ?? '불러올 값 없음');
        else fail(err);
      },
    );
  });
  const save = h('button', { class: 'btn', attrs: { type: 'button', disabled: !w } }, '저장');
  save.addEventListener('click', () => {
    if (!w) return;
    const b = ed.body();
    if (typeof b === 'string') {
      status(msg, false, b);
      return;
    }
    w.post<AnalysisOut>('journal/analysis', b).then(reload, fail);
  });
  p.body.append(list.el, detail, h('h3', { class: 'sub' }, '작성'), ed.el, formRow(prefill, save), msg);
}
