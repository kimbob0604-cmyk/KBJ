// 탭(페이지 안의 탭 — 페이지 8 의 개요·재무·리포트·컨센·밸류·공시·지분, docs/p4_design.md §1.2·§6.2).
//
// - WAI-ARIA 탭 패턴: role=tablist 안의 role=tab 버튼(aria-selected·aria-controls), role=tabpanel(aria-labelledby).
//   고른 탭만 tabindex=0(나머지 -1), 안 보이는 패널은 hidden.
// - 키보드: ←/→ 이전·다음(끝에서 돈다), Home·End 처음·끝 — 자동 선택. 이 키는 preventDefault 해서 셸의
//   페이지 이동(←/→ — app/pager.ts)이 같은 키를 다시 쓰지 않게 한다.
// - 처음 보이는 탭에만 onFirstShow(id, panel) 를 한 번 부른다 — 탭 모듈을 처음 열 때 불러오기(동적 import)용.
//   오류는 그 탭 패널 안 한 줄로(다른 탭은 계속 — 절대 규칙 4). 패널 class 는 .tabbar/.tabpanel(셸의 .tabs 와 별개).
import { clear, h, uid } from './dom';

export interface TabDef<K extends string> {
  id: K;
  label: string;
  /** 비활성(준비 중 등) — 고를 수 없다 */
  disabled?: boolean;
}

export interface TabsOptions<K extends string> {
  /** tablist 의 접근 이름(예 '종목 상세 탭') */
  label: string;
  tabs: readonly TabDef<K>[];
  /** 처음 고른 탭(없으면 첫 활성 탭) */
  value?: K;
  /** 사용자가 탭을 바꿨을 때(select() 로 바꾼 것도) */
  onChange?: (id: K) => void;
  /** 탭이 처음 보일 때 한 번 — 패널을 채운다. Promise 면 실패를 패널 안 오류 줄로 */
  onFirstShow?: (id: K, panel: HTMLDivElement) => unknown;
}

export interface Tabs<K extends string> {
  readonly el: HTMLDivElement;
  readonly bar: HTMLDivElement;
  value(): K;
  panel(id: K): HTMLDivElement;
  select(id: K, opts?: { focus?: boolean }): void;
  /** 처음 보이기 처리(onFirstShow)가 끝날 때까지(시험용) */
  settled(): Promise<void>;
}

export function tabs<K extends string>(opts: TabsOptions<K>): Tabs<K> {
  const enabled = opts.tabs.filter((t) => !t.disabled);
  if (enabled.length === 0) throw new Error('탭: 고를 수 있는 탭이 없다');
  const first = enabled[0] as TabDef<K>;
  let current: K = opts.value && enabled.some((t) => t.id === opts.value) ? opts.value : first.id;
  const shown = new Set<K>();
  const pending: Promise<void>[] = [];
  const base = uid('tabs');

  const buttons = new Map<K, HTMLButtonElement>();
  const panels = new Map<K, HTMLDivElement>();
  for (const t of opts.tabs) {
    const tabId = `${base}-tab-${t.id}`;
    const panelId = `${base}-panel-${t.id}`;
    const b = h(
      'button',
      {
        attrs: {
          type: 'button',
          role: 'tab',
          id: tabId,
          'aria-controls': panelId,
          'aria-selected': 'false',
          'aria-disabled': t.disabled ? 'true' : undefined,
          tabindex: -1,
        },
        data: { tab: t.id },
        on: {
          click: () => {
            if (!t.disabled) select(t.id);
          },
          keydown: (e) => onKey(e, t.id),
        },
      },
      t.label,
    );
    buttons.set(t.id, b);
    panels.set(t.id, h('div', { class: 'tabpanel', attrs: { role: 'tabpanel', id: panelId, 'aria-labelledby': tabId, hidden: true }, data: { tab: t.id } }));
  }

  const bar = h('div', { class: 'tabbar', attrs: { role: 'tablist', 'aria-label': opts.label } }, [...buttons.values()]);
  const el = h('div', { class: 'tabset' }, bar, [...panels.values()]);

  function showFirst(id: K, p: HTMLDivElement): void {
    if (shown.has(id) || !opts.onFirstShow) {
      shown.add(id);
      return;
    }
    shown.add(id);
    const fail = (err: unknown): void => {
      console.error('[kbj] 탭 오류', id, err);
      clear(p);
      p.append(h('p', { class: 'msg err', attrs: { role: 'status' } }, '이 탭을 그리지 못했습니다'));
    };
    try {
      const r = opts.onFirstShow(id, p);
      if (r instanceof Promise) pending.push(r.then(() => undefined, fail));
    } catch (err) {
      fail(err);
    }
  }

  function render(focus: boolean): void {
    for (const [id, b] of buttons) {
      const on = id === current;
      b.setAttribute('aria-selected', on ? 'true' : 'false');
      b.tabIndex = on ? 0 : -1;
      const p = panels.get(id);
      if (p) p.hidden = !on;
    }
    if (focus) buttons.get(current)?.focus();
    const p = panels.get(current);
    if (p) showFirst(current, p);
  }

  function select(id: K, o: { focus?: boolean } = {}): void {
    const def = opts.tabs.find((t) => t.id === id);
    if (!def || def.disabled) return;
    const changed = id !== current;
    current = id;
    render(o.focus ?? false);
    if (changed) opts.onChange?.(id);
  }

  function onKey(e: KeyboardEvent, from: K): void {
    const i = enabled.findIndex((t) => t.id === from);
    let next: TabDef<K> | undefined;
    if (e.key === 'ArrowRight') next = enabled[(i + 1) % enabled.length];
    else if (e.key === 'ArrowLeft') next = enabled[(i - 1 + enabled.length) % enabled.length];
    else if (e.key === 'Home') next = enabled[0];
    else if (e.key === 'End') next = enabled[enabled.length - 1];
    else return;
    // 셸의 ←/→ 페이지 이동이 같은 키를 쓰지 않게
    e.preventDefault();
    if (next) select(next.id, { focus: true });
  }

  render(false);
  return {
    el,
    bar,
    value: () => current,
    panel(id) {
      const p = panels.get(id);
      if (!p) throw new Error(`탭 없음: ${id}`);
      return p;
    },
    select,
    async settled() {
      let seen = 0;
      while (seen < pending.length) {
        const batch = pending.slice(seen);
        seen = pending.length;
        await Promise.all(batch);
      }
    },
  };
}
