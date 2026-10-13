// 페이지 8 '공시·지분' 탭의 오버행 패널(CB·BW·EB·유상증자 — docs/p4_design.md §4.3·§6.2, metrics §12).
//
// - 공개 칸: 잠재 신주·행사 가능 주식·비율(분모 = DART 주식총수 — 보통주 발행), 청구기간 타임라인.
// - 로그인 칸: 상장주식수 분모 비율·금액(Σ 잠재 주식 × 전 거래일 종가 — 추정, 시가 아님)·ITM 주식.
// - 보호예수: 출처 없음 → 법정 전매제한으로 계산한 **추정 해제일만**('추정' 배지 + 근거 문구, D-P4-18).
// - 오버행 가치는 EV 에 더하지 않는다(D-P4-11).
// - 공개 파일(요약 칸만)은 로그인 응답 모양으로 바꿔 같은 그리기 코드를 쓴다(로그인 칸은 빈 칸으로 그리지 않는다).
import type { Envelope, Quality } from '../../data/source';
import { append, h } from '../../ui/dom';
import { joEok, kstDate, num, pct } from '../../ui/fmt';
import { kv, type KvItem } from '../../ui/kv';
import type { Panel } from '../../ui/panel';
import { EST_LABEL, estBadge, qValue } from '../../ui/quality';
import { table } from '../../ui/table';
import { timeline, type TimelineItem } from '../../ui/timeline';
import { fill } from '../p1_market';
import { col, dartLink, HINT, instrumentLabel, mkPanel, noPubFin, type S, shares, tabGrid, type TabCtx, tag, won } from './common';

type O = S['Overhang'];
type Inst = S['InstrumentView'];

/** 요약(공개 칸 — 로그인이면 로그인 칸도) */
interface Summary {
  potential_new_shares: number | null;
  exercisable_shares: number | null;
  exchange_shares: number | null;
  denominator: number | null;
  ratio_pct: number | null;
  exercisable_ratio_pct: number | null;
  quality: Quality;
  login_ratio_pct?: number | null;
  amount?: number | null;
  itm_shares?: number | null;
}

/** 요약 칸(공개 칸 + 로그인이면 로그인 칸) */
export function summaryItems(s: Summary, login: boolean): KvItem[] {
  const items: KvItem[] = [
    { label: '잠재 신주', value: shares(s.potential_new_shares), quality: s.quality },
    { label: '행사 가능', value: shares(s.exercisable_shares), hint: '청구기간 안' },
    { label: '오버행 비율', value: pct(s.ratio_pct, 2, false), quality: s.quality, hint: '잠재 신주 ÷ DART 주식총수' },
    { label: '행사 가능 비율', value: pct(s.exercisable_ratio_pct, 2, false) },
    { label: '분모(주식총수)', value: shares(s.denominator) },
  ];
  if (s.exchange_shares) items.push({ label: 'EB 교환 대상', value: shares(s.exchange_shares), hint: '신주 아님' });
  if (login) {
    items.push(
      { label: '상장주식수 기준', value: pct(s.login_ratio_pct, 2, false) },
      { label: '금액', value: joEok(s.amount), quality: 'estimated', est: EST_LABEL, hint: '× 전 거래일 종가' },
      { label: 'ITM 주식', value: shares(s.itm_shares), hint: '종가 ≥ 전환·행사가' },
    );
  }
  return items;
}

const INST_COLS = [
  col<Inst>('종류', (r) => h('span', null, dartLink(r.link, `${instrumentLabel(r.instrument)}${r.seq ? ` ${r.seq}회` : ''}`), r.active ? null : tag('소멸', 'dim')), true),
  col<Inst>('결정일', (r) => kstDate(r.rcept_dt)),
  col<Inst>('잔여 권면', (r) => joEok(r.remaining_face ?? r.face_value)),
  col<Inst>('전환·행사가', (r) => `${won(r.price)}${r.refix_floor === null ? '' : ` (하한 ${won(r.refix_floor)})`}`),
  col<Inst>('잠재 주식', (r) => qValue(shares(r.potential_shares), r.quality, null, undefined, EST_LABEL)),
  col<Inst>('청구기간', (r) => `${kstDate(r.exercise_start)} ~ ${kstDate(r.exercise_end)}`, false, (r) => (r.exercisable ? 'warn' : undefined)),
  col<Inst>('보호예수 해제', (r) => (r.lockup_end_est ? qValue(kstDate(r.lockup_end_est), 'estimated', null, undefined, EST_LABEL) : null)),
  col<Inst>('ITM', (r) => (r.itm === null ? null : r.itm ? 'ITM' : 'OTM')),
  col<Inst>('금액(추정)', (r) => joEok(r.amount)),
];

/** 청구기간·보호예수 타임라인 항목 */
export function timelineItems(d: Pick<O, 'timeline' | 'lockups' | 'instruments'>): TimelineItem[] {
  const seq = new Map(d.instruments.map((i) => [i.rcept_no, i.seq]));
  return [
    ...d.timeline.map((x) => ({
      label: `${instrumentLabel(x.instrument)}${seq.get(x.rcept_no) ? ` ${seq.get(x.rcept_no) ?? ''}회` : ''}${x.active ? '' : '(소멸)'}`,
      start: x.start,
      end: x.end,
      kind: x.instrument,
    })),
    ...d.lockups.map((l) => ({ label: '보호예수(추정)', start: l.listing_date, end: l.lockup_end_est, estimated: true, kind: 'lockup' })),
  ];
}

export function drawOverhang(p: Panel, d: O, login: boolean, today: Date): void {
  const s = d.summary;
  if (!s && !d.instruments.length) {
    p.showMessage('오버행 사건(CB·BW·EB·유상증자) 없음');
    return;
  }
  const items = timelineItems(d);
  append(p.body, [
    s ? h('p', { class: 'dim' }, `${kstDate(s.as_of)} 기준${login && d.close !== null ? ` · 로그인 칸 기준 종가 ${won(d.close)}(${kstDate(d.close_date)})` : ''}`) : null,
    s ? kv(summaryItems(s, login)) : null,
    login ? table({ caption: '회차별 오버행', columns: INST_COLS, rows: d.instruments, empty: '남은 오버행 없음' }).el : null,
    items.length ? timeline({ label: '청구기간·보호예수 타임라인', items, today }) : null,
    d.lockups.length
      ? h('p', { data: { section: 'lockups' } }, estBadge(), ` ${d.lockups[0]?.basis ?? ''}: ${d.lockups.map((l) => `${kstDate(l.lockup_end_est)} ${shares(l.shares)}(${num(l.months)}개월)`).join(' · ')}`)
      : null,
    login ? null : h('p', { class: 'def' }, '회차별 표·금액·ITM 은 로그인 화면'),
  ]);
}

export async function mount(t: TabCtx, el: HTMLElement): Promise<void> {
  const p = mkPanel(t, { title: '오버행', tier: 'public', span: 12 });
  tabGrid(el, p);
  p.setDef('오버행 비율 = 잠재 신주(CB·BW 전환·행사 + 유상증자) ÷ DART 주식총수. EB 는 분자에 없다. EV 에 더하지 않는다.');
  const today = t.now();
  const login = t.tier === 'login';
  await fill(
    p,
    async (): Promise<Envelope<O>> => {
      if (login) return t.get<O>(`stock/${t.code}/overhang`);
      const env = await t.pub();
      if (!env) throw noPubFin();
      const o = env.data.overhang;
      const data: O = {
        code: t.code,
        corp_code: env.data.corp_code,
        close: null,
        close_date: null,
        summary: o && { ...o, listed_shares: null, login_ratio_pct: null, amount: null, itm_shares: null },
        instruments: [],
        lockups: [],
        timeline: o?.timeline ?? [],
      };
      return { ...env, quality: o?.quality ?? env.quality, notes: o?.notes ?? [], data };
    },
    (env) => {
      drawOverhang(p, env.data, login, today);
      return 0;
    },
    login ? HINT.derive : HINT.public,
  );
}
