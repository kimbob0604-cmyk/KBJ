// 페이지 12 ⑤ 포트폴리오·수익률 저널 — 합계·포지션 표·월별 수익률 막대·최근 매매(알림 연결 표시)·매매 입력(docs/p8_design.md
// §4.7·§6, 묶음 JNL).
//
// - 포지션·손익은 서버가 매매 기록에서 읽을 때 계산(이동평균 단가). 평가 손익은 최신 확정 종가(기준일·품질 표시) — 종가가
//   없으면 '—'(0 으로 채우지 않는다). 월별 수익률은 [확인 필요 — 현금 원장 없음] 안내가 봉투 notes 로 온다.
// - 매매 입력: 쓰기 채널 `post('journal/trades')`. 보유보다 많이 팔면 서버가 422(negative_holding) — 사유만 보인다.
// - 막대 차트는 반폭 패널 안(전체 폭 차트 금지 — 글자가 커진다). 공개 빌드는 이 모듈을 번들에서 뺀다.
import type { components } from '../../api/types.gen';
import type { PageContext } from '../../app/types';
import type { Envelope } from '../../data/source';
import { barsChart } from '../../ui/bars';
import { h } from '../../ui/dom';
import { choiceField, formRow, numberField, textField } from '../../ui/form';
import { kstDate, num, pct, signClass } from '../../ui/fmt';
import { kv } from '../../ui/kv';
import { errorText, type Panel } from '../../ui/panel';
import { type Column, table } from '../../ui/table';
import { nil, CODE_RE, DATE_RE, failPanel, journalWriter, kstToday, NO_WRITER, status, won } from './journal';

type S = components['schemas'];
export type Portfolio = S['Portfolio'];
export type PositionOut = S['PositionOut'];
export type TradeList = S['TradeList'];
export type TradeOut = S['TradeOut'];
export type TradeIn = S['TradeIn'];

export const PORTFOLIO_HINT = '아직 없음 — 매매 기록 없음(아래에서 입력)';
export const RECENT_TRADES = 20;

function signed(v: number | null | undefined): HTMLElement {
  return h('span', { class: signClass(v) }, nil(v) ? '—' : `${num(v, 0, true)}원`);
}

const POS_COLUMNS: readonly Column<PositionOut>[] = [
  {
    key: 'code',
    label: '종목',
    align: 'l',
    cell: (r) => (r.name ? `${r.name} ${r.code}` : r.code),
  },
  { key: 'qty', label: '수량', cell: (r) => num(r.qty) },
  {
    key: 'avg',
    label: '평단',
    cell: (r) => (nil(r.avg_cost) ? null : num(r.avg_cost, 0)),
  },
  {
    key: 'close',
    label: '종가',
    cell: (r) => (nil(r.last_close) ? null : `${num(r.last_close)} (${kstDate(r.close_date)})`),
    cls: (r) => (r.quality && r.quality !== 'ok' ? 'warn' : undefined),
  },
  { key: 'u', label: '평가손익', cell: (r) => signed(r.unrealized) },
  {
    key: 'upct',
    label: '수익률',
    cell: (r) => h('span', { class: signClass(r.unrealized_pct) }, pct(r.unrealized_pct)),
  },
  { key: 'r', label: '실현', cell: (r) => signed(r.realized) },
];

const SIDE: Readonly<Record<string, string>> = { buy: '매수', sell: '매도' };

const TRADE_COLUMNS: readonly Column<TradeOut>[] = [
  { key: 'd', label: '날짜', align: 'l', cell: (t) => t.trade_date },
  {
    key: 'c',
    label: '종목',
    align: 'l',
    cell: (t) => (t.name_at_trade ? `${t.name_at_trade} ${t.code}` : t.code),
  },
  {
    key: 's',
    label: '구분',
    align: 'l',
    cell: (t) => SIDE[t.side] ?? t.side,
    cls: (t) => (t.side === 'buy' ? 'up' : 'down'),
  },
  { key: 'p', label: '가격', cell: (t) => num(t.price) },
  { key: 'q', label: '수량', cell: (t) => num(t.qty) },
  {
    key: 'a',
    label: '알림',
    align: 'l',
    cell: (t) => (t.signal_id ? '알림 연결' : null),
  },
];

/** 매매 입력 칸 → 본문(형식 오류는 사유 글자) */
export function tradeForm(today: string): {
  el: HTMLElement;
  body(): TradeIn | string;
} {
  const code = textField({
    label: '종목코드',
    maxLength: 6,
    placeholder: '005930',
  });
  const side = choiceField<'buy' | 'sell'>({
    label: '구분',
    options: [
      { value: 'buy', label: '매수' },
      { value: 'sell', label: '매도' },
    ],
    value: 'buy',
  });
  const date = textField({ label: '날짜', value: today, maxLength: 10 });
  const price = numberField({ label: '가격', min: 0, required: true });
  const qty = numberField({
    label: '수량',
    min: 1,
    integer: true,
    required: true,
  });
  const fee = numberField({ label: '수수료', min: 0 });
  const tax = numberField({ label: '세금', min: 0 });
  const el = h(
    'div',
    { class: 'jr-editor' },
    formRow(code.el, side.el, date.el),
    formRow(price.el, qty.el, fee.el, tax.el),
  );
  return {
    el,
    body() {
      const c = code.value().toUpperCase();
      if (!CODE_RE.test(c)) return '종목코드 형식(6자리)';
      if (!DATE_RE.test(date.value())) return '날짜 형식(YYYY-MM-DD)';
      const p = price.value();
      const q = qty.value();
      if (p === null || p <= 0) return '가격(0보다 큰 수)';
      if (q === null) return '수량(1 이상 정수)';
      return {
        code: c,
        side: side.value(),
        trade_date: date.value(),
        price: p,
        qty: q,
        fee: fee.value() ?? 0,
        tax: tax.value() ?? 0,
      };
    },
  };
}

export async function fillPortfolio(ctx: PageContext, p: Panel): Promise<void> {
  const w = journalWriter(ctx.source);
  let env: Envelope<Portfolio>;
  let trades: TradeOut[];
  try {
    env = await ctx.source.get<Portfolio>('portfolio');
    trades = (await ctx.source.get<TradeList>('journal/trades')).data.items;
  } catch (err) {
    failPanel(p, err, PORTFOLIO_HINT);
    return;
  }
  const d = env.data;
  p.clearBody();
  p.setSource(env);
  const msg = h('p', { class: 'msg' }, w ? '' : NO_WRITER);
  const form = tradeForm(kstToday(ctx.now()));
  const save = h('button', { class: 'btn', attrs: { type: 'button', disabled: !w } }, '매매 넣기');
  save.addEventListener('click', () => {
    if (!w) return;
    const b = form.body();
    if (typeof b === 'string') {
      status(msg, false, b);
      return;
    }
    w.post<TradeOut>('journal/trades', b).then(
      () => fillPortfolio(ctx, p),
      (err: unknown) => {
        status(msg, false, errorText(err));
      },
    );
  });
  const held = d.positions.filter((x) => x.qty > 0);
  p.body.append(
    kv([
      {
        label: '실현 손익 합',
        value: won(d.realized_total),
        cls: signClass(d.realized_total),
      },
      {
        label: '평가 손익 합',
        value: won(d.unrealized_total),
        cls: signClass(d.unrealized_total),
      },
      { label: '보유 종목', value: `${held.length}개` },
      { label: '매매 기록', value: `${d.trades_n}건` },
    ]),
    d.trades_n === 0
      ? h('p', { class: 'msg' }, PORTFOLIO_HINT)
      : table<PositionOut>({
          caption: '포지션',
          columns: POS_COLUMNS,
          rows: d.positions,
          rowKey: (r) => r.code,
        }).el,
    d.monthly.length
      ? barsChart({
          label: '월별 수익률(%)',
          items: d.monthly.map((m) => ({
            label: m.month,
            value: m.ret_pct ?? null,
            detail: `실현 ${won(m.realized)} · 평가 변화 ${won(m.unrealized_change)}`,
          })),
          format: (v) => pct(v),
        })
      : '',
    trades.length
      ? table<TradeOut>({
          caption: `최근 매매(${Math.min(trades.length, RECENT_TRADES)}건)`,
          columns: TRADE_COLUMNS,
          rows: trades.slice(0, RECENT_TRADES),
          rowKey: (t) => String(t.id),
        }).el
      : '',
    h('h3', { class: 'sub' }, '매매 입력'),
    form.el,
    formRow(save),
    msg,
  );
}
