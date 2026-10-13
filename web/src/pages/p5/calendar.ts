// 경제 캘린더 패널(docs/p5_design.md §6 — 페이지 5·6, D-P5-8): 공식 일정(금통위·FOMC·한국 주요 공표일 — 수기)·FRED 발표일,
// 발표 결과(같은 시리즈의 공표 값 — 지어내지 않는다)·중요도·출처.
//
// - 로그인: 'macro/calendar'(API — 예상치(ForexFactory)가 붙은 행·FF 만의 행 포함, 예상치 열이 보인다).
// - 공개: 공개 파일 'macro/calendar'(public.export — 공개 출처만, 예상치 칸 없음). 예상치 열을 그리지 않는다.
// - 기간 '오늘'·'이번 주'·'전체'(파일 창 −7일~+30일)는 화면에서 거른다(다시 요청하지 않는다).
import type { components } from '../../api/types.gen';
import type { Envelope } from '../../data/source';
import { h } from '../../ui/dom';
import { num, shortDate } from '../../ui/fmt';
import { panel, type Span } from '../../ui/panel';
import { seg } from '../../ui/seg';
import { type Column, table } from '../../ui/table';
import { fill, filters } from '../p1_market';
import { addDays, type P5Ctx, today, type Widget } from './common';

type S = components['schemas'];
export type CalRow = S['CalendarRow'];
export type CalWindow = 'today' | 'week' | 'all';

export const CALENDAR_TITLE = '경제 캘린더';
export const CALENDAR_HINT = 'macro.morning 06:20(공식 일정·FRED 발표일, 확인 전 #45)';
const COUNTRY: Record<string, string> = { KR: '한국', US: '미국', CN: '중국', JP: '일본', EU: '유로' };
const SOURCE_LABEL: Record<string, string> = {
  bok: '한국은행',
  fomc: '연준',
  fred: 'FRED',
  kosis: '통계청',
  ecos: '한국은행',
  manual: '공식 일정',
  ff: 'ForexFactory(예상치)',
};

/** 기간 거르기(KST 날짜, 끝 포함). 이번 주 = 오늘부터 6일 뒤까지 */
export function inWindow(rows: readonly CalRow[], w: CalWindow, todayStr: string): CalRow[] {
  if (w === 'all') return [...rows];
  const end = w === 'today' ? todayStr : addDays(todayStr, 6);
  return rows.filter((r) => r.date >= todayStr && r.date <= end);
}

export function sourceLabel(src: string): string {
  return src
    .split('+')
    .map((s) => SOURCE_LABEL[s] ?? s)
    .join(' + ');
}

function valueText(v: number | null | undefined, unit: string | null | undefined): string | null {
  if (typeof v !== 'number' || !Number.isFinite(v)) return null;
  return unit && unit !== '%' ? `${num(v, 2)} ${unit}` : `${num(v, 2)}${unit === '%' ? '%' : ''}`;
}

export function calendarColumns(login: boolean): Column<CalRow>[] {
  const cols: Column<CalRow>[] = [
    { key: 'date', label: '날짜', align: 'l', cell: (r) => `${shortDate(r.date)}${r.time_kst ? ` ${r.time_kst}` : ''}` },
    { key: 'country', label: '나라', align: 'l', cell: (r) => COUNTRY[r.country] ?? r.country },
    { key: 'title', label: '일정', align: 'l', cell: (r) => r.title },
    { key: 'importance', label: '중요도', cell: (r) => h('span', { attrs: { 'aria-label': `중요도 ${r.importance}` } }, '★'.repeat(r.importance)) },
    { key: 'actual', label: '발표 결과', cell: (r) => valueText(r.actual, r.unit) },
  ];
  if (login) cols.push({ key: 'forecast', label: '예상치', cell: (r) => valueText(r.forecast, r.unit) });
  cols.push({ key: 'source', label: '출처', align: 'l', cell: (r) => sourceLabel(r.source) });
  return cols;
}

export function createCalendar(c: Pick<P5Ctx, 'tier' | 'source' | 'now'>, span: Span = 6): Widget {
  const login = c.tier === 'login';
  const p = panel(c.tier, { title: CALENDAR_TITLE, tier: 'public', span });
  p.setDef(
    login
      ? '공식 일정·FRED 발표일. 발표 결과는 같은 시리즈의 공표 값, 예상치는 ForexFactory(로그인 등급).'
      : '공식 일정·FRED 발표일. 발표 결과는 같은 시리즈의 공표 값(예상치는 로그인 화면).',
  );
  let win: CalWindow = 'week';
  let last: Envelope<S['EconCalendar']> | null = null;
  const draw = (env: Envelope<S['EconCalendar']>): number => {
    const rows = inWindow(env.data.rows, win, today(c.now));
    const shown = rows.filter((r) => r.quality !== 'invalid');
    p.body.append(
      table<CalRow>({
        caption: `${win === 'today' ? '오늘' : win === 'week' ? '이번 주' : '전체'} · ${num(shown.length)}건`,
        columns: calendarColumns(login),
        rows: shown,
        rowKey: (r) => r.event_id,
        pageSize: 30,
        empty: '이 기간 일정 없음',
      }).el,
    );
    return rows.length - shown.length;
  };
  const load = (): Promise<void> =>
    fill(
      p,
      async () => {
        last = await c.source.get<S['EconCalendar']>('macro/calendar');
        return last;
      },
      draw,
      CALENDAR_HINT,
    );
  filters(
    p,
    seg<CalWindow>({
      label: '캘린더 기간',
      options: [
        { value: 'today', label: '오늘' },
        { value: 'week', label: '이번 주' },
        { value: 'all', label: '전체' },
      ],
      value: win,
      onChange: (v) => {
        win = v;
        if (last) {
          p.clearBody();
          p.setSource(last, draw(last));
        } else void load();
      },
    }).el,
  );
  return { panel: p, load };
}
