// 구간 게이지 — 0~100 가로 막대를 구간(다섯 칸 등)으로 나누고 값 위치에 표시(docs/p7_design.md D-P7-18 — 국장 공포탐욕).
//
// - 값이 없으면(null·NaN) 표시를 그리지 않고 '값 없음'(0 으로 그리지 않는다 — 절대 규칙 2).
// - 범위 밖 값은 0·100 으로 자르지 않는다 — 오류(던진다). 경계는 오름차순 0~100 사이.
// - SVG 는 viewBox 만(폭은 CSS 100%) — 좁은 폭에서 가로 넘침 없음. 색은 기존 토큰만(--down·--warn·--up·--dim).
import { h } from "./dom";
import { DASH, num, type Num } from "./fmt";
import { s, svgBox } from "./svg";

export interface GaugeBand {
  /** 구간 이름(예 '극단적 공포') */
  label: string;
  /** 칸 색 토큰 */
  tone: "down" | "dim" | "warn" | "up" | "accent";
}

export interface GaugeOptions {
  label: string;
  value: Num;
  /** 구간 경계(오름차순, 0~100 사이 — 칸 수 = 경계 수 + 1) */
  edges: readonly number[];
  bands: readonly GaugeBand[];
  w?: number;
}

export interface GaugeLayout {
  cells: { x: number; width: number; band: GaugeBand }[];
  /** 값 위치(x) — 값이 없으면 null */
  marker: number | null;
  /** 값이 든 칸 번호 — 값이 없으면 null */
  index: number | null;
}

function ok(v: Num): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/** 칸·표시 위치(순수 계산 — 시험용). 칸 경계 값은 위 칸에 든다(25 → 둘째 칸). */
export function gaugeLayout(
  value: Num,
  edges: readonly number[],
  bands: readonly GaugeBand[],
  w: number,
): GaugeLayout {
  if (bands.length !== edges.length + 1)
    throw new Error("구간 수 = 경계 수 + 1");
  const sorted = [...edges].every(
    (e, i) => e > 0 && e < 100 && (i === 0 || e > (edges[i - 1] ?? 0)),
  );
  if (!sorted) throw new Error("경계는 0~100 사이 오름차순");
  const bounds = [0, ...edges, 100];
  const cells = bands.map((band, i) => {
    const a = bounds[i] ?? 0;
    const b = bounds[i + 1] ?? 100;
    return { x: (a / 100) * w, width: ((b - a) / 100) * w, band };
  });
  if (!ok(value)) return { cells, marker: null, index: null };
  if (value < 0 || value > 100) throw new Error("게이지 값은 0~100");
  let index = edges.findIndex((e) => value < e);
  if (index < 0) index = edges.length;
  return { cells, marker: (value / 100) * w, index };
}

export function gauge(o: GaugeOptions): HTMLDivElement {
  const w = o.w ?? 300;
  const lay = gaugeLayout(o.value, o.edges, o.bands, w);
  const svg = svgBox(`0 0 ${w} 28`, o.label, "gauge-svg");
  for (const c of lay.cells) {
    s(
      "rect",
      {
        x: c.x,
        y: 6,
        width: Math.max(c.width - 1, 0),
        height: 10,
        fill: `var(--${c.band.tone})`,
        opacity: 0.55,
      },
      svg,
    );
  }
  if (lay.marker !== null) {
    s(
      "line",
      {
        x1: lay.marker,
        x2: lay.marker,
        y1: 2,
        y2: 20,
        stroke: "var(--fg)",
        "stroke-width": 2,
      },
      svg,
    );
  }
  const band = lay.index === null ? null : o.bands[lay.index];
  const text = ok(o.value)
    ? `${num(o.value, 1)} · ${band?.label ?? DASH}`
    : "값 없음";
  return h(
    "div",
    { class: "gauge" },
    svg,
    h("p", { class: "gauge-value" }, text),
  );
}
