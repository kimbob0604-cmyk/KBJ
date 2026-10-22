// 가격 사다리 — 현재가·Flip·콜월·풋월·±1σ 를 세로 한 줄에 높이 순으로(docs/p7_design.md D-P7-18 — GEX 레벨).
//
// - 값이 없는 단(null·NaN)은 그리지 않고 아래 줄에 '없음' 이름으로 적는다(0 으로 그리지 않는다).
// - 위가 높은 가격. 같은 값 둘은 이름을 합친다. SVG 는 viewBox 만(폭 100%) — 가로 넘침 없음.
// - 눈금(축 위 짧은 선)은 늘 참 위치. 가까운 값(예 풋월 870.00·Flip 870.40·현재가 868.66)의 글자는 글자 높이
//   (LABEL_GAP)만큼 벌려 놓고(`spreadLabels` — 겹친 무리는 참 위치 평균에 모은다), 옮긴 글자는 눈금과 가는
//   이음선으로 잇는다(2026-10-22 사용자 '가까운 레벨 글자가 겹쳐 읽을 수 없다').
// - 글자 크기: viewBox 폭(LADDER_W)을 화면 폭에 가깝게 두고 CSS 가 최대 폭을 묶는다 — 반폭 패널에서 글자가
//   두세 배로 커지지 않게(components.css .ladder).
import { h } from "./dom";
import { num, type Num } from "./fmt";
import { s, svgBox } from "./svg";

export interface Rung {
  /** 이름(예 '현재가'·'Flip'·'콜월') */
  name: string;
  value: Num;
  tone?: "fg" | "accent" | "up" | "down" | "warn" | "dim";
}

export interface LadderLayout {
  rows: {
    /** 눈금의 참 위치 */
    y: number;
    /** 글자 위치 — 이웃과 겹치지 않게 벌린 값(겹침이 없으면 y 와 같다) */
    labelY: number;
    value: number;
    names: string[];
    tone: NonNullable<Rung["tone"]>;
  }[];
  missing: string[];
}

/** viewBox 기본 폭·높이, 글자 줄 사이 최소 간격(viewBox 단위 — 글자 9 + 여유) */
export const LADDER_W = 300;
export const LADDER_H = 180;
export const LABEL_GAP = 12;

// 가로 배치(viewBox 단위): 값 글자(끝 맞춤) | 이음선 | 눈금(축 양쪽) | 이음선 | 이름 글자
const AXIS_X = 84;
const TICK = 8;
const LEAD = 6;

function ok(v: Num): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/**
 * 위→아래로 정렬된 참 위치 ys → 서로 gap 이상 떨어진 글자 위치(순서 그대로, 될 수 있으면 [lo, hi] 안).
 * 겹치는 이웃은 한 무리로 묶고 무리를 참 위치 평균에 둔다(한쪽으로만 밀지 않는다). 칸이 모자라면 lo 부터.
 */
export function spreadLabels(ys: readonly number[], gap: number, lo: number, hi: number): number[] {
  interface Block {
    first: number; // 무리 첫 글자의 ys 색인
    n: number;
    sum: number; // Σ(ys[first+k] − k·gap) — 무리 맨 위 글자가 바라는 위치의 합
    top: number;
  }
  const place = (b: Block): void => {
    const maxTop = hi - (b.n - 1) * gap;
    b.top = maxTop < lo ? lo : Math.min(Math.max(b.sum / b.n, lo), maxTop);
  };
  const blocks: Block[] = [];
  ys.forEach((y, i) => {
    const b: Block = { first: i, n: 1, sum: y, top: y };
    place(b);
    blocks.push(b);
    // 앞 무리와 겹치면 합친다(합친 무리가 또 앞과 겹치면 계속)
    for (;;) {
      const cur = blocks[blocks.length - 1];
      const prev = blocks[blocks.length - 2];
      if (!cur || !prev || prev.top + prev.n * gap <= cur.top + 1e-9) break;
      blocks.pop();
      for (let k = 0; k < cur.n; k += 1) prev.sum += (ys[cur.first + k] ?? 0) - (prev.n + k) * gap;
      prev.n += cur.n;
      place(prev);
    }
  });
  const out: number[] = [];
  for (const b of blocks) for (let k = 0; k < b.n; k += 1) out.push(b.top + k * gap);
  return out;
}

/** 단 위치(순수 계산 — 시험용). 높은 값이 위(y 작음). */
export function ladderLayout(
  rungs: readonly Rung[],
  h0: number,
  pad = 12,
  gap = LABEL_GAP,
): LadderLayout {
  const present = rungs.filter((r) => ok(r.value));
  const missing = rungs.filter((r) => !ok(r.value)).map((r) => r.name);
  const values = present.map((r) => r.value as number);
  const hi = Math.max(...values);
  const lo = Math.min(...values);
  const span = hi - lo;
  const merged = new Map<
    number,
    { names: string[]; tone: NonNullable<Rung["tone"]> }
  >();
  for (const r of present) {
    const v = r.value as number;
    const m = merged.get(v);
    if (m) m.names.push(r.name);
    else merged.set(v, { names: [r.name], tone: r.tone ?? "fg" });
  }
  const base = [...merged.entries()]
    .sort((a, b) => b[0] - a[0])
    .map(([value, m]) => ({
      value,
      names: m.names,
      tone: m.tone,
      y: span === 0 ? h0 / 2 : pad + ((hi - value) / span) * (h0 - 2 * pad),
    }));
  const edge = Math.min(pad, h0 / 2);
  const labels = spreadLabels(base.map((r) => r.y), gap, edge, h0 - edge);
  return { rows: base.map((r, i) => ({ ...r, labelY: labels[i] ?? r.y })), missing };
}

export function ladder(
  label: string,
  rungs: readonly Rung[],
  o: { w?: number; h?: number; digits?: number } = {},
): HTMLDivElement {
  const w = o.w ?? LADDER_W;
  const n = rungs.filter((r) => ok(r.value)).length;
  // 단이 많아 글자 칸이 모자라면 높이를 늘린다(글자를 줄이거나 겹치게 두지 않는다)
  const h0 = Math.max(o.h ?? LADDER_H, n * LABEL_GAP + 24);
  const lay = ladderLayout(rungs, h0);
  const svg = svgBox(`0 0 ${w} ${h0}`, label, "ladder-svg");
  s("line", { x1: AXIS_X, x2: AXIS_X, y1: 4, y2: h0 - 4, stroke: "var(--line)" }, svg);
  const left = AXIS_X - TICK;
  const right = AXIS_X + TICK;
  for (const r of lay.rows) {
    const stroke = `var(--${r.tone})`;
    s("line", { x1: left, x2: right, y1: r.y, y2: r.y, stroke, "stroke-width": 2 }, svg);
    if (Math.abs(r.labelY - r.y) > 0.5) {
      // 옮긴 글자 — 눈금 양끝에서 글자 줄까지 가는 이음선
      const lead = { stroke, "stroke-width": 0.75, class: "ladder-lead" };
      s("line", { x1: left, y1: r.y, x2: left - LEAD, y2: r.labelY, ...lead }, svg);
      s("line", { x1: right, y1: r.y, x2: right + LEAD, y2: r.labelY, ...lead }, svg);
    }
    const ty = r.labelY + 3;
    s(
      "text",
      { x: left - LEAD - 3, y: ty, "text-anchor": "end", class: "fg" },
      svg,
      num(r.value, o.digits ?? 2),
    );
    s("text", { x: right + LEAD + 3, y: ty }, svg, r.names.join("·"));
  }
  const out = h("div", { class: "ladder" }, svg);
  if (lay.missing.length)
    out.append(
      h("p", { class: "ladder-missing" }, `없음: ${lay.missing.join("·")}`),
    );
  return out;
}
