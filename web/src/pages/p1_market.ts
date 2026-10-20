// 페이지 1 시장(docs/p3_design.md §4.4·§6.5) — 지수 타일·시장 거래대금·업종 히트맵·시장폭·시장 투자자 수급.
//
// - 로그인: `market/summary`(지수·거래대금·시장폭 — 한 번에), `market/sectors`(기간 1·5·20), `flows/investors`.
// - 공개: 지수 자리는 TradingView 위젯(DATA_TIERS §2 — 시세는 TradingView 로만), 신용잔고·예탁금은 공개 파일
//   `credit`(공개 내보내기 credit.json — 마지막 값·직전 대비만), 나머지는 자물쇠(요청 없음).
// - 신용잔고 · 예탁금 · 펀드 자금(금투협 종합통계 — 공개 등급 원천): 로그인은 `market/credit`(20일 추이까지).
// - 보조 타일: VKOSPI(krx.vkospi)·원/달러(macro.*)는 서버가 값을 보내면 그리고, 없으면 '준비 중'. K200 야간선물(P7)은 아직 원천 없음.
// - 금액은 원 단위 → 화면에서만 억·조. 값이 없으면 '—'(0 으로 그리지 않는다). 검산 불가는 사유와 함께.
// - P5 묶음 F2(이 파일은 F2 만 고친다 — 패널 고리): 장중 투자자 수급(1분 선·카드 7 — ./p1/flows_minute.ts)·
//   프로그램매매(일별 60거래일·상위 20 — ./p1/program.ts). 로그인 전용(KIS — 공개판은 자물쇠), 60초 갱신.
import type { components } from '../api/types.gen';
import type { PageContext, PageModule } from '../app/types';
import { DataError, type Envelope, type Quality, worstQuality } from '../data/source';
import { h } from '../ui/dom';
import { DASH, eok, joEok, num, pct, shortDate, signClass, times } from '../ui/fmt';
import { type Panel, panel } from '../ui/panel';
import { checkLine, qValue, roundTol, visibleRows } from '../ui/quality';
import { seg } from '../ui/seg';
import { barChart, heatColor, level, sparkline } from '../ui/svg';
import { table } from '../ui/table';
import { MINUTE_REFRESH_MS, MINUTE_TITLE, mountMinute } from './p1/flows_minute';
import { DAILY_TITLE, mountProgram, TOP_TITLE } from './p1/program';

// 공개 빌드에서는 P5 로그인 모듈을 번들에서 뺀다(p8_stock LOGIN_VALUE 와 같은 방식 — 공개 등급은 이 줄에 오기 전에
// 자물쇠로 돌아가므로 실행 동작은 같고, 공개 JS gzip 예산 scripts/check-bundle.mjs 72 KB(ADR 0025) 를 지킨다. WRAP)
const LOGIN_F2 = import.meta.env.MODE === 'public' ? null : { mountMinute, mountProgram };
const NOOP = (): Promise<void> => Promise.resolve();

type S = components['schemas'];
type Summary = S['MarketSummary'];
type Tile = S['IndexTile'];
type CreditLine = S['CreditLine'];
type ApiChip = S['Chip'];

/** 장중 위젯 새로 고침 — 10분 슬롯 + 30초 여유(§6.9) */
export const REFRESH_MS = 630_000;

export const INVESTOR_LABEL: Record<string, string> = {
  foreign: '외국인',
  institution: '기관',
  other_corp: '기타법인',
  individual: '개인',
};

/** 원천이 아직 없을 수 있는 타일(§6.5) — 서버가 같은 code 의 타일을 보내면 그 타일로 갈아 끼운다 */
export const PENDING_TILES: readonly { code: string; name: string; phase: string }[] = [
  { code: 'K200N', name: 'K200 야간선물', phase: 'P7' },
  { code: 'VKOSPI', name: 'VKOSPI', phase: 'P7' },
  { code: 'USDKRW', name: '원/달러', phase: 'P5' },
];

/** 공개판 지수 자리 TradingView 심볼 [확인 필요 — R14] */
const TV_INDEX = [
  { symbol: 'KRX:KOSPI', label: '코스피' },
  { symbol: 'KRX:KOSDAQ', label: '코스닥' },
  { symbol: 'KRX:KOSPI200', label: '코스피200' },
];

// ── 공용 도우미(페이지 2·4·10 도 이 모듈에서 가져다 쓴다 — 같은 일을 두 벌 두지 않는다) ──

/** 불러오기 실패 표시: 404(아직 없음)는 서버 안내 문구(없으면 패널의 예정 작업)를 보이고, 그 밖 오류는 이 패널에만(응답 본문 없이). */
export function failed(p: Panel, err: unknown, hint: string): void {
  if (err instanceof DataError && err.status === 404)
    p.showMessage(err.serverMessage ?? `아직 없음 — ${hint}`);
  else p.showError(err);
}

/** 패널마다 마지막 요청 번호 — 늦게 온 옛 응답(빠르게 토글을 바꿨을 때)이 새 응답을 덮지 않게 */
const fillSeq = new WeakMap<Panel, number>();

/**
 * 봉투를 받아 패널에 그린다. draw 가 뺀 invalid 행 수를 돌려주면 원천 줄을 봉투로 채우고,
 * null 을 돌려주면 draw 가 원천 줄을 직접 채운 것이다. 같은 패널에 더 새 요청이 나갔으면 이 응답은 버린다.
 * also = 같은 응답을 쓰는 다른 패널(실패하면 그 패널에도 같은 오류 줄).
 */
export async function fill<T>(
  p: Panel,
  load: () => Promise<Envelope<T>>,
  draw: (env: Envelope<T>) => number | null,
  hint: string,
  also: readonly Panel[] = [],
): Promise<void> {
  if (p.locked || p.pending) return;
  const seq = (fillSeq.get(p) ?? 0) + 1;
  fillSeq.set(p, seq);
  try {
    const env = await load();
    if (fillSeq.get(p) !== seq) return;
    p.clearBody();
    const invalid = draw(env);
    if (invalid !== null) p.setSource(env, invalid);
  } catch (err) {
    if (fillSeq.get(p) !== seq) return;
    for (const x of [p, ...also]) failed(x, err, hint);
  }
}

/** 필터 줄(.filters)을 패널 머리와 본문 사이에 — 본문을 다시 그려도 남는다 */
export function filters(p: Panel, ...children: Node[]): HTMLElement {
  const el = h('div', { class: 'filters' }, children);
  p.el.insertBefore(el, p.body);
  return el;
}

/** 종목 이름 칸: 이름 + 작은 코드 */
export function nameCell(name: string | null | undefined, code: string): HTMLElement {
  return h('span', null, name ?? code, h('small', null, code));
}

/** 메모 줄 나누기: 낱말이 든 것만 그 패널로 */
function notesWith(notes: readonly string[], words: readonly string[]): string[] {
  return notes.filter((n) => words.some((w) => n.includes(w)));
}

// ── 지수 타일 ──────────────────────────────────────────────

export function indexTile(t: Tile): HTMLElement {
  const name = t.name ?? t.code;
  const v = qValue(num(t.value, 2), t.quality, t.as_of);
  return h(
    'div',
    { class: 'tile', data: { code: t.code } },
    // 장중이 아니면 기준일을 보인다 — 묵은 값(전 거래일 이전)이 오늘 값처럼 보이지 않게(2026-10-20 VKOSPI)
    h('div', { class: 'n' }, name, t.live ? ' · 장중' : ` · ${shortDate(t.as_of)}`),
    h('div', { class: 'p' }, v ?? DASH),
    h('div', { class: signClass(t.chg_pct) }, pct(t.chg_pct)),
    sparkline(t.spark, { label: `${name} 추이` }),
  );
}

function drawIndices(p: Panel, env: Envelope<Summary>): void {
  const { rows, invalid } = visibleRows(env.data.indices);
  p.body.append(
    h(
      'div',
      { class: 'idx' },
      rows.map(indexTile),
      // 원천이 없는 보조 타일(PENDING_TILES)은 그리지 않는다 — 서버가 값을 보내면 그 타일이 나타난다
      // (미완성처럼 보이는 '준비 중' 자리 — 사용자 요청 2026-10-20)
    ),
  );
  const q: Quality = rows.length ? worstQuality(rows.map((r) => r.quality)) : env.quality;
  p.setSource({ ...env, quality: q, notes: notesWith(env.notes, ['지수']) }, invalid);
}

// ── 신용잔고 · 예탁금 · 펀드 자금(금투협 종합통계) ─────────────

export const CREDIT_TITLE = '신용잔고 · 예탁금 · 펀드 자금';
const CREDIT_HINT = 'market_stats.kofia 13:30(D+1)';

/** 신용 타일 하나: 이름·기준일 / 마지막 값(조·억) / 직전 대비 / 추이. 값이 없으면 '—'(0 으로 그리지 않는다) */
export function creditTile(ln: CreditLine): HTMLElement {
  const v = ln.value === null || ln.quality === null ? null : qValue(joEok(ln.value), ln.quality, ln.as_of);
  const per = ln.freq === 'M' ? '전월' : '전일';
  const n = ln.spark.length;
  return h(
    'div',
    { class: 'tile', data: { key: ln.key }, attrs: { title: ln.source } },
    h('div', { class: 'n' }, ln.label, ln.date ? ` · ${shortDate(ln.date)}` : ''),
    h('div', { class: 'p' }, v ?? DASH),
    h(
      'div',
      { class: signClass(ln.change), attrs: ln.prev_date ? { title: `${shortDate(ln.prev_date)} 대비` } : undefined },
      ln.change === null ? DASH : `${per} 대비 ${joEok(ln.change, true)}`,
    ),
    n ? sparkline(ln.spark.map((p) => p.value), { label: `${ln.label} ${n}${ln.freq === 'M' ? '개월' : '영업일'} 추이` }) : null,
  );
}

/** 줄들을 그리고 원천 줄을 채운다(줄마다의 사유는 '이름: 사유'로 notes 줄에) */
function drawCreditLines(p: Panel, env: Envelope<unknown>, lines: readonly CreditLine[], extra: readonly string[] = []): void {
  p.body.append(h('div', { class: 'idx' }, lines.map(creditTile)));
  const got = lines.filter((l) => l.value !== null && l.quality !== null);
  const q: Quality = got.length ? worstQuality(got.map((l) => l.quality ?? 'invalid')) : env.quality;
  const notes = lines.filter((l) => l.note).map((l) => `${l.label}: ${l.note ?? ''}`);
  p.setSource({ ...env, quality: q, notes: [...env.notes, ...notes, ...extra] });
}

export function drawCredit(p: Panel, env: Envelope<S['CreditPanel']>): null {
  drawCreditLines(p, env, env.data.lines);
  return null;
}

/** 공개 파일 credit.json 칩 단위 → 원 배수(모르는 단위는 값을 비운다 — 서버 market_credit.UNIT_WON 과 같다) */
const UNIT_WON: Record<string, number> = { 원: 1, 천원: 1e3, 백만원: 1e6, 억원: 1e8, 십억원: 1e9, 조원: 1e12 };
const PUBLIC_CREDIT_KEYS = ['credit_balance', 'investor_deposit'] as const;

/** 공개 칩(credit.json — API 띠 칩과 같은 모양) → 신용 줄(추이 없음) */
export function creditLineOfChip(c: ApiChip): CreditLine {
  const d = c.detail as Record<string, unknown>;
  const k = typeof d.unit === 'string' ? UNIT_WON[d.unit] : undefined;
  const toWon = (x: unknown): number | null =>
    k === undefined || typeof x !== 'number' || !Number.isFinite(x) ? null : Math.round(x * k);
  const value = c.value && typeof c.value.value === 'number' ? toWon(c.value.value) : null;
  return {
    key: c.key === 'credit_balance' ? 'credit_balance' : 'investor_deposit',
    label: c.label,
    freq: 'D',
    value,
    date: typeof d.date === 'string' ? d.date : null,
    change: value === null ? null : toWon(d.change),
    prev_date: typeof d.prev_date === 'string' ? d.prev_date : null,
    spark: [],
    source: c.value?.source ?? '',
    as_of: c.value?.as_of ?? null,
    quality: value === null ? null : (c.value?.quality ?? null),
    note: [c.note, k === undefined ? '단위 확인 필요 — 값을 비운다' : null].filter(Boolean).join(' · ') || null,
  };
}

export function drawCreditPublic(p: Panel, env: Envelope<{ chips?: ApiChip[] }>): null {
  const chips = Array.isArray(env.data.chips) ? env.data.chips : [];
  const lines = PUBLIC_CREDIT_KEYS.flatMap((key) => {
    const c = chips.find((x) => x.key === key && x.tier === 'public');
    return c ? [creditLineOfChip(c)] : [];
  });
  if (lines.length === 0) {
    p.showMessage(`아직 없음 — ${CREDIT_HINT}`);
    return null;
  }
  drawCreditLines(p, env, lines, ['공개판은 마지막 값·직전 대비만(20일 추이는 로그인)']);
  return null;
}

// ── 시장 거래대금 ──────────────────────────────────────────

export function kv(items: readonly [string, Node | string | null][]): HTMLElement {
  return h(
    'dl',
    { class: 'kv' },
    items.map(([k, v]) => h('div', null, h('dt', null, k), h('dd', null, v ?? DASH))),
  );
}

export function drawTurnover(p: Panel, env: Envelope<Summary>): void {
  const t = env.data.turnover;
  if (!t?.today) {
    p.showMessage('아직 없음 — market.close_collect 15:35');
    return;
  }
  const basis = t.basis === 'intraday_index' ? '장중(지수 기준)' : '마감 확정';
  p.body.append(
    kv([
      ['오늘', qValue(joEok(t.today.value), t.today.quality, t.today.as_of)],
      ['20일 평균', joEok(t.avg20)],
      ['평균 대비', h('span', { class: t.ratio === null ? 'dim' : t.ratio >= 1 ? 'up' : 'down' }, times(t.ratio))],
      ['기준', basis],
    ]),
    barChart(
      t.series.map((d) => (d.quality === 'invalid' || d.value === null ? null : d.value / 1e8)),
      {
        label: `시장 거래대금 ${t.series.length}영업일 추이(억)`,
        ref: t.avg20 === null ? null : t.avg20 / 1e8,
        caption: t.series.length ? `${shortDate(t.series[0]?.date)} ~ ${shortDate(t.series.at(-1)?.date)}` : undefined,
      },
    ),
  );
  p.setSource(
    {
      source: t.today.source,
      as_of: t.today.as_of,
      quality: t.today.quality,
      notes: [...t.tags, ...notesWith(env.notes, ['거래대금'])],
    },
    t.series.filter((d) => d.quality === 'invalid').length,
  );
}

// ── 시장폭 ─────────────────────────────────────────────────

export function drawBreadth(p: Panel, env: Envelope<Summary>): void {
  const b = env.data.breadth;
  if (!b) {
    p.showMessage('아직 없음 — market.close_collect 15:35');
    return;
  }
  const share = (n: number): number => (b.total > 0 ? (n / b.total) * 100 : 0);
  const q: Quality = b.quality ?? env.quality;
  p.body.append(
    h(
      'div',
      { class: 'levels' },
      level('상승', share(b.up), num(b.up), 'up'),
      level('보합', share(b.flat), num(b.flat)),
      level('하락', share(b.down), num(b.down), 'down'),
      b.unknown > 0 ? level('미지', share(b.unknown), num(b.unknown), 'dim') : null,
      level(
        '20일선 위',
        b.above_ma20_pct ?? 0,
        b.above_ma20_pct === null ? DASH : `${num(b.above_ma20_pct, 1)}% (${num(b.above_ma20_n)}/${num(b.ma20_base)})`,
      ),
    ),
    kv([
      [
        '신고가',
        qValue(b.newhigh_pct === null ? num(b.newhigh_n) : `${num(b.newhigh_n)} (${num(b.newhigh_pct, 1)}%)`, q),
      ],
      ['상한가', num(b.limit_up)],
      ['하한가', num(b.limit_down)],
      ['유니버스', num(b.total)],
    ]),
  );
  p.setSource({ source: b.source, as_of: env.as_of, quality: q, notes: b.notes });
}

// ── 업종 히트맵 ────────────────────────────────────────────

type Period = '1' | '5' | '20';

export function drawSectors(p: Panel, env: Envelope<S['SectorHeat']>): number {
  const { rows, invalid } = visibleRows(env.data.cells);
  if (rows.length === 0) {
    p.showMessage('업종 지수 코드 준비 중 — config/markets.yaml sector_indices [확인 필요]');
    return invalid;
  }
  p.body.append(
    h(
      'div',
      { class: 'heat', attrs: { role: 'list', 'aria-label': `업종 등락률 ${env.data.period}일` } },
      rows.map((c) =>
        h(
          'div',
          {
            class: 'cell',
            css: { background: heatColor(c.chg_pct) },
            attrs: { role: 'listitem', title: [c.source, c.note].filter(Boolean).join(' · ') || undefined },
            data: { code: c.code },
          },
          c.name ?? c.code,
          h('b', null, qValue(pct(c.chg_pct), c.quality, c.as_of) ?? DASH),
          h('span', null, joEok(c.turnover)),
        ),
      ),
    ),
  );
  return invalid;
}

// ── 시장 투자자 수급 ───────────────────────────────────────

const INVESTORS = ['foreign', 'institution', 'other_corp', 'individual'] as const;

function sumRecent(recent: readonly S['DayTotals'][], key: string, n: number): number | null {
  const days = recent.filter((d) => d.quality !== 'invalid').slice(-n);
  if (days.length === 0) return null;
  let s = 0;
  for (const d of days) {
    const v = d.by_investor[key];
    if (v === null || v === undefined) return null;
    s += v;
  }
  return s;
}

// 시장 열 — 서버는 KIS 업종 코드(0001·1001)를 준다(market_investor_daily.market_code)
const MARKET_LABEL: Readonly<Record<string, string>> = {
  KOSPI: '코스피',
  KOSDAQ: '코스닥',
  '0001': '코스피',
  '1001': '코스닥',
};

export function drawInvestors(p: Panel, env: Envelope<S['InvestorTotals']>): null {
  const d = env.data;
  const markets = Object.keys(d.by_market).sort();
  const nRecent = Math.min(5, d.recent.length);
  const cols = [
    { key: 'inv', label: '투자자', align: 'l' as const, cell: (k: string) => INVESTOR_LABEL[k] ?? k },
    {
      key: 'today',
      label: `${shortDate(d.date)}(억)`,
      cell: (k: string) => eok(d.by_investor[k], 0, true),
      cls: (k: string) => signClass(d.by_investor[k]),
    },
    {
      key: 'recent',
      label: `${nRecent}일 합(억)`,
      cell: (k: string) => eok(sumRecent(d.recent, k, 5), 0, true),
      cls: (k: string) => signClass(sumRecent(d.recent, k, 5)),
    },
    ...markets.map((m) => ({
      key: m,
      label: `${MARKET_LABEL[m] ?? m}(억)`,
      cell: (k: string) => eok(d.by_market[m]?.[k], 0, true),
      cls: (k: string) => signClass(d.by_market[m]?.[k]),
    })),
  ];
  p.body.append(
    table({ caption: '시장 투자자별 순매수', columns: cols, rows: INVESTORS }).el,
    checkLine('검산 ① 4구분 합', d.check1_residual, {
      reason:
        d.by_investor.other_corp === null || d.by_investor.other_corp === undefined
          ? '기타법인 미제공'
          : '4구분 입력 부족',
      unit: '원',
      tol: roundTol(d, 4),
    }),
  );
  p.setSource({ ...env, notes: [...new Set([...env.notes, ...d.notes])] });
  return null;
}

// ── 페이지 ─────────────────────────────────────────────────

async function mount(ctx: PageContext): Promise<void> {
  const { tier, root } = ctx;
  const pIdx = panel(tier, {
    title: '지수',
    tier: 'public',
    span: 8,
    src: tier === 'public' ? 'TradingView 제공' : '',
  });
  const pTurn = panel(tier, { title: '시장 거래대금', tier: 'login', span: 4, lock: 'krx_daily' });
  const pHeat = panel(tier, { title: '업종 히트맵', tier: 'login', span: 8, lock: 'krx' });
  const pBreadth = panel(tier, { title: '시장폭', tier: 'login', span: 4, lock: 'quote_based' });
  const pInv = panel(tier, { title: '시장 투자자 수급', tier: 'login', span: 6, lock: 'investor' });
  // 금투협 종합통계(공개 등급) — 공개는 credit.json(마지막 값), 로그인은 market/credit(20일 추이)
  const pCredit = panel(tier, { title: CREDIT_TITLE, tier: 'public', span: 6 });
  // P5 F2 — 장중 투자자 수급·프로그램매매(KIS — 로그인 전용)
  const pMin = panel(tier, { title: MINUTE_TITLE, tier: 'login', span: 12, lock: 'kis' });
  const pPgmDaily = panel(tier, { title: DAILY_TITLE, tier: 'login', span: 6, lock: 'kis' });
  const pPgmTop = panel(tier, { title: TOP_TITLE, tier: 'login', span: 6, lock: 'kis' });
  root.append(pIdx.el, pTurn.el, pHeat.el, pBreadth.el, pInv.el, pCredit.el, pMin.el, pPgmDaily.el, pPgmTop.el);

  pTurn.setDef(
    '코스피+코스닥 주식(ETF·ETN·리츠 제외) 거래대금. 막대 = 일별, 점선 = 20일 평균. 장중 값은 지수 누적 기준(잠정).',
  );
  pCredit.setDef(
    '금투협 종합통계(D+1 13시 공표). 신용잔고 = 신용거래융자 전체(없으면 유가+코스닥). 예탁금 = 투자자예탁금. 펀드 자금 = 펀드 순자산. 선 = 최근 20개 값.',
  );
  pBreadth.setDef(
    '상승·보합·하락은 보드 유니버스 종목 수. 신고가 = 보드 종가 기준 라벨. 상·하한가 = 등락률 ±29.5% 이상.',
  );

  if (tier === 'public') {
    // 시세 자리는 TradingView 위젯(공개 빌드에서만 ctx.tradingview 가 있다)
    if (ctx.tradingview) {
      const tv = ctx.tradingview;
      const box = h('div', { class: 'idx' });
      pIdx.body.append(box);
      for (const s of TV_INDEX) {
        const cell = h('div', { class: 'tile', attrs: { role: 'figure', 'aria-label': `${s.label} 차트` } });
        box.append(cell);
        tv(cell, 'mini-symbol-overview', { symbol: s.symbol, width: '100%', height: 160, dateRange: '1M' });
      }
    } else {
      pIdx.showMessage('TradingView 위젯을 불러오지 못함');
    }
    await fill(
      pCredit,
      () => ctx.source.get<{ chips?: ApiChip[] }>('credit'),
      (env) => drawCreditPublic(pCredit, env),
      CREDIT_HINT,
    );
    return;
  }

  const period = { v: '1' as Period };
  const summaryPanels = [pIdx, pTurn, pBreadth];
  const loadSummary = async (): Promise<void> => {
    try {
      const env = await ctx.source.get<Summary>('market/summary');
      for (const p of summaryPanels) p.clearBody();
      drawIndices(pIdx, env);
      drawTurnover(pTurn, env);
      drawBreadth(pBreadth, env);
    } catch (err) {
      // 세 패널이 한 응답을 쓴다 — 같은 오류 줄을 각 패널 안에(다른 패널은 계속)
      for (const p of summaryPanels) failed(p, err, 'market.close_collect 15:35');
    }
  };
  const loadSectors = (): Promise<void> =>
    fill(
      pHeat,
      () => ctx.source.get<S['SectorHeat']>('market/sectors', { period: period.v }),
      (env) => drawSectors(pHeat, env),
      'market.intraday 장중 10분',
    );
  filters(
    pHeat,
    seg<Period>({
      label: '히트맵 기간',
      options: [
        { value: '1', label: '1일' },
        { value: '5', label: '5일' },
        { value: '20', label: '20일' },
      ],
      value: period.v,
      onChange: (v) => {
        period.v = v;
        void loadSectors();
      },
    }).el,
  );
  const loadInvestors = (): Promise<void> =>
    fill(
      pInv,
      () => ctx.source.get<S['InvestorTotals']>('flows/investors'),
      (env) => drawInvestors(pInv, env),
      'market.close_collect 15:35',
    );

  const loadCredit = (): Promise<void> =>
    fill(
      pCredit,
      () => ctx.source.get<S['CreditPanel']>('market/credit'),
      (env) => drawCredit(pCredit, env),
      CREDIT_HINT,
    );

  const loadMinute = LOGIN_F2 ? LOGIN_F2.mountMinute(ctx, pMin) : NOOP;
  const loadProgram = LOGIN_F2 ? LOGIN_F2.mountProgram(ctx, pPgmDaily, pPgmTop) : NOOP;

  await Promise.all([loadSummary(), loadSectors(), loadInvestors(), loadCredit(), loadMinute(), loadProgram()]);
  // 첫 불러오기 중에 앱이 내려갔으면(격자가 문서에서 빠짐) 주기 작업을 걸지 않는다
  if (root.isConnected) {
    ctx.every(REFRESH_MS, () => {
      void loadSummary();
      void loadSectors();
      void loadInvestors();
      void loadCredit();
    });
    // F2 — 60초 폴링(웹소켓 아님, F2 §3.6)
    ctx.every(MINUTE_REFRESH_MS, () => {
      void loadMinute();
      void loadProgram();
    });
  }
}

const page: PageModule = { mount };
export default page;
