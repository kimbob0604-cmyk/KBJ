// 페이지 7 수출입(docs/p6_design.md §6 — 진입 모듈, 웨이브 1 FND 가 탭 틀을 고정). 탭 9: 종목 | 산업 | 월별 추이 | 속보 |
// 품목 | 국가 | 지역 | 급등 | 104품목(ui/tabs.ts — P10 이 종목·산업을 앞에 더했다). 탭은 처음 보일 때 그린다(요청도 그때 — P8 방식). '품목' 탭 아래에 매핑 종목 랭킹 표(MAP).
//
// - 웨이브 2 묶음(FLASH·EXP·MAP)은 이 파일을 고치지 않는다 — 하위 모듈(src/pages/p7/*.ts)의 mount 본문만 채운다.
// - 페이지 7 은 공개 전부(관세청 공개 — DATA_TIERS §1): 공개판은 공개 파일, 로그인판은 API(키는 p7/common.ts).
// - 한 탭 모듈의 실패는 그 탭 안 오류 줄로(다른 탭은 계속 — 절대 규칙 4, ui/tabs.ts).
import type { PageContext, PageModule } from '../app/types';
import { h } from '../ui/dom';
import { panel } from '../ui/panel';
import { type TabDef, tabs } from '../ui/tabs';
import type { TabCtx, TabMount } from './p7/common';
import { mount as mountCountries } from './p7/countries';
import { mount as mountMonthly } from './p7/monthly';
import { mount as mountFlash } from './p7/flash';
import { mount as mountIndustry, TITLE as INDUSTRY_TITLE } from './p7/industry';
import { mount as mountItems } from './p7/items';
import { mount as mountItems104 } from './p7/items104';
import { mount as mountRegions } from './p7/regions';
import { mount as mountStocks } from './p7/stocks';
import { mount as mountSurge } from './p7/surge';

export type TradeTabId = 'stocks' | 'industry' | 'monthly' | 'flash' | 'items' | 'countries' | 'regions' | 'surge' | 'items104';

export const TRADE_TABS: readonly TabDef<TradeTabId>[] = [
  { id: 'stocks', label: '종목' }, // P10 — 종목별 무역 흐름(맨 앞)
  { id: 'industry', label: '산업' }, // P10 단계 4 — 산업 → 종목 → 품목(로그인)
  { id: 'monthly', label: '월별 추이' },
  { id: 'flash', label: '속보' },
  { id: 'items', label: '품목' },
  { id: 'countries', label: '국가' },
  { id: 'regions', label: '지역' },
  { id: 'surge', label: '급등' },
  { id: 'items104', label: '104품목' },
];

/**
 * '산업' 탭(로그인 전용) — 공개 빌드는 자물쇠 패널만 그리고 모듈을 번들에서 뺀다(p8_stock.ts LOGIN_VALUE 와 같은 규칙,
 * 공개 JS gzip 예산 — scripts/check-bundle.mjs). 시험(MODE 'test')은 두 등급 모두 모듈을 싣는다.
 */
const industryTab: TabMount =
  import.meta.env.MODE === 'public'
    ? (t, el) => {
        el.append(h('div', { class: 'grid' }, panel(t.tier, { title: INDUSTRY_TITLE, tier: 'login', span: 12 }).el));
        return Promise.resolve();
      }
    : mountIndustry;

/** 탭 → 모듈(순서대로 붙인다). 정적 import — 공개 번들 예산은 청크 합이라 나누지 않는다(p8_stock.ts 와 같다) */
export const TAB_MODULES: Readonly<Record<TradeTabId, readonly TabMount[]>> = {
  stocks: [mountStocks],
  industry: [industryTab],
  monthly: [mountMonthly],
  flash: [mountFlash],
  items: [mountItems],
  countries: [mountCountries],
  regions: [mountRegions],
  surge: [mountSurge],
  items104: [mountItems104],
};

async function mount(ctx: PageContext): Promise<void> {
  const t: TabCtx = { tier: ctx.tier, source: ctx.source, now: ctx.now };
  const tb = tabs<TradeTabId>({
    label: '수출입 탭',
    tabs: TRADE_TABS,
    onFirstShow: async (id, el) => {
      for (const m of TAB_MODULES[id]) await m(t, el);
    },
  });
  // 페이지 8 과 같이 탭 묶음을 전체 폭 칸(span-12)에 둔다 — 페이지 격자의 한 칸에 눌리지 않게
  ctx.root.append(h('div', { class: 'span-12' }, tb.el));
  await tb.settled();
}

const page: PageModule = { mount };
export default page;
