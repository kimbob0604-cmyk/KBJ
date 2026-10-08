// 앱 조립 — 상단 바 · 상단 띠 · 탭/미니맵 · 좌우 페이지 · 바닥 줄(docs/p3_design.md §6.4).
// 등급(tier)은 인자로 받는다: 빌드 상수는 main.ts 한 곳에서만 읽고, 시험은 두 등급을 같은 코드로 돌린다.
import type { DataSource, Tier } from '../data/source';
import { mountPlaceholder } from '../pages/placeholder';
import type { TradingViewMount } from '../pages/tradingview';
import { chip } from '../ui/chip';
import { h, replace } from '../ui/dom';
import { panel } from '../ui/panel';
import { type PageLoader, pageDef, pageLoaders, PAGES } from './pages';
import { createPager, type Pager } from './pager';
import { createScheduler } from './refresh';
import { createTopBar } from './shell';
import type { AppContext, Cleanup, PageContext, RibbonModule } from './types';

/** 상단 띠 모듈(W2 의 src/app/ribbon.ts) — 없으면 빈 목록 */
const RIBBON = import.meta.glob<RibbonModule>('./ribbon.ts', { import: 'default' });

export interface MountOptions {
  tier: Tier;
  source: DataSource;
  now?: () => Date;
  tradingview?: TradingViewMount;
  user?: string;
  onLogout?: () => void;
  /** 시험용: 페이지 모듈 목록(기본은 빌드 때 찾은 src/pages/p*_*.ts) */
  loaders?: ReadonlyMap<number, PageLoader>;
  /** 시험용: 상단 띠 모듈(null = 없음) */
  ribbon?: (() => Promise<RibbonModule>) | null;
  /** 스킨 저장소. 생략하면 브라우저 localStorage, null 이면 저장하지 않음 */
  storage?: Pick<Storage, 'getItem' | 'setItem'> | null;
  win?: Window;
}

export interface AppHandle {
  readonly pager: Pager;
  readonly ctx: AppContext;
  /** 페이지 모듈 mount 가 끝날 때까지(시험용) */
  settled(): Promise<void>;
  destroy(): void;
}

export const FOOT_TEXT: Record<Tier, string> = {
  public:
    '공개판 — 공개 등급 데이터(DART·ECOS·관세청·금투협 종합통계)와 TradingView 위젯만 보여 줍니다. 시세 기반 기능은 로그인판에 있습니다. 숫자마다 원천·시각·품질을 붙입니다.',
  login: '숫자마다 원천·시각·품질을 붙입니다. 잠정(장중) 값은 마감 뒤 확정치로 바뀝니다.',
};

export function mountApp(root: HTMLElement, o: MountOptions): AppHandle {
  const win = o.win ?? window;
  const sched = createScheduler(win, root.ownerDocument);
  const now = o.now ?? (() => new Date());
  const ctx: AppContext = {
    tier: o.tier,
    source: o.source,
    now,
    every: (ms, fn, opts) => sched.every(ms, fn, opts),
    ...(o.tradingview ? { tradingview: o.tradingview } : {}),
  };
  const cleanups: Cleanup[] = [];
  const pending: Promise<void>[] = [];
  const loaders = o.loaders ?? pageLoaders();
  const filled = new Set<number>([...loaders.keys()]);

  // ── 뼈대 ────────────────────────────────────────────────
  const top = createTopBar({
    tier: o.tier,
    now,
    every: ctx.every,
    ...(o.user ? { user: o.user } : {}),
    ...(o.onLogout ? { onLogout: o.onLogout } : {}),
    // 주지 않으면(undefined) 브라우저 localStorage — null 로 바꾸면 스킨이 저장되지 않는다
    ...(o.storage !== undefined ? { storage: o.storage } : {}),
  });
  const ribbonEl = h('div', { class: 'ribbon wrap', attrs: { role: 'region', 'aria-label': '상단 띠' } });
  const container = h('main', { class: 'pages', attrs: { id: 'pages' } });
  const grids = new Map<number, HTMLElement>();
  const sections = new Map<number, HTMLElement>();
  for (const p of PAGES) {
    const titleId = `page-${p.n}-title`;
    const grid = h('div', { class: 'grid' });
    const sec = h(
      'section',
      { class: 'page', attrs: { id: `page-${p.n}`, 'aria-labelledby': titleId }, data: { page: p.n } },
      h('h2', { class: 'sr-only', attrs: { id: titleId } }, `${p.n} ${p.title}`),
      grid,
    );
    grids.set(p.n, grid);
    sections.set(p.n, sec);
    container.append(sec);
  }

  // ── 페이지 붙이기(처음 보일 때 한 번) ────────────────────
  const mounted = new Set<number>();
  function mountPage(n: number): void {
    if (mounted.has(n)) return;
    mounted.add(n);
    const def = pageDef(n);
    const grid = grids.get(n);
    if (!def || !grid) return;
    const pctx: PageContext = { ...ctx, page: def, root: grid };
    const load = loaders.get(n);
    if (!load) {
      mountPlaceholder(pctx);
      return;
    }
    const job = load()
      .then((mod) => mod.mount(pctx))
      .then((c) => {
        if (typeof c === 'function') cleanups.push(c as Cleanup);
      })
      .catch((err: unknown) => {
        // 이 페이지만 오류 줄 — 다른 페이지·띠는 계속(절대 규칙 4)
        console.error(`[kbj] 페이지 ${n} 오류`, err);
        const p = panel(o.tier, { title: `${def.n} ${def.title}`, tier: 'public', span: 12 });
        p.showMessage('이 페이지를 그리지 못했습니다. 다른 페이지는 계속 쓸 수 있습니다.');
        replace(grid, p.el);
      });
    pending.push(job);
  }

  const pager = createPager({
    pages: PAGES,
    filled,
    container,
    sections,
    onShow: mountPage,
    win,
  });

  const nav = h(
    'div',
    { class: 'nav' },
    pager.tabs,
    h('div', { class: 'spacer' }),
    pager.minimap,
    h('span', { class: 'hint' }, '좌우로 밀거나 ←/→ 로 페이지 이동'),
  );

  replace(
    root,
    h('div', { class: 'wrap' }, top),
    ribbonEl,
    h('div', { class: 'wrap' }, nav, container, h('footer', { class: 'foot' }, FOOT_TEXT[o.tier])),
  );
  root.classList.add('app');
  root.dataset.tier = o.tier;

  // ── 상단 띠 ──────────────────────────────────────────────
  const ribbonLoader = o.ribbon === undefined ? RIBBON['./ribbon.ts'] : o.ribbon;
  if (ribbonLoader) {
    pending.push(
      ribbonLoader()
        .then((mod) => mod.mount(ribbonEl, ctx))
        .then((c) => {
          if (typeof c === 'function') cleanups.push(c as Cleanup);
        })
        .catch((err: unknown) => {
          console.error('[kbj] 상단 띠 오류', err);
          replace(ribbonEl, chip(o.tier, { key: 'ribbon', label: '상단 띠', value: '불러오지 못함', cls: 'warn' }));
        }),
    );
  } else {
    replace(ribbonEl, chip(o.tier, { key: 'ribbon', label: '상단 띠', state: 'pending', phase: 'P3' }));
  }

  return {
    pager,
    ctx,
    async settled() {
      // 처리 중에 새 작업이 생길 수 있어 빌 때까지
      let seen = 0;
      while (seen < pending.length) {
        const batch = pending.slice(seen);
        seen = pending.length;
        await Promise.all(batch);
      }
    },
    destroy() {
      sched.stop();
      pager.destroy();
      for (const c of cleanups.splice(0)) {
        try {
          c();
        } catch (err) {
          console.error('[kbj] 정리 오류', err);
        }
      }
      replace(root);
    },
  };
}
