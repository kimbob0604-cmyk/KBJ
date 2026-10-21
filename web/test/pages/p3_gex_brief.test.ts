// 페이지 3 'GEX 브리핑'(2026-10-21 요청): 감마 국면 칩·머리 문장·레벨표(뜻 줄)·행사가별 콜/풋/순감마 막대·옵션
// 포지셔닝 카드(손익분기·최대 손익·시나리오). 로그인만 — 공개판은 자물쇠, 요청 0. 404 는 .msg.
import { describe, expect, it } from 'vitest';
import { mountGex } from '../../src/pages/p3/gex';
import { BRIEF_TITLE, briefView, money, pt, strikeBars, type GexBriefing } from '../../src/pages/p3/gex_brief';
import { json } from '../helpers';
import { GEX_NOW, gexFixture, gexHarness } from '../fixtures/api/gex/harness';
import { flush, pageCtx, panelByTitle, text } from './support';

const B = gexFixture<{ data: GexBriefing }>('briefing_all').data;

async function mount(tier: 'login' | 'public', over: Record<string, (() => Response) | undefined> = {}) {
  const hz = gexHarness(over);
  const { ctx, root } = pageCtx(3, tier, hz.source, { now: () => GEX_NOW });
  await mountGex(ctx);
  await flush();
  return { hz, root };
}

describe('GEX 브리핑 — 화면', () => {
  it('국면 칩·머리 문장·레벨 줄(뜻)·막대·전략 카드를 그린다', async () => {
    const { root, hz } = await mount('login');
    const p = panelByTitle(root, BRIEF_TITLE);
    const t = text(p);
    expect(B.regime_label).toBeTruthy();
    expect(t).toContain(B.regime_label ?? '');
    expect(t).toContain(B.headline ?? '');
    expect(p.querySelectorAll('.gb-lv')).toHaveLength(B.levels.length);
    expect(t).toContain('막히면');
    expect(p.querySelector('svg.gb-bars')).not.toBeNull();
    expect(p.querySelectorAll('.gb-card')).toHaveLength(B.strategies.length);
    expect(t).toContain('손익분기');
    expect(hz.paths()).toContain('gex/briefing?scope=all');
  });

  it('공개판은 자물쇠 — 브리핑을 부르지 않는다', async () => {
    const { root, hz } = await mount('public');
    expect(panelByTitle(root, BRIEF_TITLE).classList.contains('locked')).toBe(true);
    expect(hz.paths().filter((x) => x.startsWith('gex/briefing'))).toEqual([]);
  });

  it('아직 없음(404)은 브리핑 패널 안 .msg 로', async () => {
    const nd = () => json({ code: 'no_data', message: '아직 없음 — gex.engine' }, 404);
    const { root } = await mount('login', { 'gex/briefing': nd });
    const p = panelByTitle(root, BRIEF_TITLE);
    expect(p.querySelector('.msg')).not.toBeNull();
    expect(p.querySelector('.msg.err')).toBeNull();
  });
});

describe('GEX 브리핑 — 부품', () => {
  it('money·pt 형식', () => {
    expect([money(5.6e12), money(-5857e8), money(null)]).toEqual(['+5.6조', '-5,857억', '—']);
    expect([pt(892.5), pt(890), pt(undefined)]).toEqual(['892.5', '890', '—']);
  });

  it('막대는 행사가마다 콜·풋·순 세 개, 콜월·풋월 꼬리표', () => {
    const svg = strikeBars(B.strikes, B);
    expect(svg.querySelectorAll('rect')).toHaveLength(B.strikes.length * 3);
    const tags = [...svg.querySelectorAll('text.gb-tag')].map((x) => x.textContent);
    expect(tags.some((x) => x.startsWith('CW'))).toBe(true);
    expect(tags.some((x) => x.startsWith('PW'))).toBe(true);
  });

  it('전략이 없으면 카드 구역을 그리지 않는다', () => {
    const el = briefView({ ...B, strategies: [] });
    expect(el.querySelector('.gb-strats')).toBeNull();
    expect(el.querySelector('.gb-levels')).not.toBeNull();
  });
});
