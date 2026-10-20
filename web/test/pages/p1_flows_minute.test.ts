// 페이지 1 '장중 투자자 수급'(P5 묶음 F2) — 카드 7(사모 제외)·선 토글(기본 개인·외국인·기관계·지수)·HTS 처럼 이어진 선
// (빈 분은 점선 다리 — 점·조각 없음)·지수 겹침(따로 맞춘 축)·현재값 꼬리표 비켜 놓기·정시 눈금·머리 꼬리표
// (수집 HH:MM · 60초 갱신 · 잠정)·거래소 잠금·확인 전/장 시작 전/휴장/아직 없음 화면·탭과 토글의 URL 쿼리·60초 갱신·공개판 자물쇠.
// 픽스처 = test/fixtures/api/program/minute_*.json(합성 p5_world — tests/unit/api/web_fixtures_p5/program.py).
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import page from '../../src/pages/p1_market';
import { DEFAULT_TAB, MINUTE_REFRESH_MS, MINUTE_TITLE, drawMinute, minuteTag } from '../../src/pages/p1/flows_minute';
import { bridgeSegments, eokTick, minuteChart, niceStep, nudgeLabels } from '../../src/pages/p1/flows_minute_chart';
import type { Panel } from '../../src/ui/panel';
import { eok } from '../../src/ui/fmt';
import { apiHarness, buttonByText, click, flush, notFound, type Override, pageCtx, panelByTitle, publicHarness, text } from './support';

const FX = import.meta.glob<unknown>('../fixtures/api/program/*.json', { eager: true, import: 'default' });

// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- 시험이 픽스처 모양을 적어 받는다
function fx<T = Record<string, unknown>>(name: string): T {
  const v = FX[`../fixtures/api/program/${name}.json`];
  if (v === undefined) throw new Error(`픽스처 없음: program/${name}`);
  return structuredClone(v) as T;
}

interface MinuteFx {
  notes: string[];
  data: {
    status: string;
    venue: string;
    axis: string[];
    collected_at: string | null;
    refresh_s: number;
    provisional: boolean;
    trade_date: string;
    lines: { investor: string; label: string; default_on: boolean; values: (number | null)[] }[];
    cards: { investor: string; label: string; net_value: number | null }[];
    index: { code: string; label: string; values: (number | null)[]; last: number | null; chg_pct: number | null } | null;
  };
}

const pressed = (p: ParentNode): string[] =>
  [...p.querySelectorAll<HTMLButtonElement>('.legend button')]
    .filter((b) => b.getAttribute('aria-pressed') === 'true')
    .map((b) => b.dataset.id ?? '');

/** 꼬리표(rect) 가운데 y — data-tag 순서대로 */
function tagYs(root: ParentNode): Record<string, number> {
  const out: Record<string, number> = {};
  for (const g of root.querySelectorAll<SVGGElement>('g[data-tag]')) {
    const r = g.querySelector('rect');
    out[g.dataset.tag ?? ''] = Number(r?.getAttribute('y')) + Number(r?.getAttribute('height')) / 2;
  }
  return out;
}

const MINUTE_BY_TAB: Record<string, string> = {
  kospi: 'minute_kospi',
  kosdaq: 'minute_kosdaq_off',
  k200_fut: 'minute_k200_fut',
  k200_call: 'minute_k200_fut',
  k200_put: 'minute_k200_fut',
};

function param(url: string, key: string): string | null {
  return new URL(url, 'https://x').searchParams.get(key);
}

const minute = (override?: string): Override => (url: string) =>
  new Response(JSON.stringify(fx(override ?? MINUTE_BY_TAB[param(url, 'market') ?? ''] ?? 'minute_kosdaq_off')));
const program: Record<string, Override> = {
  program_daily: (url: string) => new Response(JSON.stringify(fx(`daily_${param(url, 'market') ?? 'K'}`))),
  program_top: () => new Response(JSON.stringify(fx('top_K_buy_intraday'))),
};

async function mountLogin(over: Record<string, Override> = {}) {
  const hz = apiHarness({ 'flows_market-minute': minute(), ...program, ...over });
  const { ctx, root, every } = pageCtx(1, 'login', hz.source);
  await page.mount(ctx);
  await flush();
  return { hz, root, every, p: panelByTitle(root, MINUTE_TITLE) };
}

beforeEach(() => {
  window.history.replaceState(null, '', '/');
});
afterEach(() => {
  window.history.replaceState(null, '', '/');
});

describe('장중 투자자 수급 — 로그인', () => {
  it('기본 탭(코스피200 선물)을 부르고 카드 7·선 12·머리 꼬리표를 그린다', async () => {
    const f = fx<MinuteFx>('minute_k200_fut');
    const { hz, p } = await mountLogin();
    expect(DEFAULT_TAB).toBe('k200_fut');
    expect(hz.paths()).toContain('flows/market-minute?market=k200_fut');
    const cards = [...p.querySelectorAll('.f2-card')];
    expect(cards.map((c) => c.querySelector('span')?.textContent)).toEqual([
      '개인',
      '외국인',
      '기관 합계',
      '금융투자',
      '투신',
      '연기금',
      '기타법인',
    ]);
    expect(cards.map((c) => (c as HTMLElement).dataset.investor)).not.toContain('pe_fund'); // 투신은 사모 제외
    const frgn = f.data.cards.find((c) => c.investor === 'frgn');
    expect(text(cards[1])).toContain(eok(frgn?.net_value, 0, true));
    const head = text(p.querySelector('.f2-head'));
    expect(head).toContain(minuteTag(f.data as never));
    expect(head).toContain('수집 13:00 · 60초 갱신 · 잠정');
    expect(head).toContain('KRX(잠금)');
    const legend = [...p.querySelectorAll('.legend button')];
    expect(legend).toHaveLength(13); // 12구분 + 지수
    expect(pressed(p)).toEqual(['prsn', 'frgn', 'orgn', 'index']); // 기관 세부·기타법인은 기본 끔
    expect(text(legend[2])).toBe('기관계'); // HTS 표기
    expect(p.querySelectorAll('svg [data-series]')).toHaveLength(4);
    // 지수 범례에 현재값·등락률
    const ix = f.data.index;
    expect(ix?.label).toBe('코스피200');
    const ixBtn = p.querySelector('.legend button[data-id="index"]');
    expect(text(ixBtn)).toContain('코스피200');
    expect(text(ixBtn)).toContain((ix?.last ?? 0).toFixed(2));
    expect(text(ixBtn)).toMatch(/[+-]\d+\.\d{2}%/);
    // 시간축 = 분 축 전체, 정시 눈금 09~15 + 날짜 + 갱신
    expect([...p.querySelectorAll('.f2-hour')].map((t) => t.textContent)).toEqual(['09', '10', '11', '12', '13', '14', '15']);
    expect(text(p.querySelector('.f2-date'))).toBe('11/17');
    expect(text(p.querySelector('.f2-upd'))).toBe('갱신 13:00');
    // 오른쪽 억원 눈금(쉼표)·0 기준선
    expect(p.querySelector('line.zero')).not.toBeNull();
    expect([...p.querySelectorAll('.f2-ytick')].length).toBeGreaterThanOrEqual(3);
    expect(text(p.querySelector('h3 .src'))).toContain('KIS');
    expect(p.querySelector('.msg.err')).toBeNull();
  });

  it('빈 분은 끊지 않고 점선 다리로 잇는다 — 점·조각 없음(코스피 10:00~10:04)', async () => {
    window.history.replaceState(null, '', '/?f2m=kospi');
    const f = fx<MinuteFx>('minute_kospi');
    const { hz, p } = await mountLogin();
    expect(hz.paths()).toContain('flows/market-minute?market=kospi');
    const frgn = f.data.lines.find((l) => l.investor === 'frgn');
    const i = f.data.axis.indexOf('10:00');
    expect(frgn?.values.slice(i, i + 5)).toEqual([null, null, null, null, null]); // 서버는 지어내지 않는다
    const g = p.querySelector('svg [data-series="frgn"]');
    expect(g?.querySelectorAll('path.run')).toHaveLength(2); // 09:00~09:59 · 10:05~13:00
    expect(g?.querySelectorAll('path.gap')).toHaveLength(1); // 09:59 → 10:05 다리 하나
    expect(g?.querySelector('path.gap')?.getAttribute('stroke-dasharray')).toBeTruthy();
    expect(p.querySelectorAll('svg circle')).toHaveLength(0);
    // 지수(10분 값)는 10분 간격을 실선으로 — 다리 없음
    const ig = p.querySelector('svg [data-series="index"]');
    expect(ig?.querySelectorAll('path.run')).toHaveLength(1);
    expect(ig?.querySelectorAll('path.gap')).toHaveLength(0);
    const notes = text(p.querySelector('.notes'));
    expect(notes).toContain('못 받은 5분');
    expect(notes).not.toMatch(/#\d|flows\./); // 작업 이름·실측 번호 없음
  });

  it('현재값 꼬리표가 겹치면 비켜 놓는다(개인 +564억·기관 +351억 같은 이웃 값)', () => {
    const f = fx<MinuteFx>('minute_k200_fut');
    const n = f.data.axis.length;
    const ramp = (end: number): (number | null)[] =>
      f.data.axis.map((_, k) => (k <= 240 ? Math.round((end * 1e8 * k) / 240) : null));
    const ends: Record<string, number> = { prsn: 564, frgn: -9000, orgn: 351 };
    f.data.lines = f.data.lines.map((l) => ({ ...l, values: l.investor in ends ? ramp(ends[l.investor] ?? 0) : l.values }));
    const body = document.createElement('div');
    const p = { body, setSource: () => undefined } as unknown as Panel;
    drawMinute(p, { data: f.data, notes: [], source: 'KIS', as_of: null, quality: null } as never);
    expect(n).toBe(401);
    const ys = tagYs(body);
    expect(Object.keys(ys)).toEqual(expect.arrayContaining(['prsn', 'frgn', 'orgn', 'index']));
    expect(Math.abs((ys.prsn ?? 0) - (ys.orgn ?? 0))).toBeGreaterThanOrEqual(13); // 꼬리표 높이 이상
    expect(text(body.querySelector('g[data-tag="prsn"]'))).toBe('개인 +564억');
    expect(text(body.querySelector('g[data-tag="orgn"]'))).toBe('기관계 +351억');
  });

  it('선 토글은 URL 쿼리에 남고, 다시 열면 그 선이 켜진다', async () => {
    const { p } = await mountLogin();
    click(buttonByText(p, '금융투자'));
    expect(new URLSearchParams(window.location.search).get('f2l')).toBe('prsn,frgn,orgn,scrt,index');
    expect(p.querySelectorAll('svg [data-series]')).toHaveLength(5);
    click(p.querySelector('.legend button[data-id="index"]'));
    expect(new URLSearchParams(window.location.search).get('f2l')).toBe('prsn,frgn,orgn,scrt');
    expect(p.querySelector('svg [data-series="index"]')).toBeNull();
    const again = await mountLogin();
    expect(pressed(again.p)).toEqual(['prsn', 'frgn', 'orgn', 'scrt']);
  });

  it('시장 탭 → 쿼리·URL', async () => {
    const { hz, p } = await mountLogin();
    click(buttonByText(p, '콜옵션'));
    await flush();
    expect(hz.paths()).toContain('flows/market-minute?market=k200_call');
    expect(new URLSearchParams(window.location.search).get('f2m')).toBe('k200_call');
  });

  it('확인 전(꺼진 조합)·장 시작 전·휴장일·아직 없음 화면이 각각 다르다', async () => {
    const off = await mountLogin({ 'flows_market-minute': minute('minute_kosdaq_off') });
    expect(off.p.querySelector('.f2-state')?.getAttribute('data-status')).toBe('not_confirmed');
    expect(text(off.p)).toContain('확인 전');
    expect(off.p.querySelector('.linechart')).toBeNull(); // 값을 지어내지 않는다
    expect(text(off.p)).not.toContain('#39');

    const pre = await mountLogin({ 'flows_market-minute': minute('minute_before_open') });
    expect(pre.p.querySelector('.f2-state')?.getAttribute('data-status')).toBe('before_open');
    expect(text(pre.p)).toContain('장 시작 전');
    expect(text(pre.p.querySelector('.f2-head'))).not.toContain('잠정');
    expect(pre.p.querySelector('svg')?.getAttribute('data-empty')).toBe('true');

    const hol = await mountLogin({ 'flows_market-minute': minute('minute_holiday') });
    expect(hol.p.querySelector('.f2-state')?.getAttribute('data-status')).toBe('holiday');
    expect(text(hol.p)).toContain('휴장일');

    const none = await mountLogin({ 'flows_market-minute': () => notFound('아직 없음 — flows.market_minute 09:00~15:40 매분') });
    expect(text(none.p)).toContain('아직 없음');
  });

  it('현물 탭에 1분 자료가 없으면 그 시장의 마감 확정 순매수를 대신 보인다(빈 화면 대신 — 2026-10-20)', async () => {
    window.history.replaceState(null, '', '/?f2m=kospi');
    const { hz, p } = await mountLogin({ 'flows_market-minute': () => notFound('아직 없음 — flows.market_minute 09:00~15:40 매분') });
    expect(hz.paths()).toContain('flows/investors');
    expect(p.querySelector('[data-status="daily_fallback"]')).not.toBeNull();
    expect(text(p)).toContain('장중 1분 자료 없음');
    const cards = [...p.querySelectorAll('.f2-card')].map((c) => text(c.querySelector('span')));
    expect(cards).toEqual(['개인', '외국인', '기관 합계', '기타법인']);
    expect(text(p)).not.toContain('아직 없음 —');
  });

  it('60초 갱신을 따로 건다(웹소켓 아님)', async () => {
    const { hz, every } = await mountLogin();
    expect(every.map((e) => e.ms)).toContain(MINUTE_REFRESH_MS);
    const before = hz.paths().filter((x) => x.startsWith('flows/market-minute')).length;
    every.find((e) => e.ms === MINUTE_REFRESH_MS)?.fn();
    await flush();
    expect(hz.paths().filter((x) => x.startsWith('flows/market-minute')).length).toBe(before + 1);
  });
});

describe('수급 차트 순수 계산', () => {
  it('bridgeSegments — 실선 조각과 점선 다리, 앞뒤 빈칸은 그리지 않는다', () => {
    const r = bridgeSegments([null, 1, 2, null, null, 5, null, 7, 8, null]);
    expect(r.solid).toEqual([
      [
        [1, 1],
        [2, 2],
      ],
      [
        [7, 7],
        [8, 8],
      ],
    ]);
    expect(r.gaps).toEqual([
      [
        [2, 2],
        [5, 5],
      ],
      [
        [5, 5],
        [7, 7],
      ],
    ]); // 외톨이 5 는 점이 아니라 양쪽 다리로 이어진다
    expect(bridgeSegments([1, null, 3], 10).gaps).toEqual([]); // 지수(10분 값)는 10칸까지 실선
    expect(bridgeSegments([null, 4, null]).solid).toEqual([]);
  });

  it('nudgeLabels — 최소 간격·범위 안·입력 순서 유지', () => {
    expect(nudgeLabels([100, 102, 50], 13, 0, 200)).toEqual([100, 113, 50]);
    const bottom = nudgeLabels([195, 196, 197], 13, 0, 200);
    expect(bottom).toEqual([174, 187, 200]);
    expect(nudgeLabels([5], 13, 6.5, 200)).toEqual([6.5]);
  });

  it('niceStep·eokTick — 보기 좋은 간격, 천 단위 쉼표', () => {
    expect(niceStep(10_000)).toBe(2500);
    expect(niceStep(900)).toBe(250);
    expect(eokTick(-2500)).toBe('-2,500');
    expect(eokTick(0)).toBe('0');
  });

  it('지수만 있어도 그린다, 아무 값도 없으면 데이터 없음', () => {
    const empty = minuteChart({ label: 't', axis: ['09:00', '09:01'], series: [], date: '11/17', updated: null });
    expect(empty.el.querySelector('svg')?.getAttribute('data-empty')).toBe('true');
    const only = minuteChart({
      label: 't',
      axis: ['09:00', '09:01'],
      series: [],
      index: { label: '코스피', values: [1, 2], last: 2, chgPct: 0.1 },
      date: '11/17',
      updated: null,
    });
    expect(only.el.querySelector('svg [data-series="index"] path.run')).not.toBeNull();
  });
});

describe('장중 투자자 수급 — 공개', () => {
  it('요청하지 않고 자물쇠', async () => {
    const hz = publicHarness();
    const { ctx, root } = pageCtx(1, 'public', hz.source);
    await page.mount(ctx);
    expect(hz.paths().some((x) => x.includes('market-minute'))).toBe(false);
    expect(panelByTitle(root, MINUTE_TITLE).classList.contains('locked')).toBe(true);
  });
});
