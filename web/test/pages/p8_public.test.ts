// 페이지 8 종목 상세 — 공개판(docs/p4_design.md §6.2·§6.5 p8_public): 로그인 API 0회, 시세·수급·컨센서스·밸류에이션·
// 시나리오 적정가는 자물쇠(요청 없음), DART 재무·공시·오버행·내부자는 fin/<코드>.json(파일 자체가 봉투), 재무 파일이 없는
// 종목은 '공시 재무 없음', 리포트는 데모 종목만(reports/<코드> + 엑셀 링크), 검색은 corp_index.json.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { route } from '../../src/app/route';
import { DataError, type DataSource, type Params } from '../../src/data/source';
import { NO_PUB_FIN } from '../../src/pages/p8/common';
import page, { corpRows, settled, TABS, type TabId } from '../../src/pages/p8_stock';
import { forbiddenHits, p4PageCtx, p4PublicHarness, publicFixture } from '../acceptance/support_p4';
import { click, flush, panelByTitle, text } from './support';

async function mountPub(code: string | null, over: Record<string, unknown> = {}) {
  if (code) route.setCode(code);
  const hz = p4PublicHarness(over);
  const tv = vi.fn(() => () => undefined);
  const { ctx, root } = p4PageCtx(8, 'public', hz.source, { tradingview: tv });
  await page.mount(ctx);
  return { hz, root, tv };
}

async function openAll(root: HTMLElement): Promise<void> {
  for (const t of TABS) await openTab(root, t.id);
}

async function openTab(root: HTMLElement, id: TabId): Promise<void> {
  click(root.querySelector(`button[role="tab"][data-tab="${id}"]`));
  await settled(root);
}

beforeEach(() => {
  route.reset();
});
afterEach(() => {
  route.reset();
});

describe('페이지 8 — 공개판', () => {
  it('로그인 API 0회 — 공개 파일만, 시세·수급·컨센서스·밸류에이션·시나리오는 자물쇠', async () => {
    const { hz, root, tv } = await mountPub('Q00001');
    await openAll(root);
    const urls = hz.paths();
    expect(urls.every((u) => u.startsWith('./data/'))).toBe(true);
    // P6(WRAP 고리): '재무' 탭 '수출 겹쳐 보기'는 공개 파일 trade/stock/<코드>.json(없으면 404 → 매핑 없음)
    expect(urls.some((u) => /api|(?<!trade\/)stock\/|watchlist|filings\//.test(u))).toBe(false);
    expect(new Set(urls)).toEqual(
      new Set([
        './data/corp_index.json',
        './data/fin/Q00001.json',
        './data/earnings_prelim.json',
        './data/reports/Q00001.json',
        './data/trade/stock/Q00001.json',
      ]),
    );
    expect(text(panelByTitle(root, '수출 겹쳐 보기'))).toContain('매핑 없음');
    const locked = [...root.querySelectorAll('section.panel.locked')].map((p) => text(p.querySelector('h3 > span')));
    // P5(WRAP 고리): 개요 탭 '담은 ETF'(운용사 PDF — 로그인)도 자물쇠
    expect(locked.sort()).toEqual(['밸류에이션 밴드', '수급 20일', '시나리오 적정가', '컨센서스', '담은 ETF'].sort());
    expect(text(panelByTitle(root, '컨센서스'))).toContain('컨센서스 — 제3자 유료·재배포 금지');
    // 머리: DART 이름·시장·결산월, 시세·신고가는 자물쇠, 시세 자리는 TradingView(KRX 심볼)
    const head = text(panelByTitle(root, '종목'));
    for (const s of ['합성전자', 'Q00001', '코스피', '12월', '시세·신고가로그인']) expect(head).toContain(s);
    expect(tv).toHaveBeenCalledTimes(2);
    for (const call of tv.mock.calls as unknown as [HTMLElement, string, Record<string, unknown>][]) {
      expect(JSON.stringify(call[2])).toContain('KRX:Q00001');
    }
    // 카드: ROE·OPM·오버행·내부자만 값, PER·PBR·목표가 업사이드는 자물쇠
    const card = text(panelByTitle(root, '핵심 지표'));
    expect(card).toContain('PER로그인');
    expect(card).toContain('목표가 업사이드로그인');
    expect(card).toContain('ROE8.5%');
    expect(root.querySelectorAll('.msg.err')).toHaveLength(0);
    expect(forbiddenHits(root)).toEqual([]);
  });

  it('재무·비율·부문·공시·오버행·내부자는 fin/<코드>.json(파일 봉투) — 로그인 응답과 같은 그리기', async () => {
    const { root } = await mountPub('Q00001');
    await openAll(root);
    const fin = panelByTitle(root, '분기·연간 재무');
    expect(fin.querySelectorAll('tbody tr').length).toBeGreaterThan(0);
    expect(text(fin.querySelector('.src'))).toContain('DART');
    expect(text(panelByTitle(root, '재무 비율'))).toContain('ROE');
    expect(text(panelByTitle(root, '부문 매출'))).toContain('추정');
    const disc = panelByTitle(root, '공시 목록');
    expect(text(disc.querySelector('thead'))).toContain('키워드 점수');
    expect(text(disc.querySelector('thead'))).not.toContain('중요도');
    const fx = publicFixture<{ data: { dart_viewer: string; filings: { rows: unknown[][] } } }>('fin/Q00001');
    const first = disc.querySelector('tbody a');
    expect(first?.getAttribute('href')?.startsWith(fx.data.dart_viewer)).toBe(true);
    // 공개판 내부자: 창 합계만(보고자·금액은 로그인)
    expect(text(panelByTitle(root, '내부자'))).toContain('보고자 목록·금액은 로그인 화면');
    expect(text(panelByTitle(root, '오버행'))).toContain('오버행 사건(CB·BW·EB·유상증자) 없음');
  });

  it('잠정실적은 earnings_prelim.json 의 이 종목 — 정정은 가장 늦은 접수번호만', async () => {
    const { root } = await mountPub('Q00001');
    await openTab(root, 'filings');
    const t = panelByTitle(root, '잠정실적·실적 일정').querySelector('table');
    expect(t?.querySelectorAll('tbody tr')).toHaveLength(1);
    expect(text(t?.querySelector('caption'))).toContain('정정 전 원 공시 1건 숨김');
    expect(text(t?.querySelector('tbody'))).toContain('정정');
    // 공개 파일에는 분류(YoY)가 없다 — 칸을 그리지 않는다
    expect(text(t?.querySelector('thead'))).not.toContain('분류');
  });

  it('재무 파일이 없는 종목(corp_index fin=false)은 요청 없이 "공시 재무 없음"', async () => {
    const { hz, root } = await mountPub('Q00004');
    await openAll(root);
    // 수출 겹쳐 보기(P6)는 재무 파일과 무관하게 공개 매핑 파일만 본다
    expect(hz.paths()).toEqual(['./data/corp_index.json', './data/trade/stock/Q00004.json']);
    for (const t of ['핵심 지표', '분기·연간 재무', '재무 비율', '부문 매출', '공시 목록', '오버행', '내부자']) {
      expect(text(panelByTitle(root, t))).toContain(NO_PUB_FIN);
    }
    expect(text(panelByTitle(root, '종목'))).toContain('합성리츠');
    expect(text(panelByTitle(root, 'dart-report'))).toContain('리포트는 로그인 화면 또는 공개 데모 종목만');
    expect(root.querySelectorAll('.msg.err')).toHaveLength(0);
  });

  it('corp_index 가 없으면(404) fin/<코드> 를 바로 찾고, 그것도 404 면 "공시 재무 없음"', async () => {
    const a = await mountPub('Q00002', { corp_index: null });
    expect(a.hz.paths()).toContain('./data/fin/Q00002.json');
    expect(text(panelByTitle(a.root, '종목'))).toContain('합성바이오');
    route.reset();
    document.body.replaceChildren();
    const b = await mountPub('Q00009', { corp_index: null });
    await openTab(b.root, 'fin');
    expect(b.hz.paths()).toContain('./data/fin/Q00009.json');
    expect(text(panelByTitle(b.root, '분기·연간 재무'))).toContain(NO_PUB_FIN);
    expect(b.root.querySelectorAll('.msg.err')).toHaveLength(0);
  });

  it('리포트: 데모 종목만 reports/<코드> + 엑셀 링크(./data/reports/<코드>.xlsx), null 블록은 "없음"', async () => {
    const { hz, root } = await mountPub('Q00001');
    await openTab(root, 'report');
    expect(hz.paths()).toContain('./data/reports/Q00001.json');
    const p = panelByTitle(root, 'dart-report');
    expect(p.querySelector('[data-download="xlsx"]')?.getAttribute('href')).toBe('./data/reports/Q00001.xlsx');
    expect(text(p)).toContain('주석 표 1개 못 찾음');
    expect(text(p.querySelector('[data-section="annual_costs"]'))).toContain('연간 비용구조 없음');
    expect(text(p.querySelector('[data-section="quarterly"]'))).toContain('분기 실적 없음');
    expect(text(p)).not.toContain('형식 오류');

    route.reset();
    document.body.replaceChildren();
    const b = await mountPub('Q00002');
    await openTab(b.root, 'report');
    expect(b.hz.paths()).not.toContain('./data/reports/Q00002.json');
    expect(text(panelByTitle(b.root, 'dart-report'))).toContain('리포트는 로그인 화면 또는 공개 데모 종목만');
  });

  it('코드가 없으면 검색 안내 — 검색은 corp_index(이름 일부)로 고르고 종목을 바꾼다', async () => {
    const { hz, root } = await mountPub(null);
    expect(text(root)).toContain('종목을 검색하세요');
    const input = root.querySelector<HTMLInputElement>('input[role="combobox"]');
    if (!input) throw new Error('검색 상자 없음');
    input.value = '바이오';
    input.dispatchEvent(new Event('input'));
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    await flush();
    await settled(root);
    expect(route.getCode()).toBe('Q00002');
    expect(text(panelByTitle(root, '종목'))).toContain('합성바이오');
    expect(hz.paths().filter((u) => u === './data/corp_index.json')).toHaveLength(1);
  });

  it('corpRows: X 의 columns + rows 모양을 코드별 줄로(잘못된 코드는 버린다)', () => {
    const m = corpRows({
      columns: ['code', 'name', 'market', 'acc_mt', 'fin', 'report'],
      rows: [
        ['Q00001', '합성전자', 'KOSPI', 12, true, true],
        ['bad', 'x', null, null, false, false],
        ['Q00003', '합성홀딩스', 'KONEX', null, true, false],
      ],
    });
    expect([...m.keys()]).toEqual(['Q00001', 'Q00003']);
    expect(m.get('Q00003')).toEqual({ code: 'Q00003', name: '합성홀딩스', market: 'KONEX', acc_mt: null, fin: true, report: false });
    expect(corpRows(null).size).toBe(0);
    expect(corpRows({ columns: 'x', rows: [] }).size).toBe(0);
  });
});

describe('페이지 8 — 공개판 실패와 아직 없음 구분', () => {
  it('공개 재무 파일 500 은 오류 줄(.msg.err), 파일 없음(404)은 안내(.msg)', async () => {
    route.setCode('Q00001');
    const hz = p4PublicHarness();
    const get = hz.source.get.bind(hz.source);
    const source: DataSource = {
      ...hz.source,
      get: <T>(key: string, params?: Params) =>
        key === 'fin/Q00001' ? Promise.reject(new DataError('HTTP 500', 500, null)) : get<T>(key, params),
    };
    const { ctx, root } = p4PageCtx(8, 'public', source, { tradingview: vi.fn(() => () => undefined) });
    await page.mount(ctx);
    await openTab(root, 'fin');
    for (const t of ['분기·연간 재무', '재무 비율', '부문 매출']) {
      expect(panelByTitle(root, t).querySelector('.msg.err'), t).not.toBeNull();
    }
    expect(panelByTitle(root, '핵심 지표').querySelector('.msg.err')).not.toBeNull();
  });
});
