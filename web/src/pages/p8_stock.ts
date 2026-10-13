// 페이지 8 종목 상세(docs/p4_design.md §5·§6.1~§6.3) — 진입 모듈. 탭 모듈(src/pages/p8/*.ts)은 탭이 처음 보일 때 그린다.
//
// - 진입: 해시 #/p/8/<코드>(app/route.ts 의 선택 종목 상태), 검색 상자(로그인 'stock/search', 공개 corp_index.json),
//   관심종목 칩(로그인 'watchlist'). 코드가 없으면 관심종목 첫 종목(로그인) 또는 검색 안내(공개).
// - 머리(늘 보임) + 탭 5개: 개요·재무·리포트·컨센·밸류·공시·지분. 종목을 바꾸면 머리와 탭을 새로 그린다(고른 탭은 그대로).
// - 패널은 섹션 라우트마다 하나 — 한 패널의 실패(404 no_data·500)가 다른 패널을 멈추지 않는다(절대 규칙 4).
// - 같은 종목 안의 같은 요청은 한 번(탭끼리 나눠 쓴다 — TabCtx.get). 종목이 바뀌면 묶음을 버린다.
// - 공개판: DART 패널만 데이터를 그리고 시세·컨센서스·밸류에이션·시나리오 적정가는 자물쇠(요청 없음). 로그인 API 경로
//   문자열은 이 모듈에 없다(키만 — 공개 번들 검사 check-bundle.mjs).
import { getCode, onCode, setCode } from '../app/route';
import type { PageContext, PageModule } from '../app/types';
import type { DataSource, Envelope } from '../data/source';
import { clear, h } from '../ui/dom';
import { panel } from '../ui/panel';
import { matchItems, searchBox, type SearchItem } from '../ui/search';
import { type Seg, seg } from '../ui/seg';
import { type TabDef, type Tabs, tabs } from '../ui/tabs';
import {
  type CorpRow,
  errLine,
  HINT,
  isNotFound,
  memoGet,
  mkPanel,
  type PubFin,
  type S,
  tabGrid,
  type TabCtx,
  unpack,
} from './p8/common';
import { mount as mountConsensus } from './p8/consensus';
import { mount as mountEtfHolders, TITLE as ETF_HOLDERS_TITLE } from './p8/etf_holders';
import { mount as mountFilings } from './p8/filings';
import { mount as mountFinancials } from './p8/financials';
import { KIND_TAG, loadHeader, loadPublicHeader } from './p8/header';
import { loadIndexBadges } from './p8/index_badges';
import { mount as mountInsiders } from './p8/insiders';
import { mount as mountOverhang } from './p8/overhang';
import { mount as mountOverview } from './p8/overview';
import { mount as mountReport } from './p8/report';
import { mount as mountScenario } from './p8/scenario';
import { mount as mountTradeOverlay } from './p8/trade_overlay';
import { mount as mountTradeOverlayPrice } from './p8/trade_overlay_price';
import { mount as mountValuation } from './p8/valuation';

export type TabId = 'overview' | 'fin' | 'report' | 'value' | 'filings';

export const TABS: readonly TabDef<TabId>[] = [
  { id: 'overview', label: '개요' },
  { id: 'fin', label: '재무' },
  { id: 'report', label: '리포트' },
  { id: 'value', label: '컨센·밸류' },
  { id: 'filings', label: '공시·지분' },
];

type TabMount = (t: TabCtx, el: HTMLElement) => Promise<void>;

/**
 * '컨센·밸류' 탭의 로그인 패널 모듈(컨센서스·밸류에이션 밴드·시나리오 적정가 + 밴드 차트 부품). **공개 빌드에서는 번들에서
 * 뺀다** — 공개 등급은 이 탭을 늘 자물쇠 패널(LOCKED_VALUE)로만 그리므로 실행 동작은 같고, 공개 JS gzip 예산
 * (scripts/check-bundle.mjs 72 KB — ADR 0025)을 지킨다. 등급 상수(VITE_KBJ_TIER)는 main.ts 만 읽는 규칙이라 vite 빌드 모드
 * (MODE — 'public'·'login', 시험은 'test')로 가른다. 시험은 두 등급 모두 이 모듈을 싣고, 공개 등급은 실행 중 tier 로
 * 자물쇠 길(lockedValue)을 탄다 — 공개 빌드와 같은 길.
 */
const LOGIN_VALUE: readonly TabMount[] | null =
  import.meta.env.MODE === 'public' ? null : [mountConsensus, mountValuation, mountScenario];

/**
 * '개요' 탭의 '담은 ETF'(P5 ETF 묶음 — p8/etf_holders.ts, 로그인). 공개 빌드에서는 번들에서 빼고 같은 제목의 자물쇠
 * 패널만 그린다(LOGIN_VALUE 와 같은 방식 — 공개 등급은 요청 없음). 고리는 WRAP(docs/p5_design.md §1.8 4).
 */
const LOGIN_ETF: readonly TabMount[] | null = import.meta.env.MODE === 'public' ? null : [mountEtfHolders];
/**
 * P6 '재무' 탭 '수출 겹쳐 보기'의 로그인 한 줄(월말 종가 — p8/trade_overlay_price.ts). 공개 빌드에서는 번들에서 빼고
 * 공개 등급은 공개 패널(p8/trade_overlay.ts — 수출 지수 대 DART 분기 매출)만 그린다(docs/p6_design.md §1.6 1·D-P6-4).
 */
const LOGIN_TRADE: readonly TabMount[] | null = import.meta.env.MODE === 'public' ? null : [mountTradeOverlayPrice];
/** P5 2차(IDX) 머리 배지 한 줄(지수 편입·편출 후보) — 로그인 전용 API 라 공개 빌드는 모듈을 뺀다(LOGIN_VALUE 와 같은 방식) */
const LOGIN_INDEX_BADGES = import.meta.env.MODE === 'public' ? null : loadIndexBadges;

function lockedEtf(t: TabCtx, el: HTMLElement): Promise<void> {
  tabGrid(el, mkPanel(t, { title: ETF_HOLDERS_TITLE, tier: 'login', span: 12, lock: 'etf_issuers' }));
  return Promise.resolve();
}

/** 공개 등급의 '컨센·밸류' 탭 — 로그인 패널과 같은 제목·자물쇠 사유(ui/lock.ts), 요청 없음 */
const LOCKED_VALUE: readonly (readonly [string, string])[] = [
  ['컨센서스', 'consensus'],
  ['밸류에이션 밴드', 'valuation'],
  ['시나리오 적정가', 'valuation'],
];

function lockedValue(t: TabCtx, el: HTMLElement): Promise<void> {
  tabGrid(el, ...LOCKED_VALUE.map(([title, lock]) => mkPanel(t, { title, tier: 'login', span: 12, lock })));
  return Promise.resolve();
}

/**
 * 탭 → 패널 모듈. 한 탭에 모듈이 여럿이면 순서대로 붙인다. 정적 import — 공개 번들 크기 예산(check-bundle.mjs)은
 * 모든 청크의 합이라 탭마다 청크를 나누면 오히려 커진다(청크마다 따로 압축). 탭은 처음 보일 때 그린다(요청도 그때).
 */
function tabModules(id: TabId, t: TabCtx): readonly TabMount[] {
  switch (id) {
    case 'overview':
      return [mountOverview, ...(t.tier === 'login' && LOGIN_ETF ? LOGIN_ETF : [lockedEtf])];
    case 'fin':
      return [mountFinancials, mountTradeOverlay, ...(t.tier === 'login' && LOGIN_TRADE ? LOGIN_TRADE : [])];
    case 'report':
      return [mountReport];
    case 'value':
      return t.tier === 'login' && LOGIN_VALUE ? LOGIN_VALUE : [lockedValue];
    case 'filings':
      return [mountFilings, mountOverhang, mountInsiders];
  }
}

/** 시험용: 페이지가 그리던 것이 다 끝날 때까지(종목을 바꾼 뒤·탭을 고른 뒤) */
const IDLE = new WeakMap<HTMLElement, () => Promise<void>>();
export function settled(root: HTMLElement): Promise<void> {
  return IDLE.get(root)?.() ?? Promise.resolve();
}

/** corp_index.json → 코드별 줄 */
export function corpRows(data: unknown): Map<string, CorpRow> {
  const out = new Map<string, CorpRow>();
  for (const r of unpack<Record<string, unknown>>(data)) {
    const code = typeof r.code === 'string' ? r.code : '';
    if (!/^[0-9A-Z]{6}$/.test(code)) continue;
    out.set(code, {
      code,
      name: typeof r.name === 'string' ? r.name : code,
      market: typeof r.market === 'string' ? r.market : null,
      acc_mt: typeof r.acc_mt === 'number' ? r.acc_mt : null,
      fin: r.fin === true,
      report: r.report === true,
    });
  }
  return out;
}

/** 실패하면 다시 시도할 수 있게 지우는 한 번짜리 */
function once<T>(fn: () => Promise<T>): () => Promise<T> {
  let p: Promise<T> | null = null;
  return () => {
    if (!p) {
      const mine = fn();
      p = mine;
      mine.catch(() => {
        if (p === mine) p = null;
      });
    }
    return p;
  };
}

function tabCtx(ctx: PageContext, code: string, corpIndex: () => Promise<Map<string, CorpRow> | null>): TabCtx {
  const get = memoGet(ctx.source);
  const corp = once(async () => (await corpIndex())?.get(code) ?? null);
  const pub = once(async (): Promise<Envelope<PubFin> | null> => {
    if (ctx.tier !== 'public') return null;
    const idx = await corpIndex();
    if (idx) {
      const row = idx.get(code);
      if (!row?.fin) return null; // 목록에 없거나 재무 파일 없음 — 요청하지 않는다
    }
    try {
      return await get<PubFin>(`fin/${code}`);
    } catch (err) {
      if (isNotFound(err)) return null;
      throw err;
    }
  });
  return {
    tier: ctx.tier,
    code,
    source: ctx.source,
    now: ctx.now,
    ...(ctx.tradingview ? { tradingview: ctx.tradingview } : {}),
    get,
    pub,
    corp,
    open: (c) => setCode(c),
  };
}

function searchFn(source: DataSource, corpIndex: () => Promise<Map<string, CorpRow> | null>): (q: string) => Promise<SearchItem[]> {
  if (source.tier === 'login') {
    return async (q) => {
      const env = await source.get<S['StockSearch']>('stock/search', { q, limit: 20 });
      return env.data.items.map((it) => ({
        code: it.code,
        name: it.name ?? it.code,
        market: it.market,
        note: it.kind && KIND_TAG[it.kind] ? (KIND_TAG[it.kind] ?? null) : it.is_watch ? '관심' : null,
      }));
    };
  }
  return async (q) => {
    const idx = await corpIndex();
    const all: SearchItem[] = [...(idx?.values() ?? [])].map((r) => ({
      code: r.code,
      name: r.name,
      market: r.market,
      note: r.fin ? null : '재무 없음',
    }));
    return matchItems(all, q, 20);
  };
}

async function mount(ctx: PageContext): Promise<() => void> {
  const { tier, root, source } = ctx;
  const pending: Promise<unknown>[] = [];
  let currentTabs: Tabs<TabId> | null = null;
  let tabId: TabId = 'overview';

  // 공개 상장사 목록(corp_index.json) — 페이지에 한 번. 없으면(404) null — 그때는 fin/<코드> 를 바로 찾는다
  const corpIndex = once(async (): Promise<Map<string, CorpRow> | null> => {
    if (tier !== 'public') return null;
    try {
      const env = await source.get<unknown>('corp_index');
      return corpRows(env.data);
    } catch (err) {
      if (isNotFound(err)) return null;
      throw err;
    }
  });

  // ── 종목 선택(검색 + 관심종목 칩) ─────────────────────────
  const pSel = panel(tier, { title: '종목 선택', tier: 'public', span: 12, src: tier === 'public' ? 'DART 상장사 목록' : '' });
  const stage = h('div', { class: 'span-12', data: { stage: 'p8' } });
  root.append(pSel.el, stage);
  const box = searchBox({
    label: '종목 검색',
    placeholder: tier === 'public' ? '상장사 이름 또는 코드' : '종목명 또는 코드',
    search: searchFn(source, corpIndex),
    onSelect: (it) => setCode(it.code),
  });
  const chips = h('div', { data: { section: 'watchlist' } });
  pSel.body.append(box.el, chips);
  if (tier === 'public') pSel.setDef('공개판은 DART 공시·재무만 — 시세·컨센서스·밸류에이션은 로그인 화면.');
  let chipSeg: Seg<string> | null = null;

  const loadWatch = async (): Promise<S['WatchRow'][]> => {
    if (tier !== 'login') return [];
    try {
      const env = await source.get<S['Watchlist']>('watchlist');
      const items = env.data.items;
      clear(chips);
      if (items.length) {
        chipSeg = seg<string>({
          label: '관심종목',
          options: items.map((it) => ({ value: it.code, label: it.name ?? it.code })),
          value: getCode() ?? '',
          onChange: (v) => setCode(v),
        });
        chips.append(chipSeg.el);
      } else chips.append(h('p', { class: 'dim' }, `관심종목 없음 — ${HINT.watchlist}`));
      pSel.setSource({ ...env, notes: [] });
      return items;
    } catch (err) {
      clear(chips);
      chips.append(h('p', { class: 'msg' }, `관심종목: ${errLine(err, HINT.watchlist)}`));
      return [];
    }
  };

  // ── 종목 하나 그리기 ──────────────────────────────────────
  let gen = 0;
  const show = (code: string | null): void => {
    gen += 1;
    const my = gen;
    clear(stage);
    currentTabs = null;
    chipSeg?.set(code ?? '');
    if (!code) {
      const p = panel(tier, { title: '종목', tier: 'public', span: 12 });
      p.showMessage('종목을 검색하세요');
      stage.append(h('div', { class: 'grid' }, p.el));
      return;
    }
    const t = tabCtx(ctx, code, corpIndex);
    const pHead = panel(tier, { title: '종목', tier: 'public', span: 12 });
    const area = h('div', { class: 'span-12', data: { code } });
    stage.append(h('div', { class: 'grid' }, pHead.el, area));
    const tb = tabs<TabId>({
      label: '종목 상세 탭',
      tabs: TABS,
      value: tabId,
      onChange: (id) => {
        tabId = id;
      },
      onFirstShow: async (id, el) => {
        if (my !== gen) return; // 그 사이 종목이 바뀌었다
        await Promise.all(tabModules(id, t).map((m) => m(t, el)));
      },
    });
    currentTabs = tb;
    area.append(tb.el);
    const head = (tier === 'public' ? loadPublicHeader(pHead, t) : loadHeader(pHead, t)).then((known) => {
      if (!known && my === gen) {
        // 모르는 코드 — 탭 대신 한 줄(섹션마다 '아직 없음'을 늘어놓지 않는다)
        clear(area);
        currentTabs = null;
      }
      // 머리를 그린 뒤 지수 배지 한 줄(로그인만 — 머리가 실패해도 배지는 따로)
      if (known && my === gen && tier === 'login' && LOGIN_INDEX_BADGES) return LOGIN_INDEX_BADGES(pHead, t);
      return undefined;
    });
    pending.push(head, tb.settled());
  };

  const off = onCode((c) => {
    show(c);
  });

  let first = getCode();
  if (!first && tier === 'login') {
    const items = await loadWatch();
    first = getCode() ?? items[0]?.code ?? null;
    if (first && !getCode()) setCode(first); // onCode 가 show 를 부른다
    else show(first);
  } else {
    show(first);
    if (tier === 'login') pending.push(loadWatch());
  }

  IDLE.set(root, async () => {
    // 기다리는 사이 새 일(종목 바꿈·탭 고름)이 생기면 다시 — 늘어나지 않을 때까지
    for (;;) {
      const n = pending.length;
      const tb = currentTabs;
      await Promise.all(pending.map((p) => p.catch(() => undefined)));
      if (tb) await tb.settled();
      if (n === pending.length && tb === currentTabs) return;
    }
  });
  await settled(root);
  return () => {
    off();
    box.destroy();
  };
}

const page: PageModule = { mount };
export default page;
