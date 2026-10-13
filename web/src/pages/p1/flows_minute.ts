// 페이지 1 '장중 투자자 수급' — 1분 누적 순매수 선·카드 7(docs/p5_flow_program_prompt.md §0-1, metrics §21 — 로그인).
//
// - API `flows/market-minute?market=<탭>`(원 단위 — 서버가 백만원 → 원). 빈 분은 null — 선을 끊는다(잇지 않음).
// - 카드 7: 개인·외국인·기관 합계·금융투자·투신(사모 제외 — 사용자 결정 Q7)·연기금·기타법인. 억원.
// - 선 토글: 기본 개인·외국인·기관, 세부(금융투자·투신·사모·은행·보험·종금·연기금·기타단체)·기타법인은 끔.
//   켜고 끈 상태와 시장 탭은 URL 쿼리(f2m·f2l).
// - 머리 꼬리표 `수집 HH:MM · 60초 갱신 · 잠정` + 거래소 `KRX(잠금)` — 이 TR 에는 거래소 입력이 없다(F2 §1.3).
// - 상태를 따로 보인다: 확인 전(꺼진 조합 — #39)·장 시작 전·휴장일(직전 거래일 선)·아직 없음(404).
import type { components } from "../../api/types.gen";
import type { PageContext } from "../../app/types";
import type { Envelope } from "../../data/source";
import { h } from "../../ui/dom";
import { DASH, eok, kstTime, shortDate, signClass } from "../../ui/fmt";
import { lineChart } from "../../ui/line";
import type { Panel } from "../../ui/panel";
import { markQuality } from "../../ui/quality";
import { type Option, seg } from "../../ui/seg";
import { fill, filters } from "../p1_market";
import { readList, readParam, writeParams } from "./state";

type S = components["schemas"];
export type MarketMinute = S["MarketMinute"];

export const MINUTE_TITLE = "장중 투자자 수급";
export const MINUTE_HINT = "flows.market_minute 09:00~15:40 매분";
export const MINUTE_REFRESH_MS = 60_000;

export type MinuteTab =
  | "kospi"
  | "kosdaq"
  | "k200_fut"
  | "k200_call"
  | "k200_put"
  | "stock_fut";
/** 시장 탭(F2 §0-1 — 주식선물은 실측 #39 뒤). 기본은 GX 가 실측한 코스피200 선물 */
export const MINUTE_TABS: readonly Option<MinuteTab>[] = [
  { value: "kospi", label: "코스피" },
  { value: "kosdaq", label: "코스닥" },
  { value: "k200_fut", label: "코스피200 선물" },
  { value: "k200_call", label: "콜옵션" },
  { value: "k200_put", label: "풋옵션" },
  { value: "stock_fut", label: "주식선물" },
];
export const DEFAULT_TAB: MinuteTab = "k200_fut";
export const LINE_IDS = [
  "prsn",
  "frgn",
  "orgn",
  "scrt",
  "ivtr",
  "pe_fund",
  "bank",
  "insu",
  "mrbn",
  "fund",
  "etc_orgt",
  "etc_corp",
] as const;
export type LineId = (typeof LINE_IDS)[number];

const STATUS_TEXT: Record<string, string> = {
  before_open: "장 시작 전 — 09:00부터 매분 수집. 아래는 직전 거래일 선",
  holiday: "휴장일 — 아래는 직전 거래일 선",
};

/** 머리 꼬리표: `수집 HH:MM · 60초 갱신 · 잠정` */
export function minuteTag(d: MarketMinute): string {
  const parts = [
    `수집 ${d.collected_at ? kstTime(d.collected_at) : DASH}`,
    `${String(d.refresh_s)}초 갱신`,
  ];
  if (d.provisional) parts.push("잠정");
  return parts.join(" · ");
}

function cards(d: MarketMinute): HTMLElement {
  return h(
    "div",
    {
      class: "f2-cards",
      attrs: { role: "list", "aria-label": "투자자별 오늘 누적 순매수(억원)" },
    },
    d.cards.map((c) => {
      const v = h(
        "b",
        { class: signClass(c.net_value) },
        eok(c.net_value, 0, true),
      );
      if (c.quality) markQuality(v, c.quality);
      return h(
        "div",
        {
          class: "f2-card",
          attrs: { role: "listitem" },
          data: { investor: c.investor },
        },
        h("span", null, c.label),
        v,
      );
    }),
  );
}

/** 봉투 → 패널 본문. on = 켤 선(없으면 응답의 기본값). onToggle = 켜진 선이 바뀔 때. */
export function drawMinute(
  p: Panel,
  env: Envelope<MarketMinute>,
  on: readonly string[] | null = null,
  onToggle?: (ids: string[]) => void,
): number | null {
  const d = env.data;
  if (d.status === "not_confirmed") {
    p.body.append(
      h(
        "p",
        { class: "msg f2-state", data: { status: d.status } },
        `확인 전 — 이 시장 조합은 실측(#39) 뒤에 켠다. 값을 지어내지 않는다.`,
      ),
    );
    p.setSource({ ...env, quality: env.quality });
    return null;
  }
  p.body.append(
    h(
      "p",
      { class: "f2-head" },
      h("span", { class: "f2-tag" }, minuteTag(d)),
      " · ",
      h(
        "span",
        {
          class: "venue locked",
          attrs: {
            title: "분 단위 선은 거래소 입력이 없는 TR — KIS 기본값(KRX)",
          },
        },
        `${d.venue}(잠금)`,
      ),
      ` · ${shortDate(d.trade_date)}`,
    ),
  );
  const note = STATUS_TEXT[d.status];
  if (note)
    p.body.append(
      h("p", { class: "msg f2-state", data: { status: d.status } }, note),
    );
  p.body.append(cards(d));
  const want = new Set(
    on ?? d.lines.filter((l) => l.default_on).map((l) => l.investor),
  );
  const chart = lineChart({
    label: "투자자별 누적 순매수(분, 억원)",
    x: d.axis,
    series: d.lines.map((l) => ({
      label: l.label,
      values: l.values.map((v) => (v === null ? null : v / 1e8)),
    })),
    zero: true,
    toggle: true,
    hidden: d.lines.filter((l) => !want.has(l.investor)).map((l) => l.label),
    format: (v) =>
      `${v >= 0 ? "+" : ""}${Math.round(v).toLocaleString("ko-KR")}억`,
  });
  chart.el.dataset.lines = "minute";
  chart.el.addEventListener("click", () => {
    onToggle?.(
      chart
        .visible()
        .map((k) => d.lines[k]?.investor ?? "")
        .filter(Boolean),
    );
  });
  p.body.append(chart.el);
  p.setSource(env);
  return null;
}

/** 패널에 시장 탭을 달고, 불러오기 함수를 돌려준다(p1_market 의 60초 갱신이 부른다). */
export function mountMinute(ctx: PageContext, p: Panel): () => Promise<void> {
  const q = {
    tab:
      readParam<MinuteTab>(
        "f2m",
        MINUTE_TABS.map((o) => o.value),
      ) ?? DEFAULT_TAB,
    lines: readList<LineId>("f2l", LINE_IDS) as string[] | null,
  };
  const load = (): Promise<void> =>
    fill(
      p,
      () =>
        ctx.source.get<MarketMinute>("flows/market-minute", { market: q.tab }),
      (env) =>
        drawMinute(p, env, q.lines, (ids) => {
          q.lines = ids;
          writeParams({ f2l: ids.join(",") });
        }),
      MINUTE_HINT,
    );
  filters(
    p,
    seg<MinuteTab>({
      label: "투자자 수급 시장",
      options: MINUTE_TABS,
      value: q.tab,
      onChange: (v) => {
        q.tab = v;
        writeParams({ f2m: v });
        void load();
      },
    }).el,
  );
  p.setDef(
    "그날 누적 순매수(1분마다 받아 쌓음 — 못 받은 분은 빈칸으로 끊는다). 투신은 사모 제외(KRX 표기). 기관 세부·기타법인 선은 범례로 켠다.",
  );
  return load;
}
