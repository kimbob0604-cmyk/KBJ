// 페이지 5 공시 피드(오늘·어제 — 종류·중요도·시장 필터, 관심종목만(로그인), 정렬, 행 → 페이지 8).
//
// - 로그인: 'filings/feed'(중요도 = 제목 키워드 + 관심종목 + 시총 가산 — 읽을 때 계산). 일반 공시는 텔레그램으로
//   보내지 않는다(D-P4-14) — 화면에서만 본다.
// - 공개: filings_feed.json(최근 2영업일, 키워드 점수만의 kw_level). 거르기·정렬은 화면에서.
// - 접수 '시각'은 우리가 처음 본 시각(DART 응답은 날짜만 — first_seen_at).
import type { Envelope } from '../../data/source';
import { h } from '../../ui/dom';
import { kstTime, num, shortDate } from '../../ui/fmt';
import { panel } from '../../ui/panel';
import { type Option, seg, select } from '../../ui/seg';
import { type Column, table } from '../../ui/table';
import { fill, filters } from '../p1_market';
import { arr, col, dartLink, HINT, IMPORTANCE_LABEL, importanceCell, KIND_OPTIONS, kindLabel, MARKET_LABEL, type S, stockCol, tag } from '../p8/common';
import { addDays, marketOfCls, type P5Ctx, today, type Widget } from './common';

/** 공개 파일 한 줄(로그인 DisclosureView 와 같은 칸 + kw_level, 관심·로그인 점수 없음) */
export interface FeedRow {
  rcept_no: string;
  rcept_dt: string;
  first_seen_at: string;
  stock_code: string | null;
  corp_name: string;
  corp_cls: string | null;
  title: string;
  kind: string;
  is_correction: boolean;
  kw_score: number | null;
  /** 로그인: importance, 공개: kw_level */
  importance?: string;
  kw_level?: string;
  is_watch?: boolean;
  link: string;
}

interface PubFeed {
  dates?: string[];
  rows?: FeedRow[];
}

type Sort = 'time' | 'importance';
const RANK: Readonly<Record<string, number>> = { critical: 0, high: 1, medium: 2, low: 3 };

export const levelOf = (r: FeedRow): string => r.importance ?? r.kw_level ?? 'low';

/** 정렬 — 최신순(처음 본 시각 내림차순) 또는 중요도순(같으면 최신) */
export function sortRows(rows: readonly FeedRow[], by: Sort): FeedRow[] {
  return [...rows].sort(
    (a, b) => (by === 'importance' ? (RANK[levelOf(a)] ?? 9) - (RANK[levelOf(b)] ?? 9) : 0) || b.first_seen_at.localeCompare(a.first_seen_at),
  );
}

export function feedColumns(open: (code: string) => void): Column<FeedRow>[] {
  return [
    col('시각', (r) => `${shortDate(r.rcept_dt)} ${kstTime(r.first_seen_at)}`),
    stockCol<FeedRow>(open),
    col('제목', (r) => h('span', null, dartLink(r.link, r.title), r.is_correction ? tag('정정', 'warn') : null), true),
    col('종류', (r) => kindLabel(r.kind), true),
    col('중요도', (r) => importanceCell(levelOf(r)), true),
    col('키워드', (r) => num(r.kw_score)),
  ];
}

const opts = (vals: readonly string[], label: (v: string) => string): Option<string>[] => [
  { value: '', label: '전체' },
  ...vals.map((value) => ({ value, label: label(value) })),
];

export function createFeed(c: P5Ctx): Widget {
  const login = c.tier === 'login';
  const p = panel(c.tier, { title: '공시 피드', tier: 'public', span: 12 });
  p.setDef(
    login
      ? '중요도 = 제목 키워드 + 관심종목 + 시총 가산. 일반 공시는 텔레그램으로 보내지 않는다.'
      : '중요도는 제목 키워드 점수만(관심종목·시총 가산은 로그인 화면).',
  );
  const q = { day: '0', kind: '', level: '', market: '', watch: false, sort: 'time' as Sort };
  const count = h('span', { class: 'count', attrs: { 'aria-live': 'polite' } });

  const load = (): Promise<void> =>
    fill(
      p,
      async (): Promise<Envelope<{ day: string | null; rows: FeedRow[] }>> => {
        if (login) {
          const day = addDays(today(c.now), -Number(q.day));
          const env = await c.source.get<S['FilingsFeed']>('filings/feed', {
            date: day,
            kind: q.kind || null,
            importance: q.level || null,
            market: q.market || null,
            watch_only: q.watch,
            limit: 200,
          });
          return { ...env, data: { day: env.data.date, rows: env.data.rows } };
        }
        // 공개: 거르기는 화면에서(파일은 최근 2영업일)
        const env = await c.source.get<PubFeed>('filings_feed');
        const day = arr(env.data.dates)[Number(q.day)] ?? null;
        const rows = arr(env.data.rows).filter(
          (r) => (!day || r.rcept_dt === day) && (!q.kind || r.kind === q.kind) && (!q.level || levelOf(r) === q.level) && (!q.market || marketOfCls(r.corp_cls) === q.market),
        );
        return { ...env, data: { day, rows } };
      },
      (env) => {
        const rows = sortRows(env.data.rows, q.sort);
        count.textContent = `${num(rows.length)}건`;
        p.body.append(
          table<FeedRow>({
            caption: `${env.data.day ? shortDate(env.data.day) : '최근'} 공시`,
            columns: feedColumns(c.open),
            rows,
            rowKey: (r) => r.rcept_no,
            onSelect: (r) => {
              if (r.stock_code) c.open(r.stock_code);
            },
            empty: '조건에 맞는 공시 없음',
          }).el,
        );
        return 0;
      },
      login ? HINT.feed : HINT.public,
    );

  const set = (k: 'day' | 'kind' | 'level' | 'market') => (v: string): void => {
    q[k] = v;
    void load();
  };
  const box = h('input', {
    attrs: { type: 'checkbox' },
    on: {
      change: () => {
        q.watch = box.checked;
        void load();
      },
    },
  });
  filters(
    p,
    seg({
      label: '공시 날짜',
      options: [
        { value: '0', label: login ? '오늘' : '최근일' },
        { value: '1', label: login ? '어제' : '그 전일' },
      ],
      value: '0',
      onChange: set('day'),
    }).el,
    select({ label: '종류', options: opts(KIND_OPTIONS, kindLabel), value: '', onChange: set('kind') }).el,
    select({ label: '중요도', options: opts(['critical', 'high', 'medium', 'low'], (v) => IMPORTANCE_LABEL[v] ?? v), value: '', onChange: set('level') }).el,
    select({ label: '시장', options: opts(['KOSPI', 'KOSDAQ', 'KONEX'], (v) => MARKET_LABEL[v] ?? v), value: '', onChange: set('market') }).el,
    select<Sort>({
      label: '정렬',
      options: [
        { value: 'time', label: '최신순' },
        { value: 'importance', label: '중요도순' },
      ],
      value: 'time',
      onChange: (v) => {
        q.sort = v;
        void load();
      },
    }).el,
    ...(login ? [h('label', null, box, '관심종목만')] : []),
    count,
  );
  return { panel: p, load };
}
