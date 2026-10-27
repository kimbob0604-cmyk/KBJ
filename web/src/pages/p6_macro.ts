// 페이지 6 매크로(docs/p5_design.md §6, D-P5-5~8 — MAC). 탭 7: 금리·통화 / 물가 / 성장·경기 / 고용·가계 / 대외·환율 / 미국
// (공개 — ECOS 한은 작성 표·KOSIS·FRED 정부 시리즈·미 재무부·뉴욕연은) + 시장(로그인 — 자물쇠). 경제 캘린더 패널은 페이지 5 와
// 같은 모듈(./p5/calendar).
//
// - 로그인: API 'macro/tabs'·'macro/series?tab='·'macro/calendar'. 공개: 공개 파일 'macro/tabs'·'macro/<탭>'·'macro/calendar'
//   (public.export — 공개 시리즈만, 같은 모양). 공개판은 로그인 API 를 부르지 않는다.
// - 패널마다 따로 불러와 한 패널의 실패가 다른 패널을 멈추지 않는다(절대 규칙 4). 값을 지어내지 않는다.
import type { PageContext, PageModule } from '../app/types';
import type { Envelope } from '../data/source';
import { failText } from '../ui/panel';
import { createCalendar } from './p5/calendar';
import { type MacroTabs, MACRO_HINT } from './p6/common';
import { createMarketPanel } from './p6/market';
import { createSeriesPanel } from './p6/series';

/** 이 페이지의 패널 제목 */
export const P6_TITLES = { series: '매크로 지표', calendar: '경제 캘린더', market: '시장 탭' } as const;

async function mount(ctx: PageContext): Promise<void> {
  const c = { tier: ctx.tier, source: ctx.source, now: ctx.now };
  let tabsEnv: Envelope<MacroTabs> | null = null;
  let tabsErr: unknown = null;
  try {
    tabsEnv = await ctx.source.get<MacroTabs>('macro/tabs');
  } catch (err) {
    tabsErr = err;
  }
  const publicTabs = (tabsEnv?.data.tabs ?? []).filter((t) => t.public);
  const series = createSeriesPanel(c, publicTabs);
  if (tabsErr !== null) series.panel.showMessage(failText(tabsErr, MACRO_HINT));
  else if (tabsEnv) series.panel.setSrcText(`탭 ${publicTabs.length}개 · ${tabsEnv.source}`);
  const calendar = createCalendar(c, 4);
  const market = createMarketPanel(c);
  ctx.root.append(series.panel.el, calendar.panel.el, market.panel.el);
  await Promise.all([series.settled(), calendar.load(), market.load()]);
}

const page: PageModule = { mount };
export default page;
