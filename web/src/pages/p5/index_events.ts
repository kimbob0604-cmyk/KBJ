// 페이지 5 경제 캘린더의 '지수 이벤트' 줄(P5 2차 묶음 IDX — docs/p5_index_rebalance_prompt.md §0-3). 로그인 전용:
// p5_filings.ts 가 MODE 'public' 이면 이 모듈을 번들에서 뺀다(구성종목·컷오프 추정이 든 API 라 공개판에 없다).
//
// '/api/index-review' 의 calendar(모든 지수의 아직 효력 전 리뷰)에서 리뷰마다 두 줄: 결과 발표·리밸런싱(패시브 매매일).
// 일정이 공표 일정표와 대조 전이면(confirmed false) 제목에 '(추정)', 행 quality estimated.
// 불러오기 실패는 캘린더를 멈추지 않고 notes 한 줄로(삼키지 않는다). 404 = 일정 표가 아직 없음(index.calendar 전).
import type { components } from '../../api/types.gen';
import { DataError, type DataSource } from '../../data/source';
import { errorText } from '../../ui/panel';

type S = components['schemas'];
type CalRow = S['CalendarRow'];
type ReviewDates = S['ReviewDates'];

export const INDEX_SOURCE = 'index';

export interface IndexEvents {
  rows: CalRow[];
  notes: string[];
}

function row(rv: ReviewDates, what: 'announce' | 'trade'): CalRow {
  const est = rv.confirmed ? '' : ' (추정)';
  return {
    event_id: `index:${rv.index_id}:${rv.review_id}:${what}`,
    date: what === 'announce' ? rv.announce_date : rv.trade_date,
    time_kst: null,
    kind: 'index',
    title: `${rv.index_title} ${rv.review_id} 정기변경 ${what === 'announce' ? '결과 발표' : '리밸런싱(패시브 매매)'}${est}`,
    country: 'KR',
    importance: 2,
    source: INDEX_SOURCE,
    quality: rv.confirmed ? rv.quality : 'estimated',
    actual: null,
    actual_series: null,
    unit: null,
  };
}

/** 리뷰 일정 → 캘린더 행(발표·리밸런싱), 날짜순 */
export function indexCalRows(reviews: readonly ReviewDates[]): CalRow[] {
  return reviews
    .flatMap((rv) => [row(rv, 'announce'), row(rv, 'trade')])
    .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : a.event_id < b.event_id ? -1 : 1));
}

/** 두 행 묶음을 날짜(·시각) 순으로 합친다 — 같은 날은 원래 행 먼저 */
export function mergeRows(base: readonly CalRow[], extra: readonly CalRow[]): CalRow[] {
  const key = (r: CalRow): string => `${r.date} ${r.time_kst ?? '99:99'}`;
  return [...base.map((r, i) => ({ r, i })), ...extra.map((r, i) => ({ r, i: base.length + i }))]
    .sort((a, b) => (key(a.r) < key(b.r) ? -1 : key(a.r) > key(b.r) ? 1 : a.i - b.i))
    .map((x) => x.r);
}

/** 지수 일정을 불러 캘린더 행(base)에 섞는다. 실패해도 거부하지 않는다(base 그대로 + notes 한 줄) */
export async function withIndexEvents(source: DataSource, base: readonly CalRow[]): Promise<IndexEvents> {
  try {
    const env = await source.get<S['IndexReview']>('index-review');
    return { rows: mergeRows(base, indexCalRows(env.data.calendar)), notes: [] };
  } catch (err) {
    const note = err instanceof DataError && err.status === 404 ? '지수 일정 아직 없음 — index.calendar' : `지수 일정: ${errorText(err)}`;
    return { rows: [...base], notes: [note] };
  }
}
