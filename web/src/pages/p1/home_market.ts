// 첫 화면 '시장' 구역 카드(로그인 전용): 코스피·코스닥 지수 카드, 코스피200 선물 카드, 상승·보합·하락 띠.
//
// - 지수 카드: 이름 → 큰 값(qValue)·등락률 알약 → 기준(장중 HH:MM / MM/DD 마감) → 차트(기간 버튼 당일·3개월·1년) →
//   그 시장 상승·보합·하락 → 투자자 순매수 칸(개인·외국인·기관·프로그램). 투자자·프로그램은 장중 누적(잠정)이 마감 일별보다
//   새 날이면 장중, 아니면 마감(서버가 고른다 — 프로그램은 program 카드의 '지금' 값). 날짜가 다르면 그 날짜를 적는다.
//   차트: 당일 = 묶음의 장중 10분 점(없으면 20거래일 추이로 대신하고 그렇게 적는다), 3개월·1년 = `market/index-chart`
//   (250거래일 한 번 — 3개월은 마지막 63일). 처음 누를 때만 부르고 지수마다 기억한다(고른 기간은 갱신 뒤에도 남는다).
//   차트 요청이 실패하면 차트 자리에만 문구 — 카드의 다른 값은 그대로(절대 규칙 4).
// - 선물 카드: 전일 대비 등락률 = 근월물 직전 거래일 KRX 정산가 대비(서버 — 없으면 '—' 와 카드 notes).
// - 띠: 상승·보합·하락 **코스피+코스닥 합산**. 띠 폭은 화면 비례일 뿐(숫자는 종목 수 그대로 적는다).
import type { components } from '../../api/types.gen';
import type { Envelope, Params } from '../../data/source';
import { type Child, h } from '../../ui/dom';
import { DASH, kstTime, num, shortDate } from '../../ui/fmt';
import { qValue } from '../../ui/quality';
import { seg } from '../../ui/seg';
import { sparkline } from '../../ui/svg';
import type { HomePanel } from './home_defs';
import { bigLine, type Cards, type Home, joinSub, pill, put, renderCard, signedWon } from './home_ui';

type S = components['schemas'];
type Tile = S['IndexTile'];
type Inv = S['HomeInvestorNet'];
type ProgramNow = S['HomeProgramNow'];
type UpDown = S['HomeUpDown'];
type Chart = S['IndexChart'];

/** 지수 카드 차트 기간 */
export type Period = 'day' | '3m' | '1y';
const PERIODS: readonly { value: Period; label: string }[] = [
  { value: 'day', label: '당일' },
  { value: '3m', label: '3개월' },
  { value: '1y', label: '1년' },
];
/** 3개월 = 마지막 63거래일, 1년 = 250거래일(경로 기본값 — 요청 하나) */
export const CHART_DAYS = 250;
const DAYS_3M = 63;

/** 시장 구역 상태 — 고른 기간(지수마다)과 받은 일별 차트(지수·날짜마다 한 번) */
export interface MarketState {
  period: Record<'kospi' | 'kosdaq', Period>;
  charts: Map<string, Promise<Envelope<Chart>>>;
  get: <T>(key: string, params?: Params) => Promise<Envelope<T>>;
}

export function newMarketState(get: MarketState['get']): MarketState {
  return { period: { kospi: 'day', kosdaq: 'day' }, charts: new Map(), get };
}

const INV_KEYS = [
  ['individual', '개인'],
  ['foreign', '외국인'],
  ['institution', '기관'],
] as const;

/** 지수 타일의 기준: 장중이면 '장중 HH:MM', 아니면 'MM/DD 마감' */
export function tileBasis(t: Pick<Tile, 'live' | 'as_of'>): string {
  return t.live ? `장중 ${kstTime(t.as_of)}` : `${shortDate(t.as_of)} 마감`;
}

/** 투자자 순매수 기준 한 줄 */
export function invBasis(v: Inv): string {
  return v.basis === 'minute' ? `투자자 순매수 · 장중 누적 ${kstTime(v.as_of)}` : `투자자 순매수 · ${shortDate(v.date)} 마감`;
}

function invCell(label: string, value: Child, title?: string): HTMLElement {
  return h('div', { data: { investor: label }, attrs: title ? { title } : undefined }, h('dt', null, label), h('dd', null, value));
}

/** 프로그램 '지금' 값 설명: 장중 누적 HH:MM / MM/DD 마감 */
export function programBasis(b: Pick<ProgramNow, 'basis' | 'date' | 'as_of'>): string {
  return b.basis === 'intraday' ? `장중 누적 ${kstTime(b.as_of)}` : `${shortDate(b.date)} 마감`;
}

/** 투자자 순매수 칸(개인·외국인·기관 [+ 프로그램]) */
export function investorGrid(v: Inv | null, program?: { bar: ProgramNow | null; why: string | null }): HTMLElement {
  if (!v && !program) return h('p', { class: 'hinv-cap' }, '투자자 순매수 없음');
  const cells = INV_KEYS.map(([k, label]) => invCell(label, v ? signedWon(v[k], v.quality, v.as_of) : DASH));
  if (program) {
    const b = program.bar;
    const late = b && v && b.date !== v.date ? ` ${shortDate(b.date)}` : '';
    cells.push(
      invCell(
        '프로그램',
        b ? h('span', null, signedWon(b.net, b.quality, b.as_of), late ? h('small', null, late) : null) : DASH,
        b ? `프로그램 순매수 ${programBasis(b)}` : (program.why ?? undefined),
      ),
    );
  }
  return h(
    'div',
    null,
    h('p', { class: 'hinv-cap' }, v ? invBasis(v) : '투자자 순매수 없음'),
    h('dl', { class: `hinv n${cells.length}` }, cells),
  );
}

function programOf(cards: Cards, key: 'kospi' | 'kosdaq'): { bar: ProgramNow | null; why: string | null } {
  const c = cards.program;
  if (c.status !== 'ok' || !c.data) return { bar: null, why: c.message ?? '프로그램 순매수 없음' };
  const bar = c.data[key === 'kospi' ? 'kospi_now' : 'kosdaq_now'];
  return { bar: bar && bar.quality !== 'invalid' ? bar : null, why: bar ? '검산 실패' : '프로그램 순매수 없음' };
}

/** 한 시장 상승·보합·하락 한 줄(색은 등락에만) */
export function updownLine(u: UpDown | null | undefined): HTMLElement | null {
  if (!u) return null;
  return h(
    'p',
    { class: 'hsub', data: { updown: '' } },
    h('span', { class: 'up' }, `상승 ${num(u.up)}`),
    ` · 보합 ${num(u.flat)} · `,
    h('span', { class: 'down' }, `하락 ${num(u.down)}`),
    u.unknown ? ` · 미지 ${num(u.unknown)}` : '',
    ` · ${shortDate(u.date)} 마감`,
  );
}

/** 차트 한 장(점이 없으면 null). 크기는 요소에 직접 — 공용 CSS 를 고치면 공개 번들의 파일 이름(해시)까지 바뀐다 */
function chartSvg(values: readonly number[], label: string): SVGSVGElement | null {
  if (!values.length) return null;
  const svg = sparkline(values, { label });
  svg.style.height = '56px';
  svg.style.margin = '0 0 4px';
  return svg;
}

/** 지수 카드 차트 — 기간 버튼 + 그림 + 설명 줄. 3개월·1년은 처음 누를 때 부른다 */
function indexChart(x: S['HomeIndex'], key: 'kospi' | 'kosdaq', st: MarketState, day: string): HTMLElement[] {
  const t = x.tile;
  const name = t.name ?? t.code;
  const box = h('div', { class: 'hchart', data: { chart: key }, css: { 'min-height': '60px' } });
  const cap = h('p', { class: 'hsub' });
  let my = 0;
  const show = (svg: SVGSVGElement | null, text: string): void => {
    box.replaceChildren(...(svg ? [svg] : []));
    cap.textContent = text;
  };
  const paint = (p: Period): void => {
    my += 1;
    const ticket = my;
    if (p === 'day') {
      const d = x.today;
      if (d && d.points.length) {
        const first = d.points[0]?.t ?? '';
        const last = d.points[d.points.length - 1]?.t ?? '';
        show(chartSvg(d.points.map((q) => q.v), `${name} 당일 10분 추이`), `당일 10분 · ${shortDate(d.date)} ${first}~${last}`);
      } else {
        show(chartSvg(t.spark, `${name} ${t.spark.length}거래일 추이`), `당일 10분 값 없음 — ${t.spark.length}거래일 추이`);
      }
      return;
    }
    const ck = `${t.code}|${day}`;
    let req = st.charts.get(ck);
    if (!req) {
      req = st.get<Chart>('market/index-chart', { code: t.code, days: CHART_DAYS });
      st.charts.set(ck, req);
      req.catch(() => st.charts.delete(ck)); // 실패는 기억하지 않는다 — 다시 누르면 다시 부른다
    }
    show(null, '불러오는 중…');
    req.then(
      (env) => {
        if (ticket !== my) return;
        const pts = p === '3m' ? env.data.points.slice(-DAYS_3M) : env.data.points;
        const a = pts[0];
        const b = pts[pts.length - 1];
        show(
          chartSvg(pts.map((q) => q.close), `${name} ${pts.length}거래일 추이`),
          a && b ? `${p === '3m' ? '3개월' : '1년'} · ${num(pts.length)}거래일 · ${shortDate(a.date)}~${shortDate(b.date)} 종가` : '일봉 없음',
        );
      },
      (err: unknown) => {
        if (ticket !== my) return;
        show(null, `차트를 불러오지 못함 — ${err instanceof Error ? err.message : String(err)}`);
      },
    );
  };
  const tabs = seg<Period>({
    label: `${name} 차트 기간`,
    options: PERIODS,
    value: st.period[key],
    onChange: (v) => {
      st.period[key] = v;
      paint(v);
    },
  });
  tabs.el.classList.add('htabs');
  paint(st.period[key]);
  return [box, tabs.el, cap];
}

/** 지수 카드 메모 나누기 — '상승' 이 든 줄은 띠로 */
function notesOf(cards: Cards, band: boolean): string[] {
  return (cards.indices.notes ?? []).filter((n) => n.includes('상승') === band);
}

function drawIndex(p: HomePanel, env: Envelope<Home>, key: 'kospi' | 'kosdaq', st: MarketState): void {
  const cards = env.data.cards;
  renderCard(p, cards.indices, env, (d) => {
    const x = d[key];
    if (!x) {
      p.showMessage('이 지수 값 없음 — 카드 notes 참고');
      return 0;
    }
    const t = x.tile;
    if (t.quality === 'invalid') {
      p.showMessage('검산 실패 — 그리지 않는다');
      return 1;
    }
    p.setMeta(tileBasis(t));
    put(p.body,
      bigLine(qValue(num(t.value, 2), t.quality, t.as_of) ?? DASH, pill(t.chg_pct)),
      ...indexChart(x, key, st, env.data.date),
      updownLine(x.updown),
      investorGrid(x.investors, programOf(cards, key)),
    );
    p.el.dataset.code = t.code;
    // 상승·보합·하락 메모는 띠 카드로(지수 카드에는 지수·투자자 메모만)
    return { invalid: 0, notes: notesOf(cards, false) };
  });
}

const SESSION_KO = { day: '주간', night: '야간' } as const;

function drawFutures(p: HomePanel, env: Envelope<Home>): void {
  renderCard(p, env.data.cards.futures, env, (d) => {
    p.setMeta(
      joinSub(
        d.session ? SESSION_KO[d.session] : null,
        d.session_open === null ? null : d.session_open ? `장중 ${kstTime(d.cycle_at)}` : `마감 ${kstTime(d.cycle_at)}`,
        d.stale ? '갱신 지연' : null,
        '근월물',
      ),
    );
    const price = d.price === null || !d.price_quality ? null : qValue(num(d.price, 2), d.price_quality, d.cycle_at);
    const n = d.night;
    const chg = pill(d.chg_pct, d.chg_pct === null ? null : d.price_quality);
    chg.title =
      d.prev_settle === null || !d.prev_settle_date
        ? '전일 대비 등락률 없음 — 직전 거래일 KRX 정산가가 아직 없다'
        : `전일(${shortDate(d.prev_settle_date)}) 정산가 ${num(d.prev_settle, 2)}pt 대비`;
    put(p.body,
      bigLine(price ?? DASH, h('small', { class: 'unit' }, 'pt'), chg),
      n && n.quality !== 'invalid'
        ? h(
            'p',
            { class: 'hline', data: { code: n.code } },
            `${n.name ?? '야간'} `,
            qValue(num(n.value, 2), n.quality, n.as_of) ?? DASH,
            ' ',
            pill(n.chg_pct),
            h('small', null, ` ${tileBasis(n)} · 주간 마지막 값 대비`),
          )
        : null,
      investorGrid(d.investors),
    );
    return 0;
  });
}

/** 상승·보합·하락 띠(코스피+코스닥 합산) */
function drawBand(p: HomePanel, env: Envelope<Home>): void {
  const c = env.data.cards.indices;
  renderCard(p, c, env, (d) => {
    const b = d.breadth;
    if (!b) {
      p.showMessage('상승·하락 종목 수 없음 — market.close_collect 15:35');
      return 0;
    }
    p.setMeta(`${shortDate(b.date)} 마감 · 코스피+코스닥 합산(시장별은 지수 카드)`);
    const all = b.up + b.flat + b.down + b.unknown;
    const w = (n: number): string => `${all > 0 ? (n / all) * 100 : 0}%`;
    const label = `상승 ${num(b.up)} 보합 ${num(b.flat)} 하락 ${num(b.down)}${b.unknown ? ` 미지 ${num(b.unknown)}` : ''}`;
    put(p.body,
      h(
        'div',
        { class: 'hband', attrs: { role: 'img', 'aria-label': label } },
        h('span', { class: 'b-up', css: { width: w(b.up) } }),
        h('span', { class: 'b-flat', css: { width: w(b.flat) } }),
        h('span', { class: 'b-down', css: { width: w(b.down) } }),
        b.unknown ? h('span', { class: 'b-unk', css: { width: w(b.unknown) } }) : null,
      ),
      h(
        'p',
        { class: 'hband-txt' },
        h('span', { class: 'up' }, `상승 ${num(b.up)}`),
        ` · 보합 ${num(b.flat)} · `,
        h('span', { class: 'down' }, `하락 ${num(b.down)}`),
        b.unknown ? ` · 미지 ${num(b.unknown)}` : '',
        ` · 등락률 아는 종목 ${num(b.total)}`,
      ),
    );
    // 띠의 원천·품질은 시장폭 값(지수 카드와 다를 수 있다) — 카드 봉투 대신 띠 값으로(시각은 카드 시각)
    return { source: b.source, quality: b.quality ?? undefined, notes: notesOf(env.data.cards, true) };
  });
}

export interface MarketPanels {
  kospi: HomePanel;
  kosdaq: HomePanel;
  futures: HomePanel;
  band: HomePanel;
}

export function drawMarket(m: MarketPanels, env: Envelope<Home>, st: MarketState): void {
  drawIndex(m.kospi, env, 'kospi', st);
  drawIndex(m.kosdaq, env, 'kosdaq', st);
  drawFutures(m.futures, env);
  drawBand(m.band, env);
}
