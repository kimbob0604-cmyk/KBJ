// P4 부품 — 검색 상자·탭·밸류 밴드·워터폴·누적 막대·타임라인·키-값 (p4_design §1.2 ui_p4.test)
// 공통 단언: 빈 배열·음수·전부 null 에도 예외 없음, 그린 결과(DOM·SVG 글자)에 'NaN'·'undefined'·'Infinity'·'null' 없음.
import axe from 'axe-core';
import { describe, expect, it, vi } from 'vitest';
import { DataError } from '../src/data/source';
import { bandChart, bandPrices } from '../src/ui/band';
import { h } from '../src/ui/dom';
import { kv } from '../src/ui/kv';
import { EST_LABEL, estBadge, markQuality, qValue } from '../src/ui/quality';
import { matchItems, normalizeQuery, searchBox, type SearchItem } from '../src/ui/search';
import { stackBar, stackLayout } from '../src/ui/stackbar';
import { tabs } from '../src/ui/tabs';
import { dayNumber, timeline } from '../src/ui/timeline';
import { waterfall, waterfallBars } from '../src/ui/waterfall';

const BAD = /\b(?:NaN|undefined|Infinity|null)\b/;

/** 요소 전체(속성 포함)를 글자로 — outerHTML 은 쓰지 않는다(eslint) */
function dump(el: Node): string {
  return new XMLSerializer().serializeToString(el);
}

function expectClean(el: Node): void {
  expect(dump(el)).not.toMatch(BAD);
}

async function serious(root: Element): Promise<string[]> {
  const res = await axe.run(root, { resultTypes: ['violations'], rules: { 'color-contrast': { enabled: false } } });
  return res.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
}

const ITEMS: SearchItem[] = [
  { code: '005930', name: '가상전자', market: 'KOSPI' },
  { code: '005935', name: '가상전자우', market: 'KOSPI', note: '우선주' },
  { code: 'Q00001', name: '예시바이오', market: 'KOSDAQ' },
  { code: '000660', name: '예시하이닉스', market: 'KOSPI' },
];

// ── 검색 상자 ─────────────────────────────────────────────────

describe('검색: 로컬 찾기(공개판 corp_index)', () => {
  it('순위: 코드 일치 > 코드 앞부분 > 이름 앞부분 > 이름 포함, 대소문자 무시', () => {
    expect(matchItems(ITEMS, '005930').map((i) => i.code)).toEqual(['005930']);
    expect(matchItems(ITEMS, '0059').map((i) => i.code)).toEqual(['005930', '005935']);
    expect(matchItems(ITEMS, 'q0000').map((i) => i.code)).toEqual(['Q00001']);
    expect(matchItems(ITEMS, '예시').map((i) => i.code)).toEqual(['Q00001', '000660']);
    expect(matchItems(ITEMS, '하이').map((i) => i.code)).toEqual(['000660']);
    expect(matchItems(ITEMS, '전자', 1)).toHaveLength(1);
    expect(matchItems(ITEMS, '   ')).toEqual([]);
    expect(matchItems([], '가상')).toEqual([]);
  });

  it('질의 정리: 공백 제거·20자 자름', () => {
    expect(normalizeQuery('  가상  ')).toBe('가상');
    expect(normalizeQuery('가'.repeat(30))).toHaveLength(20);
  });
});

describe('검색 상자', () => {
  function mk(search: (q: string) => Promise<readonly SearchItem[]> | readonly SearchItem[]) {
    const onSelect = vi.fn();
    const box = searchBox({ label: '종목 검색', search, onSelect, debounceMs: 0 });
    document.body.append(box.el);
    return { box, onSelect };
  }
  const key = (el: Element, k: string) => el.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }));

  it('콤보박스 접근성: 이름표·aria-controls·aria-expanded, 후보는 option', async () => {
    const { box } = mk((q) => matchItems(ITEMS, q));
    expect(box.input.getAttribute('role')).toBe('combobox');
    expect(box.el.querySelector('label')?.getAttribute('for')).toBe(box.input.id);
    expect(box.input.getAttribute('aria-expanded')).toBe('false');
    await box.query('가상');
    const list = document.getElementById(box.input.getAttribute('aria-controls') ?? '');
    expect(list?.getAttribute('role')).toBe('listbox');
    expect(list?.hidden).toBe(false);
    expect(box.input.getAttribute('aria-expanded')).toBe('true');
    expect([...(list?.querySelectorAll('[role="option"]') ?? [])].map((li) => (li as HTMLElement).dataset.code)).toEqual(['005930', '005935']);
    expect(await serious(document.body)).toEqual([]);
    expectClean(box.el);
  });

  it('↓/↑ 이동(끝에서 돈다)·Enter 고르기 → onSelect, 목록 닫힘', async () => {
    const { box, onSelect } = mk((q) => matchItems(ITEMS, q));
    await box.query('가상');
    key(box.input, 'ArrowDown');
    expect(box.input.getAttribute('aria-activedescendant')).toMatch(/-opt-0$/);
    key(box.input, 'ArrowDown');
    key(box.input, 'ArrowDown'); // 2개 → 처음으로
    expect(box.input.getAttribute('aria-activedescendant')).toMatch(/-opt-0$/);
    key(box.input, 'ArrowUp');
    expect(box.input.getAttribute('aria-activedescendant')).toMatch(/-opt-1$/);
    key(box.input, 'Enter');
    expect(onSelect).toHaveBeenCalledWith(ITEMS[1]);
    expect(box.input.getAttribute('aria-expanded')).toBe('false');
    expect(box.input.value).toBe('가상전자우 005935');
  });

  it('Enter: 후보를 안 골랐으면 6자리 코드 그대로, 아니면 첫 후보', async () => {
    const a = mk(() => []);
    await a.box.query('q00009');
    key(a.box.input, 'Enter');
    expect(a.onSelect).toHaveBeenCalledWith({ code: 'Q00009', name: '' });
    const b = mk((q) => matchItems(ITEMS, q));
    await b.box.query('예시');
    key(b.box.input, 'Enter');
    expect(b.onSelect).toHaveBeenCalledWith(ITEMS[2]);
    const c = mk(() => []);
    await c.box.query('없는이름');
    key(c.box.input, 'Enter');
    expect(c.onSelect).not.toHaveBeenCalled();
  });

  it('결과 없음은 목록 밖 한 줄(빈 listbox 를 열지 않는다), Escape 닫기·지우기', async () => {
    const { box } = mk(() => []);
    await box.query('없음');
    expect(box.input.getAttribute('aria-expanded')).toBe('false');
    expect(box.el.querySelector('.search-msg')?.textContent).toBe('결과 없음');
    const { box: b2 } = mk((q) => matchItems(ITEMS, q));
    await b2.query('가상');
    key(b2.input, 'Escape');
    expect(b2.input.getAttribute('aria-expanded')).toBe('false');
    key(b2.input, 'Escape');
    expect(b2.input.value).toBe('');
  });

  it('잘못된 코드 모양의 후보는 버리고, 20개로 자른다', async () => {
    const many = Array.from({ length: 30 }, (_, i) => ({ code: `Q${String(i).padStart(5, '0')}`, name: `예시${i}` }));
    const { box } = mk(() => [...many, { code: 'bad', name: '틀린코드' }]);
    await box.query('예시');
    expect(box.items()).toHaveLength(20);
    expect(box.items().some((i) => i.code === 'bad')).toBe(false);
  });

  it('늦게 온 앞 질의 결과는 버린다', async () => {
    const resolvers: ((v: readonly SearchItem[]) => void)[] = [];
    const { box } = mk(() => new Promise<readonly SearchItem[]>((r) => resolvers.push(r)));
    const first = box.query('가');
    const second = box.query('예시');
    resolvers[1]?.([ITEMS[2]!]);
    await second;
    resolvers[0]?.([ITEMS[0]!]);
    await first;
    expect(box.items().map((i) => i.code)).toEqual(['Q00001']);
  });

  it('찾기 실패는 "검색하지 못함" 한 줄 — 데이터 오류는 콘솔 없이, 예상 밖 오류는 콘솔에', async () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const a = mk(() => Promise.reject(new DataError('불러오지 못함 (HTTP 500)', 500)));
    await a.box.query('가상');
    expect(a.box.el.querySelector('.search-msg.err')?.textContent).toBe('검색하지 못함');
    expect(err).not.toHaveBeenCalled();
    const b = mk(() => {
      throw new Error('고장');
    });
    await b.box.query('가상');
    expect(b.box.el.querySelector('.search-msg')?.textContent).toBe('검색하지 못함');
    expect(err).toHaveBeenCalled();
  });

  it('입력은 기다렸다가 찾는다(debounce) — 마지막 글자만', async () => {
    vi.useFakeTimers();
    try {
      const search = vi.fn((q: string) => matchItems(ITEMS, q));
      const box = searchBox({ label: '종목 검색', search, onSelect: () => undefined, debounceMs: 100 });
      document.body.append(box.el);
      for (const t of ['가', '가상']) {
        box.input.value = t;
        box.input.dispatchEvent(new Event('input'));
      }
      expect(search).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(100);
      await box.settled();
      expect(search).toHaveBeenCalledTimes(1);
      expect(search).toHaveBeenCalledWith('가상');
      // 기다리는 중에 내리면 찾지 않고, settled() 도 걸려 있지 않는다
      box.input.value = '예시';
      box.input.dispatchEvent(new Event('input'));
      box.destroy();
      await box.settled();
      await vi.advanceTimersByTimeAsync(200);
      expect(search).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('입력 직후 Enter(debounce 를 기다리는 중)는 앞 질의 목록이 아니라 지금 글자의 첫 후보를 고른다', async () => {
    vi.useFakeTimers();
    try {
      const onSelect = vi.fn();
      const search = vi.fn((q: string) => matchItems(ITEMS, q));
      const box = searchBox({ label: '종목 검색', search, onSelect, debounceMs: 150 });
      document.body.append(box.el);
      box.input.value = '가상';
      box.input.dispatchEvent(new Event('input'));
      await vi.advanceTimersByTimeAsync(150);
      await box.settled();
      expect(box.items().map((i) => i.code)).toEqual(['005930', '005935']);
      box.input.value = '예시';
      box.input.dispatchEvent(new Event('input'));
      key(box.input, 'Enter'); // 아직 '가상' 목록이 열려 있다
      await box.settled();
      expect(onSelect).toHaveBeenCalledTimes(1);
      expect(onSelect).toHaveBeenCalledWith(ITEMS[2]);
      // 기다리던 찾기는 취소되고 한 번만 찾는다
      await vi.advanceTimersByTimeAsync(300);
      expect(search.mock.calls.map((c) => c[0])).toEqual(['가상', '예시']);
    } finally {
      vi.useRealTimers();
    }
  });

  it('↓ 로 고른 뒤 글자를 더 치면 그 후보는 버린다 — 찾는 중 Enter 는 새 결과를 기다려 고른다', async () => {
    let release: (() => void) | null = null;
    const search = vi.fn((q: string) =>
      q === '가상' ? matchItems(ITEMS, q) : new Promise<readonly SearchItem[]>((r) => (release = () => r(matchItems(ITEMS, q)))),
    );
    const { box, onSelect } = mk(search);
    await box.query('가상');
    key(box.input, 'ArrowDown');
    expect(box.input.getAttribute('aria-activedescendant')).toMatch(/-opt-0$/);
    box.input.value = '예시';
    box.input.dispatchEvent(new Event('input'));
    expect(box.input.hasAttribute('aria-activedescendant')).toBe(false);
    key(box.input, 'Enter');
    expect(onSelect).not.toHaveBeenCalled();
    expect(search).toHaveBeenCalledTimes(2); // 이미 찾는 중인 같은 질의는 다시 찾지 않는다
    (release as (() => void) | null)?.();
    await box.settled();
    expect(onSelect).toHaveBeenCalledWith(ITEMS[2]);
  });
});

// ── 탭 ───────────────────────────────────────────────────────

describe('탭', () => {
  const TABS = [
    { id: 'overview', label: '개요' },
    { id: 'fin', label: '재무' },
    { id: 'report', label: '리포트' },
    { id: 'peer', label: '피어', disabled: true },
  ] as const;

  it('tablist·tab·tabpanel 연결, 고른 탭만 보이고 tabindex 0', async () => {
    const t = tabs({ label: '종목 상세 탭', tabs: TABS });
    document.body.append(t.el);
    expect(t.bar.getAttribute('role')).toBe('tablist');
    const btns = [...t.bar.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
    expect(btns.map((b) => b.getAttribute('aria-selected'))).toEqual(['true', 'false', 'false', 'false']);
    expect(btns.map((b) => b.tabIndex)).toEqual([0, -1, -1, -1]);
    expect(btns[3]?.getAttribute('aria-disabled')).toBe('true');
    for (const b of btns) {
      const p = document.getElementById(b.getAttribute('aria-controls') ?? '');
      expect(p?.getAttribute('role')).toBe('tabpanel');
      expect(p?.getAttribute('aria-labelledby')).toBe(b.id);
    }
    expect(t.panel('overview').hidden).toBe(false);
    expect(t.panel('fin').hidden).toBe(true);
    expect(await serious(document.body)).toEqual([]);
  });

  it('클릭·←/→·Home·End(비활성 탭 건너뜀), 키는 preventDefault(셸의 페이지 이동과 겹치지 않게)', () => {
    const changed: string[] = [];
    const t = tabs({ label: '탭', tabs: TABS, onChange: (id) => changed.push(id) });
    document.body.append(t.el);
    const btn = (id: string) => t.bar.querySelector<HTMLButtonElement>(`[data-tab="${id}"]`)!;
    btn('fin').click();
    expect(t.value()).toBe('fin');
    btn('peer').click();
    expect(t.value()).toBe('fin');
    const ev = new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true });
    btn('fin').dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(true);
    expect(t.value()).toBe('report');
    expect(document.activeElement).toBe(btn('report'));
    btn('report').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }));
    expect(t.value()).toBe('overview'); // 끝에서 돈다(비활성 '피어' 건너뜀)
    btn('overview').dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true, cancelable: true }));
    expect(t.value()).toBe('report');
    btn('report').dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true, cancelable: true }));
    expect(t.value()).toBe('overview');
    const other = new KeyboardEvent('keydown', { key: 'a', bubbles: true, cancelable: true });
    btn('overview').dispatchEvent(other);
    expect(other.defaultPrevented).toBe(false);
    expect(changed).toEqual(['fin', 'report', 'overview', 'report', 'overview']);
  });

  it('처음 보일 때 한 번만 채운다(동적 불러오기용), 실패는 그 탭 안 오류 줄', async () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const shown: string[] = [];
    const t = tabs({
      label: '탭',
      tabs: TABS,
      value: 'fin',
      onFirstShow: async (id, panel) => {
        shown.push(id);
        if (id === 'report') throw new Error('탭 고장');
        panel.append(h('p', null, `${id} 내용`));
      },
    });
    document.body.append(t.el);
    t.select('overview');
    t.select('fin');
    t.select('report');
    t.select('overview');
    await t.settled();
    expect(shown).toEqual(['fin', 'overview', 'report']);
    expect(t.panel('fin').textContent).toBe('fin 내용');
    expect(t.panel('report').querySelector('.msg.err')?.textContent).toBe('이 탭을 그리지 못했습니다');
    expect(t.panel('overview').textContent).toBe('overview 내용');
    expect(err).toHaveBeenCalled();
  });

  it('고를 수 있는 탭이 없으면 바로 오류(조용히 빈 화면 금지)', () => {
    expect(() => tabs({ label: '탭', tabs: [{ id: 'a', label: 'A', disabled: true }] })).toThrow('탭');
  });
});

// ── 밸류 밴드 ─────────────────────────────────────────────────

describe('밸류 밴드', () => {
  it('밴드가 = 배수 × 주당 기준값, 기준값 0 이하·없음은 null', () => {
    expect(bandPrices([1000, null, -50, 0, 2000], [5, 10])).toEqual([
      [5000, null, null, null, 10000],
      [10000, null, null, null, 20000],
    ]);
    expect(bandPrices([1000], [null])).toEqual([[null]]);
    expect(bandPrices([], [5])).toEqual([[]]);
  });

  it('가격선 + 다섯 줄, 빈 값은 선을 끊는다', () => {
    const dates = ['2026-01-02', '2026-01-05', '2026-01-06', '2026-01-07'];
    const bands = bandPrices([1000, 1000, null, 1100], [6, 8, 10, 12, 14]).map((values, k) => ({
      label: ['P10', 'P25', 'P50', 'P75', 'P90'][k]!,
      values,
      multiple: [6, 8, 10, 12, 14][k],
    }));
    const svg = bandChart({ label: 'PER 밴드', dates, price: [9000, null, 9500, 9800], bands });
    expect(svg.getAttribute('role')).toBe('img');
    expect(svg.querySelectorAll('g[data-band]')).toHaveLength(6);
    // 가격: [9000] [9500, 9800] 두 구간, 밴드마다 [0,1] [3] 두 구간
    expect(svg.querySelectorAll('g[data-band="price"] polyline')).toHaveLength(2);
    expect(svg.querySelectorAll('g[data-band="P50"] polyline')).toHaveLength(2);
    expect(svg.querySelector('g[data-band="P50"] text')?.textContent).toBe('P50 10.0x');
    expect(svg.querySelector('g[data-band="P50"] text')?.getAttribute('class')).toBe('fg');
    expect(svg.textContent).toContain('2026-01-02 ~ 2026-01-07');
    expectClean(svg);
  });

  it('빈 배열·전부 null·음수·한 점에도 예외·NaN 없음', () => {
    const empty = bandChart({ label: '빈 밴드', dates: [], price: [], bands: [] });
    expect(empty.dataset.empty).toBe('true');
    expect(empty.textContent).toContain('데이터 없음');
    const nulls = bandChart({
      label: '빈 값',
      dates: ['2026-01-02', '2026-01-05'],
      price: [null, Number.NaN],
      bands: [{ label: 'P50', values: [null, Number.POSITIVE_INFINITY] }],
    });
    expect(nulls.dataset.empty).toBe('true');
    const one = bandChart({ label: '한 점', dates: ['2026-01-02'], price: [-5], bands: [{ label: 'P50', values: [-5] }] });
    for (const svg of [empty, nulls, one]) expectClean(svg);
  });
});

// ── 워터폴 ───────────────────────────────────────────────────

describe('워터폴(브릿지)', () => {
  const steps = [
    { label: '2025 영업이익', value: 100e8, kind: 'total' as const },
    { label: '매출 증가', value: 40e8 },
    { label: '원가 증가', value: -25e8 },
    { label: '설명 안 된 차이', value: 3e8, residual: true },
    { label: '2026 영업이익', value: 118e8, kind: 'total' as const },
  ];

  it('막대 위치: total 은 0 부터, delta 는 누계에서', () => {
    expect(waterfallBars(steps).map((b) => [b.from, b.to])).toEqual([
      [0, 100e8],
      [100e8, 140e8],
      [140e8, 115e8],
      [115e8, 118e8],
      [0, 118e8],
    ]);
  });

  it('값이 없는 단계는 막대 없이 — 그 뒤 누계는 다음 total 까지 broken', () => {
    const bars = waterfallBars([
      { label: '시작', value: 10, kind: 'total' },
      { label: '모름', value: null },
      { label: '증가', value: 5 },
      { label: '끝', value: 20, kind: 'total' },
    ]);
    expect(bars.map((b) => b.broken)).toEqual([false, true, true, false]);
    expect(bars[1]).toMatchObject({ from: null, to: null });
  });

  it('그리기: 증감 색·잔차 강조·합계 부호 없음, 값 없음은 —', () => {
    const svg = waterfall({ label: '영업이익 브릿지', steps: [...steps, { label: '빈 칸', value: null }] });
    const rects = [...svg.querySelectorAll('rect')];
    expect(rects.map((r) => r.getAttribute('fill'))).toEqual(['var(--accent)', 'var(--up)', 'var(--down)', 'var(--warn)', 'var(--accent)']);
    const res = svg.querySelector('g[data-residual="true"]');
    expect(res?.textContent).toContain('설명 안 된 차이 (잔차)');
    expect(res?.querySelector('rect')?.getAttribute('stroke-dasharray')).toBe('3 2');
    const values = [...svg.querySelectorAll('g[data-step] text:last-of-type')].map((t) => t.textContent);
    expect(values).toEqual(['100억', '+40억', '-25억', '+3억', '118억', '—']);
    expectClean(svg);
  });

  it('빈 배열·전부 null·전부 음수에도 예외·NaN 없음', () => {
    const empty = waterfall({ label: '빈', steps: [] });
    expect(empty.textContent).toContain('데이터 없음');
    const nulls = waterfall({ label: '빈 값', steps: [{ label: 'a', value: null, kind: 'total' }, { label: 'b', value: Number.NaN }] });
    const neg = waterfall({ label: '음수', steps: [{ label: '적자', value: -5e8, kind: 'total' }, { label: '악화', value: -2e8 }] });
    const zero = waterfall({ label: '0', steps: [{ label: '0', value: 0, kind: 'total' }] });
    for (const svg of [empty, nulls, neg, zero]) expectClean(svg);
  });
});

// ── 누적 막대 ─────────────────────────────────────────────────

describe('누적 막대(비용 구성)', () => {
  const series = [
    { label: '원재료', values: [60, 70, null] },
    { label: '인건비', values: [30, 20, 10] },
    { label: '기타', values: [10, -5, 0] },
  ];

  it('abs: 양수는 위로, 음수는 아래로 쌓고 축은 둘 다 담는다', () => {
    const lay = stackLayout(3, series, 'abs');
    expect(lay.lo).toBe(-5);
    expect(lay.hi).toBe(100);
    const c1 = lay.segs.filter((sg) => sg.category === 1).map((sg) => [sg.series, sg.y0, sg.y1]);
    expect(c1).toEqual([
      [0, 0, 70],
      [1, 70, 90],
      [2, -5, 0],
    ]);
    expect(lay.excluded).toBe(0);
  });

  it('pct: 양수 합을 100 으로, 음수 칸은 빼고 센다, 합이 0 인 범주는 비운다', () => {
    const lay = stackLayout(3, series, 'pct');
    const c0 = lay.segs.filter((sg) => sg.category === 0).map((sg) => Math.round(sg.y1 - sg.y0));
    expect(c0).toEqual([60, 30, 10]);
    const c1 = lay.segs.filter((sg) => sg.category === 1);
    expect(c1.reduce((a, sg) => a + (sg.y1 - sg.y0), 0)).toBeCloseTo(100, 9);
    expect(lay.excluded).toBe(1);
    expect(stackLayout(1, [{ label: 'x', values: [0] }], 'pct').segs).toEqual([]);
  });

  it('그리기: 범례(HTML)·조각 title·음수 제외 줄', () => {
    const el = stackBar({ label: '분기 비용 구성', categories: ['1Q', '2Q', '3Q'], series, mode: 'pct' });
    document.body.append(el);
    expect([...el.querySelectorAll('.legend li')].map((li) => li.textContent)).toEqual(['원재료', '인건비', '기타']);
    expect(el.querySelector('.legend .sw')?.getAttribute('aria-hidden')).toBe('true');
    expect(el.querySelector('rect title')?.textContent).toContain('1Q · 원재료 60.0%');
    expect(el.querySelector('.def')?.textContent).toBe('음수 1칸은 100% 비율에서 제외');
    expectClean(el);
  });

  it('빈 배열·전부 null·전부 음수에도 예외·NaN 없음', async () => {
    const a = stackBar({ label: '빈', categories: [], series: [] });
    const b = stackBar({ label: '빈 값', categories: ['1Q'], series: [{ label: 'x', values: [null] }] });
    const c = stackBar({ label: '음수', categories: ['1Q'], series: [{ label: 'x', values: [-3] }], mode: 'pct' });
    const d = stackBar({ label: '음수 abs', categories: ['1Q', '2Q'], series: [{ label: 'x', values: [-3, -1] }] });
    for (const el of [a, b, c, d]) {
      document.body.append(el);
      expectClean(el);
    }
    expect(a.querySelector('svg')?.dataset.empty).toBe('true');
    expect(await serious(document.body)).toEqual([]);
  });
});

// ── 타임라인 ─────────────────────────────────────────────────

describe('타임라인(오버행 청구 기간·추정 보호예수)', () => {
  it('날짜 수: 형식·달력 검사', () => {
    expect(dayNumber('1970-01-02')).toBe(1);
    expect(dayNumber('2026-02-30')).toBeNull();
    expect(dayNumber('2026-1-5')).toBeNull();
    expect(dayNumber(null)).toBeNull();
  });

  it('구간 막대·한 날짜 점·날짜 없음 줄, 추정은 점선 + "추정", 오늘 선', () => {
    const svg = timeline({
      label: 'CB·BW 청구 기간',
      today: '2026-10-08',
      items: [
        { label: '1회 CB', start: '2026-03-01', end: '2027-02-28', kind: 'CB' },
        { label: '보호예수(추정)', start: null, end: '2027-04-01', estimated: true, kind: 'lockup' },
        { label: '2회 BW', start: null, end: null, kind: 'BW' },
        { label: '거꾸로', start: '2027-01-10', end: '2026-12-01' },
      ],
    });
    expect(svg.querySelectorAll('g[data-item] rect')).toHaveLength(2);
    expect(svg.querySelectorAll('g[data-item] polygon')).toHaveLength(1);
    const est = svg.querySelector('g[data-estimated="true"]');
    expect(est?.querySelector('polygon')?.getAttribute('stroke-dasharray')).toBe('2 2');
    expect(est?.textContent).toContain('추정');
    expect(svg.querySelector('g[data-kind="BW"]')?.textContent).toContain('날짜 없음');
    expect(svg.querySelector('line[data-today="2026-10-08"]')).not.toBeNull();
    const rev = svg.querySelector('g[data-item="3"] rect');
    expect(Number(rev?.getAttribute('width'))).toBeGreaterThan(0);
    expectClean(svg);
  });

  it('오늘은 Date 로 받아도 KST 날짜로', () => {
    const svg = timeline({ label: 't', today: new Date('2026-10-07T16:00:00Z'), items: [{ label: 'a', start: '2026-10-01', end: '2026-10-20' }] });
    expect(svg.querySelector('line[data-today]')?.getAttribute('data-today')).toBe('2026-10-08');
  });

  it('빈 배열·날짜 전부 없음·잘못된 날짜·한 날짜만에도 예외·NaN 없음', () => {
    const a = timeline({ label: '빈', items: [] });
    expect(a.textContent).toContain('항목 없음');
    const b = timeline({ label: '없음', items: [{ label: 'x', start: null, end: 'bad' }] });
    expect(b.textContent).toContain('날짜 없음');
    const c = timeline({ label: '한 날', items: [{ label: 'x', start: '2026-10-08', end: '2026-10-08' }] });
    for (const svg of [a, b, c]) expectClean(svg);
  });
});

// ── 키-값 ────────────────────────────────────────────────────

describe('키-값 표', () => {
  it('값·없음(—)·적용 불가 사유·자물쇠·품질(추정/지연/검산 실패)', () => {
    const dl = kv([
      { label: 'PER', value: '12.3x', cls: 'up' },
      { label: 'PBR', value: null },
      { label: 'EV/EBITDA', na: '금융업 — EV/EBITDA 해당 없음' },
      { label: '목표가 업사이드', locked: true },
      { label: '보호예수 해제', value: '2027-04-01', quality: 'estimated' },
      { label: '시총', value: '1.2조', quality: 'stale', asOf: '2026-10-07T06:30:00Z' },
      { label: '잔차', value: '3', quality: 'invalid' },
      ['오버행 비율', '4.2%'],
      { label: 'ROE', value: '8.1%', hint: 'TTM 지배' },
    ]);
    document.body.append(dl);
    const dd = (k: string) => dl.querySelector(`div[data-key="${k}"] dd`);
    expect(dd('PER')?.textContent).toBe('12.3x');
    expect(dd('PER')?.querySelector('.up')).not.toBeNull();
    expect(dd('PBR')?.textContent).toBe('—');
    expect(dd('EV/EBITDA')?.className).toBe('na');
    expect(dd('EV/EBITDA')?.textContent).toBe('금융업 — EV/EBITDA 해당 없음');
    expect(dd('목표가 업사이드')?.textContent).toBe('로그인');
    expect(dd('보호예수 해제')?.textContent).toBe('2027-04-01추정');
    expect(dd('시총')?.textContent).toBe('1.2조기준 15:30');
    expect(dd('잔차')?.textContent).toBe('검산 실패');
    expect(dd('오버행 비율')?.textContent).toBe('4.2%');
    expect(dl.querySelector('div[data-key="ROE"] dt small')?.textContent).toBe('TTM 지배');
    expectClean(dl);
  });

  it('빈 목록은 "해당 없음" 한 줄, rows 배치', async () => {
    const dl = kv([], { label: '핵심 지표' });
    expect(dl.textContent).toBe('핵심 지표해당 없음');
    const rows = kv([['a', '1']], { layout: 'rows' });
    expect(rows.className).toBe('kv rows');
    document.body.append(dl, rows);
    expect(await serious(document.body)).toEqual([]);
  });
});

describe('품질 배지 — P4 추정', () => {
  it("estimated 의 기본 글자는 '잠정'(P3), P4 추정값은 EST_LABEL('추정')", () => {
    const a = h('span', null, '1');
    markQuality(a, 'estimated');
    expect(a.querySelector('.q-badge')?.textContent).toBe('잠정');
    const b = qValue('2027-04-01', 'estimated', null, undefined, EST_LABEL);
    expect(b?.querySelector('.q-badge')?.textContent).toBe('추정');
    expect(estBadge().textContent).toBe('추정');
    expect(estBadge().getAttribute('title')).toContain('규칙으로 계산');
    expect(qValue('x', 'invalid')).toBeNull();
  });
});
