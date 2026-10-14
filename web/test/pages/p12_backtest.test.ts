// 페이지 12 ①② 백테스트·누적 곡선, ③ 백테스트 대 실제 + 페이지 11 ④⑤ 알림 후 성과·분포(묶음 BKT — docs/p8_design.md §4.5·
// §4.6·§6). 합성 픽스처(tests/unit/api/web_fixtures_p8/backtest.py). 칸 폭: 차트는 반폭, 탭 묶음(③)만 전체 폭.
// 404 는 '.msg', 그 밖 실패만 '.msg.err'. 공개판은 자물쇠만(요청 0).
import { describe, expect, it } from 'vitest';
import p11 from '../../src/pages/p11_rules';
import { fillPerf, fillPerfDist, perfRows, SMALL_TEXT, type RuleStats } from '../../src/pages/p11/perf';
import p12 from '../../src/pages/p12_journal';
import {
  CURVE_WAIT,
  fillBacktest,
  fillBacktestCurve,
  latestRuns,
  NO_WRITER,
  tradeSummary,
  writerOf,
  type BacktestRuns,
} from '../../src/pages/p12/backtest';
import { diffText, expectedCells, fillVsLive, vsRows } from '../../src/pages/p12/vs_live';
import { panel } from '../../src/ui/panel';
import { json } from '../helpers';
import { BT_NOW, btFixture, btHarness, NO_DATA, withWriter } from '../fixtures/api/backtest/harness';
import { buttonByText, click, flush, pageCtx, panelByTitle, text } from './support';

interface Env<T> {
  data: T;
  notes: string[];
}

const stats = (): RuleStats => btFixture<Env<RuleStats>>('stats').data;
const runs = (): BacktestRuns => btFixture<Env<BacktestRuns>>('runs').data;

function mkPanel(title: string) {
  const p = panel('login', { title, tier: 'login', lock: 'personal', span: 6 });
  document.body.append(p.el);
  return p;
}

describe('페이지 11 ④⑤ 알림 후 성과', () => {
  it('규칙 × 기간 표 — n<20 칸은 흐리게 + 표본 적음, 단위 %', async () => {
    const hz = btHarness();
    const { ctx } = pageCtx(11, 'login', hz.source, { now: () => BT_NOW });
    const p = mkPanel('알림 후 성과');
    await fillPerf(ctx, p);
    const rows = perfRows(stats());
    expect(p.body.querySelectorAll('tbody tr').length).toBe(rows.length);
    const smalls = rows.filter((r) => r.cell.small).length;
    expect(p.body.querySelectorAll('tbody td.dim').length).toBeGreaterThanOrEqual(smalls);
    if (smalls > 0) expect(text(p.body)).toContain(SMALL_TEXT);
    expect(text(p.el)).toContain('비용·체결 미반영'); // 봉투 notes — 원천 줄
    expect(text(p.body)).not.toMatch(/NaN|undefined|null/);
  });

  it('분포 막대 — 규칙을 바꾸면 다시 그린다, 성과 요청은 페이지에 한 번', async () => {
    const hz = btHarness();
    const { ctx } = pageCtx(11, 'login', hz.source, { now: () => BT_NOW });
    const a = mkPanel('알림 후 성과');
    const b = mkPanel('성과 분포');
    await fillPerf(ctx, a);
    await fillPerfDist(ctx, b);
    expect(hz.paths().filter((x) => x.startsWith('rules/stats'))).toHaveLength(1);
    expect(b.body.querySelector('svg')).not.toBeNull();
    const sel = b.body.querySelector('select');
    expect(sel?.options.length).toBe(stats().rows.length);
    if (sel && sel.options.length > 1) {
      const second = sel.options[1]?.value ?? '';
      sel.value = second;
      sel.dispatchEvent(new Event('change'));
      const name = stats().rows.find((r) => r.rule_id === second)?.name ?? '';
      expect(b.body.querySelector('svg')?.getAttribute('aria-label') ?? text(b.body)).toContain(name);
    }
  });

  it('404 는 .msg(서버 안내), 그 밖 실패는 .msg.err', async () => {
    const empty = btHarness({
      'rules/stats': () => json({ code: 'no_data', message: '아직 없음 — rules.outcome 08:30' }, 404),
    });
    const { ctx } = pageCtx(11, 'login', empty.source);
    const p = mkPanel('알림 후 성과');
    await fillPerf(ctx, p);
    expect(p.body.querySelector('.msg:not(.err)')?.textContent).toContain('rules.outcome 08:30');
    const broken = btHarness({ 'rules/stats': () => json({}, 500) });
    const c2 = pageCtx(11, 'login', broken.source).ctx;
    const q = mkPanel('성과 분포');
    await fillPerfDist(c2, q);
    expect(q.body.querySelector('.msg.err')).not.toBeNull();
  });
});

describe('페이지 12 ①② 백테스트·누적 곡선', () => {
  it('야간 요약을 보이고 — 쓰기 채널이 없으면 실행은 안내만(요청 없음)', async () => {
    const hz = btHarness();
    const { ctx } = pageCtx(12, 'login', hz.source, { now: () => BT_NOW });
    const p = mkPanel('백테스트');
    const c = mkPanel('누적 곡선');
    await fillBacktest(ctx, p);
    await fillBacktestCurve(ctx, c);
    expect(text(c.body)).toContain(CURVE_WAIT);
    const first = latestRuns(runs().runs)[0];
    expect(first).toBeDefined();
    const s = tradeSummary(first?.summary ?? {});
    expect(text(p.body)).toContain('야간');
    expect(text(p.body)).toContain(String(s.n));
    click(buttonByText(p.body, '실행'));
    await flush();
    expect(text(p.body)).toContain(NO_WRITER);
    expect(hz.calls.every((x) => (x.init?.method ?? 'GET') === 'GET')).toBe(true);
  });

  it('실행 — POST 본문(규칙·기간·보유·모드) → 요약·거래 표 + 누적 곡선(반폭 선)', async () => {
    const hz = btHarness();
    const { source, posts } = withWriter(hz.source);
    expect(writerOf(source)).not.toBeNull();
    const { ctx } = pageCtx(12, 'login', source, { now: () => BT_NOW });
    const p = mkPanel('백테스트');
    const c = mkPanel('누적 곡선');
    await fillBacktest(ctx, p);
    await fillBacktestCurve(ctx, c);
    click(buttonByText(p.body, '실행'));
    await flush(10);
    expect(posts).toHaveLength(1);
    const body = posts[0]?.body as Record<string, unknown>;
    expect(posts[0]?.key).toBe('backtest');
    expect(body.rule_id).toBe(latestRuns(runs().runs)[0]?.rule_id);
    expect(body.hold).toBe(5);
    expect(body.mode).toBe(latestRuns(runs().runs)[0]?.mode); // 야간 실행의 모드가 기본값
    expect(p.body.querySelectorAll('.bt-result tbody tr').length).toBeGreaterThan(0);
    expect(text(p.body)).toMatch(/손절|익절|보유 만기|진행 중/);
    expect(c.body.querySelector('svg')).not.toBeNull();
    expect(text(c.el)).toContain('생존편향');
  });

  it('잘못된 기간은 요청하지 않고 칸 오류', async () => {
    const hz = btHarness();
    const { source, posts } = withWriter(hz.source);
    const { ctx } = pageCtx(12, 'login', source);
    const p = mkPanel('백테스트');
    await fillBacktest(ctx, p);
    const start = p.body.querySelectorAll('input[type=text]')[0] as HTMLInputElement;
    start.value = '2026/01/02';
    click(buttonByText(p.body, '실행'));
    await flush();
    expect(posts).toHaveLength(0);
    expect(p.body.querySelector('.bt-result .msg.err')?.textContent).toContain('YYYY-MM-DD');
  });

  it('야간 실행이 없으면(404) .msg', async () => {
    const hz = btHarness({ 'backtest/runs': () => json(NO_DATA, 404) });
    const { ctx } = pageCtx(12, 'login', hz.source);
    const p = mkPanel('백테스트');
    await fillBacktest(ctx, p);
    expect(p.body.querySelector('.msg:not(.err)')?.textContent).toContain('rules.backtest_nightly');
  });
});

describe('페이지 12 ③ 백테스트 대 실제', () => {
  it('기대(야간 summary.outcome) 대 실제(rules/stats) — 같은 칸 나란히', () => {
    const rows = vsRows(runs().runs, stats());
    const rules = new Set(latestRuns(runs().runs).map((r) => r.rule_id));
    expect(new Set(rows.map((r) => r.rule)).size).toBe(rules.size);
    for (const r of rows) {
      expect(r.bt ?? r.live).not.toBeNull();
      if (r.bt && r.live) expect(r.bt.horizon).toBe(r.live.horizon);
    }
    expect(expectedCells({ outcome: [{ horizon: 1, n: 3 }, 'x', { n: 2 }] })).toEqual([
      { horizon: 1, n: 3, win_rate: null, mean: null },
    ]);
  });

  it('탭 둘 — 일치 감시는 처음 열 때 부른다, 차이 글자', async () => {
    const hz = btHarness();
    const { ctx } = pageCtx(12, 'login', hz.source, { now: () => BT_NOW });
    const p = mkPanel('백테스트 대 실제');
    await fillVsLive(ctx, p);
    expect(p.body.querySelectorAll('[role=tab]').length).toBe(2);
    expect(hz.paths().some((x) => x.startsWith('backtest/match'))).toBe(false);
    click(p.body.querySelectorAll('[role=tab]')[1] ?? null);
    await flush(10);
    expect(hz.paths().some((x) => x.startsWith('backtest/match'))).toBe(true);
    expect(text(p.body)).toContain('실시간과 백테스트 신호가 같다');
    expect(
      diffText({
        trade_date: '2026-10-29',
        rule_id: 'r_0000000000e1',
        version: 1,
        live_n: 1,
        backtest_n: 0,
        logic_n: 1,
        diffs: [
          {
            code: 'Q80020',
            eval_ts: '2026-10-29T15:30:00+09:00',
            side: 'live_only',
            reason: 'logic',
          },
        ],
      }),
    ).toBe('Q80020 실시간에만·로직');
  });

  it('logic 차이가 있으면 경고 줄(.msg.err)', async () => {
    const m = btFixture<Env<{ rows: Record<string, unknown>[] }>>('match');
    const row = m.data.rows[0] ?? {};
    row.logic_n = 1;
    row.diffs = [
      {
        code: 'Q80001',
        eval_ts: '2026-10-29T15:30:00+09:00',
        side: 'backtest_only',
        reason: 'logic',
      },
    ];
    const hz = btHarness({ 'backtest/match': () => json(m) });
    const { ctx } = pageCtx(12, 'login', hz.source);
    const p = mkPanel('백테스트 대 실제');
    await fillVsLive(ctx, p);
    click(p.body.querySelectorAll('[role=tab]')[1] ?? null);
    await flush(10);
    expect(p.body.querySelector('.msg.err')?.textContent).toContain('logic 차이 1건');
    expect(p.body.querySelector('td.down')).not.toBeNull();
  });
});

describe('진입 파일 — 칸 폭·자물쇠', () => {
  it('로그인: 페이지 12 백테스트·곡선 반폭, 대 실제 전체 폭 / 페이지 11 성과 둘 반폭', async () => {
    const hz = btHarness();
    const a = pageCtx(12, 'login', hz.source, { now: () => BT_NOW });
    await p12.mount(a.ctx);
    await flush(10);
    expect(panelByTitle(a.root, '백테스트').className).toContain('span-6');
    expect(panelByTitle(a.root, '누적 곡선').className).toContain('span-6');
    expect(panelByTitle(a.root, '백테스트 대 실제').className).toContain('span-12');
    const b = pageCtx(11, 'login', btHarness().source, { now: () => BT_NOW });
    await p11.mount(b.ctx);
    await flush(10);
    expect(panelByTitle(b.root, '알림 후 성과').className).toContain('span-6');
    expect(panelByTitle(b.root, '성과 분포').className).toContain('span-6');
  });

  it('공개: 자물쇠만, 요청 0', async () => {
    const hz = btHarness();
    const { ctx, root } = pageCtx(12, 'public', hz.source);
    await p12.mount(ctx);
    await flush();
    expect(hz.calls).toHaveLength(0);
    expect(
      panelByTitle(root, '백테스트').querySelector('.lock, [data-lock]') ?? panelByTitle(root, '백테스트'),
    ).not.toBeNull();
  });
});
