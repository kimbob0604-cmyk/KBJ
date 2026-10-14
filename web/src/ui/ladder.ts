// 가격 사다리 — 현재가·Flip·콜월·풋월·±1σ 를 세로 한 줄에 높이 순으로(docs/p7_design.md D-P7-18 — GEX 레벨).
//
// - 값이 없는 단(null·NaN)은 그리지 않고 아래 줄에 '없음' 이름으로 적는다(0 으로 그리지 않는다).
// - 위가 높은 가격. 같은 값 둘은 이름을 합친다. SVG 는 viewBox 만(폭 100%) — 가로 넘침 없음.
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
    y: number;
    value: number;
    names: string[];
    tone: NonNullable<Rung["tone"]>;
  }[];
  missing: string[];
}

function ok(v: Num): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/** 단 위치(순수 계산 — 시험용). 높은 값이 위(y 작음). */
export function ladderLayout(
  rungs: readonly Rung[],
  h0: number,
  pad = 12,
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
  const rows = [...merged.entries()]
    .sort((a, b) => b[0] - a[0])
    .map(([value, m]) => ({
      value,
      names: m.names,
      tone: m.tone,
      y: span === 0 ? h0 / 2 : pad + ((hi - value) / span) * (h0 - 2 * pad),
    }));
  return { rows, missing };
}

export function ladder(
  label: string,
  rungs: readonly Rung[],
  o: { w?: number; h?: number; digits?: number } = {},
): HTMLDivElement {
  const w = o.w ?? 240;
  const h0 = o.h ?? 160;
  const lay = ladderLayout(rungs, h0);
  const svg = svgBox(`0 0 ${w} ${h0}`, label, "ladder-svg");
  s("line", { x1: 60, x2: 60, y1: 4, y2: h0 - 4, stroke: "var(--line)" }, svg);
  for (const r of lay.rows) {
    s(
      "line",
      {
        x1: 52,
        x2: 68,
        y1: r.y,
        y2: r.y,
        stroke: `var(--${r.tone})`,
        "stroke-width": 2,
      },
      svg,
    );
    s(
      "text",
      { x: 48, y: r.y + 3, "text-anchor": "end", class: "fg" },
      svg,
      num(r.value, o.digits ?? 2),
    );
    s("text", { x: 74, y: r.y + 3 }, svg, r.names.join("·"));
  }
  const out = h("div", { class: "ladder" }, svg);
  if (lay.missing.length)
    out.append(
      h("p", { class: "ladder-missing" }, `없음: ${lay.missing.join("·")}`),
    );
  return out;
}
