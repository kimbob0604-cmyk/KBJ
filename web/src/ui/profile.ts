// 행사가별 GEX 프로필 — 행사가마다 세로 막대(양 --up · 음 --down) + 현재가·Flip 세로선(docs/p7_design.md D-P7-18).
//
// - 값이 없는 막대(null·NaN)는 그리지 않고 수를 아래 줄에 적는다(0 으로 그리지 않는다).
// - x = 행사가 순서(오름차순 — 같은 간격), 현재가·Flip 은 행사가 사이를 선형으로 끼워 넣는다(범위 밖이면 선 없음).
// - SVG 는 viewBox 만(폭 100%) — 가로 넘침 없음. 화면은 반폭(span-6) 칸에 둔다(전체 폭이면 글자가 커진다).
import { h } from "./dom";
import { type Num } from "./fmt";
import { s, svgBox } from "./svg";

export interface ProfileBar {
  strike: number;
  value: Num;
}

export interface ProfileOptions {
  label: string;
  bars: readonly ProfileBar[];
  price?: Num;
  flip?: Num;
  w?: number;
  h?: number;
}

export interface ProfileLayout {
  rects: {
    x: number;
    y: number;
    width: number;
    height: number;
    sign: "up" | "down" | "flat";
    strike: number;
  }[];
  zeroY: number;
  priceX: number | null;
  flipX: number | null;
  missing: number;
}

function ok(v: Num | undefined): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/** 행사가 → x(행사가 사이 선형 보간, 범위 밖 null) */
function strikeX(
  strikes: readonly number[],
  v: Num | undefined,
  x0: number,
  step: number,
): number | null {
  if (!ok(v) || strikes.length === 0) return null;
  const first = strikes[0] as number;
  const last = strikes[strikes.length - 1] as number;
  if (v < first || v > last) return null;
  if (strikes.length === 1) return x0 + step / 2;
  for (let i = 0; i < strikes.length - 1; i += 1) {
    const a = strikes[i] as number;
    const b = strikes[i + 1] as number;
    if (v >= a && v <= b)
      return x0 + step * (i + 0.5 + (b === a ? 0 : (v - a) / (b - a)));
  }
  return null;
}

/** 막대·선 위치(순수 계산 — 시험용). */
export function profileLayout(
  o: Omit<ProfileOptions, "label">,
  w: number,
  h0: number,
): ProfileLayout {
  const bars = [...o.bars].sort((a, b) => a.strike - b.strike);
  const top = 8;
  const bottom = 18;
  const left = 4;
  const vals = bars.map((b) => b.value).filter(ok);
  const hi = Math.max(0, ...vals);
  const lo = Math.min(0, ...vals);
  const span = hi - lo || 1;
  const plotH = h0 - top - bottom;
  const y = (v: number): number => top + (1 - (v - lo) / span) * plotH;
  const step = bars.length ? (w - 2 * left) / bars.length : 0;
  const zeroY = y(0);
  let missing = 0;
  const rects: ProfileLayout["rects"] = [];
  bars.forEach((b, i) => {
    if (!ok(b.value)) {
      missing += 1;
      return;
    }
    const yv = y(b.value);
    rects.push({
      x: left + i * step + step * 0.15,
      y: Math.min(yv, zeroY),
      width: Math.max(step * 0.7, 0.5),
      height: Math.max(Math.abs(zeroY - yv), 0.5),
      sign: b.value > 0 ? "up" : b.value < 0 ? "down" : "flat",
      strike: b.strike,
    });
  });
  const strikes = bars.map((b) => b.strike);
  return {
    rects,
    zeroY,
    priceX: strikeX(strikes, o.price, left, step),
    flipX: strikeX(strikes, o.flip, left, step),
    missing,
  };
}

export function profile(o: ProfileOptions): HTMLDivElement {
  const w = o.w ?? 320;
  const h0 = o.h ?? 160;
  const lay = profileLayout(o, w, h0);
  const svg = svgBox(`0 0 ${w} ${h0}`, o.label, "profile-svg");
  s(
    "line",
    { x1: 0, x2: w, y1: lay.zeroY, y2: lay.zeroY, stroke: "var(--line)" },
    svg,
  );
  for (const r of lay.rects) {
    const fill =
      r.sign === "up"
        ? "var(--up)"
        : r.sign === "down"
          ? "var(--down)"
          : "var(--dim)";
    const rect = s(
      "rect",
      { x: r.x, y: r.y, width: r.width, height: r.height, fill },
      svg,
    );
    s("title", {}, rect, String(r.strike));
  }
  if (lay.flipX !== null) {
    s(
      "line",
      {
        x1: lay.flipX,
        x2: lay.flipX,
        y1: 2,
        y2: h0 - 14,
        stroke: "var(--warn)",
        "stroke-dasharray": "3 2",
      },
      svg,
    );
    s(
      "text",
      { x: lay.flipX, y: h0 - 4, "text-anchor": "middle", class: "warn" },
      svg,
      "Flip",
    );
  }
  if (lay.priceX !== null) {
    s(
      "line",
      {
        x1: lay.priceX,
        x2: lay.priceX,
        y1: 2,
        y2: h0 - 14,
        stroke: "var(--accent)",
        "stroke-width": 1.5,
      },
      svg,
    );
    s(
      "text",
      { x: lay.priceX, y: 10, "text-anchor": "middle", class: "fg" },
      svg,
      "현재가",
    );
  }
  const out = h("div", { class: "profile" }, svg);
  if (lay.missing)
    out.append(
      h("p", { class: "profile-missing" }, `값 없는 행사가 ${lay.missing}개`),
    );
  return out;
}
