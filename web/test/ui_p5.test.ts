// P5 공용 부품 — 선(line)·양음 막대(bars)·트리맵(treemap) (docs/p5_design.md §1.1 ui_p5.test)
// 단언: null 은 잇지 않음(끊김)·선 켜고 끄기·면적 비례·색 범위·빈 입력, 그린 결과에 'NaN'·'undefined'·'Infinity'·'null' 없음,
// SVG 는 viewBox 만(고정 폭 없음 — 모바일 가로 넘침 없음), axe 심각 위반 0.
import axe from 'axe-core';
import { describe, expect, it } from 'vitest';
import { barsChart, barsLayout, MIN_SLOTS } from '../src/ui/bars';
import { lineChart, lineRange, lineRuns } from '../src/ui/line';
import { fitLabel, squarify, textWidth, treeColor, treemap } from '../src/ui/treemap';

const BAD = /\b(?:NaN|undefined|Infinity|null)\b/;

function dump(el: Node): string {
  return new XMLSerializer().serializeToString(el);
}

function expectClean(el: Element): void {
  expect(dump(el)).not.toMatch(BAD);
  const svg = el.querySelector('svg');
  expect(svg?.getAttribute('viewBox')).toMatch(/^0 0 \d+ \d+$/);
  expect(svg?.hasAttribute('width')).toBe(false); // 폭은 CSS 100% — 가로 넘침 없음
}

async function serious(root: Element): Promise<string[]> {
  const res = await axe.run(root, { resultTypes: ['violations'], rules: { 'color-contrast': { enabled: false } } });
  return res.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
}

const X = ['09:00', '09:01', '09:02', '09:03', '09:04', '09:05'];

// ── 선 ─────────────────────────────────────────────────

describe('line: 여러 선·끊긴 구간·끝점 이름표·0 기준선·켜고 끄기', () => {
  it('null 은 잇지 않는다 — 구간마다 path, 점 하나 구간은 원', () => {
    expect(lineRuns([1, 2, null, 4, NaN, 6])).toEqual([
      [
        [0, 1],
        [1, 2],
      ],
      [[3, 4]],
      [[5, 6]],
    ]);
    expect(lineRuns([null, undefined])).toEqual([]);
    const c = lineChart({ label: '개인', x: X, series: [{ label: '개인', values: [1, 2, null, 4, 5, null] }] });
    const g = c.el.querySelector('g[data-series="0"]');
    expect(g?.querySelectorAll('path')).toHaveLength(2);
    expect(g?.querySelectorAll('circle')).toHaveLength(0);
    const one = lineChart({ label: 'x', x: X, series: [{ label: 'a', values: [1, null, 3, null, null, null] }] });
    expect(one.el.querySelectorAll('circle')).toHaveLength(2);
    expect(one.el.querySelectorAll('path')).toHaveLength(0);
    expectClean(c.el);
  });

  it('0 기준선: 범위가 0 을 가로지르거나 zero=true 일 때만', () => {
    expect(lineRange([{ label: 'a', values: [-1, 2] }])).toEqual({ lo: -1, hi: 2, baseline: true });
    expect(lineRange([{ label: 'a', values: [1, 2] }])).toEqual({ lo: 1, hi: 2, baseline: false });
    expect(lineRange([{ label: 'a', values: [1, 2] }], true)).toEqual({ lo: 0, hi: 2, baseline: true });
    expect(lineRange([{ label: 'a', values: [null] }])).toBeNull();
    const flat = lineRange([{ label: 'a', values: [5, 5] }]);
    expect(flat && flat.hi > flat.lo).toBe(true);
    const cross = lineChart({ label: 'x', x: X.slice(0, 2), series: [{ label: 'a', values: [-3, 4] }] });
    expect(cross.el.querySelectorAll('line.zero')).toHaveLength(1);
    const pos = lineChart({ label: 'x', x: X.slice(0, 2), series: [{ label: 'a', values: [3, 4] }] });
    expect(pos.el.querySelectorAll('line.zero')).toHaveLength(0);
  });

  it('끝점 이름표: 선마다 마지막 값 자리에 이름 값(끝이 null 이어도 마지막 값)', () => {
    const c = lineChart({
      label: '투자자',
      x: X,
      series: [
        { label: '외국인', values: [1, 2, 3, 4, 5, 6] },
        { label: '기관', values: [-1, -2, -3, null, null, null] },
      ],
      format: (v) => `${v}억`,
    });
    expect([...c.el.querySelectorAll('text.end')].map((t) => t.textContent)).toEqual(['외국인 6억', '기관 -3억']);
    const none = lineChart({ label: 'x', x: X, series: [{ label: 'a', values: [1, 2, 3, 4, 5, 6] }], endLabels: false });
    expect(none.el.querySelectorAll('text.end')).toHaveLength(0);
  });

  it('켜고 끄기: 범례 버튼(aria-pressed)으로 선을 빼고 범위를 다시 잡는다', () => {
    const c = lineChart({
      label: '투자자',
      x: X.slice(0, 3),
      toggle: true,
      hidden: ['기관'],
      series: [
        { label: '개인', values: [1, 2, 3] },
        { label: '외국인', values: [100, 200, 300] },
        { label: '기관', values: [-5, -6, -7] },
      ],
    });
    const btns = [...c.el.querySelectorAll<HTMLButtonElement>('.legend button')];
    expect(btns.map((b) => b.getAttribute('aria-pressed'))).toEqual(['true', 'true', 'false']);
    expect(c.visible()).toEqual([0, 1]);
    expect(c.el.querySelector('g[data-series="2"]')).toBeNull();
    btns[1]?.click();
    expect(btns[1]?.getAttribute('aria-pressed')).toBe('false');
    expect(c.visible()).toEqual([0]);
    expect(c.el.querySelector('g[data-series="1"]')).toBeNull();
    expect(c.toggle(2)).toBe(true);
    expect(c.el.querySelectorAll('line.zero')).toHaveLength(1); // 개인(+)·기관(-) → 0 을 가로지른다
    c.toggle(0);
    c.toggle(2);
    expect(c.el.querySelector('svg')?.dataset.empty).toBe('true'); // 다 끄면 '데이터 없음'
    expectClean(c.el);
  });

  it('빈 입력·전부 null 에도 예외 없이 데이터 없음', () => {
    for (const c of [
      lineChart({ label: 'x', x: [], series: [] }),
      lineChart({ label: 'x', x: X, series: [{ label: 'a', values: X.map(() => null) }] }),
    ]) {
      expect(c.el.querySelector('svg')?.dataset.empty).toBe('true');
      expect(c.el.textContent).toContain('데이터 없음');
      expectClean(c.el);
    }
  });
});

// ── 양·음 막대 ─────────────────────────────────────────────────

describe('bars: 양·음 막대·막대 위 상세', () => {
  it('양수는 0 위, 음수는 0 아래, null 은 빈 칸(0 으로 그리지 않는다)', () => {
    const lay = barsLayout([10, -5, null, 0], { w: 100, h: 100, top: 0, bottom: 0, left: 0, right: 0 });
    expect(lay.missing).toBe(1);
    expect(lay.rects.map((r) => [r.index, r.sign])).toEqual([
      [0, 'up'],
      [1, 'down'],
      [3, 'flat'],
    ]);
    const [up, down] = lay.rects;
    expect(up!.y + up!.height).toBeCloseTo(lay.zeroY);
    expect(down!.y).toBeCloseTo(lay.zeroY);
    expect(up!.height / down!.height).toBeCloseTo(2); // 높이는 값에 비례
  });

  it('칸이 적으면 막대가 폭을 다 덮지 않고 오른쪽(최근)에 붙는다(하루치 막대 — 2026-10-20)', () => {
    const lay = barsLayout([-1953], { w: 640, h: 200 });
    const [r] = lay.rects;
    expect(r!.width).toBeLessThan(640 / MIN_SLOTS);
    expect(r!.x).toBeGreaterThan(600);
    const many = barsLayout(Array.from({ length: 60 }, (_, i) => i - 30), { w: 640, h: 200 });
    expect(many.rects[0]!.x).toBeLessThan(20); // 칸이 많으면 그대로 폭 전체
  });

  it('막대를 누르거나 초점을 주면 상세 줄에 이름·값·상세', () => {
    const el = barsChart({
      label: '프로그램 순매수(60일)',
      items: [
        { label: '11-12', value: 120, detail: '차익 +20 · 비차익 +100' },
        { label: '11-13', value: -80 },
        { label: '11-14', value: null },
      ],
      format: (v) => `${v > 0 ? '+' : ''}${v}억`,
    });
    const rects = [...el.querySelectorAll<SVGRectElement>('rect')];
    expect(rects.map((r) => r.dataset.sign)).toEqual(['up', 'down']);
    expect(rects[0]?.getAttribute('fill')).toBe('var(--up)');
    expect(rects[1]?.getAttribute('fill')).toBe('var(--down)');
    rects[0]?.dispatchEvent(new MouseEvent('click'));
    expect(el.querySelector('.bars-detail')?.textContent).toBe('11-12 +120억 · 차익 +20 · 비차익 +100');
    expect(rects[0]?.classList.contains('on')).toBe(true);
    rects[1]?.dispatchEvent(new FocusEvent('focus'));
    expect(el.querySelector('.bars-detail')?.textContent).toBe('11-13 -80억');
    expect(rects[0]?.classList.contains('on')).toBe(false);
    expect(el.textContent).toContain('값 없는 1칸은 비움');
    expectClean(el);
  });

  it('빈 입력·전부 null 에도 예외 없이 데이터 없음', () => {
    for (const items of [[], [{ label: 'a', value: null }]]) {
      const el = barsChart({ label: 'x', items });
      expect(el.querySelector('svg')?.dataset.empty).toBe('true');
      expectClean(el);
    }
  });
});

// ── 트리맵 ─────────────────────────────────────────────────

describe('treemap: squarified·면적 비례·색 범위·라벨 줄임', () => {
  const BOX = { x: 0, y: 0, w: 600, h: 400 };

  it('면적은 크기에 비례하고 상자를 겹침 없이 채운다', () => {
    const sizes = [60, 30, 20, 10, 5, 3, 1, null, 0, -4];
    const rects = squarify(sizes, BOX);
    const total = 60 + 30 + 20 + 10 + 5 + 3 + 1;
    sizes.forEach((v, i) => {
      const r = rects[i];
      if (typeof v !== 'number' || v <= 0) {
        expect(r).toBeNull();
        return;
      }
      expect(r).not.toBeNull();
      expect((r!.w * r!.h) / (BOX.w * BOX.h)).toBeCloseTo(v / total, 6);
      expect(r!.x).toBeGreaterThanOrEqual(-1e-9);
      expect(r!.y).toBeGreaterThanOrEqual(-1e-9);
      expect(r!.x + r!.w).toBeLessThanOrEqual(BOX.w + 1e-6);
      expect(r!.y + r!.h).toBeLessThanOrEqual(BOX.h + 1e-6);
    });
    const live = rects.filter((r) => r !== null);
    for (let a = 0; a < live.length; a++) {
      for (let b = a + 1; b < live.length; b++) {
        const p = live[a]!;
        const q = live[b]!;
        const ox = Math.min(p.x + p.w, q.x + q.w) - Math.max(p.x, q.x);
        const oy = Math.min(p.y + p.h, q.y + q.h) - Math.max(p.y, q.y);
        expect(ox <= 1e-6 || oy <= 1e-6, `${a}·${b} 겹침`).toBe(true);
      }
    }
    // squarified: 같은 크기 넷이면 거의 정사각
    const four = squarify([1, 1, 1, 1], { x: 0, y: 0, w: 100, h: 100 });
    for (const r of four) expect(Math.max(r!.w / r!.h, r!.h / r!.w)).toBeLessThan(1.01);
  });

  it('색 범위: 부호로 색, |값|/범위로 진하기, 범위 밖은 끝 색, 없는 값은 회색', () => {
    expect(treeColor(5, 10)).toEqual({ fill: 'var(--up)', opacity: 0.625 });
    expect(treeColor(-10, 10)).toEqual({ fill: 'var(--down)', opacity: 1 });
    expect(treeColor(-30, 10)).toEqual(treeColor(-10, 10));
    expect(treeColor(0, 10).fill).toBe('var(--dim)');
    expect(treeColor(null, 10).fill).toBe('var(--dim)');
    expect(treeColor(3, 0).fill).toBe('var(--dim)');
    const el = treemap({
      label: 'ETF 맵',
      range: 4,
      items: [
        { id: 'a', label: '반도체 레버리지', size: 50, color: 8 },
        { id: 'b', label: '2차전지', size: 30, color: -2 },
        { id: 'c', label: '채권', size: 20, color: null },
      ],
    });
    const op = (id: string) => el.querySelector(`g[data-id="${id}"] rect`)?.getAttribute('fill-opacity');
    expect(op('a')).toBe('1');
    expect(op('b')).toBe('0.625');
    expect(el.querySelector('g[data-id="c"] rect')?.getAttribute('fill')).toBe('var(--dim)');
    expectClean(el);
  });

  it('라벨은 칸 폭에 맞춰 줄이고(…), 아주 작은 칸은 라벨 없이 title 만', () => {
    expect(textWidth('ab', 10)).toBeCloseTo(12);
    expect(textWidth('가나', 10)).toBe(20);
    expect(fitLabel('반도체', 100, 10)).toBe('반도체');
    expect(fitLabel('반도체 레버리지', 40, 10)).toBe('반도체…'); // 30 + 6(…) ≤ 40
    expect(fitLabel('반도체 레버리지', 30, 10)).toBe('반도…');
    expect(fitLabel('반도체', 5, 10)).toBe('');
    const el = treemap({
      label: '테마',
      w: 120,
      h: 100,
      items: [
        { id: 'big', label: '아주 긴 테마 이름 반도체 장비', size: 1000, color: 1 },
        { id: 'tiny', label: '작은 테마', size: 1, color: 1 },
      ],
    });
    const big = el.querySelector('g[data-id="big"] text')?.textContent ?? '';
    expect(big.endsWith('…')).toBe(true);
    expect(el.querySelector('g[data-id="tiny"] text')).toBeNull();
    expect(el.querySelector('g[data-id="tiny"] title')?.textContent).toContain('작은 테마');
  });

  it('빈 입력·크기 전부 없음에도 예외 없이 데이터 없음, 일부 없음은 아래 줄에', () => {
    for (const items of [[], [{ id: 'a', label: 'a', size: null, color: 1 }]]) {
      const el = treemap({ label: 'x', items });
      expect(el.querySelector('svg')?.dataset.empty).toBe('true');
      expectClean(el);
    }
    const part = treemap({
      label: 'x',
      items: [
        { id: 'a', label: 'a', size: 1, color: 1 },
        { id: 'b', label: 'b', size: 0, color: 1 },
      ],
    });
    expect(part.textContent).toContain('크기 없는 1칸은 그리지 않음');
  });
});

describe('접근성', () => {
  it('세 부품 모두 axe 심각 위반 0', async () => {
    const root = document.createElement('main');
    document.body.append(root);
    root.append(
      lineChart({ label: '선', x: X, toggle: true, series: [{ label: 'a', values: [1, 2, null, 4, 5, 6] }] }).el,
      barsChart({ label: '막대', items: [{ label: 'a', value: 1 }] }),
      treemap({ label: '맵', items: [{ id: 'a', label: 'a', size: 1, color: 1 }] }),
    );
    expect(await serious(root)).toEqual([]);
    root.remove();
  });
});
