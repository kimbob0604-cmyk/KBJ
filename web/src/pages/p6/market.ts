// 페이지 6 '시장' 탭 패널(로그인 등급 — Yahoo 12종·FRED 저작권 시리즈·ECOS 타기관 표, D-P5-6). 공개판은 자물쇠(요청 없음).
import { h } from '../../ui/dom';
import { panel, type Panel } from '../../ui/panel';
import { loadTab } from './series';
import type { P6Ctx } from './common';

export const MARKET_TITLE = '시장 탭';
export const MARKET_TAB = 'market';

export function createMarketPanel(c: P6Ctx): { panel: Panel; load: () => Promise<void> } {
  const p = panel(c.tier, {
    title: MARKET_TITLE,
    tier: 'login',
    lock: '저작권·작성기관 제한 시리즈(S&P·VIX·하이일드 스프레드·제한 ECOS 표)',
    span: 12,
  });
  const load = async (): Promise<void> => {
    if (p.locked) return;
    p.clearBody();
    p.setDef('달러·원자재·지수·변동성·BTC(Yahoo — 단위 그대로)·FRED 저작권 시리즈·ECOS 타기관 표. 美10년은 공개판에서 FRED DGS10.');
    const box = h('div');
    p.body.append(box);
    const env = await loadTab(c, MARKET_TAB, box);
    if (env) p.setSource(env);
  };
  return { panel: p, load };
}
