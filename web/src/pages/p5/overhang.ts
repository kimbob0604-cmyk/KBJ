// 페이지 5 새 오버행(최근 7일 CB·BW·EB·유상·무상증자 결정 — 회사 오버행 비율, 행 → 페이지 8).
//
// - 로그인: 'filings/overhang'(days ≤ 90, 종류). 공개: overhang_recent.json(최근 30일) — 기간·종류는 화면에서 거른다.
// - 비율 = 회사 오버행 비율(최근 요약 — 분모 DART 주식총수, 보통주 발행). 금액·ITM 은 종목 상세(로그인).
import type { Envelope } from '../../data/source';
import { joEok, kstDate, num, pct } from '../../ui/fmt';
import { panel } from '../../ui/panel';
import { seg, select } from '../../ui/seg';
import { type Column, table } from '../../ui/table';
import { fill, filters } from '../p1_market';
import { arr, col, dartLink, HINT, INSTRUMENT_LABEL, instrumentLabel, type S, shares, stockCol, won } from '../p8/common';
import { addDays, type P5Ctx, today, type Widget } from './common';

type Row = S['OverhangRecentRow'];

export function overhangColumns(open: (code: string) => void): Column<Row>[] {
  return [
    col('결정일', (r) => kstDate(r.rcept_dt)),
    stockCol<Row>(open),
    col('종류', (r) => dartLink(r.link, instrumentLabel(r.instrument)), true),
    col('권면·발행액', (r) => joEok(r.face_value)),
    col('전환·행사가', (r) => won(r.price)),
    col('주식 수', (r) => shares(r.shares)),
    col('청구 시작', (r) => kstDate(r.exercise_start)),
    col('회사 오버행 비율', (r) => pct(r.ratio_pct, 2, false)),
  ];
}

export function createOverhang(c: P5Ctx): Widget {
  const login = c.tier === 'login';
  const p = panel(c.tier, { title: '새 오버행', tier: 'public', span: 6 });
  p.setDef('CB·BW·EB·유상·무상증자 결정. 비율 = 회사 잠재 신주 ÷ DART 주식총수. EB 는 신주가 아니다.');
  const q = { days: login ? '7' : '30', instrument: '' };
  const load = (): Promise<void> =>
    fill(
      p,
      async (): Promise<Envelope<Row[]>> => {
        if (login) {
          const env = await c.source.get<S['OverhangRecent']>('filings/overhang', { days: Number(q.days), instrument: q.instrument || null });
          return { ...env, data: env.data.rows };
        }
        const since = addDays(today(c.now), 1 - Number(q.days));
        const env = await c.source.get<{ rows?: Row[] }>('overhang_recent');
        return { ...env, data: arr(env.data.rows).filter((r) => r.rcept_dt >= since && (!q.instrument || r.instrument === q.instrument)) };
      },
      (env) => {
        p.body.append(
          table<Row>({
            caption: `최근 ${q.days}일 · ${num(env.data.length)}건`,
            columns: overhangColumns(c.open),
            rows: env.data,
            rowKey: (r) => r.rcept_no,
            onSelect: (r) => {
              if (r.stock_code) c.open(r.stock_code);
            },
            pageSize: 20,
            empty: '이 기간 새 오버행 결정 없음',
          }).el,
        );
        return 0;
      },
      login ? HINT.derive : HINT.public,
    );
  filters(
    p,
    seg({
      label: '오버행 기간',
      options: (login ? ['7', '30', '90'] : ['7', '30']).map((d) => ({ value: d, label: `${d}일` })),
      value: q.days,
      onChange: (v) => {
        q.days = v;
        void load();
      },
    }).el,
    select({
      label: '종류',
      options: [{ value: '', label: '전체' }, ...Object.entries(INSTRUMENT_LABEL).map(([value, label]) => ({ value, label }))],
      value: '',
      onChange: (v) => {
        q.instrument = v;
        void load();
      },
    }).el,
  );
  return { panel: p, load };
}
