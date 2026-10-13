// 페이지 8 '컨센·밸류' 탭의 시나리오 적정가 패널(로그인 — 구 이름 대신 '시나리오 적정가', D-P4-12, docs/p4_design.md §4.9).
//
// 적정가 = EPS × 배수(Bear·Base·Bull) — 시장 규모 추정이 아니다. EPS 는 KIS 추정(없으면 TTM), 배수는 5년 밴드
// P25·P50·P75(피어 평균은 P5 분류 사전 뒤). 컨센서스 최고값 출처가 없어 Bull EPS = Base × 1.2(legacy 폴백 그대로).
// EPS 가 없거나(적자) PER 밴드가 없으면 BPS × PBR 밴드(metrics §15). 우선주는 회사(보통주) 기준 — 화면에 '보통주 기준'으로 적는다(V·A 요청).
import type { Envelope } from '../../data/source';
import { h } from '../../ui/dom';
import { DASH, kstDate, num, pct, signClass } from '../../ui/fmt';
import type { Panel } from '../../ui/panel';
import { EST_LABEL, qValue } from '../../ui/quality';
import { table } from '../../ui/table';
import { fill } from '../p1_market';
import { col, HINT, mkPanel, naText, type S, tabGrid, type TabCtx, tag, won } from './common';

type Sc = S['Scenario'];
type Case = S['ScenarioCase'];

const CASE_LABEL: Record<Case['case'], string> = { bear: 'Bear', base: 'Base', bull: 'Bull' };

/** 근거 코드 → 글자(CONSENSUS_×0.7 → 'KIS 추정 × 0.7', 5Y_P25 → '5년 밴드 P25') */
export function sourceText(code: string | null | undefined): string {
  if (!code) return DASH;
  const map: readonly [RegExp, string][] = [
    [/CONSENSUS_NI_PER_SHARE/g, 'KIS 추정 순익 ÷ 주식 수'],
    [/TTM_NI_PER_SHARE/g, 'TTM 지배순익 ÷ 주식 수'],
    [/CONSENSUS/g, 'KIS 추정 EPS'],
    [/TTM_EPS/g, 'TTM EPS'],
    [/BASE/g, 'Base'],
    [/PBR_5Y_/g, 'PBR 5년 밴드 '],
    [/5Y_/g, '5년 밴드 '],
    [/_×/g, ' × '],
  ];
  let out = code;
  for (const [re, to] of map) out = out.replace(re, to);
  return out;
}

const COLS = [
  col<Case>('시나리오', (r) => h('b', null, CASE_LABEL[r.case]), true),
  col<Case>('EPS·BPS', (r) => `${r.method === 'PBR' ? 'BPS ' : ''}${won(r.eps)}`),
  col<Case>('근거', (r) => sourceText(r.eps_source), true),
  col<Case>('배수', (r) => (r.multiple === null ? null : `${num(r.multiple, 2)}x`)),
  col<Case>('배수 근거', (r) => sourceText(r.multiple_source), true),
  col<Case>('적정가', (r) => (r.tp === null ? (r.na_reason ? naText(r.na_reason) : null) : qValue(won(r.tp), r.quality, null, undefined, EST_LABEL))),
  col<Case>('업사이드', (r) => pct(r.upside_pct, 1), false, (r) => signClass(r.upside_pct)),
];

export function drawScenario(p: Panel, env: Envelope<Sc>, isPref: boolean): void {
  const d = env.data;
  if (!d.applicable) {
    p.body.append(h('p', { class: 'msg' }, `적정가 산출 안 함 — ${naText(d.na_reason)}`));
    return;
  }
  const cases = [d.cases.bear, d.cases.base, d.cases.bull].filter((c): c is Case => c !== null);
  p.body.append(
    h(
      'p',
      { class: 'dim' },
      `방식 ${d.method === 'PBR' ? 'BPS × PBR(EPS 또는 PER 밴드가 없어 — 적자·스팩 등)' : d.method === 'PER' ? 'EPS × PER' : '자료 부족'} · 기준 종가 ${won(d.price)}${d.as_of ? ` (${kstDate(d.as_of)})` : ''}`,
      isPref ? tag('보통주 기준', 'warn') : null,
    ),
    table({ caption: '시나리오 적정가(Bear·Base·Bull)', columns: COLS, rows: cases, empty: '시나리오 없음' }).el,
  );
}

export async function mount(t: TabCtx, el: HTMLElement): Promise<void> {
  const p = mkPanel(t, { title: '시나리오 적정가', tier: 'login', span: 12, lock: 'valuation' });
  tabGrid(el, p);
  if (p.locked) return;
  p.setDef('적정가 = EPS × 배수(5년 밴드 P25·P50·P75). 컨센서스 최고값 출처가 없어 Bull EPS = Base × 1.2. 시장 규모 추정이 아니다.');
  // 우선주 표시는 머리 응답(같은 요청 — 탭끼리 나눠 쓴다)에서. 머리를 못 받으면 표시만 빠진다
  const head = t.get<S['StockHeader']>(`stock/${t.code}/header`).then(
    (e) => e.data.kind === 'pref',
    () => false,
  );
  await fill(
    p,
    async () => {
      const [env, isPref] = await Promise.all([t.get<Sc>(`stock/${t.code}/scenario`), head]);
      return { ...env, data: { env, isPref } };
    },
    (x) => {
      drawScenario(p, x.data.env, x.data.isPref);
      return 0;
    },
    HINT.valuation,
  );
}
