// 페이지 9 테마·밸류체인(ADR 0039 — 사용자 결정 2026-10-15: stock-dashboard 테마 마스터를 받아 쓴다).
// 위에서 아래(휴대폰은 한 줄): ① 대테마 분류(8) ② 밸류체인(4) ③ 테마 강도 순위(12) ④ 강도 히트맵(6)
// ⑤ 밸류체인 강도(6) ⑥ 편입 종목(12).
//
// - 페이지 9 는 mixed(DATA_TIERS §2 9 행): ①② 대테마 분류(사용자 작성)는 공개 — 공개판은 공개 파일 themes.json.
//   ③~⑥(시세 기반 강도·네이버 유래 편입 종목·사유)은 로그인 — 공개판은 자물쇠만 그리고 요청하지 않는다.
// - 로그인 본문(p9/strength.ts)은 공개 빌드 번들에서 뺀다(p8_stock LOGIN_VALUE 방식 — 공개 JS 예산, ADR 0025).
// - 한 패널의 실패는 그 패널 안에서 끝난다(절대 규칙 4). 404 는 '.msg'(작업·시각), 그 밖 실패만 '.msg.err'.
import type { PageContext, PageModule } from '../app/types';
import { panel, type Panel, type Span } from '../ui/panel';
import { catalogPanels, fillCatalog, type P9Ctx } from './p9/catalog';
import { fillStrength } from './p9/strength';

/** 패널 순서 — 시험이 고정한다 */
export const P9_SECTIONS = ['sections', 'chains', 'rank', 'heat', 'chain_strength', 'members'] as const;
export type P9Section = (typeof P9_SECTIONS)[number];

export const P9_LOGIN_PANELS: Readonly<Record<'rank' | 'heat' | 'chain_strength' | 'members', { title: string; span: Span; lock: string; def: string }>> = {
  rank: { title: '테마 강도', span: 12, lock: 'quote_based', def: '편입 종목 등락의 동일가중 평균(당일·5일·20일 — 종목 하나에 한 표)·상승 비율·신고가 수. 거래대금 1위 = 그 테마에서 거래대금이 가장 큰 종목' },
  heat: { title: '강도 히트맵', span: 6, lock: 'quote_based', def: '크기 = 편입 종목 시총 합, 색 = 고른 기간 등락' },
  chain_strength: { title: '밸류체인 강도', span: 6, lock: 'quote_based', def: '같은 밸류체인 대테마들의 편입 종목 합집합' },
  members: { title: '편입 종목', span: 12, lock: '네이버 유래 편입 종목·사유 — 로그인', def: '대테마에 걸린 원본 테마의 종목 합집합 · 종목을 누르면 종목 상세' },
};

// 로그인 본문 — 공개 빌드에서는 번들에서 뺀다
const LOGIN_FILL: typeof fillStrength | null = import.meta.env.MODE === 'public' ? null : fillStrength;

async function mount(ctx: PageContext): Promise<void> {
  let picker: ((id: string) => void) | null = null;
  const t: P9Ctx = {
    tier: ctx.tier,
    source: ctx.source,
    now: ctx.now,
    pick: ctx.tier === 'login' && LOGIN_FILL ? (id) => picker?.(id) : undefined,
  };
  const pub = catalogPanels(ctx.tier);
  ctx.root.append(pub.sections.el, pub.chains.el);
  const login = {} as Record<keyof typeof P9_LOGIN_PANELS, Panel>;
  for (const id of ['rank', 'heat', 'chain_strength', 'members'] as const) {
    const m = P9_LOGIN_PANELS[id];
    login[id] = panel(ctx.tier, { title: m.title, tier: 'login', span: m.span, lock: m.lock, def: m.def, id: `p9-${id}` });
    ctx.root.append(login[id].el);
  }
  const jobs: Promise<unknown>[] = [fillCatalog(t, pub)];
  if (LOGIN_FILL && !login.rank.locked) {
    jobs.push(
      LOGIN_FILL(t, { rank: login.rank, heat: login.heat, chains: login.chain_strength, members: login.members }, (fn) => {
        picker = fn;
      }).catch((err: unknown) => {
        login.rank.showError(err);
      }),
    );
  }
  await Promise.all(jobs);
}

const page: PageModule = { mount };
export default page;
