// 키-값 표(정의 목록 dl.kv) — 핵심 지표 카드·종목 머리·오버행 요약처럼 '이름: 값' 묶음(docs/p4_design.md §1.2·§6).
//
// 규칙(§6.3)
// - 값이 없으면(null·undefined) '—'. 0 으로 채우지 않는다.
// - 적용 불가(applicable=false)는 값 대신 사유 문구(na) — 흐린 글자(.na). 사유 표(na_reason → 한국어)는 페이지가 정한다.
// - 품질: estimated 는 '추정'(기본 — P4 추정값) 또는 주어진 글자 배지, stale 은 '기준 HH:MM', invalid 는 값을 그리지 않고 '검산 실패'.
// - 자물쇠(locked): 공개판에서 로그인 칸 — 값을 받지도 그리지도 않는다(호출자가 값 자리에 아무것도 넘기지 않는다).
import type { Quality } from '../data/source';
import { h } from './dom';
import { DASH } from './fmt';
import { EST_LABEL, type EstLabel, markQuality } from './quality';

export interface KvItem {
  label: string;
  /** 이미 형식화한 값(글자) 또는 요소. null·undefined 는 '—' */
  value?: Node | string | null;
  /** 값 글자의 class(부호 색 up·down·dim·warn) */
  cls?: string;
  /** 적용 불가 사유 문구 — 있으면 value 대신 이것을 그린다 */
  na?: string | null;
  quality?: Quality | null;
  /** stale 일 때 '기준 HH:MM' */
  asOf?: string | null;
  /** estimated 배지 글자(기본 '추정') */
  est?: EstLabel;
  /** 공개판 자물쇠 칸 — '로그인' 만 그린다 */
  locked?: boolean;
  /** 이름 아래 작은 설명(정의·단위) */
  hint?: string;
}

/** [이름, 값] 짝도 받는다(P3 페이지 1 의 kv 와 같은 모양). */
export type KvInput = KvItem | readonly [string, Node | string | null | undefined];

function norm(x: KvInput): KvItem {
  if (Array.isArray(x)) {
    const [label, value] = x as readonly [string, Node | string | null | undefined];
    return { label, value: value ?? null };
  }
  return x as KvItem;
}

function valueEl(it: KvItem): HTMLElement {
  if (it.locked) return h('dd', { class: 'locked' }, h('span', { class: 'dim' }, '로그인'));
  if (it.na) return h('dd', { class: 'na' }, it.na);
  if (it.quality === 'invalid') return h('dd', { class: 'na' }, '검산 실패');
  const v = it.value;
  const isEmpty = v === null || v === undefined || v === '';
  const span = h('span', it.cls && !isEmpty ? { class: it.cls } : null, isEmpty ? DASH : v);
  if (!isEmpty && it.quality) markQuality(span, it.quality, it.asOf ?? null, it.est ?? EST_LABEL);
  return h('dd', null, span);
}

export interface KvOptions {
  /** 항목이 없을 때 이름 칸 글자(기본 '항목'). dl 에는 aria-label 을 달지 않는다(역할 없는 요소 — axe) */
  label?: string;
  /** 'cards'(기본 — 칸마다 테두리) 또는 'rows'(이름·값 두 열, 좁은 패널) */
  layout?: 'cards' | 'rows';
}

/** 정의 목록 하나. 항목이 없으면 '해당 없음' 한 줄. */
export function kv(items: readonly KvInput[], opts: KvOptions = {}): HTMLDListElement {
  const cls = opts.layout === 'rows' ? 'kv rows' : 'kv';
  const dl = h('dl', { class: cls });
  if (items.length === 0) {
    dl.append(h('div', { class: 'empty' }, h('dt', null, opts.label ?? '항목'), h('dd', { class: 'na' }, '해당 없음')));
    return dl;
  }
  for (const raw of items) {
    const it = norm(raw);
    dl.append(
      h(
        'div',
        { data: { key: it.label } },
        h('dt', null, it.label, it.hint ? h('small', null, it.hint) : null),
        valueEl(it),
      ),
    );
  }
  return dl;
}
