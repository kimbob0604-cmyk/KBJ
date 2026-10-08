// 선택 버튼 묶음(미리보기 .seg)과 선택 상자. 키보드: 버튼은 Tab·Enter·Space(기본 동작).
import { h } from './dom';

export interface Option<V extends string> {
  value: V;
  label: string;
}

export interface Seg<V extends string> {
  readonly el: HTMLDivElement;
  value(): V;
  set(value: V): void;
}

/** 하나만 고르는 버튼 묶음 — role=group + aria-pressed. */
export function seg<V extends string>(opts: {
  label: string;
  options: readonly Option<V>[];
  value: V;
  onChange?: (v: V) => void;
}): Seg<V> {
  let current = opts.value;
  const buttons = opts.options.map((o) =>
    h(
      'button',
      {
        attrs: { type: 'button', 'aria-pressed': o.value === current ? 'true' : 'false' },
        data: { value: o.value },
        on: {
          click: () => {
            if (o.value === current) return;
            set(o.value);
            opts.onChange?.(o.value);
          },
        },
      },
      o.label,
    ),
  );
  function set(v: V): void {
    current = v;
    for (const b of buttons) b.setAttribute('aria-pressed', b.dataset.value === v ? 'true' : 'false');
  }
  const el = h('div', { class: 'seg', attrs: { role: 'group', 'aria-label': opts.label } }, buttons);
  return { el, value: () => current, set };
}

export interface Select<V extends string> {
  readonly el: HTMLLabelElement;
  readonly select: HTMLSelectElement;
  value(): V;
}

/** 이름표 붙은 선택 상자(미리보기 .filters label). */
export function select<V extends string>(opts: {
  label: string;
  options: readonly Option<V>[];
  value: V;
  onChange?: (v: V) => void;
}): Select<V> {
  const sel = h(
    'select',
    { on: { change: () => opts.onChange?.(sel.value as V) } },
    opts.options.map((o) => h('option', { attrs: { value: o.value, selected: o.value === opts.value } }, o.label)),
  );
  sel.value = opts.value;
  return { el: h('label', null, opts.label, sel), select: sel, value: () => sel.value as V };
}
