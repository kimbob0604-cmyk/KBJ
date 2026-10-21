// 페이지 7 '종목' 탭(P10 단계 1 — 맨 앞 탭) '매핑 종목 랭킹' 표(docs/p6_design.md §4.6·§6, metrics §28 — 묶음 MAP) +
// 그 아래 '종목별 무역 흐름'(p7/flows.ts — 종목 이름을 누르면 그 종목 흐름). 종목↔HS 매핑표 종목의 최근 3개월 수출 지수
// YoY 내림차순(값 없음은 뒤, 동률은 코드 순 — 서버가 정렬해 준다). 서명은 p7/common.ts TabMount.
//
// - 키 'trade/stocks' — 로그인은 API(공개·local 매핑), 공개는 공개 파일(공개 매핑만 — origin 칸 없음).
// - 줄마다 상태(겹침 부족·매출 없음·자료 없음)·'검토 전' 꼬리표. 금액·비율은 달러 기준(원화 환산 없음).
import type { Envelope } from "../../data/source";
import { h } from "../../ui/dom";
import { toCsv } from "../../ui/csv";
import { panel } from "../../ui/panel";
import { seg } from "../../ui/seg";
import { sparkline } from "../../ui/svg";
import { type Column, table } from "../../ui/table";
import { failed } from "../p1_market";
import {
  csvMeta,
  ratio,
  tableHead,
  type TabCtx,
  TRADE_HINT,
  tradePanel,
  type TradeStocks,
} from "./common";
import { flowsPanel, TITLE as FLOWS_TITLE } from "./flows";

export const TITLE = "맵핑 트렌드 — 매핑 종목 랭킹";
/** 급등락: 최근 1개월 또는 3개월 수출 지수 YoY 의 절댓값이 이 이상 */
export const SWING = 0.3;
export type SortKey = "yoy_3m" | "yoy_1m" | "code";
type Row = TradeStocks["rows"][number] & { origin?: "public" | "local" };
type Ranked = Row & { __rank?: number };

export const STATUS_LABEL: Readonly<Record<string, string>> = {
  ok: "",
  no_trade: "자료 없음",
  no_revenue: "매출 없음",
  short_overlap: "겹침 부족",
};

const tagEl = (text: string, cls = "dim"): HTMLSpanElement =>
  h("span", { class: `tag ${cls}` }, text);

export function stocksCsv(rows: readonly Row[], basis: string): string {
  return toCsv<Row>(
    rows,
    [
      { label: "순위", value: (r) => rows.indexOf(r) + 1 },
      { label: "종목코드", value: "code" },
      { label: "종목", value: "name" },
      { label: "3개월 수출 지수 YoY", value: "yoy_3m" },
      { label: "상태", value: (r) => STATUS_LABEL[r.status] || "정상" },
      { label: "검토", value: (r) => (r.reviewed ? "검토" : "검토 전") },
    ],
    { meta: [...csvMeta(basis), ["매핑", "자체 큐레이션(관세청+DART 매출)"]] },
  );
}

/** 필터·정렬(순수 — 시험용). 산업 '' = 전체 */
export function filterRows(
  rows: readonly Row[],
  o: { sector: string; swing: boolean; sort: SortKey },
): Row[] {
  const big = (v: number | null | undefined): boolean =>
    typeof v === "number" && Math.abs(v) >= SWING;
  const out = rows.filter(
    (r) =>
      (!o.sector || r.sector === o.sector) &&
      (!o.swing || big(r.yoy_1m) || big(r.yoy_3m)),
  );
  if (o.sort === "code")
    return [...out].sort((a, b) => a.code.localeCompare(b.code));
  if (o.sort === "yoy_1m") {
    const v = (r: Row): number =>
      typeof r.yoy_1m === "number" ? r.yoy_1m : Number.NEGATIVE_INFINITY;
    return [...out].sort((a, b) => v(b) - v(a) || a.code.localeCompare(b.code));
  }
  return out; // 서버 순서(3개월 YoY 내림차순)
}

/** 산업 칩 목록(많은 순) */
export function sectors(
  rows: readonly Row[],
): { value: string; label: string }[] {
  const n = new Map<string, number>();
  for (const r of rows)
    if (r.sector) n.set(r.sector, (n.get(r.sector) ?? 0) + 1);
  const list = [...n.entries()].sort(
    (a, b) => b[1] - a[1] || a[0].localeCompare(b[0]),
  );
  return [
    { value: "", label: `전체(${String(rows.length)})` },
    ...list.map(([k, c]) => ({ value: k, label: `${k}(${String(c)})` })),
  ];
}

function columns(onPick?: (code: string) => void): Column<Ranked>[] {
  return [
    { key: "rank", label: "순위", cell: (r) => String(r.__rank ?? "") },
    {
      key: "name",
      label: "종목",
      align: "l",
      cell: (r) =>
        h(
          "span",
          {},
          onPick
            ? h(
                "button",
                {
                  class: "link",
                  attrs: { type: "button" },
                  on: { click: () => onPick(r.code) },
                },
                `${r.name} (${r.code})`,
              )
            : `${r.name} (${r.code})`,
          r.reviewed ? null : tagEl("검토 전", "warn"),
          r.origin === "local" ? tagEl("로그인 전용") : null,
        ),
    },
    { key: "sector", label: "산업", align: "l", cell: (r) => r.sector ?? "" },
    { key: "last", label: "최신월", cell: (r) => r.last_ym ?? "" },
    {
      key: "yoy1",
      label: "1개월 YoY",
      cell: (r) => ratio(r.yoy_1m),
      cls: (r) =>
        typeof r.yoy_1m !== "number" ? "dim" : r.yoy_1m >= 0 ? "up" : "down",
    },
    {
      key: "spark",
      label: "12개월",
      cell: (r) =>
        r.spark?.length
          ? sparkline(r.spark, {
              label: `${r.name} 12개월 수출 지수`,
              w: 80,
              h: 20,
            })
          : "",
    },
    {
      key: "yoy",
      label: "3개월 YoY",
      cell: (r) => ratio(r.yoy_3m),
      cls: (r) => (r.yoy_3m === null ? "dim" : r.yoy_3m >= 0 ? "up" : "down"),
    },
    {
      key: "status",
      label: "상태",
      align: "l",
      cell: (r) =>
        STATUS_LABEL[r.status] ? tagEl(STATUS_LABEL[r.status] ?? "") : "정상",
    },
  ];
}

export async function mount(t: TabCtx, el: HTMLElement): Promise<void> {
  const p = tradePanel(t, {
    title: TITLE,
    def: "매핑 품목 수출의 가중합(종목 수출 지수) 최근 3개월 합의 전년 대비",
  });
  el.append(h("div", { class: "grid" }, p.el));
  let env: Envelope<TradeStocks>;
  try {
    env = await t.source.get<TradeStocks>("trade/stocks");
  } catch (err) {
    failed(p, err, TRADE_HINT.stocks);
    return;
  }
  const rows: Ranked[] = env.data.rows.map((r, i) => ({ ...r, __rank: i + 1 }));
  const basis =
    env.notes.find((n) => n.startsWith("랭킹")) ?? env.as_of.slice(0, 10);
  // 흐름 패널(로그인 전용) — 공개 빌드는 자물쇠만(흐름·프리뷰 모듈을 번들에서 뺀다 — p7_trade.ts 산업 탭과 같은 규칙)
  const flows =
    import.meta.env.MODE === "public"
      ? null
      : flowsPanel(t, env.data.rows);
  el.querySelector(":scope > .grid")?.append(
    flows?.el ??
      panel(t.tier, { title: FLOWS_TITLE, tier: "login", span: 12 }).el,
  );
  const pick =
    t.tier === "login" && flows
      ? (code: string) => void flows.show(code)
      : undefined;
  const state = { sector: "", swing: false, sort: "yoy_3m" as SortKey };
  const holder = h("div", { class: "trend-table" });
  const draw = (): void => {
    const shown: Ranked[] = filterRows(env.data.rows, state).map((r) => ({
      ...r,
      __rank: rows.findIndex((x) => x.code === r.code) + 1,
    }));
    holder.replaceChildren(
      tableHead(
        `${String(shown.length)}종목 · 매핑판 ${env.data.map_version}`,
        "trade_stocks",
        () => stocksCsv(shown, basis),
      ),
      table<Ranked>({
        columns: columns(pick),
        rows: shown,
        rowKey: (r) => r.code,
        empty: "조건에 맞는 종목 없음",
      }).el,
    );
  };
  const chips = seg<string>({
    label: "산업",
    options: sectors(env.data.rows),
    value: "",
    onChange: (v) => {
      state.sector = v;
      draw();
    },
  });
  const swing = seg<"all" | "swing">({
    label: "급등락",
    options: [
      { value: "all", label: "전체" },
      { value: "swing", label: `급등락만(±${String(SWING * 100)}%)` },
    ],
    value: "all",
    onChange: (v) => {
      state.swing = v === "swing";
      draw();
    },
  });
  const sort = seg<SortKey>({
    label: "정렬",
    options: [
      { value: "yoy_3m", label: "3개월 YoY" },
      { value: "yoy_1m", label: "1개월 YoY" },
      { value: "code", label: "종목코드" },
    ],
    value: "yoy_3m",
    onChange: (v) => {
      state.sort = v;
      draw();
    },
  });
  draw();
  p.clearBody();
  p.body.append(
    h("div", { class: "trend-filters" }, chips.el, swing.el, sort.el),
    holder,
  );
  p.setSource(env);
}
