// 페이지 12 백테스트·저널(docs/p8_design.md §6 — 진입 모듈, 웨이브 1 FND 가 패널 순서·칸 폭·하위 모듈 import 를 고정).
// 위에서 아래(휴대폰은 한 줄): ① 백테스트(6) ② 누적 곡선(6 — 차트는 반폭) ③ 백테스트 대 실제(12) ④ 분석 일지(6)
// ⑤ 포트폴리오·수익률 저널(6) ⑥ 상관관계(6) ⑦ 추천 성과(6 — 이관분이 없으면 안내).
//
// - 페이지 12 는 개인 기능(DATA_TIERS §2 — 로그인 전부). 공개판은 자물쇠만 그리고 요청하지 않는다. 하위 모듈은 공개 빌드
//   번들에서 뺀다(p8_stock LOGIN_VALUE 방식 — 공개 JS 예산, ADR 0025).
// - 웨이브 2 는 하위 모듈 본문만: BKT = p12/backtest·vs_live, JNL = p12/journal·portfolio·corr·picks.
// - 데이터 없음(404)은 '.msg' 안내, 그 밖 실패만 '.msg.err'. 한 패널의 실패는 그 패널 안에서 끝난다(절대 규칙 4).
import type { PageContext, PageModule } from '../app/types';
import { type Panel, panel, type Span } from '../ui/panel';
import { fillBacktest, fillBacktestCurve } from './p12/backtest';
import { fillCorr } from './p12/corr';
import { fillJournal } from './p12/journal';
import { fillPicks } from './p12/picks';
import { fillPortfolio } from './p12/portfolio';
import { fillVsLive } from './p12/vs_live';

/** 패널 순서(§6) — 시험이 고정한다 */
export const P12_SECTIONS = ['backtest', 'curve', 'vs_live', 'journal', 'portfolio', 'corr', 'picks'] as const;
export type P12Section = (typeof P12_SECTIONS)[number];

export const P12_PANELS: Readonly<Record<P12Section, { title: string; span: Span }>> = {
  backtest: { title: '백테스트', span: 6 },
  curve: { title: '누적 곡선', span: 6 },
  vs_live: { title: '백테스트 대 실제', span: 12 },
  journal: { title: '분석 일지', span: 6 },
  portfolio: { title: '포트폴리오·수익률 저널', span: 6 },
  corr: { title: '상관관계', span: 6 },
  picks: { title: '추천 성과', span: 6 },
};

type Fill = (ctx: PageContext, p: Panel) => Promise<void>;

// 로그인 본문 — 공개 빌드에서는 번들에서 뺀다
const LOGIN_FILLS: Readonly<Record<P12Section, Fill>> | null =
  import.meta.env.MODE === 'public'
    ? null
    : {
        backtest: fillBacktest,
        curve: fillBacktestCurve,
        vs_live: fillVsLive,
        journal: fillJournal,
        portfolio: fillPortfolio,
        corr: fillCorr,
        picks: fillPicks,
      };

async function mount(ctx: PageContext): Promise<void> {
  const panels = P12_SECTIONS.map((id) => {
    const meta = P12_PANELS[id];
    const p = panel(ctx.tier, { title: meta.title, tier: 'login', lock: 'personal', span: meta.span, id: `p12-${id}` });
    ctx.root.append(p.el);
    return [id, p] as const;
  });
  if (LOGIN_FILLS === null) return;
  for (const [id, p] of panels) {
    if (p.locked) continue;
    try {
      await LOGIN_FILLS[id](ctx, p);
    } catch (err) {
      p.showError(err);
    }
  }
}

const page: PageModule = { mount };
export default page;
