// 상단 띠 — 로그인: API 칩(값 형식·잠정·마감·준비 중·값 없음 사유)·1분 새로 고침·오류 격리.
// 공개: calendar·events 공개 파일로 세션·금통위 D-n 계산, 로그인 칩은 자물쇠, 로그인 API 를 부르지 않는다.
import { describe, expect, it } from 'vitest';
import type { AppContext } from '../../src/app/types';
import ribbon, {
  CHIP_SLOTS,
  chipOptions,
  daysUntil,
  ddayLabel,
  nextMpc,
  PUBLIC_TICK_MS,
  remainText,
  RIBBON_REFRESH_MS,
  sessionAt,
} from '../../src/app/ribbon';
import type { DataSource, Tier } from '../../src/data/source';
import { fixedClock, json } from '../helpers';
import fixtureRibbon from '../fixtures/api/market_ribbon.json';
import publicCalendar from './fixtures/public_calendar.json';
import { apiHarness, flush, publicHarness, text } from './support';

const SEP16_1010 = new Date('2026-09-16T01:10:00Z'); // KST 09-16(수) 10:10

function ctxOf(tier: Tier, source: DataSource, now: Date) {
  const every: { ms: number; fn: () => void }[] = [];
  const ctx: AppContext = {
    tier,
    source,
    now: fixedClock(now),
    every: (ms, fn) => {
      every.push({ ms, fn });
      return () => undefined;
    },
  };
  const el = document.createElement('div');
  document.body.append(el);
  return { ctx, el, every };
}

const chipByKey = (el: Element, key: string): HTMLElement => {
  const c = el.querySelector<HTMLElement>(`.chip[data-key="${key}"]`);
  if (!c) throw new Error(`칩 없음: ${key}`);
  return c;
};

describe('상단 띠 — 로그인', () => {
  it('API 칩 13개를 엔진 순서대로, 값은 화면에서만 억·조', async () => {
    const hz = apiHarness();
    const { ctx, el, every } = ctxOf('login', hz.source, SEP16_1010);
    await ribbon.mount(el, ctx);
    expect(hz.paths()).toEqual(['market/ribbon']);
    const keys = [...el.querySelectorAll<HTMLElement>('.chip')].map((c) => c.dataset.key);
    expect(keys).toEqual(CHIP_SLOTS.map((s) => s.key));

    expect(text(chipByKey(el, 'session').querySelector('.v'))).toBe('정규장 · 마감까지 5:20');
    expect(text(chipByKey(el, 'bok_mpc'))).toContain('금통위 D-36');
    expect(text(chipByKey(el, 'bok_mpc').querySelector('.v'))).toBe('10/22');

    const turn = chipByKey(el, 'market_turnover');
    expect(text(turn.querySelector('.v'))).toBe('7.3조 · 17.13x');
    expect(turn.dataset.state).toBe('estimated');
    expect(text(turn.querySelector('.t'))).toBe('잠정');
    expect(turn.title).toContain('장중(지수 기준)');
    expect(turn.title).toContain('NXT 미포함');

    const fo = chipByKey(el, 'foreign_spot');
    expect(text(fo.querySelector('.v'))).toBe('-11억');
    expect(fo.querySelector('.v')?.classList.contains('down')).toBe(true);
    expect(fo.dataset.state).toBe('closed');
    expect(text(fo.querySelector('.t'))).toBe('마감');

    expect(text(chipByKey(el, 'newhigh_count').querySelector('.v'))).toBe('22');
    // 값이 없으면 0 이 아니라 '—' + 사유
    const semi = chipByKey(el, 'semi_skew');
    expect(text(semi.querySelector('.v'))).toBe('—');
    expect(semi.title).toBe('005930 값 없음');
    // 준비 중
    expect(el.querySelectorAll('.chip.pending')).toHaveLength(5);
    expect(text(chipByKey(el, 'gex_flip').querySelector('.v'))).toBe('준비 중(P7)');
    expect(el.querySelectorAll('.chip.locked')).toHaveLength(0);

    expect(every.map((e) => e.ms)).toEqual([RIBBON_REFRESH_MS]);
    every[0]?.fn();
    await flush();
    expect(hz.paths()).toEqual(['market/ribbon', 'market/ribbon']);
  });

  it('오류는 띠 안의 오류 칩 하나(본문 없이), 직전 칩은 남긴다', async () => {
    let fail = false;
    const hz = apiHarness({ market_ribbon: () => (fail ? json({ detail: 'secret' }, 500) : json(apiFixture())) });
    const { ctx, el, every } = ctxOf('login', hz.source, SEP16_1010);
    await ribbon.mount(el, ctx);
    fail = true;
    every[0]?.fn();
    await flush();
    const warn = chipByKey(el, 'ribbon');
    expect(text(warn.querySelector('.v'))).toBe('불러오지 못함 (HTTP 500)');
    expect(el.querySelectorAll('.chip')).toHaveLength(14);
    expect(text(el)).not.toContain('secret');
    // 실패가 이어져도 오류 칩은 하나
    every[0]?.fn();
    await flush();
    every[0]?.fn();
    await flush();
    expect(el.querySelectorAll('.chip[data-key="ribbon"]')).toHaveLength(1);
    expect(el.querySelectorAll('.chip')).toHaveLength(14);
    // 되살아나면 오류 칩이 사라진다
    fail = false;
    every[0]?.fn();
    await flush();
    expect(el.querySelectorAll('.chip[data-key="ribbon"]')).toHaveLength(0);
    expect(el.querySelectorAll('.chip')).toHaveLength(13);
  });

  it('처음부터 실패하면 오류 칩만', async () => {
    const hz = apiHarness({ market_ribbon: () => json({}, 503) });
    const { ctx, el } = ctxOf('login', hz.source, SEP16_1010);
    await ribbon.mount(el, ctx);
    expect(el.querySelectorAll('.chip')).toHaveLength(1);
  });

  it('지연(stale) 값은 경고색 + 기준 시각', () => {
    const o = chipOptions(
      {
        key: 'foreign_spot',
        label: '외국인 현물',
        value: { value: 500_000_000, source: 'KIS', as_of: '2026-09-15T15:30:00+09:00', quality: 'stale' },
        tier: 'login',
        phase_pending: null,
        tags: [],
        note: null,
      },
      SEP16_1010,
    );
    expect(o.value).toBe('+5억 (기준 15:30)');
    expect(o.cls).toBe('warn');
  });
});

function apiFixture(): unknown {
  return structuredClone(fixtureRibbon);
}

describe('상단 띠 — 공개', () => {
  it('공개 파일 둘만 읽고(로그인 API 0), 로그인 칩은 자물쇠', async () => {
    const hz = publicHarness();
    const now = new Date('2026-10-07T01:23:45Z'); // KST 10-07(수) 10:23:45
    const { ctx, el, every } = ctxOf('public', hz.source, now);
    await ribbon.mount(el, ctx);
    expect(hz.paths().sort()).toEqual(['./data/calendar.json', './data/events.json']);
    expect(hz.paths().some((p) => p.includes('api'))).toBe(false);

    expect(text(chipByKey(el, 'session').querySelector('.v'))).toBe('정규장 · 마감까지 5:06');
    const mpc = chipByKey(el, 'bok_mpc');
    expect(text(mpc.querySelector('.k'))).toBe('금통위 D-15');
    expect(text(mpc.querySelector('.v'))).toBe('10/22');
    expect(mpc.dataset.state).toBe('estimated'); // 일정은 원 일정 대조 전 [확인 필요]

    const locked = [...el.querySelectorAll<HTMLElement>('.chip.locked')].map((c) => c.dataset.key);
    expect(locked).toEqual([
      'market_turnover',
      'foreign_spot',
      'inst_spot',
      'newhigh_count',
      'semi_skew',
      'cyc_def',
      'kr_vs_global',
      'gex_flip',
    ]);
    expect(el.querySelectorAll('.chip.pending')).toHaveLength(3);
    for (const c of el.querySelectorAll('.chip.locked')) expect(text(c.querySelector('.v'))).toBe('로그인 필요');

    // 카운트다운은 파일을 다시 받지 않고 다시 계산
    expect(every.map((e) => e.ms)).toEqual([PUBLIC_TICK_MS]);
    every[0]?.fn();
    await flush();
    expect(hz.calls).toHaveLength(2);
  });

  it('캘린더를 못 받으면 세션 칩만 오류, 금통위는 계속', async () => {
    const hz = publicHarness({ calendar: { not: 'envelope' } });
    const { ctx, el } = ctxOf('public', hz.source, new Date('2026-10-07T01:23:45Z'));
    await ribbon.mount(el, ctx);
    // 봉투가 아니면 manifest 에서 원천을 빌리는데 manifest 가 없다(404) → 오류
    expect(chipByKey(el, 'session').querySelector('.v')?.classList.contains('warn')).toBe(true);
    expect(text(chipByKey(el, 'bok_mpc').querySelector('.k'))).toBe('금통위 D-15');
  });
});

describe('세션 계산(엔진 session_chip 과 같은 규칙)', () => {
  const cal = publicCalendar.data;
  const at = (iso: string) => sessionAt(new Date(iso), cal);
  const kstHHMM = (d: Date | null) => (d ? new Date(d.getTime() + 9 * 3600_000).toISOString().slice(0, 16) : null);

  it.each([
    ['2026-10-07T08:00:00+09:00', '장 시작 전', '2026-10-07T09:00'],
    ['2026-10-07T10:00:00+09:00', '정규장', '2026-10-07T15:30'],
    ['2026-10-07T16:00:00+09:00', '장 마감', '2026-10-07T18:00'], // 오늘 야간장이 열린다
    ['2026-10-07T19:00:00+09:00', '야간장', '2026-10-08T06:00'],
    ['2026-10-08T05:00:00+09:00', '야간장', '2026-10-08T06:00'], // 어제 연 야간장
    ['2026-10-08T16:00:00+09:00', '장 마감', '2026-10-12T09:00'], // 월물 만기일 — 야간장 없음, 다음 거래일은 연휴 뒤
    ['2026-10-09T12:00:00+09:00', '휴장', '2026-10-12T09:00'],
  ])('%s → %s', (iso, label, until) => {
    const st = at(iso);
    expect(st?.label).toBe(label);
    expect(kstHHMM(st?.until ?? null)).toBe(until);
  });

  it('캘린더에 오늘이 없으면 모름(null) — 지어내지 않는다', () => {
    expect(at('2027-01-04T10:00:00+09:00')).toBeNull();
  });

  it('남은 시간 문구', () => {
    const now = new Date('2026-10-09T03:00:00Z');
    expect(remainText(now, new Date('2026-10-09T04:05:00Z'))).toBe('1:05');
    expect(remainText(now, new Date('2026-10-12T00:00:00Z'))).toBe('10/12 09:00');
  });

  it('금통위 D-n — 지난 일정은 건너뛴다', () => {
    const now = new Date('2026-10-23T01:00:00Z');
    expect(daysUntil(now, '2026-10-22')).toBeNull();
    expect(daysUntil(now, '2026-10-23')).toBe(0);
    expect(ddayLabel(0)).toBe('금통위 D-day');
    expect(
      nextMpc(now, {
        next_bok_mpc: '2026-10-22',
        events: [
          { date: '2026-10-22', kind: 'bok_mpc' },
          { date: '2026-11-26', kind: 'bok_mpc' },
        ],
      }),
    ).toBe('2026-11-26');
    expect(nextMpc(now, { events: [] })).toBeNull();
  });
});
