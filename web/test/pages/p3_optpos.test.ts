// 페이지 3 옵션 행사가별 추정 포지션 패널(metrics §34, ADR 0044): 맨 위 칸(기준·시리즈·콜 벽·풋 벽·합성 델타·오늘 선물)·
// 콜/풋 행사가별 추정 보유 막대(OI 초과 invalid 행 제외)·오늘 바뀐 행사가 상위·레벨 표. 로그인만 — 공개판은 자물쇠, 요청 0.
// 404 는 '.msg'(작업 이름). 픽스처는 합성 5거래일 세계(tests/fixtures/synthetic/opt_pos_world.py → tests/unit/api/optpos_fixtures.py).
import { describe, expect, it } from 'vitest';
import {
  barRows,
  contracts,
  headSeries,
  LABEL_KO,
  mountOptpos,
  OPTPOS_REFRESH_MS,
  OPTPOS_TITLE,
  type OptionPositions,
} from '../../src/pages/p3/optpos';
import { optposFixture, optposHarness, OPTPOS_NOW } from '../fixtures/api/gex_optpos/harness';
import { json } from '../helpers';
import { flush, pageCtx, panelByTitle, text } from './support';

interface Env<T> {
  data: T;
}

async function mount(tier: 'login' | 'public', over: Record<string, (() => Response) | undefined> = {}) {
  const hz = optposHarness(over);
  const { ctx, root, every } = pageCtx(3, tier, hz.source, { now: () => OPTPOS_NOW });
  await mountOptpos(ctx);
  await flush();
  return { hz, root, every };
}

describe('페이지 3 옵션 행사가별 추정 포지션 — 로그인', () => {
  it('패널 하나(전체 폭) — 외국인 한 번 읽고 5분마다 다시', async () => {
    const { hz, root, every } = await mount('login');
    const p = panelByTitle(root, OPTPOS_TITLE);
    expect(p.className).toContain('span-12');
    expect(hz.paths()).toEqual(['gex/option-positions?investor=frgn']);
    expect(every.map((e) => e.ms)).toEqual([OPTPOS_REFRESH_MS]);
    expect(text(root)).not.toMatch(/NaN|undefined|null/);
  });

  it('맨 위 칸 — 근월물 시리즈·누적 시작·콜 벽 900·풋 벽 850·합성 델타·오늘 선물', async () => {
    const { root } = await mount('login');
    const head = text(panelByTitle(root, OPTPOS_TITLE).querySelector('dl'));
    expect(head).toContain('M:202611');
    expect(head).toContain('10/28 이후 누적');
    expect(head).toContain('900 (-4,900계약 · 유지)');
    expect(head).toContain('850 (-4,000계약 · 유지)');
    expect(head).toContain('-12,277계약');
    expect(head).toContain('+670계약');
  });

  it('막대 둘(콜·풋) — OI 초과(1000 콜) 행은 막대에서 뺀다', async () => {
    const { root } = await mount('login');
    const p = panelByTitle(root, OPTPOS_TITLE);
    expect(p.querySelectorAll('.optpos-charts > div.barschart')).toHaveLength(2);
    const d = optposFixture<Env<OptionPositions>>('option_positions').data;
    const s = headSeries(d);
    expect(s?.label).toBe('M:202611');
    if (!s) return;
    expect(barRows(s, 'C').some((x) => x.strike === 1000)).toBe(false);
    expect(barRows(s, 'C').map((x) => x.strike)).toEqual([...barRows(s, 'C').map((x) => x.strike)].sort((a, b) => a - b));
  });

  it('오늘 바뀐 행사가 상위 — 950·975 콜 +4,000 청산, 레벨 표에 현재 선물', async () => {
    const { root } = await mount('login');
    const p = panelByTitle(root, OPTPOS_TITLE);
    const rows = [...p.querySelectorAll('table')][0]?.querySelectorAll('tbody tr') ?? [];
    expect(text(rows[0])).toContain('950 콜');
    expect(text(rows[0])).toContain('+4,000');
    expect(text(rows[0])).toContain(LABEL_KO.cleared);
    expect(p.querySelector('.optpos-label.cleared')).not.toBeNull();
    const levels = text([...p.querySelectorAll('table')][1]);
    expect(levels).toContain('현재 선물');
    expect(levels).toContain('884.00');
    expect(text(p)).toContain('EWY 원천 없음');
    expect(text(p)).toContain('[확인 필요 — 기본값]');
  });

  it('404 는 작업 이름 안내(.msg), 그 밖 실패는 .msg.err', async () => {
    const empty = optposFixture<{ status: number }>('option_positions_empty');
    const a = await mount('login', { 'gex/option-positions': () => json(empty, 404) });
    const p = panelByTitle(a.root, OPTPOS_TITLE);
    expect(text(p.querySelector('.msg'))).toContain('gex.option_positions 평일 16:05');
    expect(p.querySelector('.msg.err')).toBeNull();
    const b = await mount('login', { 'gex/option-positions': () => json({ code: 'x', message: 'x' }, 500) });
    expect(panelByTitle(b.root, OPTPOS_TITLE).querySelector('.msg.err')).not.toBeNull();
  });
});

describe('페이지 3 옵션 행사가별 추정 포지션 — 공개판', () => {
  it('자물쇠만, 요청 0', async () => {
    const { hz, root } = await mount('public');
    expect(hz.paths()).toEqual([]);
    expect(panelByTitle(root, OPTPOS_TITLE).classList.contains('locked')).toBe(true);
  });
});

describe('형식', () => {
  it('계약 수는 반올림·부호', () => {
    expect(contracts(-4899.6)).toBe('-4,900계약');
    expect(contracts(null)).toBe('—');
  });
});
