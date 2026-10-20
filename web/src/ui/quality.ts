// 품질·원천 표시(docs/p3_design.md §6.7).
//
// | 상태 | 표시 |
// | estimated | 값 옆 '잠정' 배지 + 점선 밑줄(--est) — P4 의 공시·재무 추정값(오버행 상한·보호예수 추정 해제일·
// |           | 결산월 추정·KIS 단일 추정)은 같은 표시에 글자만 '추정'(EST_LABEL — docs/p4_design.md §6.3) |
// | stale | 흐린 글자 + '기준 HH:MM' 배지(경고색) — 패널 머리는 panel.ts 가 경고색으로 |
// | invalid 행 | 그리지 않는다. 뺀 수는 notes 줄로 |
// | 검산 불가 | '검산 불가: <사유>' — 0 으로 그리지 않는다 |
import type { Envelope, Quality } from '../data/source';
import { h } from './dom';
import { DASH, kstStamp, kstTime, num } from './fmt';

export const QUALITY_LABEL: Record<Quality, string> = {
  ok: '확정',
  stale: '지연',
  estimated: '잠정',
  invalid: '검산 실패',
};

/** estimated 배지 글자 — P3 시세(장중 잠정)와 P4 공시·재무 추정(법정 기한·상한으로 계산한 값) */
export interface EstLabel {
  text: string;
  title: string;
}

/** P3 기본: 장중 잠정 → 마감 뒤 확정 */
export const PRELIM_LABEL: EstLabel = { text: '잠정', title: '장중 잠정 — 마감 뒤 확정' };

/** P4: 출처가 직접 주지 않아 규칙으로 계산한 값(오버행 상한·보호예수 추정 해제일·결산월 추정 등) */
export const EST_LABEL: EstLabel = { text: '추정', title: '추정값 — 원천이 직접 주지 않아 규칙으로 계산' };

/** '추정'(또는 주어진 글자) 배지 하나 — 값이 아니라 패널·행 머리에 붙일 때 */
export function estBadge(label: EstLabel = EST_LABEL): HTMLSpanElement {
  return h('span', { class: 'q-badge', attrs: { title: label.title } }, label.text);
}

/**
 * 값 요소에 품질 표시를 붙인다. invalid 는 그리지 않아야 하므로 false 를 돌려준다(호출자가 행을 뺀다).
 * asOf 는 stale 일 때 '기준 HH:MM' 에 쓴다. est 는 estimated 배지 글자(기본 '잠정' — P4 추정값은 EST_LABEL).
 */
export function markQuality(el: HTMLElement, q: Quality, asOf?: string | null, est: EstLabel = PRELIM_LABEL): boolean {
  if (q === 'invalid') return false;
  if (q === 'estimated') {
    el.classList.add('q-est');
    el.appendChild(estBadge(est));
  } else if (q === 'stale') {
    el.classList.add('q-stale');
    el.appendChild(h('span', { class: 'q-badge stale' }, asOf ? `기준 ${kstTime(asOf)}` : '지연'));
  }
  return true;
}

/** 값 하나를 품질 표시와 함께: <span>값 [잠정]</span> */
export function qValue(
  text: string,
  q: Quality,
  asOf?: string | null,
  cls?: string,
  est: EstLabel = PRELIM_LABEL,
): HTMLSpanElement | null {
  const el = h('span', cls ? { class: cls } : null, text);
  return markQuality(el, q, asOf, est) ? el : null;
}

/** invalid 행을 뺀다. 뺀 수를 같이 돌려준다(notes 줄에 쓰려고). */
export function visibleRows<T extends { quality?: Quality | null }>(rows: readonly T[]): { rows: T[]; invalid: number } {
  const out: T[] = [];
  let invalid = 0;
  for (const r of rows) {
    if (r.quality === 'invalid') invalid += 1;
    else out.push(r);
  }
  return { rows: out, invalid };
}

/** 패널 머리 원천 줄: 'KRX · 10/07 15:30 · 잠정' */
export function sourceText(env: Pick<Envelope<unknown>, 'source' | 'as_of' | 'quality'>): string {
  const parts = [env.source, kstStamp(env.as_of)];
  if (env.quality !== 'ok') parts.push(QUALITY_LABEL[env.quality]);
  return parts.join(' · ');
}

/** 패널 아래 메모 줄(.def). notes 와 뺀 invalid 수. 둘 다 없으면 null. */
export function notesLine(notes: readonly string[], invalidRows = 0): HTMLParagraphElement | null {
  const items = [...notes];
  if (invalidRows > 0) items.push(`검산 실패 ${invalidRows}행 제외`);
  if (items.length === 0) return null;
  return h('p', { class: 'def' }, items.join(' · '));
}

/**
 * 검산 줄. residual 이 null·undefined 면 '검산 불가: 사유'(0 으로 그리지 않는다).
 * 숫자면 '<label> 차이 <b>n</b>' — |n| 이 tol 을 넘으면 경고 표시. tol 은 원천 표기 단위의
 * 반올림 범위(예: KIS 시장 투자자는 칸마다 백만원 반올림 — 0 < |n| ≤ tol 이면 '반올림 범위').
 */
export function checkLine(
  label: string,
  residual: number | null | undefined,
  opts: { reason?: string; unit?: string; digits?: number; tol?: number } = {},
): HTMLDivElement {
  if (residual === null || residual === undefined || !Number.isFinite(residual)) {
    return h('div', { class: 'check', data: { check: 'unavailable' } }, `검산 불가: ${opts.reason ?? '입력 부족'}`);
  }
  const tol = opts.tol ?? 0;
  const bad = Math.abs(residual) > tol;
  const rounded = !bad && residual !== 0;
  return h(
    'div',
    { class: bad ? 'check fail' : 'check', data: { check: bad ? 'fail' : 'ok' } },
    `${label} 차이 `,
    h('b', null, num(residual, opts.digits ?? 0, true)),
    opts.unit ?? '',
    bad ? (tol ? ` (반올림 범위 ±${num(tol, 0)} 밖)` : ' (0 이어야 정상)') : rounded ? ' (원천 단위 반올림 범위)' : '',
  );
}

/** KIS 시장 투자자(FHPTJ04040000)는 칸마다 백만원 반올림 — 시장 2 × 칸 수만큼 어긋날 수 있다. */
export const KIS_MARKET_ROUND_WON = 1_000_000;

/** 시장 투자자 합계 검산의 반올림 허용(원) — 칸 수 × 시장 수 × 백만원. */
export function roundTol(d: { by_market: Record<string, unknown> }, cells: number): number {
  return KIS_MARKET_ROUND_WON * cells * Math.max(1, Object.keys(d.by_market).length);
}

/** 빈 데이터 문구: '아직 없음 — krx.daily 08:05' */
export function emptyText(job?: string, at?: string): string {
  const tail = [job, at].filter(Boolean).join(' ');
  return tail ? `아직 없음 — ${tail}` : '아직 없음';
}

export { DASH };
