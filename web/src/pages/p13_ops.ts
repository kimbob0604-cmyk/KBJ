// 페이지 13 '운영' 최소판(docs/p9_design.md §6.2, §11 Q5 — 보기 전용 표 셋, ADR 0037).
// 위에서 아래(휴대폰은 한 줄): ① 서비스 상태(12) ② 오늘 작업(6) ③ 오늘 발송 기록(6). 1분마다 다시 읽는다.
//
// - 운영 기록은 로그인 전용(DATA_TIERS §2 — 개인 운영). 공개판은 자물쇠만 그리고 요청하지 않는다. 본문 모듈(p13/ops)은
//   공개 빌드 번들에서 뺀다(p11_rules 방식 — 공개 JS 예산, ADR 0025).
// - 데이터 없음(404)은 '.msg' 안내, 그 밖 실패만 '.msg.err'. 한 패널의 실패는 그 패널 안에서 끝난다(절대 규칙 4).
import type { PageContext, PageModule } from '../app/types';
import { type Panel, panel, type Span } from '../ui/panel';
import { fillJobs, fillNotify, fillStatus } from './p13/ops';

/** 패널 순서 — 시험이 고정한다 */
export const P13_SECTIONS = ['status', 'jobs', 'notify'] as const;
export type P13Section = (typeof P13_SECTIONS)[number];

export const P13_PANELS: Readonly<Record<P13Section, { title: string; span: Span }>> = {
  status: { title: '서비스 상태', span: 12 },
  jobs: { title: '오늘 작업', span: 6 },
  notify: { title: '발송 기록', span: 6 },
};

export const REFRESH_MS = 60_000;

type Fill = (ctx: PageContext, p: Panel) => Promise<void>;

// 로그인 본문 — 공개 빌드에서는 번들에서 뺀다
const LOGIN_FILLS: Readonly<Record<P13Section, Fill>> | null =
  import.meta.env.MODE === 'public' ? null : { status: fillStatus, jobs: fillJobs, notify: fillNotify };

async function mount(ctx: PageContext): Promise<void> {
  const panels = P13_SECTIONS.map((id) => {
    const meta = P13_PANELS[id];
    const p = panel(ctx.tier, { title: meta.title, tier: 'login', lock: 'personal', span: meta.span, id: `p13-${id}` });
    ctx.root.append(p.el);
    return [id, p] as const;
  });
  const fills = LOGIN_FILLS;
  if (fills === null) return;
  const open = panels.filter(([, p]) => !p.locked);
  if (open.length === 0) return;
  const load = (): Promise<void> =>
    Promise.all(
      open.map(async ([id, p]) => {
        try {
          await fills[id](ctx, p);
        } catch (err) {
          p.showError(err);
        }
      }),
    ).then(() => undefined);
  await load();
  // 첫 불러오기 중에 앱이 내려갔으면(격자가 문서에서 빠짐) 주기 작업을 걸지 않는다
  if (ctx.root.isConnected) {
    ctx.every(REFRESH_MS, () => {
      void load();
    });
  }
}

const page: PageModule = { mount };
export default page;
