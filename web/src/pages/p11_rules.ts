// 페이지 11 알림·규칙(docs/p8_design.md §6 — 진입 모듈, 웨이브 1 FND 가 패널 순서·칸 폭·하위 모듈 import 를 고정).
// 위에서 아래(휴대폰은 한 줄): ① 규칙(12) ② 편집기(6) ③ 발송 이력(6) ④ 알림 후 성과 표(6) ⑤ 수익 분포(6 — 차트는 반폭).
//
// - 페이지 11 은 개인 기능(DATA_TIERS §2 — 로그인 전부). 공개판은 자물쇠만 그리고 요청하지 않는다. 하위 모듈은 공개 빌드
//   번들에서 뺀다(p8_stock LOGIN_VALUE 방식 — 공개 JS 예산, ADR 0025).
// - 웨이브 2 는 하위 모듈 본문만: EVL = p11/rules·editor·history, BKT = p11/perf. 진입 파일·칸 폭은 FND.
// - 데이터 없음(404)은 '.msg' 안내(p.showMessage), 그 밖 실패만 '.msg.err'(p.showError). 한 패널의 실패는 그 패널 안에서
//   끝난다(다른 패널은 계속 — 절대 규칙 4).
import type { PageContext, PageModule } from '../app/types';
import { type Panel, panel, type Span } from '../ui/panel';
import { fillEditor } from './p11/editor';
import { fillHistory } from './p11/history';
import { fillPerf, fillPerfDist } from './p11/perf';
import { fillRules } from './p11/rules';

/** 패널 순서(§6) — 시험이 고정한다 */
export const P11_SECTIONS = ['rules', 'editor', 'history', 'perf', 'perf_dist'] as const;
export type P11Section = (typeof P11_SECTIONS)[number];

export const P11_PANELS: Readonly<Record<P11Section, { title: string; span: Span }>> = {
  rules: { title: '규칙', span: 12 },
  editor: { title: '편집기', span: 6 },
  history: { title: '발송 이력', span: 6 },
  perf: { title: '알림 후 성과', span: 6 },
  perf_dist: { title: '성과 분포', span: 6 },
};

type Fill = (ctx: PageContext, p: Panel) => Promise<void>;

// 로그인 본문 — 공개 빌드에서는 번들에서 뺀다
const LOGIN_FILLS: Readonly<Record<P11Section, Fill>> | null =
  import.meta.env.MODE === 'public'
    ? null
    : { rules: fillRules, editor: fillEditor, history: fillHistory, perf: fillPerf, perf_dist: fillPerfDist };

async function mount(ctx: PageContext): Promise<void> {
  const panels = P11_SECTIONS.map((id) => {
    const meta = P11_PANELS[id];
    const p = panel(ctx.tier, { title: meta.title, tier: 'login', lock: 'personal', span: meta.span, id: `p11-${id}` });
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
