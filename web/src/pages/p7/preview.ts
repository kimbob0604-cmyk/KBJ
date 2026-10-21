// 페이지 7 '종목' 탭 — 실적 프리뷰 카드(P10 단계 3 — docs/p10_trade_stocks_plan.md, 나쁜양파 'Earning Preview' 같은 판정).
// `/api/trade/stock/<코드>/preview`: 분기 수출(원 환산) 회귀로 추정한 분기 매출 vs 컨센서스(연간 − 공시 분기의 남은 분기 평균).
// 판정 BEAT·IN LINE·MISS(±band), 신뢰도(R²·점 수), 분기 매출 vs 회귀 선. 규칙 문장 — 매매 권유가 아니다. 로그인 전용.
import type { components } from '../../api/types.gen';
import { h } from '../../ui/dom';
import { eok, pct, signClass } from '../../ui/fmt';
import { kv } from '../../ui/kv';
import { lineChart } from '../../ui/line';

type S = components['schemas'];
export type TradePreview = S['TradePreview'];

const VERDICT_CLS: Readonly<Record<string, string>> = {
  BEAT: 'up',
  MISS: 'down',
  'IN LINE': 'dim',
};

/** 판정 글자 — 공시됐으면 사후 대조, 컨센서스 없으면 판정 없음 */
export function verdictText(d: TradePreview): string {
  if (d.reported) return '공시됨(사후 대조)';
  if (d.verdict === null) return d.revenue_est === null ? '추정 없음' : '판정 없음';
  const was = d.prev_verdict !== null && d.prev_verdict !== d.verdict ? ` (지난달 ${d.prev_verdict})` : '';
  return `${d.verdict}${was}`;
}

export function previewCard(d: TradePreview, notes: readonly string[]): HTMLElement {
  const est = d.months_seen < 3 ? ` · ${String(d.months_seen)}개월 반영(EST)` : '';
  const rows: [string, string][] = [
    ['대상 분기', `${d.quarter}${est}`],
    ['추정 매출', eok(d.revenue_est)],
    ['컨센서스', d.consensus === null ? '없음' : eok(d.consensus)],
    ['차이', d.diff === null ? '—' : pct(d.diff * 100, 1)],
    ['신뢰도', `${d.confidence} · R² ${d.r2 === null ? '—' : d.r2.toFixed(2)} · ${String(d.points)}개 분기`],
  ];
  const hist = d.history.filter((q) => q.revenue !== null || q.fitted !== null);
  return h(
    'section',
    { class: 'flow-block preview', attrs: { 'aria-label': '실적 프리뷰' } },
    h(
      'h5',
      null,
      `실적 프리뷰 — ${d.quarter}`,
      h('span', { class: `tag ${d.verdict ? (VERDICT_CLS[d.verdict] ?? 'dim') : 'dim'}` }, verdictText(d)),
    ),
    kv(rows),
    d.diff === null
      ? null
      : h('p', { class: signClass(d.diff) }, `판정 문턱 ±${(d.band * 100).toFixed(0)}% — 추정 ÷ 컨센서스 − 1`),
    hist.length
      ? lineChart({
          label: '분기 매출 vs 회귀 추정(억원)',
          x: hist.map((q) => q.quarter.slice(2)),
          series: [
            { label: '공시 매출', values: hist.map((q) => (q.revenue === null ? null : q.revenue / 1e8)) },
            { label: '회귀 추정', values: hist.map((q) => (q.fitted === null ? null : q.fitted / 1e8)) },
          ],
          format: (v) => `${Math.round(v).toLocaleString('ko-KR')}억`,
          h: 160,
        }).el
      : null,
    ...notes.map((n) => h('p', { class: 'msg' }, n)),
  );
}
