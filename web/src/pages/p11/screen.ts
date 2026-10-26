// 페이지 11 스크리너 — cis 순추세(전 거래일 종가 기준 거래량 급증 + 박스권 돌파, config/screen.yaml).
//
// - 데이터: GET screen/cis(로그인 — 시세 저장소 전 종목 일봉으로 그때 계산). 행을 누르면 페이지 8 종목 상세.
// - '통과'(박스 위·양봉·윗꼬리 짧음·상한가 아님·시장 강세)가 먼저, 그 안은 거래량 배수 큰 순. '거래량 급증 전체'는
//   통과하지 못한 까닭(why_not)을 함께 보인다.
// - 손절가·건너뜀 가격은 종가 기준 참고값(다음 날 실제 진입가로 다시 잰다). 매매 권유가 아니다 — 봉투 notes 그대로.
// - 공개 빌드는 이 모듈을 번들에서 뺀다(진입 파일 LOGIN_FILLS) — 모듈 최상위에 부수 효과를 두지 않는다.
import type { components } from '../../api/types.gen';
import { openStock } from '../../app/route';
import type { PageContext } from '../../app/types';
import type { Envelope } from '../../data/source';
import { h } from '../../ui/dom';
import { eok, num, pct, signClass, times } from '../../ui/fmt';
import type { Panel } from '../../ui/panel';
import { seg } from '../../ui/seg';
import { type Column, table } from '../../ui/table';
import { failPanel } from './perf';

export type ScreenCis = components['schemas']['ScreenCis'];
export type CisRow = components['schemas']['CisRowOut'];
type CisParams = components['schemas']['CisParams'];
type CisRegime = components['schemas']['CisRegime'];

export const SCREEN_HINT = '아직 없음 — krx.daily 08:05 · market.close_collect 15:35(일봉)';
export const PASS_TEXT = '통과';
const MARKET_KO: Readonly<Record<string, string>> = { KOSPI: '코스피', KOSDAQ: '코스닥' };

type View = 'pass' | 'all';

/** 시장 국면 한 줄 — '코스피 강세 · 코스닥 약세'(지수 봉이 모자라면 '모름') */
export function regimeText(regimes: readonly CisRegime[]): string {
  return regimes
    .map((g) => `${MARKET_KO[g.market] ?? g.market} ${g.on === null ? '모름' : g.on ? '강세' : '약세'}`)
    .join(' · ');
}

/** 판정 문턱 한 줄 */
export function paramsText(p: CisParams): string {
  return [
    `거래량 ≥ ${String(p.lookback_days)}일 평균×${String(p.vol_mult)}`,
    `거래대금 ≥ ${eok(p.min_turnover_won)}`,
    `${String(p.lookback_days)}일 박스 돌파`,
    `손절 −${String(p.stop_pct)}% · 추적 −${String(p.trail_pct)}% · 갭 +${String(p.gap_skip_pct)}% 넘으면 건너뜀`,
  ].join(' · ');
}

export function viewRows(data: ScreenCis, view: View): CisRow[] {
  return view === 'pass' ? data.rows.filter((r) => r.passed) : [...data.rows];
}

const COLUMNS: readonly Column<CisRow>[] = [
  {
    key: 'name',
    label: '종목',
    align: 'l',
    cell: (r) => h('span', null, r.name ?? r.code, h('small', { class: 'dim' }, ` ${r.code}`)),
  },
  { key: 'mkt', label: '시장', align: 'l', cell: (r) => MARKET_KO[r.market] ?? r.market },
  { key: 'close', label: '종가', cell: (r) => num(r.close) },
  { key: 'chg', label: '등락', cell: (r) => pct(r.chg_pct, 2), cls: (r) => signClass(r.chg_pct) },
  { key: 'ratio', label: '거래량 배수', cell: (r) => times(r.vol_ratio, 1) },
  { key: 'to', label: '거래대금', cell: (r) => eok(r.turnover) },
  { key: 'box', label: '박스 위', cell: (r) => num(r.box_high) },
  { key: 'pos', label: '마감 위치', cell: (r) => pct(r.close_pos * 100, 0, false) },
  { key: 'stop', label: '손절가', cell: (r) => num(r.stop) },
  {
    key: 'why',
    label: '판정',
    align: 'l',
    cell: (r) => (r.passed ? PASS_TEXT : r.why_not.join('·')),
    cls: (r) => (r.passed ? 'up' : 'dim'),
  },
];

export async function fillScreen(ctx: PageContext, p: Panel): Promise<void> {
  let env: Envelope<ScreenCis>;
  try {
    env = await ctx.source.get<ScreenCis>('screen/cis');
  } catch (err) {
    failPanel(p, err, SCREEN_HINT);
    return;
  }
  p.setSource(env);
  const d = env.data;
  p.clearBody();
  const summary = h(
    'p',
    { class: 'hint' },
    `${d.date} 종가 · 대상 ${num(d.scanned)} · 거래량 급증 ${num(d.spikes)} · ${PASS_TEXT} ${num(d.passed)} · 시장 ${regimeText(d.regimes)}`,
  );
  const rules = h('p', { class: 'hint' }, paramsText(d.params));
  const holder = h('div');
  const draw = (view: View): void => {
    const rows = viewRows(d, view);
    holder.replaceChildren(
      table<CisRow>({
        caption: view === 'pass' ? `${PASS_TEXT} 종목 — 다음 거래일 계획(참고)` : '거래량 급증 전체 — 통과 못 한 까닭 포함',
        columns: COLUMNS,
        rows,
        rowKey: (r) => r.code,
        onSelect: (r) => {
          openStock(r.code);
        },
        empty: view === 'pass' ? `${PASS_TEXT} 종목 없음 — '거래량 급증 전체'에서 후보를 보세요` : '거래량 급증 종목 없음',
      }).el,
    );
  };
  const pick = seg<View>({
    label: '보기',
    options: [
      { value: 'pass', label: PASS_TEXT },
      { value: 'all', label: '거래량 급증 전체' },
    ],
    value: d.passed > 0 ? 'pass' : 'all',
    onChange: draw,
  });
  draw(pick.value());
  p.body.append(summary, rules, h('div', { class: 'filters' }, pick.el), holder);
}
