// 종목 검색 상자 — 이름·코드 부분 일치, 키보드로 고르기(docs/p4_design.md §1.2·§6.1).
//
// - WAI-ARIA 콤보박스(목록형): input role=combobox(aria-expanded·aria-controls·aria-activedescendant·
//   aria-autocomplete=list) + ul role=listbox 의 li role=option(aria-selected). 결과 없음·실패는 목록 밖 role=status 한 줄.
// - 키보드: ↓/↑ 후보 이동(끝에서 돈다), Enter 고르기(고른 후보 → 올바른 6자리 코드 그대로 → 지금 글자로 찾은 목록의
//   첫 후보 — 입력 직후 debounce·응답을 기다리는 중이면 앞 질의의 목록에서 고르지 않고 지금 글자의 결과를 기다린다),
//   Escape 닫기(닫혀 있으면 지우기). ←/→ 는 입력 칸이라 셸의 페이지 이동이 가로채지 않는다(app/pager.ts isEditable).
// - 찾는 함수는 밖에서 받는다 — 로그인: API(stock/search), 공개: corp_index.json 을 matchItems() 로.
//   늦게 온 앞 질의 결과는 버린다(순번). 찾기 실패는 목록 밖 한 줄('검색하지 못함') — 삼키지 않는다.
// - 질의는 1~20자(API `q` 와 같음). 입력은 debounceMs 뒤에 찾는다(0 이면 바로 — 시험).
import { DataError } from '../data/source';
import { clear, h, uid } from './dom';

export interface SearchItem {
  code: string;
  name: string;
  /** 시장(KOSPI·KOSDAQ·KONEX) 등 짧은 꼬리표 */
  market?: string | null;
  /** 종류 배지(우선주·스팩·리츠) 등 */
  note?: string | null;
}

export const QUERY_MAX = 20;
const CODE_RE = /^[0-9A-Z]{6}$/;

export interface SearchBoxOptions {
  /** 입력 칸 이름표(화면에 보인다) — 예 '종목 검색' */
  label: string;
  placeholder?: string;
  /** 질의 → 후보. 빈 배열이면 '결과 없음' */
  search: (q: string) => Promise<readonly SearchItem[]> | readonly SearchItem[];
  onSelect: (item: SearchItem) => void;
  /** 후보 최대 수(기본 20) */
  limit?: number;
  /** 입력 뒤 기다리는 시간(기본 150ms, 0 이면 바로) */
  debounceMs?: number;
}

export interface SearchBox {
  readonly el: HTMLDivElement;
  readonly input: HTMLInputElement;
  /** 지금 보이는 후보 */
  items(): readonly SearchItem[];
  /** 입력 칸 글자를 바꾸고 찾는다 */
  query(text: string): Promise<void>;
  close(): void;
  /** 진행 중인 찾기가 끝날 때까지(시험용) */
  settled(): Promise<void>;
  destroy(): void;
}

/** 질의 정리 — 앞뒤 공백 제거, 20자로 자름. 영문은 대문자로(종목 코드 `^[0-9A-Z]{6}$`) */
export function normalizeQuery(q: string): string {
  return q.trim().slice(0, QUERY_MAX);
}

/**
 * 로컬 목록에서 찾기(공개판 corp_index.json). 순위: 코드 일치 > 코드 앞부분 > 이름 앞부분 > 이름 포함.
 * 영문 대소문자 무시. 같은 순위는 원래 순서.
 */
export function matchItems(all: readonly SearchItem[], q: string, limit = 20): SearchItem[] {
  const query = normalizeQuery(q);
  if (!query) return [];
  const upper = query.toUpperCase();
  const lower = query.toLowerCase();
  const scored: { item: SearchItem; rank: number; i: number }[] = [];
  all.forEach((item, i) => {
    const name = item.name.toLowerCase();
    let rank = -1;
    if (item.code === upper) rank = 0;
    else if (item.code.startsWith(upper)) rank = 1;
    else if (name.startsWith(lower)) rank = 2;
    else if (name.includes(lower)) rank = 3;
    if (rank >= 0) scored.push({ item, rank, i });
  });
  scored.sort((a, b) => a.rank - b.rank || a.i - b.i);
  return scored.slice(0, Math.max(0, limit)).map((x) => x.item);
}

export function searchBox(opts: SearchBoxOptions): SearchBox {
  const limit = opts.limit ?? 20;
  const wait = opts.debounceMs ?? 150;
  const listId = uid('search-list');
  const inputId = uid('search-input');
  let results: SearchItem[] = [];
  let active = -1;
  let seq = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let wake: (() => void) | null = null;
  let running: Promise<void> = Promise.resolve();
  /** 지금 보이는 후보(results)를 만든 질의 — 입력 글자와 다르면 목록은 앞 질의의 것(기다리는 중·찾는 중) */
  let shownQuery = '';
  /** 마지막으로 시작한 찾기의 질의 */
  let askedQuery = '';
  let alive = true;

  const input = h('input', {
    attrs: {
      id: inputId,
      type: 'text',
      role: 'combobox',
      'aria-autocomplete': 'list',
      'aria-expanded': 'false',
      'aria-controls': listId,
      autocomplete: 'off',
      spellcheck: 'false',
      maxlength: QUERY_MAX,
      placeholder: opts.placeholder ?? '종목명 또는 코드',
    },
  });
  const list = h('ul', { class: 'search-list', attrs: { id: listId, role: 'listbox', 'aria-label': `${opts.label} 후보` } });
  list.hidden = true;
  // 결과 없음·실패 문구는 목록(listbox) 밖에 — listbox 안에는 option 만 둔다(접근성)
  const msg = h('p', { class: 'search-msg', attrs: { role: 'status' } });
  msg.hidden = true;
  const el = h('div', { class: 'search' }, h('label', { attrs: { for: inputId } }, opts.label), input, list, msg);

  function optionId(i: number): string {
    return `${listId}-opt-${i}`;
  }

  function setOpen(open: boolean): void {
    // 후보가 없으면 목록을 열지 않는다(빈 listbox 를 보이지 않게)
    const show = open && results.length > 0;
    list.hidden = !show;
    input.setAttribute('aria-expanded', show ? 'true' : 'false');
    if (!show) {
      active = -1;
      input.removeAttribute('aria-activedescendant');
    }
  }

  function say(text: string, err = false): void {
    msg.textContent = text;
    msg.hidden = text === '';
    msg.classList.toggle('err', err);
  }

  function markActive(): void {
    list.querySelectorAll<HTMLLIElement>('li[role="option"]').forEach((li, i) => {
      li.setAttribute('aria-selected', i === active ? 'true' : 'false');
    });
    if (active >= 0) {
      input.setAttribute('aria-activedescendant', optionId(active));
      const li = list.querySelector(`#${optionId(active)}`);
      // jsdom 에는 scrollIntoView 가 없다
      if (li && typeof li.scrollIntoView === 'function') li.scrollIntoView({ block: 'nearest' });
    } else {
      input.removeAttribute('aria-activedescendant');
    }
  }

  function choose(item: SearchItem): void {
    setOpen(false);
    say('');
    input.value = item.name ? `${item.name} ${item.code}` : item.code;
    opts.onSelect(item);
  }

  function renderList(): void {
    clear(list);
    results.forEach((it, i) => {
      list.append(
        h(
          'li',
          {
            attrs: { id: optionId(i), role: 'option', 'aria-selected': 'false' },
            data: { code: it.code },
            on: {
              // mousedown: 입력 칸 blur(닫기)보다 먼저 고른다
              mousedown: (e) => {
                e.preventDefault();
                choose(it);
              },
            },
          },
          h('span', { class: 'nm' }, it.name || it.code),
          h('small', null, it.code),
          it.market ? h('small', null, it.market) : null,
          it.note ? h('span', { class: 'tag' }, it.note) : null,
        ),
      );
    });
    active = -1;
    markActive();
    // 결과 없음은 눈에도 보이는 한 줄(role=status — 화면 읽기 프로그램에도 알린다)
    say(results.length ? '' : '결과 없음');
    setOpen(true);
  }

  function showError(err: unknown): void {
    // 예상한 데이터 오류(DataError)는 화면 한 줄, 그 밖은 콘솔에도(감추지 않는다)
    if (!(err instanceof DataError)) console.error('[kbj] 종목 검색 오류', err);
    results = [];
    clear(list);
    setOpen(false);
    say('검색하지 못함', true);
  }

  function run(text: string): Promise<void> {
    const q = normalizeQuery(text);
    const my = ++seq;
    askedQuery = q;
    if (!q) {
      results = [];
      shownQuery = '';
      clear(list);
      say('');
      setOpen(false);
      return Promise.resolve();
    }
    let p: Promise<readonly SearchItem[]>;
    try {
      p = Promise.resolve(opts.search(q));
    } catch (err) {
      p = Promise.reject(err instanceof Error ? err : new Error(String(err)));
    }
    return p.then(
      (items) => {
        if (my !== seq) return; // 늦게 온 앞 질의
        results = items.filter((it) => CODE_RE.test(it.code)).slice(0, limit);
        shownQuery = q;
        renderList();
      },
      (err: unknown) => {
        if (my !== seq) return;
        shownQuery = q;
        showError(err);
      },
    );
  }

  /**
   * Enter 로 고를 때 목록이 지금 글자의 결과가 아니면(입력 뒤 debounce 를 기다리는 중·찾는 중) 앞 질의의 첫 후보를
   * 고르지 않는다 — 지금 글자로 바로 찾고(이미 찾는 중이면 그 결과를 기다려) 그 결과의 첫 후보를 고른다.
   */
  function chooseFirstFor(q: string): void {
    if (timer !== null || askedQuery !== q) {
      cancelTimer();
      running = run(input.value);
    }
    running = running.then(() => {
      if (!alive || normalizeQuery(input.value) !== q || shownQuery !== q) return;
      const first = results[0];
      if (first) choose(first);
    });
  }

  /** 기다리던 찾기를 취소한다 — 그 찾기를 기다리던 settled() 도 풀어 준다(영원히 걸려 있지 않게) */
  function cancelTimer(): void {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    wake?.();
    wake = null;
  }

  function schedule(): void {
    cancelTimer();
    if (wait <= 0) {
      running = run(input.value);
      return;
    }
    running = new Promise<void>((resolve) => {
      wake = resolve;
      timer = setTimeout(() => {
        timer = null;
        wake = null;
        void run(input.value).then(resolve);
      }, wait);
    });
  }

  input.addEventListener('input', () => {
    // 글자가 바뀌면 앞 목록에서 고른 후보는 버린다(Enter 가 앞 질의의 후보를 고르지 않게)
    active = -1;
    markActive();
    schedule();
  });
  input.addEventListener('keydown', (e) => {
    if (e.isComposing) return; // 한글 조합 중 Enter·화살표는 입력기 몫
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (list.hidden || results.length === 0) return;
      e.preventDefault();
      const n = results.length;
      active = e.key === 'ArrowDown' ? (active + 1) % n : (active - 1 + n) % n;
      markActive();
    } else if (e.key === 'Enter') {
      // 고른 후보 → 6자리 코드 그대로(후보에 있으면 그 이름과) → 지금 글자의 결과 목록의 첫 후보
      // (목록이 앞 질의의 것이면 지금 글자로 찾은 뒤 첫 후보 — chooseFirstFor)
      const q = normalizeQuery(input.value);
      const raw = q.toUpperCase();
      let picked: SearchItem | undefined = active >= 0 ? results[active] : undefined;
      if (!picked && CODE_RE.test(raw)) picked = results.find((r) => r.code === raw) ?? { code: raw, name: '' };
      if (picked) {
        e.preventDefault();
        choose(picked);
      } else if (q && (q !== shownQuery || timer !== null)) {
        e.preventDefault();
        chooseFirstFor(q);
      } else if (!list.hidden && results[0]) {
        e.preventDefault();
        choose(results[0]);
      }
    } else if (e.key === 'Escape') {
      if (!list.hidden) {
        e.preventDefault();
        setOpen(false);
      } else if (input.value) {
        e.preventDefault();
        input.value = '';
      }
    }
  });
  input.addEventListener('blur', () => setOpen(false));

  return {
    el,
    input,
    items: () => results,
    query(text) {
      input.value = text;
      cancelTimer();
      running = run(text);
      return running;
    },
    close: () => setOpen(false),
    settled: () => running,
    destroy() {
      alive = false;
      cancelTimer();
      seq += 1; // 진행 중인 찾기의 결과는 버린다
    },
  };
}
