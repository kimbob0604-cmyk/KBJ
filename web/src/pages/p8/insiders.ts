// 페이지 8 '공시·지분' 탭의 내부자 패널(임원·주요주주 소유보고, 5% 대량보유 — docs/p4_design.md §4.4·§6.2, metrics §13).
//
// - 창 합계(30·90·180일)는 **보고 기준 순증감(사유 미구분 포함)** — 증여·상속·신규선임 등 비거래 증감이 섞인다
//   (장내 순매수가 아니다). 정정은 늦은 접수번호만.
// - 로그인 칸: 금액 = Σ 증감 × 보고일 종가(추정 — 실거래가 아님).
// - 5% 대량보유는 목록만(임원 보고와 같은 지분을 두 번 세지 않게 합계를 내지 않는다).
// - 공개: 창의 주식 수·비율만(보고자 이름·금액은 공개 파일에 없다) — 로그인 응답 모양으로 바꿔 같은 코드로 그린다.
import type { Envelope } from '../../data/source';
import { append, h } from '../../ui/dom';
import { joEok, kstDate, num, pct, signClass } from '../../ui/fmt';
import type { Panel } from '../../ui/panel';
import { EST_LABEL, qValue } from '../../ui/quality';
import { seg } from '../../ui/seg';
import { type Column, table } from '../../ui/table';
import { fill, filters } from '../p1_market';
import { col, dartLink, HINT, mkPanel, noPubFin, type S, shares, tabGrid, type TabCtx, tag } from './common';

type I = S['Insiders'];
type W = S['WindowView'];
type Days = '30' | '90' | '180';

export function windowColumns(login: boolean): Column<W>[] {
  const cols = [
    col<W>('창', (r) => `최근 ${r.days}일`, true),
    col<W>('순증감', (r) => qValue(shares(r.net_shares, true), r.quality, null, undefined, EST_LABEL), false, (r) => signClass(r.net_shares)),
    col<W>('증가', (r) => shares(r.buy_shares)),
    col<W>('감소', (r) => shares(r.sell_shares)),
    col<W>('비율', (r) => pct(r.ratio_pct, 2), false, (r) => signClass(r.ratio_pct)),
    col<W>('보고·보고자', (r) => `${num(r.n_reports)}건 · ${num(r.n_reporters)}명`),
    col<W>('사유 확인', (r) => pct(r.reason_known_pct, 0, false)),
  ];
  if (login) cols.push(col<W>('금액(추정)', (r) => `${joEok(r.amount, true)}${r.amount_missing ? `(종가 없음 ${r.amount_missing}건 제외)` : ''}`, false, (r) => signClass(r.amount)));
  return cols;
}

type R = S['InsiderReportView'];
const REPORT_COLS = [
  col<R>('접수일', (r) => kstDate(r.rcept_dt)),
  col<R>('보고자', (r) => h('span', null, dartLink(r.link, r.reporter), r.position ? h('small', null, r.position) : null, r.major_holder ? tag('주요주주', 'dim') : null), true),
  col<R>('증감', (r) => shares(r.shares_delta, true), false, (r) => signClass(r.shares_delta)),
  col<R>('보유 후', (r) => shares(r.shares_after)),
  col<R>('지분율 후', (r) => pct(r.ratio_after, 2, false)),
  col<R>('사유', (r) => r.reason ?? '미구분', true),
];

type M = S['MajorHolderView'];
const MAJOR_COLS = [
  col<M>('접수일', (r) => kstDate(r.rcept_dt)),
  col<M>('보고자', (r) => h('span', null, dartLink(r.link, r.reporter), r.report_type ? h('small', null, r.report_type) : null), true),
  col<M>('보유 주식', (r) => shares(r.shares)),
  col<M>('증감', (r) => shares(r.shares_delta, true), false, (r) => signClass(r.shares_delta)),
  col<M>('보유 비율', (r) => pct(r.ratio, 2, false)),
  col<M>('비율 증감', (r) => pct(r.ratio_delta, 2), false, (r) => signClass(r.ratio_delta)),
  col<M>('사유', (r) => r.reason, true),
];

export function drawInsiders(p: Panel, d: I, login: boolean): void {
  append(p.body, [
    h('p', { class: 'dim' }, d.label),
    table<W>({ caption: '창 합계', columns: windowColumns(login), rows: d.windows, empty: '최근 소유보고 없음' }).el,
    login
      ? [
          table({ caption: `소유보고(최근 ${d.days}일, 최대 50건)`, columns: REPORT_COLS, rows: d.reports, pageSize: 10, empty: '이 기간 소유보고 없음' }).el,
          table({ caption: '5% 대량보유(목록만 — 합계 없음)', columns: MAJOR_COLS, rows: d.major_holders, pageSize: 10, empty: '5% 대량보유 보고 없음' }).el,
        ]
      : h('p', { class: 'def' }, '보고자 목록·금액은 로그인 화면'),
  ]);
}

export async function mount(t: TabCtx, el: HTMLElement): Promise<void> {
  const p = mkPanel(t, { title: '내부자', tier: 'public', span: 12 });
  tabGrid(el, p);
  p.setDef('보고 기준 순증감(사유 미구분 포함) — 장내 순매수가 아니다. 비율 = 순증감 ÷ DART 주식총수.');
  const login = t.tier === 'login';
  let days: Days = '90';
  const load = (): Promise<void> =>
    fill(
      p,
      async (): Promise<Envelope<I>> => {
        if (login) return t.get<I>(`stock/${t.code}/insiders`, { days: Number(days) });
        const env = await t.pub();
        if (!env) throw noPubFin();
        const ins = env.data.insider;
        const windows = (ins?.windows ?? []).map((w) => ({ ...w, as_of: ins?.as_of ?? '', amount: null, amount_missing: 0 }) as W);
        const data: I = { code: t.code, corp_code: env.data.corp_code, days: 90, label: ins?.label ?? '보고 기준 순증감', windows, reports: [], major_holders: [] };
        return { ...env, notes: [], data };
      },
      (env) => {
        drawInsiders(p, env.data, login);
        return 0;
      },
      login ? HINT.derive : HINT.public,
    );
  if (login) {
    filters(
      p,
      seg<Days>({
        label: '보고 목록 기간',
        options: [
          { value: '30', label: '30일' },
          { value: '90', label: '90일' },
          { value: '180', label: '180일' },
        ],
        value: days,
        onChange: (v) => {
          days = v;
          void load();
        },
      }).el,
    );
  }
  await load();
}
