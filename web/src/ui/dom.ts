// DOM 만들기 도우미 — 문자열 HTML(innerHTML)을 쓰지 않는다(XSS 면 제거, eslint 가 innerHTML 을 막는다).
//
// 로그인 CSP 는 `style-src 'self'` 라 style 속성(setAttribute('style'))은 막힌다. 위치·폭 같은 동적 값은
// `css` 옵션(CSSOM — el.style.setProperty)으로만 넣는다. CSSOM 조작은 CSP 가 막지 않는다.

/** 자식: 노드·글자·숫자, 또는 그것들의 (중첩) 배열. null·undefined·false 는 건너뛴다 */
export type Child = Node | string | number | null | undefined | false | readonly Child[];
export type AttrValue = string | number | boolean | null | undefined;

export interface HOptions {
  /** class 이름(공백 구분) */
  class?: string;
  /** data-* 속성 */
  data?: Record<string, string | number>;
  /** 동적 CSS 속성 — CSSOM 으로 넣는다(CSP 안전) */
  css?: Record<string, string>;
  /** 이벤트 처리기 */
  on?: { [K in keyof HTMLElementEventMap]?: (ev: HTMLElementEventMap[K]) => void };
  /** 그 밖의 속성. true = 빈 속성, false·null·undefined = 넣지 않음 */
  attrs?: Record<string, AttrValue>;
}

function setAttrs(el: Element, attrs: Record<string, AttrValue> | undefined): void {
  if (!attrs) return;
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'style') throw new Error('style 속성은 쓰지 않는다 — css 옵션(CSSOM)을 쓴다');
    if (k.startsWith('on')) throw new Error(`이벤트 속성(${k})은 쓰지 않는다 — on 옵션을 쓴다`);
    if (v === false || v === null || v === undefined) continue;
    el.setAttribute(k, v === true ? '' : String(v));
  }
}

export function append(parent: Node, children: readonly Child[]): void {
  for (const c of children) {
    if (Array.isArray(c)) {
      append(parent, c as readonly Child[]);
      continue;
    }
    if (c === null || c === undefined || c === false) continue;
    parent.appendChild(typeof c === 'string' || typeof c === 'number' ? document.createTextNode(String(c)) : (c as Node));
  }
}

/** HTML 요소를 만든다. 글자는 textContent 로만 들어간다. */
export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  opts?: HOptions | null,
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  if (opts) {
    if (opts.class) el.className = opts.class;
    if (opts.data) for (const [k, v] of Object.entries(opts.data)) el.dataset[k] = String(v);
    if (opts.css) for (const [k, v] of Object.entries(opts.css)) el.style.setProperty(k, v);
    setAttrs(el, opts.attrs);
    if (opts.on) {
      for (const [type, fn] of Object.entries(opts.on) as [string, unknown][]) {
        if (typeof fn === 'function') el.addEventListener(type, fn as EventListener);
      }
    }
  }
  append(el, children);
  return el;
}

/** 자식을 모두 지운다. */
export function clear(node: Node): void {
  while (node.firstChild) node.removeChild(node.firstChild);
}

/** 자식을 바꾼다. */
export function replace(node: Element, ...children: Child[]): void {
  clear(node);
  append(node, children);
}

let seq = 0;
/** 문서 안에서 겹치지 않는 id(aria-labelledby 연결용). */
export function uid(prefix: string): string {
  seq += 1;
  return `${prefix}-${seq}`;
}

/** 입력 칸에서 친 키인지 — 전역 단축키(←/→)가 입력을 가로채지 않게. */
export function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) {
    return true;
  }
  return target.closest('[contenteditable=""], [contenteditable="true"]') !== null;
}
