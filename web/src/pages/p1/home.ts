// 첫 화면 불러오기(로그인 전용 — 공개 번들에서 빠진다): `home` 묶음 한 번 → 시장 카드 4 + 신호 카드 12, 관심종목 따로.
//
// - 묶음 요청이 실패하면 묶음 카드 전부에 같은 줄(404 는 서버 안내, 그 밖은 오류) — 관심종목·업종 지도·'자세히' 패널은
//   계속(절대 규칙 4). 카드 하나의 error/no_data 는 그 카드에만(home_ui.renderCard).
// - 늦게 온 옛 응답은 버린다(요청 번호). 갱신은 페이지의 slotEvery(장중 5분 슬롯)가 부른다.
// - 구역 머리 도장: 묶음 기준 시각·품질·카드 수(값 있음/전체, 오류 수).
import type { components } from '../../api/types.gen';
import type { PageContext } from '../../app/types';
import type { Envelope } from '../../data/source';
import { kstStamp, kstTime, num } from '../../ui/fmt';
import { QUALITY_LABEL } from '../../ui/quality';
import { failed, fill } from '../p1_market';
import type { HomePanel } from './home_defs';
import { drawMarket, type MarketPanels } from './home_market';
import { drawSignals, drawWatch, newState, type SignalPanels, WATCH_HINT } from './home_signals';
import type { Home } from './home_ui';

type Watchlist = components['schemas']['Watchlist'];

export const HOME_HINT = 'home 묶음(각 카드의 작업 시각)';

export interface HomeTargets {
  market: MarketPanels;
  signals: SignalPanels;
  watch: HomePanel;
  /** '오늘의 신호' 구역 머리 도장(묶음 기준) */
  stamp: HTMLElement;
  /** '시장' 구역 머리 도장(지수 카드 기준) */
  marketStamp: HTMLElement;
}

/** 도장 글자: '기준 09/15 15:30 · 잠정 · 카드 7/14 · 만든 시각 18:00' */
export function stampText(env: Envelope<Home>): string {
  const d = env.data;
  const total = d.n_ok + d.n_no_data + d.n_error;
  const parts = [`기준 ${kstStamp(env.as_of)}`];
  if (env.quality !== 'ok') parts.push(QUALITY_LABEL[env.quality]);
  parts.push(`카드 ${num(d.n_ok)}/${num(total)}`);
  if (d.n_error) parts.push(`오류 ${num(d.n_error)}`);
  parts.push(`만든 시각 ${kstTime(env.generated_at)}`);
  return parts.join(' · ');
}

/** 시장 구역 도장: '지수 기준 11/10 15:30 · 잠정' — 지수 카드가 없으면 빈 글자 */
export function marketStampText(env: Envelope<Home>): string {
  const c = env.data.cards.indices;
  if (c.status !== 'ok' || !c.as_of) return '';
  const parts = [`지수 기준 ${kstStamp(c.as_of)}`];
  if (c.quality && c.quality !== 'ok') parts.push(QUALITY_LABEL[c.quality]);
  return parts.join(' · ');
}

export function mountHome(ctx: PageContext, t: HomeTargets): { loadHome: () => Promise<void>; loadWatch: () => Promise<void> } {
  const st = newState();
  const { kospi, kosdaq, futures, band } = t.market;
  const all: HomePanel[] = [kospi, kosdaq, futures, band, ...Object.values<HomePanel>(t.signals)];
  let seq = 0;
  const loadHome = async (): Promise<void> => {
    seq += 1;
    const my = seq;
    try {
      const env = await ctx.source.get<Home>('home');
      if (my !== seq) return;
      t.stamp.textContent = stampText(env);
      t.marketStamp.textContent = marketStampText(env);
      drawMarket(t.market, env);
      drawSignals(t.signals, env, st);
    } catch (err) {
      if (my !== seq) return;
      t.stamp.textContent = '';
      t.marketStamp.textContent = '';
      for (const p of all) {
        p.setMeta('');
        failed(p, err, HOME_HINT);
      }
    }
  };
  const loadWatch = (): Promise<void> =>
    fill(
      t.watch,
      () => ctx.source.get<Watchlist>('watchlist'),
      (env) => {
        t.watch.setMeta('');
        return drawWatch(t.watch, env);
      },
      WATCH_HINT,
    );
  return { loadHome, loadWatch };
}
