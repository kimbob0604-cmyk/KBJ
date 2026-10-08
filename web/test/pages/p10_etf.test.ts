// 페이지 10 ETF 수급 — 자금 흐름(6정렬·유형·기간 쿼리), 유형별 순유입 + 검산 ③ 줄, 괴리율(마감 NAV/장중 iNAV),
// 구성종목 변동(액티브 우선·필터), 분할 보정·신규 상장 표시, 공개판 자물쇠.
import { describe, expect, it } from 'vitest';
import page, { changeText, MODES, sortChanges, TYPE_LABEL } from '../../src/pages/p10_etf';
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
  const { ctx, root, every } = pageCtx(10, 'login', hz.source);
  await page.mount(ctx);
  return { hz, root, every };
}

type Row = Record<string, unknown>;

describe('페이지 10 ETF — 로그인', () => {
  it('네 응답을 부르고 그린다', async () => {
    const { hz, root } = await mountLogin();
    expect(hz.paths().sort()).toEqual([
      'etf/flows?mode=in&period=5',
      'etf/holdings/changes',
      'etf/premium?basis=nav',
      'etf/types?period=5',
    ]);

    const fl = panelByTitle(root, 'ETF 자금 흐름');
    expect(fl.querySelectorAll('[role="group"] button')).toHaveLength(6);
    expect(fl.querySelectorAll('tbody tr')).toHaveLength(11);
    expect(text(fl.querySelector('.count'))).toBe('11개 · 09/09 ~ 09/15');
    const first = fl.querySelector('tbody tr');
    expect(text(first)).toContain('합성ETF07');
    expect(text(first)).toContain('국내 대표지수');
    expect(text(first)).toContain('1.7조'); // 순자산 1,660,954,053,000원
    expect(text(first)).toContain('+683'); // 순유입 68,307,262,000원 → +683억
    expect(text(fl.querySelector(':scope > .def'))).toContain('순유입(설정−환매)');
    expect(text(fl.querySelector('.notes'))).toContain('순유입과 합치지 않는다');
    expect(selectByLabel(fl, '유형').options).toHaveLength(1 + Object.keys(TYPE_LABEL).length);

    const ty = panelByTitle(root, '유형별 순유입');
    expect(ty.querySelectorAll('.lvl')).toHaveLength(7);
    expect(text(ty.querySelector('[data-check]'))).toBe('검산 ③ 순자산 변화 − (순유입 + 가격효과) 차이 0원');
    expect(text(ty)).toContain('검사 55행 · 실패 0 · 불가 0');

    const pr = panelByTitle(root, '괴리율 경고');
    expect(pr.querySelectorAll('tbody tr')).toHaveLength(3);
    expect(pr.querySelector('tbody td.warn')).not.toBeNull();
    // 표 머리는 요청이 아니라 응답의 basis 를 따른다(픽스처는 장중 iNAV 응답)
    expect(text(pr.querySelector('caption'))).toBe('장중 iNAV(잠정) 기준 · 검사 3개');

    const ho = panelByTitle(root, '구성종목 변동');
    const rows = ho.querySelectorAll('tbody tr');
    expect(rows).toHaveLength(3);
    expect(text(rows[0])).toContain('액티브');
    expect(text(rows[2])).toContain('TOP10 이탈'); // 패시브(시장대표)는 뒤로
    expect(selectByLabel(ho, '운용사').options).toHaveLength(3);
    expect(root.querySelectorAll('.msg.err')).toHaveLength(0);
  });

  it('정렬·유형·기간 → 쿼리, 기간은 유형별 순유입도 다시 부른다', async () => {
    const { hz, root } = await mountLogin();
    const fl = panelByTitle(root, 'ETF 자금 흐름');
    for (const m of MODES.slice(1)) {
      click(buttonByText(fl, m.label));
      await flush();
      expect(hz.paths()).toContain(`etf/flows?mode=${m.value}&period=5`);
    }
    choose(selectByLabel(fl, '유형'), 'leveraged_inverse');
    await flush();
    expect(hz.paths()).toContain('etf/flows?mode=inst&period=5&type=leveraged_inverse');
    choose(selectByLabel(fl, '기간'), '20');
    await flush();
    expect(hz.paths()).toContain('etf/flows?mode=inst&period=20&type=leveraged_inverse');
    expect(hz.paths()).toContain('etf/types?period=20');
  });

  it('괴리율 기준 토글(장중 iNAV) — 잠정 표시', async () => {
    const { hz, root } = await mountLogin();
    const pr = panelByTitle(root, '괴리율 경고');
    click(buttonByText(pr, '장중 iNAV'));
    await flush();
    expect(hz.paths()).toContain('etf/premium?basis=inav');
    expect(text(pr.querySelector('h3 .src'))).toContain('잠정');
    expect(pr.querySelector('tbody .q-est')).not.toBeNull();
    expect(text(pr.querySelector('thead'))).toContain('iNAV');
  });

  it('구성종목 변동 필터 → kind·issuer 쿼리', async () => {
    const { hz, root } = await mountLogin();
    const ho = panelByTitle(root, '구성종목 변동');
    choose(selectByLabel(ho, '변동'), 'NEW');
    await flush();
    expect(hz.paths()).toContain('etf/holdings/changes?kind=NEW');
    choose(selectByLabel(ho, '운용사'), '합성운용B');
    await flush();
    expect(hz.paths()).toContain(
      'etf/holdings/changes?issuer=합성운용B&kind=NEW'.replace('합성운용B', encodeURIComponent('합성운용B')),
    );
  });

  it('분할 보정·신규 상장·invalid 행', async () => {
    const f = fixture<{ data: { rows: Row[]; new_listings: Row[] } }>('etf_flows');
    const [a, b] = f.data.rows;
    if (!a || !b) throw new Error('픽스처 행 부족');
    a.status = 'split_adjusted';
    a.quality = 'estimated';
    b.status = 'invalid';
    b.quality = 'invalid';
    f.data.new_listings = [{ code: 'E09999', name: '합성신규ETF', listed_on: '2026-09-14', net_asset: 50_000_000_000 }];
    const { root } = await mountLogin({ etf_flows: f });
    const fl = panelByTitle(root, 'ETF 자금 흐름');
    expect(fl.querySelectorAll('tbody tr')).toHaveLength(10);
    expect(text(fl.querySelector('tbody tr'))).toContain('분할 보정');
    expect(fl.querySelector('tbody tr .q-badge')?.textContent).toBe('잠정');
    expect(text(fl.querySelector('.notes'))).toContain('검산 실패 1행 제외');
    expect(text(fl.querySelector('[data-section="new-listings"]'))).toContain('합성신규ETF(E09999) 09/14 500억');
  });

  it('보고 순자산이 없으면 검산 ③ 불가 — 0 으로 그리지 않는다', async () => {
    const t = fixture<{ data: { check3: Row } }>('etf_types');
    t.data.check3.residual = null;
    t.data.check3.net_asset_chg = null;
    const { root } = await mountLogin({ etf_types: t });
    expect(text(panelByTitle(root, '유형별 순유입').querySelector('[data-check]'))).toBe('검산 불가: 보고 순자산 없음');
  });

  it('검산 ③ 판정은 행마다 허용오차 — 합계 잔차가 0 이 아니어도 실패 행이 없으면 정상, 잔차는 그대로', async () => {
    const ok = fixture<{ data: { check3: Row } }>('etf_types');
    ok.data.check3.residual = 37;
    ok.data.check3.n_failed = 0;
    const a = await mountLogin({ etf_types: ok });
    const lineA = panelByTitle(a.root, '유형별 순유입').querySelector<HTMLElement>('[data-check]');
    expect(lineA?.dataset.check).toBe('ok');
    expect(text(lineA)).toContain('차이 +37원 (행마다 허용오차 안)');

    const bad = fixture<{ data: { check3: Row } }>('etf_types');
    bad.data.check3.residual = 0;
    bad.data.check3.n_failed = 2;
    const b = await mountLogin({ etf_types: bad });
    const lineB = panelByTitle(b.root, '유형별 순유입').querySelector<HTMLElement>('[data-check]');
    expect(lineB?.dataset.check).toBe('fail');
    expect(text(lineB)).toContain('허용오차 밖 2건');
  });

  it('늦게 온 옛 응답은 새 응답을 덮지 않는다(정렬을 빠르게 바꿀 때)', async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((r) => {
      release = r;
    });
    let gated = false;
    const flowsFor = (url: string) => {
      const f = fixture<{ data: Row }>('etf_flows');
      f.data.n_total = url.includes('mode=value') ? 111 : url.includes('mode=indiv') ? 222 : 5;
      return f;
    };
    const { root } = await mountLogin({
      etf_flows: async (url: string) => {
        if (gated && url.includes('mode=value')) await gate;
        return new Response(JSON.stringify(flowsFor(url)), { status: 200 });
      },
    });
    gated = true;
    const fl = panelByTitle(root, 'ETF 자금 흐름');
    click(buttonByText(fl, '거래대금 상위')); // 늦게 온다
    click(buttonByText(fl, '개인 순매수')); // 먼저 온다
    await flush();
    expect(text(fl.querySelector('.count'))).toContain('222개');
    release();
    await flush();
    expect(text(fl.querySelector('.count'))).toContain('222개');
  });

  it('구성종목 정렬·문구 도우미', () => {
    const base = {
      fund_id: 'f',
      etf_code: null,
      fund_name: null,
      issuer: null,
      code: 'Q1',
      name: null,
      kind_label: '',
      prev_qty: null,
      cur_qty: null,
      prev_wt: null,
      cur_wt: null,
      qty_pct_adj: null,
      asof: '2026-09-15',
      prev_asof: '2026-09-14',
      gap_days: 1,
    };
    const rows = [
      { ...base, fund_id: 'p', is_active: false, theme: '시장대표', kind: 'ADD' as const },
      { ...base, fund_id: 't', is_active: false, theme: 'AI반도체', kind: 'ADD' as const },
      { ...base, fund_id: 'a', is_active: true, theme: null, kind: 'ADD' as const },
    ];
    expect(sortChanges(rows).map((r) => r.fund_id)).toEqual(['a', 't', 'p']);
    expect(changeText({ ...base, is_active: true, theme: null, kind: 'CUT', qty_pct_adj: -12.5 })).toBe(
      '수량 -12.50%(CU 보정)',
    );
    expect(changeText({ ...base, is_active: true, theme: null, kind: 'NEW', cur_wt: 3.1 })).toBe('비중 3.10%');
    expect(changeText({ ...base, is_active: true, theme: null, kind: 'DROP' })).toBe('제외');
  });
});

describe('페이지 10 ETF — 공개', () => {
  it('요청 없이 자물쇠만', async () => {
    const hz = publicHarness();
    const { ctx, root } = pageCtx(10, 'public', hz.source);
    await page.mount(ctx);
    expect(hz.calls).toHaveLength(0);
    expect(root.querySelectorAll('.panel.locked')).toHaveLength(4);
    expect(text(panelByTitle(root, '구성종목 변동'))).toContain('운용사 구성종목');
  });
});
