// P7 공용 부품(ui/gauge·ladder·profile — docs/p7_design.md D-P7-18)과 페이지 3 자리표시 진입(p3_diag.ts).
// 빈 입력·null·음수 막대·범위 밖 값·좁은 폭(가로 넘침 없음 — viewBox 만, width 속성 없음)을 본다.
import { describe, expect, it } from 'vitest';
import type { DataSource } from '../src/data/source';
import p3, { P3_SECTIONS } from '../src/pages/p3_diag';
import { SHADOW_TEXT } from '../src/pages/p3/gex';
import { type GaugeBand, gauge, gaugeLayout } from '../src/ui/gauge';
import { LABEL_GAP, ladder, ladderLayout, spreadLabels } from '../src/ui/ladder';
import { profile, profileLayout } from '../src/ui/profile';
import { pageCtx, panelByTitle } from './pages/support';

const BANDS: GaugeBand[] = [
  { label: '극단적 공포', tone: 'down' },
  { label: '공포', tone: 'down' },
  { label: '중립', tone: 'dim' },
  { label: '탐욕', tone: 'up' },
  { label: '극단적 탐욕', tone: 'up' },
];
const EDGES = [25, 45, 56, 76];

describe('gauge — 0~100 구간 막대', () => {
  it('구간 경계 값은 위 칸에 든다, 값 위치는 비율', () => {
    expect(gaugeLayout(24.9, EDGES, BANDS, 100).index).toBe(0);
    expect(gaugeLayout(25, EDGES, BANDS, 100).index).toBe(1);
    expect(gaugeLayout(100, EDGES, BANDS, 100).index).toBe(4);
    const lay = gaugeLayout(50, EDGES, BANDS, 200);
    expect(lay.marker).toBe(100);
    expect(lay.cells.map((c) => Math.round(c.width))).toEqual([50, 40, 22, 40, 48]);
  });

  it('값 없음(null·NaN)은 표시를 그리지 않는다 — 0 으로 그리지 않음', () => {
    for (const v of [null, Number.NaN, undefined]) {
      const lay = gaugeLayout(v, EDGES, BANDS, 100);
      expect(lay.marker).toBeNull();
      expect(lay.index).toBeNull();
    }
    const el = gauge({ label: '공포탐욕', value: null, edges: EDGES, bands: BANDS });
    expect(el.textContent).toContain('값 없음');
    expect(el.querySelector('line')).toBeNull();
  });

  it('범위 밖 값·틀린 경계는 오류(조용히 자르지 않는다)', () => {
    expect(() => gaugeLayout(101, EDGES, BANDS, 100)).toThrow();
    expect(() => gaugeLayout(-1, EDGES, BANDS, 100)).toThrow();
    expect(() => gaugeLayout(10, [45, 25, 56, 76], BANDS, 100)).toThrow();
    expect(() => gaugeLayout(10, [25, 45], BANDS, 100)).toThrow();
  });

  it('좁은 폭 — viewBox 만(width 속성 없음), 구간 이름이 글로', () => {
    const el = gauge({ label: '공포탐욕', value: 60, edges: EDGES, bands: BANDS });
    const svg = el.querySelector('svg');
    expect(svg?.getAttribute('viewBox')).toBe('0 0 300 28');
    expect(svg?.hasAttribute('width')).toBe(false);
    expect(el.textContent).toContain('탐욕');
  });
});

describe('ladder — 가격 사다리', () => {
  it('높은 값이 위, 같은 값은 이름을 합친다, 없는 값은 아래 줄', () => {
    const lay = ladderLayout(
      [
        { name: '현재가', value: 350 },
        { name: 'Flip', value: 345 },
        { name: '콜월', value: 360 },
        { name: '풋월', value: 345 },
        { name: '+1σ', value: null },
      ],
      100,
    );
    expect(lay.rows.map((r) => r.value)).toEqual([360, 350, 345]);
    expect(lay.rows[2]?.names).toEqual(['Flip', '풋월']);
    expect(lay.rows[0]?.y).toBeLessThan(lay.rows[2]?.y ?? 0);
    expect(lay.missing).toEqual(['+1σ']);
  });

  it('빈 입력도 그린다(단 없음 + 없음 줄), 값 하나면 가운데', () => {
    const empty = ladder('GEX 사다리', [{ name: 'Flip', value: null }]);
    expect(empty.querySelectorAll('svg line').length).toBe(1); // 세로 축만
    expect(empty.textContent).toContain('없음: Flip');
    expect(ladderLayout([{ name: '현재가', value: 1 }], 100).rows[0]?.y).toBe(50);
    expect(empty.querySelector('svg')?.hasAttribute('width')).toBe(false);
  });

  it('가까운 값의 글자는 글자 높이만큼 벌린다 — 눈금은 참 위치, 순서 그대로(2026-10-22 화면: 풋월·Flip·현재가 겹침)', () => {
    const rungs = [
      { name: '현재가', value: 868.66 },
      { name: 'Flip', value: 870.4 },
      { name: '콜월', value: 885 },
      { name: '풋월', value: 870 },
      { name: '+1σ', value: 883.1 },
      { name: '−1σ', value: 854.22 },
    ];
    const lay = ladderLayout(rungs, 180);
    expect(lay.rows.map((r) => r.names[0])).toEqual(['콜월', '+1σ', 'Flip', '풋월', '현재가', '−1σ']);
    const ys = lay.rows.map((r) => r.labelY);
    for (let i = 1; i < ys.length; i += 1) expect((ys[i] ?? 0) - (ys[i - 1] ?? 0)).toBeGreaterThanOrEqual(LABEL_GAP - 1e-9);
    for (const y of ys) {
      expect(y).toBeGreaterThanOrEqual(12 - 1e-9);
      expect(y).toBeLessThanOrEqual(168 + 1e-9);
    }
    // 눈금(참 위치)은 값 비율 그대로 — 콜월 맨 위, −1σ 맨 아래
    expect(lay.rows[0]?.y).toBeCloseTo(12, 6);
    expect(lay.rows.at(-1)?.y).toBeCloseTo(168, 6);
    // 떨어진 단(−1σ)은 옮기지 않는다
    expect(lay.rows.at(-1)?.labelY).toBeCloseTo(168, 6);
    // 그린 결과 — 옮긴 단만 이음선(양쪽 둘씩), 글자 수는 단마다 둘
    const el = ladder('GEX 사다리', rungs);
    const moved = lay.rows.filter((r) => Math.abs(r.labelY - r.y) > 0.5).length;
    expect(moved).toBeGreaterThan(0);
    expect(el.querySelectorAll('line.ladder-lead').length).toBe(moved * 2);
    expect(el.querySelectorAll('svg text').length).toBe(rungs.length * 2);
  });

  it('겹친 무리는 참 위치 평균에 모은다(한쪽으로만 밀지 않는다), 칸이 모자라면 위부터 순서대로', () => {
    expect(spreadLabels([50, 50, 50], 10, 0, 100)).toEqual([40, 50, 60]);
    expect(spreadLabels([10, 40, 90], 10, 0, 100)).toEqual([10, 40, 90]);
    expect(spreadLabels([0, 1], 10, 0, 100)).toEqual([0, 10]); // 위 끝 — 아래로
    expect(spreadLabels([99, 100], 10, 0, 100)).toEqual([90, 100]); // 아래 끝 — 위로
    expect(spreadLabels([5, 5, 5, 5], 10, 0, 20)).toEqual([0, 10, 20, 30]); // 칸 부족 — 순서·간격은 지킨다
    expect(spreadLabels([], 10, 0, 100)).toEqual([]);
  });
});

describe('profile — 행사가별 GEX', () => {
  const bars = [
    { strike: 340, value: -2e9 },
    { strike: 345, value: -1e9 },
    { strike: 350, value: 0 },
    { strike: 355, value: 3e9 },
    { strike: 360, value: null },
  ];

  it('음수 막대는 0 아래(down), 양수는 위(up), 없는 값은 빠지고 수를 센다', () => {
    const lay = profileLayout({ bars, price: 352.5, flip: 347.5 }, 200, 100);
    expect(lay.rects.map((r) => r.sign)).toEqual(['down', 'down', 'flat', 'up']);
    const neg = lay.rects[0];
    const pos = lay.rects[3];
    expect((neg?.y ?? 0) >= lay.zeroY - 0.01).toBe(true);
    expect((pos?.y ?? 0) + (pos?.height ?? 0)).toBeCloseTo(lay.zeroY, 5);
    expect(lay.missing).toBe(1);
    expect(lay.flipX).not.toBeNull();
    expect((lay.flipX ?? 0) < (lay.priceX ?? 0)).toBe(true);
  });

  it('범위 밖 현재가·Flip 은 선을 그리지 않는다, 빈 입력도 그린다', () => {
    const lay = profileLayout({ bars, price: 500, flip: null }, 200, 100);
    expect(lay.priceX).toBeNull();
    expect(lay.flipX).toBeNull();
    const el = profile({ label: 'GEX 프로필', bars: [] });
    expect(el.querySelectorAll('rect').length).toBe(0);
    const svg = el.querySelector('svg');
    expect(svg?.getAttribute('viewBox')).toBe('0 0 320 160');
    expect(svg?.hasAttribute('width')).toBe(false);
  });

  it('그림 — 막대·현재가·Flip 선·값 없는 행사가 줄', () => {
    const el = profile({ label: 'GEX 프로필', bars, price: 352.5, flip: 347.5 });
    expect(el.querySelectorAll('rect').length).toBe(4);
    expect(el.textContent).toContain('Flip');
    expect(el.textContent).toContain('값 없는 행사가 1개');
  });
});

describe('페이지 3 자리표시(p3_diag.ts) — 패널 순서·등급 갈림', () => {
  const never: DataSource = { tier: 'public', get: () => Promise.reject(new Error('부르지 않는다')) };

  it('순서 고정: 판정 띠 → 공공지표 → 시장 진단 → GEX → 선물 OI(ADR 0043) → 이벤트', () => {
    expect(P3_SECTIONS).toEqual(['verdict', 'public', 'market', 'gex', 'oi', 'events']);
  });

  // 웨이브 2(DIAG)가 본문을 채웠다 — 공개 패널은 열리고(공개 파일을 부른다), 로그인 패널만 자물쇠. 본문 시험은
  // test/pages/p3_diag.test.ts(공개판 요청은 공개 파일 diag/* 뿐).
  it('공개판: 공공지표 진단·이벤트만 열리고 나머지는 자물쇠', async () => {
    const { ctx, root } = pageCtx(3, 'public', never);
    await p3.mount(ctx);
    const titles = [...root.querySelectorAll('section.panel h3 > span:first-child')].map((e) => e.textContent);
    expect(titles).toEqual([
      '종합 판정',
      '국장 공포탐욕',
      '공공지표 진단',
      '공공지표 추이',
      '시장 진단',
      '시장 진단 추이',
      'GEX 브리핑',
      'GEX 프로필',
      'GEX 레벨',
      '확장 지표',
      '선물 미결제약정(OI)',
      '옵션 투자자 일별(콜·풋)',
      '이벤트 달력',
    ]);
    for (const t of ['종합 판정', '국장 공포탐욕', '시장 진단', '시장 진단 추이', 'GEX 브리핑', 'GEX 프로필', 'GEX 레벨', '확장 지표',
      '선물 미결제약정(OI)', '옵션 투자자 일별(콜·풋)']) {
      expect(panelByTitle(root, t).classList.contains('locked'), t).toBe(true);
    }
    for (const t of ['공공지표 진단', '공공지표 추이', '이벤트 달력']) {
      expect(panelByTitle(root, t).classList.contains('locked'), t).toBe(false);
    }
    expect(panelByTitle(root, 'GEX 프로필').textContent).toContain('KIS 옵션 체인 기반');
  });

  it('로그인판: 차트 패널은 반폭(span-6) 이하, 확장 지표 자리는 섀도 문구', async () => {
    const { ctx, root } = pageCtx(3, 'login', { ...never, tier: 'login' });
    await p3.mount(ctx);
    for (const t of ['GEX 프로필', 'GEX 레벨', '국장 공포탐욕', '공공지표 진단', '시장 진단']) {
      expect(panelByTitle(root, t).classList.contains('span-6'), t).toBe(true);
    }
    expect(panelByTitle(root, '확장 지표').textContent).toContain(SHADOW_TEXT);
    expect(root.querySelectorAll('section.locked').length).toBe(0);
  });
});
