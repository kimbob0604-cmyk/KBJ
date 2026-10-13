// 페이지 5 공시·일정(docs/p4_design.md §6.4) — P4 의 DART 패널: 공시 피드·잠정실적·새 오버행·내부자 큰 변동·실적 일정.
// P5(MAC — docs/p5_design.md §6): 경제 캘린더 패널(./p5/calendar — 페이지 6 과 같은 모듈). 뉴스는 만들지 않는다.
// P5 2차(IDX): 로그인 캘린더에 지수 정기변경 일정 줄(./p5/index_events — 발표·리밸런싱).
//
// - 등급: mixed — DART 패널은 공개(공개판은 public.export 파일을 화면에서 거른다), 관심종목만 보기·중요도 가산은 로그인.
// - 행·종목 이름을 누르면 종목 상세(페이지 8 — app/route.ts openStock → #/p/8/<코드>).
// - 장중 새로 고침(로그인): 공시 피드·잠정실적 60초(피드 수집은 매분, 서버 캐시 30초). 공개 파일은 하루 한 번이라 없다.
// - 패널마다 따로 불러와 한 패널의 실패가 다른 패널을 멈추지 않는다(절대 규칙 4).
import { openStock } from '../app/route';
import type { PageContext, PageModule } from '../app/types';
import { createCalendar } from './p5/calendar';
import type { P5Ctx } from './p5/common';
import { createFeed } from './p5/feed';
import { createInsiders } from './p5/insiders';
import { createOverhang } from './p5/overhang';
import { createPrelim } from './p5/prelim';
import { withIndexEvents } from './p5/index_events';
import { createSchedule } from './p5/schedule';

// P5 2차(IDX) 지수 정기변경 일정 줄 — 로그인 전용 API 라 공개 빌드는 모듈을 번들에서 뺀다(p10_etf LOGIN_INDEX 와 같은 방식)
const LOGIN_INDEX_EVENTS = import.meta.env.MODE === 'public' ? null : withIndexEvents;

/** 로그인 장중 새로 고침(공시 피드·잠정실적) */
export const FEED_REFRESH_MS = 60_000;

async function mount(ctx: PageContext): Promise<void> {
  const c: P5Ctx = { tier: ctx.tier, source: ctx.source, now: ctx.now, open: (code) => void openStock(code) };
  const feed = createFeed(c);
  const prelim = createPrelim(c);
  const overhang = createOverhang(c);
  const insiders = createInsiders(c);
  const schedule = createSchedule(c);
  const indexEvents = ctx.tier === 'login' ? LOGIN_INDEX_EVENTS : null;
  const calendar = createCalendar(c, 6, indexEvents ? (rows) => indexEvents(ctx.source, rows) : undefined);
  ctx.root.append(
    feed.panel.el,
    prelim.panel.el,
    overhang.panel.el,
    insiders.panel.el,
    schedule.panel.el,
    calendar.panel.el,
  );
  await Promise.all([feed.load(), prelim.load(), overhang.load(), insiders.load(), schedule.load(), calendar.load()]);
  // 첫 불러오기 중에 앱이 내려갔으면(격자가 문서에서 빠짐) 주기 작업을 걸지 않는다
  if (ctx.tier === 'login' && ctx.root.isConnected) {
    ctx.every(FEED_REFRESH_MS, () => {
      void feed.load();
      void prelim.load();
    });
  }
}

const page: PageModule = { mount };
export default page;
