// 페이지 5 내부자 큰 변동(최근 7일 — 보고 기준 순증감, 비율 하한, 행 → 페이지 8).
//
// - 로그인: 'filings/insiders'(days ≤ 90, min_ratio_pct). 공개: insider_recent.json(최근 30일 — 회사별 최신 창,
//   보고자 이름·금액 없음) — 비율 하한은 화면에서 거른다.
// - 보고 기준 순증감(사유 미구분 포함) — 증여·상속·신규선임이 섞인다(장내 순매수가 아니다).
import type { Envelope } from '../../data/source';
import { kstDate, num, pct, signClass } from '../../ui/fmt';
import { panel } from '../../ui/panel';
import { seg, select } from '../../ui/seg';
import { type Column, table } from '../../ui/table';
import { fill, filters } from '../p1_market';
import { arr, col, HINT, type S, shares, stockCol } from '../p8/common';
import { passMinRatio, type P5Ctx, type Widget } from './common';

/** 로그인 InsiderRecentRow 와 공개 파일 줄 공통(공개는 last_rcept_dt 대신 as_of) */
type Row = Omit<S['InsiderRecentRow'], 'last_rcept_dt' | 'is_watch'> & { last_rcept_dt?: string; as_of?: string; is_watch?: boolean };

export function insiderColumns(open: (code: string) => void): Column<Row>[] {
  return [
    stockCol<Row>(open),
    col('순증감', (r) => shares(r.net_shares, true), false, (r) => signClass(r.net_shares)),
    col('증가', (r) => shares(r.buy_shares)),
    col('감소', (r) => shares(r.sell_shares)),
    col('비율', (r) => pct(r.ratio_pct, 2), false, (r) => signClass(r.ratio_pct)),
    col('보고·보고자', (r) => `${num(r.n_reports)}건 · ${num(r.n_reporters)}명`),
    col('마지막 보고', (r) => kstDate(r.last_rcept_dt ?? r.as_of)),
  ];
}

export function createInsiders(c: P5Ctx): Widget {
  const login = c.tier === 'login';
  const p = panel(c.tier, { title: '내부자 큰 변동', tier: 'public', span: 6 });
  p.setDef('임원·주요주주 소유보고의 보고 기준 순증감(사유 미구분 포함 — 장내 순매수가 아니다). 비율 = 순증감 ÷ DART 주식총수.');
  const q = { days: login ? '7' : '30', min: '' };
  const load = (): Promise<void> => {
    const min = q.min ? Number(q.min) : null;
    return fill(
      p,
      async (): Promise<Envelope<Row[]>> => {
        if (login) {
          const env = await c.source.get<S['InsiderRecent']>('filings/insiders', { days: Number(q.days), min_ratio_pct: min });
          return { ...env, data: env.data.rows };
        }
        const env = await c.source.get<{ rows?: Row[] }>('insider_recent');
        return { ...env, data: arr(env.data.rows).filter((r) => passMinRatio(r.ratio_pct, min)) };
      },
      (env) => {
        p.body.append(
          table<Row>({
            caption: `최근 ${q.days}일 · ${num(env.data.length)}개 회사`,
            columns: insiderColumns(c.open),
            rows: env.data,
            rowKey: (r) => r.corp_code,
            onSelect: (r) => {
              if (r.stock_code) c.open(r.stock_code);
            },
            pageSize: 20,
            empty: '조건에 맞는 변동 없음',
          }).el,
        );
        return 0;
      },
      login ? HINT.derive : HINT.public,
    );
  };
  filters(
    p,
    ...(login
      ? [
          seg({
            label: '내부자 기간',
            options: ['7', '30', '90'].map((d) => ({ value: d, label: `${d}일` })),
            value: q.days,
            onChange: (v) => {
              q.days = v;
              void load();
            },
          }).el,
        ]
      : []),
    select({
      label: '비율 하한',
      options: [{ value: '', label: '전체' }, ...['0.1', '0.5', '1'].map((v) => ({ value: v, label: `|비율| ≥ ${v}%` }))],
      value: '',
      onChange: (v) => {
        q.min = v;
        void load();
      },
    }).el,
  );
  return { panel: p, load };
}
