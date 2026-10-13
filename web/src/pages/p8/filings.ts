// 페이지 8 '공시·지분' 탭의 공시 목록 + 잠정실적·실적 일정(docs/p4_design.md §4.1·§4.2·§6.2).
//
// - 로그인: 'stock/<코드>/disclosures'(30·90·365일, 종류 필터 — 중요도 = 키워드 + 관심종목 + 시총 가산),
//   'stock/<코드>/earnings'(잠정실적 + YoY 분류·실적 일정·알림 기록).
// - 공개: fin/<코드>.json 의 최근 공시(키워드 점수만)·실적 일정, earnings_prelim.json(최근 14일)의 이 종목 잠정실적
//   — 로그인 응답 모양으로 바꿔 같은 그리기 코드를 쓴다.
// - 일정의 법정 기한은 추정(분기·반기 45일, 사업 90일 [확인 필요]) — '추정' 배지.
// - 분기 컨센서스 대비 서프라이즈는 출처 없음(KIS 교체로 잃음 — D-P4-10).
import type { Envelope } from '../../data/source';
import { append, h } from '../../ui/dom';
import { kstDate, kstTime, pct } from '../../ui/fmt';
import type { Panel } from '../../ui/panel';
import { seg, select } from '../../ui/seg';
import { table } from '../../ui/table';
import { fill, filters } from '../p1_market';
import {
  arr,
  col,
  discColumns,
  type DiscRow,
  errLine,
  HINT,
  KIND_OPTIONS,
  kindLabel,
  latestPrelims,
  mkPanel,
  noPubFin,
  prelimColumns,
  pubDiscRows,
  pubPrelims,
  type S,
  type ScheduleLike,
  scheduleColumns,
  signalCell,
  supersededNote,
  tabGrid,
  type TabCtx,
} from './common';

type Sp = S['SurpriseView'];
const SURPRISE_COLS = [
  col<Sp>('기간', (r) => r.period, true),
  col<Sp>('분류', (r) => signalCell(r.signal, r.priority), true),
  col<Sp>('알림', (r) => (r.alerted_at ? `${kstDate(r.alerted_at)} ${kstTime(r.alerted_at)}` : '안 보냄')),
  col<Sp>('3거래일', (r) => pct(r.perf_3d, 1)),
  col<Sp>('5거래일', (r) => pct(r.perf_5d, 1)),
  col<Sp>('7거래일', (r) => pct(r.perf_7d, 1)),
];

interface Earn {
  prelims: S['PrelimView'][];
  schedule: readonly ScheduleLike[];
  surprises: Sp[];
  /** 공개: 잠정실적 파일을 못 읽은 이유(그 표 자리에 한 줄) */
  prelimErr?: string;
}

function drawEarnings(p: Panel, d: Earn, login: boolean): void {
  // 정정공시는 원 공시를 대체 — 같은 기간·연결/별도는 가장 늦은 접수번호만(§4.1)
  const pre = latestPrelims(d.prelims);
  append(p.body, [
    d.prelimErr
      ? h('p', { class: 'msg' }, `잠정실적: ${d.prelimErr}`)
      : table({
          caption: `${login ? '잠정실적' : '잠정실적(최근 14일)'}${supersededNote(pre.superseded)}`,
          columns: prelimColumns(login),
          rows: pre.rows,
          pageSize: 10,
          empty: '잠정실적 공시 없음',
        }).el,
    table({ caption: '실적 일정', columns: scheduleColumns(), rows: d.schedule, empty: '예정된 실적 공시 없음' }).el,
    d.surprises.length ? table({ caption: '분류·알림 기록(알림 뒤 3·5·7거래일 수익률)', columns: SURPRISE_COLS, rows: d.surprises }).el : null,
  ]);
}

export async function mount(t: TabCtx, el: HTMLElement): Promise<void> {
  const pDisc = mkPanel(t, { title: '공시 목록', tier: 'public', span: 12 });
  const pEarn = mkPanel(t, { title: '잠정실적·실적 일정', tier: 'public', span: 12 });
  tabGrid(el, pDisc, pEarn);
  pEarn.setDef('분류 = 전년동기 대비(YoY). 분기 컨센서스 대비 서프라이즈는 출처 없음(KIS 교체로 잃음). 법정 기한은 추정.');
  const login = t.tier === 'login';
  const code = t.code;
  const q = { days: '30', kind: '' };
  const hint = login ? HINT.feed : HINT.public;

  const loadDisc = (): Promise<void> =>
    fill(
      pDisc,
      async (): Promise<Envelope<DiscRow[]>> => {
        if (login) {
          const env = await t.get<S['Disclosures']>(`stock/${code}/disclosures`, { days: Number(q.days), kind: q.kind || null });
          return { ...env, data: env.data.rows };
        }
        const env = await t.pub();
        if (!env) throw noPubFin();
        return { ...env, notes: ['중요도는 제목 키워드 점수만(로그인 화면은 관심종목·시총 가산)'], data: pubDiscRows(env.data) };
      },
      (env) => {
        pDisc.body.append(
          table({
            caption: login ? `최근 ${q.days}일${q.kind ? ` · ${kindLabel(q.kind)}` : ''} · ${env.data.length}건` : '최근 공시(최대 20건)',
            columns: discColumns(login),
            rows: env.data,
            empty: '공시 없음',
          }).el,
        );
        return 0;
      },
      hint,
    );
  if (login) {
    const set = (k: 'days' | 'kind') => (v: string): void => {
      q[k] = v;
      void loadDisc();
    };
    filters(
      pDisc,
      seg({
        label: '공시 기간',
        options: ['30', '90', '365'].map((d) => ({ value: d, label: `${d}일` })),
        value: q.days,
        onChange: set('days'),
      }).el,
      select({ label: '종류', options: [{ value: '', label: '전체' }, ...KIND_OPTIONS.map((k) => ({ value: k, label: kindLabel(k) }))], value: '', onChange: set('kind') }).el,
    );
  }
  await Promise.all([
    loadDisc(),
    fill(
      pEarn,
      async (): Promise<Envelope<Earn>> => {
        if (login) return t.get<S['Earnings']>(`stock/${code}/earnings`);
        const env = await t.pub();
        if (!env) throw noPubFin();
        const data: Earn = { prelims: [], schedule: arr(env.data.schedule), surprises: [] };
        try {
          data.prelims = await pubPrelims(t);
        } catch (err) {
          data.prelimErr = errLine(err, HINT.public);
        }
        return { ...env, notes: [], data };
      },
      (env) => {
        drawEarnings(pEarn, env.data, login);
        return 0;
      },
      login ? HINT.earnings : HINT.public,
    ),
  ]);
}
