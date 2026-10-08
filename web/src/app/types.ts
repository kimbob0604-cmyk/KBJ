// 셸과 페이지 모듈 사이의 약속(W2 가 페이지 1·2·4·10 과 상단 띠를 이 약속에 맞춰 만든다).
//
// 페이지 모듈: src/pages/p<번호>_<이름>.ts 가 `export default` 로 PageModule 을 내보내면 셸이 찾아 붙인다
// (app/pages.ts 의 import.meta.glob). 띠: src/app/ribbon.ts 가 `export default` 로 RibbonModule.
// 파일이 없으면 셸은 '준비 중' 자리를 그린다 — 그래서 W(셸)와 W2(위젯)가 같은 파일을 고치지 않는다.
import type { DataSource, Tier } from '../data/source';
import type { TradingViewMount } from '../pages/tradingview';
import type { PageDef } from './pages';

export type Cleanup = () => void;

export interface AppContext {
  /** 보는 쪽 등급(빌드 등급) */
  readonly tier: Tier;
  readonly source: DataSource;
  /** 주입된 시계 — 시험은 가짜 시계 */
  readonly now: () => Date;
  /**
   * 주기 실행. 탭이 안 보이면(document.hidden) 멈추고 다시 보이면 한 번 바로 돈다(§6.9).
   * 앱이 내려가면 자동으로 멈춘다. 돌려준 함수로 먼저 멈출 수 있다.
   */
  readonly every: (ms: number, fn: () => void, opts?: { immediate?: boolean }) => Cleanup;
  /** 공개 빌드에만 있다 — 시세 자리에 TradingView 위젯(DATA_TIERS §2) */
  readonly tradingview?: TradingViewMount;
}

export interface PageContext extends AppContext {
  readonly page: PageDef;
  /** 페이지 격자(.grid) — 패널을 여기에 붙인다 */
  readonly root: HTMLElement;
}

/**
 * 페이지 모듈. mount 는 페이지가 처음 보일 때 한 번 불린다. 돌려준 값(또는 Promise 의 값)이 함수(Cleanup)면
 * 앱이 내려갈 때 부른다. 아무것도 돌려주지 않아도 된다(그래서 반환형은 unknown).
 */
export interface PageModule {
  mount(ctx: PageContext): unknown;
}

/** 상단 띠 모듈 — mount 의 반환 규칙은 PageModule 과 같다. */
export interface RibbonModule {
  mount(el: HTMLElement, ctx: AppContext): unknown;
}
