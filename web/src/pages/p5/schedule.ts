// 페이지 5 실적 일정(오늘부터 14일 — 결산실적공시예고·법정 제출기한(추정), 행 → 페이지 8).
//
// - 로그인: 'filings/schedule'(days ≤ 60). 공개: earnings_schedule.json(앞으로 30일) — 기간은 화면에서 거른다.
// - 법정 기한은 추정(분기·반기 45일, 사업 90일 [확인 필요]) — '추정' 배지. 예고 공시가 있으면 그 날.
import type { Envelope } from '../../data/source';
import { num } from '../../ui/fmt';
import { panel } from '../../ui/panel';
import { seg } from '../../ui/seg';
import { table } from '../../ui/table';
import { fill, filters } from '../p1_market';
import { arr, HINT, type S, type ScheduleLike, scheduleColumns } from '../p8/common';
import { addDays, type P5Ctx, today, type Widget } from './common';

export function createSchedule(c: P5Ctx): Widget {
  const login = c.tier === 'login';
  const p = panel(c.tier, { title: '실적 일정', tier: 'public', span: 6 });
  p.setDef('결산실적공시예고(본문 예정일) 또는 법정 제출기한(분기·반기 45일, 사업 90일 — 추정).');
  let days = '14';
  const load = (): Promise<void> =>
    fill(
      p,
      async (): Promise<Envelope<ScheduleLike[]>> => {
        if (login) {
          const env = await c.source.get<S['EarningsSchedule']>('filings/schedule', { days: Number(days) });
          return { ...env, data: env.data.rows };
        }
        const from = today(c.now);
        const to = addDays(from, Number(days) - 1);
        const env = await c.source.get<{ rows?: ScheduleLike[] }>('earnings_schedule');
        return { ...env, data: arr(env.data.rows).filter((r) => r.planned_date >= from && r.planned_date <= to) };
      },
      (env) => {
        p.body.append(
          table<ScheduleLike>({
            caption: `오늘부터 ${days}일 · ${num(env.data.length)}건`,
            columns: scheduleColumns(c.open),
            rows: [...env.data].sort((a, b) => a.planned_date.localeCompare(b.planned_date)),
            rowKey: (r) => `${r.stock_code ?? ''}:${r.period_end}:${r.kind}`,
            onSelect: (r) => {
              if (r.stock_code) c.open(r.stock_code);
            },
            pageSize: 20,
            empty: '이 기간 예정된 실적 공시 없음',
          }).el,
        );
        return 0;
      },
      login ? HINT.derive : HINT.public,
    );
  filters(
    p,
    seg({
      label: '일정 기간',
      options: (login ? ['14', '30', '60'] : ['14', '30']).map((d) => ({ value: d, label: `${d}일` })),
      value: days,
      onChange: (v) => {
        days = v;
        void load();
      },
    }).el,
  );
  return { panel: p, load };
}
