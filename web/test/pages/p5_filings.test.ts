// 페이지 5 공시·일정(DART 패널 — docs/p4_design.md §6.4·§6.5 p5): 필터·정렬·관심종목 토글 → 쿼리(로그인)·화면 거르기(공개),
// 행·종목 이름 → 페이지 8(openStock → #/p/8/<코드>), 잠정실적은 대상마다 가장 늦은 접수번호, 장중 새로 고침(로그인만),
// 한 패널 실패가 다른 패널을 멈추지 않음, 공개판은 로그인 API 0회.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { route, STOCK_PAGE } from '../../src/app/route';
import { type FeedRow, levelOf, sortRows } from '../../src/pages/p5/feed';
import { sortPrelims } from '../../src/pages/p5/prelim';
import { addDays, marketOfCls, passMinRatio } from '../../src/pages/p5/common';
import { latestPrelims, type S } from '../../src/pages/p8/common';
import page, { FEED_REFRESH_MS } from '../../src/pages/p5_filings';
import { json } from '../helpers';
import { forbiddenHits, p4ApiHarness, p4Fixture, p4PageCtx, type P4Override, p4PublicHarness } from '../acceptance/support_p4';
import { buttonByText, choose, click, flush, panelByTitle, selectByLabel, text } from './support';

async function mountLogin(over: Record<string, P4Override> = {}) {
  const hz = p4ApiHarness(over);
  const { ctx, root, every } = p4PageCtx(5, 'login', hz.source);
  await page.mount(ctx);
  return { hz, root, every };
}

async function mountPublic(over: Record<string, unknown> = {}) {
  const hz = p4PublicHarness(over);
  const { ctx, root, every } = p4PageCtx(5, 'public', hz.source);
  await page.mount(ctx);
  return { hz, root, every };
}

/** 페이지 이동을 기록하는 가짜 navigator 를 선택 종목 상태에 물린다 */
function bindNav(): { go: ReturnType<typeof vi.fn> } {
  const go = vi.fn();
  route.bind({ go });
  return { go };
}

const PANELS = ['공시 피드', '잠정실적', '새 오버행', '내부자 큰 변동', '실적 일정'];

beforeEach(() => {
  route.reset();
});
afterEach(() => {
  route.reset();
});

describe('페이지 5 — 로그인', () => {
  it('기본 쿼리(오늘·7일·14일)로 다섯 패널 + 경제 캘린더(P5 MAC)를 부르고 그린다', async () => {
    const { hz, root } = await mountLogin();
    expect(hz.paths().sort()).toEqual(
      [
        'filings/earnings?from=2026-11-10&to=2026-11-16',
        'filings/feed?date=2026-11-16&limit=200&watch_only=false',
        'filings/insiders?days=7',
        'filings/overhang?days=7',
        'filings/schedule?days=14',
        'index-review', // P5 2차 IDX — 캘린더 지수 이벤트 줄(로그인)
        'macro/calendar',
      ].sort(),
    );
    for (const t of PANELS) expect(panelByTitle(root, t).querySelector('.msg.err')).toBeNull();
    expect(panelByTitle(root, '경제 캘린더').classList.contains('is-pending')).toBe(false);
    const feed = panelByTitle(root, '공시 피드');
    expect(feed.querySelectorAll('tbody tr')).toHaveLength(40);
    expect(text(feed.querySelector('.count'))).toBe('40건');
    expect(text(feed.querySelector(':scope > .def'))).toContain('일반 공시는 텔레그램으로 보내지 않는다');
    expect(forbiddenHits(root)).toEqual([]);
  });

  it('공시 피드: 날짜·종류·중요도·시장·관심종목만 → 쿼리', async () => {
    const { hz, root } = await mountLogin();
    const feed = panelByTitle(root, '공시 피드');
    click(buttonByText(feed, '어제'));
    await flush();
    expect(hz.paths().at(-1)).toBe('filings/feed?date=2026-11-15&limit=200&watch_only=false');
    choose(selectByLabel(feed, '종류'), 'prelim');
    choose(selectByLabel(feed, '중요도'), 'critical');
    choose(selectByLabel(feed, '시장'), 'KOSDAQ');
    const box = feed.querySelector<HTMLInputElement>('input[type="checkbox"]');
    if (!box) throw new Error('관심종목 토글 없음');
    box.checked = true;
    box.dispatchEvent(new Event('change'));
    await flush();
    expect(hz.paths().at(-1)).toBe('filings/feed?date=2026-11-15&importance=critical&kind=prelim&limit=200&market=KOSDAQ&watch_only=true');
  });

  it('공시 피드: 중요도순 정렬 — 매우 중요가 먼저, 같으면 최신', async () => {
    const { root } = await mountLogin();
    const feed = panelByTitle(root, '공시 피드');
    choose(selectByLabel(feed, '정렬'), 'importance');
    await flush();
    const first = feed.querySelector('tbody tr');
    expect(text(first)).toContain('매우 중요');
  });

  it('행·종목 이름을 누르면 종목 상세(페이지 8)로 — 종목 코드 없는 행은 이동하지 않는다', async () => {
    const { go } = bindNav();
    const { root } = await mountLogin();
    const feed = panelByTitle(root, '공시 피드');
    const rows = [...feed.querySelectorAll<HTMLTableRowElement>('tbody tr')];
    const noCode = rows.find((r) => !r.querySelector('[data-open]'));
    click(noCode);
    expect(go).not.toHaveBeenCalled();
    const withCode = rows.find((r) => text(r).includes('모의모빌리티'));
    click(withCode);
    expect(route.getCode()).toBe('994130');
    expect(go).toHaveBeenCalledWith(STOCK_PAGE);
    // 이름 단추(행 안)도 같은 일 — 행 클릭과 두 번 열리지 않는다
    go.mockClear();
    click(panelByTitle(root, '잠정실적').querySelector('[data-open="994150"]'));
    expect(route.getCode()).toBe('994150');
    expect(go).toHaveBeenCalledTimes(1);
  });

  it('잠정실적: 정정은 가장 늦은 접수번호만, 분류(YoY) 색·관심 꼬리표, 오늘 → from=to', async () => {
    const { hz, root } = await mountLogin();
    const p = panelByTitle(root, '잠정실적');
    const fx = p4Fixture<{ data: { rows: S['PrelimView'][] } }>('filings/earnings');
    const latest = latestPrelims(fx.data.rows);
    expect(latest.superseded).toBe(1);
    expect(p.querySelectorAll('tbody tr')).toHaveLength(latest.rows.length);
    expect(text(p.querySelector('caption'))).toContain('정정 전 원 공시 1건 숨김');
    // 분류 우선순위 정렬: 1 먼저(같으면 관심종목 먼저)
    expect(text(p.querySelector('tbody tr'))).toContain('모의에너지');
    expect(p.querySelector('[data-signal="YOY_BIG_UP"]')?.classList.contains('up')).toBe(true);
    expect(text(p)).toContain('관심');
    click(buttonByText(p, '오늘'));
    await flush();
    expect(hz.paths().at(-1)).toBe('filings/earnings?from=2026-11-16&to=2026-11-16');
  });

  it('새 오버행·내부자·일정: 기간·종류·비율 하한 → 쿼리', async () => {
    const { hz, root } = await mountLogin();
    const o = panelByTitle(root, '새 오버행');
    click(buttonByText(o, '30일'));
    choose(selectByLabel(o, '종류'), 'cb');
    await flush();
    expect(hz.paths()).toContain('filings/overhang?days=30&instrument=cb');
    const i = panelByTitle(root, '내부자 큰 변동');
    click(buttonByText(i, '90일'));
    choose(selectByLabel(i, '비율 하한'), '0.5');
    await flush();
    expect(hz.paths()).toContain('filings/insiders?days=90&min_ratio_pct=0.5');
    const s = panelByTitle(root, '실적 일정');
    click(buttonByText(s, '60일'));
    await flush();
    expect(hz.paths()).toContain('filings/schedule?days=60');
    expect(text(s)).toContain('이 기간 예정된 실적 공시 없음');
  });

  it('장중 새로 고침 60초 — 공시 피드·잠정실적만 다시 부른다', async () => {
    const { hz, every } = await mountLogin();
    expect(every.map((e) => e.ms)).toEqual([FEED_REFRESH_MS]);
    const n = hz.paths().length;
    every[0]?.fn();
    await flush();
    const added = hz.paths().slice(n).map((p) => p.split('?')[0]);
    expect(added.sort()).toEqual(['filings/earnings', 'filings/feed']);
  });

  it('한 패널 실패(500)·아직 없음(404)은 그 패널만 — 다른 패널은 그린다', async () => {
    const { root } = await mountLogin({
      'filings/overhang': () => json({ detail: 'x' }, 500),
      'filings/schedule': () => json({ code: 'no_data', message: '아직 없음 — filings.derive 16:00' }, 404),
    });
    expect(text(panelByTitle(root, '새 오버행').querySelector('.msg.err'))).toContain('HTTP 500');
    expect(text(panelByTitle(root, '실적 일정'))).toContain('아직 없음 — filings.derive 16:00');
    for (const t of ['공시 피드', '잠정실적', '내부자 큰 변동']) {
      expect(panelByTitle(root, t).querySelector('.msg.err')).toBeNull();
      expect(panelByTitle(root, t).querySelector('table')).not.toBeNull();
    }
  });
});

describe('페이지 5 — 공개판', () => {
  it('로그인 API 0회 — 공개 파일 다섯 + 매크로 캘린더, 새로 고침 없음, 관심종목 토글 없음', async () => {
    const { hz, root, every } = await mountPublic();
    expect(hz.paths().sort()).toEqual(
      [
        './data/earnings_prelim.json',
        './data/earnings_schedule.json',
        './data/filings_feed.json',
        './data/insider_recent.json',
        './data/macro/calendar.json',
        './data/overhang_recent.json',
      ].sort(),
    );
    expect(every).toHaveLength(0);
    const feed = panelByTitle(root, '공시 피드');
    expect(feed.querySelector('input[type="checkbox"]')).toBeNull();
    expect(text(feed.querySelector(':scope > .def'))).toContain('중요도는 제목 키워드 점수만');
    for (const t of PANELS) expect(panelByTitle(root, t).querySelector('.msg.err')).toBeNull();
    // 표 안에는 금지 낱말이 없다(공개 파일의 notes 문구는 X 의 글 — 표 밖)
    for (const t of root.querySelectorAll('table')) expect(forbiddenHits(t)).toEqual([]);
  });

  it('공시 피드: 파일의 날짜 목록으로 최근일·그 전일, 종류·시장 거르기는 화면에서', async () => {
    const { hz, root } = await mountPublic();
    const feed = panelByTitle(root, '공시 피드');
    const n0 = feed.querySelectorAll('tbody tr').length;
    expect(n0).toBeGreaterThan(0);
    expect(text(feed.querySelector('caption'))).toContain('11/13');
    click(buttonByText(feed, '그 전일'));
    await flush();
    expect(text(feed.querySelector('caption'))).toContain('11/12');
    click(buttonByText(feed, '최근일'));
    choose(selectByLabel(feed, '종류'), 'overhang');
    await flush();
    const rows = [...feed.querySelectorAll('tbody tr')];
    expect(rows.length).toBe(1);
    expect(text(rows[0])).toContain('전환사채권발행결정');
    choose(selectByLabel(feed, '시장'), 'KOSPI');
    await flush();
    expect(text(feed)).toContain('조건에 맞는 공시 없음');
    // 거르기는 다시 요청하지 않고 같은 파일(캐시 없는 정적 소스라 다시 읽을 수는 있다 — 로그인 API 는 0)
    expect(hz.paths().every((u) => u.startsWith('./data/'))).toBe(true);
  });

  it('잠정실적·내부자·일정: 기간·비율 하한을 화면에서 거른다, 행 → 페이지 8', async () => {
    const { go } = bindNav();
    const { root } = await mountPublic();
    const pre = panelByTitle(root, '잠정실적');
    expect(pre.querySelectorAll('tbody tr')).toHaveLength(1);
    expect(text(pre.querySelector('caption'))).toContain('정정 전 원 공시 1건 숨김');
    expect(text(pre.querySelector('thead'))).not.toContain('분류');
    const ins = panelByTitle(root, '내부자 큰 변동');
    expect(ins.querySelectorAll('tbody tr')).toHaveLength(2);
    choose(selectByLabel(ins, '비율 하한'), '0.5');
    await flush();
    expect(ins.querySelectorAll('tbody tr')).toHaveLength(1);
    expect(text(ins.querySelector('tbody'))).toContain('합성바이오');
    const sch = panelByTitle(root, '실적 일정');
    expect(sch.querySelectorAll('tbody tr')).toHaveLength(2);
    expect(text(sch)).toContain('추정');
    click(sch.querySelector('tbody tr'));
    expect(route.getCode()).toBe('Q00001');
    expect(go).toHaveBeenCalledWith(STOCK_PAGE);
    const oh = panelByTitle(root, '새 오버행');
    expect(oh.querySelectorAll('tbody tr')).toHaveLength(2);
    click(buttonByText(oh, '7일'));
    await flush();
    expect(oh.querySelectorAll('tbody tr')).toHaveLength(1);
  });

  it('공개 파일이 없으면(404) 그 패널만 "아직 없음"', async () => {
    const { root } = await mountPublic({ filings_feed: null });
    expect(text(panelByTitle(root, '공시 피드'))).toContain('아직 없음');
    expect(panelByTitle(root, '잠정실적').querySelector('table')).not.toBeNull();
  });
});

describe('페이지 5 — 순수 함수', () => {
  const row = (o: Partial<FeedRow>): FeedRow => ({
    rcept_no: '1',
    rcept_dt: '2026-11-16',
    first_seen_at: '2026-11-16T09:00:00+09:00',
    stock_code: null,
    corp_name: 'x',
    corp_cls: 'Y',
    title: 't',
    kind: 'other',
    is_correction: false,
    kw_score: 0,
    link: '',
    ...o,
  });

  it('sortRows: 최신순·중요도순(로그인 importance, 공개 kw_level)', () => {
    const a = row({ rcept_no: 'a', first_seen_at: '2026-11-16T09:00:00+09:00', importance: 'low' });
    const b = row({ rcept_no: 'b', first_seen_at: '2026-11-16T10:00:00+09:00', kw_level: 'high' });
    const c = row({ rcept_no: 'c', first_seen_at: '2026-11-16T08:00:00+09:00', importance: 'critical' });
    expect(sortRows([a, b, c], 'time').map((r) => r.rcept_no)).toEqual(['b', 'a', 'c']);
    expect(sortRows([a, b, c], 'importance').map((r) => r.rcept_no)).toEqual(['c', 'b', 'a']);
    expect(levelOf(row({}))).toBe('low');
  });

  it('sortPrelims: 우선순위(없으면 뒤) → 관심종목 → 최신 접수', () => {
    const p = (rcept_no: string, priority: number | null, is_watch: boolean) => ({ rcept_no, priority, is_watch }) as S['PrelimView'];
    const out = sortPrelims([p('1', 5, true), p('2', 1, false), p('3', 1, true), p('4', null, true)], 'priority');
    expect(out.map((r) => r.rcept_no)).toEqual(['3', '2', '1', '4']);
    expect(sortPrelims([p('1', 1, true), p('3', 5, false)], 'time').map((r) => r.rcept_no)).toEqual(['3', '1']);
  });

  it('latestPrelims: 회사·기간·연결/별도마다 가장 늦은 접수번호 — 연결과 별도는 따로', () => {
    const p = (rcept_no: string, corp_code: string, period: string, scope: S['PrelimView']['scope']) => ({ rcept_no, corp_code, period, scope });
    const out = latestPrelims([
      p('20261116000002', 'A', '2026Q3', 'consolidated'),
      p('20261116000001', 'A', '2026Q3', 'consolidated'),
      p('20261116000003', 'A', '2026Q3', 'standalone'),
      p('20261116000004', 'B', '2026Q3', 'consolidated'),
    ]);
    expect(out.rows.map((r) => r.rcept_no)).toEqual(['20261116000002', '20261116000003', '20261116000004']);
    expect(out.superseded).toBe(1);
  });

  it('addDays·marketOfCls·passMinRatio', () => {
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28');
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01');
    expect([marketOfCls('Y'), marketOfCls('K'), marketOfCls('N'), marketOfCls('E')]).toEqual(['KOSPI', 'KOSDAQ', 'KONEX', null]);
    expect(passMinRatio(-0.78, 0.5)).toBe(true);
    expect(passMinRatio(0.02, 0.5)).toBe(false);
    expect(passMinRatio(null, 0.5)).toBe(false);
    expect(passMinRatio(null, null)).toBe(true);
  });
});
