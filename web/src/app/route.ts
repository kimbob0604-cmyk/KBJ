// 선택 종목 상태 — 페이지 8(종목 상세)이 보는 종목 하나와, 다른 페이지에서 종목 열기(docs/p4_design.md §1.2·§6.1).
//
// - 상태는 앱 하나에 하나(`route`). 셸(pager)이 처음 해시 `#/p/8/<코드>` 를 읽어 넣고, 종목이 바뀌면 페이지 8 에
//   있을 때만 해시를 replaceState 로 고친다(뒤로 가기 기록을 쌓지 않음 — P3 규칙 그대로).
// - 종목 코드는 `^[0-9A-Z]{6}$` 만(P3 API 규칙). 그 밖의 값은 무시한다(상태를 바꾸지 않고 false).
// - 페이지 모듈(W2)은 이 파일의 함수만 쓴다:
//     getCode()            지금 종목(없으면 null)
//     onCode(fn)           바뀔 때마다 fn(code) — 돌려준 함수로 끊는다(페이지 정리 때)
//     setCode(code)        이 페이지에 머문 채 종목만 바꾼다(검색 상자·관심종목 칩)
//     openStock(code)      페이지 8 로 가서 그 종목을 연다(페이지 4 수급 표 행·페이지 5 공시 행)
// - 이 파일에는 데이터 요청이 없다(공개·로그인 번들 둘 다에 들어간다).

/** 종목 상세 페이지 번호 — 해시에 종목 코드가 붙는 유일한 페이지 */
export const STOCK_PAGE = 8;

/** 종목 코드 형식(P3·P4 API 의 `{code}` 와 같다) */
export const CODE_RE = /^[0-9A-Z]{6}$/;

export function isStockCode(v: unknown): v is string {
  return typeof v === 'string' && CODE_RE.test(v);
}

export type CodeListener = (code: string | null) => void;

/** 페이지 이동을 맡는 쪽(셸의 pager) */
export interface Navigator {
  go(n: number): void;
}

export interface Route {
  getCode(): string | null;
  /** 종목이 바뀔 때마다(같은 값이면 부르지 않는다). 돌려준 함수로 끊는다 */
  onCode(fn: CodeListener): () => void;
  /** 페이지는 그대로 두고 종목만 바꾼다. null 은 선택 해제. 잘못된 코드는 false(상태 그대로) */
  setCode(code: string | null): boolean;
  /** 페이지 8 로 가서 그 종목을 연다. 잘못된 코드는 false(이동 없음) */
  openStock(code: string): boolean;
  /** 셸이 붙인다 — openStock 의 페이지 이동 담당. 돌려준 함수로 뗀다(앱을 내릴 때) */
  bind(nav: Navigator): () => void;
  /** 처음 상태로 — 종목 없음·듣는 쪽 없음·이동 담당 없음(앱을 내릴 때·시험) */
  reset(): void;
}

export function createRoute(): Route {
  let code: string | null = null;
  let nav: Navigator | null = null;
  const listeners = new Set<CodeListener>();

  function emit(): void {
    for (const fn of [...listeners]) {
      try {
        fn(code);
      } catch (err) {
        // 한 구독자의 오류가 다른 구독자·셸을 멈추지 않게(절대 규칙 4) — 감추지 않고 콘솔에
        console.error('[kbj] 종목 선택 구독 오류', err);
      }
    }
  }

  const api: Route = {
    getCode: () => code,
    onCode(fn) {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
    setCode(next) {
      if (next !== null && !isStockCode(next)) return false;
      if (next === code) return true;
      code = next;
      emit();
      return true;
    },
    openStock(next) {
      if (!api.setCode(next)) return false;
      nav?.go(STOCK_PAGE);
      return true;
    },
    bind(n) {
      nav = n;
      return () => {
        if (nav === n) nav = null;
      };
    },
    reset() {
      code = null;
      nav = null;
      listeners.clear();
    },
  };
  return api;
}

/** 앱의 선택 종목 상태(하나뿐) — 셸(app.ts)이 pager 에 물리고, 페이지 모듈은 아래 함수로 쓴다 */
export const route: Route = createRoute();

export const getCode = (): string | null => route.getCode();
export const onCode = (fn: CodeListener): (() => void) => route.onCode(fn);
export const setCode = (code: string | null): boolean => route.setCode(code);
export const openStock = (code: string): boolean => route.openStock(code);

/** 종목 상세 해시 — 다른 페이지가 링크(href)를 그릴 때. 잘못된 코드면 null */
export function stockHash(code: string): string | null {
  return isStockCode(code) ? `#/p/${STOCK_PAGE}/${code}` : null;
}
