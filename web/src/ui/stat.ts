// 요약 숫자 카드(주식봇식 — 큰 숫자 한 줄 + 이름·기준일 + 한 줄 해석 + 구간 꼬리표). 패널 맨 위에 '지금 값'을 먼저
// 보이고 차트는 아래에. 격자는 지수 타일(.idx)을 그대로 쓴다 — 폭이 좁으면 줄을 바꾼다(가로 넘침 없음).
//
// - 값이 없으면 '—'(0 으로 그리지 않는다). tone 은 색만(up·down·warn·dim) — 뜻은 zone 글자로도 적는다(색맹 고려).
import { h } from './dom';
import { DASH } from './fmt';

export type StatTone = 'up' | 'down' | 'warn' | 'dim' | null;

export interface StatCard {
  label: string;
  value: string | null;
  /** 이름 옆 작은 글자(기준일 등) */
  asof?: string | null;
  /** 값 아래 한 줄(전일 대비·해석) */
  sub?: string | null;
  subTone?: StatTone;
  /** 구간 꼬리표(예 '과열') */
  zone?: string | null;
  tone?: StatTone;
  /** 마우스를 올리면 보이는 설명 */
  title?: string;
}

export function statGrid(cards: readonly StatCard[]): HTMLDivElement {
  return h(
    'div',
    { class: 'idx stats' },
    cards.map((c) =>
      h(
        'div',
        {
          class: 'tile stat',
          data: { stat: c.label },
          attrs: c.title ? { title: c.title } : undefined,
        },
        h('span', { class: 'n' }, c.label, c.asof ? h('small', null, ` ${c.asof}`) : null),
        h(
          'span',
          { class: c.tone ? `p ${c.tone}` : 'p' },
          c.value ?? DASH,
          c.zone ? h('span', { class: c.tone ? `zone ${c.tone}` : 'zone' }, c.zone) : null,
        ),
        c.sub ? h('span', { class: c.subTone ? `s ${c.subTone}` : 's' }, c.sub) : null,
      ),
    ),
  );
}
