// 페이지 7 '월별 추이' 탭(metrics §29a — 2026-10-20 사용자 요청): 상위 류 + 기타 쌓은 막대·전년동월비 선·분기 QoQ·YoY.
// 로그인은 API 'trade/monthly', 공개는 같은 이름 파일 — 수출·수입이 한 응답(전환에 다시 부르지 않는다). 합성 픽스처만.
import { describe, expect, it } from "vitest";
import page from "../../src/pages/p7_trade";
import { shortYm, summary, TITLE } from "../../src/pages/p7/monthly";
import type { TradeMonthly } from "../../src/pages/p7/common";
import {
  EXPLORE_NOW,
  exploreFixture,
  exploreLoginHarness,
  explorePublicHarness,
} from "../fixtures/api/trade_explore/harness";
import { buttonByText, flush, pageCtx, panelByTitle, text } from "./support";

async function mount(tier: "login" | "public") {
  const hz =
    tier === "login" ? exploreLoginHarness({}) : explorePublicHarness({});
  const { ctx, root } = pageCtx(7, tier, hz.source, { now: () => EXPLORE_NOW });
  await page.mount(ctx);
  await flush(10);
  return { hz, root };
}

describe("페이지 7 월별 추이", () => {
  it("첫 탭 — 쌓은 막대(상위 류 + 기타)·전년동월비 선·분기 표·최신 달/분기 요약", async () => {
    const { hz, root } = await mount("login");
    expect(
      hz.paths().filter((p) => p.startsWith("trade/monthly")),
    ).toHaveLength(1);
    const p = panelByTitle(root, TITLE);
    expect(p.querySelector(".stackbar svg")).not.toBeNull();
    expect(p.querySelector(".linechart, svg.linechart")).not.toBeNull();
    expect(text(p)).toContain("QoQ(직전 분기)");
    expect(text(p)).toContain("기타");
    expect(text(p)).not.toMatch(/NaN|undefined/);
    buttonByText(p, "수입").click();
    await flush();
    expect(text(p)).toContain("수입");
    expect(
      hz.paths().filter((x) => x.startsWith("trade/monthly")),
    ).toHaveLength(1); // 다시 부르지 않는다
  });

  it("공개판은 같은 이름 공개 파일 하나", async () => {
    const { hz, root } = await mount("public");
    expect(hz.paths().filter((p) => p.includes("trade/monthly"))).toEqual([
      "./data/trade/monthly.json",
    ]);
    expect(
      panelByTitle(root, TITLE).querySelector(".stackbar svg"),
    ).not.toBeNull();
  });

  it("요약 줄 — 최신 달 전월비·전년동월비, 최근 분기 QoQ·YoY", () => {
    const env = exploreFixture<{ data: TradeMonthly }>("monthly");
    const s = summary(env.data.exp);
    const m = env.data.exp.months.at(-1);
    const q = env.data.exp.quarters.at(-1);
    expect(s[0]?.[0]).toBe(`${m?.ym} 수출`);
    expect(s.map((x) => x[0])).toEqual([
      `${m?.ym} 수출`,
      "전월비",
      "전년동월비",
      `${q?.q} 합`,
      "QoQ(직전 분기)",
      "YoY(전년 같은 분기)",
    ]);
    expect(shortYm("2026-09")).toBe("26.09");
  });
});
