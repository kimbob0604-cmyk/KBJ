// 페이지 3 진단(묶음 DIAG — docs/p7_design.md §6.1 ①②③⑤·§6.2): 판정 띠(종합 판정·공포탐욕 gauge)·공공지표 진단(.lvl
// 행 + 매크로 위험 게이지)·시장 진단(지표 카드)·추이 차트(반폭)·이벤트 달력(줄 목록). 공개판은 공공지표·이벤트만 그리고
// 로그인 패널은 자물쇠(요청 0, 공개 파일에 로그인 판정 키 0). 404 는 '.msg', 그 밖 실패만 '.msg.err'.
import { describe, expect, it } from 'vitest';
import {
  eventText,
  indicatorItem,
  mountEvents,
  mountMarket,
  mountPublic,
  mountVerdict,
  TITLES,
  valueText,
  type DiagSummary,
} from '../../src/pages/p3/diag';
import p3 from '../../src/pages/p3_diag';
import { P3_SECTIONS } from '../../src/pages/p3_diag';
import { json } from '../helpers';
import { DIAG_NOW, diagFixture, diagLoginHarness, diagPublicHarness } from '../fixtures/api/diag/harness';
import { gexHarness } from '../fixtures/api/gex/harness';
import { buttonByText, flush, pageCtx, panelByTitle, text } from './support';

interface Env<T> {
  source: string;
  as_of: string;
  quality: string;
  notes: string[];
  data: T;
}

type Over = Record<string, (() => Response) | undefined>;

async function mountDiag(tier: 'login' | 'public', over: Over = {}) {
  const hz = tier === 'login' ? diagLoginHarness(over) : diagPublicHarness(over);
  const { ctx, root } = pageCtx(3, tier, hz.source, { now: () => DIAG_NOW });
  await mountVerdict(ctx);
  await mountPublic(ctx);
  await mountMarket(ctx);
  await mountEvents(ctx);
  await flush();
  return { hz, root };
}

function spans(root: HTMLElement): [string, string][] {
  return [...root.querySelectorAll('section.panel')].map((p) => [text(p.querySelector('h3 > span')), p.className]);
}

describe('페이지 3 진단 — 로그인', () => {
  it('패널 순서·반폭 배치 — 차트는 반폭, 이벤트 달력만 전체 폭(차트 없음)', async () => {
    const { root } = await mountDiag('login');
    const s = spans(root);
    expect(s.map((x) => x[0])).toEqual([
      TITLES.verdict, TITLES.fear, TITLES.public, TITLES.publicHist, TITLES.market, TITLES.marketHist, TITLES.events,
    ]);
    for (const [title, cls] of s) expect(cls).toContain(title === TITLES.events ? 'span-12' : 'span-6');
    expect(panelByTitle(root, TITLES.events).querySelector('svg')).toBeNull();
    expect(text(root)).not.toMatch(/NaN|undefined|null/);
  });

  it('요약은 한 번만 부른다(패널 넷이 나눈다)', async () => {
    const { hz } = await mountDiag('login');
    expect(hz.paths().filter((p) => p.startsWith('diag/summary'))).toHaveLength(1);
  });

  it('종합 판정 — 판정 이름·근거·이벤트 주의, 공포탐욕 — gauge·성분', async () => {
    const { root } = await mountDiag('login');
    const d = diagFixture<Env<DiagSummary>>('summary').data;
    const v = panelByTitle(root, TITLES.verdict);
    expect(text(v)).toContain('중립');
    for (const t of d.verdict?.top ?? []) expect(text(v)).toContain(t);
    expect(text(v)).toContain('이벤트 주의');
    const f = panelByTitle(root, TITLES.fear);
    expect(f.querySelector('.gauge svg')).not.toBeNull();
    expect(text(f)).toContain(`${(d.fear_greed?.score ?? 0).toFixed(1)} · 중립`);
    expect(text(f)).toContain('빠진 성분: 변동성(뒤집음)'); // 영문 키가 아니라 화면 이름(2026-10-20)
    expect(text(f)).not.toContain('export_momentum');
  });

  it('공공지표 진단 — .lvl 행 다섯·게이지, 값 없는 지표는 사유(0 아님)', async () => {
    const { root } = await mountDiag('login');
    const p = panelByTitle(root, TITLES.public);
    const rows = [...p.querySelectorAll('.lvl')].map((r) => text(r));
    expect(rows).toHaveLength(5);
    expect(rows.some((r) => r.startsWith('신용스프레드') && r.includes('%p'))).toBe(true);
    expect(rows.find((r) => r.startsWith('수출 모멘텀'))).toContain('준비 중');
    expect(text(p)).toContain('매크로 위험 게이지');
  });

  it('시장 진단 카드·추이 — 고르면 그 지표 이력을 부른다', async () => {
    const { hz, root } = await mountDiag('login');
    const m = panelByTitle(root, TITLES.market);
    expect(text(m)).toContain('시장폭(20일선 위)');
    expect(text(m)).toContain('업종 지수 코드 미정');
    const hp = panelByTitle(root, TITLES.marketHist);
    expect(hp.querySelector('svg')).not.toBeNull();
    buttonByText(hp, '국장 공포탐욕').click();
    await flush();
    expect(hz.paths().some((p) => p.startsWith('diag/history') && p.includes('key=fear_greed'))).toBe(true);
  });

  it('이벤트 달력 — 오늘 이후만, D-n·오늘', async () => {
    const { root } = await mountDiag('login');
    const rows = [...panelByTitle(root, TITLES.events).querySelectorAll('.lvl')].map((r) => text(r));
    expect(rows[0]).toContain('월물 만기 · 만기 · 오늘');
    expect(rows.some((r) => r.includes('실적 몰림 12종목') && r.includes('D-1'))).toBe(true);
  });

  it('아직 없음(404)은 .msg, 그 밖 실패(500)만 .msg.err', async () => {
    const nd = () => json(diagFixture('summary_empty'), 404);
    const { root } = await mountDiag('login', { 'diag/summary': nd });
    const v = panelByTitle(root, TITLES.verdict);
    expect(text(v.querySelector('.msg:not(.err)'))).toContain('아직 없음');
    expect(v.querySelector('.msg.err')).toBeNull();
    const bad = await mountDiag('login', { 'diag/events': () => json({ code: 'internal', message: '오류' }, 500) });
    expect(panelByTitle(bad.root, TITLES.events).querySelector('.msg.err')).not.toBeNull();
    expect(panelByTitle(bad.root, TITLES.public).querySelector('.msg.err')).toBeNull();
  });
});

describe('페이지 3 진단 — 공개', () => {
  it('공공지표·이벤트만 그리고 로그인 패널은 자물쇠 — API 요청 0, 공개 파일만', async () => {
    const { hz, root } = await mountDiag('public');
    for (const t of [TITLES.verdict, TITLES.fear, TITLES.market, TITLES.marketHist]) {
      expect(panelByTitle(root, t).classList.contains('locked')).toBe(true);
    }
    expect(hz.paths().every((p) => p.startsWith('./data/diag/'))).toBe(true);
    expect([...panelByTitle(root, TITLES.public).querySelectorAll('.lvl')]).toHaveLength(5);
    expect(panelByTitle(root, TITLES.publicHist).querySelector('svg')).not.toBeNull();
    expect(text(root)).not.toMatch(/공포탐욕 \d|위험 선호 점수|NaN|undefined/);
  });

  it('공개 파일에 로그인 판정 키가 없다', () => {
    for (const name of ['public/diag/summary', 'public/diag/history', 'public/diag/events']) {
      const s = JSON.stringify(diagFixture(name));
      expect(s).not.toMatch(/"(fear_greed|verdict|gex[a-z_]*)"\s*:/);
    }
  });
});

describe('페이지 3 진입 — 하위 모듈 순서', () => {
  it('진입 모듈은 P3_SECTIONS 순서대로 붙인다(GEX 는 ENG 하위 모듈)', async () => {
    const dz = diagLoginHarness();
    const gz = gexHarness();
    const source = {
      ...dz.source,
      get: <T,>(key: string, params?: Record<string, string>) =>
        key.startsWith('gex/') ? gz.source.get<T>(key, params) : dz.source.get<T>(key, params),
    };
    const { ctx, root } = pageCtx(3, 'login', source, { now: () => DIAG_NOW });
    await p3.mount(ctx);
    await flush();
    expect(P3_SECTIONS).toEqual(['verdict', 'public', 'market', 'gex', 'oi', 'events']);
    const titles = spans(root).map((x) => x[0]);
    expect(titles[0]).toBe(TITLES.verdict);
    expect(titles.at(-1)).toBe(TITLES.events);
  });
});

describe('진단 순수 도우미', () => {
  it('값 글자 — 원은 억, %·bp 는 부호, %p 는 그대로, 없으면 —', () => {
    expect(valueText(3_000_000_000_000, '원')).toBe('+30,000억');
    expect(valueText(-12.5, 'bp')).toBe('-12.50bp');
    expect(valueText(0.62, '%p')).toBe('0.62%p');
    expect(valueText(null, '%')).toBe('—');
  });

  it('지표 칸 — 구간·백분위·꼬리표, 값 없으면 사유', () => {
    const base = { key: 'credit_balance', tier: 'public' as const, unit: '%', quality: 'ok' as const, reasons: [], flags: ['overheat'] };
    const it1 = indicatorItem({ ...base, value: 4.2, state: 'extreme_high', pct_rank: 93, score: 1 });
    expect(it1.value).toBe('+4.20% · 극단 높음 · 93%ile · 과열');
    expect(it1.cls).toBe('up');
    const it2 = indicatorItem({ ...base, value: null, state: null, pct_rank: null, score: null, reasons: ['준비 중 — 없음'] });
    expect(it2.na).toBe('준비 중 — 없음');
  });

  it('이벤트 글자 — 오늘·D-n·지난 것', () => {
    const e = { date: '2026-12-10', kind: 'expiry' as const, title: '월물 만기', quality: 'ok' as const };
    expect(eventText({ ...e, days_left: 0 })).toBe('만기 · 오늘');
    expect(eventText({ ...e, days_left: 3 })).toBe('만기 · D-3');
    expect(eventText({ ...e, days_left: null })).toBe('만기');
  });
});
