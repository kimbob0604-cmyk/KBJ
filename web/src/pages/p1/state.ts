// 페이지 1 F2 위젯의 탭·토글 상태를 URL 쿼리에 둔다(docs/p5_flow_program_prompt.md §3.6 — 새로 고침·공유해도 같은 화면).
//
// - 해시(#/p/1)는 셸이 쓴다 — 여기서는 쿼리(?f2m=…)만 replaceState 로 고친다(뒤로 가기 기록을 쌓지 않음).
// - 모르는 값은 무시하고 기본값을 쓴다(입력값을 그대로 믿지 않는다). replaceState 가 막힌 곳(file://)은 상태만 빠진다.

export type QueryWin = Pick<Window, "location" | "history">;

function win(): QueryWin | null {
  return typeof window === "undefined" ? null : window;
}

/** 쿼리 값 하나 — allowed 에 없으면 null */
export function readParam<V extends string>(
  key: string,
  allowed: readonly V[],
  w: QueryWin | null = win(),
): V | null {
  if (!w) return null;
  const v = new URLSearchParams(w.location.search).get(key);
  return v !== null && (allowed as readonly string[]).includes(v)
    ? (v as V)
    : null;
}

/** 쉼표 목록 — allowed 에 있는 것만(빈 문자열은 '모두 끔') */
export function readList<V extends string>(
  key: string,
  allowed: readonly V[],
  w: QueryWin | null = win(),
): V[] | null {
  if (!w) return null;
  const v = new URLSearchParams(w.location.search).get(key);
  if (v === null) return null;
  return v
    .split(",")
    .filter((x): x is V => (allowed as readonly string[]).includes(x));
}

/** 쿼리 값들을 고친다(null = 지움). 해시는 그대로. */
export function writeParams(
  values: Readonly<Record<string, string | null>>,
  w: QueryWin | null = win(),
): void {
  if (!w) return;
  const q = new URLSearchParams(w.location.search);
  for (const [k, v] of Object.entries(values)) {
    if (v === null) q.delete(k);
    else q.set(k, v);
  }
  const qs = q.toString();
  const url = `${w.location.pathname}${qs ? `?${qs}` : ""}${w.location.hash}`;
  try {
    w.history.replaceState(w.history.state, "", url);
  } catch {
    // replaceState 가 막힌 곳 — 쿼리 동기화만 빠진다(화면 상태는 그대로)
  }
}
