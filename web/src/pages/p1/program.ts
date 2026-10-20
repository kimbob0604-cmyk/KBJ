// 페이지 1 '프로그램매매' — 일별 60거래일 막대 + 순매수/순매도 상위 20(docs/p5_flow_program_prompt.md §0-2·§0-3, metrics §22 — 로그인).
//
// - API `program/daily?market=K|Q&days=60`, `program/top?market=&side=buy|sell&scope=intraday|close`. 금액은 원 → 화면에서만 억.
// - 막대 = 전체 순매수, 막대에 올리면(초점·누름) 차익·비차익·위탁·자기. 아래 줄 `YYYY-MM-DD · 합계 · 비차익 · 차익 · 지수`.
// - **상위 표는 범위를 숨기지 않는다**(F2 §1.4 — KIS 에 프로그램 순위 TR 이 없어 종목 TR 로 범위를 훑는다): 응답의
//   `scope_label`(장중 '거래대금 상위 N + 관심종목' / 마감 '전 종목(마감 확정)')을 표 위에 `범위: …` 로.
// - 종목을 누르면 페이지 8(종목 상세). 탭 상태는 URL 쿼리(pgm·pgs·pgc).
import type { components } from "../../api/types.gen";
import { openStock } from "../../app/route";
import type { PageContext } from "../../app/types";
import type { Envelope } from "../../data/source";
import { barsChart } from "../../ui/bars";
import { h } from "../../ui/dom";
import { DASH, eok, eokNum, num, pct, signClass } from "../../ui/fmt";
import type { Panel } from "../../ui/panel";
import { visibleRows } from "../../ui/quality";
import { type Option, seg } from "../../ui/seg";
import { type Column, table } from "../../ui/table";
import { fill, filters, nameCell } from "../p1_market";
import { readParam, writeParams } from "./state";

type S = components["schemas"];
export type ProgramDaily = S["ProgramDaily"];
export type ProgramTop = S["ProgramTop"];
type DayBar = S["ProgramDayBar"];
type TopRow = S["ProgramTopRow"];

export const DAILY_TITLE = "프로그램 순매수(일별)";
export const TOP_TITLE = "프로그램 순매수 상위";
export const DAILY_HINT = "program.daily 15:50(확인 전 #40)";
export const TOP_HINT =
  "program.stock_scan 10분 · program.stock_sweep 18:10(확인 전 #41)";

export type PgMarket = "K" | "Q";
export type PgSide = "buy" | "sell";
export type PgScope = "intraday" | "close";
export const MARKETS: readonly Option<PgMarket>[] = [
  { value: "K", label: "코스피" },
  { value: "Q", label: "코스닥" },
];
export const SIDES: readonly Option<PgSide>[] = [
  { value: "buy", label: "순매수 상위" },
  { value: "sell", label: "순매도 상위" },
];
export const SCOPES: readonly Option<PgScope>[] = [
  { value: "intraday", label: "장중" },
  { value: "close", label: "마감" },
];

/** 막대 위 상세: '차익 +1억 · 비차익 -2억 · 위탁 +3억 · 자기 -4억' */
export function barDetail(b: DayBar): string {
  return [
    `차익 ${eok(b.arbt_net, 0, true)}`,
    `비차익 ${eok(b.nabt_net, 0, true)}`,
    `위탁 ${eok(b.entm_net, 0, true)}`,
    `자기 ${eok(b.onsl_net, 0, true)}`,
  ].join(" · ");
}

/** 아래 줄: `YYYY-MM-DD · 합계 · 비차익 · 차익 · 지수` */
export function dayLine(b: DayBar): string {
  return [
    b.date,
    `합계 ${eok(b.net, 0, true)}`,
    `비차익 ${eok(b.nabt_net, 0, true)}`,
    `차익 ${eok(b.arbt_net, 0, true)}`,
    `지수 ${b.index_close === null ? DASH : num(b.index_close, 2)}`,
  ].join(" · ");
}

/** 막대 아래 표 — 날짜별 합계·차익·비차익·위탁·자기(막대만으로는 값이 안 보인다 — 2026-10-20). */
export const RECENT_ROWS = 5;
const eokCell = (v: number | null) => h("span", { class: signClass(v) }, eokNum(v, 0, true));
export const DAY_COLUMNS: readonly Column<DayBar>[] = [
  { key: "date", label: "날짜", align: "l", cell: (b) => b.date },
  { key: "net", label: "합계", cell: (b) => eokCell(b.net) },
  { key: "arbt", label: "차익", cell: (b) => eokCell(b.arbt_net) },
  { key: "nabt", label: "비차익", cell: (b) => eokCell(b.nabt_net) },
  { key: "entm", label: "위탁", cell: (b) => eokCell(b.entm_net) },
  { key: "onsl", label: "자기", cell: (b) => eokCell(b.onsl_net) },
];

export function drawDaily(p: Panel, env: Envelope<ProgramDaily>): number {
  const d = env.data;
  const shown = d.rows.map((b) =>
    b.quality === "invalid" ? { ...b, net: null } : b,
  );
  p.body.append(
    barsChart({
      label: `프로그램 순매수 ${String(d.days)}거래일(억원)`,
      items: shown.map((b) => ({
        label: b.date,
        value: b.net === null ? null : b.net / 1e8,
        detail: barDetail(b),
      })),
      format: (v) =>
        `${v >= 0 ? "+" : ""}${Math.round(v).toLocaleString("ko-KR")}억`,
    }),
  );
  const last = [...d.rows].reverse().find((b) => b.quality !== "invalid");
  if (last) p.body.append(h("p", { class: "f2-dayline" }, dayLine(last)));
  const recent = [...shown].reverse().slice(0, RECENT_ROWS);
  p.body.append(
    table<DayBar>({
      caption: `최근 ${String(recent.length)}거래일 프로그램 순매수(억)`,
      columns: DAY_COLUMNS,
      rows: recent,
      rowKey: (b) => b.date,
    }).el,
  );
  return d.rows.filter((b) => b.quality === "invalid").length;
}

export const TOP_COLUMNS: readonly Column<TopRow>[] = [
  { key: "rank", label: "순위", cell: (r) => num(r.rank) },
  {
    key: "name",
    label: "종목",
    align: "l",
    cls: () => "nm",
    cell: (r) => nameCell(r.name, r.code),
  },
  { key: "price", label: "현재가", cell: (r) => num(r.price) },
  {
    key: "chg",
    label: "등락",
    cell: (r) => h("span", { class: signClass(r.chg_pct) }, pct(r.chg_pct)),
  },
  {
    key: "net",
    label: "순매수(억)",
    cell: (r) => eokNum(r.net_value, 0, true),
    cls: (r) => signClass(r.net_value),
  },
  { key: "buy", label: "매수(억)", cell: (r) => eokNum(r.buy_value) },
  { key: "sell", label: "매도(억)", cell: (r) => eokNum(r.sell_value) },
];

export function drawTop(p: Panel, env: Envelope<ProgramTop>): number {
  const d = env.data;
  const { rows, invalid } = visibleRows(d.rows);
  p.body.append(
    h(
      "p",
      { class: "f2-scope", data: { scope: d.scope } },
      `범위: ${d.scope_label}`,
    ),
    table<TopRow>({
      caption: `${d.market === "K" ? "코스피" : "코스닥"} 프로그램 ${d.side === "buy" ? "순매수" : "순매도"} 상위 ${String(rows.length)}`,
      columns: TOP_COLUMNS,
      rows,
      rowKey: (r) => r.code,
      onSelect: (r) => void openStock(r.code),
      pageSize: 20,
      empty: d.side === "buy" ? "순매수 종목 없음" : "순매도 종목 없음",
    }).el,
  );
  return invalid;
}

/** 두 패널에 탭을 달고, 불러오기 함수를 돌려준다(p1_market 의 60초 갱신이 부른다). */
export function mountProgram(
  ctx: PageContext,
  pDaily: Panel,
  pTop: Panel,
): () => Promise<void> {
  const q = {
    market: readParam<PgMarket>("pgm", ["K", "Q"]) ?? "K",
    side: readParam<PgSide>("pgs", ["buy", "sell"]) ?? "buy",
    scope: readParam<PgScope>("pgc", ["intraday", "close"]) ?? "intraday",
  };
  const loadDaily = (): Promise<void> =>
    fill(
      pDaily,
      () =>
        ctx.source.get<ProgramDaily>("program/daily", {
          market: q.market,
          days: 60,
        }),
      (env) => drawDaily(pDaily, env),
      DAILY_HINT,
    );
  const loadTop = (): Promise<void> =>
    fill(
      pTop,
      () =>
        ctx.source.get<ProgramTop>("program/top", {
          market: q.market,
          side: q.side,
          scope: q.scope,
        }),
      (env) => drawTop(pTop, env),
      TOP_HINT,
    );
  const load = async (): Promise<void> => {
    await Promise.all([loadDaily(), loadTop()]);
  };
  const marketSeg = (label: string) =>
    seg<PgMarket>({
      label,
      options: MARKETS,
      value: q.market,
      onChange: (v) => {
        q.market = v;
        writeParams({ pgm: v });
        for (const s of segs) s.set(v);
        void load();
      },
    });
  const segs = [
    marketSeg("프로그램 일별 시장"),
    marketSeg("프로그램 상위 시장"),
  ];
  filters(pDaily, segs[0]?.el ?? h("span"));
  filters(
    pTop,
    segs[1]?.el ?? h("span"),
    seg<PgSide>({
      label: "프로그램 상위 방향",
      options: SIDES,
      value: q.side,
      onChange: (v) => {
        q.side = v;
        writeParams({ pgs: v });
        void loadTop();
      },
    }).el,
    seg<PgScope>({
      label: "프로그램 상위 범위",
      options: SCOPES,
      value: q.scope,
      onChange: (v) => {
        q.scope = v;
        writeParams({ pgc: v });
        void loadTop();
      },
    }).el,
  );
  pDaily.setDef(
    "막대 = 전체(차익+비차익) 순매수. 막대를 누르거나 초점을 주면 차익·비차익·위탁·자기.",
  );
  pTop.setDef(
    "KIS 에는 프로그램 순위 TR 이 없어 종목별 TR 로 범위를 훑어 순위를 만든다 — 장중은 거래대금 상위 N + 관심종목, 마감 뒤는 전 종목.",
  );
  return load;
}
