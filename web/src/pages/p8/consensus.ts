// 페이지 8 '컨센·밸류' 탭의 컨센서스 패널(로그인 — KIS, docs/p4_design.md §4.7·§6.2).
//
// - 표: 연간 추정(FY)·NTM(FY1·FY2 가중), 목표가(증권사별 의견에서 우리가 계산 — 평균·중앙·범위·증권사 수), 의견 분포,
//   리비전(7·30일 — 신호 색), 최근 의견 20건.
// - 'KIS 추정'이 시장 컨센서스인지 [확인 필요 #29] — 확인 전에는 서버가 준 이름표(label) 그대로 쓴다.
// - KIS 교체로 잃은 것(분기 컨센서스·분기 서프라이즈·최고/최저)은 늘 적는다(D-P4-10) — 패널 정의 줄.
// - 수집이 꺼져 있는 동안(#29 전)은 404 no_data — 서버 문구 그대로.
// - 공개판: 자물쇠('컨센서스 — 제3자 유료·재배포 금지'), 요청 없음.
import type { Envelope } from '../../data/source';
import { h } from '../../ui/dom';
import { DASH, kstDate, num, pct, signClass } from '../../ui/fmt';
import { kv } from '../../ui/kv';
import type { Panel } from '../../ui/panel';
import { EST_LABEL, qValue } from '../../ui/quality';
import { level } from '../../ui/svg';
import { table } from '../../ui/table';
import { fill } from '../p1_market';
import {
  col,
  HINT,
  lostLine,
  METRIC_LABEL,
  mkPanel,
  money,
  naText,
  revisionCell,
  type S,
  tabGrid,
  type TabCtx,
  won,
} from './common';

type C = S['Consensus'];

/** '202612' → '2026.12' */
export function ymLabel(ym: string | null | undefined): string {
  return ym && /^\d{6}$/.test(ym) ? `${ym.slice(0, 4)}.${ym.slice(4)}` : (ym ?? DASH);
}

type Fy = S['FyEstimateView'];
type Rv = S['RevisionView'];
type Op = S['OpinionView'];
const perShare = (m: string): boolean => m === 'eps' || m === 'target_mean';

export const FY_COLS = [
  col<Fy>('회계연도', (r) => ymLabel(r.fiscal_ym), true),
  col<Fy>('구분', (r) => (r.is_estimate ? h('span', { class: 'warn' }, '추정') : '실적'), true),
  col<Fy>('매출', (r) => qValue(money(r.revenue), r.quality, null, undefined, EST_LABEL)),
  col<Fy>('영업이익', (r) => money(r.op), false, (r) => signClass(r.op)),
  col<Fy>('지배순익', (r) => money(r.ni_owner ?? r.ni), false, (r) => signClass(r.ni_owner ?? r.ni)),
  col<Fy>('EPS', (r) => won(r.eps), false, (r) => signClass(r.eps)),
];

const REV_COLS = [
  col<Rv>('항목', (r) => `${METRIC_LABEL[r.metric] ?? r.metric}${r.metric === 'target_mean' ? '' : ` ${ymLabel(r.fiscal_ym)}`}`, true),
  col<Rv>('창', (r) => `${r.window_days}일`),
  col<Rv>('현재', (r) => (perShare(r.metric) ? won(r.current) : money(r.current))),
  col<Rv>('기준', (r) => `${perShare(r.metric) ? won(r.baseline) : money(r.baseline)}${r.baseline_date ? ` (${kstDate(r.baseline_date)})` : ''}`),
  col<Rv>('변화', (r) => pct(r.pct, 1), false, (r) => signClass(r.pct)),
  col<Rv>('신호', (r) => revisionCell(r.signal), true),
];

const OPINION_COLS = [
  col<Op>('증권사', (r) => r.broker, true),
  col<Op>('날짜', (r) => kstDate(r.opinion_date)),
  col<Op>('의견', (r) => r.opinion, true),
  col<Op>('직전', (r) => r.opinion_prev, true),
  col<Op>('목표가', (r) => won(r.target_price)),
];

export function drawConsensus(p: Panel, env: Envelope<C>, asked: string): void {
  const d = env.data;
  p.setDef(lostLine(d.lost));
  if (!d.applicable) {
    p.body.append(h('p', { class: 'msg' }, naText(d.na_reason)));
    return;
  }
  p.body.append(
    h(
      'p',
      { class: 'dim' },
      `${d.label} · 스냅 ${d.snap_date ? kstDate(d.snap_date) : DASH}`,
      d.code !== asked ? ` · 우선주 — 보통주(${d.code}) 컨센서스` : '',
    ),
    table({ caption: '연간 실적·추정', columns: FY_COLS, rows: d.fy, empty: '연간 추정 없음' }).el,
  );
  const n = d.ntm;
  p.body.append(
    n && n.weight !== null
      ? kv([
          { label: 'NTM EPS', value: won(n.eps), quality: n.quality },
          { label: 'NTM 순이익', value: money(n.ni), quality: n.quality },
          { label: 'NTM 영업이익', value: money(n.op), quality: n.quality },
          { label: 'FY1 가중', value: pct(n.weight * 100, 0, false), hint: `${ymLabel(n.fy1_ym)} · ${ymLabel(n.fy2_ym)}` },
        ])
      : h('p', { class: 'dim' }, 'NTM 없음 — FY2 추정이 없어 FY1 만으로 만들지 않는다'),
  );
  const tg = d.target;
  p.body.append(
    tg
      ? kv([
          { label: '목표가 평균', value: won(tg.mean), quality: tg.quality },
          { label: '중앙', value: won(tg.median) },
          { label: '범위', value: `${won(tg.low)} ~ ${won(tg.high)}` },
          { label: '증권사 수', value: `${num(tg.n_brokers)}곳`, hint: `최근 ${tg.window_days}일` },
          { label: '업사이드', value: pct(tg.upside_pct, 1), cls: signClass(tg.upside_pct), hint: '평균 ÷ 전 거래일 종가 − 1' },
        ])
      : h('p', { class: 'dim' }, '목표가 없음 — 최근 의견에 목표가가 없다'),
  );
  const o = d.opinions;
  if (o && o.n_brokers > 0) {
    const share = (x: number): number => (x / o.n_brokers) * 100;
    p.body.append(
      h(
        'div',
        { class: 'levels', data: { section: 'opinions' } },
        level('매수', share(o.buy), `${num(o.buy)}곳`, 'up'),
        level('중립', share(o.hold), `${num(o.hold)}곳`),
        level('매도', share(o.sell), `${num(o.sell)}곳`, 'down'),
        o.unrated ? level('의견 없음', share(o.unrated), `${num(o.unrated)}곳`, 'dim') : null,
        o.unknown ? level('모름', share(o.unknown), `${num(o.unknown)}곳`, 'dim') : null,
      ),
      h('p', { class: 'def' }, `의견 분포 — 최근 ${o.window_days}일 증권사별 마지막 의견`),
    );
  }
  p.body.append(
    table({ caption: '리비전(7·30일)', columns: REV_COLS, rows: d.revisions, pageSize: 12, empty: '리비전 없음 — 기준 스냅이 아직 없다' }).el,
    table({ caption: '최근 의견', columns: OPINION_COLS, rows: d.recent.slice(0, 20), pageSize: 10, empty: '최근 의견 없음' }).el,
  );
}

export async function mount(t: TabCtx, el: HTMLElement): Promise<void> {
  const p = mkPanel(t, { title: '컨센서스', tier: 'login', span: 12, lock: 'consensus' });
  tabGrid(el, p);
  if (p.locked) return;
  p.setDef(lostLine());
  await fill(
    p,
    () => t.get<C>(`stock/${t.code}/consensus`),
    (env) => {
      drawConsensus(p, env, t.code);
      return 0;
    },
    HINT.consensus,
  );
}
