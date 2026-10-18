// 페이지 8 종목 상세 — 로그인(docs/p4_design.md §6.1~§6.3·§6.5): 진입(관심종목 첫 종목·칩·검색), 머리(신고가 3축·정지·
// 우선주), 탭은 처음 열 때만 요청·같은 요청은 한 번, na_reason 문구, 404 no_data 문구, 한 패널 500 → 그 패널만 오류 줄,
// 모르는 코드, 필터 → 쿼리, 리포트 블록·엑셀 링크, 외국 통화.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { route } from '../../src/app/route';
import { NO_PUB_FIN } from '../../src/pages/p8/common';
import page, { settled, TABS, type TabId } from '../../src/pages/p8_stock';
import { json } from '../helpers';
import { p4ApiHarness, p4PageCtx, type P4Override, stockFixture } from '../acceptance/support_p4';
import { buttonByText, choose, click, flush, panelByTitle, selectByLabel, text } from './support';

async function mount8(code: string | null, over: Record<string, P4Override> = {}) {
  if (code) route.setCode(code);
  const hz = p4ApiHarness(over);
  const { ctx, root } = p4PageCtx(8, 'login', hz.source);
  const cleanup = await page.mount(ctx);
  return { hz, root, cleanup };
}

async function openTab(root: HTMLElement, id: TabId): Promise<void> {
  click(root.querySelector(`button[role="tab"][data-tab="${id}"]`));
  await settled(root);
}

/** 그 패널 본문(머리·필터 빼고) */
function body(root: HTMLElement, title: string): string {
  return text(panelByTitle(root, title));
}

const count = (paths: string[], p: string): number => paths.filter((x) => x === p).length;

beforeEach(() => {
  route.reset();
});
afterEach(() => {
  route.reset();
});

describe('페이지 8 — 진입과 머리(로그인)', () => {
  it('코드가 없으면 관심종목 첫 종목을 열고 머리·관심종목 칩·개요 탭만 그린다', async () => {
    const { hz, root } = await mount8(null);
    expect(route.getCode()).toBe('994010');
    const paths = hz.paths();
    expect(paths).toContain('watchlist');
    expect(paths).toContain('stock/994010/header');
    // 개요 탭의 요청만 — 다른 탭(리포트·부문·시나리오)은 처음 열 때
    for (const p of ['stock/994010/report', 'stock/994010/segments', 'stock/994010/scenario']) expect(paths).not.toContain(p);
    expect(root.querySelectorAll('[data-section="watchlist"] button')).toHaveLength(19);
    expect(root.querySelector('[role="tab"][aria-selected="true"]')?.getAttribute('data-tab')).toBe('overview');
    expect(root.querySelectorAll('[role="tab"]')).toHaveLength(TABS.length);

    const head = body(root, '종목');
    expect(head).toContain('모의전자');
    expect(head).toContain('994010');
    expect(head).toContain('관심');
    // 신고가 3축(ADR 0017) — 60일·252일 표기는 쓰지 않는다
    for (const s of ['신고가 120일', '120 KRX 거래일', '신고가 52주', '달력 364일', '신고가 역사적', '상장 이후']) expect(head).toContain(s);
    expect(head).not.toMatch(/60일|252일/);
    expect(text(panelByTitle(root, '핵심 지표'))).toContain('15.2x');
    expect(root.querySelectorAll('.msg.err')).toHaveLength(0);
  });

  it('탭은 처음 열 때만 요청하고 같은 요청(키 + 쿼리)은 탭끼리 나눠 쓴다', async () => {
    const { hz, root } = await mount8('994010');
    await openTab(root, 'fin');
    await openTab(root, 'value');
    await openTab(root, 'filings');
    await openTab(root, 'report');
    await openTab(root, 'overview');
    await openTab(root, 'fin');
    const paths = hz.paths();
    // 개요 카드와 재무 탭이 같은 분기 재무, 개요 카드와 밸류 탭이 같은 PER 밸류, 머리와 시나리오가 같은 머리
    expect(count(paths, 'stock/994010/financials?basis=q&n=12')).toBe(1);
    expect(count(paths, 'stock/994010/valuation?metric=per')).toBe(1);
    expect(count(paths, 'stock/994010/header')).toBe(1);
    expect(count(paths, 'stock/994010/consensus')).toBe(1);
    expect(count(paths, 'stock/994010/disclosures?days=30')).toBe(1);
    expect(count(paths, 'stock/994010/earnings')).toBe(1);
    expect(count(paths, 'stock/994010/segments')).toBe(1);
    expect(count(paths, 'stock/994010/report')).toBe(1);
    // 내부자 탭은 90일 보고 목록(개요 카드도 90일 창)
    expect(count(paths, 'stock/994010/insiders?days=90')).toBe(1);
    expect(root.querySelectorAll('.msg.err')).toHaveLength(0);
  });

  it('관심종목 칩·검색으로 종목을 바꾸면 머리와 탭을 새로 그리고 고른 탭은 그대로', async () => {
    const { hz, root } = await mount8('994010');
    await openTab(root, 'fin');
    click(buttonByText(root.querySelector('[data-section="watchlist"]') ?? root, '모의바이오'));
    await settled(root);
    expect(route.getCode()).toBe('994020');
    expect(body(root, '종목')).toContain('모의바이오');
    expect(root.querySelector('[role="tab"][aria-selected="true"]')?.getAttribute('data-tab')).toBe('fin');
    expect(hz.paths()).toContain('stock/994020/financials?basis=q&n=12');
    expect(root.querySelector('[data-section="watchlist"] [aria-pressed="true"]')?.textContent).toBe('모의바이오');

    // 검색 — Enter 는 지금 글자로 찾은 첫 후보(994010)
    const input = root.querySelector<HTMLInputElement>('input[role="combobox"]');
    if (!input) throw new Error('검색 상자 없음');
    input.value = '모의';
    input.dispatchEvent(new Event('input'));
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    await flush();
    await settled(root);
    expect(hz.paths()).toContain('stock/search?limit=20&q=%EB%AA%A8%EC%9D%98');
    expect(route.getCode()).toBe('994010');
    expect(body(root, '종목')).toContain('모의전자');
  });

  it('모르는 코드는 머리 한 줄만 — 탭을 그리지 않는다(섹션마다 아직 없음을 늘어놓지 않음)', async () => {
    const { root } = await mount8('999999');
    expect(body(root, '종목')).toContain('모르는 종목 코드 999999');
    expect(root.querySelector('[role="tablist"]')).toBeNull();
    expect(root.querySelectorAll('.msg.err')).toHaveLength(0);
  });

  it('우선주: 보통주 보기 단추, 컨센서스·시나리오는 보통주(회사) 기준', async () => {
    const { root } = await mount8('994015');
    const head = body(root, '종목');
    expect(head).toContain('우선주');
    expect(head).toContain('재무·공시·컨센서스는 회사(보통주) 것');
    await openTab(root, 'value');
    expect(body(root, '컨센서스')).toContain('우선주 — 보통주(994010) 컨센서스');
    expect(body(root, '시나리오 적정가')).toContain('보통주 기준');
    click(buttonByText(panelByTitle(root, '종목'), '보통주 994010 보기'));
    await settled(root);
    expect(route.getCode()).toBe('994010');
  });

  it('거래정지: 상태 배지 + 시세 칸 지연("정지 — 마지막 거래"), 못 채운 신고가 축은 —', async () => {
    const { root } = await mount8('994030');
    const p = panelByTitle(root, '종목');
    expect(text(p)).toContain('거래정지');
    expect(text(p.querySelector('[data-stale="true"]'))).toBe('정지 — 마지막 거래 2026-11-06');
    // 정지 종목은 그날 board 산출이 없다(newhigh null) — 세 축 모두 '—'(지어내지 않음)
    const hd = stockFixture<{ data: { newhigh: unknown } }>('994030', 'header');
    expect(hd.data.newhigh).toBeNull();
    expect(text(p)).toContain('신고가 120일120 KRX 거래일—');
    expect(text(p)).toContain('신고가 52주달력 364일—');
    expect(text(p)).toContain('신고가 역사적상장 이후—');
  });
});

describe('페이지 8 — 적용 불가·아직 없음·오류(로그인)', () => {
  it.each([
    ['994070', 'value', '밸류에이션 밴드', '적자 — PER 산출 안 함'],
    ['994100', 'value', '밸류에이션 밴드', '금융업 — EV/EBITDA 해당 없음'],
    ['994150', 'value', '밸류에이션 밴드', '상세 수집 대상 아님 — 관심종목에 넣으면 다음 수집(16:00·21:00)'],
    ['994040', 'value', '밸류에이션 밴드', '스팩 — 실적 지표 해당 없음'],
    ['994020', 'value', '시나리오 적정가', '적정가 산출 안 함 — 상장 1년 미만 — 5년 밴드 없음'],
    ['994120', 'value', '시나리오 적정가', '자본잠식 — PBR 산출 안 함'],
    ['994090', 'value', '컨센서스', '코넥스 — 컨센서스 없음'],
    ['994080', 'fin', '분기·연간 재무', '정기보고서 없음 — 재무 미제출'],
    ['994180', 'value', '밸류에이션 밴드', '재무 통화가 원화가 아님 — 환산하지 않는다'],
  ] as const)('%s %s 탭 %s: %s', async (code, tab, title, want) => {
    const { root } = await mount8(code);
    await openTab(root, tab);
    expect(body(root, title)).toContain(want);
    expect(root.querySelectorAll('.msg.err')).toHaveLength(0);
  });

  it('404 no_data 는 서버 안내 그대로 — 다른 패널은 계속(컨센서스 꺼짐·리포트 없음)', async () => {
    const { root } = await mount8('994080');
    await openTab(root, 'value');
    const msg = stockFixture<{ message: string }>('994080', 'consensus').message;
    expect(msg).toContain('consensus.snapshot');
    expect(body(root, '컨센서스')).toContain(msg);
    // KIS 교체로 잃은 것은 늘 적는다(D-P4-10)
    expect(body(root, '컨센서스')).toContain('출처 없음 — KIS 교체로 잃음: 분기 컨센서스·분기 어닝 서프라이즈·컨센서스 최고·최저');
    expect(text(panelByTitle(root, '핵심 지표'))).toContain(msg);
    await openTab(root, 'report');
    expect(body(root, 'dart-report')).toContain('아직 없음 — reports.dart_excel 21:00');
    expect(root.querySelector('[data-download="xlsx"]')).toBeNull();
    expect(root.querySelectorAll('.msg.err')).toHaveLength(0);
  });

  it('한 패널 500 → 그 패널만 오류 줄(본문 없이 상태 코드), 나머지는 그린다', async () => {
    const boom = (): Response => json({ detail: '비밀 본문' }, 500);
    const { root } = await mount8('994010', { 'stock/994010/chart': boom, 'stock/994010/scenario': boom });
    const chart = panelByTitle(root, '가격 차트');
    expect(text(chart.querySelector('.msg.err'))).toContain('HTTP 500');
    expect(text(chart)).not.toContain('비밀 본문');
    for (const t of ['핵심 지표', '수급 20일', '최근 공시·잠정실적']) expect(panelByTitle(root, t).querySelector('.msg.err')).toBeNull();
    await openTab(root, 'value');
    expect(panelByTitle(root, '시나리오 적정가').querySelector('.msg.err')).not.toBeNull();
    expect(panelByTitle(root, '컨센서스').querySelector('.msg.err')).toBeNull();
    expect(panelByTitle(root, '밸류에이션 밴드').querySelector('.msg.err')).toBeNull();
    expect(root.querySelectorAll('.msg.err')).toHaveLength(2);
  });

  it('핵심 지표 카드는 원천마다 따로 — 밸류에이션이 실패해도 ROE·오버행·업사이드는 그린다', async () => {
    const { root } = await mount8('994010', { 'stock/994010/valuation': () => json({}, 503) });
    const card = text(panelByTitle(root, '핵심 지표'));
    expect(card).toContain('ROE');
    expect(card).toContain('9.2%');
    expect(card).toContain('+18.9%');
    expect(card).toContain('불러오지 못함');
  });

  it('핵심 지표 카드: 아직 없음(404 no_data)은 실패가 아니다 — 그 칸에 서버 안내, 오류 문구 없음(§6.3)', async () => {
    const msg = '아직 없음 — fin.valuation_band 08:50';
    const { root } = await mount8('994010', { 'stock/994010/valuation': () => json({ code: 'no_data', message: msg }, 404) });
    const card = text(panelByTitle(root, '핵심 지표'));
    expect(card).toContain(msg);
    expect(card).not.toContain('불러오지 못함');
    expect(card).toContain('ROE');
    expect(root.querySelectorAll('.msg.err')).toHaveLength(0);
  });
});

describe('페이지 8 — 탭 내용·필터(로그인)', () => {
  it('재무: 분기 12 표·OPM 차트·비율·부문(추정), 연간 4 → basis=a&n=4', async () => {
    const { hz, root } = await mount8('994010');
    await openTab(root, 'fin');
    const fin = panelByTitle(root, '분기·연간 재무');
    expect(fin.querySelectorAll('tbody tr').length).toBeGreaterThan(0);
    expect(fin.querySelector('svg')).not.toBeNull();
    expect(text(fin.querySelector('caption'))).toContain('분기(누적 차분) · 연결');
    expect(body(root, '재무 비율')).toContain('ROE');
    expect(body(root, '부문 매출')).toContain('추정');
    click(buttonByText(fin, '연간 4'));
    await flush();
    expect(hz.paths()).toContain('stock/994010/financials?basis=a&n=4');
  });

  it('외국 통화(USD) 회사는 환산하지 않고 단위에 통화를 붙인다', async () => {
    const { root } = await mount8('994180');
    await openTab(root, 'fin');
    const fin = panelByTitle(root, '분기·연간 재무');
    expect(text(fin.querySelector('caption'))).toContain('USD(환산 안 함)');
    expect(text(fin.querySelector('thead'))).toContain('매출(백만 USD)');
    // 0.0 으로 뭉개지지 않는다(억 단위면 작은 회사 값이 0 처럼 보인다)
    const first = fin.querySelector('tbody tr td:nth-child(2)');
    expect(text(first)).not.toBe('0.0');
  });

  it('리포트: 블록 다섯·경고 줄·엑셀 링크(같은 출처 API — 페이지 코드에 경로 문자열 없음)', async () => {
    const { root } = await mount8('994010');
    await openTab(root, 'report');
    const p = panelByTitle(root, 'dart-report');
    expect([...p.querySelectorAll('section[data-section]')].map((s) => s.getAttribute('data-section'))).toEqual([
      'quarterly',
      'annual_costs',
      'bridge',
      'orders',
      'quarterly_costs',
    ]);
    expect(p.querySelector('[data-download="xlsx"]')?.getAttribute('href')).toBe('/api/stock/994010/report.xlsx');
    expect(text(p.querySelector('[data-check="warning"]'))).toContain('이익브릿지 잔차');
    expect(p.querySelector('.msg.err')).toBeNull();
    expect(p.querySelectorAll('svg').length).toBeGreaterThanOrEqual(3);
  });

  it('리포트: 모양이 틀린 블록은 그 블록만 형식 오류, null 블록은 없음', async () => {
    const env = stockFixture<{ data: { blocks: Record<string, unknown> } }>('994010', 'report');
    env.data.blocks.bridge = { steps: 'x' };
    env.data.blocks.orders = null;
    const { root } = await mount8('994010', { 'stock/994010/report': env });
    await openTab(root, 'report');
    const p = panelByTitle(root, 'dart-report');
    expect(text(p.querySelector('[data-section="bridge"]'))).toContain('이익 브릿지 — 형식 오류(이 블록만 건너뜀)');
    expect(text(p.querySelector('[data-section="orders"]'))).toContain('수주 현황 없음');
    expect(p.querySelector('[data-section="quarterly"] svg')).not.toBeNull();
    expect(p.querySelector('[data-section="quarterly_costs"] svg')).not.toBeNull();
  });

  it('컨센·밸류: 컨센서스 표·목표가·리비전, 밸류 지표 선택 → metric 쿼리, 시나리오 적정가(TAM 표기 없음)', async () => {
    const { hz, root } = await mount8('994010');
    await openTab(root, 'value');
    const c = panelByTitle(root, '컨센서스');
    expect(text(c)).toContain('KIS 추정');
    expect(text(c)).toContain('목표가 평균');
    expect(text(c)).toContain('리비전(7·30일)');
    const v = panelByTitle(root, '밸류에이션 밴드');
    expect(v.querySelector('[data-section="band"] svg')).not.toBeNull();
    choose(selectByLabel(v, '밴드 지표'), 'pbr');
    await flush();
    expect(hz.paths()).toContain('stock/994010/valuation?metric=pbr');
    const sc = text(panelByTitle(root, '시나리오 적정가'));
    for (const s of ['Bear', 'Base', 'Bull', 'EPS × PER']) expect(sc).toContain(s);
    expect(text(root)).not.toContain('TAM');
  });

  it('공시·지분: 공시 기간·종류 → 쿼리, 잠정실적은 대상마다 가장 늦은 접수번호, 내부자 기간 → days', async () => {
    const { hz, root } = await mount8('994140');
    await openTab(root, 'filings');
    const d = panelByTitle(root, '공시 목록');
    click(buttonByText(d, '90일'));
    await flush();
    expect(hz.paths()).toContain('stock/994140/disclosures?days=90');
    choose(selectByLabel(d, '종류'), 'prelim');
    await flush();
    expect(hz.paths()).toContain('stock/994140/disclosures?days=90&kind=prelim');

    const earn = stockFixture<{ data: { prelims: { rcept_no: string; period: string; scope: string }[] } }>('994140', 'earnings');
    const keys = new Set(earn.data.prelims.map((r) => `${r.period}|${r.scope}`));
    const e = panelByTitle(root, '잠정실적·실적 일정');
    const prelimTable = e.querySelector('table');
    expect(prelimTable?.querySelectorAll('tbody tr').length).toBe(Math.min(keys.size, 10));
    expect(earn.data.prelims.length).toBeGreaterThan(keys.size); // 원 공시 + 정정
    expect(text(prelimTable?.querySelector('caption'))).toContain('정정 전 원 공시 1건 숨김');
    expect(text(prelimTable?.querySelector('tbody'))).toContain('정정');

    const ins = panelByTitle(root, '내부자');
    click(buttonByText(ins, '180일'));
    await flush();
    expect(hz.paths()).toContain('stock/994140/insiders?days=180');
  });

  it('오버행: 요약(공개 칸 + 로그인 칸)·회차 표·타임라인·추정 보호예수', async () => {
    const { root } = await mount8('994130');
    await openTab(root, 'filings');
    const o = panelByTitle(root, '오버행');
    for (const s of ['잠재 신주', '오버행 비율', '행사 가능 비율', '상장주식수 기준', '금액', 'ITM 주식']) expect(text(o)).toContain(s);
    expect(o.querySelectorAll('tbody tr').length).toBeGreaterThan(0);
    expect(o.querySelector('svg')).not.toBeNull();
    expect(text(o)).toContain('추정');
  });

  it('종목을 바꾸는 사이에 늦게 온 앞 종목 응답은 그리지 않는다', async () => {
    let release: (() => void) | null = null;
    const slow = (): Promise<Response> =>
      new Promise((resolve) => {
        release = () => {
          resolve(json(stockFixture('994010', 'header')));
        };
      });
    const hz = p4ApiHarness({ 'stock/994010/header': slow });
    route.setCode('994010');
    const { ctx, root } = p4PageCtx(8, 'login', hz.source);
    const mounting = page.mount(ctx);
    await flush();
    route.setCode('994020');
    await flush();
    (release as (() => void) | null)?.();
    await mounting;
    await settled(root);
    expect(body(root, '종목')).toContain('모의바이오');
    expect(body(root, '종목')).not.toContain('모의전자');
  });

  it('정리 함수가 구독을 끊는다 — 내린 뒤 종목을 바꿔도 다시 그리지 않는다', async () => {
    const err = vi.spyOn(console, 'error');
    const { hz, cleanup } = await mount8('994010');
    expect(typeof cleanup).toBe('function');
    (cleanup as () => void)();
    const n = hz.calls.length;
    route.setCode('994020');
    await flush();
    expect(hz.calls.length).toBe(n);
    expect(err).not.toHaveBeenCalled();
  });

  it('공개 재무 파일 없음 문구는 로그인판에 나오지 않는다(로그인은 API 만)', async () => {
    const { hz, root } = await mount8('994010');
    for (const t of TABS) await openTab(root, t.id);
    expect(text(root)).not.toContain(NO_PUB_FIN);
    expect(hz.paths().every((p) => !p.startsWith('fin/') && !p.endsWith('.json'))).toBe(true);
  });
});

describe('페이지 8 — 일봉만 있는 종목(데모 시드 990001 같은 — 재무·컨센·공시·지분 없음)', () => {
  // 머리·차트·수급만 있고 섹션 라우트는 전부 404 no_data. 데모 서버는 쓰기 채널(관심종목 별)이 있는 소스다
  const SECTIONS = [
    'valuation', 'financials', 'segments', 'overhang', 'insiders', 'consensus', 'disclosures', 'earnings', 'report',
    'scenario',
  ] as const; // prettier-ignore
  const noData = (): Response => json({ code: 'no_data', message: '아직 없음 — 시험(일봉만)' }, 404);
  const dailyOnly = (code: string): Record<string, P4Override> => {
    const over: Record<string, P4Override> = { [`etf/holders/${code}`]: noData, [`trade/stock/${code}`]: noData };
    for (const s of SECTIONS) over[`stock/${code}/${s}`] = noData;
    return over;
  };
  async function mountWritable(code: string, over: Record<string, P4Override>, put: () => Promise<never>) {
    route.setCode(code);
    const hz = p4ApiHarness(over);
    const source = { ...hz.source, put, del: put }; // 쓰기 채널이 있는 로그인 소스(데모 서버와 같은 모양)
    const { ctx, root } = p4PageCtx(8, 'login', source);
    await page.mount(ctx);
    return root;
  }

  it('탭마다 안내(.msg)만 — 오류 줄(.msg.err)은 하나도 없다(빈 오류 줄 자리도 없음)', async () => {
    const root = await mountWritable('994010', dailyOnly('994010'), () => Promise.reject(new Error('부르면 안 된다')));
    expect(root.querySelector('.watch-star button')).not.toBeNull(); // 별(쓰기 채널)이 그려진 상태에서
    for (const t of TABS) {
      await openTab(root, t.id);
      expect(root.querySelectorAll('.msg.err'), t.id).toHaveLength(0);
    }
    expect(root.querySelectorAll('.msg').length).toBeGreaterThan(0);
    expect(text(root)).toContain('아직 없음 — 시험(일봉만)');
    expect(text(panelByTitle(root, '가격 차트'))).not.toContain('아직 없음');
  });

  it('진짜 실패만 오류 줄 — 관심종목 별 쓰기 실패는 그때 .msg.err 로 사유를 보인다', async () => {
    const root = await mountWritable('994010', dailyOnly('994010'), () => Promise.reject(new Error('HTTP 503')));
    const note = root.querySelector('.watch-star .msg');
    expect(note?.classList.contains('err')).toBe(false);
    expect((note as HTMLElement | null)?.hidden).toBe(true);
    click(root.querySelector('.watch-star button'));
    await flush();
    const err = root.querySelectorAll('.watch-star .msg.err');
    expect(err).toHaveLength(1);
    expect(text(err[0] ?? null)).toContain('HTTP 503');
  });
});
