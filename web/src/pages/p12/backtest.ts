// 페이지 12 ① 백테스트(규칙·기간·보유·손절/익절 → 요약·거래 표)·② 누적 곡선(반폭)(docs/p8_design.md §4.6·§6, 묶음 BKT).
//
// - 처음: 야간 실행 요약(GET backtest/runs — rules.backtest_nightly 08:45, 3년)을 규칙별로 보인다.
// - '실행': 즉석 백테스트(POST backtest — 저장 없음, 상한 config/rules.yaml adhoc). 쓰기 채널(CSRF)은 데이터 소스의
//   선택 기능 `post` — 없으면 실행 칸에 안내만(요청하지 않는다). 결과가 오면 ② 누적 곡선을 같이 그린다.
// - 체결 규칙·비용·생존편향 문구는 봉투 notes(원천 줄)에 — 수익을 약속하는 문구 없음.
// - 데이터 없음(404)은 '.msg', 그 밖 실패만 '.msg.err'. 공개 빌드는 이 모듈을 번들에서 뺀다(모듈 최상위 부수 효과 없음).
import type { components } from '../../api/types.gen';
import type { PageContext } from '../../app/types';
import type { Envelope } from '../../data/source';
import { h } from '../../ui/dom';
import { formRow, numberField, textField } from '../../ui/form';
import { num, pct, shortDate, times } from '../../ui/fmt';
import { kv } from '../../ui/kv';
import { lineChart } from '../../ui/line';
import { errorText, type Panel } from '../../ui/panel';
import { type Option, seg, select } from '../../ui/seg';
import { type Column, table } from '../../ui/table';
import { failPanel, isNoData, pctCell } from '../p11/perf';

export type BacktestRuns = components['schemas']['BacktestRuns'];
export type BacktestRun = components['schemas']['BacktestRunOut'];
export type BacktestResult = components['schemas']['BacktestResult'];
export type BacktestBody = components['schemas']['BacktestIn'];
type Trade = components['schemas']['BacktestTrade'];
type Mode = BacktestBody['mode'];

export const BT_HINT = '아직 없음 — rules.backtest_nightly 08:45';
export const NO_WRITER = '즉석 실행은 쓰기 채널(로그인 CSRF) 준비 뒤 — 야간 결과만 보인다';
export const CURVE_WAIT = '백테스트를 실행하면 누적 곡선(거래 단위 복리)을 그린다';
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const REASONS: Readonly<Record<Trade['reason'], string>> = {
  stop: '손절',
  target: '익절',
  hold: '보유 만기',
  open_end: '진행 중',
};
const MODES: readonly Option<NonNullable<Mode>>[] = [
  { value: 'close', label: '마감' },
  { value: 'intraday_replay', label: '장중 재생' },
];

/** 쓰기 채널 — 데이터 소스가 갖고 있으면 쓴다(로그인 CSRF·Origin 을 소스가 붙인다). */
export interface BacktestWriter {
  post<T>(key: string, body: unknown): Promise<Envelope<T>>;
}

export function writerOf(source: unknown): BacktestWriter | null {
  const post = (source as Partial<BacktestWriter> | null)?.post;
  return typeof post === 'function' ? (source as BacktestWriter) : null;
}

// 페이지 문맥마다 한 번 — ①·③ 이 나눈다
let runsCache: WeakMap<PageContext, Promise<Envelope<BacktestRuns>>> | undefined;
export function loadRuns(ctx: PageContext): Promise<Envelope<BacktestRuns>> {
  runsCache ??= new WeakMap();
  let got = runsCache.get(ctx);
  if (!got) {
    got = ctx.source.get<BacktestRuns>('backtest/runs');
    runsCache.set(ctx, got);
  }
  return got;
}

/** 규칙마다 가장 최근 실행 하나(서버가 최신 먼저 준다) */
export function latestRuns(runs: readonly BacktestRun[]): BacktestRun[] {
  const seen = new Set<string>();
  const out: BacktestRun[] = [];
  for (const r of runs) {
    if (seen.has(r.rule_id)) continue;
    seen.add(r.rule_id);
    out.push(r);
  }
  return out;
}

function rec(v: unknown): Record<string, unknown> {
  return v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function numOrNull(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/** 야간 요약(summary.trades) — 서버 칸 이름 그대로, 모르는 값은 null */
export interface TradeSummary {
  n: number | null;
  win_rate: number | null;
  mean: number | null;
  median: number | null;
  profit_factor: number | null;
  mdd: number | null;
  annualized: number | null;
}

export function tradeSummary(summary: Record<string, unknown>): TradeSummary {
  const t = rec(summary.trades);
  return {
    n: numOrNull(t.n),
    win_rate: numOrNull(t.win_rate),
    mean: numOrNull(t.mean),
    median: numOrNull(t.median),
    profit_factor: numOrNull(t.profit_factor),
    mdd: numOrNull(t.mdd),
    annualized: numOrNull(t.annualized),
  };
}

function summaryKv(s: TradeSummary, caption: string): HTMLElement {
  return h(
    'div',
    { class: 'bt-summary' },
    h('p', { class: 'dim' }, caption),
    kv([
      ['거래', num(s.n)],
      ['승률', pct(s.win_rate, 1, false)],
      ['평균(건)', pctCell(s.mean)],
      ['중앙(건)', pctCell(s.median)],
      ['PF', times(s.profit_factor)],
      ['MDD', pct(s.mdd === null ? null : s.mdd * 100, 1)],
      ['연환산', pct(s.annualized === null ? null : s.annualized * 100, 1)],
    ]),
  );
}

const TRADE_COLUMNS: readonly Column<Trade>[] = [
  { key: 'code', label: '종목', align: 'l', cell: (t) => t.code },
  { key: 'sig', label: '신호', cell: (t) => shortDate(t.signal_date) },
  {
    key: 'in',
    label: '진입',
    cell: (t) => `${shortDate(t.entry_date)} ${num(t.entry_price)}`,
  },
  {
    key: 'out',
    label: '청산',
    cell: (t) => `${shortDate(t.exit_date)} ${num(t.exit_price)}`,
  },
  { key: 'why', label: '사유', align: 'l', cell: (t) => REASONS[t.reason] },
  { key: 'ret', label: '수익', cell: (t) => pctCell(t.ret_pct) },
];

function resultView(r: BacktestResult): HTMLElement {
  const s = r.summary;
  const head = summaryKv(
    {
      n: s.n,
      win_rate: s.win_rate ?? null,
      mean: s.mean ?? null,
      median: s.median ?? null,
      profit_factor: s.profit_factor ?? null,
      mdd: s.mdd ?? null,
      annualized: s.annualized ?? null,
    },
    `즉석 ${r.start} ~ ${r.end}${r.cached ? ' · 캐시' : ''}`,
  );
  const trades = [...r.trades].reverse();
  return h(
    'div',
    null,
    head,
    table<Trade>({
      caption: '거래(최근 먼저) — 비용 반영 순수익',
      columns: TRADE_COLUMNS,
      rows: trades,
      rowKey: (t) => `${t.code}:${t.signal_date}:${t.entry_date}`,
      pageSize: 20,
      empty: '거래 없음',
    }).el,
  );
}

interface Shared {
  curve: Panel | null;
  result: BacktestResult | null;
}
let sharedState: WeakMap<PageContext, Shared> | undefined;
function shared(ctx: PageContext): Shared {
  sharedState ??= new WeakMap();
  let s = sharedState.get(ctx);
  if (!s) {
    s = { curve: null, result: null };
    sharedState.set(ctx, s);
  }
  return s;
}

function drawCurve(p: Panel, env: Envelope<BacktestResult>): void {
  const r = env.data;
  p.setSource(env);
  if (r.curve.length === 0) {
    p.showMessage('거래 없음 — 곡선을 그릴 수 없다');
    return;
  }
  p.clearBody();
  p.body.append(
    lineChart({
      label: `누적(거래 단위 복리) ${r.start} ~ ${r.end}`,
      x: r.curve.map((c) => c.date),
      series: [{ label: '누적(배)', values: r.curve.map((c) => c.equity) }],
      format: (v) => times(v),
    }).el,
  );
}

function readBody(
  rule: string,
  mode: Mode,
  f: {
    start: string;
    end: string;
    hold: number | null;
    stop: number | null;
    target: number | null;
  },
): BacktestBody | string {
  if (!DATE.test(f.start) || !DATE.test(f.end)) return '기간은 YYYY-MM-DD';
  if (f.start > f.end) return '시작이 끝보다 늦다';
  if (f.hold === null) return '보유 일수를 넣는다(1~120)';
  return {
    rule_id: rule,
    start: f.start,
    end: f.end,
    mode,
    hold: f.hold,
    stop_pct: f.stop,
    target_pct: f.target,
  };
}

export async function fillBacktest(ctx: PageContext, p: Panel): Promise<void> {
  let env: Envelope<BacktestRuns>;
  try {
    env = await loadRuns(ctx);
  } catch (err) {
    failPanel(p, err, BT_HINT);
    return;
  }
  p.setSource(env);
  const runs = latestRuns(env.data.runs);
  const first = runs[0];
  if (!first) {
    p.showMessage(BT_HINT);
    return;
  }
  p.clearBody();
  const nightly = h('div', { class: 'bt-nightly' });
  const out = h('div', { class: 'bt-result' });
  const showNightly = (r: BacktestRun): void => {
    nightly.replaceChildren(
      summaryKv(tradeSummary(r.summary), `야간 ${r.start} ~ ${r.end} · ${r.mode === 'close' ? '마감' : '장중 재생'}`),
    );
  };
  const mode = seg<NonNullable<Mode>>({
    label: '모드',
    options: MODES,
    value: first.mode,
  });
  const start = textField({ label: '시작', value: first.start, maxLength: 10 });
  const end = textField({ label: '끝', value: first.end, maxLength: 10 });
  const hold = numberField({
    label: '보유(일)',
    value: 5,
    min: 1,
    max: 120,
    integer: true,
    required: true,
  });
  const stop = numberField({
    label: '손절 %',
    value: null,
    min: -50,
    max: -0.1,
  });
  const target = numberField({
    label: '익절 %',
    value: null,
    min: 0.1,
    max: 200,
  });
  let current = first;
  const rule = select<string>({
    label: '규칙',
    options: runs.map((r) => ({ value: r.rule_id, label: r.rule_id })),
    value: first.rule_id,
    onChange: (v) => {
      const r = runs.find((x) => x.rule_id === v);
      if (!r) return;
      current = r;
      showNightly(r);
      start.set(r.start);
      end.set(r.end);
      mode.set(r.mode);
    },
  });
  const writer = writerOf(ctx.source);
  const run = h('button', { class: 'btn', attrs: { type: 'button' } }, '실행');
  run.addEventListener('click', () => {
    if (!writer) {
      out.replaceChildren(h('p', { class: 'msg', attrs: { role: 'status' } }, NO_WRITER));
      return;
    }
    const body = readBody(current.rule_id, mode.value(), {
      start: start.value(),
      end: end.value(),
      hold: hold.value(),
      stop: stop.value(),
      target: target.value(),
    });
    if (typeof body === 'string') {
      out.replaceChildren(h('p', { class: 'msg err', attrs: { role: 'status' } }, body));
      return;
    }
    run.disabled = true;
    out.replaceChildren(h('p', { class: 'msg', attrs: { role: 'status' } }, '계산 중…'));
    void writer
      .post<BacktestResult>('backtest', body)
      .then((res) => {
        out.replaceChildren(resultView(res.data));
        const s = shared(ctx);
        s.result = res.data;
        if (s.curve) drawCurve(s.curve, res);
      })
      .catch((err: unknown) => {
        const msg = isNoData(err) ? (err.serverMessage ?? errorText(err)) : errorText(err);
        out.replaceChildren(
          h(
            'p',
            {
              class: isNoData(err) ? 'msg' : 'msg err',
              attrs: { role: 'status' },
            },
            msg,
          ),
        );
      })
      .finally(() => {
        run.disabled = false;
      });
  });
  showNightly(first);
  p.body.append(formRow(rule.el, mode.el), formRow(start.el, end.el, hold.el, stop.el, target.el, run));
  if (!writer) p.body.append(h('p', { class: 'dim' }, NO_WRITER));
  p.body.append(nightly, out);
}

export async function fillBacktestCurve(ctx: PageContext, p: Panel): Promise<void> {
  shared(ctx).curve = p;
  p.showMessage(CURVE_WAIT);
  return Promise.resolve();
}
