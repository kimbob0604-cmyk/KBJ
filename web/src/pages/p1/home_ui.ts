// 첫 화면 카드 안 부품(로그인 전용 — 공개 번들에서 빠진다): 순위 목록·등락률 알약·부호 금액·카드 상태 그리기.
//
// - 숫자는 ui/fmt.ts 로만 반올림한다. 값이 없으면 '—'(0 으로 그리지 않는다). 색은 등락에만(--up·--down).
// - 값마다 품질 표시: 행에 quality 가 있으면 그 값에 잠정·지연 배지(markQuality), invalid 행은 그리지 않고 수를 notes 로.
// - 종목 이름은 단추 — 누르면 페이지 8 종목 상세(app/route.ts openStock).
import type { components } from '../../api/types.gen';
import { isStockCode, openStock } from '../../app/route';
import { DataError, type Envelope, type Quality } from '../../data/source';
import { type Child, h } from '../../ui/dom';
import { DASH, joEok, pct, signClass } from '../../ui/fmt';
import { markQuality } from '../../ui/quality';
import type { HomePanel } from './home_defs';

type S = components['schemas'];
export type Home = S['Home'];
export type Cards = S['HomeCards'];

/** 카드 공통 칸(HomeCard[T]) */
export interface CardOf<T> {
  status: 'ok' | 'no_data' | 'error';
  page: number;
  keys: string[];
  source?: string | null;
  as_of?: string | null;
  quality?: Quality | null;
  notes?: string[];
  message?: string | null;
  error?: string | null;
  data?: T | null;
}

/** 서버 오류 코드 → 문구(본문·내부 문구는 서버가 싣지 않는다 — 코드만) */
const CARD_ERROR: Readonly<Record<string, string>> = {
  unavailable: '원천을 읽지 못함',
  unknown_code: '모르는 코드',
  internal: '서버 처리 오류',
};

export function cardErrorText(code: string | null | undefined): string {
  return `불러오지 못함 — ${code ? (CARD_ERROR[code] ?? code) : '사유 없음'} (이 카드만)`;
}

/** draw 가 돌려주는 것: 뺀 invalid 행 수, 또는 그 수 + 원천 줄을 카드 봉투 대신 쓸 칸(띠처럼 값의 원천이 다를 때) */
export type DrawResult = number | { invalid?: number; source?: string; quality?: Quality; notes?: string[] };

/**
 * 카드 상태대로 그린다: ok → draw(돌려준 수 = 뺀 invalid 행), no_data → 서버 안내, error → 코드 문구.
 * draw 가 던지면(예상 밖 모양) 그 카드만 오류 줄 — 다른 카드는 계속(절대 규칙 4). 오류는 콘솔에 남긴다(panel.showError).
 */
export function renderCard<T>(p: HomePanel, c: CardOf<T> | undefined, env: Envelope<Home>, draw: (d: T) => DrawResult): void {
  p.clearBody();
  p.setMeta('');
  if (!c) {
    p.showError(new DataError(cardErrorText('internal')));
    return;
  }
  if (c.status === 'error') {
    p.setSrcText('');
    p.showError(new DataError(cardErrorText(c.error)));
    return;
  }
  if (c.status === 'no_data' || c.data === null || c.data === undefined) {
    p.setSrcText('');
    p.showMessage(c.message ?? '아직 없음');
    return;
  }
  try {
    const r = draw(c.data);
    const o = typeof r === 'number' ? { invalid: r } : r;
    p.setSource(
      {
        source: o.source ?? c.source ?? env.source,
        as_of: c.as_of ?? env.as_of,
        quality: o.quality ?? c.quality ?? env.quality,
        notes: o.notes ?? c.notes ?? [],
      },
      o.invalid ?? 0,
    );
  } catch (err) {
    p.setMeta('');
    p.showError(err);
  }
}

/** 등락률 알약(오른쪽 정렬·부호 색). quality 가 있으면 배지 */
export function pill(v: number | null | undefined, q?: Quality | null, digits = 2): HTMLSpanElement {
  const el = h('span', { class: `pill ${signClass(v)}` }, pct(v, digits));
  if (q) markQuality(el, q);
  return el;
}

/** 부호 붙은 금액(조·억) + 부호 색. quality 가 있으면 배지 */
export function signedWon(v: number | null | undefined, q?: Quality | null, asOf?: string | null): HTMLSpanElement {
  const el = h('span', { class: `sw ${signClass(v)}` }, joEok(v, true));
  if (q && v !== null && v !== undefined) markQuality(el, q, asOf);
  return el;
}

/** 종목 이름 — 코드가 올바르면 단추(페이지 8), 아니면 글자 */
export function stockName(name: string | null | undefined, code: string | null | undefined): HTMLElement {
  const label = name ?? code ?? DASH;
  if (!isStockCode(code)) return h('span', { class: 'nm-t' }, label);
  return h(
    'button',
    {
      class: 'link nm-t',
      attrs: { type: 'button', title: `${label} 종목 상세` },
      data: { open: code },
      on: { click: () => void openStock(code) },
    },
    label,
  );
}

export interface RankRow {
  rank?: number | null;
  name: Child;
  sub?: Child;
  right?: Child;
  data?: Record<string, string>;
}

/** 순위 목록: 순위(1~3위 진하게) → 이름 + 흐린 보조 줄 → 오른쪽 값 */
export function rankList(rows: readonly RankRow[], label: string): HTMLOListElement {
  return h(
    'ol',
    { class: 'hlist', attrs: { 'aria-label': label } },
    rows.map((r) =>
      h(
        'li',
        { data: r.data },
        h('span', { class: r.rank !== null && r.rank !== undefined && r.rank <= 3 ? 'rk top' : 'rk' }, r.rank ?? ''),
        h('span', { class: 'nm' }, r.name, r.sub ? h('small', null, r.sub) : null),
        h('span', { class: 'v' }, r.right ?? null),
      ),
    ),
  );
}

/** 빈 목록 안내(카드 안 — 0 행은 오류가 아니다) */
export function emptyLine(text: string): HTMLParagraphElement {
  return h('p', { class: 'msg' }, text);
}

/** 큰 값 한 줄(값 + 단위 + 꼬리) */
export function bigLine(value: Child, ...rest: Child[]): HTMLDivElement {
  return h('div', { class: 'hbig' }, h('span', { class: 'hv' }, value), ...rest);
}

/** 시장 이름 */
export const MARKET_KO: Readonly<Record<string, string>> = { KOSPI: '코스피', KOSDAQ: '코스닥', KONEX: '코넥스' };
export const marketKo = (m: string | null | undefined): string | null => (m ? (MARKET_KO[m] ?? m) : null);

/** 보조 줄 조각을 ' · ' 로(빈 것은 뺀다) */
export function joinSub(...parts: (string | null | undefined | false)[]): string {
  return parts.filter((x): x is string => typeof x === 'string' && x !== '' && x !== DASH).join(' · ');
}
