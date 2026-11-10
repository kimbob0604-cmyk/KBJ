// 첫 화면 '시장' 구역 카드(로그인 전용): 코스피·코스닥 지수 카드, 코스피200 선물 카드, 상승·보합·하락 띠.
//
// - 지수 카드: 이름 → 큰 값(qValue)·등락률 알약 → 기준(장중 HH:MM / MM/DD 마감) → 20거래일 추이 → 투자자 순매수 칸
//   (개인·외국인·기관·프로그램). 투자자는 장중 1분 누적(잠정)이 마감 일별보다 새 날이면 장중, 아니면 마감(서버가 고른다).
//   프로그램은 마감 일별(`home` program 카드 — 장중 누적은 아직 API 에 없다). 날짜가 다르면 그 날짜를 적는다.
// - 선물 카드: 전일 대비 등락률 칸이 없다(전일 정산가를 API 에 싣지 않는다 — 카드 notes) → 알약은 '—'.
// - 상승·보합·하락은 **코스피+코스닥 합산**(시장별 값 없음) — 그래서 지수 카드 안이 아니라 띠 한 줄로 따로 그린다.
//   띠 폭은 화면 비례일 뿐(새 지표가 아니다 — 숫자는 종목 수 그대로 적는다).
import type { components } from '../../api/types.gen';
import type { Envelope } from '../../data/source';
import { type Child, h } from '../../ui/dom';
import { DASH, kstTime, num, shortDate } from '../../ui/fmt';
import { qValue } from '../../ui/quality';
import { sparkline } from '../../ui/svg';
import type { HomePanel } from './home_defs';
import { bigLine, type Cards, type Home, joinSub, pill, put, renderCard, signedWon } from './home_ui';

type S = components['schemas'];
type Tile = S['IndexTile'];
type Inv = S['HomeInvestorNet'];
type ProgramBar = S['ProgramDayBar'];

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

/** 투자자 순매수 칸(개인·외국인·기관 [+ 프로그램]) */
export function investorGrid(v: Inv | null, program?: { bar: ProgramBar | null; why: string | null }): HTMLElement {
  if (!v && !program) return h('p', { class: 'hinv-cap' }, '투자자 순매수 없음');
  const cells = INV_KEYS.map(([k, label]) => invCell(label, v ? signedWon(v[k], v.quality, v.as_of) : DASH));
  if (program) {
    const b = program.bar;
    const late = b && v && b.date !== v.date ? ` ${shortDate(b.date)}` : '';
    cells.push(
      invCell(
        '프로그램',
        b ? h('span', null, signedWon(b.net, b.quality), late ? h('small', null, late) : null) : DASH,
        b ? `프로그램 순매수 ${shortDate(b.date)} 마감(일별 — 장중 누적 없음)` : (program.why ?? undefined),
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

function programOf(cards: Cards, key: 'kospi' | 'kosdaq'): { bar: ProgramBar | null; why: string | null } {
  const c = cards.program;
  if (c.status !== 'ok' || !c.data) return { bar: null, why: c.message ?? '프로그램 순매수 없음' };
  const bar = c.data[key];
  return { bar: bar && bar.quality !== 'invalid' ? bar : null, why: bar ? '검산 실패' : '프로그램 순매수 없음' };
}

/** 지수 카드 메모 나누기 — '상승' 이 든 줄은 띠로 */
function notesOf(cards: Cards, band: boolean): string[] {
  return (cards.indices.notes ?? []).filter((n) => n.includes('상승') === band);
}

function drawIndex(p: HomePanel, env: Envelope<Home>, key: 'kospi' | 'kosdaq'): void {
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
      t.spark.length ? sparkline(t.spark, { label: `${t.name ?? t.code} ${t.spark.length}거래일 추이` }) : null,
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
    put(p.body,
      bigLine(
        price ?? DASH,
        h('small', { class: 'unit' }, 'pt'),
        h('span', { class: 'pill dim', attrs: { title: '전일 대비 등락률 칸 없음 — 전일 정산가를 아직 싣지 않는다' } }, DASH),
      ),
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
    p.setMeta(`${shortDate(b.date)} 마감 · 코스피+코스닥 합산(시장별 값 없음)`);
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

export function drawMarket(m: MarketPanels, env: Envelope<Home>): void {
  drawIndex(m.kospi, env, 'kospi');
  drawIndex(m.kosdaq, env, 'kosdaq');
  drawFutures(m.futures, env);
  drawBand(m.band, env);
}
