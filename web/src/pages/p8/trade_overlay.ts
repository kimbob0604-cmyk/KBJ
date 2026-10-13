// 페이지 8 '재무' 탭의 '수출 겹쳐 보기' 패널(docs/p6_design.md §4.6·§6, metrics §28 — 묶음 MAP). 공개 등급:
// 종목 수출 지수(매핑 HS × 관세청 월간, 가중합) 분기 YoY 대 DART 분기 매출 YoY 두 선, 시차 상관(수출이 0·1·2분기 앞섬),
// 매핑 품목 표·근거·'검토 전'·'환율 미조정' 꼬리표, CSV. p8_stock.ts 고리는 WRAP(IDX 배지 코드와 다른 줄).
//
// - 키 하나: 'trade/stock/<코드>' — 로그인은 API, 공개는 공개 파일(trade/stock/<코드>.json, 같은 모양 — origin 칸은 없다).
// - 매핑이 없는 종목은 '매핑 없음 — 매핑표에 없는 종목'(오류가 아니라 안내 줄). 공개판은 파일이 없으면 같은 줄.
// - 상태: no_trade(매핑 접두 자료 없음)·no_revenue(재무 없음 — 매출 선 없음)·short_overlap(8분기 미만 — 상관 없음).
// - 금액은 미화 달러(원화 환산 없음 — 사용자 결정 Q8). 시세는 이 패널에 없다(로그인 월말 종가 줄은 trade_overlay_price).
import type { Envelope } from '../../data/source';
import { downloadCsv, toCsv } from '../../ui/csv';
import { h } from '../../ui/dom';
import { DASH, num, pct } from '../../ui/fmt';
import { kv } from '../../ui/kv';
import { lineChart } from '../../ui/line';
import type { Panel } from '../../ui/panel';
import { table } from '../../ui/table';
import { col, errLine, isNotFound, mkPanel, type S, tabGrid, type TabCtx, tag } from './common';

export type TradeStock = S['TradeStock'];
type Point = TradeStock['quarterly'][number];
type Line = TradeStock['lines'][number];

export const TITLE = '수출 겹쳐 보기';
export const NOT_MAPPED = '매핑 없음 — 매핑표에 없는 종목';
export const HINT = 'trade.stock_index 21:00(매핑 종목 수출 지수)';
export const DRAFT_TAG = '검토 전';
export const SOURCE_TEXT = '관세청(공공데이터포털)+DART';
export const STATUS_TEXT: Readonly<Record<string, string>> = {
  ok: '',
  no_trade: '매핑 접두의 관세청 자료 없음 — 지수 없음',
  no_revenue: '재무(분기 매출) 없음 — 매출 선 없음',
  short_overlap: '겹친 분기가 8개 미만 — 시차 상관 없음(선은 그린다)',
};
const LAGS = ['0', '1', '2'] as const;

/** 데이터 키(로그인 API·공개 파일 같은 이름) */
export const stockKey = (code: string): string => `trade/stock/${code}`;

const fin = (v: number | null | undefined): v is number => typeof v === 'number' && Number.isFinite(v);
/** 비율(0.123) → '+12.3%' */
export const ratio = (v: number | null | undefined): string => (fin(v) ? pct(v * 100, 1, true) : DASH);
/** 달러 → '12.3억 달러'·'4,567만 달러' */
export function usd(v: number | null | undefined): string {
  if (!fin(v)) return DASH;
  return Math.abs(v) >= 1e8 ? `${num(v / 1e8, 1)}억 달러` : `${num(v / 1e4)}만 달러`;
}

/** 404 → 매핑 없음(공개 파일 없음·로그인 no_data 의 서버 문구) 또는 오류 줄 */
export function notMappedText(err: unknown): string {
  return isNotFound(err) ? errLine(err, NOT_MAPPED) : errLine(err, HINT);
}

/** 시차 상관 한 줄 — 'r(0분기) +0.31 · r(1분기) …' 또는 겹침 부족 */
export function lagText(lc: TradeStock['lag_corr']): string {
  const parts = LAGS.filter((k) => k in lc).map((k) => {
    const v = lc[k];
    return `${k}분기 ${fin(v) ? num(v, 2, true) : DASH}`;
  });
  return parts.length ? `시차 상관(수출이 앞섬) ${parts.join(' · ')}` : '시차 상관 없음';
}

/** 분기 표 CSV(보이는 값 그대로 — 비율은 소수) */
export function overlayCsv(d: TradeStock): string {
  return toCsv<Point>(
    d.quarterly,
    [
      { label: '분기', value: 'period' },
      { label: '수출 지수(달러)', value: 'export_usd' },
      { label: '수출 지수 YoY', value: 'yoy' },
      { label: '매출 YoY', value: 'revenue_yoy' },
      { label: '품질', value: 'quality' },
    ],
    {
      meta: [
        ['출처', SOURCE_TEXT],
        ['종목', `${d.name} (${d.code})`],
        ['매핑판', d.map_version],
        ['단위', '미화 달러(원화 환산 없음 — 환율 미조정)'],
      ],
    },
  );
}

function lineLabel(l: Line): string {
  const where = l.region_id ? `지역 ${l.region_id}` : l.countries.length ? l.countries.join('·') : '전 국가';
  return where;
}

/** 패널 본문 그리기(공개·로그인 같은 모양). invalid 행 수 0 */
export function drawOverlay(p: Panel, env: Envelope<TradeStock>): number {
  const d = env.data;
  const head = h(
    'div',
    { class: 'tags' },
    tag(`${d.name} (${d.code})`),
    d.reviewed ? null : tag(DRAFT_TAG, 'warn', '매핑 가중치는 자체 큐레이션 초안 — 사람이 아직 확인하지 않았다'),
    tag(d.fx_note, 'dim'),
    d.origin === 'local' ? tag('로그인 전용 매핑', 'dim') : null,
  );
  p.body.append(head);
  const note = STATUS_TEXT[d.status] ?? '';
  if (note) p.body.append(h('p', { class: 'dim' }, note));
  const qs = d.quarterly;
  if (qs.length) {
    p.body.append(
      lineChart({
        label: `${d.name} 수출 지수 YoY 대 분기 매출 YoY`,
        x: qs.map((q) => q.period),
        series: [
          {
            label: '수출 지수 YoY',
            values: qs.map((q) => (fin(q.yoy) ? q.yoy * 100 : null)),
          },
          {
            label: '매출 YoY',
            values: qs.map((q) => (fin(q.revenue_yoy) ? q.revenue_yoy * 100 : null)),
          },
        ],
        zero: true,
        format: (v) => pct(v, 1, true),
      }).el,
    );
  }
  const last = [...d.monthly].reverse().find((m) => fin(m.export_usd));
  p.body.append(
    kv([
      { label: '시차 상관', value: lagText(d.lag_corr) },
      {
        label: '최근 월 지수',
        value: last ? `${last.period} ${usd(last.export_usd)}(YoY ${ratio(last.yoy)})` : DASH,
      },
      { label: '매핑판', value: d.map_version },
    ]),
  );
  p.body.append(
    table<Line>({
      caption: `매핑 품목 ${d.lines.length}줄`,
      columns: [
        col('HS', (l) => l.hs, true),
        col('품목', (l) => l.name ?? DASH, true),
        col('흐름', (l) => (l.flow === 'exp' ? '수출' : '수입'), true),
        col('범위', lineLabel, true),
        col('가중치', (l) => num(l.weight, 2)),
      ],
      rows: d.lines,
      pageSize: 20,
    }).el,
  );
  p.body.append(
    h(
      'ul',
      { class: 'evidence' },
      d.evidence.map((e) => h('li', {}, `${e.note}${e.rcept_no ? ` (접수번호 ${e.rcept_no})` : ''}`)),
    ),
  );
  p.body.append(
    h(
      'button',
      {
        class: 'btn csv',
        attrs: { type: 'button' },
        on: {
          click: () => downloadCsv(`trade_overlay_${d.code}`, overlayCsv(d)),
        },
      },
      'CSV',
    ),
  );
  return 0;
}

export async function mount(t: TabCtx, el: HTMLElement): Promise<void> {
  const p = mkPanel(t, {
    title: TITLE,
    tier: 'public',
    span: 12,
    src: SOURCE_TEXT,
  });
  tabGrid(el, p);
  try {
    const env = await t.get<TradeStock>(stockKey(t.code));
    p.clearBody();
    const invalid = drawOverlay(p, env);
    p.setSource(env, invalid);
  } catch (err) {
    if (isNotFound(err)) p.showMessage(notMappedText(err));
    else p.showError(err);
  }
}
