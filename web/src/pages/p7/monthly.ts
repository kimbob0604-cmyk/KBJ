// 페이지 7 '월별 추이' 탭(metrics §29a — 2026-10-20 사용자 요청: 쌓아서 보고 QoQ·YoY). 월별 수출(수입) 합을
// 상위 HS2 류 + 기타로 쌓은 막대, 전년동월비 선, 최근 분기 QoQ·YoY 요약·분기 표.
// 로그인: 'trade/monthly'(API), 공개: 같은 이름 파일 — 수출·수입이 한 응답에 같이 온다(전환에 다시 부르지 않는다).
import { h } from '../../ui/dom';
import { kv } from '../../ui/kv';
import { lineChart } from '../../ui/line';
import { seg } from '../../ui/seg';
import { stackBar } from '../../ui/stackbar';
import { signClass } from '../../ui/fmt';
import { type Column, table } from '../../ui/table';
import {
  failNote,
  ratio,
  type TabCtx,
  TRADE_HINT,
  type TradeMonthly,
  tradePanel,
  usd,
} from './common';

export const TITLE = '월별 수출입 추이';
type Flow = 'exp' | 'imp';
type Stack = TradeMonthly['exp'];
type Quarter = Stack['quarters'][number];

const FLOW_LABEL: Readonly<Record<Flow, string>> = { exp: '수출', imp: '수입' };

/** '2026-09' → '26.09' */
export function shortYm(ym: string): string {
  return `${ym.slice(2, 4)}.${ym.slice(5, 7)}`;
}

/** 최신 달·최근 분기 요약 줄 */
export function summary(st: Stack): [string, string][] {
  const m = st.months.at(-1);
  const q = st.quarters.at(-1);
  const out: [string, string][] = [];
  if (m) {
    out.push([`${m.ym} ${FLOW_LABEL[st.flow]}`, usd(m.total)]);
    out.push(['전월비', ratio(m.mom)]);
    out.push(['전년동월비', ratio(m.yoy)]);
  }
  if (q) {
    out.push([`${q.q} 합`, usd(q.total)]);
    out.push(['QoQ(직전 분기)', ratio(q.qoq)]);
    out.push(['YoY(전년 같은 분기)', ratio(q.yoy)]);
  }
  return out;
}

const Q_COLS: readonly Column<Quarter>[] = [
  { key: 'q', label: '분기', align: 'l', cell: (r) => r.q },
  { key: 't', label: '합계', cell: (r) => usd(r.total) },
  {
    key: 'qoq',
    label: 'QoQ',
    cell: (r) => ratio(r.qoq),
    cls: (r) => signClass(r.qoq ?? null),
  },
  {
    key: 'yoy',
    label: 'YoY',
    cell: (r) => ratio(r.yoy),
    cls: (r) => signClass(r.yoy ?? null),
  },
];

export function draw(body: HTMLElement, st: Stack): void {
  const cats = st.months.map((m) => shortYm(m.ym));
  const series = st.chapters.map((c) => ({
    label: c.hs === 'other' ? c.name : `${c.hs} ${c.name}`,
    values: st.months.map((m) => m.parts[c.hs] ?? null),
  }));
  body.replaceChildren(
    kv(summary(st)),
    stackBar({
      label: `월별 ${FLOW_LABEL[st.flow]}(달러) — 상위 류 + 기타`,
      categories: cats,
      series,
      format: (v) => usd(v),
      h: 260,
    }),
    lineChart({
      label: `${FLOW_LABEL[st.flow]} 전년동월비(%)`,
      x: cats,
      series: [
        {
          label: '전년동월비',
          values: st.months.map((m) => (m.yoy === null ? null : m.yoy * 100)),
        },
      ],
      zero: true,
      format: (v) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`,
      h: 140,
    }).el,
    table({
      caption: '분기 합계 — 세 달이 다 있는 분기만',
      columns: Q_COLS,
      rows: [...st.quarters].reverse(),
      pageSize: 8,
      empty: '분기 없음',
    }).el,
  );
}

export async function mount(t: TabCtx, el: HTMLElement): Promise<void> {
  const p = tradePanel(t, { title: TITLE, span: 12 });
  p.setDef(
    '월 합계 = 그 달 HS10 품목 합(달러, 원화 환산 없음). 쌓기 = 최근 12개월 합 상위 6개 류 + 기타. 전월비·전년동월비 = 그 달 ÷ 비교 달 − 1, QoQ = 분기 합 ÷ 직전 분기 − 1, YoY = ÷ 전년 같은 분기 − 1.',
  );
  el.append(h('div', { class: 'grid' }, p.el));
  try {
    const env = await t.source.get<TradeMonthly>('trade/monthly');
    p.setSource(env);
    let flow: Flow = 'exp';
    const body = h('div');
    const sw = seg<Flow>({
      label: '수출·수입',
      options: [
        { value: 'exp', label: '수출' },
        { value: 'imp', label: '수입' },
      ],
      value: flow,
      onChange: (v) => {
        flow = v;
        draw(body, env.data[flow]);
      },
    });
    p.clearBody();
    p.body.append(
      sw.el,
      body,
    );
    draw(body, env.data[flow]);
  } catch (err) {
    p.clearBody();
    p.body.append(failNote(err, TRADE_HINT.monthly));
  }
}
