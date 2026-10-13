// 페이지 5 잠정실적(오늘·이번 주 — YoY 분류 색, 관심종목 강조, 행 → 페이지 8).
//
// - 로그인: 'filings/earnings'(from·to ≤ 31일) — 분류(signal·priority)는 알림 작업 기록 또는 같은 엔진 규칙.
// - 공개: earnings_prelim.json(최근 14일) — 분류 없음(YoY·QoQ % 만), 기간은 화면에서 거른다.
// - 관심종목 실시간 알림(우선순위 ≤ 2)은 사건 시점에 텔레그램으로 간다(ADR 0018) — 이 패널은 화면 목록.
import type { Envelope } from '../../data/source';
import { h } from '../../ui/dom';
import { num, shortDate } from '../../ui/fmt';
import { panel } from '../../ui/panel';
import { seg, select } from '../../ui/seg';
import { table } from '../../ui/table';
import { fill, filters } from '../p1_market';
import { arr, HINT, latestPrelims, prelimColumns, type S, supersededNote } from '../p8/common';
import { addDays, type P5Ctx, today, type Widget } from './common';

type Prelim = S['PrelimView'];
type Sort = 'priority' | 'time';

/** 정렬 — 분류 우선순위(1 먼저, 같으면 관심종목 먼저) 또는 최신 접수 */
export function sortPrelims(rows: readonly Prelim[], by: Sort): Prelim[] {
  return [...rows].sort(
    (a, b) =>
      (by === 'priority' ? (a.priority ?? 9) - (b.priority ?? 9) || Number(b.is_watch) - Number(a.is_watch) : 0) ||
      b.rcept_no.localeCompare(a.rcept_no),
  );
}

export function createPrelim(c: P5Ctx): Widget {
  const login = c.tier === 'login';
  const p = panel(c.tier, { title: '잠정실적', tier: 'public', span: 12 });
  p.setDef(
    login
      ? '분류 = 전년동기 대비(YoY). 관심종목의 우선순위 1·2 는 사건 시점에 텔레그램 알림. 컨센서스 대비는 출처 없음(KIS 교체로 잃음).'
      : 'YoY·QoQ = (당기 − 기준) ÷ |기준|. 분류·관심종목은 로그인 화면.',
  );
  const q: { week: boolean; sort: Sort } = { week: true, sort: login ? 'priority' : 'time' };
  const count = h('span', { class: 'count', attrs: { 'aria-live': 'polite' } });

  const load = (): Promise<void> => {
    const to = today(c.now);
    const from = q.week ? addDays(to, -6) : to;
    return fill(
      p,
      async (): Promise<Envelope<Prelim[]>> => {
        if (login) {
          const env = await c.source.get<S['EarningsList']>('filings/earnings', { from, to });
          return { ...env, data: env.data.rows };
        }
        const env = await c.source.get<{ rows?: Prelim[] }>('earnings_prelim');
        return { ...env, data: arr(env.data.rows).filter((r) => r.rcept_dt >= from && r.rcept_dt <= to) };
      },
      (env) => {
        // 정정공시는 원 공시를 대체 — 같은 회사·기간·연결/별도는 가장 늦은 접수번호만(§4.1)
        const latest = latestPrelims(env.data);
        const rows = sortPrelims(latest.rows, q.sort);
        count.textContent = `${num(rows.length)}건`;
        p.body.append(
          table<Prelim>({
            caption: `${shortDate(from)} ~ ${shortDate(to)} 잠정실적${supersededNote(latest.superseded)}`,
            columns: prelimColumns(login, c.open),
            rows,
            rowKey: (r) => r.rcept_no,
            onSelect: (r) => {
              if (r.stock_code) c.open(r.stock_code);
            },
            empty: '이 기간 잠정실적 공시 없음',
          }).el,
        );
        return 0;
      },
      login ? HINT.earnings : HINT.public,
    );
  };

  filters(
    p,
    seg({
      label: '잠정실적 기간',
      options: [
        { value: 'd', label: '오늘' },
        { value: 'w', label: '이번 주(7일)' },
      ],
      value: 'w',
      onChange: (v) => {
        q.week = v === 'w';
        void load();
      },
    }).el,
    select<Sort>({
      label: '정렬',
      options: [
        { value: 'priority', label: '분류 우선순위' },
        { value: 'time', label: '최신 접수' },
      ],
      value: q.sort,
      onChange: (v) => {
        q.sort = v;
        void load();
      },
    }).el,
    count,
  );
  return { panel: p, load };
}
