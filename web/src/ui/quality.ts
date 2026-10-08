// 품질·원천 표시(docs/p3_design.md §6.7).
//
// | 상태 | 표시 |
// | estimated | 값 옆 '잠정' 배지 + 점선 밑줄(--est) |
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

/**
 * 값 요소에 품질 표시를 붙인다. invalid 는 그리지 않아야 하므로 false 를 돌려준다(호출자가 행을 뺀다).
 * asOf 는 stale 일 때 '기준 HH:MM' 에 쓴다.
 */
export function markQuality(el: HTMLElement, q: Quality, asOf?: string | null): boolean {
  if (q === 'invalid') return false;
  if (q === 'estimated') {
    el.classList.add('q-est');
    el.appendChild(h('span', { class: 'q-badge', attrs: { title: '장중 잠정 — 마감 뒤 확정' } }, '잠정'));
  } else if (q === 'stale') {
    el.classList.add('q-stale');
    el.appendChild(h('span', { class: 'q-badge stale' }, asOf ? `기준 ${kstTime(asOf)}` : '지연'));
  }
  return true;
}

/** 값 하나를 품질 표시와 함께: <span>값 [잠정]</span> */
export function qValue(text: string, q: Quality, asOf?: string | null, cls?: string): HTMLSpanElement | null {
  const el = h('span', cls ? { class: cls } : null, text);
  return markQuality(el, q, asOf) ? el : null;
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
 * 숫자면 '<label> 차이 <b>n</b>' — 0 이 아니면 경고 표시.
 */
export function checkLine(
  label: string,
  residual: number | null | undefined,
  opts: { reason?: string; unit?: string; digits?: number } = {},
): HTMLDivElement {
  if (residual === null || residual === undefined || !Number.isFinite(residual)) {
    return h('div', { class: 'check', data: { check: 'unavailable' } }, `검산 불가: ${opts.reason ?? '입력 부족'}`);
  }
  const bad = residual !== 0;
  return h(
    'div',
    { class: bad ? 'check fail' : 'check', data: { check: bad ? 'fail' : 'ok' } },
    `${label} 차이 `,
    h('b', null, num(residual, opts.digits ?? 0, true)),
    opts.unit ?? '',
    bad ? ' (0 이어야 정상)' : '',
  );
}

/** 빈 데이터 문구: '아직 없음 — krx.daily 08:05' */
export function emptyText(job?: string, at?: string): string {
  const tail = [job, at].filter(Boolean).join(' ');
  return tail ? `아직 없음 — ${tail}` : '아직 없음';
}

export { DASH };
