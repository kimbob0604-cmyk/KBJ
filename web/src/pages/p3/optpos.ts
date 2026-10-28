// 페이지 3 옵션 행사가별 투자자 추정 포지션 패널(metrics §34, ADR 0044 — 로그인 전용, KIS·KRX 원천).
//
// - 패널 하나(전체 폭): 맨 위 칸(기준일·누적 시작·콜 벽·풋 벽·합성 델타·오늘 선물) + 근월물 콜·풋 행사가별 추정 보유 막대
//   + 오늘 바뀐 행사가 상위 표 + 레벨 표 + 시리즈별 델타.
// - 데이터 키 'gex/option-positions'(investor=frgn) 한 번. 404 는 '.msg'(작업 이름), 그 밖 실패만 '.msg.err'.
// - 값은 모두 추정치(키움 방식 — 투자자별 합계 → 평균단가 → 가까운 두 행사가 2:1) — 칸마다 '추정' 배지, OI 초과 행(invalid)은
//   막대·벽에서 빼고 수를 notes 에. 판정은 라벨 코드(청산·축소·확대·신규·반전·유지·작음)일 뿐 문장으로 단정하지 않는다.
// - 공개 빌드에서는 이 모듈 본문이 번들에서 빠진다(p3_diag 의 LOGIN_MOUNTS) — 틀(optposPanels)만 자물쇠로.
import type { components } from '../../api/types.gen';
import type { PageContext } from '../../app/types';
import { DataError, type Envelope } from '../../data/source';
import { barsChart, type BarItem } from '../../ui/bars';
import { h, replace } from '../../ui/dom';
import { DASH, num, pct, shortDate, signClass, type Num } from '../../ui/fmt';
import { kv, type KvItem } from '../../ui/kv';
import { errorText, type Panel, panel } from '../../ui/panel';
import { table, type Column } from '../../ui/table';

type S = components['schemas'];
export type OptionPositions = S['OptionPositions'];
export type OptPosSeries = S['OptPosSeries'];
export type OptPosStrike = S['OptPosStrike'];
type Label = OptPosStrike['label'];
type LevelKind = S['OptPosLevel']['kind'];

export const OPTPOS_KEY = 'gex/option-positions';
export const OPTPOS_REFRESH_MS = 300_000; // 하루 한 번(16:05) 바뀐다 — KRX OI(다음 날 08:05)도
export const OPTPOS_SOURCE = 'KIS 투자자별 합계·옵션 체인(키움 방식 추정) + KRX 옵션 OI';
export const OPTPOS_HINT = 'gex.option_positions 평일 16:05';
export const OPTPOS_TITLE = '외국인 옵션 행사가별 추정 포지션';
export const LABEL_KO: Readonly<Record<Label, string>> = {
  small: '작음',
  new: '신규',
  flipped: '반전',
  cleared: '청산',
  reduced: '축소',
  added: '확대',
  hold: '유지',
};
export const LEVEL_KO: Readonly<Record<LevelKind, string>> = {
  call_wall: '콜 벽',
  put_wall: '풋 벽',
  prev_call_wall: '전일 콜 벽',
  prev_put_wall: '전일 풋 벽',
  futures: '현재 선물',
};
export const OPTPOS_DEF =
  '행사가별 투자자 포지션은 거래소 값이 아니다 — 투자자별 콜·풋 합계 매매(1분)의 평균단가에 가장 가까운 두 행사가에 ' +
  '2 : 1 로 배분해 누적한 추정치(키움 방식). 보유 = 누적 시작일 이후 변화분(실제 잔고 아님), − 는 순매도. ' +
  '벽 = 가장 큰 순매도 행사가. 합성 델타 = 선물 누적 순매수 + Σ 보유 × 델타(선물 환산 계약). ' +
  '월물 투자자 합계(전 만기)는 근월물 행사가로만 배분한다.';

function finite(v: Num): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/** 계약 수 — '−4,900계약'(소수 배분은 반올림) */
export function contracts(v: Num, signed = true): string {
  return finite(v) ? `${num(Math.round(v), 0, signed)}계약` : DASH;
}

function strikeText(k: number): string {
  return Number.isInteger(k) ? String(k) : k.toFixed(1);
}

function wallText(w: S['OptPosWall'] | null | undefined): string | null {
  if (!w) return null;
  return `${strikeText(w.strike)} (${contracts(w.holding)}${w.label ? ` · ${LABEL_KO[w.label]}` : ''})`;
}

/** 근월물(월물 가장 가까운 만기) — 없으면 첫 시리즈 */
export function headSeries(d: OptionPositions): OptPosSeries | undefined {
  return d.series.find((s) => s.mrkt_cls === '') ?? d.series[0];
}

/** 막대에 쓰는 행 — invalid(OI 초과) 제외, 행사가 순 */
export function barRows(s: OptPosSeries, cp: 'C' | 'P'): OptPosStrike[] {
  return s.strikes.filter((x) => x.cp === cp && x.quality !== 'invalid').sort((a, b) => a.strike - b.strike);
}

export function headItems(d: OptionPositions, s: OptPosSeries | undefined): KvItem[] {
  const est = 'estimated' as const;
  return [
    { label: '기준', value: shortDate(d.trade_date) },
    { label: '시리즈', value: s ? s.label : null, hint: s ? `${shortDate(s.accum_from)} 이후 누적` : undefined },
    { label: '콜 벽', value: wallText(s?.call_wall), quality: est, hint: '최대 콜 순매도' },
    { label: '풋 벽', value: wallText(s?.put_wall), quality: est, hint: '최대 풋 순매도' },
    {
      label: '합성 델타',
      value: contracts(d.synthetic.total),
      cls: signClass(d.synthetic.total),
      quality: d.synthetic.quality,
      hint: `선물 ${contracts(d.synthetic.futures)} + 옵션 ${contracts(d.synthetic.options)}`,
    },
    { label: '오늘 선물', value: contracts(d.futures_today), cls: signClass(d.futures_today), hint: '순매수' },
  ];
}

function bars(s: OptPosSeries, cp: 'C' | 'P'): HTMLElement {
  const items: BarItem[] = barRows(s, cp).map((x) => ({
    label: strikeText(x.strike),
    value: x.holding,
    detail: `오늘 ${contracts(x.change)} · ${LABEL_KO[x.label]}${x.oi ? ` · OI ${num(x.oi)}` : ''}`,
  }));
  return barsChart({
    label: `${s.label} ${cp === 'C' ? '콜' : '풋'} 행사가별 추정 보유(계약)`,
    items,
    format: (v) => num(Math.round(v), 0, true),
  });
}

type Top = S['OptPosTop'];

function topTable(d: OptionPositions): HTMLElement {
  const cols: Column<Top>[] = [
    { key: 's', label: '시리즈', align: 'l', cell: (r) => r.series },
    { key: 'k', label: '행사가', cell: (r) => `${strikeText(r.strike)} ${r.cp === 'C' ? '콜' : '풋'}` },
    { key: 'c', label: '오늘', cell: (r) => num(Math.round(r.change), 0, true), cls: (r) => signClass(r.change) },
    { key: 'p', label: '전일 보유', cell: (r) => num(Math.round(r.prev_holding), 0, true), cls: (r) => signClass(r.prev_holding) },
    { key: 'h', label: '보유', cell: (r) => num(Math.round(r.holding), 0, true), cls: (r) => signClass(r.holding) },
    { key: 'l', label: '판정', align: 'l', cell: (r) => h('span', { class: `optpos-label ${r.label}` }, LABEL_KO[r.label]) },
  ];
  return table<Top>({
    caption: '오늘 바뀐 행사가 상위(계약)',
    columns: cols,
    rows: d.top_changes,
    rowKey: (r) => `${r.series}-${String(r.strike)}-${r.cp}`,
    empty: '오늘 변화 없음',
  }).el;
}

type Lv = S['OptPosLevel'];

function levelTable(d: OptionPositions): HTMLElement {
  const cols: Column<Lv>[] = [
    { key: 'k', label: '레벨', align: 'l', cell: (r) => LEVEL_KO[r.kind] },
    { key: 'v', label: '선물 pt', cell: (r) => num(r.level, 2) },
    { key: 'd', label: '선물 대비', cell: (r) => pct(r.distance_pct, 2), cls: (r) => signClass(r.distance_pct) },
    { key: 'e', label: 'EWY', cell: (r) => (finite(r.ewy) ? num(r.ewy, 2) : null) },
  ];
  return table<Lv>({ caption: '레벨 표', columns: cols, rows: d.levels, rowKey: (r) => r.kind }).el;
}

export function draw(p: Panel, env: Envelope<OptionPositions>): void {
  const d = env.data;
  const invalid = d.series.reduce((a, s) => a + s.invalid, 0);
  p.setSource(env, invalid);
  const s = headSeries(d);
  if (!s) {
    p.showMessage('살아 있는 시리즈가 없음(만기 소멸)');
    return;
  }
  const deltas = d.synthetic.by_series.map((x) => `${x.series} ${contracts(x.delta)}`).join(' · ');
  replace(
    p.body,
    kv(headItems(d, s)),
    h('div', { class: 'optpos-charts' }, bars(s, 'C'), bars(s, 'P')),
    topTable(d),
    levelTable(d),
    h('p', { class: 'dim' }, `시리즈별 옵션 델타: ${deltas || DASH}`),
    h('p', { class: 'dim' }, `EWY 환산: ${d.ewy.reason}`),
    h(
      'p',
      { class: 'dim' },
      `판정 기준: 보유 ${String(d.thresholds.min_judge)}계약 미만 '작음' · 줄어든 비율 ${String(Math.round(d.thresholds.clear_pct * 100))}% 이상 '청산' · ` +
        `${String(Math.round(d.thresholds.reduce_pct * 100))}% 이상 '축소' · 늘어난 비율 ${String(Math.round(d.thresholds.add_pct * 100))}% 이상 '확대' [확인 필요 — 기본값]`,
    ),
  );
}

function fail(p: Panel, err: unknown): void {
  if (err instanceof DataError && err.status === 404) p.showMessage(`${errorText(err)} — ${OPTPOS_HINT}`);
  else p.showError(err);
}

/** 틀만(공개 빌드는 이 틀만 쓰고 본문 mountOptpos 는 번들에서 빠진다) */
export function optposPanels(ctx: PageContext): { pos: Panel } {
  const pos = panel(ctx.tier, {
    tier: 'login',
    lock: 'investor',
    src: OPTPOS_SOURCE,
    span: 12,
    title: OPTPOS_TITLE,
    def: OPTPOS_DEF,
  });
  ctx.root.append(pos.el);
  return { pos };
}

export async function mountOptpos(ctx: PageContext): Promise<void> {
  const { pos } = optposPanels(ctx);
  if (pos.locked) return; // 공개판 — 자물쇠만, 요청하지 않는다

  async function load(): Promise<void> {
    let env: Envelope<OptionPositions>;
    try {
      env = await ctx.source.get<OptionPositions>(OPTPOS_KEY, { investor: 'frgn' });
    } catch (err) {
      fail(pos, err);
      return;
    }
    try {
      draw(pos, env);
    } catch (err) {
      pos.showError(err);
    }
  }

  await load();
  if (!ctx.root.isConnected) return;
  let busy = false;
  ctx.every(OPTPOS_REFRESH_MS, () => {
    if (busy) return;
    busy = true;
    void load().finally(() => {
      busy = false;
    });
  });
}
