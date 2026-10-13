// 완료 시험(프런트) — "종목 상세가 관심 종목 20개에서 오류 없이 나온다"(docs/p4_design.md §0.3·§6.5·§8.4).
//
// 합성 세계 p4_world 20종목에 P4 처리기를 돌려 A 가 만든 응답(test/fixtures/api/stock/<코드>/<섹션>.json, 상태 표
// stock/_index.json)으로 페이지 8 을 그린다 — 20종목 × 5탭:
//   console.error 0, DOM(글자·속성)에 NaN·undefined·Infinity·null 0, 패널 오류 줄(.msg.err) 0,
//   404 no_data 섹션은 그 패널에 서버 안내 문구, 적용 불가(applicable=false)는 사유 문구, axe 심각·치명 위반 0.
// 그리고 한 섹션이 500 이어도 그 패널만 오류 줄 — 다른 패널은 그린다(섹션마다).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { route } from '../../src/app/route';
import { type NaReason, naText } from '../../src/pages/p8/common';
import page, { settled, TABS } from '../../src/pages/p8_stock';
import { json } from '../helpers';
import { panelByTitle, text } from '../pages/support';
import { CODES, forbiddenHits, p4ApiHarness, p4PageCtx, type P4Override, SECTIONS, seriousA11y, STATUS, stockFixture } from './support_p4';

/** 섹션 라우트 → 그 섹션을 그리는 패널 제목 */
const PANEL_OF: Record<(typeof SECTIONS)[number], string> = {
  chart: '가격 차트',
  financials: '분기·연간 재무',
  report: 'dart-report',
  segments: '부문 매출',
  consensus: '컨센서스',
  valuation: '밸류에이션 밴드',
  scenario: '시나리오 적정가',
  overhang: '오버행',
  insiders: '내부자',
  disclosures: '공시 목록',
  earnings: '잠정실적·실적 일정',
};

/** 같은 섹션 응답을 그리는 다른 패널(비율 표는 분기 재무 응답의 ratios) — 그 섹션이 실패하면 함께 오류 줄 */
const ALSO: Partial<Record<(typeof SECTIONS)[number], readonly string[]>> = { financials: ['재무 비율'] };

interface Body {
  message?: string;
  data?: { applicable?: boolean; na_reason?: NaReason | null };
}

async function mountAll(code: string, over: Record<string, P4Override> = {}) {
  route.setCode(code);
  const hz = p4ApiHarness(over);
  const { ctx, root } = p4PageCtx(8, 'login', hz.source);
  await page.mount(ctx);
  for (const t of TABS) {
    root.querySelector<HTMLButtonElement>(`button[role="tab"][data-tab="${t.id}"]`)?.click();
    await settled(root);
  }
  return { hz, root };
}

let err: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  route.reset();
  err = vi.spyOn(console, 'error');
});
afterEach(() => {
  route.reset();
});

describe('완료 시험 — 관심종목 20개 × 5탭(로그인)', () => {
  it('합성 세계는 20종목, 섹션 상태 표가 모든 섹션을 덮는다', () => {
    expect(CODES).toHaveLength(20);
    for (const c of CODES) for (const s of [...SECTIONS, 'header', 'flows']) expect(STATUS[c]?.[s], `${c} ${s}`).toBeDefined();
    // 기대 행렬에는 200 과 404 no_data 만 — 500 은 0(§0.3)
    const all = CODES.flatMap((c) => Object.values(STATUS[c] ?? {}));
    expect(all.every((s) => s === 200 || s === 404)).toBe(true);
  });

  it.each(CODES)(
    '%s — 오류 없이 5탭, 404 는 서버 안내, 적용 불가는 사유, 금지 낱말·axe 심각 위반 0',
    async (code) => {
      const { hz, root } = await mountAll(code);
      const st = STATUS[code] ?? {};
      expect(root.querySelector('[role="tablist"]')).not.toBeNull();
      // 모든 탭 패널이 그려졌다(빈 탭 0)
      for (const p of root.querySelectorAll('[role="tabpanel"]')) expect(p.querySelectorAll('section.panel').length).toBeGreaterThan(0);
      for (const s of SECTIONS) {
        const p = panelByTitle(root, PANEL_OF[s]);
        const fx = stockFixture<Body>(code, s);
        expect(p.querySelector('.msg.err'), `${code} ${s} 오류 줄`).toBeNull();
        if (st[s] === 404) {
          expect(fx.message, `${code} ${s} 404 본문`).toBeTruthy();
          expect(text(p), `${code} ${s} 404 문구`).toContain(fx.message ?? '');
        } else if (fx.data?.applicable === false && fx.data.na_reason) {
          expect(text(p), `${code} ${s} 사유`).toContain(naText(fx.data.na_reason));
        }
      }
      // 머리: 이름·코드, 신고가 3축 이름(60일·252일 표기 없음)
      const head = text(panelByTitle(root, '종목'));
      expect(head).toContain(code);
      expect(head).toContain('신고가 120일');
      expect(head).not.toMatch(/60일|252일/);
      expect(text(root)).not.toContain('TAM');
      expect(root.querySelectorAll('.msg.err')).toHaveLength(0);
      expect(forbiddenHits(root)).toEqual([]);
      expect(err).not.toHaveBeenCalled();
      // 공개 파일·외부 주소를 부르지 않는다 — 같은 출처 API 만
      expect(hz.calls.every((c) => c.url.startsWith('/api/'))).toBe(true);
      // 접근성: 숨은 탭까지 한 번에 보게 펼친 뒤 axe(색 대비는 jsdom 이 못 셈 — P3 와 같음)
      for (const p of root.querySelectorAll<HTMLElement>('[role="tabpanel"]')) p.hidden = false;
      expect(await seriousA11y(root)).toEqual([]);
    },
    30_000,
  );
});

describe('완료 시험 — 한 섹션 실패가 다른 패널을 멈추지 않는다', () => {
  it.each(SECTIONS)(
    '994010 의 %s 가 500 이어도 그 패널만 오류 줄',
    async (s) => {
      const { root } = await mountAll('994010', { [`stock/994010/${s}`]: () => json({ detail: 'boom' }, 500) });
      const bad = panelByTitle(root, PANEL_OF[s]);
      expect(text(bad.querySelector('.msg.err'))).toContain('HTTP 500');
      const also = ALSO[s] ?? [];
      for (const t of also) expect(text(panelByTitle(root, t).querySelector('.msg.err'))).toContain('HTTP 500');
      for (const other of SECTIONS) {
        if (other === s) continue;
        // 개요의 핵심 지표 카드·최근 공시는 원천마다 따로 받아(allSettled) 오류 줄 없이 그 칸만 비운다
        expect(panelByTitle(root, PANEL_OF[other]).querySelector('.msg.err'), `${s} 실패 → ${other}`).toBeNull();
      }
      expect(panelByTitle(root, '종목').querySelector('.msg.err')).toBeNull();
      expect(root.querySelectorAll('.msg.err')).toHaveLength(1 + also.length);
      expect(forbiddenHits(root)).toEqual([]);
      expect(err).not.toHaveBeenCalled();
    },
    30_000,
  );

  it('머리가 500 이어도 탭은 그린다(머리 패널만 오류 줄)', async () => {
    const { root } = await mountAll('994010', { 'stock/994010/header': () => json({}, 500) });
    expect(panelByTitle(root, '종목').querySelector('.msg.err')).not.toBeNull();
    expect(root.querySelector('[role="tablist"]')).not.toBeNull();
    expect(panelByTitle(root, '분기·연간 재무').querySelector('table')).not.toBeNull();
  });
});
