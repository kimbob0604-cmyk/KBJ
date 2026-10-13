// 페이지 10 P5 패널 — ETF 맵(트리맵·기간·묶음·레버리지 토글·신규 상장 회색), 보유 탐색(ETF → 구성종목, 종목 → 담은 ETF,
// 맵 칸 누르기), 액티브 동시 편입, 공개판 자물쇠. 픽스처 = test/fixtures/api/etf/*.json(합성 p5_world 응답 —
// tests/unit/api/web_fixtures_p5/etf.py).
import { describe, expect, it } from 'vitest';
import page from '../../src/pages/p10_etf';
import { TITLES } from '../../src/pages/p10/index';
import { cellLabel, colorRange, type MapData, mapItems } from '../../src/pages/p10/map';
import { normalizeCode } from '../../src/pages/p10/holdings';
import { json } from '../helpers';
import { apiHarness, buttonByText, click, flush, notFound, type Override, pageCtx, panelByTitle, publicHarness, text } from './support';

const P5 = import.meta.glob<unknown>('../fixtures/api/etf/*.json', { eager: true, import: 'default' });

// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- 시험이 픽스처 모양을 적어 받는다
function p5<T = Record<string, unknown>>(name: string): T {
  const v = P5[`../fixtures/api/etf/${name}.json`];
  if (v === undefined) throw new Error(`픽스처 없음: etf/${name}`);
  return structuredClone(v) as T;
}

function mapFor(url: string): Response {
  const lev = url.includes('lev=true');
  const name = url.includes('group=theme') ? 'map_theme_1w_lev' : lev || url.includes('period=3M') ? 'map_type_3m_lev' : 'map_type_1m';
  return json(p5(name));
}

/** P5 경로 → 픽스처(apiHarness 의 이름 규칙: '/' → '_') */
function p5Overrides(): Record<string, Override> {
  return {
    etf_map: mapFor,
    'etf_active-consensus': (url: string) => json(p5(url.includes('days=1') ? 'active_consensus' : 'active_consensus_5d')),
    etf_fund_997560_holdings: p5('fund_holdings_active_a'),
    etf_fund_997520_holdings: p5('fund_holdings_semi'),
    etf_holders_997190: p5('holders_robot'),
    etf_holders_997240: p5('holders_none'),
  };
}

async function mountLogin(over: Record<string, Override> = {}) {
  const hz = apiHarness({ ...p5Overrides(), ...over });
  const { ctx, root, every } = pageCtx(10, 'login', hz.source);
  await page.mount(ctx);
  await flush();
  return { hz, root, every };
}

function submitCode(panelEl: HTMLElement, code: string): void {
  const input = panelEl.querySelector('input');
  const form = panelEl.querySelector('form');
  if (!input || !form) throw new Error('입력 상자 없음');
  input.value = code;
  form.dispatchEvent(new Event('submit', { cancelable: true }));
}

describe('페이지 10 P5 — 로그인', () => {
  it('맵·동시 편입을 부르고 보유 탐색은 부르지 않는다', async () => {
    const { hz, root } = await mountLogin();
    const paths = hz.paths();
    expect(paths).toContain('etf/map?group=type&period=1M');
    expect(paths).toContain('etf/active-consensus?days=1');
    expect(paths.filter((p) => p.startsWith('etf/fund/') || p.startsWith('etf/holders/'))).toEqual([]);

    const m = panelByTitle(root, TITLES.map);
    const data = p5<{ data: MapData }>('map_type_1m').data;
    expect(m.querySelectorAll('.map-group')).toHaveLength(data.groups.length);
    const ids = [...m.querySelectorAll('g[data-id]')].map((g) => g.getAttribute('data-id'));
    expect(ids).toHaveLength(6);
    expect(ids).not.toContain('997540'); // 레버리지 숨김
    expect(text(m.querySelector('[data-section="map-head"]'))).toContain('11/16 기준 · 1개월 수익률 · ETF 6개');
    expect(text(m)).toContain('국내 테마');
    // 신규 상장은 회색(색 값 없음) + 꼬리표
    const newCell = m.querySelector('g[data-id="997580"] title');
    expect(text(newCell)).toContain('합성 신규상장 · 신규');
    expect(text(m.querySelector('.src'))).toContain('KRX');

    const c = panelByTitle(root, TITLES.cons);
    const rows = c.querySelectorAll('tbody tr');
    expect(rows).toHaveLength(1);
    expect(text(rows[0])).toContain('합성로봇');
    expect(text(rows[0])).toContain('합성 액티브A, 합성 액티브B');
    expect(text(rows[0])).toContain('신규 편입');

    const h = panelByTitle(root, TITLES.hold);
    expect(text(h)).toContain('코드를 넣으세요');
  });

  it('기간·묶음·레버리지 토글이 쿼리를 바꾼다', async () => {
    const { hz, root } = await mountLogin();
    const m = panelByTitle(root, TITLES.map);
    click(buttonByText(m, '3개월'));
    await flush();
    expect(hz.paths()).toContain('etf/map?group=type&period=3M');
    click(buttonByText(m, '보기'));
    await flush();
    expect(hz.paths()).toContain('etf/map?group=type&lev=true&period=3M');
    expect([...m.querySelectorAll('g[data-id]')].map((g) => g.getAttribute('data-id'))).toContain('997540');
    click(buttonByText(m, '테마'));
    await flush();
    expect(hz.paths()).toContain('etf/map?group=theme&lev=true&period=3M');
    expect(m.querySelector('.map-group[data-group="반도체"]')).not.toBeNull();

    const c = panelByTitle(root, TITLES.cons);
    click(buttonByText(c, '5일'));
    await flush();
    expect(hz.paths()).toContain('etf/active-consensus?days=5');
  });

  it('보유 탐색 — ETF 구성종목, 종목 → 담은 ETF, 잘못된 코드', async () => {
    const { hz, root } = await mountLogin();
    const h = panelByTitle(root, TITLES.hold);
    submitCode(h, '99756');
    await flush();
    expect(text(h.querySelector('.count'))).toBe('코드는 영숫자 6자리');
    expect(hz.paths().some((p) => p.startsWith('etf/fund/'))).toBe(false);

    submitCode(h, '997560');
    await flush();
    expect(hz.paths()).toContain('etf/fund/997560/holdings');
    expect(h.querySelectorAll('tbody tr')).toHaveLength(6);
    expect(text(h.querySelector('caption'))).toContain('합성 액티브A (997560) · PDF 11/16 · 6종목 · 변동 2');
    expect(h.querySelectorAll('tbody .tag.up')).toHaveLength(2);

    click(buttonByText(h, '종목 → 담은 ETF'));
    submitCode(h, '997190');
    await flush();
    expect(hz.paths()).toContain('etf/holders/997190');
    const rows = h.querySelectorAll('tbody tr');
    expect(rows).toHaveLength(2);
    expect(text(rows[0])).toContain('합성 액티브B');
    expect(text(rows[0])).toContain('액티브');
    expect(text(h.querySelector('caption'))).toContain('담은 ETF 2개 · 액티브 2');

    submitCode(h, '997240');
    await flush();
    expect(text(h)).toContain('이 종목을 담은 ETF 없음');
  });

  it('맵 칸을 누르면 그 ETF 구성종목을 연다', async () => {
    const { hz, root } = await mountLogin();
    const m = panelByTitle(root, TITLES.map);
    const cell = m.querySelector('g[data-id="997520"] rect');
    if (!cell) throw new Error('칸 없음');
    cell.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await flush();
    expect(hz.paths()).toContain('etf/fund/997520/holdings');
    const h = panelByTitle(root, TITLES.hold);
    expect(text(h.querySelector('caption'))).toContain('합성 반도체 (997520)');
  });

  it('아직 없음(404)은 그 패널에 작업·시각을 적는다', async () => {
    const { root } = await mountLogin({
      etf_map: () => notFound('아직 없음 — etf.collect 08:00 · krx.daily 08:05'),
    });
    const m = panelByTitle(root, TITLES.map);
    expect(text(m)).toContain('아직 없음');
    expect(panelByTitle(root, TITLES.cons).querySelectorAll('tbody tr')).toHaveLength(1);
  });

  it('주기 새로 고침은 맵·동시 편입을 다시 부른다', async () => {
    const { hz, every } = await mountLogin();
    const before = hz.paths().filter((p) => p.startsWith('etf/map')).length;
    for (const e of every) e.fn();
    await flush();
    expect(hz.paths().filter((p) => p.startsWith('etf/map')).length).toBe(before + 1);
  });
});

describe('페이지 10 P5 — 공개판', () => {
  it('세 패널 모두 자물쇠, 요청 없음', async () => {
    const hz = publicHarness();
    const { ctx, root } = pageCtx(10, 'public', hz.source);
    await page.mount(ctx);
    for (const t of Object.values(TITLES)) expect(panelByTitle(root, t).classList.contains('locked')).toBe(true);
    expect(hz.calls).toHaveLength(0);
  });
});

describe('페이지 10 P5 — 도우미', () => {
  it('맵 칸: 신규 상장은 색 없음 + 꼬리표, 색 범위는 모든 묶음 공통', () => {
    const d = p5<{ data: MapData }>('map_type_3m_lev').data;
    const cells = d.groups.flatMap((g) => g.cells);
    const items = mapItems(cells);
    const nw = items.find((i) => i.id === '997580');
    expect(nw?.color).toBeNull();
    expect(nw?.label).toBe('합성 신규상장 · 신규');
    const want = Math.max(...cells.filter((c) => !c.new_listing && c.ret_pct !== null).map((c) => Math.abs(c.ret_pct ?? 0)));
    expect(colorRange(d)).toBe(want);
    expect(cellLabel({ ...cells[0]!, name: null, new_listing: false })).toBe(cells[0]!.code);
  });

  it('코드 정규화', () => {
    expect(normalizeCode(' 997560 ')).toBe('997560');
    expect(normalizeCode('q00001')).toBe('Q00001');
    expect(normalizeCode('12345')).toBeNull();
    expect(normalizeCode('00593O!')).toBeNull();
  });
});
