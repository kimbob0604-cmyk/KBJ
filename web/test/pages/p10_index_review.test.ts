// P5 2차 묶음 IDX — 지수 정기변경 화면: 페이지 10 패널(지수 탭·D-day 띠·편입/편출/경계 표·컷오프 나란히·빈 목록 상태·
// '추정' 표시·필요 상승률 정렬), 페이지 5 캘린더 지수 줄, 페이지 8 머리 배지 한 줄, 공개판 자물쇠(요청 없음).
// 픽스처 = test/fixtures/api/index_review/*.json(합성 index_world 응답 — tests/unit/api/web_fixtures_p5/index_review.py).
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { route } from '../../src/app/route';
import page10 from '../../src/pages/p10_etf';
import page5 from '../../src/pages/p5_filings';
import page8 from '../../src/pages/p8_stock';
import {
  type Candidate,
  dday,
  INDEX_TITLE,
  type IndexReview,
  ratioPct,
  sortByRise,
} from '../../src/pages/p10/index_review';
import { indexCalRows, mergeRows } from '../../src/pages/p5/index_events';
import type { Envelope } from '../../src/data/source';
import { json } from '../helpers';
import { p4ApiHarness, p4PageCtx } from '../acceptance/support_p4';
import { apiHarness, click, flush, idxFixture, type Override, pageCtx, panelByTitle, publicHarness, selectByLabel, text } from './support';

type Env = Envelope<IndexReview>;

const review = (name: string): Env => idxFixture<Env>(name);

async function mount10(over: Record<string, Override> = {}) {
  const hz = apiHarness(over);
  const { ctx, root } = pageCtx(10, 'login', hz.source);
  await page10.mount(ctx);
  await flush();
  return { hz, root, p: panelByTitle(root, INDEX_TITLE) };
}

function tbodyCodes(table: Element | null | undefined): string[] {
  return [...(table?.querySelectorAll('tbody tr') ?? [])].map((tr) => text(tr.querySelector('small')));
}

/** 컷오프 종류·표 이름 → 그 표 */
function tableOf(p: HTMLElement, kind: string, key: string): HTMLTableElement | null {
  const head = p.querySelector(`[data-run="${kind}"] [data-section="${kind}-${key}"]`);
  return (head?.nextElementSibling?.querySelector('table') as HTMLTableElement | null) ?? null;
}

beforeEach(() => {
  route.reset();
});
afterEach(() => {
  route.reset();
});

describe('페이지 10 지수 정기변경 — 로그인', () => {
  it('기본 탭(MSCI) · D-day 띠 · 컷오프 근거 · 편입/편출/경계 표(사용자 예시 열)', async () => {
    const { hz, p } = await mount10();
    expect(hz.paths()).toContain('index-review');
    expect(p.querySelector('.msg.err')).toBeNull();
    const d = review('review_msci').data;
    const run = d.runs[0];
    if (!run || !d.review) throw new Error('픽스처 모양');

    // 지수 탭 3개, MSCI 눌림
    const tabs = [...p.querySelectorAll('[role="group"][aria-label="지수"] button')];
    expect(tabs.map((b) => text(b))).toEqual(['MSCI Korea', 'KOSPI200', 'KOSDAQ150']);
    expect(tabs[0]?.getAttribute('aria-pressed')).toBe('true');
    // 리뷰 고르기(지난 리뷰 꼬리표)
    const sel = selectByLabel(p, '리뷰');
    expect([...sel.options].map((o) => o.textContent)).toEqual(['2026-05 (지남)', '2026-11']);
    expect(sel.value).toBe('2026-11');

    // D-day 띠: 기준일·발표·리밸런싱·효력일, 확인 전 일정이라 '추정'
    const band = p.querySelector('[data-section="band"]');
    expect(text(band)).toContain('기준일');
    expect(text(band)).toContain('D-day'); // 기준일 = 오늘(합성 CLOCK)
    expect(text(band)).toContain(`11/11 · ${dday(d.review.d_announce)}`);
    expect(text(band)).toContain('리밸런싱');
    expect(text(band)).toContain('D-32');
    expect(band?.querySelectorAll('.q-badge').length).toBe(4);
    expect(text(band?.querySelector('.q-badge'))).toBe('추정');
    expect(text(p.querySelector('[data-section="band-head"]'))).toContain('일정 추정');

    // 컷오프 나란히(엔진 추정 한 줄) — 값·구간·근거, '추정'
    const runs = p.querySelector('table caption')?.closest('table');
    expect(text(runs?.querySelector('caption'))).toContain('엔진 추정과 사용자 입력');
    const runRow = runs?.querySelector('tbody tr');
    expect(text(runRow)).toContain('엔진 추정');
    expect(text(runRow)).toContain('coverage 85%');
    expect(text(runRow)).toContain('추정');

    // 표 셋 — 행 수 = 응답, 열 = 사용자 예시
    for (const key of ['adds', 'deletes', 'borderline'] as const) {
      const t = tableOf(p, 'model', key);
      expect(tbodyCodes(t)).toEqual(sortByRise(run[key]).map((r) => r.code));
    }
    const adds = tableOf(p, 'model', 'adds');
    expect([...(adds?.querySelectorAll('thead th') ?? [])].map((th) => text(th))).toEqual([
      '종목',
      '시가총액',
      '유동시총',
      '유동비율',
      '시총 컷오프 괴리율',
      '유동시총 컷오프 괴리율',
      '조건 충족',
      '필요 주가 상승률',
      '예상 패시브 매수(억)',
      '거래대금 대비',
    ]);
    const first = run.adds[0];
    if (!first) throw new Error('편입 후보 없음');
    const tr = adds?.querySelector('tbody tr');
    expect(text(tr)).toContain(first.name ?? first.code);
    expect(text(tr)).toContain(ratioPct(first.required_rise));
    expect(text(tr)).toContain('유동'); // 필요 상승률 기준(큰 쪽)
    expect(text(tr)).toContain('O');
    // 머리 줄 '추정'
    expect(text(p.querySelector('[data-section="model-adds"] .q-badge'))).toBe('추정');
    expect(text(p)).not.toMatch(/NaN|undefined|Infinity/);
  });

  it('필요 상승률 오름차순 — 서버 순서가 섞여 와도 화면이 맞춘다(없는 값은 뒤)', async () => {
    const env = review('review_msci');
    const run = env.data.runs[0];
    if (!run) throw new Error('픽스처 모양');
    run.borderline = [...run.borderline].reverse();
    const last = run.borderline[0];
    if (last) last.required_rise = null;
    const { p } = await mount10({ 'index-review': env });
    const codes = tbodyCodes(tableOf(p, 'model', 'borderline'));
    const want = sortByRise(run.borderline);
    expect(codes).toEqual(want.map((r) => r.code));
    expect(codes.at(-1)).toBe(last?.code);
    const rises = want.map((r) => r.required_rise).filter((v): v is number => typeof v === 'number');
    expect(rises).toEqual([...rises].sort((a, b) => a - b));
  });

  it('지수 탭을 누르면 index= 로 다시 부르고 KRX 형은 유동 괴리율 —', async () => {
    const { hz, p } = await mount10();
    click([...p.querySelectorAll('[aria-label="지수"] button')].find((b) => text(b) === 'KOSPI200'));
    await flush();
    expect(hz.paths()).toContain('index-review?index=kospi200');
    const d = review('review_kospi200').data;
    expect(text(p.querySelector('[data-section="band-head"]'))).toContain('KOSPI200 2026-12');
    expect(text(p)).toContain('rank 54/66');
    const t = tableOf(p, 'model', 'adds');
    expect(tbodyCodes(t)).toEqual(sortByRise(d.runs[0]?.adds ?? []).map((r) => r.code));
    expect(text(t?.querySelector('tbody tr td:nth-child(6)'))).toBe('—');
    // 리뷰 고르기 → review=
    const sel = selectByLabel(p, '리뷰');
    expect(sel.value).toBe('2026-12');
  });

  it('지난 리뷰를 고르면 review= — 예측 없음 한 줄', async () => {
    const { hz, p } = await mount10();
    const sel = selectByLabel(p, '리뷰');
    sel.value = '2026-05';
    sel.dispatchEvent(new Event('change'));
    await flush();
    expect(hz.paths()).toContain('index-review?index=msci_korea&review=2026-05');
    expect(text(p.querySelector('[data-section="no-runs"]'))).toContain('예측 없음');
    expect(p.querySelector('[data-run]')).toBeNull();
  });

  it('구성종목 목록이 없으면 표 대신 빈 목록 상태(지어내지 않는다)', async () => {
    const { p } = await mount10({ 'index-review': review('review_empty') });
    const run = p.querySelector('[data-run="model"]');
    expect(run?.getAttribute('data-status')).toBe('no_constituents');
    expect(text(run)).toContain('구성종목 목록 없음');
    expect(run?.querySelector('table')).toBeNull();
    expect(p.querySelector('.msg.err')).toBeNull();
    expect(text(p.querySelector('.notes'))).toContain('구성종목 목록 없음');
  });

  it('사용자 입력 컷오프가 있으면 엔진 추정과 나란히 — 표도 컷오프마다', async () => {
    const env = review('review_user');
    const { p } = await mount10({ 'index-review': env });
    const rows = [...(p.querySelector('table')?.querySelectorAll('tbody tr') ?? [])];
    expect(rows.map((r) => text(r.querySelector('td')))).toEqual(['엔진 추정', '사용자 입력']);
    expect(text(rows[1])).toContain('2.0조');
    expect(text(rows[1])).not.toContain('추정'); // 사용자 값에는 '추정' 배지 없음
    const user = env.data.runs[1];
    if (!user) throw new Error('픽스처 모양');
    expect(tbodyCodes(tableOf(p, 'user', 'deletes'))).toEqual(sortByRise(user.deletes).map((r) => r.code));
    expect(text(p.querySelector('[data-section="user-adds"]'))).toContain('사용자 입력 컷오프');
  });

  it('404(일정 표 없음)는 서버 안내 한 줄, 다른 패널은 계속', async () => {
    const { p, root } = await mount10({
      'index-review': () => json({ code: 'no_data', message: '아직 없음 — index.calendar 17:10' }, 404),
    });
    expect(text(p.querySelector('.msg'))).toContain('index.calendar');
    expect(p.querySelector('.msg.err')).toBeNull();
    expect(panelByTitle(root, 'ETF 자금 흐름').querySelector('table')).not.toBeNull();
  });

  it('도우미: D-day·비율', () => {
    expect([dday(0), dday(12), dday(-3)]).toEqual(['D-day', 'D-12', 'D+3']);
    expect(ratioPct(0.124)).toBe('+12.4%');
    expect(ratioPct(-0.8756)).toBe('-87.6%');
    expect(ratioPct(null)).toBe('—');
    const rows = [{ required_rise: 0.2 }, { required_rise: null }, { required_rise: -0.1 }] as Candidate[];
    expect(sortByRise(rows).map((r) => r.required_rise)).toEqual([-0.1, 0.2, null]);
  });
});

describe('공개판 — 지수 정기변경은 자물쇠만', () => {
  it('요청 없음 · 자물쇠 문구', async () => {
    const hz = publicHarness();
    const { ctx, root } = pageCtx(10, 'public', hz.source);
    await page10.mount(ctx);
    expect(hz.calls).toHaveLength(0);
    const p = panelByTitle(root, INDEX_TITLE);
    expect(p.classList.contains('locked')).toBe(true);
    expect(text(p)).toContain('구성종목·컷오프 추정');
  });
});

describe('페이지 5 캘린더 — 지수 이벤트 줄(로그인)', () => {
  it('리뷰마다 발표·리밸런싱 두 줄을 날짜순으로 섞고, 확인 전 일정은 (추정)', async () => {
    const hz = p4ApiHarness({ 'index-review': idxFixture('review_msci') });
    const { ctx, root } = p4PageCtx(5, 'login', hz.source);
    await page5.mount(ctx);
    await flush();
    expect(hz.paths()).toContain('index-review');
    const cal = panelByTitle(root, '경제 캘린더');
    click([...cal.querySelectorAll('[aria-label="캘린더 기간"] button')].find((b) => text(b) === '전체'));
    expect(text(cal)).toContain('MSCI Korea 2026-11 정기변경 결과 발표 (추정)');
    expect(text(cal)).toContain('KOSPI200 2026-12 정기변경 리밸런싱(패시브 매매) (추정)');
    expect(text(cal)).toContain('지수 일정');
    expect(cal.querySelector('.msg.err')).toBeNull();
  });

  it('행 만들기·합치기(순수)', () => {
    const cal = review('review_msci').data.calendar;
    const rows = indexCalRows(cal);
    expect(rows).toHaveLength(cal.length * 2);
    expect(rows.map((r) => r.date)).toEqual([...rows.map((r) => r.date)].sort());
    expect(rows.every((r) => r.quality === 'estimated' && r.actual === null && r.source === 'index')).toBe(true);
    const base = [{ ...rows[0], event_id: 'x', date: '2026-11-11', time_kst: '09:00', source: 'fred' }] as typeof rows;
    const merged = mergeRows(base, rows);
    expect(merged.map((r) => r.date)).toEqual([...merged.map((r) => r.date)].sort());
    expect(merged.findIndex((r) => r.event_id === 'x')).toBeLessThan(merged.findIndex((r) => r.event_id.endsWith('2026-11:announce')));
  });

  it('지수 일정 실패는 캘린더를 멈추지 않고 notes 한 줄', async () => {
    const hz = p4ApiHarness({ 'index-review': () => json({ code: 'internal' }, 500) });
    const { ctx, root } = p4PageCtx(5, 'login', hz.source);
    await page5.mount(ctx);
    await flush();
    const cal = panelByTitle(root, '경제 캘린더');
    expect(cal.querySelector('table')).not.toBeNull();
    expect(text(cal.querySelector('.notes'))).toContain('지수 일정');
  });
});

describe('페이지 8 머리 — 지수 배지 한 줄(로그인)', () => {
  it('편입 후보 배지(라벨 그대로·추정) — 배지가 없거나 404 면 줄이 없다', async () => {
    const code = '994010';
    const body = idxFixture<{ data: { code: string } }>('stock_993023');
    body.data.code = code;
    route.setCode(code);
    const hz = p4ApiHarness({ [`index-review/stock/${code}`]: body });
    const { ctx, root } = p4PageCtx(8, 'login', hz.source);
    await page8.mount(ctx);
    await flush();
    expect(hz.paths()).toContain(`index-review/stock/${code}`);
    const line = root.querySelector('[data-section="index-badges"]');
    expect(text(line)).toContain('MSCI Korea 편입 후보 · 여유 -87.6%');
    expect(text(line)).toContain('KOSPI200 편입 후보');
    expect(line?.querySelectorAll('.tag')).toHaveLength(2);
    expect(text(line?.querySelector('.q-badge'))).toBe('추정');
  });

  it('404(예측 없음)면 배지 줄 없음·오류 없음', async () => {
    route.setCode('994010');
    const hz = p4ApiHarness();
    const { ctx, root } = p4PageCtx(8, 'login', hz.source);
    await page8.mount(ctx);
    await flush();
    expect(root.querySelector('[data-section="index-badges"]')).toBeNull();
    expect(root.querySelector('.msg.err')).toBeNull();
  });
});
