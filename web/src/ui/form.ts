// 터미널식 입력 부품 — 숫자·글자·선택·토글·행 추가/삭제·오류 표시(docs/p8_design.md D-P8-23, 페이지 11 편집기·12 입력).
//
// - 문자열 HTML 없음(dom.h 만), style 속성 없음(CSP). 색·글꼴은 기존 토큰(테마 5종이 그대로 따른다).
// - 숫자 칸은 빈칸·잘못된 값을 0 으로 바꾸지 않는다 — value() 가 null 을 돌려주고 칸에 오류를 적는다(절대 규칙 2).
// - 오류 문구는 위치·사유만(서버 422 와 같은 모양 — 입력값을 되풀이하지 않는다).
// - 모바일 폭: 칸은 줄을 바꿔 쌓인다(.form-row flex-wrap) — 가로 넘침 없음.
import { h } from './dom';
import { type Option, type Select, select } from './seg';

export interface FieldError {
  /** JSON 포인터(서버 검증 결과) 또는 칸 이름 */
  pointer: string;
  /** 사유 종류(영문 식별자) */
  reason: string;
}

export interface NumberField {
  readonly el: HTMLLabelElement;
  readonly input: HTMLInputElement;
  /** 유효한 수 또는 null(빈칸·잘못된 값·범위 밖) */
  value(): number | null;
  set(v: number | null): void;
}

let seq = 0;
function uid(prefix: string): string {
  seq += 1;
  return `${prefix}-${seq}`;
}

function errSlot(): HTMLSpanElement {
  return h('span', { class: 'form-err', attrs: { role: 'alert', 'aria-live': 'polite' } });
}

/** 숫자 칸. 범위 밖·정수 아님(integer)·빈칸(required)은 null + 칸 오류. */
export function numberField(opts: {
  label: string;
  value?: number | null;
  min?: number;
  max?: number;
  step?: number;
  integer?: boolean;
  required?: boolean;
  onChange?: (v: number | null) => void;
}): NumberField {
  const err = errSlot();
  const id = uid('num');
  const input = h('input', {
    attrs: {
      id,
      type: 'number',
      inputmode: 'decimal',
      min: opts.min,
      max: opts.max,
      step: opts.step ?? (opts.integer ? 1 : 'any'),
      'aria-describedby': `${id}-err`,
    },
  });
  err.id = `${id}-err`;
  function parse(): number | null {
    const raw = input.value.trim();
    if (raw === '') return null;
    const v = Number(raw);
    if (!Number.isFinite(v)) return null;
    if (opts.integer && !Number.isInteger(v)) return null;
    if (opts.min !== undefined && v < opts.min) return null;
    if (opts.max !== undefined && v > opts.max) return null;
    return v;
  }
  function check(): number | null {
    const v = parse();
    const empty = input.value.trim() === '';
    const bad = (!empty && v === null) || (empty && opts.required === true);
    err.textContent = bad ? (empty ? '필수' : '범위·형식 확인') : '';
    input.setAttribute('aria-invalid', bad ? 'true' : 'false');
    return v;
  }
  input.addEventListener('input', () => opts.onChange?.(check()));
  const el = h('label', { class: 'form-field' }, h('span', { class: 'form-label' }, opts.label), input, err);
  const field: NumberField = {
    el,
    input,
    value: () => check(),
    set(v) {
      input.value = v === null ? '' : String(v);
      check();
    },
  };
  if (opts.value !== undefined) field.set(opts.value);
  return field;
}

export interface TextField {
  readonly el: HTMLLabelElement;
  readonly input: HTMLInputElement;
  value(): string;
  set(v: string): void;
}

/** 글자 칸(앞뒤 공백 제거한 값). */
export function textField(opts: {
  label: string;
  value?: string;
  maxLength?: number;
  placeholder?: string;
  onChange?: (v: string) => void;
}): TextField {
  const input = h('input', {
    attrs: { type: 'text', maxlength: opts.maxLength, placeholder: opts.placeholder, autocomplete: 'off' },
    on: { input: () => opts.onChange?.(input.value.trim()) },
  });
  input.value = opts.value ?? '';
  const el = h('label', { class: 'form-field' }, h('span', { class: 'form-label' }, opts.label), input, errSlot());
  return {
    el,
    input,
    value: () => input.value.trim(),
    set(v) {
      input.value = v;
    },
  };
}

/** 선택 칸 — 기존 select(ui/seg)를 같은 칸 모양으로 감싼다. */
export function choiceField<V extends string>(opts: {
  label: string;
  options: readonly Option<V>[];
  value: V;
  onChange?: (v: V) => void;
}): Select<V> {
  const sel = select(opts);
  sel.el.classList.add('form-field');
  return sel;
}

export interface Toggle {
  readonly el: HTMLButtonElement;
  value(): boolean;
  set(v: boolean): void;
}

/** 켜짐/꺼짐 토글 — role=switch + aria-checked. 글자로도 상태를 보인다(색만으로 구분하지 않는다). */
export function toggle(opts: { label: string; value: boolean; onChange?: (v: boolean) => void }): Toggle {
  let on = opts.value;
  const state = h('span', { class: 'toggle-state' });
  const el = h(
    'button',
    {
      class: 'toggle',
      attrs: { type: 'button', role: 'switch', 'aria-label': opts.label },
      on: {
        click: () => {
          set(!on);
          opts.onChange?.(on);
        },
      },
    },
    h('span', { class: 'toggle-label' }, opts.label),
    state,
  );
  function set(v: boolean): void {
    on = v;
    el.setAttribute('aria-checked', on ? 'true' : 'false');
    state.textContent = on ? '켜짐' : '꺼짐';
  }
  set(on);
  return { el, value: () => on, set };
}

export interface Rows<T> {
  readonly el: HTMLDivElement;
  items(): T[];
  set(items: readonly T[]): void;
}

/** 행 추가/삭제 목록(조건 행 등). 최소·최대 개수 밖으로는 버튼이 꺼진다. */
export function rows<T>(opts: {
  label: string;
  items: readonly T[];
  render: (item: T, index: number, update: (next: T) => void) => HTMLElement;
  make: () => T;
  min?: number;
  max?: number;
  onChange?: (items: T[]) => void;
}): Rows<T> {
  let list: T[] = [...opts.items];
  const min = opts.min ?? 0;
  const max = opts.max ?? Number.POSITIVE_INFINITY;
  const body = h('ol', { class: 'rows-list' });
  const add = h(
    'button',
    {
      class: 'rows-add',
      attrs: { type: 'button' },
      on: {
        click: () => {
          if (list.length >= max) return;
          list = [...list, opts.make()];
          draw();
          opts.onChange?.(list);
        },
      },
    },
    '+ 행 추가',
  );
  function draw(): void {
    body.replaceChildren(
      ...list.map((item, i) => {
        const del = h(
          'button',
          {
            class: 'rows-del',
            attrs: { type: 'button', 'aria-label': `${i + 1}번째 행 삭제`, disabled: list.length <= min },
            on: {
              click: () => {
                if (list.length <= min) return;
                list = list.filter((_, j) => j !== i);
                draw();
                opts.onChange?.(list);
              },
            },
          },
          '−',
        );
        const update = (next: T): void => {
          list = list.map((x, j) => (j === i ? next : x));
          opts.onChange?.(list);
        };
        return h('li', { class: 'rows-item' }, opts.render(item, i, update), del);
      }),
    );
    add.disabled = list.length >= max;
  }
  draw();
  const el = h('div', { class: 'rows', attrs: { role: 'group', 'aria-label': opts.label } }, body, add);
  return {
    el,
    items: () => [...list],
    set(items) {
      list = [...items];
      draw();
    },
  };
}

/** 서버 검증 오류 목록(위치·사유만). 오류가 없으면 비운다. */
export function formErrors(el: HTMLElement, issues: readonly FieldError[]): void {
  el.classList.add('form-errors');
  el.setAttribute('role', 'alert');
  el.replaceChildren(
    ...issues.map((i) => h('div', { class: 'form-err' }, h('code', null, i.pointer || '/'), ` ${i.reason}`)),
  );
}

/** 칸들을 한 줄로(좁은 폭에서는 줄바꿈). */
export function formRow(...children: HTMLElement[]): HTMLDivElement {
  return h('div', { class: 'form-row' }, children);
}
