// 첫 화면(페이지 1) 카드 틀 — 구역 머리·카드 목록·카드 한 장(패널 + 한 줄 메타 + '더 보기 ›').
//
// - 공개·로그인 두 번들에 다 들어간다(공개판도 카드 자리에 자물쇠를 그린다) — 그래서 그리기 코드는 없고 틀만 둔다.
//   값을 그리는 코드는 로그인 전용 ./home.ts·home_market.ts·home_signals.ts(공개 번들에서 빠진다 — ADR 0025 예산).
// - 카드 = ui/panel.ts 패널(등급 자물쇠·원천 줄·오류 격리) + 머리 아래 메타 줄(기준·건수) + '더 보기 ›'(#/p/N).
//   페이지 1 안의 카드는 링크 대신 '자세히 ›' 단추(같은 쪽 아래 '자세히' 구역의 패널로 내려간다).
// - 데이터는 `home` 묶음(GET /api/home — 카드마다 봉투·상태) 한 번 + 관심종목(`watchlist`) + 업종 지도(`market/sectors`).
import type { Tier } from '../../data/source';
import { h } from '../../ui/dom';
import type { LockText } from '../../ui/lock';
import { type Panel, panel } from '../../ui/panel';

/** 신호 카드 키 — `home` 묶음 카드(지수·선물 빼고) + 관심종목(따로 부른다 — 개인 데이터는 묶음에 없다) */
export type SignalKey =
  | 'filings'
  | 'watchlist'
  | 'breadth'
  | 'spike'
  | 'movers'
  | 'foreign_inst'
  | 'newhigh'
  | 'gex'
  | 'diag'
  | 'etf'
  | 'export'
  | 'themes'
  | 'program';

export interface CardDef {
  key: SignalKey;
  title: string;
  /** '더 보기'가 갈 페이지(1 이면 같은 쪽 '자세히' 구역) */
  page: number;
  lock: LockText;
  /** 격자 전체 폭 */
  wide?: boolean;
}

/** '오늘의 신호' 카드 순서(위 → 아래, 3열) — 맨 위 공시는 전체 폭 */
export const SIGNAL_CARDS: readonly CardDef[] = [
  { key: 'filings', title: '오늘의 공시', page: 5, lock: '첫 화면 묶음은 로그인 — 공시 목록은 페이지 5', wide: true },
  { key: 'watchlist', title: '관심종목', page: 8, lock: 'personal' },
  { key: 'breadth', title: '시장 폭 · 20일선', page: 1, lock: 'quote_based' },
  { key: 'spike', title: '거래대금 급증', page: 4, lock: 'krx_daily' },
  { key: 'movers', title: '급등 · 급락', page: 4, lock: 'krx_daily' },
  { key: 'foreign_inst', title: '외국인 + 기관', page: 4, lock: 'investor' },
  { key: 'newhigh', title: '신고가', page: 2, lock: 'board' },
  { key: 'gex', title: 'GEX 감마', page: 3, lock: 'options' },
  { key: 'diag', title: '진단', page: 3, lock: '종합 판정은 시세 성분 포함 — 공공지표 진단은 페이지 3' },
  { key: 'etf', title: 'ETF 순유입', page: 10, lock: 'etf' },
  { key: 'export', title: '수출 속보', page: 7, lock: '첫 화면 묶음은 로그인 — 수출 속보는 페이지 7' },
  { key: 'themes', title: '테마 강도', page: 9, lock: 'quote_based' },
  { key: 'program', title: '프로그램 매매', page: 1, lock: 'kis' },
];

/** 시장 구역 지수 카드 제목 */
export const MARKET_TITLES = { kospi: '코스피', kosdaq: '코스닥', futures: '코스피200 선물', band: '상승 · 보합 · 하락' } as const;

export const SECTION = { market: '시장', signals: '오늘의 신호', detail: '자세히' } as const;

/** 카드 한 장 — 패널 + 메타 줄 + 더 보기 */
export interface HomePanel extends Panel {
  readonly key: string;
  /** 한 줄 메타(기준일·조건·건수) */
  setMeta(text: string): void;
}

/** 구역 머리(h3 + 기준 시각 도장). 격자 한 줄 전체 */
export function sectionHead(title: string): { el: HTMLElement; stamp: HTMLParagraphElement } {
  const stamp = h('p', { class: 'hstamp' });
  return { el: h('div', { class: 'hsec span-12', data: { section: title } }, h('h3', null, title), stamp), stamp };
}

/** 카드 격자(3열 → 좁으면 2열·1열 — design/components.css .hgrid) */
export function cardGrid(cls: string, label: string): HTMLElement {
  return h('div', { class: `hgrid ${cls} span-12`, attrs: { role: 'group', 'aria-label': label } });
}

/**
 * 카드 하나. page 가 1 이면 같은 쪽 패널(detail)로 내려가는 단추, 아니면 #/p/N 링크.
 * 공개 빌드의 로그인 카드는 자물쇠만(메타 줄 없음 — 요청도 없다). '더 보기'는 남긴다(그 페이지의 공개 패널로).
 */
export function homePanel(
  viewer: Tier,
  o: { key: string; title: string; tier: Tier; page: number; lock?: LockText; wide?: boolean; detail?: () => Panel | null },
): HomePanel {
  const p = panel(viewer, { title: o.title, tier: o.tier, lock: o.lock });
  p.el.classList.add('hcard');
  if (o.wide) p.el.classList.add('wide');
  p.el.dataset.card = o.key;
  const meta = h('p', { class: 'hmeta' });
  const label = `${o.title} 더 보기`;
  const more =
    o.page === 1
      ? o.detail
        ? h(
            'button',
            {
              class: 'hmore',
              attrs: { type: 'button', 'aria-label': `${o.title} 자세히` },
              on: {
                click: () => {
                  const el = o.detail?.()?.el;
                  // jsdom 에는 scrollIntoView 가 없다
                  if (el && typeof el.scrollIntoView === 'function') el.scrollIntoView({ block: 'start' });
                },
              },
            },
            '자세히 ›',
          )
        : null
      : h('a', { class: 'hmore', attrs: { href: `#/p/${o.page}`, 'aria-label': `${label} — 페이지 ${o.page}` } }, '더 보기 ›');
  p.el.insertBefore(h('div', { class: 'hbar' }, p.locked ? null : meta, more), p.head.nextSibling);
  return {
    ...p,
    key: o.key,
    setMeta(text) {
      meta.textContent = text;
    },
  };
}
