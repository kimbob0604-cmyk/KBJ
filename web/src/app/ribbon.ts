// 상단 띠(docs/p3_design.md §4.4·§6.4·§6.5) — 칩 = 이름·값·꼬리표(잠정·마감·준비 중·자물쇠).
//
// - 로그인 빌드: `market/ribbon`(API) 한 번에 칩 전부. 1분마다 새로 고침(탭이 숨으면 멈춤 — ctx.every).
// - 공개 빌드: 공개 파일 `calendar`·`events` 로 세션·금통위 D-n 만 화면에서 계산한다. 로그인 등급 칩은 자물쇠,
//   P3 에 원천이 없는 칩은 '준비 중(Pn)'. 공개 빌드는 로그인 API 키를 묻지 않는다(묻더라도 static.ts 가 요청 없이 막는다).
// - 칩 순서·이름·단계는 엔진 `kbj/engines/market/ribbon.py` 의 CHIP_ORDER·PENDING 과 같다.
// - 값이 없는 칩은 0 이 아니라 '—'(사유는 title). 금액은 원 단위 → 화면에서만 억·조.
import type { components } from '../api/types.gen';
import type { Envelope, Quality } from '../data/source';
import { chip, type ChipOptions, type ChipState } from '../ui/chip';
import { replace } from '../ui/dom';
import { DASH, eok, joEok, kstParts, kstStamp, kstTime, num, shortDate, signClass, times, toDate } from '../ui/fmt';
import { errorText } from '../ui/panel';
import type { AppContext, RibbonModule } from './types';

type ApiChip = components['schemas']['Chip'];
type Ribbon = components['schemas']['Ribbon'];

/** 로그인 띠 새로 고침 주기(§6.9 "장중 띠 1분") */
export const RIBBON_REFRESH_MS = 60_000;
/** 공개 띠: 세션 카운트다운만 다시 계산(파일은 다시 받지 않는다) */
export const PUBLIC_TICK_MS = 30_000;

/** 칩 자리(엔진 CHIP_ORDER 와 같은 순서). phase 가 있으면 P3 에 원천이 없다. */
export const CHIP_SLOTS: readonly { key: string; label: string; tier: 'public' | 'login'; phase?: string }[] = [
  { key: 'session', label: '세션', tier: 'public' },
  { key: 'bok_mpc', label: '금통위', tier: 'public' },
  { key: 'credit_spread', label: '신용스프레드 AA-', tier: 'public', phase: 'P5' },
  { key: 'credit_balance', label: '신용잔고', tier: 'public', phase: 'P5' },
  { key: 'export_flash', label: '수출 속보', tier: 'public', phase: 'P6' },
  { key: 'market_turnover', label: '시장 거래대금', tier: 'login' },
  { key: 'foreign_spot', label: '외국인 현물', tier: 'login' },
  { key: 'inst_spot', label: '기관 현물', tier: 'login' },
  { key: 'newhigh_count', label: '신고가 수', tier: 'login' },
  { key: 'semi_skew', label: '반도체 쏠림', tier: 'login' },
  { key: 'cyc_def', label: '경기민감 대 방어', tier: 'login' },
  { key: 'kr_vs_global', label: '한국 대 글로벌', tier: 'login', phase: 'P5' },
  { key: 'gex_flip', label: 'GEX Flip 거리', tier: 'login', phase: 'P7' },
];

// ── 시간 계산(KST) ──────────────────────────────────────────

const DAY_MS = 86_400_000;

/** 'YYYY-MM-DD' + 'HH:MM'(KST) → Date */
function kstAt(date: string, hhmm: string): Date | null {
  return /^\d{2}:\d{2}$/.test(hhmm) ? toDate(`${date}T${hhmm}:00+09:00`) : null;
}

function addDays(date: string, n: number): string {
  const d = new Date(Date.parse(`${date}T00:00:00Z`) + n * DAY_MS);
  return d.toISOString().slice(0, 10);
}

/** 남은 시간: 하루 미만은 'H:MM', 그 이상은 'MM/DD HH:MM' */
export function remainText(now: Date, until: Date): string {
  const ms = until.getTime() - now.getTime();
  if (ms >= DAY_MS) return `${shortDate(until)} ${kstTime(until)}`;
  const min = Math.max(0, Math.floor(ms / 60_000));
  return `${Math.floor(min / 60)}:${String(min % 60).padStart(2, '0')}`;
}

const NEXT_WORD: Record<string, string> = {
  정규장: '마감까지',
  '장 시작 전': '개장까지',
  야간장: '야간 종료까지',
  '장 마감': '다음 세션까지',
  휴장: '개장까지',
};

export function sessionText(label: string, now: Date, until: Date | null): string {
  if (!until) return label;
  return `${label} · ${NEXT_WORD[label] ?? '전환까지'} ${remainText(now, until)}`;
}

/** 금통위 D-n(KST 날짜 차). 날짜가 지났으면 null */
export function daysUntil(now: Date, date: string): number | null {
  const p = kstParts(now);
  if (!p || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return null;
  const today = Date.UTC(p.year, p.month - 1, p.day);
  const n = Math.round((Date.parse(`${date}T00:00:00Z`) - today) / DAY_MS);
  return n >= 0 ? n : null;
}

export function ddayLabel(n: number): string {
  return n === 0 ? '금통위 D-day' : `금통위 D-${n}`;
}

// ── 공개: calendar.json 으로 세션 계산 ──────────────────────

export interface CalDay {
  date: string;
  trading: boolean;
  open?: string | null;
  close?: string | null;
  night_session?: boolean;
}

export interface CalendarData {
  next_trading_day?: string | null;
  session?: { open?: string; close?: string; night?: { open: string; close: string; next_day_close?: boolean } };
  days: CalDay[];
}

export interface SessionState {
  label: string;
  until: Date | null;
}

/**
 * 세션 상태(엔진 `session_chip` 과 같은 규칙): 야간장 → 장 시작 전 → 정규장 → 장 마감 → 휴장.
 * 캘린더에 오늘이 없으면 null(모름 — 지어내지 않는다).
 */
export function sessionAt(now: Date, cal: CalendarData): SessionState | null {
  const p = kstParts(now);
  if (!p) return null;
  const today = `${p.year}-${String(p.month).padStart(2, '0')}-${String(p.day).padStart(2, '0')}`;
  const byDate = new Map(cal.days.map((d) => [d.date, d]));
  const day = byDate.get(today);
  if (!day) return null;
  const t = now.getTime();
  const at = (date: string, hhmm: string | null | undefined): Date | null => (hhmm ? kstAt(date, hhmm) : null);
  const night = cal.session?.night;
  const nextTrading = (from: string): CalDay | undefined =>
    cal.days.find((d) => d.date > from && d.trading) ?? undefined;
  const openOf = (d: CalDay | undefined): Date | null => (d ? at(d.date, d.open ?? cal.session?.open) : null);

  // 야간장: 어제 연 야간장이 오늘 새벽까지, 또는 오늘 저녁에 연 야간장
  if (night) {
    const yesterday = byDate.get(addDays(today, -1));
    const morningEnd = at(today, night.close);
    if (yesterday?.night_session && morningEnd && t < morningEnd.getTime())
      return { label: '야간장', until: morningEnd };
    const nightOpen = at(today, night.open);
    if (day.night_session && nightOpen && t >= nightOpen.getTime()) {
      return { label: '야간장', until: at(addDays(today, night.next_day_close === false ? 0 : 1), night.close) };
    }
  }
  if (!day.trading) return { label: '휴장', until: openOf(nextTrading(today)) };
  const open = at(today, day.open ?? cal.session?.open);
  const close = at(today, day.close ?? cal.session?.close);
  if (open && t < open.getTime()) return { label: '장 시작 전', until: open };
  if (close && t < close.getTime()) return { label: '정규장', until: close };
  const nightOpen = night ? at(today, night.open) : null;
  if (day.night_session && nightOpen && t < nightOpen.getTime()) return { label: '장 마감', until: nightOpen };
  return { label: '장 마감', until: openOf(nextTrading(today)) };
}

interface EventsData {
  events?: { date: string; kind?: string; label?: string; title?: string }[];
  next_bok_mpc?: string | null;
}

/** 다음 금통위 날짜(파일의 next_bok_mpc, 없으면 목록에서 오늘 이후 첫 일정) */
export function nextMpc(now: Date, ev: EventsData): string | null {
  const fromList = (ev.events ?? [])
    .filter((e) => (e.kind === undefined || e.kind.startsWith('bok')) && daysUntil(now, e.date) !== null)
    .map((e) => e.date)
    .sort()[0];
  if (ev.next_bok_mpc && daysUntil(now, ev.next_bok_mpc) !== null) return ev.next_bok_mpc;
  return fromList ?? null;
}

// ── 칩 만들기 ───────────────────────────────────────────────

function stateOf(q: Quality, closed: boolean): ChipState {
  if (q === 'estimated') return 'estimated';
  return closed ? 'closed' : 'live';
}

function titleOf(parts: readonly (string | null | undefined)[]): string | undefined {
  const t = parts.filter((x): x is string => typeof x === 'string' && x.length > 0).join(' · ');
  return t || undefined;
}

function detailNum(c: ApiChip, key: string): number | null {
  const v = c.detail?.[key];
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function detailStr(c: ApiChip, key: string): string | null {
  const v = c.detail?.[key];
  return typeof v === 'string' ? v : null;
}

/** API 칩 하나 → 화면 칩 옵션. 값 형식은 칩 키마다. */
export function chipOptions(c: ApiChip, now: Date): ChipOptions {
  const base: ChipOptions = { key: c.key, label: c.label, tier: c.tier };
  if (c.phase_pending) return { ...base, state: 'pending', phase: c.phase_pending };
  const v = c.value;
  if (!v || v.quality === 'invalid') {
    return { ...base, value: DASH, cls: 'dim', title: titleOf([c.note ?? '값 없음', ...c.tags]) };
  }
  const closed = c.tags.includes('마감');
  const title = titleOf([v.source, kstStamp(v.as_of), ...c.tags, c.note]);
  const n = typeof v.value === 'number' ? v.value : null;
  let text: string;
  let cls: string | undefined;
  switch (c.key) {
    case 'session': {
      const until = detailStr(c, 'until');
      text = sessionText(String(v.value), now, until ? toDate(until) : null);
      break;
    }
    case 'bok_mpc': {
      const date = detailStr(c, 'date');
      text = date ? shortDate(date) : String(v.value);
      break;
    }
    case 'market_turnover': {
      const ratio = detailNum(c, 'ratio_avg20');
      text = ratio === null ? joEok(n) : `${joEok(n)} · ${times(ratio)}`;
      cls = ratio === null ? undefined : ratio >= 1 ? 'up' : 'down';
      break;
    }
    case 'foreign_spot':
    case 'inst_spot':
      text = eok(n, 0, true);
      cls = signClass(n);
      break;
    case 'newhigh_count': {
      const u = detailNum(c, 'universe');
      text = u === null ? num(n) : `${num(n)} / ${num(u)}`;
      break;
    }
    case 'semi_skew':
    case 'cyc_def':
      text = n === null ? DASH : `${num(n, 2, true)}%p`;
      cls = signClass(n);
      break;
    default:
      text = n === null ? String(v.value) : num(n, 2);
  }
  if (v.quality === 'stale') {
    return {
      ...base,
      value: `${text} (기준 ${kstTime(v.as_of)})`,
      cls: 'warn',
      state: closed ? 'closed' : 'live',
      title,
    };
  }
  return { ...base, value: text, ...(cls ? { cls } : {}), state: stateOf(v.quality, closed), title };
}

// ── 붙이기 ──────────────────────────────────────────────────

function render(el: HTMLElement, ctx: AppContext, opts: readonly ChipOptions[]): void {
  replace(
    el,
    opts.map((o) => chip(ctx.tier, o)),
  );
}

async function mountLogin(el: HTMLElement, ctx: AppContext): Promise<void> {
  let last: Envelope<Ribbon> | null = null;
  const load = async (): Promise<void> => {
    try {
      last = await ctx.source.get<Ribbon>('market/ribbon');
      const now = ctx.now();
      render(
        el,
        ctx,
        last.data.chips.map((c) => chipOptions(c, now)),
      );
    } catch (err) {
      // 띠만 오류 칩 — 페이지는 계속(절대 규칙 4). 직전 칩이 있으면 남기고 오류 칩을 앞에 붙인다
      // (실패가 이어져도 오류 칩은 하나 — 직전 오류 칩을 바꿔 끼운다)
      const warn = chip(ctx.tier, { key: 'ribbon', label: '상단 띠', value: errorText(err), cls: 'warn' });
      if (last) {
        el.querySelector(':scope > .chip[data-key="ribbon"]')?.remove();
        el.prepend(warn);
      } else replace(el, warn);
    }
  };
  await load();
  // 첫 불러오기 중에 앱이 내려갔으면 주기 작업을 걸지 않는다
  if (el.isConnected) ctx.every(RIBBON_REFRESH_MS, () => void load());
}

async function mountPublic(el: HTMLElement, ctx: AppContext): Promise<void> {
  const [cal, ev] = await Promise.allSettled([
    ctx.source.get<CalendarData>('calendar'),
    ctx.source.get<EventsData>('events'),
  ]);
  const draw = (): void => {
    const now = ctx.now();
    const opts: ChipOptions[] = CHIP_SLOTS.map((s) => {
      const base: ChipOptions = { key: s.key, label: s.label, tier: s.tier };
      if (s.phase) return { ...base, state: 'pending', phase: s.phase };
      if (s.tier === 'login') return { ...base, state: 'locked' };
      if (s.key === 'session') {
        if (cal.status === 'rejected') return { ...base, value: errorText(cal.reason), cls: 'warn' };
        const st = Array.isArray(cal.value.data.days) ? sessionAt(now, cal.value.data) : null;
        if (!st) return { ...base, value: DASH, cls: 'dim', title: '캘린더에 오늘이 없음' };
        return {
          ...base,
          value: sessionText(st.label, now, st.until),
          state: stateOf(cal.value.quality, false),
          title: titleOf([cal.value.source, kstStamp(cal.value.as_of)]),
        };
      }
      // bok_mpc
      if (ev.status === 'rejected') return { ...base, value: errorText(ev.reason), cls: 'warn' };
      const date = nextMpc(now, ev.value.data);
      const n = date ? daysUntil(now, date) : null;
      if (!date || n === null) return { ...base, value: DASH, cls: 'dim', title: '다음 일정 없음' };
      return {
        ...base,
        label: ddayLabel(n),
        value: shortDate(date),
        state: stateOf(ev.value.quality, false),
        title: titleOf([ev.value.source, ...ev.value.notes]),
      };
    });
    render(el, ctx, opts);
  };
  draw();
  if (el.isConnected) ctx.every(PUBLIC_TICK_MS, draw);
}

const ribbon: RibbonModule = {
  mount(el, ctx) {
    return ctx.tier === 'login' ? mountLogin(el, ctx) : mountPublic(el, ctx);
  },
};

export default ribbon;
