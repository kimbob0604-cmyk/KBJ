// 표 — 머리 th scope=col, 행 선택(클릭·Enter·Space), 기본 50행 + '더 보기'(§6.9).
import { clear, h } from './dom';

export interface Column<T> {
  key: string;
  label: string;
  /** 'l' = 왼쪽 정렬(이름·문자), 기본 오른쪽(숫자) */
  align?: 'l' | 'r';
  /** 칸 내용. null 이면 '—' */
  cell: (row: T) => Node | string | null;
  /** 칸 class(예: 부호 색 'up'·'down') */
  cls?: (row: T) => string | undefined;
}

export interface TableOptions<T> {
  caption?: string;
  columns: readonly Column<T>[];
  rows: readonly T[];
  rowKey?: (row: T) => string;
  onSelect?: (row: T) => void;
  selected?: string | null;
  /** 처음 보이는 행 수(기본 50). '더 보기'로 같은 수만큼 늘린다 */
  pageSize?: number;
  empty?: string;
}

export interface Table<T> {
  readonly el: HTMLDivElement;
  update(rows: readonly T[]): void;
  select(key: string | null): void;
}

export function table<T>(opts: TableOptions<T>): Table<T> {
  const pageSize = opts.pageSize ?? 50;
  let rows: readonly T[] = opts.rows;
  let shown = pageSize;
  let selected = opts.selected ?? null;

  const tbody = h('tbody');
  const more = h('button', { class: 'btn more', attrs: { type: 'button' } }, '더 보기');
  more.addEventListener('click', () => {
    shown += pageSize;
    render();
  });
  const thead = h(
    'thead',
    null,
    h(
      'tr',
      null,
      opts.columns.map((c) => h('th', { class: c.align === 'l' ? 'l' : undefined, attrs: { scope: 'col' } }, c.label)),
    ),
  );
  const tableEl = h('table', null, opts.caption ? h('caption', null, opts.caption) : null, thead, tbody);
  const el = h('div', { class: 'tbl' }, tableEl, more);

  function rowEl(r: T): HTMLTableRowElement {
    const key = opts.rowKey?.(r);
    const tr = h(
      'tr',
      null,
      opts.columns.map((c) => {
        const v = c.cell(r);
        const cls = [c.align === 'l' ? 'l' : '', c.cls?.(r) ?? ''].filter(Boolean).join(' ');
        return h('td', cls ? { class: cls } : null, v ?? '—');
      }),
    );
    if (opts.onSelect && key !== undefined) {
      const onSelect = opts.onSelect;
      tr.classList.add('sel');
      tr.tabIndex = 0;
      tr.dataset.key = key;
      tr.setAttribute('aria-selected', key === selected ? 'true' : 'false');
      tr.addEventListener('click', () => {
        api.select(key);
        onSelect(r);
      });
      tr.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          api.select(key);
          onSelect(r);
        }
      });
    }
    return tr;
  }

  function render(): void {
    clear(tbody);
    if (rows.length === 0) {
      tbody.append(
        h('tr', null, h('td', { class: 'l dim', attrs: { colspan: opts.columns.length } }, opts.empty ?? '해당 없음')),
      );
    } else {
      for (const r of rows.slice(0, shown)) tbody.append(rowEl(r));
    }
    more.hidden = rows.length <= shown;
  }

  const api: Table<T> = {
    el,
    update(next) {
      rows = next;
      shown = pageSize;
      render();
    },
    select(key) {
      selected = key;
      for (const tr of tbody.querySelectorAll<HTMLTableRowElement>('tr[data-key]')) {
        tr.setAttribute('aria-selected', tr.dataset.key === key ? 'true' : 'false');
      }
    },
  };
  render();
  return api;
}
