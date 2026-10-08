// 상단 띠 칩(미리보기 .chip) — 이름·값·꼬리표(잠정·마감·준비 중·자물쇠). 띠 내용(app/ribbon.ts)은 W2 가 채운다.
import type { Tier } from '../data/source';
import { h } from './dom';

export type ChipState = 'live' | 'estimated' | 'closed' | 'pending' | 'locked';

export interface ChipOptions {
  key: string;
  label: string;
  /** 값 문구(이미 형식화된 것). pending·locked 면 무시 */
  value?: string;
  /** 값 색 클래스(up·down·warn·dim) */
  cls?: string;
  state?: ChipState;
  /** pending 일 때 단계(예 'P5') */
  phase?: string;
  /** 원천·시각(마우스 올리면) — 예 'KRX · 10/07 15:30' */
  title?: string;
  /** 칩 등급 — 보는 쪽이 공개고 이 값이 login 이면 자물쇠 */
  tier?: Tier;
}

const TAIL: Partial<Record<ChipState, string>> = { estimated: '잠정', closed: '마감' };

export function chip(viewer: Tier, o: ChipOptions): HTMLDivElement {
  const locked = o.state === 'locked' || (viewer === 'public' && o.tier === 'login');
  const state: ChipState = locked ? 'locked' : (o.state ?? 'live');
  let value: string;
  if (state === 'locked') value = '로그인 필요';
  else if (state === 'pending') value = o.phase ? `준비 중(${o.phase})` : '준비 중';
  else value = o.value ?? '—';
  const tail = TAIL[state];
  return h(
    'div',
    {
      class: `chip ${state === 'locked' ? 'locked' : state === 'pending' ? 'pending' : ''}`.trim(),
      data: { key: o.key, state },
      attrs: { title: locked ? undefined : o.title },
    },
    h('span', { class: 'k' }, o.label, locked ? h('span', { class: 'sr-only' }, ' (로그인 등급)') : null),
    h('span', { class: `v ${state === 'live' || state === 'estimated' || state === 'closed' ? (o.cls ?? '') : ''}`.trim() }, value),
    tail ? h('span', { class: 't' }, tail) : null,
  );
}
