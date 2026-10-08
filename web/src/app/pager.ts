// 탭·미니맵·좌우 스와이프·←/→·해시 #/p/<n>(docs/p3_design.md §6.4).
//
// - 스와이프: CSS scroll-snap(.pages). 보이는 페이지 감지는 IntersectionObserver(없으면 scroll 위치로).
// - 해시: 처음 열 때 #/p/4 면 4쪽으로. 페이지를 바꾸면 history.replaceState 로 해시만 바꾼다(뒤로 가기 기록을 쌓지 않음).
// - 키보드: 입력 칸 밖에서 ←/→(수정 키 없이). prefers-reduced-motion 이면 스크롤 애니메이션 없음.
// - 탭: 13개 전부, aria-current="page". 미니맵은 장식(aria-hidden). 구현 전 페이지는 점선(todo).
import { h, isEditable } from '../ui/dom';
import type { PageDef } from './pages';

export interface PagerOptions {
  pages: readonly PageDef[];
  /** 위젯이 있는(채워진) 페이지 번호 — 나머지는 점선 */
  filled: ReadonlySet<number>;
  container: HTMLElement;
  sections: ReadonlyMap<number, HTMLElement>;
  onShow: (n: number) => void;
  win?: Window;
}

export interface Pager {
  readonly tabs: HTMLElement;
  readonly minimap: HTMLElement;
  current(): number;
  go(n: number, opts?: { smooth?: boolean }): void;
  destroy(): void;
}

export const HASH_RE = /^#\/p\/(\d{1,2})$/;

export function parseHash(hash: string, pages: readonly PageDef[]): number | null {
  const m = HASH_RE.exec(hash);
  if (!m?.[1]) return null;
  const n = Number(m[1]);
  return pages.some((p) => p.n === n) ? n : null;
}

/** 페이지가 '보인다'고 볼 가로 비율 */
export const VISIBLE_SHARE = 0.6;

/**
 * 관찰 항목이 보이는 페이지인지 — **루트 너비 대비 보이는 가로 폭**으로 판단한다.
 * intersectionRatio 는 쓰지 않는다: 아직 그리지 않은(높이 0) 페이지는 넓이가 0 이라 바로 옆에 붙어만
 * 있어도 브라우저가 ratio 1·isIntersecting 으로 알린다 → 다음 페이지가 '현재'로 잡혀 탭 강조가 한 칸
 * 밀렸다(P3 데모 화면 점검 — 크로미움에서 재현).
 */
export function isMostlyVisible(
  e: Pick<IntersectionObserverEntry, 'isIntersecting' | 'intersectionRect' | 'rootBounds'>,
  fallbackRootWidth: number,
): boolean {
  if (!e.isIntersecting) return false;
  const w = e.rootBounds?.width ?? fallbackRootWidth;
  return w > 0 && e.intersectionRect.width >= VISIBLE_SHARE * w;
}

export function createPager(o: PagerOptions): Pager {
  const win = o.win ?? window;
  const order = o.pages.map((p) => p.n);
  let cur = order[0] ?? 1;

  const reduced = (): boolean => {
    try {
      return typeof win.matchMedia === 'function' && win.matchMedia('(prefers-reduced-motion: reduce)').matches;
    } catch {
      return false;
    }
  };

  const tabButtons = new Map<number, HTMLButtonElement>();
  const tabs = h(
    'nav',
    { class: 'tabs', attrs: { 'aria-label': '페이지' } },
    o.pages.map((p) => {
      const b = h(
        'button',
        {
          class: o.filled.has(p.n) ? undefined : 'todo',
          attrs: { type: 'button', 'aria-controls': o.sections.get(p.n)?.id },
          data: { page: p.n },
          on: { click: () => api.go(p.n) },
        },
        `${p.n} ${p.title}`,
      );
      tabButtons.set(p.n, b);
      return b;
    }),
  );

  const marks = new Map<number, HTMLElement>();
  const minimap = h(
    'div',
    { class: 'minimap', attrs: { 'aria-hidden': 'true', title: `전체 ${o.pages.length}개 페이지` } },
    o.pages.map((p) => {
      const i = h('i', { class: o.filled.has(p.n) ? undefined : 'todo', data: { page: p.n } });
      marks.set(p.n, i);
      return i;
    }),
  );

  function mark(n: number): void {
    for (const [k, b] of tabButtons) {
      if (k === n) b.setAttribute('aria-current', 'page');
      else b.removeAttribute('aria-current');
    }
    for (const [k, i] of marks) i.classList.toggle('on', k === n);
  }

  // 한 번이라도 보인 페이지(위젯은 처음 보일 때 붙인다)
  const shown = new Set<number>();

  function setCurrent(n: number, fromHash = false): void {
    if (!order.includes(n)) return;
    const changed = n !== cur;
    cur = n;
    mark(n);
    if (!fromHash && win.location.hash !== `#/p/${n}`) {
      try {
        win.history.replaceState(null, '', `#/p/${n}`);
      } catch {
        // file:// 등 replaceState 가 막힌 곳 — 해시 동기화만 빠진다
      }
    }
    if (changed || !shown.has(n)) {
      shown.add(n);
      o.onShow(n);
    }
  }

  function scrollToPage(n: number, smooth: boolean): void {
    const idx = order.indexOf(n);
    const left = idx * o.container.clientWidth;
    // 'auto' 는 CSS scroll-behavior(smooth)를 따르므로 즉시 이동은 'instant' 로 적는다
    const behavior: ScrollBehavior = smooth && !reduced() ? 'smooth' : 'instant';
    if (typeof o.container.scrollTo === 'function') o.container.scrollTo({ left, behavior });
    else o.container.scrollLeft = left;
  }

  // 보이는 페이지 감지
  let observer: IntersectionObserver | null = null;
  let suppressUntil = 0;
  const onScroll = (): void => {
    if (Date.now() < suppressUntil) return;
    const w = o.container.clientWidth;
    if (w <= 0) return;
    const n = order[Math.round(o.container.scrollLeft / w)];
    if (n !== undefined && n !== cur) setCurrent(n);
  };
  const IO = (win as Window & { IntersectionObserver?: typeof IntersectionObserver }).IntersectionObserver;
  if (typeof IO === 'function') {
    const io = new IO(
      (entries: IntersectionObserverEntry[]) => {
        if (Date.now() < suppressUntil) return;
        for (const e of entries) {
          if (!isMostlyVisible(e, o.container.clientWidth)) continue;
          const n = Number((e.target as HTMLElement).dataset.page);
          if (n !== cur) setCurrent(n);
        }
      },
      // 0 과 1 사이 여러 문턱 — 폭 판단은 isMostlyVisible 이 한다(문턱은 알림을 받을 시점만 정한다)
      { root: o.container, threshold: [0, 0.25, 0.5, 0.6, 0.75, 1] },
    );
    for (const sec of o.sections.values()) io.observe(sec);
    observer = io;
  } else {
    o.container.addEventListener('scroll', onScroll, { passive: true });
  }

  const onKey = (e: KeyboardEvent): void => {
    if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    if (isEditable(e.target)) return;
    const idx = order.indexOf(cur);
    const next = order[idx + (e.key === 'ArrowRight' ? 1 : -1)];
    if (next === undefined) return;
    e.preventDefault();
    api.go(next);
  };
  const onHash = (): void => {
    const n = parseHash(win.location.hash, o.pages);
    if (n !== null && n !== cur) {
      suppressUntil = Date.now() + 600;
      scrollToPage(n, false);
      setCurrent(n, true);
    }
  };
  win.addEventListener('keydown', onKey);
  win.addEventListener('hashchange', onHash);

  const api: Pager = {
    tabs,
    minimap,
    current: () => cur,
    go(n, opts) {
      if (!order.includes(n)) return;
      // 부드러운 스크롤 동안 관찰자가 중간 페이지를 '현재'로 잡지 않게 잠시 무시
      suppressUntil = Date.now() + 600;
      scrollToPage(n, opts?.smooth ?? true);
      setCurrent(n);
    },
    destroy() {
      observer?.disconnect();
      o.container.removeEventListener('scroll', onScroll);
      win.removeEventListener('keydown', onKey);
      win.removeEventListener('hashchange', onHash);
    },
  };

  const initial = parseHash(win.location.hash, o.pages) ?? cur;
  scrollToPage(initial, false);
  setCurrent(initial, true);
  return api;
}
