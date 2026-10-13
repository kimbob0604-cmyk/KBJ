// 페이지 10 '지수 정기변경' 패널(P5 2차 묶음 IDX — docs/p5_index_rebalance_prompt.md §0-3, metrics §24). 로그인 전용:
// 구성종목·컷오프 추정이 들어가 공개 빌드·내보내기에 없다(p10_etf.ts 가 MODE 'public' 이면 이 모듈을 번들에서 뺀다).
//
// - 지수 탭(MSCI Korea·KOSPI200·KOSDAQ150) · 리뷰 고르기 · 일정 띠(기준일·발표·리밸런싱(매매일)·효력일 + D-day).
// - 컷오프: 엔진 추정(구간·방법)과 사용자 입력(증권사 추정치 — set-cutoff)을 나란히(요약 표 한 줄씩), 후보 표는 컷오프마다.
// - 후보 표 열 = 사용자 예시: 종목·시가총액·유동시총·유동비율·시총/유동시총 컷오프 괴리율·조건 충족(O/X)·필요 주가 상승률
//   (시총·유동 중 큰 쪽)·예상 패시브 매수(억)·일평균 거래대금 대비 배수. 필요 상승률 오름차순(서버도 같은 순 — 화면에서 다시 맞춘다).
// - 구성종목 목록이 없으면 표 대신 '목록 없음' 한 줄(지어내지 않는다). 일정 미확인·엔진 추정값은 '추정'.
// - 행을 누르면 종목 상세(페이지 8).
import type { components } from '../../api/types.gen';
import { openStock } from '../../app/route';
import type { PageContext } from '../../app/types';
import type { Envelope } from '../../data/source';
import { clear, h } from '../../ui/dom';
import { DASH, eokNum, joEok, num, pct, shortDate, signClass, times } from '../../ui/fmt';
import { kv, type KvItem } from '../../ui/kv';
import { type Panel, panel } from '../../ui/panel';
import { EST_LABEL, estBadge, visibleRows } from '../../ui/quality';
import { type Option, seg, select } from '../../ui/seg';
import { type Column, table } from '../../ui/table';
import { fill, filters, nameCell } from '../p1_market';

type S = components['schemas'];
export type IndexReview = S['IndexReview'];
export type ReviewDates = S['ReviewDates'];
export type IndexRun = S['IndexRunView'];
export type Candidate = S['CandidateRow'];
type CutoffKind = IndexRun['cutoff_kind'];

export const INDEX_TITLE = '지수 정기변경';
export const INDEX_HINT = 'index.predict 17:20(장 마감 뒤)';
export const INDEX_LOCK = '구성종목·컷오프 추정 — 로그인';
export const KIND_LABEL: Record<CutoffKind, string> = { model: '엔진 추정', user: '사용자 입력' };
export const VERDICT_LABEL: Record<Candidate['verdict'], string> = {
  add: '편입 후보',
  delete: '편출 후보',
  borderline: '경계',
  keep: '유지',
};
const TABLES: readonly { key: 'adds' | 'deletes' | 'borderline'; title: string }[] = [
  { key: 'adds', title: '편입 후보' },
  { key: 'deletes', title: '편출 후보' },
  { key: 'borderline', title: '경계' },
];

/** 'D-12'·'D-day'·'D+3'(지났다) */
export function dday(n: number): string {
  return n === 0 ? 'D-day' : n > 0 ? `D-${String(n)}` : `D+${String(-n)}`;
}

/** 비율(0.124) → '+12.4%' */
export function ratioPct(v: number | null | undefined, digits = 1): string {
  return typeof v === 'number' && Number.isFinite(v) ? pct(v * 100, digits) : DASH;
}

/** 필요 상승률 오름차순(없는 값은 뒤로) — 같으면 서버 순서 그대로 */
export function sortByRise(rows: readonly Candidate[]): Candidate[] {
  const key = (r: Candidate): number =>
    typeof r.required_rise === 'number' && Number.isFinite(r.required_rise) ? r.required_rise : Infinity;
  return [...rows].sort((a, b) => key(a) - key(b));
}

/** 일정 띠 — 기준일·발표·리밸런싱(패시브 매매일)·효력일과 D-day. 확인 전 일정은 값마다 '추정' */
export function bandItems(rv: ReviewDates): KvItem[] {
  const q: 'estimated' | null = rv.confirmed ? null : 'estimated';
  const one = (label: string, day: string, d: number, hint?: string): KvItem => ({
    label,
    value: `${shortDate(day)} · ${dday(d)}`,
    cls: d === 0 ? 'up' : d < 0 ? 'dim' : undefined,
    quality: q,
    est: EST_LABEL,
    hint,
  });
  return [
    one('기준일', rv.base_date, rv.d_base, '가격 기준'),
    one('발표', rv.announce_date, rv.d_announce),
    one('리밸런싱', rv.trade_date, rv.d_trade, '패시브 매매(효력 전 영업일 종가)'),
    one('효력일', rv.effective_date, rv.d_effective),
  ];
}

export function candidateColumns(): Column<Candidate>[] {
  return [
    { key: 'name', label: '종목', align: 'l', cell: (r) => nameCell(r.name, r.code) },
    { key: 'mktcap', label: '시가총액', cell: (r) => joEok(r.mktcap) },
    { key: 'ff_mcap', label: '유동시총', cell: (r) => joEok(r.ff_mcap) },
    {
      key: 'fif',
      label: '유동비율',
      cell: (r) => (typeof r.fif === 'number' ? h('span', { attrs: { title: r.fif_how ?? '' } }, pct(r.fif * 100, 1, false)) : null),
    },
    { key: 'gap_mcap', label: '시총 컷오프 괴리율', cell: (r) => ratioPct(r.gap_mcap), cls: (r) => signClass(r.gap_mcap) },
    { key: 'gap_ff', label: '유동시총 컷오프 괴리율', cell: (r) => ratioPct(r.gap_ff), cls: (r) => signClass(r.gap_ff) },
    {
      key: 'condition_met',
      label: '조건 충족',
      cell: (r) => (r.condition_met === null ? null : r.condition_met ? 'O' : 'X'),
      cls: (r) => (r.condition_met ? 'up' : 'dim'),
    },
    {
      key: 'required_rise',
      label: '필요 주가 상승률',
      cell: (r) =>
        typeof r.required_rise === 'number'
          ? h(
              'span',
              null,
              ratioPct(r.required_rise),
              r.required_basis ? h('small', null, r.required_basis === 'ff' ? '유동' : '시총') : null,
            )
          : null,
      cls: (r) => signClass(r.required_rise),
    },
    { key: 'passive_won', label: '예상 패시브 매수(억)', cell: (r) => eokNum(r.passive_won, 0, true), cls: (r) => signClass(r.passive_won) },
    { key: 'adv_multiple', label: '거래대금 대비', cell: (r) => times(r.adv_multiple, 1) },
  ];
}

function cutoffText(run: IndexRun): string {
  const c = run.cutoff;
  if (!c) return DASH;
  return joEok(c.won);
}

function rangeText(run: IndexRun): string {
  const c = run.cutoff;
  if (!c || c.low_won === null || c.high_won === null) return DASH;
  return `${joEok(c.low_won)} ~ ${joEok(c.high_won)}`;
}

/** 컷오프 나란히 — 엔진 추정·사용자 입력 한 줄씩(근거: 구간·방법·구성종목 수·추종 자금) */
export function runColumns(): Column<IndexRun>[] {
  const est = (r: IndexRun): Node | string => (r.cutoff_kind === 'model' && r.cutoff ? h('span', { class: 'q-est' }, cutoffText(r), estBadge()) : cutoffText(r));
  return [
    { key: 'kind', label: '컷오프', align: 'l', cell: (r) => KIND_LABEL[r.cutoff_kind] },
    { key: 'won', label: '값', cell: est },
    { key: 'range', label: '추정 구간', cell: rangeText },
    { key: 'method', label: '근거', align: 'l', cell: (r) => r.cutoff?.method ?? null },
    { key: 'n_add', label: '편입', cell: (r) => num(r.n_add) },
    { key: 'n_delete', label: '편출', cell: (r) => num(r.n_delete) },
    { key: 'n_borderline', label: '경계', cell: (r) => num(r.n_borderline) },
    { key: 'members', label: '구성종목', cell: (r) => num(r.n_members) },
    { key: 'aum', label: '추종 자금', cell: (r) => joEok(r.aum_won) },
  ];
}

/** 한 컷오프의 후보 표 셋(편입·편출·경계). 목록 없음·데이터 없음이면 한 줄. 돌려주는 값 = 뺀 invalid 행 수 */
export function drawRun(box: HTMLElement, run: IndexRun, withKind: boolean): number {
  const head = `${withKind ? `${KIND_LABEL[run.cutoff_kind]} 컷오프 · ` : ''}${shortDate(run.base_date)} 기준`;
  const section = h('div', { data: { run: run.cutoff_kind, status: run.status } });
  box.append(section);
  if (run.status !== 'ok') {
    section.append(
      h(
        'p',
        { class: 'msg', data: { section: 'run-empty' } },
        `${head} — ${run.status === 'no_constituents' ? '구성종목 목록 없음 — 예측하지 않는다' : '데이터 없음'}`,
      ),
    );
    return 0;
  }
  let invalid = 0;
  for (const t of TABLES) {
    const { rows, invalid: bad } = visibleRows(run[t.key]);
    invalid += bad;
    const shown = sortByRise(rows);
    section.append(
      h(
        'p',
        { class: 'def', data: { section: `${run.cutoff_kind}-${t.key}` } },
        `${head} · ${t.title} ${num(shown.length)}`,
        run.quality === 'estimated' ? estBadge() : null,
        t.key === 'deletes' ? ' — 필요 상승률 = 유지에 필요한 상승' : '',
      ),
      table<Candidate>({
        caption: `${KIND_LABEL[run.cutoff_kind]} ${t.title}`,
        columns: candidateColumns(),
        rows: shown,
        rowKey: (r) => r.code,
        onSelect: (r) => void openStock(r.code),
        pageSize: 30,
        empty: `${t.title} 없음`,
      }).el,
    );
  }
  if (run.notes.length) section.append(h('p', { class: 'def' }, run.notes.join(' · ')));
  return invalid;
}

export function drawReview(p: Panel, env: Envelope<IndexReview>): number {
  const d = env.data;
  const rv = d.review;
  if (rv) {
    p.body.append(
      h(
        'p',
        { class: 'def', data: { section: 'band-head' } },
        `${rv.index_title} ${rv.review_id} 리뷰`,
        rv.confirmed ? '' : ' — 일정 추정(공표 일정표 대조 전) ',
        rv.confirmed ? null : estBadge(),
      ),
      h('div', { data: { section: 'band' } }, kv(bandItems(rv))),
    );
  }
  if (!d.runs.length) {
    p.body.append(h('p', { class: 'msg', data: { section: 'no-runs' } }, env.notes[0] ?? `예측 없음 — ${INDEX_HINT}`));
    return 0;
  }
  p.body.append(
    table<IndexRun>({
      caption: '컷오프 — 엔진 추정과 사용자 입력 나란히',
      columns: runColumns(),
      rows: d.runs,
      rowKey: (r) => r.cutoff_kind,
      empty: '컷오프 없음',
    }).el,
  );
  const box = h('div', { data: { section: 'runs' } });
  p.body.append(box);
  const many = d.runs.length > 1;
  return d.runs.reduce((n, run) => n + drawRun(box, run, many), 0);
}

export interface IndexReviewWidget {
  readonly panel: Panel;
  load(): Promise<void>;
}

/** 페이지 10 에 '지수 정기변경' 패널을 붙인다(로그인 빌드만 부른다). */
export function mountIndexReview(ctx: Pick<PageContext, 'tier' | 'root' | 'source'>): IndexReviewWidget {
  const p = panel(ctx.tier, { title: INDEX_TITLE, tier: 'login', span: 12, lock: INDEX_LOCK });
  ctx.root.append(p.el);
  if (p.locked) return { panel: p, load: () => Promise.resolve() };
  p.setDef(
    '컷오프 대비 괴리율 = 값 ÷ 컷오프 − 1. 필요 주가 상승률 = 시총·유동시총 조건을 둘 다 넘는 최소 상승(음수 = 그만큼 내려도 충족). 예상 패시브 매수 = 추종 자금 × 편입 비중(편출은 −). 규칙·컷오프는 추정(metrics §24).',
  );
  const q = { index: null as string | null, review: null as string | null };
  const tabBox = h('span');
  const reviewBox = h('span');
  let tabsFor = '';
  let reviewsFor = '';

  const drawControls = (d: IndexReview): void => {
    const ids = d.indexes.map((t) => t.id).join(',');
    if (ids !== tabsFor) {
      tabsFor = ids;
      clear(tabBox);
      tabBox.append(
        seg<string>({
          label: '지수',
          options: d.indexes.map((t): Option<string> => ({ value: t.id, label: t.title })),
          value: d.index_id,
          onChange: (v) => {
            q.index = v;
            q.review = null;
            void load();
          },
        }).el,
      );
    }
    const rid = d.review?.review_id ?? '';
    const key = `${d.index_id}:${d.reviews.map((r) => r.review_id).join(',')}:${rid}`;
    if (key !== reviewsFor) {
      reviewsFor = key;
      clear(reviewBox);
      if (d.reviews.length) {
        reviewBox.append(
          select<string>({
            label: '리뷰',
            options: d.reviews.map((r) => ({
              value: r.review_id,
              label: `${r.review_id}${r.d_effective < 0 ? ' (지남)' : ''}`,
            })),
            value: rid,
            onChange: (v) => {
              q.index = d.index_id;
              q.review = v;
              void load();
            },
          }).el,
        );
      }
    }
  };

  const load = (): Promise<void> =>
    fill(
      p,
      () => ctx.source.get<IndexReview>('index-review', { index: q.index, review: q.review }),
      (env) => {
        drawControls(env.data);
        return drawReview(p, env);
      },
      INDEX_HINT,
    );
  filters(p, tabBox, reviewBox);
  return { panel: p, load };
}
