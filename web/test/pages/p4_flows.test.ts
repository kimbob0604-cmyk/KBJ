// 페이지 4 수급·스크리닝 — 스크리너 6기준·필터 쿼리, 종목 상세 차트, 투자자별·기관 7구분·검산 ①② 줄, 장중 잠정, 공개판 자물쇠.
import { describe, expect, it } from 'vitest';
import page, { cumulative, emptyText, MODE_DEF, MODES } from '../../src/pages/p4_flows';
import {
  apiHarness,
  buttonByText,
  choose,
  click,
  fixture,
  flush,
  pageCtx,
  panelByTitle,
  publicHarness,
  selectByLabel,
  text,
} from './support';

async function mountLogin(over: Parameters<typeof apiHarness>[0] = {}) {
  const hz = apiHarness(over);
  const { ctx, root, every } = pageCtx(4, 'login', hz.source);
  await page.mount(ctx);
  return { hz, root, every };
}

const SCREEN_DEFAULT =
  'flows/screen?include_flagged=false&market=all&min_avg_turnover=30000000000&mode=value&period=5&share_class=common';

interface InvFx {
  data: {
    by_investor: Record<string, number | null>;
    inst7: Record<string, number> | null;
    check1_residual: number | null;
    check2_residual: number | null;
  };
}

describe('페이지 4 수급 — 로그인', () => {
  it('기본 쿼리(일평균 300억·5일·보통주·관리종목 제외)로 부르고 그린다', async () => {
    const { hz, root } = await mountLogin();
    expect(hz.paths().sort()).toEqual(['flows/intraday', 'flows/investors', SCREEN_DEFAULT]);

    const scr = panelByTitle(root, '종목 스크리너');
    expect(scr.querySelectorAll('[role="group"] button')).toHaveLength(6);
    expect(scr.querySelectorAll('tbody tr')).toHaveLength(5);
    expect(text(scr.querySelector('.count'))).toBe('30종목 · 관리·정지 제외 2 · 상태 모름(제외 못함) 1');
    expect(text(scr.querySelector(':scope > .def'))).toContain(MODE_DEF.value);
    expect(text(scr.querySelector('.notes'))).toContain('검산 ①(4구분 합 0) 불가');
    const first = scr.querySelector('tbody tr');
    expect(text(first)).toContain('합성10');
    expect(text(first)).toContain('706'); // 70,556,975,250원 → 706억

    expect(text(panelByTitle(root, '종목 상세').querySelector('.msg'))).toBe('표에서 종목을 고르세요');
  });

  it('6기준·시장·기간·하한·종류·관리종목 토글이 쿼리로 간다', async () => {
    const { hz, root } = await mountLogin();
    const scr = panelByTitle(root, '종목 스크리너');
    for (const m of MODES.slice(1)) {
      click(buttonByText(scr, m.label));
      await flush();
      expect(hz.paths().some((p) => p.includes(`mode=${m.value}&`))).toBe(true);
    }
    expect(text(scr.querySelector(':scope > .def'))).toContain(MODE_DEF.spike);
    choose(selectByLabel(scr, '시장'), 'KOSDAQ');
    choose(selectByLabel(scr, '기간'), '20');
    choose(selectByLabel(scr, '일평균 거래대금 ≥'), '0');
    choose(selectByLabel(scr, '종류'), 'pref');
    const box = scr.querySelector<HTMLInputElement>('input[type="checkbox"]');
    if (!box) throw new Error('관리종목 토글 없음');
    box.checked = true;
    box.dispatchEvent(new Event('change'));
    await flush();
    expect(hz.paths().at(-1)).toBe(
      'flows/screen?include_flagged=true&market=KOSDAQ&min_avg_turnover=0&mode=spike&period=20&share_class=pref',
    );
  });

  it('행 선택 → 종목 상세(20일 막대 + 누적 순매수 선, 검산 줄)', async () => {
    const { hz, root } = await mountLogin();
    const row = panelByTitle(root, '종목 스크리너').querySelector<HTMLTableRowElement>('tbody tr');
    click(row);
    await flush();
    expect(hz.paths()).toContain('flows/stock/Q00010?days=20');
    expect(row?.getAttribute('aria-selected')).toBe('true');
    const det = panelByTitle(root, '종목 상세');
    expect(det.querySelectorAll('svg rect')).toHaveLength(20);
    expect(det.querySelectorAll('svg polyline')).toHaveLength(3);
    expect(text(det)).toContain('누적 외국인');
    // 누적 외국인 15,000,000원 → 반올림 0억은 부호 없이, 누적 개인 63,000,000원 → +1억
    expect(text(det.querySelector('.kv'))).toContain('누적 외국인0억');
    expect(text(det.querySelector('.kv'))).toContain('누적 개인+1억');
    const checks = det.querySelectorAll('[data-check]');
    expect([...checks].map((c) => c.getAttribute('data-check'))).toEqual(['ok', 'ok']);
    expect(text(det.querySelector('h3 .src'))).toContain('KRX+KIS');
  });

  it('투자자별 + 기관 7구분 + 검산 ①② 0 차이', async () => {
    const { root } = await mountLogin();
    const inv = panelByTitle(root, '투자자별 순매수');
    expect(inv.querySelectorAll('.lvl')).toHaveLength(4 + 7);
    expect(inv.querySelector('[data-section="inst7"]')).not.toBeNull();
    const checks = [...inv.querySelectorAll('[data-check]')].map((c) => text(c));
    expect(checks).toEqual(['검산 ① 4구분 합 차이 0원', '검산 ② 7구분 합 − 기관 차이 0원']);
  });

  it('7구분이 없으면 칸을 숨기고, 기타법인이 없으면 ① 불가(R2)', async () => {
    const inv = fixture<InvFx>('flows_investors');
    inv.data.inst7 = null;
    inv.data.check2_residual = null;
    inv.data.by_investor.other_corp = null;
    inv.data.check1_residual = null;
    const { root } = await mountLogin({ flows_investors: inv });
    const p = panelByTitle(root, '투자자별 순매수');
    expect(p.querySelector('[data-section="inst7"]')).toBeNull();
    expect(text(p.querySelector('[data-check]'))).toBe('검산 불가: 기타법인 미제공');
    expect(text(p.querySelector('.body'))).not.toContain('검산 ②');
  });

  it('검산 잔차가 0 이 아니면 경고 표시', async () => {
    const inv = fixture<InvFx>('flows_investors');
    inv.data.check2_residual = 1_000_000;
    const { root } = await mountLogin({ flows_investors: inv });
    const fail = panelByTitle(root, '투자자별 순매수').querySelector('[data-check="fail"]');
    expect(text(fail)).toBe('검산 ② 7구분 합 − 기관 차이 +1,000,000원 (0 이어야 정상)');
  });

  it('invalid 행은 그리지 않고 수만 적는다', async () => {
    const scr = fixture<{ data: { rows: { quality: string }[] } }>('flows_screen');
    const r0 = scr.data.rows[0];
    if (r0) r0.quality = 'invalid';
    const { root } = await mountLogin({ flows_screen: scr });
    const p = panelByTitle(root, '종목 스크리너');
    expect(p.querySelectorAll('tbody tr')).toHaveLength(4);
    expect(text(p.querySelector('.notes'))).toContain('검산 실패 1행 제외');
    expect(text(p)).not.toContain('합성10');
  });

  it('장중 잠정: 세 표 모두 잠정 배지, 이름은 순위 응답·스크리너에서', async () => {
    const { root } = await mountLogin();
    const p = panelByTitle(root, '장중 잠정');
    expect(p.querySelectorAll('table')).toHaveLength(3);
    expect(text(p.querySelector('h3 .src'))).toContain('잠정');
    expect(text(p)).toContain('10:00 슬롯 · 장중 잠정 — 15:35 마감 뒤 확정');
    expect(p.querySelectorAll('.q-badge').length).toBeGreaterThanOrEqual(15);
    expect(text(p.querySelector('tbody tr'))).toContain('합성Q00000');
  });

  it('누적 합 — 값이 없는 날은 끊고 이어서 더한다', () => {
    expect(cumulative([1, 2, null, 3])).toEqual([1, 3, null, 6]);
  });
});

describe('페이지 4 수급 — 공개', () => {
  it('요청 없이 자물쇠만', async () => {
    const hz = publicHarness();
    const { ctx, root } = pageCtx(4, 'public', hz.source);
    await page.mount(ctx);
    expect(hz.calls).toHaveLength(0);
    expect(root.querySelectorAll('.panel.locked')).toHaveLength(4);
    expect(root.querySelectorAll('.filters')).toHaveLength(0);
  });
});

describe('스크리너 빈 결과 안내', () => {
  const base = { n_total: 30, n_excluded: { flagged: 0, invalid: 0, below_min: 0, status_unknown: 0 } };
  it('하한 미달이 있으면 하한을 낮추라고 알린다', () => {
    const d = { ...base, n_excluded: { ...base.n_excluded, flagged: 2, below_min: 28 } };
    expect(emptyText(d as never)).toBe('조건에 맞는 종목 없음 — 일평균 거래대금 하한 미달 28종목, 하한을 낮춰 보세요');
  });
  it('관리·정지 제외만 있으면 그 수를, 아무것도 없으면 기본 문구', () => {
    expect(emptyText({ ...base, n_excluded: { ...base.n_excluded, flagged: 3 } } as never)).toContain('관리·정지 제외 3종목');
    expect(emptyText(base as never)).toBe('조건에 맞는 종목 없음');
  });
  it('빈 표에 안내가 그려진다', async () => {
    const screen = fixture<{ data: { rows: unknown[]; n_excluded: Record<string, number> } }>('flows_screen');
    screen.data.rows = [];
    screen.data.n_excluded.below_min = 5;
    const { root } = await mountLogin({ flows_screen: screen });
    expect(text(panelByTitle(root, '종목 스크리너'))).toContain('하한 미달 5종목, 하한을 낮춰 보세요');
  });
});
