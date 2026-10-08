// 스킨 5종 대비 — tokens.css 를 읽어 WCAG 2 대비를 계산한다(§6.3).
// 글자: 4.5:1(AA 본문). 잠정 점선 밑줄(--est)은 글자가 아닌 선이라 3:1(비텍스트 대비).
import { describe, expect, it } from 'vitest';
import css from '../src/design/tokens.css?raw';
import { SKINS } from '../src/app/skin';

type Tokens = Record<string, string>;

function parseBlocks(text: string): Map<string, Tokens> {
  const out = new Map<string, Tokens>();
  const clean = text.replace(/\/\*[\s\S]*?\*\//g, '');
  const re = /:root(?:\[data-skin='([a-z]+)'\])?\s*\{([^}]*)\}/g;
  for (const m of clean.matchAll(re)) {
    const vars: Tokens = {};
    for (const d of (m[2] ?? '').matchAll(/--([a-z]+)\s*:\s*([^;]+);/g)) vars[d[1] ?? ''] = (d[2] ?? '').trim();
    out.set(m[1] ?? 'amber', vars);
  }
  return out;
}

function luminance(hex: string): number {
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!m?.[1]) throw new Error(`색 형식: ${hex}`);
  const c = [0, 2, 4].map((i) => parseInt(m[1]!.slice(i, i + 2), 16) / 255);
  const lin = c.map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * lin[0]! + 0.7152 * lin[1]! + 0.0722 * lin[2]!;
}

export function contrast(a: string, b: string): number {
  const x = luminance(a);
  const y = luminance(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

const blocks = parseBlocks(css);
const base = blocks.get('amber') ?? {};
const skin = (id: string): Tokens => ({ ...base, ...(blocks.get(id) ?? {}) });

// [글자색, 바탕색, 최소 대비]
const PAIRS: [string, string, number][] = [
  ['fg', 'bg', 4.5],
  ['fg', 'panel', 4.5],
  ['dim', 'panel', 4.5],
  ['dim', 'bg', 4.5],
  ['accent', 'panel', 4.5], // 패널 제목
  ['accent', 'bg', 4.5], // 브랜드·선택 탭
  ['bg', 'accent', 4.5], // 눌린 버튼 글자
  ['up', 'panel', 4.5],
  ['up', 'bg', 4.5],
  ['down', 'panel', 4.5],
  ['down', 'bg', 4.5],
  ['warn', 'panel', 4.5],
  ['est', 'panel', 3],
];

describe('디자인 토큰', () => {
  it('스킨 다섯 벌이 모두 있다', () => {
    expect([...blocks.keys()].sort()).toEqual(SKINS.map((s) => s.id).sort());
  });

  it('스킨마다 같은 토큰 이름(빠진 색 없음)', () => {
    const names = Object.keys(base)
      .filter((k) => !['mono', 'kr'].includes(k))
      .sort();
    for (const s of SKINS) {
      if (s.id === 'amber') continue;
      expect(Object.keys(blocks.get(s.id) ?? {}).sort(), s.id).toEqual(names);
    }
  });

  for (const s of SKINS) {
    it(`${s.label}: 글자 대비 AA`, () => {
      const t = skin(s.id);
      for (const [fg, bg, min] of PAIRS) {
        const r = contrast(t[fg] ?? '', t[bg] ?? '');
        expect(r, `${s.id} --${fg} / --${bg} = ${r.toFixed(2)}`).toBeGreaterThanOrEqual(min);
      }
    });
  }

  it('상승 적색·하락 청색(국내 관례) — 빨강 성분이 상승에서 더 크다', () => {
    for (const s of SKINS) {
      const t = skin(s.id);
      const red = (hex: string) => parseInt(hex.slice(1, 3), 16);
      const blue = (hex: string) => parseInt(hex.slice(5, 7), 16);
      expect(red(t.up ?? '') > blue(t.up ?? ''), `${s.id} up`).toBe(true);
      expect(blue(t.down ?? '') > red(t.down ?? ''), `${s.id} down`).toBe(true);
    }
  });
});

describe('가로 넘침 방지(구성 CSS)', () => {
  it('.page 는 position: relative — 안의 절대 위치 요소(.sr-only 제목)가 .pages 가로 스크롤 밖으로 빠지지 않게', async () => {
    const { default: components } = await import('../src/design/components.css?raw');
    const clean = components.replace(/\/\*[\s\S]*?\*\//g, '');
    const block = /(^|\n)\.page\s*\{([^}]*)\}/.exec(clean)?.[2] ?? '';
    expect(block).toMatch(/position:\s*relative/);
  });
});
