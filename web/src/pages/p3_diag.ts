// 페이지 3 진단(docs/p7_design.md §6 — 진입 모듈, 웨이브 1 FND-B 가 패널 순서·등급 갈림·하위 모듈 import 를 고정).
// 위에서 아래(휴대폰은 한 줄): ① 판정 띠 ② 공공지표 진단 ③ 시장 진단 ④ GEX 레벨 ⑤ 이벤트 달력.
//
// - 페이지 3 은 mixed(DATA_TIERS): 공개판은 ②⑤(공공지표 진단·이벤트)만 그리고 나머지는 자물쇠(요청하지 않는다).
// - 웨이브 2 는 하위 모듈 본문만: DIAG = p3/diag.ts(①②③⑤)와 이 파일, ENG = p3/gex.ts(④).
// - 한 하위 모듈의 실패는 그 패널 안 오류로(다른 패널은 계속 — 절대 규칙 4). 순서는 위 번호대로 붙인다.
import type { PageContext, PageModule } from "../app/types";
import { marketPanels, mountEvents, mountMarket, mountPublic, mountVerdict, verdictPanels } from "./p3/diag";
import { gexPanels, mountGex } from "./p3/gex";

/** 패널 순서(§6.1) — 시험이 고정한다 */
export const P3_SECTIONS = [
  "verdict",
  "public",
  "market",
  "gex",
  "events",
] as const;
export type P3Section = (typeof P3_SECTIONS)[number];

type Mount = (ctx: PageContext) => Promise<void>;

// 로그인 패널(①③④) 본문 — 공개 빌드에서는 번들에서 뺀다(p8_stock LOGIN_VALUE 와 같은 방식, P7 WRAP 요청을
// P8 WRAP 이 처리 — 공개 JS gzip 예산 ADR 0025). 공개판은 같은 틀(자물쇠)만 그린다 — 요청 0.
const LOGIN_MOUNTS: Readonly<Record<'verdict' | 'market' | 'gex', Mount>> | null =
  import.meta.env.MODE === "public" ? null : { verdict: mountVerdict, market: mountMarket, gex: mountGex };

const lockedOnly = (frame: (ctx: PageContext) => unknown): Mount =>
  (ctx) => {
    frame(ctx);
    return Promise.resolve();
  };

const MOUNTS: Readonly<Record<P3Section, Mount>> = {
  verdict: LOGIN_MOUNTS?.verdict ?? lockedOnly(verdictPanels),
  public: mountPublic,
  market: LOGIN_MOUNTS?.market ?? lockedOnly(marketPanels),
  gex: LOGIN_MOUNTS?.gex ?? lockedOnly(gexPanels),
  events: mountEvents,
};

async function mount(ctx: PageContext): Promise<void> {
  for (const id of P3_SECTIONS) await MOUNTS[id](ctx);
}

const page: PageModule = { mount };
export default page;
